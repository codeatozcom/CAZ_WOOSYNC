import json

import frappe
from frappe.utils import cstr, flt, strip_html


# ---------------------------------------------------------------------------
# WooCommerce → ERPNext
# ---------------------------------------------------------------------------


def sync_product_to_erp(store_name, woo_product_id, payload=None):
    """
    Create or update an ERPNext Item from a WooCommerce product.
    payload: parsed JSON dict from webhook (None → fetch from WC API).
    """
    from caz_woosync.utils.rate_limiter import WooCommerceClient

    store = frappe.get_doc("Caz Woo Store", store_name)

    if payload is None:
        client = WooCommerceClient(store_name)
        resp = client.get(f"products/{woo_product_id}")
        if resp.status_code != 200:
            frappe.throw(
                f"WooCommerce API returned HTTP {resp.status_code} for product {woo_product_id}. "
                "Check your API credentials and that the product exists."
            )
        payload = resp.json()

    product_type = payload.get("type", "simple")

    if product_type == "variable":
        # Variable product support is deferred to Phase 9 (Advanced Products)
        frappe.log_error(
            f"Variable product {woo_product_id} received in store {store_name}. "
            "Variable product sync is planned for Phase 9. Skipping.",
            "CAZ WooSync: Variable Product Skipped",
        )
        return

    _sync_simple_product(payload, store)


def _sync_simple_product(payload, store):
    """Create or update a simple ERPNext Item from a WooCommerce product payload."""
    woo_id = str(payload.get("id", ""))
    sku = (payload.get("sku") or "").strip()
    item_name = (payload.get("name") or "")[:140]  # ERPNext field limit

    # -- Match existing item --
    item_code = _find_existing_item(store, woo_id, sku)

    if item_code:
        item = frappe.get_doc("Item", item_code)
        _update_item_fields(item, payload, store)
        item.save(ignore_permissions=True)
    else:
        if not store.create_items_from_woo:
            return  # Store configured to not create items automatically

        item = frappe.new_doc("Item")
        item_code = sku if sku else f"WOO-{woo_id}"
        item.item_code = item_code
        item.item_name = item_name or item_code
        _update_item_fields(item, payload, store)
        item.insert(ignore_permissions=True)

    # -- Item Price --
    _upsert_item_price(item.name, payload, store)

    # -- Mapping --
    _upsert_item_mapping(store.name, woo_id, item.name, product_type="simple")

    frappe.db.commit()


def _update_item_fields(item, payload, store):
    """Apply WooCommerce payload fields to an ERPNext Item doc."""
    raw_description = payload.get("description") or payload.get("short_description") or ""
    item.description = strip_html(raw_description or "") or item.item_name

    item.item_group = store.item_group or "All Item Groups"
    item.stock_uom = store.default_uom or "Nos"
    item.is_stock_item = 1 if payload.get("type") not in ("virtual", "downloadable") else 0
    item.disabled = 0 if payload.get("status") == "publish" else 1

    weight = payload.get("weight")
    if weight:
        try:
            item.weight_per_unit = flt(weight)
            item.weight_uom = "Kg"
        except (ValueError, TypeError):
            pass


def _upsert_item_price(item_code, payload, store):
    """Create or update an Item Price record for the WooCommerce regular_price."""
    price_str = payload.get("regular_price") or payload.get("price") or "0"
    try:
        price = flt(price_str)
    except (ValueError, TypeError):
        price = 0.0

    if price <= 0:
        return

    price_list = getattr(store, "item_price_list", None) or "Standard Selling"

    existing = frappe.db.get_value(
        "Item Price",
        {"item_code": item_code, "price_list": price_list, "selling": 1},
        "name",
    )
    if existing:
        frappe.db.set_value("Item Price", existing, "price_list_rate", price)
    else:
        ip = frappe.new_doc("Item Price")
        ip.item_code = item_code
        ip.price_list = price_list
        ip.selling = 1
        ip.price_list_rate = price
        ip.insert(ignore_permissions=True)


def _find_existing_item(store, woo_id, sku):
    """Return existing ERPNext item_code matching this WooCommerce product, or None."""
    # 1. Check mapping table first
    mapped = frappe.db.get_value(
        "Caz Woo Item Mapping",
        {"store": store.name, "woo_id": woo_id},
        "erp_item",
    )
    if mapped:
        return mapped

    # 2. Match by SKU if store is configured to use SKU matching
    match_field = getattr(store, "item_match_field", "SKU") or "SKU"
    if match_field == "SKU" and sku:
        item_code = frappe.db.get_value("Item", {"item_code": sku}, "name")
        if item_code:
            return item_code

    return None


# ---------------------------------------------------------------------------
# ERPNext → WooCommerce
# ---------------------------------------------------------------------------


def sync_item_to_woo(store_name, item_code):
    """Push an ERPNext Item to WooCommerce as a product."""
    from caz_woosync.utils.rate_limiter import WooCommerceClient

    store = frappe.get_doc("Caz Woo Store", store_name)
    item = frappe.get_doc("Item", item_code)
    client = WooCommerceClient(store_name)

    woo_payload = _build_woo_payload(item, store, client)

    mapping = frappe.db.get_value(
        "Caz Woo Item Mapping",
        {"store": store_name, "erp_item": item_code},
        ["name", "woo_id"],
        as_dict=True,
    )

    if mapping and mapping.woo_id:
        resp = client.put(f"products/{mapping.woo_id}", woo_payload)
        if resp.status_code not in (200, 201):
            frappe.throw(
                f"WooCommerce rejected product update (HTTP {resp.status_code}): {resp.text[:300]}"
            )
        woo_id = mapping.woo_id
    else:
        resp = client.post("products", woo_payload)
        if resp.status_code not in (200, 201):
            frappe.throw(
                f"WooCommerce rejected product creation (HTTP {resp.status_code}): {resp.text[:300]}"
            )
        woo_id = str(resp.json().get("id", ""))

    _upsert_item_mapping(store_name, woo_id, item_code, product_type="simple")
    frappe.db.commit()


def _build_woo_payload(item, store, client):
    """Build a WooCommerce product payload from an ERPNext Item."""
    # Resolve category
    category_id = None
    if item.item_group and item.item_group not in ("All Item Groups", "Products"):
        category_id = _get_or_create_wc_category(item.item_group, client)

    payload = {
        "name": item.item_name,
        "status": "draft" if item.disabled else "publish",
        "description": item.description or "",
        "type": "simple",
        "manage_stock": True if item.is_stock_item else False,
    }
    if category_id:
        payload["categories"] = [{"id": category_id}]

    # Pricing
    price = frappe.db.get_value(
        "Item Price",
        {
            "item_code": item.name,
            "price_list": getattr(store, "item_price_list", None) or "Standard Selling",
            "selling": 1,
        },
        "price_list_rate",
    )
    if price:
        payload["regular_price"] = cstr(price)

    return payload


def _get_or_create_wc_category(group_name, client):
    """Find or create a WooCommerce product category matching item_group."""
    resp = client.get("products/categories", params={"search": group_name, "per_page": 5})
    if resp.status_code == 200:
        for cat in resp.json():
            if cat.get("name", "").lower() == group_name.lower():
                return cat["id"]

    # Create new category
    try:
        create_resp = client.post("products/categories", {"name": group_name})
        if create_resp.status_code not in (200, 201):
            frappe.throw(
                f"Failed to create WooCommerce category '{group_name}': "
                f"HTTP {create_resp.status_code} — {create_resp.text[:200]}"
            )
        return create_resp.json().get("id")
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"CAZ WooSync: Failed to create WooCommerce category '{group_name}'",
        )
        raise


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------


def _upsert_item_mapping(store_name, woo_id, erp_item, product_type="simple"):
    """Create or update a Caz Woo Item Mapping record."""
    existing = frappe.db.get_value(
        "Caz Woo Item Mapping",
        {"store": store_name, "woo_id": cstr(woo_id)},
        "name",
    )
    if existing:
        frappe.db.set_value(
            "Caz Woo Item Mapping",
            existing,
            {
                "erp_item": erp_item,
                "product_type": product_type,
                "last_synced": frappe.utils.now(),
            },
        )
    else:
        doc = frappe.new_doc("Caz Woo Item Mapping")
        doc.store = store_name
        doc.woo_id = cstr(woo_id)
        doc.erp_item = erp_item
        doc.product_type = product_type
        doc.last_synced = frappe.utils.now()
        doc.insert(ignore_permissions=True)


# ---------------------------------------------------------------------------
# ERPNext doc_events hook
# ---------------------------------------------------------------------------


def on_item_update(doc, method=None):
    """
    Triggered by Frappe doc_events when an ERPNext Item is saved.
    Queues the item for sync to WooCommerce if a mapping exists.
    """
    # Guard: skip during system operations to prevent spurious syncs
    if (
        frappe.flags.in_migrate
        or frappe.flags.in_patch
        or frappe.flags.in_import
        or frappe.flags.in_install
    ):
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
                {"store": store.name, "erp_item": doc.name},
            ):
                continue

            # Dedup: skip if already queued or processing
            if frappe.db.exists(
                "Caz Woo Sync Queue",
                {
                    "store": store.name,
                    "erp_docname": doc.name,
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
                    "entity_type": "Product",
                    "erp_doctype": "Item",
                    "erp_docname": doc.name,
                    "status": "Queued",
                    "payload": "{}",
                }
            )
            queue_doc.insert(ignore_permissions=True)
        frappe.db.commit()

    frappe.db.after_commit(_enqueue_after_commit)
