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

# @frappe.whitelist()
# def sync_customers_to_quickbooks():
#     """Sync unsynced ERPNext customers to QuickBooks, using settings-driven config."""

#     # Load settings
#     settings = frappe.get_doc("QuickBooks Settings")

#     minor_version = settings.minor_version or "75"
#     realm_id = settings.quickbooks_company_id
#     access_token = settings.access_token
#     base_url = settings.base_url.strip()
#     scope = settings.auth_scope or ""

#     # Validate essentials
#     if not realm_id or not access_token:
#         frappe.throw("Missing QuickBooks Company ID or access token.")

#     if "com.intuit.quickbooks.accounting" not in scope.split():
#         frappe.throw("Access token does not include required 'com.intuit.quickbooks.accounting' scope.")

#     # Ensure URL is usable
#     if not base_url.startswith("http"):
#         base_url = "https://" + base_url

#     endpoint = f"{base_url}/v3/company/{realm_id}/customer?minorversion={minor_version}"

#     # Get unsynced customers
#     unsynced_customers = frappe.get_all("Customer",
#         filters={"custom_is_customer_synced": 0},
#         fields=["name", "customer_name", "email_id", "mobile_no", "customer_primary_address"]
#     )

#     if not unsynced_customers:
#         frappe.logger().info("[QuickBooks Sync] No unsynced customers found.")
#         return

#     for cust in unsynced_customers:
#         address = frappe.get_doc("Address", cust["customer_primary_address"]) if cust["customer_primary_address"] else None

#         payload = {
#             "DisplayName": cust["customer_name"],
#             "PrimaryEmailAddr": {"Address": cust["email_id"]} if cust.get("email_id") else None,
#             "PrimaryPhone": {"FreeFormNumber": cust["mobile_no"]} if cust.get("mobile_no") else None,
#             "BillAddr": {
#                 "Line1": address.address_line1 if address else "",
#                 "City": address.city if address else "",
#                 "CountrySubDivisionCode": address.state if address else "",
#                 "PostalCode": address.pincode if address else "",
#                 "Country": address.country if address else ""
#             },
#             "Notes": f"Imported from ERPNext Customer: {cust['name']}"
#         }

#         payload = {k: v for k, v in payload.items() if v}

#         res = requests.post(
#             endpoint,
#             headers={
#                 "Authorization": f"Bearer {access_token}",
#                 "Accept": "application/json",
#                 "Content-Type": "application/json"
#             },
#             json=payload
#         )

#         if res.ok:
#             frappe.db.set_value("Customer", cust["name"], "custom_is_customer_synced", 1)
#             frappe.logger().info(f"[QuickBooks Sync] Synced customer: {cust['customer_name']}")
#     else:
#             error_msg = f"[QuickBooks Sync] Failed for {cust['customer_name']} - Status Code: {res.status_code}\nResponse: {res.text[:1000]}"

#             if res.status_code == 403 and "003100" in res.text:
#                 error_msg += "\nReason: ApplicationAuthorizationFailed (Error 003100) – Likely due to expired or invalid token, incorrect realm ID, or missing authorization."

#             frappe.log_error(
#                 title=f"[QuickBooks Sync] Error syncing {cust['customer_name']}",
#                 message=error_msg
#             )

#     frappe.db.commit()


@frappe.whitelist()
def sync_customers_to_quickbooks():
    """Sync unsynced ERPNext customers to QuickBooks, using settings-driven config."""

    settings = frappe.get_doc("QuickBooks Settings")

    minor_version = settings.minor_version or "75"
    realm_id = settings.quickbooks_company_id
    access_token = settings.access_token
    base_url = settings.base_url.strip()
    scope = settings.auth_scope or ""

    if not realm_id or not access_token:
        frappe.throw("Missing QuickBooks Company ID or access token.")

    if "com.intuit.quickbooks.accounting" not in scope.split():
        frappe.throw("Access token does not include required 'com.intuit.quickbooks.accounting' scope.")

    if not base_url.startswith("http"):
        base_url = "https://" + base_url

    endpoint = f"{base_url}/v3/company/{realm_id}/customer?minorversion={minor_version}"

    unsynced_customers = frappe.get_all("Customer",
        filters={"custom_is_customer_synced": 0},
        fields=["name", "customer_name", "email_id", "mobile_no", "customer_primary_address"]
    )

    if not unsynced_customers:
        frappe.logger().info("[QuickBooks Sync] No unsynced customers found.")
        return

    for cust in unsynced_customers:
        try:
            email = cust["email_id"] or get_primary_contact_email(cust["name"])
            address = None

            if cust["customer_primary_address"]:
                address = frappe.get_doc("Address", cust["customer_primary_address"])
            else:
                address = get_billing_address_for_customer(cust["name"])

            payload = {
                "DisplayName": cust["customer_name"],
                "PrimaryEmailAddr": {"Address": email} if email else None,
                "PrimaryPhone": {"FreeFormNumber": cust["mobile_no"]} if cust.get("mobile_no") else None,
                "BillAddr": {
                    "Line1": address.address_line1 if address else "",
                    "City": address.city if address else "",
                    "CountrySubDivisionCode": address.state if address else "",
                    "PostalCode": address.pincode if address else "",
                    "Country": address.country if address else ""
                } if address else None,
                "Notes": f"Imported from ERPNext Customer: {cust['name']}"
            }

            # Remove None entries
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
                    error_msg += "\nReason: ApplicationAuthorizationFailed (Error 003100)"

                frappe.log_error(
                    title=f"[QuickBooks Sync] Error syncing {cust['customer_name']}",
                    message=error_msg
                )

        except Exception as e:
            frappe.log_error(f"[QuickBooks Sync] Exception for {cust['name']}", str(e))

    frappe.db.commit()


# 🔍 Fetch email from linked Contact
def get_primary_contact_email(customer_name):
    contact_link = frappe.db.get_value("Dynamic Link", {
        "link_doctype": "Customer",
        "link_name": customer_name,
        "parenttype": "Contact"
    }, "parent")

    if contact_link:
        return frappe.db.get_value("Contact", contact_link, "email_id")
    return None


# 📍 Fallback to latest billing address if primary not set
def get_billing_address_for_customer(customer_name):
    addresses = frappe.get_all("Dynamic Link",
        filters={
            "link_doctype": "Customer",
            "link_name": customer_name,
            "parenttype": "Address"
        },
        fields=["parent"]
    )

    for addr in addresses:
        address_doc = frappe.get_doc("Address", addr["parent"])
        if address_doc.address_type == "Billing":
            return address_doc

    return None


@frappe.whitelist()
def start_sales_order_sync():
     if not frappe.has_permission("QuickBooks Sync", "write"):
        frappe.throw("Not permitted")

     frappe.enqueue(
        method=sync_sale_order_to_quickbooks,  # Direct function reference
        queue='long'
    )


import frappe
import requests
from frappe.utils.background_jobs import enqueue
from frappe.utils import now
from frappe import _
import json

@frappe.whitelist()
def start_sales_order_sync():
    if not frappe.has_permission("QuickBooks Sync", "write"):
        frappe.throw(_("Not permitted"))

    frappe.enqueue(
        method=sync_sale_order_to_quickbooks,
        queue='long',
        timeout=600  # Increase if syncing many records
    )


@frappe.whitelist()
def sync_sale_order_to_quickbooks():
    settings = frappe.get_doc("QuickBooks Settings")
    access_token = settings.access_token
    company_id = settings.quickbooks_company_id
    base_url = settings.base_url.strip().rstrip("/")
    minor_version = settings.minor_version

    url = f"{base_url}/v3/company/{company_id}/estimate?$minorversion={minor_version}"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    # Get unsynced Sales Orders
    sales_orders = frappe.get_all(
        "Sales Order",
        filters={ "custom_is_quickbook_synced": 0},
        fields=["name", "customer", "transaction_date", "grand_total"]
    )

    if not sales_orders:
        frappe.logger().info("No new sales orders to sync with QuickBooks.")
        return

    for so in sales_orders:
        try:
            so_doc = frappe.get_doc("Sales Order", so.name)

            qb_payload = {
                "CustomerRef": {
                    "name": so_doc.customer,
                    "value" : 77
                },
               "TxnDate": so_doc.delivery_date.isoformat() if so_doc.delivery_date else None,
                "TotalAmt": float(so_doc.grand_total),
                 "Line": [
                    {
                        "DetailType": "SalesItemLineDetail",
                        "Amount": float(item.amount),
                        "Description": item.custom_item_detail_notes or item.item_name,
                        "SalesItemLineDetail": {
                            "Qty": item.qty,
                            "UnitPrice": float(item.rate),

                            "TaxCodeRef": {
                                "value": "5"
                            }
                        }
                    } for item in so_doc.items
                    ],
                    "TxnTaxDetail": {
                     "TotalTax": 0
                 },
            }

            response = requests.post(url, headers=headers, data=json.dumps(qb_payload))

            if response.status_code == 200:
                # Mark SO as synced
                so_doc.db_set("custom_is_quickbook_synced", 1)
                frappe.logger().info(f"[QuickBooks] Sales Order {so_doc.name} synced successfully.")
            else:
                frappe.log_error(
                    title="QuickBooks Sync Failed",
                    message=f"Sales Order: {so_doc.name}, Status Code: {response.status_code}, Response: {response.text}"
                )

        except Exception as e:
            frappe.log_error(f"Failed to sync SO {so['name']} to QuickBooks: {str(e)}")

