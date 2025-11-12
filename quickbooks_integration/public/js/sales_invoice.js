frappe.ui.form.on("Sales Invoice", {
    refresh: (frm) => {
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
    },
});
