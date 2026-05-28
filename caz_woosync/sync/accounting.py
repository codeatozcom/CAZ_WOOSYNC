"""
WooCommerce → ERPNext Accounting Sync (Phase 7).

Handles creating Sales Invoices and Payment Entries from WooCommerce orders.
Triggered when WC order status becomes 'completed' or 'processing'.
"""
import frappe
from frappe.utils import today, nowdate


# ---------------------------------------------------------------------------
# Payment method mapping: WC payment_method → ERPNext Mode of Payment
# ---------------------------------------------------------------------------
_PAYMENT_METHOD_MAP = {
    "credit_card": "Credit Card",
    "creditcard": "Credit Card",
    "stripe": "Credit Card",
    "paypal": "PayPal",
    "paypal_standard": "PayPal",
    "ppec_paypal": "PayPal",
}

# COD / unpaid methods — do NOT create a Payment Entry for these
_COD_METHODS = {"cod", "cash_on_delivery", ""}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_invoice_from_order(store_name, so_name):
    """
    Create a Sales Invoice from an ERPNext Sales Order for a WooCommerce order.

    Called when WC order status becomes 'completed' or 'processing'.
    Idempotent: returns existing SI name if one already exists for this SO.
    """
    store = frappe.get_doc("Caz Woo Store", store_name)

    if not getattr(store, "auto_create_invoice", 1):
        frappe.logger().info(
            f"CAZ WooSync: auto_create_invoice disabled for store {store_name}. Skipping."
        )
        return None

    # 1. Idempotency check — find existing SI linked to this SO
    existing_si = frappe.db.get_value(
        "Sales Invoice Item",
        {"sales_order": so_name},
        "parent",
    )
    if existing_si:
        frappe.logger().info(
            f"CAZ WooSync: Sales Invoice {existing_si} already exists for SO {so_name}. Skipping."
        )
        return existing_si

    # 2. Fetch the Sales Order
    so = frappe.get_doc("Sales Order", so_name)

    # 3. Build Sales Invoice items from SO items
    si_items = []
    for so_item in (so.items or []):
        row = {
            "item_code": so_item.item_code,
            "item_name": so_item.item_name,
            "qty": so_item.qty,
            "rate": so_item.rate,
            "sales_order": so_name,
            "so_detail": so_item.name,
        }
        income_account = (
            getattr(so_item, "income_account", None)
            or getattr(store, "income_account", None)
        )
        if income_account:
            row["income_account"] = income_account
        si_items.append(row)

    if not si_items:
        frappe.logger().warning(
            f"CAZ WooSync: SO {so_name} has no items; cannot create Sales Invoice."
        )
        return None

    # 4. Build Sales Invoice taxes from SO taxes
    si_taxes = []
    for tax_row in (getattr(so, "taxes", None) or []):
        si_taxes.append({
            "charge_type": getattr(tax_row, "charge_type", "Actual"),
            "account_head": getattr(tax_row, "account_head", ""),
            "description": getattr(tax_row, "description", "Tax"),
            "tax_amount": getattr(tax_row, "tax_amount", 0),
        })

    # 5. Determine naming series
    naming_series = (
        getattr(store, "invoice_naming_series", None)
        or "SINV-CAZWOO-.YYYY.-"
    )

    # 6. Determine receivable account
    receivable_account = (
        getattr(store, "receivable_account", None)
        or _get_default_account(so.company, "Receivable")
    )

    posting_date_val = today()

    # 7. Build and insert SI
    si = frappe.new_doc("Sales Invoice")
    si.update({
        "naming_series": naming_series,
        "company": so.company,
        "customer": so.customer,
        "currency": so.currency,
        "set_posting_time": 1,
        "posting_date": posting_date_val,
        "due_date": posting_date_val,
        "is_pos": 0,
        "items": si_items,
    })

    if receivable_account:
        si.debit_to = receivable_account

    if si_taxes:
        si.set("taxes", si_taxes)

    si.insert(ignore_permissions=True)

    # 8. Submit if store setting says so
    if getattr(store, "auto_submit_invoice", 0):
        si.submit()

    # 9. Update Caz Woo Order Mapping with si_name
    mapping_name = frappe.db.get_value(
        "Caz Woo Order Mapping",
        {"store": store_name, "sales_order": so_name},
        "name",
    )
    if mapping_name:
        frappe.db.set_value(
            "Caz Woo Order Mapping",
            mapping_name,
            "si_name",
            si.name,
        )

    frappe.logger().info(
        f"CAZ WooSync: Created Sales Invoice {si.name} for SO {so_name}."
    )
    return si.name


def create_payment_from_order(store_name, so_name, woo_order_payload):
    """
    Create a Payment Entry for a WooCommerce order.

    Called after Sales Invoice is created/submitted.
    Only creates PE if the WC payment_method indicates already-paid (not COD).
    Idempotent: returns existing PE name if one already exists for this SI.
    """
    store = frappe.get_doc("Caz Woo Store", store_name)

    if not getattr(store, "auto_create_payment", 1):
        frappe.logger().info(
            f"CAZ WooSync: auto_create_payment disabled for store {store_name}. Skipping."
        )
        return None

    # 1. Skip COD / unpaid methods
    payment_method = str(woo_order_payload.get("payment_method") or "").lower().strip()
    if payment_method in _COD_METHODS:
        frappe.logger().info(
            f"CAZ WooSync: Skipping Payment Entry for COD/unpaid method '{payment_method}' "
            f"on SO {so_name}."
        )
        return None

    # 2. Get SI name from Caz Woo Order Mapping
    si_name = frappe.db.get_value(
        "Caz Woo Order Mapping",
        {"store": store_name, "sales_order": so_name},
        "si_name",
    )
    if not si_name:
        frappe.logger().warning(
            f"CAZ WooSync: No Sales Invoice found in mapping for SO {so_name}. Cannot create PE."
        )
        return None

    # 3. Idempotency check — find existing PE linked to SI
    existing_pe = frappe.db.get_value(
        "Payment Entry Reference",
        {"reference_doctype": "Sales Invoice", "reference_name": si_name},
        "parent",
    )
    if existing_pe:
        frappe.logger().info(
            f"CAZ WooSync: Payment Entry {existing_pe} already exists for SI {si_name}. Skipping."
        )
        return existing_pe

    # 4. Resolve mode of payment
    payment_method_title = str(woo_order_payload.get("payment_method_title") or "").strip()
    mode_of_payment = _map_payment_method(payment_method, payment_method_title)

    # 5. Get the SI doc
    si = frappe.get_doc("Sales Invoice", si_name)

    # 6. Determine accounts
    paid_from = (
        getattr(store, "receivable_account", None)
        or _get_default_account(si.company, "Receivable")
    )
    paid_to = (
        getattr(store, "payment_account", None)
        or _get_default_account(si.company, "Bank")
        or _get_default_account(si.company, "Cash")
    )

    # 7. Determine payment amount
    paid_amount = float(woo_order_payload.get("total") or 0)
    if paid_amount <= 0:
        frappe.logger().warning(
            f"CAZ WooSync: WC order total is 0 or missing for SO {so_name}. Skipping PE."
        )
        return None

    # 8. Reference fields
    reference_no = str(
        woo_order_payload.get("transaction_id")
        or woo_order_payload.get("id")
        or ""
    )
    reference_date_val = today()

    # 9. Build and insert PE
    pe = frappe.new_doc("Payment Entry")
    pe.update({
        "payment_type": "Receive",
        "party_type": "Customer",
        "party": si.customer,
        "company": si.company,
        "mode_of_payment": mode_of_payment,
        "paid_from": paid_from,
        "paid_to": paid_to,
        "paid_amount": paid_amount,
        "received_amount": paid_amount,
        "reference_no": reference_no,
        "reference_date": reference_date_val,
        "remarks": f"CAZ WooSync: Payment for WooCommerce order {woo_order_payload.get('id')}",
    })

    # Add reference row linking to the Sales Invoice
    pe.append("references", {
        "reference_doctype": "Sales Invoice",
        "reference_name": si_name,
        "allocated_amount": paid_amount,
    })

    pe.insert(ignore_permissions=True)

    # 10. Submit if store setting says so
    if getattr(store, "auto_submit_payment", 0):
        pe.submit()

    frappe.logger().info(
        f"CAZ WooSync: Created Payment Entry {pe.name} for SI {si_name} (SO {so_name})."
    )
    return pe.name


def handle_order_status_change(store_name, woo_order_id, new_status, payload):
    """
    Called by sync/orders.py _update_order_status to trigger accounting actions.

    Maps WC status transitions to accounting documents:
    - 'completed'  → create_invoice_from_order + create_payment_from_order
    - 'processing' → create_invoice_from_order only (payment pending)
    - 'cancelled'  → cancel SI and PE if they exist (only if docstatus==1)
    - 'refunded'   → log warning (full refund handling in Phase 12)
    - Others       → no-op
    """
    woo_order_id = str(woo_order_id)
    status = (new_status or "").lower().strip()

    # Resolve SO name from mapping
    so_name = frappe.db.get_value(
        "Caz Woo Order Mapping",
        {"store": store_name, "woo_order_id": woo_order_id},
        "sales_order",
    )

    if not so_name:
        frappe.logger().warning(
            f"CAZ WooSync: No Sales Order found for WC order {woo_order_id} in store {store_name}. "
            f"Cannot process accounting for status '{status}'."
        )
        return

    if status == "completed":
        try:
            si_name = create_invoice_from_order(store_name, so_name)
            if si_name:
                create_payment_from_order(store_name, so_name, payload)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"CAZ WooSync: Accounting failed for completed order {woo_order_id}",
            )

    elif status == "processing":
        try:
            create_invoice_from_order(store_name, so_name)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"CAZ WooSync: Invoice creation failed for processing order {woo_order_id}",
            )

    elif status == "cancelled":
        _cancel_accounting_docs(store_name, so_name, woo_order_id)

    elif status == "refunded":
        frappe.logger().warning(
            f"CAZ WooSync: Order {woo_order_id} refunded — full refund handling deferred to Phase 12."
        )

    else:
        # Other statuses (pending, on-hold, failed, etc.) — no accounting action
        frappe.logger().info(
            f"CAZ WooSync: No accounting action for order {woo_order_id} status '{status}'."
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _cancel_accounting_docs(store_name, so_name, woo_order_id):
    """Cancel SI and PE linked to this order if they are submitted."""
    mapping = frappe.db.get_value(
        "Caz Woo Order Mapping",
        {"store": store_name, "sales_order": so_name},
        ["si_name"],
        as_dict=True,
    )
    si_name = mapping.get("si_name") if mapping else None

    # Cancel Payment Entry first (child of SI)
    if si_name:
        pe_name = frappe.db.get_value(
            "Payment Entry Reference",
            {"reference_doctype": "Sales Invoice", "reference_name": si_name},
            "parent",
        )
        if pe_name:
            try:
                pe = frappe.get_doc("Payment Entry", pe_name)
                if pe.docstatus == 1:
                    pe.cancel()
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    f"CAZ WooSync: Failed to cancel Payment Entry {pe_name}",
                )

        # Cancel Sales Invoice
        try:
            si = frappe.get_doc("Sales Invoice", si_name)
            if si.docstatus == 1:
                si.cancel()
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"CAZ WooSync: Failed to cancel Sales Invoice {si_name}",
            )


def _map_payment_method(payment_method, payment_method_title=""):
    """
    Map WooCommerce payment_method slug to ERPNext Mode of Payment.

    Known mappings: Credit Card → "Credit Card", PayPal → "PayPal".
    Anything else falls back to "Wire Transfer" as a safe default.
    """
    key = payment_method.lower().strip()
    if key in _PAYMENT_METHOD_MAP:
        return _PAYMENT_METHOD_MAP[key]

    # Also try matching on payment_method_title
    title_lower = payment_method_title.lower().strip()
    if "credit" in title_lower or "card" in title_lower or "stripe" in title_lower:
        return "Credit Card"
    if "paypal" in title_lower:
        return "PayPal"

    return "Wire Transfer"


def _get_default_account(company, account_type):
    """
    Get default account for a company by account_type.

    account_type: one of "Receivable", "Bank", "Cash".
    Returns account name or empty string if not found.
    """
    return (
        frappe.db.get_value(
            "Account",
            {"company": company, "account_type": account_type, "is_group": 0},
            "name",
        )
        or ""
    )
