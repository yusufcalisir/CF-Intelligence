# Empirical Benchmark Results Summary

> **CF-Intelligence Benchmark Execution Records**  
> Generated from standalone reproducible CLI runners in `benchmarks/runners/`.  
> Output directory: `benchmarks/results/raw/`

---

## 1. Inference Gateway Concurrency & Latency Stress Test
- **Runner**: `benchmarks/runners/run_latency_benchmark.py`
- **Target**: Fast-Path REST Inference Pipeline (<15ms SLA target)
- **Raw Artifact**: [`latency_concurrency_benchmark.json`](./raw/latency_concurrency_benchmark.json)

| Concurrency Level | Measured Throughput | p50 Latency (ms) | p95 Latency (ms) | p99 Latency (ms) | Error Rate |
|:---:|:---:|:---:|:---:|:---:|:---:|
| **1** | **551.3 req/s** | **1.71 ms** | **2.20 ms** | **2.29 ms** | 0.0% |
| **10** | **3,866.6 req/s** | **2.21 ms** | **2.96 ms** | **3.17 ms** | 0.0% |
| **50** | **4,786.5 req/s** | **7.53 ms** | **15.04 ms** | **17.77 ms** | 0.0% |
| **100** | **4,821.6 req/s** | **10.33 ms** | **22.15 ms** | **26.95 ms** | 0.0% |
| **250** | **4,789.8 req/s** | **18.22 ms** | **38.95 ms** | **49.06 ms** | 0.0% |
| **500** | **4,450.8 req/s** | **27.03 ms** | **56.70 ms** | **71.99 ms** | 0.0% |

### Micro-Latency Component Breakdown (Single Request Fast-Path)
- Token Auth & ABAC Authorization: **0.31 ms**
- Redis Feature Store Vector Lookup: **0.81 ms**
- 9-Signal Composite Risk Scoring Engine: **0.12 ms**
- PyTorch Neural Network Forward Pass: **0.42 ms**
- Pydantic v2 Serialization & Response: **0.11 ms**
- **Total Fast-Path Serving Latency**: **~1.77 ms** (Well within <15ms SLA)

---

## 2. Differential Privacy Utility Frontier Sweep
- **Runner**: `benchmarks/runners/run_dp_tradeoff.py`
- **Methodology**: Subsampled Gaussian mechanism with Rényi DP (RDP) moments composition
- **Raw Artifact**: [`dp_privacy_utility_tradeoff.json`](./raw/dp_privacy_utility_tradeoff.json)

| Gaussian Noise Multiplier ($\sigma$) | Rényi DP Privacy Budget ($\epsilon$) | Model PR-AUC | Model ROC-AUC | Guarantee Interpretation |
|:---:|:---:|:---:|:---:|:---|
| **$\sigma = 3.0$** | $\epsilon = 1.858$ ($\delta=10^{-5}$) | **0.1963** | 0.8301 | Strong Privacy Regime |
| **$\sigma = 2.0$** | $\epsilon = 2.839$ ($\delta=10^{-5}$) | **0.0722** | 0.7470 | Moderate-Strong Privacy |
| **$\sigma = 1.2$** | $\epsilon = 4.910$ ($\delta=10^{-5}$) | **0.3081** | 0.8944 | Balanced Tradeoff |
| **$\sigma = 0.8$** | $\epsilon = 7.696$ ($\delta=10^{-5}$) | **0.2833** | 0.9244 | Moderate Privacy |
| **$\sigma = 0.4$** | $\epsilon = 17.323$ ($\delta=10^{-5}$) | **0.6205** | 0.9687 | Weak Privacy (Low Noise) |
| **$\sigma = 0.0$** | $\infty$ (Non-Private Baseline) | **0.6272** | 0.9684 | Zero Privacy (Pure Baseline) |

---

## 3. Byzantine Adversarial Attack Resilience
- **Runner**: `benchmarks/runners/run_byzantine_benchmark.py`
- **Attack Modality**: Sign Inversion ($\Delta w_{\mathrm{mal}} = -3.0 \cdot \Delta w_{\mathrm{honest}}$, 2 malicious out of 10 clients)
- **Raw Artifact**: [`byzantine_benchmark_sign_inversion.json`](./raw/byzantine_benchmark_sign_inversion.json)

| Aggregation Strategy | Test PR-AUC | Test ROC-AUC | Adversarial Breakdown Status |
|:---|:---:|:---:|:---|
| **Honest FedAvg (Clean Baseline)** | **0.7369** | **0.9781** | Reference Baseline (0 Attackers) |
| **Poisoned FedAvg (No Defense)** | **0.6794** | **0.9665** | Degraded by Malicious Inversion |
| **Coordinate-wise Trimmed Mean ($\beta=0.20$)** | **0.7344** | **0.9782** | **Resilient** (99.7% of Clean PR-AUC) |
| **Krum (Blanchard et al., 2017)** | **0.7257** | **0.9688** | **Resilient** (98.5% of Clean PR-AUC) |
| **Bulyan (Guerraoui et al., 2018)** | **0.7070** | **0.9716** | **Resilient** (95.9% of Clean PR-AUC) |

---

## 4. GraphSAGE Inductive Node Classification
- **Runner**: `benchmarks/runners/run_graph_benchmark.py`
- **Dataset / Graph**: 2-Layer PyTorch GraphSAGE with Mean Aggregator (166 node features, temporal split)
- **Raw Artifact**: [`graphsage_elliptic_benchmark.json`](./raw/graphsage_elliptic_benchmark.json)

| Evaluation Metric | Measured Value | Operational Significance |
|:---|:---:|:---|
| **PR-AUC (Illicit Node Class)** | **0.9001** | Primary metric under severe graph label imbalance |
| **ROC-AUC** | **0.9860** | Global ranking separation across graph nodes |
| **Precision** | **0.9636** | High confidence on flagged suspicious entity accounts |
| **Recall** | **0.3333** | Conservative threshold (p >= 0.5) prior to threshold tuning |
| **F1-Score** | **0.4953** | Harmonic mean on minority illicit class |

---

## 5. Fraud Detection: Centralized vs Federated Baselines
- **Runner**: `benchmarks/runners/run_fraud_benchmark.py`
- **Raw Artifacts**: [`fraud_benchmark_paysim.json`](./raw/fraud_benchmark_paysim.json), [`fraud_benchmark_ieee_cis.json`](./raw/fraud_benchmark_ieee_cis.json)

| Dataset | Evaluation Setting | PR-AUC | ROC-AUC | Recall @ 0.1% FPR | Recall @ 0.5% FPR | Recall @ 1.0% FPR |
|:---|:---|:---:|:---:|:---:|:---:|:---:|
| **PaySim** | Centralized Baseline | **0.4654** | 0.9891 | 0.4000 | 0.6000 | 0.6000 |
| **PaySim** | Federated FedAvg (5 clients) | **0.1463** | 0.9712 | 0.2000 | 0.2000 | 0.2000 |
| **IEEE-CIS** | Centralized Baseline | **0.7811** | 0.9892 | 0.3692 | 0.6154 | 0.6923 |
| **IEEE-CIS** | Federated FedAvg (5 clients) | **0.7554** | 0.9859 | 0.4308 | 0.6308 | 0.6769 |

---

## 6. Federated Optimization Under Non-IID Label Skew
- **Runner**: `benchmarks/runners/run_fl_benchmark.py`
- **Experimental Setup**: 5 Clients, Dirichlet parameter $\alpha = 0.5$ (severe class imbalance skew across clients), 10 communication rounds.
- **Raw Artifact**: [`fl_comparison_alpha_0.5.json`](./raw/fl_comparison_alpha_0.5.json)

| FL Strategy | Convergence PR-AUC (Round 1) | Final PR-AUC (Round 10) | Final ROC-AUC | Communication Volume (MB) |
|:---|:---:|:---:|:---:|:---:|
| **FedAvg** (McMahan et al., 2017) | 0.2602 | 0.0757 | 0.4347 | 0.147 MB |
| **FedProx** ($\mu=0.01$; Li et al., 2020) | 0.0599 | 0.0548 | 0.2080 | 0.147 MB |
| **SCAFFOLD** (Karimireddy et al., 2020) | 0.0595 | 0.0570 | 0.2448 | 0.147 MB |

---

## 7. Generated Visual Figures
All benchmark runs automatically feed into [`benchmarks/runners/generate_charts.py`](../runners/generate_charts.py), generating 300 DPI publication-grade figures stored in `docs/figures/`:
- `docs/figures/benchmark_auc_comparison.png`
- `docs/figures/benchmark_fl_convergence.png`
- `docs/figures/benchmark_privacy_utility.png`
- `docs/figures/benchmark_byzantine_resilience.png`
- `docs/figures/benchmark_latency_concurrency.png`
