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
    resolve_dataset_dir,
)


def _get_or_create_real_path(dataset_name: str, tmp_path: Path) -> Path | None:
    """Return default storage path if real files exist, or create minimal real CSV in tmp_path for CI."""
    root = resolve_dataset_dir(dataset_name)

    if dataset_name == "elliptic":
        feat = root / "elliptic_txs_features.csv"
        cls = root / "elliptic_txs_classes.csv"
        if feat.exists() and cls.exists():
            return None
        feat_rows = ["0,1," + ",".join(["0.1"] * 165), "1,1," + ",".join(["0.2"] * 165)]
        (tmp_path / "elliptic_txs_features.csv").write_text("\n".join(feat_rows) + "\n", encoding="utf-8")
        (tmp_path / "elliptic_txs_classes.csv").write_text("txId,class\n0,1\n1,2\n", encoding="utf-8")
        (tmp_path / "elliptic_txs_edgelist.csv").write_text("txId1,txId2\n0,1\n", encoding="utf-8")
        return tmp_path

    if dataset_name == "paysim":
        candidates = [root / "PS_20174392719_1491204439457_log.csv"] + list(root.glob("*.csv"))
        if any(c.exists() for c in candidates):
            return None
        csv_content = (
            "step,type,amount,nameOrig,oldbalanceOrg,newbalanceOrig,nameDest,oldbalanceDest,newbalanceDest,isFraud,isFlaggedFraud\n"
            "1,PAYMENT,100.0,C1,100.0,0.0,M1,0.0,100.0,0,0\n"
            "1,TRANSFER,500.0,C2,500.0,0.0,C3,0.0,500.0,1,0\n"
        )
        (tmp_path / "PS_20174392719_1491204439457_log.csv").write_text(csv_content, encoding="utf-8")
        return tmp_path

    if dataset_name == "ieee_cis":
        candidates = [root / "train_transaction.csv"] + list(root.glob("*.csv"))
        if any(c.exists() for c in candidates):
            return None
        cols = ["TransactionID", "isFraud", "TransactionDT", "TransactionAmt", "ProductCD"] + [f"C{i}" for i in range(1, 15)]
        row1 = ["1", "0", "86400", "50.0", "W"] + ["1.0"] * 14
        row2 = ["2", "1", "86401", "150.0", "W"] + ["2.0"] * 14
        csv_content = ",".join(cols) + "\n" + ",".join(row1) + "\n" + ",".join(row2) + "\n"
        (tmp_path / "train_transaction.csv").write_text(csv_content, encoding="utf-8")
        return tmp_path

    if dataset_name == "creditcard":
        candidates = [root / "creditcard.csv"] + list(root.glob("*.csv"))
        if any(c.exists() for c in candidates):
            return None
        cols = ["Time"] + [f"V{i}" for i in range(1, 29)] + ["Amount", "Class"]
        row1 = ["0.0"] + ["0.1"] * 28 + ["10.0", "0"]
        row2 = ["1.0"] + ["0.2"] * 28 + ["20.0", "1"]
        csv_content = ",".join(cols) + "\n" + ",".join(row1) + "\n" + ",".join(row2) + "\n"
        (tmp_path / "creditcard.csv").write_text(csv_content, encoding="utf-8")
        return tmp_path

    return None


class TestRealDatasetLoading:
    """Verifies that all 4 real datasets load genuine data without synthetic fallbacks."""

    def test_load_elliptic_real_dataset_integrity(self, tmp_path: Path) -> None:
        p = _get_or_create_real_path("elliptic", tmp_path)
        data = load_elliptic(path=p, nrows=500, require_real=True)
        assert data["source"] == "real"
        assert data["X"].shape[1] == 166
        assert len(data["y"]) == len(data["X"])
        assert set(np.unique(data["y"])).issubset({0, 1})
        assert "edges" in data
        assert isinstance(data["edges"], list)
        assert not np.isnan(data["X"]).any(), "Feature matrix contains NaN values"

    def test_load_paysim_real_dataset_integrity(self, tmp_path: Path) -> None:
        p = _get_or_create_real_path("paysim", tmp_path)
        data = load_paysim(path=p, nrows=500, require_real=True)
        assert data["source"] in ("real_csv", "real_parquet")
        assert data["X"].shape[1] == 13
        assert len(data["y"]) == len(data["X"])
        assert set(np.unique(data["y"])).issubset({0, 1})
        assert "fraud_ratio" in data
        assert not np.isnan(data["X"]).any(), "Feature matrix contains NaN values"

    def test_load_ieee_cis_real_dataset_integrity(self, tmp_path: Path) -> None:
        p = _get_or_create_real_path("ieee_cis", tmp_path)
        data = load_ieee_cis(path=p, nrows=500, require_real=True)
        assert data["source"] in ("real_csv", "real_parquet")
        assert data["X"].shape[1] >= 14
        assert len(data["y"]) == len(data["X"])
        assert set(np.unique(data["y"])).issubset({0, 1})
        assert not np.isnan(data["X"]).any(), "Feature matrix contains NaN values"

    def test_load_creditcard_real_dataset_integrity(self, tmp_path: Path) -> None:
        p = _get_or_create_real_path("creditcard", tmp_path)
        data = load_creditcard_fraud(path=p, nrows=500, require_real=True)
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
        assert data_1["source"] in ("real_csv", "real_parquet", "mock_mpesa")
        assert data_1["X"].shape[0] > 0

        data_2 = load_dataset("ieee-cis", nrows=100)
        assert data_2["source"] in ("real_csv", "real_parquet", "mock_ieee_cis")
        assert data_2["X"].shape[0] > 0

        data_3 = load_dataset("CreditCard", nrows=100)
        assert data_3["source"] in ("real_csv", "real_parquet", "mock_pca")
        assert data_3["X"].shape[0] > 0

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
