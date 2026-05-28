frappe.ui.form.on("Caz Woo Store", {
	refresh(frm) {
		// Buttons only make sense on saved (non-new) records
		if (!frm.is_new()) {
			frm.add_custom_button(__("Test Connection"), () => {
				frm.call({
					method: "test_connection",
					doc: frm.doc,
					freeze: true,
					freeze_message: __("Testing connection to WooCommerce…"),
					callback(r) {
						if (r.message && r.message.success) {
							frappe.msgprint({
								title: __("Connection Successful"),
								message: `✅ ${r.message.message} (${r.message.response_ms} ms)`,
								indicator: "green",
							});
						} else {
							frappe.msgprint({
								title: __("Connection Failed"),
								message: `❌ ${r.message ? r.message.message : __("Unknown error")}`,
								indicator: "red",
							});
						}
						frm.reload_doc();
					},
				});
			}, __("WooCommerce"));

			frm.add_custom_button(__("Install Webhooks"), () => {
				frm.call({
					method: "install_webhooks",
					doc: frm.doc,
					freeze: true,
					freeze_message: __("Installing webhooks in WooCommerce…"),
					callback(r) {
						if (!r.message || !r.message.results) {
							frappe.msgprint(__("No response from server. Check error logs."));
							return;
						}
						const rows = r.message.results.map((item) => {
							const icon = item.success ? "✅" : "❌";
							const note = item.note ? ` <em>(${item.note})</em>` : "";
							const err = item.error
								? `<br><small class="text-muted">${item.error}</small>`
								: "";
							return `<tr><td>${icon} <code>${item.topic}</code>${note}${err}</td></tr>`;
						});
						frappe.msgprint({
							title: __("Webhook Installation Results"),
							message: `<table class="table table-bordered">${rows.join("")}</table>`,
							indicator: r.message.results.every((i) => i.success)
								? "green"
								: "orange",
						});
						frm.reload_doc();
					},
				});
			}, __("WooCommerce"));
		}

		// Colour the connection_status field
		const status = frm.doc.connection_status;
		const colour_map = {
			Connected: "green",
			Failed: "red",
			Untested: "orange",
		};
		const colour = colour_map[status] || "grey";
		frm.get_field("connection_status") &&
			frm.get_field("connection_status").$wrapper
				.find(".control-value")
				.css("color", colour)
				.css("font-weight", "bold");
	},
});
