"""Fachkern der Disposition: reine, zustandslose Rechenregeln.

Dieses Modul ist das Herz der Anwendung und bewusst frei von Framework-,
IO- und Prognose-Abhaengigkeiten. Es kennt weder FastAPI noch Pydantic,
pandas oder TimesFM - nur Zahlen und die fachlichen Formeln. Dadurch ist
es vollstaendig isoliert testbar.

Formeln (Vorgabe Fachbereich):
    tagesbedarf        = prognostizierter Monatsabsatz / 30
    reichweite_tage    = bestand / tagesbedarf          (sonst SENTINEL)
    sicherheitsbestand = lieferzeit * (tagesbedarf_P90 - tagesbedarf_P50)
    meldebestand       = (tagesbedarf * lieferzeit) + sicherheitsbestand
    nachbestellmenge   = max(0, meldebestand - bestand)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "KEINE_REICHWEITE",
    "NACHKOMMASTELLEN",
    "TAGE_PRO_MONAT",
    "Dispositionskennzahlen",
    "Status",
    "berechne_kennzahlen",
    "bestimme_status",
    "empfohlene_massnahme",
    "tagesbedarf_aus_monatsabsatz",
]

#: Kaufmaennischer Monat, auf den jede Prognose normiert wird.
TAGE_PRO_MONAT = 30.0

#: Sentinel-Reichweite, wenn kein Bedarf prognostiziert wird (kein Verbrauch).
KEINE_REICHWEITE = 999.0

#: Einheitliche Rundung aller ausgewiesenen Kennzahlen.
NACHKOMMASTELLEN = 2


class Status(str, Enum):
    """Ampel-Status eines Artikels.

    Der Wert enthaelt das Ampel-Symbol fuer die direkte Anzeige in
    Frontends; ``code`` liefert die maschinenlesbare Kurzform.
    """

    KRITISCH = "🔴 KRITISCH"
    OPTIMAL = "🟢 OPTIMAL"
    UEBERBESTAND = "🟡 UEBERBESTAND"

    @property
    def code(self) -> str:
        """Symbolfreier Code, z. B. ``"KRITISCH"``."""
        return self.name

    @property
    def prioritaet(self) -> int:
        """Sortierrang fuer die Prioritaetenliste (0 = dringendster Rang)."""
        return _PRIORITAET[self]


_PRIORITAET: dict[Status, int] = {
    Status.KRITISCH: 0,
    Status.OPTIMAL: 1,
    Status.UEBERBESTAND: 2,
}


@dataclass(frozen=True, slots=True)
class Dispositionskennzahlen:
    """Vollstaendiges Rechenergebnis fuer eine SKU."""

    prognose_tagesbedarf: float
    prognose_tagesbedarf_p90: float
    reichweite_tage: float
    sicherheitsbestand: float
    meldebestand: float
    nachbestellmenge: float
    status: Status


def _runde(wert: float) -> float:
    return round(float(wert), NACHKOMMASTELLEN)


def tagesbedarf_aus_monatsabsatz(monatsabsatz: float) -> float:
    """Rechnet einen Monatsabsatz auf den Tagesbedarf herunter."""
    return max(0.0, float(monatsabsatz)) / TAGE_PRO_MONAT


def berechne_reichweite(bestand: float, tagesbedarf: float) -> float:
    """Reichweite in Tagen; ohne Bedarf gilt der Sentinel ``999``."""
    if tagesbedarf <= 0:
        return KEINE_REICHWEITE
    return max(0.0, float(bestand)) / tagesbedarf


def bestimme_status(reichweite_tage: float, lieferzeit: int | float) -> Status:
    """Ordnet die Reichweite der Ampel zu.

    - ``KRITISCH``     : reichweite <= lieferzeit
    - ``OPTIMAL``      : lieferzeit < reichweite <= lieferzeit * 2
    - ``UEBERBESTAND`` : reichweite > lieferzeit * 2
    """
    lz = float(lieferzeit)
    if reichweite_tage <= lz:
        return Status.KRITISCH
    if reichweite_tage <= lz * 2:
        return Status.OPTIMAL
    return Status.UEBERBESTAND


def berechne_kennzahlen(
    *,
    bestand: float,
    lieferzeit: int,
    monatsabsatz_p50: float,
    monatsabsatz_p90: float,
    mindestbestellmenge: float = 0.0,
) -> Dispositionskennzahlen:
    """Wendet die Dispositionsformeln an.

    Alle ausgewiesenen Werte werden auf :data:`NACHKOMMASTELLEN` gerundet.
    Nachgelagerte Groessen werden bewusst aus den bereits gerundeten
    Vorgaengern abgeleitet, damit die veroeffentlichten Zahlen exakt
    zueinander passen (``nachbestellmenge == meldebestand - bestand``).

    Args:
        bestand: Aktueller Lagerbestand.
        lieferzeit: Wiederbeschaffungszeit in Tagen.
        monatsabsatz_p50: Prognostizierter Monatsabsatz (Median).
        monatsabsatz_p90: Prognostizierter Monatsabsatz im 90 %-Quantil.
        mindestbestellmenge: Optionale Mindestbestellmenge des Lieferanten.
            Sie hebt eine bereits ausgeloeste Bestellung auf die kleinste
            zulaessige Menge an, loest aber selbst nie eine Bestellung aus.

    Returns:
        Das gerundete Kennzahlen-Set inklusive Ampel-Status.
    """
    bestand = max(0.0, float(bestand))
    lieferzeit_f = float(lieferzeit)

    tagesbedarf = _runde(tagesbedarf_aus_monatsabsatz(monatsabsatz_p50))
    tagesbedarf_p90 = _runde(tagesbedarf_aus_monatsabsatz(monatsabsatz_p90))
    # Das P90 kann rechnerisch nie unter dem P50 liegen; Rundung absichern.
    tagesbedarf_p90 = max(tagesbedarf, tagesbedarf_p90)

    reichweite = berechne_reichweite(bestand, tagesbedarf)
    reichweite = _runde(min(reichweite, KEINE_REICHWEITE))

    sicherheitsbestand = _runde(max(0.0, lieferzeit_f * (tagesbedarf_p90 - tagesbedarf)))
    meldebestand = _runde(tagesbedarf * lieferzeit_f + sicherheitsbestand)

    nachbestellmenge = _runde(max(0.0, meldebestand - bestand))
    moq = max(0.0, float(mindestbestellmenge or 0.0))
    if nachbestellmenge > 0 and moq > nachbestellmenge:
        nachbestellmenge = _runde(moq)

    return Dispositionskennzahlen(
        prognose_tagesbedarf=tagesbedarf,
        prognose_tagesbedarf_p90=tagesbedarf_p90,
        reichweite_tage=reichweite,
        sicherheitsbestand=sicherheitsbestand,
        meldebestand=meldebestand,
        nachbestellmenge=nachbestellmenge,
        status=bestimme_status(reichweite, lieferzeit_f),
    )


def empfohlene_massnahme(kennzahlen: Dispositionskennzahlen, lieferzeit: int) -> str:
    """Formuliert eine sprachliche Handlungsempfehlung zum Kennzahlen-Set."""
    reichweite = kennzahlen.reichweite_tage
    menge = kennzahlen.nachbestellmenge

    if kennzahlen.status is Status.KRITISCH:
        if kennzahlen.prognose_tagesbedarf <= 0:
            return (
                "Bestand erschoepft, aber kein Verbrauch prognostiziert - "
                "Artikelstamm und Bedarfstreiber pruefen."
            )
        if menge <= 0:
            return (
                f"Reichweite {reichweite:g} Tage liegt unter der Lieferzeit "
                f"({lieferzeit} Tage). Meldebestand ist gedeckt, Verbrauch eng "
                "ueberwachen."
            )
        fehltage = max(0.0, lieferzeit - reichweite)
        return (
            f"Sofort {menge:g} Einheiten bestellen: Reichweite {reichweite:g} Tage "
            f"deckt die Lieferzeit von {lieferzeit} Tagen nicht "
            f"(Fehlmenge ab Tag {reichweite:g}, {fehltage:g} Tage Unterdeckung)."
        )

    if kennzahlen.status is Status.OPTIMAL:
        if menge > 0:
            return (
                f"Bestand im Zielkorridor ({reichweite:g} Tage). Meldebestand "
                f"erreicht - Regelbestellung ueber {menge:g} Einheiten einplanen."
            )
        return (
            f"Bestand im Zielkorridor ({reichweite:g} Tage Reichweite bei "
            f"{lieferzeit} Tagen Lieferzeit). Keine Massnahme erforderlich."
        )

    if kennzahlen.prognose_tagesbedarf <= 0:
        return (
            "Kein Verbrauch prognostiziert - Artikel auf Auslauf bzw. "
            "Bestandsabbau pruefen."
        )
    # Bei gedeckelter Reichweite ist der exakte Wert fachlich irrelevant.
    reichweite_text = (
        f"ueber {KEINE_REICHWEITE:g}" if reichweite >= KEINE_REICHWEITE else f"{reichweite:g}"
    )
    return (
        f"Ueberbestand: {reichweite_text} Tage Reichweite bei {lieferzeit} Tagen "
        "Lieferzeit. Bestellungen aussetzen und Kapitalbindung abbauen."
    )
