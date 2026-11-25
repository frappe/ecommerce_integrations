# Copyright (c) 2021, Frappe and Contributors
# See LICENSE

import base64
import hashlib
import hmac
import unittest

import frappe
from frappe.utils.password import get_decrypted_password, remove_encrypted_password

from ecommerce_integrations.shopify.constants import (
	ADDRESS_ID_FIELD,
	CUSTOMER_ID_FIELD,
	FULLFILLMENT_ID_FIELD,
	ITEM_SELLING_RATE_FIELD,
	ORDER_ID_FIELD,
	ORDER_ITEM_DISCOUNT_FIELD,
	ORDER_NUMBER_FIELD,
	ORDER_STATUS_FIELD,
	SETTING_DOCTYPE,
	SUPPLIER_ID_FIELD,
)

from .shopify_setting import setup_custom_fields


class TestShopifySetting(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.db.sql(
			"""delete from `tabCustom Field`
			where name like '%shopify%'"""
		)

	def test_custom_field_creation(self):
		setup_custom_fields()

		created_fields = frappe.get_all(
			"Custom Field",
			filters={"fieldname": ["LIKE", "%shopify%"]},
			fields="fieldName",
			as_list=True,
			order_by=None,
		)

		required_fields = set(
			[
				ADDRESS_ID_FIELD,
				CUSTOMER_ID_FIELD,
				FULLFILLMENT_ID_FIELD,
				ITEM_SELLING_RATE_FIELD,
				ORDER_ID_FIELD,
				ORDER_NUMBER_FIELD,
				ORDER_STATUS_FIELD,
				SUPPLIER_ID_FIELD,
				ORDER_ITEM_DISCOUNT_FIELD,
			]
		)

		self.assertGreaterEqual(len(created_fields), 13)
		created_fields_set = {d[0] for d in created_fields}

		self.assertEqual(created_fields_set, required_fields)


class TestSharedSecretEncryption(unittest.TestCase):
	def tearDown(self):
		frappe.db.rollback()

	def seed_unencrypted_secret(self, secret):
		remove_encrypted_password(SETTING_DOCTYPE, SETTING_DOCTYPE, "shared_secret")
		frappe.db.set_single_value(SETTING_DOCTYPE, "shared_secret", secret, update_modified=False)

	def test_plaintext_secret_is_encrypted_and_webhooks_still_validate(self):
		from ecommerce_integrations.patches import encrypt_shopify_shared_secret as encryption
		from ecommerce_integrations.shopify.connection import _validate_request

		secret = "plaintext-secret"
		body = b'{"id": 1}'
		self.seed_unencrypted_secret(secret)

		encryption.execute()

		self.assertEqual(
			get_decrypted_password(SETTING_DOCTYPE, SETTING_DOCTYPE, "shared_secret"),
			secret,
		)
		stored = frappe.db.get_single_value(SETTING_DOCTYPE, "shared_secret", cache=False)
		self.assertEqual(set(stored), {"*"})

		digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
		hmac_header = base64.b64encode(digest).decode()
		_validate_request(frappe._dict(data=body), hmac_header)

	def test_patch_is_idempotent(self):
		from ecommerce_integrations.patches import encrypt_shopify_shared_secret as encryption

		self.seed_unencrypted_secret("plaintext-secret")

		encryption.execute()
		encryption.execute()

		self.assertEqual(
			get_decrypted_password(SETTING_DOCTYPE, SETTING_DOCTYPE, "shared_secret"),
			"plaintext-secret",
		)
