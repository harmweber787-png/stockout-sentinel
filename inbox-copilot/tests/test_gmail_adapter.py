"""Phase 1b - GmailAdapter (Google-Client gemockt): A01-A06 und H06-H08."""

from __future__ import annotations

import base64
import logging
import time
from email import message_from_bytes, policy
from typing import Any
from unittest.mock import MagicMock

import httplib2
import pytest
from googleapiclient.errors import HttpError

from inbox_copilot import gmail_ingest
from inbox_copilot import service as service_modul
from inbox_copilot.config import Settings
from inbox_copilot.gmail_adapter import GmailAdapter, MailAdapterError
from inbox_copilot.schemas import CustomerConfig
from inbox_copilot.service import CopilotService, StateStore
from tests.conftest import (
    BEAT_MEIER_GMAIL_ID,
    BEAT_MEIER_THREAD_ID,
    FakeGmail,
    load_fixture,
    make_pdf,
)
from tests.test_pipeline import (
    CapturingAuditLogger,
    MockLLMClient,
    drafter_dict,
    triage_dict,
)

# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


def http_error(status: int) -> HttpError:
    resp = httplib2.Response({"status": status})
    resp.reason = "Fehler ohne Betreff und Absender"
    return HttpError(resp, b'{"error": {"message": "generic"}}')


def mock_service() -> MagicMock:
    """``service.users().<ressource>().<methode>(...).execute()`` als MagicMock."""
    svc = MagicMock(name="gmail_service")
    return svc


def labels_api(svc: MagicMock) -> MagicMock:
    api: MagicMock = svc.users.return_value.labels.return_value
    return api


def messages_api(svc: MagicMock) -> MagicMock:
    api: MagicMock = svc.users.return_value.messages.return_value
    return api


def drafts_api(svc: MagicMock) -> MagicMock:
    api: MagicMock = svc.users.return_value.drafts.return_value
    return api


def threads_api(svc: MagicMock) -> MagicMock:
    api: MagicMock = svc.users.return_value.threads.return_value
    return api


async def kein_schlaf(_: float) -> None:
    return None


def adapter_mit(settings: Settings, svc: MagicMock) -> GmailAdapter:
    return GmailAdapter(settings, service=svc, sleep=kein_schlaf)


def triage_eskalation(message_id: str) -> dict[str, Any]:
    return triage_dict(
        message_id=message_id,
        kategorie="reklamation_maengel",
        dringlichkeit="heute",
        antwort_typ="eingangsbestaetigung",
        frist={
            "erkannt": True,
            "datum": "2026-09-25",
            "beleg": "eine Frist bis 25. September 2026 zur Nachbesserung",
            "rechtsfolge": "gesetzlich",
        },
        risiko_flags=["haftung_schaden"],
        fehlende_infos=["anhang_inhalt"],
        attachment_relevant=True,
        confidence=0.93,
        routing={
            "aktion": "eskalieren",
            "label_zusatz": "AI/99-Achtung-Chef",
            "grund": "Maengelruege mit gesetzlicher Frist.",
        },
    )


def beat_meier_fake(attachment: bytes | None = None) -> FakeGmail:
    nachricht = load_fixture("beat_meier_plain.json")
    thread = load_fixture("thread_four_messages.json")
    # Der Thread-Fixture enthaelt bewusst einen Draft (I11); fuer den
    # End-to-End-Lauf darf noch keiner existieren (Idempotenz-Pruefung).
    thread["messages"] = [
        m for m in thread["messages"] if "DRAFT" not in m.get("labelIds", [])
    ]
    return FakeGmail(
        messages={BEAT_MEIER_GMAIL_ID: nachricht},
        threads={BEAT_MEIER_THREAD_ID: thread},
        attachments={"ANGjdJ_att_maengelliste": attachment or make_pdf(["Protokoll"])},
    )


def service_mit(
    settings: Settings,
    customer: CustomerConfig,
    fake: FakeGmail,
    llm: MockLLMClient,
) -> CopilotService:
    state = StateStore(
        settings.gmail_state_path, ring_size=settings.processed_ring_size
    )
    return CopilotService(settings, customer, fake, llm, CapturingAuditLogger(), state)


# ---------------------------------------------------------------------------
# A01 / H06 / H07 - Labels
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a01_ensure_labels_legt_nur_fehlende_an(settings: Settings) -> None:
    svc = mock_service()
    labels_api(svc).list.return_value.execute.return_value = {
        "labels": [
            {"id": "Label_10", "name": settings.label_triaged},
            {"id": "Label_20", "name": settings.label_draft_ready},
            {"id": "INBOX", "name": "INBOX"},
        ]
    }
    labels_api(svc).create.return_value.execute.side_effect = [
        {"id": "Label_21"},
        {"id": "Label_99"},
    ]
    adapter = adapter_mit(settings, svc)

    ids = await adapter.ensure_labels()

    assert ids == {
        settings.label_triaged: "Label_10",
        settings.label_draft_ready: "Label_20",
        settings.label_draft_placeholder: "Label_21",
        settings.label_escalate: "Label_99",
    }
    angelegt = [c.kwargs["body"]["name"] for c in labels_api(svc).create.call_args_list]
    assert angelegt == [settings.label_draft_placeholder, settings.label_escalate]


@pytest.mark.asyncio
async def test_h07_farben_nur_aus_palette(settings: Settings) -> None:
    svc = mock_service()
    labels_api(svc).list.return_value.execute.return_value = {"labels": []}
    labels_api(svc).create.return_value.execute.side_effect = [
        {"id": "L10"},
        {"id": "L20"},
        {"id": "L21"},
        {"id": "L99"},
    ]
    adapter = adapter_mit(settings, svc)

    await adapter.ensure_labels()

    bodies = {
        c.kwargs["body"]["name"]: c.kwargs["body"]
        for c in labels_api(svc).create.call_args_list
    }
    assert "color" not in bodies[settings.label_triaged]
    assert "color" not in bodies[settings.label_draft_ready]
    assert bodies[settings.label_escalate]["color"] == {
        "backgroundColor": "#fb4c2f",
        "textColor": "#ffffff",
    }
    assert bodies[settings.label_draft_placeholder]["color"] == {
        "backgroundColor": "#ffc8af",
        "textColor": "#000000",
    }
    for body in bodies.values():
        assert body["labelListVisibility"] == "labelShow"
        assert body["messageListVisibility"] == "show"


@pytest.mark.asyncio
async def test_h06_farbe_abgelehnt_label_ohne_farbe(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    svc = mock_service()
    labels_api(svc).list.return_value.execute.return_value = {
        "labels": [
            {"id": "L10", "name": settings.label_triaged},
            {"id": "L20", "name": settings.label_draft_ready},
            {"id": "L21", "name": settings.label_draft_placeholder},
        ]
    }
    labels_api(svc).create.return_value.execute.side_effect = [
        http_error(400),
        {"id": "L99"},
    ]
    adapter = adapter_mit(settings, svc)

    with caplog.at_level(logging.WARNING, logger="inbox_copilot.gmail"):
        ids = await adapter.ensure_labels()

    assert ids[settings.label_escalate] == "L99"
    aufrufe = labels_api(svc).create.call_args_list
    assert "color" in aufrufe[0].kwargs["body"]
    assert "color" not in aufrufe[1].kwargs["body"]
    assert "LABEL_COLOR_REJECTED" in "\n".join(caplog.messages)


# ---------------------------------------------------------------------------
# A02 - Draft-MIME
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a02_create_draft_mime(settings: Settings) -> None:
    svc = mock_service()
    drafts_api(svc).create.return_value.execute.return_value = {"id": "r-draft-1"}
    adapter = adapter_mit(settings, svc)

    draft_id = await adapter.create_draft(
        thread_id="thread-abc",
        subject="[PRÜFEN] Re: Bauvorhaben Urdorf – Fassade",
        html_body="<p>Grüezi</p>",
        plain_body="Grüezi",
        to="Beat Meier <b.meier@bauleitung-meier.ch>",
        in_reply_to="<sia118-0001@bauleitung-meier.ch>",
        references="<offerte-0815@weber-bau.example>",
    )

    assert draft_id == "r-draft-1"
    body = drafts_api(svc).create.call_args.kwargs["body"]
    assert body["message"]["threadId"] == "thread-abc"
    roh = body["message"]["raw"]
    mime = message_from_bytes(
        gmail_ingest.decode_base64url_bytes(roh), policy=policy.default
    )
    assert mime["To"] == "Beat Meier <b.meier@bauleitung-meier.ch>"
    assert mime["Subject"] == "[PRÜFEN] Re: Bauvorhaben Urdorf – Fassade"
    assert mime["In-Reply-To"] == "<sia118-0001@bauleitung-meier.ch>"
    assert mime["References"] == (
        "<offerte-0815@weber-bau.example> <sia118-0001@bauleitung-meier.ch>"
    )
    assert mime["From"] is None
    assert mime.get_content_type() == "multipart/alternative"
    teile = [t.get_content_type() for t in mime.walk() if not t.is_multipart()]
    assert teile == ["text/plain", "text/html"]
    for teil in mime.walk():
        if not teil.is_multipart():
            assert teil["Content-Transfer-Encoding"] == "quoted-printable"
            assert teil.get_content_charset() == "utf-8"
    # raw ist base64url und wieder dekodierbar
    assert base64.urlsafe_b64decode(roh).startswith(
        b"To:"
    ) or b"Subject:" in base64.urlsafe_b64decode(roh)


# ---------------------------------------------------------------------------
# A03 - thread_has_draft
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a03_thread_has_draft(settings: Settings) -> None:
    svc = mock_service()
    threads_api(svc).get.return_value.execute.side_effect = [
        {
            "messages": [
                {"id": "a", "labelIds": ["INBOX"]},
                {"id": "b", "labelIds": ["DRAFT"]},
            ]
        },
        {
            "messages": [
                {"id": "a", "labelIds": ["INBOX"]},
                {"id": "b", "labelIds": ["SENT"]},
            ]
        },
    ]
    adapter = adapter_mit(settings, svc)

    assert await adapter.thread_has_draft("t1") is True
    assert await adapter.thread_has_draft("t2") is False


# ---------------------------------------------------------------------------
# A04 / A05 - Backoff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a04_backoff_429_dritter_versuch_erfolgreich(settings: Settings) -> None:
    svc = mock_service()
    messages_api(svc).get.return_value.execute.side_effect = [
        http_error(429),
        http_error(429),
        {"id": "m1", "threadId": "t1", "payload": {}},
    ]
    schlaf: list[float] = []

    async def merke(sekunden: float) -> None:
        schlaf.append(sekunden)

    adapter = GmailAdapter(settings, service=svc, sleep=merke)

    antwort = await adapter.get_message("m1")

    assert antwort["id"] == "m1"
    assert messages_api(svc).get.return_value.execute.call_count == 3
    assert schlaf == [0.0, 0.0]  # Basis 0 in Tests; Faktor 1, 2 (, 4)


@pytest.mark.asyncio
async def test_a05_drei_5xx_ergeben_mailadaptererror(settings: Settings) -> None:
    svc = mock_service()
    messages_api(svc).get.return_value.execute.side_effect = [
        http_error(503),
        http_error(503),
        http_error(503),
    ]
    adapter = adapter_mit(settings, svc)

    with pytest.raises(MailAdapterError) as info:
        await adapter.get_message("m1")

    assert info.value.code == "GMAIL_HTTP_503"
    assert info.value.status == 503
    assert "Betreff" not in str(info.value)
    assert "@" not in str(info.value)
    assert info.value.__cause__ is None
    assert messages_api(svc).get.return_value.execute.call_count == 3


# ---------------------------------------------------------------------------
# A06 - End-to-End mit Fake-Gmail und Mock-LLM
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a06_end_to_end_beat_meier(
    settings: Settings, weber_customer: CustomerConfig
) -> None:
    fake = beat_meier_fake()
    llm = MockLLMClient(
        triage=[triage_eskalation(BEAT_MEIER_GMAIL_ID)],
        drafter=[
            drafter_dict(
                message_id=BEAT_MEIER_GMAIL_ID,
                subject_reply="Bauvorhaben Urdorf – Fassade",
            )
        ],
    )
    service = service_mit(settings, weber_customer, fake, llm)
    await service.startup()

    outcome = await service.process_message(BEAT_MEIER_GMAIL_ID)

    assert outcome.status == "ESCALATED_WITH_DRAFT"
    assert fake.message_labels[BEAT_MEIER_GMAIL_ID] == {
        settings.label_triaged,
        settings.label_escalate,
        settings.label_draft_placeholder,
    }
    assert len(fake.drafts) == 1
    draft = fake.drafts[0]
    assert draft["subject"] == "[PRÜFEN] Re: Bauvorhaben Urdorf – Fassade"
    assert draft["thread_id"] == BEAT_MEIER_THREAD_ID
    html = draft["html_body"]
    assert html.index("background-color:#fff3cd") < html.index("<hr>")
    assert '<mark style="background:#ffe66d">[[RUECKMELDUNG_BIS' in html
    assert draft["to"] == "Beat Meier <b.meier@bauleitung-meier.ch>"
    assert draft["in_reply_to"] == "<sia118-0001@bauleitung-meier.ch>"
    assert draft["references"] == "<offerte-0815@weber-bau.example>"
    # Der Anhang wurde geladen und dem Drafter als Auszug uebergeben.
    assert fake.calls["get_attachment"] == 1
    drafter_call = llm.calls[1]
    assert drafter_call.user_payload["extracted_attachment_text"] is not None
    assert "[AUSZUG" in drafter_call.user_payload["extracted_attachment_text"]
    assert outcome.audit.total_ms >= 0


# ---------------------------------------------------------------------------
# H08 - Parse-Timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_h08_parse_timeout_liefert_none_und_draft_entsteht(
    settings: Settings,
    weber_customer: CustomerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def blockiert(*_: Any, **__: Any) -> str | None:
        time.sleep(1.0)
        return "haette nie ankommen duerfen"

    monkeypatch.setattr(service_modul, "extract_pdf_text_safe", blockiert)
    schnell = settings.model_copy(update={"parse_timeout_s": 0.05})
    fake = beat_meier_fake()
    entwurf = drafter_dict(
        message_id=BEAT_MEIER_GMAIL_ID,
        subject_reply="Bauvorhaben Urdorf – Fassade",
        plain_body=(
            "Grüezi Herr Meier\n\n"
            "besten Dank für Ihre Nachricht betreffend die Fassade in Urdorf. "
            "Den Anhang prüfen wir: [[ANHANG_BEZUG: Anhang prüfen]]. "
            "Eine Rückmeldung erhalten Sie bis "
            "[[RUECKMELDUNG_BIS: Datum wählen]].\n\n"
            "Freundliche Grüsse\n\nHans Weber\nWeber Bau GmbH\nDietikon"
        ),
        platzhalter=[
            {"key": "ANHANG_BEZUG", "hinweis": "Anhang prüfen"},
            {"key": "RUECKMELDUNG_BIS", "hinweis": "Datum wählen"},
        ],
    )
    llm = MockLLMClient(
        triage=[triage_eskalation(BEAT_MEIER_GMAIL_ID)], drafter=[entwurf]
    )
    service = service_mit(schnell, weber_customer, fake, llm)
    await service.startup()

    start = time.perf_counter()
    outcome = await service.process_message(BEAT_MEIER_GMAIL_ID)
    dauer = time.perf_counter() - start

    assert dauer < 0.9, "Timeout hat nicht gegriffen"
    assert llm.calls[1].user_payload["extracted_attachment_text"] is None
    assert outcome.status == "ESCALATED_WITH_DRAFT"
    assert "[[ANHANG_BEZUG" in fake.drafts[0]["plain_body"]
