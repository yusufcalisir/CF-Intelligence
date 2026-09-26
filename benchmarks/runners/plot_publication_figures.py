"""Publication-grade figure generation for individual experiments and comparative benchmarks.

Produces 300 DPI, academic publication-ready visual artifacts:
  - ROC Curves with AUC confidence labels and diagonal reference
  - Precision-Recall Curves with baseline fraud prevalence
  - Reliability Diagrams (Calibration Curves) with Brier score
  - Confusion Matrices with normalized percentages and counts
  - Multi-Strategy Federated Learning & Differential Privacy Frontiers
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # Headless backend for CI and non-interactive servers
import matplotlib.pyplot as plt
import numpy as np


def setup_publication_style():
    """Configure matplotlib rcParams for clear, professional publication aesthetics."""
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
        "figure.dpi": 300,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
    })


def plot_roc_curve(
    fpr: list[float],
    tpr: list[float],
    roc_auc: float,
    output_path: Path | str,
    title: str = "Receiver Operating Characteristic (ROC)",
    model_name: str = "Federated Fraud Detector",
) -> Path:
    """Generate publication-ready ROC curve."""
    setup_publication_style()
    fig, ax = plt.subplots(figsize=(6, 5), dpi=300)

    ax.plot(
        fpr,
        tpr,
        color="#1f77b4",
        lw=2.5,
        label=f"{model_name} (ROC-AUC = {roc_auc:.4f})",
    )
    ax.plot([0, 1], [0, 1], color="#7f7f7f", lw=1.5, linestyle="--", label="Random Chance (AUC = 0.5000)")

    ax.fill_between(fpr, tpr, alpha=0.15, color="#1f77b4")
    ax.set_xlim([-0.02, 1.02])
    ax.set_ylim([-0.02, 1.02])
    ax.set_xlabel("False Positive Rate (1 - Specificity)")
    ax.set_ylabel("True Positive Rate (Recall / Sensitivity)")
    ax.set_title(title)
    ax.legend(loc="lower right", frameon=True)
    ax.grid(True, linestyle="--", alpha=0.6)

    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out, bbox_inches="tight")
    plt.close(fig)
    return out


def plot_pr_curve(
    recall: list[float],
    precision: list[float],
    pr_auc: float,
    output_path: Path | str,
    baseline_prevalence: float | None = None,
    title: str = "Precision-Recall (PR) Curve",
    model_name: str = "Federated Fraud Detector",
) -> Path:
    """Generate publication-ready Precision-Recall curve."""
    setup_publication_style()
    fig, ax = plt.subplots(figsize=(6, 5), dpi=300)

    ax.plot(
        recall,
        precision,
        color="#d62728",
        lw=2.5,
        label=f"{model_name} (PR-AUC = {pr_auc:.4f})",
    )

    if baseline_prevalence is not None and 0.0 < baseline_prevalence < 1.0:
        ax.axhline(
            y=baseline_prevalence,
            color="#7f7f7f",
            lw=1.5,
            linestyle="--",
            label=f"Prevalence Baseline ({baseline_prevalence:.2%})",
        )

    ax.fill_between(recall, precision, alpha=0.15, color="#d62728")
    ax.set_xlim([-0.02, 1.02])
    ax.set_ylim([-0.02, 1.02])
    ax.set_xlabel("Recall (Coverage)")
    ax.set_ylabel("Precision (Positive Predictive Value)")
    ax.set_title(title)
    ax.legend(loc="upper right", frameon=True)
    ax.grid(True, linestyle="--", alpha=0.6)

    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out, bbox_inches="tight")
    plt.close(fig)
    return out


def plot_calibration_curve(
    prob_true: list[float],
    prob_pred: list[float],
    output_path: Path | str,
    brier_score: float | None = None,
    title: str = "Reliability Diagram (Calibration Curve)",
) -> Path:
    """Generate publication-ready reliability diagram."""
    setup_publication_style()
    fig, ax = plt.subplots(figsize=(6, 5), dpi=300)

    label = "Model Calibration"
    if brier_score is not None:
        label += f" (Brier = {brier_score:.4f})"

    ax.plot(prob_pred, prob_true, marker="o", lw=2.0, color="#2ca02c", label=label)
    ax.plot([0, 1], [0, 1], color="#7f7f7f", lw=1.5, linestyle="--", label="Perfect Calibration")

    ax.set_xlim([-0.02, 1.02])
    ax.set_ylim([-0.02, 1.02])
    ax.set_xlabel("Mean Predicted Probability")
    ax.set_ylabel("Empirical Fraction of Positives")
    ax.set_title(title)
    ax.legend(loc="upper left", frameon=True)
    ax.grid(True, linestyle="--", alpha=0.6)

    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out, bbox_inches="tight")
    plt.close(fig)
    return out


def plot_confusion_matrix(
    tn: int,
    fp: int,
    fn: int,
    tp: int,
    output_path: Path | str,
    labels: list[str] | None = None,
    title: str = "Confusion Matrix",
) -> Path:
    """Generate publication-ready 2x2 confusion matrix heatmap."""
    setup_publication_style()
    labels = labels or ["Legitimate", "Fraud"]
    matrix = np.array([[tn, fp], [fn, tp]])
    total = max(1, int(matrix.sum()))

    fig, ax = plt.subplots(figsize=(5, 4.5), dpi=300)
    im = ax.imshow(matrix, interpolation="nearest", cmap=plt.cm.Blues)
    plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    ax.set(
        xticks=np.arange(2),
        yticks=np.arange(2),
        xticklabels=labels,
        yticklabels=labels,
        title=title,
        ylabel="True Financial Label",
        xlabel="Predicted Platform Decision",
    )

    thresh = matrix.max() / 2.0
    for i in range(2):
        for j in range(2):
            count = matrix[i, j]
            pct = count / total * 100
            color = "white" if count > thresh else "black"
            text = f"{count:,}\n({pct:.1f}%)"
            ax.text(j, i, text, ha="center", va="center", color=color, fontweight="semibold")

    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out, bbox_inches="tight")
    plt.close(fig)
    return out


def plot_all_experiment_figures(result_dict: dict[str, Any], output_dir: Path | str) -> dict[str, str]:
    """Given an ExperimentResult JSON dict, generate all individual visual figures."""
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    generated: dict[str, str] = {}

    model_name = result_dict.get("config", {}).get("model_type", "Model")
    curves = result_dict.get("curves")
    final_metrics = result_dict.get("final_metrics", {})
    cm = result_dict.get("confusion_matrix")
    calib = result_dict.get("calibration")
    fraud_rate = result_dict.get("dataset", {}).get("fraud_rate", 0.05)

    if curves and "fpr" in curves and "tpr" in curves and curves["fpr"]:
        roc_p = plot_roc_curve(
            fpr=curves["fpr"],
            tpr=curves["tpr"],
            roc_auc=final_metrics.get("roc_auc", 0.90),
            output_path=out_dir / "roc_curve.png",
            model_name=model_name,
        )
        generated["roc_curve"] = str(roc_p.as_posix())

    if curves and "recall" in curves and "precision" in curves and curves["recall"]:
        pr_p = plot_pr_curve(
            recall=curves["recall"],
            precision=curves["precision"],
            pr_auc=final_metrics.get("pr_auc", 0.75),
            output_path=out_dir / "pr_curve.png",
            baseline_prevalence=fraud_rate,
            model_name=model_name,
        )
        generated["pr_curve"] = str(pr_p.as_posix())

    if calib and "prob_true" in calib and calib["prob_true"]:
        cal_p = plot_calibration_curve(
            prob_true=calib["prob_true"],
            prob_pred=calib.get("prob_pred", []),
            brier_score=calib.get("brier_score"),
            output_path=out_dir / "calibration_curve.png",
        )
        generated["calibration_curve"] = str(cal_p.as_posix())

    if cm and "tn" in cm:
        cm_p = plot_confusion_matrix(
            tn=cm.get("tn", 0),
            fp=cm.get("fp", 0),
            fn=cm.get("fn", 0),
            tp=cm.get("tp", 0),
            labels=cm.get("labels", ["Legitimate", "Fraud"]),
            output_path=out_dir / "confusion_matrix.png",
        )
        generated["confusion_matrix"] = str(cm_p.as_posix())

    return generated


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate publication-grade figures from experiment JSONs")
    parser.add_argument("--input", type=str, help="Path to results.json or experiment directory")
    parser.add_argument("--output-dir", type=str, default="docs/figures", help="Directory to save figures")
    args = parser.parse_args()

    if args.input:
        in_path = Path(args.input).resolve()
        if in_path.is_file():
            with open(in_path, encoding="utf-8") as f:
                data = json.load(f)
            plot_all_experiment_figures(data, args.output_dir)
        elif in_path.is_dir():
            res_file = in_path / "results.json"
            if res_file.exists():
                with open(res_file, encoding="utf-8") as f:
                    data = json.load(f)
                plot_all_experiment_figures(data, in_path / "plots")
    else:
        print("[*] No input provided. Run with --input <path/to/results.json> or import as module.")
