import frappe
import json

@frappe.whitelist()
def validate_partial_picking(so_item_list):
	if isinstance(so_item_list, str):
		so_item_list = json.loads(so_item_list)
	for so in so_item_list:
		so_item_doc = frappe.get_doc("Sales Order Item",so['so_item'])
	if (((so_item_doc.picked_qty + so['qty'] )/ so_item_doc.qty)*100) >
 (100 + frappe.db.get_single_value('Stock Settings', 'over_delivery_receipt_allowance')):
		frappe.throw('You are picking more than required quantity for '
 + so_item_doc.item_code + '. Check if there is any other picklist for ' + so_item_doc.parent)

@frappe.whitelist()
def validate(self, method = None):
	if self.get('locations'):
		for pl in self.get('locations'):
			if pl.picked_qty and float(pl.picked_qty) > 0:
				if pl.picked_qty > pl.qty:
					pl.picked_qty = pl.qty
					frappe.throw("Row "+ str(pl.idx) + ": Picked Qty cannot be more than Sales Order Qty")
				if pl.picked_qty == 0 and self.docstatus == 1:
					frappe.throw('You have not picked ' + pl.item_code + ' in row '+ str(pl.idx) + '. Pick the item to proceed!')
		item_so_list = [d.sales_order for d in self.get("locations")]
		unique_so_list =[]
		for i in item_so_list:
			if i not in unique_so_list:
				unique_so_list.append(i)
		if len(unique_so_list) > 30:
			frappe.throw('Cannot add more than 30 Sales Orders!')
		so_list = [d.sales_order for d in self.get("order_details")]
		for so in unique_so_list:
			if so not in so_list:
				pl_so_child = self.append("order_details",{})
				pl_so_child.sales_order = so
			total_item_count = 0
			fully_picked_item_count = 0
			partial_picked_item_count = 0
			for item in self.get('locations'):
				if item.sales_order == so:
					total_item_count = total_item_count + 1
					if item.picked_qty == item.qty:
						fully_picked_item_count = fully_picked_item_count + 1
					elif int(item.picked_qty) > 0:
						partial_picked_item_count = partial_picked_item_count + 1
			if fully_picked_item_count == total_item_count:
				for x in self.get("order_details"):
					if x.sales_order == so:
						x.pick_status = 'Fully Picked'
			elif fully_picked_item_count == 0 and partial_picked_item_count == 0:
				for x in self.get("order_details"):
					if x.sales_order == so:
						x.pick_status = ''
			elif int(partial_picked_item_count) > 0:
				for x in self.get("order_details"):
					if x.sales_order == so:
						x.pick_status = 'Partially Picked'
