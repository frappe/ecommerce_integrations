import frappe
from frappe import _
from frappe.utils import date_diff, getdate

from ecommerce_integrations.unicommerce.api_client import UnicommerceAPIClient, _utc_timeformat
from ecommerce_integrations.unicommerce.constants import ORDER_CODE_FIELD, SETTINGS_DOCTYPE
from ecommerce_integrations.unicommerce.order import (
	_create_sales_invoices,
	backfill_display_order_code,
	create_order,
)
from ecommerce_integrations.unicommerce.utils import create_unicommerce_log

SEARCH_ENDPOINT = "/services/rest/v1/oms/saleOrder/search"

PAGE_SIZE = 1000

LOCK_KEY = "unicommerce_sync_old_orders_running"
JOB_ID = "unicommerce_sync_old_orders"

JOB_TIMEOUT_SEC = 12 * 60 * 60
LOCK_TTL_SEC = 13 * 60 * 60

# Max inclusive calendar days (e.g. 1 Jan-31 Jan = 31 days). Uni search cap is ~31 days.
MAX_SELECTABLE_RANGE_DAYS = 31


def _validate_date_range(from_date, to_date):
	"""Validate dates server-side (endpoint is callable directly, not just via the JS button)."""
	if not from_date or not to_date:
		frappe.throw(_("Both From Date and To Date are required."))
	from_date, to_date = getdate(from_date), getdate(to_date)
	if from_date > to_date:
		frappe.throw(_("From Date cannot be after To Date."))
	if from_date > getdate():
		frappe.throw(_("From Date cannot be in the future."))
	# Inclusive day count: 1 Jan-31 Jan -> 31; reject anything longer.
	if date_diff(to_date, from_date) + 1 > MAX_SELECTABLE_RANGE_DAYS:
		frappe.throw(_("Date range cannot be longer than {0} days.").format(MAX_SELECTABLE_RANGE_DAYS))
	return from_date, to_date


@frappe.whitelist()
def enqueue_sync_old_orders(from_date: str, to_date: str):
	"""Run the old-order sync in the background `long` queue"""
	frappe.only_for("System Manager")

	from_date, to_date = _validate_date_range(from_date, to_date)

	if frappe.cache().get_value(LOCK_KEY):
		frappe.throw(_("A sync is already running. Please wait for it to finish."))

	# deduplicate + job_id atomically refuses a second job that is already queued/started.
	enqueued = frappe.enqueue(
		"ecommerce_integrations.unicommerce.sync_old_orders.sync_old_orders",
		queue="long",
		timeout=JOB_TIMEOUT_SEC,
		job_id=JOB_ID,
		deduplicate=True,
		from_date=from_date,
		to_date=to_date,
	)
	if enqueued is None:
		frappe.throw(_("A sync is already queued. Please wait for it to finish."))
	return f"Old-order sync queued for {from_date} -> {to_date}"


def sync_old_orders(from_date, to_date, client=None):
	"""Fetch every order CREATED in [from_date, to_date) and sync the missing ones."""
	settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)
	if not settings.is_enabled():
		return {"error": "Unicommerce integration is disabled"}

	# --- single-run guard: block a second concurrent sync ---
	if frappe.cache().get_value(LOCK_KEY):
		return {"error": "A sync is already running. Please wait for it to finish."}
	frappe.cache().set_value(LOCK_KEY, 1, expires_in_sec=LOCK_TTL_SEC)
	try:
		return _run_sync(settings, from_date, to_date, client)
	finally:
		frappe.cache().delete_value(LOCK_KEY)


def _run_sync(settings, from_date, to_date, client=None):
	"""Core sync loop. Runs under the single-run lock held by sync_old_orders."""
	if client is None:
		client = UnicommerceAPIClient()

	frappe.set_user("Administrator")  # nosemgrep: frappe-semgrep-rules.rules.security.frappe-setuser
	completed_mode = bool(settings.only_sync_completed_orders)
	status = "COMPLETE" if completed_mode else None
	enabled_channels = set(
		frappe.get_all("Unicommerce Channel", filters={"enabled": 1}, pluck="channel_id", limit=0)
	)

	summary = {
		"range": f"{from_date} -> {to_date}",
		"total_reported": None,
		"fetched": 0,
		"created": 0,
		"skipped_existing": 0,
		"off_channel": 0,
		"failed": 0,
		"incomplete": False,
	}

	for page in _fetch_orders_in_range(client, from_date, to_date, status, summary):
		existing = set(
			frappe.get_all(
				"Sales Order",
				filters={ORDER_CODE_FIELD: ["in", [o["code"] for o in page]]},
				pluck=ORDER_CODE_FIELD,
			)
		)

		for order_summary in page:
			code = order_summary["code"]
			summary["fetched"] += 1

			if order_summary.get("channel") not in enabled_channels:
				summary["off_channel"] += 1
				continue

			# If the SO already exists with not completed status, will be skipped.
			existing_so = code in existing
			if existing_so and not completed_mode:
				# The order is skipped, but its display order no. may still be missing.
				# displayOrderCode comes from the search result, so this needs no API call.
				# Log and move on if it fails: the order itself is already synced.
				try:
					backfill_display_order_code(code, order_summary.get("displayOrderCode"))
				except Exception as e:
					create_unicommerce_log(status="Error", exception=e, rollback=True, make_new=True)
				summary["skipped_existing"] += 1
				continue

			try:
				detail = client.get_sales_order(order_code=code)
				if not detail:
					summary["failed"] += 1
					continue

				# create_order is self-contained: it de-dupes, logs, commits on
				# success and rolls back its own partial work on failure.
				sales_order = create_order(detail, client=client)
				if sales_order is None:
					summary["failed"] += 1
					continue

				if completed_mode:
					_create_sales_invoices(detail, sales_order, client)

				# Count only after the order (and its invoice) is fully handled, so a
				# late failure lands in `failed` instead of double-counting this order.
				if existing_so:
					summary["skipped_existing"] += 1
				else:
					summary["created"] += 1
			except Exception as e:
				summary["failed"] += 1
				create_unicommerce_log(status="Error", exception=e, rollback=True, make_new=True)

	_log_summary(summary)
	return summary


def _fetch_orders_in_range(client, from_date, to_date, status, summary):
	"""Yield each page of UNIQUE orders (each with a valid code) in the date range."""
	# Full days: start of From .. end of To. No padding, so the whole range fits the 31-day cap.
	base_body = {
		"fromDate": _utc_timeformat(f"{from_date} 00:00:00"),
		"toDate": _utc_timeformat(f"{to_date} 23:59:59"),
		"dateType": "CREATED",
	}
	if status:
		base_body["status"] = status

	display_start = 0
	total_records = None
	seen = set()

	while True:
		body = dict(base_body)
		body["searchOptions"] = {
			"displayStart": display_start,
			"displayLength": PAGE_SIZE,
			"getCount": display_start == 0,
		}

		resp, ok = _request_with_retry(client, body)
		if not ok or resp is None:
			summary["incomplete"] = True
			create_unicommerce_log(
				status="Error",
				make_new=True,
				message=(
					f"Old-order sync search FAILED at displayStart={display_start} "
					f"for range. Already-created orders are safe. "
					f"Re-run the same range to resume."
				),
			)
			return

		if display_start == 0:
			total_records = resp.get("totalRecords")
			summary["total_reported"] = total_records
			create_unicommerce_log(
				status="Success",
				make_new=True,
				message=f"Old-order sync: Unicommerce reports {total_records} orders in this range.",
			)

		elements = resp.get("elements") or []
		if not elements:
			return

		new_page = []
		for element in elements:
			code = element.get("code")
			if code and code not in seen:
				seen.add(code)
				new_page.append(element)  # only NEW, valid orders

		# Stall guard: a full page with ZERO new orders means the API ignored
		if not new_page:
			summary["incomplete"] = True
			create_unicommerce_log(
				status="Error",
				make_new=True,
				message=(
					f"Old-order sync STALLED at displayStart={display_start}: page returned "
					f"no new orders (pagination not advancing). Check the API / re-run."
				),
			)
			return

		yield new_page

		display_start += len(elements)

		# Stop once the reported total is reached
		if total_records is not None and display_start >= total_records:
			return


def _request_with_retry(client, body, attempts=3):
	"""POST to the search endpoint, retrying a few times on transient failure."""
	resp, ok = None, False
	for attempt in range(attempts):
		is_last_attempt = attempt == attempts - 1
		# Treat an unexpected raise as a failed attempt so it doesn't abort the backfill unlogged.
		try:
			resp, ok = client.request(endpoint=SEARCH_ENDPOINT, body=body, log_error=is_last_attempt)
		except Exception:
			resp, ok = None, False
		if ok and resp is not None:
			return resp, True
	return resp, ok


def _log_summary(summary):
	incomplete = summary.get("incomplete")
	headline = "INCOMPLETE — re-run this range" if incomplete else "complete"
	status = "Error" if incomplete else "Success"
	message = (
		f"Unicommerce old-order sync {headline} for {summary['range']}\n"
		f"  Reported by Unicommerce : {summary['total_reported']}\n"
		f"  Orders walked           : {summary['fetched']}\n"
		f"  Created (new)           : {summary['created']}\n"
		f"  Skipped (already synced): {summary['skipped_existing']}\n"
		f"  Skipped (off-channel)   : {summary['off_channel']}\n"
		f"  Failed                  : {summary['failed']}"
	)
	create_unicommerce_log(status=status, make_new=True, message=message)
