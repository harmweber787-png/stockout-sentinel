"""Feld-Erhebung auf dem LINDAS-SPARQL-Endpunkt.

Dieses Skript ist **kein Engine-Code**, sondern das Messinstrument davor: es
fragt den Endpunkt, welche Graphen, Klassen und Praedikate es dort wirklich
gibt, und schreibt daraus eine Feldtabelle. Erst auf deren Basis entsteht
``kmu_discovery/sources/lindas.py`` - keine Feldnamen aus dem Gedaechtnis.

Erhoben wird in vier Schritten:
  1. Welche benannten Graphen fuehren Zefix-Daten?
  2. Welche Klassen kommen im Zielgraphen vor, wie haeufig?
  3. Welche Praedikate haengen an der Zielklasse - mit Abdeckung, Beispielwert,
     Datentyp und maximaler Kardinalitaet je Subjekt?
  4. Ein vollstaendiges Beispielsubjekt als Plausibilitaetskontrolle.

Rechtlicher Rahmen: LINDAS ist ein offizieller Open-Data-Dienst des Bundes.
Das Skript stellt wenige, klar begrenzte Abfragen mit identifizierendem
User-Agent, haelt eine Mindestpause ein und respektiert ``Retry-After``. Es
laedt keine Personendaten in Bulk und schreibt nichts zurueck.

Beispielaufruf:

    # Zeigt nur die Abfragen, ohne Netz - zum Gegenlesen
    python scripts/probe_lindas.py --dry-run

    # Echte Erhebung, Tabelle nach stdout, Rohdaten als JSON
    python scripts/probe_lindas.py --json-out lindas_felder.json
"""

from __future__ import annotations

import argparse
import json
import random
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_ENDPOINT = "https://lindas.admin.ch/query"
DEFAULT_USER_AGENT = (
    "kmu-discovery-probe/0.1 (Abklaerung Datenquelle, wenige Abfragen; "
    "Kontakt siehe Repository)"
)
#: Mindestpause zwischen zwei Abfragen. LINDAS dokumentiert kein Rate-Limit,
#: deshalb defensiv.
MIN_INTERVAL_SECONDS = 1.0
MAX_ATTEMPTS = 4


class ProbeError(RuntimeError):
    """Der Endpunkt war nicht erreichbar oder hat einen Fehler geliefert."""


@dataclass(frozen=True, slots=True)
class Binding:
    """Ein Ergebniswert einer SPARQL-Abfrage."""

    value: str
    kind: str
    datatype: str | None = None
    language: str | None = None

    @classmethod
    def parse(cls, raw: dict[str, str]) -> Binding:
        """Liest einen Binding-Eintrag aus der SPARQL-JSON-Antwort."""
        return cls(
            value=raw.get("value", ""),
            kind=raw.get("type", "unknown"),
            datatype=raw.get("datatype"),
            language=raw.get("xml:lang"),
        )


@dataclass(frozen=True, slots=True)
class FieldRow:
    """Eine Zeile der Feldtabelle."""

    predicate: str
    occurrences: int
    distinct_subjects: int
    max_per_subject: int
    value_kind: str
    datatype: str | None
    sample_value: str

    def coverage(self, subject_total: int) -> float:
        """Anteil der Subjekte, die dieses Praedikat ueberhaupt tragen."""
        return round(self.distinct_subjects / subject_total, 3) if subject_total else 0.0


@dataclass
class ProbeResult:
    """Gesamtergebnis der Erhebung."""

    endpoint: str
    graph: str | None = None
    graphs: list[tuple[str, int]] = field(default_factory=list)
    classes: list[tuple[str, int]] = field(default_factory=list)
    target_class: str | None = None
    subject_total: int = 0
    fields: list[FieldRow] = field(default_factory=list)
    sample_subject: list[tuple[str, str]] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)


# -- SPARQL-Abfragen ------------------------------------------------------ #


def q_graphs(limit: int) -> str:
    """Benannte Graphen mit Trippelzahl."""
    return f"""
SELECT ?graph (COUNT(*) AS ?n)
WHERE {{ GRAPH ?graph {{ ?s ?p ?o }} }}
GROUP BY ?graph
ORDER BY DESC(?n)
LIMIT {limit}
""".strip()


def q_classes(graph: str, limit: int) -> str:
    """Klassen im Zielgraphen mit Instanzzahl."""
    return f"""
SELECT ?class (COUNT(?s) AS ?n)
WHERE {{ GRAPH <{graph}> {{ ?s a ?class }} }}
GROUP BY ?class
ORDER BY DESC(?n)
LIMIT {limit}
""".strip()


def q_subject_total(graph: str, target_class: str) -> str:
    """Anzahl Instanzen der Zielklasse - Nenner fuer die Abdeckung."""
    return f"""
SELECT (COUNT(DISTINCT ?s) AS ?n)
WHERE {{ GRAPH <{graph}> {{ ?s a <{target_class}> }} }}
""".strip()


def q_predicates(graph: str, target_class: str, limit: int) -> str:
    """Praedikate der Zielklasse mit Haeufigkeit und Beispielwert."""
    return f"""
SELECT ?predicate
       (COUNT(*) AS ?occurrences)
       (COUNT(DISTINCT ?s) AS ?subjects)
       (SAMPLE(?o) AS ?sample)
WHERE {{
  GRAPH <{graph}> {{
    ?s a <{target_class}> .
    ?s ?predicate ?o .
  }}
}}
GROUP BY ?predicate
ORDER BY DESC(?occurrences)
LIMIT {limit}
""".strip()


def q_max_cardinality(graph: str, target_class: str, predicate: str) -> str:
    """Groesste Anzahl Werte eines Praedikats je Subjekt (1:1 oder 1:n?)."""
    return f"""
SELECT (MAX(?n) AS ?max_per_subject)
WHERE {{
  SELECT ?s (COUNT(?o) AS ?n)
  WHERE {{
    GRAPH <{graph}> {{
      ?s a <{target_class}> .
      ?s <{predicate}> ?o .
    }}
  }}
  GROUP BY ?s
}}
""".strip()


def q_sample_subject(graph: str, target_class: str, limit: int) -> str:
    """Ein vollstaendiges Subjekt zur Plausibilitaetskontrolle."""
    return f"""
SELECT ?predicate ?value
WHERE {{
  GRAPH <{graph}> {{
    {{ SELECT ?s WHERE {{ ?s a <{target_class}> }} LIMIT 1 }}
    ?s ?predicate ?value .
  }}
}}
LIMIT {limit}
""".strip()


# -- HTTP ----------------------------------------------------------------- #


class SparqlClient:
    """Minimaler, hoeflicher SPARQL-Client fuer die Erhebung.

    Bewusst ohne Fremdbibliothek: das Skript soll ohne Setup laufen. Der
    Produktivclient in ``kmu_discovery/sources/`` bekommt spaeter Token-Bucket,
    Cache und typisierte Fehler - hier genuegt Mindestpause und Backoff.
    """

    def __init__(
        self,
        endpoint: str,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 60.0,
        min_interval: float = MIN_INTERVAL_SECONDS,
        ca_bundle: str | None = None,
        verbose: bool = False,
    ) -> None:
        """Merkt sich Endpunkt und Hoeflichkeitsparameter."""
        self._endpoint = endpoint
        self._user_agent = user_agent
        self._timeout = timeout
        self._min_interval = min_interval
        self._verbose = verbose
        self._last_call = 0.0
        self._context = ssl.create_default_context(cafile=ca_bundle) if ca_bundle else None

    def query(self, sparql: str) -> list[dict[str, Binding]]:
        """Fuehrt eine Abfrage aus und liefert die Bindings."""
        self._wait()
        data = urllib.parse.urlencode({"query": sparql}).encode("utf-8")
        request = urllib.request.Request(
            self._endpoint,
            data=data,
            headers={
                "Accept": "application/sparql-results+json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": self._user_agent,
            },
        )
        payload = self._read_with_retry(request)
        results = payload.get("results", {}).get("bindings", [])
        return [
            {name: Binding.parse(raw) for name, raw in row.items()}
            for row in results
            if isinstance(row, dict)
        ]

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if self._last_call and elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call = time.monotonic()

    def _read_with_retry(self, request: urllib.request.Request) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(
                    request, timeout=self._timeout, context=self._context
                ) as response:
                    raw = response.read().decode("utf-8")
                parsed: Any = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise ProbeError("Antwort ist kein SPARQL-JSON-Objekt")
                return parsed
            except urllib.error.HTTPError as error:
                last = error
                if error.code in {429, 502, 503, 504}:
                    self._sleep_before_retry(attempt, error.headers.get("Retry-After"))
                    continue
                body = error.read().decode("utf-8", errors="replace")[:400]
                raise ProbeError(f"HTTP {error.code} vom Endpunkt: {body}") from error
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


# -- Erhebung ------------------------------------------------------------- #


def _int(bindings: dict[str, Binding], name: str) -> int:
    binding = bindings.get(name)
    try:
        return int(binding.value) if binding else 0
    except ValueError:
        return 0


def probe(
    client: SparqlClient,
    graph: str | None,
    target_class: str | None,
    graph_hint: str,
    limit: int,
    cardinality_for: int,
    verbose: bool,
) -> ProbeResult:
    """Fuehrt die vierstufige Erhebung aus."""
    result = ProbeResult(endpoint=client._endpoint)

    if graph is None:
        if verbose:
            print("1/4 Benannte Graphen ...", file=sys.stderr)
        query = q_graphs(limit)
        result.queries.append(query)
        rows = client.query(query)
        result.graphs = [(row["graph"].value, _int(row, "n")) for row in rows if "graph" in row]
        candidates = [name for name, _ in result.graphs if graph_hint in name.lower()]
        if not candidates:
            raise ProbeError(
                f"Kein Graph enthaelt {graph_hint!r}. Gefunden: "
                + ", ".join(name for name, _ in result.graphs[:10])
            )
        graph = candidates[0]
    result.graph = graph

    if verbose:
        print(f"2/4 Klassen in <{graph}> ...", file=sys.stderr)
    query = q_classes(graph, limit)
    result.queries.append(query)
    rows = client.query(query)
    result.classes = [(row["class"].value, _int(row, "n")) for row in rows if "class" in row]

    if target_class is None:
        if not result.classes:
            raise ProbeError(f"Graph <{graph}> fuehrt keine typisierten Subjekte")
        target_class = result.classes[0][0]
    result.target_class = target_class

    query = q_subject_total(graph, target_class)
    result.queries.append(query)
    totals = client.query(query)
    result.subject_total = _int(totals[0], "n") if totals else 0

    if verbose:
        print(f"3/4 Praedikate von <{target_class}> ...", file=sys.stderr)
    query = q_predicates(graph, target_class, limit)
    result.queries.append(query)
    rows = client.query(query)

    for index, row in enumerate(rows):
        predicate = row["predicate"].value
        sample = row.get("sample")
        max_per_subject = 0
        if index < cardinality_for:
            card_query = q_max_cardinality(graph, target_class, predicate)
            result.queries.append(card_query)
            card_rows = client.query(card_query)
            max_per_subject = _int(card_rows[0], "max_per_subject") if card_rows else 0
        result.fields.append(
            FieldRow(
                predicate=predicate,
                occurrences=_int(row, "occurrences"),
                distinct_subjects=_int(row, "subjects"),
                max_per_subject=max_per_subject,
                value_kind=sample.kind if sample else "unknown",
                datatype=(sample.datatype or sample.language) if sample else None,
                sample_value=sample.value if sample else "",
            )
        )

    if verbose:
        print("4/4 Beispielsubjekt ...", file=sys.stderr)
    query = q_sample_subject(graph, target_class, limit)
    result.queries.append(query)
    rows = client.query(query)
    result.sample_subject = [
        (row["predicate"].value, row["value"].value) for row in rows if "predicate" in row
    ]
    return result


# -- Ausgabe -------------------------------------------------------------- #


def render_markdown(result: ProbeResult) -> str:
    """Rendert die Feldtabelle als Markdown."""
    lines = [
        "# LINDAS-Feldtabelle (live erhoben)",
        "",
        f"* Endpunkt: `{result.endpoint}`",
        f"* Graph: `{result.graph}`",
        f"* Zielklasse: `{result.target_class}`",
        f"* Instanzen der Zielklasse: {result.subject_total}",
        "",
        "## Graphen",
        "",
        "| Graph | Tripel |",
        "|---|---:|",
    ]
    lines.extend(f"| `{name}` | {count} |" for name, count in result.graphs)
    lines.extend(["", "## Klassen im Zielgraphen", "", "| Klasse | Instanzen |", "|---|---:|"])
    lines.extend(f"| `{name}` | {count} |" for name, count in result.classes)
    lines.extend(
        [
            "",
            "## Felder der Zielklasse",
            "",
            "| Praedikat | Abdeckung | Vorkommen | max. je Subjekt | Typ | Beispielwert |",
            "|---|---:|---:|---:|---|---|",
        ]
    )
    for row in result.fields:
        card = str(row.max_per_subject) if row.max_per_subject else "-"
        kind = row.datatype or row.value_kind
        sample = row.sample_value.replace("|", "\\|")[:80]
        lines.append(
            f"| `{row.predicate}` | {row.coverage(result.subject_total):.1%} | "
            f"{row.occurrences} | {card} | {kind} | {sample} |"
        )
    lines.extend(["", "## Beispielsubjekt", "", "| Praedikat | Wert |", "|---|---|"])
    lines.extend(
        f"| `{predicate}` | {value.replace('|', chr(92) + '|')[:100]} |"
        for predicate, value in result.sample_subject
    )
    return "\n".join(lines)


def _as_payload(result: ProbeResult) -> dict[str, Any]:
    return {
        "endpoint": result.endpoint,
        "graph": result.graph,
        "target_class": result.target_class,
        "subject_total": result.subject_total,
        "graphs": [{"graph": name, "triples": n} for name, n in result.graphs],
        "classes": [{"class": name, "instances": n} for name, n in result.classes],
        "fields": [
            {
                "predicate": row.predicate,
                "occurrences": row.occurrences,
                "distinct_subjects": row.distinct_subjects,
                "coverage": row.coverage(result.subject_total),
                "max_per_subject": row.max_per_subject,
                "value_kind": row.value_kind,
                "datatype": row.datatype,
                "sample_value": row.sample_value,
            }
            for row in result.fields
        ],
        "sample_subject": [{"predicate": p, "value": v} for p, v in result.sample_subject],
        "queries": result.queries,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Erhebt die Feldtabelle oder zeigt im Trockenlauf nur die Abfragen."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--graph", default=None, help="Zielgraph; sonst automatisch gesucht.")
    parser.add_argument("--graph-hint", default="zefix", help="Suchwort fuer den Zielgraphen.")
    parser.add_argument("--class", dest="target_class", default=None, help="Zielklasse (URI).")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument(
        "--cardinality-for",
        type=int,
        default=15,
        help="Fuer wie viele haeufigste Praedikate die Kardinalitaet erhoben wird.",
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--min-interval", type=float, default=MIN_INTERVAL_SECONDS)
    parser.add_argument("--ca-bundle", default=None, help="Eigenes CA-Bundle (Proxy-Umgebung).")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Nur die Abfragen zeigen.")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if args.dry_run:
        graph = args.graph or "https://lindas.admin.ch/foj/zefix"
        target = args.target_class or "https://schema.ld.admin.ch/ZefixOrganisation"
        print("# Trockenlauf - diese Abfragen wuerden gestellt (keine Netzwerkzugriffe)\n")
        print(f"# Platzhalter: Graph <{graph}>, Klasse <{target}>")
        print("# Beide werden im Echtlauf aus Schritt 1 und 2 ermittelt, nicht angenommen.\n")
        for label, query in (
            ("1) Graphen", q_graphs(args.limit)),
            ("2) Klassen", q_classes(graph, args.limit)),
            ("3) Instanzzahl", q_subject_total(graph, target)),
            ("4) Praedikate", q_predicates(graph, target, args.limit)),
            ("5) Kardinalitaet", q_max_cardinality(graph, target, "http://schema.org/name")),
            ("6) Beispielsubjekt", q_sample_subject(graph, target, args.limit)),
        ):
            print(f"--- {label} ---\n{query}\n")
        return 0

    client = SparqlClient(
        endpoint=args.endpoint,
        user_agent=args.user_agent,
        timeout=args.timeout,
        min_interval=args.min_interval,
        ca_bundle=args.ca_bundle,
        verbose=not args.quiet,
    )
    try:
        result = probe(
            client,
            graph=args.graph,
            target_class=args.target_class,
            graph_hint=args.graph_hint.lower(),
            limit=args.limit,
            cardinality_for=args.cardinality_for,
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
