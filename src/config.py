"""Zentrale Laufzeit-Konfiguration (12-Factor: alles ueber Environment).

Die Anwendung haelt keinen Zustand und liest keine Stammdaten von der
Platte. Konfiguriert wird ausschliesslich das *Verhalten* der Engine.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

__all__ = ["EngineConfig", "lade_config"]


def _env_float(name: str, standard: float) -> float:
    rohwert = os.getenv(name)
    if rohwert is None or not rohwert.strip():
        return standard
    try:
        return float(rohwert)
    except ValueError:
        return standard


def _env_int(name: str, standard: int) -> int:
    rohwert = os.getenv(name)
    if rohwert is None or not rohwert.strip():
        return standard
    try:
        return int(rohwert)
    except ValueError:
        return standard


@dataclass(frozen=True, slots=True)
class EngineConfig:
    """Stellschrauben der Prognose- und Dispositionslogik.

    Attributes:
        prognose_strategie: ``"auto"`` (TimesFM mit statistischem Fallback),
            ``"timesfm"`` (nur TimesFM) oder ``"statistisch"``.
        timesfm_checkpoint: Repo-ID/Pfad der TimesFM-Gewichte.
        timesfm_backend: Rechen-Backend fuer TimesFM.
        timesfm_min_kontext: Mindestlaenge der Historie fuer TimesFM.
        standard_periodenlaenge_tage: Fallback-Kadenz, wenn sich aus den
            Datumsangaben keine ableiten laesst.
        min_variationskoeffizient: Untergrenze der relativen Nachfrage-
            streuung fuer den Sicherheitskorridor.
        trend_daempfung: Daempfung der Trendfortschreibung.
        max_sku_pro_request: Schutzgrenze gegen ueberdimensionierte Batches.
    """

    prognose_strategie: str = "auto"
    timesfm_checkpoint: str = "google/timesfm-2.0-500m-pytorch"
    timesfm_backend: str = "cpu"
    timesfm_min_kontext: int = 8
    standard_periodenlaenge_tage: float = 30.0
    min_variationskoeffizient: float = 0.10
    trend_daempfung: float = 0.7
    max_sku_pro_request: int = 20_000


def lade_config() -> EngineConfig:
    """Baut die Konfiguration aus den Umgebungsvariablen."""
    strategie = (os.getenv("STOCKOUT_PROGNOSE_STRATEGIE") or "auto").strip().lower()
    if strategie not in {"auto", "timesfm", "statistisch"}:
        strategie = "auto"

    return EngineConfig(
        prognose_strategie=strategie,
        timesfm_checkpoint=(
            os.getenv("STOCKOUT_TIMESFM_CHECKPOINT")
            or "google/timesfm-2.0-500m-pytorch"
        ),
        timesfm_backend=(os.getenv("STOCKOUT_TIMESFM_BACKEND") or "cpu").strip().lower(),
        timesfm_min_kontext=_env_int("STOCKOUT_TIMESFM_MIN_KONTEXT", 8),
        standard_periodenlaenge_tage=_env_float("STOCKOUT_PERIODENLAENGE_TAGE", 30.0),
        min_variationskoeffizient=_env_float("STOCKOUT_MIN_VARIATIONSKOEFF", 0.10),
        trend_daempfung=_env_float("STOCKOUT_TREND_DAEMPFUNG", 0.7),
        max_sku_pro_request=_env_int("STOCKOUT_MAX_SKU_PRO_REQUEST", 20_000),
    )
