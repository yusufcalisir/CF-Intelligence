"""Synthetic Dataset Generators & Controlled Research/Test Fixtures.

Dedicated module for explicitly creating synthetic test fixtures, research testbeds,
and non-IID development experiments.

CRITICAL ARCHITECTURAL BOUNDARY:
- These generators are strictly separated from real-data loaders.
- Real dataset loaders (load_paysim, load_ieee_cis, load_elliptic, etc.) NEVER
  import or fall back to these functions.
- All outputs from this module are authoritatively labeled with:
    is_synthetic = True
    artifact_origin = "generated_in_process"
    scientific_origin = "synthetic"
    provenance = "CONTROLLED_PROJECT_SYNTHETIC" or "TEST_FIXTURE"
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.domain.enums import DatasetProvenance

logger = logging.getLogger(__name__)

# Constants matching canonical shapes for test fixtures
ELLIPTIC_FEATURE_DIM = 166
ELLIPTIC_ILLICIT_RATIO = 0.021

AMLSIM_FEATURE_COLS = [
    "step",
    "amount",
    "oldbalanceOrg",
    "newbalanceOrig",
    "oldbalanceDest",
    "newbalanceDest",
]
AMLSIM_FRAUD_RATIO = 0.0013

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
PAYSIM_REAL_FRAUD_RATIO = 0.00129

IEEE_CIS_FEATURE_DIM = 40
IEEE_CIS_REAL_FRAUD_RATIO = 0.035

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
    try:
        import networkx as nx
    except ImportError:
        return None

    G = nx.DiGraph()
    n_limit = len(y) if max_nodes is None else min(len(y), max_nodes)
    for i in range(n_limit):
        G.add_node(i, txId=tx_ids[i], timestep=int(timesteps[i]), label=int(y[i]))
    for u, v in edges:
        if u < n_limit and v < n_limit:
            G.add_edge(u, v)
    return G


def generate_synthetic_elliptic(
    n_mock_nodes: int = 1000,
    rng: np.random.Generator | None = None,
    include_unknown: bool = False,
    temporal_split: bool = False,
    split_timestep: int = 34,
    target_nodes: int | None = None,
    construct_graph: bool = False,
    all_rows: bool = False,
    nrows: int | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Generate controlled synthetic Elliptic Bitcoin transaction graph fixture.

    Explicitly labeled as synthetic; never presented as real benchmark evidence.
    """
    n_mock_nodes = target_nodes or nrows or n_mock_nodes
    rng = rng or np.random.default_rng(42)
    logger.info(
        "[Synthetic Elliptic Generator] Generating %d synthetic nodes (%d features)",
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

    tx_ids = np.array([f"synth_tx_{i}" for i in range(n_mock_nodes)])
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

    res: dict[str, Any] = {
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
        "provenance": DatasetProvenance.TEST_FIXTURE.value,
        "artifact_origin": "generated_in_process",
        "scientific_origin": "synthetic",
        "is_synthetic": True,
        "fraud_ratio": fraud_ratio,
        "to_pyg_data": to_pyg_data,
        "to_networkx": to_networkx,
    }

    if construct_graph:
        res["graph"] = to_networkx()

    if temporal_split and train_mask is not None and test_mask is not None and train_labeled_mask is not None and test_labeled_mask is not None:
        res.update({
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

    return res


def generate_synthetic_amlsim(
    n_mock_txns: int = 2000,
    rng: np.random.Generator | None = None,
) -> dict[str, Any]:
    """Generate controlled synthetic AMLSim-shaped transaction graph fixture."""
    rng = rng or np.random.default_rng(42)
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
    edges = list(zip(senders, receivers, strict=False))
    edge_index = np.stack([senders, receivers], axis=0)

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
        "provenance": DatasetProvenance.TEST_FIXTURE.value,
        "artifact_origin": "generated_in_process",
        "scientific_origin": "synthetic",
        "is_synthetic": True,
        "fraud_ratio": AMLSIM_FRAUD_RATIO,
    }


def generate_synthetic_paysim(
    n_mock_txns: int = 2000,
    rng: np.random.Generator | None = None,
) -> dict[str, Any]:
    """Generate controlled synthetic PaySim-shaped tabular fixture."""
    rng = rng or np.random.default_rng(42)
    n_fraud = max(1, int(n_mock_txns * PAYSIM_REAL_FRAUD_RATIO))
    n_legit = n_mock_txns - n_fraud

    type_probs = [0.338, 0.084, 0.351, 0.007, 0.220]
    types_legit = rng.choice(PAYSIM_TYPES, size=n_legit, p=type_probs)
    types_fraud = rng.choice(["TRANSFER", "CASH_OUT"], size=n_fraud, p=[0.5, 0.5])
    types_all = np.concatenate([types_legit, types_fraud])

    steps = rng.integers(1, 744, size=n_mock_txns).astype(np.float32)
    amounts_legit = rng.lognormal(mean=9.5, sigma=1.5, size=n_legit).astype(np.float32)
    amounts_fraud = rng.lognormal(mean=13.0, sigma=1.2, size=n_fraud).astype(np.float32)
    amounts = np.concatenate([amounts_legit, amounts_fraud])

    old_bal_orig = np.abs(rng.lognormal(mean=10.0, sigma=2.0, size=n_mock_txns)).astype(np.float32)
    new_bal_orig = np.maximum(0, old_bal_orig - amounts)
    new_bal_orig[n_legit:] = 0.0

    old_bal_dest = np.abs(rng.lognormal(mean=9.0, sigma=2.2, size=n_mock_txns)).astype(np.float32)
    new_bal_dest = (old_bal_dest + amounts).astype(np.float32)

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
    idx = rng.permutation(n_mock_txns)

    return {
        "X": X[idx],
        "y": y[idx],
        "feature_names": PAYSIM_FEATURE_COLS,
        "source": "mock",
        "provenance": DatasetProvenance.TEST_FIXTURE.value,
        "artifact_origin": "generated_in_process",
        "scientific_origin": "synthetic",
        "is_synthetic": True,
        "fraud_ratio": float(np.mean(y)),
        "steps": X[idx, 0],
    }


def generate_synthetic_ieee_cis(
    n_mock_txns: int = 2000,
    rng: np.random.Generator | None = None,
) -> dict[str, Any]:
    """Generate controlled synthetic IEEE-CIS-shaped tabular fixture."""
    rng = rng or np.random.default_rng(42)
    n_fraud = max(2 if n_mock_txns >= 20 else 1, int(n_mock_txns * IEEE_CIS_REAL_FRAUD_RATIO))
    n_legit = n_mock_txns - n_fraud

    amt_legit = rng.lognormal(mean=4.5, sigma=1.1, size=n_legit).astype(np.float32)
    amt_fraud = rng.lognormal(mean=5.2, sigma=1.3, size=n_fraud).astype(np.float32)
    amts = np.concatenate([amt_legit, amt_fraud])

    c_features = rng.poisson(lam=1.5, size=(n_mock_txns, 14)).astype(np.float32)
    c_features[n_legit:, :] += rng.poisson(lam=5.0, size=(n_fraud, 14)).astype(np.float32)

    d_features = rng.exponential(scale=100.0, size=(n_mock_txns, 10)).astype(np.float32)
    d_features[n_legit:, :] = rng.exponential(scale=15.0, size=(n_fraud, 10)).astype(np.float32)

    v_features = rng.standard_normal((n_mock_txns, IEEE_CIS_FEATURE_DIM - 25)).astype(np.float32)
    v_features[n_legit:, :] += 1.8

    X = np.column_stack([amts.reshape(-1, 1), c_features, d_features, v_features])
    y = np.array([0] * n_legit + [1] * n_fraud, dtype=int)

    idx = rng.permutation(n_mock_txns)
    X = X[idx]
    y = y[idx]

    mock_dt = np.sort(rng.integers(86400, 86400 * 180, size=n_mock_txns)).astype(np.float64)
    feature_names = [f"feat_{i}" for i in range(X.shape[1])]

    return {
        "X": X,
        "y": y,
        "feature_names": feature_names,
        "source": "synthetic_generator",
        "provenance": DatasetProvenance.TEST_FIXTURE.value,
        "artifact_origin": "generated_in_process",
        "scientific_origin": "synthetic",
        "is_synthetic": True,
        "fraud_ratio": float(np.mean(y)),
        "transaction_dt": mock_dt,
    }


def generate_synthetic_creditcard(
    n_mock_txns: int = 2000,
    include_time: bool = False,
    scale_time_amount: bool = True,
    scaling_strategy: str = "robust",
    split_data: bool = False,
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
    test_ratio: float = 0.15,
    stratified: bool = True,
    temporal_split: bool = False,
    rng: np.random.Generator | None = None,
    seed: int = 42,
    **kwargs: Any,
) -> dict[str, Any]:
    """Generate controlled synthetic European Cardholders PCA-shaped fixture."""
    rng = rng or np.random.default_rng(seed)
    n_fraud = max(6, int(n_mock_txns * 0.01)) if n_mock_txns < 5_000 else max(1, int(n_mock_txns * 0.00172))
    n_legit = n_mock_txns - n_fraud
    y_raw = np.array([0] * n_legit + [1] * n_fraud, dtype=int)
    idx_perm = rng.permutation(n_mock_txns)
    y = y_raw[idx_perm]

    pca_features = rng.standard_normal((n_mock_txns, 28)).astype(np.float32)
    amount_vals = np.abs(rng.exponential(scale=88.0, size=n_mock_txns)).astype(np.float32)
    time_vals = rng.uniform(0.0, 172800.0, size=n_mock_txns).astype(np.float32)

    from app.application.services.dataloader import _process_creditcard_dataframe

    data_dict = {"Time": time_vals}
    for i in range(1, 29):
        data_dict[f"V{i}"] = pca_features[:, i - 1]
    data_dict["Amount"] = amount_vals
    data_dict["Class"] = y
    df = pd.DataFrame(data_dict)

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
        source="mock_pca",
        provenance=DatasetProvenance.TEST_FIXTURE.value,
        artifact_origin="generated_in_process",
        scientific_origin="synthetic",
        is_synthetic=True,
    )


def generate_synthetic_synthaml(
    n_mock_alerts: int = 500,
    rng: np.random.Generator | None = None,
) -> dict[str, Any]:
    """Generate in-process synthetic Spar Nord Bank SynthAML alert fixture."""
    rng = rng or np.random.default_rng(42)
    import sys
    import tempfile

    repo_root = Path(__file__).resolve().parents[4]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from scripts.generate_synthaml_dataset import generate_synthaml

    with tempfile.TemporaryDirectory() as tmp_dir:
        alerts_df, tx_df = generate_synthaml(tmp_dir, n_alerts=n_mock_alerts, seed=42)

    from app.application.services.dataloader import _aggregate_synthaml_alert_features

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
        "source": "synthetic_generator",
        "provenance": DatasetProvenance.CONTROLLED_PROJECT_SYNTHETIC.value,
        "artifact_origin": "generated_in_process",
        "scientific_origin": "synthetic",
        "is_synthetic": True,
        "fraud_ratio": float(np.mean(y == 1)),
        "n_alerts": len(y),
        "n_transactions": len(tx_df),
    }


def generate_synthetic_amlnet(
    n_mock_txns: int = 1000,
    rng: np.random.Generator | None = None,
) -> dict[str, Any]:
    """Generate in-process synthetic AUSTRAC AMLNet transaction fixture."""
    rng = rng or np.random.default_rng(42)
    import sys
    import tempfile

    repo_root = Path(__file__).resolve().parents[4]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from scripts.generate_amlnet_dataset import generate_amlnet

    from app.application.services.dataloader import _process_amlnet_dataframe

    seed = int(rng.integers(0, 100000))
    with tempfile.TemporaryDirectory() as tmp_dir:
        df = generate_amlnet(tmp_dir, n_transactions=n_mock_txns, seed=seed)

    res = _process_amlnet_dataframe(df, source="synthetic_generator")
    res["provenance"] = DatasetProvenance.CONTROLLED_PROJECT_SYNTHETIC.value
    res["artifact_origin"] = "generated_in_process"
    res["scientific_origin"] = "synthetic"
    res["is_synthetic"] = True
    return res
