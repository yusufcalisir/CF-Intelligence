# Elliptic Bitcoin GraphSAGE Inductive Benchmark Audit Dossier

> **Dataset**: Elliptic Bitcoin Transaction Graph (Weber et al., 2019)  
> **Execution Timestamp**: `2026-09-26T19:49:40.308490+00:00`  
> **Git Commit**: `229c2623bbafc042d625b320cae770b9de2c97fd`  
> **Temporal Invariant**: Strict past-to-future split (Timesteps 1-34 Train vs 35-49 Test)  
> **Hardware**: AMD64 Family 25 Model 80 Stepping 0, AuthenticAMD (16 vCPUs), 7.3 GB RAM, Windows 11  

---

## 1. Executive Summary & Comparative Matrix

| Evaluation Paradigm | PR-AUC | ROC-AUC | Recall @ 0.1% FPR | Recall @ 0.5% FPR | F1-Score | Brier Score | Latency (ms/1k) | $\Delta$ PR-AUC vs Tabular |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Inductive GraphSAGE (2-Layer Mean Aggregator)** | `0.4372` | `0.8388` | `0.1320` | `0.1828` | `0.3804` | `0.1916` | `6.16` ms | **-0.0229** |
| **Inductive GraphSAGE (1-Layer Mean Aggregator)** | `0.4604` | `0.8430` | `0.1496` | `0.2909` | `0.2739` | `0.1933` | `2.93` ms | **+0.0003** |
| **Tabular MLP Baseline (No Graph / 0-Hop)** | `0.4602` | `0.8613` | `0.0822` | `0.2041` | `0.3580` | `0.1143` | `1.44` ms | **Baseline (0.0)** |
| **GraphSAGE (GCN Symmetric Aggregator)** | `0.4655` | `0.8337` | `0.0785` | `0.2844` | `0.3587` | `0.1912` | `5.71` ms | **+0.0053** |

---

## 2. Controlled Neighborhood Hop Ablation Analysis

To isolate the exact causal impact of graph topology from raw node features:

```
0-Hop (Tabular MLP Baseline)       PR-AUC: 0.4602  |  ROC-AUC: 0.8613  |  Recall@0.1%FPR: 0.0822
1-Hop (GraphSAGE Immediate Neigh)   PR-AUC: 0.4604  |  ROC-AUC: 0.8430  |  Recall@0.1%FPR: 0.1496
2-Hop (GraphSAGE Full Champion)    PR-AUC: 0.4372  |  ROC-AUC: 0.8388  |  Recall@0.1%FPR: 0.1320
```

- **Neighborhood Aggregation Uplift ($\Delta$ PR-AUC)**: `-0.0229`
- **Discriminative Separation Uplift ($\Delta$ ROC-AUC)**: `-0.0225`
- **Low-FPR Operational Safety Uplift ($\Delta$ Recall @ 0.1% FPR)**: `+0.0499`

### Aggregator Function Invariants

- **Mean Aggregator**: PR-AUC `0.4372`, ROC-AUC `0.8388`.
- **GCN Symmetric Aggregator**: PR-AUC `0.4655`, ROC-AUC `0.8337`.

---

## 3. Dataset Characteristics & Partition Invariants

- **Total Nodes**: 203,769 Bitcoin transactions
- **Feature Dimension**: 165 dimensions (local + precomputed flow aggregates)
- **Train Partition (Timesteps 1-34)**: 34 timesteps, strict temporal ceiling
- **Test Partition (Timesteps 35-49)**: 15 out-of-time test timesteps, evaluating inductive generalizability

---

## 4. Empirical Confusion Matrix (GraphSAGE 2-Layer)

- **True Negatives (TN)**: `13,732` (Correctly identified licit Bitcoin transactions)
- **False Positives (FP)**: `1,855` (Benign transactions flagged as suspicious)
- **False Negatives (FN)**: `393` (Missed illicit transactions)
- **True Positives (TP)**: `690` (Successfully intercepted money laundering transactions)

---

## 5. Scientific Artifact Traceability

- **Result Payload**: [`results.json`](file:///C:\Users\Yusuf\Desktop\projects\Privacy-preserving cross-bank fraud detection using Federated Learning\experiments\elliptic\results.json)
- **Comparative Baselines**: [`comparative_baselines.json`](file:///C:\Users\Yusuf\Desktop\projects\Privacy-preserving cross-bank fraud detection using Federated Learning\experiments\elliptic\comparative_baselines.json)
- **Raw Benchmark**: [`graphsage_elliptic_benchmark.json`](file:///C:\Users\Yusuf\Desktop\projects\Privacy-preserving cross-bank fraud detection using Federated Learning\benchmarks\results\raw\graphsage_elliptic_benchmark.json)
- **Consolidated Figure**: [`benchmark_graphsage_elliptic.png`](file:///C:\Users\Yusuf\Desktop\projects\Privacy-preserving cross-bank fraud detection using Federated Learning\docs\figures\benchmark_graphsage_elliptic.png)

