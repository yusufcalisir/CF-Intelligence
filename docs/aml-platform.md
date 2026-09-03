# Collaborative AML Intelligence Platform

This document describes the privacy-preserving cross-bank Anti-Money Laundering (AML) features added in Phase 2 of the Collaborative Fraud Intelligence Simulator.

## Overview

Traditional AML tools operate in isolation, looking only at transactions within a single bank. This leaves blind spots for cross-institution activity, such as:
1. **Multi-bank fraud rings** where malicious actors share devices or bank accounts across different institutions.
2. **Layering patterns** where funds are split into smaller transactions and routed through multiple banks to stay below reporting thresholds.
3. **Distributed card testing** where stolen cards are verified via small purchases across multiple banks before executing a large purchase.

The Collaborative AML Intelligence Platform enables banks to resolve entities and share intelligence to address these blind spots without exposing private customer transaction histories.

---

## Core AML Features

### 1. Low-Latency Real-Time Risk Decision API (`POST /v1/transactions/score`)
A dedicated serving gateway providing sub-10ms transaction risk evaluation against the globally trained federated model.

* **Endpoint**: `POST /v1/transactions/score` (and `/api/v1/transactions/score`)
* **Sub-10ms SLA**: Evaluates incoming payments against 9-signal composite risk engine and global PyTorch model weights within a strict 10ms response latency SLA (`latency_ms`).
* **Request JSON Payload**:
```json
{
  "transaction_id": "tx_8941203",
  "account_id": "acc_de_91823",
  "amount": 12500.00,
  "currency": "EUR",
  "merchant_id": "merchant_9912",
  "country": "EE",
  "device_id": "dev_fp_4491a"
}
```
* **Response JSON Payload**:
```json
{
  "risk_score": 873,
  "risk_level": "HIGH",
  "decision": "REVIEW",
  "model_version": "v2.4.1",
  "explanations": [
    { "feature": "merchant_velocity_1h", "contribution": 0.34 },
    { "feature": "cross_entity_device_link", "contribution": 0.27 }
  ],
  "related_entities": [
    { "entity_type": "device", "risk": "HIGH" }
  ],
  "latency_ms": 8
}
```
* **Automated Decision Logic**:
  - `risk_score`: Integer normalized between `0` and `1000`.
  - `risk_level`: Categorical classification (`LOW` for score < 300, `MEDIUM` for score 300–700, `HIGH` for score > 700).
  - `decision`: Automated recommendation (`ALLOW` for LOW, `REVIEW` for MEDIUM/HIGH, `BLOCK` for critical score > 900).
  - `explanations`: Real-time SHAP feature attributions ranking top factors driving the risk score.
  - `related_entities`: Connected entity risk levels (Device, Merchant, Account).

### 2. Collaborative Alert Intelligence
When a bank's local model flags a transaction, it generates a local alert. The bank can optionally publish a stripped-down, privacy-preserving indicator of this alert to the shared layer.
* **Hashed Identifiers**: The shared indicator contains only a deterministic hash of the transaction or customer, not raw PII.
* **Risk Indicator**: A normalized value representing the bank's assessment confidence.
* **Reason Codes**: Standard codes (e.g. `VEL-001` for velocity, `GEO-RISK` for high-risk country) that help other banks assess relevance.

### 2. Privacy-Preserving Entity Resolution
To correlate activity across banks without disclosing PII, the platform utilizes **deterministic HMAC-SHA256 hashing**.
* Each bank hashes sensitive identifiers (e.g., customer emails, phone numbers, or account IDs) using a shared HMAC key.
* The resulting hashes are compared at the shared layer. If `bank_a` and `bank_b` both see the hash `e3b0c442...`, they know they are dealing with the same real-world entity without ever knowing who that customer is.

### 3. Entity Relationship Graph
Resolved entities and their connections are mapped in an in-memory relationship graph:
* **Nodes**: Represent privacy-preserving entities (Customers, Devices, Merchants, IP Addresses).
* **Edges**: Represent relationships (owns account, transacted with merchant, used device, shared IP address).
* **Interactive Visualization**: Serialized to React Flow for visual representation, highlighting high-risk clusters that indicate organized crime.

### 4. 9-Signal Risk Engine
A composite risk scoring pipeline combines multiple independent signals to produce a unified score between `0` and `1000`:
1. **ML Model Prediction**: Federated model classification score.
2. **Velocity Rules**: Rate of transactions per hour.
3. **Merchant Reputation**: Historical fraud rates at the target merchant.
4. **Geographical Risk**: Origin of transaction (e.g., known high-risk country codes).
5. **Device Anomaly**: High-risk device categories or channels (e.g., telephone banking).
6. **Customer History**: Historical customer score and account age.
7. **Previous Alerts**: Prior alerts generated for this entity.
8. **Chargeback History**: Historical chargeback rates.
9. **Behavior Anomaly**: Deviation from the customer's typical transaction baseline.

---

## Fraud Scenarios Simulator

The platform includes a real-time scenario simulator to showcase how collaborative intelligence improves detection over single-bank observation:

1. **Cross-Institution Fraud Ring**:
   * *Behavior*: 4 actors share devices and IP addresses to execute transactions across 3 banks.
   * *Outcome*: Individual banks see separate events with a medium confidence (~62%). Collaborative resolution connects the nodes, triggering a critical alert (~91% confidence).
2. **Account Takeover (ATO)**:
   * *Behavior*: Attacker registers a new device, changes contact details, and executes rapid withdrawals.
   * *Outcome*: Cross-bank intelligence flags the synchronized target profile and escalates the risk rating immediately.
3. **Layered Money Laundering**:
   * *Behavior*: Large deposit at bank A is split into sub-threshold payments to banks B & C before being consolidated.
   * *Outcome*: Individually, the transactions look like small, normal wires. The shared intelligence layer tracks the split flow and highlights the layering pattern.
4. **Card Testing**:
   * *Behavior*: Cards are tested with sub-$5 transactions at different merchants/banks.
   * *Outcome*: Individual banks ignore the minor transactions. Graph correlation links the test charges to reveal the card testing ring before large-scale card draining occurs.
5. **Interactive Smurfing / Layering High-Velocity Burst (500 tx/s)**:
   * *Behavior*: Syndicate executes high-frequency sub-threshold transfers ($4,850 – $9,950) across Bank Alpha, Bank Beta, and Bank Gamma.
   * *Outcome*: Intercepted in real time via GraphSAGE relational embeddings and MinHash LSH Private Set Intersection.
6. **Byzantine Poisoned Gradient Injection & Krum Quarantine**:
   * *Behavior*: Compromised participant node (Bank Gamma) injects maliciously scaled, inverted weights ($\Delta w \times -10.0$) during federated aggregation.
   * *Outcome*: Krum defense shield detects Euclidean distance anomaly ($\Delta = 48.2 > 14.1$), rejects the gradient, triggers visual node quarantine (`QUARANTINED BY KRUM`), and preserves global model AUC (+0.42).

---

## 🧬 Feature Store Integration (Feast / Hopsworks)

To handle production scale and meet strict SLA latency bounds (<50ms), the platform integrates a dual offline-online Feature Store (simulating Feast / Hopsworks):

### 1. Online Feature Store (Redis-backed)
- Serves live features to the `RiskScoringEngine` and MLP inference pipeline in real time.
- Uses a unified entity mapping (`customer_id`, `merchant_id`) to pull aggregated vectors under strict latency bounds (<5ms execution).

### 2. Offline Feature Store (Snowflake / BigQuery)
- Provides point-in-time joins (`get_historical_features`) over historical logs.
- Guarantees data leakage prevention by ensuring features represent the state of entities exactly as they existed at the transaction timestamp.

## 🏦 Real-Bank Connector Integrations & Ingestion Adapters

To interface with live production financial systems and process real-time transaction streams without relying solely on synthetic data, the platform provides concrete connector implementations and financial message parsing services under `backend/app/infrastructure/connectors/`:

### 1. Standardized `NormalizedTransaction` Domain Contract
All incoming feeds (ISO XML, SWIFT MT103, REST webhooks, streaming queues, EOD files) are converted into a unified `NormalizedTransaction` Pydantic model (`base_connector.py`), enforcing consistent field extraction downstream:
* `transaction_id`: Cryptographic or message reference ID.
* `account_id` / `counterparty_account_id`: Originating debtor and target creditor accounts.
* `amount` / `currency`: Transaction amount and ISO 4217 currency code.
* `timestamp`: UTC event timestamp.
* `merchant_category_code` (MCC): 4-digit ISO 18245 code.
* `origin_country` / `destination_country`: ISO 3166-1 alpha-2 country codes.
* `channel_type`: High-level channel identifier (`ISO20022_PACS008`, `SWIFT_MT103`, `STREAMING`, `REST_WEBHOOK`, `BATCH_EOD`).

### 2. Concrete Adapter Implementations
* **Streaming Payment Connector (`StreamingPaymentConnector`)**: Ingests high-volume continuous payment events from Kafka, RabbitMQ, or Redis streams, updating the in-memory graph stream and streaming GAT models.
* **ISO 20022 & SWIFT Message Parser (`ISO20022MessagingConnector`)**: Financial message parser supporting ISO 20022 MX (`pacs.008.001.08` & `pacs.009` XML) and legacy SWIFT MT103/MT202 records.
* **Batch EOD File Connector (`BatchEODFileConnector`)**: End-Of-Day file parser for batch CSV and Parquet transaction dumps.
* **Real Dataset Ingestion Studio & Data Contract Validator (`datasets.py` & `piiSanitizer.ts`)**: Interactive drag-and-drop CSV/Parquet ingestion pipeline featuring client-side Zero-PII sanitization (Luhn PAN check, IBAN/TCKN regex, Type-Salted HMAC tokenization) and 12-rule Great Expectations data contract audits.
* **Core Banking System REST Adapter (`RESTBankConnector`)**: Dynamically handles OAuth2 Client Credentials authentication token refresh, mTLS certificate validation, HMAC payload signing, and real-time webhook ingestion.
* **Message Queue AMQP Connector (`RabbitMQBankConnector`)**: Subscribes asynchronously to CBS AMQP queues (using `pika`) with graceful fallback if the broker is offline.

### 3. Configurable Factory Resolution (`factory.py`)
* `BankConnectorFactory` inspects per-bank configuration settings (`{bank_id}_connector_type`) and instantiates the matching adapter (`mock`, `rest`, `redis`, `rabbitmq`, `streaming`, `iso20022`, `batch`), enabling seamless bank-specific adapter swapping.

---

## 📄 AML Case Management Workflow Alignment (Phase 2 Task 7)

To satisfy national Financial Intelligence Unit (FIU) standards (e.g. FinCEN, MASAK) and support judicial admissibility:

### 1. SAR Filed Lifecycle Status
- Added `SAR_FILED` state to case management workflow.
- Allowed state transitions: `Escalated` -> `SAR Filed` and `SAR Filed` -> `Closed Confirmed`.

### 2. Automatic E-Filing Report Generation
- **FinCEN XML Compiler**: Transitions into `SAR_FILED` status trigger the `RegulatoryReporterService` to compile case details, timeline events, investigator notes, and suspect hashes into a fully schema-compliant FinCEN BSA Suspicious Activity Report (SAR) XML file.
- Files are persisted locally under `storage/regulatory_filings/` and exposed via secure FastAPI download endpoints (`/api/v1/cases/{case_id}/sar-report`).

### 3. Cryptographic Timeline Audit Chain
- Implemented sequential SHA-256 block hashing over case events:
  $$H_i = \text{SHA-256}(timestamp \mathbin{\Vert} type \mathbin{\Vert} description \mathbin{\Vert} actor \mathbin{\Vert} H_{i-1})$$
- Enforces an append-only, tamper-proof log of all investigator movements and status transitions. Changing or deleting any event breaks downstream block verification, ensuring audit credibility.

---

## 🔍 Full-Fledged AML Investigation Lifecycle (Phase 2 Task 8)

To support full-lineage auditing, secure dual-authorization, and role-based tracking:

### 1. Case Evidence Registry
- **Immutable Evidence Store**: Exposes a dedicated storage layer (`RedisStore("evidence")`) for document files, KYC profiles, and ledger proofs linked to investigation cases.
- **Cryptographic Content Hashing**: Enforces SHA-256 hashing of evidence contents upon registration. The hash is saved directly in the registry to guarantee chain-of-custody and prevent unauthorized modifications to investigation files.
- Exposed in the user interface under the Case Details page, allowing investigators to upload documents and view registered records with their hashes.

### 2. Multi-Signature Gating (Four-Eyes Principle)
- **Closure Validation**: Gated final case closure statuses (`CLOSED_CONFIRMED` and `CLOSED_FALSE_POSITIVE`) behind supervisor approval.
- **Secondary Signature Verification**: Status change API requests require a valid `supervisor_signature` key that must not be empty and must be different from the analyst actor's name.

### 3. Investigator Role Auditing
- **Compliance Activity Logging**: Active logs track analyst actions including case accesses (`access_case`), entity profile queries (`query_entity`), cross-bank resolution requests (`cross_bank_resolve`), and Private Set Intersection matching (`cross_bank_psi`).
- **Session Duration Tracking**: Logs session durations to detect anomalous queries or internal threats.
- Exposed in a real-time investigator activity audit trail grid at the bottom of the main Investigation Dashboard.

### 4. Closed-Loop Retraining Feedback Pipeline & Human-in-the-Loop Architecture
- **Closed-Loop Ground Truth Feedback**: When an investigator closes a case as `CLOSED_CONFIRMED` (Confirmed Fraud / True Positive) or `CLOSED_FALSE_POSITIVE` (Legitimate Activity / False Positive), the confirmed label (`actual_label = 1` or `0`) is automatically written directly into the local training dataset.
- **Model Evaluation Engine (`log_feedback`)**: Invokes `ModelEvaluationEngine.log_feedback` to evaluate Champion/Challenger prediction performance in real time.
- **Federated Retraining Loop**: Local PyTorch model training epochs consume these confirmed analyst labels. Upgraded local model weights are subsequently aggregated during the next federated round, improving fraud detection accuracy across the entire consortium.

---

## 🤝 Consortium Incentive Mechanisms & Client Contribution Auditing (Phase 2 Task 22)

To align institutional incentives and enforce accountability in commercial federated anti-fraud network consortia:

### 1. Federated Shapley Value (SV) Estimation
- **Leave-One-Out (LOO) Shapley Evaluation**: At the end of a simulation, the coordinator automatically computes the marginal utility contribution of each participant bank.
- **Auditing Protocol**:
  1. Measures the baseline performance (F1-score) of the aggregate global model ($F_{\text{global}}$) on the shared global validation dataset.
  2. Aggregates subsets of client parameters excluding one client at a time (LOO model).
  3. Evaluates the LOO model to find its validation performance ($F_{-i}$).
  4. Calculates the marginal contribution score: $SV_i = F_{\text{global}} - F_{-i}$.

### 2. Free-Rider & Poisoning Detection (Quarantine Trigger)
- **Free-Rider Identification**: The auditing service monitors the variance of client parameter updates relative to the global model state. A client update with variance below $10^{-6}$ indicates zero-variance training (a client returning raw base weights or random noise to acquire global weights without training).
- **Poisoning Isolation**: If a client's marginal contribution score $SV_i \le -0.05$, it means including their updates significantly degrades the overall model accuracy, indicating malicious parameter poisoning.
- **Client Quarantine**: Flagged clients are dynamically set to `QUARANTINED` and their connection status becomes `ClientStatus.OFFLINE`. They are automatically excluded from participating in subsequent aggregation rounds.

### 3. Web3 & CBDC Smart Contract Incentive Settlement
- **Programmatic On-Chain Clearing**: Replaces static virtual payout calculations with automated smart contract token disbursements on EVM / Sepolia testnets via [ConsortiumIncentiveSettlement.sol](file:///contracts/ConsortiumIncentiveSettlement.sol).
- **Tokenized Asset Support**: Programmatically disburses Wholesale CBDC (`wCBDC`), Fiat-Backed Stablecoins (`USDC`), or Digital Lira (`e-TRY`) in 18-decimal token wei precision upon simulation epoch completion.
- **LOO Shapley Weighting & Basis Points**: Leave-One-Out Shapley contribution scores ($SV_i$) are scaled to basis points (`bps`) and mapped directly to participant bank wallet addresses.
- **Quarantine Enforcer**: If a node is flagged for model poisoning ($SV_i \le -0.05$) or free-riding (update variance $< 10^{-6}$), `setNodeQuarantine()` locks the node's wallet on-chain, setting payout amounts to `$0.00` with state `BLOCKED_QUARANTINE`.
- **Cryptographic Audit Hash Binding**: Every transaction hash (`settlement_tx_hash`) and block number (`settlement_block_number`) generated by [smart_contract_driver.py](file:///backend/app/infrastructure/security/smart_contract_driver.py) is appended to the tamper-proof SHA-256 audit ledger (`immutable_audit_chain.py`) for verifiable on-chain auditability.

---

## 🔒 Hardware and Cryptographic Security Drivers (TEE & FHE)

To maximize the security of the collaborative AML platform, hardware and cryptographic isolation mechanisms have been added:

### 1. Trusted Execution Environment (TEE - Intel SGX / AWS Nitro)
- **Secure Memory Aggregation:** Raw model parameters from clients are collected as plaintext only within the isolated hardware memory regions (enclave) of the TEE. No actor outside the enclave, including the host operating system or hypervisor, can access these parameters.
- **Remote Attestation:** The integrity of the code running inside the enclave is verified using `MRENCLAVE` (code measurement) and `MRSIGNER` (signing authority) digests. Training rounds do not start unless the cryptographic signature verification is successful.

### 2. Fully Homomorphic Encryption (FHE - CKKS)
- **Computation on Encrypted Data:** Clients send their model updates encrypted with the FHE public key. The server aggregates these updates homomorphically (homomorphic average) directly over the ciphertexts without decrypting them. The server never observes plaintext parameters at any stage.
- **Noise and Dimension Management:** Performance/security tradeoffs are monitored dynamically via CKKS noise accumulation and key management simulation.

---

## ⚡ Real-Time Streaming GNN Dynamics

To capture the temporal and structural evolution of financial networks, the platform supports real-time streaming GNNs:

### 1. Dynamic Sliding-Window Graph Stream
- **Dynamic Ingestion:** Incoming mock transactions from REST/AMQP queues are streamed into a graph buffer in real time, dynamically building/updating nodes (accounts, devices) and edges (transactions).
- **Time-bound Pruning:** Old edges and disconnected nodes are automatically pruned using a sliding-window threshold (e.g. 60 minutes) to prevent memory leakages and focus on recent active patterns.

### 2. Incremental Online GNN Training
- **Graph Attention Network (GAT):** Implements multi-head self-attention coefficients dynamically over topological neighborhoods to learn complex fraud propagation paths.
- **Incremental Training:** Runs continuous online backpropagation training steps directly on active transaction streams, computing self-supervised contrastive or classification loss metrics to dynamically refine model state.
- **Telemetry & Visualization:** Live stats (node/edge counts), attention distributions, and online loss convergence charts are rendered dynamically on the frontend.

---

## 🛡️ Active Defense & Adversarial Training (Adversarial ML Defense)

To harden local bank models against evasion attacks where fraudsters perturb transaction features to bypass detection bounds:

### 1. Adversarial Evasion Generators (FGSM & PGD)
- **Fast Gradient Sign Method (FGSM)**: 1-step gradient perturbation $x_{\text{adv}} = x + \epsilon \cdot \text{sign}(\nabla_x \mathcal{L})$.
- **Projected Gradient Descent (PGD)**: 5-step iterative perturbation $x^{t+1} = \Pi_{x+S} (x^t + \alpha \cdot \text{sign}(\nabla_{x^t} \mathcal{L}))$.
- **Tabular Constraint Projection ($\Pi_{\mathcal{X}}$)**: Enforces $L_\infty$ perturbation noise bounds ($\epsilon \in [0.01, 0.25]$) while projecting non-negative transaction constraints and normalized feature bounds to maintain realistic domain inputs.

### 2. Robust Training Loss Formulation
- Blends clean loss and adversarial loss during local SGD iterations:
  $$\mathcal{L}_{\text{total}} = \lambda \mathcal{L}(f_\theta(x_{\text{clean}}), y) + (1-\lambda) \mathcal{L}(f_\theta(x_{\text{adv}}), y)$$
- Calculates Clean Accuracy vs Robust Accuracy under FGSM & PGD evasion stress tests, outputting evasion rejection rates on the glassmorphic `AdversarialDefensePanel`.

---

## 🏛️ Audit Compliance & Model Governance (Fed SR 11-7)

To meet Federal Reserve SR 11-7 Model Risk Management guidelines and regulatory audit standards across multi-bank consortiums:

### 1. Semantic Versioning & Dual Cryptographic Sign-Off Gating
- **Semantic Versioning (`SemanticVersion`)**: Tracks model versions using semver standard tags (`v1.0.0`, `v2.4.1`).
- **Dual Sign-Off Gate (`DualSignoffGate`)**: Candidate models cannot be promoted to champion/production state without valid cryptographic signatures from **both**:
  1. **ML Engineering Lead** (`ml_engineer` role)
  2. **Bank Compliance Officer** (`compliance_officer` role)

### 2. MLOps Shadow Deployment & Canary Testing
- **10% Shadow Routing (`ShadowDeploymentEngine`)**: Promoted candidate models shadow 10% of live prediction traffic based on MD5 request ID hash routing, executing alongside the active champion model without impacting production decisions.
- **Canary Metric Comparison**: Evaluates candidate shadow ROC-AUC against active champion ROC-AUC to confirm model superiority before 100% traffic shift.

### 3. Automatic Safety Rollback Trigger
- **Telemetry Safety Thresholds (`AutomaticRollbackTrigger`)**: Live model performance is continuously monitored against two hard safety limits:
  - **Live ROC-AUC Drop**: Automatically rolls back to the previous champion model if live ROC-AUC falls below `0.65`.
  - **Latency Spike**: Automatically rolls back if 99th percentile inference latency exceeds `200 ms`.

### 4. Cryptographic Audit Lineage Manifest
- **Audit Manifest (`CryptographicAuditLineage`)**: Every model iteration binds a cryptographic lineage record:
  - Model Version Tag (`vX.Y.Z`)
  - Git Commit SHA
  - Training Dataset SHA-256 Hash
  - Differential Privacy Budget ($\epsilon, \delta$)
  - Cryptographic Sign-Off Signatures and Timestamps

---

## 📊 Empirical Benchmarks & Experimental Validation (Phase 5 Section 5.4)

To scientifically quantify accuracy, latency, communication payload, and differential privacy trade-offs under extreme class imbalance ($< 0.1\%$ fraud rate), the platform provides an automated benchmarking suite (`benchmark.py` and `backend/app/domain/metrics_service.py`):

| Model Configuration | PR-AUC | ROC-AUC | Recall@0.1%FPR | P@100 | Latency (ms) | Payload (MB) | DP ($\epsilon$) | OOD Delta |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Local-Only Model (Bank A)** | 0.3489 | 0.9457 | 0.2600 | 0.2500 | 3.80 | 0.00 | N/A | -0.1420 |
| **Centralized Pooled (Non-Private)** | 0.9925 | 1.0000 | 0.9800 | 0.5000 | 6.20 | 142.50 | N/A | +0.0450 |
| **Standard FedAvg** | 0.8954 | 0.9991 | 0.7800 | 0.4800 | 4.10 | 1.25 | N/A | +0.0210 |
| **FedProx ($\mu = 0.01$)** | 0.9618 | 0.9997 | 0.9400 | 0.5000 | 4.50 | 1.25 | N/A | +0.0320 |
| **FedGNN (Graph Attention Network)** | 0.9789 | 0.9998 | 0.9600 | 0.4900 | 7.40 | 2.40 | N/A | +0.0480 |
| **Federated + Privacy Entity Intelligence** | 0.9494 | 0.9994 | 0.9000 | 0.4800 | 8.90 | 3.10 | 2.5 | +0.0410 |

### Key Experimental Insights
1. **Federated Superiority over Isolated Models**: Collaborative training boosts PR-AUC from 0.3489 (Local-Only) to 0.8954+ (Federated), proving small regional banks gain significant fraud detection power without centralizing raw data.
2. **FedGNN & Non-IID Handling**: Graph structural embeddings (FedGNN) achieve 0.9789 PR-AUC, closing 98.6% of the gap to non-private centralized upper bounds while preserving data sovereignty.
3. **Privacy-Preserving Trade-off**: Combined FedGNN + DH-PSI + Opacus DP ($\epsilon=2.5$) retains 0.9494 PR-AUC and sub-10ms inference latency ($8.90$ ms), meeting enterprise SLA targets.

---

## 🇪🇺 Cross-Border Data Sovereignty & EU AI Act Compliance (Phase 6 Section 6.1)

To meet Tier-1 bank CISO, Legal Compliance, and EU AI Act Regulation (EU) 2024/1689 requirements:

### 1. Regional Aggregation Rings (`regional_governance.py`)
- **Geographic Segregation**: Bank nodes are partitioned into regional clusters (`EU-Central`, `US-East`, `APAC-Singapore`).
- **Intra-Region Aggregation**: Model updates are aggregated locally within regional boundaries.
- **Inter-Region DP Meta-Aggregation**: Inter-regional meta-aggregation applies Differential Privacy noise scrubbing ($\epsilon_{\text{inter}}$) before cross-border transfer.
- **Schrems II / GDPR Article 22 Compliance**: `CrossBorderSovereigntyFilter` strictly blocks unencrypted or non-DP-scrubbed raw model parameter transfers across geographic boundaries.

### 2. EU AI Act High-Risk AI Compliance Engine (`ai_act_compliance.py`)
- **Articles 10-15 Mandate Verification**: Automatically generates immutable JSON compliance certificates (`storage/regulatory_filings/eu_ai_act_certificate_{version}.json`) binding:
  - **Article 10 (Data Governance)**: Zero Raw PII Policy & Bias Mitigation bounds.
  - **Article 11 (Technical Documentation)**: Federated learning lineage and git commit SHA binding.
  - **Article 12 (Record-Keeping)**: Automated OpenTelemetry trace context logs.
  - **Article 13 (Transparency)**: SHAP feature explainability attributions.
  - **Article 14 (Human Oversight)**: Four-Eyes Principle dual analyst/supervisor sign-offs.
  - **Article 15 (Accuracy & Robustness)**: Adversarial PGD evasion rejection scores & mTLS PKI verification.




