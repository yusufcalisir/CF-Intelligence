"""Elliptic Bitcoin Graph Inductive Representation Learning & Benchmark Suite.

Executes the GraphSAGE Neighborhood Aggregator benchmark on the Elliptic Bitcoin dataset:
1. Ingests 203,769 Bitcoin transaction nodes and 234,355 directed edges via load_elliptic.
2. Enforces strict temporal splitting:
   - Train partition: Timesteps 1 to 34 (29,894 labeled nodes: 3,462 illicit, 26,432 licit).
   - Test partition: Timesteps 35 to 49 (16,670 labeled nodes: 1,083 illicit, 15,587 licit).
   - Zero future lookahead leakage across temporal graph boundaries.
3. Benchmarks Inductive GraphSAGE (2-layer Mean Neighborhood Aggregator) against Tabular MLP Baseline.
4. Performs controlled ablations:
   - Neighborhood Hop Depth: 0-hop (Tabular MLP) vs 1-hop GraphSAGE vs 2-hop GraphSAGE.
   - Neighborhood Aggregation Uplift: Delta PR-AUC, Delta ROC-AUC, Delta Recall @ strict FPRs.
   - Aggregator Type: Mean Aggregator vs GCN Symmetric Adjacency vs Bidirectional.
   - Temporal Generalization: Out-of-time performance stability across timesteps 35 to 49.
5. Generates publication-grade empirical plots and serializes machine-readable artifacts:
   - experiments/elliptic/results.json (Pydantic v2 ExperimentResult schema)
   - experiments/elliptic/comparative_baselines.json
   - experiments/elliptic/audit_dossier.md
   - benchmarks/results/raw/graphsage_elliptic_benchmark.json
   - experiments/elliptic/plots/*.png and docs/figures/benchmark_graphsage_elliptic.png
"""

# ruff: noqa: E402
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import platform
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # Headless backend for CI/CD and automated runners
import matplotlib.pyplot as plt
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F  # noqa: N812
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)

# Ensure repository root and backend are in sys.path
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.application.services.dataloader import (
    compute_file_sha256,
    load_elliptic,
    resolve_dataset_dir,
)
from experiments.harness.schema import (
    ConfusionMatrixData,
    CurvePoint,
    DatasetMetadata,
    ExperimentConfig,
    ExperimentResult,
    HardwareMetadata,
    StepMetric,
)

logger = logging.getLogger("experiments.elliptic.train_graphsage")

CANONICAL_AGGREGATE_METRICS: tuple[str, ...] = (
    "pr_auc",
    "roc_auc",
    "precision",
    "recall",
    "f1_score",
    "recall_at_01_fpr",
)

CANONICAL_CONFIG: dict[str, Any] = {
    "dataset_mode": "real",
    "require_real": True,
    "all_rows": True,
    "seeds": (42, 123, 456),
    "epochs": 15,
    "lr": 0.005,
    "weight_decay": 1e-4,
    "hidden_dim": 128,
    "embedding_dim": 64,
    "dropout": 0.2,
    "num_layers": 2,
    "val_start_timestep": 31,
    "split_timestep": 34,
    "checkpoint_criterion": "validation_pr_auc_maximization",
    "threshold_selection_criterion": "validation_f1_maximization",
    "threshold_grid_count": 99,
    "class_weighting": "positive_class_prevalence_ratio",
}


def validate_canonical_configuration(
    *,
    dataset_mode: str,
    all_rows: bool,
    seeds: Sequence[int],
    epochs: int,
    lr: float,
    hidden_dim: int,
    embedding_dim: int,
    val_start_timestep: int = 31,
    split_timestep: int = 34,
    output_dir: Path | str | None = None,
    require_real: bool = True,
    nrows: int | None = None,
) -> None:
    """Validate that provided configuration matches the immutable canonical protocol exactly.

    Raises:
        ValueError: If any parameter deviates from the accepted canonical scientific configuration.
    """
    if dataset_mode != CANONICAL_CONFIG["dataset_mode"]:
        raise ValueError(
            f"Canonical write authorization rejected: dataset_mode must be "
            f"'{CANONICAL_CONFIG['dataset_mode']}', received '{dataset_mode}'"
        )
    if not all_rows:
        raise ValueError(
            "Canonical write authorization rejected: all_rows must be True (full 203k graph required)"
        )
    if nrows is not None:
        raise ValueError(
            f"Canonical write authorization rejected: nrows subsampling prohibited, received {nrows}"
        )
    if not require_real:
        raise ValueError(
            "Canonical write authorization rejected: require_real must be True"
        )
    if tuple(seeds) != CANONICAL_CONFIG["seeds"]:
        raise ValueError(
            f"Canonical write authorization rejected: seeds must match canonical {list(CANONICAL_CONFIG['seeds'])}, "
            f"received {list(seeds)}"
        )
    if epochs != CANONICAL_CONFIG["epochs"]:
        raise ValueError(
            f"Canonical write authorization rejected: epochs must be {CANONICAL_CONFIG['epochs']}, "
            f"received {epochs}"
        )
    if abs(lr - CANONICAL_CONFIG["lr"]) > 1e-9:
        raise ValueError(
            f"Canonical write authorization rejected: lr must be {CANONICAL_CONFIG['lr']}, "
            f"received {lr}"
        )
    if hidden_dim != CANONICAL_CONFIG["hidden_dim"]:
        raise ValueError(
            f"Canonical write authorization rejected: hidden_dim must be {CANONICAL_CONFIG['hidden_dim']}, "
            f"received {hidden_dim}"
        )
    if embedding_dim != CANONICAL_CONFIG["embedding_dim"]:
        raise ValueError(
            f"Canonical write authorization rejected: embedding_dim must be {CANONICAL_CONFIG['embedding_dim']}, "
            f"received {embedding_dim}"
        )
    if val_start_timestep != CANONICAL_CONFIG["val_start_timestep"]:
        raise ValueError(
            f"Canonical write authorization rejected: val_start_timestep must be {CANONICAL_CONFIG['val_start_timestep']}, "
            f"received {val_start_timestep}"
        )
    if split_timestep != CANONICAL_CONFIG["split_timestep"]:
        raise ValueError(
            f"Canonical write authorization rejected: split_timestep must be {CANONICAL_CONFIG['split_timestep']}, "
            f"received {split_timestep}"
        )
    if output_dir is not None:
        raise ValueError(
            f"Canonical write authorization rejected: output_dir must be None (canonical writes serialize to "
            f"benchmarks/results/raw/), received '{output_dir}'"
        )



# ===========================================================================
# 1. Visualization Styling
# ===========================================================================
def setup_publication_style() -> None:
    """Configure matplotlib rcParams for publication-ready visual artifacts."""
    plt.style.use("seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "default")
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.titleweight": "bold",
        "axes.labelsize": 10,
        "axes.labelweight": "semibold",
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "legend.fontsize": 9,
        "figure.titlesize": 13,
        "figure.titleweight": "bold",
        "figure.dpi": 300,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
    })


# ===========================================================================
# 2. Neural Architectures: Tabular MLP vs GraphSAGE
# ===========================================================================
class TabularMLPBaseline(nn.Module):
    """0-Hop Tabular Baseline: Multi-Layer Perceptron on local node features.

    Evaluates node classification performance strictly without graph topology or
    neighborhood message passing.
    """

    def __init__(self, in_dim: int, hidden_dim: int = 128, out_dim: int = 1, dropout: float = 0.2):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, 64),
            nn.LayerNorm(64),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(64, out_dim),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor, *args: Any, **kwargs: Any) -> torch.Tensor:
        return self.net(x).squeeze(-1)


class GraphSAGELayer(nn.Module):
    """Single GraphSAGE inductive message passing layer with skip combination."""

    def __init__(self, in_features: int, out_features: int, bias: bool = True):
        super().__init__()
        self.weight_self = nn.Linear(in_features, out_features, bias=False)
        self.weight_neigh = nn.Linear(in_features, out_features, bias=False)
        self.bias = nn.Parameter(torch.zeros(out_features)) if bias else None
        self.norm = nn.LayerNorm(out_features)

    def forward(self, h: torch.Tensor, adj_sparse: torch.Tensor) -> torch.Tensor:
        # Neighborhood aggregation via sparse matrix multiplication
        neigh_h = torch.sparse.mm(adj_sparse, h)
        out = self.weight_self(h) + self.weight_neigh(neigh_h)
        if self.bias is not None:
            out = out + self.bias
        out = self.norm(out)
        return F.relu(out)


class EllipticGraphSAGEClassifier(nn.Module):
    """Inductive GraphSAGE Neural Classifier for Transaction Fraud Detection.

    Supports 1-hop and 2-hop neighborhood aggregation with configurable
    embedding dimensionality and separate classification head.
    """

    def __init__(
        self,
        in_dim: int,
        hidden_dim: int = 128,
        embedding_dim: int = 64,
        num_layers: int = 2,
        dropout: float = 0.2,
    ):
        super().__init__()
        self.num_layers = num_layers
        self.in_dim = in_dim
        self.hidden_dim = hidden_dim
        self.embedding_dim = embedding_dim
        self.dropout = nn.Dropout(dropout)

        if num_layers == 1:
            self.layer1 = GraphSAGELayer(in_dim, embedding_dim)
            self.layer2 = None
        else:
            self.layer1 = GraphSAGELayer(in_dim, hidden_dim)
            self.layer2 = GraphSAGELayer(hidden_dim, embedding_dim)

        self.classifier = nn.Sequential(
            nn.Linear(embedding_dim, 16),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(16, 1),
            nn.Sigmoid(),
        )

    def get_embeddings(self, x: torch.Tensor | Any, adj_sparse: torch.Tensor | Any) -> torch.Tensor:
        h = self.layer1(x, adj_sparse)
        h = self.dropout(h)
        if self.layer2 is not None:
            h = self.layer2(h, adj_sparse)
            h = self.dropout(h)
        return F.normalize(h, p=2, dim=1)

    def forward(self, x: torch.Tensor | Any, adj_sparse: torch.Tensor | Any) -> torch.Tensor:
        embeddings = self.get_embeddings(x, adj_sparse)
        return self.classifier(embeddings).squeeze(-1)


# ===========================================================================
# 3. Graph Adjacency Operators
# ===========================================================================
def build_normalized_adjacency(
    edge_index: np.ndarray | Any,
    num_nodes: int,
    mode: str = "mean",
    add_self_loops: bool = True,
    bidirectional: bool = True,
) -> torch.Tensor:
    """Construct normalized PyTorch sparse COO tensor representing graph adjacency.

    Args:
        edge_index: (2, E) numpy array of directed edges (src -> dst).
        num_nodes: Total number of nodes in graph snapshot.
        mode: Aggregator normalization ('mean' for D^{-1}A or 'gcn' for D^{-1/2}AD^{-1/2}).
        add_self_loops: Whether to add identity connections (u -> u).
        bidirectional: In financial flows, whether to include reverse edges (flow provenance).

    Returns:
        torch.Tensor coalesced sparse COO matrix of shape (num_nodes, num_nodes).
    """
    if edge_index.shape[1] == 0:
        # Fallback to pure diagonal self-loops if graph has no edges
        indices = torch.arange(num_nodes, dtype=torch.long)
        indices_2d = torch.stack([indices, indices])
        values = torch.ones(num_nodes, dtype=torch.float32)
        return torch.sparse_coo_tensor(indices_2d, values, (num_nodes, num_nodes)).coalesce()

    src = torch.from_numpy(edge_index[0]).long()
    dst = torch.from_numpy(edge_index[1]).long()

    # Filter out-of-bounds node indices
    valid = (src < num_nodes) & (dst < num_nodes) & (src >= 0) & (dst >= 0)
    src, dst = src[valid], dst[valid]

    rows = [src]
    cols = [dst]

    if bidirectional:
        rows.append(dst)
        cols.append(src)

    if add_self_loops:
        self_idx = torch.arange(num_nodes, dtype=torch.long)
        rows.append(self_idx)
        cols.append(self_idx)

    all_rows = torch.cat(rows)
    all_cols = torch.cat(cols)

    deg = torch.bincount(all_rows, minlength=num_nodes).float()

    if mode == "gcn":
        # Symmetric normalization: D^{-1/2} A D^{-1/2}
        deg_inv_sqrt = torch.where(deg > 0, 1.0 / torch.sqrt(deg), torch.zeros_like(deg))
        weights = deg_inv_sqrt[all_rows] * deg_inv_sqrt[all_cols]
    else:
        # Row normalization (Mean Aggregator): D^{-1} A
        deg_inv = torch.where(deg > 0, 1.0 / deg, torch.zeros_like(deg))
        weights = deg_inv[all_rows]

    indices = torch.stack([all_rows, all_cols])
    return torch.sparse_coo_tensor(indices, weights, (num_nodes, num_nodes)).coalesce()


# ===========================================================================
# 4. Evaluation & Metric Engine
# ===========================================================================
def compute_fixed_fpr_recalls(
    y_true: np.ndarray | Any,
    y_prob: np.ndarray | Any,
    target_fprs: Sequence[float] = (0.001, 0.005, 0.01),
) -> dict[str, float]:
    """Calculate operational True Positive Rate (Recall) at strict False Positive Rate bounds."""
    fpr, tpr, thresholds = roc_curve(y_true, y_prob)
    results: dict[str, float] = {}

    for alpha in target_fprs:
        key = f"recall_at_{str(alpha).replace('.', '')}_fpr"
        # Find maximum threshold where empirical FPR <= alpha
        valid_indices = np.where(fpr <= alpha)[0]
        if len(valid_indices) > 0:
            best_idx = valid_indices[-1]
            rec = float(tpr[best_idx])
        else:
            rec = 0.0
        results[key] = round(rec, 6)

    return results


def evaluate_predictions(
    y_true: np.ndarray | Any,
    y_prob: np.ndarray | Any,
    threshold: float = 0.5,
) -> dict[str, Any]:
    """Extract full empirical evaluation metrics on binary fraud predictions.

    Args:
        y_true: Ground truth binary labels (0 or 1).
        y_prob: Continuous prediction scores/probabilities in [0.0, 1.0].
        threshold: Frozen decision threshold for binary classification metrics.

    Returns:
        Dictionary with ranking metrics (continuous) and operational metrics (thresholded).
    """
    # Ensure binary labels are strictly 0 and 1
    valid_mask = np.isin(y_true, [0, 1])
    y_t = y_true[valid_mask].astype(int)
    y_p = y_prob[valid_mask].astype(float)

    if len(y_t) == 0 or np.sum(y_t == 1) == 0:
        return {
            "pr_auc": 0.0,
            "roc_auc": 0.5,
            "f1_score": 0.0,
            "precision": 0.0,
            "recall": 0.0,
            "operating_threshold": round(threshold, 4),
            "brier_score": 0.0,
            "recall_at_01_fpr": 0.0,
            "recall_at_05_fpr": 0.0,
            "recall_at_10_fpr": 0.0,
            "confusion_matrix": {"tn": len(y_t), "fp": 0, "fn": 0, "tp": 0},
            "non_canonical_05": {
                "precision": 0.0,
                "recall": 0.0,
                "f1_score": 0.0,
                "confusion_matrix": {"tn": len(y_t), "fp": 0, "fn": 0, "tp": 0},
            },
        }

    # Threshold-independent continuous ranking metrics
    pr_auc = float(average_precision_score(y_t, y_p))
    roc_auc = float(roc_auc_score(y_t, y_p))
    brier = float(brier_score_loss(y_t, y_p))

    # Binary metrics at evaluated operating threshold
    bin_preds = (y_p >= threshold).astype(int)
    f1 = float(f1_score(y_t, bin_preds, zero_division=0))  # type: ignore[arg-type]
    prec = float(precision_score(y_t, bin_preds, zero_division=0))  # type: ignore[arg-type]
    rec = float(recall_score(y_t, bin_preds, zero_division=0))  # type: ignore[arg-type]

    # Fixed-FPR operational metrics
    fixed_fprs = compute_fixed_fpr_recalls(y_t, y_p, [0.001, 0.005, 0.01])

    # Confusion matrix at operating threshold
    cm = confusion_matrix(y_t, bin_preds, labels=[0, 1])
    tn, fp, fn, tp = int(cm[0, 0]), int(cm[0, 1]), int(cm[1, 0]), int(cm[1, 1])

    # Secondary diagnostic at non-canonical 0.5 threshold
    bin_05 = (y_p >= 0.5).astype(int)
    cm_05 = confusion_matrix(y_t, bin_05, labels=[0, 1])
    prec_05 = float(precision_score(y_t, bin_05, zero_division=0))  # type: ignore[arg-type]
    rec_05 = float(recall_score(y_t, bin_05, zero_division=0))  # type: ignore[arg-type]
    f1_05 = float(f1_score(y_t, bin_05, zero_division=0))  # type: ignore[arg-type]

    return {
        "pr_auc": round(pr_auc, 6),
        "roc_auc": round(roc_auc, 6),
        "f1_score": round(f1, 6),
        "precision": round(prec, 6),
        "recall": round(rec, 6),
        "operating_threshold": round(threshold, 4),
        "brier_score": round(brier, 6),
        "recall_at_01_fpr": fixed_fprs.get("recall_at_0001_fpr", 0.0),
        "recall_at_05_fpr": fixed_fprs.get("recall_at_0005_fpr", 0.0),
        "recall_at_10_fpr": fixed_fprs.get("recall_at_001_fpr", 0.0),
        "confusion_matrix": {"tn": tn, "fp": fp, "fn": fn, "tp": tp},
        "non_canonical_05": {
            "threshold": 0.5,
            "precision": round(prec_05, 6),
            "recall": round(rec_05, 6),
            "f1_score": round(f1_05, 6),
            "confusion_matrix": {
                "tn": int(cm_05[0, 0]),
                "fp": int(cm_05[0, 1]),
                "fn": int(cm_05[1, 0]),
                "tp": int(cm_05[1, 1]),
            },
        },
    }


# ===========================================================================
# 5. Master Benchmark Orchestrator
# ===========================================================================
class EllipticGraphSAGEBenchmark:
    """Comprehensive Inductive GraphSAGE vs Tabular MLP Benchmark Orchestrator."""

    def __init__(
        self,
        seed: int = 42,
        seeds: Sequence[int] | None = None,
        data_dir: Path | str | None = None,
        require_real: bool | None = None,
        dataset_mode: str = "real",
        all_rows: bool = True,
        nrows: int | None = None,
        split_timestep: int = 34,
        val_start_timestep: int = 31,
        is_canonical: bool = False,
    ):
        self.seed = seed
        self.seeds = list(seeds) if seeds is not None else [seed]
        self.data_dir = Path(data_dir) if data_dir is not None else None
        if require_real is False and dataset_mode == "real":
            dataset_mode = "synthetic"
        if require_real is None:
            require_real = (dataset_mode == "real")
        self.require_real = (require_real is True) and (dataset_mode == "real")
        self.dataset_mode = dataset_mode
        self.all_rows = all_rows
        self.nrows = nrows
        self.split_timestep = split_timestep
        self.val_start_timestep = val_start_timestep
        self.is_canonical = is_canonical

        if is_canonical:
            validate_canonical_configuration(
                dataset_mode=self.dataset_mode,
                all_rows=self.all_rows,
                require_real=self.require_real,
                seeds=self.seeds,
                epochs=CANONICAL_CONFIG["epochs"],
                lr=CANONICAL_CONFIG["lr"],
                hidden_dim=CANONICAL_CONFIG["hidden_dim"],
                embedding_dim=CANONICAL_CONFIG["embedding_dim"],
                val_start_timestep=self.val_start_timestep,
                split_timestep=self.split_timestep,
                nrows=self.nrows,
            )

        np.random.seed(seed)
        torch.manual_seed(seed)

    def load_dataset(self) -> dict[str, Any]:
        """Alias for load_and_preprocess."""
        return self.load_and_preprocess()

    def load_and_preprocess(self) -> dict[str, Any]:
        """Ingest Elliptic Bitcoin data and fit zero-leakage preprocessor on train partition."""
        logger.info(
            "[Elliptic] Loading dataset (dataset_mode=%s, require_real=%s, all_rows=%s)...",
            self.dataset_mode,
            self.require_real,
            self.all_rows,
        )

        root = self.data_dir or resolve_dataset_dir("elliptic")
        features_csv = root / "elliptic_txs_features.csv"
        classes_csv = root / "elliptic_txs_classes.csv"
        parquet_cache = root / "elliptic_cache.parquet"
        has_real_files = parquet_cache.exists() or (features_csv.exists() and classes_csv.exists())

        if self.dataset_mode == "real" and not has_real_files:
            raise FileNotFoundError(
                f"Real Elliptic Bitcoin dataset files not found in '{root}'. "
                f"Expected 'elliptic_cache.parquet' or ('elliptic_txs_features.csv' and 'elliptic_txs_classes.csv'). "
                f"Silent synthetic fallback is strictly disabled in canonical real-data mode."
            )

        if self.dataset_mode == "synthetic":
            from app.application.services.synthetic_dataset_generators import (
                generate_synthetic_elliptic,
            )

            raw_data = generate_synthetic_elliptic(
                target_nodes=self.nrows or 5000,
                include_unknown=True,
                temporal_split=True,
                split_timestep=self.split_timestep,
                construct_graph=True,
                all_rows=self.all_rows,
                nrows=self.nrows,
            )
        else:
            raw_data = load_elliptic(
                path=self.data_dir,
                include_unknown=True,
                temporal_split=True,
                split_timestep=self.split_timestep,
                construct_graph=True,
                all_rows=self.all_rows,
                nrows=self.nrows,
            )

        X_raw = raw_data["X"]
        y = raw_data["y"]
        edge_index = raw_data["edge_index"]
        timesteps = raw_data["timesteps"]

        # If col 0 is timestep, isolate features 1..end (165 features), otherwise use all features
        if X_raw.shape[1] > 165:
            features = X_raw[:, 1:].astype(np.float32)
        else:
            features = X_raw.astype(np.float32)

        # Strict past-to-future temporal partitions:
        # Train: timesteps < val_start_timestep (1 to 30)
        # Validation: val_start_timestep <= timesteps <= split_timestep (31 to 34)
        # Test: timesteps > split_timestep (35 to 49)
        train_mask = (timesteps < self.val_start_timestep) & (y != -1)
        val_mask = (timesteps >= self.val_start_timestep) & (timesteps <= self.split_timestep) & (y != -1)
        test_mask = (timesteps > self.split_timestep) & (y != -1)

        # Standard scale fitted strictly on train split avoiding val/test distribution leakage
        train_feat = features[train_mask] if np.any(train_mask) else features
        feat_mean = np.mean(train_feat, axis=0)
        feat_std = np.std(train_feat, axis=0)
        feat_std[feat_std < 1e-6] = 1.0

        features_normalized = (features - feat_mean) / feat_std

        # Compute physical dataset file hashes if real files exist
        file_hashes: dict[str, str] = {}
        for fname in ["elliptic_cache.parquet", "elliptic_txs_classes.csv", "elliptic_txs_edgelist.csv", "elliptic_txs_features.csv"]:
            fpath = root / fname
            if fpath.exists():
                with contextlib.suppress(Exception):
                    file_hashes[fname] = compute_file_sha256(fpath)

        return {
            "X": features_normalized,
            "y": y,
            "edge_index": edge_index,
            "train_mask": train_mask,
            "val_mask": val_mask,
            "test_mask": test_mask,
            "train_labeled_mask": train_mask,
            "test_labeled_mask": test_mask,
            "val_labeled_mask": val_mask,
            "timesteps": timesteps,
            "source": raw_data.get("source", "real"),
            "file_hashes": file_hashes,
            "n_train_labeled": int(np.sum(train_mask)),
            "n_val_labeled": int(np.sum(val_mask)),
            "n_test_labeled": int(np.sum(test_mask)),
            "n_train_illicit": int(np.sum(train_mask & (y == 1))),
            "n_val_illicit": int(np.sum(val_mask & (y == 1))),
            "n_test_illicit": int(np.sum(test_mask & (y == 1))),
            "n_train_licit": int(np.sum(train_mask & (y == 0))),
            "n_val_licit": int(np.sum(val_mask & (y == 0))),
            "n_test_licit": int(np.sum(test_mask & (y == 0))),
        }

    def train_and_evaluate_model(
        self,
        model: nn.Module,
        X: np.ndarray,
        y: np.ndarray,
        train_mask: np.ndarray,
        test_mask: np.ndarray,
        val_mask: np.ndarray | None = None,
        adj_sparse: torch.Tensor | None = None,
        epochs: int = 15,
        lr: float = 0.005,
        weight_decay: float = 1e-4,
    ) -> tuple[dict[str, Any], np.ndarray, list[float], int, float, float | None]:
        """Train candidate architecture, select checkpoint and threshold via validation split, and evaluate on untouched test partition."""
        x_t = torch.from_numpy(X).float()
        y_t = torch.from_numpy(y).float()
        tr_mask_t = torch.from_numpy(train_mask)
        has_val = val_mask is not None and np.any(val_mask) and np.sum(y[val_mask] == 1) > 0
        val_mask_t = torch.from_numpy(val_mask) if has_val else None

        # Compute positive class re-weighting on training partition to combat class imbalance
        y_train = y[train_mask]
        num_pos = int(np.sum(y_train == 1))
        num_neg = int(np.sum(y_train == 0))
        pos_weight = float(num_neg / max(1, num_pos))
        weight_tensor = torch.where(y_t[tr_mask_t] == 1, torch.tensor(pos_weight), 1.0)

        optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)
        loss_history: list[float] = []

        best_val_prauc = -1.0
        best_epoch = epochs
        best_state = None

        for epoch in range(epochs):
            model.train()
            optimizer.zero_grad()
            preds = model(x_t, adj_sparse) if adj_sparse is not None else model(x_t)
            loss = F.binary_cross_entropy(preds[tr_mask_t], y_t[tr_mask_t], weight=weight_tensor)
            loss.backward()
            optimizer.step()
            loss_history.append(float(loss.item()))

            # Validation checkpoint tracking (PR-AUC on validation split)
            if has_val:
                model.eval()
                with torch.no_grad():
                    val_preds_ep = model(x_t, adj_sparse) if adj_sparse is not None else model(x_t)
                    val_y_arr = y[val_mask]
                    val_preds_arr = val_preds_ep[val_mask_t].cpu().numpy()
                    val_pr = float(average_precision_score(val_y_arr, val_preds_arr))
                    if val_pr > best_val_prauc:
                        best_val_prauc = val_pr
                        best_epoch = epoch + 1
                        best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}

        # Restore best checkpoint if validation was enabled
        if best_state is not None:
            model.load_state_dict(best_state)

        model.eval()
        t0 = time.perf_counter()
        with torch.no_grad():
            if adj_sparse is not None:
                all_preds = model(x_t, adj_sparse).cpu().numpy()
            else:
                all_preds = model(x_t).cpu().numpy()
        latency_ms = (time.perf_counter() - t0) * 1000.0

        # Threshold selection: sweep on validation predictions to maximize F1 (deterministic tie-breaking: pick lowest)
        best_threshold = 0.5
        if has_val:
            val_preds = all_preds[val_mask]
            val_y = y[val_mask]
            thresholds = np.linspace(0.01, 0.99, 99)
            best_val_f1 = -1.0
            for th in thresholds:
                f1 = float(f1_score(val_y, (val_preds >= th).astype(int), zero_division=0))  # type: ignore[arg-type]
                if f1 > best_val_f1:
                    best_val_f1 = f1
                    best_threshold = float(th)

        test_preds = all_preds[test_mask]
        test_y = y[test_mask]

        metrics = evaluate_predictions(test_y, test_preds, threshold=best_threshold)
        metrics["inference_latency_ms"] = round(latency_ms, 2)
        metrics["inference_latency_per_1k_ms"] = round(latency_ms / (len(y) / 1000.0), 3)
        metrics["best_epoch"] = best_epoch
        metrics["val_pr_auc"] = round(best_val_prauc, 6) if best_val_prauc >= 0 else None

        return metrics, all_preds, loss_history, best_epoch, best_threshold, (best_val_prauc if best_val_prauc >= 0 else None)

    def run_benchmark(
        self,
        epochs: int = 15,
        lr: float = 0.005,
        hidden_dim: int = 128,
        embedding_dim: int = 64,
        output_dir: Path | str | None = None,
        is_canonical: bool = False,
    ) -> dict[str, Any]:
        """Execute full GraphSAGE vs Tabular MLP benchmark with multi-seed evaluation and controlled ablations."""
        effective_canonical = bool(getattr(self, "is_canonical", False) or is_canonical)
        if effective_canonical:
            validate_canonical_configuration(
                dataset_mode=self.dataset_mode,
                all_rows=self.all_rows,
                require_real=self.require_real,
                seeds=self.seeds,
                epochs=epochs,
                lr=lr,
                hidden_dim=hidden_dim,
                embedding_dim=embedding_dim,
                val_start_timestep=self.val_start_timestep,
                split_timestep=self.split_timestep,
                output_dir=output_dir,
                nrows=self.nrows,
            )

        start_time_utc = datetime.now(UTC).isoformat()
        t_start = time.perf_counter()

        data = self.load_and_preprocess()
        X = data["X"]
        y = data["y"]
        edge_index = data["edge_index"]
        train_mask = data["train_mask"]
        val_mask = data["val_mask"]
        test_mask = data["test_mask"]
        timesteps = data["timesteps"]
        num_nodes = len(y)
        in_dim = X.shape[1]

        logger.info(
            "[Elliptic Benchmark] Total Nodes: %d, Features: %d, Train Labeled (ts 1-%d): %d (illicit: %d), Val Labeled (ts %d-%d): %d (illicit: %d), Test Labeled (ts %d-49): %d (illicit: %d)",
            num_nodes,
            in_dim,
            self.val_start_timestep - 1,
            data["n_train_labeled"],
            data["n_train_illicit"],
            self.val_start_timestep,
            self.split_timestep,
            data["n_val_labeled"],
            data["n_val_illicit"],
            self.split_timestep + 1,
            data["n_test_labeled"],
            data["n_test_illicit"],
        )

        # Build sparse adjacency operators
        adj_mean = build_normalized_adjacency(edge_index, num_nodes, mode="mean", bidirectional=True)
        adj_gcn = build_normalized_adjacency(edge_index, num_nodes, mode="gcn", bidirectional=True)

        # -----------------------------------------------------------------------
        # Multi-Seed GraphSAGE 2-Layer Champion Execution
        # -----------------------------------------------------------------------
        seed_results: list[dict[str, Any]] = []
        for s in self.seeds:
            logger.info("[Elliptic Benchmark] Running GraphSAGE 2-Layer with Seed %d...", s)
            np.random.seed(s)
            torch.manual_seed(s)
            sage_seed_model = EllipticGraphSAGEClassifier(
                in_dim=in_dim, hidden_dim=hidden_dim, embedding_dim=embedding_dim, num_layers=2
            )
            s_metrics, s_preds, s_losses, s_epoch, s_th, s_val_pr = self.train_and_evaluate_model(
                sage_seed_model, X, y, train_mask, test_mask, val_mask=val_mask, adj_sparse=adj_mean, epochs=epochs, lr=lr
            )
            seed_results.append({
                "seed": s,
                "best_epoch": s_epoch,
                "val_pr_auc": s_val_pr,
                "threshold": s_th,
                "metrics": s_metrics,
                "losses": s_losses,
                "preds": s_preds,
            })
            logger.info(
                "[Seed %d] Epoch: %d, Val PR-AUC: %s, Threshold: %.4f | Test PR-AUC: %.4f, ROC-AUC: %.4f, Precision: %.4f, Recall: %.4f, F1: %.4f",
                s,
                s_epoch,
                f"{s_val_pr:.4f}" if s_val_pr is not None else "N/A",
                s_th,
                s_metrics["pr_auc"],
                s_metrics["roc_auc"],
                s_metrics["precision"],
                s_metrics["recall"],
                s_metrics["f1_score"],
            )

        # Champion primary seed results (first seed, typically 42)
        primary_res = seed_results[0]
        sage2_metrics = primary_res["metrics"]
        sage2_all_preds = primary_res["preds"]
        sage2_losses = primary_res["losses"]

        # Aggregate statistics across seeds
        pr_aucs = [r["metrics"]["pr_auc"] for r in seed_results]
        roc_aucs = [r["metrics"]["roc_auc"] for r in seed_results]
        precs = [r["metrics"]["precision"] for r in seed_results]
        recs = [r["metrics"]["recall"] for r in seed_results]
        f1s = [r["metrics"]["f1_score"] for r in seed_results]
        r01s = [r["metrics"]["recall_at_01_fpr"] for r in seed_results]
        r05s = [r["metrics"]["recall_at_05_fpr"] for r in seed_results]
        r10s = [r["metrics"]["recall_at_10_fpr"] for r in seed_results]
        ths = [r["threshold"] for r in seed_results]

        ddof = 1 if len(self.seeds) > 1 else 0
        aggregate_metrics = {
            "mean": {
                "pr_auc": round(float(np.mean(pr_aucs)), 4),
                "roc_auc": round(float(np.mean(roc_aucs)), 4),
                "precision": round(float(np.mean(precs)), 4),
                "recall": round(float(np.mean(recs)), 4),
                "f1_score": round(float(np.mean(f1s)), 4),
                "recall_at_01_fpr": round(float(np.mean(r01s)), 4),
                "recall_at_05_fpr": round(float(np.mean(r05s)), 4),
                "recall_at_10_fpr": round(float(np.mean(r10s)), 4),
                "threshold": round(float(np.mean(ths)), 4),
            },
            "std": {
                "definition": f"sample standard deviation across {len(self.seeds)} training seeds, ddof={ddof}",
                "pr_auc": round(float(np.std(pr_aucs, ddof=ddof)), 4),
                "roc_auc": round(float(np.std(roc_aucs, ddof=ddof)), 4),
                "precision": round(float(np.std(precs, ddof=ddof)), 4),
                "recall": round(float(np.std(recs, ddof=ddof)), 4),
                "f1_score": round(float(np.std(f1s, ddof=ddof)), 4),
                "recall_at_01_fpr": round(float(np.std(r01s, ddof=ddof)), 4),
            },
            "min": {
                "pr_auc": round(float(np.min(pr_aucs)), 4),
                "roc_auc": round(float(np.min(roc_aucs)), 4),
                "precision": round(float(np.min(precs)), 4),
                "recall": round(float(np.min(recs)), 4),
                "f1_score": round(float(np.min(f1s)), 4),
                "recall_at_01_fpr": round(float(np.min(r01s)), 4),
            },
            "max": {
                "pr_auc": round(float(np.max(pr_aucs)), 4),
                "roc_auc": round(float(np.max(roc_aucs)), 4),
                "precision": round(float(np.max(precs)), 4),
                "recall": round(float(np.max(recs)), 4),
                "f1_score": round(float(np.max(f1s)), 4),
                "recall_at_01_fpr": round(float(np.max(r01s)), 4),
            },
        }

        # -----------------------------------------------------------------------
        # Model 1: Tabular MLP Baseline (0-Hop)
        # -----------------------------------------------------------------------
        logger.info("[Elliptic Benchmark] Training Tabular MLP Baseline (0-hop)...")
        np.random.seed(self.seed)
        torch.manual_seed(self.seed)
        mlp_model = TabularMLPBaseline(in_dim=in_dim, hidden_dim=hidden_dim)
        mlp_metrics, mlp_all_preds, mlp_losses, _, _, _ = self.train_and_evaluate_model(
            mlp_model, X, y, train_mask, test_mask, val_mask=val_mask, adj_sparse=None, epochs=epochs, lr=lr
        )
        logger.info(
            "[Tabular MLP] PR-AUC: %.4f, ROC-AUC: %.4f, Recall@0.1%%FPR: %.4f, F1: %.4f",
            mlp_metrics["pr_auc"],
            mlp_metrics["roc_auc"],
            mlp_metrics["recall_at_01_fpr"],
            mlp_metrics["f1_score"],
        )

        # -----------------------------------------------------------------------
        # Ablation 1: GraphSAGE 1-Layer (1-Hop Neighborhood)
        # -----------------------------------------------------------------------
        logger.info("[Elliptic Benchmark] Training GraphSAGE 1-Layer (1-hop ablation)...")
        np.random.seed(self.seed)
        torch.manual_seed(self.seed)
        sage1_model = EllipticGraphSAGEClassifier(
            in_dim=in_dim, hidden_dim=hidden_dim, embedding_dim=embedding_dim, num_layers=1
        )
        sage1_metrics, sage1_all_preds, sage1_losses, _, _, _ = self.train_and_evaluate_model(
            sage1_model, X, y, train_mask, test_mask, val_mask=val_mask, adj_sparse=adj_mean, epochs=epochs, lr=lr
        )

        # -----------------------------------------------------------------------
        # Ablation 2: Aggregator Type (GCN Symmetric Adjacency)
        # -----------------------------------------------------------------------
        logger.info("[Elliptic Benchmark] Training GraphSAGE with GCN Aggregator...")
        np.random.seed(self.seed)
        torch.manual_seed(self.seed)
        gcn_model = EllipticGraphSAGEClassifier(
            in_dim=in_dim, hidden_dim=hidden_dim, embedding_dim=embedding_dim, num_layers=2
        )
        gcn_metrics, gcn_all_preds, _, _, _, _ = self.train_and_evaluate_model(
            gcn_model, X, y, train_mask, test_mask, val_mask=val_mask, adj_sparse=adj_gcn, epochs=epochs, lr=lr
        )

        # -----------------------------------------------------------------------
        # Controlled Ablation & Neighborhood Uplift Quantification
        # -----------------------------------------------------------------------
        uplift = {
            "delta_pr_auc_vs_tabular": round(sage2_metrics["pr_auc"] - mlp_metrics["pr_auc"], 6),
            "delta_roc_auc_vs_tabular": round(sage2_metrics["roc_auc"] - mlp_metrics["roc_auc"], 6),
            "delta_recall_01_fpr_vs_tabular": round(sage2_metrics["recall_at_01_fpr"] - mlp_metrics["recall_at_01_fpr"], 6),
            "delta_recall_05_fpr_vs_tabular": round(sage2_metrics["recall_at_05_fpr"] - mlp_metrics["recall_at_05_fpr"], 6),
            "delta_recall_10_fpr_vs_tabular": round(sage2_metrics["recall_at_10_fpr"] - mlp_metrics["recall_at_10_fpr"], 6),
            "delta_f1_vs_tabular": round(sage2_metrics["f1_score"] - mlp_metrics["f1_score"], 6),
            "hop_ablation": {
                "0_hop_tabular_mlp": mlp_metrics,
                "1_hop_graphsage": sage1_metrics,
                "2_hop_graphsage": sage2_metrics,
            },
            "aggregator_ablation": {
                "mean_aggregator": sage2_metrics,
                "gcn_aggregator": gcn_metrics,
            },
        }

        # -----------------------------------------------------------------------
        # Temporal Generalization Breakdown across Timesteps 35 to 49
        # -----------------------------------------------------------------------
        temporal_breakdown: list[dict[str, Any]] = []
        for ts in range(self.split_timestep + 1, 50):
            ts_mask = test_mask & (timesteps == ts)
            if np.sum(ts_mask & (y == 1)) > 0:
                y_ts = y[ts_mask]
                sage_ts_pr = float(average_precision_score(y_ts, sage2_all_preds[ts_mask]))
                mlp_ts_pr = float(average_precision_score(y_ts, mlp_all_preds[ts_mask]))
                temporal_breakdown.append({
                    "timestep": ts,
                    "total_nodes": int(np.sum(timesteps == ts)),
                    "labeled_nodes": len(y_ts),
                    "illicit_nodes": int(np.sum(y_ts == 1)),
                    "graphsage_pr_auc": round(sage_ts_pr, 4),
                    "tabular_mlp_pr_auc": round(mlp_ts_pr, 4),
                    "delta_pr_auc": round(sage_ts_pr - mlp_ts_pr, 4),
                })

        duration_sec = round(time.perf_counter() - t_start, 2)
        end_time_utc = datetime.now(UTC).isoformat()

        # Build output paths
        # Strict canonical write authorization:
        # Requires explicit canonical execution context (is_canonical flag),
        # physical real dataset mode, full graph topology (all_rows=True),
        # default output_dir (None), and multi-seed statistical evaluation (len(self.seeds) >= 3).
        # Any ordinary real diagnostic run, single-seed run, non-default output directory,
        # or synthetic run is strictly prohibited from writing or mutating canonical repository artifacts.
        effective_canonical = bool(getattr(self, "is_canonical", False) or is_canonical)
        is_canonical_real_run = (
            effective_canonical is True
            and output_dir is None
            and self.dataset_mode == CANONICAL_CONFIG["dataset_mode"]
            and self.all_rows is True
            and self.nrows is None
            and tuple(self.seeds) == CANONICAL_CONFIG["seeds"]
            and epochs == CANONICAL_CONFIG["epochs"]
            and abs(lr - CANONICAL_CONFIG["lr"]) <= 1e-9
            and hidden_dim == CANONICAL_CONFIG["hidden_dim"]
            and embedding_dim == CANONICAL_CONFIG["embedding_dim"]
            and self.val_start_timestep == CANONICAL_CONFIG["val_start_timestep"]
            and self.split_timestep == CANONICAL_CONFIG["split_timestep"]
        )
        if output_dir:
            target_dir = Path(output_dir)
            canonical_exp_dir = (REPO_ROOT / "experiments" / "elliptic").resolve()
            canonical_raw_dir = (REPO_ROOT / "benchmarks" / "results" / "raw").resolve()
            if not is_canonical_real_run and target_dir.resolve() in (canonical_exp_dir, canonical_raw_dir):
                raise ValueError(
                    f"Canonical repository artifact destinations ({target_dir}) are strictly protected. "
                    f"Non-canonical runs cannot write to canonical locations. Specify an isolated output directory."
                )
        elif is_canonical_real_run:
            target_dir = REPO_ROOT / "experiments" / "elliptic"
        elif self.dataset_mode == "real":
            target_dir = REPO_ROOT / "experiments" / "elliptic" / "diagnostic"
        else:
            target_dir = REPO_ROOT / "experiments" / "elliptic" / "synthetic"

        plots_dir = target_dir / "plots"
        plots_dir.mkdir(parents=True, exist_ok=True)
        raw_results_dir = REPO_ROOT / "benchmarks" / "results" / "raw"
        docs_figures_dir = REPO_ROOT / "docs" / "figures"
        if is_canonical_real_run:
            raw_results_dir.mkdir(parents=True, exist_ok=True)
            docs_figures_dir.mkdir(parents=True, exist_ok=True)

        # -----------------------------------------------------------------------
        # Generate Publication Plots
        # -----------------------------------------------------------------------
        plot_paths = self._generate_publication_plots(
            y[test_mask],
            mlp_all_preds[test_mask],
            sage1_all_preds[test_mask],
            sage2_all_preds[test_mask],
            uplift,
            temporal_breakdown,
            plots_dir,
            docs_figures_dir if is_canonical_real_run else None,
            is_canonical_real_run=is_canonical_real_run,
        )

        # -----------------------------------------------------------------------
        # Construct Comparative Baselines Payload
        # -----------------------------------------------------------------------
        comparative_baselines = {
            "dataset_name": "Elliptic Bitcoin Transaction Graph",
            "evaluated_at_utc": end_time_utc,
            "temporal_split": f"Timesteps 1-{self.val_start_timestep - 1} Train, {self.val_start_timestep}-{self.split_timestep} Val, {self.split_timestep + 1}-49 Test",
            "models": [
                {
                    "paradigm": "Inductive GraphSAGE (2-Layer Mean Aggregator)",
                    "category": "GRAPH_INTELLIGENCE_PRIMARY",
                    "pr_auc": sage2_metrics["pr_auc"],
                    "roc_auc": sage2_metrics["roc_auc"],
                    "recall_at_01_fpr": sage2_metrics["recall_at_01_fpr"],
                    "recall_at_05_fpr": sage2_metrics["recall_at_05_fpr"],
                    "recall_at_10_fpr": sage2_metrics["recall_at_10_fpr"],
                    "f1_score": sage2_metrics["f1_score"],
                    "brier_score": sage2_metrics["brier_score"],
                    "latency_ms": sage2_metrics["inference_latency_per_1k_ms"],
                    "delta_pr_auc_vs_tabular": uplift["delta_pr_auc_vs_tabular"],
                    "description": "2-layer neighborhood aggregation capturing 2-hop transaction money flows",
                },
                {
                    "paradigm": "Inductive GraphSAGE (1-Layer Mean Aggregator)",
                    "category": "GRAPH_INTELLIGENCE_1HOP",
                    "pr_auc": sage1_metrics["pr_auc"],
                    "roc_auc": sage1_metrics["roc_auc"],
                    "recall_at_01_fpr": sage1_metrics["recall_at_01_fpr"],
                    "recall_at_05_fpr": sage1_metrics["recall_at_05_fpr"],
                    "recall_at_10_fpr": sage1_metrics["recall_at_10_fpr"],
                    "f1_score": sage1_metrics["f1_score"],
                    "brier_score": sage1_metrics["brier_score"],
                    "latency_ms": sage1_metrics["inference_latency_per_1k_ms"],
                    "delta_pr_auc_vs_tabular": round(sage1_metrics["pr_auc"] - mlp_metrics["pr_auc"], 6),
                    "description": "1-layer immediate neighborhood aggregation (direct counterparties only)",
                },
                {
                    "paradigm": "Tabular MLP Baseline (No Graph / 0-Hop)",
                    "category": "TABULAR_BASELINE",
                    "pr_auc": mlp_metrics["pr_auc"],
                    "roc_auc": mlp_metrics["roc_auc"],
                    "recall_at_01_fpr": mlp_metrics["recall_at_01_fpr"],
                    "recall_at_05_fpr": mlp_metrics["recall_at_05_fpr"],
                    "recall_at_10_fpr": mlp_metrics["recall_at_10_fpr"],
                    "f1_score": mlp_metrics["f1_score"],
                    "brier_score": mlp_metrics["brier_score"],
                    "latency_ms": mlp_metrics["inference_latency_per_1k_ms"],
                    "delta_pr_auc_vs_tabular": 0.0,
                    "description": "Standard neural network evaluated strictly on node features without topology",
                },
                {
                    "paradigm": "GraphSAGE (GCN Symmetric Aggregator)",
                    "category": "GRAPH_ABLATION",
                    "pr_auc": gcn_metrics["pr_auc"],
                    "roc_auc": gcn_metrics["roc_auc"],
                    "recall_at_01_fpr": gcn_metrics["recall_at_01_fpr"],
                    "recall_at_05_fpr": gcn_metrics["recall_at_05_fpr"],
                    "recall_at_10_fpr": gcn_metrics["recall_at_10_fpr"],
                    "f1_score": gcn_metrics["f1_score"],
                    "brier_score": gcn_metrics["brier_score"],
                    "latency_ms": gcn_metrics["inference_latency_per_1k_ms"],
                    "delta_pr_auc_vs_tabular": round(gcn_metrics["pr_auc"] - mlp_metrics["pr_auc"], 6),
                    "description": "Symmetric normalized adjacency aggregator (Kipf & Welling variant)",
                },
            ],
            "neighborhood_aggregation_uplift": uplift,
            "temporal_generalization_breakdown": temporal_breakdown,
        }

        # -----------------------------------------------------------------------
        # Construct Standard ExperimentResult Schema
        # -----------------------------------------------------------------------
        git_commit = self._get_git_commit()
        hardware_meta = HardwareMetadata.capture()

        dataset_sha256 = (
            data["file_hashes"].get("elliptic_cache.parquet")
            or data["file_hashes"].get("elliptic_txs_features.csv")
            or hashlib.sha256(b"elliptic_dataset_v1").hexdigest()
        )

        dataset_meta = DatasetMetadata(
            dataset_name="Elliptic Bitcoin Transaction Graph",
            source_uri="backend/storage/datasets/elliptic",
            sha256_hash=dataset_sha256,
            total_samples=num_nodes,
            num_features=in_dim,
            fraud_samples=int(np.sum(y == 1)),
            fraud_rate=round(float(np.mean(y[y != -1] == 1)), 6),
            split_ratios={
                "train_nodes": round(float(np.mean(train_mask)), 4),
                "val_nodes": round(float(np.mean(val_mask)), 4),
                "test_nodes": round(float(np.mean(test_mask)), 4),
            },
        )

        exp_config = ExperimentConfig(
            experiment_id=f"exp-elliptic-graphsage-{int(time.time())}",
            experiment_name="Elliptic Bitcoin GraphSAGE Inductive Benchmark",
            description="Canonical multi-seed benchmark evaluating GraphSAGE on physical Elliptic Bitcoin graph with strict temporal validation.",
            tags=["graphsage", "elliptic", "gnn", "graph-intelligence", "bitcoin", "multi-seed"],
            model_type="GraphSAGE",
            strategy="InductiveNeighborhoodAggregation",
            seeds=self.seeds,
            num_rounds=epochs,
            learning_rate=lr,
            hyperparameters={
                "in_dim": in_dim,
                "hidden_dim": hidden_dim,
                "embedding_dim": embedding_dim,
                "split_timestep": self.split_timestep,
                "val_start_timestep": self.val_start_timestep,
                "bidirectional_flow": True,
                "loss": "BinaryCrossEntropy (pos_weight)",
                "checkpoint_criterion": "validation_pr_auc",
                "threshold_criterion": "validation_f1_maximization",
            },
            output_dir=str(target_dir),
        )

        # Build CurvePoints
        y_test_curve = y[test_mask]
        preds_curve = sage2_all_preds[test_mask]
        has_both_curve = (np.sum(y_test_curve == 1) > 0) and (np.sum(y_test_curve == 0) > 0)
        if has_both_curve:
            fpr_pts, tpr_pts, _ = roc_curve(y_test_curve, preds_curve)
            prec_pts, rec_pts, _ = precision_recall_curve(y_test_curve, preds_curve)
            indices_sub = np.linspace(0, len(fpr_pts) - 1, min(50, len(fpr_pts)), dtype=int)
            curve_data = CurvePoint(
                fpr=[round(float(fpr_pts[i]), 5) for i in indices_sub],
                tpr=[round(float(tpr_pts[i]), 5) for i in indices_sub],
                precision=[round(float(prec_pts[min(i, len(prec_pts) - 1)]), 5) for i in indices_sub],
                recall=[round(float(rec_pts[min(i, len(rec_pts) - 1)]), 5) for i in indices_sub],
            )
        else:
            curve_data = CurvePoint(
                fpr=[0.0, 1.0],
                tpr=[0.0, 1.0],
                precision=[0.5, 0.5],
                recall=[0.0, 1.0],
            )

        cm_dict = sage2_metrics.get("confusion_matrix", {"tn": 0, "fp": 0, "fn": 0, "tp": 0})
        cm_data = ConfusionMatrixData(
            tn=cm_dict.get("tn", 0),
            fp=cm_dict.get("fp", 0),
            fn=cm_dict.get("fn", 0),
            tp=cm_dict.get("tp", 0),
        )

        history_steps = [
            StepMetric(
                step=ep + 1,
                train_loss=round(sage2_losses[ep], 5),
                duration_seconds=round(duration_sec / max(1, epochs), 3),
            )
            for ep in range(len(sage2_losses))
        ]

        exp_result = ExperimentResult(
            experiment_id=exp_config.experiment_id,
            config=exp_config,
            hardware=hardware_meta,
            dataset=dataset_meta,
            git_commit=git_commit,
            start_time_utc=start_time_utc,
            end_time_utc=end_time_utc,
            total_duration_seconds=duration_sec,
            final_metrics={
                "pr_auc": aggregate_metrics["mean"]["pr_auc"],
                "roc_auc": aggregate_metrics["mean"]["roc_auc"],
                "precision": aggregate_metrics["mean"]["precision"],
                "recall": aggregate_metrics["mean"]["recall"],
                "f1_score": aggregate_metrics["mean"]["f1_score"],
                "recall_at_01_fpr": aggregate_metrics["mean"]["recall_at_01_fpr"],
                "threshold": aggregate_metrics["mean"]["threshold"],
                "pr_auc_std": aggregate_metrics["std"]["pr_auc"],
                "roc_auc_std": aggregate_metrics["std"]["roc_auc"],
                "inference_latency_per_1k_ms": sage2_metrics["inference_latency_per_1k_ms"],
            },
            history=history_steps,
            curves=curve_data,
            confusion_matrix=cm_data,
            artifact_paths={
                "results_json": str(target_dir / "results.json"),
                "comparative_baselines": str(target_dir / "comparative_baselines.json"),
                "audit_dossier": str(target_dir / "audit_dossier.md"),
                "benchmark_raw": str(target_dir / "graphsage_elliptic_benchmark.json"),
                **plot_paths,
            },
        )

        # -----------------------------------------------------------------------
        # Write Output Artifacts
        # -----------------------------------------------------------------------
        results_json_path = target_dir / "results.json"
        with open(results_json_path, "w", encoding="utf-8") as f:
            f.write(exp_result.model_dump_json(indent=2))

        baselines_json_path = target_dir / "comparative_baselines.json"
        with open(baselines_json_path, "w", encoding="utf-8") as f:
            json.dump(comparative_baselines, f, indent=2)

        # Build comprehensive canonical benchmark raw payload
        raw_payload = {
            "timestamp_utc": end_time_utc,
            "benchmark": "GraphSAGE Elliptic Bitcoin Inductive Node Classification",
            "model": "GraphSAGE (2-layer Mean Aggregator)",
            "dataset": "Elliptic Bitcoin Transaction Graph (Real)",
            "dataset_mode": self.dataset_mode,
            "git_commit": git_commit,
            "dataset_metadata": {
                "name": "Elliptic Bitcoin Transaction Graph (Weber et al., 2019)",
                "source_uri": "backend/storage/datasets/elliptic",
                "total_nodes": num_nodes,
                "total_edges": int(edge_index.shape[1]),
                "total_features": in_dim,
                "total_labeled_nodes": data["n_train_labeled"] + data["n_val_labeled"] + data["n_test_labeled"],
                "total_illicit_nodes": data["n_train_illicit"] + data["n_val_illicit"] + data["n_test_illicit"],
                "total_licit_nodes": data["n_train_licit"] + data["n_val_licit"] + data["n_test_licit"],
                "total_unknown_nodes": num_nodes - (data["n_train_labeled"] + data["n_val_labeled"] + data["n_test_labeled"]),
                "sha256_hashes": data["file_hashes"],
            },
            "temporal_split": {
                "split_policy": "Strict Out-of-Time Past-to-Future Temporal Partitioning",
                "train_timesteps": f"1-{self.val_start_timestep - 1}",
                "val_timesteps": f"{self.val_start_timestep}-{self.split_timestep}",
                "test_timesteps": f"{self.split_timestep + 1}-49",
                "train_labeled_nodes": data["n_train_labeled"],
                "train_illicit_nodes": data["n_train_illicit"],
                "train_licit_nodes": data["n_train_licit"],
                "val_labeled_nodes": data["n_val_labeled"],
                "val_illicit_nodes": data["n_val_illicit"],
                "val_licit_nodes": data["n_val_licit"],
                "test_labeled_nodes": data["n_test_labeled"],
                "test_illicit_nodes": data["n_test_illicit"],
                "test_licit_nodes": data["n_test_licit"],
                "zero_future_leakage_enforced": True,
            },
            "validation_methodology": {
                "checkpoint_selection_criterion": "validation_pr_auc_maximization",
                "operating_threshold_selection_criterion": "validation_f1_maximization",
                "tie_breaking": "lowest_threshold",
                "test_set_isolation": "final_test_labels_never_used_for_checkpoint_or_threshold_selection",
            },
            "seeds": self.seeds,
            "metrics": aggregate_metrics["mean"],
            "metrics_std": aggregate_metrics["std"],
            "metrics_min": aggregate_metrics["min"],
            "metrics_max": aggregate_metrics["max"],
            "per_seed_results": [
                {
                    "seed": r["seed"],
                    "best_epoch": r["best_epoch"],
                    "val_pr_auc": round(r["val_pr_auc"], 4) if r["val_pr_auc"] is not None else None,
                    "operating_threshold": r["threshold"],
                    "test_pr_auc": r["metrics"]["pr_auc"],
                    "test_roc_auc": r["metrics"]["roc_auc"],
                    "precision": r["metrics"]["precision"],
                    "recall": r["metrics"]["recall"],
                    "f1_score": r["metrics"]["f1_score"],
                    "recall_at_01_fpr": r["metrics"]["recall_at_01_fpr"],
                    "confusion_matrix": r["metrics"]["confusion_matrix"],
                    "non_canonical_05": r["metrics"]["non_canonical_05"],
                }
                for r in seed_results
            ],
            "non_canonical_threshold_05": {
                "description": "Secondary diagnostic only; arbitrary 0.5 threshold evaluated without validation calibration",
                "precision": round(float(np.mean([r["metrics"]["non_canonical_05"]["precision"] for r in seed_results])), 4),
                "recall": round(float(np.mean([r["metrics"]["non_canonical_05"]["recall"] for r in seed_results])), 4),
                "f1_score": round(float(np.mean([r["metrics"]["non_canonical_05"]["f1_score"] for r in seed_results])), 4),
            },
            "graphsage_2layer_champion": sage2_metrics,
            "graphsage_1layer_ablation": sage1_metrics,
            "tabular_mlp_baseline": mlp_metrics,
            "uplift": uplift,
            "legacy_synthetic_benchmark": {
                "status": "RETIRED_SYNTHETIC_SMOKE_BENCHMARK",
                "pr_auc": 0.9001,
                "roc_auc": 0.9860,
                "precision": 0.9636,
                "recall": 0.3333,
                "f1_score": 0.4953,
                "note": "Measured on synthetic 1500-node smoke fallback testbed in run_graph_benchmark.py; superseded by canonical real-data multi-seed benchmark.",
            },
            "legacy_real_data_single_run": {
                "status": "HISTORICAL_CENTRALIZED_SINGLE_RUN_BASELINE",
                "pr_auc": 0.4372,
                "roc_auc": 0.8388,
                "precision": 0.2711,
                "recall": 0.6371,
                "note": "Initial single-seed run without pre-test validation split; superseded by canonical 3-seed temporal validation benchmark.",
            },
        }

        # Write output artifacts:
        # Both repository-owned canonical scientific artifacts:
        #   1. benchmarks/results/raw/graphsage_elliptic_benchmark.json
        #   2. experiments/elliptic/graphsage_elliptic_benchmark.json
        # have equivalent fail-closed protection and are ONLY written when is_canonical_real_run is True.
        root_raw_path = raw_results_dir / "graphsage_elliptic_benchmark.json"
        canonical_exp_raw_path = REPO_ROOT / "experiments" / "elliptic" / "graphsage_elliptic_benchmark.json"
        target_raw_path = target_dir / "graphsage_elliptic_benchmark.json"

        if is_canonical_real_run:
            with open(canonical_exp_raw_path, "w", encoding="utf-8") as f:
                json.dump(raw_payload, f, indent=2)
            with open(root_raw_path, "w", encoding="utf-8") as f:
                json.dump(raw_payload, f, indent=2)
            canonical_path_str = str(root_raw_path)
            logger.info("Successfully serialized canonical benchmark artifacts to: %s and %s", canonical_exp_raw_path, root_raw_path)
        else:
            with open(target_raw_path, "w", encoding="utf-8") as f:
                json.dump(raw_payload, f, indent=2)
            canonical_path_str = str(target_raw_path)
            logger.info("Successfully serialized isolated benchmark artifacts to: %s", target_dir)

        audit_dossier_path = target_dir / "audit_dossier.md"
        self._write_audit_dossier(audit_dossier_path, comparative_baselines, exp_result)

        return {
            "status": "COMPLETED",
            "duration_seconds": duration_sec,
            "graphsage_metrics": sage2_metrics,
            "aggregate_metrics": aggregate_metrics,
            "tabular_metrics": mlp_metrics,
            "uplift": uplift,
            "paths": {
                "results_json": str(results_json_path),
                "comparative_baselines": str(baselines_json_path),
                "audit_dossier": str(audit_dossier_path),
                "raw_benchmark": canonical_path_str,
                **plot_paths,
            },
        }

    def _generate_publication_plots(
        self,
        y_test: np.ndarray,
        mlp_preds: np.ndarray,
        sage1_preds: np.ndarray,
        sage2_preds: np.ndarray,
        uplift: dict[str, Any],
        temporal_breakdown: list[dict[str, Any]],
        plots_dir: Path,
        docs_figures_dir: Path | None = None,
        is_canonical_real_run: bool = False,
    ) -> dict[str, str]:
        """Generate publication-ready figures for precision-recall, ROC, and controlled ablations."""
        setup_publication_style()
        paths: dict[str, str] = {}

        # 1. Precision-Recall Curves
        fig, ax = plt.subplots(figsize=(6, 5))
        has_both = (np.sum(y_test == 1) > 0) and (np.sum(y_test == 0) > 0)
        if has_both:
            prec_mlp, rec_mlp, _ = precision_recall_curve(y_test, mlp_preds)
            prec_s1, rec_s1, _ = precision_recall_curve(y_test, sage1_preds)
            prec_s2, rec_s2, _ = precision_recall_curve(y_test, sage2_preds)
            ap_mlp = float(average_precision_score(y_test, mlp_preds))
            ap_s1 = float(average_precision_score(y_test, sage1_preds))
            ap_s2 = float(average_precision_score(y_test, sage2_preds))
            fpr_mlp, tpr_mlp, _ = roc_curve(y_test, mlp_preds)
            fpr_s2, tpr_s2, _ = roc_curve(y_test, sage2_preds)
            roc_mlp = float(roc_auc_score(y_test, mlp_preds))
            roc_s2 = float(roc_auc_score(y_test, sage2_preds))
        else:
            rec_s2 = rec_s1 = rec_mlp = np.array([0.0, 1.0])
            prec_s2 = prec_s1 = prec_mlp = np.array([0.5, 0.5])
            fpr_s2 = fpr_mlp = np.array([0.0, 1.0])
            tpr_s2 = tpr_mlp = np.array([0.0, 1.0])
            ap_mlp = ap_s1 = ap_s2 = 0.5
            roc_mlp = roc_s2 = 0.5

        ax.plot(rec_s2, prec_s2, label=f"GraphSAGE 2-Layer (PR-AUC={ap_s2:.4f})", color="#2563eb", lw=2)
        ax.plot(rec_s1, prec_s1, label=f"GraphSAGE 1-Layer (PR-AUC={ap_s1:.4f})", color="#0891b2", lw=1.8, ls="--")
        ax.plot(rec_mlp, prec_mlp, label=f"Tabular MLP (PR-AUC={ap_mlp:.4f})", color="#dc2626", lw=1.8, ls=":")

        ax.set_title("Precision-Recall Curve (Elliptic Illicit Nodes)")
        ax.set_xlabel("Recall (True Positive Rate)")
        ax.set_ylabel("Precision")
        ax.set_xlim(0.0, 1.0)
        ax.set_ylim(0.0, 1.05)
        ax.legend(loc="upper right", frameon=True)
        pr_path = plots_dir / "pr_curves.png"
        fig.savefig(pr_path)
        plt.close(fig)
        paths["pr_curves"] = str(pr_path)

        # 2. ROC Curves
        fig, ax = plt.subplots(figsize=(6, 5))
        ax.plot(fpr_s2, tpr_s2, label=f"GraphSAGE 2-Layer (ROC-AUC={roc_s2:.4f})", color="#2563eb", lw=2)
        ax.plot(fpr_mlp, tpr_mlp, label=f"Tabular MLP (ROC-AUC={roc_mlp:.4f})", color="#dc2626", lw=1.8, ls=":")
        ax.plot([0, 1], [0, 1], color="#9ca3af", lw=1, ls="--", label="Random Chance (0.50)")

        ax.set_title("Receiver Operating Characteristic (ROC)")
        ax.set_xlabel("False Positive Rate (FPR)")
        ax.set_ylabel("True Positive Rate (Recall)")
        ax.set_xlim(0.0, 1.0)
        ax.set_ylim(0.0, 1.05)
        ax.legend(loc="lower right", frameon=True)
        roc_path = plots_dir / "roc_curves.png"
        fig.savefig(roc_path)
        plt.close(fig)
        paths["roc_curves"] = str(roc_path)

        # 3. Controlled Hop Ablation Bar Plot
        fig, ax = plt.subplots(figsize=(7, 5))
        models = ["0-Hop (Tabular)", "1-Hop GraphSAGE", "2-Hop GraphSAGE"]
        pr_scores = [
            uplift["hop_ablation"]["0_hop_tabular_mlp"]["pr_auc"],
            uplift["hop_ablation"]["1_hop_graphsage"]["pr_auc"],
            uplift["hop_ablation"]["2_hop_graphsage"]["pr_auc"],
        ]
        roc_scores = [
            uplift["hop_ablation"]["0_hop_tabular_mlp"]["roc_auc"],
            uplift["hop_ablation"]["1_hop_graphsage"]["roc_auc"],
            uplift["hop_ablation"]["2_hop_graphsage"]["roc_auc"],
        ]
        rec_01_scores = [
            uplift["hop_ablation"]["0_hop_tabular_mlp"]["recall_at_01_fpr"],
            uplift["hop_ablation"]["1_hop_graphsage"]["recall_at_01_fpr"],
            uplift["hop_ablation"]["2_hop_graphsage"]["recall_at_01_fpr"],
        ]

        x_indices = np.arange(len(models))
        bar_width = 0.25
        ax.bar(x_indices - bar_width, pr_scores, width=bar_width, label="PR-AUC", color="#2563eb")
        ax.bar(x_indices, roc_scores, width=bar_width, label="ROC-AUC", color="#0891b2")
        ax.bar(x_indices + bar_width, rec_01_scores, width=bar_width, label="Recall @ 0.1% FPR", color="#10b981")

        ax.set_title("Controlled Neighborhood Hop Ablation")
        ax.set_xticks(x_indices)
        ax.set_xticklabels(models)
        ax.set_ylabel("Metric Value")
        ax.set_ylim(0.0, 1.05)
        ax.legend(loc="upper right", frameon=True)
        ablation_path = plots_dir / "neighborhood_ablation.png"
        fig.savefig(ablation_path)
        plt.close(fig)
        paths["neighborhood_ablation"] = str(ablation_path)

        # 4. Temporal Generalization Plot across Timesteps 35 to 49
        fig, ax = plt.subplots(figsize=(8, 4.5))
        t_steps: list[int] = []
        sage_prs: list[float] = []
        mlp_prs: list[float] = []
        if temporal_breakdown:
            t_steps = [item["timestep"] for item in temporal_breakdown]
            sage_prs = [item["graphsage_pr_auc"] for item in temporal_breakdown]
            mlp_prs = [item["tabular_mlp_pr_auc"] for item in temporal_breakdown]

            ax.plot(t_steps, sage_prs, marker="o", label="GraphSAGE 2-Layer", color="#2563eb", lw=2)
            ax.plot(t_steps, mlp_prs, marker="s", label="Tabular MLP", color="#dc2626", lw=1.8, ls="--")
            ax.set_title("Temporal Generalization Stability (Test Timesteps 35-49)")
            ax.set_xlabel("Bitcoin Transaction Timestep (~2-week windows)")
            ax.set_ylabel("PR-AUC (Illicit Transactions)")
            ax.set_ylim(0.0, 1.05)
            ax.legend(loc="upper right", frameon=True)

        temporal_path = plots_dir / "temporal_generalization.png"
        fig.savefig(temporal_path)
        plt.close(fig)
        paths["temporal_generalization"] = str(temporal_path)

        # 5. Consolidated 2x2 Publication Figure
        fig, axes = plt.subplots(2, 2, figsize=(14, 10))
        # (0, 0) PR Curves
        axes[0, 0].plot(rec_s2, prec_s2, label=f"GraphSAGE 2-Layer ({ap_s2:.4f})", color="#2563eb", lw=2)
        axes[0, 0].plot(rec_s1, prec_s1, label=f"GraphSAGE 1-Layer ({ap_s1:.4f})", color="#0891b2", lw=1.8, ls="--")
        axes[0, 0].plot(rec_mlp, prec_mlp, label=f"Tabular MLP ({ap_mlp:.4f})", color="#dc2626", lw=1.8, ls=":")
        axes[0, 0].set_title("A. Precision-Recall Curves")
        axes[0, 0].set_xlabel("Recall")
        axes[0, 0].set_ylabel("Precision")
        axes[0, 0].legend(loc="upper right")

        # (0, 1) ROC Curves
        axes[0, 1].plot(fpr_s2, tpr_s2, label=f"GraphSAGE 2-Layer ({roc_s2:.4f})", color="#2563eb", lw=2)
        axes[0, 1].plot(fpr_mlp, tpr_mlp, label=f"Tabular MLP ({roc_mlp:.4f})", color="#dc2626", lw=1.8, ls=":")
        axes[0, 1].plot([0, 1], [0, 1], color="#9ca3af", lw=1, ls="--")
        axes[0, 1].set_title("B. Receiver Operating Characteristic")
        axes[0, 1].set_xlabel("False Positive Rate")
        axes[0, 1].set_ylabel("True Positive Rate")
        axes[0, 1].legend(loc="lower right")

        # (1, 0) Hop Ablation Bar Chart
        axes[1, 0].bar(x_indices - bar_width, pr_scores, width=bar_width, label="PR-AUC", color="#2563eb")
        axes[1, 0].bar(x_indices, roc_scores, width=bar_width, label="ROC-AUC", color="#0891b2")
        axes[1, 0].bar(x_indices + bar_width, rec_01_scores, width=bar_width, label="Recall @ 0.1% FPR", color="#10b981")
        axes[1, 0].set_title("C. Neighborhood Hop Ablation (0 vs 1 vs 2 Hops)")
        axes[1, 0].set_xticks(x_indices)
        axes[1, 0].set_xticklabels(models)
        axes[1, 0].legend(loc="upper right")

        # (1, 1) Temporal Generalization
        if temporal_breakdown:
            axes[1, 1].plot(t_steps, sage_prs, marker="o", label="GraphSAGE 2-Layer", color="#2563eb", lw=2)
            axes[1, 1].plot(t_steps, mlp_prs, marker="s", label="Tabular MLP", color="#dc2626", lw=1.8, ls="--")
            axes[1, 1].set_title("D. Temporal Generalization (Timesteps 35-49)")
            axes[1, 1].set_xlabel("Timestep (2-week windows)")
            axes[1, 1].set_ylabel("PR-AUC")
            axes[1, 1].legend(loc="upper right")

        fig.tight_layout()
        consolidated_path = plots_dir / "benchmark_graphsage_elliptic.png"
        fig.savefig(consolidated_path)
        paths["consolidated_figure"] = str(consolidated_path)

        if is_canonical_real_run and docs_figures_dir is not None:
            docs_figures_dir.mkdir(parents=True, exist_ok=True)
            doc_pub_path = docs_figures_dir / "benchmark_graphsage_elliptic.png"
            fig.savefig(doc_pub_path)
            paths["docs_figure"] = str(doc_pub_path)

        plt.close(fig)
        return paths

    def _write_audit_dossier(
        self,
        path: Path,
        baselines: dict[str, Any],
        exp_result: ExperimentResult,
    ) -> None:
        """Write publication-grade markdown audit dossier."""
        models = baselines["models"]
        uplift = baselines["neighborhood_aggregation_uplift"]
        hw = exp_result.hardware
        ds = exp_result.dataset

        lines = [
            "# Elliptic Bitcoin GraphSAGE Inductive Benchmark Audit Dossier",
            "",
            "> **Dataset**: Elliptic Bitcoin Transaction Graph (Weber et al., 2019)  ",
            f"> **Execution Timestamp**: `{exp_result.end_time_utc}`  ",
            f"> **Git Commit**: `{exp_result.git_commit}`  ",
            "> **Temporal Invariant**: Strict chronological split (Timesteps 1-30 Train, 31-34 Val, 35-49 Test)  ",
            f"> **Hardware**: {hw.cpu_model} ({hw.cpu_logical_cores} vCPUs), {hw.total_ram_gb:.1f} GB RAM, {platform.system()} {platform.release()}  ",
            "",
            "---",
            "",
            "## 1. Executive Summary & Comparative Matrix",
            "",
            "| Evaluation Paradigm | PR-AUC | ROC-AUC | Recall @ 0.1% FPR | Recall @ 0.5% FPR | F1-Score | Brier Score | Latency (ms/1k) | $\\Delta$ PR-AUC vs Tabular |",
            "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
        ]

        for m in models:
            delta_str = f"{m['delta_pr_auc_vs_tabular']:+.4f}" if m["delta_pr_auc_vs_tabular"] != 0.0 else "Baseline (0.0)"
            lines.append(
                f"| **{m['paradigm']}** | `{m['pr_auc']:.4f}` | `{m['roc_auc']:.4f}` | "
                f"`{m['recall_at_01_fpr']:.4f}` | `{m['recall_at_05_fpr']:.4f}` | `{m['f1_score']:.4f}` | "
                f"`{m['brier_score']:.4f}` | `{m['latency_ms']:.2f}` ms | **{delta_str}** |"
            )

        lines.extend([
            "",
            "---",
            "",
            "## 2. Exploratory Neighborhood Hop Ablation (Single-Seed Diagnostic)",
            "",
            "In exploratory single-seed ablations, 2-layer GraphSAGE was evaluated alongside 1-layer and tabular configurations under identical temporal partitions.",
            "This comparison does not establish an empirical causal mechanism for performance variations across topologies and is not used for canonical model selection:",
            "",
            "```",
            f"0-Hop (Tabular MLP Baseline)       PR-AUC: {uplift['hop_ablation']['0_hop_tabular_mlp']['pr_auc']:.4f}  |  ROC-AUC: {uplift['hop_ablation']['0_hop_tabular_mlp']['roc_auc']:.4f}  |  Recall@0.1%FPR: {uplift['hop_ablation']['0_hop_tabular_mlp']['recall_at_01_fpr']:.4f}",
            f"1-Hop (GraphSAGE Immediate Neigh)   PR-AUC: {uplift['hop_ablation']['1_hop_graphsage']['pr_auc']:.4f}  |  ROC-AUC: {uplift['hop_ablation']['1_hop_graphsage']['roc_auc']:.4f}  |  Recall@0.1%FPR: {uplift['hop_ablation']['1_hop_graphsage']['recall_at_01_fpr']:.4f}",
            f"2-Hop (GraphSAGE Full Champion)    PR-AUC: {uplift['hop_ablation']['2_hop_graphsage']['pr_auc']:.4f}  |  ROC-AUC: {uplift['hop_ablation']['2_hop_graphsage']['roc_auc']:.4f}  |  Recall@0.1%FPR: {uplift['hop_ablation']['2_hop_graphsage']['recall_at_01_fpr']:.4f}",
            "```",
            "",
            r"- **Neighborhood Aggregation Uplift ($\Delta$ PR-AUC)**: `"
            + f"{uplift['delta_pr_auc_vs_tabular']:+.4f}`",
            r"- **Discriminative Separation Uplift ($\Delta$ ROC-AUC)**: `"
            + f"{uplift['delta_roc_auc_vs_tabular']:+.4f}`",
            r"- **Low-FPR Operational Safety Uplift ($\Delta$ Recall @ 0.1% FPR)**: `"
            + f"{uplift['delta_recall_01_fpr_vs_tabular']:+.4f}`",
            "",
            "### Aggregator Function Invariants",
            "",
            f"- **Mean Aggregator**: PR-AUC `{uplift['aggregator_ablation']['mean_aggregator']['pr_auc']:.4f}`, ROC-AUC `{uplift['aggregator_ablation']['mean_aggregator']['roc_auc']:.4f}`.",
            f"- **GCN Symmetric Aggregator**: PR-AUC `{uplift['aggregator_ablation']['gcn_aggregator']['pr_auc']:.4f}`, ROC-AUC `{uplift['aggregator_ablation']['gcn_aggregator']['roc_auc']:.4f}`.",
            "",
            "---",
            "",
            "## 3. Dataset Characteristics & Partition Invariants",
            "",
            f"- **Total Nodes**: {ds.total_samples:,} Bitcoin transactions",
            f"- **Feature Dimension**: {ds.num_features} dimensions (local + precomputed flow aggregates)",
            f"- **Train Partition (Timesteps 1-34)**: {exp_result.config.hyperparameters.get('split_timestep', 34)} timesteps, strict temporal ceiling",
            "- **Test Partition (Timesteps 35-49)**: 15 out-of-time test timesteps, evaluating inductive generalizability",
            "",
            "---",
            "",
            "## 4. Empirical Confusion Matrix (GraphSAGE 2-Layer)",
            "",
            f"- **True Negatives (TN)**: `{(exp_result.confusion_matrix.tn if exp_result.confusion_matrix else 0):,}` (Correctly identified licit Bitcoin transactions)",
            f"- **False Positives (FP)**: `{(exp_result.confusion_matrix.fp if exp_result.confusion_matrix else 0):,}` (Benign transactions flagged as suspicious)",
            f"- **False Negatives (FN)**: `{(exp_result.confusion_matrix.fn if exp_result.confusion_matrix else 0):,}` (Missed illicit transactions)",
            f"- **True Positives (TP)**: `{(exp_result.confusion_matrix.tp if exp_result.confusion_matrix else 0):,}` (Successfully intercepted money laundering transactions)",
            "",
            "---",
            "",
            "## 5. Scientific Artifact Traceability",
            "",
            "- **Result Payload**: [`results.json`](results.json)",
            "- **Comparative Baselines**: [`comparative_baselines.json`](comparative_baselines.json)",
            "- **Raw Benchmark**: [`graphsage_elliptic_benchmark.json`](graphsage_elliptic_benchmark.json)",
            "- **Consolidated Figure**: [`benchmark_graphsage_elliptic.png`](plots/benchmark_graphsage_elliptic.png)",
            "",
        ])

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def _get_git_commit(self) -> str:
        """Extract current git commit hash."""
        try:
            res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            return res.stdout.strip()
        except Exception:
            return "unknown"


def run_graphsage_benchmark(
    seed: int = 42,
    seeds: Sequence[int] | None = None,
    epochs: int = 15,
    lr: float = 0.005,
    hidden_dim: int = 128,
    embedding_dim: int = 64,
    require_real: bool | None = None,
    dataset_mode: str = "real",
    all_rows: bool = True,
    nrows: int | None = None,
    output_dir: Path | str | None = None,
    is_canonical: bool = False,
) -> dict[str, Any]:
    """Top-level entry point to execute the Elliptic GraphSAGE benchmark.

    Args:
        seed: Default random seed if seeds is None.
        seeds: Sequence of seeds for multi-seed statistical evaluation.
        epochs: Number of training epochs per seed.
        lr: Learning rate.
        hidden_dim: Dimension of first GraphSAGE layer.
        embedding_dim: Dimension of second GraphSAGE layer.
        require_real: Whether to require real dataset files (fail closed if missing).
        dataset_mode: 'real' (fail closed if missing) or 'synthetic' (smoke tests).
        all_rows: Whether to load entire graph (203k nodes).
        nrows: Subsampled rows if not all_rows.
        output_dir: Output directory for benchmark artifacts.

    Returns:
        Dictionary containing benchmark status, metrics, and artifact paths.
    """
    if require_real is False and dataset_mode == "real":
        dataset_mode = "synthetic"
    if require_real is None:
        require_real = (dataset_mode == "real")
    eval_seeds = list(seeds) if seeds is not None else [seed]
    bench = EllipticGraphSAGEBenchmark(
        seed=eval_seeds[0],
        seeds=eval_seeds,
        require_real=require_real,
        dataset_mode=dataset_mode,
        all_rows=all_rows,
        nrows=nrows,
        is_canonical=is_canonical,
    )
    return bench.run_benchmark(
        epochs=epochs,
        lr=lr,
        hidden_dim=hidden_dim,
        embedding_dim=embedding_dim,
        output_dir=output_dir,
        is_canonical=is_canonical,
    )


def run_canonical_graphsage_benchmark(**kwargs: Any) -> dict[str, Any]:
    """Authoritative canonical benchmark runner executing the immutable canonical protocol.

    Rejects any parameter overrides that deviate from the accepted canonical scientific configuration.
    """
    for param, canon_val in CANONICAL_CONFIG.items():
        if param in kwargs:
            val = kwargs[param]
            if param == "seeds":
                if tuple(val) != canon_val:
                    raise ValueError(
                        f"Canonical runner rejects parameter override for '{param}': "
                        f"expected {list(canon_val)}, received {list(val)}"
                    )
            elif param == "lr":
                if abs(float(val) - float(canon_val)) > 1e-9:
                    raise ValueError(
                        f"Canonical runner rejects parameter override for '{param}': "
                        f"expected {canon_val}, received {val}"
                    )
            elif val != canon_val:
                raise ValueError(
                    f"Canonical runner rejects parameter override for '{param}': "
                    f"expected {canon_val}, received {val}"
                )

    if "output_dir" in kwargs and kwargs["output_dir"] is not None:
        raise ValueError(
            f"Canonical runner rejects non-None output_dir: received '{kwargs['output_dir']}'"
        )
    if "nrows" in kwargs and kwargs["nrows"] is not None:
        raise ValueError(
            f"Canonical runner rejects nrows subsampling: received {kwargs['nrows']}"
        )

    canonical_kwargs: dict[str, Any] = {
        "dataset_mode": CANONICAL_CONFIG["dataset_mode"],
        "require_real": CANONICAL_CONFIG["require_real"],
        "all_rows": CANONICAL_CONFIG["all_rows"],
        "seeds": list(CANONICAL_CONFIG["seeds"]),
        "epochs": CANONICAL_CONFIG["epochs"],
        "lr": CANONICAL_CONFIG["lr"],
        "hidden_dim": CANONICAL_CONFIG["hidden_dim"],
        "embedding_dim": CANONICAL_CONFIG["embedding_dim"],
        "is_canonical": True,
        "output_dir": None,
    }
    for k, v in kwargs.items():
        if k not in canonical_kwargs:
            canonical_kwargs[k] = v

    return run_graphsage_benchmark(**canonical_kwargs)

