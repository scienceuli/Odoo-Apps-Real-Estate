"""Pure-Python helpers to read DKB bank exports (no Odoo imports).

Supported inputs
----------------
* the yearly workbooks ``umsaetze_YYYY.xlsx`` (DKB columns + ``Zuordnung`` + ``Kommentar``)
* the raw DKB CSV export ``*_Umsatzliste_Girokonto_*.csv`` (``;``-separated, with or
  without the ``Status`` column)

Columns are located by their header text, so the column order does not matter.
"""
import csv
import hashlib
import io
import re
from datetime import date, datetime

HEADER_MAP = {
    "buchungsdatum": "date",
    "wertstellung": "value_date",
    "status": "status",
    "zahlungspflichtige*r": "payer",
    "zahlungsempfänger*in": "payee",
    "verwendungszweck": "purpose",
    "umsatztyp": "booking_type",
    "iban": "iban",
    "betrag (€)": "amount",
    "gläubiger-id": "creditor_ref",
    "mandatsreferenz": "mandate_ref",
    "kundenreferenz": "customer_ref",
    "zuordnung": "assignment",
    "kommentar": "comment",
}

# category codes (apartment.booking.category.code), detected from the first word(s) of "Zuordnung"
CATEGORY_KEYWORDS = [
    ("mietnomaden", "insurance"),
    ("versicherung", "insurance"),
    ("sonderumlage", "special_assessment"),
    ("hausgeld", "house_fee"),
    ("abrechnung", "settlement"),
    ("miete", "rent"),
    ("darlehen", "loan"),
    ("steuer", "tax"),
    ("kosten", "other_cost"),
    ("investment", "investment"),
    ("investition", "investment"),
    ("bankkosten", "bank_fee"),
    ("zins", "interest"),
]
GENERAL_CATEGORIES = ("investment", "bank_fee", "opening")
# categories whose bookings may be split equally between apartments of the same building
# (shared loan, property tax of a building) when the text does not name a unit
SPLIT_CATEGORIES = ("loan", "tax", "interest")


class BankFileError(ValueError):
    pass


# ---------------------------------------------------------------- normalising
def normalize(text):
    text = (text or "").lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(a, b)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def purpose_key(purpose):
    """Purpose without the parts that change every month (dates, years, amounts)."""
    s = (purpose or "").lower()
    s = re.sub(r"\d{1,2}\.\d{1,2}\.\d{2,4}", " ", s)
    s = re.sub(r"\b\d{2}[-/.]20\d{2}\b", " ", s)
    s = re.sub(r"\b20\d{2}\b", " ", s)
    s = re.sub(r"\d{1,3}(\.\d{3})*,\d{2}", " ", s)
    s = re.sub(r"saldo:.*$", " ", s)
    s = re.sub(r"\bxc\d+\b", " ", s)  # Kaprimo transaction ids
    return normalize(s)


def parse_amount(value):
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value or "").strip().replace("€", "").replace(" ", "")
    if not s:
        return 0.0
    # German format: "1.309,50", "-1.000", "696,5"
    s = s.replace(".", "").replace(",", ".")
    return float(s)


def parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value or "").strip()
    for fmt in ("%d.%m.%y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise BankFileError(f"Unbekanntes Datumsformat: {s!r}")


# ---------------------------------------------------------------- reading
def _table_from_xlsx(content):
    import openpyxl  # shipped with Odoo

    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.worksheets[0]  # first sheet holds the bookings
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _table_from_csv(content):
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover
        raise BankFileError("CSV-Datei konnte nicht gelesen werden (Zeichensatz).")
    return list(csv.reader(io.StringIO(text), delimiter=";"))


def read_table(content, filename=""):
    if content[:2] == b"PK" or filename.lower().endswith((".xlsx", ".xlsm")):
        return _table_from_xlsx(content)
    return _table_from_csv(content)


def parse_bank_file(content, filename=""):
    """Return a list of booking dicts (see HEADER_MAP values)."""
    table = read_table(content, filename)
    header_idx = None
    for i, row in enumerate(table[:30]):
        if row and str(row[0] or "").strip().lower() == "buchungsdatum":
            header_idx = i
            break
    if header_idx is None:
        raise BankFileError("Keine Kopfzeile mit 'Buchungsdatum' gefunden – ist das ein DKB-Export?")
    cols = {}
    for j, title in enumerate(table[header_idx]):
        key = HEADER_MAP.get(str(title or "").strip().lower())
        if key:
            cols[key] = j
    missing = {"date", "amount", "purpose"} - cols.keys()
    if missing:
        raise BankFileError(f"Spalten fehlen: {', '.join(sorted(missing))}")

    rows, seen = [], {}
    for line_no, raw in enumerate(table[header_idx + 1:], start=header_idx + 2):
        if not raw or raw[cols["date"]] in (None, ""):
            continue

        def get(key):
            j = cols.get(key)
            v = raw[j] if j is not None and j < len(raw) else None
            return "" if v is None else (v if isinstance(v, (int, float, date)) else str(v).strip())

        amount = parse_amount(get("amount"))
        payer, payee, purpose = get("payer"), get("payee"), get("purpose")
        # DKB quarterly account statements carry 0,00 € and no information
        if amount == 0 and payer == "DKB AG" and purpose.startswith("Abrechnung"):
            continue
        iban = str(get("iban"))
        if iban in ("0", "0000000000", "1060502638"):
            iban = ""
        booking_date = parse_date(get("date"))
        value_date = get("value_date")
        row = {
            "line_no": line_no,
            "date": booking_date,
            "value_date": parse_date(value_date) if value_date else False,
            "payer": payer,
            "payee": payee,
            "purpose": purpose,
            "booking_type": get("booking_type"),
            "iban": iban,
            "amount": amount,
            "creditor_ref": str(get("creditor_ref")),
            "mandate_ref": str(get("mandate_ref")),
            "customer_ref": str(get("customer_ref")),
            "assignment": str(get("assignment")),
            "comment": str(get("comment")),
        }
        base = "|".join(str(x) for x in (booking_date, f"{amount:.2f}", iban, payer, payee, " ".join(purpose.split())))
        digest = hashlib.sha1(base.encode()).hexdigest()
        seen[digest] = seen.get(digest, 0) + 1  # identical bookings in one file stay distinct
        row["import_key"] = f"{digest}-{seen[digest]}"
        rows.append(row)
    return rows


# ---------------------------------------------------------------- classification
def category_from_assignment(assignment):
    text = normalize(assignment)
    if not text or text.startswith("noch zu klaeren"):
        return None
    for keyword, category in CATEGORY_KEYWORDS:
        if text.split()[0].startswith(keyword):
            return category
    for keyword, category in CATEGORY_KEYWORDS:
        if keyword in text:
            return category
    return None


def _base_name(name):
    """'Zur Mühle II' -> 'zur muehle'; 'Brunnenstraße' -> 'brunnenstrasse'."""
    return re.sub(r"\s+(i{1,3}|iv|v|\d+)$", "", normalize(name))


def _unit_digits(value):
    m = re.search(r"\d+", value or "")
    return m.group(0) if m else None


UNIT_RE = re.compile(r"\b(?:whg|we|wohnung|weinheit)\s*(\d+)\b")


def resolve_apartments(assignment, purpose, apartments, allow_split=False):
    """Find the apartment(s) for a booking.

    ``apartments`` is a list of dicts with keys id, name, unit_number, city.
    Returns ``(list of apartment ids, reason)``. Several ids are only returned with
    ``allow_split`` and only if all candidates belong to the same building (same base
    name, e.g. "Zur Mühle I" and "Zur Mühle II"); the booking is then split equally.
    """
    text = normalize(assignment)
    if not text:
        return [], ""
    candidates = []
    for apt in apartments:
        base = _base_name(apt["name"])
        if not base:
            continue
        # tolerate typos at the end ("Brunnenstraßw", "Brunnenstrasse")
        if base in text or (len(base) > 8 and base[:-2] in text):
            candidates.append(apt)
    how = "Zuordnung"
    if not candidates:
        # single city letter ("Steuer H") -> apartments in that city
        letters = [t for t in (assignment or "").split() if len(t) == 1 and t.isupper()]
        if not letters:
            return [], "keine Wohnung im Text der Zuordnung"
        candidates = [a for a in apartments if (a.get("city") or "")[:1].upper() == letters[0]]
        how = f"Ort '{letters[0]}'"
        if not candidates:
            return [], f"keine Wohnung im Ort '{letters[0]}'"
    if len(candidates) == 1:
        return [candidates[0]["id"]], f"{how} -> {candidates[0]['name']}"
    # several apartments in the same building (Zur Mühle I / II): use WHG/WE number
    found = set(UNIT_RE.findall(normalize(purpose) + " " + text))
    by_unit = [a for a in candidates if _unit_digits(a.get("unit_number")) in found]
    if len(by_unit) == 1:
        return [by_unit[0]["id"]], f"Wohnungs-Nr. {by_unit[0].get('unit_number')} -> {by_unit[0]['name']}"
    names = ", ".join(a["name"] for a in candidates)
    if allow_split and len({_base_name(a["name"]) for a in candidates}) == 1:
        return [a["id"] for a in candidates], f"{how} -> aufgeteilt auf {names}"
    return [], f"{how}: mehrere Wohnungen möglich: {names}"


def split_amounts(amount, n):
    """Split ``amount`` in ``n`` parts (2 decimals) that add up exactly."""
    cents = round(amount * 100)
    base, rest = divmod(abs(cents), n)
    sign = -1 if cents < 0 else 1
    return [sign * (base + (1 if i < rest else 0)) / 100 for i in range(n)]


def split_percent(n):
    """Equal percentages for ``n`` apartments that add up to exactly 100."""
    return split_amounts(100.0, n)


_TILG_RE = re.compile(r"tilg\.?:?\s*(\d{1,3}(?:\.\d{3})*,\d{2})", re.I)
_ZINS_RE = re.compile(r"zins(?:/entg)?\.?:?\s*(\d{1,3}(?:\.\d{3})*,\d{2})", re.I)


def loan_parts(purpose, amount):
    """Principal and interest of a loan instalment if the bank text states them.

    ``...Saldo: 175.384,74 Tilg.: 606,22 Zins/Entg: 350,85`` with amount -957,07 returns
    ``(606.22, 350.85)``; ``None`` if the text has no (consistent) split.
    """
    tilg, zins = _TILG_RE.search(purpose or ""), _ZINS_RE.search(purpose or "")
    if not (tilg and zins):
        return None
    t, z = parse_amount(tilg.group(1)), parse_amount(zins.group(1))
    if abs(t + z - abs(amount)) >= 0.005:
        return None
    return t, z


def similarity(a, b):
    """0..1 similarity of two purpose keys."""
    from difflib import SequenceMatcher

    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def apartment_by_unit(purpose, apartments, like_name):
    """Apartment with the same base name as ``like_name`` whose unit number is in ``purpose``."""
    base = _base_name(like_name)
    found = set(UNIT_RE.findall(normalize(purpose)))
    hits = [a for a in apartments if _base_name(a["name"]) == base and _unit_digits(a.get("unit_number")) in found]
    return hits[0]["id"] if len(hits) == 1 else None
