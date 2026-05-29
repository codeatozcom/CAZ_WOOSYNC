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
        _sync_variable_product(payload, store)
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


def _sync_variable_product(payload, store):
    """
    Sync a WooCommerce variable product to an ERPNext Item Template + Variants.
    Variable products in WC have 'attributes' and 'variations' (list of IDs).
    The template Item is created with has_variants=1; each WC variation becomes
    an ERPNext Item Variant linked via variant_of.
    """
    from caz_woosync.utils.rate_limiter import WooCommerceClient

    woo_id = str(payload.get("id", ""))
    sku = (payload.get("sku") or "").strip()
    item_name = (payload.get("name") or "")[:140]

    # -- Find or create Item Template --
    existing_code = frappe.db.get_value(
        "Caz Woo Item Mapping",
        {"store": store.name, "woo_id": woo_id, "product_type": "variable"},
        "erp_item",
    )

    if existing_code:
        item = frappe.get_doc("Item", existing_code)
        _update_item_fields(item, payload, store)
        item_code = existing_code
    else:
        if not getattr(store, "create_items_from_woo", None):
            return

        item_code = sku if sku else f"WOO-{woo_id}"
        item = frappe.new_doc("Item")
        item.item_code = item_code
        item.item_name = item_name or item_code
        item.has_variants = 1
        item.variant_based_on = "Item Attribute"
        _update_item_fields(item, payload, store)

    # -- Ensure attributes exist and are attached to template --
    wc_attributes = payload.get("attributes") or []
    item.attributes = []
    for attr in wc_attributes:
        attr_name = (attr.get("name") or "").strip().title()
        if not attr_name:
            continue
        _ensure_item_attribute(attr_name)
        for value in attr.get("options") or []:
            _ensure_attribute_value(attr_name, str(value).strip())
        item.append("attributes", {"attribute": attr_name})

    if existing_code:
        item.save(ignore_permissions=True)
    else:
        item.insert(ignore_permissions=True)

    # -- Sync each variation --
    client = WooCommerceClient(store.name)
    page = 1
    while True:
        resp = client.get(
            f"products/{woo_id}/variations",
            params={"per_page": 100, "page": page},
        )
        if resp.status_code != 200:
            frappe.log_error(
                f"WooCommerce API returned HTTP {resp.status_code} fetching variations "
                f"for product {woo_id} (page {page}).",
                "CAZ WooSync: Variation Fetch Error",
            )
            break
        variations = resp.json()
        if not variations:
            break
        for variation in variations:
            _sync_variation(variation, item.name, woo_id, store)
        if len(variations) < 100:
            break
        page += 1

    # -- Save template mapping --
    _upsert_item_mapping(store.name, woo_id, item.name, product_type="variable")


def _ensure_item_attribute(attr_name):
    """
    Create an ERPNext Item Attribute if it does not already exist.
    Returns the attribute name so callers can chain calls.
    """
    if not frappe.db.exists("Item Attribute", attr_name):
        attr_doc = frappe.new_doc("Item Attribute")
        attr_doc.attribute_name = attr_name
        attr_doc.insert(ignore_permissions=True)
    return attr_name


def _ensure_attribute_value(attr_name, value):
    """
    Add a value to an Item Attribute's child table if it is not already present.
    Fetches the live doc, appends if missing, then saves.
    """
    if not value:
        return
    attr_doc = frappe.get_doc("Item Attribute", attr_name)
    existing_values = {
        (row.attribute_value or "").strip().lower()
        for row in (attr_doc.item_attribute_values or [])
    }
    if value.strip().lower() not in existing_values:
        attr_doc.append(
            "item_attribute_values",
            {"attribute_value": value, "abbr": value[:3].upper()},
        )
        attr_doc.save(ignore_permissions=True)


def _sync_variation(variation_payload, template_item_code, woo_parent_id, store):
    """
    Sync a single WooCommerce variation to an ERPNext Item Variant.
    Creates or updates an Item with variant_of=template_item_code and
    attributes matching the variation's attribute options.
    """
    variation_id = str(variation_payload.get("id", ""))
    sku = (variation_payload.get("sku") or "").strip()

    # -- Find existing variant --
    existing_variant = frappe.db.get_value(
        "Caz Woo Item Mapping",
        {"store": store.name, "woo_id": variation_id},
        "erp_item",
    )
    if not existing_variant and sku:
        existing_variant = frappe.db.get_value(
            "Item",
            {"variant_of": template_item_code, "item_code": sku},
            "name",
        )

    # -- Build attribute dict from WC variation --
    attributes = {
        attr["name"]: attr["option"]
        for attr in (variation_payload.get("attributes") or [])
        if attr.get("name") and attr.get("option")
    }

    if existing_variant:
        variant = frappe.get_doc("Item", existing_variant)
        # Update fields from variation
        disabled = variation_payload.get("status", "publish") != "publish"
        variant.disabled = 1 if disabled else 0
        weight = variation_payload.get("weight")
        if weight:
            try:
                variant.weight_per_unit = flt(weight)
                variant.weight_uom = "Kg"
            except (ValueError, TypeError):
                pass
        variant.save(ignore_permissions=True)
        variant_item_code = existing_variant
    else:
        # Determine item_code
        if sku:
            variant_item_code = sku
        else:
            raw = f"WOO-{woo_parent_id}-{variation_id}"
            variant_item_code = raw[:140]

        template_doc = frappe.get_doc("Item", template_item_code)
        variant = frappe.copy_doc(template_doc)
        variant.item_code = variant_item_code
        variant.item_name = variant_item_code
        variant.variant_of = template_item_code
        variant.has_variants = 0
        variant.attributes = [
            {"attribute": k, "attribute_value": v} for k, v in attributes.items()
        ]
        # Apply variation-specific fields
        disabled = variation_payload.get("status", "publish") != "publish"
        variant.disabled = 1 if disabled else 0
        weight = variation_payload.get("weight")
        if weight:
            try:
                variant.weight_per_unit = flt(weight)
                variant.weight_uom = "Kg"
            except (ValueError, TypeError):
                pass
        variant.insert(ignore_permissions=True)

    # -- Item Price for variation --
    _upsert_item_price(variant_item_code, variation_payload, store)

    # -- Mapping for variation --
    _upsert_variation_mapping(
        store_name=store.name,
        woo_id=variation_id,
        erp_item=variant_item_code,
        woo_variant_id=variation_id,
    )


def _upsert_variation_mapping(store_name, woo_id, erp_item, woo_variant_id):
    """
    Create or update a Caz Woo Item Mapping record for a WooCommerce variation.
    Uses woo_id=variation_id and product_type='variation'.
    """
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
                "product_type": "variation",
                "woo_variant_id": cstr(woo_variant_id),
                "last_synced": frappe.utils.now(),
            },
        )
    else:
        doc = frappe.new_doc("Caz Woo Item Mapping")
        doc.store = store_name
        doc.woo_id = cstr(woo_id)
        doc.erp_item = erp_item
        doc.product_type = "variation"
        doc.woo_variant_id = cstr(woo_variant_id)
        doc.last_synced = frappe.utils.now()
        doc.insert(ignore_permissions=True)


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
    """
    Push an ERPNext Item to WooCommerce as a product.
    If the item has has_variants=1 it is pushed as a variable product template
    and each variant is pushed as a WooCommerce variation.
    """
    from caz_woosync.utils.rate_limiter import WooCommerceClient

    store = frappe.get_doc("Caz Woo Store", store_name)
    item = frappe.get_doc("Item", item_code)
    client = WooCommerceClient(store_name)

    if getattr(item, "has_variants", 0):
        _sync_template_to_woo(store_name, item, store, client)
        return

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


def _sync_template_to_woo(store_name, item, store, client):
    """
    Push an ERPNext Item Template (has_variants=1) to WooCommerce as a variable product,
    then push each Item Variant as a WooCommerce variation.
    """
    # Build payload as a variable product
    woo_payload = _build_woo_payload(item, store, client)
    woo_payload["type"] = "variable"

    # Build attributes list from ERPNext Item Attribute rows
    wc_attributes = []
    for row in (item.attributes or []):
        attr_name = row.attribute
        # Fetch attribute values from Item Attribute doctype
        attr_values = frappe.db.get_all(
            "Item Attribute Value",
            filters={"parent": attr_name},
            fields=["attribute_value"],
            order_by="idx asc",
        )
        wc_attributes.append(
            {
                "name": attr_name,
                "variation": True,
                "options": [v.attribute_value for v in attr_values],
            }
        )
    if wc_attributes:
        woo_payload["attributes"] = wc_attributes

    mapping = frappe.db.get_value(
        "Caz Woo Item Mapping",
        {"store": store_name, "erp_item": item.name},
        ["name", "woo_id"],
        as_dict=True,
    )

    if mapping and mapping.woo_id:
        resp = client.put(f"products/{mapping.woo_id}", woo_payload)
        if resp.status_code not in (200, 201):
            frappe.throw(
                f"WooCommerce rejected template update (HTTP {resp.status_code}): {resp.text[:300]}"
            )
        woo_id = mapping.woo_id
    else:
        resp = client.post("products", woo_payload)
        if resp.status_code not in (200, 201):
            frappe.throw(
                f"WooCommerce rejected template creation (HTTP {resp.status_code}): {resp.text[:300]}"
            )
        woo_id = str(resp.json().get("id", ""))

    _upsert_item_mapping(store_name, woo_id, item.name, product_type="variable")

    # Push each variant
    variants = frappe.get_all(
        "Item",
        filters={"variant_of": item.name},
        fields=["name", "item_code"],
    )
    for v in variants:
        _sync_variant_to_woo(store_name, v.name, woo_id, store, client)

    frappe.db.commit()


def _sync_variant_to_woo(store_name, variant_item_code, woo_parent_id, store, client):
    """
    Push a single ERPNext Item Variant to WooCommerce as a variation under woo_parent_id.
    """
    variant = frappe.get_doc("Item", variant_item_code)

    # Build variation payload
    var_payload = {
        "status": "publish" if not variant.disabled else "private",
    }
    if getattr(variant, "weight_per_unit", None):
        var_payload["weight"] = cstr(variant.weight_per_unit)

    # Pricing
    price = frappe.db.get_value(
        "Item Price",
        {
            "item_code": variant.name,
            "price_list": getattr(store, "item_price_list", None) or "Standard Selling",
            "selling": 1,
        },
        "price_list_rate",
    )
    if price:
        var_payload["regular_price"] = cstr(price)

    # Attributes
    var_payload["attributes"] = [
        {"name": row.attribute, "option": row.attribute_value}
        for row in (variant.attributes or [])
    ]

    # Check existing variation mapping
    var_mapping = frappe.db.get_value(
        "Caz Woo Item Mapping",
        {"store": store_name, "erp_item": variant_item_code, "product_type": "variation"},
        ["name", "woo_id"],
        as_dict=True,
    )

    if var_mapping and var_mapping.woo_id:
        resp = client.put(
            f"products/{woo_parent_id}/variations/{var_mapping.woo_id}", var_payload
        )
        if resp.status_code not in (200, 201):
            frappe.log_error(
                f"WooCommerce rejected variation update for {variant_item_code} "
                f"(HTTP {resp.status_code}): {resp.text[:300]}",
                "CAZ WooSync: Variation Push Error",
            )
            return
        woo_var_id = var_mapping.woo_id
    else:
        resp = client.post(f"products/{woo_parent_id}/variations", var_payload)
        if resp.status_code not in (200, 201):
            frappe.log_error(
                f"WooCommerce rejected variation creation for {variant_item_code} "
                f"(HTTP {resp.status_code}): {resp.text[:300]}",
                "CAZ WooSync: Variation Push Error",
            )
            return
        woo_var_id = str(resp.json().get("id", ""))

    _upsert_variation_mapping(
        store_name=store_name,
        woo_id=woo_var_id,
        erp_item=variant_item_code,
        woo_variant_id=woo_var_id,
    )


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
