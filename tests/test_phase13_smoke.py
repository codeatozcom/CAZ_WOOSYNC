"""
Phase 13 smoke tests — Alerts, Notifications, and Final Polish.
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
    db.count = MagicMock(return_value=0)
    frappe_mod.db = db

    utils_mod = types.ModuleType("frappe.utils")
    utils_mod.escape_html = lambda x: str(x) if x is not None else ""
    utils_mod.now = MagicMock(return_value="2026-05-29 08:00:00")
    utils_mod.today = MagicMock(return_value="2026-05-29")
    utils_mod.nowdate = MagicMock(return_value="2026-05-29")
    utils_mod.add_to_date = MagicMock(return_value="2026-05-28 08:00:00")
    utils_mod.get_datetime = MagicMock(side_effect=lambda x=None: x)
    frappe_mod.utils = utils_mod

    frappe_mod.get_single = MagicMock()
    frappe_mod.get_doc = MagicMock()
    frappe_mod.get_all = MagicMock(return_value=[])
    frappe_mod.new_doc = MagicMock()
    frappe_mod.sendmail = MagicMock()
    frappe_mod.publish_realtime = MagicMock()
    frappe_mod.log_error = MagicMock()
    frappe_mod.get_traceback = MagicMock(return_value="traceback")
    frappe_mod.throw = MagicMock(side_effect=Exception)
    frappe_mod.enqueue = MagicMock()
    frappe_mod.whitelist = lambda fn=None, **kw: (fn if fn else lambda f: f)
    frappe_mod._ = lambda s: s

    import types as _types
    frappe_mod.flags = _types.SimpleNamespace(
        in_migrate=False, in_patch=False, in_import=False, in_install=False
    )

    sys.modules["frappe"] = frappe_mod
    sys.modules["frappe.utils"] = utils_mod
    sys.modules["frappe.utils.data"] = utils_mod
    return frappe_mod


def _load_alerts():
    """Load alerts module with fresh frappe stub."""
    for key in list(sys.modules.keys()):
        if key.startswith("caz_woosync"):
            del sys.modules[key]
    frappe = _make_frappe_stub()
    sys.path.insert(0, str(ROOT))
    import caz_woosync.utils.alerts as alerts_module
    importlib.reload(alerts_module)
    return alerts_module, frappe


# ---------------------------------------------------------------------------
# TestAlertEmailFormat
# ---------------------------------------------------------------------------

class TestAlertEmailFormat(unittest.TestCase):
    def setUp(self):
        self.alerts, self.frappe = _load_alerts()

    def _make_settings(self, **kwargs):
        defaults = {
            "alert_email": "admin@example.com",
            "alert_threshold_failures": 1,
            "alert_on_connection_failure": 1,
            "send_daily_digest": 0,
        }
        defaults.update(kwargs)
        s = MagicMock()
        for k, v in defaults.items():
            setattr(s, k, v)
        return s

    def test_subject_contains_store_name(self):
        self.frappe.get_single.return_value = self._make_settings()
        self.alerts.send_sync_failure_alert("MyStore", "Product", "99", "Some error", 3)
        call_args = self.frappe.sendmail.call_args
        self.assertIn("MyStore", call_args.kwargs.get("subject", "") or call_args[1].get("subject", ""))

    def test_subject_contains_entity_type_and_woo_id(self):
        self.frappe.get_single.return_value = self._make_settings()
        self.alerts.send_sync_failure_alert("MyStore", "Order", "42", "error", 3)
        call_kwargs = self.frappe.sendmail.call_args
        subject = call_kwargs.kwargs.get("subject") or call_kwargs[0][0] if call_kwargs[0] else ""
        # check combined: subject or message contains entity and woo_id
        all_text = str(call_kwargs)
        self.assertIn("Order", all_text)
        self.assertIn("42", all_text)

    def test_recipients_is_always_list(self):
        self.frappe.get_single.return_value = self._make_settings()
        self.alerts.send_sync_failure_alert("MyStore", "Product", "1", "err", 5)
        call_kwargs = self.frappe.sendmail.call_args
        recipients = call_kwargs.kwargs.get("recipients") or (call_kwargs[0][0] if call_kwargs[0] else None)
        self.assertIsInstance(recipients, list)

    def test_body_contains_entity_type(self):
        self.frappe.get_single.return_value = self._make_settings()
        self.alerts.send_sync_failure_alert("ShopA", "Customer", "77", "fail", 2)
        all_text = str(self.frappe.sendmail.call_args)
        self.assertIn("Customer", all_text)

    def test_body_contains_woo_id(self):
        self.frappe.get_single.return_value = self._make_settings()
        self.alerts.send_sync_failure_alert("ShopA", "Customer", "77", "fail", 2)
        all_text = str(self.frappe.sendmail.call_args)
        self.assertIn("77", all_text)


# ---------------------------------------------------------------------------
# TestDailyDigestConditions
# ---------------------------------------------------------------------------

class TestDailyDigestConditions(unittest.TestCase):
    def setUp(self):
        self.alerts, self.frappe = _load_alerts()

    def _make_settings(self, **kwargs):
        defaults = {
            "alert_email": "admin@example.com",
            "send_daily_digest": 1,
        }
        defaults.update(kwargs)
        s = MagicMock()
        for k, v in defaults.items():
            setattr(s, k, v)
        return s

    def test_digest_not_sent_if_disabled(self):
        self.frappe.get_single.return_value = self._make_settings(send_daily_digest=0)
        self.alerts.send_daily_digest("MyStore")
        self.frappe.sendmail.assert_not_called()

    def test_digest_not_sent_if_no_email(self):
        self.frappe.get_single.return_value = self._make_settings(alert_email="", send_daily_digest=1)
        self.alerts.send_daily_digest("MyStore")
        self.frappe.sendmail.assert_not_called()

    def test_digest_sent_if_both_set(self):
        self.frappe.db.sql.return_value = [{"status": "Done", "cnt": 10}]
        self.frappe.get_all.return_value = []
        self.frappe.get_single.return_value = self._make_settings(
            alert_email="admin@example.com", send_daily_digest=1
        )
        self.alerts.send_daily_digest("MyStore")
        self.frappe.sendmail.assert_called_once()


# ---------------------------------------------------------------------------
# TestConnectionAlertTrigger
# ---------------------------------------------------------------------------

class TestConnectionAlertTrigger(unittest.TestCase):
    def setUp(self):
        self.alerts, self.frappe = _load_alerts()

    def _make_settings(self, **kwargs):
        defaults = {"alert_email": "admin@example.com", "alert_on_connection_failure": 1}
        defaults.update(kwargs)
        s = MagicMock()
        for k, v in defaults.items():
            setattr(s, k, v)
        return s

    def test_send_connection_alert_called_on_failure(self):
        self.frappe.get_single.return_value = self._make_settings()
        self.alerts.send_connection_alert("MyShop", "HTTP 401")
        self.frappe.sendmail.assert_called_once()

    def test_subject_contains_store_name(self):
        self.frappe.get_single.return_value = self._make_settings()
        self.alerts.send_connection_alert("SpecialStore", "Timeout")
        call_kwargs = self.frappe.sendmail.call_args
        all_text = str(call_kwargs)
        self.assertIn("SpecialStore", all_text)

    def test_not_sent_if_alert_on_connection_failure_disabled(self):
        self.frappe.get_single.return_value = self._make_settings(alert_on_connection_failure=0)
        self.alerts.send_connection_alert("MyShop", "HTTP 500")
        self.frappe.sendmail.assert_not_called()


# ---------------------------------------------------------------------------
# TestAlertSummarySchema
# ---------------------------------------------------------------------------

class TestAlertSummarySchema(unittest.TestCase):
    def setUp(self):
        self.alerts, self.frappe = _load_alerts()

    def test_returns_required_keys(self):
        self.frappe.db.sql.return_value = [{"cnt": 5}]
        self.frappe.get_all.return_value = ["StoreA"]
        result = self.alerts.get_alert_summary("MyStore", hours=24)
        self.assertIn("failed_syncs", result)
        self.assertIn("connection_failures", result)
        self.assertIn("stores_down", result)

    def test_failed_syncs_is_int(self):
        self.frappe.db.sql.return_value = [{"cnt": 3}]
        self.frappe.get_all.return_value = []
        result = self.alerts.get_alert_summary("MyStore")
        self.assertIsInstance(result["failed_syncs"], int)

    def test_stores_down_is_list(self):
        self.frappe.db.sql.return_value = [{"cnt": 0}]
        self.frappe.get_all.return_value = ["StoreX"]
        result = self.alerts.get_alert_summary("MyStore")
        self.assertIsInstance(result["stores_down"], list)

    def test_connection_failures_is_int(self):
        self.frappe.db.sql.return_value = [{"cnt": 2}]
        self.frappe.get_all.return_value = []
        result = self.alerts.get_alert_summary("MyStore")
        self.assertIsInstance(result["connection_failures"], int)


# ---------------------------------------------------------------------------
# TestAlertSettings
# ---------------------------------------------------------------------------

class TestAlertSettings(unittest.TestCase):
    def test_settings_json_has_alert_email(self):
        settings_path = ROOT / "caz_woosync/doctype/caz_woo_settings/caz_woo_settings.json"
        data = json.loads(settings_path.read_text())
        fieldnames = [f["fieldname"] for f in data["fields"]]
        self.assertIn("alert_email", fieldnames)

    def test_settings_json_has_send_daily_digest(self):
        settings_path = ROOT / "caz_woosync/doctype/caz_woo_settings/caz_woo_settings.json"
        data = json.loads(settings_path.read_text())
        fieldnames = [f["fieldname"] for f in data["fields"]]
        self.assertIn("send_daily_digest", fieldnames)

    def test_settings_json_has_digest_hour(self):
        settings_path = ROOT / "caz_woosync/doctype/caz_woo_settings/caz_woo_settings.json"
        data = json.loads(settings_path.read_text())
        fieldnames = [f["fieldname"] for f in data["fields"]]
        self.assertIn("digest_hour", fieldnames)

    def test_settings_json_has_alert_on_connection_failure(self):
        settings_path = ROOT / "caz_woosync/doctype/caz_woo_settings/caz_woo_settings.json"
        data = json.loads(settings_path.read_text())
        fieldnames = [f["fieldname"] for f in data["fields"]]
        self.assertIn("alert_on_connection_failure", fieldnames)

    def test_settings_json_has_alert_threshold_failures(self):
        settings_path = ROOT / "caz_woosync/doctype/caz_woo_settings/caz_woo_settings.json"
        data = json.loads(settings_path.read_text())
        fieldnames = [f["fieldname"] for f in data["fields"]]
        self.assertIn("alert_threshold_failures", fieldnames)

    def test_all_new_fields_have_descriptions(self):
        settings_path = ROOT / "caz_woosync/doctype/caz_woo_settings/caz_woo_settings.json"
        data = json.loads(settings_path.read_text())
        new_fields = {"send_daily_digest", "digest_hour", "alert_on_connection_failure", "alert_threshold_failures"}
        for field in data["fields"]:
            if field["fieldname"] in new_fields:
                self.assertTrue(
                    field.get("description", "").strip(),
                    f"Field {field['fieldname']} missing description",
                )


# ---------------------------------------------------------------------------
# TestFailureThreshold
# ---------------------------------------------------------------------------

class TestFailureThreshold(unittest.TestCase):
    def setUp(self):
        self.alerts, self.frappe = _load_alerts()

    def _make_settings(self, threshold=3):
        s = MagicMock()
        s.alert_email = "admin@example.com"
        s.alert_threshold_failures = threshold
        return s

    def test_alert_not_sent_below_threshold(self):
        self.frappe.get_single.return_value = self._make_settings(threshold=3)
        self.alerts.send_sync_failure_alert("Store", "Product", "1", "error", 2)
        self.frappe.sendmail.assert_not_called()

    def test_alert_sent_at_threshold(self):
        self.frappe.get_single.return_value = self._make_settings(threshold=3)
        self.alerts.send_sync_failure_alert("Store", "Product", "1", "error", 3)
        self.frappe.sendmail.assert_called_once()

    def test_alert_sent_above_threshold(self):
        self.frappe.get_single.return_value = self._make_settings(threshold=1)
        self.alerts.send_sync_failure_alert("Store", "Order", "5", "err", 5)
        self.frappe.sendmail.assert_called_once()

    def test_threshold_default_is_3(self):
        s = MagicMock()
        s.alert_email = "admin@example.com"
        s.alert_threshold_failures = None  # will fall back to default 3
        self.frappe.get_single.return_value = s
        self.alerts.send_sync_failure_alert("Store", "Product", "1", "error", 2)
        self.frappe.sendmail.assert_not_called()


# ---------------------------------------------------------------------------
# TestRealtimePublish
# ---------------------------------------------------------------------------

class TestRealtimePublish(unittest.TestCase):
    def setUp(self):
        self.alerts, self.frappe = _load_alerts()

    def _make_settings(self, **kwargs):
        defaults = {"alert_email": "admin@example.com", "alert_threshold_failures": 1}
        defaults.update(kwargs)
        s = MagicMock()
        for k, v in defaults.items():
            setattr(s, k, v)
        return s

    def test_publish_realtime_called_with_caz_woo_alert(self):
        self.frappe.get_single.return_value = self._make_settings()
        self.alerts.send_sync_failure_alert("StoreX", "Product", "10", "error", 3)
        self.frappe.publish_realtime.assert_called()
        event_name = self.frappe.publish_realtime.call_args[0][0]
        self.assertEqual(event_name, "caz_woo_alert")

    def test_publish_realtime_payload_has_store(self):
        self.frappe.get_single.return_value = self._make_settings()
        self.alerts.send_sync_failure_alert("StoreY", "Order", "20", "err", 5)
        payload = self.frappe.publish_realtime.call_args[0][1]
        self.assertIn("store", payload)
        self.assertEqual(payload["store"], "StoreY")

    def test_connection_alert_publishes_realtime(self):
        self.frappe.get_single.return_value = self._make_settings(alert_on_connection_failure=1)
        self.alerts.send_connection_alert("StoreZ", "Timeout")
        self.frappe.publish_realtime.assert_called()
        event_name = self.frappe.publish_realtime.call_args[0][0]
        self.assertEqual(event_name, "caz_woo_alert")


# ---------------------------------------------------------------------------
# TestDigestHtmlFormat
# ---------------------------------------------------------------------------

class TestDigestHtmlFormat(unittest.TestCase):
    def setUp(self):
        self.alerts, self.frappe = _load_alerts()

    def _make_settings(self):
        s = MagicMock()
        s.alert_email = "admin@example.com"
        s.send_daily_digest = 1
        return s

    def test_digest_email_is_html(self):
        self.frappe.get_single.return_value = self._make_settings()
        self.frappe.db.sql.return_value = []
        self.frappe.db.count.return_value = 0
        self.frappe.get_all.return_value = []
        self.alerts.send_daily_digest("MyStore")
        call_kwargs = self.frappe.sendmail.call_args
        message = call_kwargs.kwargs.get("message") or ""
        self.assertTrue(
            "<html>" in message or "<table" in message or "<h2>" in message,
            f"Expected HTML tags in message, got: {message[:200]}",
        )

    def test_digest_contains_store_name(self):
        self.frappe.get_single.return_value = self._make_settings()
        self.frappe.db.sql.return_value = []
        self.frappe.db.count.return_value = 0
        self.frappe.get_all.return_value = []
        self.alerts.send_daily_digest("SpecificStore")
        call_kwargs = self.frappe.sendmail.call_args
        all_text = str(call_kwargs)
        self.assertIn("SpecificStore", all_text)

    def test_digest_contains_date(self):
        self.frappe.get_single.return_value = self._make_settings()
        self.frappe.db.sql.return_value = []
        self.frappe.db.count.return_value = 0
        self.frappe.get_all.return_value = []
        self.alerts.send_daily_digest("MyStore")
        call_kwargs = self.frappe.sendmail.call_args
        all_text = str(call_kwargs)
        self.assertIn("2026-05-29", all_text)

    def test_digest_subject_contains_store_name(self):
        self.frappe.get_single.return_value = self._make_settings()
        self.frappe.db.sql.return_value = []
        self.frappe.db.count.return_value = 0
        self.frappe.get_all.return_value = []
        self.alerts.send_daily_digest("DigestShop")
        call_kwargs = self.frappe.sendmail.call_args
        subject = call_kwargs.kwargs.get("subject") or ""
        self.assertIn("DigestShop", subject)


if __name__ == "__main__":
    unittest.main()
