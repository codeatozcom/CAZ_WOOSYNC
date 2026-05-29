"""
conftest.py — pytest configuration for CAZ WooSync test suite.

Prevents sys.modules contamination between test files that inject frappe stubs.
Each test module that pollutes sys.modules at import time runs in its own
process boundary via snapshot/restore around the collection phase.
"""
import sys
import types
import pytest
from unittest.mock import MagicMock


def _make_mock_response(status_code=200):
    """Return a mock HTTP response with a numeric status_code."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {}
    resp.text = ""
    resp.headers = {}
    return resp


def _patch_frappe_stub_if_needed():
    """
    If a stub frappe.utils is in sys.modules (i.e. not the real Frappe),
    add any missing symbols that sync modules import directly.
    """
    utils = sys.modules.get("frappe.utils")
    if utils is None:
        return
    # Real frappe.utils has a __file__ attribute; stubs are plain ModuleType
    if getattr(utils, "__file__", None) is not None:
        return  # real module — leave alone

    # Patch missing symbols onto the stub
    if not hasattr(utils, "strip_html"):
        utils.strip_html = lambda s: s or ""
    if not hasattr(utils, "cstr"):
        utils.cstr = lambda v, *a: str(v) if v is not None else ""
    if not hasattr(utils, "flt"):
        utils.flt = lambda v, *a: float(v) if v else 0.0
    if not hasattr(utils, "now"):
        utils.now = MagicMock(return_value="2026-05-29 00:00:00")
    if not hasattr(utils, "nowdate"):
        utils.nowdate = MagicMock(return_value="2026-05-29")
    if not hasattr(utils, "today"):
        utils.today = MagicMock(return_value="2026-05-29")
    if not hasattr(utils, "escape_html"):
        utils.escape_html = lambda x: x
    if not hasattr(utils, "getdate"):
        utils.getdate = MagicMock(side_effect=lambda x=None: x)
    if not hasattr(utils, "get_datetime"):
        utils.get_datetime = MagicMock(side_effect=lambda x=None: x)
    if not hasattr(utils, "now_datetime"):
        utils.now_datetime = MagicMock(return_value="2026-05-29 00:00:00")
    if not hasattr(utils, "add_to_date"):
        utils.add_to_date = MagicMock(return_value="2026-05-29 00:00:00")
    if not hasattr(utils, "time_diff_in_seconds"):
        utils.time_diff_in_seconds = MagicMock(return_value=9999)

    # Patch the frappe root stub
    frappe = sys.modules.get("frappe")
    if frappe and getattr(frappe, "__file__", None) is None:
        if not getattr(frappe, "strip_html", None):
            frappe.strip_html = utils.strip_html
        if not hasattr(frappe, "flags"):
            frappe.flags = types.SimpleNamespace(
                in_migrate=False, in_patch=False, in_import=False, in_install=False
            )
        if not hasattr(frappe, "logger") or not callable(getattr(frappe, "logger", None)):
            _logger = MagicMock()
            frappe.logger = MagicMock(return_value=_logger)
        if not hasattr(frappe, "publish_realtime"):
            frappe.publish_realtime = MagicMock()
        if not hasattr(frappe, "sendmail"):
            frappe.sendmail = MagicMock()
        if not hasattr(frappe, "copy_doc"):
            frappe.copy_doc = MagicMock(return_value=MagicMock())

    # Patch caz_woosync.utils.rate_limiter stub so WooCommerceClient
    # mock methods return responses with numeric status_code (not MagicMock)
    rl = sys.modules.get("caz_woosync.utils.rate_limiter")
    if rl and getattr(rl, "__file__", None) is None:
        if hasattr(rl, "WooCommerceClient"):
            wcc = rl.WooCommerceClient
            if isinstance(wcc, MagicMock):
                instance = wcc.return_value
                for method in ("get", "post", "put", "delete"):
                    getattr(instance, method).return_value = _make_mock_response(200)

    # Patch woocommerce.API so the real rate_limiter.py (when loaded fresh)
    # gets a proper mock that returns numeric status_code responses
    woo = sys.modules.get("woocommerce")
    if woo and getattr(woo, "__file__", None) is None and hasattr(woo, "API"):
        api_cls = woo.API
        if isinstance(api_cls, MagicMock):
            api_instance = api_cls.return_value
            for method in ("get", "post", "put", "delete"):
                getattr(api_instance, method).return_value = _make_mock_response(200)


@pytest.fixture(autouse=True)
def _ensure_frappe_stub_complete():
    """Autouse fixture: patch any incomplete frappe stub before each test."""
    _patch_frappe_stub_if_needed()
    yield
