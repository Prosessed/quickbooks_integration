# Copyright (c) 2025, Jaspreet Singh Sodhi and contributors
# For license information, please see license.txt

# import frappe
import json
import re
from datetime import timedelta
from frappe.model.document import Document
import requests
import frappe
from frappe.utils import now
from quickbooks_integration.api import refresh_quickbooks_access_token, sync_credit_memo_to_quickbooks, sync_selected_sales_invoices, sync_single_purchase_invoice_to_quickbooks, sync_single_sales_invoice
from frappe import _
import time

class QuickBooksSync(Document):
	@frappe.whitelist()
	def sync_stock_from_quickbooks(self):
		"""Sync stock quantities from QuickBooks to ERPNext (triggered from UI)."""
		try:
			start_stock_sync_background()
			return {
				"success": True,
				"message": _("Stock sync from QuickBooks has been started in the background."),
			}
		except Exception as e:
			frappe.log_error(
				message=f"Error starting stock sync from QuickBooks: {str(e)}\n{frappe.get_traceback()}",
				title="QuickBooks Stock Sync Start Error",
			)
			return {
				"success": False,
				"message": _("Error starting stock sync: {0}").format(str(e)),
			}

	@frappe.whitelist()
	def update_stock_sync_cron_job(self, interval: str | None = None):
		"""Create or update Scheduled Job Type for QuickBooks stock sync."""
		interval_text = (interval or self.stock_sync_interval or "").strip()
		if not interval_text:
			frappe.throw(_("Stock Sync Interval is not set."))

		interval_map = {
			"1 Hour": 1,
			"3 Hours": 3,
			"6 Hours": 6,
			"12 Hours": 12,
			"24 Hours": 24,
		}
		hours = interval_map.get(interval_text)
		if not hours:
			frappe.throw(_("Invalid stock sync interval: {0}").format(interval_text))

		cron_expression = f"0 */{hours} * * *"
		method_path = (
			"quickbooks_integration.quickbooks_integration.doctype."
			"quickbooks_sync.quickbooks_sync.start_stock_sync_background"
		)

		# Check if job already exists
		existing_job = frappe.db.get_value(
			"Scheduled Job Type",
			{"method": method_path},
			"name"
		)

		if existing_job:
			# Update existing job
			job = frappe.get_doc("Scheduled Job Type", existing_job)
			job.cron_format = cron_expression
			job.frequency = "Cron"
			job.stopped = 0
			job.save(ignore_permissions=True)
			status = "updated"
		else:
			# Create new job
			job = frappe.get_doc({
				"doctype": "Scheduled Job Type",
				"method": method_path,
				"cron_format": cron_expression,
				"frequency": "Cron",
				"stopped": 0
			})
			job.insert(ignore_permissions=True)
			status = "created"

		# Save interval to settings
		self.stock_sync_interval = interval_text
		self.save(ignore_permissions=True)
		frappe.db.commit()

		return {
			"status": status,
			"message": _("Stock sync scheduled every {0}").format(interval_text.lower()),
			"cron": cron_expression,
		}

	@frappe.whitelist()
	def refresh_statistics(self):
		"""Refresh all statistics (total, synced, remaining, status) for Items, Suppliers, Customers, Stock, Sales Orders – same pattern as MYOB Acumatica."""
		try:
			frappe.db.rollback()
			stats = {}
			stats["items"] = self._get_item_statistics()
			stats["suppliers"] = self._get_supplier_statistics()
			stats["customers"] = self._get_customer_statistics()
			stats["stock"] = self._get_stock_statistics()
			stats["sales_orders"] = self._get_sales_order_statistics()
			stats["sales_invoices"] = self._get_sales_invoice_statistics()
			stats["purchase_invoices"] = self._get_purchase_invoice_statistics()

			d = stats["items"]
			self.item_total_count = d.get("total", 0)
			self.item_synced_count = d.get("synced", 0)
			self.item_remaining_count = d.get("remaining", 0)
			self.item_sync_status = d.get("status", "Not Started")
			self.item_last_sync_date = d.get("last_sync_date")

			d = stats["suppliers"]
			self.supplier_total_count = d.get("total", 0)
			self.supplier_synced_count = d.get("synced", 0)
			self.supplier_remaining_count = d.get("remaining", 0)
			self.supplier_sync_status = d.get("status", "Not Started")
			self.supplier_last_sync_date = d.get("last_sync_date")

			d = stats["customers"]
			self.customer_total_count = d.get("total", 0)
			self.customer_synced_count = d.get("synced", 0)
			self.customer_remaining_count = d.get("remaining", 0)
			self.customer_disabled_count = d.get("disabled", 0)
			self.customer_sync_status = d.get("status", "Not Started")
			self.customer_last_sync_date = d.get("last_sync_date")

			d = stats["stock"]
			self.stock_total_count = d.get("total", 0)
			self.stock_synced_count = d.get("synced", 0)
			self.stock_remaining_count = d.get("remaining", 0)
			self.stock_sync_status = d.get("status", "Not Started")
			self.stock_last_sync_date = d.get("last_sync_date")

			d = stats["sales_orders"]
			self.sales_order_total_count = d.get("total", 0)
			self.sales_order_synced_count = d.get("synced", 0)
			self.sales_order_remaining_count = d.get("remaining", 0)
			self.sales_order_sync_status = d.get("status", "Not Started")
			self.sales_order_last_sync_date = d.get("last_sync_date")

			d = stats["sales_invoices"]
			self.sales_invoice_total_count = d.get("total", 0)
			self.sales_invoice_synced_count = d.get("synced", 0)
			self.sales_invoice_remaining_count = d.get("remaining", 0)
			self.sales_invoice_sync_status = d.get("status", "Not Started")
			self.sales_invoice_last_sync_date = d.get("last_sync_date")

			d = stats["purchase_invoices"]
			self.purchase_invoice_total_count = d.get("total", 0)
			self.purchase_invoice_synced_count = d.get("synced", 0)
			self.purchase_invoice_remaining_count = d.get("remaining", 0)
			self.purchase_invoice_sync_status = d.get("status", "Not Started")
			self.purchase_invoice_last_sync_date = d.get("last_sync_date")

			self.save(ignore_permissions=True)
			frappe.db.commit()
			return {"success": True, "message": _("Statistics refreshed successfully")}
		except Exception as e:
			frappe.log_error(
				message=f"Error refreshing QuickBooks Sync statistics: {str(e)}\n{frappe.get_traceback()}",
				title="QuickBooks Sync Statistics Error",
			)
			return {"success": False, "message": _("Error refreshing statistics: {0}").format(str(e))}

	def _get_item_statistics(self):
		total = frappe.db.count("Item")
		synced = frappe.db.count("Item", filters={"custom_quickbooks_item_id": ["!=", ""]})
		remaining = total - synced
		if total == 0:
			status = "No Data"
		elif synced == total:
			status = "All Synced"
		elif synced > 0:
			status = "Partially Synced"
		else:
			status = "Not Started"
		last_sync_date = frappe.db.get_value(
			"Item",
			{"custom_quickbooks_item_id": ["!=", ""]},
			"modified",
			order_by="modified desc",
		)
		return {"total": total, "synced": synced, "remaining": remaining, "status": status, "last_sync_date": last_sync_date}

    
	def _get_supplier_statistics(self):
		total = frappe.db.count("Supplier")
		synced = frappe.db.count("Supplier", filters={"custom_quickbooks_supplier_id": ["!=", ""]})
		remaining = total - synced
		if total == 0:
			status = "No Data"
		elif synced == total:
			status = "All Synced"
		elif synced > 0:
			status = "Partially Synced"
		else:
			status = "Not Started"
		last_sync_date = frappe.db.get_value(
			"Supplier",
			{"custom_quickbooks_supplier_id": ["!=", ""]},
			"modified",
			order_by="modified desc",
		)
		return {"total": total, "synced": synced, "remaining": remaining, "status": status, "last_sync_date": last_sync_date}

	def _get_customer_statistics(self):
		total = frappe.db.count("Customer")
		synced = frappe.db.count("Customer", filters={"custom_quickbooks_customer_id": ["!=", ""]})
		disabled_not_synced = frappe.db.sql(
			"""
			SELECT COUNT(*) FROM `tabCustomer`
			WHERE disabled = 1
			  AND (IFNULL(custom_quickbooks_customer_id, '') = '')
			"""
		)[0][0]
		remaining = total - synced - disabled_not_synced
		if total == 0:
			status = "No Data"
		elif synced == total:
			status = "All Synced"
		elif synced > 0:
			status = "Partially Synced"
		else:
			status = "Not Started"
		last_sync_date = frappe.db.get_value(
			"Customer",
			{"custom_quickbooks_customer_id": ["!=", ""]},
			"modified",
			order_by="modified desc",
		)
		return {
			"total": total,
			"synced": synced,
			"remaining": remaining,
			"disabled": disabled_not_synced,
			"status": status,
			"last_sync_date": last_sync_date,
		}

	def _get_stock_statistics(self):
		# Inventory items (is_stock_item = 1) that have QuickBooks Item ID are considered synced for stock
		total = frappe.db.count("Item", filters={"is_stock_item": 1})
		synced = frappe.db.count(
			"Item",
			filters={"is_stock_item": 1, "custom_quickbooks_item_id": ["!=", ""]},
		)
		remaining = total - synced
		if total == 0:
			status = "No Data"
		elif synced == total:
			status = "All Synced"
		elif synced > 0:
			status = "Partially Synced"
		else:
			status = "Not Started"
		last_sync_date = frappe.db.get_value(
			"Item",
			{"is_stock_item": 1, "custom_quickbooks_item_id": ["!=", ""]},
			"modified",
			order_by="modified desc",
		)
		return {"total": total, "synced": synced, "remaining": remaining, "status": status, "last_sync_date": last_sync_date}

	def _get_sales_order_statistics(self):
		total = frappe.db.count("Sales Order", filters={"docstatus": ["!=", 2]})
		# Synced if they have QuickBooks Sales Order ID (custom field)
		synced = frappe.db.sql(
			"""
			SELECT COUNT(*) FROM `tabSales Order`
			WHERE docstatus != 2
			  AND IFNULL(quickbooks_sales_order_id, '') != ''
			""",
			as_list=True,
		)[0][0]
		remaining = total - synced
		if total == 0:
			status = "No Data"
		elif synced == total:
			status = "All Synced"
		elif synced > 0:
			status = "Partially Synced"
		else:
			status = "Not Started"
		last_sync_date = frappe.db.get_value(
			"Sales Order",
			{"docstatus": ["!=", 2], "quickbooks_sales_order_id": ["!=", ""]},
			"modified",
			order_by="modified desc",
		)
		return {"total": total, "synced": synced, "remaining": remaining, "status": status, "last_sync_date": last_sync_date}

	def _get_sales_invoice_statistics(self):
		total = frappe.db.count("Sales Invoice", filters={"docstatus": ["!=", 2]})
		synced = frappe.db.sql(
			"""
			SELECT COUNT(*) FROM `tabSales Invoice`
			WHERE docstatus != 2
			  AND IFNULL(custom_quickbooks_invoice_id, '') != ''
			""",
			as_list=True,
		)[0][0]
		remaining = total - synced
		if total == 0:
			status = "No Data"
		elif synced == total:
			status = "All Synced"
		elif synced > 0:
			status = "Partially Synced"
		else:
			status = "Not Started"
		last_sync_date = frappe.db.get_value(
			"Sales Invoice",
			{"docstatus": ["!=", 2], "custom_quickbooks_invoice_id": ["!=", ""]},
			"modified",
			order_by="modified desc",
		)
		return {"total": total, "synced": synced, "remaining": remaining, "status": status, "last_sync_date": last_sync_date}

	def _get_purchase_invoice_statistics(self):
		total = frappe.db.count("Purchase Invoice", filters={"docstatus": ["!=", 2]})
		synced = frappe.db.sql(
			"""
			SELECT COUNT(*) FROM `tabPurchase Invoice`
			WHERE docstatus != 2
			  AND IFNULL(custom_quickbooks_bill_id, '') != ''
			""",
			as_list=True,
		)[0][0]
		remaining = total - synced
		if total == 0:
			status = "No Data"
		elif synced == total:
			status = "All Synced"
		elif synced > 0:
			status = "Partially Synced"
		else:
			status = "Not Started"
		last_sync_date = frappe.db.get_value(
			"Purchase Invoice",
			{"docstatus": ["!=", 2], "custom_quickbooks_bill_id": ["!=", ""]},
			"modified",
			order_by="modified desc",
		)
		return {"total": total, "synced": synced, "remaining": remaining, "status": status, "last_sync_date": last_sync_date}


	def _get_sales_invoice_statistics(self):
		total = frappe.db.count("Sales Invoice", filters={"docstatus": ["!=", 2]})
		synced = frappe.db.sql(
			"""
			SELECT COUNT(*) FROM `tabSales Invoice`
			WHERE docstatus != 2
			AND IFNULL(custom_quickbooks_invoice_id, '') != ''
			""",
			as_list=True,
		)[0][0]
		remaining = total - synced
		if total == 0:
			status = "No Data"
		elif synced == total:
			status = "All Synced"
		elif synced > 0:
			status = "Partially Synced"
		else:
			status = "Not Started"
		last_sync_date = frappe.db.get_value(
			"Sales Invoice",
			{"docstatus": ["!=", 2], "custom_quickbooks_invoice_id": ["!=", ""]},
			"modified",
			order_by="modified desc",
		)
		return {"total": total, "synced": synced, "remaining": remaining, "status": status, "last_sync_date": last_sync_date}

	def _get_purchase_invoice_statistics(self):
		total = frappe.db.count("Purchase Invoice", filters={"docstatus": ["!=", 2]})
		synced = frappe.db.sql(
			"""
			SELECT COUNT(*) FROM `tabPurchase Invoice`
			WHERE docstatus != 2
			AND IFNULL(custom_quickbooks_bill_id, '') != ''
			""",
			as_list=True,
		)[0][0]
		remaining = total - synced
		if total == 0:
			status = "No Data"
		elif synced == total:
			status = "All Synced"
		elif synced > 0:
			status = "Partially Synced"
		else:
			status = "Not Started"
		last_sync_date = frappe.db.get_value(
			"Purchase Invoice",
			{"docstatus": ["!=", 2], "custom_quickbooks_bill_id": ["!=", ""]},
			"modified",
			order_by="modified desc",
		)
		return {"total": total, "synced": synced, "remaining": remaining, "status": status, "last_sync_date": last_sync_date}

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


@frappe.whitelist(allow_guest=True)
def handle_sales_order_submit(doc, method):
    """
    Hook handler for Sales Order on_submit.
    Validates QuickBooks Settings and enqueues background sync job.
    """
    try:
        # Get QuickBooks Settings
        settings = frappe.get_single("QuickBooks Settings")

        # Validation: Check if QuickBooks is enabled
        if not settings.enable:
            return  # Skip silently if QuickBooks is disabled

        # Validation: Check if Order Sync is allowed
        if not settings.allow_order_sync:
            return  # Skip silently if Order Sync is disabled

        # Validation: Skip if already synced
        if doc.get("quickbooks_sales_order_id"):
            frappe.logger().info(f"[QBO] Sales Order {doc.name} already has QuickBooks ID. Skipping sync.")
            return

        # Enqueue background job
        frappe.enqueue(
            method=sync_sales_order_to_quickbooks,
            queue='long',
            docname=doc.name,
            enqueue_after_commit=True
        )


        frappe.msgprint("Sales Order synchronization with QuickBooks has started.", indicator="green")
        frappe.logger().info(f"[QBO] Enqueued Sales Order {doc.name} for QuickBooks sync")
        time.sleep(10)

    except Exception as e:
        # Log error but don't block Sales Order submission
        frappe.log_error(
            f"Error in handle_sales_order_submit for {doc.name}: {str(e)}\n{frappe.get_traceback()}",
            "QuickBooks Sales Order Hook Error"
        )


@frappe.whitelist()
def sync_sales_order_to_quickbooks(docname=None):
    """Sync a single Sales Order to QuickBooks with global invoice-level discount %"""
    import requests, json
    from frappe.utils import flt

    try:
        # Log start of sync process
        frappe.log_error(
            title="QBO Sync Started",
            message=f"Starting sync for Sales Order: {docname}"
        )

        doc = frappe.get_doc("Sales Order", docname)
        if doc.docstatus == 2:
            frappe.msgprint("Cancelled Sales Orders are not allowed to sync")
            frappe.log_error(
                title="QBO Sync Blocked - Cancelled Sales Order",
                message=f"Sales Order {docname} is cancelled (docstatus=2)"
            )
            return {"error": "Cancelled sales order cannot be synced."}

        refresh_quickbooks_access_token()
        settings = frappe.get_doc("QuickBooks Settings")
        if not settings.enable:
            frappe.msgprint("Please Enable QuickBooks Settings")
            frappe.log_error(
                title="QBO Sync Blocked - Settings Disabled",
                message=f"QuickBooks Settings is disabled for Sales Order: {docname}"
            )
            return {"error": "QuickBooks not enabled."}

        # ---- QuickBooks endpoint ----
        url = f"{settings.base_url.strip().rstrip('/')}/v3/company/{settings.quickbooks_company_id}/estimate?minorversion={settings.minor_version or '75'}"
        headers = {
            "Authorization": f"Bearer {settings.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        frappe.log_error(
            title="QBO Sync - API Configuration",
            message=f"Sales Order: {docname}\nURL: {url}\nMinor Version: {settings.minor_version or '75'}"
        )

        # ---- Customer ----
        customer = frappe.get_doc("Customer", doc.customer)
        qb_customer_id = customer.get("custom_quickbooks_customer_id") or "1"
        send_item = (settings.send_item) == 1

        frappe.log_error(
            title="QBO Sync - Customer Info",
            message=f"Sales Order: {docname}\nCustomer: {doc.customer}\nQB Customer ID: {qb_customer_id}\nSend Item: {send_item}"
        )

        # ---- Helper: get tax code ----
        def get_tax_code(item):
            tax_template = item.item_tax_template
            # if not tax_template:
            #     # Try to fetch from Item master
            #     tax_template = frappe.db.get_value("Item", item.item_code, "item_tax_template")

            if not tax_template:

                return "4"


            try:
                tax_code = frappe.get_doc("Item Tax Template", tax_template).custom_quickbooks_gst_id

                if not tax_code:
                    error_msg = f"Tax template '{tax_template}' for item {item.item_code} has no QuickBooks GST ID mapped"
                    frappe.log_error(
                        title="QBO Sync - Missing QuickBooks GST ID",
                        message=error_msg
                    )
                    frappe.throw(error_msg)

                return tax_code

            except Exception as e:
                frappe.log_error(
                    title="QBO Sync - Tax Code Fetch Error",
                    message=f"Error fetching tax code for item {item.item_code} in Sales Invoice {docname}: {str(e)}"
                )
                frappe.throw(f"Failed to fetch tax code for item {item.item_code}: {str(e)}")

        # ---- Prepare line items ----
        line_items = []
        subtotal = 0.0
        tax_code_weights = {}  # Track amount per tax code to find dominant one

        frappe.log_error(
            title="QBO Sync - Sales Order Amounts",
            message=f"Sales Order: {docname}\n" +
                    f"Total (doc.total): {doc.total}\n" +
                    f"Net Total (doc.net_total): {doc.net_total}\n" +
                    f"Grand Total (doc.grand_total): {doc.grand_total}\n" +
                    f"Discount Amount (doc.discount_amount): {getattr(doc, 'discount_amount', 0)}\n" +
                    f"Additional Discount %: {getattr(doc, 'additional_discount_percentage', 0)}"
        )

        for idx, item in enumerate(doc.items, 1):
            amount = float(item.amount or 0)
            subtotal += amount

            # Retrieve tax code once
            item_tax_code = get_tax_code(item)

            # Track weight (accumulate amount per tax code)
            tax_code_weights[item_tax_code] = tax_code_weights.get(item_tax_code, 0.0) + amount

            detail = {
                "Qty": item.qty,
                "UnitPrice": float(item.rate or 0),
                "TaxCodeRef": {"value": item_tax_code}
            }

            if send_item:
                qb_item_id = frappe.db.get_value("Item", item.item_code, "custom_quickbooks_item_id")
                if qb_item_id:
                    detail["ItemRef"] = {"value": qb_item_id, "name": item.item_name}
                    frappe.log_error(
                        title=f"QBO Sync - Line Item {idx} with QBO Item",
                        message=f"Sales Order: {docname}\nItem: {item.item_code}\nQB Item ID: {qb_item_id}\nQty: {item.qty}\nRate: {item.rate}\nAmount: {amount}"
                    )
                else:
                    frappe.log_error(
                        title=f"QBO Sync - Line Item {idx} without QBO Item",
                        message=f"Sales Order: {docname}\nItem: {item.item_code}\nNo QB Item ID found\nQty: {item.qty}\nRate: {item.rate}\nAmount: {amount}"
                    )

            line_items.append({
                "DetailType": "SalesItemLineDetail",
                "Amount": amount,
                "Description": item.description or item.item_name,
                "SalesItemLineDetail": detail
            })

        frappe.log_error(
            title="QBO Sync - Line Items Summary",
            message=f"Sales Order: {docname}\nTotal Line Items: {len(line_items)}\nSubtotal: {subtotal}"
        )

        # ---- Global invoice-level discount ----
        # In ERPNext, discount_amount is the value of the discount.
        # additional_discount_percentage is the % value.
        discount_amount = flt(doc.get("discount_amount"))
        additional_discount_percentage = flt(doc.get("additional_discount_percentage"))

        frappe.log_error(
            title="QBO DEBUG - Raw Discount Values",
            message=f"Doc: {docname}\ndiscount_amount: {discount_amount}\nadditional_discount_percentage: {additional_discount_percentage}\nsubtotal: {subtotal}"
        )

        # Force calculation if percentage exists, to ensure we have a value
        if additional_discount_percentage > 0:
            calculated_discount = round(subtotal * (additional_discount_percentage / 100.0), 2)
            # Use calculated if doc.discount_amount is 0 or vastly different?
            # Let's prefer the calculated one if discount_amount is 0
            if discount_amount == 0:
                discount_amount = calculated_discount
                frappe.log_error(title="QBO DEBUG - Using Calculated Discount", message=f"Calculated: {discount_amount}")

        frappe.log_error(
            title="QBO DEBUG - Final Discount to Send",
            message=f"Discount Amount: {discount_amount}"
        )

        if discount_amount > 0:
            # STRATEGY: Send fixed amount (no percentage)

            # Determine dominant tax code (Tax code with highest total amount)
            # Default to "5" (GST) if no items or something fails
            discount_tax_code = "5"
            if tax_code_weights:
                try:
                    # Find key with max value
                    discount_tax_code = max(tax_code_weights, key=tax_code_weights.get)
                    frappe.log_error(title="QBO DEBUG - Dominant Tax Code Found", message=f"Selected Tax Code: {discount_tax_code} based on weights: {json.dumps(tax_code_weights)}")
                except Exception as e:
                    frappe.log_error(title="QBO DEBUG - Tax Code Selection Error", message=str(e))
                    # Fallback to first item logic if weight calculation fails (shouldn't happen)
                    if len(line_items) > 0 and "SalesItemLineDetail" in line_items[0]:
                        try:
                            discount_tax_code = line_items[0]["SalesItemLineDetail"]["TaxCodeRef"]["value"]
                        except:
                            pass

            frappe.log_error(
                title="QBO DEBUG - Discount Tax Code",
                message=f"Using Tax Code: {discount_tax_code}"
            )

            # Strictly match the manual working payload
            # Removed DiscountAccountRef as it was not in the working manual payload
            discount_line = {
               "DetailType": "DiscountLineDetail",
               "Amount": discount_amount,
               "DiscountLineDetail": {
                   "PercentBased": False,
                   "TaxCodeRef": {"value": discount_tax_code}
               }
            }

            line_items.append(discount_line)

            frappe.log_error(
                title="QBO DEBUG - Discount Payload",
                message=f"Payload Line:\n{json.dumps(discount_line, indent=2)}"
            )
        else:
            frappe.log_error(
                title="QBO Sync - No Discount",
                message=f"Sales Order: {docname}\nNo additional discount percentage found"
            )

        # ---- Build payload ----
        apply_discount_on = doc.get("apply_discount_on") or "Grand Total" # Default to Grand Total if not set

        # Determine QBO behavior based on ERPNext discount setting
        if apply_discount_on == "Net Total":
            # Discount applied BEFORE tax
            apply_tax_after_discount = True
            # Discount line needs a TAXABLE code so it reduces the tax basis
            # We already calculated 'discount_tax_code' (dominant tax code) above for this purpose
        else:
            # "Grand Total" -> Discount applied AFTER tax
            apply_tax_after_discount = False
            # Discount line needs a NON-TAXABLE code so it doesn't reduce the calculated tax
            # We force it to "Non-Taxable" (usually ID "4" or "NON" in standard QBO AU/Global)
            # You might need to adjust "4" if your specific QBO Non-Taxable code is different.
            # Assuming '4' based on your earlier payload which had "TaxCodeRef": {"value": "4"} for a line item.
            discount_tax_code = "4" # Or "NON" or whatever is "Tax Free" in your system

            frappe.log_error(
                title="QBO Sync - Discount Logic",
                message=f"Apply Discount On: {apply_discount_on} -> Setting ApplyTaxAfterDiscount=False, TaxCode=Non-Taxable({discount_tax_code})"
            )

            # Update the discount line we appended earlier if we need to change the tax code
            if len(line_items) > 0 and line_items[-1].get("DetailType") == "DiscountLineDetail":
                 line_items[-1]["DiscountLineDetail"]["TaxCodeRef"]["value"] = discount_tax_code


        payload = {
            "DocNumber": doc.name,
            "TxnDate": str(doc.transaction_date),
            "CustomerRef": {"value": qb_customer_id, "name": doc.customer},
            "Line": line_items,
            "ApplyTaxAfterDiscount": apply_tax_after_discount,
            "CustomerMemo": {"value": "Generated from ERPNext"},
            "PrintStatus": "NeedToPrint",
            "EmailStatus": "NotSet"
        }

        # Remove GlobalTaxCalculation to let QBO use defaults/customer settings
        # This matches the working manual payload

        frappe.log_error(
            title="QBO Sync - Final Payload",
            message=f"Sales Order: {docname}\nPayload:\n{json.dumps(payload, indent=2)}"
        )

        # ---- Send request ----
        frappe.log_error(
            title="QBO Sync - Sending Request",
            message=f"Sales Order: {docname}\nSending POST request to QuickBooks..."
        )

        res = requests.post(url, headers=headers, data=json.dumps(payload))

        # ---- Log full request + response for debugging ----
        frappe.log_error(
            title="QBO Estimate Request + Response",
            message=(
                f"Sales Order: {docname}\n\n"
                f"REQUEST PAYLOAD:\n{json.dumps(payload, indent=2, default=str)}\n\n"
                f"STATUS CODE: {res.status_code}\n\n"
                f"RESPONSE BODY:\n{res.text}"
            )
        )

        frappe.log_error(
            title="QBO Sync - Response Received",
            message=f"Sales Order: {docname}\nStatus Code: {res.status_code}\nResponse Headers: {dict(res.headers)}\nResponse Text (first 2000 chars): {res.text[:2000]}"
        )

        body = res.json() if res.text else {}

        if res.status_code in (200, 201):
            if body.get("Estimate"):
                qbo_id = body["Estimate"]["Id"]

                # Log the discount details from QB response
                qb_lines = body["Estimate"].get("Line", [])
                discount_lines = [l for l in qb_lines if l.get("DetailType") == "DiscountLineDetail"]

                frappe.log_error(
                    title="QBO Sync - QB Response Analysis",
                    message=f"Sales Order: {docname}\n" +
                            f"QB Estimate ID: {qbo_id}\n" +
                            f"QB Total: {body['Estimate'].get('TotalAmt')}\n" +
                            f"Discount Lines Found: {len(discount_lines)}\n" +
                            f"Discount Line Details: {json.dumps(discount_lines, indent=2)}"
                )

                doc.db_set("quickbooks_sales_order_id", qbo_id)
                frappe.db.commit()

                frappe.log_error(
                    title="QBO Sync - SUCCESS",
                    message=f"Sales Order: {docname}\nQBO Estimate ID: {qbo_id}\nFull Response: {json.dumps(body, indent=2)}"
                )

                frappe.msgprint(f"Successfully synced to QuickBooks! QBO Estimate ID: {qbo_id}")
                return {"id": qbo_id, "response": body}
            else:
                frappe.log_error(
                    title="QBO Sync - Unexpected Response Format",
                    message=f"Sales Order: {docname}\nStatus: {res.status_code}\nResponse missing 'Estimate' key\nFull Response: {json.dumps(body, indent=2)}"
                )
                return {"error": "Unexpected response format", "response": body}
        else:
            # Extract detailed error information
            fault = body.get("Fault", {})
            errors = fault.get("Error", [])
            error_messages = []

            for error in errors:
                error_messages.append(f"Code: {error.get('code')}, Message: {error.get('Message')}, Detail: {error.get('Detail')}")

            frappe.log_error(
                title="QBO Sync - FAILED",
                message=f"Sales Order: {docname}\n" +
                        f"Status Code: {res.status_code}\n" +
                        f"Errors: {'; '.join(error_messages)}\n" +
                        f"Full Response: {json.dumps(body, indent=2)}\n" +
                        f"Request Payload: {json.dumps(payload, indent=2)}"
            )

            error_msg = error_messages[0] if error_messages else "Unknown error"
            frappe.msgprint(f"QuickBooks sync failed: {error_msg}")
            return {"error": f"Sync failed ({res.status_code})", "details": error_messages, "response": body}

    except Exception as e:
        import traceback
        frappe.log_error(
            title="QBO Sync - EXCEPTION",
            message=f"Sales Order: {docname}\n" +
                    f"Exception Type: {type(e).__name__}\n" +
                    f"Error: {str(e)}\n" +
                    f"Full Traceback:\n{traceback.format_exc()}"
        )
        frappe.msgprint(f"Error during sync: {str(e)}")
        return {"error": str(e), "traceback": traceback.format_exc()}

@frappe.whitelist()
def sync_invoice_to_quickbooks(docname=None):
    """
    Sync Sales Invoice to QuickBooks.
    This wrapper calls the main logic in api.py (sync_single_sales_invoice)
    to avoid duplication and ensure consistent discount handling.
    """
    try:


        # Call the centralized function in api.py
        result = sync_single_sales_invoice(docname)

        # The API function returns a dict.
        # If successful, it has "id" and "response".
        # If failed, it has "error".

        if result and result.get("id"):
            frappe.logger().info(f"[QBO] Successfully synced {docname} via wrapper. QBO ID: {result.get('id')}")
        elif result and result.get("error"):
             frappe.log_error(f"Sync failed for {docname}: {result.get('error')}", "QuickBooks Sync Wrapper Error")

    except Exception as e:
        frappe.log_error(f"Wrapper Exception for {docname}: {str(e)}", "QuickBooks Sync Wrapper Exception")
        raise e



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
    """Start QuickBooks → ERPNext item sync in background"""
    refresh_quickbooks_access_token()

    settings = frappe.get_doc("QuickBooks Settings")
    if not settings.enable or not settings.allow_item_sync_from_quickbooks:
        frappe.msgprint(
            "Navigate to QuickBooks Settings and enable Item Sync.",
            title="QuickBooks Item Sync Disabled",
            indicator="red",
        )
        return

    frappe.enqueue(item_sync, queue="long", timeout=600)
    frappe.msgprint("Item sync from QuickBooks has started in the background.")


def item_sync():
    """Sync items from QuickBooks to ERPNext with pagination"""
    settings = frappe.get_doc("QuickBooks Settings")

    headers = {
        "Authorization": f"Bearer {settings.access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    base_url = settings.base_url.rstrip("/")
    company_id = settings.quickbooks_company_id
    minor_version = settings.minor_version or "75"

    start_position = 1
    max_results = 100
    total_fetched = 0
    total_synced = 0
    total_skipped = 0
    total_errors = 0

    while True:
        query = f"SELECT * FROM Item STARTPOSITION {start_position} MAXRESULTS {max_results}"
        url = f"{base_url}/v3/company/{company_id}/query"

        response = requests.get(
            url,
            headers=headers,
            params={"query": query, "minorversion": minor_version},
        )
        response.raise_for_status()

        items = response.json().get("QueryResponse", {}).get("Item", [])
        if not items:
            break

        # Handle single item response (QuickBooks sometimes returns dict instead of list)
        if isinstance(items, dict):
            items = [items]

        total_fetched += len(items)

        for qb_item in items:
            try:
                result = create_or_update_item(qb_item)
                if result == "skipped":
                    total_skipped += 1
                elif result == "error":
                    total_errors += 1
                else:
                    total_synced += 1
            except Exception as e:
                total_errors += 1
                qb_id = qb_item.get("Id", "Unknown")
                frappe.log_error(
                    f"Unexpected error processing item QB ID {qb_id}: {str(e)}\n{frappe.get_traceback()}",
                    "QuickBooks Item Sync - Unexpected Error"
                )

        if len(items) < max_results:
            break

        start_position += max_results

    # Log summary
    frappe.log_error(
        f"[QB SYNC] Item sync completed. Total fetched: {total_fetched}, Synced: {total_synced}, Skipped: {total_skipped}, Errors: {total_errors}",
        "QuickBooks Item Sync Summary"
    )


def create_or_update_item(qb_item):
    """Create or update ERPNext Item from QuickBooks data"""

    qb_id = qb_item.get("Id")
    qb_name = qb_item.get("Name")
    qb_description = qb_item.get("Description", qb_name)

    if not qb_name:
        frappe.log_error(
            f"[QB SYNC] Item QB ID {qb_id} skipped — no Name found",
            "QuickBooks Item Sync - Missing Name"
        )
        return "skipped"

    existing_item = frappe.db.exists(
        "Item", {"custom_quickbooks_item_id": qb_id}
    )

    if existing_item:
        item = frappe.get_doc("Item", existing_item)
    else:
        item = frappe.new_doc("Item")

        item_code = qb_name
        if frappe.db.exists("Item", item_code):
            item_code = f"{qb_name}-{qb_id}"

        item.item_code = item_code
        item.item_group = "All Item Groups"

    item.item_name = qb_name
    item.description = qb_description
    item.custom_quickbooks_item_id = qb_id
    item.custom_publish_on_app = 1
    item.stock_uom = "Unit"
    item.default_unit_of_measure = "Unit"

    if qb_item.get("UnitPrice") is not None:
        item.standard_rate = float(qb_item["UnitPrice"])

    parent_ref = qb_item.get("ParentRef")
    if parent_ref and parent_ref.get("value"):
        item_group = frappe.db.get_value(
            "Item Group",
            {"custom_quickbooks_item_group_id": parent_ref["value"]},
            "name",
        )
        if item_group:
            item.item_group = item_group

    # Map tax with error logging
    tax_mapping_result = map_item_tax(item, qb_item)

    try:
        item.save(ignore_permissions=True)
        frappe.db.commit()
        frappe.logger().info(
            f"[QB SYNC] Item synced: {item.item_code} (QB ID: {qb_id})"
        )
        return "success"
    except Exception as e:
        frappe.log_error(
            f"Item sync failed for QB ID {qb_id}, Name: {qb_name}, Tax Mapping: {tax_mapping_result}, Error: {str(e)}\n{frappe.get_traceback()}",
            "QuickBooks Item Sync Error",
        )
        return "error"


def map_item_tax(item, qb_item):
    """
    Map QuickBooks tax code to ERPNext Item Tax Template.
    Returns status string for logging purposes.
    """
    qb_id = qb_item.get("Id", "Unknown")
    item_code = getattr(item, "item_code", "Unknown")

    tax_ref = qb_item.get("SalesTaxCodeRef")
    if not tax_ref:
        frappe.log_error(
            f"[QB SYNC] Tax mapping skipped for Item {item_code} (QB ID: {qb_id}) - No SalesTaxCodeRef found in QuickBooks item",
            "QuickBooks Tax Mapping - No Tax Reference"
        )
        return "no_tax_ref"

    tax_code = tax_ref.get("value")
    if not tax_code:
        frappe.log_error(
            f"[QB SYNC] Tax mapping failed for Item {item_code} (QB ID: {qb_id}) - SalesTaxCodeRef exists but value is missing. TaxRef: {tax_ref}",
            "QuickBooks Tax Mapping - Missing Tax Code Value"
        )
        return "missing_tax_code"

    tax_template = frappe.db.get_value(
        "Item Tax Template",
        {"custom_quickbooks_gst_id": tax_code},
        "name",
    )

    if not tax_template:
        frappe.log_error(
            f"[QB SYNC] Tax mapping failed for Item {item_code} (QB ID: {qb_id}) - No Item Tax Template found for QuickBooks GST code '{tax_code}'. Please create an Item Tax Template with custom_quickbooks_gst_id = '{tax_code}'",
            "QuickBooks Tax Mapping - Template Not Found"
        )
        return f"template_not_found_{tax_code}"

    # Check if tax template already exists on item
    if any(t.item_tax_template == tax_template for t in item.taxes):
        frappe.logger().info(
            f"[QB SYNC] Tax template '{tax_template}' already exists on Item {item_code} (QB ID: {qb_id})"
        )
        return f"already_mapped_{tax_template}"

    # Add tax template to item
    try:
        item.append("taxes", {"item_tax_template": tax_template})
        frappe.logger().info(
            f"[QB SYNC] Tax mapping successful for Item {item_code} (QB ID: {qb_id}) - Mapped QuickBooks GST code '{tax_code}' to Item Tax Template '{tax_template}'"
        )
        return f"mapped_{tax_template}"
    except Exception as e:
        frappe.log_error(
            f"[QB SYNC] Tax mapping error for Item {item_code} (QB ID: {qb_id}) - Failed to append tax template '{tax_template}'. Error: {str(e)}\n{frappe.get_traceback()}",
            "QuickBooks Tax Mapping - Append Error"
        )
        return f"append_error_{tax_template}"

# @frappe.whitelist()
# def start_item_background():
#     refresh_quickbooks_access_token()

#     """Enqueue item sync job to run in background."""

#     settings = frappe.get_doc("QuickBooks Settings")
#     if settings.allow_item_sync_from_quickbooks != 1 and not settings.enable:
#         frappe.frappe.msgprint('Navigate to Quickbooks Settings & Please enable Item sync to continue', title="QuickBooks Item Sync Disabled",
#                                 indicator="red",
#                             )
#         return

#     frappe.enqueue(item_sync, queue='long', timeout=300)
#     frappe.msgprint("Item sync from QuickBooks has been started in the background.")

# @frappe.whitelist()
# def item_sync():
#     """Sync items from QuickBooks to ERPNext with pagination support."""
#     settings = frappe.get_doc("QuickBooks Settings")
#     access_token = settings.access_token
#     company_id = settings.quickbooks_company_id
#     base_url = settings.base_url.strip().rstrip("/")
#     minor_version = settings.minor_version or "75"

#     start_position = 1
#     max_results = 100  # QuickBooks allows max 100 per page

#     while True:
#         query = f"SELECT * FROM Item STARTPOSITION {start_position} MAXRESULTS {max_results}"
#         url = f"{base_url}/v3/company/{company_id}/query?query={query.replace(' ', '%20')}&minorversion={minor_version}"

#         headers = {
#             "Authorization": f"Bearer {access_token}",
#             "Content-Type": "application/json",
#             "Accept": "application/json"
#         }

#         try:
#             response = requests.get(url, headers=headers)
#             response.raise_for_status()
#             data = response.json()

#             items = data.get("QueryResponse", {}).get("Item", [])
#             if not items:
#                 break  # No more items to fetch

#             for qb_item in items:
#                 create_or_update_item(qb_item)

#             frappe.logger().info(f"[QB SYNC] Fetched {len(items)} items starting from {start_position}.")

#             if len(items) < max_results:
#                 break  # Last page reached

#             start_position += max_results

#         except Exception as e:
#             frappe.log_error(message=str(e), title="QuickBooks Item Sync Failed")
#             break


# @frappe.whitelist()
# def create_or_update_item(qb_item):
#     """Create or update item in ERPNext based on QuickBooks item data."""

#     qb_id = qb_item.get("Id")
#     item_name = qb_item.get("Description", "Unnamed Item")
#     item_type = qb_item.get("Type", "Inventory")

#     # Check if the item already exists in ERPNext
#     existing = frappe.db.exists("Item", {"custom_quickbooks_item_id": qb_id})
#     if existing:
#         item = frappe.get_doc("Item", existing)
#     else:
#         item = frappe.new_doc("Item")

#     # Check if SalesTaxCodeRef exists and add tax template
#     if qb_item.get("SalesTaxCodeRef") is not None:
#         tax_code = qb_item["SalesTaxCodeRef"].get("value")
#         if tax_code:
#             # Fetch the tax template based on QuickBooks GST Code
#             tax_template = frappe.get_all("Item Tax Template", filters={"custom_quickbooks_gst_id": tax_code}, limit=1)

#             if tax_template:
#                 # Check if the tax template is already attached to the item
#                 existing_tax = frappe.get_all("Item Tax Template", filters={"custom_quickbooks_gst_id": tax_code}, limit=1)

#                 if not existing_tax:  # Only add if the tax template is not already linked
#                     item.append("taxes", {
#                         "item_tax_template": tax_template[0].name,
#                     })
#                 else:
#                     frappe.logger().info(f"[Item Sync] Tax Template {tax_template[0].name} already exists for Item {item_name}, skipping duplicate.")
#             else:
#                 frappe.logger().warn(f"[Item Sync] No Item Tax Template found for QuickBooks GST Code: {tax_code}")

#     # Set other item properties
#     item.item_name = qb_item.get("Description", "")
#     item.custom_quickbooks_item_id = qb_id
#     item.item_type = item_type
#     item.description = qb_item.get("Description", "")
#     item.stock_uom = "Unit"
#     item.default_unit_of_measure = "Unit"

#     if "UnitPrice" in qb_item:
#         item.standard_rate = float(qb_item["UnitPrice"])

#     # Map category from QBO to item group in ERPNext
#     # Check if the item has a ParentRef (category) from QuickBooks
#     parent_ref = qb_item.get("ParentRef")
#     category_mapped = False

#     if parent_ref:
#         parent_qb_id = parent_ref.get("value")
#         if parent_qb_id:
#             # Find the Item Group in ERPNext that matches the QuickBooks category ID
#             item_group_name = frappe.db.get_value("Item Group", {"custom_quickbooks_item_group_id": parent_qb_id}, "name")
#             if item_group_name:
#                 item.item_group = item_group_name
#                 category_mapped = True
#                 frappe.logger().info(f"[Item Sync] Mapped QBO category {parent_qb_id} to Item Group '{item_group_name}' for item '{item_name}'")
#             else:
#                 # Category not found in ERPNext
#                 frappe.logger().warn(f"[Item Sync] QBO category {parent_qb_id} not found in ERPNext Item Groups. Using default for item '{item_name}'")

#     # If no category was mapped, use default item group for new items
#     if not category_mapped and not existing:
#         item.item_group = "All Item Groups"

#     try:
#         # Save item and commit
#         item.save(ignore_permissions=True)
#         frappe.db.commit()
#         frappe.logger().info(f"[Item Sync] Item '{item_name}' (QB ID: {qb_id}) synced successfully.")
#     except Exception as e:
#         # Log error if saving the item fails, include item name in the log
#         error_message = f"Failed to sync item '{item_name}' (QB ID: {qb_id}) due to error: {str(e)}"
#         frappe.log_error(message=error_message, title=f"Failed to sync item {qb_id}")

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
    """Kick off QuickBooks stock sync in small background jobs."""
    refresh_quickbooks_access_token()

    settings = frappe.get_single("QuickBooks Settings")
    if not settings.enable:
        frappe.throw(
            "Please enable QuickBooks Integration in QuickBooks Settings"
        )

    frappe.enqueue(
        sync_stock_page,
        start_position=1,
        queue="long",
        timeout=300,
        job_name="qb_stock_sync_page_1"
    )

    frappe.msgprint("QuickBooks stock sync started in background.")


def sync_stock_page(start_position: int):
    """Sync one page of inventory items from QuickBooks."""

    BATCH_SIZE = 50

    settings = frappe.get_single("QuickBooks Settings")
    base_url = settings.base_url.rstrip("/")
    company_id = settings.quickbooks_company_id
    minor_version = settings.minor_version or "75"

    access_token = settings.access_token
    if not access_token:
        refresh_quickbooks_access_token()
        access_token = frappe.get_single("QuickBooks Settings").access_token

    query = (
        f"SELECT * FROM Item WHERE Type='Inventory' "
        f"STARTPOSITION {start_position} MAXRESULTS {BATCH_SIZE}"
    )

    url = (
        f"{base_url}/v3/company/{company_id}/query"
        f"?query={query.replace(' ', '%20')}"
        f"&minorversion={minor_version}"
    )

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)

        if response.status_code == 401:
            refresh_quickbooks_access_token()
            headers["Authorization"] = (
                f"Bearer {frappe.get_single('QuickBooks Settings').access_token}"
            )
            response = requests.get(url, headers=headers, timeout=30)

        response.raise_for_status()
        data = response.json()

    except Exception as e:
        frappe.log_error(
            f"QuickBooks fetch failed at position {start_position}\n{frappe.get_traceback()}",
            "QB Stock Sync - API Error"
        )
        return

    items = data.get("QueryResponse", {}).get("Item", [])
    is_last_page = False

    if not items:
        is_last_page = True
    else:
        if isinstance(items, dict):
            items = [items]

        # Check if this is the last page (less than BATCH_SIZE items returned)
        if len(items) < BATCH_SIZE:
            is_last_page = True

    reconciliation_items = []

    if items:
        for qb_item in items:
            try:
                item_data = prepare_item_for_reconciliation(qb_item)
                if item_data:
                    reconciliation_items.append(item_data)
            except Exception:
                frappe.log_error(
                    f"Item preparation failed: {qb_item.get('Id')}\n{frappe.get_traceback()}",
                    "QB Stock Sync - Item Error"
                )

    if reconciliation_items:
        create_stock_reconciliation(reconciliation_items, is_last_page=is_last_page)
        frappe.db.commit()

    # Enqueue next page if not last page
    if not is_last_page and len(items) == BATCH_SIZE:
        frappe.enqueue(
            sync_stock_page,
            start_position=start_position + BATCH_SIZE,
            queue="long",
            timeout=300,
            job_name=f"qb_stock_sync_page_{start_position + BATCH_SIZE}"
        )


# @frappe.whitelist()
# def start_stock_sync_background():
#     """Enqueue stock sync job to run in background."""
#     refresh_quickbooks_access_token()

#     settings = frappe.get_doc("QuickBooks Settings")
#     if not settings.enable:
#         frappe.msgprint(
#             'Navigate to Quickbooks Settings & Please enable QuickBooks Integration to continue',
#             title="QuickBooks Integration Disabled",
#             indicator="red"
#         )
#         return

#     frappe.enqueue(sync_stock_from_quickbooks, queue='long', timeout=600)
#     frappe.msgprint("Stock sync and reconciliation from QuickBooks has been started in the background.")


@frappe.whitelist()
def sync_stock_from_quickbooks():
    """
    Deprecated function - kept for RQ job deserialization compatibility.
    This function was replaced by sync_stock_page for better pagination handling.
    Old jobs referencing this function will fail gracefully.
    """
    frappe.log_error(
        "[QB SYNC] Deprecated sync_stock_from_quickbooks called. Use start_stock_sync_background instead.",
        "QuickBooks Stock Sync - Deprecated Function"
    )
    return {"status": "deprecated", "message": "This function has been replaced. Please use start_stock_sync_background instead."}


# @frappe.whitelist()
# def sync_stock_from_quickbooks_old():
#     """Sync inventory stock quantities from QuickBooks to ERPNext with stock reconciliation."""
#     frappe.log_error("[QB SYNC] Started stock sync job", "QuickBooks Stock Sync - Started")

#     try:
#         # Refresh access token before syncing (tokens expire after 1 hour)
#         try:
#             refresh_quickbooks_access_token()
#         except Exception as token_error:
#             frappe.log_error(
#                 message=f"Failed to refresh QuickBooks access token: {str(token_error)}\n{frappe.get_traceback()}",
#                 title="QuickBooks Stock Sync - Token Refresh Failed"
#             )
#             return

#         settings = frappe.get_single("QuickBooks Settings")
#         access_token = settings.access_token
#         company_id = settings.quickbooks_company_id
#         base_url = settings.base_url.strip().rstrip("/")
#         minor_version = settings.minor_version or "75"

#         if not access_token or not company_id:
#             frappe.log_error("Missing QuickBooks access token or company ID", "QuickBooks Stock Sync Failed")
#             return

#         start_position = 1
#         max_results = 100
#         synced_count = 0
#         error_count = 0
#         reconciliation_items = []  # Collect items for batch reconciliation

#         while True:
#             # Query inventory items from QuickBooks
#             query = f"SELECT * FROM Item WHERE Type='Inventory' STARTPOSITION {start_position} MAXRESULTS {max_results}"
#             url = f"{base_url}/v3/company/{company_id}/query?query={query.replace(' ', '%20')}&minorversion={minor_version}"

#             headers = {
#                 "Authorization": f"Bearer {access_token}",
#                 "Content-Type": "application/json",
#                 "Accept": "application/json"
#             }

#             try:
#                 response = requests.get(url, headers=headers, timeout=60)

#                 # Handle 401 Unauthorized - token expired, refresh and retry
#                 if response.status_code == 401:
#                     frappe.log_error(f"[QB SYNC] Got 401 at position {start_position}, refreshing token and retrying...", "QuickBooks Stock Sync - Token Refresh")
#                     try:
#                         refresh_quickbooks_access_token()
#                         settings = frappe.get_single("QuickBooks Settings")  # Reload settings
#                         access_token = settings.access_token
#                         headers["Authorization"] = f"Bearer {access_token}"

#                         # Retry the request with new token
#                         response = requests.get(url, headers=headers, timeout=60)
#                         response.raise_for_status()
#                     except Exception as retry_error:
#                         error_msg = (
#                             f"401 Unauthorized - Token expired and refresh/retry failed.\n"
#                             f"Position: {start_position}\n"
#                             f"Error: {str(retry_error)}\n"
#                             f"{frappe.get_traceback()}"
#                         )
#                         frappe.log_error(error_msg, "QuickBooks Stock Sync - Token Refresh Failed")
#                         break
#                 else:
#                     response.raise_for_status()

#                 data = response.json()

#                 # Handle both list and single dict responses from QuickBooks API
#                 items = data.get("QueryResponse", {}).get("Item", [])
#                 if not items:
#                     break  # No more items to fetch

#                 # Ensure items is a list (QuickBooks sometimes returns a single dict)
#                 if isinstance(items, dict):
#                     items = [items]

#                 for qb_item in items:
#                     try:
#                         item_data = prepare_item_for_reconciliation(qb_item)
#                         if item_data:
#                             reconciliation_items.append(item_data)
#                             synced_count += 1
#                     except Exception as e:
#                         error_count += 1
#                         frappe.log_error(
#                             message=f"Error preparing stock for item {qb_item.get('Id')}: {str(e)}\n{frappe.get_traceback()}",
#                             title="QuickBooks Stock Sync Item Error"
#                         )

#                 frappe.log_error(f"[QB SYNC] Fetched {len(items)} inventory items starting from {start_position}.", "QuickBooks Stock Sync - Progress")

#                 # Periodic commit to prevent memory issues and save progress
#                 if synced_count % 500 == 0:
#                     frappe.db.commit()
#                     frappe.log_error(f"[QB SYNC] Committed progress: {synced_count} items processed so far.", "QuickBooks Stock Sync - Progress")

#                 if len(items) < max_results:
#                     break  # Last page reached

#                 start_position += max_results

#             except requests.exceptions.Timeout:
#                 frappe.log_error(
#                     message=f"Request timeout at position {start_position}",
#                     title="QuickBooks Stock Sync - Timeout"
#                 )
#                 break
#             except requests.exceptions.RequestException as e:
#                 frappe.log_error(
#                     message=f"Request error at position {start_position}: {str(e)}",
#                     title="QuickBooks Stock Sync - Request Error"
#                 )
#                 break
#             except Exception as e:
#                 frappe.log_error(
#                     message=f"Unexpected error at position {start_position}: {str(e)}\n{frappe.get_traceback()}",
#                     title="QuickBooks Stock Sync Failed"
#                 )
#                 break

#         # Create Stock Reconciliation entries
#         if reconciliation_items:
#             try:
#                 create_stock_reconciliation(reconciliation_items)
#                 frappe.log_error(f"[QB SYNC] Created Stock Reconciliation with {len(reconciliation_items)} items.", "QuickBooks Stock Sync - Success")
#             except Exception as e:
#                 frappe.log_error(
#                     message=f"Error creating Stock Reconciliation: {str(e)}\n{frappe.get_traceback()}",
#                     title="QuickBooks Stock Reconciliation Error"
#                 )
#                 error_count += len(reconciliation_items)

#         frappe.log_error(f"[QB SYNC] Completed stock sync job. Synced: {synced_count}, Errors: {error_count}", "QuickBooks Stock Sync - Completed")
#         frappe.db.commit()

#     except Exception as e:
#         frappe.log_error(
#             message=f"Critical error in stock sync job: {str(e)}\n{frappe.get_traceback()}",
#             title="QuickBooks Stock Sync - Critical Error"
#         )
#         frappe.db.rollback()
#         raise


def prepare_item_for_reconciliation(qb_item):
    """Prepare item data for stock reconciliation."""
    qb_id = qb_item.get("Id")
    if not qb_id:
        frappe.log_error("[QB SYNC] Missing Item ID in QuickBooks data.", "QuickBooks Stock Sync - Item Error")
        return None

    # Find ERPNext item by QuickBooks ID
    erpnext_item = frappe.db.get_value("Item", {"custom_quickbooks_item_id": qb_id}, "name")
    if not erpnext_item:
        frappe.log_error(f"[QB SYNC] Item with QuickBooks ID {qb_id} not found in ERPNext. Skipping stock update.", "QuickBooks Stock Sync - Item Not Found")
        return None

    # Get item doc to check if it's a stock item
    item_doc = frappe.get_doc("Item", erpnext_item)
    if not item_doc.is_stock_item:
        frappe.log_error(f"[QB SYNC] Item {erpnext_item} is not a stock item. Skipping stock update.", "QuickBooks Stock Sync - Not Stock Item")
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
        frappe.log_error(f"[QB SYNC] No enabled warehouse found for item {erpnext_item}. Skipping stock update.", "QuickBooks Stock Sync - No Warehouse")
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


def create_stock_reconciliation(reconciliation_items, is_last_page=False):
    """Create or update Stock Reconciliation document with items from QuickBooks."""
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
            frappe.log_error("[QB SYNC] No company found. Cannot create Stock Reconciliation.", "QuickBooks Stock Sync - No Company")
            return

    company_abbr = frappe.db.get_value("Company", company, "abbr") or ""

    # Group items by warehouse for better organization
    warehouse_groups = {}
    for item in reconciliation_items:
        warehouse = item["warehouse"]
        if warehouse not in warehouse_groups:
            warehouse_groups[warehouse] = []
        warehouse_groups[warehouse].append(item)

    # Create or update Stock Reconciliation for each warehouse
    for warehouse, items in warehouse_groups.items():
        try:
            # Get warehouse company to ensure consistency
            warehouse_company = frappe.db.get_value("Warehouse", warehouse, "company")
            reconciliation_company = warehouse_company or company

            # Find the most recent draft Stock Reconciliation for this warehouse/company
            # Get the most recently created draft SR that has items for this warehouse
            existing_sr_name = frappe.db.sql("""
                SELECT DISTINCT sr.name
                FROM `tabStock Reconciliation` sr
                INNER JOIN `tabStock Reconciliation Item` sri ON sr.name = sri.parent
                WHERE sr.docstatus = 0
                AND sr.company = %s
                AND sri.warehouse = %s
                ORDER BY sr.creation DESC
                LIMIT 1
            """, (reconciliation_company, warehouse), as_dict=True)

            is_new_document = False

            if existing_sr_name and existing_sr_name[0].get("name"):
                # Update existing draft Stock Reconciliation
                sr_name = existing_sr_name[0]["name"]
                stock_reconciliation = frappe.get_doc("Stock Reconciliation", sr_name)
                frappe.log_error(
                    f"[QB SYNC] Found existing draft Stock Reconciliation {sr_name} for warehouse {warehouse}. Updating...",
                    "QuickBooks Stock Sync - Found Existing SR"
                )
            else:
                # Create new draft Stock Reconciliation
                is_new_document = True
                stock_reconciliation = frappe.new_doc("Stock Reconciliation")
                stock_reconciliation.company = reconciliation_company

                # Determine purpose: Opening Stock or regular reconciliation
                existing_sr_count = frappe.db.count("Stock Reconciliation", {"company": reconciliation_company, "docstatus": 1})
                purpose = "Opening Stock" if existing_sr_count == 0 else "Stock Reconciliation"
                stock_reconciliation.purpose = purpose

                # Set expense account for opening stock
                if purpose == "Opening Stock" and company_abbr:
                    expense_account = f"Temporary Opening - {company_abbr}"
                    # Check if account exists, if not, skip it
                    if frappe.db.exists("Account", expense_account):
                        stock_reconciliation.expense_account = expense_account

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

            # Add items to Stock Reconciliation (avoid duplicates)
            if items_list:
                existing_item_codes = {row.item_code for row in stock_reconciliation.items}

                for item_entry in items_list:
                    # Update existing item or add new one
                    if item_entry["item_code"] in existing_item_codes:
                        # Update existing item row
                        for row in stock_reconciliation.items:
                            if row.item_code == item_entry["item_code"] and row.warehouse == warehouse:
                                row.qty = item_entry["qty"]
                                row.valuation_rate = item_entry["valuation_rate"]
                                if "batch_no" in item_entry:
                                    row.batch_no = item_entry["batch_no"]
                                break
                    else:
                        # Add new item row
                        stock_reconciliation.append("items", item_entry)
                        existing_item_codes.add(item_entry["item_code"])

                # Save the document (insert if new, save if existing)
                if is_new_document:
                    stock_reconciliation.insert(ignore_permissions=True)
                    frappe.log_error(
                        f"[QB SYNC] Created Stock Reconciliation {stock_reconciliation.name} "
                        f"for warehouse {warehouse} with {len(items_list)} items.",
                        "QuickBooks Stock Sync - Stock Reconciliation Created"
                    )
                else:
                    stock_reconciliation.save(ignore_permissions=True)
                    frappe.log_error(
                        f"[QB SYNC] Updated Stock Reconciliation {stock_reconciliation.name} "
                        f"for warehouse {warehouse} with {len(items_list)} items.",
                        "QuickBooks Stock Sync - Stock Reconciliation Updated"
                    )

                # Submit only if this is the last page
                if is_last_page:
                    stock_reconciliation.reload()  # Reload to get latest state
                    stock_reconciliation.submit()
                    frappe.log_error(
                        f"[QB SYNC] Submitted Stock Reconciliation {stock_reconciliation.name} "
                        f"for warehouse {warehouse}.",
                        "QuickBooks Stock Sync - Stock Reconciliation Submitted"
                    )
            else:
                frappe.log_error(f"[QB SYNC] No items to reconcile for warehouse {warehouse}.", "QuickBooks Stock Sync - No Items")

        except Exception as e:
            frappe.log_error(
                message=f"Error creating/updating Stock Reconciliation for warehouse {warehouse}: {str(e)}\n{frappe.get_traceback()}",
                title="QuickBooks Stock Reconciliation Creation Error"
            )
            # Don't re-raise - continue with other warehouses instead of crashing the worker
            frappe.log_error(f"[QB SYNC] Skipping warehouse {warehouse} due to error, continuing with others...", "QuickBooks Stock Sync - Warehouse Skipped")



@frappe.whitelist()
def refresh_all_counts(docname: str):
    """
    Refresh all count fields and statistics for the QuickBooks Sync doctype (same pattern as MYOB Acumatica).
    Calls refresh_statistics to populate total/synced/remaining/status for each tab.
    """
    doc = frappe.get_doc("QuickBooks Sync", docname)
    # Populate Statistics section (total, synced, remaining, status) for all tabs
    doc.refresh_statistics()

    # Legacy counts (kept for backward compatibility)
    doc.items_count = frappe.db.count("Item")
    doc.suppliers_count = frappe.db.count("Supplier")
    doc.customers_count = frappe.db.count("Customer")
    doc.item_images_count = frappe.db.count("Item", {"image": ["!=", ""]})
    doc.sales_orders_count = frappe.db.count("Sales Order", {"docstatus": ["in", [0, 1]]})
    doc.sales_invoices_count = frappe.db.count("Sales Invoice", {"docstatus": ["in", [0, 1]]})
    doc.purchase_invoices_count = frappe.db.count("Purchase Invoice", {"docstatus": ["in", [0, 1]]})

    doc.save(ignore_permissions=True)
    frappe.db.commit()

    return {
        "success": True,
        "items_count": doc.items_count,
        "suppliers_count": doc.suppliers_count,
        "customers_count": doc.customers_count,
        "item_images_count": doc.item_images_count,
        "sales_orders_count": doc.sales_orders_count,
        "sales_invoices_count": doc.sales_invoices_count,
        "purchase_invoices_count": doc.purchase_invoices_count,
        "item_total_count": doc.item_total_count,
        "item_synced_count": doc.item_synced_count,
        "item_remaining_count": doc.item_remaining_count,
        "item_sync_status": doc.item_sync_status,
        "item_last_sync_date": doc.item_last_sync_date,
        "supplier_total_count": doc.supplier_total_count,
        "supplier_synced_count": doc.supplier_synced_count,
        "supplier_remaining_count": doc.supplier_remaining_count,
        "supplier_sync_status": doc.supplier_sync_status,
        "supplier_last_sync_date": doc.supplier_last_sync_date,
        "customer_total_count": doc.customer_total_count,
        "customer_synced_count": doc.customer_synced_count,
        "customer_remaining_count": doc.customer_remaining_count,
        "customer_disabled_count": doc.customer_disabled_count,
        "customer_sync_status": doc.customer_sync_status,
        "customer_last_sync_date": doc.customer_last_sync_date,
        "stock_total_count": doc.stock_total_count,
        "stock_synced_count": doc.stock_synced_count,
        "stock_remaining_count": doc.stock_remaining_count,
        "stock_sync_status": doc.stock_sync_status,
        "stock_last_sync_date": doc.stock_last_sync_date,
        "sales_order_total_count": doc.sales_order_total_count,
        "sales_order_synced_count": doc.sales_order_synced_count,
        "sales_order_remaining_count": doc.sales_order_remaining_count,
        "sales_order_sync_status": doc.sales_order_sync_status,
        "sales_order_last_sync_date": doc.sales_order_last_sync_date,
        "sales_invoice_total_count": getattr(doc, "sales_invoice_total_count", 0),
        "sales_invoice_synced_count": getattr(doc, "sales_invoice_synced_count", 0),
        "sales_invoice_remaining_count": getattr(doc, "sales_invoice_remaining_count", 0),
        "sales_invoice_sync_status": getattr(doc, "sales_invoice_sync_status", "Not Started"),
        "sales_invoice_last_sync_date": getattr(doc, "sales_invoice_last_sync_date", None),
        "purchase_invoice_total_count": getattr(doc, "purchase_invoice_total_count", 0),
        "purchase_invoice_synced_count": getattr(doc, "purchase_invoice_synced_count", 0),
        "purchase_invoice_remaining_count": getattr(doc, "purchase_invoice_remaining_count", 0),
        "purchase_invoice_sync_status": getattr(doc, "purchase_invoice_sync_status", "Not Started"),
        "purchase_invoice_last_sync_date": getattr(doc, "purchase_invoice_last_sync_date", None),
    }


@frappe.whitelist()
def refresh_item_list(docname: str):
    """
    Refresh the Item List child table inside QuickBooks Sync doctype.
    Marks items as synced if they have a custom_quickbooks_item_id.
    """
    doc = frappe.get_doc("QuickBooks Sync", docname)

    # Clear existing rows
    doc.set("item_list", [])

    # Fetch all items with custom_quickbooks_item_id field
    items = frappe.get_all(
        "Item",
        fields=["name", "custom_quickbooks_item_id"],
        order_by="name asc"
    )

    for item in items:
        # Check if custom_quickbooks_item_id exists and is not empty
        qb_id = item.get("custom_quickbooks_item_id")
        has_qb_id = bool(qb_id and str(qb_id).strip())

        doc.append("item_list", {
            "item_name": item.name,
            "is_synced": 1 if has_qb_id else 0,
        })

    doc.save(ignore_permissions=True)
    frappe.db.commit()

    return {"message": f"Refreshed {len(items)} items."}

@frappe.whitelist()
def refresh_supplier_list(docname: str):
    """
    Refresh the Supplier List child table inside QuickBooks Sync doctype.
    Marks suppliers as synced if they have a custom_quickbooks_supplier_id.
    """
    doc = frappe.get_doc("QuickBooks Sync", docname)

    # Clear existing rows
    doc.set("supplier_list", [])

    # Fetch all suppliers
    suppliers = frappe.get_all(
        "Supplier",
        fields=["name"],
        order_by="name asc"
    )

    for supplier in suppliers:
        has_qb_id = bool(frappe.db.get_value("Supplier", supplier.name, "custom_quickbooks_supplier_id"))

        doc.append("supplier_list", {
            "supplier_name": supplier.name,
            "is_synced": 1 if has_qb_id else 0,
        })

    doc.save(ignore_permissions=True)
    frappe.db.commit()

    return {"message": f"Refreshed {len(suppliers)} suppliers."}

@frappe.whitelist()
def refresh_customer_list(docname: str):
    """
    Refresh the Customer List child table inside QuickBooks Sync doctype.
    Marks customers as synced if they have a custom_quickbooks_customer_id.
    """
    doc = frappe.get_doc("QuickBooks Sync", docname)

    # Clear existing rows
    doc.set("customer_list", [])

    # Fetch all customers
    customers = frappe.get_all(
        "Customer",
        fields=["name"],
        order_by="name asc"
    )

    for customer in customers:
        has_qb_id = bool(frappe.db.get_value("Customer", customer.name, "custom_quickbooks_customer_id"))

        doc.append("customer_list", {
            "customer_name": customer.name,
            "is_synced": 1 if has_qb_id else 0,
        })

    doc.save(ignore_permissions=True)
    frappe.db.commit()

    return {"message": f"Refreshed {len(customers)} customers."}

@frappe.whitelist()
def refresh_sales_order_list(docname: str):
    """
    Refresh the Sales Order List child table inside QuickBooks Sync doctype.
    Marks sales orders as synced if they have a quickbooks_sales_order_id.
    Skips cancelled sales orders (docstatus = 2).
    """
    doc = frappe.get_doc("QuickBooks Sync", docname)

    # Clear existing rows
    doc.set("sales_order_list", [])

    # Fetch sales orders but exclude cancelled
    sales_orders = frappe.get_all(
        "Sales Order",
        fields=["name", "quickbooks_sales_order_id", "status", "docstatus"],
        filters={"docstatus": ["in", [0, 1]]}   # Only Draft (0) + Submitted (1)
    )

    for so in sales_orders:
        has_qb_id = bool(so.quickbooks_sales_order_id)

        doc.append("sales_order_list", {
            "sales_order_name": so.name,
            "status": "Success" if has_qb_id else "Pending",
            "is_synced": 1 if has_qb_id else 0,
        })

    doc.save(ignore_permissions=True)
    frappe.db.commit()

    return {"message": f"Refreshed {len(sales_orders)} sales orders (excluding cancelled)."}

@frappe.whitelist()
def bulk_sync_items(docname: str, selected_items=None):
    """
    Sync selected items from ERPNext to QuickBooks.
    Similar to bulk_sync_invoices but for items.
    """
    try:
        doc = frappe.get_doc("QuickBooks Sync", docname)

        # If coming from frontend with __checked rows
        if selected_items:
            # Ensure list is parsed correctly from JSON
            if isinstance(selected_items, str):
                import json
                selected_items = json.loads(selected_items)

            item_names = [d.get("item_name") for d in selected_items if d.get("item_name")]
        else:
            # fallback: all unsynced rows
            item_names = [row.item_name for row in doc.item_list if not row.is_synced]

        if not item_names:
            return {"message": "No items found for sync."}

        result = sync_selected_items(docname, item_names)

        return {
            "message": (
                f"📦 QuickBooks Item Sync Summary:\n"
                f"✅ Synced: {len(result.get('synced', []))}\n"
                f"❌ Failed: {len(result.get('failed', []))}\n"
                f"⏭ Skipped: {len(result.get('skipped', []))}\n"
                f"📦 Total Attempted: {len(item_names)}"
            )
        }

    except Exception:
        frappe.log_error("Bulk Sync Items Error", frappe.get_traceback())
        return {"message": "An error occurred while syncing items. Please check error logs."}

@frappe.whitelist()
def sync_selected_items(docname: str, selected_items: list):
    """
    Sync selected items to QuickBooks.
    Already synced items will be skipped.
    """
    if not selected_items:
        return {"synced": [], "failed": [], "skipped": []}

    from quickbooks_integration.api import create_item_on_quickbooks

    doc = frappe.get_doc("QuickBooks Sync", docname)
    synced, failed, skipped = [], [], []

    for item_name in selected_items:
        try:
            # Check child row first
            row = next((r for r in doc.item_list if r.item_name == item_name), None)
            if not row:
                skipped.append(item_name)
                continue

            if row.is_synced:  # already synced, skip
                skipped.append(item_name)
                continue

            # Check if item already has QuickBooks ID
            if frappe.db.get_value("Item", item_name, "custom_quickbooks_item_id"):
                row.is_synced = 1
                skipped.append(item_name)
                continue

            # Sync item to QuickBooks
            create_item_on_quickbooks(item_name)

            # Update row status
            row.is_synced = 1
            synced.append(item_name)

        except Exception as e:
            failed.append(item_name)
            frappe.log_error(f"Error syncing item {item_name}: {str(e)}", "QuickBooks Item Sync Error")

    doc.save(ignore_permissions=True)
    frappe.db.commit()

    return {"synced": synced, "failed": failed, "skipped": skipped}

@frappe.whitelist()
def bulk_sync_suppliers(docname: str, selected_suppliers=None):
    """
    Sync selected suppliers from ERPNext to QuickBooks.
    """
    try:
        doc = frappe.get_doc("QuickBooks Sync", docname)

        # If coming from frontend with __checked rows
        if selected_suppliers:
            if isinstance(selected_suppliers, str):
                import json
                selected_suppliers = json.loads(selected_suppliers)

            supplier_names = [d.get("supplier_name") for d in selected_suppliers if d.get("supplier_name")]
        else:
            supplier_names = [row.supplier_name for row in doc.supplier_list if not row.is_synced]

        if not supplier_names:
            return {"message": "No suppliers found for sync."}

        result = sync_selected_suppliers(docname, supplier_names)

        return {
            "message": (
                f"👥 QuickBooks Supplier Sync Summary:\n"
                f"✅ Synced: {len(result.get('synced', []))}\n"
                f"❌ Failed: {len(result.get('failed', []))}\n"
                f"⏭ Skipped: {len(result.get('skipped', []))}\n"
                f"📦 Total Attempted: {len(supplier_names)}"
            )
        }

    except Exception:
        frappe.log_error("Bulk Sync Suppliers Error", frappe.get_traceback())
        return {"message": "An error occurred while syncing suppliers. Please check error logs."}

@frappe.whitelist()
def sync_selected_suppliers(docname: str, selected_suppliers: list):
    """
    Sync selected suppliers to QuickBooks.
    """
    if not selected_suppliers:
        return {"synced": [], "failed": [], "skipped": []}

    import requests
    from frappe import _

    doc = frappe.get_doc("QuickBooks Sync", docname)
    synced, failed, skipped = [], [], []

    settings = frappe.get_single("QuickBooks Settings")
    if not (settings.enable and settings.allow_supplier_sync_to_quickbooks):
        frappe.throw(_("Please enable Supplier sync in QuickBooks Settings"))

    url = f"{settings.base_url.strip().rstrip('/')}/v3/company/{settings.quickbooks_company_id}/vendor?minorversion={settings.minor_version or '75'}"
    headers = {
        "Authorization": f"Bearer {settings.access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    for supplier_name in selected_suppliers:
        try:
            row = next((r for r in doc.supplier_list if r.supplier_name == supplier_name), None)
            if not row:
                skipped.append(supplier_name)
                continue

            if row.is_synced:
                skipped.append(supplier_name)
                continue

            # Check if already synced
            if frappe.db.get_value("Supplier", supplier_name, "custom_quickbooks_supplier_id"):
                row.is_synced = 1
                skipped.append(supplier_name)
                continue

            supplier = frappe.get_doc("Supplier", supplier_name)
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
                frappe.db.set_value("Supplier", supplier_name, "custom_quickbooks_supplier_id", qbo_id)
                row.is_synced = 1
                synced.append(supplier_name)
            else:
                failed.append(supplier_name)

        except Exception as e:
            failed.append(supplier_name)
            frappe.log_error(f"Error syncing supplier {supplier_name}: {str(e)}", "QuickBooks Supplier Sync Error")

    doc.save(ignore_permissions=True)
    frappe.db.commit()

    return {"synced": synced, "failed": failed, "skipped": skipped}

@frappe.whitelist()
def bulk_sync_customers(docname: str, selected_customers=None):
    """
    Sync selected customers from ERPNext to QuickBooks.
    """
    try:
        doc = frappe.get_doc("QuickBooks Sync", docname)

        if selected_customers:
            if isinstance(selected_customers, str):
                import json
                selected_customers = json.loads(selected_customers)

            customer_names = [d.get("customer_name") for d in selected_customers if d.get("customer_name")]
        else:
            customer_names = [row.customer_name for row in doc.customer_list if not row.is_synced]

        if not customer_names:
            return {"message": "No customers found for sync."}

        result = sync_selected_customers(docname, customer_names)

        return {
            "message": (
                f"👤 QuickBooks Customer Sync Summary:\n"
                f"✅ Synced: {len(result.get('synced', []))}\n"
                f"❌ Failed: {len(result.get('failed', []))}\n"
                f"⏭ Skipped: {len(result.get('skipped', []))}\n"
                f"📦 Total Attempted: {len(customer_names)}"
            )
        }

    except Exception:
        frappe.log_error("Bulk Sync Customers Error", frappe.get_traceback())
        return {"message": "An error occurred while syncing customers. Please check error logs."}

@frappe.whitelist()
def sync_selected_customers(docname: str, selected_customers: list):
    """
    Sync selected customers to QuickBooks.
    """
    if not selected_customers:
        return {"synced": [], "failed": [], "skipped": []}

    import requests
    from frappe import _

    doc = frappe.get_doc("QuickBooks Sync", docname)
    synced, failed, skipped = [], [], []

    settings = frappe.get_doc("QuickBooks Settings")
    minor_version = settings.minor_version or "75"
    realm_id = settings.quickbooks_company_id
    access_token = settings.access_token
    base_url = settings.base_url.strip()

    if not base_url.startswith("http"):
        base_url = "https://" + base_url

    endpoint = f"{base_url}/v3/company/{realm_id}/customer?minorversion={minor_version}"

    for customer_name in selected_customers:
        try:
            row = next((r for r in doc.customer_list if r.customer_name == customer_name), None)
            if not row:
                skipped.append(customer_name)
                continue

            if row.is_synced:
                skipped.append(customer_name)
                continue

            # Check if already synced
            if frappe.db.get_value("Customer", customer_name, "custom_quickbooks_customer_id"):
                row.is_synced = 1
                skipped.append(customer_name)
                continue

            customer = frappe.get_doc("Customer", customer_name)
            email = customer.email_id or get_primary_contact_email(customer_name)
            address = None

            if customer.customer_primary_address:
                address = frappe.get_doc("Address", customer.customer_primary_address)
            else:
                address = get_billing_address_for_customer(customer_name)

            payload = {
                "DisplayName": customer.customer_name,
                "PrimaryEmailAddr": {"Address": email} if email else None,
                "PrimaryPhone": {"FreeFormNumber": customer.mobile_no} if customer.get("mobile_no") else None,
                "BillAddr": {
                    "Line1": address.address_line1 if address else "",
                    "City": address.city if address else "",
                    "CountrySubDivisionCode": address.state if address else "",
                    "PostalCode": address.pincode if address else "",
                    "Country": address.country if address else ""
                } if address else None,
                "Notes": f"Imported from ERPNext Customer: {customer_name}"
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
                quickbooks_customer_id = res.json().get('Customer', {}).get('Id')
                if quickbooks_customer_id:
                    frappe.db.set_value("Customer", customer_name, "custom_quickbooks_customer_id", quickbooks_customer_id)
                    frappe.db.set_value("Customer", customer_name, "custom_is_customer_synced", 1)
                    row.is_synced = 1
                    synced.append(customer_name)
                else:
                    failed.append(customer_name)
            else:
                failed.append(customer_name)

        except Exception as e:
            failed.append(customer_name)
            frappe.log_error(f"Error syncing customer {customer_name}: {str(e)}", "QuickBooks Customer Sync Error")

    doc.save(ignore_permissions=True)
    frappe.db.commit()

    return {"synced": synced, "failed": failed, "skipped": skipped}

@frappe.whitelist()
def bulk_sync_sales_orders(docname: str, selected_sales_orders=None):
    """
    Sync selected sales orders from ERPNext to QuickBooks.
    """
    try:
        doc = frappe.get_doc("QuickBooks Sync", docname)

        if selected_sales_orders:
            if isinstance(selected_sales_orders, str):
                import json
                selected_sales_orders = json.loads(selected_sales_orders)

            so_names = [d.get("sales_order_name") for d in selected_sales_orders if d.get("sales_order_name")]
        else:
            so_names = [row.sales_order_name for row in doc.sales_order_list if not row.is_synced]

        if not so_names:
            return {"message": "No sales orders found for sync."}

        result = sync_selected_sales_orders(docname, so_names)

        return {
            "message": (
                f"📋 QuickBooks Sales Order Sync Summary:\n"
                f"✅ Synced: {len(result.get('synced', []))}\n"
                f"❌ Failed: {len(result.get('failed', []))}\n"
                f"⏭ Skipped: {len(result.get('skipped', []))}\n"
                f"📦 Total Attempted: {len(so_names)}"
            )
        }

    except Exception:
        frappe.log_error("Bulk Sync Sales Orders Error", frappe.get_traceback())
        return {"message": "An error occurred while syncing sales orders. Please check error logs."}

@frappe.whitelist()
def sync_selected_sales_orders(docname: str, selected_sales_orders: list):
    """
    Sync selected sales orders to QuickBooks.
    """
    if not selected_sales_orders:
        return {"synced": [], "failed": [], "skipped": []}

    doc = frappe.get_doc("QuickBooks Sync", docname)
    synced, failed, skipped = [], [], []

    for so_name in selected_sales_orders:
        try:
            row = next((r for r in doc.sales_order_list if r.sales_order_name == so_name), None)
            if not row:
                skipped.append(so_name)
                continue

            if row.is_synced:
                skipped.append(so_name)
                continue

            # Check if already synced
            if frappe.db.get_value("Sales Order", so_name, "quickbooks_sales_order_id"):
                row.is_synced = 1
                row.status = "Success"
                skipped.append(so_name)
                continue

            # Sync sales order to QuickBooks
            result = sync_sales_order_to_quickbooks(so_name)

            if result and result.get("id"):
                row.status = "Success"
                row.is_synced = 1
                synced.append(so_name)
            else:
                row.status = "Failed"
                failed.append(so_name)

        except Exception as e:
            row.status = "Failed"
            failed.append(so_name)
            frappe.log_error(f"Error syncing sales order {so_name}: {str(e)}", "QuickBooks Sales Order Sync Error")

    doc.save(ignore_permissions=True)
    frappe.db.commit()

    return {"synced": synced, "failed": failed, "skipped": skipped}

@frappe.whitelist()
def update_stock_sync_cron_job(docname: str, interval: str | None = None):
    """Delegate to doc method (same pattern as Xero)."""
    doc = frappe.get_doc("QuickBooks Sync", docname)
    return doc.update_stock_sync_cron_job(interval=interval)


@frappe.whitelist()
def update_item_sync_cron_job(docname: str, interval: str | None = None):
    """Delegate to doc method for item sync schedule."""
    doc = frappe.get_doc("QuickBooks Sync", docname)
    return doc.update_item_sync_cron_job(interval=interval)
