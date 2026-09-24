"""Modul M-1 Phase 1b - ``CopilotService``: die einzige Orchestrierungsschicht.

Runner (CLI) und spaeter ein FastAPI-Endpunkt sind reine Aufrufer dieser
Klasse. Hier liegen Idempotenz-Ringpuffer, State-Datei, Metriken und der
Ablauf pro Nachricht (Gmail -> Payload -> Pipeline -> Draft/Labels).

Nichts in diesem Modul loggt oder persistiert Mail-Inhalte: State und
Metriken enthalten ausschliesslich Gmail-IDs, Hashes, Status und Zahlen.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import tempfile
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from inbox_copilot.config import Settings
from inbox_copilot.gmail_ingest import (
    build_drafter_payload,
    build_triage_payload,
    collect_attachments,
    decode_base64url_bytes,
    extract_pdf_text_safe,
    extract_text_attachment,
    should_extract,
)
from inbox_copilot.pipeline import (
    AuditLogger,
    InboxCopilotPipeline,
    LLMClientProtocol,
    sha256_hex,
)
from inbox_copilot.schemas import (
    AttachmentMeta,
    AuditRecord,
    CustomerConfig,
    DrafterPayloadV1,
    PipelineOutcome,
    ProcessingStats,
    TriagePayloadV1,
    TriageResultV1,
)

__all__ = [
    "CopilotService",
    "GmailAdapterLike",
    "StateStore",
]

_LOG = logging.getLogger("inbox_copilot.service")
_AUDIT_LOG = logging.getLogger("inbox_copilot.audit")


# ---------------------------------------------------------------------------
# Protokoll fuer den Adapter (MailAdapter + Lesezugriffe)
# ---------------------------------------------------------------------------


class GmailAdapterLike(Protocol):
    async def ensure_labels(self) -> dict[str, str]: ...

    def seed_label_ids(self, label_ids: dict[str, str]) -> None: ...

    async def list_candidates(self) -> list[str]: ...

    async def get_message(self, message_id: str) -> dict[str, Any]: ...

    async def get_thread(self, thread_id: str) -> dict[str, Any]: ...

    async def get_attachment(self, message_id: str, attachment_id: str) -> bytes: ...

    async def thread_has_draft(self, thread_id: str) -> bool: ...

    async def labels(self, message_id: str) -> set[str]: ...

    async def add_label(self, message_id: str, label: str) -> None: ...

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
    ) -> str: ...


# ---------------------------------------------------------------------------
# 5.2 StateStore mit Idempotenz-Ringpuffer (Haertung B)
# ---------------------------------------------------------------------------


class StateStore:
    """``state/gmail_state.json`` - atomar geschrieben, nur IDs und Zeitstempel."""

    def __init__(self, path: Path, *, ring_size: int) -> None:
        self._path = path
        self._ring_size = ring_size
        self.label_ids: dict[str, str] = {}
        self.processed: deque[str] = deque(maxlen=ring_size)
        self.last_run_at: str | None = None

    def load(self) -> None:
        if not self._path.exists():
            return
        try:
            daten = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _LOG.warning(json.dumps({"event": "state_unreadable"}))
            return
        self.label_ids = {
            str(k): str(v) for k, v in (daten.get("label_ids") or {}).items()
        }
        self.processed = deque(
            (str(x) for x in daten.get("processed_message_ids") or []),
            maxlen=self._ring_size,
        )
        wert = daten.get("last_run_at")
        self.last_run_at = str(wert) if wert else None

    def save(self) -> None:
        inhalt = {
            "label_ids": self.label_ids,
            "processed_message_ids": list(self.processed),
            "last_run_at": self.last_run_at,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".gmail_state.", suffix=".tmp", dir=str(self._path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(inhalt, handle, ensure_ascii=True, sort_keys=True)
            os.replace(tmp_name, self._path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise

    def is_processed(self, message_id: str) -> bool:
        return message_id in self.processed

    def mark_processed(self, message_id: str) -> None:
        if message_id in self.processed:
            return
        self.processed.append(message_id)


# ---------------------------------------------------------------------------
# 5. CopilotService
# ---------------------------------------------------------------------------


class CopilotService:
    """Orchestriert Gmail-Zugriff, Payload-Aufbau und Pipeline pro Nachricht."""

    def __init__(
        self,
        settings: Settings,
        customer: CustomerConfig,
        adapter: GmailAdapterLike,
        llm: LLMClientProtocol,
        audit: AuditLogger,
        state: StateStore,
    ) -> None:
        self._settings = settings
        self._customer = customer
        self._adapter = adapter
        self._llm = llm
        self._audit = audit
        self._state = state
        self._pipeline = InboxCopilotPipeline(settings, llm, adapter, audit)
        self._stats = ProcessingStats()
        self._latenzen: deque[int] = deque(maxlen=settings.stats_latency_window)

    # --- Lebenszyklus ---------------------------------------------------------

    async def startup(self) -> None:
        self._state.load()
        if self._state.label_ids:
            self._adapter.seed_label_ids(self._state.label_ids)
        self._state.label_ids = await self._adapter.ensure_labels()
        self._state.save()

    def stats(self) -> ProcessingStats:
        return self._stats.model_copy(deep=True)

    # --- 5.3 Ablauf pro Nachricht ---------------------------------------------------

    async def process_message(self, message_id: str) -> PipelineOutcome:
        start = time.perf_counter()
        hash_wert = sha256_hex(message_id)

        # 1. Ringpuffer - vor jedem API- und LLM-Call.
        if self._state.is_processed(message_id):
            outcome = PipelineOutcome(
                status="SKIPPED_ALREADY_PROCESSED",
                labels_set=[],
                draft_id=None,
                audit=AuditRecord(
                    message_id_hash=hash_wert,
                    thread_id_hash="",
                    status="SKIPPED_ALREADY_PROCESSED",
                ),
            )
            self._abschliessen(outcome, start, persist=False)
            return outcome

        try:
            outcome = await self._verarbeiten(message_id)
        except Exception as fehler:  # noqa: BLE001 - pro Nachricht isoliert
            code = f"UNEXPECTED_{type(fehler).__name__}"
            _LOG.error(
                json.dumps(
                    {
                        "event": "service_exception",
                        "error_type": type(fehler).__name__,
                        "message_id_hash": hash_wert,
                    }
                )
            )
            outcome = PipelineOutcome(
                status="FAILED",
                labels_set=[],
                draft_id=None,
                audit=AuditRecord(
                    message_id_hash=hash_wert,
                    thread_id_hash="",
                    status="FAILED",
                    error_code=code,
                ),
            )
        self._state.mark_processed(message_id)
        self._abschliessen(outcome, start, persist=True)
        return outcome

    async def _verarbeiten(self, message_id: str) -> PipelineOutcome:
        # 2. Nachricht laden und Stufe-1-Payload bauen.
        message_json = await self._adapter.get_message(message_id)
        groesse = int(message_json.get("sizeEstimate") or 0)

        def bauen() -> TriagePayloadV1:
            return build_triage_payload(
                message_json,
                self._customer.company_context(),
                settings=self._settings,
            )

        if groesse > self._settings.html_parse_threshold_bytes:
            try:
                triage_payload = await asyncio.wait_for(
                    asyncio.to_thread(bauen), timeout=self._settings.parse_timeout_s
                )
            except TimeoutError:
                return self._ingest_timeout(message_id, message_json)
        else:
            triage_payload = bauen()

        # 3. Closure fuer Stufe 2 - laeuft nur, wenn die Pipeline sie anfordert.
        async def drafter_payload_factory(triage: TriageResultV1) -> DrafterPayloadV1:
            thread_json = await self._adapter.get_thread(triage_payload.thread_id)
            anhang_text = await self._attachment_text(
                message_json,
                triage_payload.cleaned_body,
                triage_payload.attachments_meta,
            )
            return build_drafter_payload(
                message_json,
                thread_json,
                triage.to_summary(),
                self._customer.company_profile(),
                anhang_text,
                depth=self._settings.thread_context_depth,
                tz=self._settings.timezone,
            )

        # 4. Pipeline.
        return await self._pipeline.process(triage_payload, drafter_payload_factory)

    def _ingest_timeout(
        self, message_id: str, message_json: dict[str, Any]
    ) -> PipelineOutcome:
        return PipelineOutcome(
            status="FAILED",
            labels_set=[],
            draft_id=None,
            audit=AuditRecord(
                message_id_hash=sha256_hex(message_id),
                thread_id_hash=sha256_hex(str(message_json.get("threadId") or "")),
                status="FAILED",
                error_code="INGEST_TIMEOUT",
            ),
        )

    async def _attachment_text(
        self,
        message_json: dict[str, Any],
        cleaned_body: str,
        attachments: list[AttachmentMeta],
    ) -> str | None:
        """Laedt und extrahiert hoechstens einen Anhang; Timeout oder Fehler -> None."""
        auswahl = should_extract(cleaned_body, attachments, settings=self._settings)
        if auswahl is None:
            return None
        teile = collect_attachments(
            message_json, inline_image_max_bytes=self._settings.inline_image_max_bytes
        )
        teil = next((t for t in teile if t.meta == auswahl), None)
        if teil is None:
            return None
        if teil.inline_data:
            daten = decode_base64url_bytes(teil.inline_data)
        elif teil.attachment_id:
            daten = await self._adapter.get_attachment(
                str(message_json["id"]), teil.attachment_id
            )
        else:
            return None
        if len(daten) > self._settings.attachment_max_bytes:
            return None

        cap = self._settings.attachment_text_char_cap

        def arbeit() -> str | None:
            if auswahl.mime_type == "application/pdf":
                return extract_pdf_text_safe(
                    daten,
                    head_pages=self._settings.pdf_head_pages,
                    include_last=self._settings.pdf_include_last_page,
                    char_cap=cap,
                    max_bytes=self._settings.attachment_max_bytes,
                )
            return extract_text_attachment(daten, charset=None, char_cap=cap)

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(arbeit), timeout=self._settings.parse_timeout_s
            )
        except TimeoutError:
            _LOG.warning(
                json.dumps({"event": "gate", "code": "ATTACHMENT_PARSE_TIMEOUT"})
            )
            return None

    # --- Metriken + State ---------------------------------------------------------

    def _abschliessen(
        self, outcome: PipelineOutcome, start: float, *, persist: bool
    ) -> None:
        total_ms = int((time.perf_counter() - start) * 1000)
        outcome.audit.total_ms = total_ms
        _AUDIT_LOG.info(
            json.dumps(
                {
                    "event": "total",
                    "message_id_hash": outcome.audit.message_id_hash,
                    "status": outcome.status,
                    "total_ms": total_ms,
                },
                sort_keys=True,
            )
        )
        stats = self._stats
        stats.messages_seen += 1
        stats.by_status[outcome.status] = stats.by_status.get(outcome.status, 0) + 1
        if outcome.draft_id is not None:
            stats.drafts_created += 1
        if outcome.status.startswith("ESCALATED"):
            stats.escalations += 1
        if outcome.audit.error_code:
            stats.errors[outcome.audit.error_code] = (
                stats.errors.get(outcome.audit.error_code, 0) + 1
            )
        stats.last_message_hash_prefix = outcome.audit.message_id_hash[:8]
        self._latenzen.append(total_ms)
        sortiert = sorted(self._latenzen)
        stats.avg_total_ms = int(sum(sortiert) / len(sortiert))
        stats.p95_total_ms = sortiert[min(len(sortiert) - 1, int(len(sortiert) * 0.95))]
        if persist:
            self._state.last_run_at = datetime.now(UTC).isoformat()
            self._state.save()

    # --- 5.4 Laeufe ---------------------------------------------------------------

    async def run_once(self) -> ProcessingStats:
        self._stats.run_started_at = datetime.now(UTC)
        try:
            kandidaten = await self._adapter.list_candidates()
        except Exception as fehler:  # noqa: BLE001 - naechstes Intervall wiederholt
            _LOG.error(
                json.dumps(
                    {"event": "list_failed", "error_type": type(fehler).__name__}
                )
            )
            return self.stats()
        for message_id in kandidaten:
            await self.process_message(message_id)
        self._state.last_run_at = datetime.now(UTC).isoformat()
        self._state.save()
        return self.stats()

    async def run_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=self._settings.poll_interval_s
                )
            except TimeoutError:
                continue
