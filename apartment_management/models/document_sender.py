from odoo import _, fields, models
from odoo.exceptions import UserError

from .document_classifier import DEFAULT_SENDERS, DEFAULT_TERMS


class ApartmentDocumentSender(models.Model):
    _name = "apartment.document.sender"
    _description = "Absender-Zuordnung für Dokumente"
    _order = "pattern"

    pattern = fields.Char(
        "Absender enthält", required=True,
        help="E-Mail-Adresse, Domain oder Teil davon, z. B. 'santo-immobilien'. "
             "Bei mehreren passenden Regeln gewinnt die längste.",
    )
    apartment_id = fields.Many2one(
        "apartment.apartment", "Wohnung", required=True, ondelete="cascade",
        help="Standard-Wohnung für Dokumente dieses Absenders",
    )
    alternative_ids = fields.Many2many(
        "apartment.apartment", string="Weitere Wohnungen",
        help="Andere Wohnungen derselben Verwaltung (z. B. im selben Haus). Ein Dokument "
             "geht dorthin, wenn nur deren Erkennungsmerkmale (WE-Nr., Lage, Mieter) im "
             "Dateinamen oder im Text vorkommen.",
    )
    partner_id = fields.Many2one(
        "res.partner", "Kontakt", help="Wird als Kontakt am Dokument eingetragen. Leer: "
                                       "Kontakt mit passender E-Mail-Domain, falls vorhanden.",
    )
    active = fields.Boolean(default=True)

    _pattern_unique = models.Constraint("unique(pattern)", "Diese Absender-Regel gibt es bereits.")

    def candidates(self):
        """Candidate apartments for apartment_classifier.pick_apartment()."""
        self.ensure_one()
        return [
            {"id": a.id, "name": a.name, "terms": a.document_match_terms or ""}
            for a in self.apartment_id | self.alternative_ids
        ]

    def action_create_defaults(self):
        """Create the sender rules of the first import (idempotent).

        Not @api.model: the list header button (display="always") passes the selected ids
        (possibly none) as first argument, which call_kw only consumes for record methods.
        """
        Apartment = self.env["apartment.apartment"]
        by_name = {a.name: a for a in Apartment.search([])}
        missing = sorted({n for _p, n, alt in DEFAULT_SENDERS for n in [n, *alt]} - set(by_name))
        if missing:
            raise UserError(_("Wohnungen nicht gefunden: %s", ", ".join(missing)))
        created = 0
        for pattern, name, alternatives in DEFAULT_SENDERS:
            alt_ids = [by_name[n].id for n in alternatives]
            rule = self.with_context(active_test=False).search([("pattern", "=", pattern)])
            if rule:
                continue
            self.create({
                "pattern": pattern,
                "apartment_id": by_name[name].id,
                "alternative_ids": [(6, 0, alt_ids)],
            })
            created += 1
        for name, terms in DEFAULT_TERMS.items():
            if not by_name[name].document_match_terms:
                by_name[name].document_match_terms = terms
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Absender-Zuordnungen"),
                "message": _("%s Regeln angelegt.", created),
                "type": "success",
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }
