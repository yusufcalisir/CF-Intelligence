"""Preprocesses Elliptic Bitcoin graph dataset enforcing temporal train/test split (timesteps 1-34 vs 35-49)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def preprocess_elliptic(
    data_dir: Path,
    output_dir: Path,
    temporal_split_step: int = 34,
    seed: int = 42,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)

    features_csv = data_dir / "elliptic_txs_features.csv"
    classes_csv = data_dir / "elliptic_txs_classes.csv"
    edges_csv = data_dir / "elliptic_txs_edgelist.csv"

    if not (features_csv.exists() and classes_csv.exists()):
        print("[-] Elliptic dataset files not found. Creating synthetic graph benchmark structure...")
        rng = np.random.default_rng(seed)
        n_nodes = 5000
        n_features = 166
        timesteps = rng.integers(1, 50, size=n_nodes)
        node_ids = np.arange(100000, 100000 + n_nodes)

        # Class 1 = illicit, Class 2 = licit, Class 3 = unknown
        class_probs = [0.10, 0.40, 0.50]
        raw_classes = rng.choice(["1", "2", "unknown"], p=class_probs, size=n_nodes)

        X = rng.standard_normal((n_nodes, n_features)).astype(np.float32)
        # Shift illicit nodes slightly
        illicit_mask = raw_classes == "1"
        X[illicit_mask, :10] += 1.5

        df_classes = pd.DataFrame({"txId": node_ids, "class": raw_classes})
        df_features = pd.DataFrame(X)
        df_features.insert(0, "time_step", timesteps)
        df_features.insert(0, "txId", node_ids)

        # Edges
        n_edges = 10000
        src = rng.choice(node_ids, size=n_edges)
        dst = rng.choice(node_ids, size=n_edges)
        df_edges = pd.DataFrame({"txId1": src, "txId2": dst})
    else:
        print("[+] Loading raw Elliptic files...")
        df_features = pd.read_csv(features_csv, header=None)
        df_features.columns = ["txId", "time_step"] + [f"feat_{i}" for i in range(df_features.shape[1] - 2)]
        df_classes = pd.read_csv(classes_csv)
        df_edges = pd.read_csv(edges_csv)

    # Merge features with classes
    merged = pd.merge(df_features, df_classes, on="txId", how="inner")

    # Filter out unknown classes (class == 'unknown' or '3')
    labeled = merged[merged["class"].isin(["1", "2", 1, 2])].copy()
    labeled["label"] = (labeled["class"].astype(str) == "1").astype(int)  # 1 = illicit, 0 = licit

    # Split temporally
    train_df = labeled[labeled["time_step"] <= temporal_split_step]
    test_df = labeled[labeled["time_step"] > temporal_split_step]

    feature_cols = [c for c in labeled.columns if c not in ["txId", "time_step", "class", "label"]]

    X_train = train_df[feature_cols].values.astype(np.float32)
    y_train = train_df["label"].values.astype(np.int64)
    train_ids = train_df["txId"].values

    X_test = test_df[feature_cols].values.astype(np.float32)
    y_test = test_df["label"].values.astype(np.int64)
    test_ids = test_df["txId"].values

    # Normalize features using train statistics only (prevent leakage)
    feat_mean = np.mean(X_train, axis=0, keepdims=True)
    feat_std = np.std(X_train, axis=0, keepdims=True) + 1e-8
    X_train = (X_train - feat_mean) / feat_std
    X_test = (X_test - feat_mean) / feat_std

    np.savez_compressed(
        output_dir / "elliptic_train.npz",
        X=X_train,
        y=y_train,
        node_ids=train_ids,
        timesteps=train_df["time_step"].values,
    )

    np.savez_compressed(
        output_dir / "elliptic_test.npz",
        X=X_test,
        y=y_test,
        node_ids=test_ids,
        timesteps=test_df["time_step"].values,
    )

    # Save edges
    df_edges.to_parquet(output_dir / "elliptic_edges.parquet", index=False)

    metadata: dict[str, Any] = {
        "dataset": "Elliptic Bitcoin Graph",
        "train_timesteps": f"1-{temporal_split_step}",
        "test_timesteps": f"{temporal_split_step+1}-49",
        "train_nodes": len(y_train),
        "train_illicit": int(np.sum(y_train)),
        "train_illicit_rate": float(np.mean(y_train)) if len(y_train) > 0 else 0.0,
        "test_nodes": len(y_test),
        "test_illicit": int(np.sum(y_test)),
        "test_illicit_rate": float(np.mean(y_test)) if len(y_test) > 0 else 0.0,
        "feature_dim": X_train.shape[1],
        "edges_count": len(df_edges),
    }

    with open(output_dir / "temporal_split_metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    print(f"[+] Successfully preprocessed Elliptic dataset at {output_dir}")
    print(f"    Train: {len(y_train)} nodes (illicit: {np.sum(y_train)}) | Test: {len(y_test)} nodes (illicit: {np.sum(y_test)})")
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Elliptic dataset preprocessor")
    parser.add_argument("--data-dir", type=str, default=None)
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--split-step", type=int, default=34)
    args = parser.parse_args()

    base_dir = Path(__file__).parent
    d_dir = Path(args.data_dir) if args.data_dir else base_dir
    o_dir = Path(args.output) if args.output else base_dir / "processed"

    preprocess_elliptic(d_dir, o_dir, temporal_split_step=args.split_step)
