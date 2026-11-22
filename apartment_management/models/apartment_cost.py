from odoo import api, fields, models

INTERVAL_FACTOR = {"monthly": 1.0, "quarterly": 1 / 3, "half_yearly": 1 / 6, "yearly": 1 / 12}


class ApartmentCost(models.Model):
    _name = "apartment.cost"
    _description = "Wohnungskosten"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    apartment_id = fields.Many2one(
        "apartment.apartment", "Wohnung", required=True, ondelete="cascade", index=True
    )
    currency_id = fields.Many2one(related="apartment_id.currency_id")
    cost_type = fields.Selection(
        [
            ("house_fee", "Hausgeld (Hausverwaltung)"),
            ("rental_management", "Mieterverwaltung"),
            ("property_tax", "Grundsteuer"),
            ("tax_office", "Finanzamt / Steuervorauszahlung"),
            ("insurance", "Versicherung"),
            ("loan", "Kredit / Darlehen"),
            ("maintenance", "Instandhaltung / Rücklage"),
            ("other", "Sonstiges"),
        ],
        "Kostenart",
        required=True,
        default="house_fee",
    )
    name = fields.Char("Beschreibung")
    partner_id = fields.Many2one("res.partner", "Empfänger")
    amount = fields.Monetary("Betrag", required=True)
    interval = fields.Selection(
        [
            ("monthly", "monatlich"),
            ("quarterly", "vierteljährlich"),
            ("half_yearly", "halbjährlich"),
            ("yearly", "jährlich"),
        ],
        "Intervall",
        required=True,
        default="monthly",
    )
    amount_monthly = fields.Monetary("Betrag / Monat", compute="_compute_amount_monthly", store=True)
    amount_yearly = fields.Monetary("Betrag / Jahr", compute="_compute_amount_monthly", store=True)

    @api.depends("amount", "interval")
    def _compute_amount_monthly(self):
        for rec in self:
            rec.amount_monthly = rec.amount * INTERVAL_FACTOR.get(rec.interval, 1.0)
            rec.amount_yearly = rec.amount_monthly * 12
