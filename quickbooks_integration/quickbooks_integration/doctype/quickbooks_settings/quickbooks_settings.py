# Copyright (c) 2025, Jaspreet Singh Sodhi and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class QuickBooksSettings(Document):
	 def before_save(self):
        if not self.quickbooks_account_id or not self.quickbooks_api_key:
            frappe.throw("quickbooks Account ID and API Key are required fields.")

    def _get(self, *args, **kwargs):
        kwargs["headers"] = {
            "api-auth-accountid": self.quickbooks_account_id,
            "api-auth-applicationkey": self.quickbooks_api_key,
            "Accept": "application/json"
        }

        try:
            response = requests.get(*args, **kwargs)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "quickbooks API Request Failed")
            frappe.throw("Unable to reach quickbooks. Please check your network or credentials.")

        if response.status_code != 200:
            try:
                error_body = response.json()
            except Exception:
                error_body = response.text
            frappe.log_error(json.dumps(error_body, indent=2), f"quickbooks GET Error {response.status_code}")
            frappe.throw(f"quickbooks API returned {response.status_code}: {error_body}")

        try:
            return response.json()
        except ValueError:
            frappe.log_error(response.text, "Invalid JSON from quickbooks")
            frappe.throw("quickbooks returned an invalid or non-JSON response.")

    def _post(self, *args, **kwargs):
        kwargs["headers"] = {
            "api-auth-accountid": self.quickbooks_account_id,
            "api-auth-applicationkey": self.quickbooks_api_key,
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

        try:
            response = requests.post(*args, **kwargs)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "quickbooks API Request Failed")
            frappe.throw("Unable to reach quickbooks. Please check your network or credentials.")

        if response.status_code != 200:
            try:
                error_body = response.json()
            except Exception:
                error_body = response.text
            frappe.log_error(json.dumps(error_body, indent=2), f"quickbooks POST Error {response.status_code}")
            frappe.throw(f"quickbooks API returned {response.status_code}: {error_body}")

        try:
            return response.json()
        except ValueError:
            frappe.log_error(response.text, "Invalid JSON from quickbooks")
            frappe.throw("quickbooks returned an invalid or non-JSON response.")

