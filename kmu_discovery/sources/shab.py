"""SHAB-Client: Handelsregister-Mutationen ueber die Amtsblattportal-API.

Das Schweizerische Handelsamtsblatt (SHAB) publiziert jede Handelsregister-
Mutation. Die Rubrik HR kennt drei Unterrubriken:

* **HR01 Neueintragung** - ein Betrieb ist neu im Register.
* **HR02 Mutation** - Adresse, Sitz, Firma, Zweck, Kapital, Status oder
  Personen haben geaendert.
* **HR03 Loeschung** - der Eintrag ist geloescht.

Fuer die Engine sind diese Meldungen **Kontaktanlaesse**: ein Betrieb, der
gerade umgezogen ist oder die Geschaeftsfuehrung gewechselt hat, ordnet seine
Ablaeufe ohnehin neu.

Jeder Feldname hier stammt aus der am 2026-09-17 live erhobenen Feldtabelle
(``docs/shab_feldtabelle.md``, erhoben mit ``scripts/probe_shab.py``), nicht
aus dem Gedaechtnis. Zwei Befunde der Erhebung praegen dieses Modul:

1. **Kein Personenelement.** Zeichnungsberechtigte stehen ausschliesslich im
   Freitext ``content/publicationText``. Strukturiert liefert die API sie
   nicht. :attr:`ShabPublication.person_change_quote` erkennt sie deshalb
   ueber feste Marker im amtlichen Text - deterministisch und zitierbar,
   aber eine Textheuristik, keine Strukturinformation.
2. **``others`` ist ein Sammeltopf.** Laut Schema deckt das Flag "any
   changement other than the subsequent listed changement options ... might
   include typos, changements in prenames or changements in signature
   authorisations". Ein gesetztes ``others`` heisst also *vielleicht*
   Personenwechsel; darum heisst die Mutationsart
   :attr:`MutationKind.PERSONEN_ODER_SONSTIGES` und nicht "Personen".

Rechtlicher Rahmen: Die API ist der von der Betreiberin vorgesehene
Maschinenzugang und laut Doku frei nutzbar ("The API is freely accessible for
anyone to use"), ohne Zugangsdaten fuer publizierte Meldungen. Die robots.txt
sperrt Crawler von der HTML-Oberflaeche; diese wird nicht benutzt. Der Client
stellt nur lesende Anfragen ueber den rate-limitierten
:class:`~kmu_discovery.sources.base.HttpTransport`. Publikationstexte
enthalten Personendaten aus einem oeffentlichen Register (revDSG): sie werden
nur zweckgebunden fuer die Kontaktaufnahme verwendet, nicht in Bulk
ausgewertet und nicht zu Personenprofilen verdichtet.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field

from kmu_discovery.config.limits import RateLimits, load_rate_limits
from kmu_discovery.models import (
    CANTONS,
    AwareDatetime,
    CompanyProfile,
    DocumentKind,
    Evidence,
    Rechtsform,
    SourceType,
    Standort,
    TextDocument,
)
from kmu_discovery.sources.base import (
    HttpTransport,
    RawCache,
    RawRecord,
    SourceBadResponseError,
    urllib_sender,
)
from kmu_discovery.sources.legal_forms import rechtsform_from_code

__all__ = [
    "DEFAULT_CANTONS",
    "MAX_PAGE_SIZE",
    "SHAB_BASE_URL",
    "MutationKind",
    "ShabAddress",
    "ShabChangements",
    "ShabClient",
    "ShabCompany",
    "ShabListEntry",
    "ShabPublication",
    "SubRubric",
    "profile_from_publication",
]

SHAB_BASE_URL = "https://amtsblattportal.ch/api/v1"
PATH_PUBLICATIONS = "/publications"
#: Groesste Seite laut API-Doku ("More than 2000 hits are not allowed");
#: der Client bleibt darunter, damit eine Antwort handlich bleibt.
MAX_PAGE_SIZE = 500
#: Zielkantone der ersten Ausbaustufe.
DEFAULT_CANTONS = ("ZH", "AG", "ZG")
#: Publikationszustand; laut Doku ein Pflichtparameter jeder Listenanfrage.
PUBLICATION_STATE_PUBLISHED = "PUBLISHED"

T = TypeVar("T")
_PLZ_RE = re.compile(r"^\d{4}$")
#: Marker der amtlichen Publikationstexte fuer Personenwechsel. Die Schreib-
#: weise ist zwischen den kantonalen Aemtern einheitlich (live gegengelesen an
#: Meldungen der Aemter ZH, AG und ZG).
_PERSON_MARKERS: tuple[str, ...] = (
    "Eingetragene Personen neu oder mutierend",
    "Ausgeschiedene Personen und erloschene Unterschriften",
    "Eingetragene Personen",
    "Personen neu oder mutierend",
)


class SubRubric(StrEnum):
    """Unterrubriken der Rubrik HR (Handelsregister)."""

    NEUEINTRAGUNG = "HR01"
    MUTATION = "HR02"
    LOESCHUNG = "HR03"


class MutationKind(StrEnum):
    """Art einer Handelsregister-Meldung, abgeleitet aus der Struktur.

    Die Werte ausser :attr:`PERSONEN_ODER_SONSTIGES` stammen aus genau einem
    Flag des Schemas. Eine Meldung kann mehrere Arten tragen.
    """

    NEUEINTRAGUNG = "neueintragung"
    LOESCHUNG = "loeschung"
    ADRESSAENDERUNG = "adressaenderung"
    SITZVERLEGUNG = "sitzverlegung"
    FIRMENAENDERUNG = "firmenaenderung"
    ZWECKAENDERUNG = "zweckaenderung"
    KAPITALAENDERUNG = "kapitalaenderung"
    RECHTSFORMAENDERUNG = "rechtsformaenderung"
    STATUSAENDERUNG = "statusaenderung"
    PERSONEN_ODER_SONSTIGES = "personen_oder_sonstiges"


#: Arten, die ein Kontaktanlass sind: der Betrieb besteht weiter und hat
#: gerade etwas umgestellt. Loeschung und Statusaenderung (Konkurs,
#: Liquidation) sind bewusst nicht dabei.
CONTACT_KINDS: frozenset[MutationKind] = frozenset(
    {
        MutationKind.NEUEINTRAGUNG,
        MutationKind.ADRESSAENDERUNG,
        MutationKind.SITZVERLEGUNG,
        MutationKind.ZWECKAENDERUNG,
        MutationKind.PERSONEN_ODER_SONSTIGES,
    }
)


# -- Modelle laut Feldtabelle --------------------------------------------- #


class _XmlModel(BaseModel):
    """Basis: eingefroren, unbekannte Felder werden ignoriert.

    ``extra="ignore"``: das Schema darf wachsen, ohne den Client zu brechen.
    Fast alle Elemente sind laut XSD ``minOccurs="0"``, deshalb ist fast
    jedes Feld optional.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")


class ShabAddress(_XmlModel):
    """``company/address``: Sitzadresse in Einzelteilen."""

    address_line1: str | None = None
    address_line2: str | None = None
    street: str | None = None
    house_number: str | None = None
    post_office_box_number: str | None = None
    post_office_box_text: str | None = None
    swiss_zip_code: str | None = None
    town: str | None = None

    def one_line(self) -> str:
        """Adresse als eine Zeile, leere Teile weggelassen."""
        street = " ".join(p for p in (self.street, self.house_number) if p)
        place = " ".join(p for p in (self.swiss_zip_code, self.town) if p)
        parts = (self.address_line1, self.address_line2, street, self.post_office_box_text, place)
        return ", ".join(p for p in parts if p)

    @property
    def plz(self) -> str | None:
        """Vierstellige PLZ, sonst None."""
        code = self.swiss_zip_code
        return code if code and _PLZ_RE.match(code) else None


class ShabCompany(_XmlModel):
    """``commonsNew/company`` bzw. ``commonsActual/company``."""

    name: str | None = None
    translations: str | None = None
    uid: str | None = None
    uid_organisation_id: str | None = None
    code13: str | None = None
    seat: str | None = None
    legal_form: str | None = Field(default=None, description="eCH-0097-Code, z. B. 0107.")
    no_address: bool | None = None
    address: ShabAddress | None = None

    @property
    def rechtsform(self) -> Rechtsform:
        """Rechtsform im Modellvokabular; UNBEKANNT ohne Code."""
        return rechtsform_from_code(self.legal_form)


class ShabChangements(_XmlModel):
    """``transaction/update/changements``: was genau geaendert hat.

    Jedes Flag ist im Schema dokumentiert. ``others`` ist ein Sammeltopf und
    deshalb kein Beleg fuer einen Personenwechsel - siehe Modul-Docstring.
    """

    others: bool = False
    name_changed: bool = False
    uid_changed: bool = False
    legal_status_changed: bool = False
    seat_changed: bool = False
    address_changed: bool = False
    purpose_changed: bool = False
    capital_changed: bool = False
    status_changed: bool = False

    def kinds(self) -> tuple[MutationKind, ...]:
        """Mutationsarten dieser Meldung, in stabiler Reihenfolge."""
        mapping = (
            (self.address_changed, MutationKind.ADRESSAENDERUNG),
            (self.seat_changed, MutationKind.SITZVERLEGUNG),
            (self.name_changed, MutationKind.FIRMENAENDERUNG),
            (self.purpose_changed, MutationKind.ZWECKAENDERUNG),
            (self.capital_changed, MutationKind.KAPITALAENDERUNG),
            (self.legal_status_changed, MutationKind.RECHTSFORMAENDERUNG),
            (self.status_changed, MutationKind.STATUSAENDERUNG),
            (self.others, MutationKind.PERSONEN_ODER_SONSTIGES),
        )
        return tuple(kind for flag, kind in mapping if flag)


class ShabListEntry(_XmlModel):
    """Ein Treffer der Listenabfrage (JSON) - nur Metadaten, kein Inhalt.

    Die API liefert in der Liste bewusst keinen Inhalt; dafuer ist ein
    Einzelabruf noetig (API-Doku, Kapitel 2.2).
    """

    id: str
    sub_rubric: SubRubric
    publication_number: str | None = None
    publication_date: date | None = None
    cantons: tuple[str, ...] = ()
    title: str | None = None


class ShabPublication(_XmlModel):
    """Eine vollstaendige HR-Publikation (XML).

    ``company_new`` ist der neue Stand (``commonsNew``), ``company_actual``
    der bisherige (``commonsActual``). HR01 fuehrt nur ``commonsNew``,
    HR03 nur ``commonsActual``, HR02 in der Regel beide.
    """

    id: str
    sub_rubric: SubRubric
    publication_number: str | None = None
    publication_date: date | None = None
    journal_date: date | None = None
    cantons: tuple[str, ...] = ()
    title: str | None = None
    publication_text: str | None = None
    company_new: ShabCompany | None = None
    company_actual: ShabCompany | None = None
    purpose: str | None = None
    changements: ShabChangements | None = None
    registration: bool = False
    deletion_date: date | None = None
    sender_office: str | None = None

    @property
    def company(self) -> ShabCompany | None:
        """Der aussagekraeftigste Betriebsstand: neu, sonst bisher."""
        return self.company_new or self.company_actual

    @property
    def canton(self) -> str | None:
        """Erstes Kantonskuerzel, sofern es ein bekannter Kanton ist."""
        for code in self.cantons:
            if code.strip().upper() in CANTONS:
                return code.strip().upper()
        return None

    @property
    def person_change_quote(self) -> str | None:
        """Belegsatz zum Personenwechsel aus dem amtlichen Text, sonst None.

        **Textheuristik, keine Strukturinformation** (siehe Modul-Docstring):
        die API kennt kein Personenelement. Geliefert wird der Satz, in dem
        der Marker steht - damit ein Treffer zitierbar bleibt.

        >>> pub = ShabPublication(id="x", sub_rubric=SubRubric.MUTATION,
        ...     publication_text="Muster AG, in Zug. Eingetragene Personen neu "
        ...     "oder mutierend: Muster, Anna, Geschaeftsfuehrerin. Weiteres.")
        >>> pub.person_change_quote
        'Eingetragene Personen neu oder mutierend: Muster, Anna, Geschaeftsfuehrerin.'
        """
        text = self.publication_text
        if not text:
            return None
        for marker in _PERSON_MARKERS:
            start = text.find(marker)
            if start < 0:
                continue
            end = text.find(".", start + len(marker))
            return text[start : end + 1] if end > 0 else text[start:]
        return None

    def kinds(self) -> tuple[MutationKind, ...]:
        """Alle Mutationsarten dieser Meldung."""
        if self.sub_rubric is SubRubric.NEUEINTRAGUNG:
            return (MutationKind.NEUEINTRAGUNG,)
        if self.sub_rubric is SubRubric.LOESCHUNG:
            return (MutationKind.LOESCHUNG,)
        return self.changements.kinds() if self.changements else ()

    @property
    def is_contact_opportunity(self) -> bool:
        """True, wenn die Meldung ein Kontaktanlass ist (siehe ``CONTACT_KINDS``)."""
        kinds = set(self.kinds())
        if MutationKind.LOESCHUNG in kinds or MutationKind.STATUSAENDERUNG in kinds:
            return False
        return bool(kinds & CONTACT_KINDS)

    @property
    def source_url(self) -> str:
        """Dereferenzierbare Beleg-URL der Publikation auf dem Portal."""
        return f"https://amtsblattportal.ch/#!/search/publications/detail/{self.id}"


# -- XML lesen ------------------------------------------------------------ #


def _strip(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(node: ET.Element | None, path: str) -> str | None:
    """Text eines Kindelements ueber einen Pfad; None, wenn leer oder fehlend."""
    found = _find(node, path)
    if found is None:
        return None
    value = (found.text or "").strip()
    return value or None


def _find(node: ET.Element | None, path: str) -> ET.Element | None:
    """Findet ein Kindelement ueber einen Pfad, ohne Namensraeume."""
    current = node
    for part in path.split("/"):
        if current is None:
            return None
        current = next((c for c in current if _strip(c.tag) == part), None)
    return current


def _flag(node: ET.Element | None, path: str) -> bool:
    return (_text(node, path) or "").lower() == "true"


def _date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _address(node: ET.Element | None) -> ShabAddress | None:
    if node is None:
        return None
    address = ShabAddress(
        address_line1=_text(node, "addressLine1"),
        address_line2=_text(node, "addressLine2"),
        street=_text(node, "street"),
        house_number=_text(node, "houseNumber"),
        post_office_box_number=_text(node, "postOfficeBoxNumber"),
        post_office_box_text=_text(node, "postOfficeBoxText"),
        swiss_zip_code=_text(node, "swissZipCode"),
        town=_text(node, "town"),
    )
    return address if address.one_line() else None


def _company(commons: ET.Element | None) -> ShabCompany | None:
    node = _find(commons, "company")
    if node is None:
        return None
    return ShabCompany(
        name=_text(node, "name"),
        translations=_text(node, "translations"),
        uid=_text(node, "uid"),
        uid_organisation_id=_text(node, "uidOrganisationId"),
        code13=_text(node, "code13"),
        seat=_text(node, "seat"),
        legal_form=_text(node, "legalForm"),
        no_address=_flag(node, "noAddress") if _text(node, "noAddress") else None,
        address=_address(_find(node, "address")),
    )


def _changements(content: ET.Element | None) -> ShabChangements | None:
    node = _find(content, "transaction/update/changements")
    if node is None:
        return None
    capital = _find(node, "capitalChanged")
    status = _find(node, "statusChanged")
    return ShabChangements(
        others=_flag(node, "others"),
        name_changed=_flag(node, "nameChanged"),
        uid_changed=_flag(node, "uidChanged"),
        legal_status_changed=_flag(node, "legalStatusChanged"),
        seat_changed=_flag(node, "seatChanged"),
        address_changed=_flag(node, "addressChanged"),
        purpose_changed=_flag(node, "purposeChanged"),
        capital_changed=any(_flag(capital, part) for part in ("nominal", "paid", "other")),
        status_changed=_any_true(status),
    )


def _any_true(node: ET.Element | None) -> bool:
    """True, wenn irgendein Blattelement unter ``node`` ``true`` ist."""
    if node is None:
        return False
    return any((child.text or "").strip().lower() == "true" for child in node.iter())


def parse_publication(xml_text: str) -> ShabPublication:
    """Liest eine Einzelpublikation (XML) in das Modell.

    :raises ValueError: wenn Pflichtangaben (ID, Unterrubrik) fehlen oder die
        Unterrubrik keine HR-Rubrik ist.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as error:
        raise ValueError(f"kein gueltiges XML: {error}") from error
    meta, content = _find(root, "meta"), _find(root, "content")
    pub_id = _text(meta, "id")
    sub_rubric = _text(meta, "subRubric")
    if not pub_id or not sub_rubric:
        raise ValueError("Publikation ohne id oder subRubric")
    if sub_rubric not in {member.value for member in SubRubric}:
        raise ValueError(f"keine HR-Publikation: subRubric={sub_rubric}")
    commons_new = _find(content, "commonsNew")
    commons_actual = _find(content, "commonsActual")
    cantons = tuple(
        (node.text or "").strip()
        for node in (meta.iter() if meta is not None else ())
        if _strip(node.tag) == "cantons" and (node.text or "").strip()
    )
    return ShabPublication(
        id=pub_id,
        sub_rubric=SubRubric(sub_rubric),
        publication_number=_text(meta, "publicationNumber"),
        publication_date=_date(_text(meta, "publicationDate")),
        journal_date=_date(_text(content, "journalDate")),
        cantons=cantons,
        title=_text(meta, "title/de"),
        publication_text=_text(content, "publicationText"),
        company_new=_company(commons_new),
        company_actual=_company(commons_actual),
        purpose=_text(commons_new, "purpose") or _text(commons_actual, "purpose"),
        changements=_changements(content),
        registration=_flag(content, "transaction/registration"),
        deletion_date=_date(_text(content, "transaction/delete/deletionDate")),
        sender_office=_text(content, "senderOffice/officeName"),
    )


def parse_list(payload: object) -> list[ShabListEntry]:
    """Liest die Trefferliste (JSON) in Metadaten-Eintraege.

    Eintraege ohne ``meta.id`` oder mit fremder Unterrubrik werden
    uebersprungen; die Liste kann laut Doku Publikationen mehrerer Typen
    enthalten.
    """
    if not isinstance(payload, dict):
        raise ValueError("Trefferliste ist kein JSON-Objekt")
    content = payload.get("content")
    if not isinstance(content, list):
        raise ValueError("Trefferliste ohne 'content'")
    entries: list[ShabListEntry] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        meta = item.get("meta")
        if not isinstance(meta, dict):
            continue
        pub_id, sub_rubric = meta.get("id"), meta.get("subRubric")
        if not isinstance(pub_id, str) or sub_rubric not in {m.value for m in SubRubric}:
            continue
        raw_cantons = meta.get("cantons")
        title = meta.get("title")
        entries.append(
            ShabListEntry(
                id=pub_id,
                sub_rubric=SubRubric(sub_rubric),
                publication_number=meta.get("publicationNumber"),
                publication_date=_date(str(meta.get("publicationDate", ""))[:10] or None),
                cantons=tuple(c for c in raw_cantons if isinstance(c, str))
                if isinstance(raw_cantons, list)
                else (),
                title=title.get("de") if isinstance(title, dict) else None,
            )
        )
    return entries


def total_hits(payload: object) -> int:
    """Gesamtzahl der Treffer laut Antwort (``total``); 0, wenn unbekannt."""
    if isinstance(payload, dict) and isinstance(payload.get("total"), int):
        count: int = payload["total"]
        return count
    return 0


# -- Uebergang ins Betriebsprofil ---------------------------------------- #


def _evidence(pub: ShabPublication, quote: str, locator: str, at: AwareDatetime) -> Evidence:
    return Evidence(
        source_type=SourceType.SHAB,
        source_url=pub.source_url,
        quote=quote[:2000],
        retrieved_at=at,
        locator=locator,
    )


def profile_from_publication(
    publication: ShabPublication, retrieved_at: datetime
) -> CompanyProfile:
    """Baut das Betriebsprofil aus einer Meldung; jedes Feld traegt einen Beleg.

    Leer bleibt, was die Meldung nicht kennt: ``noga_codes``,
    ``groessenklasse``, ``website``, ``emails``. Der Zweck wird zusaetzlich
    als :class:`TextDocument` der Art ``REGISTER_PURPOSE`` angehaengt.

    Der Publikationstext wird **nicht** als Dokument angehaengt: er enthaelt
    Personendaten und dient nur als Beleg (gekuerztes Zitat).

    :raises ValueError: wenn UID oder Firma fehlen - ohne sie gibt es kein Profil.
    """
    company = publication.company
    if company is None or not company.uid or not company.name:
        raise ValueError("SHAB-Meldung ohne UID oder Firma")

    evidence = [_evidence(publication, company.name, "commons/company/name", retrieved_at)]
    if company.legal_form:
        evidence.append(
            _evidence(publication, company.legal_form, "commons/company/legalForm", retrieved_at)
        )
    kinds = publication.kinds()
    if kinds:
        evidence.append(
            _evidence(
                publication,
                publication.title or ", ".join(k.value for k in kinds),
                f"meta/subRubric={publication.sub_rubric.value}",
                retrieved_at,
            )
        )

    standort: Standort | None = None
    canton = publication.canton
    if canton:
        address = company.address
        standort = Standort(
            kanton=canton,
            plz=address.plz if address else None,
            ort=(address.town if address and address.town else company.seat),
        )
        quote = address.one_line() if address else (company.seat or "")
        evidence.append(
            _evidence(
                publication,
                f"{quote} ({canton})".strip(),
                "commons/company/address",
                retrieved_at,
            )
        )

    documents: list[TextDocument] = []
    if publication.purpose:
        evidence.append(
            _evidence(publication, publication.purpose, "commons/purpose", retrieved_at)
        )
        documents.append(
            TextDocument(
                kind=DocumentKind.REGISTER_PURPOSE,
                url=publication.source_url,
                text=publication.purpose,
                retrieved_at=retrieved_at,
            )
        )

    return CompanyProfile(
        uid=company.uid,
        name=company.name,
        rechtsform=company.rechtsform,
        standort=standort,
        zweck=publication.purpose,
        documents=documents,
        evidence=evidence,
    )


# -- Client --------------------------------------------------------------- #


class ShabClient:
    """Liest HR-Meldungen der Amtsblattportal-API.

    Der Client kennt nur SHAB. Rate-Limit, Retry, Cache und typisierte Fehler
    kommen vom :class:`HttpTransport`; ``now`` ist injizierbar.
    """

    def __init__(
        self,
        transport: HttpTransport,
        base_url: str = SHAB_BASE_URL,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Nimmt einen Transport fuer die Quelle SHAB entgegen."""
        if transport.source is not SourceType.SHAB:
            raise ValueError(f"Transport fuer {transport.source} statt shab")
        self._transport = transport
        self._base_url = base_url.rstrip("/")
        self._now = now

    @classmethod
    def build(
        cls,
        cache_dir: Path | None = None,
        ca_bundle: Path | None = None,
        limits: RateLimits | None = None,
        base_url: str = SHAB_BASE_URL,
    ) -> ShabClient:
        """Produktiver Client; Limits aus ``rate_limits.yaml``."""
        config = (limits or load_rate_limits()).for_source(SourceType.SHAB)
        transport = HttpTransport(
            source=SourceType.SHAB,
            config=config,
            sender=urllib_sender(ca_bundle),
            cache=RawCache(cache_dir) if cache_dir else None,
        )
        return cls(transport, base_url=base_url)

    @property
    def transport(self) -> HttpTransport:
        """Der Transport - fuer Zaehler (``calls_sent``, ``cache_hits``)."""
        return self._transport

    # -- Abfragen ---------------------------------------------------------- #

    def list_publications(
        self,
        sub_rubric: SubRubric,
        cantons: Sequence[str] = DEFAULT_CANTONS,
        days: int = 90,
        page: int = 0,
        page_size: int = 100,
        today: date | None = None,
        max_age: timedelta | None = None,
    ) -> list[ShabListEntry]:
        """Eine Seite Meldungen einer Unterrubrik, neueste zuerst.

        :raises ValueError: bei unbekanntem Kanton oder unzulaessiger Seite.
        """
        params = self._list_params(sub_rubric, cantons, days, page, page_size, today)
        record = self._get(PATH_PUBLICATIONS, params, max_age)
        return self._parse(record, parse_list)

    def count_publications(
        self,
        sub_rubric: SubRubric,
        cantons: Sequence[str] = DEFAULT_CANTONS,
        days: int = 90,
        today: date | None = None,
        max_age: timedelta | None = None,
    ) -> int:
        """Gesamtzahl der Meldungen im Zeitraum, ohne die Treffer zu lesen."""
        params = self._list_params(sub_rubric, cantons, days, 0, 1, today)
        record = self._get(PATH_PUBLICATIONS, params, max_age)
        return self._parse(record, total_hits)

    def fetch_publication(
        self, publication_id: str, max_age: timedelta | None = None
    ) -> ShabPublication:
        """Eine vollstaendige Meldung per ID (XML).

        :raises ValueError: wenn ``publication_id`` keine plausible ID ist.
        :raises SourceBadResponseError: wenn die Antwort kein HR-XML ist.
        """
        if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", publication_id):
            raise ValueError(f"keine plausible Publikations-ID: {publication_id!r}")
        record = self._get(f"{PATH_PUBLICATIONS}/{publication_id}/xml", (), max_age)
        return self._parse(record, parse_publication)

    def iter_publications(
        self,
        sub_rubrics: Iterable[SubRubric] = tuple(SubRubric),
        cantons: Sequence[str] = DEFAULT_CANTONS,
        days: int = 90,
        page_size: int = 100,
        max_pages: int = 20,
        contact_only: bool = True,
        today: date | None = None,
        max_age: timedelta | None = None,
    ) -> Iterator[ShabPublication]:
        """Vollstaendige Meldungen, seitenweise nachgeladen.

        Je Treffer folgt ein Einzelabruf - die Liste enthaelt laut API-Doku
        keinen Inhalt. ``max_pages`` deckelt die Last; ``contact_only``
        liefert nur Kontaktanlaesse (siehe :attr:`CONTACT_KINDS`).
        """
        for sub_rubric in sub_rubrics:
            for page in range(max_pages):
                entries = self.list_publications(
                    sub_rubric, cantons, days, page, page_size, today, max_age
                )
                for entry in entries:
                    publication = self.fetch_publication(entry.id, max_age)
                    if contact_only and not publication.is_contact_opportunity:
                        continue
                    yield publication
                if len(entries) < page_size:
                    break

    def iter_profiles(
        self,
        sub_rubrics: Iterable[SubRubric] = tuple(SubRubric),
        cantons: Sequence[str] = DEFAULT_CANTONS,
        days: int = 90,
        page_size: int = 100,
        max_pages: int = 20,
        contact_only: bool = True,
        today: date | None = None,
        max_age: timedelta | None = None,
    ) -> Iterator[tuple[CompanyProfile, ShabPublication]]:
        """Wie :meth:`iter_publications`, aber als Profil samt Meldung.

        Meldungen ohne UID oder Firma werden uebersprungen - ohne sie gibt es
        kein Profil.
        """
        publications = self.iter_publications(
            sub_rubrics, cantons, days, page_size, max_pages, contact_only, today, max_age
        )
        for publication in publications:
            try:
                yield profile_from_publication(publication, self._now()), publication
            except ValueError:
                continue

    # -- intern ------------------------------------------------------------ #

    def _list_params(
        self,
        sub_rubric: SubRubric,
        cantons: Sequence[str],
        days: int,
        page: int,
        page_size: int,
        today: date | None,
    ) -> tuple[tuple[str, str], ...]:
        codes = [c.strip().upper() for c in cantons]
        unknown = [c for c in codes if c not in CANTONS]
        if unknown:
            raise ValueError(f"unbekannte Kantonskuerzel: {unknown}")
        if not codes:
            raise ValueError("mindestens ein Kanton erwartet")
        if not 1 <= page_size <= MAX_PAGE_SIZE or page < 0 or days < 1:
            raise ValueError(f"page_size 1..{MAX_PAGE_SIZE}, page >= 0, days >= 1 erwartet")
        end = today or self._now().date()
        start = end - timedelta(days=days)
        params: list[tuple[str, str]] = [
            ("publicationStates", PUBLICATION_STATE_PUBLISHED),
            ("subRubrics", sub_rubric.value),
        ]
        params += [("cantons", code) for code in codes]
        params += [
            ("publicationDate.start", start.isoformat()),
            ("publicationDate.end", end.isoformat()),
            ("pageRequest.size", str(page_size)),
            ("pageRequest.page", str(page)),
            ("pageRequest.sortOrders", "column:PUBLICATION_DATE|direction:DESC"),
        ]
        return tuple(params)

    def _get(
        self,
        path: str,
        params: Sequence[tuple[str, str]],
        max_age: timedelta | None,
    ) -> RawRecord:
        url = self._base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(list(params))
        accept = "application/xml" if path.endswith("/xml") else "application/json"
        return self._transport.get(url, {"Accept": accept}, max_age)

    def _parse(self, record: RawRecord, reader: Callable[..., T]) -> T:
        """Wendet einen Leser an; Lesefehler werden zu ``SourceBadResponseError``."""
        try:
            if record.url.endswith("/xml"):
                return reader(record.body)
            return reader(json.loads(record.body))
        except (ValueError, ET.ParseError) as error:
            raise SourceBadResponseError(
                SourceType.SHAB, record.url, record.status, str(error)
            ) from error
