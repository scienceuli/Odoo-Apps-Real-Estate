import hashlib

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from . import document_classifier as dc


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
    keywords = fields.Char("Schlagworte", help="Beim Import aus Dateiname und PDF-Text erkannt")
    sender = fields.Char("Absender", help="E-Mail-Absender bzw. Ordner, aus dem das Dokument stammt")
    import_info = fields.Char("Zuordnung", readonly=True, help="Wie Wohnung und Dokumentart beim Import bestimmt wurden")
    checksum = fields.Char("Prüfsumme (SHA-1)", index=True, readonly=True, copy=False,
                           help="Erkennt dieselbe Datei bei einem erneuten Import")

    @api.constrains("file_name")
    def _check_pdf(self):
        for rec in self:
            if rec.file_name and not rec.file_name.lower().endswith(".pdf"):
                raise ValidationError(_("Bitte nur PDF-Dateien hochladen."))

    @api.onchange("file_name")
    def _onchange_file_name_classify(self):
        """Suggest title, type and year from the file name of a manual upload."""
        for rec in self:
            if not rec.file_name:
                continue
            res = dc.classify(rec.file_name)
            if not rec.name:
                rec.name = res["title"]
            if rec.document_type == "other" and res["document_type"] != "other":
                rec.document_type = res["document_type"]
            if res["date"]:
                rec.date = res["date"]
            if res["year"]:
                rec.year = res["year"]
            if not rec.keywords and res["keywords"]:
                rec.keywords = ", ".join(res["keywords"])

    @staticmethod
    def _checksum(data):
        return hashlib.sha1(data).hexdigest()
