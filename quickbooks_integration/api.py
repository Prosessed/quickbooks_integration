
import frappe
import requests
from frappe import _

@frappe.whitelist(allow_guest=True)
def oauth_callback(code=None, state=None):
    if not code:
        frappe.throw(_("Authorization code not provided."))

    settings = frappe.get_single("QuickBooks Settings")
    token_url = settings.token_endpoint
    client_id = settings.client_id
    client_secret = settings.client_secret
    redirect_uri = settings.redirect_uri

    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }

    res = requests.post(
        token_url,
        auth=(client_id, client_secret),
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        data=payload
    )

    if res.status_code != 200:
        frappe.throw(_("Failed to get access token from QuickBooks: ") + res.text)

    token_data = res.json()

    settings.db_set("access_token", token_data["access_token"])
    settings.db_set("refresh_token", token_data["refresh_token"])
    settings.db_set("token_expiry", frappe.utils.now_datetime() + frappe.utils.timedelta(seconds=token_data.get("expires_in", 3600)))
    settings.db_set("is_authorized", 1)

    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = "/app/quickbooks-settings"
