"""Tests der Regel-Modelle und der ausgelieferten YAML-Kataloge."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kmu_discovery.config.rules import (
    ErpRules,
    ErpSensitivity,
    ErpVendor,
    LiabilityDomain,
    LiabilityRules,
    LiabilitySensitivity,
    TermPattern,
    load_erp_rules,
    load_liability_rules,
)
from kmu_discovery.gates.text import MatchMode
from kmu_discovery.models import GateOutcome

#: Die sechs Haftungsfelder aus dem K.o.-Katalog des Projekts.
ERWARTETE_DOMAINS = {
    "gesundheit",
    "recht",
    "bau_handwerk",
    "personalverleih",
    "treuhand",
    "finanz",
}


def test_haftungskatalog_deckt_alle_ko_felder_ab() -> None:
    rules = load_liability_rules()
    assert {domain.id for domain in rules.domains} == ERWARTETE_DOMAINS


def test_jede_haftungsdomain_hat_noga_und_begriffe() -> None:
    for domain in load_liability_rules().domains:
        assert domain.noga_reject_prefixes, f"{domain.id}: kein NOGA-Praefix"
        assert any(term.decisive for term in domain.terms), f"{domain.id}: kein harter Begriff"
        assert domain.rationale.strip(), f"{domain.id}: keine Begruendung"


def test_veto_begriffe_sind_mehrwortig() -> None:
    """Einwortige Vetos wuerden echte Treffer wegdruecken statt Zulieferer."""
    for domain in load_liability_rules().domains:
        for veto in domain.veto_terms:
            assert " " in veto, f"{domain.id}: einwortiges Veto {veto!r}"


def test_noga_praefixe_werden_notationsunabhaengig_normalisiert() -> None:
    domain = LiabilityDomain(
        id="test", label="Test", rationale="x", noga_reject_prefixes=("86.21", "87")
    )
    assert domain.noga_reject_prefixes == ("8621", "87")


def test_noga_praefix_darf_nicht_doppelt_gefuehrt_werden() -> None:
    with pytest.raises(ValidationError, match="doppelt"):
        LiabilityDomain(
            id="test",
            label="Test",
            rationale="x",
            noga_reject_prefixes=("86",),
            noga_review_prefixes=("86",),
        )


def test_domain_ids_muessen_eindeutig_sein() -> None:
    domain = LiabilityDomain(id="test", label="T", rationale="x", noga_reject_prefixes=("86",))
    with pytest.raises(ValidationError, match="doppelte Domain-ID"):
        LiabilityRules(version="1", domains=(domain, domain))


def test_term_pattern_verlangt_genau_einen_matcher() -> None:
    with pytest.raises(ValidationError, match="genau eines"):
        TermPattern(id="x", term="a", regex="a")
    with pytest.raises(ValidationError, match="genau eines"):
        TermPattern(id="x")


def test_regex_mit_grossbuchstaben_wird_abgelehnt() -> None:
    """Regexe laufen gegen den gefalteten (kleingeschriebenen) Text."""
    with pytest.raises(ValidationError, match="Grossbuchstaben"):
        TermPattern(id="x", regex="Abacus")


def test_regex_mit_umlaut_wird_abgelehnt() -> None:
    with pytest.raises(ValidationError, match="Umlaute"):
        TermPattern(id="x", regex="prämien")


def test_begriff_wird_gefaltet() -> None:
    assert TermPattern(id="x", term="Zahnärztin").folded_term == "zahnaerztin"


def test_mehrdeutiger_anbieter_darf_keinen_blossen_namen_fuehren() -> None:
    """'Sage' und 'Klara' sind im Deutschen Alltagswoerter bzw. Vornamen."""
    with pytest.raises(ValidationError, match="qualifizierte Muster"):
        ErpVendor(
            id="sage",
            label="Sage",
            ambiguous=True,
            patterns=(TermPattern(id="sage_name", term="Sage"),),
        )


def test_anbieter_ohne_muster_und_domain_wird_abgelehnt() -> None:
    with pytest.raises(ValidationError, match="weder Muster noch Domains"):
        ErpVendor(id="leer", label="Leer", patterns=())


def test_anbieter_ids_muessen_eindeutig_sein() -> None:
    vendor = ErpVendor(
        id="abacus", label="Abacus", patterns=(TermPattern(id="a", term="Abacus"),)
    )
    with pytest.raises(ValidationError, match="doppelte Anbieter-ID"):
        ErpRules(version="1", vendors=(vendor, vendor))


def test_erp_katalog_enthaelt_die_im_briefing_genannten_anbieter() -> None:
    vendors = {vendor.id for vendor in load_erp_rules().vendors}
    pflicht = {
        "abacus", "abaninja", "bexio", "klara", "swiss21", "sage", "sap",
        "dynamics", "odoo", "infoniqa", "sorba", "baubit", "bauplus",
        "tocco", "vertec",
    }
    assert pflicht <= vendors


def test_mehrdeutige_anbieter_im_katalog_sind_als_solche_markiert() -> None:
    by_id = {vendor.id: vendor for vendor in load_erp_rules().vendors}
    for vendor_id in ("sage", "klara", "topal", "banana"):
        assert by_id[vendor_id].ambiguous, f"{vendor_id} muss als mehrdeutig markiert sein"


def test_freemail_domains_sind_kleingeschrieben() -> None:
    rules = load_erp_rules()
    assert "bluewin.ch" in rules.free_mail_domains
    assert all(domain == domain.lower() for domain in rules.free_mail_domains)


def test_kataloge_werden_zwischengespeichert() -> None:
    assert load_liability_rules() is load_liability_rules()
    assert load_erp_rules() is load_erp_rules()


def test_standardmodus_ist_wortstamm() -> None:
    assert TermPattern(id="x", term="a").mode is MatchMode.STEM


# -- Fehlerasymmetrie ----------------------------------------------------- #


def test_haftungskatalog_faehrt_aggressiv() -> None:
    """Verpasster Ausschluss ist hier der teure Fehler."""
    sensitivity = load_liability_rules().sensitivity
    assert sensitivity.profile == "aggressive"
    assert GateOutcome.PASS not in {
        sensitivity.below_threshold_outcome,
        sensitivity.thin_evidence_outcome,
        sensitivity.vetoed_hit_outcome,
    }


def test_erp_katalog_faehrt_konservativ() -> None:
    """Fehlalarm ist hier der teure Fehler."""
    sensitivity = load_erp_rules().sensitivity
    assert sensitivity.profile == "conservative"
    assert sensitivity.suspected_outcome is GateOutcome.PASS


def test_aggressives_profil_vertraegt_kein_pass_als_zweifelsurteil() -> None:
    with pytest.raises(ValidationError, match="kein PASS"):
        LiabilitySensitivity(profile="aggressive", thin_evidence_outcome=GateOutcome.PASS)


def test_konservatives_profil_vertraegt_kein_reject_auf_verdacht() -> None:
    with pytest.raises(ValidationError, match="kein REJECT"):
        ErpSensitivity(profile="conservative", suspected_outcome=GateOutcome.REJECT)


def test_entschiedene_grenzfaelle_stehen_in_der_ausschlussliste() -> None:
    """71.1, 16.23, 25.11/25.12 und 88.91 sind entschieden, nicht mehr offen."""
    by_id = {domain.id: domain for domain in load_liability_rules().domains}
    bau = by_id["bau_handwerk"]
    gesundheit = by_id["gesundheit"]
    assert {"711", "1623", "2511", "2512"} <= set(bau.noga_reject_prefixes)
    assert bau.noga_review_prefixes == ()
    assert "8891" in gesundheit.noga_reject_prefixes
    assert set(gesundheit.noga_review_prefixes) == {"75", "4774"}
