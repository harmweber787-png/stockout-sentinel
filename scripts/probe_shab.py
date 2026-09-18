"""Feld-Erhebung auf der Amtsblattportal-API (SHAB, Handelsregister-Rubrik HR).

Wie ``probe_lindas.py`` und ``probe_zefix.py`` ist dieses Skript das
Messinstrument **vor** dem Adapter ``kmu_discovery/sources/shab.py``: es liest
die Felder dort ab, wo sie stehen, und schreibt eine Feldtabelle. Kein
Feldname aus dem Gedaechtnis.

Quelle: ``https://amtsblattportal.ch/api/v1`` - die dokumentierte, offene
REST-API des Amtsblattportals (Doku: ``/docs/api/``, "The API is freely
accessible for anyone to use", keine Zugangsdaten fuer publizierte Meldungen).
Die robots.txt des Portals sperrt Crawler von der HTML-Oberflaeche aus; die
API ist der von der Betreiberin vorgesehene Maschinenzugang und wird hier
ausschliesslich benutzt.

Drei Ebenen werden erhoben:

  1. **Liste** (JSON, ``/publications``): je Unterrubrik HR01 (Neueintragung),
     HR02 (Mutation), HR03 (Loeschung) eine Seite mit ``pageRequest.size``
     als Limit, fuer die Kantone ZH, AG, ZG und die letzten ``--days`` Tage.
     Daraus: Gesamtzahl (``total``) und Abdeckung der Meta-Felder.
  2. **Einzelpublikation** (XML, ``/publications/{id}/xml``): hoechstens
     ``--max-xml`` je Unterrubrik. Daraus: Abdeckung der Inhaltsfelder als
     Elementpfade, Beispielwerte.
  3. **Schema** (XSD, ``/schemas/shab/{version}/HRxx-export.xsd``): alle
     Elemente mit Kardinalitaet und Dokumentation - die Feldliste, die auch
     ohne Stichprobe gilt.

Rechtlicher Rahmen: nur lesende Abfragen, identifizierender User-Agent,
Mindestpause zwischen Anfragen, Limit auf jeder Listenanfrage, ``Retry-After``
wird beachtet. Publikationstexte enthalten Personendaten (Zeichnungs-
berechtigte); das Skript speichert davon nur gekuerzte Beispielwerte.

Beispielaufruf:

    python scripts/probe_shab.py --dry-run
    python scripts/probe_shab.py --json-out shab_felder.json
"""

from __future__ import annotations

import argparse
import json
import random
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, TypeAlias

#: JSON-Wert, wie ``json.loads`` ihn liefert.
JsonValue: TypeAlias = "dict[str, JsonValue] | list[JsonValue] | str | int | float | bool | None"

DEFAULT_BASE_URL = "https://amtsblattportal.ch/api/v1"
DEFAULT_USER_AGENT = (
    "kmu-discovery-probe/0.1 (Abklaerung Datenquelle, wenige Abfragen; Kontakt siehe Repository)"
)
#: Mindestpause zwischen zwei Anfragen. Die API dokumentiert kein Limit,
#: deshalb defensiv.
MIN_INTERVAL_SECONDS = 1.0
MAX_ATTEMPTS = 4
DEFAULT_CANTONS = ("ZH", "AG", "ZG")
DEFAULT_SUB_RUBRICS = ("HR01", "HR02", "HR03")
#: Schemaversion laut ``xsi:schemaLocation`` einer live gelesenen Publikation.
DEFAULT_SCHEMA_VERSION = "1.26"
XS = "{http://www.w3.org/2001/XMLSchema}"


class ProbeError(RuntimeError):
    """Der Endpunkt war nicht erreichbar oder hat einen Fehler geliefert."""


@dataclass(frozen=True, slots=True)
class SchemaElement:
    """Ein Element aus der XSD, mit Pfad, Kardinalitaet und Dokumentation."""

    sub_rubric: str
    path: str
    type: str
    min_occurs: str
    max_occurs: str
    documentation: str


@dataclass(frozen=True, slots=True)
class CoverageRow:
    """Live gemessene Abdeckung eines Feldes ueber die Stichprobe."""

    scope: str
    path: str
    present: int
    total: int
    sample_value: str

    @property
    def coverage(self) -> float:
        """Anteil der Stichprobe mit gesetztem Wert."""
        return round(self.present / self.total, 3) if self.total else 0.0


@dataclass
class ProbeResult:
    """Gesamtergebnis der Erhebung."""

    base_url: str
    cantons: tuple[str, ...]
    sub_rubrics: tuple[str, ...]
    date_start: str
    date_end: str
    schema_version: str
    totals: dict[str, int] = field(default_factory=dict)
    totals_per_canton: dict[str, dict[str, int]] = field(default_factory=dict)
    meta_coverage: list[CoverageRow] = field(default_factory=list)
    content_coverage: list[CoverageRow] = field(default_factory=list)
    schema_elements: list[SchemaElement] = field(default_factory=list)
    sample_ids: dict[str, list[str]] = field(default_factory=dict)
    requests: list[str] = field(default_factory=list)


# -- HTTP ----------------------------------------------------------------- #


class RestClient:
    """Minimaler, hoeflicher HTTP-Client fuer die Erhebung (nur GET).

    Ohne Fremdbibliothek, damit das Skript ohne Setup laeuft. Der
    Produktivclient in ``kmu_discovery/sources/`` bekommt Token-Bucket, Cache
    und typisierte Fehler - hier genuegen Mindestpause und Backoff.
    """

    def __init__(
        self,
        base_url: str,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 60.0,
        min_interval: float = MIN_INTERVAL_SECONDS,
        ca_bundle: str | None = None,
        verbose: bool = False,
    ) -> None:
        """Merkt sich Basis-URL und Hoeflichkeitsparameter."""
        self._base_url = base_url.rstrip("/")
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

    def get_json(self, path: str, params: Sequence[tuple[str, str]] = ()) -> JsonValue:
        """GET mit Query-Parametern, Antwort als JSON."""
        raw = self._get(path, params, "application/json")
        parsed: JsonValue = json.loads(raw) if raw.strip() else None
        return parsed

    def get_text(self, path: str, accept: str = "application/xml") -> str:
        """GET ohne Parameter, Antwort als Text (XML, XSD)."""
        return self._get(path, (), accept)

    def _get(self, path: str, params: Sequence[tuple[str, str]], accept: str) -> str:
        self._wait()
        url = self._base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(list(params))
        request = urllib.request.Request(
            url, headers={"Accept": accept, "User-Agent": self._user_agent}, method="GET"
        )
        return self._read_with_retry(request)

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if self._last_call and elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call = time.monotonic()

    def _read_with_retry(self, request: urllib.request.Request) -> str:
        last: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(
                    request, timeout=self._timeout, context=self._context
                ) as response:
                    return str(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                last = error
                if error.code in {429, 502, 503, 504}:
                    self._sleep_before_retry(attempt, error.headers.get("Retry-After"))
                    continue
                body = error.read().decode("utf-8", errors="replace")[:400]
                raise ProbeError(f"HTTP {error.code} fuer {request.full_url}: {body}") from error
            except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
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


# -- Anfragen ------------------------------------------------------------- #


def list_params(
    sub_rubric: str,
    cantons: Sequence[str],
    date_start: str,
    date_end: str,
    size: int,
    page: int = 0,
) -> list[tuple[str, str]]:
    """Query-Parameter einer Listenanfrage laut API-Doku (Kapitel 3.4).

    ``rubrics=HR`` wird bewusst **nicht** mitgegeben: Rubrik und Unterrubrik
    werden von der API mit ODER verknuepft, ``rubrics=HR&subRubrics=HR01``
    liefert also alle HR-Meldungen.

    >>> list_params("HR01", ["ZH", "ZG"], "2026-06-19", "2026-09-17", 5)[:3]
    [('publicationStates', 'PUBLISHED'), ('subRubrics', 'HR01'), ('cantons', 'ZH')]
    """
    params = [("publicationStates", "PUBLISHED"), ("subRubrics", sub_rubric)]
    params += [("cantons", canton) for canton in cantons]
    params += [
        ("publicationDate.start", date_start),
        ("publicationDate.end", date_end),
        ("pageRequest.size", str(size)),
        ("pageRequest.page", str(page)),
        ("pageRequest.sortOrders", "column:PUBLICATION_DATE|direction:DESC"),
    ]
    return params


# -- Abdeckung ------------------------------------------------------------ #


def _is_set(value: object) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def flatten_json(value: JsonValue, prefix: str = "") -> dict[str, JsonValue]:
    """Faltet ein JSON-Objekt zu Pfad -> Blattwert (Listen als ``[]``).

    >>> flatten_json({"a": {"b": 1}, "c": [{"d": 2}], "e": []})
    {'a.b': 1, 'c[].d': 2, 'e': []}
    """
    out: dict[str, JsonValue] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            out.update(flatten_json(item, f"{prefix}.{key}" if prefix else key))
    elif isinstance(value, list):
        if not value:
            out[prefix] = []
        for item in value:
            for path, leaf in flatten_json(item, prefix + "[]").items():
                out.setdefault(path, leaf)
    else:
        out[prefix] = value
    return out


def flatten_xml(root: ET.Element) -> dict[str, str]:
    """Faltet ein XML-Dokument zu Elementpfad -> Text der Blattelemente.

    Namensraeume werden entfernt; der erste Wert je Pfad bleibt stehen.
    """
    out: dict[str, str] = {}

    def walk(element: ET.Element, prefix: str) -> None:
        tag = element.tag.rsplit("}", 1)[-1]
        path = f"{prefix}/{tag}" if prefix else tag
        children = list(element)
        if children:
            for child in children:
                walk(child, path)
        else:
            out.setdefault(path, (element.text or "").strip())

    for child in root:
        walk(child, "")
    return out


def measure_coverage(scope: str, samples: Sequence[Mapping[str, object]]) -> list[CoverageRow]:
    """Abdeckung je Pfad ueber gleichartige, bereits gefaltete Stichproben."""
    paths: list[str] = []
    for sample in samples:
        for path in sample:
            if path not in paths:
                paths.append(path)
    rows: list[CoverageRow] = []
    for path in paths:
        present = 0
        sample_value = ""
        for sample in samples:
            value = sample.get(path)
            if not _is_set(value):
                continue
            present += 1
            if not sample_value:
                sample_value = str(value)[:80]
        rows.append(CoverageRow(scope, path, present, len(samples), sample_value))
    return rows


# -- XSD ------------------------------------------------------------------ #


_TAG_RE = re.compile(r"<[^>]+>")


def _documentation(element: ET.Element) -> str:
    """Erste Dokumentationsfassung eines Elements, ohne HTML-Auszeichnung.

    Die Schemas betten HTML in ``xs:documentation`` ein und wiederholen den
    Text je Sprache; hier zaehlt die erste Fassung.

    >>> import xml.etree.ElementTree as ET
    >>> xsd = ('<xs:element xmlns:xs="http://www.w3.org/2001/XMLSchema">'
    ...        '<xs:annotation><xs:documentation>&lt;div&gt;Hallo  Welt&lt;/div&gt;'
    ...        '</xs:documentation></xs:annotation></xs:element>')
    >>> _documentation(ET.fromstring(xsd))
    'Hallo Welt'
    """
    for doc in element.findall(f"{XS}annotation/{XS}documentation"):
        text = _TAG_RE.sub(" ", " ".join(doc.itertext()))
        cleaned = " ".join(text.split())
        if cleaned:
            return cleaned[:220]
    return ""


def schema_elements(sub_rubric: str, xsd: str) -> list[SchemaElement]:
    """Alle Elemente unter ``contentType`` mit Pfad, Typ, Kardinalitaet, Doku.

    Die HR-Schemas definieren die Struktur ueber benannte ``complexType``s
    (``publicationType`` -> ``metaType`` / ``contentType``); Elemente verweisen
    per ``type``-Attribut darauf. Der Baum wird deshalb entlang dieser
    Verweise aufgeloest. Nur die Inhaltsstruktur wird gelistet; die
    Meta-Struktur ist in allen Unterrubriken gleich und in der API-Doku
    (Kapitel 4.3) beschrieben.
    """
    root = ET.fromstring(xsd)
    types = {t.get("name", ""): t for t in root.findall(f"{XS}complexType")}
    rows: list[SchemaElement] = []

    def walk(node: ET.Element, prefix: str, seen: frozenset[str]) -> None:
        for child in node:
            if child.tag == f"{XS}element":
                name = child.get("name", "")
                path = f"{prefix}/{name}" if prefix else name
                type_name = child.get("type", "")
                rows.append(
                    SchemaElement(
                        sub_rubric=sub_rubric,
                        path=path,
                        type=type_name or "complex",
                        min_occurs=child.get("minOccurs", "1"),
                        max_occurs=child.get("maxOccurs", "1"),
                        documentation=_documentation(child),
                    )
                )
                walk(child, path, seen)
                target = types.get(type_name)
                if target is not None and type_name not in seen:
                    walk(target, path, seen | {type_name})
            elif child.tag in {
                f"{XS}complexType",
                f"{XS}sequence",
                f"{XS}choice",
                f"{XS}all",
                f"{XS}complexContent",
                f"{XS}extension",
            }:
                walk(child, prefix, seen)

    content = types.get("contentType")
    if content is None:
        return []
    walk(content, "", frozenset({"contentType"}))
    return rows


# -- Erhebung ------------------------------------------------------------- #


def probe(
    client: RestClient,
    cantons: Sequence[str],
    sub_rubrics: Sequence[str],
    days: int,
    sample_size: int,
    max_xml: int,
    schema_version: str,
    today: date,
    verbose: bool,
) -> ProbeResult:
    """Erhebung in drei Ebenen: Liste, Einzelpublikationen, Schema."""
    date_end = today.isoformat()
    date_start = (today - timedelta(days=days)).isoformat()
    result = ProbeResult(
        base_url=client.base_url,
        cantons=tuple(cantons),
        sub_rubrics=tuple(sub_rubrics),
        date_start=date_start,
        date_end=date_end,
        schema_version=schema_version,
    )

    for index, sub_rubric in enumerate(sub_rubrics, start=1):
        if verbose:
            print(f"{index}/{len(sub_rubrics)} Liste {sub_rubric} ...", file=sys.stderr)
        params = list_params(sub_rubric, cantons, date_start, date_end, sample_size)
        result.requests.append("GET /publications?" + urllib.parse.urlencode(params))
        payload = client.get_json("/publications", params)
        if not isinstance(payload, dict):
            raise ProbeError(f"Liste {sub_rubric}: Antwort ist kein JSON-Objekt")
        total = payload.get("total")
        result.totals[sub_rubric] = int(total) if isinstance(total, int) else 0
        content = payload.get("content")
        entries = [e for e in content if isinstance(e, dict)] if isinstance(content, list) else []
        result.meta_coverage += measure_coverage(
            sub_rubric, [flatten_json(entry) for entry in entries]
        )

        per_canton: dict[str, int] = {}
        for canton in cantons:
            params = list_params(sub_rubric, [canton], date_start, date_end, 1)
            result.requests.append("GET /publications?" + urllib.parse.urlencode(params))
            single = client.get_json("/publications", params)
            count = single.get("total") if isinstance(single, dict) else None
            per_canton[canton] = int(count) if isinstance(count, int) else 0
        result.totals_per_canton[sub_rubric] = per_canton

        ids: list[str] = []
        for entry in entries:
            meta = entry.get("meta")
            if not isinstance(meta, dict):
                continue
            pub_id = meta.get("id")
            if isinstance(pub_id, str):
                ids.append(pub_id)
        ids = ids[:max_xml]
        result.sample_ids[sub_rubric] = ids
        documents: list[dict[str, str]] = []
        for pub_id in ids:
            result.requests.append(f"GET /publications/{pub_id}/xml")
            xml_text = client.get_text(f"/publications/{pub_id}/xml")
            documents.append(flatten_xml(ET.fromstring(xml_text)))
        result.content_coverage += measure_coverage(sub_rubric, documents)

        path = f"/schemas/shab/{schema_version}/{sub_rubric}-export.xsd"
        result.requests.append(f"GET {path}")
        result.schema_elements += schema_elements(sub_rubric, client.get_text(path))
    return result


# -- Ausgabe -------------------------------------------------------------- #


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(c.replace("|", "\\|") for c in row) + " |" for row in rows]
    return lines


def render_markdown(result: ProbeResult) -> str:
    """Rendert die Feldtabelle als Markdown."""
    lines = [
        "# SHAB-Feldtabelle (live erhoben, Amtsblattportal-API)",
        "",
        f"* Basis-URL: `{result.base_url}`",
        f"* Kantone: {', '.join(result.cantons)}",
        f"* Zeitraum: {result.date_start} bis {result.date_end}",
        f"* Schemaversion: `{result.schema_version}`",
        "",
        "## Anzahl Publikationen im Zeitraum",
        "",
    ]
    header = ["Unterrubrik", *result.cantons, "alle drei"]
    rows = [
        [sr, *[str(result.totals_per_canton.get(sr, {}).get(c, 0)) for c in result.cantons],
         str(result.totals.get(sr, 0))]
        for sr in result.sub_rubrics
    ]  # fmt: skip
    lines += _table(header, rows)
    lines += ["", "## Meta-Felder der Liste (JSON)", ""]
    for sr in result.sub_rubrics:
        lines += [f"### {sr}", ""]
        lines += _table(
            ["Pfad", "Abdeckung", "Beispielwert"],
            [
                [f"`{r.path}`", f"{r.coverage:.0%}", r.sample_value]
                for r in result.meta_coverage
                if r.scope == sr
            ],
        )
        lines.append("")
    lines += ["## Inhaltsfelder der Einzelpublikation (XML)", ""]
    for sr in result.sub_rubrics:
        ids = result.sample_ids.get(sr, [])
        lines += [f"### {sr} ({len(ids)} Stichproben)", ""]
        lines += _table(
            ["Pfad", "Abdeckung", "Beispielwert"],
            [
                [f"`{r.path}`", f"{r.coverage:.0%}", r.sample_value]
                for r in result.content_coverage
                if r.scope == sr
            ],
        )
        lines.append("")
    lines += ["## Schema (XSD): Inhaltsstruktur je Unterrubrik", ""]
    for sr in result.sub_rubrics:
        lines += [f"### {sr}", ""]
        lines += _table(
            ["Pfad", "Typ", "min", "max", "Dokumentation"],
            [
                [f"`{e.path}`", e.type, e.min_occurs, e.max_occurs, e.documentation]
                for e in result.schema_elements
                if e.sub_rubric == sr
            ],
        )
        lines.append("")
    return "\n".join(lines)


def _as_payload(result: ProbeResult) -> dict[str, Any]:
    def cov(row: CoverageRow) -> dict[str, Any]:
        return {
            "scope": row.scope,
            "path": row.path,
            "present": row.present,
            "total": row.total,
            "coverage": row.coverage,
            "sample_value": row.sample_value,
        }

    return {
        "base_url": result.base_url,
        "cantons": list(result.cantons),
        "sub_rubrics": list(result.sub_rubrics),
        "date_start": result.date_start,
        "date_end": result.date_end,
        "schema_version": result.schema_version,
        "totals": result.totals,
        "totals_per_canton": result.totals_per_canton,
        "meta_coverage": [cov(r) for r in result.meta_coverage],
        "content_coverage": [cov(r) for r in result.content_coverage],
        "schema_elements": [
            {
                "sub_rubric": e.sub_rubric,
                "path": e.path,
                "type": e.type,
                "min_occurs": e.min_occurs,
                "max_occurs": e.max_occurs,
                "documentation": e.documentation,
            }
            for e in result.schema_elements
        ],
        "sample_ids": result.sample_ids,
        "requests": result.requests,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Erhebt die Feldtabelle oder zeigt im Trockenlauf nur die Anfragen."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--canton", action="append", default=None, help="Kanton (mehrfach).")
    parser.add_argument(
        "--sub-rubric", action="append", default=None, help="Unterrubrik HR01/HR02/HR03 (mehrfach)."
    )
    parser.add_argument("--days", type=int, default=90, help="Zeitraum rueckwaerts in Tagen.")
    parser.add_argument("--sample-size", type=int, default=10, help="Listeneintraege je Rubrik.")
    parser.add_argument("--max-xml", type=int, default=3, help="Einzelabrufe je Rubrik.")
    parser.add_argument("--schema-version", default=DEFAULT_SCHEMA_VERSION)
    parser.add_argument("--today", default=None, help="Stichtag JJJJ-MM-TT (Standard: heute).")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--min-interval", type=float, default=MIN_INTERVAL_SECONDS)
    parser.add_argument("--ca-bundle", default=None, help="Eigenes CA-Bundle (Proxy-Umgebung).")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Nur die Anfragen zeigen.")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    cantons = tuple(args.canton) if args.canton else DEFAULT_CANTONS
    sub_rubrics = tuple(args.sub_rubric) if args.sub_rubric else DEFAULT_SUB_RUBRICS
    today = date.fromisoformat(args.today) if args.today else datetime.now(UTC).date()
    if not 1 <= args.sample_size <= 100 or not 0 <= args.max_xml <= 10 or args.days < 1:
        print("sample-size 1..100, max-xml 0..10, days >= 1 erwartet", file=sys.stderr)
        return 2

    if args.dry_run:
        date_end = today.isoformat()
        date_start = (today - timedelta(days=args.days)).isoformat()
        print("# Trockenlauf - diese Anfragen wuerden gestellt (keine Netzwerkzugriffe)\n")
        print(f"# Basis-URL: {args.base_url}")
        print(f"# Mindestpause: {args.min_interval}s, User-Agent: {args.user_agent}\n")
        for sub_rubric in sub_rubrics:
            params = list_params(sub_rubric, cantons, date_start, date_end, args.sample_size)
            print(f"GET /publications?{urllib.parse.urlencode(params)}")
            for canton in cantons:
                params = list_params(sub_rubric, [canton], date_start, date_end, 1)
                print(f"GET /publications?{urllib.parse.urlencode(params)}")
            print(f"GET /publications/<id>/xml   (hoechstens {args.max_xml}x, IDs aus der Liste)")
            print(f"GET /schemas/shab/{args.schema_version}/{sub_rubric}-export.xsd")
        return 0

    client = RestClient(
        base_url=args.base_url,
        user_agent=args.user_agent,
        timeout=args.timeout,
        min_interval=args.min_interval,
        ca_bundle=args.ca_bundle,
        verbose=not args.quiet,
    )
    try:
        result = probe(
            client,
            cantons=cantons,
            sub_rubrics=sub_rubrics,
            days=args.days,
            sample_size=args.sample_size,
            max_xml=args.max_xml,
            schema_version=args.schema_version,
            today=today,
            verbose=not args.quiet,
        )
    except (ProbeError, ET.ParseError) as error:
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
