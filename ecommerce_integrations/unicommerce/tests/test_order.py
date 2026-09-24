import json
from collections import defaultdict
from copy import deepcopy

import frappe
from frappe.test_runner import make_test_records

from ecommerce_integrations.unicommerce.constants import (
	CHANNEL_ID_FIELD,
	CHARGE_TAX_HEADS_MAP,
	ORDER_CODE_FIELD,
	ORDER_DISPLAY_CODE_FIELD,
	ORDER_STATUS_FIELD,
	TAX_FIELDS_MAPPING,
)
from ecommerce_integrations.unicommerce.order import (
	_get_facility_code,
	_get_line_items,
	_sync_order_items,
	create_order,
	get_taxes,
)
from ecommerce_integrations.unicommerce.tests.test_client import TestCaseApiClient
from ecommerce_integrations.unicommerce.tests.utils import line_item_with_charges


class TestUnicommerceOrder(TestCaseApiClient):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_test_records("Unicommerce Channel")

	def test_validate_item_list(self):
		order_files = ["order-SO5905", "order-SO5906", "order-SO5907"]
		items_list = [
			{"MC-100", "TITANIUM_WATCH"},
			{
				"MC-100",
			},
			{"MC-100", "TITANIUM_WATCH"},
		]

		for order_file, items in zip(order_files, items_list, strict=True):
			order = self.load_fixture(order_file)["saleOrderDTO"]
			self.assertEqual(items, _sync_order_items(order, client=self.client))

	def test_get_line_items(self):
		so_items = self.load_fixture("order-SO6008-order")["saleOrderItems"]
		items = _get_line_items(so_items)

		expected_item = {
			"item_code": "TITANIUM_WATCH",
			"rate": 312000.0,
			"qty": 1,
			"stock_uom": "Nos",
			"unicommerce_batch_code": None,
			"warehouse": "Stores - WP",
			"unicommerce_order_item_code": "TITANIUM_WATCH-0",
		}

		self.assertEqual(items[0], expected_item)

	def test_get_line_items_multiple(self):
		so_items = self.load_fixture("order-SO5906")["saleOrderDTO"]["saleOrderItems"]
		items = _get_line_items(so_items)

		item_to_qty = defaultdict(int)
		total_price = 0.0

		for item in items:
			item_to_qty[item["item_code"]] += item["qty"]
			total_price += item["rate"] * item["qty"]

		self.assertEqual(item_to_qty["MC-100"], 11)
		self.assertAlmostEqual(total_price, 7028.0)

	def test_get_taxes(self):
		"""Taxes of the same SKU on multiple lines add up."""
		invoice = self.load_fixture("invoice-SDU0010")["invoice"]
		channel_config = frappe.get_doc("Unicommerce Channel", "RAINFOREST")
		line_item = invoice["invoiceItems"][0]

		single_line = get_taxes([line_item], channel_config)
		batch_split = get_taxes([deepcopy(line_item), deepcopy(line_item)], channel_config)

		self.assertEqual(len(batch_split), len(single_line))

		for tax, single_line_tax in zip(batch_split, single_line, strict=True):
			item_wise_tax = json.loads(tax["item_wise_tax_detail"])
			self.assertEqual(len(item_wise_tax), 1)

			(tax_rate, tax_amount), (expected_rate, expected_amount) = (
				next(iter(item_wise_tax.values())),
				next(iter(json.loads(single_line_tax["item_wise_tax_detail"]).values())),
			)

			self.assertEqual(tax_rate, expected_rate)
			self.assertAlmostEqual(tax_amount, 2 * expected_amount)
			self.assertAlmostEqual(tax_amount, tax["tax_amount"])

	def test_get_taxes_with_charges_billed_as_items(self):
		"""Tax on a charge billed as an item moves from the item to the charge item."""
		channel_config = frappe.get_doc("Unicommerce Channel", "RAINFOREST")
		line_item = line_item_with_charges(cod_charge=100.0)
		charge_items = {("cash_on_delivery_charges", 18.0): "COD-CHARGES"}

		taxes = get_taxes([line_item], channel_config, charge_items)
		tax_by_head = {tax["description"]: tax for tax in taxes}

		self.assertNotIn("CASH ON DELIVERY CHARGES", tax_by_head)

		for tax_head in ("CGST", "SGST"):
			tax = tax_by_head[tax_head]
			item_wise_tax = json.loads(tax["item_wise_tax_detail"])

			self.assertEqual(item_wise_tax.pop("COD-CHARGES"), [9.0, 9.0])
			self.assertEqual(next(iter(item_wise_tax.values())), [9.0, 30.51])
			self.assertAlmostEqual(tax["tax_amount"], 39.51)

	def test_get_taxes_keeps_charges_as_tax_rows_by_default(self):
		"""Charges stay tax rows by default."""
		channel_config = frappe.get_doc("Unicommerce Channel", "RAINFOREST")
		line_item = line_item_with_charges(cod_charge=100.0)

		taxes = get_taxes([line_item], channel_config)
		tax_by_head = {tax["description"]: tax for tax in taxes}

		self.assertEqual(tax_by_head["CASH ON DELIVERY CHARGES"]["tax_amount"], 100.0)
		item_wise_tax = json.loads(tax_by_head["CGST"]["item_wise_tax_detail"])
		self.assertAlmostEqual(next(iter(item_wise_tax.values()))[1], 39.51)

	def test_charge_tax_heads_are_known(self):
		"""Charge tax heads exist in TAX_FIELDS_MAPPING."""
		for charge, tax_heads in CHARGE_TAX_HEADS_MAP.items():
			for tax_head in tax_heads:
				self.assertIn(tax_head, TAX_FIELDS_MAPPING, f"{charge} covers unknown tax head {tax_head}")

	def test_charge_options_match_charge_tax_heads(self):
		"""Charge Select options and CHARGE_TAX_HEADS_MAP hold the same charges.

		They are separate copies, so a charge on one side only bills nothing.
		"""
		options = frappe.get_meta("Unicommerce Charge Item").get_field("charge").options.split("\n")

		self.assertEqual(set(options), set(CHARGE_TAX_HEADS_MAP))

	def test_get_facility_code(self):
		line_items = self.load_fixture("order-SO6008-order")["saleOrderItems"]
		facility = _get_facility_code(line_items)

		self.assertEqual(facility, "Test-123")

		bad_line_item = deepcopy(line_items[0])
		bad_line_item["facilityCode"] = "grrr"
		line_items.append(bad_line_item)

		self.assertRaises(frappe.ValidationError, _get_facility_code, line_items)

	def test_create_order(self):
		order = self.load_fixture("order-SO6008-order")

		so = create_order(order, client=self.client)

		customer_name = order["addresses"][0]["name"]
		self.assertTrue(customer_name in so.customer)
		self.assertEqual(so.get(CHANNEL_ID_FIELD), order["channel"])
		self.assertEqual(so.get(ORDER_CODE_FIELD), order["code"])
		self.assertEqual(so.get(ORDER_DISPLAY_CODE_FIELD), order["displayOrderCode"])
		self.assertEqual(so.get(ORDER_STATUS_FIELD), order["status"])

	def test_create_order_multiple_items(self):
		order = self.load_fixture("order-SO5906")["saleOrderDTO"]

		so = create_order(order, client=self.client)

		customer_name = order["addresses"][0]["name"]
		self.assertTrue(customer_name in so.customer)
		self.assertEqual(so.get(CHANNEL_ID_FIELD), order["channel"])
		self.assertEqual(so.get(ORDER_CODE_FIELD), order["code"])
		self.assertEqual(so.get(ORDER_DISPLAY_CODE_FIELD), order["displayOrderCode"])
		self.assertEqual(so.get(ORDER_STATUS_FIELD), order["status"])

		qty = sum(item.qty for item in so.items)
		amount = sum(item.amount for item in so.items)
		self.assertEqual(qty, 11)
		self.assertAlmostEqual(amount, 7028.0)
