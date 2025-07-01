# Copyright (c) 2025, Jaspreet Singh Sodhi and contributors
# For license information, please see license.txt

# import frappe
import json
from frappe.model.document import Document
import requests
import frappe
from frappe.utils import nowdate
from quickbooks_integration.api import refresh_quickbooks_access_token

class QuickBooksSync(Document):
	pass


@frappe.whitelist()
def start_customer_sync():

    refresh_quickbooks_access_token()

    settings = frappe.get_doc("QuickBooks Settings")
    if settings.allow_customer_sync_prosessed != 1:
        frappe.frappe.msgprint('Navigate to Quickbooks Settings & Please enable this option to continue', title="Disabled",
                                indicator="red",
                            )
        return
    if not frappe.has_permission("QuickBooks Sync", "write"):
        frappe.throw("Not permitted")

    frappe.enqueue(
        method=sync_customers_to_quickbooks,
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
        filters={"custom_is_customer_synced": 0, "custom_quickbooks_customer_id": ""},
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
                # Extract QuickBooks ID from the response
                quickbooks_customer_id = res.json().get('Customer', {}).get('Id')

                if quickbooks_customer_id:
                    # Save the QuickBooks ID in the Customer Doctype
                    frappe.db.set_value("Customer", cust["name"], "custom_quickbooks_customer_id", quickbooks_customer_id)
                    frappe.db.set_value("Customer", cust["name"], "custom_is_customer_synced", 1)
                    frappe.logger().info(f"[QuickBooks Sync] Synced customer: {cust['customer_name']}")
                # No error log if `quickbooks_customer_id` is not found
        except Exception as e:
            # No error log or action needed for skipped customers
            pass

    frappe.db.commit()


# @frappe.whitelist()
# def sync_customers_to_quickbooks():
#     """Sync unsynced ERPNext customers to QuickBooks, using settings-driven config."""

#     settings = frappe.get_doc("QuickBooks Settings")

#     minor_version = settings.minor_version or "75"
#     realm_id = settings.quickbooks_company_id
#     access_token = settings.access_token
#     base_url = settings.base_url.strip()
#     scope = settings.auth_scope or ""

#     if not realm_id or not access_token:
#         frappe.throw("Missing QuickBooks Company ID or access token.")

#     if "com.intuit.quickbooks.accounting" not in scope.split():
#         frappe.throw("Access token does not include required 'com.intuit.quickbooks.accounting' scope.")

#     if not base_url.startswith("http"):
#         base_url = "https://" + base_url

#     endpoint = f"{base_url}/v3/company/{realm_id}/customer?minorversion={minor_version}"

#     unsynced_customers = frappe.get_all("Customer",
#         filters={"custom_is_customer_synced": 0},
#         fields=["name", "customer_name", "email_id", "mobile_no", "customer_primary_address"]
#     )

#     if not unsynced_customers:
#         frappe.logger().info("[QuickBooks Sync] No unsynced customers found.")
#         return

#     for cust in unsynced_customers:
#         try:
#             email = cust["email_id"] or get_primary_contact_email(cust["name"])
#             address = None

#             if cust["customer_primary_address"]:
#                 address = frappe.get_doc("Address", cust["customer_primary_address"])
#             else:
#                 address = get_billing_address_for_customer(cust["name"])

#             payload = {
#                 "DisplayName": cust["customer_name"],
#                 "PrimaryEmailAddr": {"Address": email} if email else None,
#                 "PrimaryPhone": {"FreeFormNumber": cust["mobile_no"]} if cust.get("mobile_no") else None,
#                 "BillAddr": {
#                     "Line1": address.address_line1 if address else "",
#                     "City": address.city if address else "",
#                     "CountrySubDivisionCode": address.state if address else "",
#                     "PostalCode": address.pincode if address else "",
#                     "Country": address.country if address else ""
#                 } if address else None,
#                 "Notes": f"Imported from ERPNext Customer: {cust['name']}"
#             }

#             # Remove None entries
#             payload = {k: v for k, v in payload.items() if v}

#             res = requests.post(
#                 endpoint,
#                 headers={
#                     "Authorization": f"Bearer {access_token}",
#                     "Accept": "application/json",
#                     "Content-Type": "application/json"
#                 },
#                 json=payload
#             )

#             if res.ok:
#                 frappe.db.set_value("Customer", cust["name"], "custom_is_customer_synced", 1)
#                 frappe.logger().info(f"[QuickBooks Sync] Synced customer: {cust['customer_name']}")
#             else:
#                 error_msg = f"[QuickBooks Sync] Failed for {cust['customer_name']} - Status Code: {res.status_code}\nResponse: {res.text[:1000]}"
#                 if res.status_code == 403 and "003100" in res.text:
#                     error_msg += "\nReason: ApplicationAuthorizationFailed (Error 003100)"

#                 frappe.log_error(
#                     title=f"[QuickBooks Sync] Error syncing {cust['customer_name']}",
#                     message=error_msg
#                 )

#         except Exception as e:
#             frappe.log_error(f"[QuickBooks Sync] Exception for {cust['name']}", str(e))

#     frappe.db.commit()

def get_primary_contact_email(customer_name):
    contact_link = frappe.db.get_value("Dynamic Link", {
        "link_doctype": "Customer",
        "link_name": customer_name,
        "parenttype": "Contact"
    }, "parent")

    if contact_link:
        return frappe.db.get_value("Contact", contact_link, "email_id")
    return None

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

def sync_invoice_to_quickbooks(doc, method):

    refresh_quickbooks_access_token()

    settings = frappe.get_doc("QuickBooks Settings")
    url = f"{settings.base_url.strip().rstrip('/')}/v3/company/{settings.quickbooks_company_id}/invoice?minorversion={settings.minor_version or '75'}"

    headers = {
        "Authorization": f"Bearer {settings.access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    customer = frappe.get_doc("Customer", doc.customer)
    qb_customer_id = customer.get("custom_quickbooks_customer_id") or "1"
    send_item = (settings.send_item) == 1

    def get_tax_code(item):
        if not item.item_tax_template:
            return "5"
        try:
            return frappe.get_doc("Item Tax Template", item.item_tax_template).custom_quickbooks_gst_id or "5"
        except Exception as e:
            frappe.log_error(f"Error fetching GST from {item.item_tax_template}", str(e))
            return "5"

    line_items = []
    for item in doc.items:
        detail = {
            "Qty": item.qty,
            "UnitPrice": float(item.rate),
            "TaxCodeRef": {"value": get_tax_code(item)}
        }
        if send_item:
            qb_item_id = frappe.db.get_value("Item", item.item_code, "custom_quickbooks_item_id")
            if qb_item_id:
                detail["ItemRef"] = {"value": qb_item_id, "name": item.item_name}

        line_items.append({
            "DetailType": "SalesItemLineDetail",
            "Amount": float(item.amount),
            "Description": item.description or item.item_name,
            "SalesItemLineDetail": detail
        })

    payload = {
        "DocNumber": doc.name,
        "CustomerRef": {"value": qb_customer_id, "name": doc.customer},
        "Line": line_items,
        "ApplyTaxAfterDiscount": False,
        "CustomerMemo": {"value": "Generated from ERPNext"},
        "PrintStatus": "NeedToPrint",
        "EmailStatus": "NotSet"
    }

    try:
        res = requests.post(url, headers=headers, data=json.dumps(payload))
        if res.status_code == 200 and (qbo_id := res.json().get("Invoice", {}).get("Id")):
            doc.db_set("custom_quickbooks_invoice_id", qbo_id)
            frappe.logger().info(f"[QBO] Sales Invoice {doc.name} synced as QBO Invoice {qbo_id}")
        else:
            frappe.log_error("QuickBooks Invoice Sync Failed", f"Sales Invoice: {doc.name}\nStatus: {res.status_code}\nResponse: {res.text}")
    except Exception as e:
        frappe.log_error("QuickBooks Invoice Sync Error", f"Sales Invoice: {doc.name}\nError: {str(e)}")

@frappe.whitelist()
def start_customer_background():
    refresh_quickbooks_access_token()

    settings = frappe.get_doc("QuickBooks Settings")
    if settings.allow_customer_sync_from_quickbooks != 1:
        frappe.frappe.msgprint('Navigate to Quickbooks Settings & Please enable this option to continue', title="Disabled",
                                indicator="red",
                            )
        return
    """Enqueue customer sync job to run in background."""
    frappe.enqueue(sync_customers_from_quickbooks, queue='long', timeout=300)
    frappe.msgprint("Customer sync from QuickBooks has been started in the background.")

@frappe.whitelist()
def sync_customers_from_quickbooks():
    """Pull all customers from QuickBooks and sync into ERPNext with pagination."""
    frappe.logger().info("[QB SYNC] Started customer sync job")

    settings = frappe.get_single("QuickBooks Settings")
    ACCESS_TOKEN = settings.access_token
    REALM_ID = settings.quickbooks_company_id
    url = settings.base_url.replace("https://", "").strip("/")
    BASE_URL = f"https://{url}/v3/company"

    max_results = 100
    start_position = 1

    HEADERS = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    while True:
        query = f"SELECT * FROM Customer STARTPOSITION {start_position} MAXRESULTS {max_results}"
        QUERY_URL = f"{BASE_URL}/{REALM_ID}/query?query={query.replace(' ', '%20')}&minorversion=75"

        try:
            response = requests.get(QUERY_URL, headers=HEADERS)
            response.raise_for_status()
            data = response.json()

            customers = data.get("QueryResponse", {}).get("Customer", [])
            if not customers:
                break  # No more customers

            for qb_customer in customers:
                create_or_update_customer(qb_customer)

            frappe.logger().info(f"[QB SYNC] Fetched {len(customers)} customers from position {start_position}")

            if len(customers) < max_results:
                break  # Last page

            start_position += max_results

        except Exception as e:
            frappe.log_error(message=str(e), title="QuickBooks Customer Sync Failed")
            break

    frappe.logger().info("[QB SYNC] Completed customer sync job")

def create_or_update_customer(qb_customer):
    """Create or update customer in ERPNext based on QuickBooks customer data."""

    qb_id = qb_customer.get("Id")
    if not qb_id:
        frappe.logger().error("[QB SYNC] Missing Customer ID in QuickBooks data.")
        return

    display_name = qb_customer.get("DisplayName") or "Unknown Customer"
    company_name = qb_customer.get("CompanyName") or display_name

    existing = frappe.db.exists("Customer", {"custom_quickbooks_customer_id": qb_id})
    if existing:
        customer = frappe.get_doc("Customer", existing)
        frappe.logger().info(f"[QB SYNC] Updating customer: {display_name} (QB ID: {qb_id})")
    else:
        customer = frappe.new_doc("Customer")
        frappe.logger().info(f"[QB SYNC] Creating new customer: {display_name} (QB ID: {qb_id})")

    customer.customer_name = display_name
    customer.customer_type = "Company" if qb_customer.get("CompanyName") else "Individual"
    customer.custom_quickbooks_customer_id = qb_id
    customer.customer_group = "All Customer Groups"
    customer.territory = "All Territories"

    customer.save(ignore_permissions=True)

    map_customer_address(customer.name, qb_customer)
    map_customer_contact(customer.name, qb_customer)

def map_customer_address(customer_name, qb_customer):
    """Create or update billing and shipping addresses with proper customer linking."""

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
    }

    for addr_field, addr_type in address_fields:
        addr_data = qb_customer.get(addr_field)
        if not addr_data:
            continue

        address_name = f"{customer_name} - {addr_type}"

        address_lines = [addr_data.get(f"Line{i}") for i in range(1, 4) if addr_data.get(f"Line{i}")]
        address_line = "\n".join(address_lines).strip() or "Unknown"

        city = addr_data.get("City", "").strip() or "Unknown"
        state = addr_data.get("CountrySubDivisionCode", "").strip() or "Unknown"
        postal_code = addr_data.get("PostalCode", "").strip() or "Unknown"

        raw_country = addr_data.get("Country", "")
        country = country_map.get(raw_country, raw_country or "")
        if not country:
            country = "Australia" if state == "NSW" else "United States"

        if not frappe.db.exists("Country", country):
            frappe.logger().warn(f"[Address Mapping] Unknown country '{country}' for {address_name}. Defaulting to 'Australia'")
            country = "Australia"

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

        # Remove old links if any
        address.links = []
        address.append("links", {
            "link_doctype": "Customer",
            "link_name": customer_name
        })

        address.save(ignore_permissions=True)

def map_customer_contact(customer_name, qb_customer):
    """Create or update contact person linked to customer with proper customer linking."""

    phone = qb_customer.get("PrimaryPhone", {}).get("FreeFormNumber", "") or "Unknown"
    first_name = qb_customer.get("GivenName", "") or "Unknown"
    last_name = qb_customer.get("FamilyName", "") or "Unknown"
    email = qb_customer.get("PrimaryEmailAddr", {}).get("Address", "")

    existing_contacts = frappe.get_all("Contact", filters={
        "first_name": first_name,
        "last_name": last_name,
        "phone": phone
    }, fields=["name"])

    contact = None
    for contact_entry in existing_contacts:
        doc = frappe.get_doc("Contact", contact_entry.name)
        if any(link.link_doctype == "Customer" and link.link_name == customer_name for link in doc.links):
            contact = doc
            break

    if not contact:
        contact = frappe.new_doc("Contact")

    contact.first_name = first_name
    contact.last_name = last_name
    contact.phone = phone
    if email:
        contact.email_id = email

    # Remove old links if any
    contact.links = []
    contact.append("links", {
        "link_doctype": "Customer",
        "link_name": customer_name
    })

    contact.save(ignore_permissions=True)

@frappe.whitelist()
def start_item_background():
    refresh_quickbooks_access_token()

    """Enqueue item sync job to run in background."""

    settings = frappe.get_doc("QuickBooks Settings")
    if settings.allow_item_sync_from_quickbooks != 1:
        frappe.frappe.msgprint('Navigate to Quickbooks Settings & Please enable Item sync to continue', title="QuickBooks Item Sync Disabled",
                                indicator="red",
                            )
        return

    frappe.enqueue(item_sync, queue='long', timeout=300)
    frappe.msgprint("Item sync from QuickBooks has been started in the background.")

@frappe.whitelist()
def item_sync():
    """Sync items from QuickBooks to ERPNext with pagination support."""
    settings = frappe.get_doc("QuickBooks Settings")
    access_token = settings.access_token
    company_id = settings.quickbooks_company_id
    base_url = settings.base_url.strip().rstrip("/")
    minor_version = settings.minor_version or "75"

    start_position = 1
    max_results = 100  # QuickBooks allows max 100 per page

    while True:
        query = f"SELECT * FROM Item STARTPOSITION {start_position} MAXRESULTS {max_results}"
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
            if not items:
                break  # No more items to fetch

            for qb_item in items:
                create_or_update_item(qb_item)

            frappe.logger().info(f"[QB SYNC] Fetched {len(items)} items starting from {start_position}.")

            if len(items) < max_results:
                break  # Last page reached

            start_position += max_results

        except Exception as e:
            frappe.log_error(message=str(e), title="QuickBooks Item Sync Failed")
            break

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

@frappe.whitelist()
def sync_supplier_background():
    """Trigger background supplier sync."""
    refresh_quickbooks_access_token()

    settings = frappe.get_doc("QuickBooks Settings")
    if settings.allow_supplier_sync_from_quickbooks != 1:
        frappe.msgprint(
            'Navigate to Quickbooks Settings & Please enable Supplier sync to continue',
            title="QuickBooks Supplier Sync Disabled",
            indicator="red",
        )
        return

    frappe.enqueue(sync_suppliers_from_quickbooks, queue='long', timeout=300)
    frappe.msgprint("Supplier sync from QuickBooks has been started in the background.")

def sync_suppliers_from_quickbooks():
    """Pull suppliers from QuickBooks and sync into ERPNext with pagination."""
    frappe.logger().info("[QB SYNC] Started supplier sync job")

    settings = frappe.get_single("QuickBooks Settings")
    ACCESS_TOKEN = settings.access_token
    REALM_ID = settings.quickbooks_company_id
    minor_version = settings.minor_version or "75"
    url = settings.base_url.replace("https://", "").strip("/")
    BASE_URL = f"https://{url}/v3/company"

    max_results = 100
    start_position = 1

    HEADERS = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Accept": "application/json"
    }

    while True:
        query = f"SELECT * FROM Vendor STARTPOSITION {start_position} MAXRESULTS {max_results}"
        request_url = f"{BASE_URL}/{REALM_ID}/query?query={query.replace(' ', '%20')}&minorversion={minor_version}"

        try:
            response = requests.get(request_url, headers=HEADERS)
            response.raise_for_status()

            data = response.json()
            suppliers = data.get("QueryResponse", {}).get("Vendor", [])

            if not suppliers:
                break  # No more suppliers

            for qb_supplier in suppliers:
                create_or_update_supplier(qb_supplier)

            frappe.logger().info(f"[QB SYNC] Fetched {len(suppliers)} suppliers from position {start_position}")

            if len(suppliers) < max_results:
                break  # Last page

            start_position += max_results

        except Exception as e:
            frappe.log_error(message=str(e), title="QuickBooks Supplier Sync Failed")
            break

    frappe.logger().info("[QB SYNC] Completed supplier sync job")

def create_or_update_supplier(qb_supplier):
    """Create or update supplier in ERPNext based on QuickBooks supplier data."""

    qb_id = qb_supplier.get("Id")
    if not qb_id:
        frappe.logger().error("[QB SYNC] Missing Supplier ID in QuickBooks data.")
        return

    display_name = qb_supplier.get("DisplayName") or "Unknown Supplier"
    company_name = qb_supplier.get("CompanyName") or display_name

    existing = frappe.db.exists("Supplier", {"custom_quickbooks_supplier_id": qb_id})
    if existing:
        supplier = frappe.get_doc("Supplier", existing)
        frappe.logger().info(f"[QB SYNC] Updating supplier: {display_name} (QB ID: {qb_id})")
    else:
        supplier = frappe.new_doc("Supplier")
        frappe.logger().info(f"[QB SYNC] Creating new supplier: {display_name} (QB ID: {qb_id})")

    supplier.supplier_name = display_name
    supplier.supplier_type = "Company" if qb_supplier.get("CompanyName") else "Individual"
    supplier.custom_quickbooks_supplier_id = qb_id
    supplier.supplier_group = "All Supplier Groups"
    supplier.territory = "All Territories"

    supplier.save(ignore_permissions=True)

def sync_purchase_invoice_to_quickbooks(doc, method):
    refresh_quickbooks_access_token()

    """Hook function to sync Purchase Invoice to QuickBooks on submit."""
    sync_single_purchase_invoice_to_quickbooks(doc.name)

@frappe.whitelist()
def sync_single_purchase_invoice_to_quickbooks(purchase_invoice_name):
    """Sync a specific Purchase Invoice to QuickBooks as a Purchase Order on Submit (clean version)."""

    invoice = frappe.get_doc("Purchase Invoice", purchase_invoice_name)

    if invoice.get("custom_quickbooks_bill_id"):
        frappe.msgprint(f"Purchase Invoice {purchase_invoice_name} is already synced with QuickBooks.")
        return

    settings = frappe.get_single("QuickBooks Settings")
    access_token = settings.access_token
    realm_id = settings.quickbooks_company_id
    minor_version = settings.minor_version or "75"
    base_url = f"https://{settings.base_url.replace('https://', '').strip('/')}/v3/company/{realm_id}"

    vendor_qb_id = frappe.db.get_value("Supplier", invoice.supplier, "custom_quickbooks_supplier_id")
    if not vendor_qb_id:
        frappe.throw(f"QuickBooks Vendor ID not found for Supplier: {invoice.supplier}.")

    line_items = []

    for item in invoice.items:
        item_qb_id = frappe.db.get_value("Item", item.item_code, "custom_quickbooks_item_id")
        if not item_qb_id:
            frappe.throw(f"QuickBooks Item ID not found for Item: {item.item_code}.")

        line_items.append({
            "DetailType": "ItemBasedExpenseLineDetail",
            "Amount": float(item.amount),
            "ItemBasedExpenseLineDetail": {
                "ItemRef": {"value": item_qb_id},
                "Qty": float(item.qty),
                "UnitPrice": float(item.rate)
            }
        })

    payload = {
        "VendorRef": {"value": vendor_qb_id},
        "TxnDate": str(invoice.posting_date),
        "Line": line_items
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    po_url = f"{base_url}/purchaseorder?minorversion={minor_version}"

    try:
        response = requests.post(po_url, headers=headers, json=payload)
        response_json = response.json()
    except Exception as e:
        frappe.throw(f"QuickBooks sync failed due to a request error: {str(e)}")

    if response.status_code == 200 and "PurchaseOrder" in response_json:
        qb_po_id = response_json["PurchaseOrder"]["Id"]
        frappe.db.set_value("Purchase Invoice", invoice.name, "custom_quickbooks_bill_id", qb_po_id)
        frappe.db.commit()
        frappe.msgprint(f"Purchase Invoice {invoice.name} synced as Purchase Order in QuickBooks. ID: {qb_po_id}")
    else:
        frappe.throw(f"QuickBooks sync failed. Response: {response.text}")
