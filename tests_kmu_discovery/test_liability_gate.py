"""Tests des Haftungs-K.o.-Gates.

Fehlerasymmetrie dieses Gates: der **verpasste Ausschluss** ist der teure
Fehler. Das Gate faehrt deshalb aggressiv - Zweifel fuehren zu REJECT oder
mindestens ``liability_review_needed``. Die Gegenrichtung bleibt trotzdem
getestet: ein klar harmloser Betrieb muss sauber durchkommen, sonst ist die
Aggressivitaet nur Rauschen.
"""

from __future__ import annotations

import pytest

from kmu_discovery.config.rules import (
    LiabilityDomain,
    LiabilitySensitivity,
    load_liability_rules,
)
from kmu_discovery.gates.liability import (
    LIABILITY_REVIEW_FLAG,
    LIABILITY_THIN_EVIDENCE_FLAG,
    LiabilityGate,
)
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


def test_schwache_begriffe_unter_der_schwelle_gehen_in_die_pruefschlange(
    gate: LiabilityGate,
) -> None:
    """Aggressives Profil: unterschwellige Treffer sind REVIEW, nicht folgenlos."""
    company = make_company(
        noga_codes=["46.90"],
        documents=[make_document("Die Behandlung Ihrer Anfrage erfolgt umgehend.")],
    )
    result = gate.evaluate(company)
    assert result.outcome is GateOutcome.REVIEW
    assert LIABILITY_REVIEW_FLAG in result.flags
    assert {m.severity for m in result.matches if m.field is not MatchField.NOGA} == {
        Severity.REVIEW
    }


def test_konservatives_profil_laesst_unterschwelliges_durch() -> None:
    """Gegenprobe: mit umgestellter Sensitivitaet bleibt derselbe Fall folgenlos."""
    rules = load_liability_rules()
    zahm = rules.model_copy(
        update={
            "sensitivity": LiabilitySensitivity(
                profile="conservative",
                below_threshold_outcome=GateOutcome.PASS,
                thin_evidence_outcome=GateOutcome.PASS,
                vetoed_hit_outcome=GateOutcome.PASS,
            )
        }
    )
    company = make_company(
        noga_codes=["46.90"],
        documents=[make_document("Die Behandlung Ihrer Anfrage erfolgt umgehend.")],
    )
    result = LiabilityGate(rules=zahm).evaluate(company)
    assert result.outcome is GateOutcome.PASS
    assert all(m.severity is Severity.INFO for m in result.matches)


def test_zulieferer_wird_nicht_ausgeschlossen_aber_vorgelegt(
    gate: LiabilityGate, page: PageLoader
) -> None:
    """'Software fuer Arztpraxen' macht aus einem IT-Betrieb keine Arztpraxis.

    Das Kontext-Veto verhindert den Ausschluss - aber aggressiv heisst: der Fall
    geht in die Pruefschlange, nicht stillschweigend durch.
    """
    company = make_company(
        name="Musterwerk Informatik GmbH",
        noga_codes=["62.01"],
        documents=[make_document(page("website_software_anbieter"))],
    )
    result = gate.evaluate(company)
    assert result.outcome is GateOutcome.REVIEW
    assert LIABILITY_REVIEW_FLAG in result.flags


def test_konservatives_profil_laesst_zulieferer_ganz_durch(page: PageLoader) -> None:
    """Gegenprobe zur Vetobehandlung."""
    rules = load_liability_rules()
    zahm = rules.model_copy(
        update={
            "sensitivity": LiabilitySensitivity(
                profile="conservative",
                below_threshold_outcome=GateOutcome.PASS,
                thin_evidence_outcome=GateOutcome.PASS,
                vetoed_hit_outcome=GateOutcome.PASS,
            )
        }
    )
    company = make_company(
        name="Musterwerk Informatik GmbH",
        noga_codes=["62.01"],
        documents=[make_document(page("website_software_anbieter"))],
    )
    assert LiabilityGate(rules=zahm).evaluate(company).outcome is GateOutcome.PASS


# -- Grenzfaelle ---------------------------------------------------------- #


def test_veterinaer_bleibt_im_kandidatenpool() -> None:
    """NOGA 75: Tierdaten sind keine besonders schuetzenswerten Personendaten."""
    result = LiabilityGate().evaluate(make_company(noga_codes=["75.00"]))
    assert result.outcome is GateOutcome.PASS
    assert result.flags == ()


def test_noga_review_mechanik_bleibt_nutzbar(gate: LiabilityGate) -> None:
    """Im ausgelieferten Katalog steht derzeit kein Grenzfall-Praefix mehr.

    Die Mechanik bleibt trotzdem geprueft - sie wird gebraucht, sobald bei der
    Kalibrierung ein neues Feld auftaucht, das noch nicht entschieden ist.
    """
    domain = LiabilityDomain(
        id="testfeld",
        label="Testfeld",
        rationale="nur fuer den Test",
        noga_reject_prefixes=("99",),
        noga_review_prefixes=("98",),
    )
    rules = load_liability_rules().model_copy(update={"domains": (domain,)})
    result = LiabilityGate(rules=rules).evaluate(make_company(noga_codes=["98.10"]))
    assert result.outcome is GateOutcome.REVIEW
    assert LIABILITY_REVIEW_FLAG in result.flags
    assert "liability_review:testfeld" in result.flags


@pytest.mark.parametrize(
    ("noga", "domain"),
    [
        ("71.11", "bau_handwerk"),  # Planer, SIA-Bezug
        ("16.23", "bau_handwerk"),  # Bauschreinerei, Werkvertrag
        ("25.11", "bau_handwerk"),  # Metallbau
        ("25.12", "bau_handwerk"),  # Stahl- und Metallbau
        ("88.91", "gesundheit"),    # Kitas, Daten ueber Kinder
        ("47.74", "gesundheit"),    # Sanitaetshaeuser, Kostengutsprachen IV/KK
    ],
)
def test_entschiedene_grenzfaelle_schliessen_aus(
    gate: LiabilityGate, noga: str, domain: str
) -> None:
    result = gate.evaluate(make_company(noga_codes=[noga]))
    assert result.outcome is GateOutcome.REJECT
    assert f"liability_reject:{domain}" in result.flags


def test_duenne_pruefgrundlage_ist_kein_sauberes_pass(gate: LiabilityGate) -> None:
    """Kein NOGA und kein Text heisst: nichts geprueft, nicht nichts gefunden."""
    result = gate.evaluate(make_company())
    assert result.outcome is GateOutcome.REVIEW
    assert LIABILITY_THIN_EVIDENCE_FLAG in result.flags
    assert any("duenne Pruefgrundlage" in note for note in result.notes)


def test_website_ohne_noga_gilt_nicht_als_duenn(gate: LiabilityGate) -> None:
    """Ein geprueftes PASS bleibt ein PASS - sonst stuende alles auf REVIEW."""
    result = gate.evaluate(
        make_company(documents=[make_document("Familienbetrieb für Verpackungen seit 1954.")])
    )
    assert result.outcome is GateOutcome.PASS
    assert LIABILITY_THIN_EVIDENCE_FLAG not in result.flags


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
