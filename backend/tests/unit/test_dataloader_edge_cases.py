"""Comprehensive Unit Tests for Real Dataloaders & Edge Cases (Sub-Plan 0.3).

Verifies:
1. Real dataset loading for all 4 available datasets (PaySim, IEEE-CIS, CreditCard, Elliptic)
2. Strict mode (require_real=True) enforcement and FileNotFoundError triggers
3. Dirichlet non-IID partitioning invariants, validation guards, and edge cases
"""

from pathlib import Path

import numpy as np
import pytest

from app.application.services.dataloader import (
    load_creditcard_fraud,
    load_dataset,
    load_elliptic,
    load_ieee_cis,
    load_paysim,
    partition_dataset_non_iid,
)


class TestRealDatasetLoading:
    """Verifies that all 4 local real datasets load genuine data without synthetic fallbacks."""

    def test_load_elliptic_real_dataset_integrity(self) -> None:
        data = load_elliptic(nrows=500, require_real=True)
        assert data["source"] == "real"
        assert data["X"].shape[1] == 166
        assert len(data["y"]) == len(data["X"])
        assert set(np.unique(data["y"])).issubset({0, 1})
        assert "edges" in data
        assert isinstance(data["edges"], list)
        assert not np.isnan(data["X"]).any(), "Feature matrix contains NaN values"

    def test_load_paysim_real_dataset_integrity(self) -> None:
        data = load_paysim(nrows=500, require_real=True)
        assert data["source"] in ("real_csv", "real_parquet")
        assert data["X"].shape[1] == 13
        assert len(data["y"]) == len(data["X"])
        assert set(np.unique(data["y"])).issubset({0, 1})
        assert "fraud_ratio" in data
        assert not np.isnan(data["X"]).any(), "Feature matrix contains NaN values"

    def test_load_ieee_cis_real_dataset_integrity(self) -> None:
        data = load_ieee_cis(nrows=500, require_real=True)
        assert data["source"] in ("real_csv", "real_parquet")
        assert data["X"].shape[1] > 300
        assert len(data["y"]) == len(data["X"])
        assert set(np.unique(data["y"])).issubset({0, 1})
        assert not np.isnan(data["X"]).any(), "Feature matrix contains NaN values"

    def test_load_creditcard_real_dataset_integrity(self) -> None:
        data = load_creditcard_fraud(nrows=500, require_real=True)
        assert data["source"] in ("real_csv", "real_parquet")
        assert data["X"].shape[1] == 29
        assert len(data["y"]) == len(data["X"])
        assert set(np.unique(data["y"])).issubset({0, 1})
        assert not np.isnan(data["X"]).any(), "Feature matrix contains NaN values"


class TestStrictRealModeGuards:
    """Verifies that synthetic mock fallback is forbidden when require_real=True."""

    def test_strict_mode_raises_on_missing_amlsim(self) -> None:
        with pytest.raises(FileNotFoundError, match="Real AMLSim dataset export not found"):
            load_dataset("amlsim", require_real=True)

    def test_strict_mode_raises_on_nonexistent_custom_path(self, tmp_path: Path) -> None:
        empty_dir = tmp_path / "empty_dataset"
        empty_dir.mkdir()
        with pytest.raises(FileNotFoundError, match="Real PaySim dataset files not found"):
            load_paysim(path=empty_dir, require_real=True)

    def test_load_dataset_case_insensitive_and_hyphen_tolerant(self) -> None:
        data_1 = load_dataset("PaySim", nrows=100)
        assert data_1["source"] in ("real_csv", "real_parquet")

        data_2 = load_dataset("ieee-cis", nrows=100)
        assert data_2["source"] in ("real_csv", "real_parquet")

        data_3 = load_dataset("CreditCard", nrows=100)
        assert data_3["source"] in ("real_csv", "real_parquet")

    def test_load_dataset_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown dataset 'invalid_name'"):
            load_dataset("invalid_name")


class TestNonIIDPartitioningEdgeCases:
    """Verifies Dirichlet distribution Dir(alpha) partitioning edge cases and boundary guards."""

    def test_partition_empty_dataset_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot partition empty dataset"):
            partition_dataset_non_iid(np.empty((0, 5)), np.empty((0,)))

    def test_partition_length_mismatch_raises(self) -> None:
        X = np.ones((10, 5))
        y = np.ones(8)
        with pytest.raises(ValueError, match="Length mismatch between features X"):
            partition_dataset_non_iid(X, y)

    def test_partition_invalid_num_banks_raises(self) -> None:
        X = np.ones((10, 5))
        y = np.zeros(10)
        with pytest.raises(ValueError, match="num_banks must be at least 1"):
            partition_dataset_non_iid(X, y, num_banks=0)

    def test_partition_invalid_alpha_raises(self) -> None:
        X = np.ones((10, 5))
        y = np.zeros(10)
        with pytest.raises(ValueError, match="Dirichlet concentration parameter alpha must be strictly positive"):
            partition_dataset_non_iid(X, y, alpha=-0.5)

    def test_partition_extreme_alpha_skew(self) -> None:
        rng = np.random.default_rng(101)
        X = rng.standard_normal((1000, 10))
        y = (rng.random(1000) < 0.1).astype(int)

        # Extreme non-IID: alpha = 0.05
        skewed_parts = partition_dataset_non_iid(X, y, num_banks=4, alpha=0.05, seed=123)
        assert len(skewed_parts) == 4
        assert sum(p["n_samples"] for p in skewed_parts) == 1000

        # Uniform homogeneous: alpha = 100.0
        uniform_parts = partition_dataset_non_iid(X, y, num_banks=4, alpha=100.0, seed=123)
        assert len(uniform_parts) == 4
        assert sum(p["n_samples"] for p in uniform_parts) == 1000
        # All banks receive samples
        for p in uniform_parts:
            assert p["n_samples"] > 0

    def test_partition_rare_singleton_class(self) -> None:
        # Exactly 1 fraud sample among 500 records
        X = np.zeros((500, 4))
        y = np.zeros(500, dtype=int)
        y[42] = 1

        parts = partition_dataset_non_iid(X, y, num_banks=3, alpha=0.5, seed=99)
        assert len(parts) == 3
        assert sum(p["n_samples"] for p in parts) == 500
        total_fraud = sum(p["fraud_count"] for p in parts)
        assert total_fraud == 1
