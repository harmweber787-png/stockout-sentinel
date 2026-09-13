"""Pydantic-Datenmodelle der oeffentlichen API (Adapter-Schicht).

Die Modelle beschreiben ausschliesslich den Vertrag nach aussen. Sie
enthalten keine Fachlogik - die liegt in ``src/domain`` - und sind bewusst
generisch gehalten: keine Annahmen ueber Branche, Sortiment oder ERP.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "AnalysisResult",
    "AnalysisResponse",
    "BatchMeta",
    "ConsumptionRecord",
    "IngestWarnung",
    "SKUInput",
    "StatusLiteral",
]

#: Maschinenlesbare Statuscodes (ohne Ampel-Symbol).
StatusLiteral = Literal["KRITISCH", "OPTIMAL", "UEBERBESTAND"]


class ConsumptionRecord(BaseModel):
    """Verbrauch einer einzelnen Periode.

    Attributes:
        datum: Periodenkennzeichen. Beliebiges gaengiges Format
            (``2024-03-01``, ``01.03.2024``, ``2024-03``, ``Mrz 24``).
            Aus den Abstaenden leitet die Engine die Kadenz der Reihe ab.
        menge: Verbrauchte Menge in der Periode.
    """

    model_config = ConfigDict(extra="ignore")

    datum: str = Field(
        ...,
        description="Periodenkennzeichen, z. B. '2024-03-01', '01.03.2024' oder '2024-03'.",
        examples=["2024-03-01"],
    )
    menge: float = Field(
        ...,
        description="Verbrauchsmenge der Periode.",
        examples=[128.5],
    )

    @field_validator("datum", mode="before")
    @classmethod
    def _datum_zu_text(cls, wert: Any) -> Any:
        """Akzeptiert auch date/datetime/Zahlen aus fremden Serialisierern."""
        if wert is None:
            return wert
        return str(wert).strip() if not isinstance(wert, str) else wert.strip()


class SKUInput(BaseModel):
    """Dispositions-Eingabe fuer genau einen Artikel.

    Attributes:
        sku: Artikelnummer oder eindeutige Bezeichnung.
        historie: Verbrauchshistorie, mindestens zwei Perioden.
        bestand: Aktueller Lagerbestand.
        lieferzeit: Wiederbeschaffungszeit in Tagen.
        mindestbestellmenge: Optionale Mindestbestellmenge des Lieferanten.
    """

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "example": {
                "sku": "A-10045",
                "historie": [
                    {"datum": "2024-01-01", "menge": 120},
                    {"datum": "2024-02-01", "menge": 135},
                    {"datum": "2024-03-01", "menge": 150},
                ],
                "bestand": 80,
                "lieferzeit": 14,
                "mindestbestellmenge": 50,
            }
        },
    )

    sku: Annotated[str, Field(min_length=1, max_length=200)] = Field(
        ..., description="Artikelnummer oder Bezeichnung."
    )
    historie: Annotated[list[ConsumptionRecord], Field(min_length=2)] = Field(
        ..., description="Mindestens 2 Verbrauchsperioden in beliebiger Granularitaet."
    )
    bestand: float = Field(..., ge=0, description="Aktueller Lagerbestand.")
    lieferzeit: int = Field(
        ..., ge=0, le=3650, description="Wiederbeschaffungszeit in Tagen."
    )
    mindestbestellmenge: float | None = Field(
        default=0, ge=0, description="Optionale Mindestbestellmenge (MOQ)."
    )

    @field_validator("sku", mode="before")
    @classmethod
    def _sku_normalisieren(cls, wert: Any) -> Any:
        return str(wert).strip() if wert is not None else wert


class AnalysisResult(BaseModel):
    """Dispositions-Ergebnis eines Artikels.

    Attributes:
        sku: Artikelnummer aus der Eingabe.
        prognose_tagesbedarf: Prognostizierter Tagesbedarf (Monatsabsatz/30).
        reichweite_tage: Bestandsreichweite in Tagen (999 = kein Bedarf).
        meldebestand: Bestellpunkt inkl. Sicherheitsbestand.
        nachbestellmenge: Empfohlene Bestellmenge (0 = keine Bestellung).
        status: Ampel-Status inkl. Symbol, z. B. ``"🔴 KRITISCH"``.
        status_code: Symbolfreier Code fuer die maschinelle Weiterverarbeitung.
        empfohlene_massnahme: Ausformulierte Handlungsempfehlung.
        sicherheitsbestand: Puffer aus dem P90/P50-Korridor.
        prognose_tagesbedarf_p90: Tagesbedarf im 90 %-Quantil.
        prognose_modell: Bezeichner des verwendeten Prognosemodells.
        prognose_fallback: ``True``, wenn der statistische Fallback griff.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "sku": "A-10045",
                "prognose_tagesbedarf": 5.12,
                "reichweite_tage": 15.63,
                "meldebestand": 82.35,
                "nachbestellmenge": 50.0,
                "status": "🟢 OPTIMAL",
                "status_code": "OPTIMAL",
                "empfohlene_massnahme": "Bestand im Zielkorridor ...",
                "sicherheitsbestand": 10.67,
                "prognose_tagesbedarf_p90": 5.88,
                "prognose_modell": "statistical-theil-sen-p90",
                "prognose_fallback": True,
            }
        }
    )

    sku: str
    prognose_tagesbedarf: float
    reichweite_tage: float
    meldebestand: float
    nachbestellmenge: float
    status: str = Field(
        ..., description="Ampel-Status mit Symbol: '🔴 KRITISCH' | '🟢 OPTIMAL' | '🟡 UEBERBESTAND'."
    )
    status_code: StatusLiteral = Field(
        ..., description="Symbolfreier Status fuer Filter und Auswertungen."
    )
    empfohlene_massnahme: str

    # -- Nachvollziehbarkeit der Prognose ---------------------------------
    sicherheitsbestand: float = Field(
        default=0.0, description="lieferzeit * (tagesbedarf_P90 - tagesbedarf_P50)."
    )
    prognose_tagesbedarf_p90: float = Field(
        default=0.0, description="Tagesbedarf im 90 %-Quantil."
    )
    prognose_modell: str = Field(
        default="", description="Verwendetes Prognosemodell."
    )
    prognose_fallback: bool = Field(
        default=False, description="True, wenn der statistische Fallback verwendet wurde."
    )


class IngestWarnung(BaseModel):
    """Nicht-fataler Hinweis aus dem CSV-Import.

    Zeilen, die sich nicht verwerten lassen, brechen den Request nicht ab -
    sie werden hier gemeldet, damit der Rest des Exports nutzbar bleibt.
    """

    sku: str | None = None
    zeile: int | None = None
    meldung: str


class BatchMeta(BaseModel):
    """Kennzahlen ueber den ausgefuehrten Analyselauf."""

    anzahl_artikel: int
    anzahl_kritisch: int
    anzahl_optimal: int
    anzahl_ueberbestand: int
    gesamt_nachbestellmenge: float
    prognose_modelle: list[str] = Field(default_factory=list)
    erkannte_spalten: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping Zielfeld -> erkannte Spalte des Quellsystems (nur CSV).",
    )
    warnungen: list[IngestWarnung] = Field(default_factory=list)


class AnalysisResponse(BaseModel):
    """Antwort beider Analyse-Endpunkte.

    ``ergebnisse`` ist bereits als Prioritaetenliste sortiert: kritische
    Artikel mit der geringsten Reichweite zuerst.
    """

    meta: BatchMeta
    ergebnisse: list[AnalysisResult]
