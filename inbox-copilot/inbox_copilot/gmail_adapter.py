"""Modul M-1 Phase 1b - ``GmailAdapter`` (Implementierung von ``MailAdapter``).

Asynchrone Fassade ueber den synchronen Google-Client. Jeder API-Aufruf
laeuft in ``asyncio.to_thread``; 429 und 5xx werden mit exponentiellem
Backoff wiederholt, alle anderen Fehler sofort als ``MailAdapterError``
mit reinem Fehlercode weitergereicht - ohne Betreff, Absender oder Body.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any, Protocol

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from inbox_copilot.config import (
    GMAIL_DRAFT_LABEL,
    GMAIL_RATE_LIMIT_REASONS,
    GMAIL_RETRY_STATUS,
    GMAIL_USER_ID,
    Settings,
)
from inbox_copilot.gmail_ingest import decode_base64url_bytes

__all__ = [
    "GmailAdapter",
    "GmailAuth",
    "MailAdapterError",
    "OAuthDesktopAuth",
    "build_reply_mime",
]

_LOG = logging.getLogger("inbox_copilot.gmail")


class MailAdapterError(Exception):
    """Postfach-Fehler. Traegt nur einen Code und den HTTP-Status."""

    def __init__(self, code: str, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


# ---------------------------------------------------------------------------
# 4.1 Auth
# ---------------------------------------------------------------------------


class GmailAuth(Protocol):
    """Liefert einen fertig autorisierten Gmail-Service (googleapiclient Resource)."""

    def service(self) -> Any: ...


class OAuthDesktopAuth:
    """InstalledAppFlow (Desktop-App) mit Token-Refresh.

    Der Token wird ausschliesslich unter ``gmail_token_path`` abgelegt.
    Fuer Phase 2 (Service Account, Domain-wide Delegation) austauschbar.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _credentials(self, *, interactive: bool) -> Any:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        token_path = self._settings.gmail_token_path
        creds: Any = None
        if token_path.exists():
            laden: Any = Credentials.from_authorized_user_file
            creds = laden(str(token_path), self._settings.gmail_scopes)
        if creds is not None and creds.valid:
            return creds
        if creds is not None and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        elif interactive:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self._settings.gmail_credentials_path), self._settings.gmail_scopes
            )
            creds = flow.run_local_server(port=0)
        else:
            raise MailAdapterError("GMAIL_AUTH_REQUIRED")
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")
        return creds

    def run_flow(self) -> None:
        """Interaktiver Browser-Flow (``--auth``)."""
        self._credentials(interactive=True)

    def service(self) -> Any:
        creds = self._credentials(interactive=False)
        return build("gmail", "v1", credentials=creds, cache_discovery=False)


# ---------------------------------------------------------------------------
# MIME-Aufbau des Drafts (4.4)
# ---------------------------------------------------------------------------


def build_reply_mime(
    *,
    subject: str,
    plain_body: str,
    html_body: str,
    to: str | None,
    in_reply_to: str | None,
    references: str | None,
) -> EmailMessage:
    """``multipart/alternative`` (plain + html), quoted-printable, kein ``From``."""
    nachricht = EmailMessage()
    if to:
        nachricht["To"] = to
    nachricht["Subject"] = subject
    if in_reply_to:
        nachricht["In-Reply-To"] = in_reply_to
        nachricht["References"] = (
            f"{references} {in_reply_to}".strip() if references else in_reply_to
        )
    nachricht.set_content(
        plain_body, subtype="plain", charset="utf-8", cte="quoted-printable"
    )
    nachricht.add_alternative(
        html_body, subtype="html", charset="utf-8", cte="quoted-printable"
    )
    return nachricht


# ---------------------------------------------------------------------------
# 4. Adapter
# ---------------------------------------------------------------------------


class GmailAdapter:
    """``MailAdapter`` fuer Gmail plus die Lese-Methoden des Services."""

    def __init__(
        self,
        settings: Settings,
        *,
        auth: GmailAuth | None = None,
        service: Any | None = None,
        sleep: Callable[[float], Any] | None = None,
    ) -> None:
        if service is None and auth is None:
            raise ValueError("GmailAdapter braucht auth oder service")
        self._settings = settings
        self._auth = auth
        self._service = service
        self._label_ids: dict[str, str] = {}
        self._sleep = sleep

    # --- Infrastruktur ------------------------------------------------------

    def _svc(self) -> Any:
        if self._service is None and self._auth is not None:
            self._service = self._auth.service()
        return self._service

    async def _call(self, request_factory: Callable[[Any], Any]) -> Any:
        """Fuehrt ``request_factory(service).execute()`` mit Backoff aus.

        Wiederholt wird bei 429, 5xx und 403 mit Rate-Limit-Grund; jedes
        andere 403 ist sofort ``FORBIDDEN``. Wartezeit
        ``min(base * 2**n, max) * uniform(*jitter)``.
        """
        max_retries = max(0, self._settings.gmail_max_retries)
        for versuch in range(max_retries + 1):
            try:
                return await asyncio.to_thread(
                    lambda: request_factory(self._svc()).execute()
                )
            except HttpError as fehler:
                status = _http_status(fehler)
                if status == 403 and not _ist_rate_limit(fehler):
                    raise MailAdapterError("FORBIDDEN", status) from None
                wiederholbar = status in GMAIL_RETRY_STATUS or status == 403
                if not wiederholbar or versuch >= max_retries:
                    raise MailAdapterError(f"GMAIL_HTTP_{status}", status) from None
                wartezeit = min(
                    self._settings.gmail_backoff_base_s * (2**versuch),
                    self._settings.gmail_backoff_max_s,
                ) * random.uniform(*self._settings.gmail_jitter)
                _LOG.warning(
                    json.dumps(
                        {
                            "event": "gmail_retry",
                            "status": status,
                            "attempt": versuch + 1,
                            "wait_s": round(wartezeit, 3),
                        }
                    )
                )
                schlaf = self._sleep if self._sleep is not None else asyncio.sleep
                await schlaf(wartezeit)
            except (OSError, TimeoutError):
                raise MailAdapterError("GMAIL_TRANSPORT") from None
        raise MailAdapterError("GMAIL_RETRY_EXHAUSTED")  # pragma: no cover

    def seed_label_ids(self, label_ids: dict[str, str]) -> None:
        """Name->ID-Map aus dem State uebernehmen (vor ``ensure_labels``)."""
        self._label_ids = dict(label_ids)

    @property
    def label_ids(self) -> dict[str, str]:
        return dict(self._label_ids)

    # --- 4.2 Labels (Haertung A) -------------------------------------------------

    def _required_labels(self) -> list[str]:
        return [
            self._settings.label_triaged,
            self._settings.label_draft_ready,
            self._settings.label_draft_placeholder,
            self._settings.label_escalate,
        ]

    async def ensure_labels(self) -> dict[str, str]:
        antwort = await self._call(
            lambda svc: svc.users().labels().list(userId=GMAIL_USER_ID)
        )
        vorhanden: dict[str, str] = {
            str(label["name"]): str(label["id"]) for label in antwort.get("labels", [])
        }
        for name in self._required_labels():
            if name in vorhanden:
                continue
            vorhanden[name] = await self._create_label(name)
        self._label_ids = {name: vorhanden[name] for name in self._required_labels()}
        return dict(self._label_ids)

    async def _create_label(self, name: str) -> str:
        body: dict[str, Any] = {
            "name": name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        }
        farbe = self._settings.label_colors.get(name)
        if farbe is not None:
            mit_farbe = {
                **body,
                "color": {"backgroundColor": farbe[0], "textColor": farbe[1]},
            }
            try:
                angelegt = await self._call(
                    lambda svc: (
                        svc.users()
                        .labels()
                        .create(userId=GMAIL_USER_ID, body=mit_farbe)
                    )
                )
                return str(angelegt["id"])
            except MailAdapterError as fehler:
                if fehler.status != 400:
                    raise
                _LOG.warning(
                    json.dumps(
                        {"event": "gate", "code": "LABEL_COLOR_REJECTED", "label": name}
                    )
                )
        angelegt = await self._call(
            lambda svc: svc.users().labels().create(userId=GMAIL_USER_ID, body=body)
        )
        return str(angelegt["id"])

    # --- 4.3 Nachrichten ---------------------------------------------------------

    async def list_candidates(self) -> list[str]:
        ids: list[str] = []
        page_token: str | None = None
        limit = self._settings.poll_max_messages
        while len(ids) < limit:
            antwort = await self._call(
                self._list_request(page_token, min(limit - len(ids), limit))
            )
            ids.extend(str(m["id"]) for m in antwort.get("messages", []))
            page_token = antwort.get("nextPageToken")
            if not page_token:
                break
        return ids[:limit]

    def _list_request(
        self, page_token: str | None, max_results: int
    ) -> Callable[[Any], Any]:
        def anfrage(svc: Any) -> Any:
            return (
                svc.users()
                .messages()
                .list(
                    userId=GMAIL_USER_ID,
                    q=self._settings.poll_query,
                    maxResults=max_results,
                    pageToken=page_token,
                )
            )

        return anfrage

    async def get_message(self, message_id: str) -> dict[str, Any]:
        antwort = await self._call(
            lambda svc: (
                svc.users()
                .messages()
                .get(userId=GMAIL_USER_ID, id=message_id, format="full")
            )
        )
        return dict(antwort)

    async def get_thread(self, thread_id: str) -> dict[str, Any]:
        antwort = await self._call(
            lambda svc: (
                svc.users()
                .threads()
                .get(userId=GMAIL_USER_ID, id=thread_id, format="full")
            )
        )
        return dict(antwort)

    async def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        antwort = await self._call(
            lambda svc: (
                svc.users()
                .messages()
                .attachments()
                .get(userId=GMAIL_USER_ID, messageId=message_id, id=attachment_id)
            )
        )
        return decode_base64url_bytes(str(antwort.get("data", "")))

    # --- MailAdapter-Protokoll ----------------------------------------------------

    async def thread_has_draft(self, thread_id: str) -> bool:
        thread = await self.get_thread(thread_id)
        return any(
            GMAIL_DRAFT_LABEL in (m.get("labelIds") or [])
            for m in thread.get("messages", [])
        )

    async def labels(self, message_id: str) -> set[str]:
        antwort = await self._call(
            lambda svc: (
                svc.users()
                .messages()
                .get(userId=GMAIL_USER_ID, id=message_id, format="minimal")
            )
        )
        nach_name = {label_id: name for name, label_id in self._label_ids.items()}
        return {
            nach_name[l_id] for l_id in antwort.get("labelIds", []) if l_id in nach_name
        }

    async def add_label(self, message_id: str, label: str) -> None:
        label_id = self._label_ids.get(label)
        if label_id is None:
            raise MailAdapterError("GMAIL_LABEL_UNKNOWN")
        await self._call(
            lambda svc: (
                svc.users()
                .messages()
                .modify(
                    userId=GMAIL_USER_ID,
                    id=message_id,
                    body={"addLabelIds": [label_id]},
                )
            )
        )

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
        mime = build_reply_mime(
            subject=subject,
            plain_body=plain_body,
            html_body=html_body,
            to=to,
            in_reply_to=in_reply_to,
            references=references,
        )
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")
        body = {"message": {"threadId": thread_id, "raw": raw}}
        antwort = await self._call(
            lambda svc: svc.users().drafts().create(userId=GMAIL_USER_ID, body=body)
        )
        return str(antwort["id"])


def _http_status(fehler: HttpError) -> int:
    resp = getattr(fehler, "resp", None)
    status = getattr(resp, "status", None)
    if status is None:
        status = getattr(fehler, "status_code", None)
    try:
        return int(status) if status is not None else 0
    except (TypeError, ValueError):
        return 0


def _ist_rate_limit(fehler: HttpError) -> bool:
    """403 mit Rate-Limit-``reason`` (aus error_details oder dem JSON-Body)."""
    gruende: set[str] = set()
    details = getattr(fehler, "error_details", None)
    if isinstance(details, list):
        for eintrag in details:
            if isinstance(eintrag, dict) and eintrag.get("reason"):
                gruende.add(str(eintrag["reason"]))
    inhalt = getattr(fehler, "content", b"")
    try:
        daten = json.loads(
            inhalt if isinstance(inhalt, str) else inhalt.decode("utf-8")
        )
    except (ValueError, UnicodeDecodeError, AttributeError):
        daten = {}
    if isinstance(daten, dict):
        error = daten.get("error")
        if isinstance(error, dict):
            for schluessel in ("errors", "details"):
                for eintrag in error.get(schluessel) or []:
                    if isinstance(eintrag, dict) and eintrag.get("reason"):
                        gruende.add(str(eintrag["reason"]))
    return bool(gruende & GMAIL_RATE_LIMIT_REASONS)
