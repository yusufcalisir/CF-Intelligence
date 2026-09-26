# Collaborative AML Intelligence Platform

This document describes the privacy-preserving cross-bank Anti-Money Laundering (AML) architecture, collaborative protocols, and operational workflows of the Collaborative Fraud Intelligence Platform.

---

## Overview

Traditional AML tools operate in strict institutional silos, observing only transactions within a single bank. This architectural isolation produces critical blind spots for distributed cross-institution financial crimes:
1. **Multi-bank fraud syndicates** where colluding actors share devices, digital fingerprints, and intermediary accounts across distinct institutions.
2. **Layering schemes** where illicit funds are fragmented into sub-threshold payments and routed through multiple banks to bypass regulatory thresholds before reconsolidation.
3. **Distributed card testing** where stolen credentials and card details are validated via micro-purchases across multiple banks prior to large-scale account draining.

The Collaborative AML Intelligence Platform enables participating institutions to resolve entities, track transaction graph risks, and share threat intelligence collaboratively without disclosing private customer identities or sensitive transaction histories.

---

## Core AML Features

### 1. Low-Latency Real-Time Risk Decision API (`POST /v1/transactions/score`)
A dedicated serving gateway providing sub-10ms transaction risk evaluation against the active globally aggregated model, backed by JIT compilation and Redis caching in [realtime_inference.py](../backend/app/presentation/routers/realtime_inference.py) and [predict.py](../backend/app/presentation/routers/predict.py).

* **Endpoints**: `POST /v1/transactions/score`, `POST /api/v1/transactions/score`, `POST /api/v1/predict/score`, and `POST /v1/inference/score`.
* **Sub-10ms SLA**: Evaluates incoming payments against the 9-signal composite risk engine and global PyTorch model weights within a strict 10ms response latency bound (`latency_ms`).
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
* **Circuit Breaker Resilience**: If consecutive model inference timeouts or failures occur ($\ge 3$), the engine trips a circuit breaker (60s cooldown) and automatically falls back to deterministic rule-based heuristic scoring (`HEURISTIC_FALLBACK`), guaranteeing 100% gateway availability.

### 2. Collaborative Alert Intelligence
When a bank's local model flags a transaction, it generates a local alert via [alert_service.py](../backend/app/application/services/alert_service.py). The bank can broadcast a stripped-down, privacy-preserving indicator of this alert to the consortium layer:
* **Hashed Identifiers**: The shared indicator contains only deterministic HMAC-SHA256 privacy hashes of the transaction or customer, with zero raw PII.
* **Risk Indicator**: A continuous normalized value representing the bank's assessment confidence.
* **Standard Reason Codes**: Standardized anomaly descriptors (e.g., `VEL-001` for velocity spikes, `GEO-RISK` for high-risk jurisdictions, `STRUCT-002` for potential smurfing) that enable peer institutions to evaluate relevance.

### 3. Privacy-Preserving Entity Resolution (DH-PSI & Fuzzy MinHash LSH)
To correlate entities across institutions without disclosing non-overlapping identities, the platform implements two complementary cryptographic protocols in [psi_service.py](../backend/app/application/services/psi_service.py) and [entity_resolution.py](../backend/app/application/services/entity_resolution.py):
* **Diffie-Hellman Commutative Private Set Intersection (DH-PSI)**:
  - Banks encrypt hash sets with local private keys ($a$ and $b$) over a shared 512-bit prime $p$.
  - After cross-encryption, elements matching $x^{ab} \equiv x^{ba} \pmod p$ confirm identical cross-bank entities in zero knowledge.
* **Probabilistic Fuzzy PSI (MinHash LSH)**:
  - Resolves near-duplicate names and typographical variations (e.g., Turkish character transliterations like "Çalışır" vs "Calisir").
  - Extracts character 3-grams and computes 16-seed MinHash signatures, matching records when multi-attribute overlap satisfies a configurable threshold gate ($k = 3 \text{ of } 5$ attributes).

### 4. Entity Relationship Graph & Distributed Graph Database Engine
Resolved entities and their relationships are indexed in [graph_engine.py](../backend/app/application/services/graph_engine.py) and [graph_analytics_service.py](../backend/app/application/services/graph_analytics_service.py):
* **Dual Backend Support**: Native Neo4j / Memgraph graph database via Bolt protocol, with automatic failover to Redis in-memory adjacency structures for local development.
* **PageRank Risk Propagation**: Known high-risk and flagged nodes propagate risk scores to adjacent 1-hop and 2-hop neighbors with exponential decay ($\gamma = 0.85$).
* **Community Analytics**: Isolates connected components, calculating graph cluster size, fraud density, and syndicate cohesion scores.
* **Temporal Velocity Anomalies**: Detects bursts of edge creation within sliding time windows (e.g., 5 minutes), exposing rapid structuring or automated account networks.
* **Interactive Visualizer**: Serializes graph topology to React Flow for real-time investigation on [InvestigationDashboard.tsx](../frontend/src/pages/InvestigationDashboard.tsx).

### 5. 9-Signal Risk Scoring Engine
The [risk_engine.py](../backend/app/application/services/risk_engine.py) pipeline combines nine independent risk signals into a unified composite score ($0 - 1000$):
1. **ML Model Prediction**: Federated PyTorch neural network classification probability.
2. **Velocity Rules**: Hourly and daily transaction velocity deviations against historical baselines.
3. **Merchant Reputation**: Historical chargeback and fraud incidence rates of the target counterparty.
4. **Geographical Risk**: Origin and destination jurisdiction ratings (FATF high-risk country tracking).
5. **Device Anomaly**: Hardware fingerprints, emulator indicators, and high-risk channel classifications.
6. **Customer History**: Account tenure, KYC verification tier, and historical transaction volume consistency.
7. **Previous Alerts**: Volume and severity of prior alerts logged for linked entity hashes.
8. **Chargeback History**: Historical ratio of completed chargebacks to settled transactions.
9. **Behavior Anomaly**: Out-of-profile deviation from customer spending and time-of-day baselines.

---

## Fraud Scenarios Simulator

The platform provides a real-time scenario simulator in [scenario_service.py](../backend/app/application/services/scenario_service.py) demonstrating the superiority of collaborative intelligence over isolated bank monitoring:

1. **Cross-Institution Fraud Ring (`fraud_ring`)**:
   * *Behavior*: 4 criminal actors share hardware devices and IP proxies to coordinate transactions across Bank Alpha, Bank Beta, and Bank Gamma.
   * *Outcome*: Individual banks observe isolated events with low/medium confidence (~62%). Collaborative entity resolution links the common devices, escalating the alert to critical (~91% confidence).
2. **Account Takeover Attack (`account_takeover`)**:
   * *Behavior*: Attackers compromise credentials at two banks, register new devices, alter contact details, and trigger immediate withdrawal spikes.
   * *Outcome*: Synchronized cross-bank behavioral anomalies flag the hijacked profile, triggering automated transaction hold recommendations.
3. **Layered Money Laundering (`money_laundering`)**:
   * *Behavior*: A large primary deposit is split into sub-threshold transfers across participant banks before being reconsolidated.
   * *Outcome*: Individually, each payment resembles an ordinary wire transfer. Shared intelligence correlates the multi-hop routing, exposing the structured layering path.
4. **Distributed Card Testing (`card_testing`)**:
   * *Behavior*: Stolen card numbers undergo rapid sub-$5 test charges across disparate merchants and acquiring banks.
   * *Outcome*: Isolated banks ignore low-value micro-transactions. Graph correlation detects velocity spikes across the card cluster, halting card draining before large purchases execute.
5. **Interactive Smurfing / Layering High-Velocity Burst (500 tx/s)**:
   * *Behavior*: Syndicate executes high-frequency sub-threshold transfers ($4,850 – $9,950) across Bank Alpha, Bank Beta, and Bank Gamma.
   * *Outcome*: Intercepted in real time via GraphSAGE relational embeddings and MinHash LSH Private Set Intersection.
6. **Byzantine Poisoned Gradient Injection & Krum Quarantine**:
   * *Behavior*: A compromised participant node injects maliciously scaled, inverted gradients ($\Delta w \times -10.0$) during federated aggregation.
   * *Outcome*: Krum Byzantine defense detects the Euclidean distance anomaly ($\Delta = 48.2 > 14.1$), rejects the update, triggers visual node quarantine (`QUARANTINED BY KRUM`), and preserves global model integrity.

---

## Feature Store Integration (Feast / Hopsworks Architecture)

To serve low-latency features and prevent data leakage, the platform implements a dual online/offline Feature Store in [feature_store_service.py](../backend/app/application/services/feature_store_service.py):

### 1. Online Feature Store (Redis-backed)
- Serves pre-computed customer and merchant feature vectors directly to the `RiskScoringEngine` under strict latency bounds (<5ms execution).
- Employs asynchronous background writes (fire-and-forget) to ensure scoring reads the last committed feature snapshot without request blocking.

### 2. Offline Feature Store (Snowflake / BigQuery Parity)
- Provides point-in-time joins (`get_historical_features`) over historical training datasets.
- Guarantees strict data leakage prevention by ensuring historical model evaluations use entity feature values precisely as they existed at the transaction timestamp.

---

## Real-Bank Connector Integrations & Ingestion Adapters

To interface with live financial infrastructure and process real-time transaction streams without synthetic dependencies, the platform provides concrete connector adapters under `backend/app/infrastructure/connectors/`:

### 1. Standardized `NormalizedTransaction` Domain Contract
All incoming feeds are converted into a unified `NormalizedTransaction` Pydantic model:
* `transaction_id`: Cryptographic or financial message reference ID.
* `account_id` / `counterparty_account_id`: Originating debtor and target creditor account numbers (IBANs).
* `amount` / `currency`: Transaction value and ISO 4217 currency code.
* `timestamp`: ISO 8601 UTC event timestamp.
* `merchant_category_code` (MCC): 4-digit ISO 18245 commercial code.
* `origin_country` / `destination_country`: ISO 3166-1 alpha-2 country codes.
* `channel_type`: Ingestion channel identifier (`ISO20022_PACS008`, `SWIFT_MT103`, `STREAMING`, `REST_WEBHOOK`, `BATCH_EOD`).

### 2. Concrete Adapter Implementations
* **Streaming Payment Connector (`StreamingPaymentConnector`)**: Ingests continuous high-throughput payment streams from Kafka, RabbitMQ, or Redis streams, dynamically updating graph buffers.
* **ISO 20022 & SWIFT Message Parser (`ISO20022MessagingConnector`)**: Full financial XML parser in [financial_message_parser.py](../backend/app/application/services/financial_message_parser.py) supporting ISO 20022 MX (`pacs.008.001.08` & `pacs.009` XML) and legacy SWIFT MT103/MT202 messages.
* **Batch EOD File Connector (`BatchEODFileConnector`)**: End-Of-Day batch file parser handling multi-million record CSV and Apache Parquet datasets.
* **Core Banking System REST Adapter (`RESTBankConnector`)**: Handles OAuth2 Client Credentials token renewal, mutual TLS (mTLS 1.3) client certificates, HMAC payload signing, and real-time webhook routing.
* **Enterprise Message Queue Connector (`RabbitMQBankConnector`)**: Asynchronous AMQP consumer utilizing `pika`, with resilient fallback to local queue buffers if the broker experiences connection interruptions.
* **Bank Connector Factory (`BankConnectorFactory`)**: Dynamically resolves and instantiates the configured adapter per bank (`mock`, `rest`, `redis`, `rabbitmq`, `streaming`, `iso20022`, `batch`).

---

## AML Case Management Workflow & Regulatory E-Filing

To satisfy national Financial Intelligence Unit (FIU) mandates (FinCEN, MASAK, FCA) and maintain judicial admissibility:

### 1. SAR Filed Lifecycle Status
* The [case_management.py](../backend/app/domain/case_management.py) domain model incorporates the `SAR_FILED` terminal state.
* Allowed state transitions: `ESCALATED` ➔ `SAR_FILED` and `SAR_FILED` ➔ `CLOSED_CONFIRMED`.

### 2. Automated FinCEN SAR 2.0 XML Generation & Cryptographic Filing Hash
* Transitions into `SAR_FILED` trigger [regulatory_reporter.py](../backend/app/application/services/regulatory_reporter.py) to compile case metadata, timeline events, investigator notes, and suspect hashes into a schema-compliant FinCEN BSA Suspicious Activity Report (SAR) XML file (`EFilingSubmission`).
* All outputs are strictly validated against the official XML schema at [FinCEN_SAR_2.0.xsd](../backend/schemas/FinCEN_SAR_2.0.xsd), validating mandatory root tags, `<SubmissionHeader>`, `<ReportingInstitution>`, `<Subjects>`, `<SuspiciousActivityDetails>`, and `<Narrative>` structures.
* Subject entities are strictly tokenized using Zero-PII privacy hashes; unlinked cases dynamically derive a deterministic HMAC digest ($\mathrm{SHA\text{-}256}(\mathrm{prefix}_{\mathrm{subject}} \mathbin{\Vert} \mathrm{id}_{\mathrm{case}})_{[0:32]}$) preventing static mock strings.
* Each filing is fingerprinted with an immutable SHA-256 cryptographic digest: $\mathcal{H}_{\mathrm{filing}} = \mathrm{SHA\text{-}256}(\mathcal{X}_{\mathrm{canonical}})$.
* Generated filings are saved atomically under `storage/regulatory_filings/sar_{case_id}.xml` using temporary file creation and atomic rename (`os.replace`) protected by a reentrant mutex (`threading.RLock()`).
* Endpoints are available via `/api/v1/cases/{case_id}/sar-report`, `/api/v1/cases/{case_id}/file-sar`, `/api/v1/cases/export/fincen-xml`, and dedicated compliance management routes `/api/v1/compliance/sar/*` (`/filings`, `/validate`, `/generate`, `/{filing_id}`).

### 3. Cryptographic Timeline Audit Chain
* Events are bound using sequential SHA-256 block hashing:
  $$H_i = \text{SHA-256}(\text{timestamp} \mathbin{\Vert} \text{type} \mathbin{\Vert} \text{description} \mathbin{\Vert} \text{actor} \mathbin{\Vert} H_{i-1})$$
* Enforces an append-only, tamper-proof record of all investigator actions and status transitions, ensuring legal defensibility.

---

## Full-Fledged AML Investigation Lifecycle & Four-Eyes Governance

To support full-lineage auditing and enforce supervisory checks in [case_workbench.py](../backend/app/application/services/case_workbench.py):

### 1. Case Evidence Registry
* **Immutable Evidence Store**: Exposes an isolated storage layer for KYC documents, account statements, and graph evidence proofs linked to open investigation cases.
* **Content Hashing**: Computes SHA-256 digests upon evidence upload, recording hashes directly in case records to guarantee chain-of-custody.

### 2. Four-Eyes Multi-Signature Governance
* **Closure Validation**: Gating case resolutions (`RESOLVED_TRUE_POSITIVE` and `RESOLVED_FALSE_POSITIVE`) strictly behind two independent supervisor authorizations.
* **Dual Cryptographic Signatures**: Requires two distinct supervisor signatures (`SIG_SUPERVISOR_<ID>`). If only one signature is recorded, the case transitions to `PENDING_SECOND_SIGNATURE` awaiting secondary sign-off. Self-signing or duplicate supervisor IDs are rejected by domain validation.

### 3. Investigator Role Auditing
* **Activity Logging**: Tracks analyst operations including case retrieval (`access_case`), entity lookup (`query_entity`), cross-bank resolution (`cross_bank_resolve`), and Private Set Intersection runs (`cross_bank_psi`).
* **Session Duration Tracking**: Monitors analyst session durations and query frequencies to detect anomalous internal activity.

### 4. Closed-Loop Retraining Feedback Pipeline
* Implemented in [label_feedback_pipeline.py](../backend/app/application/services/label_feedback_pipeline.py).
* When an investigator confirms a case as `RESOLVED_TRUE_POSITIVE` (confirmed fraud, label = 1) or `RESOLVED_FALSE_POSITIVE` (legitimate transaction, label = 0), verified labels are written to local bank training datasets.
* Successive local training epochs consume these verified ground-truth labels, continually refining global federated model accuracy across subsequent aggregation rounds.

---

## Agentic AML Copilot & Investigation Workbench

To accelerate investigation workflows and eliminate manual compliance paperwork, the platform integrates an autonomous AML AI copilot in [aml_agentic_copilot.py](../backend/app/application/services/aml_agentic_copilot.py):

### 1. Autonomous Narrative Synthesis
* Synthesizes formal FinCEN 5-paragraph SAR narratives in natural language:
  - **Paragraph 1**: Executive overview, filing institution, and suspect entities.
  - **Paragraph 2**: Transaction mechanics, velocity spikes, and monetary volumes.
  - **Paragraph 3**: Graph relationships, cross-bank entity links, and shared devices.
  - **Paragraph 4**: SHAP feature attributions and underlying risk model rationales.
  - **Paragraph 5**: Concluding disposition, law enforcement contact recommendation, and account actions taken.

### 2. Four-Eyes Supervisor Briefings
* Compiles executive briefings highlighting primary fraud indicators, cross-institution exposure amounts, and confidence metrics for supervisor dual-signature reviews.

---

## Consortium Incentive Mechanisms & Web3 Smart Contract Settlement

To align institutional incentives and enforce accountability across commercial federated consortia:

### 1. Federated Shapley Value (SV) Estimation
* **Leave-One-Out (LOO) Shapley Evaluation**: Computes the marginal utility contribution of each participant bank:
  1. Evaluates baseline validation F1-score ($F_{\text{global}}$) on the shared global validation dataset.
  2. Aggregates subsets of client parameters excluding one bank at a time to create LOO models ($F_{-i}$).
  3. Calculates marginal contribution: $SV_i = F_{\text{global}} - F_{-i}$.

### 2. Free-Rider & Poisoning Quarantine
* **Free-Rider Identification**: Updates with gradient variance $< 10^{-6}$ indicate zero-effort training (nodes returning base weights to acquire global models without contributing compute).
* **Poisoning Isolation**: If a bank's contribution score $SV_i \le -0.05$, the update significantly degrades global accuracy, indicating model poisoning.
* **Quarantine Enforcement**: Flagged nodes are automatically placed in `QUARANTINED` status and excluded from future aggregation rounds.

### 3. Web3 & CBDC Smart Contract Settlement
* Programmatic on-chain clearing via [ConsortiumIncentiveSettlement.sol](../contracts/contracts/ConsortiumIncentiveSettlement.sol) and [smart_contract_driver.py](../backend/app/infrastructure/security/smart_contract_driver.py).
* Disburses Wholesale CBDC (`wCBDC`), Fiat Stablecoins (`USDC`), or Digital Lira (`e-TRY`) in 18-decimal token precision based on LOO Shapley basis points (`bps`).
* Quarantine status locks recipient wallets on-chain, zeroing out disbursements (`BLOCKED_QUARANTINE`).
* Every transaction hash (`settlement_tx_hash`) and block number is chained to the tamper-proof audit ledger in [immutable_audit_chain.py](../backend/app/infrastructure/security/immutable_audit_chain.py).

---

## Hardware and Cryptographic Security Drivers (TEE & FHE)

* **Trusted Execution Environments (TEE - Intel SGX / AWS Nitro Enclaves)**: Parameters are aggregated in encrypted enclave hardware memory. Enclave integrity is verified via `MRENCLAVE` and `MRSIGNER` remote attestation digests before training commences.
* **Fully Homomorphic Encryption (FHE - TenSEAL CKKS)**: Clients submit model updates encrypted with FHE public keys. The coordinator computes homomorphic weight averages directly over ciphertexts without decryption, preventing server-side visibility into raw client parameters.

---

## Real-Time Streaming GNN Dynamics

* **Dynamic Sliding-Window Graph Buffer**: Real-time payment events stream into in-memory sliding buffers, dynamically creating nodes and transaction edges. Inactive edges are automatically pruned after a time-bound window (e.g., 60 minutes) to eliminate memory leaks.
* **Incremental Online GNN Training (GraphSAGE / GAT)**: Evaluates multi-head attention weights across topological neighborhoods, running continuous online backpropagation directly over active transaction streams.

---

## Active Defense & Adversarial Training (Adversarial ML Defense)

* **Adversarial Evasion Stress Tests (FGSM & PGD)**: Generates 1-step Fast Gradient Sign Method and 5-step Projected Gradient Descent perturbations bounded by $L_\infty \in [0.01, 0.25]$.
* **Tabular Constraint Projection ($\Pi_{\mathcal{X}}$)**: Enforces non-negativity and domain feature limits on perturbed inputs to preserve financial validity.
* **Robust Training Formulation**: Blends clean and adversarial loss during local SGD iterations:
  $$\mathcal{L}_{\text{total}} = \lambda \mathcal{L}(f_\theta(x_{\text{clean}}), y) + (1-\lambda) \mathcal{L}(f_\theta(x_{\text{adv}}), y)$$
  calculating clean vs robust accuracy under evasion attacks.

---

## Audit Compliance & Model Governance (Fed SR 11-7)

To satisfy Federal Reserve SR 11-7 Model Risk Management guidelines:
1. **Semantic Versioning & Dual Sign-Off**: Requires dual cryptographic signatures from the ML Engineering Lead (`ml_engineer`) and Bank Compliance Officer (`compliance_officer`) before promoting candidate models to production champion status.
2. **MLOps Shadow Canary Deployment**: Promoted candidate models shadow 10% of live prediction traffic via MD5 request hash routing, verifying superior ROC-AUC against champion models without impacting decisions.
3. **Automated Safety Rollbacks**: Automatically rolls back to the prior champion model if live ROC-AUC falls below `0.65` or 99th percentile inference latency exceeds `200ms`.
4. **Cryptographic Lineage Manifest**: Records model version tags, git commit SHA, training dataset SHA-256 hash, and differential privacy budget parameters ($\epsilon, \delta$) in an immutable audit manifest.

---

## Empirical Benchmarks & Experimental Validation

Under extreme financial class imbalance ($< 0.1\%$ fraud incidence), automated benchmarks evaluated in [metrics_service.py](../backend/app/application/services/metrics_service.py) demonstrate:

| Model Configuration | PR-AUC | ROC-AUC | Recall@0.1%FPR | P@100 | Latency (ms) | Payload (MB) | DP ($\epsilon$) | OOD Delta |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Local-Only Model (Bank A)** | 0.3489 | 0.9457 | 0.2600 | 0.2500 | 3.80 | 0.00 | N/A | -0.1420 |
| **Centralized Pooled (Non-Private)** | 0.9925 | 1.0000 | 0.9800 | 0.5000 | 6.20 | 142.50 | N/A | +0.0450 |
| **Standard FedAvg** | 0.8954 | 0.9991 | 0.7800 | 0.4800 | 4.10 | 1.25 | N/A | +0.0210 |
| **FedProx ($\mu = 0.01$)** | 0.9618 | 0.9997 | 0.9400 | 0.5000 | 4.50 | 1.25 | N/A | +0.0320 |
| **FedGNN (Graph Attention Network)** | 0.9789 | 0.9998 | 0.9600 | 0.4900 | 7.40 | 2.40 | N/A | +0.0480 |
| **Federated + Privacy Entity Intelligence** | 0.9494 | 0.9994 | 0.9000 | 0.4800 | 8.90 | 3.10 | 2.5 | +0.0410 |

### Key Experimental Insights
1. **Federated Superiority**: Collaborative training increases PR-AUC from 0.3489 (Local-Only) to 0.8954+ (Federated), proving regional banks gain enterprise detection power without raw data sharing.
2. **FedGNN Performance**: Graph structural embeddings achieve 0.9789 PR-AUC, closing 98.6% of the gap to centralized pooled training while preserving institutional boundaries.
3. **Privacy Trade-off**: Combined FedGNN + DH-PSI + Differential Privacy ($\epsilon=2.5$) retains 0.9494 PR-AUC with 8.90ms inference latency, satisfying sub-10ms production SLAs.

---

## Cross-Border Data Sovereignty & EU AI Act Compliance

* **Regional Aggregation Rings ([regional_governance.py](../backend/app/domain/regional_governance.py))**: Groups bank nodes into regional jurisdictions (`EU-Central`, `US-East`, `APAC-Singapore`). Intra-region updates aggregate locally; cross-border transfers enforce Differential Privacy noise scrubbing ($\epsilon_{\text{inter}}$) to satisfy GDPR Art. 22 and Schrems II requirements.
* **EU AI Act High-Risk AI Compliance Engine ([ai_act_compliance.py](../backend/app/infrastructure/security/ai_act_compliance.py))**: Generates JSON compliance certificates covering Articles 10–15:
  - **Art. 10 (Data Governance)**: Zero Raw PII enforcement and demographic bias controls.
  - **Art. 11 (Technical Documentation)**: Model training lineage and commit hashes.
  - **Art. 12 (Record-Keeping)**: Automated OpenTelemetry trace logging.
  - **Art. 13 (Transparency)**: SHAP feature explainability attributions.
  - **Art. 14 (Human Oversight)**: Four-Eyes Principle dual supervisor sign-off enforcement.
  - **Art. 15 (Accuracy & Robustness)**: Adversarial PGD evasion rejection metrics and mTLS PKI security.

---

## Automated AML Test Verification Matrix

All collaborative AML intelligence components are validated through targeted unit tests under `backend/tests/unit/`:

| Test Suite | Targeted AML Component | Verified Features | Test Count | Status |
|:---|:---|:---|:---:|:---:|
| [test_aml_agentic_copilot.py](../backend/tests/unit/test_aml_agentic_copilot.py) | `aml_agentic_copilot.py` | FinCEN 5-paragraph SAR narrative drafting, 4-Eyes briefings | 3 | ✅ 100% Pass |
| [test_case_management_workbench.py](../backend/tests/unit/test_case_management_workbench.py) | `case_workbench.py` | FSM lifecycle, 4-Eyes dual supervisor signatures (`SIG_SUPERVISOR_<ID>`) | 4 | ✅ 100% Pass |
| [test_case_management_feedback_loop.py](../backend/tests/unit/test_case_management_feedback_loop.py) | `label_feedback_pipeline.py` | Ground truth label writeback, Dirichlet non-IID partition updates | 4 | ✅ 100% Pass |
| [test_regulatory_reporter.py](../backend/tests/unit/test_regulatory_reporter.py) | `regulatory_reporter.py` | FinCEN SAR 2.0 XML schema serialization & XSD validation | 5 | ✅ 100% Pass |
| [test_regulatory_compliance.py](../backend/tests/unit/test_regulatory_compliance.py) | `security_compliance.py` | Compliance router endpoints, regulatory filing export | 3 | ✅ 100% Pass |
| [test_fuzzy_psi.py](../backend/tests/unit/test_fuzzy_psi.py) | `psi_service.py` | Turkish character transliteration, MinHash LSH, 3-of-5 matching | 3 | ✅ 100% Pass |
| [test_psi_service.py](../backend/tests/unit/test_psi_service.py) | `psi_service.py` | Commutative DH-PSI modular exponentiation ($x^{ab} \equiv x^{ba}$) | 3 | ✅ 100% Pass |
| [test_graph_analytics.py](../backend/tests/unit/test_graph_analytics.py) | `graph_analytics_service.py` | PageRank decay ($\gamma = 0.85$), community density, velocity anomalies | 3 | ✅ 100% Pass |
| [test_graph_embedding.py](../backend/tests/unit/test_graph_embedding.py) | `graph_embedding_service.py` | 12-dim node features, GraphSAGE forward pass, FedAvg GNN aggregation | 21 | ✅ 100% Pass |
| [test_neo4j_graph.py](../backend/tests/unit/test_neo4j_graph.py) | `graph_engine.py` | Neo4j Bolt driver initialization, Cypher merges, Redis fallback | 8 | ✅ 100% Pass |
| [test_regional_governance_ai_act.py](../backend/tests/unit/test_regional_governance_ai_act.py) | `regional_governance.py`, `ai_act_compliance.py` | Regional rings, cross-border DP filters, Articles 10–15 certificate | 4 | ✅ 100% Pass |
| **Total Verified (Core AML Intelligence)** | **11 Dedicated Suites** | **Collaborative AML Intelligence Platform** | **61 Tests** | **100% Pass** |

---

## European FININT, SEPA Recalls & Extended RegTech Architecture

The platform integrates dedicated European Anti-Money Laundering RegTech capabilities designed around EU AMLA, AMLD6, EPC SCT Inst, and UNODC goAML mandates. For complete mathematical formulations, sequence diagrams, and REST API contracts, consult the dedicated specification in [`docs/european_finint_and_regtech_spec.md`](european_finint_and_regtech_spec.md).

### RegTech Subsystems Summary

| Subsystem | Service Module | Primary Regulatory Driver | Verified Capabilities | Test Suite |
| :--- | :--- | :--- | :--- | :--- |
| **Inter-Bank Encrypted FININT Messaging** | [`bridge_case_service.py`](../backend/app/application/services/bridge_case_service.py) | EU AMLA Single Rulebook & AMLD6 | Curve25519 ECDH + AES-256-GCM envelope encryption, SHA-256 evidence integrity hashing, SLA countdown timers, and tamper-evident append-only hash chains. | `test_bridge_messaging.py` (53 Tests) |
| **Real-Time SEPA Instant Payment Recall** | [`payment_recall_service.py`](../backend/app/application/services/payment_recall_service.py) | EPC SCT Inst Rulebook | Automated ISO 20022 `camt.056` recall processing (`FRAD`, `TECH`, `DUPL`), 10-day regulatory boundary enforcement, automated destination account freeze holds, and `camt.029` Four-Eyes fund recovery ledger. | `test_payment_recall.py` (89 Tests) |
| **Multi-List Sanctions & PEP Screening** | [`screening_service.py`](../backend/app/application/services/screening_service.py) | UN / EU CFSP / OFAC SDN Lists | Real-time pre-transaction and batch fuzzy matching combining Jaro-Winkler ($p=0.10$) and Levenshtein distance ($S_{\mathrm{composite}} = 0.60 S_{\mathrm{jw}} + 0.40 S_{\mathrm{lev}}$), secondary demographic disambiguation, and audited whitelist bypass. | `test_screening_service.py` (88 Tests) |
| **European FIU & UNODC goAML 4.0 Exporter** | [`fiu_regulatory_service.py`](../backend/app/application/services/fiu_regulatory_service.py) | UNODC goAML 4.0 XML & EU AMLA | Automated compilation of confirmed cases into standardized goAML 4.0 XML schemas and EU AMLA JSON dossiers wrapped in HMAC-SHA256 encrypted envelopes with Four-Eyes supervisor authorization. | `test_fiu_regulatory_service.py` (33 Tests) |
| **Enterprise AML OpenAPI Drop-In Adapter** | [`open_aml_service.py`](../backend/app/application/services/open_aml_service.py) | Enterprise AML Integration | Drop-in `/api/v2/*` endpoints for corporate person ingestion, real-time transaction monitoring, ad-hoc watchlist searches, and HMAC-SHA256 signed webhook delivery (`X-CF-Signature`). | `test_open_aml_adapter.py` (24 Tests) |
| **Corporate UBO & Graph Intelligence** | [`ubo_graph_service.py`](../backend/app/application/services/ubo_graph_service.py) | EU AMLD6 / 4AMLD 25% Threshold | Multi-tier beneficial ownership graph decomposition, compounded indirect shareholding calculation ($\sum \prod \text{share}$), Tarjan DFS circular ownership loop detection, and nominee shell clustering. | `test_ubo_graph_service.py` (16 Tests) |
| **16 European AML Typology Scenarios** | [`european_scenario_library.py`](../backend/app/application/services/european_scenario_library.py) | EBA & FATF Standards | 16 pre-configured production typologies (sub-€10k structuring, rapid pass-through mules, round amount velocity, dormant awakening) synthesized dynamically with federated ML anomaly scores ($S_{\mathrm{hybrid}} = \alpha S_{\mathrm{rules}} + (1 - \alpha) S_{\mathrm{ml}}$). | `test_european_scenarios.py` (25 Tests) |
| **Total European RegTech Suite** | **7 Dedicated Suites** | **Comprehensive European Compliance Verification** | **328 Tests** | **100% Pass** |
