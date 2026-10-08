# CF-Intelligence Backend Engine

> **Governing Repository Standard:** [`.agents/AGENTS.md`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/.agents/AGENTS.md) ("Never Fabricate Runtime Truth")  
> **Master Technical Certification:** [`CFI-CERT-MASTER-2026-V1`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/audit/correctness/system/technical_certification.md)  
> **Platform Version:** `v2.4.0`  
> **Runtime Stack:** Python 3.12, FastAPI 0.115+, PyTorch 2.4+ (CPU/CUDA), Pydantic v2.9+, SQLAlchemy 2.0+

---

## 1. Executive Summary

The `backend/` directory houses the core operational engine of the **Privacy-Preserving Collaborative Financial Crime Intelligence Platform (CF-Intelligence)**.

The engine coordinates privacy-preserving machine learning and collaborative anti-money laundering (AML) operations across independent banking institutions without sharing raw transaction records. It integrates:
- **Federated Learning (FL)** across non-IID client distributions with differential privacy guarantees.
- **Byzantine-Robust Aggregation** defending global models against adversarial gradient poisoning.
- **Temporal Transaction Graph Intelligence (GNN)** capturing illicit fund flows across multi-hop paths.
- **Dual-Path Explainability** delivering high-speed SLA feature attribution alongside deep SHAP game-theoretic analysis.
- **Zero-Fabrication Runtime Truth**: Guaranteed fail-closed and truthful degradation behavior under missing dependencies, hardware failures, or network partitions.

```
┌────────────────────────────────────────────────────────────────────────────────────────────────┐
│                              BACKEND CLEAN ARCHITECTURE TOPOLOGY                               │
└────────────────────────────────────────────────────────────────────────────────────────────────┘

                          [ External Ingress: REST / WebSocket / gRPC ]
                                                 │
                                                 ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 1. PRESENTATION LAYER (46 Routers, WebSocket Managers, CLI)                                    │
│    • simulations, alerts, cases, banks, graph, coordinator, privacy_defense, gateway, health   │
│    • OpenAPI 3.1 Enriched Specification (/docs, /redoc, /scalar, /openapi.json)                │
└────────────────────────────────────────────────┬───────────────────────────────────────────────┘
                                                 │
                                                 ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 2. APPLICATION LAYER (79 Services & Pipeline Coordinators)                                     │
│    • SimulationService, FLEngine, DirichletPartitioner, CaseService, AlertService              │
│    • ExplainabilityService, FlinkStreaming, AMLCopilot, RegulatoryReporter, FeatureService     │
└────────────────────────────────────────────────┬───────────────────────────────────────────────┘
                                                 │
                                                 ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 3. DOMAIN LAYER (48 Modules: Entities, Value Objects, Mathematical Invariants)                 │
│    • ByzantineDefenses (Krum, Bulyan, Trimmed Mean), AI Act Compliance, SAR Generator          │
│    • Fuzzy PSI, MinHash LSH, QuorumManager, RiskEngine, RealtimeExplainer                      │
└────────────────────────────────────────────────┬───────────────────────────────────────────────┘
                                                 │
                                                 ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 4. INFRASTRUCTURE LAYER (33 Security Modules, Persistence Repositories, Messaging)             │
│    • Security: FHE, TEE Sandbox Driver, HSM Key Service, ZK-SNARK Verifier, RDP Accountant     │
│    • Persistence: RedisStore (RLock fallbacks), PostgreSQL/asyncpg, SQLite, Database Repos     │
│    • Messaging: Kafka Consumers, Celery Workers, EventBus, WebhookDispatcher                   │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Clean Architecture Layer Breakdown

The backend codebase strictly follows the **Ports & Adapters (Clean Architecture)** design pattern:

### 2.1 Presentation Layer (`app/presentation/`)
- **46 API Routers**: Modular FastAPI endpoints covering simulation orchestration, case management, alert workflows, graph queries, regulatory reporting, and identity resolution.
- **Streaming WebSockets**: Real-time telemetry broadcasting training metrics, round convergence, and live AML incident notifications.
- **OpenAPI 3.1 & Interactive Documentation**: Enriched interactive API documentation available at:
  - Swagger UI: `http://localhost:8000/docs`
  - ReDoc: `http://localhost:8000/redoc`
  - Scalar: `http://localhost:8000/scalar`
  - Raw JSON Schema: `http://localhost:8000/openapi.json`
- **CLI Utilities (`app/presentation/cli/`)**: Standalone `cfi-cli` for running batch federated rounds, data validation, and cryptographic key generation.

### 2.2 Application Layer (`app/application/`)
- **Orchestration Services**: `SimulationService`, `FLEngine`, and `FlowerEngine` managing asynchronous training loops across virtual bank nodes.
- **Data Engineering & Lineage**: `DataLoader`, `ETLService`, `DataValidator` (powered by Great Expectations 1.x), and `FLDirichletPartitioner` generating realistic non-IID Dirichlet splits ($\alpha \in [0.1, 10.0]$).
- **Investigation & Case Management**: `CaseService`, `CaseWorkbench`, `BridgeCaseService`, and `IncidentTriage` coordinating analyst reviews with CAS concurrency guarantees.
- **Regulatory Reporting**: `FIURegulatoryService` and `RegulatoryDossierGenerator` producing audit-ready SAR (Suspicious Activity Report) dossiers.

### 2.3 Domain Layer (`app/domain/`)
- **Mathematical Correctness & Algorithms**:
  - Byzantine defenses: Krum, Multi-Krum, Bulyan, Coordinate-wise Trimmed Mean, Coordinate-wise Median.
  - Risk scoring: Composite anomaly utility, threshold calibration, and probability calibration.
  - Privacy protocols: Private Set Intersection (Fuzzy PSI), MinHash Locality-Sensitive Hashing (LSH), and Label Privacy Guards.
- **Regulatory Domain Contracts**: EU AI Act Article 9 (Risk Management System) and Article 14 (Human Oversight) compliance models.
- **Zero Framework Couplings**: Pure Python domain entities and value objects containing zero dependencies on FastAPI, databases, or network drivers.

### 2.4 Infrastructure Layer (`app/infrastructure/`)
- **Advanced Cryptography & Security (33 modules)**:
  - Differential Privacy: Renyi Differential Privacy (`RDPAccountant`), Gaussian noise calibration, and $L_2$ gradient clipping.
  - Trusted Execution Environments: `TEEDriver` with software emulation transparency.
  - Zero-Knowledge & Verification: `ZKSNARKVerifier`, Shamir secret sharing, and immutable cryptographic hash chains.
  - Hardware Security: `HSMKeyService`, `VaultClient`, and PKI certificate binders.
- **Persistence & Caching**:
  - PostgreSQL / SQLite via SQLAlchemy 2.0 async sessions.
  - Redis persistence with synchronized `threading.RLock()` in-memory fallbacks for resilient multi-threaded operation.
- **Asynchronous Messaging**: Kafka streaming handlers, Celery task workers, and Webhook dispatchers.

---

## 3. Core Algorithmic & Security Capabilities

### 3.1 Federated Learning Engine
- **Algorithms Supported**: FedAvg, FedProx (proximal regularization $\mu$), Scaffold (control variate drift correction).
- **Partitioning**: Non-IID Dirichlet distribution ($\alpha$) simulating real-world cross-bank class imbalances and transaction volumes.
- **Convergence Verification**: Real PyTorch forward/backward pass execution on CPU or GPU; zero simulated Gaussian random-walk bypasses.

### 3.2 Byzantine Defenses & Robust Aggregation
- **Defenses**:
  - **Krum & Multi-Krum**: Distance-based selection resistant up to $f < n/3$ Byzantine participants.
  - **Bulyan**: Recursive combination of Multi-Krum and Trimmed Mean eliminating rogue coordinate manipulation.
  - **Coordinate-wise Trimmed Mean & Median**: Extreme outlier elimination robust up to $f < n/2$.
- **Adversarial Injections**: Built-in test harnesses for gradient sign-flipping, Gaussian noise flooding, and targeted label manipulation.

### 3.3 Differential Privacy & Secure Aggregation
- **Privacy Accounting**: Renyi Differential Privacy (RDP) accounting tracking $(\epsilon, \delta)$ privacy expenditure across training rounds.
- **Budget Enforcement**: Training fails closed immediately when privacy budget exceeds configured threshold ($\epsilon > \epsilon_{\max}$).
- **Secure Aggregation**: Peer-to-peer masking drivers and post-quantum cryptographic primitives.

### 3.4 Temporal Transaction Graphs (GNN)
- **Temporal Preservation**: Resolves historical edge leakage defects (`GRAPH-0004`) through strict timestamp-bounded edge filtering ($t_e \le t_{\text{query}}$).
- **Graph Neural Network**: Relational Graph Convolutional Network (RGCN) embeddings capturing structuring, smurfing, and circular flow typologies.

### 3.5 Dual-Path Explainability Architecture
- **Fast-Path Heuristic Attribution**: Sub-10ms SLA attribution decoupled from heavy compute drivers for real-time transaction scoring.
- **Deep Game-Theoretic SHAP**: Off-line / on-demand `KernelExplainer` and `TreeExplainer` producing mathematically authentic Shapley attributions.

---

## 4. Configuration & Production Hardening

All backend configuration is managed through Pydantic Settings in [`app/config.py`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/config.py):

| Variable | Default | Purpose | Production Requirement |
| :--- | :---: | :--- | :--- |
| `APP_ENV` | `development` | Operating environment (`development`, `staging`, `production`) | Set to `production` |
| `APP_DEBUG` | `False` | Debug mode toggle | Must be `False` |
| `API_PORT` | `8000` | HTTP listening port | Configured per container/ingress |
| `SECRET_KEY` | `change_me` | JWT token and session signing key | **Must be customized**; cannot contain placeholder |
| `POSTGRES_PASSWORD` | `change_me` | Database credential | **Must be customized**; cannot contain placeholder |
| `CORS_ALLOWED_ORIGINS`| Localhost URLs | Allowed cross-origin domains | Cannot contain wildcard `*` in production |
| `REDIS_URL` | Optional | Distributed Redis connection string | Ephemeral in-memory fallback if absent |
| `KAFKA_BOOTSTRAP_SERVERS`| Optional | Event broker bootstrap endpoints | Standby mode if absent |

### Production Fail-Fast Validation
At startup, `Settings.validate_production_invariants()` automatically executes. If `APP_ENV=production`, any presence of default credentials, debug flags, or wildcard CORS configurations causes the process to terminate immediately with an explicit error.

### Truthful Fallback Semantics
When external cloud dependencies are absent during local development, the backend activates documented, transparent fallbacks:
- **Redis offline**: Falls back to in-memory `RedisStore` guarded by reentrant locks (`durability: ephemeral`).
- **Kafka offline**: Telemetry stream indicates `stream_source: UNAVAILABLE` without synthetic noise generation.
- **SGX absent**: `TEEDriver` reports `is_hardware_backed: false` with software sandbox execution.

---

## 5. Primary API Routes & Diagnostics

| Route | Method | Description |
| :--- | :---: | :--- |
| `/` | `GET` | Service identification, active version (`2.4.0`), and documentation links. |
| `/health` | `GET` | Liveness and component dependency health status. |
| `/health/ready` | `GET` | Kubernetes readiness probe verifying storage and database availability. |
| `/health/live` | `GET` | Kubernetes liveness probe verifying process responsiveness. |
| `/api/v1/simulations` | `GET`, `POST` | Manage and launch multi-bank federated learning simulations. |
| `/api/v1/training/{id}/rounds` | `GET`, `POST` | Step-by-step training round execution, loss curves, and model checkpoints. |
| `/api/v1/alerts` | `GET`, `POST` | Fraud alert generation, filtering, and composite risk scoring. |
| `/api/v1/alerts/{id}/explain` | `GET`, `POST` | Transaction feature attribution and counterfactual analysis. |
| `/api/v1/cases` | `GET`, `POST` | Case management workbench, investigator assignment, and status state machine. |
| `/api/v1/graph/{entity_id}` | `GET` | Subgraph neighborhood extraction with temporal edge decay. |
| `/api/v1/privacy-defense/audit/mia` | `POST` | Membership Inference Attack (MIA) privacy vulnerability audit. |
| `/api/v1/compliance/sar` | `POST` | Automated Suspicious Activity Report (SAR) XML/JSON generation. |
| `/api/v1/gateway/status` | `GET` | API Gateway routing table, rate limits, and downstream health metrics. |

---

## 6. Local Development & Operational Runbook

### 6.1 Prerequisites
- Python `3.12.x`
- Pip or `uv` package manager
- (Optional) Docker & Docker Compose for PostgreSQL / Redis services

### 6.2 Installation
```powershell
# Navigate to backend directory
cd backend

# Create and activate virtual environment
python -m venv .venv
# Windows:
.\.venv\Scripts\Activate.ps1
# Linux / macOS:
# source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 6.3 Running the Development Server
```powershell
# Start Uvicorn ASGI server with hot-reload
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 6.4 Using the CFI Command-Line Interface
```powershell
# View available CLI commands
python -m app.presentation.cli.cfi_cli --help

# Validate dataset contracts
python -m app.presentation.cli.cfi_cli validate-data --path data/synthetic/

# Run standalone simulation
python -m app.presentation.cli.cfi_cli run-sim --banks 3 --rounds 5
```

### 6.5 Targeted Test Verification Commands
```powershell
# Run API OpenAPI and schema contract tests
pytest tests/contract/test_openapi_contract.py tests/contract/test_endpoints_contract.py -v

# Run runtime truth and zero-fabrication invariant tests
pytest tests/unit/test_runtime_truth_invariants.py -v

# Run end-to-end reality integration flows
pytest tests/integration/test_system_integration_reality.py -v

# Run Byzantine robust aggregation unit tests
pytest tests/benchmarks/test_byzantine_benchmark.py -v
```
