"""
Phase 11 smoke tests — Bulk Import / Migration Tool.
No Frappe instance required. Tests run pure Python logic via mocking.
"""
import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

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
    db.sql = MagicMock(return_value=[{"cnt": 0}])
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
    f.db.sql = MagicMock(return_value=[{"cnt": 0}])
    f.db.get_all = MagicMock(return_value=[])
    f.get_all = MagicMock(return_value=[])
    f.get_doc = MagicMock(return_value=MagicMock())
    f.throw = MagicMock(side_effect=Exception)
    f.log_error = MagicMock()
    f.enqueue = MagicMock()
    f.new_doc = MagicMock(return_value=MagicMock())


def _load_bulk_import():
    """Load (or re-exec) the bulk_import module with the stub in place."""
    mod_name = "caz_woosync.sync.bulk_import"
    # Ensure parent packages exist as proper packages (with __path__)
    # Only add stub if not already a real package with __path__
    for pkg in ("caz_woosync", "caz_woosync.sync", "caz_woosync.utils"):
        if pkg not in sys.modules:
            m = types.ModuleType(pkg)
            m.__path__ = []
            sys.modules[pkg] = m
        elif not hasattr(sys.modules[pkg], "__path__"):
            # Add __path__ without replacing the real module
            sys.modules[pkg].__path__ = []

    # Stub rate_limiter so the import inside _import_entity_batch doesn't fail
    rl_mod_name = "caz_woosync.utils.rate_limiter"
    if rl_mod_name not in sys.modules:
        rl = types.ModuleType(rl_mod_name)
        rl.get_woo_client = MagicMock()
        sys.modules[rl_mod_name] = rl

    # Always re-exec so _reset_frappe changes take effect
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT / "caz_woosync/sync/bulk_import.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _patch_get_woo_client(client):
    """Return a patch context manager for get_woo_client in rate_limiter."""
    return patch("caz_woosync.utils.rate_limiter.get_woo_client", return_value=client)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBulkImportQueueing(unittest.TestCase):
    def setUp(self):
        _reset_frappe()
        self.mod = _load_bulk_import()

    def test_defaults_to_all_three_entity_types(self):
        result = self.mod.start_bulk_import("TestStore")
        self.assertEqual(set(result["queued"]), {"Product", "Order", "Customer"})

    def test_entity_types_list_preserved(self):
        result = self.mod.start_bulk_import("TestStore", entity_types=["Product", "Order"])
        self.assertEqual(result["queued"], ["Product", "Order"])

    def test_returns_queued_key(self):
        result = self.mod.start_bulk_import("TestStore")
        self.assertIn("queued", result)
        self.assertIsInstance(result["queued"], list)

    def test_enqueue_called_per_entity_type(self):
        f = _frappe()
        self.mod.start_bulk_import("TestStore", entity_types=["Product", "Customer"])
        self.assertEqual(f.enqueue.call_count, 2)


class TestEnqueuePaths(unittest.TestCase):
    def setUp(self):
        _reset_frappe()
        self.mod = _load_bulk_import()

    def test_enqueue_uses_dotted_path(self):
        f = _frappe()
        self.mod.start_bulk_import("TestStore", entity_types=["Product"])
        args, kwargs = f.enqueue.call_args
        # First positional arg or 'method' kwarg is the dotted path
        method = args[0] if args else kwargs.get("method", "")
        self.assertIsInstance(method, str)
        self.assertIn("caz_woosync.sync.bulk_import._import_entity_batch", method)

    def test_enqueue_uses_long_queue(self):
        f = _frappe()
        self.mod.start_bulk_import("TestStore", entity_types=["Order"])
        _, kwargs = f.enqueue.call_args
        self.assertEqual(kwargs.get("queue"), "long")

    def test_enqueue_timeout_7200(self):
        f = _frappe()
        self.mod.start_bulk_import("TestStore", entity_types=["Customer"])
        _, kwargs = f.enqueue.call_args
        self.assertEqual(kwargs.get("timeout"), 7200)


class TestImportProgressSchema(unittest.TestCase):
    def setUp(self):
        _reset_frappe()
        self.mod = _load_bulk_import()

    def test_progress_returns_required_keys(self):
        f = _frappe()
        f.db.sql = MagicMock(return_value=[{"cnt": 5}])
        result = self.mod.get_import_progress("TestStore")
        for key in ("queued", "processing", "done", "failed", "total_mapped"):
            self.assertIn(key, result)

    def test_total_mapped_has_products_orders_customers(self):
        f = _frappe()
        f.db.sql = MagicMock(return_value=[{"cnt": 3}])
        result = self.mod.get_import_progress("TestStore")
        mapped = result["total_mapped"]
        self.assertIn("products", mapped)
        self.assertIn("orders", mapped)
        self.assertIn("customers", mapped)

    def test_progress_values_are_integers(self):
        f = _frappe()
        f.db.sql = MagicMock(return_value=[{"cnt": 10}])
        result = self.mod.get_import_progress("TestStore")
        self.assertIsInstance(result["queued"], int)
        self.assertIsInstance(result["done"], int)
        self.assertIsInstance(result["failed"], int)
        self.assertIsInstance(result["processing"], int)


class TestPaginationLogic(unittest.TestCase):
    """Test that _import_entity_batch paginates correctly."""

    def setUp(self):
        _reset_frappe()
        self.mod = _load_bulk_import()

    def _make_client(self, pages):
        """Return a mock WC client that returns pages of records."""
        client = MagicMock()
        responses = []
        for page_records in pages:
            resp = MagicMock()
            resp.status_code = 200
            resp.json = MagicMock(return_value=page_records)
            responses.append(resp)
        client.get = MagicMock(side_effect=responses)
        return client

    def test_page_increments_on_full_page(self):
        pages = [
            [{"id": i} for i in range(50)],  # full page
            [],  # empty — stop
        ]
        client = self._make_client(pages)
        with _patch_get_woo_client(client):
            self.mod._import_entity_batch("TestStore", "Order")
        self.assertEqual(client.get.call_count, 2)

    def test_stops_on_partial_page(self):
        pages = [
            [{"id": i} for i in range(30)],  # partial — stop after this
        ]
        client = self._make_client(pages)
        with _patch_get_woo_client(client):
            self.mod._import_entity_batch("TestStore", "Product")
        self.assertEqual(client.get.call_count, 1)

    def test_stops_at_limit(self):
        # Two full pages available but limit=10
        pages = [
            [{"id": i} for i in range(50)],
            [{"id": i + 50} for i in range(50)],
        ]
        client = self._make_client(pages)
        with _patch_get_woo_client(client):
            self.mod._import_entity_batch("TestStore", "Customer", limit=10)
        # Should only fetch the first page (limit reached within it)
        self.assertEqual(client.get.call_count, 1)


class TestIdempotency(unittest.TestCase):
    def setUp(self):
        _reset_frappe()
        self.mod = _load_bulk_import()

    def test_already_mapped_product_skipped(self):
        f = _frappe()
        # exists returns True for Item Mapping → skip
        f.db.exists = MagicMock(return_value=True)

        pages = [[{"id": 1}]]
        client = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(return_value=pages[0])
        client.get = MagicMock(side_effect=[resp, MagicMock(status_code=200, json=MagicMock(return_value=[]))])

        with _patch_get_woo_client(client):
            self.mod._import_entity_batch("TestStore", "Product")

        # new_doc should not be called since product is already mapped
        f.new_doc.assert_not_called()

    def test_already_queued_item_skipped(self):
        f = _frappe()
        # First exists call (mapping) returns False, second (queue) returns True
        f.db.exists = MagicMock(side_effect=[False, True])

        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(return_value=[{"id": 1}])
        empty = MagicMock()
        empty.status_code = 200
        empty.json = MagicMock(return_value=[])
        client = MagicMock()
        client.get = MagicMock(side_effect=[resp, empty])

        with _patch_get_woo_client(client):
            self.mod._import_entity_batch("TestStore", "Product")

        f.new_doc.assert_not_called()


class TestSinceDateFiltering(unittest.TestCase):
    def setUp(self):
        _reset_frappe()
        self.mod = _load_bulk_import()

    def _run_batch(self, entity_type, since_date):
        client = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(return_value=[])
        client.get = MagicMock(return_value=resp)
        with _patch_get_woo_client(client):
            self.mod._import_entity_batch("TestStore", entity_type, since_date=since_date)
        return client

    def test_since_date_passed_as_after_for_orders(self):
        client = self._run_batch("Order", "2026-01-01")
        _, kwargs = client.get.call_args
        params = kwargs.get("params", {})
        self.assertEqual(params.get("after"), "2026-01-01")

    def test_since_date_passed_as_after_for_products(self):
        client = self._run_batch("Product", "2026-03-15")
        _, kwargs = client.get.call_args
        params = kwargs.get("params", {})
        self.assertEqual(params.get("after"), "2026-03-15")


class TestCancelImport(unittest.TestCase):
    def setUp(self):
        _reset_frappe()
        self.mod = _load_bulk_import()

    def test_cancel_calls_sql_update(self):
        f = _frappe()
        self.mod.cancel_bulk_import("TestStore")
        f.db.sql.assert_called_once()
        sql_call = f.db.sql.call_args[0][0]
        self.assertIn("Skipped", sql_call)
        self.assertIn("Queued", sql_call)

    def test_cancel_commits(self):
        f = _frappe()
        self.mod.cancel_bulk_import("TestStore")
        f.db.commit.assert_called()


class TestImportPageSchema(unittest.TestCase):
    def test_page_json_has_correct_name(self):
        page_json = ROOT / "caz_woosync/page/caz_woo_import/caz_woo_import.json"
        self.assertTrue(page_json.exists(), f"Missing: {page_json}")
        data = json.loads(page_json.read_text())
        self.assertEqual(data["name"], "caz-woo-import")

    def test_page_json_module(self):
        page_json = ROOT / "caz_woosync/page/caz_woo_import/caz_woo_import.json"
        data = json.loads(page_json.read_text())
        self.assertEqual(data["module"], "Caz Woosync")

    def test_page_json_roles_are_objects(self):
        page_json = ROOT / "caz_woosync/page/caz_woo_import/caz_woo_import.json"
        data = json.loads(page_json.read_text())
        roles = data["roles"]
        self.assertIsInstance(roles, list)
        self.assertTrue(all(isinstance(r, dict) for r in roles))
        role_names = {r["role"] for r in roles}
        self.assertIn("System Manager", role_names)
        self.assertIn("Administrator", role_names)


if __name__ == "__main__":
    unittest.main()
