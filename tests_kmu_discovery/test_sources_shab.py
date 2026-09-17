"""Tests des SHAB-Clients - ohne Netz.

Die Fixtures sind gekuerzte, aber strukturgetreue Nachbauten echter
Publikationen vom 2026-09-17 (Aemter ZH, AG, ZG), wie sie
``scripts/probe_shab.py`` erhoben hat. Personennamen in den
Publikationstexten sind ersetzt.
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from kmu_discovery.models import DocumentKind, Rechtsform, SourceType
from kmu_discovery.sources.base import HttpResponse, HttpTransport, RawCache, SourceBadResponseError
from kmu_discovery.sources.shab import (
    CONTACT_KINDS,
    MAX_PAGE_SIZE,
    SHAB_BASE_URL,
    MutationKind,
    ShabClient,
    SubRubric,
    parse_list,
    parse_publication,
    profile_from_publication,
    total_hits,
)
from tests_kmu_discovery.test_sources_base import FakeClock, ScriptedSender, config

NOW = datetime(2026, 9, 17, 9, 0, tzinfo=UTC)
TODAY = date(2026, 9, 17)
PUB_ID = "b714a093-1676-45c9-bc33-28300f249a89"


# -- Fixtures ---------------------------------------------------------------- #


def hr01_xml(**overrides: str) -> str:
    """HR01 Neueintragung, Einzelunternehmen in Wettingen (AG)."""
    values: dict[str, str] = {
        "id": "0c65a54b-7361-491f-890d-82ab8272d5a5",
        "canton": "AG",
        "uid": "CHE-306.462.688",
        "legalForm": "0101",
        "name": "Nothelfer Star Beispiel",
    }
    values.update(overrides)
    return f"""<?xml version='1.0' encoding='UTF-8'?>
<HR01:publication xmlns:HR01="https://shab.ch/shab/HR01-export">
<meta>
  <id>{values["id"]}</id>
  <rubric>HR</rubric>
  <subRubric>HR01</subRubric>
  <language>de</language>
  <publicationNumber>HR01-1006759110</publicationNumber>
  <publicationState>PUBLISHED</publicationState>
  <publicationDate>2026-09-17</publicationDate>
  <primaryTenantCode>shab</primaryTenantCode>
  <cantons>{values["canton"]}</cantons>
  <title>
    <de>Neueintragung {values["name"]}, Wettingen</de>
    <en>New entries {values["name"]}, Wettingen</en>
  </title>
</meta>
<content>
  <testImport>false</testImport>
  <journalNumber>13128</journalNumber>
  <journalDate>2026-09-14</journalDate>
  <publicationText>{values["name"]}, in Wettingen, {values["uid"]}, Guetestrasse 12, 5430
    Wettingen, Einzelunternehmen (Neueintragung). Zweck: Unterrichtung von Erwachsenen.
    Eingetragene Personen: Beispiel, Anna, Inhaberin.</publicationText>
  <commonsNew>
    <company>
      <name>{values["name"]}</name>
      <uid>{values["uid"]}</uid>
      <uidOrganisationId>306462688</uidOrganisationId>
      <uidOrganisationIdCategorie>CHE</uidOrganisationIdCategorie>
      <code13>CH40016147828</code13>
      <seat>Wettingen</seat>
      <legalForm>{values["legalForm"]}</legalForm>
      <noAddress>false</noAddress>
      <address>
        <street>Guetestrasse</street>
        <houseNumber>12</houseNumber>
        <swissZipCode>5430</swissZipCode>
        <town>Wettingen</town>
      </address>
    </company>
    <purpose>Unterrichtung von Erwachsenen sowie Erbringung von Unterrichtsdiensten.</purpose>
    <revision><optingOut>false</optingOut></revision>
  </commonsNew>
  <transaction><registration>true</registration></transaction>
  <senderOffice><officeName>Handelsregisteramt des Kantons Aargau</officeName></senderOffice>
</content>
</HR01:publication>"""


def hr02_xml(
    others: str = "true",
    address_changed: str = "false",
    seat_changed: str = "false",
    name_changed: str = "false",
    purpose_changed: str = "false",
    capital_other: str = "false",
    bankruptcy: str = "false",
    person_text: str = (
        "Eingetragene Personen neu oder mutierend: Beispiel, Christoph, von Schenkon, "
        "in Zuerich, Praesident des Verwaltungsrates, mit Kollektivunterschrift zu zweien. Ende."
    ),
) -> str:
    """HR02 Mutation, AG in Zuerich; Flags sind einstellbar."""
    return f"""<?xml version='1.0' encoding='UTF-8'?>
<HR02:publication xmlns:HR02="https://shab.ch/shab/HR02-export">
<meta>
  <id>{PUB_ID}</id>
  <rubric>HR</rubric>
  <subRubric>HR02</subRubric>
  <publicationNumber>HR02-1006758471</publicationNumber>
  <publicationState>PUBLISHED</publicationState>
  <publicationDate>2026-09-17</publicationDate>
  <cantons>ZH</cantons>
  <secondaryTenants><tenantCode>kabzh</tenantCode></secondaryTenants>
  <title><de>Mutation Beispiel AG, Zuerich</de></title>
</meta>
<content>
  <testImport>false</testImport>
  <journalNumber>43195</journalNumber>
  <journalDate>2026-09-14</journalDate>
  <publicationText>Beispiel AG, in Zuerich, Aktiengesellschaft. {person_text}</publicationText>
  <commonsNew>
    <company>
      <name>Beispiel AG</name>
      <translations>(Beispiel SA) (Beispiel Ltd)</translations>
      <uid>CHE-287.738.801</uid>
      <uidOrganisationId>287738801</uidOrganisationId>
      <seat>Zuerich</seat>
      <legalForm>0106</legalForm>
      <noAddress>false</noAddress>
      <address>
        <addressLine1>c/o Beispiel Treuhand AG</addressLine1>
        <street>Dreikoenigstrasse</street>
        <houseNumber>34</houseNumber>
        <swissZipCode>8002</swissZipCode>
        <town>Zuerich</town>
      </address>
    </company>
    <purpose>Die Gesellschaft bezweckt die Entwicklung digitaler Anwendungen.</purpose>
    <capital><nominal>151333.00</nominal><paid>151333.00</paid></capital>
    <revision><optingOut>false</optingOut></revision>
  </commonsNew>
  <commonsActual>
    <company>
      <name>Beispiel AG</name>
      <uid>CHE-287.738.801</uid>
      <seat>Zuerich</seat>
      <legalForm>0106</legalForm>
      <address>
        <street>Alte Strasse</street>
        <houseNumber>1</houseNumber>
        <swissZipCode>8002</swissZipCode>
        <town>Zuerich</town>
      </address>
    </company>
    <purpose>Die Gesellschaft bezweckt die Entwicklung digitaler Anwendungen.</purpose>
  </commonsActual>
  <lastFosc><lastFoscDate>2026-04-24</lastFoscDate><lastFoscNumber>78</lastFoscNumber></lastFosc>
  <transaction>
    <update>
      <changements>
        <others>{others}</others>
        <nameChanged>{name_changed}</nameChanged>
        <uidChanged>false</uidChanged>
        <legalStatusChanged>false</legalStatusChanged>
        <seatChanged>{seat_changed}</seatChanged>
        <addressChanged>{address_changed}</addressChanged>
        <purposeChanged>{purpose_changed}</purposeChanged>
        <capitalChanged>
          <nominal>false</nominal><paid>false</paid><other>{capital_other}</other>
        </capitalChanged>
        <statusChanged>
          <bankruptcy>
            <dissolution>{bankruptcy}</dissolution><dismissal>false</dismissal>
            <revocation>false</revocation><cancellation>false</cancellation><summary>false</summary>
          </bankruptcy>
          <liquidation>
            <dissolution><nonExceptional>false</nonExceptional><or731b>false</or731b>
              <hregv153b>false</hregv153b></dissolution>
            <revocation>false</revocation>
          </liquidation>
          <suspension>false</suspension><reentry>false</reentry><reapplication>false</reapplication>
        </statusChanged>
      </changements>
    </update>
  </transaction>
  <senderOffice><officeName>Handelsregisteramt des Kantons Zuerich</officeName></senderOffice>
</content>
</HR02:publication>"""


def hr03_xml() -> str:
    """HR03 Loeschung, Einzelunternehmen in Winterthur (ZH)."""
    return """<?xml version='1.0' encoding='UTF-8'?>
<HR03:publication xmlns:HR03="https://shab.ch/shab/HR03-export">
<meta>
  <id>3f6085cf-3c8a-42f1-a7ad-a42b352701da</id>
  <rubric>HR</rubric>
  <subRubric>HR03</subRubric>
  <publicationNumber>HR03-1006758532</publicationNumber>
  <publicationState>PUBLISHED</publicationState>
  <publicationDate>2026-09-17</publicationDate>
  <cantons>ZH</cantons>
  <title><de>Loeschung Malerbetrieb Beispiel, Winterthur</de></title>
</meta>
<content>
  <testImport>false</testImport>
  <journalNumber>43257</journalNumber>
  <journalDate>2026-09-14</journalDate>
  <publicationText>Malerbetrieb Beispiel, in Winterthur. Geloescht.</publicationText>
  <commonsActual>
    <company>
      <name>Malerbetrieb Beispiel</name>
      <uid>CHE-167.447.145</uid>
      <seat>Winterthur</seat>
      <legalForm>0101</legalForm>
      <address>
        <street>Buerglistrasse</street><houseNumber>57</houseNumber>
        <swissZipCode>8400</swissZipCode><town>Winterthur</town>
      </address>
    </company>
    <purpose>Malerarbeiten aller Art.</purpose>
  </commonsActual>
  <transaction><delete><deletionDate>2026-09-14</deletionDate></delete></transaction>
  <senderOffice><officeName>Handelsregisteramt des Kantons Zuerich</officeName></senderOffice>
</content>
</HR03:publication>"""


def list_json(*entries: dict[str, Any], total: int = 3) -> str:
    return json.dumps({"total": total, "content": list(entries), "pageRequest": {"page": 0}})


def entry(pub_id: str = PUB_ID, sub_rubric: str = "HR02", canton: str = "ZH") -> dict[str, Any]:
    return {
        "meta": {
            "id": pub_id,
            "subRubric": sub_rubric,
            "rubric": "HR",
            "publicationNumber": f"{sub_rubric}-1006758471",
            "publicationDate": "2026-09-17T00:00:00.000Z",
            "cantons": [canton],
            "title": {"de": "Mutation Beispiel AG, Zuerich", "en": "Change Beispiel AG"},
        },
        "links": [],
        "content": None,
    }


def ok(body: str, xml: bool = False) -> HttpResponse:
    kind = "application/xml" if xml else "application/json"
    return HttpResponse(status=200, body=body, headers={"Content-Type": kind})


def make_client(
    sender: ScriptedSender, cache: RawCache | None = None
) -> tuple[ShabClient, HttpTransport]:
    clock = FakeClock()
    transport = HttpTransport(
        source=SourceType.SHAB,
        config=config(burst=10),
        sender=sender,
        cache=cache,
        clock=clock.now,
        sleep=clock.sleep,
        now=lambda: NOW,
        rng=lambda: 0.0,
    )
    return ShabClient(transport, now=lambda: NOW), transport


def query(sender: ScriptedSender, index: int = 0) -> dict[str, list[str]]:
    _, url, _, _ = sender.calls[index]
    return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)


# -- XML lesen --------------------------------------------------------------- #


def test_hr01_wird_als_neueintragung_gelesen() -> None:
    pub = parse_publication(hr01_xml())
    assert pub.sub_rubric is SubRubric.NEUEINTRAGUNG
    assert pub.publication_date == date(2026, 9, 17)
    assert pub.journal_date == date(2026, 9, 14)
    assert pub.canton == "AG"
    assert pub.registration is True
    assert pub.kinds() == (MutationKind.NEUEINTRAGUNG,)
    assert pub.is_contact_opportunity
    assert pub.company is not None
    assert pub.company.uid == "CHE-306.462.688"
    assert pub.company.rechtsform is Rechtsform.EINZELUNTERNEHMEN
    assert pub.company.address is not None
    assert pub.company.address.one_line() == "Guetestrasse 12, 5430 Wettingen"
    assert pub.company.address.plz == "5430"
    assert pub.purpose is not None and pub.purpose.startswith("Unterrichtung")
    assert pub.sender_office == "Handelsregisteramt des Kantons Aargau"
    assert pub.company_actual is None


def test_hr02_liest_neuen_und_bisherigen_stand() -> None:
    pub = parse_publication(hr02_xml(address_changed="true"))
    assert pub.sub_rubric is SubRubric.MUTATION
    assert pub.company_new is not None and pub.company_actual is not None
    assert pub.company_new.address is not None
    assert pub.company_new.address.one_line() == (
        "c/o Beispiel Treuhand AG, Dreikoenigstrasse 34, 8002 Zuerich"
    )
    assert pub.company_actual.address is not None
    assert pub.company_actual.address.street == "Alte Strasse"
    assert pub.company is pub.company_new
    assert pub.company_new.rechtsform is Rechtsform.AG
    assert pub.company_new.translations == "(Beispiel SA) (Beispiel Ltd)"


def test_hr03_wird_als_loeschung_gelesen_und_ist_kein_anlass() -> None:
    pub = parse_publication(hr03_xml())
    assert pub.sub_rubric is SubRubric.LOESCHUNG
    assert pub.kinds() == (MutationKind.LOESCHUNG,)
    assert not pub.is_contact_opportunity
    assert pub.deletion_date == date(2026, 9, 14)
    assert pub.company is not None and pub.company.name == "Malerbetrieb Beispiel"
    assert pub.company_new is None


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ({"address_changed": "true", "others": "false"}, (MutationKind.ADRESSAENDERUNG,)),
        ({"seat_changed": "true", "others": "false"}, (MutationKind.SITZVERLEGUNG,)),
        ({"name_changed": "true", "others": "false"}, (MutationKind.FIRMENAENDERUNG,)),
        ({"purpose_changed": "true", "others": "false"}, (MutationKind.ZWECKAENDERUNG,)),
        ({"capital_other": "true", "others": "false"}, (MutationKind.KAPITALAENDERUNG,)),
        ({"bankruptcy": "true", "others": "false"}, (MutationKind.STATUSAENDERUNG,)),
        ({"others": "true"}, (MutationKind.PERSONEN_ODER_SONSTIGES,)),
    ],
)
def test_jedes_flag_ergibt_genau_eine_mutationsart(
    flags: dict[str, str], expected: tuple[MutationKind, ...]
) -> None:
    assert parse_publication(hr02_xml(**flags)).kinds() == expected


def test_mehrere_flags_ergeben_mehrere_arten_in_stabiler_reihenfolge() -> None:
    pub = parse_publication(hr02_xml(address_changed="true", purpose_changed="true"))
    assert pub.kinds() == (
        MutationKind.ADRESSAENDERUNG,
        MutationKind.ZWECKAENDERUNG,
        MutationKind.PERSONEN_ODER_SONSTIGES,
    )


def test_konkurs_ist_kein_kontaktanlass_auch_neben_adressaenderung() -> None:
    pub = parse_publication(hr02_xml(bankruptcy="true", address_changed="true"))
    assert MutationKind.STATUSAENDERUNG in pub.kinds()
    assert not pub.is_contact_opportunity


def test_reine_kapitalaenderung_ist_kein_kontaktanlass() -> None:
    pub = parse_publication(hr02_xml(capital_other="true", others="false"))
    assert pub.kinds() == (MutationKind.KAPITALAENDERUNG,)
    assert not pub.is_contact_opportunity
    assert MutationKind.KAPITALAENDERUNG not in CONTACT_KINDS


def test_personenzitat_kommt_aus_dem_amtlichen_text() -> None:
    quote = parse_publication(hr02_xml()).person_change_quote
    assert quote is not None
    assert quote.startswith("Eingetragene Personen neu oder mutierend:")
    assert quote.endswith(".")
    assert "Ende." not in quote


def test_personenzitat_fehlt_ohne_marker() -> None:
    pub = parse_publication(hr02_xml(person_text="Neue Adresse: Dreikoenigstrasse 34."))
    assert pub.person_change_quote is None
    assert pub.kinds() == (MutationKind.PERSONEN_ODER_SONSTIGES,)


def test_belegurl_zeigt_auf_die_portal_detailseite() -> None:
    pub = parse_publication(hr02_xml())
    assert pub.source_url.endswith(PUB_ID)
    assert pub.source_url.startswith("https://amtsblattportal.ch/")


@pytest.mark.parametrize(
    "xml",
    [
        "<nicht>wohlgeformt",
        "<p:publication xmlns:p='x'><meta><subRubric>HR02</subRubric></meta></p:publication>",
        "<p:publication xmlns:p='x'><meta><id>a</id>"
        "<subRubric>KK01</subRubric></meta></p:publication>",
    ],
)
def test_unbrauchbares_xml_wird_abgelehnt(xml: str) -> None:
    with pytest.raises(ValueError):
        parse_publication(xml)


# -- Liste lesen ------------------------------------------------------------- #


def test_parse_list_liest_metadaten_und_ueberspringt_fremdes() -> None:
    payload = json.loads(
        list_json(
            entry(),
            {"meta": {"id": "x", "subRubric": "KK01"}},
            {"kein": "meta"},
            {"meta": {"subRubric": "HR01"}},
            entry("id-2", "HR01", "ZG"),
        )
    )
    entries = parse_list(payload)
    assert [e.id for e in entries] == [PUB_ID, "id-2"]
    assert entries[0].sub_rubric is SubRubric.MUTATION
    assert entries[0].publication_date == date(2026, 9, 17)
    assert entries[0].cantons == ("ZH",)
    assert entries[0].title == "Mutation Beispiel AG, Zuerich"
    assert entries[1].sub_rubric is SubRubric.NEUEINTRAGUNG


@pytest.mark.parametrize("payload", [[], {"content": "x"}, {"total": 1}])
def test_parse_list_lehnt_fremde_antworten_ab(payload: object) -> None:
    with pytest.raises(ValueError):
        parse_list(payload)


def test_total_hits_liest_die_gesamtzahl() -> None:
    assert total_hits({"total": 18101, "content": []}) == 18101
    assert total_hits({"content": []}) == 0
    assert total_hits([]) == 0


# -- Uebergang ins Profil ---------------------------------------------------- #


def test_profil_aus_neueintragung_traegt_je_feld_einen_beleg() -> None:
    profile = profile_from_publication(parse_publication(hr01_xml()), NOW)
    assert profile.uid == "CHE-306.462.688"
    assert profile.name == "Nothelfer Star Beispiel"
    assert profile.rechtsform is Rechtsform.EINZELUNTERNEHMEN
    assert profile.standort is not None
    assert (profile.standort.kanton, profile.standort.plz, profile.standort.ort) == (
        "AG",
        "5430",
        "Wettingen",
    )
    assert profile.noga_codes == [] and profile.website is None and profile.emails == []
    assert [e.locator for e in profile.evidence] == [
        "commons/company/name",
        "commons/company/legalForm",
        "meta/subRubric=HR01",
        "commons/company/address",
        "commons/purpose",
    ]
    assert all(e.source_type is SourceType.SHAB for e in profile.evidence)
    assert all(
        e.source_url.endswith("0c65a54b-7361-491f-890d-82ab8272d5a5") for e in profile.evidence
    )
    (document,) = profile.documents
    assert document.kind is DocumentKind.REGISTER_PURPOSE


def test_profil_haengt_den_publikationstext_nicht_als_dokument_an() -> None:
    profile = profile_from_publication(parse_publication(hr02_xml()), NOW)
    assert all(d.kind is DocumentKind.REGISTER_PURPOSE for d in profile.documents)
    assert all("Eingetragene Personen" not in d.text for d in profile.documents)


def test_profil_nimmt_den_neuen_stand_bei_einer_mutation() -> None:
    profile = profile_from_publication(parse_publication(hr02_xml(address_changed="true")), NOW)
    assert profile.standort is not None and profile.standort.plz == "8002"
    address_quote = next(e.quote for e in profile.evidence if (e.locator or "").endswith("address"))
    assert address_quote.startswith("c/o Beispiel Treuhand AG")
    assert address_quote.endswith("(ZH)")


def test_profil_ohne_uid_oder_firma_ist_kein_profil() -> None:
    with pytest.raises(ValueError):
        profile_from_publication(parse_publication(hr01_xml(uid="")), NOW)
    ohne_firma = parse_publication(hr01_xml(name=""))
    with pytest.raises(ValueError):
        profile_from_publication(ohne_firma, NOW)


def test_profil_ohne_bekannten_kanton_hat_keinen_standort() -> None:
    profile = profile_from_publication(parse_publication(hr01_xml(canton="XX")), NOW)
    assert profile.standort is None


def test_profil_ohne_rechtsformcode_bleibt_unbekannt() -> None:
    profile = profile_from_publication(parse_publication(hr01_xml(legalForm="")), NOW)
    assert profile.rechtsform is Rechtsform.UNBEKANNT


# -- Client ------------------------------------------------------------------ #


def test_client_verlangt_transport_der_quelle_shab() -> None:
    clock = FakeClock()
    transport = HttpTransport(
        source=SourceType.ZEFIX,
        config=config(),
        sender=ScriptedSender([]),
        clock=clock.now,
        sleep=clock.sleep,
    )
    with pytest.raises(ValueError):
        ShabClient(transport)


def test_build_nimmt_limits_der_quelle_shab() -> None:
    client = ShabClient.build()
    assert client.transport.source is SourceType.SHAB
    assert client.transport.calls_sent == 0


def test_list_publications_baut_die_abfrage_laut_api_doku() -> None:
    sender = ScriptedSender([ok(list_json(entry()))])
    client, _ = make_client(sender)

    entries = client.list_publications(SubRubric.MUTATION, days=90, page_size=5, today=TODAY)

    assert [e.id for e in entries] == [PUB_ID]
    method, url, body, headers = sender.calls[0]
    assert (method, body) == ("GET", None)
    assert url.startswith(f"{SHAB_BASE_URL}/publications?")
    assert headers["Accept"] == "application/json"
    assert headers["User-Agent"].startswith("kmu-discovery/")
    params = query(sender)
    assert params["publicationStates"] == ["PUBLISHED"]
    assert params["subRubrics"] == ["HR02"]
    assert params["cantons"] == ["ZH", "AG", "ZG"]
    assert params["publicationDate.start"] == ["2026-06-19"]
    assert params["publicationDate.end"] == ["2026-09-17"]
    assert params["pageRequest.size"] == ["5"]
    assert params["pageRequest.sortOrders"] == ["column:PUBLICATION_DATE|direction:DESC"]


def test_list_publications_normalisiert_kantone() -> None:
    sender = ScriptedSender([ok(list_json())])
    client, _ = make_client(sender)
    client.list_publications(SubRubric.MUTATION, cantons=["zh", " ag "], today=TODAY)
    assert query(sender)["cantons"] == ["ZH", "AG"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"cantons": ["XX"]},
        {"cantons": []},
        {"page_size": 0},
        {"page_size": MAX_PAGE_SIZE + 1},
        {"page": -1},
        {"days": 0},
    ],
)
def test_list_publications_lehnt_ungueltige_parameter_ohne_netzaufruf_ab(
    kwargs: dict[str, Any],
) -> None:
    client, transport = make_client(ScriptedSender([]))
    with pytest.raises(ValueError):
        client.list_publications(SubRubric.MUTATION, today=TODAY, **kwargs)
    assert transport.calls_sent == 0


def test_count_publications_liest_nur_die_gesamtzahl() -> None:
    sender = ScriptedSender([ok(list_json(total=18101))])
    client, _ = make_client(sender)
    assert client.count_publications(SubRubric.MUTATION, today=TODAY) == 18101
    assert query(sender)["pageRequest.size"] == ["1"]


def test_fetch_publication_holt_das_xml() -> None:
    sender = ScriptedSender([ok(hr02_xml(), xml=True)])
    client, _ = make_client(sender)

    pub = client.fetch_publication(PUB_ID)

    assert pub.id == PUB_ID
    _, url, _, headers = sender.calls[0]
    assert url == f"{SHAB_BASE_URL}/publications/{PUB_ID}/xml"
    assert headers["Accept"] == "application/xml"


@pytest.mark.parametrize("bad", ["", "../../etc/passwd", "kurz", "id mit leerzeichen"])
def test_fetch_publication_lehnt_unplausible_ids_ohne_netzaufruf_ab(bad: str) -> None:
    client, transport = make_client(ScriptedSender([]))
    with pytest.raises(ValueError):
        client.fetch_publication(bad)
    assert transport.calls_sent == 0


@pytest.mark.parametrize(
    ("body", "xml"), [("<html>Wartung</html>", True), ('{"kein": "content"}', False)]
)
def test_unlesbare_antworten_werden_zu_bad_response(body: str, xml: bool) -> None:
    sender = ScriptedSender([HttpResponse(status=200, body=body, headers={})])
    client, _ = make_client(sender)
    with pytest.raises(SourceBadResponseError) as info:
        if xml:
            client.fetch_publication(PUB_ID)
        else:
            client.list_publications(SubRubric.MUTATION, today=TODAY)
    assert info.value.source is SourceType.SHAB


def test_wiederholter_abruf_kommt_aus_dem_cache(tmp_path: Path) -> None:
    sender = ScriptedSender([ok(hr02_xml(), xml=True)])
    client, transport = make_client(sender, cache=RawCache(tmp_path))
    first = client.fetch_publication(PUB_ID)
    second = client.fetch_publication(PUB_ID, max_age=timedelta(days=1))
    assert first == second
    assert (transport.calls_sent, transport.cache_hits) == (1, 1)


# -- Iteration --------------------------------------------------------------- #


def test_iter_publications_laedt_seiten_und_filtert_auf_kontaktanlaesse() -> None:
    sender = ScriptedSender(
        [
            ok(list_json(entry("aaaaaaaa-1111-2222-3333-444444444444"), entry(PUB_ID))),
            ok(hr02_xml(bankruptcy="true", others="false"), xml=True),
            ok(hr02_xml(address_changed="true", others="false"), xml=True),
        ]
    )
    client, transport = make_client(sender)

    found = list(
        client.iter_publications(
            sub_rubrics=[SubRubric.MUTATION], page_size=2, max_pages=1, today=TODAY
        )
    )

    assert [p.kinds() for p in found] == [(MutationKind.ADRESSAENDERUNG,)]
    assert transport.calls_sent == 3


def test_iter_publications_ohne_filter_liefert_auch_loeschungen() -> None:
    sender = ScriptedSender(
        [
            ok(list_json(entry("cccccccc-1111-2222-3333-444444444444", "HR03"))),
            ok(hr03_xml(), xml=True),
        ]
    )
    client, _ = make_client(sender)
    found = list(
        client.iter_publications(
            sub_rubrics=[SubRubric.LOESCHUNG],
            page_size=5,
            max_pages=1,
            contact_only=False,
            today=TODAY,
        )
    )
    assert [p.sub_rubric for p in found] == [SubRubric.LOESCHUNG]


def test_iter_publications_blaettert_bis_zur_kurzen_seite() -> None:
    full = ok(list_json(entry("dddddddd-1111-2222-3333-444444444444"), entry(PUB_ID)))
    sender = ScriptedSender(
        [
            full,
            ok(hr01_xml(), xml=True),
            ok(hr01_xml(), xml=True),
            ok(list_json(entry("eeeeeeee-1111-2222-3333-444444444444"))),
            ok(hr01_xml(), xml=True),
        ]
    )
    client, _ = make_client(sender)
    found = list(
        client.iter_publications(
            sub_rubrics=[SubRubric.NEUEINTRAGUNG], page_size=2, max_pages=5, today=TODAY
        )
    )
    assert len(found) == 3
    assert query(sender, 0)["pageRequest.page"] == ["0"]
    assert query(sender, 3)["pageRequest.page"] == ["1"]


def test_iter_profiles_ueberspringt_meldungen_ohne_uid() -> None:
    sender = ScriptedSender(
        [
            ok(
                list_json(
                    entry("ffffffff-1111-2222-3333-444444444444", "HR01"), entry(PUB_ID, "HR01")
                )
            ),
            ok(hr01_xml(uid=""), xml=True),
            ok(hr01_xml(), xml=True),
        ]
    )
    client, _ = make_client(sender)
    profiles = list(
        client.iter_profiles(
            sub_rubrics=[SubRubric.NEUEINTRAGUNG], page_size=2, max_pages=1, today=TODAY
        )
    )
    assert [p.uid for p, _ in profiles] == ["CHE-306.462.688"]
    assert all(pub.sub_rubric is SubRubric.NEUEINTRAGUNG for _, pub in profiles)
    assert profiles[0][0].evidence[0].retrieved_at == NOW
