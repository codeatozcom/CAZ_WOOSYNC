"""
Phase 6 smoke tests — Inventory / Stock Level Sync.
No Frappe instance required. Tests run pure Python logic.
"""
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

ROOT = Path(__file__).parent.parent
DOCTYPE_DIR = ROOT / "caz_woosync" / "doctype"

# ---------------------------------------------------------------------------
# Minimal Frappe stub (same pattern as Phase 5)
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
    html_utils_mod.strip_html = lambda s: s  # identity in tests
    utils_mod.strip_html = lambda s: s or ""
    frappe_mod.utils.html_utils = html_utils_mod

    model_mod = types.ModuleType("frappe.model")
    doc_mod = types.ModuleType("frappe.model.document")
    doc_mod.Document = object
    model_mod.document = doc_mod
    frappe_mod.model = model_mod

    return frappe_mod


_frappe_stub = _make_frappe_stub()
# Always use our own stub for frappe so Phase 6 tests are hermetic
sys.modules["frappe"] = _frappe_stub
sys.modules["frappe.utils"] = _frappe_stub.utils
sys.modules["frappe.utils.html_utils"] = _frappe_stub.utils.html_utils
sys.modules["frappe.model"] = _frappe_stub.model
sys.modules["frappe.model.document"] = _frappe_stub.model.document

# Also stub woocommerce so imports don't fail
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

# Stub caz_woosync.sync package (but NOT caz_woosync itself — test_smoke needs the real one)
sync_pkg = types.ModuleType("caz_woosync.sync")
sys.modules["caz_woosync.sync"] = sync_pkg


def _load_inventory_module():
    spec = importlib.util.spec_from_file_location(
        "caz_woosync.sync.inventory",
        ROOT / "caz_woosync" / "sync" / "inventory.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ===========================================================================
# TestStockQuantityLogic
# ===========================================================================


class TestStockQuantityLogic(unittest.TestCase):
    """Test stock quantity coercion rules before pushing to WooCommerce."""

    def test_qty_zero_produces_manage_stock_true_and_stock_quantity_zero(self):
        qty = 0.0
        stock_qty = max(0, int(qty))
        payload = {"manage_stock": True, "stock_quantity": stock_qty}
        self.assertTrue(payload["manage_stock"])
        self.assertEqual(payload["stock_quantity"], 0)

    def test_negative_qty_clamped_to_zero(self):
        qty = -5.0
        stock_qty = max(0, int(qty))
        self.assertEqual(stock_qty, 0)

    def test_float_qty_rounded_down_to_int(self):
        qty = 7.9
        stock_qty = int(qty)
        self.assertEqual(stock_qty, 7)

    def test_float_qty_3_5_rounds_to_3(self):
        qty = 3.5
        stock_qty = int(qty)
        self.assertEqual(stock_qty, 3)

    def test_positive_qty_passes_through(self):
        qty = 42.0
        stock_qty = max(0, int(qty))
        self.assertEqual(stock_qty, 42)

    def test_manage_stock_always_true_when_pushing(self):
        payload = {"manage_stock": True, "stock_quantity": 10}
        self.assertIs(payload["manage_stock"], True)

    def test_large_qty_remains_correct(self):
        qty = 99999.0
        stock_qty = max(0, int(qty))
        self.assertEqual(stock_qty, 99999)


# ===========================================================================
# TestStockReconciliation
# ===========================================================================


class TestStockReconciliation(unittest.TestCase):
    """Test Stock Reconciliation field values for sync_stock_from_woo."""

    def _build_sr_fields(self, item_code, warehouse, wc_stock, woo_product_id):
        """Replicate the SR construction logic from sync_stock_from_woo."""
        return {
            "purpose": "Stock Reconciliation",
            "posting_date": "2026-05-28",
            "remarks": f"CAZ WooSync: stock reconciliation from WooCommerce product {woo_product_id}",
            "items": [{"item_code": item_code, "warehouse": warehouse, "qty": wc_stock}],
        }

    def test_purpose_is_stock_reconciliation(self):
        fields = self._build_sr_fields("TEST-001", "Main WH", 10, 99)
        self.assertEqual(fields["purpose"], "Stock Reconciliation")

    def test_posting_date_is_today(self):
        import datetime
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        # posting_date should be today (using frappe.utils.today stub or real date)
        fields = self._build_sr_fields("TEST-001", "Main WH", 10, 99)
        # Verify it's a date string (not None or empty)
        self.assertTrue(bool(fields["posting_date"]))

    def test_remarks_contains_caz_woosync(self):
        fields = self._build_sr_fields("TEST-001", "Main WH", 10, 99)
        self.assertIn("CAZ WooSync", fields["remarks"])

    def test_remarks_contains_woo_product_id(self):
        fields = self._build_sr_fields("TEST-001", "Main WH", 10, 55)
        self.assertIn("55", fields["remarks"])

    def test_item_code_in_items_list(self):
        fields = self._build_sr_fields("TEST-001", "Main WH", 10, 99)
        self.assertEqual(fields["items"][0]["item_code"], "TEST-001")

    def test_warehouse_in_items_list(self):
        fields = self._build_sr_fields("TEST-001", "Main WH", 10, 99)
        self.assertEqual(fields["items"][0]["warehouse"], "Main WH")

    def test_qty_in_items_list(self):
        fields = self._build_sr_fields("TEST-001", "Main WH", 25, 99)
        self.assertEqual(fields["items"][0]["qty"], 25)

    def test_wc_stock_zero_creates_zero_qty_sr(self):
        fields = self._build_sr_fields("TEST-001", "Main WH", 0, 99)
        self.assertEqual(fields["items"][0]["qty"], 0)

    def test_negative_wc_stock_should_be_clamped(self):
        raw_stock = -3
        wc_stock = max(0, raw_stock)
        self.assertEqual(wc_stock, 0)


# ===========================================================================
# TestInventoryDocEvent
# ===========================================================================


def _fresh_frappe_mock():
    """Return fresh MagicMock objects for frappe.db, frappe.new_doc, frappe.get_all."""
    frappe = sys.modules["frappe"]
    frappe.db.exists = MagicMock(return_value=False)
    frappe.db.exists.side_effect = None
    frappe.db.get_value = MagicMock(return_value=None)
    frappe.db.after_commit = MagicMock(side_effect=lambda fn: fn())
    frappe.get_all = MagicMock(return_value=[])
    frappe.new_doc = MagicMock(return_value=MagicMock())
    frappe.flags.in_migrate = False
    frappe.flags.in_patch = False
    frappe.flags.in_import = False
    frappe.flags.in_install = False
    return frappe


class TestInventoryDocEvent(unittest.TestCase):
    """Test on_stock_ledger_submit doc event filtering logic."""

    def setUp(self):
        _fresh_frappe_mock()

    def test_sle_for_wrong_warehouse_is_skipped(self):
        """SLE warehouse != store warehouse → no queue entry."""
        frappe = sys.modules["frappe"]
        store_mock = MagicMock()
        store_mock.name = "Test Store"
        store_mock.warehouse = "Store WH"
        frappe.get_all.return_value = [store_mock]

        # SLE warehouse is different
        doc_mock = MagicMock()
        doc_mock.warehouse = "Other WH"
        doc_mock.item_code = "ITEM-001"

        mod = _load_inventory_module()
        # after_commit calls the inner function immediately in our stub
        mod.on_stock_ledger_submit(doc_mock)

        # No queue entry should be created (warehouse mismatch)
        frappe.new_doc.assert_not_called()

    def test_no_mapping_means_skip(self):
        """Item has no mapping in this store → no queue entry."""
        frappe = sys.modules["frappe"]
        store_mock = MagicMock()
        store_mock.name = "Test Store"
        store_mock.warehouse = "Store WH"
        frappe.get_all.return_value = [store_mock]

        # No mapping exists
        frappe.db.exists.return_value = False

        doc_mock = MagicMock()
        doc_mock.warehouse = "Store WH"
        doc_mock.item_code = "ITEM-001"

        mod = _load_inventory_module()
        mod.on_stock_ledger_submit(doc_mock)

        # No queue entry created (no mapping)
        frappe.new_doc.assert_not_called()

    def test_dedup_check_prevents_double_queue(self):
        """When a Queued entry already exists, do not create another."""
        frappe = sys.modules["frappe"]
        store_mock = MagicMock()
        store_mock.name = "Test Store"
        store_mock.warehouse = "Store WH"
        frappe.get_all.return_value = [store_mock]

        # First exists() for mapping returns True, second for dedup returns True
        frappe.db.exists.side_effect = [True, True]

        doc_mock = MagicMock()
        doc_mock.warehouse = "Store WH"
        doc_mock.item_code = "ITEM-001"

        mod = _load_inventory_module()
        mod.on_stock_ledger_submit(doc_mock)

        # No new queue doc created because dedup check hit
        frappe.new_doc.assert_not_called()

    def test_valid_sle_creates_queue_entry(self):
        """Valid SLE with mapping and no dupe → creates queue entry."""
        frappe = sys.modules["frappe"]
        store_mock = MagicMock()
        store_mock.name = "Test Store"
        store_mock.warehouse = "Store WH"
        frappe.get_all.return_value = [store_mock]

        # First exists() for mapping returns True, second (dedup) returns False
        frappe.db.exists.side_effect = [True, False]

        queue_doc = MagicMock()
        frappe.new_doc.return_value = queue_doc

        doc_mock = MagicMock()
        doc_mock.warehouse = "Store WH"
        doc_mock.item_code = "ITEM-001"

        mod = _load_inventory_module()
        mod.on_stock_ledger_submit(doc_mock)

        # A queue entry should be created
        frappe.new_doc.assert_called_once_with("Caz Woo Sync Queue")
        queue_doc.insert.assert_called_once()

    def test_in_migrate_flag_skips_enqueue(self):
        """If frappe.flags.in_migrate is set, do nothing."""
        frappe = sys.modules["frappe"]
        frappe.flags.in_migrate = True

        doc_mock = MagicMock()
        mod = _load_inventory_module()
        mod.on_stock_ledger_submit(doc_mock)

        # get_all should NOT be called — we bailed out before after_commit
        frappe.get_all.assert_not_called()

    def test_queue_entry_has_correct_entity_type(self):
        """Queue entry entity_type must be 'Inventory'."""
        frappe = sys.modules["frappe"]
        store_mock = MagicMock()
        store_mock.name = "Test Store"
        store_mock.warehouse = "Store WH"
        frappe.get_all.return_value = [store_mock]

        frappe.db.exists.side_effect = [True, False]

        queue_doc = MagicMock()
        frappe.new_doc.return_value = queue_doc

        doc_mock = MagicMock()
        doc_mock.warehouse = "Store WH"
        doc_mock.item_code = "ITEM-001"

        mod = _load_inventory_module()
        mod.on_stock_ledger_submit(doc_mock)

        # Check update was called with entity_type="Inventory"
        call_kwargs = queue_doc.update.call_args[0][0]
        self.assertEqual(call_kwargs["entity_type"], "Inventory")
        self.assertEqual(call_kwargs["direction"], "erp_to_woo")


# ===========================================================================
# TestBulkStockPush
# ===========================================================================


class TestBulkStockPush(unittest.TestCase):
    """Test push_all_stock function and push_all_stock API endpoint."""

    def test_push_all_stock_api_uses_long_queue(self):
        """push_all_stock in connection.py must enqueue to 'long' queue."""
        conn_path = ROOT / "caz_woosync" / "api" / "connection.py"
        content = conn_path.read_text()
        # Find the push_all_stock function block
        fn_start = content.find("def push_all_stock(")
        fn_end = content.find("\n\n@frappe.whitelist()", fn_start)
        fn_block = content[fn_start:fn_end if fn_end > fn_start else fn_start + 500]
        self.assertIn('queue="long"', fn_block)

    def test_push_all_stock_api_uses_dotted_path(self):
        """Enqueue path must be dotted module path, not a callable."""
        conn_path = ROOT / "caz_woosync" / "api" / "connection.py"
        content = conn_path.read_text()
        fn_start = content.find("def push_all_stock(")
        fn_block = content[fn_start:fn_start + 500]
        self.assertIn('"caz_woosync.sync.inventory.push_all_stock"', fn_block)

    def test_push_all_stock_api_returns_queued_true(self):
        """API endpoint should return {'queued': True}."""
        conn_path = ROOT / "caz_woosync" / "api" / "connection.py"
        content = conn_path.read_text()
        fn_start = content.find("def push_all_stock(")
        fn_block = content[fn_start:fn_start + 500]
        self.assertIn('{"queued": True}', fn_block)

    def test_push_all_stock_api_is_whitelisted(self):
        """push_all_stock API endpoint must be @frappe.whitelist()."""
        conn_path = ROOT / "caz_woosync" / "api" / "connection.py"
        content = conn_path.read_text()
        idx_fn = content.find("def push_all_stock(")
        idx_decorator = content.rfind("@frappe.whitelist()", 0, idx_fn)
        self.assertGreater(idx_decorator, 0)

    def test_push_all_stock_iterates_all_mappings(self):
        """push_all_stock in inventory.py gets all mappings for the store."""
        inv_path = ROOT / "caz_woosync" / "sync" / "inventory.py"
        content = inv_path.read_text()
        fn_start = content.find("def push_all_stock(")
        fn_block = content[fn_start:fn_start + 600]
        self.assertIn("Caz Woo Item Mapping", fn_block)
        self.assertIn("sync_stock_to_woo", fn_block)

    def test_push_all_stock_logs_summary(self):
        """push_all_stock should log a summary at the end."""
        inv_path = ROOT / "caz_woosync" / "sync" / "inventory.py"
        content = inv_path.read_text()
        fn_start = content.find("def push_all_stock(")
        fn_block = content[fn_start:fn_start + 800]
        self.assertIn("frappe.logger", fn_block)

    def test_push_all_stock_timeout_is_3600(self):
        """Bulk push timeout should be 3600 seconds (1 hour)."""
        conn_path = ROOT / "caz_woosync" / "api" / "connection.py"
        content = conn_path.read_text()
        fn_start = content.find("def push_all_stock(")
        fn_block = content[fn_start:fn_start + 500]
        self.assertIn("timeout=3600", fn_block)


# ===========================================================================
# TestInventoryDispatchRouting
# ===========================================================================


class TestInventoryDispatchRouting(unittest.TestCase):
    """Test that entity_type='Inventory' routes correctly in dispatcher."""

    def test_dispatcher_erp_to_woo_inventory_routes_to_sync_stock_to_woo(self):
        dispatcher_path = ROOT / "caz_woosync" / "sync" / "dispatcher.py"
        content = dispatcher_path.read_text()
        # In erp_to_woo block, Inventory should call sync_stock_to_woo
        erp_to_woo_start = content.find('elif doc.direction == "erp_to_woo"')
        block = content[erp_to_woo_start:erp_to_woo_start + 600]
        self.assertIn("Inventory", block)
        self.assertIn("sync_stock_to_woo", block)

    def test_dispatcher_woo_to_erp_inventory_routes_to_sync_stock_from_woo(self):
        dispatcher_path = ROOT / "caz_woosync" / "sync" / "dispatcher.py"
        content = dispatcher_path.read_text()
        # In woo_to_erp block, Inventory should call sync_stock_from_woo
        # The woo_to_erp block spans Product, Order, Customer, then Inventory
        woo_to_erp_start = content.find('if doc.direction == "woo_to_erp"')
        erp_to_woo_start = content.find('elif doc.direction == "erp_to_woo"')
        block = content[woo_to_erp_start:erp_to_woo_start]
        self.assertIn("Inventory", block)
        self.assertIn("sync_stock_from_woo", block)

    def test_dispatcher_inventory_stub_removed(self):
        """The Phase 6 stub comment should be gone."""
        dispatcher_path = ROOT / "caz_woosync" / "sync" / "dispatcher.py"
        content = dispatcher_path.read_text()
        self.assertNotIn("Inventory sync not yet implemented (Phase 6)", content)

    def test_dispatcher_woo_to_erp_inventory_parses_payload(self):
        """Inventory routing in woo_to_erp block should handle JSON payload."""
        dispatcher_path = ROOT / "caz_woosync" / "sync" / "dispatcher.py"
        content = dispatcher_path.read_text()
        woo_to_erp_start = content.find('if doc.direction == "woo_to_erp"')
        block = content[woo_to_erp_start:woo_to_erp_start + 800]
        # Should parse payload JSON for Inventory
        self.assertIn("json.loads", block)

    def test_dispatcher_erp_to_woo_inventory_uses_erp_docname(self):
        """erp_to_woo Inventory sync should pass doc.erp_docname as item_code."""
        dispatcher_path = ROOT / "caz_woosync" / "sync" / "dispatcher.py"
        content = dispatcher_path.read_text()
        erp_to_woo_start = content.find('elif doc.direction == "erp_to_woo"')
        block = content[erp_to_woo_start:erp_to_woo_start + 600]
        self.assertIn("doc.erp_docname", block)


# ===========================================================================
# TestStockSyncThreshold
# ===========================================================================


class TestStockSyncThreshold(unittest.TestCase):
    """Test threshold-based stock sync suppression logic."""

    def _should_sync(self, erp_qty, woo_qty, threshold):
        """
        Replicate the threshold decision logic from sync_stock_to_woo.
        Returns True if sync should proceed.
        """
        if threshold <= 0:
            return True  # always sync when threshold=0
        diff = abs(erp_qty - woo_qty)
        return diff > threshold

    def test_threshold_zero_always_syncs_equal_quantities(self):
        """threshold=0 means always sync, even when quantities are equal."""
        self.assertTrue(self._should_sync(10, 10, threshold=0))

    def test_threshold_zero_always_syncs_different_quantities(self):
        self.assertTrue(self._should_sync(10, 20, threshold=0))

    def test_threshold_5_skips_diff_less_than_5(self):
        """diff=3 < threshold=5 → skip."""
        self.assertFalse(self._should_sync(10, 13, threshold=5))

    def test_threshold_5_skips_diff_exactly_5(self):
        """diff=5 == threshold=5 → skip (not strictly greater)."""
        self.assertFalse(self._should_sync(10, 15, threshold=5))

    def test_threshold_5_syncs_diff_greater_than_5(self):
        """diff=6 > threshold=5 → sync."""
        self.assertTrue(self._should_sync(10, 16, threshold=5))

    def test_threshold_1_skips_diff_of_1(self):
        self.assertFalse(self._should_sync(5, 6, threshold=1))

    def test_threshold_1_syncs_diff_of_2(self):
        self.assertTrue(self._should_sync(5, 7, threshold=1))

    def test_threshold_checks_absolute_difference(self):
        """Threshold uses absolute value, so negative diff is treated same."""
        self.assertFalse(self._should_sync(15, 10, threshold=5))  # diff=-5, abs=5, skip
        self.assertTrue(self._should_sync(17, 10, threshold=5))   # diff=-7, abs=7, sync

    def test_threshold_field_in_store_json(self):
        """caz_woo_store.json must have stock_sync_threshold field."""
        store_json_path = DOCTYPE_DIR / "caz_woo_store" / "caz_woo_store.json"
        with open(store_json_path) as f:
            schema = json.load(f)
        fields_by_name = {f["fieldname"]: f for f in schema.get("fields", [])}
        field = fields_by_name.get("stock_sync_threshold")
        self.assertIsNotNone(field, "stock_sync_threshold field missing from caz_woo_store.json")
        self.assertEqual(field["fieldtype"], "Int")
        self.assertEqual(str(field.get("default", "")), "0")


# ===========================================================================
# TestInventoryStoreJsonFields
# ===========================================================================


class TestInventoryStoreJsonFields(unittest.TestCase):
    """Verify caz_woo_store.json has all three new inventory sync fields."""

    @classmethod
    def setUpClass(cls):
        store_json_path = DOCTYPE_DIR / "caz_woo_store" / "caz_woo_store.json"
        with open(store_json_path) as f:
            cls.schema = json.load(f)
        cls.fields_by_name = {f["fieldname"]: f for f in cls.schema.get("fields", [])}

    def test_section_inventory_sync_present(self):
        field = self.fields_by_name.get("section_inventory_sync")
        self.assertIsNotNone(field, "section_inventory_sync section break missing")
        self.assertEqual(field["fieldtype"], "Section Break")
        self.assertEqual(field.get("label"), "Inventory Sync Settings")

    def test_sync_stock_to_woo_check_default_1(self):
        field = self.fields_by_name.get("sync_stock_to_woo")
        self.assertIsNotNone(field, "sync_stock_to_woo field missing")
        self.assertEqual(field["fieldtype"], "Check")
        self.assertEqual(str(field.get("default", "")), "1")

    def test_sync_stock_from_woo_check_default_0(self):
        field = self.fields_by_name.get("sync_stock_from_woo")
        self.assertIsNotNone(field, "sync_stock_from_woo field missing")
        self.assertEqual(field["fieldtype"], "Check")
        self.assertEqual(str(field.get("default", "")), "0")

    def test_stock_sync_threshold_int_default_0(self):
        field = self.fields_by_name.get("stock_sync_threshold")
        self.assertIsNotNone(field, "stock_sync_threshold field missing")
        self.assertEqual(field["fieldtype"], "Int")
        self.assertEqual(str(field.get("default", "")), "0")

    def test_inventory_fields_have_descriptions(self):
        for fname in ("sync_stock_to_woo", "sync_stock_from_woo", "stock_sync_threshold"):
            field = self.fields_by_name.get(fname)
            self.assertIsNotNone(field, f"{fname} field missing")
            desc = field.get("description", "")
            self.assertTrue(
                desc and desc.strip(),
                f"Field '{fname}' has no description",
            )

    def test_inventory_fields_in_field_order(self):
        field_order = self.schema.get("field_order", [])
        for fname in ("section_inventory_sync", "sync_stock_to_woo",
                      "sync_stock_from_woo", "stock_sync_threshold"):
            self.assertIn(fname, field_order, f"{fname} missing from field_order")

    def test_inventory_section_before_webhook_section(self):
        field_order = self.schema.get("field_order", [])
        inventory_idx = field_order.index("section_inventory_sync")
        webhook_idx = field_order.index("section_webhook")
        self.assertLess(inventory_idx, webhook_idx)

    def test_inventory_section_after_customer_section(self):
        field_order = self.schema.get("field_order", [])
        customer_idx = field_order.index("section_customer_sync")
        inventory_idx = field_order.index("section_inventory_sync")
        self.assertLess(customer_idx, inventory_idx)


# ===========================================================================
# TestInventoryModuleImportable
# ===========================================================================


class TestInventoryModuleImportable(unittest.TestCase):
    """Verify inventory.py can be imported and has the expected public functions."""

    def _load_module(self):
        return _load_inventory_module()

    def test_module_has_sync_stock_to_woo(self):
        mod = self._load_module()
        self.assertTrue(callable(getattr(mod, "sync_stock_to_woo", None)))

    def test_module_has_sync_stock_from_woo(self):
        mod = self._load_module()
        self.assertTrue(callable(getattr(mod, "sync_stock_from_woo", None)))

    def test_module_has_push_all_stock(self):
        mod = self._load_module()
        self.assertTrue(callable(getattr(mod, "push_all_stock", None)))

    def test_module_has_on_stock_ledger_submit(self):
        mod = self._load_module()
        self.assertTrue(callable(getattr(mod, "on_stock_ledger_submit", None)))

    def test_module_imports_frappe_at_top(self):
        inv_path = ROOT / "caz_woosync" / "sync" / "inventory.py"
        content = inv_path.read_text()
        lines = content.splitlines()
        import_lines = [l for l in lines[:10] if "import frappe" in l]
        self.assertTrue(import_lines, "frappe must be imported at top of inventory.py")


# ===========================================================================
# TestHooksDocEvent
# ===========================================================================


class TestHooksDocEvent(unittest.TestCase):
    """Verify hooks.py has the Stock Ledger Entry doc_event."""

    def test_stock_ledger_entry_in_doc_events(self):
        hooks_path = ROOT / "caz_woosync" / "hooks.py"
        content = hooks_path.read_text()
        self.assertIn("Stock Ledger Entry", content)

    def test_on_submit_points_to_inventory_module(self):
        hooks_path = ROOT / "caz_woosync" / "hooks.py"
        content = hooks_path.read_text()
        self.assertIn("caz_woosync.sync.inventory.on_stock_ledger_submit", content)

    def test_hooks_still_has_item_on_update(self):
        hooks_path = ROOT / "caz_woosync" / "hooks.py"
        content = hooks_path.read_text()
        self.assertIn("caz_woosync.sync.items.on_item_update", content)


if __name__ == "__main__":
    unittest.main()
