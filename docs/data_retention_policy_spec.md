# 🗑️ Enterprise Data Retention & GDPR Article 17 Erasure Specification

The Automated Retention & Erasure Engine enforces Time-To-Live (TTL) data purging and fulfills GDPR Article 17 Right-to-be-Forgotten requests with cryptographic zeroization and tamper-proof audit trails.

---

## 📌 Data Retention Categories & Default TTLs

| Data Category | Default TTL | Erasure Method | Description |
| :--- | :--- | :--- | :--- |
| **`TRANSACTION_LOGS`** | 90 Days | `CRYPTOGRAPHIC_ZEROIZATION` | Raw transaction telemetry logs. |
| **`INFERENCE_AUDITS`** | 180 Days | `ANONYMIZATION` | Real-time inference scoring audit logs. |
| **`GRAPH_EDGES`** | 30 Days | `HARD_DELETE` | Temporary graph relationship edges. |
| **`EXPLAINABILITY_REPORTS`** | 60 Days | `CRYPTOGRAPHIC_ZEROIZATION` | SHAP feature attribution reports. |

---

## ⚖️ GDPR Article 17 Right-to-be-Forgotten Protocol

When a customer or institution submits an erasure request under GDPR Article 17:
1. `execute_gdpr_right_to_be_forgotten` is invoked with the HMAC-SHA256 hashed entity identifier (`entity_id_hash`).
2. **Physical Database Deletions**: Executes genuine SQL `DELETE` queries across:
   - `EntityModel`: Hard-deletes matching entity rows (filtered by `bank_id` and `privacy_id`/`id`).
   - `RelationshipModel`: Hard-deletes graph edges where `source_entity_id` or `target_entity_id` matches.
   - `AlertModel`: Hard-deletes transaction alerts matching `transaction_id` or alert `id` for the tenant.
3. **TTL Purging Wiring**: `purge_expired_records` executes SQL `DELETE` queries for `AlertModel` (`TRANSACTION_LOGS`, `INFERENCE_AUDITS`), `RelationshipModel` (`GRAPH_EDGES`), and `SharedIntelligenceModel` (`EXPLAINABILITY_REPORTS`).
4. **Scope Limitation**: Other data categories (raw CSV/Parquet uploads, cases in `CaseModel`, SAR draft XML files, and model gradient checkpoints) are not yet wired to automated database purge tasks and remain governed by external storage and statutory retention rules.
5. A SHA-256 signed `ErasureAuditRecord` is generated and committed to the immutable compliance ledger with actual rowcounts of erased database records.
