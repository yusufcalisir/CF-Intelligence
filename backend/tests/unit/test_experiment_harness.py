"""Unit tests for the Unified Experiment Tracking & Serialization Framework.

Validates schema constraints, hardware probes, tracker context lifecycles,
JSON/CSV/Parquet exports, curve generation, multi-seed aggregation,
publication plot generation, and markdown dossier compilation.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.harness.exporter import ExperimentExporter  # noqa: E402
from experiments.harness.runner import ExperimentRunner  # noqa: E402
from experiments.harness.schema import (  # noqa: E402
    AggregateSummary,
    DatasetMetadata,
    ExperimentConfig,
    ExperimentResult,
    HardwareMetadata,
)
from experiments.harness.tracker import ExperimentTracker  # noqa: E402


class TestHardwareAndDatasetMetadata:
    """Test environment discovery and dataset hashing integrity."""

    def test_hardware_metadata_capture(self):
        hw = HardwareMetadata.capture()
        assert hw.os_platform in ["Windows", "Linux", "Darwin"]
        assert hw.cpu_logical_cores >= 1
        assert hw.cpu_physical_cores >= 1
        assert hw.total_ram_gb > 0.0
        assert hw.available_ram_gb > 0.0
        assert hw.python_version != ""
        assert hw.torch_version != ""

    def test_dataset_metadata_creation_and_hashing(self):
        sample_bytes = b"transaction_id,amount,label\n1,100.5,0\n2,9999.0,1"
        meta = DatasetMetadata.from_bytes(
            data=sample_bytes,
            dataset_name="TestFinancialDataset",
            total_samples=2,
            num_features=2,
            fraud_samples=1,
            source_uri="file:///data/test.csv",
        )
        assert meta.dataset_name == "TestFinancialDataset"
        assert meta.total_samples == 2
        assert meta.num_features == 2
        assert meta.fraud_samples == 1
        assert meta.fraud_rate == 0.5
        assert len(meta.sha256_hash) == 64
        # Verify reproducible hash
        import hashlib
        expected = hashlib.sha256(sample_bytes).hexdigest()
        assert meta.sha256_hash == expected


class TestTrackerLifecycleAndCurves:
    """Test stateful tracker execution, metric accumulation, and curve calculations."""

    @pytest.fixture
    def test_config(self):
        return ExperimentConfig(
            experiment_id="test_exp_001",
            experiment_name="FraudModel_UnitTest",
            model_type="TestClassifier",
            strategy="FedAvg",
            seeds=[42],
            num_rounds=3,
            learning_rate=0.01,
            output_dir=tempfile.mkdtemp(),
        )

    @pytest.fixture
    def test_dataset(self):
        return DatasetMetadata.from_bytes(
            data=b"dummy_test_bytes",
            dataset_name="SyntheticTest",
            total_samples=100,
            num_features=5,
            fraud_samples=10,
        )

    def test_tracker_lifecycle_and_step_logging(self, test_config, test_dataset):
        with ExperimentTracker(config=test_config, dataset=test_dataset, auto_export=False) as tracker:
            assert tracker.status == "RUNNING"
            tracker.log_step(step=1, train_loss=0.65, val_loss=0.60, pr_auc=0.45, roc_auc=0.82)
            tracker.log_step(step=2, train_loss=0.55, val_loss=0.50, pr_auc=0.55, roc_auc=0.87)

        result = tracker.finalize()
        assert result.status == "COMPLETED"
        assert len(result.history) == 2
        assert result.history[0].step == 1
        assert result.history[1].train_loss == 0.55
        assert result.total_duration_seconds > 0.0
        assert result.git_commit != ""

    def test_evaluation_predictions_and_curves(self, test_config, test_dataset):
        tracker = ExperimentTracker(config=test_config, dataset=test_dataset, auto_export=False)
        tracker.start()

        # Synthetic ground truth and predictions (90 legit, 10 fraud)
        y_true = np.array([0] * 90 + [1] * 10)
        rng = np.random.default_rng(42)
        # Fraud gets higher predicted probabilities
        y_prob = np.concatenate([
            rng.uniform(0.01, 0.35, 90),
            rng.uniform(0.65, 0.99, 10),
        ])

        metrics = tracker.set_evaluation_predictions(y_true, y_prob)

        assert "roc_auc" in metrics
        assert metrics["roc_auc"] > 0.95
        assert "pr_auc" in metrics
        assert metrics["pr_auc"] > 0.75
        assert "brier_score" in metrics
        assert metrics["brier_score"] < 0.15

        # Verify curves and confusion matrix populated
        assert tracker.curves is not None
        assert len(tracker.curves.fpr) > 0
        assert len(tracker.curves.tpr) > 0
        assert len(tracker.curves.recall) > 0
        assert len(tracker.curves.precision) > 0

        assert tracker.confusion_matrix is not None
        assert tracker.confusion_matrix.tp + tracker.confusion_matrix.fn == 10
        assert tracker.confusion_matrix.tn + tracker.confusion_matrix.fp == 90

        assert tracker.calibration is not None
        assert len(tracker.calibration.prob_true) > 0


class TestExporterAndArtifactSerialization:
    """Test disk serialization to JSON, CSV, and Parquet."""

    def test_export_all_artifacts(self, tmp_path):
        cfg = ExperimentConfig(
            experiment_id="export_test_run",
            experiment_name="SerializationTest",
            model_type="TestModel",
            strategy="FedAvg",
            output_dir=str(tmp_path),
        )
        ds = DatasetMetadata.from_bytes(b"data", "Synthetic", 50, 4, 5)

        with ExperimentTracker(config=cfg, dataset=ds, auto_export=False) as tracker:
            tracker.log_step(1, train_loss=0.5, val_loss=0.4, pr_auc=0.7, roc_auc=0.9)
            tracker.set_evaluation_predictions(
                y_true=[0, 0, 0, 1, 1],
                y_prob=[0.1, 0.2, 0.15, 0.85, 0.95],
            )

        result = tracker.finalize()
        exporter = ExperimentExporter(output_root=tmp_path)
        artifacts = exporter.export_all(result)
        assert "results_json" in artifacts
        assert "traces_parquet" in artifacts

        run_dir = tmp_path / "export_test_run"
        json_file = run_dir / "results.json"
        csv_file = run_dir / "metrics.csv"
        parquet_file = run_dir / "traces.parquet"

        assert json_file.exists()
        assert csv_file.exists()
        assert parquet_file.exists()

        # Validate JSON content against Pydantic schema
        with open(json_file, encoding="utf-8") as f:
            loaded_json = json.load(f)
        reconstructed = ExperimentResult.model_validate(loaded_json)
        assert reconstructed.experiment_id == "export_test_run"
        assert len(reconstructed.history) == 1

        # Validate CSV content
        df_csv = pd.read_csv(csv_file)
        assert list(df_csv["step"]) == [1]
        assert "pr_auc" in df_csv.columns

        # Validate Parquet content
        df_parquet = pd.read_parquet(parquet_file)
        assert len(df_parquet) == 1
        assert df_parquet["experiment_id"].iloc[0] == "export_test_run"
        assert df_parquet["model_type"].iloc[0] == "TestModel"


class TestMultiSeedAggregationAndReporting:
    """Test multi-seed statistical summarization, publication plots, and dossier compilation."""

    def test_multi_seed_aggregate_summary(self, tmp_path):
        runner = ExperimentRunner(output_root=tmp_path)
        cfg = ExperimentConfig(
            experiment_id="multi_seed_test",
            experiment_name="MultiSeedTest",
            model_type="SimpleMLP",
            num_rounds=2,
            seeds=[42, 123],
            output_dir=str(tmp_path),
        )

        results, summary = runner.run_multi_seed(cfg, seeds=[42, 123])
        assert len(results) == 2
        assert isinstance(summary, AggregateSummary)
        assert summary.num_runs == 2
        assert "pr_auc" in summary.metrics
        assert "roc_auc" in summary.metrics
        assert summary.metrics["roc_auc"].mean > 0.50
        assert summary.metrics["roc_auc"].ci_95_upper >= summary.metrics["roc_auc"].ci_95_lower

        summary_file = tmp_path / "multi_seed_test" / "summary.json"
        assert summary_file.exists()

    def test_publication_plots_and_markdown_dossier(self, tmp_path):
        cfg = ExperimentConfig(
            experiment_id="dossier_test",
            experiment_name="DossierTest",
            model_type="SimpleMLP",
            num_rounds=2,
            seeds=[42],
            output_dir=str(tmp_path),
        )
        runner = ExperimentRunner(output_root=tmp_path)
        result = runner.run_single(cfg, seed=42)
        assert result.status == "COMPLETED"

        run_dir = tmp_path / "dossier_test_seed42"
        plots_dir = run_dir / "plots"

        # Verify publication figures created
        assert (plots_dir / "roc_curve.png").exists()
        assert (plots_dir / "pr_curve.png").exists()
        assert (plots_dir / "calibration_curve.png").exists()
        assert (plots_dir / "confusion_matrix.png").exists()

        # Verify Markdown dossier
        report_file = run_dir / "REPORT.md"
        assert report_file.exists()
        content = report_file.read_text(encoding="utf-8")
        assert "# Experiment Execution Dossier: `dossier_test_seed42`" in content
        assert "Hardware & Execution Environment" in content
        assert "Precision-Recall AUC (PR-AUC)" in content
        assert "Decision Confusion Matrix" in content
        assert "Training & Convergence Trajectory" in content
