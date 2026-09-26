"""Unit test suite for IEEE-CIS Fraud Detection benchmark loader, identity join, and partitioning."""

from pathlib import Path

import numpy as np
import pytest
from experiments.ieee_cis.temporal_split import IEEECISPartitioner

from app.application.services.dataloader import (
    load_dataset,
    load_ieee_cis,
    resolve_dataset_dir,
)


class TestIEEECISLoader:
    """Test suite verifying IEEE-CIS ingestion, identity join, and feature engineering."""

    def test_ieee_cis_real_dataset_loading_and_identity_join(self) -> None:
        """Verify real IEEE-CIS transaction ingestion and identity left join."""
        root = resolve_dataset_dir("ieee_cis")
        txn_csv = root / "train_transaction.csv"
        id_csv = root / "train_identity.csv"

        if not txn_csv.exists():
            pytest.skip("Physical train_transaction.csv not present on disk.")

        data = load_ieee_cis(nrows=500, require_real=True, join_identity=True)
        assert data["source"] in ("real_csv", "real_parquet")
        assert len(data["y"]) == 500
        assert data["X"].shape[0] == 500
        assert data["X"].shape[1] >= 40
        assert not np.isnan(data["X"]).any(), "Feature matrix contains NaN values"
        assert set(np.unique(data["y"])).issubset({0, 1})

        feature_names = data["feature_names"]
        assert "has_identity" in feature_names

        # Identity indicator should be binary
        has_id_idx = feature_names.index("has_identity")
        has_id_vals = data["X"][:, has_id_idx]
        assert set(np.unique(has_id_vals)).issubset({0.0, 1.0})

        # Check temporal and categorical engineered columns
        if id_csv.exists():
            assert any(c.startswith("ProductCD_") for c in feature_names)
            assert any(c.startswith("card4_") for c in feature_names)
            assert any(c.startswith("card6_") for c in feature_names)
            assert "dt_day" in feature_names
            assert "dt_hour" in feature_names

        assert "transaction_dt" in data
        assert data["transaction_dt"] is not None
        assert len(data["transaction_dt"]) == 500

    def test_ieee_cis_temporal_split_zero_leakage(self) -> None:
        """Verify temporal train/val/test splitting produces zero forward lookahead leakage."""
        split_data = load_ieee_cis(
            nrows=600,
            temporal_split=True,
            train_ratio=0.70,
            val_ratio=0.15,
            test_ratio=0.15,
        )

        assert split_data["is_strictly_chronological"] is True
        X_train = split_data["X_train"]
        X_val = split_data["X_val"]
        X_test = split_data["X_test"]

        assert len(X_train) == 420  # 70% of 600
        assert len(X_val) == 90  # 15% of 600
        assert len(X_test) == 90  # 15% of 600

        # Verify no NaNs across all splits
        assert not np.isnan(X_train).any()
        assert not np.isnan(X_val).any()
        assert not np.isnan(X_test).any()

    def test_ieee_cis_synthetic_fallback_schema_and_behavior(self, tmp_path: Path) -> None:
        """Verify synthetic mock generation when physical dataset is absent."""
        empty_dir = tmp_path / "no_ieee_cis"
        empty_dir.mkdir()

        # Strict mode must raise FileNotFoundError
        with pytest.raises(FileNotFoundError, match="Real IEEE-CIS Fraud Detection dataset files not found"):
            load_ieee_cis(path=empty_dir, nrows=500, require_real=True)

        # Non-strict mode generates synthetic mock
        mock_data = load_ieee_cis(path=empty_dir, n_mock_txns=500, require_real=False)
        assert mock_data["source"] == "mock_ieee_cis"
        assert mock_data["X"].shape[0] == 500
        assert mock_data["X"].shape[1] >= 40
        assert len(mock_data["y"]) == 500
        assert not np.isnan(mock_data["X"]).any()
        assert "transaction_dt" in mock_data
        assert len(mock_data["transaction_dt"]) == 500

    def test_ieee_cis_dataset_registry_integration(self) -> None:
        """Verify load_dataset registry resolves 'ieee_cis' and 'ieee-cis'."""
        data1 = load_dataset("ieee_cis", nrows=200)
        data2 = load_dataset("ieee-cis", nrows=200)

        assert data1["X"].shape[0] == 200
        assert data2["X"].shape[0] == 200
        assert len(data1["y"]) == 200
        assert len(data2["y"]) == 200


class TestIEEECISPartitioner:
    """Test suite verifying IEEECISPartitioner temporal splitting and Dirichlet non-IID allocation."""

    def test_partitioner_temporal_split_zero_leakage(self) -> None:
        """Verify that temporal splitting enforces max(train_dt) <= min(test_dt)."""
        partitioner = IEEECISPartitioner(
            alpha=0.5,
            num_clients=3,
            seed=42,
            test_ratio=0.20,
        )
        partitioner.load_data(nrows=500)

        assert partitioner.X_train is not None
        assert partitioner.X_test is not None
        assert partitioner.dt_train is not None
        assert partitioner.dt_test is not None

        assert len(partitioner.X_train) == 400
        assert len(partitioner.X_test) == 100

        # Exact zero lookahead leakage invariant
        train_max_dt = np.max(partitioner.dt_train)
        test_min_dt = np.min(partitioner.dt_test)
        assert train_max_dt <= test_min_dt, (
            f"Zero leakage invariant violated: max train dt {train_max_dt} > min test dt {test_min_dt}"
        )

    def test_partitioner_dirichlet_invariants(self) -> None:
        """Verify Dirichlet partitioning sample conservation, non-overlap, and non-negativity."""
        partitioner = IEEECISPartitioner(
            alpha=0.5,
            num_clients=3,
            client_names=["bank_a", "bank_b", "bank_c"],
            seed=42,
            min_samples_per_client=10,
            test_ratio=0.20,
        )
        partitioner.load_data(nrows=1000)
        client_parts = partitioner.partition_dirichlet()

        assert set(client_parts.keys()) == {"bank_a", "bank_b", "bank_c"}

        total_partitioned = 0
        all_indices: list[int] = []
        for name, (X_k, y_k) in client_parts.items():
            assert len(X_k) == len(y_k)
            assert len(y_k) >= 10, f"Client {name} has fewer than min_samples_per_client"
            total_partitioned += len(y_k)
            all_indices.extend(partitioner.client_indices[name].tolist())

        # Exact sample conservation
        n_train = len(partitioner.y_train)
        assert total_partitioned == n_train
        assert len(np.unique(all_indices)) == n_train, "Partition overlap detected"

        # Diagnostics validation
        diag = partitioner.diagnostics
        assert diag["alpha"] == 0.5
        assert diag["num_clients"] == 3
        assert diag["total_train_samples"] == n_train
        assert 0.0 <= diag["consortium_metrics"]["mean_tvd"] <= 1.0
        assert diag["consortium_metrics"]["mean_kl_divergence"] >= 0.0

    def test_partitioner_card_brand_split(self) -> None:
        """Verify card brand institutional partitioning assigns non-empty partitions."""
        partitioner = IEEECISPartitioner(num_clients=3, seed=42)
        partitioner.load_data(nrows=1000)
        brand_parts = partitioner.partition_by_card_brand()

        assert len(brand_parts) == 3
        total_brand_samples = sum(len(y_k) for _, y_k in brand_parts.values())
        assert total_brand_samples == len(partitioner.y_train)

    def test_partitioner_multi_alpha_comparison(self) -> None:
        """Verify multi-alpha comparison produces expected distribution heterogeneity scaling."""
        partitioner = IEEECISPartitioner(num_clients=3, seed=42)
        partitioner.load_data(nrows=1000)

        reports = partitioner.partition_multi_alpha([0.1, 0.5, 1.0])
        assert set(reports.keys()) == {0.1, 0.5, 1.0}

        # Alpha 0.1 should induce higher heterogeneity (KL divergence) than Alpha 1.0
        kl_01 = reports[0.1]["consortium_metrics"]["mean_kl_divergence"]
        kl_10 = reports[1.0]["consortium_metrics"]["mean_kl_divergence"]
        assert kl_01 >= kl_10 or np.isclose(kl_01, kl_10, atol=0.01)

    def test_partitioner_getters_and_summary_persistence(self, tmp_path: Path) -> None:
        """Verify get_bank_train_partitions, get_global_test, and summary JSON persistence."""
        partitioner = IEEECISPartitioner(num_clients=3, seed=42)
        partitioner.load_data(nrows=500)
        partitioner.partition_dirichlet()

        train_parts = partitioner.get_bank_train_partitions()
        assert len(train_parts) == 3

        X_test, y_test = partitioner.get_global_test()
        assert len(X_test) == 100
        assert len(y_test) == 100

        out_json = tmp_path / "ieee_cis_summary.json"
        saved_path = partitioner.save_summary(out_json)
        assert saved_path.exists()
        assert out_json.stat().st_size > 0
