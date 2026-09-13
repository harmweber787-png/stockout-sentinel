"""Adapter: CSV-Streams beliebiger ERP-Systeme -> ``SKUInput``-Objekte.

Der Import ist vollstaendig generisch und arbeitet ausschliesslich im RAM:
Es wird nichts auf die Platte geschrieben und es existieren keine
hinterlegten Beispiel- oder Stammdaten. Erkannt werden

* Trennzeichen ``;`` ``,`` Tab ``|`` (Sniffing ueber die Kopfzeile),
* die in CH/DE/FR gaengigen Zeichensaetze (UTF-8/BOM, CP1252, Latin-1),
* Schweizer und deutsche Zahlenformate (``1'234.50``, ``1.234,50``),
* zwei Tabellen-Layouts:

  - **Langformat**  - eine Zeile je Artikel und Periode
    (Spalten Artikel / Datum / Menge, dazu Bestand und Lieferzeit),
  - **Breitformat** - eine Zeile je Artikel, die Perioden stehen als
    Datumsspalten nebeneinander (typisch fuer Excel-Auswertungen).

Spaltennamen werden ueber Synonymlisten zugeordnet ("Art-Nr", "SKU",
"Bestand", "Lager", "Vorlaufzeit", ...). Die Zuordnung ist gegen Gross-/
Kleinschreibung, Trennzeichen und Umlaute unempfindlich.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Mapping, Sequence

import pandas as pd

from src.domain.timeseries import parse_datum
from src.schemas import ConsumptionRecord, IngestWarnung, SKUInput

__all__ = ["CSVIngestFehler", "IngestErgebnis", "lese_csv", "normalisiere_spalte"]

#: Reihenfolge bestimmt die Prioritaet bei der Zuordnung.
_SYNONYME: dict[str, tuple[str, ...]] = {
    "sku": (
        "artnr", "artikelnr", "artikelnummer", "artikelno", "sku", "artikel",
        "material", "materialnr", "materialnummer", "matnr", "teilenr",
        "teilenummer", "itemno", "itemnumber", "item", "produktnr", "produkt",
        "artikelbezeichnung", "bezeichnung", "artikelcode", "nummer", "nr",
    ),
    "datum": (
        "datum", "date", "periode", "period", "monat", "month", "zeitraum",
        "buchungsdatum", "belegdatum", "jahrmonat", "woche", "kalenderwoche",
        "kw", "jahr",
    ),
    "bestand": (
        "bestand", "lagerbestand", "istbestand", "aktuellerbestand",
        "bestandaktuell", "lagermenge", "lagerbestandaktuell", "verfuegbar",
        "verfuegbarerbestand", "freierbestand", "lager", "stock", "onhand",
        "stockonhand", "inventory", "vorrat",
    ),
    "lieferzeit": (
        "lieferzeit", "vorlaufzeit", "wiederbeschaffungszeit", "wbz",
        "beschaffungszeit", "leadtime", "lieferfrist", "liefertage",
        "wiederbeschaffung", "durchlaufzeit", "dlz",
    ),
    "mindestbestellmenge": (
        "mindestbestellmenge", "mindestmenge", "minbestellmenge", "moq",
        "minimumorderquantity", "mindestabnahme", "losgroesse", "bestelllos",
        "verpackungseinheit",
    ),
    "menge": (
        "verbrauch", "verbrauchsmenge", "absatz", "absatzmenge", "bedarf",
        "abgang", "entnahme", "ausgang", "verkauf", "verkaufsmenge",
        "consumption", "demand", "usage", "quantity", "qty", "menge",
        "umsatzmenge", "stueck",
    ),
}

#: Felder, die im Breitformat nicht als Periodenspalte gelten duerfen.
_STAMMFELDER = ("sku", "bestand", "lieferzeit", "mindestbestellmenge")

#: Substring-Treffer erst ab dieser Laenge zulassen (verhindert, dass "nr"
#: oder "kw" beliebige Spalten einfaengt).
_MIN_SUBSTRING_LAENGE = 5

_UMLAUTE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


class CSVIngestFehler(ValueError):
    """Der CSV-Stream laesst sich nicht als Dispositionsliste interpretieren."""


@dataclass(slots=True)
class IngestErgebnis:
    """Ergebnis des CSV-Imports.

    Attributes:
        artikel: Erfolgreich gelesene Artikel.
        spalten_mapping: Zielfeld -> tatsaechliche Spalte des Quellsystems.
        layout: ``"lang"`` oder ``"breit"``.
        warnungen: Nicht-fatale Hinweise zu uebersprungenen Zeilen.
    """

    artikel: list[SKUInput]
    spalten_mapping: dict[str, str]
    layout: str
    warnungen: list[IngestWarnung] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Normalisierung
# ---------------------------------------------------------------------------
def normalisiere_spalte(name: object) -> str:
    """Reduziert einen Spaltenkopf auf seinen Vergleichskern.

    ``"Art.-Nr."`` -> ``"artnr"``, ``"Lagerbestand (Stk)"`` -> ``"lagerbestandstk"``.
    """
    text = str(name).strip().lower().translate(_UMLAUTE)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(zeichen for zeichen in text if not unicodedata.combining(zeichen))
    return re.sub(r"[^a-z0-9]+", "", text)


def zu_float(wert: object) -> float | None:
    """Parst Zahlen aus internationalen ERP-Exporten.

    Erkennt Schweizer Apostroph-Tausender (``1'234.50``), deutsche
    Dezimalkommata (``1.234,50``), Leerzeichen-Tausender und in Klammern
    gesetzte Negativwerte. Gibt ``None`` zurueck, wenn keine Zahl erkennbar ist.
    """
    if wert is None:
        return None
    if isinstance(wert, (int, float)) and not isinstance(wert, bool):
        zahl = float(wert)
        return None if pd.isna(zahl) else zahl

    text = str(wert).strip()
    if not text or text.lower() in {"nan", "none", "null", "-", "n/a", "na"}:
        return None

    negativ = text.startswith("(") and text.endswith(")")
    if negativ:
        text = text[1:-1]

    # Waehrungs-/Einheitenzeichen und Tausender-Trenner entfernen.
    # Waehrungs-, Einheiten- und Tausenderzeichen entfernen; der
    # Zeichenklassen-Filter deckt Apostroph, Leer- und Schmalleerzeichen mit ab.
    text = re.sub(r"[^\d,.\-+]", "", text)
    # Schweizer Schreibweise "1'000.-" und nachgestellte Vorzeichen bereinigen.
    text = re.sub(r"[-+]+$", "", text)
    if not any(zeichen.isdigit() for zeichen in text):
        return None

    if "," in text and "." in text:
        # Das weiter rechts stehende Zeichen ist das Dezimaltrennzeichen.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        # Einzelnes Komma: Dezimaltrenner, ausser es trennt 3er-Gruppen.
        if re.fullmatch(r"-?\d{1,3}(,\d{3})+", text):
            text = text.replace(",", "")
        else:
            text = text.replace(",", ".")

    try:
        zahl = float(text)
    except ValueError:
        return None
    return -zahl if negativ else zahl


# ---------------------------------------------------------------------------
# Einlesen
# ---------------------------------------------------------------------------
def _dekodiere(rohdaten: bytes) -> str:
    for kodierung in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return rohdaten.decode(kodierung)
        except UnicodeDecodeError:
            continue
    return rohdaten.decode("utf-8", errors="replace")


def _erkenne_trennzeichen(text: str) -> str:
    kopf = "\n".join(text.splitlines()[:5])
    if not kopf.strip():
        raise CSVIngestFehler("Der CSV-Stream ist leer.")
    try:
        return csv.Sniffer().sniff(kopf, delimiters=";,\t|").delimiter
    except csv.Error:
        # Fallback: das Zeichen mit den meisten Vorkommen in der Kopfzeile.
        kopfzeile = text.splitlines()[0]
        kandidaten = {zeichen: kopfzeile.count(zeichen) for zeichen in ";,\t|"}
        bestes = max(kandidaten, key=lambda z: kandidaten[z])
        return bestes if kandidaten[bestes] > 0 else ","


def _lade_dataframe(rohdaten: bytes, trennzeichen: str | None) -> pd.DataFrame:
    text = _dekodiere(rohdaten)
    if not text.strip():
        raise CSVIngestFehler("Der CSV-Stream ist leer.")

    sep = trennzeichen or _erkenne_trennzeichen(text)
    try:
        frame = pd.read_csv(
            io.StringIO(text),
            sep=sep,
            dtype=str,
            keep_default_na=False,
            skip_blank_lines=True,
            engine="python",
        )
    except Exception as exc:
        raise CSVIngestFehler(f"CSV konnte nicht geparst werden: {exc}") from exc

    frame.columns = [str(spalte).strip() for spalte in frame.columns]
    if frame.empty:
        raise CSVIngestFehler("Der CSV-Stream enthaelt keine Datenzeilen.")
    return frame


# ---------------------------------------------------------------------------
# Spaltenzuordnung
# ---------------------------------------------------------------------------
def _ordne_spalten_zu(spalten: Sequence[str]) -> dict[str, str]:
    """Ordnet Zielfelder den tatsaechlichen Spalten des Quellsystems zu."""
    normalisiert = {spalte: normalisiere_spalte(spalte) for spalte in spalten}
    zuordnung: dict[str, str] = {}
    vergeben: set[str] = set()

    # Durchgang 1: exakte Treffer - sie sind immer eindeutig.
    for feld, synonyme in _SYNONYME.items():
        for synonym in synonyme:
            treffer = next(
                (s for s in spalten if s not in vergeben and normalisiert[s] == synonym),
                None,
            )
            if treffer is not None:
                zuordnung[feld] = treffer
                vergeben.add(treffer)
                break

    # Durchgang 2: Teiltreffer. "menge" zuletzt, damit zusammengesetzte
    # Koepfe wie "Lagermenge" zuvor dem Bestand zufallen.
    reihenfolge = [f for f in _SYNONYME if f != "menge"] + ["menge"]
    for feld in reihenfolge:
        if feld in zuordnung:
            continue
        for synonym in _SYNONYME[feld]:
            if len(synonym) < _MIN_SUBSTRING_LAENGE:
                continue
            treffer = next(
                (s for s in spalten if s not in vergeben and synonym in normalisiert[s]),
                None,
            )
            if treffer is not None:
                zuordnung[feld] = treffer
                vergeben.add(treffer)
                break

    return zuordnung


def _periodenspalten(
    spalten: Sequence[str], zuordnung: Mapping[str, str]
) -> list[tuple[str, object]]:
    """Findet im Breitformat die Datumsspalten, chronologisch sortiert."""
    belegt = set(zuordnung.values())
    kandidaten: list[tuple[str, object]] = []
    for spalte in spalten:
        if spalte in belegt:
            continue
        datum = parse_datum(spalte)
        if datum is not None:
            kandidaten.append((spalte, datum))
    kandidaten.sort(key=lambda paar: paar[1])  # type: ignore[arg-type,return-value]
    return kandidaten


# ---------------------------------------------------------------------------
# Layout-spezifischer Aufbau
# ---------------------------------------------------------------------------
def _baue_langformat(
    frame: pd.DataFrame, zuordnung: Mapping[str, str]
) -> tuple[list[SKUInput], list[IngestWarnung]]:
    sku_spalte = zuordnung["sku"]
    datum_spalte = zuordnung["datum"]
    menge_spalte = zuordnung["menge"]

    historien: dict[str, list[ConsumptionRecord]] = {}
    stammdaten: dict[str, dict[str, float]] = {}
    warnungen: list[IngestWarnung] = []

    for position, (_, zeile) in enumerate(frame.iterrows(), start=2):
        sku = str(zeile[sku_spalte]).strip()
        if not sku:
            warnungen.append(
                IngestWarnung(zeile=position, meldung="Zeile ohne Artikelnummer uebersprungen.")
            )
            continue

        menge = zu_float(zeile[menge_spalte])
        datum = str(zeile[datum_spalte]).strip()
        if menge is not None and datum:
            historien.setdefault(sku, []).append(
                ConsumptionRecord(datum=datum, menge=menge)
            )

        stamm = stammdaten.setdefault(sku, {})
        for feld in ("bestand", "lieferzeit", "mindestbestellmenge"):
            spalte = zuordnung.get(feld)
            if spalte is None:
                continue
            wert = zu_float(zeile[spalte])
            if wert is not None:
                stamm[feld] = wert

    artikel = _baue_artikel(historien, stammdaten, warnungen)
    return artikel, warnungen


def _baue_breitformat(
    frame: pd.DataFrame,
    zuordnung: Mapping[str, str],
    perioden: Sequence[tuple[str, object]],
) -> tuple[list[SKUInput], list[IngestWarnung]]:
    sku_spalte = zuordnung["sku"]
    historien: dict[str, list[ConsumptionRecord]] = {}
    stammdaten: dict[str, dict[str, float]] = {}
    warnungen: list[IngestWarnung] = []

    for position, (_, zeile) in enumerate(frame.iterrows(), start=2):
        sku = str(zeile[sku_spalte]).strip()
        if not sku:
            warnungen.append(
                IngestWarnung(zeile=position, meldung="Zeile ohne Artikelnummer uebersprungen.")
            )
            continue

        historie = historien.setdefault(sku, [])
        for spalte, _datum in perioden:
            menge = zu_float(zeile[spalte])
            if menge is not None:
                historie.append(ConsumptionRecord(datum=spalte, menge=menge))

        stamm = stammdaten.setdefault(sku, {})
        for feld in ("bestand", "lieferzeit", "mindestbestellmenge"):
            spalte = zuordnung.get(feld)
            if spalte is None:
                continue
            wert = zu_float(zeile[spalte])
            if wert is not None:
                stamm[feld] = wert

    artikel = _baue_artikel(historien, stammdaten, warnungen)
    return artikel, warnungen


def _baue_artikel(
    historien: Mapping[str, list[ConsumptionRecord]],
    stammdaten: Mapping[str, Mapping[str, float]],
    warnungen: list[IngestWarnung],
) -> list[SKUInput]:
    artikel: list[SKUInput] = []
    for sku, historie in historien.items():
        if len(historie) < 2:
            warnungen.append(
                IngestWarnung(
                    sku=sku,
                    meldung=(
                        f"Nur {len(historie)} Verbrauchsperiode(n) gefunden - "
                        "mindestens 2 erforderlich."
                    ),
                )
            )
            continue

        stamm = stammdaten.get(sku, {})
        if "lieferzeit" not in stamm:
            warnungen.append(
                IngestWarnung(sku=sku, meldung="Keine Lieferzeit gefunden - Artikel uebersprungen.")
            )
            continue

        artikel.append(
            SKUInput(
                sku=sku,
                historie=historie,
                bestand=max(0.0, stamm.get("bestand", 0.0)),
                lieferzeit=max(0, int(round(stamm["lieferzeit"]))),
                mindestbestellmenge=max(0.0, stamm.get("mindestbestellmenge", 0.0)),
            )
        )
    return artikel


# ---------------------------------------------------------------------------
# Einstiegspunkt
# ---------------------------------------------------------------------------
def lese_csv(rohdaten: bytes, *, trennzeichen: str | None = None) -> IngestErgebnis:
    """Liest einen CSV-Stream im RAM und liefert validierte ``SKUInput``-Objekte.

    Args:
        rohdaten: Der rohe CSV-Stream.
        trennzeichen: Optionales festes Trennzeichen; sonst automatisch erkannt.

    Raises:
        CSVIngestFehler: Wenn Pflichtspalten fehlen oder der Stream unlesbar ist.
    """
    frame = _lade_dataframe(rohdaten, trennzeichen)
    spalten = list(frame.columns)
    zuordnung = _ordne_spalten_zu(spalten)

    if "sku" not in zuordnung:
        raise CSVIngestFehler(
            "Keine Artikelspalte erkannt. Erwartet wird eine Spalte wie "
            f"'Art-Nr', 'SKU', 'Artikelnummer' oder 'Material'. Gefunden: {spalten}."
        )

    perioden = _periodenspalten(spalten, zuordnung)
    langformat = "datum" in zuordnung and "menge" in zuordnung

    if langformat:
        artikel, warnungen = _baue_langformat(frame, zuordnung)
        layout = "lang"
    elif perioden:
        artikel, warnungen = _baue_breitformat(frame, zuordnung, perioden)
        layout = "breit"
        zuordnung = dict(zuordnung)
        zuordnung["perioden"] = ", ".join(spalte for spalte, _ in perioden)
    else:
        raise CSVIngestFehler(
            "Keine Verbrauchshistorie erkannt. Erwartet werden entweder die "
            "Spalten 'Datum' und 'Menge'/'Verbrauch' (Langformat) oder "
            f"Datumsspalten je Periode (Breitformat). Gefunden: {spalten}."
        )

    if "lieferzeit" not in zuordnung:
        raise CSVIngestFehler(
            "Keine Lieferzeitspalte erkannt. Erwartet wird eine Spalte wie "
            f"'Lieferzeit', 'Vorlaufzeit' oder 'WBZ'. Gefunden: {spalten}."
        )
    if not artikel:
        raise CSVIngestFehler(
            "Keine auswertbaren Artikel gefunden. "
            + (warnungen[0].meldung if warnungen else "")
        )

    return IngestErgebnis(
        artikel=artikel,
        spalten_mapping={feld: spalte for feld, spalte in zuordnung.items()},
        layout=layout,
        warnungen=warnungen,
    )

