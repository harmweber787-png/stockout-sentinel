"""Tests des Pflichtmodus: TimesFM als zwingendes Hauptmodell.

Geprueft wird die Zusage aus der Vorgabe: bei ``FORCE_TIMESFM`` laeuft jede
Prognose ueber TimesFM und **jeder** Rueckfall auf ein anderes Verfahren ist
blockiert - auch der stille, teilweise Rueckfall ueber den Quantil-Korridor.

Die Tests benoetigen weder Netzwerk noch Modellgewichte: der Adapter laeuft
gegen ein Modell-Double mit der verifizierten TimesFM-2.5-Ausgabeform.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.adapters.statistical_forecaster import StatisticalForecaster
from src.adapters.timesfm_forecaster import TimesFMForecaster, TimesFMNichtVerfuegbar
from src.api import erstelle_app
from src.config import FORCE_TIMESFM, EngineConfig, lade_config
from src.domain.timeseries import baue_serie
from src.engine import DispositionEngine, baue_engine
from src.ports.forecasting import ForecastUnavailable
from tests.conftest import FakeTimesFMForecaster, baue_sku


class TestKonfiguration:
    """Der Schalter steht standardmaessig auf 'zwingend'."""

    def test_force_timesfm_ist_standardmaessig_an(self) -> None:
        assert FORCE_TIMESFM is True
        assert EngineConfig().force_timesfm is True

    def test_standardkonfiguration_erlaubt_keinen_fallback(self) -> None:
        config = EngineConfig()

        assert config.fallback_erlaubt is False
        assert config.prognose_strategie in {"auto", "timesfm"}

    def test_umgebung_ohne_variable_bleibt_im_pflichtmodus(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FORCE_TIMESFM", raising=False)

        config = lade_config()

        assert config.force_timesfm is True
        # Im Pflichtmodus ist die Strategie nicht verhandelbar.
        assert config.prognose_strategie == "timesfm"
        assert config.fallback_erlaubt is False

    def test_checkpoint_zeigt_auf_das_google_hf_repo(self) -> None:
        assert lade_config().timesfm_checkpoint == "google/timesfm-2.5-200m-pytorch"

    @pytest.mark.parametrize("rohwert", ["false", "0", "no", "off", "nein"])
    def test_notbetrieb_laesst_sich_explizit_einschalten(
        self, monkeypatch: pytest.MonkeyPatch, rohwert: str
    ) -> None:
        monkeypatch.setenv("FORCE_TIMESFM", rohwert)

        assert lade_config().force_timesfm is False

    @pytest.mark.parametrize("rohwert", ["true", "1", "yes", "ja", "on"])
    def test_pflichtmodus_bleibt_bei_wahren_werten(
        self, monkeypatch: pytest.MonkeyPatch, rohwert: str
    ) -> None:
        monkeypatch.setenv("FORCE_TIMESFM", rohwert)

        assert lade_config().force_timesfm is True

    def test_strategie_umgebungsvariable_kann_pflicht_nicht_aushebeln(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Auch 'statistisch' darf den Pflichtmodus nicht unterlaufen."""
        monkeypatch.delenv("FORCE_TIMESFM", raising=False)
        monkeypatch.setenv("STOCKOUT_PROGNOSE_STRATEGIE", "statistisch")

        config = lade_config()

        assert config.force_timesfm is True
        assert config.prognose_strategie == "timesfm"
        assert config.fallback_erlaubt is False

    def test_min_kontext_erlaubt_im_pflichtmodus_kurze_historien(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sonst wuerden kurze Historien am Modell vorbeilaufen bzw. scheitern."""
        monkeypatch.delenv("FORCE_TIMESFM", raising=False)
        monkeypatch.delenv("STOCKOUT_TIMESFM_MIN_KONTEXT", raising=False)

        assert lade_config().timesfm_min_kontext == 2


class TestPrognosekette:
    """Im Pflichtmodus besteht die Kette ausschliesslich aus TimesFM."""

    def test_kette_enthaelt_nur_timesfm(self) -> None:
        engine = baue_engine(EngineConfig())

        assert len(engine.prognose_kette) == 1
        assert isinstance(engine.prognose_kette[0], TimesFMForecaster)

    def test_statistischer_schaetzer_ist_nicht_in_der_kette(self) -> None:
        engine = baue_engine(EngineConfig())

        assert not any(
            isinstance(adapter, StatisticalForecaster)
            for adapter in engine.prognose_kette
        )

    def test_eingeschleuster_fallback_wird_abgelehnt(self) -> None:
        """Ein zusaetzlicher Adapter darf sich nicht in die Kette schmuggeln."""
        with pytest.raises(ValueError, match="FORCE_TIMESFM"):
            DispositionEngine(
                prognose_kette=(FakeTimesFMForecaster(), StatisticalForecaster()),
                config=EngineConfig(),
            )

    def test_rein_statistische_kette_wird_abgelehnt(self) -> None:
        with pytest.raises(ValueError, match="FORCE_TIMESFM"):
            DispositionEngine(
                prognose_kette=(StatisticalForecaster(),), config=EngineConfig()
            )

    def test_notbetrieb_erlaubt_die_gemischte_kette_weiterhin(self) -> None:
        engine = baue_engine(
            EngineConfig(force_timesfm=False, prognose_strategie="auto")
        )

        assert [type(a).__name__ for a in engine.prognose_kette] == [
            "TimesFMForecaster",
            "StatisticalForecaster",
        ]


class TestFallbackBlockade:
    """Scheitert TimesFM, scheitert der Request - kein Ersatzmodell."""

    def test_prognose_scheitert_statt_auszuweichen(self) -> None:
        engine = DispositionEngine(
            prognose_kette=(FakeTimesFMForecaster(scheitert="Checkpoint fehlt"),),
            config=EngineConfig(),
        )

        with pytest.raises(ForecastUnavailable, match="nicht ladbar"):
            engine.analysiere(baue_sku(mengen=[100.0, 110.0, 120.0], bestand=50.0))

    def test_batch_scheitert_statt_artikel_stillschweigend_zu_verlieren(self) -> None:
        """Eine Prioritaetenliste mit unbemerkt fehlenden Artikeln waere gefaehrlich."""
        engine = DispositionEngine(
            prognose_kette=(FakeTimesFMForecaster(scheitert="Checkpoint fehlt"),),
            config=EngineConfig(),
        )

        with pytest.raises(ForecastUnavailable):
            engine.analysiere_batch([baue_sku("A"), baue_sku("B")])

    def test_zu_kurze_historie_weicht_nicht_auf_statistik_aus(self) -> None:
        engine = DispositionEngine(
            prognose_kette=(FakeTimesFMForecaster(min_kontext=12),),
            config=EngineConfig(),
        )

        with pytest.raises(TimesFMNichtVerfuegbar, match="zu kurz"):
            engine.analysiere(baue_sku(mengen=[100.0, 110.0], bestand=10.0))

    def test_korridor_wird_nicht_statistisch_ergaenzt(
        self, timesfm_engine: DispositionEngine
    ) -> None:
        """Auch der teilweise Rueckfall ueber den P90-Korridor ist gesperrt."""
        engine = timesfm_engine
        serie = baue_serie([(f"2024-{m:02d}-01", 100.0) for m in range(1, 7)])
        prognose = engine.prognose_kette[0].prognose(serie)

        ergaenzt = engine._ergaenze_korridor(prognose, serie)

        assert ergaenzt is prognose
        assert "+korridor" not in ergaenzt.modell

    def test_ergebnis_wird_nie_als_fallback_markiert(
        self, timesfm_engine: DispositionEngine
    ) -> None:
        ergebnis = timesfm_engine.analysiere(
            baue_sku(mengen=[300.0] * 6, bestand=50.0, lieferzeit=10)
        )

        assert ergebnis.prognose_fallback is False
        assert ergebnis.prognose_modell.startswith("timesfm:")
        assert "statistical" not in ergebnis.prognose_modell


class TestInferenzUeberTimesFM:
    """Die Kennzahlen entstehen tatsaechlich aus der Modellausgabe."""

    def test_prognose_stammt_aus_dem_modell(
        self, timesfm_engine: DispositionEngine
    ) -> None:
        # Konstante Historie von 300 je Periode -> Modell-Double liefert P50 = 300.
        # Die Monatsersten Jan..Jun haben einen Median-Abstand von 31 Tagen; die
        # Prognose wird auf einen 30-Tage-Monat normiert (Faktor 30/31).
        ergebnis = timesfm_engine.analysiere(
            baue_sku(mengen=[300.0] * 6, bestand=0.0, lieferzeit=10)
        )

        erwarteter_tagesbedarf = 300.0 * (30.0 / 31.0) / 30.0  # = 9.68
        assert ergebnis.prognose_tagesbedarf == pytest.approx(
            erwarteter_tagesbedarf, abs=0.01
        )
        # P90-Aufschlag des Doubles ist 1.2.
        assert ergebnis.prognose_tagesbedarf_p90 == pytest.approx(
            erwarteter_tagesbedarf * 1.2, abs=0.01
        )
        # sicherheitsbestand = lieferzeit * (P90 - P50)
        assert ergebnis.sicherheitsbestand == pytest.approx(
            10 * (ergebnis.prognose_tagesbedarf_p90 - ergebnis.prognose_tagesbedarf),
            abs=0.01,
        )
        # meldebestand = tagesbedarf * lieferzeit + sicherheitsbestand
        assert ergebnis.meldebestand == pytest.approx(
            ergebnis.prognose_tagesbedarf * 10 + ergebnis.sicherheitsbestand, abs=0.01
        )

    def test_modell_erhaelt_die_verbrauchsreihe(
        self, timesfm_engine: DispositionEngine
    ) -> None:
        adapter = timesfm_engine.prognose_kette[0]
        timesfm_engine.analysiere(baue_sku(mengen=[10.0, 20.0, 30.0], bestand=5.0))

        assert adapter._modell.aufrufe, "Das Modell wurde nicht aufgerufen"
        assert list(adapter._modell.aufrufe[0]) == [10.0, 20.0, 30.0]

    def test_ampel_und_sortierung_funktionieren_unveraendert(
        self, timesfm_engine: DispositionEngine
    ) -> None:
        ergebnisse = timesfm_engine.analysiere_batch(
            [
                baue_sku("UEBER", mengen=[30.0] * 6, bestand=50_000.0, lieferzeit=10),
                baue_sku("KRITISCH", mengen=[300.0] * 6, bestand=0.0, lieferzeit=10),
                baue_sku("OPTIMAL", mengen=[300.0] * 6, bestand=150.0, lieferzeit=10),
            ]
        )

        assert [e.sku for e in ergebnisse] == ["KRITISCH", "OPTIMAL", "UEBER"]

    def test_startpruefung_laedt_das_pflichtmodell(
        self, timesfm_engine: DispositionEngine
    ) -> None:
        assert timesfm_engine.modell_bereit is False

        timesfm_engine.starte()

        assert timesfm_engine.modell_bereit is True

    def test_startpruefung_scheitert_bei_unladbarem_checkpoint(self) -> None:
        engine = DispositionEngine(
            prognose_kette=(FakeTimesFMForecaster(scheitert="403 Forbidden"),),
            config=EngineConfig(),
        )

        with pytest.raises(TimesFMNichtVerfuegbar, match="403 Forbidden"):
            engine.starte()


class TestApiImPflichtmodus:
    """Verhalten der HTTP-Schnittstelle, wenn TimesFM verbindlich ist."""

    def test_service_startet_nicht_ohne_pflichtmodell(self) -> None:
        """Fail-Fast: lieber sichtbar nicht hochkommen als falsch rechnen."""
        app = erstelle_app(
            DispositionEngine(
                prognose_kette=(FakeTimesFMForecaster(scheitert="Checkpoint fehlt"),),
                config=EngineConfig(),
            )
        )

        with pytest.raises(TimesFMNichtVerfuegbar):
            with TestClient(app):
                pass

    def test_health_meldet_pflichtmodus_und_modellstatus(self) -> None:
        app = erstelle_app(
            DispositionEngine(
                prognose_kette=(FakeTimesFMForecaster(),), config=EngineConfig()
            )
        )

        with TestClient(app) as client:
            daten = client.get("/health").json()

        assert daten["force_timesfm"] is True
        assert daten["fallback_erlaubt"] is False
        assert daten["modell_geladen"] is True
        assert daten["timesfm_checkpoint"] == "google/timesfm-2.5-200m-pytorch"
        assert daten["prognose_kette"] == ["timesfm"]

    def test_analyse_laeuft_ueber_timesfm(self) -> None:
        app = erstelle_app(
            DispositionEngine(
                prognose_kette=(FakeTimesFMForecaster(),), config=EngineConfig()
            )
        )
        nutzlast = [
            {
                "sku": "A-1",
                "historie": [
                    {"datum": f"2024-{m:02d}-01", "menge": 300} for m in range(1, 7)
                ],
                "bestand": 20,
                "lieferzeit": 14,
            }
        ]

        with TestClient(app) as client:
            antwort = client.post("/api/v1/analyze-json", json=nutzlast)

        assert antwort.status_code == 200
        ergebnis = antwort.json()["ergebnisse"][0]
        assert ergebnis["prognose_modell"] == "timesfm:google/timesfm-2.5-200m-pytorch"
        assert ergebnis["prognose_fallback"] is False
        assert antwort.json()["meta"]["prognose_modelle"] == [
            "timesfm:google/timesfm-2.5-200m-pytorch"
        ]

    def test_gescheiterte_inferenz_liefert_503_statt_ersatzergebnis(self) -> None:
        """Kein 200 mit Zahlen aus einem anderen Verfahren."""
        engine = DispositionEngine(
            prognose_kette=(FakeTimesFMForecaster(),), config=EngineConfig()
        )
        app = erstelle_app(engine)

        with TestClient(app, raise_server_exceptions=False) as client:
            # Modell nach dem Start unbrauchbar machen (Checkpoint verloren).
            engine.prognose_kette[0]._modell = None
            antwort = client.post(
                "/api/v1/analyze-json",
                json=[
                    {
                        "sku": "A-1",
                        "historie": [
                            {"datum": "2024-01-01", "menge": 10},
                            {"datum": "2024-02-01", "menge": 12},
                        ],
                        "bestand": 5,
                        "lieferzeit": 7,
                    }
                ],
            )

        assert antwort.status_code == 503
        detail = antwort.json()["detail"]
        assert "FORCE_TIMESFM" in detail
        assert "blockiert" in detail


class TestLokaleModellgewichte:
    """Autarker Betrieb: Vorrang des eingebackenen Modellverzeichnisses.

    Das Container-Image backt die Gewichte nach ``MODEL_DIR``. Der Adapter
    muss von dort laden, damit die Laufzeit ohne Netzzugang auskommt
    (``HF_HUB_OFFLINE=1``).
    """

    @staticmethod
    def _baue_checkpoint(verzeichnis, dateiname: str = "model.safetensors"):
        """Legt ein Modellverzeichnis mit Gewichtsdatei an."""
        verzeichnis.mkdir(parents=True, exist_ok=True)
        (verzeichnis / dateiname).write_bytes(b"gewichte")
        (verzeichnis / "config.json").write_text("{}")
        return verzeichnis

    def test_standardpfad_zeigt_auf_das_gebackene_verzeichnis(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MODEL_DIR", raising=False)

        assert lade_config().model_dir == "/app/models/timesfm-checkpoint"

    def test_model_dir_wird_aus_der_umgebung_gelesen(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setenv("MODEL_DIR", str(tmp_path / "ckpt"))

        assert lade_config().model_dir == str(tmp_path / "ckpt")

    def test_vorhandenes_verzeichnis_hat_vorrang_vor_der_repo_id(
        self, tmp_path
    ) -> None:
        lokal = self._baue_checkpoint(tmp_path / "timesfm-checkpoint")
        adapter = TimesFMForecaster(
            checkpoint="google/timesfm-2.5-200m-pytorch", model_dir=str(lokal)
        )

        assert adapter.quelle() == str(lokal)
        assert adapter.laedt_lokal is True

    def test_ohne_verzeichnis_gilt_die_repo_id(self, tmp_path) -> None:
        adapter = TimesFMForecaster(
            checkpoint="google/timesfm-2.5-200m-pytorch",
            model_dir=str(tmp_path / "gibt-es-nicht"),
        )

        assert adapter.quelle() == "google/timesfm-2.5-200m-pytorch"
        assert adapter.laedt_lokal is False

    def test_leeres_verzeichnis_gilt_nicht_als_checkpoint(self, tmp_path) -> None:
        """Ein nicht gegriffener Volume-Mount darf nicht als Quelle durchgehen."""
        leer = tmp_path / "leer"
        leer.mkdir()
        adapter = TimesFMForecaster(
            checkpoint="google/timesfm-2.5-200m-pytorch", model_dir=str(leer)
        )

        assert adapter.quelle() == "google/timesfm-2.5-200m-pytorch"
        assert adapter.laedt_lokal is False

    @pytest.mark.parametrize(
        "dateiname", ["model.safetensors", "pytorch_model.bin", "model.ckpt"]
    )
    def test_gaengige_gewichtsformate_werden_erkannt(
        self, tmp_path, dateiname: str
    ) -> None:
        lokal = self._baue_checkpoint(tmp_path / dateiname.split(".")[0], dateiname)
        adapter = TimesFMForecaster(model_dir=str(lokal))

        assert adapter.laedt_lokal is True

    def test_leerer_model_dir_wird_ignoriert(self) -> None:
        adapter = TimesFMForecaster(model_dir="")

        assert adapter.quelle() == "google/timesfm-2.5-200m-pytorch"

    def test_engine_reicht_model_dir_an_den_adapter_durch(self, tmp_path) -> None:
        lokal = self._baue_checkpoint(tmp_path / "ckpt")
        engine = baue_engine(EngineConfig(model_dir=str(lokal)))

        assert engine.prognose_kette[0].quelle() == str(lokal)

    def test_ergebnis_weist_die_tatsaechliche_quelle_aus(self, tmp_path) -> None:
        """Nachvollziehbarkeit: welche Gewichte haben die Empfehlung erzeugt?"""
        lokal = self._baue_checkpoint(tmp_path / "ckpt")
        engine = DispositionEngine(
            prognose_kette=(FakeTimesFMForecaster(model_dir=str(lokal)),),
            config=EngineConfig(model_dir=str(lokal)),
        )

        ergebnis = engine.analysiere(baue_sku(mengen=[300.0] * 6, bestand=50.0))

        assert ergebnis.prognose_modell == f"timesfm:{lokal}"
        assert ergebnis.prognose_fallback is False

    def test_health_weist_lokalen_bezug_aus(self, tmp_path) -> None:
        lokal = self._baue_checkpoint(tmp_path / "ckpt")
        app = erstelle_app(
            DispositionEngine(
                prognose_kette=(FakeTimesFMForecaster(model_dir=str(lokal)),),
                config=EngineConfig(model_dir=str(lokal)),
            )
        )

        with TestClient(app) as client:
            daten = client.get("/health").json()

        assert daten["timesfm_quelle"] == str(lokal)
        assert daten["modell_lokal_eingebacken"] is True
        assert daten["modell_geladen"] is True

    def test_health_meldet_hub_bezug_ohne_gebackene_gewichte(self) -> None:
        app = erstelle_app(
            DispositionEngine(
                prognose_kette=(FakeTimesFMForecaster(model_dir="/nicht/vorhanden"),),
                config=EngineConfig(),
            )
        )

        with TestClient(app) as client:
            daten = client.get("/health").json()

        assert daten["modell_lokal_eingebacken"] is False
        assert daten["timesfm_quelle"] == "google/timesfm-2.5-200m-pytorch"

    def test_fail_fast_gilt_auch_fuer_lokale_gewichte(self, tmp_path) -> None:
        """Ein defektes lokales Checkpoint darf nicht stillschweigend
        auf den Hub ausweichen - der Start muss scheitern."""
        lokal = self._baue_checkpoint(tmp_path / "ckpt")
        engine = DispositionEngine(
            prognose_kette=(
                FakeTimesFMForecaster(
                    model_dir=str(lokal), scheitert="safetensors beschaedigt"
                ),
            ),
            config=EngineConfig(model_dir=str(lokal)),
        )

        with pytest.raises(TimesFMNichtVerfuegbar, match="beschaedigt"):
            engine.starte()
