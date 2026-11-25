import datetime

from odoo.tests import BaseCase, tagged

from ..models import document_classifier as dc


@tagged("post_install", "-at_install")
class TestDocumentClassifier(BaseCase):
    def test_type_from_filename(self):
        cases = {
            "2023-11-29_HGA_2022_WE_7.pdf": "annual_statement",
            "Jahresabschluss_2024.pdf": "annual_statement",
            "WiPlan_2026_WE_7.pdf": "annual_statement",
            "2025-11-04_HKA_2024_WE_7.pdf": "utility_statement",
            "2025-11-27-BKA_2024_WE7.pdf": "utility_statement",
            "MV_Eheleute_Karcher_01.05.2001.pdf": "lease",
            "2026-03-16_Kündigung_MV.pdf": "lease",
            "Zustimmung_Mieterhöhung_MB_Karcher_01.02.2025.pdf": "lease",
            "Protokoll_ETV_29.09.2026.pdf": "owners_meeting",
            "Umlaufbeschluss_ET_Weber-Dr._Kilian.pdf": "owners_meeting",
            "Einladung_neu.pdf": "owners_meeting",
            "Mahnung_Grundsteuer.pdf": "tax",
            "Rechnung_RE25194_02.06.2025.pdf": "invoice",
            "RE_125.2025.pdf": "invoice",
            "Verwaltervertrag_Kilian+Weber_B1_WE39.pdf": "other",
            "Auerbach_Selbstauskunft.pdf": "other",
            "Angebot_AG1492_19.05.2022.pdf": "other",
        }
        for name, expected in cases.items():
            self.assertEqual(dc.classify(name)["document_type"], expected, name)

    def test_type_from_text_subject_first(self):
        text = ("HV Hendlmaier Stadtplatz 17 Bad Griesbach, 02.06.2022 Betreff: Jahresabrechnung "
                "2021 und Termin Eigentümerversammlung 10171 Brunnenstr. 1+3")
        res = dc.classify("Abr.10171-39-4-0.2021.KilianWeber_UlrichChristine.pdf", text)
        self.assertEqual(res["document_type"], "annual_statement")
        self.assertEqual(res["year"], 2021)
        self.assertEqual(res["date"], datetime.date(2022, 6, 2))
        self.assertEqual(res["title"], "Jahresabrechnung 2021")
        text = ("Bad Griesbach, 31.10.2024 Protokoll der ordentlichen Eigentümerversammlung "
                "Datum: 07.10.2024 TOP 3 Wirtschaftsplan 2025")
        res = dc.classify("scan.pdf", text)
        self.assertEqual(res["document_type"], "owners_meeting")
        self.assertEqual(res["date"], datetime.date(2024, 10, 31))
        self.assertEqual(res["year"], 2024)
        # letter head noise ("Bankverbindung") does not decide the type
        text = ("Bankverbindung: IBAN DE.. Bad Griesbach, 19.06.2023 Termininformation "
                "Trinkwasserprobeentnahme - Legionellenprüfung")
        self.assertEqual(dc.classify("Abr.2099_4.pdf", text)["label"],
                         "Information Trinkwasser / Legionellen")
        self.assertEqual(dc.classify("scan.pdf", "")["document_type"], "other")

    def test_dates(self):
        self.assertEqual(dc.date_from_filename("241125_MBA_Jahresabrechnung_221038.pdf"),
                         datetime.date(2024, 11, 25))
        self.assertEqual(dc.date_from_filename("DOC070823-07082023135815.pdf"),
                         datetime.date(2023, 8, 7))
        self.assertEqual(dc.date_from_filename("MX-2610N_20251217_125642.pdf"),
                         datetime.date(2025, 12, 17))
        self.assertEqual(dc.date_from_text("Leipzig, 8. Dezember 2022 WEG Herbartstraße"),
                         datetime.date(2022, 12, 8))
        # birth dates and periods are not the document date
        self.assertEqual(dc.date_from_text("Mieter Peggy geb. am: 25.06.1990 Leipzig, 01.06.2019"),
                         datetime.date(2019, 6, 1))
        self.assertEqual(dc.date_from_text("Zeitraum: 01.01.2026-31.12.2026 Abrechnungstage von "
                                           "01.01.2026 Druckdatum: 04.11.2025"),
                         datetime.date(2025, 11, 4))

    def test_titles(self):
        self.assertEqual(dc.title_from_filename("2023-11-29_HGA_2022_WE_7.pdf"), "HGA 2022 WE 7")
        self.assertEqual(dc.title_from_filename("Abrechnung_2023_WE4_Korrektur.pdf"),
                         "Abrechnung 2023 WE4 Korrektur")
        self.assertEqual(dc.title_from_filename("32_1.pdf"), "")
        self.assertEqual(dc.title_from_filename("scan.pdf"), "")
        self.assertEqual(dc.title_from_filename("Hasenwinkel_22___Übersicht.pdf"),
                         "Hasenwinkel 22 – Übersicht")

    def test_pick_apartment(self):
        cands = [
            {"id": 4, "name": "Zur Mühle I", "terms": "WE 4, WHG 4, 20005 400, 1. OG links"},
            {"id": 6, "name": "Zur Mühle II", "terms": "WE 6, WHG 6, 20005 600, DG links, Bock"},
        ]
        self.assertEqual(dc.pick_apartment("Abrechnung_2023_WE6_Korrektur.pdf", "", cands, 4)[0], 6)
        self.assertEqual(dc.pick_apartment("x.pdf", "Ihre Wohnungsnummer 20005 / 600 / 006", cands, 4)[0], 6)
        self.assertEqual(dc.pick_apartment("Abrechnung_2023_WE4_Korrektur.pdf", "", cands, 4)[0], 4)
        # WE 60 is not WE 6; both units mentioned -> default
        self.assertEqual(dc.pick_apartment("x.pdf", "WE 60", cands, 4)[0], 4)
        apt, why = dc.pick_apartment("x.pdf", "Wohnung Nr. WE 4 und WE 6", cands, 4)
        self.assertEqual(apt, 4)
        self.assertIn("mehrere", why)
        self.assertEqual(dc.pick_apartment("x.pdf", "Bockwurst", cands, 4)[0], 4)

    def test_sender(self):
        rules = [{"pattern": "santo-immobilien"}, {"pattern": "heilig@santo-immobilien.de"},
                 {"pattern": "t-online"}]
        self.assertEqual(dc.match_sender("heilig@santo-immobilien.de", rules)["pattern"],
                         "heilig@santo-immobilien.de")
        self.assertEqual(dc.match_sender("info@santo-immobilien.de", rules)["pattern"],
                         "santo-immobilien")
        self.assertIsNone(dc.match_sender("x@gmx.de", rules))
        self.assertEqual(dc.sender_from_path("mails/info@santo-immobilien.de/a.pdf"),
                         "info@santo-immobilien.de")
        self.assertEqual(dc.sender_from_path("a.pdf"), "")
