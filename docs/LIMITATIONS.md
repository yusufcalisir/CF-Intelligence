# Formal Limitations, Anti-Metric Shopping Protocol & Negative Result Ledger

**Document Reference:** `CFI-SPEC-LIMITATIONS-2026-V1`  
**Applicable Governance Frameworks:** Federal Reserve SR 11-7 / OCC 2011-12 (Model Risk Management — Limitations, Assumptions and Validation), EU Artificial Intelligence Act (Regulation (EU) 2024/1689) Article 13 (Transparency and Information to Deployers), ACM/IEEE Guidelines on Transparent Machine Learning Reporting, NeurIPS Paper Checklist Standards.

---

## 1. Epistemological Rationale & Anti-Metric Shopping Protocol

### 1.1 The Problem of Metric Shopping in Machine Learning
In applied machine learning, financial technology, and academic benchmarking, **metric shopping** (selective reporting, p-hacking, or post-hoc metric cherry-picking) presents a critical model risk:
1. **Selective Highlighting**: Teams evaluate multiple evaluation metrics (Accuracy, ROC-AUC, PR-AUC, $F_1$, Recall@FPR) and publish only the flattering numbers. Under extreme class imbalance ($\le 0.15$% fraud prevalence), reporting an uncalibrated accuracy of 99.85% or an inflated ROC-AUC of 0.96 conceals catastrophic real-world failure, where thousands of false alarms drown human investigator queues and zero complex money laundering is detected.
2. **Post-Hoc Threshold Optimization**: Selecting classification decision thresholds ($\theta$) by sweeping the test set to maximize an $F_1$-score artificially inflates claimed effectiveness while guaranteeing out-of-sample operational collapse.
3. **Suppression of Negative Findings**: Omitting experiments where privacy noise destroyed model convergence, or concealing algorithms that lagged behind naive baselines, creates dangerous confirmation bias and deceives institutional risk committees.
4. **Selective Seed Reporting**: Executing dozens of random initializations and reporting only the best-performing run without disclosing the variance distribution.

### 1.2 The Anti-Metric Shopping Protocol
To enforce scientific transparency, CF-Intelligence mandates the following binding protocol across all documentation, benchmark runners, and pull requests:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    ANTI-METRIC SHOPPING PROTOCOL RULES                      │
├─────────────────────────────────────────────────────────────────────────────┤
│ 1. Mandatory Metric Hierarchy Pre-Registration                              │
│    - Extreme Imbalance (<=0.15%): Primary = PR-AUC & Recall@0.1%FPR.        │
│    - ROC-AUC and Accuracy designated strictly as secondary diagnostic aids. │
├─────────────────────────────────────────────────────────────────────────────┤
│ 2. Pre-Fixed Decision Thresholds                                            │
│    - Operational thresholds MUST be fixed a priori (e.g. alpha = 0.0010 for │
│      Recall@0.1%FPR, theta = 0.50 default), NEVER tuned on test sets.       │
├─────────────────────────────────────────────────────────────────────────────┤
│ 3. Unconditional Negative Result Preservation                               │
│    - Utility collapse, baseline lag, or noise penalties MUST be published.  │
│    - Null results MUST use strict "-" representation, never omitted rows.   │
├─────────────────────────────────────────────────────────────────────────────┤
│ 4. Multi-Seed Robustness & Variance Disclosure                              │
│    - Statistical claims MUST report mean +/- std over >= 5 distinct seeds.  │
└─────────────────────────────────────────────────────────────────────────────┘
```

1. **Pre-Registered Metric Hierarchy**:
   - For fraud and AML detection (imbalance $\le 0.15$%), **Precision-Recall Area Under Curve (PR-AUC / Average Precision)** and **Recall at Fixed False Positive Rate (Recall @ 0.1% FPR)** are pre-registered as primary evaluation metrics.
   - ROC-AUC and Brier Score ($\mathrm{BS}$) are classified as secondary diagnostic indicators.
   - Overall Accuracy is strictly prohibited as a standalone efficacy claim.
2. **Pre-Fixed Operational Thresholds**:
   - Decision thresholds must be calibrated strictly on training/validation partitions or set to institutional operational SLAs ($\alpha_{\mathrm{target}} = 0.01$% or $0.1$%). Sweeping thresholds on holdout test partitions to maximize post-hoc $F_1$ is strictly forbidden.
3. **Unconditional Negative Result Preservation**:
   - Any experimental configuration where federated learning, differential privacy, or Byzantine defenses lag behind baselines must be retained in public tables and reports with explicit negative deltas ($\Delta < 0$).
4. **Multi-Seed Distribution Reporting**:
   - Benchmarks must disclose mean and sample standard deviation across standardized random seeds (e.g. `[42, 123, 456, 789, 2025]` for 5-seed benchmarks such as CrossBank v2, or `[42, 123, 456]` for 3-seed real-data benchmarks), detailing both champion and worst-case convergence behavior.

---

## 2. Unconditional Negative Result & Trade-Off Ledger

In compliance with the Anti-Metric Shopping Protocol, this section explicitly documents the empirical trade-offs, utility penalties, and architectural bottlenecks identified across our experimental benchmark suite.

### 2.1 Negative Result NR-001: Differential Privacy Utility Collapse under Strong Noise ($\sigma \ge 3.0$)

#### Empirical Observation
In [`benchmarks/results/raw/dp_privacy_utility_tradeoff.json`](../benchmarks/results/raw/dp_privacy_utility_tradeoff.json), evaluating Gaussian DP-SGD noise multipliers $\sigma \in [0.0, 3.0]$ reveals a steep Pareto trade-off between privacy loss ($\epsilon$) and fraud detection power ($\mathrm{PR\text{-}AUC}$):

| Noise Multiplier ($\sigma$) | RDP Privacy Loss ($\epsilon, \delta=10^{-5}$) | Holdout PR-AUC | Utility Delta ($\Delta \text{PR-AUC}$) | Status & Trade-Off Analysis |
| :---: | :---: | :---: | :---: | :--- |
| $\sigma = 0.0$ | $\infty$ (Non-Private Ceiling) | **0.6272** | Baseline | Full gradient utility; zero privacy defense |
| $\sigma = 0.5$ | $\epsilon = 8.4210$ | **0.5891** | $-0.0381$ (-6.1%) | Mild utility loss; weak privacy boundary |
| $\sigma = 1.0$ | $\epsilon = 3.1450$ | **0.5124** | $-0.1148$ (-18.3%) | Standard academic benchmark point |
| $\sigma = 1.5$ | $\epsilon = 1.8920$ | **0.4208** | $-0.2064$ (-32.9%) | Production operational boundary |
| $\sigma = 2.0$ | $\epsilon = 1.2410$ | **0.3150** | $-0.3122$ (-49.8%) | High privacy; severe detection degradation |
| $\sigma = 3.0$ | $\epsilon = 0.6272$ | **0.1963** | **$-0.4309$ (-68.7%)** | **Severe Utility Collapse**: -68.7% loss |

> [!NOTE]
> **Archival Note on Legacy Prototype Data & Canonical Frontier**:  
> The table above records the historical prototype run (10-feature algebraic centroid prototype without per-sample clipping). The canonical multi-seed Opacus DP-SGD neural benchmark ($N=20{,}000$, per-sample gradient clipping $C=1.0$, PRV accountant) achieves $\mathrm{PR\text{-}AUC} = 0.8965 \pm 0.0078$ at $\sigma=0.0$ and degrades to $0.3465 \pm 0.1580$ at $\sigma=3.0$ ($\epsilon=0.3497$, $-61.35\%$ relative loss) — confirming the identical fundamental negative result (utility collapse under strong privacy noise) under production-grade PyTorch Opacus execution. Full canonical results: [`dp_privacy_utility_tradeoff.json`](../benchmarks/results/raw/dp_privacy_utility_tradeoff.json).

#### Mathematical & Operational Reality
- **Why It Happens:** Differential privacy injects spherical Gaussian noise $\mathcal{N}(0, \sigma^2 C^2 \mathbf{I})$ into clipped gradient updates. In extreme class imbalance, the gradient signal corresponding to rare fraudulent samples is minuscule relative to majority legitimate traffic. At $\sigma \ge 3.0$, the perturbation variance swamps the minority gradient coordinates, destroying decision boundary refinement.
- **Operational Reality:** Banks cannot operate at extreme differential privacy ($\epsilon < 1.0$) in real-time fraud scoring without surrendering more than two-thirds of their fraud detection capability. The platform defines $\epsilon \le 1.0, \delta = 10^{-5}$ as a research target / intended configuration, while empirical evaluations benchmark points from $\epsilon = 1.858$ down to strong utility-collapse regimes. Real-world production deployment remains subject to future institutional risk appetite.

---

### 2.2 Negative Result NR-002: Decentralization & Centralization Performance Gap ($\Delta_{\mathrm{privacy}}$)

#### Empirical Observation
In [`enterprise_benchmark_report.md`](enterprise_benchmark_report.md) Section 3.2, comparing a theoretically pooled centralized database against the decentralized federated champion shows a persistent performance deficit:

| Evaluation Paradigm | Privacy Perimeter | PR-AUC | ROC-AUC | Recall @ 0.1% FPR | Centralization Gap ($\Delta_{\mathrm{privacy}}$) |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **Centralized Upper Bound (GBDT)** | Illegal Raw Data Pooling | **0.8650** | 0.9840 | 66.50% | Baseline Upper Bound |
| **Federated Champion (FedAvg/FedProx)** | Zero Raw PII (Decentralized) | **0.8420** | 0.9750 | 62.40% | **$-0.0230$ (-2.7% Gap)** |

#### Mathematical & Operational Reality
- **Why It Happens:** Centralized gradient boosting has immediate, unconstrained access to joint feature co-occurrence matrices across all institutions. Federated learning must optimize across disparate local non-IID SGD steps with bounded local epochs and DP clipping, incurring an inherent decentralized optimization penalty ($\Delta_{\mathrm{privacy}} = -0.0230$).
- **Honest Perspective:** While federated intelligence captures 97.34% of the theoretical ceiling, decentralization does not match 100% of centralized pooling. Claims that federated learning completely matches centralized pooling without any penalty are scientifically unfounded.

---

### 2.3 Negative Result NR-003: Deep Neural MLP Vulnerability to Extreme Imbalance on Tabular Data

#### Empirical Observation
In [`enterprise_benchmark_report.md`](enterprise_benchmark_report.md) Section 3.4 (PaySim benchmark across $30{,}000$ transactions with 0.05% fraud prevalence), deep multi-layer perceptrons without tree-based ensembling or specialized graph topology failed completely:

| Model Architecture | Optimization Paradigm | Holdout PR-AUC | Holdout ROC-AUC | Recall @ 0.1% FPR | Verdict |
| :--- | :--- | :---: | :---: | :---: | :--- |
| **Centralized Deep MLP** | Pooled PyTorch Neural Net | **0.0014** | 0.6219 | **0.00%** | ❌ **Failure on Tabular Imbalance** |
| **Centralized GBDT** | Pooled LightGBM / XGBoost | **0.6668** | 0.6677 | **66.67%** | ✅ **Robust Tabular Boundary** |
| **Federated Champion (FedAvg)** | Decentralized GraphSAGE+MLP | **0.1184** | 0.8700 | **33.33%** | ⚠️ **Requires Graph Inductive Bias** |

#### Mathematical & Operational Reality
- **Why It Happens:** Standard continuous backpropagation with cross-entropy loss on unweighted raw tabular streams collapses predicted fraud probabilities toward the empirical prior ($\sim 0.0005$). Without inductive relational graph biases (GraphSAGE) or decision tree partitioning (GBDT), neural networks alone are ill-suited for raw tabular fraud detection.
- **Remediation Implemented:** The platform pairs neural embeddings with GraphSAGE inductive topology and hybrid gradient boosting, rather than relying on pure tabular MLPs.

---

### 2.4 Negative Result NR-004: SCAFFOLD Control Variate Drift on Short Communication Horizons

#### Empirical Observation
In PaySim federated optimization across 10 communication rounds with Dirichlet skew ($\alpha = 0.50$):

| Federated Optimizer | Strategy Classification | 10-Round Holdout PR-AUC | Communication / Round | Convergence Stability |
| :--- | :--- | :---: | :---: | :--- |
| **FedAvg** | Parameter Averaging | **0.1184** | $0.024\text{ MB}$ | Stable gradient progress |
| **FedProx ($\mu=0.01$)** | Proximal Regularization | **0.0348** | $0.024\text{ MB}$ | Bounded drift |
| **SCAFFOLD** | Client/Server Control Variates | **0.0009** | $0.048\text{ MB}$ ($2\times$ payload) | ❌ **Early Horizon Destabilization** |

#### Mathematical & Operational Reality
- **Why It Happens:** SCAFFOLD relies on estimating client drift control variates ($c_i$) and global drift ($\bar{c}$). On short training runs ($T \le 10$ rounds), inaccurate early control variates introduce noisy corrective vectors into local SGD steps, destabilizing learning. Furthermore, SCAFFOLD doubles bandwidth overhead per round ($2\times$ payload).
- **Honest Perspective:** SCAFFOLD is not a universal panacea for Non-IID skew; in short-duration federated rounds, simple FedAvg or FedProx with proximal regularization consistently outperforms SCAFFOLD.

---

### 2.5 Negative Result NR-005: Byzantine Defense Utility Penalty on Benign Non-IID Skew

#### Empirical Observation & Canonical Multi-Round Benchmark
Evaluated across two complementary benchmark suites:

1. **Canonical Multi-Round 72-Condition Benchmark** ([`benchmarks/results/raw/byzantine_federated_canonical.json`](../benchmarks/results/raw/byzantine_federated_canonical.json), Status: `CANONICAL`):
   - Evaluated on real Credit Card Fraud tabular data partitioned into 12 simulated bank clients under Non-IID Dirichlet distribution ($\alpha = 0.50$, $\min(\text{samples}) = 50$, zero-positive clients permitted, 10 federated rounds, $N=3$ seeds).
   - Under clean baseline conditions ($f=0$), honest FedAvg achieves PR-AUC $0.7179 \pm 0.0207$. Robust aggregators incur a small clean utility penalty or maintain parity: Trimmed Mean ($\beta=0.20$) achieves $0.7177 \pm 0.0183$ ($\Delta = -0.0002$), Coordinate Median achieves $0.7145 \pm 0.0150$ ($\Delta = -0.0034$), Bulyan achieves $0.7161 \pm 0.0205$ ($\Delta = -0.0018$), Multi-Krum achieves $0.7118 \pm 0.0253$ ($\Delta = -0.0061$), and Single Krum achieves $0.6729 \pm 0.0270$ ($\Delta = -0.0450$).
   - Under evaluated scaled sign-inversion attack ($\times -3.0$, $f=2$ Byzantine nodes): FedAvg collapses to $0.5279 \pm 0.3140$ (with Seed 42 catastrophic collapse to $0.1653$), while Trimmed Mean retains $0.7141 \pm 0.0129$ ($99.54\% \pm 4.05\%$ mean retention relative to clean FedAvg) and Multi-Krum retains $0.7061 \pm 0.0341$ ($98.48\% \pm 6.93\%$).
   - Disclosed seed collapses: Single Krum collapsed to $0.0011$ on Seed 456; ALIE-style perturbations collapsed Coordinate Median ($0.2045$) and Bulyan ($0.2220$) on Seed 123.

2. **Historical Single-Seed Prototype (Quarantined)** ([`benchmarks/results/raw/byzantine_benchmark_sign_inversion.json`](../benchmarks/results/raw/byzantine_benchmark_sign_inversion.json), Status: `HISTORICAL_QUARANTINED`):
   - Evaluated an early 10-client prototype on synthetic Gaussian data ($0.7344 / 0.7369 \approx 99.66\% \approx 99.7\%$). Retained strictly for archival traceability:

| Aggregation Method | Clean Non-IID PR-AUC | Adversarial Attack Resilience | Clean Data Efficiency | Clean Penalty ($\Delta$) | Status |
| :--- | :---: | :---: | :---: | :---: | :---|
| **Naive FedAvg** | **0.7366** | 0.0000 (Collapses under Sign-Flip) | 100.0% | Baseline | Historical Prototype |
| **Coordinate Trimmed Mean** | **0.7344** | 0.7344 (Tolerates 20% poisoned) | 99.7% | $-0.0022$ (-0.3%) | Quarantined Archive |
| **Multi-Vector Krum** | **0.7257** | 0.7257 (Tolerates Byzantine nodes) | 98.5% | $-0.0109$ (-1.5%) | Quarantined Archive |
| **Bulyan Aggregator** | **0.7070** | 0.7070 (Provable resilience) | 95.9% | **$-0.0296$ (-4.0%)** | Quarantined Archive |

#### Mathematical & Operational Reality
- **Why It Happens:** Byzantine-robust aggregators (Krum, Bulyan, Trimmed Mean) filter out candidate vectors located in the geometric periphery of the client update manifold. Under Non-IID Dirichlet skew, a bank with a unique legitimate transaction specialty (e.g. high-volume cross-border wires) produces legitimate gradients that reside in the geometric tail. Aggregators periodically discard these honest updates as suspected poisoning attempts.
- **Operational Reality:** Robustness against malicious poisoning acts as an insurance policy: it guarantees survival under attack, but incurs a clean utility tax on benign data, and cannot guarantee universal immunity across all adversarial distribution shifts or seed realizations.

---

## 3. Systemic & Architectural Limitations (What This Is NOT)

To satisfy the transparency requirements of **Federal Reserve SR 11-7** and **EU AI Act Article 13**, the boundaries of this repository are formally stated:

### 3.1 Non-Production Demonstration & Benchmark Basis
- **Not Deployed in Live Financial Rails:** CF-Intelligence is an advanced research platform, reference implementation, and engineering demonstration. It has **never been deployed in live bank production**, connected to real SWIFT FIN rails, or used to block actual consumer payment transactions.
- **Synthetic & Public Benchmark Basis:** All empirical results are derived from synthetic multi-bank data generators and canonical public datasets (PaySim, IEEE-CIS, Elliptic Bitcoin, IBM AMLSim, SynthAML, AMLNet). While these datasets represent industry-standard benchmarks, they cannot capture all proprietary operational nuances of live core banking systems.

### 3.2 Single-Maintainer Portfolio & Engineering Proof-of-Concept
- **Single Author / Maintainer:** This repository is conceived, engineered, and maintained entirely by a single software engineer (**Yusuf Çalışır**).
- **Not Backed by a Commercial Consortium:** Mentions of "Bank A", "Bank B", "Bank C", "JPMorgan Node", "HSBC Node", or "Consortium Quorum" represent simulated architectural tenants in software, not active corporate partnerships or commercial joint ventures.

### 3.3 Regulatory Inspiration vs. Formal Certification
- **Conceptual Exploration, Not Legal Certification:** Architecture discussions regarding GDPR Article 17, EU AI Act High-Risk requirements, FinCEN SAR e-filing, or EPC SEPA recall rulebooks represent technical explorations of compliance patterns. The software has **not undergone formal third-party compliance audits** or certification by regulatory authorities (FinCEN, BaFin, FCA, MAS).

### 3.4 Hardware-Emulated Security vs. Bare-Metal Silicon
- **Software Emulation Layer:** Production deployments require physical Hardware Security Modules (PKCS#11 HSMs), bare-metal Intel SGX / AWS Nitro Enclaves, and live distributed Apache Kafka clusters. In local development and automated CI/CD testing, the platform utilizes authentic software emulators (`SoftwareHSMSignerEngine`, `SoftwareEmulatedTEEDriver`, in-memory message buses).

### 3.5 Explainability & Manifold Constraints
- **Bounded SHAP Evaluation Budget:** To satisfy sub-50ms API SLAs, SHAP KernelExplainer evaluates a bounded sample budget ($N=100$) over background reference sets ($N=30$), rather than calculating exhaustive exponential Shapley permutations ($2^D$).
- **Discrete Counterfactual Search:** Counterfactual explanations search a greedy discrete perturbation space over domain-mutable features, rather than performing continuous gradient descent along an empirical data manifold.

---

## 4. Epistemic Vulnerability Matrix

| Vulnerability Domain | Technical Failure Mode | Root Cause & Mechanism | Platform Safeguard & Mitigation | Residual Operational Risk |
| :--- | :--- | :--- | :--- | :--- |
| **Statistical Non-IID Skew** | Client gradient divergence / weight oscillation | Extreme Dirichlet parameter ($\alpha \le 0.05$) creates disjoint local label support | Proximal regularization (`FedProx`), learning rate decay, adaptive round quotas | Convergence delay; lower final PR-AUC on minority banks |
| **Differential Privacy** | Complete utility destruction | Noise multiplier $\sigma > 2.0$ injected into low-magnitude minority coordinates | Adaptive DP auto-scaling, RDP moments accounting circuit breaker | Cannot achieve sub-unit $\epsilon$ without severe utility loss |
| **Adversarial Poisoning** | Byzantine coordinator hijacking | Coordinated Sybil nodes submit sign-flipped gradients | Coordinate Trimmed Mean, Krum, Bulyan distance selection | Clean-data utility penalty (1.5% - 4.0% loss); requires $n \ge 4f+3$ |
| **Concept Drift** | Stale model decision boundaries | Fraudsters alter structuring velocity and account hops | Automated retraining triggers ($\mathrm{PSI} > 0.20$, KS test $p < 0.05$) | Retraining lag between alert trigger and global round aggregation |
| **Entity Resolution** | False positive cross-bank graph links | MinHash LSH collision on sparse identity tokens | Type-salted HMAC-SHA256 hashing, secondary Jaro-Winkler disambiguation | Under-clustering on highly obfuscated mule account networks |
| **Concurrency & Gateway** | ASGI event loop thread contention under load | Python GIL saturation during high-concurrency requests ($C \ge 100$) | Multi-worker Gunicorn deployment, Redis JIT caching, circuit breakers | Micro-latency spikes at peak burst traffic ($p_{99} > 80\text{ ms}$) |

---

## 5. Master Reproducibility & Integrity Verification Attestation

To establish complete transparency regarding what is empirically verified versus simulated, the repository provides an automated master reproducibility sweep:
- **Verification Harness:** [`scripts/verify_reproducibility.py`](../scripts/verify_reproducibility.py)
- **One-Command CLI:** `make reproduce-verify` or `python scripts/verify_reproducibility.py --all`
- **Automated Test Suite:** [`backend/tests/unit/test_reproducibility_verifier.py`](../backend/tests/unit/test_reproducibility_verifier.py) (10 tests, 100% passing)
- **Audit Coverage:** 38 / 38 items verified across all 6 canonical categories (100.0% pass rate)
- **Formal Attestation:** Certified reproducible in Section 8 of [`docs/engineering-audit.md`](engineering-audit.md)
