"""Programmatisch erzeugte Demo-Datensaetze fuer die Weboberflaeche.

Der Service bleibt datenfrei: Es liegen **keine** Beispieldateien im
Repository und keine branchenspezifischen Stammdaten im Code. Die hier
erzeugten Reihen sind synthetische, generische Nachfragemuster mit
neutralen Artikelnummern - sie dienen ausschliesslich dazu, die Oberflaeche
ohne echten ERP-Export ausprobieren zu koennen.

Alle Generatoren sind deterministisch (fester Zufallskeim), damit die
Oberflaeche bei gleicher Auswahl reproduzierbare Ergebnisse zeigt.
"""

from __future__ import annotations

import io
import csv
from dataclasses import dataclass
from datetime import date
from typing import Callable

import numpy as np

from src.schemas import SKUInput

__all__ = [
    "DEMO_SZENARIEN",
    "Demoszenario",
    "als_csv",
    "baue_szenario",
    "szenario_namen",
]

#: Fester Keim: gleiche Auswahl -> gleiche Zahlen.
_KEIM = 20240301


def _monatsreihe(anzahl: int, start: date = date(2023, 1, 1)) -> list[str]:
    """Erzeugt ``anzahl`` aufeinanderfolgende Monatserste als ISO-Datum."""
    daten: list[str] = []
    jahr, monat = start.year, start.month
    for _ in range(anzahl):
        daten.append(date(jahr, monat, 1).isoformat())
        monat += 1
        if monat > 12:
            monat = 1
            jahr += 1
    return daten


def _rauschen(rng: np.random.Generator, werte: np.ndarray, streuung: float) -> np.ndarray:
    """Legt multiplikatives Rauschen auf eine Basiskurve; nie negativ."""
    gestoert = werte * (1.0 + rng.normal(0.0, streuung, size=werte.size))
    return np.maximum(0.0, np.round(gestoert, 1))


# ---------------------------------------------------------------------------
# Nachfragemuster
# ---------------------------------------------------------------------------
def _konstant(rng: np.random.Generator, perioden: int, niveau: float) -> np.ndarray:
    return _rauschen(rng, np.full(perioden, niveau, dtype=float), 0.08)


def _wachsend(rng: np.random.Generator, perioden: int, niveau: float) -> np.ndarray:
    basis = niveau * (1.0 + 0.06 * np.arange(perioden))
    return _rauschen(rng, basis, 0.07)


def _saisonal(rng: np.random.Generator, perioden: int, niveau: float) -> np.ndarray:
    phase = 2 * np.pi * np.arange(perioden) / 12.0
    basis = niveau * (1.0 + 0.45 * np.sin(phase))
    return _rauschen(rng, basis, 0.06)


def _auslaufend(rng: np.random.Generator, perioden: int, niveau: float) -> np.ndarray:
    basis = niveau * np.power(0.88, np.arange(perioden))
    return _rauschen(rng, basis, 0.10)


def _sporadisch(rng: np.random.Generator, perioden: int, niveau: float) -> np.ndarray:
    """Intermittierender Bedarf: viele Nullperioden, seltene Spitzen."""
    treffer = rng.random(perioden) < 0.35
    basis = np.where(treffer, niveau * rng.uniform(0.6, 1.8, size=perioden), 0.0)
    return np.round(np.maximum(0.0, basis), 1)


_MUSTER: dict[str, Callable[[np.random.Generator, int, float], np.ndarray]] = {
    "konstant": _konstant,
    "wachsend": _wachsend,
    "saisonal": _saisonal,
    "auslaufend": _auslaufend,
    "sporadisch": _sporadisch,
}


# ---------------------------------------------------------------------------
# Szenarien
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Artikelvorlage:
    """Bauplan eines synthetischen Artikels.

    Der Bestand wird nicht absolut vorgegeben, sondern ueber
    ``ziel_reichweite`` als Vielfaches der Lieferzeit. Er wird aus der
    tatsaechlich erzeugten Historie zurueckgerechnet - damit trifft ein
    Szenario den beabsichtigten Ampelzustand auch dann, wenn sich Muster,
    Niveau oder Rauschen aendern. Eine handgesetzte Bestandszahl waere bei
    jeder Aenderung der Generatoren still falsch geworden.

    Faustwerte: < 1.0 ergibt KRITISCH, 1.0 - 2.0 OPTIMAL, > 2.0
    UEBERBESTAND.
    """

    sku: str
    muster: str
    niveau: float
    ziel_reichweite: float
    lieferzeit: int
    mindestbestellmenge: float = 0.0


@dataclass(frozen=True, slots=True)
class Demoszenario:
    """Benanntes Buendel synthetischer Artikel."""

    schluessel: str
    titel: str
    beschreibung: str
    vorlagen: tuple[Artikelvorlage, ...]
    perioden: int = 24


#: Die Szenarien decken die drei Ampelzustaende und die typischen
#: Nachfrageformen ab, damit sich die Oberflaeche vollstaendig zeigen laesst.
DEMO_SZENARIEN: tuple[Demoszenario, ...] = (
    Demoszenario(
        schluessel="mischbestand",
        titel="Gemischtes Sortiment",
        beschreibung=(
            "Querschnitt über alle drei Ampelzustände – zeigt die "
            "Prioritätenliste in voller Breite."
        ),
        vorlagen=(
            Artikelvorlage("SKU-1001", "konstant", 300.0, 0.4, 14, 50.0),
            Artikelvorlage("SKU-1002", "wachsend", 180.0, 1.4, 21),
            Artikelvorlage("SKU-1003", "saisonal", 240.0, 1.6, 10, 25.0),
            Artikelvorlage("SKU-1004", "auslaufend", 150.0, 6.0, 30),
            Artikelvorlage("SKU-1005", "sporadisch", 60.0, 3.5, 7),
            Artikelvorlage("SKU-1006", "konstant", 90.0, 0.6, 5, 100.0),
        ),
    ),
    Demoszenario(
        schluessel="engpass",
        titel="Engpass-Situation",
        beschreibung=(
            "Durchgängig knappe Bestände bei langen Lieferzeiten – alle "
            "Artikel stehen auf Rot."
        ),
        vorlagen=(
            Artikelvorlage("SKU-2001", "wachsend", 420.0, 0.3, 28, 200.0),
            Artikelvorlage("SKU-2002", "konstant", 260.0, 0.2, 21),
            Artikelvorlage("SKU-2003", "saisonal", 310.0, 0.5, 35, 150.0),
            Artikelvorlage("SKU-2004", "konstant", 140.0, 0.15, 14),
        ),
    ),
    Demoszenario(
        schluessel="ueberbestand",
        titel="Kapitalbindung",
        beschreibung=(
            "Hohe Bestände bei fallendem Bedarf – Kandidaten für "
            "Bestandsabbau."
        ),
        vorlagen=(
            Artikelvorlage("SKU-3001", "auslaufend", 200.0, 8.0, 14),
            Artikelvorlage("SKU-3002", "auslaufend", 120.0, 10.0, 10),
            Artikelvorlage("SKU-3003", "konstant", 80.0, 12.0, 7),
            Artikelvorlage("SKU-3004", "sporadisch", 45.0, 6.0, 21),
        ),
    ),
    Demoszenario(
        schluessel="saison",
        titel="Saisonales Sortiment",
        beschreibung=(
            "Ausgeprägte Jahresschwankung über zwei volle Zyklen – zeigt, "
            "wie das Modell den Verlauf aufnimmt."
        ),
        vorlagen=(
            Artikelvorlage("SKU-4001", "saisonal", 500.0, 0.5, 14, 100.0),
            Artikelvorlage("SKU-4002", "saisonal", 320.0, 1.5, 21),
            Artikelvorlage("SKU-4003", "saisonal", 210.0, 3.0, 10, 50.0),
        ),
    ),
)


def _bestand_aus_ziel(
    mengen: np.ndarray, vorlage: Artikelvorlage, periodenlaenge_tage: float = 30.0
) -> float:
    """Rechnet den Lagerbestand aus der angestrebten Reichweite zurueck.

    Als Bedarfsschaetzer dient der Median der juengsten Perioden - robust
    gegen das aufgelegte Rauschen und nah genug an dem, was das Modell
    spaeter prognostiziert.
    """
    fenster = mengen[-min(6, mengen.size) :]
    tagesbedarf = float(np.median(fenster)) / periodenlaenge_tage
    if tagesbedarf <= 0:
        # Muster ohne Verbrauch (z. B. lange Nullstrecke): ein kleiner
        # Restbestand, damit die Zeile trotzdem etwas zeigt.
        return round(float(vorlage.niveau), 1)
    return round(vorlage.ziel_reichweite * vorlage.lieferzeit * tagesbedarf, 1)


def szenario_namen() -> dict[str, str]:
    """Mapping Schluessel -> Anzeigetitel fuer die Auswahlliste."""
    return {szenario.schluessel: szenario.titel for szenario in DEMO_SZENARIEN}


def _finde(schluessel: str) -> Demoszenario:
    for szenario in DEMO_SZENARIEN:
        if szenario.schluessel == schluessel:
            return szenario
    bekannt = ", ".join(s.schluessel for s in DEMO_SZENARIEN)
    raise KeyError(f"Unbekanntes Demo-Szenario '{schluessel}'. Bekannt: {bekannt}.")


def baue_szenario(schluessel: str, *, perioden: int | None = None) -> list[SKUInput]:
    """Erzeugt die Artikel eines Szenarios als ``SKUInput``-Liste.

    Args:
        schluessel: Bezeichner aus :data:`DEMO_SZENARIEN`.
        perioden: Laenge der Historie in Monaten; sonst die Szenario-Vorgabe.

    Raises:
        KeyError: Bei unbekanntem Szenario.
        ValueError: Bei weniger als 2 Perioden - das Schema fordert mehr.
    """
    szenario = _finde(schluessel)
    anzahl = perioden if perioden is not None else szenario.perioden
    if anzahl < 2:
        raise ValueError("Eine Demo-Historie benoetigt mindestens 2 Perioden.")

    daten = _monatsreihe(anzahl)
    artikel: list[SKUInput] = []

    for versatz, vorlage in enumerate(szenario.vorlagen):
        # Je Artikel ein eigener, aber fester Keim: reproduzierbar und
        # trotzdem nicht in allen Reihen dasselbe Rauschen.
        rng = np.random.default_rng(_KEIM + versatz)
        mengen = _MUSTER[vorlage.muster](rng, anzahl, vorlage.niveau)
        artikel.append(
            SKUInput(
                sku=vorlage.sku,
                historie=[
                    {"datum": datum, "menge": float(menge)}
                    for datum, menge in zip(daten, mengen)
                ],
                bestand=_bestand_aus_ziel(mengen, vorlage),
                lieferzeit=vorlage.lieferzeit,
                mindestbestellmenge=vorlage.mindestbestellmenge,
            )
        )
    return artikel


def als_csv(artikel: list[SKUInput], *, trennzeichen: str = ";") -> str:
    """Serialisiert Artikel als ERP-typischen CSV-Export im Langformat.

    Nuetzlich, um aus einem Demo-Szenario heraus den CSV-Importpfad zu
    zeigen oder eine Vorlage zum Herunterladen anzubieten.
    """
    puffer = io.StringIO()
    schreiber = csv.writer(puffer, delimiter=trennzeichen, lineterminator="\n")
    schreiber.writerow(
        ["Art-Nr", "Datum", "Verbrauch", "Bestand", "Vorlaufzeit", "Mindestbestellmenge"]
    )
    for eintrag in artikel:
        for satz in eintrag.historie:
            schreiber.writerow(
                [
                    eintrag.sku,
                    satz.datum,
                    f"{satz.menge:g}",
                    f"{eintrag.bestand:g}",
                    eintrag.lieferzeit,
                    f"{eintrag.mindestbestellmenge or 0:g}",
                ]
            )
    return puffer.getvalue()
