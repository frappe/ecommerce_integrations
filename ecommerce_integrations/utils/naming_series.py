import frappe


@frappe.whitelist()
def get_series():
	series = {
		"sales_order_series": frappe.get_meta("Sales Order").get_options("naming_series"),
		"sales_invoice_series": frappe.get_meta("Sales Invoice").get_options("naming_series"),
		"delivery_note_series": frappe.get_meta("Delivery Note").get_options("naming_series"),
	}
	series["return_invoice_series"] = series["sales_invoice_series"]
	series["return_delivery_series"] = series["delivery_note_series"]
	return series
