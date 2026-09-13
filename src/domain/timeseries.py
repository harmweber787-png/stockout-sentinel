"""Normalisierung roher Verbrauchshistorien zu einer ``ConsumptionSeries``.

Echte ERP-Exporte liefern Perioden in beliebiger Granularitaet (taeglich,
woechentlich, monatlich, quartalsweise) und in beliebigen Datumsformaten.
Dieses Modul erkennt beides generisch, ohne Annahmen ueber Branche oder
Artikelsortiment, und liefert eine auf Tage normierte Reihe an den Kern.
"""

from __future__ import annotations

from datetime import date, datetime
from statistics import median
from typing import Iterable, Sequence

from src.ports.forecasting import ConsumptionSeries

__all__ = ["DEFAULT_PERIODENLAENGE_TAGE", "parse_datum", "baue_serie"]

#: Annahme, wenn sich aus den Datumsangaben keine Kadenz ableiten laesst.
DEFAULT_PERIODENLAENGE_TAGE = 30.0

#: Explizite Formate werden vor der heuristischen Erkennung probiert, damit
#: das in CH/DE uebliche ``TT.MM.JJJJ`` nie als ``MM/TT`` fehlinterpretiert wird.
_DATUMSFORMATE: tuple[str, ...] = (
    "%Y-%m-%d",
    "%d.%m.%Y",
    "%d.%m.%y",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%Y/%m/%d",
    "%Y-%m",
    "%m.%Y",
    "%m/%Y",
    "%Y%m%d",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
)

#: Monatsnamen/-abkuerzungen (DE/EN/FR/IT) fuer Spaltenkoepfe wie "Jan 24".
_MONATSNAMEN: dict[str, int] = {}
for _index, _gruppe in enumerate(
    (
        ("jan", "januar", "january", "janvier", "gennaio"),
        ("feb", "februar", "february", "fevrier", "février", "febbraio"),
        ("mar", "mrz", "maerz", "märz", "march", "mars", "marzo"),
        ("apr", "april", "avril", "aprile"),
        ("mai", "may", "maggio"),
        ("jun", "juni", "june", "juin", "giugno"),
        ("jul", "juli", "july", "juillet", "luglio"),
        ("aug", "august", "aout", "août", "agosto"),
        ("sep", "sept", "september", "septembre", "settembre"),
        ("okt", "oct", "oktober", "october", "octobre", "ottobre"),
        ("nov", "november", "novembre", "novembre"),
        ("dez", "dec", "dezember", "december", "decembre", "dicembre"),
    ),
    start=1,
):
    for _name in _gruppe:
        _MONATSNAMEN[_name] = _index


def _monat_aus_text(text: str) -> date | None:
    """Erkennt Perioden wie ``"Jan 24"``, ``"2024 Maerz"`` oder ``"Dez-2023"``."""
    tokens = [t for t in _tokenize(text) if t]
    monat: int | None = None
    jahr: int | None = None
    for token in tokens:
        if token.isdigit():
            if len(token) == 4:
                jahr = int(token)
            elif len(token) <= 2 and jahr is None:
                jahr = 2000 + int(token)
            continue
        treffer = _MONATSNAMEN.get(token)
        if treffer is not None:
            monat = treffer
    if monat is None:
        return None
    return date(jahr if jahr is not None else 1970, monat, 1)


def _tokenize(text: str) -> list[str]:
    aktuell: list[str] = []
    tokens: list[str] = []
    letzter_typ = ""
    for zeichen in text.strip().lower():
        typ = "d" if zeichen.isdigit() else ("a" if zeichen.isalpha() else "")
        if not typ or typ != letzter_typ:
            if aktuell:
                tokens.append("".join(aktuell))
                aktuell = []
        if typ:
            aktuell.append(zeichen)
        letzter_typ = typ
    if aktuell:
        tokens.append("".join(aktuell))
    return tokens


def parse_datum(wert: object) -> date | None:
    """Parst ein Periodenkennzeichen moeglichst tolerant zu einem ``date``.

    Unterstuetzt ``date``/``datetime``-Objekte, die gaengigen numerischen
    Formate sowie sprachliche Monatsangaben. Gibt ``None`` zurueck, wenn
    keine Interpretation moeglich ist - die Reihe bleibt dann trotzdem
    nutzbar, es greift lediglich die Standard-Periodenlaenge.
    """
    if wert is None:
        return None
    if isinstance(wert, datetime):
        return wert.date()
    if isinstance(wert, date):
        return wert

    text = str(wert).strip()
    if not text:
        return None

    for fmt in _DATUMSFORMATE:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass

    return _monat_aus_text(text)


def _periodenlaenge(daten: Sequence[date]) -> float | None:
    """Median-Abstand zwischen aufeinanderfolgenden Perioden in Tagen."""
    if len(daten) < 2:
        return None
    abstaende = [
        (spaeter - frueher).days
        for frueher, spaeter in zip(daten, daten[1:])
        if (spaeter - frueher).days > 0
    ]
    if not abstaende:
        return None
    return float(median(abstaende))


def baue_serie(
    eintraege: Iterable[tuple[object, float]],
    *,
    standard_periodenlaenge: float = DEFAULT_PERIODENLAENGE_TAGE,
) -> ConsumptionSeries:
    """Baut aus ``(datum, menge)``-Paaren eine normalisierte Verbrauchsreihe.

    Die Eintraege werden chronologisch sortiert, sofern sich alle Daten
    parsen lassen; andernfalls bleibt die uebergebene Reihenfolge erhalten.
    Negative Mengen (Retouren/Stornos) werden auf 0 begrenzt, damit sie die
    Trendschaetzung nicht verzerren.

    Raises:
        ValueError: Wenn weniger als zwei Perioden uebergeben wurden.
    """
    paare = [(parse_datum(datum), max(0.0, float(menge))) for datum, menge in eintraege]
    if len(paare) < 2:
        raise ValueError("Die Historie benoetigt mindestens 2 Verbrauchsperioden.")

    vollstaendig_datiert = all(d is not None for d, _ in paare)
    if vollstaendig_datiert:
        paare.sort(key=lambda paar: paar[0])  # type: ignore[arg-type,return-value]
        laenge = _periodenlaenge([d for d, _ in paare if d is not None])
    else:
        laenge = None

    return ConsumptionSeries(
        werte=tuple(menge for _, menge in paare),
        periodenlaenge_tage=laenge if laenge else float(standard_periodenlaenge),
        datiert=laenge is not None,
    )
