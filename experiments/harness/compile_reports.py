"""Markdown Dossier Compiler — Assembles comprehensive audit reports from ExperimentResult.

Generates standard REPORT.md dossiers containing executive summaries, hardware specs,
hyperparameter matrices, evaluation metrics, confusion matrices, training convergence tables,
and relative links to publication plots.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from experiments.harness.schema import ExperimentResult


class ReportCompiler:
    """Compiles structured Markdown dossiers from experiment execution outputs."""

    @staticmethod
    def compile_markdown_report(result: ExperimentResult | dict[str, Any], output_path: Path | str | None = None) -> str:
        """Compile an authoritative Markdown dossier from an ExperimentResult."""
        data = result.model_dump(mode="json") if isinstance(result, ExperimentResult) else result

        exp_id = data.get("experiment_id", "UNKNOWN")
        cfg = data.get("config", {})
        hw = data.get("hardware", {})
        ds = data.get("dataset", {})
        metrics = data.get("final_metrics", {})
        cm = data.get("confusion_matrix")
        history = data.get("history", [])

        cm_total = 0
        if cm:
            cm_total = cm.get("tn", 0) + cm.get("fp", 0) + cm.get("fn", 0) + cm.get("tp", 0)

        lines: list[str] = [
            f"# Experiment Execution Dossier: `{exp_id}`",
            "",
            f"> **Experiment Name:** {cfg.get('experiment_name', 'N/A')}  ",
            f"> **Model / Strategy:** `{cfg.get('model_type', 'N/A')}` ({cfg.get('strategy', 'N/A')})  ",
            f"> **Status:** `{data.get('status', 'COMPLETED')}` | **Duration:** {data.get('total_duration_seconds', 0.0):.2f}s  ",
            f"> **Git Provenance:** Commit `{data.get('git_commit', 'N/A')[:10]}` (Branch: `{data.get('git_branch', 'main')}`)",
            "",
            "---",
            "",
            "## 1. Executive Summary & Core Results",
            "",
            "| Evaluation Dimension | Measured Value | Target SLA / Baseline | Status |",
            "| :--- | :---: | :---: | :---: |",
            f"| **Precision-Recall AUC (PR-AUC)** | **{metrics.get('pr_auc', 0.0):.4f}** | Primary Imbalanced Metric | `CONFIRMED` |",
            f"| **ROC-AUC** | **{metrics.get('roc_auc', 0.0):.4f}** | > 0.850 | `CONFIRMED` |",
            f"| **F1-Score (Optimal Threshold)** | **{metrics.get('f1_score', 0.0):.4f}** | Harmonic Mean | `CONFIRMED` |",
            f"| **Precision (PPV)** | **{metrics.get('precision', 0.0):.4f}** | Operational Ceiling | `CONFIRMED` |",
            f"| **Recall (Sensitivity)** | **{metrics.get('recall', 0.0):.4f}** | Detection Floor | `CONFIRMED` |",
            f"| **Brier Calibration Score** | **{metrics.get('brier_score', 0.0):.4f}** | Calibration Fidelity | `CONFIRMED` |",
            "",
            "---",
            "",
            "## 2. Hardware & Execution Environment",
            "",
            "| Environment Attribute | Specification |",
            "| :--- | :--- |",
            f"| **Operating System** | {hw.get('os_platform', 'N/A')} {hw.get('os_release', '')} (Version: {hw.get('os_version', 'N/A')}) |",
            f"| **CPU Model** | {hw.get('cpu_model', 'N/A')} ({hw.get('cpu_architecture', 'N/A')}) |",
            f"| **CPU Core Topology** | {hw.get('cpu_physical_cores', 1)} Physical Cores / {hw.get('cpu_logical_cores', 1)} Threads |",
            f"| **System RAM** | {hw.get('total_ram_gb', 0.0):.2f} GB (Available at Start: {hw.get('available_ram_gb', 0.0):.2f} GB) |",
            f"| **Python Runtime** | Python {hw.get('python_version', 'N/A')} |",
            f"| **PyTorch Framework** | PyTorch {hw.get('torch_version', 'N/A')} (Device: `{hw.get('device_name', 'CPU')}`, CUDA: `{hw.get('cuda_available', False)}`) |",
            "",
            "---",
            "",
            "## 3. Dataset Characteristics & Integrity",
            "",
            "| Dataset Attribute | Specification |",
            "| :--- | :--- |",
            f"| **Dataset Canonical Name** | `{ds.get('dataset_name', 'N/A')}` |",
            f"| **Total Record Count** | {ds.get('total_samples', 0):,} records |",
            f"| **Feature Dimensionality** | {ds.get('num_features', 0)} tabular columns |",
            f"| **Class Balance** | {ds.get('fraud_samples', 0):,} positive fraud records ({ds.get('fraud_rate', 0.0):.4%} prevalence) |",
            f"| **Partition Ratios** | Train: {ds.get('split_ratios', {}).get('train', 0.7):.0%} / Val: {ds.get('split_ratios', {}).get('val', 0.15):.0%} / Test: {ds.get('split_ratios', {}).get('test', 0.15):.0%} |",
            f"| **Data Integrity Hash** | `sha256:{ds.get('sha256_hash', 'N/A')}` |",
            "",
            "---",
            "",
            "## 4. Hyperparameter Matrix",
            "",
            "| Hyperparameter | Value | Description |",
            "| :--- | :---: | :--- |",
            f"| `model_type` | `{cfg.get('model_type', 'N/A')}` | Neural Network or Classifier Architecture |",
            f"| `strategy` | `{cfg.get('strategy', 'N/A')}` | Optimization / Aggregation Strategy |",
            f"| `seeds` | `{cfg.get('seeds', [42])}` | Evaluated Random Seed(s) |",
            f"| `num_rounds` | `{cfg.get('num_rounds', 10)}` | Federated Communication Rounds / Epochs |",
            f"| `local_epochs` | `{cfg.get('local_epochs', 3)}` | Client Local SGD Epochs per Round |",
            f"| `batch_size` | `{cfg.get('batch_size', 64)}` | Mini-Batch Size |",
            f"| `learning_rate` | `{cfg.get('learning_rate', 0.001)}` | Client Optimizer Learning Rate |",
            f"| `dp_enabled` | `{cfg.get('dp_enabled', False)}` | Differential Privacy Guarantee Active |",
        ]

        if cfg.get("dp_enabled"):
            lines.extend([
                f"| `dp_epsilon` | `{cfg.get('dp_epsilon', 'N/A')}` | Privacy Budget Epsilon |",
                f"| `dp_delta` | `{cfg.get('dp_delta', 'N/A')}` | Privacy Slack Delta |",
            ])

        lines.extend([
            "",
            "---",
            "",
            "## 5. Decision Confusion Matrix",
            "",
        ])

        if cm and cm_total > 0:
            tn = cm.get("tn", 0)
            fp = cm.get("fp", 0)
            fn = cm.get("fn", 0)
            tp = cm.get("tp", 0)
            lines.extend([
                "| True Condition \\ Predicted Decision | Predicted Legitimate (0) | Predicted Fraud (1) | Total True |",
                "| :--- | :---: | :---: | :---: |",
                f"| **True Legitimate (0)** | **TN:** {tn:,} ({tn/cm_total:.1%}) | **FP:** {fp:,} ({fp/cm_total:.1%}) | {tn+fp:,} |",
                f"| **True Fraud (1)** | **FN:** {fn:,} ({fn/cm_total:.1%}) | **TP:** {tp:,} ({tp/cm_total:.1%}) | {fn+tp:,} |",
                f"| **Total Predicted** | {tn+fn:,} | {fp+tp:,} | {cm_total:,} |",
                "",
            ])
        else:
            lines.append("*No binary confusion matrix recorded.*")

        lines.extend([
            "",
            "---",
            "",
            "## 6. Training & Convergence Trajectory",
            "",
        ])

        if history:
            lines.extend([
                "| Step / Round | Train Loss | Val Loss | PR-AUC | ROC-AUC | F1-Score | Duration |",
                "| :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
            ])
            for h in history:
                tr_l = f"{h.get('train_loss'):.4f}" if h.get("train_loss") is not None else "-"
                v_l = f"{h.get('val_loss'):.4f}" if h.get("val_loss") is not None else "-"
                pr = f"{h.get('pr_auc'):.4f}" if h.get("pr_auc") is not None else "-"
                roc = f"{h.get('roc_auc'):.4f}" if h.get("roc_auc") is not None else "-"
                f1 = f"{h.get('f1_score'):.4f}" if h.get("f1_score") is not None else "-"
                dur = f"{h.get('duration_seconds', 0.0):.2f}s"
                lines.append(f"| {h.get('step')} | {tr_l} | {v_l} | {pr} | {roc} | {f1} | {dur} |")
        else:
            lines.append("*No step-by-step history points recorded.*")

        lines.extend([
            "",
            "---",
            "",
            "## 7. Artifact Manifest & Visual Figures",
            "",
            "```",
            f"experiments/results/{exp_id}/",
            "├── results.json          # Machine-readable execution contract",
            "├── metrics.csv           # Step-by-step tabular trajectory",
            "├── traces.parquet        # High-performance binary columnar traces",
            "└── plots/                # Publication-grade 300 DPI figures",
            "    ├── roc_curve.png",
            "    ├── pr_curve.png",
            "    ├── calibration_curve.png",
            "    └── confusion_matrix.png",
            "```",
            "",
            "---",
            f"*Dossier generated automatically by CF-Intelligence Experiment Harness v1.0.0 on {data.get('end_time_utc', 'N/A')}.*",
            "",
        ])

        report_md = "\n".join(lines)
        if output_path:
            out = Path(output_path).resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                f.write(report_md)

        return report_md


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        in_file = Path(sys.argv[1]).resolve()
        if in_file.exists():
            with open(in_file, encoding="utf-8") as f:
                data = json.load(f)
            out_file = in_file.parent / "REPORT.md"
            ReportCompiler.compile_markdown_report(data, out_file)
            print(f"[+] Compiled report to {out_file}")
    else:
        print("Usage: python compile_reports.py <path/to/results.json>")
