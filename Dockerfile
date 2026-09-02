# Multi-Stage Production Dockerfile for CFI Fraud Detection Platform

# ── Stage 1: Build Dependencies ──────────────────────
FROM python:3.12-slim-bookworm AS builder

WORKDIR /build

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY backend/requirements.txt ./
RUN uv venv /opt/venv && \
    . /opt/venv/bin/activate && \
    uv pip install -r requirements.txt

# ── Stage 2: Production Runtime ──────────────────────
FROM python:3.12-slim-bookworm AS runtime

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app/backend" \
    APP_ENV="production" \
    CFI_STORAGE_DIR="/app/storage"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 10001 cfi \
    && useradd -u 10001 -g cfi -s /bin/sh cfi \
    && mkdir -p /app/storage /app/logs /tmp/cfi_storage \
    && chmod -R 777 /app/storage /app/logs /tmp/cfi_storage \
    && chown -R cfi:cfi /app /tmp/cfi_storage

COPY --from=builder /opt/venv /opt/venv
COPY --chown=cfi:cfi backend /app/backend

RUN chmod -R 777 /app/storage /app/logs /tmp/cfi_storage

USER cfi

EXPOSE 7860

HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=5 \
    CMD curl -f http://localhost:7860/health || exit 1

CMD ["gunicorn", "app.main:app", "-w", "1", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:7860"]

