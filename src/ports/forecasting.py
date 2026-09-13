"""Port-Definition fuer die Bedarfsprognose (hexagonale Architektur).

Der Dispositions-Kern kennt ausschliesslich dieses Protokoll. Welche
Technologie die Prognose tatsaechlich liefert (neuronales Foundation-Model
wie TimesFM oder ein statistischer Schaetzer), ist fuer die Domaene
unsichtbar und wird ueber Adapter in ``src/adapters`` angebunden.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = [
    "ConsumptionSeries",
    "DemandForecast",
    "ForecastPort",
    "ForecastUnavailable",
]


class ForecastUnavailable(RuntimeError):
    """Ein Adapter kann fuer diese Reihe keine Prognose liefern.

    Wird von der Engine abgefangen: sie schaltet dann auf den naechsten
    registrierten Adapter (Fallback-Kette) um.
    """


@dataclass(frozen=True, slots=True)
class ConsumptionSeries:
    """Normalisierte Verbrauchsreihe einer SKU.

    Attributes:
        werte: Verbrauchsmengen in chronologischer Reihenfolge, eine je
            Periode. Enthaelt mindestens zwei Elemente.
        periodenlaenge_tage: Erkannter Abstand zwischen zwei Perioden in
            Tagen (z. B. 1.0 bei Tagesdaten, ~30.0 bei Monatsdaten).
            Wird aus den Datumsangaben abgeleitet.
        datiert: ``True``, wenn die Periodenlaenge aus echten, parsbaren
            Datumsangaben ermittelt wurde, ``False`` bei Rueckfall auf die
            konfigurierte Standard-Periodenlaenge.
    """

    werte: tuple[float, ...]
    periodenlaenge_tage: float
    datiert: bool = True

    def __post_init__(self) -> None:
        if len(self.werte) < 2:
            raise ValueError("Eine Verbrauchsreihe benoetigt mindestens 2 Perioden.")
        if self.periodenlaenge_tage <= 0:
            raise ValueError("periodenlaenge_tage muss groesser als 0 sein.")

    @property
    def laenge(self) -> int:
        return len(self.werte)

    @property
    def perioden_pro_monat(self) -> float:
        """Anzahl Perioden, die auf einen 30-Tage-Monat entfallen."""
        return 30.0 / self.periodenlaenge_tage


@dataclass(frozen=True, slots=True)
class DemandForecast:
    """Ergebnis einer Prognose, normiert auf einen 30-Tage-Monat.

    Attributes:
        monatsabsatz_p50: Erwarteter Monatsabsatz (Median-Szenario).
        monatsabsatz_p90: Monatsabsatz im 90 %-Quantil (Sicherheitskorridor).
        modell: Bezeichner des Adapters, der die Prognose erzeugt hat.
        fallback: ``True``, wenn nicht das primaere Modell verwendet wurde.
    """

    monatsabsatz_p50: float
    monatsabsatz_p90: float
    modell: str
    fallback: bool = False

    def __post_init__(self) -> None:
        if self.monatsabsatz_p50 < 0:
            raise ValueError("monatsabsatz_p50 darf nicht negativ sein.")
        if self.monatsabsatz_p90 < self.monatsabsatz_p50:
            raise ValueError("monatsabsatz_p90 darf nicht unter dem P50 liegen.")


@runtime_checkable
class ForecastPort(Protocol):
    """Sekundaerer Port: liefert eine Bedarfsprognose zu einer Verbrauchsreihe."""

    name: str

    def verfuegbar(self) -> bool:
        """Ob der Adapter einsatzbereit ist (Modell geladen, Deps vorhanden)."""
        ...

    def prognose(self, serie: ConsumptionSeries) -> DemandForecast:
        """Erzeugt die auf 30 Tage normierte Prognose.

        Raises:
            ForecastUnavailable: Wenn dieser Adapter die Reihe nicht
                verarbeiten kann.
        """
        ...


def ist_forecast_port(kandidat: object) -> bool:
    """Kleine Laufzeit-Pruefung fuer Dependency-Injection aus Konfiguration."""
    return (
        hasattr(kandidat, "name")
        and callable(getattr(kandidat, "verfuegbar", None))
        and callable(getattr(kandidat, "prognose", None))
    )

