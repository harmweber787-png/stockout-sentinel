"""Dispositions-Kern: orchestriert Prognose-Port und Fachregeln.

Die Engine ist der Anwendungskern der hexagonalen Architektur. Sie ist
vollstaendig zustandslos und rein In-Memory: keine Datei-, Datenbank- oder
Netzwerkzugriffe, kein Caching von Kundendaten zwischen Requests. Ein
Analyselauf haengt ausschliesslich von den uebergebenen Daten ab.

Prognosekette (Strategie ``auto``):
    1. TimesFM-Inferenz ueber den Foundation-Model-Adapter.
    2. Bei fehlendem Paket, Ladefehler, zu kurzer Historie oder Inferenz-
       fehler: robuster statistischer Schaetzer (Trend + P90-Korridor).
    3. Liefert das primaere Modell keinen Quantil-Korridor (P90 == P50),
       wird der Korridor aus dem statistischen Schaetzer ergaenzt, damit
       der Sicherheitsbestand nicht auf 0 kollabiert.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Sequence

from src.adapters.statistical_forecaster import StatisticalForecaster
from src.adapters.timesfm_forecaster import TimesFMForecaster
from src.config import EngineConfig, lade_config
from src.domain.disposition import (
    Dispositionskennzahlen,
    Status,
    berechne_kennzahlen,
    empfohlene_massnahme,
)
from src.domain.timeseries import baue_serie
from src.ports.forecasting import (
    ConsumptionSeries,
    DemandForecast,
    ForecastPort,
    ForecastUnavailable,
)
from src.schemas import AnalysisResult, SKUInput

__all__ = ["DispositionEngine", "baue_engine", "sortiere_prioritaeten"]

_LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class DispositionEngine:
    """Berechnet Dispositionskennzahlen fuer einzelne oder viele Artikel.

    Attributes:
        prognose_kette: Geordnete Prognose-Adapter. Der erste Adapter, der
            ein Ergebnis liefert, gewinnt; die uebrigen sind Fallbacks.
        config: Verhaltensparameter der Engine.
    """

    prognose_kette: tuple[ForecastPort, ...]
    config: EngineConfig

    def __post_init__(self) -> None:
        if not self.prognose_kette:
            raise ValueError("Die Prognosekette darf nicht leer sein.")

    # -- Oeffentliche API --------------------------------------------------
    def analysiere(self, artikel: SKUInput) -> AnalysisResult:
        """Berechnet das Dispositions-Ergebnis eines einzelnen Artikels.

        Raises:
            ValueError: Wenn die Historie weniger als zwei Perioden enthaelt.
        """
        serie = baue_serie(
            ((satz.datum, satz.menge) for satz in artikel.historie),
            standard_periodenlaenge=self.config.standard_periodenlaenge_tage,
        )
        prognose = self._prognostiziere(serie)

        kennzahlen = berechne_kennzahlen(
            bestand=artikel.bestand,
            lieferzeit=artikel.lieferzeit,
            monatsabsatz_p50=prognose.monatsabsatz_p50,
            monatsabsatz_p90=prognose.monatsabsatz_p90,
            mindestbestellmenge=artikel.mindestbestellmenge or 0.0,
        )
        return self._zu_ergebnis(artikel, kennzahlen, prognose)

    def analysiere_batch(self, artikel: Iterable[SKUInput]) -> list[AnalysisResult]:
        """Analysiert viele Artikel und liefert die sortierte Prioritaetenliste.

        Einzelne fehlerhafte Artikel brechen den Lauf nicht ab; sie werden
        uebersprungen und protokolliert. Vollstaendige Fehlerlisten liefert
        die API-Schicht.
        """
        ergebnisse: list[AnalysisResult] = []
        for eintrag in artikel:
            try:
                ergebnisse.append(self.analysiere(eintrag))
            except Exception as exc:  # pragma: no cover - defensiv
                _LOG.warning("SKU '%s' uebersprungen: %s", eintrag.sku, exc)
        return sortiere_prioritaeten(ergebnisse)

    # -- Prognose ----------------------------------------------------------
    def _prognostiziere(self, serie: ConsumptionSeries) -> DemandForecast:
        """Laeuft die Prognosekette ab und ergaenzt fehlende Quantile."""
        letzter_fehler: str | None = None

        for position, adapter in enumerate(self.prognose_kette):
            try:
                prognose = adapter.prognose(serie)
            except ForecastUnavailable as exc:
                letzter_fehler = str(exc)
                continue
            except Exception as exc:  # pragma: no cover - defensiv
                letzter_fehler = f"{type(exc).__name__}: {exc}"
                _LOG.warning("Prognose-Adapter '%s' fehlgeschlagen: %s", adapter.name, exc)
                continue

            prognose = self._ergaenze_korridor(prognose, serie)
            if position > 0:
                prognose = DemandForecast(
                    monatsabsatz_p50=prognose.monatsabsatz_p50,
                    monatsabsatz_p90=prognose.monatsabsatz_p90,
                    modell=prognose.modell,
                    fallback=True,
                )
            return prognose

        raise ForecastUnavailable(
            f"Kein Prognose-Adapter lieferte ein Ergebnis ({letzter_fehler})."
        )

    def _ergaenze_korridor(
        self, prognose: DemandForecast, serie: ConsumptionSeries
    ) -> DemandForecast:
        """Haengt einen statistischen P90-Korridor an, falls keiner vorliegt."""
        if prognose.monatsabsatz_p90 > prognose.monatsabsatz_p50:
            return prognose

        statistisch = self._statistischer_adapter()
        if statistisch is None:
            return prognose

        referenz = statistisch.prognose(serie)
        korridor = max(0.0, referenz.monatsabsatz_p90 - referenz.monatsabsatz_p50)
        if korridor <= 0:
            return prognose

        return DemandForecast(
            monatsabsatz_p50=prognose.monatsabsatz_p50,
            monatsabsatz_p90=prognose.monatsabsatz_p50 + korridor,
            modell=f"{prognose.modell}+korridor",
            fallback=prognose.fallback,
        )

    def _statistischer_adapter(self) -> ForecastPort | None:
        for adapter in self.prognose_kette:
            if isinstance(adapter, StatisticalForecaster):
                return adapter
        return None

    # -- Abbildung ---------------------------------------------------------
    @staticmethod
    def _zu_ergebnis(
        artikel: SKUInput,
        kennzahlen: Dispositionskennzahlen,
        prognose: DemandForecast,
    ) -> AnalysisResult:
        return AnalysisResult(
            sku=artikel.sku,
            prognose_tagesbedarf=kennzahlen.prognose_tagesbedarf,
            reichweite_tage=kennzahlen.reichweite_tage,
            meldebestand=kennzahlen.meldebestand,
            nachbestellmenge=kennzahlen.nachbestellmenge,
            status=kennzahlen.status.value,
            status_code=kennzahlen.status.code,  # type: ignore[arg-type]
            empfohlene_massnahme=empfohlene_massnahme(kennzahlen, artikel.lieferzeit),
            sicherheitsbestand=kennzahlen.sicherheitsbestand,
            prognose_tagesbedarf_p90=kennzahlen.prognose_tagesbedarf_p90,
            prognose_modell=prognose.modell,
            prognose_fallback=prognose.fallback,
        )


def sortiere_prioritaeten(ergebnisse: Sequence[AnalysisResult]) -> list[AnalysisResult]:
    """Sortiert zur Prioritaetenliste: kritisch und knapp zuerst.

    Sortierschluessel:
        1. Ampel-Rang (KRITISCH < OPTIMAL < UEBERBESTAND),
        2. Reichweite aufsteigend (geringste Reichweite zuerst),
        3. Nachbestellmenge absteigend (groesster Handlungsbedarf zuerst),
        4. SKU alphabetisch (stabile, reproduzierbare Reihenfolge).
    """
    rang = {status.code: status.prioritaet for status in Status}
    return sorted(
        ergebnisse,
        key=lambda e: (
            rang.get(e.status_code, 99),
            e.reichweite_tage,
            -e.nachbestellmenge,
            e.sku,
        ),
    )


def baue_engine(config: EngineConfig | None = None) -> DispositionEngine:
    """Factory: baut die Engine samt Prognosekette aus der Konfiguration."""
    config = config or lade_config()

    statistisch = StatisticalForecaster(
        daempfung=config.trend_daempfung,
        min_variationskoeffizient=config.min_variationskoeffizient,
    )
    timesfm = TimesFMForecaster(
        checkpoint=config.timesfm_checkpoint,
        backend=config.timesfm_backend,
        min_kontext=config.timesfm_min_kontext,
    )

    if config.prognose_strategie == "statistisch":
        kette: tuple[ForecastPort, ...] = (statistisch,)
    elif config.prognose_strategie == "timesfm":
        kette = (timesfm,)
    else:
        kette = (timesfm, statistisch)

    return DispositionEngine(prognose_kette=kette, config=config)
