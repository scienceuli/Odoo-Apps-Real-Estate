from odoo import api, fields, models


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
    land_register = fields.Char("Grundbuch / Flurstück")

    # Beteiligte
    property_manager_id = fields.Many2one(
        "res.partner", "Hausverwaltung", tracking=True, help="WEG-/Hausverwaltung"
    )
    rental_manager_id = fields.Many2one(
        "res.partner", "Mieterverwaltung", tracking=True, help="Mietverwaltung (SE-Verwaltung)"
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

    @api.depends("document_ids")
    def _compute_document_count(self):
        for rec in self:
            rec.document_count = len(rec.document_ids)

    def action_view_documents(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "apartment_management.action_apartment_document"
        )
        action["domain"] = [("apartment_id", "=", self.id)]
        action["context"] = {"default_apartment_id": self.id}
        return action
