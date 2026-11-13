# IntentQL: the React UI and the API (with the LangGraph agent) in one image.
#
#   docker compose up -d --build        (MySQL and Redis come from docker-compose.yml)
#
# Stage 1 compiles the frontend; stage 2 is the Python app, which serves that build at
# "/" next to the API. The embedding model and the retrieval index are built into the
# image, so a container starts without downloading anything.

# --- 1. frontend --------------------------------------------------------------------
FROM node:22-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


# --- 2. API -------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FASTEMBED_CACHE_PATH=/app/.models

WORKDIR /app

# dependencies first: this layer is reused until requirements.txt changes
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY config.yaml aliases.yaml grammar.md ./
COPY app/ app/
COPY compiler/ compiler/
COPY rag/ rag/
COPY agent/ agent/
COPY scripts/ scripts/
COPY knowledge/ knowledge/
COPY --from=frontend /frontend/dist frontend/dist/

# downloads the embedding model into FASTEMBED_CACHE_PATH and builds knowledge/.chroma;
# the index's fingerprint matches the knowledge/ copied above, so it is never stale here
RUN python scripts/build_index.py

# run as a non-root user; .agent_state holds the LangGraph checkpoints (a volume in compose)
RUN useradd --create-home --uid 1000 intentql \
    && mkdir -p .agent_state \
    && chown -R intentql:intentql /app
USER intentql

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

# one worker: the token budget and the in-process caches are per process
CMD ["uvicorn", "agent.server:app", "--host", "0.0.0.0", "--port", "8000"]
