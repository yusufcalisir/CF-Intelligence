# Multi-Stage Production Dockerfile for CFI Backend API & ML Engine
# Stage 1: Build virtual environment with pre-compiled wheels using uv
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS builder

WORKDIR /build

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    libpq-dev \
    && apt-get --only-upgrade install -y apt=2.6.2 libapt-pkg6.0=2.6.2 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.13.7

COPY requirements.txt ./
RUN uv venv /opt/venv && \
    . /opt/venv/bin/activate && \
    uv pip install --no-cache -r requirements.txt && \
    rm -rf /opt/venv/lib/python*/site-packages/ray/jars && \
    test ! -e /opt/venv/lib/python3.12/site-packages/ray/jars

# Stage 2: Hardened, non-root production runtime
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS runtime

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app" \
    APP_ENV="production" \
    HOME="/home/user" \
    CFI_STORAGE_DIR="/app/storage"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq5 \
    && apt-get --only-upgrade install -y apt=2.6.2 libapt-pkg6.0=2.6.2 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1000 user \
    && useradd -u 1000 -g user -m -d /home/user -s /bin/sh user \
    && mkdir -p /home/user /app/storage /app/logs /tmp/cfi_storage \
    && chown -R user:user /home/user /app/storage /app/logs /tmp/cfi_storage \
    && chmod 0750 /home/user /app/storage /app/logs /tmp/cfi_storage

COPY --from=builder /opt/venv /opt/venv
RUN chown -R root:root /opt/venv && chmod -R go-w /opt/venv
COPY --chown=root:root . /app

# Enforce least-privilege: read-only application code (dirs 0755, files 0644),
# writable permissions strictly restricted to isolated storage and logging directories
RUN find /app -type d -exec chmod 0755 {} + \
    && find /app -type f -exec chmod 0644 {} + \
    && find /app/scripts -type f -name "*.sh" -exec chmod 0755 {} + 2>/dev/null || true \
    && find /app/scripts -type f -name "*.py" -exec chmod 0755 {} + 2>/dev/null || true \
    && chown -R user:user /app/storage /app/logs /tmp/cfi_storage \
    && chmod -R 0750 /app/storage /app/logs /tmp/cfi_storage

USER user

EXPOSE 8000 50051

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
