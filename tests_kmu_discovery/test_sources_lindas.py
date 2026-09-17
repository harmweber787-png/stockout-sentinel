"""Tests des LINDAS-Clients - ohne Netz, mit Antworten in der live gesehenen Form.

Die Fixtures bilden zwei echte Betriebe nach, wie der Zefix-Graph sie am
2026-09-16 geliefert hat (eine Zeile je ``schema:name``-Fassung).
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kmu_discovery.models import (
    DocumentKind,
    Rechtsform,
    SourceType,
)
from kmu_discovery.sources.base import HttpResponse, HttpTransport, RawCache, SourceBadResponseError
from kmu_discovery.sources.lindas import (
    LEGAL_FORM_CODES,
    LINDAS_ENDPOINT,
    ZEFIX_GRAPH,
    LindasClient,
    ZefixRecord,
    legal_form_code,
    legal_form_codes_for,
    parse_records,
    parse_sparql_json,
    profile_from_record,
    rechtsform_from_legal_form,
    sparql_string,
)
from tests_kmu_discovery.test_sources_base import FakeClock, ScriptedSender, config

NOW = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)
LF = "https://ld.admin.ch/ech/97/legalforms/"
ZAZUKO_IRI = "https://register.ld.admin.ch/zefix/company/1198554"
DANSTAR_IRI = "https://register.ld.admin.ch/zefix/company/41947"


# -- Fixtures ---------------------------------------------------------------- #


def _lit(value: str, lang: str | None = None) -> dict[str, str]:
    raw = {"type": "literal", "value": value}
    if lang:
        raw["xml:lang"] = lang
    return raw


def _uri(value: str) -> dict[str, str]:
    return {"type": "uri", "value": value}


def zazuko_row() -> dict[str, dict[str, str]]:
    return {
        "company": _uri(ZAZUKO_IRI),
        "uid": _lit("CHE242294601"),
        "chid": _lit("CH03640617915"),
        "legalName": _lit("Zazuko GmbH"),
        "description": _lit("Die Gesellschaft bezweckt Dienstleistungen im Bereich Linked Data."),
        "legalForm": _uri(LF + "0107"),
        "municipality": _uri("https://ld.admin.ch/municipality/371"),
        "street": _lit("Winkelstrasse 20"),
        "postalCode": _lit("2502"),
        "locality": _lit("Biel/Bienne"),
        "region": _lit("BE"),
    }


def danstar_rows() -> list[dict[str, dict[str, str]]]:
    base = {
        "company": _uri(DANSTAR_IRI),
        "uid": _lit("CHE101456260"),
        "chid": _lit("CH17030135471"),
        "legalName": _lit("Danstar Ferment AG"),
        "description": _lit("Handel mit Gütern aller Art."),
        "legalForm": _uri(LF + "0106"),
        "municipality": _uri("https://ld.admin.ch/municipality/1711"),
        "street": _lit("Poststrasse 30"),
        "postalCode": _lit("6300"),
        "locality": _lit("Zug"),
        "region": _lit("ZG"),
    }
    return [
        {**base, "name": _lit("Danstar Ferment SA", "fr")},
        {**base, "name": _lit("Danstar Ferment Ltd", "en")},
    ]


def sparql_json(rows: list[dict[str, dict[str, str]]]) -> str:
    variables = sorted({key for row in rows for key in row})
    return json.dumps({"head": {"vars": variables}, "results": {"bindings": rows}})


def ok(rows: list[dict[str, dict[str, str]]]) -> HttpResponse:
    return HttpResponse(
        status=200,
        body=sparql_json(rows),
        headers={"Content-Type": "application/sparql-results+json"},
    )


def make_client(
    sender: ScriptedSender, cache: RawCache | None = None
) -> tuple[LindasClient, HttpTransport]:
    clock = FakeClock()
    transport = HttpTransport(
        source=SourceType.LINDAS,
        config=config(burst=10),
        sender=sender,
        cache=cache,
        clock=clock.now,
        sleep=clock.sleep,
        now=lambda: NOW,
        rng=lambda: 0.0,
    )
    return LindasClient(transport, now=lambda: NOW), transport


def sent_query(sender: ScriptedSender, index: int = 0) -> str:
    """Die SPARQL-Abfrage, die im ``index``-ten Aufruf gesendet wurde."""
    _, _, body, _ = sender.calls[index]
    assert body is not None
    return urllib.parse.parse_qs(body)["query"][0]


def make_record(**overrides: object) -> ZefixRecord:
    data: dict[str, object] = {
        "iri": ZAZUKO_IRI,
        "uid": "CHE-242.294.601",
        "legal_name": "Zazuko GmbH",
        "legal_form_iri": LF + "0107",
        "zweck": "Die Gesellschaft bezweckt Dienstleistungen im Bereich Linked Data.",
        "street": "Winkelstrasse 20",
        "plz": "2502",
        "ort": "Biel/Bienne",
        "kanton": "BE",
        "retrieved_at": NOW,
    }
    data.update(overrides)
    return ZefixRecord.model_validate(data)


# -- SPARQL-Grundlagen ------------------------------------------------------- #


def test_parse_sparql_json_liest_typ_sprache_und_datentyp() -> None:
    rows = parse_sparql_json(
        '{"head":{"vars":["a","b"]},"results":{"bindings":[{"a":{"type":"uri","value":"x"},'
        '"b":{"type":"literal","value":"3","datatype":"http://www.w3.org/2001/XMLSchema#integer"}}]}}'
    )
    assert rows[0]["a"].kind == "uri"
    assert rows[0]["b"].datatype and rows[0]["b"].datatype.endswith("#integer")


@pytest.mark.parametrize("body", ["[]", "{}", '{"results":{"bindings":{}}}', "<html>"])
def test_parse_sparql_json_lehnt_fremde_antworten_ab(body: str) -> None:
    with pytest.raises(ValueError):
        parse_sparql_json(body)


def test_sparql_string_maskiert_anfuehrungszeichen_und_umbrueche() -> None:
    assert sparql_string('a"b\\c\nd') == '"a\\"b\\\\c\\nd"'


# -- Rechtsform -------------------------------------------------------------- #

#: Alle Codes, die der Zefix-Graph am 2026-09-16 tatsaechlich fuehrte.
LIVE_CODES = [
    "0107", "0106", "0101", "0110", "0151", "0109", "0103", "0108",
    "0111", "0104", "0117", "0113", "0119", "0118", "0105",
]  # fmt: skip


def test_rechtsformtabelle_deckt_alle_live_gesehenen_codes() -> None:
    assert set(LIVE_CODES) <= set(LEGAL_FORM_CODES)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("0101", Rechtsform.EINZELUNTERNEHMEN),
        ("0106", Rechtsform.AG),
        ("0107", Rechtsform.GMBH),
        ("0110", Rechtsform.STIFTUNG),
        ("0151", Rechtsform.ZWEIGNIEDERLASSUNG),
        ("0117", Rechtsform.OEFFENTLICH_RECHTLICH),
        ("0113", Rechtsform.UNBEKANNT),
    ],
)
def test_rechtsform_aus_iri(code: str, expected: Rechtsform) -> None:
    assert rechtsform_from_legal_form(LF + code) is expected


def test_rechtsform_bei_fremder_oder_fehlender_iri_unbekannt() -> None:
    assert rechtsform_from_legal_form(None) is Rechtsform.UNBEKANNT
    assert rechtsform_from_legal_form("https://example.org/0106") is Rechtsform.UNBEKANNT
    assert legal_form_code(LF + "abc") is None


def test_codes_je_rechtsform_bilden_die_tabelle_zurueck() -> None:
    for code, form in LEGAL_FORM_CODES.items():
        if form is Rechtsform.UNBEKANNT:
            assert code not in legal_form_codes_for(form)
        else:
            assert code in legal_form_codes_for(form)
    assert legal_form_codes_for(Rechtsform.AG) == ("0105", "0106")


# -- Datensatz --------------------------------------------------------------- #


def test_parse_records_faltet_sprachfassungen_zu_einem_betrieb() -> None:
    rows = parse_sparql_json(sparql_json([*danstar_rows(), zazuko_row()]))
    records = parse_records(rows, NOW)
    assert [r.legal_name for r in records] == ["Danstar Ferment AG", "Zazuko GmbH"]
    danstar, zazuko = records
    assert danstar.alternate_names == ("Danstar Ferment SA", "Danstar Ferment Ltd")
    assert danstar.uid == "CHE-101.456.260"
    assert danstar.municipality_bfs == 1711
    assert zazuko.alternate_names == ()
    assert zazuko.chid == "CH03640617915"
    assert zazuko.retrieved_at == NOW


def test_parse_records_nimmt_firma_nicht_als_sprachfassung() -> None:
    rows = [*danstar_rows(), {**danstar_rows()[0], "name": _lit("Danstar Ferment AG")}]
    (record,) = parse_records(parse_sparql_json(sparql_json(rows)), NOW)
    assert "Danstar Ferment AG" not in record.alternate_names


def test_parse_records_ueberspringt_unbrauchbare_zeilen() -> None:
    no_uid = {k: v for k, v in zazuko_row().items() if k != "uid"}
    bad_uid = {**zazuko_row(), "uid": _lit("nicht-eine-uid")}
    no_company = {k: v for k, v in zazuko_row().items() if k != "company"}
    rows = parse_sparql_json(sparql_json([no_uid, bad_uid, no_company]))
    assert parse_records(rows, NOW) == []


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Muster AG in Liquidation", True),
        ("Exemple Sàrl en liquidation", True),
        ("Esempio SA in liquidazione", True),
        ("Liquidationen Muster GmbH", False),
        ("Muster AG", False),
    ],
)
def test_liquidationszusatz_im_namen(name: str, expected: bool) -> None:
    assert make_record(legal_name=name).in_liquidation is expected


# -- Uebergang ins Profil ---------------------------------------------------- #


def test_profil_traegt_je_feld_einen_beleg_mit_betriebs_iri() -> None:
    profile = profile_from_record(make_record())
    assert profile.uid == "CHE-242.294.601"
    assert profile.name == "Zazuko GmbH"
    assert profile.rechtsform is Rechtsform.GMBH
    assert profile.standort is not None
    assert (profile.standort.kanton, profile.standort.plz, profile.standort.ort) == (
        "BE",
        "2502",
        "Biel/Bienne",
    )
    assert profile.zweck and profile.zweck.startswith("Die Gesellschaft bezweckt")
    assert profile.noga_codes == [] and profile.website is None and profile.emails == []

    locators = [e.locator for e in profile.evidence]
    assert locators == [
        "schema:legalName",
        "schema:additionalType",
        "schema:address",
        "schema:description",
    ]
    assert all(e.source_type is SourceType.LINDAS for e in profile.evidence)
    assert all(e.source_url == ZAZUKO_IRI for e in profile.evidence)
    assert profile.evidence[2].quote == "Winkelstrasse 20, 2502 Biel/Bienne (BE)"


def test_profil_haengt_zweck_als_registerdokument_an() -> None:
    profile = profile_from_record(make_record())
    (document,) = profile.documents
    assert document.kind is DocumentKind.REGISTER_PURPOSE
    assert document.url == ZAZUKO_IRI
    assert document.text == profile.zweck


def test_profil_ohne_zweck_und_ohne_kanton_bleibt_gueltig() -> None:
    profile = profile_from_record(make_record(zweck=None, kanton=None, legal_form_iri=None))
    assert profile.documents == []
    assert profile.standort is None
    assert profile.rechtsform is Rechtsform.UNBEKANNT
    assert [e.locator for e in profile.evidence] == ["schema:legalName"]


def test_profil_verwirft_unplausible_plz_und_fremdes_kantonskuerzel() -> None:
    profile = profile_from_record(make_record(plz="CH-2502"))
    assert profile.standort is not None and profile.standort.plz is None
    assert profile_from_record(make_record(kanton="XX")).standort is None


def test_profil_kuerzt_ueberlange_belegzitate() -> None:
    profile = profile_from_record(make_record(zweck="x" * 5000))
    assert len(profile.evidence[-1].quote) == 2000
    assert len(profile.documents[0].text) == 5000


# -- Client ------------------------------------------------------------------ #


def test_fetch_by_uid_sendet_sparql_als_formular_an_den_endpunkt() -> None:
    sender = ScriptedSender([ok([zazuko_row()])])
    client, _ = make_client(sender)

    record = client.fetch_by_uid("CHE-242.294.601")

    assert record is not None and record.legal_name == "Zazuko GmbH"
    method, url, _, headers = sender.calls[0]
    assert (method, url) == ("POST", LINDAS_ENDPOINT)
    assert headers["Accept"] == "application/sparql-results+json"
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert headers["User-Agent"].startswith("kmu-discovery/")
    query = sent_query(sender)
    assert f"GRAPH <{ZEFIX_GRAPH}>" in query
    assert '"CompanyUID"' in query and '"CHE242294601"' in query
    assert "<http://schema.org/legalName> ?legalName" in query


def test_fetch_by_uid_normalisiert_schreibweisen_auf_dieselbe_abfrage() -> None:
    sender = ScriptedSender([ok([zazuko_row()]), ok([zazuko_row()])])
    client, _ = make_client(sender)
    client.fetch_by_uid("CHE-242.294.601")
    client.fetch_by_uid("che242294601")
    assert sent_query(sender, 0) == sent_query(sender, 1)


def test_fetch_by_uid_lehnt_ungueltige_uid_ohne_netzaufruf_ab() -> None:
    sender = ScriptedSender([])
    client, transport = make_client(sender)
    with pytest.raises(ValueError):
        client.fetch_by_uid('CHE-1"} } UNION { ?s ?p ?o')
    assert transport.calls_sent == 0


def test_fetch_by_uid_liefert_none_bei_leerem_ergebnis() -> None:
    client, _ = make_client(ScriptedSender([ok([]), ok([])]))
    assert client.fetch_by_uid("CHE-000.000.001") is None
    assert client.fetch_profile("CHE-000.000.001") is None


def test_fetch_profile_verbindet_abruf_und_abbildung() -> None:
    client, _ = make_client(ScriptedSender([ok(danstar_rows())]))
    profile = client.fetch_profile("CHE-101.456.260")
    assert profile is not None
    assert profile.rechtsform is Rechtsform.AG
    assert profile.standort is not None and profile.standort.kanton == "ZG"


def test_query_meldet_unlesbare_antwort_als_bad_response() -> None:
    sender = ScriptedSender([HttpResponse(status=200, body="<html>Wartung</html>", headers={})])
    client, _ = make_client(sender)
    with pytest.raises(SourceBadResponseError) as info:
        client.fetch_by_uid("CHE-242.294.601")
    assert info.value.status == 200 and info.value.source is SourceType.LINDAS


def test_wiederholter_abruf_kommt_aus_dem_cache(tmp_path: Path) -> None:
    sender = ScriptedSender([ok([zazuko_row()])])
    client, transport = make_client(sender, cache=RawCache(tmp_path))
    first = client.fetch_by_uid("CHE-242.294.601")
    second = client.fetch_by_uid("CHE-242.294.601", max_age=timedelta(days=1))
    assert first == second
    assert (transport.calls_sent, transport.cache_hits) == (1, 1)


def test_client_verlangt_transport_der_quelle_lindas() -> None:
    clock = FakeClock()
    transport = HttpTransport(
        source=SourceType.ZEFIX,
        config=config(),
        sender=ScriptedSender([]),
        clock=clock.now,
        sleep=clock.sleep,
    )
    with pytest.raises(ValueError):
        LindasClient(transport)


def test_build_nimmt_limits_der_quelle_lindas() -> None:
    client = LindasClient.build()
    assert client.transport.source is SourceType.LINDAS
    assert client.transport.calls_sent == 0


# -- Seitenweise Abfrage ----------------------------------------------------- #


def test_list_companies_baut_unterabfrage_mit_kanton_und_rechtsformen() -> None:
    sender = ScriptedSender([ok([zazuko_row()])])
    client, _ = make_client(sender)

    client.list_companies("gl", [Rechtsform.GMBH, Rechtsform.AG], limit=5, offset=10)

    query = sent_query(sender)
    assert "{ SELECT ?company WHERE {" in query
    assert '<http://schema.org/addressRegion> "GL"' in query
    assert f"VALUES ?pageForm {{ <{LF}0105> <{LF}0106> <{LF}0107> }}" in query
    assert "ORDER BY ?company LIMIT 5 OFFSET 10 }" in query


def test_list_companies_ohne_rechtsformfilter_hat_keine_values_klausel() -> None:
    sender = ScriptedSender([ok([])])
    client, _ = make_client(sender)
    client.list_companies("ZH")
    assert "VALUES" not in sent_query(sender)


@pytest.mark.parametrize(
    "kwargs",
    [{"kanton": "XX"}, {"kanton": "ZH", "limit": 0}, {"kanton": "ZH", "limit": 501},
     {"kanton": "ZH", "offset": -1}],
)  # fmt: skip
def test_list_companies_lehnt_ungueltige_parameter_ab(kwargs: dict[str, object]) -> None:
    client, transport = make_client(ScriptedSender([]))
    with pytest.raises(ValueError):
        client.list_companies(**kwargs)  # type: ignore[arg-type]
    assert transport.calls_sent == 0


def test_list_companies_mit_nur_unfilterbaren_rechtsformen_fragt_nicht() -> None:
    client, transport = make_client(ScriptedSender([]))
    assert client.list_companies("ZH", [Rechtsform.UNBEKANNT]) == []
    assert transport.calls_sent == 0


def test_iter_companies_laedt_seiten_bis_zur_kurzen_seite() -> None:
    def page(start: int, count: int) -> HttpResponse:
        rows = []
        for i in range(start, start + count):
            row = zazuko_row()
            row["company"] = _uri(f"https://register.ld.admin.ch/zefix/company/{i}")
            row["uid"] = _lit(f"CHE1000000{i:02d}")
            rows.append(row)
        return ok(rows)

    sender = ScriptedSender([page(0, 2), page(2, 2), page(4, 1)])
    client, transport = make_client(sender)

    records = list(client.iter_companies("BE", page_size=2))

    assert len(records) == 5
    assert transport.calls_sent == 3
    assert "LIMIT 2 OFFSET 0 }" in sent_query(sender, 0)
    assert "LIMIT 2 OFFSET 4 }" in sent_query(sender, 2)
