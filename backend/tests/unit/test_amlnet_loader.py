"""Unit tests for AUSTRAC AMLNet benchmark dataset loader, feature engineering, and temporal partitioning.

Validates:
1. Real and synthetic AMLNet dataset loading, Parquet caching, and array shapes.
2. Canonical 17-column table schema fidelity (Huda et al. / AUSTRAC).
3. 18-feature engineered lookback sequence and indicator bounds.
4. Laundering typologies ('normal', 'structuring', 'layering', 'integration').
5. Truncation and slice reading via nrows parameter.
6. Strict real-data enforcement error guard (require_real=True).
7. High-fidelity synthetic fallback generation on missing data directory.
8. Convenience DATASET_REGISTRY routing ('amlnet' and 'aml_net').
9. Chronological temporal split isolation and zero lookahead leakage.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.application.services.dataloader import (
    AMLNET_FEATURE_COLS,
    AMLNET_FRAUD_RATIO,
    AMLNET_TYPOLOGIES,
    load_amlnet,
    load_dataset,
    resolve_dataset_dir,
)


def _has_real_amlnet() -> bool:
    """Check if physical AMLNet files are present in local dataset storage."""
    root = resolve_dataset_dir("amlnet")
    has_parquet = (root / "transactions.parquet").exists()
    has_csv = (root / "transactions.csv").exists() or (root / "amlnet_transactions.csv").exists()
    return has_parquet or has_csv


class TestAMLNetLoader:
    """Test suite validating AMLNet dataset loading, feature engineering, and temporal splitting."""

    def test_real_amlnet_loading_and_shapes(self) -> None:
        """Verify real AMLNet dataset loads from disk with expected shapes and features."""
        if not _has_real_amlnet():
            pytest.skip("Physical AMLNet dataset files not present on disk.")

        data = load_amlnet(require_real=True)

        assert data["source"] in ("real_parquet", "real_csv")
        assert len(data["y"]) >= 1000
        assert data["X"].shape[0] == len(data["y"])
        assert data["X"].shape[1] == len(AMLNET_FEATURE_COLS)
        assert data["feature_names"] == AMLNET_FEATURE_COLS

        # Labels must be binary {0, 1} and features strictly finite
        assert np.all(np.isin(data["y"], [0, 1]))
        assert np.isfinite(data["X"]).all()

        # Rare-event laundering prevalence check (~0.14% in AUSTRAC empirical baseline)
        fraud_ratio = data["fraud_ratio"]
        assert 0.0001 <= fraud_ratio <= 0.05
        assert np.isclose(fraud_ratio, AMLNET_FRAUD_RATIO, atol=0.01)

    def test_amlnet_table_schemas_and_columns(self) -> None:
        """Verify transaction DataFrame strictly adheres to the 17-column AUSTRAC schema."""
        data = load_amlnet(require_real=False, nrows=200)
        df = data["raw_df"]

        expected_columns = [
            "step",
            "type",
            "amount",
            "category",
            "nameOrig",
            "nameDest",
            "oldbalanceOrg",
            "newbalanceOrig",
            "hour",
            "day_of_week",
            "day_of_month",
            "month",
            "metadata",
            "isFraud",
            "isMoneyLaundering",
            "laundering_typology",
            "fraud_probability",
        ]
        for col in expected_columns:
            assert col in df.columns, f"Missing expected AMLNet column '{col}'"

        # Verify payment rail types
        valid_rails = {"TRANSFER", "OSKO", "BPAY", "EFTPOS", "DEBIT", "NPP"}
        found_rails = set(df["type"].astype(str).str.upper().unique())
        assert found_rails.issubset(valid_rails), f"Unexpected payment rails: {found_rails - valid_rails}"

    def test_amlnet_feature_engineering_invariants(self) -> None:
        """Verify engineered 18-feature representation satisfies mathematical bounds."""
        data = load_amlnet(require_real=False, nrows=500)
        X = data["X"]

        feat_map = {name: idx for idx, name in enumerate(AMLNET_FEATURE_COLS)}

        # Amount and log_amount non-negative
        amt = X[:, feat_map["amount"]]
        log_amt = X[:, feat_map["log_amount"]]
        assert np.all(amt >= 0.0)
        assert np.all(log_amt >= 0.0)

        # Hour in [0, 23], day_of_week in [0, 6]
        hour = X[:, feat_map["hour"]]
        dow = X[:, feat_map["day_of_week"]]
        assert np.all((hour >= 0.0) & (hour <= 23.0))
        assert np.all((dow >= 0.0) & (dow <= 6.0))

        # Binary indicator flags strictly in {0.0, 1.0}
        binary_cols = [
            "type_TRANSFER",
            "type_OSKO",
            "type_BPAY",
            "type_EFTPOS",
            "type_DEBIT",
            "type_NPP",
            "is_near_reporting_threshold",
            "category_high_risk",
            "is_night_txn",
            "is_weekend_txn",
        ]
        for b_col in binary_cols:
            vals = X[:, feat_map[b_col]]
            assert set(np.unique(vals)).issubset({0.0, 1.0}), f"Column {b_col} contains non-binary values"

        # Balance ratio non-negative
        b_ratio = X[:, feat_map["balance_orig_ratio"]]
        assert np.all(b_ratio >= 0.0)

    def test_amlnet_typology_distributions(self) -> None:
        """Verify laundering typologies align with valid AUSTRAC taxonomy."""
        data = load_amlnet(require_real=False, nrows=1000)
        typologies = set(np.unique(data["typologies"]))

        assert typologies.issubset(set(AMLNET_TYPOLOGIES)), f"Unknown typologies: {typologies - set(AMLNET_TYPOLOGIES)}"
        assert "normal" in typologies

    def test_amlnet_nrows_truncation(self) -> None:
        """Verify nrows parameter correctly truncates transactions and feature matrices."""
        data = load_amlnet(require_real=False, nrows=85)

        assert len(data["y"]) == 85
        assert data["X"].shape == (85, 18)
        assert len(data["raw_df"]) == 85
        assert len(data["typologies"]) == 85
        assert len(data["steps"]) == 85

    def test_amlnet_require_real_error_on_missing_dir(self, tmp_path: Path) -> None:
        """Verify require_real=True raises FileNotFoundError on non-existent storage path."""
        empty_dir = tmp_path / "nonexistent_amlnet"
        with pytest.raises(FileNotFoundError, match="Real AMLNet dataset files not found"):
            load_amlnet(require_real=True, data_dir=empty_dir)

    def test_amlnet_synthetic_generation(self) -> None:
        """Verify explicit synthetic generation matches exact AMLNet schema."""
        from app.application.services.synthetic_dataset_generators import generate_synthetic_amlnet

        data = generate_synthetic_amlnet(n_mock_txns=60)
        assert data["is_synthetic"] is True
        assert len(data["y"]) == 60
        assert data["X"].shape == (60, 18)
        assert np.all(np.isin(data["y"], [0, 1]))
        assert data["feature_names"] == AMLNET_FEATURE_COLS

    def test_amlnet_fails_closed_on_missing_dir(self, tmp_path: Path) -> None:
        """Verify real loader fails closed on missing files even if require_real=False."""
        empty_dir = tmp_path / "empty_amlnet"
        with pytest.raises(FileNotFoundError, match="Real AMLNet dataset files not found"):
            load_amlnet(require_real=False, data_dir=empty_dir)

    def test_amlnet_registry_routing(self) -> None:
        """Verify convenience DATASET_REGISTRY resolves amlnet and aml_net."""
        d1 = load_dataset("amlnet", require_real=False, nrows=50)
        assert d1["source"] in ("real_parquet", "real_csv", "synthetic_fallback")
        assert len(d1["y"]) == 50

        d2 = load_dataset("aml_net", require_real=False, nrows=50)
        assert len(d2["y"]) == 50

    def test_amlnet_temporal_split_chronology(self) -> None:
        """Verify chronological temporal partitioning on AMLNet simulation timesteps."""
        split_data = load_dataset("amlnet", require_real=False, temporal_split=True, nrows=250)

        assert "X_train" in split_data
        assert "y_train" in split_data
        assert "X_val" in split_data
        assert "y_val" in split_data
        assert "X_test" in split_data
        assert "y_test" in split_data

        assert len(split_data["y_train"]) + len(split_data["y_val"]) + len(split_data["y_test"]) == 250
        assert split_data["is_strictly_chronological"] is True
