from odoo import _, api, fields, models

PLAN_PARAM = "apartment_management.analytic_plan_id"


class Apartment(models.Model):
    _name = "apartment.apartment"
    _description = "Wohnung"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

    name = fields.Char("Bezeichnung", required=True, tracking=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", "Unternehmen", required=True, default=lambda self: self.env.company
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    state = fields.Selection(
        [("rented", "Vermietet"), ("vacant", "Leerstand"), ("own_use", "Eigennutzung")],
        "Status",
        default="vacant",
        required=True,
        tracking=True,
    )

    # Lage
    street = fields.Char("Strasse")
    zip = fields.Char("PLZ")
    city = fields.Char("Ort")
    country_id = fields.Many2one("res.country", "Land")
    floor = fields.Char("Lage / Etage")
    rooms = fields.Float("Zimmer", digits=(4, 1))
    area = fields.Float("Wohnfläche (m²)", digits=(8, 2))
    unit_number = fields.Char("Wohnungs-Nr.")
    document_match_terms = fields.Char(
        "Erkennungsmerkmale Dokumente",
        help="Kommagetrennte Begriffe, an denen Dokumente dieser Wohnung erkannt werden, wenn "
             "eine Verwaltung mehrere Wohnungen betreut (z. B. 'WE 6, DG links, Bock').",
    )
    land_register = fields.Char("Grundbuch / Flurstück")

    # Beteiligte
    manager_ids = fields.One2many(
        "apartment.manager", "apartment_id", "Verwaltungen",
        context={"active_test": False},
    )
    property_manager_id = fields.Many2one(
        "res.partner", "Hausverwaltung", compute="_compute_current_managers", store=True,
        index=True, help="Aktuelle WEG-/Hausverwaltung (aus den aktiven Verwaltungen)",
    )
    rental_manager_id = fields.Many2one(
        "res.partner", "Mieterverwaltung", compute="_compute_current_managers", store=True,
        index=True, help="Aktuelle Mietverwaltung (aus den aktiven Verwaltungen)",
    )
    tenant_id = fields.Many2one("res.partner", "Mieter", tracking=True)

    # Mietverhältnis
    lease_start = fields.Date("Mietbeginn", tracking=True)
    lease_end = fields.Date("Mietende", tracking=True)
    rent_cold = fields.Monetary("Kaltmiete", tracking=True)
    rent_utilities = fields.Monetary("Nebenkosten-Vorauszahlung", tracking=True)
    rent_other = fields.Monetary("Sonstige Mieteinnahmen", help="z. B. Stellplatz, Garage")
    rent_total = fields.Monetary("Warmmiete", compute="_compute_amounts", store=True)
    deposit = fields.Monetary("Kaution")

    # Kosten
    cost_ids = fields.One2many("apartment.cost", "apartment_id", "Kosten")
    cost_monthly = fields.Monetary("Kosten / Monat", compute="_compute_amounts", store=True)
    net_monthly = fields.Monetary("Überschuss / Monat", compute="_compute_amounts", store=True)
    net_yearly = fields.Monetary("Überschuss / Jahr", compute="_compute_amounts", store=True)

    # Buchhaltung (Ist aus den gebuchten Bankumsätzen)
    analytic_account_id = fields.Many2one(
        "account.analytic.account", "Kostenstelle", copy=False, ondelete="restrict",
        help="Kostenstelle im Plan 'Wohnungen' – wird automatisch angelegt",
    )
    loan_account_id = fields.Many2one(
        "account.account", "Darlehenskonto", ondelete="restrict",
        help="Verbindlichkeitskonto des Darlehens (Tilgung). Mehrere Wohnungen können "
             "sich ein Darlehen teilen (z. B. Zur Mühle I/II).",
    )
    bank_line_count = fields.Integer("Anzahl Buchungen", compute="_compute_booked")
    booked_income_year = fields.Monetary("Einnahmen lfd. Jahr", compute="_compute_booked")
    booked_expense_year = fields.Monetary("Kosten lfd. Jahr", compute="_compute_booked",
                                          help="Aufwandskonten (ohne Tilgung)")
    booked_balance_year = fields.Monetary("Überschuss lfd. Jahr", compute="_compute_booked")
    booked_principal_year = fields.Monetary("Tilgung lfd. Jahr", compute="_compute_booked",
                                            help="Tilgungsanteil der Darlehensraten (keine Kosten)")

    # Dokumente
    document_ids = fields.One2many("apartment.document", "apartment_id", "Dokumente")
    document_count = fields.Integer(compute="_compute_document_count")

    notes = fields.Html("Notizen")

    @api.depends(
        "rent_cold", "rent_utilities", "rent_other", "cost_ids.amount_monthly"
    )
    def _compute_amounts(self):
        for rec in self:
            rec.rent_total = rec.rent_cold + rec.rent_utilities + rec.rent_other
            rec.cost_monthly = sum(rec.cost_ids.mapped("amount_monthly"))
            rec.net_monthly = rec.rent_total - rec.cost_monthly
            rec.net_yearly = rec.net_monthly * 12

    @api.depends("manager_ids.active", "manager_ids.role", "manager_ids.partner_id")
    def _compute_current_managers(self):
        for rec in self:
            current = rec.with_context(active_test=False).manager_ids.filtered("active")
            prop = current.filtered(lambda m: m.role in ("property", "both"))[:1]
            rent = current.filtered(lambda m: m.role in ("rental", "both"))[:1]
            rec.property_manager_id = prop.partner_id
            rec.rental_manager_id = rent.partner_id

    @api.depends("document_ids")
    def _compute_document_count(self):
        for rec in self:
            rec.document_count = len(rec.document_ids)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._ensure_analytic_account()
        return records

    def write(self, vals):
        res = super().write(vals)
        if "name" in vals:
            for rec in self.filtered("analytic_account_id"):
                rec.analytic_account_id.sudo().name = rec.name
        return res

    @api.model
    def _analytic_plan(self):
        plan_id = self.env["ir.config_parameter"].sudo().get_int(PLAN_PARAM)
        return self.env["account.analytic.plan"].sudo().browse(plan_id).exists()

    def _ensure_analytic_account(self):
        plan = self._analytic_plan()
        if not plan:
            return  # accounting not set up yet (Konfiguration › Buchhaltung einrichten)
        for rec in self.filtered(lambda r: not r.analytic_account_id):
            rec.analytic_account_id = self.env["account.analytic.account"].sudo().create({
                "name": rec.name,
                "plan_id": plan.id,
                "company_id": rec.company_id.id,
            })

    def _compute_booked(self):
        year_start = fields.Date.context_today(self).replace(month=1, day=1)
        plan = self._analytic_plan()
        Line = self.env["account.analytic.line"].sudo()
        for rec in self:
            rec.bank_line_count = 0
            rec.booked_income_year = rec.booked_expense_year = 0.0
            rec.booked_balance_year = rec.booked_principal_year = 0.0
            if not rec.id or not plan or not rec.analytic_account_id:
                continue
            rec.bank_line_count = self.env["account.bank.statement.line"].sudo().search_count(
                [("apartment_ids", "in", rec.id)]
            )
            groups = Line._read_group(
                [(plan._column_name(), "=", rec.analytic_account_id.id), ("date", ">=", year_start)],
                ["general_account_id"], ["amount:sum"],
            )
            for account, amount in groups:
                kind = (account.account_type or "").split("_")[0]
                if kind == "income":
                    rec.booked_income_year += amount
                elif kind == "expense":
                    rec.booked_expense_year += amount
                elif kind == "liability":
                    rec.booked_principal_year += amount
            rec.booked_balance_year = rec.booked_income_year + rec.booked_expense_year

    def action_view_bank_lines(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("apartment_management.action_apartment_bank_line")
        action["domain"] = [("apartment_ids", "in", self.id)]
        action["context"] = {"search_default_group_year": 1, "search_default_group_category": 1}
        action["name"] = _("Buchungen %s", self.name)
        return action

    def action_cost_overview(self):
        """Pivot of the analytic items: apartment x account x year (also for the tax advisor)."""
        plan = self._analytic_plan()
        column = plan._column_name() if plan else "account_id"
        accounts = (self or self.search([])).analytic_account_id
        return {
            "type": "ir.actions.act_window",
            "name": _("Kostenübersicht je Wohnung"),
            "res_model": "account.analytic.line",
            "view_mode": "pivot,list,graph",
            "views": [(False, "pivot"), (False, "list"), (False, "graph")],
            "domain": [(column, "in", accounts.ids)],
            "context": {
                "pivot_row_groupby": [column, "general_account_id"],
                "pivot_column_groupby": ["date:year"],
                "pivot_measures": ["amount"],
                "group_by": [column, "general_account_id"],
                "analytic_plan_id": plan.id,
            },
        }

    def action_view_documents(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "apartment_management.action_apartment_document"
        )
        action["domain"] = [("apartment_id", "=", self.id)]
        action["context"] = {"default_apartment_id": self.id}
        return action
