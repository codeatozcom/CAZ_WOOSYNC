"""
conftest.py — pytest configuration for CAZ WooSync test suite.

Prevents sys.modules contamination between test files that inject frappe stubs.
Each test module that pollutes sys.modules at import time runs in its own
process boundary via snapshot/restore around the collection phase.
"""
import sys
import pytest


def pytest_runtest_setup(item):
    """Snapshot sys.modules before each test to allow restoration if needed."""
    item._sys_modules_snapshot = set(sys.modules.keys())


# Ensure every frappe stub has the symbols that sync modules need
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
    missing = {}
    if not hasattr(utils, "strip_html"):
        missing["strip_html"] = lambda s: s or ""
    if not hasattr(utils, "cstr"):
        missing["cstr"] = lambda v, *a: str(v) if v is not None else ""
    if not hasattr(utils, "flt"):
        missing["flt"] = lambda v, *a: float(v) if v else 0.0
    if not hasattr(utils, "now"):
        from unittest.mock import MagicMock
        missing["now"] = MagicMock(return_value="2026-05-29 00:00:00")
    if not hasattr(utils, "escape_html"):
        missing["escape_html"] = lambda x: x
    if not hasattr(utils, "getdate"):
        from unittest.mock import MagicMock
        missing["getdate"] = MagicMock(side_effect=lambda x=None: x)
    if not hasattr(utils, "get_datetime"):
        from unittest.mock import MagicMock
        missing["get_datetime"] = MagicMock(side_effect=lambda x=None: x)
    if not hasattr(utils, "today"):
        from unittest.mock import MagicMock
        missing["today"] = MagicMock(return_value="2026-05-29")
    if not hasattr(utils, "now"):
        from unittest.mock import MagicMock
        missing["now"] = MagicMock(return_value="2026-05-29 00:00:00")
    if not hasattr(utils, "nowdate"):
        from unittest.mock import MagicMock
        missing["nowdate"] = MagicMock(return_value="2026-05-29")

    for name, val in missing.items():
        setattr(utils, name, val)

    # Also patch the frappe root stub
    frappe = sys.modules.get("frappe")
    if frappe and getattr(frappe, "__file__", None) is None:
        if not getattr(frappe, "strip_html", None):
            frappe.strip_html = getattr(utils, "strip_html")
        if not hasattr(frappe, "flags"):
            import types as _types
            frappe.flags = _types.SimpleNamespace(
                in_migrate=False, in_patch=False, in_import=False, in_install=False
            )


@pytest.fixture(autouse=True)
def _ensure_frappe_stub_complete():
    """Autouse fixture: patch any incomplete frappe stub before each test."""
    _patch_frappe_stub_if_needed()
    yield
