from time import process_time

import frappe
from frappe.utils import cstr, get_datetime
from shopify.resources import Order

from ecommerce_integrations.shopify.connection import temp_shopify_session
from ecommerce_integrations.shopify.constants import ORDER_ID_FIELD
from ecommerce_integrations.shopify.order import sync_sales_order
from ecommerce_integrations.shopify.utils import create_shopify_log

# constants
SYNC_JOB_NAME = "shopify.job.sync.all.orders"
REALTIME_KEY = "shopify.key.sync.all.orders"


@frappe.whitelist()
def get_shopify_orders(from_=None, created_at_min=None, created_at_max=None):
	shopify_orders = fetch_all_orders(from_, created_at_min, created_at_max)
	return shopify_orders


def fetch_all_orders(from_=None, created_at_min=None, created_at_max=None):
	collection = _fetch_orders_from_shopify(
		from_=from_, created_at_min=created_at_min, created_at_max=created_at_max
	)

	orders = []
	for order in collection:
		d = order.to_dict()
		d["synced"] = is_order_synced(order.id)
		orders.append(d)

	next_url = None
	if collection.has_next_page():
		next_url = collection.next_page_url

	prev_url = None
	if collection.has_previous_page():
		prev_url = collection.previous_page_url

	return {
		"orders": orders,
		"nextUrl": next_url,
		"prevUrl": prev_url,
	}


@temp_shopify_session
def _fetch_orders_from_shopify(from_=None, created_at_min=None, created_at_max=None, limit=20):
	if from_:
		collection = Order.find(from_=from_)
	else:
		kwargs = {"limit": limit, "status": "any"}
		if created_at_min:
			kwargs["created_at_min"] = get_datetime(created_at_min).astimezone().isoformat()
		if created_at_max:
			kwargs["created_at_max"] = get_datetime(created_at_max).astimezone().isoformat()
		collection = Order.find(**kwargs)

	return collection


@frappe.whitelist()
def get_order_count():
	synced_orders = frappe.db.sql(
		f"""SELECT COUNT(*) FROM `tabSales Order`
		WHERE `{ORDER_ID_FIELD}` IS NOT NULL AND `{ORDER_ID_FIELD}` != ''""",
	)[0][0]

	total_sales_orders = frappe.db.count("Sales Order")

	shopify_count = get_shopify_order_count()

	return {
		"shopifyCount": shopify_count,
		"syncedCount": synced_orders,
		"erpnextCount": total_sales_orders,
	}


@temp_shopify_session
def get_shopify_order_count():
	return Order.count(status="any")


@frappe.whitelist()
def sync_order(order_id):
	try:
		_fetch_and_sync_order(order_id)
		return True
	except Exception:
		frappe.db.rollback()
		return False


@temp_shopify_session
def _fetch_and_sync_order(order_id):
	order = Order.find(order_id)
	order_dict = order.to_dict()

	log = create_shopify_log(
		status="Queued",
		method="ecommerce_integrations.shopify.order.sync_sales_order",
		request_data=order_dict,
		make_new=True,
	)
	sync_sales_order(order_dict, request_id=log.name)


def is_order_synced(order_id):
	return bool(
		frappe.db.get_value("Sales Order", {ORDER_ID_FIELD: cstr(order_id)})
	)


@frappe.whitelist()
def import_all_orders(created_at_min=None, created_at_max=None):
	frappe.enqueue(
		queue_sync_all_orders,
		queue="long",
		job_name=SYNC_JOB_NAME,
		key=REALTIME_KEY,
		created_at_min=created_at_min,
		created_at_max=created_at_max,
	)


def queue_sync_all_orders(created_at_min=None, created_at_max=None, **kwargs):
	start_time = process_time()

	publish("Syncing all orders...")

	_sync = True
	collection = _fetch_orders_from_shopify(
		created_at_min=created_at_min,
		created_at_max=created_at_max,
		limit=50,
	)
	savepoint = "shopify_order_sync"

	while _sync:
		for order in collection:
			order_dict = order.to_dict()
			order_id = order_dict.get("id")
			order_name = order_dict.get("name", order_id)

			try:
				publish(f"Processing order {order_name}", br=False)
				frappe.db.savepoint(savepoint)

				if is_order_synced(order_id):
					publish(f"Order {order_name} already synced. Skipping...")
					continue

				log = create_shopify_log(
					status="Queued",
					method="ecommerce_integrations.shopify.order.sync_sales_order",
					request_data=order_dict,
					make_new=True,
				)
				sync_sales_order(order_dict, request_id=log.name)

				publish(f"Synced Order {order_name}", synced=True)

			except Exception as e:
				publish(f"Error Syncing Order {order_name} : {e!s}", error=True)
				frappe.db.rollback(save_point=savepoint)
				continue

		if collection.has_next_page():
			frappe.db.commit()
			collection = _fetch_orders_from_shopify(from_=collection.next_page_url)
		else:
			_sync = False

	end_time = process_time()
	publish(f"Done in {end_time - start_time:.1f}s", done=True)
	return True


def publish(message, synced=False, error=False, done=False, br=True):
	frappe.publish_realtime(
		REALTIME_KEY,
		{
			"synced": synced,
			"error": error,
			"message": message + ("<br /><br />" if br else ""),
			"done": done,
		},
	)
