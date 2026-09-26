"""Benchmark Chart Generator — Produces publication-grade figures from raw benchmark JSON outputs.

Generates:
  1. docs/figures/benchmark_auc_comparison.png (Centralized vs FedAvg on PaySim & IEEE-CIS)
  2. docs/figures/benchmark_fl_convergence.png (Convergence rounds for FedAvg, FedProx, SCAFFOLD)
  3. docs/figures/benchmark_privacy_utility.png (PR-AUC vs Epsilon trade-off)
  4. docs/figures/benchmark_byzantine_resilience.png (PR-AUC under Byzantine sign-inversion attack)
  5. docs/figures/benchmark_latency_concurrency.png (p50, p95, p99 latencies vs concurrency)
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # Headless backend
import matplotlib.pyplot as plt
import numpy as np


def setup_style():
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
        "figure.titlesize": 14,
        "figure.titleweight": "bold",
    })


def generate_auc_comparison(results_dir: Path, output_dir: Path):
    paysim_file = results_dir / "fraud_benchmark_paysim.json"
    ieee_file = results_dir / "fraud_benchmark_ieee_cis.json"

    datasets = []
    cent_pr, fed_pr = [], []
    cent_roc, fed_roc = [], []

    if paysim_file.exists():
        with open(paysim_file) as f:
            d = json.load(f)
            datasets.append("PaySim")
            cent_pr.append(d["centralized_baseline"]["pr_auc"])
            fed_pr.append(d["federated_fedavg"]["pr_auc"])
            cent_roc.append(d["centralized_baseline"]["roc_auc"])
            fed_roc.append(d["federated_fedavg"]["roc_auc"])

    if ieee_file.exists():
        with open(ieee_file) as f:
            d = json.load(f)
            datasets.append("IEEE-CIS")
            cent_pr.append(d["centralized_baseline"]["pr_auc"])
            fed_pr.append(d["federated_fedavg"]["pr_auc"])
            cent_roc.append(d["centralized_baseline"]["roc_auc"])
            fed_roc.append(d["federated_fedavg"]["roc_auc"])

    if not datasets:
        print("[!] No fraud benchmark data found, skipping AUC comparison chart.")
        return

    x = np.arange(len(datasets))
    width = 0.2

    fig, ax = plt.subplots(figsize=(8, 5), dpi=300)
    ax.bar(x - 1.5 * width, cent_pr, width, label="Centralized PR-AUC", color="#1f77b4")
    ax.bar(x - 0.5 * width, fed_pr, width, label="FedAvg PR-AUC", color="#aec7e8")
    ax.bar(x + 0.5 * width, cent_roc, width, label="Centralized ROC-AUC", color="#2ca02c")
    ax.bar(x + 1.5 * width, fed_roc, width, label="FedAvg ROC-AUC", color="#98df8a")

    ax.set_ylabel("Score (0.0 - 1.0)")
    ax.set_title("Fraud Detection Performance: Centralized Baseline vs FedAvg")
    ax.set_xticks(x)
    ax.set_xticklabels(datasets)
    ax.set_ylim(0, 1.1)
    ax.legend(loc="lower right")
    ax.grid(axis="y", linestyle="--", alpha=0.7)

    plt.tight_layout()
    out_file = output_dir / "benchmark_auc_comparison.png"
    plt.savefig(out_file)
    plt.close()
    print(f"[+] Saved: {out_file}")


def generate_fl_convergence(results_dir: Path, output_dir: Path):
    fl_file = results_dir / "fl_comparison_alpha_0.5.json"
    if not fl_file.exists():
        print("[!] No FL comparison data found, skipping convergence chart.")
        return

    with open(fl_file) as f:
        data = json.load(f)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5), dpi=300)
    colors = {"fedavg": "#1f77b4", "fedprox": "#ff7f0e", "scaffold": "#2ca02c"}
    labels = {"fedavg": "FedAvg", "fedprox": "FedProx (mu=0.01)", "scaffold": "SCAFFOLD"}

    for strat, info in data["strategies"].items():
        rounds = [h["round"] for h in info["rounds_history"]]
        pr_aucs = [h["pr_auc"] for h in info["rounds_history"]]
        roc_aucs = [h["roc_auc"] for h in info["rounds_history"]]

        c = colors.get(strat, "#333333")
        lbl = labels.get(strat, strat.upper())
        ax1.plot(rounds, pr_aucs, marker="o", linewidth=2, label=lbl, color=c)
        ax2.plot(rounds, roc_aucs, marker="s", linewidth=2, label=lbl, color=c)

    ax1.set_xlabel("Federated Round")
    ax1.set_ylabel("Validation PR-AUC")
    ax1.set_title("PR-AUC Convergence (Dirichlet alpha=0.5)")
    ax1.legend(loc="upper right")
    ax1.grid(True, linestyle="--", alpha=0.6)

    ax2.set_xlabel("Federated Round")
    ax2.set_ylabel("Validation ROC-AUC")
    ax2.set_title("ROC-AUC Convergence (Dirichlet alpha=0.5)")
    ax2.legend(loc="upper right")
    ax2.grid(True, linestyle="--", alpha=0.6)

    plt.suptitle("FL Strategy Comparison under Non-IID Label Skew", y=1.02)
    plt.tight_layout()
    out_file = output_dir / "benchmark_fl_convergence.png"
    plt.savefig(out_file, bbox_inches="tight")
    plt.close()
    print(f"[+] Saved: {out_file}")


def generate_privacy_utility(results_dir: Path, output_dir: Path):
    dp_file = results_dir / "dp_privacy_utility_tradeoff.json"
    if not dp_file.exists():
        print("[!] No DP trade-off data found, skipping privacy-utility chart.")
        return

    with open(dp_file) as f:
        data = json.load(f)

    # Exclude non-private / infinity for the numerical curve
    pts = [p for p in data["tradeoff_points"] if isinstance(p["epsilon"], (int, float))]
    pts.sort(key=lambda x: x["epsilon"])

    epsilons = [d["epsilon"] for d in pts]
    pr_aucs = [d["pr_auc"] for d in pts]
    roc_aucs = [d["roc_auc"] for d in pts]

    fig, ax = plt.subplots(figsize=(8, 5), dpi=300)
    ax.plot(epsilons, pr_aucs, marker="o", linewidth=2.5, color="#d62728", label="PR-AUC (Fraud Class)")
    ax.plot(epsilons, roc_aucs, marker="s", linewidth=2.0, color="#1f77b4", linestyle="--", label="ROC-AUC")

    # Annotate high privacy region
    ax.axvspan(0, 2.0, color="#ff9896", alpha=0.25, label="High Privacy Boundary (eps <= 2.0)")

    ax.set_xlabel("Privacy Budget Epsilon (delta = 1e-5)")
    ax.set_ylabel("Validation Utility Metric")
    ax.set_title("Differential Privacy vs Model Utility Frontier (Opacus RDP)")
    ax.set_ylim(0, 1.05)
    ax.legend(loc="lower right")
    ax.grid(True, linestyle="--", alpha=0.6)

    plt.tight_layout()
    out_file = output_dir / "benchmark_privacy_utility.png"
    plt.savefig(out_file)
    plt.close()
    print(f"[+] Saved: {out_file}")


def generate_byzantine_resilience(results_dir: Path, output_dir: Path):
    byz_file = results_dir / "byzantine_benchmark_sign_inversion.json"
    if not byz_file.exists():
        print("[!] No Byzantine data found, skipping Byzantine chart.")
        return

    with open(byz_file) as f:
        data = json.load(f)

    res = data["results"]
    display_names = ["Honest\nFedAvg", "Poisoned\nFedAvg", "Trimmed\nMean", "Krum", "Bulyan"]
    keys = [
        "Honest FedAvg (No Attackers)",
        "Poisoned FedAvg (Under Attack)",
        "Trimmed Mean (20% Coordinate Trim)",
        "Krum (Blanchard et al.)",
        "Bulyan (Guerraoui et al.)",
    ]
    colors = ["#2ca02c", "#d62728", "#1f77b4", "#ff7f0e", "#9467bd"]

    pr_aucs = [res[k]["pr_auc"] for k in keys if k in res]

    fig, ax = plt.subplots(figsize=(9, 5), dpi=300)
    bars = ax.bar(display_names[:len(pr_aucs)], pr_aucs, color=colors[:len(pr_aucs)], width=0.55, edgecolor="black", linewidth=0.8)

    for bar, val in zip(bars, pr_aucs):
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2.0, height + 0.015, f"{val:.4f}", ha="center", va="bottom", fontweight="bold")

    ax.set_ylabel("PR-AUC under Attack")
    ax.set_title("Byzantine Resilience under Sign-Inversion Attack (1 Malicious / 5 Clients)")
    ax.set_ylim(0, max(pr_aucs) * 1.25)
    ax.grid(axis="y", linestyle="--", alpha=0.6)

    plt.tight_layout()
    out_file = output_dir / "benchmark_byzantine_resilience.png"
    plt.savefig(out_file)
    plt.close()
    print(f"[+] Saved: {out_file}")


def generate_latency_concurrency(results_dir: Path, output_dir: Path):
    lat_file = results_dir / "latency_concurrency_benchmark.json"
    if not lat_file.exists():
        print("[!] No latency benchmark data found, skipping latency chart.")
        return

    with open(lat_file) as f:
        data = json.load(f)

    concurrencies = []
    p50, p95, p99 = [], [], []

    for item in data.get("concurrency_scaling", []):
        concurrencies.append(str(item["concurrency"]))
        p50.append(item["p50_latency_ms"])
        p95.append(item["p95_latency_ms"])
        p99.append(item["p99_latency_ms"])

    x = np.arange(len(concurrencies))
    width = 0.25

    fig, ax = plt.subplots(figsize=(10, 5), dpi=300)
    ax.bar(x - width, p50, width, label="p50 (Median)", color="#2ca02c")
    ax.bar(x, p95, width, label="p95", color="#1f77b4")
    ax.bar(x + width, p99, width, label="p99", color="#d62728")

    ax.set_xlabel("Concurrent Client Connections")
    ax.set_ylabel("Latency (milliseconds)")
    ax.set_title("Inference Gateway Latency under High Concurrency (1 - 500 Clients)")
    ax.set_xticks(x)
    ax.set_xticklabels(concurrencies)
    ax.legend(loc="upper left")
    ax.grid(axis="y", linestyle="--", alpha=0.6)

    plt.tight_layout()
    out_file = output_dir / "benchmark_latency_concurrency.png"
    plt.savefig(out_file)
    plt.close()
    print(f"[+] Saved: {out_file}")


def main():
    root = Path(__file__).resolve().parents[2]
    results_dir = root / "benchmarks" / "results" / "raw"
    output_dir = root / "docs" / "figures"
    output_dir.mkdir(parents=True, exist_ok=True)

    setup_style()
    print("[*] Generating benchmark visualization charts...")
    generate_auc_comparison(results_dir, output_dir)
    generate_fl_convergence(results_dir, output_dir)
    generate_privacy_utility(results_dir, output_dir)
    generate_byzantine_resilience(results_dir, output_dir)
    generate_latency_concurrency(results_dir, output_dir)
    print("[+] All benchmark charts successfully generated in docs/figures/.")


if __name__ == "__main__":
    main()
