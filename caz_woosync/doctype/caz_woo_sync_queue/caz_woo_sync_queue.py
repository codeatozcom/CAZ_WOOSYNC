import frappe
from frappe.model.document import Document


class CazWooSyncQueue(Document):
    def mark_processing(self):
        self.db_set("status", "Processing")
        self.db_set("last_attempt", frappe.utils.now())
        self.db_set("attempt_count", (self.attempt_count or 0) + 1)

    def mark_done(self):
        self.db_set("status", "Done")

    def mark_failed(self, error: str):
        self.db_set("status", "Failed")
        self.db_set("error_log", str(error)[:5000])

    def mark_skipped(self, reason: str = ""):
        self.db_set("status", "Skipped")
        if reason:
            self.db_set("error_log", str(reason)[:500])
