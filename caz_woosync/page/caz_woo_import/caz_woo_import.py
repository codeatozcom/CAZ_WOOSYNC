import frappe


@frappe.whitelist()
def start_import(store_name, entity_types, since_date=None, limit=None):
    """
    Start a bulk import job for the given store.
    entity_types: JSON string or list of "Product"|"Order"|"Customer"
    since_date: optional ISO date string to filter records after this date
    limit: optional max records per entity type
    Returns {"queued": [...entity_types...]}
    """
    import json

    from caz_woosync.sync.bulk_import import start_bulk_import

    types = json.loads(entity_types) if isinstance(entity_types, str) else entity_types
    return start_bulk_import(store_name, types, since_date, int(limit) if limit else None)


@frappe.whitelist()
def get_progress(store_name):
    """
    Return current bulk import progress stats for the given store.
    Returns queued, processing, done, failed counts and total_mapped breakdown.
    """
    from caz_woosync.sync.bulk_import import get_import_progress

    return get_import_progress(store_name)


@frappe.whitelist()
def cancel_import(store_name):
    """
    Cancel all queued bulk import items for the given store.
    Returns {"cancelled": True}
    """
    from caz_woosync.sync.bulk_import import cancel_bulk_import

    cancel_bulk_import(store_name)
    return {"cancelled": True}
