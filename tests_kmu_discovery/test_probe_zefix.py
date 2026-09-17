"""Tests des Zefix-Erhebungsskripts - ohne Netz.

Geprueft wird die Auswertung der OpenAPI-Beschreibung, die Abdeckungsmessung,
das Verhalten ohne Zugangsdaten, das Rendering und dass der Trockenlauf
nichts sendet.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.probe_zefix import (
    AuthRequiredError,
    JsonValue,
    RestClient,
    SpecField,
    main,
    measure_coverage,
    probe,
    render_markdown,
    spec_endpoints,
    spec_fields,
)

SPEC: dict[str, Any] = {
    "info": {"version": "2.7.2.3"},
    "components": {
        "securitySchemes": {"Zefix-Credentials": {"type": "http", "scheme": "basic"}},
        "schemas": {
            "CompanyFull": {
                "properties": {
                    "name": {"type": "string", "description": "primary business name"},
                    "status": {"type": "string", "enum": ["ACTIVE", "CANCELLED"]},
                    "sogcDate": {"type": "string", "format": "date"},
                    "sogcPub": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/SogcPublication"},
                    },
                    "address": {"$ref": "#/components/schemas/Address"},
                }
            },
            "Address": {"properties": {"city": {"type": "string"}, "poBox": {"type": "string"}}},
            "SogcPublication": {"properties": {"message": {"type": "string"}}},
            "CompanySearchQuery": {
                "required": ["name"],
                "properties": {"name": {"type": "string"}},
            },
        },
    },
    "paths": {
        "/api/v1/company/uid/{id}": {
            "get": {
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "array",
                                    "items": {"$ref": "#/components/schemas/CompanyFull"},
                                }
                            }
                        }
                    }
                }
            }
        },
        "/api/v1/legalForm": {"get": {"responses": {"200": {}}}},
    },
}


class FakeClient(RestClient):
    """Antwortet je Pfad aus einem Woerterbuch; Exceptions werden geworfen."""

    def __init__(self, answers: dict[str, Any]) -> None:
        """Nimmt die Antworten je Pfad entgegen."""
        super().__init__(base_url="https://example.invalid/ZefixPublicREST")
        self._answers = answers
        self.asked: list[str] = []

    def get(self, path: str) -> JsonValue:
        """Liefert die vorbereitete Antwort fuer ``path``."""
        return self._answer(path)

    def post(self, path: str, payload: Any) -> JsonValue:
        """Liefert die vorbereitete Antwort fuer ``path``."""
        return self._answer(path)

    def _answer(self, path: str) -> JsonValue:
        self.asked.append(path)
        if path not in self._answers:
            raise AssertionError(f"unerwartete Anfrage: {path}")
        answer = self._answers[path]
        if isinstance(answer, Exception):
            raise answer
        value: JsonValue = answer
        return value


# -- OpenAPI ------------------------------------------------------------------ #


def test_spec_fields_liest_typ_format_enum_und_pflicht() -> None:
    rows = spec_fields(SPEC, ("CompanyFull", "CompanySearchQuery", "Unbekannt"))
    by_name = {(r.schema, r.name): r for r in rows}
    assert by_name[("CompanyFull", "status")].enum == ("ACTIVE", "CANCELLED")
    assert by_name[("CompanyFull", "sogcDate")].format == "date"
    assert by_name[("CompanyFull", "sogcPub")].type == "array<SogcPublication>"
    assert by_name[("CompanyFull", "address")].type == "Address"
    assert by_name[("CompanySearchQuery", "name")].required
    assert not by_name[("CompanyFull", "name")].required
    assert {r.schema for r in rows} == {"CompanyFull", "CompanySearchQuery"}


def test_spec_endpoints_liest_methode_pfad_und_antworttyp() -> None:
    assert spec_endpoints(SPEC) == [
        ("GET", "/api/v1/company/uid/{id}", "array<CompanyFull>"),
        ("GET", "/api/v1/legalForm", "-"),
    ]


# -- Abdeckung ---------------------------------------------------------------- #


def test_measure_coverage_zaehlt_gesetzte_werte_und_listenlaenge() -> None:
    fields = spec_fields(SPEC, ("CompanyFull",))
    samples: list[dict[str, Any]] = [
        {"name": "A", "status": "ACTIVE", "sogcPub": [{}, {}], "address": {"city": "Bern"}},
        {"name": "B", "status": None, "sogcPub": [], "sogcDate": ""},
    ]
    rows = {r.name: r for r in measure_coverage(samples, fields, "CompanyFull")}
    assert (rows["name"].present, rows["name"].total, rows["name"].coverage) == (2, 2, 1.0)
    assert rows["status"].coverage == 0.5
    assert rows["sogcDate"].present == 0
    assert (rows["sogcPub"].present, rows["sogcPub"].max_items) == (1, 2)
    assert rows["address"].sample_value == '{"city": "Bern"}'


# -- Erhebung ----------------------------------------------------------------- #


def test_probe_ohne_zugang_liefert_spezifikation_und_hinweis() -> None:
    client = FakeClient(
        {
            "/v3/api-docs": SPEC,
            "/api/v1/legalForm": AuthRequiredError("HTTP 401: Zugangsdaten noetig"),
        }
    )
    result = probe(client, uids=("CHE-242.294.601",), search_name="Zazuko", search_canton=None,
                   max_results=5, max_companies=3, verbose=False)  # fmt: skip
    assert result.api_version == "2.7.2.3"
    assert result.auth_scheme == "Zefix-Credentials: http/basic"
    assert not result.credentials_used
    assert "401" in result.auth_note
    assert result.coverage == [] and result.legal_forms == []
    assert client.asked == ["/v3/api-docs", "/api/v1/legalForm"]
    text = render_markdown(result)
    assert "Nicht gemessen" in text and "| `status` |" in text and "ACTIVE, CANCELLED" in text


def test_probe_mit_zugang_misst_abdeckung_und_begrenzt_treffer() -> None:
    company: dict[str, Any] = {
        "name": "Zazuko GmbH",
        "status": "ACTIVE",
        "sogcPub": [{"message": "x"}],
        "address": {"city": "Biel/Bienne"},
    }
    client = FakeClient(
        {
            "/v3/api-docs": SPEC,
            "/api/v1/legalForm": [{"id": 3, "uid": "0107", "name": {"de": "GmbH"}}],
            "/api/v1/company/uid/CHE242294601": [company],
            "/api/v1/company/search": [{"name": f"Treffer {i}"} for i in range(9)],
        }
    )
    result = probe(client, uids=("CHE-242.294.601", "CHE-101.456.260"), search_name="Zazuko",
                   search_canton="BE", max_results=5, max_companies=1, verbose=False)  # fmt: skip
    assert result.credentials_used and result.auth_note == ""
    assert result.sample_count == 1 and result.search_hits == 9
    assert client.asked.count("/api/v1/company/uid/CHE101456260") == 0  # max_companies=1
    schemas = {(c.schema, c.name): c for c in result.coverage}
    assert schemas[("CompanyFull", "name")].coverage == 1.0
    assert schemas[("Address", "city")].coverage == 1.0
    assert schemas[("Address", "poBox")].coverage == 0.0
    assert schemas[("SogcPublication", "message")].present == 1
    assert schemas[("CompanyFull", "address")].present == 1
    text = render_markdown(result)
    assert "Stichprobe: 1 Betriebe per UID, 9 Suchtreffer." in text
    assert "| 3 | 0107 | GmbH | - |" in text


# -- CLI ---------------------------------------------------------------------- #


def test_trockenlauf_zeigt_anfragen_und_sendet_nichts(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ZEFIX_USER", raising=False)
    monkeypatch.delenv("ZEFIX_PASSWORD", raising=False)

    def kein_netz(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Trockenlauf darf nicht senden")

    monkeypatch.setattr("urllib.request.urlopen", kein_netz)
    assert main(["--dry-run", "--uid", "CHE-242.294.601", "--search-canton", "BE"]) == 0
    out = capsys.readouterr().out
    assert "GET /v3/api-docs" in out
    assert "GET /api/v1/company/uid/CHE242294601" in out
    assert '"canton": "BE"' in out
    assert "Zugangsdaten vorhanden: nein" in out


def test_json_out_schreibt_rohdaten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient({"/v3/api-docs": SPEC, "/api/v1/legalForm": AuthRequiredError("401")})
    monkeypatch.setattr("scripts.probe_zefix.RestClient", lambda **kwargs: client)
    target = tmp_path / "zefix_felder.json"
    assert main(["--quiet", "--json-out", str(target)]) == 0
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["api_version"] == "2.7.2.3"
    assert payload["credentials_used"] is False
    assert any(f["name"] == "status" for f in payload["spec_fields"])
    assert payload["requests"][0] == "GET /v3/api-docs"


def test_spec_field_ist_unveraenderlich() -> None:
    row = SpecField("A", "b", "string", None, False, (), "")
    with pytest.raises(AttributeError):
        row.name = "c"  # type: ignore[misc]
