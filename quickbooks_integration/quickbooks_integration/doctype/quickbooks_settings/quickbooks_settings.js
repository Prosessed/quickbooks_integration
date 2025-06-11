frappe.ui.form.on("QuickBooks Settings", {
    refresh: function (frm) {
        // Add the custom "Authorize" button on form refresh
        frm.add_custom_button("Authorize", function () {
            if (frm.is_dirty()) {
                frm.save().then(() => {
                    launch_authorization_url(frm);
                });
            } else {
                launch_authorization_url(frm);
            }
        });

        // 🔍 Debug log current route
        console.log("Frappe Route:", frappe.get_route());

        try {
            // ✅ Only redirect if route has 'undefined'
            if (frappe.get_route()[1] === "undefined") {
                frappe.set_route("quickbooks-settings");
            }
        } catch (err) {
            frappe.log_error(err.stack, "QuickBooks JS Error - Route Handling");
        }
    }
});

function launch_authorization_url(frm) {
    const client_id = frm.doc.quickbooks_client_id;
    const redirect_uri = frm.doc.redirect_uri;
    const auth_scope = frm.doc.auth_scope || "com.intuit.quickbooks.accounting";
    const csrf_token = frm.doc.csrf_token || "secure_default_token";
    const authorization_url = frm.doc.quickbooks_authorization_url;

    if (!client_id || !redirect_uri || !authorization_url) {
        frappe.msgprint("Client ID, Redirect URI, and Authorization URL are required.");
        return;
    }

    const url = `${authorization_url}?client_id=${client_id}` +
        `&redirect_uri=${encodeURIComponent(redirect_uri)}` +
        `&response_type=code&scope=${auth_scope}&state=${csrf_token}`;

    console.log("Opening QuickBooks OAuth URL:", url);
    window.open(url, "_blank");
}
