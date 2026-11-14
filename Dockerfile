# IntentQL: the React UI and the API (with the LangGraph agent) in one image.
#
#   docker compose up -d --build        (MySQL and Redis come from docker-compose.yml)
#
# Stage 1 compiles the frontend; stage 2 is the Python app, which serves that build at
# "/" next to the API. The embedding model and the retrieval index are NOT in the image:
# they live in volumes (/app/.models, /app/.index), built on the first start only.

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
    FASTEMBED_CACHE_PATH=/app/.models \
    RAG_STORE=/app/.index/chroma

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

# run as a non-root user. The volume mount points are created here, owned by that user,
# so Docker copies the ownership into new named volumes: .agent_state (checkpoints),
# .models (embedding model), .index (retrieval index)
RUN useradd --create-home --uid 1000 intentql \
    && mkdir -p .agent_state .models .index \
    && chown -R intentql:intentql /app
USER intentql

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

# build the retrieval index if missing or stale (first start only), then the API.
# One worker: the token budget and the in-process caches are per process.
CMD ["sh", "-c", "python scripts/ensure_index.py && exec uvicorn agent.server:app --host 0.0.0.0 --port 8000"]
