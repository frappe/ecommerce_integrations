from unittest.mock import MagicMock, patch

import frappe
from frappe.utils import add_days, getdate

try:  # frappe >= v16
	from frappe.tests import IntegrationTestCase
except ImportError:  # frappe v15
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from ecommerce_integrations.unicommerce import sync_old_orders as sof
from ecommerce_integrations.unicommerce.constants import ORDER_CODE_FIELD

SOF = "ecommerce_integrations.unicommerce.sync_old_orders"


def _page(codes, total=None):
	"""Build a fake search response page (frappe._dict like the real client returns)."""
	return frappe._dict({"elements": [{"code": c} for c in codes], "totalRecords": total})


class TestFetchOrdersInRange(IntegrationTestCase):
	"""Unit tests for the paginated fetch generator (the new pagination logic)."""

	def _fetch(self, client, status=None):
		# _fetch_orders_in_range yields one PAGE (list of elements) per iteration;
		# flatten to a single list of codes for easy assertions.
		summary = {"total_reported": None, "incomplete": False}
		with patch.object(sof, "create_unicommerce_log"):
			pages = list(sof._fetch_orders_in_range(client, "2026-04-01", "2026-04-30", status, summary))
		return summary, [e["code"] for page in pages for e in page]

	def test_walks_all_pages_and_dedupes(self):
		client = MagicMock()
		p1 = _page([f"O{i}" for i in range(1000)], total=1500)
		p2 = _page([f"O{i}" for i in range(1000, 1500)], total=1500)
		client.request.side_effect = [(p1, True), (p2, True)]

		summary, codes = self._fetch(client)

		self.assertEqual(len(codes), 1500)
		self.assertEqual(len(set(codes)), 1500)  # all unique
		self.assertEqual(summary["total_reported"], 1500)
		self.assertFalse(summary["incomplete"])
		# stops exactly at total -> no wasted 3rd page request
		self.assertEqual(client.request.call_count, 2)

	def test_sends_full_day_boundaries(self):
		# No ±1-day padding: the search covers start-of-From .. end-of-To exactly.
		client = MagicMock()
		client.request.return_value = (_page([], total=0), True)
		summary = {"total_reported": None, "incomplete": False}
		with (
			patch.object(sof, "create_unicommerce_log"),
			patch.object(sof, "_utc_timeformat", side_effect=str),  # pass the datetime through unchanged
		):
			list(sof._fetch_orders_in_range(client, "2026-04-01", "2026-04-30", None, summary))
		body = client.request.call_args.kwargs["body"]
		self.assertEqual(body["fromDate"], "2026-04-01 00:00:00")
		self.assertEqual(body["toDate"], "2026-04-30 23:59:59")

	def test_empty_page_stops_cleanly(self):
		client = MagicMock()
		client.request.return_value = (_page([], total=0), True)

		summary, codes = self._fetch(client)

		self.assertEqual(codes, [])
		self.assertEqual(summary["total_reported"], 0)
		self.assertFalse(summary["incomplete"])

	def test_search_failure_flags_incomplete(self):
		client = MagicMock()
		client.request.return_value = (None, False)  # fails every retry

		summary, codes = self._fetch(client)

		self.assertEqual(codes, [])
		self.assertTrue(summary["incomplete"])

	def test_stall_guard_on_non_advancing_api(self):
		# Same page returned twice -> 0 new on the 2nd page -> must stop + flag incomplete.
		client = MagicMock()
		page = _page(["A", "B"], total=None)
		client.request.side_effect = [(page, True), (page, True)]

		summary, codes = self._fetch(client)

		self.assertEqual(codes, ["A", "B"])  # only the first page's new codes
		self.assertTrue(summary["incomplete"])

	def test_skips_codeless_elements(self):
		client = MagicMock()
		bad = frappe._dict({"elements": [{"code": "A"}, {"code": None}, {"code": ""}], "totalRecords": None})
		client.request.side_effect = [(bad, True), (_page([], total=None), True)]

		summary, codes = self._fetch(client)

		self.assertEqual(codes, ["A"])  # None / "" are skipped


class TestRequestWithRetry(IntegrationTestCase):
	def test_succeeds_after_transient_failures(self):
		client = MagicMock()
		client.request.side_effect = [(None, False), (None, False), ({"ok": 1}, True)]

		resp, ok = sof._request_with_retry(client, {})

		self.assertTrue(ok)
		self.assertEqual(resp, {"ok": 1})
		self.assertEqual(client.request.call_count, 3)

	def test_logs_real_error_only_on_last_attempt(self):
		client = MagicMock()
		client.request.return_value = (None, False)

		sof._request_with_retry(client, {}, attempts=3)

		log_flags = [c.kwargs.get("log_error") for c in client.request.call_args_list]
		self.assertEqual(log_flags, [False, False, True])


class TestLogSummary(IntegrationTestCase):
	def _summary(self, incomplete):
		return {
			"range": "2026-04-01 -> 2026-04-30",
			"total_reported": 10,
			"fetched": 10,
			"created": 9,
			"skipped_existing": 1,
			"off_channel": 0,
			"failed": 0,
			"incomplete": incomplete,
		}

	def test_incomplete_reported_as_error(self):
		with patch.object(sof, "create_unicommerce_log") as log:
			sof._log_summary(self._summary(incomplete=True))
		kwargs = log.call_args.kwargs
		self.assertEqual(kwargs["status"], "Error")
		self.assertIn("INCOMPLETE", kwargs["message"])

	def test_complete_reported_as_success(self):
		with patch.object(sof, "create_unicommerce_log") as log:
			sof._log_summary(self._summary(incomplete=False))
		kwargs = log.call_args.kwargs
		self.assertEqual(kwargs["status"], "Success")
		self.assertIn("complete", kwargs["message"])


class TestRunSyncOrchestration(IntegrationTestCase):
	"""Verifies the per-order routing/counting in _run_sync without hitting the DB."""

	def test_counts_created_skipped_and_off_channel(self):
		client = MagicMock()
		client.get_sales_order.return_value = {"code": "X", "status": "COMPLETE"}

		settings = MagicMock()
		settings.only_sync_completed_orders = False  # -> existing SOs skipped early

		orders = [
			{"code": "NEW1", "channel": "SHOPIFY"},  # new -> created
			{"code": "OFF", "channel": "AMAZON"},  # off-channel -> skipped
			# already synced -> skipped, but still backfilled from the search result
			{"code": "EXIST", "channel": "SHOPIFY", "displayOrderCode": "SO-EXIST"},
		]

		def fake_fetch(client, fr, to, status, summary):
			summary["total_reported"] = len(orders)
			yield orders  # a single page holding every order

		def fake_get_all(doctype, **kwargs):
			# _run_sync makes two pluck queries: enabled channels, and existing SOs.
			if doctype == "Unicommerce Channel":
				return ["SHOPIFY"]
			if doctype == "Sales Order":
				page_codes = kwargs["filters"][ORDER_CODE_FIELD][1]
				return [c for c in page_codes if c == "EXIST"]
			return []

		with (
			patch.object(sof, "_fetch_orders_in_range", side_effect=fake_fetch),
			patch.object(sof, "create_order", return_value=MagicMock()) as create_order,
			patch.object(sof, "backfill_display_order_code") as backfill,
			patch.object(sof, "_create_sales_invoices"),
			patch.object(sof, "_log_summary"),
			patch(f"{SOF}.frappe.set_user"),
			patch(f"{SOF}.frappe.get_all", side_effect=fake_get_all),
		):
			summary = sof._run_sync(settings, "2026-04-01", "2026-04-30", client=client)

		self.assertEqual(summary["fetched"], 3)
		self.assertEqual(summary["off_channel"], 1)
		self.assertEqual(summary["created"], 1)
		self.assertEqual(summary["skipped_existing"], 1)
		self.assertEqual(summary["failed"], 0)
		create_order.assert_called_once()  # only the NEW order gets created

		# Skipping an existing order must still fill in its display order no.
		backfill.assert_called_once_with("EXIST", "SO-EXIST")


class TestValidateDateRange(IntegrationTestCase):
	"""Server-side date validation for the whitelisted endpoint (#2)."""

	def test_valid_past_range_normalized_to_dates(self):
		fr, to = sof._validate_date_range("2020-01-01", "2020-01-31")
		self.assertEqual((fr, to), (getdate("2020-01-01"), getdate("2020-01-31")))

	def test_range_over_max_throws(self):
		# Max 31 inclusive calendar days: 1-31 OK; one day longer is rejected.
		sof._validate_date_range("2020-01-01", "2020-01-31")  # 31 inclusive days -> ok
		with self.assertRaises(frappe.ValidationError):
			sof._validate_date_range("2020-01-01", "2020-02-01")  # 32 inclusive days -> throw

	def test_missing_date_throws(self):
		with self.assertRaises(frappe.ValidationError):
			sof._validate_date_range(None, "2020-01-31")
		with self.assertRaises(frappe.ValidationError):
			sof._validate_date_range("2020-01-01", "")

	def test_from_after_to_throws(self):
		with self.assertRaises(frappe.ValidationError):
			sof._validate_date_range("2020-02-01", "2020-01-01")

	def test_future_from_date_throws(self):
		future = add_days(getdate(), 5)
		with self.assertRaises(frappe.ValidationError):
			sof._validate_date_range(future, future)


class TestEnqueueGuards(IntegrationTestCase):
	"""Atomic dedup + lock guards on enqueue_sync_old_orders (#1)."""

	def _enqueue(self, cache_val, enqueue_ret):
		cache = MagicMock()
		cache.get_value.return_value = cache_val
		with (
			patch(f"{SOF}.frappe.only_for"),
			patch(f"{SOF}.frappe.cache", return_value=cache),
			patch(f"{SOF}.frappe.enqueue", return_value=enqueue_ret) as enqueue,
		):
			result = sof.enqueue_sync_old_orders("2020-01-01", "2020-01-31")
		return result, enqueue

	def test_enqueues_with_dedup_and_job_id(self):
		result, enqueue = self._enqueue(cache_val=None, enqueue_ret=MagicMock())
		enqueue.assert_called_once()
		kwargs = enqueue.call_args.kwargs
		self.assertTrue(kwargs["deduplicate"])  # atomic guard is armed
		self.assertEqual(kwargs["job_id"], sof.JOB_ID)
		self.assertEqual(kwargs["queue"], "long")
		self.assertIn("2020-01-01", result)

	def test_blocked_while_running(self):
		# lock held -> friendly throw, never reaches enqueue
		with self.assertRaises(frappe.ValidationError):
			self._enqueue(cache_val=1, enqueue_ret=MagicMock())

	def test_blocked_when_duplicate_job_queued(self):
		# enqueue() returns None (dedup skipped an already-queued job) -> throw
		with self.assertRaises(frappe.ValidationError):
			self._enqueue(cache_val=None, enqueue_ret=None)

	def test_invalid_dates_rejected_before_enqueue(self):
		cache = MagicMock()
		cache.get_value.return_value = None
		with (
			patch(f"{SOF}.frappe.only_for"),
			patch(f"{SOF}.frappe.cache", return_value=cache),
			patch(f"{SOF}.frappe.enqueue") as enqueue,
			self.assertRaises(frappe.ValidationError),
		):
			sof.enqueue_sync_old_orders("2020-02-01", "2020-01-01")  # inverted range
		enqueue.assert_not_called()
