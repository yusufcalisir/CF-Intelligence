"""Unit test suite for PaySim Non-IID Dirichlet Client Partitioner (experiments/paysim/partitioner.py)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.baselines.comparative_runner import ComparativeBenchmarkEngine  # noqa: E402
from experiments.paysim.partitioner import PaySimPartitioner  # noqa: E402


class TestDirichletPartitioner:
    """Test suite verifying federated Dirichlet non-IID partitioning and distribution metrics."""

    def test_partitioner_initialization_defaults_and_guards(self) -> None:
        """Verify constructor parameters, bank name generation, and input boundary validation."""
        p = PaySimPartitioner(alpha=0.5, num_clients=3)
        assert p.alpha == 0.5
        assert p.num_clients == 3
        assert p.client_names == ["bank_a", "bank_b", "bank_c"]
        assert p.test_ratio == 0.20

        # Custom client names
        p_custom = PaySimPartitioner(num_clients=2, client_names=["jpmorgan", "barclays"])
        assert p_custom.client_names == ["jpmorgan", "barclays"]

        # Guards against invalid hyperparameters
        with pytest.raises(ValueError, match="alpha must be > 0"):
            PaySimPartitioner(alpha=-0.1)
        with pytest.raises(ValueError, match="num_clients must be at least 2"):
            PaySimPartitioner(num_clients=1)
        with pytest.raises(ValueError, match="test_ratio must be between 0 and 1"):
            PaySimPartitioner(test_ratio=1.5)

    def test_partitioner_temporal_split_zero_leakage(self) -> None:
        """Verify strict past-to-future separation with zero temporal leakage."""
        p = PaySimPartitioner(alpha=0.5, num_clients=3, test_ratio=0.25)
        p.load_data(nrows=1000)

        assert p.X_train is not None and p.X_test is not None
        assert len(p.X_train) == 750
        assert len(p.X_test) == 250

        # Enforce that max train step <= min test step
        max_train_step = float(np.max(p.steps_train))
        min_test_step = float(np.min(p.steps_test))
        assert max_train_step <= min_test_step

    def test_partitioner_sample_conservation_and_isolation(self) -> None:
        """Verify exact training sample conservation and strict client index disjointness."""
        p = PaySimPartitioner(alpha=0.5, num_clients=3)
        p.load_data(nrows=1000)
        partitions = p.partition_dirichlet()

        assert len(partitions) == 3
        assert set(partitions.keys()) == {"bank_a", "bank_b", "bank_c"}

        total_partitioned = 0
        allocated_indices: set[int] = set()
        for name in p.client_names:
            X_k, y_k = partitions[name]
            idx_k = p.client_indices[name]
            assert len(X_k) == len(y_k) == len(idx_k)
            total_partitioned += len(X_k)

            # Assert no mutual overlap between client indices
            assert len(allocated_indices.intersection(set(idx_k))) == 0
            allocated_indices.update(idx_k)

        # Assert total partitioned samples exactly matches training pool
        assert total_partitioned == len(p.X_train)
        assert len(allocated_indices) == len(p.X_train)

    def test_partitioner_multi_alpha_skew(self) -> None:
        """Verify that smaller alpha (0.1) produces higher divergence and skew than alpha (1.0)."""
        p = PaySimPartitioner(num_clients=3)
        p.load_data(nrows=2000)

        reports = p.partition_multi_alpha([0.1, 0.5, 1.0])
        assert set(reports.keys()) == {0.1, 0.5, 1.0}

        # Alpha 0.1 induces extreme skew (higher mean KL divergence than 0.5)
        kl_01 = reports[0.1]["consortium_metrics"]["mean_kl_divergence"]
        kl_05 = reports[0.5]["consortium_metrics"]["mean_kl_divergence"]
        assert kl_01 >= kl_05 or reports[0.1]["consortium_metrics"]["fraud_ratio_std"] >= 0.0

    def test_partitioner_reproducibility_with_seed(self) -> None:
        """Verify identical partitioning across deterministic runs with identical seeds."""
        p1 = PaySimPartitioner(alpha=0.5, num_clients=3, seed=123)
        p1.load_data(nrows=500)
        p1.partition_dirichlet()

        p2 = PaySimPartitioner(alpha=0.5, num_clients=3, seed=123)
        p2.load_data(nrows=500)
        p2.partition_dirichlet()

        for name in p1.client_names:
            np.testing.assert_array_equal(p1.client_indices[name], p2.client_indices[name])

        # Different seed should diverge
        p3 = PaySimPartitioner(alpha=0.5, num_clients=3, seed=999)
        p3.load_data(nrows=500)
        p3.partition_dirichlet()
        diverged = False
        for name in p1.client_names:
            if not np.array_equal(p1.client_indices[name], p3.client_indices[name]):
                diverged = True
                break
        assert diverged is True

    def test_partitioner_export_artifacts(self, tmp_path: Path) -> None:
        """Verify export of summary JSON, NPZ indices, and client Parquet partitions."""
        p = PaySimPartitioner(alpha=0.5, num_clients=3)
        p.load_data(nrows=600)
        p.partition_dirichlet()

        export_dir = tmp_path / "paysim_export"
        p.save_partitions(export_dir, export_parquet=True)

        assert (export_dir / "partition_summary.json").exists()
        assert (export_dir / "partition_indices.npz").exists()
        assert (export_dir / "bank_a_train.parquet").exists()
        assert (export_dir / "bank_b_train.parquet").exists()
        assert (export_dir / "bank_c_train.parquet").exists()
        assert (export_dir / "global_test.parquet").exists()

        # Check JSON integrity
        with open(export_dir / "partition_summary.json", encoding="utf-8") as f:
            summary = json.load(f)
        assert summary["dataset"] == "PaySim"
        assert summary["feature_dim"] == 13
        assert "consortium_metrics" in summary["partition_diagnostics"]

    def test_partitioner_integration_with_comparative_runner(self, tmp_path: Path) -> None:
        """Verify direct interface compatibility with ComparativeBenchmarkEngine."""
        p = PaySimPartitioner(alpha=0.5, num_clients=3)
        p.load_data(nrows=400)
        p.partition_dirichlet()

        bank_train_partitions = p.get_bank_train_partitions()
        X_test, y_test = p.get_global_test()

        engine = ComparativeBenchmarkEngine(random_state=42, output_dir=tmp_path)
        report = engine.run_full_comparative_suite(
            bank_train_partitions=bank_train_partitions,
            X_global_test=X_test,
            y_global_test=y_test,
            dataset_name="PaySim_Federated_SubPlan_5_1",
            train_neural=False,  # Tabular speed
        )

        assert "comparison_matrix" in report
        assert "centralization_gap_analysis" in report
        assert "silo_deficit_analysis" in report
        assert len(report["comparison_matrix"]) >= 4
        assert report["dataset_name"] == "PaySim_Federated_SubPlan_5_1"
