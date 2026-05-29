"""
CAZ WooSync Dashboard page controller.
Provides the backend data endpoint for the multi-store overview dashboard.
"""
import frappe


@frappe.whitelist()
def get_dashboard_data():
    """Return health data for all active stores for the dashboard.

    Returns:
        list of store health dicts (see store_manager.get_store_health).
    """
    from caz_woosync.utils.store_manager import get_all_stores_health
    return get_all_stores_health()
