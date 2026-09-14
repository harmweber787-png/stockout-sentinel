"""Gemeinsame Mechanik der deterministischen Gates.

Die Gates entscheiden, das LLM nicht. Jedes Gate bekommt ein
:class:`~kmu_discovery.models.CompanyProfile`, prueft Regeln gegen definierte
Felder und liefert ein :class:`~kmu_discovery.models.GateResult` mit Belegen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from kmu_discovery.config.rules import TermPattern
from kmu_discovery.gates.text import FoldedText, TermHit, find_regex, find_term, fold
from kmu_discovery.models import (
    CompanyProfile,
    GateOutcome,
    GateResult,
    GateResultUnion,
    MatchField,
    RuleMatch,
    Severity,
)

__all__ = [
    "Gate",
    "GateReport",
    "PatternHit",
    "ScanField",
    "aggregate_outcome",
    "dedupe_matches",
    "find_pattern_hits",
    "noga_prefix_match",
    "run_gates",
    "scan_fields",
    "to_rule_match",
]


@dataclass(frozen=True, slots=True)
class ScanField:
    """Ein durchsuchbares Textfeld eines Betriebs samt Herkunft."""

    field: MatchField
    text: str
    source_url: str | None = None

    def folded(self) -> FoldedText:
        """Gefaltete Fassung des Feldtexts."""
        return fold(self.text)


@dataclass(frozen=True, slots=True)
class PatternHit:
    """Ein Regeltreffer vor der Bewertung."""

    pattern: TermPattern
    scan: ScanField
    hit: TermHit


@runtime_checkable
class Gate(Protocol):
    """Ein deterministisches Gate."""

    name: str

    def evaluate(self, company: CompanyProfile) -> GateResult:
        """Bewertet einen Betrieb. Wirft nie - Unklarheit wird zu REVIEW."""
        ...


def scan_fields(company: CompanyProfile) -> list[ScanField]:
    """Stellt alle textlichen Pruefflaechen eines Betriebs zusammen.

    Reihenfolge ist stabil: Name, Zweckartikel, dann die Dokumente in
    Eingangsreihenfolge. Damit sind Gate-Ergebnisse reproduzierbar.
    """
    fields: list[ScanField] = [ScanField(MatchField.NAME, company.name, company.website)]
    if company.zweck:
        fields.append(ScanField(MatchField.ZWECK, company.zweck, None))
    fields.extend(
        ScanField(document.match_field, document.text, document.url)
        for document in company.documents
        if document.text.strip()
    )
    return fields


def noga_prefix_match(codes: list[str], prefixes: tuple[str, ...]) -> tuple[str, str] | None:
    """Findet den ersten NOGA-Code, der auf einem der Praefixe beginnt.

    >>> noga_prefix_match(["8621"], ("86",))
    ('8621', '86')
    """
    for code in codes:
        for prefix in prefixes:
            if code.startswith(prefix):
                return code, prefix
    return None


def _veto_spans(folded: FoldedText, veto_terms: tuple[str, ...]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for veto in veto_terms:
        spans.extend(
            (hit.folded_start, hit.folded_end) for hit in find_term(folded, veto)
        )
    return spans


def find_pattern_hits(
    scan: ScanField,
    patterns: tuple[TermPattern, ...],
    veto_terms: tuple[str, ...] = (),
    veto_window: int = 0,
) -> list[PatternHit]:
    """Sucht alle Muster in einem Feld und verwirft kontext-vetotierte Treffer.

    Ein Treffer faellt weg, wenn innerhalb von ``veto_window`` Zeichen ein
    Veto-Begriff steht - das entlarvt Zulieferer ("Software fuer Arztpraxen"),
    ohne echte Treffer generell zu schwaechen.
    """
    folded = scan.folded()
    vetoes = _veto_spans(folded, veto_terms) if veto_terms and veto_window else []
    results: list[PatternHit] = []
    for pattern in patterns:
        hits = (
            find_regex(folded, pattern.regex)
            if pattern.regex is not None
            else find_term(folded, pattern.folded_term, pattern.mode)
        )
        for hit in hits:
            if any(
                hit.folded_start - veto_window <= veto_end
                and veto_start <= hit.folded_end + veto_window
                for veto_start, veto_end in vetoes
            ):
                continue
            results.append(PatternHit(pattern=pattern, scan=scan, hit=hit))
    return results


def to_rule_match(
    hit: PatternHit, domain_id: str, label: str, severity: Severity
) -> RuleMatch:
    """Uebersetzt einen Treffer in einen belegten Regeltreffer."""
    return RuleMatch(
        rule_id=hit.pattern.id,
        domain_id=domain_id,
        label=label,
        field=hit.scan.field,
        severity=severity,
        matched_text=hit.hit.matched_text,
        context_quote=hit.hit.quote,
        source_url=hit.scan.source_url,
    )


def aggregate_outcome(matches: tuple[RuleMatch, ...]) -> GateOutcome:
    """Strengstes Urteil aller Treffer; ohne Treffer PASS."""
    outcome = GateOutcome.PASS
    for match in matches:
        candidate = match.severity.to_outcome()
        if candidate.rank > outcome.rank:
            outcome = candidate
    return outcome


def dedupe_matches(matches: list[RuleMatch]) -> list[RuleMatch]:
    """Entfernt identische Treffer (gleiche Regel, Domain, Feld, Fundstelle).

    Dieselbe Nennung taucht auf einer Website oft mehrfach auf; fuer den Beleg
    genuegt die erste Fundstelle.
    """
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[RuleMatch] = []
    for match in matches:
        key = (match.rule_id, match.domain_id, match.field.value, match.matched_text.lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(match)
    return unique


class GateReport(BaseModel):
    """Gesamturteil aller Gates fuer einen Betrieb."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    uid: str
    outcome: GateOutcome
    results: tuple[GateResultUnion, ...]
    flags: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        """True, wenn der Betrieb ins Scoring darf (REVIEW zaehlt als bestanden)."""
        return self.outcome is not GateOutcome.REJECT

    def rejected_by(self) -> list[str]:
        """Namen der Gates, die den Betrieb ausgeschlossen haben."""
        return [result.gate for result in self.results if result.outcome is GateOutcome.REJECT]


def run_gates(company: CompanyProfile, gates: tuple[Gate, ...]) -> GateReport:
    """Fuehrt alle Gates aus - immer alle, auch nach einem REJECT.

    Ein vollstaendiges Bild ist mehr wert als ein paar gesparte Millisekunden:
    fuer die Kalibrierung muss nachvollziehbar bleiben, warum ein Betrieb
    ausgeschlossen wurde, nicht nur, dass er es wurde.
    """
    results = tuple(gate.evaluate(company) for gate in gates)
    outcome = GateOutcome.PASS
    flags: list[str] = []
    for result in results:
        if result.outcome.rank > outcome.rank:
            outcome = result.outcome
        flags.extend(result.flags)
    return GateReport(
        uid=company.uid, outcome=outcome, results=results, flags=tuple(dict.fromkeys(flags))
    )
