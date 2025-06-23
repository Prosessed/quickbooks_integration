frappe.ui.form.on("QuickBooks Sync", {

    refresh(frm) {
        frm.get_field("sync_customers").$input.addClass('btn-primary');
        frm.get_field("sync_sales_order").$input.addClass('btn-primary');
        frm.get_field("sync_items").$input.addClass('btn-primary');

    },


    sync_customers(frm) {
        frappe.call({
            method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.start_customer_background",
            args: {},
            callback: (r) => {
                if (!r.exc) {
                    frappe.msgprint("Customer sync has started in background.");
                }
            }
        });
    },

    sync_items(frm) {
        frappe.call({
            method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.start_item_background",
            args: {},
            callback: (r) => {
                if (!r.exc) {
                    frappe.msgprint("Item sync has started in background.");
                }
            }
        });
    },

    // sync_sales_order(frm) {
    //     frappe.call({
    //         method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.start_sales_order_sync",
    //         args: {},
    //         callback: (r) => {
    //             if (!r.exc) {
    //                 frappe.msgprint("Order sync has started in background.");
    //             }
    //         }
    //     });
    // },


});
