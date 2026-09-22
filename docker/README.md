# Enterprise Docker Infrastructure & Container Topology

This directory contains production-hardened container manifests, multi-stage Dockerfiles, reverse proxy configurations, cold-start database schemas, and observability provisioning for the **Privacy-Preserving Cross-Bank Fraud Detection Platform (CFI)**.

---

## 1. Directory Structure

```text
docker/
├── Dockerfile.backend                 # Multi-stage hardened Python 3.12 API & FL runtime (non-root UID 1000)
├── Dockerfile.frontend                # Multi-stage Node 20 / Nginx 1.27 Alpine SPA container
├── nginx/
│   ├── nginx.conf                     # Enterprise reverse proxy, WebSocket upgrade map & security headers
│   └── frontend-nginx.conf            # SPA routing fallback, gzip compression & static asset caching
├── postgres/
│   └── 01-init.sql                    # PostgreSQL 16 cold-start DDL, extensions, schemas & consortium seed
├── otel/
│   └── otel-collector-config.yaml     # OpenTelemetry Collector pipeline (OTLP receiver, Prometheus exporter)
├── prometheus/
│   └── prometheus.yml                 # Prometheus 2.50 scrape configurations for API & OTel collector
└── grafana/
    └── provisioning/
        ├── datasources/
        │   └── prometheus.yaml        # Automated Prometheus datasource link
        └── dashboards/
            ├── dashboards.yaml        # Dashboard provider specification
            └── fl_platform_dashboard.json # Pre-configured FL metrics, drift (PSI), latency & alerts
```

---

## 2. Multi-Stage Dockerfiles

### 2.1 Backend API & FL Engine (`Dockerfile.backend`)
- **Stage 1 (Builder)**: `python:3.12-slim-bookworm` utilizing `uv` for reproducible, sub-second dependency installation into `/opt/venv`.
- **Stage 2 (Runtime)**: Minimal slim image with pre-compiled wheels, zero build tools (`gcc`/`git` stripped).
- **Security Posture**: Enforces non-root user `user:user` (UID `1000`, GID `1000`) with strict directory ownership.
- **Ports Exposed**: `8000` (FastAPI HTTP / WebSocket) and `50051` (gRPC Mutual TLS Consortium Coordinator).
- **Healthcheck Probe**: `curl -f http://127.0.0.1:8000/health || exit 1` (`interval=10s`, `timeout=3s`, `retries=3`).

### 2.2 Frontend SPA (`Dockerfile.frontend`)
- **Stage 1 (Builder)**: `node:20-alpine` compiling React 19 + TypeScript bundle via `npm ci` and `npm run build`.
- **Stage 2 (Runtime)**: `nginx:1.27-alpine-slim` hardened web server serving compiled static assets.
- **Routing**: Client-side SPA routing fallback (`try_files $uri $uri/ /index.html;`) with immutable cache headers for `/assets/`.
- **Healthcheck Probe**: `wget -qO- http://127.0.0.1/health || exit 1`.

---

## 3. Nginx Reverse Proxy & Routing Blueprint (`docker/nginx/`)

The outer gateway (`cfi-gateway`) terminates HTTP/HTTPS traffic and routes requests through an internal bridge network (`cfi-network`):

| Route Path | Upstream Target | Protocol / Purpose |
|:---|:---|:---|
| `/api/*` | `http://backend_api` (`cfi-api-server:8000`) | REST API with connection pooling (`keepalive 32`) |
| `/ws/*` | `http://backend_api` (`cfi-api-server:8000`) | Bidirectional WebSockets with `Upgrade` headers & `86400s` timeout |
| `/docs`, `/scalar`, `/openapi.json` | `http://backend_api` (`cfi-api-server:8000`) | Interactive OpenAPI documentation & schema |
| `/health` | `http://backend_api` (`cfi-api-server:8000`) | Liveness and readiness endpoints |
| `/gateway-health` | Local Nginx response | Gateway healthcheck probe (HTTP `200 "cfi_gateway_healthy"`) |
| `/*` (Root Fallback) | `http://frontend_spa` (`cfi-frontend:80`) | Frontend Single Page Application |

### Security Headers Enforced
- `X-Frame-Options: SAMEORIGIN` (Clickjacking defense)
- `X-Content-Type-Options: nosniff` (MIME sniffing prevention)
- `X-XSS-Protection: 1; mode=block`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy: geolocation=(), microphone=(), camera=()`

---

## 4. Cold-Start Database Initialization (`docker/postgres/01-init.sql`)

Mounted to `/docker-entrypoint-initdb.d/01-init.sql:ro` inside PostgreSQL 16:
1. **Extensions**: Installs `uuid-ossp` and `pg_trgm` for trigram similarity fuzzy matching on transaction narratives.
2. **Dedicated Schema**: Isolates consortium tables under schema `cfi_fraud`.
3. **Core Entities**:
   - `cfi_fraud.banks`: Consortium registry (`bank_id`, `name`, `tier`, `jurisdiction`, `connector_type`).
   - `cfi_fraud.simulations`: Federated training run metadata (`simulation_id`, `champion_auc`, `privacy_epsilon`, `aggregation_strategy`).
   - `cfi_fraud.alerts`: Anti-money laundering alerts with JSONB feature payloads.
   - `cfi_fraud.cases`: Four-Eyes investigation cases with SAR narrative drafts.
   - `cfi_fraud.audit_chain`: Append-only cryptographic audit trail with SHA-256 payload hashes and digital signatures.
4. **Idempotent Seeding**: Seeds initial consortium members (`bank_alpha`, `bank_beta`, `bank_gamma`) using `ON CONFLICT (bank_id) DO UPDATE`.

---

## 5. Telemetry & Observability Pipeline

- **OpenTelemetry Collector (`docker/otel/`)**: Listens on gRPC `4317` and HTTP `4318`, applies 1-second batching, and exports metrics to Prometheus (`0.0.0.0:8889`) and traces to logs.
- **Prometheus (`docker/prometheus/`)**: Scrapes `cfi-api-server:8000` (`/metrics`) and `otel-collector:8889` on a 5-second interval.
- **Grafana (`docker/grafana/`)**: Automatically provisions Prometheus datasource and loads the `Cross-Bank FL Intelligence Platform Dashboard` featuring:
  - Federated Learning Loss Convergence (`cfi_fl_round_loss`)
  - Feature Drift Population Stability Index (`cfi_model_drift_psi`)
  - Inference Latency SLA P95/P99 (`cfi_http_request_duration_seconds_bucket`)
  - Active Cross-Bank Fraud Risk Alert Counter (`cfi_active_alerts_total`)

---

## 6. Execution & Docker Compose Profiles

```bash
# Standard Production Stack (Gateway, Frontend, Backend API, PostgreSQL, Redis)
docker compose up -d --build

# With Observability Stack (Adds OTel Collector, Prometheus, Grafana)
docker compose --profile monitoring up -d --build

# With Enterprise Security (Adds HashiCorp Vault KMS)
docker compose --profile enterprise up -d --build

# Full Enterprise Consortium Stack (All services)
docker compose --profile monitoring --profile enterprise --profile storage --profile ml up -d --build
```

---

## 7. Verification

Validate all deployment manifests, Dockerfiles, Nginx directives, healthchecks, and environment parity with the automated suite:

```bash
python scripts/verify_docker_deployment.py
```
