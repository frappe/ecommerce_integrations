from typing import List, NewType

import frappe
from frappe import _
from frappe.utils import get_url, now, to_markdown
from frappe.utils.nestedset import get_root_of
from stdnum.ean import is_valid as validate_barcode

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item import ecommerce_item
from ecommerce_integrations.unicommerce.api_client import JsonDict, UnicommerceAPIClient
from ecommerce_integrations.unicommerce.constants import (
	DEFAULT_WEIGHT_UOM,
	ITEM_BATCH_GROUP_FIELD,
	ITEM_HEIGHT_FIELD,
	ITEM_LENGTH_FIELD,
	ITEM_SYNC_CHECKBOX,
	ITEM_WIDTH_FIELD,
	MODULE_NAME,
	PRODUCT_CATEGORY_FIELD,
	SETTINGS_DOCTYPE,
	UNICOMMERCE_SKU_PATTERN,
)
from ecommerce_integrations.unicommerce.utils import create_unicommerce_log

ItemCode = NewType("ItemCode", str)

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
	"length": ITEM_LENGTH_FIELD,
	"width": ITEM_WIDTH_FIELD,
	"height": ITEM_HEIGHT_FIELD,
	"batchGroupCode": ITEM_BATCH_GROUP_FIELD,
	"maxRetailPrice": "standard_rate",
	"costPrice": "valuation_rate",
}

ERPNEXT_TO_UNI_ITEM_MAPPING = {v: k for k, v in UNI_TO_ERPNEXT_ITEM_MAPPING.items()}


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
	except Exception as e:
		create_unicommerce_log(
			status="Failure",
			message=f"Failed to import Item: {sku} from Unicommerce",
			response_data=response,
			make_new=True,
			exception=e,
			rollback=True,
		)
		raise e
	else:
		create_unicommerce_log(
			status="Success",
			message=f"Successfully imported Item: {sku} from Unicommerce",
			response_data=response,
			make_new=True,
		)


def _create_item_dict(uni_item):
	"""Helper function to build item document fields"""

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
	item_dict["name"] = item_dict["item_code"]  # when naming is by item series

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
	        1. Item group that has unicommerce_product_code linked.
	        2. Default Item group configured in Unicommerce settings.
	        3. root of Item Group tree."""

	item_group = frappe.db.get_value("Item Group", {PRODUCT_CATEGORY_FIELD: category_code})
	if category_code and item_group:
		return item_group

	default_item_group = frappe.db.get_single_value("Unicommerce Settings", "default_item_group")
	if default_item_group:
		return default_item_group

	return get_root_of("Item Group")


def upload_new_items(force=False) -> None:
	"""Upload new items to Unicommerce on hourly basis.

	All the items that have "sync_with_unicommerce" checked but do not have
	corresponding Ecommerce Item, are pushed to Unicommerce."""

	settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)

	if not (settings.is_enabled() and settings.upload_item_to_unicommerce):
		return

	new_items = _get_new_items()
	if not new_items:
		return

	log = create_unicommerce_log(status="Queued", message="Item sync initiated", make_new=True)
	synced_items = upload_items_to_unicommerce(new_items)

	unsynced_items = set(new_items) - set(synced_items)

	log.message = (
		"Item sync completed\n"
		f"Synced items: {', '.join(synced_items)}\n"
		f"Unsynced items: {', '.join(unsynced_items)}"
	)
	log.status = "Success"
	log.save()


def _get_new_items() -> List[ItemCode]:
	new_items = frappe.db.sql(
		f"""
			SELECT item.item_code
			FROM tabItem item
			LEFT JOIN `tabEcommerce Item` ei
				ON ei.erpnext_item_code = item.item_code
				WHERE ei.erpnext_item_code is NULL
					AND item.{ITEM_SYNC_CHECKBOX} = 1
		"""
	)

	return [item[0] for item in new_items]


def upload_items_to_unicommerce(
	item_codes: List[ItemCode], client: UnicommerceAPIClient = None
) -> List[ItemCode]:
	"""Upload multiple items to Unicommerce.

	Return Successfully synced item codes.
	"""
	if not client:
		client = UnicommerceAPIClient()

	synced_items = []

	for item_code in item_codes:
		item_data = _build_unicommerce_item(item_code)
		sku = item_data.get("skuCode")

		item_exists = bool(client.get_unicommerce_item(sku, log_error=False))
		_, status = client.create_update_item(item_data, update=item_exists)

		if status:
			_handle_ecommerce_item(item_code)
			synced_items.append(item_code)

	return synced_items


def _build_unicommerce_item(item_code: ItemCode) -> JsonDict:
	"""Build Unicommerce item JSON using an ERPNext item"""
	item = frappe.get_doc("Item", item_code)

	item_json = {}

	for erpnext_field, uni_field in ERPNEXT_TO_UNI_ITEM_MAPPING.items():
		value = item.get(erpnext_field)
		if value is not None:
			item_json[uni_field] = value

	item_json["enabled"] = not bool(item.get("disabled"))

	if item_json.get("description"):
		item_json["description"] = to_markdown(item_json["description"]) or item_json["description"]

	for barcode in item.barcodes:
		if not item_json.get("scanIdentifier"):
			# Set first barcode as scan identifier
			item_json["scanIdentifier"] = barcode.barcode
		if barcode.barcode_type == "EAN":
			item_json["ean"] = barcode.barcode
		elif barcode.barcode_type == "UPC-A":
			item_json["upc"] = barcode.barcode

	item_json["categoryCode"] = frappe.db.get_value(
		"Item Group", item.item_group, PRODUCT_CATEGORY_FIELD
	)
	# append site prefix to image url
	item_json["imageUrl"] = get_url(item.image)
	item_json["maxRetailPrice"] = item.standard_rate
	item_json["description"] = frappe.utils.strip_html_tags(item.description)
	item_json["costPrice"] = item.valuation_rate

	return item_json


def _handle_ecommerce_item(item_code: ItemCode) -> None:
	ecommerce_item = frappe.db.get_value(
		"Ecommerce Item", {"integration": MODULE_NAME, "erpnext_item_code": item_code}
	)

	if ecommerce_item:
		frappe.db.set_value("Ecommerce Item", ecommerce_item, "item_synced_on", now())
	else:
		frappe.get_doc(
			{
				"doctype": "Ecommerce Item",
				"integration": MODULE_NAME,
				"erpnext_item_code": item_code,
				"integration_item_code": item_code,
				"sku": item_code,
				"item_synced_on": now(),
			}
		).insert()


def validate_item(doc, method=None):
	"""Validate Item:

	1. item_code should  fulfill unicommerce SKU code requirements.
	2. Selected item group should have unicommerce product category.

	ref: http://support.unicommerce.com/index.php/knowledge-base/q-what-is-an-item-master-how-do-we-add-update-an-item-master/"""

	item = doc
	settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)

	if not settings.is_enabled() or not item.sync_with_unicommerce:
		return

	if not UNICOMMERCE_SKU_PATTERN.fullmatch(item.item_code):
		msg = _("Item code is not valid as per Unicommerce requirements.") + "<br>"
		msg += _("Unicommerce allows 3-45 character long alpha-numeric SKU code") + " "
		msg += _("with four special characters: . _ - /")
		frappe.throw(msg, title="Invalid SKU for Unicommerce")

	item_group = frappe.get_cached_doc("Item Group", item.item_group)
	if not item_group.get(PRODUCT_CATEGORY_FIELD):
		frappe.throw(
			_("Unicommerce Product category required in Item Group: {}").format(item_group.name)
		)
