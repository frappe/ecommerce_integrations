import time
import urllib.request

import dateutil
import frappe
from frappe import _

import ecommerce_integrations.amazon_sp_api.doctype.amazon_sp_api_settings.amazon_sp_api as sp_api


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
				create_sales_order(order)

			if not next_token:
				break

			orders_payload = call_sp_api_method(
				sp_api_method=orders.get_orders, created_after=created_after, next_token=next_token
			)

	except Exception as e:
		frappe.log_error(title="get_orders", message=e)


def get_order_items(order_id):
	try:
		orders = get_orders_instance()
		order_items_payload = call_sp_api_method(sp_api_method=orders.get_order_items, order_id=order_id)

		final_order_items = []
		warehouse = frappe.db.get_single_value("Amazon SP API Settings", "warehouse")

		while True:

			order_items_list = order_items_payload.get("OrderItems")
			next_token = order_items_payload.get("NextToken")

			for order_item in order_items_list:
				price = order_item.get("ItemPrice", {}).get("Amount", 0)

				final_order_items.append(
					{
						"item_code": get_item_code(order_item),
						"item_name": order_item.get("SellerSKU"),
						"description": order_item.get("Title"),
						"rate": price,
						"qty": order_item.get("QuantityOrdered"),
						"stock_uom": "Nos",
						"warehouse": warehouse,
						"conversion_factor": "1.0",
					}
				)

			if not next_token:
				break

			order_items_payload = call_sp_api_method(
				sp_api_method=orders.get_order_items, order_id=order_id, next_token=next_token
			)

		return final_order_items

	except Exception as e:
		frappe.log_error(title="get_order_items", message=e)


def get_charges_and_fees(order_id):
	try:
		finances = get_finances_instance()
		financial_events_payload = call_sp_api_method(
			sp_api_method=finances.list_financial_events_by_order_id, order_id=order_id
		)

		charges_and_fees = {"charges": [], "fees": []}

		while True:

			shipment_event_list = financial_events_payload.get("FinancialEvents", {}).get(
				"ShipmentEventList", []
			)
			next_token = financial_events_payload.get("NextToken")

			for shipment_event in shipment_event_list:

				if shipment_event:

					for shipment_item in shipment_event.get("ShipmentItemList", []):
						charges = shipment_item.get("ItemChargeList", [])
						fees = shipment_item.get("ItemFeeList", [])
						seller_sku = shipment_item.get("SellerSKU")

						for charge in charges:

							charge_type = charge.get("ChargeType")
							amount = charge.get("ChargeAmount", {}).get("CurrencyAmount", 0)

							if charge_type != "Principal" and float(amount) != 0:
								charge_account = get_account(charge_type)
								charges_and_fees.get("charges").append(
									{
										"charge_type": "Actual",
										"account_head": charge_account,
										"tax_amount": amount,
										"description": charge_type + " for " + seller_sku,
									}
								)

						for fee in fees:

							fee_type = fee.get("FeeType")
							amount = fee.get("FeeAmount", {}).get("CurrencyAmount", 0)

							if float(amount) != 0:
								fee_account = get_account(fee_type)
								charges_and_fees.get("fees").append(
									{
										"charge_type": "Actual",
										"account_head": fee_account,
										"tax_amount": amount,
										"description": fee_type + " for " + seller_sku,
									}
								)

			if not next_token:
				break

			financial_events_payload = call_sp_api_method(
				sp_api_method=finances.list_financial_events_by_order_id,
				order_id=order_id,
				next_token=next_token,
			)

		return charges_and_fees

	except Exception as e:
		frappe.log_error(title="get_charges_and_fees", message=e)


# Helper Functions


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


def get_finances_instance():
	amz_settings = frappe.get_doc("Amazon SP API Settings")
	finances = sp_api.Finances(
		iam_arn=amz_settings.iam_arn,
		client_id=amz_settings.client_id,
		client_secret=amz_settings.client_secret,
		refresh_token=amz_settings.refresh_token,
		aws_access_key=amz_settings.aws_access_key,
		aws_secret_key=amz_settings.aws_secret_key,
		country_code=amz_settings.country,
	)

	return finances


def get_reports_instance():
	amz_settings = frappe.get_doc("Amazon SP API Settings")
	reports = sp_api.Reports(
		iam_arn=amz_settings.iam_arn,
		client_id=amz_settings.client_id,
		client_secret=amz_settings.client_secret,
		refresh_token=amz_settings.refresh_token,
		aws_access_key=amz_settings.aws_access_key,
		aws_secret_key=amz_settings.aws_secret_key,
		country_code=amz_settings.country,
	)

	return reports


def get_catalog_items_instance():
	amz_settings = frappe.get_doc("Amazon SP API Settings")
	catalog_items = sp_api.CatalogItems(
		iam_arn=amz_settings.iam_arn,
		client_id=amz_settings.client_id,
		client_secret=amz_settings.client_secret,
		refresh_token=amz_settings.refresh_token,
		aws_access_key=amz_settings.aws_access_key,
		aws_secret_key=amz_settings.aws_secret_key,
		country_code=amz_settings.country,
	)

	return catalog_items


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


def create_customer(order):
	order_customer_name = ""
	buyer_info = order.get("BuyerInfo")

	if buyer_info and buyer_info.get("BuyerName"):
		order_customer_name = buyer_info.get("BuyerName")
	else:
		order_customer_name = f"Buyer - {order.get('AmazonOrderId')}"

	existing_customer_name = frappe.db.get_value(
		"Customer", filters={"name": order_customer_name}, fieldname="name"
	)

	if existing_customer_name:
		filters = [
			["Dynamic Link", "link_doctype", "=", "Customer"],
			["Dynamic Link", "link_name", "=", existing_customer_name],
			["Dynamic Link", "parenttype", "=", "Contact"],
		]

		existing_contacts = frappe.get_list("Contact", filters)

		if not existing_contacts:
			new_contact = frappe.new_doc("Contact")
			new_contact.first_name = order_customer_name
			new_contact.append("links", {"link_doctype": "Customer", "link_name": existing_customer_name})
			new_contact.insert()

		return existing_customer_name
	else:
		amz_settings = frappe.get_doc("Amazon SP API Settings")
		new_customer = frappe.new_doc("Customer")
		new_customer.customer_name = order_customer_name
		new_customer.customer_group = amz_settings.customer_group
		new_customer.territory = amz_settings.territory
		new_customer.customer_type = amz_settings.customer_type
		new_customer.save()

		new_contact = frappe.new_doc("Contact")
		new_contact.first_name = order_customer_name
		new_contact.append("links", {"link_doctype": "Customer", "link_name": new_customer.name})

		new_contact.insert()

		return new_customer.name


def create_address(order, customer_name):
	shipping_address = order.get("ShippingAddress")

	if not shipping_address:
		return
	else:
		make_address = frappe.new_doc("Address")
		make_address.address_line1 = shipping_address.get("AddressLine1", "Not Provided")
		make_address.city = shipping_address.get("City", "Not Provided")
		make_address.state = shipping_address.get("StateOrRegion")
		make_address.pincode = shipping_address.get("PostalCode")

		filters = [
			["Dynamic Link", "link_doctype", "=", "Customer"],
			["Dynamic Link", "link_name", "=", customer_name],
			["Dynamic Link", "parenttype", "=", "Address"],
		]
		existing_address = frappe.get_list("Address", filters)

		for address in existing_address:
			address_doc = frappe.get_doc("Address", address["name"])
			if (
				address_doc.address_line1 == make_address.address_line1
				and address_doc.pincode == make_address.pincode
			):
				return address

		make_address.append("links", {"link_doctype": "Customer", "link_name": customer_name})
		make_address.address_type = "Shipping"
		make_address.insert()


def get_item_code(order_item):
	sku = order_item.get("SellerSKU")

	if sku:
		if frappe.db.exists({"doctype": "Item", "item_code": sku}):
			return sku
	else:
		raise KeyError("SellerSKU")


def get_account(name):
	account_name = frappe.db.get_value("Account", {"account_name": "Amazon {0}".format(name)})

	if not account_name:
		amz_settings = frappe.get_doc("Amazon SP API Settings")
		try:
			new_account = frappe.new_doc("Account")
			new_account.account_name = "Amazon {0}".format(name)
			new_account.company = amz_settings.company
			new_account.parent_account = amz_settings.market_place_account_group
			new_account.insert(ignore_permissions=True)
			account_name = new_account.name
		except Exception as e:
			frappe.log_error(message=e, title="Create Account")

	return account_name


def create_sales_order(order):
	customer_name = create_customer(order)
	create_address(order, customer_name)

	order_id = order.get("AmazonOrderId")
	sales_order = frappe.db.get_value(
		"Sales Order", filters={"amazon_order_id": order_id}, fieldname="name"
	)

	if sales_order:
		return
	else:
		items = get_order_items(order_id)
		delivery_date = dateutil.parser.parse(order.get("LatestShipDate")).strftime("%Y-%m-%d")
		transaction_date = dateutil.parser.parse(order.get("PurchaseDate")).strftime("%Y-%m-%d")

		sales_order = frappe.get_doc(
			{
				"doctype": "Sales Order",
				"naming_series": "SO-",
				"amazon_order_id": order_id,
				"marketplace_id": order.get("MarketplaceId"),
				"customer": customer_name,
				"delivery_date": delivery_date,
				"transaction_date": transaction_date,
				"items": items,
				"company": frappe.db.get_single_value("Amazon SP API Settings", "company"),
			}
		)

		taxes_and_charges = frappe.db.get_single_value("Amazon SP API Settings", "taxes_charges")

		try:
			if taxes_and_charges:
				charges_and_fees = get_charges_and_fees(order_id)

				for charge in charges_and_fees.get("charges"):
					sales_order.append("taxes", charge)

				for fee in charges_and_fees.get("fees"):
					sales_order.append("taxes", fee)

			sales_order.insert(ignore_permissions=True)
			sales_order.submit()

		except Exception:
			import traceback

			frappe.log_error(message=traceback.format_exc(), title="Create Sales Order")


def create_manufacturer(amazon_item):
	manufacturer_name = amazon_item.get("AttributeSets")[0].get("Manufacturer")

	if not manufacturer_name:
		return None

	existing_manufacturer = frappe.db.get_value(
		"Manufacturer", filters={"short_name": manufacturer_name}
	)

	if not existing_manufacturer:
		manufacturer = frappe.new_doc("Manufacturer")
		manufacturer.short_name = manufacturer_name
		manufacturer.insert()
		return manufacturer.short_name
	else:
		return existing_manufacturer


def create_brand(amazon_item):
	brand_name = amazon_item.get("AttributeSets")[0].get("Brand")

	if not brand_name:
		return None

	existing_brand = frappe.db.get_value("Brand", filters={"brand": brand_name})

	if not existing_brand:
		brand = frappe.new_doc("Brand")
		brand.brand = brand_name
		brand.insert()
		return brand.brand
	else:
		return existing_brand


def create_item_group(amazon_item, amz_settings):
	item_group_name = amazon_item.get("AttributeSets")[0].get("ProductGroup")

	if item_group_name:
		item_group = frappe.db.get_value("Item Group", filters={"item_group_name": item_group_name})

		if not item_group:
			new_item_group = frappe.new_doc("Item Group")
			new_item_group.item_group_name = item_group_name
			new_item_group.parent_item_group = amz_settings.parent_item_group
			new_item_group.insert()
			return new_item_group.item_group_name

		return item_group.item_group_name

	raise (KeyError("ProductGroup"))


def create_ecommerce_item(amazon_item, item_code, sku):
	ecommerce_item = frappe.new_doc("Ecommerce Item")
	ecommerce_item.integration = frappe.get_meta("Amazon SP API Settings").module
	ecommerce_item.erpnext_item_code = item_code
	ecommerce_item.integration_item_code = (
		amazon_item.get("Identifiers", {}).get("MarketplaceASIN", {}).get("ASIN")
	)
	ecommerce_item.sku = sku
	ecommerce_item.insert(ignore_permissions=True)


def create_item_price(amazon_item, item_code):
	item_price = frappe.new_doc("Item Price")
	item_price.price_list = frappe.db.get_single_value("Amazon SP API Settings", "price_list")
	item_price.price_list_rate = (
		amazon_item.get("AttributeSets")[0].get("ListPrice", {}).get("Amount") or 0
	)
	item_price.item_code = item_code
	item_price.insert()


def create_item(amazon_item, sku):
	if frappe.db.get_value("Ecommerce Item", filters={"sku": sku}):
		return

	amz_settings = frappe.get_doc("Amazon SP API Settings")

	# Create Item
	item = frappe.new_doc("Item")
	item.item_code = sku
	item.item_group = create_item_group(amazon_item, amz_settings)
	item.description = amazon_item.get("AttributeSets")[0].get("Title")
	item.brand = create_brand(amazon_item)
	item.manufacturer = create_manufacturer(amazon_item)
	item.image = amazon_item.get("AttributeSets")[0].get("SmallImage", {}).get("URL")
	item.append("item_defaults", {"company": amz_settings.company})
	item.insert(ignore_permissions=True)

	# Create Ecommerce Item
	create_ecommerce_item(amazon_item, item.item_code, sku)

	# Create Item Price
	create_item_price(amazon_item, item.item_code)

	return item.name


def create_report(
	report_type="GET_FLAT_FILE_OPEN_LISTINGS_DATA", data_start_time=None, data_end_time=None
):
	try:
		reports = get_reports_instance()
		response = reports.create_report(
			report_type=report_type, data_start_time=data_start_time, data_end_time=data_end_time,
		)

		return response.get("reportId")

	except Exception as e:
		frappe.log_error(title="create_report", message=e)


def get_report_document(report_id):
	try:
		reports = get_reports_instance()

		for x in range(3):
			response = reports.get_report(report_id)
			processingStatus = response.get("processingStatus")

			if not processingStatus:
				raise (KeyError("processingStatus"))
			elif processingStatus in ["IN_PROGRESS", "IN_QUEUE"]:
				time.sleep(15)
				continue
			elif processingStatus in ["CANCELLED", "FATAL"]:
				raise (f"Report Processing Status: {processingStatus}")
			elif processingStatus == "DONE":
				report_document_id = response.get("reportDocumentId")

				if report_document_id:
					response = reports.get_report_document(report_document_id)
					url = response.get("url")

					if url:
						rows = []

						for line in urllib.request.urlopen(url):
							decoded_line = line.decode("utf-8").replace("\t", "\n")
							row = decoded_line.splitlines()
							rows.append(row)

						fields = rows[0]
						rows.pop(0)

						data = []

						for row in rows:
							data_row = {}
							for index, value in enumerate(row):
								data_row[fields[index]] = value
							data.append(data_row)

						return data
					raise (KeyError("url"))
				raise (KeyError("reportDocumentId"))
	except Exception as e:
		frappe.log_error(title="get_report_document", message=e)


def get_products_details():
	report_document = get_report_document(create_report())

	if report_document:
		catalog_items = get_catalog_items_instance()

		for asin in report_document.get("asin1") or report_document.get("product-id"):
			amazon_item = catalog_items.get_catalog_item(asin=asin)
			create_item(amazon_item, asin)

	raise ("No Product!")
