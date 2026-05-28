import frappe
from frappe.model.document import Document


class CazWooCustomerMapping(Document):
    def before_insert(self):
        # Enforce composite uniqueness: one mapping per store + woo_customer_id
        existing = frappe.db.exists(
            "Caz Woo Customer Mapping",
            {
                "store": self.store,
                "woo_customer_id": self.woo_customer_id,
            },
        )
        if existing:
            frappe.throw(
                f"A mapping for WooCommerce customer ID {self.woo_customer_id} in store "
                f"'{self.store}' already exists (record: {existing}). "
                "Delete the existing mapping before creating a new one."
            )

    def before_save(self):
        self.last_synced = frappe.utils.now()
