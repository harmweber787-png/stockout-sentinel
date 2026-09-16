"""Tests des Quellen-Unterbaus: Token-Bucket, Retry, Cache, Fehler.

Alles ohne Netz: Sender, Uhr und Schlaf sind injiziert.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from kmu_discovery.config.limits import RateLimitConfig, RateLimits, load_rate_limits
from kmu_discovery.models import SourceType
from kmu_discovery.sources.base import (
    HttpResponse,
    HttpTransport,
    RawCache,
    RawRecord,
    SourceBadResponseError,
    SourceRateLimitedError,
    SourceTimeoutError,
    SourceUnavailableError,
    TokenBucket,
    request_key,
)

NOW = datetime(2026, 9, 16, 9, 0, tzinfo=UTC)


class FakeClock:
    """Uhr, die nur durch ``sleep`` voranschreitet."""

    def __init__(self) -> None:
        """Startet bei null."""
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        """Aktuelle Fake-Zeit."""
        return self.t

    def sleep(self, seconds: float) -> None:
        """Merkt sich jede Pause und laesst die Zeit vergehen."""
        self.sleeps.append(seconds)
        self.t += seconds


class ScriptedSender:
    """Antwortet der Reihe nach; Eintraege koennen Exceptions sein."""

    def __init__(self, script: list[HttpResponse | Exception]) -> None:
        """Nimmt das Antwortskript entgegen."""
        self._script = list(script)
        self.calls: list[tuple[str, str, str | None, Mapping[str, str]]] = []

    def __call__(
        self, method: str, url: str, body: str | None, headers: Mapping[str, str], timeout: float
    ) -> HttpResponse:
        """Liefert die naechste Antwort oder wirft die naechste Exception."""
        self.calls.append((method, url, body, headers))
        if not self._script:
            raise AssertionError("mehr Aufrufe als Skript-Eintraege")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok(body: str = "ok", **headers: str) -> HttpResponse:
    return HttpResponse(status=200, body=body, headers=headers)


def status(code: int, **headers: str) -> HttpResponse:
    return HttpResponse(status=code, body=f"status {code}", headers=headers)


def config(**overrides: float | int) -> RateLimitConfig:
    base: dict[str, float | int] = {
        "requests_per_second": 1.0,
        "burst": 1,
        "max_attempts": 3,
        "timeout_seconds": 10.0,
        "backoff_base_seconds": 2.0,
        "backoff_max_seconds": 30.0,
    }
    base.update(overrides)
    return RateLimitConfig.model_validate(base)


def transport(
    sender: ScriptedSender,
    clock: FakeClock,
    cache: RawCache | None = None,
    **overrides: float | int,
) -> HttpTransport:
    return HttpTransport(
        source=SourceType.LINDAS,
        config=config(**overrides),
        sender=sender,
        cache=cache,
        clock=clock.now,
        sleep=clock.sleep,
        now=lambda: NOW,
        rng=lambda: 0.0,
    )


# -- Token-Bucket ---------------------------------------------------------- #


def test_bucket_laesst_burst_ohne_wartezeit_durch() -> None:
    clock = FakeClock()
    bucket = TokenBucket(rate=1.0, burst=3, clock=clock.now, sleep=clock.sleep)
    assert [bucket.acquire() for _ in range(3)] == [0.0, 0.0, 0.0]
    assert bucket.acquire() == 1.0


def test_bucket_haelt_die_dauerrate() -> None:
    clock = FakeClock()
    bucket = TokenBucket(rate=2.0, burst=1, clock=clock.now, sleep=clock.sleep)
    bucket.acquire()
    waits = [bucket.acquire() for _ in range(4)]
    assert all(abs(w - 0.5) < 1e-9 for w in waits)


def test_bucket_fuellt_sich_in_der_pause_wieder() -> None:
    clock = FakeClock()
    bucket = TokenBucket(rate=1.0, burst=2, clock=clock.now, sleep=clock.sleep)
    bucket.acquire()
    bucket.acquire()
    clock.t += 10.0
    assert bucket.acquire() == 0.0
    assert bucket.acquire() == 0.0
    assert bucket.acquire() == 1.0


def test_bucket_lehnt_unsinnige_parameter_ab() -> None:
    with pytest.raises(ValueError):
        TokenBucket(rate=0.0, burst=1)
    with pytest.raises(ValueError):
        TokenBucket(rate=1.0, burst=0)


def test_uid_bfs_limit_wird_hart_eingehalten() -> None:
    """20 Anfragen pro Minute: 21 Anfragen brauchen mindestens 60 Sekunden."""
    clock = FakeClock()
    limits = load_rate_limits().for_source(SourceType.UID_BFS)
    bucket = TokenBucket(
        rate=limits.requests_per_second, burst=limits.burst, clock=clock.now, sleep=clock.sleep
    )
    for _ in range(21):
        bucket.acquire()
    assert sum(clock.sleeps) >= 60.0


# -- Transport: Erfolg und Retry ------------------------------------------ #


def test_erfolgreiche_anfrage_liefert_datensatz_mit_herkunft() -> None:
    sender = ScriptedSender([ok("hallo", **{"Content-Type": "text/plain"})])
    record = transport(sender, FakeClock()).get("https://lindas.admin.ch/query")
    assert record.body == "hallo"
    assert record.source is SourceType.LINDAS
    assert record.retrieved_at == NOW
    assert record.content_type == "text/plain"
    assert record.request_key == request_key("GET", "https://lindas.admin.ch/query", None)


def test_user_agent_identifiziert_uns() -> None:
    sender = ScriptedSender([ok()])
    transport(sender, FakeClock()).get("https://x.ch/")
    assert "kmu-discovery" in sender.calls[0][3]["User-Agent"]


def test_eigene_header_ergaenzen_den_user_agent() -> None:
    sender = ScriptedSender([ok()])
    transport(sender, FakeClock()).post("https://x.ch/q", "query=1", {"Accept": "text/csv"})
    headers = sender.calls[0][3]
    assert headers["Accept"] == "text/csv"
    assert "User-Agent" in headers


def test_5xx_wird_mit_backoff_wiederholt() -> None:
    clock = FakeClock()
    sender = ScriptedSender([status(503), status(502), ok("endlich")])
    record = transport(sender, clock).get("https://x.ch/")
    assert record.body == "endlich"
    assert len(sender.calls) == 3
    # Backoff 2s, dann 4s - zusaetzlich zur Bucket-Wartezeit von je 1s.
    assert 2.0 in clock.sleeps
    assert 4.0 in clock.sleeps


def test_retry_after_hat_vorrang_vor_backoff() -> None:
    clock = FakeClock()
    sender = ScriptedSender([status(429, **{"Retry-After": "7"}), ok()])
    transport(sender, clock).get("https://x.ch/")
    assert 7.0 in clock.sleeps
    assert 2.0 not in clock.sleeps


def test_retry_after_wird_gedeckelt() -> None:
    clock = FakeClock()
    sender = ScriptedSender([status(429, **{"Retry-After": "9999"}), ok()])
    transport(sender, clock, backoff_max_seconds=30.0).get("https://x.ch/")
    assert max(clock.sleeps) <= 30.0


def test_dauerhaftes_429_wird_zu_rate_limited() -> None:
    sender = ScriptedSender([status(429), status(429), status(429)])
    with pytest.raises(SourceRateLimitedError):
        transport(sender, FakeClock()).get("https://x.ch/")
    assert len(sender.calls) == 3


def test_dauerhaftes_5xx_wird_zu_unavailable() -> None:
    sender = ScriptedSender([status(503), status(503), status(503)])
    with pytest.raises(SourceUnavailableError) as info:
        transport(sender, FakeClock()).get("https://x.ch/")
    assert info.value.source is SourceType.LINDAS
    assert info.value.url == "https://x.ch/"


def test_timeouts_werden_wiederholt_und_typisiert() -> None:
    sender = ScriptedSender([TimeoutError("langsam"), TimeoutError("langsam"), TimeoutError("x")])
    with pytest.raises(SourceTimeoutError):
        transport(sender, FakeClock()).get("https://x.ch/")


def test_verbindungsfehler_werden_wiederholt() -> None:
    sender = ScriptedSender([ConnectionError("reset"), ok("da")])
    assert transport(sender, FakeClock()).get("https://x.ch/").body == "da"


def test_4xx_wird_nicht_wiederholt() -> None:
    sender = ScriptedSender([status(404)])
    with pytest.raises(SourceBadResponseError) as info:
        transport(sender, FakeClock()).get("https://x.ch/fehlt")
    assert info.value.status == 404
    assert len(sender.calls) == 1


def test_nur_http_urls() -> None:
    sender = ScriptedSender([])
    with pytest.raises(SourceBadResponseError):
        transport(sender, FakeClock()).get("ftp://x.ch/")


def test_max_attempts_eins_heisst_kein_retry() -> None:
    sender = ScriptedSender([status(503)])
    with pytest.raises(SourceUnavailableError):
        transport(sender, FakeClock(), max_attempts=1).get("https://x.ch/")
    assert len(sender.calls) == 1


def test_zaehler_fuer_kosten_und_kalibrierung() -> None:
    sender = ScriptedSender([status(503), ok()])
    t = transport(sender, FakeClock())
    t.get("https://x.ch/")
    assert t.calls_sent == 2
    assert t.cache_hits == 0


# -- Cache und Idempotenz ------------------------------------------------- #


def test_zweiter_lauf_ruft_nichts_doppelt_ab(tmp_path: Path) -> None:
    cache = RawCache(tmp_path)
    sender = ScriptedSender([ok("einmal")])
    t = transport(sender, FakeClock(), cache)
    first = t.get("https://x.ch/a")
    second = t.get("https://x.ch/a")
    assert first == second
    assert len(sender.calls) == 1
    assert t.cache_hits == 1


def test_cache_unterscheidet_methode_url_und_body(tmp_path: Path) -> None:
    cache = RawCache(tmp_path)
    sender = ScriptedSender([ok("a"), ok("b"), ok("c")])
    t = transport(sender, FakeClock(), cache, burst=3)
    assert t.get("https://x.ch/a").body == "a"
    assert t.post("https://x.ch/a", "q=1").body == "b"
    assert t.post("https://x.ch/a", "q=2").body == "c"
    assert len(sender.calls) == 3


def test_cache_respektiert_hoechstalter(tmp_path: Path) -> None:
    cache = RawCache(tmp_path)
    old = RawRecord(
        source=SourceType.LINDAS,
        method="GET",
        url="https://x.ch/a",
        request_key=request_key("GET", "https://x.ch/a", None),
        retrieved_at=datetime.now(UTC) - timedelta(days=30),
        status=200,
        body="alt",
    )
    cache.put(old)
    sender = ScriptedSender([ok("frisch")])
    t = transport(sender, FakeClock(), cache)
    assert t.get("https://x.ch/a", max_age=timedelta(days=1)).body == "frisch"
    assert t.get("https://x.ch/a").body == "frisch"  # jetzt ueberschrieben


def test_cache_trennt_quellen(tmp_path: Path) -> None:
    cache = RawCache(tmp_path)
    key = request_key("GET", "https://x.ch/a", None)
    record = RawRecord(
        source=SourceType.ZEFIX,
        method="GET",
        url="https://x.ch/a",
        request_key=key,
        retrieved_at=NOW,
        status=200,
        body="zefix",
    )
    cache.put(record)
    assert cache.get(SourceType.ZEFIX, key) == record
    assert cache.get(SourceType.LINDAS, key) is None


def test_cache_datei_traegt_zeitstempel_und_url(tmp_path: Path) -> None:
    cache = RawCache(tmp_path)
    sender = ScriptedSender([ok("x")])
    transport(sender, FakeClock(), cache).get("https://x.ch/a")
    files = list((tmp_path / "lindas").glob("*.json"))
    assert len(files) == 1
    stored = RawRecord.model_validate_json(files[0].read_text(encoding="utf-8"))
    assert stored.url == "https://x.ch/a"
    assert stored.retrieved_at == NOW


# -- Konfiguration --------------------------------------------------------- #


def test_limits_fuer_alle_quellen_ladbar() -> None:
    limits = load_rate_limits()
    for source in SourceType:
        cfg = limits.for_source(source)
        assert cfg.requests_per_second > 0
        assert cfg.max_attempts >= 1


def test_override_ergaenzt_nur_gesetzte_felder() -> None:
    limits = load_rate_limits()
    lindas = limits.for_source(SourceType.LINDAS)
    assert lindas.burst == 2
    assert lindas.timeout_seconds == 120.0
    assert lindas.max_attempts == limits.defaults.max_attempts


def test_unbekannte_quelle_in_der_konfiguration_wird_abgelehnt() -> None:
    with pytest.raises(ValidationError, match="unbekannte Quellen"):
        RateLimits.model_validate(
            {
                "version": "1",
                "defaults": config().model_dump(),
                "sources": {"moneyhouse": {"requests_per_second": 1.0}},
            }
        )


def test_backoff_grenzen_muessen_geordnet_sein() -> None:
    with pytest.raises(ValidationError):
        config(backoff_base_seconds=10.0, backoff_max_seconds=5.0)


def test_website_limit_ist_konservativ() -> None:
    """Fremde Infrastruktur: hoechstens eine Anfrage alle zwei Sekunden, kaum Retries."""
    cfg = load_rate_limits().for_source(SourceType.COMPANY_WEBSITE)
    assert cfg.requests_per_second <= 0.5
    assert cfg.max_attempts <= 2
