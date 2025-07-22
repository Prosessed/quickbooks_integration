import frappe
import json
import hmac
import hashlib
import base64
import requests
from frappe import _
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
from quickbooks_integration.api import create_payment_entry, fetch_quickbooks_payment

def verify_qbo_signature(payload, signature):
    qb_settings = frappe.get_single('QuickBooks Settings')

    if not qb_settings.webhook_secret:
        frappe.throw("QuickBooks Webhook Secret Key not configured")

    computed_hash = hmac.new(
        key=qb_settings.get_password('webhook_secret').encode('utf-8'),
        msg=payload,
        digestmod=hashlib.sha256
    ).digest()

    computed_signature = base64.b64encode(computed_hash).decode()

    return hmac.compare_digest(computed_signature, signature)

@frappe.whitelist(allow_guest=True)
def quickbooks_webhook():
    frappe.set_user('Administrator')  # or dedicated webhook user

    payload = frappe.request.get_data()

    if not payload:
        frappe.throw(_("Invalid Payload"))

    signature = frappe.request.headers.get('intuit-signature')

    if not signature:
        frappe.throw(_("Signature Missing"))

    if verify_qbo_signature(payload, signature):
        frappe.response.status_code = 200
        payload_json = json.loads(payload)

        frappe.log_error(
            title="QuickBooks Webhook Triggered",
            message=json.dumps(payload_json, indent=2)
        )

        frappe.enqueue(
            method=process_quickbooks_webhook,
            queue="long",
            payload=payload_json
        )

        return frappe.response

    else:
        frappe.log_error(
            title="QuickBooks Webhook Failed Signature",
            message=json.dumps(json.loads(payload), indent=2)
        )
        frappe.throw(_("Authentication failed: invalid signature."))

def process_quickbooks_webhook(payload):
    notifications = payload.get('eventNotifications', [])
    for notification in notifications:
        entities = notification.get('dataChangeEvent', {}).get('entities', [])
        for entity in entities:
            if entity.get('name') == 'Payment' and entity.get('operation') in ('Create', 'Update'):
                payment_id = entity.get('id')
                qb_payment = fetch_quickbooks_payment(payment_id)
                if qb_payment:
                    create_payment_entry(qb_payment)


