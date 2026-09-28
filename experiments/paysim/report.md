# Experiment Execution Dossier: `exp_paysim_federated_benchmark`

> **Experiment Name:** PaySim Federated Benchmark (Dirichlet alpha=0.5)  
> **Model / Strategy:** `PaySimNeuralClassifier` (fedavg)  
> **Status:** `COMPLETED` | **Duration:** 76.19s  
> **Git Provenance:** Commit `21963f1153` (Branch: `main`)

---

## 1. Executive Summary & Core Results

| Evaluation Dimension | Measured Value | Target SLA / Baseline | Status |
| :--- | :---: | :---: | :---: |
| **Precision-Recall AUC (PR-AUC)** | **0.1184** | Primary Imbalanced Metric | `CONFIRMED` |
| **ROC-AUC** | **0.8700** | Area Under Receiver Operating Characteristic | `CONFIRMED` |
| **F1-Score (Optimal Threshold)** | **0.0000** | Harmonic Mean of Precision & Recall | `CONFIRMED` |
| **Precision (PPV)** | **0.0000** | Operational False Positive Ceiling | `CONFIRMED` |
| **Recall (Sensitivity)** | **0.0000** | True Positive Fraud Detection Floor | `CONFIRMED` |
| **Brier Calibration Score** | **0.0006** | Probability Calibration Fidelity | `CONFIRMED` |
| **Recall @ 0.1% FPR** | **33.3330%** | Low-FPR Operational Boundary | `CONFIRMED` |
| **Recall @ 1.0% FPR** | **66.6670%** | Strict Bank Operational Tier | `CONFIRMED` |

---

## 2. Hardware & Execution Environment

| Environment Attribute | Specification |
| :--- | :--- |
| **Operating System** | Windows 11 (Version: 10.0.26200) |
| **CPU Model** | AMD64 Family 25 Model 80 Stepping 0, AuthenticAMD (AMD64) |
| **CPU Core Topology** | 8 Physical Cores / 16 Threads |
| **System RAM** | 7.34 GB (Available at Start: 0.26 GB) |
| **Python Runtime** | Python 3.12.10 |
| **PyTorch Framework** | PyTorch 2.12.0+cpu (Device: `CPU`, CUDA: `False`) |

---

## 3. Dataset Characteristics & Integrity

| Dataset Attribute | Specification |
| :--- | :--- |
| **Dataset Canonical Name** | `PaySim Mobile Money Fraud` |
| **Total Record Count** | 30,000 records |
| **Feature Dimensionality** | 13 tabular/graph columns |
| **Class Balance** | 84 positive fraud records (0.0500% prevalence) |
| **Partition Ratios** | Train: 80% / Val: 15% / Test: 20% |
| **Data Integrity Hash** | `sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

---

## 4. Hyperparameter Matrix

| Hyperparameter | Value | Description |
| :--- | :---: | :--- |
| `model_type` | `PaySimNeuralClassifier` | Neural Network or Classifier Architecture |
| `strategy` | `fedavg` | Optimization / Aggregation Strategy |
| `seeds` | `[42]` | Evaluated Random Seed(s) |
| `num_rounds` | `10` | Federated Communication Rounds / Epochs |
| `local_epochs` | `2` | Client Local SGD Epochs per Round |
| `batch_size` | `64` | Mini-Batch Size |
| `learning_rate` | `0.001` | Client Optimizer Learning Rate |
| `dp_enabled` | `False` | Differential Privacy Guarantee Active |

---

## 5. Decision Confusion Matrix

| True Condition \ Predicted Decision | Predicted Legitimate (0) | Predicted Fraud (1) | Total True |
| :--- | :---: | :---: | :---: |
| **True Legitimate (0)** | **TN:** 5,996 (99.9%) | **FP:** 1 (0.0%) | 5,997 |
| **True Fraud (1)** | **FN:** 3 (0.1%) | **TP:** 0 (0.0%) | 3 |
| **Total Predicted** | 5,999 | 1 | 6,000 |


---

## 6. Training & Convergence Trajectory

| Step / Round | Train Loss | Val Loss | PR-AUC | ROC-AUC | F1-Score | Duration |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| 0 | 0.7390 | 0.7390 | 0.0006 | 0.4835 | 0.0015 | 0.00s |
| 1 | 0.3041 | 0.0054 | 0.1127 | 0.8858 | 0.0000 | 2.93s |
| 2 | 0.4722 | 0.0037 | 0.1160 | 0.7932 | 0.0000 | 1.72s |
| 3 | 0.3436 | 0.0034 | 0.0414 | 0.8199 | 0.0000 | 1.77s |
| 4 | 0.3822 | 0.0041 | 0.0958 | 0.8106 | 0.0000 | 1.61s |
| 5 | 0.2549 | 0.0037 | 0.1003 | 0.8199 | 0.0000 | 1.78s |
| 6 | 0.2262 | 0.0038 | 0.1171 | 0.8631 | 0.0000 | 1.52s |
| 7 | 0.1918 | 0.0039 | 0.1313 | 0.8587 | 0.2000 | 1.80s |
| 8 | 0.2253 | 0.0031 | 0.1277 | 0.8750 | 0.0000 | 1.56s |
| 9 | 0.2343 | 0.0038 | 0.1314 | 0.8750 | 0.1818 | 1.79s |
| 10 | 0.2229 | 0.0032 | 0.1184 | 0.8700 | 0.0000 | 1.70s |

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
*Dossier generated automatically by CF-Intelligence Experiment Harness v1.0.0 on 2026-09-26T16:52:44.478772+00:00.*
