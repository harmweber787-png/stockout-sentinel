"""Deterministische Textnormalisierung und Begriffssuche fuer die Gates.

Warum eine eigene Faltung statt ``str.lower()``: Schweizer Websites schreiben
Umlaute uneinheitlich ("Aerzte" / "Ärzte", "Grundstueck" / "Grundstück"). Die
Faltung bildet beide Schreibweisen auf dieselbe Form ab und fuehrt zusaetzlich
eine Offset-Tabelle mit, damit das Originalzitat als Beleg exakt
zurueckgewonnen werden kann - auch wenn sich die Zeichenzahl aendert (ä -> ae).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache

__all__ = ["FoldedText", "MatchMode", "TermHit", "find_regex", "find_term", "fold", "snippet"]

_UMLAUT_MAP = {
    "ä": "ae",
    "ö": "oe",
    "ü": "ue",
    "ß": "ss",
    "æ": "ae",
    "œ": "oe",
}
_WORD_CHARS = "0-9a-z"

#: Bindestrich-Varianten, die auf den einfachen Bindestrich abgebildet werden.
_DASHES = frozenset("\u2010\u2011\u2012\u2013\u2014\u2212")


class MatchMode(StrEnum):
    """Wie ein Suchbegriff im Text verankert wird."""

    #: Exaktes Wort: ``arzt`` trifft "Arzt", nicht "Arztpraxis".
    WORD = "word"
    #: Wortstamm am Wortanfang: ``zahnarzt`` trifft auch "Zahnarztpraxis".
    STEM = "stem"
    #: Freie Teilzeichenkette - nur fuer Domains/Token, nie fuer deutsche Woerter.
    FREE = "free"


@dataclass(frozen=True, slots=True)
class FoldedText:
    """Gefalteter Text plus Rueckabbildung auf den Originaltext."""

    original: str
    folded: str
    offsets: tuple[int, ...]

    def to_original_span(self, start: int, end: int) -> tuple[int, int]:
        """Bildet eine Fundstelle im gefalteten Text auf den Originaltext ab."""
        if start >= end:
            raise ValueError("leere Fundstelle")
        return self.offsets[start], self.offsets[end - 1] + 1


@dataclass(frozen=True, slots=True)
class TermHit:
    """Eine Fundstelle mit Originalschreibweise und Belegzitat."""

    term: str
    matched_text: str
    start: int
    end: int
    folded_start: int
    folded_end: int
    quote: str


def fold(text: str) -> FoldedText:
    """Normalisiert Text fuer den Regelabgleich und merkt sich die Offsets.

    Schritte: Unicode-NFKC, Kleinschreibung, Umlaut-/Ligaturaufloesung,
    Entfernen kombinierender Akzente, Vereinheitlichung von Bindestrich-Varianten
    und Zusammenfassen von Weissraum.

    >>> f = fold("Ärzte   und  Zahn­ärzte")
    >>> f.folded
    'aerzte und zahnaerzte'
    >>> f.to_original_span(0, 6)
    (0, 5)
    """
    normalized = unicodedata.normalize("NFKC", text)
    out: list[str] = []
    offsets: list[int] = []
    pending_space = False
    for index, char in enumerate(normalized):
        if char.isspace():
            pending_space = bool(out)
            continue
        if char in {"­", "​", "﻿"}:  # weiche Trenn-/Nullbreitenzeichen
            continue
        if pending_space:
            out.append(" ")
            offsets.append(index)
            pending_space = False
        lowered = char.lower()
        replacement = _UMLAUT_MAP.get(lowered)
        if replacement is None:
            if lowered in _DASHES:
                replacement = "-"
            else:
                decomposed = unicodedata.normalize("NFD", lowered)
                replacement = "".join(c for c in decomposed if not unicodedata.combining(c))
                if not replacement:
                    continue
        for piece in replacement:
            out.append(piece)
            offsets.append(index)
    return FoldedText(original=text, folded="".join(out), offsets=tuple(offsets))


@lru_cache(maxsize=4096)
def _compile_term(term: str, mode: MatchMode) -> re.Pattern[str]:
    escaped = re.escape(term)
    if mode is MatchMode.FREE:
        return re.compile(escaped)
    if mode is MatchMode.WORD:
        return re.compile(rf"(?<![{_WORD_CHARS}]){escaped}(?![{_WORD_CHARS}])")
    return re.compile(rf"(?<![{_WORD_CHARS}]){escaped}[a-z]*")


@lru_cache(maxsize=1024)
def _compile_regex(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


def snippet(text: str, start: int, end: int, width: int = 90) -> str:
    """Schneidet ein Belegzitat um eine Fundstelle herum aus."""
    left = max(0, start - width)
    right = min(len(text), end + width)
    raw = text[left:right]
    cleaned = re.sub(r"\s+", " ", raw).strip()
    prefix = "..." if left > 0 else ""
    suffix = "..." if right < len(text) else ""
    return f"{prefix}{cleaned}{suffix}"


def _hits(
    folded: FoldedText, pattern: re.Pattern[str], term: str, quote_width: int
) -> list[TermHit]:
    results: list[TermHit] = []
    for match in pattern.finditer(folded.folded):
        if match.start() == match.end():
            continue
        start, end = folded.to_original_span(match.start(), match.end())
        results.append(
            TermHit(
                term=term,
                matched_text=folded.original[start:end],
                start=start,
                end=end,
                folded_start=match.start(),
                folded_end=match.end(),
                quote=snippet(folded.original, start, end, quote_width),
            )
        )
    return results


def find_term(
    folded: FoldedText,
    term: str,
    mode: MatchMode = MatchMode.STEM,
    quote_width: int = 90,
) -> list[TermHit]:
    """Sucht einen bereits gefalteten Begriff im gefalteten Text.

    ``term`` muss in gefalteter Schreibweise vorliegen (kleingeschrieben, ohne
    Umlaute) - dafuer sorgt der Konfigurationslader.

    >>> hits = find_term(fold("Wir sind eine Zahnarztpraxis."), "zahnarzt")
    >>> hits[0].matched_text
    'Zahnarztpraxis'
    """
    return _hits(folded, _compile_term(term, mode), term, quote_width)


def find_regex(
    folded: FoldedText, pattern: str, quote_width: int = 90
) -> list[TermHit]:
    """Wie :func:`find_term`, aber mit explizitem Muster gegen den gefalteten Text."""
    return _hits(folded, _compile_regex(pattern), pattern, quote_width)
