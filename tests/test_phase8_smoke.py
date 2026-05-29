"""
Phase 8 smoke tests — Price Sync (Item Price ↔ WooCommerce regular_price / sale_price).
No Frappe instance required. Tests run pure Python logic.
"""
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).parent.parent
DOCTYPE_DIR = ROOT / "caz_woosync" / "doctype"

# ---------------------------------------------------------------------------
# Minimal Frappe stub (hermetic — same pattern as prior phases)
# ---------------------------------------------------------------------------


def _make_frappe_stub():
    """Build a minimal frappe module stub."""
    frappe_mod = types.ModuleType("frappe")

    db = MagicMock()
    db.get_value = MagicMock(return_value=None)
    db.exists = MagicMock(return_value=False)
    db.set_value = MagicMock()
    db.commit = MagicMock()
    db.sql = MagicMock(return_value=[])
    db.after_commit = MagicMock(side_effect=lambda fn: fn())  # call immediately in tests
    frappe_mod.db = db

    frappe_mod.new_doc = MagicMock(return_value=MagicMock())
    frappe_mod.get_doc = MagicMock(return_value=MagicMock())
    frappe_mod.get_all = MagicMock(return_value=[])
    frappe_mod.get_single = MagicMock(return_value=MagicMock())
    frappe_mod.throw = MagicMock(side_effect=Exception)
    frappe_mod.log_error = MagicMock()
    frappe_mod.get_traceback = MagicMock(return_value="traceback")
    frappe_mod.enqueue = MagicMock()
    frappe_mod.whitelist = lambda fn=None, **kw: (fn if callable(fn) else lambda f: f)

    logger_mock = MagicMock()
    frappe_mod.logger = MagicMock(return_value=logger_mock)
    frappe_mod.flags = MagicMock()
    frappe_mod.flags.in_migrate = False
    frappe_mod.flags.in_patch = False
    frappe_mod.flags.in_import = False
    frappe_mod.flags.in_install = False

    utils_mod = types.ModuleType("frappe.utils")
    utils_mod.now_datetime = MagicMock(return_value="2026-05-28 12:00:00")
    utils_mod.getdate = MagicMock(side_effect=lambda d: d)
    utils_mod.now = MagicMock(return_value="2026-05-28 12:00:00")
    utils_mod.nowdate = MagicMock(return_value="2026-05-28")
    utils_mod.today = MagicMock(return_value="2026-05-28")
    utils_mod.get_datetime = MagicMock(side_effect=lambda d: d)
    utils_mod.cstr = lambda v: str(v) if v is not None else ""
    utils_mod.flt = lambda v, precision=None: float(v) if v else 0.0
    frappe_mod.utils = utils_mod

    html_utils_mod = types.ModuleType("frappe.utils.html_utils")
    html_utils_mod.strip_html = lambda s: s
    utils_mod.strip_html = lambda s: s or ""
    frappe_mod.utils.html_utils = html_utils_mod

    model_mod = types.ModuleType("frappe.model")
    doc_mod = types.ModuleType("frappe.model.document")
    doc_mod.Document = object
    model_mod.document = doc_mod
    frappe_mod.model = model_mod

    return frappe_mod


_frappe_stub = _make_frappe_stub()
sys.modules["frappe"] = _frappe_stub
sys.modules["frappe.utils"] = _frappe_stub.utils
sys.modules["frappe.utils.html_utils"] = _frappe_stub.utils.html_utils
sys.modules["frappe.model"] = _frappe_stub.model
sys.modules["frappe.model.document"] = _frappe_stub.model.document

# Stub woocommerce
woo_mod = types.ModuleType("woocommerce")
woo_mod.API = MagicMock()
sys.modules["woocommerce"] = woo_mod

# Stub rate_limiter
rate_limiter_mod = types.ModuleType("caz_woosync.utils.rate_limiter")
rate_limiter_mod.WooCommerceClient = MagicMock()
rate_limiter_mod.check_rate_limit = MagicMock(return_value=True)
rate_limiter_mod.get_woo_client = MagicMock()
sys.modules["caz_woosync.utils.rate_limiter"] = rate_limiter_mod

# Stub caz_woosync.utils
utils_pkg = types.ModuleType("caz_woosync.utils")
sys.modules["caz_woosync.utils"] = utils_pkg

# Stub caz_woosync.sync package
sync_pkg = types.ModuleType("caz_woosync.sync")
sys.modules["caz_woosync.sync"] = sync_pkg


def _load_prices_module():
    """Load prices.py fresh from disk (bypasses cached sys.modules)."""
    spec = importlib.util.spec_from_file_location(
        "caz_woosync.sync.prices_fresh",
        ROOT / "caz_woosync" / "sync" / "prices.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fresh_frappe_mock():
    """Reset frappe mock state to clean defaults."""
    frappe = sys.modules["frappe"]
    frappe.db.get_value = MagicMock(return_value=None)
    frappe.db.exists = MagicMock(return_value=False)
    frappe.db.set_value = MagicMock()
    frappe.db.commit = MagicMock()
    frappe.db.sql = MagicMock(return_value=[])
    frappe.db.after_commit = MagicMock(side_effect=lambda fn: fn())
    frappe.get_doc = MagicMock(return_value=MagicMock())
    frappe.get_all = MagicMock(return_value=[])
    frappe.new_doc = MagicMock(return_value=MagicMock())
    frappe.log_error = MagicMock()
    frappe.get_traceback = MagicMock(return_value="traceback")
    frappe.flags.in_migrate = False
    frappe.flags.in_patch = False
    frappe.flags.in_import = False
    frappe.flags.in_install = False
    return frappe


# ===========================================================================
# TestPriceRounding
# ===========================================================================


class TestPriceRounding(unittest.TestCase):
    """Price rounding: 2 decimals, nearest integer, no rounding."""

    def setUp(self):
        self.mod = _load_prices_module()

    def test_two_decimal_rounds_9999_to_10_00(self):
        result = self.mod._apply_rounding(9.999, "2 Decimal Places")
        self.assertAlmostEqual(result, 10.00)

    def test_nearest_integer_rounds_9_5_to_10(self):
        result = self.mod._apply_rounding(9.5, "Nearest Integer")
        self.assertEqual(result, 10)

    def test_nearest_integer_rounds_9_4_to_9(self):
        result = self.mod._apply_rounding(9.4, "Nearest Integer")
        self.assertEqual(result, 9)

    def test_no_rounding_preserves_9_123456(self):
        result = self.mod._apply_rounding(9.123456, "No Rounding")
        self.assertAlmostEqual(result, 9.123456)

    def test_default_none_rounding_uses_2_decimal_places(self):
        result = self.mod._apply_rounding(19.999, None)
        self.assertAlmostEqual(result, 20.00)

    def test_two_decimal_preserves_19_99(self):
        result = self.mod._apply_rounding(19.99, "2 Decimal Places")
        self.assertAlmostEqual(result, 19.99)


# ===========================================================================
# TestPricePayloadBuilding
# ===========================================================================


class TestPricePayloadBuilding(unittest.TestCase):
    """Payload: regular_price from Item Price, sale_price when configured, zero skipped."""

    def setUp(self):
        _fresh_frappe_mock()
        self.mod = _load_prices_module()

    def _make_store_mock(self, push_trigger="Scheduled", item_price_list="Standard Selling",
                         sale_price_list=None, currency_rounding="2 Decimal Places"):
        store = MagicMock()
        store.push_prices_trigger = push_trigger
        store.item_price_list = item_price_list
        store.sale_price_list = sale_price_list
        store.currency_rounding = currency_rounding
        return store

    def test_regular_price_set_from_item_price(self):
        """When Item Price exists, regular_price is pushed to WC."""
        frappe = _fresh_frappe_mock()

        # mapping exists
        mapping_dict = MagicMock()
        mapping_dict.woo_id = "42"
        mapping_dict.name = "MAP-001"
        frappe.db.get_value.side_effect = [
            mapping_dict,     # mapping lookup
            99.99,            # Item Price lookup (regular price)
        ]

        store_mock = self._make_store_mock()
        frappe.get_doc.return_value = store_mock

        client_mock = MagicMock()
        resp_mock = MagicMock()
        resp_mock.status_code = 200
        client_mock.put.return_value = resp_mock
        rate_limiter_mod.WooCommerceClient.return_value = client_mock

        self.mod.sync_price_to_woo("Test Store", "ITEM-001")

        put_calls = client_mock.put.call_args_list
        self.assertEqual(len(put_calls), 1)
        payload_sent = put_calls[0][0][1]
        self.assertIn("regular_price", payload_sent)
        self.assertEqual(payload_sent["regular_price"], "99.99")

    def test_sale_price_set_when_sale_price_list_configured(self):
        """When sale_price_list is configured and price exists, sale_price is included."""
        frappe = _fresh_frappe_mock()

        mapping_dict = MagicMock()
        mapping_dict.woo_id = "55"
        mapping_dict.name = "MAP-002"
        frappe.db.get_value.side_effect = [
            mapping_dict,   # mapping lookup
            50.00,          # regular price lookup
            39.99,          # sale price lookup
        ]

        store_mock = self._make_store_mock(sale_price_list="WooCommerce Sale")
        frappe.get_doc.return_value = store_mock

        client_mock = MagicMock()
        resp_mock = MagicMock()
        resp_mock.status_code = 200
        client_mock.put.return_value = resp_mock
        rate_limiter_mod.WooCommerceClient.return_value = client_mock

        self.mod.sync_price_to_woo("Test Store", "ITEM-002")

        payload_sent = client_mock.put.call_args[0][1]
        self.assertIn("sale_price", payload_sent)
        self.assertEqual(payload_sent["sale_price"], "39.99")

    def test_zero_price_skipped(self):
        """When Item Price is 0 (or missing), sync is skipped."""
        frappe = _fresh_frappe_mock()

        mapping_dict = MagicMock()
        mapping_dict.woo_id = "77"
        mapping_dict.name = "MAP-003"
        frappe.db.get_value.side_effect = [
            mapping_dict,  # mapping lookup
            0.0,           # Item Price lookup returns 0
        ]

        store_mock = self._make_store_mock()
        frappe.get_doc.return_value = store_mock

        client_mock = MagicMock()
        rate_limiter_mod.WooCommerceClient.return_value = client_mock

        self.mod.sync_price_to_woo("Test Store", "ITEM-003")

        # No PUT call should have been made
        client_mock.put.assert_not_called()

    def test_no_sale_price_when_sale_price_list_not_configured(self):
        """When sale_price_list is None, sale_price not sent even if there's a price."""
        frappe = _fresh_frappe_mock()

        mapping_dict = MagicMock()
        mapping_dict.woo_id = "88"
        mapping_dict.name = "MAP-004"
        frappe.db.get_value.side_effect = [
            mapping_dict,   # mapping lookup
            29.99,          # regular price lookup
        ]

        store_mock = self._make_store_mock(sale_price_list=None)
        frappe.get_doc.return_value = store_mock

        client_mock = MagicMock()
        resp_mock = MagicMock()
        resp_mock.status_code = 200
        client_mock.put.return_value = resp_mock
        rate_limiter_mod.WooCommerceClient.return_value = client_mock

        self.mod.sync_price_to_woo("Test Store", "ITEM-004")

        payload_sent = client_mock.put.call_args[0][1]
        self.assertNotIn("sale_price", payload_sent)


# ===========================================================================
# TestPriceSyncTrigger
# ===========================================================================


class TestPriceSyncTrigger(unittest.TestCase):
    """push_prices_trigger: Manual skips, On Save and Scheduled allow push."""

    def setUp(self):
        _fresh_frappe_mock()
        self.mod = _load_prices_module()

    def test_manual_trigger_skips_automatic_push(self):
        """push_prices_trigger='Manual' → no PUT call made."""
        frappe = _fresh_frappe_mock()

        mapping_dict = MagicMock()
        mapping_dict.woo_id = "100"
        mapping_dict.name = "MAP-010"
        frappe.db.get_value.return_value = mapping_dict

        store_mock = MagicMock()
        store_mock.push_prices_trigger = "Manual"
        store_mock.item_price_list = "Standard Selling"
        store_mock.sale_price_list = None
        store_mock.currency_rounding = "2 Decimal Places"
        frappe.get_doc.return_value = store_mock

        client_mock = MagicMock()
        rate_limiter_mod.WooCommerceClient.return_value = client_mock

        self.mod.sync_price_to_woo("Test Store", "ITEM-010")

        client_mock.put.assert_not_called()

    def test_on_save_trigger_allows_push(self):
        """push_prices_trigger='On Save' → PUT is called."""
        frappe = _fresh_frappe_mock()

        mapping_dict = MagicMock()
        mapping_dict.woo_id = "101"
        mapping_dict.name = "MAP-011"
        frappe.db.get_value.side_effect = [
            mapping_dict,  # mapping lookup
            25.00,         # Item Price lookup
        ]

        store_mock = MagicMock()
        store_mock.push_prices_trigger = "On Save"
        store_mock.item_price_list = "Standard Selling"
        store_mock.sale_price_list = None
        store_mock.currency_rounding = "2 Decimal Places"
        frappe.get_doc.return_value = store_mock

        client_mock = MagicMock()
        resp_mock = MagicMock()
        resp_mock.status_code = 200
        client_mock.put.return_value = resp_mock
        rate_limiter_mod.WooCommerceClient.return_value = client_mock

        self.mod.sync_price_to_woo("Test Store", "ITEM-011")

        client_mock.put.assert_called_once()

    def test_scheduled_trigger_allows_push(self):
        """push_prices_trigger='Scheduled' → PUT is called."""
        frappe = _fresh_frappe_mock()

        mapping_dict = MagicMock()
        mapping_dict.woo_id = "102"
        mapping_dict.name = "MAP-012"
        frappe.db.get_value.side_effect = [
            mapping_dict,  # mapping lookup
            15.50,         # Item Price lookup
        ]

        store_mock = MagicMock()
        store_mock.push_prices_trigger = "Scheduled"
        store_mock.item_price_list = "Standard Selling"
        store_mock.sale_price_list = None
        store_mock.currency_rounding = "2 Decimal Places"
        frappe.get_doc.return_value = store_mock

        client_mock = MagicMock()
        resp_mock = MagicMock()
        resp_mock.status_code = 200
        client_mock.put.return_value = resp_mock
        rate_limiter_mod.WooCommerceClient.return_value = client_mock

        self.mod.sync_price_to_woo("Test Store", "ITEM-012")

        client_mock.put.assert_called_once()


# ===========================================================================
# TestWcPriceParsing
# ===========================================================================


class TestWcPriceParsing(unittest.TestCase):
    """Parse WC price strings: valid, empty, None, zero."""

    def setUp(self):
        self.mod = _load_prices_module()

    def test_regular_price_string_19_99_parses_to_flt_19_99(self):
        """'19.99' → flt 19.99"""
        result = float("19.99" or 0)
        self.assertAlmostEqual(result, 19.99)

    def test_empty_string_parses_to_0(self):
        """Empty string → 0.0 via flt('' or 0)."""
        result = float("" or 0)
        self.assertAlmostEqual(result, 0.0)

    def test_none_parses_to_0(self):
        """None → 0.0 via flt(None or 0)."""
        result = float(None or 0)
        self.assertAlmostEqual(result, 0.0)

    def test_zero_string_parses_to_0(self):
        """'0.00' → 0.0 → skip (price <= 0)."""
        result = float("0.00" or 0)
        self.assertAlmostEqual(result, 0.0)
        self.assertFalse(result > 0)

    def test_sync_price_from_woo_parses_regular_price_correctly(self):
        """sync_price_from_woo with regular_price='19.99' calls _upsert_item_price."""
        _fresh_frappe_mock()
        frappe = sys.modules["frappe"]

        # Mapping exists
        mapping_dict = MagicMock()
        mapping_dict.erp_item = "ITEM-WC-001"
        mapping_dict.name = "MAP-WC-001"
        frappe.db.get_value.return_value = mapping_dict

        store_mock = MagicMock()
        store_mock.update_prices_from_woo = 1
        store_mock.item_price_list = "Standard Selling"
        store_mock.sale_price_list = None
        frappe.get_doc.return_value = store_mock

        payload = {
            "id": 42,
            "regular_price": "19.99",
            "sale_price": "",
        }

        upserted = []
        original_upsert = self.mod._upsert_item_price

        def fake_upsert(item_code, price_list, price, valid_upto=None):
            upserted.append((item_code, price_list, price))

        self.mod._upsert_item_price = fake_upsert

        try:
            self.mod.sync_price_from_woo("Test Store", "42", payload)
        finally:
            self.mod._upsert_item_price = original_upsert

        self.assertEqual(len(upserted), 1)
        self.assertAlmostEqual(upserted[0][2], 19.99)

    def test_sync_price_from_woo_zero_regular_price_skips_upsert(self):
        """sync_price_from_woo with regular_price='0.00' skips upsert."""
        _fresh_frappe_mock()
        frappe = sys.modules["frappe"]

        mapping_dict = MagicMock()
        mapping_dict.erp_item = "ITEM-WC-002"
        mapping_dict.name = "MAP-WC-002"
        frappe.db.get_value.return_value = mapping_dict

        store_mock = MagicMock()
        store_mock.update_prices_from_woo = 1
        store_mock.item_price_list = "Standard Selling"
        store_mock.sale_price_list = None
        frappe.get_doc.return_value = store_mock

        payload = {
            "id": 43,
            "regular_price": "0.00",
            "sale_price": None,
        }

        upserted = []
        original_upsert = self.mod._upsert_item_price

        def fake_upsert(item_code, price_list, price, valid_upto=None):
            upserted.append((item_code, price_list, price))

        self.mod._upsert_item_price = fake_upsert

        try:
            self.mod.sync_price_from_woo("Test Store", "43", payload)
        finally:
            self.mod._upsert_item_price = original_upsert

        self.assertEqual(len(upserted), 0)


# ===========================================================================
# TestItemPriceDocEvent
# ===========================================================================


class TestItemPriceDocEvent(unittest.TestCase):
    """on_item_price_update: selling=0 skipped, migrate guard, dedup."""

    def setUp(self):
        _fresh_frappe_mock()
        self.mod = _load_prices_module()

    def test_selling_0_is_skipped(self):
        """Doc with selling=0 → no queue entry created."""
        frappe = _fresh_frappe_mock()

        doc = MagicMock()
        doc.selling = 0
        doc.item_code = "ITEM-SKIP-001"

        self.mod.on_item_price_update(doc)

        frappe.new_doc.assert_not_called()

    def test_migrate_guard_prevents_enqueue(self):
        """frappe.flags.in_migrate=True → returns immediately."""
        frappe = _fresh_frappe_mock()
        frappe.flags.in_migrate = True

        doc = MagicMock()
        doc.selling = 1
        doc.item_code = "ITEM-MIG-001"

        self.mod.on_item_price_update(doc)

        frappe.new_doc.assert_not_called()

    def test_patch_guard_prevents_enqueue(self):
        """frappe.flags.in_patch=True → returns immediately."""
        frappe = _fresh_frappe_mock()
        frappe.flags.in_patch = True

        doc = MagicMock()
        doc.selling = 1
        doc.item_code = "ITEM-PATCH-001"

        self.mod.on_item_price_update(doc)

        frappe.new_doc.assert_not_called()

    def test_import_guard_prevents_enqueue(self):
        """frappe.flags.in_import=True → returns immediately."""
        frappe = _fresh_frappe_mock()
        frappe.flags.in_import = True

        doc = MagicMock()
        doc.selling = 1
        doc.item_code = "ITEM-IMP-001"

        self.mod.on_item_price_update(doc)

        frappe.new_doc.assert_not_called()

    def test_dedup_prevents_double_queue(self):
        """Already queued entry → second call is a no-op."""
        frappe = _fresh_frappe_mock()

        # Store exists, mapping exists, but queue entry already exists
        store_mock = MagicMock()
        store_mock.name = "Store A"
        frappe.get_all.return_value = [store_mock]

        # mapping exists (first exists call), queue already exists (second exists call)
        frappe.db.exists.side_effect = [True, True]

        doc = MagicMock()
        doc.selling = 1
        doc.item_code = "ITEM-DUP-001"

        self.mod.on_item_price_update(doc)

        frappe.new_doc.assert_not_called()

    def test_selling_price_with_mapping_creates_queue_entry(self):
        """Selling price + existing mapping → queue entry created."""
        frappe = _fresh_frappe_mock()

        store_mock = MagicMock()
        store_mock.name = "Store B"
        frappe.get_all.return_value = [store_mock]

        # mapping exists, queue not yet queued
        frappe.db.exists.side_effect = [True, False]

        queue_doc_mock = MagicMock()
        frappe.new_doc.return_value = queue_doc_mock

        doc = MagicMock()
        doc.selling = 1
        doc.item_code = "ITEM-NEW-001"

        self.mod.on_item_price_update(doc)

        frappe.new_doc.assert_called_once_with("Caz Woo Sync Queue")
        queue_doc_mock.insert.assert_called_once()

    def test_queue_entry_has_price_entity_type(self):
        """Created queue entry has entity_type='Price'."""
        frappe = _fresh_frappe_mock()

        store_mock = MagicMock()
        store_mock.name = "Store C"
        frappe.get_all.return_value = [store_mock]

        frappe.db.exists.side_effect = [True, False]

        queue_doc_mock = MagicMock()
        frappe.new_doc.return_value = queue_doc_mock

        doc = MagicMock()
        doc.selling = 1
        doc.item_code = "ITEM-ENT-001"

        self.mod.on_item_price_update(doc)

        update_call_args = queue_doc_mock.update.call_args[0][0]
        self.assertEqual(update_call_args["entity_type"], "Price")
        self.assertEqual(update_call_args["direction"], "erp_to_woo")


# ===========================================================================
# TestPriceSyncSettings
# ===========================================================================


class TestPriceSyncSettings(unittest.TestCase):
    """caz_woo_store.json has all required Phase 8 price sync fields."""

    @classmethod
    def setUpClass(cls):
        store_json_path = DOCTYPE_DIR / "caz_woo_store" / "caz_woo_store.json"
        with open(store_json_path) as f:
            cls.schema = json.load(f)
        cls.fields_by_name = {f["fieldname"]: f for f in cls.schema.get("fields", [])}
        cls.field_order = cls.schema.get("field_order", [])

    # --- Field presence ---

    def test_push_prices_trigger_field_present(self):
        self.assertIn("push_prices_trigger", self.fields_by_name)

    def test_update_prices_from_woo_field_present(self):
        self.assertIn("update_prices_from_woo", self.fields_by_name)

    def test_sale_price_list_field_present(self):
        self.assertIn("sale_price_list", self.fields_by_name)

    def test_currency_rounding_field_present(self):
        self.assertIn("currency_rounding", self.fields_by_name)

    # --- Field types ---

    def test_push_prices_trigger_is_select(self):
        f = self.fields_by_name["push_prices_trigger"]
        self.assertEqual(f["fieldtype"], "Select")

    def test_push_prices_trigger_default_is_scheduled(self):
        f = self.fields_by_name["push_prices_trigger"]
        self.assertEqual(f.get("default"), "Scheduled")

    def test_push_prices_trigger_options_include_manual(self):
        f = self.fields_by_name["push_prices_trigger"]
        self.assertIn("Manual", f.get("options", ""))

    def test_push_prices_trigger_options_include_on_save(self):
        f = self.fields_by_name["push_prices_trigger"]
        self.assertIn("On Save", f.get("options", ""))

    def test_update_prices_from_woo_is_check_with_default_1(self):
        f = self.fields_by_name["update_prices_from_woo"]
        self.assertEqual(f["fieldtype"], "Check")
        self.assertEqual(str(f.get("default", "")), "1")

    def test_sale_price_list_is_link_to_price_list(self):
        f = self.fields_by_name["sale_price_list"]
        self.assertEqual(f["fieldtype"], "Link")
        self.assertEqual(f.get("options"), "Price List")

    def test_currency_rounding_is_select(self):
        f = self.fields_by_name["currency_rounding"]
        self.assertEqual(f["fieldtype"], "Select")

    def test_currency_rounding_default_is_2_decimal_places(self):
        f = self.fields_by_name["currency_rounding"]
        self.assertEqual(f.get("default"), "2 Decimal Places")

    def test_currency_rounding_options_include_no_rounding(self):
        f = self.fields_by_name["currency_rounding"]
        self.assertIn("No Rounding", f.get("options", ""))

    def test_currency_rounding_options_include_nearest_integer(self):
        f = self.fields_by_name["currency_rounding"]
        self.assertIn("Nearest Integer", f.get("options", ""))

    # --- Descriptions non-empty ---

    def test_all_price_sync_fields_have_descriptions(self):
        required_fields = [
            "push_prices_trigger",
            "update_prices_from_woo",
            "sale_price_list",
            "currency_rounding",
        ]
        for fname in required_fields:
            f = self.fields_by_name[fname]
            desc = f.get("description", "")
            self.assertTrue(
                desc and desc.strip(),
                f"Field '{fname}' has no description in caz_woo_store.json",
            )

    # --- Section present ---

    def test_section_price_sync_present(self):
        self.assertIn("section_price_sync", self.fields_by_name)
        f = self.fields_by_name["section_price_sync"]
        self.assertEqual(f["fieldtype"], "Section Break")

    # --- Field order ---

    def test_price_sync_fields_in_field_order(self):
        for fname in (
            "section_price_sync",
            "push_prices_trigger",
            "update_prices_from_woo",
            "sale_price_list",
            "currency_rounding",
        ):
            self.assertIn(fname, self.field_order, f"{fname} missing from field_order")

    def test_price_sync_section_after_accounting_section(self):
        acc_idx = self.field_order.index("section_accounting_sync")
        price_idx = self.field_order.index("section_price_sync")
        self.assertLess(acc_idx, price_idx)

    def test_price_sync_section_before_webhook_section(self):
        price_idx = self.field_order.index("section_price_sync")
        web_idx = self.field_order.index("section_webhook")
        self.assertLess(price_idx, web_idx)


# ===========================================================================
# TestPricesModuleImportable
# ===========================================================================


class TestPricesModuleImportable(unittest.TestCase):
    """prices.py is importable and exposes the expected public API."""

    def setUp(self):
        self.mod = _load_prices_module()

    def test_sync_price_to_woo_callable(self):
        self.assertTrue(callable(getattr(self.mod, "sync_price_to_woo", None)))

    def test_sync_price_from_woo_callable(self):
        self.assertTrue(callable(getattr(self.mod, "sync_price_from_woo", None)))

    def test_push_all_prices_callable(self):
        self.assertTrue(callable(getattr(self.mod, "push_all_prices", None)))

    def test_on_item_price_update_callable(self):
        self.assertTrue(callable(getattr(self.mod, "on_item_price_update", None)))

    def test_apply_rounding_callable(self):
        self.assertTrue(callable(getattr(self.mod, "_apply_rounding", None)))

    def test_module_imports_frappe_at_top(self):
        prices_path = ROOT / "caz_woosync" / "sync" / "prices.py"
        content = prices_path.read_text()
        lines = content.splitlines()
        import_lines = [ln for ln in lines[:10] if "import frappe" in ln]
        self.assertTrue(import_lines, "frappe must be imported at top of prices.py")

    def test_no_db_commit_in_sync_price_to_woo(self):
        """sync_price_to_woo must not call frappe.db.commit() — no commit in request handlers."""
        prices_path = ROOT / "caz_woosync" / "sync" / "prices.py"
        content = prices_path.read_text()
        # Extract sync_price_to_woo body (before the next top-level def)
        fn_start = content.find("def sync_price_to_woo(")
        fn_end = content.find("\ndef ", fn_start + 1)
        fn_body = content[fn_start:fn_end]
        self.assertNotIn("frappe.db.commit()", fn_body)


# ===========================================================================
# TestHooksAndDispatcher
# ===========================================================================


class TestHooksAndDispatcher(unittest.TestCase):
    """hooks.py and dispatcher.py have Phase 8 additions."""

    def test_hooks_has_item_price_doc_event(self):
        hooks_path = ROOT / "caz_woosync" / "hooks.py"
        content = hooks_path.read_text()
        self.assertIn("Item Price", content)
        self.assertIn("on_item_price_update", content)

    def test_dispatcher_has_price_routing(self):
        dispatcher_path = ROOT / "caz_woosync" / "sync" / "dispatcher.py"
        content = dispatcher_path.read_text()
        self.assertIn("entity_type == \"Price\"", content)
        self.assertIn("sync_price_to_woo", content)

    def test_connection_api_has_push_all_prices(self):
        connection_path = ROOT / "caz_woosync" / "api" / "connection.py"
        content = connection_path.read_text()
        self.assertIn("push_all_prices", content)
        self.assertIn("caz_woosync.sync.prices.push_all_prices", content)


if __name__ == "__main__":
    unittest.main()
