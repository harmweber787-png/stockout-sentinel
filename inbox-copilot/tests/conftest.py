"""Gemeinsame Testinfrastruktur fuer Phase 1b: Fixture-Loader, Fake-Gmail, PDF-Bau.

Alle Daten sind synthetisch. Es gibt keinen Netzzugriff.
"""

from __future__ import annotations

import io
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

from inbox_copilot.config import Settings
from inbox_copilot.schemas import CustomerConfig

FIXTURES = Path(__file__).parent / "fixtures"

#: Gmail-IDs der synthetischen Beat-Meier-Nachricht (siehe fixtures/).
BEAT_MEIER_GMAIL_ID = "18f0a1b2c3d4e5f6"
BEAT_MEIER_THREAD_ID = "18f0a1b2c3d4e500"
#: Signatur aus Teil 1 - identisch mit tests/test_pipeline.py.
SIGNATUR_WEBER = "Hans Weber\nWeber Bau GmbH\nDietikon"


def load_fixture(name: str) -> dict[str, Any]:
    daten = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(daten, dict)
    return daten


def make_pdf(texts: list[str]) -> bytes:
    """Erzeugt ein PDF mit einer Seite pro Eintrag; leerer Text = kein Textlayer."""
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=595, height=842)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        font_ref = writer._add_object(font)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        if text:
            inhalt = StreamObject()
            inhalt._data = f"BT /F1 12 Tf 72 770 Td ({text}) Tj ET".encode("latin-1")
            page[NameObject("/Contents")] = writer._add_object(inhalt)
    puffer = io.BytesIO()
    writer.write(puffer)
    return puffer.getvalue()


class FakeGmail:
    """In-Memory-Gmail, das ``GmailAdapterLike`` erfuellt und Aufrufe zaehlt."""

    def __init__(
        self,
        messages: dict[str, dict[str, Any]] | None = None,
        threads: dict[str, dict[str, Any]] | None = None,
        attachments: dict[str, bytes] | None = None,
    ) -> None:
        self.messages = dict(messages or {})
        self.threads = dict(threads or {})
        self.attachments = dict(attachments or {})
        self.calls: Counter[str] = Counter()
        self.label_ids: dict[str, str] = {}
        self.message_labels: dict[str, set[str]] = {}
        self.drafts: list[dict[str, str]] = []
        self.raise_on_get: set[str] = set()

    async def ensure_labels(self) -> dict[str, str]:
        self.calls["ensure_labels"] += 1
        for name in (
            "AI/10-Triaged",
            "AI/20-Draft-Bereit",
            "AI/21-Draft-Platzhalter",
            "AI/99-Achtung-Chef",
        ):
            self.label_ids.setdefault(name, f"Label_{len(self.label_ids) + 1}")
        return dict(self.label_ids)

    def seed_label_ids(self, label_ids: dict[str, str]) -> None:
        self.label_ids = dict(label_ids)

    async def list_candidates(self) -> list[str]:
        self.calls["list_candidates"] += 1
        return list(self.messages)

    async def get_message(self, message_id: str) -> dict[str, Any]:
        self.calls["get_message"] += 1
        if message_id in self.raise_on_get:
            raise RuntimeError("Gmail API 500 fuer b.meier@bauleitung-meier.ch")
        return self.messages[message_id]

    async def get_thread(self, thread_id: str) -> dict[str, Any]:
        self.calls["get_thread"] += 1
        return self.threads[thread_id]

    async def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        self.calls["get_attachment"] += 1
        return self.attachments[attachment_id]

    async def thread_has_draft(self, thread_id: str) -> bool:
        self.calls["thread_has_draft"] += 1
        thread = self.threads.get(thread_id, {"messages": []})
        return any(
            "DRAFT" in (m.get("labelIds") or []) for m in thread.get("messages", [])
        )

    async def labels(self, message_id: str) -> set[str]:
        self.calls["labels"] += 1
        return set(self.message_labels.get(message_id, set()))

    async def add_label(self, message_id: str, label: str) -> None:
        self.calls["add_label"] += 1
        self.message_labels.setdefault(message_id, set()).add(label)

    async def create_draft(
        self,
        *,
        thread_id: str,
        subject: str,
        html_body: str,
        plain_body: str,
        to: str | None = None,
        in_reply_to: str | None = None,
        references: str | None = None,
    ) -> str:
        self.calls["create_draft"] += 1
        self.drafts.append(
            {
                "thread_id": thread_id,
                "subject": subject,
                "html_body": html_body,
                "plain_body": plain_body,
                "to": to or "",
                "in_reply_to": in_reply_to or "",
                "references": references or "",
            }
        )
        return f"draft-{len(self.drafts)}"

    def total_calls(self) -> int:
        return sum(self.calls.values())


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        anthropic_api_key=SecretStr("test-key"),
        mail_client="gmail",
        gmail_state_path=tmp_path / "state" / "gmail_state.json",
        gmail_token_path=tmp_path / "secrets" / "token.json",
        gmail_backoff_base_s=0.0,
    )


@pytest.fixture
def weber_customer() -> CustomerConfig:
    return CustomerConfig(
        company_name="Weber Bau GmbH",
        owner_name="Hans Weber",
        location="Dietikon",
        tone_of_voice="kurz, konkret, bodenstaendig",
        signature_block=SIGNATUR_WEBER,
        mail_client="gmail",
    )
