import frappe
from frappe import _
from frappe.utils.nestedset import get_root_of


class EcommerceCustomer:
	def __init__(self, customer_id: str, customer_id_field: str, integration: str):
		self.customer_id = customer_id
		self.customer_id_field = customer_id_field
		self.integration = integration

	def is_synced(self) -> bool:
		"""Check if customer on Ecommerce site is synced with ERPNext"""

		return bool(frappe.db.exists("Customer", {self.customer_id_field: self.customer_id}))

	def get_customer_doc(self):
		"""Get ERPNext customer document."""
		if self.is_synced():
			return frappe.get_last_doc("Customer", {self.customer_id_field: self.customer_id})
		else:
			raise frappe.DoesNotExistError()

	def sync_customer(self, customer_name: str, customer_group: str) -> None:
		"""Create customer in ERPNext if one does not exist already."""
		customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"name": self.customer_id,
				self.customer_id_field: self.customer_id,
				"customer_name": customer_name,
				"customer_group": customer_group,
				"territory": get_root_of("Territory"),
				"customer_type": _("Individual"),
			}
		)

		customer.flags.ignore_mandatory = True
		customer.insert(ignore_permissions=True)

	def get_customer_address_doc(self, address_type: str):
		try:
			customer = self.get_customer_doc().name
			addresses = frappe.get_all("Address", {"link_name": customer, "address_type": address_type})
			if addresses:
				address = frappe.get_last_doc("Address", {"name": addresses[0].name})
				return address
		except frappe.DoesNotExistError:
			return None

	def create_customer_address(self, address: dict[str, str]) -> None:
		"""Create address from dictionary containing fields used in Address doctype of ERPNext."""

		customer_doc = self.get_customer_doc()

		frappe.get_doc(
			{
				"doctype": "Address",
				**address,
				"links": [{"link_doctype": "Customer", "link_name": customer_doc.name}],
			}
		).insert(ignore_mandatory=True)

	def create_customer_contact(self, contact: dict[str, str]) -> None:
		"""Create contact from dictionary containing fields used in Address doctype of ERPNext."""

		customer_doc = self.get_customer_doc()

		frappe.get_doc(
			{
				"doctype": "Contact",
				**contact,
				"links": [{"link_doctype": "Customer", "link_name": customer_doc.name}],
			}
		).insert(ignore_mandatory=True)
