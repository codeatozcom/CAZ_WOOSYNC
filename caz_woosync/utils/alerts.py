import frappe


def send_sync_failure_alert(store_name, entity_type, woo_id, error_log, attempt_count):
    """Send email + Frappe notification when a sync item fails all retries."""
    try:
        settings = frappe.get_single("Caz Woo Settings")
        threshold = getattr(settings, "alert_threshold_failures", None) or 3
        if attempt_count < threshold:
            return

        subject = f"CAZ WooSync: Sync Failed — {store_name} — {entity_type} #{woo_id}"
        message = (
            f"<p>Sync failed for <strong>{frappe.utils.escape_html(store_name)}</strong> "
            f"after {attempt_count} attempt(s).</p>"
            f"<p><strong>Store:</strong> {frappe.utils.escape_html(store_name)}<br>"
            f"<strong>Entity:</strong> {frappe.utils.escape_html(entity_type)} "
            f"#{frappe.utils.escape_html(str(woo_id))}</p>"
            f"<p><strong>Error:</strong></p>"
            f"<pre>{frappe.utils.escape_html(error_log or '')}</pre>"
        )

        if getattr(settings, "alert_email", None):
            frappe.sendmail(
                recipients=[settings.alert_email],
                subject=subject,
                message=message,
                now=True,
            )

        # In-app notification via realtime
        frappe.publish_realtime(
            "caz_woo_alert",
            {
                "store": store_name,
                "entity_type": entity_type,
                "woo_id": woo_id,
                "error_log": (error_log or "")[:500],
                "attempt_count": attempt_count,
                "subject": subject,
            },
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "CAZ WooSync: send_sync_failure_alert failed")


def send_daily_digest(store_name):
    """
    Send daily email digest of sync activity for one store.
    Called by daily_health_check in tasks.py.
    """
    try:
        settings = frappe.get_single("Caz Woo Settings")
        if not getattr(settings, "send_daily_digest", 0):
            return
        if not getattr(settings, "alert_email", None):
            return

        today_str = frappe.utils.today()
        since = frappe.utils.add_to_date(frappe.utils.now(), hours=-24)

        # Queue stats
        queue_stats = frappe.db.sql(
            """
            SELECT status, COUNT(*) AS cnt
            FROM `tabCaz Woo Sync Queue`
            WHERE store = %s AND creation >= %s
            GROUP BY status
            """,
            (store_name, since),
            as_dict=True,
        )
        stat_map = {row["status"]: row["cnt"] for row in queue_stats}

        # Mapping counts
        item_count = frappe.db.count("Caz Woo Item Mapping", {"store": store_name})
        order_count = frappe.db.count("Caz Woo Order Mapping", {"store": store_name})
        customer_count = frappe.db.count("Caz Woo Customer Mapping", {"store": store_name})

        # Connection status
        connection_status = frappe.db.get_value("Caz Woo Store", store_name, "connection_status") or "Unknown"

        # Failed stores
        failed_stores = frappe.get_all(
            "Caz Woo Store",
            filters={"connection_status": "Failed", "is_active": 1},
            fields=["name"],
            pluck="name",
        )

        # Recent failed items
        failed_items = frappe.db.sql(
            """
            SELECT entity_type, woo_id, error_log
            FROM `tabCaz Woo Sync Queue`
            WHERE store = %s AND status = 'Failed' AND creation >= %s
            LIMIT 10
            """,
            (store_name, since),
            as_dict=True,
        )

        failed_rows_html = ""
        for fi in failed_items:
            err_preview = (fi.get("error_log") or "")[:200]
            failed_rows_html += (
                f"<tr><td>{frappe.utils.escape_html(fi.get('entity_type', ''))}</td>"
                f"<td>{frappe.utils.escape_html(str(fi.get('woo_id', '')))}</td>"
                f"<td><pre style='margin:0;font-size:11px'>{frappe.utils.escape_html(err_preview)}</pre></td></tr>"
            )

        failed_stores_html = ", ".join(failed_stores) if failed_stores else "None"

        html = f"""<html>
<body style="font-family:Arial,sans-serif;color:#333">
<h2>CAZ WooSync Daily Digest</h2>
<p><strong>Store:</strong> {frappe.utils.escape_html(store_name)}<br>
<strong>Date:</strong> {today_str}</p>

<h3>Sync Activity (Last 24 Hours)</h3>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">
  <tr><th>Status</th><th>Count</th></tr>
  <tr><td>Done</td><td>{stat_map.get('Done', 0)}</td></tr>
  <tr><td>Failed</td><td>{stat_map.get('Failed', 0)}</td></tr>
  <tr><td>Skipped</td><td>{stat_map.get('Skipped', 0)}</td></tr>
  <tr><td>Queued</td><td>{stat_map.get('Queued', 0)}</td></tr>
</table>

<h3>Mapping Counts</h3>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">
  <tr><th>Type</th><th>Total Mapped</th></tr>
  <tr><td>Items</td><td>{item_count}</td></tr>
  <tr><td>Orders</td><td>{order_count}</td></tr>
  <tr><td>Customers</td><td>{customer_count}</td></tr>
</table>

<h3>Connection Status</h3>
<p><strong>{frappe.utils.escape_html(store_name)}:</strong> {frappe.utils.escape_html(connection_status)}</p>
<p><strong>Stores with connection failures:</strong> {frappe.utils.escape_html(failed_stores_html)}</p>
"""

        if failed_items:
            html += f"""
<h3>Recent Failed Sync Items</h3>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">
  <tr><th>Entity Type</th><th>Woo ID</th><th>Error (first 200 chars)</th></tr>
  {failed_rows_html}
</table>
"""

        html += "</body></html>"

        frappe.sendmail(
            recipients=[settings.alert_email],
            subject=f"CAZ WooSync Daily Digest — {store_name} — {today_str}",
            message=html,
            now=True,
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), f"CAZ WooSync: send_daily_digest failed for {store_name}")


def send_connection_alert(store_name, error_message):
    """Alert when a store connection test fails."""
    try:
        settings = frappe.get_single("Caz Woo Settings")
        if not getattr(settings, "alert_on_connection_failure", 1):
            return

        subject = f"CAZ WooSync: Connection Failed — {store_name}"
        message = (
            f"<p>The connection test for WooCommerce store "
            f"<strong>{frappe.utils.escape_html(store_name)}</strong> has failed.</p>"
            f"<p><strong>Error:</strong> {frappe.utils.escape_html(str(error_message))}</p>"
            f"<p>Please check your store URL, Consumer Key, and Consumer Secret in the store settings.</p>"
        )

        if getattr(settings, "alert_email", None):
            frappe.sendmail(
                recipients=[settings.alert_email],
                subject=subject,
                message=message,
                now=True,
            )

        frappe.publish_realtime(
            "caz_woo_alert",
            {
                "store": store_name,
                "type": "connection_failure",
                "error": str(error_message),
                "subject": subject,
            },
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), f"CAZ WooSync: send_connection_alert failed for {store_name}")


def get_alert_summary(store_name, hours=24):
    """
    Return alert summary for the last N hours:
    {"failed_syncs": int, "connection_failures": int, "stores_down": [str]}
    Uses frappe.db.sql for efficient counts.
    """
    since = frappe.utils.add_to_date(frappe.utils.now(), hours=-hours)

    failed_syncs_result = frappe.db.sql(
        """
        SELECT COUNT(*) AS cnt
        FROM `tabCaz Woo Sync Queue`
        WHERE store = %s AND status = 'Failed' AND creation >= %s
        """,
        (store_name, since),
        as_dict=True,
    )
    failed_syncs = (failed_syncs_result[0]["cnt"] if failed_syncs_result else 0) or 0

    # Connection failures: count of error log entries in the last N hours matching pattern
    conn_failures_result = frappe.db.sql(
        """
        SELECT COUNT(*) AS cnt
        FROM `tabError Log`
        WHERE method LIKE %s AND creation >= %s
        """,
        (f"%connection test failed for {store_name}%", since),
        as_dict=True,
    )
    connection_failures = (conn_failures_result[0]["cnt"] if conn_failures_result else 0) or 0

    stores_down = frappe.get_all(
        "Caz Woo Store",
        filters={"connection_status": "Failed", "is_active": 1},
        pluck="name",
    )

    return {
        "failed_syncs": int(failed_syncs),
        "connection_failures": int(connection_failures),
        "stores_down": stores_down,
    }
