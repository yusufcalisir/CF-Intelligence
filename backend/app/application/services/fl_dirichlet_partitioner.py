"""Non-IID Dirichlet Data Partitioner.

Partitions multi-bank transaction data using Dirichlet distribution Dir(alpha)
to model realistic heterogeneous label and feature distributions across participating financial institutions.
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PartitionStats:
    """Statistical summary of a Non-IID Dirichlet partitioned dataset."""

    num_clients: int
    alpha: float
    total_samples: int
    client_sample_counts: dict[int, int]
    client_class_counts: dict[int, dict[int, int]]
    client_fraud_ratios: dict[int, float]
    client_label_entropies: dict[int, float]
    total_variation_distances: dict[int, float]
    quantity_skew_ratio: float
    label_distribution_matrix: list[list[float]]

    def to_dict(self) -> dict[str, Any]:
        """Convert partition statistics to dictionary."""
        return asdict(self)


class DirichletPartitioner:
    """Partitions dataset samples across clients using Dirichlet distribution Dir(alpha)."""

    @staticmethod
    def _ensure_min_size(
        client_indices: dict[int, list[int]],
        min_size: int,
    ) -> dict[int, list[int]]:
        """Rebalances boundary sample indices between clients to guarantee minimum partition size."""
        if min_size <= 0:
            return client_indices

        num_clients = len(client_indices)
        for client_idx in range(num_clients):
            while len(client_indices[client_idx]) < min_size:
                # Find client with largest excess over min_size
                donor_idx = max(
                    client_indices.keys(),
                    key=lambda k: len(client_indices[k]) if k != client_idx else -1,
                )
                if len(client_indices[donor_idx]) <= min_size:
                    break
                transferred_sample = client_indices[donor_idx].pop()
                client_indices[client_idx].append(transferred_sample)

        return client_indices

    @staticmethod
    def partition_indices(
        labels: np.ndarray,
        num_clients: int,
        alpha: float = 0.5,
        min_size: int = 10,
        seed: int | None = 42,
    ) -> dict[int, list[int]]:
        """Partition sample indices across num_clients according to Dir(alpha).

        Args:
            labels: 1D array of class labels (e.g., 0 for legitimate, 1 for fraud).
            num_clients: Number of participating bank nodes (N >= 1).
            alpha: Dirichlet concentration parameter (alpha > 0). Smaller values mean higher Non-IID skew.
            min_size: Minimum number of samples required per client.
            seed: Random generator seed for reproducibility.

        Returns:
            Dictionary mapping client_idx (0..N-1) to list of sample indices.
        """
        if num_clients <= 0:
            raise ValueError("num_clients must be greater than 0.")
        if alpha <= 0:
            raise ValueError("alpha concentration parameter must be greater than 0.")
        if min_size < 0:
            raise ValueError("min_size must be non-negative.")

        num_samples = len(labels)
        if num_samples == 0:
            raise ValueError("labels array cannot be empty.")
        if num_samples < num_clients * min_size:
            raise ValueError(
                f"Insufficient samples ({num_samples}) to guarantee min_size={min_size} "
                f"across {num_clients} clients (requires at least {num_clients * min_size} samples)."
            )

        rng = np.random.default_rng(seed)
        unique_classes = np.unique(labels)

        # Standard Dirichlet class-wise allocation with rejection sampling
        max_attempts = 100
        client_indices: dict[int, list[int]] = {i: [] for i in range(num_clients)}

        for attempt in range(max_attempts):
            candidate_indices: dict[int, list[int]] = {i: [] for i in range(num_clients)}

            for c in unique_classes:
                idx_c = np.where(labels == c)[0].copy()
                rng.shuffle(idx_c)

                # Draw class proportion vector from Dirichlet distribution: p ~ Dir(alpha * 1_N)
                proportions = rng.dirichlet(np.repeat(alpha, num_clients))

                # Split points for class c
                split_points = (np.cumsum(proportions) * len(idx_c)).astype(int)[:-1]
                idx_c_splits = np.split(idx_c, split_points)

                for i in range(num_clients):
                    candidate_indices[i].extend(idx_c_splits[i].tolist())

            min_client_samples = min(len(idx) for idx in candidate_indices.values())
            if min_client_samples >= min_size:
                logger.info(
                    "Partitioned %d samples across %d clients with Dir(alpha=%.2f). Min samples: %d (attempt %d)",
                    num_samples,
                    num_clients,
                    alpha,
                    min_client_samples,
                    attempt + 1,
                )
                return candidate_indices

            client_indices = candidate_indices

        # If pure rejection sampling did not satisfy min_size within max_attempts,
        # rebalance boundary samples from donor clients to guarantee min_size
        client_indices = DirichletPartitioner._ensure_min_size(client_indices, min_size)
        return client_indices

    @staticmethod
    def partition_dataset(
        features: np.ndarray,
        labels: np.ndarray,
        num_clients: int,
        alpha: float = 0.5,
        min_size: int = 10,
        seed: int | None = 42,
    ) -> list[tuple[np.ndarray, np.ndarray]]:
        """Partition features and labels into list of (X_i, y_i) arrays for each client."""
        if len(features) != len(labels):
            raise ValueError(
                f"Features length ({len(features)}) does not match labels length ({len(labels)})."
            )

        client_indices_map = DirichletPartitioner.partition_indices(
            labels=labels,
            num_clients=num_clients,
            alpha=alpha,
            min_size=min_size,
            seed=seed,
        )

        client_datasets: list[tuple[np.ndarray, np.ndarray]] = []
        for i in range(num_clients):
            indices = client_indices_map[i]
            x_i = features[indices]
            y_i = labels[indices]
            client_datasets.append((x_i, y_i))

        return client_datasets

    @staticmethod
    def compute_partition_stats(
        labels: np.ndarray,
        client_indices: dict[int, list[int]],
        alpha: float = 0.5,
    ) -> PartitionStats:
        """Computes statistical summary of Non-IID Dirichlet distribution across clients.

        Calculates per-client class distributions, Total Variation Distance (TVD) from the
        global population, Shannon entropy, fraud ratio, and quantity skew.
        """
        num_clients = len(client_indices)
        total_samples = len(labels)
        unique_classes = sorted([int(c) for c in np.unique(labels)])

        # Global distribution P(Y)
        global_counts = {c: int(np.sum(labels == c)) for c in unique_classes}
        global_dist = {
            c: global_counts[c] / total_samples if total_samples > 0 else 0.0
            for c in unique_classes
        }

        client_sample_counts: dict[int, int] = {}
        client_class_counts: dict[int, dict[int, int]] = {}
        client_fraud_ratios: dict[int, float] = {}
        client_label_entropies: dict[int, float] = {}
        total_variation_distances: dict[int, float] = {}
        label_distribution_matrix: list[list[float]] = []

        for client_idx, indices in client_indices.items():
            c_samples = len(indices)
            client_sample_counts[client_idx] = c_samples
            c_labels = labels[indices] if c_samples > 0 else np.array([])

            counts: dict[int, int] = {}
            dist: dict[int, float] = {}
            for c in unique_classes:
                cnt = int(np.sum(c_labels == c)) if c_samples > 0 else 0
                counts[c] = cnt
                dist[c] = cnt / c_samples if c_samples > 0 else 0.0

            client_class_counts[client_idx] = counts
            label_distribution_matrix.append([dist[c] for c in unique_classes])

            # Fraud ratio (class 1 if present, else 0.0)
            client_fraud_ratios[client_idx] = round(dist.get(1, 0.0), 4)

            # Total Variation Distance: 0.5 * sum_c |P_i(c) - P_global(c)|
            tvd = 0.5 * sum(abs(dist[c] - global_dist[c]) for c in unique_classes)
            total_variation_distances[client_idx] = round(tvd, 4)

            # Shannon entropy: - sum_c P_i(c) * log2(P_i(c))
            entropy = 0.0
            for c in unique_classes:
                p_c = dist[c]
                if p_c > 0.0:
                    entropy -= p_c * math.log2(p_c)
            client_label_entropies[client_idx] = round(entropy, 4)

        min_c = min(client_sample_counts.values()) if client_sample_counts else 1
        max_c = max(client_sample_counts.values()) if client_sample_counts else 1
        quantity_skew = round(max_c / max(min_c, 1), 4)

        return PartitionStats(
            num_clients=num_clients,
            alpha=alpha,
            total_samples=total_samples,
            client_sample_counts=client_sample_counts,
            client_class_counts=client_class_counts,
            client_fraud_ratios=client_fraud_ratios,
            client_label_entropies=client_label_entropies,
            total_variation_distances=total_variation_distances,
            quantity_skew_ratio=quantity_skew,
            label_distribution_matrix=label_distribution_matrix,
        )

