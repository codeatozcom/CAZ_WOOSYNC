import frappe


def start_bulk_import(store_name, entity_types=None, since_date=None, limit=None):
    """
    Kick off a full bulk import of WooCommerce data into ERPNext.

    entity_types: list of "Product"|"Order"|"Customer" (default: all three)
    since_date: only import records created/modified after this date (ISO string)
    limit: max records per entity type (None = all)
    Enqueues one background job per entity type to avoid timeout.
    Returns {"queued": [...entity_types...]}
    """
    entity_types = entity_types or ["Product", "Order", "Customer"]
    for entity_type in entity_types:
        frappe.enqueue(
            "caz_woosync.sync.bulk_import._import_entity_batch",
            queue="long",
            timeout=7200,
            store_name=store_name,
            entity_type=entity_type,
            since_date=since_date,
            limit=limit,
        )
    return {"queued": entity_types}


def _import_entity_batch(store_name, entity_type, since_date=None, limit=None):
    """
    Background job: paginate through all WC records of entity_type and queue each for sync.
    Idempotent: skips records already in the sync queue or already mapped.
    """
    from caz_woosync.utils.rate_limiter import get_woo_client

    client = get_woo_client(store_name)
    per_page = 50
    page = 1
    total_processed = 0
    total_queued = 0

    while True:
        params = {"per_page": per_page, "page": page}

        if entity_type == "Product":
            if since_date:
                params["after"] = since_date
            endpoint = "products"
        elif entity_type == "Order":
            if since_date:
                params["after"] = since_date
            endpoint = "orders"
        elif entity_type == "Customer":
            endpoint = "customers"
        else:
            frappe.log_error(f"Unknown entity_type: {entity_type}", "CAZ WooSync Bulk Import")
            return

        try:
            response = client.get(endpoint, params=params)
            if response.status_code != 200:
                frappe.log_error(
                    f"WC API error {response.status_code}: {response.text[:500]}",
                    f"CAZ WooSync Bulk Import — {entity_type} page {page}",
                )
                break
            records = response.json()
        except Exception as exc:
            frappe.log_error(str(exc), f"CAZ WooSync Bulk Import — {entity_type} page {page}")
            break

        if not records:
            break

        for record in records:
            if limit and total_processed >= limit:
                break

            woo_id = str(record.get("id", ""))

            # Check if already mapped / already queued
            if entity_type == "Product":
                already_mapped = frappe.db.exists(
                    "Caz Woo Item Mapping", {"woo_product_id": woo_id, "store": store_name}
                )
                already_queued = frappe.db.exists(
                    "Caz Woo Sync Queue",
                    {"woo_id": woo_id, "entity_type": "Product", "store": store_name, "status": "Queued"},
                )
            elif entity_type == "Order":
                already_mapped = frappe.db.exists(
                    "Caz Woo Order Mapping", {"woo_order_id": woo_id, "store": store_name}
                )
                already_queued = frappe.db.exists(
                    "Caz Woo Sync Queue",
                    {"woo_id": woo_id, "entity_type": "Order", "store": store_name, "status": "Queued"},
                )
            elif entity_type == "Customer":
                already_mapped = frappe.db.exists(
                    "Caz Woo Customer Mapping", {"woo_customer_id": woo_id, "store": store_name}
                )
                already_queued = frappe.db.exists(
                    "Caz Woo Sync Queue",
                    {"woo_id": woo_id, "entity_type": "Customer", "store": store_name, "status": "Queued"},
                )

            if already_mapped or already_queued:
                total_processed += 1
                continue

            # Create queue record
            doc = frappe.new_doc("Caz Woo Sync Queue")
            doc.store = store_name
            doc.entity_type = entity_type
            doc.woo_id = woo_id
            doc.status = "Queued"
            doc.direction = "woo_to_erp"
            doc.insert(ignore_permissions=True)
            total_processed += 1
            total_queued += 1

            if total_processed % 100 == 0:
                frappe.log_error(
                    f"Bulk import progress — {entity_type}: {total_processed} processed, {total_queued} queued",
                    f"CAZ WooSync Bulk Import Progress",
                )

            if limit and total_processed >= limit:
                break

        frappe.db.commit()

        if limit and total_processed >= limit:
            break

        if len(records) < per_page:
            break

        page += 1

    frappe.log_error(
        f"Bulk import complete — {entity_type}: {total_processed} processed, {total_queued} queued",
        "CAZ WooSync Bulk Import Done",
    )


def get_import_progress(store_name):
    """
    Return import progress stats:
    {
      "queued": int,      # queue items waiting
      "processing": int,
      "done": int,
      "failed": int,
      "total_mapped": {"products": int, "orders": int, "customers": int}
    }
    """
    counts = {}
    for status in ("Queued", "Processing", "Done", "Failed"):
        rows = frappe.db.sql(
            """
            SELECT COUNT(*) AS cnt
            FROM `tabCaz Woo Sync Queue`
            WHERE store=%s AND status=%s
            """,
            (store_name, status),
            as_dict=True,
        )
        counts[status] = rows[0]["cnt"] if rows else 0

    products_mapped = frappe.db.sql(
        "SELECT COUNT(*) AS cnt FROM `tabCaz Woo Item Mapping` WHERE store=%s",
        (store_name,),
        as_dict=True,
    )
    orders_mapped = frappe.db.sql(
        "SELECT COUNT(*) AS cnt FROM `tabCaz Woo Order Mapping` WHERE store=%s",
        (store_name,),
        as_dict=True,
    )
    customers_mapped = frappe.db.sql(
        "SELECT COUNT(*) AS cnt FROM `tabCaz Woo Customer Mapping` WHERE store=%s",
        (store_name,),
        as_dict=True,
    )

    return {
        "queued": counts.get("Queued", 0),
        "processing": counts.get("Processing", 0),
        "done": counts.get("Done", 0),
        "failed": counts.get("Failed", 0),
        "total_mapped": {
            "products": products_mapped[0]["cnt"] if products_mapped else 0,
            "orders": orders_mapped[0]["cnt"] if orders_mapped else 0,
            "customers": customers_mapped[0]["cnt"] if customers_mapped else 0,
        },
    }


def cancel_bulk_import(store_name):
    """Cancel all Queued bulk import items (set status=Skipped)."""
    frappe.db.sql(
        """
        UPDATE `tabCaz Woo Sync Queue`
        SET status='Skipped', error_log='Cancelled by user'
        WHERE store=%s AND status='Queued'
        """,
        (store_name,),
    )
    frappe.db.commit()
