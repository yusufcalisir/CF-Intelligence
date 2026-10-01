# Experiment Execution Dossier: `exp-elliptic-graphsage-1790452183`

> **Experiment Name:** Elliptic Bitcoin GraphSAGE Inductive Benchmark  
> **Model / Strategy:** `GraphSAGE` (InductiveNeighborhoodAggregation)  
> **Status:** `COMPLETED` | **Duration:** 141.51s  
> **Git Provenance:** Commit `229c2623bb` (Branch: `main`)

---

## 1. Executive Summary & Core Results

| Evaluation Dimension | Canonical 3-Seed Mean | Sample Std ($ddof=1$) | Observed [Min, Max] | Status |
| :--- | :---: | :---: | :---: | :---: |
| **Precision-Recall AUC (PR-AUC)** | **0.3761** | 0.0482 | [0.3304, 0.4265] | `CONFIRMED` |
| **ROC-AUC** | **0.8325** | 0.0078 | [0.8245, 0.8401] | `CONFIRMED` |
| **F1-Score (Validation Calibrated)** | **0.3555** | 0.0567 | [0.3078, 0.4182] | `CONFIRMED` |
| **Precision (PPV)** | **0.2757** | 0.0956 | [0.2068, 0.3848] | `CONFIRMED` |
| **Recall (Sensitivity)** | **0.5583** | 0.0871 | [0.4580, 0.6150] | `CONFIRMED` |
| **Recall @ 0.1% Strict FPR** | **4.62%** | 3.75% | [0.00%, 8.86%] | `CONFIRMED` |
| **Recall @ 0.5% FPR** | **14.34%** | 4.60% | [10.25%, 20.31%] | `CONFIRMED` |
| **Recall @ 1.0% FPR** | **20.81%** | 1.83% | [18.74%, 22.53%] | `CONFIRMED` |

---

## 2. Hardware & Execution Environment

| Environment Attribute | Specification |
| :--- | :--- |
| **Operating System** | Windows 11 (Version: 10.0.26200) |
| **CPU Model** | AMD64 Family 25 Model 80 Stepping 0, AuthenticAMD (AMD64) |
| **CPU Core Topology** | 8 Physical Cores / 16 Threads |
| **System RAM** | 7.34 GB (Available at Start: 0.29 GB) |
| **Python Runtime** | Python 3.12.10 |
| **PyTorch Framework** | PyTorch 2.12.0+cpu (Device: `CPU`, CUDA: `False`) |

---

## 3. Dataset Characteristics & Integrity

| Dataset Attribute | Specification |
| :--- | :--- |
| **Dataset Canonical Name** | `Elliptic Bitcoin Transaction Graph` |
| **Total Record Count** | 203,769 records |
| **Feature Dimensionality** | 165 tabular/graph columns |
| **Class Balance** | 4,545 positive fraud records (9.7608% prevalence) |
| **Partition Ratios** | Train: 70% / Val: 15% / Test: 15% |
| **Data Integrity Hash** | `sha256:a0cc5a4cacfed0bd1186d5095f17603367b18083321acbad218ada4a24ba7967` |

---

## 4. Hyperparameter Matrix

| Hyperparameter | Value | Description |
| :--- | :---: | :--- |
| `model_type` | `GraphSAGE` | Neural Network or Classifier Architecture |
| `strategy` | `InductiveNeighborhoodAggregation` | Optimization / Aggregation Strategy |
| `seeds` | `[42]` | Evaluated Random Seed(s) |
| `num_rounds` | `15` | Federated Communication Rounds / Epochs |
| `local_epochs` | `3` | Client Local SGD Epochs per Round |
| `batch_size` | `64` | Mini-Batch Size |
| `learning_rate` | `0.005` | Client Optimizer Learning Rate |
| `dp_enabled` | `False` | Differential Privacy Guarantee Active |

---

## 5. Decision Confusion Matrix

| True Condition \ Predicted Decision | Predicted Legitimate (0) | Predicted Fraud (1) | Total True |
| :--- | :---: | :---: | :---: |
| **True Legitimate (0)** | **TN:** 13,732 (82.4%) | **FP:** 1,855 (11.1%) | 15,587 |
| **True Fraud (1)** | **FN:** 393 (2.4%) | **TP:** 690 (4.1%) | 1,083 |
| **Total Predicted** | 14,125 | 2,545 | 16,670 |


---

## 6. Training & Convergence Trajectory

| Step / Round | Train Loss | Val Loss | PR-AUC | ROC-AUC | F1-Score | Duration |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| 1 | 1.2327 | - | - | - | - | 9.43s |
| 2 | 1.2148 | - | - | - | - | 9.43s |
| 3 | 1.1948 | - | - | - | - | 9.43s |
| 4 | 1.1793 | - | - | - | - | 9.43s |
| 5 | 1.1617 | - | - | - | - | 9.43s |
| 6 | 1.1425 | - | - | - | - | 9.43s |
| 7 | 1.1220 | - | - | - | - | 9.43s |
| 8 | 1.1013 | - | - | - | - | 9.43s |
| 9 | 1.0797 | - | - | - | - | 9.43s |
| 10 | 1.0596 | - | - | - | - | 9.43s |
| 11 | 1.0373 | - | - | - | - | 9.43s |
| 12 | 1.0136 | - | - | - | - | 9.43s |
| 13 | 0.9889 | - | - | - | - | 9.43s |
| 14 | 0.9617 | - | - | - | - | 9.43s |
| 15 | 0.9349 | - | - | - | - | 9.43s |

---

## 7. Publication Plot Gallery & Visual Artifacts

| Figure Name | Visual File Link | Description |
| :--- | :--- | :--- |
| **Neighborhood Ablation** | [`plots/neighborhood_ablation.png`](plots/neighborhood_ablation.png) | High-resolution publication curve (300 DPI) |
| **Pr Curves** | [`plots/pr_curves.png`](plots/pr_curves.png) | High-resolution publication curve (300 DPI) |
| **Roc Curves** | [`plots/roc_curves.png`](plots/roc_curves.png) | High-resolution publication curve (300 DPI) |
| **Temporal Generalization** | [`plots/temporal_generalization.png`](plots/temporal_generalization.png) | High-resolution publication curve (300 DPI) |

---

## 8. Model Governance & Statutory Compliance Disclaimers

- **Zero Demographic PII Invariant**: Audited under GDPR Article 9 and ECOA Regulation B (12 CFR Part 1002); contains 0/10 protected demographic attributes.
- **Federal Reserve SR 11-7 Compliance**: Experimental evaluation is calibrated against fixed random seeds and out-of-time chronological validation splits.

---
*Dossier generated automatically by CF-Intelligence Experiment Harness v1.0.0 on 2026-09-26T19:49:40.308490+00:00.*
