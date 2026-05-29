frappe.pages["caz-woo-import"].on_page_load = function (wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "CAZ WooSync — Bulk Import",
		single_column: true,
	});

	$(frappe.render_template("caz_woo_import", {})).appendTo(page.body);

	var poll_interval = null;
	var is_running = false;

	// Store selector
	var store_field = frappe.ui.form.make_control({
		df: {
			fieldtype: "Link",
			fieldname: "store_name",
			label: "Store",
			options: "Caz Woo Store",
			description: "Select the WooCommerce store to import from.",
			reqd: 1,
		},
		parent: page.body.find(".store-field-wrapper"),
		render_input: true,
	});
	store_field.refresh();

	// Since date field
	var since_field = frappe.ui.form.make_control({
		df: {
			fieldtype: "Date",
			fieldname: "since_date",
			label: "Import Records After",
			description: "Only import records created or modified after this date (optional).",
		},
		parent: page.body.find(".since-field-wrapper"),
		render_input: true,
	});
	since_field.refresh();

	// Limit field
	var limit_field = frappe.ui.form.make_control({
		df: {
			fieldtype: "Int",
			fieldname: "limit",
			label: "Max Records Per Type",
			description: "Maximum number of records to import per entity type (leave blank for all).",
		},
		parent: page.body.find(".limit-field-wrapper"),
		render_input: true,
	});
	limit_field.refresh();

	// Start Import button
	page.body.find(".btn-start-import").on("click", function () {
		var store_name = store_field.get_value();
		if (!store_name) {
			frappe.msgprint("Please select a store.");
			return;
		}

		var entity_types = [];
		if (page.body.find(".import-products").is(":checked")) entity_types.push("Product");
		if (page.body.find(".import-orders").is(":checked")) entity_types.push("Order");
		if (page.body.find(".import-customers").is(":checked")) entity_types.push("Customer");

		if (!entity_types.length) {
			frappe.msgprint("Please select at least one entity type to import.");
			return;
		}

		var since_date = since_field.get_value() || null;
		var limit = limit_field.get_value() || null;

		frappe.call({
			method: "caz_woosync.page.caz_woo_import.caz_woo_import.start_import",
			args: {
				store_name: store_name,
				entity_types: JSON.stringify(entity_types),
				since_date: since_date,
				limit: limit,
			},
			callback: function (r) {
				if (r.message) {
					frappe.show_alert({
						message: "Import queued for: " + r.message.queued.join(", "),
						indicator: "green",
					});
					is_running = true;
					page.body.find(".progress-section").show();
					start_polling(store_name);
				}
			},
		});
	});

	// Cancel button
	page.body.find(".btn-cancel-import").on("click", function () {
		var store_name = store_field.get_value();
		if (!store_name) return;

		frappe.confirm("Cancel all queued import items?", function () {
			frappe.call({
				method: "caz_woosync.page.caz_woo_import.caz_woo_import.cancel_import",
				args: { store_name: store_name },
				callback: function (r) {
					if (r.message && r.message.cancelled) {
						frappe.show_alert({ message: "Import cancelled.", indicator: "orange" });
						stop_polling();
					}
				},
			});
		});
	});

	function start_polling(store_name) {
		stop_polling();
		poll_interval = setInterval(function () {
			fetch_progress(store_name);
		}, 5000);
		fetch_progress(store_name);
	}

	function stop_polling() {
		if (poll_interval) {
			clearInterval(poll_interval);
			poll_interval = null;
		}
	}

	function fetch_progress(store_name) {
		frappe.call({
			method: "caz_woosync.page.caz_woo_import.caz_woo_import.get_progress",
			args: { store_name: store_name },
			callback: function (r) {
				if (!r.message) return;
				var data = r.message;
				var total = (data.queued || 0) + (data.processing || 0) + (data.done || 0) + (data.failed || 0);
				var done = (data.done || 0) + (data.failed || 0);
				var pct = total > 0 ? Math.round((done / total) * 100) : 0;

				page.body.find(".stat-queued").text(data.queued || 0);
				page.body.find(".stat-processing").text(data.processing || 0);
				page.body.find(".stat-done").text(data.done || 0);
				page.body.find(".stat-failed").text(data.failed || 0);

				var mapped = data.total_mapped || {};
				page.body.find(".mapped-products").text(mapped.products || 0);
				page.body.find(".mapped-orders").text(mapped.orders || 0);
				page.body.find(".mapped-customers").text(mapped.customers || 0);

				page.body.find(".progress-bar").css("width", pct + "%").attr("aria-valuenow", pct).text(pct + "%");

				if (data.queued === 0 && data.processing === 0 && is_running) {
					stop_polling();
					is_running = false;
					frappe.show_alert({ message: "Import complete!", indicator: "green" });
				}
			},
		});
	}
};

frappe.templates["caz_woo_import"] = `
<div class="caz-woo-import-page" style="padding: 20px; max-width: 800px;">
  <div class="store-field-wrapper" style="margin-bottom: 15px;"></div>

  <div style="margin-bottom: 15px;">
    <label><strong>Entity Types to Import</strong></label><br>
    <label style="margin-right: 15px;">
      <input type="checkbox" class="import-products" checked> Products
    </label>
    <label style="margin-right: 15px;">
      <input type="checkbox" class="import-orders" checked> Orders
    </label>
    <label>
      <input type="checkbox" class="import-customers" checked> Customers
    </label>
  </div>

  <div class="since-field-wrapper" style="margin-bottom: 15px;"></div>
  <div class="limit-field-wrapper" style="margin-bottom: 15px;"></div>

  <div style="margin-bottom: 20px;">
    <button class="btn btn-primary btn-start-import">Start Import</button>
    <button class="btn btn-danger btn-cancel-import" style="margin-left: 10px;">Cancel</button>
    <a href="/caz-woo-queue" class="btn btn-default" style="margin-left: 10px;">View Queue</a>
  </div>

  <div class="progress-section" style="display:none;">
    <h4>Import Progress</h4>
    <div class="progress" style="margin-bottom: 15px;">
      <div class="progress-bar progress-bar-striped active" role="progressbar"
           aria-valuenow="0" aria-valuemin="0" aria-valuemax="100"
           style="width: 0%; min-width: 30px;">0%</div>
    </div>

    <table class="table table-bordered" style="max-width: 400px;">
      <tbody>
        <tr><td>Queued</td><td class="stat-queued">-</td></tr>
        <tr><td>Processing</td><td class="stat-processing">-</td></tr>
        <tr><td>Done</td><td class="stat-done">-</td></tr>
        <tr><td>Failed</td><td class="stat-failed">-</td></tr>
      </tbody>
    </table>

    <h5>Total Mapped in ERPNext</h5>
    <table class="table table-bordered" style="max-width: 400px;">
      <tbody>
        <tr><td>Products</td><td class="mapped-products">-</td></tr>
        <tr><td>Orders</td><td class="mapped-orders">-</td></tr>
        <tr><td>Customers</td><td class="mapped-customers">-</td></tr>
      </tbody>
    </table>
  </div>
</div>
`;
