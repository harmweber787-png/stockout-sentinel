"""Modul M-1 - saemtliche Pydantic-v2-Modelle.

Enthaelt die Eingabe-Payloads der Integrationsschicht, die beiden
LLM-Ausgabemodelle (inklusive JSON-Schema-Export fuer Structured
Outputs), die Gate-Ergebnisse, den Audit-Record und das Pipeline-Ergebnis.

Die Validatoren hier sind rein strukturell. Saemtliche Geschaeftsregeln
("Code schlaegt Modell") liegen in den Gates von ``pipeline.py``.
"""

from __future__ import annotations

import warnings
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Final, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AntwortTyp",
    "AttachmentMeta",
    "AuditRecord",
    "CompanyContext",
    "CompanyProfile",
    "DrafterPayloadV1",
    "DrafterResultV1",
    "Dringlichkeit",
    "FehlendeInfo",
    "FristInfo",
    "GateReport",
    "GateSeverity",
    "GateViolation",
    "Kategorie",
    "LabelZusatz",
    "Modus",
    "PipelineOutcome",
    "PipelineStatus",
    "Platzhalter",
    "Rechtsfolge",
    "Register",
    "RisikoFlag",
    "Routing",
    "RoutingAktion",
    "Sender",
    "Sprache",
    "SpracheAntwort",
    "ThreadMessage",
    "TriagePayloadV1",
    "TriageResultV1",
    "TriageSummary",
    "Usage",
    "json_schema_fuer",
]


# Der Feldname ``register`` ist von der Spezifikation vorgegeben und
# verdeckt ein Attribut der Pydantic-Basisklasse. ``Field(...)`` stellt
# sicher, dass das Feld ohne Default und damit als ``required`` im
# JSON-Schema landet; die rein informative Warnung wird gezielt fuer
# genau diese Meldung unterdrueckt.
warnings.filterwarnings(
    "ignore", message='Field name "register" .*', category=UserWarning
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Kategorie(StrEnum):
    """Fachliche Grobklassierung einer eingehenden Nachricht."""

    ANFRAGE_OFFERTE = "anfrage_offerte"
    TERMINANFRAGE = "terminanfrage"
    TERMINAENDERUNG = "terminaenderung"
    AUFTRAG_BESTAETIGUNG = "auftrag_bestaetigung"
    LIEFERANT_BESTELLUNG = "lieferant_bestellung"
    RECHNUNG_BELEG_EINGANG = "rechnung_beleg_eingang"
    ZAHLUNGSAUFFORDERUNG = "zahlungsaufforderung"
    REKLAMATION_MAENGEL = "reklamation_maengel"
    FRIST_RECHTLICH = "frist_rechtlich"
    BEHOERDE_AMT = "behoerde_amt"
    NEWSLETTER_WERBUNG = "newsletter_werbung"
    SPAM_PHISHING = "spam_phishing"
    SYSTEMNACHRICHT = "systemnachricht"
    SONSTIGES = "sonstiges"


class Dringlichkeit(StrEnum):
    KEINE = "keine"
    DIESE_WOCHE = "diese_woche"
    HEUTE = "heute"
    SOFORT = "sofort"


class AntwortTyp(StrEnum):
    KEINE = "keine"
    EINGANGSBESTAETIGUNG = "eingangsbestaetigung"
    INHALTLICH = "inhaltlich"


class Sprache(StrEnum):
    DE = "de"
    DE_CH_MUNDART = "de-CH-mundart"
    FR = "fr"
    IT = "it"
    EN = "en"
    ANDERE = "andere"


class SpracheAntwort(StrEnum):
    DE_CH = "de-CH"
    FR_CH = "fr-CH"
    IT_CH = "it-CH"
    EN = "en"


class Register(StrEnum):
    DU = "du"
    SIE = "sie"
    UNKLAR = "unklar"


class Rechtsfolge(StrEnum):
    GESETZLICH = "gesetzlich"
    BETREIBUNG = "betreibung"
    BEHOERDLICH = "behoerdlich"
    VERTRAGLICH = "vertraglich"
    UNKLAR = "unklar"


class RisikoFlag(StrEnum):
    """Geschlossene Liste. Ein unbekanntes Flag wird vom Schema verworfen."""

    HAFTUNG_SCHADEN = "haftung_schaden"
    SCHULDANERKENNUNG_RISIKO = "schuldanerkennung_risiko"
    DROHUNG_RECHTSWEG = "drohung_rechtsweg"
    ZAHLUNG_IBAN_AENDERUNG = "zahlung_iban_aenderung"
    CEO_FRAUD_MUSTER = "ceo_fraud_muster"
    PHISHING_VERDACHT = "phishing_verdacht"
    BESONDERS_SCHUETZENSWERTE_PERSONENDATEN = "besonders_schuetzenswerte_personendaten"


class FehlendeInfo(StrEnum):
    ANHANG_INHALT = "anhang_inhalt"
    PREIS = "preis"
    TERMIN_VERFUEGBARKEIT = "termin_verfuegbarkeit"
    PROJEKTSTATUS = "projektstatus"
    TECHNISCHE_DATEN = "technische_daten"
    LIEFERFRIST = "lieferfrist"
    ANSPRECHPERSON = "ansprechperson"
    SONSTIGES = "sonstiges"


class RoutingAktion(StrEnum):
    DRAFT_INHALTLICH = "draft_inhaltlich"
    DRAFT_EINGANGSBESTAETIGUNG = "draft_eingangsbestaetigung"
    NUR_LABELN = "nur_labeln"
    ESKALIEREN = "eskalieren"
    IGNORIEREN = "ignorieren"


class LabelZusatz(StrEnum):
    """Vom Modell vorgeschlagenes Zusatzlabel.

    DECISION: Der Enum-Wert ist Teil des LLM-Schema-Vertrags und daher
    hier woertlich fixiert. Gesetzt wird im Postfach ausschliesslich
    ``Settings.label_escalate``; dieser Enum steuert nur den Vorschlag.
    """

    KEINS = "keins"
    ACHTUNG_CHEF = "AI/99-Achtung-Chef"


class Modus(StrEnum):
    DRAFT_EINGANGSBESTAETIGUNG = "draft_eingangsbestaetigung"
    DRAFT_INHALTLICH = "draft_inhaltlich"


GateSeverity = Literal["autofix", "retry", "fatal"]

PipelineStatus = Literal[
    "DRAFT_CREATED",
    "ESCALATED_NO_DRAFT",
    "ESCALATED_WITH_DRAFT",
    "LABELED_ONLY",
    "IGNORED",
    "SKIPPED_ALREADY_PROCESSED",
    "FAILED",
]


# ---------------------------------------------------------------------------
# Eingabe-Modelle (Abschnitt 6.1)
# ---------------------------------------------------------------------------


class AttachmentMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    mime_type: str
    size_kb: int


class Sender(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    email: str


class CompanyContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_name: str
    owner_name: str
    location: str


class TriagePayloadV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    thread_id: str
    received_at: datetime
    sender: Sender
    subject: str
    cleaned_body: str
    attachments_meta: list[AttachmentMeta]
    company_context: CompanyContext


class ThreadMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sender_name: str
    sender_email: str
    timestamp: datetime
    body: str


class CompanyProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_name: str
    owner_name: str
    tone_of_voice: str
    signature_block: str


class TriageSummary(BaseModel):
    """Teilmenge von ``TriageResultV1``, die Stufe 2 sieht."""

    model_config = ConfigDict(extra="forbid")

    kategorie: Kategorie
    antwort_typ: AntwortTyp
    # ``Field(...)`` verhindert, dass Pydantic das gleichnamige
    # Attribut der Basisklasse als Default interpretiert.
    register: Register = Field(...)
    sprache_eingang: Sprache
    frist_datum: date | None
    risiko_flags: list[RisikoFlag]
    fehlende_infos: list[FehlendeInfo]


class DrafterPayloadV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    thread_id: str
    original_subject: str
    extracted_attachment_text: str | None
    triage: TriageSummary
    thread_context: list[ThreadMessage]
    company_profile: CompanyProfile


# ---------------------------------------------------------------------------
# LLM-Ausgabe-Modelle (Abschnitt 6.2)
# ---------------------------------------------------------------------------


class FristInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    erkannt: bool
    datum: date | None
    beleg: str | None
    rechtsfolge: Rechtsfolge | None


class Routing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aktion: RoutingAktion
    label_zusatz: LabelZusatz
    grund: str


class TriageResultV1(BaseModel):
    """Ausgabe Stufe 1."""

    model_config = ConfigDict(extra="forbid")

    message_id: str
    kategorie: Kategorie
    unterkategorie: str
    dringlichkeit: Dringlichkeit
    braucht_antwort: bool
    antwort_typ: AntwortTyp
    sprache: Sprache
    register: Register = Field(...)
    attachment_relevant: bool
    frist: FristInfo
    risiko_flags: list[RisikoFlag]
    fehlende_infos: list[FehlendeInfo]
    confidence: float = Field(ge=0.0, le=1.0)
    routing: Routing
    kurzbegruendung: str = Field(max_length=400)

    def to_summary(self) -> TriageSummary:
        """Baut die an Stufe 2 uebergebene Teilmenge."""
        return TriageSummary(
            kategorie=self.kategorie,
            antwort_typ=self.antwort_typ,
            register=self.register,
            sprache_eingang=self.sprache,
            frist_datum=self.frist.datum if self.frist.erkannt else None,
            risiko_flags=list(self.risiko_flags),
            fehlende_infos=list(self.fehlende_infos),
        )


class Platzhalter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    hinweis: str


class DrafterResultV1(BaseModel):
    """Ausgabe Stufe 2."""

    model_config = ConfigDict(extra="forbid")

    message_id: str
    modus: Modus
    sprache_antwort: SpracheAntwort
    register_verwendet: Register
    subject_reply: str = Field(max_length=120)
    plain_body: str = Field(max_length=2500)
    platzhalter: list[Platzhalter]
    enthaelt_zusage: bool
    hinweis_fuer_inhaber: str = Field(max_length=240)
    confidence: float = Field(ge=0.0, le=1.0)


def json_schema_fuer(modell: type[BaseModel]) -> dict[str, Any]:
    """JSON-Schema eines Modells fuer Structured Outputs / Tool-Use.

    ``extra="forbid"`` erzeugt ``additionalProperties: false``; alle
    Felder ohne Default landen in ``required``. Titel und Beschreibungen
    werden entfernt, damit das Schema kompakt bleibt und keine
    Dokumentationsartefakte an das Modell gehen.
    """
    schema = modell.model_json_schema()
    return cast(dict[str, Any], _ohne_titel(schema))


def _ohne_titel(knoten: Any) -> Any:
    if isinstance(knoten, dict):
        return {
            schluessel: _ohne_titel(wert)
            for schluessel, wert in knoten.items()
            if schluessel not in {"title", "description"}
        }
    if isinstance(knoten, list):
        return [_ohne_titel(eintrag) for eintrag in knoten]
    return knoten


#: Vorberechnete Schemata (die Modelle sind unveraenderlich).
TRIAGE_JSON_SCHEMA: Final[dict[str, Any]] = json_schema_fuer(TriageResultV1)
DRAFTER_JSON_SCHEMA: Final[dict[str, Any]] = json_schema_fuer(DrafterResultV1)


# ---------------------------------------------------------------------------
# Gate-Ergebnisse (Abschnitt 6.3)
# ---------------------------------------------------------------------------


class GateViolation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: GateSeverity
    #: Kurze, PII-freie Anweisung an das Modell. Enthaelt nie ein Zitat
    #: aus der Mail und nie einen Ausschnitt des Entwurfstexts.
    feedback: str


class GateReport(BaseModel):
    """Ergebnis eines Gate-Durchlaufs.

    DECISION: ``stats`` ist eine Erweiterung gegenueber der Spezifikation.
    Es haelt ausschliesslich Zaehler (z. B. ``eszett_replacements``) -
    also Nicht-PII - und macht die Autofixes testbar, ohne dass ein Gate
    Text nach aussen geben muesste.
    """

    model_config = ConfigDict(extra="forbid")

    violations: list[GateViolation] = Field(default_factory=list)
    stats: dict[str, int] = Field(default_factory=dict)

    def add(self, code: str, severity: GateSeverity, feedback: str) -> None:
        self.violations.append(
            GateViolation(code=code, severity=severity, feedback=feedback)
        )

    def needs_retry(self) -> bool:
        return any(v.severity == "retry" for v in self.violations)

    def is_fatal(self) -> bool:
        return any(v.severity == "fatal" for v in self.violations)

    def codes(self) -> list[str]:
        return [v.code for v in self.violations]

    def fatal_code(self) -> str | None:
        for violation in self.violations:
            if violation.severity == "fatal":
                return violation.code
        return None

    def retry_feedback(self) -> str:
        """Gebuendeltes Feedback aller Retry-Verstoesse (ohne Mail-Zitate)."""
        return "\n".join(
            f"- {v.code}: {v.feedback}"
            for v in self.violations
            if v.severity == "retry"
        )

    def merge(self, other: GateReport) -> GateReport:
        zusammen = GateReport(
            violations=[*self.violations, *other.violations],
            stats=dict(self.stats),
        )
        for schluessel, wert in other.stats.items():
            zusammen.stats[schluessel] = zusammen.stats.get(schluessel, 0) + wert
        return zusammen


# ---------------------------------------------------------------------------
# Telemetrie
# ---------------------------------------------------------------------------


class Usage(BaseModel):
    """Verbrauch und Telemetrie einer Stufe.

    DECISION: Um den Audit-Record ohne zusaetzliche Rueckgabewerte fuellen
    zu koennen, traegt ``Usage`` neben den Tokenzahlen auch die
    Retry-Anzahl und die Latenz. Alle vier Werte sind Nicht-PII.
    """

    model_config = ConfigDict(extra="forbid")

    input_tokens: int = 0
    output_tokens: int = 0
    retries: int = 0
    latency_ms: int = 0

    def plus_tokens(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens


# ---------------------------------------------------------------------------
# Audit (Abschnitt 6.10)
# ---------------------------------------------------------------------------


class AuditRecord(BaseModel):
    """Zero-Data-Retention: ausschliesslich Nicht-PII-Felder.

    Verboten und deshalb nicht vorhanden: Name, E-Mail-Adresse, Betreff,
    ``cleaned_body``, ``frist.beleg``, Entwurfstext, ``hinweis_fuer_inhaber``.
    """

    model_config = ConfigDict(extra="forbid")

    message_id_hash: str
    thread_id_hash: str
    stage_1_ms: int = 0
    stage_2_ms: int = 0
    tokens_in_1: int = 0
    tokens_out_1: int = 0
    tokens_in_2: int = 0
    tokens_out_2: int = 0
    kategorie: Kategorie | None = None
    dringlichkeit: Dringlichkeit | None = None
    confidence_1: float | None = None
    confidence_2: float | None = None
    routing_aktion: RoutingAktion | None = None
    modus: Modus | None = None
    labels_set: list[str] = Field(default_factory=list)
    gate_codes: list[str] = Field(default_factory=list)
    retries_1: int = 0
    retries_2: int = 0
    status: PipelineStatus = "FAILED"
    error_code: str | None = None


class PipelineOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: PipelineStatus
    labels_set: list[str]
    draft_id: str | None
    audit: AuditRecord
