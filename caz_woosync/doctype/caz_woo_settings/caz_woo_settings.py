import frappe
from frappe.model.document import Document


class CazWooSettings(Document):
    def validate(self):
        if self.max_requests_per_minute is not None and self.max_requests_per_minute < 1:
            frappe.throw("Max Requests per Minute must be at least 1. Please enter a valid value.")
        if self.queue_batch_size is not None and self.queue_batch_size < 1:
            frappe.throw("Queue Batch Size must be at least 1. Please enter a valid value.")


def get_settings():
    """Returns the global CAZ WooSync settings. Validates required fields are configured."""
    return frappe.get_single("Caz Woo Settings")
