# Software Correctness vs. Scientific Generalization: Dual-Axis Verification Taxonomy

**Authoritative Technical Specification & Regulatory Governance Framework**  
**Document Version:** `2.0.0`  
**Regulatory Standards:** Federal Reserve SR 11-7 / OCC 2011-12 / EU AI Act (Regulation 2024/1689) Annex IV / EBA ICT Guidelines

---

## 1. Executive Summary & Epistemological Mandate

In enterprise machine learning systems operating within regulated financial infrastructure, verifying a platform requires navigating two fundamentally distinct, orthogonal axes of validation:

1. **Axis 1: Software Correctness (Deterministic Software Implementation & Behavioral Invariants)**  
   *Does the code execute faithfully according to its formal specifications, invariants, schemas, and security boundaries without defects, crashes, or unintended side effects?*
2. **Axis 2: Scientific Generalization (Empirical Statistical Learning & Distributional Robustness)**  
   *Does the federated machine learning model learn discriminative, generalizable representations capable of detecting fraud and financial crime across heterogeneous, non-stationary, out-of-sample, and out-of-time financial transaction distributions?*

A catastrophic failure mode in enterprise AI governance is **epistemological conflation**: confusing the successful completion of a 4,000-test software suite with proof of statistical machine learning efficacy, or conversely, assuming that high empirical accuracy on a benchmark dataset guarantees production software stability, security, or tenant isolation.

This specification formalizes the strict mathematical, architectural, and procedural separation between these two verification paradigms across the CF-Intelligence repository.

---

## 2. Comparative Epistemological Matrix

The following matrix delineates the core characteristics, failure modes, tooling, oracles, and regulatory mappings governing each axis:

| Evaluation Dimension | Axis 1: Software Correctness | Axis 2: Scientific Generalization |
| :--- | :--- | :--- |
| **Core Objective** | Verify bug-free software execution, architectural integrity, and contract conformance. | Quantify statistical predictive power, discrimination, and resilience on unseen distributions. |
| **Primary Question** | *"Is the software implemented correctly according to its engineering design?"* | *"Does the model generalize to unseen financial transaction distributions?"* |
| **Failure Modes** | Deadlocks, memory leaks, contract drift, BOLA/IDOR access violations, SSRF regressions, state deserialization corruption. | Covariate shift degradation, Dirichlet non-IID collapse, excessive false positive rates, concept drift, adversarial poisoning. |
| **Primary Tooling** | `Pytest`, `Vitest`, `Playwright`, `Hardhat`, `Hypothesis`, `Stryker`, `Ruff`, `Mypy`, `Bandit`, `pip-audit`. | `benchmarks/runners/`, `experiments/harness/`, 8 canonical datasets, multi-seed statistical harnesses, ANOVA evaluators. |
| **Verification Oracle** | **Deterministic Binary Oracle**: Pass ($1$) or Fail ($0$). Exact mathematical and logical equality. | **Stochastic Continuous Oracle**: Statistical metric distributions ($\mu \pm \sigma$, Student-$t$ 95% CIs, Pareto frontiers). |
| **Data Nature** | Synthetic micro-fixtures, edge-case unit records, parameterized payloads, schema-constrained mocks. | Real-world historical transaction datasets, out-of-time temporal partitions, multi-agent laundering graphs. |
| **Execution Cadence** | Continuous Integration: commit hooks, PR validation, pre-flight smoke gates in < 20s, full suite in ~5 min. | Decoupled workflows: `benchmarks.yml`, scheduled weekly runs, multi-hour factorial grid sweeps. |
| **Key Invariants** | - SecAgg zero-sum cancellation: $\lVert\sum_i \mathbf{m}_i\rVert < 10^{-4}$<br>- Multi-tenant BOLA rejection: HTTP 403 on foreign org access<br>- Model checkpoint roundtrip: bit-exact $\mathbf{w}' = \mathbf{w}$ (`state_dict`)<br>- goAML 4.0 XML validation against UNODC XSD schemas | - Collaborative Gain: $\Delta\mathrm{PR\text{-}AUC}_{\mathrm{Fed} - \mathrm{Silo}} > 0$<br>- Operational Recall: $\mathrm{Recall@0.01\%FPR} \ge 0.50$<br>- Non-IID Dirichlet stability: $\alpha \in [0.1, 10.0]$<br>- Rényi DP accounting: $\epsilon \le 2.55, \delta = 10^{-5}$<br>- GraphSAGE inductive node classification |
| **Regulatory Standard** | - ISO/IEC 25010 (Software Product Quality)<br>- EBA ICT Risk Guidelines Section 6.4<br>- SOC 2 Type II Processing Integrity | - Federal Reserve SR 11-7 / OCC 2011-12 (Model Risk Management)<br>- EU AI Act Annex IV Section 2 (Accuracy & Robustness)<br>- Basel Committee BCBS 239 Risk Data Aggregation |
| **The Epistemic Danger** | **100% test pass rate does NOT guarantee that the model detects new money laundering rings.** | **0.99 PR-AUC is worthless if the inference gateway crashes under load or leaks PII.** |

---

## 3. Axis 1: Software Correctness Specification

### 3.1 Scope & Boundaries
Software correctness validates that the application, domain, infrastructure, and presentation layers operate according to deterministic specifications:

- **Clean Architecture Boundaries**: Domain entities remain pure and decoupled from infrastructure libraries (FastAPI, SQLAlchemy, Redis, Kafka).
- **Cryptographic & PET Invariants**:
  - Pairwise Diffie-Hellman mask generation on Curve25519 produces bit-exact zero-sum algebraic vector cancellation:

$$\sum_{i=1}^{K} \mathbf{m}_i = \mathbf{0} \implies \left\lVert \sum_{i=1}^{K} \mathbf{m}_i \right\rVert_{\infty} < 10^{-4}$$

  - Threshold Shamir secret sharing enables polynomial recovery with $T$ of $N$ shares and complete information-theoretic security with $T - 1$ shares.
  - Zero-Knowledge Groth16 verification over BN254 enforces elliptic curve pairing validity.
- **Multi-Tenant Isolation**: Row-Level Security (RLS) and Tenant ID query scoping reject Cross-Tenant Broken Object Level Authorization (BOLA/IDOR) attempts with HTTP 403 Forbidden.
- **Model Checkpoint Serialization**: PyTorch `state_dict` parameters, optimizer states, and Pydantic v2 schemas survive serialized disk roundtrips with bit-exact float equality:

$$\mathrm{CosineSimilarity}\left(\mathbf{\Theta}_{\mathrm{pre}},\, \mathbf{\Theta}_{\mathrm{post}}\right) = 1.0000000$$

### 3.2 Verification Test Suites
Software correctness is certified by 3,238 backend Pytest tests, 356 frontend Vitest tests, 72 Playwright browser tests, and 31 Hardhat smart contract tests:

```bash
# Execute rapid deterministic smoke gate (< 20 seconds)
make test-smoke

# Execute complete backend correctness and invariant suite
pytest backend/tests/ -v

# Execute frontend view, component, and contract tests
npm --prefix frontend test

# Execute EVM smart contract settlements and Shapley token tests
npm --prefix contracts test
```

---

## 4. Axis 2: Scientific Generalization Specification

### 4.1 Scope & Boundaries
Scientific generalization measures whether algorithmic patterns discovered during federated training reliably transfer to real-world, dynamic financial environments without data leakage or catastrophic performance degradation:

- **Out-of-Sample & Out-of-Time Generalization**:
  To prevent temporal lookahead bias, datasets are split along strict temporal frontiers ($T_{\mathrm{train}} < T_{\mathrm{val}} < T_{\mathrm{test}}$). Random $k$-fold cross-validation is strictly prohibited on temporal financial transactions.
- **Dirichlet Non-IID Heterogeneity** ($\alpha$ **Sensitivity**):
  Financial crime data is naturally non-IID across banks due to localized client demographics and institutional specializations. Evaluating under Dirichlet distributions ($\mathrm{Dir}(\alpha)$ for $\alpha \in \{0.1, 0.5, 1.0, 5.0, 10.0\}$) guarantees models do not diverge when partition heterogeneity peaks:

$$p_k \sim \mathrm{Dirichlet}(\alpha \cdot \mathbf{1}_K)$$

- **Differential Privacy Utility Frontiers**:
  Quantifies empirical predictive utility as Gaussian noise calibrated via Rényi Differential Privacy (RDP) moments accountant is injected into aggregated gradients ($\epsilon \in [0.5, 8.0]$).
- **Byzantine Breakdown Robustness**:
  Empirically maps breakdown thresholds when malicious actors inject sign-flipping, scaled outlier, or Gaussian poisoning updates:

$$f < \frac{K - 2}{2} \quad \text{for Bulyan}, \qquad f < \frac{K}{2} \quad \text{for Coordinate-wise Median}$$

### 4.2 Benchmark Execution Harness
Scientific generalization is certified across 8 canonical financial crime datasets: PaySim (`paysim`), IEEE-CIS (`ieee_cis`), Kaggle Credit Card (`credit_card`), Elliptic Bitcoin Graph (`elliptic`), IBM AMLSim (`amlsim`), SynthAML Spar Nord Bank (`synthaml`), AMLNet AUSTRAC (`amlnet`), and Cross-Bank Synthetic Consortium (`cross_bank`):

```bash
# Execute master empirical benchmark matrix
make benchmark-matrix

# Execute complete 16-configuration factorial ablation sweep
make benchmark-factorial

# Execute multi-seed statistical robustness harness (5 seeds, 95% CIs)
python -m experiments.harness.runner --name ProductionFraudMLP --rounds 10 --seeds 42 1337 2026
```

---

## 5. Architectural Separation in Repository Structure

The codebase enforces physical directory segregation to prevent conflating software unit tests with stochastic benchmark runs:

```
CF-Intelligence/
├── backend/tests/               # AXIS 1: SOFTWARE CORRECTNESS
│   ├── unit/                    # Fast deterministic unit tests (< 0.1s / test)
│   ├── integration/             # Component and API contract verification
│   ├── chaos/                   # Disaster recovery and fault-injection simulations
│   └── mutation/                # AST mutant killing verification
│
├── benchmarks/                  # AXIS 2: SCIENTIFIC GENERALIZATION
│   ├── runners/                 # Empirical benchmark execution scripts
│   ├── results/raw/             # Serialized golden machine-readable JSON artifacts
│   ├── results/reports/         # Automated Markdown evaluation dossiers
│   └── claim_registry.json      # Quantitative claim registry with empirical provenance
│
├── experiments/                 # AXIS 2: SCIENTIFIC ABLATIONS & FRONTIERS
│   ├── harness/                 # Unified experiment tracking and Parquet trace logger
│   ├── ablations/               # Factorial component ablation grid (2^4 = 16)
│   └── [dataset]/               # Per-dataset empirical training configurations
│
└── verification/                # MATHEMATICAL & CRYPTOGRAPHIC INVARIANTS
    └── [module]/                # 20 self-contained scientific verification suites
```

---

## 6. Regulatory Model Risk Governance Alignment

### 6.1 Federal Reserve SR 11-7 & OCC Bulletin 2011-12
- **Conceptual Soundness**: Documented in `docs/system_design.md` and `docs/threat_model.md`.
- **Ongoing Monitoring & Out-of-Sample Validation**: Addressed exclusively by Axis 2 (Empirical Benchmarking). Unit tests under Axis 1 cannot be submitted as evidence of conceptual soundness or model outcomes analysis under SR 11-7.
- **Outcomes Analysis**: Evaluated via strictly out-of-time recall at operational false positive rates ($\mathrm{Recall@0.01\%FPR}, \mathrm{Recall@0.1\%FPR}$).

### 6.2 EU Artificial Intelligence Act (Regulation 2024/1689)
- **Article 11 & Annex IV (Technical Documentation)**: Technical documentation requires documenting both technical implementation verification (Axis 1) and validation procedures for accuracy, robustness, and cybersecurity (Axis 2).
- **Article 15 (Accuracy, Robustness, and Cybersecurity)**: Requires empirical metrics evaluated on representative target populations under non-stationary distributions.
