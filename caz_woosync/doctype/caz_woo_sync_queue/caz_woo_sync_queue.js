frappe.ui.form.on("Caz Woo Sync Queue", {
	refresh(frm) {
		const status = frm.doc.status;

		if (status === "Failed") {
			frm.add_custom_button(__("Retry"), () => {
				frm.set_value("status", "Queued");
				frm.save().then(() => {
					frappe.show_alert({ message: __("Queued for retry."), indicator: "green" });
				});
			}).addClass("btn-primary");
		}

		if (status === "Failed" || status === "Queued") {
			frm.add_custom_button(__("Skip"), () => {
				frappe.confirm(
					__("Mark this sync event as Skipped? It will not be retried."),
					() => {
						frm.set_value("status", "Skipped");
						frm.save().then(() => {
							frappe.show_alert({
								message: __("Marked as Skipped."),
								indicator: "orange",
							});
						});
					}
				);
			});
		}
	},
});
