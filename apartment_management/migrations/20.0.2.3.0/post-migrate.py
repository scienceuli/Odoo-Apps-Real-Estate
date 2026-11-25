import logging

_logger = logging.getLogger(__name__)

PERIOD_TYPES = ("annual_statement", "utility_statement", "tax")


def migrate(cr, version):
    """'year' is now the reference year and follows the date by default.

    Before, it defaulted to the current year independently of the date, so documents
    entered with an older date got a wrong year. Fix those; keep explicit years of
    period documents (statements, tax), which may legitimately differ from the date.
    """
    cr.execute(
        """
        UPDATE apartment_document
           SET year = EXTRACT(YEAR FROM date)::int
         WHERE date IS NOT NULL
           AND (COALESCE(year, 0) = 0
                OR (document_type NOT IN %s AND year <> EXTRACT(YEAR FROM date)::int))
        """,
        (PERIOD_TYPES,),
    )
    _logger.info("apartment.document: reference year corrected on %s documents", cr.rowcount)
