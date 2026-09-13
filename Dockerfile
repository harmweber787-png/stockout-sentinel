# syntax=docker/dockerfile:1
###############################################################################
# Stockout-Sentinel - schlankes Laufzeit-Image fuer Cloud-Deployments
#
# Zweistufiger Build: Abhaengigkeiten werden in einem Wheel-Layer installiert
# und anschliessend in ein minimales Runtime-Image kopiert. Das Ergebnis
# enthaelt keinen Compiler und keine Build-Artefakte.
#
# TimesFM ist das zwingende Hauptmodell (FORCE_TIMESFM=true). Der Container
# laedt beim Start das Google-Checkpoint von Hugging Face und startet nicht,
# wenn das nicht gelingt - siehe Abschnitt "Modellgewichte" unten.
###############################################################################

# --- Stufe 1: Abhaengigkeiten ------------------------------------------------
FROM python:3.11-slim AS builder

# Standardmaessig die CPU-Wheels von PyTorch verwenden. Die CUDA-Variante von
# PyPI ist mehrere GB gross und in CPU-Deployments reiner Ballast.
# Fuer GPU-Images: --build-arg TORCH_INDEX_URL=https://pypi.org/simple
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

COPY requirements.txt .
# In ein eigenes Praefix installieren, damit nur dieses Verzeichnis in die
# Laufzeitstufe wandert.
RUN python -m pip install --prefix=/install \
        --extra-index-url "${TORCH_INDEX_URL}" \
        -r requirements.txt


# --- Stufe 2: Laufzeit -------------------------------------------------------
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="Stockout-Sentinel" \
      org.opencontainers.image.description="Dispositions- und Prognose-Service auf Basis von TimesFM" \
      org.opencontainers.image.licenses="Proprietary"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PORT=8000 \
    # TimesFM ist verbindlich: kein Rueckfall auf ein anderes Verfahren.
    FORCE_TIMESFM=true \
    STOCKOUT_TIMESFM_CHECKPOINT=google/timesfm-2.5-200m-pytorch \
    STOCKOUT_TIMESFM_BACKEND=cpu \
    # Modell-Cache in einem Verzeichnis, das dem Laufzeitnutzer gehoert.
    HF_HOME=/home/sentinel/.cache/huggingface \
    # Ein Worker = eine Modellkopie im Speicher. Bewusst konservativ.
    WEB_CONCURRENCY=1

# Nicht-privilegierter Nutzer: Container laufen nie als root.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin sentinel

COPY --from=builder /install /usr/local

WORKDIR /app
COPY --chown=sentinel:sentinel src/ ./src/

RUN mkdir -p "${HF_HOME}" && chown -R sentinel:sentinel /home/sentinel

USER sentinel

###############################################################################
# Modellgewichte
#
# Der Start laedt das Checkpoint von Hugging Face. Damit braucht der Container
# beim ersten Start Netzzugang zu huggingface.co. Fuer abgeschottete
# Umgebungen und schnelle Kaltstarts eine der beiden Varianten waehlen:
#
#   a) Gewichte in das Image backen (vergroessert es um ~1 GB):
#      RUN python -c "import timesfm; \
#          timesfm.TimesFM_2p5_200M_torch.from_pretrained( \
#              'google/timesfm-2.5-200m-pytorch')"
#
#   b) Gewichte als Volume mounten und lokal laden:
#      docker run -v /opt/timesfm:/models:ro \
#                 -e STOCKOUT_TIMESFM_CHECKPOINT=/models/timesfm-2.5-200m \
#                 stockout-sentinel
###############################################################################

EXPOSE 8000

# Readiness-Probe gegen den eigenen Health-Endpunkt. Die Startphase ist
# grosszuegig bemessen: das Laden des Checkpoints dauert beim Kaltstart
# deutlich laenger als ein reiner Prozessstart.
HEALTHCHECK --interval=30s --timeout=10s --start-period=300s --retries=3 \
    CMD python -c "import os,urllib.request,json,sys; \
r=urllib.request.urlopen(f\"http://127.0.0.1:{os.getenv('PORT','8000')}/health\", timeout=8); \
sys.exit(0 if r.status == 200 and json.load(r).get('modell_geladen') else 1)"

# Cloud-Runtimes (Cloud Run, App Service, Fly.io) geben den Port ueber $PORT vor.
CMD ["sh", "-c", "exec uvicorn src.api:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --proxy-headers"]
