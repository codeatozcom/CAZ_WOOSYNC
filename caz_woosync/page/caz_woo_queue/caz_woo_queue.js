frappe.pages["caz-woo-queue"].on_show = function (wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("CAZ WooSync Queue"),
		single_column: true,
	});

	var state = { store: null, status: "All", offset: 0, limit: 50, timer: null };

	// --- Toolbar filters ---
	var $storeFilter = page.add_field({
		fieldtype: "Link",
		options: "Caz Woo Store",
		label: __("Store"),
		fieldname: "store_filter",
		change: function () {
			state.store = $storeFilter.get_value() || null;
			state.offset = 0;
			loadData();
		},
	});

	var $statusFilter = page.add_field({
		fieldtype: "Select",
		label: __("Status"),
		fieldname: "status_filter",
		options: ["All", "Queued", "Processing", "Done", "Failed", "Skipped"].join("\n"),
		default: "All",
		change: function () {
			state.status = $statusFilter.get_value() || "All";
			state.offset = 0;
			loadData();
		},
	});

	// --- Action buttons ---
	page.add_button(__("Retry Failed"), function () {
		frappe.call({
			method: "caz_woosync.page.caz_woo_queue.caz_woo_queue.retry_failed_items",
			args: { store: state.store },
			callback: function (r) {
				frappe.show_alert({
					message: __("{0} items queued for retry", [r.message.retried]),
					indicator: "green",
				});
				loadData();
			},
		});
	});

	page.add_button(__("Refresh"), function () {
		loadData();
	});

	// --- Summary cards ---
	var $summary = $('<div class="row" style="margin:16px 0"></div>').appendTo(page.body);

	// --- Main table ---
	var $tableWrap = $('<div style="overflow-x:auto"></div>').appendTo(page.body);

	var STATUS_COLOUR = {
		Queued: "blue",
		Processing: "orange",
		Done: "green",
		Failed: "red",
		Skipped: "grey",
	};

	function renderSummary(summary) {
		$summary.empty();
		var order = ["Queued", "Processing", "Done", "Failed", "Skipped"];
		var map = {};
		(summary || []).forEach(function (r) {
			map[r.status] = r.count;
		});
		order.forEach(function (s) {
			var count = map[s] || 0;
			var colour = STATUS_COLOUR[s] || "grey";
			$summary.append(
				'<div class="col-sm-2">' +
					'<div class="card text-center" style="padding:12px;margin:4px">' +
					'<div style="font-size:24px;font-weight:bold;color:' + colour + '">' + count + "</div>" +
					'<div style="font-size:12px;color:#888">' + __(s) + "</div>" +
					"</div></div>"
			);
		});
	}

	function renderTable(rows) {
		$tableWrap.empty();
		if (!rows || !rows.length) {
			$tableWrap.html(
				'<p style="padding:32px;text-align:center;color:#888">' +
					__("No queue items found.") +
					"</p>"
			);
			return;
		}

		var cols = [
			{ key: "store", label: __("Store") },
			{ key: "direction", label: __("Direction") },
			{ key: "entity_type", label: __("Entity") },
			{ key: "woo_id", label: __("Woo ID") },
			{ key: "erp_docname", label: __("ERPNext Doc") },
			{ key: "status", label: __("Status") },
			{ key: "attempt_count", label: __("Attempts") },
			{ key: "last_attempt", label: __("Last Attempt") },
			{ key: "error_preview", label: __("Error") },
		];

		var html =
			'<table class="table table-bordered table-sm" style="font-size:12px">' +
			"<thead><tr>" +
			cols.map(function (c) { return "<th>" + c.label + "</th>"; }).join("") +
			"</tr></thead><tbody>";

		rows.forEach(function (row) {
			var colour = STATUS_COLOUR[row.status] || "grey";
			html += "<tr>";
			cols.forEach(function (col) {
				var val = row[col.key] || "";
				if (col.key === "status") {
					val =
						'<span class="badge" style="background:' +
						colour +
						';color:#fff">' +
						frappe.utils.escape_html(val) +
						"</span>";
				} else if (col.key === "error_preview" && val) {
					val =
						'<span title="' +
						frappe.utils.escape_html(val) +
						'" style="cursor:help">' +
						frappe.utils.escape_html(val.substring(0, 60)) +
						(val.length > 60 ? "…" : "") +
						"</span>";
				} else {
					val = frappe.utils.escape_html(String(val));
				}
				html += "<td>" + val + "</td>";
			});
			html += "</tr>";
		});

		html += "</tbody></table>";
		$tableWrap.html(html);
	}

	function loadData() {
		frappe.call({
			method: "caz_woosync.page.caz_woo_queue.caz_woo_queue.get_queue_data",
			args: {
				store: state.store,
				status: state.status,
				limit: state.limit,
				offset: state.offset,
			},
			callback: function (r) {
				if (r.message) {
					renderSummary(r.message.summary);
					renderTable(r.message.rows);
				}
			},
		});
	}

	// --- Auto-refresh every 30 seconds ---
	state.timer = setInterval(function () {
		loadData();
	}, 30000);

	// Clear timer when page is hidden
	frappe.pages["caz-woo-queue"].on_hide = function () {
		if (state.timer) {
			clearInterval(state.timer);
			state.timer = null;
		}
	};

	loadData();
};
