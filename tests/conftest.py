"""Gemeinsame Test-Fixtures.

Alle Fixtures sind reine In-Memory-Objekte. Die Testsuite liest und
schreibt bewusst keine Dateien: es gibt weder Beispiel-CSVs noch
hinterlegte Stammdaten.
"""

from __future__ import annotations

import pytest

from src.config import EngineConfig
from src.engine import DispositionEngine, baue_engine
from src.schemas import SKUInput


@pytest.fixture()
def config() -> EngineConfig:
    """Deterministische Konfiguration: rein statistische Prognose.

    Damit haengen die Tests weder vom Vorhandensein des TimesFM-Pakets
    noch von Modellgewichten oder Netzwerkzugriff ab.
    """
    return EngineConfig(prognose_strategie="statistisch")


@pytest.fixture()
def engine(config: EngineConfig) -> DispositionEngine:
    """Engine mit deterministischer Prognosekette."""
    return baue_engine(config)


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
