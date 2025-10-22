import base64
from typing import Any

import frappe
import requests
from frappe import _
from frappe.utils import cint, cstr, get_datetime
from pytz import timezone

from ecommerce_integrations.unicommerce.constants import SETTINGS_DOCTYPE
from ecommerce_integrations.unicommerce.utils import create_unicommerce_log

JsonDict = dict[str, Any]


class UnicommerceAPIClient:
	"""Wrapper around Unicommerce REST API

	API docs: https://documentation.unicommerce.com/
	"""

	def __init__(
		self,
		url: str | None = None,
		access_token: str | None = None,
	):
		self.settings = frappe.get_doc(SETTINGS_DOCTYPE)
		self.base_url = url or f"https://{self.settings.unicommerce_site}"
		self.access_token = access_token
		self.__initialize_auth()

	def __initialize_auth(self):
		"""Initialize and setup authentication details"""
		if not self.access_token:
			self.settings.renew_tokens()
			self.access_token = self.settings.get_password("access_token")

		self._auth_headers = {"Authorization": f"Bearer {self.access_token}"}

	def request(
		self,
		endpoint: str,
		method: str = "POST",
		headers: JsonDict | None = None,
		body: JsonDict | None = None,
		params: JsonDict | None = None,
		files: JsonDict | None = None,
		log_error=True,
	) -> tuple[JsonDict, bool]:
		if headers is None:
			headers = {}

		headers.update(self._auth_headers)

		url = self.base_url + endpoint

		try:
			response = requests.request(
				url=url, method=method, headers=headers, json=body, params=params, files=files
			)
			# unicommerce gives useful info in response text, show it in error logs
			response.reason = cstr(response.reason) + cstr(response.text)
			response.raise_for_status()
		except Exception:
			if log_error:
				create_unicommerce_log(status="Error", make_new=True)
			return None, False

		if method == "GET" and "application/json" not in response.headers.get("content-type"):
			return response.content, True

		data = frappe._dict(response.json())
		status = data.successful if data.successful is not None else True

		if not status:
			req = response.request
			url = f"URL: {req.url}"
			body = f"body:  {req.body.decode('utf-8')}"
			request_data = "\n\n".join([url, body])
			message = ", ".join(cstr(error["message"]) for error in data.errors)
			create_unicommerce_log(
				status="Error", response_data=data, request_data=request_data, message=message, make_new=True
			)

		return data, status

	def get_unicommerce_item(self, sku: str, log_error=True) -> JsonDict | None:
		"""Get Unicommerce item data for specified SKU code.

		ref: https://documentation.unicommerce.com/docs/itemtype-get.html
		"""
		item, status = self.request(
			endpoint="/services/rest/v1/catalog/itemType/get", body={"skuCode": sku}, log_error=log_error
		)
		if status:
			return item

	def create_update_item(self, item_dict: JsonDict, update=False) -> tuple[JsonDict, bool]:
		"""Create/update item on unicommerce.

		ref: https://documentation.unicommerce.com/docs/createoredit-itemtype.html
		"""

		endpoint = "/services/rest/v1/catalog/itemType/createOrEdit"
		if update:
			# Edit has separate endpoint even though docs suggest otherwise
			endpoint = "/services/rest/v1/catalog/itemType/edit"
		return self.request(endpoint=endpoint, body={"itemType": item_dict})

	def get_sales_order(self, order_code: str) -> JsonDict | None:
		"""Get details for a sales order.

		ref: https://documentation.unicommerce.com/docs/saleorder-get.html
		"""

		order, status = self.request(
			endpoint="/services/rest/v1/oms/saleorder/get", body={"code": order_code}
		)
		if status and "saleOrderDTO" in order:
			return order["saleOrderDTO"]

	def search_sales_order(
		self,
		from_date: str | None = None,
		to_date: str | None = None,
		status: str | None = None,
		channel: str | None = None,
		facility_codes: list[str] | None = None,
		updated_since: int | None = None,
	) -> list[JsonDict] | None:
		"""Search sales order using specified parameters and return search results.

		ref: https://documentation.unicommerce.com/docs/saleorder-search.html
		"""
		body = {
			"status": status,
			"channel": channel,
			"facility_codes": facility_codes,
			"fromDate": _utc_timeformat(from_date) if from_date else None,
			"toDate": _utc_timeformat(to_date) if to_date else None,
			"updatedSinceInMinutes": updated_since,
		}

		# remove None values.
		body = {k: v for k, v in body.items() if v is not None}

		search_results, status = self.request(endpoint="/services/rest/v1/oms/saleOrder/search", body=body)

		if status and "elements" in search_results:
			return search_results["elements"]

	def get_inventory_snapshot(
		self, sku_codes: list[str], facility_code: str, updated_since: int = 1430
	) -> JsonDict | None:
		"""Get current inventory snapshot.

		ref: https://documentation.unicommerce.com/docs/inventory-snapshot.html
		"""

		extra_headers = {"Facility": facility_code}

		body = {"itemTypeSKUs": sku_codes, "updatedSinceInMinutes": updated_since}

		response, status = self.request(
			endpoint="/services/rest/v1/inventory/inventorySnapshot/get",
			headers=extra_headers,
			body=body,
		)

		if status:
			return response

	def bulk_inventory_update(self, facility_code: str, inventory_map: dict[str, int]):
		"""Bulk update inventory on unicommerce using SKU and qty.

		The qty should be "total" quantity.
		ref: https://documentation.unicommerce.com/docs/adjust-inventory-bulk.html
		"""

		extra_headers = {"Facility": facility_code}

		inventory_adjustments = []
		for sku, qty in inventory_map.items():
			inventory_adjustments.append(
				{
					"itemSKU": sku,
					"quantity": qty,
					"shelfCode": "DEFAULT",  # XXX
					"inventoryType": "GOOD_INVENTORY",
					"adjustmentType": "REPLACE",
					"facilityCode": facility_code,
				}
			)

		response, status = self.request(
			endpoint="/services/rest/v1/inventory/adjust/bulk",
			headers=extra_headers,
			body={"inventoryAdjustments": inventory_adjustments},
		)

		if not status:
			return response, status
		else:
			# parse result by item
			try:
				item_wise_response = response["inventoryAdjustmentResponses"]
				item_wise_status = {
					item["facilityInventoryAdjustment"]["itemSKU"]: item["successful"]
					for item in item_wise_response
				}
				if False in item_wise_status.values():
					create_unicommerce_log(
						status="Failure",
						response_data=response,
						message="Inventory sync failed for some items",
						make_new=True,
					)
				return item_wise_status, status
			except Exception:
				return response, False

	def create_sales_invoice(
		self, so_code: str, so_item_codes: list[str], facility_code: str
	) -> JsonDict | None:
		body = {"saleOrderCode": so_code, "saleOrderItemCodes": so_item_codes}
		extra_headers = {"Facility": facility_code}

		response, status = self.request(
			endpoint="/services/rest/v1/invoice/createInvoiceBySaleOrderCode",
			body=body,
			headers=extra_headers,
		)
		return response

	def create_invoice_by_shipping_code(self, shipping_package_code: str, facility_code: str):
		body = {"shippingPackageCode": shipping_package_code}
		response, status = self.request(
			endpoint="/services/rest/v1/oms/shippingPackage/createInvoice",
			body=body,
			headers={"Facility": facility_code},
		)

		return response

	def create_invoice_and_assign_shipper(self, shipping_package_code: str, facility_code: str):
		"""
		 Invoice and label generation API for self-shipped orders.

		ref: https://documentation.unicommerce.com/docs/pos-shippingpackage-createinvoice-allocateshippingprovider.html
		"""
		body = {
			"shippingPackageCode": shipping_package_code,
		}
		response, status = self.request(
			endpoint="/services/rest/v1/oms/shippingPackage/createInvoiceAndAllocateShippingProvider",
			body=body,
			headers={"Facility": facility_code},
		)

		return response

	def create_invoice_and_label_by_shipping_code(
		self, shipping_package_code: str, facility_code: str, generate_label: bool = True
	):
		"""
		 Invoice and label generation API for marketplace orders.

		ref: https://documentation.unicommerce.com/docs/create_invoiceandlabel_by_shippingpackagecode.html
		"""
		body = {
			"shippingPackageCode": shipping_package_code,
			"generateUniwareShippingLabel": generate_label,
		}
		response, status = self.request(
			endpoint="/services/rest/v1/oms/shippingPackage/createInvoiceAndGenerateLabel",
			body=body,
			headers={"Facility": facility_code},
		)

		return response

	def get_sales_invoice(
		self, shipping_package_code: str, facility_code: str, is_return: bool = False
	) -> JsonDict | None:
		"""Get invoice details

		ref: https://documentation.unicommerce.com/docs/invoice-getdetails.html
		"""
		extra_headers = {"Facility": facility_code}
		response, status = self.request(
			endpoint="/services/rest/v1/invoice/details/get",
			body={"shippingPackageCode": shipping_package_code, "return": is_return},
			headers=extra_headers,
		)

		if status:
			return response

	def update_shipping_package(
		self,
		shipping_package_code: str,
		facility_code: str,
		package_type_code: str,
		weight: int = 0,
		length: int = 0,
		width: int = 0,
		height: int = 0,
	):
		"""Update shipping package dimensions and other details on Unicommerce.

		ref: https://documentation.unicommerce.com/docs/shippingpackage-edit.html
		"""

		body = {
			"shippingPackageCode": shipping_package_code,
			"shippingPackageTypeCode": package_type_code,
		}

		def _positive(numbers):
			for number in numbers:
				if cint(number) <= 0:
					return False
			return True

		if _positive([weight]):
			body["actualWeight"] = weight

		if _positive([length, width, height]):
			body["shippingBox"] = {"length": length, "width": width, "height": height}

		extra_headers = {"Facility": facility_code}
		return self.request(
			endpoint="/services/rest/v1/oms/shippingPackage/edit",
			body=body,
			headers=extra_headers,
		)

	def get_invoice_label(self, shipping_package_code: str, facility_code: str) -> str | None:
		"""Get the generated label for a given shipping package.

		ref: undocumented.
		"""
		extra_headers = {"Facility": facility_code}
		pdf, status = self.request(
			endpoint="/services/rest/v1/oms/shipment/show",
			method="GET",
			params={"shippingPackageCodes": shipping_package_code},
			headers=extra_headers,
		)
		if status and pdf:
			return base64.b64encode(pdf)

	def create_and_close_shipping_manifest(
		self,
		channel: str,
		shipping_provider_code: str,
		shipping_method_code: str,
		shipping_packages: list[str],
		facility_code: str,
		third_party_shipping: bool = True,
	):
		"""Create and close the shipping manifest in Unicommerce

		Ref: https://documentation.unicommerce.com/docs/pos-shippingmanifest-create-close.html"""

		# Even though docs dont mention it, facility code is a required header.
		extra_headers = {"Facility": facility_code}
		body = {
			"channel": channel,
			"shippingProviderCode": shipping_provider_code,
			"shippingMethodCode": shipping_method_code,
			"thirdPartyShipping": third_party_shipping,
			"shippingPackageCodes": shipping_packages,
		}

		response, status = self.request(
			endpoint="/services/rest/v1/oms/shippingManifest/createclose",
			body=body,
			headers=extra_headers,
		)

		if status:
			return response

	def get_shipping_manifest(self, shipping_manifest_code, facility_code):
		extra_headers = {"Facility": facility_code}
		response, status = self.request(
			endpoint="/services/rest/v1/oms/shippingManifest/get",
			body={"shippingManifestCode": shipping_manifest_code},
			headers=extra_headers,
		)
		if status:
			return response

	def search_shipping_packages(
		self,
		facility_code: str,
		channel: str | None = None,
		statuses: list[str] | None = None,
		updated_since: int | None = 6 * 60,
	):
		"""Search shipping packages on unicommerce matching specified criterias.

		Ref: https://documentation.unicommerce.com/docs/pos-shippingpackage-search.html"""
		body = {
			"statuses": statuses,
			"channelCode": channel,
			"updatedSinceInMinutes": updated_since,
		}
		extra_headers = {"Facility": facility_code}

		# remove None values.
		body = {k: v for k, v in body.items() if v is not None}

		search_results, statuses = self.request(
			endpoint="/services/rest/v1/oms/shippingPackage/search",
			body=body,
			headers=extra_headers,
		)

		if statuses and "elements" in search_results:
			return search_results["elements"]

	def create_import_job(
		self,
		job_name: str,
		csv_filename: str,
		facility_code: str,
		job_type: str = "CREATE_NEW",
	):
		"""Create import job by specifying job name and CSV file

		args:
		        job_name: import job code string specified by unicommerce
		        csv_filename: name of csv file.
		        facility_code: facility where import should happen
		        job_type: create / or update code.
		"""

		url_params = {"name": job_name, "importOption": job_type}

		extra_headers = {
			"Facility": facility_code,
			"cache-control": "no-cache",
		}

		file_obj = _safe_open_csv(csv_filename)
		files = [("file", (csv_filename, file_obj, "text/csv"))]

		response, status = self.request(
			endpoint="/services/rest/v1/data/import/job/create",
			params=url_params,
			files=files,
			headers=extra_headers,
		)

		file_obj.close()
		return response


def _utc_timeformat(datetime) -> str:
	"""Get datetime in UTC/GMT as required by Unicommerce"""
	return get_datetime(datetime).astimezone(timezone("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_open_csv(csv_name):
	from frappe.utils.file_manager import get_file_path

	if csv_name.split(".")[-1].lower().strip() != "csv":
		frappe.throw(_("Only CSV files can be uploaded."))

	filepath = get_file_path(csv_name)
	return open(filepath, "rb")
