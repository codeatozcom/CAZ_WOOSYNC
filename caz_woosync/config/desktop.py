from frappe import _


def get_data():
    return [
        {
            "module_name": "Caz Woosync",
            "color": "blue",
            "icon": "octicon octicon-sync",
            "type": "module",
            "label": _("CAZ WooSync"),
            "links": [
                {
                    "label": _("Multi-Store Dashboard"),
                    "icon": "octicon octicon-dashboard",
                    "type": "page",
                    "name": "caz-woo-dashboard",
                    "description": "Overview of all active WooCommerce store health, queue stats, and mapping counts.",
                },
                {
                    "label": _("Sync Queue"),
                    "icon": "octicon octicon-list-ordered",
                    "type": "page",
                    "name": "caz-woo-queue",
                    "description": "View and manage the WooCommerce sync queue for all stores.",
                },
                {
                    "label": _("Bulk Import"),
                    "icon": "octicon octicon-cloud-download",
                    "type": "page",
                    "name": "caz-woo-import",
                    "description": "Bulk import WooCommerce products, orders, and customers into ERPNext.",
                },
            ],
        }
    ]
