"""Modul M-1 - die komplette asynchrone Pipeline.

Enthaelt LLM-Client, Stufe 1 (Triage), Stufe 2 (Drafter), saemtliche
deterministischen Gates, die Retry-Engine, den Dispatcher, den
HTML-Renderer, die Label-State-Machine, den Audit-Logger sowie das
``MailAdapter``-Protokoll samt ``InMemoryMailAdapter``.

Leitsatz: Code schlaegt Modell. Das Modell schlaegt vor, die Gates
entscheiden.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import date
from typing import Any, Protocol, TypeVar, cast

import anthropic
from anthropic import AsyncAnthropic
from pydantic import BaseModel, ValidationError

from inbox_copilot.config import (
    DRAFTER_SYSTEM_PROMPT,
    ESCALATE_FLAGS,
    ESCALATE_KATEGORIEN,
    ESCALATE_RECHTSFOLGEN,
    ESZETT_ERSATZ,
    ESZETT_RE,
    FORBIDDEN_BY_LANG,
    MONATE_DE,
    NO_DRAFT_FLAGS,
    NUMMER_RE,
    PLACEHOLDER_FALLBACK_KEY,
    PLACEHOLDER_KEYS,
    PLACEHOLDER_MARK_CLOSE,
    PLACEHOLDER_MARK_OPEN,
    PLACEHOLDER_RE,
    PLAIN_HINWEIS_PREFIX,
    PLAIN_HINWEIS_TRENNER,
    RETRY_INSTRUKTION,
    SPRACHE_ZU_ANTWORTSPRACHE,
    TRIAGE_SYSTEM_PROMPT,
    WARNBOX_HTML,
    Settings,
    unterstuetzt_sampling_parameter,
)
from inbox_copilot.schemas import (
    AntwortTyp,
    AuditRecord,
    DrafterPayloadV1,
    DrafterResultV1,
    Dringlichkeit,
    GateReport,
    GateSeverity,
    LabelZusatz,
    Modus,
    PipelineOutcome,
    PipelineStatus,
    Platzhalter,
    Rechtsfolge,
    RoutingAktion,
    ThreadMessage,
    TriagePayloadV1,
    TriageResultV1,
    Usage,
    json_schema_fuer,
)

__all__ = [
    "AuditLogger",
    "InMemoryMailAdapter",
    "InboxCopilotPipeline",
    "LLMCallError",
    "LLMClient",
    "LLMClientProtocol",
    "LLMSchemaError",
    "MailAdapter",
    "StageFatalError",
    "apply_drafter_gates",
    "apply_triage_gates",
    "render_html_body",
    "render_plain_body",
    "temperatur_parameter",
]

_LOG = logging.getLogger("inbox_copilot.pipeline")

ModelT = TypeVar("ModelT", bound=BaseModel)

#: Name des Fallback-Tools, wenn das installierte SDK keine Structured
#: Outputs kennt.
EMIT_TOOL_NAME = "emit_result"


# ---------------------------------------------------------------------------
# Fehler
# ---------------------------------------------------------------------------


class LLMCallError(Exception):
    """Transportfehler eines LLM-Aufrufs.

    Traegt ausschliesslich einen Fehlercode - nie Payload-Inhalte. Alle
    Ursprungs-Exceptions werden mit ``from None`` unterdrueckt, damit kein
    Traceback ein ``repr`` des Requests transportiert.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class LLMSchemaError(LLMCallError):
    """Antwort war kein gueltiges JSON oder verletzt das Schema."""

    def __init__(self, code: str, feedback: str, raw_text: str, usage: Usage) -> None:
        super().__init__(code)
        #: PII-freie Mangelbeschreibung (nur Pfad + Fehlertyp).
        self.feedback = feedback
        #: Rohantwort des Modells - ausschliesslich fuer die Fortsetzung
        #: der Konversation beim Retry. Wird niemals geloggt.
        self.raw_text = raw_text
        self.usage = usage


class StageFatalError(Exception):
    """Eine Stufe ist endgueltig gescheitert."""

    def __init__(
        self, code: str, usage: Usage, report: GateReport | None = None
    ) -> None:
        super().__init__(code)
        self.code = code
        self.usage = usage
        self.report = report or GateReport()


# ---------------------------------------------------------------------------
# LLM-Client
# ---------------------------------------------------------------------------


class LLMClientProtocol(Protocol):
    """Schnittstelle, die die Pipeline vom LLM-Client erwartet.

    DECISION: Die Pipeline typisiert gegen dieses Protokoll statt gegen
    ``LLMClient``. ``LLMClient`` erfuellt es; Tests koennen einen Mock
    einsetzen, ohne das Anthropic-SDK zu instanziieren.
    """

    async def structured_call(
        self,
        *,
        model: str,
        system: str,
        user_payload: dict[str, Any],
        schema_model: type[ModelT],
        temperature: float,
        max_tokens: int,
        timeout_s: float,
        retry_feedback: str | None = None,
        previous_assistant_json: str | None = None,
    ) -> tuple[ModelT, Usage]: ...


def _strukturierte_ausgabe_verfuegbar() -> bool:
    """Prueft einmalig, ob das installierte SDK Structured Outputs kennt."""
    import inspect

    try:
        from anthropic.resources.messages import AsyncMessages
    except ImportError:  # pragma: no cover - SDK-Layout aelterer Versionen
        return False
    try:
        parameter = inspect.signature(AsyncMessages.create).parameters
    except (TypeError, ValueError):  # pragma: no cover - defensiv
        return False
    return "output_config" in parameter


# DECISION: Der Modus wird beim Import einmal bestimmt. Kennt das
# installierte SDK ``output_config`` (Structured Outputs), wird das
# Feature genutzt; andernfalls greift der in der Spezifikation
# vorgesehene Fallback ueber ein einziges Tool ``emit_result`` mit
# erzwungener Tool-Wahl. Beide Pfade validieren die Antwort anschliessend
# mit ``schema_model.model_validate`` - das Schema ist also in jedem Fall
# durchgesetzt.
_STRUCTURED_OUTPUT_VERFUEGBAR = _strukturierte_ausgabe_verfuegbar()


# Anpassung an anthropic-SDK 1.x.
# 1. ``messages.create`` kennt ``temperature`` dort nicht mehr als benannten
#    Parameter - der Aufruf scheitert mit ``TypeError``, noch vor jedem
#    Netzwerkzugriff. Der Parameter ist aus der SDK-Signatur entfernt, nicht
#    aus der API: ``extra_body`` wird unveraendert in den Request-Body
#    gemergt und ist damit der offizielle Ersatzweg.
# 2. Structured Outputs lehnt ``minimum``/``maximum`` bei Zahlen mit HTTP 400
#    ab ("For 'number' type, properties maximum, minimum are not supported").
# Beides wird hier defensiv abgefangen; aeltere SDKs bleiben unberuehrt.
def _sdk_kennt_temperature() -> bool:
    import inspect

    try:
        from anthropic.resources.messages import AsyncMessages
    except ImportError:  # pragma: no cover
        return False
    try:
        return "temperature" in inspect.signature(AsyncMessages.create).parameters
    except (TypeError, ValueError):  # pragma: no cover
        return False


_SDK_KENNT_TEMPERATURE = _sdk_kennt_temperature()

_SCHEMA_ZAHLENGRENZEN = frozenset(
    {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"}
)


def _ohne_zahlengrenzen(knoten: Any) -> Any:
    """Entfernt von Structured Outputs nicht unterstuetzte Zahlengrenzen.

    Die Grenzen bleiben im Pydantic-Modell bestehen und werden nach dem
    Aufruf durch ``schema_model.model_validate`` weiterhin durchgesetzt -
    entfernt wird nur die Kopie, die als Ausgabeformat an die API geht.
    """
    if isinstance(knoten, dict):
        return {
            schluessel: _ohne_zahlengrenzen(wert)
            for schluessel, wert in knoten.items()
            if schluessel not in _SCHEMA_ZAHLENGRENZEN
        }
    if isinstance(knoten, list):
        return [_ohne_zahlengrenzen(eintrag) for eintrag in knoten]
    return knoten


def temperatur_parameter(model: str, temperature: float) -> dict[str, Any]:
    """Traegt ``temperature`` so ein, wie das installierte SDK es annimmt.

    Leeres Dict, wenn das Modell den Parameter nicht akzeptiert (Opus 4.7
    und spaeter quittieren ihn mit HTTP 400, Sonnet 5 jeden Wert ausser dem
    Standardwert). Sonst der benannte Parameter (SDK 0.x) oder der Umweg
    ueber ``extra_body`` (SDK 1.x).
    """
    if not unterstuetzt_sampling_parameter(model):
        return {}
    if _SDK_KENNT_TEMPERATURE:
        return {"temperature": temperature}
    return {"extra_body": {"temperature": temperature}}


class LLMClient:
    """Duenner, asynchroner Wrapper um ``AsyncAnthropic``."""

    def __init__(
        self, settings: Settings, client: AsyncAnthropic | None = None
    ) -> None:
        self._settings = settings
        self._client = client or AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value()
        )
        self._strukturiert = _STRUCTURED_OUTPUT_VERFUEGBAR

    async def structured_call(
        self,
        *,
        model: str,
        system: str,
        user_payload: dict[str, Any],
        schema_model: type[ModelT],
        temperature: float,
        max_tokens: int,
        timeout_s: float,
        retry_feedback: str | None = None,
        previous_assistant_json: str | None = None,
    ) -> tuple[ModelT, Usage]:
        """Ruft das Modell und validiert die Antwort gegen ``schema_model``.

        Mail-Inhalte gehen ausschliesslich als JSON-serialisierter
        User-Content an das Modell; der System-Prompt wird nie formatiert
        oder interpoliert.
        """
        schema = json_schema_fuer(schema_model)
        nachrichten: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": json.dumps(user_payload, ensure_ascii=False, default=str),
            }
        ]
        if retry_feedback is not None:
            nachrichten.append(
                {"role": "assistant", "content": previous_assistant_json or "{}"}
            )
            nachrichten.append(
                {"role": "user", "content": RETRY_INSTRUKTION + retry_feedback}
            )

        parameter: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": nachrichten,
        }
        parameter.update(temperatur_parameter(model, temperature))
        if self._strukturiert:
            parameter["output_config"] = {
                "format": {
                    "type": "json_schema",
                    "schema": _ohne_zahlengrenzen(schema),
                }
            }
        else:
            parameter["tools"] = [
                {
                    "name": EMIT_TOOL_NAME,
                    "description": "Gibt das Ergebnis als validiertes JSON zurueck.",
                    "strict": True,
                    "input_schema": schema,
                }
            ]
            parameter["tool_choice"] = {"type": "tool", "name": EMIT_TOOL_NAME}

        erstellen = cast(
            Callable[..., Awaitable[Any]],
            self._client.messages.create,
        )
        try:
            antwort = await asyncio.wait_for(erstellen(**parameter), timeout=timeout_s)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise LLMCallError("LLM_TIMEOUT") from None
        except anthropic.APIError:
            raise LLMCallError("LLM_API_ERROR") from None
        except Exception:  # noqa: BLE001 - jeder Transportfehler wird gekapselt
            raise LLMCallError("LLM_TRANSPORT_ERROR") from None

        usage = Usage()
        eingang, ausgang = _token_verbrauch(antwort)
        usage.plus_tokens(eingang, ausgang)

        rohtext = _rohtext(antwort, strukturiert=self._strukturiert)
        try:
            daten = json.loads(rohtext)
        except (ValueError, TypeError):
            raise LLMSchemaError(
                "SCHEMA_INVALID",
                "Die Antwort war kein gueltiges JSON-Objekt. Gib genau ein "
                "JSON-Objekt nach dem Schema aus, ohne Text davor oder danach.",
                rohtext,
                usage,
            ) from None
        try:
            ergebnis = schema_model.model_validate(daten)
        except ValidationError as fehler:
            raise LLMSchemaError(
                "SCHEMA_INVALID",
                _validierungs_feedback(fehler),
                rohtext,
                usage,
            ) from None
        return ergebnis, usage


def _validierungs_feedback(fehler: ValidationError) -> str:
    """Pydantic-Fehler ohne die (potenziell PII-haltigen) Eingabewerte."""
    zeilen = [
        f"Feld '{'.'.join(str(teil) for teil in eintrag['loc'])}': {eintrag['type']}"
        for eintrag in fehler.errors()
    ]
    return "Schemaverletzungen:\n" + "\n".join(zeilen[:20])


def _token_verbrauch(antwort: Any) -> tuple[int, int]:
    verbrauch = getattr(antwort, "usage", None)
    eingang = getattr(verbrauch, "input_tokens", 0) or 0
    ausgang = getattr(verbrauch, "output_tokens", 0) or 0
    return int(eingang), int(ausgang)


def _rohtext(antwort: Any, *, strukturiert: bool) -> str:
    """Extrahiert den JSON-Rohtext aus einer SDK-Antwort.

    BEOBACHTUNG (Live-Lauf 23.09.2026): Der Drafter laeuft auf Sonnet 5, und
    dort denkt das Modell standardmaessig vor - vor dem Textblock kommt ein
    ``thinking``-Block. Die Schleife ueberspringt ihn, die Entwuerfe waren
    korrekt. Er erklaert aber einen Teil der gemessenen 27-32 s und der rund
    3'000 Output-Token je Entwurf. Bewusst unveraendert gelassen: wer die
    Stufe beschleunigen will, setzt dort an (``output_config.effort`` oder
    ``thinking``) - das ist eine eigene Aufgabe mit eigener Messung, kein
    Nebeneffekt dieses Fixes.
    """
    bloecke = getattr(antwort, "content", None) or []
    for block in bloecke:
        typ = getattr(block, "type", None)
        if strukturiert and typ == "text":
            return str(getattr(block, "text", ""))
        if not strukturiert and typ == "tool_use":
            return json.dumps(getattr(block, "input", {}), ensure_ascii=False)
    return ""


# ---------------------------------------------------------------------------
# MailAdapter
# ---------------------------------------------------------------------------


class MailAdapter(Protocol):
    """Von der Integrationsschicht (Ingenieur 1) zu implementieren."""

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


class InMemoryMailAdapter:
    """Dict-basierte Referenzimplementierung fuer Tests und Dry-Runs."""

    def __init__(self) -> None:
        self._labels: dict[str, set[str]] = {}
        self._drafts: dict[str, list[dict[str, str]]] = {}
        self._zaehler = 0

    async def thread_has_draft(self, thread_id: str) -> bool:
        return bool(self._drafts.get(thread_id))

    async def labels(self, message_id: str) -> set[str]:
        return set(self._labels.get(message_id, set()))

    async def add_label(self, message_id: str, label: str) -> None:
        self._labels.setdefault(message_id, set()).add(label)

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
        self._zaehler += 1
        draft_id = f"draft-{self._zaehler}"
        self._drafts.setdefault(thread_id, []).append(
            {
                "draft_id": draft_id,
                "subject": subject,
                "html_body": html_body,
                "plain_body": plain_body,
                "to": to or "",
                "in_reply_to": in_reply_to or "",
                "references": references or "",
            }
        )
        return draft_id

    # --- Testhilfen (nicht Teil des Protokolls) --------------------------

    def drafts(self, thread_id: str) -> list[dict[str, str]]:
        return list(self._drafts.get(thread_id, []))

    def seed_draft(self, thread_id: str) -> None:
        self._drafts.setdefault(thread_id, []).append(
            {
                "draft_id": "vorbestehend",
                "subject": "",
                "html_body": "",
                "plain_body": "",
            }
        )

    def seed_label(self, message_id: str, label: str) -> None:
        self._labels.setdefault(message_id, set()).add(label)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def sha256_hex(wert: str) -> str:
    return hashlib.sha256(wert.encode("utf-8")).hexdigest()


class AuditLogger:
    """Schreibt JSON-Zeilen nach stdout - ausschliesslich Nicht-PII."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("inbox_copilot.audit")

    def record(self, record: AuditRecord) -> None:
        self._logger.info(
            json.dumps(
                record.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
            )
        )


# ---------------------------------------------------------------------------
# Gates Stufe 1
# ---------------------------------------------------------------------------


def _eskalieren(
    ergebnis: TriageResultV1, report: GateReport, code: str, feedback: str
) -> None:
    ergebnis.routing.aktion = RoutingAktion.ESKALIEREN
    ergebnis.routing.label_zusatz = LabelZusatz.ACHTUNG_CHEF
    if ergebnis.braucht_antwort:
        ergebnis.antwort_typ = AntwortTyp.EINGANGSBESTAETIGUNG
    report.add(code, "autofix", feedback)


def apply_triage_gates(
    ergebnis: TriageResultV1, payload: TriagePayloadV1, settings: Settings
) -> GateReport:
    """Deterministische Nachkontrolle der Stufe 1 (Abschnitt 6.5)."""
    report = GateReport()

    if ergebnis.message_id != payload.message_id:
        report.add(
            "TRIAGE_ID_MISMATCH", "fatal", "Die message_id der Antwort passt nicht."
        )
        return report

    if ergebnis.frist.erkannt and ergebnis.frist.beleg is None:
        ergebnis.frist.erkannt = False
        ergebnis.confidence = min(ergebnis.confidence, 0.69)
        report.add(
            "TRIAGE_FRIST_OHNE_BELEG",
            "autofix",
            "Frist ohne woertlichen Beleg verworfen.",
        )

    if (
        ergebnis.frist.datum is not None
        and ergebnis.frist.datum < payload.received_at.date()
        and ergebnis.frist.rechtsfolge is not Rechtsfolge.UNKLAR
    ):
        ergebnis.frist.rechtsfolge = Rechtsfolge.UNKLAR
        report.add(
            "TRIAGE_FRIST_VERGANGENHEIT",
            "autofix",
            "Fristdatum liegt vor dem Eingang; Rechtsfolge auf unklar gesetzt.",
        )

    if (
        ergebnis.frist.rechtsfolge is not None
        and ergebnis.frist.rechtsfolge.value in ESCALATE_RECHTSFOLGEN
    ):
        _eskalieren(
            ergebnis,
            report,
            "TRIAGE_ESKALATION_RECHTSFOLGE",
            "Rechtsfolge der Frist erzwingt Eskalation.",
        )

    if {flag.value for flag in ergebnis.risiko_flags} & ESCALATE_FLAGS:
        _eskalieren(
            ergebnis,
            report,
            "TRIAGE_ESKALATION_RISIKOFLAG",
            "Risiko-Flag erzwingt Eskalation.",
        )

    if ergebnis.kategorie.value in ESCALATE_KATEGORIEN:
        _eskalieren(
            ergebnis,
            report,
            "TRIAGE_ESKALATION_KATEGORIE",
            "Kategorie erzwingt Eskalation.",
        )

    if ergebnis.confidence < settings.confidence_floor:
        _eskalieren(
            ergebnis,
            report,
            "TRIAGE_CONFIDENCE_FLOOR",
            "Confidence unter dem Schwellenwert.",
        )

    if {flag.value for flag in ergebnis.risiko_flags} & NO_DRAFT_FLAGS:
        _eskalieren(
            ergebnis,
            report,
            "TRIAGE_NO_DRAFT_FLAG",
            "Kein Entwurf an mutmassliche Angreifer.",
        )
        ergebnis.braucht_antwort = False
        ergebnis.antwort_typ = AntwortTyp.KEINE

    if ergebnis.frist.erkannt and ergebnis.dringlichkeit is Dringlichkeit.KEINE:
        ergebnis.dringlichkeit = Dringlichkeit.HEUTE
        report.add(
            "TRIAGE_DRINGLICHKEIT_ANGEHOBEN",
            "autofix",
            "Erkannte Frist hebt die Dringlichkeit auf heute.",
        )

    return report


# ---------------------------------------------------------------------------
# Gates Stufe 2
# ---------------------------------------------------------------------------


def _hinweis_ergaenzen(hinweis: str, zusatz: str) -> str:
    if zusatz in hinweis:
        return hinweis
    getrimmt = hinweis.rstrip()
    if not getrimmt:
        return zusatz
    trenner = " " if getrimmt.endswith((".", "!", "?")) else ". "
    return f"{getrimmt}{trenner}{zusatz}"


def _ziffern(text: str) -> str:
    return re.sub(r"\D", "", text)


def _datums_varianten(tag: date | None) -> list[str]:
    if tag is None:
        return []
    monat = MONATE_DE[tag.month - 1]
    return [
        tag.isoformat(),
        f"{tag.day:02d}.{tag.month:02d}.{tag.year}",
        f"{tag.day}.{tag.month}.{tag.year}",
        f"{tag.day:02d}.{tag.month:02d}.{tag.year % 100:02d}",
        f"{tag.day:02d}.{tag.month:02d}.",
        f"{tag.day}. {monat} {tag.year}",
    ]


def _quell_ziffern(payload: DrafterPayloadV1) -> set[str]:
    quellen: list[str] = [nachricht.body for nachricht in payload.thread_context]
    if payload.extracted_attachment_text:
        quellen.append(payload.extracted_attachment_text)
    quellen.append(payload.company_profile.signature_block)
    quellen.append(payload.original_subject)
    quellen.extend(_datums_varianten(payload.triage.frist_datum))

    gefunden: set[str] = set()
    for quelle in quellen:
        for treffer in NUMMER_RE.findall(quelle):
            ziffern = _ziffern(treffer)
            if ziffern:
                gefunden.add(ziffern)
    return gefunden


def _wortzahl_vor_signatur(plain_body: str, signature_block: str) -> int:
    koerper = plain_body.rstrip()
    signatur = signature_block.rstrip()
    if signatur and koerper.endswith(signatur):
        koerper = koerper[: len(koerper) - len(signatur)]
    return len(koerper.split())


def apply_drafter_gates(
    ergebnis: DrafterResultV1,
    payload: DrafterPayloadV1,
    forced_modus: Modus,
    settings: Settings,
    *,
    is_retry: bool,
) -> GateReport:
    """Gates G1-G12 der Stufe 2 (Abschnitt 6.8).

    Es werden immer **alle** Gates ausgewertet; erst danach entscheidet die
    Retry-Engine. ``is_retry`` schaltet die in der Spezifikation
    vorgesehene Nachbehandlung frei (G5 wird zum Autofix, G6/G7/G8/G10
    werden fatal).
    """
    report = GateReport()
    haerte: GateSeverity = "fatal" if is_retry else "retry"

    # G1 - Eszett
    ersetzungen = 0
    for feld in ("plain_body", "subject_reply", "hinweis_fuer_inhaber"):
        wert = cast(str, getattr(ergebnis, feld))
        neu, anzahl = ESZETT_RE.subn(ESZETT_ERSATZ, wert)
        if anzahl:
            setattr(ergebnis, feld, neu)
            ersetzungen += anzahl
    if ersetzungen:
        report.stats["eszett_replacements"] = ersetzungen
        report.add("DRAFT_ESZETT", "autofix", "Eszett durch ss ersetzt.")

    # G2 - Modus
    if ergebnis.modus is not forced_modus:
        ergebnis.modus = forced_modus
        report.add(
            "DRAFT_MODUS_KORRIGIERT", "autofix", "Modus von der Stufe 1 gesetzt."
        )

    # G4 - Platzhalter-Paritaet (die Regex-Menge gewinnt)
    im_text = [
        (treffer.group(1), treffer.group(2).strip())
        for treffer in PLACEHOLDER_RE.finditer(ergebnis.plain_body)
    ]
    gemeldet = [
        (eintrag.key, eintrag.hinweis.strip()) for eintrag in ergebnis.platzhalter
    ]
    if set(im_text) != set(gemeldet):
        ergebnis.platzhalter = [
            Platzhalter(key=key, hinweis=hinweis)
            for key, hinweis in dict.fromkeys(im_text)
        ]
        ergebnis.hinweis_fuer_inhaber = _hinweis_ergaenzen(
            ergebnis.hinweis_fuer_inhaber, "Platzhalter-Abgleich korrigiert"
        )
        report.add(
            "DRAFT_PLATZHALTER_PARITAET",
            "autofix",
            "Platzhalterliste an den Text angeglichen.",
        )

    # G5 - unzulaessiger Platzhalter-KEY
    ungueltig = sorted(
        {
            eintrag.key
            for eintrag in ergebnis.platzhalter
            if eintrag.key not in PLACEHOLDER_KEYS
        }
    )
    if ungueltig:
        if is_retry:
            for key in ungueltig:
                ergebnis.plain_body = re.sub(
                    r"\[\[" + re.escape(key) + r"\s*:",
                    f"[[{PLACEHOLDER_FALLBACK_KEY}:",
                    ergebnis.plain_body,
                )
            ergebnis.platzhalter = [
                eintrag
                if eintrag.key in PLACEHOLDER_KEYS
                else Platzhalter(key=PLACEHOLDER_FALLBACK_KEY, hinweis=eintrag.hinweis)
                for eintrag in ergebnis.platzhalter
            ]
            report.add(
                "DRAFT_PLACEHOLDER_KEY",
                "autofix",
                f"Unzulaessige Keys auf {PLACEHOLDER_FALLBACK_KEY} gemappt.",
            )
        else:
            report.add(
                "DRAFT_PLACEHOLDER_KEY",
                "retry",
                "Verwende ausschliesslich Keys aus der erlaubten Platzhalter-Liste.",
            )

    # G6 - Zusage in der Eingangsbestaetigung
    if ergebnis.modus is Modus.DRAFT_EINGANGSBESTAETIGUNG and ergebnis.enthaelt_zusage:
        report.add(
            "DRAFT_ZUSAGE",
            haerte,
            "Eine Eingangsbestaetigung darf nichts zusagen; enthaelt_zusage "
            "muss false sein.",
        )

    # G7 - Verbotsmuster (Feedback enthaelt nur den Code, nie das Zitat)
    if ergebnis.modus is Modus.DRAFT_EINGANGSBESTAETIGUNG:
        muster = FORBIDDEN_BY_LANG.get(ergebnis.sprache_antwort.value)
        if muster is not None and muster.search(ergebnis.plain_body):
            report.add(
                "DRAFT_FORBIDDEN_PATTERN",
                haerte,
                "Der Entwurf enthaelt ein unzulaessiges Muster (Wertung, "
                "Entschuldigung, Kulanz oder Fristbestaetigung). Formuliere "
                "rein bestaetigend und ohne jede Zusage.",
            )

    # G8 - Laenge
    limit = (
        settings.eingangsbestaetigung_max_words
        if ergebnis.modus is Modus.DRAFT_EINGANGSBESTAETIGUNG
        else settings.inhaltlich_max_words
    )
    if (
        _wortzahl_vor_signatur(
            ergebnis.plain_body, payload.company_profile.signature_block
        )
        > limit
    ):
        report.add(
            "DRAFT_TOO_LONG",
            haerte,
            f"Der Entwurf ueberschreitet {limit} Woerter vor der Signatur.",
        )

    # G9 - Signatur
    signatur = payload.company_profile.signature_block.rstrip()
    if signatur and not ergebnis.plain_body.rstrip().endswith(signatur):
        ergebnis.plain_body = ergebnis.plain_body.rstrip() + "\n\n" + signatur
        report.add("DRAFT_SIGNATUR_ERGAENZT", "autofix", "Signaturblock angehaengt.")

    # G10 - Sprachabgleich
    erwartet = SPRACHE_ZU_ANTWORTSPRACHE.get(payload.triage.sprache_eingang.value)
    if erwartet is not None and ergebnis.sprache_antwort.value != erwartet:
        report.add(
            "DRAFT_LANG_MISMATCH",
            haerte,
            f"Die Antwortsprache muss '{erwartet}' sein.",
        )

    # G11 - Ziffernfolge ohne Quelle
    quellen = _quell_ziffern(payload)
    ohne_quelle = [
        ziffern
        for ziffern in (
            _ziffern(treffer) for treffer in NUMMER_RE.findall(ergebnis.plain_body)
        )
        if len(ziffern) >= 2 and not any(ziffern in quelle for quelle in quellen)
    ]
    if ohne_quelle:
        ergebnis.hinweis_fuer_inhaber = _hinweis_ergaenzen(
            ergebnis.hinweis_fuer_inhaber, "Zahl ohne Quelle pruefen"
        )
        report.stats["zahlen_ohne_quelle"] = len(ohne_quelle)
        report.add(
            "DRAFT_ZAHL_OHNE_QUELLE",
            "autofix",
            "Mindestens eine Zahl hat keine Quelle im Thread.",
        )

    # G12 - message_id
    if ergebnis.message_id != payload.message_id:
        report.add(
            "DRAFT_ID_MISMATCH", "fatal", "Die message_id der Antwort passt nicht."
        )

    return report


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _markiere_platzhalter(text: str) -> str:
    return PLACEHOLDER_RE.sub(
        lambda treffer: (
            PLACEHOLDER_MARK_OPEN + treffer.group(0) + PLACEHOLDER_MARK_CLOSE
        ),
        text,
    )


def render_html_body(hinweis: str, plain_body: str) -> str:
    """Warnbox, Trennlinie, escapte Absaetze, markierte Platzhalter."""
    warnbox = WARNBOX_HTML.format(hinweis=html.escape(hinweis))
    absaetze = [absatz for absatz in plain_body.split("\n\n") if absatz.strip()]
    gerendert = "".join(
        "<p>"
        + _markiere_platzhalter(html.escape(absatz).replace("\n", "<br>"))
        + "</p>"
        for absatz in absaetze
    )
    return warnbox + "<hr>" + gerendert


def render_plain_body(hinweis: str, plain_body: str) -> str:
    return PLAIN_HINWEIS_PREFIX + hinweis + PLAIN_HINWEIS_TRENNER + plain_body


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

DrafterPayloadFactory = Callable[[TriageResultV1], Awaitable[DrafterPayloadV1]]


class InboxCopilotPipeline:
    """Oeffentliche API des Moduls M-1."""

    def __init__(
        self,
        settings: Settings,
        llm: LLMClientProtocol,
        adapter: MailAdapter,
        audit: AuditLogger,
    ) -> None:
        self._settings = settings
        self._llm = llm
        self._adapter = adapter
        self._audit = audit

    # --- Stufe 1 ---------------------------------------------------------

    def _triage_user_payload(self, payload: TriagePayloadV1) -> dict[str, Any]:
        return {
            "message_id": payload.message_id,
            "received_at": payload.received_at.isoformat(),
            "sender": payload.sender.model_dump(),
            "subject": payload.subject,
            "cleaned_body": payload.cleaned_body[
                : self._settings.triage_input_char_cap
            ],
            "attachments_meta": [
                anhang.model_dump() for anhang in payload.attachments_meta
            ],
            "company_context": payload.company_context.model_dump(),
        }

    async def run_triage(
        self, payload: TriagePayloadV1
    ) -> tuple[TriageResultV1, GateReport, Usage]:
        usage = Usage()
        start = time.perf_counter()
        user_payload = self._triage_user_payload(payload)
        feedback: str | None = None
        vorherige: str | None = None
        ergebnis: TriageResultV1 | None = None

        while ergebnis is None:
            try:
                ergebnis, teil = await self._llm.structured_call(
                    model=self._settings.triage_model,
                    system=TRIAGE_SYSTEM_PROMPT,
                    user_payload=user_payload,
                    schema_model=TriageResultV1,
                    temperature=self._settings.triage_temperature,
                    max_tokens=self._settings.triage_max_tokens,
                    timeout_s=self._settings.triage_timeout_s,
                    retry_feedback=feedback,
                    previous_assistant_json=vorherige,
                )
                usage.plus_tokens(teil.input_tokens, teil.output_tokens)
            except LLMSchemaError as fehler:
                usage.plus_tokens(fehler.usage.input_tokens, fehler.usage.output_tokens)
                if usage.retries >= self._settings.max_retries_per_stage:
                    usage.latency_ms = _ms_seit(start)
                    raise StageFatalError("TRIAGE_INVALID_JSON", usage) from None
                usage.retries += 1
                feedback = fehler.feedback
                vorherige = fehler.raw_text
            except LLMCallError as fehler:
                # DECISION: Transportfehler (Timeout, API-Fehler) werden nicht
                # wiederholt. Ein Retry verdoppelt im Timeout-Fall die Latenz
                # des Webhooks, ohne die Ursache zu beheben.
                usage.latency_ms = _ms_seit(start)
                raise StageFatalError(fehler.code, usage) from None

        usage.latency_ms = _ms_seit(start)
        report = apply_triage_gates(ergebnis, payload, self._settings)
        if report.is_fatal():
            raise StageFatalError(
                report.fatal_code() or "TRIAGE_GATE_FATAL", usage, report
            )
        return ergebnis, report, usage

    # --- Stufe 2 ---------------------------------------------------------

    def _drafter_user_payload(self, payload: DrafterPayloadV1) -> dict[str, Any]:
        anhang = payload.extracted_attachment_text
        if anhang is not None:
            anhang = anhang[: self._settings.attachment_text_char_cap]
        return {
            "message_id": payload.message_id,
            "original_subject": payload.original_subject,
            "extracted_attachment_text": anhang,
            "triage": payload.triage.model_dump(mode="json"),
            "thread_context": [
                nachricht.model_dump(mode="json")
                for nachricht in _kappe_thread(
                    payload.thread_context, self._settings.drafter_input_char_cap
                )
            ],
            "company_profile": payload.company_profile.model_dump(),
        }

    async def run_drafter(
        self, payload: DrafterPayloadV1, forced_modus: Modus
    ) -> tuple[DrafterResultV1, GateReport, Usage]:
        usage = Usage()
        start = time.perf_counter()
        user_payload = self._drafter_user_payload(payload)
        feedback: str | None = None
        vorherige: str | None = None
        gesammelt = GateReport()

        while True:
            try:
                ergebnis, teil = await self._llm.structured_call(
                    model=self._settings.drafter_model,
                    system=DRAFTER_SYSTEM_PROMPT,
                    user_payload=user_payload,
                    schema_model=DrafterResultV1,
                    temperature=self._settings.drafter_temperature,
                    max_tokens=self._settings.drafter_max_tokens,
                    timeout_s=self._settings.drafter_timeout_s,
                    retry_feedback=feedback,
                    previous_assistant_json=vorherige,
                )
                usage.plus_tokens(teil.input_tokens, teil.output_tokens)
            except LLMSchemaError as fehler:
                usage.plus_tokens(fehler.usage.input_tokens, fehler.usage.output_tokens)
                if usage.retries >= self._settings.max_retries_per_stage:
                    usage.latency_ms = _ms_seit(start)
                    raise StageFatalError(
                        "DRAFT_INVALID_JSON", usage, gesammelt
                    ) from None
                usage.retries += 1
                feedback = fehler.feedback
                vorherige = fehler.raw_text
                continue
            except LLMCallError as fehler:
                usage.latency_ms = _ms_seit(start)
                raise StageFatalError(fehler.code, usage, gesammelt) from None

            report = apply_drafter_gates(
                ergebnis,
                payload,
                forced_modus,
                self._settings,
                is_retry=usage.retries > 0,
            )
            gesammelt = gesammelt.merge(report)

            if gesammelt.is_fatal():
                usage.latency_ms = _ms_seit(start)
                raise StageFatalError(
                    report.fatal_code() or "DRAFT_GATE_FATAL", usage, gesammelt
                )
            if report.needs_retry():
                # Alle Retry-Verstoesse eines Durchlaufs werden in genau
                # einer Feedback-Nachricht gebuendelt.
                usage.retries += 1
                feedback = report.retry_feedback()
                vorherige = ergebnis.model_dump_json()
                continue

            usage.latency_ms = _ms_seit(start)
            return ergebnis, gesammelt, usage

    # --- Dispatcher ------------------------------------------------------

    def _betreff(self, entwurf: DrafterResultV1) -> str:
        prefix = self._settings.betreff_prefix()
        if len(entwurf.platzhalter) >= 1:
            return self._settings.subject_prefix_review + prefix + entwurf.subject_reply
        return prefix + entwurf.subject_reply

    async def _label(self, message_id: str, label: str, gesetzt: list[str]) -> None:
        await self._adapter.add_label(message_id, label)
        if label not in gesetzt:
            gesetzt.append(label)

    async def process(
        self,
        triage_payload: TriagePayloadV1,
        drafter_payload_factory: DrafterPayloadFactory,
    ) -> PipelineOutcome:
        """Verarbeitet genau eine Nachricht. Wirft nie eine Exception."""
        gesetzt: list[str] = []
        audit = AuditRecord(
            message_id_hash=sha256_hex(triage_payload.message_id),
            thread_id_hash=sha256_hex(triage_payload.thread_id),
        )
        try:
            return await self._process(
                triage_payload, drafter_payload_factory, gesetzt, audit
            )
        except Exception as fehler:  # noqa: BLE001 - bewusst alles auffangen
            # Nur der Exception-Typ wird protokolliert, niemals die Nachricht
            # oder ein Payload-Ausschnitt.
            _LOG.error(
                json.dumps(
                    {
                        "event": "pipeline_exception",
                        "error_type": type(fehler).__name__,
                        "message_id_hash": audit.message_id_hash,
                    },
                    sort_keys=True,
                )
            )
            try:
                await self._label(
                    triage_payload.message_id, self._settings.label_escalate, gesetzt
                )
            except Exception:  # noqa: BLE001 - Postfach nicht erreichbar
                _LOG.error(
                    json.dumps({"event": "label_failed", "error_type": "adapter"})
                )
            audit.status = "FAILED"
            audit.error_code = f"UNEXPECTED_{type(fehler).__name__}"
            audit.labels_set = list(gesetzt)
            self._audit.record(audit)
            return PipelineOutcome(
                status="FAILED", labels_set=list(gesetzt), draft_id=None, audit=audit
            )

    async def _process(
        self,
        triage_payload: TriagePayloadV1,
        drafter_payload_factory: DrafterPayloadFactory,
        gesetzt: list[str],
        audit: AuditRecord,
    ) -> PipelineOutcome:
        message_id = triage_payload.message_id
        thread_id = triage_payload.thread_id

        # --- Idempotenz ---
        if await self._adapter.thread_has_draft(thread_id) or (
            self._settings.label_triaged in await self._adapter.labels(message_id)
        ):
            return self._abschluss(audit, "SKIPPED_ALREADY_PROCESSED", gesetzt, None)

        # --- Stufe 1 ---
        try:
            triage, triage_report, triage_usage = await self.run_triage(triage_payload)
        except StageFatalError as fehler:
            audit.stage_1_ms = fehler.usage.latency_ms
            audit.tokens_in_1 = fehler.usage.input_tokens
            audit.tokens_out_1 = fehler.usage.output_tokens
            audit.retries_1 = fehler.usage.retries
            audit.gate_codes = fehler.report.codes()
            audit.error_code = fehler.code
            await self._label(message_id, self._settings.label_escalate, gesetzt)
            return self._abschluss(audit, "FAILED", gesetzt, None)

        audit.stage_1_ms = triage_usage.latency_ms
        audit.tokens_in_1 = triage_usage.input_tokens
        audit.tokens_out_1 = triage_usage.output_tokens
        audit.retries_1 = triage_usage.retries
        audit.gate_codes = triage_report.codes()
        audit.kategorie = triage.kategorie
        audit.dringlichkeit = triage.dringlichkeit
        audit.confidence_1 = triage.confidence
        audit.routing_aktion = triage.routing.aktion

        await self._label(message_id, self._settings.label_triaged, gesetzt)

        aktion = triage.routing.aktion
        if aktion is RoutingAktion.IGNORIEREN:
            return self._abschluss(audit, "IGNORED", gesetzt, None)
        if aktion is RoutingAktion.NUR_LABELN:
            return self._abschluss(audit, "LABELED_ONLY", gesetzt, None)

        if aktion is RoutingAktion.ESKALIEREN:
            await self._label(message_id, self._settings.label_escalate, gesetzt)
            if not triage.braucht_antwort:
                return self._abschluss(audit, "ESCALATED_NO_DRAFT", gesetzt, None)
            # Bei Eskalation wird der Modus im Code erzwungen, unabhaengig
            # vom antwort_typ des Modells.
            forced_modus = Modus.DRAFT_EINGANGSBESTAETIGUNG
            erfolg: PipelineStatus = "ESCALATED_WITH_DRAFT"
            misserfolg: PipelineStatus = "ESCALATED_NO_DRAFT"
        else:
            if not triage.braucht_antwort:
                # DECISION: Stufe 2 laeuft laut Spezifikation nur bei
                # braucht_antwort == True. Ein widerspruechliches Routing
                # wird konservativ als "nur labeln" behandelt.
                return self._abschluss(audit, "LABELED_ONLY", gesetzt, None)
            forced_modus = (
                Modus.DRAFT_EINGANGSBESTAETIGUNG
                if aktion is RoutingAktion.DRAFT_EINGANGSBESTAETIGUNG
                else Modus.DRAFT_INHALTLICH
            )
            erfolg = "DRAFT_CREATED"
            misserfolg = "FAILED"

        audit.modus = forced_modus

        # --- Stufe 2 ---
        roh_payload = await drafter_payload_factory(triage)
        drafter_payload = roh_payload.model_copy(
            update={
                "message_id": message_id,
                "thread_id": thread_id,
                "triage": triage.to_summary(),
            }
        )

        try:
            entwurf, draft_report, draft_usage = await self.run_drafter(
                drafter_payload, forced_modus
            )
        except StageFatalError as fehler:
            audit.stage_2_ms = fehler.usage.latency_ms
            audit.tokens_in_2 = fehler.usage.input_tokens
            audit.tokens_out_2 = fehler.usage.output_tokens
            audit.retries_2 = fehler.usage.retries
            audit.gate_codes = [*audit.gate_codes, *fehler.report.codes()]
            audit.error_code = fehler.code
            await self._label(message_id, self._settings.label_escalate, gesetzt)
            return self._abschluss(audit, misserfolg, gesetzt, None)

        audit.stage_2_ms = draft_usage.latency_ms
        audit.tokens_in_2 = draft_usage.input_tokens
        audit.tokens_out_2 = draft_usage.output_tokens
        audit.retries_2 = draft_usage.retries
        audit.gate_codes = [*audit.gate_codes, *draft_report.codes()]
        audit.confidence_2 = entwurf.confidence

        betreff = self._betreff(entwurf)
        html_body = render_html_body(entwurf.hinweis_fuer_inhaber, entwurf.plain_body)
        plain_body = render_plain_body(entwurf.hinweis_fuer_inhaber, entwurf.plain_body)
        kopf = triage_payload.reply_headers
        # DECISION: Ohne reply_headers wird der Adapter mit der Phase-1-
        # Signatur aufgerufen, damit bestehende Adapter (und die Tests aus
        # Teil 1) unveraendert funktionieren.
        if kopf is None:
            draft_id = await self._adapter.create_draft(
                thread_id=thread_id,
                subject=betreff,
                html_body=html_body,
                plain_body=plain_body,
            )
        else:
            draft_id = await self._adapter.create_draft(
                thread_id=thread_id,
                subject=betreff,
                html_body=html_body,
                plain_body=plain_body,
                to=kopf.reply_to_address(),
                in_reply_to=kopf.message_id_rfc,
                references=kopf.references,
            )
        label = (
            self._settings.label_draft_placeholder
            if len(entwurf.platzhalter) >= 1
            else self._settings.label_draft_ready
        )
        await self._label(message_id, label, gesetzt)
        return self._abschluss(audit, erfolg, gesetzt, draft_id)

    def _abschluss(
        self,
        audit: AuditRecord,
        status: PipelineStatus,
        gesetzt: Sequence[str],
        draft_id: str | None,
    ) -> PipelineOutcome:
        audit.status = status
        audit.labels_set = list(gesetzt)
        self._audit.record(audit)
        return PipelineOutcome(
            status=status,
            labels_set=list(gesetzt),
            draft_id=draft_id,
            audit=audit,
        )


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------


def _ms_seit(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _kappe_thread(
    nachrichten: Iterable[ThreadMessage], cap: int
) -> list[ThreadMessage]:
    """Kuerzt die Thread-Bodies gemeinsam auf ``cap`` Zeichen.

    Gekuerzt wird von der aeltesten Nachricht her; die neueste Nachricht
    bleibt in jedem Fall unveraendert - sie ist die, auf die geantwortet
    wird.
    """
    liste = list(nachrichten)
    gesamt = sum(len(nachricht.body) for nachricht in liste)
    if gesamt <= cap or len(liste) < 2:
        return liste

    ueberhang = gesamt - cap
    for index in range(len(liste) - 1):
        if ueberhang <= 0:
            break
        body = liste[index].body
        behalten = max(0, len(body) - ueberhang)
        ueberhang -= len(body) - behalten
        liste[index] = liste[index].model_copy(update={"body": body[:behalten]})
    return liste
