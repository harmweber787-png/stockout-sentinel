"""Tests des ERP-Negativfilters.

Fehlerasymmetrie dieses Gates: der **Fehlalarm** ist der teure Fehler - ein
faelschlich ausgeschlossener Betrieb faellt nie auf. Das Gate faehrt deshalb
konservativ: nur ein harter Nachweis schliesst aus, ein blosser Verdacht setzt
nur ein Flag. Der klassische Fehlalarm kommt von mehrdeutigen Anbieternamen
("ich sage Ihnen", "Klara Meier", "wir liefern Bananen") - dafuer gibt es
eigene Tests.
"""

from __future__ import annotations

import pytest

from kmu_discovery.config.rules import ErpSensitivity, load_erp_rules
from kmu_discovery.gates.erp import ERP_REVIEW_FLAG, ErpGate, ErpStatus
from kmu_discovery.models import DocumentKind, GateOutcome, MatchField, Severity
from tests_kmu_discovery.conftest import PageLoader, make_company, make_document


@pytest.fixture(scope="module")
def gate() -> ErpGate:
    return ErpGate()


# -- Trefferpflicht ------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "vendor"),
    [
        ("Abacus-Kenntnisse vorausgesetzt.", "abacus"),
        ("Erfahrung mit AbaNinja von Vorteil.", "abaninja"),
        ("Wir fakturieren mit bexio.", "bexio"),
        ("Wir arbeiten mit KLARA Business.", "klara"),
        ("Unsere Buchhaltung läuft auf Swiss21.", "swiss21"),
        ("Buchhaltung mit Sage 50 Extra.", "sage"),
        ("SAP Business One im Einsatz.", "sap"),
        ("Wir setzen Microsoft Dynamics 365 ein.", "dynamics"),
        ("ERP-System: Odoo.", "odoo"),
        ("Lohnbuchhaltung mit Infoniqa.", "infoniqa"),
        ("Kalkulation mit SORBA Software.", "sorba"),
        ("Devisierung mit BauBit.", "baubit"),
        ("Wir arbeiten mit BauPlus.", "bauplus"),
        ("Unser CRM ist tocco.", "tocco"),
        ("Leistungserfassung in Vertec.", "vertec"),
        ("Wir setzen PROFFIX ein.", "proffix"),
    ],
)
def test_anbieternennung_schliesst_aus(gate: ErpGate, text: str, vendor: str) -> None:
    result = gate.evaluate(make_company(documents=[make_document(text)]))
    assert result.outcome is GateOutcome.REJECT
    assert result.status is ErpStatus.DETECTED
    assert vendor in result.vendors
    assert result.absence_signal == 0.0


def test_stelleninserat_ist_ein_harter_nachweis(gate: ErpGate) -> None:
    """Ein Inserat verlangt Kenntnisse im tatsaechlich eingesetzten System."""
    company = make_company(
        documents=[make_document("Abacus-Anwenderin gesucht.", DocumentKind.JOB_POSTING)]
    )
    result = gate.evaluate(company)
    assert result.outcome is GateOutcome.REJECT
    assert any(m.field is MatchField.JOB_POSTING for m in result.matches)


def test_portal_domain_im_text_schliesst_aus(gate: ErpGate) -> None:
    company = make_company(
        documents=[make_document("Kundenlogin: https://portal.abaninja.ch/kunden")]
    )
    assert gate.evaluate(company).status is ErpStatus.DETECTED


def test_anbieter_domain_als_firmenmail_schliesst_aus(gate: ErpGate) -> None:
    result = gate.evaluate(make_company(emails=["kontakt@bexio.com"]))
    assert result.status is ErpStatus.DETECTED
    assert any(m.field is MatchField.DOMAIN for m in result.matches)


def test_unqualifizierte_nennung_schliesst_konservativ_nicht_aus(gate: ErpGate) -> None:
    """Verdacht ohne harten Nachweis: Betrieb bleibt drin, aber sichtbar markiert."""
    company = make_company(documents=[make_document("Unsere Abacus-Installation läuft seit 2015.")])
    result = gate.evaluate(company)
    assert result.outcome is GateOutcome.PASS
    assert result.status is ErpStatus.SUSPECTED
    assert ERP_REVIEW_FLAG in result.flags
    assert "erp_suspected:abacus" in result.flags


def test_aggressives_profil_wuerde_den_verdacht_ausschliessen() -> None:
    """Gegenprobe: die Sensitivitaet ist wirksam, nicht dekorativ."""
    rules = load_erp_rules()
    streng = rules.model_copy(
        update={
            "sensitivity": ErpSensitivity(
                profile="aggressive", suspected_outcome=GateOutcome.REJECT
            )
        }
    )
    company = make_company(documents=[make_document("Unsere Abacus-Installation läuft seit 2015.")])
    result = ErpGate(rules=streng).evaluate(company)
    assert result.outcome is GateOutcome.REJECT
    assert result.status is ErpStatus.SUSPECTED


# -- Fehlalarmschutz bei mehrdeutigen Namen ------------------------------- #


@pytest.mark.parametrize(
    "text",
    [
        "Ich sage Ihnen: das lohnt sich. Die Sage vom Vierwaldstättersee.",
        "Ihre Ansprechperson: Klara Meier, Sekretariat.",
        "Wir liefern Bananen und Zitrusfrüchte an den Detailhandel.",
        "Topal ist ein Ortsteil, kein Thema für uns.",
    ],
)
def test_mehrdeutige_namen_loesen_nichts_aus(gate: ErpGate, text: str) -> None:
    result = gate.evaluate(make_company(documents=[make_document(text)]))
    assert result.outcome is GateOutcome.PASS
    assert result.vendors == ()


# -- Positive Schmerzsignale ---------------------------------------------- #


def test_office_stelleninserat_zeigt_erp_abwesenheit(gate: ErpGate, page: PageLoader) -> None:
    company = make_company(
        documents=[make_document(page("job_ad_sachbearbeiterin"), DocumentKind.JOB_POSTING)]
    )
    result = gate.evaluate(company)
    assert result.status is ErpStatus.ABSENCE_INDICATED
    assert {"excel", "ms_office"} <= set(result.pain_signals)
    assert result.absence_signal > 0.5


def test_freemail_als_firmenadresse_ist_ein_signal(gate: ErpGate) -> None:
    result = gate.evaluate(make_company(emails=["info@bluewin.ch"]))
    assert result.status is ErpStatus.ABSENCE_INDICATED
    assert result.free_mail_domains == ("bluewin.ch",)
    assert "freemail_domain" in result.flags


def test_schmerzsignale_schliessen_nie_aus(gate: ErpGate, page: PageLoader) -> None:
    company = make_company(
        emails=["info@gmx.ch"],
        documents=[make_document(page("website_fahrschule"))],
    )
    result = gate.evaluate(company)
    assert result.outcome is GateOutcome.PASS
    assert all(m.severity is Severity.INFO for m in result.matches)


def test_erkanntes_erp_nullt_das_abwesenheitssignal(gate: ErpGate) -> None:
    company = make_company(
        emails=["info@bluewin.ch"],
        documents=[make_document("Excel-Listen und bexio im Einsatz.")],
    )
    result = gate.evaluate(company)
    assert result.status is ErpStatus.DETECTED
    assert result.absence_signal == 0.0


def test_abwesenheitssignal_bleibt_im_intervall(gate: ErpGate) -> None:
    text = (
        "Excel-Tabelle, MS-Office, Word-Kenntnisse, Outlook, Access-Datenbank, "
        "LibreOffice, handschriftlich, papierbasiert, Office-Kenntnisse, Excel-Liste"
    )
    result = gate.evaluate(
        make_company(emails=["info@bluewin.ch"], documents=[make_document(text)])
    )
    assert 0.0 <= result.absence_signal <= 1.0


def test_fehlende_signale_werden_als_dunkelziffer_vermerkt(gate: ErpGate) -> None:
    result = gate.evaluate(make_company(documents=[make_document("Familienbetrieb seit 1954.")]))
    assert result.status is ErpStatus.NO_SIGNAL
    assert any("Dunkelziffer" in note for note in result.notes)


def test_treffer_tragen_beleg_und_quelle(gate: ErpGate) -> None:
    company = make_company(documents=[make_document("Wir fakturieren mit bexio.")])
    for match in gate.evaluate(company).matches:
        assert match.matched_text in match.context_quote
        assert match.source_url == "https://beispiel-betrieb.ch/seite"


def test_gate_ist_reproduzierbar(gate: ErpGate, page: PageLoader) -> None:
    company = make_company(documents=[make_document(page("job_ad_sachbearbeiterin"))])
    assert gate.evaluate(company) == gate.evaluate(company)
