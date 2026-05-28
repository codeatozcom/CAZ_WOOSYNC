import frappe
from frappe.model.document import Document


class CazWooItemMapping(Document):
    def before_insert(self):
        # Enforce composite uniqueness: one mapping per store+woo_id+variant
        existing = frappe.db.exists(
            "Caz Woo Item Mapping",
            {
                "store": self.store,
                "woo_id": self.woo_id,
                "woo_variant_id": self.woo_variant_id or "",
            },
        )
        if existing:
            frappe.throw(
                f"A mapping for WooCommerce product ID {self.woo_id} in store "
                f"'{self.store}' already exists (record: {existing}). "
                "Delete the existing mapping before creating a new one."
            )

    def before_save(self):
        self.last_synced = frappe.utils.now()
