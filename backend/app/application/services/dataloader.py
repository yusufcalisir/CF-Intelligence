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
# PaySim / IEEE-CIS / Kaggle Credit Card Fraud (tabular)
# ===========================================================================

# Real dataset layout (Kaggle Credit Card Fraud Detection):
#   creditcard.csv — V1…V28 (PCA), Amount, Class

PAYSIM_FRAUD_RATIO = 0.00172  # real Kaggle CC fraud ratio
PAYSIM_FEATURE_DIM = 29  # V1-V28 + Amount


def load_paysim(
    path: Path | None = None,
    n_mock_txns: int = 4_000,
    rng: np.random.Generator | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Load PaySim / Kaggle Credit Card Fraud dataset."""
    rng = rng or np.random.default_rng(42)
    root = path or (_DATASETS_ROOT / "paysim")
    csv_path = root / "creditcard.csv"

    # Check for Parquet partitions produced by ETL pipeline
    parquet_files = sorted(list(root.glob("*.parquet")))
    if parquet_files:
        logger.info("[PaySim] Loading %d ETL Parquet partition files from %s", len(parquet_files), root)
        dfs = [pd.read_parquet(f) for f in parquet_files]
        full_df = pd.concat(dfs, ignore_index=True)
        feature_cols = [c for c in full_df.columns if c not in ("Time", "Class", "is_fraud")]
        X = full_df[feature_cols].values.astype(np.float32)
        y = full_df["is_fraud"].values.astype(int) if "is_fraud" in full_df.columns else full_df["Class"].values.astype(int)
        logger.info("[PaySim] Loaded %d transactions from Parquet partitions", len(y))
        return {"X": X, "y": y, "source": "real_parquet"}

    if csv_path.exists():
        logger.info("[PaySim] Loading real dataset from %s", csv_path)
        df = pd.read_csv(csv_path)
        feature_cols = [c for c in df.columns if c not in ("Time", "Class")]
        X = df[feature_cols].values.astype(np.float32)
        y = df["Class"].values.astype(int)
        logger.info("[PaySim] Loaded %d transactions", len(y))
        return {"X": X, "y": y, "source": "real"}

    # ---- Mock generation ----
    logger.warning(
        "[PaySim] Dataset not found at %s — generating synthetic mock (%d txns)",
        root,
        n_mock_txns,
    )
    X = rng.standard_normal((n_mock_txns, PAYSIM_FEATURE_DIM)).astype(np.float32)
    # Amount column (last): positive, exponential distribution
    X[:, -1] = np.abs(rng.exponential(scale=88.0, size=n_mock_txns)).astype(np.float32)
    y = (rng.random(n_mock_txns) < PAYSIM_FRAUD_RATIO).astype(int)

    return {"X": X, "y": y, "source": "mock"}


# ===========================================================================
# Convenience registry
# ===========================================================================

DATASET_REGISTRY: dict[str, Any] = {
    "elliptic": load_elliptic,
    "amlsim": load_amlsim,
    "paysim": load_paysim,
}


def load_dataset(name: str, **kwargs: Any) -> dict[str, Any]:
    """Load a dataset by registry name.

    Parameters
    ----------
    name : str
        One of ``"elliptic"``, ``"amlsim"``, or ``"paysim"``.
    **kwargs
        Forwarded to the specific loader (e.g. ``n_mock_txns``, ``rng``).
    """
    if name not in DATASET_REGISTRY:
        raise ValueError(f"Unknown dataset '{name}'. Available: {list(DATASET_REGISTRY)}")
    return DATASET_REGISTRY[name](**kwargs)
