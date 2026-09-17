"""Modul M-1 Phase 1b - Payload-Builder aus rohen Gmail-API-Antworten.

Reine, synchrone, netzfreie Funktionen. Eingabe sind die JSON-Objekte von
``users.messages.get(format="full")`` bzw. ``users.threads.get(format="full")``.
Ausgabe sind die Pipeline-Payloads aus ``schemas.py``.

Nichts in diesem Modul schreibt auf Disk oder loggt Mail-Inhalte.
"""

from __future__ import annotations

import base64
import io
import re
from collections.abc import Iterator
from datetime import datetime
from email.utils import parseaddr
from typing import Any
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict
from pypdf import PdfReader

from inbox_copilot.config import (
    CHARSET_RE,
    DISCLAIMER_RE,
    GMAIL_DRAFT_LABEL,
    GREETING_RE,
    HTML_BLOCK_TAGS,
    HTML_DROP_TAGS,
    OUTLOOK_FROM_RE,
    OUTLOOK_HEADER_LOOKAHEAD,
    OUTLOOK_SENT_RE,
    QUOTE_LINE_PATTERNS,
    SIGNATURE_DELIMITER_RE,
    SIGNATURE_MAX_FRACTION,
    SIGNATURE_MIN_SIGNALS,
    SIGNATURE_SIGNAL_RES,
    SIGNATURE_WINDOW_LINES,
    TIMEZONE_DEFAULT,
    Settings,
)
from inbox_copilot.schemas import (
    AttachmentMeta,
    CompanyContext,
    CompanyProfile,
    DrafterPayloadV1,
    Headers,
    Sender,
    ThreadMessage,
    TriagePayloadV1,
    TriageSummary,
)

__all__ = [
    "AttachmentPart",
    "build_drafter_payload",
    "build_thread_context",
    "build_triage_payload",
    "clean_body",
    "collect_attachments",
    "decode_base64url_bytes",
    "decode_part_text",
    "extract_body",
    "extract_headers",
    "extract_pdf_text_safe",
    "extract_text_attachment",
    "html_to_text",
    "internal_date_to_datetime",
    "should_extract",
    "strip_quotes",
    "strip_signature",
]


# ---------------------------------------------------------------------------
# 3.1 Dekodierung (Haertung D - Base64url-Padding)
# ---------------------------------------------------------------------------


def decode_base64url_bytes(data: str) -> bytes:
    """Base64url ohne abschliessendes Padding (wie von Gmail geliefert)."""
    clean = data.replace("-", "+").replace("_", "/")
    padded = clean + "=" * (-len(clean) % 4)
    return base64.b64decode(padded)


def decode_part_text(data: str, charset: str | None) -> str:
    """Deklariertes Charset -> UTF-8 -> Latin-1 -> Ersatzzeichen (verbindlich)."""
    raw = decode_base64url_bytes(data)
    for enc in ([charset] if charset else []) + ["utf-8", "latin-1"]:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Hilfsfunktionen auf der Gmail-JSON-Struktur
# ---------------------------------------------------------------------------


def _header(headers: list[dict[str, Any]] | None, name: str) -> str | None:
    """Kopfzeile case-insensitiv nachschlagen."""
    wanted = name.lower()
    for eintrag in headers or []:
        if str(eintrag.get("name", "")).lower() == wanted:
            wert = eintrag.get("value")
            return str(wert) if wert is not None else None
    return None


def _part_charset(part: dict[str, Any]) -> str | None:
    content_type = _header(part.get("headers"), "Content-Type") or ""
    treffer = CHARSET_RE.search(content_type)
    return treffer.group(1) if treffer else None


def _iter_parts(part: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Alle Parts rekursiv, den uebergebenen eingeschlossen."""
    yield part
    for unterteil in part.get("parts") or []:
        yield from _iter_parts(unterteil)


def _part_text(part: dict[str, Any]) -> str:
    body = part.get("body") or {}
    data = body.get("data")
    if not data or int(body.get("size") or 0) == 0:
        return ""
    return decode_part_text(str(data), _part_charset(part))


def _has_filename(part: dict[str, Any]) -> bool:
    return bool(str(part.get("filename") or "").strip())


# ---------------------------------------------------------------------------
# 3.2 Body-Extraktion
# ---------------------------------------------------------------------------


def html_to_text(markup: str) -> str:
    """HTML -> Text: Block-Tags als Zeilenumbruch, Skripte/Styles entfernt."""
    soup = BeautifulSoup(markup, "html.parser")
    for tag in soup.find_all(list(HTML_DROP_TAGS)):
        tag.decompose()
    for tag in soup.find_all(list(HTML_BLOCK_TAGS)):
        if tag.name == "br":
            tag.replace_with("\n")
        else:
            tag.insert_before("\n")
            tag.insert_after("\n")
    text = soup.get_text()
    zeilen = [
        re.sub(r"[ \t\r\f\v\u00a0]+", " ", zeile).strip() for zeile in text.split("\n")
    ]
    text = "\n".join(zeilen)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_body(message_json: dict[str, Any]) -> str:
    """Bevorzugt ``text/plain``; sonst ``text/html`` zu Text; sonst leer."""
    payload = message_json.get("payload") or {}
    plain: str | None = None
    html_text: str | None = None
    for part in _iter_parts(payload):
        if _has_filename(part):
            continue
        mime = str(part.get("mimeType") or "").lower()
        if mime == "text/plain" and plain is None:
            kandidat = _part_text(part)
            if kandidat.strip():
                plain = kandidat
        elif mime == "text/html" and html_text is None:
            kandidat = _part_text(part)
            if kandidat.strip():
                html_text = kandidat
    if plain is not None:
        return plain
    if html_text is not None:
        return html_to_text(html_text)
    return ""


# ---------------------------------------------------------------------------
# 3.3 Zitat-Entfernung
# ---------------------------------------------------------------------------


def _quote_start(zeilen: list[str]) -> int | None:
    for index, zeile in enumerate(zeilen):
        if any(muster.match(zeile) for muster in QUOTE_LINE_PATTERNS):
            return index
        if OUTLOOK_FROM_RE.match(zeile):
            fenster = zeilen[index + 1 : index + 1 + OUTLOOK_HEADER_LOOKAHEAD]
            if any(OUTLOOK_SENT_RE.match(folge) for folge in fenster):
                return index
    return None


def strip_quotes(text: str) -> str:
    """Verwirft ab der ersten Zitat-Markerzeile alles Folgende."""
    zeilen = text.split("\n")
    start = _quote_start(zeilen)
    if start is None:
        return text
    return "\n".join(zeilen[:start]).rstrip()


# ---------------------------------------------------------------------------
# 3.4 Signatur-Entfernung
# ---------------------------------------------------------------------------


def _strip_delimiter_signature(zeilen: list[str]) -> list[str]:
    for index, zeile in enumerate(zeilen):
        if SIGNATURE_DELIMITER_RE.match(zeile):
            return zeilen[:index]
    return zeilen


def _signal_count(zeile: str) -> int:
    return sum(1 for muster in SIGNATURE_SIGNAL_RES if muster.search(zeile))


def _strip_heuristic_signature(zeilen: list[str]) -> list[str]:
    nicht_leer = [index for index, zeile in enumerate(zeilen) if zeile.strip()]
    if len(nicht_leer) < 2:
        return zeilen
    fenster = nicht_leer[-SIGNATURE_WINDOW_LINES:]
    signale = sum(_signal_count(zeilen[index]) for index in fenster)
    if signale < SIGNATURE_MIN_SIGNALS:
        return zeilen

    # Schnittpunkt: nach der Grussformel, sonst ab der ersten Signalzeile.
    gruss = [index for index in fenster if GREETING_RE.search(zeilen[index])]
    if gruss:
        start = gruss[-1] + 1
    else:
        signalzeilen = [index for index in fenster if _signal_count(zeilen[index])]
        start = signalzeilen[0]

    # Nie mehr als 50 % der Gesamtzeilen entfernen.
    maximal_entfernbar = int(len(nicht_leer) * SIGNATURE_MAX_FRACTION)
    zu_entfernen = [index for index in nicht_leer if index >= start]
    if len(zu_entfernen) > maximal_entfernbar:
        zu_entfernen = zu_entfernen[len(zu_entfernen) - maximal_entfernbar :]
    if not zu_entfernen:
        return zeilen
    return zeilen[: zu_entfernen[0]]


def _strip_disclaimer(zeilen: list[str]) -> list[str]:
    for index, zeile in enumerate(zeilen):
        if DISCLAIMER_RE.search(zeile):
            return zeilen[:index]
    return zeilen


def strip_signature(text: str) -> str:
    """Delimiter-Signatur, Heuristik-Signatur und Disclaimer entfernen."""
    zeilen = text.split("\n")
    zeilen = _strip_delimiter_signature(zeilen)
    zeilen = _strip_disclaimer(zeilen)
    zeilen = _strip_heuristic_signature(zeilen)
    return "\n".join(zeilen).rstrip()


def clean_body(message_json: dict[str, Any]) -> str:
    """``extract_body`` -> ``strip_quotes`` -> ``strip_signature`` -> ``strip``."""
    return strip_signature(strip_quotes(extract_body(message_json))).strip()


# ---------------------------------------------------------------------------
# 3.5 Attachments (Haertung C - PDF-Memory-Guard)
# ---------------------------------------------------------------------------


class AttachmentPart(BaseModel):
    """Anhang-Metadaten plus Fundstelle im Gmail-JSON (nie an das Modell)."""

    model_config = ConfigDict(extra="forbid")

    meta: AttachmentMeta
    attachment_id: str | None
    inline_data: str | None
    content_id: str | None


def collect_attachments(
    message_json: dict[str, Any], *, inline_image_max_bytes: int
) -> list[AttachmentPart]:
    """Alle Parts mit Dateiname; Inline-Bilder (Content-ID, klein) ausgeschlossen."""
    payload = message_json.get("payload") or {}
    gefunden: list[AttachmentPart] = []
    for part in _iter_parts(payload):
        if not _has_filename(part):
            continue
        body = part.get("body") or {}
        size = int(body.get("size") or 0)
        mime = str(part.get("mimeType") or "application/octet-stream").lower()
        content_id = _header(part.get("headers"), "Content-ID")
        if content_id and mime.startswith("image/") and size < inline_image_max_bytes:
            continue
        gefunden.append(
            AttachmentPart(
                meta=AttachmentMeta(
                    filename=str(part.get("filename")),
                    mime_type=mime,
                    size_kb=round(size / 1024),
                ),
                attachment_id=body.get("attachmentId"),
                inline_data=body.get("data"),
                content_id=content_id,
            )
        )
    return gefunden


def should_extract(
    cleaned_body: str, attachments: list[AttachmentMeta], *, settings: Settings
) -> AttachmentMeta | None:
    """Erster Anhang, dessen Inhalt Stufe 2 sehen soll, sonst ``None``."""
    reference_re = re.compile(settings.attachment_reference_re, re.IGNORECASE)
    filename_re = re.compile(settings.attachment_filename_re, re.IGNORECASE)
    body_verweist = reference_re.search(cleaned_body) is not None
    for anhang in attachments:
        if not (body_verweist or filename_re.search(anhang.filename)):
            continue
        if anhang.mime_type not in settings.extractable_mime:
            continue
        if anhang.size_kb * 1024 > settings.attachment_max_bytes:
            continue
        return anhang
    return None


def extract_pdf_text_safe(
    pdf_bytes: bytes,
    *,
    head_pages: int,
    include_last: bool,
    char_cap: int,
    max_bytes: int,
) -> str | None:
    """Erste ``head_pages`` Seiten plus letzte Seite; nie auf Disk; nie Exception."""
    if len(pdf_bytes) > max_bytes:
        return None
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        n = len(reader.pages)
        idx = list(range(min(head_pages, n)))
        if include_last and n > head_pages + 1 and (n - 1) not in idx:
            idx.append(n - 1)
        chunks: list[str] = []
        total = 0
        for i in idx:
            text = reader.pages[i].extract_text() or ""
            if text.strip():
                chunks.append(f"[Seite {i + 1}]\n{text}")
                total += len(text)
            if total >= char_cap:
                break
        if not chunks:
            return None
        header = f"[AUSZUG: Seiten {', '.join(str(i + 1) for i in idx)} von {n}]\n"
        return (header + "\n".join(chunks))[:char_cap]
    except Exception:  # noqa: BLE001 - defekte PDFs duerfen nie durchschlagen
        return None


def extract_text_attachment(
    data: bytes, *, charset: str | None, char_cap: int
) -> str | None:
    """``text/plain``-Anhang mit derselben Charset-Reihenfolge wie Parts."""
    encoded = base64.urlsafe_b64encode(data).decode("ascii")
    text = decode_part_text(encoded, charset)[:char_cap]
    return text if text.strip() else None


# ---------------------------------------------------------------------------
# 3.6 Thread-Kontext
# ---------------------------------------------------------------------------


def internal_date_to_datetime(internal_date: str | int | None, tz: str) -> datetime:
    """``internalDate`` (ms Epoch) -> aware datetime in ``tz``."""
    millis = int(internal_date or 0)
    return datetime.fromtimestamp(millis / 1000, tz=ZoneInfo(tz))


def _message_sender(message_json: dict[str, Any]) -> tuple[str, str]:
    kopf = (message_json.get("payload") or {}).get("headers")
    name, adresse = parseaddr(_header(kopf, "From") or "")
    return name.strip(), adresse.strip()


def build_thread_context(
    thread_json: dict[str, Any],
    *,
    exclude_message_id: str | None,
    depth: int,
    tz: str = TIMEZONE_DEFAULT,
) -> list[ThreadMessage]:
    """Letzte ``depth`` Nachrichten ohne Drafts, chronologisch, bereinigt."""
    nachrichten = [
        nachricht
        for nachricht in thread_json.get("messages") or []
        if GMAIL_DRAFT_LABEL not in (nachricht.get("labelIds") or [])
        and nachricht.get("id") != exclude_message_id
    ]
    nachrichten.sort(key=lambda nachricht: int(nachricht.get("internalDate") or 0))
    kontext: list[ThreadMessage] = []
    for nachricht in nachrichten[-depth:] if depth > 0 else []:
        name, adresse = _message_sender(nachricht)
        kontext.append(
            ThreadMessage(
                sender_name=name,
                sender_email=adresse,
                timestamp=internal_date_to_datetime(nachricht.get("internalDate"), tz),
                body=clean_body(nachricht),
            )
        )
    return kontext


# ---------------------------------------------------------------------------
# 3.7 Oeffentliche API
# ---------------------------------------------------------------------------


def extract_headers(message_json: dict[str, Any]) -> Headers:
    kopf = (message_json.get("payload") or {}).get("headers")
    name, adresse = parseaddr(_header(kopf, "From") or "")
    return Headers(
        message_id_rfc=_header(kopf, "Message-ID"),
        references=_header(kopf, "References"),
        subject=_header(kopf, "Subject") or "",
        from_name=name.strip(),
        from_email=adresse.strip(),
        to=_header(kopf, "To") or "",
        date=_header(kopf, "Date"),
    )


def build_triage_payload(
    message_json: dict[str, Any], company: CompanyContext, *, settings: Settings
) -> TriagePayloadV1:
    kopf = extract_headers(message_json)
    anhaenge = collect_attachments(
        message_json, inline_image_max_bytes=settings.inline_image_max_bytes
    )
    return TriagePayloadV1(
        message_id=str(message_json["id"]),
        thread_id=str(message_json["threadId"]),
        received_at=internal_date_to_datetime(
            message_json.get("internalDate"), settings.timezone
        ),
        sender=Sender(name=kopf.from_name, email=kopf.from_email),
        subject=kopf.subject,
        cleaned_body=clean_body(message_json),
        attachments_meta=[anhang.meta for anhang in anhaenge],
        company_context=company,
        reply_headers=kopf,
    )


def build_drafter_payload(
    message_json: dict[str, Any],
    thread_json: dict[str, Any],
    triage: TriageSummary,
    profile: CompanyProfile,
    attachment_text: str | None,
    *,
    depth: int,
    tz: str = TIMEZONE_DEFAULT,
) -> DrafterPayloadV1:
    """Stufe-2-Payload. Die beantwortete Nachricht ist immer die letzte im Kontext.

    DECISION: Die aktuelle Nachricht wird nicht aus dem Thread-Kontext
    ausgeschlossen - der Drafter antwortet laut System-Prompt auf die
    letzte Nachricht des Kontexts, und Gate G11 prueft Zahlen gegen
    genau diesen Kontext. Fehlt sie im Thread-JSON (Suchindex-Verzug),
    wird sie aus dem Message-JSON ergaenzt.
    """
    message_id = str(message_json["id"])
    kontext = build_thread_context(
        thread_json, exclude_message_id=None, depth=depth, tz=tz
    )
    im_thread = any(
        nachricht.get("id") == message_id
        for nachricht in thread_json.get("messages") or []
    )
    if not im_thread:
        name, adresse = _message_sender(message_json)
        kontext.append(
            ThreadMessage(
                sender_name=name,
                sender_email=adresse,
                timestamp=internal_date_to_datetime(
                    message_json.get("internalDate"), tz
                ),
                body=clean_body(message_json),
            )
        )
        kontext = kontext[-depth:] if depth > 0 else kontext
    return DrafterPayloadV1(
        message_id=message_id,
        thread_id=str(message_json["threadId"]),
        original_subject=extract_headers(message_json).subject,
        extracted_attachment_text=attachment_text,
        triage=triage,
        thread_context=kontext,
        company_profile=profile,
    )
