# Copyright (c) 2025, Jaspreet Singh Sodhi and contributors
# For license information, please see license.txt

# import frappe
import json
from frappe.model.document import Document
import requests
import frappe
from frappe.utils import nowdate

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


# @frappe.whitelist()
# def start_sales_order_sync():
#      if not frappe.has_permission("QuickBooks Sync", "write"):
#         frappe.throw("Not permitted")

#      frappe.enqueue(
#         method=sync_sale_order_to_quickbooks,  # Direct function reference
#         queue='long'
#     )



def sync_invoice_to_quickbooks(doc, method):
    settings = frappe.get_doc("QuickBooks Settings")
    access_token = settings.access_token
    company_id = settings.quickbooks_company_id
    base_url = settings.base_url.strip().rstrip("/")
    minor_version = settings.minor_version or "75"

    url = f"{base_url}/v3/company/{company_id}/invoice?minorversion={minor_version}"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    customer_doc = frappe.get_doc("Customer", doc.customer)
    qb_customer_id = customer_doc.get("custom_quickbooks_customer_id") or "1"


    line_items = []
    FALLBACK_GST_CODE = "5"
    for idx, item in enumerate(doc.items, start=1):

        tax_code = FALLBACK_GST_CODE

        # Get GST code from item's Item Tax Template
        if item.item_tax_template:
            try:
                tax_template = frappe.get_doc("Item Tax Template", item.item_tax_template)
                if tax_template.custom_quickbooks_gst_id:
                    tax_code = tax_template.custom_quickbooks_gst_id
            except Exception as e:
                frappe.log_error(f"Error fetching GST code from tax template {item.item_tax_template}", str(e))

        line_items.append({
            "DetailType": "SalesItemLineDetail",
            "Amount": float(item.amount),
            "Description": item.description or item.item_name,
            "SalesItemLineDetail": {
                "Qty": item.qty,
                "UnitPrice": float(item.rate),
                "TaxCodeRef": {
                   "value": tax_code
                }
            }
        })

    payload = {
        "DocNumber": doc.name,
        "CustomerRef": {
            "value": qb_customer_id,
            "name": doc.customer
        },
        "Line": line_items,
        "ApplyTaxAfterDiscount": False,
        "CustomerMemo": {
            "value": "Generated from ERPNext"
        },
        "PrintStatus": "NeedToPrint",
        "EmailStatus": "NotSet"
    }

    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))

        if response.status_code == 200:
            qbo_invoice_id = response.json().get("Invoice", {}).get("Id")
            if qbo_invoice_id:
                doc.db_set("custom_quickbooks_invoice_id", qbo_invoice_id)
                frappe.logger().info(f"[QBO] Sales Invoice {doc.name} synced as QBO Invoice {qbo_invoice_id}")
            else:
                frappe.log_error("QuickBooks Invoice Sync - Missing ID", json.dumps(response.json(), indent=2))
        else:
            frappe.log_error(
                title="QuickBooks Invoice Sync Failed",
                message=f"Sales Invoice: {doc.name}\nStatus: {response.status_code}\nResponse: {response.text}"
            )
    except Exception as e:
        frappe.log_error(
            title="QuickBooks Invoice Sync Error",
            message=f"Sales Invoice: {doc.name}\nError: {str(e)}"
        )

@frappe.whitelist()
def start_customer_background():
    """Enqueue customer sync job to run in background."""
    frappe.enqueue(sync_customers_from_quickbooks, queue='long', timeout=300)
    frappe.msgprint("Customer sync from QuickBooks has been started in the background.")

def sync_customers_from_quickbooks():
    """Pull customers from QuickBooks and sync into ERPNext."""
    frappe.logger().info("[QB SYNC] Started customer sync job")  # ADD THIS

    settings = frappe.get_single("QuickBooks Settings")
    ACCESS_TOKEN = settings.access_token
    REALM_ID = settings.quickbooks_company_id
    url = settings.base_url.replace("https://", "").strip("/")  # strip protocol if included
    BASE_URL = f"https://{url}/v3/company"

    # QUERY_URL = f"{BASE_URL}/{REALM_ID}/query?minorversion=75"
    QUERY_URL = f"{BASE_URL}/{REALM_ID}/query?query=SELECT%20*%20FROM%20Customer&minorversion=75"


    HEADERS = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    try:
        response = requests.get(
            QUERY_URL,
            headers=HEADERS
        )
        response.raise_for_status()
        data = response.json()

        customers = data.get("QueryResponse", {}).get("Customer", [])
        for qb_customer in customers:
            create_or_update_customer(qb_customer)  # This should be defined elsewhere

        frappe.logger().info("QuickBooks Customer Sync Completed.")
    except Exception as e:
        frappe.log_error(message=str(e), title="QuickBooks Customer Sync Failed")

def create_or_update_customer(qb_customer):
    """Create or update customer in ERPNext based on QuickBooks customer data."""

    qb_id = qb_customer.get("Id")
    display_name = qb_customer.get("DisplayName") or "Unnamed Customer"
    company_name = qb_customer.get("CompanyName") or display_name

    if not qb_id:
        frappe.logger().error("[QB SYNC] Missing Customer ID in QuickBooks data.")
        return

    # Check if customer already exists by QuickBooks ID
    existing = frappe.db.exists("Customer", {"custom_quickbooks_customer_id": qb_id})
    if existing:
        customer = frappe.get_doc("Customer", existing)
        frappe.logger().info(f"[QB SYNC] Updating existing customer: {display_name} (QB ID: {qb_id})")
    else:
        customer = frappe.new_doc("Customer")
        frappe.logger().info(f"[QB SYNC] Creating new customer: {display_name} (QB ID: {qb_id})")

    customer.customer_name = display_name
    customer.customer_type = "Company" if qb_customer.get("CompanyName") else "Individual"
    customer.custom_quickbooks_customer_id = qb_id
    customer.customer_group = "All Customer Groups"  # Customize if needed
    customer.territory = "All Territories"           # Customize if needed

    customer.save(ignore_permissions=True)

    # Map Address (ignore BillAddr.Id, only used internally by QuickBooks)
    map_customer_address(customer.name, qb_customer)

    # Map Contact (phone/email if available)
    map_customer_contact(customer.name, qb_customer)

def map_customer_address(customer_name, qb_customer):
    """Create billing and shipping address records."""
    address_fields = [
        ("BillAddr", "Billing"),
        ("ShipAddr", "Shipping")
    ]

    country_map = {
        "US": "United States",
        "USA": "United States",
        "AU": "Australia",
        "AUS": "Australia",
        "CA": "Canada",
        "GB": "United Kingdom",
        "IN": "India"
        # Add more if needed
    }

    for addr_field, addr_type in address_fields:
        addr_data = qb_customer.get(addr_field)
        if not addr_data:
            continue

        address_name = f"{customer_name} - {addr_type}"
        address_line = addr_data.get("Line1")
        city = addr_data.get("City")
        state = addr_data.get("CountrySubDivisionCode")
        postal_code = addr_data.get("PostalCode")

        # Resolve country safely
        raw_country = addr_data.get("Country")
        country = country_map.get(raw_country, raw_country)
        if not country:
            country = "Australia" if state == "NSW" else "United States"

        # Validate country exists in ERPNext
        if not frappe.db.exists("Country", country):
            frappe.logger().warn(f"[Address Mapping] Unknown country '{country}' for {address_name}. Defaulting to 'Australia'")
            country = "Australia"

        # Check if address already exists
        existing = frappe.db.exists("Address", {"address_title": address_name, "address_type": addr_type})
        if existing:
            address = frappe.get_doc("Address", existing)
        else:
            address = frappe.new_doc("Address")

        address.address_title = address_name
        address.address_type = addr_type
        address.address_line1 = address_line
        address.city = city
        address.state = state
        address.pincode = postal_code
        address.country = country
        address.customer = customer_name

        address.save(ignore_permissions=True)

def map_customer_contact(customer_name, qb_customer):
    """Create contact person linked to customer."""
    phone = qb_customer.get("PrimaryPhone", {}).get("FreeFormNumber")
    first_name = qb_customer.get("GivenName", "")
    last_name = qb_customer.get("FamilyName", "")
    full_name = f"{first_name} {last_name}".strip()
    email = qb_customer.get("PrimaryEmailAddr", {}).get("Address")

    if not phone and not full_name:
        return  # No contact info to save

    # Try to find an existing contact with same phone and name
    existing_contacts = frappe.get_all("Contact", filters={
        "first_name": first_name,
        "last_name": last_name,
        "phone": phone
    }, fields=["name"])

    contact = None
    for contact_entry in existing_contacts:
        doc = frappe.get_doc("Contact", contact_entry.name)
        # Check if the link exists
        for link in doc.links:
            if link.link_doctype == "Customer" and link.link_name == customer_name:
                contact = doc
                break

    if not contact:
        contact = frappe.new_doc("Contact")

    contact.first_name = first_name
    contact.last_name = last_name
    contact.phone = phone
    if email:
        contact.email_id = email

    # Prevent duplicate link entry
    if not any(link.link_doctype == "Customer" and link.link_name == customer_name for link in contact.links):
        contact.append("links", {
            "link_doctype": "Customer",
            "link_name": customer_name
        })

    contact.save(ignore_permissions=True)



@frappe.whitelist()
def start_item_background():
    """Enqueue item sync job to run in background."""

    settings = frappe.get_doc("QuickBooks Settings")
    if settings.allow_item_sync_from_quickbooks != 1:
        frappe.frappe.msgprint('Message', title="QuickBooks Item Sync Disabled",
                                indicator="red",
                            )

    frappe.enqueue(item_sync, queue='long', timeout=300)
    frappe.msgprint("Item sync from QuickBooks has been started in the background.")

@frappe.whitelist()
def item_sync():

    """Sync items from QuickBooks to ERPNext."""
    settings = frappe.get_doc("QuickBooks Settings")
    access_token = settings.access_token
    company_id = settings.quickbooks_company_id
    base_url = settings.base_url.strip().rstrip("/")
    minor_version = settings.minor_version or "75"

    query = "SELECT * FROM Item"
    url = f"{base_url}/v3/company/{company_id}/query?query={query.replace(' ', '%20')}&minorversion={minor_version}"


    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }


    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()

        items = data.get("QueryResponse", {}).get("Item", [])
        for qb_item in items:
            create_or_update_item(qb_item)

        frappe.logger().info("[QB SYNC] Item sync completed successfully.")
    except Exception as e:
        frappe.log_error(message=str(e), title="QuickBooks Item Sync Failed")


def create_or_update_item(qb_item):

    """Create or update item in ERPNext based on QuickBooks item data."""

    qb_id = qb_item.get("Id")
    name = qb_item.get("FullyQualifiedName") or "Unnamed Item"
    item_type = qb_item.get("Type", "Inventory")

    existing = frappe.db.exists("Item", {"custom_quickbooks_item_id": qb_id})
    if existing:
        item = frappe.get_doc("Item", existing)
    else:
        item = frappe.new_doc("Item")


    if qb_item.get("SalesTaxCodeRef") is not None:
        tax_code = qb_item["SalesTaxCodeRef"].get("value")
        if tax_code:
            tax_template = frappe.get_all("Item Tax Template", filters={"custom_quickbooks_gst_id": tax_code}, limit=1)
            if tax_template:

                item.append("taxes",{
                    "item_tax_template": tax_template[0].name,
                });
            else:
                frappe.logger().warn(f"[Item Sync] No Item Tax Template found for QuickBooks GST Code: {tax_code}")

    item.item_name = name
    item.item_code = qb_item.get("Name")
    item.item_group = "All Item Groups"
    item.custom_quickbooks_item_id = qb_id
    item.item_type = item_type
    item.description = qb_item.get("Description", "")
    item.stock_uom = "Unit"
    item.default_unit_of_measure = "Unit"

    if "UnitPrice" in qb_item:
        item.standard_rate = float(qb_item["UnitPrice"])

    item.save(ignore_permissions=True)

    frappe.db.commit()
