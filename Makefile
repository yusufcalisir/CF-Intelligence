.PHONY: help dev test lint docker-up docker-down migrate clean

SHELL := /bin/bash

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ──────────────────────────────────────────────
# Development
# ──────────────────────────────────────────────

dev: ## Start all services in development mode
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

dev-backend: ## Start backend services only
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build api worker postgres redis

dev-frontend: ## Start frontend dev server only
	cd frontend && npm run dev

# ──────────────────────────────────────────────
# Docker
# ──────────────────────────────────────────────

docker-up: ## Start all services (production mode)
	docker compose up --build -d

docker-down: ## Stop all services
	docker compose down

docker-logs: ## Tail logs from all services
	docker compose logs -f

docker-clean: ## Remove all containers, volumes, and images
	docker compose down -v --rmi all

# ──────────────────────────────────────────────
# Backend
# ──────────────────────────────────────────────

test: ## Run backend tests
	cd backend && python -m pytest tests/ -v --cov=app --cov-report=term-missing

test-unit: ## Run unit tests only
	cd backend && python -m pytest tests/unit/ -v

test-integration: ## Run integration tests only
	cd backend && python -m pytest tests/integration/ -v

lint: ## Run linters
	cd backend && ruff check app/ tests/
	cd backend && ruff format --check app/ tests/
	cd backend && mypy app/

lint-fix: ## Auto-fix lint issues
	cd backend && ruff check --fix app/ tests/
	cd backend && ruff format app/ tests/

# ──────────────────────────────────────────────
# Frontend
# ──────────────────────────────────────────────

frontend-install: ## Install frontend dependencies
	cd frontend && npm install

frontend-lint: ## Lint frontend code
	cd frontend && npm run lint

frontend-test: ## Run frontend test suite (Vitest)
	cd frontend && npm test

frontend-build: ## Build frontend for production
	cd frontend && npm run build

frontend-typecheck: ## Run TypeScript type checking
	cd frontend && npx tsc --noEmit

test-all: ## Run all backend and frontend test suites
	python scripts/run_all_tests.py


# ──────────────────────────────────────────────
# Database
# ──────────────────────────────────────────────

migrate: ## Run database migrations
	cd backend && alembic upgrade head

migrate-create: ## Create a new migration (usage: make migrate-create MSG="add users table")
	cd backend && alembic revision --autogenerate -m "$(MSG)"

migrate-rollback: ## Rollback last migration
	cd backend && alembic downgrade -1

# ──────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────

clean: ## Remove build artifacts and caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
