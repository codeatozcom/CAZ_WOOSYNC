import frappe
from frappe.model.document import Document


class CazWooStore(Document):
    def validate(self):
        if self.woo_url:
            self.woo_url = self.woo_url.rstrip("/")
        if not self.woo_api_version:
            self.woo_api_version = "wc/v3"

    def before_save(self):
        site_url = frappe.utils.get_url().rstrip("/")
        self.webhook_url = (
            f"{site_url}/api/method/caz_woosync.controller.receiver.handle_webhook"
            f"?store={frappe.utils.quote(self.name or '')}"
        )

    @frappe.whitelist()
    def test_connection(self):
        from caz_woosync.api.connection import test_store_connection
        return test_store_connection(self.name)

    @frappe.whitelist()
    def install_webhooks(self):
        from caz_woosync.api.connection import install_webhooks
        return install_webhooks(self.name)
