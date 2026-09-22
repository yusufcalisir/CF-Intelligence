# Enterprise Local Storage, Data Partitions & Regulatory Ledgers

This directory serves as the centralized persistent storage target for the **Collaborative Fraud Intelligence (CFI)** platform. In production container deployments, this path is backed by a persistent volume mount (`storage-data:/app/storage`) and resolved dynamically via `backend/app/infrastructure/storage/storage_utils.py` (`CFI_STORAGE_DIR`).

---

## 1. Directory Structure

```text
storage/
├── README.md                          # Storage architecture, volume mounting & retention guide
├── datasets/                          # Partitioned training data
│   └── paysim/                        # Pre-partitioned bank datasets (bank_alpha, bank_beta, bank_gamma .parquet)
├── regulatory_filings/                # Generated SAR XML filings (FIU transmission payloads)
├── retention_ledger/                  # Cryptographic append-only erasure ledgers (GDPR Article 17)
├── label_feedback/                    # Incremental analyst ground-truth feedback per bank partition
├── benchmarks/                        # Pre-computed evaluation metrics and scenario benchmark outputs
├── certs/                             # Exported compliance audit reports and governance summaries
└── sbom_cyclonedx.json                # Automated CycloneDX 1.5 Software Bill of Materials (SBOM)
```

---

## 2. Partition & Subdirectory Invariants

| Directory / File | Subsystem / Owner | Storage Format | Invariant & Security Posture |
|:---|:---|:---|:---|
| **`datasets/`** | `dataloader.py` | Apache Parquet (Snappy) | Normalized transaction features (`f_0`...`f_29`). **Zero Raw PII**. |
| **`regulatory_filings/`** | `fiu_regulatory_service.py` | XML (`pacs.008` & AMLA SAR) | Standardized SAR filings with HMAC-SHA256 payload envelopes. |
| **`retention_ledger/`** | `right_to_be_forgotten_service.py` | JSON Lines (`.jsonl`) | Cryptographic append-only hash chains recording customer erasure requests under GDPR Article 17. |
| **`label_feedback/`** | `label_feedback_service.py` | JSON / Parquet partitions | Bank-isolated feedback chunks (`confirmed_fraud`, `false_positive`) for model retraining. |
| **`benchmarks/`** | `run_benchmark.py` | JSON | Empirical evaluation results across C1–C9 matrices and European AML scenarios. |
| **`certs/`** | `export_compliance_report.py` | JSON / Markdown | Formal EU AI Act (Articles 9, 13, 14, 15) and SOC 2 governance certificates. |
| **`sbom_cyclonedx.json`** | `generate_sbom.py` | CycloneDX 1.5 JSON | Cryptographic dependency inventory across Python and npm packages. |

---

## 3. Dynamic Resolution (`storage_utils.py`)

Application services never hardcode `/storage` paths. Storage resolution follows a strict priority chain:
1. `CFI_STORAGE_DIR` environment variable (e.g. `/app/storage` in Docker).
2. Standard container directory (`/app/storage`).
3. Project root `storage/` directory.
4. Guaranteed-writable OS temporary directory fallback (`tempfile.gettempdir()/cfi_storage`).

---

## 4. Git & Persistence Policy

- **Ignored by Git**: Runtime outputs (SAR XMLs, Parquet datasets, erasure ledgers, and temporary test outputs) are ignored via `.gitignore` (`storage/`).
- **Volume Mount**: Production orchestration mounts persistent named volumes (`storage-data` in Compose, `PersistentVolumeClaim` in Kubernetes) to guarantee zero data loss across container restarts.
