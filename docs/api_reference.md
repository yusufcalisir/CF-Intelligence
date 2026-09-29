# 📖 REST & WebSocket API Reference Specification

Comprehensive endpoint blueprints, request/response JSON schemas, authentication protocols, and streaming telemetry interfaces for the Collaborative Financial Crime Intelligence Platform (**CF-Intelligence**).

---

## 📌 Architecture & Protocol Conventions

All CF-Intelligence API endpoints follow strict Clean Architecture, OpenAPI 3.1.0, and Zero-Trust principles:
- **Base URL (Local Gateway):** http://localhost:8000 (or http://localhost via Nginx Reverse Proxy)
- **Base URL (Interactive UI):** http://localhost:3000 (Vite SPA)
- **Interactive Documentation:** 
  - Swagger UI: http://localhost:8000/docs
  - ReDoc Portal: http://localhost:8000/redoc
  - Scalar Interactive API Reference: http://localhost:8000/developer
- **Authentication & Tenant Isolation:** 
  - Bearer JWT tokens (Authorization: Bearer <token>) validated via RFC 7519 standard.
  - Multi-tenant cryptographic isolation via mandatory X-Tenant-ID header.
- **Distributed Tracing & Context Propagation:**
  - W3C Trace Context headers (	raceparent, 	racestate) propagated through all API gateways and asynchronous Celery/Kafka tasks.
- **Standardized Error Responses:**
  - RFC 7807 Problem Details (pplication/problem+json) with deterministic error codes, validation error vectors, and timestamped audit tracking.
- **Rate Limiting & Abuse Defense:**
  - Sliding-window rate limiting enforced by Redis Sentinel and slowapi (returns HTTP 429 Too Many Requests with Retry-After).

---

## 📑 API Endpoint Directory (20 Endpoints)

| # | Endpoint | Method | Category | Description |
| :--- | :--- | :---: | :--- | :--- |
| **18.1** | /api/v1/score-transaction | POST | Real-Time Scoring | Normalized transaction fraud risk scoring & feature attributions |
| **18.2** | /api/v1/auth/login | POST | Authentication | Enterprise JWT session issuance & multi-factor verification |
| **18.3** | /health, /health/ready | GET | System Probes | Deep database, cache, and inference engine readiness probes |
| **18.4** | /ws/telemetry | WS | Telemetry | Real-time multi-bank consortium WebSocket metrics broadcast |
| **18.5** | /developer | GET | Developer Portal | Interactive Scalar API Gateway documentation |
| **18.6** | /api/v1/scenarios/inject-attack | POST | Adversarial Simulation | Chaos & poisoning attack injection (label flipping, sign inversion) |
| **18.7** | /api/v1/data/ingest-dataset | POST | Data Ingestion | Multi-dataset ingestion (PaySim, IEEE-CIS, Elliptic) & contract checks |
| **18.8** | /api/v1/banks/scoring-volume | GET | Consortium Analytics | 24-hour historical consortium transaction scoring volume |
| **18.9** | /api/v1/fl/events | GET | FL Engine | Federated training round convergence & Server-Sent Events stream |
| **18.10** | /api/v1/regulatory/export-sar | POST | Regulatory & Audit | Automated FinCEN SAR XML generation & cryptographic key rotation |
| **18.11** | /api/v1/cases/{id}/label-feedback | POST | Human-in-the-Loop | Analyst verdict feedback loop & continuous retraining store |
| **18.12** | /api/v1/bridge/message | POST | FININT Bridge | Inter-bank end-to-end encrypted FININT messaging protocol |
| **18.13** | /api/v1/recalls/initiate | POST | SEPA Instant | Real-time SEPA Instant Payment Recall (camt.056) automation |
| **18.14** | /api/v1/screening/screen | POST | Sanctions / PEP | Real-time fuzzy MinHash LSH screening against OFAC & EU watchlists |
| **18.15** | /api/v1/regulatory/export-amla | POST | EU AMLA & goAML | European FIU & UNODC goAML 4.0 XML regulatory reporting |
| **18.16** | /api/v2/*, /api/v1/* | POST | Drop-in Adapter | Enterprise AML drop-in OpenAPI adapter & webhook gateway |
| **18.17** | /api/v1/ubo/analyze-graph | POST | Graph Intelligence | Cross-border corporate UBO & heterogeneous GraphSAGE analysis |
| **18.18** | /api/v1/scenarios/european-aml/evaluate | POST | European AML Rules | Hybrid deterministic FATF rule evaluation & scenario library |
| **18.19** | /api/v1/operations/asset-recovery/hold | POST | Asset Recovery | Collaborative FININT operational hub & multi-bank asset freeze |
| **18.20** | /api/v1/coordinator/* | GET/POST | FL Coordinator | Bank node registration, telemetry, and hyperparameter negotiation |

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
> **Real SHAP Attribution Computation:** The feature contribution values in the example above illustrate the response schema contract. At serving time, explanations are computed dynamically by `ExplainabilityService.compute_shap_values()` ([README Section 9.2](../README.md#92-model-explainability--counterfactual-search-explainability_servicepy--risk_enginepy)) using real `shap.KernelExplainer` against the PyTorch serving neural network (`FraudDetectionModel`), guaranteeing the mathematical Shapley local accuracy property ($\sum \phi_i + \text{base value} = f(\mathbf{x})$) within floating point tolerance.

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

> **Regulatory Simulation Notice:**  
> Generates schema-validated FinCEN BSA XML Schema 2.0 and UNODC goAML 4.0 electronic filing dossier prototypes for internal case investigation and audit readiness. The platform does **not** transmit live filings to statutory FinCEN BSA E-Filing or European FIU production portals (which require federal banking charter accreditations and dedicated government VPN leased lines).

**1. Case SAR FinCEN XML Export (`POST /api/v1/cases/export/fincen-xml`):**
Compiles confirmed fraud cases into schema-compliant FinCEN BSA XML Schema 2.0 electronic dossier prototypes:

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
  "xml": "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<EFilingSubmission ...>\n  <ReportingInstitution>Bank Alpha (Synthetic Retail Node)</ReportingInstitution>\n  <SuspiciousActivityInformation>Cross-Bank Velocity Surge</SuspiciousActivityInformation>\n</EFilingSubmission>",
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

### 18.20 Federated Learning Coordinator & Bank Node Telemetry API (`/api/v1/coordinator/*`)

Provides real-time bank edge-node registration, institutional hardware capability telemetry (PyTorch 2.4.0, CUDA/CPU, VRAM/RAM), asynchronous staleness aggregation, and dynamic hyperparameter negotiation across consortium institutions:

> **Consortium Simulation Testbed Notice:**  
> All banking institutions, node identifiers, and telemetry payloads represented in this platform (`Bank Alpha`, `Bank Beta`, `Bank Gamma`, `Meridian National`, `Nexus Digital`) are **purely synthetic simulation testbed entities**. CF-Intelligence does not connect to live commercial banking networks, core banking ledgers, SWIFT messaging infrastructure, or real financial institutions.

**1. Query Registered Consortium Clients & Telemetry (`GET /api/v1/coordinator/clients`):**
*Response (HTTP 200 OK):*
```json
[
  {
    "bank_id": "bank_alpha",
    "bank_name": "Bank Alpha (Synthetic Retail Node)",
    "pytorch_version": "2.4.0+cu124",
    "python_version": "3.12.3",
    "hardware_type": "cuda",
    "ram_gb": 128.0,
    "device_count": 4,
    "status": "ONLINE",
    "last_heartbeat": 1758921600.0,
    "registered_at": 1758921500.0
  },
  {
    "bank_id": "bank_beta",
    "bank_name": "Bank Beta (Synthetic Commercial Node)",
    "pytorch_version": "2.4.0+cu124",
    "python_version": "3.12.3",
    "hardware_type": "cuda",
    "ram_gb": 64.0,
    "device_count": 2,
    "status": "ONLINE",
    "last_heartbeat": 1758921600.0,
    "registered_at": 1758921500.0
  }
]
```

**2. Bank Node Handshake & Capability Exchange (`POST /api/v1/coordinator/handshake`):**
```json
{
  "bank_id": "bank_gamma",
  "bank_name": "Bank Gamma (Synthetic Regional Node)",
  "pytorch_version": "2.4.0+cu121",
  "python_version": "3.12.2",
  "hardware_type": "cuda",
  "ram_gb": 64.0,
  "device_count": 2
}
```

*Response (HTTP 200 OK):*
```json
{
  "registered": true,
  "bank_id": "bank_gamma",
  "status": "COMPATIBLE",
  "registered_at": 1758921600.0,
  "message": "Handshake successful with PyTorch 2.4.0+cu121."
}
```

**3. Dynamic Hardware-Aware Hyperparameter Negotiation (`POST /api/v1/coordinator/negotiate`):**
```json
{
  "bank_id": "bank_alpha",
  "base_batch_size": 64,
  "base_epochs": 5
}
```

*Response (HTTP 200 OK):*
```json
{
  "bank_id": "bank_alpha",
  "batch_size": 64,
  "local_epochs": 5,
  "use_cuda": true,
  "gradient_accumulation_steps": 1,
  "status": "COMPATIBLE"
}
```

---

