# Copyright (c) 2021, Frappe and contributors
# For license information, please see LICENSE

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder import Interval
from frappe.query_builder.functions import Now
from frappe.utils import strip_html
from frappe.utils.data import cstr


class EcommerceIntegrationLog(Document):
	def validate(self):
		self._set_title()

	def _set_title(self):
		title = None
		if self.message != "None":
			title = self.message

		if not title and self.method:
			method = self.method.split(".")[-1]
			title = method

		if title:
			title = strip_html(title)
			self.title = title if len(title) < 100 else title[:100] + "..."

	@staticmethod
	def clear_old_logs(days=90):
		table = frappe.qb.DocType("Ecommerce Integration Log")
		frappe.db.delete(
			table, filters=(table.modified < (Now() - Interval(days=days))) & (table.status == "Success")
		)


def create_log(
	module_def=None,
	status="Queued",
	response_data=None,
	request_data=None,
	exception=None,
	rollback=False,
	method=None,
	message=None,
	make_new=False,
):
	make_new = make_new or not bool(frappe.flags.request_id)

	if rollback:
		frappe.db.rollback()

	if make_new:
		log = frappe.get_doc({"doctype": "Ecommerce Integration Log", "integration": cstr(module_def)})
		log.insert(ignore_permissions=True)
	else:
		log = frappe.get_doc("Ecommerce Integration Log", frappe.flags.request_id)

	if response_data and not isinstance(response_data, str):
		response_data = json.dumps(response_data, sort_keys=True, indent=4)

	if request_data and not isinstance(request_data, str):
		request_data = json.dumps(request_data, sort_keys=True, indent=4)

	log.message = message or _get_message(exception)
	log.method = log.method or method
	log.response_data = response_data or log.response_data
	log.request_data = request_data or log.request_data
	log.traceback = log.traceback or frappe.get_traceback()
	log.status = status
	log.save(ignore_permissions=True)

	frappe.db.commit()

	return log


def _get_message(exception):
	if hasattr(exception, "message"):
		return strip_html(exception.message)
	elif hasattr(exception, "__str__"):
		return strip_html(exception.__str__())
	else:
		return _("Something went wrong while syncing")


@frappe.whitelist()
def resync(method, name, request_data):
	_retry_job(name)


def _retry_job(job: str):
	frappe.only_for("System Manager")

	doc = frappe.get_doc("Ecommerce Integration Log", job)
	if not doc.method.startswith("ecommerce_integrations.") or doc.status != "Error":
		return

	doc.db_set("status", "Queued", update_modified=False)
	doc.db_set("traceback", "", update_modified=False)

	frappe.enqueue(
		method=doc.method,
		queue="short",
		timeout=300,
		is_async=True,
		payload=json.loads(doc.request_data),
		request_id=doc.name,
		enqueue_after_commit=True,
	)


@frappe.whitelist()
def bulk_retry(names):
	if isinstance(names, str):
		names = json.loads(names)
	for name in names:
		_retry_job(name)
