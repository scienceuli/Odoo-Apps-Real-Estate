import hashlib

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from . import document_classifier as dc


# document types that refer to a period which usually differs from the document date
# (e.g. the Hausgeldabrechnung 2023 is sent in November 2024)
PERIOD_TYPES = ("annual_statement", "utility_statement", "tax")


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
    date = fields.Date("Datum", default=fields.Date.context_today,
                       help="Datum des Dokuments (Ausstellung bzw. Eingang)")
    year = fields.Integer(
        "Bezugsjahr", compute="_compute_year", store=True, readonly=False, index=True,
        help="Jahr, auf das sich das Dokument bezieht, z. B. das Abrechnungsjahr einer "
             "Hausgeld- oder Nebenkostenabrechnung. Standard: Jahr des Datums.")
    partner_id = fields.Many2one("res.partner", "Kontakt")
    file = fields.Binary("PDF", attachment=True, required=True)
    file_name = fields.Char("Dateiname")
    note = fields.Text("Bemerkung")
    keywords = fields.Char("Schlagworte", help="Beim Import aus Dateiname und PDF-Text erkannt")
    sender = fields.Char("Absender", help="E-Mail-Absender bzw. Ordner, aus dem das Dokument stammt")
    import_info = fields.Char("Zuordnung", readonly=True, help="Wie Wohnung und Dokumentart beim Import bestimmt wurden")
    checksum = fields.Char("Prüfsumme (SHA-1)", index=True, readonly=True, copy=False,
                           help="Erkennt dieselbe Datei bei einem erneuten Import")

    @api.depends("date", "document_type")
    def _compute_year(self):
        """Follow the date, except for period documents whose year was set explicitly."""
        for rec in self:
            if rec.date and (not rec.year or rec.document_type not in PERIOD_TYPES):
                rec.year = rec.date.year
            else:
                rec.year = rec.year

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
