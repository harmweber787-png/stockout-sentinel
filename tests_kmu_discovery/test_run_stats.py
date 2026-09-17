"""Tests der Lauf-Statistik.

Die Zahlen sind die Grundlage, um ``weak_hits_for_review`` spaeter an echten
Laeufen zu kalibrieren. Sie muessen deshalb exakt sein, nicht ungefaehr.
"""

from __future__ import annotations

import json

from kmu_discovery import default_gates, run_gates
from kmu_discovery.gates.base import GateReport
from kmu_discovery.gates.liability import LIABILITY_REVIEW_FLAG
from kmu_discovery.models import (
    GateOutcome,
    GateResult,
    MatchField,
    RuleMatch,
    Severity,
)
from kmu_discovery.output import RunStats
from tests_kmu_discovery.conftest import PageLoader, make_company, make_document


def _match(
    rule_id: str,
    severity: Severity = Severity.REVIEW,
    field: MatchField = MatchField.WEBSITE,
    domain_id: str = "gesundheit",
) -> RuleMatch:
    return RuleMatch(
        rule_id=rule_id,
        domain_id=domain_id,
        label="Gesundheit und Heilberufe",
        field=field,
        severity=severity,
        matched_text=rule_id.capitalize(),
        context_quote=f"... {rule_id.capitalize()} im Satz ...",
    )


def _report(
    uid: str,
    outcome: GateOutcome,
    *matches: RuleMatch,
    flags: tuple[str, ...] = (),
) -> GateReport:
    return GateReport(
        uid=uid,
        outcome=outcome,
        results=(GateResult(gate="liability", outcome=outcome, matches=matches),),
        flags=flags,
    )


def test_leerer_lauf() -> None:
    stats = RunStats.from_reports([])
    assert stats.outcomes.total == 0
    assert stats.outcomes.share(GateOutcome.PASS) == 0.0
    assert stats.review_reasons == ()
    assert stats.top_review_terms == ()


def test_urteile_werden_gezaehlt() -> None:
    stats = RunStats.from_reports(
        [
            _report("CHE-100.000.001", GateOutcome.PASS),
            _report("CHE-100.000.002", GateOutcome.PASS),
            _report("CHE-100.000.003", GateOutcome.REVIEW),
            _report("CHE-100.000.004", GateOutcome.REJECT),
        ]
    )
    assert (stats.outcomes.passed, stats.outcomes.review, stats.outcomes.rejected) == (2, 1, 1)
    assert stats.outcomes.total == 4
    assert stats.outcomes.share(GateOutcome.PASS) == 0.5


def test_review_gruende_werden_aufgeschluesselt() -> None:
    stats = RunStats.from_reports(
        [
            _report(
                "CHE-100.000.001",
                GateOutcome.REVIEW,
                flags=(LIABILITY_REVIEW_FLAG, "liability_review:gesundheit"),
            ),
            _report(
                "CHE-100.000.002",
                GateOutcome.REVIEW,
                flags=(LIABILITY_REVIEW_FLAG, "liability_review:gesundheit"),
            ),
            _report(
                "CHE-100.000.003",
                GateOutcome.REVIEW,
                flags=(LIABILITY_REVIEW_FLAG, "liability_thin_evidence"),
            ),
        ]
    )
    assert [(r.reason, r.count) for r in stats.review_reasons] == [
        ("liability_review:gesundheit", 2),
        ("liability_thin_evidence", 1),
    ]
    assert stats.review_reasons[0].share == 0.667


def test_sammelflag_wird_nicht_mitgezaehlt() -> None:
    """``liability_review_needed`` traegt jedes REVIEW - es waere nur die Gesamtzahl."""
    stats = RunStats.from_reports(
        [_report("CHE-100.000.001", GateOutcome.REVIEW, flags=(LIABILITY_REVIEW_FLAG,))]
    )
    assert stats.review_reasons == ()


def test_erp_review_flag_erscheint_als_grund() -> None:
    stats = RunStats.from_reports(
        [_report("CHE-100.000.001", GateOutcome.REVIEW, flags=("erp_review_needed",))]
    )
    assert [r.reason for r in stats.review_reasons] == ["erp_review_needed"]


def test_wortliste_zaehlt_betriebe_nicht_treffer() -> None:
    """Ein Begriff zwanzigmal auf einer Seite ist ein Betrieb, kein Signalberg."""
    stats = RunStats.from_reports(
        [
            _report(
                "CHE-100.000.001",
                GateOutcome.REVIEW,
                _match("patient"),
                _match("patient", field=MatchField.JOB_POSTING),
            ),
            _report("CHE-100.000.002", GateOutcome.REVIEW, _match("patient")),
        ]
    )
    assert [(t.rule_id, t.count) for t in stats.top_review_terms] == [("patient", 2)]


def test_nur_review_treffer_aus_review_faellen_zaehlen() -> None:
    stats = RunStats.from_reports(
        [
            # REJECT-Fall: zaehlt nicht, auch mit REVIEW-Treffern darin.
            _report("CHE-100.000.001", GateOutcome.REJECT, _match("patient")),
            # REVIEW-Fall, aber der Treffer selbst ist nur INFO.
            _report("CHE-100.000.002", GateOutcome.REVIEW, _match("x", Severity.INFO)),
            _report("CHE-100.000.003", GateOutcome.REVIEW, _match("sprechstunde")),
        ]
    )
    assert [t.rule_id for t in stats.top_review_terms] == ["sprechstunde"]


def test_noga_treffer_sind_keine_wortlisten_treffer() -> None:
    stats = RunStats.from_reports(
        [
            _report(
                "CHE-100.000.001",
                GateOutcome.REVIEW,
                _match("noga_75", field=MatchField.NOGA),
                _match("patient"),
            )
        ]
    )
    assert [t.rule_id for t in stats.top_review_terms] == ["patient"]


def test_rangfolge_und_top_n() -> None:
    reports = [
        _report("CHE-100.000.001", GateOutcome.REVIEW, _match("a"), _match("b"), _match("c")),
        _report("CHE-100.000.002", GateOutcome.REVIEW, _match("b"), _match("c")),
        _report("CHE-100.000.003", GateOutcome.REVIEW, _match("c")),
    ]
    stats = RunStats.from_reports(reports, top_n=2)
    assert [(t.rule_id, t.count) for t in stats.top_review_terms] == [("c", 3), ("b", 2)]
    assert stats.top_n == 2


def test_gleichstand_wird_stabil_sortiert() -> None:
    stats = RunStats.from_reports(
        [_report("CHE-100.000.001", GateOutcome.REVIEW, _match("zebra"), _match("alpha"))]
    )
    assert [t.rule_id for t in stats.top_review_terms] == ["alpha", "zebra"]


def test_treffer_traegt_beleg() -> None:
    stats = RunStats.from_reports(
        [_report("CHE-100.000.001", GateOutcome.REVIEW, _match("patient"))]
    )
    term = stats.top_review_terms[0]
    assert term.example_match == "Patient"
    assert "Patient" in term.example_quote
    assert term.label == "Gesundheit und Heilberufe"


def test_tabelle_nennt_die_kernzahlen() -> None:
    stats = RunStats.from_reports(
        [
            _report("CHE-100.000.001", GateOutcome.PASS),
            _report(
                "CHE-100.000.002",
                GateOutcome.REVIEW,
                _match("patient"),
                flags=(LIABILITY_REVIEW_FLAG, "liability_thin_evidence"),
            ),
        ]
    )
    table = stats.as_table()
    assert "Betriebe geprueft : 2" in table
    assert "liability_thin_evidence" in table
    assert "gesundheit/patient" in table


def test_statistik_ist_serialisierbar() -> None:
    stats = RunStats.from_reports(
        [_report("CHE-100.000.001", GateOutcome.REVIEW, _match("patient"))]
    )
    payload = json.loads(json.dumps(stats.model_dump(mode="json")))
    assert RunStats.model_validate(payload) == stats


def test_statistik_ueber_einen_echten_lauf(page: PageLoader) -> None:
    companies = [
        make_company(
            uid="CHE-100.000.001",
            name="Fahrschule Musterthal GmbH",
            noga_codes=["85.53"],
            documents=[make_document(page("website_fahrschule"))],
        ),
        make_company(
            uid="CHE-100.000.002",
            name="Praxis am Dorfplatz",
            noga_codes=["86.21"],
            documents=[make_document(page("website_arztpraxis"))],
        ),
        make_company(
            uid="CHE-100.000.003",
            name="Musterwerk Informatik GmbH",
            noga_codes=["62.01"],
            documents=[make_document(page("website_software_anbieter"))],
        ),
    ]
    gates = default_gates()
    stats = RunStats.from_reports(run_gates(company, gates) for company in companies)
    assert (stats.outcomes.passed, stats.outcomes.review, stats.outcomes.rejected) == (1, 1, 1)
    assert [r.reason for r in stats.review_reasons] == ["liability_review:gesundheit"]
    assert {t.rule_id for t in stats.top_review_terms} == {
        "klinik",
        "praxisgemeinschaft",
        "tarmed",
    }
