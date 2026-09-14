"""Haftungs-K.o.-Gate.

Schliesst Betriebe aus, deren Kerntaetigkeit in einem Haftungsfeld liegt
(Gesundheit, Recht, Bau/Handwerk, Personalverleih, Treuhand, Finanz). Rein
deterministisch: NOGA-Praefixe und kuratierte Wortlisten.

Fehlerasymmetrie: hier ist der **verpasste Ausschluss** der teure Fehler. Ein
durchgelassener Betrieb aus einem K.o.-Feld erzeugt ein Produkt, das man nicht
bauen darf, und das faellt erst auf, wenn es zu spaet ist. Das Gate faehrt
deshalb aggressiv - jeder Zweifelsfall wird ausgeschlossen oder mindestens als
``liability_review_needed`` markiert, nie stillschweigend durchgelassen. Die
Stellschrauben stehen in ``config/liability_rules.yaml`` unter ``sensitivity``.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from kmu_discovery.config.rules import LiabilityDomain, LiabilityRules, load_liability_rules
from kmu_discovery.gates.base import (
    PatternHit,
    ScanField,
    dedupe_matches,
    find_pattern_hits,
    noga_prefix_match,
    scan_fields,
    to_rule_match,
)
from kmu_discovery.models import (
    CompanyProfile,
    GateOutcome,
    GateResult,
    MatchField,
    RuleMatch,
    Severity,
)

__all__ = ["LIABILITY_REVIEW_FLAG", "LIABILITY_THIN_EVIDENCE_FLAG", "LiabilityGate"]

LIABILITY_REVIEW_FLAG = "liability_review_needed"
LIABILITY_THIN_EVIDENCE_FLAG = "liability_thin_evidence"

#: Ein Treffer in diesen Feldern beschreibt die Kerntaetigkeit selbst.
_CORE_FIELDS = frozenset({MatchField.ZWECK, MatchField.NAME})

_MAX_MATCHES = 200


class LiabilityGate:
    """Deterministischer Haftungsfilter.

    >>> from datetime import UTC, datetime
    >>> from kmu_discovery.models import CompanyProfile
    >>> company = CompanyProfile(
    ...     uid="CHE-123.456.789",
    ...     name="Zahnarztpraxis Seefeld AG",
    ...     noga_codes=["86.23"],
    ... )
    >>> result = LiabilityGate().evaluate(company)
    >>> result.outcome
    <GateOutcome.REJECT: 'reject'>
    >>> sorted({m.domain_id for m in result.matches})
    ['gesundheit']
    """

    name = "liability"

    def __init__(self, rules: LiabilityRules | None = None, rules_path: Path | None = None) -> None:
        """Laedt den Regelkatalog; ``rules`` hat Vorrang vor ``rules_path``."""
        self._rules = rules or load_liability_rules(rules_path)

    @property
    def rules(self) -> LiabilityRules:
        """Der aktive Regelkatalog."""
        return self._rules

    def evaluate(self, company: CompanyProfile) -> GateResult:
        """Prueft den Betrieb gegen alle Haftungsfelder."""
        fields = scan_fields(company)
        matches: list[RuleMatch] = []
        notes: list[str] = []
        rejected_domains: list[str] = []
        review_domains: list[str] = []

        for domain in self._rules.domains:
            domain_matches = self._evaluate_domain(company, domain, fields)
            matches.extend(domain_matches)
            severities = {match.severity for match in domain_matches}
            if Severity.REJECT in severities:
                rejected_domains.append(domain.id)
            elif Severity.REVIEW in severities:
                review_domains.append(domain.id)

        if not company.noga_codes:
            notes.append("Kein NOGA-Code vorhanden - Haftungspruefung stuetzt sich nur auf Text.")

        matches = dedupe_matches(matches)[:_MAX_MATCHES]
        outcome = (
            GateOutcome.REJECT
            if rejected_domains
            else GateOutcome.REVIEW
            if review_domains
            else GateOutcome.PASS
        )

        flags: list[str] = [f"liability_reject:{domain}" for domain in rejected_domains]
        if review_domains and outcome is GateOutcome.REVIEW:
            flags.extend(f"liability_review:{domain}" for domain in review_domains)

        # Duenne Pruefgrundlage ist kein sauberes PASS: es wurde nichts geprueft,
        # nicht nichts gefunden. Aggressives Profil hebt das Urteil an.
        thin = not company.noga_codes and not any(
            field.field is not MatchField.NAME for field in fields
        )
        if thin:
            notes.append(
                "Weder NOGA-Code noch Zweckartikel oder Dokumente vorhanden - "
                "duenne Pruefgrundlage."
            )
            thin_outcome = self._rules.sensitivity.thin_evidence_outcome
            if thin_outcome.rank > outcome.rank:
                outcome = thin_outcome
            if thin_outcome is not GateOutcome.PASS:
                flags.append(LIABILITY_THIN_EVIDENCE_FLAG)

        if outcome is GateOutcome.REVIEW:
            flags.insert(0, LIABILITY_REVIEW_FLAG)

        return GateResult(
            gate=self.name,
            outcome=outcome,
            matches=tuple(matches),
            flags=tuple(flags),
            notes=tuple(notes),
        )

    # -- intern ---------------------------------------------------------- #

    def _evaluate_domain(
        self, company: CompanyProfile, domain: LiabilityDomain, fields: list[ScanField]
    ) -> list[RuleMatch]:
        matches: list[RuleMatch] = list(_noga_matches(company, domain))

        sensitivity = self._rules.sensitivity
        decisive: list[PatternHit] = []
        vetoed_decisive: list[PatternHit] = []
        weak_by_scope: dict[bool, list[PatternHit]] = defaultdict(list)
        for scan in fields:
            hits = find_pattern_hits(
                scan,
                domain.terms,
                veto_terms=domain.veto_terms,
                veto_window=self._rules.context_veto_window,
            )
            for hit in hits:
                if hit.pattern.decisive:
                    (vetoed_decisive if hit.vetoed else decisive).append(hit)
                elif not hit.vetoed:
                    # Entkraeftete schwache Treffer zaehlen nicht zur Schwelle:
                    # sonst summierten sich lauter Zuliefererbelege zu einem Urteil.
                    weak_by_scope[scan.field in _CORE_FIELDS].append(hit)

        matches.extend(
            to_rule_match(hit, domain.id, domain.label, Severity.REJECT) for hit in decisive
        )
        vetoed_severity = Severity.from_outcome(sensitivity.vetoed_hit_outcome)
        matches.extend(
            to_rule_match(hit, domain.id, domain.label, vetoed_severity)
            for hit in vetoed_decisive
        )

        below = Severity.from_outcome(sensitivity.below_threshold_outcome)
        for is_core, hits in weak_by_scope.items():
            distinct = {hit.pattern.id for hit in hits}
            if len(distinct) >= domain.weak_hits_for_review:
                severity = Severity.REJECT if is_core else Severity.REVIEW
            else:
                severity = below
            matches.extend(
                to_rule_match(hit, domain.id, domain.label, severity) for hit in hits
            )
        return matches


def _noga_matches(company: CompanyProfile, domain: LiabilityDomain) -> list[RuleMatch]:
    matches: list[RuleMatch] = []
    for prefixes, severity in (
        (domain.noga_reject_prefixes, Severity.REJECT),
        (domain.noga_review_prefixes, Severity.REVIEW),
    ):
        found = noga_prefix_match(company.noga_codes, prefixes)
        if found is None:
            continue
        code, prefix = found
        matches.append(
            RuleMatch(
                rule_id=f"noga_{prefix}",
                domain_id=domain.id,
                label=domain.label,
                field=MatchField.NOGA,
                severity=severity,
                matched_text=code,
                context_quote=f"NOGA-Code {code} faellt unter Praefix {prefix} ({domain.label}).",
                source_url=None,
            )
        )
    return matches

