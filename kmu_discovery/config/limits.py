"""Rate-Limit-Konfiguration je Datenquelle."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kmu_discovery.config.rules import CONFIG_DIR, load_rules_file
from kmu_discovery.models import SourceType

__all__ = ["RateLimitConfig", "RateLimits", "load_rate_limits"]


class RateLimitConfig(BaseModel):
    """Hoeflichkeits- und Wiederholungsparameter einer Quelle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    requests_per_second: float = Field(gt=0.0, le=50.0)
    burst: int = Field(ge=1, le=100)
    max_attempts: int = Field(ge=1, le=10)
    timeout_seconds: float = Field(gt=0.0, le=600.0)
    backoff_base_seconds: float = Field(gt=0.0, le=60.0)
    backoff_max_seconds: float = Field(gt=0.0, le=600.0)

    @model_validator(mode="after")
    def _backoff_ordered(self) -> Self:
        if self.backoff_max_seconds < self.backoff_base_seconds:
            raise ValueError("backoff_max_seconds muss >= backoff_base_seconds sein")
        return self


class _Override(BaseModel):
    """Teilweise Angaben je Quelle; fehlende Felder kommen aus ``defaults``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    requests_per_second: float | None = None
    burst: int | None = None
    max_attempts: int | None = None
    timeout_seconds: float | None = None
    backoff_base_seconds: float | None = None
    backoff_max_seconds: float | None = None


class RateLimits(BaseModel):
    """Vollstaendige Limit-Konfiguration ueber alle Quellen."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    defaults: RateLimitConfig
    sources: dict[str, _Override] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _known_sources_only(self) -> Self:
        known = {source.value for source in SourceType}
        unknown = sorted(set(self.sources) - known)
        if unknown:
            raise ValueError(f"unbekannte Quellen in rate_limits.yaml: {unknown}")
        return self

    def for_source(self, source: SourceType) -> RateLimitConfig:
        """Effektive Konfiguration einer Quelle (Override ueber Defaults).

        >>> load_rate_limits().for_source(SourceType.UID_BFS).requests_per_second
        0.333
        """
        override = self.sources.get(source.value)
        if override is None:
            return self.defaults
        merged = self.defaults.model_dump()
        merged.update({k: v for k, v in override.model_dump().items() if v is not None})
        return RateLimitConfig.model_validate(merged)


@lru_cache(maxsize=8)
def load_rate_limits(path: Path | None = None) -> RateLimits:
    """Laedt die Limit-Konfiguration (Standard: ``config/rate_limits.yaml``)."""
    target = path or CONFIG_DIR / "rate_limits.yaml"
    return RateLimits.model_validate(load_rules_file(target))
