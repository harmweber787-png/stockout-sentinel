"""Dispositions-Kern: orchestriert Prognose-Port und Fachregeln.

Die Engine ist der Anwendungskern der hexagonalen Architektur. Sie ist
vollstaendig zustandslos und rein In-Memory: keine Datei-, Datenbank- oder
Netzwerkzugriffe auf Kundendaten, kein Caching zwischen Requests.

Prognosekette
-------------
**Standardbetrieb (``FORCE_TIMESFM=true``)** - TimesFM ist das zwingende
Hauptmodell:

* Die Kette besteht ausschliesslich aus dem TimesFM-Adapter.
* Der statistische Schaetzer ist nicht Teil der Kette und wird auch nicht
  zur Ergaenzung des Quantil-Korridors herangezogen.
* Schlaegt die Inferenz fehl, scheitert der Request mit
  :class:`ForecastUnavailable`. Ein stilles Ausweichen auf eine schwaechere
  Schaetzung findet nicht statt - eine Dispositionsempfehlung aus einem
  anderen Modell als dem freigegebenen waere fachlich nicht belastbar.

**Notbetrieb (``FORCE_TIMESFM=false``)** - erlaubt die frueheren Strategien
``auto`` (TimesFM mit statistischem Fallback) und ``statistisch``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence, TypeVar

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
    ForecastPfad,
    ForecastPort,
    ForecastUnavailable,
)
from src.schemas import AnalysisResult, SKUInput

__all__ = [
    "DispositionEngine",
    "PrognoseFehlgeschlagen",
    "baue_engine",
    "sortiere_prioritaeten",
]

_LOG = logging.getLogger(__name__)

#: Rueckgabetyp eines Kettendurchlaufs (Einzelprognose oder Pfad).
_T = TypeVar("_T")


class PrognoseFehlgeschlagen(ForecastUnavailable):
    """Fuer einen Artikel liess sich keine gueltige Prognose erzeugen.

    Im erzwungenen TimesFM-Modus wird dieser Fehler bewusst nach aussen
    gereicht, statt ihn durch einen statistischen Rueckfall zu ueberdecken.
    """


@dataclass(slots=True)
class DispositionEngine:
    """Berechnet Dispositionskennzahlen fuer einzelne oder viele Artikel.

    Attributes:
        prognose_kette: Geordnete Prognose-Adapter. Im erzwungenen Modus
            enthaelt sie genau einen Eintrag: den TimesFM-Adapter.
        config: Verhaltensparameter der Engine.
    """

    prognose_kette: tuple[ForecastPort, ...]
    config: EngineConfig

    def __post_init__(self) -> None:
        if not self.prognose_kette:
            raise ValueError("Die Prognosekette darf nicht leer sein.")
        if self.config.force_timesfm:
            self._pruefe_erzwungene_kette()

    def _pruefe_erzwungene_kette(self) -> None:
        """Stellt sicher, dass im Pflichtmodus kein Fallback eingeschleust wird."""
        fremd = [
            adapter
            for adapter in self.prognose_kette
            if not isinstance(adapter, TimesFMForecaster)
        ]
        if fremd:
            raise ValueError(
                "FORCE_TIMESFM ist aktiv: die Prognosekette darf ausser TimesFM "
                f"keinen weiteren Adapter enthalten (gefunden: "
                f"{', '.join(a.name for a in fremd)})."
            )

    # -- Startverhalten ----------------------------------------------------
    def starte(self) -> None:
        """Bereitet die Prognosekette vor und prueft ihre Einsatzbereitschaft.

        Im erzwungenen Modus wird das TimesFM-Checkpoint hier geladen. Das
        macht ein fehlendes oder unerreichbares Modell sofort sichtbar,
        statt erst beim ersten fachlichen Request.

        Raises:
            ForecastUnavailable: Wenn TimesFM verbindlich ist, sich aber
                nicht laden laesst.
        """
        if not self.config.force_timesfm:
            return
        for adapter in self.prognose_kette:
            if isinstance(adapter, TimesFMForecaster):
                adapter.lade_oder_scheitere()

    @property
    def modell_bereit(self) -> bool:
        """Ob alle Pflichtmodelle geladen sind."""
        if not self.config.force_timesfm:
            return True
        return all(
            adapter.modell_geladen
            for adapter in self.prognose_kette
            if isinstance(adapter, TimesFMForecaster)
        )

    # -- Oeffentliche API --------------------------------------------------
    def analysiere(self, artikel: SKUInput) -> AnalysisResult:
        """Berechnet das Dispositions-Ergebnis eines einzelnen Artikels.

        Raises:
            ValueError: Wenn die Historie weniger als zwei Perioden enthaelt.
            ForecastUnavailable: Wenn kein Adapter der Kette eine Prognose
                liefern konnte (im erzwungenen Modus: wenn TimesFM scheitert).
        """
        serie = self.baue_reihe(artikel)
        prognose = self._prognostiziere(serie)

        kennzahlen = berechne_kennzahlen(
            bestand=artikel.bestand,
            lieferzeit=artikel.lieferzeit,
            monatsabsatz_p50=prognose.monatsabsatz_p50,
            monatsabsatz_p90=prognose.monatsabsatz_p90,
            mindestbestellmenge=artikel.mindestbestellmenge or 0.0,
        )
        return self._zu_ergebnis(artikel, kennzahlen, prognose)

    def baue_reihe(self, artikel: SKUInput) -> ConsumptionSeries:
        """Normalisiert die Historie eines Artikels zur Verbrauchsreihe."""
        return baue_serie(
            ((satz.datum, satz.menge) for satz in artikel.historie),
            standard_periodenlaenge=self.config.standard_periodenlaenge_tage,
        )

    def prognose_pfad(self, artikel: SKUInput, perioden: int) -> ForecastPfad:
        """Liefert einen Mehrschritt-Prognosepfad zur Darstellung.

        Dient ausschliesslich der Visualisierung - die Dispositionszahlen
        stammen unveraendert aus :meth:`analysiere`. Die Regeln des
        Pflichtmodus gelten gleichermassen: scheitert TimesFM, wird nicht
        auf ein anderes Verfahren ausgewichen.

        Raises:
            ForecastUnavailable: Wenn kein Adapter der Kette einen Pfad
                liefern konnte.
            ValueError: Bei einem Horizont kleiner als 1 Periode.
        """
        if perioden < 1:
            raise ValueError("Der Prognosehorizont muss mindestens 1 Periode betragen.")
        serie = self.baue_reihe(artikel)
        return self._ueber_kette(
            lambda adapter, _position: adapter.prognose_pfad(serie, perioden)
        )

    def analysiere_batch(self, artikel: Iterable[SKUInput]) -> list[AnalysisResult]:
        """Analysiert viele Artikel und liefert die sortierte Prioritaetenliste.

        Raises:
            ForecastUnavailable: Im erzwungenen TimesFM-Modus, sobald die
                Inferenz fuer einen Artikel scheitert. Eine Prioritaetenliste,
                in der einzelne Artikel unbemerkt fehlen, waere fuer die
                Disposition gefaehrlicher als ein klarer Fehler.
        """
        ergebnisse: list[AnalysisResult] = []
        for eintrag in artikel:
            try:
                ergebnisse.append(self.analysiere(eintrag))
            except ForecastUnavailable:
                if self.config.force_timesfm:
                    raise
                _LOG.warning("SKU '%s': keine Prognose moeglich.", eintrag.sku)
            except Exception as exc:  # pragma: no cover - defensiv
                if self.config.force_timesfm:
                    raise
                _LOG.warning("SKU '%s' uebersprungen: %s", eintrag.sku, exc)
        return sortiere_prioritaeten(ergebnisse)

    # -- Prognose ----------------------------------------------------------
    def _prognostiziere(self, serie: ConsumptionSeries) -> DemandForecast:
        """Laeuft die Prognosekette ab.

        Im erzwungenen Modus besteht die Kette nur aus TimesFM; ein Fehler
        wird unveraendert weitergereicht.
        """
        def mit_adapter(adapter: ForecastPort, position: int) -> DemandForecast:
            prognose = self._ergaenze_korridor(adapter.prognose(serie), serie)
            if position == 0:
                return prognose
            return DemandForecast(
                monatsabsatz_p50=prognose.monatsabsatz_p50,
                monatsabsatz_p90=prognose.monatsabsatz_p90,
                modell=prognose.modell,
                fallback=True,
                monatsabsatz_p10=prognose.monatsabsatz_p10,
            )

        return self._ueber_kette(mit_adapter)

    def _ueber_kette(self, arbeit: Callable[[ForecastPort, int], _T]) -> _T:
        """Laeuft die Prognosekette ab und liefert das erste Ergebnis.

        Im erzwungenen Modus besteht die Kette nur aus TimesFM; ein Fehler
        wird unveraendert weitergereicht, statt ihn durch einen Rueckfall zu
        ueberdecken.

        Args:
            arbeit: Aufruf, der Adapter und dessen Position in der Kette
                entgegennimmt und das Ergebnis liefert.
        """
        letzter_fehler: str | None = None

        for position, adapter in enumerate(self.prognose_kette):
            try:
                return arbeit(adapter, position)
            except ForecastUnavailable as exc:
                if self.config.force_timesfm:
                    # Kein Ausweichen: TimesFM ist verbindlich.
                    raise
                letzter_fehler = str(exc)
                continue
            except Exception as exc:  # pragma: no cover - defensiv
                if self.config.force_timesfm:
                    raise PrognoseFehlgeschlagen(
                        f"TimesFM-Inferenz fehlgeschlagen: {type(exc).__name__}: {exc}"
                    ) from exc
                letzter_fehler = f"{type(exc).__name__}: {exc}"
                _LOG.warning("Prognose-Adapter '%s' fehlgeschlagen: %s", adapter.name, exc)
                continue

        raise PrognoseFehlgeschlagen(
            f"Kein Prognose-Adapter lieferte ein Ergebnis ({letzter_fehler})."
        )

    def _ergaenze_korridor(
        self, prognose: DemandForecast, serie: ConsumptionSeries
    ) -> DemandForecast:
        """Haengt einen statistischen P90-Korridor an, falls keiner vorliegt.

        Diese Ergaenzung ist selbst ein statistischer Rueckfall und daher im
        erzwungenen TimesFM-Modus gesperrt. Dort liefert das Modell das
        Quantilraster; fehlt es, scheitert die Inferenz bereits im Adapter.
        """
        if self.config.force_timesfm or not self.config.fallback_erlaubt:
            return prognose
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


def baue_timesfm_adapter(config: EngineConfig) -> TimesFMForecaster:
    """Baut den TimesFM-Adapter aus der Konfiguration."""
    return TimesFMForecaster(
        checkpoint=config.timesfm_checkpoint,
        model_dir=config.model_dir,
        backend=config.timesfm_backend,
        min_kontext=config.timesfm_min_kontext,
        max_kontext=config.timesfm_max_kontext,
        torch_compile=config.timesfm_torch_compile,
        pflicht=config.force_timesfm,
    )


def baue_engine(config: EngineConfig | None = None) -> DispositionEngine:
    """Factory: baut die Engine samt Prognosekette aus der Konfiguration.

    Bei ``force_timesfm`` besteht die Kette ausschliesslich aus dem
    TimesFM-Adapter - der statistische Schaetzer wird gar nicht erst
    instanziiert, damit er auch nicht versehentlich greifen kann.
    """
    config = config or lade_config()
    timesfm = baue_timesfm_adapter(config)

    if config.force_timesfm or config.prognose_strategie == "timesfm":
        return DispositionEngine(prognose_kette=(timesfm,), config=config)

    statistisch = StatisticalForecaster(
        daempfung=config.trend_daempfung,
        min_variationskoeffizient=config.min_variationskoeffizient,
    )
    if config.prognose_strategie == "statistisch":
        kette: tuple[ForecastPort, ...] = (statistisch,)
    else:
        kette = (timesfm, statistisch)

    return DispositionEngine(prognose_kette=kette, config=config)
