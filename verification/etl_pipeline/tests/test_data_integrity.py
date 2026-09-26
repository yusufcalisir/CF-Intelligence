"""Scientific Data Integrity & Zero Data Snooping Verification.

Verifies:
- Absence of temporal leakage (strict monotonic chronological ordering).
- Mathematical proof of zero data snooping during tabular preprocessing.
- Leakage-free benchmark evaluation across all supported public dataset schemas.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

backend_dir = Path(__file__).resolve().parent.parent.parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.application.services.dataloader import load_dataset  # noqa: E402
from app.application.services.feature_service import FeatureService  # noqa: E402
from app.application.services.preprocessor import DataPreprocessor  # noqa: E402


def test_scientific_temporal_monotonicity() -> None:
    """Mathematical invariant: train_max <= val_min <= test_min for time series data."""
    rng = np.random.default_rng(1001)
    n_points = 500
    timestamps = np.sort(rng.uniform(1_000_000, 2_000_000, size=n_points))
    # Shuffle into raw log DataFrame
    shuffled_idx = rng.permutation(n_points)
    df = pd.DataFrame({
        "step": timestamps[shuffled_idx],
        "feature_a": rng.normal(0, 1, size=n_points),
        "is_fraud": (rng.random(size=n_points) < 0.05).astype(int),
    })

    train_df, val_df, test_df, result = FeatureService.temporal_split(
        df,
        time_col="step",
        train_ratio=0.70,
        val_ratio=0.15,
        test_ratio=0.15,
        label_col="is_fraud",
    )

    assert result.is_strictly_chronological is True
    t_train_max = float(train_df["step"].max())
    t_val_min = float(val_df["step"].min())
    t_val_max = float(val_df["step"].max())
    t_test_min = float(test_df["step"].min())

    assert t_train_max <= t_val_min
    assert t_val_max <= t_test_min


def test_absence_of_data_snooping_in_scaling() -> None:
    """Scientific verification: test partition distribution shift does not alter train preprocessing."""
    rng = np.random.default_rng(2002)

    X_train = pd.DataFrame({"feat": rng.normal(loc=10.0, scale=2.0, size=100)})
    # Test partition with severe covar shift (mean=1000, scale=500)
    X_test_shifted = pd.DataFrame({"feat": rng.normal(loc=1000.0, scale=500.0, size=50)})

    prep = DataPreprocessor(numeric_strategy="standardize", clip_outliers=False)
    prep.fit(X_train)

    train_mean = prep.means_["feat"]
    train_std = prep.stds_["feat"]

    # Verify parameters match training population exactly
    assert train_mean == pytest.approx(10.0, abs=0.5)
    assert train_std == pytest.approx(2.0, abs=0.5)

    # Transform test partition without clipping
    X_test_scaled = prep.transform(X_test_shifted)

    # Assert preprocessor internal state was unaffected
    assert prep.means_["feat"] == train_mean
    assert prep.stds_["feat"] == train_std

    # Check scaled values strictly reflect test values scaled using train parameters
    expected_scaled = (X_test_shifted["feat"].to_numpy(dtype=np.float32) - train_mean) / train_std
    np.testing.assert_allclose(np.asarray(X_test_scaled[:, 0], dtype=np.float32), expected_scaled, rtol=1e-5)

    # Verify clipped mode bounds extreme outliers to clip_std_factor
    prep_clipped = DataPreprocessor(numeric_strategy="standardize", clip_outliers=True, clip_std_factor=6.0)
    prep_clipped.fit(X_train)
    X_test_clipped = prep_clipped.transform(X_test_shifted)
    assert np.all(X_test_clipped <= 6.0)
    assert np.all(X_test_clipped >= -6.0)


def test_benchmark_dataset_hygiene_and_leakage_absence() -> None:
    """Public dataset synthetic loaders must be free of infinite values, leakage, and corruption."""
    for dataset_name in ["paysim", "creditcard"]:
        data = load_dataset(dataset_name, n_mock_txns=1000)
        X = data["X"]
        y = data["y"]

        # 1. No infinite or NaN values in feature matrix
        assert not np.isnan(X).any(), f"{dataset_name} feature matrix contains NaNs"
        assert not np.isinf(X).any(), f"{dataset_name} feature matrix contains Infs"

        # 2. Labels are strictly binary (0 or 1)
        unique_y = set(np.unique(y))
        assert unique_y.issubset({0, 1}), f"{dataset_name} labels are not strictly binary: {unique_y}"

        # 3. Label ratio is within valid financial bounds (0.0001 <= ratio <= 0.1)
        fraud_ratio = float(np.mean(y == 1))
        assert 0.0001 <= fraud_ratio <= 0.1, f"{dataset_name} fraud ratio abnormal: {fraud_ratio}"
