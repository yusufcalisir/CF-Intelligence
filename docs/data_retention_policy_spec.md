# 🗑️ Enterprise Data Retention & GDPR Article 17 Erasure Specification

The Automated Retention & Erasure Policy Engine ([`AutomatedRetentionEngine`](../backend/app/application/services/retention_engine.py)) enforces Time-To-Live (TTL) data purging and fulfills European GDPR Article 17 Right-to-be-Forgotten erasure requests with cryptographic zeroization, physical database table deletion, and tamper-proof SHA-256 audit trails.

> [!NOTE]
> For platform-wide data classification, multi-tenant isolation schemas, and encryption controls, refer to [`docs/security_controls_matrix.md`](security_controls_matrix.md), [`docs/production_infrastructure.md`](production_infrastructure.md), and [`docs/threat_model.md`](threat_model.md).

---

## 📌 1. Architectural Retention & Erasure Pipeline

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        DATA RETENTION & GDPR ERASURE LIFECYCLE                         │
│                                                                                        │
│   [ Continuous Ingestion & Scoring Telemetry ]                                         │
│                       │                                                                │
│                       ├───► TRANSACTION_LOGS        (90 Days)  ──► Cryptographic Zero  │
│                       ├───► INFERENCE_AUDITS        (180 Days) ──► Anonymization       │
│                       ├───► GRAPH_EDGES             (30 Days)  ──► Hard SQL Delete     │
│                       └───► EXPLAINABILITY_REPORTS  (60 Days)  ──► Cryptographic Zero  │
│                                                                                        │
│   [ Scheduled Maintenance CronJob / Event Trigger ]                                    │
│                       │ (POST /v1/cron/cleanup-sessions)                               │
│                       ▼                                                                │
│   [ purge_expired_records(tenant_id, db) ] ──► Execute SQL DELETE on Expired Rows      │
│                       │                                                                │
│                       ▼                                                                │
│   [ Generate Immutable ErasureAuditRecord ] ──► Compute SHA-256 Digest                 │
│                                                                                        │
│   ──────────────────────────────────────────────────────────────────────────────────   │
│   [ GDPR Article 17 Right-to-be-Forgotten Request ]                                    │
│                       │                                                                │
│                       ▼ execute_gdpr_right_to_be_forgotten(tenant_id, entity_id_hash)  │
│   ┌─────────────────────────────────────────────────────────────────────────────────┐  │
│   │ 1. Hard-delete EntityModel rows matching privacy_id / HMAC (bank_id isolated)   │  │
│   │ 2. Hard-delete RelationshipModel edges (source_entity_id / target_entity_id)    │  │
│   │ 3. Hard-delete AlertModel records referencing transaction_id / entity           │  │
│   │ 4. Append signed ErasureAuditRecord to immutable compliance ledger              │  │
│   └─────────────────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 📑 2. Data Retention Categories & Default Schedules

The engine categorizes consortium data under the [`DataCategory`](../backend/app/domain/retention_policy.py) domain enumeration:

| Data Category | Default TTL | Erasure Method | Target Database Model & Field | Description |
| :--- | :---: | :---: | :--- | :--- |
| **`TRANSACTION_LOGS`** | **90 Days** | `CRYPTOGRAPHIC_ZEROIZATION` | `AlertModel` (`created_at < cutoff`) | Ingestion telemetry, gateway headers, and raw request payloads. |
| **`INFERENCE_AUDITS`** | **180 Days** | `ANONYMIZATION` | `AlertModel` (`created_at < cutoff`) | Real-time fraud scoring decisions, risk tiers, and model predictions. |
| **`GRAPH_EDGES`** | **30 Days** | `HARD_DELETE` | `RelationshipModel` (`created_at < cutoff`) | Dynamic graph links, transaction flows, and entity associations. |
| **`EXPLAINABILITY_REPORTS`** | **60 Days** | `CRYPTOGRAPHIC_ZEROIZATION` | `SharedIntelligenceModel` (`created_at < cutoff`) | SHAP feature attributions, typologies, and indicator context. |
| **`CUSTOMER_ENTITIES`** | **365 Days** | `CRYPTOGRAPHIC_ZEROIZATION` | `EntityModel` (`bank_id == tenant_id`) | PII privacy identifiers, KYC profile attributes, and customer entity rows. |


---

## ⚖️ 3. GDPR Article 17 Right-to-be-Forgotten Protocol

When an individual or consortium institution requests permanent erasure under GDPR Article 17:

### 3.1 HMAC-SHA256 Identifier Lookup
- The request specifies the one-way HMAC-SHA256 entity hash (`entity_id_hash`).
- In accordance with the platform's Zero Raw PII invariant, the engine operates exclusively on salted cryptographic hashes and never handles plaintext PANs, names, or national identity identifiers.

### 3.2 Physical Database Deletion Queries
When supplied with a live database session (`db_session`), the engine executes parameterized SQL `DELETE` queries:

```sql
-- 1. Purge Entity Rows (Tenant Isolated)
DELETE FROM entities 
WHERE bank_id = :tenant_id 
  AND (privacy_id = :entity_id_hash OR id = :entity_id_hash);

-- 2. Purge Graph Edges
DELETE FROM relationships 
WHERE source_entity_id = :entity_id_hash 
   OR target_entity_id = :entity_id_hash;

-- 3. Purge Alert Records (Tenant Isolated)
DELETE FROM alerts 
WHERE bank_id = :tenant_id 
  AND (transaction_id = :entity_id_hash OR id = :entity_id_hash);
```

### 3.3 Cryptographic Audit Trail (`ErasureAuditRecord`)
Every purge or RTBF execution produces a signed `ErasureAuditRecord`:
- `erasure_id`: Unique identifier (e.g. `erase_gdpr_1a2b3c4d` or `erase_ttl_9e8f7a6b`).
- `tenant_id`: Isolated bank institution ID (e.g. `bank_alpha`).
- `category`: [`DataCategory`](../backend/app/domain/retention_policy.py) enum value.
- `records_erased_count`: Actual count of physically purged SQL rows (`rowcount`).
- `erasure_hash`: SHA-256 cryptographic digest computed as $\operatorname{SHA-256}(\mathrm{erasure}_{\mathrm{id}} \mathbin{\Vert} \mathrm{tenant}_{\mathrm{id}} \mathbin{\Vert} \mathrm{category} \mathbin{\Vert} \mathrm{timestamp})$.
- `timestamp`: UTC timestamp of the completed operation.

Audit trails are queryable per tenant via:
```python
audit_trail = engine.get_erasure_audit_trail(tenant_id="bank_alpha")
```

### 3.4 Statutory Scope Limitations & Mandatory Exemptions
- **Bank Secrecy Act (BSA) & FinCEN Mandates**: Suspicious Activity Reports (SARs) compiled under 31 CFR § 1020.320 and supervisor Four-Eyes audit records are legally required to be maintained for 5 years. Pursuant to GDPR Article 17(3)(b) (*compliance with a legal obligation*), filed SAR cases are exempt from right-to-be-forgotten deletion.
- **Model Parameters**: Aggregated neural network weights do not contain raw training instances and are governed by Differential Privacy ($\epsilon = 3.0, \delta = 10^{-5}$) rather than SQL purging.

---

## 🛠️ 4. Programmatic Implementation

### 4.1 Configuring Retention Policies & Executing Erasure
```python
from app.application.services.retention_engine import AutomatedRetentionEngine
from app.domain.retention_policy import DataCategory, ErasureMethod

# Initialize retention engine (with optional SQLAlchemy session)
engine = AutomatedRetentionEngine(db_session=session)

# 1. Configure custom tenant retention policy
policy = engine.configure_tenant_policy(
    tenant_id="bank_alpha",
    category=DataCategory.TRANSACTION_LOGS,
    ttl_days=90,
    erasure_method=ErasureMethod.CRYPTOGRAPHIC_ZEROIZATION,
)

# 2. Execute automated TTL purge across expired tenant records
purged_records = engine.purge_expired_records(tenant_id="bank_alpha", db=session)
for record in purged_records:
    print(f"Purged {record.records_erased_count} records for {record.category.value} (Hash: {record.erasure_hash[:16]}...)")

# 3. Execute GDPR Article 17 Right-to-be-Forgotten
erasure = engine.execute_gdpr_right_to_be_forgotten(
    tenant_id="bank_gamma",
    entity_id_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    db=session,
)
assert erasure.records_erased_count > 0
assert len(erasure.erasure_hash) == 64
```

### 4.2 Maintenance Cron Integration
Automated TTL purging runs as part of the daily maintenance schedule in [`backend/app/presentation/routers/maintenance_cron.py`](../backend/app/presentation/routers/maintenance_cron.py):
```bash
curl -X POST http://localhost:8000/v1/cron/cleanup-sessions \
  -H "X-Cron-Secret: $CRON_SECRET"
```

### 4.3 REST API Endpoints & Compliance Gateway
Enterprise retention and GDPR erasure controls are exposed under `/api/v1/compliance` and `/v1/compliance`:

| Method | Endpoint Path | Description |
|:---|:---|:---|
| `GET` | `/api/v1/compliance/retention/policies?tenant_id=bank_alpha` | List configured TTL policies for a tenant. |
| `POST` | `/api/v1/compliance/retention/policies` | Configure or update per-tenant TTL retention policy. |
| `POST` | `/api/v1/compliance/retention/purge` | Trigger automated TTL scan and expired record purging. |
| `POST` | `/api/v1/compliance/gdpr/erasure` | Execute GDPR Art. 17 Right-to-be-Forgotten cryptographic erasure. |
| `GET` | `/api/v1/compliance/retention/audit-trail?tenant_id=bank_alpha` | Retrieve chronological immutable erasure audit ledger. |
| `GET` | `/api/v1/compliance/retention/audit-trail/verify?tenant_id=bank_alpha` | Cryptographically verify SHA-256 hash chain integrity of erasure records. |

---

## 🧪 5. Automated Unit Test Suite Parity

The retention and erasure policy engine is verified across **15 automated unit, hardening, and database integration tests**:

```bash
python -m pytest backend/tests/unit/test_retention_erasure_engine.py backend/tests/unit/test_retention_erasure_hardening.py -v
# 15 passed in 11.91s (100% Pass)
```

| Test Function | Suite | Verification Scope |
|:---|:---:|:---|
| `test_tenant_retention_policy_configuration` | Engine | Validates per-tenant TTL configuration, enum bounds, and positive integer validation. |
| `test_automated_ttl_purging_execution` | Engine | Tests multi-category TTL scanning, expired record detection, and SHA-256 digest creation. |
| `test_gdpr_article_17_right_to_be_forgotten_erasure` | Engine | Verifies Right-to-be-Forgotten execution, audit ledger entry, and tenant isolation. |
| `test_database_real_retention_purging_and_gdpr_zeroization` | Engine | Verifies genuine SQLite/PostgreSQL physical deletion of expired `AlertModel` and `EntityModel` rows. |
| `test_database_real_retention_purging_graph_edges_and_shared_intelligence` | Engine | Confirms real SQL deletion of expired `RelationshipModel` edges and `SharedIntelligenceModel` entries. |
| `test_retention_policy_validation_positive_ttl` | Hardening | Enforces positive TTL schedule validation (`gt=0`) across all categories. |
| `test_multi_tenant_policy_isolation` | Hardening | Verifies strict tenant policy isolation across distinct bank institutions. |
| `test_standalone_zero_fake_deletion_when_entity_absent` | Hardening | Asserts zero mock deletion count when entity is absent (zero-mock invariant). |
| `test_in_memory_explicit_entity_registration_and_erasure` | Hardening | Verifies in-memory entity registration, accurate deletion counting, and table tracking. |
| `test_cryptographic_hash_chain_linking` | Hardening | Validates sequential SHA-256 hash chaining (`prev_erasure_hash`) across multiple erasures. |
| `test_cryptographic_tamper_detection_broken_chain` | Hardening | Verifies that tampering with an audit record breaks cryptographic chain integrity. |
| `test_persistent_on_disk_jsonl_ledger_recovery` | Hardening | Verifies that erasure records are persisted to JSONL and recovered across engine reload. |
| `test_immutable_audit_chain_cross_system_event_recording` | Hardening | Verifies that GDPR erasures and TTL purges append events to `ImmutableAuditChain`. |
| `test_database_real_cascade_deletion_with_privacy_id_and_relationships` | Hardening | Verifies physical SQL multi-table cascade deletion resolving `privacy_id` to entity PKs. |
| `test_compliance_api_retention_and_gdpr_endpoints` | Hardening | Verifies REST API endpoints (`/retention/policies`, `/retention/purge`, `/gdpr/erasure`, `/retention/audit-trail/verify`). |

