"""Phase 1b - Payload-Builder: I01-I12 und Haertungen H01-H05."""

from __future__ import annotations

import base64

from inbox_copilot.config import Settings
from inbox_copilot.gmail_ingest import (
    build_thread_context,
    build_triage_payload,
    clean_body,
    collect_attachments,
    decode_base64url_bytes,
    decode_part_text,
    extract_body,
    extract_pdf_text_safe,
    should_extract,
    strip_quotes,
    strip_signature,
)
from inbox_copilot.schemas import (
    AttachmentMeta,
    CompanyContext,
    Sender,
    TriagePayloadV1,
)
from tests.conftest import (
    BEAT_MEIER_GMAIL_ID,
    BEAT_MEIER_THREAD_ID,
    load_fixture,
    make_pdf,
)
from tests.test_pipeline import MAENGEL_BODY

# ---------------------------------------------------------------------------
# I01 / I02 - Body
# ---------------------------------------------------------------------------


def test_i01_body_aus_text_plain() -> None:
    body = extract_body(load_fixture("beat_meier_plain.json"))
    assert body == MAENGEL_BODY


def test_i02_body_aus_html_ohne_skripte() -> None:
    body = extract_body(load_fixture("beat_meier_html_outlook.json"))
    assert body.startswith("Guten Tag Herr Weber")
    assert "alert(" not in body
    assert "color:red" not in body
    assert "Risse im Verputz" in body
    # Block-Tags erzeugen Zeilenumbrueche.
    assert "Freundliche Gruesse\nBeat Meier" in body


# ---------------------------------------------------------------------------
# I03 / I04 - Zitate
# ---------------------------------------------------------------------------


def test_i03_strip_quotes_alle_muster() -> None:
    kopf = "Danke fuer die Rueckmeldung.\n\n"
    marker = [
        "> alter Text",
        "Am 11.09.2026 um 09:12 schrieb Hans Weber <info@example.ch>:",
        "On Fri, Sep 11, 2026 at 9:12 AM Hans Weber wrote:",
        "Le ven. 11 sept. 2026 à 09:12, Hans Weber a écrit :",
        "Il giorno ven 11 set 2026 alle ore 09:12 Hans Weber ha scritto:",
        "-----Ursprüngliche Nachricht-----",
        "____________________",
        "Von: Hans Weber\nAn: Beat\nGesendet: Freitag, 11. September 2026",
    ]
    for eintrag in marker:
        text = kopf + eintrag + "\nalte Nachricht"
        assert strip_quotes(text) == "Danke fuer die Rueckmeldung.", eintrag


def test_i04_fliesstext_on_und_am_bleiben() -> None:
    text = "Am Montag schauen wir vorbei.\nWe are on site tomorrow.\nDanke."
    assert strip_quotes(text) == text
    text2 = "Von: unserer Seite aus passt es.\nBis bald."
    assert strip_quotes(text2) == text2


# ---------------------------------------------------------------------------
# I05 - I07 - Signatur
# ---------------------------------------------------------------------------


def test_i05_delimiter_signatur() -> None:
    text = "Guten Tag\n\nDanke.\n-- \nBeat Meier\nTel. 044 123 45 67"
    assert strip_signature(text) == "Guten Tag\n\nDanke."


def test_i06_heuristik_behaelt_grussformel() -> None:
    text = (
        "Guten Tag Herr Weber\n\n"
        "Bitte senden Sie uns die Offerte bis Ende Woche.\n\n"
        "Die Begehung ist fuer naechste Woche geplant.\n\n"
        "Wir freuen uns auf Ihre Rueckmeldung.\n\n"
        "Freundliche Grüsse\n"
        "Beat Meier\n"
        "Bauleitung Meier AG\n"
        "Bahnhofstrasse 1, 8001 Zürich\n"
        "Tel. 044 123 45 67\n"
        "www.bauleitung-meier.ch"
    )
    ergebnis = strip_signature(text)
    assert ergebnis.endswith("Freundliche Grüsse")
    assert "Tel." not in ergebnis
    assert "www." not in ergebnis
    assert "8001" not in ergebnis
    assert "Bitte senden Sie uns die Offerte" in ergebnis


def test_i07_heuristik_greift_nicht_ohne_signale() -> None:
    text = "Guten Tag\n\nKoennen Sie morgen vorbeikommen?\n\nMerci\nFritz"
    assert strip_signature(text) == text


# ---------------------------------------------------------------------------
# I08 - I10 - Anhaenge
# ---------------------------------------------------------------------------


def test_i08_inline_bilder_ausgeschlossen(settings: Settings) -> None:
    teile = collect_attachments(
        load_fixture("message_with_pdf_part.json"),
        inline_image_max_bytes=settings.inline_image_max_bytes,
    )
    namen = [teil.meta.filename for teil in teile]
    assert namen == ["maengelliste_protokoll.pdf"]
    assert teile[0].meta.size_kb == 180
    assert teile[0].attachment_id == "ANGjdJ_att_protokoll"


def test_i09_should_extract_bei_referenz_im_body(settings: Settings) -> None:
    anhang = AttachmentMeta(
        filename="scan_0815.pdf", mime_type="application/pdf", size_kb=120
    )
    treffer = should_extract(
        "Das Protokoll finden Sie im Anhang.", [anhang], settings=settings
    )
    assert treffer == anhang


def test_i10_should_extract_none_ohne_referenz(settings: Settings) -> None:
    anhang = AttachmentMeta(
        filename="foto_baustelle.pdf", mime_type="application/pdf", size_kb=120
    )
    assert should_extract("Bitte um Rueckruf.", [anhang], settings=settings) is None
    # Verdaechtiger Dateiname reicht, ein unpassender MIME-Typ aber nicht.
    bild = AttachmentMeta(filename="rechnung.jpg", mime_type="image/jpeg", size_kb=50)
    assert should_extract("Bitte um Rueckruf.", [bild], settings=settings) is None


# ---------------------------------------------------------------------------
# I11 / I12 - Thread-Kontext und Triage-Payload
# ---------------------------------------------------------------------------


def test_i11_thread_kontext_letzte_drei_ohne_draft() -> None:
    thread = load_fixture("thread_four_messages.json")
    kontext = build_thread_context(thread, exclude_message_id=None, depth=3)
    assert len(kontext) == 3
    assert [n.body for n in kontext][:2] == [
        "Anbei unsere Offerte.",
        "Danke, wir pruefen die Offerte.",
    ]
    assert kontext[-1].body == MAENGEL_BODY
    assert all("Entwurf ohne Versand" not in n.body for n in kontext)
    assert kontext[0].timestamp < kontext[1].timestamp < kontext[2].timestamp
    # Eigene gesendete Nachricht (SENT) bleibt im Kontext.
    assert kontext[0].sender_email == "info@weber-bau.example"


def test_i12_build_triage_payload_entspricht_referenz(settings: Settings) -> None:
    """Referenz: ``beat_meier_payload`` aus tests/test_pipeline.py (Teil 1)."""
    referenz_kontext = CompanyContext(
        company_name="Weber Bau GmbH", owner_name="Hans Weber", location="Dietikon"
    )
    payload = build_triage_payload(
        load_fixture("beat_meier_plain.json"), referenz_kontext, settings=settings
    )
    assert isinstance(payload, TriagePayloadV1)
    assert payload.message_id == BEAT_MEIER_GMAIL_ID
    assert payload.thread_id == BEAT_MEIER_THREAD_ID
    assert payload.subject == "Maengelruege Fassade Bauvorhaben Urdorf"
    assert payload.cleaned_body == MAENGEL_BODY
    assert payload.attachments_meta == [
        AttachmentMeta(
            filename="Maengelliste.pdf", mime_type="application/pdf", size_kb=240
        )
    ]
    assert payload.sender == Sender(
        name="Beat Meier", email="b.meier@bauleitung-meier.ch"
    )
    assert payload.company_context == referenz_kontext
    # internalDate (ms Epoch) -> Europe/Zurich, 13.09.2026 08:15 Lokalzeit.
    assert payload.received_at.tzinfo is not None
    assert payload.received_at.isoformat() == "2026-09-13T08:15:00+02:00"
    assert payload.reply_headers is not None
    assert payload.reply_headers.message_id_rfc == "<sia118-0001@bauleitung-meier.ch>"


# ---------------------------------------------------------------------------
# H01 - H05 - Haertungen
# ---------------------------------------------------------------------------


def test_h01_base64url_ohne_padding() -> None:
    for roh in (b"a", b"ab", b"abc", b"abcd", b"\xff\xfe\x00 Umlaut \xc3\xa4"):
        kodiert = base64.urlsafe_b64encode(roh).decode("ascii").rstrip("=")
        assert len(kodiert) % 4 != 0 or len(roh) % 3 == 0
        assert decode_base64url_bytes(kodiert) == roh


def test_h02_charset_reihenfolge_nie_exception() -> None:
    latin = "Grüezi, die Mängel müssen behoben werden.".encode("iso-8859-1")
    kodiert = base64.urlsafe_b64encode(latin).decode("ascii").rstrip("=")
    assert decode_part_text(kodiert, "iso-8859-1") == (
        "Grüezi, die Mängel müssen behoben werden."
    )
    # Ohne Charset und ungueltigem UTF-8 greift der Latin-1-Fallback.
    assert (
        decode_part_text(kodiert, None) == "Grüezi, die Mängel müssen behoben werden."
    )
    # Unbekanntes Charset -> UTF-8.
    utf8 = "Grüezi".encode()
    assert (
        decode_part_text(base64.urlsafe_b64encode(utf8).decode(), "x-gibt-es-nicht")
        == "Grüezi"
    )
    # Latin-1-Fixture als Ganzes.
    body = clean_body(load_fixture("beat_meier_latin1.json"))
    assert body.startswith("Grüezi Herr Weber")
    assert "Mängel" in body


def test_h03_pdf_ueber_groessenlimit() -> None:
    attrappe = b"%PDF-1.4 " + b"\0" * 32
    assert (
        extract_pdf_text_safe(
            attrappe, head_pages=4, include_last=True, char_cap=6000, max_bytes=16
        )
        is None
    )


def test_h04_sieben_seiten_kopf_und_letzte() -> None:
    pdf = make_pdf([f"Seite {i} Inhalt" for i in range(1, 8)])
    text = extract_pdf_text_safe(
        pdf, head_pages=4, include_last=True, char_cap=6000, max_bytes=15 * 1024 * 1024
    )
    assert text is not None
    assert text.startswith("[AUSZUG: Seiten 1, 2, 3, 4, 7 von 7]\n")
    for seite in (1, 2, 3, 4, 7):
        assert f"[Seite {seite}]\nSeite {seite} Inhalt" in text
    assert "[Seite 5]" not in text
    assert "[Seite 6]" not in text


def test_h05_leerer_textlayer_und_korrupt() -> None:
    leer = make_pdf(["", "", ""])
    assert (
        extract_pdf_text_safe(
            leer, head_pages=4, include_last=True, char_cap=6000, max_bytes=10**7
        )
        is None
    )
    korrupt = b"%PDF-1.7 garbage " + bytes(range(256))
    assert (
        extract_pdf_text_safe(
            korrupt, head_pages=4, include_last=True, char_cap=6000, max_bytes=10**7
        )
        is None
    )
