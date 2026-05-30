"""
Phase 10 smoke tests — Multi-Store improvements.
No Frappe instance required. Tests run pure Python logic via mocking.
"""
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

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

    utils_mod = types.ModuleType("frappe.utils")
    utils_mod.now_datetime = MagicMock(return_value="2026-05-29 12:00:00")
    utils_mod.now = MagicMock(return_value="2026-05-29 12:00:00")
    utils_mod.today = MagicMock(return_value="2026-05-29")
    utils_mod.get_url = MagicMock(return_value="https://erp.example.com")
    frappe_mod.utils = utils_mod

    model_mod = types.ModuleType("frappe.model")
    doc_mod = types.ModuleType("frappe.model.document")
    doc_mod.Document = object
    model_mod.document = doc_mod
    frappe_mod.model = model_mod

    return frappe_mod


if "frappe" not in sys.modules:
    _stub = _make_frappe_stub()
    sys.modules["frappe"] = _stub
    sys.modules["frappe.utils"] = _stub.utils
    sys.modules["frappe.model"] = _stub.model
    sys.modules["frappe.model.document"] = _stub.model.document


def _frappe():
    return sys.modules["frappe"]


def _reset_frappe():
    f = _frappe()
    f.db.get_value = MagicMock(return_value=None)
    f.db.exists = MagicMock(return_value=False)
    f.db.set_value = MagicMock()
    f.db.sql = MagicMock(return_value=[])
    f.db.get_all = MagicMock(return_value=[])
    f.get_all = MagicMock(return_value=[])
    f.get_doc = MagicMock(return_value=MagicMock())
    f.throw = MagicMock(side_effect=Exception)
    return f


# ---------------------------------------------------------------------------
# Load store_manager module fresh (bypasses any cached sys.modules)
# ---------------------------------------------------------------------------


def _load_store_manager():
    # Stub caz_woosync.utils package before loading the module
    if "caz_woosync.utils" not in sys.modules:
        pkg = types.ModuleType("caz_woosync.utils")
        sys.modules["caz_woosync.utils"] = pkg

    spec = importlib.util.spec_from_file_location(
        "caz_woosync.utils.store_manager_fresh",
        ROOT / "caz_woosync" / "utils" / "store_manager.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Load caz_woo_store controller fresh
# ---------------------------------------------------------------------------


def _load_caz_woo_store():
    # Ensure frappe.model.document stub is present
    if "frappe.model.document" not in sys.modules:
        f = _frappe()
        sys.modules["frappe.model.document"] = f.model.document
    if "frappe.utils" not in sys.modules:
        f = _frappe()
        sys.modules["frappe.utils"] = f.utils

    spec = importlib.util.spec_from_file_location(
        "caz_woo_store_controller_fresh",
        ROOT / "caz_woosync" / "doctype" / "caz_woo_store" / "caz_woo_store.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ===========================================================================
# TestStoreManagerFunctions — module has expected public API
# ===========================================================================


class TestStoreManagerFunctions(unittest.TestCase):
    """store_manager.py exposes get_active_stores, get_store_for_item, get_store_health."""

    def setUp(self):
        _reset_frappe()
        self.mod = _load_store_manager()

    def test_get_active_stores_callable(self):
        self.assertTrue(callable(getattr(self.mod, "get_active_stores", None)))

    def test_get_store_for_item_callable(self):
        self.assertTrue(callable(getattr(self.mod, "get_store_for_item", None)))

    def test_get_store_for_customer_callable(self):
        self.assertTrue(callable(getattr(self.mod, "get_store_for_customer", None)))

    def test_get_store_health_callable(self):
        self.assertTrue(callable(getattr(self.mod, "get_store_health", None)))

    def test_get_all_stores_health_callable(self):
        self.assertTrue(callable(getattr(self.mod, "get_all_stores_health", None)))

    def test_detect_item_conflicts_callable(self):
        self.assertTrue(callable(getattr(self.mod, "detect_item_conflicts", None)))

    def test_module_imports_frappe_at_top(self):
        content = (ROOT / "caz_woosync" / "utils" / "store_manager.py").read_text()
        import_lines = [ln for ln in content.splitlines()[:10] if "import frappe" in ln]
        self.assertTrue(import_lines, "frappe must be imported at top of store_manager.py")


# ===========================================================================
# TestStoreHealthSchema — get_store_health returns correct keys
# ===========================================================================


class TestStoreHealthSchema(unittest.TestCase):
    """get_store_health() returns a dict with all required top-level keys."""

    REQUIRED_KEYS = {
        "store_name",
        "is_active",
        "connection_status",
        "queue_stats",
        "mapping_counts",
        "last_sync_time",
        "last_connection_check",
    }

    def setUp(self):
        f = _reset_frappe()
        # db.get_value returns a dict for the store record query
        f.db.get_value = MagicMock(return_value={
            "is_active": 1,
            "connection_status": "Connected",
            "last_connection_check": "2026-05-29 10:00:00",
        })
        # db.sql returns empty result sets
        f.db.sql = MagicMock(return_value=[])
        self.mod = _load_store_manager()

    def test_all_required_keys_present(self):
        health = self.mod.get_store_health("Test Store")
        missing = self.REQUIRED_KEYS - set(health.keys())
        self.assertFalse(missing, f"Missing keys: {missing}")

    def test_store_name_matches_input(self):
        health = self.mod.get_store_health("My Store")
        self.assertEqual(health["store_name"], "My Store")

    def test_is_active_is_bool(self):
        health = self.mod.get_store_health("Test Store")
        self.assertIsInstance(health["is_active"], bool)


# ===========================================================================
# TestQueueStatKeys — queue_stats sub-dict has correct keys
# ===========================================================================


class TestQueueStatKeys(unittest.TestCase):
    REQUIRED_KEYS = {"queued", "processing", "failed", "done_today"}

    def setUp(self):
        f = _reset_frappe()
        f.db.get_value = MagicMock(return_value={
            "is_active": 1,
            "connection_status": "Connected",
            "last_connection_check": None,
        })
        f.db.sql = MagicMock(return_value=[])
        self.mod = _load_store_manager()

    def test_queue_stats_has_all_keys(self):
        health = self.mod.get_store_health("S1")
        qs = health["queue_stats"]
        missing = self.REQUIRED_KEYS - set(qs.keys())
        self.assertFalse(missing, f"Missing queue_stats keys: {missing}")

    def test_queue_stat_values_are_ints(self):
        health = self.mod.get_store_health("S1")
        for k, v in health["queue_stats"].items():
            self.assertIsInstance(v, int, f"queue_stats['{k}'] should be int")


# ===========================================================================
# TestMappingCountKeys — mapping_counts sub-dict has correct keys
# ===========================================================================


class TestMappingCountKeys(unittest.TestCase):
    REQUIRED_KEYS = {"items", "orders", "customers"}

    def setUp(self):
        f = _reset_frappe()
        f.db.get_value = MagicMock(return_value={
            "is_active": 1,
            "connection_status": "Untested",
            "last_connection_check": None,
        })
        f.db.sql = MagicMock(return_value=[])
        self.mod = _load_store_manager()

    def test_mapping_counts_has_all_keys(self):
        health = self.mod.get_store_health("S2")
        mc = health["mapping_counts"]
        missing = self.REQUIRED_KEYS - set(mc.keys())
        self.assertFalse(missing, f"Missing mapping_counts keys: {missing}")

    def test_mapping_count_values_are_ints(self):
        health = self.mod.get_store_health("S2")
        for k, v in health["mapping_counts"].items():
            self.assertIsInstance(v, int, f"mapping_counts['{k}'] should be int")


# ===========================================================================
# TestStoreUrlValidation — _validate_url rejects http, allows https
# ===========================================================================


class TestStoreUrlValidation(unittest.TestCase):
    """_validate_url raises for http://, passes for https://, skips empty."""

    def setUp(self):
        f = _reset_frappe()
        # throw raises Exception (side_effect) so we can catch it
        f.throw = MagicMock(side_effect=Exception("URL must start with https"))
        f.db.get_value = MagicMock(return_value=None)
        self.store_mod = _load_caz_woo_store()
        self.store_cls = self.store_mod.CazWooStore

    def _make_store(self, url, name="Test Store", is_active=1):
        store = self.store_cls.__new__(self.store_cls)
        store.woo_url = url
        store.name = name
        store.is_active = is_active
        store.woo_api_version = "wc/v3"
        return store

    def test_http_url_raises(self):
        # HTTP is now allowed (for local dev); only invalid schemes raise
        store = self._make_store("ftp://example.com")
        with self.assertRaises(Exception):
            store._validate_url()
        _frappe().throw.assert_called_once()

    def test_https_url_passes(self):
        store = self._make_store("https://example.com")
        # Should not raise, should not call frappe.throw
        store._validate_url()
        _frappe().throw.assert_not_called()

    def test_empty_url_skipped(self):
        store = self._make_store("")
        # Empty URL: no throw
        store._validate_url()
        _frappe().throw.assert_not_called()

    def test_none_url_skipped(self):
        store = self._make_store(None)
        store._validate_url()
        _frappe().throw.assert_not_called()


# ===========================================================================
# TestDuplicateUrlDetection — _validate_no_duplicate_url raises on conflict
# ===========================================================================


class TestDuplicateUrlDetection(unittest.TestCase):
    """_validate_no_duplicate_url raises when another active store has same URL."""

    def setUp(self):
        f = _reset_frappe()
        f.throw = MagicMock(side_effect=Exception("Duplicate URL"))
        self.store_mod = _load_caz_woo_store()
        self.store_cls = self.store_mod.CazWooStore

    def _make_store(self, url, name="Store A"):
        store = self.store_cls.__new__(self.store_cls)
        store.woo_url = url
        store.name = name
        store.is_active = 1
        store.woo_api_version = "wc/v3"
        return store

    def test_duplicate_url_raises(self):
        """Another active store has same URL → throw is called."""
        f = _frappe()
        f.db.get_value = MagicMock(return_value="Store B")  # conflict found
        store = self._make_store("https://shop.example.com", name="Store A")
        with self.assertRaises(Exception):
            store._validate_no_duplicate_url()
        f.throw.assert_called_once()

    def test_unique_url_passes(self):
        """No other store with same URL → no throw."""
        f = _frappe()
        f.db.get_value = MagicMock(return_value=None)  # no conflict
        store = self._make_store("https://unique.example.com", name="Store A")
        store._validate_no_duplicate_url()
        f.throw.assert_not_called()

    def test_empty_url_skipped(self):
        """Empty URL → skip check entirely."""
        f = _frappe()
        f.db.get_value = MagicMock(return_value="Store B")
        store = self._make_store("", name="Store A")
        store._validate_no_duplicate_url()
        f.throw.assert_not_called()


# ===========================================================================
# TestConflictDetection — detect_item_conflicts logic
# ===========================================================================


class TestConflictDetection(unittest.TestCase):
    """detect_item_conflicts returns conflicts for multi-store items, empty for single."""

    def setUp(self):
        _reset_frappe()
        self.mod = _load_store_manager()

    def test_single_store_no_conflict(self):
        """Item mapped to only one store → empty conflict list."""
        f = _frappe()
        f.get_all = MagicMock(return_value=[
            {"store": "Store A", "sync_direction": "erp_to_woo", "erp_item": "ITEM-001"},
        ])
        result = self.mod.detect_item_conflicts("ITEM-001")
        self.assertEqual(result, [])

    def test_two_stores_same_price_no_conflict(self):
        """Two stores both erp_to_woo with same price → no conflict."""
        f = _frappe()
        f.get_all = MagicMock(return_value=[
            {"store": "Store A", "sync_direction": "erp_to_woo", "erp_item": "ITEM-002"},
            {"store": "Store B", "sync_direction": "erp_to_woo", "erp_item": "ITEM-002"},
        ])
        # price_list for each store
        f.db.get_value = MagicMock(side_effect=lambda doctype, name, field: {
            ("Caz Woo Store", "Store A", "item_price_list"): "Standard Selling",
            ("Caz Woo Store", "Store B", "item_price_list"): "Standard Selling",
            ("Item Price", {"item_code": "ITEM-002", "price_list": "Standard Selling"}, "price_list_rate"): 100.0,
        }.get((doctype, name, field)))

        # Patch _get_item_price_for_store to return same price for both
        self.mod._get_item_price_for_store = lambda item, store: 100.0

        result = self.mod.detect_item_conflicts("ITEM-002")
        self.assertEqual(result, [])

    def test_two_stores_different_price_conflict(self):
        """Two stores both erp_to_woo with different prices → conflict returned."""
        f = _frappe()
        f.get_all = MagicMock(return_value=[
            {"store": "Store A", "sync_direction": "erp_to_woo", "erp_item": "ITEM-003"},
            {"store": "Store B", "sync_direction": "erp_to_woo", "erp_item": "ITEM-003"},
        ])

        # Patch _get_item_price_for_store to return different prices
        def fake_price(item, store):
            return 100.0 if store == "Store A" else 200.0

        self.mod._get_item_price_for_store = fake_price

        result = self.mod.detect_item_conflicts("ITEM-003")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["conflict"], "price_mismatch")
        self.assertIn("Store A", [result[0]["store_a"], result[0]["store_b"]])
        self.assertIn("Store B", [result[0]["store_a"], result[0]["store_b"]])

    def test_no_mappings_returns_empty(self):
        """Item with no mappings → empty list."""
        f = _frappe()
        f.get_all = MagicMock(return_value=[])
        result = self.mod.detect_item_conflicts("ITEM-UNKNOWN")
        self.assertEqual(result, [])


# ===========================================================================
# TestDashboardPageSchema — caz_woo_dashboard.json is valid
# ===========================================================================


class TestDashboardPageSchema(unittest.TestCase):
    """caz_woo_dashboard.json has correct name, module, and roles."""

    def setUp(self):
        json_path = ROOT / "caz_woosync" / "page" / "caz_woo_dashboard" / "caz_woo_dashboard.json"
        with open(json_path) as f:
            self.schema = json.load(f)

    def test_name_is_caz_woo_dashboard(self):
        self.assertEqual(self.schema.get("name"), "caz-woo-dashboard")

    def test_module_is_caz_woosync(self):
        self.assertEqual(self.schema.get("module"), "Caz Woosync")

    def test_roles_is_list_of_dicts(self):
        roles = self.schema.get("roles", [])
        self.assertIsInstance(roles, list)
        self.assertGreater(len(roles), 0)
        for r in roles:
            self.assertIsInstance(r, dict)
            self.assertIn("role", r)

    def test_has_system_manager_role(self):
        roles = [r["role"] for r in self.schema.get("roles", [])]
        self.assertIn("System Manager", roles)

    def test_has_administrator_role(self):
        roles = [r["role"] for r in self.schema.get("roles", [])]
        self.assertIn("Administrator", roles)

    def test_doctype_is_page(self):
        self.assertEqual(self.schema.get("doctype"), "Page")


if __name__ == "__main__":
    unittest.main()
