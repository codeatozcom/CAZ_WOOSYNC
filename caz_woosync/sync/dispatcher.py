import time

import frappe
from frappe.utils import now_datetime, time_diff_in_seconds

RETRY_DELAYS = [0, 60, 300, 900, 3600]  # seconds: immediate, 1m, 5m, 15m, 1h
MAX_ATTEMPTS = len(RETRY_DELAYS)  # 5


def dispatch_all_stores():
    """Entry point called by tasks.process_sync_queue (scheduled job)."""
    settings = frappe.get_single("Caz Woo Settings")
    batch_size = settings.queue_batch_size or 50

    stores = frappe.get_all(
        "Caz Woo Store",
        filters={"is_active": 1},
        fields=["name", "api_rate_limit"],
    )
    for store in stores:
        _dispatch_store(store.name, store.api_rate_limit or 60, batch_size)


def _dispatch_store(store_name, rate_limit, batch_size):
    """Process pending queue items for a single store."""
    from caz_woosync.utils.rate_limiter import check_rate_limit

    now = now_datetime()

    # Fetch candidates: Queued items that are either first-attempt or overdue for retry
    candidates = frappe.db.sql(
        """
        SELECT name, attempt_count, last_attempt
        FROM `tabCaz Woo Sync Queue`
        WHERE store = %s
          AND status = 'Queued'
          AND attempt_count < %s
        ORDER BY creation ASC
        LIMIT %s
        """,
        (store_name, MAX_ATTEMPTS, batch_size),
        as_dict=True,
    )

    for row in candidates:
        attempt = row.attempt_count or 0
        # For retries (attempt > 0), check if enough time has elapsed
        if attempt > 0 and row.last_attempt:
            elapsed = time_diff_in_seconds(now, row.last_attempt)
            required_delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
            if elapsed < required_delay:
                continue  # Not ready for retry yet

        if not check_rate_limit(store_name, rate_limit):
            break  # Rate limit hit — stop processing this store for this tick

        # Mark as Processing before fan-out to prevent duplicate pickup
        frappe.db.set_value(
            "Caz Woo Sync Queue",
            row.name,
            {
                "status": "Processing",
                "last_attempt": now,
                "attempt_count": attempt + 1,
            },
        )
        frappe.db.commit()

        frappe.enqueue(
            "caz_woosync.sync.dispatcher._process_one",
            queue="default",
            timeout=300,
            queue_item_name=row.name,
        )


def _process_one(queue_item_name):
    """Background worker: routes a single queue item to the correct sync handler."""
    doc = frappe.get_doc("Caz Woo Sync Queue", queue_item_name)

    # Guard: another worker may have already processed this
    if doc.status != "Processing":
        return

    try:
        _route(doc)
        doc.mark_done()
    except Exception:
        error = frappe.get_traceback()
        frappe.log_error(error, f"CAZ WooSync: sync failed [{doc.entity_type} #{doc.woo_id}]")
        if (doc.attempt_count or 0) >= MAX_ATTEMPTS:
            doc.mark_failed(error)
            _send_failure_alert(doc)
        else:
            # Reset to Queued so it will be retried on next tick
            doc.db_set("status", "Queued")
            doc.db_set("error_log", str(error)[:5000])
        frappe.db.commit()


def _route(doc):
    """Route queue item to correct sync handler."""
    if doc.direction == "woo_to_erp":
        if doc.entity_type == "Product":
            from caz_woosync.sync.items import sync_product_to_erp
            import json
            payload = json.loads(doc.payload) if doc.payload and doc.payload != "{}" else None
            sync_product_to_erp(doc.store, doc.woo_id, payload)
        elif doc.entity_type == "Order":
            from caz_woosync.sync.orders import sync_order_to_erp
            import json
            payload = json.loads(doc.payload) if doc.payload and doc.payload != "{}" else None
            sync_order_to_erp(doc.store, doc.woo_id, payload)
        elif doc.entity_type == "Customer":
            from caz_woosync.sync.customers import sync_customer_to_erp
            import json
            payload = json.loads(doc.payload) if doc.payload and doc.payload != "{}" else None
            sync_customer_to_erp(doc.store, doc.woo_id, payload)
        elif doc.entity_type == "Inventory":
            from caz_woosync.sync.inventory import sync_stock_from_woo
            import json
            payload = json.loads(doc.payload) if doc.payload and doc.payload != "{}" else None
            sync_stock_from_woo(doc.store, doc.woo_id, payload)
        else:
            doc.mark_skipped(f"Unknown entity type: {doc.entity_type}")
    elif doc.direction == "erp_to_woo":
        if doc.entity_type == "Product":
            from caz_woosync.sync.items import sync_item_to_woo
            sync_item_to_woo(doc.store, doc.erp_docname)
        elif doc.entity_type == "Inventory":
            from caz_woosync.sync.inventory import sync_stock_to_woo
            sync_stock_to_woo(doc.store, doc.erp_docname)
        else:
            doc.mark_skipped(f"erp_to_woo not implemented for {doc.entity_type}")
    else:
        doc.mark_skipped(f"Unknown direction: {doc.direction}")


def _send_failure_alert(doc):
    """Send alert email when a queue item exhausts all retry attempts."""
    try:
        settings = frappe.get_single("Caz Woo Settings")
        if settings.alert_email:
            frappe.sendmail(
                recipients=[settings.alert_email],
                subject=f"CAZ WooSync: Sync Failed — {doc.entity_type} #{doc.woo_id}",
                message=(
                    f"Sync queue item <strong>{doc.name}</strong> failed after "
                    f"{MAX_ATTEMPTS} attempts.<br><br>"
                    f"<strong>Store:</strong> {frappe.utils.escape_html(doc.store)}<br>"
                    f"<strong>Entity:</strong> {frappe.utils.escape_html(doc.entity_type)} "
                    f"#{frappe.utils.escape_html(str(doc.woo_id))}<br><br>"
                    f"<strong>Error:</strong><br><pre>{frappe.utils.escape_html(doc.error_log or '')}</pre>"
                ),
                now=True,
            )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "CAZ WooSync: failed to send failure alert")
