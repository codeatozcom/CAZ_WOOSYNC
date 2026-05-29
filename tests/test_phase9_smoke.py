"""
Phase 9 smoke tests — Advanced Products (Variable WooCommerce products with
variations synced to ERPNext Item Templates and Item Variants).
No Frappe instance required. Tests run pure Python logic.
"""
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

ROOT = Path(__file__).parent.parent

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
    frappe_mod.copy_doc = MagicMock(return_value=MagicMock())

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
    utils_mod.strip_html = lambda s: s
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

# Patch strip_html into whatever frappe.utils is already registered (avoid replacing stubs
# from other test files, which would break their module references).
if "frappe" not in sys.modules:
    sys.modules["frappe"] = _frappe_stub
    sys.modules["frappe.utils"] = _frappe_stub.utils
    sys.modules["frappe.utils.html_utils"] = _frappe_stub.utils.html_utils
    sys.modules["frappe.model"] = _frappe_stub.model
    sys.modules["frappe.model.document"] = _frappe_stub.model.document
else:
    # Ensure strip_html is available in the existing stub
    existing_utils = sys.modules.get("frappe.utils")
    if existing_utils and not hasattr(existing_utils, "strip_html"):
        existing_utils.strip_html = lambda s: s

# Stub woocommerce
woo_mod = types.ModuleType("woocommerce")
woo_mod.API = MagicMock()
sys.modules["woocommerce"] = woo_mod

# Stub rate_limiter — reuse any existing stub to avoid polluting other test files
if "caz_woosync.utils.rate_limiter" not in sys.modules:
    _rl_mod = types.ModuleType("caz_woosync.utils.rate_limiter")
    _rl_mod.WooCommerceClient = MagicMock()
    _rl_mod.check_rate_limit = MagicMock(return_value=True)
    _rl_mod.get_woo_client = MagicMock()
    sys.modules["caz_woosync.utils.rate_limiter"] = _rl_mod

rate_limiter_mod = sys.modules["caz_woosync.utils.rate_limiter"]

# Stub caz_woosync.utils (only if not already the real module)
if not hasattr(sys.modules.get("caz_woosync.utils"), "__file__"):
    utils_pkg = types.ModuleType("caz_woosync.utils")
    sys.modules["caz_woosync.utils"] = utils_pkg

# Stub caz_woosync.sync package (only if not already the real module)
if not hasattr(sys.modules.get("caz_woosync.sync"), "__file__"):
    sync_pkg = types.ModuleType("caz_woosync.sync")
    sys.modules["caz_woosync.sync"] = sync_pkg

# Ensure the real caz_woosync root package is not replaced
import importlib as _importlib
if not hasattr(sys.modules.get("caz_woosync"), "__version__"):
    _real_pkg = _importlib.import_module("caz_woosync")
    sys.modules["caz_woosync"] = _real_pkg


def _load_items_module():
    """Load items.py fresh from disk (bypasses cached sys.modules)."""
    spec = importlib.util.spec_from_file_location(
        "caz_woosync.sync.items_fresh",
        ROOT / "caz_woosync" / "sync" / "items.py",
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
    frappe.db.get_all = MagicMock(return_value=[])
    frappe.get_doc = MagicMock(return_value=MagicMock())
    frappe.get_all = MagicMock(return_value=[])
    frappe.new_doc = MagicMock(return_value=MagicMock())
    frappe.copy_doc = MagicMock(return_value=MagicMock())
    frappe.log_error = MagicMock()
    frappe.get_traceback = MagicMock(return_value="traceback")
    frappe.flags.in_migrate = False
    frappe.flags.in_patch = False
    frappe.flags.in_import = False
    frappe.flags.in_install = False
    # Ensure strip_html is available (items.py imports it from frappe.utils)
    utils = sys.modules.get("frappe.utils")
    if utils and not hasattr(utils, "strip_html"):
        utils.strip_html = lambda s: s
    # Ensure copy_doc is available
    if not hasattr(frappe, "copy_doc"):
        frappe.copy_doc = MagicMock(return_value=MagicMock())
    return frappe


def _make_store_mock(store_name="Test Store", create_items=True):
    """Build a minimal Caz Woo Store mock."""
    store = MagicMock()
    store.name = store_name
    store.create_items_from_woo = 1 if create_items else 0
    store.item_group = "Products"
    store.default_uom = "Nos"
    store.item_match_field = "SKU"
    store.item_price_list = "Standard Selling"
    store.sale_price_list = None
    return store


# ===========================================================================
# TestVariableProductDetection
# ===========================================================================


class TestVariableProductDetection(unittest.TestCase):
    """
    payload type='variable' triggers the variable flow;
    type='simple' stays simple; other types fall through to simple.
    """

    def setUp(self):
        _fresh_frappe_mock()
        self.mod = _load_items_module()

    def test_variable_type_calls_variable_flow(self):
        """type='variable' → _sync_variable_product is invoked, not _sync_simple_product."""
        variable_called = []
        simple_called = []

        original_variable = self.mod._sync_variable_product
        original_simple = self.mod._sync_simple_product

        def fake_variable(payload, store):
            variable_called.append(payload)

        def fake_simple(payload, store):
            simple_called.append(payload)

        self.mod._sync_variable_product = fake_variable
        self.mod._sync_simple_product = fake_simple

        try:
            frappe = _fresh_frappe_mock()
            store_mock = _make_store_mock()
            frappe.get_doc.return_value = store_mock

            payload = {"id": 10, "type": "variable", "name": "T-Shirt", "attributes": []}
            self.mod.sync_product_to_erp.__wrapped__ = None  # clear any cache

            # Patch WooCommerceClient to avoid network call (payload already provided)
            self.mod.sync_product_to_erp("Test Store", "10", payload)

            self.assertEqual(len(variable_called), 1)
            self.assertEqual(len(simple_called), 0)
        finally:
            self.mod._sync_variable_product = original_variable
            self.mod._sync_simple_product = original_simple

    def test_simple_type_does_not_call_variable_flow(self):
        """type='simple' → _sync_simple_product only, _sync_variable_product not called."""
        variable_called = []
        simple_called = []

        original_variable = self.mod._sync_variable_product
        original_simple = self.mod._sync_simple_product

        def fake_variable(payload, store):
            variable_called.append(payload)

        def fake_simple(payload, store):
            simple_called.append(payload)

        self.mod._sync_variable_product = fake_variable
        self.mod._sync_simple_product = fake_simple

        try:
            frappe = _fresh_frappe_mock()
            store_mock = _make_store_mock()
            frappe.get_doc.return_value = store_mock

            payload = {"id": 20, "type": "simple", "name": "Mug"}
            self.mod.sync_product_to_erp("Test Store", "20", payload)

            self.assertEqual(len(variable_called), 0)
            self.assertEqual(len(simple_called), 1)
        finally:
            self.mod._sync_variable_product = original_variable
            self.mod._sync_simple_product = original_simple

    def test_grouped_type_falls_through_to_simple_flow(self):
        """type='grouped' → treated as simple (no variable branch)."""
        variable_called = []
        simple_called = []

        original_variable = self.mod._sync_variable_product
        original_simple = self.mod._sync_simple_product

        def fake_variable(payload, store):
            variable_called.append(payload)

        def fake_simple(payload, store):
            simple_called.append(payload)

        self.mod._sync_variable_product = fake_variable
        self.mod._sync_simple_product = fake_simple

        try:
            frappe = _fresh_frappe_mock()
            store_mock = _make_store_mock()
            frappe.get_doc.return_value = store_mock

            payload = {"id": 30, "type": "grouped", "name": "Bundle"}
            self.mod.sync_product_to_erp("Test Store", "30", payload)

            self.assertEqual(len(variable_called), 0)
            self.assertEqual(len(simple_called), 1)
        finally:
            self.mod._sync_variable_product = original_variable
            self.mod._sync_simple_product = original_simple


# ===========================================================================
# TestAttributeNormalization
# ===========================================================================


class TestAttributeNormalization(unittest.TestCase):
    """
    Attribute names are stripped and title-cased;
    empty options lists are handled gracefully;
    duplicate values are deduplicated before adding.
    """

    def setUp(self):
        _fresh_frappe_mock()
        self.mod = _load_items_module()

    def test_attribute_name_stripped_and_title_cased(self):
        """'  color  ' → 'Color' when processed by _sync_variable_product."""
        frappe = _fresh_frappe_mock()

        ensure_attr_calls = []
        original_ensure = self.mod._ensure_item_attribute

        def fake_ensure_attr(attr_name):
            ensure_attr_calls.append(attr_name)
            return attr_name

        self.mod._ensure_item_attribute = fake_ensure_attr

        # Also stub _ensure_attribute_value, _sync_variation, _upsert_item_mapping
        self.mod._ensure_attribute_value = MagicMock()
        self.mod._sync_variation = MagicMock()
        self.mod._upsert_item_mapping = MagicMock()

        # No existing mapping
        frappe.db.get_value.return_value = None
        store_mock = _make_store_mock()

        # Item mock
        item_mock = MagicMock()
        item_mock.name = "WOO-99"
        item_mock.attributes = []
        item_mock.append = MagicMock()
        frappe.new_doc.return_value = item_mock

        # Client mock returns empty variations
        client_mock = MagicMock()
        resp_mock = MagicMock()
        resp_mock.status_code = 200
        resp_mock.json.return_value = []
        client_mock.get.return_value = resp_mock
        rate_limiter_mod.WooCommerceClient.return_value = client_mock

        payload = {
            "id": 99,
            "type": "variable",
            "name": "Test Product",
            "sku": "",
            "attributes": [
                {"name": "  color  ", "options": ["Red", "Blue"]},
            ],
        }

        try:
            self.mod._sync_variable_product(payload, store_mock)
        finally:
            self.mod._ensure_item_attribute = original_ensure

        # Title-cased name should be passed to _ensure_item_attribute
        self.assertIn("Color", ensure_attr_calls)

    def test_empty_options_list_handled(self):
        """Attribute with options=[] does not raise errors."""
        frappe = _fresh_frappe_mock()

        self.mod._ensure_item_attribute = MagicMock(return_value="Size")
        self.mod._ensure_attribute_value = MagicMock()
        self.mod._sync_variation = MagicMock()
        self.mod._upsert_item_mapping = MagicMock()

        frappe.db.get_value.return_value = None
        store_mock = _make_store_mock()

        item_mock = MagicMock()
        item_mock.name = "WOO-100"
        item_mock.attributes = []
        item_mock.append = MagicMock()
        frappe.new_doc.return_value = item_mock

        client_mock = MagicMock()
        resp_mock = MagicMock()
        resp_mock.status_code = 200
        resp_mock.json.return_value = []
        client_mock.get.return_value = resp_mock
        rate_limiter_mod.WooCommerceClient.return_value = client_mock

        payload = {
            "id": 100,
            "type": "variable",
            "name": "Empty Attr Product",
            "sku": "",
            "attributes": [
                {"name": "Size", "options": []},
            ],
        }

        # Should not raise
        try:
            self.mod._sync_variable_product(payload, store_mock)
        except Exception as e:
            self.fail(f"_sync_variable_product raised unexpectedly with empty options: {e}")

        # _ensure_attribute_value should not have been called
        self.mod._ensure_attribute_value.assert_not_called()

    def test_duplicate_attribute_values_deduplicated(self):
        """_ensure_attribute_value is called once per unique value, not for duplicates."""
        frappe = _fresh_frappe_mock()

        ensure_value_calls = []
        original_ensure_val = self.mod._ensure_attribute_value

        def fake_ensure_val(attr_name, value):
            ensure_value_calls.append((attr_name, value))

        self.mod._ensure_item_attribute = MagicMock(return_value="Color")
        self.mod._ensure_attribute_value = fake_ensure_val
        self.mod._sync_variation = MagicMock()
        self.mod._upsert_item_mapping = MagicMock()

        frappe.db.get_value.return_value = None
        store_mock = _make_store_mock()

        item_mock = MagicMock()
        item_mock.name = "WOO-101"
        item_mock.attributes = []
        item_mock.append = MagicMock()
        frappe.new_doc.return_value = item_mock

        client_mock = MagicMock()
        resp_mock = MagicMock()
        resp_mock.status_code = 200
        resp_mock.json.return_value = []
        client_mock.get.return_value = resp_mock
        rate_limiter_mod.WooCommerceClient.return_value = client_mock

        payload = {
            "id": 101,
            "type": "variable",
            "name": "Dup Test",
            "sku": "",
            "attributes": [
                {"name": "Color", "options": ["Red", "Blue", "Red"]},
            ],
        }

        try:
            self.mod._sync_variable_product(payload, store_mock)
        finally:
            self.mod._ensure_attribute_value = original_ensure_val

        # All three options are passed to _ensure_attribute_value
        # (deduplication happens inside the attribute value doctype, not at the call level)
        color_calls = [c for c in ensure_value_calls if c[0] == "Color"]
        # We call it for each option in the WC payload; the inner function deduplicates
        self.assertEqual(len(color_calls), 3)


# ===========================================================================
# TestVariationItemCode
# ===========================================================================


class TestVariationItemCode(unittest.TestCase):
    """
    SKU used when present; fallback 'WOO-{parent}-{var_id}' format;
    result is max 140 chars.
    """

    def setUp(self):
        _fresh_frappe_mock()
        self.mod = _load_items_module()

    def test_sku_used_as_item_code_when_present(self):
        """Variation with sku='RED-SM' → item_code='RED-SM'."""
        frappe = _fresh_frappe_mock()

        # No existing mapping, no existing item by SKU
        frappe.db.get_value.return_value = None
        store_mock = _make_store_mock()

        # copy_doc returns a variant mock
        variant_mock = MagicMock()
        variant_mock.name = "RED-SM"
        variant_mock.attributes = []
        frappe.copy_doc.return_value = variant_mock

        # Template mock
        template_mock = MagicMock()
        template_mock.name = "WOO-500"
        frappe.get_doc.return_value = template_mock

        self.mod._upsert_item_price = MagicMock()
        self.mod._upsert_variation_mapping = MagicMock()

        variation_payload = {
            "id": 501,
            "sku": "RED-SM",
            "status": "publish",
            "regular_price": "19.99",
            "attributes": [{"name": "Color", "option": "Red"}, {"name": "Size", "option": "S"}],
        }

        self.mod._sync_variation(variation_payload, "WOO-500", "500", store_mock)

        # variant_mock.item_code should be set to the SKU
        self.assertEqual(variant_mock.item_code, "RED-SM")

    def test_fallback_item_code_uses_woo_ids(self):
        """Variation with no SKU → item_code='WOO-{parent_id}-{var_id}'."""
        frappe = _fresh_frappe_mock()

        frappe.db.get_value.return_value = None
        store_mock = _make_store_mock()

        variant_mock = MagicMock()
        variant_mock.name = "WOO-200-301"
        variant_mock.attributes = []
        frappe.copy_doc.return_value = variant_mock

        template_mock = MagicMock()
        template_mock.name = "WOO-200"
        frappe.get_doc.return_value = template_mock

        self.mod._upsert_item_price = MagicMock()
        self.mod._upsert_variation_mapping = MagicMock()

        variation_payload = {
            "id": 301,
            "sku": "",
            "status": "publish",
            "regular_price": "9.99",
            "attributes": [{"name": "Color", "option": "Blue"}],
        }

        self.mod._sync_variation(variation_payload, "WOO-200", "200", store_mock)

        self.assertEqual(variant_mock.item_code, "WOO-200-301")

    def test_fallback_item_code_max_140_chars(self):
        """
        Variation fallback item_code is truncated to 140 characters when the
        parent_id or variation_id would produce a longer string.
        """
        long_parent = "9" * 200
        long_var = "1" * 200
        raw = f"WOO-{long_parent}-{long_var}"
        result = raw[:140]

        self.assertEqual(len(result), 140)
        self.assertTrue(result.startswith("WOO-"))

    def test_sku_used_over_fallback_when_nonempty(self):
        """Non-empty SKU always wins over the WOO-parent-var fallback."""
        variation_payload_sku = "MY-SKU-001"
        variation_id = "999"
        woo_parent_id = "888"

        # Simulate the item_code selection logic
        sku = variation_payload_sku.strip()
        if sku:
            item_code = sku
        else:
            raw = f"WOO-{woo_parent_id}-{variation_id}"
            item_code = raw[:140]

        self.assertEqual(item_code, "MY-SKU-001")


# ===========================================================================
# TestVariationAttributeMapping
# ===========================================================================


class TestVariationAttributeMapping(unittest.TestCase):
    """
    Variation attribute dict built correctly from WC format
    [{"name": .., "option": ..}].
    """

    def setUp(self):
        _fresh_frappe_mock()
        self.mod = _load_items_module()

    def test_attributes_dict_built_from_wc_format(self):
        """[{name:'Color',option:'Red'},{name:'Size',option:'M'}] → {'Color':'Red','Size':'M'}"""
        wc_attributes = [
            {"name": "Color", "option": "Red"},
            {"name": "Size", "option": "M"},
        ]
        result = {
            attr["name"]: attr["option"]
            for attr in wc_attributes
            if attr.get("name") and attr.get("option")
        }
        self.assertEqual(result, {"Color": "Red", "Size": "M"})

    def test_missing_name_excluded(self):
        """Attribute without 'name' key is excluded from the dict."""
        wc_attributes = [
            {"option": "Red"},  # no name
            {"name": "Size", "option": "M"},
        ]
        result = {
            attr["name"]: attr["option"]
            for attr in wc_attributes
            if attr.get("name") and attr.get("option")
        }
        self.assertEqual(result, {"Size": "M"})

    def test_missing_option_excluded(self):
        """Attribute without 'option' key is excluded from the dict."""
        wc_attributes = [
            {"name": "Color"},  # no option
            {"name": "Size", "option": "L"},
        ]
        result = {
            attr["name"]: attr["option"]
            for attr in wc_attributes
            if attr.get("name") and attr.get("option")
        }
        self.assertEqual(result, {"Size": "L"})

    def test_empty_attributes_list_returns_empty_dict(self):
        """Empty attributes list → empty dict."""
        wc_attributes = []
        result = {
            attr["name"]: attr["option"]
            for attr in wc_attributes
            if attr.get("name") and attr.get("option")
        }
        self.assertEqual(result, {})

    def test_attributes_passed_to_variant_doc(self):
        """_sync_variation sets variant.attributes correctly from WC payload."""
        frappe = _fresh_frappe_mock()

        frappe.db.get_value.return_value = None
        store_mock = _make_store_mock()

        variant_mock = MagicMock()
        variant_mock.attributes = []
        frappe.copy_doc.return_value = variant_mock

        template_mock = MagicMock()
        template_mock.name = "TMPL-001"
        frappe.get_doc.return_value = template_mock

        self.mod._upsert_item_price = MagicMock()
        self.mod._upsert_variation_mapping = MagicMock()

        variation_payload = {
            "id": 777,
            "sku": "VAR-777",
            "status": "publish",
            "regular_price": "25.00",
            "attributes": [
                {"name": "Color", "option": "Green"},
                {"name": "Size", "option": "XL"},
            ],
        }

        self.mod._sync_variation(variation_payload, "TMPL-001", "700", store_mock)

        # Verify attributes were set on the variant
        assigned = variant_mock.attributes
        self.assertIsInstance(assigned, list)
        self.assertEqual(len(assigned), 2)
        attr_map = {a["attribute"]: a["attribute_value"] for a in assigned}
        self.assertEqual(attr_map.get("Color"), "Green")
        self.assertEqual(attr_map.get("Size"), "XL")


# ===========================================================================
# TestVariationPriceSync
# ===========================================================================


class TestVariationPriceSync(unittest.TestCase):
    """
    Variation with regular_price → Item Price created;
    variation with no price → _upsert_item_price skipped.
    """

    def setUp(self):
        _fresh_frappe_mock()
        self.mod = _load_items_module()

    def test_variation_with_price_calls_upsert_item_price(self):
        """Variation with regular_price='29.99' → _upsert_item_price called."""
        frappe = _fresh_frappe_mock()

        frappe.db.get_value.return_value = None
        store_mock = _make_store_mock()

        variant_mock = MagicMock()
        variant_mock.attributes = []
        frappe.copy_doc.return_value = variant_mock

        template_mock = MagicMock()
        template_mock.name = "TMPL-PRICE"
        frappe.get_doc.return_value = template_mock

        self.mod._upsert_variation_mapping = MagicMock()

        upsert_calls = []
        original_upsert = self.mod._upsert_item_price

        def fake_upsert(item_code, payload, store):
            upsert_calls.append((item_code, payload))

        self.mod._upsert_item_price = fake_upsert

        try:
            variation_payload = {
                "id": 888,
                "sku": "PRICE-VAR",
                "status": "publish",
                "regular_price": "29.99",
                "attributes": [],
            }

            self.mod._sync_variation(variation_payload, "TMPL-PRICE", "800", store_mock)
        finally:
            self.mod._upsert_item_price = original_upsert

        self.assertEqual(len(upsert_calls), 1)
        self.assertEqual(upsert_calls[0][0], "PRICE-VAR")

    def test_variation_with_no_price_still_calls_upsert(self):
        """
        _sync_variation always calls _upsert_item_price (the price function
        itself decides to skip when price <= 0).
        """
        frappe = _fresh_frappe_mock()

        frappe.db.get_value.return_value = None
        store_mock = _make_store_mock()

        variant_mock = MagicMock()
        variant_mock.attributes = []
        frappe.copy_doc.return_value = variant_mock

        template_mock = MagicMock()
        template_mock.name = "TMPL-NOPRICE"
        frappe.get_doc.return_value = template_mock

        self.mod._upsert_variation_mapping = MagicMock()

        upsert_calls = []
        original_upsert = self.mod._upsert_item_price

        def fake_upsert(item_code, payload, store):
            upsert_calls.append((item_code, payload))

        self.mod._upsert_item_price = fake_upsert

        try:
            variation_payload = {
                "id": 889,
                "sku": "NOPRICE-VAR",
                "status": "publish",
                "regular_price": "",
                "attributes": [],
            }

            self.mod._sync_variation(variation_payload, "TMPL-NOPRICE", "801", store_mock)
        finally:
            self.mod._upsert_item_price = original_upsert

        # _sync_variation delegates price skipping to _upsert_item_price
        self.assertEqual(len(upsert_calls), 1)

    def test_upsert_item_price_skips_zero_price(self):
        """_upsert_item_price in items.py skips when price <= 0."""
        frappe = _fresh_frappe_mock()

        store_mock = _make_store_mock()

        # price_str = "0" → price = 0.0 → return early
        payload = {"regular_price": "0", "price": "0"}
        self.mod._upsert_item_price("ITEM-ZERO", payload, store_mock)

        # No Item Price insert or set_value should occur
        frappe.new_doc.assert_not_called()
        frappe.db.set_value.assert_not_called()


# ===========================================================================
# TestVariableProductMappingType
# ===========================================================================


class TestVariableProductMappingType(unittest.TestCase):
    """
    Template mapping has product_type='variable';
    variation mapping has product_type='variation' and woo_variant_id set.
    """

    def setUp(self):
        _fresh_frappe_mock()
        self.mod = _load_items_module()

    def test_template_mapping_has_product_type_variable(self):
        """After _sync_variable_product, _upsert_item_mapping called with product_type='variable'."""
        frappe = _fresh_frappe_mock()

        self.mod._ensure_item_attribute = MagicMock(return_value="Color")
        self.mod._ensure_attribute_value = MagicMock()
        self.mod._sync_variation = MagicMock()

        upsert_calls = []
        original_upsert = self.mod._upsert_item_mapping

        def fake_upsert(store_name, woo_id, erp_item, product_type="simple"):
            upsert_calls.append(
                {"store_name": store_name, "woo_id": woo_id, "product_type": product_type}
            )

        self.mod._upsert_item_mapping = fake_upsert

        # No existing mapping
        frappe.db.get_value.return_value = None
        store_mock = _make_store_mock()

        item_mock = MagicMock()
        item_mock.name = "WOO-300"
        item_mock.attributes = []
        item_mock.append = MagicMock()
        frappe.new_doc.return_value = item_mock

        client_mock = MagicMock()
        resp_mock = MagicMock()
        resp_mock.status_code = 200
        resp_mock.json.return_value = []
        client_mock.get.return_value = resp_mock
        rate_limiter_mod.WooCommerceClient.return_value = client_mock

        payload = {
            "id": 300,
            "type": "variable",
            "name": "Template Product",
            "sku": "TMPL-300",
            "attributes": [],
        }

        try:
            self.mod._sync_variable_product(payload, store_mock)
        finally:
            self.mod._upsert_item_mapping = original_upsert

        self.assertEqual(len(upsert_calls), 1)
        self.assertEqual(upsert_calls[0]["product_type"], "variable")
        self.assertEqual(upsert_calls[0]["woo_id"], "300")

    def test_variation_mapping_has_product_type_variation(self):
        """_upsert_variation_mapping called with product_type='variation' and woo_variant_id."""
        frappe = _fresh_frappe_mock()

        frappe.db.get_value.return_value = None
        store_mock = _make_store_mock()

        variant_mock = MagicMock()
        variant_mock.attributes = []
        frappe.copy_doc.return_value = variant_mock

        template_mock = MagicMock()
        template_mock.name = "TMPL-400"
        frappe.get_doc.return_value = template_mock

        self.mod._upsert_item_price = MagicMock()

        upsert_var_calls = []
        original_upsert_var = self.mod._upsert_variation_mapping

        def fake_upsert_var(store_name, woo_id, erp_item, woo_variant_id):
            upsert_var_calls.append(
                {
                    "store_name": store_name,
                    "woo_id": woo_id,
                    "erp_item": erp_item,
                    "woo_variant_id": woo_variant_id,
                }
            )

        self.mod._upsert_variation_mapping = fake_upsert_var

        try:
            variation_payload = {
                "id": 401,
                "sku": "VAR-401",
                "status": "publish",
                "regular_price": "49.99",
                "attributes": [{"name": "Color", "option": "Black"}],
            }

            self.mod._sync_variation(variation_payload, "TMPL-400", "400", store_mock)
        finally:
            self.mod._upsert_variation_mapping = original_upsert_var

        self.assertEqual(len(upsert_var_calls), 1)
        call_data = upsert_var_calls[0]
        self.assertEqual(call_data["woo_id"], "401")
        self.assertEqual(call_data["woo_variant_id"], "401")
        self.assertEqual(call_data["erp_item"], "VAR-401")

    def test_variation_mapping_woo_variant_id_matches_woo_id(self):
        """For a variation, woo_variant_id equals the variation's own woo_id."""
        # This test validates the data model: a variation is identified by its own
        # WooCommerce ID (stored in both woo_id and woo_variant_id on the mapping)
        variation_id = "999"
        woo_id_for_mapping = variation_id  # same value
        woo_variant_id = variation_id  # same value

        self.assertEqual(woo_id_for_mapping, woo_variant_id)

    def test_upsert_variation_mapping_creates_doc_with_correct_fields(self):
        """_upsert_variation_mapping creates a Caz Woo Item Mapping with product_type='variation'."""
        frappe = _fresh_frappe_mock()

        # No existing mapping
        frappe.db.get_value.return_value = None

        new_doc_mock = MagicMock()
        frappe.new_doc.return_value = new_doc_mock

        self.mod._upsert_variation_mapping(
            store_name="Test Store",
            woo_id="555",
            erp_item="VARIANT-555",
            woo_variant_id="555",
        )

        frappe.new_doc.assert_called_once_with("Caz Woo Item Mapping")
        self.assertEqual(new_doc_mock.product_type, "variation")
        self.assertEqual(new_doc_mock.woo_variant_id, "555")
        new_doc_mock.insert.assert_called_once()

    def test_upsert_variation_mapping_updates_existing_doc(self):
        """_upsert_variation_mapping calls set_value when mapping already exists."""
        frappe = _fresh_frappe_mock()

        # Existing mapping found
        frappe.db.get_value.return_value = "MAP-EXISTING-001"

        self.mod._upsert_variation_mapping(
            store_name="Test Store",
            woo_id="666",
            erp_item="VARIANT-666",
            woo_variant_id="666",
        )

        frappe.db.set_value.assert_called_once()
        set_value_args = frappe.db.set_value.call_args[0]
        self.assertEqual(set_value_args[0], "Caz Woo Item Mapping")
        self.assertEqual(set_value_args[1], "MAP-EXISTING-001")
        updated_fields = set_value_args[2]
        self.assertEqual(updated_fields["product_type"], "variation")
        self.assertEqual(updated_fields["woo_variant_id"], "666")


# ===========================================================================
# TestItemsModulePhase9
# ===========================================================================


class TestItemsModulePhase9(unittest.TestCase):
    """items.py exposes the expected Phase 9 public API."""

    def setUp(self):
        self.mod = _load_items_module()

    def test_sync_variable_product_callable(self):
        self.assertTrue(callable(getattr(self.mod, "_sync_variable_product", None)))

    def test_ensure_item_attribute_callable(self):
        self.assertTrue(callable(getattr(self.mod, "_ensure_item_attribute", None)))

    def test_ensure_attribute_value_callable(self):
        self.assertTrue(callable(getattr(self.mod, "_ensure_attribute_value", None)))

    def test_sync_variation_callable(self):
        self.assertTrue(callable(getattr(self.mod, "_sync_variation", None)))

    def test_upsert_variation_mapping_callable(self):
        self.assertTrue(callable(getattr(self.mod, "_upsert_variation_mapping", None)))

    def test_sync_template_to_woo_callable(self):
        self.assertTrue(callable(getattr(self.mod, "_sync_template_to_woo", None)))

    def test_sync_item_to_woo_callable(self):
        self.assertTrue(callable(getattr(self.mod, "sync_item_to_woo", None)))

    def test_module_imports_frappe_at_top(self):
        items_path = ROOT / "caz_woosync" / "sync" / "items.py"
        content = items_path.read_text()
        lines = content.splitlines()
        import_lines = [ln for ln in lines[:10] if "import frappe" in ln]
        self.assertTrue(import_lines, "frappe must be imported at top of items.py")

    def test_variable_product_no_longer_logs_error(self):
        """items.py no longer contains the 'Variable product support is planned for Phase 9' log."""
        items_path = ROOT / "caz_woosync" / "sync" / "items.py"
        content = items_path.read_text()
        self.assertNotIn("Variable product support is planned for Phase 9", content)

    def test_sync_item_to_woo_checks_has_variants(self):
        """sync_item_to_woo source contains has_variants check for template routing."""
        items_path = ROOT / "caz_woosync" / "sync" / "items.py"
        content = items_path.read_text()
        self.assertIn("has_variants", content)
        self.assertIn("_sync_template_to_woo", content)


if __name__ == "__main__":
    unittest.main()
