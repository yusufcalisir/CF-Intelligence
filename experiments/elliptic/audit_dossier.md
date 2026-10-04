# Elliptic Bitcoin GraphSAGE Inductive Benchmark Audit Dossier

> **Dataset**: Elliptic Bitcoin Transaction Graph (Weber et al., 2019)  
> **Execution Timestamp**: `2026-10-01T08:14:05.791673+00:00`  
> **Git Commit**: `2a11c237894de1f20812c6ad4903d4333966f044`  
> **Temporal Invariant**: Strict chronological split (Timesteps 1-30 Train, 31-34 Val, 35-49 Test)  
> **Hardware**: AMD64 Family 25 Model 80 Stepping 0, AuthenticAMD (16 vCPUs), 7.3 GB RAM, Windows 11  

---

## 1. Executive Summary & Comparative Matrix

| Evaluation Paradigm | PR-AUC | ROC-AUC | Recall @ 0.1% FPR | Recall @ 0.5% FPR | F1-Score | Brier Score | Latency (ms/1k) | $\Delta$ PR-AUC vs Tabular |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Inductive GraphSAGE (2-Layer Mean Aggregator)** | `0.4265` | `0.8401` | `0.0886` | `0.2031` | `0.3405` | `0.1649` | `4.95` ms | **-0.1513** |
| **Inductive GraphSAGE (1-Layer Mean Aggregator)** | `0.4556` | `0.8368` | `0.1293` | `0.2225` | `0.3992` | `0.2394` | `2.52` ms | **-0.1223** |
| **Tabular MLP Baseline (No Graph / 0-Hop)** | `0.5778` | `0.8722` | `0.2521` | `0.3897` | `0.5162` | `0.1720` | `0.83` ms | **Baseline (0.0)** |
| **GraphSAGE (GCN Symmetric Aggregator)** | `0.4922` | `0.8504` | `0.1043` | `0.2862` | `0.3845` | `0.1524` | `4.20` ms | **-0.0856** |

---

## 2. Exploratory Neighborhood Hop Ablation (Single-Seed Diagnostic)

In exploratory single-seed ablations, 2-layer GraphSAGE was evaluated alongside 1-layer and tabular configurations under identical temporal partitions.
This comparison does not establish an empirical causal mechanism for performance variations across topologies and is not used for canonical model selection:

```
0-Hop (Tabular MLP Baseline)       PR-AUC: 0.5778  |  ROC-AUC: 0.8722  |  Recall@0.1%FPR: 0.2521
1-Hop (GraphSAGE Immediate Neigh)   PR-AUC: 0.4556  |  ROC-AUC: 0.8368  |  Recall@0.1%FPR: 0.1293
2-Hop (GraphSAGE Full Champion)    PR-AUC: 0.4265  |  ROC-AUC: 0.8401  |  Recall@0.1%FPR: 0.0886
```

- **Neighborhood Aggregation Uplift ($\Delta$ PR-AUC)**: `-0.1513`
- **Discriminative Separation Uplift ($\Delta$ ROC-AUC)**: `-0.0321`
- **Low-FPR Operational Safety Uplift ($\Delta$ Recall @ 0.1% FPR)**: `-0.1634`

### Aggregator Function Invariants

- **Mean Aggregator**: PR-AUC `0.4265`, ROC-AUC `0.8401`.
- **GCN Symmetric Aggregator**: PR-AUC `0.4922`, ROC-AUC `0.8504`.

---

## 3. Dataset Characteristics & Partition Invariants

- **Total Nodes**: 203,769 Bitcoin transactions
- **Feature Dimension**: 165 dimensions (local + precomputed flow aggregates)
- **Train Partition (Timesteps 1-34)**: 34 timesteps, strict temporal ceiling
- **Test Partition (Timesteps 35-49)**: 15 out-of-time test timesteps, evaluating inductive generalizability

---

## 4. Empirical Confusion Matrix (GraphSAGE 2-Layer)

- **True Negatives (TN)**: `13,424` (Correctly identified licit Bitcoin transactions)
- **False Positives (FP)**: `2,163` (Benign transactions flagged as suspicious)
- **False Negatives (FN)**: `417` (Missed illicit transactions)
- **True Positives (TP)**: `666` (Successfully intercepted money laundering transactions)

---

## 5. Scientific Artifact Traceability

- **Result Payload**: [`results.json`](results.json)
- **Comparative Baselines**: [`comparative_baselines.json`](comparative_baselines.json)
- **Raw Benchmark**: [`graphsage_elliptic_benchmark.json`](graphsage_elliptic_benchmark.json)
- **Consolidated Figure**: [`benchmark_graphsage_elliptic.png`](plots\benchmark_graphsage_elliptic.png)

