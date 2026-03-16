import frappe
from frappe import _
from querystring_parser.parser import parse

from ecommerce_integrations.whataform.constants import EVENT_MAPPER, SETTING_DOCTYPE
from ecommerce_integrations.whataform.utils import create_whataform_log


def get_current_domain_name() -> str:
	"""Get current site domain name. E.g. test.erpnext.com

	If developer_mode is enabled and localtunnel_url is set in site config then domain  is set to localtunnel_url.
	"""
	if frappe.conf.developer_mode and frappe.conf.localtunnel_url:
		return frappe.conf.localtunnel_url
	else:
		return frappe.request.host


def get_callback_url() -> str:
	"""This must be configured as the Whataform webhook for new messages.

	If developer_mode is enabled and localtunnel_url is set in site config then callback url is set to localtunnel_url.
	"""
	url = get_current_domain_name()

	return (
		f"https://{url}/api/v2/method/ecommerce_integrations.whataform.connection.store_message_data"
	)


@frappe.whitelist(allow_guest=True)
def store_message_data() -> None:
	if frappe.local.request:
		event = "message"
		data = parse(frappe.local.request.query_string)
		_validate_request_data(data)

		process_request(data, event)


def process_request(data, event):

	# create log
	log = create_whataform_log(method=EVENT_MAPPER[event], request_data=data)

	# enqueue backround job
	frappe.enqueue(
		method=EVENT_MAPPER[event],
		queue="short",
		timeout=300,
		is_async=not frappe.flags.in_test,
		**{"payload": data, "request_id": log.name},
	)


def _validate_request_data(data):
	settings = frappe.get_doc(SETTING_DOCTYPE)
	if settings.form_id != data.form:
		create_whataform_log(status="Error", request_data=data)
		frappe.throw(_("Form ID doesn't match"))
