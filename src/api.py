"""FastAPI-Adapter: HTTP-Einstieg in den Dispositions-Kern.

Die API ist ein reiner Treiber-Adapter (driving adapter). Sie uebersetzt
HTTP-Requests in Aufrufe der Engine und deren Ergebnisse zurueck nach JSON.
Fachlogik enthaelt sie nicht.

Betriebseigenschaften:
    * zustandslos - jeder Request ist unabhaengig, horizontal skalierbar,
    * In-Memory   - hochgeladene Daten werden nie persistiert,
    * generisch   - keine Annahmen ueber Branche, Sortiment oder ERP.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Sequence

from fastapi import Body, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from src.adapters.csv_ingest import CSVIngestFehler, lese_csv
from src.config import lade_config
from src.domain.disposition import Status
from src.engine import DispositionEngine, baue_engine
from src.ports.forecasting import ForecastUnavailable
from src.schemas import (
    AnalysisResponse,
    AnalysisResult,
    BatchMeta,
    IngestWarnung,
    SKUInput,
)

__all__ = ["app", "erstelle_app", "hole_engine"]

_LOG = logging.getLogger(__name__)

# Statuscodes bewusst als Literale: die Konstantennamen in Starlette
# haben sich zwischen Versionen geaendert (422 Entity -> Content).
HTTP_422_UNPROCESSABLE = 422
HTTP_413_ZU_GROSS = 413
HTTP_503_MODELL_NICHT_BEREIT = 503

#: Obergrenze fuer CSV-Uploads (Schutz vor Speicherdruck, 64 MiB).
MAX_CSV_BYTES = 64 * 1024 * 1024

BESCHREIBUNG = """
**Stockout-Sentinel** ist ein generischer Dispositions-Service: Er prognostiziert
den Bedarf je Artikel, berechnet Meldebestand und Nachbestellmenge und liefert
eine nach Dringlichkeit sortierte Prioritaetenliste.

* `POST /api/v1/analyze-json` - strukturierte Artikelliste (System-zu-System).
* `POST /api/v1/analyze-csv`  - CSV-Export eines beliebigen ERP-Systems.

**Prognosemodell:** Im Standardbetrieb (`FORCE_TIMESFM=true`) laeuft jede
Prognose ueber das TimesFM-Foundation-Model von Google, geladen direkt vom
Hugging-Face-Checkpoint. Ein Rueckfall auf ein anderes Verfahren ist
blockiert: laesst sich das Modell nicht laden, startet der Service nicht;
scheitert eine Inferenz, antwortet der Endpunkt mit `503` statt mit Zahlen
aus einer Ersatzrechnung.

Der Service ist zustandslos und speichert keine uebergebenen Daten.
"""


def _konfiguriere_logging() -> None:
    """Stellt sicher, dass die Startmeldungen des Service sichtbar sind.

    Ohne Handler auf dem Root-Logger verschwinden INFO-Meldungen - gerade
    das Laden des Pflichtmodells muss im Betrieb aber nachvollziehbar sein.
    Eine bereits vorhandene Konfiguration (z. B. durch den Hoster) wird
    nicht ueberschrieben.
    """
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )


def erstelle_app(engine: DispositionEngine | None = None) -> FastAPI:
    """Baut die FastAPI-Anwendung (Factory, damit Tests injizieren koennen)."""
    _konfiguriere_logging()
    @asynccontextmanager
    async def lifespan(laufende_app: FastAPI) -> AsyncIterator[None]:
        """Baut die Engine einmalig beim Start (Prognosekette inklusive)."""
        if laufende_app.state.engine is None:
            laufende_app.state.engine = baue_engine(lade_config())
        engine = laufende_app.state.engine

        # Ist TimesFM verbindlich, wird das Checkpoint hier geladen. Faellt
        # das aus, startet der Service bewusst nicht: ein Dispositions-
        # service, der klaglos mit einem anderen Modell weiterrechnet, waere
        # schlimmer als einer, der sichtbar nicht hochkommt.
        if engine.config.force_timesfm:
            _LOG.info(
                "FORCE_TIMESFM aktiv - lade Pflichtmodell '%s' ...",
                engine.config.timesfm_checkpoint,
            )
            engine.starte()
            _LOG.info("Pflichtmodell geladen, Fallback ist blockiert.")

        _LOG.info(
            "Stockout-Sentinel bereit. Prognosekette: %s",
            ", ".join(a.name for a in engine.prognose_kette),
        )
        yield

    anwendung = FastAPI(
        title="Stockout-Sentinel API",
        version="1.0.0",
        description=BESCHREIBUNG,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    anwendung.state.engine = engine

    _registriere_routen(anwendung)
    return anwendung


def hole_engine(request: Request) -> DispositionEngine:
    """Dependency: liefert die Engine aus dem Anwendungszustand."""
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        engine = baue_engine(lade_config())
        request.app.state.engine = engine
    return engine


def _baue_antwort(
    ergebnisse: Sequence[AnalysisResult],
    *,
    erkannte_spalten: dict[str, str] | None = None,
    warnungen: Sequence[IngestWarnung] | None = None,
) -> AnalysisResponse:
    """Fasst die Ergebnisse samt Kennzahlen zur API-Antwort zusammen."""
    zaehler = {status_wert.code: 0 for status_wert in Status}
    for ergebnis in ergebnisse:
        zaehler[ergebnis.status_code] = zaehler.get(ergebnis.status_code, 0) + 1

    modelle = sorted({ergebnis.prognose_modell for ergebnis in ergebnisse if ergebnis.prognose_modell})

    meta = BatchMeta(
        anzahl_artikel=len(ergebnisse),
        anzahl_kritisch=zaehler.get(Status.KRITISCH.code, 0),
        anzahl_optimal=zaehler.get(Status.OPTIMAL.code, 0),
        anzahl_ueberbestand=zaehler.get(Status.UEBERBESTAND.code, 0),
        gesamt_nachbestellmenge=round(
            sum(ergebnis.nachbestellmenge for ergebnis in ergebnisse), 2
        ),
        prognose_modelle=modelle,
        erkannte_spalten=erkannte_spalten or {},
        warnungen=list(warnungen or []),
    )
    return AnalysisResponse(meta=meta, ergebnisse=list(ergebnisse))


def _registriere_routen(anwendung: FastAPI) -> None:
    @anwendung.get("/health", tags=["Betrieb"], summary="Liveness- und Readiness-Probe")
    async def health(request: Request) -> dict[str, object]:
        """Meldet Betriebsbereitschaft und die aktive Prognosekette."""
        engine = hole_engine(request)
        bereit = engine.modell_bereit
        return {
            "status": "ok" if bereit else "degraded",
            "version": anwendung.version,
            "prognose_kette": [adapter.name for adapter in engine.prognose_kette],
            "prognose_strategie": engine.config.prognose_strategie,
            "force_timesfm": engine.config.force_timesfm,
            "timesfm_checkpoint": engine.config.timesfm_checkpoint,
            "modell_geladen": bereit,
            "fallback_erlaubt": engine.config.fallback_erlaubt,
        }

    @anwendung.post(
        "/api/v1/analyze-json",
        response_model=AnalysisResponse,
        tags=["Disposition"],
        summary="Artikelliste analysieren (JSON)",
    )
    async def analyze_json(
        request: Request,
        artikel: Annotated[
            list[SKUInput],
            Body(
                ...,
                description="Liste der zu disponierenden Artikel.",
                examples=[
                    [
                        {
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
                    ]
                ],
            ),
        ],
    ) -> AnalysisResponse:
        """Analysiert eine Artikelliste und liefert die Prioritaetenliste.

        Sortierung: kritische Artikel mit der geringsten Reichweite zuerst.
        """
        engine = hole_engine(request)
        _pruefe_batchgroesse(len(artikel), engine)
        if not artikel:
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE,
                detail="Die Artikelliste ist leer.",
            )
        return _baue_antwort(engine.analysiere_batch(artikel))

    @anwendung.post(
        "/api/v1/analyze-csv",
        response_model=AnalysisResponse,
        tags=["Disposition"],
        summary="ERP-CSV-Export analysieren",
    )
    async def analyze_csv(
        request: Request,
        datei: Annotated[
            UploadFile | None,
            File(description="CSV-Datei als multipart/form-data."),
        ] = None,
        trennzeichen: Annotated[
            str | None,
            Query(
                description="Festes Trennzeichen erzwingen (sonst automatisch erkannt).",
                max_length=1,
            ),
        ] = None,
    ) -> AnalysisResponse:
        """Liest einen ERP-CSV-Export im RAM und analysiert ihn.

        Der Stream kann als ``multipart/form-data`` (Feld ``datei``) oder
        direkt als Request-Body (``text/csv``) gesendet werden. Spalten
        werden flexibel zugeordnet ("Art-Nr", "SKU", "Bestand", "Lager",
        "Vorlaufzeit", ...); Lang- und Breitformat werden erkannt.
        """
        engine = hole_engine(request)
        rohdaten = await _lies_rohdaten(request, datei)

        try:
            import_ergebnis = lese_csv(rohdaten, trennzeichen=trennzeichen)
        except CSVIngestFehler as exc:
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE, detail=str(exc)
            ) from exc

        _pruefe_batchgroesse(len(import_ergebnis.artikel), engine)
        ergebnisse = engine.analysiere_batch(import_ergebnis.artikel)
        return _baue_antwort(
            ergebnisse,
            erkannte_spalten=import_ergebnis.spalten_mapping,
            warnungen=import_ergebnis.warnungen,
        )

    @anwendung.exception_handler(CSVIngestFehler)
    async def _csv_fehler(_: Request, exc: CSVIngestFehler) -> JSONResponse:
        return JSONResponse(
            status_code=HTTP_422_UNPROCESSABLE, content={"detail": str(exc)}
        )

    @anwendung.exception_handler(ForecastUnavailable)
    async def _prognose_fehler(_: Request, exc: ForecastUnavailable) -> JSONResponse:
        """Meldet eine gescheiterte Pflichtprognose als 503.

        Bewusst ein Fehler und kein Ergebnis aus einem Ersatzmodell: die
        Antwort darf nicht so aussehen, als sei sie von TimesFM gerechnet.
        """
        _LOG.error("Prognose nicht moeglich: %s", exc)
        return JSONResponse(
            status_code=HTTP_503_MODELL_NICHT_BEREIT,
            content={
                "detail": (
                    "Die Bedarfsprognose konnte nicht erstellt werden. "
                    "TimesFM ist als Hauptmodell verbindlich (FORCE_TIMESFM), "
                    "ein Rueckfall auf ein anderes Verfahren ist blockiert. "
                    f"Ursache: {exc}"
                )
            },
        )


def _pruefe_batchgroesse(anzahl: int, engine: DispositionEngine) -> None:
    grenze = engine.config.max_sku_pro_request
    if anzahl > grenze:
        raise HTTPException(
            status_code=HTTP_413_ZU_GROSS,
            detail=f"{anzahl} Artikel uebersteigen das Limit von {grenze} pro Request.",
        )


async def _lies_rohdaten(request: Request, datei: UploadFile | None) -> bytes:
    """Holt den CSV-Stream aus dem Upload-Feld oder direkt aus dem Body."""
    if datei is not None:
        rohdaten = await datei.read()
    else:
        rohdaten = await request.body()

    if not rohdaten:
        raise HTTPException(
            status_code=HTTP_422_UNPROCESSABLE,
            detail=(
                "Kein CSV-Inhalt empfangen. Sende die Datei als multipart/form-data "
                "im Feld 'datei' oder als Request-Body mit Content-Type text/csv."
            ),
        )
    if len(rohdaten) > MAX_CSV_BYTES:
        raise HTTPException(
            status_code=HTTP_413_ZU_GROSS,
            detail=f"CSV ueberschreitet das Limit von {MAX_CSV_BYTES // (1024 * 1024)} MiB.",
        )
    return rohdaten


#: ASGI-Einstiegspunkt: ``uvicorn src.api:app``
app = erstelle_app()
