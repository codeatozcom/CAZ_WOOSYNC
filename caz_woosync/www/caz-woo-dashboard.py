import frappe


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.throw("Please log in to access the dashboard.", frappe.PermissionError)

    context.no_cache = 1
    context.title = "CAZ WooSync Dashboard"

    context.stores = frappe.get_all(
        "Caz Woo Store",
        fields=["name", "store_name", "woo_url", "connection_status", "last_connection_check", "is_active"],
    )

    today = frappe.utils.today()
    context.queue_summary = frappe.db.sql(
        """
        SELECT status, COUNT(*) as count
        FROM `tabCaz Woo Sync Queue`
        WHERE DATE(creation) = %s
        GROUP BY status
        """,
        (today,),
        as_dict=True,
    )

    context.recent_queue = frappe.get_all(
        "Caz Woo Sync Queue",
        fields=["name", "store", "entity_type", "woo_id", "status", "last_attempt", "error_log"],
        order_by="creation desc",
        limit=20,
    )
