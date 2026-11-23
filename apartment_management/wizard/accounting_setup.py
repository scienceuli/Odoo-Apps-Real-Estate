from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

from ..models import bank_parser
from ..models.apartment import PLAN_PARAM

# code, name, account type, category code, category name
ACCOUNTS = [
    ("4000", "Mieteinnahmen", "income", "rent", "Miete"),
    ("6000", "Hausgeld", "expense", "house_fee", "Hausgeld"),
    ("6010", "Sonderumlage", "expense", "special_assessment", "Sonderumlage"),
    ("6050", "Hausgeldabrechnung (Nachzahlung/Erstattung)", "expense", "settlement", "Abrechnung"),
    ("6100", "Grundsteuer / Abgaben", "expense", "tax", "Steuer"),
    ("6200", "Versicherungen", "expense", "insurance", "Versicherung"),
    ("6300", "Instandhaltung / sonstige Kosten", "expense", "other_cost", "Kosten"),
    ("6400", "Schuldzinsen", "expense", "interest", "Schuldzinsen"),
    ("6900", "Bankgebühren", "expense", "bank_fee", "Bankkosten"),
    ("2900", "Privateinlagen (Investment)", "equity", "investment", "Investment"),
    ("2990", "Anfangssaldo / Saldovortrag", "equity", "opening", "Anfangssaldo"),
]
LOAN_CODE_START = 3100


class ApartmentAccountingSetup(models.TransientModel):
    """Set up the accounting of the rental company (idempotent, can be run again)."""

    _name = "apartment.accounting.setup"
    _description = "Buchhaltung für Wohnungen einrichten"

    company_id = fields.Many2one("res.company", "Unternehmen", required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    journal_id = fields.Many2one(
        "account.journal", "Bankjournal", domain="[('type', '=', 'bank'), ('company_id', '=', company_id)]",
        help="Leer lassen, um ein neues Bankjournal anzulegen",
    )
    journal_name = fields.Char("Name neues Bankjournal", default="DKB Girokonto")
    journal_code = fields.Char("Kürzel", default="DKB", size=5)
    iban = fields.Char("IBAN")
    opening_date = fields.Date("Anfangssaldo zum")
    opening_balance = fields.Monetary("Anfangssaldo")
    result = fields.Text("Ergebnis", readonly=True)

    # ------------------------------------------------------------ helpers
    def _account(self, code, name, account_type):
        """Account named ``name``; created with ``code`` or the next free number.

        Existing accounts are only reused by name, never by code: in a standard chart
        (e.g. SKR03) the number may already mean something else.
        """
        Account = self.env["account.account"].with_company(self.company_id)
        company_domain = Account._check_company_domain(self.company_id)
        account = Account.search([*company_domain, ("name", "=", name)], limit=1)
        if account:
            return account, False
        while Account.search_count([*company_domain, ("code", "=", code)]):
            code = str(int(code) + 1)
        return Account.create({
            "code": code,
            "name": name,
            "account_type": account_type,
            "company_ids": [Command.link(self.company_id.id)],
        }), True

    @api.model
    def _base_display_name(self, name):
        """'Zur Mühle II' -> 'Zur Mühle' (loan shared by the apartments of one building)."""
        parts = name.split()
        if len(parts) > 1 and bank_parser._base_name(name) != bank_parser.normalize(name):
            return " ".join(parts[:-1])
        return name

    # ------------------------------------------------------------ action
    def action_setup(self):
        self.ensure_one()
        company = self.company_id
        log = []
        env = self.env
        apartments = env["apartment.apartment"].search([("company_id", "=", company.id)])
        if not apartments:
            raise UserError(_("Im Unternehmen %s gibt es noch keine Wohnungen.", company.name))

        # 1. chart of accounts (needed for suspense account and journals)
        if not company.chart_template:
            env["account.chart.template"].try_loading(None, company, install_demo=False)  # chart of the country
            log.append(_("Kontenplan %s geladen", company.chart_template))

        # 2. rights: full accounting features + analytic accounting for the current user
        groups = env.ref("account.group_account_user") | env.ref("analytic.group_analytic_accounting")
        missing_groups = groups - env.user.all_group_ids
        if missing_groups:
            env.user.sudo().write({"group_ids": [Command.link(g.id) for g in missing_groups]})
            log.append(_("Rechte ergänzt: %s", ", ".join(missing_groups.mapped("name"))))

        # 3. accounts + categories
        Category = env["apartment.booking.category"]
        categories = Category._by_code(company)
        for seq, (code, name, account_type, cat_code, cat_name) in enumerate(ACCOUNTS, start=1):
            account, created = self._account(code, name, account_type)
            if created:
                log.append(_("Konto %(code)s %(name)s angelegt", code=account.code, name=name))
            category = categories.get(cat_code)
            if not category:
                Category.create({"name": cat_name, "code": cat_code, "company_id": company.id,
                                 "account_id": account.id, "sequence": seq * 10})
                log.append(_("Kategorie %s angelegt", cat_name))
            elif not category.account_id:
                category.account_id = account
        if "loan" not in categories:
            Category.create({"name": "Darlehen", "code": "loan", "company_id": company.id, "sequence": 15})
            log.append(_("Kategorie Darlehen angelegt"))

        # 4. analytic plan + cost centres
        Plan = env["account.analytic.plan"].sudo()
        plan = env["apartment.apartment"]._analytic_plan()
        if not plan:
            plan = Plan.search([("name", "=", "Wohnungen"), ("parent_id", "=", False)], limit=1) or Plan.create(
                {"name": "Wohnungen", "default_applicability": "optional"}
            )
            env["ir.config_parameter"].sudo().set_int(PLAN_PARAM, plan.id)
            log.append(_("Kostenstellen-Plan 'Wohnungen' angelegt"))
        without = apartments.filtered(lambda a: not a.analytic_account_id)
        without._ensure_analytic_account()
        if without:
            log.append(_("Kostenstellen angelegt: %s", ", ".join(without.mapped("name"))))

        # 5. loan account per building (Zur Mühle I + II share one loan)
        for building, apts in apartments.grouped(lambda a: self._base_display_name(a.name)).items():
            if all(a.loan_account_id for a in apts):
                continue
            account = apts.loan_account_id[:1]
            if not account:
                name = _("Darlehen %s", building)
                account, created = self._account(str(LOAN_CODE_START), name, "liability_non_current")
                if created:
                    log.append(_("Konto %(code)s %(name)s angelegt", code=account.code, name=name))
            apts.filtered(lambda a: not a.loan_account_id).loan_account_id = account

        # 6. bank journal
        journal = self.journal_id
        if not journal:
            Journal = env["account.journal"]
            bank_journals = Journal.search([*Journal._check_company_domain(company), ("type", "=", "bank")])
            journal = bank_journals.filtered(lambda j: j.code == self.journal_code)[:1]
            if not journal:
                # the chart of accounts usually creates an empty "Bank" journal: use that one
                unused = bank_journals.filtered(
                    lambda j: not j.bank_account_id
                    and not env["account.bank.statement.line"].search_count([("journal_id", "=", j.id)], limit=1)
                )
                if len(unused) == 1:
                    journal = unused
                    journal.name = self.journal_name or journal.name
                    log.append(_("Bankjournal %s übernommen", journal.name))
            if not journal:
                journal = Journal.create({
                    "name": self.journal_name or "Bank",
                    "code": self.journal_code or "BNK",
                    "type": "bank",
                    "company_id": company.id,
                })
                log.append(_("Bankjournal %s angelegt", journal.name))
        if self.iban and not journal.bank_account_id:
            journal.set_bank_account(self.iban.replace(" ", ""))

        # 7. opening balance
        if self.opening_date and self.opening_balance:
            Line = env["account.bank.statement.line"]
            key = f"opening-{self.opening_date}"
            if not Line.search_count([("journal_id", "=", journal.id), ("apartment_import_key", "=", key)]):
                Line.create({
                    "journal_id": journal.id,
                    "date": self.opening_date,
                    "amount": self.opening_balance,
                    "payment_ref": _("Anfangssaldo"),
                    "apartment_import_key": key,
                    "apartment_category_id": Category._by_code(company)["opening"].id,
                })
                log.append(_("Anfangssaldo %s gebucht", self.opening_balance))

        if not log:
            log.append(_("Alles war bereits eingerichtet."))
        self.result = "\n".join(log)
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
