<div align="center">

# Collaborative Fraud Intelligence Platform

### Privacy-Preserving Cross-Bank Financial Fraud Detection and Anti-Money Laundering Architecture

[![CI Build](https://github.com/yusufcalisir/CF-Intelligence/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/yusufcalisir/CF-Intelligence/actions/workflows/ci.yml)
[![Vercel Deployment](https://img.shields.io/badge/Vercel-Live_Demo-000000.svg?style=flat&logo=vercel&logoColor=white)](https://cf-intelligence.vercel.app)
[![Python Version](https://img.shields.io/badge/python-3.12-3776AB.svg?style=flat&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115.0-009688.svg?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.4.0-EE4C2C.svg?style=flat&logo=pytorch&logoColor=white)](https://pytorch.org)
[![Passing Tests](https://img.shields.io/badge/tests-3171%2F3171_passing-success.svg?style=flat&logo=pytest&logoColor=white)](https://github.com/yusufcalisir/CF-Intelligence/actions)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Security Policy](https://img.shields.io/badge/Security-Policy-blue.svg)](SECURITY.md)

**[🌐 Live Demo Deployment](https://cf-intelligence.vercel.app)** | **[📖 Interactive API Reference](https://cf-intelligence.vercel.app/developer)**

---

### Architectural Specification Index

| Core Production Architecture | Engineering Rationale & Validation | Research & Operations |
|:---|:---|:---|
| [1. Executive Summary](#1-executive-summary--architectural-scope) | [13. Design Decisions & Trade-Offs](#13-design-decisions--trade-offs) | [19. Research & Exploratory Modules](#19-research--exploratory-modules) |
| [2. Master System Architecture](#2-master-system-architecture) | [14. Limitations & What This Is Not](#14-limitations--what-this-is-not) | [20. Prerequisites & System Requirements](#20-prerequisites-and-system-requirements) |
| [3. Directory Structure](#3-clean-architecture-directory-structure) | [15. Empirical Benchmarks](#15-empirical-performance--benchmark-suite) | [21. Quick Start Guide](#21-step-by-step-operator-quick-start) |
| [4. Data Ingestion & Parsing](#4-multi-bank-synthetic-data--multi-standard-ingestion) | [16. Regulatory Concepts Explored](#16-regulatory-concepts-explored) | [22. AI Collaboration Methodology](#22-development-methodology--ai-collaboration) |
| [5. Federated Learning](#5-federated-learning-engines--non-iid-optimization) | [17. Subsystem Self-Verification](#17-subsystem-self-verification-reports-verification) | [23. Related Work & References](#23-related-work-and-references) |
| [6. Core PET Security Perimeter](#6-core-privacy-enhancing-technologies-dp--secagg) | [18. API Blueprints](#18-api-endpoint-blueprints--json-schemas) | [24. Citation](#24-academic-citation-and-reference-format) |
| [7. Byzantine Defense](#7-byzantine-poisoning-defense--adversarial-robustness) | | [25. Author & Maintenance](#25-author-and-maintenance) |
| [8. Graph Intelligence](#8-graph-intelligence--fuzzy-entity-resolution) | | |
| [9. Composite Risk Engine](#9-9-signal-composite-risk-engine--model-explainability) | | |
| [10. Multi-Layer Defense & Gateway](#10-multi-layer-defense-gateway-broken-access-control--rate-limiting) | | |
| [11. Case Management & European RegTech](#11-human-in-the-loop-workbench-european-finint--regulatory-regtech) | | |
| [12. Database, HA & Disaster Recovery](#12-database-architecture-ha--disaster-recovery-operations) | | |

</div>

---

## 1. Executive Summary & Architectural Scope

Financial institutions operate under strict regulatory and statutory constraints (GDPR Articles 6 and 17, CCPA, Bank Secrecy Act, national banking secrecy legislation) that prohibit centralizing or pooling raw customer transaction records across institutional boundaries. This data fragmentation creates systemic blind spots in fraud detection:

1. **Cross-Bank Velocity & Layering Syndicates:** Criminal networks distribute illicit funds sequentially across multiple bank nodes within minutes, clearing accounts before individual single-bank rule engines detect velocity anomalies.
2. **Structured Smurfing Networks:** Money laundering rings break large deposits into micro-transactions placed across multiple financial institutions to remain strictly below single-bank regulatory reporting thresholds ($10,000 USD / €10,000 EUR).

The **Collaborative Fraud Intelligence Platform (CF-Intelligence)** addresses this fragmentation through a privacy-preserving federated architecture. Participating institutions collaboratively train shared machine learning models and graph embeddings without centralizing raw transaction records or customer PII.

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                             CORE PRODUCTION PIPELINE                             │
│                                                                                  │
│   [ Bank Alpha ]       [ Bank Beta ]       [ Bank Gamma ]    (Local Nodes)       │
│         │                    │                    │                              │
│         ▼                    ▼                    ▼                              │
│   ┌────────────────────────────────────────────────────────┐                     │
│   │ Local Privacy Boundary: Opacus DP + Curve25519 SecAgg  │ (Zero Raw PII)      │
│   └──────────────────────────┬─────────────────────────────┘                     │
│                              ▼                                                   │
│   ┌────────────────────────────────────────────────────────┐                     │
│   │ Byzantine Coordinator: FedProx/SCAFFOLD + Krum/Bulyan  │ (Drift & Poisoning) │
│   └──────────────────────────┬─────────────────────────────┘                     │
│                              ▼                                                   │
│   ┌────────────────────────────────────────────────────────┐                     │
│   │ Canary Gate & Champion/Challenger Model Registry       │ (Quality Gating)    │
│   └──────────────────────────┬─────────────────────────────┘                     │
│                              ▼                                                   │
│   ┌────────────────────────────────────────────────────────┐                     │
│   │ Real-Time Scoring (<14.2ms / ~308ms) + SHAP + Case WB  │ (Serving & SAR)     │
│   └────────────────────────────────────────────────────────┘                     │
└──────────────────────────────────────────────────────────────────────────────────┘
```

The core production path focuses on eight defensible engineering components:
- **Federated Learning Engines:** `FedAvg`, `FedProx`, and `SCAFFOLD` optimization handling extreme Non-IID Dirichlet label skew ($\alpha \le 0.50$).
- **Differential Privacy Guard:** Local gradient perturbation via Opacus with Gaussian noise and Rényi DP accounting ($\epsilon = 1.0, \delta = 10^{-5}$).
- **Secure Aggregation (SecAgg):** Peer-to-peer Curve25519 Diffie-Hellman pairwise zero-sum masking for masked parameter aggregation.
- **Byzantine Consensus:** `Krum` (single representative selection), `Trimmed Mean`, and `Bulyan` (Krum candidate selection + coordinate trimmed mean) aggregators paired with Spectral SVD backdoor filtering.
- **Graph Intelligence:** PyTorch `GraphSAGE` relational embeddings and MinHash LSH Private Set Intersection (Fuzzy PSI) for entity resolution.
- **Real-Time Composite Scoring:** Low-latency inference gateway combining 9 statistical, behavioral, and topological signals (< 14.2 ms Fast-Path raw, ~308 ms Concurrent Ensemble).
- **Multi-Layer Defense & Rate Limiting:** 3-layer architecture (Cloudflare WAF $\rightarrow$ Vercel Security Middleware (Node.js) $\rightarrow$ FastAPI `slowapi` & BOLA isolation).
- **Explainability & Governance:** Real-time SHAP feature attributions and a 6-stage case management workbench with automated FinCEN BSA SAR XML compilation.

*Note: Exploratory cryptographic research modules (zk-SNARK attestation, TenSEAL CKKS FHE, Post-Quantum Kyber-768, Hardware TEE drivers, EVM incentive contracts, and Cross-Chain bridges) are isolated in the [Research & Exploratory Modules](#19-research--exploratory-modules) section.*

---

## 2. Master System Architecture

### 2.1 Core System Topology

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                     Consortium Banking Institutions (Client Nodes)                   │
│          [ Bank Alpha ]            [ Bank Beta ]            [ Bank Gamma ]           │
│          (Retail / POS)          (Commercial Wires)         (Fintech / ACH)          │
└──────────────────────────────────────────┬───────────────────────────────────────────┘
                                           │ Local SGD Updates
                                           ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                             Local Privacy Boundary (PETs)                            │
│  - Opacus Differential Privacy Guard (L2 Norm Clipping C=1.0, Noise Scale sigma)     │
│  - Curve25519 ECDH Pairwise SecAgg Masking (Zero-Sum Vector Perturbation)            │
│  - Shamir (t, n) Threshold Secret Sharing (Galois Field Z_p Dropout Recovery)        │
└──────────────────────────────────────────┬───────────────────────────────────────────┘
                                           │ Masked Gradients (Zero Raw PII)
                                           ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                      Byzantine-Robust Server Coordinator Engine                      │
│  - Aggregators: FedAvg / FedProx (mu=0.01) / SCAFFOLD (Control Variates) / Bulyan    │
│  - Anomaly Filter: Spectral SVD Top Eigenvalue Backdoor Trigger Detection            │
│  - Non-IID Partitioner: Dirichlet Dir(alpha) Distribution Modeling                   │
└──────────────────────────────────────────┬───────────────────────────────────────────┘
                                           │ Candidate Global Weights
                                           ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                         Canary Quality Gate & Model Registry                         │
│  - Holdout Verification (PR-AUC, ROC-AUC) -> Promote Champion / Auto-Rollback        │
└──────────────────────────────────────────┬───────────────────────────────────────────┘
                                           │ Active Champion Model
                                           ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                       Real-Time Scoring & Operational Serving                        │
│  - Real-Time Scoring Gateway (<14.2ms Fast-Path / ~308ms Ensemble SLA)               │
│  - Multi-Layer Defense: Cloudflare WAF + Vercel Middleware (Node.js) + slowapi Limit │
│  - Broken Access Control (BOLA/IDOR) Multi-Tenant Isolation Middleware               │
│  - Fast SHAP Explanations (KernelExplainer) & Counterfactual Feature Sensitivity     │
│  - 6-Stage Case Workbench (Four-Eyes Supervisor Signature) & FinCEN BSA SAR XML      │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

---

### 2.2 Core Federated Training Lifecycle

```mermaid
flowchart TD
    subgraph Banks["1. Consortium Banking Nodes (Local Data & Training)"]
        Alpha["🏛️ Bank Alpha Node<br/><code>pacs.008 ISO 20022 Feeds</code>"]
        Beta["🏛️ Bank Beta Node<br/><code>camt.053 Account Ledgers</code>"]
        Gamma["🏛️ Bank Gamma Node<br/><code>GraphSAGE Subgraphs</code>"]
    end

    subgraph PETs["2. Local Privacy Perimeter"]
        DP["🛡️ Opacus Differential Privacy<br/><i>L2 Norm Clipping (C=1.0) & Gaussian Noise (σ)</i>"]
        SecAgg["🔗 Curve25519 Pairwise SecAgg<br/><i>Zero-Sum Pairwise Masking + Shamir Secret Sharing</i>"]
    end

    subgraph Coordinator["3. Byzantine Coordinator & Aggregation"]
        Agg["🛡️ Byzantine-Robust Aggregation<br/><i>FedProx · SCAFFOLD · Krum · Trimmed Mean · Bulyan</i>"]
        SVD["🔍 Spectral SVD Poisoning Filter<br/><i>Cosine Distance & Top Eigenvalue Anomaly Check</i>"]
        Canary["🚦 Canary Quality Gate<br/><i>Holdout Dataset Evaluation (PR-AUC / Recall@0.1% FPR)</i>"]
    end

    subgraph Serving["4. Operational Inference & Compliance"]
        Gateway["🚀 Real-Time Scoring Gateway<br/><i>Sub-100ms Inference & 9-Signal Composite Risk</i>"]
        SHAP["📊 Fast SHAP Explainer<br/><i>Feature Attribution & Counterfactual Sensitivity</i>"]
        CaseWB["📋 6-Stage Case Workbench<br/><i>Four-Eyes Supervisor Signature & FinCEN BSA SAR XML</i>"]
    end

    Alpha --> DP
    Beta --> DP
    Gamma --> DP
    DP --> SecAgg
    
    SecAgg --> Agg
    Agg --> SVD
    
    SVD -->|Clean Weights| Canary
    SVD -->|Poisoned Outlier| Quarantine["⚠️ Quarantine Node Update"]
    
    Canary -->|Quality Passed| Gateway
    Canary -->|Quality Degraded| Rollback["⏪ Auto-Rollback to Previous Champion"]
    
    Gateway --> SHAP
    Gateway --> CaseWB
```

---

### 2.3 Multi-Region Active-Passive HA Failover Sequence

```mermaid
sequenceDiagram
    autonumber
    participant App as API Client
    participant Primary as Primary Region (US-East)
    participant Standby as Standby Region (US-West)
    participant State as Distributed State Store

    App->>Primary: POST /v1/inference/score (Transaction Payload)
    Primary-->>App: 200 OK (Risk Score: 895.4 / BLOCK)
    Primary->>Standby: Heartbeat Ping (Interval: 3s)
    
    Note over Primary: Primary Region Network Outage
    
    Standby->>Primary: Heartbeat Probe (Timeout 15s)
    Standby->>Primary: Heartbeat Retries (3/3 Failed)
    
    Note over Standby: Standby Promotion Triggered (RTO < 30s)
    Standby->>State: Acquire Leader State Lock
    State-->>Standby: State Lock Granted
    
    App->>Standby: POST /v1/inference/score (Failover Path)
    Standby-->>App: 200 OK (Risk Score: 895.4 / BLOCK)
```

---

## 3. Clean Architecture Directory Structure

```
CF-Intelligence/
├── ruff.toml                                        # Project-wide Ruff linter, formatter & rule configuration
├── Dockerfile                                       # Multi-stage production container specification (Hugging Face Spaces)
├── docker-compose.yml                               # Enterprise multi-container orchestration (Gateway, SPA, API, Postgres, Redis)
├── docker-compose.multinode.yml                     # 3-Node distributed bank consortium cluster
├── docker-compose.dev.yml                           # Local developer stack with hot reloading (Backend, Frontend, Postgres, Redis)
├── .env.example                                     # Hardened production environment configuration template
├── Makefile                                         # Developer & CI automation tasks
├── benchmark.py                                     # Master multi-model & multi-dataset benchmark suite
├── vercel.json                                      # Vercel deployment configuration & serverless rewrites
├── pytest.ini                                       # Global pytest test runner configuration
├── SECURITY.md                                      # Enterprise vulnerability disclosure, PGP keys & security SLAs
│
├── docker/                                          # Production Enterprise Container Manifests
│   ├── README.md                                    # Container topology, multi-stage builds, reverse proxy & monitoring spec
│   ├── Dockerfile.frontend                          # Multi-stage Node 20 builder & Alpine Nginx SPA container
│   ├── Dockerfile.backend                           # Hardened Python 3.12 slim non-root API & ML container
│   ├── nginx/
│   │   ├── nginx.conf                               # Gateway reverse proxy, HTTP/2, TLS 1.3 & WebSocket upstream
│   │   └── frontend-nginx.conf                      # Internal SPA routing fallback & caching configuration
│   ├── postgres/
│   │   └── 01-init.sql                              # Cold-boot idempotent schema, tables & consortium seed script
│   ├── otel/
│   │   └── otel-collector-config.yaml               # OpenTelemetry Collector OTLP pipeline & Prometheus exporter
│   ├── prometheus/
│   │   └── prometheus.yml                           # Prometheus scrape configs for API metrics & OTel bridge
│   └── grafana/
│       └── provisioning/                            # Auto-provisioned Prometheus datasource & platform dashboards
│
├── backend/                                         # Clean Architecture Python 3.12 Backend
│   ├── alembic.ini                                  # Alembic DB migration configuration
│   ├── pip_audit_results.json                       # Comprehensive dependency security & vulnerability audit record
│   ├── pyproject.toml                               # Backend dependency & pytest/coverage config
│   ├── requirements.txt                             # Pinned production dependencies (FastAPI, PyTorch, Opacus, TenSEAL, Bcrypt, Locust)
│   ├── app/
│   │   ├── config.py                                # Platform configuration, CORS whitelists & Sentry/env management
│   │   ├── dependencies.py                          # FastAPI Dependency Injection & Tenant Resolution
│   │   ├── main.py                                  # Application entrypoint, strict CORS, SecurityHeaders & error middleware
│   │   │
│   │   ├── domain/                                  # Enterprise Domain Entities, Value Objects & Security Invariants
│   │   │   ├── entities.py                          # Core domain entities (Bank, Transaction, Alert, ModelCheckpoint)
│   │   │   ├── entities_phase2.py                   # Extended consortium entities & audit tracking
│   │   │   ├── value_objects.py                     # Immutable value objects (NormalizedTransaction, ModelWeights)
│   │   │   ├── value_objects_phase2.py              # HMAC salted identifiers & tokenized entity descriptors
│   │   │   ├── value_objects_pqc.py                 # NIST Level 3/5 Kyber-768 & Dilithium-3 metadata
│   │   │   ├── value_objects_rdp.py                 # Rényi Differential Privacy accounting structures
│   │   │   ├── value_objects_zkp.py                 # Groth16 zk-SNARK attestation proof structures
│   │   │   ├── value_objects_unlearning.py          # Exact / Approximate federated unlearning definitions
│   │   │   ├── value_objects_copilot.py             # AML agentic copilot reasoning & investigation artifacts
│   │   │   ├── value_objects_bridge.py              # Layer-2 cross-chain settlement bridge envelopes
│   │   │   ├── model_lifecycle.py                   # Champion / Challenger state machine & rollback policies
│   │   │   ├── model_governance.py                  # SR 11-7 model risk governance, bias audit & audit trail
│   │   │   ├── byzantine_defense.py                 # Krum, Trimmed Mean, Bulyan & Coordinate Median rules
│   │   │   ├── spectral_defense.py                  # Spectral SVD top eigenvalue backdoor anomaly filter
│   │   │   ├── fuzzy_psi.py                         # MinHash LSH Private Set Intersection algorithm
│   │   │   ├── psi_service.py                       # Zero-PII cross-bank entity intersection engine
│   │   │   ├── ai_act_compliance.py                 # EU AI Act risk classification, transparency & audit invariants
│   │   │   ├── consortium_policy.py                 # Consortium governance rules, quorum & voting invariants
│   │   │   ├── consortium_governance.py             # Node membership lifecycle & cryptographic attestation
│   │   │   ├── distribution_fidelity_service.py     # Jensen-Shannon & Wasserstein distribution divergence auditor
│   │   │   ├── realtime_explainer.py                # Sub-ms fast decision attribution & cached async SHAP engine
│   │   │   ├── case_management.py                   # Four-Eyes dual supervisor approval state machine
│   │   │   ├── security_evaluator.py                # Membership Inference Attack (MIA) privacy evaluator
│   │   │   ├── regional_governance.py               # Cross-jurisdiction data sovereignty & residency guards
│   │   │   ├── tenant_management.py                 # Multi-tenant cryptographic isolation invariants
│   │   │   ├── incident_playbook.py                 # Automated incident triage & containment procedures
│   │   │   ├── inference_fallback.py                # Graceful degradation & shadow scoring fallbacks
│   │   │   ├── label_privacy_guard.py               # Differential privacy gradient perturbation invariants
│   │   │   ├── protocol_versioning.py               # Zero-downtime protocol compatibility negotiation
│   │   │   ├── quorum_manager.py                    # Byzantine fault tolerance consortium quorum manager
│   │   │   ├── retention_policy.py                  # GDPR Art. 17 right-to-be-forgotten zeroization policies
│   │   │   ├── sla_contract.py                      # Sub-100ms inference SLA contract & latency bounds
│   │   │   ├── async_fl_engine.py                   # Asynchronous federated learning domain coordination
│   │   │   ├── benchmark_runner.py                  # Multi-dataset empirical performance evaluator
│   │   │   ├── enums.py                             # Platform domain enumerations (RiskDecision, NodeStatus, etc.)
│   │   │   ├── dr_coordinator.py                    # Disaster recovery domain state & leader election bounds
│   │   │   ├── deployment_state.py                  # Zero-downtime canary deployment state descriptors
│   │   │   ├── backup_record.py                     # Immutable cryptographic database backup metadata
│   │   │   ├── data_validator.py                    # Ingested payment data contract validation & schema conformance
│   │   │   ├── metrics_service.py                   # Scientific validation metrics (PR-AUC, Recall@0.1% FPR, latency)
│   │   │   └── web_console.py                       # Web console telemetry & audit logging contracts
│   │   │
│   │   ├── application/
│   │   │   ├── schemas/                             # Clean Architecture Pydantic v2 Contract Envelopes & DTOs (39 Modules)
│   │   │   └── services/                            # Application Use Cases & Core Orchestration Services
│   │   │       ├── fl_engine.py                     # Server-side FL parameter aggregation (FedAvg, SCAFFOLD, Byzantine defenses)
│   │   │       ├── flower_engine.py                 # Flower FL simulation bridge (Ray runtime & zero-mock native fallback)
│   │   │       ├── flower_p2p_engine.py             # Serverless P2P gossip engine (Ring/Mesh topologies, Byzantine defenses)
│   │   │       ├── fl_dirichlet_partitioner.py      # Non-IID Dirichlet label skew partitioner (alpha <= 0.50)
│   │   │       ├── fl_hyperparameter_optimizer.py   # Optuna automated federated hyperparameter tuner
│   │   │       ├── privacy_service.py               # Opacus DP guard (L2 norm clipping C=1.0 & Gaussian noise)
│   │   │       ├── privacy_audit_service.py         # Rényi DP cumulative budget tracker & empirical MIA auditor
│   │   │       ├── risk_engine.py                   # 9-Signal composite risk scoring engine (<100ms)
│   │   │       ├── feature_store_service.py         # Online low-latency entity feature retrieval service (<5ms)
│   │   │       ├── policy_engine.py                 # In-memory rule execution with cache invalidation
│   │   │       ├── graph_embedding_service.py       # PyTorch GraphSAGE relational embedding service
│   │   │       ├── graph_embedding_model.py         # Inductive GraphSAGE GNN architecture (64/128-dim)
│   │   │       ├── graph_analytics_service.py       # Multi-hop graph traversal & PageRank anomaly service
│   │   │       ├── graph_engine.py                  # Graph database synchronization & cypher queries
│   │   │       ├── streaming_graph_service.py       # Dynamic streaming graph edge updates & cache
│   │   │       ├── streaming_gnn_model.py           # Real-time online streaming Graph Neural Network
│   │   │       ├── flink_graph_streaming.py         # Distributed streaming pipeline connector
│   │   │       ├── coordinator_service.py           # Federation round coordinator & consensus orchestrator
│   │   │       ├── case_service.py                  # Core case investigation lifecycle state machine
│   │   │       ├── case_workbench.py                # 6-Stage case management workbench & supervisor signatures
│   │   │       ├── bridge_case_service.py           # Inter-bank encrypted FININT messaging & case information exchange
│   │   │       ├── payment_recall_service.py        # Real-time SEPA Instant payment recall (camt.056 / camt.029) & recovery ledger
│   │   │       ├── screening_service.py             # Real-time sanctions (UN/EU/OFAC) and PEP fuzzy screening engine
│   │   │       ├── fiu_regulatory_service.py        # European FIU & UNODC goAML 4.0 XML / AMLA regulatory exporter
│   │   │       ├── open_aml_service.py              # Drop-in Enterprise AML OpenAPI adapter & signed webhook gateway
│   │   │       ├── ubo_graph_service.py             # Corporate UBO intelligence, circular ownership & nominee detection
│   │   │       ├── european_scenario_library.py     # 16 European banking AML scenarios & hybrid deterministic rule engine
│   │   │       ├── asset_recovery_service.py        # Asset Recovery & Collaborative FININT Operational Hub (MTTR & ROI Console)
│   │   │       ├── drift_service.py                 # PSI & Jensen-Shannon feature drift detector
│   │   │       ├── auto_rollback.py                 # Champion auto-rollback on drift or accuracy degradation
│   │   │       ├── automated_retraining.py          # Continuous automated retraining trigger pipeline
│   │   │       ├── retraining_trigger_engine.py     # Drift threshold evaluation & model fine-tuning scheduler
│   │   │       ├── elliptic_benchmark_service.py    # Elliptic Bitcoin transaction graph benchmark evaluator
│   │   │       ├── entity_resolution.py             # Cross-bank MinHash fuzzy entity resolution service
│   │   │       ├── explainability_service.py        # Real-time SHAP Kernel & feature attribution generator
│   │   │       ├── financial_message_parser.py      # ISO 20022 (pacs.008, camt.053) & SWIFT MT103 parser
│   │   │       ├── data_generator.py                # Synthetic multi-bank transaction & typology generator
│   │   │       ├── data_validator.py                # Pandera schema & distribution fidelity validator
│   │   │       ├── dataloader.py                    # Real dataset loaders (Elliptic, PaySim, IEEE-CIS)
│   │   │       ├── bank_onboarding_service.py       # Automated bank onboarding & credential provisioning
│   │   │       ├── alert_service.py                 # Real-time fraud alert triage & notification engine
│   │   │       ├── regulatory_reporter.py           # FinCEN BSA SAR XML report compiler
│   │   │       ├── retention_engine.py              # Data retention TTL scheduler & automated zeroization
│   │   │       ├── simulation_service.py            # FL simulation orchestrator & synthetic scenarios
│   │   │       ├── scenario_service.py              # Fraud typology scenario catalog & execution
│   │   │       ├── sla_monitor.py                   # Sub-100ms latency p50/p95/p99 SLA monitor
│   │   │       ├── sla_contract_engine.py           # Node SLA enforcement & penalty accounting
│   │   │       ├── security_compliance.py           # Automated SOC 2 evidence collection & policy engine
│   │   │       ├── aml_agentic_copilot.py           # Autonomous AML investigative agent & narrative compiler
│   │   │       ├── federated_unlearning_engine.py   # GDPR Right-to-be-Forgotten federated unlearning
│   │   │       ├── kms_service.py                   # Envelope encryption & HSM key management service
│   │   │       ├── incident_triage.py               # Autonomous security incident response & mitigation
│   │   │       ├── tenant_metering.py               # Multi-tenant API metering, quotas & rate controls
│   │   │       ├── webhook_service.py               # HMAC-SHA256 signed asynchronous webhook dispatcher
│   │   │       ├── zero_downtime_deployer.py        # Rolling zero-downtime model deployment coordinator
│   │   │       ├── model_registry.py                # Model versioning, lineage tracking & canary promoter
│   │   │       ├── model_service.py                 # Local client training (FedProx proximal loss, SCAFFOLD drift correction, Opacus DP)
│   │   │       ├── adversarial_service.py           # Adversarial attack simulation (sign-flip, label-flip, backdoor)
│   │   │       ├── consortium_service.py            # Multi-bank consortium voting & policy management
│   │   │       ├── etl_service.py                   # Batch ETL pipeline & feature precomputation
│   │   │       ├── idempotency.py                   # Distributed request deduplication & idempotency keys
│   │   │       ├── label_feedback_pipeline.py       # Human-in-the-loop analyst feedback ingestion
│   │   │       ├── streaming_engine.py              # Low-latency Kafka / Redis streaming processor
│   │   │       ├── connector_diagnostics_service.py # Enterprise connector health, transport ping & probe engine
│   │   │       ├── design_partner_service.py        # Enterprise design partner pilot onboarding & trial sandbox provisioning
│   │   │       ├── metrics_service.py               # Empirical validation metrics aggregator & cross-bank benchmark service
│   │   │       ├── psi_service.py                   # Private Set Intersection (DH-PSI / Fuzzy MinHash) application service
│   │   │       └── support_diagnostics.py           # Automated health diagnostics & support bundle generator

│   │   │
│   │   ├── infrastructure/                          # Infrastructure & External Technology Adapters
│   │   │   ├── security/                            # Enterprise Security Suite & Cryptographic Drivers
│   │   │   │   ├── auth_service.py                  # Enterprise auth, short-lived JWTs (15m), refresh rotation, brute-force lockout
│   │   │   │   ├── password_hasher.py               # Bcrypt password hashing (cost=12) with salted digests
│   │   │   │   ├── error_handler.py                 # Production error sanitization, zero stack trace leakage & Sentry hook
│   │   │   │   ├── security_headers.py              # Comprehensive HTTP security headers (CSP, HSTS, X-Frame-Options, nosniff)
│   │   │   │   ├── p2p_secagg_driver.py             # Curve25519 ECDH pairwise SecAgg zero-sum masking
│   │   │   │   ├── shamir_engine.py                 # Shamir (t, n) threshold secret sharing over GF(p)
│   │   │   │   ├── abac_engine.py                   # Attribute-Based Access Control policy engine
│   │   │   │   ├── vault_client.py                  # HashiCorp Vault PKI & secret manager integration
│   │   │   │   ├── vault_hsm_pki_binder.py          # Vault PKI & HSM root CA binder
│   │   │   │   ├── hsm_signer.py                    # Hardware Security Module (PKCS#11) cryptographic signer
│   │   │   │   ├── hsm_key_service.py               # Hardware Security Module (HSM) PKCS#11 & Vault Transit Zero-Trust key service
│   │   │   │   ├── mtls_manager.py                  # mTLS certificate lifecycle, rotation, SAN & CRL revocation
│   │   │   │   ├── cert_generator.py                # Self-signed and X.509 development certificate generator
│   │   │   │   ├── perimeter_waf.py                 # Perimeter firewall, OWASP Top 10 rules & lockout tracking
│   │   │   │   ├── rate_limiter.py                  # slowapi granular route quotas & DDoS sliding window protection
│   │   │   │   ├── immutable_audit_chain.py         # Tamper-evident append-only SHA-256 cryptographic audit chain
│   │   │   │   ├── adaptive_dp_autoscaler.py        # Dynamic (epsilon, delta) budget autoscaler on distribution drift
│   │   │   │   ├── oidc_authenticator.py            # OpenID Connect (OIDC) JWT provider integration
│   │   │   │   ├── signature_verifier.py            # eIDAS QWAC/QSeal & ECDSA digital signature verifier
│   │   │   │   ├── compression_engine.py            # Gradient quantization & Zstandard compression engine
│   │   │   │   ├── secure_parameter_pipeline.py     # End-to-end encrypted gradient aggregation pipeline
│   │   │   │   ├── tenant_kms.py                    # Multi-tenant envelope encryption & key rotation
│   │   │   │   ├── smart_contract_driver.py         # Web3 JSON-RPC provider & smart contract caller
│   │   │   │   ├── gnosis_multisig_coordinator.py   # Multi-signature consortium governance coordinator
│   │   │   │   ├── fhe_driver.py                    # [Research] TenSEAL CKKS Homomorphic Encryption driver
│   │   │   │   ├── pqc_secagg_driver.py             # [Research] Post-Quantum CRYSTALS-Kyber-768 SecAgg driver
│   │   │   │   ├── tee_driver.py                    # [Research] Hardware TEE Intel SGX / AWS Nitro driver
│   │   │   │   ├── zk_snark_verifier.py             # [Research] Groth16 zk-SNARK Poseidon proof verifier
│   │   │   │   └── layer2_crosschain_bridge.py      # [Research] Layer-2 cross-chain settlement bridge (Chainlink CCIP)
│   │   │   │
│   │   │   ├── database/                            # SQLAlchemy Async ORM, engine, search_path isolation & DDL quoting
│   │   │   │   ├── __init__.py                      # Engine pool, multi-tenant session factory & metadata migration
│   │   │   │   ├── tenant_provisioner.py            # PostgreSQL schema & SQLite dynamic tenant database provisioner
│   │   │   │   ├── migration_manager.py             # Programmatic Alembic database migration runner & auto-stamping
│   │   │   │   └── migrations/                      # Alembic versioned schema migrations
│   │   │   │       ├── env.py                       # Dynamic DB URL, multi-tenant schema runner & SQLite batch mode
│   │   │   │       └── versions/                    # Linear migration revision scripts
│   │   │   │           ├── 001_production_domain_tables.py # Core production domain tables DDL
│   │   │   │           └── 002_core_and_aml_tables.py      # Core AML, simulation & evidence tables DDL
│   │   │   │
│   │   │   ├── connectors/                          # ISO 20022, SWIFT, Streaming & Message Queue Ingestion
│   │   │   │   ├── factory.py                       # Dynamic connector factory & protocol registry
│   │   │   │   ├── base_connector.py                # Abstract ingestion connector interface
│   │   │   │   ├── iso20022_connector.py            # pacs.008 & camt.053 XML message ingestion adapter
│   │   │   │   ├── open_banking_connector.py        # PSD2 Open Banking REST webhook ingestion adapter
│   │   │   │   ├── rest_connector.py                # Core banking REST API HTTP ingestion adapter
│   │   │   │   ├── kafka_connector.py               # Apache Kafka distributed streaming topic consumer
│   │   │   │   ├── rabbitmq_connector.py            # RabbitMQ AMQP message broker queue consumer
│   │   │   │   ├── redis_connector.py               # Redis Streams / PubSub message ingest adapter
│   │   │   │   ├── parquet_connector.py             # High-throughput columnar Parquet batch reader
│   │   │   │   ├── batch_connector.py               # Batch CSV/JSON file ingestion pipeline
│   │   │   │   ├── streaming_connector.py           # Real-time WebSocket / TCP stream ingestion adapter
│   │   │   │   ├── kafka_streaming_connector.py     # CNCF CloudEvents 1.0 & Apache Kafka event bus connector (DLQ & idempotency)
│   │   │   │   ├── mambu_connector.py               # Mambu cloud core banking v2 REST/webhook connector
│   │   │   │   └── thought_machine_connector.py     # Thought Machine Vault Core posting instruction batch streaming connector
│   │   │   │
│   │   │   ├── feature_store/                       # Low-Latency Online/Offline Feature Store
│   │   │   │   ├── store.py                         # Unified online feature store interface
│   │   │   │   ├── redis_store.py                   # Sub-2ms Redis key-value online feature backend
│   │   │   │   ├── feast_store.py                   # Feast enterprise feature store integration
│   │   │   │   ├── rolling_aggregators.py           # 1h, 24h, 7d velocity & amount z-score sliding windows
│   │   │   │   └── bloom_filter.py                  # Probabilistic entity membership Bloom filter
│   │   │   │
│   │   │   ├── disaster_recovery/                   # Multi-Region Failover & Business Continuity
│   │   │   │   ├── region_failover.py               # Automated active-passive standby region promotion (RTO < 30s)
│   │   │   │   ├── chaos_dr_drill.py                # Simulated split-brain & datacenter outage chaos runner
│   │   │   │   └── backup_verifier.py               # Automated cryptographic database backup & restore verifier
│   │   │   │
│   │   │   ├── telemetry/                           # Enterprise Observability & Distributed Tracing
│   │   │   │   ├── __init__.py                      # Prometheus metrics exporter (TPS, p50/p95/p99 latency, SLA counters)
│   │   │   │   └── otel_tracer.py                   # OpenTelemetry (OTel) OTLP distributed trace propagator
│   │   │   │
│   │   │   ├── repositories/                        # Clean Architecture Database Repositories
│   │   │   │   ├── alert_repository.py              # Real-time alert persistence & query filters
│   │   │   │   ├── bank_repository.py               # Bank institution registration & credential store
│   │   │   │   ├── case_repository.py               # Investigation cases & supervisor signature history
│   │   │   │   ├── entity_repository.py             # Resolved entity profiles & graph node mappings
│   │   │   │   ├── round_repository.py              # Federated learning training rounds & weight checkpoints
│   │   │   │   ├── metrics_repository.py            # Historical benchmark & telemetry time-series store
│   │   │   │   └── simulation_repository.py         # Synthetic simulation scenario runs & outcome states
│   │   │   │
│   │   │   ├── grpc/                                # High-Performance gRPC Communication
│   │   │   │   ├── client.py                        # Asynchronous gRPC consortium client stub
│   │   │   │   ├── server.py                        # Multi-bank gRPC transport server
│   │   │   │   ├── servicer.py                      # Weight submission, model distribution & sync servicers
│   │   │   │   ├── types.py                         # Protobuf message type definitions & converters
│   │   │   │   ├── version_interceptor.py           # Protocol version compatibility interceptor
│   │   │   │   └── proto/                           # Protocol Buffer schema definitions (.proto)
│   │   │   │
│   │   │   ├── client_daemon/                       # Local Banking Client Daemon
│   │   │   │   └── hardware_detector.py             # Apple Silicon MPS / NVIDIA CUDA / CPU acceleration detector
│   │   │   ├── event_bus.py                         # Internal asynchronous in-process event pub/sub bus
│   │   │   ├── cache.py                             # In-memory LRU fast-path & multi-tier cache manager
│   │   │   ├── models.py                            # SQLAlchemy ORM database table models (Multi-Tenant)
│   │   │   ├── redis_store.py                       # Global Redis connection pool & atomic locking
│   │   │   ├── celery_app.py                        # Celery distributed async worker configuration
│   │   │   └── tenant_provisioner.py                # Tenant database migration & schema isolation provisioner
│   │   │
│   │   └── presentation/                            # API Gateway, REST Endpoints & WebSockets
│   │       ├── routers/                             # 43 Modular FastAPI Routers
│   │       │   ├── auth.py                          # Bcrypt authentication, short-lived JWT (15m), refresh rotation & lockout
│   │       │   ├── predict.py                       # Real-time transaction scoring & composite risk inference (<100ms)
│   │       │   ├── realtime_inference.py            # High-throughput batch & streaming inference endpoints
│   │       │   ├── alerts.py                        # Real-time fraud alert triage & disposition API
│   │       │   ├── cases.py                         # 6-Stage case management & Four-Eyes supervisor approval API
│   │       │   ├── bridge_messaging.py              # Inter-Bank Encrypted FININT Case Messaging & Information Request Protocol
│   │       │   ├── payment_recall.py                # Real-Time SEPA Instant Payment Recall Engine (camt.056/camt.029)
│   │       │   ├── screening.py                     # Real-Time Multi-List Sanctions & PEP Fuzzy Screening Engine
│   │       │   ├── regulatory.py                    # European FIU & UNODC goAML 4.0 XML / AMLA Regulatory Exporter
│   │       │   ├── open_aml_adapter.py              # Drop-in Enterprise AML OpenAPI & Webhook Ingestion Gateway
│   │       │   ├── ubo_graph.py                     # Corporate UBO Graph, Cycle Detection & Nominee Syndicates API
│   │       │   ├── european_scenarios.py            # European AML Monitoring Scenarios & Hybrid Deterministic Rule Engine API
│   │       │   ├── asset_recovery.py                # Asset Recovery & Collaborative FININT Operational Hub (MTTR & ROI Console)
│   │       │   ├── core_banking_gateway.py          # Cloud Core Banking Gateway (Mambu v2 & Thought Machine Vault Core)
│   │       │   ├── banks.py                         # Consortium member management & data upload endpoints
│   │       │   ├── bank_client.py                   # Distributed bank client local training & evaluation daemon
│   │       │   ├── coordinator.py                   # Federation round orchestration & model sync API
│   │       │   ├── training.py                      # Local and distributed federated training triggers
│   │       │   ├── model_registry.py                # Model checkpoint registry, canary promotion & rollback API
│   │       │   ├── entities.py                      # Entity resolution, graph nodes & identity profile API
│   │       │   ├── graph.py                         # Knowledge graph traversal, multi-hop paths & PageRank API
│   │       │   ├── security.py                      # Enterprise Security Suite status, ABAC eval & audit chain API
│   │       │   ├── compliance.py                    # SOC 2 automated compliance & EU AI Act audit reports
│   │       │   ├── onboarding.py                    # Automated bank node onboarding & mTLS certificate bundle API
│   │       │   ├── simulation.py                    # Synthetic scenario execution & FL benchmark runner API
│   │       │   ├── scenarios.py                     # Fraud typology simulation & interactive chaos attack injection API
│   │       │   ├── datasets.py                      # Real dataset preview, Great Expectations contract gating & consortium enrollment API
│   │       │   ├── dashboard.py                     # Executive metrics, risk breakdown & real-time KPI aggregates
│   │       │   ├── monitoring.py                    # Prometheus health, latency SLA & system resource metrics
│   │       │   ├── optimization.py                  # Federated hyperparameter tuning & Optuna trial status API
│   │       │   ├── privacy_defense.py               # Differential Privacy budget & membership inference defense
│   │       │   ├── psd2.py                          # Open Banking PSD2 / SCA compliance & risk API
│   │       │   ├── financial_messages.py            # Extended ISO 20022 Financial Rails (camt.053/pacs.002/pacs.003) API
│   │       │   ├── regulatory_dossier.py            # EU AI Act High-Risk & SR 11-7 Model Validation Dossier API
│   │       │   ├── rules.py                         # Business rule engine management & threshold tuning API
│   │       │   ├── settlement.py                    # Consortium token settlement & incentive distribution API
│   │       │   ├── diagnostics.py                   # Enterprise connector diagnostics & active test probes API
│   │       │   ├── webhook_gateway.py               # Asynchronous webhook registration & HMAC verification API
│   │       │   ├── design_partner.py                # Enterprise design partner portal & trial provisioning API
│   │       │   ├── feedback.py                      # Human-in-the-loop analyst feedback, labeling & retraining buffer API
│   │       │   ├── maintenance_cron.py              # Automated background maintenance, cache eviction & zeroization
│   │       │   ├── health.py                        # Liveness (/health) and Readiness (/ready) probe endpoints
│   │       │   ├── admin_console.py                 # Administrative cluster operations & operator tools
│   │       │   ├── gateway.py                       # Multi-tenant API routing & header normalization gateway
│   │       │   └── copilot.py                       # Autonomous agentic AML copilot & evidence assembly API
│   │       └── websockets/                          # Real-Time Streaming Channels
│   │           ├── manager.py                       # WebSocket ConnectionManager, broadcast channels & ping heartbeats
│   │           ├── streaming_ws.py                  # Live transaction stream & composite risk scoring feed
│   │           └── training_ws.py                   # Real-time federated training round progress & weight metrics
│   │
│   └── tests/                                       # Comprehensive Backend Test Suite (2,514 Tests)
│       ├── unit/                                    # Unit tests for domain invariants, services, security, attack injector & data contracts
│       ├── integration/                             # End-to-end API, gRPC, database & multi-tenant integration tests
│       ├── mutation/                                # AST boundary & fault injection mutant suites (86.2% backend AST kill rate)
│       └── property/                                # Hypothesis property-based mathematical invariance tests
│
├── frontend/                                        # React 19 / Vite TypeScript Web Console
│   ├── middleware.ts                                # Vercel Security Middleware (Node.js runtime & security guards)
│   ├── e2e-workflows/                               # Playwright Real-Browser Multi-Device E2E Suite (10 Tests)
│   ├── e2e-visual/                                  # Playwright Visual Regression Suite (Strict baseline comparison)
│   │   ├── auth_session_flow.spec.ts                # Session token lifecycle, navigation & security header validation
│   │   ├── federated_training_lifecycle.spec.ts     # FL coordinator rounds, weight sync & live telemetry convergence
│   │   ├── investigation_four_eyes_sar.spec.ts      # Four-Eyes dual supervisor approval & FinCEN SAR XML export
│   │   ├── chaos_attack_simulation.spec.ts          # Interactive Byzantine gradient injection & Krum quarantine
│   │   └── dataset_custom_ingest_flow.spec.ts       # CSV/Parquet drag-and-drop & GE data contract gating
│   ├── src/
│   │   ├── pages/                                   # 19 Enterprise Web Console Views
│   │   │   ├── LandingPage.tsx                      # High-converting SaaS landing page, interactive demo & feature matrices
│   │   │   ├── Dashboard.tsx                        # Executive KPI dashboard, risk distributions & fraud metrics
│   │   │   ├── LiveOperationsView.tsx               # Real-time transaction streaming terminal & manual transaction scoring
│   │   │   ├── InvestigationDashboard.tsx           # Multi-stage alert investigation workbench & evidence timeline
│   │   │   ├── CaseDetailPage.tsx                   # Case detail view, Four-Eyes supervisor signing & SAR XML export
│   │   │   ├── CasesPage.tsx                        # Active cases index, severity filtering & SLA countdown timers
│   │   │   ├── AlertsPage.tsx                       # Live alert feed, triage actions & bulk disposition controls
│   │   │   ├── GraphPage.tsx                        # Interactive 2D/3D knowledge graph visualizer & entity networks
│   │   │   ├── SecurityPage.tsx                     # Zero-Trust security posture, ABAC policy tester & audit ledger
│   │   │   ├── ObservabilityPage.tsx                # Prometheus SLA metrics, latency heatmaps & health telemetry
│   │   │   ├── PrivacyDefensePage.tsx               # Differential Privacy budget gauges & Membership Inference defense
│   │   │   ├── CoordinatorPage.tsx                  # Federated learning coordinator console & client node status
│   │   │   ├── BankOnboardingPage.tsx               # Self-service bank consortium onboarding & mTLS certificate wizard
│   │   │   ├── BenchmarkHubPage.tsx                 # Real-time benchmark comparison hub (FL vs. Isolated across 4 open datasets)
│   │   │   ├── ApiDocsPage.tsx                      # Interactive API documentation portal & live OpenAPI request runner (/developer)
│   │   │   ├── PoliciesPage.tsx                     # Dynamic AML risk policy rule manager & threshold tuning
│   │   │   ├── PsiPage.tsx                          # Private Set Intersection (Fuzzy PSI) cross-bank entity lookup
│   │   │   ├── SimulationView.tsx                   # Multi-bank round orchestrator, parameter distribution & live simulation launcher
│   │   │   └── ScenariosPage.tsx                    # Pre-packaged fraud typology attack scenario simulator
│   │   │
│   │   ├── components/                              # Modular UI Design System
│   │   │   ├── Navbar.tsx                           # Global navigation bar, environment badges & system status
│   │   │   ├── Predictor.tsx                        # Live transaction simulation & risk scoring widget
│   │   │   ├── BenchmarkLaunchModal.tsx             # 4-Dataset empirical benchmark launcher & scenario configuration modal
│   │   │   ├── PlatformLaunchModal.tsx              # Guided platform launch & tenant configuration modal
│   │   │   ├── CounterfactualWorkbench.tsx          # Actionable recourse & counterfactual explanation generator
│   │   │   ├── DatasetTrainingConfigPanel.tsx       # Non-IID Dirichlet distribution & client partition allocation panel
│   │   │   ├── DriftAnalytics.tsx                   # Feature drift detector & population stability index (PSI) charts
│   │   │   ├── FLRoundRunner.tsx                    # Multi-bank round orchestrator & real simulation launcher
│   │   │   ├── layout/                              # Responsive Application Layout & Navigation
│   │   │   │   ├── Header.tsx                       # Top utility bar, real-time connection status & theme toggle
│   │   │   │   ├── Sidebar.tsx                      # Multi-role navigation sidebar, active route indicators & badges
│   │   │   │   └── Layout.tsx                       # Unified responsive application container & content router
│   │   │   ├── common/                              # Shared Resilience & Core Utilities
│   │   │   │   └── ErrorBoundary.tsx                # React error boundary isolating component render failures
│   │   │   ├── dashboard/                           # Real-Time Operational & Governance Panels
│   │   │   │   ├── AdversarialDefensePanel.tsx      # Byzantine gradient defense & quarantine monitor
│   │   │   │   ├── ModelRegistryPanel.tsx           # Champion / Challenger model lifecycle & promotion
│   │   │   │   ├── PrivacyMonitor.tsx               # Differential Privacy epsilon budget consumption gauge
│   │   │   │   └── StreamingGNNPanel.tsx            # Dynamic Graph Neural Network streaming anomaly telemetry
│   │   │   ├── chaos/                               # Live Adversarial Attack Simulator & Interactive Chaos
│   │   │   │   └── ChaosAttackInjectorPanel.tsx     # 500 tx/s smurfing burst & Byzantine gradient poisoning panel
│   │   │   ├── charts/                              # High-Performance Consortium Verification Charts
│   │   │   │   ├── MetricsComparisonBarChart.tsx    # Multi-bank comparison across ROC-AUC, PR-AUC, F1, latency, FPR
│   │   │   │   ├── ConfusionMatrix.tsx              # Dynamic classification threshold confusion matrix
│   │   │   │   ├── ROCCurve.tsx                     # Empirical ROC curve overlay (Global FL vs. Local baselines)
│   │   │   │   ├── FeatureImportance.tsx            # SHAP & tree feature importance attribution bars
│   │   │   │   ├── LossChart.tsx                    # Multi-round training loss convergence curve
│   │   │   │   └── MetricsRadar.tsx                 # Multi-axis consortium compliance & performance radar
│   │   │   ├── ingestion/                           # Real Dataset Ingestion Studio & Schema Alignment
│   │   │   │   ├── DatasetDropzone.tsx              # Drag-and-drop CSV/Parquet uploader with pre-flight check
│   │   │   │   ├── SchemaMappingTable.tsx           # Interactive 9-signal canonical schema alignment preview
│   │   │   │   ├── DataContractAuditCard.tsx        # Great Expectations (GE 1.x) validation audit scorecard
│   │   │   │   ├── ConsortiumAssignmentPanel.tsx    # Multi-bank partition allocator & FL enrollment trigger
│   │   │   │   └── DatasetIngestionStudioModal.tsx  # Master 4-step wizard modal for enterprise data ingestion
│   │   │   └── ...                                  # UI badges, metric cards, modals & data tables
│   │   │
│   │   ├── api/                                     # API Integration Layer
│   │   │   ├── client.ts                            # Axios / Fetch client with JWT interception & retry logic
│   │   │   ├── queries.ts                           # TanStack React Query hooks & cache management
│   │   │   └── types.ts                             # Synchronized TypeScript schemas & response contracts
│   │   │
│   │   ├── services/                                # Client Services & Event Handlers
│   │   │   ├── websocketService.ts                  # Reconnecting WebSocket client for live transactions & training
│   │   │   └── soundService.ts                      # Auditory alerts for high-risk fraud detections (Synthesized Web Audio)
│   │   │
│   │   ├── hooks/                                   # Custom React Hooks
│   │   ├── utils/                                   # Cryptographic helpers, number formatters & mutant killers
│   │   │   └── piiSanitizer.ts                      # Luhn algorithm, IBAN/TCKN regex & Type-Salted HMAC Zero-PII sanitizer
│   │   └── e2e/                                     # Playwright end-to-end browser user workflow specs
│   │
│   └── tests/                                       # Vitest & React Testing Library Suite (313 Tests across 81 Files)
│
├── sdk/                                             # Official Consortium Client SDK
│   ├── README.md                                    # Top-level SDK overview & quick-start guide
│   ├── examples/                                    # Core banking integration & streaming transaction examples
│   │   └── reference_bank_connector.py              # End-to-end reference bank adapter implementation
│   └── python/                                      # Python 3.10+ Integration SDK (`cfi-connector-sdk`)
│       ├── cfi_connector_sdk/                       # SDK core package (Adapters, Client, Health Monitor)
│       ├── tests/                                   # SDK unit & integration test suite (11 Tests)
│       └── pyproject.toml                           # SDK packaging configuration
│
├── docs/                                            # Complete Technical Specifications & Architecture (38+ Docs)
│   ├── architecture.md                              # Master Clean Architecture system design specification
│   ├── architecture-phase2.md                       # Extended enterprise consortium & security specifications
│   ├── threat_model.md                              # Formal STRIDE threat model & attack surface analysis
│   ├── system_design.md                             # High-level component interactions & data flow blueprints
│   ├── aml-platform.md                              # AML/CFT compliance platform architecture & SAR workflows
│   ├── realtime_inference_api.md                    # Real-time scoring API contract & empirical load test SLAs
│   ├── sla_slo_contract_spec.md                     # Enterprise SLA/SLO contract terms & error budget accounting
│   ├── bank_onboarding_guide.md                     # Step-by-step consortium bank node onboarding guide
│   ├── incident_response_playbook.md                # Security incident response & automated containment playbooks
│   ├── disaster_recovery_plan.md                    # Business continuity & active-passive region failover plan
│   ├── model_risk_management_sr11_7.md              # Federal Reserve SR 11-7 model risk management compliance
│   ├── saas_multitenancy.md                         # Cryptographic multi-tenant isolation & BOLA defense
│   ├── security_controls_matrix.md                  # Comprehensive security controls & compliance mapping
│   ├── engineering_decisions.md                     # Architecture Decision Records (ADRs) & technical trade-offs
│   ├── real_world_benchmarks.md                     # Empirical validation on PaySim, IEEE-CIS & Elliptic datasets
│   └── ...                                          # Additional operational, API, and deployment documentation
│
├── verification/                                    # 18 Scientific Subsystem Self-Verification Modules
│   ├── README.md                                    # Master scientific audit catalog & mathematical verification index
│   ├── mathematical/                                # Master mathematical protocol & 35 formal invariant proofs
│   ├── federated_learning/                          # FL convergence, Non-IID Dirichlet skew & optimizer audits
│   ├── differential_privacy/                        # Opacus DP noise scale & Rényi DP accounting verification
│   ├── secure_aggregation/                          # Curve25519 SecAgg pairwise masking & Shamir recovery audits
│   ├── zero_trust_pki/                              # Vault PKI, mTLS certificate lifecycles & ABAC policy audits
│   ├── risk_scoring/                                # 9-Signal composite scoring & sub-100ms inference SLA audit
│   ├── explainability/                              # SHAP feature attributions & counterfactual generator audits
│   ├── real_data_benchmark/                         # Elliptic Bitcoin AML graph dataset empirical benchmark
│   ├── api/                                         # 100% REST endpoint & Pydantic schema contract verification
│   ├── telemetry/                                   # Prometheus metrics export & OpenTelemetry trace audit
│   ├── audit_logging/                               # Immutable SHA-256 audit chain & tamper-evidence verification
│   ├── connectors/                                  # ISO 20022 & SWIFT message parser conformance audits
│   ├── etl_pipeline/                                # Pandera data contracts & distribution bounds validation
│   ├── drift_detection/                             # Population Stability Index (PSI) drift detection audit
│   ├── federation_coordinator/                      # Consortium consensus, round quorum & canary promotion audit
│   ├── graph_intelligence/                          # Inductive GraphSAGE relational embedding verification
│   ├── smart_contracts/                             # Consortium incentive settlement smart contract audit
│   └── terraform_iac/                               # Multi-cloud Terraform IaC & Cloudflare perimeter security audit
│
├── deployments/                                     # Infrastructure as Code (IaC) & Cloud Orchestration
│   ├── terraform/                                   # Multi-cloud Terraform IaC (AWS, GCP, Azure, Cloudflare WAF)
│   ├── kubernetes/                                  # Production K8s manifests, HPA, NetworkPolicies & Istio mTLS
│   ├── helm/                                        # Production Helm charts for distributed consortium deployment
│   ├── argocd/                                      # GitOps continuous delivery Application manifests
│   └── docker/                                      # Container specifications for API, client daemons & workers
│
├── helm/                                            # Standalone Unified Kubernetes Helm Chart
│   ├── README.md                                    # Architecture, values reference & verification guide
│   └── cfi-platform/                                # Unified application chart (Deployment, Service, HPA, Ingress)
│
├── monitoring/                                      # Production Observability & Telemetry Stacks
│   ├── README.md                                    # Telemetry architecture, metric dictionaries & SLA alerts
│   ├── prometheus.yml                               # Prometheus server configuration & scrape targets
│   ├── prometheus/                                  # Prometheus server configuration & SLA alert rules
│   ├── grafana/                                     # Provisioned Grafana dashboard JSON models
│   ├── alertmanager/                                # Alertmanager routing, Slack/PagerDuty notification rules
│   ├── loki/                                        # Loki centralized log aggregation configuration
│   └── promtail/                                    # Promtail log shipping agent configuration
│
├── storage/                                         # Persistent Data Target & Partitioned Artifacts
│   ├── README.md                                    # Storage layout, volume mounting & retention guide
│   ├── datasets/                                    # Parquet feature sets for federated learning (PaySim, etc.)
│   ├── regulatory_filings/                          # Generated SAR XML filings (FIU transmission payloads)
│   ├── retention_ledger/                            # GDPR Article 17 append-only erasure ledgers
│   ├── label_feedback/                              # Incremental active-learning feedback partitions
│   └── benchmarks/                                  # Pre-computed evaluation metrics and scenario results
│
├── contracts/                                       # Hardhat EVM Smart Contracts [Research]
│   ├── contracts/                                   # ConsortiumIncentiveSettlement.sol, GnosisSafeMultiSigCoordinator.sol
│   ├── stubs/                                       # Zero-dependency signing-key stub (vulnerability mitigation)
│   └── test/                                        # Hardhat Mocha/Chai contract unit tests & gas audits (31 Tests)
│
└── scripts/                                         # Developer Automation, Benchmarks, Load Testing & CLI Tooling
    ├── README.md                                    # CLI reference, benchmark workflows & validation guides
    ├── generate_sbom.py                             # Automated SPDX / CycloneDX SBOM generator & pip-audit runner
    ├── generate_secrets.py                          # One-click cryptographic 256-bit secret generator for .env
    ├── verify_docker_deployment.py                  # Automated Docker Compose pre-flight and runtime smoke test
    ├── run_load_test.py                             # High-throughput asynchronous load tester & SLA report generator
    ├── locustfile.py                                # Locust multi-user payment streaming load testing suite
    ├── realtime_benchmark.py                        # Sub-100ms in-process ASGI inference benchmark runner
    ├── transaction_stream.py                        # Event-driven real-time transaction streaming pipeline
    ├── run_elliptic_benchmark.py                    # Real Elliptic Bitcoin transaction graph benchmark runner
    ├── run_enterprise_stress_test.py                # High-throughput ISO 20022 payment stream stress test
    ├── run_abac_benchmark.py                        # ABAC authorization policy engine throughput benchmark
    ├── run_fl_synthetic_benchmark.py                # Synthetic multi-bank FL convergence benchmark runner
    ├── run_benchmark.py                             # 9-Configuration matrix empirical benchmark runner
    ├── ast_mutation_engine.py                       # Dynamic Python AST mutant generator & fault injection transformer
    ├── run_mutation_tests.py                        # Dynamic AST mutation test runner (86.2% backend AST kill rate / 90.2% composite score)
    ├── run_coverage_audit.py                        # Multi-dimensional coverage auditor with 75% regression gate (--cov-fail-under)
    ├── validate_k8s_manifests.py                    # Rendered Helm manifest dry-run validator (kubectl apply --dry-run=client)
    ├── run_all_tests.py                             # Unified cross-stack test runner (Backend pytest + Frontend vitest)
    ├── run_all_verifications.py                     # Master scientific verification runner (18 modules)
    ├── audit_api_contracts.py                       # REST endpoint, schema & TypeScript contract auditor
    ├── codebase_integrity_scanner.py                # Autonomous 22-vector zero-mock and dead-code scanner
    ├── cfi_cli.py                                   # Master platform operator & consortium management CLI
    ├── export_compliance_report.py                  # Automated EU AI Act & SOC 2 compliance report exporter
    ├── setup_cloudflare_waf.py                      # Automated Cloudflare WAF rule & rate limiter provisioner
    ├── init_vault_pki.py                            # Vault PKI root CA & consortium certificate bootstrapper
    ├── chaos_harness.py                             # Network latency injection & split-brain chaos test harness
    ├── download_real_benchmarks.py                  # Kaggle & public financial benchmark dataset downloader
    ├── benchmark_prepare_datasets.py                # PaySim, IEEE-CIS & Elliptic dataset preprocessor
    ├── etl_dataset_pipeline.py                      # Automated ETL feature extraction pipeline
    ├── capture_openapi_snapshot.py                  # OpenAPI JSON schema snapshot exporter
    ├── generate_plots.py                            # Benchmark performance & PR curve chart generator
    └── production_smoke_test.py                     # Zero-downtime production deployment smoke tester
```

---

## 4. Multi-Bank Synthetic Data & Multi-Standard Ingestion

### 4.1 Synthetic Multi-Bank Data Generator (`data_generator.py`)
Generates reproducible cross-bank transaction datasets across 3 distinct financial institutions (Bank Alpha, Bank Beta, Bank Gamma) modeling heterogeneous local fraud distributions (credit card velocity, structured wire transfers, cross-border layering) with customizable random seeds and noise profiles.

### 4.2 Multi-Standard Financial Payload Parser & Connectors (`financial_message_parser.py`, `iso20022_connector.py`, `open_banking_connector.py`)
Parses industry financial payload formats into a unified `NormalizedTransaction` schema:
- **ISO 20022 Messages:** `pacs.008` (Financial Interbank Credit Transfer) and `camt.053` (Bank-to-Customer Statement XML).
- **SWIFT MT Messages:** Legacy `MT103` Single Customer Credit Transfer.
- **PSD2 Open Banking:** Open Banking REST API webhook JSON payloads with eIDAS QWAC/QSeal signature parsing.

### 4.3 Data Contracts & Validation (`data_validator.py`)
- **Pandera Data Contracts:** Validates incoming DataFrame schema types, non-negative amounts, and ISO country codes.
- **Distribution Bounds Gating:** Asserts variance and mean boundaries prior to batch ingestion.

### 4.4 Real Dataset Ingestion Studio & Great Expectations Data Contracts (`datasets.py` & `piiSanitizer.ts`)

The platform includes an interactive enterprise ingestion studio allowing bank data scientists to import real transaction dumps into the consortium without centralizing PII or violating data contracts:

- **Client-Side Zero-PII Pre-Flight (`piiSanitizer.ts`):** 
  - Validates Card PANs using the **Luhn Algorithm Checksum**.
  - Identifies international IBANs and national identity identifiers (SSN/TCKN) before transmission.
  - Applies **Type-Salted HMAC-SHA256 Pseudonymization** in the browser, issuing a cryptographic `ZERO-PII VERIFIED` receipt.
  - Inspects Parquet `PAR1` magic byte headers directly in WebAssembly/browser memory.
- **Interactive Schema Alignment & Preview (`SchemaMappingTable.tsx`):**
  - Renders a 10-row tabular preview with automated heuristic column mapping to 9 canonical AML signals (`transaction_amount`, `timestamp`, `source_account_id`, `destination_account_id`, `channel_type`, `is_fraud`, etc.).
- **Great Expectations (GE 1.x) Contract Gating (`datasets.py`):**
  - Runs 12 automated validation rules on uploaded partitions (checking null bounds, non-negative monetary amounts, valid timestamps, and payment channel categories).
  - Computes Non-IID Dirichlet class concentration ($\alpha = 0.52$) and Kolmogorov-Smirnov distribution drift ($0.024$).
  - Isolates malformed or poisoned records into a quarantine bucket with a one-click downloadable audit file (`failed_records.csv`).
- **Consortium Node Allocation (`ConsortiumAssignmentPanel.tsx`):**
  - Assigns validated partitions to local bank nodes (`Bank Alpha`, `Bank Beta`, `Bank Gamma`, or guest `Bank Delta`) with partition replacement or append strategies.

---

## 5. Federated Learning Engines & Non-IID Optimization

### 5.1 Core Federated Learning Engine (`fl_engine.py` & `model_service.py`)
Orchestrates multi-client federated training rounds supporting canonical optimization and drift mitigation strategies:
1. **FedAvg (`AggregationMethod.FED_AVG` & `FED_AVG_WEIGHTED`):** Weighted/unweighted parameter averaging based on client dataset size $w_{t+1} = \sum_{k=1}^K \frac{n_k}{n} w_t^k$ (McMahan et al., 2017).
2. **FedProx (`AggregationMethod.FED_PROX`):** Canonical proximal regularization framework (Li et al., 2020) for heterogeneous and Non-IID banking networks. Local clients minimize $h_k(w; w_t) = F_k(w) + \frac{\mu}{2} \|w - w_t\|^2$ (`model_service.py:train_local`), bounding client drift from global consensus model $w_t$. Server-side aggregation performs sample-weighted consensus averaging while supporting runtime dynamic tuning of $\mu \in [0.001, 0.5]$ (benchmark default $\mu = 0.01$).
3. **SCAFFOLD (`AggregationMethod.SCAFFOLD`):** Corrects client-side gradient trajectories against drift ($g_i \leftarrow g_i - c_i + c$ in `model_service.py`) using control variates; server-side aggregation in `fl_engine.py` evaluates weighted parameter averaging while tracking global variate states $c$.
4. **FedAdam (`AggregationMethod.FED_ADAM`):** Server-side adaptive optimization with first ($m_t$) and second ($v_t$) moment tracking with bias correction.
5. **FedYogi (`AggregationMethod.FED_YOGI`):** Adaptive server-side optimization controlling directional update variance via sign-based second-moment updates $v_t \leftarrow v_t - (1 - \beta_2) \text{sign}(v_t - \Delta_t^2) \Delta_t^2$.
6. **FedAdagrad (`AggregationMethod.FED_ADAGRAD`):** Adaptive gradient server-side learning rate scaling for sparse update coordinates.
7. **MOON (Model-Contrastive FL):** Contrastive representation learning maximizing cosine similarity between local and global representations while pushing away previous local representations.
8. **Thread-Safe Simulation State Lifecycle & Concurrency Guard:** Multi-tenant simulation execution is protected by `threading.Lock()`, eliminating state collisions across concurrent simulation runs in `_server_m_by_sim`, `_server_v_by_sim`, `_server_round_by_sim`, and `_server_c_by_sim`.
9. **Zero-Memory-Leak State Pruning (`clear_simulation_state`):** Automatically clears server optimizer tensors and variate states upon simulation completion or failure, guaranteeing constant resident memory footprint in long-running SaaS deployments.

### 5.2 Dirichlet Non-IID Partitioning & Optuna Hyperparameter Tuning
- **Dirichlet Partitioner (`fl_dirichlet_partitioner.py`):** Models realistic bank label heterogeneity across institutions using the Dirichlet distribution:

$$
p_k \sim \text{Dirichlet}(\alpha \mathbf{p}), \quad \alpha \in [0.01, 10.0]
$$

  where lower concentration ($\alpha \le 0.50$) synthesizes severe non-IID class imbalance and higher concentration ($\alpha \to 10.0$) approximates uniform IID distributions.
- **Optuna Bayesian TPE Optimizer (`fl_hyperparameter_optimizer.py`):** Performs automated search over learning rate, local epochs, DP clipping bounds $C_{\text{max}}$, noise scale $\sigma$, and FedProx $\mu$ using `TPESampler` with early `MedianPruner` stopping.

### 5.3 Asynchronous Federated Learning Engine & Dynamic Quorum Coordination (`async_fl_engine.py`, `coordinator_service.py`, `quorum_manager.py`)
Provides non-blocking, asynchronous parameter updates (FedAsync; Xie et al., 2019) allowing fast participant banks to contribute weights continuously without waiting on high-latency straggler nodes:
1. **Staleness Attenuation Factor ($S(\tau)$):** Down-weights parameter updates from slower nodes based on staleness delay $\tau = t_{\mathrm{current}} - t_{\mathrm{submitted}}$ using polynomial decay with damping coefficient $\alpha$:

$$
S(\tau) = (1 + \tau)^{-\alpha}, \quad \alpha \in [0.1, 1.0]
$$

   Supported attenuation formulations also include exponential $S(\tau) = e^{-\alpha \tau}$, constant $S(\tau) = 1.0$, and hinge decay $S(\tau) = \min\left(1, \frac{1}{\alpha(\tau - 2) + 1}\right)$.
2. **Effective Learning Rate & Global Consensus Update:** Global model parameters $W^{(t+1)}$ are updated as:

$$
W^{(t+1)} = (1 - \eta \cdot S(\tau)) W^{(t)} + \eta \cdot S(\tau) W_{\mathrm{client}}
$$

3. **Straggler Bounded Cutoff ($\tau_{\max}$):** Updates with staleness delay exceeding $\tau_{\max} = 50$ are automatically dropped to prevent parameter degradation from ancient checkpoints.
4. **Byzantine & Non-Finite Defense:** Client parameter updates undergo strict numerical validation (`np.isfinite`); updates containing `NaN` or `Inf` are rejected with `ValueError`, keeping the global consensus model unpoisoned.
5. **Thread-Safe Mutex Lock:** All read and write operations on global weights and update histories are serialized via internal `threading.Lock()`, guaranteeing race-free multi-tenant concurrency.
6. **Dynamic Quorum Timeout Manager (`quorum_manager.py`):** Continuously monitors participant check-in progress across bank nodes. A round transitions to `QUORUM_REACHED` as soon as $\ge 60\%$ of active nodes submit, or to `TIMEOUT_EXPIRED` after the 300-second target window.
7. **Federation Coordinator REST APIs (`coordinator.py`):**
   - `POST /api/v1/coordinator/handshake`: Dynamic client registration and runtime compatibility validation.
   - `POST /api/v1/coordinator/heartbeat`: Periodic liveness check-in; drops unresponsive nodes after 15 seconds.
   - `GET /api/v1/coordinator/clients`: Active participant registry and capability profiles.
   - `GET /api/v1/coordinator/negotiate` & `POST /negotiate`: Heterogeneous hardware parameter negotiation (CUDA vs. CPU).
   - `POST /api/v1/coordinator/async-update`: Submit FedAsync staleness-attenuated parameter updates.
   - `GET /api/v1/coordinator/async-status`: Retrieve runtime staleness metrics and engine parameters.
   - `GET /api/v1/coordinator/quorum-status`: Inspect dynamic round quorum progress and countdown.
   - `POST /api/v1/coordinator/rounds/prune`: Free historical in-memory round data to maintain constant memory footprint.

---

## 6. Core Privacy-Enhancing Technologies: DP & SecAgg

### 6.1 Opacus Differential Privacy Guard (`privacy_service.py`)
Applies formal $(\epsilon, \delta)$-Differential Privacy to local model training rounds:
- **$L_2$ Gradient Norm Clipping ($C$):**

$$
\bar{g}_i = \frac{g_i}{\max\left(1, \frac{\|g_i\|_2}{C}\right)}
$$

- **Gaussian Noise Addition ($\sigma$):**

$$
\sigma = \frac{\sqrt{2 \ln(1.25/\delta)}}{\epsilon}, \quad \tilde{g}_i = \bar{g}_i + \mathcal{N}(0, \sigma^2 C^2 I)
$$

- **Rényi DP (RDP) Accounting:** Tracks cumulative privacy budget spend across training rounds to guarantee $\epsilon_{\text{total}} \le \epsilon_{\text{target}}$.

### 6.2 Curve25519 Pairwise Masking SecAgg (`p2p_secagg_driver.py`)
Implements zero-sum pairwise vector perturbation based on the Bonawitz et al. protocol:
- Client pairs derive shared secrets using Curve25519 ECDH key exchange: $s_{uv} = \text{HKDF}(\text{ECDH}(sk_u, pk_v))$.
- Masked updates satisfy the zero-sum invariant across all non-dropped clients:

$$
y_k = w_k + \sum_{j > k} s_{kj} - \sum_{j < k} s_{jk} \implies \sum_k y_k = \sum_k w_k
$$

- **Shamir (t, n) Threshold Secret Sharing (`shamir_engine.py`):** Shares secret keys across Galois prime field $\mathbb{Z}_p$ to reconstruct dropout masks if a node disconnects during aggregation.

### 6.3 Confidential Federated Unlearning (GDPR Art. 17) (`federated_unlearning_engine.py`)
Provides mathematically sound parameter erasure when a participating bank withdraws from the consortium or when an entity exercises GDPR Article 17 ("Right to Erasure / Right to be Forgotten"):
- **Exact Re-Aggregation:** Recomputes the global model parameters by evaluating FedAvg strictly over the stored model parameter contributions of all retained consortium members, excluding the targeted departing bank:

$$
\mathbf{w}_{\text{unlearned}} = \frac{1}{K - 1} \sum_{k \neq \text{target}} \mathbf{w}_k
$$

- **Lineage Subtraction:** When global consensus weights and the target bank's contribution are known from the immediate prior round, algebraically subtracts the target bank's influence without requiring full retraining from scratch:

$$
\mathbf{w}_{\text{unlearned}} = \frac{K \cdot \mathbf{w}_{\text{global}} - \mathbf{w}_{\text{target}}}{K - 1}
$$

- **Illustrative Simulator Fallback:** In confidential production federations with zero-knowledge secure aggregation where individual client parameter vectors are never persisted to disk (enforcing zero-raw-PII storage invariants), unlearning requests executed without stored gradient history run via an **illustrative simulator** (`UnlearningMethod.SIMULATED_UNLEARNING`). This honestly benchmarks parameter divergence and issues an unlearning audit receipt without claiming non-existent Hessian matrix inversion ($\mathbf{H}^{-1} \nabla \mathcal{L}$) or conjugate gradient solvers.
- **Structural Exclusion & Empirical MIA Guarantee:** In zero-raw-PII cross-bank settings, membership-inference attack risk after unlearning is not empirically measured without target client evaluation sets — instead, structural exclusion is mathematically guaranteed (the target bank's parameter contributions are verifiably excluded or algebraically subtracted from the global consensus checkpoint). When client evaluation samples (`y_true, y_pred_prob, member_mask`) are optionally provided, empirical loss-threshold shadow attack accuracy is measured via `MIAEvaluator` (`security_evaluator.py`).

---

## 7. Byzantine Poisoning Defense & Adversarial Robustness

### 7.1 Byzantine-Robust Aggregator Suite (`fl_engine.py`)
Resists adversarial or compromised client updates using robust aggregation rules:
- **Krum:** Selects the single client update that minimizes the sum of squared Euclidean distances to the closest $n - f - 2$ neighbors.
- **Trimmed Mean:** Computes coordinate-wise averages after trimming the top and bottom $\beta$ fraction of outlier values.
- **Coordinate-Wise Median:** Computes the element-wise median across parameter updates.
- **Bulyan:** Combines Krum candidate selection (selecting the $n - 2f$ lowest-distance candidates) with coordinate-wise trimmed mean.

### 7.2 Spectral SVD Backdoor Defense (`spectral_defense.py`)
Computes top Singular Value Decomposition (SVD) on parameter matrices to detect and quarantine anomalous gradient trajectories and backdoor triggers prior to aggregation.

### 7.3 Interactive Chaos & Adversarial Attack Simulator (`ChaosAttackInjectorPanel.tsx` & `scenarios.py`)

To empirically demonstrate defense mechanisms in real-time, the platform includes an interactive chaos injector panel embedded directly into the live operator consoles:

- **500 tx/s Smurfing / Layering Burst Interception:** 
  - Simulates a coordinated money laundering syndicate executing high-velocity micro-transfers ($4,850 – $9,950) across multiple consortium institutions.
  - GraphSAGE relational graph embeddings and MinHash LSH Private Set Intersection intercept the syndicate, demonstrating immediate velocity threshold escalation.
- **Byzantine Poisoned Gradient Attack ($\Delta w \times -10.0$):**
  - Simulates a compromised bank node (Bank Gamma) injecting inverted, malicious parameter weights to degrade the global model.
  - The **Krum / Bulyan Defense Shield** evaluates neighbor Euclidean distance sums ($\Delta = 48.2$, exceeding the distance threshold of $14.1$).
  - The malicious gradient is rejected, Bank Gamma is isolated with an immediate visual quarantine badge (`QUARANTINED BY KRUM`), and global model resilience is maintained.

> **Simulation Notice & Metric Classification:** In alignment with platform-wide transparency principles (as applied to illustrative unlearning and dropout simulators), the model accuracy telemetry (`auc_protected`, `auc_compromised_baseline`) displayed in the Chaos Attack Injector HUD is a **continuous live demo indicator / simulation proxy** derived from gradient cosine alignment and boundary strain:
> 
> $$\text{AUC}_{\text{protected}} = \text{clamp}\Big(0.9100, 0.9600, 0.9418 - 1.5(1 - \cos\theta) - \frac{\|\Delta w\|_2}{1200 \cdot \tau}\Big)$$
> 
> This responsive continuous function provides operators with immediate visual feedback on gradient deviation and defense recovery under active adversarial stress. It is explicitly labeled in the UI as **`SIMULATED DEMO`** and **`Simulated Proxy`**, clearly distinguishing it from offline holdout dataset evaluations measured on static open benchmarks in the [Empirical Benchmarks](#15-empirical-performance--benchmark-suite).

---

## 8. Graph Intelligence & Fuzzy Entity Resolution

### 8.1 PyTorch GraphSAGE Embeddings (`graph_embedding_service.py` & `graph_embedding_model.py`)
Trains inductive GraphSAGE models on local banking transaction graphs to produce $L_2$-normalized 64/128-dimensional entity embeddings, capturing multi-hop relational context across transaction networks.

### 8.2 Fuzzy Private Set Intersection (PSI) (`fuzzy_psi.py` & `entity_resolution.py`)
Uses MinHash Locality-Sensitive Hashing (LSH) to identify matching customer entities across institutions without sharing plain customer identifiers or raw database records.

### 8.3 Cross-Border Corporate UBO & Heterogeneous Graph Intelligence (`ubo_graph_service.py` & `ubo_graph.py`)
- **Multi-Tier Beneficial Ownership Graph Traversal:** Ingests complex corporate ownership structures as directed property graphs, tracing shareholding pathways from target legal entities (`ORG_*`) to ultimate natural persons (`PER_*`).
- **Compounded Indirect Shareholding Calculation:** Automatically compounds indirect equity stakes along directed paths across arbitrarily nested holding and nominee structures:
  $$\mathrm{Ownership}_{\mathrm{eff}}(u, e) = \sum_{p \in \mathcal{P}(u, e)} \prod_{(v, w) \in p} \mathrm{share}(v, w)$$
- **EU AMLD6 / 4AMLD 25% Statutory Threshold Gate:** Automatically flags natural persons whose cumulative direct and indirect effective equity or voting rights reach or exceed $\ge 25.0\%$ as primary Ultimate Beneficial Owners (UBOs).
- **Tarjan DFS Circular Ownership Loop Detection:** Applies depth-first cycle search algorithms to identify circular corporate layering rings ($\mathrm{Entity}_A \to \mathrm{Entity}_B \to \mathrm{Entity}_C \to \mathrm{Entity}_A$) engineered to obscure true controlling entities.
- **Shell Company & Nominee Syndicate Risk Clustering:** Evaluates corporate structures for asset-shielding indicators, including high-risk FATF secrecy jurisdictions, single-director nominee saturation across unrelated corporations, and opaque multi-jurisdictional shell cascades.

---

## 9. 9-Signal Composite Risk Engine & Model Explainability

### 9.1 Composite Risk Scoring Engine (`risk_engine.py` & `value_objects_phase2.py`)
Combines 9 independent risk signals into a unified risk score ($0 - 1000$):

$$
\text{Risk Score} = \text{round}\left(\min\left(1.0, \frac{\sum_{i=1}^{9} w_i S_i}{\sum_{i=1}^{9} w_i}\right) \times 1000, 1\right) \quad \text{where } \sum_{i=1}^{9} w_i = 1.00
$$

| Signal Name | Identifier | What It Evaluates in `risk_engine.py` | Weight |
|:---|:---|:---|:---:|
| `ml_prediction` | $S_{\text{ml}}$ | Supervised ML model fraud probability confidence ($0.0 - 1.0$) output by local/global neural classifier | 0.25 |
| `velocity_rules` | $S_{\text{velocity}}$ | Hourly transaction frequency ($v$ txns/hr), normalized via $\min(1.0, \max(0.0, (v - 2) / 8))$; $\ge 10$ txns/hr is max risk | 0.15
| `merchant_reputation` | $S_{\text{merchant}}$ | Blends merchant individual risk score ($60\%$) with category risk ($40\%$) from FATF lookup table `MERCHANT_RISK` | 0.10 |
| `country_risk` | $S_{\text{country}}$ | Evaluates originating ISO-2 country jurisdiction against FATF watchlist lookup table `COUNTRY_RISK` | 0.10 |
| `customer_history` | $S_{\text{history}}$ | Inverted customer tenure score ($1.0 - \text{history}$), adding $+0.30$ risk penalty if account age $< 30$ days | 0.10 |
| `device_anomaly` | $S_{\text{device}}$ | Originating channel/device risk heuristic (`phone_banking`=0.40, `atm`=0.35, `web`=0.15, `mobile`=0.10, `pos`=0.05) | 0.08 |
| `previous_alerts` | $S_{\text{alerts}}$ | Entity's historical AML alert frequency from in-memory ledger, normalized via $\min(1.0, \text{count} / 5)$ ($5+$ alerts $\to 1.0$) | 0.08 |
| `chargeback_history` | $S_{\text{chargeback}}$ | Entity's historical chargeback dispute rate, normalized via $\min(1.0, \text{rate} \times 10)$ ($10\%$ chargeback $\to 1.0$) | 0.07 |
| `behavior_anomaly` | $S_{\text{behavior}}$ | Statistical amount deviation from entity baseline ($Z$-score $z = \|x - \mu\| / \sigma$), normalized via $\min(1.0, \max(0.0, (z - 1) / 3))$ | 0.07 |

> [!NOTE]
> **Architectural Clarification on Graph Intelligence (GraphSAGE):** Graph-based anomaly detection (FedGNN / GraphSAGE embeddings and PageRank centrality) is implemented as an independent, asynchronous graph intelligence pipeline ([Section 8](#8-graph-intelligence--fuzzy-entity-resolution), `graph_analytics_service.py`, `streaming_gnn_model.py`), operating on multi-hop consortium transaction subgraphs. It is **not** one of the 9 signals evaluated in the real-time synchronous composite risk engine (`risk_engine.py`).

### 9.2 Model Explainability & Counterfactual Search (`explainability_service.py` & `risk_engine.py`)

The platform implements rigorous model explainability and actionable remediation simulation directly aligned with EU AI Act Article 13/14 transparency mandates and GDPR Article 22 human intervention requirements:

1. **Real-Time SHAP Explanations (`shap.KernelExplainer`):**
   - Feature importance is computed dynamically using a real `shap.KernelExplainer` running against the serving PyTorch neural network model (`FraudDetectionModel`) with a calibrated baseline reference distribution ($N=30$ normal transactions).
   - **Mathematical Additivity Axiom Guarantee:** For any input transaction vector $\mathbf{x}$, the local accuracy property holds strictly within floating point precision:

$$
\sum_{i=1}^{M} \phi_i(\mathbf{x}) + \mathbb{E}[f(X)] = f(\mathbf{x}) \quad (\text{Observed residual: } |\sum \phi_i + \text{base value} - f(\mathbf{x})| < 10^{-8})
$$
   - Each returned explanation includes the exact attribution $\phi_i$, the normalized feature value, the model output, expected base value, and an explicit `explanation_method: "shap_kernel_explainer"` provenance tag. Analytical heuristics are retained strictly as an emergency secondary fallback and are always explicitly labeled with `explanation_method: "fallback_heuristic"`.

2. **Real Counterfactual Remediation Simulator (`generate_counterfactuals`):**
   - Counterfactual generation does not use static point-deduction heuristics or canned rules. Instead, it executes an **iterative greedy local coordinate search** across the mutable transaction features (`country_code`, `transaction_amount`, `velocity`, `merchant_category`, `device_type`).
   - Every candidate perturbation step is evaluated by passing the mutated transaction directly through the real `RiskScoringEngine.score_transaction()`.
   - The returned `remediated_score` is the exact, empirical output of the risk scoring engine on the counterfactual transaction vector.
   - For an alerted transaction at risk score `850.0/1000` (flagged for `GEO-RISK`, `HIGH-AMT`, `VEL-001`, `MERCH-RISK`), the greedy search evaluates candidate perturbations, selects the minimal intervention path (`country_code: KP -> US`), and re-scores the transaction through the real engine down to `313.2/1000`, successfully clearing the `350.0` review threshold (`is_cleared: True`).

```
=== REAL VERIFIED EXECUTION TRACE (ExplainabilityService & RiskScoringEngine) ===
SHAP Expected Base Value:      0.473187
Model Inference Output f(x):   0.468756
Sum of SHAP Attributions:     -0.004431
Sum(SHAP) + Base Value:        0.468756 (Additivity Error: 0.0000000000)
Top Attributions:
  • device_type:              -0.007340 (raw: phone_banking, method: shap_kernel_explainer)
  • velocity:                 +0.006152 (raw: 12.5 txns/hr,   method: shap_kernel_explainer)
  • merchant_risk_score:      -0.003335 (raw: 0.85,           method: shap_kernel_explainer)
  • merchant_category:        +0.002905 (raw: crypto,         method: shap_kernel_explainer)

Counterfactual Search (Alert: 850.0 -> Target: 350.0):
  Step 1: Changed country_code from 'KP' to 'US' -> Real Engine Score: 850.0 -> 313.2 (delta -536.8)
  Result: CLEARED (Final Remediated Score: 313.2 <= 350.0 Threshold, 1 action required)
```

---

## 10. Multi-Layer Defense Gateway, Broken Access Control & Rate Limiting

The platform enforces a zero-trust multi-layer perimeter and application defense architecture designed to withstand volumetric denial-of-service, automated scraping, and broken access control exploits without degrading core ML scoring throughput:

```mermaid
flowchart LR
    Client["Client / Scraper / Attacker"] --> L1["1. Cloudflare Anycast Edge\n• L3/L4 DDoS Mitigation\n• Bot Fight Mode & Managed Challenge\n• Custom WAF Rules & TLS 1.3 Strict\n• Rate Limit: 60 req/10s on /api/*"]
    
    L1 --> L2["2. Vercel Security Middleware (Node.js)\n• Serverless Node.js Execution\n• @upstash/ratelimit Sliding Window\n• Static Asset Bypass (.js/.css/fonts)\n• Fail-Open Graceful Degradation"]
    
    L2 --> L3["3. FastAPI Application Layer\n• slowapi Granular Route Quotas\n• TenantAccessControlMiddleware (BOLA/IDOR)\n• ML Predict: 60/min | FL Sim: 10/min\n• DDoSProtectionMiddleware (100 req/10s)"]
```

### 10.1 Multi-Layer Perimeter & Gateway Architecture

| Layer | Component | Engine / Implementation | Enforced Protection & Limits | Response Code |
| :--- | :--- | :--- | :--- | :---: |
| **Layer 1** | **Cloudflare Perimeter** | Anycast WAF & L7 Rate Limiter (`scripts/setup_cloudflare_waf.py`) | L3/L4 DDoS absorption, TLS 1.3 Strict, custom firewall rules (sensitive path & null-byte blocking), 100 reqs / 10s challenge on mutating routes. | `403 Challenge` / `429` |
| **Layer 2** | **Vercel Security Middleware** | Node.js Runtime Middleware (`frontend/middleware.ts`) | `@upstash/ratelimit` global sliding window: 20 reqs/min for ML inference, 60 reqs/min for general API. | `429 Too Many Requests` |
| **Layer 3** | **Application WAF & Rate Limiting** | `PerimeterWAFGuard`, `slowapi` & `DDoSProtectionMiddleware` (`backend/app/main.py`) | OWASP Top 10 payload rejection (SQLi, XSS, null-bytes across URL, body, and HTTP headers), thread-safe brute-force lockout with bounded memory pruning (1,000 IPs), in-process route quotas (`/predict`: 60/min, `/simulations`: 10/min), and sliding-window burst protection (100 req/10s). | `400 Bad Request` / `403 Forbidden` / `429 Too Many Requests` |

### 10.2 Broken Object Level Authorization (BOLA/IDOR) & gRPC Cross-Tenant Isolation

To eliminate Broken Access Control (OWASP API1:2023), the platform implements cryptographic tenant verification across all alert, entity, and case presentation endpoints:
- **Tenant Identity Extraction:** Decodes OIDC JWT bearer tokens (`sub`, `bank_id`, `roles`), `X-Tenant-ID`, and `X-Bank-ID` headers with cross-bank investigator bypass (`super_admin`, `cross_bank_investigator`, `compliance_auditor`).
- **Middleware Interception:** `TenantAccessControlMiddleware` intercepts incoming requests, blocking cross-tenant URL parameter tampering (`?bank_id=other_bank`) with `HTTP 403 Forbidden` (`https://cfi-platform.org/errors/TenantAccessDenied`).
- **Endpoint-Level Isolation:** Routers invoke `enforce_tenant_isolation(caller_tenant, target_bank_id)` to ensure callers can never inspect foreign bank alert payloads or graph entities.
- **Dynamic ABAC Policy Engine (`abac_engine.py`):** Evaluates subject attributes (`bank_id`, `roles`, `clearance_level`, `shift_hours`, `approval_tier`), resource attributes (`bank_id`, `amount`, `classification_level`), and environment attributes (UTC clock, client IP) across 6 sequential policies (`RULE-SUPERADMIN-OVERRIDE`, `RULE-TENANT-ISOLATION`, `RULE-IP-RANGE-RESTRICTION`, `RULE-SHIFT-HOURS-RESTRICTION`, `RULE-APPROVAL-TIER-EXCEEDED`, `RULE-CLEARANCE-LEVEL-INSUFFICIENT`).
- **Fail-Closed Zero-Trust Enforcement:** In strict accordance with Zero-Trust principles, any malformed client IP string or unparseable shift hour range fails closed immediately (`allowed=False`) rather than falling open, preventing parser evasion exploits. The stateless engine achieves **>122,000 evaluations/s** with **7.8 µs** mean latency.
- **gRPC mTLS Cross-Tenant Registration:** In the gRPC transport servicer (`servicer.py`), certificate fingerprints are bound authoritatively at onboarding time (`issue_mtls_certificate`), not on first gRPC contact — this closes the trust-on-first-use race condition where an unregistered attacker could claim a bank identity before the legitimate bank onboards. Note: this still relies on a self-signed certificate model rather than a full CA chain of trust (Vault PKI root CA signature verification is not yet implemented); that remains a known scope limitation.

### 10.3 Enterprise Authentication, OIDC SSO & Brute-Force Lockout Defense (`auth_service.py`, `oidc_authenticator.py`, `password_hasher.py`)

- **Bcrypt Password Hashing:** Passwords are hashed using bcrypt with adaptive work factor (cost=12, 4096 iterations) and cryptographically secure per-password salt. Plaintext, MD5, and SHA-1 storage are strictly prohibited.
- **Short-Lived JWT Access Tokens:** Access tokens have an enforced 15-minute (900s) lifetime signed with 256-bit HMAC-SHA256 (RFC 7518 compliant).
- **Cryptographic OIDC Token Verification (`OIDCAuthenticator`):** Enterprise federated SSO bearer tokens are validated via PyJWT HMAC-SHA256 signature verification (`verify_signature: True`, `verify_exp: True`). Forged signatures and expired tokens are rejected with explicit error codes, extracting authoritative claims (`sub`, `bank_id`, `roles`, `clearance_level`, `shift_hours`, `approval_tier`, `allowed_ip_subnets`).
- **Refresh Token Rotation:** Refresh tokens (7-day validity) are single-use. Exchanging a refresh token via `POST /api/v1/auth/refresh` immediately revokes the previous token and issues a new access/refresh token pair, preventing replay of stolen credentials.
- **Brute-Force Account & IP Lockout:** Consecutive failed authentication attempts are tracked per user and client IP. After **5 failed attempts**, the account and IP are temporarily locked out for **15 minutes (900 seconds)**, returning HTTP `429 Too Many Requests` with `Retry-After: 900`. Lockouts can be reset via `reset_client_lockout` upon valid authentication.

### 10.4 Production Error Sanitization & PII Leakage Protection (`error_handler.py`, `data_validator.py`, `piiSanitizer.ts`)

- **Zero Information Leakage:** In production environments (`app_env="production"` or `app_debug=False`), all unhandled 500 exceptions are stripped of stack traces, internal filesystem paths (`C:\...`, `/var/...`), database table names, and SQL statements.
- **RFC 7807 Problem Details:** Clients receive a clean, uniform generic message (`"Something went wrong. An unexpected internal error occurred."`) alongside a unique incident tracking reference (`incident_id = "inc_..."` and `X-Incident-ID` response header).
- **Type-Salted HMAC-SHA256 PII Masking in Logs & Sentry:** Both error messages and Python tracebacks pass through real-time PII regex scrubbers before being written to disk or dispatched to Sentry. Personal and financial identifiers (IBAN, SSN/TCKN, Credit Card PAN, Email, and Phone numbers) are automatically substituted with type-salted HMAC-SHA256 tokens (`[MASKED_PII:<TYPE>:<DIGEST[:12]>]`), ensuring zero raw PII retention in application log sinks.
- **Streaming Zero-Raw-PII Ingestion Gate (`data_validator.py`):** Incoming streaming batches are scanned for cleartext PII columns (`iban`, `ssn`, `tckn`, `pan`, `card_number`, `national_id`). Corrupted or non-compliant batches are rejected with `DataContractValidationError`, quarantined with bounded FIFO memory limits (`MAX_QUARANTINE_PER_BANK = 100`), and redacted in heap memory to prevent memory leaks and cleartext PII heap retention.
- **Client-Side Edge Cryptographic Pseudonymization (`piiSanitizer.ts`):** Edge pre-flight dropzones compute standard pure TypeScript HMAC-SHA256 digests (RFC 2104 / FIPS 180-4) with Luhn validation, generating cryptographic audit receipts for Zero-Raw-PII compliance prior to network transfer.

### 10.5 Strict CORS Whitelist, Perimeter WAF & HTTP Security Headers (`security_headers.py`, `perimeter_waf.py`)

- **Wildcard Prohibition:** Wildcard CORS (`allow_origins=["*"]`) is strictly banned. CORS is constrained to explicit platform domains (`https://cf-intelligence.vercel.app`, `https://cfi-platform.vercel.app`), local development ports, and authenticated Vercel preview regexes (`^https:\/\/(cf-intelligence|cf-intelligence-git-[a-z0-9-]+-yusufcalisirs-projects)\.vercel\.app$`).
- **Perimeter WAF Guard (`PerimeterWAFGuard`):** Inspects incoming HTTP requests for OWASP Top 10 injection patterns:
  - **SQL Injection (SQLi):** Scans URL path, JSON body, and all HTTP request headers (e.g. `X-Filter`, `User-Agent`) for SQLi patterns (`UNION SELECT`, `DROP TABLE`, `OR 1=1`, comment queries).
  - **Cross-Site Scripting (XSS):** Blocks `<script>` tags, `javascript:` pseudo-protocols, and DOM event handlers (`onload=`) in bodies and headers.
  - **Null-Byte Injection:** Blocks `\x00` null bytes across paths, bodies, and header keys/values.
  - **Sensitive Path Scanning:** Rejects access to `/.env`, `/.git`, `/admin`, `/actuator`, `/wp-admin`, and `/config.json`.
  - **Thread Safety & Memory Pruning:** All lockout tracking dictionary mutations are synchronized via `threading.Lock` and bounded by automatic LRU pruning (`_max_tracked_ips = 1000`) to eliminate unbounded memory growth.
- **Defensive Security Headers Injection:** Outbound responses automatically wrap the following defensive headers:
  - `Content-Security-Policy`: API-strict directive (`default-src 'none'; script-src 'none'; connect-src 'self'`), with tailored CDN allowances on `/docs` and `/scalar`.
  - `Strict-Transport-Security`: Enforces HTTPS (`max-age=31536000; includeSubDomains; preload`).
  - `X-Content-Type-Options: nosniff`: Prevents MIME-sniffing attacks.
  - `X-Frame-Options: DENY`: Blocks iframe clickjacking.
  - `Referrer-Policy: no-referrer`: Eliminates cross-origin URL leakage.
  - `Permissions-Policy: geolocation=(), microphone=(), camera=(), payment=()`: Disables unused browser hardware interfaces.
  - `X-XSS-Protection: 1; mode=block`: Legacy browser filter hardening.

### 10.6 Real-Time Scoring Gateway, Tenant Quotas & SLA Monitoring

- **REST Inference Endpoints (`POST /api/v1/predict`, `POST /api/v1/predict/fast` & `POST /api/v2/transactions/evaluate`):** Screen normalized transactions and return actionable decisions (`ALLOW` <300, `REVIEW` 300-699, `BLOCK` $\ge$700). High-throughput payment rails (SEPA Instant, TARGET Instant Payment Settlement - TIPS) utilize the fast-path endpoint with sub-5ms raw neural latency (<14.2ms worst-case), while the enterprise OpenAPI v2 adapter (`/api/v2/transactions/evaluate`) enables drop-in integration with legacy AML pipelines without payload transformation. Full concurrent 9-signal feature store enrichment achieves ~258.9ms p50 / ~308.2ms p99 latency (well within the 350ms multi-model SLA budget).
- **Tenant Quota Enforcement (`TenantMeteringService` & `dependencies.py`):** The `enforce_tenant_quota` dependency actively intercepts requests to `/api/v1/predict` and `/api/v1/score-transaction`, tracking monthly quota consumption per tenant tier (e.g., 100,000 monthly calls) via atomic `acquire_quota()` and recording usage metrics (`record_inference`). When an institution breaches its quota ceiling, the gateway rejects the request with `HTTP 429 Too Many Requests` (`detail: "Tenant API quota limit exceeded"`). *Scope Note:* Metering is actively wired and enforced at the high-throughput inference gateway endpoints, rather than universally wrapping every internal admin or diagnostic probe.
- **Latency & SLA Monitor (`sla_monitor.py`):** Continuously tracks p50, p95, and p99 inference latencies with Prometheus telemetry exports.

### 10.7 STRIDE Threat Model & Attack Surface Summary

The platform enforces concrete, test-verified defenses across all 6 STRIDE attack categories (detailed in [docs/threat_model.md](docs/threat_model.md)):

| STRIDE Pillar | Threat Persona | Target Asset | Attack Technique | Technical Defense | Test Evidence |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Spoofing** | Compromised Node / Attacker | Node Identity & API | Forged cert / tenant header | Vault PKI mTLS (`mtls_manager.py`) + Bcrypt cost=12 (`password_hasher.py`) + 15m JWT & 5-fail lockout | `test_auth_security.py` |
| **Tampering** | Byzantine Bank / Attacker | Model Weights & DB | Sign-flip / backdoor / SQLi | Bulyan/Krum aggregation (`fl_engine.py`) + Spectral SVD (`spectral_defense.py`) + ORM DDL quoting | `test_byzantine_defense_validation.py` |
| **Repudiation** | Rogue Analyst / Bank | Case Workflow | Denying case closure/signature | Four-Eyes dual supervisor signatures (`case_workbench.py`) + Tamper-evident SHA-256 audit ledger | `test_case_management_workbench.py` |
| **Info Disclosure** | Honest-but-Curious Server | Raw PII & Gradients | Gradient Inversion (DLG) / MIA | Opacus DP ($\epsilon=1.0, \delta=10^{-5}$) + Curve25519 SecAgg + BOLA 403 + Production error sanitization | `test_error_sanitization.py` |
| **Denial of Service** | Botnet / Malicious Node | Scoring Availability | Volumetric `/predict` flood / NaN | 3-Tier Rate Limiting (Cloudflare WAF + Vercel Middleware + `slowapi`) + Finite tensor validation | `test_ddos_middleware.py` |
| **Privilege Escalation** | Rogue Internal User | Model Promotion / SAR | Unauthorized model promotion | ABAC policy engine (`abac_engine.py`) + SR 11-7 holdout PR-AUC $\ge$ champion gate | `test_enterprise_security_suite.py` |

### 10.8 Automated Security Floor Hardening & Zero-Vulnerability Dependency Perimeter

To meet stringent Tier-1 bank cybersecurity and vendor procurement standards, the repository enforces strict security floors across both Python and Node ecosystems:

- **0 Dependabot Security Alerts:** Upgraded and pinned all indirect transitive dependencies, eliminating 20 historical CVE advisories (5 high, 10 moderate, 5 low).
- **Enforced Security Floor Constraints:**
  - `urllib3 >= 2.6.3`: Neutralizes proxy credential leakage (CVE-2023-45803, CVE-2024-37891).
  - `jinja2 >= 3.1.6`: Prevents server-side template injection and XSS sandbox escapes (CVE-2024-22195, CVE-2024-34064).
  - `aiohttp >= 3.13.3`: Eliminates HTTP request smuggling and CRLF header injection.
  - `cryptography >= 46.0.5`: Patches memory safety vulnerabilities in underlying OpenSSL bindings.
  - `opacus >= 1.5.4`: Resolves PyTorch 2.4 gradient tensor compatibility and guarantees mathematical DP noise precision.
- **Enterprise npm Hygiene:** Frontend dependencies audit returns `0 vulnerabilities` across 38 direct and indirect packages.

### 10.9 Developer Webhook Perimeter & Multi-Layer SSRF Defense (`webhook_service.py` & `webhook_gateway.py`)

The platform dispatches real-time event notifications (`ALERT_CREATED`, `MODEL_PROMOTED`, `CASE_RESOLVED`) to external bank subscriber endpoints while maintaining strict SSRF perimeter isolation:
- **Multi-Stage SSRF Validation (`validate_target_url`):** Enforced at both webhook registration (`POST /v1/webhooks/subscriptions`) and pre-dispatch delivery (`deliver_payload_async`):
  - *Scheme Validation:* Restricts allowed protocols strictly to `http` and `https` (rejects `file://`, `ftp://`, `gopher://`).
  - *IP-Range Blocking:* Blocks loopback (`127.0.0.0/8`, `::1`), link-local (`169.254.0.0/16`, `fe80::/10`), private RFC 1918 subnets (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), multicast, and reserved IP literals.
  - *DNS Resolution Inspection:* Resolves candidate hostnames via `socket.getaddrinfo` and inspects all resolved IP addresses against private/loopback/cloud metadata boundaries to prevent DNS rebinding.
  - *Fail-Closed Security Policy:* If DNS resolution fails (`socket.gaierror`, `socket.herror`, `OSError`), the URL is strictly REJECTED (returns `False`). The gateway fails closed rather than allowing unverified domains through.
- **Receiver-Side HMAC Signature Verification:** Payloads are signed with HMAC-SHA256 (`X-CFI-Signature-256`). Subscribers verify incoming webhooks using constant-time comparison (`WebhookService.verify_signature` with `hmac.compare_digest`) or via the `POST /v1/webhooks/verify` endpoint.
- **Non-Blocking Delivery with Bounded Timeouts:** Deliveries run asynchronously with a strict 3.0s timeout and exponential backoff retry.

---

## 11. Human-in-the-Loop Workbench, European FININT & Regulatory RegTech

### 11.1 Case Management & 6-Stage Human-in-the-Loop Workbench (`case_workbench.py`)
- **6-Stage Case Workbench:** Manages alert review lifecycles (`NEW` $\to$ `ASSIGNED` $\to$ `UNDER_INVESTIGATION` $\to$ `PENDING_SECOND_SIGNATURE` $\to$ `RESOLVED_CONFIRMED_FRAUD` / `RESOLVED_FALSE_POSITIVE`, with optional `ESCALATED`) enforcing Four-Eyes dual supervisor signature authorization. Resolving a case strictly requires two distinct supervisor identities (`SIG_SUPERVISOR_<ID1>`, `SIG_SUPERVISOR_<ID2>`); single signatures and duplicate signers are cryptographically rejected.
- **FinCEN BSA SAR XML Compiler (`regulatory_reporter.py`):** Generates compliant Suspicious Activity Report (SAR) XML documents validated against the official FinCEN BSA XML Schema 2.0 (`backend/schemas/FinCEN_SAR_2.0.xsd`) using `lxml.etree.XMLSchema`.

### 11.2 Inter-Bank Encrypted FININT Messaging Protocol (`bridge_case_service.py` & `bridge_messaging.py`)
- **End-to-End Encrypted Case Exchange:** Enables compliance officers across consortium institutions to exchange sensitive evidentiary case payloads protected by Curve25519 Elliptic Curve Diffie-Hellman (X25519 ECDH), HKDF-SHA256 key derivation, and authenticated AES-256-GCM envelope encryption.
- **Cryptographic Evidence Commitment:** Case payloads include a verifiable SHA-256 hexadecimal hash commitment of the unencrypted evidence, automatically verified by the recipient node upon decryption to detect tampering in transit.
- **Tamper-Evident SHA-256 Hash-Chained Audit Trail:** Every status transition (`SUBMITTED`, `ACKNOWLEDGED`, `IN_REVIEW`, `RESPONDED`, `CLOSED`) is permanently recorded in a SHA-256 append-only hash chain linking the prior block digest, ticket ID, status, and timestamp.
- **SLA Countdown Timers & Dual Control:** Inter-bank tickets enforce urgency SLAs (`URGENT` < 4h, `STANDARD` 24h, `EXTENDED` 72h) and Four-Eyes supervisor authorization before dispatching cross-border regulatory intelligence.

### 11.3 Real-Time SEPA Instant Payment Recall Automation (`payment_recall_service.py` & `payment_recall.py`)
- **ISO 20022 `camt.056` & `camt.029` Schemes:** Full automation of European Payments Council (EPC) SEPA Instant Credit Transfer Recall rulebooks, processing payment cancellation requests (`camt.056`) citing standardized reason codes (`FRAD` fraud, `TECH` technical error, `DUPL` duplicate, `CUST` customer request).
- **Automated Account Freeze:** When a recall is registered with reason `FRAD`, the engine automatically verifies destination account status and triggers an immediate account freeze hold on the recipient bank node to prevent mule cash-outs.
- **10-Day Regulatory SLA Boundary:** Enforces the strict EPC 10-calendar-day window; recall attempts submitted after 10 days are deterministically rejected with `SLA_BREACH_RECALL_EXPIRED`.
- **Four-Eyes Resolution & Fund Recovery Ledger:** Resolutions (`camt.029`: `ACCEPTED` / `REJECTED` / `PENDING`) require supervisor verification and update a dedicated immutable recovery ledger tracking credited/debited balances.

### 11.4 Real-Time Multi-List Sanctions & PEP Screening Engine (`screening_service.py` & `screening.py`)
- **Multi-List Global Registry:** Real-time screening against UN Consolidated List, EU Common Foreign and Security Policy (CFSP) List, US OFAC Specially Designated Nationals (SDN) List, and Politically Exposed Persons (PEP) registries.
- **Dual-Algorithm Fuzzy String Matching:** Combines the Jaro-Winkler prefix-weighted metric ($p=0.10$) and normalized Levenshtein edit distance:
  $$S_{\mathrm{composite}} = 0.60 \cdot S_{\mathrm{jw}} + 0.40 \cdot S_{\mathrm{lev}}$$
- **Secondary Demographic Disambiguation:** Candidates with $S_{\mathrm{composite}} \ge 0.70$ undergo secondary validation against Date of Birth (DOB) and ISO 3166-1 alpha-2 Nationality, boosting confidence by $+0.15$ for matched attributes.
- **Audited Whitelist Bypass:** Compliance officers can register verified false positives in an institution-isolated whitelist with audit notes and dual sign-off, bypassing repetitive operational halts.

### 11.5 European FIU & UNODC goAML 4.0 / EU AMLA Regulatory Exporter (`fiu_regulatory_service.py` & `regulatory.py`)
- **UNODC goAML 4.0 XML Standardization:** Generates compliant electronic Suspicious Transaction Reports (STR) and Suspicious Activity Reports (SAR) formatted to the UNODC goAML XML 4.0 schema for national Financial Intelligence Units (FIUs).
- **EU AMLA Standardized JSON Format:** Compiles structured incident files adhering to the emerging EU Anti-Money Laundering Authority (AMLA) Single Rulebook format, detailing consortium velocity, mule accounts, and typologies.
- **Encrypted Transmission Envelope:** Wraps exported reports in an encrypted HMAC-SHA256 signed envelope (`CFI_REGULATORY_ENVELOPE_SECRET`) with submission tracking and mandatory Four-Eyes supervisor sign-off before transmission.

### 11.6 European AML Monitoring Scenario Library & Hybrid Rule Engine (`european_scenario_library.py` & `european_scenarios.py`)
- **16 Pre-Configured European Banking AML Typologies:** Codifies 16 production scenarios aligned with European Banking Authority (EBA) mandates and FATF typologies, including Sub-€10,000 Structuring/Smurfing (`SCN_EUR_STRUCTURING_SUB_10K`), Rapid Pass-Through Mule Accounts (`SCN_EUR_PASS_THROUGH_MULE`), High-Risk Non-Cooperative Jurisdiction Flows (`SCN_EUR_HIGH_RISK_JURISDICTION`), Round Amount Velocity Layering (`SCN_EUR_ROUND_AMOUNT_LAYERING`), Dormant Account Sudden Awakening (`SCN_EUR_DORMANT_SUDDEN_ACTIVITY`), Fan-Out Disbursement (`SCN_EUR_FAN_OUT_DISBURSEMENT`), and Terrorist Financing Indicators.
- **Hybrid Scoring Synthesis Engine:** Synthesizes deterministic regulatory rule breaches with federated machine learning anomaly scores to produce an authoritative composite risk score:
  $$S_{\mathrm{hybrid}} = \alpha \cdot S_{\mathrm{rules}} + (1 - \alpha) \cdot S_{\mathrm{ml}} \quad (\text{default } \alpha = 0.50)$$
- **Explainability Narrative Compiler:** Automatically compiles human-readable investigative narratives summarizing exact rule trigger conditions, threshold deviations, and recommended statutory actions (`ALLOW`, `MANUAL_REVIEW`, `SAR_ESCALATION`, `IMMEDIATE_BLOCK`) to accelerate compliance officer triage.

### 11.7 Asset Recovery & Collaborative FININT Operational Hub (`asset_recovery_service.py` & `asset_recovery.py`)
- **Aggregated EUR Recovery & Containment Telemetry:** Tracks consortium-wide financial impact metrics across SEPA Instant Payment Recall (`camt.056`) events and inter-bank FININT account holds with high-precision `Decimal` EUR accounting.
- **MTTR Alert-to-Freeze Latency Reduction:** Computes empirical Mean Time to Response (MTTR $P_{50}$, $P_{90}$, $P_{99}$) in minutes, evaluating operational performance against the legacy bilateral 48-hour (2,880-minute) inter-bank baseline, demonstrating a >98% latency reduction in cross-bank mule chain freezes.
- **Tamper-Evident SHA-256 Audit Hash Chain:** Anchors every recall execution, provisional hold, and recovery event into an append-only cryptographic hash chain ($H_t = \mathrm{SHA256}(H_{t-1} \,\|\, \mathrm{payload})$), ensuring evidentiary admissibility for EU judicial proceedings and AMLA compliance audits.

### 11.8 Data Retention, Erasure & PII Redaction
- **Data Retention & Erasure Engine (`retention_engine.py`):** Enforces configurable TTL retention rules and cryptographically zeroizes expired records. Database purging (`purge_expired_records`) executes real SQL `DELETE` operations against physical database tables for alerts (`AlertModel` under `TRANSACTION_LOGS` and `INFERENCE_AUDITS`), graph relationships (`RelationshipModel` under `GRAPH_EDGES`), and shared intelligence reports (`SharedIntelligenceModel` under `EXPLAINABILITY_REPORTS`). GDPR Article 17 erasure (`execute_gdpr_right_to_be_forgotten`) executes real SQL deletions across `EntityModel`, `RelationshipModel`, and `AlertModel`. *Scope Limitation:* Other data categories (raw transaction batches, cases in `CaseModel`, SAR draft XML files, and federated model gradient checkpoints) are not yet wired to automated database purge tasks and remain managed by external storage/retention policies.
- **Support Diagnostics & PII Redaction (`support_diagnostics.py`):** Generates sanitized diagnostics bundles from multi-line log sources (strings, files, lists) prior to support export. Redacts international IBANs (generic format matching 2-letter country code + 2 check digits + alphanumeric account string), payment card numbers (validated via ISO/IEC 7812 Luhn MOD-10 checksum algorithm), Turkish/Generic National IDs (11-digit algorithmic validation), raw account numbers, phone numbers (international and domestic formats), and contextual customer names (`Customer Name: [REDACTED]`). *Scope Note:* Name redaction uses deterministic keyword-prefixed heuristic regex patterns (e.g. `Customer Name:`, `Account Holder:`, `Client Name:`), not true Named Entity Recognition (NER) ML models.

---

## 12. Database Architecture, HA & Disaster Recovery Operations

### 12.1 Multi-Tenant Relational Persistence & Alembic Migration Engine
The persistence tier utilizes SQLAlchemy 2.0 Async ORM backed by a linear, dual-revision Alembic migration lifecycle (`001_production_domain_tables` $\to$ `002_core_and_aml_tables`) across 17 domain, AML, and evidence models:
- **Dual-Engine Multi-Tenancy:**
  - *PostgreSQL / CockroachDB:* Dedicated tenant schema spaces (`tenant_{bank_id}`) isolated with `CREATE SCHEMA IF NOT EXISTS` and dynamic `SET search_path TO tenant_{bank_id}, public` scoping, guarded by double-quoted SQL injection sanitization (`_pg_quote_identifier`).
  - *SQLite:* Dynamic isolated database files (`cfi_{bank_id}.db`) stored in guaranteed writable runtime directories (`_STORAGE_ROOT`) with full batch migration support (`render_as_batch=True`).
- **Dynamic Active Tenant Discovery:** The migration environment (`migrations/env.py`) dynamically queries registered institutions from the `tenant_configs` table (`_get_active_tenants()`), seamlessly migrating newly onboarded banks with resilient fallback to configured `VALID_TENANTS`.
- **Zero-Drift Parity & Reversibility:** Validated via automated `compare_metadata()` tests guaranteeing 100% schema parity with zero drift operations, complete linear revision resolution, and reversible rollback (`downgrade base`).
- **Automatic Schema Adoption & Stamping:** Programmatic startup migration manager (`migration_manager.py`) inspects target databases; if domain tables exist without version records (e.g. from developer ORM bootstrapping), it automatically stamps `head` via `_ensure_migrated_or_stamped()`, preventing collision crashes during container boot.
- **Offline Migration Mode:** Generates standalone SQL DDL statements (`--sql`) for strict air-gapped change-control environments.

### 12.2 Concurrency Safety & Race Condition Defenses
- **Thread-Safe Champion Promotion:** `ModelRegistry` and `ModelRegistryVault` enforce reentrant mutual exclusion locks (`threading.RLock()`) and atomic disk file swaps (`tempfile` + `os.replace`) to guarantee zero dual-champion states under concurrent load.
- **Atomic Tenant Metering:** `TenantMeteringService` utilizes an atomic `acquire_quota(tenant_id, tier, units)` routine to eliminate check-then-act race conditions during high-volume inference bursts.
- **Three-State Idempotency Key Pipeline:** `IdempotencyService` manages request states (`"ACQUIRED"`, `"IN_PROGRESS"`, `"HIT"`) preventing duplicate concurrent execution of financial workflows and replay attacks.
- **Tamper-Evident Audit Chain:** `ImmutableAuditChain` guarantees thread-safe, append-only block addition with HMAC-SHA256 integrity verification.

### 12.3 Cryptographic Key Lifecycle & Multi-Tenant KMS
- **Multi-Tenant Key Isolation:** Per-tenant envelope encryption using AES-256-GCM via `TenantKMSService` with automated key rotation and derivation from master Vault secrets.
- **Multi-Version Keyring & Envelope Headers:** Supports versioned keyrings (`v1`, `v2`, ...) with explicit envelope format `v{version}:{iv_b64}:{tag_b64}:{ciphertext_b64}`.
- **Data Re-Encryption & Revocation:** `re_encrypt_tenant_data()` migrates historical records from retired versions to active keys, while `invalidate_retired_keys()` guarantees un-migrated ciphertexts under revoked keys fail closed with `DecryptionError`.
- **Scheduled Maintenance Cron Endpoint:** `POST /v1/cron/rotate-keys` provides secure token-authorized trigger (`CRON_SECRET_KEY`) for automated key rotation schedulers (Kubernetes CronJobs / CloudWatch Events).
- **Honest Telemetry Reporting:** Explicit reporting of Vault PKI engine availability (Live HashiCorp Vault vs transparent in-memory cryptographic simulation).

### 12.4 Disaster Recovery & SRE Operations
- **Active-Passive Multi-Region Failover (`region_failover.py`):** Automates standby region promotion upon primary heartbeat timeout (>15s), with target $\text{RTO} < 30\text{s}$.
- **Operator CLI (`cfi_cli.py`):** Command-line tool for monitoring platform health, inspecting cluster status, and triggering administrative tasks.
- **Production-Hardened Systemic Resilience:**
  - *DDoS Memory Pruning:* Automatic IP timestamp sliding window cleanup preventing dictionary memory exhaustion (`_MAX_TRACKED_IPS = 1000`), backed by LRU hard ceiling eviction bound at `_HARD_CEILING_TRACKED_IPS = 5000` concurrent active IPs.
  - *Byzantine Non-Finite Isolation:* Immediate client quarantining on `NaN`/`Inf` model updates and champion fallback in `fl_engine.py` and `spectral_defense.py`.
  - *Universal Safe Evaluation:* Centralized `safe_roc_auc_score` and `safe_pr_auc_score` preventing single-class / empty-array evaluation crashes.
  - *Multi-Tier Redis Caching:* In-memory LRU fast paths ($\sim 0.001\text{ms}$) with 0.1s socket connect timeouts and graceful degradation to local DB/PyTorch.
  - *Developer Webhook Perimeter:* Multi-layer SSRF protection (scheme validation + IP-range blocking for loopback/link-local/RFC 1918/multicast + fail-closed DNS resolution validation at registration and pre-dispatch), constant-time HMAC-SHA256 signature verification (`verify_signature`), and non-blocking bounded 3.0s delivery timeouts (`deliver_payload_async`).
  - *Graph Ego-Network Budget:* BFS traversal capped at 100 nodes max to prevent browser DOM and React Flow canvas thread freezing.
  - *Frontend Error Boundaries:* Complete UI tree isolation with dark-themed recovery cards eliminating White Screen of Death (WSOD) risks.

---

## 13. Design Decisions & Trade-Offs

### 13.1 FedProx & SCAFFOLD vs. Naive FedAvg for Non-IID Banking Partitions
In realistic cross-bank consortia, member institutions exhibit severe statistical heterogeneity (Dirichlet skew $\alpha \le 0.50$): a retail-focused bank primarily processes domestic point-of-sale transactions, whereas a commercial bank processes large cross-border corporate wires. In naive `FedAvg`, this Non-IID distribution causes severe *client drift*, where local SGD trajectories pull client weights toward disparate local minima, destabilizing global model convergence. `FedProx` counters this by introducing a proximal regularization penalty $\frac{\mu}{2} \|\mathbf{w} - \mathbf{w}^t\|^2$ that dynamically penalizes local weights that stray too far from the global consensus. Specifically, this proximal penalty is implemented client-side in `model_service.py`'s `train_local` method as a PyTorch loss regularization term added to local cross-entropy loss; the server-side aggregation for FedProx in `fl_engine.py` is standard sample-weighted parameter averaging, identical to FedAvg.

For scenarios with higher variance, `SCAFFOLD` maintains client and server control variates ($c_i, c$) that estimate gradient drift directions and apply trajectory corrections ($g_i \leftarrow g_i - c_i + c$) directly during local backpropagation in `model_service.py`. *Implementation Note:* The server-side aggregation step for SCAFFOLD in `fl_engine.py` currently performs a weighted average identical to FedAvg; the drift-correction mechanism operates during client-side local training via gradient correction ($c_i, c$), while server-side variate tracking ($\bar{c}$) is stored in memory but not yet fed back into the cross-round connector layer end-to-end.

### 13.2 Byzantine-Robust Aggregators: Krum vs. Trimmed Mean vs. Bulyan
Standard coordinate averaging has a breakdown point of $0\%$: a single compromised client sending adversarially scaled or sign-flipped gradients ($-\gamma \nabla \mathcal{L}$) can degrade or hijack the global model. To defend against adversarial bank updates, the coordinator implements three distinct Byzantine-robust aggregation strategies, each offering a specific trade-off between robustness, assumption requirements, and computational cost. `Krum` (`AggregationMethod.KRUM` in `fl_engine.py`) operates on Euclidean distances across full parameter vectors, selecting the single representative client update that minimizes the sum of squared distances to its $n - f - 2$ closest neighbors; it provably tolerates up to $f < n/2$ attackers with $O(n^2 \cdot d)$ complexity but can struggle with benign Non-IID variance. Coordinate-wise `Trimmed Mean` trims the top and bottom $\beta$ fraction per coordinate, offering fast $O(n \log n \cdot d)$ computation and robustness against individual parameter extremes, but requires coordinate independence. `Bulyan` (`AggregationMethod.BULYAN`) combines both by using Krum-style scoring to select a trusted candidate subset of $n - 2f$ clients and then computing coordinate-wise trimmed mean on that selected subset, achieving the strongest known adversarial resilience at the cost of requiring $n \ge 4f + 3$ participants.

### 13.3 Differential Privacy Budget Calibration ($\epsilon = 1.0, \delta = 10^{-5}$)
The differential privacy budget is calibrated to balance concrete empirical protection against Membership Inference Attacks (MIA) with actionable fraud detection utility. In production-like fraud scenarios characterized by extreme class imbalance ($0.01\% - 0.1\%$ fraud prevalence), setting $\epsilon < 0.1$ injects excessive Gaussian noise into gradient updates, causing fraud recall to collapse below $30\%$. Conversely, setting $\epsilon > 10.0$ offers negligible mathematical defense against gradient reconstruction attacks. We select $\epsilon = 1.0$ and $\delta = 10^{-5}$ (strictly smaller than $1/N$) as our baseline operating point, where empirical MIA success remains bounded below $52.4\%$ (approaching random guessing) while preserving $\ge 62.4\%$ Recall at $0.1\%$ False Positive Rate. Privacy loss across multiple training rounds is tracked using Rényi Differential Privacy (RDP) moments accounting, achieving tight sub-linear $O(\sqrt{T})$ composition rather than pessimistic linear summation ($\sum \epsilon_t$).

### 13.4 Curve25519 Pairwise Masking SecAgg vs. Homomorphic Encryption
For protecting parameter updates in transit between banks and the aggregation coordinator, Curve25519 ECDH pairwise masking (SecAgg) was chosen as the default mechanism over Fully Homomorphic Encryption (CKKS FHE). SecAgg relies on zero-sum vector perturbations: pairs of clients establish shared symmetric secrets via Diffie-Hellman and add mutually cancelling pseudorandom masks to their parameter vectors before transmission. The coordinator sums the masked updates, causing masks to algebraically sum to zero ($\sum y_u = \sum w_u$) without exposing individual bank contributions. This software protocol achieves high throughput (>5.6M parameters/sec in NumPy vectorized simulation, ~513k parameters/sec in pure Python Curve25519 P2P modular arithmetic driver) with zero ciphertext expansion (preserving standard 32-bit floating-point payload sizes). In contrast, while CKKS FHE allows homomorphic arithmetic on encrypted ciphertexts without requiring client-to-client pairing, it introduces significant polynomial ring ciphertext bloat ($10\times - 50\times$ payload size) and substantial CPU overhead during encryption and evaluation. SecAgg was therefore selected as the primary path for interactive rounds, keeping FHE as an exploratory option.

---

## 14. Limitations & What This Is Not

> **Scope & Limitations Notice:**  
> - **Synthetic & Public Benchmark Basis:** This platform has been developed and evaluated using synthetic multi-bank data generators and canonical public research datasets (Elliptic, PaySim, IEEE-CIS). It has **not been deployed in live banking production**.
> - **Exploratory Concepts, Not Certified Compliance:** Discussions of regulatory frameworks (e.g., GDPR, EU AI Act, Bank Secrecy Act) reflect architectural design inspirations and conceptual models. The platform is **not independently certified** by any compliance or auditing body.
> - **Single-Maintainer Project:** This repository is an independent technical portfolio and research codebase conceived and maintained by a single engineer (**Yusuf Çalışır**), demonstrating end-to-end distributed system design, privacy-enhancing technologies, and anti-fraud architectures.
> - **Explainability & Counterfactual Scope:** SHAP explanations are computed via a real `shap.KernelExplainer` against the serving neural network model with unit-tested additivity verification ($|\sum \phi_i + \text{base value} - f(\mathbf{x})| < 10^{-8}$), and counterfactual remediation paths are searched via iterative greedy perturbation re-scored directly by `RiskScoringEngine.score_transaction()`. Practical engineering trade-offs apply: (1) KernelExplainer uses a bounded sample budget ($N=100$ permutations over $N=30$ reference baselines) to satisfy sub-100ms serving constraints rather than exhaustive shapley sampling; (2) Counterfactual search explores a discrete candidate space over domain-mutable features rather than continuous gradient-based manifold optimization (e.g., DiCE).
> - **Algorithmic Verification Scope & Precision Gaps:** While core cryptographic invariants (e.g., SecAgg zero-sum cancellation, differential privacy Gaussian noise bounds, and Krum neighbor distance scoring) are verified with exact numeric unit tests, certain algorithmic modules are validated via integration-level behavioral tests rather than closed-form numerical assertions. Specifically: (1) `FedProx` tests verify that local training completes over 2 epochs and returns a valid model under $\mu = 10.0$, but do not assert an exact numerical proximal-term loss value; (2) `RiskScoringEngine` tests verify that all 9 signals are produced, weights sum to 1.0, and risk thresholds behave ordinally (e.g., score $> 800$ for high-risk inputs), but do not assert exact weighted-sum arithmetic for a deterministic input vector.
> - **Pluggable Dual-Mode Hardware & Emulation Architecture:** Core security and infrastructure drivers (`hsm_signer.py`, `tee_driver.py`, `vault_hsm_pki_binder.py`, `smart_contract_driver.py`, `event_bus.py`) implement a dual-mode pattern: production setups interface with physical PKCS#11 HSM tokens, bare-metal Intel SGX / AWS Nitro Enclaves, distributed Apache Kafka clusters, and live EVM networks. For local development, CI/CD pipelines, and zero-hardware cloud instances, high-fidelity software emulators (`SoftwareHSMSignerEngine`, `SoftwareEmulatedTEEDriver`, `ConsortiumSettlementLedgerSimulator`) provide authentic cryptographic behavior without requiring specialized silicon.
> - **Enterprise Zero-Mock Connector Policy:** All bank connectors default to standard REST/Batch/ISO 20022 schemas rather than mock implementations, maintaining strict schema conformity across client nodes.
> - **Open Banking PSD2 XS2A Sandbox Scope:** The presentation router (`/api/v1/psd2/*`) operates as an Open Banking XS2A sandbox interface that deterministically synthesizes consented account balances and transaction histories via SHA-256 derivation per `account_id` (with `acc_1` serving canonical integration test fixtures). Institutional production data ingestion operates through the connector layer (`OpenBankingConnector`, `ISO20022MessagingConnector`, mTLS REST) rather than synthetic presentation endpoints.
> - **Evaluator Utilities & Empirical Attack Simulation:** The `security_evaluator.py` Byzantine resilience evaluator executes real empirical parameter vector aggregation simulations across clean consensus baselines and poisoned vectors for FedAvg, FedProx, Coordinate Median, Trimmed Mean, Krum, and Bulyan. Other evaluators (MIA, DLG) use simplified heuristic proxies rather than exhaustive shadow-model training.
> - **Binary Model Serialization Protocol:** The gRPC model distribution transport packages global models using standardized `b"CFI1"` framed headers with UTF-8 metadata and raw 32-bit floating-point weight buffers.

---

## 15. Empirical Performance & Benchmark Suite

All benchmark measurements are derived from the integrated test suite executed across synthetic multi-bank partitions and canonical open-source financial datasets.

### 15.1 Core Platform Engineering Metrics

| Benchmark Dimension | Measured Value | Design Target | Verification Reference | Verification Status |
| :--- | :---: | :---: | :--- | :---: |
| **Inference Latency (Fast-Path Raw)** | < 14.2 ms | < 50 ms | `realtime_inference.py` | `Self-Verified (Internal Test Suite)` |
| **Concurrent Ensemble Latency (p50 / p99)** | **258.9 ms (p50) / 308.2 ms (p99)** | < 350 ms (Ensemble SLA) | `test_load_concurrency_verification.py` | `Empirical Load Benchmark (15 workers, 9-signal feature store)` |
| **HTTP Endpoint Latency under Load (p50 / p99)** | **166 ms (p50) / 395 ms (p99) @ 97.6 req/s** | < 100 ms (p99 SLA) | [`scripts/realtime_benchmark.py`](scripts/realtime_benchmark.py) | `Empirical ASGI Load Test (1,500 real requests, 20-concurrency, 3 endpoints; GIL-bound single-process)` |
| **Event-Driven Stream SLA (p50 / p99)** | **26.1 ms (p50) / 62.75 ms (p99) @ 62.4 tx/s** | < 100 ms (Stream SLA) | [`scripts/transaction_stream.py`](scripts/transaction_stream.py) | `Empirical ASGI Stream Pipeline (1,883 real transactions, 30s, asyncio.Queue producer→consumer, 0 errors, 100% utilization)` |
| **FATF High-Risk Country Block Rate** | **4.83% BLOCK / 59.4% REVIEW / 35.7% ALLOW** | FATF NCCT → mandatory BLOCK | [`scripts/transaction_stream.py`](scripts/transaction_stream.py) | `Empirical (5% high-risk injection: KP, IR, SY; verified against ground truth)` |
| **ABAC Authorization Throughput** | **132,942 req/s (mean)** | > 5,000 req/s | [`scripts/run_abac_benchmark.py`](scripts/run_abac_benchmark.py) | `Empirical In-Memory Benchmark (50k evals x 3 rounds)` |
| **ABAC Decision Latency** | < 0.015 ms (p99) | < 1 ms | [`scripts/run_abac_benchmark.py`](scripts/run_abac_benchmark.py) | `Empirical In-Memory Benchmark` |
| **SecAgg Throughput (Curve25519 P2P Driver)** | **~513,000 param/s** | > 250k param/s | `p2p_secagg_driver.py` | `Empirical Single-Thread Modular Masking Benchmark` |
| **SecAgg Throughput (NumPy Vectorized Masking)** | **~5,630,000 param/s** | > 1M param/s | `fl_engine.py` | `Empirical NumPy Array Vectorization Benchmark` |
| **SecAgg Latency Scaling** | **O(n x d), R^2 = 0.9703** | Linear O(n x d) | `fl_engine.py` (NumPy masking path via `secagg_benchmark_scalability.py`) | `Empirical Vectorization Benchmark (see secagg_scalability_benchmark_report.md; variance range: 0.91–0.99)` |
| **FL Synthetic ROC-AUC (FedAvg)** | **0.835 mean (range 0.563–0.952)** | > 0.80 measured / 0.950 lab design goal | `simulation_service.py` (5-seed empirical benchmark, 3-bank consortium, 5 rounds) | `Empirical Simulation Benchmark (5 seeds: [42, 123, 456, 789, 2026])` |
| **Differential Privacy Budget** | $\epsilon = 1.0, \delta = 10^{-5}$ | $\epsilon \le 2.0$ | `privacy_audit_service.py` | `Self-Verified (Internal Test Suite)` |
| **Disaster Recovery Failover (RTO)** | **15.01 s (RPO = 0 records)** | < 30 s | `chaos_dr_drill.py` | `Logical Drill (in-memory state model: 15.0s baseline timeout + ~10-20ms promotion; not multi-region cloud infra failover)` |
| **Multi-Tenant Isolation & Security** | **21/21 SaaS Multi-Tenant Tests Passing** | Strict Isolation (403 BOLA rejection, Linear Alembic, Vault KMS) | [`docs/saas_multitenancy.md`](docs/saas_multitenancy.md) | `Self-Verified (4/4 BOLA Security, 3/3 Lifecycle, 4/4 Alembic, 5/5 KMS, 5/5 Concurrency)` |
| **Full Test Suite Pass Rate** | **2,864 / 2,864 passing (3,171 total incl. verification)** | 100% | 2,518 Backend Pytest + 315 Frontend Vitest + 31 Smart Contracts (+ 307 Scientific Verification Tests) | `Self-Verified (Internal Test Suite)` |

---

### 15.2 Real-World Open Benchmark Datasets

Under Non-IID Dirichlet distribution ($\alpha = 0.50$), the platform evaluates against canonical open benchmark datasets using precision-recall metrics suited for severe class imbalance:

| Benchmark Dataset | Domain & Scale | Federated PR-AUC | Single-Bank PR-AUC | Recall @ 0.1% FPR | False Alarm Reduction |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **[PaySim](https://www.kaggle.com/datasets/ealaxi/paysim1)** | Mobile Money (6.36M txns) | **0.8420** | 0.6940 (`+0.1480`) | **62.4%** (`+19.2%`) | **-64.7% False Alarms** |
| **[IEEE-CIS](https://www.kaggle.com/competitions/ieee-fraud-detection)** | E-Commerce / Cards (590k txns) | **0.8120** | 0.6510 (`+0.1610`) | **58.9%** (`+21.4%`) | **-58.3% False Alarms** |
| **[Elliptic AML Graph](https://www.kaggle.com/datasets/ellipticco/elliptic-data-set)** | Bitcoin Graph (203k total / 46.5k labeled nodes, 234k edges) | **0.8746** | 0.2543 (`+0.6203`) | **80.6%** (`+28.2%`) | **-61.2% False Alarms** |
| **[Credit Card Fraud](https://www.kaggle.com/datasets/mlg-ulb/creditcardfraud)** | European Cards PCA (284k txns, LEAF $\alpha=0.50$) | **0.8250** | 0.6430 (`+0.1820`) | **59.8%** (`+20.1%`) | **-65.0% False Alarms** |

---

### 15.3 Executable Benchmark & Verification CLI Tooling

All benchmark measurements and verification suites can be directly reproduced via standalone CLI scripts:

| Benchmark / Evaluation Target | CLI Command | Evaluated Capabilities & Output |
| :--- | :--- | :--- |
| **Offline Dataset ETL & Ingestion** | `python scripts/etl_dataset_pipeline.py --dataset paysim --banks 3 --alpha 0.5` | Kaggle-free offline dataset ingestion pipeline supporting PaySim, AMLSim, Elliptic, IEEE-CIS, and Credit Card. Performs zero-raw-PII HMAC-SHA256 sanitization, Dirichlet non-IID partitioning, Parquet export, and cryptographic `dataset_manifest.json` generation. |
| **Real-Time HTTP Endpoint Benchmark** | `python scripts/realtime_benchmark.py --requests 500 --concurrency 20 --sla 100.0` | Empirical ASGI in-process load test (httpx.ASGITransport) measuring p50/p95/p99 latency distributions across `/api/v1/transactions/score`, `/api/v1/predict`, and `/v1/inference/score` (JIT TorchScript). No network stack overhead. Generates `reports/realtime_benchmark.json`. |
| **Event-Driven Transaction Stream Pipeline** | `python scripts/transaction_stream.py --tps 50 --workers 10 --duration 30` | Full producer→asyncio.Queue→consumer pipeline simulating continuous real-time transaction ingestion at configurable TPS. Measures end-to-end scoring latency, queue wait distribution, actual throughput, and fraud detection outcomes (ALLOW/REVIEW/BLOCK) with ~5% injected FATF high-risk transactions. Generates `reports/stream_pipeline_results.json`. |
| **Playwright Real-Browser E2E** | `npm --prefix frontend run test:e2e:workflows` | 10 headless browser workflows across Chromium and Firefox verifying authentication lifecycles, live FL training round telemetry, Four-Eyes SAR signing, Byzantine chaos attack injection, and custom dataset ingestion. |
| **Enterprise Docker Deployment** | `python scripts/verify_docker_deployment.py` | Automated pre-flight and runtime smoke test verifying zero Compose syntax drift, PostgreSQL 16 cold-start schema, Redis 7.2 ping, Nginx security headers, and WebSocket keepalive routing. |
| **Real Elliptic AML Graph** | `python scripts/run_elliptic_benchmark.py` | Benchmarks real Bitcoin transaction graph (46.5k nodes, 234k edges) through GraphSAGE vs. isolated baseline. Generates [`verification/real_data_benchmark/`](verification/real_data_benchmark/). |
| **Full Multi-Dataset Suite** | `python benchmark.py` | Evaluates 6-model matrix (Local, Pooled, FedAvg, FedProx, FedGNN, DP) + PaySim (6.36M), IEEE-CIS (20k), Elliptic with distribution fidelity audit. |
| **9-Configuration Matrix (C1–C9)** | `python scripts/run_benchmark.py --samples 1000 --rounds 5` | Compares PR-AUC, ROC-AUC, F1, Recall@1% FPR, transmitted payload (MB), and DP epsilon consumption across 9 predefined architectural variants. |
| **Enterprise ISO 20022 Stress Test** | `python scripts/run_enterprise_stress_test.py --banks 5 --target-tps 10000 --duration 10` | High-throughput concurrent stream simulation of `pacs.008` messages measuring peak TPS, p50/p99 latency, and error rates. Generates `reports/`. |
| **End-to-End API Contract Audit** | `python scripts/audit_api_contracts.py` | Audits 100% of REST endpoints, Pydantic schemas, WebSocket streams, and status codes with zero orphaned routes. |
| **EU AI Act & Governance Export** | `python scripts/export_compliance_report.py` | Generates standardized multi-page markdown compliance audit reports covering bias, explainability, and model governance. |
| **Mutation Testing & Fault Injection** | `python scripts/run_mutation_tests.py` | Injects 29 dynamic Python AST mutants & 12 TypeScript invariants across frontend & backend with 86.2% backend AST kill rate (90.2% composite score). |
| **Branch Coverage Audit** | `python scripts/run_coverage_audit.py --backend` | Computes 4-tier coverage metrics (Statements, Decision Branches, Functions, Lines) via `pytest-cov --cov-branch` with strict 75% regression gate (`--cov-fail-under=75`). |
| **Kubernetes Manifest Dry-Run Audit** | `python scripts/validate_k8s_manifests.py --all` | Renders Helm charts and executes authentic `kubectl apply --dry-run=client` against all 39 production resources with zero template errors. |
| **Bank Integration Sandbox** | `python scripts/cfi_cli.py sandbox run --transactions 1000` | Self-service integration sandbox simulating 1,000 transactions through local inference pipeline with hardware acceleration detection. |
| **Dynamic CycloneDX 1.5 SBOM** | `python scripts/generate_sbom.py --format cyclonedx` | Generates automated, dependency-verified CycloneDX 1.5 JSON SBOM capturing 330 components across Python and npm runtimes with license and hash tracking. Outputs to `storage/sbom_cyclonedx.json`. |

---

## 16. Regulatory Concepts Explored

The technical architecture of CF-Intelligence explores how system design patterns can be structured around real-world regulatory and compliance principles across European and international jurisdictions (detailed in [`docs/european_finint_and_regtech_spec.md`](docs/european_finint_and_regtech_spec.md)):

1. **Data Minimization & Sovereign Privacy (GDPR Art. 6 & 17, CCPA):**  
   Cross-border banking secrecy and data protection statutes prohibit pooling raw customer records across institutions. The platform addresses this through federated learning: raw transactions remain within the local banking node, and only differentially private gradients ($\epsilon = 1.0, \delta = 10^{-5}$) and zero-sum masked vectors are transmitted.
2. **Cross-Bank FININT Case Messaging (EU AMLA Single Rulebook & AMLD6):**  
   Under the EU Anti-Money Laundering Authority (AMLA) Single Rulebook and AMLD6, obliged credit institutions are mandated to exchange operational fraud intelligence and mule indicators without violating GDPR data sovereignty. CF-Intelligence implements this via Curve25519 Elliptic Curve Diffie-Hellman (X25519 ECDH), HKDF-SHA256 key derivation, AES-256-GCM authenticated encryption, SHA-256 evidence integrity hashing, SLA timers, and immutable SHA-256 hash-chained audit trails.
3. **SEPA Instant Payment Recall Automation (EPC SCT Inst Rulebook):**  
   Governs the automated processing of inter-bank payment recall requests (`camt.056`) citing standardized EPC reason codes (`FRAD`, `TECH`, `DUPL`, `CUST`) and investigation resolutions (`camt.029`). The engine enforces the strict EPC 10-calendar-day regulatory window, triggers automated account freeze holds on destination bank nodes upon fraudulent payment detection, and maintains an immutable fund recovery ledger.
4. **Real-Time Multi-List Sanctions & PEP Compliance (UN, EU CFSP, OFAC SDN):**  
   Continuous pre-clearing and batch transaction screening against consolidated global sanction registries and Politically Exposed Persons (PEP) lists. The engine executes dual-algorithm fuzzy matching (Jaro-Winkler prefix-weighted metric combined with normalized Levenshtein distance), secondary demographic disambiguation (DOB, ISO nationality), and an audited false-positive whitelist bypass.
5. **Electronic Suspicious Activity Reporting (FinCEN BSA & UNODC goAML 4.0 / EU AMLA):**  
   Statutory anti-money laundering frameworks mandate standardized electronic filings for suspicious transactions. The platform provides automated compilation of confirmed investigation cases into US FinCEN BSA XML 2.0 schemas, UNODC goAML 4.0 XML reports for national Financial Intelligence Units (FIUs), and standardized EU AMLA JSON dossiers wrapped in HMAC-SHA256 encrypted envelopes with mandatory Four-Eyes supervisor sign-off.
6. **Model Transparency & Meaningful Human Oversight (EU AI Act & SR 11-7):**  
   High-risk financial AI governance mandates require explainability and human supervisory control. The architecture integrates real-time KernelExplainer SHAP feature attributions into scoring responses and implements a "Four-Eyes Principle" workflow requiring dual supervisor authorization before closing investigation cases or submitting regulatory filings.
7. **Zero-Trust Access Control & Cryptographic Audit Trails:**  
   The architecture models Attribute-Based Access Control (ABAC) and append-only cryptographic audit chains (`block_hash` linking) to explore security controls for managing multi-institution consortium lifecycles and model promotion gates.
8. **Beneficial Ownership Transparency & Circular Layering (EU AMLD6 & 4AMLD 25% Threshold):**  
   Statutory corporate transparency mandates (EU 4th/5th/6th AML Directives) require financial institutions to identify Ultimate Beneficial Owners (UBOs) holding $\ge 25.0\%$ cumulative ownership or voting rights. CF-Intelligence implements multi-tier directed graph traversal, automated compounding of indirect shareholdings, Tarjan-based circular ownership loop detection, and high-risk jurisdiction shell company clustering.
9. **Standardized Typology Detection & Hybrid Rule Blending (FATF Recommendations & EBA Guidelines):**  
   European Banking Authority (EBA) and FATF standards mandate continuous automated monitoring across established typologies. The platform deploys 16 pre-configured statutory scenarios (sub-€10k structuring/smurfing, rapid pass-through mule movement, round amount layering, fan-out disbursement, dormant account awakening) synthesized dynamically with federated ML anomaly scores ($S_{\mathrm{hybrid}} = \alpha S_{\mathrm{rules}} + (1 - \alpha) S_{\mathrm{ml}}$).


---

## 17. Subsystem Self-Verification Reports (`verification/`)

The reports below document the internal scientific verification suites validating mathematical invariants, differential privacy bounds, cryptographic drivers, and algorithmic implementations:

| Subsystem Module | Target Component Scope | Self-Verification Report | Verification Status |
| :--- | :--- | :--- | :---: |
| **Federated Learning Engine** | `fl_engine.py`, `flower_engine.py` | [Verification Report ↗](verification/federated_learning/scientific_audit_report.md) | `Self-Verified (Internal Test Suite)` |
| **Differential Privacy** | `privacy_service.py`, `psi_service.py` | [Verification Report ↗](verification/differential_privacy/scientific_audit_report.md) | `Self-Verified (Internal Test Suite)` |
| **Secure Aggregation** | `p2p_secagg_driver.py`, `shamir_engine.py` | [Verification Report ↗](verification/secure_aggregation/scientific_audit_report.md) | `Self-Verified (Internal Test Suite)` |
| **Zero-Trust PKI & ABAC** | `vault_client.py`, `abac_engine.py` | [Verification Report ↗](verification/zero_trust_pki/scientific_audit_report.md) | `Self-Verified (Internal Test Suite)` |
| **Federation Coordinator** | `coordinator_service.py` | [Verification Report ↗](verification/federation_coordinator/scientific_audit_report.md) | `Self-Verified (Internal Test Suite)` |
| **AML Risk Scoring Engine** | `risk_engine.py`, `alert_service.py` | [Verification Report ↗](verification/risk_scoring/scientific_audit_report.md) | `Self-Verified (Internal Test Suite)` |
| **Graph Intelligence (FedGNN)** | `graph_embedding_service.py`, `graph_embedding_model.py` | [Verification Report ↗](verification/graph_intelligence/scientific_audit_report.md) | `Self-Verified (Internal Test Suite)` |
| **Model Drift Detection** | `drift_service.py`, `auto_rollback.py` | [Verification Report ↗](verification/drift_detection/scientific_audit_report.md) | `Self-Verified (Internal Test Suite)` |
| **Explainability (XAI)** | `explainability_service.py`, `realtime_explainer.py` | [Verification Report ↗](verification/explainability/scientific_audit_report.md) | `Self-Verified (Internal Test Suite)` |
| **Financial Connectors** | `financial_message_parser.py` | [Verification Report ↗](verification/connectors/scientific_audit_report.md) | `Self-Verified (Internal Test Suite)` |
| **ETL & Data Pipeline** | `data_generator.py`, `data_validator.py` | [Verification Report ↗](verification/etl_pipeline/scientific_audit_report.md) | `Self-Verified (Internal Test Suite)` |
| **Smart Contracts Suite** | `ConsortiumIncentiveSettlement.sol` | [Verification Report ↗](verification/smart_contracts/scientific_audit_report.md) | `Self-Verified (Internal Test Suite)` |
| **Audit Logging & Compliance** | `privacy_audit_service.py` | [Verification Report ↗](verification/audit_logging/scientific_audit_report.md) | `Self-Verified (Internal Test Suite)` |
| **API Gateway & Middleware** | `main.py`, `routers/` | [Verification Report ↗](verification/api/scientific_audit_report.md) | `Self-Verified (Internal Test Suite)` |
| **Telemetry & Observability** | `metrics_service.py`, `sla_monitor.py` | [Verification Report ↗](verification/telemetry/scientific_audit_report.md) | `Self-Verified (Internal Test Suite)` |
| **Terraform IaC & Cloud** | `deployments/` | [Verification Report ↗](verification/terraform_iac/scientific_audit_report.md) | `Self-Verified (Internal Test Suite)` |
| **Master Mathematical Protocol**| 35 Formal Mathematical Invariants | [Verification Report ↗](verification/mathematical/scientific_audit_report.md) | `Self-Verified (Internal Test Suite)` |
| **Real-World Graph Benchmark** | `Elliptic AML Bitcoin Graph Dataset` | [Verification Report ↗](verification/real_data_benchmark/README.md) | `Self-Verified (Internal Test Suite)` |

---

## 18. API Endpoint Blueprints & JSON Schemas

### 18.1 Real-Time Transaction Risk Scoring

**Normalized Transaction Scoring Request (`POST /api/v1/score-transaction`):**
```json
{
  "transaction_id": "txn_88492049281",
  "account_id": "DE89370400440532013000",
  "amount": 250000.0,
  "currency": "EUR",
  "merchant_id": "crypto_exchange_01",
  "country": "US",
  "device_id": "dev_fp_993810a"
}
```

**Normalized Transaction Scoring Response (HTTP 200 OK):**
```json
{
  "risk_score": 895,
  "risk_level": "HIGH",
  "decision": "BLOCK",
  "model_version": "v2.4.1",
  "explanations": [
    {"feature": "velocity", "contribution": 0.38},
    {"feature": "transaction_amount", "contribution": 0.29},
    {"feature": "merchant_risk_score", "contribution": 0.18}
  ],
  "related_entities": [
    {"entity_type": "merchant", "risk": "HIGH"}
  ],
  "latency_ms": 14.2
}
```

> [!NOTE]
> **Real SHAP Attribution Computation:** The feature contribution values in the example above illustrate the response schema contract. At serving time, explanations are computed dynamically by `ExplainabilityService.compute_shap_values()` ([Section 9.2](#92-model-explainability--counterfactual-search-explainability_servicepy--risk_enginepy)) using real `shap.KernelExplainer` against the PyTorch serving neural network (`FraudDetectionModel`), guaranteeing the mathematical Shapley local accuracy property ($\sum \phi_i + \text{base value} = f(\mathbf{x})$) within floating point tolerance.

**Full-Feature Inference Request (`POST /api/v1/predict`):**
```json
{
  "transaction_amount": 250000.0,
  "merchant_category": "crypto",
  "country_code": "US",
  "device_type": "web_browser",
  "velocity": 12.5,
  "hour_of_day": 3,
  "merchant_risk_score": 0.85,
  "customer_history_score": 0.12,
  "chargeback_count": 4,
  "account_age_days": 14,
  "bank_id": "bank_alpha"
}
```

**Full-Feature Inference Response (HTTP 200 OK):**
```json
{
  "fraud_probability": 0.942,
  "risk_score": 895.4,
  "is_fraud_suspected": true,
  "risk_level": "CRITICAL",
  "policy_action": "BLOCK",
  "triggered_rules": [
    "HIGH_VELOCITY_SUSPICIOUS_MERCHANT",
    "NEW_ACCOUNT_HIGH_VALUE_CRYPTO"
  ],
  "breakdown": [
    {
      "signal_name": "S_velocity",
      "weight": 0.20,
      "raw_value": 12.5,
      "normalized_score": 980.0,
      "explanation": "High velocity transfer burst within 1 hour"
    },
    {
      "signal_name": "S_graph",
      "weight": 0.15,
      "raw_value": 0.88,
      "normalized_score": 920.0,
      "explanation": "GraphSAGE embedding anomaly detected across entity cluster"
    }
  ]
}
```

### 18.2 Enterprise Authentication & Session Management

**Login Request (`POST /api/v1/auth/login`):**
```json
{
  "username": "investigator_alpha",
  "password": "CorrectHorseBatteryStaple123!"
}
```

**Login Response (HTTP 200 OK):**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 900,
  "user": {
    "username": "investigator_alpha",
    "bank_id": "bank_alpha",
    "roles": ["investigator", "analyst"]
  }
}
```

**Token Refresh Request (`POST /api/v1/auth/refresh`):**
```json
{
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

**Lockout Status Check (`GET /api/v1/auth/lockout-status?username=investigator_alpha`):**
```json
{
  "username": "investigator_alpha",
  "client_ip": "198.51.100.42",
  "is_locked_out": false,
  "remaining_lockout_seconds": 0,
  "user_failure_count": 0,
  "ip_failure_count": 0
}
```

### 18.3 Enterprise Connector Diagnostics & Live Probes

**List Connector Health Status (`GET /api/v1/diagnostics/connectors`):**
```json
{
  "status": "healthy",
  "total_connectors": 7,
  "healthy_connectors": 7,
  "degraded_connectors": 0,
  "unhealthy_connectors": 0,
  "connectors": [
    {
      "id": "kafka_stream",
      "name": "Apache Kafka (Distributed Event Bus)",
      "category": "STREAMING",
      "status": "HEALTHY",
      "endpoint": "kafka.internal:9092",
      "latency_ms": 4.2,
      "last_checked": "2026-09-02T14:35:00Z"
    },
    {
      "id": "vault_pki",
      "name": "HashiCorp Vault (PKI & Secrets)",
      "category": "SECURITY",
      "status": "HEALTHY",
      "endpoint": "https://vault.internal:8200",
      "latency_ms": 6.8,
      "last_checked": "2026-09-02T14:35:00Z"
    }
  ]
}
```

**Execute On-Demand Connector Ping Probe (`POST /api/v1/diagnostics/test-connector`):**
```json
{
  "connector_id": "splunk_hec"
}
```

**Probe Response (HTTP 200 OK):**
```json
{
  "connector_id": "splunk_hec",
  "status": "HEALTHY",
  "latency_ms": 11.4,
  "handshake_trace": [
    "DNS resolution: splunk.internal -> 10.200.4.15",
    "TCP SYN/ACK established on port 8088",
    "TLS 1.3 handshake: ECDHE-RSA-AES256-GCM-SHA384",
    "HEC Token validation probe: HTTP 200 OK (Channel active)"
  ],
  "timestamp": "2026-09-02T14:35:10Z"
}
```

### 18.4 Real-Time WebSocket Telemetry Stream

**Connection Endpoint:** `ws://localhost:8000/ws/telemetry` (or `wss://...` in production)

**Inbound Client Subscription Message:**
```json
{
  "action": "subscribe",
  "channels": ["transactions", "alerts", "heartbeat"]
}
```

**Outbound Real-Time Fraud Alert Event Broadcast:**
```json
{
  "type": "FRAUD_ALERT",
  "transaction_id": "txn_live_994821",
  "bank_id": "bank_alpha",
  "amount": 250000.0,
  "currency": "EUR",
  "risk_score": 942,
  "decision": "BLOCK_AND_ESCALATE",
  "reason": "Velocity surge detected across 3 consortium nodes within 90 seconds",
  "timestamp": "2026-09-02T14:35:15Z"
}
```

### 18.5 Interactive Developer Portal & Scalar API Gateway

- **Dark-Themed Scalar Gateway:** `GET /scalar` (Renders modern `@scalar/api-reference` targeting `/openapi.json`).
- **Interactive Multi-Language SDK Portal:** Route `/developer` and `/api-docs` provides client generator for **cURL**, **Python (httpx)**, **Node.js (axios)**, **Java (OkHttp)**, and **Go (net/http)** with live in-browser execution runner.
- **OpenAPI 3.1 JSON Specification:** Available via `GET /openapi.json` or exported directly via the Developer Portal UI.

### 18.6 Interactive Chaos & Adversarial Attack Simulation (`POST /api/v1/scenarios/inject-attack`)

**Attack Injection Request:**
```json
{
  "attack_type": "byzantine_poisoning",
  "adversary_bank": "bank_gamma",
  "target_bank": "bank_alpha",
  "intensity_rate": 500,
  "defense_strategy": "krum"
}
```

**Attack Execution Response (HTTP 200 OK):**
```json
{
  "attack_id": "ATK-BYZ-9941",
  "attack_type": "byzantine_poisoning",
  "status": "quarantined",
  "defense_activated": "Krum Robust Byzantine Aggregation",
  "adversary_quarantined": "bank_gamma",
  "euclidean_distance": 48.24,
  "distance_threshold": 14.10,
  "packets_blocked": 500,
  "mitigation_latency_ms": 3.8,
  "auc_protected": 0.9412,
  "auc_compromised_baseline": 0.5218,
  "log_entry": "Byzantine poisoned gradient from bank_gamma rejected by Krum Robust Byzantine Aggregation (dist 48.2 > threshold 14.1). Model AUC preserved at 0.9412."
}
```
*Note: `auc_protected` and `auc_compromised_baseline` in this endpoint represent continuous simulated demo proxy metrics for live operator HUD feedback and are explicitly tagged as simulated in the schema and console UI.*

### 18.7 Real Dataset Ingestion & Great Expectations Contract Gating

**1. Validate Preview & Schema Auto-Detection (`POST /api/v1/datasets/validate-preview`):**
```json
{
  "file_name": "corporate_wires_q3.csv",
  "content": "timestamp,amount,src,dst,channel,is_fraud\n2026-09-01T08:00:00Z,12500.50,acc_101,acc_902,SWIFT,0\n...",
  "delimiter": ","
}
```

**Preview Response (HTTP 200 OK):**
```json
{
  "inferred_columns": [
    {"source_col": "timestamp", "target_signal": "timestamp", "confidence": 0.98, "inferred_type": "datetime"},
    {"source_col": "amount", "target_signal": "transaction_amount", "confidence": 0.99, "inferred_type": "float"},
    {"source_col": "src", "target_signal": "source_account_id", "confidence": 0.95, "inferred_type": "string"},
    {"source_col": "dst", "target_signal": "destination_account_id", "confidence": 0.95, "inferred_type": "string"},
    {"source_col": "channel", "target_signal": "channel_type", "confidence": 0.92, "inferred_type": "string"},
    {"source_col": "is_fraud", "target_signal": "is_fraud", "confidence": 1.0, "inferred_type": "integer"}
  ],
  "row_count": 5000,
  "column_count": 6,
  "sample_rows": [],
  "pii_detected": false
}
```

**2. Great Expectations Contract Audit (`POST /api/v1/datasets/contract-audit`):**
```json
{
  "file_name": "corporate_wires_q3.csv",
  "column_mappings": [
    {"source_col": "amount", "target_signal": "transaction_amount"},
    {"source_col": "src", "target_signal": "source_account_id"},
    {"source_col": "is_fraud", "target_signal": "is_fraud"}
  ],
  "rows": []
}
```

**Audit Scorecard Response (HTTP 200 OK):**
```json
{
  "passed": true,
  "total_checks": 12,
  "passed_checks": 12,
  "failed_checks": 0,
  "checks": [
    {"check_name": "expect_column_values_to_not_be_null: amount", "status": "passed"},
    {"check_name": "expect_column_values_to_be_between: amount [0.01, 10000000.0]", "status": "passed"},
    {"check_name": "expect_column_values_to_be_in_set: channel_type", "status": "passed"}
  ],
  "quarantined_rows_count": 0,
  "dirichlet_alpha_estimate": 0.524,
  "ks_drift_score": 0.024
}
```

**3. Consortium Enrollment (`POST /api/v1/datasets/consortium-enroll`):**
```json
{
  "dataset_name": "Bank_Alpha_Q3_Wires",
  "target_bank": "bank_alpha",
  "partition_strategy": "append_partition",
  "row_count": 5000,
  "dirichlet_alpha": 0.524
}
```

### 18.8 24-Hour Consortium Transaction Scoring Volume (`GET /api/v1/banks/scoring-volume`)

Aggregates empirical hourly transaction velocity and volume across all onboarded consortium institutions for operational throughput monitoring:

**Request (`GET /api/v1/banks/scoring-volume`):**
```http
GET /api/v1/banks/scoring-volume HTTP/1.1
Host: api.cfi-platform.org
Authorization: Bearer <jwt_token>
```

**Response (HTTP 200 OK):**
```json
[
  {"time": "00:00", "volume": 1420},
  {"time": "01:00", "volume": 890},
  {"time": "02:00", "volume": 612},
  {"time": "03:00", "volume": 480},
  {"time": "12:00", "volume": 8920},
  {"time": "14:00", "volume": 9410},
  {"time": "23:00", "volume": 2150}
]
```

### 18.9 Federated Training Convergence & Real-Time Event Streaming

**1. Query Training Round Convergence (`GET /api/v1/training/rounds/{simulation_id}`):**
```json
[
  {
    "round_number": 1,
    "total_rounds": 5,
    "global_loss": 0.5412,
    "auc": 0.8641,
    "per_bank_auc": {
      "bank_a": 0.8812,
      "bank_b": 0.8540,
      "bank_c": 0.8571
    },
    "per_bank_loss": {
      "bank_a": 0.5210,
      "bank_b": 0.5580,
      "bank_c": 0.5446
    },
    "participating_banks": ["bank_a", "bank_b", "bank_c"],
    "dropped_banks": [],
    "duration_ms": 1420
  }
]
```

**2. Real-Time Training Event WebSocket Stream (`WS /api/v1/training/ws/{simulation_id}`):**
Publishes real-time training iteration progress broadcast via internal Redis Pub/Sub (`training:{simulation_id}` and `training:live_prod_v2`):
```json
{
  "event_type": "round_complete",
  "data": {
    "round": 3,
    "total": 5,
    "loss": 0.2841,
    "auc": 0.9412,
    "per_bank_auc": {"bank_a": 0.951, "bank_b": 0.932, "bank_c": 0.940},
    "participants": ["bank_a", "bank_b", "bank_c"],
    "duration_ms": 1380
  }
}
```

### 18.10 Regulatory SAR Export & Key Rotation Cron Endpoints

**1. Case SAR FinCEN XML Export (`POST /api/v1/cases/export/fincen-xml`):**
Exports confirmed fraud cases directly into official FinCEN BSA XML Schema 2.0 electronic filings:

*Request (`POST /api/v1/cases/export/fincen-xml`):*
```json
{
  "case_id": "CASE-2026-9941"
}
```

*Response (HTTP 200 OK):*
```json
{
  "status": "FILED",
  "submission_id": "SAR-XML-2026-9941-A8F2",
  "case_id": "CASE-2026-9941",
  "xml": "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<EFilingSubmission ...>\n  <ReportingInstitution>Bank Alpha</ReportingInstitution>\n  <SuspiciousActivityInformation>Cross-Bank Velocity Surge</SuspiciousActivityInformation>\n</EFilingSubmission>",
  "timestamp": "2026-09-06T12:00:00Z"
}
```

**2. Scheduled Key Rotation Trigger (`POST /v1/cron/rotate-keys`):**
Triggered by Kubernetes CronJobs or cloud event schedulers to rotate per-tenant KMS envelope keys:

*Request (`POST /v1/cron/rotate-keys`):*
```http
POST /v1/cron/rotate-keys HTTP/1.1
Host: api.cfi-platform.org
Authorization: Bearer <CFI_CRON_SECRET>
Content-Type: application/json

{
  "tenant_id": "bank_a",
  "keep_last_n": 2
}
```

*Response (HTTP 200 OK):*
```json
{
  "status": "SUCCESS",
  "tenant_id": "bank_a",
  "active_version": 2,
  "retired_versions": [1],
  "reencrypted_records_count": 450,
  "timestamp_iso": "2026-09-06T00:00:00Z"
}
```

### 18.11 Continuous Human-in-the-Loop Feedback & Retraining Ground-Truth Store

Connects investigator case determinations directly back to tenant-isolated retraining buffers for continuous federated model fine-tuning:

**1. Ingest Analyst Ground-Truth Determination (`POST /api/v1/feedback/ingest`):**
```json
{
  "tenant_id": "bank_alpha",
  "alert_id": "alt_2001",
  "determination": "CONFIRMED_FRAUD",
  "priority": 3,
  "weight": 2.0,
  "notes": "Confirmed syndicate structuring across 3 mule accounts"
}
```

*Response (HTTP 201 Created):*
```json
{
  "status": "success",
  "item": {
    "transaction_id_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "label": "CONFIRMED_FRAUD",
    "weight": 2.0,
    "priority": 3,
    "consumed_for_retraining": false,
    "recorded_at": "2026-09-16T12:00:00Z"
  }
}
```

**2. Sample Prioritized, Stratified Retraining Batch (`POST /api/v1/feedback/retraining-batch`):**
```json
{
  "tenant_id": "bank_alpha",
  "batch_size": 32,
  "stratified": true,
  "mark_consumed": true
}
```

*Response (HTTP 200 OK):*
```json
{
  "tenant_id": "bank_alpha",
  "batch_size": 32,
  "items": [],
  "fraud_count": 16,
  "false_positive_count": 16,
  "mean_priority": 2.45
}
```

**3. Compute Differential-Privacy-Protected Gradient Update (`POST /api/v1/feedback/dp-gradient`):**
```json
{
  "tenant_id": "bank_alpha",
  "epsilon": 1.0,
  "delta": 1e-5,
  "clip_norm": 1.0
}
```

*Response (HTTP 200 OK):*
```json
{
  "tenant_id": "bank_alpha",
  "delta_weights": [0.03512, 0.07184, 0.10621, 0.14289],
  "sample_count": 32,
  "epsilon": 1.0,
  "delta": 1e-05,
  "sigma": 4.84379
}
```

### 18.12 Inter-Bank Encrypted FININT Messaging API (`/api/v1/bridge/*`)

Enables compliance officers to exchange end-to-end encrypted FININT case tickets and evidentiary payloads across consortium institutions:

**1. Create Encrypted Inter-Bank Ticket (`POST /api/v1/bridge/cases`):**
```json
{
  "originating_bank_id": "bank_alpha",
  "recipient_bank_id": "bank_beta",
  "case_id": "CASE-EU-2026-0841",
  "request_type": "MULE_ACCOUNT_INQUIRY",
  "urgency": "URGENT",
  "subject_identifier": "DE89370400440532013000",
  "evidence_payload": "Confirmed rapid layering across 4 intermediary accounts within 180 seconds. Total outbound: EUR 145,000.",
  "recipient_public_key_hex": "5a4b3c2d1e0f9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b"
}
```

*Response (HTTP 201 Created):*
```json
{
  "ticket_id": "FININT-2026-A1B2C3D4",
  "status": "SUBMITTED",
  "originating_bank_id": "bank_alpha",
  "recipient_bank_id": "bank_beta",
  "encrypted_payload_b64": "v1:G4k9...:AQID...:ZGF0YQ==",
  "evidence_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "sla_deadline_iso": "2026-09-22T20:00:00Z",
  "audit_chain_block_hash": "8f3b2a1c0d9e...f7a6b"
}
```

**2. Verify Immutable Audit Chain (`GET /api/v1/bridge/cases/{ticket_id}/audit-trail`):**
```json
{
  "ticket_id": "FININT-2026-A1B2C3D4",
  "chain_valid": true,
  "block_count": 3,
  "blocks": [
    {"index": 0, "event": "TICKET_CREATED", "status": "SUBMITTED", "block_hash": "8f3b2a..."},
    {"index": 1, "event": "STATUS_TRANSITION", "status": "IN_REVIEW", "block_hash": "c4d5e6..."},
    {"index": 2, "event": "RESPONSE_ATTACHED", "status": "RESPONDED", "block_hash": "1a2b3c..."}
  ]
}
```

### 18.13 Real-Time SEPA Instant Payment Recall API (`/api/v1/recalls/*`)

Automates European Payments Council (EPC) SEPA Instant Credit Transfer payment recall workflows (`camt.056` / `camt.029`):

**1. Initiate Fraud Recall (`POST /api/v1/recalls/initiate`):**
```json
{
  "original_transaction_id": "TX-SEPA-2026-8819",
  "original_end_to_end_id": "E2E-SEPA-2026-8819-A",
  "originating_bank_id": "bank_alpha",
  "destination_bank_id": "bank_beta",
  "debtor_iban": "DE89370400440532013000",
  "creditor_iban": "FR7630006000011234567890189",
  "amount": 49500.0,
  "currency": "EUR",
  "reason_code": "FRAD",
  "reason_narrative": "Authorized Push Payment fraud detected via impersonation syndicate."
}
```

*Response (HTTP 201 Created):*
```json
{
  "recall_id": "REC-2026-991204",
  "status": "INITIATED",
  "reason_code": "FRAD",
  "destination_account_frozen": true,
  "sla_deadline_iso": "2026-10-02T12:00:00Z",
  "days_remaining": 10,
  "recovery_transaction_id": "REC-HOLD-8819"
}
```

**2. Resolve Recall Investigation with Dual Control (`POST /api/v1/recalls/{recall_id}/resolve`):**
```json
{
  "resolution_code": "ACCEPTED",
  "supervisor_id": "SIG_SUPERVISOR_FINCRIME_44",
  "returned_amount": 49500.0,
  "resolution_notes": "Funds successfully quarantined on beneficiary mule account and queued for repatriation."
}
```

### 18.14 Real-Time Multi-List Sanctions & PEP Screening API (`/api/v1/screening/*`)

Executes sub-10ms fuzzy matching across UN, EU CFSP, OFAC SDN, and PEP registries:

**1. Screen Entity / Transaction Subject (`POST /api/v1/screening/screen`):**
```json
{
  "entity_name": "Vladimir Petrovich Ivanov",
  "date_of_birth": "1974-05-12",
  "nationality": "RU",
  "threshold": 0.80
}
```

*Response (HTTP 200 OK):*
```json
{
  "query_name": "Vladimir Petrovich Ivanov",
  "decision": "MATCH",
  "highest_score": 0.932,
  "matches": [
    {
      "list_source": "EU_CFSP",
      "target_name": "Vladimir Petrovitch Ivanov",
      "composite_score": 0.932,
      "jw_score": 0.941,
      "lev_score": 0.918,
      "dob_match": true,
      "nationality_match": true,
      "sanction_program": "EU_UKRAINE_RESTRICTIONS_2026"
    }
  ],
  "whitelist_bypassed": false,
  "latency_ms": 3.4
}
```

### 18.15 European FIU & UNODC goAML 4.0 / EU AMLA Regulatory Exporter API (`/api/v1/regulatory/*`)

Compiles confirmed AML cases into standardized electronic filing packages:

**1. Export UNODC goAML 4.0 XML (`POST /api/v1/regulatory/export/goaml-xml`):**
```json
{
  "case_id": "CASE-2026-9941",
  "report_code": "STR",
  "fiu_destination": "FIU_GERMANY_ZFIU",
  "supervisor_id": "SIG_SUPERVISOR_AML_01"
}
```

*Response (HTTP 200 OK):*
```json
{
  "status": "GENERATED",
  "submission_id": "GOAML-STR-2026-9941-F12A",
  "schema_version": "goAML 4.0 XML",
  "xml_payload": "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<report report_code=\"STR\">\n  <reporting_entity>BANK_ALPHA_DE</reporting_entity>\n  <reason>Cross-Bank Mule Structuring</reason>\n</report>",
  "envelope_digest": "4a7b9c...e2f1",
  "created_at": "2026-09-22T21:40:00Z"
}
```

### 18.16 Enterprise AML OpenAPI Drop-in Adapter & Webhook Gateway (`/api/v2/*`, `/api/v1/*`)

Provides backward-compatible drop-in endpoints matching industry-standard AML and transaction monitoring OpenAPI schemas, enabling member institutions to integrate existing core banking systems without bespoke integration middleware:

**1. Ingest Corporate Legal Entity with Ultimate Beneficial Owners (`POST /api/v2/persons`):**
```json
{
  "type": "LEGAL",
  "company_name": "Acrobat Capital Holdings B.V.",
  "registration_number": "NL-88392102",
  "country": "NL",
  "ubos": [
    {
      "name": "David Alexander Meyer",
      "ownership_percentage": 68.5,
      "is_pep": false
    }
  ]
}
```

*Response (HTTP 201 Created):*
```json
{
  "person_id": "PER-LEGAL-7A2B9C",
  "status": "ACTIVE",
  "risk_tier": "MEDIUM",
  "ubo_count": 1,
  "created_at": "2026-09-22T21:45:00Z"
}
```

**2. Execute Real-Time AML Monitoring Check (`POST /api/v1/transactions/{transaction_id}/monitoring-checks`):**
```json
{
  "mode": "ONLINE",
  "direction": "OUTBOUND"
}
```

*Response (HTTP 200 OK):*
```json
{
  "transaction_id": "TX-AML-90218",
  "action": "SUSPEND",
  "risk_score": 884.0,
  "alerts": [
    {
      "scenario_code": "SCN_EUR_STRUCTURING_SUB_10K",
      "severity": "CRITICAL",
      "description": "High-velocity structuring sequence below EUR 10,000 reporting threshold."
    }
  ],
  "latency_ms": 4.2
}
```

**3. Register HMAC-SHA256 Signed Webhook Ingestion Gateway (`POST /api/v1/webhook-subscriptions`):**
```json
{
  "callback_url": "https://bank-alpha.internal.net/aml/events",
  "event_types": ["ALERT_CREATED", "SCREENING_ALERT_CREATED"]
}
```

*Response (HTTP 201 Created):*
```json
{
  "subscription_id": "SUB-AML-449102",
  "target_url": "https://bank-alpha.internal.net/aml/events",
  "status": "ACTIVE",
  "signing_secret": "whsec_7f9a...3b2c",
  "subscribed_events": ["ALERT_CREATED", "SCREENING_ALERT_CREATED"]
}
```

### 18.17 Cross-Border Corporate UBO & Heterogeneous Graph Intelligence API (`/api/v1/ubo/*`)

Provides consortium-wide graph intelligence for multi-tier Ultimate Beneficial Owner (UBO) calculation, circular ownership loop identification, nominee director syndicate detection, and offshore shell company clustering:

**1. Calculate Multi-Tier Compounded Beneficial Ownership (`GET /api/v1/ubo/entities/{entity_id}/beneficial-owners?threshold=25.0&max_depth=8`):**
*Response (HTTP 200 OK):*
```json
{
  "entity_id": "ORG-LUX-HOLDING",
  "statutory_threshold": 25.0,
  "beneficial_owners": [
    {
      "node_id": "PER-UBO-ALICE",
      "name": "Alice Vance",
      "node_type": "NATURAL_PERSON",
      "jurisdiction": "DE",
      "direct_percentage": 15.0,
      "indirect_percentage": 12.5,
      "effective_percentage": 27.5,
      "reaches_statutory_threshold": true,
      "is_pep": false,
      "is_sanctioned": false,
      "shortest_hop_distance": 1,
      "control_paths": [
        ["PER-UBO-ALICE", "ORG-LUX-HOLDING"],
        ["PER-UBO-ALICE", "ORG-NL-BV", "ORG-LUX-HOLDING"]
      ]
    }
  ],
  "total_beneficial_owners_identified": 1,
  "depth_analyzed": 2,
  "calculated_at": "2026-09-22T21:50:00Z"
}
```

**2. Audit Entity for Structural Corporate Anomalies (`GET /api/v1/ubo/entities/{entity_id}/anomalies`):**
*Response (HTTP 200 OK):*
```json
{
  "target_entity_id": "ORG-SHELL-CYPRUS",
  "anomalies_detected": [
    {
      "anomaly_type": "CIRCULAR_OWNERSHIP",
      "severity": "CRITICAL",
      "description": "Directed circular ownership loop detected across 3 entities.",
      "involved_entities": ["ORG-SHELL-CYPRUS", "ORG-BVI-HOLDINGS", "ORG-MALTA-CORP"],
      "confidence_score": 1.0,
      "detected_at": "2026-09-22T21:50:05Z"
    }
  ],
  "has_circular_ownership": true,
  "has_nominee_directors": false,
  "has_high_risk_offshore": true,
  "has_pep_or_sanctions_exposure": false,
  "composite_structural_risk_score": 85.0
}
```

**3. Export Directed Ego-Subgraph for Interactive Visualizer (`GET /api/v1/ubo/entities/{entity_id}/subgraph?max_hops=3`):**
*Response (HTTP 200 OK):*
```json
{
  "root_id": "ORG-LUX-HOLDING",
  "nodes": [
    {"node_id": "ORG-LUX-HOLDING", "name": "Luxembourg Holdings S.A.", "node_type": "LEGAL_ENTITY", "jurisdiction": "LU"},
    {"node_id": "PER-UBO-ALICE", "name": "Alice Vance", "node_type": "NATURAL_PERSON", "jurisdiction": "DE"}
  ],
  "edges": [
    {"source_id": "PER-UBO-ALICE", "target_id": "ORG-LUX-HOLDING", "relation_type": "DIRECT_OWNERSHIP", "percentage": 15.0}
  ],
  "total_nodes": 2,
  "total_edges": 1
}
```

### 18.18 European AML Monitoring Scenario Library & Hybrid Deterministic Rule Engine API (`/api/v1/scenarios/european-aml/*`)

Provides 16 pre-configured statutory European AML monitoring rules and a hybrid scoring synthesizer that blends deterministic compliance rule penalties with Federated GNN risk embeddings into an explainable composite decision:

**1. Real-Time Hybrid AML Risk Evaluation (`POST /api/v1/scenarios/european-aml/evaluate`):**
```json
{
  "transaction": {
    "transaction_id": "TX-EUR-90218",
    "amount": 9500.0,
    "currency": "EUR",
    "originator_id": "CUST-ALICE-100",
    "beneficiary_id": "CUST-BOB-200",
    "origin_country": "DE",
    "destination_country": "FR",
    "payment_rail": "SEPA_INSTANT",
    "recent_distinct_counterparties_24h": 1,
    "funds_retention_ratio": 1.0
  },
  "ml_risk_score": 0.35,
  "strict_regulatory_override": true
}
```

*Response (HTTP 200 OK):*
```json
{
  "transaction_id": "TX-EUR-90218",
  "action": "MANUAL_REVIEW",
  "composite_risk_score": 533.5,
  "rule_penalty_score": 320.0,
  "ml_risk_score": 0.35,
  "ml_penalty_equivalent": 350.0,
  "regulatory_override_applied": false,
  "triggered_scenarios": [
    {
      "scenario_code": "SCN_EUR_STRUCTURING_SUB_10K",
      "scenario_name": "Sub-€10,000 Threshold Structuring (Smurfing)",
      "category": "STRUCTURING",
      "severity": "HIGH",
      "penalty_score": 320.0,
      "regulatory_citation": "EU AMLD6 Art. 33 & FATF Recommendation 10",
      "trigger_rationale": "Transfer of €9,500.00 positioned just below the €10,000 statutory reporting threshold."
    }
  ],
  "total_scenarios_evaluated": 16,
  "total_scenarios_triggered": 1,
  "explainability_narrative": "Action 'MANUAL_REVIEW' decided with composite risk 533.5/1000. Triggered 1 European AML scenario(s): [SCN_EUR_STRUCTURING_SUB_10K].",
  "evaluated_at": "2026-09-23T00:05:00Z"
}
```

**2. Query European AML Scenario Library Catalog (`GET /api/v1/scenarios/european-aml/library`):**
*Response (HTTP 200 OK):*
```json
{
  "total_scenarios": 16,
  "scenarios": [
    {
      "scenario_code": "SCN_EUR_STRUCTURING_SUB_10K",
      "name": "Sub-€10,000 Threshold Structuring (Smurfing)",
      "category": "STRUCTURING",
      "severity": "HIGH",
      "base_penalty": 320.0,
      "regulatory_basis": "EU AMLD6 Art. 33 & FATF Recommendation 10",
      "description": "Transaction structured immediately below the €10,000 European statutory reporting threshold."
    }
  ]
}
```

### 18.19 Asset Recovery & Collaborative FININT Operational Hub API (`/api/v1/operations/asset-recovery/*`)

Provides real-time aggregated financial containment and MTTR operational telemetry across all ISO 20022 `camt.056` payment recalls and inter-bank FININT provisional holds:

**1. Aggregated Operational Summary (`GET /api/v1/operations/asset-recovery/summary`):**
*Response (HTTP 200 OK):*
```json
{
  "total_recovered_eur": 2845000.0,
  "total_frozen_eur": 1920000.0,
  "total_events_count": 11,
  "successful_recalls_count": 5,
  "provisional_holds_count": 4,
  "partial_recoveries_count": 2,
  "mttr_minutes_p50": 18.5,
  "mttr_minutes_p90": 42.0,
  "mttr_minutes_p99": 75.0,
  "legacy_baseline_mttr_minutes": 2880.0,
  "mttr_reduction_percent": 99.36,
  "cross_bank_contagion_containment_rate": 90.91,
  "mule_chains_disrupted": 7,
  "last_audit_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "generated_at": "2026-09-23T12:00:00Z"
}
```

**2. Per-Typology Containment Breakdown (`GET /api/v1/operations/asset-recovery/breakdown-by-typology`):**
*Response (HTTP 200 OK):*
```json
[
  {
    "typology": "CRYPTO_CASHOUT",
    "total_eur": 1250000.0,
    "events_count": 3,
    "avg_mttr_minutes": 14.2,
    "containment_rate": 100.0,
    "risk_level": "CRITICAL"
  },
  {
    "typology": "APP_FRAUD_MULE_CHAIN",
    "total_eur": 850000.0,
    "events_count": 2,
    "avg_mttr_minutes": 22.5,
    "containment_rate": 100.0,
    "risk_level": "HIGH"
  }
]
```

---

## 19. Research & Exploratory Modules

> **Research & Exploration Note:**  
> The modules in this section were built to explore adjacent cryptographic, hardware-assisted, and decentralized coordination primitives. They are maintained as standalone prototypes and are **not required dependencies** of the core federated fraud detection pipeline.

```mermaid
flowchart TD
    subgraph ResearchCrypto["Exploratory Cryptographic Primitives"]
        zk["Groth16 zk-SNARK Weight Attestation<br/><code>zk_snark_verifier.py · Circom</code>"]
        PQC["Post-Quantum Hybrid SecAgg<br/><code>pqc_secagg_driver.py · Kyber-768</code>"]
        FHE["TenSEAL CKKS Homomorphic Encryption<br/><code>fhe_driver.py · Polynomial Rings</code>"]
        TEE["Hardware TEE SGX / Nitro Driver<br/><code>tee_driver.py · Remote Attestation</code>"]
    end

    subgraph ResearchSettlement["Exploratory Settlement & Governance"]
        EVM["EVM Shapley Incentive Settlement<br/><code>ConsortiumIncentiveSettlement.sol</code>"]
        MultiSig["Gnosis Safe 2-of-3 Governance<br/><code>GnosisSafeMultiSigCoordinator.sol</code>"]
        Bridge["Layer-2 Cross-Chain Settlement Bridge<br/><code>layer2_crosschain_bridge.py · Chainlink CCIP</code>"]
    end
```

### 19.1 Groth16 zk-SNARK Model Weight Attestation (`zk_snark_verifier.py` & `weight_attestation.circom`)
Prototyped zero-knowledge proof circuits using Circom and Groth16 over the BN254 curve to verify that local bank updates satisfy $L_2$ norm clip bounds and match Poseidon hash commitments without revealing raw weight vectors.

### 19.2 Post-Quantum Cryptography (PQC SecAgg) (`pqc_secagg_driver.py`)
Exploratory hybrid SecAgg driver integrating CRYSTALS-Kyber-768 (NIST FIPS 203) Key Encapsulation Mechanism and CRYSTALS-Dilithium-3 (NIST FIPS 204) signatures to evaluate quantum-resistant key exchange overhead in distributed federations.

### 19.3 TenSEAL CKKS Homomorphic Encryption (`fhe_driver.py`)
Prototyped Fully Homomorphic Encryption driver using Microsoft SEAL CKKS polynomial rings ($N=8192$) for evaluating encrypted vector additions on untrusted aggregation servers without pairwise client coordination.

### 19.4 Hardware Trusted Execution Environment (TEE) Driver (`tee_driver.py`)
Confidential Computing enclave driver modeling Intel SGX / AWS Nitro Enclave remote attestation structures (`MRENCLAVE` validation) and memory sealing patterns. For cloud environments and CI/CD pipelines without bare-metal enclave access, the `SoftwareEmulatedTEEDriver` executes full cryptographic attestation and data sealing in memory.

### 19.5 Consortium Smart Contracts & Gnosis Safe Multi-Sig (`contracts/`)
Solidity 0.8.20 smart contracts (`ConsortiumIncentiveSettlement.sol`, `GnosisSafeMultiSigCoordinator.sol`) exploring automated reward settlement and 2-of-3 multi-signature governance across consortium participants. Leave-One-Out Shapley marginal contributions are computed off-chain in Python (`smart_contract_driver.py` via `ConsortiumSettlementLedgerSimulator`); the smart contract itself is an escrow/settlement ledger that verifies pool balance conservation, prevents double-claiming, and enforces quarantine zero-payout rules over the pre-computed allocations.

### 19.6 Cross-Chain Settlement Bridge (`layer2_crosschain_bridge.py`)
Exploratory token routing connector modeling Chainlink CCIP `EVM2AnyMessage` payloads across Ethereum Layer-2 rollups (Arbitrum, Optimism) and enterprise ledgers (Hyperledger Fabric, Canton).

---

## 20. Prerequisites and System Requirements

| Dependency | Minimum Version | Purpose |
| :--- | :---: | :--- |
| **Docker** | 24.0+ | Container runtime for API, Redis, Postgres, Kafka |
| **Docker Compose** | 2.20+ | Multi-container orchestration |
| **Python** | 3.12+ | Backend runtime and test suite |
| **Node.js** | 20 LTS+ | Frontend console and Hardhat EVM |
| **npm** | 9.0+ | Frontend and contract toolchain |
| **Git** | 2.40+ | Version control |
| **RAM** | 8 GB minimum | 16 GB recommended for full FL simulation |
| **Storage** | 4 GB free | Container images and test data |

---

## 21. Step-by-Step Operator Quick Start

### Step 1: Clone Repository and One-Click Enterprise Launch
```bash
git clone https://github.com/yusufcalisir/CF-Intelligence.git
cd CF-Intelligence

# 1. Generate cryptographically hardened 256-bit secrets for PostgreSQL, Redis, and JWT
python scripts/generate_secrets.py

# 2. Launch complete production stack (Gateway + Frontend SPA + FastAPI + Postgres 16 + Redis 7.2)
docker compose up -d --build

# 3. Execute pre-flight verification smoke test (asserts zero syntax drift and service health)
python scripts/verify_docker_deployment.py
```
Open `http://localhost` in your corporate browser to access the unified platform with zero CORS configuration friction.

### Step 2: Install Backend Dependencies and Run Pytest Suite (Developer Mode)
```bash
cd backend
pip install -r requirements.txt

# Run Alembic schema migrations (upgrade to latest head: 002_core_and_aml_tables)
alembic upgrade head

# Run backend test suite
pytest tests/ -v
```

### Step 3: Run Interactive Benchmark Suites
```bash
# Run standalone Elliptic AML Bitcoin Graph benchmark
python scripts/run_elliptic_benchmark.py

# Run master multi-dataset benchmark suite (PaySim, IEEE-CIS, Elliptic)
python benchmark.py
```

### Step 4: Launch Web Console (Developer Mode)
```bash
cd frontend
npm install
npm run dev
```
Open `http://localhost:3000` to inspect the visualizer, counterfactual workbench, and live operations dashboard.

### Step 5: Master Test Suites Execution (2,864 Tests Core / 3,171 Total)
```bash
# (Ensure commands are executed from the repository root directory)
# 1. Run full backend pytest suite (2,518 tests)
pytest backend/tests/ -v

# 2. Run Interactive POC Sandbox Replay CLI evaluation
python benchmark.py --poc-replay

# 3. Run full frontend vitest suite (315 tests across 81 test files)
npm --prefix frontend test

# 4. Run Playwright real-browser multi-device E2E suite (10 browser tests)
npm --prefix frontend run test:e2e:workflows
# or from frontend directory: npx playwright test e2e-workflows --project=desktop-1440-chromium

# 5. Run Playwright strict visual regression testing suite (3 baseline comparisons)
npm --prefix frontend run test:visual

# 6. Run Kubernetes manifest dry-run validation suite (39 rendered resources)
python scripts/validate_k8s_manifests.py --all

# 7. Run master scientific invariant verification suite (18 modules, 307 tests)
python scripts/run_all_verifications.py
```

---

## 22. Development Methodology & AI Collaboration

This platform was engineered using a human-directed pair-programming workflow leveraging modern AI coding tools as productivity accelerators:

- **Human Lead Systems Architecture & Domain Engineering:** All core system topology designs, algorithmic selections (`FedProx`, `SCAFFOLD`, `Krum / Bulyan`, `GraphSAGE`), mathematical formulations, threat modeling, regulatory alignment patterns, and domain abstractions were conceived, designed, and directed by the author (**Yusuf Çalışır**).
- **AI-Assisted Productivity Tooling:** AI foundation models (Claude, Gemini, and Antigravity) were utilized as interactive engineering tools for boilerplate synthesis, expanding test fixtures, drafting documentation, and diagnosing edge-case regressions:

| Engineering Responsibility | Primary Ownership | AI Collaboration Scope |
| :--- | :---: | :--- |
| **System Architecture & Topology** | Human Lead | Conceptual design, component boundaries, threat modeling |
| **Algorithm Selection & Invariants** | Human Lead | Mathematical equations, DP calibration, Byzantine defense rules |
| **Core Domain & Service Logic** | Human + AI Pair | Python 3.12 implementation, FastAPI routers, PyTorch models |
| **Test Suite Engineering** | Human + AI Pair | Pytest unit/integration tests, Hypothesis property tests |
| **Documentation & Benchmarks** | Human + AI Pair | Markdown specifications, benchmark scripts, verification logs |

---

## 23. Related Work and References

1. McMahan, B., et al. (2017). *Communication-Efficient Learning of Deep Networks from Decentralized Data.* AISTATS.
2. Bonawitz, K., et al. (2017). *Practical Secure Aggregation for Privacy-Preserving Machine Learning.* ACM CCS.
3. Abadi, M., et al. (2016). *Deep Learning with Differential Privacy.* ACM CCS.
4. Blanchard, P., et al. (2017). *Machine Learning with Adversaries: Byzantine Tolerant Gradient Descent.* NeurIPS.
5. Yin, D., et al. (2018). *Byzantine-Robust Distributed Learning: Towards Optimal Statistical Rates.* ICML.
6. El Mhamdi, E. M., et al. (2018). *The Hidden Vulnerability of Distributed Learning in Byzantium.* ICML.
7. Hamilton, W. L., et al. (2017). *Inductive Representation Learning on Large Graphs.* NeurIPS.
8. Geyer, R. C., et al. (2017). *Differentially Private Federated Learning: A Client Level Perspective.* NeurIPS Workshop.

---

## 24. Academic Citation and Reference Format

```bibtex
@software{calisir2026cfintelligence,
  author       = {Yusuf {\c{C}}al{\i}{\c{s}}{\i}r},
  title        = {Collaborative Fraud Intelligence Platform: Privacy-Preserving Cross-Bank Financial Fraud Detection and Anti-Money Laundering Architecture},
  year         = {2026},
  publisher    = {GitHub},
  journal      = {GitHub Repository},
  howpublished = {\url{https://github.com/yusufcalisir/CF-Intelligence}},
  version      = {1.0.0}
}
```

---

## 25. Author and Maintenance

Designed, developed, and maintained by **Yusuf Çalışır**.

For questions regarding system architecture, federated learning pipelines, or privacy-enhancing technologies, please open an issue in the [GitHub Repository Issues](https://github.com/yusufcalisir/CF-Intelligence/issues).
