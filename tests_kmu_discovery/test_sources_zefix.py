"""Tests des Zefix-Clients - ohne Netz.

Die Fixtures folgen der am 2026-09-16 gelesenen OpenAPI-Beschreibung
(``CompanyFull``, ``CompanyShort``, ``LegalForm``, ``ErrorDetails``); Werte
fuer den Betrieb stammen aus der LINDAS-Erhebung desselben Tages. Eine echte
Antwort lag ohne Zugangsdaten nicht vor - siehe ``docs/zefix_feldtabelle.md``.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from kmu_discovery.models import DocumentKind, Rechtsform, SourceType
from kmu_discovery.sources.base import HttpResponse, HttpTransport, RawCache, SourceBadResponseError
from kmu_discovery.sources.zefix import (
    MAX_RESULTS,
    ZEFIX_BASE_URL,
    ZefixClient,
    ZefixCompany,
    ZefixCredentials,
    ZefixCredentialsError,
    ZefixStatus,
    profile_from_company,
)
from tests_kmu_discovery.test_sources_base import FakeClock, ScriptedSender, config

NOW = datetime(2026, 9, 16, 21, 0, tzinfo=UTC)
CREDENTIALS = ZefixCredentials("benutzer", "geheim")
DETAIL_URL = "https://www.zefix.admin.ch/de/search/entity/list/firm/1198554"


# -- Fixtures ---------------------------------------------------------------- #


def legal_form_gmbh() -> dict[str, Any]:
    return {
        "id": 3,
        "uid": "0107",
        "name": {
            "de": "Gesellschaft mit beschränkter Haftung",
            "fr": "Société à responsabilité limitée",
        },
        "shortName": {"de": "GmbH", "fr": "Sàrl"},
    }


def company_full(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": "Zazuko GmbH",
        "ehraid": 1198554,
        "uid": "CHE242294601",
        "chid": "CH03640617915",
        "legalSeatId": 371,
        "legalSeat": "Biel/Bienne",
        "registryOfCommerceId": 36,
        "legalForm": legal_form_gmbh(),
        "status": "ACTIVE",
        "sogcDate": "2024-03-12",
        "deletionDate": None,
        "translation": [],
        "purpose": "Die Gesellschaft bezweckt Dienstleistungen im Bereich Linked Data.",
        "sogcPub": [
            {
                "sogcDate": "2024-03-12",
                "sogcId": 1005866543,
                "registryOfCommerceId": 36,
                "registryOfCommerceCanton": "BE",
                "registryOfCommerceJournalId": 4711,
                "registryOfCommerceJournalDate": "2024-03-07",
                "message": "Zazuko GmbH, in Biel/Bienne, ... Neue Adresse: Winkelstrasse 20.",
                "mutationTypes": [{"id": 12, "key": "ADDRESS"}],
            },
            {
                "sogcDate": "2014-06-02",
                "sogcId": 1001521234,
                "registryOfCommerceCanton": "BE",
                "message": "Neueintragung.",
                "mutationTypes": [{"id": 1, "key": "NEW"}],
            },
        ],
        "address": {
            "organisation": None,
            "careOf": None,
            "street": "Winkelstrasse",
            "houseNumber": "20",
            "addon": None,
            "poBox": None,
            "city": "Biel/Bienne",
            "swissZipCode": "2502",
        },
        "canton": "BE",
        "capitalNominal": "20000",
        "capitalCurrency": "CHF",
        "headOffices": [],
        "furtherHeadOffices": [],
        "branchOffices": [],
        "hasTakenOver": [],
        "wasTakenOverBy": [],
        "auditCompanies": [],
        "oldNames": [{"name": "Zazuko Sàrl", "sequenceNr": 1, "translation": []}],
        "cantonalExcerptWeb": "https://be.chregister.ch/cr-portal/auszug/auszug.xhtml?uid=CHE-242.294.601",
        "zefixDetailWeb": {"de": DETAIL_URL, "fr": DETAIL_URL.replace("/de/", "/fr/")},
    }
    data.update(overrides)
    return data


def company_short(**overrides: Any) -> dict[str, Any]:
    keys = (
        "name", "ehraid", "uid", "chid", "legalSeatId", "legalSeat",
        "registryOfCommerceId", "legalForm", "status", "sogcDate", "deletionDate",
    )  # fmt: skip
    data = {key: company_full()[key] for key in keys}
    data.update(overrides)
    return data


def ok(payload: object) -> HttpResponse:
    return HttpResponse(
        status=200, body=json.dumps(payload), headers={"Content-Type": "application/json"}
    )


def make_client(
    sender: ScriptedSender, cache: RawCache | None = None
) -> tuple[ZefixClient, HttpTransport]:
    clock = FakeClock()
    transport = HttpTransport(
        source=SourceType.ZEFIX,
        config=config(burst=10),
        sender=sender,
        cache=cache,
        clock=clock.now,
        sleep=clock.sleep,
        now=lambda: NOW,
        rng=lambda: 0.0,
    )
    return ZefixClient(transport, CREDENTIALS, now=lambda: NOW), transport


def sent_body(sender: ScriptedSender, index: int = 0) -> dict[str, Any]:
    _, _, body, _ = sender.calls[index]
    assert body is not None
    result: dict[str, Any] = json.loads(body)
    return result


# -- Zugangsdaten ------------------------------------------------------------ #


def test_credentials_bilden_basic_auth_header() -> None:
    expected = base64.b64encode(b"benutzer:geheim").decode()
    assert CREDENTIALS.header() == f"Basic {expected}"


def test_credentials_aus_umgebung_nur_wenn_beide_gesetzt() -> None:
    assert ZefixCredentials.from_env({"ZEFIX_USER": "a", "ZEFIX_PASSWORD": "b"}) == ("a", "b")
    assert ZefixCredentials.from_env({"ZEFIX_USER": "a"}) is None
    assert ZefixCredentials.from_env({}) is None


def test_build_ohne_zugangsdaten_bricht_klar_ab(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZEFIX_USER", raising=False)
    monkeypatch.delenv("ZEFIX_PASSWORD", raising=False)
    with pytest.raises(ZefixCredentialsError):
        ZefixClient.build()


def test_build_nimmt_limits_der_quelle_zefix() -> None:
    client = ZefixClient.build(credentials=CREDENTIALS)
    assert client.transport.source is SourceType.ZEFIX
    assert client.transport.calls_sent == 0


def test_client_verlangt_transport_der_quelle_zefix() -> None:
    clock = FakeClock()
    transport = HttpTransport(
        source=SourceType.LINDAS,
        config=config(),
        sender=ScriptedSender([]),
        clock=clock.now,
        sleep=clock.sleep,
    )
    with pytest.raises(ValueError):
        ZefixClient(transport, CREDENTIALS)


# -- Modelle ----------------------------------------------------------------- #


def test_company_liest_aliasse_und_verschachtelung() -> None:
    company = ZefixCompany.model_validate(company_full())
    assert company.uid == "CHE242294601"
    assert company.legal_form is not None and company.legal_form.uid == "0107"
    assert company.rechtsform is Rechtsform.GMBH
    assert company.address is not None and company.address.one_line() == (
        "Winkelstrasse 20, 2502 Biel/Bienne"
    )
    assert company.sogc_pub[0].sogc_date == date(2024, 3, 12)
    assert company.sogc_pub[0].mutation_types[0].key == "ADDRESS"
    assert company.old_names[0].name == "Zazuko Sàrl"
    assert company.zefix_detail_web is not None and company.zefix_detail_web.first() == DETAIL_URL


def test_company_ignoriert_unbekannte_felder_und_vertraegt_fehlende() -> None:
    company = ZefixCompany.model_validate({"name": "Nur Name AG", "neuesFeld": 1})
    assert company.name == "Nur Name AG"
    assert company.uid is None and company.address is None and company.sogc_pub == ()
    assert company.rechtsform is Rechtsform.UNBEKANNT
    assert company.earliest_sogc_date is None
    assert company.source_url is None


def test_company_abgeleitete_groessen() -> None:
    company = ZefixCompany.model_validate(company_full())
    assert company.is_active
    assert company.earliest_sogc_date == date(2014, 6, 2)
    assert company.mutation_count == 2
    assert not company.has_audit_company
    assert company.source_url == DETAIL_URL

    cancelled = ZefixCompany.model_validate(
        company_full(status=ZefixStatus.BEING_CANCELLED, zefixDetailWeb=None)
    )
    assert not cancelled.is_active
    assert cancelled.source_url and cancelled.source_url.startswith("https://be.chregister.ch/")


# -- Uebergang ins Profil ---------------------------------------------------- #


def test_profil_traegt_je_feld_einen_beleg_mit_detailseite() -> None:
    profile = profile_from_company(ZefixCompany.model_validate(company_full()), NOW)
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
    assert [e.locator for e in profile.evidence] == [
        "CompanyFull.name",
        "CompanyFull.legalForm.uid",
        "CompanyFull.status",
        "CompanyFull.address",
        "CompanyFull.purpose",
    ]
    assert all(e.source_type is SourceType.ZEFIX for e in profile.evidence)
    assert all(e.source_url == DETAIL_URL for e in profile.evidence)
    assert profile.evidence[3].quote == "Winkelstrasse 20, 2502 Biel/Bienne (BE)"
    (document,) = profile.documents
    assert document.kind is DocumentKind.REGISTER_PURPOSE and document.url == DETAIL_URL


def test_profil_ohne_detailseite_belegt_mit_api_url() -> None:
    company = ZefixCompany.model_validate(
        company_full(zefixDetailWeb=None, cantonalExcerptWeb=None)
    )
    profile = profile_from_company(company, NOW)
    assert profile.evidence[0].source_url == f"{ZEFIX_BASE_URL}/api/v1/company/uid/CHE242294601"


def test_profil_ohne_zweck_adresse_und_rechtsform_bleibt_gueltig() -> None:
    company = ZefixCompany.model_validate(
        company_full(purpose=None, address=None, canton=None, legalForm=None, status=None)
    )
    profile = profile_from_company(company, NOW)
    assert profile.documents == [] and profile.standort is None
    assert profile.rechtsform is Rechtsform.UNBEKANNT
    assert [e.locator for e in profile.evidence] == ["CompanyFull.name"]


def test_profil_verwirft_unplausible_plz_und_nimmt_sitz_als_ort() -> None:
    company = ZefixCompany.model_validate(company_full(address={"swissZipCode": "CH-2502"}))
    profile = profile_from_company(company, NOW)
    assert profile.standort is not None and profile.standort.plz is None
    without = ZefixCompany.model_validate(company_full(address=None))
    assert profile_from_company(without, NOW).standort is not None
    assert profile_from_company(without, NOW).standort.ort == "Biel/Bienne"  # type: ignore[union-attr]


def test_profil_ohne_uid_oder_firma_ist_kein_profil() -> None:
    with pytest.raises(ValueError):
        profile_from_company(ZefixCompany.model_validate(company_full(uid=None)), NOW)
    with pytest.raises(ValueError):
        profile_from_company(ZefixCompany.model_validate(company_full(name=None)), NOW)


# -- Client: Einzelabruf ----------------------------------------------------- #


def test_fetch_by_uid_sendet_get_mit_basic_auth_und_kompakter_uid() -> None:
    sender = ScriptedSender([ok([company_full()])])
    client, _ = make_client(sender)

    company = client.fetch_by_uid("CHE-242.294.601")

    assert company is not None and company.name == "Zazuko GmbH"
    method, url, body, headers = sender.calls[0]
    assert (method, url, body) == ("GET", f"{ZEFIX_BASE_URL}/api/v1/company/uid/CHE242294601", None)
    assert headers["Authorization"] == CREDENTIALS.header()
    assert headers["Accept"] == "application/json"
    assert headers["User-Agent"].startswith("kmu-discovery/")


def test_fetch_by_uid_lehnt_ungueltige_uid_ohne_netzaufruf_ab() -> None:
    client, transport = make_client(ScriptedSender([]))
    with pytest.raises(ValueError):
        client.fetch_by_uid("CHE-1/../../legalForm")
    assert transport.calls_sent == 0


def test_fetch_by_uid_bevorzugt_den_aktiven_eintrag() -> None:
    old = company_full(status="CANCELLED", ehraid=1, deletionDate="2010-01-01")
    sender = ScriptedSender([ok([old, company_full()])])
    client, _ = make_client(sender)
    company = client.fetch_by_uid("CHE-242.294.601")
    assert company is not None and company.ehraid == 1198554


def test_fetch_by_uid_nimmt_ersten_eintrag_wenn_keiner_aktiv() -> None:
    old = company_full(status="CANCELLED", ehraid=1)
    client, _ = make_client(ScriptedSender([ok([old])]))
    company = client.fetch_by_uid("CHE-242.294.601")
    assert company is not None and company.ehraid == 1


@pytest.mark.parametrize(
    "response",
    [
        HttpResponse(status=404, body='{"error":{"type":"NOT_FOUND","message":"x"}}', headers={}),
        ok({"error": {"type": "NOT_FOUND", "message": "no company"}}),
        ok([]),
    ],
)
def test_fetch_by_uid_liefert_none_bei_unbekannter_uid(response: HttpResponse) -> None:
    client, _ = make_client(ScriptedSender([response]))
    assert client.fetch_by_uid("CHE-000.000.001") is None


def test_fetch_by_uid_meldet_andere_fehlerobjekte_als_bad_response() -> None:
    client, _ = make_client(
        ScriptedSender([ok({"error": {"type": "INVALID_REQUEST_DATA", "message": "bad"}})])
    )
    with pytest.raises(SourceBadResponseError) as info:
        client.fetch_by_uid("CHE-242.294.601")
    assert "INVALID_REQUEST_DATA" in info.value.message


@pytest.mark.parametrize(
    "body", ["<html>Wartung</html>", '{"name": "kein array"}', '[{"ehraid": "x"}]']
)
def test_fetch_by_uid_meldet_unpassende_antworten(body: str) -> None:
    client, _ = make_client(ScriptedSender([HttpResponse(status=200, body=body, headers={})]))
    with pytest.raises(SourceBadResponseError):
        client.fetch_by_uid("CHE-242.294.601")


def test_fetch_by_uid_reicht_401_als_bad_response_weiter() -> None:
    client, _ = make_client(ScriptedSender([HttpResponse(status=401, body="", headers={})]))
    with pytest.raises(SourceBadResponseError) as info:
        client.fetch_by_uid("CHE-242.294.601")
    assert info.value.status == 401


def test_fetch_profile_verbindet_abruf_und_abbildung() -> None:
    client, _ = make_client(ScriptedSender([ok([company_full()])]))
    profile = client.fetch_profile("CHE-242.294.601")
    assert profile is not None and profile.evidence[0].retrieved_at == NOW


def test_wiederholter_abruf_kommt_aus_dem_cache(tmp_path: Path) -> None:
    sender = ScriptedSender([ok([company_full()])])
    client, transport = make_client(sender, cache=RawCache(tmp_path))
    first = client.fetch_by_uid("CHE-242.294.601")
    second = client.fetch_by_uid("CHE-242.294.601", max_age=timedelta(days=1))
    assert first == second
    assert (transport.calls_sent, transport.cache_hits) == (1, 1)
    cached = next(tmp_path.rglob("*.json")).read_text(encoding="utf-8")
    assert "Authorization" not in cached and "geheim" not in cached


# -- Client: Rechtsformen und Suche ----------------------------------------- #


def test_legal_forms_liest_die_liste() -> None:
    sender = ScriptedSender([ok([legal_form_gmbh(), {"id": 1, "uid": "0101"}])])
    client, _ = make_client(sender)
    forms = client.legal_forms()
    assert [f.rechtsform for f in forms] == [Rechtsform.GMBH, Rechtsform.EINZELUNTERNEHMEN]
    assert sender.calls[0][1] == f"{ZEFIX_BASE_URL}/api/v1/legalForm"


def test_search_sendet_json_mit_name_kanton_und_rechtsform() -> None:
    sender = ScriptedSender([ok([company_short()])])
    client, _ = make_client(sender)

    hits = client.search("Zazuko", kanton="be", rechtsform=Rechtsform.GMBH)

    assert [h.name for h in hits] == ["Zazuko GmbH"]
    method, url, _, headers = sender.calls[0]
    assert (method, url) == ("POST", f"{ZEFIX_BASE_URL}/api/v1/company/search")
    assert headers["Content-Type"] == "application/json"
    assert sent_body(sender) == {
        "activeOnly": True,
        "canton": "BE",
        "legalFormUid": "0107",
        "name": "Zazuko",
    }


def test_search_ohne_filter_schickt_nur_name_und_activeonly() -> None:
    sender = ScriptedSender([ok([])])
    client, _ = make_client(sender)
    assert client.search("Muster", active_only=False) == []
    assert sent_body(sender) == {"activeOnly": False, "name": "Muster"}


def test_search_mit_mehreren_codes_fragt_je_code_und_dedupliziert() -> None:
    branch = company_short(ehraid=77, name="Muster AG Zweigniederlassung Bern")
    sender = ScriptedSender([ok([branch]), ok([branch, company_short(ehraid=78)])])
    client, transport = make_client(sender)

    hits = client.search("Muster", rechtsform=Rechtsform.ZWEIGNIEDERLASSUNG)

    assert [h.ehraid for h in hits] == [77, 78]
    assert transport.calls_sent == 2
    assert [sent_body(sender, i)["legalFormUid"] for i in range(2)] == ["0111", "0151"]


def test_search_kuerzt_auf_max_results() -> None:
    hits = [company_short(ehraid=i) for i in range(5)]
    client, _ = make_client(ScriptedSender([ok(hits)]))
    assert len(client.search("Muster", max_results=2)) == 2


def test_search_behandelt_not_found_als_leer() -> None:
    client, _ = make_client(ScriptedSender([ok({"error": {"type": "NOT_FOUND"}})]))
    assert client.search("Gibtesnicht") == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "  "},
        {"name": "x", "kanton": "XX"},
        {"name": "x", "rechtsform": Rechtsform.UNBEKANNT},
        {"name": "x", "max_results": 0},
        {"name": "x", "max_results": MAX_RESULTS + 1},
    ],
)
def test_search_lehnt_ungueltige_parameter_ohne_netzaufruf_ab(kwargs: dict[str, Any]) -> None:
    client, transport = make_client(ScriptedSender([]))
    with pytest.raises(ValueError):
        client.search(**kwargs)
    assert transport.calls_sent == 0
