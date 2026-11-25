"""Move Shopify's plaintext shared secret into encrypted storage."""

import frappe
from frappe.utils.password import set_encrypted_password

from ecommerce_integrations.shopify.constants import SETTING_DOCTYPE


def execute():
	secret = frappe.db.get_single_value(SETTING_DOCTYPE, "shared_secret", cache=False)

	# A missing value or the dummy value of a previously encrypted Password field
	# needs no migration, which also makes this patch safe to run more than once.
	if not secret or set(secret) == {"*"}:
		return

	set_encrypted_password(SETTING_DOCTYPE, SETTING_DOCTYPE, secret, "shared_secret")
	frappe.db.set_single_value(
		SETTING_DOCTYPE,
		"shared_secret",
		"*" * len(secret),
		update_modified=False,
	)
