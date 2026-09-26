# Unified Experiment Tracking, Serialization & Publication Suite

> **CF-Intelligence Experiment Infrastructure Specification**  
> **Package:** `experiments/harness/`  
> **Schema Version:** `1.0.0` (Pydantic v2 & TypeScript Synchronization)  
> **Status:** Production-Ready & Tested

---

## 1. Architectural Overview & Design Objectives

The **Unified Experiment Infrastructure** provides a rigorous, reproducible framework for designing, executing, tracking, and publishing collaborative financial crime machine learning experiments.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                   EXPERIMENT HARNESS ARCHITECTURAL TOPOLOGY                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌─────────────────────┐              ┌────────────────────────┐           │
│   │  ExperimentConfig   │              │    DatasetMetadata     │           │
│   │ (Hyperparams/Seeds) │              │ (SHA-256 / Partitions) │           │
│   └──────────┬──────────┘              └───────────┬────────────┘           │
│              │                                     │                        │
│              ▼                                     ▼                        │
│   ┌─────────────────────────────────────────────────────────────┐           │
│   │                     ExperimentTracker                       │           │
│   │  • Context Manager (__enter__ / __exit__)                   │           │
│   │  • Automatic Hardware Environment Probing (CPU, RAM, Torch) │           │
│   │  • Automated Git Provenance Capture (SHA-1 / Branch)        │           │
│   │  • Step-by-Step Training & Validation Trajectory Logging    │           │
│   │  • Evaluation Curve Synthesis (ROC, PR, Brier, Confusion)   │           │
│   └──────────────────────────────┬──────────────────────────────┘           │
│                                  │                                          │
│                                  ▼                                          │
│   ┌─────────────────────────────────────────────────────────────┐           │
│   │                     ExperimentExporter                      │           │
│   │  • Atomic JSON Serialization (results.json)                 │           │
│   │  • Tabular Trajectory Logging (metrics.csv)                 │           │
│   │  • High-Performance Columnar Traces (traces.parquet)        │           │
│   └──────────────┬──────────────────────────────┬───────────────┘           │
│                  │                              │                           │
│                  ▼                              ▼                           │
│   ┌──────────────────────────────┐ ┌────────────────────────────┐           │
│   │    Publication Plot Suite    │ │      ReportCompiler        │           │
│   │ • ROC Curve (300 DPI)        │ │ • Executive Summary Matrix │           │
│   │ • PR Curve (Prevalence line) │ │ • Confusion Matrix Table   │           │
│   │ • Reliability Diagram        │ │ • Hardware & Hyperparams   │           │
│   │ • Confusion Matrix Heatmap   │ │ • REPORT.md Markdown Dossier│          │
│   └──────────────────────────────┘ └────────────────────────────┘           │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Core Design Principles:
1. **Zero Fabrication & Deterministic Reproducibility**:
   - Every experiment run captures the exact `git_commit` SHA-1 hash, execution branch, dataset `sha256_hash`, evaluated random seeds, and host hardware environment.
2. **Atomic Disk Operations**:
   - Serialization to `results.json`, `metrics.csv`, and `traces.parquet` uses staged atomic file replacement (`.tmp` $\to$ target) to prevent corrupted states during unexpected halts.
3. **Multi-Seed Statistical Aggregation**:
   - Rather than single-seed evaluations, the runner aggregates across multiple random seeds (e.g. $[42, 123, 456]$), reporting mean ($\mu$), sample standard deviation ($\sigma$), min, max, and exact 95% confidence intervals ($\mu \pm 1.96 \cdot \frac{\sigma}{\sqrt{N}}$).
4. **Publication-Grade Visual Assets**:
   - Plots are generated directly from raw execution arrays using headless Matplotlib (`Agg` backend) at 300 DPI with tight bounding boxes, suitable for academic papers and regulatory dossiers.

---

## 2. Directory Layout & Artifact Specification

Each experiment execution produces an isolated, self-contained run folder:

```
experiments/results/<experiment_id>/
├── results.json          # Authoritative machine-readable execution contract
├── metrics.csv           # Step-by-step tabular trajectory
├── traces.parquet        # High-performance binary columnar traces (PyArrow)
├── REPORT.md             # Senior-engineering Markdown audit dossier
├── summary.json          # Multi-seed aggregate summary (for multi-seed runs)
└── plots/                # 300 DPI publication figures
    ├── roc_curve.png
    ├── pr_curve.png
    ├── calibration_curve.png
    └── confusion_matrix.png
```

---

## 3. Machine-Readable Schema Specification

### 3.1 Hardware Metadata (`HardwareMetadata`)
Captured dynamically at execution initialization:
- `os_platform`: Operating system family (`Windows`, `Linux`, `Darwin`).
- `os_release` & `os_version`: OS kernel and build version.
- `cpu_model` & `cpu_architecture`: Processor name and architecture string.
- `cpu_physical_cores` & `cpu_logical_cores`: Core count from OS / `psutil`.
- `total_ram_gb` & `available_ram_gb`: Physical host memory in Gigabytes.
- `python_version` & `torch_version`: Runtime and neural library versions.
- `cuda_available` & `device_name`: GPU acceleration state and device descriptor.

### 3.2 Dataset Metadata (`DatasetMetadata`)
- `dataset_name`: Canonical identifier (e.g. `SyntheticFinancialConsortium`, `PaySim`, `IEEE-CIS`).
- `sha256_hash`: 64-character SHA-256 checksum computed over raw record bytes.
- `total_samples` & `num_features`: Matrix dimensionality.
- `fraud_samples` & `fraud_rate`: Positive class distribution and prevalence.
- `split_ratios`: Proportions assigned to `train`, `val`, and `test` splits.

### 3.3 Experiment Result Contract (`ExperimentResult`)
Top-level payload stored in `results.json`:
- `schema_version`: Semantic schema version (currently `1.0.0`).
- `experiment_id`: Unique run identifier.
- `config`: Hyperparameters, model architecture, strategy, and training parameters.
- `hardware`: Environment snapshot.
- `dataset`: Dataset integrity and feature distribution.
- `git_commit`: Active git commit SHA-1.
- `git_branch`: Active git branch.
- `status`: Execution state (`PENDING`, `RUNNING`, `COMPLETED`, `FAILED`).
- `start_time_utc` & `end_time_utc`: ISO 8601 timestamps.
- `total_duration_seconds`: Monotonic runtime duration.
- `final_metrics`: Key evaluation indicators (`pr_auc`, `roc_auc`, `f1_score`, `precision`, `recall`, `brier_score`).
- `history`: List of `StepMetric` snapshots.
- `curves`: Decimated $(x, y)$ coordinate pairs for ROC and PR curves.
- `confusion_matrix`: Discrete binary confusion metrics ($TN, FP, FN, TP$).
- `calibration`: Binned empirical fraud prevalence vs predicted probabilities.
- `artifact_paths`: Manifest of relative links to generated files and figures.

---

## 4. Multi-Seed Aggregate Summary (`AggregateSummary`)

When evaluating algorithms across varying seeds $S = \{s_1, s_2, \dots, s_n\}$:

$$\mu = \frac{1}{n} \sum_{i=1}^n x_i, \quad s = \sqrt{\frac{1}{n-1} \sum_{i=1}^n (x_i - \mu)^2}$$

$$\text{CI}_{95\%} = \left[ \max\left(0, \mu - 1.96 \cdot \frac{s}{\sqrt{n}}\right), \, \min\left(1, \mu + 1.96 \cdot \frac{s}{\sqrt{n}}\right) \right]$$

Stored in `summary.json`:
```json
{
  "experiment_name": "ProductionFraudMLP",
  "num_runs": 3,
  "seeds": [42, 123, 456],
  "metrics": {
    "pr_auc": {
      "mean": 0.812450,
      "std": 0.015230,
      "min": 0.795120,
      "max": 0.824900,
      "ci_95_lower": 0.795202,
      "ci_95_upper": 0.829698
    },
    "roc_auc": {
      "mean": 0.965400,
      "std": 0.004120,
      "min": 0.961000,
      "max": 0.969100,
      "ci_95_lower": 0.960737,
      "ci_95_upper": 0.970063
    }
  }
}
```

---

## 5. Usage & CLI Quickstart

### 5.1 Programmatic Tracking (Python API)

```python
from experiments.harness import ExperimentConfig, DatasetMetadata, ExperimentTracker

config = ExperimentConfig(
    experiment_id="fed_fraud_v1",
    experiment_name="CrossBank_Fraud_Detection",
    model_type="FederatedMLP",
    strategy="FedAvg",
    num_rounds=10,
    seeds=[42],
    learning_rate=0.001,
)

dataset_meta = DatasetMetadata.from_bytes(
    data=raw_csv_bytes,
    dataset_name="PaySim_Partition1",
    total_samples=10000,
    num_features=16,
    fraud_samples=500,
)

with ExperimentTracker(config, dataset_meta) as tracker:
    for round_idx in range(1, 11):
        # ... client training and server aggregation ...
        tracker.log_step(
            step=round_idx,
            train_loss=loss,
            val_loss=val_loss,
            pr_auc=pr_auc,
            roc_auc=roc_auc,
        )
    # Set final validation predictions to synthesize curves & confusion matrix
    tracker.set_evaluation_predictions(y_test, y_pred_prob)
```

### 5.2 Command-Line Interface

```bash
# Run multi-seed experiment and compile dossier
python -m experiments.harness.runner --name FraudMLP_Production --rounds 5 --seeds 42 123 456

# Generate all publication charts from raw benchmarks and experiments
python scripts/generate_charts.py --include-experiments

# Compile Markdown report from an existing results.json
python -m experiments.harness.compile_reports experiments/results/fraudmlp_production_seed42/results.json
```

### 5.3 Make Targets
```bash
make experiment-all    # Runs multi-seed experiment harness and chart compilation
make experiment-clean  # Removes temporary artifacts
```

---

## 6. Frontend Schema Synchronization

TypeScript interfaces mirroring all Pydantic v2 schemas are maintained in:
- `frontend/src/types/benchmark.ts`
- Exported via `frontend/src/types.ts`

Any frontend component or dashboard consuming `results.json` receives fully typed, validated models ensuring zero schema drift across the full stack.
