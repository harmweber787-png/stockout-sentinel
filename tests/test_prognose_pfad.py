"""Tests der Mehrschritt-Prognose (Grundlage des Radar-Graphen).

Der Pfad dient ausschliesslich der Darstellung - die Dispositionszahlen
kommen weiterhin aus der Einzelprognose. Geprueft wird, dass er ein
sauberes Quantilband liefert, die Kadenz uebernimmt und die Regeln des
TimesFM-Pflichtmodus einhaelt.
"""

from __future__ import annotations

import pytest

from src.adapters.statistical_forecaster import StatisticalForecaster
from src.config import EngineConfig
from src.domain.timeseries import baue_serie
from src.engine import DispositionEngine, baue_engine
from src.ports.forecasting import ForecastPfad, ForecastUnavailable
from tests.conftest import FakeTimesFMForecaster, baue_sku


def monatsserie(mengen: list[float]):
    return baue_serie(
        [(f"2024-{monat:02d}-01", menge) for monat, menge in enumerate(mengen, start=1)]
    )


class TestStatistischerPfad:
    def test_pfad_hat_die_gewuenschte_laenge(self) -> None:
        pfad = StatisticalForecaster().prognose_pfad(monatsserie([100.0] * 6), 4)

        assert isinstance(pfad, ForecastPfad)
        assert pfad.laenge == 4
        assert len(pfad.p10) == len(pfad.p50) == len(pfad.p90) == 4

    def test_quantile_sind_geordnet(self) -> None:
        pfad = StatisticalForecaster().prognose_pfad(
            monatsserie([100.0, 130.0, 90.0, 140.0, 110.0, 120.0]), 8
        )

        assert all(u <= m <= o for u, m, o in zip(pfad.p10, pfad.p50, pfad.p90))

    def test_korridor_weitet_sich_mit_dem_horizont(self) -> None:
        """Je weiter extrapoliert wird, desto unsicherer die Aussage."""
        pfad = StatisticalForecaster().prognose_pfad(
            monatsserie([100.0, 110.0, 95.0, 105.0, 115.0, 100.0]), 12
        )

        erste = pfad.p90[0] - pfad.p10[0]
        letzte = pfad.p90[-1] - pfad.p10[-1]
        assert letzte > erste

    def test_aufwaertstrend_wird_fortgeschrieben(self) -> None:
        pfad = StatisticalForecaster().prognose_pfad(
            monatsserie([100.0, 110.0, 120.0, 130.0, 140.0, 150.0]), 5
        )

        assert pfad.p50 == tuple(sorted(pfad.p50))
        assert pfad.p50[-1] > 150.0

    def test_pfad_wird_nie_negativ(self) -> None:
        pfad = StatisticalForecaster().prognose_pfad(
            monatsserie([200.0, 150.0, 100.0, 50.0, 20.0, 5.0]), 24
        )

        assert all(wert >= 0 for wert in pfad.p10)
        assert all(wert >= 0 for wert in pfad.p50)

    def test_kadenz_wird_uebernommen(self) -> None:
        taeglich = baue_serie([(f"2024-01-{tag:02d}", 10.0) for tag in range(1, 15)])

        assert StatisticalForecaster().prognose_pfad(taeglich, 3).periodenlaenge_tage == 1.0

    @pytest.mark.parametrize("perioden", [0, -1])
    def test_ungueltiger_horizont_wird_abgelehnt(self, perioden: int) -> None:
        with pytest.raises(ValueError, match="mindestens 1"):
            StatisticalForecaster().prognose_pfad(monatsserie([10.0, 12.0]), perioden)

    def test_p10_ergaenzt_auch_die_einzelprognose(self) -> None:
        prognose = StatisticalForecaster().prognose(monatsserie([100.0] * 6))

        assert prognose.monatsabsatz_p10 is not None
        assert prognose.monatsabsatz_p10 <= prognose.monatsabsatz_p50
        assert prognose.monatsabsatz_p50 <= prognose.monatsabsatz_p90


class TestEnginePfad:
    def test_engine_liefert_den_pfad(self) -> None:
        engine = baue_engine(
            EngineConfig(force_timesfm=False, prognose_strategie="statistisch")
        )

        pfad = engine.prognose_pfad(baue_sku(mengen=[100.0] * 6), 6)

        assert pfad.laenge == 6
        assert pfad.modell.startswith("statistical")

    def test_engine_lehnt_horizont_null_ab(self) -> None:
        engine = baue_engine(
            EngineConfig(force_timesfm=False, prognose_strategie="statistisch")
        )

        with pytest.raises(ValueError, match="mindestens 1"):
            engine.prognose_pfad(baue_sku(), 0)

    def test_pfad_laeuft_im_pflichtmodus_ueber_timesfm(self) -> None:
        engine = DispositionEngine(
            prognose_kette=(FakeTimesFMForecaster(),), config=EngineConfig()
        )

        pfad = engine.prognose_pfad(baue_sku(mengen=[300.0] * 6), 4)

        assert pfad.modell.startswith("timesfm:")
        assert "statistical" not in pfad.modell

    def test_pfad_weicht_im_pflichtmodus_nicht_aus(self) -> None:
        """Auch die Darstellung darf nicht heimlich aus einem Ersatzmodell kommen."""
        engine = DispositionEngine(
            prognose_kette=(FakeTimesFMForecaster(scheitert="Checkpoint fehlt"),),
            config=EngineConfig(),
        )

        with pytest.raises(ForecastUnavailable):
            engine.prognose_pfad(baue_sku(mengen=[300.0] * 6), 4)

    def test_dispositionszahlen_bleiben_von_der_pfadlaenge_unberuehrt(self) -> None:
        """Der Horizont ist eine Darstellungsfrage, keine fachliche."""
        engine = baue_engine(
            EngineConfig(force_timesfm=False, prognose_strategie="statistisch")
        )
        artikel = baue_sku(mengen=[300.0] * 6, bestand=100.0, lieferzeit=10)

        vorher = engine.analysiere(artikel)
        engine.prognose_pfad(artikel, 24)
        nachher = engine.analysiere(artikel)

        assert vorher == nachher


class TestPfadDatentyp:
    def test_ungleiche_laengen_werden_abgelehnt(self) -> None:
        with pytest.raises(ValueError, match="gleich lang"):
            ForecastPfad(
                p10=(1.0,), p50=(2.0, 3.0), p90=(4.0, 5.0),
                periodenlaenge_tage=30.0, modell="test",
            )

    def test_leerer_pfad_wird_abgelehnt(self) -> None:
        with pytest.raises(ValueError, match="nicht leer"):
            ForecastPfad(
                p10=(), p50=(), p90=(), periodenlaenge_tage=30.0, modell="test"
            )
