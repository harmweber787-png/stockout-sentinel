"""Tests des generischen CSV-Imports.

Alle CSV-Streams werden im Test als Bytes erzeugt - es liegen bewusst
keine Beispieldateien im Repository.
"""

from __future__ import annotations

import pytest

from src.adapters.csv_ingest import (
    CSVIngestFehler,
    lese_csv,
    normalisiere_spalte,
    zu_float,
)


class TestZahlenformate:
    """ERP-Exporte liefern Zahlen in sehr unterschiedlichen Schreibweisen."""

    @pytest.mark.parametrize(
        ("rohwert", "erwartet"),
        [
            ("1234.5", 1234.5),
            ("1'234.50", 1234.5),      # Schweizer Tausender-Apostroph
            ("1.234,50", 1234.5),      # deutsches Format
            ("1,234.50", 1234.5),      # angelsaechsisches Format
            ("1 234,50", 1234.5),      # Leerzeichen als Tausender
            ("12,5", 12.5),
            ("CHF 1'000.-", 1000.0),   # Schweizer Betragsschreibweise
            ("(30)", -30.0),           # Negativwert in Klammern
            ("-5", -5.0),
            ("0", 0.0),
            (42, 42.0),
        ],
    )
    def test_zahlen_werden_erkannt(self, rohwert: object, erwartet: float) -> None:
        assert zu_float(rohwert) == pytest.approx(erwartet)

    @pytest.mark.parametrize("rohwert", ["", "   ", "-", "n/a", "NULL", "abc", None])
    def test_nicht_zahlen_liefern_none(self, rohwert: object) -> None:
        assert zu_float(rohwert) is None


class TestSpaltennormalisierung:
    """Spaltenkoepfe werden auf einen vergleichbaren Kern reduziert."""

    @pytest.mark.parametrize(
        ("kopf", "erwartet"),
        [
            ("Art-Nr", "artnr"),
            ("Art.-Nr.", "artnr"),
            ("ART NR", "artnr"),
            ("Lagerbestand", "lagerbestand"),
            ("Vorlaufzeit (Tage)", "vorlaufzeittage"),
            ("Wiederbeschäftigung", "wiederbeschaeftigung"),
        ],
    )
    def test_normalisierung(self, kopf: str, erwartet: str) -> None:
        assert normalisiere_spalte(kopf) == erwartet


class TestLangformat:
    """Eine Zeile je Artikel und Periode."""

    def test_semikolon_getrennter_deutscher_export(self) -> None:
        rohdaten = (
            "Art-Nr;Datum;Verbrauch;Bestand;Vorlaufzeit;Mindestbestellmenge\n"
            "A-100;01.01.2024;120;450;14;50\n"
            "A-100;01.02.2024;135;450;14;50\n"
            "A-100;01.03.2024;150;450;14;50\n"
            "B-200;01.01.2024;1'200;300;21;0\n"
            "B-200;01.02.2024;1'150;300;21;0\n"
            "B-200;01.03.2024;1'400;300;21;0\n"
        ).encode("utf-8")

        ergebnis = lese_csv(rohdaten)

        assert ergebnis.layout == "lang"
        assert ergebnis.spalten_mapping["sku"] == "Art-Nr"
        assert ergebnis.spalten_mapping["lieferzeit"] == "Vorlaufzeit"
        assert ergebnis.spalten_mapping["menge"] == "Verbrauch"
        assert {a.sku for a in ergebnis.artikel} == {"A-100", "B-200"}

        a100 = next(a for a in ergebnis.artikel if a.sku == "A-100")
        assert a100.bestand == 450
        assert a100.lieferzeit == 14
        assert a100.mindestbestellmenge == 50
        assert [satz.menge for satz in a100.historie] == [120, 135, 150]

        b200 = next(a for a in ergebnis.artikel if a.sku == "B-200")
        assert [satz.menge for satz in b200.historie] == [1200, 1150, 1400]

    def test_englischer_export_mit_komma(self) -> None:
        rohdaten = (
            "SKU,Date,Quantity,Stock,Lead Time\n"
            "X-1,2024-01-01,10,80,7\n"
            "X-1,2024-02-01,12,80,7\n"
        ).encode("utf-8")

        ergebnis = lese_csv(rohdaten)

        assert ergebnis.layout == "lang"
        assert ergebnis.artikel[0].sku == "X-1"
        assert ergebnis.artikel[0].lieferzeit == 7

    def test_tabulator_getrennter_export(self) -> None:
        rohdaten = (
            "Material\tPeriode\tAbgang\tLager\tWBZ\n"
            "M-1\t2024-01\t5\t100\t3\n"
            "M-1\t2024-02\t7\t100\t3\n"
        ).encode("utf-8")

        ergebnis = lese_csv(rohdaten)

        assert ergebnis.artikel[0].sku == "M-1"
        assert ergebnis.artikel[0].bestand == 100

    def test_lager_wird_als_bestand_erkannt(self) -> None:
        """Die Spalte 'Lager' ist laut Schnittstellenvorgabe der Bestand."""
        rohdaten = (
            "SKU;Datum;Verbrauch;Lager;Lieferzeit\n"
            "L-1;2024-01;10;777;5\n"
            "L-1;2024-02;10;777;5\n"
        ).encode("utf-8")

        ergebnis = lese_csv(rohdaten)

        assert ergebnis.spalten_mapping["bestand"] == "Lager"
        assert ergebnis.artikel[0].bestand == 777


class TestBreitformat:
    """Eine Zeile je Artikel, Perioden als Spalten nebeneinander."""

    def test_datumsspalten_werden_als_perioden_erkannt(self) -> None:
        rohdaten = (
            "SKU,Stock,Lead Time,2024-01,2024-02,2024-03,2024-04\n"
            "X-1,80,7,10,12,11,13\n"
            "X-2,5000,30,200,180,220,210\n"
        ).encode("utf-8")

        ergebnis = lese_csv(rohdaten)

        assert ergebnis.layout == "breit"
        assert len(ergebnis.artikel) == 2
        x1 = next(a for a in ergebnis.artikel if a.sku == "X-1")
        assert [satz.menge for satz in x1.historie] == [10, 12, 11, 13]

    def test_monatsnamen_als_spaltenkoepfe(self) -> None:
        rohdaten = (
            "Artikelnummer\tLager\tWiederbeschaffungszeit\tJan 24\tFeb 24\tMrz 24\n"
            "M-9\t250\t10\t100\t110\t105\n"
        ).encode("utf-8")

        ergebnis = lese_csv(rohdaten)

        assert ergebnis.layout == "breit"
        assert [satz.menge for satz in ergebnis.artikel[0].historie] == [100, 110, 105]

    def test_perioden_werden_chronologisch_sortiert(self) -> None:
        rohdaten = (
            "SKU,Bestand,Lieferzeit,2024-03,2024-01,2024-02\n"
            "S-1,10,5,30,10,20\n"
        ).encode("utf-8")

        ergebnis = lese_csv(rohdaten)

        assert [satz.menge for satz in ergebnis.artikel[0].historie] == [10, 20, 30]


class TestZeichensaetze:
    """CH/DE-Exporte kommen selten als reines UTF-8."""

    @pytest.mark.parametrize("kodierung", ["utf-8", "utf-8-sig", "cp1252", "latin-1"])
    def test_umlaute_in_verschiedenen_kodierungen(self, kodierung: str) -> None:
        rohdaten = (
            "Artikelnummer;Datum;Verbrauch;Bestand;Vorlaufzeit\n"
            "Schräubchen-Ö;2024-01;10;50;5\n"
            "Schräubchen-Ö;2024-02;12;50;5\n"
        ).encode(kodierung)

        ergebnis = lese_csv(rohdaten)

        assert ergebnis.artikel[0].sku == "Schräubchen-Ö"


class TestFehlerfaelle:
    """Unbrauchbare Streams muessen klar und fruehzeitig scheitern."""

    def test_leerer_stream(self) -> None:
        with pytest.raises(CSVIngestFehler, match="leer"):
            lese_csv(b"")

    def test_ohne_artikelspalte(self) -> None:
        with pytest.raises(CSVIngestFehler, match="Artikelspalte"):
            lese_csv(b"alpha;beta\n1;2\n")

    def test_ohne_historie(self) -> None:
        with pytest.raises(CSVIngestFehler, match="Verbrauchshistorie"):
            lese_csv(b"Art-Nr;Bestand;Lieferzeit\nA-1;10;5\n")

    def test_ohne_lieferzeit(self) -> None:
        with pytest.raises(CSVIngestFehler, match="Lieferzeit"):
            lese_csv(b"Art-Nr;Datum;Verbrauch;Bestand\nA-1;2024-01;5;10\n")

    def test_artikel_mit_einer_periode_wird_gemeldet_nicht_geworfen(self) -> None:
        """Ein unbrauchbarer Artikel darf den ganzen Export nicht kippen."""
        rohdaten = (
            "Art-Nr;Datum;Verbrauch;Bestand;Lieferzeit\n"
            "GUT-1;2024-01;5;10;3\n"
            "GUT-1;2024-02;7;10;3\n"
            "ZU-KURZ;2024-01;5;10;3\n"
        ).encode("utf-8")

        ergebnis = lese_csv(rohdaten)

        assert [a.sku for a in ergebnis.artikel] == ["GUT-1"]
        assert any(w.sku == "ZU-KURZ" for w in ergebnis.warnungen)

    def test_zeile_ohne_artikelnummer_wird_gemeldet(self) -> None:
        rohdaten = (
            "Art-Nr;Datum;Verbrauch;Bestand;Lieferzeit\n"
            "A-1;2024-01;5;10;3\n"
            "A-1;2024-02;7;10;3\n"
            ";2024-02;1;1;1\n"
        ).encode("utf-8")

        ergebnis = lese_csv(rohdaten)

        assert len(ergebnis.artikel) == 1
        assert any("Artikelnummer" in w.meldung for w in ergebnis.warnungen)

    def test_erzwungenes_trennzeichen(self) -> None:
        rohdaten = (
            "Art-Nr|Datum|Verbrauch|Bestand|Lieferzeit\n"
            "A-1|2024-01|5|10|3\n"
            "A-1|2024-02|7|10|3\n"
        ).encode("utf-8")

        ergebnis = lese_csv(rohdaten, trennzeichen="|")

        assert ergebnis.artikel[0].sku == "A-1"
