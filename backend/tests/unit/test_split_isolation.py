"""Unit tests for Data Preprocessor and Temporal Split Isolation.

Verifies:
- Strict train-split isolation (parameters learned only from training partition).
- Handling of unseen test categorical tokens (mapped to zero-vector without crashing).
- Zero data snooping (outliers/distributions in test have 0% effect on train statistics).
- Chronological temporal ordering (t_train_max <= t_val_min <= t_test_min).
- Full state serialization and deserialization fidelity.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.application.services.dataloader import load_paysim, temporal_split_dataset
from app.application.services.feature_service import FeatureService
from app.application.services.preprocessor import DataPreprocessor, PreprocessingStateError


class TestDataPreprocessorIsolation:
    """Test suite ensuring zero-leakage preprocessing and split isolation."""

    def test_preprocessor_not_fitted_raises_error(self) -> None:
        """Calling transform before fit must raise PreprocessingStateError."""
        prep = DataPreprocessor()
        df = pd.DataFrame({"col1": [1.0, 2.0, 3.0]})
        with pytest.raises(PreprocessingStateError, match="must be fitted before transform"):
            prep.transform(df)

    def test_preprocessor_fit_transform_strict_isolation(self) -> None:
        """Preprocessor statistics must derive strictly from training set."""
        train_data = pd.DataFrame({
            "amount": [100.0, 200.0, 300.0],
            "balance": [1000.0, 2000.0, 3000.0],
        })
        test_data = pd.DataFrame({
            "amount": [999999.0, 888888.0],  # Extreme outliers in test set
            "balance": [5000.0, 6000.0],
        })

        prep = DataPreprocessor(numeric_strategy="standardize", impute_strategy="mean")
        X_train_transformed = prep.fit_transform(train_data)

        # Means and stds must reflect ONLY train_data
        assert prep.means_["amount"] == pytest.approx(200.0)
        assert prep.means_["balance"] == pytest.approx(2000.0)
        assert prep.stds_["amount"] > 0
        assert prep.stds_["balance"] > 0

        # Transforming test data must use train statistics without mutating them
        X_test_transformed = prep.transform(test_data)

        # Verify mean of amount has NOT changed
        assert prep.means_["amount"] == pytest.approx(200.0)
        assert X_train_transformed.shape == (3, 2)
        assert X_test_transformed.shape == (2, 2)

    def test_unseen_categorical_vocabulary_isolation(self) -> None:
        """Categories present only in test set must be mapped to zero-vector without leaking."""
        train_df = pd.DataFrame({
            "amount": [10.0, 20.0, 30.0],
            "channel": ["MOBILE", "WEB", "MOBILE"],
        })
        test_df = pd.DataFrame({
            "amount": [40.0, 50.0],
            "channel": ["ATM", "BRANCH"],  # Completely unseen in train
        })

        prep = DataPreprocessor()
        X_train = prep.fit_transform(train_df)
        X_test = prep.transform(test_df)

        assert "MOBILE" in prep.categories_["channel"]
        assert "WEB" in prep.categories_["channel"]
        assert "ATM" not in prep.categories_["channel"]
        assert "BRANCH" not in prep.categories_["channel"]

        # Number of output features: 1 numeric ("amount") + 2 categories ("MOBILE", "WEB")
        assert prep.feature_names_out_ == ["amount", "channel_MOBILE", "channel_WEB"]
        assert X_train.shape[1] == 3
        assert X_test.shape[1] == 3

        # Test rows for ATM and BRANCH must have zeros for all train category indicators
        assert np.all(X_test[:, 1:] == 0.0)

    def test_preprocessor_outlier_clipping(self) -> None:
        """Outlier clipping bounds transformed values to clip_std_factor."""
        train_df = pd.DataFrame({"feat": [0.0, 1.0, 2.0, 3.0, 4.0]})
        test_df = pd.DataFrame({"feat": [10000.0, -10000.0]})

        prep = DataPreprocessor(numeric_strategy="standardize", clip_outliers=True, clip_std_factor=5.0)
        prep.fit(train_df)
        X_test = prep.transform(test_df)

        assert np.all(X_test <= 5.0)
        assert np.all(X_test >= -5.0)

    def test_serialization_fidelity(self) -> None:
        """Serialized preprocessor state must reconstitute identical transformation behavior."""
        df = pd.DataFrame({
            "amount": [100.0, 200.0, np.nan, 400.0],
            "category": ["TRANSFER", "CASH_OUT", "TRANSFER", "PAYMENT"],
        })

        prep = DataPreprocessor(numeric_strategy="minmax", impute_strategy="median")
        X_transformed_orig = prep.fit_transform(df)

        state_dict = prep.to_dict()
        restored_prep = DataPreprocessor.from_dict(state_dict)

        X_transformed_restored = restored_prep.transform(df)
        np.testing.assert_allclose(X_transformed_orig, X_transformed_restored, rtol=1e-5)


class TestTemporalSplitIsolation:
    """Test suite ensuring chronological arrow of time is enforced without lookahead."""

    def test_temporal_split_monotonic_order(self) -> None:
        """Verifies t_train_max <= t_val_min <= t_test_min."""
        n_samples = 100
        df = pd.DataFrame({
            "step": np.random.default_rng(42).permutation(np.arange(n_samples)),
            "amount": np.random.default_rng(42).uniform(10, 500, size=n_samples),
            "isFraud": (np.random.default_rng(42).random(n_samples) < 0.1).astype(int),
        })

        train_df, val_df, test_df, meta = FeatureService.temporal_split(
            df,
            time_col="step",
            train_ratio=0.70,
            val_ratio=0.15,
            test_ratio=0.15,
            label_col="isFraud",
        )

        assert meta.is_strictly_chronological is True
        assert len(train_df) == 70
        assert len(val_df) == 15
        assert len(test_df) == 15

        # Check temporal order boundaries
        assert meta.train_time_max is not None and meta.val_time_min is not None
        assert meta.val_time_max is not None and meta.test_time_min is not None
        assert float(meta.train_time_max) <= float(meta.val_time_min)
        assert float(meta.val_time_max) <= float(meta.test_time_min)

        # Check internal ascending order
        assert train_df["step"].is_monotonic_increasing
        assert val_df["step"].is_monotonic_increasing
        assert test_df["step"].is_monotonic_increasing

    def test_temporal_split_boundary_overlap_detection(self) -> None:
        """Boundary overlap is flagged when boundary timestamps match across partitions."""
        # 10 records all at timestamp 5.0
        df = pd.DataFrame({
            "step": [5.0] * 10,
            "amount": list(range(10)),
        })

        _, _, _, meta = FeatureService.temporal_split(
            df,
            time_col="step",
            train_ratio=0.6,
            val_ratio=0.2,
            test_ratio=0.2,
        )

        assert meta.boundary_overlap is True

    def test_dataloader_temporal_split_integration(self) -> None:
        """Verifies dataloader temporal split helper on PaySim mock dataset."""
        paysim_data = load_paysim(n_mock_txns=200)
        split_result = temporal_split_dataset(
            paysim_data,
            train_ratio=0.70,
            val_ratio=0.15,
            test_ratio=0.15,
            preprocess=True,
        )

        assert split_result["is_strictly_chronological"] is True
        assert split_result["X_train"].shape[0] == 140
        assert split_result["X_val"].shape[0] == 30
        assert split_result["X_test"].shape[0] == 30
        assert split_result["preprocessor"] is not None
        assert split_result["preprocessor"].is_fitted is True
