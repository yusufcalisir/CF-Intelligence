"""Exporter utilities for experiment artifacts (JSON, CSV, Parquet, and Markdown).

Handles atomic disk writes, structured tabular traces, and schema-compliant outputs.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pandas as pd

from experiments.harness.schema import AggregateSummary, ExperimentResult


class ExperimentExporter:
    """Handles serialization and persistence of experiment execution artifacts."""

    def __init__(self, output_root: Path | str | None = None):
        if output_root:
            self.output_root = Path(output_root).resolve()
        else:
            self.output_root = Path("experiments/results").resolve()

    def get_run_dir(self, experiment_id: str) -> Path:
        """Return dedicated output directory for an experiment run."""
        run_dir = self.output_root / experiment_id
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def export_all(self, result: ExperimentResult) -> dict[str, str]:
        """Export all primary artifacts (results.json, metrics.csv, traces.parquet).

        Returns:
            Dictionary of relative artifact paths.
        """
        run_dir = self.get_run_dir(result.experiment_id)
        artifact_paths: dict[str, str] = {}

        # 1. Export results.json
        json_path = self.export_results_json(result, run_dir)
        artifact_paths["results_json"] = str(json_path.as_posix())

        # 2. Export metrics.csv
        csv_path = self.export_metrics_csv(result, run_dir)
        if csv_path:
            artifact_paths["metrics_csv"] = str(csv_path.as_posix())

        # 3. Export traces.parquet
        parquet_path = self.export_traces_parquet(result, run_dir)
        if parquet_path:
            artifact_paths["traces_parquet"] = str(parquet_path.as_posix())

        # Update in-memory result artifact paths
        result.artifact_paths.update(artifact_paths)
        # Re-save results.json with updated artifact_paths
        self.export_results_json(result, run_dir)

        return artifact_paths

    def export_results_json(self, result: ExperimentResult, run_dir: Path) -> Path:
        """Atomically export ExperimentResult to results.json."""
        target_path = run_dir / "results.json"
        temp_path = run_dir / "results.json.tmp"

        payload = result.model_dump(mode="json")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        temp_path.replace(target_path)
        return target_path

    def export_metrics_csv(self, result: ExperimentResult, run_dir: Path) -> Path | None:
        """Export step-by-step history to metrics.csv."""
        if not result.history:
            return None

        target_path = run_dir / "metrics.csv"
        temp_path = run_dir / "metrics.csv.tmp"

        fieldnames = [
            "step",
            "train_loss",
            "val_loss",
            "pr_auc",
            "roc_auc",
            "accuracy",
            "f1_score",
            "precision",
            "recall",
            "duration_seconds",
            "timestamp_utc",
        ]

        with open(temp_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for step_metric in result.history:
                row = step_metric.model_dump(mode="json")
                writer.writerow(row)

        temp_path.replace(target_path)
        return target_path

    def export_traces_parquet(self, result: ExperimentResult, run_dir: Path) -> Path | None:
        """Export detailed execution traces to traces.parquet."""
        if not result.history:
            return None

        target_path = run_dir / "traces.parquet"
        temp_path = run_dir / "traces.parquet.tmp"

        records: list[dict[str, Any]] = []
        seed = result.config.seeds[0] if result.config.seeds else 42
        for step_metric in result.history:
            data = step_metric.model_dump(mode="json")
            if "extra" in data and isinstance(data["extra"], dict):
                data["extra"] = json.dumps(data["extra"])
            data["experiment_id"] = result.experiment_id
            data["experiment_name"] = result.config.experiment_name
            data["model_type"] = result.config.model_type
            data["strategy"] = result.config.strategy
            data["seed"] = seed
            records.append(data)

        df = pd.DataFrame(records)
        df.to_parquet(temp_path, engine="pyarrow", index=False)
        temp_path.replace(target_path)
        return target_path

    def export_aggregate_summary(self, summary: AggregateSummary, output_dir: Path | str) -> Path:
        """Export multi-seed AggregateSummary to summary.json."""
        out_dir = Path(output_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        target_path = out_dir / "summary.json"
        temp_path = out_dir / "summary.json.tmp"

        payload = summary.model_dump(mode="json")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        temp_path.replace(target_path)
        return target_path
