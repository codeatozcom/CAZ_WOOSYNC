/**
 * CAZ WooSync Multi-Store Dashboard
 * Renders a health card per store and auto-refreshes every 60 seconds.
 */
frappe.pages["caz-woo-dashboard"].on_page_load = function (wrapper) {
    var page = frappe.ui.make_app_page({
        parent: wrapper,
        title: "CAZ WooSync Dashboard",
        single_column: true,
    });

    // Store the refresh interval so we can clear it on page unload
    var refresh_interval = null;

    function get_status_color(status) {
        if (!status || status === "Untested") return "#888888";
        if (status === "Connected") return "#28a745";
        return "#dc3545"; // Failed or anything else
    }

    function badge(count, color) {
        return (
            '<span style="display:inline-block;min-width:28px;padding:2px 8px;border-radius:12px;' +
            'background:' + color + ';color:#fff;font-size:12px;font-weight:600;text-align:center;">' +
            count +
            "</span>"
        );
    }

    function render_store_card(store) {
        var conn_color = get_status_color(store.connection_status);
        var qs = store.queue_stats || {};
        var mc = store.mapping_counts || {};

        var card = $('<div class="caz-store-card" style="' +
            'background:#fff;border:1px solid #ddd;border-radius:8px;' +
            'padding:20px;margin-bottom:16px;box-shadow:0 1px 4px rgba(0,0,0,0.06);">' +

            // Header row
            '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">' +
            '<h4 style="margin:0;font-size:16px;">' + frappe.utils.escape_html(store.store_name) + '</h4>' +
            '<span style="display:inline-flex;align-items:center;gap:6px;">' +
            '<span style="width:10px;height:10px;border-radius:50%;background:' + conn_color + ';display:inline-block;"></span>' +
            '<span style="font-size:13px;color:' + conn_color + ';">' + frappe.utils.escape_html(store.connection_status || "Untested") + '</span>' +
            '</span>' +
            '</div>' +

            // Queue stats
            '<div style="margin-bottom:10px;">' +
            '<span style="font-size:12px;color:#888;margin-right:8px;">Queue:</span>' +
            '<span style="margin-right:6px;">Queued ' + badge(qs.queued || 0, "#6c757d") + '</span>' +
            '<span style="margin-right:6px;">Processing ' + badge(qs.processing || 0, "#007bff") + '</span>' +
            '<span style="margin-right:6px;">Failed ' + badge(qs.failed || 0, "#dc3545") + '</span>' +
            '<span>Done today ' + badge(qs.done_today || 0, "#28a745") + '</span>' +
            '</div>' +

            // Mapping counts
            '<div style="margin-bottom:10px;">' +
            '<span style="font-size:12px;color:#888;margin-right:8px;">Mapped:</span>' +
            '<span style="margin-right:12px;font-size:13px;">Items: <strong>' + (mc.items || 0) + '</strong></span>' +
            '<span style="margin-right:12px;font-size:13px;">Orders: <strong>' + (mc.orders || 0) + '</strong></span>' +
            '<span style="font-size:13px;">Customers: <strong>' + (mc.customers || 0) + '</strong></span>' +
            '</div>' +

            // Last sync
            '<div style="margin-bottom:14px;font-size:12px;color:#888;">' +
            'Last sync: ' + frappe.utils.escape_html(store.last_sync_time || "Never") +
            '</div>' +

            // Action buttons
            '<div style="display:flex;gap:8px;">' +
            '<button class="btn btn-sm btn-default btn-test-conn" data-store="' + frappe.utils.escape_html(store.store_name) + '">Test Connection</button>' +
            '<button class="btn btn-sm btn-default btn-view-queue" data-store="' + frappe.utils.escape_html(store.store_name) + '">View Queue</button>' +
            '</div>' +

            '</div>');

        card.find(".btn-test-conn").on("click", function () {
            var sname = $(this).data("store");
            frappe.show_alert({ message: "Testing connection to " + sname + "…", indicator: "blue" });
            frappe.call({
                method: "caz_woosync.api.connection.test_store_connection",
                args: { store_name: sname },
                callback: function (r) {
                    if (r.message && r.message.success) {
                        frappe.show_alert({ message: "Connected to " + sname + " (" + r.message.response_ms + " ms)", indicator: "green" });
                    } else {
                        frappe.show_alert({ message: (r.message && r.message.message) || "Connection failed", indicator: "red" });
                    }
                    load_dashboard(); // refresh cards after test
                },
            });
        });

        card.find(".btn-view-queue").on("click", function () {
            var sname = $(this).data("store");
            frappe.set_route("caz-woo-queue", { store: sname });
        });

        return card;
    }

    function render_dashboard(stores) {
        var $body = $(wrapper).find(".caz-dashboard-body");
        $body.empty();

        if (!stores || stores.length === 0) {
            $body.append('<p style="color:#888;padding:20px;">No active stores found. Create a Caz Woo Store and mark it active.</p>');
            return;
        }

        stores.forEach(function (store) {
            $body.append(render_store_card(store));
        });
    }

    function show_spinner() {
        $(wrapper).find(".caz-dashboard-spinner").show();
        $(wrapper).find(".caz-dashboard-body").hide();
    }

    function hide_spinner() {
        $(wrapper).find(".caz-dashboard-spinner").hide();
        $(wrapper).find(".caz-dashboard-body").show();
    }

    function load_dashboard() {
        show_spinner();
        frappe.call({
            method: "caz_woosync.page.caz_woo_dashboard.caz_woo_dashboard.get_dashboard_data",
            callback: function (r) {
                hide_spinner();
                render_dashboard(r.message || []);
            },
            error: function () {
                hide_spinner();
                frappe.show_alert({ message: "Failed to load dashboard data", indicator: "red" });
            },
        });
    }

    // Build layout
    $(wrapper).find(".page-content").append(
        '<div class="caz-dashboard-spinner" style="padding:40px;text-align:center;">' +
        '<div class="loading-spinner"></div><p style="color:#888;margin-top:10px;">Loading stores…</p>' +
        '</div>' +
        '<div class="caz-dashboard-body" style="padding:16px;max-width:900px;display:none;"></div>'
    );

    // Initial load
    load_dashboard();

    // Auto-refresh every 60 seconds
    refresh_interval = setInterval(load_dashboard, 60000);

    // Clean up on page unload
    $(wrapper).on("remove", function () {
        if (refresh_interval) {
            clearInterval(refresh_interval);
        }
    });
};
