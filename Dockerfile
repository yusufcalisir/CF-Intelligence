# Multi-Stage Production Dockerfile for CFI Fraud Detection Platform

# ── Stage 1: Build Dependencies ──────────────────────
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS builder

WORKDIR /build

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && apt-get --only-upgrade install -y apt=2.6.2 libapt-pkg6.0=2.6.2 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.13.7

COPY backend/requirements.txt ./
RUN uv venv /opt/venv && \
    . /opt/venv/bin/activate && \
    uv pip install --no-cache -r requirements.txt && \
    rm -rf /opt/venv/lib/python*/site-packages/ray/jars && \
    find /opt/venv -name "bom.cdx.json" -delete && \
    test ! -e /opt/venv/lib/python3.12/site-packages/ray/jars

# ── Stage 2: Production Runtime ──────────────────────
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS runtime

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app/backend" \
    APP_ENV="development" \
    APP_DEBUG="false" \
    HOME="/home/user" \
    CFI_STORAGE_DIR="/app/storage"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && apt-get --only-upgrade install -y apt=2.6.2 libapt-pkg6.0=2.6.2 \
    && rm -rf /var/lib/apt/lists/* \
    && /usr/local/bin/python3 -m pip install --no-cache-dir --upgrade pip==26.3 \
    && find /usr/local -name "bom.cdx.json" -delete \
    && groupadd -g 1000 user \
    && useradd -u 1000 -g user -m -d /home/user -s /bin/sh user \
    && mkdir -p /home/user /app/storage /app/logs /tmp/cfi_storage \
    && chown -R user:user /home/user /app/storage /app/logs /tmp/cfi_storage \
    && chmod 0750 /home/user /app/storage /app/logs /tmp/cfi_storage

COPY --from=builder /opt/venv /opt/venv
RUN chown -R root:root /opt/venv && chmod -R go-w /opt/venv
COPY --chown=root:root backend /app/backend

RUN find /app/backend -type d -exec chmod 0755 {} + \
    && find /app/backend -type f -exec chmod 0644 {} + \
    && find /app/backend/scripts -type f -name "*.sh" -exec chmod 0755 {} + 2>/dev/null || true \
    && find /app/backend/scripts -type f -name "*.py" -exec chmod 0755 {} + 2>/dev/null || true \
    && chown -R user:user /home/user /app/storage /app/logs /tmp/cfi_storage \
    && chmod -R 0750 /home/user /app/storage /app/logs /tmp/cfi_storage

USER user

EXPOSE 7860

HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=5 \
    CMD curl -f http://localhost:7860/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]


