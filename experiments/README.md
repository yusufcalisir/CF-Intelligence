# Collaborative Fraud Intelligence (CFI) — Scientific Experimentation Framework 🧪

This directory contains the authoritative research, empirical benchmark suites, ablation matrices, and cryptographic evaluation pipelines for the **Privacy-Preserving Cross-Bank Fraud Detection Platform (CF-Intelligence)**.

The framework adheres to strict scientific standards: zero data leakage, out-of-time chronological partitioning, reproducible multi-seed execution (`42, 123, 456, 789, 1024`), and formal 5-artifact audit compliance across all canonical datasets.

---

## 1. Directory Structure & Taxonomy

```text
experiments/
├── README.md                          # Framework architecture, benchmark taxonomy & replication guide
├── __init__.py                        # Top-level package export
│
├── harness/                           # Unified Experiment Harness & Automated Reporting
│   ├── schema.py                      # Pydantic v2 schemas for experiment configs & results
│   ├── runner.py                      # Standardized single-experiment execution runner
│   ├── multi_seed_runner.py           # Multi-seed controlled statistical aggregator (N=5 seeds)
│   ├── tracker.py                     # Step-by-step metric tracking & history recording
│   ├── exporter.py                    # Multi-format artifact exporter (JSON, CSV, Markdown)
│   └── compile_reports.py             # 5-artifact hierarchy compiler & verification engine
│
├── results/                           # Aggregated multi-seed results, comparative baselines & logs
│
├── [ Canonical Dataset Benchmarks (Strict 5-Artifact Hierarchy) ]
│   ├── paysim/                        # PaySim Mobile Money Fraud (6.36M transactions, Non-IID Dirichlet)
│   ├── ieee_cis/                      # IEEE-CIS E-Commerce Fraud (590k transactions, 394 features)
│   ├── credit_card/                   # European Credit Card Fraud (284k transactions, 0.172% fraud)
│   ├── elliptic/                      # Elliptic Bitcoin AML Graph (203k nodes, 234k edges, GraphSAGE)
│   ├── amlsim/                        # AMLSim Multi-Agent Synthetic Simulator (1.32M transactions)
│   ├── synthaml/                      # SynthAML Consortium (mule rings, layering, shell companies)
│   ├── amlnet/                        # AMLNet High-Imbalance Cross-Bank Transaction Network
│   └── cross_bank/                    # 3-Bank Consortium Flagship Benchmark (Alpha, Beta, Gamma)
│
└── [ Specialized Research & Methodological Suites ]
    ├── ablations/                     # Full factorial ablation matrix (2^4 = 16 runs) & Dirichlet sweeps
    ├── baselines/                     # Classical ML (LR, RF, XGB) vs. Local Silos vs. Pooled Upper Bound
    ├── byzantine/                     # Byzantine poisoning attacks vs. robust aggregators (Krum, Bulyan)
    ├── dp_evaluation/                 # Differential Privacy noise sweeps & Rényi DP accounting
    ├── privacy/                       # Membership Inference Attack (MIA) empirical auditing
    ├── fairness/                      # EU AI Act Title III Demographic Parity & 4/5ths Rule audit
    ├── temporal/                      # Out-of-time temporal generalization & concept drift analysis
    ├── error_analysis/                # Stratified FP/FN failure mode decomposition
    ├── thresholds/                    # Decision threshold tuning & financial utility matrix optimization
    └── communication/                 # Network bandwidth, zlib compression & mTLS payload profiling
```

---

## 2. Standardized 5-Artifact Hierarchy

Every canonical dataset benchmark directory enforces the **5-artifact standard** validated by `experiments/harness/compile_reports.py`:

| Artifact | Format | Purpose & Invariant |
|:---|:---|:---|
| **`config.json`** | JSON | Exact hyperparameters, model architecture, federation settings, seed, and data partition specifications. |
| **`results.json`** | JSON | Authoritative quantitative evaluation outputs (PR-AUC, ROC-AUC, F1, Recall@FPR, Loss, confusion matrix). |
| **`metrics.csv`** | CSV | Tabular metrics per round, bank, and aggregation phase for automated plotting and dashboard ingestion. |
| **`report.md`** | Markdown | Comprehensive analytical report detailing problem context, dataset distribution, methodology, and results. |
| **`plots/`** | PNG Directory | High-resolution empirical curves (minimum 3 images: `pr_curves.png`, `roc_curves.png`, `optimizer_convergence.png`). |

---

## 3. Canonical Datasets & Benchmark Summary

| Dataset | Modality & Scale | Primary Architecture | Federated Advantage over Local | Canonical Artifact Path |
|:---|:---|:---|:---|:---|
| **PaySim** | Tabular · 6.36M records | Temporal GNN / MLP | **+18.7% PR-AUC** (0.871 vs. 0.734) | [`experiments/paysim/`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/experiments/paysim) |
| **IEEE-CIS** | Tabular · 590k records | Deep Fraud MLP | **+12.4% PR-AUC** (0.642 vs. 0.571) | [`experiments/ieee_cis/`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/experiments/ieee_cis) |
| **Credit Card** | Tabular · 284k records (0.172% fraud) | Imbalance Resilient MLP | **+11.8% PR-AUC** (0.841 vs. 0.752) | [`experiments/credit_card/`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/experiments/credit_card) |
| **Elliptic** | Dynamic Graph · 203k nodes, 234k edges | Inductive GraphSAGE (2-Layer Mean) | **+20.8% PR-AUC** (0.355 vs. 0.294) | [`experiments/elliptic/`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/experiments/elliptic) |
| **AMLSim** | Graph/Tabular · 1.32M records | Graph Conv + Rule Engine | **+15.2% PR-AUC** (0.789 vs. 0.685) | [`experiments/amlsim/`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/experiments/amlsim) |
| **SynthAML** | Graph · 500k transactions | Relational GNN | **+14.6% PR-AUC** (0.762 vs. 0.665) | [`experiments/synthaml/`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/experiments/synthaml) |
| **AMLNet** | Tabular · 850k records | Deep Attention MLP | **+13.1% PR-AUC** (0.745 vs. 0.659) | [`experiments/amlnet/`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/experiments/amlnet) |
| **Cross-Bank** | Multi-Bank Mesh · 3-Bank Consortium | Federated GraphSAGE + GAT | **+24.1% PR-AUC** (0.892 vs. 0.719) | [`experiments/cross_bank/`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/experiments/cross_bank) |

---

## 4. Specialized Methodological Suites

### 4.1 Full Factorial Ablation Matrix (`experiments/ablations/`)
Evaluates all $2^4 = 16$ orthogonal system configurations across 4 binary factors:
- **Factor A**: Differential Privacy ($\epsilon \le 4.0$) vs. None
- **Factor B**: Secure Aggregation (SecAgg additive masking) vs. Plaintext
- **Factor C**: Graph Neural Network inductive features vs. Raw Tabular
- **Factor D**: FedProx proximal term ($\mu = 0.01$) vs. Standard FedAvg

### 4.2 Non-IID Dirichlet Sweep (`experiments/ablations/dirichlet_sweep.py`)
Stress-tests federated convergence under severe statistical heterogeneity using Dirichlet concentration parameters $\alpha \in \{0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0\}$.

### 4.3 Byzantine Attack & Defense Suite (`experiments/byzantine/`)
Assesses consortium defense under adversarial corruption:
- **Attacks**: Label Flipping ($y \leftarrow 1 - y$), Targeted Backdoor Watermarking, and Gradient Explosion ($10\times$ norm scaling).
- **Aggregators**: Multi-Krum ($m=1$), Coordinate-Wise Trimmed Mean ($\beta = 0.1$), Bulyan, and baseline FedAvg.

### 4.4 Differential Privacy & MIA Auditing (`experiments/dp_evaluation/` & `experiments/privacy/`)
- Sweeps noise multiplier $\sigma \in [0.5, 3.0]$ and gradient clipping norm $C \in [0.1, 5.0]$.
- Evaluates empirical privacy protection against shadow-model Membership Inference Attacks (MIA).

### 4.5 Regulatory Fairness Audit (`experiments/fairness/`)
Verifies compliance with **EU AI Act Title III High-Risk AI Requirements**:
- Automated demographic parity and equalized odds testing across customer age brackets, business entity types, and transaction corridors.
- Confirms zero degradation below the US EEOC Four-Fifths Rule ($80\%$ threshold).

---

## 5. Execution & Replication Commands

```bash
# 1. Verify 5-artifact hierarchy across all 8 canonical datasets
python experiments/harness/compile_reports.py --verify

# 2. Run multi-seed statistical aggregation
python experiments/harness/multi_seed_runner.py --dataset credit_card --seeds 42 123 456

# 3. Execute classical ML baselines comparison
python experiments/baselines/comparative_runner.py --dataset paysim

# 4. Run Byzantine poisoning robustness suite
python experiments/byzantine/run_poisoning_suite.py

# 5. Run Differential Privacy noise sweep
python experiments/dp_evaluation/run_dp_noise_sweep.py

# 6. Execute full factorial ablation matrix
python experiments/ablations/factorial_runner.py
```

---

## 6. Automated Testing & Verification

The experimentation framework is covered by comprehensive unit and integration test suites:

```bash
# Run artifact hierarchy & harness validation
pytest backend/tests/unit/test_experiment_artifact_hierarchy.py backend/tests/unit/test_experiment_harness.py

# Run ablation and Dirichlet sweep validation
pytest backend/tests/unit/test_factorial_ablation_matrix.py backend/tests/unit/test_dirichlet_sweep.py

# Run baseline and statistical fairness suites
pytest backend/tests/unit/test_baselines.py backend/tests/unit/test_demographic_fairness_audit.py
```
