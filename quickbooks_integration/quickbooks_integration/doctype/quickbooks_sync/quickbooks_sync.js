frappe.ui.form.on("QuickBooks Sync", {

    refresh(frm) {
        frm.get_field("sync_customers").$input.addClass('btn-primary');

    },


    sync_customers(frm) {
        frappe.call({
            method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.start_customer_sync",
            args: {},
            callback: (r) => {
                if (!r.exc) {
                    frappe.msgprint("Customer sync has started in background.");
                }
            }
        });
    },

});
