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
| **1** | **377.2 req/s** | **2.39 ms** | **3.19 ms** | **3.53 ms** | 0.0% |
| **10** | **1,452.0 req/s** | **5.94 ms** | **7.40 ms** | **7.96 ms** | 0.0% |
| **50** | **1,791.0 req/s** | **17.94 ms** | **30.68 ms** | **35.45 ms** | 0.0% |
| **100** | **1,394.7 req/s** | **37.75 ms** | **64.72 ms** | **79.34 ms** | 0.0% |
| **250** | **1,286.4 req/s** | **71.80 ms** | **126.02 ms** | **147.07 ms** | 0.0% |
| **500** | **1,043.6 req/s** | **116.28 ms** | **285.27 ms** | **361.49 ms** | 0.0% |

### Micro-Latency Component Breakdown (Single Request Fast-Path)
- Token Auth & ABAC Authorization: **~0.005 ms**
- Redis Feature Store Vector Lookup: **~0.000 ms** (in-memory fast cache)
- PyTorch Neural Network Forward Pass: **~0.186 ms**
- 9-Signal Composite Risk Scoring Engine: **~2.088 ms**
- Pydantic v2 Serialization & Response: **~0.015 ms**
- **Total Fast-Path Serving Latency**: **~2.294 ms** (Well within <15ms SLA)
- **Full-Path with SHAP Attribution**: **~2.985 ms** (Well within <50ms SLA)

---

## 2. Differential Privacy Utility Frontier Sweep
- **Runner**: `benchmarks/runners/run_dp_tradeoff.py` (canonical engine: `experiments/dp_evaluation/run_dp_noise_sweep.py`)
- **Methodology**: Genuine DP-SGD via PyTorch Opacus (`PrivacyEngine(accountant='prv')`) with per-sample gradient clipping ($C = 1.0$), Gaussian perturbation mechanism, and Privacy Random Variables (PRV) accounting (`PRVAccountant`, Gopi et al., 2021). Fixed-noise-multiplier sweep across $\sigma \in \{3.0, 2.0, 1.0, 0.5, 0.0\}$.
- **Dataset**: Canonical synthetic banking fraud dataset ($N = 20{,}000$ transactions, 15 features, 2.1% prevalence, 84 test fraud cases).
- **Evaluation**: 3 independent seeds (`[42, 123, 456]`), reporting mean $\pm$ sample standard deviation ($\text{ddof}=1$).
- **Raw Artifact**: [`dp_privacy_utility_tradeoff.json`](./raw/dp_privacy_utility_tradeoff.json)
- **Visual Artifact**: [`docs/figures/benchmark_privacy_utility.png`](../../docs/figures/benchmark_privacy_utility.png)

| Gaussian Noise Multiplier ($\sigma$) | Accounted Privacy Budget ($\epsilon, \delta=10^{-5}$) | Model PR-AUC (Mean $\pm$ Sample Std) | Model ROC-AUC (Mean $\pm$ Sample Std) | Relative Utility Loss | Operational / Compliance Status ($\epsilon \le 2.0$ Target) |
|:---:|:---:|:---:|:---:|:---:|:---|
| **$\sigma = 3.0$** | $\epsilon = 0.3497$ | **0.3465 $\pm$ 0.1580** | 0.8720 $\pm$ 0.0551 | -61.35% | Strict Privacy Regime ($\epsilon \le 1.0$, Compliant) |
| **$\sigma = 2.0$** | $\epsilon = 0.5725$ | **0.4710 $\pm$ 0.1752** | 0.8990 $\pm$ 0.0431 | -47.46% | Moderate-Strong Privacy ($\epsilon \le 1.0$, Compliant) |
| **$\sigma = 1.0$** | $\epsilon = 1.7744$ | **0.7088 $\pm$ 0.1155** | 0.9514 $\pm$ 0.0184 | -20.94% | Balanced Production Target ($\epsilon \le 2.0$, Compliant) |
| **$\sigma = 0.5$** | $\epsilon = 12.1989$ | **0.8457 $\pm$ 0.0429** | 0.9777 $\pm$ 0.0047 | -5.67% | High Utility / Weak Privacy (Target $\epsilon \le 2.0$ Exceeded) |
| **$\sigma = 0.0$** | $\infty$ (Non-Private Baseline) | **0.8965 $\pm$ 0.0078** | 0.9872 $\pm$ 0.0050 | 0.00% (Ceiling) | Zero Privacy (Identical Baseline Schedule) |

> [!NOTE]
> **Statistical Limitations & Operational Target Clarification**: Reported uncertainties represent the sample standard deviation across 3 independent training seeds ($\text{ddof}=1$), capturing model training stochasticity under a fixed dataset realization ($N_{\mathrm{total}}=20{,}000$, 15 features, seed 42) and fixed split ($N_{\mathrm{test}}=4{,}000$ with 84 test fraud positives). The $\epsilon \le 2.0$ threshold represents a statutory / consortium design target: configurations $\sigma=1.0$ ($\epsilon \approx 1.7744$), $\sigma=2.0$ ($\epsilon \approx 0.5725$), and $\sigma=3.0$ ($\epsilon \approx 0.3497$) comply with this target. Configuration $\sigma=0.5$ ($\epsilon \approx 12.199$) deliberately exceeds the target to illustrate the upper utility boundary.
>
> **Archival Note on Legacy Prototype Data**: Previous values published in legacy commits (`0.1963`, `0.0722`, `0.3081`, `0.2833`, `0.6205`, `0.6272`) originated from an obsolete 10-feature algebraic centroid prototype that lacked per-sample clipping semantics and used disconnected static constants. They have been permanently superseded by the above multi-seed Opacus DP-SGD benchmark and archived in `experiments/dp_evaluation/audit_dossier.md`.

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
- **Runner**: `benchmarks/runners/run_graph_benchmark.py --dataset-mode real --epochs 15`
- **Dataset / Graph**: 2-Layer PyTorch GraphSAGE with Mean Aggregator on physical Elliptic Bitcoin Graph (203k nodes, 234k edges, 165 node features)
- **Evaluation Design**: Strict out-of-time temporal split (Train: timesteps 1–30, Validation: 31–34, Test: 35–49) evaluated across 3 training seeds (42, 123, 456)
- **Validation Calibration**: Checkpoint selection by validation PR-AUC maximization; operating threshold calibrated by validation F1 maximization (test set strictly untouched)
- **Raw Artifact**: [`graphsage_elliptic_benchmark.json`](./raw/graphsage_elliptic_benchmark.json)

| Evaluation Metric | Canonical Real-Data Mean | Sample Std ($ddof=1$) | Min | Max | Operational Significance |
|:---|:---:|:---:|:---:|:---:|:---|
| **PR-AUC (Illicit Class)** | **0.3761** | 0.0482 | 0.3304 | 0.4265 | Primary ranking metric on continuous test scores under severe temporal fraud shift |
| **ROC-AUC** | **0.8325** | 0.0078 | 0.8245 | 0.8401 | Global distribution separation on continuous test scores |
| **Precision** | **0.2757** | 0.0956 | 0.2068 | 0.3848 | Measured at frozen validation-calibrated operating threshold (mean $t^* = 0.65$) |
| **Recall** | **0.5583** | 0.0871 | 0.4580 | 0.6150 | Illicit transaction capture at frozen validation-calibrated threshold |
| **F1-Score** | **0.3555** | 0.0567 | 0.3078 | 0.4182 | Harmonic mean on minority illicit class at frozen validation threshold |
| **Recall @ 0.1% Strict FPR** | **0.0462** | 0.0375 | 0.0000 | 0.0886 | High-precision regime illicit catch rate |

> [!NOTE]
> **Historical Artifact Context**: The legacy values (PR-AUC 0.9001, ROC-AUC 0.9860, Precision 0.9636, Recall 0.3333) originated from an artificial 1,500-node synthetic chain graph smoke fallback. That synthetic run has been retired and isolated to `graphsage_synthetic_smoke_benchmark.json`. The numbers above represent the canonical, reproducible benchmark on the physical Elliptic dataset.


---

## 5. Fraud Detection: Centralized vs Federated Baselines
- **Runner**: `benchmarks/runners/run_fraud_benchmark.py`, `experiments/credit_card/run_creditcard_benchmark.py`
- **Raw Artifacts**: [`fraud_benchmark_paysim.json`](./raw/fraud_benchmark_paysim.json), [`fraud_benchmark_ieee_cis.json`](./raw/fraud_benchmark_ieee_cis.json), [`fraud_benchmark_credit_card.json`](./raw/fraud_benchmark_credit_card.json)

| Dataset | Evaluation Setting | PR-AUC | ROC-AUC | Recall @ 0.1% FPR | Recall @ 0.5% FPR | Recall @ 1.0% FPR |
|:---|:---|:---:|:---:|:---:|:---:|:---:|
| **PaySim** | Centralized Baseline | **0.4654** | 0.9891 | 0.4000 | 0.6000 | 0.6000 |
| **PaySim** | Federated FedAvg (5 clients) | **0.1463** | 0.9712 | 0.2000 | 0.2000 | 0.2000 |
| **IEEE-CIS** | Centralized Baseline | **0.7811** | 0.9892 | 0.3692 | 0.6154 | 0.6923 |
| **IEEE-CIS** | Federated FedAvg (5 clients) | **0.7554** | 0.9859 | 0.4308 | 0.6308 | 0.6769 |
| **Credit Card** | Centralized Equalized (10 ep, 35.6k steps) | **0.8219** (Seed 42: 0.7800) | 0.9802 | 0.8653 | 0.8889 | 0.8990 |
| **Credit Card** | Federated FedAvg (5 rounds × 2 ep) | **0.8248** (Seed 42: 0.7788) | 0.9845 | 0.8653 | 0.8855 | 0.8990 |
| **Credit Card** | Centralized Legacy (2 ep, 7.1k steps) | **0.7449** (Seed 42: 0.7059) | 0.9861 | 0.8519 | 0.8754 | 0.8822 |
| **Credit Card** | Bank C Silo (Near-Zero Fraud: 2 cases) | **0.5428** (Seed 42: 0.6113) | 0.9630 | 0.6061 | 0.7879 | 0.7980 |

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
