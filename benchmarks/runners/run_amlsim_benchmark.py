"""IBM AMLSim Multi-Hop Pattern Detection & Structural Laundering Benchmark Runner.

Evaluates inductive graph representation learning (GraphSAGE) against tabular baselines
in intercepting complex multi-hop financial laundering typologies (cycles, fan-in, fan-out)
on the IBM Research AMLSim transaction graph.
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

from experiments.amlsim.evaluate_patterns import run_amlsim_pattern_benchmark

logger = logging.getLogger("benchmarks.runners.run_amlsim_benchmark")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Run IBM AMLSim Multi-Hop Pattern Detection Benchmark")
    parser.add_argument("--epochs", type=int, default=15, help="Number of training epochs")
    parser.add_argument("--lr", type=float, default=0.005, help="Learning rate")
    parser.add_argument("--hidden-dim", type=int, default=64, help="Hidden dimension")
    parser.add_argument("--embedding-dim", type=int, default=32, help="Embedding dimension")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--require-real", action="store_true", help="Require real physical dataset")
    parser.add_argument("--all-rows", action="store_true", help="Load entire 1.32M transactions")
    parser.add_argument("--nrows", type=int, default=None, help="Target row limit (subsampling)")
    parser.add_argument("--output-dir", type=str, default=None, help="Output directory")

    args = parser.parse_args()

    results = run_amlsim_pattern_benchmark(
        seed=args.seed,
        epochs=args.epochs,
        lr=args.lr,
        hidden_dim=args.hidden_dim,
        embedding_dim=args.embedding_dim,
        require_real=args.require_real,
        all_rows=args.all_rows,
        nrows=args.nrows,
        output_dir=args.output_dir,
    )

    sage_m = results["graphsage_metrics"]
    tab_m = results["tabular_metrics"]
    uplift = results["uplift"]

    print("\n" + "=" * 80)
    print("      IBM AMLSIM MULTI-HOP PATTERN DETECTION & TYPOLOGY BENCHMARK")
    print("=" * 80)
    print(f"{'Evaluation Metric':<30} | {'Tabular MLP':<14} | {'GraphSAGE 2-Layer':<18} | {'Uplift':<10}")
    print("-" * 80)
    print(f"{'PR-AUC':<30} | {tab_m['pr_auc']:<14.4f} | {sage_m['pr_auc']:<18.4f} | {uplift['delta_pr_auc_vs_tabular']:+10.4f}")
    print(f"{'ROC-AUC':<30} | {tab_m['roc_auc']:<14.4f} | {sage_m['roc_auc']:<18.4f} | {uplift['delta_roc_auc_vs_tabular']:+10.4f}")
    print(f"{'Recall @ 0.1% Strict FPR':<30} | {tab_m['recall_at_01_fpr']:<14.4f} | {sage_m['recall_at_01_fpr']:<18.4f} | {uplift['delta_recall_01_fpr_vs_tabular']:+10.4f}")
    print(f"{'Cycle Recall (Circular Flow)':<30} | {tab_m['cycle_recall']:<14.4f} | {sage_m['cycle_recall']:<18.4f} | {uplift['delta_cycle_recall_vs_tabular']:+10.4f}")
    print(f"{'Fan-In Recall (Smurfing)':<30} | {tab_m['fan_in_recall']:<14.4f} | {sage_m['fan_in_recall']:<18.4f} | {uplift['delta_fan_in_recall_vs_tabular']:+10.4f}")
    print(f"{'F1-Score':<30} | {tab_m['f1_score']:<14.4f} | {sage_m['f1_score']:<18.4f} | {sage_m['f1_score'] - tab_m['f1_score']:+10.4f}")
    print(f"{'Inference Latency (ms/1k)':<30} | {tab_m['inference_latency_per_1k_ms']:<14.2f} | {sage_m['inference_latency_per_1k_ms']:<18.2f} | {'N/A':<10}")
    print("=" * 80)
    print(f"Artifacts successfully serialized to: {results['paths']['results_json']}\n")


if __name__ == "__main__":
    main()
