# Copyright (c) 2025, Jaspreet Singh Sodhi and contributors
# For license information, please see license.txt

import frappe
from frappe import _

# QuickBooks sync IDs that must not carry over when amending a cancelled doc.
# Amend ignores no_copy in Frappe, so clear these explicitly on validate of new amended docs.
QUICKBOOKS_AMEND_CLEAR_FIELDS = {
	"Sales Order": [
		"quickbooks_sales_order_id",
		"custom_quickbooks_sync_status",
	],
	"Sales Invoice": [
		"custom_quickbooks_invoice_id",
		"custom_quickbooks_credit_memo_id",
		"custom_quickbooks_sync_status",
	],
	"Purchase Order": [
		# Add Purchase Order QuickBooks custom fields here when introduced
	],
	"Purchase Invoice": [
		"custom_quickbooks_bill_id",
		"custom_quickbooks_debitnote_id",
		"custom_quickbooks_sync_status",
	],
}

QUICKBOOKS_SYNC_STATUS_FIELD = "custom_quickbooks_sync_status"
QUICKBOOKS_SYNC_STATUSES = ("Pending", "Success", "Failed")


def clear_quickbooks_integration_fields_on_amend(doc, event_name=None):
	"""Clear QuickBooks integration custom fields on amended documents so they can re-sync.

	Frappe Amend copies fields even when no_copy=1. Call this from validate on
	Sales Order / Sales Invoice / Purchase Order / Purchase Invoice.
	"""
	if event_name and event_name != "validate":
		return

	if not doc.is_new() or not doc.get("amended_from"):
		return

	for fieldname in QUICKBOOKS_AMEND_CLEAR_FIELDS.get(doc.doctype, []):
		if not doc.meta.has_field(fieldname):
			continue
		df = doc.meta.get_field(fieldname)
		doc.set(fieldname, 0 if df and df.fieldtype == "Check" else "")


def set_quickbooks_sync_status(doctype, name, status):
	"""Set custom_quickbooks_sync_status from QuickBooks sync response.

	Always uses db.set_value so submitted docs (and docs with non-standard
	ERPNext status values like "Invoiced") are not revalidated via doc.save().

	On Failed: force Pending -> Failed when already Failed, then fire Desk
	Notification Value Change rules (Slack) without blocking the sync.
	"""
	if status not in QUICKBOOKS_SYNC_STATUSES:
		frappe.throw(_("Invalid QuickBooks sync status: {0}").format(status))

	if not frappe.db.has_column(doctype, QUICKBOOKS_SYNC_STATUS_FIELD):
		return

	old_status = frappe.db.get_value(doctype, name, QUICKBOOKS_SYNC_STATUS_FIELD) or ""

	if status == "Failed":
		# Clear any failed/partial DB transaction before status update
		frappe.db.commit()
		old_status = frappe.db.get_value(doctype, name, QUICKBOOKS_SYNC_STATUS_FIELD) or ""
		# Already Failed: reset first so Value Change fires again on retry
		if old_status == "Failed":
			frappe.db.set_value(
				doctype,
				name,
				QUICKBOOKS_SYNC_STATUS_FIELD,
				"Pending",
				update_modified=False,
			)
			old_status = "Pending"

		frappe.db.set_value(
			doctype,
			name,
			QUICKBOOKS_SYNC_STATUS_FIELD,
			"Failed",
			update_modified=False,
		)
		_trigger_quickbooks_sync_failed_notifications(doctype, name, old_status)
		return

	if old_status == status:
		return

	frappe.db.set_value(
		doctype,
		name,
		QUICKBOOKS_SYNC_STATUS_FIELD,
		status,
		update_modified=False,
	)


def _trigger_quickbooks_sync_failed_notifications(doctype, name, previous_status):
	"""Fire Desk Value Change notifications after db.set_value (non-blocking)."""
	from frappe.email.doctype.notification.notification import evaluate_alert

	try:
		doc = frappe.get_doc(doctype, name)
		doc_before_save = frappe.get_doc(doctype, name)
		doc_before_save.set(QUICKBOOKS_SYNC_STATUS_FIELD, previous_status)
		doc._doc_before_save = doc_before_save

		for alert_name in frappe.get_all(
			"Notification",
			filters={
				"enabled": 1,
				"document_type": doctype,
				"event": "Value Change",
				"value_changed": QUICKBOOKS_SYNC_STATUS_FIELD,
			},
			pluck="name",
		):
			evaluate_alert(doc, alert_name, "Value Change")
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			"QuickBooks Sync Failed Notification",
		)
