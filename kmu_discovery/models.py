"""Kern-Datenmodelle der Problem-Discovery-Engine.

Alle Module tauschen ausschliesslich diese Pydantic-Modelle aus, niemals
``dict[str, Any]``. Grundregel des Projekts: **ohne Beleg kein Signal** - jedes
extrahierte Merkmal traegt eine :class:`Evidence` mit Quell-URL und Originalzitat.

revDSG-Hinweis: :class:`Entscheider` enthaelt Personendaten natuerlicher
Personen. Diese duerfen nur aus offiziellen Registern (Zefix, SHAB) stammen und
nur zweckgebunden fuer die manuelle Kontaktaufnahme verwendet werden. Es findet
keine Profilbildung ueber Privatpersonen statt.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

__all__ = [
    "CANTONS",
    "CompanyProfile",
    "DocumentKind",
    "Entscheider",
    "ErpGateResult",
    "ErpStatus",
    "Evidence",
    "GateOutcome",
    "GateResult",
    "GateResultUnion",
    "Groessenklasse",
    "MatchField",
    "Rechtsform",
    "RuleMatch",
    "Severity",
    "SourceType",
    "Standort",
    "TextDocument",
    "normalize_noga",
    "uid_check_digit",
    "uid_checksum_valid",
]


# --------------------------------------------------------------------------
# Primitive Typen
# --------------------------------------------------------------------------

_UID_RE = re.compile(r"^CHE-\d{3}\.\d{3}\.\d{3}$")
_NOGA_RE = re.compile(r"^\d{2,6}$")

#: Die 26 Kantonskuerzel.
CANTONS: frozenset[str] = frozenset(
    {
        "AG", "AI", "AR", "BE", "BL", "BS", "FR", "GE", "GL", "GR", "JU", "LU",
        "NE", "NW", "OW", "SG", "SH", "SO", "SZ", "TG", "TI", "UR", "VD", "VS",
        "ZG", "ZH",
    }
)


def _validate_http_url(value: str) -> str:
    """Erlaubt ausschliesslich absolute http(s)-URLs."""
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError(f"keine absolute http(s)-URL: {value!r}")
    return value


def _validate_uid(value: str) -> str:
    """Normalisiert und prueft das UID-Format ``CHE-123.456.789``."""
    compact = re.sub(r"[^0-9A-Za-z]", "", value).upper()
    if not compact.startswith("CHE") or len(compact) != 12 or not compact[3:].isdigit():
        raise ValueError(f"keine gueltige UID: {value!r}")
    digits = compact[3:]
    formatted = f"CHE-{digits[0:3]}.{digits[3:6]}.{digits[6:9]}"
    if not _UID_RE.match(formatted):  # pragma: no cover - durch Konstruktion erfuellt
        raise ValueError(f"keine gueltige UID: {value!r}")
    return formatted


def _validate_canton(value: str) -> str:
    upper = value.strip().upper()
    if upper not in CANTONS:
        raise ValueError(f"unbekanntes Kantonskuerzel: {value!r}")
    return upper


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError("Zeitstempel muss zeitzonenbehaftet sein")
    return value


SourceUrl = Annotated[str, StringConstraints(min_length=8), AfterValidator(_validate_http_url)]
Uid = Annotated[str, AfterValidator(_validate_uid)]
Canton = Annotated[str, AfterValidator(_validate_canton)]
AwareDatetime = Annotated[datetime, AfterValidator(_require_aware)]


def normalize_noga(code: str) -> str:
    """Reduziert einen NOGA-Code auf seine Ziffernfolge.

    Die Schreibweise der NOGA-2008-Codes schwankt zwischen Quellen
    (``86.21``, ``8621``, ``86.21.0``). Fuer Praefix-Vergleiche wird deshalb
    notationsunabhaengig auf reine Ziffern normalisiert.

    >>> normalize_noga("86.21")
    '8621'
    """
    digits = re.sub(r"\D", "", code)
    if not _NOGA_RE.match(digits):
        raise ValueError(f"kein plausibler NOGA-Code: {code!r}")
    return digits


# --------------------------------------------------------------------------
# UID-Pruefziffer
# --------------------------------------------------------------------------

_UID_WEIGHTS: tuple[int, ...] = (5, 4, 3, 2, 7, 6, 5, 4)


def uid_check_digit(digits: str) -> int:
    """Berechnet die Mod-11-Pruefziffer der ersten acht UID-Stellen.

    ACHTUNG - VERIFIKATIONSSCHULD: Gewichtungen und Modulo-Regel stammen aus der
    BFS-Dokumentation zur UID und sind hier **nicht** gegen einen amtlichen
    Testvektor verifiziert. Die Funktion ist deshalb bewusst nur beratend; die
    Modellvalidierung von :class:`CompanyProfile` prueft ausschliesslich das
    Format. Vor Produktivnutzung gegen den UID-Webservice des BFS gegenpruefen.

    :raises ValueError: wenn ``digits`` keine acht Ziffern enthaelt oder die
        Pruefsumme rechnerisch ungueltig ist (Rest 10).
    """
    if len(digits) != 8 or not digits.isdigit():
        raise ValueError("genau acht Ziffern erwartet")
    total = sum(int(d) * w for d, w in zip(digits, _UID_WEIGHTS, strict=True))
    remainder = 11 - (total % 11)
    if remainder == 10:
        raise ValueError("rechnerisch ungueltige UID-Basis")
    return 0 if remainder == 11 else remainder


def uid_checksum_valid(uid: str) -> bool:
    """Beratende Pruefziffernkontrolle einer UID. Siehe :func:`uid_check_digit`."""
    try:
        normalized = _validate_uid(uid)
        digits = re.sub(r"\D", "", normalized)  # 9 Ziffern, 'CHE' faellt weg
        return uid_check_digit(digits[:8]) == int(digits[8])
    except ValueError:
        return False


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------


class SourceType(StrEnum):
    """Herkunft eines Belegs. Bestimmt mit, wie belastbar ein Signal ist."""

    ZEFIX = "zefix"
    LINDAS = "lindas"
    UID_BFS = "uid_bfs"
    SHAB = "shab"
    OPENDATA_SWISS = "opendata_swiss"
    SIMAP = "simap"
    COMPANY_WEBSITE = "company_website"
    JOB_POSTING = "job_posting"
    MANUAL = "manual"


class DocumentKind(StrEnum):
    """Art eines eingesammelten Textdokuments."""

    WEBSITE_PAGE = "website_page"
    IMPRESSUM = "impressum"
    CONTACT_PAGE = "contact_page"
    FORMS_PAGE = "forms_page"
    JOB_POSTING = "job_posting"
    REGISTER_PURPOSE = "register_purpose"
    OTHER = "other"


class Rechtsform(StrEnum):
    """Rechtsform gemaess Handelsregister."""

    EINZELUNTERNEHMEN = "einzelunternehmen"
    KOLLEKTIVGESELLSCHAFT = "kollektivgesellschaft"
    KOMMANDITGESELLSCHAFT = "kommanditgesellschaft"
    AG = "ag"
    GMBH = "gmbh"
    GENOSSENSCHAFT = "genossenschaft"
    VEREIN = "verein"
    STIFTUNG = "stiftung"
    ZWEIGNIEDERLASSUNG = "zweigniederlassung"
    OEFFENTLICH_RECHTLICH = "oeffentlich_rechtlich"
    UNBEKANNT = "unbekannt"


class Groessenklasse(StrEnum):
    """Groessenklassen nach BFS-Konvention (Vollzeitaequivalente)."""

    MIKRO = "mikro"        # 0-9 VZA
    KLEIN = "klein"        # 10-49 VZA
    MITTEL = "mittel"      # 50-249 VZA
    GROSS = "gross"        # 250+ VZA
    UNBEKANNT = "unbekannt"


class GateOutcome(StrEnum):
    """Ergebnis eines deterministischen Gates."""

    PASS = "pass"
    REVIEW = "review"
    REJECT = "reject"

    @property
    def rank(self) -> int:
        """Strenge-Rang; hoeher gewinnt bei der Aggregation."""
        return {"pass": 0, "review": 1, "reject": 2}[self.value]


class Severity(StrEnum):
    """Wirkung eines einzelnen Regeltreffers."""

    REJECT = "reject"
    REVIEW = "review"
    INFO = "info"

    @classmethod
    def from_outcome(cls, outcome: GateOutcome) -> Severity:
        """Gegenrichtung zu :meth:`to_outcome` - fuer konfigurierte Urteile.

        >>> Severity.from_outcome(GateOutcome.PASS)
        <Severity.INFO: 'info'>
        """
        return {
            GateOutcome.REJECT: cls.REJECT,
            GateOutcome.REVIEW: cls.REVIEW,
            GateOutcome.PASS: cls.INFO,
        }[outcome]

    def to_outcome(self) -> GateOutcome:
        """Uebersetzt die Trefferwirkung in ein Gate-Urteil."""
        return {
            Severity.REJECT: GateOutcome.REJECT,
            Severity.REVIEW: GateOutcome.REVIEW,
            Severity.INFO: GateOutcome.PASS,
        }[self]


class MatchField(StrEnum):
    """In welchem Feld ein Regeltreffer entstanden ist."""

    NOGA = "noga"
    ZWECK = "zweck"
    NAME = "name"
    WEBSITE = "website"
    JOB_POSTING = "job_posting"
    DOMAIN = "domain"
    EMAIL = "email"


# --------------------------------------------------------------------------
# Belege und Betriebsprofil
# --------------------------------------------------------------------------


class Evidence(BaseModel):
    """Ein Beleg: Quelle, URL, Originalzitat, Abrufzeitpunkt.

    >>> from datetime import UTC, datetime
    >>> Evidence(
    ...     source_type=SourceType.COMPANY_WEBSITE,
    ...     source_url="https://example.ch/formulare",
    ...     quote="Bitte Formular ausdrucken und per Fax senden.",
    ...     retrieved_at=datetime(2026, 9, 14, tzinfo=UTC),
    ... ).source_type
    <SourceType.COMPANY_WEBSITE: 'company_website'>
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_type: SourceType
    source_url: SourceUrl
    quote: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    retrieved_at: AwareDatetime
    locator: str | None = Field(
        default=None, description="Optionaler Feinzeiger (CSS-Selektor, XML-Pfad, Feldname)."
    )


class Standort(BaseModel):
    """Sitz des Betriebs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kanton: Canton
    plz: Annotated[str, StringConstraints(pattern=r"^\d{4}$")] | None = None
    ort: str | None = None


class Entscheider(BaseModel):
    """Zeichnungsberechtigte Person aus einem offiziellen Register.

    revDSG: nur aus Zefix/SHAB, nur fuer die Kontaktaufnahme. Der Beleg ist
    Pflicht, damit die Herkunft jederzeit nachweisbar bleibt.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    funktion: str | None = None
    evidence: Evidence

    @field_validator("evidence")
    @classmethod
    def _only_official_registers(cls, value: Evidence) -> Evidence:
        allowed = {SourceType.ZEFIX, SourceType.LINDAS, SourceType.SHAB}
        if value.source_type not in allowed:
            raise ValueError(
                "Personendaten nur aus offiziellen Registern (Zefix, LINDAS, SHAB); "
                f"erhalten: {value.source_type}"
            )
        return value


class TextDocument(BaseModel):
    """Ein eingesammelter Text, auf dem Gates und Extraktion arbeiten."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: DocumentKind
    url: SourceUrl | None = None
    text: str
    retrieved_at: AwareDatetime

    @property
    def match_field(self) -> MatchField:
        """Auf welches Regelfeld dieses Dokument abgebildet wird."""
        if self.kind is DocumentKind.JOB_POSTING:
            return MatchField.JOB_POSTING
        if self.kind is DocumentKind.REGISTER_PURPOSE:
            return MatchField.ZWECK
        return MatchField.WEBSITE


class CompanyProfile(BaseModel):
    """Alles, was vor dem Scoring ueber einen Betrieb bekannt ist."""

    model_config = ConfigDict(extra="forbid")

    uid: Uid
    name: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    rechtsform: Rechtsform = Rechtsform.UNBEKANNT
    noga_codes: list[str] = Field(default_factory=list)
    groessenklasse: Groessenklasse = Groessenklasse.UNBEKANNT
    standort: Standort | None = None
    zweck: str | None = Field(default=None, description="Zweckartikel aus dem Handelsregister.")
    website: SourceUrl | None = None
    emails: list[str] = Field(default_factory=list)
    documents: list[TextDocument] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)

    @field_validator("noga_codes")
    @classmethod
    def _normalize_noga_codes(cls, value: list[str]) -> list[str]:
        return [normalize_noga(code) for code in value]

    @field_validator("emails")
    @classmethod
    def _normalize_emails(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for raw in value:
            candidate = raw.strip().lower()
            if "@" not in candidate or candidate.startswith("@") or candidate.endswith("@"):
                raise ValueError(f"keine plausible E-Mail-Adresse: {raw!r}")
            normalized.append(candidate)
        return normalized

    @property
    def email_domains(self) -> list[str]:
        """Domain-Teil aller hinterlegten E-Mail-Adressen."""
        return [address.rsplit("@", 1)[1] for address in self.emails]


# --------------------------------------------------------------------------
# Gate-Ergebnisse
# --------------------------------------------------------------------------


class RuleMatch(BaseModel):
    """Ein einzelner Regeltreffer inklusive Beleg."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    domain_id: str
    label: str
    field: MatchField
    severity: Severity
    matched_text: str
    context_quote: str
    source_url: str | None = None


class GateResult(BaseModel):
    """Ergebnis eines Gates. ``outcome`` ist immer das strengste Treffer-Urteil.

    ``kind`` ist der Diskriminator fuer Gate-spezifische Unterklassen. Ohne ihn
    verlieren ``model_dump``/``model_validate`` die Zusatzfelder einer
    Unterklasse stillschweigend - genau das darf bei Belegen nicht passieren.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["gate"] = "gate"
    gate: str
    outcome: GateOutcome
    matches: tuple[RuleMatch, ...] = ()
    flags: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        """True, wenn dieses Gate den Betrieb nicht ausschliesst."""
        return self.outcome is not GateOutcome.REJECT

    def reasons(self) -> list[str]:
        """Kurze, menschenlesbare Begruendungen - eine Zeile je Treffer."""
        return [
            f"[{m.severity}] {m.label} ({m.field}): {m.matched_text!r}" for m in self.matches
        ]


class ErpStatus(StrEnum):
    """Befund des ERP-Negativfilters."""

    DETECTED = "erp_detected"
    SUSPECTED = "erp_suspected"
    ABSENCE_INDICATED = "erp_absence_indicated"
    NO_SIGNAL = "no_signal"


class ErpGateResult(GateResult):
    """Gate-Ergebnis mit ERP-Status und Rohsignal fuer den Teilscore."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["erp"] = "erp"  # type: ignore[assignment]
    status: ErpStatus
    vendors: tuple[str, ...] = ()
    pain_signals: tuple[str, ...] = ()
    free_mail_domains: tuple[str, ...] = ()
    absence_signal: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Rohsignal 0-1 fuer ERP-Abwesenheit. Gewichtung erfolgt im Scoring.",
    )


#: Alle Gate-Ergebnistypen, ueber ``kind`` unterscheidbar. Nur so ueberleben
#: Gate-spezifische Felder eine JSON-Runde.
GateResultUnion = Annotated[ErpGateResult | GateResult, Field(discriminator="kind")]
