import frappe


def process_sync_queue():
    """
    Scheduled every 5 minutes. Dispatches pending sync queue items to background workers.
    """
    try:
        from caz_woosync.sync.dispatcher import dispatch_all_stores
        dispatch_all_stores()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "CAZ WooSync: process_sync_queue failed")


def poll_woocommerce_changes():
    """
    Scheduled every 15 minutes as cron fallback.
    Fetches products modified since last_sync_time and queues them.
    WooCommerce REST API does not support modified_after filter directly —
    we use orderby=modified&order=desc and stop when records are older than last_sync_time.
    """
    try:
        from caz_woosync.utils.rate_limiter import WooCommerceClient

        stores = frappe.get_all(
            "Caz Woo Store",
            filters={"is_active": 1},
            fields=["name", "last_sync_time"],
        )
        for store in stores:
            try:
                _poll_store(store)
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    f"CAZ WooSync: poll failed for store {store.name}",
                )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "CAZ WooSync: poll_woocommerce_changes failed")


def _poll_store(store):
    """Poll a single store for changed products and orders."""
    from caz_woosync.utils.rate_limiter import WooCommerceClient

    client = WooCommerceClient(store.name)
    last_sync = store.last_sync_time

    _poll_products(store, client, last_sync)
    _poll_orders(store, client, last_sync)
    _poll_customers(store, client, last_sync)

    # Update last_sync_time
    frappe.db.set_value("Caz Woo Store", store.name, "last_sync_time", frappe.utils.now())
    frappe.db.commit()


def _poll_products(store, client, last_sync):
    """Poll WooCommerce products modified since last_sync_time."""
    from frappe.utils import get_datetime

    page = 1
    while True:
        resp = client.get(
            "products",
            params={"orderby": "modified", "order": "desc", "per_page": 50, "page": page},
        )
        if resp.status_code != 200:
            break

        products = resp.json()
        if not products:
            break

        found_older = False
        for product in products:
            date_modified = product.get("date_modified")
            if last_sync and date_modified:
                try:
                    mod_dt = get_datetime(date_modified.replace("T", " "))
                    if mod_dt <= get_datetime(last_sync):
                        found_older = True
                        break
                except Exception:
                    pass

            woo_id = str(product.get("id", ""))
            if not woo_id:
                continue

            # Skip if already queued
            if frappe.db.exists(
                "Caz Woo Sync Queue",
                {"store": store.name, "woo_id": woo_id, "status": ["in", ["Queued", "Processing"]]},
            ):
                continue

            queue_doc = frappe.new_doc("Caz Woo Sync Queue")
            queue_doc.update({
                "store": store.name,
                "direction": "woo_to_erp",
                "entity_type": "Product",
                "woo_id": woo_id,
                "status": "Queued",
                "payload": "{}",
            })
            queue_doc.insert(ignore_permissions=True)

        frappe.db.commit()

        if found_older or len(products) < 50:
            break
        page += 1


def _poll_orders(store, client, last_sync):
    """Poll WooCommerce orders modified since last_sync_time."""
    from frappe.utils import get_datetime

    page = 1
    while True:
        resp = client.get(
            "orders",
            params={"orderby": "modified", "order": "desc", "per_page": 50, "page": page},
        )
        if resp.status_code != 200:
            break

        orders = resp.json()
        if not orders:
            break

        found_older = False
        for order in orders:
            date_modified = order.get("date_modified")
            if last_sync and date_modified:
                try:
                    mod_dt = get_datetime(date_modified.replace("T", " "))
                    if mod_dt <= get_datetime(last_sync):
                        found_older = True
                        break
                except Exception:
                    pass

            woo_id = str(order.get("id", ""))
            if not woo_id:
                continue

            # Skip if already queued
            if frappe.db.exists(
                "Caz Woo Sync Queue",
                {"store": store.name, "woo_id": woo_id, "entity_type": "Order",
                 "status": ["in", ["Queued", "Processing"]]},
            ):
                continue

            queue_doc = frappe.new_doc("Caz Woo Sync Queue")
            queue_doc.update({
                "store": store.name,
                "direction": "woo_to_erp",
                "entity_type": "Order",
                "woo_id": woo_id,
                "status": "Queued",
                "payload": "{}",
            })
            queue_doc.insert(ignore_permissions=True)

        frappe.db.commit()

        if found_older or len(orders) < 50:
            break
        page += 1


def _poll_customers(store, client, last_sync):
    """Poll WooCommerce customers modified since last_sync_time."""
    from frappe.utils import get_datetime

    page = 1
    while True:
        resp = client.get(
            "customers",
            params={"orderby": "modified", "order": "desc", "per_page": 50, "page": page},
        )
        if resp.status_code != 200:
            break

        customers = resp.json()
        if not customers:
            break

        found_older = False
        for customer in customers:
            date_modified = customer.get("date_modified")
            if last_sync and date_modified:
                try:
                    mod_dt = get_datetime(date_modified.replace("T", " "))
                    if mod_dt <= get_datetime(last_sync):
                        found_older = True
                        break
                except Exception:
                    pass

            woo_id = str(customer.get("id", ""))
            if not woo_id:
                continue

            # Skip if already queued
            if frappe.db.exists(
                "Caz Woo Sync Queue",
                {"store": store.name, "woo_id": woo_id, "entity_type": "Customer",
                 "status": ["in", ["Queued", "Processing"]]},
            ):
                continue

            queue_doc = frappe.new_doc("Caz Woo Sync Queue")
            queue_doc.update({
                "store": store.name,
                "direction": "woo_to_erp",
                "entity_type": "Customer",
                "woo_id": woo_id,
                "status": "Queued",
                "payload": "{}",
            })
            queue_doc.insert(ignore_permissions=True)

        frappe.db.commit()

        if found_older or len(customers) < 50:
            break
        page += 1


def daily_health_check():
    """Scheduled daily. Checks connection health for all active stores."""
    stores = frappe.get_all(
        "Caz Woo Store",
        filters={"is_active": 1},
        fields=["name"],
    )
    for store in stores:
        try:
            from caz_woosync.api.connection import test_store_connection
            test_store_connection(store.name)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"CAZ WooSync health check failed: {store.name}",
            )
