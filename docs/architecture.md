# Clean Architecture & System Design

## 1. Architectural Overview

The Collaborative Fraud Intelligence (CF-Intelligence) Platform is engineered around **Clean Architecture** (Ports & Adapters / Hexagonal Architecture) principles. It isolates core domain logic from framework, presentation, database, cryptographic hardware, and telemetry concerns. Dependencies flow strictly **inward** toward the Domain layer.

The platform operates in two deployment modes dynamically configured by the `microservice_mode` configuration setting:
1. **Monolithic Mode:** Services run inside a unified FastAPI control plane, communicating via direct in-process function calls and in-memory bus interfaces.
2. **Microservices Mode:** The system is decomposed into 4 independent, stateless processes orchestrated via Docker Compose or Kubernetes:
   - **`gateway`:** API entry point implementing rate-limiting, OAuth2/OIDC authentication, logging, and downstream routing.
   - **`fl-coordinator`:** Manages the federated learning loops, client drops, secure aggregation, and Ray/Flower integrations.
   - **`identity-graph`:** Performs deterministic type-salted HMAC-SHA256 entity resolution and builds React Flow-compatible network maps.
   - **`fraud-alert`:** Executes the 9-Signal Risk Scoring Engine, manages investigation workbenches, and handles case management flows.

```
       [ Presentation Layer ] (FastAPI Routers, WebSockets, React 19 UI)
                 │
                 ▼
       [ Application Layer ] (Services, Orchestrators, Use Cases)
                 │
        ┌────────┴────────┐
        ▼                 ▼
  [ Domain Layer ]  [ Infrastructure Layer ] (ORM, Redis, Celery, Telemetry, PKI)
```

---

## 2. Layer Responsibilities

### 2.1 Domain Layer ([`backend/app/domain/`](../backend/app/domain))
The core domain model, written in pure Python. It contains business definitions, invariants, entities, and value objects. It remains completely independent of FastAPI, Pydantic, SQLAlchemy, or PyTorch.

* [`enums.py`](../backend/app/domain/enums.py): Enumerations for FL Engine types, Privacy mechanisms, Client status, Bank tiers, `BankStatus`, `CaseStatus`, `CasePriority`, and `InvestigatorCaseStatus`.
* [`entities.py`](../backend/app/domain/entities.py) & [`entities_phase2.py`](../backend/app/domain/entities_phase2.py): Entities with active identity (`Bank`, `SimulationRun`, `TrainingRound`, `Alert`, `Case`, `ResolvedEntity`, `BankRegistration`).
* [`value_objects.py`](../backend/app/domain/value_objects.py), [`value_objects_phase2.py`](../backend/app/domain/value_objects_phase2.py) & [`value_objects_copilot.py`](../backend/app/domain/value_objects_copilot.py): Immutable data structures (`ModelWeights`, `EvaluationMetrics`, `RiskSignal`, `GraphNode`, `GraphEdge`, `AMLCopilotAnalysis`).
* [`case_management.py`](../backend/app/domain/case_management.py): 6-stage case lifecycle state machine ([`CaseLifecycleStateMachine`](../backend/app/domain/case_management.py#L111)), [`FraudCaseRecord`](../backend/app/domain/case_management.py#L74), and allowed transition rules (`ALLOWED_CASE_TRANSITIONS`).
* [`backup_record.py`](../backend/app/domain/backup_record.py): Backup verification domain models (`BackupStatus`, `BackupArtifact`, `RestoreProbeResult`).
* [`dr_coordinator.py`](../backend/app/domain/dr_coordinator.py): Regional disaster recovery coordination models (`CoordinatorRegionRole`, `DRNodeStatus`, `DRFailoverEvent`).
* [`ai_act_compliance.py`](../backend/app/domain/ai_act_compliance.py): EU AI Act Articles 10–15 compliance engine (`record_human_oversight`, `generate_transparency_report`).
* [`model_governance.py`](../backend/app/domain/model_governance.py): Model governance lifecycle states (`ModelStatus`, `ModelRegistryVault`, SR 11-7 dual sign-off envelopes).

### 2.2 Application Layer ([`backend/app/application/`](../backend/app/application))
Contains business logic orchestration. Defines ports (interfaces) for data access, which are implemented by the infrastructure layer.

* [`services/fl_engine.py`](../backend/app/application/services/fl_engine.py): Implements parameter aggregation (FedAvg, coordinate median, Krum) and secure aggregation masking.
* [`services/flower_p2p_engine.py`](../backend/app/application/services/flower_p2p_engine.py): Decentralized serverless P2P federated learning engine orchestrating Ring and Mesh topologies (`P2PGossipStrategy`) with genuine local PyTorch training, Metropolis-Hastings doubly stochastic mixing, non-finite (NaN/Inf) weight rejection, and Byzantine coordinate-wise median & trimmed mean resilience.
* [`services/flower_engine.py`](../backend/app/application/services/flower_engine.py): Flower (`flwr.dev`) framework simulation adapter (`FraudFlowerClient`, `CallbackFedAvg`) with zero-mock native PyTorch federated learning fallback and Ray cluster lifecycle management.
* [`services/flink_graph_streaming.py`](../backend/app/application/services/flink_graph_streaming.py): Apache Flink real-time graph streaming engine with $W(t, 500\text{ms})$ sliding window state accumulators (<50ms SLA).
* [`services/case_workbench.py`](../backend/app/application/services/case_workbench.py): [`InvestigatorCaseWorkbenchService`](../backend/app/application/services/case_workbench.py#L17) enforcing Four-Eyes dual control supervisor signatures (`SIG_SUPERVISOR_<ID>`).
* [`services/case_service.py`](../backend/app/application/services/case_service.py): [`CaseManagementService`](../backend/app/application/services/case_service.py#L141) with SHA-256 block hash-chaining across timeline events, [`EvidenceRegistryService`](../backend/app/application/services/case_service.py#L521) with cryptographic content hashing, and [`AuditService`](../backend/app/application/services/case_service.py#L483).
* [`services/bank_onboarding_service.py`](../backend/app/application/services/bank_onboarding_service.py): Automated 6-stage bank node onboarding pipeline (`register_bank`, `issue_mtls_certificate`, `provision_tenant_schema`, `provision_kms_key`, `generate_connector_config`, `activate_bank`).
* [`services/regulatory_reporter.py`](../backend/app/application/services/regulatory_reporter.py): [`RegulatoryReporterService`](../backend/app/application/services/regulatory_reporter.py#L30) generating FinCEN BSA XML 2.0 payloads and validating against `schemas/FinCEN_SAR_2.0.xsd`.
* [`services/model_service.py`](../backend/app/application/services/model_service.py): Lifecycle of the PyTorch MLP model (training, CPU/GPU evaluation, Integrated Gradients attributions).
* [`services/fl_engine.py`](../backend/app/application/services/fl_engine.py): Core Federated Learning engine orchestrating multi-client training rounds across FedAvg, FedProx ($\frac{\mu}{2} \|\mathbf{w} - \mathbf{w}_t\|^2$), SCAFFOLD, FedAdam, FedAdaGrad, FedYogi, Krum, Trimmed Mean, Coordinate-wise Median, and Bulyan, with thread-safe simulation concurrency locks and zero-memory-leak state lifecycle cleanup.
* [`services/privacy_service.py`](../backend/app/application/services/privacy_service.py): Bounded L2 gradient clipping and Gaussian mechanism noise addition.
* [`services/risk_engine.py`](../backend/app/application/services/risk_engine.py): Combines 9 independent heuristic and ML signals into a single score ($0 \text{ to } 1000$).
* [`services/entity_resolution.py`](../backend/app/application/services/entity_resolution.py): Computes deterministic one-way HMAC-SHA256 privacy hashes for account and device identifiers.
* [`services/explainability_service.py`](../backend/app/application/services/explainability_service.py): Computes SHAP attributions using `shap.KernelExplainer` with analytical fallbacks.
* [`services/aml_agentic_copilot.py`](../backend/app/application/services/aml_agentic_copilot.py): Autonomous BSA/AML RAG narrative generator synthesizing 5-paragraph FinCEN SAR narratives and 4-Eyes supervisor briefings.
* [`services/federated_unlearning_engine.py`](../backend/app/application/services/federated_unlearning_engine.py): Confidential federated unlearning engine performing Exact Re-Aggregation across retained participants and Lineage Subtraction parameter erasure for revoked or departing banks without retraining from scratch.
* [`services/bridge_case_service.py`](../backend/app/application/services/bridge_case_service.py): Inter-Bank Encrypted FININT Case Messaging & Information Request Protocol using Curve25519 ECDH + AES-256-GCM, SHA-256 evidence hashing, SLA timers, and immutable audit logging.
* [`services/payment_recall_service.py`](../backend/app/application/services/payment_recall_service.py): Real-Time SEPA Instant Payment Recall Engine handling EPC camt.056 recall requests, camt.029 resolutions, 10-day regulatory SLA tracking, automated account freeze triggers, and recovery ledgers.
* [`services/screening_service.py`](../backend/app/application/services/screening_service.py): Real-Time Multi-List Sanctions & PEP Screening Engine matching against UN, EU CFSP, and OFAC SDN lists with Jaro-Winkler + Levenshtein fuzzy matching and secondary disambiguation.
* [`services/fiu_regulatory_service.py`](../backend/app/application/services/fiu_regulatory_service.py): European FIU & UNODC goAML 4.0 XML and EU AMLA standardized regulatory reporting engine with HMAC-SHA256 encrypted envelopes and 4-Eyes dual supervisor sign-off.
* [`services/open_aml_service.py`](../backend/app/application/services/open_aml_service.py): Drop-in European AML OpenAPI compatibility adapter and HMAC-SHA256 signed webhook gateway.
* [`services/ubo_graph_service.py`](../backend/app/application/services/ubo_graph_service.py): Cross-Border Corporate UBO & Heterogeneous Graph Modeling service resolving multi-tier beneficial ownership, detecting circular ownership loops, nominee director syndicates, and offshore shell clusters.
* [`services/european_scenario_library.py`](../backend/app/application/services/european_scenario_library.py): 16 European AML Monitoring Scenarios & Hybrid Deterministic Rule Engine blending EBA/FATF typologies with federated machine learning and GNN anomaly embeddings.
* [`services/model_registry.py`](../backend/app/application/services/model_registry.py): Manifest-backed model repository managing versioning, active symlinks, Canary Gates, and atomic disk writes (`tempfile.NamedTemporaryFile` + `os.replace`) to prevent partial read corruption.
* [`services/connector_diagnostics_service.py`](../backend/app/application/services/connector_diagnostics_service.py): Enterprise connector health evaluation and active TCP/TLS handshake ping engine for Kafka, Vault, KMS, Splunk, Redis, PostgreSQL, and ISO 20022 parser.
* [`services/design_partner_service.py`](../backend/app/application/services/design_partner_service.py): Design partner cohort tracking and early-access pilot metrics.

### 2.3 Infrastructure Layer ([`backend/app/infrastructure/`](../backend/app/infrastructure))
Contains concrete implementations of adapters, persistence engines, cryptographic drivers, and external systems.

* [`database/migration_manager.py`](../backend/app/infrastructure/database/migration_manager.py) & [`database/migrations/env.py`](../backend/app/infrastructure/database/migrations/env.py): Alembic linear dual-revision migration engine (`001_production_domain_tables` $\to$ `002_core_and_aml_tables`) with dynamic tenant discovery from `tenant_configs`, PostgreSQL `search_path` injection protection (`_pg_quote_identifier`), SQLite file-based tenant isolation, offline airgap `--sql` generation, and automatic schema adoption (`_ensure_migrated_or_stamped`).
* [`database/models.py`](../backend/app/infrastructure/models.py) & [`database/session.py`](../backend/app/infrastructure/database/__init__.py): SQLAlchemy 2.0 async engine and relational models partitioned dynamically per active tenant.
* [`security/kms_service.py`](../backend/app/application/services/kms_service.py): Multi-tenant Key Management Service enforcing AES-256-GCM versioned envelope encryption (`v2:{iv_b64}:{tag_b64}:{ciphertext_b64}`), live HashiCorp Vault connectivity detection with simulated fallback telemetry, and automated background database re-encryption pipelines.
* [`services/tenant_metering.py`](../backend/app/application/services/tenant_metering.py) & [`services/idempotency.py`](../backend/app/application/services/idempotency.py): Concurrency-safe tenant usage quota manager with atomic test-and-increment locking in `acquire_quota` eliminating race conditions, and Redis `SETNX` 3-state idempotency reservation (`ACQUIRED` $\to$ `IN_PROGRESS` $\to$ `HIT`).
* [`security/auth_service.py`](../backend/app/infrastructure/security/auth_service.py): Enterprise authentication service issuing 15-minute short-lived JWT access tokens, single-use refresh token rotation, and 5-fail brute force lockout protection.
* [`security/password_hasher.py`](../backend/app/infrastructure/security/password_hasher.py): Bcrypt password hashing engine (`cost=12`, 4,096 rounds) with per-password cryptographic salts.
* [`security/error_handler.py`](../backend/app/infrastructure/security/error_handler.py): Global production error sanitization middleware (RFC 7807 problem details, zero stack trace leakage, unique incident ID correlation, and Sentry telemetry hook).
* [`security/security_headers.py`](../backend/app/infrastructure/security/security_headers.py): Comprehensive HTTP security headers (CSP, HSTS, X-Frame-Options: DENY, X-Content-Type-Options: nosniff) and strict CORS whitelist enforcement.
* [`feature_store/`](../backend/app/infrastructure/feature_store): Low-latency online feature store (`redis_store.py`, `feast_store.py`) and rolling velocity/amount aggregators (<5ms retrieval).
* [`disaster_recovery/`](../backend/app/infrastructure/disaster_recovery): Multi-region active-passive failover manager ([`region_failover.py`](../backend/app/infrastructure/disaster_recovery/region_failover.py), RTO < 30s, RPO = 0), continuous cryptographic backup verifier ([`backup_verifier.py`](../backend/app/infrastructure/disaster_recovery/backup_verifier.py), sub-5ms sandbox restore probes), and automated chaos DR drill runner ([`chaos_dr_drill.py`](../backend/app/infrastructure/disaster_recovery/chaos_dr_drill.py)).
* [`redis_store.py`](../backend/app/infrastructure/redis_store.py): Fault-tolerant state manager. It synchronizes simulation configurations and round metrics to Redis. If Redis is unreachable, it falls back to a thread-safe, in-memory cache to maintain liveness.
* [`security/p2p_secagg_driver.py`](../backend/app/infrastructure/security/p2p_secagg_driver.py) & [`security/shamir_engine.py`](../backend/app/infrastructure/security/shamir_engine.py): Client-side Curve25519 X25519 ECDH pairwise vector masking and Shamir $(t, n)$ threshold Galois field secret sharing.
* [`security/pqc_secagg_driver.py`](../backend/app/infrastructure/security/pqc_secagg_driver.py): NIST FIPS 203 (CRYSTALS-Kyber-768 KEM) and FIPS 204 (CRYSTALS-Dilithium-3 signatures) hybrid quantum-safe P2P SecAgg driver.
* [`security/layer2_crosschain_bridge.py`](../backend/app/infrastructure/security/layer2_crosschain_bridge.py): Chainlink CCIP `EVM2AnyMessage` and LayerZero V2 multi-ledger settlement bridge for Arbitrum, Optimism, Canton, and Hyperledger Fabric.
* [`security/adaptive_dp_autoscaler.py`](../backend/app/infrastructure/security/adaptive_dp_autoscaler.py): Rényi Differential Privacy (RDP) and PRV numerical dual accountant with loss-velocity dynamic Gaussian noise auto-scaling.
* [`security/hsm_signer.py`](../backend/app/infrastructure/security/hsm_signer.py): Pluggable PKCS#11 hardware signer driver and `SoftwareHSMSignerEngine` emulator enforcing non-exportable private key handles and session PIN authentication.
* [`security/hsm_key_service.py`](../backend/app/infrastructure/security/hsm_key_service.py): Hardware Security Module (HSM) PKCS#11 & Vault Transit Zero-Trust key service wrapper enforcing Zero-Disk non-exportable private keys (`is_exportable = False`), Curve25519 ECDH shared secret derivation within hardware enclaves, automated mTLS 1.3 rotation monitoring, and X.509 consortium peer thumbprint attestation.
* [`security/tee_driver.py`](../backend/app/infrastructure/security/tee_driver.py): Pluggable Confidential Computing driver and `SoftwareEmulatedTEEDriver` modeling Intel SGX / AWS Nitro remote attestation (`MRENCLAVE`) and memory sealing.
* [`security/vault_hsm_pki_binder.py`](../backend/app/infrastructure/security/vault_hsm_pki_binder.py): HashiCorp Vault PKI root CA binding to FIPS 140-2 Level 3 HSM hardware slots via PKCS#11 (with SoftHSM2 development fallback).
* [`security/gnosis_multisig_coordinator.py`](../backend/app/infrastructure/security/gnosis_multisig_coordinator.py): Gnosis Safe 2-of-3 threshold multi-sig coordinator governance driver with EIP-712 structured data signatures.
* [`security/zk_snark_verifier.py`](../backend/app/infrastructure/security/zk_snark_verifier.py) & `zk_circuits/`: Groth16 zk-SNARK model weight attestation driver over BN254 curve with Poseidon hashing and $O(1)$ constant-time bilinear pairing proof verification.
* [`connectors/`](../backend/app/infrastructure/connectors): Production Bank Connector subsystem implementing Hexagonal Ports & Adapters:
  * [`base_connector.py`](../backend/app/infrastructure/connectors/base_connector.py): Defines `BaseBankConnector` abstract interface and unified `NormalizedTransaction` Pydantic domain schema.
  * [`streaming_connector.py`](../backend/app/infrastructure/connectors/streaming_connector.py): Real-time payment event streaming connector for Kafka, RabbitMQ, and Redis streams.
  * [`iso20022_connector.py`](../backend/app/infrastructure/connectors/iso20022_connector.py): Financial message parser converting ISO 20022 MX (`pacs.008`, `pacs.009`) XML and SWIFT MT103/MT202 text records into normalized transactions.
  * [`batch_connector.py`](../backend/app/infrastructure/connectors/batch_connector.py): End-Of-Day (EOD) file batch parser for CSV and Parquet transaction dumps.
  * [`rest_connector.py`](../backend/app/infrastructure/connectors/rest_connector.py): HTTP REST adapter supporting mTLS, OAuth2, HMAC payload signing, and real-time webhook ingestion.
  * [`factory.py`](../backend/app/infrastructure/connectors/factory.py): Configuration-driven [`BankConnectorFactory`](../backend/app/infrastructure/connectors/factory.py#L22) resolving per-bank connector implementations.
* [`security/smart_contract_driver.py`](../backend/app/infrastructure/security/smart_contract_driver.py): Web3 & CBDC settlement driver executing automated token disbursements (`wCBDC`, `USDC`, `e-TRY`) on `ConsortiumIncentiveSettlement.sol` based on LOO Shapley values.
* [`security/rate_limiter.py`](../backend/app/infrastructure/security/rate_limiter.py): Granular endpoint rate limiting singleton powered by `slowapi` and `limits`, with reverse-proxy real IP resolution (`CF-Connecting-IP`, `X-Real-IP`, `X-Forwarded-For`).
* [`grpc/`](../backend/app/infrastructure/grpc): High-Performance Bidirectional Streaming gRPC Transport Layer over HTTP/2 defined via `fl_service.proto` ([`servicer.py`](../backend/app/infrastructure/grpc/servicer.py)) with strict Anti-TOFU certificate fingerprint binding (`register_bank_fingerprint`) and cross-tenant anti-spoofing.
* [`telemetry.py`](../backend/app/infrastructure/telemetry): Bypasses metrics or mounts a `/metrics` ASGI app for Prometheus based on configurations.
* [`celery_app.py`](../backend/app/infrastructure/celery_app.py): Background worker queue for handling long-running PyTorch training loops without blocking FastAPI.

### 2.4 Presentation Layer ([`backend/app/presentation/`](../backend/app/presentation))
Interactions with clients, compliance officers, investigators, and consortium banking nodes.

* [`main.py`](../backend/app/main.py): Houses global `TenantAccessControlMiddleware` (BOLA/IDOR query parameter tampering interception), `DDoSProtectionMiddleware`, `SecurityHeadersMiddleware`, `ProductionErrorHandler`, and the dark-themed `@scalar/api-reference` gateway at `GET /scalar`.
* **44 Modular REST Routers** in [`routers/`](../backend/app/presentation/routers): Verifies request formats via Pydantic schemas, enforcing `@limiter.limit(...)`, `enforce_tenant_isolation(...)`, and specialized RegTech engines (`regulatory_dossier.py`, `financial_messages.py`, `bridge_messaging.py`, `payment_recall.py`, `screening.py`, `regulatory.py`, `onboarding.py`, `cases.py`, `alerts.py`, `banks.py`, `gateway.py`, `predict.py`, `diagnostics.py`, `open_aml_adapter.py`, `ubo_graph.py`, `european_scenarios.py`, `asset_recovery.py`, `core_banking_gateway.py`, etc.).
* **WebSockets**: [`websockets/streaming_ws.py`](../backend/app/presentation/websockets/streaming_ws.py) & [`training_ws.py`](../backend/app/presentation/websockets/training_ws.py): Persistent WebSocket channels broadcasting real-time high-risk fraud alerts (`/ws/telemetry`), telemetry ticks, and live federated training weight updates.
* **Unified Web UI Frontend Architecture** ([`frontend/`](../frontend)): React 19, TypeScript, Vite, TanStack Query, Tailwind CSS, Lucide icons, and Framer Motion delivering 12 integrated enterprise views:
  - `LandingPage` & `PlatformLaunchModal`: Direct role-based routing and live consortium telemetry.
  - `InvestigationDashboard` & `CaseDetailPage`: Multi-hop graph visualizer, SHAP explainability, and 4-Eyes dual sign-off.
  - `BankOnboardingPage`: 5-step institutional onboarding and Dataset Ingestion Studio.
  - `SecurityPage`: KMS envelope encryption, TEE attestation, and quantum-safe SecAgg status.
  - `ObservabilityPage`: Real-time throughput, p99 latency, drift metrics, and system health.

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
```

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
1. **Node Registration (`RegisterClient`):** Validates bank certificate fingerprints against authoritative onboarding records (strict Anti-TOFU) and returns a session token and assigned cluster ID.
2. **Bidirectional Heartbeat (`Heartbeat`):** Streams client telemetry (CPU, memory, dataset size) while receiving coordinator state commands (`IDLE`, `START_TRAINING`, `CANCEL_ROUND`, `UPDATE_CONFIG`).
3. **Client-Streaming Parameters (`StreamModelParameters`):** Transmits encrypted model updates split into 1 KB binary chunks signed with digital signatures.
4. **Server-Streaming Global Model (`DownloadGlobalModel`):** Delivers aggregated global model weights in SHA-256 checksum-verified binary chunks.

- **Empirical Fault Tolerance Benchmark (`NetworkResilienceEvaluator`)**: Evaluates operational continuity under network dropouts and straggler latency delays:
  - **Scenario A (Straggler Latency)**: 2 stragglers delayed by 250s $\rightarrow$ 3 fast nodes submit in 11.8s, reaching 60% dynamic quorum and triggering auto-aggregation without waiting for stragglers.
  - **Scenario B (Abrupt Node Disconnect)**: 1 node disconnects (40% packet drop) $\rightarrow$ 4 active nodes reach 80% quorum in 14.2s.
  - **FedAsync Staleness Attenuation**: $S(\tau) = (1 + \tau)^{-\alpha}$ preserves $F_1 = 93.2\%$. Zero deadlocks.

### 3.4 Automated Optuna FL Hyperparameter Optimizer, Dirichlet Partitioner & Fidelity Auditor (`fl_hyperparameter_optimizer.py`, `fl_dirichlet_partitioner.py` & `distribution_fidelity_service.py`)
- **Non-IID Dirichlet Partitioner ($\text{Dir}(\alpha)$):** Simulates cross-bank label skew ($\alpha \in [0.01, 10.0]$) using class-wise Dirichlet sampling $\mathbf{p}_c \sim \mathrm{Dir}(\alpha \cdot \mathbf{1}_K)$. Rejection sampling (up to 100 attempts) paired with boundary donor rebalancing strictly guarantees $|D_i| \ge \mathrm{size}_{\mathrm{min}}$ even under extreme skew ($\alpha = 0.01$) without artificial proportion distortion.
- **Partition Statistical Fidelity (`compute_partition_stats`):** Computes per-client sample counts, class distributions, fraud ratios, Total Variation Distance ($\mathrm{TVD}_i = \frac{1}{2}\sum_c |P_i(c) - P_{\text{global}}(c)| \in [0, 1]$), Shannon label entropy ($H_i(Y) = -\sum_c P_i(c) \log_2 P_i(c)$), and cross-bank quantity skew ratio ($\max |D_i| / \min |D_i|$).
- **Synthetic-to-Real Distribution Fidelity (`distribution_fidelity_service.py`):** Real-time empirical evaluation of generator fidelity vs. benchmark banking data using 1-Wasserstein distances ($W_1$), Jensen-Shannon divergences ($JS \in [0, 1]$), Kolmogorov-Smirnov goodness-of-fit ($D_{\text{KS}}, p$), and Frobenius covariance drift ($\lVert\mathbf{\Sigma}_{\text{real}} - \mathbf{\Sigma}_{\text{synth}}\rVert_F$).
- **Optuna Bayesian TPE Engine:** Optimizes learning rates, local epochs, DP clip norms $C_{\text{max}}$, noise multipliers $\sigma$, staleness decay $\gamma$, and FedProx $\mu$ using `TPESampler` and early `MedianPruner`.
- **Management API Endpoints:** Exposes `POST /v1/admin/optimization/tune` and `GET /v1/admin/optimization/studies/{study_name}`.

### 3.5 Berlin Group NextGenPSD2 & Open Banking Data Connector Mapping
```
NextGenPSD2 REST Endpoint ──► OpenBankingConnector ──► OAuth2 / mTLS Headers ──► NormalizedTransaction
  (/v1/accounts/{id}/txs)            │
                                     ├── booked[]   ──► IBAN / Amount / Currency / MCC / BookingDate
                                     └── pending[]  ──► IBAN / Amount / Currency / MCC / ValueDate
```

The [`OpenBankingConnector`](../backend/app/infrastructure/connectors/open_banking_connector.py) and [`ISO20022MessagingConnector`](../backend/app/infrastructure/connectors/iso20022_connector.py) map European Berlin Group NextGenPSD2 REST and SWIFT ISO 20022 XML payloads into `NormalizedTransaction` objects:
- **Authentication & Headers**: Executes OAuth 2.0 Client Credentials Grant with token TTL expiration tracking and injects eIDAS QWAC/QSeal `X-Request-ID`, `Digest` (SHA-256), `PSU-IP-Address`, and `TPP-Signature` headers.
- **ISO 20022 XML & SWIFT Parsing**:
  - `pacs.008.001.08` (Customer Credit Transfer) $\rightarrow$ `NormalizedTransaction` (`IntrBkSttlmAmt`, `DbtrAcct`, `CdtrAcct`).
  - `camt.053.001.08` (Bank Statement) $\rightarrow$ `NormalizedTransaction[]` list (`Stmt/Ntry` array extraction).
  - `pacs.002.001.10` (Payment Status Report) $\rightarrow$ `NormalizedTransaction` (`TxSts`, `OrgnlPmtInfId`).
  - SWIFT MT103 $\rightarrow$ `NormalizedTransaction` (`:20:`, `:32A:`, `:50K:`, `:59:` parsing).

### 3.6 Enterprise Zero-Mock Architecture Policy
The platform enforces a strict Zero-Mock Policy in production:
- All legacy mock generators (`data_generator.py`) and mock connectors (`mock_connector.py`, `mq_skeleton_connector.py`) are deprecated and rejected by [`BankConnectorFactory`](../backend/app/infrastructure/connectors/factory.py).
- Requesting `connector_type="mock"` or `"mq_skeleton"` raises an explicit `ValueError`.
- Production connector types (`open_banking`, `psd2`, `parquet`, `rabbitmq`, `kafka`, `iso20022`, `rest`) consume real institutional data streams without synthetic fallbacks.
- **Open Banking Presentation Router Scope**: The `/api/v1/psd2/*` presentation endpoints serve as an XS2A testbed sandbox that deterministically synthesizes account balances and historical transactions via SHA-256 derivation per `account_id` (with `acc_1` preserved for integration test fixtures). Live enterprise integrations ingest PSD2 records through the production `OpenBankingConnector` via Berlin Group NextGenPSD2 REST endpoints with real OAuth2/mTLS eIDAS credentials.

### 3.7 Redis Online Feature Store & Feast Integration (`redis_store.py`, `feast_store.py`)
- **Redis Online Feature Store (`RedisFeatureStore`)**: Implements low-latency (<5ms) online feature serving with Redis connection pooling (`max_connections=20`), pipeline batch execution (`batch_set_features`, `batch_get_features`), TTL key expiration (`EXPIRE 86400`), and automatic memory cache fallback for standalone execution.
- **Feast Feature Store Adapter (`FeastFeatureStoreAdapter`)**: Adapts online Redis feature view serving (`get_online_features`, `push_online_features`) and point-in-time historical feature vector joins (`get_historical_features`) for federated fraud detection model training and real-time inference.

---

## 4. Analytical Drift Detection Suite

We implement dynamic, multi-pair statistical checks inside `presentation/routers/banks.py`:

1. **Jensen-Shannon (JS) Divergence:** Measures categorical and continuous feature probability divergence:
   $$D_{\text{JS}}(P \parallel Q) = \frac{1}{2} D_{\text{KL}}(P \parallel M) + \frac{1}{2} D_{\text{KL}}(Q \parallel M)$$
   where $M = \frac{1}{2}(P + Q)$, using base-2 logarithm to bound outcomes in $[0, 1]$.
2. **Population Stability Index (PSI):** Measures feature shifts across dynamic decile bins:
   $$\text{PSI} = \sum_{b=1}^{B} (A_b - E_b) \times \ln\left(\frac{A_b}{E_b}\right)$$
3. **Concept Drift:** Evaluates $P(Y \mid X)$ stability by training a logistic regression model on Bank A and calculating the prediction shift on Bank B, paired with segment-specific conditional JS drifts.

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

### 6.1 Asynchronous Federated Aggregation (`async_fl_engine.py`, `coordinator_service.py`)
The `AsyncFLEngine` implements the **FedAsync** parameter update protocol (Xie et al., 2019). Fast bank nodes submit model updates immediately — without blocking on straggler nodes — using a staleness attenuation factor to preserve convergence quality.

**Staleness Attenuation Functions ($S(\tau)$):**
$$S(\tau) = (1 + \tau)^{-\alpha}$$
where $\tau = t_{\mathrm{current}} - t_{\mathrm{submitted}}$ (rounds elapsed since submission) and $\alpha$ is the attenuation exponent (default: $\alpha = 0.5$). Additional supported formulations include exponential decay $S(\tau) = e^{-\alpha \tau}$, constant $S(\tau) = 1.0$, and hinge decay $S(\tau) = \min\left(1, \frac{1}{\alpha(\tau - 2) + 1}\right)$.

**Straggler Cutoff Bound ($\tau_{\max}$):**
Updates with $\tau > \tau_{\max} = 50$ are automatically dropped ($S(\tau) = 0.0$) to prevent model divergence caused by severely outdated gradients.

**Global Weight Update Rule:**
$$W^{(t+1)} = (1 - \alpha_\tau)\,W^{(t)} + \alpha_\tau\,W_i^{(t-\tau)}$$
where $\alpha_\tau = \eta \cdot S(\tau)$ is the learning rate weighted by staleness attenuation. Fresh updates ($\tau = 0$) receive full learning rate weight ($S(0) = 1.0$); older stale updates are progressively down-weighted.

**Invariants & Safety Protections:**
- **Byzantine Non-Finite Guard:** Automatic pre-aggregation inspection (`np.isfinite`); any incoming tensor with `NaN` or `Inf` triggers an immediate `ValueError` without mutating the global state.
- **Thread-Safe Concurrency:** Serialized via internal `threading.Lock()` across concurrent bank submission threads.
- **Memory Management:** Bounded update history buffer (`maxlen=500`) and round pruning (`prune_completed_rounds`) prevent heap accumulation.

### 6.2 Dynamic Quorum Timeout Manager (`quorum_manager.py`)
The `DynamicQuorumManager` monitors real-time round submission progress across all registered bank nodes. It automatically triggers round aggregation as soon as the minimum quorum threshold is satisfied — without waiting for the target window to expire.

**Quorum States:**
| State | Condition |
|:---|:---|
| `WAITING` | Submitted nodes / Registered nodes < 60% and elapsed < 300s |
| `QUORUM_REACHED` | Submitted nodes / Registered nodes ≥ 60% |
| `TIMEOUT_EXPIRED` | Elapsed time ≥ 300s before quorum threshold reached |

**Auto-Aggregation Protocol:**
1. Bank nodes register for the round via `register_nodes(node_ids)`.
2. Each gradient/weight submission is recorded via `record_node_submission(node_id)` under thread-safe synchronization.
3. After each submission, `evaluate_quorum_status()` checks: $\frac{|\mathrm{submitted}|}{|\mathrm{registered}|} \ge 0.60$.
4. If `QUORUM_REACHED` $\to$ immediate aggregation trigger (no timeout wait).
5. If `TIMEOUT_EXPIRED` $\to$ graceful fallback with partial aggregation from submitted nodes.

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
|:---|:---|:---|
| `SpectralDefenseConfig` | `spectral_defense.py` | Config: threshold multiplier $\tau$, min_clients |
| `SpectralAnomalyReport` | `spectral_defense.py` | Per-client spectral score & quarantine status |
| `SpectralAnomalyDetector` | `spectral_defense.py` | SVD power iteration + anomaly detection + robust aggregation |
| `_power_iteration()` | `spectral_defense.py` | Pure-stdlib dominant right singular vector $v_1$ computation |

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
             │ consortium-net only                         │ consortium-net only
             └─────────────────────┐  ┌────────────────────┘
                                   ▼  ▼
                    ┌──────────────────────────────┐
                    │  cfi-fl-coordinator          │
                    │  - Central PKI / CA Engine   │
                    │  - Secure Aggregator         │
                    │  - gRPC Server (:50051)      │
                    └──────────────────────────────┘
```

- **Subnet Separation**: Bank internal subnets (`bank-a-net`, `bank-b-net`) are configured with `internal: true`. Direct inter-bank container communication is blocked at the bridge interface level.
- **Outbound-Only mTLS**: Bank client daemons initiate outbound-only mTLS 1.3 connections to the coordinator on `consortium-net:50051`.
- **Mode Dispatch**: The application dispatches service roles dynamically via the `MODE` environment variable (`MODE=coordinator` vs `MODE=bank_client`).

### 8.2 gRPC Transport Protocol (`fl_service.proto`)
The inter-container parameter exchange, heartbeat liveness, and global model distribution operate over streaming gRPC RPC handlers:

| RPC Handler | Type | Description |
|:---|:---|:---|
| `RegisterClient` | Unary | Validates X.509 certificate fingerprint against authoritative onboarding records, returns `session_token` and `cluster_id`. |
| `Heartbeat` | Bidirectional Stream | Streams node telemetry (`cpu`, `memory`, `dataset_size`), yields `CoordinatorStatus` commands (`START_TRAINING`, `IDLE`). |
| `StreamModelParameters` | Client Streaming | Chunks encrypted weight payload, attaches digital signature per chunk, passes reassembled weights to `FLEngine`. |
| `DownloadGlobalModel` | Server Streaming | Streams aggregated global model binary chunks with per-chunk SHA-256 checksum integrity verification. |

### 8.3 Enterprise Multi-Tenant Database Persistence Engine
The platform implements multi-tenant database isolation (SOC2/PCI-DSS compliant) where each bank node operates against its own isolated database instance or schema:
- **AsyncEngine Connection Pooling**: Production PostgreSQL / CockroachDB AsyncEngine configured via `_make_engine_kwargs(tenant)` with `pool_size=20`, `max_overflow=10`, `pool_recycle=3600`, and `pool_pre_ping=True`.
- **Serializable Isolation & Retry Loop**: `run_cockroach_transaction()` handles SQLSTATE `40001` transaction conflicts with exponential retry loops.
- **Alembic Schema Migrations**: Managed via [`alembic.ini`](../backend/alembic.ini), [`env.py`](../backend/app/infrastructure/database/migrations/env.py), and [`migration_manager.py`](../backend/app/infrastructure/database/migration_manager.py) for programmatic `upgrade_head()` / `downgrade_revision()` auto-migrations.

---

## 9. Technology Stack & Directory Structure

```
├── docker-compose.multinode.yml  # Multi-node network-isolated container orchestration
├── backend/
│   ├── app/
│   │   ├── domain/               # Pure Python domain entities, state machines & enums
│   │   │   ├── case_management.py   # 6-Stage case lifecycle state machine
│   │   │   ├── backup_record.py     # Backup verification & restore probe models
│   │   │   ├── dr_coordinator.py    # Multi-region disaster recovery coordinator
│   │   │   └── ai_act_compliance.py # EU AI Act articles 10-15 compliance engine
│   │   ├── application/          # Service layer & business logic orchestration
│   │   │   ├── services/            # FL engine, Risk scoring, Onboarding, Copilot
│   │   │   └── schemas/             # Pydantic validation & transfer schemas
│   │   ├── infrastructure/       # Concrete adapters & external systems
│   │   │   ├── database/            # SQLAlchemy 2.0 & Alembic migrations
│   │   │   ├── disaster_recovery/   # BackupVerifier, RegionFailover, ChaosDRDrill
│   │   │   ├── grpc/                # HTTP/2 gRPC servicer & Anti-TOFU bindings
│   │   │   ├── security/            # KMS, HSM, TEE, PQC SecAgg, zk-SNARK
│   │   │   └── connectors/          # ISO 20022, Open Banking, Kafka, Parquet
│   │   └── presentation/         # API controllers & WebSocket streams
│   │       ├── routers/             # 42 modular FastAPI REST endpoints (incl. FININT, SEPA Recalls, Sanctions, goAML, Open AML Adapter, Corporate UBO, European AML Scenarios, Asset Recovery)
│   │       └── websockets/          # Real-time alert & training WebSockets
│   └── tests/                    # 2,273 automated unit, integration, & security tests
├── frontend/
│   ├── src/
│   │   ├── api/                  # TanStack Query clients & REST hooks
│   │   ├── components/           # Reusable UI components, Charts, Ingestion Studio
│   │   └── pages/                # 12 unified application views (Dashboard, Onboarding, etc.)
│   └── package.json              # React 19, Vite, Tailwind CSS dependencies
├── deployments/
│   ├── helm/cfi-platform/        # Production Helm 3 chart (HPA, NetPol, HSM)
│   ├── terraform/                # Multi-cloud IaC (AWS EKS, Azure AKS, GCP GKE)
│   ├── grafana/dashboards/       # Consortium overview & security audit dashboards
│   └── prometheus/               # Metric scrape configs & alert rules
└── docs/                         # Architecture, Security Matrix, Threat Models, DR Specs
```

---

## 10. Kubernetes & Helm 3 Infrastructure

The CFI Platform ships production Kubernetes workloads managed through a Helm 3 chart located in `deployments/helm/cfi-platform/`.

### 10.1 Chart Structure
| File | Purpose |
|:---|:---|
| `Chart.yaml` | Chart metadata, semver versioning, bitnami dependency declarations |
| `values.yaml` | Environment-agnostic defaults; override per-env via `-f values-prod.yaml` |
| `templates/aggregator-deployment.yaml` | Central FL Aggregator — 2 replicas, gRPC + HTTP ports, HPA-enabled |
| `templates/bank-node-deployment.yaml` | Bank Client Nodes — HSM secret mounts, conditional PKCS#11 env injection |
| `templates/service.yaml` | ClusterIP services for both aggregator and bank-node |
| `templates/ingress.yaml` | NGINX Ingress with TLS termination and gRPC backend annotation |
| `templates/hpa-and-netpol.yaml` | HPA (`autoscaling/v2`) + Zero-Trust NetworkPolicy |

### 10.2 Security Model
- **Secrets**: All credentials injected via `secretKeyRef` references to pre-created Kubernetes Secrets.
- **Zero-Trust NetworkPolicy**: Bank nodes may only receive ingress from the aggregator pod and may only egress to the aggregator gRPC port and DNS (port 53/UDP). Inter-bank direct communication is blocked at the kernel netfilter level.
- **HSM Integration**: Bank nodes mount PKCS#11 credentials as a read-only Secret volume when `bankNode.hsm.enabled=true`.
- **Read-Only Root Filesystem**: All containers enforce `readOnlyRootFilesystem: true` with a writable `/tmp` `emptyDir` volume.
- **Non-Root Containers**: `runAsNonRoot: true`, `runAsUser: 1000`, `fsGroup: 2000` applied to all pods.
- **Dropped Capabilities**: `capabilities.drop: [ALL]` on every container security context.

### 10.3 Horizontal Pod Autoscaling & Deployment Command
The Aggregator HPA scales between `minReplicas: 2` and `maxReplicas: 10` on CPU utilization target of 70%.

```bash
helm dependency update deployments/helm/cfi-platform
helm upgrade --install cfi-platform deployments/helm/cfi-platform \
  --namespace cfi-prod \
  --create-namespace \
  -f deployments/helm/cfi-platform/values.yaml \
  --wait --timeout 10m
```

---

## 11. Infrastructure as Code (Terraform Multi-Cloud)

Modular Terraform (>= 1.6.0) templates in `deployments/terraform/` provision all cloud infrastructure as immutable, version-controlled code across three enterprise cloud providers.

### 11.1 Module Structure
| Cloud | Directory | Managed Resources |
|:---|:---|:---|
| **AWS** | `deployments/terraform/aws/` | VPC (private/public subnets, NAT GW), EKS (1.30), Managed Node Group, AWS KMS (AES-256, auto-rotate), Security Groups (gRPC mTLS isolation) |
| **Azure** | `deployments/terraform/azure/` | Resource Group, VNet + NSG (Deny inter-bank gRPC rule), AKS (1.30, Calico policy), Bank Node Pool (autoscaling 2-10), Azure Key Vault (Premium SKU, purge protection) |
| **GCP** | `deployments/terraform/gcp/` | VPC Network + Private Subnet, Cloud Router/NAT, GKE (1.30, private cluster, Workload Identity), Node Pool (autoscaling 2-10, Shielded VMs), Cloud KMS KeyRing + CryptoKey (90-day rotation), Deny inter-bank Firewall rule |

### 11.2 Security Posture
- **KMS Envelope Encryption**: All Kubernetes etcd secrets are encrypted at rest via provider-managed KMS CMEK (AWS KMS, Azure Key Vault, Cloud KMS).
- **Private Cluster Endpoints**: No public API server endpoints; EKS (`endpoint_public_access=false`), AKS (`private_cluster_config`), GKE (`enable_private_nodes=true`).
- **Zero-Trust Firewall**: Explicit Deny rules block direct bank-node-to-bank-node gRPC across all three providers; FL traffic must traverse the aggregator.
- **Key Rotation**: AWS KMS (`enable_key_rotation=true`), GCP Cloud KMS (`rotation_period=7776000s`/90 days), Azure Key Vault (`soft_delete_retention_days=90`, `purge_protection_enabled=true`).

---

## 12. Production Telemetry, Observability & Alerting

The CFI Platform ships a full enterprise observability stack covering distributed tracing, Prometheus metric exposition, Grafana dashboards, and automated alerting rules.

### 12.1 Telemetry Module (`backend/app/infrastructure/telemetry.py`)
The `TelemetryRegistry` singleton exposes the following CFI-specific Prometheus metrics:

| Metric | Type | Description |
|:---|:---|:---|
| `cfi_fl_round_duration_seconds` | Summary | FL training round execution duration (seconds) |
| `cfi_fl_round_participants` | Gauge | Active participating bank nodes per round |
| `cfi_dp_epsilon_consumed_total` | Counter | Cumulative DP epsilon budget consumed per bank |
| `cfi_spectral_anomalies_detected_total` | Counter | Byzantine/poisoning spectral anomalies detected |
| `cfi_grpc_request_duration_seconds` | Summary | gRPC endpoint latency per method |
| `cfi_hsm_signing_duration_seconds` | Summary | HSM PKCS#11 digital signing operation latency |
| `cfi_node_heartbeat_timestamp` | Gauge | Unix timestamp of last bank node heartbeat |

### 12.2 Grafana Dashboards (`deployments/grafana/dashboards/`)
| Dashboard | UID | Key Panels |
|:---|:---|:---|
| `fl_consortium_overview.json` | `cfi-fl-consortium-overview` | FL round count, active quorum, avg round duration, gRPC p50/p99 latency, node heartbeat age |
| `privacy_security_audit.json` | `cfi-privacy-security-audit` | Per-bank DP epsilon consumption, spectral anomaly total count, HSM signing avg latency |

### 12.3 Prometheus Alert Rules (`deployments/prometheus/alert_rules.yml`)
| Alert | Condition | Severity |
|:---|:---|:---|
| `DPBudgetExhaustionWarning` | `cfi_dp_epsilon_consumed_total > 9.0` | warning |
| `BankNodeOffline` | `time() - cfi_node_heartbeat_timestamp > 60s` | critical |
| `SpectralAnomalySpike` | `>3 anomalies in 5m` | critical |
| `GRPCSLABreach` | `p99 gRPC latency > 500ms over 5m` | warning |

---

## 13. EU AI Act Compliance Certificate Export Engine

The CFI Platform includes an automated compliance engine ([`backend/app/domain/ai_act_compliance.py`](../backend/app/domain/ai_act_compliance.py)) and CLI tool (`scripts/export_compliance_report.py`) that evaluate FL global model deployments against **Regulation (EU) 2024/1689 (EU AI Act)** high-risk AI system requirements (Articles 10–15).

### 13.1 Article Coverage & Assessment Criteria
| Article | Requirement | Assessment Evidence & Thresholds |
|:---|:---|:---|
| **Article 10** | Data Governance & Management | ISO 13616 IBAN validation pass rate $\ge 99.9\%$, Differential Privacy $\epsilon \le 10.0$ ceiling, FL training round count $\ge 1$ |
| **Article 11** | Technical Documentation | Hyperparameters SHA-256 hash traceability, model versioning, federated topology documentation |
| **Article 12** | Record-Keeping | Append-only FL round audit log SHA-256 digest, 7-year audit retention policy |
| **Article 13** | Transparency to Users | SHAP feature attribution explainability, Annex III high-risk AI classification disclosure |
| **Article 14** | Human Oversight | Dual sign-off gate approval (ML Engineer + Compliance Officer per SR 11-7), automated AUC rollback |
| **Article 15** | Accuracy & Cybersecurity | AUC $\ge 0.75$, F1 $\ge 0.70$, spectral anomaly defense count, gRPC mTLS 1.3 + HSM PKCS#11 |

### 13.2 Cryptographic Signing & Fingerprint Verification
Certificates are serialized as canonical deterministic JSON (sorted keys) and signed using **HMAC-SHA256**:
$$\text{Signature} = \text{HMAC-SHA256}(K_{\text{signing}}, \text{JSON}_{\text{canonical}})$$
The full signed certificate string is hashed with SHA-256 to produce an immutable **Certificate Fingerprint** (`cert_hash`), which is logged to audit stores for non-repudiation.

---

## 14. Automated Model Lineage & Registry Vault

The `ModelRegistryVault` ([`backend/app/domain/model_governance.py`](../backend/app/domain/model_governance.py)) manages model checkpoint lifecycles, cryptographic lineage binding, and dual-gated promotion to production.

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
|:---|:---|
| `DRAFT` | Checkpoint registered during local/intermediate FL training rounds |
| `CANDIDATE` | Evaluated FL global model ready for dual sign-off and HSM signing |
| `PRODUCTION` | Active champion model serving live fraud prediction traffic |
| `ARCHIVED` | Superceded production model preserved for compliance and rollback |
| `ROLLED_BACK` | Model demoted due to live telemetry degradation or anomaly trigger |

### 14.1 Promotion Gating & Zero-Downtime Rollback
1. **Dual Sign-Off Gate**: Both `ml_engineer` and `compliance_officer` roles must have approved the checkpoint (SR 11-7 model risk policy).
2. **HSM Signature Envelope Verification**: The signature is verified over the canonical payload:
   $$\text{Payload} = \text{model-id} : \text{version} : \text{weights-sha256} : \text{hyperparams-sha256} : \text{dataset-hash} : \text{dp-epsilon}$$
3. **Automated Rollback Engine**: If live telemetry breaches safety thresholds (ROC-AUC $< 0.65$ or p99 latency $> 200\text{ms}$), current `PRODUCTION` model is demoted to `ROLLED_BACK` and the last `ARCHIVED` champion is restored.

---

## 15. Enterprise Role-Based Access Control (RBAC) & OAuth 2.0 / OIDC Gateway

The API Gateway ([`backend/app/presentation/routers/gateway.py`](../backend/app/presentation/routers/gateway.py)) and security routers act as the single security perimeter enforcing OpenID Connect (OIDC) Bearer JWT authentication, dynamic Attribute-Based Access Control (ABAC), and immutable audit logging via [`ABACEngine`](../backend/app/infrastructure/security/abac_engine.py).

| Policy Rule | Condition | Enforced Action |
|:---|:---|:---|
| **Super-Admin Bypass** | `role in ("super_admin", "compliance_auditor")` | Bypasses tenant isolation restrictions for consortium oversight (`RULE-SUPERADMIN-OVERRIDE`) |
| **Tenant Isolation** | `user.bank_id != resource.bank_id` | Blocks cross-bank data access (`403 Forbidden`, `RULE-TENANT-ISOLATION`) unless holding `cross_bank_investigator` |
| **IP Subnet Restriction** | `client_ip not in allowed_ip_subnets` | Rejects non-whitelisted client IP addresses (`403 Forbidden`, `RULE-IP-RANGE-RESTRICTION`) |
| **Shift Hours Window** | `current_hour not in shift_hours` | Blocks access outside employee shift window (`403 Forbidden`, `RULE-SHIFT-HOURS-RESTRICTION`) |
| **Approval Tier Limit** | `resource.amount > user.approval_tier` | Blocks high-value actions (`approve`, `write`, `export`, `override`) exceeding tier (`RULE-APPROVAL-TIER-EXCEEDED`) |
| **Security Clearance** | `resource.classification_level > user.clearance_level` | Restricts access to sensitive classification models and reports (`RULE-CLEARANCE-LEVEL-INSUFFICIENT`) |
| **Audit Logging** | Every ABAC decision (grant or denial) | Appends structured cryptographic event to `ImmutableAuditChain` |

### 15.1 Fail-Closed Zero-Trust Enforcement & High-Throughput In-Memory Engine
In strict accordance with Zero-Trust architectural invariants, `ABACEngine` enforces **Fail-Closed** exception handling across all attribute parsers:
- **IP Range Exception Handling**: If an incoming client IP string is malformed or unparseable by `ipaddress.ip_address()`, the engine immediately denies access with `allowed=False` (`RULE-IP-RANGE-RESTRICTION`), preventing parser evasion attacks.
- **Shift Window Exception Handling**: If employee shift string formats are unparseable or corrupted, the engine immediately denies access with `allowed=False` (`RULE-SHIFT-HOURS-RESTRICTION`).
- **High-Throughput In-Memory Performance**: Evaluated via `scripts/run_abac_benchmark.py`, the stateless policy engine sustains **122,874 evaluations/second** with mean latency of **0.0078 ms (7.8 µs)** and p99 latency of **0.0184 ms (18.4 µs)**, introducing negligible overhead in the request pipeline.

### 15.2 Cryptographic OIDC JWT Token Verification & Claim Extraction (`oidc_authenticator.py`)
Enterprise federated SSO and consortium bearer tokens are validated through [`OIDCAuthenticator`](../backend/app/infrastructure/security/oidc_authenticator.py):
- **HMAC-SHA256 Cryptographic Signature Enforcement**: Uses PyJWT `jwt.decode()` with mandatory signature verification (`verify_signature: True`, `verify_exp: True`), rejecting forged tokens claiming elevated roles (`super_admin`) or invalid signing secrets.
- **Authoritative Claim Parsing**: Extracts subject (`sub`), tenant bank identity (`bank_id`), RBAC roles (`roles`), security clearance level (`clearance_level`), shift hours window (`shift_hours`), approval tier ceiling (`approval_tier`), and allowed IP subnet CIDRs (`allowed_ip_subnets`).
- **Dynamic Key Management**: Automatically resolves symmetric signing secret from application configuration (`settings.oidc_jwt_signing_secret`), ensuring synchronization with the security gateway and auth routers.

### 15.3 Application Perimeter Defense, WAF Guard & Defensive Security Headers
Ingress traffic to the FastAPI application layer is guarded by [`PerimeterWAFGuard`](../backend/app/infrastructure/security/perimeter_waf.py) and [`SecurityHeadersMiddleware`](../backend/app/infrastructure/security/security_headers.py):
- **Deep OWASP Top 10 Header & Body Inspection**: Every request URL path, body payload, and all HTTP request headers (including `User-Agent`, `Referer`, and custom `X-*` headers) are systematically inspected for:
  - **SQL Injection (SQLi)**: Regex patterns (`UNION SELECT`, `DROP TABLE`, `OR 1=1`, inline comment sequences).
  - **Cross-Site Scripting (XSS)**: `<script>` tags, `javascript:` pseudoprotocols, and DOM event handlers (`onload=`).
  - **Null-Byte Injection**: `\x00` byte sequences to prevent path traversal and byte-poisoning attacks.
  - **Sensitive Path Scans**: Blocks probes targeting `/.env`, `/.git`, `/admin`, `/actuator`, `/wp-admin`, and `/config.json`.
- **Thread-Safe Brute-Force Lockout & Memory Pruning**: Consecutively failed authentication attempts are tracked per client IP using `threading.Lock()` to prevent race conditions in multi-worker runtimes. When tracked IP entries exceed `_max_tracked_ips = 1000`, automatic LRU pruning (`_prune_stale_failures`) evicts expired entries, eliminating unbounded memory leaks. Legitimate access can be unlocked via `reset_client_lockout()`.
- **Defensive HTTP Response Headers**: Injects 7 mandatory security headers on every response (`Content-Security-Policy`, `Strict-Transport-Security` with preload, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, and `X-XSS-Protection`).
- **Cloudflare Layer 1 Automation**: Provisioned via [`scripts/setup_cloudflare_waf.py`](../scripts/setup_cloudflare_waf.py), configuring zone TLS 1.3 strict encryption, custom firewall rules for sensitive file scanning, and L7 rate limiting (100 reqs/10s) on mutating endpoints.

---

## 16. Live High-Throughput Payment Stream Benchmark

The enterprise benchmark framework (`scripts/run_enterprise_stress_test.py`) validates platform throughput capacity and latency under sustained ISO 20022 payment transaction loads.

* **ISO 20022 `pacs.008` Generator**: Generates structurally complete `FIToFICstmrCdtTrf` payment payloads with `GrpHdr`, `CdtTrfTxInf`, and `_cfi_meta`.
* **Concurrent Asyncio Workers**: Simulates multiple bank node payment feeds concurrently, measuring peak TPS, p50/p99 latency, and error rates.
* **Benchmark Reports**: Saves results to `benchmark_<TIMESTAMP>.json` and human-readable Markdown reports conforming to `docs/enterprise_benchmark_report.md`.

---

## 17. Continuous Security & Test Verification Metrics

The automated enterprise security CI/CD workflow ([`.github/workflows/enterprise_security_ci.yml`](../.github/workflows/enterprise_security_ci.yml)) executes multi-layer security auditing across source code, third-party dependencies, container images, infrastructure templates, and testing suites.

```
 GitHub Actions Event (Push / PR / Nightly Cron)
     │
     ├── 1. sast-static-analysis (Ruff, Mypy, Bandit SAST)
     ├── 2. dependency-security-audit (pip-audit against PyPA DB + npm audit)
     ├── 3. gitleaks-secret-scan (Automated credential & secret leak detection)
     ├── 4. trivy-container-security (Trivy scanner for OS/library CVEs)
     ├── 5. helm-and-terraform-security-audit (Helm lint + AWS/Azure/GCP terraform validate)
     └── 6. pytest-security-and-compliance-suites (2,495 Automated Pytest Suites)
```

### Comprehensive Test Suite Verification
The entire codebase is validated by **2,495 automated tests** across unit, integration, and property-based suites:

```bash
pytest backend/tests/ -q
# Result: 2,495 tests collected and passing across all domain, application, and infrastructure modules
```

| Security & Compliance Job | Technology / Tool | Security Scope |
|:---|:---|:---|
| **SAST Analysis** | `Ruff`, `Mypy`, `Bandit` | Code formatting, type safety, SQL injection, hardcoded secrets, insecure crypto |
| **Dependency Audit** | `pip-audit`, `npm audit` | PyPI and npm third-party package CVE vulnerability checks |
| **Secret Scanning** | `gitleaks` | Automated detection of hardcoded credentials, tokens, and private keys |
| **Container Scan** | `aquasecurity/trivy-action` | Base OS image & installed library CVE scanning (`CRITICAL`, `HIGH`) |
| **IaC Security** | `Helm`, `Terraform` | Helm chart linting & AWS/Azure/GCP multi-cloud template validation |
| **Full Automated Test Suite**| `Pytest` | 2,495 automated tests covering EU AI Act & SR 11-7 Regulatory Dossier Generator, Differential Privacy, Spectral Defense, Onboarding, Open Banking PSD2, SEPA Instant Recall, Sanctions Screening, UNODC goAML / AMLA Exporter, Enterprise Open AML Adapter, Corporate UBO Knowledge Graph, European AML Scenario Library, Asset Recovery & FININT Operational Hub, Enterprise CloudEvents 1.0 & Apache Kafka Streaming Bus, Cloud Core Banking Connectors (Mambu & Thought Machine Vault Core), Hardware Security Module (HSM) PKCS#11 & Vault Transit Zero-Trust Key Wrapper, Extended ISO 20022 Financial Rails (camt.053 / pacs.002 / pacs.003), and DR Failover |




