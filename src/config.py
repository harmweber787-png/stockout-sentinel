"""Zentrale Laufzeit-Konfiguration (12-Factor: alles ueber Environment).

Die Anwendung haelt keinen Zustand und liest keine Stammdaten von der
Platte. Konfiguriert wird ausschliesslich das *Verhalten* der Engine.

Standardbetrieb: ``FORCE_TIMESFM=True``. TimesFM ist damit das zwingende
Hauptmodell - der Service laedt beim Start das echte Google-Checkpoint von
Hugging Face und verweigert den Betrieb, wenn das nicht gelingt. Ein
statistischer Rueckfall findet in diesem Modus nicht statt.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

__all__ = [
    "FORCE_TIMESFM",
    "MODEL_DIR_STANDARD",
    "TIMESFM_HF_CHECKPOINT",
    "EngineConfig",
    "lade_config",
]

#: Vorgabe des Fachbereichs: TimesFM ist verbindlich, kein Fallback.
#: Ueber die Umgebungsvariable ``FORCE_TIMESFM`` abschaltbar (Notbetrieb).
FORCE_TIMESFM = True

#: Offizielles Hugging-Face-Checkpoint von Google fuer TimesFM 2.5.
TIMESFM_HF_CHECKPOINT = "google/timesfm-2.5-200m-pytorch"

#: Standardpfad der im Container-Image eingebackenen Modellgewichte.
#: Existiert das Verzeichnis, laedt der Adapter daraus - und braucht
#: keinerlei Netzzugang (HF_HUB_OFFLINE=1).
MODEL_DIR_STANDARD = "/app/models/timesfm-checkpoint"

_WAHR = {"1", "true", "yes", "y", "on", "ja", "wahr"}
_FALSCH = {"0", "false", "no", "n", "off", "nein", "falsch"}


def _env_bool(name: str, standard: bool) -> bool:
    rohwert = os.getenv(name)
    if rohwert is None or not rohwert.strip():
        return standard
    normalisiert = rohwert.strip().lower()
    if normalisiert in _WAHR:
        return True
    if normalisiert in _FALSCH:
        return False
    return standard


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
        force_timesfm: Wenn ``True`` (Standard), ist TimesFM das zwingende
            Hauptmodell: die Prognosekette besteht ausschliesslich aus dem
            TimesFM-Adapter, das Modell wird beim Start geladen und jeder
            statistische Rueckfall ist blockiert. Schlaegt die Inferenz
            fehl, scheitert der Request - er wird nicht stillschweigend
            durch eine schwaechere Schaetzung ersetzt.
        prognose_strategie: Nur wirksam, wenn ``force_timesfm`` abgeschaltet
            ist. ``"auto"`` (TimesFM mit statistischem Fallback),
            ``"timesfm"`` oder ``"statistisch"``.
        timesfm_checkpoint: Repo-ID des Hugging-Face-Checkpoints oder Pfad
            eines lokal vorgehaltenen Modellverzeichnisses.
        model_dir: Verzeichnis der eingebackenen Modellgewichte. Liegt es
            vor, hat es Vorrang vor ``timesfm_checkpoint`` - damit laeuft
            ein autarkes Image ohne Netzzugang. Fehlt es, faellt die
            Aufloesung auf das Checkpoint zurueck.
        timesfm_backend: Rechen-Backend fuer TimesFM ("cpu", "gpu").
        timesfm_min_kontext: Mindestlaenge der Historie fuer TimesFM. Im
            erzwungenen Modus ist der Standard 2 (das Schema-Minimum),
            damit wirklich jeder Artikel ueber TimesFM laeuft - das Modell
            padded kuerzere Kontexte selbst.
        timesfm_max_kontext: Maximale Kontextlaenge der Inferenz.
        timesfm_torch_compile: ``torch.compile`` fuer die Inferenz nutzen.
            Beschleunigt den Dauerbetrieb, verlaengert den ersten Aufruf.
        standard_periodenlaenge_tage: Fallback-Kadenz, wenn sich aus den
            Datumsangaben keine ableiten laesst.
        min_variationskoeffizient: Untergrenze der relativen Nachfrage-
            streuung fuer den Sicherheitskorridor (nur statistischer Pfad).
        trend_daempfung: Daempfung der Trendfortschreibung (nur statistisch).
        max_sku_pro_request: Schutzgrenze gegen ueberdimensionierte Batches.
    """

    force_timesfm: bool = FORCE_TIMESFM
    prognose_strategie: str = "auto"
    timesfm_checkpoint: str = TIMESFM_HF_CHECKPOINT
    model_dir: str = MODEL_DIR_STANDARD
    timesfm_backend: str = "cpu"
    timesfm_min_kontext: int = 2
    timesfm_max_kontext: int = 512
    timesfm_torch_compile: bool = False
    standard_periodenlaenge_tage: float = 30.0
    min_variationskoeffizient: float = 0.10
    trend_daempfung: float = 0.7
    max_sku_pro_request: int = 20_000

    @property
    def fallback_erlaubt(self) -> bool:
        """Ob ein nicht-neuronaler Rueckfall ueberhaupt zulaessig ist."""
        return not self.force_timesfm and self.prognose_strategie != "timesfm"


def lade_config() -> EngineConfig:
    """Baut die Konfiguration aus den Umgebungsvariablen."""
    force = _env_bool("FORCE_TIMESFM", FORCE_TIMESFM)

    strategie = (os.getenv("STOCKOUT_PROGNOSE_STRATEGIE") or "auto").strip().lower()
    if strategie not in {"auto", "timesfm", "statistisch"}:
        strategie = "auto"
    if force:
        # Im erzwungenen Modus ist die Strategie nicht verhandelbar.
        strategie = "timesfm"

    return EngineConfig(
        force_timesfm=force,
        prognose_strategie=strategie,
        timesfm_checkpoint=(
            os.getenv("STOCKOUT_TIMESFM_CHECKPOINT") or TIMESFM_HF_CHECKPOINT
        ),
        model_dir=(os.getenv("MODEL_DIR") or MODEL_DIR_STANDARD).strip(),
        timesfm_backend=(os.getenv("STOCKOUT_TIMESFM_BACKEND") or "cpu").strip().lower(),
        timesfm_min_kontext=_env_int("STOCKOUT_TIMESFM_MIN_KONTEXT", 2 if force else 8),
        timesfm_max_kontext=_env_int("STOCKOUT_TIMESFM_MAX_KONTEXT", 512),
        timesfm_torch_compile=_env_bool("STOCKOUT_TIMESFM_TORCH_COMPILE", False),
        standard_periodenlaenge_tage=_env_float("STOCKOUT_PERIODENLAENGE_TAGE", 30.0),
        min_variationskoeffizient=_env_float("STOCKOUT_MIN_VARIATIONSKOEFF", 0.10),
        trend_daempfung=_env_float("STOCKOUT_TREND_DAEMPFUNG", 0.7),
        max_sku_pro_request=_env_int("STOCKOUT_MAX_SKU_PRO_REQUEST", 20_000),
    )
