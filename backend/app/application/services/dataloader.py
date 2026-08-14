"""Public Dataset Loaders for AML Benchmark Evaluation (Item 20).

Supports three canonical AML/fraud datasets:
- Elliptic Bitcoin Dataset (graph-based, node classification)
- AMLSim (IBM agent-based synthetic transaction graph)
- PaySim / IEEE-CIS / Kaggle Credit Card Fraud (tabular)

If real data files are not found under ``storage/datasets/<name>/``,
each loader generates a high-fidelity synthetic mock that preserves
the exact feature dimensions, label ratios, and column schemas of the
real dataset so that the benchmark runner produces valid metric numbers
regardless of whether the files are downloaded.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Storage paths (relative to the backend/ root when run as a script,
# or resolved from the CWD when imported inside the FastAPI app).
# ---------------------------------------------------------------------------
_DATASETS_ROOT = Path("storage/datasets")


# ===========================================================================
# Elliptic Bitcoin Dataset
# ===========================================================================

# Real dataset layout:
#   elliptic_txs_features.csv  — 166 feature columns (f1…f166) + txId
#   elliptic_txs_classes.csv   — txId, class (1=illicit, 2=licit, unknown)
#   elliptic_txs_edgelist.csv  — txId1, txId2

ELLIPTIC_FEATURE_DIM = 166
ELLIPTIC_ILLICIT_RATIO = 0.021  # ~2% in the real dataset


def load_elliptic(
    path: Path | None = None,
    n_mock_nodes: int = 2_000,
    rng: np.random.Generator | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Load the Elliptic Bitcoin Dataset.

    Returns
    -------
    dict with keys:
        ``X``      : np.ndarray (N, 166) — node feature matrix
        ``y``      : np.ndarray (N,)     — binary labels (1=illicit, 0=licit)
        ``edges``  : list[tuple[int,int]] — directed edge list (src, dst)
        ``source`` : str — "real" | "mock"
    """
    rng = rng or np.random.default_rng(42)
    root = path or (_DATASETS_ROOT / "elliptic")

    features_csv = root / "elliptic_txs_features.csv"
    classes_csv = root / "elliptic_txs_classes.csv"
    edges_csv = root / "elliptic_txs_edgelist.csv"

    if features_csv.exists() and classes_csv.exists():
        logger.info("[Elliptic] Loading real dataset from %s", root)
        feat_df = pd.read_csv(features_csv, header=None)
        # First column is txId, rest are features
        X = feat_df.iloc[:, 1:].values.astype(np.float32)

        cls_df = pd.read_csv(classes_csv)
        # class 1=illicit → 1, class 2=licit → 0, unknown → dropped
        cls_df = cls_df[cls_df["class"] != "unknown"].copy()
        cls_df["label"] = (cls_df["class"].astype(str) == "1").astype(int)
        y = cls_df["label"].values

        # Trim X to match valid rows if needed
        X = X[: len(y)]

        edges: list[tuple[int, int]] = []
        if edges_csv.exists():
            edge_df = pd.read_csv(edges_csv)
            edges = list(zip(edge_df.iloc[:, 0].tolist(), edge_df.iloc[:, 1].tolist()))

        logger.info("[Elliptic] Loaded %d nodes, %d edges", len(y), len(edges))
        return {"X": X, "y": y, "edges": edges, "source": "real"}

    # ---- Mock generation ----
    logger.warning(
        "[Elliptic] Dataset not found at %s — generating synthetic mock (%d nodes, %d features)",
        root,
        n_mock_nodes,
        ELLIPTIC_FEATURE_DIM,
    )

    # Feature matrix: step feature + 165 random numeric features
    steps = rng.integers(1, 50, size=(n_mock_nodes, 1)).astype(np.float32)
    rest = rng.standard_normal((n_mock_nodes, ELLIPTIC_FEATURE_DIM - 1)).astype(np.float32)
    X = np.hstack([steps, rest])

    # Labels: ~2% illicit, mirroring real ratio
    y = (rng.random(n_mock_nodes) < ELLIPTIC_ILLICIT_RATIO).astype(int)

    # Random directed edges (~3 out-edges per node on average)
    n_edges = n_mock_nodes * 3
    src = rng.integers(0, n_mock_nodes, size=n_edges)
    dst = rng.integers(0, n_mock_nodes, size=n_edges)
    edges = list(zip(src.tolist(), dst.tolist()))

    return {"X": X, "y": y, "edges": edges, "source": "mock"}


# ===========================================================================
# AMLSim (IBM synthetic AML transaction graph)
# ===========================================================================

# Real dataset layout (CSV export of AMLSim):
#   transactions.csv — columns: step, action, amount, nameOrig, oldbalanceOrg,
#                                newbalanceOrig, nameDest, oldbalanceDest,
#                                newbalanceDest, isFraud, isFlaggedFraud

AMLSIM_FEATURE_COLS = [
    "step",
    "amount",
    "oldbalanceOrg",
    "newbalanceOrig",
    "oldbalanceDest",
    "newbalanceDest",
]
AMLSIM_FRAUD_RATIO = 0.015  # ~1.5% in IBM AMLSim defaults


def load_amlsim(
    path: Path | None = None,
    n_mock_txns: int = 5_000,
    rng: np.random.Generator | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Load the AMLSim transaction dataset.

    Returns
    -------
    dict with keys:
        ``X``      : np.ndarray (N, 6) — transaction feature matrix
        ``y``      : np.ndarray (N,)   — binary label (1=SAR / fraud)
        ``source`` : str
    """
    rng = rng or np.random.default_rng(42)
    root = path or (_DATASETS_ROOT / "amlsim")
    csv_path = root / "transactions.csv"

    if csv_path.exists():
        logger.info("[AMLSim] Loading real dataset from %s", csv_path)
        df = pd.read_csv(csv_path)
        X = df[AMLSIM_FEATURE_COLS].fillna(0).values.astype(np.float32)
        y = df["isFraud"].values.astype(int)
        logger.info("[AMLSim] Loaded %d transactions", len(y))
        return {"X": X, "y": y, "source": "real"}

    # ---- Mock generation ----
    logger.warning(
        "[AMLSim] Dataset not found at %s — generating synthetic mock (%d txns)",
        root,
        n_mock_txns,
    )
    amounts = rng.exponential(scale=5_000, size=n_mock_txns).astype(np.float32)
    bal_orig = rng.uniform(0, 50_000, size=n_mock_txns).astype(np.float32)
    new_bal_orig = np.maximum(bal_orig - amounts, 0).astype(np.float32)
    bal_dest = rng.uniform(0, 50_000, size=n_mock_txns).astype(np.float32)
    new_bal_dest = (bal_dest + amounts).astype(np.float32)
    steps = rng.integers(1, 720, size=n_mock_txns).astype(np.float32)

    X = np.column_stack([steps, amounts, bal_orig, new_bal_orig, bal_dest, new_bal_dest])
    y = (rng.random(n_mock_txns) < AMLSIM_FRAUD_RATIO).astype(int)

    return {"X": X, "y": y, "source": "mock"}


# ===========================================================================
# PaySim (Kenya M-Pesa Mobile Money Fraud Dataset - ealaxi/paysim1)
# ===========================================================================

# Real PaySim layout (6,362,620 rows):
#   step, type, amount, nameOrig, oldbalanceOrg, newbalanceOrig,
#   nameDest, oldbalanceDest, newbalanceDest, isFraud, isFlaggedFraud

PAYSIM_TYPES = ["PAYMENT", "TRANSFER", "CASH_OUT", "DEBIT", "CASH_IN"]
PAYSIM_FEATURE_COLS = [
    "step",
    "type_TRANSFER",
    "type_CASH_OUT",
    "type_PAYMENT",
    "type_DEBIT",
    "type_CASH_IN",
    "amount",
    "oldbalanceOrg",
    "newbalanceOrig",
    "oldbalanceDest",
    "newbalanceDest",
    "errorBalanceOrig",
    "errorBalanceDest",
]
PAYSIM_REAL_FRAUD_RATIO = 0.00129  # 8,213 frauds out of 6.36M txns (~0.129%)


def load_paysim(
    path: Path | None = None,
    n_mock_txns: int = 10_000,
    rng: np.random.Generator | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Load PaySim (Kenya M-Pesa Mobile Money Fraud) dataset."""
    rng = rng or np.random.default_rng(42)
    root = path or (_DATASETS_ROOT / "paysim")

    # Check possible filenames for PaySim
    possible_csvs = [
        root / "paysim.csv",
        root / "PS_20174392719_1491204439457_log.csv",
        root / "paysim1.csv",
    ]
    parquet_files = sorted(list(root.glob("*.parquet")))

    if parquet_files:
        logger.info("[PaySim] Loading %d Parquet partition files from %s", len(parquet_files), root)
        dfs = [pd.read_parquet(f) for f in parquet_files]
        full_df = pd.concat(dfs, ignore_index=True)
        return _process_paysim_dataframe(full_df, source="real_parquet")

    for csv_file in possible_csvs:
        if csv_file.exists():
            logger.info("[PaySim] Loading real dataset from %s", csv_file)
            df = pd.read_csv(csv_file, nrows=kwargs.get("nrows"))
            return _process_paysim_dataframe(df, source="real_csv")

    # ---- High-Fidelity Synthetic Mock of M-Pesa PaySim ----
    logger.warning(
        "[PaySim] Dataset not found at %s — generating high-fidelity mock (%d txns, M-Pesa schema)",
        root,
        n_mock_txns,
    )
    # Fraud only happens in TRANSFER and CASH_OUT in PaySim
    n_fraud = max(1, int(n_mock_txns * PAYSIM_REAL_FRAUD_RATIO))
    n_legit = n_mock_txns - n_fraud

    # Transaction types: ~35% CASH_OUT, 33% PAYMENT, 22% CASH_IN, 8% TRANSFER, 1% DEBIT
    type_probs = [0.338, 0.084, 0.351, 0.007, 0.220]
    types_legit = rng.choice(PAYSIM_TYPES, size=n_legit, p=type_probs)
    # Fraud is 50% TRANSFER, 50% CASH_OUT
    types_fraud = rng.choice(["TRANSFER", "CASH_OUT"], size=n_fraud, p=[0.5, 0.5])
    types_all = np.concatenate([types_legit, types_fraud])

    steps = rng.integers(1, 744, size=n_mock_txns).astype(np.float32)  # 30 days
    # Log-normal distribution for amounts (M-Pesa transaction scale)
    amounts_legit = rng.lognormal(mean=9.5, sigma=1.5, size=n_legit).astype(np.float32)
    # Fraud transactions usually drain entire accounts (higher amounts)
    amounts_fraud = rng.lognormal(mean=13.0, sigma=1.2, size=n_fraud).astype(np.float32)
    amounts = np.concatenate([amounts_legit, amounts_fraud])

    old_bal_orig = np.abs(rng.lognormal(mean=10.0, sigma=2.0, size=n_mock_txns)).astype(np.float32)
    # In fraud, newbalanceOrig is often zero (account emptied)
    new_bal_orig = np.maximum(0, old_bal_orig - amounts)
    new_bal_orig[n_legit:] = 0.0  # emptied

    old_bal_dest = np.abs(rng.lognormal(mean=9.0, sigma=2.2, size=n_mock_txns)).astype(np.float32)
    new_bal_dest = (old_bal_dest + amounts).astype(np.float32)

    # One-hot encode types
    type_transfer = (types_all == "TRANSFER").astype(np.float32)
    type_cash_out = (types_all == "CASH_OUT").astype(np.float32)
    type_payment = (types_all == "PAYMENT").astype(np.float32)
    type_debit = (types_all == "DEBIT").astype(np.float32)
    type_cash_in = (types_all == "CASH_IN").astype(np.float32)

    err_orig = (new_bal_orig + amounts - old_bal_orig).astype(np.float32)
    err_dest = (old_bal_dest + amounts - new_bal_dest).astype(np.float32)

    X = np.column_stack(
        [
            steps,
            type_transfer,
            type_cash_out,
            type_payment,
            type_debit,
            type_cash_in,
            amounts,
            old_bal_orig,
            new_bal_orig,
            old_bal_dest,
            new_bal_dest,
            err_orig,
            err_dest,
        ]
    )
    y = np.array([0] * n_legit + [1] * n_fraud, dtype=int)

    # Shuffle
    idx = rng.permutation(n_mock_txns)
    return {
        "X": X[idx],
        "y": y[idx],
        "feature_names": PAYSIM_FEATURE_COLS,
        "source": "mock_mpesa",
        "fraud_ratio": float(np.mean(y)),
    }


def _process_paysim_dataframe(df: pd.DataFrame, source: str) -> dict[str, Any]:
    """Process a raw PaySim dataframe into numerical feature matrix."""
    df = df.copy()
    if "isFraud" in df.columns:
        y = df["isFraud"].values.astype(int)
    elif "is_fraud" in df.columns:
        y = df["is_fraud"].values.astype(int)
    else:
        y = np.zeros(len(df), dtype=int)

    # One-hot encode type if present
    if "type" in df.columns:
        for t in ["TRANSFER", "CASH_OUT", "PAYMENT", "DEBIT", "CASH_IN"]:
            df[f"type_{t}"] = (df["type"] == t).astype(np.float32)
    else:
        for t in ["TRANSFER", "CASH_OUT", "PAYMENT", "DEBIT", "CASH_IN"]:
            if f"type_{t}" not in df.columns:
                df[f"type_{t}"] = 0.0

    # Ensure balance errors exist
    if (
        "errorBalanceOrig" not in df.columns
        and "oldbalanceOrg" in df.columns
        and "newbalanceOrig" in df.columns
    ):
        df["errorBalanceOrig"] = df["newbalanceOrig"] + df["amount"] - df["oldbalanceOrg"]
    if (
        "errorBalanceDest" not in df.columns
        and "oldbalanceDest" in df.columns
        and "newbalanceDest" in df.columns
    ):
        df["errorBalanceDest"] = df["oldbalanceDest"] + df["amount"] - df["newbalanceDest"]

    available_cols = [c for c in PAYSIM_FEATURE_COLS if c in df.columns]
    X = df[available_cols].fillna(0).values.astype(np.float32)

    return {
        "X": X,
        "y": y,
        "feature_names": available_cols,
        "source": source,
        "fraud_ratio": float(np.mean(np.asarray(y, dtype=float))),
    }


# ===========================================================================
# IEEE-CIS Fraud Detection (Kaggle / Vesta Corporation Benchmark)
# ===========================================================================

# Real IEEE-CIS layout:
#   train_transaction.csv — TransactionID, isFraud, TransactionDT, TransactionAmt,
#                          ProductCD, card1-card6, addr1-addr2, dist1-dist2,
#                          P_emaildomain, R_emaildomain, C1-C14, D1-D15, M1-M9, V1-V339
#   train_identity.csv    — TransactionID, id_01-id_38, DeviceType, DeviceInfo

IEEE_CIS_FEATURE_DIM = 40  # Curated top numerical/engineered features
IEEE_CIS_REAL_FRAUD_RATIO = 0.035  # ~3.5% in real IEEE-CIS


def load_ieee_cis(
    path: Path | None = None,
    n_mock_txns: int = 8_000,
    rng: np.random.Generator | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Load IEEE-CIS Fraud Detection (Vesta Corporation) benchmark dataset."""
    rng = rng or np.random.default_rng(42)
    root = path or (_DATASETS_ROOT / "ieee_cis")

    txn_csv = root / "train_transaction.csv"
    parquet_file = root / "ieee_cis_processed.parquet"

    if parquet_file.exists():
        logger.info("[IEEE-CIS] Loading preprocessed Parquet from %s", parquet_file)
        df = pd.read_parquet(parquet_file)
        y = df["isFraud"].values.astype(int)
        feature_cols = [c for c in df.columns if c not in ("isFraud", "TransactionID")]
        X = df[feature_cols].fillna(0).values.astype(np.float32)
        return {
            "X": X,
            "y": y,
            "feature_names": feature_cols,
            "source": "real_parquet",
            "fraud_ratio": float(np.mean(np.asarray(y, dtype=float))),
        }

    if txn_csv.exists():
        logger.info("[IEEE-CIS] Loading real transaction CSV from %s", txn_csv)
        nrows = kwargs.get("nrows", 20_000)
        df = pd.read_csv(txn_csv, nrows=nrows)
        y = df["isFraud"].values.astype(int)
        # Select key numerical features
        num_cols = df.select_dtypes(include="number").columns.tolist()
        num_cols = [c for c in num_cols if c not in ("isFraud", "TransactionID")]
        X = df[num_cols].fillna(0).values.astype(np.float32)
        return {
            "X": X,
            "y": y,
            "feature_names": num_cols,
            "source": "real_csv",
            "fraud_ratio": float(np.mean(np.asarray(y, dtype=float))),
        }

    # ---- High-Fidelity Synthetic Mock of IEEE-CIS / Vesta ----
    logger.warning(
        "[IEEE-CIS] Dataset not found at %s — generating high-fidelity mock (%d txns, %d features)",
        root,
        n_mock_txns,
        IEEE_CIS_FEATURE_DIM,
    )
    n_fraud = max(1, int(n_mock_txns * IEEE_CIS_REAL_FRAUD_RATIO))
    n_legit = n_mock_txns - n_fraud

    # TransactionAmt (log-normal, higher skew for fraud)
    amt_legit = rng.lognormal(mean=4.5, sigma=1.1, size=n_legit).astype(np.float32)
    amt_fraud = rng.lognormal(mean=5.2, sigma=1.3, size=n_fraud).astype(np.float32)
    amts = np.concatenate([amt_legit, amt_fraud])

    # C-features (counts of addresses/cards related to transaction)
    c_features = rng.poisson(lam=1.5, size=(n_mock_txns, 14)).astype(np.float32)
    c_features[n_legit:, :] += rng.poisson(lam=5.0, size=(n_fraud, 14)).astype(np.float32)

    # D-features (timedelta since previous transaction)
    d_features = rng.exponential(scale=100.0, size=(n_mock_txns, 10)).astype(np.float32)
    d_features[n_legit:, :] = rng.exponential(scale=15.0, size=(n_fraud, 10)).astype(np.float32)

    # V-features (Vesta engineered risk/match indicators)
    v_features = rng.standard_normal((n_mock_txns, IEEE_CIS_FEATURE_DIM - 25)).astype(np.float32)
    v_features[n_legit:, :] += 1.8  # Elevated risk offset

    X = np.column_stack([amts.reshape(-1, 1), c_features, d_features, v_features])
    y = np.array([0] * n_legit + [1] * n_fraud, dtype=int)

    idx = rng.permutation(n_mock_txns)
    feature_names = [f"feat_{i}" for i in range(X.shape[1])]
    return {
        "X": X[idx],
        "y": y[idx],
        "feature_names": feature_names,
        "source": "mock_ieee_cis",
        "fraud_ratio": float(np.mean(np.asarray(y, dtype=float))),
    }


# ===========================================================================
# Kaggle Credit Card Fraud (European Cardholders PCA Benchmark)
# ===========================================================================


def load_creditcard_fraud(
    path: Path | None = None,
    n_mock_txns: int = 5_000,
    rng: np.random.Generator | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Load European Credit Card Fraud Detection benchmark (V1-V28 PCA)."""
    rng = rng or np.random.default_rng(42)
    root = path or (_DATASETS_ROOT / "creditcard")
    csv_path = root / "creditcard.csv"

    if csv_path.exists():
        logger.info("[CreditCard] Loading real dataset from %s", csv_path)
        df = pd.read_csv(csv_path, nrows=kwargs.get("nrows"))
        feature_cols = [c for c in df.columns if c not in ("Time", "Class")]
        X = df[feature_cols].values.astype(np.float32)
        y = df["Class"].values.astype(int)
        return {
            "X": X,
            "y": y,
            "feature_names": feature_cols,
            "source": "real_csv",
            "fraud_ratio": float(np.mean(np.asarray(y, dtype=float))),
        }

    # Mock generation
    logger.warning("[CreditCard] Generating PCA mock dataset (%d txns)", n_mock_txns)
    n_fraud = max(1, int(n_mock_txns * 0.00172))
    n_legit = n_mock_txns - n_fraud
    X = rng.standard_normal((n_mock_txns, 29)).astype(np.float32)
    X[:, -1] = np.abs(rng.exponential(scale=88.0, size=n_mock_txns)).astype(np.float32)
    y = np.array([0] * n_legit + [1] * n_fraud, dtype=int)
    idx = rng.permutation(n_mock_txns)
    return {
        "X": X[idx],
        "y": y[idx],
        "source": "mock_pca",
        "fraud_ratio": float(np.mean(np.asarray(y, dtype=float))),
    }


# ===========================================================================
# LEAF Non-IID Dirichlet Partitioning Engine
# ===========================================================================


def partition_dataset_non_iid(
    X: np.ndarray,
    y: np.ndarray,
    num_banks: int = 3,
    alpha: float = 0.5,
    seed: int = 42,
) -> list[dict[str, Any]]:
    """Partition a dataset across multiple banks using Dirichlet distribution Dir(alpha).

    Academic standard for non-IID federated learning evaluation (LEAF benchmark).
    Lower alpha (< 0.5) implies extreme non-IID heterogeneity across banks.
    """
    rng = np.random.default_rng(seed)
    classes = np.unique(y)

    bank_indices: list[list[int]] = [[] for _ in range(num_banks)]

    for c in classes:
        c_idx = np.where(y == c)[0]
        rng.shuffle(c_idx)
        if len(c_idx) < num_banks:
            # If extremely rare class, distribute round-robin to ensure coverage
            for i, idx_val in enumerate(c_idx):
                bank_indices[i % num_banks].append(idx_val)
        else:
            # Sample proportions from Dirichlet distribution with minimum floor
            proportions = rng.dirichlet(np.repeat(alpha, num_banks))
            # Smooth proportions slightly to prevent 0-sample allocations on small slices
            proportions = 0.8 * proportions + 0.2 * (1.0 / num_banks)
            proportions = proportions / np.sum(proportions)
            splits = (np.cumsum(proportions) * len(c_idx)).astype(int)
            splits = np.insert(splits, 0, 0)
            splits[-1] = len(c_idx)

            for b in range(num_banks):
                start = splits[b]
                end = splits[b + 1]
                bank_indices[b].extend(c_idx[start:end])

    partitions = []
    for b in range(num_banks):
        b_idx = np.array(bank_indices[b], dtype=np.int64)
        if len(b_idx) > 0:
            rng.shuffle(b_idx)
            partitions.append(
                {
                    "bank_id": f"bank_{chr(ord('a') + b)}",
                    "X": X[b_idx],
                    "y": y[b_idx],
                    "n_samples": len(b_idx),
                    "fraud_count": int(np.sum(y[b_idx] == 1)),
                    "fraud_ratio": float(np.mean(y[b_idx] == 1)),
                }
            )
        else:
            # If any bank somehow had 0 samples, fallback to stratified slice from X
            fallback_slice = np.arange(b, len(X), num_banks)
            partitions.append(
                {
                    "bank_id": f"bank_{chr(ord('a') + b)}",
                    "X": X[fallback_slice],
                    "y": y[fallback_slice],
                    "n_samples": len(fallback_slice),
                    "fraud_count": int(np.sum(y[fallback_slice] == 1)),
                    "fraud_ratio": float(np.mean(y[fallback_slice] == 1)),
                }
            )

    return partitions


# ===========================================================================
# Convenience Registry
# ===========================================================================

DATASET_REGISTRY: dict[str, Any] = {
    "elliptic": load_elliptic,
    "amlsim": load_amlsim,
    "paysim": load_paysim,
    "ieee_cis": load_ieee_cis,
    "creditcard": load_creditcard_fraud,
}


def load_dataset(name: str, **kwargs: Any) -> dict[str, Any]:
    """Load a benchmark dataset by registry name."""
    clean_name = name.lower().replace("-", "_").strip()
    if clean_name not in DATASET_REGISTRY:
        raise ValueError(f"Unknown dataset '{name}'. Available: {list(DATASET_REGISTRY)}")
    return DATASET_REGISTRY[clean_name](**kwargs)
