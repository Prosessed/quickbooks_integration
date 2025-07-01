frappe.ui.form.on("QuickBooks Sync", {

    refresh(frm) {
        frm.get_field("sync_customers").$input.addClass('btn-primary');
        frm.get_field("sync_customers_to_quickbooks").$input.addClass('btn-primary');
        frm.get_field("sync_sales_order").$input.addClass('btn-primary');
        frm.get_field("sync_items").$input.addClass('btn-primary');
        frm.get_field("sync_suppliers").$input.addClass('btn-primary');
        frm.get_field("sync_sales_invoices").$input.addClass('btn-primary');

    },


    sync_customers_to_quickbooks(frm) {
        frappe.call({
            method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.start_customer_sync",
            args: {},
            callback: (r) => {
                if (!r.exc) {
                    frappe.msgprint("Customer sync from prosessed to quickbooks has started in background.");
                }
            }
        });
    },

    sync_customers(frm) {
        frappe.call({
            method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.start_customer_background",
            args: {},
            callback: (r) => {
                if (!r.exc) {
                    frappe.msgprint("Customer sync from quickbooks to prosessed has started in background.");
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

    sync_suppliers(frm) {
        frappe.call({
            method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.sync_supplier_background",
            args: {},
            callback: (r) => {
                if (!r.exc) {
                    frappe.msgprint("Supplier sync has started in background.");
                }
            }
        });
    },
    sync_selected_sales_invoices: (frm) => {
        if (frm.doc.si_count) {
            const selected_invoices = frm.doc.sales_invoice_list.filter((e) => e.__checked);

            if (selected_invoices.length > 0) {
                frm.call({
                    doc: cur_frm.doc,
                    args: {
                        selected_si: selected_invoices.map((e) => e.name),
                    },
                    method: 'quickbooks_integration.api.sync_selected_sales_invoices',
                    freeze: true,
                    freeze_msg: __("Syncing selected Sales Invoices to QuickBooks..."),
                    callback: (r) => {
                        if (!r.exc) {
                            frappe.msgprint(r.message);
                        } else {
                            frappe.msgprint(r.message);
                        }
                    }
                });
            } else {
                frappe.msgprint("Please select invoices to sync.", "Warning", "orange");
            }
        }
    }




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
