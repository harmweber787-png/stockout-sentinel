"""Gemeinsame Test-Fixtures.

Alle Fixtures sind reine In-Memory-Objekte. Die Testsuite liest und
schreibt bewusst keine Dateien und geht nie ins Netz: es gibt weder
Beispiel-CSVs noch echte Modellgewichte.

Zwei Betriebsarten werden abgedeckt:

* ``engine``          - Notbetrieb (``force_timesfm=False``, statistisch).
  Prueft die Dispositionsformeln unabhaengig vom Modell.
* ``timesfm_engine``  - Pflichtmodus mit einem Modell-Double. Der Adapter
  laeuft dabei vollstaendig durch seinen echten Code (compile-Pfad
  ausgenommen), nur die Gewichte sind ersetzt.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.adapters.timesfm_forecaster import TimesFMForecaster
from src.config import EngineConfig
from src.engine import DispositionEngine, baue_engine
from src.schemas import SKUInput

#: Quantilraster von TimesFM: Index 0 = Punktprognose, 1..9 = 0.1 .. 0.9.
_QUANTIL_KANAELE = 10
_P90_AUFSCHLAG = 1.2


class FakeTimesFMModell:
    """Modell-Double mit der verifizierten TimesFM-2.5-Ausgabeform.

    Liefert ``(punkt, quantile)`` mit den Formen ``(1, 1)`` und
    ``(1, 1, 10)`` - exakt wie ``TimesFM_2p5_200M_torch.forecast``. Der
    prognostizierte Wert ist der Mittelwert der Kontextreihe, das P90
    liegt konstant darueber. Dadurch sind die Tests deterministisch.
    """

    def __init__(self, *, faktor: float = 1.0) -> None:
        self.faktor = faktor
        self.aufrufe: list[np.ndarray] = []

    def forecast(self, horizon: int, inputs: list[np.ndarray]):
        kontext = np.asarray(inputs[0], dtype=float)
        self.aufrufe.append(kontext)

        p50 = float(np.mean(kontext)) * self.faktor
        reihe = np.zeros((1, 1, _QUANTIL_KANAELE), dtype=float)
        reihe[0, 0, 0] = p50                       # Punktprognose
        for index in range(1, _QUANTIL_KANAELE):   # Quantile 0.1 .. 0.9
            reihe[0, 0, index] = p50 * (0.6 + 0.075 * index)
        reihe[0, 0, 5] = p50                       # P50
        reihe[0, 0, 9] = p50 * _P90_AUFSCHLAG      # P90
        return np.array([[p50]]), reihe


class FakeTimesFMForecaster(TimesFMForecaster):
    """TimesFM-Adapter mit ersetztem Modell statt echter Gewichte.

    Erbt von :class:`TimesFMForecaster`, damit die Pflichtpruefungen der
    Engine (``isinstance``) greifen und der komplette Inferenz- und
    Quantil-Extraktionspfad des echten Adapters durchlaufen wird.
    """

    def __init__(self, *, faktor: float = 1.0, scheitert: str | None = None, **kwargs):
        kwargs.setdefault("checkpoint", "google/timesfm-2.5-200m-pytorch")
        super().__init__(**kwargs)
        self._faktor = faktor
        self._scheitert = scheitert

    def _baue_modell(self) -> object:
        if self._scheitert:
            raise RuntimeError(self._scheitert)
        self._api = "timesfm-2.5"
        return FakeTimesFMModell(faktor=self._faktor)


# ---------------------------------------------------------------------------
# Konfigurationen
# ---------------------------------------------------------------------------
@pytest.fixture()
def config() -> EngineConfig:
    """Notbetrieb: deterministische, rein statistische Prognose.

    Damit haengen die Formeltests weder von Modellgewichten noch von
    Netzwerkzugriff ab.
    """
    return EngineConfig(force_timesfm=False, prognose_strategie="statistisch")


@pytest.fixture()
def force_config() -> EngineConfig:
    """Pflichtmodus: TimesFM verbindlich, Fallback blockiert."""
    return EngineConfig(force_timesfm=True)


@pytest.fixture()
def engine(config: EngineConfig) -> DispositionEngine:
    """Engine mit deterministischer statistischer Prognosekette."""
    return baue_engine(config)


@pytest.fixture()
def timesfm_engine(force_config: EngineConfig) -> DispositionEngine:
    """Engine im Pflichtmodus mit Modell-Double."""
    return DispositionEngine(
        prognose_kette=(FakeTimesFMForecaster(min_kontext=force_config.timesfm_min_kontext),),
        config=force_config,
    )


def baue_sku(
    sku: str = "TEST-SKU",
    *,
    mengen: list[float] | None = None,
    bestand: float = 0.0,
    lieferzeit: int = 10,
    mindestbestellmenge: float = 0.0,
) -> SKUInput:
    """Baut einen Artikel mit monatlicher Historie aus den uebergebenen Mengen."""
    mengen = [100.0, 100.0, 100.0] if mengen is None else mengen
    historie = [
        {"datum": f"2024-{monat:02d}-01", "menge": menge}
        for monat, menge in enumerate(mengen, start=1)
    ]
    return SKUInput(
        sku=sku,
        historie=historie,
        bestand=bestand,
        lieferzeit=lieferzeit,
        mindestbestellmenge=mindestbestellmenge,
    )


@pytest.fixture()
def sku_factory():
    """Stellt :func:`baue_sku` als Fixture bereit."""
    return baue_sku
