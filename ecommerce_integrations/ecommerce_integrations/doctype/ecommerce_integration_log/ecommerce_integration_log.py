# Copyright (c) 2021, Frappe and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.utils.data import cstr
from frappe.model.document import Document


class EcommerceIntegrationLog(Document):
	pass


def create_log(
	module_def=None,
	status="Queued",
	response_data=None,
	request_data=None,
	exception=None,
	rollback=False,
	method=None,
):
	make_new = not bool(frappe.flags.request_id)

	if rollback:
		frappe.db.rollback()

	if make_new:
		log = frappe.get_doc(
			{"doctype": "Ecommerce Integration Log", "integration": cstr(module_def)}
		)
		log.insert(ignore_permissions=True)
	else:
		log = frappe.get_doc("Ecommerce Integration Log", frappe.flags.request_id)

	if not isinstance(response_data, str):
		response_data = json.dumps(response_data, sort_keys=True, indent=4)

	if not isinstance(request_data, str):
		request_data = json.dumps(request_data, sort_keys=True, indent=4)

	log.message = __get_message(exception)
	log.method = log.method or method
	log.response_data = log.response_data or response_data
	log.request_data = log.request_data or request_data
	log.traceback = log.traceback or frappe.get_traceback()
	log.status = status
	log.save(ignore_permissions=True)

	frappe.db.commit()

	return log


def __get_message(exception):
	if hasattr(exception, "message"):
		message = exception.message
	elif hasattr(exception, "__str__"):
		message = exception.__str__()
	else:
		message = _("Something went wrong while syncing")

	return message


@frappe.whitelist()
def resync(method, name, request_data):
	frappe.db.set_value(
		"Ecommerce Integration Log", name, "status", "Queued", update_modified=False
	)
	frappe.enqueue(
		method=method,
		queue="short",
		timeout=300,
		is_async=True,
		**{"order": json.loads(request_data), "request_id": name}
	)
