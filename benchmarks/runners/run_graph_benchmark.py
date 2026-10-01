"""GraphSAGE Graph Intelligence Canonical Benchmark Runner.

Authoritative execution path for inductive node classification on financial transaction graphs:
- Physical real Elliptic Bitcoin Dataset (MIT-IBM Watson / Elliptic, 203k nodes, 234k edges)
- Strict out-of-time past-to-future temporal split (train: 1-30, val: 31-34, test: 35-49)
- Multi-seed statistical evaluation (seeds 42, 123, 456) with sample standard deviation (ddof=1)
- Validation-only checkpoint selection (PR-AUC maximization)
- Validation-only operating threshold calibration (F1 maximization)
- Ranking metrics (PR-AUC, ROC-AUC) evaluated on continuous test prediction scores
- Operating metrics (Precision, Recall, F1) evaluated on frozen validation threshold

Fails closed: In canonical 'real' mode, missing real dataset files raise FileNotFoundError
and NEVER silently fall back to synthetic data.
"""
# ruff: noqa: E402
from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

# Ensure repository root and backend are in sys.path
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F  # noqa: N812
except ImportError:
    torch = None  # type: ignore

from experiments.elliptic.train_graphsage import (
    run_canonical_graphsage_benchmark,
)

logger = logging.getLogger("benchmarks.runners.run_graph_benchmark")


def _run_synthetic_smoke_benchmark(
    seed: int = 42,
    epochs: int = 20,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Execute legacy synthetic graph smoke benchmark for fast unit/smoke testing.

    This testbed uses an artificial 1500-node chain graph with shifted Gaussian features.
    Artifacts are strictly isolated and labeled as RETIRED_SYNTHETIC_SMOKE_BENCHMARK so they
    never overwrite or corrupt canonical real Elliptic benchmark claims.
    """
    if torch is None:
        raise RuntimeError("PyTorch is required to execute the GraphSAGE smoke benchmark.")

    np.random.seed(seed)
    torch.manual_seed(seed)

    n_train, n_test = 3000, 1500
    n_features = 166
    rng = np.random.default_rng(seed)

    y_tr = (rng.random(n_train) < 0.10).astype(np.int64)
    X_tr = rng.standard_normal((n_train, n_features)).astype(np.float32)
    X_tr[y_tr == 1, :10] += 1.5

    y_te = (rng.random(n_test) < 0.10).astype(np.int64)
    X_te = rng.standard_normal((n_test, n_features)).astype(np.float32)
    X_te[y_te == 1, :10] += 1.5

    def make_sparse_adj(n_nodes: int) -> torch.Tensor:
        indices = [[i, i] for i in range(n_nodes)]
        for i in range(n_nodes - 1):
            indices.append([i, i + 1])
            indices.append([i + 1, i])
        i_tensor = torch.LongTensor(indices).t()
        deg = torch.zeros(n_nodes)
        for idx in indices:
            deg[idx[0]] += 1.0
        v_norm = torch.FloatTensor([1.0 / (deg[idx[0]] ** 0.5 * deg[idx[1]] ** 0.5) for idx in indices])
        return torch.sparse_coo_tensor(i_tensor, v_norm, (n_nodes, n_nodes))

    adj_tr = make_sparse_adj(len(y_tr))
    adj_te = make_sparse_adj(len(y_te))

    class SimpleGraphSAGELayer(nn.Module):
        def __init__(self, in_features: int, out_features: int):
            super().__init__()
            self.weight_self = nn.Linear(in_features, out_features, bias=False)
            self.weight_neigh = nn.Linear(in_features, out_features, bias=False)
            self.bias = nn.Parameter(torch.zeros(out_features))

        def forward(self, h: torch.Tensor, adj_norm: torch.Tensor) -> torch.Tensor:
            neigh_h = torch.spmm(adj_norm, h)
            out = self.weight_self(h) + self.weight_neigh(neigh_h) + self.bias
            return F.relu(out)

    class SimpleGraphSAGEClassifier(nn.Module):
        def __init__(self, in_dim: int, hidden_dim: int = 64, out_dim: int = 1):
            super().__init__()
            self.sage1 = SimpleGraphSAGELayer(in_dim, hidden_dim)
            self.sage2 = SimpleGraphSAGELayer(hidden_dim, 32)
            self.classifier = nn.Linear(32, out_dim)

        def forward(self, x: torch.Tensor, adj_norm: torch.Tensor) -> torch.Tensor:
            h1 = self.sage1(x, adj_norm)
            h2 = self.sage2(h1, adj_norm)
            return torch.sigmoid(self.classifier(h2))

    model = SimpleGraphSAGEClassifier(in_dim=X_tr.shape[1], hidden_dim=64)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.01)
    criterion = nn.BCELoss()

    t_X_tr = torch.from_numpy(X_tr)
    t_y_tr = torch.from_numpy(y_tr).float().unsqueeze(1)

    model.train()
    for _ in range(epochs):
        optimizer.zero_grad()
        out = model(t_X_tr, adj_tr)
        loss = criterion(out, t_y_tr)
        loss.backward()
        optimizer.step()

    model.eval()
    with torch.no_grad():
        preds = model(torch.from_numpy(X_te), adj_te).numpy().flatten()

    pr_auc = float(average_precision_score(y_te, preds))
    roc_auc = float(roc_auc_score(y_te, preds))
    bin_preds = (preds >= 0.5).astype(int)
    prec = float(precision_score(y_te, bin_preds, zero_division=0))
    rec = float(recall_score(y_te, bin_preds, zero_division=0))
    f1 = float(f1_score(y_te, bin_preds, zero_division=0))

    payload = {
        "timestamp_utc": datetime.datetime.now(datetime.UTC).isoformat(),
        "benchmark_classification": "LEGACY_SYNTHETIC_SMOKE_BENCHMARK",
        "dataset": "synthetic_chain_graph",
        "model": "GraphSAGE (2-layer Mean Aggregator)",
        "temporal_split": "Synthetic Train vs Test",
        "train_nodes": len(y_tr),
        "test_nodes": len(y_te),
        "metrics": {
            "pr_auc": round(pr_auc, 4),
            "roc_auc": round(roc_auc, 4),
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1_score": round(f1, 4),
        },
        "warning": "This result is from a synthetic smoke graph testbed and must NOT be used for real Elliptic benchmark claims.",
    }

    out_dir = output_dir or (REPO_ROOT / "benchmarks" / "results" / "raw")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "graphsage_synthetic_smoke_benchmark.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print("\n========== [SMOKE] GRAPHSAGE SYNTHETIC BENCHMARK ==========")
    print(f"PR-AUC (Illicit class): {pr_auc:.4f}")
    print(f"ROC-AUC:                {roc_auc:.4f}")
    print(f"Precision:              {prec:.4f}")
    print(f"Recall:                 {rec:.4f}")
    print(f"F1-Score:               {f1:.4f}")
    print(f"Saved synthetic smoke results to: {out_file}\n")
    return payload


def run_graph_benchmark(
    seed: int = 42,
    seeds: Sequence[int] | None = None,
    epochs: int = 15,
    dataset_mode: str = "real",
    require_real: bool = True,
    output_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Execute the canonical GraphSAGE graph intelligence benchmark.

    Args:
        seed: Default seed if seeds sequence is not provided.
        seeds: Evaluation seeds (default [42, 123, 456]).
        epochs: Training epochs per seed (default 15).
        dataset_mode: 'real' (authoritative real Elliptic benchmark) or 'synthetic' (smoke test).
        require_real: If True and dataset_mode is 'real', fail closed if real files are missing.
        output_dir: Custom output directory for benchmark artifacts.

    Returns:
        Benchmark results payload.
    """
    eval_seeds = list(seeds) if seeds is not None else [42, 123, 456]

    if dataset_mode.lower() == "synthetic":
        logger.warning(
            "Executing SYNTHETIC smoke benchmark as explicitly requested. "
            "Results will be isolated to graphsage_synthetic_smoke_benchmark.json."
        )
        out_dir = Path(output_dir) if output_dir else None
        return _run_synthetic_smoke_benchmark(seed=seed, epochs=epochs, output_dir=out_dir)

    # Canonical Real Mode: delegate to authoritative real experiment
    logger.info("Executing CANONICAL real-data Elliptic GraphSAGE benchmark across seeds %s...", eval_seeds)
    return run_canonical_graphsage_benchmark(
        seed=eval_seeds[0],
        seeds=eval_seeds,
        epochs=epochs,
        require_real=require_real,
        dataset_mode="real",
        all_rows=True,
        output_dir=output_dir,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(
        description="Run Canonical GraphSAGE Inductive Benchmark on Elliptic Bitcoin Dataset"
    )
    parser.add_argument(
        "--dataset-mode",
        choices=["real", "synthetic"],
        default="real",
        help="Dataset mode: 'real' (authoritative physical dataset, fails closed) or 'synthetic' (smoke test)",
    )
    parser.add_argument("--epochs", type=int, default=15, help="Number of training epochs per seed")
    parser.add_argument(
        "--seeds",
        type=int,
        nargs="+",
        default=[42, 123, 456],
        help="Seeds for multi-seed statistical evaluation",
    )
    parser.add_argument(
        "--require-real",
        action="store_true",
        default=True,
        help="Enforce real dataset presence; fail closed if missing (default: True)",
    )
    parser.add_argument("--output-dir", type=str, default=None, help="Output directory for artifacts")

    args = parser.parse_args()

    results = run_graph_benchmark(
        seeds=args.seeds,
        epochs=args.epochs,
        dataset_mode=args.dataset_mode,
        require_real=args.require_real,
        output_dir=args.output_dir,
    )

    if args.dataset_mode == "real" and "aggregate_metrics" in results:
        agg = results["aggregate_metrics"]
        print("\n" + "=" * 80)
        print("     CANONICAL REAL-DATA ELLIPTIC GRAPHSAGE MULTI-SEED BENCHMARK (N=3)")
        print("=" * 80)
        print(f"{'Metric':<25} | {'Mean':<10} | {'Sample Std (ddof=1)':<20} | {'Min':<8} | {'Max':<8}")
        print("-" * 80)
        for m in ["pr_auc", "roc_auc", "precision", "recall", "f1_score", "recall_at_01_fpr"]:
            mean_v = agg["mean"].get(m, 0.0)
            std_v = agg["std"].get(m, 0.0)
            min_v = agg["min"].get(m, 0.0)
            max_v = agg["max"].get(m, 0.0)
            print(f"{m:<25} | {mean_v:<10.4f} | {std_v:<20.4f} | {min_v:<8.4f} | {max_v:<8.4f}")
        print("=" * 80)
        print(f"Artifact successfully written to: {results['paths']['raw_benchmark']}\n")


if __name__ == "__main__":
    main()

