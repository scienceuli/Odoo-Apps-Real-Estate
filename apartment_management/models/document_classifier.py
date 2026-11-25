"""Classify apartment documents (PDF) from file name and extracted text.

Pure Python (no Odoo import) so the same rules can be used by the import wizard
and by local helper scripts (e.g. a pre-classification with OCR before uploading).

Main entry points:

* ``classify(filename, text)`` -> dict with document_type, label, title, date, year, keywords
* ``pick_apartment(filename, text, candidates, default_id)`` -> (apartment id, reason)
* ``match_sender(sender, rules)`` -> best matching sender rule
"""
import datetime
import re
import unicodedata

HEAD_CHARS = 2500  # the subject of a letter is always in the first part of the text

MONTHS = {
    "januar": 1, "jan": 1, "februar": 2, "feb": 2, "märz": 3, "maerz": 3, "mär": 3, "april": 4,
    "apr": 4, "mai": 5, "juni": 6, "jun": 6, "juli": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9, "oktober": 10, "okt": 10, "november": 11, "nov": 11,
    "dezember": 12, "dez": 12,
}

# (regex, document_type, label) – first match wins. Checked against the file name first,
# then against the beginning of the text. Order matters: specific before general.
RULES = [
    # --- other: things that mention rent/statements but are none of the types
    (r"verwaltervertrag|verwaltungsvertrag|hausverwaltervollmacht|verwaltervollmacht|hv-vertrag"
     r"|sondereigentumsverwaltung|sondermietverwaltung|vertrag[ _-]*se[ _-]*verwaltung",
     "other", "Verwaltervertrag / Vollmacht"),
    (r"tarif(ü|ue)bersicht", "other", "Tarifübersicht Verwaltung"),
    (r"selbstauskunft|schufa|bonit(ä|ae)ts|vorvermieterbescheinigung|perso(nalausweis)?[ _]"
     r"|ausweis|gehaltsnachweis|lohnschein|verdienst(nachweis|abrechnung)|arbeitsvertrag"
     r"|dienstvertrag|unterlagen[ _]teil",
     "other", "Mieterbewerbung"),
    (r"(ü|ue)bergabeprotokoll|wohnungs(ü|ue)bergabe|abnahmeprotokoll|(ü|ue)bergabe der wohnung",
     "handover", "Übergabeprotokoll"),
    # --- utility statement (tenant side) before annual statement (owner side)
    (r"betriebskostenabrechnung|nebenkostenabrechnung|heiz-? und nebenkostenabrechnung"
     r"|heizkostenabrechnung|(?<![a-z])(hka|bka)(?![a-z])"
     r"|einzelabrechnung energie- und betriebskosten",
     "utility_statement", "Heiz-/Betriebskostenabrechnung"),
    (r"jahresabrechnung|jahresabschluss|hausgeldabrechnung|(?<![a-z])hga(?![a-z])"
     r"|abrechnung[ _-]*20\d\d[ _-]*we",
     "annual_statement", "Jahresabrechnung"),
    (r"wirtschaftsplan|(?<![a-z])wiplan", "annual_statement", "Wirtschaftsplan"),
    (r"hausgeldkonto|kontoauszug hausgeld", "annual_statement", "Hausgeldkonto"),
    # --- owners' meeting
    (r"eigent(ü|ue)merversammlung|(?<![a-z])(ao)?etv(?![a-z])|umlaufbeschl|umlaufverfahren"
     r"|(?<![a-z])einladung(?![a-z])"
     r"|protokollniederschrift|niederschrift der beschl|anwesenheitsliste|beiratsbericht"
     r"|beschluss",
     "owners_meeting", "Eigentümerversammlung / Beschluss"),
    # --- lease
    (r"mietvertrag|wohnraummietvertrag|(?<![a-z])mv(?![a-z])|k(ü|ue)ndigung des mietvertrag"
     r"|mieterh(ö|oe)hung|mietb(ü|ue)rgschaft|mietverh(ä|ae)ltnis",
     "lease", "Mietvertrag"),
    # --- tax
    (r"grundsteuer|finanzamt|steuerbescheid|grundbesitzabgaben|stadtkasse",
     "tax", "Steuer / Abgaben"),
    # --- invoice (only explicit invoices; offers are "other")
    (r"(?<![a-z])rechnung(?![a-z])|rechnungs-?nr|rechnungsnummer|(?<![a-z])re[ _-]?\d|re-nr"
     r"|hiermit berechnen wir|provisionsabrechnung|(?<![a-z])r\d{5}(?![0-9])",
     "invoice", "Rechnung"),
    # --- other, but with a useful label
    (r"angebots?-?n(umme)?r|kostenvoranschlag|kostenangebot|(?<![a-z])ag\d{6,}"
     r"|(?<![a-z])angebot(?= (nr|für|vom|zur|über))|^\W*angebot",
     "other", "Angebot"),
    ("@name", r"angebot", "other", "Angebot"),
    (r"sonderumlage|umlageberechnung", "other", "Sonderumlage"),
    (r"multiparking|duplexparker", "other", "Information Duplexparker"),
    (r"sanierungsfahrplan|(?<![a-z])isfp(?![a-z])", "other", "Sanierungsfahrplan"),
    (r"m(ü|ue)llentsorgung", "other", "Information an Eigentümer"),
    (r"legionellen|trinkwasser|beprobung", "other", "Information Trinkwasser / Legionellen"),
    (r"jahresablesung|ablesung|rauchwarnmelder", "other", "Information Ablesung / Rauchwarnmelder"),
    (r"begr(ü|ue)(ß|ss)ungsschreiben", "other", "Begrüßungsschreiben"),
    (r"kamerabefahrung|haltungs-? ?statistik|abwasser", "other", "Gutachten Abwasser"),
    (r"mahnung", "other", "Mahnung"),
    (r"informationsschreiben|information an alle", "other", "Information an Eigentümer"),
    (r"sepa-?(basis)?-?lastschrift|lastschriftmandat|sepa-ls|(ä|ae)nderung der bankverbindung",
     "other", "SEPA / Bankverbindung"),
    (r"deutsche kreditbank|preis- und leistungsverzeichnis", "other", "Bank"),
]
# a rule may start with "@name": it is only applied to the file name (too vague for text)
_RULES = [
    (re.compile(r[-3], re.I), r[-2], r[-1], r[0] == "@name") for r in RULES
]

KEYWORDS = [
    "Jahresabrechnung", "Jahresabschluss", "Hausgeldabrechnung", "Wirtschaftsplan", "Hausgeld",
    "Heizkosten", "Betriebskosten", "Nebenkosten", "Sonderumlage", "Rücklage",
    "Eigentümerversammlung", "Umlaufbeschluss", "Beschluss", "Protokoll", "Einladung",
    "Beiratsbericht", "Mietvertrag", "Kündigung", "Mieterhöhung", "Mietbürgschaft",
    "Selbstauskunft", "Kaution", "Übergabe", "Rechnung", "Angebot", "Provision", "Grundsteuer",
    "Finanzamt", "Mahnung", "Verwaltervertrag", "Vollmacht", "SEPA", "Legionellen",
    "Rauchwarnmelder", "Ablesung", "Dachfenster", "Fenster", "Warmwasserspeicher", "Heizung",
    "Abwasser", "Graffiti", "Instandhaltung", "Reparatur",
]
_KEYWORDS = [
    (kw, re.compile(r"(?<![a-zäöüß])" + re.escape(kw.lower()), re.I)) for kw in KEYWORDS
]

STATEMENT_YEAR = re.compile(
    r"(?:jahresabrechnung|jahresabschluss|hausgeldabrechnung|heizkostenabrechnung"
    r"|betriebskostenabrechnung|nebenkostenabrechnung|wirtschaftsplan|wiplan|hga|hka|bka"
    r"|abrechnung|wirtschaftsjahr)[\s_-]*(?:ab\s*01\.01\.)?((?:19|20)\d\d)(?!\d)",
    re.I,
)
PERIOD_YEAR = re.compile(
    r"zeitraum:?\s*(?:vom\s*)?01\.01\.((?:19|20)\d\d)\s*(?:-|bis)\s*31\.12\.\1", re.I
)

# file names that carry no information (scanner names, numbers, internal ids)
MEANINGLESS_NAME = re.compile(
    r"^(scan|doc\d|mx-\d|sl\d|img|image|dokument|unbenannt|abr\.\d|r\d+$|ag\d+$|\d[\d_ .-]*$"
    r"|\d{6}_[a-z]{3}_)",
    re.I,
)


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()


def _valid(y, m, d):
    try:
        date = datetime.date(int(y), int(m), int(d))
    except ValueError:
        return None
    if not 1990 <= date.year <= datetime.date.today().year + 1:
        return None
    return date


def date_from_filename(filename):
    base = filename.rsplit("/", 1)[-1]
    for rx, order in (
        (r"((?:19|20)\d\d)-(\d\d)-(\d\d)", "ymd"),
        (r"(?<!\d)(\d\d)\.(\d\d)\.((?:19|20)\d\d)(?!\d)", "dmy"),
        (r"(?<!\d)((?:19|20)\d\d)(\d\d)(\d\d)(?=_|\d{6}|\.|$)", "ymd"),
        (r"-(\d\d)(\d\d)((?:19|20)\d\d)\d{6}", "dmy"),
        (r"^(\d\d)(\d\d)(\d\d)_", "yymmdd"),
    ):
        m = re.search(rx, base)
        if not m:
            continue
        a, b, c = m.groups()
        if order == "ymd":
            date = _valid(a, b, c)
        elif order == "dmy":
            date = _valid(c, b, a)
        else:
            date = _valid("20" + a, b, c)
        if date:
            return date
    return None


_NUM_DATE = r"(\d{1,2})\.\s?(\d{1,2})\.\s?((?:19|20)\d\d)(?!\d)"
_WORD_DATE = r"(\d{1,2})\.?\s+([A-Za-zäÄ]{3,9})\.?\s+((?:19|20)\d\d)(?!\d)"


def date_from_text(text):
    """Date of a letter: 'Datum: …', 'Ort, den …' or 'Ort, 8. Dezember 2022'; else first date."""
    head = clean_text(text)[:HEAD_CHARS]
    candidates = [
        r"datum\s*:?\s*" + _NUM_DATE,
        r"[A-Za-zÄÖÜäöüß)]{3,},?\s+(?:den\s*)?" + _NUM_DATE,
        r"[A-Za-zÄÖÜäöüß)]{3,},\s*(?:den\s*)?" + _WORD_DATE,
    ]
    found = []  # the earliest letter date wins (letter head before meeting dates etc.)
    for rx in candidates:
        for m in re.finditer(rx, head, re.I):
            d, mo, y = m.groups()
            if not mo.isdigit():
                mo = MONTHS.get(mo.lower().rstrip("."))
                if not mo:
                    continue
            if _skip_date(head, m.start(1)):
                continue
            date = _valid(y, mo, d)
            if date:
                found.append((m.start(), date))
                break
    if found:
        return min(found)[1]
    for m in re.finditer(_NUM_DATE, head):
        if _skip_date(head, m.start()):
            continue
        d, mo, y = m.groups()
        date = _valid(y, mo, d)
        if date:
            return date
    return None


def _skip_date(head, pos):
    """Dates that are not the date of the document (periods, birth dates, entry dates)."""
    before = head[max(0, pos - 16):pos].lower()
    return bool(re.search(
        r"zeitraum|einzug|eintritt|geb\.|geb |geboren|geburts|gültig"
        r"|(von|vom|bis|ab|seit)\s*:?\s*$",
        before))


def statement_year(filename, text):
    for source in (filename, clean_text(text)[:HEAD_CHARS]):
        m = STATEMENT_YEAR.search(source.replace("_", " "))
        if m and _valid(m.group(1), 1, 1):
            return int(m.group(1))
        m = PERIOD_YEAR.search(source)
        if m:
            return int(m.group(1))
    return None


def classify_type(filename, text):
    """Return (document_type, label) or ('other', '') when nothing matches.

    The file name is checked first (rule order decides). In the text, the rule whose
    term appears first wins: the subject of a letter comes before its body.
    """
    base = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0].replace("_", " ")
    for rx, dtype, label, _name_only in _RULES:
        if rx.search(base):
            return dtype, label
    head = clean_text(text)[:HEAD_CHARS]
    best = None
    for prio, (rx, dtype, label, name_only) in enumerate(_RULES):
        if name_only:
            continue
        m = rx.search(head)
        if m and (best is None or (m.start(), prio) < best[0]):
            best = ((m.start(), prio), dtype, label)
    if best:
        return best[1], best[2]
    return "other", ""


def keywords(filename, text, limit=8):
    source = filename.replace("_", " ") + " " + clean_text(text)[:HEAD_CHARS * 2]
    found = []
    for kw, rx in _KEYWORDS:
        if rx.search(source) and kw not in found:
            found.append(kw)
        if len(found) >= limit:
            break
    return found


def title_from_filename(filename):
    """Readable title from the file name, or '' if the name is meaningless."""
    base = filename.rsplit("/", 1)[-1]
    base = re.sub(r"\.pdf$", "", base, flags=re.I)
    base = re.sub(r"\._?pdf$", "", base, flags=re.I)
    if MEANINGLESS_NAME.search(base):
        return ""
    base = re.sub(r"(?<!WE)(?<!WHG)(?<!Nr)_\d{1,2}$", "", base, flags=re.I)  # duplicate suffix _1
    base = re.sub(r"^(?:\d{4}-\d\d-\d\d_)+", "", base)          # date prefix (kept in 'date')
    base = re.sub(r"_\d{8}_\d{4,}$|_\d{8}_\d+_\d+$", "", base)  # scanner time stamps
    base = base.replace("__", " – ").replace("_", " ").replace("+", " + ")
    base = re.sub(r"\s+", " ", base).strip(" -–.")
    if len(re.sub(r"[^A-Za-zÄÖÜäöüß]", "", base)) < 4:
        return ""
    return base


def classify(filename, text=""):
    text = text or ""
    dtype, label = classify_type(filename, text)
    date = date_from_filename(filename) or date_from_text(text)
    year = None
    if dtype in ("annual_statement", "utility_statement"):
        year = statement_year(filename, text)
    year = year or (date.year if date else None)
    title = title_from_filename(filename)
    if not title:
        title = label or "Dokument"
        if dtype in ("annual_statement", "utility_statement") and year:
            title = f"{title} {year}"
        elif date:
            title = f"{title} {date.strftime('%d.%m.%Y')}"
    return {
        "document_type": dtype,
        "label": label,
        "title": title[:200],
        "date": date,
        "year": year,
        "keywords": keywords(filename, text),
    }


# ------------------------------------------------------------------ apartments

# Sender (domain of the e-mail address) -> apartment, as used for the first import of the
# mail attachments (prompt 04). Apartments are looked up by name; Zur Mühle I/II share a
# building, so their senders may deliver documents for both units.
DEFAULT_SENDERS = [
    ("santo-immobilien", "Herbartstraße", []),
    ("heye-hausverwaltung", "Herbartstraße", []),
    ("gvs-immoservice", "Hasenwinkel", []),
    ("hendlmaier", "Brunnenstraße", []),
    ("kaprimo", "Brunnenstraße", []),
    ("mattern-immobilien", "Brunnenstraße", []),
    ("schwerdtner-immobilien", "Zur Mühle I", ["Zur Mühle II"]),
    ("viveto-verwaltung", "Zur Mühle I", ["Zur Mühle II"]),
    ("steffen-immobilien", "Zur Mühle I", ["Zur Mühle II"]),
    ("t-online", "Zur Mühle I", ["Zur Mühle II"]),
]

# Recognition terms for apartments sharing a building (only filled in when empty).
DEFAULT_TERMS = {
    "Zur Mühle I": "WE 4, WE4, WHG 4, Whg. 4, Wohnung 4, 20005 400, 0072 00061 04, 1. OG links",
    "Zur Mühle II": "WE 6, WE6, WHG 6, Whg. 6, Wohnung 6, 20005 600, 0072 00061 06, DG links, Bock",
}



def _term_regex(term):
    tokens = re.findall(r"[0-9]+|[^\W\d_]+", term.lower())
    if not tokens:
        return None
    body = r"[\s_./-]*".join(re.escape(t) for t in tokens)
    end = r"(?![0-9])" if tokens[-1].isdigit() else r"(?![^\W\d_])"
    start = r"(?<![0-9])" if tokens[0].isdigit() else r"(?<![^\W\d_])"
    return re.compile(start + body + end, re.I)


def split_terms(terms):
    return [t.strip() for t in re.split(r"[,;\n]", terms or "") if t.strip()]


def term_hits(source, terms):
    hits = []
    for term in split_terms(terms):
        rx = _term_regex(term)
        if rx and rx.search(source):
            hits.append(term)
    return hits


def pick_apartment(filename, text, candidates, default_id=None):
    """Choose one apartment among several of the same sender (e.g. Zur Mühle I/II).

    ``candidates``: list of dicts {id, name, terms}. An apartment wins when only its
    recognition terms appear in the file name or the head of the text. Otherwise the
    default apartment is kept (a document for both units, or no unit mentioned).
    """
    if not candidates:
        return default_id, ""
    if len(candidates) == 1:
        return candidates[0]["id"], ""
    source = filename.replace("_", " ") + " " + clean_text(text)[:1500]
    scored = []
    for cand in candidates:
        hits = term_hits(source, cand.get("terms") or "")
        if hits:
            scored.append((cand, hits))
    if len(scored) == 1:
        cand, hits = scored[0]
        return cand["id"], "Merkmal: " + ", ".join(hits)
    if len(scored) > 1:
        return default_id, "mehrere Wohnungen erwähnt (" + ", ".join(
            c["name"] for c, _h in scored) + ") – Standard"
    return default_id, ""


def match_sender(sender, rules):
    """Best rule for a sender address/folder name: the longest matching pattern wins.

    ``rules``: iterable of objects/dicts with a 'pattern' (e-mail, domain or part of it).
    """
    sender = (sender or "").lower()
    best, best_len = None, -1
    for rule in rules:
        pattern = (rule["pattern"] if isinstance(rule, dict) else rule.pattern or "").lower().strip()
        if pattern and pattern in sender and len(pattern) > best_len:
            best, best_len = rule, len(pattern)
    return best


def sender_from_path(path):
    """Sender folder of a path inside a ZIP: the nearest folder that looks like an e-mail/domain."""
    parts = [p for p in path.replace("\\", "/").split("/")[:-1] if p]
    for part in reversed(parts):
        if "@" in part or re.search(r"\.[a-z]{2,}$", part, re.I):
            return part
    return parts[-1] if parts else ""
