# 🔄 CF-Intelligence End-to-End Scientific Reproducibility Guide

**Document Version:** 1.0.0  
**Effective Date:** 2026-09-29  
**Regulatory & Standards Alignment:** Federal Reserve SR 11-7 / OCC 2011-12 (Model Replication & Verification), EU AI Act Article 11 & Annex IV (Technical Documentation & Auditable Reproducibility), ACM / IEEE Reproducible Research Standards  
**Target Platform:** Privacy-Preserving Collaborative Financial Crime Intelligence Platform (CF-Intelligence)

---

## 1. Executive Summary & Scientific Reproducibility Scope

This document provides the authoritative, publication-grade, step-by-step reproducibility guide for the **CF-Intelligence** platform. Every empirical finding, benchmark measurement, privacy-utility frontier, Byzantine fault breakdown curve, and ablation matrix reported in the repository can be reproduced deterministically from source code and canonical benchmark datasets.

The platform guarantees three levels of reproducibility:
1. **Bitwise Exact Software Determinism**: All non-convex stochastic optimizers (FedAvg, FedProx, DP-SGD), graph neighborhood samplers, and synthetic transaction generators utilize cryptographically initialized pseudo-random seeds ($S \in \{42, 123, 456, 789, 101112\}$) and PyTorch deterministic algorithms (`torch.use_deterministic_algorithms(True)`).
2. **Empirical Metric Parity**: All reported evaluation metrics ($\operatorname{PR-AUC}$, $\operatorname{ROC-AUC}$, $\text{Recall @ 0.01% FPR}$, $\text{Recall @ 0.1% FPR}$, $\text{ECE}$, $\text{Brier Score}$) reconcile within documented floating-point confidence intervals ($95\%\text{ CI}$) across distinct CPU/GPU architectures.
3. **Immutable 5-Artifact Hierarchy**: Every executed experiment writes an identical, auditable artifact bundle (`config.json`, `results.json`, `metrics.csv`, `report.md`, `plots/*.png`), ensuring external MLflow, Weights & Biases, or regulatory audit ingestion without proprietary tool lock-in.

---

## 2. Hardware & Compute Environment Prerequisites

### 2.1 Hardware Specifications

The benchmarking suite is engineered to execute efficiently on standard commodity developer workstations while scaling to multi-GPU enterprise compute nodes:

| Resource Dimension | Minimal Development / Test Specification | Recommended Publication Benchmark Specification | Multi-Tenant Cloud / Production Node |
| :--- | :--- | :--- | :--- |
| **Processor (CPU)** | 4 physical cores (x86_64 or ARM64 Apple Silicon) | 8 physical cores (e.g. AMD Ryzen 7 / Intel Core i7 / Xeon) | 16+ vCPUs (c6i.4xlarge / Standard_D16s_v5) |
| **System Memory (RAM)** | 8 GB RAM | 16 GB RAM | 32–64 GB ECC RAM |
| **Storage (Disk)** | 10 GB free NVMe SSD storage | 30 GB free NVMe SSD storage | 100+ GB GP3 SSD storage |
| **Accelerator (GPU)** | Not required (CPU fallback fully supported) | Optional: NVIDIA GPU (8 GB VRAM, Turing or newer) | NVIDIA A10G / T4 / L4 (16–24 GB VRAM) |
| **Network Rails** | Local loopback (`127.0.0.1`) | 1 Gbps internet connection (for initial dataset download) | 10 Gbps VPC mTLS internal backplane |

### 2.2 Operating System Compatibility

The test suite and benchmark harness are continuously verified across:
- **Ubuntu Linux**: 22.04 LTS / 24.04 LTS (`x86_64`)
- **Microsoft Windows**: Windows 11 / Windows Server 2022 (`PowerShell 7` or `CMD`)
- **macOS**: Sonoma 14.x / Sequoia 15.x (`Apple Silicon M-series`)

---

## 3. Software Stack, Python 3.12 & Environment Locks

### 3.1 Core Runtimes

- **Python**: `3.12.x` (Strictly verified on Python `3.12.10`)
- **Node.js**: `v20.x` or `v22.x` LTS (with `npm 10.x+`)
- **PyTorch**: `2.4.0` (Supports both CPU and CUDA 12.1/12.4 wheels)
- **FastAPI / Pydantic**: FastAPI `0.115.0`, Pydantic `v2.9.2`

### 3.2 Environment Setup (Fast Track with `uv`)

We strongly recommend [`uv`](https://github.com/astral-sh/uv) for 10-100× faster dependency installation and deterministic lock resolution:

```bash
# 1. Clone repository
git clone https://github.com/yusufcalisir/CF-Intelligence.git
cd CF-Intelligence

# 2. Create isolated virtual environment
python -m venv .venv

# 3. Activate virtual environment
# On Linux / macOS:
source .venv/bin/activate
# On Windows (PowerShell):
.venv\Scripts\Activate.ps1
# On Windows (CMD):
.venv\Scripts\activate.bat

# 4. Install uv and production dependencies
pip install uv
uv pip install -r backend/requirements.txt
uv pip install pytest pytest-cov pytest-asyncio
```

### 3.3 Frontend Console Setup (Optional for UI Verification)

```bash
cd frontend
npm install
npm run build
cd ..
```

---

## 4. One-Line Master CLI Reproduction Workflows

The repository includes a comprehensive `Makefile` automating end-to-end execution:

```bash
# 1. Complete Environment Verification & Smoke Test
make test

# 2. Master One-Line Verification of All Automated Test Suites (3,218+ Pytest + 355 Vitest + 31 Hardhat)
make test-all

# 3. Run All Empirical Benchmark Runners & Compile Master Matrix
make benchmark-all

# 4. Validate Master Empirical Matrix Schema & Numerical Invariants
make benchmark-matrix

# 5. Audit Standardized 5-Artifact Hierarchy Across All 8 Datasets
make benchmark-verify

# 6. Run 16-Configuration Factorial Ablation Matrix Sweep
make benchmark-factorial
```

---

## 5. Dataset Acquisition Protocol & Credential Verification

Due to institutional licensing agreements and Kaggle competition terms, raw benchmark datasets are not stored in git. The platform provides automated download scripts with cryptographic checksum validation:

```bash
# Run automated dataset download CLI
python scripts/download_real_benchmarks.py --all
```

### 5.1 Canonical Datasets Inventory & Credential Matrix

| Dataset Identifier | Distribution Source | License | Size | Authentication Required? | Target Module |
| :--- | :--- | :--- | :--- | :---: | :--- |
| **`paysim`** | Kaggle (`ealaxi/paysim1`) | CC BY-SA 4.0 | 470 MB | Kaggle API Token (`~/.kaggle/access_token`) | Tabular Transaction Fraud |
| **`ieee_cis`** | Kaggle (`ieee-fraud-detection`) | Competition License | 1.28 GB | Kaggle API Token (`~/.kaggle/access_token`) | Extreme Imbalance & Identity Join |
| **`credit_card`** | Kaggle (`mlg-ulb/creditcardfraud`) | ODbL 1.0 | 144 MB | Kaggle API Token (`~/.kaggle/access_token`) | Low-FPR Operational Profiling |
| **`elliptic`** | Kaggle (`elliptic-data-set`) | CC BY 4.0 | 665 MB | Kaggle API Token (`~/.kaggle/access_token`) | Inductive GraphSAGE AML |
| **`amlsim`** | Kaggle (`anshankul/ibm-amlsim...`) | Apache 2.0 | 73 MB | Kaggle API Token (`~/.kaggle/access_token`) | Multi-Hop Laundering Cycles |
| **`synthaml`** | Nature Scientific Data / Figshare | CC BY 4.0 | 10.4 MB | None (Direct Open Access) | Alert SAR Classification |
| **`amlnet`** | Griffith University / Zenodo | CC BY-NC 4.0 | 25 MB | None (Direct Open Access) | Rare-Event Smurfing Detection |
| **`cross_bank`** | Internal Deterministic Generator | MIT | Synthetic | None (Self-Contained) | 7 Cross-Bank Consortium Scenarios |

### 5.2 Kaggle API Configuration

To enable automated downloading of the Kaggle-hosted datasets (`paysim`, `ieee_cis`, `credit_card`, `elliptic`, `amlsim`):
1. Navigate to your Kaggle Account settings: `https://www.kaggle.com/settings`
2. Click **Create New Token** to download `kaggle.json`.
3. Place `kaggle.json` in:
   - Linux / macOS: `~/.kaggle/kaggle.json` (chmod 600: `chmod 600 ~/.kaggle/kaggle.json`)
   - Windows: `%USERPROFILE%\.kaggle\kaggle.json`
4. Alternatively, export environment variables:
   ```bash
   export KAGGLE_USERNAME="your_username"
   export KAGGLE_KEY="your_api_key"
   ```

### 5.3 Offline / Synthetic Evaluation Mode

If external network access or Kaggle API credentials are unavailable, **every benchmark runner supports synthetic evaluation mode**:
```bash
python benchmarks/runners/run_fraud_benchmark.py --dataset paysim --synthetic-eval
```
In this mode, deterministic synthetic distributions mirroring the exact feature columns, dimensionalities, and class imbalances of the real datasets are generated on-the-fly, enabling 100% code path verification in air-gapped or CI environments.

---

## 6. Per-Dataset Step-by-Step Empirical Reproduction

### 6.1 PaySim Mobile Money Benchmark (6.36M Transactions)
```bash
# Fast evaluation (10k transactions, 5 rounds)
python benchmarks/runners/run_paysim_benchmark.py --nrows 10000 --rounds 5 --local-epochs 2

# Full publication benchmark (50k transactions, 10 rounds, 3 clients)
python benchmarks/runners/run_paysim_benchmark.py --nrows 50000 --rounds 10 --local-epochs 2 --n-clients 3
```

### 6.2 IEEE-CIS Fraud Detection Benchmark (590k Transactions)
```bash
# Execute federated training across Vesta Corp card features
python benchmarks/runners/run_ieee_cis_benchmark.py --nrows 25000 --rounds 5 --clients 3
```

### 6.3 European Credit Card Benchmark (284k Transactions)
```bash
# Low-FPR threshold evaluation and PR-AUC measurement
python benchmarks/runners/run_creditcard_benchmark.py --rounds 5 --clients 3
```

### 6.4 Elliptic Bitcoin Transaction Graph (203k Nodes, 234k Edges)
```bash
# Inductive GraphSAGE 2-layer neighborhood aggregation with temporal split (t=34)
python benchmarks/runners/run_graphsage_benchmark.py --epochs 15 --hidden-dim 64
```

### 6.5 IBM Research AMLSim Multi-Hop Banking Graph (1.32M Transactions)
```bash
# Graph message passing on multi-hop cycle and fan-in smurfing patterns
python benchmarks/runners/run_amlsim_benchmark.py --rounds 10 --hidden-dim 48
```

### 6.6 Danish Spar Nord Bank SynthAML Benchmark (20k Alerts)
```bash
# Federated alert MLP classifier vs isolated bank silos
python benchmarks/runners/run_synthaml_benchmark.py --rounds 6 --clients 3
```

### 6.7 Australian AUSTRAC AMLNet Benchmark (1.09M Transactions)
```bash
# Extreme imbalance (1:714) threshold evaluation
python benchmarks/runners/run_amlnet_benchmark.py --rounds 6 --clients 3
```

### 6.8 CFI-CrossBank Multi-Bank Consortium Benchmark (7 Scenarios)
```bash
# Execute flagship 3-bank consortium topology with Zero-Positive transfer
python experiments/cross_bank/run_consortium_benchmark.py --scenarios 7
```

---

## 7. Multi-Factor Ablation & Privacy Frontiers

### 7.1 $2^4 = 16$ Factorial Component Ablation Matrix
Reproduces the full orthogonal factorial grid across Graph $\times$ DP $\times$ SecAgg $\times$ CrossBank:
```bash
python benchmarks/runners/run_factorial_ablation.py --n-samples 8000 --n-clients 5 --rounds 5
```
Output:
- Telemetry JSON: `benchmarks/results/raw/factorial_ablation_matrix.json`
- Dossier: `experiments/ablations/ablation_report.md`

### 7.2 Differential Privacy-Utility Pareto Frontier
Sweeps Gaussian noise multipliers $\sigma \in \{0.5, 1.0, 1.5, 2.0\}$ against federation rounds $T \in \{5, 10, 20, 50\}$ under Rényi DP moments accountant:
```bash
python benchmarks/runners/run_dp_tradeoff.py
```
Output:
- Telemetry JSON: `benchmarks/results/raw/dp_privacy_utility_tradeoff.json`

### 7.3 Byzantine Adversarial Poisoning Breakdown Curve
Evaluates Krum, Bulyan, Coordinate-Wise Median, and Trimmed Mean against Sign-Flip and Gaussian noise injectors:
```bash
python benchmarks/runners/run_byzantine_benchmark.py --attack sign_inversion --byzantine 2
```
Output:
- Telemetry JSON: `benchmarks/results/raw/byzantine_breakdown_analysis.json`

---

## 8. Artifact Hierarchy & Master Matrix Verification

### 8.1 Automated 5-Artifact Hierarchy Compilation & Audit
To verify that every dataset directory contains the mandatory 5 canonical artifacts:
```bash
# Verify all 8 benchmark directories conform to the 5-artifact standard
python -m experiments.harness.compile_reports --verify

# Re-compile markdown dossiers and tabular metrics from raw results.json
python -m experiments.harness.compile_reports --all --force
```

### 8.2 Master Empirical Matrix Compilation & Strict Null Verification
```bash
# Generate and verify consolidated master benchmark matrix
python benchmarks/generate_master_benchmark_matrix.py --verify

# Print formatted Markdown comparative table
python benchmarks/generate_master_benchmark_matrix.py --markdown
```

---

## 9. Determinism, Seed Control & Floating-Point Stability

To guarantee bitwise or epsilon-bounded numerical reproducibility:

1. **Deterministic Pseudo-Random Initialization**:
   ```python
   import random
   import numpy as np
   import torch

   SEED = 42
   random.seed(SEED)
   np.random.seed(SEED)
   torch.manual_seed(SEED)
   if torch.cuda.is_available():
       torch.cuda.manual_seed_all(SEED)
   ```

2. **PyTorch Algorithmic Determinism**:
   When running on NVIDIA GPUs, atomic floating-point operations in cuBLAS or cuDNN can cause non-deterministic gradient accumulations. To enforce strict determinism:
   ```bash
   export CUBLAS_WORKSPACE_CONFIG=:4096:8
   export PYTHONHASHSEED=42
   ```
   Inside PyTorch scripts:
   ```python
   torch.use_deterministic_algorithms(True)
   torch.backends.cudnn.deterministic = True
   torch.backends.cudnn.benchmark = False
   ```

3. **CPU Multi-Threading Pinning**:
   To prevent asynchronous CPU thread race conditions during gradient reductions:
   ```bash
   export OMP_NUM_THREADS=1
   export MKL_NUM_THREADS=1
   ```

---

## 10. Automated Verification & Test Suite Mapping

The reproducibility pipeline is continuously verified by targeted unit and integration suites:

| Verification Suite | Target Path | Coverage Scope |
| :--- | :--- | :--- |
| **Reproducibility Guide Invariants** | [`backend/tests/unit/test_reproducibility_guide.py`](../backend/tests/unit/test_reproducibility_guide.py) | Verifies `REPRODUCIBILITY.md` existence, required sections, hardware specs, dataset commands, Makefile targets, and KaTeX integrity |
| **Master Benchmark Matrix Engine** | [`backend/tests/unit/test_master_benchmark_matrix.py`](../backend/tests/unit/test_master_benchmark_matrix.py) | Verifies 8-dataset matrix schema, strict null representation, and numerical parity |
| **Factorial Ablation Engine** | [`backend/tests/unit/test_factorial_ablation_matrix.py`](../backend/tests/unit/test_factorial_ablation_matrix.py) | Verifies 16-configuration grid, ANOVA main effects, and Pareto frontier |
| **Experiment Artifact Standard** | [`backend/tests/unit/test_experiment_artifact_hierarchy.py`](../backend/tests/unit/test_experiment_artifact_hierarchy.py) | Verifies 5-artifact hierarchy (`config.json`, `results.json`, `metrics.csv`, `report.md`, `plots/`) across all 8 datasets |
| **Authoritative Dataset Cards** | [`backend/tests/unit/test_dataset_cards.py`](../backend/tests/unit/test_dataset_cards.py) | Verifies `DATASETS.md` provenance, licensing, and 0/10 protected demographic PII scan |
| **Model & System Cards** | [`backend/tests/unit/test_system_and_model_cards.py`](../backend/tests/unit/test_system_and_model_cards.py) | Verifies `MODEL_CARD.md` and `SYSTEM_CARD.md` regulatory conformity |
| **Master Test Runner** | [`scripts/run_all_tests.py`](../scripts/run_all_tests.py) | Orchestrates all backend (3,308), frontend (355), verification (409), and contracts (31) tests |
