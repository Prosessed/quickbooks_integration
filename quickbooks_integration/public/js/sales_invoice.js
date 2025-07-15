frappe.ui.form.on("Sales Invoice",{
    refresh : (frm) => {
        if (frm.doc.docstatus == 1) {
            frm.add_custom_button(
                __("QBO Sales Invoice"),
                () => {
                    frappe.call({
                        method : 'quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.sync_invoice_to_quickbooks',
                        args : {
                            doc : frm.doc
                        },
                        callback : (r) => {
                            if (!r.exec) {
                                console.log(r.message);
                            }
                        }
                    })
                },__("Create"))
        }

    },

})
