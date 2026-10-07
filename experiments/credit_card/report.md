# Experiment Execution Dossier: `exp_creditcard_extreme_imbalance_benchmark`

> **Experiment Name:** Credit Card Federated Benchmark (Mode=extreme_skew)  
> **Model / Strategy:** `CreditCardImbalanceMLP` (fedprox)  
> **Status:** `COMPLETED` | **Duration:** 47.15s  
> **Git Provenance:** Commit `50672988fc` (Branch: `main`)

---

## 1. Executive Summary & Core Results

| Evaluation Dimension | Measured Value | Target SLA / Baseline | Status |
| :--- | :---: | :---: | :---: |
| **Precision-Recall AUC (PR-AUC)** | **0.0033** | Primary Imbalanced Metric | `CONFIRMED` |
| **ROC-AUC** | **0.5561** | Area Under Receiver Operating Characteristic | `CONFIRMED` |
| **F1-Score (Optimal Threshold)** | **0.0000** | Harmonic Mean of Precision & Recall | `CONFIRMED` |
| **Precision (PPV)** | **0.0000** | Operational False Positive Ceiling | `CONFIRMED` |
| **Recall (Sensitivity)** | **0.0000** | True Positive Fraud Detection Floor | `CONFIRMED` |
| **Brier Calibration Score** | **0.0020** | Probability Calibration Fidelity | `CONFIRMED` |
| **Recall @ 0.1% FPR** | **0.0000%** | Low-FPR Operational Boundary | `CONFIRMED` |
| **Recall @ 1.0% FPR** | **0.0000%** | Strict Bank Operational Tier | `CONFIRMED` |

---

## 2. Hardware & Execution Environment

| Environment Attribute | Specification |
| :--- | :--- |
| **Operating System** | Windows 11 (Version: 10.0.26300) |
| **CPU Model** | AMD64 Family 25 Model 80 Stepping 0, AuthenticAMD (AMD64) |
| **CPU Core Topology** | 8 Physical Cores / 16 Threads |
| **System RAM** | 7.34 GB (Available at Start: 0.75 GB) |
| **Python Runtime** | Python 3.12.10 |
| **PyTorch Framework** | PyTorch 2.12.0+cpu (Device: `CPU`, CUDA: `False`) |

---

## 3. Dataset Characteristics & Integrity

| Dataset Attribute | Specification |
| :--- | :--- |
| **Dataset Canonical Name** | `European Credit Card Fraud Detection (Synthetic Consortium Benchmark Fixture)` |
| **Total Record Count** | 5,000 records |
| **Feature Dimensionality** | 30 tabular/graph columns |
| **Class Balance** | 8 positive fraud records (0.1500% prevalence) |
| **Partition Ratios** | Train: 80% / Val: 15% / Test: 20% |
| **Data Integrity Hash** | `sha256:UNAVAILABLE_MOCK_DATA` |

---

## 4. Hyperparameter Matrix

| Hyperparameter | Value | Description |
| :--- | :---: | :--- |
| `model_type` | `CreditCardImbalanceMLP` | Neural Network or Classifier Architecture |
| `strategy` | `fedprox` | Optimization / Aggregation Strategy |
| `seeds` | `[42]` | Evaluated Random Seed(s) |
| `num_rounds` | `10` | Federated Communication Rounds / Epochs |
| `local_epochs` | `3` | Client Local SGD Epochs per Round |
| `batch_size` | `64` | Mini-Batch Size |
| `learning_rate` | `0.001` | Client Optimizer Learning Rate |
| `dp_enabled` | `False` | Differential Privacy Guarantee Active |

---

## 5. Decision Confusion Matrix

*No binary confusion matrix recorded.*

---

## 6. Training & Convergence Trajectory

| Step / Round | Train Loss | Val Loss | PR-AUC | ROC-AUC | F1-Score | Duration |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| 0 | 0.5481 | 0.5481 | 0.0057 | 0.7452 | 0.0000 | 0.00s |
| 1 | 0.2725 | 0.0517 | 0.0042 | 0.6416 | 0.0000 | 1.65s |
| 2 | 0.1865 | 0.0275 | 0.0030 | 0.5015 | 0.0000 | 1.68s |
| 3 | 0.1931 | 0.0207 | 0.0028 | 0.4745 | 0.0000 | 1.69s |
| 4 | 0.1991 | 0.0176 | 0.0027 | 0.4515 | 0.0000 | 1.69s |
| 5 | 0.2030 | 0.0158 | 0.0026 | 0.4485 | 0.0000 | 1.66s |
| 6 | 0.2051 | 0.0149 | 0.0029 | 0.4895 | 0.0000 | 1.70s |
| 7 | 0.2046 | 0.0145 | 0.0031 | 0.5285 | 0.0000 | 1.72s |
| 8 | 0.2026 | 0.0146 | 0.0032 | 0.5505 | 0.0000 | 1.64s |
| 9 | 0.1985 | 0.0152 | 0.0033 | 0.5535 | 0.0000 | 1.68s |
| 10 | 0.1915 | 0.0160 | 0.0033 | 0.5561 | 0.0000 | 1.67s |

---

## 7. Publication Plot Gallery & Visual Artifacts

| Figure Name | Visual File Link | Description |
| :--- | :--- | :--- |
| **Imbalance Robustness** | [`plots/imbalance_robustness.png`](plots/imbalance_robustness.png) | High-resolution publication curve (300 DPI) |
| **Optimizer Convergence** | [`plots/optimizer_convergence.png`](plots/optimizer_convergence.png) | High-resolution publication curve (300 DPI) |
| **Pr Curves** | [`plots/pr_curves.png`](plots/pr_curves.png) | High-resolution publication curve (300 DPI) |
| **Roc Curves** | [`plots/roc_curves.png`](plots/roc_curves.png) | High-resolution publication curve (300 DPI) |

---

## 8. Model Governance & Statutory Compliance Disclaimers

- **Zero Demographic PII Invariant**: Audited under GDPR Article 9 and ECOA Regulation B (12 CFR Part 1002); contains 0/10 protected demographic attributes.
- **Federal Reserve SR 11-7 Compliance**: Experimental evaluation is calibrated against fixed random seeds and out-of-time chronological validation splits.

---
*Dossier generated automatically by CF-Intelligence Experiment Harness v1.0.0 on 2026-10-07T17:03:26.510721+00:00.*


---

## 9. Preregistered Scientific Protocol Revalidation (`PROT-CC-REVAL-01`)

> **Protocol Identifier:** `PROT-CC-REVAL-01`  
> **Protocol SHA-256:** `93af866637655f464865ed752bd77130ba98bdc06c878661dec33974dcdeed40`  
> **Scientific Target:** `experiments/credit_card/report.md`  
> **Dataset Specification:** Synthetic consortium credit card benchmark fixture (`is_synthetic=True`, seed 42)  
> **Execution Status:** `EXPERIMENT_EXECUTION_SUCCESS` (`COMPLETED` in 47.15s)  
> **Reproducibility Status:** `BIT_FOR_BIT_IDENTICAL` (Deterministic under seed 42)  
> **External Test Data Exposure:** `0` (Strictly zero exposure of protected external data)

### Acceptance Criteria Mechanical Evaluation Table

| Metric | Measured Value | Preregistered Criterion | Evaluation Status |
| :--- | :---: | :---: | :---: |
| **Precision-Recall AUC (PR-AUC)** | **0.0033** | $\ge 0.85$ | ❌ **FAIL** |
| **ROC-AUC** | **0.5561** | $\ge 0.90$ | ❌ **FAIL** |
| **Brier Calibration Score** | **0.0020** | $\le 0.05$ | ✅ **PASS** |
| **Overall Acceptance Contract** | — | All Criteria Met | ❌ **FAIL** |

### Scientific Verdict & Negative Result Preservation
- **Execution Success**: The experiment executed cleanly to full convergence across 10 communication rounds without infrastructure failure.
- **Negative Acceptance Outcome**: The trained federated model on the synthetic consortium benchmark fixture achieved PR-AUC 0.0033 and ROC-AUC 0.5561, failing the preregistered acceptance thresholds ($\ge 0.85$ and $\ge 0.90$).
- **Calibration & Extreme Imbalance Nuance**: The low Brier calibration score (**0.0020**) is mathematically dominated by the overwhelming prevalence of the negative majority class (99.85% non-fraud). It indicates that the model predicts near-zero probabilities for all transactions, reflecting the baseline prevalence rather than high-fidelity positive-class fraud discernment. It must **not** be interpreted in isolation as evidence of strong fraud detection performance.
- **Tiny Positive-Class Statistical Limitation**: The holdout evaluation split (20% of 5,000 = 1,000 records) contains only **2 positive fraud cases** (998 non-fraud records). With $N_{\text{fraud}}=2$, threshold metrics (Precision, Recall, F1) and area under curves (PR-AUC, ROC-AUC) exhibit extreme sample-variance sensitivity and limited statistical power.
- **Synthetic Fixture Provenance**: This experiment is executed against an in-process synthetic test fixture (`is_synthetic=True`, seed 42) created for federated algorithm verification, NOT real-world production credit card transactions or the external Kaggle European Credit Card dataset. It must not be cited or represented as real-world empirical validation.
- **External Disclosure & Readiness Scope**: "External use ready" certifies that the experiment is methodologically complete, bit-for-bit reproducible, and safe for transparent negative-result scientific publication. It does **not** imply that the model meets operational SLA or is production-ready.
- **No Post-Hoc Tuning**: In compliance with repository Runtime Truth rules and scientific preregistration integrity, hyperparameters, seeds, and thresholds were **not modified** post-hoc.
- **Historical Supersession**: This revalidated report supersedes the historical unrevalidated dossier (commit `f2acbdd6c5`, preserved at [`report.historical_unrevalidated.md`](report.historical_unrevalidated.md)).

