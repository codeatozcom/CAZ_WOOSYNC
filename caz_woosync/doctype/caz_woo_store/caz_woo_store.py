from urllib.parse import quote as url_quote

import frappe
from frappe.model.document import Document


class CazWooStore(Document):
    def validate(self):
        if self.woo_url:
            self.woo_url = self.woo_url.rstrip("/")
        if not self.woo_api_version:
            self.woo_api_version = "wc/v3"
        self._validate_url()
        self._validate_no_duplicate_url()

    def _validate_url(self):
        """Ensure woo_url starts with http:// or https://."""
        if self.woo_url and not (self.woo_url.startswith("https://") or self.woo_url.startswith("http://")):
            frappe.throw(
                "WooCommerce URL must start with http:// or https://."
            )

    def _validate_no_duplicate_url(self):
        """Prevent two active stores pointing to the same WooCommerce URL."""
        if self.woo_url:
            existing = frappe.db.get_value(
                "Caz Woo Store",
                {"woo_url": self.woo_url, "name": ["!=", self.name], "is_active": 1},
                "name",
            )
            if existing:
                frappe.throw(
                    f"Store '{existing}' is already configured for this WooCommerce URL."
                    " Each store URL must be unique."
                )

    def before_save(self):
        site_url = frappe.utils.get_url().rstrip("/")
        self.webhook_url = (
            f"{site_url}/api/method/caz_woosync.controller.receiver.handle_webhook"
            f"?store={url_quote(self.name or '')}"
        )

    @frappe.whitelist()
    def test_connection(self):
        from caz_woosync.api.connection import test_store_connection
        return test_store_connection(self.name)

    @frappe.whitelist()
    def install_webhooks(self):
        from caz_woosync.api.connection import install_webhooks
        return install_webhooks(self.name)
