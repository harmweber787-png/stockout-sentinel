"""Integrationstests der HTTP-Schnittstelle (In-Memory, ohne Server)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api import erstelle_app
from src.config import EngineConfig
from src.engine import baue_engine


@pytest.fixture()
def client() -> TestClient:
    """TestClient mit deterministischer, rein statistischer Prognosekette."""
    app = erstelle_app(
        baue_engine(EngineConfig(force_timesfm=False, prognose_strategie="statistisch"))
    )
    with TestClient(app) as test_client:
        yield test_client


def artikel_json(
    sku: str, mengen: list[float], bestand: float, lieferzeit: int, moq: float = 0
) -> dict:
    return {
        "sku": sku,
        "historie": [
            {"datum": f"2024-{monat:02d}-01", "menge": menge}
            for monat, menge in enumerate(mengen, start=1)
        ],
        "bestand": bestand,
        "lieferzeit": lieferzeit,
        "mindestbestellmenge": moq,
    }


class TestBetrieb:
    def test_health(self, client: TestClient) -> None:
        antwort = client.get("/health")

        assert antwort.status_code == 200
        assert antwort.json()["status"] == "ok"
        assert antwort.json()["prognose_kette"]

    def test_openapi_dokument_ist_gueltig(self, client: TestClient) -> None:
        dokument = client.get("/openapi.json").json()

        assert "/api/v1/analyze-json" in dokument["paths"]
        assert "/api/v1/analyze-csv" in dokument["paths"]


class TestAnalyzeJson:
    def test_prioritaetenliste_ist_sortiert(self, client: TestClient) -> None:
        nutzlast = [
            artikel_json("UEBER", [30] * 4, 50_000, 10),
            artikel_json("KRITISCH", [300] * 4, 0, 10),
            artikel_json("OPTIMAL", [300] * 4, 150, 10),
        ]

        antwort = client.post("/api/v1/analyze-json", json=nutzlast)

        assert antwort.status_code == 200
        daten = antwort.json()
        assert [e["sku"] for e in daten["ergebnisse"]] == ["KRITISCH", "OPTIMAL", "UEBER"]
        assert daten["meta"]["anzahl_artikel"] == 3
        assert daten["meta"]["anzahl_kritisch"] == 1

    def test_antwortfelder_vollstaendig(self, client: TestClient) -> None:
        antwort = client.post(
            "/api/v1/analyze-json", json=[artikel_json("A", [100, 110, 105], 50, 10)]
        )

        ergebnis = antwort.json()["ergebnisse"][0]
        for feld in (
            "sku",
            "prognose_tagesbedarf",
            "reichweite_tage",
            "meldebestand",
            "nachbestellmenge",
            "status",
            "status_code",
            "empfohlene_massnahme",
        ):
            assert feld in ergebnis, f"Feld '{feld}' fehlt in der Antwort"
        assert ergebnis["status_code"] in {"KRITISCH", "OPTIMAL", "UEBERBESTAND"}
        assert ergebnis["status"].endswith(ergebnis["status_code"])

    def test_gesamtbedarf_summiert_die_bestellmengen(self, client: TestClient) -> None:
        nutzlast = [
            artikel_json("A", [300] * 4, 0, 10),
            artikel_json("B", [300] * 4, 0, 10),
        ]

        daten = client.post("/api/v1/analyze-json", json=nutzlast).json()

        summe = sum(e["nachbestellmenge"] for e in daten["ergebnisse"])
        assert daten["meta"]["gesamt_nachbestellmenge"] == pytest.approx(summe)

    def test_leere_liste_wird_abgelehnt(self, client: TestClient) -> None:
        assert client.post("/api/v1/analyze-json", json=[]).status_code == 422

    def test_zu_kurze_historie_wird_abgelehnt(self, client: TestClient) -> None:
        antwort = client.post(
            "/api/v1/analyze-json", json=[artikel_json("A", [100], 10, 5)]
        )

        assert antwort.status_code == 422

    def test_negativer_bestand_wird_abgelehnt(self, client: TestClient) -> None:
        antwort = client.post(
            "/api/v1/analyze-json", json=[artikel_json("A", [100, 110], -5, 5)]
        )

        assert antwort.status_code == 422


CSV_LANG = (
    "Art-Nr;Datum;Verbrauch;Bestand;Vorlaufzeit;Mindestbestellmenge\n"
    "A-100;01.01.2024;120;80;14;50\n"
    "A-100;01.02.2024;135;80;14;50\n"
    "A-100;01.03.2024;150;80;14;50\n"
    "B-200;01.01.2024;1'200;3000;21;0\n"
    "B-200;01.02.2024;1'150;3000;21;0\n"
    "B-200;01.03.2024;1'400;3000;21;0\n"
).encode("utf-8")


class TestAnalyzeCsv:
    def test_multipart_upload(self, client: TestClient) -> None:
        antwort = client.post(
            "/api/v1/analyze-csv",
            files={"datei": ("export.csv", CSV_LANG, "text/csv")},
        )

        assert antwort.status_code == 200
        daten = antwort.json()
        assert daten["meta"]["anzahl_artikel"] == 2
        assert daten["meta"]["erkannte_spalten"]["sku"] == "Art-Nr"
        assert daten["meta"]["erkannte_spalten"]["lieferzeit"] == "Vorlaufzeit"

    def test_roher_request_body(self, client: TestClient) -> None:
        antwort = client.post(
            "/api/v1/analyze-csv",
            content=CSV_LANG,
            headers={"Content-Type": "text/csv"},
        )

        assert antwort.status_code == 200
        assert antwort.json()["meta"]["anzahl_artikel"] == 2

    def test_gleiche_struktur_wie_json_endpunkt(self, client: TestClient) -> None:
        csv_antwort = client.post(
            "/api/v1/analyze-csv", content=CSV_LANG, headers={"Content-Type": "text/csv"}
        ).json()
        json_antwort = client.post(
            "/api/v1/analyze-json", json=[artikel_json("A", [100, 110], 10, 5)]
        ).json()

        assert csv_antwort.keys() == json_antwort.keys()
        assert csv_antwort["ergebnisse"][0].keys() == json_antwort["ergebnisse"][0].keys()

    def test_breitformat_upload(self, client: TestClient) -> None:
        rohdaten = (
            "SKU,Stock,Lead Time,2024-01,2024-02,2024-03\n"
            "X-1,80,7,10,12,11\n"
        ).encode("utf-8")

        antwort = client.post(
            "/api/v1/analyze-csv", files={"datei": ("wide.csv", rohdaten, "text/csv")}
        )

        assert antwort.status_code == 200
        assert antwort.json()["ergebnisse"][0]["sku"] == "X-1"

    def test_warnungen_werden_durchgereicht(self, client: TestClient) -> None:
        rohdaten = (
            "Art-Nr;Datum;Verbrauch;Bestand;Lieferzeit\n"
            "GUT;2024-01;5;10;3\n"
            "GUT;2024-02;7;10;3\n"
            "KURZ;2024-01;5;10;3\n"
        ).encode("utf-8")

        daten = client.post(
            "/api/v1/analyze-csv", content=rohdaten, headers={"Content-Type": "text/csv"}
        ).json()

        assert daten["meta"]["anzahl_artikel"] == 1
        assert any(w["sku"] == "KURZ" for w in daten["meta"]["warnungen"])

    def test_leerer_stream_wird_abgelehnt(self, client: TestClient) -> None:
        antwort = client.post(
            "/api/v1/analyze-csv", content=b"", headers={"Content-Type": "text/csv"}
        )

        assert antwort.status_code == 422

    def test_unbrauchbares_csv_meldet_sprechenden_fehler(self, client: TestClient) -> None:
        antwort = client.post(
            "/api/v1/analyze-csv",
            content=b"alpha;beta\n1;2\n",
            headers={"Content-Type": "text/csv"},
        )

        assert antwort.status_code == 422
        assert "Artikelspalte" in antwort.json()["detail"]


class TestZustandslosigkeit:
    """Zwei identische Requests muessen identische Antworten liefern."""

    def test_wiederholter_request_liefert_identisches_ergebnis(
        self, client: TestClient
    ) -> None:
        nutzlast = [artikel_json("A", [100, 110, 105, 120], 50, 10)]

        erste = client.post("/api/v1/analyze-json", json=nutzlast).json()
        zweite = client.post("/api/v1/analyze-json", json=nutzlast).json()

        assert erste == zweite

    def test_frueherer_upload_beeinflusst_spaeteren_nicht(
        self, client: TestClient
    ) -> None:
        client.post(
            "/api/v1/analyze-csv", content=CSV_LANG, headers={"Content-Type": "text/csv"}
        )
        nutzlast = [artikel_json("NEU", [10, 12], 5, 3)]

        daten = client.post("/api/v1/analyze-json", json=nutzlast).json()

        assert [e["sku"] for e in daten["ergebnisse"]] == ["NEU"]
