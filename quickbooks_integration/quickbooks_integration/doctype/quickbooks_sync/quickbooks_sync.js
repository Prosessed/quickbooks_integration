frappe.ui.form.on("QuickBooks Sync", {
    refresh(frm) {
        [
            "sync_customers",
            "sync_customers_to_quickbooks",
            "sync_sales_order",
            "sync_items",
            "sync_suppliers",
            "sync_suppliers_to_quickbooks",
            "sync_sales_invoices",
            "refresh_sales_invoices",
            "sync_items_to_quickbooks",
            "sync_item_images",
            "sync_purchase_invoices",
            "refresh_purchase_invoices",
            "sync_stock",
            "sync_item_groups",
            "refresh_items",
            "refresh_suppliers",
            "refresh_customers",
            "refresh_sales_orders"
        ].forEach(field => {
            if (frm.get_field(field)) {
                frm.get_field(field).$input.addClass("btn-primary");
            }
        });

        refresh_all_counts(frm);
    },

    /* ---------------- Customers ---------------- */

    sync_customers(frm) {
        frappe.call({
            method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.start_customer_background",
            callback: () => {
                frappe.msgprint("Customer sync from QuickBooks to Prosessed started.");
                setTimeout(() => refresh_all_counts(frm), 2000);
            }
        });
    },

    sync_customers_to_quickbooks(frm) {
        const selected = (frm.doc.customer_list || []).filter(r => r.__checked && !r.is_synced);
        
        if (selected.length === 0) {
            // If no selection, sync all unsynced customers (backward compatibility)
            frappe.call({
                method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.start_customer_sync",
                callback: () => {
                    frappe.msgprint("Customer sync from Prosessed to QuickBooks started.");
                    setTimeout(() => refresh_all_counts(frm), 2000);
                }
            });
            return;
        }
        
        frappe.call({
            method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.bulk_sync_customers",
            args: { docname: frm.doc.name, selected_customers: selected },
            freeze: true,
            freeze_msg: __("Syncing Customers..."),
            callback: (r) => {
                if (r.message && r.message.message) {
                    frappe.msgprint(r.message.message);
                }
                refresh_customers(frm, false);
            }
        });
    },

    refresh_customers(frm, show_message = true) {
        frappe.call({
            method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.refresh_customer_list",
            args: { docname: frm.doc.name },
            freeze: true,
            freeze_msg: __("Fetching Customers..."),
            callback: () => {
                setTimeout(() => {
                    frm.reload_doc().then(() => {
                        if (show_message) {
                            frappe.msgprint("Customers refreshed.");
                        }
                        setTimeout(() => refresh_all_counts(frm), 1000);
                    }).catch(() => {
                        frm.refresh_field("customer_list");
                        if (show_message) {
                            frappe.msgprint("Customers refreshed.");
                        }
                    });
                }, 100);
            }
        });
    },

    /* ---------------- Items ---------------- */

    sync_items(frm) {
        frappe.call({
            method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.start_item_background",
            callback: () => {
                frappe.msgprint("Item sync started.");
                setTimeout(() => refresh_all_counts(frm), 2000);
            }
        });
    },

    sync_items_to_quickbooks(frm) {
        const selected = (frm.doc.item_list || []).filter(r => r.__checked && !r.is_synced);
        
        if (selected.length === 0) {
            // If no selection, sync all unsynced items (backward compatibility)
            frappe.call({
                method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.sync_items_to_quickbooks_background",
                callback: () => {
                    frappe.msgprint("Item sync to QuickBooks started.");
                    setTimeout(() => refresh_all_counts(frm), 2000);
                }
            });
            return;
        }
        
        frappe.call({
            method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.bulk_sync_items",
            args: { docname: frm.doc.name, selected_items: selected },
            freeze: true,
            freeze_msg: __("Syncing Items..."),
            callback: (r) => {
                if (r.message && r.message.message) {
                    frappe.msgprint(r.message.message);
                }
                // Refresh the list to update sync status
                refresh_items(frm, false);
            }
        });
    },

    refresh_items(frm, show_message = true) {
        frappe.call({
            method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.refresh_item_list",
            args: { docname: frm.doc.name },
            freeze: true,
            freeze_msg: __("Fetching Items..."),
            callback: () => {
                // Reload document to get latest version
                // Use setTimeout to ensure backend save is complete
                setTimeout(() => {
                    frm.reload_doc().then(() => {
                        if (show_message) {
                            frappe.msgprint("Items refreshed.");
                        }
                        setTimeout(() => refresh_all_counts(frm), 1000);
                    }).catch(() => {
                        // If reload fails due to timestamp mismatch, refresh field instead
                        frm.refresh_field("item_list");
                        if (show_message) {
                            frappe.msgprint("Items refreshed.");
                        }
                    });
                }, 100);
            }
        });
    },

    /* ---------------- Suppliers ---------------- */

    sync_suppliers(frm) {
        frappe.call({
            method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.sync_supplier_background",
            callback: () => {
                frappe.msgprint("Supplier sync started.");
                setTimeout(() => refresh_all_counts(frm), 2000);
            }
        });
    },

    sync_suppliers_to_quickbooks(frm) {
        const selected = (frm.doc.supplier_list || []).filter(r => r.__checked && !r.is_synced);
        
        if (selected.length === 0) {
            // If no selection, sync all unsynced suppliers (backward compatibility)
            frappe.call({
                method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.sync_supplier_to_qbo_background",
                callback: () => {
                    frappe.msgprint("Supplier sync to QuickBooks started.");
                    setTimeout(() => refresh_all_counts(frm), 2000);
                }
            });
            return;
        }
        
        frappe.call({
            method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.bulk_sync_suppliers",
            args: { docname: frm.doc.name, selected_suppliers: selected },
            freeze: true,
            freeze_msg: __("Syncing Suppliers..."),
            callback: (r) => {
                if (r.message && r.message.message) {
                    frappe.msgprint(r.message.message);
                }
                refresh_suppliers(frm, false);
            }
        });
    },

    refresh_suppliers(frm, show_message = true) {
        frappe.call({
            method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.refresh_supplier_list",
            args: { docname: frm.doc.name },
            freeze: true,
            freeze_msg: __("Fetching Suppliers..."),
            callback: () => {
                setTimeout(() => {
                    frm.reload_doc().then(() => {
                        if (show_message) {
                            frappe.msgprint("Suppliers refreshed.");
                        }
                        setTimeout(() => refresh_all_counts(frm), 1000);
                    }).catch(() => {
                        frm.refresh_field("supplier_list");
                        if (show_message) {
                            frappe.msgprint("Suppliers refreshed.");
                        }
                    });
                }, 100);
            }
        });
    },

    /* ---------------- Item Images ---------------- */

    sync_item_images(frm) {
        frappe.call({
            method: "quickbooks_integration.api.start_item_images_sync_background",
            callback: () => {
                frappe.msgprint("Item images sync started.");
                setTimeout(() => refresh_all_counts(frm), 2000);
            }
        });
    },

    /* ---------------- Stock ---------------- */

    sync_stock(frm) {
        frappe.call({
            method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.start_stock_sync_background",
            callback: () => {
                frappe.msgprint("Stock sync started.");
                setTimeout(() => refresh_all_counts(frm), 2000);
            }
        });
    },
    stock_sync_interval(frm) {
        // Get the value directly from the field input element to ensure we have the selected value
        const field = frm.fields_dict.stock_sync_interval;
        if (!field) return;
        
        // Get value from the input element directly
        const interval = field.get_value();
        if (!interval) return;
        
        // Store the interval value to use after reload
        const selectedInterval = interval;
        
        frappe.call({
            method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.update_stock_sync_cron_job",
            args: {
                docname: frm.doc.name,
                interval: selectedInterval
            },
            freeze: true,
            freeze_message: __("Updating stock sync schedule...")
        })
        .then(r => {
            if (r?.message?.message) {
                frappe.show_alert(
                    {
                        message: r.message.message,
                        indicator: "green"
                    },
                    5
                );
            }
    
            // Reload the document to get the saved value from backend
            frm.reload_doc().then(() => {
                // Ensure the field shows the correct saved value
                frm.set_value("stock_sync_interval", selectedInterval);
                frm.refresh_field("stock_sync_interval");
            });
        })
        .catch((error) => {
            frappe.show_alert(
                {
                    message: __("Failed to update stock sync schedule."),
                    indicator: "red"
                },
                5
            );
            // Reload to restore previous value on error
            frm.reload_doc();
        });
    },
    /* ---------------- Sales Orders ---------------- */

    sync_sales_order(frm) {
        const selected = (frm.doc.sales_order_list || []).filter(r => r.__checked && !r.is_synced);
        
        if (selected.length === 0) {
            frappe.msgprint("Please select unsynced sales orders to sync.");
            return;
        }
        
        frappe.call({
            method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.bulk_sync_sales_orders",
            args: { docname: frm.doc.name, selected_sales_orders: selected },
            freeze: true,
            freeze_msg: __("Syncing Sales Orders..."),
            callback: (r) => {
                if (r.message && r.message.message) {
                    frappe.msgprint(r.message.message);
                }
                refresh_sales_orders(frm, false);
            }
        });
    },

    refresh_sales_orders(frm, show_message = true) {
        frappe.call({
            method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.refresh_sales_order_list",
            args: { docname: frm.doc.name },
            freeze: true,
            freeze_msg: __("Fetching Sales Orders..."),
            callback: () => {
                setTimeout(() => {
                    frm.reload_doc().then(() => {
                        if (show_message) {
                            frappe.msgprint("Sales orders refreshed.");
                        }
                        setTimeout(() => refresh_all_counts(frm), 1000);
                    }).catch(() => {
                        frm.refresh_field("sales_order_list");
                        if (show_message) {
                            frappe.msgprint("Sales orders refreshed.");
                        }
                    });
                }, 100);
            }
        });
    },

    /* ---------------- Sales Invoices ---------------- */

    refresh_sales_invoices(frm, show_message = true) {
        frappe.call({
            method: "quickbooks_integration.api.refresh_sales_invoice_list",
            args: { docname: frm.doc.name },
            freeze: true,
            freeze_msg: __("Fetching Sales Invoices..."),
            callback: () => {
                setTimeout(() => {
                    frm.reload_doc().then(() => {
                        if (show_message) {
                            frappe.msgprint("Sales invoices refreshed.");
                        }
                        setTimeout(() => refresh_all_counts(frm), 1000);
                    }).catch(() => {
                        frm.refresh_field("sales_invoice_list");
                        if (show_message) {
                            frappe.msgprint("Sales invoices refreshed.");
                        }
                    });
                }, 100);
            }
        });
    },

    sync_sales_invoices(frm) {
        const selected = (frm.doc.sales_invoice_list || []).filter(r => r.__checked);

        if (!selected.length) {
            frappe.msgprint("Please select sales invoices.");
            return;
        }

        frappe.call({
            method: "quickbooks_integration.api.bulk_sync_invoices",
            args: { docname: frm.doc.name, selected_invoices: selected },
            freeze: true,
            freeze_msg: __("Syncing Sales Invoices..."),
            callback: (r) => {
                if (r.message && r.message.message) {
                    frappe.msgprint(r.message.message);
                }
                refresh_sales_invoices(frm, false);
            }
        });
    },

    /* ---------------- Purchase Invoices ---------------- */

    refresh_purchase_invoices(frm, show_message = true) {
        frappe.call({
            method: "quickbooks_integration.api.refresh_purchase_invoices",
            args: { docname: frm.doc.name },
            freeze: true,
            freeze_msg: __("Fetching Purchase Invoices..."),
            callback: () => {
                setTimeout(() => {
                    frm.reload_doc().then(() => {
                        if (show_message) {
                            frappe.msgprint("Purchase invoices refreshed.");
                        }
                        setTimeout(() => refresh_all_counts(frm), 1000);
                    }).catch(() => {
                        frm.refresh_field("purchase_invoice_list");
                        if (show_message) {
                            frappe.msgprint("Purchase invoices refreshed.");
                        }
                    });
                }, 100);
            }
        });
    },

    sync_purchase_invoices(frm) {
        const selected = (frm.doc.purchase_invoice_list || []).filter(r => r.__checked);

        if (!selected.length) {
            frappe.msgprint("Please select purchase invoices.");
            return;
        }

        frappe.call({
            method: "quickbooks_integration.api.bulk_sync_purchase_invoices",
            args: { docname: frm.doc.name, selected_invoices: selected },
            freeze: true,
            freeze_msg: __("Syncing Purchase Invoices..."),
            callback: (r) => {
                if (r.message && r.message.message) {
                    frappe.msgprint(r.message.message);
                }
                refresh_purchase_invoices(frm, false);
            }
        });
    },

    /* ---------------- Item Groups ---------------- */

    sync_item_groups(frm) {
        frappe.call({
            method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.start_item_group_background",
            callback: () => {
                frappe.msgprint("Item Group sync started.");
                setTimeout(() => refresh_all_counts(frm), 2000);
            }
        });
    }
});


/* ---------------- Counts ---------------- */

const refresh_all_counts = (frm) => {
    frappe.call({
        method: "quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync.refresh_all_counts",
        args: { docname: frm.doc.name },
        callback: (r) => {
            if (!r.message) return;

            Object.keys(r.message).forEach(field => {
                if (frm.fields_dict[field]) {
                    frm.set_value(field, r.message[field]);
                }
            });
        }
    });
};
