from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    apartment_count = fields.Integer("Wohnungen", compute="_compute_apartment_count")

    def _apartment_domain(self):
        """Apartments where the partner is tenant or (current or former) manager."""
        managed = (
            self.env["apartment.manager"]
            .with_context(active_test=False)
            .search([("partner_id", "in", self.ids)])
            .apartment_id
        )
        return ["|", ("tenant_id", "in", self.ids), ("id", "in", managed.ids)]

    def _compute_apartment_count(self):
        Apartment = self.env["apartment.apartment"]
        for rec in self:
            rec.apartment_count = Apartment.search_count(rec._apartment_domain())

    def action_view_apartments(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "apartment_management.action_apartment"
        )
        action["domain"] = self._apartment_domain()
        return action
