import frappe


def process_sync_queue():
    """Scheduled: processes pending items in the sync queue. Phase 2 will implement this fully."""
    pass


def daily_health_check():
    """Scheduled: checks connection health for all active stores."""
    stores = frappe.get_all(
        "Caz Woo Store",
        filters={"is_active": 1},
        fields=["name"],
    )
    for store in stores:
        try:
            from caz_woosync.api.connection import test_store_connection
            test_store_connection(store.name)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"CAZ WooSync health check failed: {store.name}")
