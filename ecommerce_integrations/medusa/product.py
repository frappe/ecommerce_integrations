# Copyright (c) 2024, Frappe and contributors
# For license information, please see LICENSE

import frappe
from frappe import _, msgprint
from frappe.utils import cstr, flt
from frappe.utils.nestedset import get_root_of

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item import ecommerce_item
from ecommerce_integrations.medusa.connection import MedusaClient
from ecommerce_integrations.medusa.constants import (
	ITEM_SELLING_RATE_FIELD,
	MODULE_NAME,
	SETTING_DOCTYPE,
	WEIGHT_UOM,
)
from ecommerce_integrations.medusa.utils import create_medusa_log, to_amount

# Medusa v2's convention for a product without meaningful options (used by the
# admin dashboard and the POST /admin/products docs): a single option titled
# "Default option" with the single value "Default option value".
DEFAULT_OPTION_TITLE = "Default option"
DEFAULT_OPTION_VALUE = "Default option value"


class MedusaProduct:
	"""Medusa v2 product -> ERPNext Item.

	Mirrors ``ecommerce_integrations.shopify.product.ShopifyProduct``. A Medusa
	product becomes either a single ERPNext Item (one variant / no options) or an
	Item template + variant Items (multiple variants / options).
	"""

	def __init__(self, product, variant_id: str | None = None, sku: str | None = None):
		# accept either a fully-fetched product dict or a product id
		if isinstance(product, dict):
			self.product = product
			self.product_id = str(product.get("id"))
		else:
			self.product_id = str(product)
			self.product = None

		self.variant_id = str(variant_id) if variant_id else None
		self.sku = str(sku) if sku else None
		self.has_variants = 0
		self.setting = frappe.get_doc(SETTING_DOCTYPE)

		if not self.setting.is_enabled():
			frappe.throw(_("Can not create Medusa product when integration is disabled."))

	# -- helpers ----------------------------------------------------------------

	def _fetch(self) -> dict:
		if self.product is None:
			self.product = MedusaClient().get_product(self.product_id)
		return self.product

	def is_synced(self) -> bool:
		return ecommerce_item.is_synced(
			MODULE_NAME,
			integration_item_code=self.product_id,
			variant_id=self.variant_id,
			sku=self.sku,
		)

	def get_erpnext_item(self):
		return ecommerce_item.get_erpnext_item(
			MODULE_NAME,
			integration_item_code=self.product_id,
			variant_id=self.variant_id,
			sku=self.sku,
			has_variants=self.has_variants,
		)

	# -- sync -------------------------------------------------------------------

	def sync_product(self):
		if self.is_synced():
			return
		product = self._fetch()
		self._make_item(product)

	def _make_item(self, product):
		warehouse = self.setting.warehouse
		variants = product.get("variants") or []

		if _has_variants(product):
			self.has_variants = 1
			attributes = self._create_attributes(product)
			self._create_item(product, warehouse, has_variant=1, attributes=attributes)
			self._create_item_variants(product, warehouse, attributes)
		else:
			# single item: collapse the (single) variant onto the product
			variant = variants[0] if variants else {}
			product = dict(product)
			product["variant_id"] = variant.get("id")
			product["sku"] = variant.get("sku")
			product["weight"] = product.get("weight") or variant.get("weight")
			product["price"] = _get_variant_price(variant, _setting_currency(self.setting))
			self._create_item(product, warehouse)

	def _create_attributes(self, product):
		"""Build ``Item Attribute`` rows from Medusa ``options``/``values``."""
		attributes = []
		for option in product.get("options") or []:
			name = option.get("title")
			values = [v.get("value") for v in option.get("values") or [] if v.get("value")]
			# de-dup while keeping order (Medusa repeats values across variants)
			values = list(dict.fromkeys(values))

			if not frappe.db.get_value("Item Attribute", name, "name"):
				frappe.get_doc(
					{
						"doctype": "Item Attribute",
						"attribute_name": name,
						"item_attribute_values": [{"attribute_value": v, "abbr": v} for v in values],
					}
				).insert()
				attributes.append({"attribute": name})
			else:
				item_attr = frappe.get_doc("Item Attribute", name)
				if not item_attr.numeric_values:
					self._set_new_attribute_values(item_attr, values)
					item_attr.save()
					attributes.append({"attribute": name})
				else:
					attributes.append(
						{
							"attribute": name,
							"from_range": item_attr.get("from_range"),
							"to_range": item_attr.get("to_range"),
							"increment": item_attr.get("increment"),
							"numeric_values": item_attr.get("numeric_values"),
						}
					)
		return attributes

	def _set_new_attribute_values(self, item_attr, values):
		for value in values:
			if not any(
				(d.abbr.lower() == value.lower() or d.attribute_value.lower() == value.lower())
				for d in item_attr.item_attribute_values
			):
				item_attr.append("item_attribute_values", {"attribute_value": value, "abbr": value})

	def _create_item(self, product, warehouse, has_variant=0, attributes=None, variant_of=None):
		item_dict = {
			"variant_of": variant_of,
			"is_stock_item": 1,
			"item_code": cstr(product.get("item_code")) or cstr(product.get("id")),
			"item_name": (product.get("title") or "").strip(),
			"description": product.get("description") or product.get("title"),
			"item_group": self._get_item_group(product.get("type")),
			"has_variants": has_variant,
			"attributes": attributes or [],
			"stock_uom": product.get("uom") or _("Nos"),
			"sku": product.get("sku"),
			"default_warehouse": warehouse,
			"image": product.get("thumbnail"),
			"weight_uom": WEIGHT_UOM,
			"weight_per_unit": product.get("weight"),
		}

		integration_item_code = str(product.get("id"))  # medusa product id
		variant_id = cstr(product.get("variant_id"))  # medusa variant id (single/variant)
		sku = item_dict["sku"]

		if not _match_sku_and_link_item(
			item_dict, integration_item_code, variant_id, variant_of=variant_of, has_variant=has_variant
		):
			ecommerce_item.create_ecommerce_item(
				MODULE_NAME,
				integration_item_code,
				item_dict,
				variant_id=variant_id,
				sku=sku,
				variant_of=variant_of,
				has_variants=has_variant,
			)

	def _create_item_variants(self, product, warehouse, attributes):
		template_item = ecommerce_item.get_erpnext_item(
			MODULE_NAME, integration_item_code=str(product.get("id")), has_variants=1
		)
		if not template_item:
			return

		currency = _setting_currency(self.setting)
		for variant in product.get("variants") or []:
			medusa_item_variant = {
				"id": product.get("id"),
				"variant_id": variant.get("id"),
				"item_code": variant.get("sku") or variant.get("id"),
				"title": (product.get("title") or "").strip() + "-" + (variant.get("title") or ""),
				"type": product.get("type"),
				"sku": variant.get("sku"),
				"uom": template_item.stock_uom or _("Nos"),
				"price": _get_variant_price(variant, currency),
				"weight": variant.get("weight") or product.get("weight"),
				"thumbnail": product.get("thumbnail"),
			}

			# Medusa variant ``options`` is a list of {value} aligned to product options
			variant_values = [o.get("value") for o in variant.get("options") or []]
			for i, value in enumerate(variant_values):
				if i < len(attributes) and value is not None:
					attributes[i].update({"attribute_value": self._get_attribute_value(value, attributes[i])})

			self._create_item(medusa_item_variant, warehouse, 0, attributes, template_item.name)

	def _get_attribute_value(self, variant_attr_val, attribute):
		attribute_value = frappe.db.sql(
			"""select attribute_value from `tabItem Attribute Value`
			where parent = %s and (abbr = %s or attribute_value = %s)""",
			(attribute["attribute"], variant_attr_val, variant_attr_val),
			as_list=1,
		)
		return attribute_value[0][0] if len(attribute_value) > 0 else variant_attr_val

	def _get_item_group(self, product_type=None):
		parent_item_group = get_root_of("Item Group")

		# Medusa product type is an object {id, value}; fall back to the default group
		type_value = product_type.get("value") if isinstance(product_type, dict) else product_type
		if not type_value:
			return parent_item_group

		if frappe.db.get_value("Item Group", type_value, "name"):
			return type_value

		item_group = frappe.get_doc(
			{
				"doctype": "Item Group",
				"item_group_name": type_value,
				"parent_item_group": parent_item_group,
				"is_group": "No",
			}
		).insert()
		return item_group.name


def _has_variants(product) -> bool:
	"""A Medusa product is a template if it has options or >1 variants.

	A product with a single implicit "Default option / Default variant" is a
	single Item.
	"""
	options = product.get("options") or []
	variants = product.get("variants") or []
	if len(variants) > 1:
		return True
	# Medusa v2 products always carry >=1 option; the implicit one (created by the
	# admin dashboard / documented POST /admin/products convention) is titled
	# "Default option" with value "Default option value". Treat a lone default
	# option as a single item. (Doc-verified 2026-07-02.)
	for option in options:
		title = (option.get("title") or "").lower()
		if title not in ("", "default option", "default"):
			return True
	return False


def _get_variant_price(variant, currency: str | None = None) -> float | None:
	"""Pick a variant's base price from Medusa v2 ``variants.prices``.

	GET /admin/products includes prices by default (``*variants.prices`` plus
	``price_rules.attribute/value`` are in the endpoint's default fields);
	``amount`` is a decimal major-currency value. Prices scoped to a context
	(e.g. a region) carry ``price_rules`` rows, plain currency prices carry
	none. Prefer the rule-free price in ``currency``, then any rule-free
	price, then the first. (Doc-verified 2026-07-02.)
	"""
	prices = variant.get("prices") or []
	if not prices:
		return None

	base_prices = [p for p in prices if not (p.get("price_rules") or p.get("rules"))]
	if currency:
		for price in base_prices:
			if (price.get("currency_code") or "").lower() == currency.lower():
				return to_amount(price.get("amount"))

	price = base_prices[0] if base_prices else prices[0]
	return to_amount(price.get("amount"))


def _setting_currency(setting) -> str | None:
	"""Currency used for price selection/upload: the Setting's selling price
	list's currency if configured, else the site's default currency."""
	return (
		setting.selling_price_list
		and frappe.db.get_value("Price List", setting.selling_price_list, "currency")
	) or frappe.defaults.get_global_default("currency")


def _match_sku_and_link_item(item_dict, product_id, variant_id, variant_of=None, has_variant=False) -> bool:
	"""Match a new item with an existing ERPNext item by Medusa SKU == item_code.

	Returns True if matched and linked via an Ecommerce Item (no Item created).
	"""
	sku = item_dict.get("sku")
	# templates carry no SKU of their own; a variant may match an existing Item by SKU
	if not sku or has_variant:
		return False

	item_name = frappe.db.get_value("Item", {"item_code": sku})
	if item_name:
		try:
			frappe.get_doc(
				{
					"doctype": "Ecommerce Item",
					"integration": MODULE_NAME,
					"erpnext_item_code": item_name,
					"integration_item_code": product_id,
					"has_variants": 0,
					"variant_id": cstr(variant_id),
					"variant_of": cstr(variant_of) if variant_of else None,
					"sku": sku,
				}
			).insert()
			return True
		except Exception:
			return False
	return False


def create_items_if_not_exist(order: dict) -> None:
	"""Using a Medusa order, sync all line-item products that aren't yet synced."""
	for line_item in order.get("items", []):
		product_id = line_item.get("product_id")
		variant_id = line_item.get("variant_id")
		sku = line_item.get("variant_sku") or line_item.get("sku")
		if not product_id:
			continue

		product = MedusaProduct(product_id, variant_id=variant_id, sku=sku)
		if not product.is_synced():
			product.sync_product()


def get_item_code(line_item: dict) -> str:
	"""Resolve the ERPNext item_code for a Medusa order line item.

	Looks up the Ecommerce Item by variant_id / sku; if missing, lazily syncs the
	product from Medusa and returns the newly-created item_code.
	"""
	variant_id = line_item.get("variant_id")
	sku = line_item.get("variant_sku") or line_item.get("sku")
	product_id = line_item.get("product_id")

	item_code = ecommerce_item.get_erpnext_item_code(
		integration=MODULE_NAME,
		integration_item_code=cstr(product_id),
		variant_id=cstr(variant_id) if variant_id else None,
	)
	if item_code:
		return item_code

	item = ecommerce_item.get_erpnext_item(
		integration=MODULE_NAME,
		integration_item_code=cstr(product_id),
		variant_id=cstr(variant_id) if variant_id else None,
		sku=sku,
	)
	if item:
		return item.item_code

	# not synced yet -> create from the Medusa product, then resolve again
	if product_id:
		product = MedusaProduct(product_id, variant_id=variant_id, sku=sku)
		if not product.is_synced():
			product.sync_product()

		item = ecommerce_item.get_erpnext_item(
			integration=MODULE_NAME,
			integration_item_code=cstr(product_id),
			variant_id=cstr(variant_id) if variant_id else None,
			sku=sku,
		)
		if item:
			return item.item_code

	frappe.throw(_("Could not resolve ERPNext item for Medusa line item {0}").format(line_item.get("id")))


# -- ERPNext Item -> Medusa (doc hook) -------------------------------------------


def upload_erpnext_item(item, method=None):
	"""``Item`` doc-hook (after_insert / on_update): push ERPNext Items to Medusa.

	New items are created on Medusa, existing ones updated, gated on the
	``Medusa Setting`` toggles. Mirrors ``shopify.product.upload_erpnext_item``.
	"""
	template_item = item  # alias for readability

	if item.flags.from_integration or frappe.flags.in_import:
		return

	setting = frappe.get_doc(SETTING_DOCTYPE)
	if not setting.is_enabled() or not setting.upload_erpnext_items:
		return

	if item.has_variants:
		# templates are pushed implicitly when their variants sync
		return

	if item.variant_of and not setting.upload_variants_as_items:
		msgprint(_("Enable variant sync in Medusa Setting to upload item to Medusa."))
		return

	if item.variant_of:
		template_item = frappe.get_doc("Item", item.variant_of)

	product_id = frappe.db.get_value(
		"Ecommerce Item",
		{"erpnext_item_code": template_item.name, "integration": MODULE_NAME},
		"integration_item_code",
	)
	is_new_product = not bool(product_id)

	if is_new_product:
		_create_medusa_product(item, template_item, setting)
	elif setting.update_medusa_item_on_update:
		_update_medusa_product(item, template_item, setting, product_id)


def _variant_body(item, setting) -> dict:
	"""Build a Medusa variant body from an ERPNext Item.

	Per POST /admin/products (AdminCreateProductVariant): ``title`` and
	``prices`` are required (``prices`` may be an empty array), ``options`` is
	an object mapping option title -> value and must line up with the product's
	options, and ``sku``/``manage_inventory``/``weight`` (number, grams) are
	optional. (Doc-verified 2026-07-02.)
	"""
	body = {
		"title": item.item_name or item.item_code,
		"sku": item.item_code,
		"manage_inventory": bool(item.is_stock_item),
		"options": {DEFAULT_OPTION_TITLE: DEFAULT_OPTION_VALUE},
		"prices": [],
	}
	if item.get("weight_per_unit"):
		body["weight"] = flt(item.weight_per_unit)

	price = item.get(ITEM_SELLING_RATE_FIELD)
	if price is not None:
		currency = _setting_currency(setting)
		# price shape: {amount, currency_code} with amount a decimal
		# major-currency value, e.g. 19.99 == $19.99 (doc-verified 2026-07-02)
		body["prices"] = [{"amount": flt(price), "currency_code": (currency or "usd").lower()}]
	return body


def _product_body(template_item, setting, client) -> dict:
	"""Build a Medusa product body from an ERPNext (template) Item.

	Per POST /admin/products (AdminCreateProduct, strict schema): ``title`` is
	required, ``status`` is one of draft/proposed/published/rejected, ``weight``
	is a number, and the product type is referenced by ``type_id`` — the
	v1-style nested ``type: {value}`` no longer exists, so the Item Group is
	resolved to a Product Type id first. (Doc-verified 2026-07-02.)
	"""
	status = "published" if setting.sync_new_item_as_active else "draft"
	if template_item.get("disabled"):
		status = "draft"
	body = {
		"title": template_item.item_name or template_item.item_code,
		"description": template_item.description,
		"status": status,
	}
	if template_item.get("weight_per_unit"):
		body["weight"] = flt(template_item.weight_per_unit)
	if template_item.get("item_group"):
		type_id = client.get_or_create_product_type(template_item.item_group)
		if type_id:
			body["type_id"] = type_id
	return body


def _create_medusa_product(item, template_item, setting):
	client = MedusaClient()
	body = _product_body(template_item, setting, client)

	# a product created with variants must declare matching options; ERPNext
	# items have none of their own, so use Medusa's documented default option
	# (doc-verified 2026-07-02)
	body["options"] = [{"title": DEFAULT_OPTION_TITLE, "values": [DEFAULT_OPTION_VALUE]}]
	variant = _variant_body(item if not item.has_variants else template_item, setting)
	body["variants"] = [variant]

	try:
		product = client.create_product(body)
	except Exception as e:
		create_medusa_log(
			status="Error",
			exception=e,
			message=f"Failed to upload Item {item.name} to Medusa",
			method="upload_erpnext_item",
		)
		return

	product_id = str(product.get("id"))
	variants = product.get("variants") or []
	variant_id = str(variants[0].get("id")) if variants else ""
	variant_sku = variants[0].get("sku") if variants else item.item_code

	# write the Ecommerce Item link(s)
	link_errors = []
	ecom_items = {item.name: item}
	ecom_items[template_item.name] = template_item
	for d in ecom_items.values():
		try:
			frappe.get_doc(
				{
					"doctype": "Ecommerce Item",
					"erpnext_item_code": d.name,
					"integration": MODULE_NAME,
					"integration_item_code": product_id,
					"variant_id": "" if d.has_variants else variant_id,
					"sku": "" if d.has_variants else cstr(variant_sku),
					"has_variants": d.has_variants,
					"variant_of": d.variant_of,
				}
			).insert()
		except Exception as e:
			link_errors.append(f"{d.name}: {e}")

	if link_errors:
		create_medusa_log(
			status="Error",
			request_data=body,
			message=f"Created Medusa product {product_id} but failed to link ERPNext items: {link_errors}",
			method="upload_erpnext_item",
		)
		return

	create_medusa_log(
		status="Success",
		request_data=body,
		message=f"Created Item: {item.name}, medusa product: {product_id}",
		method="upload_erpnext_item",
	)


def _update_medusa_product(item, template_item, setting, product_id):
	client = MedusaClient()
	body = _product_body(template_item, setting, client)

	try:
		client.update_product(product_id, body)
	except Exception as e:
		create_medusa_log(
			status="Error",
			exception=e,
			message=f"Failed to update Item {item.name} on Medusa",
			method="upload_erpnext_item",
		)
		return

	create_medusa_log(
		status="Success",
		request_data=body,
		message=f"Updated Item: {item.name}, medusa product: {product_id}",
		method="upload_erpnext_item",
	)
