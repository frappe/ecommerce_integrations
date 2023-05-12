import json

import frappe
from frappe import _


def validate(self, method=None):
	sales_order = self.get("locations")[0].sales_order
	unicommerce_order_code = frappe.db.get_value("Sales Order", sales_order, "unicommerce_order_code")
	if unicommerce_order_code:
		if self.get("locations"):
			for pl in self.get("locations"):
				if pl.picked_qty and float(pl.picked_qty) > 0:
					if pl.picked_qty > pl.qty:
						pl.picked_qty = pl.qty

						frappe.throw(_("Row {0} Picked Qty cannot be more than Sales Order Qty").format(pl.idx))
				if pl.picked_qty == 0 and pl.docstatus == 1:
					frappe.throw(
						_("You have not picked {0} in row {1} . Pick the item to proceed!").format(
							pl.item_code, pl.idx
						)
					)
			item_so_list = [d.sales_order for d in self.get("locations")]
			unique_so_list = []
			for i in item_so_list:
				if i not in unique_so_list:
					unique_so_list.append(i)
			so_list = [d.sales_order for d in self.get("order_details")]
			for so in unique_so_list:
				if so not in so_list:
					pl_so_child = self.append("order_details", {})
					pl_so_child.sales_order = so
				total_item_count = 0
				fully_picked_item_count = 0
				partial_picked_item_count = 0
				for item in self.get("locations"):
					if item.sales_order == so:
						total_item_count = total_item_count + 1
						if item.picked_qty == item.qty:
							fully_picked_item_count = fully_picked_item_count + 1
						elif int(item.picked_qty) > 0:
							partial_picked_item_count = partial_picked_item_count + 1
				if fully_picked_item_count == total_item_count:
					for x in self.get("order_details"):
						if x.sales_order == so:
							x.pick_status = "Fully Picked"
				elif fully_picked_item_count == 0 and partial_picked_item_count == 0:
					for x in self.get("order_details"):
						if x.sales_order == so:
							x.pick_status = ""
				elif int(partial_picked_item_count) > 0:
					for x in self.get("order_details"):
						if x.sales_order == so:
							x.pick_status = "Partially Picked"
