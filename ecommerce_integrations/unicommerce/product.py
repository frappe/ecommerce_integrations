import frappe
from frappe import _
from frappe.utils.nestedset import get_root_of
from stdnum.ean import is_valid as validate_barcode

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item import ecommerce_item
from ecommerce_integrations.unicommerce.api_client import UnicommerceAPIClient
from ecommerce_integrations.unicommerce.constants import DEFAULT_WEIGHT_UOM, MODULE_NAME
from ecommerce_integrations.unicommerce.utils import create_unicommerce_log

# unicommerce product to ERPNext item mapping
# reference: https://documentation.unicommerce.com/docs/itemtype-get.html
UNI_TO_ERPNEXT_ITEM_MAPPING = {
	"skuCode": "item_code",
	"name": "item_name",
	"description": "description",
	"weight": "weight_per_unit",  # weight_uom = always grams
	"brand": "brand",  # Link Field, migth not exist
	"shelfLife": "shelf_life_in_days",
	"hsnCode": "gst_hsn_code",
	"imageUrl": "image",
}

ERPNEXT_TO_UNI_ITEM_MAPPING = {v:k for k,v in UNI_TO_ERPNEXT_ITEM_MAPPING.items()}


def import_product_from_unicommerce(sku: str, client: UnicommerceAPIClient = None) -> None:
	"""Sync specified SKU from Unicommerce."""

	if not client:
		client = UnicommerceAPIClient()

	response = client.get_unicommerce_item(sku)

	try:
		if not response:
			frappe.throw(_("Unicommerce item not found"))

		item = response["itemTypeDTO"]
		if _check_and_match_existing_item(item):
			return

		item_dict = _create_item_dict(item)
		ecommerce_item.create_ecommerce_item(MODULE_NAME, integration_item_code=sku, item_dict=item_dict)
	except Exception:
		create_unicommerce_log(
			status="Failure",
			message=f"Failed to import Item: {sku} from Unicommerce",
			response_data=response,
		)
	else:
		create_unicommerce_log(
			status="Success",
			message=f"Successfully imported Item: {sku} from Unicommerce",
			response_data=response,
		)


def _create_item_dict(uni_item):
	""" Helper function to build item document fields"""

	item_dict = {"weight_uom": DEFAULT_WEIGHT_UOM}

	_validate_create_brand(uni_item.get("brand"))

	for uni_field, erpnext_field in UNI_TO_ERPNEXT_ITEM_MAPPING.items():

		value = uni_item.get(uni_field)
		if not _validate_field(erpnext_field, value):
			continue

		item_dict[erpnext_field] = value

	item_dict["barcodes"] = _get_barcode_data(uni_item)
	item_dict["disabled"] = int(not uni_item.get("enabled"))
	item_dict["item_group"] = _get_item_group(uni_item.get("categoryCode"))

	return item_dict


def _get_barcode_data(uni_item):
	"""Extract barcode information from Unicommerce item and return as child doctype row for Item table"""
	barcodes = []

	ean = uni_item.get("ean")
	upc = uni_item.get("upc")

	if ean and validate_barcode(ean):
		barcodes.append({"barcode": ean, "barcode_type": "EAN"})
	if upc and validate_barcode(upc):
		barcodes.append({"barcode": upc, "barcode_type": "UPC-A"})

	return barcodes


def _check_and_match_existing_item(uni_item):
	"""Tries to match new item with existing item using SKU == item_code.

	Returns true if matched and linked.
	"""

	sku = uni_item["skuCode"]
	item_name = frappe.db.get_value("Item", {"item_code": sku})
	if item_name:
		try:
			ecommerce_item = frappe.get_doc(
				{
					"doctype": "Ecommerce Item",
					"integration": MODULE_NAME,
					"erpnext_item_code": item_name,
					"integration_item_code": sku,
					"has_variants": 0,
					"sku": sku,
				}
			)
			ecommerce_item.insert()
			return True
		except Exception:
			return False


def _validate_create_brand(brand):
	"""Create the brand if it does not exist."""
	if not brand:
		return

	if not frappe.db.exists("Brand", brand):
		frappe.get_doc(doctype="Brand", brand=brand).insert()


def _validate_field(item_field, name):
	"""Check if field exists in item doctype, if it's a link field then also check if linked document exists"""
	meta = frappe.get_meta("Item")
	field = meta.get_field(item_field)
	if not field:
		return False

	if field.fieldtype != "Link":
		return True

	doctype = field.options
	return bool(frappe.db.exists(doctype, name))


def _get_item_group(category_code):
	"""Given unicommerce category code find the Item group in ERPNext.

	Returns item group with following priority:
	        1. Item group with same name as categoryCode on Unicommerce.
	        2. Default Item group configured in Unicommerce settings.
	        3. root of Item Group tree."""

	if category_code and frappe.db.exists("Item Group", category_code):
		return category_code

	default_item_group = frappe.db.get_single_value("Unicommerce Settings", "default_item_group")
	if default_item_group:
		return default_item_group

	return get_root_of("Item Group")
