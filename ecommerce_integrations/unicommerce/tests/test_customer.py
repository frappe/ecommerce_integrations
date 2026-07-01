import frappe

from ecommerce_integrations.unicommerce.customer import (
	_create_customer_addresses,
	_create_new_customer,
	sync_customer,
)
from ecommerce_integrations.unicommerce.tests.test_client import UnicommerceClientTestSuite


class TestUnicommerceCustomer(UnicommerceClientTestSuite):
	def test_create_customer(self):
		order = self.load_fixture("order-SO5905")["saleOrderDTO"]

		_create_new_customer(order)

		customer = frappe.get_last_doc("Customer")
		self.assertEqual(customer.customer_group, "Individual")
		self.assertEqual(customer.customer_type, "Individual")
		self.assertEqual(customer.customer_name, "Ramesh Suresh")

		_create_customer_addresses(order.get("addresses", []), customer)

		new_addresses = frappe.get_all(
			"Address", filters={"link_name": customer.name}, fields=["address_type", "state"]
		)

		self.assertEqual(len(new_addresses), 2)

		address_types = {address.address_type for address in new_addresses}
		self.assertEqual(address_types, {"Shipping", "Billing"})

		states = {address.state for address in new_addresses}
		self.assertEqual(states, {"Maharashtra"})

	def test_deduplication(self):
		"""requirement: Literally same order should not create duplicates."""
		order = self.load_fixture("order-SO5841")["saleOrderDTO"]

		customer = sync_customer(order)
		same_customer = sync_customer(order)

		self.assertEqual(customer.name, same_customer.name)
