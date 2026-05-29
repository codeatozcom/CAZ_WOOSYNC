"""
WooCommerce → ERPNext Refund Handling (Phase 12).

Handles creating Credit Notes (Sales Invoice with is_return=1) from WooCommerce refunds.
"""
import frappe
from frappe.utils import today


def handle_refund(store_name, woo_order_id, payload=None):
    """
    Process a WooCommerce order refund.
    Creates a Credit Note (Sales Invoice with is_return=1) in ERPNext.
    payload: webhook JSON for the refund event (order.updated with status=refunded)
    """
    woo_order_id = str(woo_order_id)
    store = frappe.get_doc("Caz Woo Store", store_name)

    # 1. Get mapping: find si_name from Caz Woo Order Mapping by woo_order_id
    si_name = frappe.db.get_value(
        "Caz Woo Order Mapping",
        {"store": store_name, "woo_order_id": woo_order_id},
        "si_name",
    )

    # 2. If no SI: log and return
    if not si_name:
        frappe.logger().warning(
            f"CAZ WooSync: No Sales Invoice found for WC order {woo_order_id} in store {store_name}. "
            "Cannot create credit note."
        )
        return None

    # 3. Check if credit note already exists (idempotent)
    existing_cn = frappe.db.get_value(
        "Sales Invoice",
        {"return_against": si_name, "docstatus": ["!=", 2]},
        "name",
    )

    # 4. If exists: return existing credit note name
    if existing_cn:
        frappe.logger().info(
            f"CAZ WooSync: Credit note {existing_cn} already exists for SI {si_name}. Skipping."
        )
        return existing_cn

    # 5. If payload: get refund amount from payload
    refund_amount = None
    if payload:
        refunds = payload.get("refunds", [{}])
        if refunds:
            refund_total = refunds[0].get("total", "0")
            try:
                refund_amount = float(refund_total)
            except (TypeError, ValueError):
                refund_amount = None

    # 6. Get original SI
    original_si = frappe.get_doc("Sales Invoice", si_name)

    # 7. Create credit note items: copy from original SI with negative qty
    cn_items = []
    for si_item in (original_si.items or []):
        cn_items.append({
            "item_code": si_item.item_code,
            "item_name": getattr(si_item, "item_name", si_item.item_code),
            "qty": -(abs(si_item.qty or 1)),
            "rate": si_item.rate,
        })

    # 8. Build and insert credit note
    cn = frappe.new_doc("Sales Invoice")
    cn.update({
        "is_return": 1,
        "return_against": si_name,
        "customer": original_si.customer,
        "company": store.company,
        "posting_date": today(),
        "items": cn_items,
    })

    cn.insert(ignore_permissions=True)

    # 9. Submit if store.auto_submit_invoice == 1
    if getattr(store, "auto_submit_invoice", 0) == 1:
        cn.submit()

    # 10. Log result
    frappe.logger().info(
        f"CAZ WooSync: Created credit note {cn.name} for SI {si_name} (WC order {woo_order_id})."
    )
    return cn.name


def handle_partial_refund(store_name, woo_order_id, refund_amount, refund_reason=""):
    """
    Process a partial refund — creates a credit note for the partial amount only.
    Used when WC refund is less than the full order total.
    """
    woo_order_id = str(woo_order_id)
    store = frappe.get_doc("Caz Woo Store", store_name)

    # Get si_name from mapping
    si_name = frappe.db.get_value(
        "Caz Woo Order Mapping",
        {"store": store_name, "woo_order_id": woo_order_id},
        "si_name",
    )

    if not si_name:
        frappe.logger().warning(
            f"CAZ WooSync: No Sales Invoice for WC order {woo_order_id}. Cannot create partial credit note."
        )
        return None

    original_si = frappe.get_doc("Sales Invoice", si_name)

    # Ensure "Refund Adjustment" item exists
    if not frappe.db.exists("Item", "Refund Adjustment"):
        adj_item = frappe.new_doc("Item")
        adj_item.update({
            "item_code": "Refund Adjustment",
            "item_name": "Refund Adjustment",
            "is_stock_item": 0,
            "item_group": "All Item Groups",
        })
        adj_item.insert(ignore_permissions=True)

    remarks = refund_reason or "WooCommerce partial refund"

    cn = frappe.new_doc("Sales Invoice")
    cn.update({
        "is_return": 1,
        "return_against": si_name,
        "customer": original_si.customer,
        "company": store.company,
        "posting_date": today(),
        "remarks": remarks,
        "items": [
            {
                "item_code": "Refund Adjustment",
                "item_name": "Refund Adjustment",
                "qty": -1,
                "rate": float(refund_amount),
            }
        ],
    })

    cn.insert(ignore_permissions=True)

    if getattr(store, "auto_submit_invoice", 0) == 1:
        cn.submit()

    frappe.logger().info(
        f"CAZ WooSync: Created partial credit note {cn.name} for SI {si_name}, amount={refund_amount}."
    )
    return cn.name
