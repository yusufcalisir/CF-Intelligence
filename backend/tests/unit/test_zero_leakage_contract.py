"""Unit tests for strict zero-leakage federated partitioning contract.

Verifies mathematical invariants guaranteeing:
1. Complete index and hash disjointness between client training partitions and the global test set.
2. Pairwise client partition independence.
3. Temporal monotonicity and absence of lookahead bias.
4. Preprocessor and scaler isolation (zero data snooping).
5. Pre-flight training gating in the federated learning engine.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from app.application.services.dataloader import (
    FederatedDataLeakageError,
    ZeroLeakagePartitionContract,
    partition_and_isolate_federated_dataset,
)
from app.application.services.fl_engine import FederatedLearningEngine
from app.application.services.preprocessor import DataPreprocessor


def test_index_disjointness_clean_partition_passes() -> None:
    """Disjoint client partitions and global test set must pass index verification with zero overlap."""
    client_indices = {
        0: [0, 1, 2, 3],
        1: [4, 5, 6, 7],
        2: [8, 9, 10, 11],
    }
    test_indices = [12, 13, 14, 15]

    is_disjoint, overlap_count, violations = ZeroLeakagePartitionContract.verify_index_disjointness(
        client_indices, test_indices
    )
    assert is_disjoint is True
    assert overlap_count == 0
    assert len(violations) == 0


def test_index_disjointness_leakage_detected_and_raises() -> None:
    """Overlapping index between client partition and global test set must trigger leakage violation."""
    client_indices = {
        0: [0, 1, 2, 12],  # Index 12 leaks into test set
        1: [4, 5, 6, 7],
    }
    test_indices = [12, 13, 14, 15]

    is_disjoint, overlap_count, violations = ZeroLeakagePartitionContract.verify_index_disjointness(
        client_indices, test_indices
    )
    assert is_disjoint is False
    assert overlap_count == 1
    assert any("Test leakage detected" in v for v in violations)


def test_pairwise_client_overlap_detected() -> None:
    """Duplicate sample indices between two clients must be detected as cross-client leakage."""
    client_indices = {
        0: [0, 1, 2, 3],
        1: [3, 4, 5, 6],  # Index 3 duplicated from client 0
    }
    test_indices = [10, 11, 12]

    is_disjoint, overlap_count, violations = ZeroLeakagePartitionContract.verify_index_disjointness(
        client_indices, test_indices
    )
    assert is_disjoint is False
    assert overlap_count == 1
    assert any("Cross-client overlap detected" in v for v in violations)


def test_hash_collision_duplicate_row_detection() -> None:
    """Exact duplicate feature vector between training partition and test set must be flagged."""
    row_leak = np.array([1.23, 4.56, 7.89], dtype=np.float32)
    client_feats = {
        0: np.array([[0.1, 0.2, 0.3], row_leak], dtype=np.float32),
        1: np.array([[2.0, 3.0, 4.0]], dtype=np.float32),
    }
    test_feats = np.array([row_leak, [9.0, 9.0, 9.0]], dtype=np.float32)

    is_disjoint, collision_count, violations = ZeroLeakagePartitionContract.verify_hash_disjointness(
        client_feats, test_feats
    )
    assert is_disjoint is False
    assert collision_count == 1
    assert any("Feature hash collision detected" in v for v in violations)


def test_temporal_monotonicity_strict_ordering() -> None:
    """Chronologically partitioned training data preceding test timestamps must pass cleanly."""
    client_timestamps = {
        0: np.array([100.0, 105.0, 110.0]),
        1: np.array([102.0, 108.0, 112.0]),
    }
    test_timestamps = np.array([115.0, 120.0, 125.0])

    is_monotonic, max_train, min_test, violations = (
        ZeroLeakagePartitionContract.verify_temporal_monotonicity(
            client_timestamps, test_timestamps
        )
    )
    assert is_monotonic is True
    assert max_train == 112.0
    assert min_test == 115.0
    assert len(violations) == 0


def test_temporal_monotonicity_lookahead_leakage_rejection() -> None:
    """Training sample with timestamp succeeding test timestamp must trigger lookahead leakage error."""
    client_timestamps = {
        0: np.array([100.0, 125.0]),  # 125.0 is in the future relative to test min 115.0
        1: np.array([102.0, 108.0]),
    }
    test_timestamps = np.array([115.0, 120.0])

    is_monotonic, max_train, min_test, violations = (
        ZeroLeakagePartitionContract.verify_temporal_monotonicity(
            client_timestamps, test_timestamps
        )
    )
    assert is_monotonic is False
    assert max_train == 125.0
    assert min_test == 115.0
    assert any("Temporal leakage detected" in v for v in violations)


def test_scaler_isolation_test_alteration_invariance() -> None:
    """DataPreprocessor fitted strictly on training data must remain parameter-invariant under test perturbations."""
    rng = np.random.default_rng(42)
    X_train = rng.normal(loc=10.0, scale=2.0, size=(100, 3)).astype(np.float32)

    # Clean preprocessor fitted strictly on training split
    prep = DataPreprocessor(numeric_strategy="standardize")
    prep.fit(X_train)

    train_means_initial = dict(prep.means_)

    # Create extreme outlier test set
    X_test_perturbed = rng.normal(loc=5000.0, scale=100.0, size=(50, 3)).astype(np.float32)
    _ = prep.transform(X_test_perturbed)

    # Invariant: transform on perturbed test data must NOT alter fitted parameters
    assert prep.means_ == train_means_initial
    is_isolated, violations = ZeroLeakagePartitionContract.verify_scaler_isolation(
        prep, X_train, X_test_perturbed
    )
    assert is_isolated is True
    assert len(violations) == 0


def test_scaler_leakage_dirty_fit_detected() -> None:
    """Preprocessor contaminated with test data must be flagged by verify_scaler_isolation."""
    rng = np.random.default_rng(42)
    X_train = rng.normal(loc=0.0, scale=1.0, size=(100, 2)).astype(np.float32)
    X_test = rng.normal(loc=100.0, scale=1.0, size=(50, 2)).astype(np.float32)

    # Contaminated preprocessor (fitted on combined train + test)
    X_dirty = np.vstack([X_train, X_test])
    prep_dirty = DataPreprocessor(numeric_strategy="standardize")
    prep_dirty.fit(X_dirty)

    # Verifying against pure training features must fail
    is_isolated, violations = ZeroLeakagePartitionContract.verify_scaler_isolation(
        prep_dirty, X_train, X_test
    )
    assert is_isolated is False
    assert any("Scaler leakage" in v for v in violations)


def test_partition_and_isolate_federated_dataset_end_to_end() -> None:
    """Complete pipeline must partition data across clients while certifying zero leakage."""
    rng = np.random.default_rng(100)
    n_samples = 300
    features = rng.normal(size=(n_samples, 4)).astype(np.float32)
    labels = rng.integers(0, 2, size=n_samples)
    timestamps = np.linspace(1000.0, 2000.0, n_samples)

    result = partition_and_isolate_federated_dataset(
        features=features,
        labels=labels,
        timestamps=timestamps,
        num_clients=3,
        test_ratio=0.20,
        alpha=0.5,
        min_size=10,
        temporal_split=True,
        preprocess=True,
        seed=42,
    )

    assert result["num_clients"] == 3
    assert result["test_sample_count"] == int(n_samples * 0.20)
    assert result["train_sample_count"] == n_samples - result["test_sample_count"]

    report = result["audit_report"]
    assert report.is_valid is True
    assert report.index_overlap_count == 0
    assert report.hash_collision_count == 0
    assert report.temporal_monotonic is True
    assert report.scaler_isolated is True
    assert len(report.violations) == 0


def test_fl_engine_gate_training_zero_leakage_integration() -> None:
    """FederatedLearningEngine must verify zero leakage and gate training execution."""
    settings = MagicMock()
    model_service = MagicMock()
    privacy_service = MagicMock()
    engine = FederatedLearningEngine(settings, model_service, privacy_service)

    client_datasets = {
        0: (np.array([[1.0, 2.0]], dtype=np.float32), np.array([0])),
        1: (np.array([[3.0, 4.0]], dtype=np.float32), np.array([1])),
    }
    test_clean = (np.array([[5.0, 6.0]], dtype=np.float32), np.array([0]))
    test_leaky = (np.array([[1.0, 2.0]], dtype=np.float32), np.array([0]))  # Matches client 0

    # 1. Clean passes gate
    is_clean = engine.gate_training_zero_leakage(client_datasets, test_clean)
    assert is_clean is True

    # 2. Leaky triggers exception when raise_on_violation=True
    with pytest.raises(FederatedDataLeakageError):
        engine.gate_training_zero_leakage(client_datasets, test_leaky)
