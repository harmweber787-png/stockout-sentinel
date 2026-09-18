"""Quellenunabhaengiger Unterbau fuer alle Datenquellen-Clients.

Jede Quelle bekommt genau einen Client; kein Client kennt eine andere Quelle.
Was alle brauchen, steht hier: Token-Bucket-Rate-Limit, exponentielles Backoff
mit Jitter und ``Retry-After``, Timeouts, typisierte Fehler und ein
Rohdaten-Cache mit Zeitstempel und Quell-URL, damit wiederholte Laeufe nichts
doppelt abrufen (Idempotenz).

Der Transport haengt an einem austauschbaren ``Sender`` - im Test ein Fake, im
Betrieb ``urllib``. So sind Rate-Limit, Retry und Cache ohne Netz pruefbar.
"""

from __future__ import annotations

import hashlib
import random
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from kmu_discovery.config.limits import RateLimitConfig
from kmu_discovery.models import AwareDatetime, SourceType

__all__ = [
    "DEFAULT_USER_AGENT",
    "HttpResponse",
    "HttpTransport",
    "RawCache",
    "RawRecord",
    "Sender",
    "SourceBadResponseError",
    "SourceError",
    "SourceRateLimitedError",
    "SourceTimeoutError",
    "SourceUnavailableError",
    "TokenBucket",
    "request_key",
    "urllib_sender",
]

DEFAULT_USER_AGENT = (
    "kmu-discovery/0.1 (Problem-Discovery-Engine; robots.txt-konform; "
    "Kontakt siehe Repository)"
)

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


# -- Fehler --------------------------------------------------------------- #


class SourceError(Exception):
    """Basisfehler eines Quellen-Clients. Traegt immer die Quelle und die URL."""

    def __init__(self, source: SourceType, url: str, message: str) -> None:
        """Merkt sich Quelle und URL fuer das Fehlerprotokoll je Betrieb."""
        super().__init__(f"[{source}] {url}: {message}")
        self.source = source
        self.url = url
        self.message = message


class SourceUnavailableError(SourceError):
    """Quelle nicht erreichbar oder nach allen Versuchen weiterhin 5xx."""


class SourceTimeoutError(SourceUnavailableError):
    """Zeitueberschreitung bei allen Versuchen."""


class SourceRateLimitedError(SourceUnavailableError):
    """Quelle hat auch nach Backoff mit 429 geantwortet."""


class SourceBadResponseError(SourceError):
    """Endgueltiger Fehler (4xx ausser 429) oder unlesbare Antwort."""

    def __init__(self, source: SourceType, url: str, status: int, message: str) -> None:
        """Merkt sich zusaetzlich den HTTP-Status."""
        super().__init__(source, url, f"HTTP {status}: {message}")
        self.status = status


# -- Token-Bucket ---------------------------------------------------------- #


class TokenBucket:
    """Klassischer Token-Bucket: ``rate`` Token pro Sekunde, bis ``burst`` Vorrat.

    ``clock`` und ``sleep`` sind injizierbar, damit Tests ohne Wartezeit laufen.

    >>> bucket = TokenBucket(rate=2.0, burst=1, clock=lambda: 0.0, sleep=lambda s: None)
    >>> bucket.acquire()
    0.0
    >>> round(bucket.acquire(), 2)
    0.5
    """

    def __init__(
        self,
        rate: float,
        burst: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Startet mit vollem Vorrat."""
        if rate <= 0 or burst < 1:
            raise ValueError("rate > 0 und burst >= 1 erwartet")
        self._rate = rate
        self._burst = float(burst)
        self._tokens = float(burst)
        self._clock = clock
        self._sleep = sleep
        self._last = clock()

    def acquire(self) -> float:
        """Nimmt ein Token; wartet bei Bedarf. Liefert die gewartete Zeit."""
        self._refill()
        waited = 0.0
        if self._tokens < 1.0:
            waited = (1.0 - self._tokens) / self._rate
            self._sleep(waited)
            self._last = self._last + waited
            self._tokens = 1.0
        self._tokens -= 1.0
        return waited

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._last)
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        self._last = now


# -- Rohdaten ------------------------------------------------------------- #


def request_key(method: str, url: str, body: str | None) -> str:
    """Stabiler Schluessel einer Anfrage: Methode, URL, Body.

    >>> request_key("get", "https://x.ch/a", None) == request_key("GET", "https://x.ch/a", None)
    True
    >>> request_key("GET", "https://x.ch/a", None) != request_key("GET", "https://x.ch/a", "q=1")
    True
    """
    digest = hashlib.sha256()
    digest.update(method.upper().encode())
    digest.update(b"\n")
    digest.update(url.encode())
    digest.update(b"\n")
    digest.update((body or "").encode())
    return digest.hexdigest()


class RawRecord(BaseModel):
    """Eine abgerufene Antwort samt Herkunft und Zeitstempel - die Beleg-Basis."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: SourceType
    method: Literal["GET", "POST"]
    url: str
    request_key: str
    retrieved_at: AwareDatetime
    status: int = Field(ge=100, le=599)
    content_type: str | None = None
    body: str

    def age(self, now: datetime | None = None) -> timedelta:
        """Alter des Datensatzes."""
        return (now or datetime.now(UTC)) - self.retrieved_at


class RawCache:
    """Dateibasierter Cache fuer Rohantworten, ein JSON je Anfrage.

    Der Cache ist bewusst dumm: keine Invalidierung ausser ``max_age``. Wer
    frische Daten will, gibt ein Hoechstalter mit; wer den Cache umgehen will,
    uebergibt keinen.
    """

    def __init__(self, directory: Path) -> None:
        """Legt das Verzeichnis bei Bedarf an."""
        self._directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def _path(self, source: SourceType, key: str) -> Path:
        return self._directory / source.value / f"{key}.json"

    def get(
        self,
        source: SourceType,
        key: str,
        max_age: timedelta | None = None,
        now: datetime | None = None,
    ) -> RawRecord | None:
        """Liefert den Datensatz, wenn vorhanden und nicht zu alt.

        ``now`` muss dieselbe Uhr sein, die ``retrieved_at`` gestempelt hat -
        sonst vergleicht die Frischepruefung zwei verschiedene Zeitachsen.
        """
        path = self._path(source, key)
        if not path.exists():
            return None
        record = RawRecord.model_validate_json(path.read_text(encoding="utf-8"))
        if max_age is not None and record.age(now) > max_age:
            return None
        return record

    def put(self, record: RawRecord) -> None:
        """Schreibt den Datensatz atomar (erst Temp-Datei, dann Umbenennen)."""
        path = self._path(record.source, record.request_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(record.model_dump_json(), encoding="utf-8")
        temp.replace(path)


# -- Transport ------------------------------------------------------------ #


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """Was ein Sender zurueckgibt - unabhaengig von der HTTP-Bibliothek."""

    status: int
    body: str
    headers: Mapping[str, str]

    @property
    def retry_after(self) -> float | None:
        """``Retry-After`` in Sekunden, falls numerisch angegeben."""
        value = self.headers.get("Retry-After") or self.headers.get("retry-after")
        if value is None:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            return None


#: Fuehrt eine HTTP-Anfrage aus. Wirft ``TimeoutError`` bei Zeitueberschreitung
#: und ``ConnectionError`` bei Netzproblemen; HTTP-Fehler kommen als Antwort.
Sender = Callable[[str, str, str | None, Mapping[str, str], float], HttpResponse]


def urllib_sender(ca_bundle: Path | None = None) -> Sender:
    """Produktiver Sender auf ``urllib``-Basis, Proxy-Variablen werden beachtet."""
    context = ssl.create_default_context(cafile=str(ca_bundle)) if ca_bundle else None

    def send(
        method: str, url: str, body: str | None, headers: Mapping[str, str], timeout: float
    ) -> HttpResponse:
        request = urllib.request.Request(
            url, data=body.encode("utf-8") if body is not None else None, method=method
        )
        for name, value in headers.items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(
                request, timeout=timeout, context=context
            ) as response:
                return HttpResponse(
                    status=response.status,
                    body=response.read().decode("utf-8", errors="replace"),
                    headers=dict(response.headers.items()),
                )
        except urllib.error.HTTPError as error:
            return HttpResponse(
                status=error.code,
                body=error.read().decode("utf-8", errors="replace"),
                headers=dict(error.headers.items()),
            )
        except TimeoutError:
            raise
        except urllib.error.URLError as error:
            if isinstance(error.reason, TimeoutError):
                raise TimeoutError(str(error.reason)) from error
            raise ConnectionError(str(error.reason)) from error

    return send


class HttpTransport:
    """Rate-limitierter, wiederholender, cachender Transport fuer eine Quelle.

    Reihenfolge je Anfrage: Cache -> Token-Bucket -> Senden -> Retry-Logik ->
    Cache schreiben. Ein fehlgeschlagener Aufruf wirft einen typisierten
    ``SourceError``; der aufrufende Client entscheidet, ob der Betrieb
    uebersprungen wird - der Batch laeuft weiter.
    """

    def __init__(
        self,
        source: SourceType,
        config: RateLimitConfig,
        sender: Sender,
        cache: RawCache | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        rng: Callable[[], float] = random.random,
    ) -> None:
        """Baut Bucket und Zaehler; ``clock``/``sleep``/``now``/``rng`` sind injizierbar."""
        self._source = source
        self._config = config
        self._sender = sender
        self._cache = cache
        self._user_agent = user_agent
        self._sleep = sleep
        self._now = now
        self._rng = rng
        self._bucket = TokenBucket(
            rate=config.requests_per_second, burst=config.burst, clock=clock, sleep=sleep
        )
        self.calls_sent = 0
        self.cache_hits = 0

    @property
    def source(self) -> SourceType:
        """Die Quelle, die dieser Transport bedient."""
        return self._source

    def get(
        self,
        url: str,
        headers: Mapping[str, str] | None = None,
        max_age: timedelta | None = None,
    ) -> RawRecord:
        """GET mit Cache, Rate-Limit und Retry."""
        return self.request("GET", url, None, headers, max_age)

    def post(
        self,
        url: str,
        body: str,
        headers: Mapping[str, str] | None = None,
        max_age: timedelta | None = None,
    ) -> RawRecord:
        """POST mit Cache, Rate-Limit und Retry (z. B. SPARQL-Abfragen)."""
        return self.request("POST", url, body, headers, max_age)

    def request(
        self,
        method: Literal["GET", "POST"],
        url: str,
        body: str | None,
        headers: Mapping[str, str] | None = None,
        max_age: timedelta | None = None,
    ) -> RawRecord:
        """Kern: Cache pruefen, sonst hoeflich senden und Ergebnis ablegen."""
        if not url.startswith(("http://", "https://")):
            raise SourceBadResponseError(self._source, url, 0, "keine http(s)-URL")
        key = request_key(method, url, body)
        if self._cache is not None:
            cached = self._cache.get(self._source, key, max_age, self._now())
            if cached is not None:
                self.cache_hits += 1
                return cached

        merged = {"User-Agent": self._user_agent, **(headers or {})}
        response = self._send_with_retry(method, url, body, merged)
        record = RawRecord(
            source=self._source,
            method=method,
            url=url,
            request_key=key,
            retrieved_at=self._now(),
            status=response.status,
            content_type=response.headers.get("Content-Type")
            or response.headers.get("content-type"),
            body=response.body,
        )
        if self._cache is not None:
            self._cache.put(record)
        return record

    # -- intern ---------------------------------------------------------- #

    def _send_with_retry(
        self, method: str, url: str, body: str | None, headers: Mapping[str, str]
    ) -> HttpResponse:
        attempts = self._config.max_attempts
        last_failure: str = "unbekannt"
        last_status: int | None = None
        for attempt in range(1, attempts + 1):
            self._bucket.acquire()
            self.calls_sent += 1
            try:
                response = self._sender(method, url, body, headers, self._config.timeout_seconds)
            except TimeoutError as error:
                last_failure, last_status = f"Timeout ({error})", None
                self._backoff(attempt, attempts, None)
                continue
            except ConnectionError as error:
                last_failure, last_status = f"Verbindung ({error})", None
                self._backoff(attempt, attempts, None)
                continue

            if response.status < 400:
                return response
            if response.status in _RETRYABLE_STATUS:
                last_failure, last_status = response.body[:200], response.status
                self._backoff(attempt, attempts, response.retry_after)
                continue
            raise SourceBadResponseError(self._source, url, response.status, response.body[:200])

        if last_status == 429:
            raise SourceRateLimitedError(self._source, url, f"nach {attempts} Versuchen: 429")
        if last_status is None and last_failure.startswith("Timeout"):
            raise SourceTimeoutError(
                self._source, url, f"nach {attempts} Versuchen: {last_failure}"
            )
        raise SourceUnavailableError(
            self._source, url, f"nach {attempts} Versuchen: {last_status or ''} {last_failure}"
        )

    def _backoff(self, attempt: int, attempts: int, retry_after: float | None) -> None:
        if attempt >= attempts:
            return
        if retry_after is not None:
            delay = min(retry_after, self._config.backoff_max_seconds)
        else:
            base = self._config.backoff_base_seconds * (2 ** (attempt - 1))
            delay = min(base, self._config.backoff_max_seconds)
        delay += self._rng() * 0.5
        self._sleep(delay)
