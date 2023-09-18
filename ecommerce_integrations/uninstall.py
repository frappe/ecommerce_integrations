import frappe


def before_uninstall():
	# This large table is linked with "modules" hence gets deleted one by one
	frappe.db.delete("Ecommerce Integration Log")
