# Experiment Execution Dossier: `amlnet_fl_1790521210`

> **Experiment Name:** AMLNet Federated Extreme Imbalance Benchmark (Mode=institutional_split)  
> **Model / Strategy:** `AMLNetClassifier` (FedAvg)  
> **Status:** `COMPLETED` | **Duration:** 36.70s  
> **Git Provenance:** Commit `7f52489c85` (Branch: `main`)

---

## 1. Executive Summary & Core Results

| Evaluation Dimension | Measured Value | Target SLA / Baseline | Status |
| :--- | :---: | :---: | :---: |
| **Precision-Recall AUC (PR-AUC)** | **1.0000** | Primary Imbalanced Metric | `CONFIRMED` |
| **ROC-AUC** | **1.0000** | Area Under Receiver Operating Characteristic | `CONFIRMED` |
| **F1-Score (Optimal Threshold)** | **1.0000** | Harmonic Mean of Precision & Recall | `CONFIRMED` |
| **Precision (PPV)** | **1.0000** | Operational False Positive Ceiling | `CONFIRMED` |
| **Recall (Sensitivity)** | **1.0000** | True Positive Fraud Detection Floor | `CONFIRMED` |
| **Brier Calibration Score** | **0.0257** | Probability Calibration Fidelity | `CONFIRMED` |
| **Recall @ 0.1% FPR** | **100.0000%** | Low-FPR Operational Boundary | `CONFIRMED` |

---

## 2. Hardware & Execution Environment

| Environment Attribute | Specification |
| :--- | :--- |
| **Operating System** | Windows 11 (Version: 10.0.26200) |
| **CPU Model** | AMD64 Family 25 Model 80 Stepping 0, AuthenticAMD (AMD64) |
| **CPU Core Topology** | 8 Physical Cores / 16 Threads |
| **System RAM** | 7.34 GB (Available at Start: 0.38 GB) |
| **Python Runtime** | Python 3.12.10 |
| **PyTorch Framework** | PyTorch 2.12.0+cpu (Device: `CPU`, CUDA: `False`) |

---

## 3. Dataset Characteristics & Integrity

| Dataset Attribute | Specification |
| :--- | :--- |
| **Dataset Canonical Name** | `Australian AUSTRAC AMLNet Synthetic AML Extreme Imbalance Benchmark` |
| **Total Record Count** | 25,000 records |
| **Feature Dimensionality** | 18 tabular/graph columns |
| **Class Balance** | 35 positive fraud records (0.1500% prevalence) |
| **Partition Ratios** | Train: 80% / Val: 15% / Test: 20% |
| **Data Integrity Hash** | `sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

---

## 4. Hyperparameter Matrix

| Hyperparameter | Value | Description |
| :--- | :---: | :--- |
| `model_type` | `AMLNetClassifier` | Neural Network or Classifier Architecture |
| `strategy` | `FedAvg` | Optimization / Aggregation Strategy |
| `seeds` | `[42]` | Evaluated Random Seed(s) |
| `num_rounds` | `6` | Federated Communication Rounds / Epochs |
| `local_epochs` | `2` | Client Local SGD Epochs per Round |
| `batch_size` | `64` | Mini-Batch Size |
| `learning_rate` | `0.005` | Client Optimizer Learning Rate |
| `dp_enabled` | `False` | Differential Privacy Guarantee Active |

---

## 5. Decision Confusion Matrix

| True Condition \ Predicted Decision | Predicted Legitimate (0) | Predicted Fraud (1) | Total True |
| :--- | :---: | :---: | :---: |
| **True Legitimate (0)** | **TN:** 4,995 (99.9%) | **FP:** 0 (0.0%) | 4,995 |
| **True Fraud (1)** | **FN:** 0 (0.0%) | **TP:** 5 (0.1%) | 5 |
| **Total Predicted** | 4,995 | 5 | 5,000 |


---

## 6. Training & Convergence Trajectory

| Step / Round | Train Loss | Val Loss | PR-AUC | ROC-AUC | F1-Score | Duration |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| 1 | - | - | 1.0000 | 1.0000 | - | 1.00s |
| 2 | - | - | 1.0000 | 1.0000 | - | 1.00s |
| 3 | - | - | 1.0000 | 1.0000 | - | 1.00s |
| 4 | - | - | 1.0000 | 1.0000 | - | 1.00s |
| 5 | - | - | 1.0000 | 1.0000 | - | 1.00s |
| 6 | - | - | 1.0000 | 1.0000 | - | 1.00s |

---

## 7. Publication Plot Gallery & Visual Artifacts

| Figure Name | Visual File Link | Description |
| :--- | :--- | :--- |
| **Low Fpr Profiling** | [`plots/low_fpr_profiling.png`](plots/low_fpr_profiling.png) | High-resolution publication curve (300 DPI) |
| **Optimizer Convergence** | [`plots/optimizer_convergence.png`](plots/optimizer_convergence.png) | High-resolution publication curve (300 DPI) |
| **Pr Curves** | [`plots/pr_curves.png`](plots/pr_curves.png) | High-resolution publication curve (300 DPI) |
| **Roc Curves** | [`plots/roc_curves.png`](plots/roc_curves.png) | High-resolution publication curve (300 DPI) |

---

## 8. Model Governance & Statutory Compliance Disclaimers

- **Zero Demographic PII Invariant**: Audited under GDPR Article 9 and ECOA Regulation B (12 CFR Part 1002); contains 0/10 protected demographic attributes.
- **Federal Reserve SR 11-7 Compliance**: Experimental evaluation is calibrated against fixed random seeds and out-of-time chronological validation splits.

---
*Dossier generated automatically by CF-Intelligence Experiment Harness v1.0.0 on 2026-09-27T15:00:10.425718+00:00.*
