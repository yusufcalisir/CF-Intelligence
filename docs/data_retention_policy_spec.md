# 🗑️ Enterprise Data Retention & GDPR Article 17 Erasure Specification

The Automated Retention & Erasure Policy Engine (`AutomatedRetentionEngine`) enforces Time-To-Live (TTL) data purging and fulfills European GDPR Article 17 Right-to-be-Forgotten erasure requests with cryptographic zeroization and tamper-proof audit trails.

---

## 📌 Architectural Retention Pipeline

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        DATA RETENTION & GDPR ERASURE LIFECYCLE                         │
│                                                                                        │
│   [ Continuous Ingestion & Scoring Telemetry ]                                         │
│                       │                                                                │
│                       ├───► TRANSACTION_LOGS (Default: 90 Days) ──► Cryptographic Zero │
│                       ├───► INFERENCE_AUDITS (Default: 180 Days) ──► Anonymization     │
│                       ├───► GRAPH_EDGES      (Default: 30 Days) ──► Hard SQL Delete    │
│                       └───► EXPLAINABILITY_REPORTS (Default: 60d)──► Cryptographic Zero │
│                                                                                        │
│   [ Scheduled Daily Cron / Event Trigger ]                                             │
│                       │                                                                │
│                       ▼                                                                │
│   [ purge_expired_records(tenant_id) ] ──► Execute SQL DELETE on Expired Rows          │
│                       │                                                                │
│                       ▼                                                                │
│   [ Generate Immutable ErasureAuditRecord ] ──► Compute SHA-256 Digest                │
│                                                                                        │
│   ──────────────────────────────────────────────────────────────────────────────────   │
│   [ GDPR Article 17 Right-to-be-Forgotten Request ]                                    │
│                       │                                                                │
│                       ▼ execute_gdpr_right_to_be_forgotten(tenant_id, entity_id_hash)  │
│   ┌─────────────────────────────────────────────────────────────────────────────────┐  │
│   │ 1. Hard-delete EntityModel rows matching privacy_id / HMAC hash                 │  │
│   │ 2. Hard-delete RelationshipModel edges (source_entity_id / target_entity_id)    │  │
│   │ 3. Hard-delete AlertModel records referencing transaction_id / entity           │  │
│   │ 4. Append signed ErasureAuditRecord to immutable compliance ledger              │  │
│   └─────────────────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 📑 Data Retention Categories & Default Schedules

| Data Category | Default TTL | Erasure Method | Description |
| :--- | :---: | :--- | :--- |
| **`TRANSACTION_LOGS`** | **90 Days** | `CRYPTOGRAPHIC_ZEROIZATION` | Raw ingestion telemetry, headers, and request logs. |
| **`INFERENCE_AUDITS`** | **180 Days** | `ANONYMIZATION` | Real-time fraud scoring decisions and risk scores. |
| **`GRAPH_EDGES`** | **30 Days** | `HARD_DELETE` | Dynamic network graph links between resolved entities. |
| **`EXPLAINABILITY_REPORTS`** | **60 Days** | `CRYPTOGRAPHIC_ZEROIZATION` | SHAP KernelExplainer feature attributions and prompt contexts. |

---

## ⚖️ GDPR Article 17 Right-to-be-Forgotten Protocol

When an individual or financial institution requests permanent erasure under GDPR Article 17:

1. **HMAC-SHA256 Identifier Lookup**:
   - The request specifies the one-way HMAC-SHA256 entity hash (`entity_id_hash`).
   - The engine never handles plaintext PANs, names, or national ID numbers.
2. **Physical Database Deletion Queries**:
   ```sql
   -- 1. Purge Entity Rows
   DELETE FROM entities WHERE bank_id = :bank_id AND (privacy_id = :entity_hash OR id = :entity_hash);

   -- 2. Purge Graph Edges
   DELETE FROM relationships WHERE source_entity_id = :entity_hash OR target_entity_id = :entity_hash;

   -- 3. Purge Alert Records
   DELETE FROM alerts WHERE bank_id = :bank_id AND (transaction_id = :entity_hash OR id = :entity_hash);
   ```
3. **Immutable Compliance Audit Record**:
   An `ErasureAuditRecord` is generated containing:
   - `erasure_id`: Unique identifier (e.g. `erase_gdpr_1a2b3c4d`).
   - `records_erased_count`: Actual count of physically purged SQL rows.
   - `erasure_hash`: SHA-256 cryptographic digest of the erasure operation.
4. **Scope Limitations & Statutory Retention**:
   - In accordance with Bank Secrecy Act (BSA) and FinCEN statutory mandates, filed Suspicious Activity Reports (SARs) and closed Four-Eyes supervisor approvals must be retained for 5 years and are exempt from GDPR erasure pursuant to GDPR Article 17(3)(b) (compliance with a legal obligation).

---

## 🛠️ Code Implementation Example

```python
from app.application.services.retention_engine import AutomatedRetentionEngine
from app.domain.retention_policy import DataCategory, ErasureMethod

engine = AutomatedRetentionEngine()

# 1. Configure custom tenant retention policy
policy = engine.configure_tenant_policy(
    tenant_id="bank_alpha",
    category=DataCategory.TRANSACTION_LOGS,
    ttl_days=90,
    erasure_method=ErasureMethod.CRYPTOGRAPHIC_ZEROIZATION,
)

# 2. Execute GDPR erasure
record = engine.execute_gdpr_right_to_be_forgotten(
    tenant_id="bank_gamma",
    entity_id_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
)
assert record.records_erased_count > 0
assert len(record.erasure_hash) == 64
```

---

## 🧪 Automated Unit Test Suite

```bash
pytest backend/tests/unit/test_retention_erasure_engine.py -v
```

**Verification Results:**
- `test_tenant_retention_policy_configuration`: `PASSED`
- `test_automated_ttl_purging_execution`: `PASSED` (Verifies multi-category purging)
- `test_gdpr_article_17_right_to_be_forgotten_erasure`: `PASSED` (Physical SQL deletion and SHA-256 audit trail verified)
