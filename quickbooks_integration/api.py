import json
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



@frappe.whitelist(allow_guest=True)
def enqueue_sync_invoice_cancellation_to_quickbooks(doc, method):
    """Enqueue the sync invoice cancellation job to QuickBooks"""
    frappe.msgprint("Invoice synchronization with QuickBooks has started.", indicator="green")
    # Pass only doc.name (which is the doc_name) when enqueuing
    frappe.enqueue(sync_sales_invoice_cancellation, queue='long', doc_name=doc.name)




def sync_sales_invoice_cancellation(doc_name):
    """
    Called via Frappe doc_events on Sales Invoice cancel.
    """
    # Fetch the Sales Invoice document using the doc_name
    doc = frappe.get_doc("Sales Invoice", doc_name)

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



@frappe.whitelist()
def get_quickbooks_purchase_order_sync_token(purchase_order_id):
    """
    Fetch the latest SyncToken for a QuickBooks Purchase Order by its ID.

    Args:
        purchase_order_id (str): The QuickBooks Purchase Order ID.

    Returns:
        str: SyncToken of the Purchase Order.

    Raises:
        frappe.ValidationError: If fetching the Purchase Order or SyncToken fails.
    """
    if not purchase_order_id:
        frappe.throw(_("QuickBooks Purchase Order ID is required."))

    settings = frappe.get_single("QuickBooks Settings")
    access_token = settings.access_token
    realm_id = settings.quickbooks_company_id
    minor_version = settings.minor_version or "75"
    base_url = settings.base_url.rstrip("/").replace("https://", "").strip("/")

    url = f"https://{base_url}/v3/company/{realm_id}/purchaseorder/{purchase_order_id}?minorversion={minor_version}"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        frappe.throw(_("Failed to fetch Purchase Order from QuickBooks. Error: {0}").format(str(e)))

    purchase_order_data = data.get("PurchaseOrder")
    if not purchase_order_data:
        frappe.throw(_("No Purchase Order data found for ID {0} in QuickBooks.").format(purchase_order_id))

    sync_token = purchase_order_data.get("SyncToken")
    if sync_token is None:
        frappe.throw(_("SyncToken not found for QuickBooks Purchase Order ID {0}.").format(purchase_order_id))

    return sync_token


@frappe.whitelist()
def sync_purchase_invoice_cancellation(doc, method):
    """
    Frappe doc_event hook for Purchase Invoice cancellation.
    Cancels the linked Purchase Order in QuickBooks, if available.
    """
    qb_purchase_order_id = doc.get("custom_quickbooks_bill_id")

    if not qb_purchase_order_id:
        frappe.msgprint(_("No QuickBooks Purchase Order ID found for {0}. Skipping cancellation.").format(doc.name))
        return

    cancel_quickbooks_purchase_order(qb_purchase_order_id)
    frappe.msgprint(_("Purchase Invoice {0} successfully voided in QuickBooks.").format(doc.name))


@frappe.whitelist()
def cancel_quickbooks_purchase_order(purchase_order_id):
    """
    Cancel (void) a Purchase Order in QuickBooks by its ID.

    Args:
        purchase_order_id (str): The QuickBooks Purchase Order ID.
    """
    refresh_quickbooks_access_token()

    if not purchase_order_id:
        frappe.throw(_("QuickBooks Purchase Order ID is required."))

    settings = frappe.get_single("QuickBooks Settings")
    access_token = settings.access_token
    realm_id = settings.quickbooks_company_id
    base_url = settings.base_url.rstrip("/").replace("https://", "").strip("/")
    url = f"https://{base_url}/v3/company/{realm_id}/purchaseorder?operation=delete"

    sync_token = get_quickbooks_purchase_order_sync_token(purchase_order_id)

    payload = {
        "Id": purchase_order_id,
        "SyncToken": sync_token
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    frappe.logger().info(f"[QuickBooks] Voiding Purchase Order {purchase_order_id} with SyncToken {sync_token}")
    frappe.logger().info(f"[QuickBooks] Void Purchase Order Request URL: {url}")
    frappe.logger().info(f"[QuickBooks] Void Purchase Order Payload: {payload}")

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        frappe.logger().info(f"[QuickBooks] Void Purchase Order Response: {response.text}")
    except requests.RequestException as e:
        frappe.throw(_("Failed to cancel Purchase Order in QuickBooks. Error: {0}").format(str(e)))

    return _("Purchase Order {0} has been successfully cancelled in QuickBooks.").format(purchase_order_id)


@frappe.whitelist(allow_guest=True)
def sync_selected_sales_invoices(selected_si):
    """Enqueue the selected sales invoices job to QuickBooks"""
    frappe.msgprint("Selected Sales Invoices synchronization with QuickBooks has started.", indicator="green")

    # Ensure the argument is a list of names
    if isinstance(selected_si, str):
        selected_si = json.loads(selected_si)

    # Fetch the selected sales invoices
    unsynced_invoices = frappe.get_all('Sales Invoice', filters={'name': ['in', selected_si], 'is_synced': 0}, fields=['name'])

    if unsynced_invoices:
        for invoice in unsynced_invoices:
            # Enqueue the sync job for each unsynced invoice
            frappe.enqueue('quickbooks_integration.api.sync_single_sales_invoice',
                           docname=invoice['name'], queue='long')

        frappe.msgprint(f"{len(unsynced_invoices)} invoices have been added to the sync queue.", indicator="green")
    else:
        frappe.msgprint("No unsynced invoices found.", indicator="orange")

    return "Bulk sync jobs for selected invoices have been enqueued."



@frappe.whitelist(allow_guest=True)
def sync_single_sales_invoice(docname):
    """Sync a single Sales Invoice to QuickBooks"""
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

    # Prepare line items for QuickBooks API
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
        # Making the API request to QuickBooks
        res = requests.post(url, headers=headers, data=json.dumps(payload))

        if res.status_code == 200 and (qbo_id := res.json().get("Invoice", {}).get("Id")):
            doc.db_set("custom_quickbooks_invoice_id", qbo_id)
            doc.db_set("status", "Confirmed")
            doc.db_set("is_synced", 1)
            create_quickbooks_sync_record(doc, status="Confirmred", synced=1)
            frappe.db.commit()

            frappe.logger().info(f"[QBO] Sales Invoice {doc.name} synced as QBO Invoice {qbo_id}")
        else:
            frappe.log_error("QuickBooks Invoice Sync Failed", f"Sales Invoice: {doc.name}\nStatus: {res.status_code}\nResponse: {res.text}")
            create_quickbooks_sync_record(doc, status="Failure", synced=0)
    except Exception as e:
        frappe.log_error("QuickBooks Invoice Sync Error", f"Sales Invoice: {doc.name}\nError: {str(e)}")
        create_quickbooks_sync_record(doc, status="Failure", synced=0)

    frappe.db.commit()







def create_quickbooks_sync_record(doc, status, synced):
    try:
        qb_sync_meta = frappe.get_doc("QuickBooks Sync")
        if qb_sync_meta:
            quickbooks_sync = frappe.get_doc("QuickBooks Sync", doc.name) or frappe.get_doc({
                "doctype": "QuickBooks Sync",
                "parent": doc.name,
                "parenttype": "Sales Invoice",
                "sales_invoice_list": [{
                    "invoice_name": doc.name,
                    "quickbooks_invoice_status": status,
                    "is_synced": synced,
                }]
            })
            quickbooks_sync.insert(ignore_permissions=True)
            frappe.db.commit()
            frappe.logger().info(f"QuickBooks Sync for {doc.name} inserted with status {status}.")
        else:
            frappe.throw("QuickBooks Sync DocType not found.")
    except Exception as e:
        frappe.log_error(f"Error inserting QuickBooks Sync for {doc.name}", str(e))

def create_item_on_quickbooks(item_name):
    refresh_quickbooks_access_token()

    item_doc = frappe.get_doc("Item", item_name)

    if item_doc.custom_quickbooks_item_id:
        frappe.msgprint(_("Item {0} already exists in QuickBooks with ID {1}.").format(item_name, item_doc.custom_quickbooks_item_id))
        return

    settings = frappe.get_single("QuickBooks Settings")
    access_token = settings.access_token
    realm_id = settings.quickbooks_company_id
    base_url = settings.base_url.rstrip("/").replace("https://", "").strip("/")
    url = f"https://{base_url}/v3/company/{realm_id}/item?minorversion={settings.minor_version or '75'}"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    gst_code = get_tax_code_for_item(item_name)

    payload = {
        "Name": item_doc.item_name,
        "Taxable": True,
        "Type": "Service",
        "IncomeAccountRef": {"name": "Sales of Product Income", "value": "79"}
    }

    if gst_code:
        payload["PurchaseTaxCodeRef"] = {"value": gst_code}

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()

        data = response.json()
        quickbooks_id = data.get("Item", {}).get("Id")

        if quickbooks_id:
            item_doc.custom_quickbooks_item_id = quickbooks_id
            item_doc.save(ignore_permissions=True)
            frappe.db.commit()
            frappe.msgprint(_("Item {0} created successfully in QuickBooks with ID {1}.").format(item_name, quickbooks_id))
        else:
            frappe.throw(_("QuickBooks API returned no valid Item ID for the item {0}.").format(item_name))

    except requests.RequestException as e:
        frappe.log_error(message=f"QuickBooks API Error: {response.text}", title="QuickBooks API Request Failed")
        frappe.throw(_("Failed to create Item in QuickBooks. Error: {0}").format(str(e)))

    except Exception as e:
        frappe.log_error(f"Unexpected error while creating item {item_name}: {str(e)}", title="Unexpected Error in Item Sync")
        frappe.throw(_("An unexpected error occurred while creating the item {0} in QuickBooks.").format(item_name))

def get_tax_code_for_item(item_name):
    item_doc = frappe.get_doc("Item", item_name)

    for tax_row in item_doc.taxes:
        tax_template_name = tax_row.item_tax_template
        tax_template = frappe.get_all("Item Tax Template",
                                      filters={"name": tax_template_name},
                                      fields=["custom_quickbooks_gst_id"],
                                      limit=1)

        if tax_template:
            gst_id = tax_template[0].custom_quickbooks_gst_id
            return gst_id
        else:
            frappe.logger().warn(f"[Item Sync] No Item Tax Template found for tax template: {tax_template_name}")

    frappe.logger().warn(f"[Item Sync] No tax templates found for item: {item_name}")
    return None

@frappe.whitelist(allow_guest=True)
def sync_credit_memo_to_quickbooks(docname):
    try:
        invoice = frappe.get_doc("Sales Invoice", docname)
        sync_credit_memo(invoice)
    except Exception as e:
        error_message = f"Error in syncing Credit Memo {docname}: {str(e)}"
        frappe.log_error("QuickBooks Credit Memo Sync Error", error_message)

def sync_credit_memo(invoice):
    # Refresh access token if needed
    refresh_quickbooks_access_token()

    settings = frappe.get_doc("QuickBooks Settings")
    url = f"{settings.base_url}/v3/company/{settings.quickbooks_company_id}/creditmemo?minorversion={settings.minor_version or '75'}"
    headers = {
        "Authorization": f"Bearer {settings.access_token}",
        "Content-Type": "application/json"
    }

    def get_tax_code(item):
        return frappe.get_doc("Item Tax Template", item.item_tax_template).custom_quickbooks_gst_id or "5" if item.item_tax_template else "5"

    line_items = []
    for item in invoice.items:
        item_ref = frappe.db.get_value("Item", item.item_code, "custom_quickbooks_item_id")
        if not item_ref:
            error_message = f"QuickBooks Item ID is missing for Item {item.item_code} in Credit Memo {invoice.name}"
            frappe.throw(error_message)


        line_items.append({
            "DetailType": "SalesItemLineDetail",
            "Amount": abs(item.qty * item.rate),
            "SalesItemLineDetail": {
                "Qty": abs(item.qty),
                "UnitPrice": abs(item.rate),
                "ItemRef": {"value": item_ref},
                "TaxCodeRef": {"value": get_tax_code(item)}
            }
        })

    total_amount = abs(invoice.grand_total)

    if total_amount < 0:
        frappe.throw(f"Credit Memo Total Amount must be zero or greater. Current total: {total_amount}")

    qb_customer_id = frappe.db.get_value("Customer", invoice.customer, "custom_quickbooks_customer_id")
    if not qb_customer_id:
        frappe.throw(f"QuickBooks Customer ID is missing for Customer {invoice.customer}")

    payload = {
        "DocNumber": invoice.name,
        "CustomerRef": {"value": qb_customer_id},
        "Line": line_items,
        "CustomerMemo": {"value": "Credit Memo from ERPNext"}
    }

    frappe.logger().info(f"Credit Memo Payload: {json.dumps(payload, indent=4)}")

    try:
        res = requests.post(url, headers=headers, data=json.dumps(payload))

        if res.status_code != 200:
            error_message = f"Failed to sync Credit Memo {invoice.name} - Status Code: {res.status_code} - Response Body: {res.text}"
            frappe.log_error("QuickBooks Credit Memo Sync Failed", error_message)

        res.raise_for_status()

        if res.status_code == 200:
            qb_id = res.json().get("CreditMemo", {}).get("Id")
            if qb_id:
                frappe.logger().info(f"Credit Memo {invoice.name} synced as QuickBooks Credit Memo {qb_id}")
        else:
            frappe.throw(f"Failed to sync Credit Memo. Response: {res.status_code} - {res.text}")

    except requests.exceptions.RequestException as e:
        error_message = f"Error syncing Credit Memo {invoice.name}: {str(e)} - Response Body: {res.text if res else 'No response from server'}"
        frappe.throw(f"Error syncing Credit Memo {invoice.name}: {str(e)}")
