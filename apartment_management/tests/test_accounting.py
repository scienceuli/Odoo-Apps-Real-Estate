import io
from datetime import date, datetime as dt

from odoo import Command
from odoo.tests import TransactionCase, tagged

CSV_HEADER = (
    '﻿"Girokonto";"DE08120300001060502638"\n'
    '"Zeitraum:";"01.01.2026 - 22.11.2026"\n'
    '"Kontostand vom 22.11.2026:";"5.630,75 €"\n'
    '""\n'
    '"Buchungsdatum";"Wertstellung";"Status";"Zahlungspflichtige*r";"Zahlungsempfänger*in";'
    '"Verwendungszweck";"Umsatztyp";"IBAN";"Betrag (€)";"Gläubiger-ID";"Mandatsreferenz";"Kundenreferenz"\n'
)
ME = "Ulrich Kilian und Christine Weber"
HASENWINKEL_LOAN = ("Rechnung   Darl-Leist. 6003966352 01.12.2025-  30.12.20 Saldo: 175.384,74 "
                    "Tilg.: 606,22 Zins/Entg: 350,85")


def csv_line(day, payer, payee, purpose, iban, amount):
    typ = "Ausgang" if amount.startswith("-") else "Eingang"
    return f'"{day}";"{day}";"Gebucht";"{payer}";"{payee}";"{purpose}";"{typ}";"{iban}";"{amount}";"";"";""\n'


def xlsx(rows):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Girokonto", "DE08120300001060502638"])
    ws.append(["Zeitraum:", "01.01.2026 - 31.12.2026"])
    ws.append([])
    ws.append(["Buchungsdatum", "Wertstellung", "Zahlungspflichtige*r", "Zahlungsempfänger*in",
               "Verwendungszweck", "Umsatztyp", "IBAN", "Betrag (€)", "Gläubiger-ID",
               "Mandatsreferenz", "Kundenreferenz", "Zuordnung", "Kommentar"])
    for day, payer, payee, purpose, iban, amount, assignment in rows:
        ws.append([day, day, payer, payee, purpose, "Ausgang" if amount < 0 else "Eingang", iban, amount,
                   None, None, None, assignment, None])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@tagged("post_install", "-at_install")
class TestAccounting(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Vermietung Test", "country_id": cls.env.ref("base.de").id})
        cls.env.user.company_ids |= cls.company
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=[cls.company.id]))
        Apt = cls.env["apartment.apartment"]
        vals = {"company_id": cls.company.id}
        cls.hasenwinkel = Apt.create({**vals, "name": "Hasenwinkel", "city": "Halle"})
        cls.brunnen = Apt.create({**vals, "name": "Brunnenstraße", "city": "Leipzig"})
        cls.herbart = Apt.create({**vals, "name": "Herbartstraße", "city": "Leipzig"})
        cls.muehle1 = Apt.create({**vals, "name": "Zur Mühle I", "unit_number": "WHG 4", "city": "Wiedemar"})
        cls.muehle2 = Apt.create({**vals, "name": "Zur Mühle II", "unit_number": "WHG 6", "city": "Wiedemar"})
        cls.setup = cls.env["apartment.accounting.setup"].create({
            "company_id": cls.company.id,
            "iban": "DE08 1203 0000 1060 5026 38",
            "opening_date": date(2025, 12, 31),
            "opening_balance": 5000.0,
        })
        cls.setup.action_setup()
        cls.journal = cls.env["account.journal"].search(
            [("company_id", "=", cls.company.id), ("type", "=", "bank"), ("name", "=", "DKB Girokonto")])
        cls.Line = cls.env["account.bank.statement.line"]
        cls.cat = cls.env["apartment.booking.category"]._by_code(cls.company)

    def _counterpart(self, line):
        _liq, suspense, other = line._seek_for_lines()
        return suspense | other

    def _dist(self, *apartments):
        return {str(a.analytic_account_id.id) for a in apartments}

    # ---------------------------------------------------------------- setup
    def test_setup(self):
        self.assertEqual(len(self.journal), 1)
        self.assertEqual(self.journal.bank_account_id.sanitized_account_number, "DE08120300001060502638")
        self.assertEqual(len(self.cat), 12)
        self.assertEqual(self.cat["rent"].account_id.code, "4000")
        self.assertTrue(self.cat["bank_fee"].is_general)
        self.assertTrue(self.cat["loan"].allow_split)
        for apt in self.hasenwinkel | self.brunnen | self.herbart | self.muehle1 | self.muehle2:
            self.assertTrue(apt.analytic_account_id, apt.name)
            self.assertTrue(apt.loan_account_id, apt.name)
        self.assertEqual(self.muehle1.loan_account_id, self.muehle2.loan_account_id)
        self.assertEqual(self.muehle1.loan_account_id.name, "Darlehen Zur Mühle")
        self.assertNotEqual(self.brunnen.loan_account_id, self.herbart.loan_account_id)
        opening = self.Line.search([("journal_id", "=", self.journal.id)])
        self.assertEqual(len(opening), 1)
        self.assertTrue(opening.is_reconciled)
        self.assertEqual(self._counterpart(opening).account_id.code, "2990")
        # idempotent; a new apartment gets its cost centre automatically
        self.env["apartment.accounting.setup"].create({"company_id": self.company.id}).action_setup()
        self.assertEqual(len(self.env["apartment.booking.category"]._by_code(self.company)), 12)
        new = self.env["apartment.apartment"].create({"name": "Neu", "company_id": self.company.id})
        self.assertTrue(new.analytic_account_id)

    # ---------------------------------------------------------------- import xlsx
    def test_xlsx_import_books_with_cost_centres(self):
        content = xlsx([
            (dt(2026, 5, 2), ME, "WEG Zur Mühle 11", "Hausgeld WE 4", "DE37120300001070005788", -339.47,
             "Hausgeld Zur Mühle"),
            (dt(2026, 5, 9), "WEG Verwaltung Schwerdtner-DKB", "Kilian", "WHG 6, MB Bock Miete 05.2026",
             "DE37120300001070005788", 438.64, "Miete W Zur Mühle"),
            (dt(2026, 1, 2), "KILIAN UND WEBER", "Kilian und Weber", "Teilzahlung Darlehen",
             "DE17651915000032917228", -741.65, "Darlehen W Zur Mühle"),
            (dt(2026, 2, 16), ME, "GEMEINDE WIEDEMAR", "Grundsteuer", "DE11860555921100000000", -40.0,
             "Steuer W"),
            (dt(2026, 1, 2), ME, "Sparkasse", HASENWINKEL_LOAN, "DE22800537620000000000", -957.07,
             "Darlehen H Hasenwinkel"),
            (dt(2026, 5, 6), "Kilian Ulrich", "ULRICH KILIAN", "bekannt", "DE09120300001074407279", 1000.0,
             "Investment"),
            (dt(2026, 1, 13), "DKB AG", "DKB AG", "Entgelt Girokarte", 0, -11.88, "Bankkosten"),
            (dt(2026, 8, 12), ME, "Tischlerei", "RE25/194", "DE11111111111111111111", -424.84, "noch zu klaeren"),
        ])
        res = self.Line.apartment_import_bank_file(self.journal, content, "umsaetze_2026.xlsx")
        self.assertEqual(len(res["created"]), 8)
        by_ref = {line.payment_ref: line for line in res["created"]}

        hausgeld = by_ref["Hausgeld WE 4"]
        self.assertTrue(hausgeld.is_reconciled)
        self.assertEqual(hausgeld.apartment_ids, self.muehle1)
        cp = self._counterpart(hausgeld)
        self.assertEqual(cp.account_id.code, "6000")
        self.assertEqual(cp.analytic_distribution, {str(self.muehle1.analytic_account_id.id): 100.0})
        self.assertEqual(by_ref["WHG 6, MB Bock Miete 05.2026"].apartment_ids, self.muehle2)

        loan = by_ref["Teilzahlung Darlehen"]
        self.assertEqual(loan.apartment_ids, self.muehle1 | self.muehle2)
        cp = self._counterpart(loan)
        self.assertEqual(cp.account_id, self.muehle1.loan_account_id)
        self.assertEqual(set(cp.analytic_distribution), self._dist(self.muehle1, self.muehle2))
        self.assertEqual(sorted(cp.analytic_distribution.values()), [50.0, 50.0])
        self.assertEqual(by_ref["Grundsteuer"].apartment_ids, self.muehle1 | self.muehle2)

        hw = self._counterpart(by_ref[HASENWINKEL_LOAN])
        self.assertEqual(len(hw), 2, "Hasenwinkel instalment split into principal and interest")
        self.assertEqual(hw.filtered(lambda l: l.account_id == self.hasenwinkel.loan_account_id).balance, 606.22)
        self.assertEqual(hw.filtered(lambda l: l.account_id.code == "6400").balance, 350.85)

        for ref, code in (("bekannt", "2900"), ("Entgelt Girokarte", "6900")):
            line = by_ref[ref]
            self.assertTrue(line.is_reconciled)
            self.assertFalse(line.apartment_ids)
            self.assertEqual(self._counterpart(line).account_id.code, code)
            self.assertFalse(self._counterpart(line).analytic_distribution)

        open_line = by_ref["RE25/194"]
        self.assertFalse(open_line.is_reconciled)
        self.assertEqual(self._counterpart(open_line).account_id, self.journal.suspense_account_id)
        self.assertEqual(res["to_check"], open_line)

        # same file again: nothing new
        res2 = self.Line.apartment_import_bank_file(self.journal, content, "umsaetze_2026.xlsx")
        self.assertFalse(res2["created"])
        self.assertEqual(res2["skipped"], 8)

        # clarify the open booking by hand -> booked immediately
        open_line.write({"apartment_category_id": self.cat["other_cost"].id,
                         "apartment_ids": [Command.set(self.hasenwinkel.ids)]})
        self.assertTrue(open_line.is_reconciled)
        self.assertEqual(self._counterpart(open_line).account_id.code, "6300")
        # and back: no category -> suspense account again
        open_line.apartment_category_id = False
        self.assertFalse(open_line.is_reconciled)
        self.assertEqual(self._counterpart(open_line).account_id, self.journal.suspense_account_id)

        # apartment figures come from the analytic items
        self.muehle1.invalidate_recordset()
        self.assertAlmostEqual(self.muehle1.booked_expense_year, -339.47 - 20.0)
        self.assertAlmostEqual(self.muehle1.booked_principal_year, -370.83, delta=0.011)
        self.assertAlmostEqual(self.muehle2.booked_income_year, 438.64)
        self.assertAlmostEqual(self.hasenwinkel.booked_expense_year, -350.85)
        self.assertAlmostEqual(self.hasenwinkel.booked_principal_year, -606.22)
        self.assertEqual(self.muehle1.bank_line_count, 3)
        action = self.env["apartment.apartment"].action_cost_overview()
        self.assertEqual(action["res_model"], "account.analytic.line")
        self.assertTrue(self.env["account.analytic.line"].search(action["domain"]))

    # ---------------------------------------------------------------- csv, history, rules
    def test_csv_learns_from_history_and_rules(self):
        first = CSV_HEADER + csv_line("02.01.26", ME, "WEG Zur Mühle 11", "Hausgeld WE 6",
                                      "DE37120300001070005788", "-360,17")
        line = self.Line.apartment_import_bank_file(self.journal, first.encode(), "jan.csv")["created"]
        self.assertFalse(line.is_reconciled, "raw CSV without history and rules cannot be classified")
        line.write({"apartment_category_id": self.cat["house_fee"].id,
                    "apartment_ids": [Command.set(self.muehle2.ids)]})
        self.assertTrue(line.is_reconciled)

        self.env["apartment.bank.rule"].create({
            "name": "Grundsteuer Leipzig 657366",
            "company_id": self.company.id,
            "purpose_contains": "5.0100.657366.4",
            "category_id": self.cat["tax"].id,
            "apartment_ids": [Command.set(self.brunnen.ids)],
        })
        second = CSV_HEADER + "".join([
            csv_line("02.02.26", ME, "WEG Zur Mühle 11", "Hausgeld WE 6", "DE37120300001070005788", "-360,17"),
            csv_line("02.02.26", ME, "WEG Zur Mühle 11", "Hausgeld WE 4", "DE37120300001070005788", "-339,47"),
            csv_line("16.02.26", ME, "STADTKASSE LEIPZIG", "5.0100.657366.4", "DE60860700000170011100", "-68,48"),
            csv_line("06.02.26", "Kilian Ulrich", "ULRICH KILIAN", "Investition", "DE09120300001074407279", "1.000"),
            csv_line("01.01.26", "DKB AG", "DKB AG", "Abrechnung 31.12.2025", "1060502638", "0"),
        ])
        res = self.Line.apartment_import_bank_file(self.journal, second.encode(), "feb.csv")
        self.assertEqual(res["rows"], 4, "0,00 € DKB statements are ignored")
        by_ref = {line.payment_ref: line for line in res["created"]}
        self.assertEqual(by_ref["Hausgeld WE 6"].apartment_ids, self.muehle2)
        self.assertTrue(by_ref["Hausgeld WE 6"].is_reconciled)
        self.assertEqual(by_ref["Hausgeld WE 4"].apartment_ids, self.muehle1, "unit number beats history")
        self.assertEqual(by_ref["5.0100.657366.4"].apartment_ids, self.brunnen)
        self.assertEqual(self._counterpart(by_ref["5.0100.657366.4"]).account_id.code, "6100")
        self.assertFalse(by_ref["Investition"].is_reconciled)

    def test_apply_rules_later(self):
        content = CSV_HEADER + csv_line("01.04.26", ME, "RHION VERSICHERUNGS AG", "67-02-06025645 Gebaeude",
                                        "DE95120300001084826344", "-141,08")
        line = self.Line.apartment_import_bank_file(self.journal, content.encode(), "apr.csv")["created"]
        self.assertFalse(line.is_reconciled)
        self.env["apartment.bank.rule"].create({
            "name": "RHION 06025645", "company_id": self.company.id, "purpose_contains": "67-02-06025645",
            "category_id": self.cat["insurance"].id, "apartment_ids": [Command.set(self.herbart.ids)],
        })
        line.action_apartment_apply_rules()
        self.assertTrue(line.is_reconciled)
        self.assertEqual(line.apartment_ids, self.herbart)
        self.assertEqual(self._counterpart(line).account_id.code, "6200")

    # ---------------------------------------------------------------- interest
    def test_interest_entry(self):
        wizard = self.env["apartment.interest.entry"].create({
            "company_id": self.company.id,
            "date": date(2026, 12, 31),
            "apartment_ids": [Command.set((self.muehle1 | self.muehle2).ids)],
            "amount": 1200.0,
        })
        self.assertEqual(wizard.loan_account_id, self.muehle1.loan_account_id)
        move = self.env["account.move"].browse(wizard.action_post()["res_id"])
        self.assertEqual(move.state, "posted")
        interest = move.line_ids.filtered(lambda l: l.account_id.code == "6400")
        self.assertEqual(interest.balance, 1200.0)
        self.assertEqual(len(interest.analytic_line_ids), 2)
        self.assertEqual(sorted(interest.analytic_line_ids.mapped("amount")), [-600.0, -600.0])
