# 🌐 Enterprise Deployment Guide: From Single-Node On-Premises to Multi-Node Mesh

This comprehensive guide details the production deployment models for the **Collaborative Fraud Intelligence (CFI)** platform:
1. **Part 1: One-Click Enterprise On-Premises Production Stack (`docker-compose.yml`)** — The unified production stack hosting the security gateway, frontend SPA, backend API, PostgreSQL 16, and Redis 7.2 with optional OpenTelemetry, Prometheus, and Grafana monitoring profiles.
2. **Part 2: Multi-Node Network-Isolated Distributed Cluster (`docker-compose.multinode.yml`)** — The multi-container distributed topology enforcing strict subnet isolation across distinct bank institutions.
3. **Part 3: Enterprise Kubernetes & GitOps Helm Mesh (`deployments/helm/cfi-platform`)** — Production Kubernetes microservices deployment with automated NetworkPolicies, HPAs, PodDisruptionBudgets, and ArgoCD synchronization.
4. **Part 4: Post-Deployment Smoke Testing & Automated Verification** — CLI tools and test suites ensuring 100% deployment integrity.

> [!NOTE]
> For cloud infrastructure topologies, Terraform manifests, and disaster recovery failover architectures, refer to [`docs/production_infrastructure.md`](production_infrastructure.md) and [`docs/disaster_recovery_plan.md`](disaster_recovery_plan.md). For interactive API exploration, see [`docs/developer_and_api_portal.md`](developer_and_api_portal.md).

---

# Part 1: One-Click Enterprise On-Premises Stack (`docker-compose.yml`)

## 1.1 Architectural Topology

```
[ Bank Network / Corporate Browser / Core Banking ESB ]
        │
        ▼ (Port 80 / 443)
  ┌─────────────────────────────────────────────────────────────┐
  │ 1. Enterprise Nginx Gateway (`cfi-gateway`)                 │
  │    • Same-Origin Routing: eliminates browser CORS           │
  │    • WebSocket Keepalive: 86400s timeout on /ws/*           │
  │    • Hardened Headers: HSTS, CSP, X-Frame-Options           │
  └──────────────────────┬──────────────────────────────────────┘
                         │
        ┌────────────────┴────────────────┐
        ▼ (/ and /assets/*)               ▼ (/api/* and /ws/*)
  ┌───────────────────────────┐     ┌───────────────────────────┐
  │ 2. Frontend Web Container │     │ 3. Backend API & Engine   │
  │    (`cfi-frontend`)       │     │    (`cfi-api-server`)     │
  │    • Multi-stage Alpine   │     │    • Python 3.12, Uvicorn │
  │    • HTML5 pushstate SPA  │     │    • Non-root user (1000) │
  └───────────────────────────┘     └─────────────┬─────────────┘
                                                  │
                         ┌────────────────────────┴────────────────────────┐
                         ▼ (State / Relational)                            ▼ (Cache / Events)
  ┌───────────────────────────────────────────────┐ ┌───────────────────────────────────────────────┐
  │ 4. PostgreSQL 16 Enterprise Database          │ │ 5. Redis 7.2 Cache & Message Broker           │
  │    (`cfi-postgres`)                           │ │    (`cfi-redis`)                              │
  │    • Idempotent init SQL (01-init.sql)        │ │    • Protected mode auth, AOF persistence     │
  │    • pg_isready healthcheck gating            │ │    • Memory ceiling (512MB volatile-lru)      │
  └───────────────────────────────────────────────┘ └───────────────────────────────────────────────┘
                         │                                                 │
                         └────────────────────────┬────────────────────────┘
                                                  ▼ (Optional --profile monitoring)
  ┌───────────────────────────┐     ┌───────────────────────────┐     ┌───────────────────────────┐
  │ 6. OpenTelemetry Collector│     │ 7. Prometheus v2.50.1     │     │ 8. Grafana OSS 10.3.3     │
  │    (`cfi-otel-collector`) │ ──► │    (`cfi-prometheus`)     │ ──► │    (`cfi-grafana`)        │
  │    • Ports 4317/4318/8889 │     │    • Port 9090            │     │    • Port 3000            │
  └───────────────────────────┘     └───────────────────────────┘     └───────────────────────────┘
```

## 1.2 Step-by-Step Operator Instructions

### Step 1: Generate Cryptographically Secure Production Secrets
Run the platform secrets generator to populate `.env` with 256-bit random tokens:
```bash
python scripts/generate_secrets.py
```
This utility replaces default placeholders in `.env.example` with cryptographically random strings for `SECRET_KEY`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, and `CONSORTIUM_HMAC_SALT`.

### Step 2: Pre-Flight Configuration Verification
Assert zero Compose syntax drift and verify service manifest integrity:
```bash
python scripts/verify_docker_deployment.py
```
The verification script checks:
- Existence of `docker-compose.yml`, `Dockerfile.frontend`, `Dockerfile.backend`, `nginx.conf`, and `01-init.sql`.
- Syntactic validity via `docker compose config`.
- Required environment variable floors.
- Nginx security headers and long-lived WebSocket keepalive directives.

### Step 3: Launch Enterprise Stack
Spin up the 5 core production containers:
```bash
docker compose up -d --build
```

To include the OpenTelemetry, Prometheus, and Grafana monitoring stack:
```bash
docker compose --profile monitoring up -d --build
```

### Step 4: Verify Live Service Health
Check container health statuses:
```bash
docker compose ps
```
All containers (`cfi-gateway`, `cfi-frontend`, `cfi-api-server`, `cfi-postgres`, `cfi-redis`) should transition to `healthy`.

---

# Part 2: Multi-Node Network-Isolated Deployment (`docker-compose.multinode.yml`)

## 2.1 Architecture Overview

```
Bank A Private Subnet (cfi-bank-a-private-net)     Bank B Private Subnet (cfi-bank-b-private-net)
┌────────────────────────────────────────────┐     ┌────────────────────────────────────────────┐
│  cfi-bank-client-a                         │     │  cfi-bank-client-b                         │
│  - Isolated DB Volume (bank-a-data)        │     │  - Isolated DB Volume (bank-b-data)        │
│  - Bank A X.509 Cert (bank-a-pki)          │     │  - Bank B X.509 Cert (bank-b-pki)          │
└─────────────────────┬──────────────────────┘     └─────────────────────┬──────────────────────┘
                      │ consortium-net only                              │ consortium-net only
                      └──────────────────────┐    ┌──────────────────────┘
                                             ▼    ▼
                              ┌───────────────────────────────────┐
                              │  cfi-fl-coordinator               │
                              │  - Central PKI / CA (coord-pki)   │
                              │  - Secure Aggregator (FedAvg/Krum)│
                              │  - gRPC Target: :50051            │
                              │  - REST API Target: :8000         │
                              └───────────────────────────────────┘
```

### Key Isolation Rules
- **No Direct Inter-Bank Routing**: `cfi-bank-client-a` cannot reach `cfi-bank-client-b` directly. `bank-a-net` and `bank-b-net` are marked `internal: true`.
- **Outbound-Only Communication**: Bank client daemons initiate outbound mTLS connections to the central coordinator over `consortium-net` port 50051. Bank nodes expose no inbound listening ports to external entities.
- **Cryptographic Certificate Isolation**: Each participant container uses a dedicated X.509 mTLS certificate and private key stored in an isolated volume.

---

## 2.2 Step-by-Step Deployment Instructions

### Step 1: Provision Per-Node X.509 PKI Certificates
Generate separate mTLS certificate bundles for each node:
```bash
# Provision Coordinator PKI
python scripts/init_vault_pki.py --node-id coordinator --out-dir pki/coordinator

# Provision Bank A PKI
python scripts/init_vault_pki.py --node-id bank-a --out-dir pki/bank-a

# Provision Bank B PKI
python scripts/init_vault_pki.py --node-id bank-b --out-dir pki/bank-b
```

Each directory contains:
- `cert.pem`: Node leaf certificate
- `key.pem`: Node RSA private key
- `ca.pem`: Consortium Root CA certificate

### Step 2: Validate Docker Compose Configuration
Verify multi-node compose configuration syntax:
```bash
docker compose -f docker-compose.multinode.yml config
```

### Step 3: Launch Multi-Node Stack
Start the coordinator and bank client containers:
```bash
docker compose -f docker-compose.multinode.yml up -d --build
```

### Step 4: Verify Container Status & Health
Check container statuses:
```bash
docker compose -f docker-compose.multinode.yml ps
```

Verify coordinator health endpoint:
```bash
curl -f http://localhost:8000/health
```

### Step 5: Verify Network Isolation Boundaries
Confirm that Bank A **cannot** communicate directly with Bank B:
```bash
# Expect failure / unreachable host (proving internal subnet isolation)
docker exec cfi-bank-client-a ping -c 2 cfi-bank-client-b
```

Verify that Bank A **can** reach the central coordinator over gRPC port 50051:
```bash
docker exec cfi-bank-client-a nc -zv coordinator 50051
```

---

# Part 3: Enterprise Kubernetes & GitOps Helm Mesh (`deployments/helm/cfi-platform`)

For enterprise cloud deployments on AWS EKS, GCP GKE, or Azure AKS, the platform provides a hardened Helm chart and ArgoCD GitOps application manifests.

## 3.1 Kubernetes Architecture & Resource Matrix

The Helm chart in [`deployments/helm/cfi-platform`](../deployments/helm/cfi-platform) deploys **16 validated Kubernetes resources**:

| Resource Category | Resource Name | Purpose |
| :--- | :--- | :--- |
| **NetworkPolicy** | `cfi-release-bank-node-isolation` | Restricts bank client pods to outbound-only egress towards coordinator |
| **NetworkPolicy** | `cfi-release-coordinator-ingress` | Gating ingress to coordinator on mTLS port 50051 and HTTP 8080 |
| **NetworkPolicy** | `cfi-release-default-deny-cross-namespace` | Enforces zero-trust cross-namespace tenant isolation |
| **PodDisruptionBudget** | `cfi-release-coordinator-pdb` | Ensures high-availability quorum during cluster node upgrades |
| **PodDisruptionBudget** | `cfi-release-fraud-alert-pdb` | Gating zero-downtime rolling updates for scoring service |
| **Service & Deployment** | `cfi-release-aggregator` | Byzantine-resilient federated model aggregation engine |
| **Service & Deployment** | `cfi-release-bank-node` | Containerized bank training daemon pods |
| **Deployment** | `cfi-release-coordinator` | Central federated coordinator and hyperparameter negotiation |
| **Deployment** | `cfi-release-fraud-alert` | Real-time payment transaction scoring and alert engine |
| **Deployment** | `cfi-release-identity-graph` | Privacy-preserving identity graph and MinHash LSH fuzzy resolver |
| **HPA** | `cfi-release-aggregator-hpa` | Horizontal Pod Autoscaler for aggregation worker pool |
| **HPA** | `cfi-release-coordinator-hpa` | Autoscaling coordinator based on participating bank load |
| **HPA** | `cfi-release-fraud-alert-hpa` | Autoscaling scoring service targeting 70% CPU threshold |
| **Ingress** | `cfi-release-ingress` | TLS termination and host-based routing for API and Web portals |

## 3.2 Kubernetes Dry-Run Manifest Validation

Validate all Helm templates and Kubernetes API contracts before cluster application:
```bash
python scripts/validate_k8s_manifests.py
```
*Output: 16 Kubernetes resources rendered and validated cleanly against Kubernetes API schemas.*

## 3.3 Helm Deployment Execution

```bash
helm upgrade --install cfi-release deployments/helm/cfi-platform \
  --namespace cfi \
  --create-namespace \
  -f deployments/helm/cfi-platform/values.yaml
```

## 3.4 ArgoCD GitOps Continuous Deployment

Deploy via ArgoCD GitOps controller:
```bash
kubectl apply -f deployments/argocd/application.yaml -n argocd
```

---

# Part 4: Post-Deployment Smoke Testing & Diagnostics

Following any production deployment, execute the automated verification suite to validate end-to-end service reachability, SLA compliance, and documentation gateways:

```bash
python scripts/production_smoke_test.py --target-url http://localhost:80
```

The smoke test validates:
- **`GET /health`**: Health status and microservice readiness.
- **`GET /openapi.json`**: OpenAPI 3.1 schema completeness.
- **`GET /scalar`**: Interactive dark-themed Scalar documentation gateway.
- **`GET /docs`**: Standard Swagger UI interface.
- **`GET /api/v1/security/status`**: KMS encryption, mTLS, and ABAC policy state.
- **`POST /api/v1/predict`**: Sub-10ms risk scoring forward pass.
- **`GET /api/v1/diagnostics/connectors`**: Health probes across Kafka, Vault, KMS, Splunk, Redis, and Database.

---

# Part 5: 🧪 Automated Test Suite Parity

The deployment configurations and infrastructure manifests are backed by automated tests:

```bash
python -m pytest \
  backend/tests/unit/test_docker_manifests.py \
  backend/tests/integration/test_multinode_fl_round.py -v
# 6 passed in 0.94s (100% Pass)
```

| Test Suite | Test Count | Scope |
| :--- | :---: | :--- |
| `test_docker_manifests.py` | 3 | Secrets generator entropy, 5 core services Compose structure, Nginx WebSocket & security headers |
| `test_multinode_fl_round.py` | 3 | Multi-node network isolation boundary, per-node mTLS PKI cert generation, daemon config |
| `verify_docker_deployment.py` | CLI | 100% audit pass across Dockerfiles, Compose manifests, env floor, and Nginx reverse proxy |
| `validate_k8s_manifests.py` | CLI | Dry-run schema validation of all 16 Kubernetes Helm resources |
