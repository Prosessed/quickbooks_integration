frappe.ui.form.on("Purchase Invoice", {
	refresh: (frm) => {
		if (frm.is_new()) {
			return;
		}

		const hasBillId = Boolean(frm.doc.custom_quickbooks_bill_id);
		const hasDebitNoteId = Boolean(frm.doc.custom_quickbooks_debitnote_id);
		const cancelledOnQbo = cint(frm.doc.custom_is_cancelled_on_quickbooks) === 1;

		if (frm.doc.docstatus === 1 && !hasBillId && !hasDebitNoteId) {
			frm.add_custom_button(__("Sync to QuickBooks"), () => {
				frappe.call({
					method: "quickbooks_integration.api.sync_single_purchase_invoice_to_quickbooks",
					args: { purchase_invoice_name: frm.doc.name },
					freeze: true,
					freeze_message: __("Syncing Purchase Invoice to QuickBooks..."),
					callback: (r) => {
						if (!r.exc) {
							frappe.show_alert({
								message: __("Purchase Invoice sync to QuickBooks completed."),
								indicator: "green",
							});
							frm.reload_doc();
						}
					},
				});
			}, __("QuickBooks"));
		}

		if (
			frm.doc.docstatus === 2
			&& (hasBillId || hasDebitNoteId)
			&& !cancelledOnQbo
		) {
			frm.add_custom_button(__("Cancel on QuickBooks"), () => {
				frappe.confirm(
					__("Delete the linked Bill/Vendor Credit in QuickBooks for {0}?", [frm.doc.name]),
					() => {
						frappe.call({
							method: "quickbooks_integration.api.cancel_purchase_invoice_on_quickbooks",
							args: { purchase_invoice_name: frm.doc.name },
							freeze: true,
							freeze_message: __("Cancelling Purchase Invoice on QuickBooks..."),
							callback: (r) => {
								if (!r.exc) {
									frappe.show_alert({
										message: __("Purchase Invoice cancelled on QuickBooks."),
										indicator: "green",
									});
									frm.reload_doc();
								}
							},
						});
					}
				);
			}, __("QuickBooks"));
		}
	},
});
