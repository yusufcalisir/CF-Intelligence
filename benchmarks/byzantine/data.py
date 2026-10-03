# pyright: reportArgumentType=false
# pyright: reportAttributeAccessIssue=false
"""Data loading, strict train/test isolation, and non-IID client partitioning.

Guarantees:
1. Scaler fit strictly on training set.
2. Test set remains global, untouched, and strictly isolated from client partitioning.
3. Dirichlet alpha=0.5 non-IID partitioning performed on training partition only.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from torch.utils.data import TensorDataset

from benchmarks.byzantine.config import ByzantineBenchmarkConfig

logger = logging.getLogger(__name__)


def compute_partition_sha256(client_idx_map: dict[int, np.ndarray]) -> str:
    """Computes deterministic SHA-256 hash of client partition assignments."""
    canonical_data = [
        [cid, [int(idx) for idx in sorted(client_idx_map[cid])]]
        for cid in sorted(client_idx_map.keys())
    ]
    encoded = json.dumps(canonical_data, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def generate_synthetic_creditcard_mock(n_samples: int = 2000, seed: int = 42) -> pd.DataFrame:
    """Generates mock credit card fraud tabular DataFrame matching real schema (30 features + Class)."""
    rng = np.random.default_rng(seed)
    n_fraud = max(10, int(n_samples * 0.005))
    n_normal = n_samples - n_fraud

    normal_feats = rng.standard_normal((n_normal, 29))
    normal_amounts = rng.exponential(scale=50.0, size=(n_normal, 1))
    normal_data = np.hstack([normal_feats, normal_amounts])

    fraud_feats = rng.standard_normal((n_fraud, 29))
    fraud_feats[:, :3] += 2.5
    fraud_amounts = rng.exponential(scale=150.0, size=(n_fraud, 1))
    fraud_data = np.hstack([fraud_feats, fraud_amounts])

    X = np.vstack([normal_data, fraud_data])
    y = np.array([0] * n_normal + [1] * n_fraud)

    indices = rng.permutation(n_samples)
    X = X[indices]
    y = y[indices]

    cols = [f"V{i}" for i in range(1, 29)] + ["Amount", "Time"]
    df = pd.DataFrame(X, columns=cols)
    df["Class"] = y
    return df


def load_raw_features_and_labels(
    config: ByzantineBenchmarkConfig,
    repo_root: Path | None = None,
) -> tuple[np.ndarray, np.ndarray, str]:
    """Loads raw credit card tabular dataset or mock, returning (X, y, provenance_source)."""
    root = repo_root or Path(__file__).resolve().parents[2]
    csv_path = root / config.dataset.data_path

    if not config.dataset.use_mock and csv_path.is_file():
        logger.info("Loading physical Credit Card CSV from: %s", csv_path)
        df = pd.read_csv(csv_path)
        feature_cols = [c for c in df.columns if c not in ("Class",)]
        X = df[feature_cols].to_numpy(dtype=np.float32)
        y = df["Class"].to_numpy(dtype=np.int64)
        provenance = "REAL_DATA_CSV"
    else:
        logger.info("Generating synthetic Credit Card mock (n=%d)", config.dataset.n_mock_samples)
        df = generate_synthetic_creditcard_mock(n_samples=config.dataset.n_mock_samples, seed=config.dataset.split_seed)
        feature_cols = [c for c in df.columns if c not in ("Class",)]
        X = df[feature_cols].to_numpy(dtype=np.float32)
        y = df["Class"].to_numpy(dtype=np.int64)
        provenance = "MOCK_PCA_SYNTHETIC"

    return X, y, provenance


def partition_dirichlet(
    X_train: np.ndarray,
    y_train: np.ndarray,
    n_clients: int,
    alpha: float = 0.5,
    seed: int = 42,
    min_samples: int = 50,
) -> dict[int, np.ndarray]:
    """Partitions training sample indices across n_clients using Dirichlet class skew."""
    rng = np.random.default_rng(seed)
    client_indices: dict[int, list[int]] = {i: [] for i in range(n_clients)}

    for class_val in (0, 1):
        class_idxs = np.where(y_train == class_val)[0]
        rng.shuffle(class_idxs)

        proportions = rng.dirichlet(np.repeat(alpha, n_clients))
        # Normalize and compute split counts
        proportions = proportions / proportions.sum()
        split_counts = (proportions * len(class_idxs)).astype(int)

        # Distribute remainder
        rem = len(class_idxs) - split_counts.sum()
        for i in range(rem):
            split_counts[i % n_clients] += 1

        offset = 0
        for i in range(n_clients):
            cnt = split_counts[i]
            client_indices[i].extend(class_idxs[offset : offset + cnt])
            offset += cnt

    # Ensure every client has at least min_samples by balancing negative examples if needed
    for i in range(n_clients):
        if len(client_indices[i]) < min_samples:
            donor_clients = sorted(range(n_clients), key=lambda c: len(client_indices[c]), reverse=True)
            for d in donor_clients:
                while len(client_indices[i]) < min_samples and len(client_indices[d]) > min_samples + 20:
                    moved = client_indices[d].pop()
                    client_indices[i].append(moved)

    return {i: np.array(client_indices[i], dtype=np.int64) for i in range(n_clients)}


def load_and_partition_byzantine_data(
    config: ByzantineBenchmarkConfig,
    seed: int,
    repo_root: Path | None = None,
) -> tuple[dict[int, TensorDataset], TensorDataset, dict[str, Any]]:
    """Loads, scales, isolates test set, and partitions training data across consortium nodes.

    Returns:
        (client_datasets, untouched_test_dataset, metadata_dict)
    """
    X_raw, y_raw, provenance = load_raw_features_and_labels(config, repo_root)

    # 1. Strict Stratified Train/Test Split
    X_train_raw, X_test_raw, y_train, y_test = train_test_split(
        X_raw,
        y_raw,
        test_size=config.dataset.test_ratio,
        random_state=config.dataset.split_seed,
        stratify=y_raw,
    )

    # 2. Strict Preprocessing Fit ON TRAINING DATA ONLY
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train_raw).astype(np.float32)
    X_test_scaled = scaler.transform(X_test_raw).astype(np.float32)

    # 3. Untouched Global Test Set
    test_dataset = TensorDataset(
        torch.from_numpy(X_test_scaled),
        torch.from_numpy(y_test).float(),
    )

    # 4. Dirichlet Non-IID Partition ON TRAINING DATA ONLY
    client_idx_map = partition_dirichlet(
        X_train=X_train_scaled,
        y_train=y_train,
        n_clients=config.federation.n_clients,
        alpha=config.federation.partition_alpha,
        seed=seed,
        min_samples=config.federation.min_samples_per_client,
    )

    client_datasets: dict[int, TensorDataset] = {}
    client_metadata: dict[int, dict[str, Any]] = {}

    for client_id, idxs in client_idx_map.items():
        x_c = X_train_scaled[idxs]
        y_c = y_train[idxs]
        client_datasets[client_id] = TensorDataset(
            torch.from_numpy(x_c),
            torch.from_numpy(y_c).float(),
        )

        pos_cnt = int(np.sum(y_c == 1))
        neg_cnt = int(np.sum(y_c == 0))
        client_metadata[client_id] = {
            "client_id": client_id,
            "sample_count": len(idxs),
            "positive_count": pos_cnt,
            "negative_count": neg_cnt,
            "prevalence": round(pos_cnt / max(1, len(idxs)), 6),
            "is_malicious": client_id in config.attacks[0].malicious_client_ids if config.attacks else False,
        }

    dataset_metadata = {
        "provenance_type": provenance,
        "feature_dim": X_train_scaled.shape[1],
        "total_train_samples": len(X_train_scaled),
        "total_test_samples": len(X_test_scaled),
        "test_positive_count": int(np.sum(y_test == 1)),
        "test_negative_count": int(np.sum(y_test == 0)),
        "test_prevalence": round(float(np.mean(y_test)), 6),
        "partition_sha256": compute_partition_sha256(client_idx_map),
        "client_metadata": client_metadata,
    }

    return client_datasets, test_dataset, dataset_metadata
