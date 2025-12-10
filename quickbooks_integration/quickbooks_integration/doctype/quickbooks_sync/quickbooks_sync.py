# Copyright (c) 2025, Jaspreet Singh Sodhi and contributors
# For license information, please see license.txt

# import frappe
import json
import re
from frappe.model.document import Document
import requests
import frappe
from frappe.utils import nowdate
from quickbooks_integration.api import refresh_quickbooks_access_token, sync_credit_memo_to_quickbooks, sync_selected_sales_invoices,sync_single_purchase_invoice_to_quickbooks
from frappe import _
class QuickBooksSync(Document):
	pass



@frappe.whitelist()
def start_customer_sync():

    refresh_quickbooks_access_token()

    settings = frappe.get_doc("QuickBooks Settings")
    if settings.allow_customer_sync_prosessed != 1 or not settings.enable:
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


@frappe.whitelist(allow_guest=True)
def handle_invoice_save(doc, method):
    """Decide whether to sync Sales Invoice or Credit Note to QuickBooks"""

    frappe.log_error("Here docname is ", doc.name)

    if doc.is_return:
        start_customer_sync()
        enqueue_sync_credit_note_to_quickbooks(doc, method)

    else:
        # Otherwise, sync the regular sales invoice
        start_customer_sync()
        enqueue_sync_invoice_to_quickbooks(doc ,method)




@frappe.whitelist(allow_guest=True)
def enqueue_sync_invoice_to_quickbooks(doc, method):

    settings = frappe.get_doc("QuickBooks Settings")

    if not settings.enable:
        frappe.frappe.msgprint('Please Enable Quickbooks Integration')
        return


    """Enqueue the sync invoice job to QuickBooks"""
    frappe.msgprint("Invoice synchronization with QuickBooks has started.", indicator="green")
    frappe.enqueue(sync_invoice_to_quickbooks, queue='long', docname=doc.name)

@frappe.whitelist(allow_guest=True)
def enqueue_sync_credit_note_to_quickbooks(doc, method):
    """Enqueue the sync invoice job to QuickBooks"""
    frappe.log_error("Came here" , doc.name)
    frappe.msgprint("Credit Note synchronization with QuickBooks has started.", indicator="green")
    frappe.enqueue(sync_credit_memo_to_quickbooks, queue='long', docname=doc.name)

@frappe.whitelist()
def sync_invoice_to_quickbooks(docname=None):
    doc = frappe.get_doc("Sales Invoice", docname)
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
        "TxnDate": str(doc.posting_date),
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

    frappe.db.commit()



@frappe.whitelist()
def start_customer_background():
    refresh_quickbooks_access_token()



    settings = frappe.get_doc("QuickBooks Settings")
    if settings.allow_customer_sync_from_quickbooks != 1 or not settings.enable:
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

    does_address_exist = frappe.db.exists("Address", {"address_title": f"{display_name} - Billing", "address_type": "Billing"})
    does_contact_exist = frappe.db.exists("Contact", {"first_name": display_name})

    if not does_address_exist:
        try:
            map_customer_address(customer.name, qb_customer)
        except Exception:
            frappe.log_error(
                message=frappe.get_traceback(),
                title=f"QuickBooks Customer Address Sync Failed (QB ID: {qb_id})"
            )
            frappe.logger().error(f"[QB SYNC] Address sync failed for customer {display_name} (QB ID: {qb_id})")

    if not does_contact_exist:
        try:
            map_customer_contact(customer.name, qb_customer)
        except Exception:
            frappe.log_error(
                message=frappe.get_traceback(),
                title=f"QuickBooks Customer Contact Sync Failed (QB ID: {qb_id})"
            )
            frappe.logger().error(f"[QB SYNC] Contact sync failed for customer {display_name} (QB ID: {qb_id})")


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

        # Check if the customer is already linked to the address
        link_exists = any(link.link_name == customer_name and link.link_doctype == "Customer" for link in address.links)

        if not link_exists:
            # Only append the link if it's not already present
            address.append("links", {
                "link_doctype": "Customer",
                "link_name": customer_name
            })

        address.save(ignore_permissions=True)



def sanitize_phone_number(raw_phone: str) -> str:
    """Normalize QuickBooks phone numbers while keeping sync resilient."""
    if not raw_phone:
        return ""

    value = str(raw_phone).strip()
    if not value:
        return ""

    has_plus_prefix = value.startswith("+")
    digits_only = re.sub(r"[^\d]", "", value)

    if not digits_only or len(digits_only) < 6:
        return ""

    return f"+{digits_only}" if has_plus_prefix else digits_only


def map_customer_contact(customer_name, qb_customer):
    """Create or update contact person linked to customer with proper customer linking."""
    phone = qb_customer.get("PrimaryPhone", {}).get("FreeFormNumber", "") or ""
    email = qb_customer.get("PrimaryEmailAddr", {}).get("Address", "")
    first_name = customer_name

    existing = frappe.db.exists("Contact", {"first_name": customer_name})
    if existing:
        contact = frappe.get_doc("Contact", existing)
    else:
        contact = frappe.new_doc("Contact")


    contact.first_name = first_name

    sanitized_phone = sanitize_phone_number(phone)
    if phone and not sanitized_phone:
        frappe.logger().warning(f"[QB SYNC] Skipping invalid phone number '{phone}' for customer {customer_name}")

    if email:
        email_exists = any(email_row.email_id == email for email_row in contact.email_ids)
        if not email_exists:
            contact.append("email_ids", {
                "email_id": email,
                "is_primary": 1
            })

    if sanitized_phone:
        phone_exists = any(phone_row.phone == sanitized_phone for phone_row in contact.phone_nos)
        if not phone_exists:
            contact.append("phone_nos", {
                "phone": sanitized_phone,
                "is_primary_mobile_no": 1
            })

    link_exists = any(
        link.link_doctype == "Customer" and link.link_name == customer_name
        for link in contact.links
    )

    if not link_exists:
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
    if settings.allow_item_sync_from_quickbooks != 1 and not settings.enable:
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


@frappe.whitelist()
def create_or_update_item(qb_item):
    """Create or update item in ERPNext based on QuickBooks item data."""

    qb_id = qb_item.get("Id")
    item_name = qb_item.get("Description", "Unnamed Item")
    item_type = qb_item.get("Type", "Inventory")

    # Check if the item already exists in ERPNext
    existing = frappe.db.exists("Item", {"custom_quickbooks_item_id": qb_id})
    if existing:
        item = frappe.get_doc("Item", existing)
    else:
        item = frappe.new_doc("Item")

    # Check if SalesTaxCodeRef exists and add tax template
    if qb_item.get("SalesTaxCodeRef") is not None:
        tax_code = qb_item["SalesTaxCodeRef"].get("value")
        if tax_code:
            # Fetch the tax template based on QuickBooks GST Code
            tax_template = frappe.get_all("Item Tax Template", filters={"custom_quickbooks_gst_id": tax_code}, limit=1)

            if tax_template:
                # Check if the tax template is already attached to the item
                existing_tax = frappe.get_all("Item Tax Template", filters={"custom_quickbooks_gst_id": tax_code}, limit=1)

                if not existing_tax:  # Only add if the tax template is not already linked
                    item.append("taxes", {
                        "item_tax_template": tax_template[0].name,
                    })
                else:
                    frappe.logger().info(f"[Item Sync] Tax Template {tax_template[0].name} already exists for Item {item_name}, skipping duplicate.")
            else:
                frappe.logger().warn(f"[Item Sync] No Item Tax Template found for QuickBooks GST Code: {tax_code}")

    # Set other item properties
    item.item_name = qb_item.get("Description", "")
    item.custom_quickbooks_item_id = qb_id
    item.item_type = item_type
    item.description = qb_item.get("Description", "")
    item.stock_uom = "Unit"
    item.default_unit_of_measure = "Unit"

    if "UnitPrice" in qb_item:
        item.standard_rate = float(qb_item["UnitPrice"])

    # Map category from QBO to item group in ERPNext
    # Check if the item has a ParentRef (category) from QuickBooks
    parent_ref = qb_item.get("ParentRef")
    category_mapped = False
    
    if parent_ref:
        parent_qb_id = parent_ref.get("value")
        if parent_qb_id:
            # Find the Item Group in ERPNext that matches the QuickBooks category ID
            item_group_name = frappe.db.get_value("Item Group", {"custom_quickbooks_item_group_id": parent_qb_id}, "name")
            if item_group_name:
                item.item_group = item_group_name
                category_mapped = True
                frappe.logger().info(f"[Item Sync] Mapped QBO category {parent_qb_id} to Item Group '{item_group_name}' for item '{item_name}'")
            else:
                # Category not found in ERPNext
                frappe.logger().warn(f"[Item Sync] QBO category {parent_qb_id} not found in ERPNext Item Groups. Using default for item '{item_name}'")
    
    # If no category was mapped, use default item group for new items
    if not category_mapped and not existing:
        item.item_group = "All Item Groups"

    try:
        # Save item and commit
        item.save(ignore_permissions=True)
        frappe.db.commit()
        frappe.logger().info(f"[Item Sync] Item '{item_name}' (QB ID: {qb_id}) synced successfully.")
    except Exception as e:
        # Log error if saving the item fails, include item name in the log
        error_message = f"Failed to sync item '{item_name}' (QB ID: {qb_id}) due to error: {str(e)}"
        frappe.log_error(message=error_message, title=f"Failed to sync item {qb_id}")

@frappe.whitelist()
def start_item_group_background():
    """Enqueue item group sync job to run in background."""
    refresh_quickbooks_access_token()

    settings = frappe.get_doc("QuickBooks Settings")
    if settings.allow_item_sync_from_quickbooks != 1 and not settings.enable:
        frappe.frappe.msgprint('Navigate to Quickbooks Settings & Please enable Item sync to continue', title="QuickBooks Item Group Sync Disabled",
                                indicator="red",
                            )
        return

    frappe.enqueue(sync_item_groups_from_quickbooks, queue='long', timeout=300)
    frappe.msgprint("Item Group sync from QuickBooks has been started in the background.")

@frappe.whitelist()
def sync_item_groups_from_quickbooks():
    """Sync item groups from QuickBooks to ERPNext with pagination support."""
    frappe.logger().info("[QB SYNC] Started item group sync job")
    
    settings = frappe.get_doc("QuickBooks Settings")
    access_token = settings.access_token
    company_id = settings.quickbooks_company_id
    base_url = settings.base_url.strip().rstrip("/")
    minor_version = settings.minor_version or "75"

    if not access_token or not company_id:
        frappe.log_error("Missing QuickBooks access token or company ID", "QuickBooks Item Group Sync Failed")
        return

    start_position = 1
    max_results = 100  # QuickBooks allows max 100 per page

    while True:
        # Query Item Groups (Items with Type="Category") from QuickBooks
        query = f"SELECT * FROM Item WHERE Type='Category' STARTPOSITION {start_position} MAXRESULTS {max_results}"
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

            item_groups = data.get("QueryResponse", {}).get("Item", [])
            if not item_groups:
                break  # No more item groups to fetch

            for qb_item_group in item_groups:
                create_or_update_item_group(qb_item_group)

            frappe.logger().info(f"[QB SYNC] Fetched {len(item_groups)} item groups starting from {start_position}.")

            if len(item_groups) < max_results:
                break  # Last page reached

            start_position += max_results

        except Exception as e:
            frappe.log_error(message=str(e), title="QuickBooks Item Group Sync Failed")
            break

    frappe.logger().info("[QB SYNC] Completed item group sync job")
    frappe.db.commit()

@frappe.whitelist()
def create_or_update_item_group(qb_item_group):
    """Create or update item group in ERPNext based on QuickBooks item group data."""
    
    qb_id = qb_item_group.get("Id")
    if not qb_id:
        frappe.logger().error("[QB SYNC] Missing Item Group ID in QuickBooks data.")
        return

    group_name = qb_item_group.get("Name") or qb_item_group.get("Description") or "Unnamed Item Group"
    
    # Check if the item group already exists in ERPNext by QuickBooks ID
    existing = frappe.db.exists("Item Group", {"custom_quickbooks_item_group_id": qb_id})
    
    if existing:
        item_group = frappe.get_doc("Item Group", existing)
        frappe.logger().info(f"[QB SYNC] Updating item group: {group_name} (QB ID: {qb_id})")
    else:
        item_group = frappe.new_doc("Item Group")
        frappe.logger().info(f"[QB SYNC] Creating new item group: {group_name} (QB ID: {qb_id})")

    # Set item group properties
    item_group.item_group_name = group_name
    item_group.custom_quickbooks_item_group_id = qb_id
    item_group.is_group = 1
    
    # Handle parent item group if exists
    parent_ref = qb_item_group.get("ParentRef")
    if parent_ref:
        parent_qb_id = parent_ref.get("value")
        if parent_qb_id:
            # Find parent item group in ERPNext by QuickBooks ID
            parent_item_group = frappe.db.get_value("Item Group", {"custom_quickbooks_item_group_id": parent_qb_id}, "name")
            if parent_item_group:
                item_group.parent_item_group = parent_item_group
            else:
                # Parent doesn't exist yet, will be set on next sync
                frappe.logger().warn(f"[QB SYNC] Parent item group with QB ID {parent_qb_id} not found. Will be set on next sync.")

    # Set default parent if not set
    if not item_group.parent_item_group:
        item_group.parent_item_group = "All Item Groups"

    try:
        # Save item group and commit
        item_group.save(ignore_permissions=True)
        frappe.logger().info(f"[Item Group Sync] Item Group '{group_name}' (QB ID: {qb_id}) synced successfully.")
    except Exception as e:
        # Log error if saving the item group fails
        error_message = f"Failed to sync item group '{group_name}' (QB ID: {qb_id}) due to error: {str(e)}"
        frappe.log_error(message=error_message, title=f"Failed to sync item group {qb_id}")

@frappe.whitelist()
def sync_supplier_background():
    """Trigger background supplier sync."""
    refresh_quickbooks_access_token()

    settings = frappe.get_doc("QuickBooks Settings")
    if settings.allow_supplier_sync_from_quickbooks != 1 and not settings.enable:
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

@frappe.whitelist()
def sync_supplier_to_qbo_background():
    """Trigger background supplier sync."""
    refresh_quickbooks_access_token()

    settings = frappe.get_doc("QuickBooks Settings")
    if settings.allow_supplier_sync_to_quickbooks != 1 and not settings.enable:
        frappe.msgprint(
            'Navigate to Quickbooks Settings & Please enable Supplier sync to continue',
            title="QuickBooks Supplier Sync Disabled",
            indicator="red",
        )
        return

    frappe.enqueue(sync_suppliers_to_quickbooks, queue='long', timeout=300)
    frappe.msgprint("Supplier sync from QuickBooks has been started in the background.")


@frappe.whitelist()
def sync_suppliers_to_quickbooks():
    """
    Create vendors in QuickBooks for ERPNext Suppliers
    that do not yet have a custom_quickbooks_supplier_id.
    """
    try:
        settings = frappe.get_single("QuickBooks Settings")
        if not (settings.enable and settings.allow_supplier_sync_to_quickbooks):
            frappe.throw(_("Please enable Supplier sync in QuickBooks Settings"))

        url = f"{settings.base_url.strip().rstrip('/')}/v3/company/{settings.quickbooks_company_id}/vendor?minorversion={settings.minor_version or '75'}"
        headers = {
            "Authorization": f"Bearer {settings.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        # ✅ Removed `phone` because Supplier doctype does not have it
        suppliers = frappe.get_all(
            "Supplier",
            filters={"custom_quickbooks_supplier_id": ["is", "not set"]},
            fields=["name", "supplier_name"]
        )

        if not suppliers:
            return {"success": True, "message": _("All suppliers are already synced to QuickBooks.")}

        synced, errors = [], []

        for supplier in suppliers:
            try:
                # Build vendor payload dynamically
                vendor_payload = {
                    "DisplayName": supplier.supplier_name,
                    "CompanyName": supplier.supplier_name,
                    "PrintOnCheckName": supplier.supplier_name
                }

                res = requests.post(url, headers=headers, json=vendor_payload, timeout=30)
                res.raise_for_status()
                data = res.json()

                if "Vendor" in data:
                    qbo_id = data["Vendor"].get("Id")
                    frappe.db.set_value("Supplier", supplier.name, "custom_quickbooks_supplier_id", qbo_id)
                    synced.append({"supplier": supplier.name, "qbo_id": qbo_id})
                else:
                    errors.append({"supplier": supplier.name, "error": data})

            except Exception as e:
                errors.append({"supplier": supplier.name, "error": str(e)})

        frappe.db.commit()

        return {
            "success": True,
            "synced": synced,
            "errors": errors,
            "message": _("{0} supplier(s) synced, {1} error(s)").format(len(synced), len(errors))
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "QuickBooks Supplier Sync Error")
        frappe.throw(_("Error while syncing suppliers: {0}").format(str(e)))



def sync_purchase_invoice_to_quickbooks(doc, method):

    settings = frappe.get_doc("QuickBooks Settings")

    if not settings.enable:
        frappe.frappe.msgprint('Please Enable Quickbooks Integration')
        return
    refresh_quickbooks_access_token()

    """Hook function to sync Purchase Invoice to QuickBooks on submit."""
    sync_single_purchase_invoice_to_quickbooks(doc.name)



@frappe.whitelist()
def sync_items_to_quickbooks_background():
    """Enqueue item sync job to run in background."""

    settings = frappe.get_doc("QuickBooks Settings")
    if settings.allow_item_sync_to_quickbooks != 1 and not settings.enable:
        frappe.msgprint('Navigate to Quickbooks Settings & Please enable Item sync to continue', title="QuickBooks Item Sync Disabled",
                        indicator="red")
        return

    # Fetch items where custom_quickbooks_item_id is NULL
    items = frappe.get_all('Item', filters={'custom_quickbooks_item_id': ('in', [None, ''])}, fields=['name'])

    frappe.log_error(f"[Item Sync] Found {len(items)} items to sync to QuickBooks.")

    for item in items:
        # Enqueue each item for sync
        frappe.enqueue('quickbooks_integration.api.create_item_on_quickbooks',
                       queue='short',
                       timeout=300,
                       item_name=item.name)

    frappe.msgprint("Item sync to QuickBooks has been started in the background.")


@frappe.whitelist()
def start_stock_sync_background():
    """Enqueue stock sync job to run in background."""
    refresh_quickbooks_access_token()

    settings = frappe.get_doc("QuickBooks Settings")
    if not settings.enable:
        frappe.msgprint(
            'Navigate to Quickbooks Settings & Please enable QuickBooks Integration to continue',
            title="QuickBooks Integration Disabled",
            indicator="red"
        )
        return

    frappe.enqueue(sync_stock_from_quickbooks, queue='long', timeout=600)
    frappe.msgprint("Stock sync and reconciliation from QuickBooks has been started in the background.")


@frappe.whitelist()
def sync_stock_from_quickbooks():
    """Sync inventory stock quantities from QuickBooks to ERPNext with stock reconciliation."""
    frappe.logger().info("[QB SYNC] Started stock sync job")

    settings = frappe.get_single("QuickBooks Settings")
    access_token = settings.access_token
    company_id = settings.quickbooks_company_id
    base_url = settings.base_url.strip().rstrip("/")
    minor_version = settings.minor_version or "75"

    if not access_token or not company_id:
        frappe.log_error("Missing QuickBooks access token or company ID", "QuickBooks Stock Sync Failed")
        return

    start_position = 1
    max_results = 100
    synced_count = 0
    error_count = 0
    reconciliation_items = []  # Collect items for batch reconciliation

    while True:
        # Query inventory items from QuickBooks
        query = f"SELECT * FROM Item WHERE Type='Inventory' STARTPOSITION {start_position} MAXRESULTS {max_results}"
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
                try:
                    item_data = prepare_item_for_reconciliation(qb_item)
                    if item_data:
                        reconciliation_items.append(item_data)
                        synced_count += 1
                except Exception as e:
                    error_count += 1
                    frappe.log_error(
                        message=f"Error preparing stock for item {qb_item.get('Id')}: {str(e)}",
                        title="QuickBooks Stock Sync Item Error"
                    )

            frappe.logger().info(f"[QB SYNC] Fetched {len(items)} inventory items starting from {start_position}.")

            if len(items) < max_results:
                break  # Last page reached

            start_position += max_results

        except Exception as e:
            frappe.log_error(message=str(e), title="QuickBooks Stock Sync Failed")
            break

    # Create Stock Reconciliation entries
    if reconciliation_items:
        try:
            create_stock_reconciliation(reconciliation_items)
            frappe.logger().info(f"[QB SYNC] Created Stock Reconciliation with {len(reconciliation_items)} items.")
        except Exception as e:
            frappe.log_error(
                message=f"Error creating Stock Reconciliation: {str(e)}",
                title="QuickBooks Stock Reconciliation Error"
            )
            error_count += len(reconciliation_items)

    frappe.logger().info(f"[QB SYNC] Completed stock sync job. Synced: {synced_count}, Errors: {error_count}")
    frappe.db.commit()


def prepare_item_for_reconciliation(qb_item):
    """Prepare item data for stock reconciliation."""
    qb_id = qb_item.get("Id")
    if not qb_id:
        frappe.logger().error("[QB SYNC] Missing Item ID in QuickBooks data.")
        return None

    # Find ERPNext item by QuickBooks ID
    erpnext_item = frappe.db.get_value("Item", {"custom_quickbooks_item_id": qb_id}, "name")
    if not erpnext_item:
        frappe.logger().warn(f"[QB SYNC] Item with QuickBooks ID {qb_id} not found in ERPNext. Skipping stock update.")
        return None

    # Get item doc to check if it's a stock item
    item_doc = frappe.get_doc("Item", erpnext_item)
    if not item_doc.is_stock_item:
        frappe.logger().info(f"[QB SYNC] Item {erpnext_item} is not a stock item. Skipping stock update.")
        return None

    # Get quantity on hand from QuickBooks
    qty_on_hand = qb_item.get("QtyOnHand", 0)
    if qty_on_hand is None:
        qty_on_hand = 0

    try:
        qty_on_hand = float(qty_on_hand)
        # Treat negative stock as 0
        qty_on_hand = max(qty_on_hand, 0)
    except (ValueError, TypeError):
        qty_on_hand = 0

    # Get warehouse - try from existing Bin first, then from Stock Settings, then use any enabled warehouse
    default_warehouse = None
    
    # Try to get warehouse from existing Bin records for this item (only if warehouse is enabled)
    existing_bin = frappe.db.get_value(
        "Bin",
        {"item_code": erpnext_item},
        "warehouse",
        order_by="creation desc"
    )
    
    if existing_bin:
        # Verify the warehouse from Bin is enabled
        warehouse_enabled = frappe.db.get_value("Warehouse", existing_bin, "disabled")
        if not warehouse_enabled:  # disabled = 0 means enabled
            default_warehouse = existing_bin
    
    if not default_warehouse:
        # Get default warehouse from Stock Settings (verify it's enabled)
        stock_settings_warehouse = frappe.db.get_single_value("Stock Settings", "default_warehouse")
        if stock_settings_warehouse:
            warehouse_enabled = frappe.db.get_value("Warehouse", stock_settings_warehouse, "disabled")
            if not warehouse_enabled:  # disabled = 0 means enabled
                default_warehouse = stock_settings_warehouse
        
        if not default_warehouse:
            # Get any enabled warehouse (is_group = 0, disabled = 0)
            warehouses = frappe.get_all(
                "Warehouse", 
                filters={"is_group": 0, "disabled": 0}, 
                limit=1
            )
            if warehouses:
                default_warehouse = warehouses[0].name
    
    if not default_warehouse:
        frappe.logger().warn(f"[QB SYNC] No enabled warehouse found for item {erpnext_item}. Skipping stock update.")
        return None

    # Valuation rate = 1 as per requirement
    valuation_rate = 1.0

    # Update item valuation rate to 1
    item_doc.valuation_rate = valuation_rate
    item_doc.save(ignore_permissions=True)

    return {
        "item_code": erpnext_item,
        "warehouse": default_warehouse,
        "qty": qty_on_hand,
        "valuation_rate": valuation_rate,
        "qb_id": qb_id,
        "item_doc": item_doc  # Pass item_doc for batch handling
    }


def create_stock_reconciliation(reconciliation_items):
    """Create Stock Reconciliation document with items from QuickBooks - following NetSuite pattern."""
    if not reconciliation_items:
        return

    # Get default company
    company = frappe.defaults.get_user_default("Company") or frappe.defaults.get_global_default("company")
    if not company:
        # Try alternative method
        company = frappe.db.get_single_value("Global Defaults", "default_company")
    
    if not company:
        # Get first available company
        companies = frappe.get_all("Company", limit=1)
        if companies:
            company = companies[0].name
        else:
            frappe.logger().error("[QB SYNC] No company found. Cannot create Stock Reconciliation.")
            return

    company_abbr = frappe.db.get_value("Company", company, "abbr") or ""

    # Group items by warehouse for better organization
    warehouse_groups = {}
    for item in reconciliation_items:
        warehouse = item["warehouse"]
        if warehouse not in warehouse_groups:
            warehouse_groups[warehouse] = []
        warehouse_groups[warehouse].append(item)

    # Create Stock Reconciliation for each warehouse
    for warehouse, items in warehouse_groups.items():
        try:
            # Get warehouse company to ensure consistency
            warehouse_company = frappe.db.get_value("Warehouse", warehouse, "company")
            reconciliation_company = warehouse_company or company
            
            # Determine purpose: Opening Stock or regular reconciliation
            existing_sr_count = frappe.db.count("Stock Reconciliation", {"company": reconciliation_company})
            purpose = "Opening Stock" if existing_sr_count == 0 else "Stock Reconciliation"
            
            # Prepare items list with all required fields
            items_list = []
            
            for item_data in items:
                item_code = item_data["item_code"]
                item_doc = item_data.get("item_doc")
                
                if not item_doc:
                    item_doc = frappe.get_doc("Item", item_code)
                
                # Get current stock quantity
                current_qty = frappe.db.get_value(
                    "Bin",
                    {"item_code": item_code, "warehouse": warehouse},
                    "actual_qty"
                ) or 0

                # Only add if quantity differs or if we need to set initial stock
                if current_qty != item_data["qty"] or item_data["qty"] > 0:
                    item_entry = {
                        "item_code": item_code,
                        "warehouse": warehouse,
                        "qty": item_data["qty"],
                        "valuation_rate": item_data["valuation_rate"],
                        "use_serial_batch_fields": 1
                    }
                    
                    # Handle batch if required
                    if item_doc.has_batch_no:
                        # Try to fetch latest batch, else skip batch number
                        batch_no = frappe.db.get_value(
                            "Batch",
                            {"item": item_code},
                            "name",
                            order_by="creation desc"
                        )
                        if batch_no:
                            item_entry["batch_no"] = batch_no
                    
                    items_list.append(item_entry)

            # Only create if there are items to reconcile
            if items_list:
                stock_reconciliation = frappe.new_doc("Stock Reconciliation")
                stock_reconciliation.company = reconciliation_company
                stock_reconciliation.purpose = purpose
                
                # Set expense account for opening stock
                if purpose == "Opening Stock" and company_abbr:
                    expense_account = f"Temporary Opening - {company_abbr}"
                    # Check if account exists, if not, skip it
                    if frappe.db.exists("Account", expense_account):
                        stock_reconciliation.expense_account = expense_account
                
                # Set items
                for item_entry in items_list:
                    stock_reconciliation.append("items", item_entry)
                
                stock_reconciliation.insert(ignore_permissions=True)
                stock_reconciliation.submit()
                
                frappe.logger().info(
                    f"[QB SYNC] Created and submitted Stock Reconciliation {stock_reconciliation.name} "
                    f"for warehouse {warehouse} with {len(items_list)} items. Purpose: {purpose}."
                )
            else:
                frappe.logger().info(f"[QB SYNC] No items to reconcile for warehouse {warehouse}.")

        except Exception as e:
            frappe.log_error(
                message=f"Error creating Stock Reconciliation for warehouse {warehouse}: {str(e)}\n{frappe.get_traceback()}",
                title="QuickBooks Stock Reconciliation Creation Error"
            )
            raise

