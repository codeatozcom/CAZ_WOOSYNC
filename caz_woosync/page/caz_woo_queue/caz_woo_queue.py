import frappe


@frappe.whitelist()
def get_queue_data(store=None, status=None, limit=50, offset=0):
    """Return paginated queue items for the dashboard."""
    limit = min(int(limit), 200)
    offset = int(offset)

    conditions = []
    values = []

    if store:
        conditions.append("store = %s")
        values.append(store)
    if status and status != "All":
        conditions.append("status = %s")
        values.append(status)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    rows = frappe.db.sql(
        f"""
        SELECT name, store, direction, entity_type, woo_id,
               erp_docname, status, attempt_count, last_attempt,
               SUBSTRING(error_log, 1, 200) AS error_preview,
               creation
        FROM `tabCaz Woo Sync Queue`
        {where}
        ORDER BY creation DESC
        LIMIT %s OFFSET %s
        """,
        values + [limit, offset],
        as_dict=True,
    )

    total = frappe.db.sql(
        f"SELECT COUNT(*) FROM `tabCaz Woo Sync Queue` {where}",
        values,
    )[0][0]

    summary = frappe.db.sql(
        """
        SELECT status, COUNT(*) as count
        FROM `tabCaz Woo Sync Queue`
        GROUP BY status
        """,
        as_dict=True,
    )

    return {"rows": rows, "total": total, "summary": summary}


@frappe.whitelist()
def retry_failed_items(store=None):
    """Reset all Failed items to Queued so they will be retried."""
    conditions = ["status = 'Failed'"]
    values = []
    if store:
        conditions.append("store = %s")
        values.append(store)

    where = "WHERE " + " AND ".join(conditions)
    count = frappe.db.sql(
        f"SELECT COUNT(*) FROM `tabCaz Woo Sync Queue` {where}", values
    )[0][0]

    frappe.db.sql(
        f"""
        UPDATE `tabCaz Woo Sync Queue`
        SET status = 'Queued', attempt_count = 0, error_log = NULL, modified = NOW()
        {where}
        """,
        values,
    )
    frappe.db.commit()
    return {"retried": count}


@frappe.whitelist()
def skip_items(names):
    """Mark specific queue items as Skipped."""
    import json

    if isinstance(names, str):
        names = json.loads(names)
    if not names:
        return {"skipped": 0}

    placeholders = ", ".join(["%s"] * len(names))
    frappe.db.sql(
        f"UPDATE `tabCaz Woo Sync Queue` SET status = 'Skipped', modified = NOW() "
        f"WHERE name IN ({placeholders})",
        names,
    )
    frappe.db.commit()
    return {"skipped": len(names)}
