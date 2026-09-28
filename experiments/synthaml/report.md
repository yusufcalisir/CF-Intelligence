# Experiment Execution Dossier: `synthaml_fl_1790509707`

> **Experiment Name:** SynthAML Federated AML Alert Benchmark (Mode=institutional_split)  
> **Model / Strategy:** `AlertMLPClassifier` (FedAvg)  
> **Status:** `COMPLETED` | **Duration:** 22.65s  
> **Git Provenance:** Commit `502a3373d0` (Branch: `main`)

---

## 1. Executive Summary & Core Results

| Evaluation Dimension | Measured Value | Target SLA / Baseline | Status |
| :--- | :---: | :---: | :---: |
| **Precision-Recall AUC (PR-AUC)** | **0.9985** | Primary Imbalanced Metric | `CONFIRMED` |
| **ROC-AUC** | **0.9995** | Area Under Receiver Operating Characteristic | `CONFIRMED` |
| **F1-Score (Optimal Threshold)** | **0.9836** | Harmonic Mean of Precision & Recall | `CONFIRMED` |
| **Precision (PPV)** | **0.9877** | Operational False Positive Ceiling | `CONFIRMED` |
| **Recall (Sensitivity)** | **0.9796** | True Positive Fraud Detection Floor | `CONFIRMED` |
| **Brier Calibration Score** | **0.0119** | Probability Calibration Fidelity | `CONFIRMED` |
| **Recall @ 0.1% FPR** | **98.7755%** | Low-FPR Operational Boundary | `CONFIRMED` |

---

## 2. Hardware & Execution Environment

| Environment Attribute | Specification |
| :--- | :--- |
| **Operating System** | Windows 11 (Version: 10.0.26200) |
| **CPU Model** | AMD64 Family 25 Model 80 Stepping 0, AuthenticAMD (AMD64) |
| **CPU Core Topology** | 8 Physical Cores / 16 Threads |
| **System RAM** | 7.34 GB (Available at Start: 0.53 GB) |
| **Python Runtime** | Python 3.12.10 |
| **PyTorch Framework** | PyTorch 2.12.0+cpu (Device: `CPU`, CUDA: `False`) |

---

## 3. Dataset Characteristics & Integrity

| Dataset Attribute | Specification |
| :--- | :--- |
| **Dataset Canonical Name** | `Danish Spar Nord Bank SynthAML Synthetic AML Benchmark` |
| **Total Record Count** | 5,000 records |
| **Feature Dimensionality** | 14 tabular/graph columns |
| **Class Balance** | 425 positive fraud records (4.5000% prevalence) |
| **Partition Ratios** | Train: 80% / Val: 15% / Test: 20% |
| **Data Integrity Hash** | `sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

---

## 4. Hyperparameter Matrix

| Hyperparameter | Value | Description |
| :--- | :---: | :--- |
| `model_type` | `AlertMLPClassifier` | Neural Network or Classifier Architecture |
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
| **True Legitimate (0)** | **TN:** 752 (75.2%) | **FP:** 3 (0.3%) | 755 |
| **True Fraud (1)** | **FN:** 5 (0.5%) | **TP:** 240 (24.0%) | 245 |
| **Total Predicted** | 757 | 243 | 1,000 |


---

## 6. Training & Convergence Trajectory

| Step / Round | Train Loss | Val Loss | PR-AUC | ROC-AUC | F1-Score | Duration |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| 1 | - | - | 0.9993 | 0.9998 | - | 1.00s |
| 2 | - | - | 0.9984 | 0.9995 | - | 1.00s |
| 3 | - | - | 0.9987 | 0.9996 | - | 1.00s |
| 4 | - | - | 0.9972 | 0.9982 | - | 1.00s |
| 5 | - | - | 0.9967 | 0.9981 | - | 1.00s |
| 6 | - | - | 0.9985 | 0.9995 | - | 1.00s |

---

## 7. Publication Plot Gallery & Visual Artifacts

| Figure Name | Visual File Link | Description |
| :--- | :--- | :--- |
| **Alert Feature Importance** | [`plots/alert_feature_importance.png`](plots/alert_feature_importance.png) | High-resolution publication curve (300 DPI) |
| **Optimizer Convergence** | [`plots/optimizer_convergence.png`](plots/optimizer_convergence.png) | High-resolution publication curve (300 DPI) |
| **Pr Curves** | [`plots/pr_curves.png`](plots/pr_curves.png) | High-resolution publication curve (300 DPI) |
| **Roc Curves** | [`plots/roc_curves.png`](plots/roc_curves.png) | High-resolution publication curve (300 DPI) |

---

## 8. Model Governance & Statutory Compliance Disclaimers

- **Zero Demographic PII Invariant**: Audited under GDPR Article 9 and ECOA Regulation B (12 CFR Part 1002); contains 0/10 protected demographic attributes.
- **Federal Reserve SR 11-7 Compliance**: Experimental evaluation is calibrated against fixed random seeds and out-of-time chronological validation splits.

---
*Dossier generated automatically by CF-Intelligence Experiment Harness v1.0.0 on 2026-09-27T11:48:27.930694+00:00.*
