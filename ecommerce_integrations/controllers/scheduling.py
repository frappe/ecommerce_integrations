import frappe
from frappe.utils import add_to_date, cint, get_datetime, now


def need_to_run(setting, interval_field, timestamp_field, docname: str | None = None) -> bool:
	"""A utility function to make "configurable" scheduled events.

	If timestamp_field is older than current_time - inveterval_field then this function updates the timestamp_field to `now()` and returns True,
	otherwise False.
	This can be used to make "configurable" scheduled events.
	Assumptions:
	        - interval_field is in minutes.
	        - timestamp field is datetime field.
	        - This function is called from scheuled job with less frequency than lowest interval_field. Ideally, every minute.
	"""
	if docname:
		values = frappe.db.get_value(setting, docname, [interval_field, timestamp_field], as_dict=1) or {}
		interval = values.get(interval_field)
		last_run = values.get(timestamp_field)
	else:
		interval = frappe.db.get_single_value(setting, interval_field, cache=True)
		last_run = frappe.db.get_single_value(setting, timestamp_field)

	if last_run and get_datetime() < get_datetime(add_to_date(last_run, minutes=cint(interval, default=10))):
		return False

	if docname:
		frappe.db.set_value(setting, docname, timestamp_field, now(), update_modified=False)
	else:
		frappe.db.set_value(setting, None, timestamp_field, now(), update_modified=False)
	return True
