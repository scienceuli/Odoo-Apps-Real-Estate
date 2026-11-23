from datetime import date

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

from ..models import bank_parser


class ApartmentInterestEntry(models.TransientModel):
    """Yearly interest from the bank's interest statement: Schuldzinsen an Darlehen.

    The instalments are booked completely to the loan account during the year; this entry
    moves the interest part from the loan account to the interest expense account.
    """

    _name = "apartment.interest.entry"
    _description = "Jahreszinsen eines Darlehens buchen"

    @api.model
    def _default_date(self):
        return date(fields.Date.context_today(self).year - 1, 12, 31)

    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, required=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    date = fields.Date("Datum", required=True, default=_default_date)
    apartment_ids = fields.Many2many(
        "apartment.apartment", string="Wohnungen", required=True,
        help="Wohnungen des Darlehens; mehrere Wohnungen = gleichmäßige Aufteilung",
    )
    loan_account_id = fields.Many2one(
        "account.account", "Darlehenskonto", compute="_compute_loan_account", store=True, readonly=False, precompute=True,
        required=True,
    )
    amount = fields.Monetary("Zinsen laut Bank", required=True)
    ref = fields.Char("Bezeichnung", compute="_compute_ref", store=True, readonly=False,
                      precompute=True)
    journal_id = fields.Many2one(
        "account.journal", "Journal", required=True, domain="[('type', '=', 'general')]",
        default=lambda self: self.env["account.journal"].search(
            [("type", "=", "general"), *self.env["account.journal"]._check_company_domain(self.env.company)],
            limit=1),
    )

    @api.depends("apartment_ids")
    def _compute_loan_account(self):
        for rec in self:
            accounts = rec.apartment_ids.loan_account_id
            rec.loan_account_id = accounts[:1] if len(accounts) == 1 else False

    @api.depends("apartment_ids", "date")
    def _compute_ref(self):
        for rec in self:
            rec.ref = _("Schuldzinsen %(year)s %(apts)s", year=rec.date.year if rec.date else "",
                        apts=", ".join(rec.apartment_ids.mapped("name")))

    def action_post(self):
        self.ensure_one()
        if self.amount <= 0:
            raise UserError(_("Bitte den Zinsbetrag (positiv) eingeben."))
        interest = self.env["apartment.booking.category"]._by_code(self.company_id).get("interest")
        if not interest or not interest.account_id:
            raise UserError(_("Kategorie 'Schuldzinsen' mit Konto fehlt – bitte Buchhaltung einrichten."))
        missing = self.apartment_ids.filtered(lambda a: not a.analytic_account_id)
        if missing:
            raise UserError(_("Kostenstelle fehlt bei: %s", ", ".join(missing.mapped("name"))))
        distribution = {
            str(apt.analytic_account_id.id): pct
            for apt, pct in zip(self.apartment_ids, bank_parser.split_percent(len(self.apartment_ids)))
        }
        move = self.env["account.move"].create({
            "move_type": "entry",
            "journal_id": self.journal_id.id,
            "date": self.date,
            "ref": self.ref,
            "line_ids": [
                Command.create({
                    "name": self.ref,
                    "account_id": interest.account_id.id,
                    "balance": self.amount,
                    "analytic_distribution": distribution,
                }),
                Command.create({
                    "name": self.ref,
                    "account_id": self.loan_account_id.id,
                    "balance": -self.amount,
                    "analytic_distribution": distribution,
                }),
            ],
        })
        move.action_post()
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": move.id,
            "view_mode": "form",
            "views": [(False, "form")],
        }
