"""
Phase 7 smoke tests — Accounting Sync (Sales Invoice + Payment Entry).
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
    db.after_commit = MagicMock(side_effect=lambda fn: fn())
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


def _load_accounting_module():
    """Load accounting.py fresh from disk (bypasses cached sys.modules)."""
    spec = importlib.util.spec_from_file_location(
        "caz_woosync.sync.accounting_fresh",
        ROOT / "caz_woosync" / "sync" / "accounting.py",
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
    frappe.get_doc = MagicMock(return_value=MagicMock())
    frappe.get_all = MagicMock(return_value=[])
    frappe.new_doc = MagicMock(return_value=MagicMock())
    frappe.log_error = MagicMock()
    frappe.get_traceback = MagicMock(return_value="traceback")
    return frappe


# ===========================================================================
# TestPaymentMethodMapping
# ===========================================================================


class TestPaymentMethodMapping(unittest.TestCase):
    """COD methods produce no PE; card → Credit Card; PayPal → PayPal; unknown → Wire Transfer."""

    def setUp(self):
        self.mod = _load_accounting_module()

    def test_cod_method_is_in_cod_set(self):
        self.assertIn("cod", self.mod._COD_METHODS)

    def test_cash_on_delivery_is_in_cod_set(self):
        self.assertIn("cash_on_delivery", self.mod._COD_METHODS)

    def test_empty_string_is_in_cod_set(self):
        self.assertIn("", self.mod._COD_METHODS)

    def test_credit_card_maps_to_credit_card(self):
        result = self.mod._map_payment_method("credit_card")
        self.assertEqual(result, "Credit Card")

    def test_stripe_maps_to_credit_card(self):
        result = self.mod._map_payment_method("stripe")
        self.assertEqual(result, "Credit Card")

    def test_paypal_maps_to_paypal(self):
        result = self.mod._map_payment_method("paypal")
        self.assertEqual(result, "PayPal")

    def test_paypal_standard_maps_to_paypal(self):
        result = self.mod._map_payment_method("paypal_standard")
        self.assertEqual(result, "PayPal")

    def test_ppec_paypal_maps_to_paypal(self):
        result = self.mod._map_payment_method("ppec_paypal")
        self.assertEqual(result, "PayPal")

    def test_unknown_method_falls_back_to_wire_transfer(self):
        result = self.mod._map_payment_method("bacs")
        self.assertEqual(result, "Wire Transfer")

    def test_unknown_method_cheque_falls_back_to_wire_transfer(self):
        result = self.mod._map_payment_method("cheque")
        self.assertEqual(result, "Wire Transfer")

    def test_title_with_credit_card_text_maps_to_credit_card(self):
        result = self.mod._map_payment_method("unknown_method", "Credit Card via Gateway")
        self.assertEqual(result, "Credit Card")

    def test_title_with_paypal_text_maps_to_paypal(self):
        result = self.mod._map_payment_method("unknown_method", "Pay with PayPal")
        self.assertEqual(result, "PayPal")

    def test_title_stripe_text_maps_to_credit_card(self):
        result = self.mod._map_payment_method("unknown_method", "Stripe Gateway")
        self.assertEqual(result, "Credit Card")


# ===========================================================================
# TestCODSkipLogic
# ===========================================================================


class TestCODSkipLogic(unittest.TestCase):
    """COD/empty payment method → PE is skipped; non-COD → PE creation is attempted."""

    def setUp(self):
        _fresh_frappe_mock()
        self.mod = _load_accounting_module()

    def test_cod_payment_method_returns_none(self):
        """payment_method='cod' → skip PE, return None."""
        frappe = sys.modules["frappe"]

        store_mock = MagicMock()
        store_mock.auto_create_payment = 1
        frappe.get_doc.return_value = store_mock

        result = self.mod.create_payment_from_order(
            "Test Store",
            "SO-001",
            {"payment_method": "cod", "total": "100.00", "id": 42},
        )
        self.assertIsNone(result)

    def test_empty_payment_method_returns_none(self):
        """payment_method='' → skip PE, return None."""
        frappe = sys.modules["frappe"]

        store_mock = MagicMock()
        store_mock.auto_create_payment = 1
        frappe.get_doc.return_value = store_mock

        result = self.mod.create_payment_from_order(
            "Test Store",
            "SO-001",
            {"payment_method": "", "total": "50.00", "id": 99},
        )
        self.assertIsNone(result)

    def test_cash_on_delivery_method_returns_none(self):
        """payment_method='cash_on_delivery' → skip PE."""
        frappe = sys.modules["frappe"]

        store_mock = MagicMock()
        store_mock.auto_create_payment = 1
        frappe.get_doc.return_value = store_mock

        result = self.mod.create_payment_from_order(
            "Test Store",
            "SO-001",
            {"payment_method": "cash_on_delivery", "total": "75.00", "id": 55},
        )
        self.assertIsNone(result)

    def test_stripe_payment_method_proceeds_to_lookup(self):
        """payment_method='stripe' → not COD, tries to look up SI name."""
        frappe = sys.modules["frappe"]

        store_mock = MagicMock()
        store_mock.auto_create_payment = 1
        frappe.get_doc.return_value = store_mock
        # No SI in mapping → returns None but for a different reason
        frappe.db.get_value.return_value = None

        result = self.mod.create_payment_from_order(
            "Test Store",
            "SO-001",
            {"payment_method": "stripe", "total": "120.00", "id": 77},
        )
        # Returns None because no SI found, but COD check did not trigger
        self.assertIsNone(result)
        # db.get_value should have been called to look up SI mapping
        frappe.db.get_value.assert_called()


# ===========================================================================
# TestOrderStatusToAccounting
# ===========================================================================


class TestOrderStatusToAccounting(unittest.TestCase):
    """'completed' triggers invoice+payment; 'processing' invoice only; others handled correctly."""

    def setUp(self):
        _fresh_frappe_mock()
        self.mod = _load_accounting_module()

    def _make_store_mock(self, auto_invoice=1, auto_payment=1):
        store = MagicMock()
        store.auto_create_invoice = auto_invoice
        store.auto_create_payment = auto_payment
        store.auto_submit_invoice = 0
        store.auto_submit_payment = 0
        store.receivable_account = "Debtors - TEST"
        store.payment_account = "Bank - TEST"
        store.invoice_naming_series = "SINV-CAZWOO-.YYYY.-"
        store.income_account = "Sales - TEST"
        return store

    def test_completed_status_calls_both_invoice_and_payment(self):
        """'completed' → create_invoice_from_order AND create_payment_from_order both called."""
        frappe = sys.modules["frappe"]

        # SO name returned from mapping
        frappe.db.get_value.return_value = "SO-00001"

        called = []
        original_create_invoice = self.mod.create_invoice_from_order
        original_create_payment = self.mod.create_payment_from_order

        def fake_invoice(store_name, so_name):
            called.append(("invoice", store_name, so_name))
            return "SINV-00001"

        def fake_payment(store_name, so_name, payload):
            called.append(("payment", store_name, so_name))
            return "PE-00001"

        self.mod.create_invoice_from_order = fake_invoice
        self.mod.create_payment_from_order = fake_payment

        try:
            self.mod.handle_order_status_change(
                "Test Store", "123", "completed",
                {"id": 123, "status": "completed", "total": "99.00"},
            )
        finally:
            self.mod.create_invoice_from_order = original_create_invoice
            self.mod.create_payment_from_order = original_create_payment

        action_types = [c[0] for c in called]
        self.assertIn("invoice", action_types)
        self.assertIn("payment", action_types)

    def test_processing_status_calls_invoice_only(self):
        """'processing' → create_invoice_from_order only."""
        frappe = sys.modules["frappe"]
        frappe.db.get_value.return_value = "SO-00001"

        called = []
        original_create_invoice = self.mod.create_invoice_from_order
        original_create_payment = self.mod.create_payment_from_order

        def fake_invoice(store_name, so_name):
            called.append("invoice")
            return "SINV-00001"

        def fake_payment(store_name, so_name, payload):
            called.append("payment")
            return "PE-00001"

        self.mod.create_invoice_from_order = fake_invoice
        self.mod.create_payment_from_order = fake_payment

        try:
            self.mod.handle_order_status_change(
                "Test Store", "124", "processing",
                {"id": 124, "status": "processing"},
            )
        finally:
            self.mod.create_invoice_from_order = original_create_invoice
            self.mod.create_payment_from_order = original_create_payment

        self.assertIn("invoice", called)
        self.assertNotIn("payment", called)

    def test_cancelled_status_triggers_cancel_path(self):
        """'cancelled' → _cancel_accounting_docs is called."""
        frappe = sys.modules["frappe"]
        frappe.db.get_value.return_value = "SO-00001"

        cancel_called = []
        original_cancel = self.mod._cancel_accounting_docs

        def fake_cancel(store_name, so_name, woo_order_id):
            cancel_called.append((store_name, so_name))

        self.mod._cancel_accounting_docs = fake_cancel

        try:
            self.mod.handle_order_status_change(
                "Test Store", "125", "cancelled",
                {"id": 125, "status": "cancelled"},
            )
        finally:
            self.mod._cancel_accounting_docs = original_cancel

        self.assertEqual(len(cancel_called), 1)
        self.assertEqual(cancel_called[0][1], "SO-00001")

    def test_refunded_status_is_noop(self):
        """'refunded' → no invoice/payment created, just logs a warning."""
        frappe = sys.modules["frappe"]
        frappe.db.get_value.return_value = "SO-00001"

        invoice_created = []
        original_create_invoice = self.mod.create_invoice_from_order

        def fake_invoice(store_name, so_name):
            invoice_created.append(so_name)
            return "SINV-00001"

        self.mod.create_invoice_from_order = fake_invoice

        try:
            self.mod.handle_order_status_change(
                "Test Store", "126", "refunded",
                {"id": 126, "status": "refunded"},
            )
        finally:
            self.mod.create_invoice_from_order = original_create_invoice

        self.assertEqual(len(invoice_created), 0)

    def test_pending_status_is_noop(self):
        """'pending' → no accounting action at all."""
        frappe = sys.modules["frappe"]
        frappe.db.get_value.return_value = "SO-00001"

        invoice_created = []
        original_create_invoice = self.mod.create_invoice_from_order

        def fake_invoice(store_name, so_name):
            invoice_created.append(so_name)
            return "SINV-00001"

        self.mod.create_invoice_from_order = fake_invoice

        try:
            self.mod.handle_order_status_change(
                "Test Store", "127", "pending",
                {"id": 127, "status": "pending"},
            )
        finally:
            self.mod.create_invoice_from_order = original_create_invoice

        self.assertEqual(len(invoice_created), 0)

    def test_no_so_mapping_logs_warning_and_returns(self):
        """No Sales Order in mapping → logs warning, no crash."""
        frappe = sys.modules["frappe"]
        frappe.db.get_value.return_value = None  # no mapping

        # Should not raise
        self.mod.handle_order_status_change(
            "Test Store", "999", "completed",
            {"id": 999, "status": "completed"},
        )
        frappe.logger().warning.assert_called()


# ===========================================================================
# TestInvoiceIdempotency
# ===========================================================================


class TestInvoiceIdempotency(unittest.TestCase):
    """Second call with same SO name returns existing SI, does not create duplicate."""

    def setUp(self):
        _fresh_frappe_mock()
        self.mod = _load_accounting_module()

    def test_existing_si_returned_without_insert(self):
        """If SI already exists for SO, return its name without inserting a new one."""
        frappe = sys.modules["frappe"]

        store_mock = MagicMock()
        store_mock.auto_create_invoice = 1
        frappe.get_doc.return_value = store_mock

        # SI already exists — db.get_value("Sales Invoice Item", ...) returns parent name
        frappe.db.get_value.return_value = "SINV-EXISTING-001"

        result = self.mod.create_invoice_from_order("Test Store", "SO-DUPLICATE-001")

        self.assertEqual(result, "SINV-EXISTING-001")
        frappe.new_doc.assert_not_called()

    def test_no_existing_si_creates_new_one(self):
        """If no SI exists, new_doc is called to create one."""
        frappe = sys.modules["frappe"]

        store_mock = MagicMock()
        store_mock.auto_create_invoice = 1
        store_mock.auto_submit_invoice = 0
        store_mock.receivable_account = "Debtors - T"
        store_mock.income_account = ""
        store_mock.invoice_naming_series = "SINV-CAZWOO-.YYYY.-"

        so_mock = MagicMock()
        so_mock.customer = "Test Customer"
        so_mock.company = "Test Company"
        so_mock.currency = "USD"
        item_mock = MagicMock()
        item_mock.item_code = "ITEM-001"
        item_mock.item_name = "Test Item"
        item_mock.qty = 1
        item_mock.rate = 100.0
        item_mock.income_account = ""
        item_mock.name = "row-001"
        so_mock.items = [item_mock]
        so_mock.taxes = []

        # db.get_value: first call (idempotency check) returns None, next calls return None
        frappe.db.get_value.return_value = None

        call_count = [0]

        def get_doc_side_effect(doctype, name=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return store_mock  # first call is store
            return so_mock  # second call is SO

        frappe.get_doc.side_effect = get_doc_side_effect

        si_mock = MagicMock()
        si_mock.name = "SINV-CAZWOO-2026-001"
        si_mock.items = []
        frappe.new_doc.return_value = si_mock

        result = self.mod.create_invoice_from_order("Test Store", "SO-NEW-001")

        frappe.new_doc.assert_called_once_with("Sales Invoice")
        si_mock.insert.assert_called_once()
        self.assertEqual(result, "SINV-CAZWOO-2026-001")

    def test_idempotency_check_uses_sales_invoice_item_doctype(self):
        """Idempotency check must query 'Sales Invoice Item', not 'Sales Invoice'."""
        acc_path = ROOT / "caz_woosync" / "sync" / "accounting.py"
        content = acc_path.read_text()
        self.assertIn("Sales Invoice Item", content)
        # Specifically in the idempotency check context
        idx = content.find("Sales Invoice Item")
        # The check should be near the top of create_invoice_from_order
        fn_start = content.find("def create_invoice_from_order(")
        self.assertGreater(idx, fn_start, "Sales Invoice Item check must be inside create_invoice_from_order")


# ===========================================================================
# TestPaymentEntryFields
# ===========================================================================


class TestPaymentEntryFields(unittest.TestCase):
    """payment_type='Receive', party_type='Customer', paid_amount from WC total."""

    def setUp(self):
        _fresh_frappe_mock()
        self.mod = _load_accounting_module()

    def test_payment_entry_fields_set_correctly(self):
        """PE fields: payment_type=Receive, party_type=Customer, paid_amount from payload total."""
        frappe = sys.modules["frappe"]

        store_mock = MagicMock()
        store_mock.auto_create_payment = 1
        store_mock.auto_submit_payment = 0
        store_mock.receivable_account = "Debtors - T"
        store_mock.payment_account = "Bank - T"

        si_mock = MagicMock()
        si_mock.customer = "WC Customer"
        si_mock.company = "Test Company"

        def get_doc_side_effect(doctype, name=None):
            if doctype == "Caz Woo Store":
                return store_mock
            if doctype == "Sales Invoice":
                return si_mock
            return MagicMock()

        frappe.get_doc.side_effect = get_doc_side_effect

        # db.get_value returns: first call = SI name from mapping, second = no existing PE
        call_idx = [0]
        def db_get_value_side_effect(*args, **kwargs):
            call_idx[0] += 1
            if call_idx[0] == 1:
                return "SINV-00001"   # SI name from order mapping
            return None               # no existing PE reference

        frappe.db.get_value.side_effect = db_get_value_side_effect

        pe_mock = MagicMock()
        pe_mock.name = "PE-00001"
        pe_mock.references = []
        frappe.new_doc.return_value = pe_mock

        payload = {
            "payment_method": "stripe",
            "payment_method_title": "Credit Card",
            "total": "150.00",
            "id": 42,
            "transaction_id": "txn_abc123",
        }

        result = self.mod.create_payment_from_order("Test Store", "SO-001", payload)

        frappe.new_doc.assert_called_once_with("Payment Entry")
        update_kwargs = pe_mock.update.call_args[0][0]
        self.assertEqual(update_kwargs["payment_type"], "Receive")
        self.assertEqual(update_kwargs["party_type"], "Customer")
        self.assertEqual(update_kwargs["party"], "WC Customer")
        self.assertAlmostEqual(update_kwargs["paid_amount"], 150.0)
        self.assertEqual(update_kwargs["reference_no"], "txn_abc123")
        pe_mock.insert.assert_called_once()
        self.assertEqual(result, "PE-00001")

    def test_payment_entry_references_row_added(self):
        """PE must have a reference row linking to Sales Invoice."""
        frappe = sys.modules["frappe"]

        store_mock = MagicMock()
        store_mock.auto_create_payment = 1
        store_mock.auto_submit_payment = 0
        store_mock.receivable_account = "Debtors - T"
        store_mock.payment_account = "Bank - T"

        si_mock = MagicMock()
        si_mock.customer = "WC Customer"
        si_mock.company = "Test Company"

        def get_doc_side_effect(doctype, name=None):
            if doctype == "Caz Woo Store":
                return store_mock
            return si_mock

        frappe.get_doc.side_effect = get_doc_side_effect

        call_idx = [0]
        def db_get_value_side_effect(*args, **kwargs):
            call_idx[0] += 1
            if call_idx[0] == 1:
                return "SINV-00002"
            return None

        frappe.db.get_value.side_effect = db_get_value_side_effect

        pe_mock = MagicMock()
        pe_mock.name = "PE-00002"
        frappe.new_doc.return_value = pe_mock

        payload = {
            "payment_method": "paypal",
            "total": "80.00",
            "id": 99,
        }

        self.mod.create_payment_from_order("Test Store", "SO-002", payload)

        pe_mock.append.assert_called_once()
        append_args = pe_mock.append.call_args
        self.assertEqual(append_args[0][0], "references")
        ref_row = append_args[0][1]
        self.assertEqual(ref_row["reference_doctype"], "Sales Invoice")
        self.assertEqual(ref_row["reference_name"], "SINV-00002")
        self.assertAlmostEqual(ref_row["allocated_amount"], 80.0)

    def test_zero_total_returns_none(self):
        """WC order with total=0 → skip PE creation, return None."""
        frappe = sys.modules["frappe"]

        store_mock = MagicMock()
        store_mock.auto_create_payment = 1
        frappe.get_doc.return_value = store_mock

        # First db.get_value call: SI name from mapping; second: no existing PE
        call_idx = [0]
        def db_get_value_side_effect(*args, **kwargs):
            call_idx[0] += 1
            if call_idx[0] == 1:
                return "SINV-ZERO"   # SI name from order mapping
            return None               # no existing PE reference

        frappe.db.get_value.side_effect = db_get_value_side_effect

        result = self.mod.create_payment_from_order(
            "Test Store", "SO-ZERO",
            {"payment_method": "stripe", "total": "0", "id": 10},
        )
        self.assertIsNone(result)

    def test_order_id_used_as_reference_when_no_transaction_id(self):
        """When transaction_id is missing, WC order id is used as reference_no."""
        frappe = sys.modules["frappe"]

        store_mock = MagicMock()
        store_mock.auto_create_payment = 1
        store_mock.auto_submit_payment = 0
        store_mock.receivable_account = "Debtors - T"
        store_mock.payment_account = "Bank - T"

        si_mock = MagicMock()
        si_mock.customer = "WC Customer"
        si_mock.company = "Test Company"

        def get_doc_side_effect(doctype, name=None):
            if doctype == "Caz Woo Store":
                return store_mock
            return si_mock

        frappe.get_doc.side_effect = get_doc_side_effect

        call_idx = [0]
        def db_get_value_side_effect(*args, **kwargs):
            call_idx[0] += 1
            if call_idx[0] == 1:
                return "SINV-00003"
            return None

        frappe.db.get_value.side_effect = db_get_value_side_effect

        pe_mock = MagicMock()
        pe_mock.name = "PE-00003"
        frappe.new_doc.return_value = pe_mock

        payload = {
            "payment_method": "stripe",
            "total": "55.00",
            "id": 777,
            # transaction_id intentionally absent
        }

        self.mod.create_payment_from_order("Test Store", "SO-003", payload)

        update_kwargs = pe_mock.update.call_args[0][0]
        self.assertEqual(update_kwargs["reference_no"], "777")


# ===========================================================================
# TestAccountingSettings
# ===========================================================================


class TestAccountingSettings(unittest.TestCase):
    """caz_woo_store.json has all required Phase 7 accounting fields."""

    @classmethod
    def setUpClass(cls):
        store_json_path = DOCTYPE_DIR / "caz_woo_store" / "caz_woo_store.json"
        with open(store_json_path) as f:
            cls.schema = json.load(f)
        cls.fields_by_name = {f["fieldname"]: f for f in cls.schema.get("fields", [])}
        cls.field_order = cls.schema.get("field_order", [])

    # --- Field presence ---

    def test_auto_create_invoice_field_present(self):
        self.assertIn("auto_create_invoice", self.fields_by_name)

    def test_auto_submit_invoice_field_present(self):
        self.assertIn("auto_submit_invoice", self.fields_by_name)

    def test_auto_create_payment_field_present(self):
        self.assertIn("auto_create_payment", self.fields_by_name)

    def test_auto_submit_payment_field_present(self):
        self.assertIn("auto_submit_payment", self.fields_by_name)

    def test_receivable_account_field_present(self):
        self.assertIn("receivable_account", self.fields_by_name)

    def test_payment_account_field_present(self):
        self.assertIn("payment_account", self.fields_by_name)

    def test_invoice_naming_series_field_present(self):
        self.assertIn("invoice_naming_series", self.fields_by_name)

    # --- Field types and defaults ---

    def test_auto_create_invoice_is_check_with_default_1(self):
        f = self.fields_by_name["auto_create_invoice"]
        self.assertEqual(f["fieldtype"], "Check")
        self.assertEqual(str(f.get("default", "")), "1")

    def test_auto_submit_invoice_is_check_with_default_0(self):
        f = self.fields_by_name["auto_submit_invoice"]
        self.assertEqual(f["fieldtype"], "Check")
        self.assertEqual(str(f.get("default", "")), "0")

    def test_auto_create_payment_is_check_with_default_1(self):
        f = self.fields_by_name["auto_create_payment"]
        self.assertEqual(f["fieldtype"], "Check")
        self.assertEqual(str(f.get("default", "")), "1")

    def test_auto_submit_payment_is_check_with_default_0(self):
        f = self.fields_by_name["auto_submit_payment"]
        self.assertEqual(f["fieldtype"], "Check")
        self.assertEqual(str(f.get("default", "")), "0")

    def test_receivable_account_is_link_to_account(self):
        f = self.fields_by_name["receivable_account"]
        self.assertEqual(f["fieldtype"], "Link")
        self.assertEqual(f.get("options"), "Account")

    def test_payment_account_is_link_to_account(self):
        f = self.fields_by_name["payment_account"]
        self.assertEqual(f["fieldtype"], "Link")
        self.assertEqual(f.get("options"), "Account")

    def test_invoice_naming_series_is_data_with_default(self):
        f = self.fields_by_name["invoice_naming_series"]
        self.assertEqual(f["fieldtype"], "Data")
        self.assertIn("SINV-CAZWOO", f.get("default", ""))

    # --- Descriptions non-empty ---

    def test_all_accounting_fields_have_descriptions(self):
        required_fields = [
            "auto_create_invoice",
            "auto_submit_invoice",
            "auto_create_payment",
            "auto_submit_payment",
            "receivable_account",
            "payment_account",
            "invoice_naming_series",
        ]
        for fname in required_fields:
            f = self.fields_by_name[fname]
            desc = f.get("description", "")
            self.assertTrue(
                desc and desc.strip(),
                f"Field '{fname}' has no description in caz_woo_store.json",
            )

    # --- Section present ---

    def test_section_accounting_sync_present(self):
        self.assertIn("section_accounting_sync", self.fields_by_name)
        f = self.fields_by_name["section_accounting_sync"]
        self.assertEqual(f["fieldtype"], "Section Break")

    # --- Field order ---

    def test_accounting_fields_in_field_order(self):
        for fname in (
            "section_accounting_sync",
            "auto_create_invoice",
            "auto_submit_invoice",
            "auto_create_payment",
            "auto_submit_payment",
            "receivable_account",
            "payment_account",
            "invoice_naming_series",
        ):
            self.assertIn(fname, self.field_order, f"{fname} missing from field_order")

    def test_accounting_section_after_inventory_section(self):
        inv_idx = self.field_order.index("section_inventory_sync")
        acc_idx = self.field_order.index("section_accounting_sync")
        self.assertLess(inv_idx, acc_idx)

    def test_accounting_section_before_webhook_section(self):
        acc_idx = self.field_order.index("section_accounting_sync")
        web_idx = self.field_order.index("section_webhook")
        self.assertLess(acc_idx, web_idx)


# ===========================================================================
# TestOrderMappingSiNameField
# ===========================================================================


class TestOrderMappingSiNameField(unittest.TestCase):
    """caz_woo_order_mapping.json has si_name field."""

    @classmethod
    def setUpClass(cls):
        mapping_json = DOCTYPE_DIR / "caz_woo_order_mapping" / "caz_woo_order_mapping.json"
        with open(mapping_json) as f:
            cls.schema = json.load(f)
        cls.fields_by_name = {f["fieldname"]: f for f in cls.schema.get("fields", [])}
        cls.field_order = cls.schema.get("field_order", [])

    def test_si_name_field_present(self):
        self.assertIn("si_name", self.fields_by_name)

    def test_si_name_is_link_to_sales_invoice(self):
        f = self.fields_by_name["si_name"]
        self.assertEqual(f["fieldtype"], "Link")
        self.assertEqual(f.get("options"), "Sales Invoice")

    def test_si_name_in_field_order(self):
        self.assertIn("si_name", self.field_order)

    def test_si_name_has_description(self):
        f = self.fields_by_name["si_name"]
        desc = f.get("description", "")
        self.assertTrue(desc and desc.strip(), "si_name field has no description")


# ===========================================================================
# TestAccountingModuleImportable
# ===========================================================================


class TestAccountingModuleImportable(unittest.TestCase):
    """accounting.py is importable and has the expected public functions."""

    def setUp(self):
        self.mod = _load_accounting_module()

    def test_create_invoice_from_order_callable(self):
        self.assertTrue(callable(getattr(self.mod, "create_invoice_from_order", None)))

    def test_create_payment_from_order_callable(self):
        self.assertTrue(callable(getattr(self.mod, "create_payment_from_order", None)))

    def test_handle_order_status_change_callable(self):
        self.assertTrue(callable(getattr(self.mod, "handle_order_status_change", None)))

    def test_get_default_account_callable(self):
        self.assertTrue(callable(getattr(self.mod, "_get_default_account", None)))

    def test_map_payment_method_callable(self):
        self.assertTrue(callable(getattr(self.mod, "_map_payment_method", None)))

    def test_module_imports_frappe_at_top(self):
        acc_path = ROOT / "caz_woosync" / "sync" / "accounting.py"
        content = acc_path.read_text()
        lines = content.splitlines()
        import_lines = [ln for ln in lines[:10] if "import frappe" in ln]
        self.assertTrue(import_lines, "frappe must be imported at top of accounting.py")

    def test_no_db_commit_in_accounting(self):
        """accounting.py must never call frappe.db.commit() directly."""
        acc_path = ROOT / "caz_woosync" / "sync" / "accounting.py"
        content = acc_path.read_text()
        self.assertNotIn("frappe.db.commit()", content)


# ===========================================================================
# TestOrdersModuleIntegration
# ===========================================================================


class TestOrdersModuleIntegration(unittest.TestCase):
    """orders.py calls handle_order_status_change for accounting-relevant statuses."""

    def test_orders_py_imports_accounting_handle(self):
        """orders.py should import/call handle_order_status_change."""
        orders_path = ROOT / "caz_woosync" / "sync" / "orders.py"
        content = orders_path.read_text()
        self.assertIn("handle_order_status_change", content)

    def test_orders_py_calls_handle_for_completed(self):
        """_update_order_status should call handle_order_status_change for 'completed'."""
        orders_path = ROOT / "caz_woosync" / "sync" / "orders.py"
        content = orders_path.read_text()
        fn_start = content.find("def _update_order_status(")
        fn_block = content[fn_start:fn_start + 1500]
        self.assertIn("handle_order_status_change", fn_block)

    def test_orders_py_calls_handle_for_processing(self):
        """_update_order_status block should cover 'processing' status."""
        orders_path = ROOT / "caz_woosync" / "sync" / "orders.py"
        content = orders_path.read_text()
        fn_start = content.find("def _update_order_status(")
        fn_block = content[fn_start:fn_start + 1500]
        self.assertIn('"processing"', fn_block)

    def test_orders_py_calls_handle_for_cancelled(self):
        """_update_order_status block should cover 'cancelled' status."""
        orders_path = ROOT / "caz_woosync" / "sync" / "orders.py"
        content = orders_path.read_text()
        fn_start = content.find("def _update_order_status(")
        fn_block = content[fn_start:fn_start + 1500]
        self.assertIn('"cancelled"', fn_block)


if __name__ == "__main__":
    unittest.main()
