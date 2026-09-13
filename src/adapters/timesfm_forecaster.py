"""Adapter: Inferenz gegen das TimesFM-Foundation-Model von Google.

TimesFM ist das **Hauptmodell** des Dispositions-Service. Bei
``FORCE_TIMESFM=true`` (Standard) laeuft jede Prognose ueber dieses Modell;
ein statistischer Rueckfall ist dann vollstaendig blockiert.

Bezugsquelle der Gewichte (in dieser Reihenfolge):

1. **``MODEL_DIR``** - ein lokal vorgehaltenes Modellverzeichnis. Liegt es
   vor, hat es Vorrang. Das Container-Image backt die Gewichte beim Build
   dorthin, sodass die Laufzeit ohne jeden Netzzugang auskommt
   (``HF_HUB_OFFLINE=1``).
2. **``STOCKOUT_TIMESFM_CHECKPOINT``** - Repo-ID des Hugging-Face-Hubs oder
   ebenfalls ein lokaler Pfad.
3. Andernfalls das offizielle Google-Checkpoint
   ``google/timesfm-2.5-200m-pytorch``.

``from_pretrained`` erkennt lokale Verzeichnisse selbst, sodass beide Wege
denselben Codepfad nutzen.

Unterstuetzte Paket-Generationen:
    * **timesfm 3.x / 2.5** (bevorzugt) - ``TimesFM_2p5_200M_torch``:
      ``from_pretrained(repo_id)`` -> ``compile(ForecastConfig)`` ->
      ``forecast(horizon, inputs)``.
    * **timesfm 2.0 / 1.x** (Bestandsinstallationen) - ``TimesFm`` mit
      ``TimesFmHparams``/``TimesFmCheckpoint`` bzw. flacher Signatur.

Konfiguration (siehe ``src/config.py``):
    FORCE_TIMESFM                  TimesFM zwingend, kein Fallback
    MODEL_DIR                      lokales Modellverzeichnis (Vorrang)
    STOCKOUT_TIMESFM_CHECKPOINT    HF-Repo-ID oder lokaler Pfad
    STOCKOUT_TIMESFM_BACKEND       "cpu" | "gpu"
    STOCKOUT_TIMESFM_MIN_KONTEXT   Mindestanzahl Perioden fuer eine Inferenz
    STOCKOUT_TIMESFM_MAX_KONTEXT   Maximale Kontextlaenge
    STOCKOUT_TIMESFM_TORCH_COMPILE ``torch.compile`` aktivieren
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from src.ports.forecasting import (
    ConsumptionSeries,
    DemandForecast,
    ForecastPfad,
    ForecastUnavailable,
)

__all__ = ["TimesFMForecaster", "TimesFMNichtVerfuegbar"]

_LOG = logging.getLogger(__name__)

#: Quantilraster von TimesFM: Index 0 ist die Punktprognose, die Indizes
#: 1..9 tragen die Quantile 0.1 .. 0.9. Verifiziert gegen timesfm 3.0.2
#: (``TimesFM_2p5_200M_Definition.quantiles``, ``decode_index = 5``).
_QUANTILE = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
_INDEX_P10 = 1 + _QUANTILE.index(0.1)  # -> 1
_INDEX_P50 = 1 + _QUANTILE.index(0.5)  # -> 5
_INDEX_P90 = 1 + _QUANTILE.index(0.9)  # -> 9

#: Dateien, an denen ein lokales Modellverzeichnis erkannt wird.
_GEWICHTSDATEIEN = ("model.safetensors", "pytorch_model.bin", "model.ckpt")


class TimesFMNichtVerfuegbar(ForecastUnavailable):
    """Das TimesFM-Modell konnte nicht geladen oder nicht genutzt werden.

    Im erzwungenen Modus (``FORCE_TIMESFM``) wird dieser Fehler bewusst bis
    zum Aufrufer durchgereicht, statt ihn durch eine schwaechere Schaetzung
    zu ueberdecken.
    """


def _frequenz_indikator(periodenlaenge_tage: float) -> int:
    """TimesFM-Frequenzindikator (nur fuer die 1.x/2.0-API).

    0 = hochfrequent (taeglich), 1 = mittel (woechentlich/monatlich),
    2 = niederfrequent (quartals-/jahresweise).
    """
    if periodenlaenge_tage <= 1.5:
        return 0
    if periodenlaenge_tage <= 45.0:
        return 1
    return 2


@dataclass
class TimesFMForecaster:
    """Prognose-Adapter fuer TimesFM (thread-safe, Modell wird einmal geladen).

    Attributes:
        checkpoint: Hugging-Face-Repo-ID oder lokaler Modellpfad.
        model_dir: Lokal gebackenes Modellverzeichnis. Existiert es und
            enthaelt es Gewichte, wird es dem ``checkpoint`` vorgezogen -
            damit laeuft ein Image mit eingebackenen Gewichten vollstaendig
            offline.
        backend: Rechen-Backend ("cpu", "gpu").
        min_kontext: Mindestlaenge der Historie fuer eine Inferenz.
        max_kontext: Maximale Kontextlaenge; laengere Reihen werden auf die
            juengsten Perioden gekuerzt.
        horizon_len: Prognosehorizont der Dispositionsprognose (1 Periode).
        max_horizont: Groesster kompilierter Horizont. Begrenzt, wie weit
            ``prognose_pfad`` in die Zukunft reichen kann. TimesFM rundet
            intern auf ein Vielfaches der Output-Patchlaenge (128) auf.
        torch_compile: ``torch.compile`` fuer die Inferenz aktivieren.
        pflicht: Wenn ``True``, ist dieser Adapter das zwingende Hauptmodell.
            Fehler werden dann als harte Fehler gemeldet und nie stumm
            geschluckt.
    """

    checkpoint: str = "google/timesfm-2.5-200m-pytorch"
    model_dir: str | None = None
    backend: str = "cpu"
    min_kontext: int = 2
    max_kontext: int = 512
    horizon_len: int = 1
    max_horizont: int = 128
    torch_compile: bool = False
    pflicht: bool = True
    name: str = "timesfm"

    _modell: object | None = field(default=None, init=False, repr=False)
    _geladen: bool = field(default=False, init=False, repr=False)
    _fehler: str | None = field(default=None, init=False, repr=False)
    _api: str = field(default="", init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    # -- Bezugsquelle der Gewichte ----------------------------------------
    @staticmethod
    def _ist_modellverzeichnis(pfad: str) -> bool:
        """Ob der Pfad ein Verzeichnis mit brauchbaren Modellgewichten ist.

        Ein leeres oder halb befuelltes Verzeichnis - etwa ein Volume-Mount,
        der nicht griff - darf nicht als gueltige Quelle durchgehen, sonst
        scheitert der Ladevorgang mit einer irrefuehrenden Meldung.
        """
        if not pfad:
            return False
        verzeichnis = Path(pfad)
        if not verzeichnis.is_dir():
            return False
        return any((verzeichnis / datei).is_file() for datei in _GEWICHTSDATEIEN)

    def quelle(self) -> str:
        """Liefert den tatsaechlich verwendeten Checkpoint-Bezeichner.

        ``MODEL_DIR`` hat Vorrang, sofern das Verzeichnis existiert und
        Gewichte enthaelt; andernfalls gilt ``checkpoint``.
        """
        if self.model_dir and self._ist_modellverzeichnis(self.model_dir):
            return self.model_dir
        return self.checkpoint

    @property
    def laedt_lokal(self) -> bool:
        """Ob die Gewichte aus dem lokalen Verzeichnis kommen (Offline-Betrieb)."""
        return self.quelle() != self.checkpoint

    # -- Lebenszyklus ------------------------------------------------------
    def verfuegbar(self) -> bool:
        """Ob das Modell geladen werden konnte. Loest das Laden bei Bedarf aus."""
        return self._lade_modell() is not None

    def lade_oder_scheitere(self) -> None:
        """Laedt das Modell vorab und wirft bei Misserfolg.

        Wird beim Start des Service aufgerufen, damit ein fehlendes oder
        nicht ladbares Checkpoint sofort auffaellt und nicht erst beim
        ersten fachlichen Request.

        Raises:
            TimesFMNichtVerfuegbar: Wenn das Modell nicht ladbar ist.
        """
        if self._lade_modell() is None:
            raise TimesFMNichtVerfuegbar(
                f"TimesFM-Checkpoint '{self.quelle()}' konnte nicht geladen "
                f"werden: {self._fehler or 'unbekannter Fehler'}"
            )

    @property
    def modell_geladen(self) -> bool:
        """Ob das Modell bereits im Speicher liegt (ohne Ladeversuch)."""
        return self._modell is not None

    @property
    def ladefehler(self) -> str | None:
        """Meldung des letzten Ladeversuchs, falls er scheiterte."""
        return self._fehler

    def _lade_modell(self) -> object | None:
        if self._geladen:
            return self._modell
        with self._lock:
            if self._geladen:
                return self._modell
            self._geladen = True
            try:
                self._modell = self._baue_modell()
                _LOG.info(
                    "TimesFM-Checkpoint '%s' geladen (API: %s, Bezug: %s).",
                    self.quelle(),
                    self._api,
                    "lokal eingebacken" if self.laedt_lokal else "Hugging-Face-Hub",
                )
            except Exception as exc:
                self._modell = None
                self._fehler = f"{type(exc).__name__}: {exc}"
                melder = _LOG.error if self.pflicht else _LOG.info
                melder("TimesFM nicht verfuegbar: %s", self._fehler)
            return self._modell

    # -- Modellaufbau ------------------------------------------------------
    def _baue_modell(self) -> object:
        """Instanziiert TimesFM ueber die jeweils vorhandene Paket-API."""
        import timesfm  # noqa: PLC0415 - bewusst lazy, spart Startzeit

        if hasattr(timesfm, "TimesFM_2p5_200M_torch"):
            return self._baue_modell_2p5(timesfm)
        return self._baue_modell_legacy(timesfm)

    def _baue_modell_2p5(self, timesfm: object) -> object:
        """timesfm 3.x / 2.5: Checkpoint laden (lokal oder vom HF-Hub)."""
        self._api = "timesfm-2.5"
        klasse = timesfm.TimesFM_2p5_200M_torch  # type: ignore[attr-defined]

        # from_pretrained erkennt lokale Verzeichnisse selbst; mit
        # eingebackenen Gewichten kommt der Aufruf ohne Netzzugang aus.
        modell = klasse.from_pretrained(self.quelle(), torch_compile=self.torch_compile)

        modell.compile(
            timesfm.ForecastConfig(  # type: ignore[attr-defined]
                max_context=self.max_kontext,
                # Grosszuegig kompilieren, damit auch mehrschrittige
                # Prognosepfade ohne erneutes compile() bedient werden.
                max_horizon=max(self.horizon_len, self.max_horizont),
                normalize_inputs=True,
                use_continuous_quantile_head=True,
                # Verhindert sich kreuzende Quantile - sonst koennte das
                # P90 unter das P50 fallen und der Korridor kippen.
                fix_quantile_crossing=True,
                # Verbrauchsmengen sind nie negativ.
                infer_is_positive=True,
            )
        )
        return modell

    def _baue_modell_legacy(self, timesfm: object) -> object:
        """timesfm 2.0 / 1.x: Bestandsinstallationen weiter bedienen."""
        hparams_cls = getattr(timesfm, "TimesFmHparams", None)
        checkpoint_cls = getattr(timesfm, "TimesFmCheckpoint", None)

        if hparams_cls is not None and checkpoint_cls is not None:
            self._api = "timesfm-2.0"
            return timesfm.TimesFm(  # type: ignore[attr-defined]
                hparams=hparams_cls(
                    backend=self.backend,
                    per_core_batch_size=32,
                    horizon_len=max(self.horizon_len, self.max_horizont),
                    context_len=self.max_kontext,
                ),
                checkpoint=checkpoint_cls(huggingface_repo_id=self.quelle()),
            )

        self._api = "timesfm-1.x"
        modell = timesfm.TimesFm(  # type: ignore[attr-defined]
            backend=self.backend,
            per_core_batch_size=32,
            horizon_len=self.horizon_len,
        )
        modell.load_from_checkpoint(repo_id=self.quelle())
        return modell

    # -- Inferenz ----------------------------------------------------------
    def prognose(self, serie: ConsumptionSeries) -> DemandForecast:
        """Fuehrt die TimesFM-Inferenz aus und normiert sie auf 30 Tage.

        Raises:
            TimesFMNichtVerfuegbar: Wenn das Modell fehlt, die Historie zu
                kurz ist oder die Inferenz scheitert.
        """
        if serie.laenge < self.min_kontext:
            raise TimesFMNichtVerfuegbar(
                f"Historie zu kurz fuer TimesFM "
                f"({serie.laenge} < {self.min_kontext} Perioden)."
            )

        modell = self._lade_modell()
        if modell is None:
            raise TimesFMNichtVerfuegbar(
                f"TimesFM-Modell '{self.quelle()}' nicht ladbar "
                f"({self._fehler or 'unbekannter Fehler'})."
            )

        kontext = np.asarray(serie.werte, dtype=float)[-self.max_kontext :]

        try:
            punkt, quantile = self._inferiere(modell, kontext, serie)
        except Exception as exc:
            raise TimesFMNichtVerfuegbar(
                f"TimesFM-Inferenz fehlgeschlagen: {type(exc).__name__}: {exc}"
            ) from exc

        p50, p90 = self._extrahiere_quantile(punkt, quantile)
        p10 = float(self._pruefe_quantilraster(quantile)[0, 0, _INDEX_P10])

        faktor = serie.perioden_pro_monat
        p50 = max(0.0, p50)
        p90 = max(p50, p90)
        p10 = min(p50, max(0.0, p10)) if np.isfinite(p10) else p50
        return DemandForecast(
            monatsabsatz_p50=p50 * faktor,
            monatsabsatz_p90=p90 * faktor,
            monatsabsatz_p10=p10 * faktor,
            # Ausgewiesen wird die tatsaechlich geladene Quelle - eine
            # Dispositionsempfehlung muss nachvollziehbar machen, welche
            # Gewichte sie erzeugt haben.
            modell=f"{self.name}:{self.quelle()}",
        )

    def prognose_pfad(self, serie: ConsumptionSeries, perioden: int) -> ForecastPfad:
        """Mehrschritt-Prognose fuer die Darstellung des Korridors.

        TimesFM liefert das Quantilraster fuer jede Horizontperiode einzeln -
        der Korridor entsteht also aus dem Modell selbst und nicht aus einer
        nachtraeglichen Fortschreibung.

        Raises:
            TimesFMNichtVerfuegbar: Wenn das Modell fehlt, die Historie zu
                kurz ist oder die Inferenz scheitert.
            ValueError: Bei einem Horizont kleiner als 1 Periode.
        """
        if perioden < 1:
            raise ValueError("Der Prognosehorizont muss mindestens 1 Periode betragen.")
        if serie.laenge < self.min_kontext:
            raise TimesFMNichtVerfuegbar(
                f"Historie zu kurz fuer TimesFM "
                f"({serie.laenge} < {self.min_kontext} Perioden)."
            )

        modell = self._lade_modell()
        if modell is None:
            raise TimesFMNichtVerfuegbar(
                f"TimesFM-Modell '{self.quelle()}' nicht ladbar "
                f"({self._fehler or 'unbekannter Fehler'})."
            )

        horizont = min(perioden, self.max_horizont)
        kontext = np.asarray(serie.werte, dtype=float)[-self.max_kontext :]

        try:
            _punkt, quantile = self._inferiere(modell, kontext, serie, horizont=horizont)
        except Exception as exc:
            raise TimesFMNichtVerfuegbar(
                f"TimesFM-Inferenz fehlgeschlagen: {type(exc).__name__}: {exc}"
            ) from exc

        raster = self._pruefe_quantilraster(quantile)
        verfuegbar = min(horizont, raster.shape[1])

        p10: list[float] = []
        p50: list[float] = []
        p90: list[float] = []
        for schritt in range(verfuegbar):
            reihe = raster[0, schritt]
            median = max(0.0, float(reihe[_INDEX_P50]))
            p50.append(median)
            p90.append(max(median, float(reihe[_INDEX_P90])))
            p10.append(min(median, max(0.0, float(reihe[_INDEX_P10]))))

        return ForecastPfad(
            p10=tuple(p10),
            p50=tuple(p50),
            p90=tuple(p90),
            periodenlaenge_tage=serie.periodenlaenge_tage,
            modell=f"{self.name}:{self.quelle()}",
        )

    def _inferiere(
        self,
        modell: object,
        kontext: np.ndarray,
        serie: ConsumptionSeries,
        horizont: int = 1,
    ) -> tuple[object, object]:
        """Ruft ``forecast`` in der Signatur der jeweiligen Paket-Generation."""
        if self._api == "timesfm-2.5":
            # 2.5 kennt keinen Frequenzindikator; der Horizont ist Pflichtarg.
            return modell.forecast(horizon=horizont, inputs=[kontext])  # type: ignore[attr-defined]

        frequenz = [_frequenz_indikator(serie.periodenlaenge_tage)]
        return modell.forecast([kontext], freq=frequenz)  # type: ignore[attr-defined]

    @staticmethod
    def _pruefe_quantilraster(quantile: object) -> np.ndarray:
        """Validiert die Quantilausgabe und gibt sie als Array zurueck.

        Die Ausgabe hat die Form ``[batch, horizon, 10]``: Index 0 traegt die
        Punktprognose, die Indizes 1..9 die Quantile 0.1 .. 0.9.

        Raises:
            TimesFMNichtVerfuegbar: Wenn kein auswertbares Quantilraster
                vorliegt. Ohne P90 laesst sich kein Sicherheitsbestand
                berechnen - und im erzwungenen Modus darf er nicht
                statistisch ergaenzt werden.
        """
        quantil_array = np.asarray(quantile, dtype=float)
        if quantil_array.ndim != 3 or quantil_array.shape[-1] <= _INDEX_P90:
            raise TimesFMNichtVerfuegbar(
                "TimesFM lieferte kein auswertbares Quantilraster "
                f"(Form {quantil_array.shape}, erwartet [batch, horizon, >= "
                f"{_INDEX_P90 + 1}])."
            )
        return quantil_array

    @classmethod
    def _extrahiere_quantile(cls, punkt: object, quantile: object) -> tuple[float, float]:
        """Liest P50/P90 der ersten Prognoseperiode aus der TimesFM-Ausgabe."""
        reihe = cls._pruefe_quantilraster(quantile)[0, 0]
        p50 = float(reihe[_INDEX_P50])
        p90 = float(reihe[_INDEX_P90])

        # Die Punktprognose dient als Plausibilitaetsanker, falls das
        # P50-Quantil nicht endlich ist.
        if not np.isfinite(p50):
            punkt_array = np.asarray(punkt, dtype=float)
            p50 = float(punkt_array.reshape(punkt_array.shape[0], -1)[0, 0])
        if not np.isfinite(p90):
            p90 = p50

        return p50, p90
