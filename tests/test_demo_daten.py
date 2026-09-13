"""Tests der programmatisch erzeugten Demo-Datensaetze.

Die Szenarien ersetzen keine Beispieldateien - sie werden zur Laufzeit
erzeugt. Geprueft wird, dass sie deterministisch sind, das Schema erfuellen
und tatsaechlich das zeigen, was ihre Beschreibung verspricht.
"""

from __future__ import annotations

import pytest

from src.adapters.csv_ingest import lese_csv
from src.config import EngineConfig
from src.demo_daten import (
    DEMO_SZENARIEN,
    als_csv,
    baue_szenario,
    szenario_namen,
)
from src.engine import baue_engine
from src.schemas import SKUInput

SCHLUESSEL = [szenario.schluessel for szenario in DEMO_SZENARIEN]


@pytest.fixture()
def demo_engine():
    """Engine im Notbetrieb - die Demo braucht keine Modellgewichte."""
    return baue_engine(
        EngineConfig(force_timesfm=False, prognose_strategie="statistisch")
    )


class TestErzeugung:
    @pytest.mark.parametrize("schluessel", SCHLUESSEL)
    def test_szenario_erfuellt_das_schema(self, schluessel: str) -> None:
        artikel = baue_szenario(schluessel)

        assert artikel, "Szenario ohne Artikel"
        for eintrag in artikel:
            assert isinstance(eintrag, SKUInput)
            assert len(eintrag.historie) >= 2
            assert eintrag.bestand >= 0
            assert eintrag.lieferzeit > 0
            assert all(satz.menge >= 0 for satz in eintrag.historie)

    @pytest.mark.parametrize("schluessel", SCHLUESSEL)
    def test_erzeugung_ist_deterministisch(self, schluessel: str) -> None:
        erste = baue_szenario(schluessel)
        zweite = baue_szenario(schluessel)

        assert [a.sku for a in erste] == [a.sku for a in zweite]
        assert [satz.menge for satz in erste[0].historie] == [
            satz.menge for satz in zweite[0].historie
        ]
        assert [a.bestand for a in erste] == [a.bestand for a in zweite]

    def test_artikelnummern_sind_eindeutig(self) -> None:
        for schluessel in SCHLUESSEL:
            skus = [a.sku for a in baue_szenario(schluessel)]
            assert len(skus) == len(set(skus))

    @pytest.mark.parametrize("perioden", [2, 6, 12, 36])
    def test_historienlaenge_ist_steuerbar(self, perioden: int) -> None:
        artikel = baue_szenario("mischbestand", perioden=perioden)

        assert all(len(a.historie) == perioden for a in artikel)

    def test_zu_kurze_historie_wird_abgelehnt(self) -> None:
        with pytest.raises(ValueError, match="mindestens 2"):
            baue_szenario("mischbestand", perioden=1)

    def test_unbekanntes_szenario_meldet_die_bekannten(self) -> None:
        with pytest.raises(KeyError, match="mischbestand"):
            baue_szenario("gibt-es-nicht")

    def test_namensliste_deckt_alle_szenarien(self) -> None:
        assert set(szenario_namen()) == set(SCHLUESSEL)


class TestAussagekraft:
    """Ein Demo-Szenario muss zeigen, was seine Beschreibung verspricht."""

    def test_mischbestand_zeigt_alle_drei_ampelzustaende(self, demo_engine) -> None:
        ergebnisse = demo_engine.analysiere_batch(baue_szenario("mischbestand"))
        zustaende = {e.status_code for e in ergebnisse}

        assert zustaende == {"KRITISCH", "OPTIMAL", "UEBERBESTAND"}

    def test_engpass_ist_durchgaengig_kritisch(self, demo_engine) -> None:
        ergebnisse = demo_engine.analysiere_batch(baue_szenario("engpass"))

        assert {e.status_code for e in ergebnisse} == {"KRITISCH"}
        assert all(e.nachbestellmenge > 0 for e in ergebnisse)

    def test_kapitalbindung_loest_keine_bestellung_aus(self, demo_engine) -> None:
        ergebnisse = demo_engine.analysiere_batch(baue_szenario("ueberbestand"))

        assert {e.status_code for e in ergebnisse} == {"UEBERBESTAND"}
        assert all(e.nachbestellmenge == 0 for e in ergebnisse)

    def test_saison_zeigt_gemischte_lage(self, demo_engine) -> None:
        ergebnisse = demo_engine.analysiere_batch(baue_szenario("saison"))

        assert len({e.status_code for e in ergebnisse}) >= 2


class TestCsvExport:
    """Der Export muss durch den eigenen Importer wieder hineinpassen."""

    @pytest.mark.parametrize("schluessel", SCHLUESSEL)
    def test_export_ist_wieder_einlesbar(self, schluessel: str) -> None:
        original = baue_szenario(schluessel, perioden=12)
        rohdaten = als_csv(original).encode("utf-8")

        ergebnis = lese_csv(rohdaten)

        assert {a.sku for a in ergebnis.artikel} == {a.sku for a in original}
        assert not ergebnis.warnungen

    def test_werte_ueberstehen_den_rundlauf(self) -> None:
        original = baue_szenario("mischbestand", perioden=8)
        gelesen = lese_csv(als_csv(original).encode("utf-8")).artikel

        nach_sku = {a.sku: a for a in gelesen}
        for eintrag in original:
            zurueck = nach_sku[eintrag.sku]
            assert zurueck.bestand == pytest.approx(eintrag.bestand)
            assert zurueck.lieferzeit == eintrag.lieferzeit
            assert [s.menge for s in zurueck.historie] == pytest.approx(
                [s.menge for s in eintrag.historie]
            )

    def test_kopfzeile_nennt_erp_typische_spalten(self) -> None:
        kopf = als_csv(baue_szenario("engpass", perioden=3)).splitlines()[0]

        assert kopf.split(";")[:3] == ["Art-Nr", "Datum", "Verbrauch"]
