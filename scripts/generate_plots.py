#!/usr/bin/env python3
"""Multi-Configuration Benchmark Visualization & Plot Generator (Section 8.2).

Generates high-resolution comparative figures:
1. docs/figures/benchmark_auc_comparison.png - ROC-AUC & PR-AUC across C1–C9
2. docs/figures/benchmark_privacy_utility.png - Privacy-Utility trade-off curve (AUC vs. ε)
3. docs/figures/benchmark_communication.png - Transmitted bytes vs. rounds to convergence
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

# Add backend to path for fallback generation
backend_path = str(Path(__file__).resolve().parents[1] / "backend")
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from app.domain.benchmark_runner import BenchmarkRunner  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("generate_plots")


def generate_benchmark_plots(
    json_file: str | Path = "storage/benchmark_results.json", out_dir: str | Path = "docs/figures"
) -> None:
    """Renders high-resolution benchmark visualization figures."""
    json_path = Path(json_file)
    target_dir = Path(out_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    if not json_path.exists():
        logger.info("JSON benchmark results not found at %s. Running BenchmarkRunner...", json_path)
        runner = BenchmarkRunner()
        runner.run_all()
        runner.save_results_json(json_path)

    data = json.loads(json_path.read_text(encoding="utf-8"))

    configs = ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9"]
    roc_aucs = [data[c]["roc_auc"] for c in configs]
    pr_aucs = [data[c]["pr_auc"] for c in configs]
    bytes_mb = [data[c]["total_bytes_transmitted"] / 1_000_000 for c in configs]


    # --- Plot 1: ROC-AUC & PR-AUC Comparison Bar Chart ---
    plt.style.use(
        "seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "default"
    )
    fig, ax = plt.subplots(figsize=(12, 6))

    x = np.arange(len(configs))
    width = 0.35

    rects1 = ax.bar(x - width / 2, roc_aucs, width, label="ROC-AUC", color="#1f77b4")
    rects2 = ax.bar(x + width / 2, pr_aucs, width, label="PR-AUC", color="#ff7f0e")

    ax.set_ylabel("Metric Score", fontsize=12, fontweight="bold")
    ax.set_title(
        "Cross-Bank Federated Fraud Intelligence - 9 Configuration Benchmark",
        fontsize=14,
        fontweight="bold",
        pad=15,
    )
    ax.set_xticks(x)
    ax.set_xticklabels(configs, fontweight="bold")
    ax.set_ylim(0.70, 1.0)
    ax.legend(frameon=True, facecolor="white", framealpha=0.9)

    ax.bar_label(rects1, padding=3, fmt="%.3f", fontsize=8)
    ax.bar_label(rects2, padding=3, fmt="%.3f", fontsize=8)

    fig.tight_layout()
    fig1_path = target_dir / "benchmark_auc_comparison.png"
    plt.savefig(fig1_path, dpi=300)
    plt.close()
    logger.info("Saved Figure 1: %s", fig1_path)

    # --- Plot 2: Privacy-Utility Trade-off Curve (AUC vs Epsilon) ---
    fig, ax = plt.subplots(figsize=(8, 5))

    privacy_configs = ["C3", "C5", "C7", "C9"]
    eps_vals = [
        data[c]["epsilon_consumed"] if data[c]["epsilon_consumed"] > 0 else 10.0
        for c in privacy_configs
    ]
    auc_vals = [data[c]["roc_auc"] for c in privacy_configs]
    labels = [
        "FedAvg (Eps=Inf)",
        "FedAvg+DP (Eps=1.0)",
        "Full Privacy (Eps=1.0)",
        "Full Stack (Eps=1.0)",
    ]

    ax.plot(
        eps_vals, auc_vals, "o--", color="#2ca02c", linewidth=2.5, markersize=8, label="ROC-AUC"
    )

    for i, txt in enumerate(labels):
        ax.annotate(
            txt,
            (eps_vals[i], auc_vals[i]),
            textcoords="offset points",
            xytext=(0, 10),
            ha="center",
            fontsize=9,
            fontweight="bold",
        )

    ax.set_xlabel("Differential Privacy Budget (Epsilon ε)", fontsize=11, fontweight="bold")
    ax.set_ylabel("Global ROC-AUC Score", fontsize=11, fontweight="bold")
    ax.set_title(
        "Privacy-Utility Trade-off (Differential Privacy Noise vs AUC)",
        fontsize=13,
        fontweight="bold",
        pad=15,
    )
    ax.set_ylim(0.85, 0.95)
    ax.grid(True, linestyle="--", alpha=0.6)

    fig.tight_layout()
    fig2_path = target_dir / "benchmark_privacy_utility.png"
    plt.savefig(fig2_path, dpi=300)
    plt.close()
    logger.info("Saved Figure 2: %s", fig2_path)

    # --- Plot 3: Communication Payload Size (MB) ---
    fig, ax = plt.subplots(figsize=(10, 5))

    colors = [
        "#7f7f7f",
        "#d62728",
        "#1f77b4",
        "#1f77b4",
        "#9467bd",
        "#17becf",
        "#9467bd",
        "#e377c2",
        "#bcbd22",
    ]
    bars = ax.bar(configs, bytes_mb, color=colors)

    ax.set_ylabel("Total Network Bytes Transmitted (MB)", fontsize=11, fontweight="bold")
    ax.set_title(
        "Communication Network Overhead Across Architectures",
        fontsize=13,
        fontweight="bold",
        pad=15,
    )
    ax.set_ylim(0, max(bytes_mb) * 1.2)

    ax.bar_label(bars, padding=3, fmt="%.1f MB", fontsize=8)

    fig.tight_layout()
    fig3_path = target_dir / "benchmark_communication.png"
    plt.savefig(fig3_path, dpi=300)
    plt.close()
    logger.info("Saved Figure 3: %s", fig3_path)


if __name__ == "__main__":
    generate_benchmark_plots()
