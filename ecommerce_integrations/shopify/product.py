import json
from typing import Optional

import frappe
from frappe import _, msgprint
from frappe.utils import cint, cstr, flt
from frappe.utils.nestedset import get_root_of
from shopify import GraphQL

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item import (
	ecommerce_item,
)
from ecommerce_integrations.shopify.connection import temp_shopify_session
from ecommerce_integrations.shopify.constants import (
	ITEM_SELLING_RATE_FIELD,
	MODULE_NAME,
	SETTING_DOCTYPE,
	SUPPLIER_ID_FIELD,
	WEIGHT_TO_ERPNEXT_UOM_MAP,
)
from ecommerce_integrations.shopify.utils import create_shopify_log


class ShopifyProduct:
	def __init__(
		self,
		product_id: str,
		variant_id: str | None = None,
		sku: str | None = None,
		has_variants: int | None = 0,
	):
		self.product_id = str(product_id)
		self.variant_id = str(variant_id) if variant_id else None
		self.sku = str(sku) if sku else None
		self.has_variants = has_variants
		self.setting = frappe.get_doc(SETTING_DOCTYPE)

		if not self.setting.is_enabled():
			frappe.throw(_("Can not create Shopify product when integration is disabled."))

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

	def sync_product(self):
		if not self.is_synced():
			product_dict = self.fetch_shopify_product(self.product_id)

			self._make_item(product_dict)

	def _make_item(self, product_dict):
		_add_weight_details(product_dict)

		warehouse = self.setting.warehouse

		if _has_variants(product_dict):
			self.has_variants = 1
			attributes = self._create_attribute(product_dict)
			self._create_item(product_dict, warehouse, 1, attributes)
			self._create_item_variants(product_dict, warehouse, attributes)

		else:
			product_dict["variant_id"] = product_dict["variants"][0]["id"]
			self._create_item(product_dict, warehouse)

	def _create_attribute(self, product_dict):
		attribute = []
		for attr in product_dict.get("options"):
			if not frappe.db.get_value("Item Attribute", attr.get("name"), "name"):
				frappe.get_doc(
					{
						"doctype": "Item Attribute",
						"attribute_name": attr.get("name"),
						"item_attribute_values": [
							{"attribute_value": attr_value, "abbr": attr_value}
							for attr_value in attr.get("values")
						],
					}
				).insert()
				attribute.append({"attribute": attr.get("name")})

			else:
				# check for attribute values
				item_attr = frappe.get_doc("Item Attribute", attr.get("name"))
				if not item_attr.numeric_values:
					self._set_new_attribute_values(item_attr, attr.get("values"))
					item_attr.save()
					attribute.append({"attribute": attr.get("name")})

				else:
					attribute.append(
						{
							"attribute": attr.get("name"),
							"from_range": item_attr.get("from_range"),
							"to_range": item_attr.get("to_range"),
							"increment": item_attr.get("increment"),
							"numeric_values": item_attr.get("numeric_values"),
						}
					)
		return attribute

	def _set_new_attribute_values(self, item_attr, values):
		for attr_value in values:
			if not any(
				(d.abbr.lower() == attr_value.lower() or d.attribute_value.lower() == attr_value.lower())
				for d in item_attr.item_attribute_values
			):
				item_attr.append(
					"item_attribute_values",
					{"attribute_value": attr_value, "abbr": attr_value},
				)

	def _create_item(self, product_dict, warehouse, has_variant=0, attributes=None, variant_of=None):
		item_code = cstr(product_dict.get("id"))

		# HSN code handling
		hsn_code = str(product_dict.get("metafield") or "").strip()
		if not hsn_code.isdigit() or len(hsn_code) not in (6, 8):
			hsn_code = "999713"
		price = flt(product_dict.get("price") or 0)
		stock_qty = flt(product_dict.get("stock_qty") or 0)

		if not has_variant and not variant_of:
			# For Single product without varaint
			first_variant = product_dict["variants"][0]
			price = flt(first_variant.get("price") or 0)
			stock_qty = flt(first_variant.get("stock_qty") or 0)

		item_dict = {
			"variant_of": variant_of,
			"is_stock_item": 1,
			"item_code": item_code,
			"item_name": (product_dict.get("title") or "").strip(),
			"description": product_dict.get("body_html") or product_dict.get("title"),
			"item_group": self._get_item_group(product_dict.get("product_type")),
			"has_variants": has_variant,
			"attributes": attributes or [],
			"sku": product_dict.get("sku") or _get_sku(product_dict),
			"default_warehouse": warehouse,
			"image": _get_item_image(product_dict),
			"weight_uom": WEIGHT_TO_ERPNEXT_UOM_MAP.get(product_dict.get("weight_unit")),
			"weight_per_unit": product_dict.get("weight"),
			"default_supplier": self._get_supplier(product_dict),
			"gst_hsn_code": hsn_code,
		}

		is_template = has_variant and not variant_of
		# update stock and price when it is not an template
		if not is_template:
			if stock_qty:
				item_dict["opening_stock"] = stock_qty
				item_dict["valuation_rate"] = price

			if price:
				item_dict["standard_rate"] = price
				item_dict["shopify_selling_rate"] = price

		# Clean and normalize attributes
		cleaned = [
			attr
			for attr in attributes or []
			if attr.get("attribute", "").lower() not in ("default", "default title")
		]
		normalized_attributes = []
		for attr in cleaned:
			if not attr.get("attribute"):
				continue
			if not attr.get("attribute_value") and attr.get("attribute"):
				attr["attribute_value"] = (
					frappe.db.get_value(
						"Item Attribute Value", {"parent": attr["attribute"]}, "attribute_value"
					)
					or ""
				)
			normalized_attributes.append(
				{
					"attribute": attr["attribute"],
					"attribute_value": attr["attribute_value"],
					"doctype": "Item Variant Attribute",
				}
			)

		item_dict["attributes"] = normalized_attributes

		if variant_of and not normalized_attributes:
			item_dict["has_variants"] = 0
			item_dict["variant_of"] = None

		try:
			integration_item_code = product_dict["id"]
			variant_id = product_dict.get("variant_id", "")
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

		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Shopify Item Creation Failed: {item_code}")

	def _create_item_variants(self, product_dict, warehouse, attributes):
		template_item = ecommerce_item.get_erpnext_item(
			MODULE_NAME, integration_item_code=product_dict.get("id"), has_variants=1
		)

		if not template_item:
			return

		template_item_code = template_item.name

		for variant in product_dict.get("variants", []):
			try:
				variant_id = variant.get("id")
				# Varaint is updated it it exsist
				if frappe.db.exists("Item", variant_id):
					frappe.db.set_value(
						"Item",
						variant_id,
						{
							"standard_rate": flt(variant.get("price")),
							"valuation_rate": flt(variant.get("price")),
							"shopify_selling_rate": flt(variant.get("price")),
						},
					)

					if flt(variant.get("stock_qty")):
						frappe.db.set_value(
							"Item", variant_id, "opening_stock", flt(variant.get("stock_qty"))
						)

					continue

				# Build variant attributes
				variant_attributes = []
				variant_index = product_dict["variants"].index(variant)
				for _, option in enumerate(product_dict.get("options", []), start=1):
					option_name = option.get("name")
					if variant_index < len(option.get("values", [])):
						raw_value = option["values"][variant_index]
						if option_name and raw_value:
							attribute_value = self._get_attribute_value(raw_value, {"attribute": option_name})
							variant_attributes.append(
								{"attribute": option_name, "attribute_value": attribute_value}
							)

				shopify_item_variant = {
					"id": variant_id,
					"variant_id": variant_id,
					"variant_of": template_item_code,
					"item_code": variant_id,
					"title": variant.get("title") or product_dict.get("title"),
					"product_type": product_dict.get("product_type"),
					"sku": variant.get("sku"),
					"uom": template_item.stock_uom or _("Nos"),
					"price": flt(variant.get("price") or product_dict.get("item_price") or 0),
					"stock_qty": flt(variant.get("stock_qty") or 0),
					"weight_unit": variant.get("weight_unit"),
					"weight": variant.get("weight"),
					"body_html": product_dict.get("body_html"),
				}

				self._create_item(
					shopify_item_variant,
					warehouse,
					has_variant=0,
					attributes=variant_attributes,
					variant_of=template_item_code,
				)
			except Exception:
				frappe.log_error(
					frappe.get_traceback(), f"Shopify Variant Creation Failed: {variant.get('id')}"
				)

	def _get_attribute_value(self, variant_attr_val, attribute):
		av = frappe.qb.DocType("Item Attribute Value")

		# Check if attribute value exists
		attribute_value = (
			frappe.qb.from_(av)
			.select(av.attribute_value)
			.where(
				(av.parent == attribute["attribute"])
				& ((av.abbr == variant_attr_val) | (av.attribute_value == variant_attr_val))
			)
		).run(as_list=True)

		if attribute_value:
			return str(attribute_value[0][0])
		else:
			# Create missing attribute value in ERPNext
			new_val = frappe.get_doc(
				{
					"doctype": "Item Attribute Value",
					"parent": attribute["attribute"],
					"parenttype": "Item Attribute",
					"parentfield": "attribute_values",
					"attribute_value": str(variant_attr_val),
				}
			).insert(ignore_permissions=True)
			return str(new_val.attribute_value)

	def _get_item_group(self, product_type=None):
		parent_item_group = get_root_of("Item Group")

		if not product_type:
			return parent_item_group

		if frappe.db.get_value("Item Group", product_type, "name"):
			return product_type
		item_group = frappe.get_doc(
			{
				"doctype": "Item Group",
				"item_group_name": product_type,
				"parent_item_group": parent_item_group,
				"is_group": "No",
			}
		).insert()
		return item_group.name

	def _get_supplier(self, product_dict):
		if not product_dict.get("vendor"):
			return ""

		vendor_name = product_dict.get("vendor")
		vendor_id = vendor_name.lower()
		exs = frappe.qb.DocType("Supplier")
		existing_supplier = (
			frappe.qb.from_(exs)
			.select(exs.name)
			.where((exs.name == vendor_name) | (exs[SUPPLIER_ID_FIELD] == vendor_id))
		).run(as_dict=True)

		if existing_supplier:
			return existing_supplier[0]["name"]

		supplier = frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": vendor_name,
				SUPPLIER_ID_FIELD: vendor_id,
				"supplier_group": self._get_supplier_group(),
			}
		).insert(ignore_permissions=True)

		return supplier.name

	def _get_supplier_group(self):
		group_name = "Shopify Supplier"

		supplier_group = frappe.db.get_value("Supplier Group", {"name": group_name}, "name")

		if not supplier_group:
			supplier_group = (
				frappe.get_doc({"doctype": "Supplier Group", "supplier_group_name": group_name})
				.insert(ignore_permissions=True)
				.name
			)

		return supplier_group

	@temp_shopify_session
	def fetch_shopify_product(self, product_id: str) -> dict:
		"""Fetch shopify product using GraphQL API to get all details including variants and options."""
		if "/" in product_id:
			product_id = product_id.split("/")[-1]
		if not product_id.startswith("gid://"):
			product_id = f"gid://shopify/Product/{product_id}"
		query = """
				query ($id: ID!) {
				product(id: $id) {
					id
					title
					descriptionHtml
					productType
					vendor
					featuredMedia {
					... on MediaImage {
						id
						image {
						url
						}
					}
					}
					options(first: 10) {
					name
					values
					}
					metafield(namespace: "custom", key: "hsn_code") {
					value
					}
					variants(first: 50) {
					edges {
						node {
						id
						title
						sku
						price
						inventoryQuantity
						inventoryItem {
							id
							tracked
							measurement {
							weight {
								value
								unit
							}
							}
							inventoryLevels(first: 10) {
							edges {
								node {
								id
								quantities(names: ["available"]) {
									name
									quantity
								}
								location {
									id
									name
									legacyResourceId
								}
								}
							}
							}
						}
						}
					}
					}
				}
				}

					"""
		variables = {"id": product_id}
		response = GraphQL().execute(query, variables)

		if isinstance(response, str):
			data = json.loads(response)
		else:
			data = response

		if "errors" in data:
			frappe.log_error(
				json.dumps(data["errors"], indent=2),
				"Shopify GraphQL Product Fetch Error",
			)

		product = data.get("data", {}).get("product", {})

		if not product:
			frappe.throw(_("No product data found in Shopify GraphQL response , Product may be deleted"))

		normalized = {
			"id": product.get("id").split("/")[-1],
			"title": product.get("title"),
			"body_html": product.get("descriptionHtml"),
			"product_type": product.get("productType"),
			"vendor": product.get("vendor"),
			"image": {
				"src": (
					product.get("featuredMedia", {}).get("image", {}).get("url")
					if product.get("featuredMedia")
					else None
				)
			},
			"options": [
				{"name": opt.get("name"), "values": opt.get("values", [])}
				for opt in product.get("options", [])
			],
			"metafield": (product.get("metafield", {}).get("value") if product.get("metafield") else None),
			"variants": [],
		}
		for edge in product.get("variants", {}).get("edges", []):
			node = edge.get("node", {})
			total_stock = 0
			inventory_levels = node.get("inventoryItem", {}).get("inventoryLevels", {}).get("edges", [])

			for level in inventory_levels:
				quantities = level.get("node", {}).get("quantities", [])
				for qty in quantities:
					if qty.get("name") == "available":
						total_stock += qty.get("quantity", 0)
			normalized["variants"].append(
				{
					"id": node.get("id").split("/")[-1],
					"title": node.get("title"),
					"sku": node.get("sku"),
					"price": node.get("price"),
					"weight": node.get("inventoryItem", {})
					.get("measurement", {})
					.get("weight", {})
					.get("value"),
					"weight_unit": node.get("inventoryItem", {})
					.get("measurement", {})
					.get("weight", {})
					.get("unit"),
					"stock_qty": total_stock,
				}
			)

		return normalized


def _add_weight_details(product_dict):
	variants = product_dict.get("variants")
	if variants:
		product_dict["weight"] = variants[0]["weight"]
		product_dict["weight_unit"] = variants[0]["weight_unit"]


def _has_variants(product_dict) -> bool:
	options = product_dict.get("options")
	return bool(options and "Default Title" not in options[0]["values"])


def _get_sku(product_dict):
	if product_dict.get("variants"):
		return product_dict.get("variants")[0].get("sku")
	return ""


def _get_item_image(product_dict):
	if product_dict.get("image"):
		return product_dict.get("image").get("src")
	return None


def _match_sku_and_link_item(item_dict, product_id, variant_id, variant_of=None, has_variant=False) -> bool:
	"""Tries to match new item with existing item using Shopify SKU == item_code.

	Returns true if matched and linked.
	"""
	sku = item_dict.get("sku")
	if not sku or variant_of or has_variant:
		return False

	item_name = frappe.db.get_value("Item", {"item_code": sku})
	if item_name:
		try:
			ecommerce_item = frappe.get_doc(
				{
					"doctype": "Ecommerce Item",
					"integration": MODULE_NAME,
					"erpnext_item_code": item_name,
					"integration_item_code": product_id,
					"has_variants": 0,
					"variant_id": cstr(variant_id),
					"sku": sku,
				}
			)
			ecommerce_item.insert()
			return True
		except Exception:
			return False


def create_items_if_not_exist(order):
	"""Using shopify order, sync all items that are not already synced."""
	for item in order.get("line_items", []):
		product_id = item["product_id"]
		variant_id = item.get("variant_id")
		sku = item.get("sku")
		product = ShopifyProduct(product_id, variant_id=variant_id, sku=sku)

		if not product.is_synced():
			product.sync_product()


def get_item_code(shopify_item):
	"""Get item code using shopify_item dict.

	Item should contain both product_id and variant_id."""

	item = ecommerce_item.get_erpnext_item(
		integration=MODULE_NAME,
		integration_item_code=shopify_item.get("product_id"),
		variant_id=shopify_item.get("variant_id"),
		sku=shopify_item.get("sku"),
	)
	if item:
		return item.item_code


def delete_from_shopify(product_id: str | None = None, variant_id: str | None = None) -> dict | None:
	"""
	Delete a Shopify product using GraphQL.
	Note:This doesn't support deleting individual variants, only full products.
	"""
	if not product_id and not variant_id:
		return None

	def ensure_gid(value: str) -> str:
		if not value:
			return None
		if value.startswith("gid://"):
			return value
		return f"gid://shopify/Product/{value}"

	gid = ensure_gid(product_id or variant_id)

	mutation = """
	mutation productDelete($input: ProductDeleteInput!) {
		productDelete(input: $input) {
			deletedProductId
			userErrors {
				field
				message
			}
		}
	}
	"""

	variables = {"input": {"id": gid}}

	try:
		raw = GraphQL().execute(mutation, variables)
	except Exception:
		frappe.log_error(
			f"Shopify GraphQL execution failed:\n{frappe.get_traceback()}",
			"Shopify GraphQL Error",
		)
		raise

	if isinstance(raw, str):
		try:
			data = json.loads(raw)
		except Exception:
			frappe.log_error(f"Invalid JSON response from Shopify: {raw}", "Shopify GraphQL Error")
			raise
	else:
		data = raw

	if "errors" in data:
		frappe.log_error(json.dumps(data["errors"], indent=2), "Shopify GraphQL Errors")
		raise Exception(f"Shopify GraphQL errors: {data['errors']}")

	result = data.get("data", {}).get("productDelete")
	if not result:
		frappe.log_error(json.dumps(data, indent=2), "Shopify Delete Unexpected Response")
		raise Exception("Unexpected response from Shopify during delete.")

	user_errors = result.get("userErrors") or []
	if user_errors:
		msgs = ", ".join([ue.get("message", str(ue)) for ue in user_errors])
		frappe.log_error(json.dumps(user_errors, indent=2), "Shopify Delete User Errors")
		raise Exception(f"Shopify Delete Error: {msgs}")

	frappe.logger().info(f"Shopify Delete Success: {json.dumps(result, indent=2)}")
	return result


@temp_shopify_session
def shopify_graphql_product_mutation(action: str, product_data: dict) -> dict:
	"""
	Create or update Shopify product using new GraphQL `productSet` mutation.
	Handles both single and multi-variant products.
	"""

	import json

	from shopify import GraphQL

	# --- Key fixes for productSet mutation ---
	# 1. For single variant products (default variant), use productOptions with "Title"
	# 2. For variant optionValues, use "optionName" and "name" (not "value")
	# 3. Don't include "options" field in productSet input - use "productOptions" instead

	if product_data.get("variants"):
		# For single variant products with default "Title" option
		if len(product_data["variants"]) == 1:
			variant = product_data["variants"][0]

			# Set up productOptions for single variant
			if "productOptions" not in product_data:
				product_data["productOptions"] = [
					{
						"name": "Title",
						"position": 1,
						"values": [{"name": "Default Title"}],
					}
				]

			if "optionValues" not in variant or not variant["optionValues"]:
				variant["optionValues"] = [{"optionName": "Title", "name": "Default Title"}]
			else:
				for opt_val in variant["optionValues"]:
					if "value" in opt_val:
						opt_val["name"] = opt_val.pop("value")
					if "name" in opt_val and "optionName" not in opt_val:
						opt_val["optionName"] = opt_val.pop("name")
						opt_val["name"] = opt_val.get("name", "Default Title")

	if "options" in product_data:
		del product_data["options"]

	# --- productSet mutation for both create and update ---
	mutation = """
		mutation ProductSet($productSet: ProductSetInput!, $synchronous: Boolean!) {
			productSet(synchronous: $synchronous, input: $productSet) {
				product {
					id
					title
					descriptionHtml
					productType
					status
					vendor
					metafields(first: 10, namespace: "custom") {
						edges {
							node {
								key
								value
							}
						}
					}
					variants(first: 50) {
						nodes {
							id
							title
							sku
							price
							inventoryItem {
								id
								tracked
								inventoryLevels(first: 5) {
									nodes {
										quantities(names: ["available"]) {
											name
											quantity
										}
										location {
											id
											name
										}
									}
								}
							}
						}
					}
				}
				userErrors {
					field
					message
					code
				}
			}
		}
	"""

	# --- Variables ---
	variables = {"synchronous": True, "productSet": product_data}

	# --- Execute GraphQL mutation ---
	response = GraphQL().execute(mutation, variables)

	if isinstance(response, str):
		data = json.loads(response)
	else:
		data = response

	# --- Handle top-level GraphQL errors ---
	if "errors" in data:
		frappe.log_error(
			json.dumps(data["errors"], indent=2),
			f"Shopify GraphQL Product {action.title()} Error",
		)

	result = data.get("data", {}).get("productSet", {})
	user_errors = result.get("userErrors")

	if user_errors:
		frappe.log_error(json.dumps(data, indent=2), f"Shopify GraphQL {action.title()} Raw Response")

	product = result.get("product", {})
	if not product:
		frappe.throw(_(f"No product returned from Shopify after {action}"))

	normalized = {
		"id": product.get("id").split("/")[-1] if product.get("id") else None,
		"title": product.get("title"),
		"body_html": product.get("descriptionHtml"),
		"product_type": product.get("productType"),
		"status": product.get("status"),
		"vendor": product.get("vendor"),
		"metafields": {},
		"variants": [],
	}

	for edge in product.get("metafields", {}).get("edges", []):
		node = edge.get("node", {})
		normalized["metafields"][node.get("key")] = node.get("value")

	for node in product.get("variants", {}).get("nodes", []):
		inventory = node.get("inventoryItem", {})
		tracked = inventory.get("tracked")
		inventory_levels = []

		for inv_node in inventory.get("inventoryLevels", {}).get("nodes", []):
			inventory_levels.append(
				{
					"location_id": inv_node.get("location", {}).get("id"),
					"location_name": inv_node.get("location", {}).get("name"),
					"available": next(
						(
							q.get("quantity")
							for q in inv_node.get("quantities", [])
							if q.get("name") == "available"
						),
						None,
					),
				}
			)

		normalized["variants"].append(
			{
				"id": node.get("id").split("/")[-1] if node.get("id") else None,
				"title": node.get("title"),
				"sku": node.get("sku"),
				"price": node.get("price"),
				"inventory_item_id": inventory.get("id"),
				"tracked": tracked,
				"inventory_levels": inventory_levels,
			}
		)
	return normalized


def upload_erpnext_item(doc, method=None):
	template_item = item = doc

	if item.flags.from_integration:
		return

	setting = frappe.get_doc(SETTING_DOCTYPE)

	if not setting.is_enabled() or not setting.upload_erpnext_items:
		return

	if frappe.flags.in_import:
		return

	# In GraphQL flow, allow templates (has_variants=1) so variants can be generated
	if len(item.attributes) > 3:
		frappe.msgprint(_("Template items/Items with 4 or more attributes can not be uploaded to Shopify."))
		return

	if item.variant_of and not setting.upload_variants_as_items:
		frappe.msgprint(_("Enable variant sync in setting to upload item to Shopify."))
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
		product_data = map_erpnext_item_to_shopify(erpnext_item=template_item)
		product = shopify_graphql_product_mutation("create", product_data)

		is_successful = bool(product)

		if is_successful:
			update_default_variant_properties(
				product,
				sku=template_item.item_code,
				price=template_item.get(ITEM_SELLING_RATE_FIELD),
				is_stock_item=template_item.is_stock_item,
			)

			# Create Ecommerce Item records
			ecom_items = list(set([item, template_item]))

			for d in ecom_items:
				first_variant = (product.get("variants") or [{}])[0]

				variant_id = "" if d.has_variants else cstr(first_variant.get("id") or "")
				sku = "" if d.has_variants else cstr(first_variant.get("sku") or "")

				ecom_item = frappe.get_doc(
					{
						"doctype": "Ecommerce Item",
						"erpnext_item_code": d.name,
						"integration": MODULE_NAME,
						"integration_item_code": cstr(product.get("id")),
						"variant_id": variant_id,
						"sku": sku,
						"has_variants": d.has_variants,
						"variant_of": d.variant_of,
					}
				)
				ecom_item.insert()

		write_upload_log(status=is_successful, product=product, item=item)

	elif setting.update_shopify_item_on_update:
		product_data = map_erpnext_item_to_shopify(erpnext_item=template_item)
		product_data["id"] = f"gid://shopify/Product/{product_id}"

		product = shopify_graphql_product_mutation("update", product_data)
		is_successful = bool(product)

		if is_successful:
			# Push back returned Shopify data → ERPNext
			map_erpnext_item_to_shopify(shopify_product=product, erpnext_item=template_item)

			# If THIS ITEM is NOT a variant → update default variant
			if not item.variant_of:
				update_default_variant_properties(
					product,
					is_stock_item=template_item.is_stock_item,
					price=item.get(ITEM_SELLING_RATE_FIELD),
				)

			# If THIS ITEM IS A VARIANT → update variant-level attributes
			else:
				variant_attributes = {
					"sku": item.item_code,
					"price": item.get(ITEM_SELLING_RATE_FIELD),
				}

				# Build Shopify options (max 3)
				product["options"] = []
				max_index_range = min(3, len(template_item.attributes))

				for i in range(0, max_index_range):
					attr = template_item.attributes[i]
					product["options"].append(
						{
							"name": attr.attribute,
							"values": frappe.db.get_all(
								"Item Attribute Value",
								{"parent": attr.attribute},
								pluck="attribute_value",
							),
						}
					)

					try:
						variant_attributes[f"option{i+1}"] = item.attributes[i].attribute_value
					except IndexError:
						frappe.throw(
							_("Shopify Error: Missing value for attribute {}").format(attr.attribute)
						)

				map_erpnext_variant_to_shopify_variant(product, item, variant_attributes)

		write_upload_log(status=is_successful, product=product, item=item, action="Updated")


@temp_shopify_session
def map_erpnext_variant_to_shopify_variant(shopify_product, erpnext_item, variant_attributes):
	"""Maps variant and updates price + stock in Shopify."""

	graphql = GraphQL()

	stock_qty = cint(erpnext_item.opening_stock)

	default_warehouse = frappe.db.get_single_value("Shopify Setting", "warehouse")
	location_id = get_shopify_location_id(default_warehouse)

	shopify_variants = getattr(shopify_product, "variants", None) or shopify_product.get("variants", [])

	if not shopify_variants:
		frappe.msgprint(_("No variants found in Shopify product"))
		return None

	target_sku = variant_attributes.get("sku")
	target_variant_id = None
	inventory_item_id = None

	for v in shopify_variants:
		sku = v.get("sku") if isinstance(v, dict) else v.sku
		if sku == target_sku:
			target_variant_id = v.get("id") if isinstance(v, dict) else v.id
			inventory_item_id = v.get("inventory_item_id") if isinstance(v, dict) else v.inventory_item_id

	if not target_variant_id:
		frappe.log_error(
			message=f"Could not find variant in Shopify for SKU: {target_sku}",
			title="Shopify Variant Not Found",
		)
		return None

	price_mutation = """
		mutation updateVariantPrice($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
			productVariantsBulkUpdate(productId: $productId, variants: $variants){
				productVariants {
					id
					price
				}
				userErrors {
					field
					message
				}
			}
		}
		"""
	price_variables = {
		"productId": f"gid://shopify/Product/{shopify_product.get('id')}",
		"variants": [
			{
				"id": f"gid://shopify/ProductVariant/{target_variant_id}",
				"price": flt(variant_attributes.get("price")),
			}
		],
	}

	price_response = graphql.execute(price_mutation, price_variables)
	if isinstance(price_response, str):
		price_response = json.loads(price_response)

	if "errors" in price_response:
		frappe.log_error(json.dumps(price_response["errors"], indent=2), "Shopify Price Update Error")

	if inventory_item_id:
		stock_mutation = """
		mutation inventorySetQuantities($input: InventorySetQuantitiesInput!) {
			inventorySetQuantities(input: $input) {
				inventoryAdjustmentGroup {
					createdAt
					reason
					referenceDocumentUri
					changes {
						name
						delta
						quantityAfterChange
					}
				}
				userErrors {
					code
					field
					message
				}
			}
		}
	"""

		stock_variables = {
			"input": {
				"ignoreCompareQuantity": True,
				"name": "available",
				"reason": "correction",
				"quantities": [
					{
						"inventoryItemId": inventory_item_id,
						"locationId": f"gid://shopify/Location/{location_id}",
						"quantity": stock_qty,
					}
				],
			}
		}

		stock_response = graphql.execute(stock_mutation, stock_variables)

		if isinstance(stock_response, str):
			stock_response = json.loads(stock_response)
			stock_response = graphql.execute(stock_mutation, stock_variables)

			if isinstance(stock_response, str):
				stock_response = json.loads(stock_response)

			if "errors" in stock_response:
				frappe.log_error(json.dumps(stock_response["errors"], indent=2), "Shopify Stock Update Error")

	existing = frappe.db.get_value(
		"Ecommerce Item",
		{
			"erpnext_item_code": erpnext_item.name,
			"integration": MODULE_NAME,
			"sku": target_sku,
		},
		"name",
	)

	if existing:
		# Update existing mapping
		frappe.db.set_value(
			"Ecommerce Item",
			existing,
			{
				"variant_id": target_variant_id,
				"integration_item_code": shopify_product.get("id"),
			},
		)
	else:
		# Insert new mapping
		frappe.get_doc(
			{
				"doctype": "Ecommerce Item",
				"erpnext_item_code": erpnext_item.name,
				"integration": MODULE_NAME,
				"integration_item_code": shopify_product.get("id"),
				"variant_id": target_variant_id,
				"sku": target_sku,
				"variant_of": erpnext_item.variant_of,
			}
		).insert(ignore_permissions=True)

	return target_variant_id


def map_erpnext_item_to_shopify(erpnext_item, shopify_product=None):
	"""Map ERPNext Item fields to Shopify GraphQL `productSet` mutation input structure."""

	# ---- Base Product Info ----
	product_data = {
		"title": erpnext_item.item_name,
		"descriptionHtml": f"<p>{erpnext_item.description or erpnext_item.item_name}</p>",
		"productType": erpnext_item.item_group or "All Item Groups",
		"vendor": erpnext_item.brand or "Default Vendor",
		"status": "DRAFT" if erpnext_item.disabled else "ACTIVE",
		"metafields": [
			{
				"namespace": "custom",
				"key": "hsn_code",
				"value": str(erpnext_item.gst_hsn_code or ""),
				"type": "number_integer",
			}
		],
	}

	# ---- Detect Variant Attributes ----
	attributes = getattr(erpnext_item, "attributes", [])
	has_variants = bool(erpnext_item.has_variants)

	product_options = []
	attribute_values_map = {}

	if has_variants:
		for idx, attr in enumerate(attributes, start=1):
			# Ensure attribute behaves like an object
			attribute_name = getattr(attr, "attribute", None) or attr.get("attribute")
			attribute_value = getattr(attr, "attribute_value", None) or attr.get("attribute_value")

			# Use the value from the attribute itself if present, fallback to DB
			if attribute_value:
				values = [attribute_value]
			else:
				values = frappe.db.get_all(
					"Item Attribute Value",
					filters={"parent": attribute_name},
					pluck="attribute_value",
				)

			if values:
				product_options.append(
					{
						"name": attribute_name,
						"position": idx,
						"values": [{"name": v} for v in values],
					}
				)
				attribute_values_map[attribute_name] = values
	else:
		product_options = [
			{
				"name": "Title",
				"position": 1,
				"values": [{"name": "Default Title"}],
			}
		]

	product_data["productOptions"] = product_options
	# ---- Generate Variants ----
	variants = []

	if has_variants and attribute_values_map:
		import itertools

		attribute_names = list(attribute_values_map.keys())
		attribute_value_lists = [attribute_values_map[name] for name in attribute_names]

		for combination in itertools.product(*attribute_value_lists):
			variant = {
				"sku": f"{erpnext_item.item_code}-{'-'.join(combination)}",
				"price": str(erpnext_item.get(ITEM_SELLING_RATE_FIELD) or "0"),
				"optionValues": [],
			}

			for attr_name, attr_value in zip(attribute_names, combination, strict=False):
				variant["optionValues"].append(
					{
						"optionName": attr_name,
						"name": attr_value,
					}
				)

			if erpnext_item.weight_per_unit:
				uom = get_shopify_weight_uom(erpnext_item.weight_uom)
				variant["weight"] = erpnext_item.weight_per_unit
				variant["weightUnit"] = uom

			default_warehouse = frappe.db.get_single_value("Shopify Setting", "warehouse")
			shopify_location_id = get_shopify_location_id(default_warehouse)
			if shopify_location_id and cint(erpnext_item.opening_stock or 0) > 0:
				variant["inventoryQuantities"] = [
					{
						"locationId": f"gid://shopify/Location/{shopify_location_id}",
						"name": "available",
						"quantity": cint(erpnext_item.opening_stock or 0),
					}
				]

			variants.append(variant)
	else:
		base_variant = {
			"sku": erpnext_item.item_code,
			"price": str(erpnext_item.get(ITEM_SELLING_RATE_FIELD) or "0"),
			"optionValues": [
				{
					"optionName": "Title",
					"name": "Default Title",
				}
			],
		}

		if erpnext_item.weight_per_unit:
			uom = get_shopify_weight_uom(erpnext_item.weight_uom)
			base_variant["weight"] = erpnext_item.weight_per_unit
			base_variant["weightUnit"] = uom

		default_warehouse = frappe.db.get_single_value("Shopify Setting", "warehouse")
		shopify_location_id = get_shopify_location_id(default_warehouse)
		if shopify_location_id and cint(erpnext_item.opening_stock or 0) > 0:
			base_variant["inventoryQuantities"] = [
				{
					"locationId": f"gid://shopify/Location/{shopify_location_id}",
					"name": "available",
					"quantity": cint(erpnext_item.opening_stock or 0),
				}
			]

		variants.append(base_variant)

	product_data["variants"] = variants

	if shopify_product:
		product_data["id"] = (
			shopify_product.get("id")
			if isinstance(shopify_product, dict)
			else getattr(shopify_product, "id", None)
		)

	return product_data


def get_shopify_location_id(erpnext_warehouse: str | None = None) -> str | None:
	"""
	Fetch the Shopify Location ID from the Shopify Setting doctype.
	If `erpnext_warehouse` is provided, map it using the child table.
	Otherwise, return the first available location.
	"""
	try:
		shopify_setting = frappe.get_single("Shopify Setting")
		mappings = shopify_setting.get("shopify_warehouse_mapping") or []

		if erpnext_warehouse:
			for mapping in mappings:
				if mapping.erpnext_warehouse == erpnext_warehouse:
					return mapping.shopify_location_id

		# Fallback to first location if available
		if mappings:
			return mappings[0].shopify_location_id

		frappe.log_error(
			"No Shopify Location ID found in Shopify Setting",
			"get_shopify_location_id",
		)
		return None

	except Exception:
		frappe.log_error(f"Error in get_shopify_location_id: {frappe.get_traceback()} ")
		return None


def get_shopify_weight_uom(erpnext_weight_uom: str) -> str:
	"""Return Shopify weight unit name (e.g. 'KILOGRAMS') for a given ERPNext UOM.
	Handles both map shapes (SHOPIFY->ERPNext or ERPNext->SHOPIFY), is case-insensitive,
	and falls back to sensible defaults/synonyms.
	"""
	if not erpnext_weight_uom:
		return "GRAMS"

	key = cstr(erpnext_weight_uom).strip().lower()

	# If constants are SHOPIFY -> ERPNext, build reverse map: erpnext_lower -> shopify_name
	reverse_map = {
		erpnext_uom.lower(): shopify_uom for shopify_uom, erpnext_uom in WEIGHT_TO_ERPNEXT_UOM_MAP.items()
	}
	if key in reverse_map:
		return reverse_map[key]

	# If constants are ERPNext -> SHOPIFY, check direct mapping
	direct_map = {k.lower(): v for k, v in WEIGHT_TO_ERPNEXT_UOM_MAP.items()}
	if key in direct_map:
		return direct_map[key]

	# fallbacks (erpnext uom -> shopify name)
	synonyms = {
		"kg": "KILOGRAMS",
		"kilogram": "KILOGRAMS",
		"kilograms": "KILOGRAMS",
		"g": "GRAMS",
		"gram": "GRAMS",
		"grams": "GRAMS",
		"oz": "OUNCES",
		"ounce": "OUNCES",
		"ounces": "OUNCES",
		"lb": "POUNDS",
		"lbs": "POUNDS",
		"pound": "POUNDS",
		"pounds": "POUNDS",
	}

	return synonyms.get(key, "GRAMS")


def update_default_variant_properties(
	shopify_product: dict,
	is_stock_item: bool,
	sku: str | None = None,
	price: float | None = None,
):
	"""Update default variant properties for Shopify products (GraphQL + REST compatible).

	Handles both:
	 - REST API format: product["variants"] -> [ { ...variant... } ]
	 - GraphQL format: product["variants"]["edges"] -> [ { "node": {...variant...} } ]
	"""

	# Determine variant source
	variants = shopify_product.get("variants")
	default_variant = {}

	# ---- GraphQL Format ----
	if isinstance(variants, dict) and "edges" in variants:
		edges = variants.get("edges", [])
		if edges and isinstance(edges[0], dict):
			default_variant = edges[0].get("node", {}) or {}
	if not default_variant:
		frappe.log_error(
			f"No default variant found for Shopify product: {shopify_product}",
			"update_default_variant_properties",
		)
		return

	# ---- Apply Updates ----
	if sku:
		default_variant["sku"] = sku

	if price is not None:
		# In GraphQL structure, this is often nested under node or variant input
		default_variant["price"] = float(price)

	if is_stock_item:
		# REST uses "inventory_management" = "shopify"
		# GraphQL uses "tracked" = true
		if "inventory_management" in default_variant:
			default_variant["inventory_management"] = "shopify"
		else:
			default_variant["tracked"] = True
	else:
		# If not stock item, untrack in GraphQL context
		default_variant["tracked"] = False

	return default_variant


def create_item(payload, request_id=None):
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id
	if not payload:
		data = frappe.request.get_json()

	product_id = payload.get("id")
	if not product_id:
		frappe.log_error("Shopify Product Webhook: Missing product ID", str(data))
		return

	try:
		sp = ShopifyProduct(product_id=product_id)
		sp.sync_product()
	except Exception:
		frappe.log_error(f"Shopify Product Sync Failed ({product_id})", frappe.get_traceback())


def write_upload_log(status: bool, product, item, action="Created") -> None:
	"""Log upload results for Shopify product sync (JSON-safe)."""

	# --- STEP 1: Extract raw product data safely ---
	if hasattr(product, "to_dict"):
		raw = product.to_dict()
	elif isinstance(product, dict):
		raw = product
	else:
		raw = {"raw": str(product)}

	def safe_default(o):
		return str(o)

	try:
		product_json = json.dumps(raw, default=safe_default)
		product_data = json.loads(product_json)
	except Exception:
		# absolute last fallback
		product_data = {"raw": str(raw)}

	# --- STEP 3: Handle the ERROR case ---
	if not status:
		error_messages = ""

		# Shopify REST API errors
		if hasattr(product, "errors"):
			try:
				error_messages = ", ".join(product.errors.full_messages())
			except Exception:
				error_messages = str(product.errors)

		# GraphQL / dict errors
		elif isinstance(product, dict) and "errors" in product:
			try:
				error_messages = json.dumps(product["errors"], indent=2)
			except Exception:
				error_messages = str(product["errors"])

		msg = _("Failed to upload item to Shopify") + "<br>"
		if error_messages:
			msg += _("Shopify reported errors:") + " " + error_messages

		frappe.msgprint(msg, title="Note", indicator="orange")

		create_shopify_log(
			status="Error",
			request_data=product_data,
			message=msg,
			method="upload_erpnext_item",
		)
		return

	# --- STEP 4: Handle the SUCCESS case ---
	product_id = None

	# Shopify REST (object)
	if hasattr(product, "id"):
		product_id = getattr(product, "id", None)

	# Shopify GraphQL (dict)
	if not product_id:
		product_id = product_data.get("id") or "Unknown"

	create_shopify_log(
		status="Success",
		request_data=product_data,
		message=f"{action} Item: {item.name}, shopify product: {product_id}",
		method="upload_erpnext_item",
	)
