"""Non-IID Dirichlet Data Partitioner.

Partitions multi-bank transaction data using Dirichlet distribution Dir(alpha)
to model realistic heterogeneous label and feature distributions across participating financial institutions.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)


class DirichletPartitioner:
    """Partitions dataset samples across clients using Dirichlet distribution Dir(alpha)."""

    @staticmethod
    def partition_indices(
        labels: np.ndarray,
        num_clients: int,
        alpha: float = 0.5,
        min_size: int = 10,
        seed: int = 42,
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

        rng = np.random.default_rng(seed)
        num_samples = len(labels)
        unique_classes = np.unique(labels)

        # Retry loop to guarantee minimum sample size per client
        for attempt in range(100):
            client_indices: dict[int, list[int]] = {i: [] for i in range(num_clients)}

            for c in unique_classes:
                idx_c = np.where(labels == c)[0]
                rng.shuffle(idx_c)

                # Draw proportions p ~ Dir(alpha * 1_N)
                proportions = rng.dirichlet(np.repeat(alpha, num_clients))

                # Balance proportions if any client got zero
                proportions = np.array(
                    [
                        p * (len(client_indices[i]) < num_samples / num_clients)
                        for i, p in enumerate(proportions)
                    ]
                )
                if np.sum(proportions) == 0:
                    proportions = np.ones(num_clients) / num_clients
                else:
                    proportions = proportions / np.sum(proportions)

                # Compute split points
                split_points = (np.cumsum(proportions) * len(idx_c)).astype(int)[:-1]
                idx_c_splits = np.split(idx_c, split_points)

                for i in range(num_clients):
                    client_indices[i].extend(idx_c_splits[i].tolist())

            min_client_samples = min(len(idx) for idx in client_indices.values())
            if min_client_samples >= min_size or attempt == 99:
                logger.info(
                    "Partitioned %d samples across %d clients with Dir(alpha=%.2f). Min samples: %d (attempt %d)",
                    num_samples,
                    num_clients,
                    alpha,
                    min_client_samples,
                    attempt + 1,
                )
                return client_indices

        return client_indices

    @staticmethod
    def partition_dataset(
        features: np.ndarray,
        labels: np.ndarray,
        num_clients: int,
        alpha: float = 0.5,
        min_size: int = 10,
        seed: int = 42,
    ) -> list[tuple[np.ndarray, np.ndarray]]:
        """Partition features and labels into list of (X_i, y_i) arrays for each client."""
        client_indices_map = DirichletPartitioner.partition_indices(
            labels=labels,
            num_clients=num_clients,
            alpha=alpha,
            min_size=min_size,
            seed=seed,
        )

        client_datasets = []
        for i in range(num_clients):
            indices = client_indices_map[i]
            X_i = features[indices]
            y_i = labels[indices]
            client_datasets.append((X_i, y_i))

        return client_datasets
