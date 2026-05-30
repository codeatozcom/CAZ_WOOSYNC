import json

import frappe


@frappe.whitelist(allow_guest=True)
def handle_webhook(**kwargs):
    """
    Receives all WooCommerce webhook POST requests.
    URL: /api/method/caz_woosync.controller.receiver.handle_webhook?store=<store_name>

    Security: validates HMAC-SHA256 signature before touching any data.
    Processing: enqueues a background job — returns 200 immediately.
    """
    request = frappe.local.request

    # 1. Store identification
    store_name = frappe.form_dict.get("store")
    if not store_name:
        raise frappe.AuthenticationError("store parameter is required")

    if not frappe.db.exists("Caz Woo Store", store_name):
        raise frappe.AuthenticationError("Unknown store")

    # 2. Read raw body FIRST — before any parsing that could consume the stream
    raw_body = request.get_data(cache=True)

    # 3. Security: IP allowlist
    from caz_woosync.doctype.caz_woo_settings.caz_woo_settings import get_settings
    from caz_woosync.utils.security import get_client_ip, is_ip_allowed, verify_webhook_signature

    settings = get_settings()
    client_ip = get_client_ip()
    if not is_ip_allowed(client_ip, settings.allowed_webhook_ips or ""):
        frappe.log_error(
            f"Webhook from disallowed IP {client_ip} for store {store_name}",
            "CAZ WooSync Security",
        )
        raise frappe.AuthenticationError("IP address not allowed")

    # 4. Security: HMAC-SHA256 signature — must happen before any business logic
    if settings.verify_webhook_signature:
        signature = request.headers.get("X-WC-Webhook-Signature", "")
        store_doc = frappe.get_doc("Caz Woo Store", store_name)
        webhook_secret = store_doc.webhook_secret or ""
        if not verify_webhook_signature(raw_body, signature, webhook_secret):
            frappe.log_error(
                f"Invalid HMAC signature from {client_ip} for store {store_name}. "
                f"Header signature: {signature[:20]}...",
                "CAZ WooSync Security",
            )
            raise frappe.AuthenticationError(
                "Invalid webhook signature. Check that the Webhook Secret matches in WooCommerce and ERPNext."
            )

    # 5. Parse event metadata from headers
    topic = request.headers.get("X-WC-Webhook-Topic", "")
    woo_id = request.headers.get("X-WC-Webhook-Resource-ID", "")

    # 6. Enqueue background processing — do not process synchronously
    frappe.enqueue(
        "caz_woosync.controller.receiver._process_webhook",
        queue="default",
        timeout=300,
        store_name=store_name,
        topic=topic,
        woo_id=str(woo_id),
        raw_body=raw_body.decode("utf-8", errors="replace"),
    )

    frappe.response["status"] = "queued"
    frappe.response["topic"] = topic


def _process_webhook(store_name: str, topic: str, woo_id: str, raw_body: str):
    """
    Background job: persists webhook data to the sync queue.
    Runs in a Frappe RQ worker — has its own DB connection and transaction.
    """
    entity_type = _topic_to_entity(topic)
    if not entity_type:
        return  # Unknown topic — silently ignore

    try:
        payload_dict = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        payload_dict = {"_raw": raw_body[:1000]}

    queue_doc = frappe.new_doc("Caz Woo Sync Queue")
    queue_doc.update({
        "store": store_name,
        "direction": "woo_to_erp",
        "entity_type": entity_type,
        "woo_id": woo_id,
        "status": "Queued",
        "payload": json.dumps(payload_dict),
    })
    queue_doc.insert(ignore_permissions=True)
    frappe.db.commit()


def _topic_to_entity(topic: str):
    """Map WooCommerce webhook topic to sync queue entity type."""
    mapping = {
        "order": "Order",
        "product": "Product",
        "customer": "Customer",
        "coupon": "Coupon",
    }
    resource = topic.split(".")[0] if topic else ""
    return mapping.get(resource)
