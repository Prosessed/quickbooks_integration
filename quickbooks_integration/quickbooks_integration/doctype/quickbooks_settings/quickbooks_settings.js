// Copyright (c) 2025, Jaspreet Singh Sodhi and contributors
// For license information, please see license.txt

// frappe.ui.form.on("QuickBooks Settings", {
// 	refresh(frm) {

// 	},
// });

frappe.ui.form.on("QuickBooks Settings", {
    refresh: function (frm) {
        frm.add_custom_button("Authorize", function () {
            console.log(frm.doc.client_id);
            console.log(frm.doc.redirect_uri);

            if (!frm.doc.quickbooks_client_id || !frm.doc.redirect_uri) {
                frappe.msgprint("Client ID and Redirect URI are required.");
                return;
            }

            const url = `${frm.doc.authorization_url}?client_id=${frm.doc.quickbooks_client_id}` +
                `&redirect_uri=${encodeURIComponent(frm.doc.redirect_uri)}` +
                `&response_type=code&scope=${frm.doc.auth_scope}&state=${frm.doc.csrf_token}`;

            window.open(url, "_blank");
        });
    }
});
