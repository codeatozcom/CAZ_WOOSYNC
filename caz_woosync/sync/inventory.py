import frappe
from frappe.utils import cstr, flt, today


# ---------------------------------------------------------------------------
# ERPNext → WooCommerce
# ---------------------------------------------------------------------------


def sync_stock_to_woo(store_name, item_code, warehouse=None):
    """
    Push ERPNext stock level for item_code to WooCommerce.
    warehouse: override store.warehouse if provided.
    Called after Stock Ledger Entry is submitted.
    """
    from caz_woosync.utils.rate_limiter import WooCommerceClient

    # 1. Fetch ERPNext Item — check is_stock_item=1, else skip
    is_stock_item = frappe.db.get_value("Item", item_code, "is_stock_item")
    if not is_stock_item:
        frappe.logger("caz_woosync").debug(
            f"CAZ WooSync: skipping stock push for non-stock item {item_code}"
        )
        return

    # 2. Check Caz Woo Item Mapping to get woo_product_id for this store+item_code
    mapping = frappe.db.get_value(
        "Caz Woo Item Mapping",
        {"store": store_name, "erp_item": item_code},
        ["name", "woo_id", "product_type", "woo_variant_id"],
        as_dict=True,
    )
    if not mapping or not mapping.woo_id:
        # Item not synced to this store — skip silently
        return

    store = frappe.get_doc("Caz Woo Store", store_name)

    # Check store-level flag
    if not getattr(store, "sync_stock_to_woo", 1):
        return

    # 3. Determine warehouse
    wh = warehouse or store.warehouse
    if not wh:
        frappe.log_error(
            f"CAZ WooSync: no warehouse configured for store '{store_name}'. "
            f"Cannot push stock for item {item_code}.",
            "CAZ WooSync: Inventory Sync Error",
        )
        return

    # 4. Get actual stock from Bin
    qty = flt(
        frappe.db.get_value(
            "Bin",
            {"item_code": item_code, "warehouse": wh},
            "actual_qty",
        )
        or 0
    )

    # Apply threshold check
    threshold = getattr(store, "stock_sync_threshold", 0) or 0
    if threshold > 0:
        current_woo_qty = _get_woo_stock(store_name, mapping, store)
        if current_woo_qty is not None and abs(qty - current_woo_qty) <= threshold:
            frappe.logger("caz_woosync").debug(
                f"CAZ WooSync: skipping stock push for {item_code} (diff within threshold {threshold})"
            )
            return

    # Clamp negative quantities to zero
    stock_qty = max(0, int(qty))

    # 5 & 6. Push to WooCommerce — handle variation vs simple product
    client = WooCommerceClient(store_name)
    woo_payload = {"manage_stock": True, "stock_quantity": stock_qty}

    try:
        if mapping.product_type == "variation" and mapping.woo_variant_id:
            # For variations: PUT /products/{parent_id}/variations/{variation_id}
            resp = client.put(
                f"products/{mapping.woo_id}/variations/{mapping.woo_variant_id}",
                woo_payload,
            )
        else:
            # Simple product: PUT /products/{woo_product_id}
            resp = client.put(f"products/{mapping.woo_id}", woo_payload)

        if resp.status_code not in (200, 201):
            frappe.throw(
                f"WooCommerce rejected stock update for product {mapping.woo_id} "
                f"(HTTP {resp.status_code}): {resp.text[:300]}"
            )

        # 7. Log success
        frappe.logger("caz_woosync").info(
            f"CAZ WooSync: pushed stock {stock_qty} for item {item_code} "
            f"to WooCommerce product {mapping.woo_id} in store {store_name}"
        )

        # 8. Update last_synced
        frappe.db.set_value("Caz Woo Item Mapping", mapping.name, "last_synced", frappe.utils.now())

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"CAZ WooSync: failed to push stock for item {item_code} to store {store_name}",
        )
        raise


def _get_woo_stock(store_name, mapping, store):
    """Fetch current stock quantity from WooCommerce for threshold comparison."""
    try:
        from caz_woosync.utils.rate_limiter import WooCommerceClient
        client = WooCommerceClient(store_name)
        if mapping.product_type == "variation" and mapping.woo_variant_id:
            resp = client.get(f"products/{mapping.woo_id}/variations/{mapping.woo_variant_id}")
        else:
            resp = client.get(f"products/{mapping.woo_id}")
        if resp.status_code == 200:
            return flt(resp.json().get("stock_quantity") or 0)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# WooCommerce → ERPNext
# ---------------------------------------------------------------------------


def sync_stock_from_woo(store_name, woo_product_id, payload=None):
    """
    Pull WooCommerce stock level and create an ERPNext Stock Reconciliation.
    payload: webhook JSON (None → fetch from WC API).
    Called when a WC product.updated webhook arrives with stock change.
    """
    from caz_woosync.utils.rate_limiter import WooCommerceClient

    store = frappe.get_doc("Caz Woo Store", store_name)

    # Check store-level flag
    if not getattr(store, "sync_stock_from_woo", 0):
        return

    # 1. If payload None: fetch from API
    if payload is None:
        client = WooCommerceClient(store_name)
        resp = client.get(f"products/{woo_product_id}")
        if resp.status_code != 200:
            frappe.throw(
                f"WooCommerce API returned HTTP {resp.status_code} for product "
                f"{woo_product_id}. Check credentials and product existence."
            )
        payload = resp.json()

    # 2. Only proceed if manage_stock is True
    if not payload.get("manage_stock"):
        return

    # 3. Get WC stock quantity
    wc_stock = int(flt(payload.get("stock_quantity") or 0))
    # Clamp to zero
    wc_stock = max(0, wc_stock)

    # 4. Find ERPNext item_code via Caz Woo Item Mapping
    mapping = frappe.db.get_value(
        "Caz Woo Item Mapping",
        {"store": store_name, "woo_id": cstr(woo_product_id)},
        ["name", "erp_item"],
        as_dict=True,
    )
    if not mapping or not mapping.erp_item:
        # No mapping — skip silently
        return

    item_code = mapping.erp_item

    # 5. Determine warehouse
    warehouse = store.warehouse
    if not warehouse:
        frappe.log_error(
            f"CAZ WooSync: no warehouse configured for store '{store_name}'. "
            f"Cannot create Stock Reconciliation for item {item_code}.",
            "CAZ WooSync: Inventory Sync Error",
        )
        return

    # 6. Get current ERPNext qty from Bin
    current_qty = flt(
        frappe.db.get_value(
            "Bin",
            {"item_code": item_code, "warehouse": warehouse},
            "actual_qty",
        )
        or 0
    )

    # 7. If quantities match: skip
    if int(current_qty) == wc_stock:
        frappe.logger("caz_woosync").debug(
            f"CAZ WooSync: stock for {item_code} already matches WooCommerce ({wc_stock}). Skipping."
        )
        return

    # 8. Create Stock Reconciliation
    sr = frappe.new_doc("Stock Reconciliation")
    sr.company = store.company
    sr.purpose = "Stock Reconciliation"
    sr.posting_date = today()
    sr.remarks = f"CAZ WooSync: stock reconciliation from WooCommerce product {woo_product_id}"
    sr.append("items", {
        "item_code": item_code,
        "warehouse": warehouse,
        "qty": wc_stock,
    })
    sr.insert(ignore_permissions=True)
    sr.submit()

    frappe.logger("caz_woosync").info(
        f"CAZ WooSync: created Stock Reconciliation {sr.name} for item {item_code} "
        f"with qty {wc_stock} from WooCommerce product {woo_product_id}"
    )

    # Update last_synced on mapping
    frappe.db.set_value("Caz Woo Item Mapping", mapping.name, "last_synced", frappe.utils.now())


# ---------------------------------------------------------------------------
# Bulk push
# ---------------------------------------------------------------------------


def push_all_stock(store_name):
    """
    Bulk push all mapped items' stock levels to WooCommerce.
    Called by the daily health check or manual trigger.
    """
    mappings = frappe.get_all(
        "Caz Woo Item Mapping",
        filters={"store": store_name},
        fields=["erp_item"],
    )

    success_count = 0
    fail_count = 0

    for mapping in mappings:
        try:
            sync_stock_to_woo(store_name, mapping.erp_item)
            success_count += 1
        except Exception:
            fail_count += 1
            frappe.log_error(
                frappe.get_traceback(),
                f"CAZ WooSync: push_all_stock failed for item {mapping.erp_item} in store {store_name}",
            )

    frappe.logger("caz_woosync").info(
        f"CAZ WooSync: push_all_stock for store {store_name} — "
        f"{success_count} succeeded, {fail_count} failed, "
        f"{len(mappings)} total mapped items."
    )


# ---------------------------------------------------------------------------
# ERPNext doc_events hook
# ---------------------------------------------------------------------------


def on_stock_ledger_submit(doc, method=None):
    """Triggered when a Stock Ledger Entry is submitted. Queues stock sync."""
    if (
        frappe.flags.in_migrate
        or frappe.flags.in_patch
        or frappe.flags.in_import
        or frappe.flags.in_install
    ):
        return

    def _enqueue():
        stores = frappe.get_all(
            "Caz Woo Store",
            filters={
                "is_active": 1,
                "sync_direction": ["in", ["Both Ways", "ERPNext to WooCommerce"]],
            },
            fields=["name", "warehouse"],
        )
        for store in stores:
            if store.warehouse and store.warehouse != doc.warehouse:
                continue  # SLE is not for this store's warehouse
            if not frappe.db.exists(
                "Caz Woo Item Mapping",
                {"store": store.name, "erp_item": doc.item_code},
            ):
                continue
            # Dedup: skip if already queued for this item+store
            if frappe.db.exists(
                "Caz Woo Sync Queue",
                {
                    "store": store.name,
                    "erp_docname": doc.item_code,
                    "entity_type": "Inventory",
                    "status": ["in", ["Queued", "Processing"]],
                },
            ):
                continue
            q = frappe.new_doc("Caz Woo Sync Queue")
            q.update(
                {
                    "store": store.name,
                    "direction": "erp_to_woo",
                    "entity_type": "Inventory",
                    "erp_doctype": "Item",
                    "erp_docname": doc.item_code,
                    "status": "Queued",
                    "payload": "{}",
                }
            )
            q.insert(ignore_permissions=True)
        frappe.db.commit()

    frappe.db.after_commit(_enqueue)
