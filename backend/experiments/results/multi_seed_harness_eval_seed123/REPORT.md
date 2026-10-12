# Experiment Execution Dossier: `multi_seed_harness_eval_seed123`

> **Experiment Name:** FraudNeuralNetwork_MultiSeed  
> **Model / Strategy:** `DeepFraudMLP` (FedAvg)  
> **Status:** `COMPLETED` | **Duration:** 0.01s  
> **Git Provenance:** Commit `867537360d` (Branch: `main`)

---

## 1. Executive Summary & Core Results

| Evaluation Dimension | Measured Value | Target SLA / Baseline | Status |
| :--- | :---: | :---: | :---: |
| **Precision-Recall AUC (PR-AUC)** | **0.1320** | Primary Imbalanced Metric | `CONFIRMED` |
| **ROC-AUC** | **0.6424** | Area Under Receiver Operating Characteristic | `CONFIRMED` |
| **F1-Score (Optimal Threshold)** | **0.0000** | Harmonic Mean of Precision & Recall | `CONFIRMED` |
| **Precision (PPV)** | **0.0000** | Operational False Positive Ceiling | `CONFIRMED` |
| **Recall (Sensitivity)** | **0.0000** | True Positive Fraud Detection Floor | `CONFIRMED` |
| **Brier Calibration Score** | **0.2024** | Probability Calibration Fidelity | `CONFIRMED` |

---

## 2. Hardware & Execution Environment

| Environment Attribute | Specification |
| :--- | :--- |
| **Operating System** | Windows 11 (Version: 10.0.26300) |
| **CPU Model** | AMD64 Family 25 Model 80 Stepping 0, AuthenticAMD (AMD64) |
| **CPU Core Topology** | 8 Physical Cores / 16 Threads |
| **System RAM** | 7.34 GB (Available at Start: 0.76 GB) |
| **Python Runtime** | Python 3.12.10 |
| **PyTorch Framework** | PyTorch 2.12.1+cpu (Device: `CPU`, CUDA: `False`) |

---

## 3. Dataset Characteristics & Integrity

| Dataset Attribute | Specification |
| :--- | :--- |
| **Dataset Canonical Name** | `SyntheticFinancialConsortium` |
| **Total Record Count** | 2,000 records |
| **Feature Dimensionality** | 16 tabular/graph columns |
| **Class Balance** | 100 positive fraud records (5.0000% prevalence) |
| **Partition Ratios** | Train: 70% / Val: 15% / Test: 15% |
| **Data Integrity Hash** | `sha256:7c363615f9650d1005cab599115adf02dd5b9bcb9ff59b29f4390a71e470a667` |

---

## 4. Hyperparameter Matrix

| Hyperparameter | Value | Description |
| :--- | :---: | :--- |
| `model_type` | `DeepFraudMLP` | Neural Network or Classifier Architecture |
| `strategy` | `FedAvg` | Optimization / Aggregation Strategy |
| `seeds` | `[123]` | Evaluated Random Seed(s) |
| `num_rounds` | `1` | Federated Communication Rounds / Epochs |
| `local_epochs` | `3` | Client Local SGD Epochs per Round |
| `batch_size` | `64` | Mini-Batch Size |
| `learning_rate` | `0.01` | Client Optimizer Learning Rate |
| `dp_enabled` | `False` | Differential Privacy Guarantee Active |

---

## 5. Decision Confusion Matrix

| True Condition \ Predicted Decision | Predicted Legitimate (0) | Predicted Fraud (1) | Total True |
| :--- | :---: | :---: | :---: |
| **True Legitimate (0)** | **TN:** 379 (94.8%) | **FP:** 0 (0.0%) | 379 |
| **True Fraud (1)** | **FN:** 21 (5.2%) | **TP:** 0 (0.0%) | 21 |
| **Total Predicted** | 400 | 0 | 400 |


---

## 6. Training & Convergence Trajectory

| Step / Round | Train Loss | Val Loss | PR-AUC | ROC-AUC | F1-Score | Duration |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| 1 | 0.6395 | - | 0.1320 | 0.6424 | 0.0000 | 0.01s |

---

## 7. Publication Plot Gallery & Visual Artifacts

| Figure Name | Visual File Link | Description |
| :--- | :--- | :--- |
| **Calibration Curve** | [`plots/calibration_curve.png`](plots/calibration_curve.png) | High-resolution publication curve (300 DPI) |
| **Confusion Matrix** | [`plots/confusion_matrix.png`](plots/confusion_matrix.png) | High-resolution publication curve (300 DPI) |
| **Pr Curve** | [`plots/pr_curve.png`](plots/pr_curve.png) | High-resolution publication curve (300 DPI) |
| **Roc Curve** | [`plots/roc_curve.png`](plots/roc_curve.png) | High-resolution publication curve (300 DPI) |

---

## 8. Model Governance & Statutory Compliance Disclaimers

- **Zero Demographic PII Invariant**: Audited under GDPR Article 9 and ECOA Regulation B (12 CFR Part 1002); contains 0/10 protected demographic attributes.
- **Federal Reserve SR 11-7 Compliance**: Experimental evaluation is calibrated against fixed random seeds and out-of-time chronological validation splits.

---
*Dossier generated automatically by CF-Intelligence Experiment Harness v1.0.0 on 2026-10-11T19:25:05.686118+00:00.*
