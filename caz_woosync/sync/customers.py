"""
WooCommerce → ERPNext Customer Sync (Phase 5).

Handles creating and updating ERPNext Customers, Contacts, and Addresses
from WooCommerce customer payloads.
"""
import frappe
from frappe.utils import now_datetime
from frappe.utils.html_utils import strip_html

# Maximum character length for customer_name (ERPNext field limit)
CUSTOMER_NAME_MAX_LEN = 140
# Maximum character length for phone numbers
PHONE_MAX_LEN = 20


def sync_customer_to_erp(store_name, woo_customer_id, payload=None):
    """
    Create or update an ERPNext Customer from a WooCommerce customer.
    payload: parsed JSON dict from webhook (None → fetch from WC API).
    """
    store = frappe.get_doc("Caz Woo Store", store_name)
    woo_customer_id = str(woo_customer_id)

    # 1. Fetch payload from API if not provided
    if payload is None:
        from caz_woosync.utils.rate_limiter import WooCommerceClient
        client = WooCommerceClient(store_name)
        resp = client.get(f"customers/{woo_customer_id}")
        if resp.status_code != 200:
            frappe.throw(
                f"WooCommerce API returned HTTP {resp.status_code} for customer {woo_customer_id}. "
                "Check your API credentials and that the customer exists."
            )
        payload = resp.json()

    email = (strip_html(payload.get("email") or "")).lower().strip()

    # 2. Check mapping by woo_customer_id + store
    mapping = frappe.db.get_value(
        "Caz Woo Customer Mapping",
        {"store": store_name, "woo_customer_id": woo_customer_id},
        ["name", "customer"],
        as_dict=True,
    )

    # 3. Also check by email in case customer changed ID (edge case)
    if not mapping and email:
        mapping = frappe.db.get_value(
            "Caz Woo Customer Mapping",
            {"store": store_name, "woo_email": email},
            ["name", "customer"],
            as_dict=True,
        )

    if mapping and mapping.get("customer"):
        # 4. Mapping exists → update customer fields
        customer_name = mapping["customer"]
        _update_customer_fields(customer_name, payload)
    else:
        # 5. No mapping → create new customer
        customer_name = _create_customer(payload, store)

    # 6. Upsert Contact
    _upsert_contact(customer_name, payload)

    # 7 & 8. Upsert Billing and Shipping Addresses
    billing = payload.get("billing") or {}
    shipping = payload.get("shipping") or {}

    _upsert_address(customer_name, billing, addr_type="Billing")

    # Only upsert shipping if it differs from billing (check address_line1 as sentinel)
    billing_addr1 = strip_html(billing.get("address_1") or "").strip()
    shipping_addr1 = strip_html(shipping.get("address_1") or "").strip()
    if shipping_addr1 and shipping_addr1 != billing_addr1:
        _upsert_address(customer_name, shipping, addr_type="Shipping")

    # 9. Upsert mapping
    _upsert_customer_mapping(store_name, woo_customer_id, customer_name, email)

    # 10. Commit
    frappe.db.commit()


def _create_customer(payload, store):
    """Create ERPNext Customer from WooCommerce customer payload."""
    first = strip_html(payload.get("first_name") or "").strip()
    last = strip_html(payload.get("last_name") or "").strip()
    email = (strip_html(payload.get("email") or "")).lower().strip()

    # Build customer_name: first + last, fallback to email username, fallback to email
    full_name = f"{first} {last}".strip()
    if not full_name:
        # Use part before @ as fallback
        full_name = email.split("@")[0] if email else "WooCommerce Customer"

    # Enforce max length
    customer_name = full_name[:CUSTOMER_NAME_MAX_LEN]

    customer_group = getattr(store, "customer_group", None) or "All Customer Groups"
    territory = getattr(store, "default_territory", None) or "All Territories"

    customer = frappe.new_doc("Customer")
    customer.update({
        "customer_name": customer_name,
        "customer_type": "Individual",
        "customer_group": customer_group,
        "territory": territory,
    })
    customer.insert(ignore_permissions=True)

    return customer.name


def _update_customer_fields(customer_name, payload):
    """Update an existing ERPNext Customer from a WooCommerce payload."""
    first = strip_html(payload.get("first_name") or "").strip()
    last = strip_html(payload.get("last_name") or "").strip()

    # Compute new name from WC payload billing info if available
    billing = payload.get("billing") or {}
    billing_first = strip_html(billing.get("first_name") or "").strip()
    billing_last = strip_html(billing.get("last_name") or "").strip()

    # Prefer top-level first/last; fall back to billing
    new_first = first or billing_first
    new_last = last or billing_last
    new_full = f"{new_first} {new_last}".strip()

    if new_full:
        current_name = frappe.db.get_value("Customer", customer_name, "customer_name") or ""
        if new_full[:CUSTOMER_NAME_MAX_LEN] != current_name:
            frappe.db.set_value(
                "Customer",
                customer_name,
                "customer_name",
                new_full[:CUSTOMER_NAME_MAX_LEN],
            )


def _upsert_contact(customer_name, payload):
    """Create or update a Contact linked to this Customer."""
    billing = payload.get("billing") or {}

    email = (strip_html(payload.get("email") or "") or strip_html(billing.get("email") or "")).lower().strip()
    first = (strip_html(payload.get("first_name") or "") or strip_html(billing.get("first_name") or "")).strip()
    last = (strip_html(payload.get("last_name") or "") or strip_html(billing.get("last_name") or "")).strip()
    phone = strip_html(billing.get("phone") or "").strip()[:PHONE_MAX_LEN]

    if not email and not phone:
        return  # Not enough contact info

    # Look up Contact by email_id in Contact Email child table
    existing_contact = None
    if email:
        result = frappe.db.sql(
            """
            SELECT c.name
            FROM `tabContact` c
            JOIN `tabContact Email` ce ON ce.parent = c.name
            JOIN `tabDynamic Link` dl ON dl.parent = c.name
                AND dl.link_doctype = 'Customer'
                AND dl.link_name = %s
            WHERE ce.email_id = %s
            LIMIT 1
            """,
            (customer_name, email),
            as_dict=True,
        )
        if result:
            existing_contact = result[0]["name"]

    if existing_contact:
        contact = frappe.get_doc("Contact", existing_contact)
        contact.first_name = first or contact.first_name
        contact.last_name = last or contact.last_name

        # Update phone if changed
        if phone:
            phone_exists = any(
                pn.phone == phone for pn in (contact.phone_nos or [])
            )
            if not phone_exists:
                contact.phone_nos = []
                contact.append("phone_nos", {
                    "phone": phone,
                    "is_primary_phone": 1,
                })

        contact.save(ignore_permissions=True)
    else:
        contact = frappe.new_doc("Contact")
        contact.first_name = first or customer_name
        contact.last_name = last
        contact.is_primary_contact = 1

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
            "link_name": customer_name,
        })
        try:
            contact.insert(ignore_permissions=True)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"CAZ WooSync: Failed to create Contact for customer {customer_name}",
            )


def _upsert_address(customer_name, addr_data, addr_type="Billing"):
    """
    Create or update an Address linked to this Customer.
    addr_type: 'Billing' or 'Shipping'.
    Skip if address_line1 is empty.
    """
    address_line1 = strip_html(addr_data.get("address_1") or "").strip()
    if not address_line1:
        return  # Skip if no address_line1

    address_line2 = strip_html(addr_data.get("address_2") or "").strip()
    city = strip_html(addr_data.get("city") or "").strip()
    state = strip_html(addr_data.get("state") or "").strip()
    pincode = strip_html(addr_data.get("postcode") or "").strip()
    country_code = strip_html(addr_data.get("country") or "").strip()

    # Resolve ISO country code to ERPNext Country name
    country = ""
    if country_code:
        country = frappe.db.get_value("Country", {"name": country_code}) or country_code

    # Look up existing address linked to customer via Dynamic Link
    existing_address = frappe.db.sql(
        """
        SELECT a.name
        FROM `tabAddress` a
        JOIN `tabDynamic Link` dl ON dl.parent = a.name
            AND dl.link_doctype = 'Customer'
            AND dl.link_name = %s
        WHERE a.address_type = %s
        LIMIT 1
        """,
        (customer_name, addr_type),
        as_dict=True,
    )

    if existing_address:
        addr = frappe.get_doc("Address", existing_address[0]["name"])
    else:
        addr = frappe.new_doc("Address")
        first = strip_html(addr_data.get("first_name") or "").strip()
        last = strip_html(addr_data.get("last_name") or "").strip()
        address_title = f"{first} {last}".strip() or customer_name
        addr.address_title = address_title
        addr.address_type = addr_type

    addr.address_line1 = address_line1
    addr.address_line2 = address_line2
    addr.city = city or "Unknown"
    addr.state = state
    addr.country = country
    addr.pincode = pincode

    # Ensure Dynamic Link to Customer exists
    existing_links = [
        lnk for lnk in (addr.links or [])
        if lnk.link_doctype == "Customer" and lnk.link_name == customer_name
    ]
    if not existing_links:
        addr.links = [lnk for lnk in (addr.links or []) if lnk.link_doctype != "Customer"]
        addr.append("links", {
            "link_doctype": "Customer",
            "link_name": customer_name,
        })

    if existing_address:
        addr.save(ignore_permissions=True)
    else:
        addr.insert(ignore_permissions=True)


def _upsert_customer_mapping(store_name, woo_customer_id, customer_name, email):
    """Create or update Caz Woo Customer Mapping."""
    existing = frappe.db.get_value(
        "Caz Woo Customer Mapping",
        {"store": store_name, "woo_customer_id": woo_customer_id},
        "name",
    )

    if existing:
        frappe.db.set_value(
            "Caz Woo Customer Mapping",
            existing,
            {
                "customer": customer_name,
                "woo_email": email,
                "last_synced": now_datetime(),
                "sync_error": "",
            },
        )
    else:
        mapping = frappe.new_doc("Caz Woo Customer Mapping")
        mapping.update({
            "store": store_name,
            "woo_customer_id": woo_customer_id,
            "woo_email": email,
            "customer": customer_name,
            "last_synced": now_datetime(),
            "sync_error": "",
        })
        mapping.insert(ignore_permissions=True)
