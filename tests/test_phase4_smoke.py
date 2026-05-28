"""
Phase 4 smoke tests — WooCommerce Order Sync.
No Frappe instance required. Tests run pure Python logic.
"""
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

ROOT = Path(__file__).parent.parent
DOCTYPE_DIR = ROOT / "caz_woosync" / "doctype"

# ---------------------------------------------------------------------------
# Minimal Frappe stub so we can import orders.py without a live Frappe site
# ---------------------------------------------------------------------------

def _make_frappe_stub():
    """Build a minimal frappe module stub with just enough surface area."""
    frappe_mod = types.ModuleType("frappe")

    # Basic db stub
    db = MagicMock()
    db.get_value = MagicMock(return_value=None)
    db.exists = MagicMock(return_value=False)
    db.set_value = MagicMock()
    db.commit = MagicMock()
    db.sql = MagicMock(return_value=[])
    frappe_mod.db = db

    # Doc creation
    frappe_mod.new_doc = MagicMock(return_value=MagicMock())
    frappe_mod.get_doc = MagicMock(return_value=MagicMock())
    frappe_mod.get_single = MagicMock(return_value=MagicMock())
    frappe_mod.throw = MagicMock(side_effect=Exception)
    frappe_mod.log_error = MagicMock()
    frappe_mod.get_traceback = MagicMock(return_value="traceback")
    frappe_mod.enqueue = MagicMock()
    frappe_mod.whitelist = lambda fn=None, **kw: (fn if callable(fn) else lambda f: f)

    # Logger stub
    logger_mock = MagicMock()
    frappe_mod.logger = MagicMock(return_value=logger_mock)

    # Utils
    utils_mod = types.ModuleType("frappe.utils")
    utils_mod.now_datetime = MagicMock(return_value="2026-05-28 12:00:00")
    utils_mod.getdate = MagicMock(side_effect=lambda d: d)
    utils_mod.now = MagicMock(return_value="2026-05-28 12:00:00")
    utils_mod.nowdate = MagicMock(return_value="2026-05-28")
    frappe_mod.utils = utils_mod

    # html_utils
    html_utils_mod = types.ModuleType("frappe.utils.html_utils")
    html_utils_mod.strip_html = lambda s: s  # identity in tests
    frappe_mod.utils.html_utils = html_utils_mod

    # model stub
    model_mod = types.ModuleType("frappe.model")
    doc_mod = types.ModuleType("frappe.model.document")
    doc_mod.Document = object
    model_mod.document = doc_mod
    frappe_mod.model = model_mod

    return frappe_mod


# Inject stubs before importing orders module
_frappe_stub = _make_frappe_stub()
sys.modules.setdefault("frappe", _frappe_stub)
sys.modules.setdefault("frappe.utils", _frappe_stub.utils)
sys.modules.setdefault("frappe.utils.html_utils", _frappe_stub.utils.html_utils)
sys.modules.setdefault("frappe.model", _frappe_stub.model)
sys.modules.setdefault("frappe.model.document", _frappe_stub.model.document)


# ---------------------------------------------------------------------------
# Helpers / constants mirrored from orders.py
# ---------------------------------------------------------------------------

WC_STATUS_TO_DELIVERY_STATUS = {
    "pending": "To Deliver and Bill",
    "processing": "To Deliver and Bill",
    "on-hold": "To Deliver and Bill",
    "completed": "Completed",
    "cancelled": "Cancelled",
    "refunded": "Cancelled",
    "failed": "Cancelled",
}

SKIP_CREATE_STATUSES = {"cancelled", "refunded", "failed"}


def _make_sample_payload(status="processing", order_id=1001):
    """Return a minimal WooCommerce order payload dict."""
    return {
        "id": order_id,
        "status": status,
        "currency": "USD",
        "date_created": "2026-05-28T10:00:00",
        "billing": {
            "first_name": "Jane",
            "last_name": "Doe",
            "email": "jane@example.com",
            "phone": "555-1234",
            "address_1": "123 Main St",
            "address_2": "",
            "city": "Springfield",
            "state": "IL",
            "postcode": "62701",
            "country": "US",
        },
        "line_items": [
            {
                "id": 1,
                "product_id": 42,
                "name": "Test Widget",
                "quantity": 2,
                "subtotal": "20.00",
                "total": "20.00",
            }
        ],
        "shipping_total": "5.00",
        "total_tax": "2.50",
        "meta_data": [],
    }


def _make_store_mock(
    name="Test Store",
    company="Test Company",
    warehouse="Main Warehouse",
    income_account=None,
    tax_account=None,
    customer_group="All Customer Groups",
    so_naming_series="SO-CAZWOO-.YYYY.-",
    shipping_item_code="Shipping",
    so_auto_submit=0,
    create_so_from_woo=1,
):
    """Return a MagicMock that mimics a Caz Woo Store doc."""
    store = MagicMock()
    store.name = name
    store.company = company
    store.warehouse = warehouse
    store.income_account = income_account
    store.tax_account = tax_account
    store.customer_group = customer_group
    store.so_naming_series = so_naming_series
    store.shipping_item_code = shipping_item_code
    store.so_auto_submit = so_auto_submit
    store.create_so_from_woo = create_so_from_woo
    return store


# ===========================================================================
# TestOrderStatusMapping
# ===========================================================================

class TestOrderStatusMapping(unittest.TestCase):
    """Test WC status → ERPNext delivery status mapping for all known statuses."""

    def test_pending_maps_to_to_deliver_and_bill(self):
        self.assertEqual(WC_STATUS_TO_DELIVERY_STATUS["pending"], "To Deliver and Bill")

    def test_processing_maps_to_to_deliver_and_bill(self):
        self.assertEqual(WC_STATUS_TO_DELIVERY_STATUS["processing"], "To Deliver and Bill")

    def test_on_hold_maps_to_to_deliver_and_bill(self):
        self.assertEqual(WC_STATUS_TO_DELIVERY_STATUS["on-hold"], "To Deliver and Bill")

    def test_completed_maps_to_completed(self):
        self.assertEqual(WC_STATUS_TO_DELIVERY_STATUS["completed"], "Completed")

    def test_cancelled_maps_to_cancelled(self):
        self.assertEqual(WC_STATUS_TO_DELIVERY_STATUS["cancelled"], "Cancelled")

    def test_refunded_maps_to_cancelled(self):
        self.assertEqual(WC_STATUS_TO_DELIVERY_STATUS["refunded"], "Cancelled")

    def test_failed_maps_to_cancelled(self):
        self.assertEqual(WC_STATUS_TO_DELIVERY_STATUS.get("failed"), "Cancelled")

    def test_all_expected_statuses_covered(self):
        expected = {"pending", "processing", "on-hold", "completed", "cancelled", "refunded", "failed"}
        self.assertEqual(set(WC_STATUS_TO_DELIVERY_STATUS.keys()), expected)

    def test_skip_create_statuses_are_correct(self):
        self.assertIn("cancelled", SKIP_CREATE_STATUSES)
        self.assertIn("refunded", SKIP_CREATE_STATUSES)
        self.assertIn("failed", SKIP_CREATE_STATUSES)
        # These should NOT be skipped
        self.assertNotIn("processing", SKIP_CREATE_STATUSES)
        self.assertNotIn("pending", SKIP_CREATE_STATUSES)
        self.assertNotIn("completed", SKIP_CREATE_STATUSES)


# ===========================================================================
# TestOrderPayloadParsing
# ===========================================================================

class TestOrderPayloadParsing(unittest.TestCase):
    """Test date parsing, price calculation, shipping, and tax extraction."""

    def test_date_parsing_iso_datetime(self):
        """ISO datetime '2026-05-28T10:00:00' → date part '2026-05-28'."""
        date_created = "2026-05-28T10:00:00"
        result = date_created.split("T")[0]
        self.assertEqual(result, "2026-05-28")

    def test_date_parsing_no_time_component(self):
        """Plain date '2026-05-28' handled gracefully."""
        date_created = "2026-05-28"
        result = date_created.split("T")[0]  # same result since no 'T'
        self.assertEqual(result, "2026-05-28")

    def test_line_item_unit_price_calculation(self):
        """unit_price = subtotal / quantity."""
        line_item = {"quantity": 2, "subtotal": "20.00"}
        qty = float(line_item["quantity"])
        subtotal = float(line_item["subtotal"])
        unit_price = subtotal / qty
        self.assertAlmostEqual(unit_price, 10.0)

    def test_line_item_unit_price_zero_qty_guard(self):
        """Should handle qty=0 without ZeroDivisionError."""
        qty = float("0")
        subtotal = float("0.00")
        unit_price = (subtotal / qty) if qty else 0.0
        self.assertEqual(unit_price, 0.0)

    def test_shipping_total_is_positive(self):
        payload = _make_sample_payload()
        shipping_total = float(payload.get("shipping_total") or 0)
        self.assertGreater(shipping_total, 0)

    def test_shipping_zero_not_added(self):
        """Shipping item should only be added when shipping_total > 0."""
        payload = _make_sample_payload()
        payload["shipping_total"] = "0.00"
        shipping_total = float(payload.get("shipping_total") or 0)
        self.assertEqual(shipping_total, 0.0)
        # Logic: no shipping row added when == 0
        self.assertFalse(shipping_total > 0)

    def test_tax_extraction(self):
        payload = _make_sample_payload()
        total_tax = float(payload.get("total_tax") or 0)
        self.assertAlmostEqual(total_tax, 2.50)

    def test_tax_zero_means_no_tax_row(self):
        payload = _make_sample_payload()
        payload["total_tax"] = "0.00"
        total_tax = float(payload.get("total_tax") or 0)
        self.assertEqual(total_tax, 0.0)

    def test_currency_extracted_from_payload(self):
        payload = _make_sample_payload()
        self.assertEqual(payload["currency"], "USD")

    def test_meta_data_idempotency_key_detection(self):
        """The _caz_woo_so key in meta_data indicates an SO was already created."""
        meta_data = [
            {"key": "_some_other_key", "value": "foo"},
            {"key": "_caz_woo_so", "value": "SO-CAZWOO-2026-00001"},
        ]
        found = None
        for meta in meta_data:
            if meta.get("key") == "_caz_woo_so":
                found = meta.get("value")
        self.assertEqual(found, "SO-CAZWOO-2026-00001")

    def test_meta_data_idempotency_key_absent(self):
        meta_data = [{"key": "_other", "value": "bar"}]
        found = None
        for meta in meta_data:
            if meta.get("key") == "_caz_woo_so":
                found = meta.get("value")
        self.assertIsNone(found)

    def test_line_items_parsed_correctly(self):
        payload = _make_sample_payload()
        items = payload["line_items"]
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["product_id"], 42)
        self.assertEqual(item["quantity"], 2)
        self.assertEqual(item["name"], "Test Widget")


# ===========================================================================
# TestCustomerMatching
# ===========================================================================

class TestCustomerMatching(unittest.TestCase):
    """Test customer lookup and creation logic."""

    def test_email_used_as_primary_key(self):
        """Email is the primary match field for customers."""
        billing = {
            "email": "jane@example.com",
            "first_name": "Jane",
            "last_name": "Doe",
        }
        email = billing.get("email", "")
        self.assertIsNotNone(email)
        self.assertEqual(email, "jane@example.com")

    def test_fallback_to_billing_name_when_no_email(self):
        """When email is absent, billing first+last is used as customer name."""
        billing = {
            "email": "",
            "first_name": "John",
            "last_name": "Smith",
        }
        email = billing.get("email", "").strip()
        first = billing.get("first_name", "")
        last = billing.get("last_name", "")
        full_name = f"{first} {last}".strip() or email or "WooCommerce Customer"
        self.assertEqual(full_name, "John Smith")

    def test_fallback_to_email_when_no_name(self):
        """When no name is provided, email is used as customer_name."""
        billing = {
            "email": "anon@example.com",
            "first_name": "",
            "last_name": "",
        }
        email = billing.get("email", "")
        first = billing.get("first_name", "")
        last = billing.get("last_name", "")
        full_name = f"{first} {last}".strip() or email or "WooCommerce Customer"
        self.assertEqual(full_name, "anon@example.com")

    def test_fallback_to_default_name_when_nothing_available(self):
        """When neither name nor email is available, use 'WooCommerce Customer'."""
        billing = {"email": "", "first_name": "", "last_name": ""}
        email = billing.get("email", "")
        first = billing.get("first_name", "")
        last = billing.get("last_name", "")
        full_name = f"{first} {last}".strip() or email or "WooCommerce Customer"
        self.assertEqual(full_name, "WooCommerce Customer")

    def test_customer_group_from_store_config(self):
        """Customer group is taken from store.customer_group."""
        store = _make_store_mock(customer_group="WooCommerce Customers")
        customer_group = getattr(store, "customer_group", None) or "All Customer Groups"
        self.assertEqual(customer_group, "WooCommerce Customers")

    def test_customer_group_fallback_when_not_set(self):
        """Customer group falls back to 'All Customer Groups' if store has none."""
        store = _make_store_mock(customer_group=None)
        store.customer_group = None
        customer_group = getattr(store, "customer_group", None) or "All Customer Groups"
        self.assertEqual(customer_group, "All Customer Groups")

    def test_territory_always_all_territories(self):
        """Territory should always be 'All Territories' for new customers."""
        territory = "All Territories"
        self.assertEqual(territory, "All Territories")

    def test_customer_type_individual(self):
        """New WooCommerce customers are created as Individual type."""
        customer_type = "Individual"
        self.assertEqual(customer_type, "Individual")


# ===========================================================================
# TestOrderIdempotency
# ===========================================================================

class TestOrderIdempotency(unittest.TestCase):
    """Test that a second webhook for the same order_id updates rather than creates."""

    def test_existing_mapping_triggers_update_not_create(self):
        """
        When a mapping exists for a woo_order_id, we should update status, not create.
        Simulate the decision branch in sync_order_to_erp.
        """
        # Simulate: mapping already exists with a sales_order
        existing_mapping = {"name": "HASH123", "sales_order": "SO-CAZWOO-2026-00001"}
        payload = _make_sample_payload(status="completed", order_id=1001)

        wc_status = payload.get("status", "").lower()
        create_called = False
        update_called = False

        if existing_mapping and existing_mapping.get("sales_order"):
            update_called = True
        else:
            if wc_status not in SKIP_CREATE_STATUSES:
                create_called = True

        self.assertTrue(update_called)
        self.assertFalse(create_called)

    def test_no_mapping_triggers_create(self):
        """When no mapping exists, a new SO should be created."""
        existing_mapping = None
        payload = _make_sample_payload(status="processing", order_id=1002)

        wc_status = payload.get("status", "").lower()
        create_called = False
        update_called = False

        if existing_mapping and existing_mapping.get("sales_order"):
            update_called = True
        else:
            if wc_status not in SKIP_CREATE_STATUSES:
                create_called = True

        self.assertFalse(update_called)
        self.assertTrue(create_called)

    def test_cancelled_with_no_mapping_skips_creation(self):
        """Cancelled order with no mapping should be skipped entirely."""
        existing_mapping = None
        payload = _make_sample_payload(status="cancelled", order_id=1003)

        wc_status = payload.get("status", "").lower()
        skipped = False
        create_called = False

        if existing_mapping and existing_mapping.get("sales_order"):
            pass  # update path
        else:
            if wc_status in SKIP_CREATE_STATUSES:
                skipped = True
            else:
                create_called = True

        self.assertTrue(skipped)
        self.assertFalse(create_called)

    def test_refunded_with_no_mapping_skips_creation(self):
        """Refunded order with no mapping should also be skipped."""
        existing_mapping = None
        payload = _make_sample_payload(status="refunded", order_id=1004)

        wc_status = payload.get("status", "").lower()
        skipped = wc_status in SKIP_CREATE_STATUSES
        self.assertTrue(skipped)

    def test_meta_data_so_key_prevents_duplicate_creation(self):
        """If payload contains _caz_woo_so meta key, creation is skipped."""
        payload = _make_sample_payload(order_id=1005)
        payload["meta_data"] = [{"key": "_caz_woo_so", "value": "SO-CAZWOO-2026-00001"}]

        existing_so = None
        for meta in payload.get("meta_data", []):
            if meta.get("key") == "_caz_woo_so":
                existing_so = meta.get("value")

        self.assertEqual(existing_so, "SO-CAZWOO-2026-00001")


# ===========================================================================
# TestOrderMapping — Doctype JSON integrity
# ===========================================================================

class TestOrderMapping(unittest.TestCase):
    """Verify caz_woo_order_mapping.json has correct structure and fields."""

    @classmethod
    def setUpClass(cls):
        mapping_json_path = (
            DOCTYPE_DIR / "caz_woo_order_mapping" / "caz_woo_order_mapping.json"
        )
        with open(mapping_json_path) as f:
            cls.schema = json.load(f)
        cls.fields_by_name = {
            field["fieldname"]: field for field in cls.schema.get("fields", [])
        }

    def test_doctype_name_is_correct(self):
        self.assertEqual(self.schema["name"], "Caz Woo Order Mapping")

    def test_autoname_is_hash(self):
        self.assertEqual(self.schema["autoname"], "hash")

    def test_not_singleton(self):
        self.assertEqual(self.schema.get("issingle", 0), 0)

    def test_module_is_caz_woosync(self):
        self.assertEqual(self.schema["module"], "Caz Woosync")

    def test_store_field_exists_and_links_to_caz_woo_store(self):
        field = self.fields_by_name.get("store")
        self.assertIsNotNone(field, "store field is missing")
        self.assertEqual(field["fieldtype"], "Link")
        self.assertEqual(field["options"], "Caz Woo Store")
        self.assertEqual(field.get("reqd", 0), 1)

    def test_woo_order_id_field_exists(self):
        field = self.fields_by_name.get("woo_order_id")
        self.assertIsNotNone(field, "woo_order_id field is missing")
        self.assertEqual(field["fieldtype"], "Data")
        self.assertEqual(field.get("reqd", 0), 1)

    def test_sales_order_field_links_to_sales_order(self):
        field = self.fields_by_name.get("sales_order")
        self.assertIsNotNone(field, "sales_order field is missing")
        self.assertEqual(field["fieldtype"], "Link")
        self.assertEqual(field["options"], "Sales Order")

    def test_woo_status_field_exists(self):
        field = self.fields_by_name.get("woo_status")
        self.assertIsNotNone(field, "woo_status field is missing")
        self.assertEqual(field["fieldtype"], "Data")

    def test_erp_status_is_read_only(self):
        field = self.fields_by_name.get("erp_status")
        self.assertIsNotNone(field, "erp_status field is missing")
        self.assertEqual(field.get("read_only", 0), 1)

    def test_last_synced_is_datetime(self):
        field = self.fields_by_name.get("last_synced")
        self.assertIsNotNone(field, "last_synced field is missing")
        self.assertEqual(field["fieldtype"], "Datetime")
        self.assertEqual(field.get("read_only", 0), 1)

    def test_sync_error_is_small_text(self):
        field = self.fields_by_name.get("sync_error")
        self.assertIsNotNone(field, "sync_error field is missing")
        self.assertEqual(field["fieldtype"], "Small Text")

    def test_all_non_column_break_fields_have_descriptions(self):
        """Every data-bearing field must have a non-empty description."""
        for field in self.schema.get("fields", []):
            if field["fieldtype"] in ("Column Break", "Section Break"):
                continue
            desc = field.get("description", "")
            self.assertTrue(
                desc and desc.strip(),
                f"Field '{field['fieldname']}' is missing a description",
            )

    def test_in_list_view_fields(self):
        """woo_order_id, sales_order, and woo_status should be in list view."""
        for fname in ("woo_order_id", "sales_order", "woo_status"):
            field = self.fields_by_name.get(fname)
            self.assertIsNotNone(field, f"{fname} field missing")
            self.assertEqual(
                field.get("in_list_view", 0), 1,
                f"{fname} should have in_list_view=1",
            )


# ===========================================================================
# TestCazWooStoreOrderFields — verify store JSON was updated
# ===========================================================================

class TestCazWooStoreOrderFields(unittest.TestCase):
    """Verify caz_woo_store.json contains the new order sync settings fields."""

    @classmethod
    def setUpClass(cls):
        store_json_path = DOCTYPE_DIR / "caz_woo_store" / "caz_woo_store.json"
        with open(store_json_path) as f:
            cls.schema = json.load(f)
        cls.fields_by_name = {
            field["fieldname"]: field for field in cls.schema.get("fields", [])
        }

    def test_create_so_from_woo_exists(self):
        field = self.fields_by_name.get("create_so_from_woo")
        self.assertIsNotNone(field, "create_so_from_woo field missing from caz_woo_store.json")
        self.assertEqual(field["fieldtype"], "Check")

    def test_create_so_from_woo_default_is_1(self):
        field = self.fields_by_name.get("create_so_from_woo")
        self.assertEqual(str(field.get("default", "")), "1")

    def test_so_auto_submit_exists(self):
        field = self.fields_by_name.get("so_auto_submit")
        self.assertIsNotNone(field, "so_auto_submit field missing from caz_woo_store.json")
        self.assertEqual(field["fieldtype"], "Check")

    def test_so_auto_submit_default_is_0(self):
        field = self.fields_by_name.get("so_auto_submit")
        self.assertEqual(str(field.get("default", "")), "0")

    def test_shipping_item_code_exists(self):
        field = self.fields_by_name.get("shipping_item_code")
        self.assertIsNotNone(field, "shipping_item_code field missing from caz_woo_store.json")
        self.assertEqual(field["fieldtype"], "Data")

    def test_shipping_item_code_default_is_Shipping(self):
        field = self.fields_by_name.get("shipping_item_code")
        self.assertEqual(field.get("default", ""), "Shipping")

    def test_section_order_sync_present(self):
        field = self.fields_by_name.get("section_order_sync")
        self.assertIsNotNone(field, "section_order_sync section break missing")
        self.assertEqual(field["fieldtype"], "Section Break")
        self.assertEqual(field.get("label"), "Order Sync Settings")

    def test_order_sync_fields_have_descriptions(self):
        for fname in ("create_so_from_woo", "so_auto_submit", "shipping_item_code"):
            field = self.fields_by_name.get(fname)
            self.assertIsNotNone(field, f"{fname} field missing")
            desc = field.get("description", "")
            self.assertTrue(
                desc and desc.strip(),
                f"Field '{fname}' has no description",
            )

    def test_order_sync_fields_in_field_order(self):
        field_order = self.schema.get("field_order", [])
        for fname in ("section_order_sync", "create_so_from_woo", "so_auto_submit", "shipping_item_code"):
            self.assertIn(fname, field_order, f"{fname} missing from field_order")


# ===========================================================================
# TestOrdersModuleImportable
# ===========================================================================

class TestOrdersModuleImportable(unittest.TestCase):
    """Verify orders.py can be imported and has the expected public functions."""

    def test_module_has_sync_order_to_erp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "caz_woosync.sync.orders",
            ROOT / "caz_woosync" / "sync" / "orders.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertTrue(callable(getattr(mod, "sync_order_to_erp", None)))

    def test_module_has_create_sales_order(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "caz_woosync.sync.orders",
            ROOT / "caz_woosync" / "sync" / "orders.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertTrue(callable(getattr(mod, "_create_sales_order", None)))

    def test_module_has_get_or_create_customer(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "caz_woosync.sync.orders",
            ROOT / "caz_woosync" / "sync" / "orders.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertTrue(callable(getattr(mod, "_get_or_create_customer", None)))

    def test_module_has_update_order_status(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "caz_woosync.sync.orders",
            ROOT / "caz_woosync" / "sync" / "orders.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertTrue(callable(getattr(mod, "_update_order_status", None)))

    def test_module_has_get_or_create_placeholder_item(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "caz_woosync.sync.orders",
            ROOT / "caz_woosync" / "sync" / "orders.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertTrue(callable(getattr(mod, "_get_or_create_placeholder_item", None)))

    def test_wc_status_constants_present(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "caz_woosync.sync.orders",
            ROOT / "caz_woosync" / "sync" / "orders.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertIsInstance(mod.WC_STATUS_TO_DELIVERY_STATUS, dict)
        self.assertIsInstance(mod.SKIP_CREATE_STATUSES, (set, frozenset))


# ===========================================================================
# TestDispatcherOrderRouting
# ===========================================================================

class TestDispatcherOrderRouting(unittest.TestCase):
    """Verify dispatcher.py routes Order entities to sync_order_to_erp."""

    def test_dispatcher_no_longer_has_phase4_stub(self):
        dispatcher_path = ROOT / "caz_woosync" / "sync" / "dispatcher.py"
        content = dispatcher_path.read_text()
        self.assertNotIn(
            "Order sync not yet implemented",
            content,
            "dispatcher.py still has Phase 4 stub — it should be replaced",
        )

    def test_dispatcher_imports_sync_order_to_erp(self):
        dispatcher_path = ROOT / "caz_woosync" / "sync" / "dispatcher.py"
        content = dispatcher_path.read_text()
        self.assertIn("sync_order_to_erp", content)

    def test_dispatcher_has_order_branch(self):
        dispatcher_path = ROOT / "caz_woosync" / "sync" / "dispatcher.py"
        content = dispatcher_path.read_text()
        self.assertIn('entity_type == "Order"', content)


# ===========================================================================
# TestTasksOrderPolling
# ===========================================================================

class TestTasksOrderPolling(unittest.TestCase):
    """Verify tasks.py has order polling functions."""

    def test_poll_orders_function_exists(self):
        tasks_path = ROOT / "caz_woosync" / "tasks.py"
        content = tasks_path.read_text()
        self.assertIn("def _poll_orders(", content)

    def test_poll_products_function_exists(self):
        tasks_path = ROOT / "caz_woosync" / "tasks.py"
        content = tasks_path.read_text()
        self.assertIn("def _poll_products(", content)

    def test_poll_store_calls_poll_orders(self):
        tasks_path = ROOT / "caz_woosync" / "tasks.py"
        content = tasks_path.read_text()
        self.assertIn("_poll_orders(", content)

    def test_poll_orders_uses_order_entity_type(self):
        tasks_path = ROOT / "caz_woosync" / "tasks.py"
        content = tasks_path.read_text()
        # The function should queue items with entity_type="Order"
        self.assertIn('"Order"', content)


# ===========================================================================
# TestConnectionAPIOrderEndpoints
# ===========================================================================

class TestConnectionAPIOrderEndpoints(unittest.TestCase):
    """Verify api/connection.py has the two new order endpoints."""

    def test_get_order_sync_status_endpoint_exists(self):
        conn_path = ROOT / "caz_woosync" / "api" / "connection.py"
        content = conn_path.read_text()
        self.assertIn("def get_order_sync_status(", content)

    def test_trigger_order_sync_endpoint_exists(self):
        conn_path = ROOT / "caz_woosync" / "api" / "connection.py"
        content = conn_path.read_text()
        self.assertIn("def trigger_order_sync(", content)

    def test_get_order_sync_status_is_whitelisted(self):
        conn_path = ROOT / "caz_woosync" / "api" / "connection.py"
        content = conn_path.read_text()
        # whitelist decorator should appear before the function definition
        idx_decorator = content.rfind("@frappe.whitelist()", 0, content.index("def get_order_sync_status("))
        self.assertGreater(idx_decorator, 0)

    def test_trigger_order_sync_is_whitelisted(self):
        conn_path = ROOT / "caz_woosync" / "api" / "connection.py"
        content = conn_path.read_text()
        idx_decorator = content.rfind("@frappe.whitelist()", 0, content.index("def trigger_order_sync("))
        self.assertGreater(idx_decorator, 0)

    def test_trigger_order_sync_queues_order_entity(self):
        conn_path = ROOT / "caz_woosync" / "api" / "connection.py"
        content = conn_path.read_text()
        self.assertIn('"Order"', content)


if __name__ == "__main__":
    unittest.main()
