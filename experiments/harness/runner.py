"""Experiment Runner Orchestrator.

Manages execution of single-seed and multi-seed experiment configurations,
handles synthetic and real dataset integration, triggers publication figure plotting,
assembles multi-seed aggregate statistical summaries, and compiles markdown dossiers.
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

from benchmarks.runners.plot_publication_figures import plot_all_experiment_figures
from experiments.harness.compile_reports import ReportCompiler
from experiments.harness.exporter import ExperimentExporter
from experiments.harness.schema import (
    AggregateMetric,
    AggregateSummary,
    DatasetMetadata,
    ExperimentConfig,
    ExperimentResult,
)
from experiments.harness.tracker import ExperimentTracker


class SimpleMLP(nn.Module):
    """Calibrated fraud detection Multi-Layer Perceptron for benchmarks."""

    def __init__(self, in_features: int = 16, hidden_dim: int = 32):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_features, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, 16),
            nn.ReLU(),
            nn.Linear(16, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


def generate_synthetic_fraud_dataset(
    num_samples: int = 2000,
    num_features: int = 16,
    fraud_rate: float = 0.05,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray, DatasetMetadata]:
    """Generate reproducible synthetic financial fraud dataset with known imbalance."""
    rng = np.random.default_rng(seed)
    num_fraud = int(num_samples * fraud_rate)
    num_legit = num_samples - num_fraud

    # Legit transactions centered at 0
    x_legit = rng.normal(loc=0.0, scale=1.0, size=(num_legit, num_features))
    y_legit = np.zeros(num_legit, dtype=int)

    # Fraud transactions shifted in feature space
    x_fraud = rng.normal(loc=1.5, scale=1.2, size=(num_fraud, num_features))
    y_fraud = np.ones(num_fraud, dtype=int)

    x = np.vstack([x_legit, x_fraud]).astype(np.float32)
    y = np.concatenate([y_legit, y_fraud]).astype(int)

    # Shuffle
    perm = rng.permutation(num_samples)
    x, y = x[perm], y[perm]

    # Compute SHA-256 over array bytes for integrity
    meta = DatasetMetadata.from_bytes(
        data=x.tobytes() + y.tobytes(),
        dataset_name="SyntheticFinancialConsortium",
        total_samples=num_samples,
        num_features=num_features,
        fraud_samples=num_fraud,
    )
    return x, y, meta


class ExperimentRunner:
    """Executes experiments end-to-end with tracking, export, and figure generation."""

    def __init__(self, output_root: Path | str | None = None):
        self.output_root = Path(output_root).resolve() if output_root else Path("experiments/results").resolve()
        self.exporter = ExperimentExporter(output_root=self.output_root)

    def run_single(
        self,
        config: ExperimentConfig,
        seed: int,
        dataset_tuple: tuple[np.ndarray, np.ndarray, DatasetMetadata] | None = None,
        custom_train_fn: Callable[..., Any] | None = None,
    ) -> ExperimentResult:
        """Execute a single seed experiment run."""
        # 1. Set seed
        torch.manual_seed(seed)
        np.random.seed(seed)

        # 2. Acquire dataset
        if dataset_tuple is None:
            x, y, dataset_meta = generate_synthetic_fraud_dataset(seed=seed)
        else:
            x, y, dataset_meta = dataset_tuple

        # Train / test split (80 / 20)
        split_idx = int(0.80 * len(x))
        x_train, y_train = x[:split_idx], y[:split_idx]
        x_test, y_test = x[split_idx:], y[split_idx:]

        # Create single-seed run config
        single_config = config.model_copy(deep=True)
        single_config.experiment_id = f"{config.experiment_id}_seed{seed}"
        single_config.seeds = [seed]

        # 3. Track execution
        with ExperimentTracker(config=single_config, dataset=dataset_meta, auto_export=False) as tracker:
            if custom_train_fn:
                custom_train_fn(tracker, x_train, y_train, x_test, y_test, single_config)
            else:
                self._default_train_loop(tracker, x_train, y_train, x_test, y_test, single_config)

        # 4. Finalize result
        result = tracker.finalize()

        # 5. Export primary artifacts (results.json, metrics.csv, traces.parquet)
        self.exporter.export_all(result)

        # 6. Generate publication plots
        run_dir = self.exporter.get_run_dir(result.experiment_id)
        plot_dir = run_dir / "plots"
        plots = plot_all_experiment_figures(result.model_dump(mode="json"), plot_dir)
        result.artifact_paths.update(plots)

        # 7. Compile Markdown dossier
        report_path = run_dir / "REPORT.md"
        ReportCompiler.compile_markdown_report(result, report_path)
        result.artifact_paths["report_md"] = str(report_path.as_posix())

        # Re-save final results.json with complete plot and report links
        self.exporter.export_results_json(result, run_dir)
        return result

    def run_multi_seed(
        self,
        config: ExperimentConfig,
        seeds: list[int] | None = None,
        dataset_tuple: tuple[np.ndarray, np.ndarray, DatasetMetadata] | None = None,
    ) -> tuple[list[ExperimentResult], AggregateSummary]:
        """Execute experiment across multiple seeds and compute aggregate statistics."""
        target_seeds = seeds or config.seeds or [42]
        results: list[ExperimentResult] = []

        for s in target_seeds:
            res = self.run_single(config, seed=s, dataset_tuple=dataset_tuple)
            results.append(res)

        summary = self.compute_aggregate_summary(config.experiment_name, results)
        summary_dir = self.output_root / config.experiment_id
        self.exporter.export_aggregate_summary(summary, summary_dir)

        return results, summary

    def compute_aggregate_summary(
        self, experiment_name: str, results: list[ExperimentResult]
    ) -> AggregateSummary:
        """Compute statistical mean, standard deviation, and 95% confidence intervals."""
        metric_keys = ["pr_auc", "roc_auc", "f1_score", "precision", "recall", "brier_score"]
        aggregated: dict[str, AggregateMetric] = {}
        n = len(results)
        seeds = [r.config.seeds[0] for r in results if r.config.seeds]

        for k in metric_keys:
            vals = [r.final_metrics[k] for r in results if k in r.final_metrics]
            if not vals:
                continue
            mean_v = float(np.mean(vals))
            std_v = float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0
            min_v = float(np.min(vals))
            max_v = float(np.max(vals))
            # 95% confidence interval margin: 1.96 * (std / sqrt(n))
            margin = 1.96 * (std_v / math.sqrt(n)) if n > 1 else 0.0
            aggregated[k] = AggregateMetric(
                mean=round(mean_v, 6),
                std=round(std_v, 6),
                min=round(min_v, 6),
                max=round(max_v, 6),
                ci_95_lower=round(max(0.0, mean_v - margin), 6),
                ci_95_upper=round(min(1.0, mean_v + margin), 6),
            )

        return AggregateSummary(
            experiment_name=experiment_name,
            num_runs=n,
            seeds=seeds,
            metrics=aggregated,
            individual_results=[r.experiment_id for r in results],
        )

    def _default_train_loop(
        self,
        tracker: ExperimentTracker,
        x_train: np.ndarray,
        y_train: np.ndarray,
        x_test: np.ndarray,
        y_test: np.ndarray,
        config: ExperimentConfig,
    ) -> None:
        """Standard calibrated training loop executing rounds of gradient descent."""
        in_features = x_train.shape[1]
        model = SimpleMLP(in_features=in_features, hidden_dim=32)
        criterion = nn.BCEWithLogitsLoss()
        optimizer = optim.Adam(model.parameters(), lr=config.learning_rate)

        x_t = torch.from_numpy(x_train)
        y_t = torch.from_numpy(y_train).float().unsqueeze(1)
        x_val = torch.from_numpy(x_test)

        rounds = config.num_rounds
        for r in range(1, rounds + 1):
            model.train()
            optimizer.zero_grad()
            logits = model(x_t)
            loss = criterion(logits, y_t)
            loss.backward()
            optimizer.step()

            # Evaluation on validation set
            model.eval()
            with torch.no_grad():
                val_logits = model(x_val)
                val_probs = torch.sigmoid(val_logits).squeeze().numpy()
                tracker.set_evaluation_predictions(y_test, val_probs)
                val_metrics = tracker.final_metrics

            tracker.log_step(
                step=r,
                train_loss=float(loss.item()),
                pr_auc=val_metrics.get("pr_auc"),
                roc_auc=val_metrics.get("roc_auc"),
                f1_score=val_metrics.get("f1_score"),
                accuracy=val_metrics.get("accuracy"),
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run reproducible fraud detection experiment")
    parser.add_argument("--name", type=str, default="FraudMLP_Benchmark", help="Experiment name")
    parser.add_argument("--rounds", type=int, default=5, help="Number of training rounds")
    parser.add_argument("--seeds", type=int, nargs="+", default=[42, 123], help="Random seeds to evaluate")
    args = parser.parse_args()

    cfg = ExperimentConfig(
        experiment_id=args.name.lower().replace(" ", "_"),
        experiment_name=args.name,
        model_type="FederatedMLP",
        strategy="FedAvg",
        num_rounds=args.rounds,
        seeds=args.seeds,
        learning_rate=0.01,
        output_dir="experiments/results",
    )

    runner = ExperimentRunner()
    results, summary = runner.run_multi_seed(cfg, seeds=args.seeds)
    print(f"[+] Completed {len(results)} seed runs. Summary saved.")
