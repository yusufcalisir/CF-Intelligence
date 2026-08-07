# Clean Architecture & System Design

## 1. Architectural Overview

The Collaborative Fraud Intelligence (CFI) Simulator is designed around **Clean Architecture** (Ports & Adapters / Hexagonal Architecture) principles. It isolates core domain logic from framework, presentation, database, and telemetry concerns. Dependencies flow strictly **inward** toward the Domain layer.

The platform operates in two deployment modes dynamically configured by the `microservice_mode` configuration setting:
1.  **Monolithic Mode:** Services run inside a single FastAPI control plane, communicating via direct in-process function calls.
2.  **Microservices Mode:** The system is decomposed into 4 independent, stateless processes orchestrated via Docker Compose:
    *   **`gateway`:** API entry point implementing rate-limiting, authentication, logging, and down-stream routing.
    *   **`fl-coordinator`:** Manages the federated learning loops, client drops, secure aggregation, and Ray/Flower integrations.
    *   **`identity-graph`:** Performs deterministic HMAC-SHA256 entity resolution and builds React Flow-compatible network maps.
    *   **`fraud-alert`:** Executes the 9-Signal Risk Scoring Engine and handles case management flows.

```
       [ Presentation Layer ] (FastAPI Routers, WebSockets, React UI)
                 │
                 ▼
       [ Application Layer ] (Services, Orchestrators, Use Cases)
                 │
        ┌────────┴────────┐
        ▼                 ▼
  [ Domain Layer ]  [ Infrastructure Layer ] (ORM, Redis, Celery, Telemetry)
```

---

## 2. Layer Responsibilities

### 2.1 Domain Layer (`backend/app/domain/`)
The core domain model, written in pure Python. It contains business definitions, structures, and value objects. It remains completely independent of FastAPI, Pydantic, SQLAlchemy, or PyTorch.

*   `enums.py`: Enumerations for FL Engine types, Privacy mechanisms, Client status, and Bank tiers.
*   `entities.py` & `entities_phase2.py`: Entities with active identity (`Bank`, `SimulationRun`, `TrainingRound`, `Alert`, `Case`, `ResolvedEntity`).
*   `value_objects.py` & `value_objects_phase2.py`: Immutable data structures (`ModelWeights`, `EvaluationMetrics`, `RiskSignal`, `GraphNode`, `GraphEdge`).

### 2.2 Application Layer (`backend/app/application/`)
Contains business logic orchestration. Defines ports (interfaces) for data access, which are implemented by the infrastructure layer.

*   `services/fl_engine.py`: Implements parameter aggregation (FedAvg, coordinate median, Krum) and secure aggregation masking.
*   `services/model_service.py`: Lifecycle of the PyTorch MLP model (training, CPU/GPU evaluation, Integrated Gradients attributions).
*   `services/privacy_service.py`: Bounded L2 gradient clipping and Gaussian mechanism noise addition.
*   `services/risk_engine.py`: Combines 9 independent heuristic and ML signals into a single score ($0 \text{ to } 1000$).
*   `services/entity_resolution.py`: Computes deterministic one-way HMAC-SHA256 privacy hashes for account and device identifiers.
*   `services/explainability_service.py`: Computes SHAP attributions using `shap.KernelExplainer` with analytical fallbacks.
*   `services/model_registry.py`: Manifest-backed model repository managing versioning, active symlinks, and Canary Gates.

### 2.3 Infrastructure Layer (`backend/app/infrastructure/`)
Concrete implementation of dependencies. Adapts foreign libraries and databases.

*   `database.py` & `models.py`: SQLAlchemy 2.0 async engine and relational database tables.
*   `redis_store.py`: A fault-tolerant state manager. It synchronizes simulation configurations and round metrics to Redis. If Redis is unreachable, it falls back to a thread-safe, in-memory cache to maintain liveness.
*   `connectors/`: Production Bank Connector sub-system implementing Hexagonal Ports & Adapters:
    *   `base_connector.py`: Defines the `BaseBankConnector` abstract interface and unified `NormalizedTransaction` Pydantic domain schema.
    *   `streaming_connector.py`: High-throughput real-time payment event streaming connector for Kafka, RabbitMQ, and Redis streams.
    *   `iso20022_connector.py`: Financial message parser converting ISO 20022 MX (`pacs.008`, `pacs.009`) XML and SWIFT MT103/MT202 text records into normalized transactions.
    *   `batch_connector.py`: End-Of-Day (EOD) file batch parser for CSV and Parquet transaction dumps.
    *   `rest_connector.py`: HTTP REST adapter supporting mTLS, OAuth2, HMAC payload signing, and real-time webhook ingestion.
    *   `factory.py`: Configuration-driven `BankConnectorFactory` resolving per-bank connector implementations.
*   `security/smart_contract_driver.py`: Web3 & CBDC settlement driver executing automated token disbursements (`wCBDC`, `USDC`, `e-TRY`) on `ConsortiumIncentiveSettlement.sol` based on LOO Shapley values.
*   `grpc/`: High-Performance Bidirectional Streaming gRPC Transport Layer over HTTP/2 defined via `fl_service.proto` (`RegisterClient`, `Heartbeat` streaming, `StreamModelParameters` chunking, `DownloadGlobalModel` chunking).
*   `telemetry.py`: Bypasses metrics or mounts a `/metrics` ASGI app for Prometheus based on configurations.
*   `celery_app.py`: Background worker queue for handling long-running PyTorch training loops without blocking FastAPI.

### 2.4 Presentation Layer (`backend/app/presentation/`)
Interactions with clients.
*   `routers/*.py`: FastAPI REST API endpoints verifying request formats via Pydantic schemas.
*   `websockets/*.py`: Persistent WebSocket connections sending training round progress and scenario replay events.

---

## 3. Data Flow & Mechanics

### 3.1 Federated Training Cycle
```
[React UI] ──(Start)──► [Gateway] ──► [Simulation Tasks (Celery)]
                                             │
   ┌─────────────────────────────────────────┘
   ▼
[fl-coordinator]
   ├── 1. Generate Non-IID bank datasets
   ├── 2. For round r = 1..R:
   │     ├── Apply client availability (dropout probability)
   │     ├── Client local SGD training (ModelService.train_local)
   │     ├── If DP: PrivacyService.clip_model_update + add_noise_to_weights
   │     ├── If SecAgg: apply_secure_aggregation_masks
   │     └── Aggregate parameters (FedAvg, Median, or Krum)
   ├── 3. Evaluate candidate model on holdout validation data
   └── 4. Promote candidate if AUC >= Active AUC - 0.005 (Canary Gate)
```

### 3.2 Real-Time Collaborative AML Screening
```
[Transaction Event] ──► [fraud-alert Service]
                                │
                                ▼
                    [Risk Scoring Engine] (9 Signals)
                                │
                                ▼ (If Score >= 600)
                     [Alert Generated] ──(HMAC-SHA256)──► [identity-graph]
                                                                │
                                                                ▼
                                                      [Entity Resolution]
                                                                │
                                                                ▼
                                                        [React Flow Map]
### 3.3 High-Performance Bidirectional gRPC Transport Layer
```
[Bank Node Client] ──(HTTP/2 Channel)──► [gRPC Server (50051)] ──► [FederatedLearningServicer]
        │                                                                   │
        ├── 1. RegisterClient(bank_id, cert_fp) ──────────────────────────► session_token & cluster_id
        ├── 2. Heartbeat(stream ClientHeartbeat) ◄──(Bidirectional)───────► stream CoordinatorStatus
        ├── 3. StreamModelParameters(stream ParameterChunk) ──────────────► Reassemble & Validate Payload
        └── 4. DownloadGlobalModel(ModelDownloadRequest) ◄──(Server Stream)── stream ModelChunk (SHA-256)
```

The gRPC transport layer handles high-throughput, low-latency node communications using Protocol Buffers (`cfi.fl.v1.FederatedLearningService`):
1. **Node Registration (`RegisterClient`):** Validates bank certificate fingerprints and returns a session token and assigned cluster ID.
2. **Bidirectional Heartbeat (`Heartbeat`):** Streams client telemetry (CPU, memory, dataset size) while receiving coordinator state commands (`IDLE`, `START_TRAINING`, `CANCEL_ROUND`, `UPDATE_CONFIG`).
3. **Client-Streaming Parameters (`StreamModelParameters`):** Transmits encrypted model updates split into 1 KB binary chunks signed with digital signatures.
4. **Server-Streaming Global Model (`DownloadGlobalModel`):** Delivers aggregated global model weights in SHA-256 checksum-verified binary chunks.

- **Empirical Fault Tolerance Benchmark (`NetworkResilienceEvaluator`)**: Evaluates operational continuity under network dropouts and straggler latency delays:
  - **Scenario A (Straggler Latency)**: 2 stragglers delayed by 250s $\rightarrow$ 3 fast nodes submit in 11.8s, reaching 60% dynamic quorum and triggering auto-aggregation without waiting for stragglers.
  - **Scenario B (Abrupt Node Disconnect)**: 1 node disconnects (40% packet drop) $\rightarrow$ 4 active nodes reach 80% quorum in 14.2s.
  - **FedAsync Staleness Attenuation**: $S(\tau) = (1 + \tau)^{-\alpha}$ preserves $F_1 = 93.2\%$. Zero deadlocks.

### 3.5 Automated Optuna FL Hyperparameter Optimizer & Dirichlet Partitioner (`fl_hyperparameter_optimizer.py` & `fl_dirichlet_partitioner.py`)
- **Non-IID Dirichlet Partitioner ($\text{Dir}(\alpha)$):** Simulates cross-bank label skew ($\alpha \in [0.01, 10.0]$).
- **Optuna Bayesian TPE Engine:** Optimizes learning rates, local epochs, DP clip norms $C_{\text{max}}$, noise multipliers $\sigma$, staleness decay $\gamma$, and FedProx $\mu$ using `TPESampler` and early `MedianPruner`.
- **Management API Endpoints:** Exposes `POST /v1/admin/optimization/tune` and `GET /v1/admin/optimization/studies/{study_name}`.

---

### 3.4 Berlin Group NextGenPSD2 & Open Banking Data Connector Mapping

```
NextGenPSD2 REST Endpoint ──► OpenBankingConnector ──► OAuth2 / mTLS Headers ──► NormalizedTransaction
  (/v1/accounts/{id}/txs)            │
                                     ├── booked[]   ──► IBAN / Amount / Currency / MCC / BookingDate
                                     └── pending[]  ──► IBAN / Amount / Currency / MCC / ValueDate
```

The [`OpenBankingConnector`](file:///backend/app/infrastructure/connectors/open_banking_connector.py) and [`ISO20022MessagingConnector`](file:///backend/app/infrastructure/connectors/iso20022_connector.py) map European Berlin Group NextGenPSD2 REST and SWIFT ISO 20022 XML payloads into `NormalizedTransaction` objects:
- **Authentication & Headers**: Executes OAuth 2.0 Client Credentials Grant with token TTL expiration tracking and injects eIDAS QWAC/QSeal `X-Request-ID`, `Digest` (SHA-256), `PSU-IP-Address`, and `TPP-Signature` headers.
- **ISO 20022 XML & SWIFT Parsing**:
  - `pacs.008.001.08` (Customer Credit Transfer) $\rightarrow$ `NormalizedTransaction` (`IntrBkSttlmAmt`, `DbtrAcct`, `CdtrAcct`).
  - `camt.053.001.08` (Bank Statement) $\rightarrow$ `NormalizedTransaction[]` list (`Stmt/Ntry` array extraction).
  - `pacs.002.001.10` (Payment Status Report) $\rightarrow$ `NormalizedTransaction` (`TxSts`, `OrgnlPmtInfId`).
  - SWIFT MT103 $\rightarrow$ `NormalizedTransaction` (`:20:`, `:32A:`, `:50K:`, `:59:` parsing).

### 3.5 Enterprise Zero-Mock Architecture Policy

The platform enforces a strict Zero-Mock Policy in production:
- All legacy mock generators (`data_generator.py`) and mock connectors (`mock_connector.py`, `mq_skeleton_connector.py`) are deprecated and rejected by [`BankConnectorFactory`](file:///backend/app/infrastructure/connectors/factory.py).
- Requesting `connector_type="mock"` or `"mq_skeleton"` raises an explicit `ValueError`.
- Production connector types (`open_banking`, `psd2`, `parquet`, `rabbitmq`, `kafka`, `iso20022`, `rest`) consume real institutional data streams without synthetic fallbacks.

### 3.8 Redis Online Feature Store & Feast Integration (`redis_store.py`, `feast_store.py`)
- **Redis Online Feature Store (`RedisFeatureStore`)**: Implements low-latency (<5ms) online feature serving with Redis connection pooling (`max_connections=20`), pipeline batch execution (`batch_set_features`, `batch_get_features`), TTL key expiration (`EXPIRE 86400`), and automatic memory cache fallback for standalone execution.
- **Feast Feature Store Adapter (`FeastFeatureStoreAdapter`)**: Adapts online Redis feature view serving (`get_online_features`, `push_online_features`) and point-in-time historical feature vector joins (`get_historical_features`) for federated fraud detection model training and real-time inference.

---








## 4. Analytical Drift Detection Suite

We implement dynamic, multi-pair statistical checks inside `presentation/routers/banks.py`:

1.  **Jensen-Shannon (JS) Divergence:** Measures categorical and continuous feature probability divergence:
    $$D_{\text{JS}}(P \parallel Q) = \frac{1}{2} D_{\text{KL}}(P \parallel M) + \frac{1}{2} D_{\text{KL}}(Q \parallel M)$$
    where $M = \frac{1}{2}(P + Q)$, using base-2 logarithm to bound outcomes in $[0, 1]$.
2.  **Population Stability Index (PSI):** Measures feature shifts across dynamic decile bins:
    $$\text{PSI} = \sum_{b=1}^{B} (A_b - E_b) \times \ln\left(\frac{A_b}{E_b}\right)$$
3.  **Concept Drift:** Evaluates $P(Y \mid X)$ stability by training a logistic regression model on Bank A and calculating the prediction shift on Bank B, paired with segment-specific conditional JS drifts.

### 4.1 Asynchronous Background Retraining Pipeline
Model retraining is decoupled from manual tick loops into an automated, asynchronous Celery worker task (`execute_automated_retraining_task` in `backend/app/tasks/simulation_tasks.py`):
1. **Trigger Criteria (`RetrainingTriggerEngine`)**:
   - **Data Ingestion Threshold**: Triggers when new normalized transactions reach target batch volume ($\ge 50,000$ records).
   - **Drift Detection Trigger**: Triggers when Population Stability Index ($\text{PSI} > 0.20$) or Kolmogorov-Smirnov test ($p < 0.05$) indicates significant feature/concept drift.
   - **Scheduled Consortium Cadence**: Periodic cron trigger for scheduled federated rounds.
2. **Worker Execution Pipeline**:
   - Local Celery worker fetches normalized training batch from `StreamingFeatureStore`.
   - Executes PyTorch model training loop with Opacus Differential Privacy (DP-SGD) noise injection (`add_noise_to_weights`).
   - Evaluates candidate model accuracy and ROC-AUC quality gate ($\text{ROC-AUC} > 0.70$).
   - Compresses encrypted parameter update payload (Zstandard) and queues it for gRPC transmission to central coordinator.

---

## 5. Cryptographic Parameter Exchange Pipeline

The parameter exchange pipeline (`backend/app/infrastructure/security/secure_parameter_pipeline.py`) enforces a 7-step cryptographic transmission sequence to protect local model updates against gradient inversion attacks, model poisoning, and network interception:

1. **Gradient Calculation ($\Delta w$)**: Computes parameter deltas relative to current global model weights.
2. **Gradient Sparsification & Compression (`compression_engine.py`)**: Applies Top-K sparsification (retaining top K% highest magnitude gradient elements) and Zstandard/zlib lossless payload compression to reduce bandwidth footprint.
3. **Differential Privacy Injection (`privacy_service.py`)**: Inject calibrated Gaussian noise via Opacus DP-SGD ($\epsilon, \delta$) with clipping bounds.
4. **Cryptographic Masking (`fhe_driver.py` / SecAgg)**: Applies pairwise zero-sum SecAgg masks or FHE CKKS ciphertext vectors.
5. **Digital Envelope Signing (`signature_verifier.py`)**: Signs compressed payload using 4096-bit RSA-PSS or Ed25519 private keys (`DigitalEnvelopeSigner`).
6. **gRPC Delivery over mTLS 1.3 (`fl_service.proto`)**: Streams signed parameter chunks over outbound mutual TLS 1.3 channels to central coordinator.
7. **Signature Verification & Byzantine Aggregation (`signature_verifier.py`)**: Central coordinator verifies digital signature against Vault PKI public keys (`SignatureVerifier`), rejects tampered payloads, applies Byzantine defense (Krum / Coordinate-wise Median), and aggregates global model weights.

---

## 6. High-Availability & Asynchronous Fault Tolerance

Prevents training round deadlocks caused by bank node network outages, maintenance windows, or latency spikes.

### 6.1 Asynchronous Federated Aggregation (`async_fl_engine.py`)

The `AsyncFLEngine` implements the **FedAsync** parameter update protocol. Fast bank nodes submit model updates immediately — without blocking on straggler nodes — using a staleness attenuation factor to preserve convergence quality.

**Staleness Attenuation Function:**

$$S(\tau) = (1 + \tau)^{-\alpha}$$

where $\tau = t_{\text{current}} - t_{\text{submitted}}$ (rounds elapsed since submission) and $\alpha$ is the attenuation exponent (default: $\alpha = 0.5$).

**Global Weight Update Rule:**

$$W^{(t+1)} = (1 - \alpha_\tau)\,W^{(t)} + \alpha_\tau\,W_i^{(t-\tau)}$$

where $\alpha_\tau = \eta \cdot S(\tau)$ is the learning rate weighted by staleness attenuation. Fresh updates ($\tau = 0$) receive full learning rate weight ($S(0) = 1.0$); older stale updates are progressively down-weighted.

### 6.2 Dynamic Quorum Timeout Manager (`quorum_manager.py`)

The `DynamicQuorumManager` monitors real-time round submission progress across all registered bank nodes. It automatically triggers round aggregation as soon as the minimum quorum threshold is satisfied — without waiting for the target window to expire.

**Quorum States:**

| State | Condition |
| :--- | :--- |
| `WAITING` | Submitted nodes / Registered nodes < 60% and elapsed < 300s |
| `QUORUM_REACHED` | Submitted nodes / Registered nodes ≥ 60% |
| `TIMEOUT_EXPIRED` | Elapsed time ≥ 300s before quorum threshold reached |

**Auto-Aggregation Protocol:**
1. Bank nodes register for the round via `register_nodes(node_ids)`.
2. Each gradient/weight submission is recorded via `record_node_submission(node_id)`.
3. After each submission, `evaluate_quorum_status()` checks: $\frac{|\text{submitted}|}{|\text{registered}|} \ge 0.60$.
4. If `QUORUM_REACHED` → immediate aggregation trigger (no timeout wait).
5. If `TIMEOUT_EXPIRED` → graceful fallback with partial aggregation from submitted nodes.

---

## 7. Spectral Anomaly Detection & Backdoor Poisoning Defense (`spectral_defense.py`)

### 7.1 Threat Model

Targeted backdoor attacks attempt to inject a **low-rank gradient perturbation** into the federated aggregation process — malicious bank nodes submit parameter updates that are indistinguishable from legitimate updates in L2 norm, but align along a shared stealthy subspace designed to bypass fraud detection for specific money mule accounts.

### 7.2 SVD Spectral Projection Algorithm

The `SpectralAnomalyDetector` applies Singular Value Decomposition to the stacked gradient matrix $G \in \mathbb{R}^{K \times d}$ (K clients, d parameters) before aggregation:

**Step 1 — Stack gradient matrix:**
$$G = \begin{bmatrix} \Delta w_1^T \\ \Delta w_2^T \\ \vdots \\ \Delta w_K^T \end{bmatrix}$$

**Step 2 — Compute dominant right singular vector via power iteration:**
$$G = U \Sigma V^T \quad \Rightarrow \quad v_1 = \arg\max_{‖v‖=1} ‖Gv‖$$

**Step 3 — Compute per-client spectral projection scores:**
$$s_i = |\langle \Delta w_i, v_1 \rangle|^2$$

Backdoor-poisoned updates inject a dominant low-rank perturbation that results in disproportionately large $s_i$ values, separating malicious clients from the honest majority.

**Step 4 — Threshold detection:**
$$\theta = \mu_s + \tau \cdot \sigma_s$$
$$\text{quarantine}(i) \iff s_i > \theta$$

where $\tau$ = `spectral_threshold_multiplier` (default: 1.5), $\mu_s$ = mean score, $\sigma_s$ = standard deviation.

### 7.3 Robust Spectral Aggregation

After quarantining poisoned nodes, the `aggregate_robust_spectral()` method computes a clean parameter average over the honest subset $\mathcal{H} = \{i : s_i \le \theta\}$:

$$w^{(t+1)}_{\text{global}} = \frac{1}{|\mathcal{H}|} \sum_{i \in \mathcal{H}} \Delta w_i$$

### 7.4 Implementation Details

| Component | Module | Purpose |
| :--- | :--- | :--- |
| `SpectralDefenseConfig` | `spectral_defense.py` | Config: threshold multiplier τ, min_clients |
| `SpectralAnomalyReport` | `spectral_defense.py` | Per-client spectral score & quarantine status |
| `SpectralAnomalyDetector` | `spectral_defense.py` | SVD power iteration + anomaly detection + robust aggregation |
| `_power_iteration()` | `spectral_defense.py` | Pure-stdlib dominant right singular vector $v_1$ computation |

> **Pure stdlib implementation**: The spectral defense module uses only Python stdlib (`math`, `dataclasses`) — no numpy or scipy dependency required — maintaining clean domain layer isolation.

---

## 8. Multi-Node Network-Isolated Deployment Model (`docker-compose.multinode.yml`)

### 8.1 Network Isolation Architecture

The platform supports a multi-container deployment model where each participating bank (`bank-a`, `bank-b`) runs inside an isolated network namespace.

```
Bank A Subnet (bank-a-net)                  Bank B Subnet (bank-b-net)
┌──────────────────────────────┐            ┌──────────────────────────────┐
│  cfi-bank-client-a           │            │  cfi-bank-client-b           │
│  - Isolated Storage Vault    │            │  - Isolated Storage Vault    │
│  - Dedicated mTLS X.509 Cert │            │  - Dedicated mTLS X.509 Cert │
└────────────┬─────────────────┘            └──────────────┬───────────────┘
             │ consortium-net only                          │ consortium-net only
             └─────────────────────┐  ┌────────────────────┘
                                   ▼  ▼
                    ┌──────────────────────────────┐
                    │  cfi-fl-coordinator          │
                    │  - Central PKI / CA Engine    │
                    │  - Secure Aggregator          │
                    │  - gRPC Server (:50051)       │
                    └──────────────────────────────┘
```

- **Subnet Separation**: Bank internal subnets (`bank-a-net`, `bank-b-net`) are configured with `internal: true`. Direct inter-bank container communication is blocked at the bridge interface level.
- **Outbound-Only mTLS**: Bank client daemons initiate outbound-only mTLS 1.3 connections to the coordinator on `consortium-net:50051`.
- **Mode Dispatch**: The application dispatches service roles dynamically via the `MODE` environment variable (`MODE=coordinator` vs `MODE=bank_client`).

### 8.3 Enterprise Multi-Tenant Database Persistence Engine (`database.py`)

The platform implements multi-tenant database isolation (SOC2/PCI-DSS compliant) where each bank node operates against its own isolated database instance or schema:
- **AsyncEngine Connection Pooling**: Production PostgreSQL / CockroachDB AsyncEngine configured via `_make_engine_kwargs(tenant)` with `pool_size=20`, `max_overflow=10`, `pool_recycle=3600`, and `pool_pre_ping=True`.
- **Serializable Isolation & Retry Loop**: `run_cockroach_transaction()` handles SQLSTATE `40001` transaction conflicts with exponential retry loops.
- **Alembic Schema Migrations**: Managed via [`alembic.ini`](file:///backend/alembic.ini), [`env.py`](file:///backend/app/infrastructure/database/migrations/env.py), and [`migration_manager.py`](file:///backend/app/infrastructure/database/migration_manager.py) for programmatic `upgrade_head()` / `downgrade_revision()` auto-migrations and tracking versioned revision scripts (`001_production_domain_tables`) across multi-tenant bank schemas.

### 8.2 gRPC Transport Protocol (`fl_service.proto`)

The inter-container parameter exchange, heartbeat liveness, and global model distribution operate over streaming gRPC RPC handlers:

| RPC Handler | Type | Description |
| :--- | :--- | :--- |
| `RegisterClient` | Unary | Validates X.509 certificate fingerprint against consortium CA, returns `session_token` and `cluster_id`. |
| `Heartbeat` | Bidirectional Stream | Streams node telemetry (`cpu`, `memory`, `dataset_size`), yields `CoordinatorStatus` commands (`START_TRAINING`, `IDLE`). |
| `StreamModelParameters` | Client Streaming | Chunks encrypted weight payload, attaches digital signature per chunk, passes reassembled weights to `FLEngine`. |
| `DownloadGlobalModel` | Server Streaming | Streams aggregated global model binary chunks with per-chunk SHA-256 checksum integrity verification. |

> **Security Guarantee**: Unregistered or certificate-revoked bank nodes are rejected at the `RegisterClient` handler before any parameter stream is accepted.


---

## 9. Technology Stack & Directory Structure

```
├── docker-compose.multinode.yml  # Multi-node network-isolated container orchestration
├── backend/
│   ├── app/
│   │   ├── domain/               # Domain Value Objects & Entities
│   │   ├── application/          # Service Layer (Business Logic)
│   │   ├── infrastructure/       # Database, Redis, Celery, Telemetry
│   │   └── presentation/         # API Controllers & WebSocket handlers
│   └── tests/                    # Unit, Integration, & Property-Based suites
├── frontend/
│   ├── src/
│   │   ├── api/                  # REST client and Query hooks
│   │   ├── components/           # Reusable Layouts, Dashboards, Charts
│   │   └── pages/                # Views (Dashboard, Alerts, Registry, Graph)
│   └── package.json
└── docs/                         # Security, System Design & Deployment documentation
```


---

## 3.9 Kubernetes & Helm 3 Infrastructure

The CFI Platform ships production Kubernetes workloads managed through a Helm 3 chart located in `deployments/helm/cfi-platform/`.

### 3.9.1 Chart Structure

| File | Purpose |
|---|---|
| `Chart.yaml` | Chart metadata, semver versioning, bitnami dependency declarations |
| `values.yaml` | Environment-agnostic defaults; override per-env via `-f values-prod.yaml` |
| `templates/aggregator-deployment.yaml` | Central FL Aggregator — 2 replicas, gRPC + HTTP ports, HPA-enabled |
| `templates/bank-node-deployment.yaml` | Bank Client Nodes — HSM secret mounts, conditional PKCS#11 env injection |
| `templates/service.yaml` | ClusterIP services for both aggregator and bank-node |
| `templates/ingress.yaml` | NGINX Ingress with TLS termination and gRPC backend annotation |
| `templates/hpa-and-netpol.yaml` | HPA (`autoscaling/v2`) + Zero-Trust NetworkPolicy |

### 3.9.2 Security Model

- **Secrets**: All credentials injected via `secretKeyRef` references to pre-created Kubernetes Secrets (never in `values.yaml` plaintext).
- **Zero-Trust NetworkPolicy**: Bank nodes may only receive ingress from the aggregator pod and may only egress to the aggregator gRPC port and DNS (port 53/UDP). Inter-bank direct communication is blocked at the kernel netfilter level.
- **HSM Integration**: Bank nodes mount PKCS#11 credentials as a read-only Secret volume when `bankNode.hsm.enabled=true`.
- **Read-Only Root Filesystem**: All containers enforce `readOnlyRootFilesystem: true` with a writable `/tmp` `emptyDir` volume.
- **Non-Root Containers**: `runAsNonRoot: true`, `runAsUser: 1000`, `fsGroup: 2000` applied to all pods.
- **Dropped Capabilities**: `capabilities.drop: [ALL]` on every container security context.

### 3.9.3 Horizontal Pod Autoscaling

The Aggregator HPA scales between `minReplicas: 2` and `maxReplicas: 10` on CPU utilization target of 70%, providing elastic capacity during FL round coordination spikes.


### 3.9.4 Deployment Command

```bash
helm dependency update deployments/helm/cfi-platform
helm upgrade --install cfi-platform deployments/helm/cfi-platform \
  --namespace cfi-prod \
  --create-namespace \
  -f deployments/helm/cfi-platform/values.yaml \
  --wait --timeout 10m
```

---

## 3.10 Infrastructure as Code (Terraform Multi-Cloud)

Modular Terraform (>= 1.6.0) templates in `deployments/terraform/` provision all cloud infrastructure as immutable, version-controlled code across three enterprise cloud providers.

### 3.10.1 Module Structure

| Cloud | Directory | Managed Resources |
|---|---|---|
| **AWS** | `deployments/terraform/aws/` | VPC (private/public subnets, NAT GW), EKS (1.30), Managed Node Group, AWS KMS (AES-256, auto-rotate), Security Groups (gRPC mTLS isolation) |
| **Azure** | `deployments/terraform/azure/` | Resource Group, VNet + NSG (Deny inter-bank gRPC rule), AKS (1.30, Calico policy), Bank Node Pool (autoscaling 2-10), Azure Key Vault (Premium SKU, purge protection) |
| **GCP** | `deployments/terraform/gcp/` | VPC Network + Private Subnet, Cloud Router/NAT, GKE (1.30, private cluster, Workload Identity), Node Pool (autoscaling 2-10, Shielded VMs), Cloud KMS KeyRing + CryptoKey (90-day rotation), Deny inter-bank Firewall rule |

### 3.10.2 Security Posture

- **KMS Envelope Encryption**: All Kubernetes etcd secrets are encrypted at rest via provider-managed KMS CMEK (AWS KMS, Azure Key Vault, Cloud KMS).
- **Private Cluster Endpoints**: No public API server endpoints; EKS (`endpoint_public_access=false`), AKS (`private_cluster_config`), GKE (`enable_private_nodes=true`).
- **Zero-Trust Firewall**: Explicit Deny rules block direct bank-node-to-bank-node gRPC across all three providers; FL traffic must traverse the aggregator.
- **No Hardcoded Secrets**: All sensitive values use Terraform variables or data sources — verified by the automated test suite.
- **Key Rotation**: AWS KMS (`enable_key_rotation=true`), GCP Cloud KMS (`rotation_period=7776000s`/90 days), Azure Key Vault (`soft_delete_retention_days=90`, `purge_protection_enabled=true`).

### 3.10.3 Deployment Commands

```bash
# AWS
cd deployments/terraform/aws
terraform init && terraform plan -out=tfplan
terraform apply tfplan

# Azure
cd deployments/terraform/azure
terraform init && terraform plan -out=tfplan
terraform apply tfplan

# GCP
cd deployments/terraform/gcp
terraform init -var="gcp_project_id=YOUR_PROJECT_ID"
terraform plan -var="gcp_project_id=YOUR_PROJECT_ID" -out=tfplan
terraform apply tfplan
```

---

## 3.11 Production Telemetry, Observability & Alerting

The CFI Platform ships a full enterprise observability stack covering distributed tracing, Prometheus metric exposition, Grafana dashboards, and automated alerting rules.

### 3.11.1 Telemetry Module (`backend/app/infrastructure/telemetry/`)

The `TelemetryRegistry` singleton exposes the following CFI-specific Prometheus metrics:

| Metric | Type | Description |
|---|---|---|
| `cfi_fl_round_duration_seconds` | Summary | FL training round execution duration (seconds) |
| `cfi_fl_round_participants` | Gauge | Active participating bank nodes per round |
| `cfi_dp_epsilon_consumed_total` | Counter | Cumulative DP epsilon budget consumed per bank |
| `cfi_spectral_anomalies_detected_total` | Counter | Byzantine/poisoning spectral anomalies detected |
| `cfi_grpc_request_duration_seconds` | Summary | gRPC endpoint latency per method |
| `cfi_hsm_signing_duration_seconds` | Summary | HSM PKCS#11 digital signing operation latency |
| `cfi_node_heartbeat_timestamp` | Gauge | Unix timestamp of last bank node heartbeat |

Convenience decorators `@track_grpc_latency(method)` and `@track_fl_round` instrument handlers automatically. `get_prometheus_metrics_bytes()` renders the standard Prometheus exposition text format for `/metrics` HTTP responses.

### 3.11.2 Grafana Dashboards (`deployments/grafana/dashboards/`)

| Dashboard | UID | Key Panels |
|---|---|---|
| `fl_consortium_overview.json` | `cfi-fl-consortium-overview` | FL round count, active quorum, avg round duration, gRPC p50/p99 latency, node heartbeat age |
| `privacy_security_audit.json` | `cfi-privacy-security-audit` | Per-bank DP epsilon consumption, spectral anomaly total count, HSM signing avg latency |

### 3.11.3 Prometheus Alert Rules (`deployments/prometheus/alert_rules.yml`)

| Alert | Condition | Severity |
|---|---|---|
| `DPBudgetExhaustionWarning` | `cfi_dp_epsilon_consumed_total > 9.0` | warning |
| `BankNodeOffline` | `time() - cfi_node_heartbeat_timestamp > 60s` | critical |
| `SpectralAnomalySpike` | `>3 anomalies in 5m` | critical |
| `GRPCSLABreach` | `p99 gRPC latency > 500ms over 5m` | warning |

---

## 3.12 EU AI Act Compliance Certificate Export Engine

The CFI Platform includes an automated compliance engine (`backend/app/domain/ai_act_compliance.py`) and CLI tool (`scripts/export_compliance_report.py`) that evaluate FL global model deployments against **Regulation (EU) 2024/1689 (EU AI Act)** high-risk AI system requirements (Articles 10–15).

### 3.12.1 Article Coverage & Assessment Criteria

| Article | Requirement | Assessment Evidence & Thresholds |
|---|---|---|
| **Article 10** | Data Governance & Management | ISO 13616 IBAN validation pass rate $\ge 99.9\%$, Differential Privacy $\epsilon \le 10.0$ ceiling, FL training round count $\ge 1$ |
| **Article 11** | Technical Documentation | Hyperparameters SHA-256 hash traceability, model versioning, federated topology documentation |
| **Article 12** | Record-Keeping | Append-only FL round audit log SHA-256 digest, 7-year audit retention policy |
| **Article 13** | Transparency to Users | SHAP feature attribution explainability, Annex III high-risk AI classification disclosure |
| **Article 14** | Human Oversight | Dual sign-off gate approval (ML Engineer + Compliance Officer per SR 11-7), automated AUC rollback |
| **Article 15** | Accuracy & Cybersecurity | AUC $\ge 0.75$, F1 $\ge 0.70$, spectral anomaly defense count, gRPC mTLS 1.3 + HSM PKCS#11 |

### 3.12.2 Cryptographic Signing & Fingerprint Verification

Certificates are serialized as canonical deterministic JSON (sorted keys) and signed using **HMAC-SHA256**:

$$\text{Signature} = \text{HMAC-SHA256}(K_{\text{signing}}, \text{JSON}_{\text{canonical}})$$

The full signed certificate string is hashed with SHA-256 to produce an immutable **Certificate Fingerprint** (`cert_hash`), which is logged to audit stores for non-repudiation. Verification is performed via constant-time digest comparison (`hmac.compare_digest`).

### 3.12.3 CLI Export Tool (`scripts/export_compliance_report.py`)

Compliance officers generate audit binders using the CLI tool:

```bash
python scripts/export_compliance_report.py \
  --model-version v2.1.0 \
  --fl-rounds 25 \
  --dp-epsilon 2.3 \
  --dual-signoff \
  --model-auc 0.93 \
  --model-f1 0.88 \
  --audit-log-sha256 <SHA256_HEX> \
  --hyperparams-sha256 <SHA256_HEX> \
  --output-dir certs/ \
  --format json,markdown
```

Outputs machine-readable JSON certificates (`.json`) and human-readable Markdown compliance binders (`.md`).

---

## 3.13 Automated Model Lineage & Registry Vault

The `ModelRegistryVault` (`backend/app/domain/model_governance.py`) manages model checkpoint lifecycles, cryptographic lineage binding, and dual-gated promotion to production.

### 3.13.1 Checkpoint Lifecycle States (`ModelStatus`)

```
 [ FL Training ] ──> DRAFT / CANDIDATE ──(Dual Sign-Off + HSM Sig)──> PRODUCTION
                                                                         │
                                                             (New Prod)  ▼
                                                                     ARCHIVED
                                                                         │
                                                             (Rollback)  ▼
                                                                    ROLLED_BACK
```

| State | Description |
|---|---|
| `DRAFT` | Checkpoint registered during local/intermediate FL training rounds |
| `CANDIDATE` | Evaluated FL global model ready for dual sign-off and HSM signing |
| `PRODUCTION` | Active champion model serving live fraud prediction traffic |
| `ARCHIVED` | Superceded production model preserved for compliance and rollback |
| `ROLLED_BACK` | Model demoted due to live telemetry degradation or anomaly trigger |

### 3.13.2 Promotion Gating & Signature Envelope

Before promotion to `PRODUCTION`, two mandatory gating criteria are enforced:

1. **Dual Sign-Off Gate**: Both `ml_engineer` and `compliance_officer` roles must have approved the checkpoint (SR 11-7 model risk policy).
2. **HSM Signature Envelope Verification**: The signature is verified over the canonical payload:
   $$\text{Payload} = \text{model\_id} : \text{version} : \text{weights\_sha256} : \text{hyperparams\_sha256} : \text{dataset\_hash} : \text{dp\_epsilon}$$

If either verification fails, `promote_to_production()` raises `ModelGovernanceError` or `InvalidSignatureError`.

### 3.13.3 Zero-Downtime Rollback Engine (`rollback_production`)

If live telemetry breaches safety thresholds (e.g. ROC-AUC $< 0.65$ or p99 latency $> 200\text{ms}$):
1. The current `PRODUCTION` checkpoint is immediately demoted to `ROLLED_BACK`.
2. The most recent `ARCHIVED` checkpoint is automatically promoted back to `PRODUCTION`.

---

## 3.14 Enterprise Role-Based Access Control (RBAC) & OAuth 2.0 / OIDC Gateway

The API Gateway (`backend/app/presentation/routers/gateway.py`) acts as the single security perimeter enforcing OpenID Connect (OIDC) Bearer JWT authentication, dynamic Attribute-Based Access Control (ABAC), and immutable audit logging.

### 3.14.1 Authentication Pipeline (`authenticate_request`)

```
 HTTP Request ──> Header: Authorization: Bearer <JWT>
                        │
                        ▼
             [ OIDCAuthenticator ] ──(Invalid / Expired)──> 401 Unauthorized
                        │
                        ▼ (Valid UserClaims: sub, username, bank_id, roles)
             [ Legacy API Key Fallback ]
```

1. **OIDC Bearer JWT**: Decodes standard claims (`sub`, `iss`, `aud`, `exp`) and custom banking claims (`bank_id`, `roles`, `clearance_level`, `shift_hours`, `approval_tier`). If invalid or expired, request is rejected with `401 Unauthorized`.
2. **API Key Fallback**: Retains legacy `X-API-Key` map for backward compatibility with automated external integrations.

### 3.14.2 Multi-Tenant ABAC & Audit Chain Integration (`check_authorization`)

| Policy Rule | Condition | Enforced Action |
|---|---|---|
| **Super-Admin Bypass** | `role in ("super_admin", "compliance_auditor")` | Bypasses tenant isolation restrictions |
| **Tenant Isolation** | `user.bank_id != resource.bank_id` | Blocks cross-bank data access (`403 Forbidden`) |
| **Shift Hours Window** | `current_hour not in shift_hours` | Blocks access outside employee shift |
| **IP Subnet Restriction** | `client_ip not in allowed_ip_subnets` | Rejects non-whitelisted IP addresses |
| **Audit Logging** | Every ABAC denial or unauthorized request | Appends event to `ImmutableAuditChain` |

---

## 3.15 Live High-Throughput Payment Stream Benchmark

The enterprise benchmark framework (`scripts/run_enterprise_stress_test.py`) validates platform throughput capacity and latency under sustained ISO 20022 payment transaction loads.

### 3.15.1 ISO 20022 pacs.008 Payload Generator (`PaymentTransactionGenerator`)

Generates structurally complete `FIToFICstmrCdtTrf` payment payloads:
- **`GrpHdr`**: MsgId, CreDtTm, NbOfTxs, SttlmInf, InstgAgt/InstdAgt BICs
- **`CdtTrfTxInf`**: Payment IDs (InstrId, EndToEndId, TxId, UETR), IntrbkSttlmAmt, IBAN sender/receiver accounts, purpose code
- **`_cfi_meta`**: Extended FL fraud detection attributes (risk_score, cross_border flag, payload_sha256)

Payload uniqueness is guaranteed by UUID4 transaction IDs; amounts span $10.00–$2,000,000.00 across SWIFT/SEPA/FedWire/CHAPS channels.

### 3.15.2 Concurrent Worker Architecture (`EnterpriseStressTestRunner`)

```
 StressTestRunner
     │
     ├── Worker [bank_a] ──┐
     ├── Worker [bank_b] ──┤
     ├── Worker [bank_c] ──┼──> asyncio.gather() ──> Aggregated Metrics
     ├── Worker [bank_d] ──┤
     └── Worker [bank_e] ──┘
```

Each asyncio worker continuously dispatches `batch_size` transactions per tick, collecting per-tx latency samples. Aggregate metrics: **peak TPS**, **p50/p99 latency**, **error rate**.

### 3.15.3 Benchmark Report Output

| Output | Format | Description |
|---|---|---|
| `benchmark_<TIMESTAMP>.json` | JSON | Machine-readable raw metrics |
| `benchmark_<TIMESTAMP>.md` | Markdown | Human-readable report with tables and conformance verdict |
| Template | `docs/enterprise_benchmark_report.md` | Reference template with run instructions |

---

## 3.16 Continuous Security & Vulnerability Audit Pipeline

The automated enterprise security CI/CD workflow ([`.github/workflows/enterprise_security_ci.yml`](file:///.github/workflows/enterprise_security_ci.yml)) executes multi-layer security auditing across source code, third-party dependencies, container images, infrastructure templates, and testing suites on every push/PR and nightly schedule (`0 2 * * *`).

### 3.16.1 Security Audit Jobs & Tools

```
 GitHub Actions Event (Push / PR / Nightly Cron)
     │
     ├── 1. sast-static-analysis (Ruff, Mypy, Bandit SAST)
     ├── 2. dependency-security-audit (pip-audit against PyPA vulnerability DB)
     ├── 3. trivy-container-security (Trivy scanner for OS/library CVEs)
     ├── 4. helm-and-terraform-security-audit (Helm lint + AWS/Azure/GCP terraform validate)
     └── 5. pytest-security-and-compliance-suites (Full unit, security & EU AI Act test suite)
```

| Security Job | Technology / Tool | Security Scope |
|---|---|---|
| **SAST Analysis** | `Ruff`, `Mypy`, `Bandit` | Code formatting, type safety, SQL injection, hardcoded secrets, insecure crypto |
| **Dependency Audit** | `pip-audit` | PyPI third-party package CVE vulnerability checks |
| **Container Scan** | `aquasecurity/trivy-action` | Base OS image & installed library CVE scanning (`CRITICAL`, `HIGH`) |
| **IaC Security** | `Helm`, `Terraform` | Helm chart linting & AWS/Azure/GCP multi-cloud template validation |
| **Compliance Suites** | `Pytest` | EU AI Act, Differential Privacy, Spectral Defense, HSM Signing unit/integration suites |





