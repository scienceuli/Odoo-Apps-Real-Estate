from odoo import api, fields, models

from . import bank_parser

CATEGORY_CODES = [
    ("rent", "Miete"),
    ("loan", "Darlehen"),
    ("interest", "Schuldzinsen"),
    ("house_fee", "Hausgeld"),
    ("special_assessment", "Sonderumlage"),
    ("settlement", "Abrechnung (Hausgeld / Betriebskosten)"),
    ("other_cost", "Kosten (Handwerker, Sonstiges)"),
    ("tax", "Steuer / Abgaben"),
    ("insurance", "Versicherung"),
    ("investment", "Investment / Privateinlage"),
    ("bank_fee", "Bankkosten"),
    ("opening", "Anfangssaldo"),
]


class ApartmentBookingCategory(models.Model):
    """Category of a bank booking and the account it is booked to.

    The category is what the yearly xlsx files call "Zuordnung" (Miete, Hausgeld, ...).
    Loans are booked to the loan account of the apartment (``apartment.loan_account_id``),
    the account here is only the fallback.
    """

    _name = "apartment.booking.category"
    _description = "Buchungskategorie"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    name = fields.Char("Bezeichnung", required=True, translate=False)
    code = fields.Selection(CATEGORY_CODES, "Typ", required=True)
    company_id = fields.Many2one(
        "res.company", "Unternehmen", required=True, default=lambda self: self.env.company, index=True
    )
    account_id = fields.Many2one(
        "account.account", "Konto", check_company=True,
        help="Gegenkonto der Bankbuchung. Darlehen: Konto der Wohnung hat Vorrang.",
    )
    is_general = fields.Boolean(
        "Allgemein", compute="_compute_flags", store=True,
        help="Ohne Wohnungsbezug (Investment, Bankkosten) – keine Kostenstelle",
    )
    allow_split = fields.Boolean(
        "Aufteilen erlaubt", compute="_compute_flags", store=True, readonly=False,
        help="Nennt der Text keine Wohnungsnummer, wird die Buchung gleichmäßig auf alle "
             "Wohnungen desselben Hauses verteilt (z. B. Darlehen, Grundsteuer Zur Mühle).",
    )

    _code_company_unique = models.Constraint(
        "UNIQUE(company_id, code)", "Jeder Kategorietyp darf je Unternehmen nur einmal vorkommen."
    )

    @api.depends("code")
    def _compute_flags(self):
        for rec in self:
            rec.is_general = rec.code in bank_parser.GENERAL_CATEGORIES
            rec.allow_split = rec.code in bank_parser.SPLIT_CATEGORIES

    @api.model
    def _by_code(self, company=None):
        company = company or self.env.company
        return {c.code: c for c in self.search([("company_id", "=", company.id)])}
