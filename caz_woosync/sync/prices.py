import frappe
from frappe.utils import cstr, flt


# ---------------------------------------------------------------------------
# ERPNext → WooCommerce
# ---------------------------------------------------------------------------


def sync_price_to_woo(store_name, item_code, price_list=None):
    """
    Push ERPNext Item Price to WooCommerce as regular_price.
    price_list: override store.item_price_list if provided.
    """
    from caz_woosync.utils.rate_limiter import WooCommerceClient

    # 1. Find woo_product_id via Caz Woo Item Mapping (store + item_code)
    mapping = frappe.db.get_value(
        "Caz Woo Item Mapping",
        {"store": store_name, "erp_item": item_code},
        ["name", "woo_id"],
        as_dict=True,
    )

    # 2. If no mapping: skip silently
    if not mapping or not mapping.woo_id:
        return

    store = frappe.get_doc("Caz Woo Store", store_name)

    # 5. Check store.push_prices_trigger — if "Manual" skip automatic push
    push_trigger = getattr(store, "push_prices_trigger", None) or "Scheduled"
    if push_trigger == "Manual":
        frappe.logger("caz_woosync").debug(
            f"CAZ WooSync: price push skipped for {item_code} (push_prices_trigger=Manual)"
        )
        return

    # 3. Get price from Item Price
    pl = price_list or getattr(store, "item_price_list", None) or "Standard Selling"
    price = flt(
        frappe.db.get_value(
            "Item Price",
            {"item_code": item_code, "price_list": pl, "selling": 1},
            "price_list_rate",
        )
        or 0
    )

    # 4. If no price found: skip
    if price <= 0:
        frappe.logger("caz_woosync").debug(
            f"CAZ WooSync: no selling price for item {item_code} in price list '{pl}'. Skipping push."
        )
        return

    # Apply currency rounding
    price = _apply_rounding(price, getattr(store, "currency_rounding", None))

    # 6. Build WC payload
    woo_payload = {"regular_price": str(price)}

    # 7. Also set sale_price if a sale Item Price exists
    sale_price_list = getattr(store, "sale_price_list", None)
    if sale_price_list:
        sale_price = flt(
            frappe.db.get_value(
                "Item Price",
                {"item_code": item_code, "price_list": sale_price_list, "selling": 1},
                "price_list_rate",
            )
            or 0
        )
        if sale_price > 0:
            sale_price = _apply_rounding(sale_price, getattr(store, "currency_rounding", None))
            woo_payload["sale_price"] = str(sale_price)

    # 8. PUT /products/{woo_product_id}
    client = WooCommerceClient(store_name)
    try:
        resp = client.put(f"products/{mapping.woo_id}", woo_payload)
        if resp.status_code not in (200, 201):
            frappe.throw(
                f"WooCommerce rejected price update for product {mapping.woo_id} "
                f"(HTTP {resp.status_code}): {resp.text[:300]}"
            )

        # 9. Log result
        frappe.logger("caz_woosync").info(
            f"CAZ WooSync: pushed price {price} for item {item_code} "
            f"to WooCommerce product {mapping.woo_id} in store {store_name}"
        )
        frappe.db.set_value("Caz Woo Item Mapping", mapping.name, "last_synced", frappe.utils.now())

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"CAZ WooSync: failed to push price for item {item_code} to store {store_name}",
        )
        raise


# ---------------------------------------------------------------------------
# WooCommerce → ERPNext
# ---------------------------------------------------------------------------


def sync_price_from_woo(store_name, woo_product_id, payload=None):
    """
    Pull WooCommerce product price and update ERPNext Item Price.
    Called when a product.updated webhook arrives.
    payload: parsed JSON dict from webhook (None → fetch from WC API).
    """
    from caz_woosync.utils.rate_limiter import WooCommerceClient

    # 1. If payload None: GET /products/{woo_product_id}
    if payload is None:
        client = WooCommerceClient(store_name)
        resp = client.get(f"products/{woo_product_id}")
        if resp.status_code != 200:
            frappe.throw(
                f"WooCommerce API returned HTTP {resp.status_code} for product "
                f"{woo_product_id}. Check credentials and product existence."
            )
        payload = resp.json()

    # 2. Find erp_item via Caz Woo Item Mapping
    mapping = frappe.db.get_value(
        "Caz Woo Item Mapping",
        {"store": store_name, "woo_id": cstr(woo_product_id)},
        ["name", "erp_item"],
        as_dict=True,
    )

    # 3. If no mapping: skip
    if not mapping or not mapping.erp_item:
        return

    store = frappe.get_doc("Caz Woo Store", store_name)

    # 4. Check store.update_prices_from_woo flag
    if not getattr(store, "update_prices_from_woo", 1):
        frappe.logger("caz_woosync").debug(
            f"CAZ WooSync: update_prices_from_woo disabled for store {store_name}. Skipping."
        )
        return

    item_code = mapping.erp_item

    # 5. Parse prices with None/empty guard
    regular_price = flt(payload.get("regular_price") or 0)
    sale_price = flt(payload.get("sale_price") or 0)

    # 7. If regular_price > 0: upsert Item Price
    if regular_price > 0:
        price_list = getattr(store, "item_price_list", None) or "Standard Selling"
        _upsert_item_price(item_code, price_list, regular_price)

    # 8. If sale_price > 0 and store.sale_price_list: upsert Item Price
    sale_price_list = getattr(store, "sale_price_list", None)
    if sale_price > 0 and sale_price_list:
        valid_upto = payload.get("date_on_sale_to") or None
        _upsert_item_price(item_code, sale_price_list, sale_price, valid_upto=valid_upto)

    frappe.logger("caz_woosync").info(
        f"CAZ WooSync: updated price for item {item_code} from WooCommerce product "
        f"{woo_product_id} in store {store_name} — regular: {regular_price}, sale: {sale_price}"
    )


def _upsert_item_price(item_code, price_list, price, valid_upto=None):
    """Create or update an Item Price record."""
    filters = {"item_code": item_code, "price_list": price_list, "selling": 1}
    existing = frappe.db.get_value("Item Price", filters, "name")

    if existing:
        update_vals = {"price_list_rate": price}
        if valid_upto:
            update_vals["valid_upto"] = valid_upto
        frappe.db.set_value("Item Price", existing, update_vals)
    else:
        ip = frappe.new_doc("Item Price")
        ip.item_code = item_code
        ip.price_list = price_list
        ip.selling = 1
        ip.price_list_rate = price
        if valid_upto:
            ip.valid_upto = valid_upto
        ip.insert(ignore_permissions=True)


# ---------------------------------------------------------------------------
# Bulk push
# ---------------------------------------------------------------------------


def push_all_prices(store_name):
    """Bulk push all mapped items' prices to WooCommerce."""
    mappings = frappe.get_all(
        "Caz Woo Item Mapping",
        filters={"store": store_name},
        fields=["erp_item"],
    )

    success_count = 0
    fail_count = 0

    for mapping in mappings:
        try:
            sync_price_to_woo(store_name, mapping.erp_item)
            success_count += 1
        except Exception:
            fail_count += 1
            frappe.log_error(
                frappe.get_traceback(),
                f"CAZ WooSync: push_all_prices failed for item {mapping.erp_item} in store {store_name}",
            )

    frappe.logger("caz_woosync").info(
        f"CAZ WooSync: push_all_prices for store {store_name} — "
        f"{success_count} succeeded, {fail_count} failed, "
        f"{len(mappings)} total mapped items."
    )


# ---------------------------------------------------------------------------
# ERPNext doc_events hook
# ---------------------------------------------------------------------------


def on_item_price_update(doc, method=None):
    """
    Frappe doc_event: called when Item Price is saved.
    Queues price sync to WooCommerce for the affected item.
    """
    # Guard: skip during system operations to prevent spurious syncs
    if (
        frappe.flags.in_migrate
        or frappe.flags.in_patch
        or frappe.flags.in_import
        or frappe.flags.in_install
    ):
        return

    # Check doc.selling == 1 (only sync selling prices)
    if not getattr(doc, "selling", 0):
        return

    def _enqueue_after_commit():
        stores = frappe.get_all(
            "Caz Woo Store",
            filters={
                "is_active": 1,
                "sync_direction": ["in", ["Both Ways", "ERPNext to WooCommerce"]],
            },
            fields=["name"],
        )
        for store in stores:
            # Only queue if a mapping exists (don't push unmapped items)
            if not frappe.db.exists(
                "Caz Woo Item Mapping",
                {"store": store.name, "erp_item": doc.item_code},
            ):
                continue

            # Dedup: skip if already queued or processing
            if frappe.db.exists(
                "Caz Woo Sync Queue",
                {
                    "store": store.name,
                    "erp_docname": doc.item_code,
                    "entity_type": "Price",
                    "direction": "erp_to_woo",
                    "status": ["in", ["Queued", "Processing"]],
                },
            ):
                continue

            queue_doc = frappe.new_doc("Caz Woo Sync Queue")
            queue_doc.update(
                {
                    "store": store.name,
                    "direction": "erp_to_woo",
                    "entity_type": "Price",
                    "erp_doctype": "Item Price",
                    "erp_docname": doc.item_code,
                    "status": "Queued",
                    "payload": "{}",
                }
            )
            queue_doc.insert(ignore_permissions=True)
        frappe.db.commit()

    frappe.db.after_commit(_enqueue_after_commit)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apply_rounding(price, currency_rounding):
    """Apply rounding to a price value based on the store's currency_rounding setting."""
    if currency_rounding == "Nearest Integer":
        return float(round(price))
    elif currency_rounding == "No Rounding":
        return price
    else:
        # Default: "2 Decimal Places"
        return round(price, 2)
