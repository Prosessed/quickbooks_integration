import json
import os
import time
import hashlib
from datetime import timedelta
from urllib.parse import urlencode

import frappe
import requests
from frappe import _
from frappe.utils import now_datetime, nowdate, flt
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry


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



@frappe.whitelist()
def fetch_quickbooks_customer_statement(**kwargs):
    """
    Fetch the Customer Balance Detail report from QuickBooks and return it in ERP-style format.
    """

    def normalize_key(raw_key, fallback):
        key = raw_key or fallback or ""
        return key.strip().lower().replace(" ", "_").replace("*", "")

    def get_column_keys(report_json):
        keys = []
        columns = report_json.get("Columns", {}).get("Column", [])
        for idx, column in enumerate(columns):
            meta = next((md.get("Value") for md in column.get("MetaData", []) if md.get("Name") == "ColKey"), None)
            keys.append(normalize_key(meta, column.get("ColTitle") or f"col_{idx}"))
        return keys

    def coldata_to_dict(coldata, keys):
        record = {}
        for idx, col in enumerate(coldata or []):
            key = keys[idx] if idx < len(keys) else f"col_{idx}"
            if col.get("value") not in (None, ""):
                record[key] = col.get("value")
            if col.get("id"):
                record[f"{key}_id"] = col.get("id")
        return record

    def flatten_rows(row_items, keys, bucket, summary_bucket):
        for row in row_items or []:
            row_type = row.get("type")
            if row_type == "Data":
                bucket.append(coldata_to_dict(row.get("ColData", []), keys))
            elif row_type == "Summary":
                summary_bucket.append(coldata_to_dict(row.get("ColData", []), keys))

            if row.get("Rows"):
                flatten_rows(row["Rows"].get("Row", []), keys, bucket, summary_bucket)
            elif row.get("Row"):
                flatten_rows(row.get("Row", []), keys, bucket, summary_bucket)

    def resolve_erp_customer(identifier):
        if not identifier:
            return None

        filters = {"custom_quickbooks_customer_id": identifier}
        doc = frappe.db.get_value("Customer", filters, ["name", "customer_name", "custom_quickbooks_customer_id"], as_dict=True)
        if doc:
            return doc

        if frappe.db.exists("Customer", identifier):
            return frappe.db.get_value("Customer", identifier, ["name", "customer_name", "custom_quickbooks_customer_id"], as_dict=True)

        return None

    def get_contact_details(erp_customer):
        if not erp_customer:
            return {"contact_name": None, "phone": None, "email": None, "address": None}

        contact_name = None
        phone = None
        email = None
        address_text = None

        primary_contact = frappe.db.get_value(
            "Dynamic Link",
            {
                "link_doctype": "Customer",
                "link_name": erp_customer.name,
                "parenttype": "Contact",
            },
            "parent",
        )

        if primary_contact:
            contact_doc = frappe.get_doc("Contact", primary_contact)
            contact_name = contact_doc.first_name or contact_doc.name
            phone = contact_doc.mobile_no or contact_doc.phone
            email = contact_doc.email_id

        address_link = frappe.db.get_value(
            "Dynamic Link",
            {
                "link_doctype": "Customer",
                "link_name": erp_customer.name,
                "parenttype": "Address",
            },
            "parent",
        )

        if address_link:
            address_doc = frappe.get_doc("Address", address_link)
            address_text = ", ".join(filter(None, [address_doc.address_line1, address_doc.city, address_doc.country]))

        return {
            "contact_name": contact_name,
            "phone": phone,
            "email": email,
            "address": address_text,
        }

    def build_erp_response(report_json, customer_info, contact_info, start_date, end_date):
        column_keys = get_column_keys(report_json)
        transactions, summaries = [], []
        flatten_rows(report_json.get("Rows", {}).get("Row", []), column_keys, transactions, summaries)

        currency = report_json.get("Header", {}).get("Currency")

        def pick(record, *keys):
            for key in keys:
                if key in record and record[key] not in (None, ""):
                    return record[key]
            return ""

        invoices, credit_notes, payments = [], [], []

        for txn in transactions:
            txn_type = (pick(txn, "txn_type", "col_0") or "").lower()
            doc_num = pick(txn, "doc_num", "docnum", "txn_id")
            tx_date = pick(txn, "tx_date", "date")
            due_date = pick(txn, "due_date")
            memo = pick(txn, "memo", "cust_msg")
            amount = flt(pick(txn, "subt_amount", "amount", "amount_due", "balance", "open_balance"))
            balance = flt(pick(txn, "balance", "open_balance", "amount_due", "outstanding_amount"))

            if txn_type in ("invoice", "salesreceipt", "sales_receipt"):
                outstanding = balance if balance else amount
                invoices.append({
                    "invoice_id": doc_num or txn.get("col_1"),
                    "posting_date": tx_date or "",
                    "due_date": due_date or "",
                    "grand_total": amount,
                    "outstanding_amount": outstanding,
                    "status": "Paid" if outstanding == 0 else "Unpaid",
                    "currency": currency,
                    "payment_status": "Paid" if outstanding == 0 else "Unpaid",
                })
            elif txn_type in ("creditmemo", "credit_memo", "credit"):
                remaining_credit = balance if balance else amount
                credit_notes.append({
                    "credit_note_no": doc_num or txn.get("col_1"),
                    "posting_date": tx_date or "",
                    "status": "Closed" if remaining_credit == 0 else "Open",
                    "total": amount,
                    "remaining_credit": remaining_credit,
                    "currency": currency,
                })
            elif txn_type in ("payment", "receivepayment", "sales_payment"):
                payments.append({
                    "payment_id": doc_num or txn.get("col_1"),
                    "posting_date": tx_date or "",
                    "paid_amount": amount,
                    "received_amount": amount,
                    "payment_type": "Receive",
                    "mode_of_payment": pick(txn, "ship_via"),
                    "reference_no": memo,
                })

        total_invoices = sum(flt(inv["grand_total"]) for inv in invoices)
        total_credit_notes = sum(flt(note["total"]) for note in credit_notes)
        total_payments = sum(flt(pay["paid_amount"]) for pay in payments)
        closing_balance = (
            sum(flt(inv["outstanding_amount"]) for inv in invoices)
            - sum(flt(note["remaining_credit"]) for note in credit_notes)
            - total_payments
        )

        return {
            "status": "success",
            "customer": (customer_info.name if customer_info else None),
            "customer_name": (customer_info.customer_name if customer_info else report_json.get("Header", {}).get("Customer")),
            "company": frappe.defaults.get_global_default("company"),
            "from_date": start_date,
            "to_date": end_date,
            "contact_details": contact_info,
            "invoices": invoices,
            "credit_notes": credit_notes,
            "payments": payments,
            "summary": {
                "currency": currency,
                "opening_balance": 0.0,
                "total_invoices": total_invoices,
                "total_payments": total_payments * -1,
                "credit_notes": total_credit_notes * -1,
                "closing_balance": closing_balance,
            },
        }

    try:
        refresh_quickbooks_access_token()
    except Exception:
        frappe.logger().warning("[QuickBooks] Unable to refresh access token, attempting with existing token.")

    settings = frappe.get_single("QuickBooks Settings")

    if not settings.enable:
        frappe.throw(_("QuickBooks integration is disabled. Please enable it in QuickBooks Settings."))

    access_token = settings.access_token
    realm_id = settings.quickbooks_company_id
    base_url = (settings.base_url or "").strip().rstrip("/")

    if not access_token or not realm_id or not base_url:
        frappe.throw(_("QuickBooks settings are incomplete. Please provide access token, company ID, and base URL."))

    if not base_url.startswith("http"):
        base_url = f"https://{base_url}"

    allowed_params = [
        "customer",
        "shipvia",
        "term",
        "end_duedate",
        "start_duedate",
        "custom1",
        "sort_by",
        "arpaid",
        "report_date",
        "sort_order",
        "aging_method",
        "department",
        "columns",
    ]

    request_params = {}
    form_dict = frappe._dict(frappe.form_dict or {})

    for param in allowed_params:
        value = kwargs.get(param, form_dict.get(param))
        if value in (None, "", []):
            continue
        request_params[param] = value

    request_params["minorversion"] = settings.minor_version or "75"

    customer_identifier = request_params.get("customer") or ""
    start_date = request_params.get("start_duedate")
    end_date = request_params.get("end_duedate")

    if not customer_identifier:
        frappe.throw(_("Customer parameter is required to fetch the statement."))

    erp_customer = resolve_erp_customer(customer_identifier)
    contact_details = get_contact_details(erp_customer)

    query_string = urlencode(request_params, doseq=True)
    endpoint = f"{base_url}/v3/company/{realm_id}/reports/CustomerBalanceDetail"
    url = f"{endpoint}?{query_string}" if query_string else endpoint

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        report_json = response.json()
        return build_erp_response(report_json, erp_customer, contact_details, start_date, end_date)
    except requests.RequestException as exc:
        frappe.log_error(
            message=f"{frappe.get_traceback()}\nURL: {url}\nDetails: {str(exc)}",
            title="QuickBooks Customer Statement Fetch Failed"
        )
        frappe.throw(_("Unable to fetch customer statement from QuickBooks. Please try again later."))
    except ValueError:
        frappe.log_error(
            message=f"{frappe.get_traceback()}\nURL: {url}\nDetails: Non-JSON response",
            title="QuickBooks Customer Statement Invalid Response"
        )
        frappe.throw(_("QuickBooks returned an invalid response for the customer statement request."))



@frappe.whitelist(allow_guest=True)
def enqueue_sync_invoice_cancellation_to_quickbooks(doc, method):
    settings = frappe.get_doc("QuickBooks Settings")
    if not settings.enable:
        frappe.msgprint('Please Enable Quickbook Settings')
        return
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
def sync_single_sales_invoice(docname):
    """Sync a single Sales Invoice to QuickBooks with global invoice-level discount %"""
    import requests, json
    doc = frappe.get_doc("Sales Invoice", docname)

    if doc.docstatus == 2:
        frappe.msgprint("Cancelled Invoices are not allowed to sync")
        return {"error": "Cancelled invoice cannot be synced."}

    refresh_quickbooks_access_token()
    settings = frappe.get_doc("QuickBooks Settings")

    if not settings.enable:
        frappe.msgprint("Please Enable QuickBooks Settings")
        return {"error": "QuickBooks not enabled."}

    # ---- QuickBooks endpoint ----
    url = f"{settings.base_url.strip().rstrip('/')}/v3/company/{settings.quickbooks_company_id}/invoice?minorversion={settings.minor_version or '75'}"
    headers = {
        "Authorization": f"Bearer {settings.access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    # ---- Customer ----
    customer = frappe.get_doc("Customer", doc.customer)
    qb_customer_id = customer.get("custom_quickbooks_customer_id") or "1"
    send_item = (settings.send_item) == 1

    # ---- Helper: get tax code ----
    def get_tax_code(item):
        if not item.item_tax_template:
            return "5"
        try:
            return frappe.get_doc("Item Tax Template", item.item_tax_template).custom_quickbooks_gst_id or "5"
        except Exception as e:
            frappe.log_error(f"Error fetching GST from {item.item_tax_template}", str(e))
            return "5"

    # ---- Prepare line items ----
    line_items = []
    subtotal = 0.0
    for item in doc.items:
        amount = float(item.amount or 0)
        subtotal += amount
        detail = {
            "Qty": item.qty,
            "UnitPrice": float(item.rate or 0),
            "TaxCodeRef": {"value": get_tax_code(item)}
        }
        if send_item:
            qb_item_id = frappe.db.get_value("Item", item.item_code, "custom_quickbooks_item_id")
            if qb_item_id:
                detail["ItemRef"] = {"value": qb_item_id, "name": item.item_name}

        line_items.append({
            "DetailType": "SalesItemLineDetail",
            "Amount": amount,
            "Description": item.description or item.item_name,
            "SalesItemLineDetail": detail
        })

    # ---- Global invoice-level discount ----
    discount_percent = float(doc.get("additional_discount_percentage") or 0)
    if discount_percent > 0:
        discount_amount = round(subtotal * (discount_percent / 100.0), 2)
        discount_line = {
            "DetailType": "DiscountLineDetail",
            "Amount": discount_amount,
            "Description": f"Discount {discount_percent}%",
            "DiscountLineDetail": {
                "PercentBased": True,
                "Percent": discount_percent,
                # 👇 confirmed from your QBO chart: Id=67 "Discounts given"
                "DiscountAccountRef": {"value": "53", "name": "Discounts given"}
            }
        }
        line_items.append(discount_line)

    # ---- Build payload ----
    payload = {
        "DocNumber": doc.name,
        "TxnDate": str(doc.posting_date),
        "DueDate": str(getattr(doc, "due_date", doc.posting_date)),
        "CustomerRef": {"value": qb_customer_id, "name": doc.customer},
        "Line": line_items,
        "ApplyTaxAfterDiscount": True,
        "CustomerMemo": {"value": "Generated from ERPNext"},
        "PrintStatus": "NeedToPrint",
        "EmailStatus": "NotSet",
        "GlobalTaxCalculation": "TaxInclusive"
    }

    # ---- Send request ----
    try:
        res = requests.post(url, headers=headers, data=json.dumps(payload))
        body = res.json() if res.text else {}

        if res.status_code in (200, 201) and body.get("Invoice", {}).get("Id"):
            qbo_id = body["Invoice"]["Id"]
            doc.db_set("custom_quickbooks_invoice_id", qbo_id)
            doc.db_set("status", "Confirmed")
            doc.db_set("is_synced", 1)
            frappe.db.commit()

            frappe.logger().info(f"[QBO] Sales Invoice {doc.name} synced as QBO Invoice {qbo_id}")
            return {"id": qbo_id, "response": body}
        else:
            frappe.log_error("QuickBooks Invoice Sync Failed",
                             f"Sales Invoice: {doc.name}\nStatus: {res.status_code}\nResponse: {res.text}")
            return {"error": f"Sync failed ({res.status_code})", "response": body}

    except Exception as e:
        frappe.log_error("QuickBooks Invoice Sync Error",
                         f"Sales Invoice: {doc.name}\nError: {str(e)}")
        return {"error": str(e)}



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
def sync_credit_memo_to_quickbooks(docname=None):
    settings = frappe.get_doc("QuickBooks Settings")

    if not settings.enable:
        frappe.msgprint('Please Enable Quickbook Settings')
        return

    try:
        invoice = frappe.get_doc("Sales Invoice", docname)
        sync_credit_memo(invoice)
    except Exception as e:
        error_message = f"Error in syncing Credit Memo {docname}: {str(e)}"
        frappe.log_error(error_message, "QuickBooks Credit Memo Sync Error")


@frappe.whitelist(allow_guest=True)
def sync_credit_memo(invoice):
    # Refresh access token if needed
    refresh_quickbooks_access_token()

    settings = frappe.get_doc("QuickBooks Settings")
    url = f"{settings.base_url}/v3/company/{settings.quickbooks_company_id}/creditmemo?minorversion={settings.minor_version or '75'}"
    headers = {
        "Authorization": f"Bearer {settings.access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    def get_tax_code(item):
        if item.item_tax_template:
            return frappe.db.get_value(
                "Item Tax Template",
                item.item_tax_template,
                "custom_quickbooks_gst_id"
            ) or "5"
        return "5"

    line_items = []
    for item in invoice.items:
        item_ref = frappe.db.get_value("Item", item.item_code, "custom_quickbooks_item_id")
        if not item_ref:
            frappe.throw(f"QuickBooks Item ID is missing for Item {item.item_code} in Credit Memo {invoice.name}")

        line_items.append({
            "DetailType": "SalesItemLineDetail",
            "Amount": abs(item.qty * item.rate),
            "Description": item.item_name or item.item_code,
            "SalesItemLineDetail": {
                "Qty": abs(item.qty),
                "UnitPrice": abs(item.rate),
                "ItemRef": {"value": "1862", "name" : "Credit"},
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
        # "DocNumber": invoice.name,
        "TxnDate": invoice.posting_date.strftime("%Y-%m-%d") if invoice.posting_date else frappe.utils.nowdate(),
        "CustomerRef": {"value": qb_customer_id},
        "Line": line_items,
        "CustomerMemo": {"value": "Credit Memo from ERPNext"}
    }

    try:
        res = requests.post(url, headers=headers, json=payload)

        if res.status_code != 200:
            frappe.log_error(
                f"Status Code: {res.status_code}\nResponse Body: {res.text}",
                f"QuickBooks Credit Memo Sync Failed - {invoice.name}"
            )
            frappe.throw(f"Failed to sync Credit Memo {invoice.name}")

        try:
            qb_data = res.json()
        except Exception:
            frappe.log_error(res.text, f"QuickBooks Non-JSON Response - {invoice.name}")
            frappe.throw(f"QuickBooks returned non-JSON response for Credit Memo {invoice.name}")

        qb_id = qb_data.get("CreditMemo", {}).get("Id")
        if qb_id:
              # Save QB CreditMemo ID back to Sales Invoice
            frappe.db.set_value("Sales Invoice", invoice.name, "custom_quickbooks_credit_memo_id", qb_id)
            frappe.db.commit()

            frappe.msgprint(f"Credit Memo synced successfully with QuickBooks. QB ID: {qb_id}")
       
            return qb_id
        else:
            frappe.log_error(res.text, f"QuickBooks Credit Memo Missing ID - {invoice.name}")
            frappe.throw(f"Credit Memo synced but no Id returned for {invoice.name}")

    except requests.exceptions.RequestException as e:
        frappe.log_error(str(e), f"QuickBooks Credit Memo Request Error - {invoice.name}")
        frappe.throw(f"Error syncing Credit Memo {invoice.name}: {str(e)}")


def fetch_quickbooks_payment(payment_id):

    refresh_quickbooks_access_token()
    qb_settings = frappe.get_single("QuickBooks Settings")
    headers = {
        "Authorization": f"Bearer {qb_settings.access_token}",
        "Accept": "application/json"
    }

    url = f"{qb_settings.base_url}/v3/company/{qb_settings.quickbooks_company_id}/payment/{payment_id}?minorversion=70"
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        return response.json().get('Payment')

    frappe.log_error("QuickBooks API Fetch Failed", response.text)
    return None

def create_payment_entry(payment):
    customer_ref = payment.get('CustomerRef', {}).get('value')
    txn_date = payment.get('TxnDate')
    total_amount = payment.get('TotalAmt')
    payment_id = payment.get('Id')

    if not customer_ref:
        frappe.log_error("Customer reference missing from QuickBooks payment", payment)
        return

    customer = frappe.db.get_value("Customer", {"custom_quickbooks_customer_id": customer_ref}, "name")

    if not customer:
        frappe.log_error(f"No ERPNext Customer found for QuickBooks customer ref {customer_ref}", payment)
        return

    for line in payment.get('Line', []):
        for txn in line.get('LinkedTxn', []):
            if txn.get('TxnType') == 'Invoice':
                invoice_id = txn.get('TxnId')
                erpnext_invoice = frappe.db.get_value("Sales Invoice", {"custom_quickbooks_invoice_id": invoice_id}, "name")

                if not erpnext_invoice:
                    frappe.log_error(f"No matching Sales Invoice found for QuickBooks invoice {invoice_id}", payment)
                    continue

                if frappe.db.exists('Payment Entry', {'reference_no': payment_id}):
                    frappe.log_error(f"Payment Entry already exists for QuickBooks payment {payment_id}", payment)
                    continue

                qbo_payment_method_id = payment.get('PaymentMethodRef', {}).get('value')


                try:
                    pe = get_payment_entry('Sales Invoice', erpnext_invoice)
                    pe.payment_type = "Receive"
                    pe.party_type = "Customer"
                    pe.party = customer
                    pe.posting_date =  nowdate()
                    pe.reference_no = payment_id
                    pe.reference_date =  nowdate()
                    pe.paid_amount = total_amount
                    pe.received_amount = total_amount
                    pe.mode_of_payment = frappe.db.get_value("Payment Method", {"custom_quickbooks_payment_method_id": qbo_payment_method_id}, "name") or "Cash"
                    pe.remarks = "Created via QuickBooks Webhook"

                    allocated_amount = min(pe.references[0].outstanding_amount, total_amount)
                    pe.references[0].allocated_amount = allocated_amount
                    if total_amount > allocated_amount:
                        pe.unallocated_amount = total_amount - allocated_amount

                    pe.insert(ignore_permissions=True)
                    pe.submit()

                    frappe.log_error(
                        title=f"ERPNext Payment Entry Created: {pe.name}",
                        message=json.dumps(payment, indent=2)
                    )

                except Exception as e:
                    frappe.db.rollback()
                    frappe.log_error(
                        title="Error creating Payment Entry from QuickBooks",
                        message=str(e) + "\n" + json.dumps(payment, indent=2)
                    )

@frappe.whitelist()
def refresh_sales_invoice_list(docname: str):
    """
    Refresh the Sales Invoice List child table inside QuickBooks Sync doctype.
    Marks invoices as Success if they have a QuickBooks Invoice ID, else Pending.
    Skips cancelled invoices (docstatus = 2).
    """
    doc = frappe.get_doc("QuickBooks Sync", docname)

    # Clear existing rows
    doc.set("sales_invoice_list", [])

    # Fetch invoices but exclude cancelled
    invoices = frappe.get_all(
        "Sales Invoice",
        fields=["name", "custom_quickbooks_invoice_id", "status", "docstatus"],
        filters={"docstatus": ["in", [0, 1]]}   # Only Draft (0) + Submitted (1)
    )

    for inv in invoices:
        has_qb_id = bool(inv.custom_quickbooks_invoice_id)

        doc.append("sales_invoice_list", {
            "invoice_name": inv.name,
            "status": "Success" if has_qb_id else "Pending",
            "quickbooks_invoice_status": inv.status or "",
            "is_synced": 1 if has_qb_id else 0,
        })

    doc.count = len(invoices)
    doc.save(ignore_permissions=True)

    return {"message": f"Refreshed {len(invoices)} invoices (excluding cancelled)."}


@frappe.whitelist()
def bulk_sync_invoices(docname: str, selected_invoices=None):
    """
    Sync only selected invoices (if provided), otherwise all from the doc.
    """
    try:
        doc = frappe.get_doc("QuickBooks Sync", docname)

        # If coming from frontend with __checked rows
        if selected_invoices:
            # Ensure list is parsed correctly from JSON
            if isinstance(selected_invoices, str):
                import json
                selected_invoices = json.loads(selected_invoices)

            invoice_names = [d.get("invoice_name") for d in selected_invoices if d.get("invoice_name")]
        else:
            # fallback: all rows
            invoice_names = [row.invoice_name for row in doc.sales_invoice_list]

        if not invoice_names:
            return {"message": "No invoices found for sync."}

        result = sync_selected_sales_invoices(docname, invoice_names)

        return {
            "message": (
                f"🧾 QuickBooks Sales Invoice Sync Summary:\n"
                f"✅ Synced: {len(result.get('synced', []))}\n"
                f"❌ Failed: {len(result.get('failed', []))}\n"
                f"⏭ Skipped: {len(result.get('skipped', []))}\n"
                f"📦 Total Attempted: {len(invoice_names)}"
            )
        }

    except Exception:
        frappe.log_error("Bulk Sync Invoices Error", frappe.get_traceback())
        return {"message": "An error occurred while syncing invoices. Please check error logs."}



@frappe.whitelist()
def sync_selected_sales_invoices(docname: str, selected_si: list):
    """
    Sync selected invoices to QuickBooks.
    Already synced invoices or cancelled ones will be skipped.
    """
    if not selected_si:
        return "No invoices selected."

    doc = frappe.get_doc("QuickBooks Sync", docname)
    synced, failed, skipped = [], [], []

    for si in selected_si:
        # check child row first
        row = next((r for r in doc.sales_invoice_list if r.invoice_name == si), None)
        if not row:
            skipped.append(si)
            continue

        if row.is_synced:  # already synced, skip
            skipped.append(si)
            continue

        try:
            # --- Step 1: Load Sales Invoice ---
            si_doc = frappe.get_doc("Sales Invoice", si)

            # --- Step 2: Skip if Cancelled ---
            if si_doc.docstatus == 2:
                row.status = "Cancelled"
                skipped.append(si_doc.name)
                continue

            # --- Step 3: If Draft, submit it ---
            if si_doc.docstatus == 0:
                si_doc.submit()  # approve/submit before invoicing

            # --- Step 4: Update workflow status to 'Invoiced' ---
            frappe.db.set_value("Sales Invoice", si_doc.name, "status", "Invoiced")

            # --- Step 5: Sync with QuickBooks ---
            result = sync_single_sales_invoice(si_doc.name)

            if result and result.get("id"):
                row.status = "Success"
                row.is_synced = 1

                # update Sales Invoice with QuickBooks ID
                frappe.db.set_value("Sales Invoice", si_doc.name, {
                    "custom_quickbooks_invoice_id": result.get("id"),
                    "status": "Invoiced"
                })
                synced.append(si_doc.name)
            else:
                raise Exception("QuickBooks sync failed or returned no ID")

        except Exception as e:
            row.status = "Failed"
            row.quickbooks_invoice_status = str(e)
            row.is_synced = 0
            failed.append(si)

    doc.save(ignore_permissions=True)
    refresh_sales_invoice_list(docname)

    return {
        "synced": synced,
        "failed": failed,
        "skipped": skipped
    }



@frappe.whitelist()
def refresh_purchase_invoices(docname: str):
    """
    Refresh the Sales Invoice List child table inside QuickBooks Sync doctype.
    Marks invoices as Success if they have a QuickBooks Invoice ID, else Pending.
    Skips cancelled invoices (docstatus = 2).
    """
    doc = frappe.get_doc("QuickBooks Sync", docname)

    # Clear existing rows
    doc.set("purchase_invoice_list", [])

    # Fetch invoices but exclude cancelled
    invoices = frappe.get_all(
        "Purchase Invoice",
        fields=["name", "custom_quickbooks_bill_id", "status", "docstatus"],
        filters={"docstatus": ["in", [0, 1]]}   # Only Draft (0) + Submitted (1)
    )

    for inv in invoices:
        has_qb_id = bool(inv.custom_quickbooks_bill_id)

        doc.append("purchase_invoice_list", {
            "invoice_name": inv.name,
            "status": "Success" if has_qb_id else "Pending",
            "quickbooks_invoice_status": inv.status or "",
            "is_synced": 1 if has_qb_id else 0,
        })

    doc.purchase_count = len(invoices)
    doc.save(ignore_permissions=True)

    return {"message": f"Refreshed {len(invoices)} invoices (excluding cancelled)."}



@frappe.whitelist()
def bulk_sync_purchase_invoices(docname: str, selected_invoices=None):
    """
    Sync only selected invoices (if provided), otherwise all from the doc.
    """
    try:
        frappe.log_error(
            title="Bulk Sync Purchase Invoices - Start",
            message=f"Docname: {docname}\nSelected Invoices (raw): {selected_invoices}"
        )

        doc = frappe.get_doc("QuickBooks Sync", docname)

        # If coming from frontend with __checked rows
        if selected_invoices:
            # Ensure list is parsed correctly from JSON
            if isinstance(selected_invoices, str):
                import json
                selected_invoices = json.loads(selected_invoices)

            invoice_names = [d.get("invoice_name") for d in selected_invoices if d.get("invoice_name")]
        else:
            # fallback: all rows
            invoice_names = [row.invoice_name for row in doc.sales_invoice_list]

        frappe.log_error(
            title="Bulk Sync Purchase Invoices - Parsed",
            message=f"Docname: {docname}\nInvoices to Sync: {invoice_names}"
        )

        if not invoice_names:
            return {"message": "No invoices found for sync."}

        result = sync_selected_purchase_invoices(docname, invoice_names)

        summary = {
            "message": (
                f"🧾 QuickBooks Sales Invoice Sync Summary:\n"
                f"✅ Synced: {len(result.get('synced', []))}\n"
                f"❌ Failed: {len(result.get('failed', []))}\n"
                f"⏭ Skipped: {len(result.get('skipped', []))}\n"
                f"📦 Total Attempted: {len(invoice_names)}"
            )
        }

        frappe.log_error(
            title="Bulk Sync Purchase Invoices - Summary",
            message=f"Docname: {docname}\nResult: {frappe.as_json(result)}"
        )

        return summary

    except Exception:
        frappe.log_error(
            title="Bulk Sync Purchase Invoices - Error",
            message=frappe.get_traceback()
        )
        return {"message": "An error occurred while syncing invoices. Please check error logs."}


@frappe.whitelist()
def sync_selected_purchase_invoices(docname: str, selected_si: list):
    """
    Sync selected purchase invoices to QuickBooks.
    Already synced invoices or cancelled ones will be skipped.
    """
    if not selected_si:
        return "No Purchase Invoice selected."

    doc = frappe.get_doc("QuickBooks Sync", docname)
    synced, failed, skipped = [], [], []

    frappe.log_error(
        title="Sync Selected Purchase Invoices - Start",
        message=f"Docname: {docname}\nSelected Invoices: {selected_si}"
    )

    for si in selected_si:
        try:
            # check child row first
            row = next((r for r in doc.purchase_invoice_list if r.invoice_name == si), None)
            if not row:
                skipped.append(si)
                frappe.log_error("Sync Invoice - Skipped (Not Found)", f"Invoice: {si}")
                continue

            if row.is_synced:  # already synced, skip
                skipped.append(si)
                frappe.log_error("Sync Invoice - Skipped (Already Synced)", f"Invoice: {si}")
                continue

            # --- Step 1: Load Purchase Invoice ---
            si_doc = frappe.get_doc("Purchase Invoice", si)

            # --- Step 2: Skip if Cancelled ---
            if si_doc.docstatus == 2:
                row.status = "Cancelled"
                skipped.append(si_doc.name)
                frappe.log_error("Sync Invoice - Skipped (Cancelled)", f"Invoice: {si_doc.name}")
                continue

            # --- Step 3: If Draft, submit it ---
            if si_doc.docstatus == 0:
                si_doc.submit()  # approve/submit before invoicing

            # --- Step 4: Update workflow status ---
            frappe.db.set_value("Purchase Invoice", si_doc.name, "status", "Submitted")

            # --- Step 5: Sync with QuickBooks ---
            result = sync_single_purchase_invoice_to_quickbooks(si_doc.name)

            frappe.log_error(
                title="Sync Invoice - Response",
                message=f"Invoice: {si_doc.name}\nResult: {frappe.as_json(result)}"
            )

            if result and result.get("id"):
                row.status = "Success"
                row.is_synced = 1

                if si_doc.is_return:
                    frappe.db.set_value("Purchase Invoice", si_doc.name, {
                        "custom_quickbooks_debitnote_id": result.get("id"),
                        "status": "Returned"
                    })
                else:
                    frappe.db.set_value("Purchase Invoice", si_doc.name, {
                        "custom_quickbooks_bill_id": result.get("id"),
                        "status": "Invoiced"
                    })

                synced.append(si_doc.name)

            else:
                raise Exception("QuickBooks sync failed or returned no ID")

        except Exception as e:
            row.status = "Failed"
            row.quickbooks_invoice_status = str(e)
            row.is_synced = 0
            failed.append(si)

            frappe.log_error(
                title="Sync Invoice - Failed",
                message=f"Invoice: {si}\nError: {frappe.get_traceback()}"
            )

    doc.save(ignore_permissions=True)
    refresh_purchase_invoices(docname)

    summary = {
        "synced": synced,
        "failed": failed,
        "skipped": skipped
    }

    frappe.log_error(
        title="Sync Selected Purchase Invoices - Summary",
        message=f"Docname: {docname}\nSummary: {frappe.as_json(summary)}"
    )

    return summary

@frappe.whitelist()
def sync_single_purchase_invoice_to_quickbooks(purchase_invoice_name):
    refresh_quickbooks_access_token()
    """Sync a specific Purchase Invoice to QuickBooks as a Bill or Vendor Credit on Submit."""

    invoice = frappe.get_doc("Purchase Invoice", purchase_invoice_name)

    if invoice.get("custom_quickbooks_bill_id"):
        frappe.msgprint(f"Purchase Invoice {purchase_invoice_name} is already synced with QuickBooks.")
        return

    # 🔹 If it's a return invoice, create Vendor Credit
    if invoice.is_return:
        frappe.log_error(
            title="QuickBooks Vendor Credit Sync - Triggered",
            message=f"Purchase Invoice {invoice.name} detected as Return. Creating Vendor Credit..."
        )
        return sync_debit_note_to_quickbooks(invoice)

    # Otherwise, continue normal Bill creation
    return create_quickbooks_bill(invoice)


def create_quickbooks_bill(invoice):
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
        description = item.item_name or item.item_code
        line_items.append({
            "DetailType": "ItemBasedExpenseLineDetail",
            "Amount": float(item.amount),
            "Description": description,
            "ItemBasedExpenseLineDetail": {
                "ItemRef": {"value": "3446"},
                "Qty": float(item.qty),
                "UnitPrice": float(item.rate),
                "TaxCodeRef": {"value": "11"}
            }
        })

    doc_number = frappe.db.get_value("Purchase Order", invoice.items[0].purchase_order, "custom_old_id") or invoice.name

    payload = {
        "DocNumber": doc_number,
        "VendorRef": {"value": vendor_qb_id},
        "TxnDate": str(invoice.posting_date),
        "Line": line_items
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    bill_url = f"{base_url}/bill?minorversion={minor_version}"

    try:
        frappe.log_error("QuickBooks Bill Sync - Request", f"{invoice.name}\nPayload: {frappe.as_json(payload)}")
        response = requests.post(bill_url, headers=headers, json=payload)
        response_json = response.json()
        frappe.log_error("QuickBooks Bill Sync - Response", f"{invoice.name}\n{frappe.as_json(response_json)}")
    except Exception as e:
        frappe.log_error("QuickBooks Bill Sync - Exception", frappe.get_traceback())
        frappe.throw(f"QuickBooks sync failed due to a request error: {str(e)}")

    if response.status_code == 200 and "Bill" in response_json:
        qb_bill_id = response_json["Bill"]["Id"]
        frappe.db.set_value("Purchase Invoice", invoice.name, "custom_quickbooks_bill_id", qb_bill_id)
        frappe.db.commit()
        frappe.msgprint(f"Purchase Invoice {invoice.name} synced as Bill in QuickBooks. ID: {qb_bill_id}")
        return {"id": qb_bill_id}
    else:
        frappe.throw(f"QuickBooks sync failed. Response: {response.text}")



# @frappe.whitelist()
# def sync_single_purchase_invoice_to_quickbooks(purchase_invoice_name):
#     refresh_quickbooks_access_token()
#     """Sync a specific Purchase Invoice to QuickBooks as a Bill on Submit."""

#     invoice = frappe.get_doc("Purchase Invoice", purchase_invoice_name)

#     if invoice.get("custom_quickbooks_bill_id"):
#         frappe.msgprint(f"Purchase Invoice {purchase_invoice_name} is already synced with QuickBooks.")
#         return

#     settings = frappe.get_single("QuickBooks Settings")
#     access_token = settings.access_token
#     realm_id = settings.quickbooks_company_id
#     minor_version = settings.minor_version or "75"
#     base_url = f"https://{settings.base_url.replace('https://', '').strip('/')}/v3/company/{realm_id}"

#     # Vendor mapping
#     vendor_qb_id = frappe.db.get_value("Supplier", invoice.supplier, "custom_quickbooks_supplier_id")
#     if not vendor_qb_id:
#         frappe.throw(f"QuickBooks Vendor ID not found for Supplier: {invoice.supplier}.")

#     line_items = []

#     for item in invoice.items:
#         description = item.item_name or item.item_code

#         line_items.append({
#             "DetailType": "ItemBasedExpenseLineDetail",
#             "Amount": float(item.amount),
#             "Description": description,
#             "ItemBasedExpenseLineDetail": {
#                 "ItemRef": {"value": "3446"},
#                 "Qty": float(item.qty),
#                 "UnitPrice": float(item.rate),
#                 "TaxCodeRef": {"value": "11"}
#             }
#         })

#     # --- 🔍 Find connected Purchase Order and its custom_old_id ---
#     connected_po_id = None
#     custom_old_id = None

#     # The Purchase Invoice might have links to PO in items
#     for item in invoice.items:
#         if item.purchase_order:
#             connected_po_id = item.purchase_order
#             break

#     if connected_po_id:
#         custom_old_id = frappe.db.get_value("Purchase Order", connected_po_id, "custom_old_id")

#     # --- 🧠 Choose DocNumber intelligently ---
#     doc_number = custom_old_id if custom_old_id else invoice.name

#     payload = {
#         "DocNumber": doc_number,
#         "VendorRef": {"value": vendor_qb_id},
#         "TxnDate": str(invoice.posting_date),
#         "Line": line_items
#     }

#     headers = {
#         "Authorization": f"Bearer {access_token}",
#         "Content-Type": "application/json",
#         "Accept": "application/json"
#     }

#     bill_url = f"{base_url}/bill?minorversion={minor_version}"

#     try:
#         frappe.log_error(
#             title="QuickBooks Bill Sync - Request",
#             message=f"Invoice: {invoice.name}\nPayload: {frappe.as_json(payload)}"
#         )

#         response = requests.post(bill_url, headers=headers, json=payload)

#         try:
#             response_json = response.json()
#         except Exception:
#             response_json = {"raw_text": response.text}

#         frappe.log_error(
#             title="QuickBooks Bill Sync - Response",
#             message=f"Invoice: {invoice.name}\nStatus: {response.status_code}\nResponse: {frappe.as_json(response_json)}"
#         )

#     except Exception as e:
#         frappe.log_error(
#             title="QuickBooks Bill Sync - Exception",
#             message=f"Invoice: {invoice.name}\nError: {frappe.get_traceback()}"
#         )
#         frappe.throw(f"QuickBooks sync failed due to a request error: {str(e)}")

#     if response.status_code == 200 and "Bill" in response_json:
#         qb_bill_id = response_json["Bill"]["Id"]
#         frappe.db.set_value("Purchase Invoice", invoice.name, "custom_quickbooks_bill_id", qb_bill_id)
#         frappe.db.commit()
#         frappe.msgprint(f"Purchase Invoice {invoice.name} synced as Bill in QuickBooks. ID: {qb_bill_id}")
#     else:
#         frappe.throw(f"QuickBooks sync failed. Response: {response.text}")
def sync_debit_note_to_quickbooks(invoice):
    """Sync a return Purchase Invoice to QuickBooks as a Vendor Credit."""

    settings = frappe.get_single("QuickBooks Settings")
    access_token = settings.access_token
    realm_id = settings.quickbooks_company_id
    minor_version = settings.minor_version or "75"
    base_url = f"https://{settings.base_url.replace('https://', '').strip('/')}/v3/company/{realm_id}"

    vendor_qb_id = frappe.db.get_value("Supplier", invoice.supplier, "custom_quickbooks_supplier_id")
    if not vendor_qb_id:
        frappe.throw(f"QuickBooks Vendor ID not found for Supplier: {invoice.supplier}.")

    # 🔹 Use ItemBasedExpenseLineDetail instead
    line_items = []
    for item in invoice.items:
        amount = abs(float(item.amount))
        qty = abs(float(item.qty))
        rate = abs(float(item.rate))

        line_items.append({
            "DetailType": "ItemBasedExpenseLineDetail",
            "Amount": amount,
            "Description": item.item_name or item.item_code,
            "ItemBasedExpenseLineDetail": {
                "ItemRef": {"value": frappe.db.get_value("Item", item.item_code, "custom_quickbooks_item_id") or "3446"},
                "Qty": qty,
                "UnitPrice": rate,
                "TaxCodeRef": {"value": "TAX"}
            }
        })

    payload = {
        "VendorRef": {"value": vendor_qb_id},
        "TxnDate": str(invoice.posting_date),
        "TotalAmt": abs(float(invoice.grand_total)),
        "Line": line_items
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    vendor_credit_url = f"{base_url}/vendorcredit?minorversion={minor_version}"

    response = requests.post(vendor_credit_url, headers=headers, json=payload)
    try:
        response_json = response.json()
    except:
        response_json = {"raw": response.text}

    frappe.log_error("QuickBooks Vendor Credit Sync - Response", f"{invoice.name}\n{frappe.as_json(response_json)}")

    if response.status_code == 200 and "VendorCredit" in response_json:
        qb_credit_id = response_json["VendorCredit"]["Id"]
        frappe.db.set_value("Purchase Invoice", invoice.name, {
            "custom_quickbooks_debitnote_id": qb_credit_id,
        })
        frappe.db.commit()
        frappe.msgprint(f"Return Invoice {invoice.name} synced as Vendor Credit in QuickBooks. ID: {qb_credit_id}")
        return {"id": qb_credit_id}
    else:
        frappe.throw(f"Vendor Credit sync failed. Response: {response.text}")


@frappe.whitelist()
def start_item_images_sync_background():
    """Enqueue item images sync job to run in background."""
    refresh_quickbooks_access_token()

    settings = frappe.get_doc("QuickBooks Settings")
    if not settings.enable:
        frappe.msgprint(
            'Navigate to Quickbooks Settings & Please enable QuickBooks Integration to continue',
            title="QuickBooks Integration Disabled",
            indicator="red"
        )
        return

    frappe.enqueue(sync_quickbooks_item_images, queue='long', timeout=1800)  # 30 minutes timeout
    frappe.msgprint("Item images sync from QuickBooks has been started in the background.")


@frappe.whitelist(allow_guest = True)
def sync_quickbooks_item_images():
    """
    Fetch item attachments (images) from QuickBooks Online and store them in ERPNext File doctype.
    The attachment must already exist in QBO as an Attachable linked to an Item.
    Supports pagination to fetch all images.

    Mapping:
       QBO Item.Id  -->  ERP Item.custom_quickbooks_item_id
       QBO Attachable.Download URL --> ERPNext File saved + Linked to Item
    """
    frappe.logger().info("[QB SYNC] Started item images sync job")

    # --- Refresh Token First ---
    refresh_quickbooks_access_token()

    settings = frappe.get_single("QuickBooks Settings")
    access_token = settings.access_token
    realm_id = settings.quickbooks_company_id
    base_url = settings.base_url.rstrip("/")
    minor_version = settings.minor_version or "75"

    if not access_token or not realm_id:
        frappe.log_error("Missing QuickBooks access token or company ID", "QuickBooks Item Images Sync Failed")
        return

    imported = []
    skipped = []
    start_position = 1
    max_results = 50  # Smaller batches to avoid timeouts

    # -----------------------------------------------------------------------------------
    # FETCH ALL ATTACHABLES WITH PAGINATION
    # -----------------------------------------------------------------------------------
    while True:
        # Query attachables linked to Items with pagination
        query = f"SELECT * FROM Attachable WHERE AttachableRef.EntityRef.type = 'Item' STARTPOSITION {start_position} MAXRESULTS {max_results}"
        url = f"{base_url}/v3/company/{realm_id}/query?query={query.replace(' ', '%20')}&minorversion={minor_version}"

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json"
        }

        try:
            res = requests.get(url, headers=headers, timeout=60)
            res.raise_for_status()
            data = res.json()
        except requests.exceptions.Timeout:
            frappe.log_error(f"QBO Item Image Fetch Timeout at position {start_position}", "QuickBooks Item Images Sync Timeout")
            # Retry once
            try:
                res = requests.get(url, headers=headers, timeout=120)
                res.raise_for_status()
                data = res.json()
            except Exception as e:
                frappe.log_error(f"QBO Item Image Fetch Retry Failed: {str(e)}", "QuickBooks Item Images Sync Failed")
                break
        except Exception as e:
            frappe.log_error(f"QBO Item Image Fetch Failed: {str(e)}", "QuickBooks Item Images Sync Failed")
            break

        attachables = data.get("QueryResponse", {}).get("Attachable", [])
        if not attachables:
            break  # No more attachables

        frappe.logger().info(f"[QB SYNC] Fetched {len(attachables)} attachables starting from {start_position}.")

        # -----------------------------------------------------------------------------------
        # PROCESS EACH ATTACHABLE
        # -----------------------------------------------------------------------------------
        for attach in attachables:
            try:
                file_name = attach.get("FileName")
                content_type = attach.get("ContentType")
                attach_id = attach.get("Id")

                if not file_name or not attach_id:
                    skipped.append({"id": attach_id or "unknown", "reason": "missing filename or id"})
                    continue

                # Validate metadata
                refs = attach.get("AttachableRef", [])
                if not refs:
                    skipped.append({"id": attach_id, "reason": "no refs"})
                    continue

                ref = refs[0] if isinstance(refs, list) else refs
                entity_ref = ref.get("EntityRef", {}) if isinstance(ref, dict) else {}
                
                if entity_ref.get("type") != "Item":
                    skipped.append({"id": attach_id, "reason": "not item"})
                    continue

                qbo_item_id = entity_ref.get("value")
                if not qbo_item_id:
                    skipped.append({"id": attach_id, "reason": "no item id"})
                    continue

                # Lookup ERPNext Item by QuickBooks Item ID
                erp_item_name = frappe.db.get_value(
                    "Item",
                    {"custom_quickbooks_item_id": qbo_item_id},
                    "name"
                )

                if not erp_item_name:
                    skipped.append({"id": attach_id, "reason": f"ERPNext item not found for QBO ID {qbo_item_id}"})
                    continue

                # Check if file already exists for this item
                existing_file = frappe.db.exists(
                    "File",
                    {
                        "file_name": file_name,
                        "attached_to_doctype": "Item",
                        "attached_to_name": erp_item_name
                    }
                )
                if existing_file:
                    skipped.append({"id": attach_id, "reason": "file already exists"})
                    continue

                # -----------------------------------------------------------------------------------
                # DOWNLOAD THE FILE FROM QUICKBOOKS
                # -----------------------------------------------------------------------------------
                download_url = f"{base_url}/v3/company/{realm_id}/download/{attach_id}"

                try:
                    # Use longer timeout for file downloads (images can be large)
                    file_res = requests.get(
                        download_url,
                        headers={"Authorization": f"Bearer {access_token}"},
                        timeout=120,  # 2 minutes for large images
                        stream=True  # Stream download for better memory handling
                    )
                    file_res.raise_for_status()
                    # Read content after successful response
                    file_content = file_res.content
                except requests.exceptions.Timeout:
                    skipped.append({"id": attach_id, "reason": "download timeout"})
                    frappe.logger().warn(f"[QB SYNC] Timeout downloading image {file_name} (ID: {attach_id})")
                    continue
                except Exception as e:
                    skipped.append({"id": attach_id, "reason": f"download failed: {str(e)}"})
                    continue

                # -----------------------------------------------------------------------------------
                # SAVE FILE TO ERPNext PROPERLY
                # -----------------------------------------------------------------------------------
                try:
                    # Sanitize file name
                    safe_file_name = file_name.replace(" ", "_").replace("/", "_")
                    
                    # Get file path using frappe's utility
                    file_path = frappe.utils.get_files_path(safe_file_name, is_private=0)
                    
                    # Ensure directory exists
                    os.makedirs(os.path.dirname(file_path), exist_ok=True)
                    
                    # Write file content to disk
                    with open(file_path, "wb") as f:
                        f.write(file_content)
                    
                    # Calculate file size and hash (use content already in memory)
                    file_size = len(file_content)
                    content_hash = hashlib.md5(file_content).hexdigest()
                    
                    # Create File document with proper fields
                    file_doc = frappe.get_doc({
                        "doctype": "File",
                        "file_name": safe_file_name,
                        "file_url": f"/files/{safe_file_name}",
                        "is_private": 0,
                        "file_size": file_size,
                        "content_hash": content_hash,
                        "attached_to_doctype": "Item",
                        "attached_to_name": erp_item_name
                    })
                    file_doc.insert(ignore_permissions=True)
                    
                    # Update item's image field if it's an image and item doesn't have one
                    if content_type and content_type.startswith("image/"):
                        item_doc = frappe.get_doc("Item", erp_item_name)
                        if not item_doc.image:
                            item_doc.image = file_doc.file_url
                            item_doc.save(ignore_permissions=True)
                    
                    # Commit after each file to avoid long transactions
                    frappe.db.commit()

                    imported.append({
                        "item": erp_item_name,
                        "filename": safe_file_name,
                        "qbo_attach_id": attach_id
                    })
                    
                    frappe.logger().info(f"[QB SYNC] Imported image {safe_file_name} for item {erp_item_name}")
                    
                    # Small delay to avoid overwhelming the system
                    time.sleep(0.1)

                except Exception as e:
                    frappe.log_error(
                        f"File save failed for {file_name}: {str(e)}\n{frappe.get_traceback()}",
                        "QuickBooks Item Image Save Failed"
                    )
                    skipped.append({"id": attach_id, "reason": f"file save failed: {str(e)}"})

            except Exception as e:
                frappe.log_error(
                    f"Error processing attachable {attach.get('Id', 'unknown')}: {str(e)}",
                    "QuickBooks Item Image Processing Error"
                )
                skipped.append({"id": attach.get("Id", "unknown"), "reason": f"processing error: {str(e)}"})

        # Check if we need to fetch more
        if len(attachables) < max_results:
            break  # Last page reached

        start_position += max_results

    # -----------------------------------------------------------------------------------
    # SUMMARY OUTPUT
    # -----------------------------------------------------------------------------------
    frappe.logger().info(
        f"[QB SYNC] Completed item images sync job. Imported: {len(imported)}, Skipped: {len(skipped)}"
    )
    frappe.db.commit()

    return {
        "message": f"Item image sync completed. Imported: {len(imported)}, Skipped: {len(skipped)}",
        "imported_count": len(imported),
        "skipped_count": len(skipped),
        "imported": imported[:10],  # Return first 10 for display
        "skipped": skipped[:10]  # Return first 10 for display
    }
