"""Tests der Kern-Datenmodelle."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from kmu_discovery.models import (
    CompanyProfile,
    DocumentKind,
    Entscheider,
    Evidence,
    GateOutcome,
    MatchField,
    SourceType,
    Standort,
    normalize_noga,
    uid_check_digit,
    uid_checksum_valid,
)
from tests_kmu_discovery.conftest import RETRIEVED_AT, make_company, make_document


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("CHE-123.456.789", "CHE-123.456.789"),
        ("che123456789", "CHE-123.456.789"),
        ("CHE 123 456 789", "CHE-123.456.789"),
    ],
)
def test_uid_wird_normalisiert(raw: str, expected: str) -> None:
    assert make_company(uid=raw).uid == expected


@pytest.mark.parametrize("raw", ["CHE-123.456.78", "DE-123456789", "123456789", ""])
def test_ungueltige_uid_wird_abgelehnt(raw: str) -> None:
    with pytest.raises(ValidationError):
        make_company(uid=raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("86.21", "8621"), ("8621", "8621"), ("41", "41"), ("86.21.0", "86210")],
)
def test_noga_normalisierung(raw: str, expected: str) -> None:
    assert normalize_noga(raw) == expected


@pytest.mark.parametrize("raw", ["A6", "8", "8621000", ""])
def test_unplausibler_noga_code_wirft(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_noga(raw)


def test_uid_pruefziffer_ist_in_sich_konsistent() -> None:
    # Die Pruefziffer-Formel ist gegen die BFS-Doku noch nicht verifiziert
    # (siehe Docstring). Getestet wird deshalb nur die Selbstkonsistenz.
    basis = "12345678"
    digit = uid_check_digit(basis)
    assert 0 <= digit <= 9
    assert uid_checksum_valid(f"CHE-{basis[0:3]}.{basis[3:6]}.{basis[6:8]}{digit}")


def test_uid_pruefziffer_verlangt_acht_ziffern() -> None:
    with pytest.raises(ValueError):
        uid_check_digit("1234")


def test_evidence_verlangt_zeitzone() -> None:
    with pytest.raises(ValidationError):
        Evidence(
            source_type=SourceType.COMPANY_WEBSITE,
            source_url="https://beispiel.ch",
            quote="Text",
            retrieved_at=datetime(2026, 9, 14),  # noqa: DTZ001 - genau das wird geprueft
        )


def test_evidence_verlangt_absolute_url() -> None:
    with pytest.raises(ValidationError):
        Evidence(
            source_type=SourceType.COMPANY_WEBSITE,
            source_url="/formulare",
            quote="Text",
            retrieved_at=RETRIEVED_AT,
        )


def test_entscheider_nur_aus_offiziellen_registern() -> None:
    """revDSG: Personendaten duerfen nicht aus einem Website-Crawl stammen."""
    website_beleg = Evidence(
        source_type=SourceType.COMPANY_WEBSITE,
        source_url="https://beispiel.ch/team",
        quote="Geschäftsführer: A. Muster",
        retrieved_at=RETRIEVED_AT,
    )
    with pytest.raises(ValidationError, match="offiziellen Registern"):
        Entscheider(name="A. Muster", evidence=website_beleg)

    register_beleg = website_beleg.model_copy(
        update={"source_type": SourceType.ZEFIX, "source_url": "https://www.zefix.ch/de/search"}
    )
    assert Entscheider(name="A. Muster", evidence=register_beleg).name == "A. Muster"


def test_standort_prueft_kanton() -> None:
    assert Standort(kanton="zh", plz="8001", ort="Zürich").kanton == "ZH"
    with pytest.raises(ValidationError):
        Standort(kanton="XX")


def test_email_domains() -> None:
    company = make_company(emails=["Info@Bluewin.CH", "sekretariat@beispiel.ch"])
    assert company.emails == ["info@bluewin.ch", "sekretariat@beispiel.ch"]
    assert company.email_domains == ["bluewin.ch", "beispiel.ch"]


def test_ungueltige_email_wird_abgelehnt() -> None:
    with pytest.raises(ValidationError):
        make_company(emails=["kein-at-zeichen"])


def test_dokumentart_bestimmt_regelfeld() -> None:
    assert make_document("x", DocumentKind.JOB_POSTING).match_field is MatchField.JOB_POSTING
    assert make_document("x", DocumentKind.REGISTER_PURPOSE).match_field is MatchField.ZWECK
    assert make_document("x", DocumentKind.IMPRESSUM).match_field is MatchField.WEBSITE


def test_gate_outcome_rang() -> None:
    assert GateOutcome.REJECT.rank > GateOutcome.REVIEW.rank > GateOutcome.PASS.rank


def test_company_profile_verbietet_unbekannte_felder() -> None:
    with pytest.raises(ValidationError):
        CompanyProfile.model_validate(
            {"uid": "CHE-100.000.001", "name": "X", "umsatz": 1_000_000}
        )
