import time

import frappe


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
                results.append({"topic": topic, "success": True})
            else:
                results.append({
                    "topic": topic,
                    "success": False,
                    "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
                })
        except Exception as exc:
            results.append({"topic": topic, "success": False, "error": str(exc)})

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
