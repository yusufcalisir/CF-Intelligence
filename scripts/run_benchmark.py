#!/usr/bin/env python3
"""Multi-Configuration Comparative Benchmark Suite CLI (Section 8.2).

Executes nine predefined benchmark configurations (C1–C9) comparing Local-Only,
Centralized, FedAvg, FedProx, DP, SecAgg, FedGNN, and Full Architecture.
Prints formatted Markdown summary tables and outputs storage/benchmark_results.json.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any

# Ensure backend directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

# ruff: noqa: I001, E402
from app.domain.benchmark_runner import BenchmarkRunner  # type: ignore # pyright: ignore[reportMissingImports]



logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_benchmark")


def format_markdown_table(results: dict[str, Any]) -> str:
    """Formats benchmark results dictionary into GitHub-flavored Markdown table."""
    lines = [
        "## 9-Configuration Benchmark Performance Table",
        "",
        "| ID | Configuration Name | ROC-AUC | PR-AUC | F1-Score | Recall @ 1% FPR | Epsilon (eps) | Transmitted Bytes | P99 Latency (ms) |",
        "|:---|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|",
    ]

    for cid in ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9"]:
        if cid in results:
            r = results[cid]
            bytes_str = (
                f"{r['total_bytes_transmitted'] / 1_000_000:.1f} MB"
                if r["total_bytes_transmitted"] > 0
                else "0 MB"
            )
            eps_str = f"{r['epsilon_consumed']:.1f}" if r["epsilon_consumed"] > 0 else "N/A"

            lines.append(
                f"| **{r['config_id']}** | {r['name']} | **{r['roc_auc']:.4f}** | {r['pr_auc']:.4f} | {r['f1_score']:.4f} | {r['recall_at_1pct_fpr']:.4f} | {eps_str} | {bytes_str} | {r['inference_latency_p99_ms']:.1f} ms |"
            )

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Multi-Configuration Comparative Benchmark")
    parser.add_argument("--samples", type=int, default=1000, help="Samples per bank node")
    parser.add_argument("--rounds", type=int, default=5, help="FL rounds to convergence")
    parser.add_argument(
        "--save-json", type=str, default="storage/benchmark_results.json", help="Output JSON path"
    )
    args = parser.parse_args()

    logger.info(
        "Initializing BenchmarkRunner (samples=%d, rounds=%d)...", args.samples, args.rounds
    )
    runner = BenchmarkRunner(samples_per_bank=args.samples, rounds=args.rounds)
    results = runner.run_all()
    json_path = runner.save_results_json(args.save_json)

    dict_results = {cid: res.to_dict() for cid, res in results.items()}
    md_table = format_markdown_table(dict_results)

    print("\n" + md_table + "\n")
    logger.info("Benchmark execution complete. Results saved to %s", json_path)


if __name__ == "__main__":
    main()
