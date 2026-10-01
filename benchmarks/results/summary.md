# Empirical Benchmark Results Summary

> **CF-Intelligence Benchmark Execution Records**  
> Generated from standalone reproducible CLI runners in `benchmarks/runners/`.  
> Output directory: `benchmarks/results/raw/`

---

## 1. Latency & Concurrency Benchmarks

CF-Intelligence explicitly separates two fundamentally distinct benchmark classes:

### 1.1 In-Process Scoring Pipeline Microbenchmark
- **Runner**: `benchmarks/runners/run_latency_benchmark.py`
- **Scope**: Measures in-process algorithmic compute budget on host CPU threads (PyTorch CPU forward computation through project model architecture, 9-signal composite risk engine, and Pydantic v2 response serialization). Operates on randomly initialized model weights. **Excludes** network sockets, HTTP/ASGI, Uvicorn, Redis, PostgreSQL, and real authentication.
- **Methodology**: Evaluated across 3 independent repetitions with $N \ge 1{,}000$ measured requests per concurrency tier ($C \in \{1, 10, 50, 100, 250, 500\}$; 3,000 total requests per tier). All warm-up requests excluded from measured statistics. Error rate dynamically observed from real execution. Full raw latency samples preserved in companion artifact.
- **Raw Artifacts**: [`latency_microbenchmark.json`](./raw/latency_microbenchmark.json) | Raw Samples: [`latency_microbenchmark_samples.json`](./raw/latency_microbenchmark_samples.json) | Compatibility: [`latency_concurrency_benchmark.json`](./raw/latency_concurrency_benchmark.json)

| Concurrency Level | Measured Throughput (Mean $\pm$ SD) | p50 Latency (ms) | p95 Latency (ms) | p99 Latency (ms) | Observed Error Rate | Statistical Validity |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **1** | **338.5 $\pm$ 45.4 req/s** | **2.70 $\pm$ 0.15 ms** | 3.75 $\pm$ 0.44 ms | 8.87 $\pm$ 1.96 ms | 0.00% | Valid ($N=3000 \ge 1000$) |
| **10** | **1,231.0 $\pm$ 137.9 req/s** | **7.61 $\pm$ 0.95 ms** | 10.92 $\pm$ 1.25 ms | 18.13 $\pm$ 2.45 ms | 0.00% | Valid ($N=3000 \ge 1000$) |
| **50** | **1,246.3 $\pm$ 87.9 req/s** | **31.97 $\pm$ 4.21 ms** | 53.16 $\pm$ 6.32 ms | 63.09 $\pm$ 7.15 ms | 0.00% | Valid ($N=3000 \ge 1000$) |
| **100** | **1,109.9 $\pm$ 76.5 req/s** | **47.49 $\pm$ 5.82 ms** | 87.31 $\pm$ 9.14 ms | 105.02 $\pm$ 11.20 ms | 0.00% | Valid ($N=3000 \ge 1000$) |
| **250** | **1,149.2 $\pm$ 94.3 req/s** | **46.96 $\pm$ 6.12 ms** | 79.14 $\pm$ 8.95 ms | 92.85 $\pm$ 10.45 ms | 0.00% | Valid ($N=3000 \ge 1000$) |
| **500** | **1,013.4 $\pm$ 102.1 req/s** | **32.07 $\pm$ 4.88 ms** | 53.84 $\pm$ 7.55 ms | 122.07 $\pm$ 14.80 ms | 0.00% | Valid ($N=3000 \ge 1000$) |

#### Micro-Latency Component Breakdown (Single Request Fast-Path)
- Token Auth & ABAC Authorization (In-Memory Check): **0.015 ms**
- Feature Store Vector Snapshot Read: **0.001 ms**
- PyTorch Neural Network Forward Pass (Project Architecture): **0.384 ms**
- 9-Signal Composite Risk Scoring Engine: **2.142 ms**
- Pydantic v2 Serialization & Response: **0.028 ms**
- **Total Fast-Path Compute Latency**: **~2.569 ms** (Satisfies internal target <15ms)
- **Full-Path with Linear SHAP Attribution**: **~2.516 ms** (Satisfies internal target <50ms)

> [!NOTE]
> **Archival Note on Legacy Pre-Repair Results**: Previous published numbers (legacy pre-calibration: 2.29 / 17.77 / 71.99 ms; pre-repair calibration: 3.53 / 35.45 / 361.49 ms at $N=10$) are permanently superseded by this controlled multi-repetition methodology ($N \ge 1000$ per tier across 3 independent sweeps). Unsupported claims attributing high-concurrency latency to "connection pool queueing" have been eliminated; observed degradation reflects ThreadPoolExecutor thread scheduling and CPython GIL contention.

---

### 1.2 End-to-End Local HTTP Service Benchmark
- **Runner**: `benchmarks/runners/run_http_benchmark.py`
- **Scope**: Measures client-observed wall-clock HTTP latency against a live running Uvicorn ASGI server over local loopback TCP sockets (`127.0.0.1:8089/api/v1/score-transaction`). Includes TCP framing, Uvicorn event loop dispatch, FastAPI middleware stack (SecurityHeaders, DDoS, CORS, TenantIsolation), SlowAPI rate limiting, Pydantic request validation, ModelService evaluation offloaded via `asyncio.to_thread`, and response serialization.
- **Server Provenance**: Uvicorn 0.47.0 (single-worker ASGI process), Python 3.12.10, AMD Ryzen 16-core, Windows 11.
- **Client Provenance**: `httpx` (0.28.1) async client with keep-alive connection pooling, closed-loop concurrency sweep.
- **Raw Artifacts**: [`latency_http_service_benchmark.json`](./raw/latency_http_service_benchmark.json) | Raw Samples: [`latency_http_service_samples.json`](./raw/latency_http_service_samples.json)

| Concurrency Level | Throughput (Mean $\pm$ SD) | Status 2xx | Status 4xx (Rate-Limited) | Timeouts | p50 Latency (ms) | p95 Latency (ms) | p99 Latency (ms) | Max Latency (ms) |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **1** | **16.6 $\pm$ 14.6 req/s** | 101 | 1,399 | 0 | **2.60 ms** | 11.71 ms | 13.73 ms | 47.98 ms |
| **10** | **72.9 $\pm$ 63.3 req/s** | 876 | 624 | 0 | **60.91 ms** | 105.37 ms | 374.15 ms | 612.44 ms |
| **50** | **69.6 $\pm$ 3.8 req/s** | 1,323 | 177 | 0 | **384.84 ms** | 1,671.15 ms | 2,842.80 ms | 3,421.10 ms |
| **100** | **59.9 $\pm$ 2.4 req/s** | 1,406 | 94 | 0 | **992.25 ms** | 4,550.41 ms | 5,685.55 ms | 6,102.30 ms |
| **250** | **51.4 $\pm$ 1.9 req/s** | 1,477 | 23 | 0 | **4,071.14 ms** | 6,141.72 ms | 6,697.44 ms | 7,105.40 ms |
| **500** | **50.1 $\pm$ 1.2 req/s** | 2,971 | 29 | 0 | **8,655.79 ms** | 12,800.86 ms | 14,668.38 ms | 15,210.00 ms |

> [!NOTE]
> **Operational Observations**:
> 1. **Rate Limiting**: The tested route enforces `@limiter.limit("60/minute")`. Under closed-loop single-worker load ($C=1$), rapid sequential requests from a single client IP trigger HTTP 429 Too Many Requests after the 60-request quota is exhausted, explaining the 4xx distribution.
> 2. **Single-Worker Event Loop Saturation**: In a single Uvicorn process without horizontal worker scaling, concurrent requests queue on the ASGI event loop, increasing client-observed p50 from 2.60ms ($C=1$) to 8,655.79ms ($C=500$) while server throughput plateaus at ~50–73 req/s.

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
