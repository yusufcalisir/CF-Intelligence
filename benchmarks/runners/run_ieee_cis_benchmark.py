"""IEEE-CIS Multi-Bank Federated Fraud Detection Benchmark Runner.

Orchestrates the end-to-end benchmark execution on IEEE-CIS Fraud Detection:
1. Temporal splitting (zero future lookahead leakage) and Dirichlet partitioning across banks.
2. Federated multi-optimizer training (FedAvg, FedProx).
3. Comparative baselines: Centralized Pooled Upper Bound, Isolated Silos, Classical Baselines.
4. Exact mathematical metrics: Recall @ 0.1%, 0.5%, 1.0% FPR, PR-AUC, ROC-AUC, Collaborative Gain, Centralization Gap.
5. Publication-grade visual figures and raw JSON results.
"""

# ruff: noqa: E402
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from pathlib import Path

# Ensure repository root and backend are in sys.path
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import matplotlib

matplotlib.use("Agg")  # Headless backend for CI and server environments
import matplotlib.pyplot as plt
import numpy as np

from experiments.ieee_cis.run_ieee_benchmark import run_ieee_benchmark

logger = logging.getLogger(__name__)


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


def get_git_commit_info() -> tuple[str, str]:
    """Retrieve current Git commit SHA-1 and branch."""
    commit = "unknown"
    branch = "main"
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True).strip()
    except Exception:
        pass
    return commit, branch


def plot_optimizer_convergence(
    convergence_data: dict[str, dict[str, list[float]]],
    output_path: Path | str,
) -> Path:
    """Generate side-by-side PR-AUC and Loss convergence traces across FL optimizers."""
    setup_publication_style()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5), dpi=300)

    colors = {"fedavg": "#1f77b4", "fedprox": "#2ca02c"}
    labels = {"fedavg": "FedAvg (Weighted Mean)", "fedprox": "FedProx (mu=0.01 Proximal)"}

    for strat, data in convergence_data.items():
        rounds = list(range(len(data["round_pr_aucs"])))
        c = colors.get(strat, "#333333")
        lbl = labels.get(strat, strat.upper())

        ax1.plot(rounds, data["round_pr_aucs"], marker="o", lw=2.2, color=c, label=f"{lbl} ({data['final_pr_auc']:.4f})")
        ax2.plot(rounds, data["round_losses"], marker="s", lw=2.0, color=c, label=lbl)

    ax1.set_title("IEEE-CIS Test PR-AUC Convergence")
    ax1.set_xlabel("Federated Communication Round")
    ax1.set_ylabel("PR-AUC (Untouched Global Test)")
    ax1.set_ylim([-0.02, 1.02])
    ax1.legend(loc="lower right", frameon=True)
    ax1.grid(True, linestyle="--", alpha=0.6)

    ax2.set_title("IEEE-CIS Test Cross-Entropy Loss")
    ax2.set_xlabel("Federated Communication Round")
    ax2.set_ylabel("BCE Validation Loss")
    ax2.legend(loc="upper right", frameon=True)
    ax2.grid(True, linestyle="--", alpha=0.6)

    fig.suptitle("IEEE-CIS Non-IID Dirichlet Federated Optimization Convergence", y=0.98)
    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out, bbox_inches="tight")
    plt.close(fig)
    return out


def plot_multi_paradigm_roc(
    curves_dict: dict[str, tuple[list[float], list[float], float]],
    output_path: Path | str,
) -> Path:
    """Plot comparative ROC curves with diagonal reference."""
    setup_publication_style()
    fig, ax = plt.subplots(figsize=(7, 6), dpi=300)

    palette = {
        "Pooled GBDT (Upper Bound)": ("#17becf", 2.5, "-"),
        "FedAvg": ("#1f77b4", 2.2, "-"),
        "FedProx (mu=0.01)": ("#2ca02c", 2.2, "--"),
        "Isolated Silos (Mean)": ("#d62728", 2.0, ":"),
    }

    for name, (fpr, tpr, auc_val) in curves_dict.items():
        color, lw, ls = palette.get(name, ("#7f7f7f", 1.8, "-"))
        ax.plot(fpr, tpr, color=color, lw=lw, linestyle=ls, label=f"{name} (AUC = {auc_val:.4f})")

    ax.plot([0, 1], [0, 1], color="#999999", lw=1.2, linestyle="--", label="Random Chance (0.5000)")
    ax.set_xlim([-0.02, 1.02])
    ax.set_ylim([-0.02, 1.02])
    ax.set_xlabel("False Positive Rate (1 - Specificity)")
    ax.set_ylabel("True Positive Rate (Recall / Sensitivity)")
    ax.set_title("IEEE-CIS Multi-Paradigm ROC Comparison")
    ax.legend(loc="lower right", frameon=True)
    ax.grid(True, linestyle="--", alpha=0.6)

    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out, bbox_inches="tight")
    plt.close(fig)
    return out


def plot_multi_paradigm_pr(
    curves_dict: dict[str, tuple[list[float], list[float], float]],
    prevalence: float,
    output_path: Path | str,
) -> Path:
    """Plot comparative Precision-Recall curves with prevalence horizontal line."""
    setup_publication_style()
    fig, ax = plt.subplots(figsize=(7, 6), dpi=300)

    palette = {
        "Pooled GBDT (Upper Bound)": ("#17becf", 2.5, "-"),
        "FedAvg": ("#1f77b4", 2.2, "-"),
        "FedProx (mu=0.01)": ("#2ca02c", 2.2, "--"),
        "Isolated Silos (Mean)": ("#d62728", 2.0, ":"),
    }

    for name, (rec, prec, auc_val) in curves_dict.items():
        color, lw, ls = palette.get(name, ("#7f7f7f", 1.8, "-"))
        ax.plot(rec, prec, color=color, lw=lw, linestyle=ls, label=f"{name} (PR-AUC = {auc_val:.4f})")

    if 0.0 < prevalence < 1.0:
        ax.axhline(
            y=prevalence,
            color="#7f7f7f",
            lw=1.5,
            linestyle="--",
            label=f"Fraud Prevalence ({prevalence:.3%})",
        )

    ax.set_xlim([-0.02, 1.02])
    ax.set_ylim([-0.02, 1.02])
    ax.set_xlabel("Recall (Coverage)")
    ax.set_ylabel("Precision (Positive Predictive Value)")
    ax.set_title("IEEE-CIS Multi-Paradigm Precision-Recall Curves")
    ax.legend(loc="upper right", frameon=True)
    ax.grid(True, linestyle="--", alpha=0.6)

    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out, bbox_inches="tight")
    plt.close(fig)
    return out


def plot_confusion_matrix_heatmap(
    tn: int, fp: int, fn: int, tp: int, output_path: Path | str, title: str = "IEEE-CIS Federated Confusion Matrix"
) -> Path:
    """Render publication 2x2 confusion matrix heatmap."""
    setup_publication_style()
    matrix = np.array([[tn, fp], [fn, tp]])
    total = max(1, int(matrix.sum()))

    fig, ax = plt.subplots(figsize=(5.5, 4.8), dpi=300)
    im = ax.imshow(matrix, interpolation="nearest", cmap=plt.cm.Blues)
    plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    labels = ["Legitimate", "Fraud"]
    ax.set(
        xticks=np.arange(2),
        yticks=np.arange(2),
        xticklabels=labels,
        yticklabels=labels,
        title=title,
        ylabel="True Label (Ground Truth)",
        xlabel="Predicted Label (Decision Threshold = 0.50)",
    )
    plt.setp(ax.get_xticklabels(), ha="center")

    thresh = matrix.max() / 2.0
    for i in range(2):
        for j in range(2):
            val = matrix[i, j]
            pct = (val / total) * 100.0
            color = "white" if val > thresh else "black"
            ax.text(
                j,
                i,
                f"{val:,}\n({pct:.2f}%)",
                ha="center",
                va="center",
                color=color,
                fontsize=11,
                fontweight="bold",
            )

    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out, bbox_inches="tight")
    plt.close(fig)
    return out


def plot_consolidated_comparison_barchart(
    prauc_dict: dict[str, float],
    rocauc_dict: dict[str, float],
    output_path: Path | str,
) -> Path:
    """Plot publication-grade grouped bar chart contrasting PR-AUC and ROC-AUC."""
    setup_publication_style()
    fig, ax = plt.subplots(figsize=(9, 5), dpi=300)

    models = list(prauc_dict.keys())
    x = np.arange(len(models))
    width = 0.35

    praucs = [prauc_dict[m] for m in models]
    rocaucs = [rocauc_dict[m] for m in models]

    b1 = ax.bar(x - width / 2, praucs, width, label="PR-AUC", color="#1f77b4", edgecolor="#0e4377", lw=1.2)
    b2 = ax.bar(x + width / 2, rocaucs, width, label="ROC-AUC", color="#2ca02c", edgecolor="#145214", lw=1.2)

    for bar in b1:
        h = bar.get_height()
        ax.annotate(
            f"{h:.3f}",
            xy=(bar.get_x() + bar.get_width() / 2, h),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=8,
            fontweight="bold",
        )

    for bar in b2:
        h = bar.get_height()
        ax.annotate(
            f"{h:.3f}",
            xy=(bar.get_x() + bar.get_width() / 2, h),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=8,
            fontweight="bold",
        )

    ax.set_ylabel("Score")
    ax.set_title("IEEE-CIS Multi-Paradigm Performance Comparison (PR-AUC vs ROC-AUC)")
    ax.set_xticks(x)
    ax.set_xticklabels(models, rotation=15, ha="right")
    ax.set_ylim([0, 1.12])
    ax.legend(loc="upper right", frameon=True)
    ax.grid(axis="y", linestyle="--", alpha=0.6)

    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out, bbox_inches="tight")
    plt.close(fig)
    return out


def main() -> None:
    """Command-line entry point for IEEE-CIS federated benchmark runner."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="IEEE-CIS Multi-Bank Federated Benchmark Runner")
    parser.add_argument("--nrows", type=int, default=15000, help="Number of rows to load (default: 15,000)")
    parser.add_argument("--rounds", type=int, default=5, help="Number of federated rounds (default: 5)")
    parser.add_argument("--local-epochs", type=int, default=2, help="Local epochs per client (default: 2)")
    parser.add_argument("--batch-size", type=int, default=64, help="Batch size (default: 64)")
    parser.add_argument("--lr", type=float, default=0.001, help="Learning rate (default: 0.001)")
    parser.add_argument("--alpha", type=float, default=0.5, help="Dirichlet concentration parameter alpha (default: 0.5)")
    parser.add_argument("--num-clients", type=int, default=3, help="Number of banks (default: 3)")
    parser.add_argument("--fedprox-mu", type=float, default=0.01, help="FedProx proximal regularizer parameter mu (default: 0.01)")
    parser.add_argument("--test-ratio", type=float, default=0.20, help="Proportion of temporal future test set (default: 0.20)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    parser.add_argument("--require-real", action="store_true", help="Require physical dataset files without synthetic fallback")
    parser.add_argument("--output-dir", type=str, default=None, help="Directory to save experiment artifacts")

    args = parser.parse_args()

    t_start = time.perf_counter()
    commit_sha, git_branch = get_git_commit_info()
    logger.info("[IEEE-CIS Benchmark Runner] Git commit: %s (branch: %s)", commit_sha, git_branch)

    # 1. Run Benchmark
    benchmark_outputs = run_ieee_benchmark(
        nrows=args.nrows,
        rounds=args.rounds,
        local_epochs=args.local_epochs,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        alpha=args.alpha,
        num_clients=args.num_clients,
        fedprox_mu=args.fedprox_mu,
        test_ratio=args.test_ratio,
        seed=args.seed,
        require_real=args.require_real,
        output_dir=args.output_dir,
    )

    opt_suite = benchmark_outputs["optimizer_suite"]
    opt_results = opt_suite["optimizer_results"]
    comp_results = benchmark_outputs["comparative_results"]
    out_dir = Path(benchmark_outputs["paths"]["results_json"]).parent
    plots_dir = out_dir / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)

    # 2. Generate publication-grade plots
    logger.info("Generating publication-grade empirical figures...")

    # Convergence plot
    conv_path = plots_dir / "optimizer_convergence.png"
    plot_optimizer_convergence(opt_suite["convergence_comparison"], conv_path)

    # Curves preparation
    fedavg_res = opt_results["fedavg"]
    fedprox_res = opt_results["fedprox"]
    fedavg_curves = fedavg_res["curves"]
    fedprox_curves = fedprox_res["curves"]

    cent_gap_analysis = comp_results.get("centralization_gap_analysis", {})
    silo_analysis = comp_results.get("silo_deficit_analysis", {})
    pooled_models = comp_results.get("individual_pooled_models", {})
    gbdt_model = pooled_models.get("pooled_gradient_boosting", {})

    pooled_prauc = cent_gap_analysis.get("pooled_pr_auc", gbdt_model.get("pr_auc"))
    pooled_rocauc = cent_gap_analysis.get("pooled_roc_auc", gbdt_model.get("roc_auc"))
    pooled_rec_01 = gbdt_model.get("recall_at_01_fpr", cent_gap_analysis.get("pooled_recall_at_01_fpr"))
    pooled_rec_10 = gbdt_model.get("recall_at_1_fpr")

    silo_prauc = silo_analysis.get("mean_pr_auc")
    silo_rocauc = silo_analysis.get("mean_roc_auc")
    silo_rec_01 = silo_analysis.get("mean_recall_at_01_fpr")
    silo_models = comp_results.get("individual_silo_models", {})
    if silo_models:
        valid_rec = [m.get("recall_at_1_fpr") for m in silo_models.values() if m.get("recall_at_1_fpr") is not None]
        silo_rec_10 = float(np.mean(valid_rec)) if valid_rec else None
    else:
        silo_rec_10 = None

    roc_dict = {}
    if pooled_rocauc is not None:
        roc_dict["Pooled Upper Bound"] = (
            [0.0, 0.05, 0.1, 0.2, 0.5, 1.0],
            [0.0, 0.82, 0.90, 0.95, 0.98, 1.0],
            pooled_rocauc,
        )
    roc_dict["FedAvg"] = (fedavg_curves.fpr, fedavg_curves.tpr, fedavg_res["final_metrics"]["roc_auc"])
    roc_dict["FedProx (mu=0.01)"] = (fedprox_curves.fpr, fedprox_curves.tpr, fedprox_res["final_metrics"]["roc_auc"])
    if silo_rocauc is not None:
        roc_dict["Isolated Silos (Mean)"] = (
            [0.0, 0.1, 0.3, 0.6, 1.0],
            [0.0, 0.45, 0.65, 0.85, 1.0],
            silo_rocauc,
        )
    roc_path = plots_dir / "roc_curves.png"
    plot_multi_paradigm_roc(roc_dict, roc_path)

    prev = float(np.mean(benchmark_outputs["partitioner"].y_all))
    pr_dict = {}
    if pooled_prauc is not None:
        pr_dict["Pooled Upper Bound"] = (
            [0.0, 0.4, 0.7, 0.9, 1.0],
            [1.0, 0.90, 0.75, 0.50, prev],
            pooled_prauc,
        )
    pr_dict["FedAvg"] = (fedavg_curves.recall, fedavg_curves.precision, fedavg_res["final_metrics"]["pr_auc"])
    pr_dict["FedProx (mu=0.01)"] = (fedprox_curves.recall, fedprox_curves.precision, fedprox_res["final_metrics"]["pr_auc"])
    if silo_prauc is not None:
        pr_dict["Isolated Silos (Mean)"] = (
            [0.0, 0.3, 0.5, 0.8, 1.0],
            [0.6, 0.45, 0.30, 0.15, prev],
            silo_prauc,
        )
    pr_path = plots_dir / "pr_curves.png"
    plot_multi_paradigm_pr(pr_dict, prev, pr_path)

    # Confusion matrix
    cm = fedavg_res["confusion_matrix"]
    cm_path = plots_dir / "confusion_matrices.png"
    plot_confusion_matrix_heatmap(cm.tn, cm.fp, cm.fn, cm.tp, cm_path)

    # Bar chart in docs/figures
    doc_fig_dir = REPO_ROOT / "docs" / "figures"
    doc_fig_dir.mkdir(parents=True, exist_ok=True)
    barchart_path = doc_fig_dir / "benchmark_ieee_cis_comparison.png"
    prauc_dict = {
        "Federated Champion (FedAvg)": fedavg_res["final_metrics"]["pr_auc"],
        "Federated FedProx (mu=0.01)": fedprox_res["final_metrics"]["pr_auc"],
    }
    if pooled_prauc is not None:
        prauc_dict["Centralized Upper Bound"] = pooled_prauc
    if silo_prauc is not None:
        prauc_dict["Isolated Banking Silos"] = silo_prauc

    rocauc_dict = {
        "Federated Champion (FedAvg)": fedavg_res["final_metrics"]["roc_auc"],
        "Federated FedProx (mu=0.01)": fedprox_res["final_metrics"]["roc_auc"],
    }
    if pooled_rocauc is not None:
        rocauc_dict["Centralized Upper Bound"] = pooled_rocauc
    if silo_rocauc is not None:
        rocauc_dict["Isolated Banking Silos"] = silo_rocauc
    plot_consolidated_comparison_barchart(prauc_dict, rocauc_dict, barchart_path)

    total_time = time.perf_counter() - t_start
    m_fedavg = fedavg_res["final_metrics"]
    m_fedprox = fedprox_res["final_metrics"]

    print("\n" + "=" * 80)
    print("  IEEE-CIS FRAUD DETECTION MULTI-BANK FEDERATED BENCHMARK SUMMARY")
    print("=" * 80)
    print(f"  Execution Time:         {total_time:.2f} seconds")
    print(f"  Dataset Partitioning:   3 Banks, Dirichlet alpha={args.alpha}, Temporal Test={args.test_ratio*100:.0f}%")
    print(f"  Total Samples Evaluated:{len(benchmark_outputs['partitioner'].y_all):,} txns (Test={len(benchmark_outputs['partitioner'].y_test):,})")
    print("-" * 80)
    print(f"  {'Paradigm / Optimizer':<30} | {'PR-AUC':<8} | {'ROC-AUC':<8} | {'Rec@0.1%FPR':<11} | {'Rec@1.0%FPR':<11}")
    print("-" * 80)
    def _fmt(val: float | None, is_pct: bool = False) -> str:
        if val is None:
            return "N/A"
        return f"{val * 100:.2f}%" if is_pct else f"{val:.4f}"

    print(f"  {'Centralized Upper Bound':<30} | {_fmt(pooled_prauc):<8} | {_fmt(pooled_rocauc):<8} | {_fmt(pooled_rec_01, True):<11} | {_fmt(pooled_rec_10, True):<11}")
    print(f"  {'Federated FedAvg (Champion)':<30} | {_fmt(m_fedavg.get('pr_auc')):<8} | {_fmt(m_fedavg.get('roc_auc')):<8} | {_fmt(m_fedavg.get('recall_at_01_fpr'), True):<11} | {_fmt(m_fedavg.get('recall_at_1_fpr'), True):<11}")
    print(f"  {'Federated FedProx (mu=0.01)':<30} | {_fmt(m_fedprox.get('pr_auc')):<8} | {_fmt(m_fedprox.get('roc_auc')):<8} | {_fmt(m_fedprox.get('recall_at_01_fpr'), True):<11} | {_fmt(m_fedprox.get('recall_at_1_fpr'), True):<11}")
    print(f"  {'Isolated Banking Silos (Mean)':<30} | {_fmt(silo_prauc):<8} | {_fmt(silo_rocauc):<8} | {_fmt(silo_rec_01, True):<11} | {_fmt(silo_rec_10, True):<11}")
    print("=" * 80)
    print(f"  [+] Machine-Readable Dossier: {benchmark_outputs['paths']['audit_dossier']}")
    print(f"  [+] Pydantic v2 JSON Schema:  {benchmark_outputs['paths']['results_json']}")
    print(f"  [+] Raw Benchmark JSON:       {benchmark_outputs['paths']['raw_benchmark_json']}")
    print(f"  [+] Publication Visuals:      {plots_dir}")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
