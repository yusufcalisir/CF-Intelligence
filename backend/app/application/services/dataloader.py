"""Authoritative Real-World and External Dataset Loaders for Fraud & AML Benchmarks.

Strict Real-Data Runtime Boundary:
Each loader (load_paysim, load_ieee_cis, load_elliptic, load_creditcard_fraud,
load_amlsim, load_synthaml, load_amlnet) loads and validates real physical
dataset files from storage. If physical files are absent or unparseable,
the loader fails closed (raises FileNotFoundError / ValueError).

Silent synthetic fallbacks, mock generation, and fabricated runtime truth
are strictly prohibited in all real loader paths.
Synthetic test fixtures are isolated in `synthetic_dataset_generators.py`.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.domain.enums import DatasetProvenance
from app.infrastructure.storage.storage_utils import get_storage_dir
from app.application.services.synthetic_dataset_generators import (
    generate_synthetic_amlnet,
    generate_synthetic_amlsim,
    generate_synthetic_creditcard,
    generate_synthetic_elliptic,
    generate_synthetic_ieee_cis,
    generate_synthetic_paysim,
    generate_synthetic_synthaml,
)

# Compatibility aliases for non-runtime test code
_generate_mock_elliptic = generate_synthetic_elliptic
_generate_mock_amlsim = generate_synthetic_amlsim
_generate_mock_paysim = generate_synthetic_paysim
_generate_mock_ieee_cis = generate_synthetic_ieee_cis
_generate_mock_synthaml = generate_synthetic_synthaml
_generate_mock_amlnet = generate_synthetic_amlnet

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


def compute_file_sha256(file_path: Path | str) -> str:
    """Compute SHA-256 hex digest of a physical file by streaming 64KB chunks."""
    p = Path(file_path)
    if not p.is_file():
        raise FileNotFoundError(f"File not found for SHA-256 computation: {p}")
    hasher = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


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
        import importlib
        pyg_data_mod = importlib.import_module("torch_geometric.data")
        return pyg_data_mod.Data(**data_dict)
    except (ImportError, AttributeError):
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


def load_elliptic(
    path: Path | None = None,
    nrows: int | None = None,
    all_rows: bool = False,
    include_unknown: bool = False,
    temporal_split: bool = False,
    split_timestep: int = 34,
    max_timesteps: int | None = None,
    construct_graph: bool = True,
    use_cache: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """Load the real Elliptic Bitcoin Transaction Graph Dataset.

    Fails closed with FileNotFoundError if physical dataset files are missing.
    No synthetic generator fallback.

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
        ``source``          : str                 — "real_parquet" | "real_csv"
        ``provenance``      : str                 — "EMPIRICAL_EXTERNAL_DATA"
        ``artifact_origin`` : str                 — "external_physical_file"
        ``scientific_origin``: str                — "empirical"
        ``is_synthetic``    : bool                — False
        ``fraud_ratio``     : float               — ratio of illicit nodes among labeled nodes
        ``to_pyg_data``     : callable            — exports graph to PyTorch Geometric Data object or dict
        ``to_networkx``     : callable            — exports graph to NetworkX DiGraph
    """
    target_nrows = None if all_rows else (nrows or (int(kwargs["nrows"]) if "nrows" in kwargs and kwargs["nrows"] is not None else (kwargs.get("n_samples") or kwargs.get("n_mock_nodes"))))
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
    if not has_real_files:
        raise FileNotFoundError(
            f"Real Elliptic Bitcoin dataset files not found in '{root}'. "
            f"Expected 'elliptic_cache.parquet' or ('elliptic_txs_features.csv' and 'elliptic_txs_classes.csv'). "
            f"Real dataset loaders fail closed; synthetic substitutes are prohibited."
        )

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
        read_nrows = max(target_nrows * 5, 2000) if (target_nrows and not all_rows) else None
        feat_df = pd.read_csv(features_csv, header=None, nrows=read_nrows)
        feat_df.rename(columns={0: "txId"}, inplace=True)
        feat_df["txId"] = feat_df["txId"].astype(str)

        cls_df = pd.read_csv(classes_csv, nrows=read_nrows)
        cls_df["txId"] = cls_df["txId"].astype(str)

        merged_df = pd.merge(cls_df, feat_df, on="txId", how="inner")
        if 1 in merged_df.columns:
            merged_df.rename(columns={1: "timestep"}, inplace=True)
        df = merged_df

    if max_timesteps is not None and "timestep" in df.columns:
        df = df[df["timestep"] <= max_timesteps].copy()

    if len(df) == 0:
        raise ValueError("Elliptic dataset is empty after reading.")

    class_col = "class" if "class" in df.columns else ("label" if "label" in df.columns else None)
    if class_col is not None:
        c_str = df[class_col].astype(str)
        if not include_unknown:
            df = df[c_str.isin(["1", "2"])].copy()
            if len(df) == 0:
                raise ValueError("Elliptic dataset has 0 labeled samples (class 1 or 2) after filtering unknown.")
            c_str = df[class_col].astype(str)
            y: np.ndarray = np.asarray((c_str == "1").to_numpy(dtype=int))
        else:
            c_arr = c_str.to_numpy()
            y = np.where(c_arr == "1", 1, np.where(c_arr == "2", 0, -1)).astype(int)
    else:
        raise ValueError(
            f"Elliptic dataset missing required label/class column ('class' or 'label'). "
            f"Available columns: {list(df.columns)}. Missing labels must not be fabricated."
        )

    if target_nrows is not None and not all_rows and len(df) > target_nrows:
        df = df.iloc[:target_nrows].copy()
        y = y[:target_nrows]

    feature_cols = [c for c in df.columns if c not in ("txId", "class", "label")]
    X: np.ndarray = np.asarray(df[feature_cols].to_numpy(), dtype=np.float32)

    if "timestep" in df.columns:
        timesteps: np.ndarray = np.asarray(df["timestep"].to_numpy(), dtype=int)
    elif X.shape[1] > 0:
        timesteps = np.asarray(X[:, 0], dtype=int)
    else:
        timesteps = np.ones(len(y), dtype=int)

    tx_ids: np.ndarray = np.asarray(df["txId"].astype(str).to_numpy(), dtype=str) if "txId" in df.columns else np.array([str(i) for i in range(len(y))])
    tx_to_idx = {tx_id: idx for idx, tx_id in enumerate(tx_ids)}
    idx_to_tx = {idx: tx_id for idx, tx_id in enumerate(tx_ids)}

    edges: list[tuple[int, int]] = []
    if construct_graph and edges_csv.exists():
        read_edge_rows = None if all_rows else (max(target_nrows * 10, 5000) if target_nrows else None)
        edge_df = pd.read_csv(edges_csv, nrows=read_edge_rows)
        src_col = edge_df.columns[0]
        dst_col = edge_df.columns[1]

        src_mapped = edge_df[src_col].astype(str).map(tx_to_idx)
        dst_mapped = edge_df[dst_col].astype(str).map(tx_to_idx)
        valid = src_mapped.notna() & dst_mapped.notna()

        src_arr = np.asarray(src_mapped[valid].to_numpy(), dtype=np.int64)
        dst_arr = np.asarray(dst_mapped[valid].to_numpy(), dtype=np.int64)
        edges = [(int(u), int(v)) for u, v in zip(src_arr, dst_arr, strict=False)]
        edge_index = np.vstack([src_arr, dst_arr]).astype(np.int64) if len(edges) > 0 else np.zeros((2, 0), dtype=np.int64)
    else:
        edge_index = np.zeros((2, 0), dtype=np.int64)

    adjacency_lists: list[list[int]] = [[] for _ in range(len(y))]
    for u, v in edges:
        if 0 <= u < len(y) and 0 <= v < len(y):
            adjacency_lists[u].append(v)
            adjacency_lists[v].append(u)

    labeled_mask = y != -1
    fraud_ratio = float(np.mean(y[labeled_mask] == 1)) if np.any(labeled_mask) else 0.0

    train_mask: np.ndarray | None = np.asarray(timesteps <= split_timestep) if temporal_split else None
    test_mask: np.ndarray | None = np.asarray(timesteps > split_timestep) if temporal_split else None
    train_labeled_mask: np.ndarray | None = (train_mask & (y != -1)) if (temporal_split and train_mask is not None and include_unknown) else train_mask
    test_labeled_mask: np.ndarray | None = (test_mask & (y != -1)) if (temporal_split and test_mask is not None and include_unknown) else test_mask

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

    source_desc = "real"

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
        "source": source_desc,
        "provenance": DatasetProvenance.EMPIRICAL_EXTERNAL_DATA.value,
        "artifact_origin": "external_physical_file",
        "scientific_origin": "empirical",
        "is_synthetic": False,
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
        import importlib
        pyg_data_mod = importlib.import_module("torch_geometric.data")
        return pyg_data_mod.Data(**data_dict)
    except (ImportError, AttributeError):
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
    if len(df) == 0:
        raise ValueError("AMLSim dataset dataframe is empty.")

    # 1. Label detection
    label_col = next((c for c in ["IS_FRAUD", "isFraud", "is_fraud", "Is Laundering", "is_laundering", "label"] if c in df.columns), None)
    if label_col is None:
        raise ValueError(
            f"AMLSim dataset missing required fraud/laundering label column. "
            f"Available columns: {list(df.columns)}. Missing labels must not be fabricated."
        )
    if df[label_col].isna().any():
        raise ValueError(f"AMLSim dataset contains NaN in label column '{label_col}'. Missing labels cannot be fabricated.")
    try:
        y: np.ndarray = np.asarray(df[label_col].astype(int).to_numpy(), dtype=int)
    except Exception as exc:
        raise ValueError(f"AMLSim dataset contains invalid non-integer values in label column '{label_col}': {exc}") from exc

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
        steps: np.ndarray = np.asarray(df[step_col].fillna(0).to_numpy(), dtype=np.float32)
        amounts: np.ndarray = np.asarray(df[amount_col].fillna(0.0).to_numpy(), dtype=np.float32)
        if acc_map:
            bal_orig: np.ndarray = np.asarray(df[sender_col].map(acc_map).fillna(0.0).to_numpy(), dtype=np.float32)
            bal_dest: np.ndarray = np.asarray(df[receiver_col].map(acc_map).fillna(0.0).to_numpy(), dtype=np.float32)
        elif "oldbalanceOrg" in df.columns and "oldbalanceDest" in df.columns:
            bal_orig = np.asarray(df["oldbalanceOrg"].fillna(0.0).to_numpy(), dtype=np.float32)
            bal_dest = np.asarray(df["oldbalanceDest"].fillna(0.0).to_numpy(), dtype=np.float32)
        else:
            bal_orig = np.zeros(len(df), dtype=np.float32)
            bal_dest = np.zeros(len(df), dtype=np.float32)

        new_bal_orig = (
            np.maximum(bal_orig - amounts, 0.0).astype(np.float32)
            if "newbalanceOrig" not in df.columns
            else np.asarray(df["newbalanceOrig"].fillna(0.0).to_numpy(), dtype=np.float32)
        )
        new_bal_dest = (
            (bal_dest + amounts).astype(np.float32)
            if "newbalanceDest" not in df.columns
            else np.asarray(df["newbalanceDest"].fillna(0.0).to_numpy(), dtype=np.float32)
        )

        X = np.column_stack([steps, amounts, bal_orig, new_bal_orig, bal_dest, new_bal_dest])
        feature_names = AMLSIM_FEATURE_COLS

        senders = np.asarray(df[sender_col].to_numpy(), dtype=np.int64)
        receivers = np.asarray(df[receiver_col].to_numpy(), dtype=np.int64)
        edges: list[tuple[int, int]] = [(int(u), int(v)) for u, v in zip(senders, receivers, strict=False)]
        edge_index = np.stack([senders, receivers], axis=0)
    else:
        available_cols = [c for c in AMLSIM_FEATURE_COLS if c in df.columns]
        if not available_cols:
            available_cols = [c for c in df.columns if c not in ("isFraud", "is_fraud", "IS_FRAUD", "label") and pd.api.types.is_numeric_dtype(df[c])]
        X = np.asarray(df[available_cols].fillna(0).to_numpy(), dtype=np.float32)
        feature_names = available_cols
        edges = []
        edge_index = np.zeros((2, 0), dtype=np.int64)
        amounts = X[:, 1] if X.shape[1] > 1 else np.zeros(len(X), dtype=np.float32)
        steps = X[:, 0] if X.shape[1] > 0 else np.zeros(len(X), dtype=np.float32)

    fraud_ratio = float(np.mean(y == 1)) if len(y) > 0 else 0.0
    tx_ids: np.ndarray = np.asarray(df[tx_id_col].to_numpy()) if tx_id_col else np.arange(len(y))
    timesteps: np.ndarray = np.asarray(df[step_col].to_numpy(), dtype=np.int64) if step_col else np.zeros(len(y), dtype=np.int64)

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
        "provenance": DatasetProvenance.PUBLIC_SIMULATED_DATASET.value,
        "artifact_origin": "external_physical_file",
        "scientific_origin": "simulated",
        "is_synthetic": False,
        "fraud_ratio": fraud_ratio,
        "to_pyg_data": to_pyg_data,
        "to_networkx": to_networkx,
    }

    if temporal_split:
        clean_kwargs = {k: v for k, v in kwargs.items() if k not in ("nrows", "all_rows", "temporal_split", "dataset_mode")}
        return temporal_split_dataset(res, time_col="step", **clean_kwargs)

    return res


def load_amlsim(
    path: Path | None = None,
    nrows: int | None = None,
    all_rows: bool = False,
    temporal_split: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Load the IBM AMLSim transaction dataset.

    Fails closed with FileNotFoundError if physical dataset files are missing.
    No synthetic generator fallback.

    Returns
    -------
    dict with keys:
        ``X``               : np.ndarray (N, 6) — transaction feature matrix
        ``y``               : np.ndarray (N,)   — binary label (1=SAR / fraud, 0=legit)
        ``feature_names``   : list[str]         — column names of X
        ``edges``           : list[tuple[int, int]] — graph edge pairs
        ``edge_index``      : np.ndarray (2, E) — PyTorch Geometric compatible edge index
        ``source``          : str               — 'real_parquet' or 'real_csv'
        ``provenance``      : str               — 'PUBLIC_SIMULATED_DATASET'
        ``artifact_origin`` : str               — 'external_physical_file'
        ``scientific_origin``: str              — 'simulated'
        ``is_synthetic``    : bool              — False
        ``fraud_ratio``     : float             — fraud class prevalence
        ``to_pyg_data``     : Callable          — export to PyTorch Geometric Data
        ``to_networkx``     : Callable          — export to NetworkX DiGraph
    """
    if all_rows:
        target_nrows = None
    elif nrows is not None:
        target_nrows = None if nrows <= 0 else nrows
    elif "nrows" in kwargs and kwargs["nrows"] is not None:
        kw_nrows = int(kwargs["nrows"])
        target_nrows = None if kw_nrows <= 0 else kw_nrows
    else:
        target_nrows = None

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

    raise FileNotFoundError(
        f"Real AMLSim dataset export not found in '{root}'. "
        f"Expected 'transactions.parquet' or 'transactions.csv'. "
        f"Real dataset loaders fail closed; synthetic substitutes are prohibited."
    )




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


def _process_paysim_dataframe(df: pd.DataFrame, source: str) -> dict[str, Any]:
    """Process a raw PaySim dataframe into numerical feature matrix."""
    if len(df) == 0:
        raise ValueError("Malformed PaySim dataset: dataframe is empty")
    df = df.copy()
    lbl_col = "isFraud" if "isFraud" in df.columns else ("is_fraud" if "is_fraud" in df.columns else None)
    if lbl_col is None:
        raise ValueError("Malformed PaySim dataset: missing required label column 'isFraud' or 'is_fraud'")
    if df[lbl_col].isna().any():
        raise ValueError(f"Malformed PaySim dataset: label column '{lbl_col}' contains NaN values. Labels cannot be fabricated.")
    try:
        y = df[lbl_col].astype(int).values
    except Exception as exc:
        raise ValueError(f"Malformed PaySim dataset: invalid label values in '{lbl_col}': {exc}") from exc

    # Check if this is already an engineered/partitioned feature dataframe without raw PaySim columns
    has_raw_signals = any(c in df.columns for c in ("amount", "step", "oldbalanceOrg", "type"))
    if not has_raw_signals:
        drop_set = {"isFraud", "is_fraud", "isFlaggedFraud", "nameOrig", "nameDest", "type"}
        available_cols = [c for c in df.columns if c not in drop_set and pd.api.types.is_numeric_dtype(df[c])]
        if not available_cols:
            raise ValueError("Malformed PaySim dataset: no valid feature columns found")
        X = df[available_cols].fillna(0).values.astype(np.float32)
        return {
            "X": X,
            "y": y,
            "feature_names": available_cols,
            "source": source,
            "provenance": DatasetProvenance.PUBLIC_SIMULATED_DATASET.value,
            "artifact_origin": "external_physical_file",
            "scientific_origin": "simulated",
            "is_synthetic": False,
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
        "provenance": DatasetProvenance.PUBLIC_SIMULATED_DATASET.value,
        "artifact_origin": "external_physical_file",
        "scientific_origin": "simulated",
        "is_synthetic": False,
        "fraud_ratio": float(np.mean(np.asarray(y, dtype=float))) if len(y) > 0 else 0.0,
        "steps": X[:, 0] if (len(available_cols) > 0 and available_cols[0] == "step") else None,
    }


def load_paysim(
    path: Path | None = None,
    nrows: int | None = None,
    all_rows: bool = False,
    temporal_split: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Load PaySim (Kenya M-Pesa Mobile Money Fraud) dataset.

    Fails closed with FileNotFoundError if physical dataset files are missing.
    No synthetic generator fallback.

    Returns
    -------
    dict with keys:
        ``X``               : np.ndarray (N, 13) — transaction feature matrix
        ``y``               : np.ndarray (N,)    — binary label (1=fraud, 0=legit)
        ``feature_names``   : list[str]          — column names of X
        ``source``          : str                — 'real_parquet' or 'real_csv'
        ``provenance``      : str                — 'PUBLIC_SIMULATED_DATASET'
        ``artifact_origin`` : str                — 'external_physical_file'
        ``scientific_origin``: str               — 'simulated'
        ``is_synthetic``    : bool               — False
        ``fraud_ratio``     : float              — fraud class prevalence
        ``steps``           : np.ndarray | None  — simulation step / timestep
    """
    if all_rows:
        target_nrows = None
    elif nrows is not None:
        target_nrows = None if nrows <= 0 else nrows
    elif "nrows" in kwargs and kwargs["nrows"] is not None:
        kw_nrows = int(kwargs["nrows"])
        target_nrows = None if kw_nrows <= 0 else kw_nrows
    elif "n_mock_txns" in kwargs and kwargs["n_mock_txns"] is not None:
        target_nrows = int(kwargs["n_mock_txns"])
    elif "n_samples" in kwargs and kwargs["n_samples"] is not None:
        target_nrows = int(kwargs["n_samples"])
    else:
        target_nrows = None

    root = resolve_dataset_dir("paysim", path)

    if root.is_file():
        logger.info("[PaySim] Loading real dataset from explicit file %s (nrows=%s)", root, target_nrows)
        df = pd.read_csv(root, nrows=target_nrows)
        res = _process_paysim_dataframe(df, source="real_csv")
        if temporal_split:
            clean_kwargs = {k: v for k, v in kwargs.items() if k not in ("nrows", "all_rows", "temporal_split", "dataset_mode")}
            return temporal_split_dataset(res, **clean_kwargs)
        return res

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
        if temporal_split:
            clean_kwargs = {k: v for k, v in kwargs.items() if k not in ("nrows", "all_rows", "temporal_split", "dataset_mode")}
            return temporal_split_dataset(res, **clean_kwargs)
        return res

    for csv_file in possible_csvs:
        if csv_file.exists():
            logger.info("[PaySim] Loading real dataset from %s (nrows=%s)", csv_file, target_nrows)
            df = pd.read_csv(csv_file, nrows=target_nrows)
            res = _process_paysim_dataframe(df, source="real_csv")
            if temporal_split:
                clean_kwargs = {k: v for k, v in kwargs.items() if k not in ("nrows", "all_rows", "temporal_split", "dataset_mode")}
                return temporal_split_dataset(res, **clean_kwargs)
            return res

    all_csvs = list(root.glob("*paysim*.csv")) + list(root.glob("*PS*.csv")) + list(root.glob("*.csv"))
    for csv_file in all_csvs:
        if csv_file.exists():
            logger.info("[PaySim] Loading real dataset from %s (nrows=%s)", csv_file, target_nrows)
            df = pd.read_csv(csv_file, nrows=target_nrows)
            res = _process_paysim_dataframe(df, source="real_csv")
            if temporal_split:
                clean_kwargs = {k: v for k, v in kwargs.items() if k not in ("nrows", "all_rows", "temporal_split", "dataset_mode")}
                return temporal_split_dataset(res, **clean_kwargs)
            return res

    raise FileNotFoundError(
        f"Real PaySim dataset files not found in '{root}'. "
        f"Expected 'PS_20174392719_1491204439457_log.csv' or partitioned Parquet files. "
        f"Real dataset loaders fail closed; synthetic substitutes are prohibited."
    )


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


def _process_ieee_cis_dataframe(df: pd.DataFrame, source: str) -> dict[str, Any]:
    """Process a raw or merged IEEE-CIS dataframe into numerical feature matrix."""
    if len(df) == 0:
        raise ValueError("IEEE-CIS dataset dataframe is empty.")
    df = df.copy()

    # 1. Label extraction
    lbl_col = next((c for c in ["isFraud", "is_fraud", "label"] if c in df.columns), None)
    if lbl_col is None:
        raise ValueError(
            f"IEEE-CIS dataset missing required fraud label column ('isFraud', 'is_fraud', or 'label'). "
            f"Available columns: {list(df.columns)}. Missing labels must not be fabricated."
        )
    if df[lbl_col].isna().any():
        raise ValueError(f"IEEE-CIS dataset contains NaN in label column '{lbl_col}'. Missing labels cannot be fabricated.")
    try:
        y = df[lbl_col].astype(int).values
    except Exception as exc:
        raise ValueError(f"IEEE-CIS dataset contains invalid non-integer values in label column '{lbl_col}': {exc}") from exc

    # 2. Identity indicators
    if "id_01" in df.columns:
        df["has_identity"] = df["id_01"].notnull().astype(np.float32)
    elif "has_identity" not in df.columns:
        df["has_identity"] = 0.0

    # 3. Temporal features along TransactionDT
    transaction_dt: np.ndarray | None = None
    if "TransactionDT" in df.columns:
        dt_series = pd.to_numeric(df["TransactionDT"], errors="coerce").fillna(0)
        df["dt_day"] = ((dt_series // 86400) % 7).astype(np.float32)
        df["dt_hour"] = ((dt_series // 3600) % 24).astype(np.float32)
        transaction_dt = np.asarray(dt_series.values, dtype=np.float64)

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

    # 7. Exclude raw non-numeric, identifier, and chronological split axis columns
    drop_cols = {
        "TransactionID",
        "TransactionDT",  # Crucial: Raw timestamp is split/order axis, never an ordinary input feature
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
        "provenance": DatasetProvenance.EMPIRICAL_EXTERNAL_DATA.value,
        "artifact_origin": "external_physical_file",
        "scientific_origin": "empirical",
        "is_synthetic": False,
        "fraud_ratio": float(np.mean(np.asarray(y, dtype=float))) if len(y) > 0 else 0.0,
        "transaction_dt": transaction_dt,
    }


def load_ieee_cis(
    path: Path | None = None,
    nrows: int | None = None,
    all_rows: bool = False,
    join_identity: bool = True,
    temporal_split: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Load IEEE-CIS Fraud Detection (Vesta Corporation) benchmark dataset.

    Fails closed with FileNotFoundError if physical dataset files are missing.
    No synthetic generator fallback.

    Performs:
    1. Transaction and identity left join on 'TransactionID'.
    2. Missing value imputation and categorical encoding (ProductCD, card4, card6, DeviceType, M1-M9).
    3. Temporal feature engineering on 'TransactionDT' (day, hour, zero future leakage).
    """
    if all_rows:
        target_nrows = None
    elif nrows is not None:
        target_nrows = None if nrows <= 0 else nrows
    elif "nrows" in kwargs and kwargs["nrows"] is not None:
        kw_nrows = int(kwargs["nrows"])
        target_nrows = None if kw_nrows <= 0 else kw_nrows
    else:
        target_nrows = None

    root = resolve_dataset_dir("ieee_cis", path)

    parquet_files = sorted(list(root.glob("*.parquet")))
    if parquet_files:
        chosen_parquet = parquet_files[0]
        logger.info("[IEEE-CIS] Loading preprocessed Parquet from %s", chosen_parquet)
        df = pd.read_parquet(chosen_parquet)
        if target_nrows:
            df = df.iloc[:target_nrows]
        res = _process_ieee_cis_dataframe(df, source="real_parquet")
        if temporal_split:
            clean_kwargs = {
                k: v
                for k, v in kwargs.items()
                if k not in ("nrows", "all_rows", "temporal_split", "join_identity", "dataset_mode")
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
            if temporal_split:
                clean_kwargs = {
                    k: v
                    for k, v in kwargs.items()
                    if k not in ("nrows", "all_rows", "temporal_split", "join_identity", "dataset_mode")
                }
                return temporal_split_dataset(res, time_col="TransactionDT", **clean_kwargs)
            return res

    raise FileNotFoundError(
        f"Real IEEE-CIS Fraud Detection dataset files not found in '{root}'. "
        f"Expected 'train_transaction.csv' or partitioned Parquet files. "
        f"Real dataset loaders fail closed; synthetic substitutes are prohibited."
    )


# ===========================================================================
# Kaggle Credit Card Fraud (European Cardholders PCA Benchmark)
# ===========================================================================


def load_creditcard_fraud(
    path: Path | None = None,
    include_time: bool = False,
    scale_time_amount: bool = True,
    scaling_strategy: str = "robust",
    split_data: bool = False,
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
    test_ratio: float = 0.15,
    stratified: bool = True,
    temporal_split: bool = False,
    seed: int = 42,
    nrows: int | None = None,
    all_rows: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Load European Credit Card Fraud Detection benchmark (V1-V28 PCA, Time, Amount).

    Fails closed with FileNotFoundError if physical dataset files are missing.
    No synthetic generator fallback.

    Supports:
    - Zero-leakage train/validation/test 3-way splitting (stratified or temporal)
    - RobustScaler / StandardScaler on Time and Amount fit strictly on the training partition
    - Imbalance ratio quantification (0.172% fraud prevalence)
    - Backward-compatible 29-feature default or 30-feature (include_time=True) extraction
    """
    root = resolve_dataset_dir("creditcard", path)
    target_nrows = None if all_rows else (nrows or kwargs.get("nrows") or kwargs.get("n_mock_txns") or kwargs.get("n_samples") or None)

    chosen_source = "real_csv"
    chosen_file_path: Path | None = None
    df: pd.DataFrame | None = None

    if root.is_file():
        chosen_file_path = root
        if root.suffix.lower() == ".parquet":
            df = pd.read_parquet(root)
            chosen_source = "real_parquet"
        else:
            df = pd.read_csv(root, nrows=target_nrows)
            chosen_source = "real_csv"
        if target_nrows is not None and df is not None:
            df = df.iloc[:target_nrows]
    else:
        parquet_files = sorted(list(root.glob("*.parquet")))
        if parquet_files:
            chosen_parquet = parquet_files[0]
            chosen_file_path = chosen_parquet
            logger.info("[CreditCard] Loading preprocessed Parquet from %s", chosen_parquet)
            df = pd.read_parquet(chosen_parquet)
            if target_nrows is not None:
                df = df.iloc[:target_nrows]
            chosen_source = "real_parquet"
        else:
            csv_candidates = [root / "creditcard.csv"] + list(root.glob("*credit*.csv")) + list(root.glob("*.csv"))
            for csv_path in csv_candidates:
                if csv_path.exists() and not csv_path.name.endswith(".parquet"):
                    chosen_file_path = csv_path
                    logger.info("[CreditCard] Loading real dataset from %s", csv_path)
                    df = pd.read_csv(csv_path, nrows=target_nrows)
                    chosen_source = "real_csv"
                    break

    if df is None:
        raise FileNotFoundError(
            f"Real Credit Card Fraud dataset files not found in '{root}'. "
            f"Expected 'creditcard.csv' or Parquet cache. "
            f"Real dataset loaders fail closed; synthetic substitutes are prohibited."
        )

    dataset_sha256: str | None = None
    if chosen_file_path is not None and chosen_file_path.is_file():
        dataset_sha256 = compute_file_sha256(chosen_file_path)

    return _process_creditcard_dataframe(
        df=df,
        include_time=include_time,
        scale_time_amount=scale_time_amount,
        scaling_strategy=scaling_strategy,
        split_data=split_data,
        train_ratio=train_ratio,
        val_ratio=val_ratio,
        test_ratio=test_ratio,
        stratified=stratified,
        temporal_split=temporal_split,
        seed=seed,
        source=chosen_source,
        provenance=DatasetProvenance.EMPIRICAL_EXTERNAL_DATA.value,
        artifact_origin="external_physical_file",
        scientific_origin="empirical",
        is_synthetic=False,
        chosen_file_path=chosen_file_path,
        dataset_sha256=dataset_sha256,
    )


def _process_creditcard_dataframe(
    df: pd.DataFrame,
    include_time: bool = True,
    scale_time_amount: bool = True,
    scaling_strategy: str = "robust",
    split_data: bool = False,
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
    test_ratio: float = 0.15,
    stratified: bool = True,
    temporal_split: bool = False,
    seed: int = 42,
    source: str = "real_csv",
    provenance: str = DatasetProvenance.EMPIRICAL_EXTERNAL_DATA.value,
    artifact_origin: str = "external_physical_file",
    scientific_origin: str = "empirical",
    is_synthetic: bool = False,
    chosen_file_path: Path | None = None,
    dataset_sha256: str | None = None,
) -> dict[str, Any]:
    if include_time and "Time" in df.columns:
        pca_cols = [c for c in df.columns if c.startswith("V")]
        amount_col = ["Amount"] if "Amount" in df.columns else []
        feature_cols = ["Time"] + pca_cols + amount_col
    else:
        feature_cols = [c for c in df.columns if c not in ("Time", "Class", "is_fraud", "isFraud") and pd.api.types.is_numeric_dtype(df[c])]

    X = np.asarray(df[feature_cols].fillna(0).values, dtype=np.float32)
    if len(df) == 0:
        raise ValueError("CreditCard dataset dataframe is empty.")

    label_col = next((c for c in ["Class", "class", "is_fraud", "isFraud", "label"] if c in df.columns), None)
    if label_col is None:
        raise ValueError(
            f"CreditCard dataset missing required label column ('Class'). "
            f"Available columns: {list(df.columns)}. Missing labels must not be fabricated."
        )
    if df[label_col].isna().any():
        raise ValueError(f"CreditCard dataset contains NaN in label column '{label_col}'. Missing labels cannot be fabricated.")
    try:
        raw_y = df[label_col].values
        y = np.asarray(raw_y, dtype=int)
    except Exception as exc:
        raise ValueError(f"CreditCard dataset contains invalid non-integer values in label column '{label_col}': {exc}") from exc

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
            "source": source,
            "provenance": provenance,
            "artifact_origin": artifact_origin,
            "scientific_origin": scientific_origin,
            "is_synthetic": is_synthetic,
            "file_path": str(chosen_file_path) if chosen_file_path else None,
            "sha256_hash": dataset_sha256,
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
        "source": source,
        "provenance": provenance,
        "artifact_origin": artifact_origin,
        "scientific_origin": scientific_origin,
        "is_synthetic": is_synthetic,
        "file_path": str(chosen_file_path) if chosen_file_path else None,
        "sha256_hash": dataset_sha256,
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
    if len(alerts_df) == 0:
        raise ValueError("SynthAML alerts dataframe is empty.")

    label_col = next((c for c in ["OUTCOME", "outcome", "is_fraud", "IS_FRAUD", "label"] if c in alerts_df.columns), None)
    if label_col is None:
        raise ValueError(
            f"SynthAML alerts table missing required label column ('OUTCOME' or 'is_fraud'). "
            f"Available columns: {list(alerts_df.columns)}. Missing labels must not be fabricated."
        )
    if alerts_df[label_col].isna().any():
        raise ValueError(f"SynthAML alerts table contains NaN in label column '{label_col}'. Missing labels cannot be fabricated.")
    try:
        y: np.ndarray = np.asarray(alerts_df[label_col].astype(int).to_numpy(), dtype=int)
    except Exception as exc:
        raise ValueError(f"SynthAML alerts table contains invalid non-integer values in label column '{label_col}': {exc}") from exc

    alert_id_col = next((c for c in ["ALERT_ID", "alert_id", "id"] if c in alerts_df.columns), "ALERT_ID")
    tx_alert_col = next((c for c in ["ALERT_ID", "alert_id", "id"] if c in tx_df.columns), "ALERT_ID")

    # Group transactions by alert ID
    grouped = tx_df.groupby(tx_alert_col)

    feature_matrix: list[list[float]] = []
    alert_ids = alerts_df[alert_id_col].to_numpy()

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
                sizes = np.asarray(group[size_col].astype(float).to_numpy(), dtype=np.float64)
                s_mean = float(np.mean(sizes))
                s_max = float(np.max(sizes))
                s_std = float(np.std(sizes)) if len(sizes) > 1 else 0.0

                if entry_col:
                    is_cr = np.asarray((group[entry_col].astype(str).str.lower() == "credit").to_numpy(), dtype=bool)
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
                timestamps = np.asarray(group[ts_col].to_numpy(), dtype=np.float64)
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

    X: np.ndarray = np.asarray(feature_matrix, dtype=np.float32)
    return X, y, SYNTHAML_FEATURE_COLS


def _generate_mock_synthaml(
    n_mock_alerts: int = 500,
    rng: np.random.Generator | None = None,
) -> dict[str, Any]:
    """Generate high-fidelity synthetic mock of the SynthAML benchmark."""
    if rng is None:
        rng = np.random.default_rng(42)

    logger.warning("[SynthAML] Generating synthetic fallback mock (%d alerts)...", n_mock_alerts)
    import sys
    import tempfile

    repo_root = Path(__file__).resolve().parents[4]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

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
    data_dir: Path | str | None = None,
    all_rows: bool = False,
    nrows: int | None = None,
    path: Path | str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Load the SynthAML Spar Nord Bank synthetic AML alert and transaction dataset.

    Fails closed with FileNotFoundError if physical dataset files are missing.
    No synthetic generator fallback.

    Args:
        data_dir: Explicit dataset path, or resolved automatically via resolve_dataset_dir.
        all_rows: If True, load all rows without truncation.
        nrows: Maximum number of alerts to ingest (useful for fast testing).
        path: Alias for data_dir.

    Returns:
        dict containing:
        - X: (N, 14) float array of engineered alert features.
        - y: (N,) binary int array of alert outcomes (1 = reported/SAR, 0 = dismissed).
        - feature_names: list of 14 engineered feature names.
        - alerts_df: DataFrame of alerts.
        - transactions_df: DataFrame of lookback transaction sequences.
        - alert_ids: Array of alert IDs.
        - timestamps: Array of alert timestamps.
        - source: 'real_parquet' or 'real_csv'.
        - provenance: 'CONTROLLED_PROJECT_SYNTHETIC'.
        - fraud_ratio: Prevalence of reported SAR alerts.
    """
    target_dir = path if path is not None else data_dir
    root = resolve_dataset_dir("synthaml", explicit_path=target_dir)
    target_nrows = None if all_rows else (nrows or (int(kwargs["nrows"]) if "nrows" in kwargs and kwargs["nrows"] is not None else None))

    alerts_parquet = root / "alerts.parquet"
    tx_parquet = root / "transactions.parquet"

    alerts_csv = root / "synthetic_alerts.csv" if (root / "synthetic_alerts.csv").exists() else root / "alerts.csv"
    tx_csv = root / "synthetic_transactions.csv" if (root / "synthetic_transactions.csv").exists() else root / "transactions.csv"

    # 1. Fast Parquet loader
    if alerts_parquet.exists() and tx_parquet.exists():
        logger.info("[SynthAML] Ingesting columnar Parquet cache from '%s'...", root)
        alerts_df = pd.read_parquet(alerts_parquet)
        tx_df = pd.read_parquet(tx_parquet)
        if target_nrows is not None and len(alerts_df) > target_nrows:
            alerts_df = alerts_df.iloc[:target_nrows].copy()
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
            "provenance": DatasetProvenance.CONTROLLED_PROJECT_SYNTHETIC.value,
            "artifact_origin": "external_physical_file",
            "scientific_origin": "synthetic",
            "is_synthetic": False,
            "fraud_ratio": float(np.mean(y == 1)),
            "n_alerts": len(y),
            "n_transactions": len(tx_df),
        }

    # 2. Raw CSV loader
    if alerts_csv.exists() and tx_csv.exists():
        logger.info("[SynthAML] Ingesting CSV files from '%s'...", root)
        alerts_df = pd.read_csv(alerts_csv)
        tx_df = pd.read_csv(tx_csv)
        if target_nrows is not None and len(alerts_df) > target_nrows:
            alerts_df = alerts_df.iloc[:target_nrows].copy()
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
            "provenance": DatasetProvenance.CONTROLLED_PROJECT_SYNTHETIC.value,
            "artifact_origin": "external_physical_file",
            "scientific_origin": "synthetic",
            "is_synthetic": False,
            "fraud_ratio": float(np.mean(y == 1)),
            "n_alerts": len(y),
            "n_transactions": len(tx_df),
        }

    raise FileNotFoundError(
        f"Real SynthAML dataset files not found in '{root}'. "
        f"Expected 'synthetic_alerts.csv' and 'synthetic_transactions.csv' (or 'alerts.parquet'/'transactions.parquet'). "
        f"Real dataset loaders fail closed; synthetic substitutes are prohibited."
    )


# ===========================================================================
# AMLNet (AUSTRAC Knowledge-Guided Multi-Agent Synthetic AML Benchmark)
# ===========================================================================

# Canonical Huda et al. / AUSTRAC layout:
#   step, type, amount, category, nameOrig, nameDest, oldbalanceOrg, newbalanceOrig,
#   hour, day_of_week, day_of_month, month, metadata, isFraud, isMoneyLaundering,
#   laundering_typology, fraud_probability

AMLNET_FEATURE_COLS = [
    "amount",
    "log_amount",
    "oldbalanceOrg",
    "newbalanceOrig",
    "balance_orig_delta",
    "balance_orig_ratio",
    "hour",
    "day_of_week",
    "type_TRANSFER",
    "type_OSKO",
    "type_BPAY",
    "type_EFTPOS",
    "type_DEBIT",
    "type_NPP",
    "is_near_reporting_threshold",
    "category_high_risk",
    "is_night_txn",
    "is_weekend_txn",
]
AMLNET_FRAUD_RATIO = 0.0014  # ~0.14% empirical AUSTRAC rare-event laundering prevalence
AMLNET_TYPOLOGIES = ["normal", "structuring", "layering", "integration"]


def _process_amlnet_dataframe(
    df: pd.DataFrame,
    source: str = "real_parquet",
) -> dict[str, Any]:
    """Process an AMLNet DataFrame into standardized numerical features, labels, and metadata."""
    if len(df) == 0:
        raise ValueError("AMLNet dataset dataframe is empty.")

    # 1. Labels
    label_col = next((c for c in ["isMoneyLaundering", "is_money_laundering", "isLaundering", "isFraud", "is_fraud"] if c in df.columns), None)
    if label_col is None:
        raise ValueError(
            f"AMLNet dataset missing required laundering label column. "
            f"Available columns: {list(df.columns)}. Missing labels must not be fabricated."
        )
    if df[label_col].isna().any():
        raise ValueError(f"AMLNet dataset contains missing or NaN labels in '{label_col}'. Missing labels must not be fabricated.")
    try:
        y = np.asarray(df[label_col].astype(int).values, dtype=int)
    except Exception as exc:
        raise ValueError(f"AMLNet dataset contains invalid non-integer values in label column '{label_col}': {exc}") from exc

    typology_col = next((c for c in ["laundering_typology", "typology", "laundering_phase"] if c in df.columns), None)
    if typology_col:
        typologies = np.asarray(df[typology_col].fillna("normal").astype(str).values, dtype=object)
    else:
        typologies = np.array(["normal" if val == 0 else "suspicious" for val in y], dtype=object)

    # 2. Amounts & Balances
    n = len(df)
    amount = np.asarray(pd.to_numeric(df.get("amount", pd.Series([0.0] * n)), errors="coerce").fillna(0.0).values, dtype=np.float32)
    log_amount = np.log1p(np.maximum(0.0, amount)).astype(np.float32)
    old_bal = np.asarray(pd.to_numeric(df.get("oldbalanceOrg", pd.Series([0.0] * n)), errors="coerce").fillna(0.0).values, dtype=np.float32)
    new_bal = np.asarray(pd.to_numeric(df.get("newbalanceOrig", pd.Series([0.0] * n)), errors="coerce").fillna(0.0).values, dtype=np.float32)

    bal_delta = (new_bal + amount - old_bal).astype(np.float32)
    bal_ratio = (amount / (old_bal + 1.0)).astype(np.float32)

    # 3. Temporal
    hour = np.asarray(pd.to_numeric(df.get("hour", pd.Series([12] * n)), errors="coerce").fillna(12).values, dtype=np.float32)
    dow = np.asarray(pd.to_numeric(df.get("day_of_week", pd.Series([0] * n)), errors="coerce").fillna(0).values, dtype=np.float32)

    # 4. Payment channel one-hot encoding
    type_col = df.get("type", pd.Series(["TRANSFER"] * len(df))).astype(str).str.upper()
    t_transfer = (type_col == "TRANSFER").astype(np.float32).values
    t_osko = (type_col == "OSKO").astype(np.float32).values
    t_bpay = (type_col == "BPAY").astype(np.float32).values
    t_eftpos = (type_col == "EFTPOS").astype(np.float32).values
    t_debit = (type_col == "DEBIT").astype(np.float32).values
    t_npp = (type_col == "NPP").astype(np.float32).values

    # 5. Risk indicators
    # Structuring: amounts close to the AUSTRAC $10,000 threshold
    near_thresh = ((amount >= 8500.0) & (amount < 10000.0)).astype(np.float32)

    # High-risk category
    cat_col = df.get("category", pd.Series(["Retail"] * len(df))).astype(str)
    high_risk_cats = {"Cryptocurrency", "Shell Company", "Luxury Goods", "Gambling", "Investment"}
    high_risk = cat_col.isin(high_risk_cats).astype(np.float32).values

    # Timing flags
    is_night = ((hour < 5.0) | (hour > 22.0)).astype(np.float32)
    is_weekend = (dow >= 5.0).astype(np.float32)

    X = np.column_stack([
        np.asarray(amount, dtype=np.float32),
        np.asarray(log_amount, dtype=np.float32),
        np.asarray(old_bal, dtype=np.float32),
        np.asarray(new_bal, dtype=np.float32),
        np.asarray(bal_delta, dtype=np.float32),
        np.asarray(bal_ratio, dtype=np.float32),
        np.asarray(hour, dtype=np.float32),
        np.asarray(dow, dtype=np.float32),
        np.asarray(t_transfer, dtype=np.float32),
        np.asarray(t_osko, dtype=np.float32),
        np.asarray(t_bpay, dtype=np.float32),
        np.asarray(t_eftpos, dtype=np.float32),
        np.asarray(t_debit, dtype=np.float32),
        np.asarray(t_npp, dtype=np.float32),
        np.asarray(near_thresh, dtype=np.float32),
        np.asarray(high_risk, dtype=np.float32),
        np.asarray(is_night, dtype=np.float32),
        np.asarray(is_weekend, dtype=np.float32),
    ]).astype(np.float32)

    step_default = pd.Series(range(len(df)))
    steps = np.asarray(pd.to_numeric(df.get("step", step_default), errors="coerce").fillna(0).values, dtype=np.float64)

    return {
        "X": X,
        "y": y,
        "feature_names": AMLNET_FEATURE_COLS,
        "raw_df": df,
        "typologies": typologies,
        "steps": steps,
        "source": source,
        "provenance": DatasetProvenance.CONTROLLED_PROJECT_SYNTHETIC.value,
        "artifact_origin": "external_physical_file",
        "scientific_origin": "synthetic",
        "is_synthetic": False,
        "fraud_ratio": float(np.mean(y == 1)) if len(y) > 0 else 0.0,
        "n_samples": len(y),
    }


def load_amlnet(
    data_dir: Path | str | None = None,
    all_rows: bool = False,
    nrows: int | None = None,
    path: Path | str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Load the AUSTRAC-aligned AMLNet multi-agent synthetic AML transaction dataset.

    Fails closed with FileNotFoundError if physical dataset files are missing.
    No synthetic generator fallback.

    Args:
        data_dir: Explicit dataset path, or resolved automatically via resolve_dataset_dir.
        all_rows: If True, load all rows without truncation.
        nrows: Maximum number of transactions to ingest (useful for fast testing).
        path: Alias for data_dir.

    Returns:
        dict containing:
        - X: Feature matrix (N, 18).
        - y: Binary laundering labels (N,).
        - feature_names: Names of engineered features.
        - raw_df: Underlying pandas DataFrame.
        - typologies: Array of AML typologies ('normal', 'structuring', 'layering', 'integration').
        - steps: Array of simulation timesteps.
        - source: 'real_parquet' or 'real_csv'.
        - provenance: 'CONTROLLED_PROJECT_SYNTHETIC'.
        - fraud_ratio: Class prevalence of money laundering.
        - n_samples: Total number of ingested records.
    """
    target_dir = path if path is not None else data_dir
    root = resolve_dataset_dir("amlnet", explicit_path=target_dir)
    target_nrows = None if all_rows else (nrows or (int(kwargs["nrows"]) if "nrows" in kwargs and kwargs["nrows"] is not None else None))

    parquet_cache = root / "transactions.parquet"
    csv_candidates = [
        root / "transactions.csv",
        root / "amlnet_transactions.csv",
    ] + list(root.glob("*.csv"))

    # 1. Fast Parquet loader
    if parquet_cache.exists():
        logger.info("[AMLNet] Ingesting columnar Parquet cache from '%s'...", parquet_cache)
        df = pd.read_parquet(parquet_cache)
        if target_nrows is not None and len(df) > target_nrows:
            df = df.iloc[:target_nrows].copy()
        return _process_amlnet_dataframe(df, source="real_parquet")

    # 2. Raw CSV loader
    for csv_file in csv_candidates:
        if csv_file.exists() and not csv_file.name.endswith(".parquet"):
            logger.info("[AMLNet] Ingesting CSV from '%s'...", csv_file)
            df = pd.read_csv(csv_file, nrows=target_nrows)
            # Cache to Parquet for accelerated future reads
            if not parquet_cache.exists() and target_nrows is None:
                try:
                    df.to_parquet(parquet_cache, index=False)
                    logger.info("[AMLNet] Cached Parquet to '%s'", parquet_cache)
                except Exception as exc:
                    logger.debug("[AMLNet] Skipping Parquet cache write: %s", exc)
            return _process_amlnet_dataframe(df, source="real_csv")

    raise FileNotFoundError(
        f"Real AMLNet dataset files not found in '{root}'. "
        f"Expected 'transactions.csv' or 'transactions.parquet'. "
        f"Real dataset loaders fail closed; synthetic substitutes are prohibited."
    )


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
    "amlnet": load_amlnet,
    "aml_net": load_amlnet,
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
    elif "timestamps" in dataset_dict and dataset_dict["timestamps"] is not None:
        time_values = np.asarray(dataset_dict["timestamps"], dtype=np.float64)

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
    temporal_split: bool = False,
    preprocess: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Load a registered real benchmark dataset, optionally applying temporal split and preprocessing."""
    clean_name = name.lower().replace("-", "_").strip()
    if clean_name not in DATASET_REGISTRY:
        raise ValueError(f"Unknown dataset '{name}'. Available: {list(DATASET_REGISTRY)}")

    clean_kwargs = {
        k: v
        for k, v in kwargs.items()
        if k not in (
            "require_real",
            "force_synthetic",
            "force_mock",
            "allow_synthetic",
            "n_mock_txns",
            "n_mock_alerts",
            "temporal_split",
            "preprocess",
        )
    }

    loader = DATASET_REGISTRY[clean_name]
    data = loader(temporal_split=temporal_split, **clean_kwargs)

    # If the loader did not already perform temporal splitting, apply it here
    if temporal_split and "train" not in data and "train_mask" not in data and "X_train" not in data:
        ts_kwargs = {
            k: v
            for k, v in kwargs.items()
            if k not in (
                "nrows",
                "all_rows",
                "temporal_split",
                "dataset_mode",
            )
        }
        return temporal_split_dataset(data, preprocess=preprocess, **ts_kwargs)

    return data


# ===========================================================================
# Strict Zero-Leakage Federated Partitioning Contract & Defense
# ===========================================================================

class FederatedDataLeakageError(ValueError):
    """Raised when data leakage or partitioning contract violation is detected."""


@dataclass(frozen=True)
class ZeroLeakageAuditReport:
    """Audit report attesting to zero-leakage federated partitioning contract compliance."""

    is_valid: bool
    train_sample_count: int
    test_sample_count: int
    num_clients: int
    client_sample_counts: dict[int, int]
    index_overlap_count: int
    hash_collision_count: int
    temporal_monotonic: bool
    max_train_timestamp: float | None
    min_test_timestamp: float | None
    scaler_isolated: bool
    violations: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Convert report to dictionary."""
        return asdict(self)


class ZeroLeakagePartitionContract:
    """Enforces mathematical invariants guaranteeing zero data leakage in federated partitioning."""

    @staticmethod
    def verify_index_disjointness(
        client_train_indices: Mapping[int, Sequence[int] | np.ndarray] | list[Sequence[int] | np.ndarray],
        test_indices: Sequence[int] | np.ndarray,
    ) -> tuple[bool, int, list[str]]:
        """Verify that client training indices and test indices are strictly disjoint, and clients pairwise disjoint."""
        violations: list[str] = []
        test_set = set(int(idx) for idx in test_indices)
        total_overlap = 0

        if isinstance(client_train_indices, list):
            client_map: Mapping[int, Sequence[int] | np.ndarray] = {i: client_train_indices[i] for i in range(len(client_train_indices))}
        else:
            client_map = client_train_indices

        # 1. Check client vs test disjointness
        for client_id, indices in client_map.items():
            client_set = set(int(idx) for idx in indices)
            overlap = client_set.intersection(test_set)
            if overlap:
                total_overlap += len(overlap)
                violations.append(
                    f"Test leakage detected: Client {client_id} shares {len(overlap)} sample indices with the global test set."
                )

        # 2. Check pairwise client disjointness
        client_ids = list(client_map.keys())
        for i in range(len(client_ids)):
            c_i = client_ids[i]
            set_i = set(int(idx) for idx in client_map[c_i])
            for j in range(i + 1, len(client_ids)):
                c_j = client_ids[j]
                set_j = set(int(idx) for idx in client_map[c_j])
                inter = set_i.intersection(set_j)
                if inter:
                    total_overlap += len(inter)
                    violations.append(
                        f"Cross-client overlap detected: Client {c_i} and Client {c_j} share {len(inter)} sample indices."
                    )

        return (len(violations) == 0, total_overlap, violations)

    @staticmethod
    def verify_hash_disjointness(
        client_train_features: dict[int, np.ndarray] | list[np.ndarray],
        test_features: np.ndarray,
    ) -> tuple[bool, int, list[str]]:
        """Verify that no exact feature vectors in the global test set appear in client training sets."""
        violations: list[str] = []
        collision_count = 0

        test_arr = np.asarray(test_features)
        if len(test_arr) == 0:
            return (True, 0, violations)

        test_hashes: set[bytes] = set()
        for row in test_arr:
            test_hashes.add(hashlib.sha256(np.ascontiguousarray(row).tobytes()).digest())

        if isinstance(client_train_features, list):
            client_map = {i: client_train_features[i] for i in range(len(client_train_features))}
        else:
            client_map = client_train_features

        for client_id, c_features in client_map.items():
            c_arr = np.asarray(c_features)
            if len(c_arr) == 0:
                continue
            client_collisions = 0
            for row in c_arr:
                row_h = hashlib.sha256(np.ascontiguousarray(row).tobytes()).digest()
                if row_h in test_hashes:
                    client_collisions += 1
            if client_collisions > 0:
                collision_count += client_collisions
                violations.append(
                    f"Feature hash collision detected: Client {client_id} contains {client_collisions} exact duplicate feature rows matching global test set."
                )

        return (len(violations) == 0, collision_count, violations)

    @staticmethod
    def verify_temporal_monotonicity(
        client_train_timestamps: Mapping[int, np.ndarray] | list[np.ndarray],
        test_timestamps: np.ndarray,
    ) -> tuple[bool, float | None, float | None, list[str]]:
        """Verify that max(train_timestamp) <= min(test_timestamp) across all clients."""
        violations: list[str] = []
        if isinstance(client_train_timestamps, list):
            client_map: Mapping[int, np.ndarray] = {i: client_train_timestamps[i] for i in range(len(client_train_timestamps))}
        else:
            client_map = client_train_timestamps

        max_train: float | None = None
        for _client_id, ts in client_map.items():
            ts_arr = np.asarray(ts, dtype=np.float64)
            if len(ts_arr) > 0:
                c_max = float(np.max(ts_arr))
                if max_train is None or c_max > max_train:
                    max_train = c_max

        test_ts_arr = np.asarray(test_timestamps, dtype=np.float64)
        min_test: float | None = float(np.min(test_ts_arr)) if len(test_ts_arr) > 0 else None

        if max_train is not None and min_test is not None and max_train > min_test:
            violations.append(
                f"Temporal leakage detected: Max client training timestamp ({max_train}) exceeds min global test timestamp ({min_test})."
            )

        return (len(violations) == 0, max_train, min_test, violations)

    @staticmethod
    def verify_scaler_isolation(
        preprocessor: Any,
        train_features: np.ndarray | pd.DataFrame,
        test_features: np.ndarray | pd.DataFrame,
        raw_train_features: np.ndarray | pd.DataFrame | None = None,
    ) -> tuple[bool, list[str]]:
        """Verify that preprocessor / normalization was fitted exclusively on training data without snooping test data."""
        violations: list[str] = []
        if preprocessor is None:
            return (True, violations)

        if not getattr(preprocessor, "is_fitted", False):
            violations.append("Preprocessor is not marked as fitted.")
            return (False, violations)

        means = getattr(preprocessor, "means_", None)
        if isinstance(means, dict) and means:
            eval_feats = raw_train_features if raw_train_features is not None else train_features
            is_std = getattr(preprocessor, "numeric_strategy", "") == "standardize"
            if isinstance(eval_feats, pd.DataFrame):
                for col, fit_val in means.items():
                    if col in eval_feats.columns:
                        series = pd.to_numeric(eval_feats[col], errors="coerce").dropna()
                        if len(series) > 0:
                            actual_mean = float(series.mean())
                            if raw_train_features is None and is_std and abs(actual_mean) < 1e-2 and abs(fit_val) > 1e-2:
                                expected_mean = 0.0
                            else:
                                expected_mean = fit_val
                            if abs(actual_mean - expected_mean) > 1e-3:
                                violations.append(
                                    f"Scaler leakage: Training feature mean for column '{col}' ({actual_mean:.4f}) does not match expected ({expected_mean:.4f})."
                                )
                                break
            elif isinstance(eval_feats, np.ndarray) and eval_feats.ndim == 2:
                for idx, (col, fit_val) in enumerate(means.items()):
                    if idx < eval_feats.shape[1]:
                        actual_mean = float(np.mean(eval_feats[:, idx]))
                        if raw_train_features is None and is_std and abs(actual_mean) < 1e-2 and abs(fit_val) > 1e-2:
                            expected_mean = 0.0
                        else:
                            expected_mean = fit_val
                        if abs(actual_mean - expected_mean) > 1e-3:
                            violations.append(
                                f"Scaler leakage: Training feature mean for '{col}' ({actual_mean:.4f}) does not match expected ({expected_mean:.4f})."
                            )
                            break

        return (len(violations) == 0, violations)

    @classmethod
    def audit_federated_partitions(
        cls,
        client_datasets: Mapping[int, tuple[np.ndarray, np.ndarray]] | list[tuple[np.ndarray, np.ndarray]],
        test_dataset: tuple[np.ndarray, np.ndarray],
        client_indices: Mapping[int, Sequence[int] | np.ndarray] | list[Sequence[int] | np.ndarray] | None = None,
        test_indices: Sequence[int] | np.ndarray | None = None,
        client_timestamps: Mapping[int, np.ndarray] | list[np.ndarray] | None = None,
        test_timestamps: np.ndarray | None = None,
        preprocessor: Any | None = None,
        raw_train_features: np.ndarray | pd.DataFrame | None = None,
        raise_on_violation: bool = True,
    ) -> ZeroLeakageAuditReport:
        """Run complete 4-pillar zero-leakage federated audit across partitions."""
        all_violations: list[str] = []

        if isinstance(client_datasets, list):
            client_map: Mapping[int, tuple[np.ndarray, np.ndarray]] = {i: client_datasets[i] for i in range(len(client_datasets))}
        else:
            client_map = client_datasets

        num_clients = len(client_map)
        client_sample_counts = {cid: len(client_map[cid][0]) for cid in client_map}
        train_sample_count = sum(client_sample_counts.values())
        test_sample_count = len(test_dataset[0])

        # 1. Index disjointness
        total_overlap = 0
        if client_indices is not None and test_indices is not None:
            _idx_ok, total_overlap, idx_viols = cls.verify_index_disjointness(client_indices, test_indices)
            all_violations.extend(idx_viols)

        # 2. Hash disjointness
        client_feats = {cid: client_map[cid][0] for cid in client_map}
        test_feats = test_dataset[0]
        _hash_ok, collision_count, hash_viols = cls.verify_hash_disjointness(client_feats, test_feats)
        all_violations.extend(hash_viols)

        # 3. Temporal Monotonicity
        temporal_ok = True
        max_train_ts: float | None = None
        min_test_ts: float | None = None
        if client_timestamps is not None and test_timestamps is not None:
            temporal_ok, max_train_ts, min_test_ts, temp_viols = cls.verify_temporal_monotonicity(
                client_timestamps, test_timestamps
            )
            all_violations.extend(temp_viols)

        # 4. Scaler Isolation
        scaler_ok = True
        if preprocessor is not None:
            non_empty_train = [client_map[cid][0] for cid in client_map if len(client_map[cid][0]) > 0]
            if non_empty_train:
                train_feats_combined = np.vstack(non_empty_train)
                scaler_ok, scaler_viols = cls.verify_scaler_isolation(
                    preprocessor, train_feats_combined, test_feats, raw_train_features=raw_train_features
                )
                all_violations.extend(scaler_viols)

        is_valid = len(all_violations) == 0

        report = ZeroLeakageAuditReport(
            is_valid=is_valid,
            train_sample_count=train_sample_count,
            test_sample_count=test_sample_count,
            num_clients=num_clients,
            client_sample_counts=client_sample_counts,
            index_overlap_count=total_overlap,
            hash_collision_count=collision_count,
            temporal_monotonic=temporal_ok,
            max_train_timestamp=max_train_ts,
            min_test_timestamp=min_test_ts,
            scaler_isolated=scaler_ok,
            violations=all_violations,
        )

        if raise_on_violation and not is_valid:
            violation_summary = "; ".join(all_violations)
            raise FederatedDataLeakageError(
                f"Zero-leakage federated partitioning contract violated: {violation_summary}"
            )

        return report


def partition_and_isolate_federated_dataset(
    features: np.ndarray | pd.DataFrame,
    labels: np.ndarray | pd.Series,
    timestamps: np.ndarray | Sequence[float] | None = None,
    num_clients: int = 3,
    test_ratio: float = 0.20,
    alpha: float = 0.5,
    min_size: int = 10,
    temporal_split: bool = True,
    preprocess: bool = False,
    numeric_strategy: str = "standardize",
    impute_strategy: str = "median",
    seed: int | None = 42,
) -> dict[str, Any]:
    """Partition dataset across federated clients with strict zero-leakage global test isolation."""
    from app.application.services.fl_dirichlet_partitioner import DirichletPartitioner
    from app.application.services.preprocessor import DataPreprocessor

    if isinstance(features, pd.DataFrame):
        X_arr = features.to_numpy(dtype=np.float32)
        feature_names = list(features.columns)
    else:
        X_arr = np.asarray(features, dtype=np.float32)
        feature_names = [f"feat_{i}" for i in range(X_arr.shape[1])] if X_arr.ndim > 1 else ["feat_0"]

    if isinstance(labels, pd.Series):
        y_arr = labels.to_numpy(dtype=int)
    else:
        y_arr = np.asarray(labels, dtype=int)

    n_samples = len(X_arr)
    if n_samples == 0:
        raise ValueError("Cannot partition empty dataset.")

    # 1. Determine chronological or randomized split
    ts_train_pool: np.ndarray | None = None
    ts_test: np.ndarray | None = None

    if timestamps is not None and temporal_split:
        ts_arr = np.asarray(timestamps, dtype=np.float64)
        sort_order = np.argsort(ts_arr)
        X_sorted = X_arr[sort_order]
        y_sorted = y_arr[sort_order]
        ts_sorted = ts_arr[sort_order]
        indices_sorted = sort_order

        n_test = int(n_samples * test_ratio)
        n_train = n_samples - n_test

        X_train_pool = X_sorted[:n_train]
        y_train_pool = y_sorted[:n_train]
        ts_train_pool = ts_sorted[:n_train]
        train_indices_pool = indices_sorted[:n_train]

        X_test_raw = X_sorted[n_train:]
        y_test = y_sorted[n_train:]
        ts_test = ts_sorted[n_train:]
        test_indices = indices_sorted[n_train:]
    else:
        rng = np.random.default_rng(seed)
        shuffled_indices = np.arange(n_samples)
        rng.shuffle(shuffled_indices)

        n_test = int(n_samples * test_ratio)
        n_train = n_samples - n_test

        train_indices_pool = shuffled_indices[:n_train]
        test_indices = shuffled_indices[n_train:]

        X_train_pool = X_arr[train_indices_pool]
        y_train_pool = y_arr[train_indices_pool]
        ts_train_pool = np.asarray(timestamps)[train_indices_pool] if timestamps is not None else None

        X_test_raw = X_arr[test_indices]
        y_test = y_arr[test_indices]
        ts_test = np.asarray(timestamps)[test_indices] if timestamps is not None else None

    # 2. Strict Preprocessor Isolation (fit exclusively on train pool)
    raw_train_pool = X_train_pool.copy()
    if preprocess:
        prep = DataPreprocessor(numeric_strategy=numeric_strategy, impute_strategy=impute_strategy)
        X_train_pool = prep.fit_transform(X_train_pool)
        X_test = prep.transform(X_test_raw)
    else:
        prep = None
        X_test = X_test_raw

    # 3. Partition training pool across clients using Dirichlet Partitioner
    client_indices_rel = DirichletPartitioner.partition_indices(
        labels=y_train_pool,
        num_clients=num_clients,
        alpha=alpha,
        min_size=min_size,
        seed=seed,
    )

    client_train_datasets: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    client_train_indices: dict[int, list[int]] = {}
    client_train_timestamps: dict[int, np.ndarray] | None = {} if ts_train_pool is not None else None

    for cid in range(num_clients):
        rel_idx = client_indices_rel[cid]
        c_x = X_train_pool[rel_idx]
        c_y = y_train_pool[rel_idx]
        client_train_datasets[cid] = (c_x, c_y)
        client_train_indices[cid] = [int(train_indices_pool[i]) for i in rel_idx]
        if client_train_timestamps is not None and ts_train_pool is not None:
            client_train_timestamps[cid] = ts_train_pool[rel_idx]

    # 4. Run Zero-Leakage Partition Contract Audit
    audit_report = ZeroLeakagePartitionContract.audit_federated_partitions(
        client_datasets=client_train_datasets,
        test_dataset=(X_test, y_test),
        client_indices=client_train_indices,
        test_indices=test_indices,
        client_timestamps=client_train_timestamps,
        test_timestamps=ts_test,
        preprocessor=prep,
        raw_train_features=raw_train_pool if preprocess else None,
        raise_on_violation=True,
    )

    return {
        "client_train_datasets": client_train_datasets,
        "test_dataset": (X_test, y_test),
        "client_train_indices": client_train_indices,
        "test_indices": test_indices,
        "client_train_timestamps": client_train_timestamps,
        "test_timestamps": ts_test,
        "audit_report": audit_report,
        "preprocessor": prep,
        "feature_names": feature_names,
        "num_clients": num_clients,
        "train_sample_count": len(X_train_pool),
        "test_sample_count": len(X_test),
    }



