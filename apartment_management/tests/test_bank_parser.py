from odoo.tests import BaseCase, tagged

from ..models import bank_parser as bp

APTS = [
    {"id": 1, "name": "Hasenwinkel", "unit_number": "", "city": "Halle"},
    {"id": 2, "name": "Brunnenstraße", "unit_number": "", "city": "Leipzig"},
    {"id": 3, "name": "Herbartstraße", "unit_number": "", "city": "Leipzig"},
    {"id": 4, "name": "Zur Mühle I", "unit_number": "WHG 4", "city": "Wiedemar"},
    {"id": 5, "name": "Zur Mühle II", "unit_number": "WHG 6", "city": "Wiedemar"},
]


@tagged("post_install", "-at_install")
class TestBankParser(BaseCase):
    def test_resolve_by_name_unit_and_typo(self):
        self.assertEqual(bp.resolve_apartments("Hausgeld Zur Mühle", "Hausgeld WE 4", APTS)[0], [4])
        self.assertEqual(bp.resolve_apartments("Miete W Zur Mühle", "WHG 6, Miete 05.2025", APTS)[0], [5])
        self.assertEqual(bp.resolve_apartments("Abrechnung L Brunnenstraßw", "", APTS)[0], [2])
        self.assertEqual(bp.resolve_apartments("Steuer H", "5.0100", APTS)[0], [1])

    def test_split_only_within_one_building(self):
        ids, why = bp.resolve_apartments("Darlehen W Zur Mühle", "Teilzahlung", APTS, allow_split=True)
        self.assertEqual(ids, [4, 5])
        self.assertIn("aufgeteilt", why)
        self.assertEqual(bp.resolve_apartments("Darlehen W Zur Mühle", "Teilzahlung", APTS)[0], [])
        # Leipzig: two different houses -> never split
        self.assertEqual(bp.resolve_apartments("Steuer L", "5.0100.657366.4", APTS, allow_split=True)[0], [])
        self.assertEqual(bp.resolve_apartments("Steuer W", "Grundsteuer", APTS, allow_split=True)[0], [4, 5])

    def test_loan_parts_and_splits(self):
        text = ("Rechnung   Darl-Leist. 6003966352 01.12.2024-  30.12.20 Saldo: 175.384,74 "
                "Tilg.: 606,22 Zins/Entg: 350,85")
        self.assertEqual(bp.loan_parts(text, -957.07), (606.22, 350.85))
        self.assertIsNone(bp.loan_parts(text, -942.07), "inconsistent split is ignored")
        self.assertIsNone(bp.loan_parts("Darlehensrate", -438.4))
        self.assertEqual(bp.split_amounts(-741.65, 2), [-370.83, -370.82])
        self.assertEqual(sum(bp.split_percent(3)), 100.0)
