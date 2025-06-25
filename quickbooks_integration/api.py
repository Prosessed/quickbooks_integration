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



@frappe.whitelist()
def refresh_quickbooks_access_token():
    """
    Refresh the QuickBooks access token using the refresh token.

    Returns:
        str: New access token if successful.

    Raises:
        frappe.ValidationError: If the refresh token is not set or if the request fails.
    """

    settings = frappe.get_single("QuickBooks Settings")
    if not settings.refresh_token:
        frappe.throw(_("Refresh token is not set. Please re-authorize QuickBooks."))

    token_url = settings.quickbooks_tokenendpoint
    client_id = settings.quickbooks_client_id
    client_secret = settings.quickbooks_client_secret

    payload = {
        "grant_type": "refresh_token",
        "refresh_token": settings.refresh_token,
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
            frappe.log_error(res.text, "QuickBooks Refresh Token Error")
            frappe.throw(_("Failed to refresh access token from QuickBooks."))

        token_data = res.json()
        settings.access_token = token_data.get("access_token")
        settings.refresh_token = token_data.get("refresh_token")
        settings.token_expiry = now_datetime() + timedelta(seconds=token_data.get("expires_in", 3600))
        settings.save(ignore_permissions=True)
        frappe.db.commit()

        return settings.access_token

    except Exception:
        frappe.log_error(frappe.get_traceback(), "QuickBooks Access Token Refresh Failed")
        frappe.throw(_("Something went wrong while refreshing QuickBooks access token."))



def sync_sales_invoice_cancellation(doc, method):
    """
    Called via Frappe doc_events on Sales Invoice cancel.
    """

    qb_invoice_id = doc.get("custom_quickbooks_invoice_id")

    if not qb_invoice_id:
        frappe.msgprint(f"No QuickBooks Invoice ID found for {doc.name}. Skipping QuickBooks cancellation.")
        return

    cancel_quickbooks_invoice(qb_invoice_id)
    frappe.msgprint(f"Sales Invoice {doc.name} successfully voided in QuickBooks.")


@frappe.whitelist()
def get_quickbooks_invoice_sync_token(invoice_id):
    """
    Fetch the latest SyncToken for a QuickBooks Invoice by its ID.

    Args:
        invoice_id (str): The QuickBooks Invoice ID.

    Returns:
        str: SyncToken of the Invoice.

    Raises:
        frappe.ValidationError: If fetching the Invoice or SyncToken fails.
    """


    if not invoice_id:
        frappe.throw("QuickBooks Invoice ID is required.")

    settings = frappe.get_single("QuickBooks Settings")
    access_token = settings.access_token
    realm_id = settings.quickbooks_company_id
    minor_version = settings.minor_version or "75"
    base_url = f"https://{settings.base_url.replace('https://', '').strip('/')}/v3/company/{realm_id}"

    url = f"{base_url}/invoice/{invoice_id}?minorversion={minor_version}"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        frappe.throw(f"Failed to fetch Invoice from QuickBooks. Error: {str(e)}")

    invoice_data = data.get("Invoice")
    if not invoice_data:
        frappe.throw(f"No Invoice data found for ID {invoice_id} in QuickBooks.")

    sync_token = invoice_data.get("SyncToken")
    if sync_token is None:
        frappe.throw(f"SyncToken not found for QuickBooks Invoice ID {invoice_id}.")

    return sync_token


@frappe.whitelist()
def cancel_quickbooks_invoice(invoice_id):
    refresh_quickbooks_access_token()

    if not invoice_id:
        frappe.throw("QuickBooks Invoice ID is required.")

    settings = frappe.get_single("QuickBooks Settings")
    access_token = settings.access_token
    realm_id = settings.quickbooks_company_id

    base_url = settings.base_url.rstrip("/")  # Clean trailing slash if any
    url = f"{base_url}/v3/company/{realm_id}/invoice/?operation=void"

    sync_token = get_quickbooks_invoice_sync_token(invoice_id)
    frappe.logger().info(f"[QB] SyncToken for Invoice {invoice_id}: {sync_token}")
    frappe.logger().info(f"[QB] Void Invoice Request URL: {url}")

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    payload = {
        "Id": invoice_id,
        "SyncToken": sync_token
    }

    frappe.logger().info(f"[QB] Void Invoice Request Payload: {payload}")

    try:
        response = requests.post(url, headers=headers, json=payload)
        frappe.logger().info(f"[QB] Void Invoice Response: {response.text}")
        response.raise_for_status()
    except requests.RequestException as e:
        frappe.throw(f"Failed to cancel Invoice in QuickBooks. Error: {str(e)}")

    return _("Invoice {0} has been successfully cancelled in QuickBooks.").format(invoice_id)
