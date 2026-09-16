# 🔄 Privacy-Preserving Label Feedback Loop Specification

The Human-in-the-Loop Label Feedback Loop connects investigator case determinations (`CLOSED_CONFIRMED` or `CLOSED_FALSE_POSITIVE`) back into local bank training buffers, driving continuous federated model fine-tuning without compromising customer privacy or violating zero-PII invariants.

---

## 📌 Architectural Feedback Loop & Retraining Flow

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                     PRIVACY-PRESERVING LABEL FEEDBACK RETRAINING                       │
│                                                                                        │
│   [ Fraud Investigator / Supervisor Workbench ]                                        │
│                       │                                                                │
│                       ├───► Case Resolved: CLOSED_CONFIRMED       (Fraud Verdict)      │
│                       └───► Case Resolved: CLOSED_FALSE_POSITIVE  (Benign Verdict)     │
│                                           │                                            │
│                                           ▼                                            │
│   ┌─────────────────────────────────────────────────────────────────────────────────┐  │
│   │ 1. Zero-PII Label Privacy Guard                                                 │  │
│   │    - Validates transaction ID is HMAC-SHA256 hash (>= 32 hex chars)             │  │
│   │    - Strips customer names, PANs, and IBANs from feedback payload               │  │
│   │    - Maps verdict: CONFIRMED ──► label: 1, FALSE_POSITIVE ──► label: 0          │  │
│   └─────────────────────────────────────────────────────────────────────────────────┘  │
│                                           │                                            │
│                                           ▼                                            │
│   ┌─────────────────────────────────────────────────────────────────────────────────┐  │
│   │ 2. Local Bank Training Buffer (Inside Bank Boundary)                            │  │
│   │    - Appends tuple: (HMAC_feature_vector, verified_label, timestamp)            │  │
│   │    - Written strictly to `storage/{tenant_id}/label_buffer.json`                │  │
│   │    - Never shared across banks or transmitted to coordinator                    │  │
│   └─────────────────────────────────────────────────────────────────────────────────┘  │
│                                           │                                            │
│                                           ▼                                            │
│   ┌─────────────────────────────────────────────────────────────────────────────────┐  │
│   │ 3. Local Fine-Tuning with Differential Privacy                                  │  │
│   │    - Local PyTorch MLP / GNN fine-tunes on verified label buffer                │  │
│   │    - Injects calibrated Gaussian noise (RDP accountant: ε <= 1.0, δ = 1e-5)     │  │
│   │    - Clipped gradient delta (ΔW_k) encrypted via Paillier HE                    │  │
│   └─────────────────────────────────────────────────────────────────────────────────┘  │
│                                           │                                            │
│                                           ▼                                            │
│   [ Next FL Training Round: Global Model Updated with Human-Verified Intelligence ]    │
│   (Achieves -64.7% False Alarm Triage Load across Banking Consortium)                  │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 🏷️ Feedback Label Mapping & Metadata Schema

When an investigator resolves a case on the Investigator Case Workbench or Case Management Service:

```python
# 1. Closed Confirmed Fraud -> Label 1 (CONFIRMED_FRAUD)
timeline_event = {
    "event_type": "status_changed",
    "old_status": "investigating",
    "new_status": "closed_confirmed",
    "metadata": {
        "retraining_feedback_label": 1,
        "retraining_feedback_recorded": True,
        "supervisor_signature": "SIG_SUPERVISOR_ALICE",
        "timestamp": "2026-09-07T14:30:00Z"
    }
}

# 2. Closed False Positive -> Label 0 (FALSE_POSITIVE)
timeline_event = {
    "event_type": "status_changed",
    "old_status": "investigating",
    "new_status": "closed_false_positive",
    "metadata": {
        "retraining_feedback_label": 0,
        "retraining_feedback_recorded": True,
        "supervisor_signature": "SIG_SUPERVISOR_BOB",
        "timestamp": "2026-09-07T14:35:00Z"
    }
}
```

### Pipeline Service & Domain Components

The label feedback loop is orchestrated by two primary backend components:
- **`LocalLabelFeedbackPipeline`** ([`backend/app/application/services/label_feedback_pipeline.py`](../backend/app/application/services/label_feedback_pipeline.py)): Ingests analyst ground-truth determinations, maintains isolated per-tenant memory buffers, and computes DP-noise-protected gradient updates ($\Delta W_k$).
- **`LabelPrivacyGuard`** ([`backend/app/domain/label_privacy_guard.py`](../backend/app/domain/label_privacy_guard.py)): Enforces strict Zero-PII boundaries by rejecting identifiers shorter than 32 hex characters, regex-matching cleartext IBAN/SSN/email formats, and blocking forbidden raw attributes (`iban`, `ssn`, `email`, `customer_name`, `credit_card`).
- **`CaseManagementService`** ([`backend/app/application/services/case_service.py`](../backend/app/application/services/case_service.py)): Automatically logs feedback into the `ModelEvaluationEngine` and marks the case timeline upon terminal status transitions (`closed_confirmed` or `closed_false_positive`).

```python
# Programmatic Ingestion via LocalLabelFeedbackPipeline
pipeline = LocalLabelFeedbackPipeline()

item = pipeline.ingest_analyst_determination(
    tenant_id="bank_alpha",
    transaction_id_hash="a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4",
    determination="CONFIRMED_FRAUD",
)
# Returns LabelFeedbackItem(transaction_id_hash=..., label=FeedbackLabel.CONFIRMED_FRAUD, weight=1.0)
```

---

## 🛡️ Differential Privacy & Security Invariants

1. **Strict Zero-PII Boundary**:
   - The feedback loop operates exclusively on HMAC-SHA256 hashed transaction identifiers ($\ge 32$ hexadecimal characters).
   - Any raw transaction strings, cardholder PANs, Turkish/EU IBANs, or US SSNs raise `LabelPrivacyViolationError`.
2. **Local Buffer Isolation**:
   - Ground-truth feedback labels are written strictly to local on-premises tenant storage (`storage/{tenant_id}/label_buffer.json`). Other consortium banks have zero access to peer feedback files.
3. **Calibrated DP Noise Injection**:
   - Local model gradient updates ($\Delta W_k$) incorporate calibrated Gaussian noise:
     $$\sigma = \frac{C \sqrt{2 \ln(1.25/\delta)}}{\epsilon}$$
     where clipping threshold $C = 1.0$, default privacy budget $\epsilon = 1.0$ (validated within $(0.0, 2.0]$), and $\delta = 10^{-5}$.
   - Prevents re-identification of specific fraud victims or accounts through model inversion attacks.
4. **Prioritized Retraining Queue (Experience Replay)**:
   - Buffer items maintain business-impact priority weights ($P \in \{1, 2, 3\}$).
   - Confirmed fraud verdicts default to $P = 3, w = 2.0$; false positive verdicts default to $P = 1, w = 1.0$.
   - Retraining batch sampling (`/api/v1/feedback/retraining-batch`) supports balanced stratification to prevent fraud class starvation during federated fine-tuning.

```python
# Differential Privacy Gradient Computation
update = pipeline.compute_dp_gradient_update(tenant_id="bank_alpha", epsilon=1.0)
# Returns:
# {
#     "tenant_id": "bank_alpha",
#     "delta_weights": [0.03512, 0.07184, 0.10621, 0.14289],
#     "sample_count": 3,
#     "epsilon": 1.0,
#     "delta": 1e-05,
#     "sigma": 4.84379
# }
```

---

## 🌐 Dedicated REST API Endpoints

The label feedback pipeline exposes a dedicated `/api/v1/feedback` REST blueprint registered in `backend/app/main.py`:

| HTTP Method | Endpoint Path | Description | Access Control |
|:---|:---|:---|:---|
| `POST` | `/api/v1/feedback/ingest` | Ingests analyst ground-truth verdict with Zero-PII auto-hashing | Analyst / Supervisor |
| `GET` | `/api/v1/feedback/stats/{tenant_id}` | Retrieves tenant feedback buffer count, fraud ratio, and priority distribution | Bank Investigator |
| `POST` | `/api/v1/feedback/retraining-batch` | Samples prioritized, stratified batch for local model fine-tuning | Retraining Service |
| `POST` | `/api/v1/feedback/dp-gradient` | Computes Gaussian-DP-noise-injected weight updates ($\Delta W$) | Local FL Client |
| `DELETE` | `/api/v1/feedback/buffer/{tenant_id}` | Clears in-memory and on-disk buffer for specified tenant | Tenant Admin |

---

## 📈 Empirical Impact on Consortium Accuracy

By continuously closing the loop between human AML investigators and federated optimization:
- **False Positive Overhead**: Reduced by **`-64.7%`** (PaySim M-Pesa benchmark) and **`-58.3%`** (IEEE-CIS benchmark).
- **PR-AUC Gain**: Collaborative models gain **`+0.1480`** to **`+0.6203`** PR-AUC over single-bank isolated baselines.
- **Analyst Triage Fatigue**: High-confidence automated triage frees investigators to focus exclusively on complex multi-hop mule networks.

---

## 🧪 Automated Unit Test Suite

The label feedback loop and case management integration are validated across three dedicated test modules totaling **17 automated test cases**:

```bash
python -m pytest backend/tests/unit/test_label_feedback_pipeline_hardening.py backend/tests/unit/test_label_feedback_pipeline.py backend/tests/unit/test_case_management_feedback_loop.py -v
```

### 1. `backend/tests/unit/test_label_feedback_pipeline_hardening.py` (10 Tests)
- `test_ingest_with_alert_id_auto_hashing_satisfies_zero_pii`: Validates automatic HMAC-SHA256 hashing of arbitrary alert IDs to satisfy $\ge 32$ hex characters.
- `test_priority_queueing_and_weighted_retraining_batch_sampling`: Validates strict priority ordering ($3 \to 2 \to 1$) and consumption tracking.
- `test_stratified_batch_sampling_with_class_balance`: Validates stratified sampling ensuring rare confirmed fraud samples are balanced against false positives.
- `test_thread_safe_concurrent_feedback_ingestion`: Validates race-free ingestion across 20 concurrent worker threads with `RLock`.
- `test_buffer_persistence_and_atomic_file_restore`: Validates atomic serialization to `storage/{tenant_id}/label_buffer.json` and restoration.
- `test_dp_gradient_analytical_sigma_and_gaussian_noise`: Validates analytical Gaussian noise scale $\sigma = (C \sqrt{2 \ln(1.25/\delta)}) / \epsilon$.
- `test_end_to_end_case_service_real_feedback_ingestion`: Validates real `CaseManagementService` terminal status transitions triggering pipeline ingestion.
- `test_feedback_router_ingest_and_stats_endpoints`: Validates `POST /api/v1/feedback/ingest` (201 Created) and `GET /api/v1/feedback/stats/{tenant_id}` (200 OK).
- `test_feedback_router_retraining_batch_and_dp_gradient_endpoints`: Validates prioritized batch sampling and DP gradient generation endpoints.
- `test_invalid_inputs_and_privacy_violation_rejection`: Validates HTTP 400 rejection for missing identifiers, raw IBANs, or invalid $\epsilon > 2.0$.

### 2. `backend/tests/unit/test_label_feedback_pipeline.py` (3 Tests)
- `test_local_label_feedback_ingestion_and_buffer_management`: Verifies analyst determination label ingestion and tenant buffer tracking.
- `test_label_privacy_guard_rejects_unmasked_pii`: Verifies zero-PII enforcement blocking raw IBAN, short identifiers, or unmasked SSN/email keys.
- `test_dp_gradient_update_computation_with_noise_injection`: Verifies Gaussian DP noise injection on local gradient updates and epsilon boundary checks.

### 3. `backend/tests/unit/test_case_management_feedback_loop.py` (4 Tests)
- `test_case_escalation_and_assignment`: Verifies escalation of alerts into an investigation case and investigator assignment.
- `test_analyst_determination_closed_confirmed_feedback_loop`: Verifies `closed_confirmed` verdict records label 1 retraining feedback and generates SAR XML.
- `test_analyst_determination_closed_false_positive_feedback_loop`: Verifies `closed_false_positive` verdict records label 0 retraining feedback.
- `test_fincen_sar_report_generation_and_download`: Verifies SAR report endpoint returns valid FinCEN XML payload (`EFilingSubmission`).

**Test Execution Parity**: 17 passed in 18.38s (100% pass rate).
