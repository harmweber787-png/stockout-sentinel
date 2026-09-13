"""Grenzfallpruefungen des Dispositions-Kerns.

Die Suite arbeitet ausschliesslich mit In-Memory-Fixtures: keine externen
CSV-Dateien, keine Stammdaten, keine Netzwerkzugriffe. Die Prognosekette
ist auf den statistischen Schaetzer festgelegt, damit die Ergebnisse
reproduzierbar sind.
"""

from __future__ import annotations

import pytest

from src.domain.disposition import (
    KEINE_REICHWEITE,
    Status,
    berechne_kennzahlen,
    bestimme_status,
)
from src.engine import DispositionEngine, sortiere_prioritaeten
from src.schemas import SKUInput
from tests.conftest import baue_sku


# ---------------------------------------------------------------------------
# Test 1: Nullbestand bei hoher Nachfrage
# ---------------------------------------------------------------------------
class TestNullbestand:
    """Ein leeres Lager bei laufendem Verbrauch muss die rote Ampel ausloesen."""

    def test_nullbestand_bei_hoher_nachfrage_ist_kritisch(
        self, engine: DispositionEngine
    ) -> None:
        artikel = baue_sku(
            "LEER-HOHE-NACHFRAGE",
            mengen=[3000.0, 3200.0, 3100.0, 3300.0],
            bestand=0.0,
            lieferzeit=14,
        )

        ergebnis = engine.analysiere(artikel)

        assert ergebnis.status == "🔴 KRITISCH"
        assert ergebnis.status_code == "KRITISCH"
        assert ergebnis.reichweite_tage == 0.0
        assert ergebnis.prognose_tagesbedarf > 0
        # Bei leerem Lager muss der volle Meldebestand nachbestellt werden.
        assert ergebnis.nachbestellmenge == ergebnis.meldebestand
        assert "Sofort" in ergebnis.empfohlene_massnahme

    @pytest.mark.parametrize("lieferzeit", [1, 7, 14, 30, 90])
    def test_nullbestand_ist_unabhaengig_von_der_lieferzeit_kritisch(
        self, engine: DispositionEngine, lieferzeit: int
    ) -> None:
        artikel = baue_sku(
            mengen=[500.0, 520.0, 510.0], bestand=0.0, lieferzeit=lieferzeit
        )

        assert engine.analysiere(artikel).status_code == "KRITISCH"

    def test_reichweite_unter_lieferzeit_ist_kritisch(
        self, engine: DispositionEngine
    ) -> None:
        # 300/Monat -> 10/Tag. Bestand 50 -> 5 Tage Reichweite < 20 Tage Lieferzeit.
        artikel = baue_sku(
            mengen=[300.0, 300.0, 300.0], bestand=50.0, lieferzeit=20
        )

        ergebnis = engine.analysiere(artikel)

        assert ergebnis.prognose_tagesbedarf == pytest.approx(10.0, abs=0.5)
        assert ergebnis.reichweite_tage < 20
        assert ergebnis.status_code == "KRITISCH"


# ---------------------------------------------------------------------------
# Test 2: Nachbestellmenge == Meldebestand - Ist-Bestand
# ---------------------------------------------------------------------------
class TestNachbestellmenge:
    """Die Bestellmenge muss exakt die Luecke zum Meldebestand schliessen."""

    @pytest.mark.parametrize("bestand", [0.0, 25.0, 80.0, 150.0])
    def test_nachbestellmenge_entspricht_meldebestand_minus_bestand(
        self, engine: DispositionEngine, bestand: float
    ) -> None:
        artikel = baue_sku(
            "DELTA-PRUEFUNG",
            mengen=[600.0, 640.0, 620.0, 660.0],
            bestand=bestand,
            lieferzeit=12,
            mindestbestellmenge=0.0,
        )

        ergebnis = engine.analysiere(artikel)

        erwartet = max(0.0, round(ergebnis.meldebestand - bestand, 2))
        assert ergebnis.nachbestellmenge == erwartet

    def test_meldebestand_setzt_sich_aus_bedarf_und_sicherheitsbestand_zusammen(
        self, engine: DispositionEngine
    ) -> None:
        artikel = baue_sku(
            mengen=[600.0, 640.0, 620.0, 660.0], bestand=10.0, lieferzeit=12
        )

        ergebnis = engine.analysiere(artikel)

        grundbedarf = ergebnis.prognose_tagesbedarf * artikel.lieferzeit
        assert ergebnis.meldebestand == pytest.approx(
            grundbedarf + ergebnis.sicherheitsbestand, abs=0.01
        )
        # sicherheitsbestand = lieferzeit * (tagesbedarf_P90 - tagesbedarf_P50)
        assert ergebnis.sicherheitsbestand == pytest.approx(
            artikel.lieferzeit
            * (ergebnis.prognose_tagesbedarf_p90 - ergebnis.prognose_tagesbedarf),
            abs=0.01,
        )

    def test_mindestbestellmenge_hebt_bestellung_an_loest_aber_keine_aus(
        self, engine: DispositionEngine
    ) -> None:
        """Die MOQ hebt eine ausgeloeste Bestellung an - mehr nicht."""
        knapp = baue_sku(
            "MIT-MOQ",
            mengen=[300.0, 300.0, 300.0],
            bestand=100.0,
            lieferzeit=15,
            mindestbestellmenge=5000.0,
        )
        gedeckt = baue_sku(
            "OHNE-BEDARF",
            mengen=[300.0, 300.0, 300.0],
            bestand=100_000.0,
            lieferzeit=15,
            mindestbestellmenge=5000.0,
        )

        assert engine.analysiere(knapp).nachbestellmenge == 5000.0
        assert engine.analysiere(gedeckt).nachbestellmenge == 0.0


# ---------------------------------------------------------------------------
# Test 3: Hoher Ueberbestand
# ---------------------------------------------------------------------------
class TestUeberbestand:
    """Ein weit gedecktes Lager darf keine Bestellung ausloesen."""

    def test_hoher_ueberbestand_schlaegt_keine_bestellung_vor(
        self, engine: DispositionEngine
    ) -> None:
        # 30/Monat -> 1/Tag. Bestand 50'000 -> weit jenseits 2x Lieferzeit.
        artikel = baue_sku(
            "UEBERBESTAND",
            mengen=[30.0, 30.0, 30.0, 30.0],
            bestand=50_000.0,
            lieferzeit=10,
        )

        ergebnis = engine.analysiere(artikel)

        assert ergebnis.nachbestellmenge == 0.0
        assert ergebnis.status == "🟡 UEBERBESTAND"
        assert ergebnis.status_code == "UEBERBESTAND"
        assert ergebnis.reichweite_tage > artikel.lieferzeit * 2
        assert ergebnis.meldebestand < ergebnis.reichweite_tage * ergebnis.prognose_tagesbedarf

    def test_ueberbestand_auch_bei_hoher_mindestbestellmenge_ohne_bestellung(
        self, engine: DispositionEngine
    ) -> None:
        artikel = baue_sku(
            mengen=[30.0, 30.0, 30.0],
            bestand=50_000.0,
            lieferzeit=10,
            mindestbestellmenge=10_000.0,
        )

        assert engine.analysiere(artikel).nachbestellmenge == 0.0


# ---------------------------------------------------------------------------
# Ampel-Grenzen und Sentinel
# ---------------------------------------------------------------------------
class TestAmpelgrenzen:
    """Die Schwellen der Statusampel exakt an den Uebergaengen."""

    @pytest.mark.parametrize(
        ("reichweite", "erwartet"),
        [
            (0.0, Status.KRITISCH),
            (9.99, Status.KRITISCH),
            (10.0, Status.KRITISCH),  # reichweite <= lieferzeit
            (10.01, Status.OPTIMAL),
            (20.0, Status.OPTIMAL),  # reichweite <= lieferzeit * 2
            (20.01, Status.UEBERBESTAND),
            (999.0, Status.UEBERBESTAND),
        ],
    )
    def test_statusgrenzen(self, reichweite: float, erwartet: Status) -> None:
        assert bestimme_status(reichweite, lieferzeit=10) is erwartet

    def test_ohne_verbrauch_gilt_die_sentinel_reichweite(
        self, engine: DispositionEngine
    ) -> None:
        artikel = baue_sku(
            "OHNE-VERBRAUCH", mengen=[0.0, 0.0, 0.0], bestand=500.0, lieferzeit=10
        )

        ergebnis = engine.analysiere(artikel)

        assert ergebnis.prognose_tagesbedarf == 0.0
        assert ergebnis.reichweite_tage == KEINE_REICHWEITE
        assert ergebnis.nachbestellmenge == 0.0
        assert ergebnis.status_code == "UEBERBESTAND"

    def test_reichweite_wird_bei_sentinel_gedeckelt(
        self, engine: DispositionEngine
    ) -> None:
        artikel = baue_sku(mengen=[1.0, 1.0, 1.0], bestand=10_000_000.0, lieferzeit=5)

        assert engine.analysiere(artikel).reichweite_tage == KEINE_REICHWEITE


# ---------------------------------------------------------------------------
# Prognose: Trend, Robustheit, Kadenz
# ---------------------------------------------------------------------------
class TestPrognose:
    """Verhalten des statistischen Schaetzers an fachlichen Grenzfaellen."""

    def test_sicherheitsbestand_ist_nie_negativ(
        self, engine: DispositionEngine
    ) -> None:
        artikel = baue_sku(mengen=[900.0, 600.0, 300.0, 100.0], bestand=50.0, lieferzeit=8)

        ergebnis = engine.analysiere(artikel)

        assert ergebnis.sicherheitsbestand >= 0
        assert ergebnis.prognose_tagesbedarf_p90 >= ergebnis.prognose_tagesbedarf

    def test_aufwaertstrend_erhoeht_den_prognostizierten_bedarf(
        self, engine: DispositionEngine
    ) -> None:
        flach = baue_sku(mengen=[100.0] * 6, bestand=0.0, lieferzeit=10)
        steigend = baue_sku(
            mengen=[100.0, 120.0, 140.0, 160.0, 180.0, 200.0], bestand=0.0, lieferzeit=10
        )

        assert (
            engine.analysiere(steigend).prognose_tagesbedarf
            > engine.analysiere(flach).prognose_tagesbedarf
        )

    def test_einzelner_ausreisser_dominiert_die_prognose_nicht(
        self, engine: DispositionEngine
    ) -> None:
        """Ein Erfassungsfehler darf die Disposition nicht entgleisen lassen."""
        sauber = baue_sku(mengen=[100.0, 105.0, 98.0, 102.0, 101.0], bestand=0.0, lieferzeit=10)
        mit_ausreisser = baue_sku(
            mengen=[100.0, 105.0, 50_000.0, 102.0, 101.0], bestand=0.0, lieferzeit=10
        )

        referenz = engine.analysiere(sauber).prognose_tagesbedarf
        gestoert = engine.analysiere(mit_ausreisser).prognose_tagesbedarf

        assert gestoert < referenz * 2, (
            f"Ausreisser verzerrt die Prognose zu stark: {gestoert} vs. {referenz}"
        )

    def test_tagesdaten_werden_auf_den_monat_hochgerechnet(
        self, engine: DispositionEngine
    ) -> None:
        """10 Stueck/Tag muessen ~10 Stueck Tagesbedarf ergeben, nicht 10/30."""
        artikel = SKUInput(
            sku="TAGESDATEN",
            historie=[
                {"datum": f"2024-01-{tag:02d}", "menge": 10.0} for tag in range(1, 15)
            ],
            bestand=0.0,
            lieferzeit=5,
        )

        assert engine.analysiere(artikel).prognose_tagesbedarf == pytest.approx(10.0, abs=0.5)

    def test_zwei_perioden_genuegen(self, engine: DispositionEngine) -> None:
        artikel = baue_sku(mengen=[100.0, 110.0], bestand=20.0, lieferzeit=7)

        ergebnis = engine.analysiere(artikel)

        assert ergebnis.prognose_tagesbedarf > 0
        assert ergebnis.status_code in {"KRITISCH", "OPTIMAL", "UEBERBESTAND"}

    def test_negative_mengen_werden_nicht_als_bedarf_gewertet(
        self, engine: DispositionEngine
    ) -> None:
        """Retouren/Stornos duerfen den Bedarf nicht ins Negative ziehen."""
        artikel = baue_sku(mengen=[100.0, -50.0, 100.0], bestand=0.0, lieferzeit=5)

        assert engine.analysiere(artikel).prognose_tagesbedarf >= 0

    def test_fallback_greift_ohne_timesfm(self, engine: DispositionEngine) -> None:
        """Ohne TimesFM-Paket liefert die Kette trotzdem ein Ergebnis."""
        ergebnis = engine.analysiere(baue_sku(mengen=[10.0, 12.0, 11.0], bestand=5.0))

        assert ergebnis.prognose_modell.startswith("statistical")


# ---------------------------------------------------------------------------
# Batch und Priorisierung
# ---------------------------------------------------------------------------
class TestPrioritaetenliste:
    """Sortierung des Batch-Ergebnisses."""

    def test_kritische_artikel_mit_geringster_reichweite_zuerst(
        self, engine: DispositionEngine
    ) -> None:
        artikel = [
            baue_sku("UEBER", mengen=[30.0] * 4, bestand=50_000.0, lieferzeit=10),
            baue_sku("KRITISCH-MITTEL", mengen=[300.0] * 4, bestand=50.0, lieferzeit=10),
            baue_sku("OPTIMAL", mengen=[300.0] * 4, bestand=150.0, lieferzeit=10),
            baue_sku("KRITISCH-LEER", mengen=[300.0] * 4, bestand=0.0, lieferzeit=10),
        ]

        ergebnisse = engine.analysiere_batch(artikel)

        assert [e.sku for e in ergebnisse] == [
            "KRITISCH-LEER",
            "KRITISCH-MITTEL",
            "OPTIMAL",
            "UEBER",
        ]

    def test_sortierung_ist_stabil_und_reproduzierbar(
        self, engine: DispositionEngine
    ) -> None:
        artikel = [
            baue_sku(f"SKU-{i:02d}", mengen=[300.0] * 4, bestand=0.0, lieferzeit=10)
            for i in range(5)
        ]

        erster = [e.sku for e in engine.analysiere_batch(artikel)]
        zweiter = [e.sku for e in engine.analysiere_batch(list(reversed(artikel)))]

        assert erster == zweiter

    def test_leerer_batch_liefert_leere_liste(self, engine: DispositionEngine) -> None:
        assert engine.analysiere_batch([]) == []

    def test_sortiere_prioritaeten_ist_eine_reine_funktion(
        self, engine: DispositionEngine
    ) -> None:
        ergebnisse = engine.analysiere_batch(
            [baue_sku("A", bestand=0.0), baue_sku("B", bestand=1_000_000.0)]
        )
        original = list(ergebnisse)

        sortiere_prioritaeten(ergebnisse)

        assert ergebnisse == original


# ---------------------------------------------------------------------------
# Reine Formelebene (ohne Prognose)
# ---------------------------------------------------------------------------
class TestFormeln:
    """Direkte Pruefung der Domaenenformeln mit vorgegebenen Prognosewerten."""

    def test_formelkette_mit_bekannten_werten(self) -> None:
        # 300/Monat -> 10/Tag; 360/Monat -> 12/Tag im P90.
        kennzahlen = berechne_kennzahlen(
            bestand=40.0,
            lieferzeit=10,
            monatsabsatz_p50=300.0,
            monatsabsatz_p90=360.0,
        )

        assert kennzahlen.prognose_tagesbedarf == 10.0
        assert kennzahlen.prognose_tagesbedarf_p90 == 12.0
        assert kennzahlen.reichweite_tage == 4.0            # 40 / 10
        assert kennzahlen.sicherheitsbestand == 20.0        # 10 * (12 - 10)
        assert kennzahlen.meldebestand == 120.0             # 10 * 10 + 20
        assert kennzahlen.nachbestellmenge == 80.0          # 120 - 40
        assert kennzahlen.status is Status.KRITISCH

    def test_ohne_bedarf_bleibt_die_reichweite_am_sentinel(self) -> None:
        kennzahlen = berechne_kennzahlen(
            bestand=100.0, lieferzeit=5, monatsabsatz_p50=0.0, monatsabsatz_p90=0.0
        )

        assert kennzahlen.reichweite_tage == KEINE_REICHWEITE
        assert kennzahlen.meldebestand == 0.0
        assert kennzahlen.nachbestellmenge == 0.0

    def test_lieferzeit_null_erzeugt_keinen_meldebestand(self) -> None:
        kennzahlen = berechne_kennzahlen(
            bestand=10.0, lieferzeit=0, monatsabsatz_p50=300.0, monatsabsatz_p90=360.0
        )

        assert kennzahlen.meldebestand == 0.0
        assert kennzahlen.nachbestellmenge == 0.0

    def test_status_codes_sind_symbolfrei(self) -> None:
        assert {s.code for s in Status} == {"KRITISCH", "OPTIMAL", "UEBERBESTAND"}
        assert all(s.value.endswith(s.code) for s in Status)
