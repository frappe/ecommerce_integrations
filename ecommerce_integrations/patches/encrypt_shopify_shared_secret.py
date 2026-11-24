"""Move the plaintext `shared_secret` of each Shopify Account into encrypted storage.

The field was a plain Data field: the secret sat unencrypted in the column, fully
readable on the form and in the Activity log. As a Password field its value belongs
in the __Auth table, and the column must keep only the dummy mask - otherwise the
secret stays readable in the database.
"""

import frappe
from frappe.utils.password import set_encrypted_password

from ecommerce_integrations.shopify.constants import ACCOUNT_DOCTYPE


def execute():
	for account in frappe.get_all(ACCOUNT_DOCTYPE, ["name", "shared_secret"]):
		secret = account.shared_secret
		# never set or already masked: nothing to move, so re-running the patch is safe
		if not secret or set(secret) == {"*"}:
			continue

		set_encrypted_password(ACCOUNT_DOCTYPE, account.name, secret, "shared_secret")
		frappe.db.set_value(
			ACCOUNT_DOCTYPE, account.name, "shared_secret", "*" * len(secret), update_modified=False
		)
