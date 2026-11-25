===================
Wohnungsverwaltung
===================

Einfache Verwaltung einzelner vermieteter Wohnungen in Odoo 20.

* Wohnung mit Adresse, Fläche, Zimmern, Status (vermietet / Leerstand / Eigennutzung)
* Hausverwaltung, Mieterverwaltung und Mieter als normale Kontakte
* Kaltmiete, Nebenkosten-Vorauszahlung, sonstige Einnahmen, Kaution
* Kostenpositionen (Hausgeld, Grundsteuer, Finanzamt, Versicherung, Kredit ...)
  mit Intervall; Umrechnung auf Monat/Jahr und Überschussberechnung
* PDF-Dokumente (Mietverträge, Jahresabrechnungen, Steuerbescheide ...) mit
  Dokumentart, Jahr und PDF-Vorschau
* Smart-Button "Wohnungen" auf dem Kontakt
* Chatter mit Änderungsverfolgung und Aktivitäten
Buchhaltung (Odoo Community, Modul ``account``)
-----------------------------------------------

Die Bankumsätze liegen in der Odoo-Buchhaltung (Bankjournal); die Wohnungen sind
**Kostenstellen** (analytischer Plan „Wohnungen“). Die Kostenübersicht einer Wohnung ist
eine Auswertung der gebuchten Beträge ihrer Kostenstelle.

* *Konfiguration › Buchhaltung einrichten* (einmalig, wiederholbar) im eigenen
  Unternehmen für die Vermietung: Kontenplan des Landes, schlanke eigene Konten
  (Mieteinnahmen, Hausgeld, Sonderumlage, Hausgeldabrechnung, Grundsteuer/Abgaben,
  Versicherungen, Instandhaltung/sonstige Kosten, Schuldzinsen, Bankgebühren,
  Privateinlagen, Anfangssaldo), je Konto eine Kategorie, je Wohnung eine Kostenstelle,
  je Haus ein Darlehenskonto (Zur Mühle I/II teilen sich eines), Bankjournal mit IBAN,
  Anfangssaldo. Der Benutzer erhält „Volle Buchhaltungsfunktionen“ und „Kostenstellen“.
* *Buchhaltung › Import*: DKB-Umsatzliste (CSV) oder Jahresdatei ``umsaetze_JJJJ.xlsx``
  (Spalte „Zuordnung“). Jeder Umsatz wird eine Bankauszugszeile:

  - Kategorie aus dem ersten Wort der Zuordnung, Wohnung aus dem Wohnungsnamen;
    Zur Mühle I/II über die Wohnungs-Nr. („WHG 4“) im Verwendungszweck;
    Ortskürzel („Steuer H“) über das Feld Ort
  - Darlehen und Grundsteuer ohne Wohnungs-Nr. werden gleichmäßig auf die Wohnungen
    desselben Hauses verteilt (nie über verschiedene Häuser)
  - fehlt etwas: Zuordnungsregeln, danach frühere Umsätze mit gleicher IBAN und
    ähnlichem Verwendungszweck
  - vollständig zugeordnet → sofort gebucht (Konto der Kategorie, Kostenstelle(n));
    sonst bleibt der Umsatz auf dem Zwischenkonto unter *Buchhaltung › Zu klären*
  - Darlehensraten gehen auf das Darlehenskonto; steht die Aufteilung im Text
    („Tilg.: 606,22 Zins/Entg: 350,85“, Hasenwinkel), wird direkt in Tilgung und
    Schuldzinsen getrennt
  - Dubletten werden erkannt (CSV und xlsx desselben Zeitraums); gebuchte Umsätze
    werden nie überschrieben
* Kategorie/Wohnungen in der Liste oder im Formular ändern bucht den Umsatz sofort um;
  Kategorie leeren → zurück auf das Zwischenkonto. „Regeln anwenden“ für markierte
  offene Umsätze.
* *Buchhaltung › Jahreszinsen buchen*: Zinsen laut Zinsbescheinigung der Bank
  (Schuldzinsen an Darlehen) – für Darlehen ohne Aufteilung im Verwendungszweck.
* Auswertungen: *Kostenübersicht je Wohnung* (Pivot Wohnung × Konto × Jahr, Export
  nach Excel – für die Steuerberatung), *Übersicht nach Konten* (alle Konten inkl.
  allgemeiner Posten), Smart-Buttons und Ist-Werte des laufenden Jahres auf der Wohnung
  (Einnahmen, Kosten, Überschuss, Tilgung). Die Kostenpositionen der Wohnung bleiben
  als Plan.
* Investment und Bankkosten sind allgemein (ohne Kostenstelle).

Dokumente importieren
---------------------

* *Konfiguration › Dokument-Absender*: Absender (E-Mail, Domain oder Teil davon) → Wohnung.
  „Standard-Zuordnungen anlegen“ legt die bekannten Hausverwaltungen an (santo, heye →
  Herbartstraße; gvs → Hasenwinkel; hendlmaier, kaprimo, mattern → Brunnenstraße;
  schwerdtner, viveto, steffen, t-online → Zur Mühle I mit Zur Mühle II als weiterer Wohnung).
* *Erkennungsmerkmale Dokumente* an der Wohnung (z. B. „WE 6, DG links, Bock“): betreut ein
  Absender mehrere Wohnungen, geht ein Dokument an die Wohnung, deren Merkmale allein im
  Dateinamen bzw. Textanfang vorkommen; sonst an die Standard-Wohnung der Regel.
* *Dokumente › Import*: ZIP mit Ordnern je Absender (``<absender>/<datei>.pdf``) oder ein
  einzelnes PDF (Absender bzw. Ersatz-Wohnung im Assistenten angeben). Dokumentart, Titel,
  Datum, Jahr (bei Abrechnungen das Abrechnungsjahr) und Schlagworte werden aus Dateiname und
  PDF-Text bestimmt (``models/document_classifier.py``); nicht Erkennbares wird „Sonstiges“.
  Eine ``manifest.csv`` (``;``, Spalten ``file;apartment;document_type;title;date;year;keywords;sender``)
  in der ZIP hat Vorrang – so kann eine Vorab-Klassifizierung mit OCR (gescannte PDFs haben
  keinen Text) mitgegeben werden. Gleiche Dateien (SHA-1) werden nicht doppelt angelegt.
* Beim manuellen Hochladen schlägt der Dateiname Titel, Dokumentart und Jahr vor.
