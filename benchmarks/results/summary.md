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

### 1.2 Local HTTP Service Capacity & Rate-Limiter Benchmarks

The repository explicitly separates HTTP performance evaluation into two complementary benchmarks:

#### Class B1: Local HTTP Successful-Inference Capacity Benchmark
- **Runner**: enchmarks/runners/run_http_benchmark.py
- **Scope**: Measures client-observed wall-clock HTTP latency against a live running Uvicorn ASGI server over local loopback TCP sockets (127.0.0.1:8089/api/v1/score-transaction). Includes TCP framing, Uvicorn event loop dispatch, FastAPI pure-ASGI middleware stack (SecurityHeaders, DDoS, CORS, TenantIsolation, MTLS, W3C, APIVersion, ContentType; active custom BaseHTTPMiddleware depth = 0), Pydantic request validation, ModelService evaluation, and response serialization. Rate limiting is controlled at benchmark configuration level to isolate pure inference capacity.
- **Server Provenance**: Uvicorn 0.47.0 (single-worker ASGI process), Python 3.12.10, AMD Ryzen 16-core, Windows 11.
- **Client Provenance**: iohttp (3.14.3) async client in separate OS process with keep-alive connection pooling, closed-loop concurrency sweep ( \ge 1{,}000$ per repetition across 3 independent sweeps, 3,000 requests per tier).
- **Authoritative Canonical Artifact (Post-BaseHTTP=0 Final State)**: [latency_http_service_benchmark_post_basehttp0_diagnosis.json](./raw/latency_http_service_benchmark_post_basehttp0_diagnosis.json) | Samples: [latency_http_service_samples.json](./raw/latency_http_service_samples.json)
- **Historical Pre-Intervention Baseline**: [latency_http_service_benchmark_pre_intervention.json](./raw/latency_http_service_benchmark_pre_intervention.json)

##### Canonical Implementation (Active Custom BaseHTTPMiddleware Depth = 0)

| Concurrency Level ($) | Successful Throughput (Mean $\pm$ SD) | Status 2xx | Status 4xx | Timeouts | Pooled p50 Latency (ms) | Pooled p95 Latency (ms) | Pooled p99 Latency (ms) | Max Latency (ms) | Success Rate |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **1** | **134.9 $\pm$ 2.4 req/s** | 3,000 | 0 | 0 | **7.12 ms** | 9.00 ms | 10.85 ms | 14.88 ms | 100.0% |
| **10** | **452.8 $\pm$ 14.0 req/s** | 3,000 | 0 | 0 | **20.48 ms** | 31.84 ms | 39.96 ms | 53.64 ms | 100.0% |
| **50** | **543.0 $\pm$ 23.6 req/s** | 3,000 | 0 | 0 | **89.02 ms** | 104.70 ms | 156.81 ms | 189.07 ms | 100.0% |
| **100** | **520.8 $\pm$ 37.6 req/s** | 3,000 | 0 | 0 | **178.50 ms** | 266.38 ms | 289.42 ms | 337.89 ms | 100.0% |
| **250** | **431.9 $\pm$ 29.6 req/s** | 3,000 | 0 | 0 | **474.97 ms** | 918.33 ms | 950.14 ms | 1,024.12 ms | 100.0% |
| **500** | **401.7 $\pm$ 26.7 req/s** | 3,000 | 0 | 0 | **1,060.89 ms** | 1,469.24 ms | 1,760.77 ms | 2,187.52 ms | 100.0% |

> [!NOTE]
> **Canonical Post-BaseHTTP=0 Findings & Scope**:
> 1. **Peak Empirical Throughput**: Reached **543.0 $\pm$ 23.6 req/s** at =50$ with 100% 2xx success rate across 3,000 requests. Single-client median latency is **7.12 ms** (pooled p50) / **7.13 ms** (per-rep mean).
> 2. **High-Concurrency Queueing Characterization**: At =500$, throughput sustains **401.7 $\pm$ 26.7 req/s** with 18,000/18,000 successful responses across the full diagnostic sweep. Latency grows at high concurrency (pooled p50 = 1,060.89 ms, pooled p99 = 1,760.77 ms). Correlated stage tracing in post_basehttp_bottleneck_diagnosis.json localizes the largest component of median latency to the pre_route_ms socket/dispatch region, confirming that model compute is not the dominant high-concurrency bottleneck. A unique throughput-limiting root cause was not causally isolated. Localhost single-worker results do not constitute production-capacity claims.

##### Historical Methodology Experiment: HTTPX Benchmark Harness (Superseded for Server Capacity)

- **Artifact**: [latency_http_service_benchmark_httpx.json](./raw/latency_http_service_benchmark_httpx.json)
- **Methodological Context**: Prior evaluations used an in-process httpx client. Controlled load-generator isolation demonstrated substantial client-side event-loop starvation under high concurrency (heartbeat lag $> 1{,}000\text{ ms}$), capping observed client throughput at $\sim 55.8\text{ req/s}$ at =500$. Migrating to a separate-process iohttp closed-loop client resolved client starvation, yielding the canonical measurements above. The historical HTTPX numbers below are preserved strictly as a methodological artifact:

| Concurrency Level | HTTPX Throughput | Status 2xx | Timeouts | 2xx p50 Latency (ms) | 2xx p95 Latency (ms) | 2xx p99 Latency (ms) | Success Rate | Methodological Status |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---|
| **1** | **91.1 $\pm$ 2.3 req/s** | 3,000 | 0 | **10.77 ms** | 12.41 ms | 15.21 ms | 100.0% | Valid single-client baseline |
| **10** | **191.0 $\pm$ 1.9 req/s** | 3,000 | 0 | **48.06 ms** | 57.85 ms | 80.65 ms | 100.0% | Historical offload baseline |
| **50** | **120.6 $\pm$ 2.0 req/s** | 3,000 | 0 | **231.56 ms** | 1,205.94 ms | 1,918.50 ms | 100.0% | Client loop lag emerging |
| **100** | **82.7 $\pm$ 5.5 req/s** | 3,000 | 0 | **648.51 ms** | 3,199.80 ms | 5,152.51 ms | 100.0% | Client loop lag growing |
| **250** | **68.0 $\pm$ 3.6 req/s** | 2,998 | 0 | **2,379.38 ms** | 7,885.00 ms | 10,207.36 ms | 99.9% | Client loop starvation |
| **500** | **59.3 $\pm$ 0.2 req/s** | 2,989 | 0 | **7,358.68 ms** | 11,104.51 ms | 12,729.72 ms | 99.6% | Severe client starvation (Superseded) |

#### Class B2: Rate-Limited Public-Endpoint Behavior Benchmark
- **Runner**: enchmarks/runners/run_http_ratelimit_benchmark.py
- **Scope**: Exercises the production SlowAPI rate limiter (60/minute) under stable client identity to evaluate enforcement and rejection latency.
- **Raw Artifacts**: [
ate_limit_behavior_benchmark.json](./raw/rate_limit_behavior_benchmark.json) | Raw Samples: [
ate_limit_behavior_samples.json](./raw/rate_limit_behavior_samples.json)
- **Measured Outcomes**:
  - Allowed 2xx Before Quota Exhaustion: 60 requests ( = 11.46\text{ ms}$, mean $= 12.16\text{ ms}$).
  - Rate-Limited 429 Rejections: 60 requests rejected ( = 4.41\text{ ms}$, mean $= 4.06\text{ ms}$).
  - Transition: Rejection occurs deterministically at request index 60 with RFC 7807 problem details and Retry-After: 60 headers.

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

- **Status**: `CANONICAL` (Canonical 72-Condition Multi-Round Benchmark)
- **Runner**: `benchmarks/runners/run_byzantine_federated_benchmark.py --config canonical`
- **Scope & Protocol**: Real Credit Card Fraud tabular dataset partitioned across 12 simulated bank clients under Non-IID Dirichlet distribution ($\alpha = 0.50$, $\min(\text{samples}) = 50$, zero-positive clients permitted). Evaluated over 10 federated rounds, 1 local epoch per round, PyTorch MLP architecture, `MODEL_DELTA` aggregation space, $f=2$ Byzantine attackers ($16.7\%$), evaluated across $N=3$ seeds (`[42, 123, 456]`). Evaluates 6 aggregators across 4 attack states (18 clean conditions, 54 attacked conditions = 72 total conditions).
- **Authoritative Canonical Artifact**: [`byzantine_federated_canonical.json`](./raw/byzantine_federated_canonical.json) (Status: `CANONICAL`, Size: 47,417 bytes, SHA-256: `c760df9912a1235f0131bd4060ab8fa274dddcb5b25c558ca4436c558723ff4d`)
- **Provenance Caveat**: `MACHINE_CONFIG_INCOMPLETE_BUT_INTENT_AND_EXECUTION_MATCH` (frozen protocol definition hash `d0640c8e...` vs execution-resolved config hash `f3c89626...`).

### Canonical Multi-Round Benchmark Results

Primary metric: Average Precision (`sklearn.metrics.average_precision_score`, reported as PR-AUC). Retention is calculated per-seed as $\mathrm{Retention}(c, s) = \mathrm{AP}(c, s) / \mathrm{AP}(\text{clean FedAvg}, s)$, reporting the mean of per-seed ratios with sample standard deviation ($\mathrm{ddof}=1$).

| Aggregation Strategy | Clean PR-AUC ($f=0$) | Scaled Sign-Inversion ($\times -3.0$) | Gaussian Noise ($\sigma=1.0$) | Omniscient ALIE ($z=1.0$) | Mean Retention (Sign-Flip) | Observed Stability & Notes |
|:---|:---:|:---:|:---:|:---:|:---:|:---|
| **FedAvg (Unprotected)** | **0.7179 $\pm$ 0.0207** | 0.5279 $\pm$ 0.3140 | 0.7202 $\pm$ 0.0034 | 0.6365 $\pm$ 0.1474 | 73.19% $\pm$ 43.19% | Severe vulnerability; Seed 42 collapsed to **0.1653** under sign-flip |
| **Coordinate Median** | 0.7145 $\pm$ 0.0150 | 0.7062 $\pm$ 0.0210 | 0.7200 $\pm$ 0.0105 | 0.5364 $\pm$ 0.2881 | 98.50% $\pm$ 4.70% | Stable under sign-flip; collapses under ALIE on Seed 123 (**0.2045**) |
| **Trimmed Mean ($\beta=0.20$)** | 0.7177 $\pm$ 0.0183 | **0.7141 $\pm$ 0.0129** | **0.7259 $\pm$ 0.0004** | **0.7189 $\pm$ 0.0171** | **99.54% $\pm$ 4.05%** | Most consistent retention across evaluated configurations ($N=3$) |
| **Single Krum** | 0.6729 $\pm$ 0.0270 | 0.4716 $\pm$ 0.4081 | 0.7148 $\pm$ 0.0264 | 0.6684 $\pm$ 0.0309 | 65.34% $\pm$ 56.49% | **Catastrophic collapse on Seed 456** (**0.0011**; root cause unidentifiable from raw artifact) |
| **Multi-Krum ($m=10$)** | 0.7118 $\pm$ 0.0253 | **0.7061 $\pm$ 0.0341** | 0.7224 $\pm$ 0.0039 | 0.7131 $\pm$ 0.0191 | **98.48% $\pm$ 6.93%** | Resilient across evaluated attacks; minor degradation on Seed 456 (0.6667) |
| **Bulyan** | 0.7161 $\pm$ 0.0205 | 0.6961 $\pm$ 0.0363 | 0.7168 $\pm$ 0.0094 | 0.4363 $\pm$ 0.2072 | 97.09% $\pm$ 5.92% | Tolerates sign-flip; collapses under ALIE on Seed 123 (**0.2220**) |

> [!WARNING]
> **Mandatory Disclosure of Seed-Level Instability**:
> Multi-seed aggregation must not obscure catastrophic seed-level failures:
> 1. **Sign-Flip + FedAvg**: Seed 42 collapsed to $\text{PR-AUC} = 0.165317$ (vs 0.711586 on Seed 123 and 0.706788 on Seed 456).
> 2. **Sign-Flip + Single Krum**: Seed 456 experienced complete collapse to $\text{PR-AUC} = 0.001104$ (`ROOT_CAUSE_NOT_IDENTIFIABLE_FROM_CANONICAL_ARTIFACT`).
> 3. **ALIE + Coordinate Median**: Seed 123 collapsed to $\text{PR-AUC} = 0.204505$.
> 4. **ALIE + Bulyan**: Seed 123 collapsed to $\text{PR-AUC} = 0.222000$.

### Historical Prototype Archive (Quarantined)

- **Historical Raw Artifact**: [`byzantine_benchmark_sign_inversion.json`](./raw/byzantine_benchmark_sign_inversion.json) (Status: `HISTORICAL_QUARANTINED`)
- The historical single-seed synthetic Gaussian proxy ($0.7344 / 0.7369 \approx 99.66\% \approx 99.7\%$) evaluated a 10-client prototype and is quarantined from canonical claims. Note: Exploratory diagnostic figure `docs/figures/benchmark_byzantine_resilience.png` reflects a separate historical script and is decoupled from canonical FL evaluation.


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
- **Canonical Runners**: `experiments/paysim/run_paysim_canonical_benchmark.py`, `experiments/credit_card/run_creditcard_benchmark.py`
- **Canonical Artifacts**: [`experiments/paysim/canonical_results.json`](../../experiments/paysim/canonical_results.json), [`experiments/credit_card/multi_seed_controlled_results.json`](../../experiments/credit_card/multi_seed_controlled_results.json)
- **Historical / Smoke Artifacts**: [`fraud_benchmark_paysim.json`](./raw/fraud_benchmark_paysim.json) (`SUPERSEDED`), [`fraud_benchmark_ieee_cis.json`](./raw/fraud_benchmark_ieee_cis.json) (`HISTORICAL`), [`fraud_benchmark_credit_card.json`](./raw/fraud_benchmark_credit_card.json) (`SUPERSEDED_PLACEHOLDER`)

| Dataset | Evaluation Setting | Provenance | PR-AUC | ROC-AUC | Recall @ 0.1% FPR | Status |
|:---|:---|:---:|:---:|:---:|:---:|:---|
| **PaySim** | Centralized Baseline (30 ep, 59.7k steps) | `EXTERNALLY_SIMULATED` | **0.9545 $\pm$ 0.0073** | 0.9994 | 0.9877 | `CANONICAL` |
| **PaySim** | Federated FedAvg (3 clients, 10 rounds) | `EXTERNALLY_SIMULATED` | **0.9545 $\pm$ 0.0119** | 0.9993 | 0.9816 | `CANONICAL` |
| **PaySim (Historical)** | Centralized / FedAvg (10-feat synthetic fallback) | `PROJECT_SYNTHETIC` | 0.4654 / 0.1463 | 0.9891 / 0.9712 | 0.4000 / 0.2000 | `SUPERSEDED` |
| **Credit Card** | Centralized Equalized (10 ep, 35.6k steps) | `REAL_DATA` | **0.8219 $\pm$ 0.0364** | 0.9803 | 0.8653 | `CANONICAL` |
| **Credit Card** | Federated FedAvg (3 clients, 5 rnds × 2 ep) | `REAL_DATA` | **0.8248 $\pm$ 0.0417** | 0.9841 | 0.8653 | `CANONICAL` |
| **Credit Card** | Centralized Legacy (2 ep, 7.1k steps) | `REAL_DATA` | 0.7449 $\pm$ 0.0385 | 0.9860 | 0.8519 | `HISTORICAL` |
| **Credit Card** | Bank C Silo (Near-Zero Fraud: 2 cases) | `REAL_DATA` | 0.5428 $\pm$ 0.1185 | 0.9630 | 0.6061 | `CANONICAL` |
| **IEEE-CIS (Real 590k)** | Centralized Pooled (10 ep, 9,230 steps) | `REAL_DATA` | **0.4422 $\pm$ 0.0034** | 0.8536 | 0.2004 | `CANONICAL` |
| **IEEE-CIS (Real 590k)** | Federated FedAvg (3 clients, 5 rnds × 2 ep, 9,240 steps) | `REAL_DATA` | **0.3895 $\pm$ 0.0124** | 0.8325 | 0.1976 | `CANONICAL` |
| **IEEE-CIS (Historical)** | Centralized / FedAvg (10-feat synthetic smoke) | `PROJECT_SYNTHETIC` | 0.7811 / 0.7554 | 0.9709 / 0.9672 | 0.3692 / 0.4308 | `SUPERSEDED` |

> [!NOTE]
> **Scientific Provenance Notes**:
> 1. **PaySim Canonical Parity**: Evaluated across 3 independent seeds (`[42, 123, 456]`) on a 10% systematic sample ($N=636{,}262$ transactions, 817 fraud) of the physical PaySim CSV (`PS_20174392719_1491204439457_log.csv`, SHA-256: `16910f90...`). Centralized equalized budget achieves PR-AUC $0.9545 \pm 0.0073$; Federated FedAvg achieves $0.9545 \pm 0.0119$ ($\Delta = 0.0000 \pm 0.0191$). Historical synthetic fallback values (`0.4654` / `0.1463`) are archived as `SUPERSEDED`.
> 2. **IEEE-CIS Canonical Execution**: Evaluated across 3 predefined random seeds (`[42, 123, 456]`) on the full physical Kaggle IEEE-CIS dataset ($N=590{,}540$ transactions, 434 merged columns, 421 numeric input features, $20{,}663$ fraud cases). Strict 80/20 chronological holdout on `TransactionDT` (train: $472{,}432$ txns, test: $118{,}108$ txns; zero future lookahead, $4{,}064$ test fraud cases, $114{,}044$ test non-fraud cases). Standardizer fit strictly on train partition. Primary metric is Average Precision (`sklearn.metrics.average_precision_score`, reported as PR-AUC). Under Dirichlet $\alpha=0.5$ non-IID client partitioning across 3 simulated bank clients with closely matched optimizer work (9,230 centralized vs. 9,240 federated local optimizer steps; ~0.11% difference), Centralized PR-AUC is $0.4422 \pm 0.0034$ and FedAvg PR-AUC is $0.3895 \pm 0.0124$ (paired difference $\Delta = -0.0527 \pm 0.0090$, corresponding to ~11.9% relative reduction vs centralized mean; client heterogeneity is one plausible contributor to the observed gap). Recall @ 0.1% FPR diagnostic operating point achieves $20.04\% \pm 0.32\%$ Centralized vs $19.76\% \pm 1.41\%$ FedAvg (approx. 114 false positives, approx. 803 true positives, ~917 total investigation alerts). The predefined engineering target of `0.8120` was not reached under the evaluated chronological holdout; temporal distribution shift is a plausible contributor, but this experiment did not isolate its causal effect. Historical synthetic smoke metrics (`0.7811` / `0.7554`) came from an earlier synthetic fallback experiment and are archived as non-comparable. Transaction data are real competition data; bank federation is simulated.
> 3. **Credit Card Budget Equalization**: When given equalized compute budgets (10 dataset passes, 35.6k optimizer steps), Centralized and FedAvg showed similar observed mean performance across the three evaluated seeds ($0.8219 \pm 0.0364$ vs $0.8248 \pm 0.0417$; observed mean difference $+0.0029$). Bank C experiences a collapse in silo isolation ($0.5428$) but is rescued to $0.8248$ via collaborative federated learning.

---

## 6. Federated Optimization Under Non-IID Label Skew
- **Runner**: `benchmarks/runners/run_fl_benchmark.py`
- **Experimental Setup**: 5 Clients, Dirichlet parameter $\alpha = 0.5$ (severe class imbalance skew across clients), 10 communication rounds.
- **Raw Artifact**: [`fl_comparison_alpha_0.5.json`](./raw/fl_comparison_alpha_0.5.json)

| FL Strategy | Convergence PR-AUC (Round 1) | Final PR-AUC (Round 10) | Final ROC-AUC | Communication Volume (MB) |
|:---|:---:|:---:|:---:|:---:|
| **FedAvg** (McMahan et al., 2017) | **0.0179** | **0.2331** | **0.9202** | 0.147 MB |
| **FedProx** ($\mu=0.01$; Li et al., 2020) | **0.0179** | **0.2285** | **0.9185** | 0.147 MB |
| **SCAFFOLD** (Karimireddy et al., 2020) | **0.0179** | **0.2196** | **0.9175** | 0.294 MB |

> [!NOTE]
> **Non-IID Provenance Synchronization**: Values above reflect authoritative Level 1 disk measurements from `fl_comparison_alpha_0.5.json`. Earlier draft summary values (`0.0757` / `0.0548` / `0.0570`) were from an uncommitted draft run and have been reconciled. Under severe Dirichlet skew ($\alpha=0.5$), FedAvg ($0.2331$) and FedProx ($0.2285$) maintain competitive classification performance while SCAFFOLD consumes 2× communication ($0.294\text{ MB}$) due to client state drift control vectors.

---

## 7. Generated Visual Figures
All benchmark runs automatically feed into [`benchmarks/runners/generate_charts.py`](../runners/generate_charts.py), generating 300 DPI publication-grade figures stored in `docs/figures/`:
- `docs/figures/benchmark_auc_comparison.png`
- `docs/figures/benchmark_fl_convergence.png`
- `docs/figures/benchmark_privacy_utility.png`
- `docs/figures/benchmark_byzantine_resilience.png`
- `docs/figures/benchmark_latency_concurrency.png`

---

## 8. CFI-CrossBank-02 Consortium Benchmark (Canonical Protocol 2.1.0)
- **Protocol & Execution Identity**: `CFI-CrossBank-02` Version 2.1.0 (Frozen at commit `2f64a02b65caa0b35588ec8c016290d1f2ac6e61`, promoted at `4cc1ffba0d253a4c336b2d5e267b5b6666c742c1`)
- **Canonical Raw Artifact**: [`crossbank_v2_canonical.json`](./raw/crossbank_v2_canonical.json) (SHA-256: `b6f802cad979c8cca083dd030cfc0bd12beb06846ea1b4ee317b8747ba0efe6a`, 322,468 bytes)
- **Evaluation Scope**: 5 canonical seeds (`[42, 123, 456, 789, 2025]`), 3 synthetic banking institutions (Bank A 50%, Bank B 30%, Bank C 20% with zero historical training fraud), 7 complex fraud scenarios.

| Evaluation Condition | Paradigm & Feature Regime | Provenance | Average Precision (AP) | ROC-AUC | Recall @ 0.1% Val FPR | Status |
|:---|:---|:---:|:---:|:---:|:---:|:---|
| **Centralized Baseline** | Centralized Pooled (Matched Local Feats) | `PROJECT_SYNTHETIC` | 0.1454 $\pm$ 0.0186 | **0.9091 $\pm$ 0.0112** | 0.0041 $\pm$ 0.0038 | `CANONICAL` |
| **Federated FedAvg** | Collaborative Local Features (Realistic) | `PROJECT_SYNTHETIC` | **0.1779 $\pm$ 0.0260** | 0.9013 $\pm$ 0.0066 | 0.0130 $\pm$ 0.0096 | `CANONICAL` |
| **Isolated Local Silos** | Independent Bank Models (Alpha / Beta / Gamma) | `PROJECT_SYNTHETIC` | 0.1656 $\pm$ 0.0278 | 0.7116 $\pm$ 0.0219 | 0.0098 $\pm$ 0.0064 | `CANONICAL` |
| **Consortium Signal (Oracle)** | Federated Cross-Bank Signal (Diagnostic Ceiling) | `PROJECT_SYNTHETIC` | **0.8140 $\pm$ 0.1133** | **0.9227 $\pm$ 0.0758** | 0.7840 $\pm$ 0.1112 | `ORACLE_UPPER_BOUND_ABLATION` |

> [!NOTE]
> **CrossBank v2 Scientific Findings & Preserved Negative Results**:
> 1. **Q2 Collaborative Uplift & Institution Disparity**: Federated parameter sharing achieves a pooled AP gain over isolated local training ($0.1779$ vs $0.1656$, paired $\Delta\text{AP} = +0.0123 \pm 0.0138$; paired $\Delta\text{ROC} = +0.1897 \pm 0.0204$). However, per-bank decomposition reveals this pooled gain is driven by rescuing Bank C (which had zero local training fraud; $\Delta\text{ROC} = +0.3514 \pm 0.2009$, $\Delta\text{AP} = +0.1086 \pm 0.0596$). Data-rich institutions experience slight regressions (Bank A: $-0.0293 \pm 0.0104$, Bank B: $-0.0171 \pm 0.0226$), refuting the assumption that federated learning uniformly benefits every participant.
> 2. **Q3 Centralized vs Federated Trade-off**: Metrics diverge across paradigms: AP favors FedAvg ($+0.0325 \pm 0.0181$), while ROC-AUC favors Centralized ($-0.0079 \pm 0.0056$). Both conditions carry a provenance caveat (the centralized baseline was trained on bank-local features). Claims of "matching centralized performance" are permanently retired as no equivalence test was preregistered.
> 3. **Scenario 7 Multi-Hop Layering Negative Result**: Under realistic bank-local features, federated training detected only 5 of 133 pooled Scenario 7 test incidents ($3.76\%$ recall; $3.92\%$ mean per-seed recall), confirming that realistic bank-local features fail to intercept unseen multi-hop layering.
> 4. **Bank C Cold-Start Low-FPR Detection Failure**: At enterprise-grade $0.1\%$ false positive rate operating thresholds, Bank C test recall was exactly $0.0\%$ in all 5 seeds ($TP=0$), demonstrating that ranking transfer does not translate to operational detection in the ultra-low-FPR regime.
> 5. **Predecessor Supersession**: CFI-CrossBank-01 (the historical 7-scenario prototype with 2 test incidents) is formally superseded by CFI-CrossBank-02.
> 6. **Diagnostic Oracle Boundary**: The consortium signal condition achieved high metrics ($0.8140$ AP / $0.9227$ ROC) through access to a label-equivalent scenario indicator, serving strictly as a diagnostic upper bound ablation (`ORACLE_UPPER_BOUND_ABLATION`), not deployable technology.

