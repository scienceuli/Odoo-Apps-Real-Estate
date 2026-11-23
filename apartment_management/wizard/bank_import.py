import base64
import binascii

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.bank_parser import BankFileError


def _raw_bytes(value):
    """Binary field content as bytes (Odoo 20: raw content, Odoo <= 19: base64)."""
    if not value:
        return b""
    data = value.encode() if isinstance(value, str) else bytes(value)
    if data[:2] == b"PK" or b";" in data[:4096]:  # xlsx (zip) or DKB csv: already raw
        return data
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return data


class ApartmentBankImport(models.TransientModel):
    _name = "apartment.bank.import"
    _description = "Bankumsätze importieren"

    @api.model
    def _default_journal(self):
        return self.env["account.journal"].search(
            [("type", "=", "bank"), *self.env["account.journal"]._check_company_domain(self.env.company)],
            limit=1,
        )

    journal_id = fields.Many2one(
        "account.journal", "Bankkonto", required=True, default=_default_journal,
        domain="[('type', '=', 'bank')]",
    )
    file = fields.Binary("Datei", required=True, attachment=False,
                         help="DKB-Umsatzliste (CSV) oder Jahresdatei umsaetze_JJJJ.xlsx")
    file_name = fields.Char("Dateiname")
    update_existing = fields.Boolean(
        "Offene Umsätze aktualisieren", default=True,
        help="Bereits importierte, noch nicht gebuchte Umsätze mit der Zuordnung aus dieser Datei "
             "ergänzen. Bereits gebuchte Umsätze werden nie geändert.",
    )
    state = fields.Selection([("draft", "Entwurf"), ("done", "Importiert")], default="draft")
    result = fields.Text("Ergebnis", readonly=True)
    line_ids = fields.Many2many("account.bank.statement.line", string="Importierte Umsätze")
    to_check_ids = fields.Many2many(
        "account.bank.statement.line", "apartment_bank_import_check_rel", string="Zu klären"
    )

    def action_import(self):
        self.ensure_one()
        try:
            res = self.env["account.bank.statement.line"].apartment_import_bank_file(
                self.journal_id, _raw_bytes(self.file), self.file_name or "", self.update_existing
            )
        except BankFileError as e:
            raise UserError(str(e)) from e
        lines = [
            _("Datei: %s", self.file_name or "-"),
            _("Zeilen gelesen: %s", res["rows"]),
            _("Neu angelegt: %s", len(res["created"])),
            _("Nachträglich gebucht: %s", len(res["updated"])),
            _("Übersprungen (bereits vorhanden): %s", res["skipped"]),
            _("Davon noch zu klären: %s", len(res["to_check"])),
        ]
        self.write({
            "state": "done",
            "result": "\n".join(lines),
            "line_ids": [(6, 0, (res["created"] | res["updated"]).ids)],
            "to_check_ids": [(6, 0, res["to_check"].ids)],
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def _open(self, records, name):
        action = self.env["ir.actions.act_window"]._for_xml_id("apartment_management.action_apartment_bank_line")
        action.update({"name": name, "domain": [("id", "in", records.ids)], "context": {}})
        return action

    def action_view_imported(self):
        return self._open(self.line_ids, _("Importierte Umsätze"))

    def action_view_to_check(self):
        return self._open(self.to_check_ids, _("Zu klärende Umsätze"))
