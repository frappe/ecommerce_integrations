import frappe
from frappe.test_runner import make_test_records

from ecommerce_integrations.unicommerce.customer import (
	_create_customer_addresses,
	_create_new_customer,
	sync_customer,
)
from ecommerce_integrations.unicommerce.tests.test_client import TestCaseApiClient


class TestUnicommerceProduct(TestCaseApiClient):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_test_records("Unicommerce Channel")

	def test_create_customer(self):
		order = self.load_fixture("order-SO5905")["saleOrderDTO"]

		_create_new_customer(order)

		customer = frappe.get_last_doc("Customer")
		self.assertEqual(customer.customer_group, "Individual")
		self.assertEqual(customer.customer_type, "Individual")
		self.assertEqual(customer.customer_name, "Ramesh Suresh")

		_create_customer_addresses(order.get("addresses", []), customer)

		new_addresses = frappe.get_all("Address", filters={"link_name": customer.name}, fields=["*"])

		self.assertEqual(len(new_addresses), 2)
		addr_types = {d.address_type for d in new_addresses}
		self.assertEqual(addr_types, {"Shipping", "Billing"})

		states = {d.state for d in new_addresses}
		self.assertEqual(states, {"Maharashtra"})

	def test_deduplication(self):
		"""requirement: Literally same order should not create duplicates."""
		order = self.load_fixture("order-SO5841")["saleOrderDTO"]
		customer = sync_customer(order)
		same_customer = sync_customer(order)

		self.assertEqual(customer.name, same_customer.name)
