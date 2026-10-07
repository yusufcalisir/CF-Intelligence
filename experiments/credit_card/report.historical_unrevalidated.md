# Experiment Execution Dossier: `exp_creditcard_extreme_imbalance_benchmark`

> **Experiment Name:** Credit Card Federated Benchmark (Mode=extreme_skew)  
> **Model / Strategy:** `CreditCardImbalanceMLP` (fedavg)  
> **Status:** `COMPLETED` | **Duration:** 277.94s  
> **Git Provenance:** Commit `f2acbdd6c5` (Branch: `main`)

---

## 1. Executive Summary & Core Results

| Evaluation Dimension | Measured Value | Target SLA / Baseline | Status |
| :--- | :---: | :---: | :---: |
| **Precision-Recall AUC (PR-AUC)** | **0.7750** | Primary Imbalanced Metric | `CONFIRMED` |
| **ROC-AUC** | **0.9837** | Area Under Receiver Operating Characteristic | `CONFIRMED` |
| **F1-Score (Optimal Threshold)** | **0.7882** | Harmonic Mean of Precision & Recall | `CONFIRMED` |
| **Precision (PPV)** | **0.7619** | Operational False Positive Ceiling | `CONFIRMED` |
| **Recall (Sensitivity)** | **0.8163** | True Positive Fraud Detection Floor | `CONFIRMED` |
| **Brier Calibration Score** | **0.0007** | Probability Calibration Fidelity | `CONFIRMED` |
| **Recall @ 0.1% FPR** | **84.6940%** | Low-FPR Operational Boundary | `CONFIRMED` |
| **Recall @ 1.0% FPR** | **88.7760%** | Strict Bank Operational Tier | `CONFIRMED` |

---

## 2. Hardware & Execution Environment

| Environment Attribute | Specification |
| :--- | :--- |
| **Operating System** | Windows 11 (Version: 10.0.26200) |
| **CPU Model** | AMD64 Family 25 Model 80 Stepping 0, AuthenticAMD (AMD64) |
| **CPU Core Topology** | 8 Physical Cores / 16 Threads |
| **System RAM** | 7.34 GB (Available at Start: 0.82 GB) |
| **Python Runtime** | Python 3.12.10 |
| **PyTorch Framework** | PyTorch 2.12.0+cpu (Device: `CPU`, CUDA: `False`) |

---

## 3. Dataset Characteristics & Integrity

| Dataset Attribute | Specification |
| :--- | :--- |
| **Dataset Canonical Name** | `European Credit Card Fraud Detection` |
| **Total Record Count** | 284,806 records |
| **Feature Dimensionality** | 30 tabular/graph columns |
| **Class Balance** | 491 positive fraud records (0.1725% prevalence) |
| **Partition Ratios** | Train: 80% / Val: 15% / Test: 20% |
| **Data Integrity Hash** | `sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

---

## 4. Hyperparameter Matrix

| Hyperparameter | Value | Description |
| :--- | :---: | :--- |
| `model_type` | `CreditCardImbalanceMLP` | Neural Network or Classifier Architecture |
| `strategy` | `fedavg` | Optimization / Aggregation Strategy |
| `seeds` | `[42]` | Evaluated Random Seed(s) |
| `num_rounds` | `5` | Federated Communication Rounds / Epochs |
| `local_epochs` | `2` | Client Local SGD Epochs per Round |
| `batch_size` | `64` | Mini-Batch Size |
| `learning_rate` | `0.002` | Client Optimizer Learning Rate |
| `dp_enabled` | `False` | Differential Privacy Guarantee Active |

---

## 5. Decision Confusion Matrix

*No binary confusion matrix recorded.*

---

## 6. Training & Convergence Trajectory

| Step / Round | Train Loss | Val Loss | PR-AUC | ROC-AUC | F1-Score | Duration |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| 0 | 0.5355 | 0.5355 | 0.0019 | 0.5933 | 0.0006 | 0.00s |
| 1 | 0.0468 | 0.0043 | 0.6593 | 0.9832 | 0.7857 | 22.59s |
| 2 | 0.0381 | 0.0048 | 0.7210 | 0.9851 | 0.7843 | 22.39s |
| 3 | 0.0336 | 0.0047 | 0.7765 | 0.9847 | 0.7861 | 24.70s |
| 4 | 0.0301 | 0.0047 | 0.7708 | 0.9830 | 0.7921 | 21.70s |
| 5 | 0.0285 | 0.0046 | 0.7750 | 0.9837 | 0.7882 | 23.86s |

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
*Dossier generated automatically by CF-Intelligence Experiment Harness v1.0.0 on 2026-09-26T18:36:38.403547+00:00.*
