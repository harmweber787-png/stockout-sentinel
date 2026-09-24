"""Regressionsschutz gegen Brueche im installierten Anthropic-SDK.

Die uebrige Suite mockt den LLM-Client durchgehend und bemerkt deshalb
nicht, wenn sich die Signatur von ``messages.create`` aendert oder das
Ausgabeschema Konstrukte enthaelt, die Structured Outputs ablehnt. Die
Tests hier pruefen genau diese Nahtstelle - ohne Netzzugriff.

Anlass: Live-Lauf vom 23.09.2026 gegen anthropic 1.8.0. Dort scheiterte
jede Nachricht an zwei Punkten, die keine Mock-Suite sehen kann:
``TypeError`` wegen ``temperature`` und HTTP 400 wegen ``minimum``/
``maximum`` im Schema.
"""

from __future__ import annotations

import inspect
import os
from typing import Any

import pytest
from pydantic import BaseModel, SecretStr

from inbox_copilot.config import Settings
from inbox_copilot.pipeline import (
    LLMClient,
    _ohne_zahlengrenzen,
    temperatur_parameter,
)
from inbox_copilot.schemas import (
    DrafterResultV1,
    TriageResultV1,
    json_schema_fuer,
)

SCHEMA_MODELLE: list[type[BaseModel]] = [TriageResultV1, DrafterResultV1]
ZAHLENGRENZEN = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"}


def _create_signatur() -> inspect.Signature:
    from anthropic.resources.messages import AsyncMessages

    return inspect.signature(AsyncMessages.create)


class SignaturClient:
    """Faengt den Parameter-Dict ab, statt die API aufzurufen."""

    def __init__(self) -> None:
        self.parameter: dict[str, Any] = {}
        self.messages = self

    async def create(self, **kwargs: Any) -> Any:
        self.parameter = kwargs
        raise AssertionError("abgefangen")  # pragma: no cover - nie erreicht


async def _gebaute_parameter(settings: Settings, model: str) -> dict[str, Any]:
    """Laesst ``structured_call`` den Request bauen und faengt ihn ab."""
    abfang = SignaturClient()
    client = LLMClient(settings, client=abfang)  # type: ignore[arg-type]
    with pytest.raises(Exception):  # noqa: B017 - der Abfang wirft immer
        await client.structured_call(
            model=model,
            system="Systemtext",
            user_payload={"cleaned_body": "Text"},
            schema_model=TriageResultV1,
            temperature=0.0,
            max_tokens=settings.triage_max_tokens,
            timeout_s=settings.triage_timeout_s,
        )
    return abfang.parameter


# ---------------------------------------------------------------------------
# Signatur: jeder gebaute Parameter muss vom installierten SDK akzeptiert werden
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["claude-haiku-4-5-20251001", "claude-sonnet-5"])
async def test_parameter_passen_zur_sdk_signatur(model: str) -> None:
    """Schlaegt an, sobald das SDK einen benutzten Parameter entfernt.

    Genau dieser Test haette den ``TypeError`` wegen ``temperature`` unter
    SDK 1.x vor dem Live-Lauf gefangen.
    """
    settings = Settings(anthropic_api_key=SecretStr("test-key"))
    parameter = await _gebaute_parameter(settings, model)

    assert parameter, "structured_call hat keinen Request gebaut"
    signatur = _create_signatur()
    unbekannt = sorted(set(parameter) - set(signatur.parameters))
    assert not unbekannt, (
        f"Das installierte SDK kennt diese Parameter nicht (mehr): {unbekannt}"
    )
    # Der Aufruf muss sich auch tatsaechlich binden lassen (Pflichtfelder).
    signatur.bind_partial(**parameter)
    assert parameter["model"] == model
    assert "messages" in parameter
    assert "max_tokens" in parameter


@pytest.mark.asyncio
async def test_temperature_erreicht_das_modell_das_sie_akzeptiert() -> None:
    """Haiku 4.5 bekommt die Temperatur - als Parameter oder via extra_body."""
    settings = Settings(anthropic_api_key=SecretStr("test-key"))
    parameter = await _gebaute_parameter(settings, settings.triage_model)

    gesendet = parameter.get(
        "temperature", parameter.get("extra_body", {}).get("temperature")
    )
    assert gesendet == 0.0, "Die Determinismus-Einstellung der Triage geht verloren"


def test_temperatur_parameter_pro_sdk_und_modell() -> None:
    """Modelle ohne Sampling-Parameter bekommen nichts, andere den passenden Weg."""
    # Sonnet 5 lehnt Sampling-Parameter ab - weder Feld noch extra_body.
    assert temperatur_parameter("claude-sonnet-5", 0.2) == {}
    assert temperatur_parameter("claude-opus-5", 0.2) == {}

    eintrag = temperatur_parameter("claude-haiku-4-5-20251001", 0.0)
    assert eintrag in ({"temperature": 0.0}, {"extra_body": {"temperature": 0.0}})
    # Welcher Weg gewaehlt wird, entscheidet die Signatur des SDK.
    kennt_parameter = "temperature" in _create_signatur().parameters
    assert ("temperature" in eintrag) is kennt_parameter


# ---------------------------------------------------------------------------
# Schema: Structured Outputs vertraegt keine Zahlengrenzen
# ---------------------------------------------------------------------------


def _finde_zahlengrenzen(knoten: Any, pfad: str = "") -> list[str]:
    """Alle Fundstellen von minimum/maximum, rekursiv inklusive ``$defs``."""
    treffer: list[str] = []
    if isinstance(knoten, dict):
        for schluessel, wert in knoten.items():
            stelle = f"{pfad}.{schluessel}" if pfad else schluessel
            if schluessel in ZAHLENGRENZEN:
                treffer.append(stelle)
            treffer.extend(_finde_zahlengrenzen(wert, stelle))
    elif isinstance(knoten, list):
        for index, eintrag in enumerate(knoten):
            treffer.extend(_finde_zahlengrenzen(eintrag, f"{pfad}[{index}]"))
    return treffer


@pytest.mark.parametrize("modell", SCHEMA_MODELLE, ids=lambda m: m.__name__)
def test_bereinigtes_schema_ohne_zahlengrenzen(modell: type[BaseModel]) -> None:
    roh = json_schema_fuer(modell)
    # Das Rohschema hat die Grenzen - sonst prueft der Test nichts.
    assert _finde_zahlengrenzen(roh), f"{modell.__name__} hat keine Zahlengrenzen mehr"

    bereinigt = _ohne_zahlengrenzen(roh)

    gefunden = _finde_zahlengrenzen(bereinigt)
    assert not gefunden, f"Zahlengrenzen uebrig: {gefunden}"
    # Der Rest des Schemas bleibt unangetastet.
    assert bereinigt["required"] == roh["required"]
    assert bereinigt["additionalProperties"] is False
    assert set(bereinigt["properties"]) == set(roh["properties"])


@pytest.mark.parametrize("modell", SCHEMA_MODELLE, ids=lambda m: m.__name__)
def test_zahlengrenzen_bleiben_in_der_validierung(modell: type[BaseModel]) -> None:
    """Die Bereinigung betrifft nur die Schema-Kopie, nicht das Modell."""
    felder = modell.model_fields["confidence"].metadata
    assert felder, "confidence hat keine Grenzen mehr - Gate-Annahme gefaehrdet"
    with pytest.raises(ValueError):
        modell.model_validate({"confidence": 1.5})


@pytest.mark.asyncio
async def test_gesendetes_schema_ist_bereinigt() -> None:
    """Der Weg bis in den Request: output_config traegt kein minimum/maximum."""
    settings = Settings(anthropic_api_key=SecretStr("test-key"))
    parameter = await _gebaute_parameter(settings, settings.triage_model)

    schema = parameter["output_config"]["format"]["schema"]
    assert not _finde_zahlengrenzen(schema)


# ---------------------------------------------------------------------------
# Live-Rauchtest - nicht in der Standard-Suite (pytest -m live)
# ---------------------------------------------------------------------------


#: Gemessen am 23.09.2026 auf macOS gegen die echte API (anthropic 1.8.0,
#: Schema bereinigt, output_config aktiv). Die Tabelle belegt, warum
#: ``temperatur_parameter`` nach Modell unterscheidet, statt pauschal zu
#: senden: Haiku 4.5 nimmt den Wert ueber extra_body an, Sonnet 5 weist ihn
#: mit HTTP 400 ab. Pauschales Senden haette jeden Entwurf zerlegt.
#:
#: (Rolle, gesendete Temperatur oder None, Erfolg erwartet)
LIVE_TEMPERATUR_MATRIX: list[tuple[str, float | None, bool]] = [
    ("triage", 0.0, True),
    ("triage", None, True),
    ("drafter", 0.2, False),
    ("drafter", None, True),
]

#: Wortlaut der Ablehnung von Sonnet 5, Stand 23.09.2026:
#: "`temperature` is deprecated for this model."
#: Geprueft wird auf die beiden tragenden Woerter, damit eine umformulierte
#: Meldung den Test nicht faellt - ein geaendertes *Verhalten* schon.
ABLEHNUNG_KENNWORTE = ("temperature", "deprecated")


async def _roher_aufruf(
    settings: Settings, model: str, temperature: float | None
) -> Any:
    """Setzt den Request direkt ab, unter Umgehung der Modell-Weiche.

    Nur so laesst sich auch der Fall pruefen, den der Produktivcode
    absichtlich nie erzeugt: Sonnet 5 mit gesendeter Temperatur.
    """
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
    zusatz: dict[str, Any] = {}
    if temperature is not None:
        zusatz["extra_body"] = {"temperature": temperature}
    return await client.messages.create(
        model=model,
        max_tokens=settings.triage_max_tokens,
        system="Du gibst ausschliesslich ein JSON-Objekt nach dem Schema aus.",
        messages=[{"role": "user", "content": '{"cleaned_body": "Guten Tag"}'}],
        output_config={
            "format": {
                "type": "json_schema",
                "schema": _ohne_zahlengrenzen(json_schema_fuer(TriageResultV1)),
            }
        },
        **zusatz,
    )


@pytest.mark.live
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rolle", "temperature", "erfolg"),
    LIVE_TEMPERATUR_MATRIX,
    ids=lambda wert: str(wert),
)
async def test_live_temperatur_matrix(
    rolle: str, temperature: float | None, erfolg: bool
) -> None:
    """Belegt die vier gemessenen Kombinationen gegen die echte API."""
    import anthropic

    schluessel = os.environ.get("ANTHROPIC_API_KEY")
    if not schluessel:
        pytest.skip("ANTHROPIC_API_KEY nicht gesetzt")

    settings = Settings(anthropic_api_key=SecretStr(schluessel))
    model = settings.triage_model if rolle == "triage" else settings.drafter_model

    if erfolg:
        antwort = await _roher_aufruf(settings, model, temperature)
        assert antwort.content, f"{model} lieferte keinen Inhalt"
        return

    with pytest.raises(anthropic.BadRequestError) as info:
        await _roher_aufruf(settings, model, temperature)
    meldung = str(info.value).lower()
    for kennwort in ABLEHNUNG_KENNWORTE:
        assert kennwort in meldung, (
            f"{model} lehnt die Temperatur anders ab als am 23.09.2026 gemessen "
            f"('{kennwort}' fehlt): {meldung}"
        )


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_weiche_waehlt_nur_akzeptierte_kombinationen() -> None:
    """Was ``temperatur_parameter`` sendet, muss die API auch annehmen."""
    schluessel = os.environ.get("ANTHROPIC_API_KEY")
    if not schluessel:
        pytest.skip("ANTHROPIC_API_KEY nicht gesetzt")

    settings = Settings(anthropic_api_key=SecretStr(schluessel))
    for model, temperatur in (
        (settings.triage_model, settings.triage_temperature),
        (settings.drafter_model, settings.drafter_temperature),
    ):
        gewaehlt = temperatur_parameter(model, temperatur)
        gesendet = gewaehlt.get(
            "temperature", gewaehlt.get("extra_body", {}).get("temperature")
        )
        antwort = await _roher_aufruf(settings, model, gesendet)
        assert antwort.content, f"{model} lehnte die gewaehlte Kombination ab"


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_minimalaufruf_gegen_echte_api() -> None:
    """Minimalaufruf gegen die echte API; kostet einen Bruchteil eines Cents."""
    schluessel = os.environ.get("ANTHROPIC_API_KEY")
    if not schluessel:
        pytest.skip("ANTHROPIC_API_KEY nicht gesetzt")

    settings = Settings(anthropic_api_key=SecretStr(schluessel))
    client = LLMClient(settings)

    ergebnis, usage = await client.structured_call(
        model=settings.triage_model,
        system="Du gibst ausschliesslich ein JSON-Objekt nach dem Schema aus.",
        user_payload={
            "message_id": "live-smoke-1",
            "subject": "Testlauf",
            "cleaned_body": "Guten Tag, dies ist ein Rauchtest. Freundliche Gruesse",
        },
        schema_model=TriageResultV1,
        temperature=settings.triage_temperature,
        max_tokens=settings.triage_max_tokens,
        timeout_s=settings.triage_timeout_s,
    )

    assert ergebnis.message_id
    assert 0.0 <= ergebnis.confidence <= 1.0
    assert usage.input_tokens > 0
    assert usage.output_tokens > 0
