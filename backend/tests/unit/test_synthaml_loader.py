"""Unit Tests for SynthAML Dataset Loader and Feature Engineering.

Validates:
1. Real SynthAML dataset loading, Parquet caching, and tensor/array shapes.
2. Canonical alert and transaction schema column fidelity (Spar Nord / Nature Sci Data 2023).
3. 14-feature lookback sequence aggregation pipeline.
4. Class balance and label fidelity (OUTCOME binary values in {0, 1}).
5. Slice reading and row truncation (nrows argument).
6. Strict real-data enforcement error guard (require_real=True).
7. High-fidelity synthetic fallback generation on missing data directory.
8. DATASET_REGISTRY routing ('synthaml' and 'synth_aml').
9. Chronological temporal split isolation and absence of future lookahead leakage.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.application.services.dataloader import (
    SYNTHAML_FEATURE_COLS,
    SYNTHAML_FRAUD_RATIO,
    load_dataset,
    load_synthaml,
)


class TestSynthAMLLoader:
    """Validates real and mock ingestion of the SynthAML benchmark dataset."""

    def test_real_synthaml_loading_and_shapes(self) -> None:
        """Verify real SynthAML dataset loads from disk with expected shapes and features."""
        data = load_synthaml(require_real=True)

        assert data["source"] in ("real_parquet", "real_csv")
        assert len(data["y"]) >= 1000
        assert data["X"].shape[0] == len(data["y"])
        assert data["X"].shape[1] == len(SYNTHAML_FEATURE_COLS)
        assert data["feature_names"] == SYNTHAML_FEATURE_COLS

        # Check binary labels and finite numeric features
        assert np.all(np.isin(data["y"], [0, 1]))
        assert np.isfinite(data["X"]).all()

        # Class ratio check (~8.5% reported SARs in Spar Nord empirical baseline)
        fraud_ratio = data["fraud_ratio"]
        assert 0.03 <= fraud_ratio <= 0.20
        assert np.isclose(fraud_ratio, SYNTHAML_FRAUD_RATIO, atol=0.04)

    def test_synthaml_table_schemas_and_columns(self) -> None:
        """Verify alert and transaction DataFrames adhere strictly to Spar Nord schema."""
        data = load_synthaml(require_real=True, nrows=200)

        alerts_df = data["alerts_df"]
        tx_df = data["transactions_df"]

        # Alert table schema
        for col in ["ALERT_ID", "DATE", "TIMESTAMP", "OUTCOME"]:
            assert col in alerts_df.columns, f"Missing alert column '{col}'"

        # Transaction table schema
        for col in ["TRANSACTION_ID", "ALERT_ID", "TIMESTAMP", "ENTRY", "TYPE", "SIZE", "AMOUNT_DKK"]:
            assert col in tx_df.columns, f"Missing transaction column '{col}'"

        # Channels and entries
        valid_channels = {"card", "cash", "international", "wire"}
        found_channels = set(tx_df["TYPE"].astype(str).str.lower().unique())
        assert found_channels.issubset(valid_channels)

        valid_entries = {"credit", "debit"}
        found_entries = set(tx_df["ENTRY"].astype(str).str.lower().unique())
        assert found_entries.issubset(valid_entries)

    def test_synthaml_feature_engineering_invariants(self) -> None:
        """Verify aggregated alert features adhere to mathematical bounds."""
        data = load_synthaml(require_real=True, nrows=300)
        X = data["X"]

        # Feature index mapping
        feat_map = {name: idx for idx, name in enumerate(SYNTHAML_FEATURE_COLS)}

        # Transaction count >= 1
        n_tx = X[:, feat_map["n_transactions"]]
        assert np.all(n_tx >= 1.0)

        # Ratios must be bounded in [0, 1]
        for ratio_col in ["credit_ratio", "card_ratio", "cash_ratio", "international_ratio", "wire_ratio"]:
            r = X[:, feat_map[ratio_col]]
            assert np.all(r >= 0.0)
            assert np.all(r <= 1.0001)

        # Window days must be positive
        w_days = X[:, feat_map["window_days"]]
        assert np.all(w_days > 0.0)

        # Frequency must be non-negative
        freq = X[:, feat_map["tx_frequency_per_day"]]
        assert np.all(freq >= 0.0)

    def test_synthaml_nrows_truncation(self) -> None:
        """Verify nrows parameter correctly truncates alerts and transaction slices."""
        data = load_synthaml(require_real=True, nrows=75)

        assert len(data["y"]) == 75
        assert data["X"].shape == (75, 14)
        assert len(data["alerts_df"]) == 75

        # Check transactions strictly belong to the 75 alerts
        alert_ids = set(data["alerts_df"]["ALERT_ID"].values)
        tx_alert_ids = set(data["transactions_df"]["ALERT_ID"].values)
        assert tx_alert_ids.issubset(alert_ids)

    def test_synthaml_require_real_error_on_missing_dir(self, tmp_path: Path) -> None:
        """Verify require_real=True raises FileNotFoundError on non-existent storage path."""
        empty_dir = tmp_path / "nonexistent_synthaml"
        with pytest.raises(FileNotFoundError, match="Real SynthAML dataset files not found"):
            load_synthaml(require_real=True, data_dir=empty_dir)

    def test_synthaml_synthetic_fallback_generation(self, tmp_path: Path) -> None:
        """Verify synthetic fallback generates high-fidelity mock matching exact schema."""
        empty_dir = tmp_path / "empty_synthaml"
        data = load_synthaml(require_real=False, data_dir=empty_dir, n_mock_alerts=50, seed=123)

        assert data["source"] == "synthetic_fallback"
        assert len(data["y"]) == 50
        assert data["X"].shape == (50, 14)
        assert np.all(np.isin(data["y"], [0, 1]))
        assert data["feature_names"] == SYNTHAML_FEATURE_COLS

    def test_synthaml_registry_routing(self) -> None:
        """Verify convenience DATASET_REGISTRY resolves synthaml and synth_aml."""
        d1 = load_dataset("synthaml", require_real=True, nrows=50)
        assert d1["source"] in ("real_parquet", "real_csv")
        assert len(d1["y"]) == 50

        d2 = load_dataset("synth_aml", require_real=True, nrows=50)
        assert len(d2["y"]) == 50

    def test_synthaml_temporal_split_chronology(self) -> None:
        """Verify chronological temporal partitioning on SynthAML alert dates."""
        split_data = load_dataset("synthaml", require_real=True, temporal_split=True, nrows=200)

        assert "X_train" in split_data
        assert "y_train" in split_data
        assert "X_val" in split_data
        assert "y_val" in split_data
        assert "X_test" in split_data
        assert "y_test" in split_data

        assert len(split_data["y_train"]) + len(split_data["y_val"]) + len(split_data["y_test"]) == 200
        assert split_data["is_strictly_chronological"] is True
