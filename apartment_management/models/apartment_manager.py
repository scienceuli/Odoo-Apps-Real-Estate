from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

PROPERTY_ROLES = ("property", "both")
RENTAL_ROLES = ("rental", "both")


class ApartmentManager(models.Model):
    _name = "apartment.manager"
    _description = "Verwaltung einer Wohnung"
    _order = "active desc, date_from desc, id desc"
    _rec_name = "partner_id"

    apartment_id = fields.Many2one(
        "apartment.apartment", "Wohnung", required=True, ondelete="cascade", index=True
    )
    partner_id = fields.Many2one(
        "res.partner", "Verwaltung", required=True, index=True,
        context={"default_is_company": True},
    )
    contact_id = fields.Many2one(
        "res.partner", "Ansprechpartner",
        domain="[('parent_id', '=', partner_id)]",
    )
    role = fields.Selection(
        [
            ("property", "Hausverwaltung"),
            ("rental", "Mieterverwaltung"),
            ("both", "Haus- und Mieterverwaltung"),
        ],
        "Funktion",
        required=True,
        default="property",
    )
    date_from = fields.Date("Von")
    date_to = fields.Date("Bis")
    active = fields.Boolean("Aktiv", default=True)
    note = fields.Char("Bemerkung")

    @api.constrains("apartment_id", "role", "active")
    def _check_single_active_role(self):
        for apartment in self.apartment_id:
            current = apartment.with_context(active_test=False).manager_ids.filtered("active")
            if len(current.filtered(lambda m: m.role in PROPERTY_ROLES)) > 1:
                raise ValidationError(_(
                    "Die Wohnung „%s“ hat bereits eine aktive Hausverwaltung. "
                    "Beende zuerst die bisherige.", apartment.name))
            if len(current.filtered(lambda m: m.role in RENTAL_ROLES)) > 1:
                raise ValidationError(_(
                    "Die Wohnung „%s“ hat bereits eine aktive Mieterverwaltung. "
                    "Beende zuerst die bisherige.", apartment.name))

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for rec in self:
            if rec.date_from and rec.date_to and rec.date_to < rec.date_from:
                raise ValidationError(_("„Bis“ darf nicht vor „Von“ liegen."))

    def action_end(self):
        """End the mandate: set the end date (if missing) and archive the line."""
        today = fields.Date.context_today(self)
        for rec in self:
            rec.write({"date_to": rec.date_to or today, "active": False})
            rec.apartment_id.message_post(body=_(
                "%(role)s %(partner)s beendet per %(date)s.",
                role=dict(rec._fields["role"].selection)[rec.role],
                partner=rec.partner_id.display_name,
                date=rec.date_to,
            ))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records.filtered("active"):
            rec.apartment_id.message_post(body=_(
                "Neue %(role)s: %(partner)s",
                role=dict(rec._fields["role"].selection)[rec.role],
                partner=rec.partner_id.display_name,
            ))
        return records
