"""ERP-Negativfilter.

Betriebe mit einem Standard-ERP haben den Medienbruch bereits geloest und sind
keine Zielbetriebe. Umgekehrt liefert das Gate positive Schmerzsignale
(Excel/Office als einzige Software, Freemail als Firmenadresse) als Rohwert
fuer den Teilscore "ERP-Abwesenheit" - die Gewichtung passiert in ``scoring/``,
nicht hier.

Fehlerasymmetrie: hier ist der **Fehlalarm** der teure Fehler. Ein faelschlich
ausgeschlossener Betrieb faellt nie auf, weil er nie mehr auftaucht. Das Gate
faehrt deshalb konservativ - nur ein harter Nachweis schliesst aus, ein
blosser Verdacht laesst den Betrieb drin und setzt ``erp_review_needed``. Die
Stellschraube steht in ``config/erp_rules.yaml`` unter ``sensitivity``.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from kmu_discovery.config.rules import ErpRules, ErpVendor, TermPattern, load_erp_rules
from kmu_discovery.gates.base import (
    ScanField,
    dedupe_matches,
    find_pattern_hits,
    scan_fields,
    to_rule_match,
)
from kmu_discovery.gates.text import MatchMode
from kmu_discovery.models import (
    CompanyProfile,
    ErpGateResult,
    ErpStatus,
    GateOutcome,
    MatchField,
    RuleMatch,
    Severity,
)

__all__ = ["ERP_REVIEW_FLAG", "ErpGate", "ErpGateResult", "ErpStatus"]

ERP_REVIEW_FLAG = "erp_review_needed"

#: In diesen Feldern ist eine Anbieternennung ein harter Nachweis: ein
#: Stelleninserat verlangt Kenntnisse im real eingesetzten System, eine
#: Portal-Domain ist eine Installation, der Zweckartikel ist amtlich.
_HARD_FIELDS = frozenset({MatchField.JOB_POSTING, MatchField.DOMAIN, MatchField.ZWECK})

_MAX_MATCHES = 200


class ErpGate:
    """Deterministischer ERP-Negativfilter.

    >>> from datetime import UTC, datetime
    >>> from kmu_discovery.models import CompanyProfile, DocumentKind, TextDocument
    >>> company = CompanyProfile(
    ...     uid="CHE-123.456.789",
    ...     name="Muster Logistik AG",
    ...     documents=[TextDocument(
    ...         kind=DocumentKind.JOB_POSTING,
    ...         url="https://muster.ch/jobs/sachbearbeiter",
    ...         text="Abacus-Kenntnisse vorausgesetzt.",
    ...         retrieved_at=datetime(2026, 9, 14, tzinfo=UTC),
    ...     )],
    ... )
    >>> result = ErpGate().evaluate(company)
    >>> result.status
    <ErpStatus.DETECTED: 'erp_detected'>
    >>> result.outcome
    <GateOutcome.REJECT: 'reject'>
    """

    name = "erp"

    def __init__(self, rules: ErpRules | None = None, rules_path: Path | None = None) -> None:
        """Laedt den Regelkatalog; ``rules`` hat Vorrang vor ``rules_path``."""
        self._rules = rules or load_erp_rules(rules_path)

    @property
    def rules(self) -> ErpRules:
        """Der aktive Regelkatalog."""
        return self._rules

    def evaluate(self, company: CompanyProfile) -> ErpGateResult:
        """Prueft den Betrieb auf ERP-Signale und ERP-Abwesenheitssignale."""
        fields = scan_fields(company)
        fields.extend(_domain_fields(company))

        matches: list[RuleMatch] = []
        hard_vendors: list[str] = []
        soft_vendors: list[str] = []

        for vendor in self._rules.vendors:
            vendor_matches = _vendor_matches(vendor, fields)
            matches.extend(vendor_matches)
            severities = {match.severity for match in vendor_matches}
            if Severity.REJECT in severities:
                hard_vendors.append(vendor.id)
            elif Severity.REVIEW in severities:
                soft_vendors.append(vendor.id)

        pain_ids, pain_matches = _pain_matches(self._rules.pain_signals, fields)
        matches.extend(pain_matches)
        free_mail = tuple(
            sorted({d for d in company.email_domains if d in self._rules.free_mail_domains})
        )

        if hard_vendors:
            status = ErpStatus.DETECTED
            outcome = GateOutcome.REJECT
        elif soft_vendors:
            status = ErpStatus.SUSPECTED
            outcome = self._rules.sensitivity.suspected_outcome
        elif len(pain_ids) >= self._rules.pain_signals_for_absence or free_mail:
            status = ErpStatus.ABSENCE_INDICATED
            outcome = GateOutcome.PASS
        else:
            status = ErpStatus.NO_SIGNAL
            outcome = GateOutcome.PASS

        notes: list[str] = []
        if status is ErpStatus.NO_SIGNAL:
            notes.append(
                "Weder ERP- noch Schmerzsignale gefunden - Betrieb bleibt drin, aber die "
                "Pruefgrundlage ist duenn (moegliche Dunkelziffer)."
            )

        flags: list[str] = [f"erp_detected:{vendor}" for vendor in hard_vendors]
        if soft_vendors:
            # Verdacht wird immer sichtbar gemacht, auch wenn er den Betrieb
            # konservativ nicht ausschliesst.
            flags.append(ERP_REVIEW_FLAG)
            flags.extend(f"erp_suspected:{vendor}" for vendor in soft_vendors)
        if free_mail:
            flags.append("freemail_domain")

        return ErpGateResult(
            gate=self.name,
            outcome=outcome,
            matches=tuple(dedupe_matches(matches)[:_MAX_MATCHES]),
            flags=tuple(flags),
            notes=tuple(notes),
            status=status,
            vendors=tuple(hard_vendors + soft_vendors),
            pain_signals=tuple(sorted(pain_ids)),
            free_mail_domains=free_mail,
            absence_signal=_absence_signal(status, pain_ids, free_mail),
        )


# -- intern -------------------------------------------------------------- #


def _domain_fields(company: CompanyProfile) -> list[ScanField]:
    """Website-Host und Mail-Domains als eigenes Pruef-Feld."""
    fields: list[ScanField] = []
    if company.website:
        host = urlsplit(company.website).netloc.lower()
        if host:
            fields.append(ScanField(MatchField.DOMAIN, host, company.website))
    for domain in company.email_domains:
        fields.append(ScanField(MatchField.DOMAIN, domain, None))
    return fields


def _vendor_patterns(vendor: ErpVendor) -> tuple[TermPattern, ...]:
    domain_patterns = tuple(
        TermPattern(
            id=f"{vendor.id}_domain_{index}",
            term=domain,
            mode=MatchMode.FREE,
            decisive=True,
            note=f"Portal-/Cloud-Domain von {vendor.label}",
        )
        for index, domain in enumerate(vendor.domains)
    )
    return vendor.patterns + domain_patterns


def _vendor_matches(vendor: ErpVendor, fields: list[ScanField]) -> list[RuleMatch]:
    patterns = _vendor_patterns(vendor)
    matches: list[RuleMatch] = []
    for scan in fields:
        for hit in find_pattern_hits(scan, patterns):
            severity = (
                Severity.REJECT
                if scan.field in _HARD_FIELDS or hit.pattern.decisive
                else Severity.REVIEW
            )
            matches.append(to_rule_match(hit, vendor.id, vendor.label, severity))
    return matches


def _pain_matches(
    patterns: tuple[TermPattern, ...], fields: list[ScanField]
) -> tuple[set[str], list[RuleMatch]]:
    found: set[str] = set()
    matches: list[RuleMatch] = []
    for scan in fields:
        if scan.field is MatchField.DOMAIN:
            continue
        for hit in find_pattern_hits(scan, patterns):
            if hit.pattern.id in found:
                continue
            found.add(hit.pattern.id)
            matches.append(
                to_rule_match(hit, "pain_signal", "ERP-Abwesenheitssignal", Severity.INFO)
            )
    return found, matches


def _absence_signal(status: ErpStatus, pain_ids: set[str], free_mail: tuple[str, ...]) -> float:
    """Rohsignal fuer den Teilscore 'ERP-Abwesenheit' (0-1).

    Bewusst simpel und deterministisch: ein erkanntes ERP nullt das Signal, ein
    verdaechtigtes daempft es stark, sonst zaehlen die positiven Schmerzsignale.
    """
    if status is ErpStatus.DETECTED:
        return 0.0
    if status is ErpStatus.SUSPECTED:
        return 0.25
    score = 0.4 + 0.1 * len(pain_ids) + (0.15 if free_mail else 0.0)
    return round(min(score, 1.0), 3)
