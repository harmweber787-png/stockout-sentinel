#!/usr/bin/env python3
"""Erzeugt einen realistischen ERP-Export eines Schuhgeschaefts.

Eigenstaendiges Skript fuer den End-to-End-Test: Es greift bewusst **nicht**
auf ``src/`` zu und benoetigt ausser der Standardbibliothek nichts. So
entsteht eine Datei, die der Anwendung genauso fremd ist wie ein echter
Export aus einem Warenwirtschaftssystem.

Erzeugt werden 300 Artikel mit 24 Monaten Absatzhistorie. Die Saisonalitaet
folgt dem, was ein Schuhhandel tatsaechlich sieht:

* **Sommerschuhe** (Sandale, Zehentrenner, Badeschuh, Espadrille) mit
  Spitze im Juni/Juli und praktisch keinem Absatz im Winter,
* **Winterschuhe** (Winterstiefel, Schneeboot, gefuetterte Boots) spiegel-
  bildlich dazu mit Spitze im November/Dezember,
* **Uebergangsschuhe** (Halbschuh, Chelsea Boot, Stiefelette) mit zwei
  flacheren Spitzen im Fruehjahr und Herbst,
* **Ganzjahresschuhe** (Sneaker, Laufschuh, Hallenschuh, Arbeitsschuh) mit
  geringer Schwankung und leichtem Weihnachtsgeschaeft,
* **Kinderschuhe** mit ausgepraegter Spitze zum Schulanfang.

Aufruf:
    python3 erzeuge_schuhdaten.py [--ziel PFAD] [--artikel 300] [--monate 24]

Der Zufallskeim ist fest: gleicher Aufruf, gleiche Datei.
"""

from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass
from pathlib import Path

STANDARD_ZIEL = "schuhgeschaeft_300_artikel.csv"
KEIM = 20240913

#: Monatsfaktoren (Januar .. Dezember). Der Mittelwert liegt jeweils bei
#: rund 1.0, damit das Grundniveau eines Artikels seine Jahresmenge bestimmt
#: und nicht die Saisonform.
SAISONPROFILE: dict[str, tuple[float, ...]] = {
    "sommer": (0.15, 0.20, 0.45, 0.85, 1.55, 2.20, 2.30, 1.70, 0.80, 0.35, 0.20, 0.15),
    "winter": (1.60, 1.10, 0.50, 0.25, 0.15, 0.10, 0.10, 0.20, 0.60, 1.40, 2.10, 2.40),
    "uebergang": (0.70, 0.80, 1.30, 1.45, 1.10, 0.80, 0.70, 0.90, 1.40, 1.50, 1.10, 0.85),
    "ganzjahr": (0.90, 0.90, 1.05, 1.10, 1.10, 1.00, 0.95, 1.00, 1.10, 1.05, 1.00, 1.25),
    "schulanfang": (0.70, 0.75, 0.95, 1.00, 0.95, 0.80, 1.10, 2.10, 1.75, 0.95, 0.75, 1.20),
    "hausschuh": (1.30, 1.00, 0.70, 0.50, 0.35, 0.30, 0.30, 0.45, 0.85, 1.35, 1.85, 2.05),
}


@dataclass(frozen=True)
class Modell:
    """Ein Schuhmodell als Bauplan fuer mehrere Groessen und Farben.

    Attributes:
        kuerzel: Kuerzel im Artikelschluessel.
        saison: Schluessel in :data:`SAISONPROFILE`.
        gruppe: Zielgruppe (DA/HE/KI) - bestimmt die Groessenreihe.
        grundabsatz: Mittlerer Monatsabsatz je Groesse im Saisonmittel.
        lieferzeit: Wiederbeschaffungszeit in Tagen.
    """

    kuerzel: str
    saison: str
    gruppe: str
    grundabsatz: float
    lieferzeit: int


#: Lieferzeiten bilden ab, was im Schuhhandel ueblich ist: Fernost-Produktion
#: mit langem Vorlauf, europaeische Nachlieferung deutlich kuerzer.
MODELLE: tuple[Modell, ...] = (
    # Sommer
    Modell("SANDALE", "sommer", "DA", 34.0, 75),
    Modell("ZEHENTRENNER", "sommer", "DA", 46.0, 90),
    Modell("ESPADRILLE", "sommer", "DA", 22.0, 70),
    Modell("BADESCHUH", "sommer", "HE", 30.0, 60),
    Modell("RIEMENSANDALE", "sommer", "HE", 19.0, 80),
    # Winter
    Modell("WINTERSTIEFEL", "winter", "DA", 26.0, 95),
    Modell("SCHNEEBOOT", "winter", "HE", 21.0, 105),
    Modell("LAMMFELLBOOT", "winter", "DA", 15.0, 110),
    Modell("THERMOSTIEFEL", "winter", "KI", 18.0, 85),
    # Uebergang
    Modell("CHELSEABOOT", "uebergang", "DA", 24.0, 55),
    Modell("STIEFELETTE", "uebergang", "DA", 20.0, 60),
    Modell("HALBSCHUH", "uebergang", "HE", 27.0, 45),
    Modell("SCHNUERSCHUH", "uebergang", "HE", 23.0, 50),
    Modell("LOAFER", "uebergang", "DA", 17.0, 65),
    # Ganzjahr
    Modell("SNEAKER", "ganzjahr", "DA", 58.0, 40),
    Modell("SNEAKER", "ganzjahr", "HE", 62.0, 40),
    Modell("LAUFSCHUH", "ganzjahr", "HE", 44.0, 35),
    Modell("TRAILRUNNER", "ganzjahr", "HE", 26.0, 50),
    Modell("HALLENSCHUH", "ganzjahr", "KI", 25.0, 45),
    Modell("ARBEITSSCHUH", "ganzjahr", "HE", 33.0, 30),
    Modell("BUEROSCHUH", "ganzjahr", "DA", 21.0, 35),
    # Kinder / Hausschuh
    Modell("KINDERSNEAKER", "schulanfang", "KI", 40.0, 55),
    Modell("SCHULSCHUH", "schulanfang", "KI", 29.0, 60),
    Modell("HAUSSCHUH", "hausschuh", "DA", 24.0, 25),
    Modell("FILZPANTOFFEL", "hausschuh", "HE", 18.0, 21),
)

GROESSEN: dict[str, tuple[int, ...]] = {
    "DA": (36, 37, 38, 39, 40, 41),
    "HE": (40, 41, 42, 43, 44, 45, 46),
    "KI": (28, 30, 32, 34, 35),
}

#: Groessengang: die mittleren Groessen tragen den Umsatz.
GROESSENGEWICHT: dict[str, tuple[float, ...]] = {
    "DA": (0.55, 0.90, 1.25, 1.30, 0.95, 0.55),
    "HE": (0.45, 0.75, 1.10, 1.30, 1.20, 0.80, 0.45),
    "KI": (0.70, 1.00, 1.20, 1.00, 0.75),
}

FARBEN: tuple[tuple[str, float], ...] = (
    ("SW", 1.35),   # schwarz
    ("BR", 0.95),   # braun
    ("WS", 1.10),   # weiss
    ("NV", 0.80),   # navy
    ("GR", 0.70),   # grau
    ("BG", 0.60),   # beige
    ("RT", 0.45),   # rot
)


def monatsliste(monate: int, startjahr: int, startmonat: int) -> list[tuple[int, int]]:
    """Liefert ``monate`` aufeinanderfolgende ``(jahr, monat)``-Paare."""
    reihe: list[tuple[int, int]] = []
    jahr, monat = startjahr, startmonat
    for _ in range(monate):
        reihe.append((jahr, monat))
        monat += 1
        if monat > 12:
            monat = 1
            jahr += 1
    return reihe


def baue_artikel(anzahl: int, rng: random.Random) -> list[dict]:
    """Erzeugt ``anzahl`` eindeutige Artikel aus Modell/Groesse/Farbe."""
    kandidaten: list[dict] = []
    for modell in MODELLE:
        groessen = GROESSEN[modell.gruppe]
        gewichte = GROESSENGEWICHT[modell.gruppe]
        for groesse, groessengewicht in zip(groessen, gewichte):
            for farbe, farbgewicht in FARBEN:
                kandidaten.append(
                    {
                        "artikel_id": f"{modell.kuerzel}-{modell.gruppe}-{groesse}-{farbe}",
                        "modell": modell,
                        "niveau": modell.grundabsatz * groessengewicht * farbgewicht,
                    }
                )

    if anzahl > len(kandidaten):
        raise ValueError(
            f"Nur {len(kandidaten)} eindeutige Artikel moeglich, {anzahl} angefragt."
        )

    # Stabil mischen und die ersten n nehmen: reproduzierbar, aber keine
    # Haeufung eines einzelnen Modells am Anfang der Datei.
    rng.shuffle(kandidaten)
    gewaehlt = kandidaten[:anzahl]
    gewaehlt.sort(key=lambda eintrag: eintrag["artikel_id"])
    return gewaehlt


def erzeuge_zeilen(artikel: list[dict], monate: int, rng: random.Random) -> list[dict]:
    """Baut die Langformat-Zeilen: eine je Artikel und Monat."""
    kalender = monatsliste(monate, startjahr=2023, startmonat=1)
    zeilen: list[dict] = []

    for eintrag in artikel:
        modell: Modell = eintrag["modell"]
        profil = SAISONPROFILE[modell.saison]

        # Leichter Lebenszyklus-Trend je Artikel: manche Modelle laufen an,
        # andere aus. Ueber 24 Monate ergibt das +/- 25 %.
        trend_gesamt = rng.uniform(-0.25, 0.25)
        mengen: list[int] = []

        for index, (_jahr, monat) in enumerate(kalender):
            saison = profil[monat - 1]
            trend = 1.0 + trend_gesamt * (index / max(1, monate - 1))
            rauschen = rng.gauss(1.0, 0.18)
            menge = eintrag["niveau"] * saison * trend * rauschen
            mengen.append(max(0, int(round(menge))))

        # Bestand aus dem juengsten Absatz ableiten. Die Streuung bildet ab,
        # dass ein realer Bestand nicht kalibriert ist: einige Artikel sind
        # knapp, viele passend, saisonfremde Ware liegt schwer im Lager.
        letzte = mengen[-3:] or mengen
        basis = sum(letzte) / len(letzte)
        deckung = rng.lognormvariate(0.45, 0.75)  # Median ~1.6 Monate
        bestand = max(0, int(round(basis * deckung)))

        for (jahr, monat), menge in zip(kalender, mengen):
            zeilen.append(
                {
                    "artikel_id": eintrag["artikel_id"],
                    "datum": f"{jahr:04d}-{monat:02d}-01",
                    "menge": menge,
                    "bestand": bestand,
                    "lieferzeit_tage": modell.lieferzeit,
                }
            )
    return zeilen


def schreibe_csv(zeilen: list[dict], ziel: Path) -> None:
    """Schreibt den Export im ERP-typischen Langformat."""
    spalten = ["artikel_id", "datum", "menge", "bestand", "lieferzeit_tage"]
    with ziel.open("w", encoding="utf-8", newline="") as datei:
        schreiber = csv.DictWriter(datei, fieldnames=spalten, delimiter=";")
        schreiber.writeheader()
        schreiber.writerows(zeilen)


def main() -> None:
    zerleger = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    zerleger.add_argument("--ziel", default=STANDARD_ZIEL, help="Zieldatei")
    zerleger.add_argument("--artikel", type=int, default=300, help="Anzahl Artikel")
    zerleger.add_argument("--monate", type=int, default=24, help="Monate Historie")
    argumente = zerleger.parse_args()

    rng = random.Random(KEIM)
    artikel = baue_artikel(argumente.artikel, rng)
    zeilen = erzeuge_zeilen(artikel, argumente.monate, rng)

    ziel = Path(argumente.ziel)
    schreibe_csv(zeilen, ziel)

    print(f"Geschrieben: {ziel.resolve()}")
    print(f"  Artikel : {len(artikel)}")
    print(f"  Monate  : {argumente.monate}")
    print(f"  Zeilen  : {len(zeilen)} (+ Kopfzeile)")
    print(f"  Groesse : {ziel.stat().st_size / 1024:.0f} KiB")


if __name__ == "__main__":
    main()
