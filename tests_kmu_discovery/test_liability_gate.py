"""Tests des Haftungs-K.o.-Gates.

Zwei Klassen von Tests, beide gleich wichtig:
  * Trefferpflicht  - jedes K.o.-Feld muss erkannt werden.
  * Fehlalarmschutz - harmlose Betriebe duerfen nicht ausgeschlossen werden.
    Ein falsches REJECT kostet einen Kandidaten unbemerkt; das ist der teurere
    Fehler, weil er nie auffaellt.
"""

from __future__ import annotations

import pytest

from kmu_discovery.gates.liability import LIABILITY_REVIEW_FLAG, LiabilityGate
from kmu_discovery.models import DocumentKind, GateOutcome, MatchField, Severity
from tests_kmu_discovery.conftest import PageLoader, make_company, make_document


@pytest.fixture(scope="module")
def gate() -> LiabilityGate:
    return LiabilityGate()


# -- Trefferpflicht ------------------------------------------------------- #


@pytest.mark.parametrize(
    ("noga", "domain"),
    [
        ("86.21", "gesundheit"),
        ("87.10", "gesundheit"),
        ("88.10", "gesundheit"),
        ("47.73", "gesundheit"),
        ("69.10", "recht"),
        ("82.91", "recht"),
        ("41.20", "bau_handwerk"),
        ("43.22", "bau_handwerk"),
        ("81.30", "bau_handwerk"),
        ("78.20", "personalverleih"),
        ("69.20", "treuhand"),
        ("64.19", "finanz"),
        ("65.12", "finanz"),
        ("66.22", "finanz"),
    ],
)
def test_noga_code_schliesst_aus(gate: LiabilityGate, noga: str, domain: str) -> None:
    result = gate.evaluate(make_company(noga_codes=[noga]))
    assert result.outcome is GateOutcome.REJECT
    assert f"liability_reject:{domain}" in result.flags
    noga_matches = [m for m in result.matches if m.field is MatchField.NOGA]
    assert any(m.domain_id == domain for m in noga_matches)


@pytest.mark.parametrize(
    ("firmenname", "domain"),
    [
        ("Zahnarztpraxis Seefeld AG", "gesundheit"),
        ("Spitex Musterthal", "gesundheit"),
        ("Advokatur Muster & Partner", "recht"),
        ("Muster Notariat AG", "recht"),
        ("Muster Treuhand AG", "treuhand"),
        ("Muster Personalverleih GmbH", "personalverleih"),
        ("Muster Vermögensverwaltung AG", "finanz"),
        ("Muster Gartenbau GmbH", "bau_handwerk"),
    ],
)
def test_firmenname_schliesst_aus(gate: LiabilityGate, firmenname: str, domain: str) -> None:
    result = gate.evaluate(make_company(name=firmenname))
    assert result.outcome is GateOutcome.REJECT
    assert f"liability_reject:{domain}" in result.flags


def test_zweckartikel_schliesst_aus(gate: LiabilityGate) -> None:
    company = make_company(
        name="Muster AG", zweck="Führung eines Treuhandbüros sowie Steuerberatung."
    )
    assert gate.evaluate(company).outcome is GateOutcome.REJECT


def test_arztpraxis_website_ohne_noga_wird_erkannt(gate: LiabilityGate, page: PageLoader) -> None:
    """Auch ohne Registerdaten muss die Website zum Ausschluss reichen."""
    company = make_company(
        name="Dorfplatz Gesundheit GmbH", documents=[make_document(page("website_arztpraxis"))]
    )
    result = gate.evaluate(company)
    assert result.outcome is GateOutcome.REJECT
    assert {m.rule_id for m in result.matches} >= {"praxisgemeinschaft", "tarmed"}


def test_jeder_treffer_traegt_einen_beleg(gate: LiabilityGate, page: PageLoader) -> None:
    """Ohne Beleg kein Signal - Grundregel des Projekts."""
    company = make_company(
        name="Dorfplatz Gesundheit GmbH",
        noga_codes=["86.21"],
        documents=[make_document(page("website_arztpraxis"))],
    )
    for match in gate.evaluate(company).matches:
        assert match.context_quote.strip(), match.rule_id
        if match.field is not MatchField.NOGA:
            assert match.matched_text in match.context_quote
            assert match.source_url is not None


# -- Fehlalarmschutz ------------------------------------------------------ #


def test_zielbetrieb_faellt_nicht_durch(gate: LiabilityGate, page: PageLoader) -> None:
    company = make_company(
        name="Fahrschule Musterthal GmbH",
        noga_codes=["85.53"],
        zweck="Betrieb einer Fahrschule.",
        documents=[
            make_document(page("website_fahrschule")),
            make_document(page("job_ad_sachbearbeiterin"), DocumentKind.JOB_POSTING),
        ],
    )
    result = gate.evaluate(company)
    assert result.outcome is GateOutcome.PASS
    assert not result.flags


def test_rechtsform_ist_kein_rechtsdienst(gate: LiabilityGate) -> None:
    """'Rechtsform' darf den Wortstamm 'Recht' nicht ausloesen."""
    company = make_company(
        documents=[make_document("Die Rechtsform ist eine Aktiengesellschaft.")]
    )
    assert gate.evaluate(company).outcome is GateOutcome.PASS


def test_eigener_treuhaender_schliesst_nicht_hart_aus(gate: LiabilityGate) -> None:
    """Ein Betrieb, der seinen Treuhaender erwaehnt, ist selbst kein Treuhaender.

    Erwartet wird REVIEW, nicht REJECT: der Fall ist unscharf, aber er darf
    nicht stillschweigend durchgehen.
    """
    company = make_company(
        name="Muster Handel AG",
        noga_codes=["46.90"],
        documents=[make_document("Unser Treuhänder erstellt den Jahresabschluss.")],
    )
    result = gate.evaluate(company)
    assert result.outcome is GateOutcome.REVIEW
    assert LIABILITY_REVIEW_FLAG in result.flags


def test_einzelnes_schwaches_wort_bleibt_folgenlos(gate: LiabilityGate) -> None:
    company = make_company(documents=[make_document("In der Praxis hat sich das bewährt.")])
    result = gate.evaluate(company)
    assert result.outcome is GateOutcome.PASS
    assert all(m.severity is Severity.INFO for m in result.matches)


def test_zulieferer_wird_durch_kontext_veto_gerettet(
    gate: LiabilityGate, page: PageLoader
) -> None:
    """'Software fuer Arztpraxen' macht aus einem IT-Betrieb keine Arztpraxis."""
    company = make_company(
        name="Musterwerk Informatik GmbH",
        noga_codes=["62.01"],
        documents=[make_document(page("website_software_anbieter"))],
    )
    assert gate.evaluate(company).outcome is GateOutcome.PASS


# -- Grenzfaelle ---------------------------------------------------------- #


@pytest.mark.parametrize("noga", ["75.00", "71.11", "16.23", "88.91"])
def test_grenzfall_noga_wird_markiert_nicht_verworfen(gate: LiabilityGate, noga: str) -> None:
    result = gate.evaluate(make_company(noga_codes=[noga]))
    assert result.outcome is GateOutcome.REVIEW
    assert LIABILITY_REVIEW_FLAG in result.flags


def test_duenne_pruefgrundlage_wird_vermerkt(gate: LiabilityGate) -> None:
    result = gate.evaluate(make_company())
    assert result.outcome is GateOutcome.PASS
    assert any("NOGA" in note for note in result.notes)
    assert any("duenne Pruefgrundlage" in note for note in result.notes)


def test_treffer_werden_entdoppelt(gate: LiabilityGate) -> None:
    text = "Spitex Musterthal. " * 5
    result = gate.evaluate(make_company(documents=[make_document(text)]))
    assert len([m for m in result.matches if m.rule_id == "spitex"]) == 1


def test_gate_ist_reproduzierbar(gate: LiabilityGate, page: PageLoader) -> None:
    company = make_company(
        name="Praxis am Dorfplatz",
        noga_codes=["86.21"],
        documents=[make_document(page("website_arztpraxis"))],
    )
    assert gate.evaluate(company) == gate.evaluate(company)
