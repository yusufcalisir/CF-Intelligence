"""Unit tests for Non-IID Dirichlet Data Partitioner and Partition Statistical Fidelity."""

from __future__ import annotations

import numpy as np
import pytest

from app.application.services.fl_dirichlet_partitioner import (
    DirichletPartitioner,
    PartitionStats,
)


class TestDirichletPartitioner:
    """Comprehensive test suite verifying Dirichlet Dir(alpha) distribution properties."""

    def test_partition_indices_conservation_and_uniqueness(self) -> None:
        """Verify all samples are partitioned with zero duplicates and zero omissions."""
        num_samples = 300
        labels = np.array([0] * 200 + [1] * 100)
        num_clients = 4

        client_indices = DirichletPartitioner.partition_indices(
            labels=labels,
            num_clients=num_clients,
            alpha=0.5,
            min_size=10,
            seed=42,
        )

        assert len(client_indices) == num_clients
        all_assigned = []
        for client_id, idx_list in client_indices.items():
            assert len(idx_list) >= 10, f"Client {client_id} has fewer than min_size=10 samples"
            all_assigned.extend(idx_list)

        # Check total sample count conservation
        assert len(all_assigned) == num_samples
        # Check uniqueness (every index from 0 to 299 assigned exactly once)
        assert sorted(all_assigned) == list(range(num_samples))

    def test_partition_indices_extreme_non_iid_min_size(self) -> None:
        """Verify min_size boundary guarantee holds even under extreme Dirichlet skew (alpha=0.01)."""
        labels = np.array([0] * 250 + [1] * 50)
        num_clients = 5
        min_size = 15

        client_indices = DirichletPartitioner.partition_indices(
            labels=labels,
            num_clients=num_clients,
            alpha=0.01,
            min_size=min_size,
            seed=1337,
        )

        for client_id, idx_list in client_indices.items():
            assert len(idx_list) >= min_size, (
                f"Client {client_id} received {len(idx_list)} samples, expected >= {min_size}"
            )

        total_partitioned = sum(len(idx) for idx in client_indices.values())
        assert total_partitioned == len(labels)

    def test_partition_indices_iid_uniformity(self) -> None:
        """Verify high alpha (alpha=100.0) produces approximately uniform class distributions."""
        labels = np.array([0] * 600 + [1] * 400)
        num_clients = 3

        client_indices = DirichletPartitioner.partition_indices(
            labels=labels,
            num_clients=num_clients,
            alpha=100.0,
            min_size=20,
            seed=42,
        )

        stats = DirichletPartitioner.compute_partition_stats(
            labels=labels,
            client_indices=client_indices,
            alpha=100.0,
        )

        # In uniform IID, fraud ratio should be close to global fraud ratio (400/1000 = 0.40)
        for client_id, ratio in stats.client_fraud_ratios.items():
            assert pytest.approx(0.40, abs=0.08) == ratio
            # TVD should be low (< 0.15) for high alpha
            assert stats.total_variation_distances[client_id] < 0.15

    def test_partition_dataset_alignment(self) -> None:
        """Verify features and labels split and align correctly for each client."""
        features = np.random.randn(150, 4)
        labels = np.array([0] * 100 + [1] * 50)
        num_clients = 3

        client_datasets = DirichletPartitioner.partition_dataset(
            features=features,
            labels=labels,
            num_clients=num_clients,
            alpha=0.5,
            min_size=10,
            seed=42,
        )

        assert len(client_datasets) == num_clients
        total_samples = 0
        for x_i, y_i in client_datasets:
            assert len(x_i) == len(y_i)
            assert len(x_i) >= 10
            assert x_i.shape[1] == 4
            total_samples += len(x_i)

        assert total_samples == 150

    def test_compute_partition_stats_metrics(self) -> None:
        """Verify PartitionStats calculates TVD, entropy, fraud ratios, and matrix correctly."""
        labels = np.array([0] * 80 + [1] * 20)
        client_indices = {
            0: list(range(0, 50)),    # 50 class 0, 0 class 1
            1: list(range(50, 100)),  # 30 class 0, 20 class 1
        }

        stats = DirichletPartitioner.compute_partition_stats(
            labels=labels,
            client_indices=client_indices,
            alpha=0.2,
        )

        assert isinstance(stats, PartitionStats)
        assert stats.num_clients == 2
        assert stats.alpha == 0.2
        assert stats.total_samples == 100
        assert stats.client_sample_counts == {0: 50, 1: 50}
        assert stats.client_fraud_ratios[0] == 0.0
        assert stats.client_fraud_ratios[1] == 0.4

        # Client 0 has only class 0 -> entropy is 0.0
        assert stats.client_label_entropies[0] == 0.0
        # Client 1 has mixed classes -> entropy > 0.0
        assert stats.client_label_entropies[1] > 0.0

        # Total Variation Distance bounded in [0, 1]
        for tvd in stats.total_variation_distances.values():
            assert 0.0 <= tvd <= 1.0

        assert len(stats.label_distribution_matrix) == 2
        assert len(stats.label_distribution_matrix[0]) == 2
        assert stats.quantity_skew_ratio == 1.0

    def test_validation_errors(self) -> None:
        """Verify strict input validation against invalid inputs and edge conditions."""
        labels = np.array([0, 1, 0, 1])

        with pytest.raises(ValueError, match="num_clients must be greater than 0"):
            DirichletPartitioner.partition_indices(labels=labels, num_clients=0)

        with pytest.raises(ValueError, match="alpha concentration parameter must be greater than 0"):
            DirichletPartitioner.partition_indices(labels=labels, num_clients=2, alpha=-0.5)

        with pytest.raises(ValueError, match="min_size must be non-negative"):
            DirichletPartitioner.partition_indices(labels=labels, num_clients=2, min_size=-1)

        with pytest.raises(ValueError, match="labels array cannot be empty"):
            DirichletPartitioner.partition_indices(labels=np.array([]), num_clients=2)

        with pytest.raises(ValueError, match="Insufficient samples"):
            DirichletPartitioner.partition_indices(labels=labels, num_clients=3, min_size=5)

        with pytest.raises(ValueError, match="Features length .* does not match labels length"):
            DirichletPartitioner.partition_dataset(
                features=np.random.randn(5, 2),
                labels=np.array([0, 1]),
                num_clients=2,
            )
