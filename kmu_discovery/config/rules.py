"""Regelmodelle und YAML-Lader fuer die deterministischen Gates.

Wortlisten, NOGA-Praefixe und Schwellenwerte liegen in YAML, damit sie ohne
Code-Aenderung kalibriert werden koennen. Beim Laden werden alle Suchbegriffe
durch dieselbe Faltung geschickt wie der zu pruefende Text - so kann in der
YAML in normaler Schreibweise ("Zahnärztin") formuliert werden.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from kmu_discovery.gates.text import MatchMode, fold
from kmu_discovery.models import GateOutcome, normalize_noga

__all__ = [
    "CONFIG_DIR",
    "ErpRules",
    "ErpSensitivity",
    "ErpVendor",
    "LiabilityDomain",
    "LiabilityRules",
    "LiabilitySensitivity",
    "TermPattern",
    "load_erp_rules",
    "load_liability_rules",
    "load_rules_file",
]

CONFIG_DIR = Path(__file__).resolve().parent

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+$", max_length=64)]


class TermPattern(BaseModel):
    """Ein Suchmuster: entweder Begriff (mit Modus) oder Regex auf dem gefalteten Text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Identifier
    term: str | None = None
    regex: str | None = None
    mode: MatchMode = MatchMode.STEM
    decisive: bool = Field(
        default=False,
        description=(
            "Ein einzelner Treffer genuegt fuer ein hartes Urteil. Nur fuer Begriffe "
            "vergeben, die ausserhalb der Zieltaetigkeit praktisch nie vorkommen."
        ),
    )
    note: str | None = None

    @model_validator(mode="after")
    def _exactly_one_matcher(self) -> Self:
        if (self.term is None) == (self.regex is None):
            raise ValueError(f"{self.id}: genau eines von 'term' oder 'regex' setzen")
        if self.regex is not None:
            if any(char.isupper() for char in self.regex):
                raise ValueError(
                    f"{self.id}: Regex laeuft gegen gefalteten Text - keine Grossbuchstaben"
                )
            if any(char in self.regex for char in "äöüßÄÖÜ"):
                raise ValueError(
                    f"{self.id}: Regex laeuft gegen gefalteten Text - "
                    "Umlaute ausschreiben (ae/oe/ue)"
                )
        return self

    @property
    def folded_term(self) -> str:
        """Der Suchbegriff in gefalteter Schreibweise."""
        if self.term is None:  # pragma: no cover - durch Validator ausgeschlossen
            raise ValueError(f"{self.id}: kein Begriff gesetzt")
        return fold(self.term).folded


class LiabilitySensitivity(BaseModel):
    """Fehlerasymmetrie des Haftungs-Gates.

    Beim Haftungsfilter ist der **verpasste Ausschluss** der teure Fehler: ein
    durchgelassener Betrieb aus einem K.o.-Feld erzeugt ein Produkt, das man
    nicht bauen darf. Deshalb ist das Profil ``aggressive``: Zweifel fuehren zu
    REJECT oder mindestens ``liability_review_needed``, nie zu einem stillen
    PASS.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: Literal["aggressive", "conservative"] = "aggressive"
    below_threshold_outcome: GateOutcome = Field(
        default=GateOutcome.REVIEW,
        description=(
            "Urteil, wenn schwache Begriffe die Schwelle nicht erreichen. "
            "Aggressiv: REVIEW statt folgenlos."
        ),
    )
    thin_evidence_outcome: GateOutcome = Field(
        default=GateOutcome.REVIEW,
        description=(
            "Urteil, wenn weder NOGA-Code noch Text vorliegt - dann wurde nichts "
            "geprueft, nicht 'nichts gefunden'."
        ),
    )
    vetoed_hit_outcome: GateOutcome = Field(
        default=GateOutcome.REVIEW,
        description=(
            "Urteil, wenn ein entscheidender Treffer nur durch ein Kontext-Veto "
            "wegfiel. Aggressiv: der Fall geht in die Pruefschlange, nicht durch."
        ),
    )

    @model_validator(mode="after")
    def _profile_matches_knobs(self) -> Self:
        if self.profile == "aggressive" and GateOutcome.PASS in {
            self.below_threshold_outcome,
            self.thin_evidence_outcome,
            self.vetoed_hit_outcome,
        }:
            raise ValueError(
                "Profil 'aggressive' vertraegt kein PASS als Zweifelsurteil - "
                "entweder Profil auf 'conservative' setzen oder das Urteil anheben"
            )
        return self


class ErpSensitivity(BaseModel):
    """Fehlerasymmetrie des ERP-Gates.

    Beim ERP-Filter ist der **Fehlalarm** der teure Fehler: ein faelschlich
    ausgeschlossener Betrieb faellt nie auf, weil er nie mehr auftaucht. Deshalb
    ist das Profil ``conservative``: ein Verdacht ohne harten Nachweis laesst den
    Betrieb drin und setzt nur ``erp_review_needed``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: Literal["aggressive", "conservative"] = "conservative"
    suspected_outcome: GateOutcome = Field(
        default=GateOutcome.PASS,
        description=(
            "Urteil bei unqualifizierter Anbieternennung (Verdacht ohne harten "
            "Nachweis). Konservativ: PASS mit Flag."
        ),
    )

    @model_validator(mode="after")
    def _profile_matches_knobs(self) -> Self:
        if self.profile == "conservative" and self.suspected_outcome is GateOutcome.REJECT:
            raise ValueError(
                "Profil 'conservative' vertraegt kein REJECT auf blossen Verdacht"
            )
        return self


class LiabilityDomain(BaseModel):
    """Ein Haftungsfeld aus dem K.o.-Katalog."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Identifier
    label: str
    rationale: str
    noga_reject_prefixes: tuple[str, ...] = ()
    noga_review_prefixes: tuple[str, ...] = ()
    terms: tuple[TermPattern, ...] = ()
    veto_terms: tuple[str, ...] = Field(
        default=(),
        description=(
            "Treffer werden verworfen, wenn einer dieser Begriffe im Umfeld steht - "
            "typisch fuer Zulieferer ('Software fuer Arztpraxen')."
        ),
    )
    weak_hits_for_review: int = Field(
        default=2,
        ge=1,
        description="Anzahl unterschiedlicher nicht-entscheidender Begriffe fuer ein REVIEW.",
    )

    @model_validator(mode="after")
    def _normalize(self) -> Self:
        reject = tuple(normalize_noga(code) for code in self.noga_reject_prefixes)
        review = tuple(normalize_noga(code) for code in self.noga_review_prefixes)
        overlap = set(reject) & set(review)
        if overlap:
            raise ValueError(f"{self.id}: NOGA-Praefix doppelt gefuehrt: {sorted(overlap)}")
        object.__setattr__(self, "noga_reject_prefixes", reject)
        object.__setattr__(self, "noga_review_prefixes", review)
        object.__setattr__(
            self, "veto_terms", tuple(fold(term).folded for term in self.veto_terms)
        )
        return self


class LiabilityRules(BaseModel):
    """Vollstaendiger Haftungs-K.o.-Katalog."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    context_veto_window: int = Field(default=120, ge=0, le=2000)
    sensitivity: LiabilitySensitivity = LiabilitySensitivity()
    domains: tuple[LiabilityDomain, ...]

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [domain.id for domain in self.domains]
        if len(ids) != len(set(ids)):
            raise ValueError("doppelte Domain-ID im Haftungskatalog")
        return self


class ErpVendor(BaseModel):
    """Ein ERP-Anbieter mit seinen Erkennungsmustern."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Identifier
    label: str
    patterns: tuple[TermPattern, ...]
    domains: tuple[str, ...] = Field(
        default=(), description="Hostnamen von Kundenportalen/Cloud-Diensten des Anbieters."
    )
    ambiguous: bool = Field(
        default=False,
        description=(
            "Anbietername ist im Deutschen mehrdeutig (z. B. 'Sage', 'Klara'). "
            "Solche Anbieter duerfen nur qualifizierte Muster fuehren."
        ),
    )

    @model_validator(mode="after")
    def _ambiguous_needs_qualified_patterns(self) -> Self:
        if not self.patterns and not self.domains:
            raise ValueError(f"{self.id}: weder Muster noch Domains hinterlegt")
        if self.ambiguous:
            bare = [p.id for p in self.patterns if p.term is not None and " " not in p.term]
            if bare:
                raise ValueError(
                    f"{self.id}: mehrdeutiger Anbieter braucht qualifizierte Muster, "
                    f"nicht den blossen Namen: {bare}"
                )
        object.__setattr__(self, "domains", tuple(d.strip().lower() for d in self.domains))
        return self


class ErpRules(BaseModel):
    """ERP-Negativfilter: Anbietermuster plus positive Schmerzsignale."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    sensitivity: ErpSensitivity = ErpSensitivity()
    vendors: tuple[ErpVendor, ...]
    pain_signals: tuple[TermPattern, ...] = ()
    free_mail_domains: tuple[str, ...] = ()
    pain_signals_for_absence: int = Field(
        default=2,
        ge=1,
        description="Anzahl unterschiedlicher Schmerzsignale fuer den Status 'absence_indicated'.",
    )

    @model_validator(mode="after")
    def _normalize(self) -> Self:
        ids = [vendor.id for vendor in self.vendors]
        if len(ids) != len(set(ids)):
            raise ValueError("doppelte Anbieter-ID im ERP-Katalog")
        object.__setattr__(
            self, "free_mail_domains", tuple(d.strip().lower() for d in self.free_mail_domains)
        )
        return self


def load_rules_file(path: Path) -> dict[str, object]:
    """Liest eine YAML-Regeldatei und garantiert ein Mapping auf oberster Ebene."""
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: YAML-Mapping erwartet, gefunden {type(data).__name__}")
    return data


@lru_cache(maxsize=8)
def load_liability_rules(path: Path | None = None) -> LiabilityRules:
    """Laedt den Haftungskatalog (Standard: ``config/liability_rules.yaml``)."""
    target = path or CONFIG_DIR / "liability_rules.yaml"
    return LiabilityRules.model_validate(load_rules_file(target))


@lru_cache(maxsize=8)
def load_erp_rules(path: Path | None = None) -> ErpRules:
    """Laedt den ERP-Negativfilter (Standard: ``config/erp_rules.yaml``)."""
    target = path or CONFIG_DIR / "erp_rules.yaml"
    return ErpRules.model_validate(load_rules_file(target))
