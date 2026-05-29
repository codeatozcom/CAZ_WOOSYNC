"""
WooCommerce → ERPNext Coupon/Discount Sync (Phase 12).

Syncs WooCommerce coupons to ERPNext Pricing Rules.
"""
import frappe


def sync_coupon_to_erp(store_name, woo_coupon_id, payload=None):
    """
    Sync a WooCommerce coupon to an ERPNext Pricing Rule.
    payload: webhook JSON (None → fetch from WC API GET /coupons/{id})
    """
    woo_coupon_id = str(woo_coupon_id)
    store = frappe.get_doc("Caz Woo Store", store_name)

    # 1. If payload None: fetch from WC API
    if payload is None:
        from caz_woosync.utils.rate_limiter import get_woo_client
        client = get_woo_client(store_name)
        response = client.get(f"coupons/{woo_coupon_id}")
        payload = response.json()

    # 2. Check if Pricing Rule already exists (idempotent)
    existing = frappe.db.get_value(
        "Pricing Rule",
        {"title": ["like", f"%#{woo_coupon_id}%"]},
        "name",
    )
    if existing:
        frappe.logger().info(
            f"CAZ WooSync: Pricing Rule already exists for WC coupon #{woo_coupon_id}. Skipping."
        )
        return existing

    # 3. Map coupon fields to Pricing Rule
    coupon_code = payload.get("code", "")
    title = f"WC Coupon: {coupon_code} (#{woo_coupon_id})"[:140]

    discount_type = payload.get("discount_type", "")
    amount = payload.get("amount", "0")
    try:
        amount_float = float(amount)
    except (TypeError, ValueError):
        amount_float = 0.0

    if discount_type == "percent":
        rate_or_discount = "Discount Percentage"
        discount_percentage = amount_float
        discount_amount = 0.0
    else:
        # fixed_cart or fixed_product
        rate_or_discount = "Discount Amount"
        discount_percentage = 0.0
        discount_amount = amount_float

    # apply_on: Grand Total for cart coupons, Item Code for product coupons
    if discount_type == "fixed_product":
        apply_on = "Item Code"
    else:
        apply_on = "Grand Total"

    # Dates
    valid_from = (payload.get("date_created") or "")[:10] or None
    date_expires = payload.get("date_expires") or ""
    valid_upto = date_expires[:10] if date_expires else ""

    min_amount = payload.get("minimum_amount") or 0
    try:
        min_qty = float(min_amount)
    except (TypeError, ValueError):
        min_qty = 0.0

    coupon_code_upper = coupon_code.upper()

    # 4. Insert Pricing Rule
    pr = frappe.new_doc("Pricing Rule")
    pr.update({
        "title": title,
        "rate_or_discount": rate_or_discount,
        "discount_percentage": discount_percentage,
        "discount_amount": discount_amount,
        "apply_on": apply_on,
        "selling": 1,
        "company": store.company,
        "coupon_code": coupon_code_upper,
        "min_qty": min_qty,
    })

    if valid_from:
        pr.valid_from = valid_from

    if valid_upto:
        pr.valid_upto = valid_upto

    pr.insert(ignore_permissions=True)

    frappe.logger().info(
        f"CAZ WooSync: Created Pricing Rule {pr.name} for WC coupon #{woo_coupon_id}."
    )
    # 5. Return pricing_rule_name
    return pr.name


def sync_all_coupons(store_name):
    """Bulk sync all WC coupons to ERPNext Pricing Rules."""
    from caz_woosync.utils.rate_limiter import get_woo_client

    client = get_woo_client(store_name)
    page = 1
    per_page = 100

    while True:
        response = client.get("coupons", params={"per_page": per_page, "page": page})
        coupons = response.json()
        if not coupons:
            break

        for coupon in coupons:
            woo_coupon_id = coupon.get("id")
            if woo_coupon_id:
                try:
                    sync_coupon_to_erp(store_name, str(woo_coupon_id), payload=coupon)
                except Exception:
                    frappe.log_error(
                        frappe.get_traceback(),
                        f"CAZ WooSync: Failed to sync coupon #{woo_coupon_id}",
                    )

        if len(coupons) < per_page:
            break
        page += 1

    frappe.logger().info(f"CAZ WooSync: Bulk coupon sync complete for store {store_name}.")
