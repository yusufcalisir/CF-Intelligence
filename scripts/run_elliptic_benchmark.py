"""CLI Script to execute Elliptic Benchmark and save verification reports."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.application.services.elliptic_benchmark_service import EllipticBenchmarkService


def main() -> None:
    print("=" * 80)
    print(" CF-INTELLIGENCE: ELLIPTIC AML GRAPH BENCHMARK ")
    print("=" * 80)

    service = EllipticBenchmarkService()
    results = service.run_benchmark(n_samples=5000, random_seed=42)

    fed = results["metrics"]["federated_graph_pipeline"]
    loc = results["metrics"]["isolated_single_bank_baseline"]
    adv = results["metrics"]["federated_advantage"]

    print(f"Dataset: {results['dataset']} ({results['source_type'].upper()})")
    print(f"Evaluated Nodes: {results['total_nodes']:,} | Edges: {results['total_edges']:,}")
    print(f"Illicit Rate: {results['illicit_rate_percent']}%")
    print("-" * 80)
    print(f"{'Pipeline':<40} | {'PR-AUC':<8} | {'ROC-AUC':<8} | {'Recall@0.1%':<12}")
    print("-" * 80)
    print(f"{'Federated Graph Pipeline':<40} | {fed['pr_auc']:<8.4f} | {fed['roc_auc']:<8.4f} | {fed['recall_at_01_fpr']*100:<11.1f}%")
    print(f"{'Isolated Single-Bank Baseline':<40} | {loc['pr_auc']:<8.4f} | {loc['roc_auc']:<8.4f} | {loc['recall_at_01_fpr']*100:<11.1f}%")
    print(f"{'Federation Advantage (Delta)':<40} | +{adv['pr_auc_gain']:<7.4f} | +{adv['roc_auc_gain']:<7.4f} | +{adv['recall_gain']*100:<10.1f}%")
    print("-" * 80)

    report_path = service.save_report(results, output_dir=Path("verification") / "real_data_benchmark")
    print(f"[+] Verification report generated at: {report_path}")
    print("=" * 80)


if __name__ == "__main__":
    main()
