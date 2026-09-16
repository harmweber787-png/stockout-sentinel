"""Zefix-Client: Handelsregister-Stammdaten ueber die Zefix Public REST API.

Die API (``https://www.zefix.admin.ch/ZefixPublicREST``, OGD-Lizenz, Quelle
nennen) liefert je Betrieb Firma, UID, Rechtsform, Sitz, Status, Zweck,
Adresse, Kapital, Verknuepfungen (Haupt-/Zweigniederlassungen, Revisions-
stellen, Uebernahmen), fruehere Namen und die SHAB-Publikationen mit Datum und
Mutationsart. **Nicht** enthalten: NOGA-Code, Groessenklasse, Website,
E-Mail, ein Gruendungsdatum als eigenes Feld oder zeichnungsberechtigte
Personen.

Jeder Feldname hier stammt aus der am 2026-09-16 live gelesenen OpenAPI-
Beschreibung der API (Version 2.7.2.3; ``docs/zefix_feldtabelle.md``,
erhoben mit ``scripts/probe_zefix.py``). Die Abdeckung je Feld ist **nicht
gemessen**: die Daten-Endpunkte verlangen Basic-Auth, und ohne Zugangsdaten
gab es keine Stichprobe. Alle Felder sind deshalb optional modelliert, und
die Fehlerform bei unbekannter UID (HTTP 404 oder Fehlerobjekt mit
``NOT_FOUND``) wird in beiden Varianten behandelt, bis eine Stichprobe die
tatsaechliche Form belegt.

Rechtlicher Rahmen: Zugang nur ueber die dokumentierte Schnittstelle mit
eigenen Zugangsdaten (``ZEFIX_USER`` / ``ZEFIX_PASSWORD``). Die interne
Web-API des Portals steht unter ``Disallow: /`` in dessen robots.txt und wird
nicht benutzt. Die Suche hat keinen serverseitigen Limit-Parameter; der Client
begrenzt selbst und stellt nur eng gefasste Suchen. Personendaten (Firmen von
Einzelunternehmen) stammen aus dem oeffentlichen Register und werden nur
zweckgebunden fuer die Kontaktaufnahme verwendet (revDSG).
"""

from __future__ import annotations

import base64
import json
import os
import re
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple, TypeVar

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from kmu_discovery.config.limits import RateLimits, load_rate_limits
from kmu_discovery.models import (
    CANTONS,
    CompanyProfile,
    DocumentKind,
    Evidence,
    Rechtsform,
    SourceType,
    Standort,
    TextDocument,
    Uid,
)
from kmu_discovery.sources.base import (
    HttpTransport,
    RawCache,
    RawRecord,
    SourceBadResponseError,
    urllib_sender,
)
from kmu_discovery.sources.legal_forms import legal_form_codes_for, rechtsform_from_code

__all__ = [
    "MAX_RESULTS",
    "ZEFIX_BASE_URL",
    "ZefixAddress",
    "ZefixClient",
    "ZefixCompany",
    "ZefixCompanyShort",
    "ZefixCredentials",
    "ZefixCredentialsError",
    "ZefixLegalForm",
    "ZefixMutationType",
    "ZefixOldName",
    "ZefixSogcPublication",
    "ZefixStatus",
    "ZefixText",
    "profile_from_company",
]

ZEFIX_BASE_URL = "https://www.zefix.admin.ch/ZefixPublicREST"
PATH_LEGAL_FORMS = "/api/v1/legalForm"
PATH_COMPANY_BY_UID = "/api/v1/company/uid/"
PATH_SEARCH = "/api/v1/company/search"
#: Obergrenze fuer gelesene Suchtreffer je Aufruf. Die API selbst kennt keinen
#: Limit-Parameter und bricht grosse Listen mit ``RESULTLIST_TO_LARGE`` ab.
MAX_RESULTS = 200
#: Fehlertypen laut OpenAPI ``ErrorDetails.type``.
ERROR_NOT_FOUND = "NOT_FOUND"
ERROR_RESULTLIST_TOO_LARGE = "RESULTLIST_TO_LARGE"

_UID_ADAPTER: TypeAdapter[str] = TypeAdapter(Uid)
T = TypeVar("T")
_PLZ_RE = re.compile(r"^\d{4}$")


# -- Zugangsdaten --------------------------------------------------------- #


class ZefixCredentials(NamedTuple):
    """Benutzer und Passwort fuer Basic-Auth."""

    user: str
    password: str

    def header(self) -> str:
        """Wert des ``Authorization``-Headers."""
        token = base64.b64encode(f"{self.user}:{self.password}".encode()).decode("ascii")
        return f"Basic {token}"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ZefixCredentials | None:
        """Liest ``ZEFIX_USER`` und ``ZEFIX_PASSWORD``; None, wenn eines fehlt."""
        env = os.environ if environ is None else environ
        user, password = env.get("ZEFIX_USER"), env.get("ZEFIX_PASSWORD")
        return cls(user, password) if user and password else None


class ZefixCredentialsError(ValueError):
    """Zugangsdaten fehlen - die API antwortet ohne sie mit HTTP 401."""


# -- Modelle laut OpenAPI ------------------------------------------------- #


class _ApiModel(BaseModel):
    """Basis: eingefroren, camelCase-Aliasse, unbekannte Felder werden ignoriert.

    ``extra="ignore"`` statt ``forbid``: die API darf Felder hinzufuegen, ohne
    dass der Client bricht. Fehlt ein Feld, ist es None - kein Feld ist laut
    OpenAPI Pflicht.
    """

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)


class ZefixStatus:
    """Werte von ``CompanyShort.status`` laut OpenAPI-Enum."""

    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"
    BEING_CANCELLED = "BEING_CANCELLED"


class ZefixText(_ApiModel):
    """``DFIEString``: ein Text in bis zu vier Sprachen."""

    de: str | None = None
    fr: str | None = None
    it: str | None = None
    en: str | None = None

    def first(self) -> str | None:
        """Erste vorhandene Fassung in der Reihenfolge de, fr, it, en."""
        return next((t for t in (self.de, self.fr, self.it, self.en) if t), None)


class ZefixLegalForm(_ApiModel):
    """``LegalForm``: interne ID plus eCH-0097-Code (``uid``)."""

    id: int | None = None
    uid: str | None = Field(default=None, description="eCH-0097-Code, z. B. 0107.")
    name: ZefixText | None = None
    short_name: ZefixText | None = Field(default=None, alias="shortName")

    @property
    def rechtsform(self) -> Rechtsform:
        """Rechtsform im Modellvokabular."""
        return rechtsform_from_code(self.uid)


class ZefixAddress(_ApiModel):
    """``Address``: Sitzadresse in Einzelteilen."""

    organisation: str | None = None
    care_of: str | None = Field(default=None, alias="careOf")
    street: str | None = None
    house_number: str | None = Field(default=None, alias="houseNumber")
    addon: str | None = None
    po_box: str | None = Field(default=None, alias="poBox")
    city: str | None = None
    swiss_zip_code: str | None = Field(default=None, alias="swissZipCode")

    def one_line(self) -> str:
        """Adresse als eine Zeile, leere Teile weggelassen."""
        street = " ".join(p for p in (self.street, self.house_number) if p)
        place = " ".join(p for p in (self.swiss_zip_code, self.city) if p)
        return ", ".join(p for p in (self.care_of, street, self.po_box, place) if p)


class ZefixMutationType(_ApiModel):
    """``MutationType``: Art einer SHAB-Mutation."""

    id: int | None = None
    key: str | None = None


class ZefixSogcPublication(_ApiModel):
    """``SogcPublication``: eine SHAB-Publikation zum Betrieb."""

    sogc_date: date | None = Field(default=None, alias="sogcDate")
    sogc_id: int | None = Field(default=None, alias="sogcId")
    registry_of_commerce_id: int | None = Field(default=None, alias="registryOfCommerceId")
    registry_of_commerce_canton: str | None = Field(default=None, alias="registryOfCommerceCanton")
    registry_of_commerce_journal_id: int | None = Field(
        default=None, alias="registryOfCommerceJournalId"
    )
    registry_of_commerce_journal_date: date | None = Field(
        default=None, alias="registryOfCommerceJournalDate"
    )
    message: str | None = None
    mutation_types: tuple[ZefixMutationType, ...] = Field(default=(), alias="mutationTypes")


class ZefixOldName(_ApiModel):
    """``CompanyOldName``: frueherer Name; hoehere ``sequenceNr`` = aelter."""

    name: str | None = None
    sequence_nr: int | None = Field(default=None, alias="sequenceNr")
    translation: tuple[str, ...] = ()


class ZefixCompanyShort(_ApiModel):
    """``CompanyShort``: Suchtreffer und Verknuepfungen."""

    name: str | None = None
    ehraid: int | None = None
    uid: str | None = None
    chid: str | None = None
    legal_seat_id: int | None = Field(default=None, alias="legalSeatId")
    legal_seat: str | None = Field(default=None, alias="legalSeat")
    registry_of_commerce_id: int | None = Field(default=None, alias="registryOfCommerceId")
    legal_form: ZefixLegalForm | None = Field(default=None, alias="legalForm")
    status: str | None = None
    sogc_date: date | None = Field(default=None, alias="sogcDate")
    deletion_date: date | None = Field(default=None, alias="deletionDate")

    @property
    def is_active(self) -> bool:
        """True nur bei Status ``ACTIVE``; Liquidation und Loeschung zaehlen nicht."""
        return self.status == ZefixStatus.ACTIVE

    @property
    def rechtsform(self) -> Rechtsform:
        """Rechtsform im Modellvokabular; UNBEKANNT ohne ``legalForm.uid``."""
        return self.legal_form.rechtsform if self.legal_form else Rechtsform.UNBEKANNT


class ZefixCompany(ZefixCompanyShort):
    """``CompanyFull``: der vollstaendige Registereintrag."""

    translation: tuple[str, ...] = ()
    purpose: str | None = None
    sogc_pub: tuple[ZefixSogcPublication, ...] = Field(default=(), alias="sogcPub")
    address: ZefixAddress | None = None
    canton: str | None = None
    capital_nominal: str | None = Field(default=None, alias="capitalNominal")
    capital_currency: str | None = Field(default=None, alias="capitalCurrency")
    head_offices: tuple[ZefixCompanyShort, ...] = Field(default=(), alias="headOffices")
    further_head_offices: tuple[ZefixCompanyShort, ...] = Field(
        default=(), alias="furtherHeadOffices"
    )
    branch_offices: tuple[ZefixCompanyShort, ...] = Field(default=(), alias="branchOffices")
    has_taken_over: tuple[ZefixCompanyShort, ...] = Field(default=(), alias="hasTakenOver")
    was_taken_over_by: tuple[ZefixCompanyShort, ...] = Field(default=(), alias="wasTakenOverBy")
    audit_companies: tuple[ZefixCompanyShort, ...] = Field(default=(), alias="auditCompanies")
    old_names: tuple[ZefixOldName, ...] = Field(default=(), alias="oldNames")
    cantonal_excerpt_web: str | None = Field(default=None, alias="cantonalExcerptWeb")
    zefix_detail_web: ZefixText | None = Field(default=None, alias="zefixDetailWeb")

    @property
    def earliest_sogc_date(self) -> date | None:
        """Aelteste gelieferte SHAB-Publikation.

        Nur eine Untergrenze fuer das Alter des Eintrags: ob ``sogcPub`` die
        ganze Historie enthaelt, ist ohne Stichprobe nicht belegt. Ein
        Gruendungsdatum als eigenes Feld kennt die API nicht.
        """
        dates = [p.sogc_date for p in self.sogc_pub if p.sogc_date]
        return min(dates) if dates else None

    @property
    def mutation_count(self) -> int:
        """Anzahl gelieferter SHAB-Publikationen."""
        return len(self.sogc_pub)

    @property
    def has_audit_company(self) -> bool:
        """True, wenn eine Revisionsstelle eingetragen ist.

        Fehlt sie bei AG/GmbH, hat der Betrieb in der Regel auf die
        eingeschraenkte Revision verzichtet (Opting-out, nur bis zehn
        Vollzeitstellen im Jahresmittel zulaessig) - ein Groessensignal.
        """
        return bool(self.audit_companies)

    @property
    def source_url(self) -> str | None:
        """Beste dereferenzierbare Beleg-URL: Zefix-Detailseite, sonst Kantonsauszug."""
        detail = self.zefix_detail_web.first() if self.zefix_detail_web else None
        for candidate in (detail, self.cantonal_excerpt_web):
            if candidate and candidate.startswith(("http://", "https://")):
                return candidate
        return None


_COMPANIES: TypeAdapter[list[ZefixCompany]] = TypeAdapter(list[ZefixCompany])
_SHORTS: TypeAdapter[list[ZefixCompanyShort]] = TypeAdapter(list[ZefixCompanyShort])
_LEGAL_FORMS: TypeAdapter[list[ZefixLegalForm]] = TypeAdapter(list[ZefixLegalForm])


# -- Uebergang ins Betriebsprofil ---------------------------------------- #


def _evidence(
    company: ZefixCompany, fallback_url: str, quote: str, locator: str, retrieved_at: datetime
) -> Evidence:
    return Evidence(
        source_type=SourceType.ZEFIX,
        source_url=company.source_url or fallback_url,
        quote=quote[:2000],
        retrieved_at=retrieved_at,
        locator=locator,
    )


def profile_from_company(
    company: ZefixCompany,
    retrieved_at: datetime,
    fallback_url: str = ZEFIX_BASE_URL,
) -> CompanyProfile:
    """Baut das Betriebsprofil; jedes uebernommene Feld traegt einen Beleg.

    Leer bleiben, was die API nicht kennt: ``noga_codes``, ``groessenklasse``,
    ``website``, ``emails``. Der Zweck wird zusaetzlich als
    :class:`TextDocument` der Art ``REGISTER_PURPOSE`` angehaengt.

    :raises ValueError: wenn UID oder Firma fehlen - ohne sie gibt es kein Profil.
    """
    if not company.uid or not company.name:
        raise ValueError("Zefix-Eintrag ohne UID oder Firma")
    url = fallback_url
    if not company.source_url and company.uid:
        compact = re.sub(r"[^0-9A-Za-z]", "", company.uid)
        url = f"{fallback_url}{PATH_COMPANY_BY_UID}{compact}"

    evidence = [_evidence(company, url, company.name, "CompanyFull.name", retrieved_at)]
    if company.legal_form and company.legal_form.uid:
        evidence.append(
            _evidence(
                company, url, company.legal_form.uid, "CompanyFull.legalForm.uid", retrieved_at
            )
        )
    if company.status:
        evidence.append(_evidence(company, url, company.status, "CompanyFull.status", retrieved_at))

    standort: Standort | None = None
    if company.canton and company.canton.strip().upper() in CANTONS:
        address = company.address
        plz = (
            address.swiss_zip_code
            if address and address.swiss_zip_code and _PLZ_RE.match(address.swiss_zip_code)
            else None
        )
        standort = Standort(
            kanton=company.canton, plz=plz, ort=(address.city if address else company.legal_seat)
        )
        quote = address.one_line() if address else (company.legal_seat or "")
        evidence.append(
            _evidence(
                company,
                url,
                f"{quote} ({company.canton})".strip(),
                "CompanyFull.address",
                retrieved_at,
            )
        )

    documents: list[TextDocument] = []
    if company.purpose:
        evidence.append(
            _evidence(company, url, company.purpose, "CompanyFull.purpose", retrieved_at)
        )
        documents.append(
            TextDocument(
                kind=DocumentKind.REGISTER_PURPOSE,
                url=company.source_url or url,
                text=company.purpose,
                retrieved_at=retrieved_at,
            )
        )

    return CompanyProfile(
        uid=company.uid,
        name=company.name,
        rechtsform=company.rechtsform,
        standort=standort,
        zweck=company.purpose,
        documents=documents,
        evidence=evidence,
    )


# -- Client --------------------------------------------------------------- #


class ZefixClient:
    """Liest Registereintraege aus der Zefix Public REST API.

    Der Client kennt nur Zefix. Rate-Limit, Retry, Cache und typisierte
    Fehler kommen vom :class:`HttpTransport`; ``now`` ist injizierbar. Die
    Zugangsdaten gehen nur in den ``Authorization``-Header und nie in den
    Rohdaten-Cache (der speichert URL, Body und Antwort, keine Header).
    """

    def __init__(
        self,
        transport: HttpTransport,
        credentials: ZefixCredentials,
        base_url: str = ZEFIX_BASE_URL,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Nimmt einen Transport fuer die Quelle Zefix und die Zugangsdaten entgegen."""
        if transport.source is not SourceType.ZEFIX:
            raise ValueError(f"Transport fuer {transport.source} statt zefix")
        self._transport = transport
        self._credentials = credentials
        self._base_url = base_url.rstrip("/")
        self._now = now

    @classmethod
    def build(
        cls,
        credentials: ZefixCredentials | None = None,
        cache_dir: Path | None = None,
        ca_bundle: Path | None = None,
        limits: RateLimits | None = None,
        base_url: str = ZEFIX_BASE_URL,
    ) -> ZefixClient:
        """Produktiver Client; Zugangsdaten aus dem Argument oder der Umgebung.

        :raises ZefixCredentialsError: wenn keine Zugangsdaten vorliegen.
        """
        resolved = credentials or ZefixCredentials.from_env()
        if resolved is None:
            raise ZefixCredentialsError(
                "Zugangsdaten fehlen: ZEFIX_USER und ZEFIX_PASSWORD setzen "
                "(Zugang beim Eidgenoessischen Amt fuer das Handelsregister beantragen)"
            )
        config = (limits or load_rate_limits()).for_source(SourceType.ZEFIX)
        transport = HttpTransport(
            source=SourceType.ZEFIX,
            config=config,
            sender=urllib_sender(ca_bundle),
            cache=RawCache(cache_dir) if cache_dir else None,
        )
        return cls(transport, resolved, base_url=base_url)

    @property
    def transport(self) -> HttpTransport:
        """Der Transport - fuer Zaehler (``calls_sent``, ``cache_hits``)."""
        return self._transport

    # -- Abfragen ---------------------------------------------------------- #

    def legal_forms(self, max_age: timedelta | None = None) -> list[ZefixLegalForm]:
        """Die Rechtsformliste der API (interne ID, eCH-0097-Code, Namen)."""
        record = self._get(PATH_LEGAL_FORMS, max_age)
        return self._validate(_LEGAL_FORMS, record)

    def fetch_by_uid(self, uid: str, max_age: timedelta | None = None) -> ZefixCompany | None:
        """Ein Betrieb per UID; None, wenn die API ihn nicht kennt.

        Die API liefert eine Liste (eine UID kann mehrere Eintraege haben,
        z. B. nach Loeschung und Neueintrag). Bevorzugt wird der aktive Eintrag,
        sonst der erste.

        :raises ValueError: wenn ``uid`` kein gueltiges UID-Format hat.
        """
        normalized = _UID_ADAPTER.validate_python(uid)
        compact = normalized.replace("-", "").replace(".", "")
        try:
            record = self._get(PATH_COMPANY_BY_UID + compact, max_age)
        except SourceBadResponseError as error:
            if error.status == 404:
                return None
            raise
        if self._is_not_found(record):
            return None
        companies = self._validate(_COMPANIES, record)
        if not companies:
            return None
        return next((c for c in companies if c.is_active), companies[0])

    def fetch_profile(self, uid: str, max_age: timedelta | None = None) -> CompanyProfile | None:
        """Bequemlichkeit: :meth:`fetch_by_uid` plus :func:`profile_from_company`."""
        company = self.fetch_by_uid(uid, max_age)
        if company is None:
            return None
        return profile_from_company(company, self._now(), self._base_url)

    def search(
        self,
        name: str,
        kanton: str | None = None,
        rechtsform: Rechtsform | None = None,
        active_only: bool = True,
        max_results: int = MAX_RESULTS,
        max_age: timedelta | None = None,
    ) -> list[ZefixCompanyShort]:
        """Namenssuche, eng gefasst: Name plus optional Kanton und Rechtsform.

        Die API filtert je Aufruf auf genau einen eCH-0097-Code; eine
        Rechtsform mit mehreren Codes (Zweigniederlassung) kostet mehrere
        Aufrufe. Treffer werden auf ``max_results`` gekuerzt, weil die API
        keinen Limit-Parameter kennt.

        :raises ValueError: bei leerem Namen, unbekanntem Kanton oder einer
            Rechtsform ohne Codes (``UNBEKANNT``).
        """
        if not name.strip():
            raise ValueError("Suchbegriff darf nicht leer sein")
        if not 1 <= max_results <= MAX_RESULTS:
            raise ValueError(f"max_results 1..{MAX_RESULTS} erwartet")
        query: dict[str, Any] = {"name": name.strip(), "activeOnly": active_only}
        if kanton is not None:
            canton = kanton.strip().upper()
            if canton not in CANTONS:
                raise ValueError(f"unbekanntes Kantonskuerzel: {kanton!r}")
            query["canton"] = canton
        codes: tuple[str | None, ...] = (None,)
        if rechtsform is not None:
            codes = legal_form_codes_for(rechtsform)
            if not codes:
                raise ValueError(f"Rechtsform {rechtsform} hat keinen eCH-0097-Code")

        hits: list[ZefixCompanyShort] = []
        seen: set[int | str] = set()
        for code in codes:
            payload = dict(query)
            if code is not None:
                payload["legalFormUid"] = code
            record = self._post(PATH_SEARCH, payload, max_age)
            if self._is_not_found(record):
                continue
            for hit in self._validate(_SHORTS, record):
                key: int | str = hit.ehraid if hit.ehraid is not None else (hit.uid or id(hit))
                if key in seen:
                    continue
                seen.add(key)
                hits.append(hit)
                if len(hits) >= max_results:
                    return hits
        return hits

    # -- intern ------------------------------------------------------------ #

    def _headers(self, with_body: bool) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Authorization": self._credentials.header(),
        }
        if with_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _get(self, path: str, max_age: timedelta | None) -> RawRecord:
        return self._transport.get(self._base_url + path, self._headers(False), max_age)

    def _post(self, path: str, payload: Mapping[str, Any], max_age: timedelta | None) -> RawRecord:
        body = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return self._transport.post(self._base_url + path, body, self._headers(True), max_age)

    def _parse(self, record: RawRecord) -> object:
        try:
            parsed: object = json.loads(record.body) if record.body.strip() else None
            return parsed
        except json.JSONDecodeError as error:
            raise SourceBadResponseError(
                SourceType.ZEFIX, record.url, record.status, f"kein JSON: {error}"
            ) from error

    def _is_not_found(self, record: RawRecord) -> bool:
        """Fehlerobjekt laut OpenAPI (``{"error": {"type": ...}}``) auswerten.

        ``NOT_FOUND`` heisst "kein Treffer"; jede andere Fehlerart ist ein
        endgueltiger Fehler der Anfrage.
        """
        parsed = self._parse(record)
        if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
            kind = str(parsed["error"].get("type", ""))
            if kind == ERROR_NOT_FOUND:
                return True
            message = str(parsed["error"].get("message", ""))[:200]
            raise SourceBadResponseError(
                SourceType.ZEFIX, record.url, record.status, f"{kind}: {message}"
            )
        return False

    def _validate(self, adapter: TypeAdapter[T], record: RawRecord) -> T:
        parsed = self._parse(record)
        if not isinstance(parsed, list):
            raise SourceBadResponseError(
                SourceType.ZEFIX, record.url, record.status, "Antwort ist keine Liste"
            )
        try:
            return adapter.validate_python(parsed)
        except ValidationError as error:
            raise SourceBadResponseError(
                SourceType.ZEFIX,
                record.url,
                record.status,
                f"Antwort passt nicht zum Schema: {error}",
            ) from error
