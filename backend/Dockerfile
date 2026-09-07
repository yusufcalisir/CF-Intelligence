# Multi-Stage Production Dockerfile for CFI Backend API & ML Engine
# Stage 1: Build virtual environment with pre-compiled wheels using uv
FROM python:3.12-slim-bookworm AS builder

WORKDIR /build

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY requirements.txt ./
RUN uv venv /opt/venv && \
    . /opt/venv/bin/activate && \
    uv pip install --no-cache -r requirements.txt

# Stage 2: Hardened, non-root production runtime
FROM python:3.12-slim-bookworm AS runtime

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
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1000 user \
    && useradd -u 1000 -g user -m -d /home/user -s /bin/sh user \
    && mkdir -p /home/user /app/storage /app/logs /tmp/cfi_storage \
    && chmod -R 777 /home/user /app /tmp/cfi_storage \
    && chown -R user:user /home/user /app /tmp/cfi_storage

COPY --from=builder /opt/venv /opt/venv
COPY --chown=user:user . /app

RUN chmod -R 777 /home/user /app/storage /app/logs /tmp/cfi_storage

USER user

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
