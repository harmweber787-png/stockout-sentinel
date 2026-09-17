# syntax=docker/dockerfile:1
###############################################################################
# Stockout-Sentinel - autarkes Laufzeit-Image mit eingebackenen Modellgewichten
#
# Das Image ist nach dem Build vollstaendig netzunabhaengig: Das TimesFM-
# Checkpoint von Google liegt im Dateisystem, und die Laufzeit ist per
# HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE strikt offline gestellt. Damit
# entfaellt das Verfuegbarkeitsrisiko, das ein Download beim Containerstart
# mit sich bringt - relevant, weil TimesFM Pflichtmodell ist und der Service
# ohne geladenes Modell bewusst nicht hochfaehrt (FORCE_TIMESFM).
#
# Preis dafuer: das Image waechst um die Groesse der Gewichte (~1 GB).
###############################################################################

# --- Stufe 1: Abhaengigkeiten ------------------------------------------------
FROM python:3.11-slim AS builder

# PyTorch aus dem CPU-Index: die CUDA-Variante von PyPI ist mehrere GB gross
# und in CPU-Deployments reiner Ballast.
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

COPY requirements.txt .

# PyTorch zuerst und gezielt aus dem CPU-Index. Der PyTorch-Index fuehrt nicht
# alle transitiven Abhaengigkeiten (filelock, jinja2, sympy, ...), daher bleibt
# PyPI als zusaetzliche Quelle eingehaengt.
RUN python -m pip install --prefix=/install \
        --index-url "${TORCH_INDEX_URL}" \
        --extra-index-url https://pypi.org/simple \
        torch

# Restliche Abhaengigkeiten. torch ist bereits erfuellt und wird nicht
# erneut - und schon gar nicht als CUDA-Variante - gezogen.
RUN python -m pip install --prefix=/install \
        --extra-index-url "${TORCH_INDEX_URL}" \
        -r requirements.txt \
        huggingface_hub


# --- Stufe 2: Laufzeit -------------------------------------------------------
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="Stockout-Sentinel" \
      org.opencontainers.image.description="Dispositions- und Prognose-Service mit eingebackenem TimesFM-Modell" \
      org.opencontainers.image.licenses="Proprietary"

ARG TIMESFM_REPO_ID=google/timesfm-2.5-200m-pytorch
ARG MODEL_DIR=/app/models/timesfm-checkpoint

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

COPY --from=builder /install /usr/local

# Nicht-privilegierter Nutzer: Container laufen nie als root.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin sentinel

###############################################################################
# Model Baking
#
# Laeuft bewusst VOR den Offline-Umgebungsvariablen - waeren HF_HUB_OFFLINE
# und TRANSFORMERS_OFFLINE hier schon gesetzt, wuerde der Download scheitern.
# Der anschliessende Abgleich laesst den Build fehlschlagen, falls die
# Gewichtsdatei fehlt: ein Image ohne Gewichte koennte zur Laufzeit nicht
# starten, und das soll hier auffallen, nicht erst im Deployment.
###############################################################################
RUN python -c "\
from huggingface_hub import snapshot_download; \
snapshot_download( \
    repo_id='${TIMESFM_REPO_ID}', \
    local_dir='${MODEL_DIR}', \
    local_dir_use_symlinks=False)" \
 && test -f "${MODEL_DIR}/model.safetensors" \
 && chown -R sentinel:sentinel /app/models \
 && du -sh "${MODEL_DIR}"

# --- Strikter Offline-Betrieb zur Laufzeit -----------------------------------
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    MODEL_DIR="/app/models/timesfm-checkpoint" \
    WEB_CONCURRENCY=1 \
    PORT=8000 \
    # TimesFM ist verbindlich: kein Rueckfall auf ein anderes Verfahren.
    FORCE_TIMESFM=true \
    STOCKOUT_TIMESFM_CHECKPOINT=google/timesfm-2.5-200m-pytorch \
    STOCKOUT_TIMESFM_BACKEND=cpu

WORKDIR /app
COPY --chown=sentinel:sentinel src/ ./src/
# Die Streamlit-Oberflaeche teilt sich Image und Modellgewichte mit der API.
# Standard-Entrypoint bleibt die API; die UI wird per abweichendem CMD
# gestartet (siehe README).
COPY --chown=sentinel:sentinel app.py ./app.py

USER sentinel

# 8000: REST-API (Standard) - 8501: Streamlit-Oberflaeche (alternativer CMD)
EXPOSE 8000 8501

# Readiness-Probe gegen den eigenen Health-Endpunkt. 'modell_geladen'
# unterscheidet "Prozess lebt" von "Pflichtmodell einsatzbereit". Da die
# Gewichte lokal liegen, ist die Startphase deutlich kuerzer als bei einem
# Download beim Containerstart.
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD python -c "import os,urllib.request,json,sys; \
r=urllib.request.urlopen(f\"http://127.0.0.1:{os.getenv('PORT','8000')}/health\", timeout=8); \
sys.exit(0 if r.status == 200 and json.load(r).get('modell_geladen') else 1)"

# Cloud-Runtimes (Cloud Run, App Service, Fly.io) geben den Port ueber $PORT vor.
# WEB_CONCURRENCY bleibt bei 1: jeder Worker haelt eine eigene Modellkopie.
CMD ["sh", "-c", "exec uvicorn src.api:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --proxy-headers"]
