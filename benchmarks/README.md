# CF-Intelligence Empirical Benchmarking Suite

This directory contains the reproducible benchmark runners, dataset preprocessing pipelines, evaluation protocols, and machine-readable result artifacts for **CF-Intelligence**.

---

## 1. Architectural Scope & Evaluation Pillars

The benchmark suite provides reproducible quantitative evaluation across five core engineering pillars:

| Evaluation Pillar | Script Runner | Target Datasets / Testbeds | Primary Metrics |
|:---|:---|:---|:---|
| **Fraud Detection Performance** | `benchmarks/runners/run_fraud_benchmark.py` | PaySim, IEEE-CIS | PR-AUC, ROC-AUC, Recall @ 0.1% / 0.5% / 1% FPR |
| **Federated Optimization** | `benchmarks/runners/run_fl_benchmark.py` | Dirichlet $\mathrm{Dir}(\alpha)$ Skew | Convergence Rounds, Communication MB, PR-AUC |
| **Differential Privacy Tradeoff** | `benchmarks/runners/run_dp_tradeoff.py` | Subsampled Gaussian / RDP | $\epsilon$ vs PR-AUC Frontier, Clipping Norm $C$ |
| **Byzantine Adversarial Defense** | `benchmarks/runners/run_byzantine_benchmark.py` | Sign Inversion, Noise | Robust PR-AUC (Krum, Trimmed Mean, Bulyan) |
| **Graph Intelligence (GraphSAGE)**| `benchmarks/runners/run_graph_benchmark.py` | Elliptic Bitcoin Graph | Node PR-AUC, ROC-AUC, Temporal Split (Timestep 34) |
| **Inference Gateway Latency** | `benchmarks/runners/run_latency_benchmark.py` | HTTP Concurrency $C \in [1, 500]$ | p50, p95, p99 Latency (ms), Throughput (req/s) |

---

## 2. Dataset Acquisition & Preprocessing

Due to dataset licensing policies (Kaggle Competition Rules, CC BY-NC 4.0), raw datasets are never checked into git. Automated preprocessors convert raw CSVs into zero-leakage, Non-IID federated client partitions:

### 2.1 PaySim Mobile Money Dataset
- **Acquisition**: Follow [`benchmarks/datasets/paysim/download_instructions.md`](./datasets/paysim/download_instructions.md).
- **Validation**:
  ```bash
  python benchmarks/datasets/paysim/validate.py
  ```
- **Partitioning**:
  ```bash
  python benchmarks/datasets/paysim/preprocess.py --alpha 0.5 --clients 5 --limit 50000
  ```

### 2.2 IEEE-CIS Fraud Detection Dataset
- **Acquisition**: Follow [`benchmarks/datasets/ieee_cis/download_instructions.md`](./datasets/ieee_cis/download_instructions.md).
- **Validation**:
  ```bash
  python benchmarks/datasets/ieee_cis/validate.py
  ```
- **Partitioning**:
  ```bash
  python benchmarks/datasets/ieee_cis/preprocess.py --alpha 0.5 --clients 5 --limit 25000
  ```

### 2.3 Elliptic Bitcoin Transaction Graph Dataset
- **Acquisition**: Follow [`benchmarks/datasets/elliptic/download_instructions.md`](./datasets/elliptic/download_instructions.md).
- **Validation**:
  ```bash
  python benchmarks/datasets/elliptic/validate.py
  ```
- **Temporal Zero-Leakage Preprocessing**:
  ```bash
  python benchmarks/datasets/elliptic/preprocess.py --split-step 34
  ```

---

## 3. Benchmark Reproduction Commands

All runners support synthetic evaluation out-of-the-box when raw Kaggle datasets are not yet downloaded:

```bash
# 1. Run Fraud Detection Benchmark (PaySim)
python benchmarks/runners/run_fraud_benchmark.py --dataset paysim --rounds 10

# 2. Run FL Optimization Benchmark (Non-IID Dirichlet Skew alpha=0.5)
python benchmarks/runners/run_fl_benchmark.py --rounds 10 --alpha 0.5

# 3. Run Differential Privacy Utility Frontier Sweep
python benchmarks/runners/run_dp_tradeoff.py

# 4. Run Byzantine Adversarial Defense Benchmark (Sign Inversion Attack)
python benchmarks/runners/run_byzantine_benchmark.py --attack sign_inversion --byzantine 2

# 5. Run GraphSAGE Node Classification Benchmark
python benchmarks/runners/run_graph_benchmark.py

# 6. Run Real-Time Gateway Concurrency Stress Test
python benchmarks/runners/run_latency_benchmark.py --workers 50
```

---

## 4. Operational Metric Definitions

Fraud detection suffers from severe class imbalance ($< 0.5\%$ positive labels). ROC-AUC is known to be overly optimistic because large True Negative counts mask hundreds of false alarms. Therefore, this platform prioritizes:

### Precision-Recall AUC (PR-AUC)
$$\mathrm{PR\text{-}AUC} = \sum_{k=1}^{n} (R_k - R_{k-1}) P_k$$
Directly quantifies the tradeoff between catching fraud (Recall) and minimizing false customer alert friction (Precision).

### Operational Recall @ Fixed False Positive Rates (FPR)
$$\mathrm{Recall@0.1\%FPR}, \quad \mathrm{Recall@0.5\%FPR}, \quad \mathrm{Recall@1.0\%FPR}$$
- **0.1% FPR**: The threshold below which transactions can be automatically declined without manual review.
- **0.5% FPR**: The operational budget for SMS / 2FA step-up challenges.
- **1.0% FPR**: Maximum load feasible for human AML compliance analyst queues.

---

## 5. Machine-Readable Results Schema

All benchmark executions output deterministic JSON artifacts to `benchmarks/results/raw/`:

```
benchmarks/results/
├── raw/
│   ├── fraud_benchmark_paysim.json
│   ├── fl_comparison_alpha_0.5.json
│   ├── dp_privacy_utility_tradeoff.json
│   ├── byzantine_benchmark_sign_inversion.json
│   ├── graphsage_elliptic_benchmark.json
│   └── latency_concurrency_benchmark.json
└── summary.md
```

### Environmental Metadata Transparency
Every generated artifact records host machine specifications (CPU model, total RAM, OS kernel, Python version, PyTorch version, timestamp UTC) to ensure reproducible comparisons across heterogeneous hardware.

---

## 6. Scientific Limitations

1. **Synthetic and Public Proxy Datasets**: Real inter-bank clearing feeds (SWIFT MT/pacs, Fedwire) contain proprietary transaction topologies not fully mirrored in public datasets.
2. **In-Process Simulation**: Multi-client benchmarks execute as concurrent threads or processes on a single physical host; inter-datacenter WAN packet latency ($30\text{--}80\mathrm{ms}$) is modeled analytically.
3. **Differential Privacy Degradation**: Under strict privacy budgets ($\epsilon \le 1.0$), noise injection disproportionately degrades minority class Recall@0.1% FPR.
