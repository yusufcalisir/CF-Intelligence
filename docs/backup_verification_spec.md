# 🛡️ Automated Backup Verification & Sandbox Restore Probes Specification

The Backup Verification Engine (`BackupVerifier`) delivers continuous cryptographic validation of database snapshots, PyTorch model checkpoints, and KMS keyrings, preventing data loss from silent bit rot, incomplete writes, or ransomware tampering.

---

## 📌 Architectural Overview & Verification Flow

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        AUTOMATED BACKUP INTEGRITY & PROBE PIPELINE                     │
│                                                                                        │
│  [ Primary Data Source ]                                                               │
│   - PostgreSQL 16 Schema Dumps                                                         │
│   - SQLite Isolated Bank DBs                                                           │
│   - PyTorch Global/Local Weights (.pt)                                                 │
│   - Versioned KMS Envelopes                                                            │
│               │                                                                        │
│               ▼                                                                        │
│  [ Backup Generation Engine ] ──► [ create_backup_artifact ]                           │
│                                           │ (Computes SHA-256 Digest)                  │
│                                           ▼                                            │
│                                  Status: `CREATED`                                     │
│                                           │                                            │
│                                           ├───► [ verify_checksum ]                    │
│                                           │           │                                │
│                                           │     [Hash Mismatch?]                       │
│                                           │      ├── YES ──► Status: `CORRUPTED`       │
│                                           │      │           (Alert Dispatched)        │
│                                           │      └── NO  ──► Status: `VERIFIED`        │
│                                           │                                            │
│                                           ▼                                            │
│                                [ Sandbox Restore Probe ]                               │
│                                 (run_sandbox_restore_probe)                            │
│                                           │ (Isolated Ephemeral Tempfile)              │
│                                           ▼                                            │
│                                  Status: `RESTORED`                                    │
│                                (Duration: < 5.0ms SLA)                                 │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 🔐 Core Domain Models (`app.domain.backup_record`)

### 1. `BackupArtifact` Data Structure
```python
@dataclass
class BackupArtifact:
    backup_id: str             # Unique UUID identifier (e.g. backup_9f8e7d6c)
    tenant_id: str             # Institution ID (e.g. bank_alpha, coordinator)
    file_path: str             # Target backup storage path
    size_bytes: int            # Exact file size in bytes
    sha256_checksum: str       # 64-character hexadecimal SHA-256 hash
    status: BackupStatus       # CREATED | VERIFIED | RESTORED | CORRUPTED
    created_at: datetime       # UTC timestamp of creation
```

### 2. `RestoreProbeResult` Metrics
```python
@dataclass
class RestoreProbeResult:
    probe_id: str              # Unique probe UUID (e.g. probe_1a2b3c4d)
    backup_id: str             # Correlated backup artifact ID
    success: bool              # True if checksum matches and sandbox restored
    checksum_matched: bool     # Cryptographic integrity boolean
    restore_duration_ms: float # Probe latency in milliseconds (< 5.0ms)
    probed_at: datetime        # Execution timestamp
```

---

## ⚙️ Verification Lifecycle & Methods

### 1. Artifact Registration (`create_backup_artifact`)
When a database snapshot or model checkpoint is generated, `create_backup_artifact` registers the file metadata, calculates the SHA-256 digest over the raw byte stream, and records the initial `BackupStatus.CREATED`.

```python
verifier = BackupVerifier()
artifact = verifier.create_backup_artifact(
    tenant_id="bank_alpha",
    file_path="storage/backups/bank_alpha_20260907.db",
    data_bytes=snapshot_bytes,
)
```

### 2. Continuous Checksum Attestation (`verify_checksum`)
Scheduled health monitors recalculate the SHA-256 hash across stored files.
- **Matching Hash**: Promotes artifact to `BackupStatus.VERIFIED`.
- **Mismatch**: Immediately flags artifact as `BackupStatus.CORRUPTED` and triggers automated incident alerting (`SEV2_MAJOR`).

### 3. Isolated Sandbox Restore Probe (`run_sandbox_restore_probe`)
Unlike naive checksum tools, `BackupVerifier` executes automated sandbox restore probes in isolated memory/temp sandboxes without touching production volumes:
- Verifies SQLite/PostgreSQL header sanity.
- Confirms PyTorch `torch.load(..., weights_only=True)` desensitization.
- Records exact restore duration ($\text{SLA} < 10\text{ms}$).
- Promotes artifact to `BackupStatus.RESTORED` upon success.

---

## 🧪 Automated Unit Test Suite

All backup verification, checksum mismatch detection, and sandbox restore probe behaviors are validated in continuous integration:

```bash
pytest backend/tests/unit/test_backup_verifier.py -v
```

**Test Coverage Summary:**
- `test_backup_artifact_creation_and_checksum_verification`: `PASSED`
- `test_corrupted_backup_detection`: `PASSED` (Detects byte corruption and enforces `CORRUPTED` state)
- `test_sandbox_restore_probe_execution`: `PASSED` (Executes sandbox dry-run with sub-5ms latency)
