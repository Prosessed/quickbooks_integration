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

        frm.add_custom_button("Lookup Item", function () {
            lookup_quickbooks_item(frm);
        });
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

function lookup_quickbooks_item(frm) {
    const prompt = new frappe.ui.Dialog({
        title: __("Lookup QuickBooks Item"),
        fields: [
            {
                fieldname: "item_name",
                fieldtype: "Data",
                label: __("Item Name"),
                reqd: 1,
                description: __("Exact QuickBooks Item Name (e.g. Credit)"),
            },
        ],
        primary_action_label: __("Search"),
        primary_action(values) {
            const item_name = (values.item_name || "").trim();
            if (!item_name) {
                frappe.msgprint(__("Item Name is required."));
                return;
            }

            prompt.hide();
            frappe.call({
                method: "quickbooks_integration.api.lookup_quickbooks_item_by_name",
                args: { item_name },
                freeze: true,
                freeze_message: __("Looking up item in QuickBooks..."),
                callback(r) {
                    const result = r.message || {};
                    const items = result.items || [];
                    if (!items.length) {
                        frappe.msgprint({
                            title: __("No Results"),
                            indicator: "orange",
                            message: __("No QuickBooks Item found for that name."),
                        });
                        return;
                    }
                    show_quickbooks_item_results(items, result.query);
                },
            });
        },
    });
    prompt.show();
}

function show_quickbooks_item_results(items, query) {
    const summary_html = items
        .map((item) => {
            const id = frappe.utils.escape_html(item.Id || "");
            const name = frappe.utils.escape_html(item.Name || "");
            const type = frappe.utils.escape_html(item.Type || "");
            const active = item.Active ? __("Yes") : __("No");
            return `
                <div style="margin-bottom: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--border-color);">
                    <p><strong>${__("Item Code (Id)")}:</strong>
                        <code style="font-size: 1.1em;">${id}</code>
                    </p>
                    <p><strong>${__("Name")}:</strong> ${name}</p>
                    <p><strong>${__("Type")}:</strong> ${type}</p>
                    <p><strong>${__("Active")}:</strong> ${active}</p>
                </div>
            `;
        })
        .join("");

    const json_html = `
        <pre style="max-height: 360px; overflow: auto; white-space: pre-wrap; word-break: break-word; background: var(--control-bg); padding: 12px; border-radius: 6px; font-size: 12px;">${frappe.utils.escape_html(
            JSON.stringify(items, null, 2)
        )}</pre>
    `;

    const dialog = new frappe.ui.Dialog({
        title: __("QuickBooks Item Details"),
        size: "large",
        fields: [
            {
                fieldname: "summary_html",
                fieldtype: "HTML",
                options: summary_html,
            },
            {
                fieldname: "full_json",
                fieldtype: "HTML",
                options: `<p><strong>${__("Full Response")}</strong></p>${json_html}`,
            },
            {
                fieldname: "query_note",
                fieldtype: "HTML",
                options: query
                    ? `<p class="text-muted" style="margin-top: 8px;"><small>${__("Query")}: ${frappe.utils.escape_html(query)}</small></p>`
                    : "",
            },
        ],
        primary_action_label: __("Close"),
        primary_action() {
            dialog.hide();
        },
    });
    dialog.show();
}
