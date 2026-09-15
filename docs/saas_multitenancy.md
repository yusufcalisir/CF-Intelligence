# 🏢 Enterprise SaaS Multi-Tenancy & Tenant Lifecycle Isolation Architecture

The Collaborative Fraud Intelligence (CFI) platform operates as a multi-tenant SaaS and enterprise on-premise framework providing hard database schema isolation, context-routed session management, automated Alembic migrations, atomic quota metering, and per-institution cryptographic key paths.

---

## 🏗️ Multi-Tenant Architecture Overview

```
                          ┌────────────────────────┐
                          │   Inbound API Gateway  │
                          │   (JWT / mTLS Auth)    │
                          └───────────┬────────────┘
                                      │ Tenant ID Extracted
                                      ▼
                        ┌────────────────────────────┐
                        │ TenantAccessControl        │
                        │ Middleware (BOLA Defense)  │
                        └─────────────┬──────────────┘
                                      │ active_tenant.set("bank_a")
                                      ▼
             ┌─────────────────────────────────────────────────┐
             │       AsyncEngine Multi-Tenant Pool             │
             ├────────────────────────┬────────────────────────┤
             │  PostgreSQL / CRDB     │   SQLite Edge Mode     │
             │  CREATE SCHEMA         │   Dynamic Isolated DB  │
             │  SET search_path       │   storage/cfi_bank.db  │
             └───────────┬────────────┴───────────┬────────────┘
                         │                        │
         ┌───────────────┴──────────────┐ ┌───────┴──────────────────────┐
         │ Alembic Migration Engine     │ │ Tenant KMS & Key Lifecycle   │
         │ 001 -> 002 Linear Revisions  │ │ AES-256-GCM v1/v2 Envelopes  │
         │ Dynamic tenant_configs Scan  │ │ Re-encryption & Rotation     │
         └──────────────────────────────┘ └──────────────────────────────┘
```

---

## 🔒 1. Dual-Engine Data Isolation Strategy

Data isolation is enforced at the database engine level to comply with SOC 2 Type II, PCI-DSS, and European Banking Secrecy laws:

### 1.1 PostgreSQL / CockroachDB Schema Scoping
- **Dedicated Schema Namespace:** Each institution is assigned a dedicated schema namespace `tenant_{bank_id}` isolated via `CREATE SCHEMA IF NOT EXISTS`.
- **Dynamic Search Path Scoping:** Operations scope session transactions using `SET search_path TO tenant_{bank_id}, public`.
- **SQL Injection Sanitization:** All tenant schema identifiers are validated against SQL injection patterns and double-quoted via `_pg_quote_identifier` before execution.

### 1.2 SQLite Edge & Development Multi-Database Isolation
- **Dynamic Database Files:** In lightweight or edge deployments (`database_type == "sqlite"`), each bank node operates against its own isolated physical SQLite database file (`_STORAGE_ROOT/cfi_{bank_id}.db`).
- **Batch Migration Compatibility:** All schema alter operations render in batch mode (`render_as_batch=True`), preventing SQLite `ALTER TABLE` schema lockups.

---

## 🔄 2. Alembic Migration Engine & Dynamic Tenant Discovery

Database migrations are managed programmatically via Alembic (`alembic.ini` and `backend/app/infrastructure/database/migrations/env.py`):

- **Linear Dual-Revision Lifecycle:**
  - `001_production_domain_tables`: Generates foundational multi-tenant domain tables (`banks`, `alerts`, `cases`, `models`, `audit_logs`).
  - `002_core_and_aml_tables`: Generates advanced AML, graph relationship, simulation, and evidence tables.
- **Dynamic Active Tenant Discovery (`_get_active_tenants`):**
  Instead of relying on a static hardcoded bank list, the migration runner dynamically queries registered institutions from the `tenant_configs` relational table, ensuring newly onboarded banks are migrated automatically:
  ```python
  def _get_active_tenants(connection) -> list[str]:
      """Query tenant_configs table dynamically, falling back to VALID_TENANTS."""
      try:
          result = connection.execute(text("SELECT tenant_id FROM tenant_configs WHERE status = 'ACTIVE'"))
          tenants = [row[0] for row in result.fetchall()]
          return tenants if tenants else list(VALID_TENANTS)
      except Exception:
          return list(VALID_TENANTS)
  ```
- **Automatic Schema Adoption (`_ensure_migrated_or_stamped`):**
  When booting against pre-existing database tables created during bootstrap, `migration_manager` utilities automatically inspect domain table presence and stamp the active `head` revision, preventing revision collision crashes.
- **Offline Migration Mode:**
  Supports standalone SQL DDL generation via `alembic upgrade head --sql` for air-gapped banking deployments.

---

## 🛡️ 3. Context-Driven Routing & Broken Object Level Authorization (BOLA) Defense

- **Context-Bound Execution:** The `active_tenant` `ContextVar` securely isolates asynchronous task contexts without cross-request leakage.
- **BOLA / IDOR Enforcement:** The `enforce_tenant_isolation` dependency extracts caller identity from cryptographically validated JWT access tokens and rejects unauthorized cross-tenant requests with `HTTP 403 Forbidden` (`TenantAccessDenied`).

---

## ⚡ 4. Concurrency Safety & Atomic Tenant Quota Metering

High-volume inference bursts and collaborative FL rounds require race-condition-free quota tracking:

- **Atomic Quota Acquisition:** `TenantMeteringService` executes an atomic `acquire_quota(tenant_id, feature, count)` routine under reentrant lock (`threading.RLock()`), eliminating check-then-act race conditions during concurrent burst load.
- **Fail-Closed Feature Validation:** Restricted strictly to supported features (`INFERENCE`, `FL_ROUND`, `STORAGE`). Any unrecognized feature string fails closed and returns `(False, "Unsupported quota feature")`. Non-positive counts (`count <= 0`) are rejected with `ValueError`.
- **Temporal Rollover Invariant:**
  - Daily Inferences: Automatically reset to 0 upon day boundary rollover (`last_reset_date != today`).
  - Monthly FL Rounds: Automatically reset to 0 upon month boundary rollover (`last_reset_month != this_month`), preventing historical rounds from blocking training in new billing cycles.
- **Storage Telemetry & Quota Bounds:** Real-time storage consumption tracking via `update_storage_usage(tenant_id, storage_mb)`, incorporated into dynamic billing summaries (`daily_inferences`, `monthly_fl_rounds`, `storage_used_mb`, `estimated_cost_usd`).
- **Redis Namespace Enclosure:** `CacheService.get_tenant_key` deterministically prefixes caching keys (`cfi:tenant:{clean_tenant}:{resource_key}`) to prevent cross-tenant cache pollution.
- **Three-State Idempotency Engine:** `IdempotencyService` guarantees request deduplication across `"ACQUIRED"`, `"IN_PROGRESS"`, and `"HIT"` states, rejecting concurrent duplicate submissions of financial cases or settlement claims.
- **Thread-Safe Champion Promotion:** `ModelRegistry` enforces reentrant mutual exclusion (`threading.RLock()`) and atomic file replacement (`tempfile` + `os.replace`), preventing dual-champion states under parallel sign-offs.

---

## 🔐 5. Cryptographic Key Lifecycle & Multi-Tenant KMS

Each institution maintains isolated cryptographic keys managed through `TenantKMSService`:

- **Versioned Envelope Format:** Stored ciphertexts maintain explicit versioning headers:
  $$\mathrm{Payload} = \mathtt{v}\{\mathrm{version}\} : \mathrm{iv}_{\mathrm{b64}} : \mathrm{tag}_{\mathrm{b64}} : \mathrm{ciphertext}_{\mathrm{b64}}$$
- **Data Re-Encryption (`re_encrypt_tenant_data`):** Migrates historical encrypted records from retired keys to active version keys during scheduled maintenance.
- **Revocation Fail-Closed (`invalidate_retired_keys`):** Un-migrated ciphertexts under revoked keys immediately fail closed with `DecryptionError`.
- **Scheduled Maintenance Cron (`POST /v1/cron/rotate-keys`):** Protected endpoint authorized with `CRON_SECRET_KEY` for scheduled Kubernetes CronJob or CloudWatch Event execution.

---

## 📊 6. Tenant Lifecycle State Machine

```
      ┌────────────────┐
      │  PROVISIONING  │ ──► DDL Migration & Schema Provisioning
      └───────┬────────┘
              │ Schema Verified
              ▼
      ┌────────────────┐
      │     ACTIVE     │ ──► Full FL Training & Scoring Participation
      └───────┬────────┘
              │ Compliance Flag / Sanctions
              ▼
      ┌────────────────┐
      │   SUSPENDED    │ ──► Quota Rejected (HTTP 403 / 429)
      └───────┬────────┘
              │ Contract Termination
              ▼
      ┌────────────────┐
      │    DELETED     │ ──► Cryptographic Zeroization & GDPR Art. 17 Purge
      └────────────────┘
```

---

## 🛠️ 7. Developer Code Example

```python
from app.infrastructure.database import active_tenant
from app.infrastructure.tenant_provisioner import TenantProvisioner
from app.infrastructure.database.migration_manager import upgrade_head

# 1. Onboard a new bank node with automated Alembic migration
provisioner = TenantProvisioner()
tenant_record = await provisioner.provision_tenant("bank_delta", "Delta Regional Bank")
print("Provisioned:", tenant_record.name, tenant_record.status)

# Optional: Run Alembic migrations to latest head across schemas
upgrade_head()

# 2. Execute tenant-isolated operation
token = active_tenant.set("bank_delta")
try:
    # Operations here automatically execute against bank_delta schema / DB
    pass
finally:
    active_tenant.reset(token)
```

---

## 🧪 8. Automated Test Verification Matrix

All SaaS multi-tenancy capabilities are verified by continuous automated test suites:

| Test Suite | File Path | Verified Capabilities | Status |
| :--- | :--- | :--- | :---: |
| **Tenant Lifecycle** | `backend/tests/unit/test_saas_multi_tenancy.py` | State transitions (PROVISIONING $\to$ ACTIVE $\to$ SUSPENDED $\to$ DELETED) | `3/3 PASSED` |
| **Multi-Tenancy Hardening** | `backend/tests/unit/test_saas_multi_tenancy_hardening.py` | Invariants, thread concurrency, monthly rollover, fail-closed quota, storage | `9/9 PASSED` |
| **KMS & DB Isolation** | `backend/tests/unit/test_multi_tenancy.py` | Engine isolation, KMS key persistence, model vault directories, log filtering | `18/18 PASSED` |
| **BOLA / IDOR Security** | `backend/tests/unit/test_multi_tenant_security_audit.py` | Cross-tenant 403 isolation, ContextVar leakage prevention, Redis key prefixing | `4/4 PASSED` |
| **Tenant KMS & Metering** | `backend/tests/unit/test_tenant_kms_metering.py` | Quota boundary enforcement, 429 rate limits, per-tenant envelope encryption | `4/4 PASSED` |
| **Alembic Migrations** | `backend/tests/integration/test_alembic_migrations.py` | Dual revision linear head (`002_core_and_aml_tables`), offline SQL, dynamic discovery | `4/4 PASSED` |
| **KMS Key Lifecycle** | `backend/tests/unit/test_key_lifecycle_vault.py` | Versioned envelope encryption, re-encryption, invalidation, rotation cron | `5/5 PASSED` |
| **Concurrency Safety** | `backend/tests/unit/test_concurrency_safety.py` | Atomic quota acquire under 50 threads, single champion promotion, idempotency locks | `5/5 PASSED` |
