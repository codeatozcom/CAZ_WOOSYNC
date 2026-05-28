import frappe


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.throw("Please log in to access the setup wizard.", frappe.PermissionError)

    context.no_cache = 1
    context.title = "CAZ WooSync — Setup Wizard"
    context.stores = frappe.get_all(
        "Caz Woo Store",
        fields=["name", "store_name", "woo_url", "connection_status", "is_active", "webhook_url"],
        order_by="creation desc",
    )
    context.companies = frappe.get_all("Company", fields=["name"])
    context.item_groups = frappe.get_all("Item Group", fields=["name"])
    context.customer_groups = frappe.get_all("Customer Group", fields=["name"])
