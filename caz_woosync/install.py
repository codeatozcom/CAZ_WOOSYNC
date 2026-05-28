import frappe


def after_install():
    """Create singleton settings record with defaults on app install."""
    if not frappe.db.exists("Caz Woo Settings", "Caz Woo Settings"):
        doc = frappe.get_single("Caz Woo Settings")
        doc.max_requests_per_minute = 60
        doc.queue_batch_size = 50
        doc.verify_webhook_signature = 1
        doc.save(ignore_permissions=True)
        frappe.db.commit()


def after_uninstall():
    pass
