"""
Store management utilities for CAZ WooSync.
Provides helpers for multi-store health, conflict detection, and store lookups.
"""
import frappe


def get_active_stores(fields=None):
    """Return all active Caz Woo Store records.

    Args:
        fields: list of field names to return; defaults to ["name"].

    Returns:
        list of dicts (or list of values when fields has one entry).
    """
    if fields is None:
        fields = ["name"]
    return frappe.get_all("Caz Woo Store", filters={"is_active": 1}, fields=fields)


def get_store_for_item(item_code):
    """Return list of store names that have a mapping for this item.

    Args:
        item_code: ERPNext Item code string.

    Returns:
        list of store name strings.
    """
    rows = frappe.get_all(
        "Caz Woo Item Mapping",
        filters={"erp_item": item_code},
        fields=["store"],
    )
    return [r["store"] for r in rows]


def get_store_for_customer(customer_name):
    """Return list of store names that have a mapping for this customer.

    Looks up the Caz Woo Customer Mapping doctype.

    Args:
        customer_name: ERPNext Customer name string.

    Returns:
        list of store name strings.
    """
    rows = frappe.get_all(
        "Caz Woo Customer Mapping",
        filters={"customer": customer_name},
        fields=["store"],
    )
    return [r["store"] for r in rows]


def detect_item_conflicts(item_code):
    """Detect conflicting sync directions for an item mapped to multiple stores.

    A conflict is raised when two stores both sync erp_to_woo for the same item
    but have different selling prices, indicating a potential price discrepancy.

    Args:
        item_code: ERPNext Item code string.

    Returns:
        list of conflict dicts:
        [{"store_a": str, "store_b": str, "conflict": "price_mismatch"|"stock_mismatch"}]
    """
    mappings = frappe.get_all(
        "Caz Woo Item Mapping",
        filters={"erp_item": item_code},
        fields=["store", "sync_direction", "erp_item"],
    )

    if len(mappings) < 2:
        return []

    conflicts = []
    # Compare each pair of stores for price conflicts
    for i in range(len(mappings)):
        for j in range(i + 1, len(mappings)):
            ma = mappings[i]
            mb = mappings[j]

            store_a = ma.get("store", "")
            store_b = mb.get("store", "")

            dir_a = ma.get("sync_direction", "")
            dir_b = mb.get("sync_direction", "")

            # Only flag conflict when both are pushing erp_to_woo
            if dir_a != "erp_to_woo" or dir_b != "erp_to_woo":
                continue

            # Check price difference using Item Price for each store's price list
            price_a = _get_item_price_for_store(item_code, store_a)
            price_b = _get_item_price_for_store(item_code, store_b)

            if price_a is not None and price_b is not None and price_a != price_b:
                conflicts.append({
                    "store_a": store_a,
                    "store_b": store_b,
                    "conflict": "price_mismatch",
                })

    return conflicts


def _get_item_price_for_store(item_code, store_name):
    """Return the selling price for item_code from the store's configured price list."""
    price_list = frappe.db.get_value("Caz Woo Store", store_name, "item_price_list")
    if not price_list:
        return None
    price = frappe.db.get_value(
        "Item Price",
        {"item_code": item_code, "price_list": price_list},
        "price_list_rate",
    )
    return float(price) if price is not None else None


def get_store_health(store_name):
    """Return a health summary dict for a single store.

    Uses efficient SQL for queue stat counts.

    Args:
        store_name: name of the Caz Woo Store document.

    Returns:
        dict with keys: store_name, is_active, connection_status, queue_stats,
        mapping_counts, last_sync_time, last_connection_check.
    """
    store = frappe.db.get_value(
        "Caz Woo Store",
        store_name,
        ["is_active", "connection_status", "last_connection_check"],
        as_dict=True,
    ) or {}

    # Queue stats — single SQL query for all statuses
    today = frappe.utils.today()
    queue_rows = frappe.db.sql(
        """
        SELECT
            SUM(status = 'Queued') AS queued,
            SUM(status = 'Processing') AS processing,
            SUM(status = 'Failed') AS failed,
            SUM(status = 'Done' AND DATE(creation) = %s) AS done_today
        FROM `tabCaz Woo Sync Queue`
        WHERE store = %s
        """,
        (today, store_name),
        as_dict=True,
    )
    q = queue_rows[0] if queue_rows else {}

    queue_stats = {
        "queued": int(q.get("queued") or 0),
        "processing": int(q.get("processing") or 0),
        "failed": int(q.get("failed") or 0),
        "done_today": int(q.get("done_today") or 0),
    }

    # Mapping counts — single SQL per entity type
    item_count = frappe.db.sql(
        "SELECT COUNT(*) FROM `tabCaz Woo Item Mapping` WHERE store = %s",
        (store_name,),
    )
    order_count = frappe.db.sql(
        "SELECT COUNT(*) FROM `tabCaz Woo Order Mapping` WHERE store = %s",
        (store_name,),
    )
    customer_count = frappe.db.sql(
        "SELECT COUNT(*) FROM `tabCaz Woo Customer Mapping` WHERE store = %s",
        (store_name,),
    )

    mapping_counts = {
        "items": int(item_count[0][0]) if item_count else 0,
        "orders": int(order_count[0][0]) if order_count else 0,
        "customers": int(customer_count[0][0]) if customer_count else 0,
    }

    # Last sync time: most recent Done entry for this store
    last_sync_row = frappe.db.sql(
        """
        SELECT MAX(modified) FROM `tabCaz Woo Sync Queue`
        WHERE store = %s AND status = 'Done'
        """,
        (store_name,),
    )
    last_sync_time = str(last_sync_row[0][0] or "") if last_sync_row else ""

    return {
        "store_name": store_name,
        "is_active": bool(store.get("is_active")),
        "connection_status": store.get("connection_status") or "Untested",
        "queue_stats": queue_stats,
        "mapping_counts": mapping_counts,
        "last_sync_time": last_sync_time,
        "last_connection_check": str(store.get("last_connection_check") or ""),
    }


def get_all_stores_health():
    """Return health summaries for all active stores.

    Returns:
        list of dicts from get_store_health() for each active store.
    """
    stores = get_active_stores(fields=["name"])
    return [get_store_health(s["name"]) for s in stores]
