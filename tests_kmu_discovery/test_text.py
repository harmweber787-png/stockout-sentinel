"""Tests der Textfaltung und Begriffssuche."""

from __future__ import annotations

import pytest

from kmu_discovery.gates.text import MatchMode, find_regex, find_term, fold, snippet


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ärzte", "aerzte"),
        ("Aerzte", "aerzte"),
        ("Grundstück", "grundstueck"),
        ("Grundstueck", "grundstueck"),
        ("Straße", "strasse"),
        ("Café", "cafe"),
        ("MS-Office", "ms-office"),
        ("Bau\u2013Plus", "bau-plus"),
        ("mehrere    Leerzeichen", "mehrere leerzeichen"),
    ],
)
def test_faltung_vereinheitlicht_schweizer_schreibweisen(raw: str, expected: str) -> None:
    assert fold(raw).folded == expected


def test_faltung_haelt_offsets_trotz_laengenaenderung() -> None:
    folded = fold("Die Zahnärztin kommt.")
    hits = find_term(folded, "zahnaerztin")
    assert [hit.matched_text for hit in hits] == ["Zahnärztin"]


def test_faltung_ignoriert_weiche_trennzeichen() -> None:
    assert fold("Zahn­ärztin").folded == "zahnaerztin"


def test_stem_trifft_deutsche_komposita() -> None:
    hits = find_term(fold("Wir betreiben eine Zahnarztpraxis."), "zahnarzt")
    assert hits[0].matched_text == "Zahnarztpraxis"


def test_word_trifft_kein_kompositum() -> None:
    text = fold("Die Rechtsform ist eine GmbH.")
    assert find_term(text, "recht", MatchMode.WORD) == []
    assert find_term(text, "rechtsform", MatchMode.WORD) != []


def test_stem_verankert_am_wortanfang() -> None:
    """Wortstaemme verankern am Wortanfang.

    'therapie' darf nicht in 'Physiotherapie' anschlagen - sonst zaehlten
    beliebige Teilwoerter mit und jede Wortliste waere unbrauchbar.
    """
    assert find_term(fold("Wir bieten Physiotherapie an."), "therapie") == []


def test_free_modus_trifft_domains() -> None:
    hits = find_term(fold("Login unter portal.abacus.ch"), "abacus.ch", MatchMode.FREE)
    assert hits[0].matched_text == "abacus.ch"


def test_regex_laeuft_gegen_den_gefalteten_text() -> None:
    hits = find_regex(fold("Gute MS-Office-Kenntnisse."), "(ms|microsoft)[- ]office")
    assert hits[0].matched_text == "MS-Office"


def test_zitat_wird_um_die_fundstelle_geschnitten() -> None:
    text = "A" * 200 + " Zahnarztpraxis " + "B" * 200
    hit = find_term(fold(text), "zahnarzt")[0]
    assert "Zahnarztpraxis" in hit.quote
    assert hit.quote.startswith("...") and hit.quote.endswith("...")


def test_snippet_ohne_kuerzung_hat_keine_auslassungspunkte() -> None:
    assert snippet("Kurzer Text", 0, 6) == "Kurzer Text"


def test_leerer_text_liefert_keine_treffer() -> None:
    assert find_term(fold("   "), "abacus") == []
