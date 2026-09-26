"""Preprocesses IEEE-CIS Fraud Detection dataset and creates client partitions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def preprocess_ieee_cis(
    data_dir: Path,
    output_dir: Path,
    sample_limit: int | None = 10000,
    n_clients: int = 5,
    dirichlet_alpha: float = 0.5,
    seed: int = 42,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    txn_file = data_dir / "train_transaction.csv"

    if not txn_file.exists():
        print(f"[-] IEEE-CIS file not found at {txn_file}. Generating synthetic proxy structure...")
        rng = np.random.default_rng(seed)
        n_samples = sample_limit or 10000
        fraud_p = 0.035
        y = (rng.random(n_samples) < fraud_p).astype(int)
        df = pd.DataFrame({
            "TransactionID": np.arange(1000, 1000 + n_samples),
            "isFraud": y,
            "TransactionAmt": rng.exponential(scale=150.0, size=n_samples),
            "card1": rng.integers(1000, 9999, size=n_samples),
            "card2": rng.integers(100, 999, size=n_samples),
            "C1": rng.integers(0, 50, size=n_samples),
            "C2": rng.integers(0, 50, size=n_samples),
            "V1": rng.standard_normal(size=n_samples),
            "V2": rng.standard_normal(size=n_samples),
            "V3": rng.standard_normal(size=n_samples),
        })
    else:
        print(f"[+] Loading raw IEEE-CIS dataset from {txn_file}...")
        nrows = sample_limit if sample_limit and sample_limit > 0 else None
        # Select salient columns to keep memory bounded
        base_cols = ["TransactionID", "isFraud", "TransactionAmt", "card1", "card2", "C1", "C2", "V1", "V2", "V3"]
        try:
            df = pd.read_csv(txn_file, nrows=nrows, usecols=lambda c: c in base_cols or c.startswith("V"))
        except Exception:
            df = pd.read_csv(txn_file, nrows=nrows)

    # Impute missing numericals with median
    num_cols = [c for c in df.columns if c not in ["TransactionID", "isFraud"]]
    for col in num_cols:
        if df[col].dtype.kind in "biufc":
            df[col] = df[col].fillna(df[col].median())
            c_min, c_max = df[col].min(), df[col].max()
            if c_max > c_min:
                df[col] = (df[col] - c_min) / (c_max - c_min)
            else:
                df[col] = 0.0

    X = df[num_cols].values.astype(np.float32)
    y = df["isFraud"].values.astype(np.int64)

    # Non-IID Dirichlet Partition
    rng = np.random.default_rng(seed)
    client_indices: list[list[int]] = [[] for _ in range(n_clients)]

    for c in [0, 1]:
        idx_c = np.where(y == c)[0]
        rng.shuffle(idx_c)
        proportions = rng.dirichlet(np.repeat(dirichlet_alpha, n_clients))
        proportions = proportions / proportions.sum()
        splits = (np.cumsum(proportions) * len(idx_c)).astype(int)[:-1]
        client_subsets = np.split(idx_c, splits)
        for i in range(n_clients):
            client_indices[i].extend(client_subsets[i].tolist())

    metadata: dict[str, Any] = {
        "dataset": "IEEE-CIS",
        "total_samples": len(y),
        "fraud_samples": int(np.sum(y)),
        "fraud_rate": float(np.mean(y)),
        "feature_dim": X.shape[1],
        "n_clients": n_clients,
        "dirichlet_alpha": dirichlet_alpha,
        "client_distributions": {},
    }

    for i in range(n_clients):
        c_idx = np.array(client_indices[i])
        c_X = X[c_idx]
        c_y = y[c_idx]
        np.savez_compressed(
            output_dir / f"client_{i}.npz",
            X=c_X,
            y=c_y,
        )
        metadata["client_distributions"][f"client_{i}"] = {
            "samples": len(c_y),
            "fraud_count": int(np.sum(c_y)),
            "fraud_rate": float(np.mean(c_y)) if len(c_y) > 0 else 0.0,
        }

    with open(output_dir / "partition_metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    print(f"[+] Successfully preprocessed IEEE-CIS into {n_clients} client partitions at {output_dir}")
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="IEEE-CIS dataset preprocessor")
    parser.add_argument("--data-dir", type=str, default=None)
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--limit", type=int, default=10000)
    parser.add_argument("--clients", type=int, default=5)
    parser.add_argument("--alpha", type=float, default=0.5)
    args = parser.parse_args()

    base_dir = Path(__file__).parent
    d_dir = Path(args.data_dir) if args.data_dir else base_dir
    o_dir = Path(args.output) if args.output else base_dir / "processed"

    preprocess_ieee_cis(d_dir, o_dir, sample_limit=args.limit, n_clients=args.clients, dirichlet_alpha=args.alpha)
