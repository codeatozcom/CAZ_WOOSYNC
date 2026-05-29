"""
Phase 12 smoke tests — Refund handling, Coupon sync, Webhook routing.
No Frappe instance required. Tests run pure Python logic via mocking.
"""
import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

ROOT = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Minimal Frappe stub (hermetic)
# ---------------------------------------------------------------------------


def _make_frappe_stub():
    frappe_mod = types.ModuleType("frappe")

    db = MagicMock()
    db.get_value = MagicMock(return_value=None)
    db.exists = MagicMock(return_value=False)
    db.set_value = MagicMock()
    db.commit = MagicMock()
    db.sql = MagicMock(return_value=[])
    db.get_all = MagicMock(return_value=[])
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

    _logger = MagicMock()
    frappe_mod.logger = MagicMock(return_value=_logger)

    # utils
    utils_mod = types.ModuleType("frappe.utils")
    utils_mod.today = MagicMock(return_value="2026-05-29")
    utils_mod.nowdate = MagicMock(return_value="2026-05-29")
    frappe_mod.utils = utils_mod

    # AuthenticationError, ValidationError
    class _FrappeError(Exception):
        pass

    frappe_mod.AuthenticationError = _FrappeError
    frappe_mod.ValidationError = _FrappeError
    frappe_mod.DoesNotExistError = _FrappeError

    # local
    frappe_mod.local = MagicMock()
    frappe_mod.form_dict = {}
    frappe_mod.response = {}

    return frappe_mod


def _install_frappe_stub():
    """Install hermetic frappe stub — must happen before any module under test is imported."""
    frappe_mod = _make_frappe_stub()
    sys.modules["frappe"] = frappe_mod
    sys.modules["frappe.utils"] = frappe_mod.utils

    # Stub sub-packages needed by the sync modules
    for sub in [
        "frappe.model",
        "frappe.model.document",
    ]:
        sys.modules[sub] = types.ModuleType(sub)

    return frappe_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_module(rel_path):
    """Load a module from the project root by relative path."""
    module_path = ROOT / rel_path
    spec = importlib.util.spec_from_file_location(rel_path, module_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRefundIdempotency(unittest.TestCase):
    """Second call for same order returns existing credit note, no duplicate created."""

    def setUp(self):
        self.frappe = _install_frappe_stub()

    def test_returns_existing_credit_note(self):
        self.frappe.db.get_value = MagicMock(side_effect=lambda doctype, filters, field, **kw: {
            "Caz Woo Order Mapping": "SINV-001",
            "Sales Invoice": "CN-001",
        }.get(doctype))

        refunds_mod = _load_module("caz_woosync/sync/refunds.py")

        result = refunds_mod.handle_refund("TestStore", "42")

        self.assertEqual(result, "CN-001")
        # new_doc should NOT have been called (no new credit note created)
        self.frappe.new_doc.assert_not_called()

    def test_no_si_returns_none(self):
        self.frappe.db.get_value = MagicMock(return_value=None)

        refunds_mod = _load_module("caz_woosync/sync/refunds.py")

        result = refunds_mod.handle_refund("TestStore", "99")

        self.assertIsNone(result)
        self.frappe.new_doc.assert_not_called()


class TestRefundCreditNoteFields(unittest.TestCase):
    """Credit note has is_return=1, return_against=si_name, posting_date=today."""

    def setUp(self):
        self.frappe = _install_frappe_stub()

    def test_credit_note_fields(self):
        # db.get_value: mapping returns si_name, credit note check returns None
        call_count = [0]

        def _get_value(doctype, filters, field, **kw):
            if doctype == "Caz Woo Order Mapping":
                return "SINV-CAZWOO-2026-001"
            # Second call: check existing credit note — return None (doesn't exist)
            return None

        self.frappe.db.get_value = MagicMock(side_effect=_get_value)

        # Mock store and original SI
        store = MagicMock()
        store.company = "Test Company"
        store.auto_submit_invoice = 0

        original_si = MagicMock()
        original_si.customer = "CUST-001"
        original_si.items = []

        self.frappe.get_doc = MagicMock(side_effect=lambda doctype, name: {
            "Caz Woo Store": store,
            "Sales Invoice": original_si,
        }.get(doctype, MagicMock()))

        cn_doc = MagicMock()
        cn_doc.name = "CN-2026-001"
        self.frappe.new_doc = MagicMock(return_value=cn_doc)

        refunds_mod = _load_module("caz_woosync/sync/refunds.py")

        result = refunds_mod.handle_refund("TestStore", "42")

        # cn_doc.update should have been called with is_return=1
        update_calls = cn_doc.update.call_args_list
        self.assertTrue(len(update_calls) > 0)
        args = update_calls[0][0][0]
        self.assertEqual(args["is_return"], 1)
        self.assertEqual(args["return_against"], "SINV-CAZWOO-2026-001")
        self.assertEqual(args["posting_date"], "2026-05-29")
        self.assertEqual(args["customer"], "CUST-001")
        self.assertEqual(result, cn_doc.name)


class TestPartialRefundItem(unittest.TestCase):
    """Partial refund creates single line item 'Refund Adjustment' with negative qty."""

    def setUp(self):
        self.frappe = _install_frappe_stub()

    def test_partial_refund_item(self):
        def _get_value(doctype, filters, field, **kw):
            if doctype == "Caz Woo Order Mapping":
                return "SINV-001"
            return None

        self.frappe.db.get_value = MagicMock(side_effect=_get_value)
        self.frappe.db.exists = MagicMock(return_value=False)

        store = MagicMock()
        store.company = "Test Company"
        store.auto_submit_invoice = 0

        original_si = MagicMock()
        original_si.customer = "CUST-001"

        self.frappe.get_doc = MagicMock(side_effect=lambda doctype, name: {
            "Caz Woo Store": store,
            "Sales Invoice": original_si,
        }.get(doctype, MagicMock()))

        item_doc = MagicMock()
        cn_doc = MagicMock()
        cn_doc.name = "CN-PARTIAL-001"

        new_doc_calls = []
        def _new_doc(doctype):
            if doctype == "Item":
                return item_doc
            return cn_doc
        self.frappe.new_doc = MagicMock(side_effect=_new_doc)

        refunds_mod = _load_module("caz_woosync/sync/refunds.py")

        result = refunds_mod.handle_partial_refund("TestStore", "42", 25.00, "Customer request")

        # Find the update call that has 'items' key (the SI credit note, not the Item doc)
        update_calls = [c for c in cn_doc.update.call_args_list if "items" in c[0][0]]
        self.assertTrue(len(update_calls) > 0, "No update call with 'items' found")
        args = update_calls[0][0][0]
        items = args["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["item_code"], "Refund Adjustment")
        self.assertEqual(items[0]["qty"], -1)
        self.assertEqual(items[0]["rate"], 25.00)
        self.assertEqual(args["remarks"], "Customer request")
        self.assertEqual(result, cn_doc.name)

    def test_partial_refund_default_reason(self):
        def _get_value(doctype, filters, field, **kw):
            if doctype == "Caz Woo Order Mapping":
                return "SINV-001"
            return None

        self.frappe.db.get_value = MagicMock(side_effect=_get_value)
        self.frappe.db.exists = MagicMock(return_value=True)  # item exists

        store = MagicMock()
        store.company = "Test Company"
        store.auto_submit_invoice = 0

        original_si = MagicMock()
        original_si.customer = "CUST-001"

        self.frappe.get_doc = MagicMock(side_effect=lambda doctype, name: {
            "Caz Woo Store": store,
            "Sales Invoice": original_si,
        }.get(doctype, MagicMock()))

        cn_doc = MagicMock()
        cn_doc.name = "CN-PARTIAL-002"
        self.frappe.new_doc = MagicMock(return_value=cn_doc)

        refunds_mod = _load_module("caz_woosync/sync/refunds.py")

        result = refunds_mod.handle_partial_refund("TestStore", "42", 10.00)

        update_calls = cn_doc.update.call_args_list
        args = update_calls[0][0][0]
        self.assertEqual(args["remarks"], "WooCommerce partial refund")


class TestCouponDiscountTypeMapping(unittest.TestCase):
    """percent→Discount Percentage; fixed_cart→Discount Amount; fixed_product→Discount Amount."""

    def setUp(self):
        self.frappe = _install_frappe_stub()

    def _run_coupon(self, discount_type, amount="10"):
        self.frappe.db.get_value = MagicMock(return_value=None)

        store = MagicMock()
        store.company = "Test Company"
        self.frappe.get_doc = MagicMock(return_value=store)

        pr_doc = MagicMock()
        pr_doc.name = "PR-001"
        self.frappe.new_doc = MagicMock(return_value=pr_doc)

        coupons_mod = _load_module("caz_woosync/sync/coupons.py")

        payload = {
            "id": 1,
            "code": "SAVE10",
            "discount_type": discount_type,
            "amount": amount,
            "date_created": "2026-01-01T00:00:00",
            "date_expires": "",
            "minimum_amount": "",
        }

        coupons_mod.sync_coupon_to_erp("TestStore", "1", payload)
        return pr_doc.update.call_args_list[0][0][0]

    def test_percent_maps_to_discount_percentage(self):
        args = self._run_coupon("percent", "15")
        self.assertEqual(args["rate_or_discount"], "Discount Percentage")
        self.assertEqual(args["discount_percentage"], 15.0)

    def test_fixed_cart_maps_to_discount_amount(self):
        args = self._run_coupon("fixed_cart", "20")
        self.assertEqual(args["rate_or_discount"], "Discount Amount")
        self.assertEqual(args["discount_amount"], 20.0)

    def test_fixed_product_maps_to_discount_amount(self):
        args = self._run_coupon("fixed_product", "5")
        self.assertEqual(args["rate_or_discount"], "Discount Amount")
        self.assertEqual(args["discount_amount"], 5.0)


class TestCouponCodeFormatting(unittest.TestCase):
    """coupon code uppercased; title contains coupon code and woo_coupon_id; max 140 chars."""

    def setUp(self):
        self.frappe = _install_frappe_stub()

    def test_coupon_code_uppercased_and_title_format(self):
        self.frappe.db.get_value = MagicMock(return_value=None)

        store = MagicMock()
        store.company = "Test Company"
        self.frappe.get_doc = MagicMock(return_value=store)

        pr_doc = MagicMock()
        pr_doc.name = "PR-001"
        self.frappe.new_doc = MagicMock(return_value=pr_doc)

        coupons_mod = _load_module("caz_woosync/sync/coupons.py")

        payload = {
            "id": 42,
            "code": "summer_sale",
            "discount_type": "percent",
            "amount": "10",
            "date_created": "2026-01-01T00:00:00",
            "date_expires": "",
            "minimum_amount": "",
        }

        coupons_mod.sync_coupon_to_erp("TestStore", "42", payload)

        args = pr_doc.update.call_args_list[0][0][0]
        self.assertEqual(args["coupon_code"], "SUMMER_SALE")
        self.assertIn("summer_sale", args["title"])
        self.assertIn("#42", args["title"])
        self.assertLessEqual(len(args["title"]), 140)

    def test_title_max_140_chars(self):
        self.frappe.db.get_value = MagicMock(return_value=None)

        store = MagicMock()
        store.company = "Test Company"
        self.frappe.get_doc = MagicMock(return_value=store)

        pr_doc = MagicMock()
        pr_doc.name = "PR-001"
        self.frappe.new_doc = MagicMock(return_value=pr_doc)

        coupons_mod = _load_module("caz_woosync/sync/coupons.py")

        long_code = "A" * 200
        payload = {
            "id": 99,
            "code": long_code,
            "discount_type": "percent",
            "amount": "5",
            "date_created": "2026-01-01T00:00:00",
            "date_expires": "",
            "minimum_amount": "",
        }

        coupons_mod.sync_coupon_to_erp("TestStore", "99", payload)

        args = pr_doc.update.call_args_list[0][0][0]
        self.assertLessEqual(len(args["title"]), 140)


class TestCouponExpiryHandling(unittest.TestCase):
    """empty date_expires → no valid_upto; valid date → valid_upto set."""

    def setUp(self):
        self.frappe = _install_frappe_stub()

    def _run_coupon_expiry(self, date_expires):
        self.frappe.db.get_value = MagicMock(return_value=None)

        store = MagicMock()
        store.company = "Test Company"
        self.frappe.get_doc = MagicMock(return_value=store)

        pr_doc = MagicMock()
        pr_doc.name = "PR-001"
        self.frappe.new_doc = MagicMock(return_value=pr_doc)

        coupons_mod = _load_module("caz_woosync/sync/coupons.py")

        payload = {
            "id": 5,
            "code": "EXPIRY",
            "discount_type": "percent",
            "amount": "10",
            "date_created": "2026-01-01T00:00:00",
            "date_expires": date_expires,
            "minimum_amount": "",
        }

        coupons_mod.sync_coupon_to_erp("TestStore", "5", payload)
        return pr_doc

    def test_empty_date_expires_no_valid_upto(self):
        # Track attribute assignments on the doc
        set_attrs = {}

        class TrackingMock(MagicMock):
            def __setattr__(self, name, value):
                if not name.startswith("_") and name not in ("name", "return_value", "side_effect"):
                    set_attrs[name] = value
                super().__setattr__(name, value)

        pr_doc = TrackingMock()
        pr_doc.name = "PR-001"

        self.frappe.db.get_value = MagicMock(return_value=None)
        store = MagicMock()
        store.company = "Test Company"
        self.frappe.get_doc = MagicMock(return_value=store)
        self.frappe.new_doc = MagicMock(return_value=pr_doc)

        coupons_mod = _load_module("caz_woosync/sync/coupons.py")
        payload = {
            "id": 5, "code": "EXPIRY", "discount_type": "percent",
            "amount": "10", "date_created": "2026-01-01T00:00:00",
            "date_expires": "", "minimum_amount": "",
        }
        coupons_mod.sync_coupon_to_erp("TestStore", "5", payload)
        self.assertNotIn("valid_upto", set_attrs)

    def test_valid_date_expires_sets_valid_upto(self):
        set_attrs = {}

        class TrackingMock(MagicMock):
            def __setattr__(self, name, value):
                if not name.startswith("_") and name not in ("name", "return_value", "side_effect"):
                    set_attrs[name] = value
                super().__setattr__(name, value)

        pr_doc = TrackingMock()
        pr_doc.name = "PR-001"

        self.frappe.db.get_value = MagicMock(return_value=None)
        store = MagicMock()
        store.company = "Test Company"
        self.frappe.get_doc = MagicMock(return_value=store)
        self.frappe.new_doc = MagicMock(return_value=pr_doc)

        coupons_mod = _load_module("caz_woosync/sync/coupons.py")
        payload = {
            "id": 5, "code": "EXPIRY", "discount_type": "percent",
            "amount": "10", "date_created": "2026-01-01T00:00:00",
            "date_expires": "2026-12-31T23:59:59", "minimum_amount": "",
        }
        coupons_mod.sync_coupon_to_erp("TestStore", "5", payload)
        self.assertIn("valid_upto", set_attrs)
        self.assertEqual(set_attrs["valid_upto"], "2026-12-31")


class TestTopicRouting(unittest.TestCase):
    """coupon.created → entity_type=Coupon; coupon.updated → Coupon."""

    def setUp(self):
        self.frappe = _install_frappe_stub()

    def _load_receiver(self):
        # Stub security deps
        security_mod = types.ModuleType("caz_woosync.utils.security")
        security_mod.get_client_ip = MagicMock(return_value="127.0.0.1")
        security_mod.is_ip_allowed = MagicMock(return_value=True)
        security_mod.verify_webhook_signature = MagicMock(return_value=True)
        sys.modules["caz_woosync.utils.security"] = security_mod

        settings_mod = types.ModuleType("caz_woosync.doctype.caz_woo_settings.caz_woo_settings")
        settings_mod.get_settings = MagicMock(return_value=MagicMock(
            allowed_webhook_ips="",
            verify_webhook_signature=False,
        ))
        sys.modules["caz_woosync.doctype.caz_woo_settings.caz_woo_settings"] = settings_mod

        return _load_module("caz_woosync/controller/receiver.py")

    def test_coupon_created_maps_to_coupon(self):
        receiver_mod = self._load_receiver()
        result = receiver_mod._topic_to_entity("coupon.created")
        self.assertEqual(result, "Coupon")

    def test_coupon_updated_maps_to_coupon(self):
        receiver_mod = self._load_receiver()
        result = receiver_mod._topic_to_entity("coupon.updated")
        self.assertEqual(result, "Coupon")

    def test_order_maps_to_order(self):
        receiver_mod = self._load_receiver()
        result = receiver_mod._topic_to_entity("order.created")
        self.assertEqual(result, "Order")

    def test_unknown_returns_none(self):
        receiver_mod = self._load_receiver()
        result = receiver_mod._topic_to_entity("unknown.event")
        self.assertIsNone(result)


class TestRefundDispatchRouting(unittest.TestCase):
    """refunded status triggers handle_refund; pending does not."""

    def setUp(self):
        self.frappe = _install_frappe_stub()

    def test_refunded_status_triggers_handle_refund(self):
        # so_name found
        self.frappe.db.get_value = MagicMock(return_value="SO-001")

        accounting_mod = _load_module("caz_woosync/sync/accounting.py")

        with patch.dict(sys.modules, {}):
            refunds_mock = MagicMock()
            refunds_mock.handle_refund = MagicMock()
            sys.modules["caz_woosync.sync.refunds"] = refunds_mock

            accounting_mod.handle_order_status_change(
                "TestStore", "123", "refunded", {"id": 123}
            )

            refunds_mock.handle_refund.assert_called_once_with(
                "TestStore", "123", {"id": 123}
            )

    def test_pending_status_does_not_trigger_handle_refund(self):
        self.frappe.db.get_value = MagicMock(return_value="SO-001")

        accounting_mod = _load_module("caz_woosync/sync/accounting.py")

        refunds_mock = MagicMock()
        refunds_mock.handle_refund = MagicMock()
        sys.modules["caz_woosync.sync.refunds"] = refunds_mock

        accounting_mod.handle_order_status_change(
            "TestStore", "123", "pending", {"id": 123}
        )

        refunds_mock.handle_refund.assert_not_called()


if __name__ == "__main__":
    unittest.main()
