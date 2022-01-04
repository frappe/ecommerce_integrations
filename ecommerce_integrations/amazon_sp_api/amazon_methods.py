import time

import amazon_sp_api as sp_api
import frappe
from frappe import _


def return_as_list(input):
	if isinstance(input, list):
		return input
	else:
		return [input]


def call_sp_api_method(sp_api_method, **kwargs):
	amz_settings = frappe.get_doc("Amazon SP API Settings")
	max_retries = amz_settings.max_retry_limit

	for x in range(max_retries):
		try:
			result = sp_api_method(**kwargs)
			return result.get("payload")
		except Exception as e:
			frappe.log_error(message=e, title=f'Method "{sp_api_method.__name__}" failed')
			time.sleep(3)
			continue

	amz_settings.enable_sync = 0
	amz_settings.save()

	frappe.throw(_("Sync has been temporarily disabled because maximum retries have been exceeded!"))


def get_orders_instance():
	amz_settings = frappe.get_doc("Amazon SP API Settings")
	orders = sp_api.Orders(
		iam_arn=amz_settings.iam_arn,
		client_id=amz_settings.client_id,
		client_secret=amz_settings.client_secret,
		refresh_token=amz_settings.refresh_token,
		aws_access_key=amz_settings.aws_access_key,
		aws_secret_key=amz_settings.aws_secret_key,
		country_code=amz_settings.country,
	)

	return orders


def get_orders(created_after):
	try:
		orders = get_orders_instance()
		order_statuses = [
			"PendingAvailability",
			"Pending",
			"Unshipped",
			"PartiallyShipped",
			"Shipped",
			"InvoiceUnconfirmed",
			"Canceled",
			"Unfulfillable",
		]
		fulfillment_channels = ["FBA", "SellerFulfilled"]

		orders_payload = call_sp_api_method(
			sp_api_method=orders.get_orders,
			created_after=created_after,
			order_statuses=order_statuses,
			fulfillment_channels=fulfillment_channels,
			max_results=50,
		)

		while True:

			orders_list = orders_payload.get("Orders")
			next_token = orders_payload.get("NextToken")

			if not orders_list or len(orders_list) == 0:
				break

			for order in orders_list:
				create_sales_order(order, created_after)

			if not next_token:
				break

			orders_payload = call_sp_api_method(
				sp_api_method=orders.get_orders, created_after=created_after, next_token=next_token
			)

	except Exception as e:
		frappe.log_error(title="get_orders", message=e)


def create_sales_order(order, created_after):
	pass
