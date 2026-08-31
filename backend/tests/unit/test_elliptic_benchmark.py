"""Unit tests for Elliptic Benchmark Service."""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.application.services.elliptic_benchmark_service import EllipticBenchmarkService


def test_elliptic_benchmark_service_execution():
    service = EllipticBenchmarkService()
    results = service.run_benchmark(n_samples=500, random_seed=42)

    assert "dataset" in results
    assert "metrics" in results
    assert "federated_graph_pipeline" in results["metrics"]
    assert "isolated_single_bank_baseline" in results["metrics"]
    assert "federated_advantage" in results["metrics"]

    fed = results["metrics"]["federated_graph_pipeline"]
    assert 0.0 <= fed["roc_auc"] <= 1.0
    assert 0.0 <= fed["pr_auc"] <= 1.0
    assert 0.0 <= fed["recall_at_01_fpr"] <= 1.0

    with tempfile.TemporaryDirectory() as tmp_dir:
        report_path = service.save_report(results, output_dir=Path(tmp_dir))
        assert report_path.exists()
        assert (Path(tmp_dir) / "benchmark_report.json").exists()
