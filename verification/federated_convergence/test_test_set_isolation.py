"""Scientific Verification: Global Test Set Isolation & Zero-Leakage Federated Partitioning.

Module 21 of the Scientific Self-Verification Registry.
Formally verifies the 4 mathematical invariants of zero data leakage:
1. Index Disjointness: I(D_test) ∩ (U_k I(D_train^(k))) = ∅.
2. Inter-Client Pairwise Disjointness: ∀ i ≠ j, I(D_train^(i)) ∩ I(D_train^(j)) = ∅.
3. Temporal Monotonicity: max_{k, x ∈ D_train^(k)} t(x) ≤ min_{x ∈ D_test} t(x).
4. Preprocessor Isolation (Zero Data Snooping): ∂θ_prep / ∂D_test = 0.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

backend_dir = Path(__file__).resolve().parent.parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from app.application.services.dataloader import (  # noqa: E402
    FederatedDataLeakageError,
    ZeroLeakagePartitionContract,
    partition_and_isolate_federated_dataset,
)
from app.application.services.fl_engine import FederatedLearningEngine  # noqa: E402
from app.application.services.preprocessor import DataPreprocessor  # noqa: E402


def test_inv_01_global_test_index_complete_disjointness() -> None:
    """Invariant 1: Global test index set must have zero intersection with any client training partition."""
    client_indices = {
        0: np.array([10, 20, 30]),
        1: np.array([40, 50, 60]),
        2: np.array([70, 80, 90]),
    }
    test_indices = np.array([100, 110, 120])

    is_disjoint, overlap, violations = ZeroLeakagePartitionContract.verify_index_disjointness(
        client_indices, test_indices
    )
    assert is_disjoint is True
    assert overlap == 0
    assert len(violations) == 0


def test_inv_02_inter_client_partition_pairwise_disjointness() -> None:
    """Invariant 2: Federated client training partitions must be pairwise mutually disjoint."""
    client_indices = {
        0: [1, 2, 3],
        1: [4, 5, 6],
        2: [7, 8, 9],
        3: [10, 11, 12],
    }
    test_indices = [13, 14, 15]

    is_disjoint, overlap, violations = ZeroLeakagePartitionContract.verify_index_disjointness(
        client_indices, test_indices
    )
    assert is_disjoint is True
    assert overlap == 0
    assert len(violations) == 0


def test_inv_03_temporal_boundary_strict_monotonicity() -> None:
    """Invariant 3: Training timestamps across all clients must precede or equal minimum test timestamp."""
    client_ts = {
        0: np.array([10.0, 20.0, 35.0]),
        1: np.array([15.0, 25.0, 40.0]),
        2: np.array([12.0, 30.0, 42.0]),
    }
    test_ts = np.array([45.0, 50.0, 60.0])

    is_mono, max_train, min_test, viols = ZeroLeakagePartitionContract.verify_temporal_monotonicity(
        client_ts, test_ts
    )
    assert is_mono is True
    assert max_train == 42.0
    assert min_test == 45.0
    assert max_train <= min_test
    assert len(viols) == 0


def test_inv_04_preprocessor_parameter_zero_gradient_wrt_test() -> None:
    """Invariant 4: Preprocessor state must have zero mathematical dependence on test set data."""
    rng = np.random.default_rng(2024)
    X_train = rng.normal(loc=5.0, scale=1.5, size=(200, 4)).astype(np.float32)

    prep = DataPreprocessor(numeric_strategy="standardize")
    prep.fit(X_train)

    fitted_means = dict(prep.means_)
    fitted_stds = dict(prep.stds_)

    # Apply to wildly shifted test distribution
    X_test_shifted = rng.uniform(low=500.0, high=1000.0, size=(100, 4)).astype(np.float32)
    _ = prep.transform(X_test_shifted)

    # Invariant: parameters must remain bit-exact identical
    for col in fitted_means:
        assert prep.means_[col] == fitted_means[col]
        assert prep.stds_[col] == fitted_stds[col]

    is_isolated, viols = ZeroLeakagePartitionContract.verify_scaler_isolation(
        prep, X_train, X_test_shifted
    )
    assert is_isolated is True
    assert len(viols) == 0


def test_inv_05_unseen_category_imputation_isolation() -> None:
    """Invariant 5: Unseen categories appearing strictly in the test partition must never leak into preprocessor vocabulary."""
    train_df = pd.DataFrame({
        "channel": ["ATM", "POS", "ONLINE", "ATM", "POS"],
        "amount": [10.0, 20.0, 30.0, 40.0, 50.0],
    })
    test_df = pd.DataFrame({
        "channel": ["WIRE", "CRYPTO", "ATM"],  # WIRE and CRYPTO are unseen in train
        "amount": [100.0, 200.0, 300.0],
    })

    prep = DataPreprocessor(numeric_strategy="standardize")
    prep.fit(train_df)

    assert "WIRE" not in prep.categories_["channel"]
    assert "CRYPTO" not in prep.categories_["channel"]
    assert set(prep.categories_["channel"]) == {"ATM", "POS", "ONLINE"}

    # Transforming test must not alter training category vocabulary
    X_test_transformed = prep.transform(test_df)
    assert X_test_transformed.shape[0] == 3
    assert set(prep.categories_["channel"]) == {"ATM", "POS", "ONLINE"}


def test_inv_06_dirichlet_skew_preserves_test_isolation() -> None:
    """Invariant 6: Extreme Non-IID Dirichlet skew (alpha=0.05) must preserve complete test isolation."""
    rng = np.random.default_rng(999)
    n_samples = 500
    features = rng.normal(size=(n_samples, 6)).astype(np.float32)
    labels = rng.integers(0, 2, size=n_samples)
    timestamps = np.linspace(100.0, 500.0, n_samples)

    result = partition_and_isolate_federated_dataset(
        features=features,
        labels=labels,
        timestamps=timestamps,
        num_clients=5,
        test_ratio=0.25,
        alpha=0.05,  # Extreme Non-IID skew
        min_size=10,
        temporal_split=True,
        preprocess=True,
        seed=123,
    )

    report = result["audit_report"]
    assert report.is_valid is True
    assert report.num_clients == 5
    assert report.index_overlap_count == 0
    assert report.temporal_monotonic is True
    assert report.scaler_isolated is True


def test_inv_07_synthetic_outlier_injection_in_test_cannot_snoop() -> None:
    """Invariant 7: Injecting massive outliers into the test set must not contaminate min/max scalers."""
    rng = np.random.default_rng(42)
    X_train = rng.uniform(low=0.0, high=10.0, size=(100, 2)).astype(np.float32)

    prep = DataPreprocessor(numeric_strategy="minmax")
    prep.fit(X_train)

    train_maxs = dict(prep.maxs_)

    # Inject 10,000x outlier in test
    X_test_outlier = np.array([[100_000.0, 500_000.0]], dtype=np.float32)
    _ = prep.transform(X_test_outlier)

    # Invariant: prep.maxs_ must remain bounded by training max
    for col in train_maxs:
        assert prep.maxs_[col] <= 10.0
        assert prep.maxs_[col] == train_maxs[col]


def test_inv_08_fl_engine_enforces_zero_leakage_pre_flight() -> None:
    """Invariant 8: FederatedLearningEngine pre-flight gate must block and quarantine leaky partitions."""
    settings = MagicMock()
    model_service = MagicMock()
    privacy_service = MagicMock()
    engine = FederatedLearningEngine(settings, model_service, privacy_service)

    client_datasets = {
        0: (np.array([[10.0, 20.0]], dtype=np.float32), np.array([0])),
        1: (np.array([[30.0, 40.0]], dtype=np.float32), np.array([1])),
    }
    # Test sample collides with client 1
    test_colliding = (np.array([[30.0, 40.0]], dtype=np.float32), np.array([1]))

    with pytest.raises(FederatedDataLeakageError) as exc_info:
        engine.gate_training_zero_leakage(client_datasets, test_colliding)

    assert "Feature hash collision detected" in str(exc_info.value)


def test_inv_09_multi_seed_partition_isolation_stability() -> None:
    """Invariant 9: Zero leakage contract must hold deterministically across all canonical seeds."""
    canonical_seeds = [42, 123, 456, 789, 1024]
    rng = np.random.default_rng(777)
    features = rng.normal(size=(200, 3)).astype(np.float32)
    labels = rng.integers(0, 2, size=200)
    timestamps = np.linspace(10.0, 100.0, 200)

    for seed in canonical_seeds:
        res = partition_and_isolate_federated_dataset(
            features=features,
            labels=labels,
            timestamps=timestamps,
            num_clients=3,
            test_ratio=0.20,
            alpha=0.5,
            seed=seed,
        )
        assert res["audit_report"].is_valid is True
        assert res["audit_report"].index_overlap_count == 0


def test_inv_10_tampered_partition_immediate_quarantine() -> None:
    """Invariant 10: Explicit temporal inversion must immediately trigger quarantine and raise."""
    client_datasets = {
        0: (np.array([[1.0]], dtype=np.float32), np.array([0])),
    }
    test_dataset = (np.array([[2.0]], dtype=np.float32), np.array([0]))

    # Training timestamp 200.0 exceeds test timestamp 100.0
    client_ts = {0: np.array([200.0])}
    test_ts = np.array([100.0])

    with pytest.raises(FederatedDataLeakageError) as exc_info:
        ZeroLeakagePartitionContract.audit_federated_partitions(
            client_datasets=client_datasets,
            test_dataset=test_dataset,
            client_timestamps=client_ts,
            test_timestamps=test_ts,
            raise_on_violation=True,
        )

    assert "Temporal leakage detected" in str(exc_info.value)
