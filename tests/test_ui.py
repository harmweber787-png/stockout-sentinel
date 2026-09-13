"""Tests der Streamlit-Oberflaeche.

Geprueft wird mit Streamlits eigenem ``AppTest``: das Skript wird
tatsaechlich ausgefuehrt, Widgets werden bedient und Ausnahmen sichtbar
gemacht. Ein Browser ist dafuer nicht noetig.

Die Tests laufen im Notbetrieb (``FORCE_TIMESFM=false``), damit sie ohne
Modellgewichte und ohne Netzzugang auskommen. Das Verhalten im Pflichtmodus
wird eigens geprueft.
"""

from __future__ import annotations

import pathlib

import pandas as pd
import pytest

streamlit = pytest.importorskip("streamlit", reason="Streamlit nicht installiert")
from streamlit.testing.v1 import AppTest  # noqa: E402

import app as ui  # noqa: E402
from src.adapters.statistical_forecaster import StatisticalForecaster  # noqa: E402
from src.domain.timeseries import baue_serie  # noqa: E402
from tests.conftest import baue_sku  # noqa: E402

#: Grosszuegig: der erste Lauf baut die Engine auf.
ZEITGRENZE = 120

#: AppTest loest relative Pfade gegen die aufrufende Datei auf - hier
#: waere das tests/, deshalb der absolute Pfad zur App im Projektwurzel.
APP_PFAD = str(pathlib.Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture()
def notbetrieb(monkeypatch: pytest.MonkeyPatch):
    """Setzt den Notbetrieb und leert den Streamlit-Ressourcencache.

    Ohne das Leeren wuerde eine zuvor gebaute Engine ueber Testgrenzen
    hinweg weiterverwendet und die Tests waeren reihenfolgeabhaengig.
    """
    monkeypatch.setenv("FORCE_TIMESFM", "false")
    monkeypatch.setenv("STOCKOUT_PROGNOSE_STRATEGIE", "statistisch")
    streamlit.cache_resource.clear()
    yield
    streamlit.cache_resource.clear()


@pytest.fixture()
def pflichtmodus(monkeypatch: pytest.MonkeyPatch):
    """Pflichtmodus mit einem Checkpoint-Pfad, der garantiert nicht laedt."""
    monkeypatch.setenv("FORCE_TIMESFM", "true")
    monkeypatch.setenv("MODEL_DIR", "/nicht/vorhanden")
    monkeypatch.setenv("STOCKOUT_TIMESFM_CHECKPOINT", "/nicht/vorhanden")
    streamlit.cache_resource.clear()
    yield
    streamlit.cache_resource.clear()


# ---------------------------------------------------------------------------
# Reine Hilfsfunktionen
# ---------------------------------------------------------------------------
class TestHorizontumrechnung:
    @pytest.mark.parametrize(
        ("tage", "kadenz", "erwartet"),
        [
            (90, 30, 3),      # Monatsdaten
            (90, 1, 90),      # Tagesdaten
            (90, 7, 13),      # Wochendaten, aufgerundet
            (30, 30, 1),
            (1, 30, 1),       # nie weniger als eine Periode
        ],
    )
    def test_umrechnung(self, tage: float, kadenz: float, erwartet: int) -> None:
        assert ui.horizont_in_perioden(tage, kadenz) == erwartet

    def test_obergrenze_wird_eingehalten(self) -> None:
        assert ui.horizont_in_perioden(100_000, 1, obergrenze=128) == 128

    def test_ungueltige_kadenz_wird_abgelehnt(self) -> None:
        with pytest.raises(ValueError, match="groesser als 0"):
            ui.horizont_in_perioden(30, 0)


class TestVerlaufsrahmen:
    def test_rahmen_verbindet_historie_und_prognose(self) -> None:
        artikel = baue_sku(mengen=[100.0, 110.0, 120.0, 130.0, 140.0, 150.0])
        pfad = StatisticalForecaster().prognose_pfad(
            baue_serie([(s.datum, s.menge) for s in artikel.historie]), 4
        )

        historie, prognose = ui.verlaufsrahmen(artikel, pfad)

        assert list(historie.columns) == ["datum", "menge"]
        assert len(historie) == 6
        # Anschlusspunkt + 4 Prognoseperioden
        assert len(prognose) == 5
        assert prognose["datum"].iloc[0] == historie["datum"].iloc[-1]
        assert prognose["p50"].iloc[0] == historie["menge"].iloc[-1]

    def test_prognose_liegt_zeitlich_nach_der_historie(self) -> None:
        artikel = baue_sku(mengen=[100.0] * 6)
        pfad = StatisticalForecaster().prognose_pfad(
            baue_serie([(s.datum, s.menge) for s in artikel.historie]), 3
        )

        historie, prognose = ui.verlaufsrahmen(artikel, pfad)

        assert prognose["datum"].iloc[-1] > historie["datum"].iloc[-1]
        assert prognose["datum"].is_monotonic_increasing

    def test_korridor_umschliesst_den_median(self) -> None:
        artikel = baue_sku(mengen=[100.0, 130.0, 90.0, 140.0, 110.0, 120.0])
        pfad = StatisticalForecaster().prognose_pfad(
            baue_serie([(s.datum, s.menge) for s in artikel.historie]), 6
        )

        _historie, prognose = ui.verlaufsrahmen(artikel, pfad)

        assert (prognose["p10"] <= prognose["p50"]).all()
        assert (prognose["p50"] <= prognose["p90"]).all()

    def test_unparsbare_daten_kippen_den_graphen_nicht(self) -> None:
        """Exotische Periodenkennzeichen duerfen die Darstellung nicht brechen."""
        from src.schemas import SKUInput

        artikel = SKUInput(
            sku="OHNE-DATUM",
            historie=[{"datum": f"Periode {i}", "menge": 100.0} for i in range(1, 7)],
            bestand=50.0,
            lieferzeit=10,
        )
        pfad = StatisticalForecaster().prognose_pfad(
            baue_serie([(s.datum, s.menge) for s in artikel.historie]), 3
        )

        historie, prognose = ui.verlaufsrahmen(artikel, pfad)

        assert len(historie) == 6
        assert historie["datum"].notna().all()
        assert len(prognose) == 4


class TestEingefuegterText:
    """Copy-&-Paste-Weg: CSV ohne Datei-Dialog (relevant auf Mobilgeraeten)."""

    def test_zeilenenden_und_bom_werden_bereinigt(self) -> None:
        """Aus Messengern und Notiz-Apps kommt selten sauberer Text."""
        roh = ui.text_zu_rohdaten("\ufeffArtikel;Bestand\r\nA;1\r\n")

        assert roh == b"Artikel;Bestand\nA;1\n"

    def test_geschuetzte_leerzeichen_werden_ersetzt(self) -> None:
        roh = ui.text_zu_rohdaten("Artikel;Bestand\nA;1\u00a0000\n")

        assert "\u00a0" not in roh.decode("utf-8")

    def test_leerzeilen_an_den_raendern_fallen_weg(self) -> None:
        roh = ui.text_zu_rohdaten("\n\n  \nArtikel;Bestand\nA;1\n\n  \n")

        assert roh == b"Artikel;Bestand\nA;1\n"

    @pytest.mark.parametrize("text", ["", "   ", "\n\n", "\ufeff"])
    def test_leerer_text_ergibt_leere_rohdaten(self, text: str) -> None:
        assert ui.text_zu_rohdaten(text) == b""

    def test_text_wird_genutzt_wenn_keine_datei_vorliegt(self) -> None:
        rohdaten, herkunft, hinweis = ui.waehle_csv_quelle(None, None, "A;B\nx;1")

        assert rohdaten == b"A;B\nx;1\n"
        assert herkunft == "Eingefügter CSV-Text"
        assert hinweis is None

    def test_datei_hat_vorrang_und_das_wird_gemeldet(self) -> None:
        """Keine der beiden Eingaben darf stillschweigend verfallen."""
        rohdaten, herkunft, hinweis = ui.waehle_csv_quelle(
            b"aus-datei", "export.csv", "auch text"
        )

        assert rohdaten == b"aus-datei"
        assert herkunft == "export.csv"
        assert hinweis and "Datei" in hinweis
        assert "eingefügter" in hinweis, "Sichtbarer Text braucht echte Umlaute"

    def test_ohne_beides_bleibt_alles_leer(self) -> None:
        assert ui.waehle_csv_quelle(None, None, None) == (b"", "", None)

    def test_eingefuegter_text_laeuft_durch_den_echten_importer(self) -> None:
        from src.adapters.csv_ingest import lese_csv

        text = (
            "Artikel;Bestand;Lieferzeit;2024-01;2024-02;2024-03\r\n"
            "SNEAKER-42-SW;120;30;55;61;58\r\n"
            "STIEFEL-41-BR;40;60;12;9;7\r\n"
        )
        rohdaten, _herkunft, _hinweis = ui.waehle_csv_quelle(None, None, text)

        ergebnis = lese_csv(rohdaten)

        assert [a.sku for a in ergebnis.artikel] == ["SNEAKER-42-SW", "STIEFEL-41-BR"]
        assert ergebnis.layout == "breit"
        assert ergebnis.artikel[0].bestand == 120
        assert ergebnis.artikel[0].lieferzeit == 30


# ---------------------------------------------------------------------------
# Vollstaendiger Lauf der Oberflaeche
# ---------------------------------------------------------------------------
@pytest.mark.usefixtures("notbetrieb")
class TestOberflaeche:
    def test_app_laeuft_ohne_ausnahme(self) -> None:
        at = AppTest.from_file(APP_PFAD, default_timeout=ZEITGRENZE).run()

        assert not at.exception, at.exception
        assert at.title[0].value.endswith("Stockout-Sentinel")

    def test_alle_drei_zonen_sind_vorhanden(self) -> None:
        at = AppTest.from_file(APP_PFAD, default_timeout=ZEITGRENZE).run()

        ueberschriften = " ".join(s.value for s in at.subheader)
        assert "2 · Radar" in ueberschriften
        assert "3 · Entscheidung" in ueberschriften
        # Zone 1 liegt in der Seitenleiste.
        assert any("Eingabe" in h.value for h in at.sidebar.header)

    def test_zone_1_bietet_upload_demo_und_horizont(self) -> None:
        at = AppTest.from_file(APP_PFAD, default_timeout=ZEITGRENZE).run()

        assert any(r.label == "Datenquelle" for r in at.sidebar.radio)
        assert any("Szenario" in s.label for s in at.sidebar.selectbox)
        schieber = [s.label for s in at.sidebar.slider]
        assert any("Horizont" in label for label in schieber)

    def test_zone_3_zeigt_die_ampeltabelle(self) -> None:
        at = AppTest.from_file(APP_PFAD, default_timeout=ZEITGRENZE).run()

        assert at.dataframe, "Keine Entscheidungstabelle gerendert"
        tabelle: pd.DataFrame = at.dataframe[0].value
        for spalte in ("Status", "Artikel", "Reichweite (Tage)", "Nachbestellmenge"):
            assert spalte in tabelle.columns
        assert tabelle["Status"].str.contains("KRITISCH|OPTIMAL|UEBERBESTAND").all()

    def test_kennzahlen_zaehlen_die_ampelzustaende(self) -> None:
        at = AppTest.from_file(APP_PFAD, default_timeout=ZEITGRENZE).run()

        beschriftungen = [m.label for m in at.metric]
        assert "🔴 Kritisch" in beschriftungen
        assert "🟢 Optimal" in beschriftungen
        assert "🟡 Überbestand" in beschriftungen

    def test_prioritaetenliste_ist_sortiert(self) -> None:
        at = AppTest.from_file(APP_PFAD, default_timeout=ZEITGRENZE).run()
        tabelle: pd.DataFrame = at.dataframe[0].value

        rang = {"🔴 KRITISCH": 0, "🟢 OPTIMAL": 1, "🟡 UEBERBESTAND": 2}
        raenge = [rang[status] for status in tabelle["Status"]]
        assert raenge == sorted(raenge)

    def test_szenariowechsel_aendert_das_ergebnis(self) -> None:
        at = AppTest.from_file(APP_PFAD, default_timeout=ZEITGRENZE).run()
        at.sidebar.selectbox[0].select("Engpass-Situation").run()

        assert not at.exception, at.exception
        tabelle: pd.DataFrame = at.dataframe[0].value
        assert set(tabelle["Status"]) == {"🔴 KRITISCH"}

    def test_horizont_laesst_sich_auf_tage_umstellen(self) -> None:
        at = AppTest.from_file(APP_PFAD, default_timeout=ZEITGRENZE).run()
        at.sidebar.radio[1].set_value("Tage").run()

        assert not at.exception, at.exception
        assert any("Horizont (Tage)" == s.label for s in at.sidebar.slider)

    def test_statusfilter_schraenkt_die_tabelle_ein(self) -> None:
        at = AppTest.from_file(APP_PFAD, default_timeout=ZEITGRENZE).run()
        at.multiselect[0].set_value(["🔴 KRITISCH"]).run()

        assert not at.exception, at.exception
        assert set(at.dataframe[0].value["Status"]) == {"🔴 KRITISCH"}

    def test_csv_modus_laeuft_ohne_datei_weiter(self) -> None:
        at = AppTest.from_file(APP_PFAD, default_timeout=ZEITGRENZE).run()
        at.sidebar.radio[0].set_value("CSV-Upload").run()

        assert not at.exception, at.exception
        assert any("keine Datei" in i.value for i in at.sidebar.info)

    def test_textfeld_steht_unter_dem_upload_bereit(self) -> None:
        at = AppTest.from_file(APP_PFAD, default_timeout=ZEITGRENZE).run()
        at.sidebar.radio[0].set_value("CSV-Upload").run()

        assert at.sidebar.text_area, "Kein Textfeld zum Einfuegen vorhanden"
        assert "einfügen" in at.sidebar.text_area[0].label.lower()

    def test_eingefuegter_csv_text_erzeugt_die_ampeltabelle(self) -> None:
        """Der eigentliche Zweck: Disposition ohne jeden Datei-Dialog."""
        at = AppTest.from_file(APP_PFAD, default_timeout=ZEITGRENZE).run()
        at.sidebar.radio[0].set_value("CSV-Upload").run()
        at.sidebar.text_area[0].set_value(
            "Artikel;Bestand;Lieferzeit;2024-01;2024-02;2024-03;2024-04\n"
            "SNEAKER-42-SW;20;30;55;61;58;60\n"
            "STIEFEL-41-BR;900;60;12;9;7;8\n"
        ).run()

        assert not at.exception, at.exception
        assert at.dataframe, "Keine Tabelle nach dem Einfuegen"

        tabelle: pd.DataFrame = at.dataframe[0].value
        assert set(tabelle["Artikel"]) == {"SNEAKER-42-SW", "STIEFEL-41-BR"}
        # Knapper Bestand bei 30 Tagen Lieferzeit -> Bestellvorschlag.
        knapp = tabelle[tabelle["Artikel"] == "SNEAKER-42-SW"].iloc[0]
        assert knapp["Status"] == "🔴 KRITISCH"
        assert knapp["Nachbestellmenge"] > 0
        # Weit gedeckt -> kein Bestellvorschlag.
        reichlich = tabelle[tabelle["Artikel"] == "STIEFEL-41-BR"].iloc[0]
        assert reichlich["Nachbestellmenge"] == 0

    def test_unbrauchbarer_text_meldet_einen_fehler(self) -> None:
        at = AppTest.from_file(APP_PFAD, default_timeout=ZEITGRENZE).run()
        at.sidebar.radio[0].set_value("CSV-Upload").run()
        at.sidebar.text_area[0].set_value("nur;irgendwas\n1;2\n").run()

        assert not at.exception, at.exception
        assert any("nicht lesbar" in e.value for e in at.sidebar.error)
        assert not at.dataframe


@pytest.mark.usefixtures("pflichtmodus")
class TestOberflaecheImPflichtmodus:
    """Ohne Modell zeigt die Oberflaeche einen Fehler - und keine Zahlen."""

    def test_fehlendes_modell_stuerzt_die_app_nicht_ab(self) -> None:
        at = AppTest.from_file(APP_PFAD, default_timeout=ZEITGRENZE).run()

        assert not at.exception, at.exception
        assert at.sidebar.error, "Kein Hinweis auf das fehlende Modell"

    def test_ohne_modell_werden_keine_zahlen_gezeigt(self) -> None:
        """Kein stiller Rueckfall: lieber nichts als Zahlen aus einem Ersatzmodell."""
        at = AppTest.from_file(APP_PFAD, default_timeout=ZEITGRENZE).run()

        assert not at.dataframe, "Trotz fehlendem Pflichtmodell wurde eine Tabelle gezeigt"
        assert at.error, "Kein Fehlerhinweis im Hauptbereich"
