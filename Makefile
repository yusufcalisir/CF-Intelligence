.PHONY: help dev test lint docker-up docker-down migrate clean benchmark benchmark-fraud benchmark-fl benchmark-dp benchmark-byzantine benchmark-graph benchmark-latency generate-charts benchmark-security

SHELL := /bin/bash

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ──────────────────────────────────────────────
# Development
# ──────────────────────────────────────────────

dev: ## Start all services in development mode
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

dev-backend: ## Start backend services only
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build backend postgres redis

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

kafka-up: ## Start Kafka and Redis streaming services
	docker compose up -d kafka redis

kafka-topics: ## List Kafka event topics
	docker compose exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list

# ──────────────────────────────────────────────
# Backend
# ──────────────────────────────────────────────

test: ## Run backend tests
	cd backend && python -m pytest tests/ -v --cov=app --cov-report=term-missing

test-unit: ## Run unit tests only
	cd backend && python -m pytest tests/unit/ -v

test-integration: ## Run integration tests only
	cd backend && python -m pytest tests/integration/ -v

test-core-banking: ## Run Cloud Core Banking gateway tests (Mambu & Thought Machine)
	cd backend && python -m pytest tests/unit/test_core_banking_gateway.py -v

test-hsm: ## Run Hardware Security Module (HSM) PKCS#11 & Vault Transit tests
	cd backend && python -m pytest tests/unit/test_hsm_key_service.py -v

test-iso20022: ## Run Extended ISO 20022 Financial Rails tests (camt.053, pacs.002, pacs.003)
	cd backend && python -m pytest tests/unit/test_extended_iso20022_parser.py tests/unit/test_financial_message_parser_hardening.py -v

test-regulatory-dossier: ## Run EU AI Act & SR 11-7 Regulatory Dossier generator tests
	cd backend && python -m pytest tests/unit/test_regulatory_dossier_generator.py -v

test-poc-simulator: ## Run Interactive POC Sandbox Replay & Multi-Bank Simulator tests
	cd backend && python -m pytest tests/unit/test_multi_bank_simulator.py -v

lint: ## Run linters (matching CI pipeline)
	cd backend && ruff check app/ tests/
	cd backend && mypy app/ --ignore-missing-imports

lint-fix: ## Auto-fix lint issues
	cd backend && ruff check --fix app/ tests/

format: ## Format backend code with ruff
	cd backend && ruff format app/ tests/

format-check: ## Check formatting with ruff
	cd backend && ruff format --check app/ tests/

# ──────────────────────────────────────────────
# Empirical Benchmarks
# ──────────────────────────────────────────────

benchmark: benchmark-fraud benchmark-fl benchmark-dp benchmark-byzantine benchmark-graph benchmark-latency ## Run complete benchmark suite

benchmark-fraud: ## Run fraud detection benchmark (PaySim / IEEE-CIS)
	python benchmarks/runners/run_fraud_benchmark.py --dataset paysim --rounds 10

benchmark-fl: ## Run federated learning optimization benchmark (Non-IID Dirichlet)
	python benchmarks/runners/run_fl_benchmark.py --rounds 10 --alpha 0.5

benchmark-dp: ## Run differential privacy utility frontier sweep
	python benchmarks/runners/run_dp_tradeoff.py

benchmark-byzantine: ## Run Byzantine resilience benchmark (Sign Inversion)
	python benchmarks/runners/run_byzantine_benchmark.py --attack sign_inversion

benchmark-graph: ## Run GraphSAGE node classification benchmark (Elliptic)
	python benchmarks/runners/run_graph_benchmark.py

benchmark-latency: ## Run inference gateway latency and concurrency stress test
	python benchmarks/runners/run_latency_benchmark.py --workers 50

benchmark-security: ## Run security regression test suite (SSRF, BOLA, Tenant Isolation)
	cd backend && python -m pytest tests/unit/test_perimeter_waf.py tests/unit/test_multi_tenancy.py tests/unit/test_auth_security.py -v

# ──────────────────────────────────────────────
# Frontend
# ──────────────────────────────────────────────

frontend-install: ## Install frontend dependencies
	cd frontend && npm install

frontend-lint: ## Lint frontend code
	cd frontend && npm run lint

frontend-test: ## Run frontend test suite (Vitest)
	cd frontend && npm test

frontend-responsive-test: ## Run multi-device Playwright responsive tests
	cd frontend && npm run test:responsive

frontend-visual-test: ## Run Playwright visual regression tests
	cd frontend && npm run test:visual

frontend-visual-update: ## Update Playwright baseline snapshots
	cd frontend && npm run test:visual:update

frontend-build: ## Build frontend for production
	cd frontend && npm run build

frontend-typecheck: ## Run TypeScript type checking
	cd frontend && npx tsc --noEmit

test-all: ## Run all backend, frontend, visual, and verification test suites
	python scripts/run_all_tests.py --all


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
# Benchmarks & Reproducibility Suite
# ──────────────────────────────────────────────

benchmark: benchmark-fraud benchmark-fl benchmark-dp benchmark-byzantine benchmark-graph benchmark-latency generate-charts ## Run complete reproducible benchmark suite

benchmark-fraud: ## Run PaySim & IEEE-CIS fraud detection benchmarks
	python benchmarks/runners/run_fraud_benchmark.py --dataset paysim --synthetic-eval
	python benchmarks/runners/run_fraud_benchmark.py --dataset ieee_cis --synthetic-eval

benchmark-fl: ## Run Federated Learning (FedAvg, FedProx, SCAFFOLD) benchmark
	python benchmarks/runners/run_fl_benchmark.py --rounds 10 --alpha 0.5

benchmark-dp: ## Run Differential Privacy privacy-utility tradeoff sweep
	python benchmarks/runners/run_dp_tradeoff.py

benchmark-byzantine: ## Run Byzantine resilience benchmark under model poisoning
	python benchmarks/runners/run_byzantine_benchmark.py

benchmark-graph: ## Run GraphSAGE inductive graph intelligence benchmark
	python benchmarks/runners/run_graph_benchmark.py

benchmark-latency: ## Run Inference Gateway concurrency and latency harness
	python benchmarks/runners/run_latency_benchmark.py --mock-load

generate-charts: ## Generate publication-grade figures from raw benchmark JSONs
	python benchmarks/runners/generate_charts.py

benchmark-security: ## Run comprehensive security regression test suite
	python -m pytest backend/tests/unit/test_perimeter_waf.py backend/tests/unit/test_multi_tenancy.py -v

# ──────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────

clean: ## Remove build artifacts and caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
