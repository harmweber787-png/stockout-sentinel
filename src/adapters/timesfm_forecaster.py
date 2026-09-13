"""Adapter: Inferenz gegen das TimesFM-Foundation-Model.

TimesFM (Google Research) ist ein vortrainiertes Zeitreihen-Modell, das
Nullshot-Prognosen inklusive Quantilen liefert. Der Adapter bindet es als
*optionale* Technologie an: Das Paket wird erst beim ersten Aufruf geladen.
Fehlt es, laesst sich das Checkpoint nicht laden oder ist die Historie zu
kurz, wird :class:`ForecastUnavailable` ausgeloest und die Engine schaltet
auf den statistischen Schaetzer um. Der Fachkern bleibt davon unberuehrt.

Konfiguration ueber Umgebungsvariablen (siehe ``src/config.py``):
    STOCKOUT_TIMESFM_CHECKPOINT   Repo-ID oder Pfad des Checkpoints
    STOCKOUT_TIMESFM_BACKEND      "cpu" | "gpu" | "tpu"
    STOCKOUT_TIMESFM_MIN_KONTEXT  Mindestanzahl Perioden fuer eine Inferenz
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

import numpy as np

from src.ports.forecasting import (
    ConsumptionSeries,
    DemandForecast,
    ForecastUnavailable,
)

__all__ = ["TimesFMForecaster"]

_LOG = logging.getLogger(__name__)

#: Quantilraster, das TimesFM ausgibt (Index 0 = Mittelwert, 1..9 = 0.1..0.9).
_QUANTILE = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)

#: Zuordnung Periodenlaenge (Tage) -> TimesFM-Frequenzindikator.
#: 0 = hochfrequent (taeglich/stuendlich), 1 = mittel (woechentlich/monatlich),
#: 2 = niederfrequent (quartals-/jahresweise).
def _frequenz_indikator(periodenlaenge_tage: float) -> int:
    if periodenlaenge_tage <= 1.5:
        return 0
    if periodenlaenge_tage <= 45.0:
        return 1
    return 2


@dataclass
class TimesFMForecaster:
    """Prognose-Adapter fuer das TimesFM-Modell (lazy geladen, thread-safe).

    Attributes:
        checkpoint: Repo-ID oder lokaler Pfad der Modellgewichte.
        backend: Rechen-Backend ("cpu", "gpu", "tpu").
        min_kontext: Mindestlaenge der Historie. Darunter ist der
            statistische Schaetzer verlaesslicher als eine Nullshot-Inferenz.
        horizon_len: Prognosehorizont in Perioden (wir benoetigen 1).
    """

    checkpoint: str = "google/timesfm-2.0-500m-pytorch"
    backend: str = "cpu"
    min_kontext: int = 8
    horizon_len: int = 1
    name: str = "timesfm"

    _modell: object | None = field(default=None, init=False, repr=False)
    _geladen: bool = field(default=False, init=False, repr=False)
    _fehler: str | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    # -- Lebenszyklus ------------------------------------------------------
    def verfuegbar(self) -> bool:
        """Ob das Modell geladen werden konnte. Loest das Laden bei Bedarf aus."""
        return self._lade_modell() is not None

    def _lade_modell(self) -> object | None:
        if self._geladen:
            return self._modell
        with self._lock:
            if self._geladen:
                return self._modell
            self._geladen = True
            try:
                self._modell = self._baue_modell()
                _LOG.info("TimesFM-Checkpoint '%s' geladen.", self.checkpoint)
            except Exception as exc:  # pragma: no cover - umgebungsabhaengig
                self._modell = None
                self._fehler = f"{type(exc).__name__}: {exc}"
                _LOG.info(
                    "TimesFM nicht verfuegbar (%s) - statistischer Fallback aktiv.",
                    self._fehler,
                )
            return self._modell

    def _baue_modell(self) -> object:  # pragma: no cover - benoetigt timesfm
        """Instanziiert TimesFM ueber die jeweils vorhandene Paket-API."""
        import timesfm  # type: ignore[import-not-found]

        # TimesFM 2.x: Hparams + Checkpoint-Objekte.
        hparams_cls = getattr(timesfm, "TimesFmHparams", None)
        checkpoint_cls = getattr(timesfm, "TimesFmCheckpoint", None)
        if hparams_cls is not None and checkpoint_cls is not None:
            return timesfm.TimesFm(
                hparams=hparams_cls(
                    backend=self.backend,
                    per_core_batch_size=32,
                    horizon_len=self.horizon_len,
                ),
                checkpoint=checkpoint_cls(huggingface_repo_id=self.checkpoint),
            )

        # TimesFM 1.x: flache Signatur.
        modell = timesfm.TimesFm(
            backend=self.backend,
            per_core_batch_size=32,
            horizon_len=self.horizon_len,
        )
        modell.load_from_checkpoint(repo_id=self.checkpoint)
        return modell

    # -- Inferenz ----------------------------------------------------------
    def prognose(self, serie: ConsumptionSeries) -> DemandForecast:
        if serie.laenge < self.min_kontext:
            raise ForecastUnavailable(
                f"Historie zu kurz fuer TimesFM "
                f"({serie.laenge} < {self.min_kontext} Perioden)."
            )

        modell = self._lade_modell()
        if modell is None:
            raise ForecastUnavailable(
                f"TimesFM-Modell nicht ladbar ({self._fehler or 'unbekannt'})."
            )

        kontext = [np.asarray(serie.werte, dtype=float)]
        frequenz = [_frequenz_indikator(serie.periodenlaenge_tage)]

        try:
            punkt, quantile = modell.forecast(kontext, freq=frequenz)  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover - umgebungsabhaengig
            raise ForecastUnavailable(f"TimesFM-Inferenz fehlgeschlagen: {exc}") from exc

        p50, p90 = self._extrahiere_quantile(punkt, quantile)

        faktor = serie.perioden_pro_monat
        p50 = max(0.0, p50)
        p90 = max(p50, p90)
        return DemandForecast(
            monatsabsatz_p50=p50 * faktor,
            monatsabsatz_p90=p90 * faktor,
            modell=f"{self.name}:{self.checkpoint}",
        )

    @staticmethod
    def _extrahiere_quantile(punkt: object, quantile: object) -> tuple[float, float]:
        """Liest P50/P90 der ersten Prognoseperiode aus der TimesFM-Ausgabe.

        Die Quantilausgabe hat die Form ``[batch, horizon, 10]`` mit dem
        Mittelwert an Index 0 und den Quantilen 0.1 .. 0.9 an 1 .. 9.
        Weicht eine Paketversion davon ab, wird auf die Punktprognose
        zurueckgefallen.
        """
        punkt_arr = np.asarray(punkt, dtype=float)
        p50 = float(punkt_arr.reshape(punkt_arr.shape[0], -1)[0, 0])

        try:
            q = np.asarray(quantile, dtype=float)
            if q.ndim == 3 and q.shape[-1] >= len(_QUANTILE) + 1:
                reihe = q[0, 0]
                p50 = float(reihe[1 + _QUANTILE.index(0.5)])
                p90 = float(reihe[1 + _QUANTILE.index(0.9)])
                return p50, p90
        except (ValueError, IndexError, TypeError):  # pragma: no cover
            _LOG.debug("Quantilausgabe von TimesFM nicht interpretierbar.")

        # Ohne Quantile bleibt nur die Punktprognose; der Sicherheits-
        # korridor kollabiert dann und die Engine ergaenzt ihn statistisch.
        return p50, p50
