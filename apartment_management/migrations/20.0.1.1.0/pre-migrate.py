def migrate(cr, version):
    """Keep the former plain manager fields before they become computed."""
    cr.execute(
        """
        CREATE TABLE IF NOT EXISTS apartment_manager_legacy AS
        SELECT id AS apartment_id, property_manager_id, rental_manager_id
          FROM apartment_apartment
         WHERE property_manager_id IS NOT NULL OR rental_manager_id IS NOT NULL
        """
    )
