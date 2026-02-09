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



# @frappe.whitelist()
# def fetch_quickbooks_customer_statement(**kwargs):
#     """
#     Fetch the Customer Balance Detail report from QuickBooks and return it in ERP-style format.
#     """

#     def normalize_key(raw_key, fallback):
#         key = raw_key or fallback or ""
#         return key.strip().lower().replace(" ", "_").replace("*", "")

#     def get_column_keys(report_json):
#         keys = []
#         columns_data = report_json.get("Columns", {})

#         # Handle different column structures
#         if isinstance(columns_data, dict):
#             columns = columns_data.get("Column", [])
#             if not columns:
#                 columns = columns_data.get("column", [])  # Try lowercase
#         elif isinstance(columns_data, list):
#             columns = columns_data
#         else:
#             columns = []

#         if not columns:
#             # Fallback: try to infer from header
#             header = report_json.get("Header", {})
#             if header:
#                 # Use common column names for Customer Balance Detail report
#                 keys = ["date", "transaction_type", "doc_num", "customer", "due_date", "amount", "balance"]

#         for idx, column in enumerate(columns):
#             if isinstance(column, dict):
#                 meta = None
#                 metadata = column.get("MetaData", [])
#                 if metadata:
#                     for md in metadata:
#                         if isinstance(md, dict) and md.get("Name") == "ColKey":
#                             meta = md.get("Value")
#                             break

#                 col_title = column.get("ColTitle") or column.get("col_title") or column.get("title") or ""
#                 col_type = column.get("ColType") or column.get("col_type") or ""

#                 # Normalize the key
#                 normalized = normalize_key(meta, col_title or f"col_{idx}")

#                 # Map common QuickBooks column titles to standard names
#                 col_lower = col_title.lower()
#                 if "date" in col_lower and "due" not in col_lower:
#                     normalized = "date"
#                 elif "due" in col_lower:
#                     normalized = "due_date"
#                 elif "type" in col_lower or "transaction" in col_lower:
#                     normalized = "transaction_type"
#                 elif "num" in col_lower or "doc" in col_lower or "ref" in col_lower:
#                     normalized = "doc_num"
#                 elif "amount" in col_lower and "balance" not in col_lower:
#                     normalized = "amount"
#                 elif "balance" in col_lower:
#                     normalized = "balance"
#                 elif "memo" in col_lower or "description" in col_lower:
#                     normalized = "memo"

#                 keys.append(normalized)
#             else:
#                 keys.append(f"col_{idx}")

#         return keys if keys else [f"col_{i}" for i in range(10)]  # Default fallback

#     def coldata_to_dict(coldata, keys):
#         record = {}
#         if not coldata:
#             return record

#         for idx, col in enumerate(coldata):
#             if not isinstance(col, dict):
#                 continue

#             key = keys[idx] if idx < len(keys) else f"col_{idx}"

#             # Extract value - handle different formats
#             value = col.get("value")
#             if value is None:
#                 value = col.get("Value")  # Try capitalized
#             if value is None:
#                 value = col.get("text") or col.get("Text")

#             if value not in (None, ""):
#                 # Clean up value - remove currency symbols and whitespace
#                 if isinstance(value, str):
#                     value = value.strip()
#                 record[key] = value

#             # Extract ID if present (this is crucial for transaction type detection)
#             col_id = col.get("id") or col.get("Id") or col.get("ID")
#             if col_id:
#                 record[f"{key}_id"] = col_id
#                 # Also store as generic id for transaction identification
#                 if idx == 0:  # Usually first column has transaction ID
#                     record["transaction_id"] = col_id

#             # Extract href if present (contains transaction type info)
#             href = col.get("href") or col.get("Href")
#             if href:
#                 record[f"{key}_href"] = href
#                 # Extract transaction type from href if available
#                 if "invoice" in href.lower() and "transaction_type" not in record:
#                     record["transaction_type"] = "invoice"
#                 elif "creditmemo" in href.lower() or "credit" in href.lower():
#                     record["transaction_type"] = "creditmemo"
#                 elif "payment" in href.lower():
#                     record["transaction_type"] = "payment"

#         return record

#     def flatten_rows(row_items, keys, bucket, summary_bucket):
#         if not row_items:
#             return

#         # Handle both list and single row
#         if not isinstance(row_items, list):
#             row_items = [row_items]

#         for row in row_items:
#             if not isinstance(row, dict):
#                 continue

#             # Handle both lowercase and capitalized type values
#             row_type = (row.get("type") or "").lower()
#             col_data = row.get("ColData", [])

#             # Process data rows (actual transactions)
#             if row_type == "data":
#                 record = coldata_to_dict(col_data, keys)
#                 if record:  # Only add if we have data
#                     bucket.append(record)
#             # Process summary rows (totals, opening balance, etc.)
#             elif row_type == "summary":
#                 record = coldata_to_dict(col_data, keys)
#                 if record:  # Only add if we have data
#                     summary_bucket.append(record)
#             # If type is not specified but has ColData, treat as data row
#             elif col_data and not row_type:
#                 record = coldata_to_dict(col_data, keys)
#                 if record:
#                     bucket.append(record)

#             # Recursively process nested rows
#             nested_rows = None
#             if row.get("Rows"):
#                 nested_data = row["Rows"]
#                 if isinstance(nested_data, dict):
#                     nested_rows = nested_data.get("Row", [])
#                 elif isinstance(nested_data, list):
#                     nested_rows = nested_data
#             elif row.get("Row"):
#                 nested_rows = row.get("Row")

#             if nested_rows:
#                 flatten_rows(nested_rows, keys, bucket, summary_bucket)

#     def resolve_erp_customer(identifier):
#         if not identifier:
#             return None

#         filters = {"custom_quickbooks_customer_id": identifier}
#         doc = frappe.db.get_value("Customer", filters, ["name", "customer_name", "custom_quickbooks_customer_id"], as_dict=True)
#         if doc:
#             return doc

#         if frappe.db.exists("Customer", identifier):
#             return frappe.db.get_value("Customer", identifier, ["name", "customer_name", "custom_quickbooks_customer_id"], as_dict=True)

#         return None

#     def get_contact_details(erp_customer):
#         if not erp_customer:
#             return {"contact_name": None, "phone": None, "email": None, "address": None}

#         contact_name = None
#         phone = None
#         email = None
#         address_text = None

#         primary_contact = frappe.db.get_value(
#             "Dynamic Link",
#             {
#                 "link_doctype": "Customer",
#                 "link_name": erp_customer.name,
#                 "parenttype": "Contact",
#             },
#             "parent",
#         )

#         if primary_contact:
#             contact_doc = frappe.get_doc("Contact", primary_contact)
#             contact_name = contact_doc.first_name or contact_doc.name
#             phone = contact_doc.mobile_no or contact_doc.phone
#             email = contact_doc.email_id

#         address_link = frappe.db.get_value(
#             "Dynamic Link",
#             {
#                 "link_doctype": "Customer",
#                 "link_name": erp_customer.name,
#                 "parenttype": "Address",
#             },
#             "parent",
#         )

#         if address_link:
#             address_doc = frappe.get_doc("Address", address_link)
#             address_text = ", ".join(filter(None, [address_doc.address_line1, address_doc.city, address_doc.country]))

#         return {
#             "contact_name": contact_name,
#             "phone": phone,
#             "email": email,
#             "address": address_text,
#         }

#     def build_erp_response(report_json, customer_info, contact_info, start_date, end_date):
#         column_keys = get_column_keys(report_json)
#         transactions, summaries = [], []

#         # Handle different Rows structures from QuickBooks API
#         rows_data = report_json.get("Rows", {})
#         if not rows_data:
#             rows_data = report_json.get("rows", {})  # Try lowercase

#         # Extract Row array from Rows container
#         row_items = None
#         if isinstance(rows_data, dict):
#             row_items = rows_data.get("Row", [])
#             if not row_items:
#                 row_items = rows_data.get("row", [])  # Try lowercase
#         elif isinstance(rows_data, list):
#             row_items = rows_data

#         if not row_items:
#             # If no rows found, log for debugging
#             frappe.logger().debug(f"[QuickBooks] No rows found in report. Structure: {list(report_json.keys())}")

#         flatten_rows(row_items or [], column_keys, transactions, summaries)

#         # Debug logging (can be removed in production if needed)
#         frappe.logger().debug(f"[QuickBooks] Parsed {len(transactions)} transactions, {len(summaries)} summaries")
#         if transactions:
#             frappe.logger().debug(f"[QuickBooks] Sample transaction keys: {list(transactions[0].keys())}")

#         currency = report_json.get("Header", {}).get("Currency") or "AUD"

#         def pick(record, *keys):
#             for key in keys:
#                 if key in record and record[key] not in (None, ""):
#                     return record[key]
#             return ""

#         invoices, credit_notes, payments = [], [], []
#         opening_balance = 0.0

#         # Process summary rows first to get opening balance
#         for summary in summaries:
#             summary_type = (pick(summary, "col_0", "col_1") or "").lower()
#             balance_value = flt(pick(summary, "balance", "open_balance", "amount", "col_2", "col_3", "col_4"))

#             # Look for opening balance in summary rows
#             if "opening" in summary_type or "beginning" in summary_type:
#                 opening_balance = balance_value
#             elif "total" in summary_type and balance_value and opening_balance == 0:
#                 # Sometimes opening balance is in a total row
#                 opening_balance = balance_value

#         # Process transactions
#         for txn in transactions:
#             # Try multiple field name variations for transaction type
#             # First check if we extracted it from href
#             txn_type_raw = pick(txn, "transaction_type", "txn_type", "type", "col_0", "col_1") or ""
#             txn_type = str(txn_type_raw).lower().strip()

#             # Try multiple field name variations for document number
#             doc_num = pick(txn, "doc_num", "docnum", "txn_id", "num", "transaction_id", "col_1", "col_2", "col_0")

#             # Try multiple field name variations for dates
#             tx_date = pick(txn, "tx_date", "date", "transaction_date", "col_0", "col_1")
#             due_date = pick(txn, "due_date", "due", "col_2", "col_3")

#             memo = pick(txn, "memo", "cust_msg", "description", "col_4", "col_5")

#             # Try multiple field name variations for amounts
#             # In Customer Balance Detail report, amounts are typically in later columns
#             amount = flt(pick(txn, "subt_amount", "amount", "total_amount", "col_3", "col_4", "col_5", "col_6"))
#             balance = flt(pick(txn, "balance", "open_balance", "amount_due", "outstanding_amount", "col_4", "col_5", "col_6", "col_7"))

#             # If amount is 0 but balance has value, use balance
#             if amount == 0 and balance != 0:
#                 amount = abs(balance)

#             # Skip if no meaningful data (but be more lenient - if we have a date or doc_num, include it)
#             if not doc_num and amount == 0 and balance == 0 and not tx_date:
#                 continue

#             # Determine transaction type more reliably
#             # Check href first (most reliable)
#             for key in txn.keys():
#                 if "href" in key.lower() and txn[key]:
#                     href = str(txn[key]).lower()
#                     if "invoice" in href and not txn_type:
#                         txn_type = "invoice"
#                     elif ("creditmemo" in href or "credit" in href) and not txn_type:
#                         txn_type = "creditmemo"
#                     elif "payment" in href and not txn_type:
#                         txn_type = "payment"
#                     break

#             # If still no type, try to infer from document number or transaction type field
#             if not txn_type or txn_type == "":
#                 # Check transaction type column values
#                 type_value = str(pick(txn, "col_0", "col_1", "col_2") or "").lower()
#                 if "invoice" in type_value or "inv" in type_value:
#                     txn_type = "invoice"
#                 elif "credit" in type_value or "memo" in type_value:
#                     txn_type = "creditmemo"
#                 elif "payment" in type_value or "pay" in type_value:
#                     txn_type = "payment"
#                 # Try to infer from document number
#                 elif doc_num:
#                     doc_str = str(doc_num).lower()
#                     if "invoice" in doc_str or "inv" in doc_str or "si-" in doc_str:
#                         txn_type = "invoice"
#                     elif "credit" in doc_str or "cn-" in doc_str or "cm-" in doc_str:
#                         txn_type = "creditmemo"
#                     elif "payment" in doc_str or "pay-" in doc_str or "pmt" in doc_str:
#                         txn_type = "payment"

#             # If we still can't determine type, check amount sign
#             # Invoices are typically positive, payments negative, credit memos can be either
#             if not txn_type or txn_type == "":
#                 if amount < 0:
#                     txn_type = "payment"
#                 elif amount > 0:
#                     # Default to invoice if positive (most common)
#                     txn_type = "invoice"

#             # Categorize transactions
#             if txn_type in ("invoice", "salesreceipt", "sales_receipt", "inv", "sales invoice"):
#                 outstanding = abs(balance) if balance != 0 else abs(amount)
#                 invoices.append({
#                     "invoice_id": doc_num or "",
#                     "posting_date": tx_date or "",
#                     "due_date": due_date or "",
#                     "grand_total": abs(amount) if amount != 0 else abs(outstanding),
#                     "outstanding_amount": outstanding,
#                     "status": "Paid" if outstanding == 0 else "Unpaid",
#                     "currency": currency,
#                     "payment_status": "Paid" if outstanding == 0 else "Unpaid",
#                 })
#             elif txn_type in ("creditmemo", "credit_memo", "credit", "credit memo", "cm"):
#                 remaining_credit = abs(balance) if balance != 0 else abs(amount)
#                 credit_notes.append({
#                     "credit_note_no": doc_num or "",
#                     "posting_date": tx_date or "",
#                     "status": "Closed" if remaining_credit == 0 else "Open",
#                     "total": abs(amount) if amount != 0 else abs(remaining_credit),
#                     "remaining_credit": remaining_credit,
#                     "currency": currency,
#                 })
#             elif txn_type in ("payment", "receivepayment", "sales_payment", "payment received", "pmt"):
#                 # Payments are typically negative in QuickBooks
#                 paid_amount = abs(amount) if amount != 0 else abs(balance)
#                 payments.append({
#                     "payment_id": doc_num or "",
#                     "posting_date": tx_date or "",
#                     "paid_amount": paid_amount,
#                     "received_amount": paid_amount,
#                     "payment_type": "Receive",
#                     "mode_of_payment": pick(txn, "ship_via", "payment_method"),
#                     "reference_no": memo,
#                 })
#             else:
#                 # If we can't categorize, log it but don't skip (might be a new transaction type)
#                 frappe.logger().debug(f"[QuickBooks] Unrecognized transaction type: {txn_type}, doc_num: {doc_num}, amount: {amount}")
#                 # Default to invoice for unknown types with positive amounts
#                 if amount > 0:
#                     outstanding = abs(balance) if balance != 0 else abs(amount)
#                     invoices.append({
#                         "invoice_id": doc_num or "",
#                         "posting_date": tx_date or "",
#                         "due_date": due_date or "",
#                         "grand_total": abs(amount) if amount != 0 else abs(outstanding),
#                         "outstanding_amount": outstanding,
#                         "status": "Paid" if outstanding == 0 else "Unpaid",
#                         "currency": currency,
#                         "payment_status": "Paid" if outstanding == 0 else "Unpaid",
#                     })

#         # Calculate totals
#         total_invoices = sum(flt(inv["grand_total"]) for inv in invoices)
#         total_credit_notes = sum(flt(note["total"]) for note in credit_notes)
#         total_payments = sum(flt(pay["paid_amount"]) for pay in payments)

#         # Debug logging
#         frappe.logger().debug(
#             f"[QuickBooks] Categorized: {len(invoices)} invoices, {len(credit_notes)} credit notes, "
#             f"{len(payments)} payments from {len(transactions)} total transactions"
#         )

#         # Calculate closing balance: opening + invoices - credit notes - payments
#         closing_balance = opening_balance + total_invoices - total_credit_notes - total_payments

#         return {
#             "status": "success",
#             "customer": (customer_info.name if customer_info else None),
#             "customer_name": (customer_info.customer_name if customer_info else report_json.get("Header", {}).get("Customer")),
#             "company": frappe.defaults.get_global_default("company"),
#             "from_date": start_date,
#             "to_date": end_date,
#             "contact_details": contact_info,
#             "invoices": invoices,
#             "credit_notes": credit_notes,
#             "payments": payments,
#             "summary": {
#                 "currency": currency,
#                 "opening_balance": opening_balance,
#                 "total_invoices": total_invoices,
#                 "total_payments": total_payments * -1,
#                 "credit_notes": total_credit_notes * -1,
#                 "closing_balance": closing_balance,
#             },
#         }

#     try:
#         refresh_quickbooks_access_token()
#     except Exception:
#         frappe.logger().warning("[QuickBooks] Unable to refresh access token, attempting with existing token.")

#     settings = frappe.get_single("QuickBooks Settings")

#     if not settings.enable:
#         frappe.throw(_("QuickBooks integration is disabled. Please enable it in QuickBooks Settings."))

#     access_token = settings.access_token
#     realm_id = settings.quickbooks_company_id
#     base_url = (settings.base_url or "").strip().rstrip("/")

#     if not access_token or not realm_id or not base_url:
#         frappe.throw(_("QuickBooks settings are incomplete. Please provide access token, company ID, and base URL."))

#     if not base_url.startswith("http"):
#         base_url = f"https://{base_url}"

#     allowed_params = [
#         "customer",
#         "shipvia",
#         "term",
#         "end_duedate",
#         "start_duedate",
#         "custom1",
#         "sort_by",
#         "arpaid",
#         "report_date",
#         "sort_order",
#         "aging_method",
#         "department",
#         "columns",
#     ]

#     request_params = {}
#     form_dict = frappe._dict(frappe.form_dict or {})

#     for param in allowed_params:
#         value = kwargs.get(param, form_dict.get(param))
#         if value in (None, "", []):
#             continue
#         request_params[param] = value

#     request_params["minorversion"] = settings.minor_version or "75"

#     customer_identifier = request_params.get("customer") or ""
#     start_date = request_params.get("start_duedate")
#     end_date = request_params.get("end_duedate")

#     if not customer_identifier:
#         frappe.throw(_("Customer parameter is required to fetch the statement."))

#     erp_customer = resolve_erp_customer(customer_identifier)
#     contact_details = get_contact_details(erp_customer)

#     query_string = urlencode(request_params, doseq=True)
#     endpoint = f"{base_url}/v3/company/{realm_id}/reports/CustomerBalanceDetail"
#     url = f"{endpoint}?{query_string}" if query_string else endpoint

#     headers = {
#         "Authorization": f"Bearer {access_token}",
#         "Accept": "application/json",
#         "Content-Type": "application/json"
#     }

#     try:
#         response = requests.get(url, headers=headers, timeout=30)
#         response.raise_for_status()
#         report_json = response.json()
#         return build_erp_response(report_json, erp_customer, contact_details, start_date, end_date)
#     except requests.RequestException as exc:
#         frappe.log_error(
#             message=f"{frappe.get_traceback()}\nURL: {url}\nDetails: {str(exc)}",
#             title="QuickBooks Customer Statement Fetch Failed"
#         )
#         frappe.throw(_("Unable to fetch customer statement from QuickBooks. Please try again later."))
#     except ValueError:
#         frappe.log_error(
#             message=f"{frappe.get_traceback()}\nURL: {url}\nDetails: Non-JSON response",
#             title="QuickBooks Customer Statement Invalid Response"
#         )
#         frappe.throw(_("QuickBooks returned an invalid response for the customer statement request."))

@frappe.whitelist()
def fetch_quickbooks_customer_statement(**kwargs):
    """
    Fetch a clean customer statement from QuickBooks Online using the TransactionList report.
    Returns invoices, payments, credit notes, and balances for the given period.
    """

    # ---------------------- Helpers ----------------------
    def qbo_get(url, token):
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def normalize(value):
        return (value or "").strip()

    def flt_safe(v):
        try:
            return flt(v)
        except Exception:
            return 0.0

    # Identify transaction type based on QBO keywords
    def detect_type(type_str, doc_num):
        s = (type_str or "").lower()
        d = (doc_num or "").lower()

        if "invoice" in s or "inv" in d or d.startswith("si-"):
            return "invoice"
        if "credit" in s or "memo" in s or "cm-" in d:
            return "creditmemo"
        if "payment" in s or "pmt" in s or d.startswith("pmt"):
            return "payment"
        return None

    # ---------------------- Read Settings ----------------------
    settings = frappe.get_single("QuickBooks Settings")
    if not settings.enable:
        frappe.throw("QuickBooks integration is disabled.")

    base_url = settings.base_url.rstrip("/")
    token = settings.access_token
    realm_id = settings.quickbooks_company_id
    customer = kwargs.get("customer")
    start_date = kwargs.get("start_date")
    end_date = kwargs.get("end_date")

    if not customer:
        frappe.throw("Customer parameter is required.")

    # Swap reversed periods
    if start_date and end_date and end_date < start_date:
        start_date, end_date = end_date, start_date

    # ---------------------- Resolve ERP customer ----------------------
    erp_customer = frappe.db.get_value(
        "Customer",
        {"custom_quickbooks_customer_id": customer},
        ["name", "customer_name"],
        as_dict=True,
    )

    # ---------------------- Build QBO request ----------------------
    params = {
        "customer": customer,
        "start_date": start_date,
        "end_date": end_date,
        "minorversion": settings.minor_version or "75",
    }

    qs = urlencode(params)
    url = f"{base_url}/v3/company/{realm_id}/reports/TransactionList?{qs}"

    # ---------------------- Fetch QBO Report ----------------------
    try:
        report = qbo_get(url, token)
    except Exception as e:
        frappe.throw(f"QuickBooks API error: {str(e)}")

    rows = report.get("Rows", {}).get("Row", [])
    currency = report.get("Header", {}).get("Currency", "AUD")

    invoices = []
    credit_notes = []
    payments = []
    opening_balance = 0.0

    # ---------------------- Parse Rows ----------------------
    for row in rows:
        if row.get("type") != "Data":
            continue

        cols = row.get("ColData", [])
        if len(cols) < 5:
            continue

        tx_date = normalize(cols[0].get("value"))
        tx_type = normalize(cols[1].get("value"))
        doc_num = normalize(cols[2].get("value"))
        amount = flt_safe(cols[4].get("value"))

        detected = detect_type(tx_type, doc_num)
        if not detected:
            continue

        # -------- Categorize --------
        if detected == "invoice":
            invoices.append({
                "invoice_id": doc_num,
                "posting_date": tx_date,
                "due_date": "",
                "grand_total": abs(amount),
                "outstanding_amount": abs(amount),
                "status": "Unpaid",
                "payment_status": "Unpaid",
                "currency": currency,
            })

        elif detected == "creditmemo":
            credit_notes.append({
                "credit_note_no": doc_num,
                "posting_date": tx_date,
                "total": abs(amount),
                "remaining_credit": abs(amount),
                "status": "Open",
                "currency": currency,
            })

        elif detected == "payment":
            payments.append({
                "payment_id": doc_num,
                "posting_date": tx_date,
                "paid_amount": abs(amount),
                "received_amount": abs(amount),
                "payment_type": "Receive",
                "mode_of_payment": None,
                "reference_no": None,
            })

    # ---------------------- Totals ----------------------
    total_invoices = sum(flt_safe(i["grand_total"]) for i in invoices)
    total_credits = sum(flt_safe(c["total"]) for c in credit_notes)
    total_payments = sum(flt_safe(p["paid_amount"]) for p in payments)

    closing_balance = opening_balance + total_invoices - total_credits - total_payments

    # ---------------------- Response ----------------------
    return {
        "status": "success",
        "customer": erp_customer.name if erp_customer else customer,
        "customer_name": erp_customer.customer_name if erp_customer else customer,
        "company": frappe.defaults.get_global_default("company"),
        "from_date": start_date,
        "to_date": end_date,
        "contact_details": {},
        "invoices": invoices,
        "credit_notes": credit_notes,
        "payments": payments,
        "summary": {
            "currency": currency,
            "opening_balance": opening_balance,
            "total_invoices": total_invoices,
            "total_payments": -total_payments,
            "credit_notes": -total_credits,
            "closing_balance": closing_balance,
        },
    }


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
    """Sync a single Sales Invoice to QuickBooks with global invoice-level discount"""
    import requests
    import json
    from frappe.utils import flt

    try:
        doc = frappe.get_doc("Sales Invoice", docname)

        # Validation checks
        if doc.docstatus == 2:
            frappe.msgprint("Cancelled Invoices are not allowed to sync")
            return {"error": "Cancelled invoice cannot be synced."}

        refresh_quickbooks_access_token()
        settings = frappe.get_doc("QuickBooks Settings")

        if not settings.enable:
            frappe.msgprint("Please Enable QuickBooks Settings")
            return {"error": "QuickBooks not enabled."}

        # QuickBooks endpoint
        url = f"{settings.base_url.strip().rstrip('/')}/v3/company/{settings.quickbooks_company_id}/invoice?minorversion={settings.minor_version or '75'}"
        headers = {
            "Authorization": f"Bearer {settings.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        # Customer
        customer = frappe.get_doc("Customer", doc.customer)
        qb_customer_id = customer.get("custom_quickbooks_customer_id") or "1"
        send_item = (settings.send_item) == 1

        # Helper: get tax code from Item master
        def get_tax_code(item_code):
            """Fetch tax code from Item master's tax template"""
            try:
                # Get item_tax_template from Item master (not from Sales Invoice Item)
                tax_template = frappe.db.get_value("Item", item_code, "item_tax_template")

                if not tax_template:
                    return "5"  # Default GST code

                # Get QuickBooks GST ID from the tax template
                tax_code = frappe.db.get_value(
                    "Item Tax Template",
                    tax_template,
                    "custom_quickbooks_gst_id"
                ) or "5"

                return tax_code
            except Exception:
                return "5"  # Default GST code on any error

        # Prepare line items
        line_items = []
        subtotal = 0.0
        tax_code_weights = {}  # Track amount per tax code

        for item in doc.items:
            amount = flt(item.amount)
            subtotal += amount

            # Get tax code from Item master using item_code
            item_tax_code = get_tax_code(item.item_code)

            # Track weight (accumulate amount per tax code)
            tax_code_weights[item_tax_code] = tax_code_weights.get(item_tax_code, 0.0) + amount

            detail = {
                "Qty": item.qty,
                "UnitPrice": flt(item.rate),
                "TaxCodeRef": {"value": item_tax_code}
            }

            # Add ItemRef if sending items to QB
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

        # Handle global invoice-level discount
        discount_amount = flt(doc.get("discount_amount"))
        additional_discount_percentage = flt(doc.get("additional_discount_percentage"))

        # Calculate discount if percentage exists but amount is zero
        if additional_discount_percentage > 0 and discount_amount == 0:
            discount_amount = round(subtotal * (additional_discount_percentage / 100.0), 2)

        # Determine discount tax code (dominant tax code from line items)
        discount_tax_code = "5"  # Default
        if tax_code_weights:
            discount_tax_code = max(tax_code_weights, key=tax_code_weights.get)

        # Add discount line if applicable
        apply_discount_on = doc.get("apply_discount_on") or "Grand Total"
        apply_tax_after_discount = True  # Default for "Net Total"

        if discount_amount > 0:
            if apply_discount_on == "Grand Total":
                # Discount after tax - use non-taxable code
                apply_tax_after_discount = False
                discount_tax_code = "4"  # Non-taxable

            discount_line = {
                "DetailType": "DiscountLineDetail",
                "Amount": discount_amount,
                "DiscountLineDetail": {
                    "PercentBased": False,
                    "TaxCodeRef": {"value": discount_tax_code}
                }
            }
            line_items.append(discount_line)

        # Build payload
        payload = {
            "DocNumber": doc.name,
            "TxnDate": str(doc.posting_date),
            "DueDate": str(doc.due_date or doc.posting_date),
            "CustomerRef": {"value": qb_customer_id, "name": doc.customer},
            "Line": line_items,
            "ApplyTaxAfterDiscount": apply_tax_after_discount,
            "CustomerMemo": {"value": "Generated from ERPNext"},
            "PrintStatus": "NeedToPrint",
            "EmailStatus": "NotSet"
        }

        # # LOG 1: Sync initiated with payload
        # frappe.log_error(
        #     title=f"QBO Sync Initiated - {docname}",
        #     message=f"PAYLOAD:\n{json.dumps(payload, indent=2, default=str)}"
        # )

        # Send request
        res = requests.post(url, headers=headers, data=json.dumps(payload))
        body = res.json() if res.text else {}

        # LOG 2: Sync completed with response
        frappe.log_error(
            title=f"QBO Sync Completed - {docname}",
            message=f"STATUS: {res.status_code}\n\nRESPONSE:\n{json.dumps(body, indent=2, default=str)}"
        )

        # Handle response
        if res.status_code in (200, 201):
            if body.get("Invoice"):
                qbo_id = body["Invoice"]["Id"]

                # Update only fields that exist
                doc.db_set("custom_quickbooks_invoice_id", qbo_id)
                doc.db_set("status", "Confirmed")
                # Removed: doc.db_set("is_synced", 1)
                frappe.db.commit()

                frappe.msgprint(f"Successfully synced to QuickBooks! QBO Invoice ID: {qbo_id}")
                return {"id": qbo_id, "response": body}
            else:
                return {"error": "Unexpected response format", "response": body}
        else:
            # Extract error messages
            fault = body.get("Fault", {})
            errors = fault.get("Error", [])
            error_messages = [
                f"Code: {error.get('code')}, Message: {error.get('Message')}, Detail: {error.get('Detail')}"
                for error in errors
            ]

            error_msg = error_messages[0] if error_messages else "Unknown error"
            frappe.msgprint(f"QuickBooks sync failed: {error_msg}")
            return {
                "error": f"Sync failed ({res.status_code})",
                "details": error_messages,
                "response": body
            }

    except Exception as e:
        import traceback
        frappe.log_error(
            title=f"QBO Sync Exception - {docname}",
            message=f"ERROR: {str(e)}\n\nTRACEBACK:\n{traceback.format_exc()}"
        )
        frappe.msgprint(f"Error during sync: {str(e)}")
        return {"error": str(e), "traceback": traceback.format_exc()}

def create_item_on_quickbooks(item_name):
    refresh_quickbooks_access_token()

    item_doc = frappe.get_doc("Item", item_name)

    if item_doc.custom_quickbooks_item_id:
        # Item already exists in QuickBooks, but still set custom_publish_on_app = 1 when synced
        if item_doc.custom_publish_on_app != 1:
            item_doc.custom_publish_on_app = 1
            item_doc.save(ignore_permissions=True)
            frappe.db.commit()
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
            item_doc.custom_publish_on_app = 1
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
    doc.sales_invoices_count = len(invoices)
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
    doc.purchase_invoices_count = len(invoices)
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

    frappe.enqueue(sync_quickbooks_item_images, queue='long', timeout=7200)  # 2 hours timeout for large batches
    frappe.msgprint("Item images sync from QuickBooks has been started in the background.")


@frappe.whitelist(allow_guest = True)
def sync_quickbooks_item_images():
    """
    Fetch item attachments (images) from QuickBooks Online and store them in ERPNext File doctype.

    Correct QuickBooks API flow:
    1. Query Attachables linked to Items
    2. For each Attachable, call /download/{attachableId} to get TempDownloadUri
    3. Download the actual image from TempDownloadUri
    4. Save to Frappe File system and link to Item

    Mapping:
       QBO Item.Id  -->  ERP Item.custom_quickbooks_item_id
       QBO Attachable --> Download URL --> Image file --> ERPNext File
    """
    frappe.logger().info("[QB SYNC] Started item images sync job")

    # --- Refresh Token First ---
    try:
        refresh_quickbooks_access_token()
    except Exception as e:
        frappe.log_error(
            f"Failed to refresh QuickBooks access token: {str(e)}\n{frappe.get_traceback()}",
            "QuickBooks Item Images Sync - Token Refresh Failed"
        )
        return

    settings = frappe.get_single("QuickBooks Settings")
    access_token = settings.access_token
    realm_id = settings.quickbooks_company_id
    base_url = settings.base_url.rstrip("/")
    minor_version = settings.minor_version or "75"

    if not access_token or not realm_id:
        error_msg = f"Missing QuickBooks credentials. Access Token: {'Present' if access_token else 'Missing'}, Realm ID: {'Present' if realm_id else 'Missing'}"
        frappe.log_error(error_msg, "QuickBooks Item Images Sync Failed")
        return

    imported = []
    skipped = []
    start_position = 1
    max_results = 50  # Smaller batches to avoid timeouts
    processed_count = 0  # Track processed items for periodic token refresh
    last_token_refresh = time.time()  # Track last token refresh time
    token_refresh_interval = 300  # Refresh token every 5 minutes (300 seconds)

    # Standard headers for QuickBooks API
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }

    def refresh_token_if_needed():
        """Refresh token if it's been more than the refresh interval."""
        nonlocal access_token, headers, last_token_refresh, settings
        current_time = time.time()
        if current_time - last_token_refresh > token_refresh_interval:
            try:
                frappe.logger().info("[QB SYNC] Refreshing access token (periodic refresh)")
                refresh_quickbooks_access_token()
                settings = frappe.get_single("QuickBooks Settings")  # Reload settings
                access_token = settings.access_token
                headers["Authorization"] = f"Bearer {access_token}"
                last_token_refresh = current_time
                frappe.logger().info("[QB SYNC] Token refreshed successfully")
            except Exception as e:
                frappe.log_error(
                    f"Periodic token refresh failed: {str(e)}",
                    "QuickBooks Item Images Sync - Periodic Token Refresh Failed"
                )

    # -----------------------------------------------------------------------------------
    # FETCH ALL ATTACHABLES WITH PAGINATION
    # -----------------------------------------------------------------------------------
    while True:
        # Query attachables linked to Items with pagination
        # Filter by EntityRef.type = 'Item' to get only item attachments
        # Note: QuickBooks query syntax - use lowercase 'item' for type filter
        query = f"SELECT * FROM Attachable WHERE AttachableRef.EntityRef.type = 'Item' STARTPOSITION {start_position} MAXRESULTS {max_results}"
        query_encoded = urlencode({"query": query, "minorversion": minor_version})
        url = f"{base_url}/v3/company/{realm_id}/query?{query_encoded}"

        # Refresh token periodically to prevent expiration
        refresh_token_if_needed()

        try:
            res = requests.get(url, headers=headers, timeout=60)

            # Check for 401 - token expired, refresh and retry
            if res.status_code == 401:
                frappe.logger().info(f"[QB SYNC] Got 401 at position {start_position}, refreshing token and retrying...")
                try:
                    refresh_quickbooks_access_token()
                    settings = frappe.get_single("QuickBooks Settings")  # Reload settings
                    access_token = settings.access_token
                    headers["Authorization"] = f"Bearer {access_token}"
                    last_token_refresh = time.time()

                    # Retry the request with new token
                    res = requests.get(url, headers=headers, timeout=60)
                    res.raise_for_status()
                    data = res.json()
                    frappe.logger().info(f"[QB SYNC] Retry successful after token refresh at position {start_position}")
                except Exception as retry_error:
                    error_msg = (
                        f"401 Unauthorized - Token expired and refresh/retry failed.\n"
                        f"Position: {start_position}\n"
                        f"Error: {str(retry_error)}\n"
                        f"URL: {url}\n"
                        f"{frappe.get_traceback()}"
                    )
                    frappe.log_error(error_msg, "QuickBooks Item Images Sync - Token Refresh Failed")
                    break
            else:
                res.raise_for_status()
                data = res.json()
        except requests.exceptions.Timeout:
            error_msg = (
                f"QuickBooks API timeout while fetching attachables.\n"
                f"Position: {start_position}\n"
                f"URL: {url}\n"
                f"Retrying with longer timeout..."
            )
            frappe.log_error(error_msg, "QuickBooks Item Images Sync Timeout")
            # Retry once - refresh token first in case it expired
            try:
                refresh_token_if_needed()
                res = requests.get(url, headers=headers, timeout=120)

                # Check for 401 on retry
                if res.status_code == 401:
                    refresh_quickbooks_access_token()
                    settings.reload()
                    access_token = settings.access_token
                    headers["Authorization"] = f"Bearer {access_token}"
                    res = requests.get(url, headers=headers, timeout=120)

                res.raise_for_status()
                data = res.json()
            except Exception as e:
                error_msg = (
                    f"QuickBooks API retry failed after timeout.\n"
                    f"Position: {start_position}\n"
                    f"Error: {str(e)}\n"
                    f"URL: {url}\n"
                    f"{frappe.get_traceback()}"
                )
                frappe.log_error(error_msg, "QuickBooks Item Images Sync Failed")
                break
        except Exception as e:
            error_msg = (
                f"QuickBooks API request failed while fetching attachables.\n"
                f"Position: {start_position}\n"
                f"Error: {str(e)}\n"
                f"URL: {url}\n"
            )
            if 'res' in locals():
                error_msg += (
                    f"Response Status: {res.status_code}\n"
                    f"Response Headers: {dict(res.headers)}\n"
                    f"Response Body: {res.text[:1000]}"
                )
            error_msg += f"\n{frappe.get_traceback()}"
            frappe.log_error(error_msg, "QuickBooks Item Images Sync Failed")
            break

        # Check for QuickBooks API errors in response
        if "Fault" in data:
            error_info = data.get("Fault", {})
            errors = error_info.get("Error", [{}])
            error_details = []
            for err in errors:
                error_details.append(
                    f"Code: {err.get('code', 'N/A')}, "
                    f"Message: {err.get('Message', 'Unknown error')}, "
                    f"Detail: {err.get('Detail', 'N/A')}"
                )
            error_msg = (
                f"QuickBooks API returned a fault response.\n"
                f"Position: {start_position}\n"
                f"Errors: {'; '.join(error_details)}\n"
                f"Full Response: {json.dumps(data, indent=2)[:2000]}"
            )
            frappe.log_error(error_msg, "QuickBooks Item Images Sync API Error")
            break

        # Handle both single object and array responses
        query_response = data.get("QueryResponse", {})
        attachables = query_response.get("Attachable", [])

        # If single object, convert to list
        if isinstance(attachables, dict):
            attachables = [attachables]

        if not attachables:
            break  # No more attachables

        frappe.logger().info(f"[QB SYNC] Fetched {len(attachables)} attachables starting from {start_position}.")

        # -----------------------------------------------------------------------------------
        # PROCESS EACH ATTACHABLE
        # -----------------------------------------------------------------------------------
        for attach in attachables:
            try:
                file_name = attach.get("FileName")
                content_type = attach.get("ContentType") or ""
                attach_id = attach.get("Id")

                if not file_name or not attach_id:
                    skipped.append({"id": attach_id or "unknown", "reason": "missing filename or id"})
                    continue

                # Filter for images only - check content type and file extension
                is_image = False
                if content_type:
                    is_image = content_type.lower().startswith("image/")

                # Also check file extension as fallback
                if not is_image and file_name:
                    image_extensions = [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp"]
                    file_lower = file_name.lower()
                    is_image = any(file_lower.endswith(ext) for ext in image_extensions)

                if not is_image:
                    skipped.append({"id": attach_id, "reason": f"not an image (content_type: {content_type}, filename: {file_name})"})
                    continue

                # Validate metadata - get Item reference
                refs = attach.get("AttachableRef", [])
                if not refs:
                    skipped.append({"id": attach_id, "reason": "no refs"})
                    continue

                # Handle both list and single ref
                ref = refs[0] if isinstance(refs, list) else refs
                entity_ref = ref.get("EntityRef", {}) if isinstance(ref, dict) else {}

                # Check entity type (case-insensitive - QuickBooks may return "Item" or "item")
                entity_type = entity_ref.get("type", "").lower()
                if entity_type != "item":
                    skipped.append({"id": attach_id, "reason": f"not item (type: {entity_ref.get('type')})"})
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

                # Check if file already exists for this item (by name and item)
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
                # STEP 1: Get temporary download URL from QuickBooks
                # -----------------------------------------------------------------------------------
                # First, try to use TempDownloadUri from the Attachable object itself
                temp_download_uri = attach.get("TempDownloadUri")

                if temp_download_uri:
                    frappe.logger().info(f"[QB SYNC] Using TempDownloadUri from Attachable object for {attach_id}")

                # If not available in the response, call the download endpoint
                if not temp_download_uri:
                    download_endpoint = f"{base_url}/v3/company/{realm_id}/download/{attach_id}"

                    try:
                        # Refresh token if needed before getting download URL
                        refresh_token_if_needed()

                        # Call download endpoint to get TempDownloadUri
                        # According to QuickBooks docs, this returns text/plain with a URL string
                        download_headers = {
                            "Authorization": f"Bearer {access_token}",
                            "Accept": "text/plain"  # QuickBooks returns text/plain for download endpoint
                        }

                        temp_url_res = requests.get(download_endpoint, headers=download_headers, timeout=30)

                        # Handle 401 - refresh token and retry
                        if temp_url_res.status_code == 401:
                            frappe.logger().info(f"[QB SYNC] Got 401 getting download URL for {attach_id}, refreshing token...")
                            refresh_quickbooks_access_token()
                            settings = frappe.get_single("QuickBooks Settings")  # Reload settings
                            access_token = settings.access_token
                            download_headers["Authorization"] = f"Bearer {access_token}"
                            headers["Authorization"] = f"Bearer {access_token}"
                            last_token_refresh = time.time()
                            temp_url_res = requests.get(download_endpoint, headers=download_headers, timeout=30)

                        temp_url_res.raise_for_status()

                        # The response is text/plain and contains either:
                        # 1. A plain URL string (most common)
                        # 2. A JSON string with TempDownloadUri field
                        response_text = temp_url_res.text.strip()

                        # Try parsing as JSON first (in case it's JSON wrapped)
                        try:
                            temp_url_data = json.loads(response_text)
                            temp_download_uri = temp_url_data.get("TempDownloadUri") or temp_url_data.get("tempDownloadUri")
                        except (ValueError, json.JSONDecodeError):
                            # If not JSON, it's likely a plain URL string
                            # Check if it looks like a URL
                            if response_text.startswith("http://") or response_text.startswith("https://"):
                                temp_download_uri = response_text
                            else:
                                # Try to extract URL from quoted string
                                if response_text.startswith('"') and response_text.endswith('"'):
                                    temp_download_uri = response_text.strip('"')
                                else:
                                    temp_download_uri = response_text

                        if not temp_download_uri:
                            skipped.append({"id": attach_id, "reason": "no TempDownloadUri in response"})
                            continue

                    except requests.exceptions.Timeout:
                        error_msg = (
                            f"Timeout while fetching download URL from QuickBooks.\n"
                            f"Attachable ID: {attach_id}\n"
                            f"File Name: {file_name}\n"
                            f"Item: {erp_item_name}\n"
                            f"Download Endpoint: {download_endpoint}\n"
                            f"Timeout: 30 seconds"
                        )
                        frappe.log_error(error_msg, "QuickBooks Item Images Sync - Download URL Timeout")
                        skipped.append({"id": attach_id, "reason": "timeout getting download URL"})
                        continue
                    except Exception as e:
                        error_msg = (
                            f"Failed to get download URL from QuickBooks.\n"
                            f"Attachable ID: {attach_id}\n"
                            f"File Name: {file_name}\n"
                            f"Item: {erp_item_name}\n"
                            f"Download Endpoint: {download_endpoint}\n"
                            f"Error: {str(e)}\n"
                            f"{frappe.get_traceback()}"
                        )
                        frappe.log_error(error_msg, "QuickBooks Item Images Sync - Download URL Failed")
                        skipped.append({"id": attach_id, "reason": f"failed to get download URL: {str(e)}"})
                        continue

                # -----------------------------------------------------------------------------------
                # STEP 2: Download the actual image from temporary URL
                # -----------------------------------------------------------------------------------
                # Note: TempDownloadUri expires after 15 minutes, so we download immediately after getting it
                try:
                    # Download the actual image from the temporary URL
                    # No auth needed for the temp URL, but it may expire
                    image_res = requests.get(
                        temp_download_uri,
                        timeout=60,  # 60 seconds timeout to avoid hanging on slow downloads
                        stream=True  # Stream download for better memory handling
                    )

                    # Check for 401 Unauthorized - URL might have expired
                    if image_res.status_code == 401:
                        # Try to get a fresh download URL and retry once
                        frappe.logger().info(f"[QB SYNC] Got 401 for {attach_id}, fetching fresh download URL...")
                        try:
                            download_endpoint = f"{base_url}/v3/company/{realm_id}/download/{attach_id}"
                            download_headers = {
                                "Authorization": f"Bearer {access_token}",
                                "Accept": "text/plain"
                            }
                            temp_url_res = requests.get(download_endpoint, headers=download_headers, timeout=30)
                            temp_url_res.raise_for_status()
                            response_text = temp_url_res.text.strip()

                            # Parse the fresh URL
                            try:
                                temp_url_data = json.loads(response_text)
                                temp_download_uri = temp_url_data.get("TempDownloadUri") or temp_url_data.get("tempDownloadUri")
                            except (ValueError, json.JSONDecodeError):
                                if response_text.startswith("http://") or response_text.startswith("https://"):
                                    temp_download_uri = response_text
                                elif response_text.startswith('"') and response_text.endswith('"'):
                                    temp_download_uri = response_text.strip('"')
                                else:
                                    temp_download_uri = response_text

                            if temp_download_uri:
                                # Retry download with fresh URL
                                image_res = requests.get(
                                    temp_download_uri,
                                    timeout=60,
                                    stream=True
                                )
                                image_res.raise_for_status()
                            else:
                                raise Exception("Could not get fresh download URL")
                        except Exception as retry_error:
                            error_msg = (
                                f"401 Unauthorized - Temporary download URL expired and retry failed.\n"
                                f"Attachable ID: {attach_id}\n"
                                f"File Name: {file_name}\n"
                                f"Item: {erp_item_name}\n"
                                f"Retry Error: {str(retry_error)}\n"
                                f"Note: QuickBooks temporary URLs expire after 15 minutes"
                            )
                            frappe.log_error(error_msg, "QuickBooks Item Images Sync - URL Expired")
                            skipped.append({"id": attach_id, "reason": "download URL expired (401)"})
                            continue
                    else:
                        # For other status codes, raise the exception normally
                        image_res.raise_for_status()

                    # Read content after successful response
                    file_content = image_res.content

                    if not file_content or len(file_content) == 0:
                        skipped.append({"id": attach_id, "reason": "empty file content"})
                        continue

                except requests.exceptions.Timeout:
                    error_msg = (
                        f"Timeout while downloading image from temporary URL.\n"
                        f"Attachable ID: {attach_id}\n"
                        f"File Name: {file_name}\n"
                        f"Item: {erp_item_name}\n"
                        f"Download URL: {temp_download_uri[:200]}...\n"
                        f"Timeout: 60 seconds"
                    )
                    frappe.log_error(error_msg, "QuickBooks Item Images Sync - Image Download Timeout")
                    skipped.append({"id": attach_id, "reason": "timeout downloading image"})
                    continue
                except requests.exceptions.HTTPError as http_error:
                    # Handle HTTP errors (including 401 if retry didn't work)
                    if http_error.response and http_error.response.status_code == 401:
                        error_msg = (
                            f"401 Unauthorized - Temporary download URL expired.\n"
                            f"Attachable ID: {attach_id}\n"
                            f"File Name: {file_name}\n"
                            f"Item: {erp_item_name}\n"
                            f"Note: QuickBooks temporary URLs expire after 15 minutes. URL may have expired before download."
                        )
                        frappe.log_error(error_msg, "QuickBooks Item Images Sync - URL Expired")
                        skipped.append({"id": attach_id, "reason": "download URL expired (401)"})
                    else:
                        error_msg = (
                            f"HTTP error while downloading image from temporary URL.\n"
                            f"Attachable ID: {attach_id}\n"
                            f"File Name: {file_name}\n"
                            f"Item: {erp_item_name}\n"
                            f"Status Code: {http_error.response.status_code if http_error.response else 'N/A'}\n"
                            f"Error: {str(http_error)}\n"
                            f"Download URL: {temp_download_uri[:200]}..."
                        )
                        frappe.log_error(error_msg, "QuickBooks Item Images Sync - Image Download HTTP Error")
                        skipped.append({"id": attach_id, "reason": f"HTTP {http_error.response.status_code if http_error.response else 'error'}"})
                    continue
                except Exception as e:
                    error_msg = (
                        f"Failed to download image from temporary URL.\n"
                        f"Attachable ID: {attach_id}\n"
                        f"File Name: {file_name}\n"
                        f"Item: {erp_item_name}\n"
                        f"Download URL: {temp_download_uri[:200]}...\n"
                        f"Error: {str(e)}\n"
                        f"{frappe.get_traceback()}"
                    )
                    frappe.log_error(error_msg, "QuickBooks Item Images Sync - Image Download Failed")
                    skipped.append({"id": attach_id, "reason": f"download failed: {str(e)}"})
                    continue

                # -----------------------------------------------------------------------------------
                # STEP 3: Compress image if it's too large
                # -----------------------------------------------------------------------------------
                # Check file size and compress if needed before saving
                from frappe.core.api.file import get_max_file_size
                max_file_size = get_max_file_size()  # Returns size in bytes
                file_size = len(file_content)
                original_file_size = file_size
                was_compressed = False

                # Compress image if it exceeds the size limit
                if file_size > max_file_size:
                    try:
                        from PIL import Image
                        import io

                        # Determine image format from content type or file extension
                        image_format = None
                        if content_type:
                            if "jpeg" in content_type.lower() or "jpg" in content_type.lower():
                                image_format = "JPEG"
                            elif "png" in content_type.lower():
                                image_format = "PNG"
                            elif "gif" in content_type.lower():
                                image_format = "GIF"

                        # Try to determine from file extension if content type doesn't help
                        if not image_format:
                            file_lower = file_name.lower()
                            if file_lower.endswith((".jpg", ".jpeg")):
                                image_format = "JPEG"
                            elif file_lower.endswith(".png"):
                                image_format = "PNG"
                            elif file_lower.endswith(".gif"):
                                image_format = "GIF"

                        if image_format:
                            frappe.logger().info(
                                f"[QB SYNC] Compressing large image {file_name} "
                                f"({file_size / (1024 * 1024):.2f} MB) for item {erp_item_name}"
                            )

                            # Open image
                            image = Image.open(io.BytesIO(file_content))
                            original_size = image.size

                            # Determine if image has transparency (for PNG)
                            has_transparency = image.mode in ('RGBA', 'LA', 'P') or 'transparency' in image.info

                            # Progressive compression: try different quality/size combinations
                            quality_levels = [85, 75, 65, 55, 45, 35]  # JPEG quality levels
                            max_dimensions = [
                                (2048, 2048),  # Start with reasonable size
                                (1600, 1600),
                                (1200, 1200),
                                (1024, 1024),
                                (800, 800),
                                (600, 600)
                            ]

                            compressed_content = None
                            for max_dim, quality in zip(max_dimensions, quality_levels):
                                try:
                                    # Create a copy for resizing
                                    img_copy = image.copy()

                                    # Resize if needed (maintain aspect ratio)
                                    if img_copy.size[0] > max_dim[0] or img_copy.size[1] > max_dim[1]:
                                        img_copy.thumbnail(max_dim, Image.Resampling.LANCZOS)

                                    # Convert to RGB for JPEG (removes transparency)
                                    if image_format == "JPEG" and img_copy.mode != "RGB":
                                        # Create white background for transparent images
                                        rgb_img = Image.new("RGB", img_copy.size, (255, 255, 255))
                                        if img_copy.mode == "RGBA":
                                            rgb_img.paste(img_copy, mask=img_copy.split()[3])  # Use alpha channel as mask
                                        else:
                                            rgb_img.paste(img_copy)
                                        img_copy = rgb_img

                                    # Save with compression
                                    output = io.BytesIO()
                                    save_kwargs = {
                                        "format": image_format,
                                        "optimize": True
                                    }

                                    if image_format == "JPEG":
                                        save_kwargs["quality"] = quality
                                        save_kwargs["progressive"] = True
                                    elif image_format == "PNG":
                                        # PNG compression level (0-9, 9 is maximum compression)
                                        save_kwargs["compress_level"] = 9
                                        if has_transparency:
                                            # Preserve transparency for PNG
                                            img_copy = img_copy.convert("RGBA")

                                    img_copy.save(output, **save_kwargs)
                                    compressed_content = output.getvalue()

                                    # Check if compressed size is acceptable
                                    if len(compressed_content) <= max_file_size:
                                        file_content = compressed_content
                                        file_size = len(file_content)
                                        was_compressed = True
                                        frappe.logger().info(
                                            f"[QB SYNC] Successfully compressed {file_name} from "
                                            f"{original_file_size / (1024 * 1024):.2f} MB to "
                                            f"{file_size / (1024 * 1024):.2f} MB "
                                            f"(size: {img_copy.size[0]}x{img_copy.size[1]}, quality: {quality})"
                                        )
                                        break

                                except Exception as compress_error:
                                    frappe.logger().warn(
                                        f"[QB SYNC] Compression attempt failed for {file_name}: {str(compress_error)}"
                                    )
                                    continue

                            # If compression didn't work or still too large, log and skip
                            if not compressed_content or len(compressed_content) > max_file_size:
                                file_size_mb = original_file_size / (1024 * 1024)
                                max_size_mb = max_file_size / (1024 * 1024)
                                error_msg = (
                                    f"File size exceeds maximum allowed limit even after compression.\n"
                                    f"Attachable ID: {attach_id}\n"
                                    f"File Name: {file_name}\n"
                                    f"Item: {erp_item_name}\n"
                                    f"Original File Size: {file_size_mb:.2f} MB\n"
                                    f"Maximum Allowed: {max_size_mb:.2f} MB\n"
                                    f"Note: Image too large to compress within limit"
                                )
                                frappe.log_error(error_msg, "QuickBooks Item Images Sync - File Too Large After Compression")
                                skipped.append({
                                    "id": attach_id,
                                    "reason": f"file too large even after compression ({file_size_mb:.2f} MB > {max_size_mb:.2f} MB limit)"
                                })
                                continue
                        else:
                            # Not a recognized image format, skip compression
                            file_size_mb = file_size / (1024 * 1024)
                            max_size_mb = max_file_size / (1024 * 1024)
                            error_msg = (
                                f"File size exceeds maximum allowed limit (not a compressible image format).\n"
                                f"Attachable ID: {attach_id}\n"
                                f"File Name: {file_name}\n"
                                f"Item: {erp_item_name}\n"
                                f"File Size: {file_size_mb:.2f} MB\n"
                                f"Maximum Allowed: {max_size_mb:.2f} MB\n"
                                f"Content Type: {content_type}"
                            )
                            frappe.log_error(error_msg, "QuickBooks Item Images Sync - File Too Large")
                            skipped.append({
                                "id": attach_id,
                                "reason": f"file too large ({file_size_mb:.2f} MB > {max_size_mb:.2f} MB limit)"
                            })
                            continue

                    except Exception as compress_error:
                        # If compression fails, log error but try to save original if it's under limit
                        frappe.log_error(
                            f"Image compression failed for {file_name}: {str(compress_error)}\n{frappe.get_traceback()}",
                            "QuickBooks Item Images Sync - Compression Error"
                        )
                        # Fall through to check if original is under limit
                        if file_size > max_file_size:
                            file_size_mb = file_size / (1024 * 1024)
                            max_size_mb = max_file_size / (1024 * 1024)
                            skipped.append({
                                "id": attach_id,
                                "reason": f"compression failed, file too large ({file_size_mb:.2f} MB > {max_size_mb:.2f} MB limit)"
                            })
                            continue

                # -----------------------------------------------------------------------------------
                # STEP 4: Save file to Frappe using proper Frappe file handling
                # -----------------------------------------------------------------------------------
                try:

                    # Sanitize file name for filesystem
                    safe_file_name = file_name.replace(" ", "_").replace("/", "_").replace("\\", "_")
                    # Remove any path components
                    safe_file_name = os.path.basename(safe_file_name)

                    # Use Frappe's file handling utilities
                    from frappe.utils.file_manager import save_file
                    from frappe.core.doctype.file.exceptions import MaxFileSizeReachedError

                    # Save file using Frappe's save_file which handles all the File doctype creation
                    # Note: MaxFileSizeReachedError is imported above for use in exception handlers
                    try:
                        file_doc = save_file(
                            fname=safe_file_name,
                            content=file_content,
                            dt="Item",
                            dn=erp_item_name,
                            folder=None,
                            is_private=0,
                            decode=False  # Already binary content
                        )
                    except MaxFileSizeReachedError as size_error:
                        # Handle file size error from save_file (in case our check missed it)
                        file_size_mb = len(file_content) / (1024 * 1024)
                        error_msg = (
                            f"File size exceeds maximum allowed limit (caught by save_file).\n"
                            f"Attachable ID: {attach_id}\n"
                            f"File Name: {file_name}\n"
                            f"Item: {erp_item_name}\n"
                            f"File Size: {file_size_mb:.2f} MB\n"
                            f"Error: {str(size_error)}\n"
                            f"Note: Increase 'Max File Size' in System Settings to allow larger files"
                        )
                        frappe.log_error(error_msg, "QuickBooks Item Images Sync - File Too Large")
                        skipped.append({
                            "id": attach_id,
                            "reason": f"file too large ({file_size_mb:.2f} MB exceeds limit)"
                        })
                        continue

                    if not file_doc:
                        raise Exception("save_file returned None")

                    # Update item's image field if it's an image and item doesn't have one
                    item_doc = frappe.get_doc("Item", erp_item_name)
                    if not item_doc.image:
                        item_doc.image = file_doc.file_url
                        item_doc.save(ignore_permissions=True)

                    # Commit after each file to avoid long transactions
                    frappe.db.commit()

                    imported.append({
                        "item": erp_item_name,
                        "filename": safe_file_name,
                        "qbo_attach_id": attach_id,
                        "file_url": file_doc.file_url
                    })

                    frappe.logger().info(f"[QB SYNC] Imported image {safe_file_name} for item {erp_item_name}")

                    processed_count += 1

                    # Small delay to avoid overwhelming the system
                    time.sleep(0.1)

                except MaxFileSizeReachedError as size_error:
                    # Handle file size error (catch here too in case it wasn't caught earlier)
                    file_size_mb = len(file_content) / (1024 * 1024)
                    error_msg = (
                        f"File size exceeds maximum allowed limit.\n"
                        f"Attachable ID: {attach_id}\n"
                        f"File Name: {file_name}\n"
                        f"Item: {erp_item_name}\n"
                        f"File Size: {file_size_mb:.2f} MB\n"
                        f"Error: {str(size_error)}\n"
                        f"Note: Increase 'Max File Size' in System Settings to allow larger files"
                    )
                    frappe.log_error(error_msg, "QuickBooks Item Images Sync - File Too Large")
                    skipped.append({
                        "id": attach_id,
                        "reason": f"file too large ({file_size_mb:.2f} MB exceeds limit)"
                    })
                    continue
                except Exception as e:
                    # Check if it's a file size error by checking the error message
                    error_str = str(e)
                    if "File size exceeded" in error_str or "MaxFileSizeReachedError" in str(type(e)):
                        file_size_mb = len(file_content) / (1024 * 1024)
                        error_msg = (
                            f"File size exceeds maximum allowed limit (caught as generic exception).\n"
                            f"Attachable ID: {attach_id}\n"
                            f"File Name: {file_name}\n"
                            f"Item: {erp_item_name}\n"
                            f"File Size: {file_size_mb:.2f} MB\n"
                            f"Error: {error_str}\n"
                            f"Note: Increase 'Max File Size' in System Settings to allow larger files"
                        )
                        frappe.log_error(error_msg, "QuickBooks Item Images Sync - File Too Large")
                        skipped.append({
                            "id": attach_id,
                            "reason": f"file too large ({file_size_mb:.2f} MB exceeds limit)"
                        })
                        continue
                    else:
                        error_msg = (
                            f"Failed to save file to Frappe File system.\n"
                            f"Attachable ID: {attach_id}\n"
                            f"File Name: {file_name} (sanitized: {safe_file_name})\n"
                            f"Item: {erp_item_name}\n"
                            f"File Size: {len(file_content)} bytes\n"
                            f"Error: {str(e)}\n"
                            f"{frappe.get_traceback()}"
                        )
                        frappe.log_error(error_msg, "QuickBooks Item Image Save Failed")
                        skipped.append({"id": attach_id, "reason": f"file save failed: {str(e)}"})

            except Exception as e:
                error_msg = (
                    f"Unexpected error while processing attachable.\n"
                    f"Attachable ID: {attach.get('Id', 'unknown')}\n"
                    f"File Name: {attach.get('FileName', 'unknown')}\n"
                    f"Attachable Data: {json.dumps(attach, indent=2)[:1000]}\n"
                    f"Error: {str(e)}\n"
                    f"{frappe.get_traceback()}"
                )
                frappe.log_error(error_msg, "QuickBooks Item Image Processing Error")
                skipped.append({"id": attach.get("Id", "unknown"), "reason": f"processing error: {str(e)}"})

        # Check if we need to fetch more
        if len(attachables) < max_results:
            break  # Last page reached

        start_position += max_results

    # -----------------------------------------------------------------------------------
    # SUMMARY OUTPUT
    # -----------------------------------------------------------------------------------
    summary_msg = (
        f"QuickBooks Item Images Sync completed.\n"
        f"Total Imported: {len(imported)}\n"
        f"Total Skipped: {len(skipped)}\n"
        f"Success Rate: {(len(imported) / (len(imported) + len(skipped)) * 100) if (len(imported) + len(skipped)) > 0 else 0:.1f}%\n\n"
    )

    if imported:
        summary_msg += "Imported Items (first 10):\n"
        for item in imported[:10]:
            summary_msg += f"  - {item.get('item')}: {item.get('filename')}\n"
        summary_msg += "\n"

    if skipped:
        summary_msg += "Skipped Items (first 10):\n"
        for item in skipped[:10]:
            summary_msg += f"  - ID {item.get('id')}: {item.get('reason')}\n"

    # Log summary for tracking
    frappe.log_error(
        summary_msg,
        "QuickBooks Item Images Sync - Summary"
    )

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
