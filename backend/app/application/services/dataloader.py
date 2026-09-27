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

from app.infrastructure.storage.storage_utils import get_storage_dir

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Storage paths (relative to get_storage_dir() or local repository storage)
# ---------------------------------------------------------------------------
def resolve_dataset_dir(dataset_name: str, explicit_path: Path | str | None = None) -> Path:
    """Resolve storage directory for a target dataset across candidate locations.

    Checks candidates in order:
    1. Explicit path if passed and non-empty.
    2. Candidate paths containing actual dataset files (*.csv, *.parquet):
       - Path(get_storage_dir()) / "datasets" / clean_name
       - project_root / "storage" / "datasets" / clean_name
       - project_root / "backend" / "storage" / "datasets" / clean_name
       - cwd / "storage" / "datasets" / clean_name
       - cwd / "backend" / "storage" / "datasets" / clean_name
    3. First existing candidate directory, or fallback to default candidate.
    """
    if explicit_path is not None and str(explicit_path).strip():
        return Path(explicit_path)

    clean_name = dataset_name.lower().replace("-", "_").strip()
    candidates: list[Path] = []

    # Storage dir from centralized utility
    candidates.append(Path(get_storage_dir()) / "datasets" / clean_name)

    # Walk up to locate project root from this source file
    here = Path(__file__).resolve()
    curr = here
    for _ in range(5):
        curr = curr.parent
        candidates.append(curr / "storage" / "datasets" / clean_name)
        candidates.append(curr / "backend" / "storage" / "datasets" / clean_name)

    # Current working directory candidates
    cwd = Path.cwd().resolve()
    candidates.append(cwd / "storage" / "datasets" / clean_name)
    candidates.append(cwd / "backend" / "storage" / "datasets" / clean_name)

    # Return first candidate with actual data files
    for c in candidates:
        if c.is_dir():
            has_data = (
                any(c.glob("*.csv"))
                or any(c.glob("*.parquet"))
                or any(c.glob("*.txt"))
            )
            if has_data:
                return c

    # Return first candidate directory that exists
    for c in candidates:
        if c.is_dir():
            return c

    return candidates[0]


def _get_datasets_root() -> Path:
    storage_root = Path(get_storage_dir()) / "datasets"
    if storage_root.exists():
        return storage_root
    local_storage = Path("storage/datasets")
    if local_storage.exists():
        return local_storage
    return storage_root


_DATASETS_ROOT = _get_datasets_root()


# ===========================================================================
# Elliptic Bitcoin Dataset
# ===========================================================================

# Real dataset layout:
#   elliptic_txs_features.csv  — 166 feature columns (f1…f166) + txId
#   elliptic_txs_classes.csv   — txId, class (1=illicit, 2=licit, unknown)
#   elliptic_txs_edgelist.csv  — txId1, txId2

ELLIPTIC_FEATURE_DIM = 166
ELLIPTIC_ILLICIT_RATIO = 0.021  # ~2% in the real dataset


def _make_elliptic_pyg_data(
    X: np.ndarray,
    y: np.ndarray,
    edge_index: np.ndarray,
    timesteps: np.ndarray,
    temporal_split: bool = False,
    train_mask: np.ndarray | None = None,
    test_mask: np.ndarray | None = None,
    train_labeled_mask: np.ndarray | None = None,
    test_labeled_mask: np.ndarray | None = None,
) -> Any:
    """Helper to convert graph matrices into PyTorch Geometric Data or tensor dict."""
    import torch

    data_dict: dict[str, Any] = {
        "x": torch.from_numpy(X),
        "y": torch.from_numpy(y),
        "edge_index": torch.from_numpy(edge_index),
        "timesteps": torch.from_numpy(timesteps),
    }
    if temporal_split and train_mask is not None and test_mask is not None:
        data_dict["train_mask"] = torch.from_numpy(train_mask)
        data_dict["test_mask"] = torch.from_numpy(test_mask)
        if train_labeled_mask is not None:
            data_dict["train_labeled_mask"] = torch.from_numpy(train_labeled_mask)
        if test_labeled_mask is not None:
            data_dict["test_labeled_mask"] = torch.from_numpy(test_labeled_mask)
    try:
        from torch_geometric.data import Data
        return Data(**data_dict)
    except ImportError:
        return data_dict


def _make_elliptic_networkx_graph(
    y: np.ndarray,
    tx_ids: np.ndarray,
    timesteps: np.ndarray,
    edges: list[tuple[int, int]],
    max_nodes: int | None = None,
) -> Any:
    """Helper to convert node arrays and edges into NetworkX DiGraph."""
    import networkx as nx

    G = nx.DiGraph()
    n_limit = len(y) if max_nodes is None else min(len(y), max_nodes)
    for i in range(n_limit):
        G.add_node(i, txId=tx_ids[i], timestep=int(timesteps[i]), label=int(y[i]))
    for u, v in edges:
        if u < n_limit and v < n_limit:
            G.add_edge(u, v)
    return G


def _generate_mock_elliptic(
    n_mock_nodes: int,
    rng: np.random.Generator,
    include_unknown: bool = False,
    temporal_split: bool = False,
    split_timestep: int = 34,
) -> dict[str, Any]:
    """Generate high-fidelity synthetic mock Elliptic graph."""
    logger.info(
        "[Elliptic] Generating synthetic mock (%d nodes, %d features)",
        n_mock_nodes,
        ELLIPTIC_FEATURE_DIM,
    )

    timesteps = rng.integers(1, 50, size=n_mock_nodes).astype(int)
    steps = timesteps.reshape(-1, 1).astype(np.float32)
    rest = rng.standard_normal((n_mock_nodes, ELLIPTIC_FEATURE_DIM - 1)).astype(np.float32)
    X = np.hstack([steps, rest])

    if include_unknown:
        rand_vals = rng.random(n_mock_nodes)
        y = np.where(rand_vals < 0.05, 1, np.where(rand_vals < 0.35, 0, -1)).astype(int)
        if temporal_split:
            test_indices = np.where(timesteps > split_timestep)[0]
            if len(test_indices) >= 4:
                y[test_indices[0]] = 1
                y[test_indices[1]] = 1
                y[test_indices[2]] = 0
                y[test_indices[3]] = 0
            train_indices = np.where(timesteps <= split_timestep)[0]
            if len(train_indices) >= 4:
                y[train_indices[0]] = 1
                y[train_indices[1]] = 1
                y[train_indices[2]] = 0
                y[train_indices[3]] = 0
    else:
        y = (rng.random(n_mock_nodes) < ELLIPTIC_ILLICIT_RATIO).astype(int)
        if temporal_split:
            test_indices = np.where(timesteps > split_timestep)[0]
            if len(test_indices) >= 2:
                y[test_indices[0]] = 1
                y[test_indices[1]] = 0

    # Generate intra-timestep directed edges (~3 out-edges per node)
    edges: list[tuple[int, int]] = []
    nodes_by_ts: dict[int, list[int]] = {}
    for idx_node, ts in enumerate(timesteps):
        nodes_by_ts.setdefault(int(ts), []).append(idx_node)

    for ts_nodes in nodes_by_ts.values():
        if len(ts_nodes) > 1:
            n_ts_edges = min(len(ts_nodes) * 3, len(ts_nodes) * (len(ts_nodes) - 1))
            src_sample = rng.choice(ts_nodes, size=n_ts_edges, replace=True)
            dst_sample = rng.choice(ts_nodes, size=n_ts_edges, replace=True)
            for s, d in zip(src_sample, dst_sample, strict=False):
                if s != d:
                    edges.append((int(s), int(d)))

    if not edges:
        edges = [(0, 1)] if n_mock_nodes > 1 else []

    edge_index = (
        np.array([[e[0] for e in edges], [e[1] for e in edges]], dtype=np.int64)
        if edges
        else np.zeros((2, 0), dtype=np.int64)
    )

    adjacency_lists: list[list[int]] = [[] for _ in range(n_mock_nodes)]
    for u, v in edges:
        if 0 <= u < n_mock_nodes and 0 <= v < n_mock_nodes:
            adjacency_lists[u].append(v)
            adjacency_lists[v].append(u)

    tx_ids = np.array([f"mock_tx_{i}" for i in range(n_mock_nodes)])
    tx_to_idx = {tx_id: idx for idx, tx_id in enumerate(tx_ids)}
    idx_to_tx = {idx: tx_id for idx, tx_id in enumerate(tx_ids)}

    labeled_mask = y != -1
    fraud_ratio = float(np.mean(y[labeled_mask] == 1)) if np.any(labeled_mask) else 0.0

    train_mask = timesteps <= split_timestep if temporal_split else None
    test_mask = timesteps > split_timestep if temporal_split else None
    train_labeled_mask = (train_mask & (y != -1)) if (temporal_split and train_mask is not None and include_unknown) else train_mask
    test_labeled_mask = (test_mask & (y != -1)) if (temporal_split and test_mask is not None and include_unknown) else test_mask

    def to_pyg_data() -> Any:
        return _make_elliptic_pyg_data(
            X=X,
            y=y,
            edge_index=edge_index,
            timesteps=timesteps,
            temporal_split=temporal_split,
            train_mask=train_mask,
            test_mask=test_mask,
            train_labeled_mask=train_labeled_mask,
            test_labeled_mask=test_labeled_mask,
        )

    def to_networkx(max_nodes: int | None = None) -> Any:
        return _make_elliptic_networkx_graph(
            y=y,
            tx_ids=tx_ids,
            timesteps=timesteps,
            edges=edges,
            max_nodes=max_nodes,
        )

    mock_res: dict[str, Any] = {
        "X": X,
        "y": y,
        "edges": edges,
        "edge_index": edge_index,
        "adjacency_lists": adjacency_lists,
        "timesteps": timesteps,
        "tx_ids": tx_ids,
        "tx_to_idx": tx_to_idx,
        "idx_to_tx": idx_to_tx,
        "source": "mock",
        "fraud_ratio": fraud_ratio,
        "to_pyg_data": to_pyg_data,
        "to_networkx": to_networkx,
    }

    if temporal_split and train_mask is not None and test_mask is not None and train_labeled_mask is not None and test_labeled_mask is not None:
        mock_res.update({
            "train_mask": train_mask,
            "test_mask": test_mask,
            "train_labeled_mask": train_labeled_mask,
            "test_labeled_mask": test_labeled_mask,
            "split_timestep": split_timestep,
            "n_train": int(np.sum(train_mask)),
            "n_test": int(np.sum(test_mask)),
            "n_train_labeled": int(np.sum(train_labeled_mask)),
            "n_test_labeled": int(np.sum(test_labeled_mask)),
        })

    return mock_res


def load_elliptic(
    path: Path | None = None,
    nrows: int | None = None,
    n_mock_nodes: int = 2_000,
    rng: np.random.Generator | None = None,
    require_real: bool = False,
    all_rows: bool = False,
    include_unknown: bool = False,
    temporal_split: bool = False,
    split_timestep: int = 34,
    max_timesteps: int | None = None,
    construct_graph: bool = True,
    use_cache: bool = True,
    force_mock: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Load the Elliptic Bitcoin Transaction Graph Dataset.

    Supports:
    - Real dataset loading via accelerated Parquet cache or raw CSVs.
    - Zero future-leakage temporal split (timesteps 1..split_timestep train, split_timestep+1..49 test).
    - Inclusion or exclusion of unknown background transactions (y = -1).
    - PyTorch Geometric compatible edge_index (2, E) and undirected/directed adjacency lists.
    - Export helpers to_pyg_data() and to_networkx().

    Returns
    -------
    dict with keys:
        ``X``               : np.ndarray (N, 166) — node feature matrix (col 0: timestep, cols 1..165: features)
        ``y``               : np.ndarray (N,)     — node labels (1=illicit, 0=licit, -1=unknown if include_unknown)
        ``edges``           : list[tuple[int,int]] — directed edge list (src, dst)
        ``edge_index``      : np.ndarray (2, E)   — PyG-compatible 2xE edge index array
        ``adjacency_lists`` : list[list[int]]     — per-node neighbor lists for message passing
        ``timesteps``       : np.ndarray (N,)     — discrete timestep per transaction (1..49)
        ``tx_ids``          : np.ndarray (N,)     — original transaction IDs
        ``tx_to_idx``       : dict[str, int]      — map from transaction ID to node index
        ``idx_to_tx``       : dict[int, str]      — map from node index to transaction ID
        ``source``          : str                 — "real" | "mock"
        ``fraud_ratio``     : float               — ratio of illicit nodes among labeled nodes
        ``to_pyg_data``     : callable            — exports graph to PyTorch Geometric Data object or dict
        ``to_networkx``     : callable            — exports graph to NetworkX DiGraph
        (If temporal_split=True):
        ``train_mask``      : np.ndarray (N,) bool — True for timestep <= split_timestep
        ``test_mask``       : np.ndarray (N,) bool — True for timestep > split_timestep
        ``train_labeled_mask``: np.ndarray (N,) bool — True for train nodes with y in {0, 1}
        ``test_labeled_mask`` : np.ndarray (N,) bool — True for test nodes with y in {0, 1}
        ``split_timestep``  : int
        ``n_train``         : int
        ``n_test``          : int
        ``n_train_labeled`` : int
        ``n_test_labeled``  : int
    """
    rng = rng or np.random.default_rng(42)
    target_nodes = kwargs.get("n_mock_nodes") or kwargs.get("n_mock_txns") or kwargs.get("nrows") or nrows or n_mock_nodes
    n_mock_nodes = int(target_nodes)
    target_nrows = None if all_rows else (nrows or (int(kwargs["nrows"]) if "nrows" in kwargs and kwargs["nrows"] is not None else None))
    root = resolve_dataset_dir("elliptic", path)

    features_csv = root / "elliptic_txs_features.csv"
    classes_csv = root / "elliptic_txs_classes.csv"
    edges_csv = root / "elliptic_txs_edgelist.csv"
    parquet_cache = root / "elliptic_cache.parquet"

    if not features_csv.exists():
        cand_feat = list(root.glob("*features*.csv"))
        if cand_feat:
            features_csv = cand_feat[0]
    if not classes_csv.exists():
        cand_cls = list(root.glob("*classes*.csv"))
        if cand_cls:
            classes_csv = cand_cls[0]
    if not edges_csv.exists():
        cand_edges = list(root.glob("*edgelist*.csv"))
        if cand_edges:
            edges_csv = cand_edges[0]
    if not parquet_cache.exists():
        cand_pq = list(root.glob("*.parquet"))
        if cand_pq:
            parquet_cache = cand_pq[0]

    has_real_files = parquet_cache.exists() or (features_csv.exists() and classes_csv.exists())

    # Decide real vs mock
    should_load_real = (
        not force_mock
        and (
            require_real
            or all_rows
            or temporal_split
            or path is not None
            or ("nrows" in kwargs and kwargs["nrows"] is not None)
            or (nrows is not None and not kwargs.get("n_mock_nodes"))
            or kwargs.get("use_real", False)
        )
        and has_real_files
    )

    if should_load_real:
        logger.info("[Elliptic] Loading real dataset from %s (use_cache=%s)", root, use_cache)
        df: pd.DataFrame | None = None

        if use_cache and parquet_cache.exists():
            try:
                df = pd.read_parquet(parquet_cache)
                logger.info("[Elliptic] Loaded Parquet cache with shape %s", df.shape)
            except Exception as e:
                logger.warning("[Elliptic] Failed reading Parquet cache (%s), falling back to CSV", e)
                df = None

        if df is None:
            read_nrows = max(int(target_nrows) * 5, 2000) if (target_nrows and not all_rows) else None
            feat_df = pd.read_csv(features_csv, header=None, nrows=read_nrows)
            feat_df.rename(columns={0: "txId"}, inplace=True)
            feat_df["txId"] = feat_df["txId"].astype(str)

            cls_df = pd.read_csv(classes_csv, nrows=read_nrows)
            cls_df["txId"] = cls_df["txId"].astype(str)

            merged_df = pd.merge(cls_df, feat_df, on="txId", how="inner")
            # In raw CSV, column 1 is timestep
            if 1 in merged_df.columns:
                merged_df.rename(columns={1: "timestep"}, inplace=True)
            df = merged_df

        # Filter max timesteps if requested
        if max_timesteps is not None and "timestep" in df.columns:
            df = df[df["timestep"] <= max_timesteps].copy()

        # Class handling: 1=illicit, 2=licit, unknown
        class_col = "class" if "class" in df.columns else ("label" if "label" in df.columns else None)
        if class_col is not None:
            c_str = df[class_col].astype(str)
            if not include_unknown:
                df = df[c_str.isin(["1", "2"])].copy()
                c_str = df[class_col].astype(str)
                y = (c_str == "1").values.astype(int)
            else:
                y = np.where(c_str == "1", 1, np.where(c_str == "2", 0, -1)).astype(int)
        else:
            y = np.zeros(len(df), dtype=int)

        if target_nrows is not None and not all_rows and len(df) > target_nrows:
            df = df.iloc[:target_nrows].copy()
            y = y[:target_nrows]

        # Extract features X: timestep (col 0) + 165 numeric features
        feature_cols = [c for c in df.columns if c not in ("txId", "class", "label")]
        X = df[feature_cols].values.astype(np.float32)

        # Timesteps
        if "timestep" in df.columns:
            timesteps = df["timestep"].values.astype(int)
        elif X.shape[1] > 0:
            timesteps = X[:, 0].astype(int)
        else:
            timesteps = np.ones(len(y), dtype=int)

        tx_ids = df["txId"].astype(str).values if "txId" in df.columns else np.array([str(i) for i in range(len(y))])
        tx_to_idx = {tx_id: idx for idx, tx_id in enumerate(tx_ids)}
        idx_to_tx = {idx: tx_id for idx, tx_id in enumerate(tx_ids)}

        # Build graph topology
        edges: list[tuple[int, int]] = []
        if construct_graph and edges_csv.exists():
            read_edge_rows = None if all_rows else (max(int(target_nrows) * 10, 5000) if target_nrows else None)
            edge_df = pd.read_csv(edges_csv, nrows=read_edge_rows)
            src_col = edge_df.columns[0]
            dst_col = edge_df.columns[1]

            src_mapped = edge_df[src_col].astype(str).map(tx_to_idx)
            dst_mapped = edge_df[dst_col].astype(str).map(tx_to_idx)
            valid = src_mapped.notna() & dst_mapped.notna()

            src_arr = src_mapped[valid].astype(int).values
            dst_arr = dst_mapped[valid].astype(int).values
            edges = list(zip(src_arr.tolist(), dst_arr.tolist(), strict=False))
            edge_index = np.vstack([src_arr, dst_arr]).astype(np.int64) if len(edges) > 0 else np.zeros((2, 0), dtype=np.int64)
        else:
            edge_index = np.zeros((2, 0), dtype=np.int64)

        # Build adjacency lists for message passing
        adjacency_lists: list[list[int]] = [[] for _ in range(len(y))]
        for u, v in edges:
            if 0 <= u < len(y) and 0 <= v < len(y):
                adjacency_lists[u].append(v)
                adjacency_lists[v].append(u)

        labeled_mask = y != -1
        fraud_ratio = float(np.mean(y[labeled_mask] == 1)) if np.any(labeled_mask) else 0.0

        train_mask = timesteps <= split_timestep if temporal_split else None
        test_mask = timesteps > split_timestep if temporal_split else None
        train_labeled_mask = (train_mask & (y != -1)) if (temporal_split and train_mask is not None and include_unknown) else train_mask
        test_labeled_mask = (test_mask & (y != -1)) if (temporal_split and test_mask is not None and include_unknown) else test_mask

        def to_pyg_data() -> Any:
            return _make_elliptic_pyg_data(
                X=X,
                y=y,
                edge_index=edge_index,
                timesteps=timesteps,
                temporal_split=temporal_split,
                train_mask=train_mask,
                test_mask=test_mask,
                train_labeled_mask=train_labeled_mask,
                test_labeled_mask=test_labeled_mask,
            )

        def to_networkx(max_nodes: int | None = None) -> Any:
            return _make_elliptic_networkx_graph(
                y=y,
                tx_ids=tx_ids,
                timesteps=timesteps,
                edges=edges,
                max_nodes=max_nodes,
            )

        result: dict[str, Any] = {
            "X": X,
            "y": y,
            "edges": edges,
            "edge_index": edge_index,
            "adjacency_lists": adjacency_lists,
            "timesteps": timesteps,
            "tx_ids": tx_ids,
            "tx_to_idx": tx_to_idx,
            "idx_to_tx": idx_to_tx,
            "source": "real",
            "fraud_ratio": fraud_ratio,
            "to_pyg_data": to_pyg_data,
            "to_networkx": to_networkx,
        }

        if temporal_split and train_mask is not None and test_mask is not None and train_labeled_mask is not None and test_labeled_mask is not None:
            result.update({
                "train_mask": train_mask,
                "test_mask": test_mask,
                "train_labeled_mask": train_labeled_mask,
                "test_labeled_mask": test_labeled_mask,
                "split_timestep": split_timestep,
                "n_train": int(np.sum(train_mask)),
                "n_test": int(np.sum(test_mask)),
                "n_train_labeled": int(np.sum(train_labeled_mask)),
                "n_test_labeled": int(np.sum(test_labeled_mask)),
            })

        logger.info(
            "[Elliptic] Successfully loaded real dataset: %d nodes, %d edges, fraud_ratio=%.4f",
            len(y),
            len(edges),
            fraud_ratio,
        )
        return result

    if require_real:
        raise FileNotFoundError(
            f"Real Elliptic Bitcoin dataset files not found in '{root}'. "
            f"Expected 'elliptic_cache.parquet' or ('elliptic_txs_features.csv' and 'elliptic_txs_classes.csv'). "
            f"Synthetic fallback is disabled under strict real-data mode."
        )

    return _generate_mock_elliptic(
        n_mock_nodes=n_mock_nodes,
        rng=rng,
        include_unknown=include_unknown,
        temporal_split=temporal_split,
        split_timestep=split_timestep,
    )


# ===========================================================================
# AMLSim (IBM Research Agent-Based Anti-Money Laundering Synthetic Graph)
# ===========================================================================

# Canonical IBM AMLSim simulator layout:
#   transactions.csv — TX_ID, SENDER_ACCOUNT_ID, RECEIVER_ACCOUNT_ID, TX_TYPE,
#                      TX_AMOUNT, TIMESTAMP, IS_FRAUD, ALERT_ID
#   accounts.csv     — ACCOUNT_ID, CUSTOMER_ID, INIT_BALANCE, COUNTRY, ACCOUNT_TYPE,
#                      IS_FRAUD, TX_BEHAVIOR_ID
#   alerts.csv       — ALERT_ID, ALERT_TYPE (fan_in, cycle, fan_out), IS_FRAUD,
#                      TX_ID, SENDER_ACCOUNT_ID, RECEIVER_ACCOUNT_ID, TX_TYPE, TX_AMOUNT, TIMESTAMP

AMLSIM_FEATURE_COLS = [
    "step",
    "amount",
    "oldbalanceOrg",
    "newbalanceOrig",
    "oldbalanceDest",
    "newbalanceDest",
]
AMLSIM_FRAUD_RATIO = 0.0013  # ~0.13% in real 1.32M AMLSim benchmark (1,719 frauds)


def _make_amlsim_pyg_data(
    X: np.ndarray,
    y: np.ndarray,
    edge_index: np.ndarray,
    timesteps: np.ndarray | None = None,
    alert_types: list[str] | np.ndarray | None = None,
) -> Any:
    """Helper to convert AMLSim matrices into PyTorch Geometric Data or tensor dict."""
    import torch

    data_dict: dict[str, Any] = {
        "x": torch.from_numpy(X),
        "y": torch.from_numpy(y),
        "edge_index": torch.from_numpy(edge_index),
    }
    if timesteps is not None:
        data_dict["timesteps"] = torch.from_numpy(timesteps)
    if alert_types is not None:
        data_dict["alert_types"] = list(alert_types)
    try:
        from torch_geometric.data import Data
        return Data(**data_dict)
    except ImportError:
        return data_dict


def _make_amlsim_networkx_graph(
    edges: list[tuple[int, int]],
    y: np.ndarray | None = None,
    tx_ids: np.ndarray | None = None,
    amounts: np.ndarray | None = None,
    timesteps: np.ndarray | None = None,
    alert_types: list[str] | np.ndarray | None = None,
    max_edges: int | None = None,
) -> Any:
    """Helper to convert AMLSim transactions into a NetworkX DiGraph."""
    try:
        import networkx as nx
    except ImportError:
        logger.warning("networkx is not installed; to_networkx returns None")
        return None

    G = nx.DiGraph()
    limit = len(edges) if max_edges is None else min(len(edges), max_edges)

    for i in range(limit):
        u, v = edges[i]
        attr: dict[str, Any] = {}
        if y is not None and i < len(y):
            attr["is_fraud"] = int(y[i])
        if tx_ids is not None and i < len(tx_ids):
            attr["tx_id"] = int(tx_ids[i])
        if amounts is not None and i < len(amounts):
            attr["amount"] = float(amounts[i])
        if timesteps is not None and i < len(timesteps):
            attr["step"] = int(timesteps[i])
        if alert_types is not None and i < len(alert_types):
            attr["alert_type"] = str(alert_types[i])
        G.add_edge(u, v, **attr)

    return G


def _process_amlsim_dataframe(
    df: pd.DataFrame,
    root: Path,
    source: str = "real_csv",
    temporal_split: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Process a raw AMLSim DataFrame into standardized features, labels, and graph structures."""
    # 1. Label detection
    label_col = next((c for c in ["IS_FRAUD", "isFraud", "is_fraud", "Is Laundering", "is_laundering", "label"] if c in df.columns), None)
    if label_col is not None:
        y = df[label_col].astype(bool).astype(int).values
    else:
        y = np.zeros(len(df), dtype=int)

    # 2. Check for alerts.csv
    alerts_csv = root / "alerts.csv"
    alerts_df = None
    alert_types = np.array(["none"] * len(df), dtype=object)
    if alerts_csv.exists():
        try:
            alerts_df = pd.read_csv(alerts_csv)
            if "ALERT_ID" in alerts_df.columns and "ALERT_TYPE" in alerts_df.columns and "ALERT_ID" in df.columns:
                al_map = alerts_df.drop_duplicates("ALERT_ID").set_index("ALERT_ID")["ALERT_TYPE"].to_dict()
                alert_types = np.array([al_map.get(aid, "none") for aid in df["ALERT_ID"].values], dtype=object)
        except Exception as exc:
            logger.warning("[AMLSim] Failed to load alerts.csv: %s", exc)

    # 3. Check for accounts.csv
    accounts_csv = root / "accounts.csv"
    accounts_df = None
    acc_map: dict[int, float] = {}
    if accounts_csv.exists():
        try:
            accounts_df = pd.read_csv(accounts_csv)
            if "ACCOUNT_ID" in accounts_df.columns and "INIT_BALANCE" in accounts_df.columns:
                acc_map = accounts_df.set_index("ACCOUNT_ID")["INIT_BALANCE"].to_dict()
        except Exception as exc:
            logger.warning("[AMLSim] Failed to load accounts.csv: %s", exc)

    # 4. Feature and graph extraction
    sender_col = next((c for c in ["SENDER_ACCOUNT_ID", "nameOrig", "From Bank", "Account", "sender"] if c in df.columns), None)
    receiver_col = next((c for c in ["RECEIVER_ACCOUNT_ID", "nameDest", "To Bank", "Account.1", "receiver"] if c in df.columns), None)
    amount_col = next((c for c in ["TX_AMOUNT", "amount", "Amount Received", "Amount Paid"] if c in df.columns), None)
    step_col = next((c for c in ["TIMESTAMP", "step", "Timestamp", "time"] if c in df.columns), None)
    tx_id_col = next((c for c in ["TX_ID", "tx_id", "transaction_id"] if c in df.columns), None)

    if sender_col and receiver_col and amount_col and step_col:
        steps = df[step_col].fillna(0).values.astype(np.float32)
        amounts = df[amount_col].fillna(0.0).values.astype(np.float32)
        if acc_map:
            bal_orig = df[sender_col].map(acc_map).fillna(0.0).values.astype(np.float32)
            bal_dest = df[receiver_col].map(acc_map).fillna(0.0).values.astype(np.float32)
        elif "oldbalanceOrg" in df.columns and "oldbalanceDest" in df.columns:
            bal_orig = df["oldbalanceOrg"].fillna(0.0).values.astype(np.float32)
            bal_dest = df["oldbalanceDest"].fillna(0.0).values.astype(np.float32)
        else:
            bal_orig = np.zeros(len(df), dtype=np.float32)
            bal_dest = np.zeros(len(df), dtype=np.float32)

        new_bal_orig = (
            np.maximum(bal_orig - amounts, 0.0).astype(np.float32)
            if "newbalanceOrig" not in df.columns
            else df["newbalanceOrig"].fillna(0.0).values.astype(np.float32)
        )
        new_bal_dest = (
            (bal_dest + amounts).astype(np.float32)
            if "newbalanceDest" not in df.columns
            else df["newbalanceDest"].fillna(0.0).values.astype(np.float32)
        )

        X = np.column_stack([steps, amounts, bal_orig, new_bal_orig, bal_dest, new_bal_dest])
        feature_names = AMLSIM_FEATURE_COLS

        senders = df[sender_col].values
        receivers = df[receiver_col].values
        edges = list(zip(senders, receivers))
        edge_index = np.stack([senders, receivers], axis=0)
    else:
        available_cols = [c for c in AMLSIM_FEATURE_COLS if c in df.columns]
        if not available_cols:
            available_cols = [c for c in df.columns if c not in ("isFraud", "is_fraud", "IS_FRAUD", "label") and pd.api.types.is_numeric_dtype(df[c])]
        X = df[available_cols].fillna(0).values.astype(np.float32)
        feature_names = available_cols
        edges = []
        edge_index = np.zeros((2, 0), dtype=int)
        amounts = X[:, 1] if X.shape[1] > 1 else np.zeros(len(X), dtype=np.float32)
        steps = X[:, 0] if X.shape[1] > 0 else np.zeros(len(X), dtype=np.float32)

    fraud_ratio = float(np.mean(y == 1)) if len(y) > 0 else 0.0
    tx_ids = df[tx_id_col].values if tx_id_col else np.arange(len(y))
    timesteps = df[step_col].values.astype(np.int64) if step_col else np.zeros(len(y), dtype=np.int64)

    def to_pyg_data() -> Any:
        return _make_amlsim_pyg_data(X=X, y=y, edge_index=edge_index, timesteps=timesteps, alert_types=alert_types)

    def to_networkx(max_edges: int | None = None) -> Any:
        return _make_amlsim_networkx_graph(
            edges=edges,
            y=y,
            tx_ids=tx_ids,
            amounts=amounts,
            timesteps=timesteps,
            alert_types=alert_types,
            max_edges=max_edges,
        )

    res: dict[str, Any] = {
        "X": X,
        "y": y,
        "feature_names": feature_names,
        "edges": edges,
        "edge_index": edge_index,
        "tx_ids": tx_ids,
        "timesteps": timesteps,
        "alert_types": alert_types,
        "alerts": alerts_df,
        "accounts": accounts_df,
        "source": source,
        "fraud_ratio": fraud_ratio,
        "to_pyg_data": to_pyg_data,
        "to_networkx": to_networkx,
    }

    if temporal_split:
        clean_kwargs = {k: v for k, v in kwargs.items() if k not in ("nrows", "n_mock_txns", "all_rows", "require_real", "temporal_split")}
        return temporal_split_dataset(res, time_col="step", **clean_kwargs)

    return res


def load_amlsim(
    path: Path | None = None,
    nrows: int | None = None,
    n_mock_txns: int = 5_000,
    rng: np.random.Generator | None = None,
    require_real: bool = False,
    all_rows: bool = False,
    temporal_split: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Load the IBM AMLSim transaction dataset.

    Returns
    -------
    dict with keys:
        ``X``           : np.ndarray (N, 6) — transaction feature matrix
        ``y``           : np.ndarray (N,)   — binary label (1=SAR / fraud, 0=legit)
        ``feature_names``: list[str]         — column names of X
        ``edges``       : list[tuple[int, int]] — graph edge pairs
        ``edge_index``  : np.ndarray (2, E) — PyTorch Geometric compatible edge index
        ``source``      : str               — 'real_parquet', 'real_csv', or 'mock'
        ``fraud_ratio`` : float             — fraud class prevalence
        ``to_pyg_data`` : Callable          — export to PyTorch Geometric Data
        ``to_networkx`` : Callable          — export to NetworkX DiGraph
    """
    rng = rng or np.random.default_rng(42)
    if all_rows:
        target_nrows = None
    elif nrows is not None:
        target_nrows = None if nrows <= 0 else nrows
    elif "nrows" in kwargs:
        kw_nrows = kwargs.get("nrows")
        target_nrows = None if (kw_nrows is None or kw_nrows <= 0) else int(kw_nrows)
    else:
        target_nrows = int(kwargs.get("n_mock_txns") or n_mock_txns)
    n_mock_txns = target_nrows or n_mock_txns
    root = resolve_dataset_dir("amlsim", path)

    # 1. Parquet cache check
    parquet_cache = root / "transactions.parquet"
    if parquet_cache.exists():
        logger.info("[AMLSim] Loading from Parquet cache %s (nrows=%s)", parquet_cache, target_nrows)
        df = pd.read_parquet(parquet_cache)
        if target_nrows is not None and len(df) > target_nrows:
            df = df.iloc[:target_nrows]
        return _process_amlsim_dataframe(df, root=root, source="real_parquet", temporal_split=temporal_split, **kwargs)

    # 2. CSV candidates
    csv_candidates = [root / "transactions.csv"] + list(root.glob("*transaction*.csv")) + list(root.glob("*.csv"))
    for csv_path in csv_candidates:
        if csv_path.exists() and not csv_path.name.endswith(".parquet"):
            logger.info("[AMLSim] Loading real dataset from %s (nrows=%s)", csv_path, target_nrows)
            df = pd.read_csv(csv_path, nrows=target_nrows)
            if target_nrows is None and not parquet_cache.exists():
                try:
                    df.to_parquet(parquet_cache, index=False)
                    logger.info("[AMLSim] Cached %d transactions to %s", len(df), parquet_cache)
                except Exception as exc:
                    logger.debug("[AMLSim] Skipping parquet caching: %s", exc)
            return _process_amlsim_dataframe(df, root=root, source="real_csv", temporal_split=temporal_split, **kwargs)

    if require_real:
        raise FileNotFoundError(
            f"Real AMLSim dataset export not found in '{root}'. "
            f"AMLSim is an IBM synthetic transaction generator; provide transactions.csv or generate records. "
            f"Synthetic fallback is disabled under strict real-data mode."
        )

    # ---- Mock generation ----
    logger.info(
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

    senders = rng.integers(0, 1000, size=n_mock_txns)
    receivers = rng.integers(0, 1000, size=n_mock_txns)
    edges = list(zip(senders, receivers))
    edge_index = np.stack([senders, receivers], axis=0)

    def to_pyg_data() -> Any:
        return _make_amlsim_pyg_data(X=X, y=y, edge_index=edge_index, timesteps=steps.astype(int))

    def to_networkx(max_edges: int | None = None) -> Any:
        return _make_amlsim_networkx_graph(edges=edges, y=y, amounts=amounts, timesteps=steps.astype(int), max_edges=max_edges)

    return {
        "X": X,
        "y": y,
        "feature_names": AMLSIM_FEATURE_COLS,
        "edges": edges,
        "edge_index": edge_index,
        "tx_ids": np.arange(n_mock_txns),
        "timesteps": steps.astype(int),
        "alert_types": np.array(["none"] * n_mock_txns, dtype=object),
        "alerts": None,
        "accounts": None,
        "source": "mock",
        "fraud_ratio": AMLSIM_FRAUD_RATIO,
        "to_pyg_data": to_pyg_data,
        "to_networkx": to_networkx,
    }



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
    nrows: int | None = None,
    n_mock_txns: int = 10_000,
    rng: np.random.Generator | None = None,
    require_real: bool = False,
    all_rows: bool = False,
    temporal_split: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Load PaySim (Kenya M-Pesa Mobile Money Fraud) dataset."""
    rng = rng or np.random.default_rng(42)
    if all_rows:
        target_nrows = None
    elif nrows is not None:
        target_nrows = None if nrows <= 0 else nrows
    elif "nrows" in kwargs:
        kw_nrows = kwargs.get("nrows")
        target_nrows = None if (kw_nrows is None or kw_nrows <= 0) else int(kw_nrows)
    else:
        target_nrows = int(kwargs.get("n_mock_txns") or n_mock_txns)
    n_mock_txns = target_nrows or n_mock_txns
    root = resolve_dataset_dir("paysim", path)

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
        if target_nrows:
            full_df = full_df.iloc[:target_nrows]
        res = _process_paysim_dataframe(full_df, source="real_parquet")
        if temporal_split or kwargs.get("temporal_split"):
            clean_kwargs = {k: v for k, v in kwargs.items() if k not in ("nrows", "n_mock_txns", "all_rows", "require_real", "temporal_split")}
            return temporal_split_dataset(res, **clean_kwargs)
        return res

    for csv_file in possible_csvs:
        if csv_file.exists():
            logger.info("[PaySim] Loading real dataset from %s (nrows=%s)", csv_file, target_nrows)
            df = pd.read_csv(csv_file, nrows=target_nrows)
            res = _process_paysim_dataframe(df, source="real_csv")
            if temporal_split or kwargs.get("temporal_split"):
                clean_kwargs = {k: v for k, v in kwargs.items() if k not in ("nrows", "n_mock_txns", "all_rows", "require_real", "temporal_split")}
                return temporal_split_dataset(res, **clean_kwargs)
            return res

    all_csvs = list(root.glob("*paysim*.csv")) + list(root.glob("*PS*.csv")) + list(root.glob("*.csv"))
    for csv_file in all_csvs:
        if csv_file.exists():
            logger.info("[PaySim] Loading real dataset from %s (nrows=%s)", csv_file, target_nrows)
            df = pd.read_csv(csv_file, nrows=target_nrows)
            res = _process_paysim_dataframe(df, source="real_csv")
            if temporal_split or kwargs.get("temporal_split"):
                clean_kwargs = {k: v for k, v in kwargs.items() if k not in ("nrows", "n_mock_txns", "all_rows", "require_real", "temporal_split")}
                return temporal_split_dataset(res, **clean_kwargs)
            return res

    if require_real:
        raise FileNotFoundError(
            f"Real PaySim dataset files not found in '{root}'. "
            f"Expected 'PS_20174392719_1491204439457_log.csv' or partitioned Parquet files. "
            f"Synthetic fallback is disabled under strict real-data mode."
        )

    # ---- High-Fidelity Synthetic Mock of M-Pesa PaySim ----
    logger.info(
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
    res = {
        "X": X[idx],
        "y": y[idx],
        "feature_names": PAYSIM_FEATURE_COLS,
        "source": "mock_mpesa",
        "fraud_ratio": float(np.mean(y)),
        "steps": X[idx, 0],
    }
    if temporal_split or kwargs.get("temporal_split"):
        clean_kwargs = {k: v for k, v in kwargs.items() if k not in ("nrows", "n_mock_txns", "all_rows", "require_real", "temporal_split")}
        return temporal_split_dataset(res, **clean_kwargs)
    return res


def _process_paysim_dataframe(df: pd.DataFrame, source: str) -> dict[str, Any]:
    """Process a raw PaySim dataframe into numerical feature matrix."""
    df = df.copy()
    if "isFraud" in df.columns:
        y = df["isFraud"].values.astype(int)
    elif "is_fraud" in df.columns:
        y = df["is_fraud"].values.astype(int)
    else:
        y = np.zeros(len(df), dtype=int)

    # Check if this is already an engineered/partitioned feature dataframe without raw PaySim columns
    has_raw_signals = any(c in df.columns for c in ("amount", "step", "oldbalanceOrg", "type"))
    if not has_raw_signals:
        drop_set = {"isFraud", "is_fraud", "isFlaggedFraud", "nameOrig", "nameDest", "type"}
        available_cols = [c for c in df.columns if c not in drop_set and pd.api.types.is_numeric_dtype(df[c])]
        X = df[available_cols].fillna(0).values.astype(np.float32)
        return {
            "X": X,
            "y": y,
            "feature_names": available_cols,
            "source": source,
            "fraud_ratio": float(np.mean(np.asarray(y, dtype=float))) if len(y) > 0 else 0.0,
        }

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
        amt = df["amount"] if "amount" in df.columns else 0.0
        df["errorBalanceOrig"] = df["newbalanceOrig"] + amt - df["oldbalanceOrg"]
    if (
        "errorBalanceDest" not in df.columns
        and "oldbalanceDest" in df.columns
        and "newbalanceDest" in df.columns
    ):
        amt = df["amount"] if "amount" in df.columns else 0.0
        df["errorBalanceDest"] = df["oldbalanceDest"] + amt - df["newbalanceDest"]

    available_cols = [c for c in PAYSIM_FEATURE_COLS if c in df.columns]
    if not available_cols:
        drop_set = {"isFraud", "is_fraud", "isFlaggedFraud", "nameOrig", "nameDest", "type"}
        available_cols = [c for c in df.columns if c not in drop_set and pd.api.types.is_numeric_dtype(df[c])]
    X = df[available_cols].fillna(0).values.astype(np.float32)

    return {
        "X": X,
        "y": y,
        "feature_names": available_cols,
        "source": source,
        "fraud_ratio": float(np.mean(np.asarray(y, dtype=float))) if len(y) > 0 else 0.0,
        "steps": X[:, 0] if (len(available_cols) > 0 and available_cols[0] == "step") else None,
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
    nrows: int | None = None,
    n_mock_txns: int = 8_000,
    rng: np.random.Generator | None = None,
    require_real: bool = False,
    all_rows: bool = False,
    join_identity: bool = True,
    temporal_split: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Load IEEE-CIS Fraud Detection (Vesta Corporation) benchmark dataset.

    Performs:
    1. Transaction and identity left join on 'TransactionID'.
    2. Missing value imputation and categorical encoding (ProductCD, card4, card6, DeviceType, M1-M9).
    3. Temporal feature engineering on 'TransactionDT' (day, hour, zero future leakage).
    """
    rng = rng or np.random.default_rng(42)
    if all_rows:
        target_nrows = None
    elif nrows is not None:
        target_nrows = None if nrows <= 0 else nrows
    elif "nrows" in kwargs:
        kw_nrows = kwargs.get("nrows")
        target_nrows = None if (kw_nrows is None or kw_nrows <= 0) else int(kw_nrows)
    else:
        target_nrows = int(kwargs.get("n_mock_txns") or n_mock_txns)
    n_mock_txns = target_nrows or n_mock_txns
    root = resolve_dataset_dir("ieee_cis", path)

    parquet_files = sorted(list(root.glob("*.parquet")))
    if parquet_files:
        chosen_parquet = parquet_files[0]
        logger.info("[IEEE-CIS] Loading preprocessed Parquet from %s", chosen_parquet)
        df = pd.read_parquet(chosen_parquet)
        if target_nrows:
            df = df.iloc[:target_nrows]
        res = _process_ieee_cis_dataframe(df, source="real_parquet")
        if temporal_split or kwargs.get("temporal_split"):
            clean_kwargs = {
                k: v
                for k, v in kwargs.items()
                if k not in ("nrows", "n_mock_txns", "all_rows", "require_real", "temporal_split", "join_identity")
            }
            return temporal_split_dataset(res, time_col="TransactionDT", **clean_kwargs)
        return res

    txn_csv_candidates = [root / "train_transaction.csv"] + list(root.glob("*transaction*.csv")) + list(root.glob("*.csv"))
    # Exclude identity csv from transaction candidates
    txn_csv_candidates = [c for c in txn_csv_candidates if "identity" not in c.name.lower()]

    for txn_csv in txn_csv_candidates:
        if txn_csv.exists():
            logger.info("[IEEE-CIS] Loading real transaction CSV from %s (nrows=%s)", txn_csv, target_nrows)
            txn_df = pd.read_csv(txn_csv, nrows=target_nrows)

            # Check if identity join is requested and available
            id_csv_candidates = [root / "train_identity.csv"] + list(root.glob("*identity*.csv"))
            id_csv = next((c for c in id_csv_candidates if c.exists()), None)

            if join_identity and id_csv is not None and "TransactionID" in txn_df.columns:
                logger.info("[IEEE-CIS] Joining identity data from %s", id_csv)
                id_df = pd.read_csv(id_csv)
                target_ids = set(txn_df["TransactionID"].unique())
                id_df_sub = id_df[id_df["TransactionID"].isin(target_ids)]
                merged_df = pd.merge(txn_df, id_df_sub, on="TransactionID", how="left")
            else:
                merged_df = txn_df

            res = _process_ieee_cis_dataframe(merged_df, source="real_csv")
            if temporal_split or kwargs.get("temporal_split"):
                clean_kwargs = {
                    k: v
                    for k, v in kwargs.items()
                    if k not in ("nrows", "n_mock_txns", "all_rows", "require_real", "temporal_split", "join_identity")
                }
                return temporal_split_dataset(res, time_col="TransactionDT", **clean_kwargs)
            return res

    if require_real:
        raise FileNotFoundError(
            f"Real IEEE-CIS Fraud Detection dataset files not found in '{root}'. "
            f"Expected 'train_transaction.csv'. "
            f"Synthetic fallback is disabled under strict real-data mode."
        )

    # ---- High-Fidelity Synthetic Mock of IEEE-CIS / Vesta ----
    logger.info(
        "[IEEE-CIS] Dataset not found at %s — generating high-fidelity mock (%d txns, %d features)",
        root,
        n_mock_txns,
        IEEE_CIS_FEATURE_DIM,
    )
    n_fraud = max(2 if n_mock_txns >= 20 else 1, int(n_mock_txns * IEEE_CIS_REAL_FRAUD_RATIO))
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

    # Permute X and y so fraud is distributed uniformly across the temporal timeline
    idx = rng.permutation(n_mock_txns)
    X = X[idx]
    y = y[idx]

    # Guarantee both temporal partitions (train ~80% and test ~20%) contain fraud samples
    if n_mock_txns >= 20 and n_fraud >= 2:
        split_point = int(n_mock_txns * 0.8)
        train_frauds = np.where(y[:split_point] == 1)[0]
        test_frauds = np.where(y[split_point:] == 1)[0]
        if len(train_frauds) == 0 and len(test_frauds) > 0:
            legit_train = np.where(y[:split_point] == 0)[0][0]
            fraud_test = split_point + test_frauds[0]
            y[legit_train], y[fraud_test] = y[fraud_test], y[legit_train]
            X[[legit_train, fraud_test]] = X[[fraud_test, legit_train]]
        elif len(test_frauds) == 0 and len(train_frauds) > 1:
            fraud_train = train_frauds[-1]
            legit_test = split_point + np.where(y[split_point:] == 0)[0][0]
            y[fraud_train], y[legit_test] = y[legit_test], y[fraud_train]
            X[[fraud_train, legit_test]] = X[[legit_test, fraud_train]]

    # TransactionDT (seconds, strictly monotonic simulation matching chronological order)
    mock_dt = np.sort(rng.integers(86400, 86400 * 180, size=n_mock_txns)).astype(np.float64)

    feature_names = [f"feat_{i}" for i in range(X.shape[1])]
    res = {
        "X": X,
        "y": y,
        "feature_names": feature_names,
        "source": "mock_ieee_cis",
        "fraud_ratio": float(np.mean(np.asarray(y, dtype=float))),
        "transaction_dt": mock_dt,
    }
    if temporal_split or kwargs.get("temporal_split"):
        clean_kwargs = {
            k: v
            for k, v in kwargs.items()
            if k not in ("nrows", "n_mock_txns", "all_rows", "require_real", "temporal_split", "join_identity")
        }
        return temporal_split_dataset(res, time_col="TransactionDT", **clean_kwargs)
    return res


def _process_ieee_cis_dataframe(df: pd.DataFrame, source: str) -> dict[str, Any]:
    """Process a raw or merged IEEE-CIS dataframe into numerical feature matrix."""
    df = df.copy()

    # 1. Label extraction
    if "isFraud" in df.columns:
        y = df["isFraud"].values.astype(int)
    elif "is_fraud" in df.columns:
        y = df["is_fraud"].values.astype(int)
    elif "label" in df.columns:
        y = df["label"].values.astype(int)
    else:
        y = np.zeros(len(df), dtype=int)

    # 2. Identity indicators
    if "id_01" in df.columns:
        df["has_identity"] = df["id_01"].notnull().astype(np.float32)
    elif "has_identity" not in df.columns:
        df["has_identity"] = 0.0

    # 3. Temporal features along TransactionDT
    transaction_dt: np.ndarray | None = None
    if "TransactionDT" in df.columns:
        dt_vals = np.asarray(df["TransactionDT"].values, dtype=np.float64)
        df["dt_day"] = ((dt_vals // 86400) % 7).astype(np.float32)
        df["dt_hour"] = ((dt_vals // 3600) % 24).astype(np.float32)
        transaction_dt = dt_vals

    # 4. Amount log transformation
    if "TransactionAmt" in df.columns:
        df["log_TransactionAmt"] = np.log1p(np.maximum(0, df["TransactionAmt"].fillna(0))).astype(np.float32)

    # 5. Low-cardinality categoricals
    if "ProductCD" in df.columns:
        for cat in ["W", "H", "C", "S", "R"]:
            df[f"ProductCD_{cat}"] = (df["ProductCD"] == cat).astype(np.float32)
    if "card4" in df.columns:
        for cat in ["visa", "mastercard", "discover", "american express"]:
            col_clean = cat.replace(" ", "_")
            df[f"card4_{col_clean}"] = (df["card4"] == cat).astype(np.float32)
    if "card6" in df.columns:
        for cat in ["debit", "credit"]:
            df[f"card6_{cat}"] = (df["card6"] == cat).astype(np.float32)
    if "DeviceType" in df.columns:
        for cat in ["desktop", "mobile"]:
            df[f"DeviceType_{cat}"] = (df["DeviceType"] == cat).astype(np.float32)

    # 6. Binary/ternary match flags & identity indicators
    for m in [f"M{i}" for i in range(1, 10)]:
        if m in df.columns:
            df[f"{m}_flag"] = df[m].map({"T": 1.0, "F": 0.0}).fillna(-1.0).astype(np.float32)
    for id_f in ["id_12", "id_28", "id_29"]:
        if id_f in df.columns:
            df[f"{id_f}_flag"] = df[id_f].map({"Found": 1.0, "NotFound": 0.0}).fillna(-1.0).astype(np.float32)
    for id_tf in ["id_35", "id_36", "id_37", "id_38"]:
        if id_tf in df.columns:
            df[f"{id_tf}_flag"] = df[id_tf].map({"T": 1.0, "F": 0.0}).fillna(-1.0).astype(np.float32)

    # 7. Exclude raw non-numeric & identifier columns
    drop_cols = {
        "TransactionID",
        "isFraud",
        "is_fraud",
        "label",
        "ProductCD",
        "card4",
        "card6",
        "P_emaildomain",
        "R_emaildomain",
        "DeviceType",
        "DeviceInfo",
    }
    drop_cols.update([f"M{i}" for i in range(1, 10)])
    drop_cols.update([f"id_{i:02d}" for i in range(12, 39)])
    drop_cols.update([f"id_{i}" for i in range(12, 39)])

    num_cols = [c for c in df.columns if c not in drop_cols and pd.api.types.is_numeric_dtype(df[c])]
    X = df[num_cols].fillna(0).values.astype(np.float32)

    return {
        "X": X,
        "y": y,
        "feature_names": num_cols,
        "source": source,
        "fraud_ratio": float(np.mean(np.asarray(y, dtype=float))) if len(y) > 0 else 0.0,
        "transaction_dt": transaction_dt,
    }


# ===========================================================================
# Kaggle Credit Card Fraud (European Cardholders PCA Benchmark)
# ===========================================================================


def load_creditcard_fraud(
    path: Path | None = None,
    n_mock_txns: int = 5_000,
    rng: np.random.Generator | None = None,
    require_real: bool = False,
    include_time: bool = False,
    scale_time_amount: bool = True,
    scaling_strategy: str = "robust",
    split_data: bool = False,
    train_ratio: float = 0.60,
    val_ratio: float = 0.20,
    test_ratio: float = 0.20,
    stratified: bool = True,
    temporal_split: bool = False,
    seed: int = 42,
    **kwargs: Any,
) -> dict[str, Any]:
    """Load European Credit Card Fraud Detection benchmark (V1-V28 PCA, Time, Amount).

    Supports:
    - Zero-leakage train/validation/test 3-way splitting (stratified or temporal)
    - RobustScaler / StandardScaler on Time and Amount fit strictly on the training partition
    - Imbalance ratio quantification (0.172% fraud prevalence)
    - Backward-compatible 29-feature default or 30-feature (include_time=True) extraction
    """
    rng = rng or np.random.default_rng(seed)
    target_txns = kwargs.get("n_mock_txns") or kwargs.get("nrows") or n_mock_txns
    n_mock_txns = int(target_txns)
    root = resolve_dataset_dir("creditcard", path)

    target_nrows = None if kwargs.get("all_rows", False) else (kwargs.get("nrows") or None)

    chosen_source = "mock_pca"
    df: pd.DataFrame | None = None

    parquet_files = sorted(list(root.glob("*.parquet")))
    if parquet_files:
        chosen_parquet = parquet_files[0]
        logger.info("[CreditCard] Loading preprocessed Parquet from %s", chosen_parquet)
        df = pd.read_parquet(chosen_parquet)
        if target_nrows is not None:
            df = df.iloc[:target_nrows]
        chosen_source = "real_parquet"
    else:
        csv_candidates = [root / "creditcard.csv"] + list(root.glob("*credit*.csv")) + list(root.glob("*.csv"))
        for csv_path in csv_candidates:
            if csv_path.exists():
                logger.info("[CreditCard] Loading real dataset from %s", csv_path)
                df = pd.read_csv(csv_path, nrows=target_nrows)
                chosen_source = "real_csv"
                break

    if df is not None:
        if include_time and "Time" in df.columns:
            pca_cols = [c for c in df.columns if c.startswith("V")]
            amount_col = ["Amount"] if "Amount" in df.columns else []
            feature_cols = ["Time"] + pca_cols + amount_col
        else:
            feature_cols = [c for c in df.columns if c not in ("Time", "Class", "is_fraud", "isFraud") and pd.api.types.is_numeric_dtype(df[c])]

        X = np.asarray(df[feature_cols].fillna(0).values, dtype=np.float32)
        raw_y = df["Class"].values if "Class" in df.columns else df["is_fraud"].values
        y = np.asarray(raw_y, dtype=int)
    else:
        if require_real:
            raise FileNotFoundError(
                f"Real Credit Card Fraud dataset files not found in '{root}'. "
                f"Expected 'creditcard.csv'. "
                f"Synthetic fallback is disabled under strict real-data mode."
            )
        logger.warning("[CreditCard] Generating PCA mock dataset (%d txns)", n_mock_txns)
        # Ensure at least 6 frauds in small mock datasets so train, val, and test splits
        # each contain positive samples under extreme imbalance scenarios.
        n_fraud = max(6, int(n_mock_txns * 0.01)) if n_mock_txns < 5_000 else max(1, int(n_mock_txns * 0.00172))
        n_legit = n_mock_txns - n_fraud
        y_raw = np.array([0] * n_legit + [1] * n_fraud, dtype=int)
        idx_perm = rng.permutation(n_mock_txns)
        y = y_raw[idx_perm]

        pca_features = rng.standard_normal((n_mock_txns, 28)).astype(np.float32)
        amount_vals = np.abs(rng.exponential(scale=88.0, size=n_mock_txns)).astype(np.float32)
        time_vals = rng.uniform(0.0, 172800.0, size=n_mock_txns).astype(np.float32)

        if include_time:
            X = np.column_stack([time_vals, pca_features, amount_vals]).astype(np.float32)
            feature_cols = ["Time"] + [f"V{i}" for i in range(1, 29)] + ["Amount"]
        else:
            X = np.column_stack([pca_features, amount_vals]).astype(np.float32)
            feature_cols = [f"V{i}" for i in range(1, 29)] + ["Amount"]

    if split_data:
        n_samples = len(y)
        if temporal_split and "Time" in feature_cols:
            time_idx = feature_cols.index("Time")
            order = np.argsort(X[:, time_idx])
            n_train = int(n_samples * train_ratio)
            n_val = int(n_samples * val_ratio)
            train_idx = order[:n_train]
            val_idx = order[n_train:n_train + n_val]
            test_idx = order[n_train + n_val:]
        else:
            rng_split = np.random.default_rng(seed)
            pos_indices = np.where(y == 1)[0]
            neg_indices = np.where(y == 0)[0]
            rng_split.shuffle(pos_indices)
            rng_split.shuffle(neg_indices)

            n_pos = len(pos_indices)
            if val_ratio <= 0.0:
                # 2-way train/test split: no validation partition
                n_pos_tr = max(1, int(n_pos * train_ratio)) if n_pos > 1 else (1 if n_pos == 1 else 0)
                if n_pos_tr >= n_pos and n_pos > 1:
                    n_pos_tr = n_pos - 1
                pos_tr = pos_indices[:n_pos_tr]
                pos_va = np.array([], dtype=int)
                pos_te = pos_indices[n_pos_tr:]
            else:
                # 3-way train/val/test split
                if n_pos >= 3:
                    n_pos_tr = max(1, int(n_pos * train_ratio))
                    n_pos_va = max(1, int(n_pos * val_ratio))
                    if n_pos_tr + n_pos_va >= n_pos:
                        n_pos_tr = max(1, n_pos - 2)
                        n_pos_va = 1
                    pos_tr = pos_indices[:n_pos_tr]
                    pos_va = pos_indices[n_pos_tr:n_pos_tr + n_pos_va]
                    pos_te = pos_indices[n_pos_tr + n_pos_va:]
                elif n_pos == 2:
                    pos_tr = pos_indices[:1]
                    pos_va = pos_indices[1:2]
                    pos_te = pos_indices[2:]
                elif n_pos == 1:
                    pos_tr = pos_indices[:1]
                    pos_va = pos_indices[1:]
                    pos_te = pos_indices[1:]
                else:
                    pos_tr = np.array([], dtype=int)
                    pos_va = np.array([], dtype=int)
                    pos_te = np.array([], dtype=int)

            n_neg_tr = int(len(neg_indices) * train_ratio)
            n_neg_va = int(len(neg_indices) * val_ratio)
            neg_tr = neg_indices[:n_neg_tr]
            neg_va = neg_indices[n_neg_tr:n_neg_tr + n_neg_va]
            neg_te = neg_indices[n_neg_tr + n_neg_va:]

            train_idx = np.sort(np.concatenate([pos_tr, neg_tr]))
            val_idx = np.sort(np.concatenate([pos_va, neg_va]))
            test_idx = np.sort(np.concatenate([pos_te, neg_te]))

        X_train = X[train_idx].copy()
        y_train = y[train_idx].copy()
        X_val = X[val_idx].copy()
        y_val = y[val_idx].copy()
        X_test = X[test_idx].copy()
        y_test = y[test_idx].copy()

        scaling_params: dict[str, Any] = {}
        if scale_time_amount:
            for col in ("Time", "Amount"):
                if col in feature_cols:
                    c_idx = feature_cols.index(col)
                    train_vals = np.asarray(X_train[:, c_idx], dtype=np.float32)
                    if scaling_strategy == "robust":
                        q25 = float(np.percentile(train_vals, 25))
                        q75 = float(np.percentile(train_vals, 75))
                        med = float(np.median(train_vals))
                        iqr = max(q75 - q25, 1e-7)
                        scaling_params[col] = {"center": med, "scale": iqr, "strategy": "robust"}
                    else:
                        mean_val = float(np.mean(train_vals))
                        std_val = max(float(np.std(train_vals)), 1e-7)
                        scaling_params[col] = {"center": mean_val, "scale": std_val, "strategy": "standard"}

                    c_center = scaling_params[col]["center"]
                    c_scale = scaling_params[col]["scale"]
                    X_train[:, c_idx] = (X_train[:, c_idx] - c_center) / c_scale
                    X_val[:, c_idx] = (X_val[:, c_idx] - c_center) / c_scale
                    X_test[:, c_idx] = (X_test[:, c_idx] - c_center) / c_scale
                    X[:, c_idx] = (X[:, c_idx] - c_center) / c_scale

        return {
            "X": X,
            "y": y,
            "train": {"X": X_train, "y": y_train, "indices": train_idx},
            "val": {"X": X_val, "y": y_val, "indices": val_idx},
            "test": {"X": X_test, "y": y_test, "indices": test_idx},
            "feature_names": feature_cols,
            "source": chosen_source,
            "fraud_ratio": float(np.mean(y)),
            "imbalance_ratio": float(np.sum(y == 0) / max(1, np.sum(y == 1))),
            "scaling_params": scaling_params,
            "split_ratios": {"train": train_ratio, "val": val_ratio, "test": test_ratio},
        }

    scaling_params_unsplit: dict[str, Any] = {}
    if scale_time_amount:
        for col in ("Time", "Amount"):
            if col in feature_cols:
                c_idx = feature_cols.index(col)
                col_vals = np.asarray(X[:, c_idx], dtype=np.float32)
                if scaling_strategy == "robust":
                    q25 = float(np.percentile(col_vals, 25))
                    q75 = float(np.percentile(col_vals, 75))
                    med = float(np.median(col_vals))
                    iqr = max(q75 - q25, 1e-7)
                    scaling_params_unsplit[col] = {"center": med, "scale": iqr, "strategy": "robust"}
                else:
                    mean_val = float(np.mean(col_vals))
                    std_val = max(float(np.std(col_vals)), 1e-7)
                    scaling_params_unsplit[col] = {"center": mean_val, "scale": std_val, "strategy": "standard"}

                c_center = scaling_params_unsplit[col]["center"]
                c_scale = scaling_params_unsplit[col]["scale"]
                X[:, c_idx] = (X[:, c_idx] - c_center) / c_scale

    return {
        "X": X,
        "y": y,
        "feature_names": feature_cols,
        "source": chosen_source,
        "fraud_ratio": float(np.mean(y)),
        "imbalance_ratio": float(np.sum(y == 0) / max(1, np.sum(y == 1))),
        "scaling_params": scaling_params_unsplit,
    }


load_creditcard = load_creditcard_fraud


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
    if len(X) == 0:
        raise ValueError("Cannot partition empty dataset (X is empty)")
    if len(X) != len(y):
        raise ValueError(f"Length mismatch between features X ({len(X)}) and labels y ({len(y)})")
    if num_banks < 1:
        raise ValueError(f"num_banks must be at least 1, got {num_banks}")
    if alpha <= 0.0:
        raise ValueError(f"Dirichlet concentration parameter alpha must be strictly positive, got {alpha}")

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
# SynthAML (Danish Spar Nord Bank Synthetic AML Benchmark - Nature Sci Data 2023)
# ===========================================================================

# Canonical SynthAML layout:
#   synthetic_alerts.csv       — ALERT_ID, ACCOUNT_ID, DATE, TIMESTAMP, OUTCOME
#   synthetic_transactions.csv — TRANSACTION_ID, ALERT_ID, ACCOUNT_ID, TIMESTAMP,
#                                DATE, ENTRY, TYPE, SIZE, AMOUNT_DKK

SYNTHAML_FEATURE_COLS = [
    "n_transactions",
    "credit_ratio",
    "card_ratio",
    "cash_ratio",
    "international_ratio",
    "wire_ratio",
    "size_mean",
    "size_max",
    "size_std",
    "total_credit_volume",
    "total_debit_volume",
    "net_flow",
    "window_days",
    "tx_frequency_per_day",
]
SYNTHAML_FRAUD_RATIO = 0.085  # ~8.5% in Spar Nord empirical baseline (Nature Sci Data 2023)


def _aggregate_synthaml_alert_features(
    alerts_df: pd.DataFrame,
    tx_df: pd.DataFrame,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Aggregate lookback transaction sequences into alert-level tabular features.

    Conforms to the benchmark feature pipeline described in:
    "A synthetic data set to benchmark anti-money laundering methods" (DOI: 10.1038/s41597-023-02569-2).
    """
    label_col = next((c for c in ["OUTCOME", "outcome", "is_fraud", "IS_FRAUD", "label"] if c in alerts_df.columns), None)
    if label_col is not None:
        y = alerts_df[label_col].astype(bool).astype(int).values
    else:
        y = np.zeros(len(alerts_df), dtype=int)

    alert_id_col = next((c for c in ["ALERT_ID", "alert_id", "id"] if c in alerts_df.columns), "ALERT_ID")
    tx_alert_col = next((c for c in ["ALERT_ID", "alert_id", "id"] if c in tx_df.columns), "ALERT_ID")

    # Group transactions by alert ID
    grouped = tx_df.groupby(tx_alert_col)

    feature_matrix: list[list[float]] = []
    alert_ids = alerts_df[alert_id_col].values

    for aid in alert_ids:
        if aid in grouped.groups:
            group = grouped.get_group(aid)
            n_tx = len(group)

            # Directional entry
            entry_col = next((c for c in ["ENTRY", "entry"] if c in group.columns), None)
            if entry_col:
                n_credits = (group[entry_col].astype(str).str.lower() == "credit").sum()
                credit_ratio = float(n_credits / max(1, n_tx))
            else:
                credit_ratio = 0.5

            # Transaction channels: card, cash, international, wire
            type_col = next((c for c in ["TYPE", "type", "channel"] if c in group.columns), None)
            if type_col:
                types_lower = group[type_col].astype(str).str.lower()
                card_ratio = float((types_lower == "card").sum() / max(1, n_tx))
                cash_ratio = float((types_lower == "cash").sum() / max(1, n_tx))
                intl_ratio = float((types_lower == "international").sum() / max(1, n_tx))
                wire_ratio = float((types_lower == "wire").sum() / max(1, n_tx))
            else:
                card_ratio, cash_ratio, intl_ratio, wire_ratio = 0.5, 0.2, 0.1, 0.2

            # Size metrics (standardized log-DKK)
            size_col = next((c for c in ["SIZE", "size", "TX_AMOUNT", "amount"] if c in group.columns), None)
            if size_col:
                sizes = group[size_col].astype(float).values
                s_mean = float(np.mean(sizes))
                s_max = float(np.max(sizes))
                s_std = float(np.std(sizes)) if len(sizes) > 1 else 0.0

                if entry_col:
                    is_cr = (group[entry_col].astype(str).str.lower() == "credit").values
                    cr_vol = float(np.sum(sizes[is_cr])) if np.any(is_cr) else 0.0
                    db_vol = float(np.sum(sizes[~is_cr])) if np.any(~is_cr) else 0.0
                else:
                    cr_vol = float(np.sum(sizes)) / 2.0
                    db_vol = cr_vol
                net_flow = float(cr_vol - db_vol)
            else:
                s_mean, s_max, s_std, cr_vol, db_vol, net_flow = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

            # Window temporal metrics
            ts_col = next((c for c in ["TIMESTAMP", "timestamp", "date", "DATE"] if c in group.columns), None)
            if ts_col and pd.api.types.is_numeric_dtype(group[ts_col]):
                timestamps = group[ts_col].values
                span_sec = float(np.max(timestamps) - np.min(timestamps))
                window_days = max(1.0 / 24.0, span_sec / 86400.0)
            else:
                window_days = 30.0
            tx_freq = float(n_tx / max(1.0, window_days))

            feature_matrix.append([
                float(n_tx),
                credit_ratio,
                card_ratio,
                cash_ratio,
                intl_ratio,
                wire_ratio,
                s_mean,
                s_max,
                s_std,
                cr_vol,
                db_vol,
                net_flow,
                window_days,
                tx_freq,
            ])
        else:
            # Fallback zero vector if no transactions found for alert
            feature_matrix.append([0.0] * len(SYNTHAML_FEATURE_COLS))

    X = np.array(feature_matrix, dtype=np.float32)
    return X, y, SYNTHAML_FEATURE_COLS


def _generate_mock_synthaml(
    n_mock_alerts: int = 500,
    rng: np.random.Generator | None = None,
) -> dict[str, Any]:
    """Generate high-fidelity synthetic mock of the SynthAML benchmark."""
    if rng is None:
        rng = np.random.default_rng(42)

    logger.warning("[SynthAML] Generating synthetic fallback mock (%d alerts)...", n_mock_alerts)
    import tempfile

    from scripts.generate_synthaml_dataset import generate_synthaml

    with tempfile.TemporaryDirectory() as tmp_dir:
        alerts_df, tx_df = generate_synthaml(tmp_dir, n_alerts=n_mock_alerts, seed=42)

    X, y, feature_names = _aggregate_synthaml_alert_features(alerts_df, tx_df)

    return {
        "X": X,
        "y": y,
        "feature_names": feature_names,
        "alerts_df": alerts_df,
        "transactions_df": tx_df,
        "alert_ids": alerts_df["ALERT_ID"].values,
        "account_ids": alerts_df["ACCOUNT_ID"].values,
        "timestamps": alerts_df["TIMESTAMP"].values,
        "dates": alerts_df["DATE"].values,
        "source": "synthetic_fallback",
        "fraud_ratio": float(np.mean(y == 1)),
        "n_alerts": len(y),
        "n_transactions": len(tx_df),
    }


def load_synthaml(
    require_real: bool = False,
    data_dir: Path | str | None = None,
    all_rows: bool = False,
    nrows: int | None = None,
    n_mock_alerts: int = 500,
    seed: int = 42,
) -> dict[str, Any]:
    """Load the SynthAML Spar Nord Bank synthetic AML alert and transaction dataset.

    Args:
        require_real: If True, raise FileNotFoundError when real dataset files are missing.
        data_dir: Explicit dataset path, or resolved automatically via resolve_dataset_dir.
        all_rows: If True, load all rows without truncation.
        nrows: Maximum number of alerts to ingest (useful for fast testing).
        n_mock_alerts: Number of alerts to generate in synthetic fallback mode.
        seed: Random seed for mock generator.

    Returns:
        dict containing:
        - X: (N, 14) float array of engineered alert features.
        - y: (N,) binary int array of alert outcomes (1 = reported/SAR, 0 = dismissed).
        - feature_names: list of 14 engineered feature names.
        - alerts_df: DataFrame of alerts.
        - transactions_df: DataFrame of lookback transaction sequences.
        - alert_ids: Array of alert IDs.
        - timestamps: Array of alert timestamps.
        - source: 'real_parquet', 'real_csv', or 'synthetic_fallback'.
        - fraud_ratio: Prevalence of reported SAR alerts.
    """
    root = resolve_dataset_dir("synthaml", explicit_path=data_dir)
    alerts_parquet = root / "alerts.parquet"
    tx_parquet = root / "transactions.parquet"

    alerts_csv = root / "synthetic_alerts.csv" if (root / "synthetic_alerts.csv").exists() else root / "alerts.csv"
    tx_csv = root / "synthetic_transactions.csv" if (root / "synthetic_transactions.csv").exists() else root / "transactions.csv"

    # 1. Fast Parquet loader
    if alerts_parquet.exists() and tx_parquet.exists():
        logger.info("[SynthAML] Ingesting columnar Parquet cache from '%s'...", root)
        alerts_df = pd.read_parquet(alerts_parquet)
        tx_df = pd.read_parquet(tx_parquet)
        if not all_rows and nrows is not None and len(alerts_df) > nrows:
            alerts_df = alerts_df.iloc[:nrows].copy()
            valid_aids = set(alerts_df["ALERT_ID"].values)
            tx_df = tx_df[tx_df["ALERT_ID"].isin(valid_aids)].copy()

        X, y, feature_names = _aggregate_synthaml_alert_features(alerts_df, tx_df)
        return {
            "X": X,
            "y": y,
            "feature_names": feature_names,
            "alerts_df": alerts_df,
            "transactions_df": tx_df,
            "alert_ids": alerts_df["ALERT_ID"].values,
            "account_ids": alerts_df["ACCOUNT_ID"].values if "ACCOUNT_ID" in alerts_df.columns else np.zeros(len(y), dtype=int),
            "timestamps": alerts_df["TIMESTAMP"].values if "TIMESTAMP" in alerts_df.columns else np.zeros(len(y), dtype=int),
            "dates": alerts_df["DATE"].values if "DATE" in alerts_df.columns else np.array([""] * len(y)),
            "source": "real_parquet",
            "fraud_ratio": float(np.mean(y == 1)),
            "n_alerts": len(y),
            "n_transactions": len(tx_df),
        }

    # 2. Raw CSV loader
    if alerts_csv.exists() and tx_csv.exists():
        logger.info("[SynthAML] Ingesting CSV files from '%s'...", root)
        alerts_df = pd.read_csv(alerts_csv)
        tx_df = pd.read_csv(tx_csv)
        if not all_rows and nrows is not None and len(alerts_df) > nrows:
            alerts_df = alerts_df.iloc[:nrows].copy()
            valid_aids = set(alerts_df["ALERT_ID"].values)
            tx_df = tx_df[tx_df["ALERT_ID"].isin(valid_aids)].copy()

        # Cache to Parquet for accelerated future reads
        try:
            alerts_df.to_parquet(alerts_parquet, index=False)
            tx_df.to_parquet(tx_parquet, index=False)
            logger.info("[SynthAML] Cached Parquet files to '%s'", root)
        except Exception as exc:
            logger.warning("[SynthAML] Parquet caching skipped: %s", exc)

        X, y, feature_names = _aggregate_synthaml_alert_features(alerts_df, tx_df)
        return {
            "X": X,
            "y": y,
            "feature_names": feature_names,
            "alerts_df": alerts_df,
            "transactions_df": tx_df,
            "alert_ids": alerts_df["ALERT_ID"].values,
            "account_ids": alerts_df["ACCOUNT_ID"].values if "ACCOUNT_ID" in alerts_df.columns else np.zeros(len(y), dtype=int),
            "timestamps": alerts_df["TIMESTAMP"].values if "TIMESTAMP" in alerts_df.columns else np.zeros(len(y), dtype=int),
            "dates": alerts_df["DATE"].values if "DATE" in alerts_df.columns else np.array([""] * len(y)),
            "source": "real_csv",
            "fraud_ratio": float(np.mean(y == 1)),
            "n_alerts": len(y),
            "n_transactions": len(tx_df),
        }

    # 3. Missing file handling
    if require_real:
        raise FileNotFoundError(
            f"Real SynthAML dataset files not found in '{root}'. "
            f"Expected 'synthetic_alerts.csv' and 'synthetic_transactions.csv' (or 'alerts.parquet'/'transactions.parquet'). "
            f"Synthetic fallback is disabled under strict real-data mode."
        )

    rng = np.random.default_rng(seed)
    return _generate_mock_synthaml(n_mock_alerts=nrows or n_mock_alerts, rng=rng)


# ===========================================================================
# Convenience Registry
# ===========================================================================

DATASET_REGISTRY: dict[str, Any] = {
    "elliptic": load_elliptic,
    "amlsim": load_amlsim,
    "paysim": load_paysim,
    "ieee_cis": load_ieee_cis,
    "creditcard": load_creditcard_fraud,
    "credit_card": load_creditcard_fraud,
    "synthaml": load_synthaml,
    "synth_aml": load_synthaml,
}


def temporal_split_dataset(
    dataset_dict: dict[str, Any],
    time_col: str | None = None,
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
    test_ratio: float = 0.15,
    preprocess: bool = False,
    numeric_strategy: str = "standardize",
    impute_strategy: str = "median",
    **kwargs: Any,
) -> dict[str, Any]:
    """Split a dataset chronologically to enforce strict train-val-test temporal isolation."""
    from app.application.services.feature_service import FeatureService
    from app.application.services.preprocessor import DataPreprocessor

    X = dataset_dict["X"]
    y = dataset_dict["y"]
    feature_names = dataset_dict.get("feature_names")

    # If already a pandas DataFrame
    if isinstance(X, pd.DataFrame):
        df = X.copy()
        df["_label_"] = y
        train_df, val_df, test_df, split_meta = FeatureService.temporal_split(
            df,
            time_col=time_col,
            train_ratio=train_ratio,
            val_ratio=val_ratio,
            test_ratio=test_ratio,
            label_col="_label_",
        )
        y_train = train_df.pop("_label_").to_numpy(dtype=int)
        y_val = val_df.pop("_label_").to_numpy(dtype=int)
        y_test = test_df.pop("_label_").to_numpy(dtype=int)

        if preprocess:
            prep = DataPreprocessor(numeric_strategy=numeric_strategy, impute_strategy=impute_strategy)
            X_train = prep.fit_transform(train_df)
            X_val = prep.transform(val_df)
            X_test = prep.transform(test_df)
        else:
            prep = None
            X_train = train_df.to_numpy(dtype=np.float32)
            X_val = val_df.to_numpy(dtype=np.float32)
            X_test = test_df.to_numpy(dtype=np.float32)

        return {
            "X_train": X_train,
            "y_train": y_train,
            "X_val": X_val,
            "y_val": y_val,
            "X_test": X_test,
            "y_test": y_test,
            "split_meta": split_meta,
            "preprocessor": prep,
            "source": dataset_dict.get("source", "unknown"),
        }

    # If X is a numpy array
    X_arr = np.asarray(X, dtype=np.float32)
    y_arr = np.asarray(y, dtype=int)
    n_samples = len(X_arr)

    # Determine time values and sort chronologically
    time_idx = 0
    time_values: np.ndarray | None = None
    if "transaction_dt" in dataset_dict and dataset_dict["transaction_dt"] is not None:
        time_values = np.asarray(dataset_dict["transaction_dt"], dtype=np.float64)
    elif "steps" in dataset_dict and dataset_dict["steps"] is not None:
        time_values = np.asarray(dataset_dict["steps"], dtype=np.float64)

    if time_values is None:
        if feature_names and time_col:
            if time_col in feature_names:
                time_idx = feature_names.index(time_col)
        elif feature_names:
            detected = FeatureService.detect_time_column(pd.DataFrame(columns=feature_names))
            if detected and detected in feature_names:
                time_idx = feature_names.index(detected)
        time_values = X_arr[:, time_idx]

    # Sort chronologically by time column
    sort_idx = np.argsort(time_values)
    X_sorted = X_arr[sort_idx]
    y_sorted = y_arr[sort_idx]
    time_sorted = time_values[sort_idx]

    n_train = int(n_samples * train_ratio)
    n_val = int(n_samples * val_ratio)

    X_train_raw = X_sorted[:n_train]
    y_train = y_sorted[:n_train]
    X_val_raw = X_sorted[n_train : n_train + n_val]
    y_val = y_sorted[n_train : n_train + n_val]
    X_test_raw = X_sorted[n_train + n_val :]
    y_test = y_sorted[n_train + n_val :]

    if preprocess:
        prep = DataPreprocessor(numeric_strategy=numeric_strategy, impute_strategy=impute_strategy)
        X_train = prep.fit_transform(X_train_raw)
        X_val = prep.transform(X_val_raw)
        X_test = prep.transform(X_test_raw)
    else:
        prep = None
        X_train = X_train_raw
        X_val = X_val_raw
        X_test = X_test_raw

    return {
        "X_train": X_train,
        "y_train": y_train,
        "X_val": X_val,
        "y_val": y_val,
        "X_test": X_test,
        "y_test": y_test,
        "time_col_idx": time_idx,
        "preprocessor": prep,
        "source": dataset_dict.get("source", "unknown"),
        "is_strictly_chronological": bool(
            len(X_train) == 0 or len(X_val) == 0 or time_sorted[n_train - 1] <= time_sorted[n_train]
        ),
    }


def load_dataset(
    name: str,
    require_real: bool = False,
    temporal_split: bool = False,
    preprocess: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Load a benchmark dataset by registry name, optionally applying temporal split and preprocessing."""
    clean_name = name.lower().replace("-", "_").strip()
    if clean_name not in DATASET_REGISTRY:
        raise ValueError(f"Unknown dataset '{name}'. Available: {list(DATASET_REGISTRY)}")
    data = DATASET_REGISTRY[clean_name](require_real=require_real, **kwargs)

    if temporal_split:
        clean_kwargs = {k: v for k, v in kwargs.items() if k not in ("nrows", "n_mock_txns", "all_rows", "require_real", "temporal_split")}
        return temporal_split_dataset(data, preprocess=preprocess, **clean_kwargs)

    return data


