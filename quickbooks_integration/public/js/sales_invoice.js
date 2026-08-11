frappe.ui.form.on("Sales Invoice", {
	refresh: (frm) => {
		if (frm.is_new()) {
			return;
		}

		const hasInvoiceId = Boolean(frm.doc.custom_quickbooks_invoice_id);
		const hasCreditMemoId = Boolean(frm.doc.custom_quickbooks_credit_memo_id);
		const cancelledOnQbo = cint(frm.doc.custom_is_cancelled_on_quickbooks) === 1;

		if (frm.doc.docstatus === 1) {
			if (frm.doc.is_return) {
				frm.page.add_action_item(__("QBO Credit Note"), () => {
					frappe.call({
						method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.sync_credit_memo_to_quickbooks",
						args: { docname: frm.doc.name },
						callback: (r) => {
							if (!r.exc) {
								frappe.msgprint("Credit Note pushed to QuickBooks.");
							}
						}
					});
				});
			} else {
				frm.page.add_action_item(__("QBO Sales Invoice"), () => {
					frappe.call({
						method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.sync_invoice_to_quickbooks",
						args: { docname: frm.doc.name },
						callback: (r) => {
							if (!r.exc) {
								frappe.msgprint("Invoice pushed to QuickBooks.");
							}
						}
					});
				});
			}
		}

		if (
			frm.doc.docstatus === 2
			&& (hasInvoiceId || hasCreditMemoId)
			&& !cancelledOnQbo
		) {
			frm.add_custom_button(__("Cancel on QuickBooks"), () => {
				frappe.confirm(
					__("Void/delete the linked Invoice/Credit Memo in QuickBooks for {0}?", [frm.doc.name]),
					() => {
						frappe.call({
							method: "quickbooks_integration.api.cancel_sales_invoice_on_quickbooks",
							args: { sales_invoice_name: frm.doc.name },
							freeze: true,
							freeze_message: __("Cancelling Sales Invoice on QuickBooks..."),
							callback: (r) => {
								if (!r.exc) {
									frappe.show_alert({
										message: __("Sales Invoice cancelled on QuickBooks."),
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
