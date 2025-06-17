# Copyright (c) 2025, Jaspreet Singh Sodhi and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document
import requests
import frappe

class QuickBooksSync(Document):
	pass


@frappe.whitelist()
def start_customer_sync():
    if not frappe.has_permission("QuickBooks Sync", "write"):
        frappe.throw("Not permitted")

    frappe.enqueue(
        method=sync_customers_to_quickbooks,  # Direct function reference
        queue='long'
    )
from frappe.utils import now_datetime
import requests

@frappe.whitelist()
def sync_customers_to_quickbooks():
    """Sync unsynced ERPNext customers to QuickBooks, using settings-driven config."""

    # Load settings
    settings = frappe.get_doc("QuickBooks Settings")

    minor_version = settings.minor_version or "75"
    realm_id = settings.quickbooks_company_id
    access_token = settings.access_token
    base_url = settings.base_url.strip()
    scope = settings.auth_scope or ""

    # Validate essentials
    if not realm_id or not access_token:
        frappe.throw("Missing QuickBooks Company ID or access token.")

    if "com.intuit.quickbooks.accounting" not in scope.split():
        frappe.throw("Access token does not include required 'com.intuit.quickbooks.accounting' scope.")

    # Ensure URL is usable
    if not base_url.startswith("http"):
        base_url = "https://" + base_url

    endpoint = f"{base_url}/v3/company/{realm_id}/customer?minorversion={minor_version}"

    # Get unsynced customers
    unsynced_customers = frappe.get_all("Customer",
        filters={"custom_is_customer_synced": 0},
        fields=["name", "customer_name", "email_id", "mobile_no", "customer_primary_address"]
    )

    if not unsynced_customers:
        frappe.logger().info("[QuickBooks Sync] No unsynced customers found.")
        return

    for cust in unsynced_customers:
        address = frappe.get_doc("Address", cust["customer_primary_address"]) if cust["customer_primary_address"] else None

        payload = {
            "DisplayName": cust["customer_name"],
            "PrimaryEmailAddr": {"Address": cust["email_id"]} if cust.get("email_id") else None,
            "PrimaryPhone": {"FreeFormNumber": cust["mobile_no"]} if cust.get("mobile_no") else None,
            "BillAddr": {
                "Line1": address.address_line1 if address else "",
                "City": address.city if address else "",
                "CountrySubDivisionCode": address.state if address else "",
                "PostalCode": address.pincode if address else "",
                "Country": address.country if address else ""
            },
            "Notes": f"Imported from ERPNext Customer: {cust['name']}"
        }

        payload = {k: v for k, v in payload.items() if v}

        res = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json"
            },
            json=payload
        )

        if res.ok:
            frappe.db.set_value("Customer", cust["name"], "custom_is_customer_synced", 1)
            frappe.logger().info(f"[QuickBooks Sync] Synced customer: {cust['customer_name']}")
    else:
            error_msg = f"[QuickBooks Sync] Failed for {cust['customer_name']} - Status Code: {res.status_code}\nResponse: {res.text[:1000]}"

            if res.status_code == 403 and "003100" in res.text:
                error_msg += "\nReason: ApplicationAuthorizationFailed (Error 003100) – Likely due to expired or invalid token, incorrect realm ID, or missing authorization."

            frappe.log_error(
                title=f"[QuickBooks Sync] Error syncing {cust['customer_name']}",
                message=error_msg
            )

    frappe.db.commit()
