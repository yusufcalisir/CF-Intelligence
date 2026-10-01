"""GraphSAGE Inductive Representation Learning & Benchmark Runner on Elliptic Bitcoin Graph.

Evaluates inductive node classification performance on financial transaction graphs
(Elliptic Bitcoin Dataset or controlled synthetic graph).
Enforces strict temporal split (timesteps 1-34 train vs 35-49 test) with zero future leakage.
Measures: PR-AUC, ROC-AUC, Recall @ fixed FPR (0.1%, 0.5%, 1.0%), Precision, Recall, F1 on illicit node detection.
Quantifies exact Neighborhood Aggregation Uplift (Delta PR-AUC, Delta ROC-AUC).
"""

# ruff: noqa: E402
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Ensure repository root and backend are in sys.path
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from experiments.elliptic.train_graphsage import run_graphsage_benchmark

logger = logging.getLogger("benchmarks.runners.run_graphsage_benchmark")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Run GraphSAGE Inductive Benchmark on Elliptic Bitcoin Dataset")
    parser.add_argument("--epochs", type=int, default=15, help="Number of training epochs per seed")
    parser.add_argument("--lr", type=float, default=0.005, help="Learning rate")
    parser.add_argument("--hidden-dim", type=int, default=128, help="Hidden dimension")
    parser.add_argument("--embedding-dim", type=int, default=64, help="Embedding dimension")
    parser.add_argument("--seed", type=int, default=42, help="Default random seed")
    parser.add_argument(
        "--seeds",
        type=int,
        nargs="+",
        default=[42, 123, 456],
        help="Seeds for multi-seed statistical evaluation",
    )
    parser.add_argument(
        "--dataset-mode",
        choices=["real", "synthetic"],
        default="real",
        help="Dataset mode: 'real' (fails closed) or 'synthetic' (smoke)",
    )
    parser.add_argument("--require-real", action="store_true", default=True, help="Require real physical dataset")
    parser.add_argument("--all-rows", action="store_true", default=True, help="Load entire 203,769 nodes")
    parser.add_argument("--nrows", type=int, default=None, help="Target number of rows (subsampling if not all-rows)")
    parser.add_argument("--output-dir", type=str, default=None, help="Output directory")

    args = parser.parse_args()

    results = run_graphsage_benchmark(
        seed=args.seed,
        seeds=args.seeds,
        epochs=args.epochs,
        lr=args.lr,
        hidden_dim=args.hidden_dim,
        embedding_dim=args.embedding_dim,
        require_real=args.require_real,
        dataset_mode=args.dataset_mode,
        all_rows=args.all_rows,
        nrows=args.nrows,
        output_dir=args.output_dir,
    )

    sage_m = results["graphsage_metrics"]
    tab_m = results["tabular_metrics"]
    uplift = results["uplift"]

    print("\n" + "=" * 78)
    print("        GRAPHSAGE VS TABULAR MLP INDUCTIVE CLASSIFICATION BENCHMARK")
    print("=" * 78)
    print(f"{'Evaluation Metric':<28} | {'Tabular MLP':<14} | {'GraphSAGE 2-Layer':<18} | {'Uplift':<10}")
    print("-" * 78)
    print(f"{'PR-AUC (Illicit Transactions)':<28} | {tab_m['pr_auc']:<14.4f} | {sage_m['pr_auc']:<18.4f} | {uplift['delta_pr_auc_vs_tabular']:+10.4f}")
    print(f"{'ROC-AUC (Distribution Sep)':<28} | {tab_m['roc_auc']:<14.4f} | {sage_m['roc_auc']:<18.4f} | {uplift['delta_roc_auc_vs_tabular']:+10.4f}")
    print(f"{'Recall @ 0.1% Strict FPR':<28} | {tab_m['recall_at_01_fpr']:<14.4f} | {sage_m['recall_at_01_fpr']:<18.4f} | {uplift['delta_recall_01_fpr_vs_tabular']:+10.4f}")
    print(f"{'Recall @ 0.5% FPR':<28} | {tab_m['recall_at_05_fpr']:<14.4f} | {sage_m['recall_at_05_fpr']:<18.4f} | {uplift['delta_recall_05_fpr_vs_tabular']:+10.4f}")
    print(f"{'Recall @ 1.0% FPR':<28} | {tab_m['recall_at_10_fpr']:<14.4f} | {sage_m['recall_at_10_fpr']:<18.4f} | {uplift['delta_recall_10_fpr_vs_tabular']:+10.4f}")
    print(f"{'F1-Score':<28} | {tab_m['f1_score']:<14.4f} | {sage_m['f1_score']:<18.4f} | {uplift['delta_f1_vs_tabular']:+10.4f}")
    print(f"{'Brier Score Loss':<28} | {tab_m['brier_score']:<14.4f} | {sage_m['brier_score']:<18.4f} | {sage_m['brier_score'] - tab_m['brier_score']:+10.4f}")
    print(f"{'Inference Latency (ms/1k)':<28} | {tab_m['inference_latency_per_1k_ms']:<14.2f} | {sage_m['inference_latency_per_1k_ms']:<18.2f} | {'N/A':<10}")
    print("=" * 78)

    if "aggregate_metrics" in results:
        agg = results["aggregate_metrics"]
        print("\n" + "=" * 78)
        print("     MULTI-SEED STATISTICAL AGGREGATES (SEEDS: 42, 123, 456 | DDOF=1)")
        print("=" * 78)
        print(f"{'Metric':<25} | {'Mean':<10} | {'Sample Std (ddof=1)':<20} | {'Min':<8} | {'Max':<8}")
        print("-" * 78)
        for m in ["pr_auc", "roc_auc", "precision", "recall", "f1_score", "recall_at_01_fpr"]:
            mean_v = agg["mean"].get(m, 0.0)
            std_v = agg["std"].get(m, 0.0)
            min_v = agg["min"].get(m, 0.0)
            max_v = agg["max"].get(m, 0.0)
            print(f"{m:<25} | {mean_v:<10.4f} | {std_v:<20.4f} | {min_v:<8.4f} | {max_v:<8.4f}")
        print("=" * 78)

    print(f"Artifacts successfully serialized to: {results['paths']['results_json']}\n")


if __name__ == "__main__":
    main()
