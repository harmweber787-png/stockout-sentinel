"""Tests der Gate-Kette und des Beispielaufrufs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kmu_discovery import default_gates, run_gates
from kmu_discovery.__main__ import main
from kmu_discovery.gates.base import GateReport, aggregate_outcome, noga_prefix_match
from kmu_discovery.models import DocumentKind, GateOutcome, GateResult, RuleMatch, Severity
from tests_kmu_discovery.conftest import PageLoader, make_company, make_document


def test_zielbetrieb_besteht_beide_gates(page: PageLoader) -> None:
    company = make_company(
        name="Fahrschule Musterthal GmbH",
        noga_codes=["85.53"],
        zweck="Betrieb einer Fahrschule.",
        emails=["info@bluewin.ch"],
        documents=[
            make_document(page("website_fahrschule")),
            make_document(page("job_ad_sachbearbeiterin"), DocumentKind.JOB_POSTING),
        ],
    )
    report = run_gates(company, default_gates())
    assert report.outcome is GateOutcome.PASS
    assert report.passed
    assert report.rejected_by() == []
    assert "freemail_domain" in report.flags


def test_haftungsausschluss_schlaegt_auf_das_gesamturteil_durch(page: PageLoader) -> None:
    company = make_company(
        name="Praxis am Dorfplatz",
        noga_codes=["86.21"],
        documents=[make_document(page("website_arztpraxis"))],
    )
    report = run_gates(company, default_gates())
    assert report.outcome is GateOutcome.REJECT
    assert report.rejected_by() == ["liability"]


def test_alle_gates_laufen_auch_nach_einem_reject() -> None:
    """Fuer die Kalibrierung muss nachvollziehbar bleiben, was jedes Gate sah."""
    company = make_company(
        name="Muster Treuhand AG",
        documents=[make_document("Buchhaltung mit bexio.")],
    )
    report = run_gates(company, default_gates())
    assert [result.gate for result in report.results] == ["liability", "erp"]
    assert sorted(report.rejected_by()) == ["erp", "liability"]


def test_flags_werden_entdoppelt_und_reihenfolgestabil() -> None:
    company = make_company(name="Muster Treuhand AG", emails=["a@bluewin.ch", "b@gmx.ch"])
    report = run_gates(company, default_gates())
    assert len(report.flags) == len(set(report.flags))
    assert report.flags[0] == "liability_reject:treuhand"


def test_bericht_ist_serialisierbar(page: PageLoader) -> None:
    company = make_company(
        name="Fahrschule Musterthal GmbH",
        documents=[make_document(page("website_fahrschule"))],
    )
    report = run_gates(company, default_gates())
    payload = json.loads(json.dumps(report.model_dump(mode="json")))
    assert payload["uid"] == "CHE-100.000.001"
    assert GateReport.model_validate(payload) == report


def test_aggregate_outcome_nimmt_das_strengste_urteil() -> None:
    def match(severity: Severity) -> RuleMatch:
        return RuleMatch(
            rule_id="x",
            domain_id="d",
            label="L",
            field="website",  # type: ignore[arg-type]
            severity=severity,
            matched_text="x",
            context_quote="x",
        )

    assert aggregate_outcome(()) is GateOutcome.PASS
    assert aggregate_outcome((match(Severity.INFO),)) is GateOutcome.PASS
    assert aggregate_outcome((match(Severity.INFO), match(Severity.REVIEW))) is GateOutcome.REVIEW
    assert (
        aggregate_outcome((match(Severity.REVIEW), match(Severity.REJECT)))
        is GateOutcome.REJECT
    )


def test_noga_prefix_match() -> None:
    assert noga_prefix_match(["8621"], ("86",)) == ("8621", "86")
    assert noga_prefix_match(["4690"], ("86",)) is None
    assert noga_prefix_match([], ("86",)) is None


def test_gate_result_reasons_sind_lesbar() -> None:
    result = GateResult(gate="liability", outcome=GateOutcome.PASS)
    assert result.reasons() == []
    assert result.passed


def test_cli_demo_laeuft(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--demo"]) == 0
    out = capsys.readouterr().out
    assert "REJECT" in out
    assert "Lauf-Statistik" in out


def test_cli_begrenzt_die_trefferliste(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--demo", "--format", "json", "--top", "1"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["stats"]["top_review_terms"]) == 1


def test_cli_json_ausgabe_ist_gueltig(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--demo", "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [entry["outcome"] for entry in payload["reports"]] == [
        "pass",
        "reject",
        "review",
        "reject",
    ]
    assert payload["stats"]["outcomes"] == {"passed": 1, "review": 1, "rejected": 2}


def test_cli_liest_json_datei(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "betriebe.json"
    path.write_text(
        json.dumps([{"uid": "CHE-100.000.009", "name": "Muster Treuhand AG"}]),
        encoding="utf-8",
    )
    assert main(["--input", str(path), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["reports"][0]["outcome"] == "reject"
