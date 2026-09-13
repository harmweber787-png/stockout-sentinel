# syntax=docker/dockerfile:1
###############################################################################
# Stockout-Sentinel - schlankes Laufzeit-Image fuer Cloud-Deployments
#
# Zweistufiger Build: Abhaengigkeiten werden in einem Wheel-Layer installiert
# und anschliessend in ein minimales Runtime-Image kopiert. Das Ergebnis
# enthaelt keinen Compiler und keine Build-Artefakte.
###############################################################################

# --- Stufe 1: Abhaengigkeiten ------------------------------------------------
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

COPY requirements.txt .
# In ein eigenes Praefix installieren, damit nur dieses Verzeichnis in die
# Laufzeitstufe wandert.
RUN python -m pip install --prefix=/install -r requirements.txt


# --- Stufe 2: Laufzeit -------------------------------------------------------
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="Stockout-Sentinel" \
      org.opencontainers.image.description="Generischer Dispositions- und Prognose-Service" \
      org.opencontainers.image.licenses="Proprietary"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PORT=8000 \
    # Prognosestrategie: "auto" nutzt TimesFM, sofern installiert, und faellt
    # sonst auf den statistischen Schaetzer zurueck.
    STOCKOUT_PROGNOSE_STRATEGIE=auto

# Nicht-privilegierter Nutzer: Container laufen nie als root.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin sentinel

COPY --from=builder /install /usr/local

WORKDIR /app
COPY --chown=sentinel:sentinel src/ ./src/

USER sentinel

EXPOSE 8000

# Readiness-Probe gegen den eigenen Health-Endpunkt.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen(f\"http://127.0.0.1:{os.getenv('PORT','8000')}/health\", timeout=4).status == 200 else 1)"

# Cloud-Runtimes (Cloud Run, App Service, Fly.io) geben den Port ueber $PORT vor.
CMD ["sh", "-c", "exec uvicorn src.api:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --proxy-headers"]
