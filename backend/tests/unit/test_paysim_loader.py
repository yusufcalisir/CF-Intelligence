"""Unit test suite for PaySim real-dataset loader, feature engineering, and temporal splitting."""

from pathlib import Path

import numpy as np
import pytest

from app.application.services.dataloader import (
    PAYSIM_FEATURE_COLS,
    load_dataset,
    load_paysim,
    resolve_dataset_dir,
)


class TestPaySimLoader:
    """Test suite verifying PaySim data ingestion and feature engineering."""

    def test_paysim_real_dataset_loading_if_present(self) -> None:
        """Verify real PaySim CSV ingestion when present in local storage."""
        root = resolve_dataset_dir("paysim")
        real_csv = root / "PS_20174392719_1491204439457_log.csv"

        if not real_csv.exists():
            pytest.skip("Physical PaySim dataset CSV not present on disk.")

        data = load_paysim(nrows=500, require_real=True)
        assert data["source"] == "real_csv"
        assert data["X"].shape == (500, len(PAYSIM_FEATURE_COLS))
        assert len(data["y"]) == 500
        assert data["feature_names"] == PAYSIM_FEATURE_COLS
        assert 0.0 <= data["fraud_ratio"] <= 1.0
        assert "steps" in data
        assert data["steps"] is not None

    def test_paysim_feature_engineering_equations(self) -> None:
        """Verify accounting error features and one-hot encoding consistency."""
        data = load_paysim(nrows=1000)
        X = data["X"]
        feats = data["feature_names"]

        amt_idx = feats.index("amount")
        old_orig_idx = feats.index("oldbalanceOrg")
        new_orig_idx = feats.index("newbalanceOrig")
        old_dest_idx = feats.index("oldbalanceDest")
        new_dest_idx = feats.index("newbalanceDest")
        err_orig_idx = feats.index("errorBalanceOrig")
        err_dest_idx = feats.index("errorBalanceDest")

        # Mathematical equation: errorBalanceOrig = newbalanceOrig + amount - oldbalanceOrg
        # Note: atol=1.0 accounts for 32-bit float truncation on large currency amounts
        expected_err_orig = X[:, new_orig_idx] + X[:, amt_idx] - X[:, old_orig_idx]
        np.testing.assert_allclose(X[:, err_orig_idx], expected_err_orig, rtol=1e-3, atol=1.0)

        # Mathematical equation: errorBalanceDest = oldbalanceDest + amount - newbalanceDest
        expected_err_dest = X[:, old_dest_idx] + X[:, amt_idx] - X[:, new_dest_idx]
        np.testing.assert_allclose(X[:, err_dest_idx], expected_err_dest, rtol=1e-3, atol=1.0)

        # One-hot encoded transaction types sum to 1.0 for each row
        type_indices = [
            feats.index("type_TRANSFER"),
            feats.index("type_CASH_OUT"),
            feats.index("type_PAYMENT"),
            feats.index("type_DEBIT"),
            feats.index("type_CASH_IN"),
        ]
        type_sum = np.sum(X[:, type_indices], axis=1)
        np.testing.assert_allclose(type_sum, 1.0, rtol=1e-5, atol=1e-5)

    def test_paysim_temporal_split_zero_leakage(self) -> None:
        """Verify temporal train/val/test splitting produces zero forward leakage."""
        split_data = load_paysim(
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

        # Step is column 0
        train_steps = X_train[:, 0]
        val_steps = X_val[:, 0]
        test_steps = X_test[:, 0]

        assert len(X_train) == 420  # 70% of 600
        assert len(X_val) == 90  # 15% of 600
        assert len(X_test) == 90  # 15% of 600

        # Monotonic temporal progression
        assert np.max(train_steps) <= np.min(val_steps)
        assert np.max(val_steps) <= np.min(test_steps)

    def test_paysim_synthetic_fallback_matches_schema(self, tmp_path: Path) -> None:
        """Verify mock fallback reproduces identical 13-feature schema when real data is absent."""
        empty_dir = tmp_path / "no_paysim"
        empty_dir.mkdir()

        data = load_paysim(path=empty_dir, n_mock_txns=300, require_real=False)
        assert data["source"] == "mock_mpesa"
        assert data["X"].shape == (300, 13)
        assert len(data["y"]) == 300
        assert data["feature_names"] == PAYSIM_FEATURE_COLS
        assert set(np.unique(data["y"])).issubset({0, 1})

    def test_paysim_require_real_raises_when_missing(self, tmp_path: Path) -> None:
        """Verify FileNotFoundError is raised when require_real=True and files are absent."""
        empty_dir = tmp_path / "empty_vault"
        empty_dir.mkdir()

        with pytest.raises(FileNotFoundError, match="Real PaySim dataset files not found"):
            load_paysim(path=empty_dir, require_real=True)

    def test_paysim_via_load_dataset_registry(self) -> None:
        """Verify load_dataset('paysim') correctly loads with temporal splitting."""
        res = load_dataset("paysim", nrows=200, temporal_split=True)
        assert "X_train" in res
        assert "X_test" in res
        assert res["is_strictly_chronological"] is True
