# System Card: CF-Intelligence Enterprise Federated Fraud & AML Platform

**System Name:** CF-Intelligence Collaborative Intelligence Platform  
**System Version:** `2.4.0`  
**Classification:** Financial AI Research & Engineering Framework (AI Risk & Governance Informed)  
**System Operator:** Research / portfolio implementation by Yusuf Çalışır  
**Deployment Model:** Designed for future multi-institution consortium deployment; no commercial banking consortium is currently operating this system.  
**Release Date:** September 2026  
**License:** MIT License  
**Governance & Security Alignment:** Engineered with controls informed by EU AI Act (Articles 9–15 principles), Federal Reserve SR 11-7 / OCC 2011-12 Model Risk Management, PCI-DSS v4.0 & SOC 2 Type II architectural concepts, ISO 20022 message schema structures, and FinCEN BSA e-Filing guidelines.  

---

## 1. System Overview & Executive Summary

The CF-Intelligence Platform is an open-source, production-oriented research and engineering platform for privacy-preserving federated fraud detection and Anti-Money Laundering (AML) across simulated multi-institution banking perimeters. It evaluates collaborative machine learning across distributed financial topologies without sharing, pooling, or exposing raw customer PII or transaction records.

The platform couples sub-3ms pre-authorization transaction risk scoring (`DeepFraudMLP`) with multi-hop graph topology analysis (`FedGNN-GraphSAGE`), real-time CloudEvents 1.0 streaming, and an investigator workbench governed by Four-Eyes dual control.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        CF-INTELLIGENCE SYSTEM TOPOLOGY                                 │
├────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                        │
│   Bank Node Alpha         Bank Node Beta           Bank Node Gamma     Regulator Node  │
│   ┌───────────────┐       ┌───────────────┐       ┌───────────────┐   ┌────────────┐   │
│   │ Local Core    │       │ Local Core    │       │ Local Core    │   │ Read-Only  │   │
│   │ Banking Rails │       │ Banking Rails │       │ Banking Rails │   │ Regulatory │   │
│   │ (ISO 20022)   │       │ (ISO 20022)   │       │ (ISO 20022)   │   │ Telemetry  │   │
│   └───────┬───────┘       └───────┬───────┘       └───────┬───────┘   └─────▲──────┘   │
│           │                       │                       │                 │          │
│           ▼                       ▼                       ▼                 │          │
│   ┌───────────────┐       ┌───────────────┐       ┌───────────────┐         │          │
│   │ Salted Token- │       │ Salted Token- │       │ Salted Token- │         │          │
│   │ ization & PII │       │ ization & PII │       │ ization & PII │         │          │
│   │ Sanitization  │       │ Sanitization  │       │ Sanitization  │         │          │
│   └───────┬───────┘       └───────┬───────┘       └───────┬───────┘         │          │
│           │                       │                       │                 │          │
│           ▼                       ▼                       ▼                 │          │
│   ┌───────────────┐       ┌───────────────┐       ┌───────────────┐         │          │
│   │ Differential  │       │ Differential  │       │ Differential  │         │          │
│   │ Privacy (RDP) │       │ Privacy (RDP) │       │ Privacy (RDP) │         │          │
│   │ Local Training│       │ Local Training│       │ Local Training│         │          │
│   └───────┬───────┘       └───────┬───────┘       └───────┬───────┘         │          │
│           │                       │                       │                 │          │
│           │   Homomorphic Encrypted Weight Deltas (TenSEAL CKKS)  │                 │          │
│           └───────────────────────┼───────────────────────┘                 │          │
│                                   ▼                                         │          │
│                  ┌─────────────────────────────────┐                        │          │
│                  │  Federated Aggregator & Engine  │                        │          │
│                  │  - Pairwise SecAgg (Zero-Sum)   │                        │          │
│                  │  - Byzantine Defense (Bulyan)   │────────────────────────┘          │
│                  │  - Spectral Anomaly Filter      │                                   │
│                  └────────────────┬────────────────┘                                   │
│                                   │                                                    │
│                                   ▼                                                    │
│                  ┌─────────────────────────────────┐                                   │
│                  │ Enterprise Inference Gateway    │                                   │
│                  │ - 1,394.7 req/s Peak Throughput │                                   │
│                  │ - 2.29ms Fast-Path Latency      │                                   │
│                  └────────────────┬────────────────┘                                   │
│                                   │                                                    │
│                                   ▼                                                    │
│                  ┌─────────────────────────────────┐                                   │
│                  │ Four-Eyes Case Management UI    │                                   │
│                  │ - Tier 1 Triage & Fast Action   │                                   │
│                  │ - Tier 2 Supervisor Approval    │                                   │
│                  │ - SHA-256 Tamper-Evident Audit  │                                   │
│                  └─────────────────────────────────┘                                   │
│                                                                                        │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Clean Architecture & Layer Responsibilities

The system enforces strict Clean Architecture separation across four decoupled layers with unidirectional inward dependency flow:

| Layer | Component Scope | Technology Stack | Key Responsibilities |
| :--- | :--- | :--- | :--- |
| **Domain Layer** | `backend/app/domain/` | Pure Python, Zero Framework Dependencies | Pure business logic, fraud entities, invariant rules, Byzantine defense algorithms (`Bulyan`, `Krum`, `SpectralDefense`), Shapley consortium value allocation. |
| **Application Layer** | `backend/app/application/` | Python 3.12, Pydantic v2, Tenacity | Use case orchestration, federated round coordination, SAR e-filing pipelines, drift detection services, case workflow state machine. |
| **Infrastructure Layer** | `backend/app/infrastructure/` | PyTorch, PyG, TenSEAL, Opacus, PostgreSQL, Redis, Kafka, PKCS#11 | Database persistence, multi-tenant schema isolation, mTLS 1.3 cryptographic engines, Vault transit KMS wrapper, CloudEvents 1.0 streaming. |
| **Presentation Layer** | `backend/app/presentation/` | FastAPI 0.115, React 19, TypeScript 5.8, Tailwind CSS | 42 modular REST API endpoints, real-time WebSocket telemetry, interactive Case Management workbenches, and SIEM event broadcasters. |

---

## 3. Regulatory Compliance & Institutional Governance

### 3.1 EU AI Act Alignment & Model Governance (Articles 9–15 Principles)
*Regulatory Context:* Under the EU AI Act (Regulation (EU) 2024/1689 Annex III Item 5(b)), AI systems intended for evaluating creditworthiness or credit scoring are categorized as High-Risk AI Systems, while AI systems used specifically for detecting financial fraud are explicitly excluded from that designation. CF-Intelligence does not claim to be a statutory high-risk system or to have completed formal third-party conformity assessment; rather, it proactively benchmarks and demonstrates full technical alignment with Articles 9 through 15 requirements to model institutional financial AI best practices:
- **Article 9 (Risk Management System)**: Continuous model risk management governed by [`docs/model_risk_management_sr11_7.md`](docs/model_risk_management_sr11_7.md), systematically auditing failure modes (FM-01 through FM-04) and maintaining automated risk controls.
- **Article 10 (Data & Data Governance)**: Rigorous 7-dataset audit confirming zero protected demographic PII; operational proxy evaluations under the EEOC Four-Fifths rule ($0.80 \le \mathrm{DIR} \le 1.25$).
- **Article 11 (Technical Documentation)**: Comprehensive documentation maintained in [`docs/architecture.md`](docs/architecture.md), [`MODEL_CARD.md`](MODEL_CARD.md), and [`SYSTEM_CARD.md`](SYSTEM_CARD.md).
- **Article 12 (Record-Keeping & Logging)**: Cryptographic SHA-256 hash-chained audit trails recording all inference events, feature vectors, model versions, and analyst sign-offs.
- **Article 13 (Transparency & Provision of Information)**: Multi-level explainability via SHAP attributions, Integrated Gradients, and GNNExplainer topological path extraction.
- **Article 14 (Human Oversight)**: Mandatory Human-in-the-Loop workflow requiring Four-Eyes dual control for high-risk alerts and automated block overrides.
- **Article 15 (Accuracy, Robustness & Cybersecurity)**: Multi-seed statistical verification ($\mu \pm \sigma$), Byzantine-resilient aggregation (withstanding up to 33% malicious clients), and mTLS zero-trust communication.

### 3.2 Federal Reserve SR 11-7 / OCC 2011-12 Model Risk Management
- **Conceptual Soundness**: Models are validated across 7 real and synthetic benchmarks, demonstrating statistical convergence and multi-hop graph detection uplift.
- **Ongoing Monitoring**: Automated Population Stability Index (PSI > 0.25) and Kolmogorov-Smirnov drift alarms trigger dynamic canary retraining.
- **Outcomes Analysis**: Fine-grained error stratification across Transaction Amount, Diurnal Quadrant, Merchant Category Code, and Network Degree.
- **Dual Cryptographic Sign-Off**: Model promotion to `PRODUCTION` requires mutual digital signatures from an ML Engineer and a Compliance Officer.

### 3.3 Financial Intelligence & Regulatory e-Filing
- **FinCEN BSA / AMLA / UNODC goAML**: Automated export of Suspicious Activity Reports (SAR) and Unusual Transaction Reports (UTR) in standardized XML/JSON.
- **ISO 20022 Financial Rails**: Native parsing and validation for `pacs.008` (credit transfer), `pacs.002` (payment status), `pacs.003` (direct debit), and `camt.053` (bank statement) messages.
- **Open Banking PSD2**: Strong Customer Authentication (SCA) telemetry and transaction risk analysis (TRA) exemptions.

---

## 4. Human-in-the-Loop & Case Management Workflows

The platform rejects fully autonomous adverse financial actions. Every escalated alert transitions through a state machine governed by human operators:

```
[Inference Gateway: Risk Score S]
          │
          ├── S < 0.50 ──────► Fast-Path Auto-Approve (Audit Logged)
          │
          ├── 0.50 <= S < 0.85 ► Tier 1 Analyst Investigation Workbench
          │                      ├── False Positive ──► Dismiss & Record Reason Code
          │                      └── Confirmed Risk  ──► Escalate to Tier 2 Supervisor
          │
          └── S >= 0.85 ─────► Immediate Pre-Authorization Hold
                                 └── Four-Eyes Dual-Approval Review
                                       ├── Approved Release (Dual Sign-Off)
                                       └── Block & File SAR / goAML XML
```

### 4.1 Four-Eyes Dual Control Guarantee
- **Self-Approval Prohibition**: An investigator cannot approve an alert or file a SAR on a case they originated.
- **Separation of Duties**: Operational analysts (Tier 1) triage alerts; Senior Supervisors / MLROs (Tier 2) execute account restrictions or regulatory disclosures.
- **Tamper-Evident Audit Chain**: Every state transition is appended to an immutable Merkle hash chain:

$$H_i = \mathrm{SHA256}(H_{i-1} \mathbin{\Vert} \mathrm{Timestamp} \mathbin{\Vert} \mathrm{AnalystID} \mathbin{\Vert} \mathrm{Action} \mathbin{\Vert} \mathrm{CaseID})$$

---

## 5. Multi-Tenant Privacy, Security & Cryptographic Perimeter

The platform operates under a Zero-Trust, multi-tenant isolation model informed by security controls from SOC 2 Type II (Trust Services Criteria) and PCI-DSS v4.0 design patterns:

### 5.1 Zero Raw PII Invariant
- Customer identifiers, account numbers, and device fingerprints are deterministically hashed using institution-specific type-salted HMAC-SHA256 tokens before leaving the local core banking boundary.
- Natural person demographic attributes (Age, Gender, Race, Religion, etc.) are strictly excluded by design (0/10 protected attributes present across all evaluated public benchmarks).

### 5.2 Cryptographic Boundaries
- **Differential Privacy (Opacus PRV & RDP Accounting)**: Opacus gradient clipping ($C = 1.0$) and calibrated Gaussian noise injection provide provable formal privacy boundaries (target budget $\epsilon \le 1.0, \delta = 10^{-5}$; canonical empirical benchmarks evaluated at $\epsilon = 0.3497$ with $\sigma = 3.0$ and $\epsilon = 1.7744$ with $\sigma = 1.0$ using `PRVAccountant`).
- **Homomorphic Encryption**: Model updates are encrypted via TenSEAL CKKS (8192-degree polynomial modulus) allowing the central coordinator to aggregate weights in ciphertext space without decryption.
- **Private Set Intersection (DH-PSI)**: Participating banks cross-reference high-velocity accounts using Curve25519 Diffie-Hellman private set intersection without revealing non-overlapping client portfolios.
- **Hardware Security Modules (HSM)**: Cryptographic root keys are enveloped using PKCS#11 HSM interfaces and HashiCorp Vault Transit KMS.
- **mTLS 1.3 & ABAC**: All inter-bank and service-to-service gRPC channels enforce mutual TLS with dynamic Certificate Revocation Lists (CRL) and Attribute-Based Access Control.

---

## 6. Adversarial Robustness, Poisoning Defenses & Fail-Safes

### 6.1 Byzantine-Resilient Federated Aggregation
The central aggregator incorporates robust Byzantine aggregation algorithms to neutralize model poisoning, backdoor injections, and adversarial client manipulation:
- **`Bulyan`**: Integrates Krum candidate selection with trimmed mean coordinate filtering, maintaining mathematical convergence when up to $f < \frac{n-2}{4}$ clients are compromised.
- **`Krum` / `Multi-Krum`**: Selects updates that minimize cumulative Euclidean distance to neighbor vectors.
- **`Coordinate-Wise Median` & `Trimmed Mean`**: Filters dimensional outliers exceeding statistical deviation thresholds.
- **`Spectral Defense`**: Computes top singular vector projections of client weight deltas, purging coordinated low-rank sign-flip attacks.

### 6.2 System Fail-Safes & Resilience
- **Automated Circuit Breakers**: If inference latency spikes or model confidence collapses, the gateway falls back to deterministic rule-based scoring engines within 5ms.
- **Disaster Recovery (Chaos Drills)**:
  - **Recovery Time Objective (RTO)**: **15.01 seconds** (state failover and worker reprovisioning).
  - **Recovery Point Objective (RPO)**: **0 records** (synchronized write-ahead transaction logging).

---

## 7. Deployment Topology, Performance & Scalability

### 7.1 Production Hardware Benchmarks
Evaluated on standard enterprise infrastructure under multi-concurrency stress testing ([`latency_concurrency_benchmark.json`](benchmarks/results/raw/latency_concurrency_benchmark.json)):
- **Single-Request Fast-Path**: **2.29 ms** (well below $< 15\text{ ms}$ SLA; p50: 2.39 ms, p99: 3.53 ms at $C=1$).
- **Peak Throughput**: **1,394.7 requests/second** at $C = 100$ concurrent client channels (peak: 1,791.0 req/s at $C = 50$).
- **Latency Distribution (C = 100 Stress Load)**:
  - **p50**: **37.75 ms**
  - **p95**: **64.72 ms**
  - **p99**: **79.34 ms**
- **Bandwidth Consumption**: Model gradient payloads are compressed by **74.8%** via Zstandard, Top-$k$ sparsification ($k=20\%$), and INT8 quantization.

### 7.2 Multi-Cloud Infrastructure as Code (IaC)
- **Containerization**: Minimal OCI-compliant distroless Docker containers with non-root execution.
- **Orchestration**: Production Helm charts for AWS EKS, Azure AKS, and Google Cloud GKE.
- **Cloud Agnostic**: Terraform IaC modules implementing private VPC subnets, AWS KMS, Azure Key Vault, and GCP Cloud KMS bindings.

---

## 8. Verification & Continuous Validation

The integrity of the system is certified across all layers by **4,110 total automated tests** (100% pass rate):
- **Backend Pytest Suite**: **3,314 automated tests** (unit, integration, chaos, property-based, and security invariants).
- **Scientific Verification Suite**: **409 verification tests** across 21 modules (differential privacy moments accounting, membership inference attack resistance, DLG gradient inversion resilience, test set isolation).
- **Frontend Vitest Suite**: **356 component and integration tests**.
- **Smart Contracts Suite**: **31 Hardhat tests** for consortium Shapley value settlement.

*(Note: 3,314 Backend + 409 Scientific + 356 Frontend + 31 Contracts = 4,110 automated tests. Additional optional E2E/Playwright tests run in separate browser workflows).*
