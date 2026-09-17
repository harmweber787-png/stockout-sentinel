"""Phase 1b - CopilotService: S01-S04 (Ringpuffer, State, Fehlerisolation)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from inbox_copilot.config import Settings
from inbox_copilot.schemas import CustomerConfig
from inbox_copilot.service import CopilotService, StateStore
from tests.conftest import (
    BEAT_MEIER_GMAIL_ID,
    BEAT_MEIER_THREAD_ID,
    FakeGmail,
    load_fixture,
)
from tests.test_pipeline import CapturingAuditLogger, MockLLMClient, triage_dict


def triage_ignorieren(message_id: str) -> dict[str, Any]:
    return triage_dict(
        message_id=message_id,
        kategorie="newsletter_werbung",
        dringlichkeit="keine",
        braucht_antwort=False,
        antwort_typ="keine",
        confidence=0.97,
        routing={
            "aktion": "ignorieren",
            "label_zusatz": "keins",
            "grund": "Massenmail ohne Anliegen.",
        },
    )


def fake_mit_kopien(ids: list[str]) -> FakeGmail:
    """Beat-Meier-Fixture unter mehreren Gmail-IDs."""
    vorlage = load_fixture("beat_meier_plain.json")
    thread = load_fixture("thread_four_messages.json")
    thread["messages"] = [
        m for m in thread["messages"] if "DRAFT" not in m.get("labelIds", [])
    ]
    nachrichten = {}
    for message_id in ids:
        kopie = json.loads(json.dumps(vorlage))
        kopie["id"] = message_id
        nachrichten[message_id] = kopie
    return FakeGmail(messages=nachrichten, threads={BEAT_MEIER_THREAD_ID: thread})


def service_mit(
    settings: Settings, customer: CustomerConfig, fake: FakeGmail, llm: MockLLMClient
) -> tuple[CopilotService, StateStore]:
    state = StateStore(
        settings.gmail_state_path, ring_size=settings.processed_ring_size
    )
    service = CopilotService(
        settings, customer, fake, llm, CapturingAuditLogger(), state
    )
    return service, state


@pytest.mark.asyncio
async def test_s01_ringpuffer_zweiter_aufruf_skipped(
    settings: Settings, weber_customer: CustomerConfig
) -> None:
    fake = fake_mit_kopien([BEAT_MEIER_GMAIL_ID])
    llm = MockLLMClient(triage=[triage_ignorieren(BEAT_MEIER_GMAIL_ID)])
    service, _ = service_mit(settings, weber_customer, fake, llm)
    await service.startup()

    erster = await service.process_message(BEAT_MEIER_GMAIL_ID)
    aufrufe_adapter = fake.total_calls()
    aufrufe_llm = len(llm.calls)
    zweiter = await service.process_message(BEAT_MEIER_GMAIL_ID)

    assert erster.status == "IGNORED"
    assert zweiter.status == "SKIPPED_ALREADY_PROCESSED"
    assert fake.total_calls() == aufrufe_adapter
    assert len(llm.calls) == aufrufe_llm
    assert service.stats().by_status == {"IGNORED": 1, "SKIPPED_ALREADY_PROCESSED": 1}


@pytest.mark.asyncio
async def test_s02_ringpuffer_kapazitaet_und_state_ohne_pii(
    settings: Settings, weber_customer: CustomerConfig
) -> None:
    fake = FakeGmail()
    llm = MockLLMClient()
    service, state = service_mit(settings, weber_customer, fake, llm)
    await service.startup()

    for nummer in range(1001):
        state.mark_processed(f"gmailid{nummer:05d}")
    state.save()

    assert len(state.processed) == 1000
    assert not state.is_processed("gmailid00000")
    assert state.is_processed("gmailid00001")
    assert state.is_processed("gmailid01000")

    neu = StateStore(settings.gmail_state_path, ring_size=settings.processed_ring_size)
    neu.load()
    assert list(neu.processed) == list(state.processed)
    assert neu.label_ids == fake.label_ids

    roh = settings.gmail_state_path.read_text(encoding="utf-8")
    daten = json.loads(roh)
    assert set(daten) == {"label_ids", "processed_message_ids", "last_run_at"}
    assert "@" not in roh
    for wort in ("Maengelruege", "Fassade", "Meier", "Urdorf", "Weber"):
        assert wort not in roh
    assert not list(settings.gmail_state_path.parent.glob("*.tmp"))
    assert service.stats().messages_seen == 0


@pytest.mark.asyncio
async def test_s03_run_once_isoliert_fehler(
    settings: Settings, weber_customer: CustomerConfig
) -> None:
    ids = ["gm-eins", "gm-zwei", "gm-drei"]
    fake = fake_mit_kopien(ids)
    fake.raise_on_get.add("gm-zwei")
    llm = MockLLMClient(
        triage=[triage_ignorieren("gm-eins"), triage_ignorieren("gm-drei")]
    )
    service, state = service_mit(settings, weber_customer, fake, llm)
    await service.startup()

    stats = await service.run_once()

    assert stats.messages_seen == 3
    assert stats.by_status == {"IGNORED": 2, "FAILED": 1}
    assert stats.errors == {"UNEXPECTED_RuntimeError": 1}
    assert stats.run_started_at is not None
    assert stats.last_message_hash_prefix is not None
    assert len(stats.last_message_hash_prefix) == 8
    assert all(state.is_processed(message_id) for message_id in ids)
    assert json.loads(stats.model_dump_json())["by_status"]["FAILED"] == 1
    # Das Gmail-Fehlerdetail (mit Adresse) erreicht die Kennzahlen nicht.
    assert "@" not in stats.model_dump_json()


@pytest.mark.asyncio
async def test_s04_factory_bei_ignorieren_nicht_aufgerufen(
    settings: Settings, weber_customer: CustomerConfig
) -> None:
    fake = fake_mit_kopien([BEAT_MEIER_GMAIL_ID])
    llm = MockLLMClient(triage=[triage_ignorieren(BEAT_MEIER_GMAIL_ID)])
    service, _ = service_mit(settings, weber_customer, fake, llm)
    await service.startup()

    outcome = await service.process_message(BEAT_MEIER_GMAIL_ID)

    assert outcome.status == "IGNORED"
    assert fake.calls["get_thread"] == 0
    assert fake.calls["get_attachment"] == 0
    assert llm.drafter_calls == 0
    assert fake.calls["get_message"] == 1
