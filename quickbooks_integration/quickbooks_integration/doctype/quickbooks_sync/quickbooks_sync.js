
frappe.ui.form.on("QuickBooks Sync", {

    refresh(frm) {
        frm.get_field("sync_customers").$input.addClass('btn-primary');
        frm.get_field("sync_customers_to_quickbooks").$input.addClass('btn-primary');
        frm.get_field("sync_sales_order").$input.addClass('btn-primary');
        frm.get_field("sync_items").$input.addClass('btn-primary');
        frm.get_field("sync_suppliers").$input.addClass('btn-primary');
        frm.get_field("sync_suppliers_to_quickbooks").$input.addClass('btn-primary');
        frm.get_field("sync_sales_invoices").$input.addClass('btn-primary');
        frm.get_field("refresh_sales_invoices").$input.addClass('btn-primary');
        frm.get_field("sync_items_to_quickbooks").$input.addClass('btn-primary');
        frm.get_field("sync_item_images").$input.addClass('btn-primary');
        frm.get_field("sync_purchase_invoices").$input.addClass('btn-primary');
        frm.get_field("refresh_purchase_invoices").$input.addClass('btn-primary');
        frm.get_field("sync_stock").$input.addClass('btn-primary');
        frm.get_field("sync_item_groups").$input.addClass('btn-primary');


       
        
       
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


    refresh_sales_invoices(frm) {
        frappe.call({
            method: "quickbooks_integration.api.refresh_sales_invoice_list",
            args: { docname: frm.doc.name },
            freeze: true,
            freeze_msg: __("Fetching latest Sales Invoices..."),
            callback: function (r) {
                if (!r.exc) {
                    frm.reload_doc();
                    frappe.msgprint("Invoice list refreshed.");
                }
            }
        });
    },

    refresh_purchase_invoices(frm) {
        frappe.call({
            method: "quickbooks_integration.api.refresh_purchase_invoices",
            args: { docname: frm.doc.name },
            freeze: true,
            freeze_msg: __("Fetching latest Sales Invoices..."),
            callback: function (r) {
                if (!r.exc) {
                    frm.reload_doc();
                    frappe.msgprint("Invoice list refreshed.");
                }
            }
        });
    },

    sync_purchase_invoices: (frm) => {
        if (frm.doc.purchase_invoice_list && frm.doc.purchase_invoice_list.length) {
            frappe.call({
                method: "quickbooks_integration.api.bulk_sync_purchase_invoices",  // ✅ full path to API
                args: {
                    docname: frm.doc.name,
                    selected_invoices: frm.doc.purchase_invoice_list.filter(row => row.__checked)
                },
                freeze: true,
                freeze_msg: __("Syncing Purchase Sales Invoices..."),
                callback: (r) => {
                    if (!r.exc) {
                        frm.reload_doc();
                        frappe.msgprint(r.message.message || "Bulk sync complete.");
                    }
                }
            });
        } else {
            frappe.msgprint(__("No Sales Invoices available for sync."));
        }
    },
  
    sync_sales_invoices: (frm) => {
        if (frm.doc.sales_invoice_list && frm.doc.sales_invoice_list.length) {
            frappe.call({
                method: "quickbooks_integration.api.bulk_sync_invoices",  
                args: {
                    docname: frm.doc.name,
                    selected_invoices: frm.doc.sales_invoice_list.filter(row => row.__checked)
                },
                freeze: true,
                freeze_msg: __("Syncing Sales Invoices..."),
                callback: (r) => {
                    if (!r.exc) {
                        frm.reload_doc();
                        frappe.msgprint(r.message.message || "Bulk sync complete.");
                    }
                }
            });
        } else {
            frappe.msgprint(__("No Sales Invoices available for sync."));
        }
    }
    
,    
   
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

    sync_suppliers_to_quickbooks(frm) {
        frappe.call({
            method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.sync_supplier_to_qbo_background",
            args: {},
            callback: (r) => {
                if (!r.exc) {
                    frappe.msgprint("Supplier sync has started in background.");
                }
            }
        });
    },

    // ✅ NEW: sync selected invoices from child table
    sync_selected_sales_invoices(frm) {
        if (frm.doc.sales_invoice_list && frm.doc.sales_invoice_list.length > 0) {
            const selected_invoices = frm.doc.sales_invoice_list.filter((row) => row.__checked);

            if (selected_invoices.length > 0) {
                frappe.call({
                    method: "quickbooks_integration.api.sync_selected_sales_invoices",
                    args: {
                        docname: frm.doc.name,
                        selected_si: selected_invoices.map((row) => row.sales_invoice)
                    },
                    freeze: true,
                    freeze_msg: __("Syncing selected Sales Invoices to QuickBooks..."),
                    callback: (r) => {
                        if (!r.exc) {
                            frappe.msgprint(r.message || "Selected invoices synced successfully.");
                            frm.reload_doc();
                        }
                    }
                });
            } else {
                frappe.msgprint("Please select invoices to sync.", "Warning");
            }
        } else {
            frappe.msgprint("No invoices available in the list.");
        }
    },

    sync_items_to_quickbooks(frm) {
        frappe.call({
            method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.sync_items_to_quickbooks_background",
            args: {},
            callback: (r) => {
                if (!r.exc) {
                    frappe.msgprint("Item sync to quickbooks has started in background.");
                }
            }
        });
    },

    sync_item_images(frm) {
        frappe.call({
            method: "quickbooks_integration.api.start_item_images_sync_background",
            args: {},
            callback: (r) => {
                if (!r.exc) {
                    frappe.msgprint("Item images sync from QuickBooks has started in background.");
                }
            }
        });
    },

    sync_stock(frm) {
        frappe.call({
            method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.start_stock_sync_background",
            args: {},
            callback: (r) => {
                if (!r.exc) {
                    frappe.msgprint("Stock sync and reconciliation from QuickBooks has started in background.");
                }
            }
        });
    },

    sync_item_groups(frm) {
        frappe.call({
            method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.start_item_group_background",
            args: {},
            callback: (r) => {
                if (!r.exc) {
                    frappe.msgprint("Item Group sync from QuickBooks has started in background.");
                }
            }
        });
    },


});
