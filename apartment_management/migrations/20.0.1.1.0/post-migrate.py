from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Turn the former two manager fields into active apartment.manager lines."""
    cr.execute("SELECT to_regclass('apartment_manager_legacy')")
    if not cr.fetchone()[0]:
        return
    env = api.Environment(cr, SUPERUSER_ID, {"tracking_disable": True})
    cr.execute(
        "SELECT apartment_id, property_manager_id, rental_manager_id FROM apartment_manager_legacy"
    )
    vals_list = []
    for apartment_id, prop_id, rent_id in cr.fetchall():
        if prop_id and prop_id == rent_id:
            vals_list.append({"apartment_id": apartment_id, "partner_id": prop_id, "role": "both"})
            continue
        if prop_id:
            vals_list.append({"apartment_id": apartment_id, "partner_id": prop_id, "role": "property"})
        if rent_id:
            vals_list.append({"apartment_id": apartment_id, "partner_id": rent_id, "role": "rental"})
    managers = env["apartment.manager"].create(vals_list)
    # make sure the stored "current manager" fields match the new lines
    apartments = managers.apartment_id
    env.add_to_compute(apartments._fields["property_manager_id"], apartments)
    env.add_to_compute(apartments._fields["rental_manager_id"], apartments)
    apartments.flush_recordset()
    cr.execute("DROP TABLE apartment_manager_legacy")
