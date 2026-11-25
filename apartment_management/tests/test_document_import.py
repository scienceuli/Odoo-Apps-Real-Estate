import io
import zipfile

from odoo.exceptions import UserError
from odoo.service.model import call_kw
from odoo.tests import TransactionCase, tagged


def make_pdf(text):
    """Minimal one-page PDF with a text line (enough for pypdf text extraction)."""
    stream = f"BT /F1 12 Tf 50 750 Td ({text}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    return out.getvalue()


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


@tagged("post_install", "-at_install")
class TestDocumentImport(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Apt = cls.env["apartment.apartment"]
        cls.apts = {n: Apt.create({"name": n}) for n in [
            "Herbartstraße", "Brunnenstraße", "Zur Mühle I", "Zur Mühle II", "Hasenwinkel"]}
        cls.env["apartment.document.sender"].action_create_defaults()
        cls.santo = cls.env["res.partner"].create({
            "name": "SANTO Immobilienverwaltung", "is_company": True, "email": "info@santo-immobilien.de"})

    def _upload(self, data, name):
        """Like /web/binary/upload_attachment from the many2many_binary widget (res_id 0)."""
        return self.env["ir.attachment"].create({
            "name": name, "raw": data, "res_model": "apartment.document.import", "res_id": 0})

    def _run(self, data, name="mails.zip", **kw):
        files = data if isinstance(data, dict) else {name: data}
        uploads = self.env["ir.attachment"]
        for file_name, content in files.items():
            uploads |= self._upload(content, file_name)
        wiz = self.env["apartment.document.import"].create({
            "attachment_ids": [(6, 0, uploads.ids)], **kw})
        wiz.action_import()
        self.assertFalse(uploads.exists(), "upload files are removed after the import")
        return wiz

    def test_default_rules(self):
        rules = self.env["apartment.document.sender"].search([])
        self.assertEqual(len(rules), 10)
        muehle = rules.filtered(lambda r: r.pattern == "viveto-verwaltung")
        self.assertEqual(muehle.apartment_id, self.apts["Zur Mühle I"])
        self.assertEqual(muehle.alternative_ids, self.apts["Zur Mühle II"])
        self.assertIn("DG links", self.apts["Zur Mühle II"].document_match_terms)
        # idempotent
        self.env["apartment.document.sender"].action_create_defaults()
        self.assertEqual(self.env["apartment.document.sender"].search_count([]), 10)
        # as called by the list header button (web client passes the selected ids first)
        call_kw(self.env["apartment.document.sender"], "action_create_defaults", [[]], {})
        call_kw(self.env["apartment.document.sender"], "action_create_defaults", [rules[:2].ids], {})
        self.assertEqual(self.env["apartment.document.sender"].search_count([]), 10)

    def test_zip_import(self):
        hga = make_pdf("Leipzig, 18.11.2024 Hausgeldabrechnung 2023 WEG Herbartstrasse 1a")
        data = make_zip({
            "heilig@santo-immobilien.de/2024-11-18_HGA_2023_WE_7.pdf": hga,
            "heilig@santo-immobilien.de/copy/2024-11-18_HGA_2023_WE_7.pdf": hga,  # same content
            "f.gaebel@viveto-verwaltung.de/Abrechnung_2023_WE6_Korrektur.pdf": make_pdf("x"),
            "f.gaebel@viveto-verwaltung.de/Abrechnung_2023_WE4_Korrektur.pdf": make_pdf("y"),
            "post@hendlmaier.de/Abr.10171-39-4-0.2099.pdf": make_pdf(
                "Bad Griesbach, 17.07.2025 Protokoll der ordentlichen Eigentuemerversammlung"),
            "x@gmx.de/irgendwas.pdf": make_pdf("z"),
            "schwerdtner-gruppe@t-online.de/HV-Vertrag.doc": b"doc",
        })
        wiz = self._run(data)
        docs = wiz.document_ids
        self.assertEqual(len(docs), 4, wiz.result + "\n" + wiz.skipped)
        hga_doc = docs.filtered(lambda d: d.file_name == "2024-11-18_HGA_2023_WE_7.pdf")
        self.assertEqual(hga_doc.apartment_id, self.apts["Herbartstraße"])
        self.assertEqual(hga_doc.document_type, "annual_statement")
        self.assertEqual(hga_doc.year, 2023)
        self.assertEqual(str(hga_doc.date), "2024-11-18")
        self.assertEqual(hga_doc.partner_id, self.santo)
        self.assertEqual(hga_doc.sender, "heilig@santo-immobilien.de")
        we6 = docs.filtered(lambda d: "WE6" in d.file_name)
        self.assertEqual(we6.apartment_id, self.apts["Zur Mühle II"])
        we4 = docs.filtered(lambda d: "WE4" in d.file_name)
        self.assertEqual(we4.apartment_id, self.apts["Zur Mühle I"])
        prot = docs.filtered(lambda d: d.apartment_id == self.apts["Brunnenstraße"])
        self.assertEqual(prot.document_type, "owners_meeting")
        self.assertEqual(prot.name, "Eigentümerversammlung / Beschluss 17.07.2025")
        self.assertIn("bereits vorhanden", wiz.skipped)
        self.assertIn("x@gmx.de/irgendwas.pdf", wiz.skipped)
        self.assertIn("kein PDF", wiz.skipped)
        # second run: everything is a duplicate
        wiz2 = self._run(data)
        self.assertFalse(wiz2.document_ids)

    def test_manifest_and_single_pdf(self):
        manifest = ("file;apartment;document_type;title;date;year;keywords\n"
                    "scans/scan.pdf;Brunnenstraße;lease;Mietvertrag Skerswetat;2019-06-01;2019;Mietvertrag\n")
        data = make_zip({"scans/scan.pdf": make_pdf("scan"), "manifest.csv": manifest.encode()})
        wiz = self._run(data)
        doc = wiz.document_ids
        self.assertEqual(doc.apartment_id, self.apts["Brunnenstraße"])
        self.assertEqual((doc.document_type, doc.name, doc.year), ("lease", "Mietvertrag Skerswetat", 2019))
        # single PDF: sender field decides, fallback apartment otherwise
        wiz = self._run(make_pdf("Rechnung Nr. 4711"), name="Rechnung_4711.pdf",
                        sender="info@gvs-immoservice.de")
        self.assertEqual(wiz.document_ids.apartment_id, self.apts["Hasenwinkel"])
        self.assertEqual(wiz.document_ids.document_type, "invoice")
        wiz = self._run(make_pdf("Info"), name="info.pdf", apartment_id=self.apts["Zur Mühle II"].id)
        self.assertEqual(wiz.document_ids.apartment_id, self.apts["Zur Mühle II"])

    def test_several_files_and_errors(self):
        zip_a = make_zip({"info@gvs-immoservice.de/Protokoll_ETV_2024.pdf": make_pdf("Protokoll")})
        wiz = self._run({
            "a.zip": zip_a,
            "Rechnung_0815.pdf": make_pdf("Rechnung"),
        }, sender="steffen-immobilien.de")
        self.assertEqual(len(wiz.document_ids), 2, wiz.result + "\n" + wiz.skipped)
        self.assertEqual(
            {(d.apartment_id.name, d.document_type) for d in wiz.document_ids},
            {("Hasenwinkel", "owners_meeting"), ("Zur Mühle I", "invoice")})
        self.assertIn("a.zip, Rechnung_0815.pdf", wiz.result)
        # button called without a saved wizard (Odoo 20 after a failed save): clear message
        with self.assertRaisesRegex(UserError, "nicht gespeichert"):
            call_kw(self.env["apartment.document.import"], "action_import", [[]], {})
        with self.assertRaisesRegex(UserError, "mindestens eine"):
            self.env["apartment.document.import"].create({}).action_import()
        with self.assertRaisesRegex(UserError, "weder eine ZIP"):
            self._run(b"hello", name="notes.txt")
        # vacuumed wizards take their upload files with them
        upload = self._upload(zip_a, "b.zip")
        self.env["apartment.document.import"].create({"attachment_ids": [(6, 0, upload.ids)]}).unlink()
        self.assertFalse(upload.exists())

    def test_onchange_suggests_type(self):
        doc = self.env["apartment.document"].new({"file_name": "2025-11-04_HKA_2024_WE_7.pdf"})
        doc._onchange_file_name_classify()
        self.assertEqual(doc.document_type, "utility_statement")
        self.assertEqual(doc.year, 2024)
        self.assertEqual(doc.name, "HKA 2024 WE 7")
