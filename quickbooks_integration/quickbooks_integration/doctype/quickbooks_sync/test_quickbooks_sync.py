# Copyright (c) 2025, Jaspreet Singh Sodhi and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from quickbooks_integration.quickbooks_integration.doctype.quickbooks_sync.quickbooks_sync import (
	_resolve_qb_country,
	create_or_update_supplier,
	sanitize_phone_number,
)


def _count_linked(doctype, supplier_name):
	return frappe.db.count(
		"Dynamic Link",
		{
			"link_doctype": "Supplier",
			"link_name": supplier_name,
			"parenttype": doctype,
		},
	)


class TestQuickBooksSync(FrappeTestCase):
	def setUp(self):
		self.qb_ids = ["56", "32", "40", "46", "51"]
		self._cleanup()

	def tearDown(self):
		self._cleanup()

	def _cleanup(self):
		for qb_id in self.qb_ids:
			supplier_name = frappe.db.get_value(
				"Supplier",
				{"custom_quickbooks_supplier_id": qb_id},
				"name",
			)
			if not supplier_name:
				continue

			for parenttype in ("Address", "Contact"):
				parents = frappe.get_all(
					"Dynamic Link",
					filters={
						"link_doctype": "Supplier",
						"link_name": supplier_name,
						"parenttype": parenttype,
					},
					pluck="parent",
				)
				for parent in parents:
					if frappe.db.exists(parenttype, parent):
						frappe.delete_doc(parenttype, parent, force=1, ignore_permissions=True)

			frappe.delete_doc("Supplier", supplier_name, force=1, ignore_permissions=True)

	def test_sanitize_phone_number(self):
		self.assertEqual(sanitize_phone_number("(650) 555-1616"), "6505551616")
		self.assertEqual(sanitize_phone_number("+1 (650) 555-1616"), "+16505551616")
		self.assertEqual(sanitize_phone_number("123"), "")
		self.assertEqual(sanitize_phone_number(""), "")

	def test_country_codes_resolve_to_erpnext_country_names(self):
		united_states = frappe.db.get_value("Country", {"code": "US"}, "name")
		australia = frappe.db.get_value("Country", {"code": "AU"}, "name")
		if not united_states or not australia:
			self.skipTest("Standard US and AU Country records are required")

		self.assertEqual(_resolve_qb_country("US"), united_states)
		self.assertEqual(_resolve_qb_country("usa"), united_states)
		self.assertEqual(_resolve_qb_country("AU"), australia)
		self.assertEqual(_resolve_qb_country("AUS"), australia)
		self.assertEqual(_resolve_qb_country(australia), australia)

	def test_missing_country_resolves_to_valid_configured_country(self):
		resolved_country = _resolve_qb_country(None)

		self.assertTrue(resolved_country)
		self.assertTrue(frappe.db.exists("Country", resolved_country))

	def test_no_detail_vendor_creates_supplier_only(self):
		"""Bob's Burger Joint has no address/contact fields."""
		qb_supplier = {
			"Id": "56",
			"DisplayName": "Bob's Burger Joint",
			"PrintOnCheckName": "Bob's Burger Joint",
			"Active": True,
		}

		supplier = create_or_update_supplier(qb_supplier)

		self.assertTrue(supplier)
		self.assertEqual(supplier.supplier_name, "Bob's Burger Joint")
		self.assertEqual(_count_linked("Address", supplier.name), 0)
		self.assertEqual(_count_linked("Contact", supplier.name), 0)
		self.assertFalse(supplier.supplier_primary_address)
		self.assertFalse(supplier.supplier_primary_contact)

	def test_address_and_phone_vendor(self):
		"""Cal Telephone has BillAddr + PrimaryPhone, no email/name parts."""
		qb_supplier = {
			"Id": "32",
			"DisplayName": "Cal Telephone",
			"CompanyName": "Cal Telephone",
			"BillAddr": {
				"City": "Palo Alto",
				"Line1": "10 Main St.",
				"PostalCode": "94303",
				"CountrySubDivisionCode": "CA",
				"Id": "33",
			},
			"PrimaryPhone": {"FreeFormNumber": "(650) 555-1616"},
		}

		supplier = create_or_update_supplier(qb_supplier)
		supplier.reload()

		self.assertEqual(supplier.supplier_type, "Company")
		self.assertEqual(_count_linked("Address", supplier.name), 1)
		self.assertEqual(_count_linked("Contact", supplier.name), 1)
		self.assertTrue(supplier.supplier_primary_address)
		self.assertTrue(supplier.supplier_primary_contact)

		address = frappe.get_doc("Address", supplier.supplier_primary_address)
		self.assertEqual(address.address_type, "Billing")
		self.assertEqual(address.address_line1, "10 Main St.")
		self.assertEqual(address.city, "Palo Alto")
		self.assertEqual(address.state, "CA")
		self.assertEqual(address.pincode, "94303")
		self.assertTrue(frappe.db.exists("Country", address.country))

		contact = frappe.get_doc("Contact", supplier.supplier_primary_contact)
		self.assertEqual(contact.first_name, "Cal Telephone")
		phones = [row.phone for row in contact.phone_nos]
		self.assertIn("6505551616", phones)

	def test_full_contact_and_address_vendor(self):
		"""Norton Lumber has GivenName/FamilyName, email, phone, and BillAddr."""
		qb_supplier = {
			"Id": "46",
			"GivenName": "Julie",
			"DisplayName": "Norton Lumber and Building Materials",
			"FamilyName": "Norton",
			"CompanyName": "Norton Lumber and Building Materials",
			"PrimaryEmailAddr": {"Address": "Materials@intuit.com"},
			"PrimaryPhone": {"FreeFormNumber": "(650) 363-6578"},
			"BillAddr": {
				"City": "Middlefield",
				"Line1": "4528 Country Road",
				"PostalCode": "94303",
				"CountrySubDivisionCode": "CA",
				"Id": "40",
			},
		}

		supplier = create_or_update_supplier(qb_supplier)
		supplier.reload()

		contact = frappe.get_doc("Contact", supplier.supplier_primary_contact)
		self.assertEqual(contact.first_name, "Julie")
		self.assertEqual(contact.last_name, "Norton")
		emails = [row.email_id for row in contact.email_ids]
		self.assertIn("Materials@intuit.com", emails)
		self.assertEqual(contact.email_id, "Materials@intuit.com")

		address = frappe.get_doc("Address", supplier.supplier_primary_address)
		self.assertEqual(address.address_line1, "4528 Country Road")
		self.assertEqual(address.city, "Middlefield")

	def test_resync_updates_without_duplicates(self):
		"""Re-syncing the same vendor must update existing Address/Contact, not create new ones."""
		qb_supplier = {
			"Id": "51",
			"GivenName": "Tim",
			"DisplayName": "Tim Philip Masonry",
			"FamilyName": "Philip",
			"CompanyName": "Tim Philip Masonry",
			"PrimaryEmailAddr": {"Address": "tim.philip@timphilipmasonry.com"},
			"PrimaryPhone": {"FreeFormNumber": "(800) 556-1254"},
			"Mobile": {"FreeFormNumber": "(650) 555-1549"},
			"BillAddr": {
				"City": "Middlefield",
				"Line1": "3948 Elm St.",
				"PostalCode": "94482",
				"CountrySubDivisionCode": "CA",
				"Id": "45",
			},
		}

		supplier = create_or_update_supplier(qb_supplier)
		supplier.reload()
		first_address = supplier.supplier_primary_address
		first_contact = supplier.supplier_primary_contact

		qb_supplier["BillAddr"]["Line1"] = "3950 Elm St."
		qb_supplier["BillAddr"]["PostalCode"] = "94483"
		qb_supplier["PrimaryEmailAddr"]["Address"] = "updated.tim@timphilipmasonry.com"
		qb_supplier["PrimaryPhone"]["FreeFormNumber"] = "(800) 556-9999"

		supplier = create_or_update_supplier(qb_supplier)
		supplier.reload()

		self.assertEqual(supplier.supplier_primary_address, first_address)
		self.assertEqual(supplier.supplier_primary_contact, first_contact)
		self.assertEqual(_count_linked("Address", supplier.name), 1)
		self.assertEqual(_count_linked("Contact", supplier.name), 1)

		address = frappe.get_doc("Address", first_address)
		self.assertEqual(address.address_line1, "3950 Elm St.")
		self.assertEqual(address.pincode, "94483")

		contact = frappe.get_doc("Contact", first_contact)
		emails = [row.email_id for row in contact.email_ids]
		phones = [row.phone for row in contact.phone_nos]
		self.assertEqual(emails, ["updated.tim@timphilipmasonry.com"])
		self.assertEqual(contact.email_id, "updated.tim@timphilipmasonry.com")
		self.assertIn("8005569999", phones)
		self.assertIn("6505551549", phones)
		self.assertNotIn("8005561254", phones)

		# Third sync with same payload must not add more email/phone rows or links.
		create_or_update_supplier(qb_supplier)
		contact.reload()
		self.assertEqual(len(contact.email_ids), 1)
		self.assertEqual(len([p for p in contact.phone_nos if p.phone == "8005569999"]), 1)
		self.assertEqual(_count_linked("Address", supplier.name), 1)
		self.assertEqual(_count_linked("Contact", supplier.name), 1)
		self.assertEqual(
			len([link for link in contact.links if link.link_doctype == "Supplier"]),
			1,
		)

	def test_named_contact_with_mobile_and_address(self):
		"""Hall Properties has GivenName/FamilyName, phones, and BillAddr without email."""
		qb_supplier = {
			"Id": "40",
			"GivenName": "Melanie",
			"DisplayName": "Hall Properties",
			"FamilyName": "Hall",
			"CompanyName": "Hall Properties",
			"Mobile": {"FreeFormNumber": "(973) 888-6222"},
			"PrimaryPhone": {"FreeFormNumber": "(973) 555-3827"},
			"BillAddr": {
				"City": "South Orange",
				"Line1": "P.O.Box 357",
				"PostalCode": "07079",
				"CountrySubDivisionCode": "NJ",
				"Id": "36",
			},
		}

		supplier = create_or_update_supplier(qb_supplier)
		supplier.reload()

		contact = frappe.get_doc("Contact", supplier.supplier_primary_contact)
		self.assertEqual(contact.first_name, "Melanie")
		self.assertEqual(contact.last_name, "Hall")
		phones = {row.phone: row for row in contact.phone_nos}
		self.assertIn("9735553827", phones)
		self.assertIn("9738886222", phones)
		self.assertEqual(phones["9735553827"].is_primary_phone, 1)
		self.assertEqual(phones["9738886222"].is_primary_mobile_no, 1)
