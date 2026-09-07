# 🔒 Air-Gapped Banking Data Center Deployment & Security Guide

The Collaborative Fraud Intelligence (CFI) platform supports fully air-gapped, zero-internet on-premises deployments tailored for sovereign banking enclaves, central bank nodes, and high-security financial data centers.

---

## 📌 Architectural Principles for Air-Gapped Operation

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                          SECURE AIR-GAPPED BANKING PERIMETER                           │
│                                                                                        │
│  [ Secure Media / Optical Ingest ] ──► [ AirGapBundleBuilder Verifier ]               │
│                                                   │ (SHA-256 Validated)                │
│                                                   ▼                                    │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐  │
│  │                            LOCAL ISOLATED RUNTIME                                │  │
│  │                                                                                  │  │
│  │  [ Local Container Registry ]  ──►  Docker Daemon / Offline Kubernetes Cluster  │  │
│  │  [ Offline Wheelhouse Mirror ] ──►  CPython 3.12 Runtime                          │  │
│  │  [ Offline Model Registry ]    ──►  PyTorch Champion Enclave                      │  │
│  │  [ Alembic Offline SQL Migrator ] ──► PostgreSQL 16 Schema / SQLite DB          │  │
│  │                                                                                  │  │
│  └──────────────────────────────────────────────────────────────────────────────────┘  │
│                                                   ▲                                    │
│                                                   │ Inbound LAN Only                   │
│  [ Core Banking Network ] ──► [ PerimeterWAFGuard ] ── (mTLS + Strict IP Whitelist)   │
│                                (SQLi / XSS Blocked)                                    │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

1. **Zero External Internet Access**: No telemetry calls, package registry pulls (PyPI, npm), container image pulls (Docker Hub), or external OCSP/CRL queries are executed.
2. **Cryptographic Bundle Attestation**: Every offline asset is verified against a SHA-256 signed manifest before installation.
3. **Strict Perimeter Filtering**: Ingress traffic from the internal banking LAN is inspected by `PerimeterWAFGuard` with strict IP whitelisting and attack payload rejection.
4. **Offline Database Migrations**: Schema alterations are generated as static SQL scripts via Alembic `--sql` mode, enabling manual review by bank Database Administrators (DBAs) prior to execution.

---

## 📦 Air-Gapped Bundle Structure & Manifest

The `AirGapBundleBuilder` (`backend/app/infrastructure/deployment/airgap_installer.py`) generates deterministic, versioned deployment bundles with an immutable cryptographic manifest.

### Manifest Schema (`airgap_manifest.json`)
```json
{
  "bundle_id": "airgap_a1b2c3d4",
  "version": "v2.0.0",
  "sha256_checksum": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "total_files": 4,
  "created_at": "2026-09-07T12:00:00Z"
}
```

### Bundle Contents (`cfi_airgap_v2.0.0.json`)
```json
{
  "bundle_id": "airgap_a1b2c3d4",
  "version": "v2.0.0",
  "offline_wheels": [
    "torch-2.2.0-cp312-manylinux.whl",
    "fastapi-0.110.0.whl",
    "alembic-1.14.0.whl",
    "cryptography-46.0.5.whl"
  ],
  "docker_compose": "version: '3.8'\nservices:\n  cfi_backend:\n    image: cfi_backend:offline",
  "model_weights": "model_v2.0.0.pt"
}
```

---

## 🛠️ Step-by-Step Offline Deployment Procedure

### Stage 1: Build & Sign Bundle in Staging Environment
On an internet-connected build runner:
```bash
python -c "
from pathlib import Path
from app.infrastructure.deployment.airgap_installer import AirGapBundleBuilder
builder = AirGapBundleBuilder()
manifest = builder.build_airgap_bundle(output_dir=Path('./dist/airgap'), target_version='v2.0.0')
print(f'Bundle generated: {manifest.bundle_id}, SHA-256: {manifest.sha256_checksum}')
"
```

### Stage 2: Offline Migration Generation (`--sql` Mode)
Generate the complete offline SQL schema migration without connecting to a live database:
```bash
# Output raw SQL for DBA change approval
cd backend
alembic upgrade 001_production_domain_tables:002_core_and_aml_tables --sql > /dist/airgap/migrations_v2.sql
```

DBAs can review and execute this script directly within PostgreSQL:
```sql
-- Executed inside target tenant schema
SET search_path = bank_alpha;
\i /dist/airgap/migrations_v2.sql
```

### Stage 3: Transfer & Verify Integrity Inside Air-Gapped Enclave
Transfer the bundle into the isolated network via write-once optical media or hardware-encrypted USB:
```python
from pathlib import Path
from app.infrastructure.deployment.airgap_installer import AirGapBundleBuilder

builder = AirGapBundleBuilder()
bundle_file = Path("/media/secure/cfi_airgap_v2.0.0.json")
manifest_file = Path("/media/secure/airgap_manifest.json")

# Cryptographic verification
is_valid = builder.verify_airgap_bundle(bundle_file=bundle_file, manifest_file=manifest_file)
if not is_valid:
    raise SecurityError("CRITICAL: Air-gapped bundle corrupted or tampered!")
```

### Stage 4: Launch Offline Docker Stack
```bash
# Load pre-packaged offline container images
docker load -i cfi_backend_offline.tar
docker load -i cfi_frontend_offline.tar
docker load -i cfi_redis_offline.tar
docker load -i cfi_postgres_offline.tar

# Start isolated cluster with local volumes
docker compose -f docker-compose.airgap.yml up -d
```

---

## 🛡️ Perimeter WAF Guard (`PerimeterWAFGuard`)

The air-gapped gateway enforces edge inspection through `PerimeterWAFGuard` (`backend/app/infrastructure/security/perimeter_waf.py`), protecting internal APIs from lateral compromise:

| Inspection Layer | Rule Trigger | Action | Description |
| :--- | :--- | :---: | :--- |
| **IP Whitelisting** | `WAFRuleCategory.IP_WHITELIST` | **REJECT (403)** | Rejects requests from non-whitelisted internal network addresses. |
| **SQL Injection** | `WAFRuleCategory.SQLI_INJECTION` | **REJECT (400)** | Blocks `UNION SELECT`, `DROP TABLE`, `OR 1=1`, and stacked queries. |
| **XSS Filtering** | `WAFRuleCategory.XSS_ATTACK` | **REJECT (400)** | Blocks script tags, javascript pseudoprotocols, and DOM event injection. |
| **Path Traversal** | `WAFRuleCategory.PATH_TRAVERSAL` | **REJECT (400)** | Intercepts `../` directory traversal attempts against file endpoints. |

### Configuration Example
```python
from app.infrastructure.security.perimeter_waf import PerimeterWAFGuard

waf = PerimeterWAFGuard(
    whitelisted_ips=["10.10.20.1", "10.10.20.2"],
    enforce_whitelist=True
)

result = waf.inspect_request(client_ip="10.10.20.1", body='{"transaction_id": "tx_99"}')
assert result.allowed is True
```

---

## 🧪 Automated Verification Test Suite

Verify all air-gapped packaging, checksum attestation, and perimeter defense behaviors using the targeted test suite:

```bash
pytest backend/tests/unit/test_perimeter_airgap.py -v
```

**Verification Results:**
- `test_perimeter_waf_request_inspection`: `PASSED` (Whitelisting, SQLi, XSS blocked)
- `test_airgap_bundle_building_and_checksum_verification`: `PASSED` (Manifest generation, byte-exact SHA-256 verification, and tamper detection)
