import frappe
import requests
from frappe import _
from frappe.utils import now_datetime
from datetime import timedelta

@frappe.whitelist(allow_guest=True)
def oauth_callback(code=None, state=None):
    if not code:
        frappe.throw(_("Authorization code not provided."))

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

        frappe.log_error(
            title="✅ QuickBooks token data",
            message=frappe.as_json({
                "TOKEN_DATA" : token_data
            })
        )

        settings.access_token = token_data.get("access_token")
        settings.refresh_token = token_data.get("refresh_token")
        settings.token_expiry = now_datetime() + timedelta(seconds=token_data.get("expires_in", 3600))
        settings.is_authorized = 1
        settings.save(ignore_permissions=True)


        frappe.log_error(
            title="✅ Tokens Saved to QuickBooks Settings",
            message=frappe.as_json({
                "access_token": token_data.get("access_token"),
                "refresh_token": token_data.get("refresh_token"),
                "token_expiry": (now_datetime() + timedelta(seconds=token_data.get("expires_in", 3600))).isoformat(),
                "is_authorized": 1
            })
        )


        # Redirect back to UI
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = "/app/quickbooks-settings"

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "QuickBooks OAuth Callback Failed")
        frappe.throw(_("Something went wrong during QuickBooks authorization."))
