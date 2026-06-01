app_name = "caz_woosync"
app_title = "Caz Woosync"
app_publisher = "CodeAtoZ"
app_description = "Real-time WooCommerce sync for ERPNext v14, v15 and v16"
app_email = "support@codeatoz.com"
app_license = "MIT"
app_version = "1.0.0"
app_icon = "octicon octicon-sync"
app_color = "blue"

required_apps = ["erpnext"]

after_install = "caz_woosync.install.after_install"
after_uninstall = "caz_woosync.install.after_uninstall"

scheduler_events = {
    "cron": {
        "*/5 * * * *": [
            "caz_woosync.tasks.process_sync_queue",
        ],
        "*/15 * * * *": [
            "caz_woosync.tasks.poll_woocommerce_changes",
        ],
    },
    "daily": [
        "caz_woosync.tasks.daily_health_check",
    ],
}

doctype_js = {
    "Caz Woo Store": "doctype/caz_woo_store/caz_woo_store.js",
    "Caz Woo Sync Queue": "doctype/caz_woo_sync_queue/caz_woo_sync_queue.js",
}

doc_events = {
    "Item": {
        "on_update": "caz_woosync.sync.items.on_item_update",
    },
    "Stock Ledger Entry": {
        "on_submit": "caz_woosync.sync.inventory.on_stock_ledger_submit",
    },
    "Item Price": {
        "on_update": "caz_woosync.sync.prices.on_item_price_update",
    },
}
