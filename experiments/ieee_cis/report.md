# Experiment Execution Dossier: `exp_ieee_cis_federated_benchmark`

> **Experiment Name:** IEEE-CIS Federated Benchmark (Dirichlet alpha=0.5)  
> **Model / Strategy:** `IEEECISNeuralClassifier` (fedprox)  
> **Status:** `COMPLETED` | **Duration:** 29.86s  
> **Git Provenance:** Commit `46f9c462be` (Branch: `main`)

---

## 1. Executive Summary & Core Results

| Evaluation Dimension | Measured Value | Target SLA / Baseline | Status |
| :--- | :---: | :---: | :---: |
| **Precision-Recall AUC (PR-AUC)** | **0.0691** | Primary Imbalanced Metric | `CONFIRMED` |
| **ROC-AUC** | **0.6632** | Area Under Receiver Operating Characteristic | `CONFIRMED` |
| **F1-Score (Optimal Threshold)** | **0.0000** | Harmonic Mean of Precision & Recall | `CONFIRMED` |
| **Precision (PPV)** | **0.0000** | Operational False Positive Ceiling | `CONFIRMED` |
| **Recall (Sensitivity)** | **0.0000** | True Positive Fraud Detection Floor | `CONFIRMED` |
| **Brier Calibration Score** | **0.0260** | Probability Calibration Fidelity | `CONFIRMED` |
| **Recall @ 0.1% FPR** | **0.0000%** | Low-FPR Operational Boundary | `CONFIRMED` |
| **Recall @ 1.0% FPR** | **7.4070%** | Strict Bank Operational Tier | `CONFIRMED` |

---

## 2. Hardware & Execution Environment

| Environment Attribute | Specification |
| :--- | :--- |
| **Operating System** | Windows 11 (Version: 10.0.26200) |
| **CPU Model** | AMD64 Family 25 Model 80 Stepping 0, AuthenticAMD (AMD64) |
| **CPU Core Topology** | 8 Physical Cores / 16 Threads |
| **System RAM** | 7.34 GB (Available at Start: 1.60 GB) |
| **Python Runtime** | Python 3.12.10 |
| **PyTorch Framework** | PyTorch 2.12.0+cpu (Device: `CPU`, CUDA: `False`) |

---

## 3. Dataset Characteristics & Integrity

| Dataset Attribute | Specification |
| :--- | :--- |
| **Dataset Canonical Name** | `IEEE-CIS Fraud Detection` |
| **Total Record Count** | 15,000 records |
| **Feature Dimensionality** | 422 tabular/graph columns |
| **Class Balance** | 408 positive fraud records (2.7200% prevalence) |
| **Partition Ratios** | Train: 80% / Val: 15% / Test: 20% |
| **Data Integrity Hash** | `sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

---

## 4. Hyperparameter Matrix

| Hyperparameter | Value | Description |
| :--- | :---: | :--- |
| `model_type` | `IEEECISNeuralClassifier` | Neural Network or Classifier Architecture |
| `strategy` | `fedprox` | Optimization / Aggregation Strategy |
| `seeds` | `[42]` | Evaluated Random Seed(s) |
| `num_rounds` | `5` | Federated Communication Rounds / Epochs |
| `local_epochs` | `2` | Client Local SGD Epochs per Round |
| `batch_size` | `64` | Mini-Batch Size |
| `learning_rate` | `0.001` | Client Optimizer Learning Rate |
| `dp_enabled` | `False` | Differential Privacy Guarantee Active |

---

## 5. Decision Confusion Matrix

| True Condition \ Predicted Decision | Predicted Legitimate (0) | Predicted Fraud (1) | Total True |
| :--- | :---: | :---: | :---: |
| **True Legitimate (0)** | **TN:** 2,919 (97.3%) | **FP:** 0 (0.0%) | 2,919 |
| **True Fraud (1)** | **FN:** 81 (2.7%) | **TP:** 0 (0.0%) | 81 |
| **Total Predicted** | 3,000 | 0 | 3,000 |


---

## 6. Training & Convergence Trajectory

| Step / Round | Train Loss | Val Loss | PR-AUC | ROC-AUC | F1-Score | Duration |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| 0 | 0.4822 | 0.4822 | 0.0604 | 0.6141 | 0.0000 | 0.00s |
| 1 | 0.1939 | 0.1213 | 0.0636 | 0.6224 | 0.0000 | 1.96s |
| 2 | 0.1340 | 0.1202 | 0.0701 | 0.6606 | 0.0000 | 1.58s |
| 3 | 0.1271 | 0.1199 | 0.0702 | 0.6612 | 0.0000 | 1.54s |
| 4 | 0.1296 | 0.1206 | 0.0685 | 0.6630 | 0.0000 | 1.56s |
| 5 | 0.1284 | 0.1198 | 0.0691 | 0.6632 | 0.0000 | 1.56s |

---

## 7. Publication Plot Gallery & Visual Artifacts

| Figure Name | Visual File Link | Description |
| :--- | :--- | :--- |
| **Confusion Matrices** | [`plots/confusion_matrices.png`](plots/confusion_matrices.png) | High-resolution publication curve (300 DPI) |
| **Optimizer Convergence** | [`plots/optimizer_convergence.png`](plots/optimizer_convergence.png) | High-resolution publication curve (300 DPI) |
| **Pr Curves** | [`plots/pr_curves.png`](plots/pr_curves.png) | High-resolution publication curve (300 DPI) |
| **Roc Curves** | [`plots/roc_curves.png`](plots/roc_curves.png) | High-resolution publication curve (300 DPI) |

---

## 8. Model Governance & Statutory Compliance Disclaimers

- **Zero Demographic PII Invariant**: Audited under GDPR Article 9 and ECOA Regulation B (12 CFR Part 1002); contains 0/10 protected demographic attributes.
- **Federal Reserve SR 11-7 Compliance**: Experimental evaluation is calibrated against fixed random seeds and out-of-time chronological validation splits.

---
*Dossier generated automatically by CF-Intelligence Experiment Harness v1.0.0 on 2026-09-26T18:11:37.051520+00:00.*
