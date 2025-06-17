import frappe
import requests
from frappe import _
from frappe.utils import now_datetime
from datetime import timedelta

@frappe.whitelist(allow_guest=True)
def oauth_callback():
    code = frappe.form_dict.get("code")
    realm_id = frappe.form_dict.get("realmId")
    state = frappe.form_dict.get("state")

    if not code:
        frappe.throw(_("Authorization code not provided."))
    if not realm_id:
        frappe.throw(_("QuickBooks realm ID (Company ID) not provided."))

    # Ensure the settings doc exists
    if not frappe.db.exists("QuickBooks Settings", None):
        doc = frappe.new_doc("QuickBooks Settings")
        doc.name = "QuickBooks Settings"
        doc.insert(ignore_permissions=True)

    settings = frappe.get_single("QuickBooks Settings")

    token_url = settings.quickbooks_tokenendpoint
    client_id = settings.quickbooks_client_id
    client_secret = settings.quickbooks_client_secret
    redirect_uri = settings.redirect_uri

    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }

    try:
        res = requests.post(
            token_url,
            auth=(client_id, client_secret),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded"
            },
            data=payload,
            timeout=10
        )

        if res.status_code != 200:
            frappe.log_error(res.text, "QuickBooks OAuth Token Error")
            frappe.throw(_("Failed to get access token from QuickBooks."))

        token_data = res.json()

        settings.access_token = token_data.get("access_token")
        settings.refresh_token = token_data.get("refresh_token")
        settings.token_expiry = now_datetime() + timedelta(seconds=token_data.get("expires_in", 3600))
        settings.is_authorized = 1
        settings.save(ignore_permissions=True)
        frappe.db.commit()

        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = "/app/quickbooks-settings"

    except Exception:
        frappe.log_error(frappe.get_traceback(), "QuickBooks OAuth Callback Failed")
        frappe.throw(_("Something went wrong during QuickBooks authorization."))


