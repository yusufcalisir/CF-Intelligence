#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"
export OTEL_ENABLED=true

# Ensure .env files exist
if [ ! -f .env ]; then
    echo "[.env not found] Creating root .env from .env.example..."
    cp .env.example .env
fi
if [ ! -f backend/.env ]; then
    echo "[backend/.env not found] Creating backend/.env from .env.example..."
    cp .env.example backend/.env
fi

echo "==================================================="
echo "Starting Database and Cache (Docker - postgres & redis)..."
echo "==================================================="
if command -v docker &> /dev/null; then
    docker compose up -d --no-deps postgres redis jaeger prometheus grafana || true
else
    echo "[Info] Docker is not running. Falling back to local in-memory storage."
fi

echo "==================================================="
echo "Checking and Preparing Environments..."
echo "==================================================="

# 1. Backend virtual environment
cd backend
if [ ! -d .venv ]; then
    echo "[.venv not found] Creating Python virtual environment..."
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
else
    echo "[Backend] Virtual environment ready."
    .venv/bin/pip install -r requirements.txt
fi

# 2. Database migrations
echo "[Backend] Running database migrations (Alembic)..."
.venv/bin/python -m alembic upgrade head || echo "[Note] Database offline or using fallback."
cd ..

# 3. Synthetic Benchmark Datasets check
if [ ! -f storage/benchmark_datasets/bank_alpha.parquet ]; then
    echo "[Datasets] Generating synthetic multi-bank benchmark datasets..."
    cd backend
    .venv/bin/python ../scripts/benchmark_prepare_datasets.py
    cd ..
fi

# 4. Frontend check
cd frontend
if [ ! -d node_modules ]; then
    echo "[node_modules not found] Installing frontend dependencies..."
    npm install
else
    echo "[Frontend] node_modules ready."
fi
cd ..

echo "==================================================="
echo "Starting Backend & Frontend..."
echo "==================================================="
export POSTGRES_HOST=localhost
export REDIS_HOST=localhost
export CELERY_BROKER_URL=redis://localhost:6379/0
export CELERY_RESULT_BACKEND=redis://localhost:6379/1

# Start Backend API in background
cd backend
.venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!
cd ..

# Start Frontend dev server in background
cd frontend
npm run dev &
FRONTEND_PID=$!
cd ..

echo "Services launched!"
echo "- Backend API Docs: http://localhost:8000/docs"
echo "- Frontend Console: http://localhost:3000"
echo "Press Ctrl+C to stop services."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true" EXIT
wait
