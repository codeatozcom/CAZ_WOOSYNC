"""
WooCommerce → ERPNext Order Sync (Phase 4).

Handles creating and updating ERPNext Sales Orders from WooCommerce orders.
"""
import frappe
from frappe.utils import now_datetime, getdate
from frappe.utils.html_utils import strip_html


# WooCommerce status → ERPNext delivery_status mapping
WC_STATUS_TO_DELIVERY_STATUS = {
    "pending": "To Deliver and Bill",
    "processing": "To Deliver and Bill",
    "on-hold": "To Deliver and Bill",
    "completed": "Completed",
    "cancelled": "Cancelled",
    "refunded": "Cancelled",
    "failed": "Cancelled",
}

# Statuses that should never trigger SO creation (no point in creating then immediately cancelling)
SKIP_CREATE_STATUSES = {"cancelled", "refunded", "failed"}


def sync_order_to_erp(store_name, woo_order_id, payload=None):
    """
    Create or update an ERPNext Sales Order from a WooCommerce order.
    payload: parsed JSON dict from webhook (None → fetch from WC API).
    """
    store = frappe.get_doc("Caz Woo Store", store_name)

    # 1. Fetch payload from API if not provided
    if payload is None:
        from caz_woosync.utils.rate_limiter import WooCommerceClient
        client = WooCommerceClient(store_name)
        resp = client.get(f"orders/{woo_order_id}")
        if resp.status_code != 200:
            frappe.throw(
                f"WooCommerce API returned HTTP {resp.status_code} for order {woo_order_id}."
            )
        payload = resp.json()

    woo_order_id = str(woo_order_id)

    # 2. Check for existing mapping
    existing_mapping = frappe.db.get_value(
        "Caz Woo Order Mapping",
        {"store": store_name, "woo_order_id": woo_order_id},
        ["name", "sales_order"],
        as_dict=True,
    )

    wc_status = (payload.get("status") or "").lower()

    if existing_mapping and existing_mapping.get("sales_order"):
        # 3. Mapping exists — update status only
        so_name = existing_mapping["sales_order"]
        _update_order_status(so_name, payload, store)
        _upsert_order_mapping(
            store_name=store_name,
            woo_order_id=woo_order_id,
            so_name=so_name,
            wc_status=wc_status,
            mapping_name=existing_mapping["name"],
        )
    else:
        # 4. Skip cancelled/refunded orders with no existing SO
        if wc_status in SKIP_CREATE_STATUSES:
            frappe.logger().info(
                f"CAZ WooSync: Skipping order {woo_order_id} — status is '{wc_status}', no SO to create."
            )
            return

        # 5. Create new Sales Order
        so_doc = _create_sales_order(payload, store)
        so_name = so_doc.name if so_doc else None

        # 6. Upsert mapping
        _upsert_order_mapping(
            store_name=store_name,
            woo_order_id=woo_order_id,
            so_name=so_name,
            wc_status=wc_status,
            mapping_name=existing_mapping["name"] if existing_mapping else None,
        )

    # 7. Commit
    frappe.db.commit()


def _create_sales_order(payload, store):
    """Create ERPNext Sales Order from WooCommerce order payload."""
    # --- Idempotency check via meta_data ---
    meta_data = payload.get("meta_data") or []
    for meta in meta_data:
        if meta.get("key") == "_caz_woo_so":
            existing_so = meta.get("value")
            if existing_so and frappe.db.exists("Sales Order", existing_so):
                frappe.logger().info(
                    f"CAZ WooSync: Order already has SO {existing_so} via meta_data. Skipping creation."
                )
                return frappe.get_doc("Sales Order", existing_so)

    # --- Customer ---
    customer_name = _get_or_create_customer(payload, store)

    # --- Transaction date ---
    date_created = payload.get("date_created") or ""
    try:
        transaction_date = str(getdate(date_created.split("T")[0])) if "T" in date_created else str(getdate(date_created))
    except Exception:
        transaction_date = str(getdate(frappe.utils.nowdate()))

    # --- Currency ---
    currency = strip_html(payload.get("currency") or "") or "USD"

    # --- Delivery status ---
    wc_status = (payload.get("status") or "").lower()
    delivery_status = WC_STATUS_TO_DELIVERY_STATUS.get(wc_status, "To Deliver and Bill")

    # --- Line items ---
    items = []
    for li in (payload.get("line_items") or []):
        product_id = li.get("product_id") or li.get("variation_id")
        qty = float(li.get("quantity") or 1)
        subtotal = float(li.get("subtotal") or 0)
        unit_price = (subtotal / qty) if qty else 0.0
        item_name_raw = strip_html(li.get("name") or "")

        # Find item via mapping, fallback to placeholder
        item_code = _resolve_item_code(product_id, li, store)

        item_row = {
            "item_code": item_code,
            "item_name": item_name_raw[:140] if item_name_raw else item_code,
            "qty": qty,
            "rate": unit_price,
            "warehouse": store.warehouse,
        }
        if getattr(store, "income_account", None):
            item_row["income_account"] = store.income_account

        items.append(item_row)

    # --- Shipping as a line item ---
    shipping_total = float(payload.get("shipping_total") or 0)
    if shipping_total > 0:
        shipping_item_code = getattr(store, "shipping_item_code", None) or "Shipping"
        _get_or_create_shipping_item(shipping_item_code)
        shipping_row = {
            "item_code": shipping_item_code,
            "item_name": "Shipping",
            "qty": 1,
            "rate": shipping_total,
            "warehouse": store.warehouse,
        }
        if getattr(store, "income_account", None):
            shipping_row["income_account"] = store.income_account
        items.append(shipping_row)

    if not items:
        frappe.logger().warning(
            f"CAZ WooSync: WooCommerce order {payload.get('id')} has no line items. Skipping."
        )
        return None

    # --- Taxes ---
    taxes = []
    total_tax = float(payload.get("total_tax") or 0)
    if total_tax > 0 and getattr(store, "tax_account", None):
        taxes.append({
            "charge_type": "Actual",
            "account_head": store.tax_account,
            "description": "WooCommerce Tax",
            "tax_amount": total_tax,
        })

    # --- SO document ---
    so = frappe.new_doc("Sales Order")
    so.update({
        "naming_series": getattr(store, "so_naming_series", None) or "SO-CAZWOO-.YYYY.-",
        "company": store.company,
        "customer": customer_name,
        "transaction_date": transaction_date,
        "delivery_date": transaction_date,
        "currency": currency,
        "ignore_pricing_rule": 1,
        "items": items,
    })

    if taxes:
        so.set("taxes", taxes)

    so.insert(ignore_permissions=True)

    # Auto-submit if store setting is enabled
    if getattr(store, "so_auto_submit", 0):
        so.submit()

    # --- Billing address ---
    billing = payload.get("billing") or {}
    _sync_billing_address(billing, customer_name, store)

    return so


def _resolve_item_code(product_id, line_item, store):
    """Resolve WC product_id to ERPNext item_code via mapping or placeholder."""
    if product_id:
        # Try Caz Woo Item Mapping
        mapped_item = frappe.db.get_value(
            "Caz Woo Item Mapping",
            {"store": store.name, "woo_id": str(product_id)},
            "erp_item",
        )
        if mapped_item:
            return mapped_item

        # Fallback: create placeholder
        return _get_or_create_placeholder_item(product_id, line_item)

    # No product_id: use item_name slugified
    item_name = strip_html(line_item.get("name") or "Unknown Item")
    return item_name[:140]


def _get_or_create_placeholder_item(woo_product_id, line_item):
    """Create a minimal ERPNext Item for a WC product not yet in the mapping."""
    item_code = f"WOO-{woo_product_id}"
    if not frappe.db.exists("Item", item_code):
        item_name = strip_html(line_item.get("name") or item_code)[:140]
        new_item = frappe.new_doc("Item")
        new_item.update({
            "item_code": item_code,
            "item_name": item_name or item_code,
            "is_stock_item": 0,
            "item_group": "All Item Groups",
            "stock_uom": "Nos",
        })
        new_item.insert(ignore_permissions=True)
    return item_code


def _get_or_create_shipping_item(item_code):
    """Ensure the shipping line item exists in ERPNext."""
    if not frappe.db.exists("Item", item_code):
        shipping_item = frappe.new_doc("Item")
        shipping_item.update({
            "item_code": item_code,
            "item_name": item_code,
            "is_stock_item": 0,
            "item_group": "All Item Groups",
            "stock_uom": "Nos",
            "description": "WooCommerce shipping charge",
        })
        shipping_item.insert(ignore_permissions=True)


def _sync_billing_address(billing, customer_name, store):
    """Create or update ERPNext Address from WooCommerce billing info."""
    first = strip_html(billing.get("first_name") or "")
    last = strip_html(billing.get("last_name") or "")
    address1 = strip_html(billing.get("address_1") or "")

    if not (first or last) or not address1:
        return  # Not enough data

    address_title = f"{first} {last}".strip() or customer_name
    city = strip_html(billing.get("city") or "")
    state = strip_html(billing.get("state") or "")
    country = strip_html(billing.get("country") or "")
    pincode = strip_html(billing.get("postcode") or "")
    address2 = strip_html(billing.get("address_2") or "")
    phone = strip_html(billing.get("phone") or "")
    email = strip_html(billing.get("email") or "")

    # Check for existing address linked to customer
    existing = frappe.db.get_value(
        "Address",
        {"address_title": address_title, "address_type": "Billing"},
        "name",
    )

    if existing:
        addr = frappe.get_doc("Address", existing)
    else:
        addr = frappe.new_doc("Address")
        addr.address_title = address_title
        addr.address_type = "Billing"

    addr.update({
        "address_line1": address1,
        "address_line2": address2,
        "city": city or "Unknown",
        "state": state,
        "country": country,
        "pincode": pincode,
        "phone": phone,
        "email_id": email,
    })

    # Link to customer
    addr.links = [{"link_doctype": "Customer", "link_name": customer_name}]

    if existing:
        addr.save(ignore_permissions=True)
    else:
        addr.insert(ignore_permissions=True)


def _update_order_status(so_name, payload, store):
    """Update SO status fields when WooCommerce order status changes."""
    wc_status = (payload.get("status") or "").lower()

    if wc_status == "cancelled":
        try:
            so = frappe.get_doc("Sales Order", so_name)
            if so.docstatus == 1:
                so.cancel()
            else:
                frappe.logger().info(
                    f"CAZ WooSync: SO {so_name} is not submitted (docstatus={so.docstatus}), cannot cancel."
                )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"CAZ WooSync: Failed to cancel SO {so_name}",
            )

    elif wc_status == "completed":
        # Fulfillment handled separately; no action here
        frappe.logger().info(
            f"CAZ WooSync: Order {payload.get('id')} completed — fulfillment handled in a later phase."
        )

    elif wc_status == "refunded":
        # Refund handling is Phase 7 — log only
        frappe.logger().info(
            f"CAZ WooSync: Order {payload.get('id')} refunded — refund handling deferred to Phase 7."
        )

    else:
        # Other statuses: just log and update mapping
        frappe.logger().info(
            f"CAZ WooSync: WooCommerce order {payload.get('id')} status changed to '{wc_status}'."
        )


def _get_or_create_customer(payload, store):
    """Find or create ERPNext Customer from WooCommerce order billing info."""
    billing = payload.get("billing") or {}
    email = strip_html(billing.get("email") or "")
    first = strip_html(billing.get("first_name") or "")
    last = strip_html(billing.get("last_name") or "")
    phone = strip_html(billing.get("phone") or "")

    # --- Try to find by email ---
    if email:
        # Search via customer_primary_email field
        customer_by_email = frappe.db.get_value(
            "Customer",
            {"customer_primary_email": email},
            "name",
        )
        if customer_by_email:
            return customer_by_email

        # Search via linked Contact email
        contact_email_match = frappe.db.sql(
            """
            SELECT dl.link_name
            FROM `tabContact Email` ce
            JOIN `tabContact` c ON c.name = ce.parent
            JOIN `tabDynamic Link` dl ON dl.parent = c.name
                AND dl.link_doctype = 'Customer'
            WHERE ce.email_id = %s
            LIMIT 1
            """,
            (email,),
            as_dict=True,
        )
        if contact_email_match:
            return contact_email_match[0]["link_name"]

    # --- Create new Customer ---
    full_name = f"{first} {last}".strip() or email or "WooCommerce Customer"
    customer_group = getattr(store, "customer_group", None) or "All Customer Groups"

    customer = frappe.new_doc("Customer")
    customer.update({
        "customer_name": full_name,
        "customer_type": "Individual",
        "customer_group": customer_group,
        "territory": "All Territories",
    })
    customer.insert(ignore_permissions=True)

    # --- Create Contact ---
    if email or phone:
        contact = frappe.new_doc("Contact")
        contact.update({
            "first_name": first or full_name,
            "last_name": last,
        })
        if email:
            contact.append("email_ids", {
                "email_id": email,
                "is_primary": 1,
            })
        if phone:
            contact.append("phone_nos", {
                "phone": phone,
                "is_primary_phone": 1,
            })
        contact.append("links", {
            "link_doctype": "Customer",
            "link_name": customer.name,
        })
        try:
            contact.insert(ignore_permissions=True)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"CAZ WooSync: Failed to create Contact for customer {customer.name}",
            )

    return customer.name


def _upsert_order_mapping(store_name, woo_order_id, so_name, wc_status, mapping_name=None):
    """Create or update the Caz Woo Order Mapping record."""
    erp_status = ""
    if so_name:
        erp_status = frappe.db.get_value("Sales Order", so_name, "status") or ""

    if mapping_name and frappe.db.exists("Caz Woo Order Mapping", mapping_name):
        frappe.db.set_value(
            "Caz Woo Order Mapping",
            mapping_name,
            {
                "sales_order": so_name,
                "woo_status": wc_status,
                "erp_status": erp_status,
                "last_synced": now_datetime(),
                "sync_error": "",
            },
        )
    else:
        mapping = frappe.new_doc("Caz Woo Order Mapping")
        mapping.update({
            "store": store_name,
            "woo_order_id": woo_order_id,
            "sales_order": so_name,
            "woo_status": wc_status,
            "erp_status": erp_status,
            "last_synced": now_datetime(),
            "sync_error": "",
        })
        mapping.insert(ignore_permissions=True)
