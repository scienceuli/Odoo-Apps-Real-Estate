from odoo import fields, models

from . import bank_parser


class ApartmentBankRule(models.Model):
    """User-defined rule: bank text -> category and apartment(s)."""

    _name = "apartment.bank.rule"
    _description = "Zuordnungsregel für Bankbuchungen"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    name = fields.Char("Bezeichnung", required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", "Unternehmen", required=True, default=lambda self: self.env.company
    )
    counterparty_contains = fields.Char("Gegenpartei enthält", help="Zahlungspflichtige*r oder Empfänger*in")
    purpose_contains = fields.Char("Verwendungszweck enthält")
    assignment_contains = fields.Char("Zuordnung (Datei) enthält")
    iban = fields.Char("IBAN")
    amount = fields.Float("Betrag", help="0 = beliebiger Betrag")
    category_id = fields.Many2one(
        "apartment.booking.category", "Kategorie", check_company=True, ondelete="cascade"
    )
    apartment_ids = fields.Many2many(
        "apartment.apartment", string="Wohnungen",
        help="Mehrere Wohnungen: der Betrag wird gleichmäßig aufgeteilt",
    )

    def _matches(self, row):
        self.ensure_one()

        def contains(needle, *haystacks):
            if not needle:
                return True
            n = bank_parser.normalize(needle)
            return any(n in bank_parser.normalize(h) for h in haystacks)

        if not any([self.counterparty_contains, self.purpose_contains, self.assignment_contains, self.iban]):
            return False  # a rule without any condition would match everything
        if self.iban and self.iban.replace(" ", "") != (row.get("iban") or "").replace(" ", ""):
            return False
        if self.amount and abs(self.amount - row.get("amount", 0.0)) >= 0.005:
            return False
        return (
            contains(self.counterparty_contains, row.get("payer"), row.get("payee"))
            and contains(self.purpose_contains, row.get("purpose"))
            and contains(self.assignment_contains, row.get("assignment"))
        )

    def _find_match(self, row):
        for rule in self:
            if rule._matches(row):
                return rule
        return self.browse()
