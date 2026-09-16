"""LINDAS-Client: Zefix-Stammdaten ueber den SPARQL-Endpunkt des Bundes.

LINDAS (Linked Data Service des Bundesarchivs) spiegelt das Handelsregister
(Zefix) als RDF-Graph. Der Graph liefert je Betrieb Firma, UID, Rechtsform,
Zweckartikel, Sitzadresse und BFS-Gemeindenummer - **nicht** NOGA-Code,
Groessenklasse, Website, E-Mail oder zeichnungsberechtigte Personen. Diese
Felder bleiben im :class:`~kmu_discovery.models.CompanyProfile` leer und
kommen aus anderen Quellen.

Jeder Praedikatname hier stammt aus der am 2026-09-16 live erhobenen
Feldtabelle (``docs/lindas_feldtabelle.md``, erhoben mit
``scripts/probe_lindas.py``), nicht aus dem Gedaechtnis. Die Abdeckung je
Feld steht als Kommentar an der Konstante.

Rechtlicher Rahmen: LINDAS ist ein offizieller Open-Data-Dienst. Der Client
stellt wenige, gezielte Abfragen mit identifizierendem User-Agent ueber den
rate-limitierten :class:`~kmu_discovery.sources.base.HttpTransport` und
laedt keine Personendaten in Bulk. Firmen von Einzelunternehmen enthalten
naturgemaess Personennamen; sie stammen aus dem oeffentlichen Register und
werden nur zweckgebunden fuer die Kontaktaufnahme verwendet (revDSG).
"""

from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from kmu_discovery.config.limits import RateLimits, load_rate_limits
from kmu_discovery.models import (
    CANTONS,
    AwareDatetime,
    CompanyProfile,
    DocumentKind,
    Evidence,
    Rechtsform,
    SourceType,
    SourceUrl,
    Standort,
    TextDocument,
    Uid,
)
from kmu_discovery.sources.base import (
    HttpTransport,
    RawCache,
    SourceBadResponseError,
    urllib_sender,
)

__all__ = [
    "LEGAL_FORM_CODES",
    "LINDAS_ENDPOINT",
    "MAX_PAGE_SIZE",
    "ZEFIX_GRAPH",
    "LindasClient",
    "SparqlValue",
    "ZefixRecord",
    "legal_form_code",
    "legal_form_codes_for",
    "parse_records",
    "parse_sparql_json",
    "profile_from_record",
    "rechtsform_from_legal_form",
    "sparql_string",
]

LINDAS_ENDPOINT = "https://lindas.admin.ch/query"
#: Benannter Graph mit den Zefix-Daten. Mit ``ASK`` live bestaetigt.
ZEFIX_GRAPH = "https://lindas.admin.ch/foj/zefix"
#: Zielklasse; 793 459 Instanzen am 2026-09-16.
ZEFIX_ORGANISATION = "https://schema.ld.admin.ch/ZefixOrganisation"
#: Praefix der eCH-0097-Rechtsform-IRIs, Objekt von ``schema:additionalType``.
LEGAL_FORMS_BASE = "https://ld.admin.ch/ech/97/legalforms/"
#: Praefix der Gemeinde-IRIs, Objekt von ``schema.ld.admin.ch/municipality``.
MUNICIPALITY_BASE = "https://ld.admin.ch/municipality/"
#: Groesste Seite fuer :meth:`LindasClient.list_companies` - haelt die
#: Antwortgroesse und die Last auf dem Endpunkt klein.
MAX_PAGE_SIZE = 500

_SCHEMA = "http://schema.org/"

#: Praedikate der Zielklasse mit live gemessener Abdeckung (Subjekte mit
#: mindestens einem Wert) und maximaler Kardinalitaet je Subjekt.
#: ``locn:address`` und ``schema:inDefinedTermSet`` sind absichtlich nicht
#: dabei: das eine dupliziert ``schema:address``, das andere ist konstant.
P_LEGAL_NAME = _SCHEMA + "legalName"  # 100.0 %, 1 je Subjekt
P_NAME = _SCHEMA + "name"  # 99.99 %, bis 8 je Subjekt (Sprachfassungen)
P_DESCRIPTION = _SCHEMA + "description"  # 98.4 %, 1 je Subjekt (Zweckartikel)
P_ADDITIONAL_TYPE = _SCHEMA + "additionalType"  # 100.0 %, 1 (Rechtsform-IRI)
P_IDENTIFIER = _SCHEMA + "identifier"  # 100.0 %, 3 (PropertyValue-Knoten)
P_ADDRESS = _SCHEMA + "address"  # 100.0 %, 1 (PostalAddress-Knoten)
P_MUNICIPALITY = "https://schema.ld.admin.ch/municipality"  # 100.0 %, 1
P_VALUE = _SCHEMA + "value"  # am PropertyValue-Knoten, 100 %
P_STREET = _SCHEMA + "streetAddress"  # am Adressknoten
P_POSTAL_CODE = _SCHEMA + "postalCode"  # am Adressknoten
P_LOCALITY = _SCHEMA + "addressLocality"  # am Adressknoten
P_REGION = _SCHEMA + "addressRegion"  # am Adressknoten, Kantonskuerzel

#: ``schema:name`` der drei Identifikator-Knoten je Betrieb (je 793 459).
ID_UID = "CompanyUID"
ID_CHID = "CompanyCHID"

#: eCH-0097-Rechtsformcodes -> Modell-Rechtsform. Codes und Bezeichnungen aus
#: dem Graphen ``https://lindas.admin.ch/lindas-ech`` (live gelesen). Alle 15
#: im Zefix-Graphen vorkommenden Codes sind abgedeckt; Codes ausserhalb des
#: Handelsregisters (02xx-05xx) kommen dort nicht vor und bleiben UNBEKANNT.
LEGAL_FORM_CODES: Mapping[str, Rechtsform] = {
    "0101": Rechtsform.EINZELUNTERNEHMEN,
    "0103": Rechtsform.KOLLEKTIVGESELLSCHAFT,
    "0104": Rechtsform.KOMMANDITGESELLSCHAFT,
    "0105": Rechtsform.AG,  # Kommanditaktiengesellschaft - AG-Sonderform
    "0106": Rechtsform.AG,
    "0107": Rechtsform.GMBH,
    "0108": Rechtsform.GENOSSENSCHAFT,
    "0109": Rechtsform.VEREIN,
    "0110": Rechtsform.STIFTUNG,
    "0111": Rechtsform.ZWEIGNIEDERLASSUNG,  # auslaendische Niederlassung
    "0113": Rechtsform.UNBEKANNT,  # besondere Rechtsform
    "0117": Rechtsform.OEFFENTLICH_RECHTLICH,  # Institut des oeffentlichen Rechts
    "0118": Rechtsform.UNBEKANNT,  # nichtkaufmaennische Prokuren
    "0119": Rechtsform.UNBEKANNT,  # Haupt von Gemeinderschaften
    "0151": Rechtsform.ZWEIGNIEDERLASSUNG,  # schweizerische Zweigniederlassung
}

_UID_ADAPTER: TypeAdapter[str] = TypeAdapter(Uid)
_PLZ_RE = re.compile(r"^\d{4}$")
_LIQUIDATION_RE = re.compile(
    r"\bin\s+liquidation\b|\ben\s+liquidation\b|\bin\s+liquidazione\b", re.IGNORECASE
)


# -- SPARQL-Grundlagen ---------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SparqlValue:
    """Ein Ergebniswert aus einer SPARQL-JSON-Antwort."""

    value: str
    kind: str
    datatype: str | None = None
    language: str | None = None

    @classmethod
    def parse(cls, raw: Mapping[str, Any]) -> SparqlValue:
        """Liest einen Binding-Eintrag (``{"type": ..., "value": ...}``)."""
        return cls(
            value=str(raw.get("value", "")),
            kind=str(raw.get("type", "unknown")),
            datatype=raw.get("datatype"),
            language=raw.get("xml:lang"),
        )


SparqlRow = dict[str, SparqlValue]


def parse_sparql_json(body: str) -> list[SparqlRow]:
    """Zerlegt eine ``application/sparql-results+json``-Antwort in Zeilen.

    >>> parse_sparql_json('{"head":{"vars":["x"]},"results":{"bindings":['
    ...                   '{"x":{"type":"literal","value":"a","xml:lang":"de"}}]}}')
    [{'x': SparqlValue(value='a', kind='literal', datatype=None, language='de')}]

    :raises ValueError: wenn der Text kein SPARQL-JSON-Ergebnis ist.
    """
    parsed = json.loads(body)
    if not isinstance(parsed, dict) or "results" not in parsed:
        raise ValueError("kein SPARQL-JSON-Ergebnis (Schluessel 'results' fehlt)")
    bindings = parsed["results"].get("bindings")
    if not isinstance(bindings, list):
        raise ValueError("kein SPARQL-JSON-Ergebnis ('bindings' ist keine Liste)")
    rows: list[SparqlRow] = []
    for row in bindings:
        if not isinstance(row, dict):
            raise ValueError("Binding-Zeile ist kein Objekt")
        rows.append({name: SparqlValue.parse(raw) for name, raw in row.items()})
    return rows


def sparql_string(value: str) -> str:
    r"""Kodiert einen Text als SPARQL-Stringliteral in doppelten Anfuehrungszeichen.

    >>> sparql_string('a"b\\c')
    '"a\\"b\\\\c"'
    """
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


# -- Rechtsform ----------------------------------------------------------- #


def legal_form_code(iri: str | None) -> str | None:
    """Zieht den eCH-0097-Code aus der Rechtsform-IRI.

    >>> legal_form_code("https://ld.admin.ch/ech/97/legalforms/0107")
    '0107'
    >>> legal_form_code("https://example.org/other") is None
    True
    """
    if iri and iri.startswith(LEGAL_FORMS_BASE):
        code = iri[len(LEGAL_FORMS_BASE) :]
        if code.isdigit():
            return code
    return None


def rechtsform_from_legal_form(iri: str | None) -> Rechtsform:
    """Bildet eine Rechtsform-IRI auf das Modell ab; unbekannt bleibt UNBEKANNT.

    >>> rechtsform_from_legal_form("https://ld.admin.ch/ech/97/legalforms/0106")
    <Rechtsform.AG: 'ag'>
    """
    code = legal_form_code(iri)
    if code is None:
        return Rechtsform.UNBEKANNT
    return LEGAL_FORM_CODES.get(code, Rechtsform.UNBEKANNT)


def legal_form_codes_for(rechtsform: Rechtsform) -> tuple[str, ...]:
    """Alle eCH-0097-Codes, die auf diese Rechtsform abgebildet werden.

    ``UNBEKANNT`` liefert bewusst nichts: es steht fuer alles, was die Tabelle
    nicht kennt, und laesst sich deshalb nicht als Filter ausdruecken.

    >>> legal_form_codes_for(Rechtsform.ZWEIGNIEDERLASSUNG)
    ('0111', '0151')
    >>> legal_form_codes_for(Rechtsform.UNBEKANNT)
    ()
    """
    if rechtsform is Rechtsform.UNBEKANNT:
        return ()
    return tuple(code for code, form in LEGAL_FORM_CODES.items() if form is rechtsform)


# -- Datensatz ------------------------------------------------------------ #


class ZefixRecord(BaseModel):
    """Ein Betrieb, wie ihn der Zefix-Graph auf LINDAS liefert.

    Felder sind bewusst roh (Kantonskuerzel als Text, PLZ als Text); die
    Validierung passiert erst beim Uebergang ins :class:`CompanyProfile`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    iri: SourceUrl = Field(description="Betriebs-IRI auf register.ld.admin.ch, dereferenzierbar.")
    uid: Uid
    chid: str | None = None
    legal_name: str
    alternate_names: tuple[str, ...] = Field(
        default=(), description="Weitere Sprachfassungen der Firma, ohne die Firma selbst."
    )
    zweck: str | None = None
    legal_form_iri: str | None = None
    street: str | None = None
    plz: str | None = None
    ort: str | None = None
    kanton: str | None = None
    municipality_bfs: int | None = None
    retrieved_at: AwareDatetime

    @property
    def legal_form_code(self) -> str | None:
        """eCH-0097-Code der Rechtsform, z. B. ``0107``."""
        return legal_form_code(self.legal_form_iri)

    @property
    def rechtsform(self) -> Rechtsform:
        """Rechtsform im Modellvokabular."""
        return rechtsform_from_legal_form(self.legal_form_iri)

    @property
    def in_liquidation(self) -> bool:
        """True, wenn die Firma den Liquidationszusatz traegt.

        Der Graph fuehrt kein Statusfeld; der Zusatz steht nur im Namen
        (``in Liquidation`` / ``en liquidation`` / ``in liquidazione``).
        """
        return bool(_LIQUIDATION_RE.search(self.legal_name))


def _first(row: SparqlRow, name: str) -> str | None:
    value = row.get(name)
    return value.value if value is not None and value.value != "" else None


def parse_records(rows: Iterable[SparqlRow], retrieved_at: datetime) -> list[ZefixRecord]:
    """Faltet die Zeilen einer Betriebsabfrage zu Datensaetzen.

    Eine Zeile je Kombination aus Betrieb und ``schema:name``; alle uebrigen
    Felder sind 1:1 und werden aus der ersten Zeile mit Wert genommen.
    Zeilen ohne ``?company`` oder ohne gueltige UID werden uebersprungen.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        iri = _first(row, "company")
        if iri is None:
            continue
        entry = grouped.setdefault(iri, {"names": [], "row": {}})
        for key, value in row.items():
            if key == "name":
                if value.value and value.value not in entry["names"]:
                    entry["names"].append(value.value)
            elif key not in entry["row"] and value.value != "":
                entry["row"][key] = value.value

    records: list[ZefixRecord] = []
    for iri, entry in grouped.items():
        fields: dict[str, str] = entry["row"]
        uid = fields.get("uid")
        legal_name = fields.get("legalName")
        if not uid or not legal_name:
            continue
        try:
            normalized_uid = _UID_ADAPTER.validate_python(uid)
        except ValueError:
            continue
        municipality = fields.get("municipality")
        bfs: int | None = None
        if municipality and municipality.startswith(MUNICIPALITY_BASE):
            tail = municipality[len(MUNICIPALITY_BASE) :]
            bfs = int(tail) if tail.isdigit() else None
        records.append(
            ZefixRecord(
                iri=iri,
                uid=normalized_uid,
                chid=fields.get("chid"),
                legal_name=legal_name,
                alternate_names=tuple(n for n in entry["names"] if n != legal_name),
                zweck=fields.get("description"),
                legal_form_iri=fields.get("legalForm"),
                street=fields.get("street"),
                plz=fields.get("postalCode"),
                ort=fields.get("locality"),
                kanton=fields.get("region"),
                municipality_bfs=bfs,
                retrieved_at=retrieved_at,
            )
        )
    return records


# -- Uebergang ins Betriebsprofil ---------------------------------------- #


def _evidence(record: ZefixRecord, quote: str, locator: str) -> Evidence:
    return Evidence(
        source_type=SourceType.LINDAS,
        source_url=record.iri,
        quote=quote[:2000],
        retrieved_at=record.retrieved_at,
        locator=locator,
    )


def profile_from_record(record: ZefixRecord) -> CompanyProfile:
    """Baut das Betriebsprofil; jedes uebernommene Feld traegt einen Beleg.

    Was der Graph nicht kennt, bleibt leer: ``noga_codes``, ``groessenklasse``,
    ``website``, ``emails``. Der Zweckartikel wird zusaetzlich als
    :class:`TextDocument` der Art ``REGISTER_PURPOSE`` angehaengt, damit die
    Gates darauf arbeiten koennen.
    """
    evidence = [_evidence(record, record.legal_name, "schema:legalName")]
    if record.legal_form_iri:
        evidence.append(_evidence(record, record.legal_form_iri, "schema:additionalType"))

    standort: Standort | None = None
    if record.kanton and record.kanton.strip().upper() in CANTONS:
        plz = record.plz if record.plz and _PLZ_RE.match(record.plz) else None
        standort = Standort(kanton=record.kanton, plz=plz, ort=record.ort)
        address = ", ".join(
            part
            for part in (record.street, f"{record.plz or ''} {record.ort or ''}".strip())
            if part
        )
        evidence.append(_evidence(record, f"{address} ({record.kanton})", "schema:address"))

    documents: list[TextDocument] = []
    if record.zweck:
        evidence.append(_evidence(record, record.zweck, "schema:description"))
        documents.append(
            TextDocument(
                kind=DocumentKind.REGISTER_PURPOSE,
                url=record.iri,
                text=record.zweck,
                retrieved_at=record.retrieved_at,
            )
        )

    return CompanyProfile(
        uid=record.uid,
        name=record.legal_name,
        rechtsform=record.rechtsform,
        standort=standort,
        zweck=record.zweck,
        documents=documents,
        evidence=evidence,
    )


# -- Client --------------------------------------------------------------- #


class LindasClient:
    """Liest Zefix-Betriebe aus LINDAS - einzeln per UID oder seitenweise.

    Der Client kennt nur LINDAS. Rate-Limit, Retry, Cache und typisierte
    Fehler kommen vom :class:`HttpTransport`; ``now`` ist injizierbar.
    """

    def __init__(
        self,
        transport: HttpTransport,
        endpoint: str = LINDAS_ENDPOINT,
        graph: str = ZEFIX_GRAPH,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Nimmt einen Transport fuer die Quelle LINDAS entgegen."""
        if transport.source is not SourceType.LINDAS:
            raise ValueError(f"Transport fuer {transport.source} statt lindas")
        self._transport = transport
        self._endpoint = endpoint
        self._graph = graph
        self._now = now

    @classmethod
    def build(
        cls,
        cache_dir: Path | None = None,
        ca_bundle: Path | None = None,
        limits: RateLimits | None = None,
        endpoint: str = LINDAS_ENDPOINT,
    ) -> LindasClient:
        """Produktiver Client: ``urllib``-Sender, Limits aus ``rate_limits.yaml``."""
        config = (limits or load_rate_limits()).for_source(SourceType.LINDAS)
        transport = HttpTransport(
            source=SourceType.LINDAS,
            config=config,
            sender=urllib_sender(ca_bundle),
            cache=RawCache(cache_dir) if cache_dir else None,
        )
        return cls(transport, endpoint=endpoint)

    @property
    def transport(self) -> HttpTransport:
        """Der Transport - fuer Zaehler (``calls_sent``, ``cache_hits``)."""
        return self._transport

    # -- Abfragen ---------------------------------------------------------- #

    def query(self, sparql: str, max_age: timedelta | None = None) -> list[SparqlRow]:
        """Stellt eine SELECT-Abfrage und liefert die Ergebniszeilen.

        :raises SourceError: Transportfehler (siehe ``sources.base``).
        :raises SourceBadResponseError: Antwort ist kein SPARQL-JSON.
        """
        body = urllib.parse.urlencode({"query": sparql})
        record = self._transport.post(
            self._endpoint,
            body,
            headers={
                "Accept": "application/sparql-results+json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            max_age=max_age,
        )
        try:
            return parse_sparql_json(record.body)
        except ValueError as error:
            raise SourceBadResponseError(
                SourceType.LINDAS, self._endpoint, record.status, str(error)
            ) from error

    def fetch_by_uid(self, uid: str, max_age: timedelta | None = None) -> ZefixRecord | None:
        """Ein Betrieb per UID (``CHE-123.456.789`` oder kompakt); None, wenn unbekannt.

        :raises ValueError: wenn ``uid`` kein gueltiges UID-Format hat.
        """
        normalized = _UID_ADAPTER.validate_python(uid)
        compact = normalized.replace("-", "").replace(".", "")
        selector = (
            f"?company {_iri(P_IDENTIFIER)} ?uidNode .\n"
            f"    ?uidNode {_iri(P_NAME)} {sparql_string(ID_UID)} ; "
            f"{_iri(P_VALUE)} {sparql_string(compact)} ."
        )
        rows = self.query(self._company_query(selector), max_age)
        records = parse_records(rows, self._now())
        return records[0] if records else None

    def list_companies(
        self,
        kanton: str,
        rechtsformen: Iterable[Rechtsform] | None = None,
        limit: int = 100,
        offset: int = 0,
        max_age: timedelta | None = None,
    ) -> list[ZefixRecord]:
        """Eine Seite Betriebe eines Kantons, stabil nach Betriebs-IRI sortiert.

        ``rechtsformen`` schraenkt auf eCH-0097-Codes ein, die auf diese
        Rechtsformen abbilden; ``UNBEKANNT`` ist dabei nicht filterbar.

        :raises ValueError: bei unbekanntem Kanton oder unzulaessiger Seite.
        """
        canton = kanton.strip().upper()
        if canton not in CANTONS:
            raise ValueError(f"unbekanntes Kantonskuerzel: {kanton!r}")
        if not 1 <= limit <= MAX_PAGE_SIZE or offset < 0:
            raise ValueError(f"limit 1..{MAX_PAGE_SIZE} und offset >= 0 erwartet")

        patterns = [
            f"?company a {_iri(ZEFIX_ORGANISATION)} ; {_iri(P_ADDRESS)} ?pageAddr .",
            f"?pageAddr {_iri(P_REGION)} {sparql_string(canton)} .",
        ]
        if rechtsformen is not None:
            codes = sorted({code for form in rechtsformen for code in legal_form_codes_for(form)})
            if not codes:
                return []
            values = " ".join(_iri(LEGAL_FORMS_BASE + code) for code in codes)
            patterns.append(f"VALUES ?pageForm {{ {values} }}")
            patterns.append(f"?company {_iri(P_ADDITIONAL_TYPE)} ?pageForm .")
        inner = "\n      ".join(patterns)
        selector = (
            "{ SELECT ?company WHERE {\n"
            f"      {inner}\n"
            f"    }} ORDER BY ?company LIMIT {limit} OFFSET {offset} }}"
        )
        rows = self.query(self._company_query(selector), max_age)
        return parse_records(rows, self._now())

    def iter_companies(
        self,
        kanton: str,
        rechtsformen: Iterable[Rechtsform] | None = None,
        page_size: int = 100,
        max_age: timedelta | None = None,
    ) -> Iterator[ZefixRecord]:
        """Alle Betriebe eines Kantons, seitenweise nachgeladen."""
        forms = tuple(rechtsformen) if rechtsformen is not None else None
        offset = 0
        while True:
            page = self.list_companies(kanton, forms, page_size, offset, max_age)
            yield from page
            if len(page) < page_size:
                return
            offset += page_size

    def fetch_profile(self, uid: str, max_age: timedelta | None = None) -> CompanyProfile | None:
        """Bequemlichkeit: :meth:`fetch_by_uid` plus :func:`profile_from_record`."""
        record = self.fetch_by_uid(uid, max_age)
        return profile_from_record(record) if record else None

    # -- intern ------------------------------------------------------------ #

    def _company_query(self, selector: str) -> str:
        """Gemeinsamer Rumpf: ``selector`` bindet ``?company``, der Rest holt die Felder.

        Alle Felder ausser ``schema:legalName`` sind OPTIONAL, damit ein
        Betrieb ohne Zweckartikel (1.6 %) nicht verschwindet.
        """
        return f"""SELECT ?company ?uid ?chid ?legalName ?name ?description ?legalForm
       ?municipality ?street ?postalCode ?locality ?region
WHERE {{
  GRAPH {_iri(self._graph)} {{
    {selector}
    ?company a {_iri(ZEFIX_ORGANISATION)} ;
             {_iri(P_LEGAL_NAME)} ?legalName ;
             {_iri(P_IDENTIFIER)} ?uidId .
    ?uidId {_iri(P_NAME)} {sparql_string(ID_UID)} ; {_iri(P_VALUE)} ?uid .
    OPTIONAL {{ ?company {_iri(P_IDENTIFIER)} ?chidId .
               ?chidId {_iri(P_NAME)} {sparql_string(ID_CHID)} ; {_iri(P_VALUE)} ?chid . }}
    OPTIONAL {{ ?company {_iri(P_NAME)} ?name . }}
    OPTIONAL {{ ?company {_iri(P_DESCRIPTION)} ?description . }}
    OPTIONAL {{ ?company {_iri(P_ADDITIONAL_TYPE)} ?legalForm . }}
    OPTIONAL {{ ?company {_iri(P_MUNICIPALITY)} ?municipality . }}
    OPTIONAL {{
      ?company {_iri(P_ADDRESS)} ?addr .
      OPTIONAL {{ ?addr {_iri(P_STREET)} ?street . }}
      OPTIONAL {{ ?addr {_iri(P_POSTAL_CODE)} ?postalCode . }}
      OPTIONAL {{ ?addr {_iri(P_LOCALITY)} ?locality . }}
      OPTIONAL {{ ?addr {_iri(P_REGION)} ?region . }}
    }}
  }}
}}
ORDER BY ?company"""


def _iri(value: str) -> str:
    """Schreibt eine IRI in spitzen Klammern; verbietet Zeichen, die sie sprengen.

    >>> _iri("https://example.org/a")
    '<https://example.org/a>'
    """
    if any(ch in value for ch in '<>"{}|^`\\ ') or not value.startswith(("http://", "https://")):
        raise ValueError(f"keine zulaessige IRI: {value!r}")
    return f"<{value}>"
