# Copyright (c) 2024, Frappe and contributors
# For license information, please see LICENSE

import frappe

from ecommerce_integrations.medusa.connection import process_request
from ecommerce_integrations.medusa.constants import MODULE_NAME, ORDER_ID_FIELD, SETTING_DOCTYPE

from .utils import TestCase

ORDER_ID = "order_01HORDER00000000000000001"


class TestConnection(TestCase):
	def test_disabled_integration_ignores_signed_events(self):
		# A correctly-signed event must not mutate ERPNext while the integration is
		# disabled (e.g. a stale subscriber still holding the secret).
		frappe.db.set_single_value(SETTING_DOCTYPE, "enable_medusa", 0)
		try:
			process_request(self.load_fixture("order"), "order.placed")

			self.assertFalse(frappe.db.exists("Sales Order", {ORDER_ID_FIELD: ORDER_ID}))
			self.assertTrue(
				frappe.db.exists(
					"Ecommerce Integration Log", {"integration": MODULE_NAME, "status": "Invalid"}
				)
			)
		finally:
			frappe.db.set_single_value(SETTING_DOCTYPE, "enable_medusa", 1)

	def test_fetch_locations_does_not_require_warehouse(self):
		# fetching stock locations appends rows that have no ERPNext warehouse yet; it must
		# not save (validate would reject them) before the operator maps them.
		setting = frappe.get_doc(SETTING_DOCTYPE)
		setting.fetch_medusa_locations()
		self.assertTrue(any(r.medusa_location_id for r in setting.medusa_warehouse_mapping))
