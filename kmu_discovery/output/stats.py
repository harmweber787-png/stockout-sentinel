"""Lauf-Statistik ueber eine Gate-Kette.

Zweck ist Kalibrierung, nicht Reporting: ``weak_hits_for_review`` und die
Wortlisten sollen spaeter an echten Zahlen justiert werden statt am Bauchgefuehl.
Dafuer braucht es die Verteilung der Urteile, die Gruende hinter jedem REVIEW
und die Wortlisten-Treffer, die REVIEW am haeufigsten ausloesen.

Gezaehlt werden **Betriebe, nicht Treffer**: ein Begriff, der auf einer Website
zwanzigmal steht, ist ein Betrieb, kein zwanzigfaches Signal.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from kmu_discovery.gates.base import GateReport
from kmu_discovery.gates.liability import LIABILITY_REVIEW_FLAG
from kmu_discovery.models import GateOutcome, MatchField, Severity

__all__ = ["DEFAULT_TOP_N", "OutcomeCounts", "ReasonTally", "RunStats", "TermTally"]

DEFAULT_TOP_N = 10

#: Sammelflag, das jedes REVIEW traegt - in der Aufschluesselung waere es nur
#: eine Wiederholung der Gesamtzahl.
_UMBRELLA_FLAGS = frozenset({LIABILITY_REVIEW_FLAG})


class OutcomeCounts(BaseModel):
    """Verteilung der Gesamturteile ueber einen Lauf."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: int = Field(default=0, ge=0)
    review: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)

    @property
    def total(self) -> int:
        """Anzahl bewerteter Betriebe."""
        return self.passed + self.review + self.rejected

    def share(self, outcome: GateOutcome) -> float:
        """Anteil eines Urteils am Lauf (0.0, wenn der Lauf leer war)."""
        if self.total == 0:
            return 0.0
        value = {
            GateOutcome.PASS: self.passed,
            GateOutcome.REVIEW: self.review,
            GateOutcome.REJECT: self.rejected,
        }[outcome]
        return round(value / self.total, 3)


class ReasonTally(BaseModel):
    """Ein REVIEW-Grund und wie oft er vorkam."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str
    count: int = Field(ge=1)
    share: float = Field(ge=0.0, le=1.0, description="Anteil an allen REVIEW-Faellen.")


class TermTally(BaseModel):
    """Ein Wortlisten-Treffer, der zu REVIEW gefuehrt hat."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    domain_id: str
    label: str
    count: int = Field(ge=1, description="Anzahl betroffener Betriebe, nicht Treffer.")
    example_match: str = Field(description="Die getroffene Stelle in Originalschreibweise.")
    example_quote: str = Field(description="Ein Beleg, damit die Zahl pruefbar bleibt.")


class RunStats(BaseModel):
    """Zusammenfassung eines Gate-Laufs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcomes: OutcomeCounts
    review_reasons: tuple[ReasonTally, ...] = ()
    top_review_terms: tuple[TermTally, ...] = ()
    top_n: int = Field(default=DEFAULT_TOP_N, ge=1)

    @classmethod
    def from_reports(
        cls, reports: Iterable[GateReport], top_n: int = DEFAULT_TOP_N
    ) -> RunStats:
        """Wertet eine Liste von Gate-Berichten aus.

        >>> RunStats.from_reports([]).outcomes.total
        0
        """
        materialized: Sequence[GateReport] = list(reports)
        counts = Counter(report.outcome for report in materialized)
        outcomes = OutcomeCounts(
            passed=counts[GateOutcome.PASS],
            review=counts[GateOutcome.REVIEW],
            rejected=counts[GateOutcome.REJECT],
        )
        reviewed = [r for r in materialized if r.outcome is GateOutcome.REVIEW]
        return cls(
            outcomes=outcomes,
            review_reasons=_tally_reasons(reviewed),
            top_review_terms=_tally_terms(reviewed, top_n),
            top_n=top_n,
        )

    def as_table(self) -> str:
        """Rendert die Statistik als Textblock fuer die Konsole."""
        lines = [
            "Lauf-Statistik",
            "=" * 62,
            f"  Betriebe geprueft : {self.outcomes.total}",
            f"  PASS              : {self.outcomes.passed:>5}"
            f"  ({self.outcomes.share(GateOutcome.PASS):.1%})",
            f"  REVIEW            : {self.outcomes.review:>5}"
            f"  ({self.outcomes.share(GateOutcome.REVIEW):.1%})",
            f"  REJECT            : {self.outcomes.rejected:>5}"
            f"  ({self.outcomes.share(GateOutcome.REJECT):.1%})",
        ]
        lines.extend(_reason_block(self.review_reasons))
        lines.extend(_term_block(self.top_review_terms, self.top_n))
        return "\n".join(lines)


# -- intern -------------------------------------------------------------- #


def _tally_reasons(reviewed: Sequence[GateReport]) -> tuple[ReasonTally, ...]:
    counter: Counter[str] = Counter()
    for report in reviewed:
        counter.update(flag for flag in report.flags if flag not in _UMBRELLA_FLAGS)
    total = len(reviewed)
    return tuple(
        ReasonTally(
            reason=reason,
            count=count,
            share=round(count / total, 3) if total else 0.0,
        )
        for reason, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    )


def _tally_terms(reviewed: Sequence[GateReport], top_n: int) -> tuple[TermTally, ...]:
    counter: Counter[tuple[str, str]] = Counter()
    labels: dict[tuple[str, str], str] = {}
    matched: dict[tuple[str, str], str] = {}
    quotes: dict[tuple[str, str], str] = {}
    for report in reviewed:
        seen: set[tuple[str, str]] = set()
        for result in report.results:
            for match in result.matches:
                if match.severity is not Severity.REVIEW or match.field is MatchField.NOGA:
                    continue
                key = (match.rule_id, match.domain_id)
                if key in seen:
                    continue
                seen.add(key)
                counter[key] += 1
                labels.setdefault(key, match.label)
                matched.setdefault(key, match.matched_text)
                quotes.setdefault(key, match.context_quote)
    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    return tuple(
        TermTally(
            rule_id=rule_id,
            domain_id=domain_id,
            label=labels[(rule_id, domain_id)],
            count=count,
            example_match=matched[(rule_id, domain_id)],
            example_quote=quotes[(rule_id, domain_id)],
        )
        for (rule_id, domain_id), count in ranked[:top_n]
    )


def _reason_block(reasons: tuple[ReasonTally, ...]) -> list[str]:
    lines = ["", "REVIEW nach Grund", "-" * 62]
    if not reasons:
        lines.append("  (keine REVIEW-Faelle)")
        return lines
    width = max(len(reason.reason) for reason in reasons)
    lines.extend(
        f"  {reason.reason:<{width}}  {reason.count:>5}  ({reason.share:.1%})"
        for reason in reasons
    )
    return lines


def _term_block(terms: tuple[TermTally, ...], top_n: int) -> list[str]:
    lines = ["", f"Haeufigste Wortlisten-Treffer mit REVIEW (Top {top_n})", "-" * 62]
    if not terms:
        lines.append("  (keine Wortlisten-Treffer)")
        return lines
    width = max(len(f"{term.domain_id}/{term.rule_id}") for term in terms)
    for term in terms:
        name = f"{term.domain_id}/{term.rule_id}"
        lines.append(f"  {name:<{width}}  {term.count:>5}  {_shorten(term.example_match)}")
    lines.append("  (vollstaendige Belegzitate im JSON-Format)")
    return lines


def _shorten(text: str, width: int = 40) -> str:
    return text if len(text) <= width else f"{text[: width - 3]}..."
