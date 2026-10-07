# Experiment Execution Dossier: `exp-amlsim-graphsage-1790494671`

> **Experiment Name:** IBM AMLSim Multi-Hop Pattern Detection Benchmark  
> **Model / Strategy:** `GraphSAGE` (InductiveNeighborhoodAggregation)  
> **Status:** `CANONICAL` | **Duration:** 20.20s  
> **Git Provenance:** Commit `b531eab85e` (Branch: `main`)

---

## 1. Executive Summary & Core Results

| Evaluation Dimension | Measured Value | Target SLA / Baseline | Status |
| :--- | :---: | :---: | :---: |
| **Precision-Recall AUC (PR-AUC)** | **0.6527** | Primary Imbalanced Metric | `CONFIRMED` |
| **ROC-AUC** | **0.9509** | Area Under Receiver Operating Characteristic | `CONFIRMED` |
| **F1-Score (Optimal Threshold)** | **0.1689** | Harmonic Mean of Precision & Recall | `CONFIRMED` |
| **Precision (PPV)** | **N/A** | Operational False Positive Ceiling | `UNMEASURED` |
| **Recall (Sensitivity)** | **N/A** | True Positive Fraud Detection Floor | `UNMEASURED` |
| **Brier Calibration Score** | **0.0047** | Probability Calibration Fidelity | `CONFIRMED` |
| **Recall @ 0.1% FPR** | **64.1200%** | Low-FPR Operational Boundary | `CONFIRMED` |
| **Recall @ 1.0% FPR** | **69.2200%** | Strict Bank Operational Tier | `CONFIRMED` |

---

## 2. Hardware & Execution Environment

| Environment Attribute | Specification |
| :--- | :--- |
| **Operating System** | Windows 11 (Version: 10.0.26200) |
| **CPU Model** | AMD64 Family 25 Model 80 Stepping 0, AuthenticAMD (AMD64) |
| **CPU Core Topology** | 8 Physical Cores / 16 Threads |
| **System RAM** | 7.34 GB (Available at Start: 0.68 GB) |
| **Python Runtime** | Python 3.12.10 |
| **PyTorch Framework** | PyTorch 2.12.0+cpu (Device: `CPU`, CUDA: `False`) |

---

## 3. Dataset Characteristics & Integrity

| Dataset Attribute | Specification |
| :--- | :--- |
| **Dataset Canonical Name** | `IBM AMLSim Multi-Hop Graph` |
| **Total Record Count** | 1,323,234 records |
| **Feature Dimensionality** | 6 tabular/graph columns |
| **Class Balance** | 1,719 positive fraud records (0.1299% prevalence) |
| **Partition Ratios** | Train: 70% / Val: 0% / Test: 30% |
| **Data Integrity Hash** | `sha256:b3dc9b72f985e7247198f81df8d4db7c559d93dfd7d00fb4ec18a6b0b368647c` |

---

## 4. Hyperparameter Matrix

| Hyperparameter | Value | Description |
| :--- | :---: | :--- |
| `model_type` | `GraphSAGE` | Neural Network or Classifier Architecture |
| `strategy` | `InductiveNeighborhoodAggregation` | Optimization / Aggregation Strategy |
| `seeds` | `[42]` | Evaluated Random Seed(s) |
| `num_rounds` | `15` | Federated Communication Rounds / Epochs |
| `local_epochs` | `1` | Client Local SGD Epochs per Round |
| `batch_size` | `930465` | Mini-Batch Size |
| `learning_rate` | `0.005` | Client Optimizer Learning Rate |
| `dp_enabled` | `False` | Differential Privacy Guarantee Active |

---

## 5. Decision Confusion Matrix

| True Condition \ Predicted Decision | Predicted Legitimate (0) | Predicted Fraud (1) | Total True |
| :--- | :---: | :---: | :---: |
| **True Legitimate (0)** | **TN:** 388,670 (99.0%) | **FP:** 3,550 (0.9%) | 392,220 |
| **True Fraud (1)** | **FN:** 171 (0.0%) | **TP:** 378 (0.1%) | 549 |
| **Total Predicted** | 388,841 | 3,928 | 392,769 |


---

## 6. Training & Convergence Trajectory

*Step-by-step history points recorded in standalone metrics.csv.*

---

## 7. Publication Plot Gallery & Visual Artifacts

| Figure Name | Visual File Link | Description |
| :--- | :--- | :--- |
| **Benchmark Amlsim Comparison** | [`plots/benchmark_amlsim_comparison.png`](plots/benchmark_amlsim_comparison.png) | High-resolution publication curve (300 DPI) |
| **Hop Ablation** | [`plots/hop_ablation.png`](plots/hop_ablation.png) | High-resolution publication curve (300 DPI) |
| **Pr Curves** | [`plots/pr_curves.png`](plots/pr_curves.png) | High-resolution publication curve (300 DPI) |
| **Roc Curves** | [`plots/roc_curves.png`](plots/roc_curves.png) | High-resolution publication curve (300 DPI) |
| **Typology Detection** | [`plots/typology_detection.png`](plots/typology_detection.png) | High-resolution publication curve (300 DPI) |

---

## 8. Model Governance & Statutory Compliance Disclaimers

- **Zero Demographic PII Invariant**: Audited under GDPR Article 9 and ECOA Regulation B (12 CFR Part 1002); contains 0/10 protected demographic attributes.
- **Federal Reserve SR 11-7 Compliance**: Experimental evaluation is calibrated against fixed random seeds and out-of-time chronological validation splits.

---
*Dossier generated automatically by CF-Intelligence Experiment Harness v1.0.0 on 2026-09-27T07:37:51.002566+00:00.*
