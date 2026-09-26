"""Preprocesses PaySim dataset and generates federated client partitions with Dirichlet Non-IID skew."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def preprocess_paysim(
    csv_path: Path,
    output_dir: Path,
    sample_limit: int | None = None,
    n_clients: int = 5,
    dirichlet_alpha: float = 0.5,
    seed: int = 42,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)

    if not csv_path.exists():
        print(f"[-] Raw PaySim file not found at {csv_path}. Generating synthetic PaySim structure for testing...")
        rng = np.random.default_rng(seed)
        n_samples = sample_limit or 10000
        fraud_p = 0.005
        y = (rng.random(n_samples) < fraud_p).astype(int)
        amounts = rng.exponential(scale=500.0, size=n_samples)
        amounts[y == 1] *= 5.0
        types = rng.choice(["CASH_OUT", "TRANSFER", "PAYMENT", "CASH_IN", "DEBIT"], size=n_samples)
        df = pd.DataFrame({
            "step": rng.integers(1, 744, size=n_samples),
            "type": types,
            "amount": amounts,
            "oldbalanceOrg": rng.uniform(0, 100000, size=n_samples),
            "newbalanceOrig": rng.uniform(0, 100000, size=n_samples),
            "oldbalanceDest": rng.uniform(0, 100000, size=n_samples),
            "newbalanceDest": rng.uniform(0, 100000, size=n_samples),
            "isFraud": y,
        })
    else:
        print(f"[+] Loading raw PaySim dataset from {csv_path}...")
        nrows = sample_limit if sample_limit and sample_limit > 0 else None
        df = pd.read_csv(csv_path, nrows=nrows)

    # 1. Feature Engineering
    # Focus on TRANSFER and CASH_OUT (only transaction types with actual fraud in PaySim)
    relevant_types = ["TRANSFER", "CASH_OUT"]
    df_filtered = df[df["type"].isin(relevant_types)].copy()
    if len(df_filtered) == 0:
        df_filtered = df.copy()

    # Numerical features normalized
    df_filtered["orig_balance_delta"] = df_filtered["oldbalanceOrg"] - df_filtered["newbalanceOrig"]
    df_filtered["dest_balance_delta"] = df_filtered["newbalanceDest"] - df_filtered["oldbalanceDest"]
    df_filtered["hour_of_day"] = df_filtered["step"] % 24

    # One-hot encode type
    type_dummies = pd.get_dummies(df_filtered["type"], prefix="type", drop_first=False)
    feature_cols = [
        "amount",
        "oldbalanceOrg",
        "newbalanceOrig",
        "oldbalanceDest",
        "newbalanceDest",
        "orig_balance_delta",
        "dest_balance_delta",
        "hour_of_day",
    ]
    X_num = df_filtered[feature_cols].copy()
    # Min-max / robust scaling
    for col in feature_cols:
        col_max = X_num[col].max()
        col_min = X_num[col].min()
        if col_max > col_min:
            X_num[col] = (X_num[col] - col_min) / (col_max - col_min)
        else:
            X_num[col] = 0.0

    X = pd.concat([X_num, type_dummies], axis=1).values.astype(np.float32)
    y = df_filtered["isFraud"].values.astype(np.int64)

    # 2. Dirichlet Non-IID Partitioning
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
        "dataset": "PaySim",
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

    print(f"[+] Successfully preprocessed PaySim into {n_clients} client partitions at {output_dir}")
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PaySim dataset preprocessor")
    parser.add_argument("--csv", type=str, default=None, help="Path to raw PaySim CSV")
    parser.add_argument("--output", type=str, default=None, help="Output directory")
    parser.add_argument("--limit", type=int, default=10000, help="Max rows to process (default 10,000 for quick run)")
    parser.add_argument("--clients", type=int, default=5, help="Number of federated client partitions")
    parser.add_argument("--alpha", type=float, default=0.5, help="Dirichlet Non-IID alpha")
    args = parser.parse_args()

    base_dir = Path(__file__).parent
    csv_file = Path(args.csv) if args.csv else base_dir / "PS_20174392719_1491204439457_log.csv"
    out_dir = Path(args.output) if args.output else base_dir / "processed"

    preprocess_paysim(csv_file, out_dir, sample_limit=args.limit, n_clients=args.clients, dirichlet_alpha=args.alpha)
