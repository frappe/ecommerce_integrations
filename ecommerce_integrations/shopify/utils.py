# Copyright (c) 2021, Frappe and contributors
# For license information, please see LICENSE

import frappe
from frappe import _, _dict

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_integration_log.ecommerce_integration_log import (
	create_log,
)
from ecommerce_integrations.shopify.constants import (
	MODULE_NAME,
	OLD_SETTINGS_DOCTYPE,
	SETTING_DOCTYPE,
	ACCOUNT_DOCTYPE,
)
from frappe.model.document import Document


def resolve_account_context(account=None):
	"""Standardized account resolution with legacy fallback.
	
	This function serves as a unified resolver for Shopify account contexts,
	handling both the new multi-tenant Shopify Account system and the legacy
	Shopify Setting singleton pattern.
	
	Args:
		account (None | str | Document, optional): Account context to resolve.
			- None: Returns legacy Shopify Setting document for backward compatibility
			- str: Account name to fetch the corresponding Shopify Account document
			- Document: Assumes it's already a loaded Frappe document (Shopify Account 
			  or Shopify Setting) and returns it directly without validation
			  
	Returns:
		Document: Either a Shopify Account document (new multi-tenant) or 
				 Shopify Setting document (legacy singleton)
				 
	Raises:
		frappe.DoesNotExistError: If the specified account name doesn't exist
		
	Note:
		The function assumes that any non-string, non-None parameter is a valid
		Frappe document object. This assumption is based on the controlled usage
		patterns within the Shopify integration system where only document objects
		or account names are passed. No type validation is performed on document
		objects for performance reasons.
		
	Examples:
		>>> # Legacy fallback
		>>> setting = resolve_account_context(None)
		>>> 
		>>> # Fetch by account name
		>>> account = resolve_account_context("My Shopify Store")
		>>> 
		>>> # Pass existing document
		>>> existing_doc = frappe.get_doc("Shopify Account", "My Store")
		>>> same_doc = resolve_account_context(existing_doc)
		>>> assert existing_doc is same_doc  # Returns same object
	"""
	
	if account:
		if isinstance(account, str):
			return frappe.get_doc(ACCOUNT_DOCTYPE, account)
		elif isinstance(account, Document):  # Check if it's a Frappe document
			return account
		else:
			frappe.throw(f"Invalid account parameter type: {type(account)}")
	else:
		# Legacy fallback for backward compatibility
		return frappe.get_doc(SETTING_DOCTYPE)

def create_shopify_log(account=None, **kwargs):
	"""Enhanced logging with account context support."""
	reference_document = None
	
	if account:
		account_doc = resolve_account_context(account)
		if hasattr(account_doc, 'name') and account_doc.doctype == "Shopify Account":
			reference_document = account_doc.name
	
	return create_log(
		module_def=MODULE_NAME, 
		reference_document=reference_document,
		**kwargs
	)


def migrate_from_old_connector(payload=None, request_id=None):
	"""This function is called to migrate data from old connector to new connector."""

	if request_id:
		log = frappe.get_doc("Ecommerce Integration Log", request_id)
	else:
		log = create_shopify_log(
			status="Queued",
			method="ecommerce_integrations.shopify.utils.migrate_from_old_connector",
		)

	frappe.enqueue(
		method=_migrate_items_to_ecommerce_item,
		queue="long",
		is_async=True,
		log=log,
	)


def ensure_old_connector_is_disabled():
	try:
		old_setting = frappe.get_doc(OLD_SETTINGS_DOCTYPE)
	except Exception:
		frappe.clear_last_message()
		return

	if old_setting.enable_shopify:
		link = frappe.utils.get_link_to_form(OLD_SETTINGS_DOCTYPE, OLD_SETTINGS_DOCTYPE)
		msg = _("Please disable old Shopify integration from {0} to proceed.").format(link)
		frappe.throw(msg)


def _migrate_items_to_ecommerce_item(log):
	shopify_fields = ["shopify_product_id", "shopify_variant_id"]

	for field in shopify_fields:
		if not frappe.db.exists({"doctype": "Custom Field", "fieldname": field}):
			return

	items = _get_items_to_migrate()

	try:
		_create_ecommerce_items(items)
	except Exception:
		log.status = "Error"
		log.traceback = frappe.get_traceback()
		log.save()
		return

	frappe.db.set_value(SETTING_DOCTYPE, SETTING_DOCTYPE, "is_old_data_migrated", 1)
	log.status = "Success"
	log.save()


def _get_items_to_migrate() -> list[_dict]:
	"""get all list of items that have shopify fields but do not have associated ecommerce item."""

	old_data = frappe.db.sql(
		"""SELECT item.name as erpnext_item_code, shopify_product_id, shopify_variant_id, item.variant_of, item.has_variants
			FROM tabItem item
			LEFT JOIN `tabEcommerce Item` ei on ei.erpnext_item_code = item.name
			WHERE ei.erpnext_item_code IS NULL AND shopify_product_id IS NOT NULL""",
		as_dict=True,
	)

	return old_data or []


def _create_ecommerce_items(items: list[_dict]) -> None:
	for item in items:
		if not all((item.erpnext_item_code, item.shopify_product_id, item.shopify_variant_id)):
			continue

		ecommerce_item = frappe.get_doc(
			{
				"doctype": "Ecommerce Item",
				"integration": MODULE_NAME,
				"erpnext_item_code": item.erpnext_item_code,
				"integration_item_code": item.shopify_product_id,
				"variant_id": item.shopify_variant_id,
				"variant_of": item.variant_of,
				"has_variants": item.has_variants,
			}
		)
		ecommerce_item.save()
