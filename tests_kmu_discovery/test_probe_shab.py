"""Tests des SHAB-Erhebungsskripts - ohne Netz.

Geprueft wird der Aufbau der Listenparameter, das Falten von JSON und XML,
die Abdeckungsmessung, das Aufloesen benannter XSD-Typen, das Rendering und
dass der Trockenlauf nichts sendet.
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.probe_shab import (
    JsonValue,
    ProbeError,
    RestClient,
    flatten_json,
    flatten_xml,
    list_params,
    main,
    measure_coverage,
    probe,
    render_markdown,
    schema_elements,
)

XSD = """<?xml version='1.0' encoding='UTF-8'?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" version="1.26">
  <xs:element name="publication" type="publicationType"/>
  <xs:complexType name="publicationType">
    <xs:sequence>
      <xs:element name="meta" type="metaType"/>
      <xs:element name="content" type="contentType"/>
    </xs:sequence>
  </xs:complexType>
  <xs:complexType name="metaType">
    <xs:sequence><xs:element name="id" type="xs:string"/></xs:sequence>
  </xs:complexType>
  <xs:complexType name="contentType">
    <xs:sequence>
      <xs:element name="publicationText" type="xs:string">
        <xs:annotation>
          <xs:documentation>&lt;div&gt;Publication  text&lt;/div&gt;</xs:documentation>
          <xs:documentation>Text der Publikation</xs:documentation>
        </xs:annotation>
      </xs:element>
      <xs:element name="commonsNew" type="commonsType" minOccurs="0" maxOccurs="unbounded"/>
    </xs:sequence>
  </xs:complexType>
  <xs:complexType name="commonsType">
    <xs:sequence>
      <xs:element name="purpose" type="xs:string" minOccurs="0"/>
    </xs:sequence>
  </xs:complexType>
</xs:schema>"""

PUBLICATION_XML = """<?xml version='1.0' encoding='UTF-8'?>
<HR01:publication xmlns:HR01="https://shab.ch/shab/HR01-export">
  <meta><id>abc</id><cantons>ZH</cantons></meta>
  <content>
    <journalDate>2026-09-14</journalDate>
    <commonsNew><company><name>Beispiel AG</name></company></commonsNew>
    <leer></leer>
  </content>
</HR01:publication>"""


class FakeClient(RestClient):
    """Antwortet je Pfad aus einem Woerterbuch, ohne Netzwerk."""

    def __init__(self, answers: dict[str, Any]) -> None:
        """Nimmt die Antworten je Pfad entgegen."""
        super().__init__(base_url="https://example.invalid/api/v1")
        self._answers = answers
        self.asked: list[str] = []

    def get_json(self, path: str, params: Any = ()) -> JsonValue:
        """Liefert die vorbereitete JSON-Antwort; unterscheidet nach Seitengroesse."""
        size = dict(params).get("pageRequest.size", "")
        key = f"{path}?size={size}"
        self.asked.append(key)
        answer = self._answers.get(key, self._answers.get(path))
        if answer is None:
            raise AssertionError(f"unerwartete Anfrage: {key}")
        if isinstance(answer, Exception):
            raise answer
        value: JsonValue = answer
        return value

    def get_text(self, path: str, accept: str = "application/xml") -> str:
        """Liefert die vorbereitete Text-Antwort."""
        self.asked.append(path)
        answer = self._answers.get(path)
        if answer is None:
            raise AssertionError(f"unerwartete Anfrage: {path}")
        if isinstance(answer, Exception):
            raise answer
        return str(answer)


# -- Parameter --------------------------------------------------------------- #


def test_list_params_folgt_der_api_doku() -> None:
    params = list_params("HR02", ["ZH", "ZG"], "2026-06-19", "2026-09-17", 10, page=2)
    as_dict: dict[str, list[str]] = {}
    for key, value in params:
        as_dict.setdefault(key, []).append(value)
    assert as_dict["publicationStates"] == ["PUBLISHED"]
    assert as_dict["subRubrics"] == ["HR02"]
    assert as_dict["cantons"] == ["ZH", "ZG"]
    assert as_dict["pageRequest.size"] == ["10"]
    assert as_dict["pageRequest.page"] == ["2"]
    assert "rubrics" not in as_dict  # Rubrik und Unterrubrik werden mit ODER verknuepft


# -- Falten ------------------------------------------------------------------ #


def test_flatten_json_faltet_verschachtelung_und_listen() -> None:
    flat = flatten_json({"a": {"b": 1}, "c": [{"d": 2}, {"d": 3}], "e": [], "f": None})
    assert flat == {"a.b": 1, "c[].d": 2, "e": [], "f": None}


def test_flatten_xml_liefert_pfade_ohne_namensraum() -> None:
    flat = flatten_xml(ET.fromstring(PUBLICATION_XML))
    assert flat["meta/id"] == "abc"
    assert flat["meta/cantons"] == "ZH"
    assert flat["content/commonsNew/company/name"] == "Beispiel AG"
    assert flat["content/leer"] == ""
    assert not any(path.startswith("{") for path in flat)


# -- Abdeckung --------------------------------------------------------------- #


def test_measure_coverage_zaehlt_gesetzte_werte() -> None:
    rows = {
        r.path: r
        for r in measure_coverage("HR01", [{"a": "x", "b": "", "c": None}, {"a": "y", "b": "z"}])
    }
    assert (rows["a"].present, rows["a"].coverage) == (2, 1.0)
    assert rows["b"].coverage == 0.5
    assert rows["c"].present == 0
    assert rows["a"].sample_value == "x"
    assert rows["a"].scope == "HR01"


# -- XSD --------------------------------------------------------------------- #


def test_schema_elements_loest_benannte_typen_auf() -> None:
    rows = {r.path: r for r in schema_elements("HR01", XSD)}
    assert set(rows) == {"publicationText", "commonsNew", "commonsNew/purpose"}
    assert rows["publicationText"].type == "xs:string"
    assert rows["publicationText"].documentation == "Publication text"
    assert (rows["commonsNew"].min_occurs, rows["commonsNew"].max_occurs) == ("0", "unbounded")
    assert rows["commonsNew/purpose"].min_occurs == "0"
    assert all(r.sub_rubric == "HR01" for r in rows.values())


def test_schema_elements_ohne_contenttype_bleibt_leer() -> None:
    assert schema_elements("HR01", '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"/>') == []


# -- Erhebung ---------------------------------------------------------------- #


def _list_payload(total: int, count: int = 1) -> dict[str, Any]:
    return {
        "total": total,
        "content": [
            {"meta": {"id": f"id-{i}", "subRubric": "HR01", "cantons": ["ZH"]}}
            for i in range(count)
        ],
    }


def test_probe_erhebt_zahlen_abdeckung_und_schema() -> None:
    client = FakeClient(
        {
            "/publications?size=10": _list_payload(4177, 2),
            "/publications?size=1": _list_payload(2438),
            "/publications/id-0/xml": PUBLICATION_XML,
            "/schemas/shab/1.26/HR01-export.xsd": XSD,
        }
    )

    result = probe(
        client,
        cantons=["ZH", "AG"],
        sub_rubrics=["HR01"],
        days=90,
        sample_size=10,
        max_xml=1,
        schema_version="1.26",
        today=date(2026, 9, 17),
        verbose=False,
    )

    assert result.date_start == "2026-06-19" and result.date_end == "2026-09-17"
    assert result.totals == {"HR01": 4177}
    assert result.totals_per_canton == {"HR01": {"ZH": 2438, "AG": 2438}}
    assert result.sample_ids == {"HR01": ["id-0"]}
    meta = {r.path: r for r in result.meta_coverage}
    assert meta["meta.id"].coverage == 1.0
    content = {r.path: r for r in result.content_coverage}
    assert content["content/commonsNew/company/name"].sample_value == "Beispiel AG"
    assert len(result.schema_elements) == 3
    assert client.asked.count("/publications/id-1/xml") == 0  # max_xml=1

    text = render_markdown(result)
    assert "| HR01 | 2438 | 2438 | 4177 |" in text
    assert "`content/commonsNew/company/name`" in text
    assert "Publication text" in text


def test_probe_reicht_transportfehler_weiter() -> None:
    client = FakeClient({"/publications?size=10": ProbeError("HTTP 503")})
    with pytest.raises(ProbeError):
        probe(client, cantons=["ZH"], sub_rubrics=["HR01"], days=90, sample_size=10,
              max_xml=0, schema_version="1.26", today=date(2026, 9, 17), verbose=False)  # fmt: skip


def test_probe_lehnt_fremde_listenantwort_ab() -> None:
    client = FakeClient({"/publications?size=10": ["kein", "objekt"]})
    with pytest.raises(ProbeError):
        probe(client, cantons=["ZH"], sub_rubrics=["HR01"], days=90, sample_size=10,
              max_xml=0, schema_version="1.26", today=date(2026, 9, 17), verbose=False)  # fmt: skip


# -- CLI --------------------------------------------------------------------- #


def test_trockenlauf_zeigt_anfragen_und_sendet_nichts(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def kein_netz(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Trockenlauf darf nicht senden")

    monkeypatch.setattr("urllib.request.urlopen", kein_netz)
    assert main(["--dry-run", "--today", "2026-09-17", "--days", "90"]) == 0
    out = capsys.readouterr().out
    assert "subRubrics=HR01" in out and "subRubrics=HR03" in out
    assert "cantons=ZH&cantons=AG&cantons=ZG" in out
    assert "publicationDate.start=2026-06-19" in out
    assert "/schemas/shab/1.26/HR02-export.xsd" in out


@pytest.mark.parametrize(
    "argv",
    [
        ["--sample-size", "0"],
        ["--sample-size", "101"],
        ["--max-xml", "11"],
        ["--days", "0"],
    ],
)
def test_ungueltige_parameter_brechen_ab(argv: list[str]) -> None:
    assert main(["--dry-run", *argv]) == 2


def test_json_out_schreibt_rohdaten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient(
        {
            "/publications?size=10": _list_payload(4177, 1),
            "/publications?size=1": _list_payload(2438),
            "/publications/id-0/xml": PUBLICATION_XML,
            "/schemas/shab/1.26/HR01-export.xsd": XSD,
        }
    )
    monkeypatch.setattr("scripts.probe_shab.RestClient", lambda **kwargs: client)
    target = tmp_path / "shab_felder.json"
    argv = ["--quiet", "--sub-rubric", "HR01", "--canton", "ZH", "--today", "2026-09-17",
            "--json-out", str(target)]  # fmt: skip
    assert main(argv) == 0
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["totals"] == {"HR01": 4177}
    assert payload["cantons"] == ["ZH"]
    assert payload["requests"][0].startswith("GET /publications?")
    assert any(e["path"] == "commonsNew/purpose" for e in payload["schema_elements"])
