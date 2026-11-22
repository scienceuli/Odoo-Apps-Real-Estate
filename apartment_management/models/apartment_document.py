from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ApartmentDocument(models.Model):
    _name = "apartment.document"
    _description = "Wohnungsdokument"
    _order = "date desc, id desc"

    apartment_id = fields.Many2one(
        "apartment.apartment", "Wohnung", required=True, ondelete="cascade", index=True
    )
    name = fields.Char("Titel", required=True)
    document_type = fields.Selection(
        [
            ("lease", "Mietvertrag"),
            ("annual_statement", "Jahresabrechnung / Jahresabschluss"),
            ("utility_statement", "Nebenkostenabrechnung"),
            ("tax", "Steuer / Finanzamt"),
            ("owners_meeting", "Eigentümerversammlung"),
            ("handover", "Übergabeprotokoll"),
            ("invoice", "Rechnung"),
            ("other", "Sonstiges"),
        ],
        "Dokumentart",
        required=True,
        default="other",
    )
    date = fields.Date("Datum", default=fields.Date.context_today)
    year = fields.Integer("Jahr", default=lambda self: fields.Date.context_today(self).year)
    partner_id = fields.Many2one("res.partner", "Kontakt")
    file = fields.Binary("PDF", attachment=True, required=True)
    file_name = fields.Char("Dateiname")
    note = fields.Text("Bemerkung")

    @api.constrains("file_name")
    def _check_pdf(self):
        for rec in self:
            if rec.file_name and not rec.file_name.lower().endswith(".pdf"):
                raise ValidationError(_("Bitte nur PDF-Dateien hochladen."))
