import base64
import binascii
import csv
import io
import logging
import zipfile

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models import document_classifier as dc

_logger = logging.getLogger(__name__)

MANIFEST = "manifest.csv"
MAX_TEXT_PAGES = 3


def _raw_bytes(value):
    """Binary field content as bytes (Odoo 20: raw content, Odoo <= 19: base64)."""
    if not value:
        return b""
    data = value.encode() if isinstance(value, str) else bytes(value)
    if data[:2] == b"PK" or data[:5] == b"%PDF-":
        return data
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return data


def pdf_text(data, pages=MAX_TEXT_PAGES):
    """Text of the first pages of a PDF ('' for scans or unreadable files)."""
    try:
        from odoo.tools.pdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:pages])
    except Exception as e:  # noqa: BLE001 – a broken PDF must not stop the import
        _logger.info("PDF text extraction failed: %s", e)
        return ""


def read_manifest(data):
    """manifest.csv (';', UTF-8) -> {path: row}. Columns: file;apartment;document_type;…"""
    text = data.decode("utf-8-sig", errors="replace")
    rows = csv.DictReader(io.StringIO(text), delimiter=";")
    return {(r.get("file") or "").strip(): r for r in rows if (r.get("file") or "").strip()}


class ApartmentDocumentImport(models.TransientModel):
    _name = "apartment.document.import"
    _description = "Dokumente importieren"

    file = fields.Binary(
        "Datei", required=True, attachment=False,
        help="ZIP mit PDFs (Ordner je Absender, z. B. info@santo-immobilien.de/…; optional "
             "manifest.csv mit vorab bestimmter Zuordnung) oder ein einzelnes PDF",
    )
    file_name = fields.Char("Dateiname")
    sender = fields.Char(
        "Absender", help="Für ein einzelnes PDF oder eine ZIP ohne Absender-Ordner: "
                         "E-Mail-Adresse oder Domain des Absenders",
    )
    apartment_id = fields.Many2one(
        "apartment.apartment", "Wohnung (Ersatz)",
        help="Für Dateien, deren Absender keiner Absender-Zuordnung entspricht. "
             "Leer: solche Dateien werden übersprungen.",
    )
    use_manifest = fields.Boolean(
        "manifest.csv verwenden", default=True,
        help="Enthält die ZIP eine manifest.csv, gelten deren Wohnung, Dokumentart, Titel, "
             "Datum, Jahr und Schlagworte (z. B. mit OCR vorab ermittelt).",
    )
    state = fields.Selection([("draft", "Entwurf"), ("done", "Importiert")], default="draft")
    result = fields.Text("Ergebnis", readonly=True)
    skipped = fields.Text("Übersprungen", readonly=True)
    document_ids = fields.Many2many("apartment.document", string="Importierte Dokumente")

    # ------------------------------------------------------------------ helpers

    def _entries(self):
        """[(path, bytes)] of the PDFs in the upload, plus the manifest (or {})."""
        data = _raw_bytes(self.file)
        name = self.file_name or "dokument.pdf"
        if data[:5] == b"%PDF-":
            return [(name, data)], {}, []
        if data[:2] != b"PK":
            raise UserError(_("Bitte eine ZIP-Datei oder ein PDF hochladen."))
        entries, manifest, ignored = [], {}, []
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info in zf.infolist():
                    path = info.filename
                    base = path.rsplit("/", 1)[-1]
                    if info.is_dir() or path.startswith("__MACOSX/") or base.startswith("."):
                        continue
                    if base.lower() == MANIFEST:
                        if self.use_manifest:
                            manifest = read_manifest(zf.read(info))
                        continue
                    if not base.lower().endswith(".pdf"):
                        ignored.append(path)
                        continue
                    entries.append((path, zf.read(info)))
        except zipfile.BadZipFile as e:
            raise UserError(_("Die ZIP-Datei ist beschädigt: %s", e)) from e
        return entries, manifest, ignored

    def _partner_for(self, rule, sender):
        if rule and rule.partner_id:
            return rule.partner_id
        domain = sender.split("@", 1)[-1].lower() if sender else ""
        if not domain or "." not in domain:
            return self.env["res.partner"]
        partner = self.env["res.partner"].search([("email", "=ilike", f"%@{domain}")], limit=1)
        return partner.commercial_partner_id

    def _plan(self, path, data, manifest, rules, apartments_by_name):
        """Values for one file, or (None, reason) if it cannot be assigned."""
        Doc = self.env["apartment.document"]
        sender = dc.sender_from_path(path) or self.sender or ""
        row = manifest.get(path) or {}
        if row.get("sender"):
            sender = row["sender"].strip()
        rule = dc.match_sender(sender, rules) if sender else None
        file_name = path.rsplit("/", 1)[-1]

        text = "" if row else pdf_text(data)
        res = dc.classify(file_name, text)
        info = []

        apartment = self.env["apartment.apartment"]
        if row.get("apartment"):
            apartment = apartments_by_name.get(row["apartment"].strip(), apartment)
            if apartment:
                info.append(_("manifest.csv"))
        if not apartment and rule:
            apt_id, why = dc.pick_apartment(file_name, text, rule.candidates(), rule.apartment_id.id)
            apartment = apartment.browse(apt_id)
            info.append(_("Absender %(p)s", p=rule.pattern) + (f" – {why}" if why else ""))
        if not apartment and self.apartment_id:
            apartment = self.apartment_id
            info.append(_("Ersatz-Wohnung"))
        if not apartment:
            return None, _("keine Wohnung (Absender „%s“ ohne Zuordnung)", sender or "-")

        types = dict(Doc._fields["document_type"].selection)
        dtype = (row.get("document_type") or "").strip() or res["document_type"]
        if dtype not in types:
            dtype = "other"
        date, year = res["date"], res["year"]
        if row:
            try:
                date = fields.Date.to_date((row.get("date") or "").strip() or None) or date
                year = int((row.get("year") or "").strip() or 0) or year
            except ValueError:
                info.append(_("Datum/Jahr in manifest.csv ungültig"))
        keywords = row["keywords"] if "keywords" in row else ", ".join(res["keywords"])
        vals = {
            "apartment_id": apartment.id,
            "name": (row.get("title") or "").strip() or res["title"],
            "document_type": dtype,
            "date": date or False,
            "year": year or False,
            "keywords": keywords or False,
            "sender": sender or False,
            "partner_id": self._partner_for(rule, sender).id or False,
            "file": base64.b64encode(data).decode(),
            "file_name": file_name,
            "checksum": Doc._checksum(data),
            "import_info": "; ".join(info)[:250],
        }
        return vals, None

    # ------------------------------------------------------------------ action

    def action_import(self):
        self.ensure_one()
        entries, manifest, ignored = self._entries()
        if not entries:
            raise UserError(_("Keine PDF-Dateien gefunden."))
        Doc = self.env["apartment.document"]
        rules = self.env["apartment.document.sender"].search([])
        apartments_by_name = {a.name: a for a in self.env["apartment.apartment"].search([])}

        vals_list, skipped, seen = [], [], set()
        duplicates = 0
        for path, data in entries:
            vals, reason = self._plan(path, data, manifest, rules, apartments_by_name)
            if not vals:
                skipped.append(f"{path}: {reason}")
                continue
            key = (vals["apartment_id"], vals["checksum"])
            if key in seen or Doc.search_count(
                [("apartment_id", "=", vals["apartment_id"]), ("checksum", "=", vals["checksum"])],
                limit=1,
            ):
                duplicates += 1
                skipped.append(f"{path}: {_('bereits vorhanden')}")
                continue
            seen.add(key)
            vals_list.append(vals)
        docs = Doc.create(vals_list)
        skipped += [f"{p}: {_('kein PDF')}" for p in ignored]

        by_type = {}
        types = dict(Doc._fields["document_type"].selection)
        for doc in docs:
            by_type[types[doc.document_type]] = by_type.get(types[doc.document_type], 0) + 1
        by_apartment = {}
        for doc in docs:
            by_apartment[doc.apartment_id.name] = by_apartment.get(doc.apartment_id.name, 0) + 1
        lines = [
            _("Datei: %s", self.file_name or "-"),
            _("PDF-Dateien: %s", len(entries)),
            _("Importiert: %s", len(docs)),
            _("Bereits vorhanden: %s", duplicates),
            _("Übersprungen (ohne Wohnung / kein PDF): %s", len(skipped) - duplicates),
            "",
            _("Je Wohnung:"),
            *[f"  {k}: {v}" for k, v in sorted(by_apartment.items())],
            _("Je Dokumentart:"),
            *[f"  {k}: {v}" for k, v in sorted(by_type.items())],
        ]
        self.write({
            "state": "done",
            "result": "\n".join(lines),
            "skipped": "\n".join(skipped),
            "document_ids": [(6, 0, docs.ids)],
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_view_documents(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "apartment_management.action_apartment_document"
        )
        action["domain"] = [("id", "in", self.document_ids.ids)]
        action["context"] = {"search_default_group_apartment": 1}
        return action
