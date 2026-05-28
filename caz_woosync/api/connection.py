import time

import frappe
from frappe.utils import cstr


@frappe.whitelist()
def test_store_connection(store_name: str) -> dict:
    """
    Test connectivity to a WooCommerce store.
    Returns {"success": bool, "message": str, "response_ms": int}.
    """
    try:
        from caz_woosync.utils.rate_limiter import get_woo_client
        client = get_woo_client(store_name)
        t0 = time.monotonic()
        response = client.get("")
        response_ms = int((time.monotonic() - t0) * 1000)

        if response.status_code == 200:
            frappe.db.set_value(
                "Caz Woo Store",
                store_name,
                {
                    "connection_status": "Connected",
                    "last_connection_check": frappe.utils.now(),
                },
            )
            return {
                "success": True,
                "message": "Connected successfully to WooCommerce.",
                "response_ms": response_ms,
            }
        else:
            _mark_store_failed(store_name)
            return {
                "success": False,
                "message": (
                    f"WooCommerce returned HTTP {response.status_code}. "
                    "Check your Consumer Key and Consumer Secret in WooCommerce > Settings > Advanced > REST API."
                ),
                "response_ms": response_ms,
            }
    except Exception as exc:
        _mark_store_failed(store_name)
        frappe.log_error(frappe.get_traceback(), f"CAZ WooSync: connection test failed for {store_name}")
        return {
            "success": False,
            "message": (
                f"Could not connect to WooCommerce: {exc}. "
                "Check that your Store URL is correct and publicly accessible."
            ),
            "response_ms": 0,
        }


@frappe.whitelist()
def install_webhooks(store_name: str) -> dict:
    """
    Install all 8 required WooCommerce webhooks for the given store.
    Returns {"results": [{"topic": str, "success": bool, "error": str}]}.
    """
    from caz_woosync.utils.rate_limiter import get_woo_client

    store = frappe.get_doc("Caz Woo Store", store_name)
    client = get_woo_client(store_name)
    delivery_url = store.webhook_url
    if not delivery_url:
        frappe.throw(
            "Webhook URL is not set. Save the store record first so ERPNext can generate the webhook URL."
        )

    webhook_secret = store.get_password("webhook_secret") or ""

    topics = [
        "order.created",
        "order.updated",
        "order.deleted",
        "product.created",
        "product.updated",
        "product.deleted",
        "customer.created",
        "customer.updated",
    ]

    # Fetch existing webhooks to avoid duplicates
    existing_response = client.get("webhooks", params={"per_page": 100})
    existing_topics = set()
    if existing_response.status_code == 200:
        for wh in existing_response.json():
            if wh.get("delivery_url") == delivery_url:
                existing_topics.add(wh.get("topic"))

    results = []
    woo_webhook_ids = {}  # topic → woo id

    # Collect IDs of already-installed webhooks
    if existing_response.status_code == 200:
        for wh in existing_response.json():
            if wh.get("delivery_url") == delivery_url:
                woo_webhook_ids[wh.get("topic")] = str(wh.get("id", ""))

    for topic in topics:
        if topic in existing_topics:
            results.append({"topic": topic, "success": True, "note": "already installed"})
            continue
        payload = {
            "name": f"CAZ WooSync — {topic}",
            "status": "active",
            "topic": topic,
            "delivery_url": delivery_url,
        }
        if webhook_secret:
            payload["secret"] = webhook_secret
        try:
            resp = client.post("webhooks", payload)
            if resp.status_code in (200, 201):
                woo_id = str(resp.json().get("id", ""))
                woo_webhook_ids[topic] = woo_id
                results.append({"topic": topic, "success": True, "woo_webhook_id": woo_id})
            else:
                results.append({
                    "topic": topic,
                    "success": False,
                    "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
                })
        except Exception as exc:
            results.append({"topic": topic, "success": False, "error": str(exc)})

    # Update the store's webhooks child table
    store.webhooks = []
    for topic in topics:
        result = next((r for r in results if r["topic"] == topic), {})
        store.append("webhooks", {
            "topic": topic,
            "woo_webhook_id": woo_webhook_ids.get(topic, ""),
            "status": "Active" if result.get("success") else "Failed",
            "delivery_url": delivery_url,
        })
    store.save(ignore_permissions=True)
    frappe.db.commit()

    return {"results": results}


@frappe.whitelist()
def get_connection_status(store_name: str) -> dict:
    """Return current connection status and stats for the dashboard."""
    store = frappe.get_doc("Caz Woo Store", store_name)
    today = frappe.utils.today()

    counts = frappe.db.sql(
        """
        SELECT status, COUNT(*) as cnt
        FROM `tabCaz Woo Sync Queue`
        WHERE store = %s AND DATE(creation) = %s
        GROUP BY status
        """,
        (store_name, today),
        as_dict=True,
    )
    count_map = {row["status"]: row["cnt"] for row in counts}

    return {
        "store_name": store.store_name,
        "woo_url": store.woo_url,
        "connection_status": store.connection_status,
        "last_connection_check": str(store.last_connection_check or ""),
        "is_active": store.is_active,
        "today_done": count_map.get("Done", 0),
        "today_failed": count_map.get("Failed", 0),
        "today_queued": count_map.get("Queued", 0),
    }


@frappe.whitelist()
def repair_webhooks(store_name: str) -> dict:
    """Re-install any missing webhooks. Same as install_webhooks but explicitly described as repair."""
    return install_webhooks(store_name)


def _mark_store_failed(store_name: str):
    frappe.db.set_value(
        "Caz Woo Store",
        store_name,
        {
            "connection_status": "Failed",
            "last_connection_check": frappe.utils.now(),
        },
    )


@frappe.whitelist()
def trigger_item_sync(store_name: str, woo_product_id: str) -> dict:
    """Manually queue a WooCommerce product for sync to ERPNext."""
    if not frappe.db.exists("Caz Woo Store", store_name):
        frappe.throw(f"Store '{store_name}' not found. Check the store name and try again.")

    queue_doc = frappe.new_doc("Caz Woo Sync Queue")
    queue_doc.update({
        "store": store_name,
        "direction": "woo_to_erp",
        "entity_type": "Product",
        "woo_id": cstr(woo_product_id),
        "status": "Queued",
        "payload": "{}",
    })
    queue_doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return {"queued": True, "queue_name": queue_doc.name}


@frappe.whitelist()
def get_order_sync_status(store_name: str, woo_order_id: str) -> dict:
    """Return sync status for a WooCommerce order."""
    mapping = frappe.db.get_value(
        "Caz Woo Order Mapping",
        {"store": store_name, "woo_order_id": cstr(woo_order_id)},
        ["sales_order", "woo_status", "erp_status", "last_synced", "sync_error"],
        as_dict=True,
    )
    if not mapping:
        return {"synced": False, "message": "No mapping found for this order."}
    return {
        "synced": True,
        "sales_order": mapping.sales_order,
        "woo_status": mapping.woo_status or "",
        "erp_status": mapping.erp_status or "",
        "last_synced": str(mapping.last_synced or ""),
        "sync_error": mapping.sync_error or "",
    }


@frappe.whitelist()
def trigger_order_sync(store_name: str, woo_order_id: str) -> dict:
    """Manually queue a WooCommerce order for sync."""
    if not frappe.db.exists("Caz Woo Store", store_name):
        frappe.throw(f"Store '{store_name}' not found. Check the store name and try again.")

    queue_doc = frappe.new_doc("Caz Woo Sync Queue")
    queue_doc.update({
        "store": store_name,
        "direction": "woo_to_erp",
        "entity_type": "Order",
        "woo_id": cstr(woo_order_id),
        "status": "Queued",
        "payload": "{}",
    })
    queue_doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return {"queued": True, "queue_name": queue_doc.name}


@frappe.whitelist()
def get_customer_sync_status(store_name: str, woo_customer_id: str) -> dict:
    """Return sync status for a WooCommerce customer."""
    mapping = frappe.db.get_value(
        "Caz Woo Customer Mapping",
        {"store": store_name, "woo_customer_id": cstr(woo_customer_id)},
        ["customer", "woo_email", "last_synced", "sync_error"],
        as_dict=True,
    )
    if not mapping:
        return {"synced": False, "message": "No mapping found for this customer."}
    return {
        "synced": True,
        "customer": mapping.customer,
        "woo_email": mapping.woo_email or "",
        "last_synced": str(mapping.last_synced or ""),
        "sync_error": mapping.sync_error or "",
    }


@frappe.whitelist()
def trigger_customer_sync(store_name: str, woo_customer_id: str) -> dict:
    """Manually queue a WooCommerce customer for sync."""
    if not frappe.db.exists("Caz Woo Store", store_name):
        frappe.throw(f"Store '{store_name}' not found. Check the store name and try again.")

    queue_doc = frappe.new_doc("Caz Woo Sync Queue")
    queue_doc.update({
        "store": store_name,
        "direction": "woo_to_erp",
        "entity_type": "Customer",
        "woo_id": cstr(woo_customer_id),
        "status": "Queued",
        "payload": "{}",
    })
    queue_doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return {"queued": True, "queue_name": queue_doc.name}


@frappe.whitelist()
def get_item_sync_status(store_name: str, woo_product_id: str) -> dict:
    """Return the sync status for a WooCommerce product from the item mapping table."""
    mapping = frappe.db.get_value(
        "Caz Woo Item Mapping",
        {"store": store_name, "woo_id": cstr(woo_product_id)},
        ["erp_item", "last_synced", "product_type"],
        as_dict=True,
    )
    if not mapping:
        return {"synced": False, "message": "No mapping found for this product."}
    return {
        "synced": True,
        "erp_item": mapping.erp_item,
        "last_synced": str(mapping.last_synced or ""),
        "product_type": mapping.product_type or "simple",
    }
