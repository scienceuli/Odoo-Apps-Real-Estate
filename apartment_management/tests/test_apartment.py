import base64

from odoo.exceptions import ValidationError

try:  # Odoo 20+: binary fields hold raw content
    from odoo.tools import BinaryBytes

    def make_pdf(data):
        return BinaryBytes(data)
except ImportError:  # Odoo <= 19: base64-encoded

    def make_pdf(data):
        return base64.b64encode(data)
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestApartment(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tenant = cls.env["res.partner"].create({"name": "Max Mieter"})
        cls.manager = cls.env["res.partner"].create({"name": "HV GmbH", "is_company": True})
        cls.apartment = cls.env["apartment.apartment"].create({
            "name": "Musterstrasse 5, EG",
            "tenant_id": cls.tenant.id,
            "manager_ids": [(0, 0, {"partner_id": cls.manager.id, "role": "property"})],
            "state": "rented",
            "rent_cold": 800.0,
            "rent_utilities": 200.0,
            "cost_ids": [
                (0, 0, {"cost_type": "house_fee", "amount": 300.0, "interval": "monthly"}),
                (0, 0, {"cost_type": "property_tax", "amount": 120.0, "interval": "quarterly"}),
                (0, 0, {"cost_type": "insurance", "amount": 240.0, "interval": "yearly"}),
            ],
        })

    def test_amounts(self):
        a = self.apartment
        self.assertAlmostEqual(a.rent_total, 1000.0)
        self.assertAlmostEqual(a.cost_monthly, 300 + 40 + 20)
        self.assertAlmostEqual(a.net_monthly, 640.0)
        self.assertAlmostEqual(a.net_yearly, 7680.0)

    def test_documents_and_partner_link(self):
        pdf = make_pdf(b"%PDF-1.4\n%%EOF")
        self.env["apartment.document"].create({
            "apartment_id": self.apartment.id, "name": "Mietvertrag",
            "document_type": "lease", "file": pdf, "file_name": "vertrag.pdf",
        })
        self.assertEqual(self.apartment.document_count, 1)
        self.assertEqual(self.tenant.apartment_count, 1)
        self.assertEqual(self.manager.apartment_count, 1)
        with self.assertRaises(ValidationError):
            self.env["apartment.document"].create({
                "apartment_id": self.apartment.id, "name": "Bild",
                "file": pdf, "file_name": "foto.jpg",
            })

    def test_manager_roles_and_history(self):
        a = self.apartment
        Manager = self.env["apartment.manager"]
        rental = self.env["res.partner"].create({"name": "MV AG", "is_company": True})
        self.assertEqual(a.property_manager_id, self.manager)
        self.assertFalse(a.rental_manager_id)

        # different companies: two active lines are fine
        Manager.create({"apartment_id": a.id, "partner_id": rental.id, "role": "rental"})
        self.assertEqual(a.rental_manager_id, rental)

        # a second active Hausverwaltung is refused (also via the combined role)
        other = self.env["res.partner"].create({"name": "Neue HV", "is_company": True})
        with self.assertRaises(ValidationError):
            Manager.create({"apartment_id": a.id, "partner_id": other.id, "role": "property"})
        with self.assertRaises(ValidationError):
            Manager.create({"apartment_id": a.id, "partner_id": other.id, "role": "both"})

        # change of Hausverwaltung: end the old one, add the new one
        old_line = a.manager_ids.filtered(lambda m: m.partner_id == self.manager)
        old_line.action_end()
        self.assertFalse(old_line.active)
        self.assertTrue(old_line.date_to)
        self.assertFalse(a.property_manager_id)
        Manager.create({"apartment_id": a.id, "partner_id": other.id, "role": "property"})
        self.assertEqual(a.property_manager_id, other)
        self.assertEqual(len(a.manager_ids), 3, "archived line stays in the history")
        # former manager still finds the apartment
        self.assertEqual(self.manager.apartment_count, 1)

    def test_manager_both_role(self):
        both = self.env["res.partner"].create({"name": "Alles AG", "is_company": True})
        a = self.env["apartment.apartment"].create({
            "name": "Combined",
            "manager_ids": [(0, 0, {"partner_id": both.id, "role": "both"})],
        })
        self.assertEqual(a.property_manager_id, both)
        self.assertEqual(a.rental_manager_id, both)
        with self.assertRaises(ValidationError):
            self.env["apartment.manager"].create(
                {"apartment_id": a.id, "partner_id": both.id, "role": "rental"})
