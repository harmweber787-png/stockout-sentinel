"""Feld-Erhebung auf der Zefix Public REST API des Bundes.

Wie ``probe_lindas.py`` ist dieses Skript das Messinstrument **vor** dem
Adapter ``kmu_discovery/sources/zefix.py``: es liest die Felder der API dort
ab, wo sie stehen, und schreibt eine Feldtabelle. Kein Feldname aus dem
Gedaechtnis.

Die API (``https://www.zefix.admin.ch/ZefixPublicREST``) hat zwei Ebenen:

  1. Die OpenAPI-Beschreibung ``/v3/api-docs`` ist ohne Login lesbar. Aus ihr
     stammen Endpunkte, Schemas, Typen, Pflichtfelder und Enums.
  2. Die Daten selbst verlangen Basic-Auth (Zugang beim Eidgenoessischen Amt
     fuer das Handelsregister). Mit Zugangsdaten (``ZEFIX_USER`` /
     ``ZEFIX_PASSWORD`` in der Umgebung) misst das Skript zusaetzlich die
     Abdeckung je Feld an wenigen Stichproben: Rechtsformliste, einzelne
     Betriebe per UID, eine eng gefasste Namenssuche. Ohne Zugangsdaten
     weist die Tabelle die Abdeckung als "nicht gemessen" aus.

Die interne Web-API des Zefix-Portals (``/ZefixREST``) antwortet zwar ohne
Login, steht aber unter ``Disallow: /`` in der robots.txt des Portals und ist
nicht die dokumentierte Schnittstelle. Sie wird hier bewusst nicht benutzt.

Die Suche kennt keinen serverseitigen Limit-Parameter (die API bricht grosse
Ergebnislisten mit ``RESULTLIST_TO_LARGE`` ab). Das Skript begrenzt deshalb
selbst: eng gefasste Suchbegriffe, hoechstens ``--max-results`` gelesene
Treffer, hoechstens ``--max-companies`` Detailabrufe, Mindestpause zwischen
den Anfragen, identifizierender User-Agent, ``Retry-After`` wird beachtet.

Beispielaufruf:

    # Zeigt nur die Anfragen, ohne Netz - zum Gegenlesen
    python scripts/probe_zefix.py --dry-run

    # Echte Erhebung, Tabelle nach stdout, Rohdaten als JSON
    python scripts/probe_zefix.py --json-out zefix_felder.json
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import ssl
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeAlias

#: JSON-Wert, wie ``json.loads`` ihn liefert.
JsonValue: TypeAlias = "dict[str, JsonValue] | list[JsonValue] | str | int | float | bool | None"

DEFAULT_BASE_URL = "https://www.zefix.admin.ch/ZefixPublicREST"
SPEC_PATH = "/v3/api-docs"
DEFAULT_USER_AGENT = (
    "kmu-discovery-probe/0.1 (Abklaerung Datenquelle, wenige Abfragen; Kontakt siehe Repository)"
)
#: Mindestpause zwischen zwei Anfragen. Zefix dokumentiert kein Limit,
#: deshalb defensiv.
MIN_INTERVAL_SECONDS = 1.0
MAX_ATTEMPTS = 4
#: UIDs fuer die Stichprobe: aus der LINDAS-Erhebung vom 2026-09-16 bekannt.
DEFAULT_UIDS = ("CHE-242.294.601", "CHE-101.456.260")
#: Schemas, deren Felder in die Tabelle kommen (Reihenfolge = Ausgabe).
SCHEMAS_OF_INTEREST = (
    "CompanyFull",
    "CompanyShort",
    "Address",
    "LegalForm",
    "SogcPublication",
    "MutationType",
    "CompanyOldName",
    "CompanySearchQuery",
    "ErrorDetails",
)


class ProbeError(RuntimeError):
    """Der Endpunkt war nicht erreichbar oder hat einen Fehler geliefert."""


class AuthRequiredError(ProbeError):
    """Die Daten-Endpunkte verlangen Zugangsdaten (HTTP 401/403)."""


@dataclass(frozen=True, slots=True)
class SpecField:
    """Ein Feld aus der OpenAPI-Beschreibung."""

    schema: str
    name: str
    type: str
    format: str | None
    required: bool
    enum: tuple[str, ...]
    description: str


@dataclass(frozen=True, slots=True)
class CoverageRow:
    """Live gemessene Abdeckung eines Feldes ueber die Stichprobe."""

    schema: str
    name: str
    present: int
    total: int
    max_items: int
    sample_value: str

    @property
    def coverage(self) -> float:
        """Anteil der Stichprobe mit gesetztem Wert."""
        return round(self.present / self.total, 3) if self.total else 0.0


@dataclass
class ProbeResult:
    """Gesamtergebnis der Erhebung."""

    base_url: str
    api_version: str = ""
    auth_scheme: str = ""
    endpoints: list[tuple[str, str, str]] = field(default_factory=list)
    spec_fields: list[SpecField] = field(default_factory=list)
    credentials_used: bool = False
    auth_note: str = ""
    legal_forms: list[dict[str, Any]] = field(default_factory=list)
    coverage: list[CoverageRow] = field(default_factory=list)
    sample_count: int = 0
    search_hits: int = 0
    requests: list[str] = field(default_factory=list)


# -- HTTP ----------------------------------------------------------------- #


class RestClient:
    """Minimaler, hoeflicher REST-Client fuer die Erhebung.

    Ohne Fremdbibliothek, damit das Skript ohne Setup laeuft. Der
    Produktivclient in ``kmu_discovery/sources/`` bekommt Token-Bucket, Cache
    und typisierte Fehler - hier genuegen Mindestpause und Backoff.
    """

    def __init__(
        self,
        base_url: str,
        credentials: tuple[str, str] | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 30.0,
        min_interval: float = MIN_INTERVAL_SECONDS,
        ca_bundle: str | None = None,
        verbose: bool = False,
    ) -> None:
        """Merkt sich Basis-URL, Zugangsdaten und Hoeflichkeitsparameter."""
        self._base_url = base_url.rstrip("/")
        self._credentials = credentials
        self._user_agent = user_agent
        self._timeout = timeout
        self._min_interval = min_interval
        self._verbose = verbose
        self._last_call = 0.0
        self._context = ssl.create_default_context(cafile=ca_bundle) if ca_bundle else None

    @property
    def base_url(self) -> str:
        """Basis-URL ohne abschliessenden Schraegstrich."""
        return self._base_url

    def get(self, path: str) -> JsonValue:
        """GET auf ``path`` (relativ zur Basis-URL), Antwort als JSON."""
        return self._call("GET", path, None)

    def post(self, path: str, payload: Mapping[str, Any]) -> JsonValue:
        """POST mit JSON-Body, Antwort als JSON."""
        return self._call("POST", path, payload)

    def _call(self, method: str, path: str, payload: Mapping[str, Any] | None) -> JsonValue:
        self._wait()
        headers = {"Accept": "application/json", "User-Agent": self._user_agent}
        data: bytes | None = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self._credentials is not None:
            token = base64.b64encode(":".join(self._credentials).encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        request = urllib.request.Request(
            self._base_url + path, data=data, headers=headers, method=method
        )
        return self._read_with_retry(request)

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if self._last_call and elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call = time.monotonic()

    def _read_with_retry(self, request: urllib.request.Request) -> JsonValue:
        last: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(
                    request, timeout=self._timeout, context=self._context
                ) as response:
                    raw = response.read().decode("utf-8")
                parsed: JsonValue = json.loads(raw) if raw.strip() else None
                return parsed
            except urllib.error.HTTPError as error:
                last = error
                if error.code in {401, 403}:
                    raise AuthRequiredError(
                        f"HTTP {error.code} fuer {request.full_url}: Zugangsdaten noetig"
                    ) from error
                if error.code in {429, 502, 503, 504}:
                    self._sleep_before_retry(attempt, error.headers.get("Retry-After"))
                    continue
                body = error.read().decode("utf-8", errors="replace")[:400]
                raise ProbeError(f"HTTP {error.code} fuer {request.full_url}: {body}") from error
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
                last = error
                self._sleep_before_retry(attempt, None)
        raise ProbeError(f"Endpunkt nach {MAX_ATTEMPTS} Versuchen nicht erreichbar: {last}")

    def _sleep_before_retry(self, attempt: int, retry_after: str | None) -> None:
        if attempt >= MAX_ATTEMPTS:
            return
        delay = float(retry_after) if retry_after and retry_after.isdigit() else 2.0**attempt
        delay += random.uniform(0, 0.5)
        if self._verbose:
            print(f"  ... erneuter Versuch in {delay:.1f}s", file=sys.stderr)
        time.sleep(delay)


# -- OpenAPI auswerten ---------------------------------------------------- #


def _type_label(prop: Mapping[str, Any]) -> str:
    """Kurzbezeichnung eines Schema-Typs (``string``, ``array<LegalForm>``, ...)."""
    if "$ref" in prop:
        return str(prop["$ref"]).rsplit("/", 1)[-1]
    kind = str(prop.get("type", "object"))
    if kind == "array":
        return f"array<{_type_label(prop.get('items', {}))}>"
    return kind


def spec_fields(spec: Mapping[str, Any], schemas: Sequence[str]) -> list[SpecField]:
    """Liest die Felder der genannten Schemas aus der OpenAPI-Beschreibung."""
    components = spec.get("components", {}).get("schemas", {})
    rows: list[SpecField] = []
    for schema_name in schemas:
        schema = components.get(schema_name)
        if not isinstance(schema, dict):
            continue
        required = set(schema.get("required", []))
        for name, prop in schema.get("properties", {}).items():
            rows.append(
                SpecField(
                    schema=schema_name,
                    name=name,
                    type=_type_label(prop),
                    format=prop.get("format"),
                    required=name in required,
                    enum=tuple(str(v) for v in prop.get("enum", [])),
                    description=str(prop.get("description", "")).strip(),
                )
            )
    return rows


def spec_endpoints(spec: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """(Methode, Pfad, Antworttyp) je Operation."""
    rows: list[tuple[str, str, str]] = []
    for path, operations in spec.get("paths", {}).items():
        for method, operation in operations.items():
            content: Mapping[str, Any] = (
                operation.get("responses", {}).get("200", {}).get("content", {})
            )
            first: Mapping[str, Any] = next(iter(content.values()), {})
            schema: Mapping[str, Any] = first.get("schema", {})
            rows.append((method.upper(), path, _type_label(schema) if schema else "-"))
    return rows


# -- Abdeckung messen ----------------------------------------------------- #


def _is_set(value: object) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def measure_coverage(
    samples: Sequence[Mapping[str, Any]], fields: Sequence[SpecField], schema: str
) -> list[CoverageRow]:
    """Abdeckung der Felder eines Schemas ueber eine Liste gleichartiger Objekte."""
    rows: list[CoverageRow] = []
    for spec_field in fields:
        if spec_field.schema != schema:
            continue
        present = 0
        max_items = 0
        sample_value = ""
        for sample in samples:
            value = sample.get(spec_field.name)
            if not _is_set(value):
                continue
            present += 1
            if isinstance(value, list):
                max_items = max(max_items, len(value))
            if not sample_value:
                sample_value = json.dumps(value, ensure_ascii=False)[:80]
        rows.append(
            CoverageRow(
                schema=schema,
                name=spec_field.name,
                present=present,
                total=len(samples),
                max_items=max_items,
                sample_value=sample_value,
            )
        )
    return rows


def _nested(samples: Sequence[Mapping[str, Any]], key: str) -> list[Mapping[str, Any]]:
    """Sammelt verschachtelte Objekte (einzeln oder als Liste) unter ``key``."""
    out: list[Mapping[str, Any]] = []
    for sample in samples:
        value = sample.get(key)
        if isinstance(value, dict):
            out.append(value)
        elif isinstance(value, list):
            out.extend(item for item in value if isinstance(item, dict))
    return out


# -- Erhebung ------------------------------------------------------------- #


def probe(
    client: RestClient,
    uids: Sequence[str],
    search_name: str,
    search_canton: str | None,
    max_results: int,
    max_companies: int,
    verbose: bool,
) -> ProbeResult:
    """Erhebung: Spezifikation immer, Stichproben nur mit Zugangsdaten."""
    result = ProbeResult(base_url=client.base_url)

    if verbose:
        print("1/3 OpenAPI-Beschreibung ...", file=sys.stderr)
    result.requests.append(f"GET {SPEC_PATH}")
    raw_spec = client.get(SPEC_PATH)
    if not isinstance(raw_spec, dict):
        raise ProbeError("OpenAPI-Beschreibung ist kein JSON-Objekt")
    spec: dict[str, Any] = raw_spec
    result.api_version = str(spec.get("info", {}).get("version", ""))
    schemes: dict[str, Any] = spec.get("components", {}).get("securitySchemes", {})
    result.auth_scheme = ", ".join(
        f"{name}: {value.get('type')}/{value.get('scheme')}" for name, value in schemes.items()
    )
    result.endpoints = spec_endpoints(spec)
    result.spec_fields = spec_fields(spec, SCHEMAS_OF_INTEREST)

    if verbose:
        print("2/3 Rechtsformen und Stichproben ...", file=sys.stderr)
    companies: list[Mapping[str, Any]] = []
    shorts: list[Mapping[str, Any]] = []
    try:
        result.requests.append("GET /api/v1/legalForm")
        forms = client.get("/api/v1/legalForm")
        if isinstance(forms, list):
            result.legal_forms = [f for f in forms if isinstance(f, dict)]
        for uid in list(uids)[:max_companies]:
            compact = uid.replace("-", "").replace(".", "")
            result.requests.append(f"GET /api/v1/company/uid/{compact}")
            payload = client.get(f"/api/v1/company/uid/{compact}")
            if isinstance(payload, list):
                companies.extend(item for item in payload if isinstance(item, dict))
        query: dict[str, Any] = {"name": search_name, "activeOnly": True}
        if search_canton:
            query["canton"] = search_canton
        result.requests.append(f"POST /api/v1/company/search {json.dumps(query)}")
        hits = client.post("/api/v1/company/search", query)
        if isinstance(hits, list):
            result.search_hits = len(hits)
            shorts = [item for item in hits[:max_results] if isinstance(item, dict)]
        result.credentials_used = True
    except AuthRequiredError as error:
        result.auth_note = str(error)
        if verbose:
            print(f"  Stichproben uebersprungen: {error}", file=sys.stderr)

    if verbose:
        print("3/3 Abdeckung ...", file=sys.stderr)
    result.sample_count = len(companies)
    if companies:
        fields = result.spec_fields
        result.coverage += measure_coverage(companies, fields, "CompanyFull")
        result.coverage += measure_coverage(_nested(companies, "address"), fields, "Address")
        result.coverage += measure_coverage(_nested(companies, "legalForm"), fields, "LegalForm")
        result.coverage += measure_coverage(
            _nested(companies, "sogcPub"), fields, "SogcPublication"
        )
        result.coverage += measure_coverage(
            _nested(_nested(companies, "sogcPub"), "mutationTypes"), fields, "MutationType"
        )
        result.coverage += measure_coverage(
            _nested(companies, "oldNames"), fields, "CompanyOldName"
        )
    if shorts:
        result.coverage += measure_coverage(shorts, result.spec_fields, "CompanyShort")
    return result


# -- Ausgabe -------------------------------------------------------------- #


def render_markdown(result: ProbeResult) -> str:
    """Rendert die Feldtabelle als Markdown."""
    lines = [
        "# Zefix-Feldtabelle (live erhoben)",
        "",
        f"* Basis-URL: `{result.base_url}`",
        f"* API-Version laut OpenAPI: `{result.api_version or '-'}`",
        f"* Authentifizierung: {result.auth_scheme or '-'}",
        f"* Zugangsdaten verwendet: {'ja' if result.credentials_used else 'nein'}",
    ]
    if result.auth_note:
        lines.append(f"* Hinweis: {result.auth_note}")
    lines += ["", "## Endpunkte", "", "| Methode | Pfad | Antwort |", "|---|---|---|"]
    lines += [f"| {m} | `{p}` | `{r}` |" for m, p, r in result.endpoints]
    lines += ["", "## Felder laut OpenAPI", ""]
    current = ""
    for row in result.spec_fields:
        if row.schema != current:
            current = row.schema
            lines += [
                f"### {current}",
                "",
                "| Feld | Typ | Format | Pflicht | Enum | Beschreibung |",
                "|---|---|---|---|---|---|",
            ]
        lines.append(
            f"| `{row.name}` | `{row.type}` | {row.format or '-'} | "
            f"{'ja' if row.required else 'nein'} | {', '.join(row.enum) or '-'} | "
            f"{row.description.replace('|', chr(92) + '|')} |"
        )
        if row is result.spec_fields[-1] or (
            result.spec_fields[result.spec_fields.index(row) + 1].schema != current
        ):
            lines.append("")
    lines += ["## Abdeckung in der Stichprobe", ""]
    if not result.coverage:
        lines.append(
            "Nicht gemessen: die Daten-Endpunkte verlangen Zugangsdaten. "
            "Mit `ZEFIX_USER`/`ZEFIX_PASSWORD` in der Umgebung misst das Skript "
            "die Abdeckung an Stichproben."
        )
    else:
        lines += [
            f"Stichprobe: {result.sample_count} Betriebe per UID, "
            f"{result.search_hits} Suchtreffer.",
            "",
            "| Schema | Feld | Abdeckung | max. Elemente | Beispielwert |",
            "|---|---|---:|---:|---|",
        ]
        for cov in result.coverage:
            items = str(cov.max_items) if cov.max_items else "-"
            lines.append(
                f"| {cov.schema} | `{cov.name}` | {cov.coverage:.0%} | {items} | "
                f"{cov.sample_value.replace('|', chr(92) + '|')} |"
            )
    if result.legal_forms:
        lines += ["", "## Rechtsformen", ""]
        lines += ["| id | eCH-0097 | Name (de) | Kurz |", "|---|---|---|---|"]
        for form in result.legal_forms:
            name = form.get("name", {}) if isinstance(form.get("name"), dict) else {}
            short = form.get("shortName", {}) if isinstance(form.get("shortName"), dict) else {}
            lines.append(
                f"| {form.get('id', '-')} | {form.get('uid', '-')} | "
                f"{name.get('de', '-')} | {short.get('de', '-')} |"
            )
    return "\n".join(lines)


def _as_payload(result: ProbeResult) -> dict[str, Any]:
    return {
        "base_url": result.base_url,
        "api_version": result.api_version,
        "auth_scheme": result.auth_scheme,
        "credentials_used": result.credentials_used,
        "auth_note": result.auth_note,
        "endpoints": [{"method": m, "path": p, "response": r} for m, p, r in result.endpoints],
        "spec_fields": [
            {
                "schema": f.schema,
                "name": f.name,
                "type": f.type,
                "format": f.format,
                "required": f.required,
                "enum": list(f.enum),
                "description": f.description,
            }
            for f in result.spec_fields
        ],
        "coverage": [
            {
                "schema": c.schema,
                "name": c.name,
                "present": c.present,
                "total": c.total,
                "coverage": c.coverage,
                "max_items": c.max_items,
                "sample_value": c.sample_value,
            }
            for c in result.coverage
        ],
        "sample_count": result.sample_count,
        "search_hits": result.search_hits,
        "legal_forms": result.legal_forms,
        "requests": result.requests,
    }


def _credentials_from_env(user_override: str | None) -> tuple[str, str] | None:
    user = user_override or os.environ.get("ZEFIX_USER")
    password = os.environ.get("ZEFIX_PASSWORD")
    if user and password:
        return (user, password)
    return None


def main(argv: Sequence[str] | None = None) -> int:
    """Erhebt die Feldtabelle oder zeigt im Trockenlauf nur die Anfragen."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--uid", action="append", default=None, help="UID fuer die Stichprobe (mehrfach)."
    )
    parser.add_argument("--search-name", default="Zazuko", help="Eng gefasster Suchbegriff.")
    parser.add_argument("--search-canton", default=None, help="Kanton fuer die Suche.")
    parser.add_argument("--max-results", type=int, default=5, help="Hoechstens gelesene Treffer.")
    parser.add_argument("--max-companies", type=int, default=3, help="Hoechstens Detailabrufe.")
    parser.add_argument("--user", default=None, help="Benutzer; Passwort nur via ZEFIX_PASSWORD.")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--min-interval", type=float, default=MIN_INTERVAL_SECONDS)
    parser.add_argument("--ca-bundle", default=None, help="Eigenes CA-Bundle (Proxy-Umgebung).")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Nur die Anfragen zeigen.")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    uids = tuple(args.uid) if args.uid else DEFAULT_UIDS
    credentials = _credentials_from_env(args.user)

    if args.dry_run:
        print("# Trockenlauf - diese Anfragen wuerden gestellt (keine Netzwerkzugriffe)\n")
        print(f"# Basis-URL: {args.base_url}")
        print(f"# Zugangsdaten vorhanden: {'ja' if credentials else 'nein'}")
        print(f"# Mindestpause: {args.min_interval}s, User-Agent: {args.user_agent}\n")
        print(f"1) GET {SPEC_PATH}   (ohne Login lesbar)")
        print("2) GET /api/v1/legalForm   (Basic-Auth)")
        for uid in uids[: args.max_companies]:
            compact = uid.replace("-", "").replace(".", "")
            print(f"3) GET /api/v1/company/uid/{compact}   (Basic-Auth)")
        query: dict[str, Any] = {"name": args.search_name, "activeOnly": True}
        if args.search_canton:
            query["canton"] = args.search_canton
        print(f"4) POST /api/v1/company/search {json.dumps(query)}   (Basic-Auth)")
        print(
            f"   -> hoechstens {args.max_results} Treffer gelesen; "
            "die API selbst kennt keinen Limit-Parameter."
        )
        return 0

    client = RestClient(
        base_url=args.base_url,
        credentials=credentials,
        user_agent=args.user_agent,
        timeout=args.timeout,
        min_interval=args.min_interval,
        ca_bundle=args.ca_bundle,
        verbose=not args.quiet,
    )
    try:
        result = probe(
            client,
            uids=uids,
            search_name=args.search_name,
            search_canton=args.search_canton,
            max_results=args.max_results,
            max_companies=args.max_companies,
            verbose=not args.quiet,
        )
    except ProbeError as error:
        print(f"Erhebung fehlgeschlagen: {error}", file=sys.stderr)
        return 1

    print(render_markdown(result))
    if args.json_out:
        args.json_out.write_text(
            json.dumps(_as_payload(result), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        if not args.quiet:
            print(f"\nRohdaten geschrieben: {args.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
