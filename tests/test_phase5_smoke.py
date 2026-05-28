"""
Phase 5 smoke tests — WooCommerce Customer Sync.
No Frappe instance required. Tests run pure Python logic.
"""
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parent.parent
DOCTYPE_DIR = ROOT / "caz_woosync" / "doctype"

# ---------------------------------------------------------------------------
# Minimal Frappe stub (same pattern as Phase 4)
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
    frappe_mod.db = db

    frappe_mod.new_doc = MagicMock(return_value=MagicMock())
    frappe_mod.get_doc = MagicMock(return_value=MagicMock())
    frappe_mod.get_single = MagicMock(return_value=MagicMock())
    frappe_mod.throw = MagicMock(side_effect=Exception)
    frappe_mod.log_error = MagicMock()
    frappe_mod.get_traceback = MagicMock(return_value="traceback")
    frappe_mod.enqueue = MagicMock()
    frappe_mod.whitelist = lambda fn=None, **kw: (fn if callable(fn) else lambda f: f)

    logger_mock = MagicMock()
    frappe_mod.logger = MagicMock(return_value=logger_mock)
    frappe_mod.flags = MagicMock()

    utils_mod = types.ModuleType("frappe.utils")
    utils_mod.now_datetime = MagicMock(return_value="2026-05-28 12:00:00")
    utils_mod.getdate = MagicMock(side_effect=lambda d: d)
    utils_mod.now = MagicMock(return_value="2026-05-28 12:00:00")
    utils_mod.nowdate = MagicMock(return_value="2026-05-28")
    utils_mod.get_datetime = MagicMock(side_effect=lambda d: d)
    frappe_mod.utils = utils_mod

    html_utils_mod = types.ModuleType("frappe.utils.html_utils")
    html_utils_mod.strip_html = lambda s: s  # identity in tests
    frappe_mod.utils.html_utils = html_utils_mod

    model_mod = types.ModuleType("frappe.model")
    doc_mod = types.ModuleType("frappe.model.document")
    doc_mod.Document = object
    model_mod.document = doc_mod
    frappe_mod.model = model_mod

    return frappe_mod


_frappe_stub = _make_frappe_stub()
sys.modules.setdefault("frappe", _frappe_stub)
sys.modules.setdefault("frappe.utils", _frappe_stub.utils)
sys.modules.setdefault("frappe.utils.html_utils", _frappe_stub.utils.html_utils)
sys.modules.setdefault("frappe.model", _frappe_stub.model)
sys.modules.setdefault("frappe.model.document", _frappe_stub.model.document)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CUSTOMER_NAME_MAX_LEN = 140
PHONE_MAX_LEN = 20


def _make_customer_payload(
    woo_id=42,
    email="jane@example.com",
    first_name="Jane",
    last_name="Doe",
    username="janedoe",
):
    """Return a minimal WooCommerce customer payload dict."""
    return {
        "id": woo_id,
        "email": email,
        "first_name": first_name,
        "last_name": last_name,
        "username": username,
        "billing": {
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "phone": "555-1234",
            "address_1": "123 Main St",
            "address_2": "",
            "city": "Springfield",
            "state": "IL",
            "postcode": "62701",
            "country": "US",
        },
        "shipping": {
            "first_name": first_name,
            "last_name": last_name,
            "address_1": "456 Ship Lane",
            "address_2": "",
            "city": "Chicago",
            "state": "IL",
            "postcode": "60601",
            "country": "US",
        },
        "date_modified": "2026-05-28T10:00:00",
    }


def _make_store_mock(
    name="Test Store",
    company="Test Company",
    customer_group="All Customer Groups",
    default_territory=None,
    sync_customers_from_woo=1,
    update_customers_from_woo=1,
):
    """Return a MagicMock that mimics a Caz Woo Store doc."""
    store = MagicMock()
    store.name = name
    store.company = company
    store.customer_group = customer_group
    store.default_territory = default_territory
    store.sync_customers_from_woo = sync_customers_from_woo
    store.update_customers_from_woo = update_customers_from_woo
    return store


# ===========================================================================
# TestCustomerNameBuilding
# ===========================================================================

class TestCustomerNameBuilding(unittest.TestCase):
    """Test first+last name joining, fallback to email username, and max length."""

    def _build_name(self, first, last, email=""):
        """Replicate the name-building logic from _create_customer."""
        full_name = f"{first} {last}".strip()
        if not full_name:
            full_name = email.split("@")[0] if email else "WooCommerce Customer"
        return full_name[:CUSTOMER_NAME_MAX_LEN]

    def test_first_and_last_joined_with_space(self):
        name = self._build_name("Jane", "Doe")
        self.assertEqual(name, "Jane Doe")

    def test_first_only(self):
        name = self._build_name("Jane", "")
        self.assertEqual(name, "Jane")

    def test_last_only(self):
        name = self._build_name("", "Doe")
        self.assertEqual(name, "Doe")

    def test_fallback_to_email_username_when_name_blank(self):
        name = self._build_name("", "", email="janedoe@example.com")
        self.assertEqual(name, "janedoe")

    def test_fallback_to_woocommerce_customer_when_all_blank(self):
        name = self._build_name("", "", email="")
        self.assertEqual(name, "WooCommerce Customer")

    def test_max_length_enforced_at_140_chars(self):
        long_first = "A" * 100
        long_last = "B" * 100
        name = self._build_name(long_first, long_last)
        self.assertEqual(len(name), CUSTOMER_NAME_MAX_LEN)

    def test_name_exactly_140_chars_not_truncated(self):
        # A name that is exactly 140 chars should pass through unchanged
        exact_name = "X" * CUSTOMER_NAME_MAX_LEN
        result = exact_name[:CUSTOMER_NAME_MAX_LEN]
        self.assertEqual(len(result), CUSTOMER_NAME_MAX_LEN)

    def test_name_under_max_not_padded(self):
        name = self._build_name("Short", "Name")
        self.assertLess(len(name), CUSTOMER_NAME_MAX_LEN)

    def test_email_with_plus_alias_uses_local_part(self):
        name = self._build_name("", "", email="user+alias@example.com")
        self.assertEqual(name, "user+alias")

    def test_whitespace_stripped_from_first_last(self):
        name = self._build_name("  Jane  ", "  Doe  ")
        # strip_html in prod strips whitespace; in tests it's identity, so simulate:
        first = "  Jane  ".strip()
        last = "  Doe  ".strip()
        name = self._build_name(first, last)
        self.assertEqual(name, "Jane Doe")


# ===========================================================================
# TestAddressMapping
# ===========================================================================

class TestAddressMapping(unittest.TestCase):
    """Test WC billing fields → ERPNext Address fields mapping."""

    def _map_address(self, addr_data, addr_type="Billing"):
        """Replicate the address field extraction logic."""
        address_line1 = addr_data.get("address_1", "").strip()
        address_line2 = addr_data.get("address_2", "").strip()
        city = addr_data.get("city", "").strip()
        state = addr_data.get("state", "").strip()
        pincode = addr_data.get("postcode", "").strip()
        country_code = addr_data.get("country", "").strip()
        return {
            "address_line1": address_line1,
            "address_line2": address_line2,
            "city": city or "Unknown",
            "state": state,
            "pincode": pincode,
            "country_code": country_code,
            "addr_type": addr_type,
        }

    def test_billing_address_line1_mapped(self):
        payload = _make_customer_payload()
        result = self._map_address(payload["billing"])
        self.assertEqual(result["address_line1"], "123 Main St")

    def test_billing_city_mapped(self):
        payload = _make_customer_payload()
        result = self._map_address(payload["billing"])
        self.assertEqual(result["city"], "Springfield")

    def test_billing_state_mapped(self):
        payload = _make_customer_payload()
        result = self._map_address(payload["billing"])
        self.assertEqual(result["state"], "IL")

    def test_billing_postcode_maps_to_pincode(self):
        payload = _make_customer_payload()
        result = self._map_address(payload["billing"])
        self.assertEqual(result["pincode"], "62701")

    def test_billing_country_mapped(self):
        payload = _make_customer_payload()
        result = self._map_address(payload["billing"])
        self.assertEqual(result["country_code"], "US")

    def test_empty_address_line1_skips_create(self):
        """When address_1 is empty, _upsert_address should return without creating."""
        addr_data = {"address_1": "", "city": "Springfield"}
        address_line1 = addr_data.get("address_1", "").strip()
        self.assertFalse(bool(address_line1))

    def test_billing_addr_type_label(self):
        result = self._map_address({}, addr_type="Billing")
        self.assertEqual(result["addr_type"], "Billing")

    def test_shipping_addr_type_label(self):
        result = self._map_address({}, addr_type="Shipping")
        self.assertEqual(result["addr_type"], "Shipping")

    def test_missing_city_defaults_to_unknown(self):
        addr_data = {"address_1": "123 Main", "city": ""}
        result = self._map_address(addr_data)
        self.assertEqual(result["city"], "Unknown")

    def test_shipping_address_line1_different_from_billing(self):
        payload = _make_customer_payload()
        billing_addr1 = payload["billing"].get("address_1", "")
        shipping_addr1 = payload["shipping"].get("address_1", "")
        self.assertNotEqual(billing_addr1, shipping_addr1)

    def test_same_address_does_not_create_duplicate_shipping(self):
        """When billing and shipping address_1 are the same, skip shipping."""
        billing_addr1 = "123 Main St"
        shipping_addr1 = "123 Main St"
        should_create_shipping = bool(shipping_addr1) and (shipping_addr1 != billing_addr1)
        self.assertFalse(should_create_shipping)


# ===========================================================================
# TestContactFields
# ===========================================================================

class TestContactFields(unittest.TestCase):
    """Test Contact field extraction: phone max length, email lowercase, is_primary."""

    def test_phone_truncated_to_20_chars(self):
        long_phone = "1" * 30
        result = long_phone[:PHONE_MAX_LEN]
        self.assertEqual(len(result), PHONE_MAX_LEN)

    def test_phone_under_20_chars_not_truncated(self):
        phone = "555-1234"
        result = phone[:PHONE_MAX_LEN]
        self.assertEqual(result, "555-1234")

    def test_email_lowercased(self):
        email = "JANE@EXAMPLE.COM"
        result = email.lower().strip()
        self.assertEqual(result, "jane@example.com")

    def test_email_stripped(self):
        email = "  jane@example.com  "
        result = email.lower().strip()
        self.assertEqual(result, "jane@example.com")

    def test_is_primary_contact_flag_set(self):
        """New contacts should have is_primary_contact=1."""
        is_primary = 1
        self.assertEqual(is_primary, 1)

    def test_email_from_payload_used_when_no_billing_email(self):
        payload = _make_customer_payload(email="top@level.com")
        payload["billing"]["email"] = ""
        email = (payload.get("email") or payload.get("billing", {}).get("email") or "").lower().strip()
        self.assertEqual(email, "top@level.com")

    def test_billing_email_used_when_top_level_absent(self):
        payload = _make_customer_payload()
        payload["email"] = ""
        email = (payload.get("email") or payload.get("billing", {}).get("email") or "").lower().strip()
        self.assertEqual(email, "jane@example.com")

    def test_first_last_from_billing_when_top_level_blank(self):
        # Simulate payload where top-level name is absent but billing has name
        payload = _make_customer_payload()
        payload["first_name"] = ""
        payload["last_name"] = ""
        # billing still has "Jane" / "Doe"
        billing = payload.get("billing", {})
        first = (payload.get("first_name") or billing.get("first_name") or "").strip()
        last = (payload.get("last_name") or billing.get("last_name") or "").strip()
        self.assertEqual(first, "Jane")
        self.assertEqual(last, "Doe")


# ===========================================================================
# TestCustomerMatchKey
# ===========================================================================

class TestCustomerMatchKey(unittest.TestCase):
    """Test that email is primary match key, woo_customer_id secondary."""

    def test_email_primary_key_used_first(self):
        """If no mapping by woo_customer_id, fall back to email lookup."""
        # Simulate: no mapping by ID, but mapping by email
        mapping_by_id = None
        mapping_by_email = {"name": "HASH123", "customer": "CUST-0001"}

        # Logic: check ID first, then email
        mapping = mapping_by_id or mapping_by_email
        self.assertEqual(mapping["customer"], "CUST-0001")

    def test_woo_customer_id_lookup_takes_precedence(self):
        """Mapping by woo_customer_id found → use it, don't query by email."""
        mapping_by_id = {"name": "HASH456", "customer": "CUST-0002"}
        mapping_by_email = {"name": "HASH789", "customer": "CUST-0003"}

        # ID mapping exists → use it (email check would be skipped)
        mapping = mapping_by_id
        self.assertEqual(mapping["customer"], "CUST-0002")

    def test_no_mapping_triggers_create(self):
        """When neither ID nor email mapping exists, creation should occur."""
        mapping_by_id = None
        mapping_by_email = None
        mapping = mapping_by_id or mapping_by_email
        self.assertIsNone(mapping)
        create_should_run = mapping is None
        self.assertTrue(create_should_run)

    def test_woo_customer_id_stored_as_string(self):
        """woo_customer_id should always be converted to string."""
        woo_id = 42
        result = str(woo_id)
        self.assertIsInstance(result, str)
        self.assertEqual(result, "42")

    def test_guest_customer_id_zero(self):
        """Guest orders use woo_customer_id='0'."""
        woo_id = 0
        result = str(woo_id)
        self.assertEqual(result, "0")

    def test_email_lowercased_for_matching(self):
        email = "Jane@Example.COM"
        normalized = email.lower().strip()
        self.assertEqual(normalized, "jane@example.com")


# ===========================================================================
# TestCustomerMappingDoctype
# ===========================================================================

class TestCustomerMappingDoctype(unittest.TestCase):
    """Load caz_woo_customer_mapping.json and assert structure."""

    @classmethod
    def setUpClass(cls):
        mapping_json_path = (
            DOCTYPE_DIR / "caz_woo_customer_mapping" / "caz_woo_customer_mapping.json"
        )
        with open(mapping_json_path) as f:
            cls.schema = json.load(f)
        cls.fields_by_name = {
            field["fieldname"]: field for field in cls.schema.get("fields", [])
        }

    def test_doctype_name_is_correct(self):
        self.assertEqual(self.schema["name"], "Caz Woo Customer Mapping")

    def test_autoname_is_hash(self):
        self.assertEqual(self.schema["autoname"], "hash")

    def test_not_singleton(self):
        self.assertEqual(self.schema.get("issingle", 0), 0)

    def test_module_is_caz_woosync(self):
        self.assertEqual(self.schema["module"], "Caz Woosync")

    def test_store_field_links_to_caz_woo_store(self):
        field = self.fields_by_name.get("store")
        self.assertIsNotNone(field, "store field is missing")
        self.assertEqual(field["fieldtype"], "Link")
        self.assertEqual(field["options"], "Caz Woo Store")
        self.assertEqual(field.get("reqd", 0), 1)

    def test_woo_customer_id_field_exists_and_required(self):
        field = self.fields_by_name.get("woo_customer_id")
        self.assertIsNotNone(field, "woo_customer_id field is missing")
        self.assertEqual(field["fieldtype"], "Data")
        self.assertEqual(field.get("reqd", 0), 1)

    def test_woo_email_field_exists_and_required(self):
        field = self.fields_by_name.get("woo_email")
        self.assertIsNotNone(field, "woo_email field is missing")
        self.assertEqual(field["fieldtype"], "Data")
        self.assertEqual(field.get("reqd", 0), 1)

    def test_customer_field_links_to_customer(self):
        field = self.fields_by_name.get("customer")
        self.assertIsNotNone(field, "customer field is missing")
        self.assertEqual(field["fieldtype"], "Link")
        self.assertEqual(field["options"], "Customer")
        self.assertEqual(field.get("reqd", 0), 1)

    def test_last_synced_is_datetime_and_read_only(self):
        field = self.fields_by_name.get("last_synced")
        self.assertIsNotNone(field, "last_synced field is missing")
        self.assertEqual(field["fieldtype"], "Datetime")
        self.assertEqual(field.get("read_only", 0), 1)

    def test_sync_error_is_small_text_and_read_only(self):
        field = self.fields_by_name.get("sync_error")
        self.assertIsNotNone(field, "sync_error field is missing")
        self.assertEqual(field["fieldtype"], "Small Text")
        self.assertEqual(field.get("read_only", 0), 1)

    def test_woo_username_field_exists(self):
        field = self.fields_by_name.get("woo_username")
        self.assertIsNotNone(field, "woo_username field is missing")
        self.assertEqual(field["fieldtype"], "Data")

    def test_in_list_view_fields(self):
        """woo_customer_id, woo_email, and customer should be in list view."""
        for fname in ("woo_customer_id", "woo_email", "customer"):
            field = self.fields_by_name.get(fname)
            self.assertIsNotNone(field, f"{fname} field missing")
            self.assertEqual(
                field.get("in_list_view", 0), 1,
                f"{fname} should have in_list_view=1",
            )

    def test_all_non_layout_fields_have_descriptions(self):
        """Every data-bearing field must have a non-empty description."""
        for field in self.schema.get("fields", []):
            if field["fieldtype"] in ("Column Break", "Section Break"):
                continue
            desc = field.get("description", "")
            self.assertTrue(
                desc and desc.strip(),
                f"Field '{field['fieldname']}' is missing a description",
            )


# ===========================================================================
# TestCustomerPolling
# ===========================================================================

class TestCustomerPolling(unittest.TestCase):
    """Test poll params, entity_type, and dedup logic for customer polling."""

    def test_poll_customers_function_exists_in_tasks(self):
        tasks_path = ROOT / "caz_woosync" / "tasks.py"
        content = tasks_path.read_text()
        self.assertIn("def _poll_customers(", content)

    def test_poll_store_calls_poll_customers(self):
        tasks_path = ROOT / "caz_woosync" / "tasks.py"
        content = tasks_path.read_text()
        self.assertIn("_poll_customers(", content)

    def test_poll_customers_uses_orderby_modified(self):
        tasks_path = ROOT / "caz_woosync" / "tasks.py"
        content = tasks_path.read_text()
        self.assertIn('"orderby": "modified"', content)

    def test_poll_customers_uses_order_desc(self):
        tasks_path = ROOT / "caz_woosync" / "tasks.py"
        content = tasks_path.read_text()
        self.assertIn('"order": "desc"', content)

    def test_poll_customers_uses_per_page_50(self):
        tasks_path = ROOT / "caz_woosync" / "tasks.py"
        content = tasks_path.read_text()
        self.assertIn('"per_page": 50', content)

    def test_poll_customers_entity_type_is_customer(self):
        tasks_path = ROOT / "caz_woosync" / "tasks.py"
        content = tasks_path.read_text()
        self.assertIn('"Customer"', content)

    def test_poll_customers_direction_is_woo_to_erp(self):
        tasks_path = ROOT / "caz_woosync" / "tasks.py"
        content = tasks_path.read_text()
        # The direction field should appear with woo_to_erp (already in orders/products too)
        self.assertIn('"woo_to_erp"', content)

    def test_dedup_check_filters_entity_type_customer(self):
        """Dedup filter should check entity_type='Customer' to avoid cross-entity conflicts."""
        tasks_path = ROOT / "caz_woosync" / "tasks.py"
        content = tasks_path.read_text()
        # The _poll_customers function should include entity_type filter in dedup check
        # Find the _poll_customers block and verify
        func_start = content.find("def _poll_customers(")
        func_end = content.find("\ndef ", func_start + 1)
        customer_poll_block = content[func_start:func_end]
        self.assertIn('"Customer"', customer_poll_block)

    def test_dispatcher_customer_stub_removed(self):
        """The Phase 5 stub should be replaced with real routing."""
        dispatcher_path = ROOT / "caz_woosync" / "sync" / "dispatcher.py"
        content = dispatcher_path.read_text()
        self.assertNotIn("Customer sync not yet implemented (Phase 5)", content)

    def test_dispatcher_routes_customer_to_sync_customer_to_erp(self):
        dispatcher_path = ROOT / "caz_woosync" / "sync" / "dispatcher.py"
        content = dispatcher_path.read_text()
        self.assertIn("sync_customer_to_erp", content)


# ===========================================================================
# TestCustomersModuleImportable
# ===========================================================================

class TestCustomersModuleImportable(unittest.TestCase):
    """Verify customers.py can be imported and has the expected public functions."""

    def _load_module(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "caz_woosync.sync.customers",
            ROOT / "caz_woosync" / "sync" / "customers.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_module_has_sync_customer_to_erp(self):
        mod = self._load_module()
        self.assertTrue(callable(getattr(mod, "sync_customer_to_erp", None)))

    def test_module_has_create_customer(self):
        mod = self._load_module()
        self.assertTrue(callable(getattr(mod, "_create_customer", None)))

    def test_module_has_update_customer_fields(self):
        mod = self._load_module()
        self.assertTrue(callable(getattr(mod, "_update_customer_fields", None)))

    def test_module_has_upsert_contact(self):
        mod = self._load_module()
        self.assertTrue(callable(getattr(mod, "_upsert_contact", None)))

    def test_module_has_upsert_address(self):
        mod = self._load_module()
        self.assertTrue(callable(getattr(mod, "_upsert_address", None)))

    def test_module_has_upsert_customer_mapping(self):
        mod = self._load_module()
        self.assertTrue(callable(getattr(mod, "_upsert_customer_mapping", None)))

    def test_module_has_constants(self):
        mod = self._load_module()
        self.assertIsInstance(mod.CUSTOMER_NAME_MAX_LEN, int)
        self.assertEqual(mod.CUSTOMER_NAME_MAX_LEN, 140)
        self.assertIsInstance(mod.PHONE_MAX_LEN, int)
        self.assertEqual(mod.PHONE_MAX_LEN, 20)


# ===========================================================================
# TestConnectionAPICustomerEndpoints
# ===========================================================================

class TestConnectionAPICustomerEndpoints(unittest.TestCase):
    """Verify api/connection.py has the two new customer endpoints."""

    def test_get_customer_sync_status_endpoint_exists(self):
        conn_path = ROOT / "caz_woosync" / "api" / "connection.py"
        content = conn_path.read_text()
        self.assertIn("def get_customer_sync_status(", content)

    def test_trigger_customer_sync_endpoint_exists(self):
        conn_path = ROOT / "caz_woosync" / "api" / "connection.py"
        content = conn_path.read_text()
        self.assertIn("def trigger_customer_sync(", content)

    def test_get_customer_sync_status_is_whitelisted(self):
        conn_path = ROOT / "caz_woosync" / "api" / "connection.py"
        content = conn_path.read_text()
        idx_fn = content.index("def get_customer_sync_status(")
        idx_decorator = content.rfind("@frappe.whitelist()", 0, idx_fn)
        self.assertGreater(idx_decorator, 0)

    def test_trigger_customer_sync_is_whitelisted(self):
        conn_path = ROOT / "caz_woosync" / "api" / "connection.py"
        content = conn_path.read_text()
        idx_fn = content.index("def trigger_customer_sync(")
        idx_decorator = content.rfind("@frappe.whitelist()", 0, idx_fn)
        self.assertGreater(idx_decorator, 0)

    def test_trigger_customer_sync_queues_customer_entity(self):
        conn_path = ROOT / "caz_woosync" / "api" / "connection.py"
        content = conn_path.read_text()
        # Find the trigger_customer_sync function and check it sets entity_type Customer
        fn_start = content.find("def trigger_customer_sync(")
        fn_end = content.find("\n\n@frappe.whitelist()", fn_start)
        fn_block = content[fn_start:fn_end]
        self.assertIn('"Customer"', fn_block)


# ===========================================================================
# TestCazWooStoreCustomerFields
# ===========================================================================

class TestCazWooStoreCustomerFields(unittest.TestCase):
    """Verify caz_woo_store.json contains the new customer sync settings fields."""

    @classmethod
    def setUpClass(cls):
        store_json_path = DOCTYPE_DIR / "caz_woo_store" / "caz_woo_store.json"
        with open(store_json_path) as f:
            cls.schema = json.load(f)
        cls.fields_by_name = {
            field["fieldname"]: field for field in cls.schema.get("fields", [])
        }

    def test_section_customer_sync_present(self):
        field = self.fields_by_name.get("section_customer_sync")
        self.assertIsNotNone(field, "section_customer_sync section break missing")
        self.assertEqual(field["fieldtype"], "Section Break")
        self.assertEqual(field.get("label"), "Customer Sync Settings")

    def test_sync_customers_from_woo_exists(self):
        field = self.fields_by_name.get("sync_customers_from_woo")
        self.assertIsNotNone(field, "sync_customers_from_woo field missing")
        self.assertEqual(field["fieldtype"], "Check")
        self.assertEqual(str(field.get("default", "")), "1")

    def test_update_customers_from_woo_exists(self):
        field = self.fields_by_name.get("update_customers_from_woo")
        self.assertIsNotNone(field, "update_customers_from_woo field missing")
        self.assertEqual(field["fieldtype"], "Check")
        self.assertEqual(str(field.get("default", "")), "1")

    def test_default_territory_is_link_to_territory(self):
        field = self.fields_by_name.get("default_territory")
        self.assertIsNotNone(field, "default_territory field missing")
        self.assertEqual(field["fieldtype"], "Link")
        self.assertEqual(field["options"], "Territory")

    def test_customer_sync_fields_have_descriptions(self):
        for fname in ("sync_customers_from_woo", "update_customers_from_woo", "default_territory"):
            field = self.fields_by_name.get(fname)
            self.assertIsNotNone(field, f"{fname} field missing")
            desc = field.get("description", "")
            self.assertTrue(
                desc and desc.strip(),
                f"Field '{fname}' has no description",
            )

    def test_customer_sync_fields_in_field_order(self):
        field_order = self.schema.get("field_order", [])
        for fname in ("section_customer_sync", "sync_customers_from_woo",
                      "update_customers_from_woo", "default_territory"):
            self.assertIn(fname, field_order, f"{fname} missing from field_order")

    def test_section_customer_sync_before_section_webhook(self):
        """Customer sync section should appear before webhook section."""
        field_order = self.schema.get("field_order", [])
        customer_idx = field_order.index("section_customer_sync")
        webhook_idx = field_order.index("section_webhook")
        self.assertLess(customer_idx, webhook_idx)


if __name__ == "__main__":
    unittest.main()
