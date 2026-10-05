"""Differential Privacy Privacy-Utility Frontier Benchmark (Opacus DP-SGD).

Executes a scientifically rigorous, reproducible DP-SGD benchmark:
1. Canonical synthetic fraud dataset: N=20,000 transactions, 15 features, 2% fraud rate
   (16,000 train / 4,000 test with 84 test fraud cases for robust support).
2. Genuine per-sample DP-SGD via PyTorch Opacus PrivacyEngine(accountant='prv'):
   - Per-sample gradient clipping (C = 1.0)
   - Calibrated Gaussian mechanism across noise multipliers σ ∈ {0.0, 0.5, 1.0, 2.0, 3.0}
   - Explicit Numerical Privacy Random Variables (PRV) accounting (Gopi et al., 2021)
   - Fixed-noise-multiplier sweep via make_private() (NOT make_private_with_epsilon)
3. Multi-seed evaluation across 3 seeds (42, 123, 456) reporting mean ± sample standard deviation (ddof=1).
4. Matching non-private baseline (σ = 0.0) evaluated under identical architecture and schedule.
5. Archival separation of legacy 10-feature centroid prototype results.

Outputs
-------
  experiments/dp_evaluation/dp_sweep_results.json
  benchmarks/results/raw/dp_privacy_utility_tradeoff.json
  experiments/dp_evaluation/audit_dossier.md
  docs/figures/benchmark_privacy_utility.png
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np
import torch
import torch.nn as nn
from opacus import PrivacyEngine
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.preprocessing import StandardScaler
from torch.utils.data import DataLoader, TensorDataset

# Repository paths
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "backend"))
sys.path.insert(0, str(_REPO_ROOT))

from app.application.services.model_service import FraudDetectionModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical benchmark configuration
# ---------------------------------------------------------------------------

N_SAMPLES: int = 20_000
N_FEATURES: int = 15
FRAUD_RATE: float = 0.02
DATA_SEED: int = 42

DEFAULT_SEEDS: list[int] = [42, 123, 456]
DEFAULT_SIGMAS: list[float] = [3.0, 2.0, 1.0, 0.5, 0.0]  # σ=3.0 first, σ=0.0 last

EPOCHS: int = 5
BATCH_SIZE: int = 256
LEARNING_RATE: float = 1e-3
MAX_GRAD_NORM: float = 1.0
DELTA: float = 1e-5

# Historical reference points from legacy 10-feature centroid prototype (commit f0705141)
LEGACY_PROTOTYPE_POINTS: list[dict[str, Any]] = [
    {
        "noise_multiplier": 3.0,
        "epsilon": 1.858,
        "delta": 1e-05,
        "clip_norm": 1.0,
        "pr_auc": 0.1963,
        "roc_auc": 0.8301,
        "classification": "LEGACY_NON_DP_SGD_PROTOTYPE",
    },
    {
        "noise_multiplier": 2.0,
        "epsilon": 2.839,
        "delta": 1e-05,
        "clip_norm": 1.0,
        "pr_auc": 0.0722,
        "roc_auc": 0.7470,
        "classification": "LEGACY_NON_DP_SGD_PROTOTYPE",
    },
    {
        "noise_multiplier": 1.2,
        "epsilon": 4.910,
        "delta": 1e-05,
        "clip_norm": 1.0,
        "pr_auc": 0.3081,
        "roc_auc": 0.8944,
        "classification": "LEGACY_NON_DP_SGD_PROTOTYPE",
    },
    {
        "noise_multiplier": 0.8,
        "epsilon": 7.696,
        "delta": 1e-05,
        "clip_norm": 1.0,
        "pr_auc": 0.2833,
        "roc_auc": 0.9244,
        "classification": "LEGACY_NON_DP_SGD_PROTOTYPE",
    },
    {
        "noise_multiplier": 0.4,
        "epsilon": 17.323,
        "delta": 1e-05,
        "clip_norm": 1.0,
        "pr_auc": 0.6205,
        "roc_auc": 0.9687,
        "classification": "LEGACY_NON_DP_SGD_PROTOTYPE",
    },
    {
        "noise_multiplier": 0.0,
        "epsilon": "infinity (non-private)",
        "delta": 1e-05,
        "clip_norm": 1.0,
        "pr_auc": 0.6272,
        "roc_auc": 0.9684,
        "classification": "LEGACY_NON_DP_SGD_PROTOTYPE",
    },
]


# ---------------------------------------------------------------------------
# Dataset generator
# ---------------------------------------------------------------------------

def generate_synthetic_fraud_dataset(
    n_samples: int = N_SAMPLES,
    n_features: int = N_FEATURES,
    fraud_rate: float = FRAUD_RATE,
    seed: int = DATA_SEED,
) -> tuple[np.ndarray, np.ndarray]:
    """Generate a reproducible synthetic fraud dataset.

    Legitimate transactions: standard multivariate normal.
    Fraud transactions: structured signal shifted by +1.8 on first 5 features.
    """
    rng = np.random.default_rng(seed)
    n_fraud = int(n_samples * fraud_rate)
    n_legit = n_samples - n_fraud

    X_legit = rng.standard_normal((n_legit, n_features))
    y_legit = np.zeros(n_legit, dtype=np.float32)

    shift = np.zeros(n_features)
    shift[:5] = 1.8
    X_fraud = rng.standard_normal((n_fraud, n_features)) + shift
    y_fraud = np.ones(n_fraud, dtype=np.float32)

    X = np.vstack([X_legit, X_fraud]).astype(np.float32)
    y = np.concatenate([y_legit, y_fraud])

    perm = rng.permutation(n_samples)
    return X[perm], y[perm]


# ---------------------------------------------------------------------------
# Single run training & evaluation
# ---------------------------------------------------------------------------

def train_and_evaluate_single_run(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray,
    sigma: float,
    seed: int,
    epochs: int = EPOCHS,
    batch_size: int = BATCH_SIZE,
    lr: float = LEARNING_RATE,
    max_grad_norm: float = MAX_GRAD_NORM,
    delta: float = DELTA,
) -> dict[str, Any]:
    """Train single neural model with or without Opacus DP-SGD."""
    torch.manual_seed(seed)
    np.random.seed(seed)

    input_dim = X_train.shape[1]
    model = FraudDetectionModel(input_dim=input_dim, dp_compatible=True)
    optimizer = torch.optim.Adam(cast("Any", model.parameters()), lr=lr)
    criterion = nn.BCELoss()

    ds = TensorDataset(torch.tensor(X_train), torch.tensor(y_train))
    loader = DataLoader(ds, batch_size=batch_size, shuffle=True)

    t0 = time.perf_counter()

    eval_model: Any
    if sigma > 0.0:
        privacy_engine = PrivacyEngine(accountant="prv")
        try:
            priv_res: Any = privacy_engine.make_private(
                module=cast("Any", model),
                optimizer=optimizer,
                data_loader=loader,
                noise_multiplier=sigma,
                max_grad_norm=max_grad_norm,
                grad_sample_mode="ew",
            )
        except Exception:
            priv_res = privacy_engine.make_private(
                module=cast("Any", model),
                optimizer=optimizer,
                data_loader=loader,
                noise_multiplier=sigma,
                max_grad_norm=max_grad_norm,
                grad_sample_mode="hooks",
            )
        model_p: Any = priv_res[0]
        opt_p: Any = priv_res[1]
        loader_p: Any = priv_res[2]
        try:
            model_p.train()
            for _ep in range(epochs):
                for xb, yb in loader_p:
                    opt_p.zero_grad()
                    pred = model_p(xb)
                    loss = criterion(pred, yb)
                    loss.backward()
                    opt_p.step()

            accounted_eps = float(privacy_engine.get_epsilon(delta=delta))
            eval_model = getattr(model_p, "_module", model_p)
            steps = len(loader_p) * epochs
        finally:
            if hasattr(model_p, "remove_hooks"):
                with contextlib.suppress(Exception):
                    model_p.remove_hooks()
    else:
        model.train()
        for _ep in range(epochs):
            for xb, yb in loader:
                optimizer.zero_grad()
                pred = model(xb)
                loss = criterion(pred, yb)
                loss.backward()
                optimizer.step()

        accounted_eps = float("inf")
        eval_model = model
        steps = len(loader) * epochs

    elapsed = time.perf_counter() - t0

    # Evaluation on held-out test partition
    eval_model.eval()
    with torch.no_grad():
        x_te = torch.tensor(X_test)
        scores = eval_model(x_te).squeeze().numpy()

    pr_auc = float(average_precision_score(y_test, scores))
    roc_auc = float(roc_auc_score(y_test, scores))

    return {
        "seed": seed,
        "sigma": sigma,
        "epsilon": accounted_eps,
        "delta": delta,
        "clip_norm": max_grad_norm,
        "epochs": epochs,
        "batch_size": batch_size,
        "optimizer_steps": steps,
        "sample_rate_q": round(batch_size / len(X_train), 5),
        "pr_auc": round(pr_auc, 6),
        "roc_auc": round(roc_auc, 6),
        "runtime_seconds": round(elapsed, 3),
        "dp_enabled": sigma > 0.0,
    }


# ---------------------------------------------------------------------------
# Suite result structure
# ---------------------------------------------------------------------------

@dataclass
class DPSweepResults:
    """Canonical multi-seed DP benchmark result suite."""

    dataset_metadata: dict[str, Any]
    seeds: list[int]
    tradeoff_points: list[dict[str, Any]]
    configurations: list[dict[str, Any]]
    frontier_summary: list[dict[str, Any]]
    legacy_prototype_metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "2.1.0",
            "benchmark_id": "dp_privacy_utility_tradeoff_opacus",
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "privacy_engine": "PyTorch Opacus (PrivacyEngine, accountant='prv')",
            "accountant": "PRVAccountant (Privacy Random Variables, Gopi et al. 2021)",
            "sweep_type": "fixed_noise_multiplier",
            "dataset": self.dataset_metadata,
            "seeds": self.seeds,
            "tradeoff_points": self.tradeoff_points,
            "configurations": self.configurations,
            "frontier_summary": self.frontier_summary,
            "legacy_prototype_metadata": self.legacy_prototype_metadata,
        }


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def run_canonical_dp_benchmark(
    sigmas: list[float] = DEFAULT_SIGMAS,
    seeds: list[int] = DEFAULT_SEEDS,
    n_samples: int = N_SAMPLES,
    n_features: int = N_FEATURES,
    fraud_rate: float = FRAUD_RATE,
    data_seed: int = DATA_SEED,
    epochs: int = EPOCHS,
    batch_size: int = BATCH_SIZE,
    lr: float = LEARNING_RATE,
    max_grad_norm: float = MAX_GRAD_NORM,
    delta: float = DELTA,
    base_dir: Path | None = None,
) -> DPSweepResults:
    """Execute complete canonical Opacus DP-SGD benchmark suite."""
    if base_dir is None:
        base_dir = _REPO_ROOT

    logger.info("Generating canonical synthetic dataset: N=%d, features=%d, fraud_rate=%.2f",
                n_samples, n_features, fraud_rate)
    X, y = generate_synthetic_fraud_dataset(
        n_samples=n_samples, n_features=n_features, fraud_rate=fraud_rate, seed=data_seed
    )

    split = int(0.8 * len(X))
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    scaler = StandardScaler()
    X_train = scaler.fit_transform(X_train).astype(np.float32)
    X_test = scaler.transform(X_test).astype(np.float32)

    n_train_fraud = int(np.sum(y_train))
    n_test_fraud = int(np.sum(y_test))

    dataset_metadata = {
        "name": "synthetic_banking",
        "n_samples": n_samples,
        "n_features": n_features,
        "fraud_rate": fraud_rate,
        "data_seed": data_seed,
        "train_samples": len(X_train),
        "test_samples": len(X_test),
        "train_fraud_count": n_train_fraud,
        "test_fraud_count": n_test_fraud,
        "test_fraud_support": f"{n_test_fraud} positive instances (2.1% prevalence)",
        "preprocessing": "StandardScaler(fit_train, transform_test)",
    }

    logger.info("Dataset prepared: train=%d (fraud=%d), test=%d (fraud=%d)",
                len(X_train), n_train_fraud, len(X_test), n_test_fraud)

    all_configurations: list[dict[str, Any]] = []
    tradeoff_points: list[dict[str, Any]] = []
    frontier_summary: list[dict[str, Any]] = []

    # 1. First run Non-Private Baseline to establish baseline PR-AUC
    logger.info("--- Running Non-Private Baseline (σ=0.0) across seeds %s ---", seeds)
    baseline_runs: list[dict[str, Any]] = []
    for s in seeds:
        res = train_and_evaluate_single_run(
            X_train, y_train, X_test, y_test,
            sigma=0.0, seed=s, epochs=epochs, batch_size=batch_size,
            lr=lr, max_grad_norm=max_grad_norm, delta=delta,
        )
        baseline_runs.append(res)
        all_configurations.append(res)
        logger.info("Baseline seed %d: PR-AUC=%.4f, ROC-AUC=%.4f", s, res["pr_auc"], res["roc_auc"])

    baseline_mean_pr = float(np.mean([r["pr_auc"] for r in baseline_runs]))
    baseline_std_pr = float(np.std([r["pr_auc"] for r in baseline_runs], ddof=1 if len(baseline_runs) > 1 else 0))
    baseline_mean_roc = float(np.mean([r["roc_auc"] for r in baseline_runs]))
    baseline_std_roc = float(np.std([r["roc_auc"] for r in baseline_runs], ddof=1 if len(baseline_runs) > 1 else 0))

    logger.info(
        "Non-Private Baseline established: PR-AUC = %.4f ± %.4f, ROC-AUC = %.4f ± %.4f",
        baseline_mean_pr,
        baseline_std_pr,
        baseline_mean_roc,
        baseline_std_roc,
    )

    # 2. Run DP sweeps for all sigmas in order
    for sigma in sigmas:
        if sigma == 0.0:
            runs = baseline_runs
            eps_val: float | str = "infinity (non-private)"
            eps_num = float("inf")
        else:
            logger.info("--- Running Opacus DP-SGD σ=%.1f across seeds %s ---", sigma, seeds)
            runs = []
            for s in seeds:
                res = train_and_evaluate_single_run(
                    X_train, y_train, X_test, y_test,
                    sigma=sigma, seed=s, epochs=epochs, batch_size=batch_size,
                    lr=lr, max_grad_norm=max_grad_norm, delta=delta,
                )
                runs.append(res)
                all_configurations.append(res)
                logger.info("σ=%.1f seed %d: ε=%.4f, PR-AUC=%.4f, ROC-AUC=%.4f [%.2fs]",
                            sigma, s, res["epsilon"], res["pr_auc"], res["roc_auc"], res["runtime_seconds"])
            eps_num = runs[0]["epsilon"]
            eps_val = round(eps_num, 4)

        prs = [r["pr_auc"] for r in runs]
        rocs = [r["roc_auc"] for r in runs]
        mean_pr = float(np.mean(prs))
        std_pr = float(np.std(prs, ddof=1 if len(prs) > 1 else 0))
        mean_roc = float(np.mean(rocs))
        std_roc = float(np.std(rocs, ddof=1 if len(rocs) > 1 else 0))

        abs_loss = baseline_mean_pr - mean_pr
        rel_loss = abs_loss / baseline_mean_pr if baseline_mean_pr > 0 else 0.0

        pt_dict = {
            "noise_multiplier": sigma,
            "epsilon": eps_val,
            "delta": delta,
            "clip_norm": max_grad_norm,
            "pr_auc": round(mean_pr, 4),
            "roc_auc": round(mean_roc, 4),
            "pr_auc_std": round(std_pr, 4),
            "roc_auc_std": round(std_roc, 4),
            "pr_auc_min": round(float(np.min(prs)), 4),
            "pr_auc_max": round(float(np.max(prs)), 4),
            "absolute_loss": round(abs_loss, 4),
            "relative_loss_pct": round(rel_loss * 100, 2),
            "per_seed_pr_auc": {str(r["seed"]): r["pr_auc"] for r in runs},
            "std_definition": "sample standard deviation across training seeds (ddof=1)",
        }
        tradeoff_points.append(pt_dict)

        frontier_summary.append({
            "noise_multiplier": sigma,
            "epsilon": eps_val,
            "pr_auc_mean": round(mean_pr, 4),
            "pr_auc_std": round(std_pr, 4),
            "roc_auc_mean": round(mean_roc, 4),
            "relative_loss_pct": round(rel_loss * 100, 2),
        })

    legacy_metadata = {
        "status": "ARCHIVED -- non-DP-SGD centroid vector prototype",
        "origin_commit": "f0705141 (2026-09-26)",
        "description": (
            "Legacy 10-feature centroid difference with single output noise vector, "
            "superseded by genuine Opacus DP-SGD neural benchmark."
        ),
        "values": list(LEGACY_PROTOTYPE_POINTS),
    }

    suite = DPSweepResults(
        dataset_metadata=dataset_metadata,
        seeds=seeds,
        tradeoff_points=tradeoff_points,
        configurations=all_configurations,
        frontier_summary=frontier_summary,
        legacy_prototype_metadata=legacy_metadata,
    )

    # Serialize artifacts
    _serialize_artifacts(suite, base_dir)

    return suite


def _serialize_artifacts(suite: DPSweepResults, base_dir: Path) -> None:
    """Save all benchmark JSON artifacts, dossier, and figures."""
    dp_dir = base_dir / "experiments" / "dp_evaluation"
    dp_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = base_dir / "benchmarks" / "results" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    fig_dir = base_dir / "docs" / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)

    result_path = dp_dir / "dp_sweep_results.json"
    raw_path = raw_dir / "dp_privacy_utility_tradeoff.json"
    dossier_path = dp_dir / "audit_dossier.md"
    fig_path = fig_dir / "benchmark_privacy_utility.png"

    data = suite.to_dict()
    result_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    raw_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _write_dossier(suite, dossier_path)
    _generate_figure(suite, fig_path)

    logger.info("Canonical artifacts serialized:")
    logger.info("  %s", result_path)
    logger.info("  %s", raw_path)
    logger.info("  %s", dossier_path)
    logger.info("  %s", fig_path)


def _write_dossier(suite: DPSweepResults, path: Path) -> None:
    """Generate Markdown audit dossier."""
    lines = [
        "# Canonical Differential Privacy Benchmark Audit Dossier (Opacus DP-SGD)",
        "",
        "**Verification Engine:** PyTorch Opacus `PrivacyEngine(accountant='prv')`  ",
        "**Privacy Accountant:** PRVAccountant (Privacy Random Variables, Gopi et al. 2021)  ",
        "**Sweep Type:** Fixed-noise-multiplier (σ ∈ {3.0, 2.0, 1.0, 0.5, 0.0}) — NOT target-ε calibration  ",
        f"**Dataset:** Synthetic Banking (N={suite.dataset_metadata['n_samples']:,}, 15 features, "
        f"prevalence={suite.dataset_metadata['fraud_rate']*100:.1f}%)  ",
        f"**Test Support:** {suite.dataset_metadata['test_fraud_count']} fraud transactions out of "
        f"{suite.dataset_metadata['test_samples']:,} test samples  ",
        f"**Seeds Evaluated:** {suite.seeds}  ",
        f"**Security Parameter:** $\\delta = {DELTA:.0e}$ ($< 1/N_{{\\text{{train}}}}$)  ",
        "**Reported ±:** Sample standard deviation across training seeds (ddof=1).  ",
        "Measures training stochasticity only. Dataset realization is fixed (data_seed=42).  ",
        "",
        "## 1. Canonical Privacy-Utility Frontier",
        "",
        "| Noise Multiplier (σ) | Accounted Epsilon (ε) | Test PR-AUC (mean ± sample std) | Test ROC-AUC | Relative Utility Loss | Regime |",
        "| :---: | :---: | :---: | :---: | :---: | :---|",
    ]

    for pt in suite.tradeoff_points:
        sigma = pt["noise_multiplier"]
        eps_str = f"ε = {pt['epsilon']:.4f}" if isinstance(pt["epsilon"], (int, float)) else str(pt["epsilon"])
        regime = "Non-Private Baseline" if sigma == 0.0 else (
            "Weak Privacy" if sigma <= 0.5 else (
                "Balanced Tradeoff" if sigma <= 1.0 else (
                    "Strong Privacy" if sigma <= 2.0 else "High Privacy Regime"
                )
            )
        )
        loss_pct_str = "-" if sigma == 0.0 else f"-{pt['relative_loss_pct']:.1f}%"
        lines.append(
            f"| **σ = {sigma:.1f}** | {eps_str} | **{pt['pr_auc']:.4f} ± {pt['pr_auc_std']:.4f}** | "
            f"{pt['roc_auc']:.4f} ± {pt['roc_auc_std']:.4f} | "
            f"{loss_pct_str} | {regime} |"
        )

    lines.extend([
        "",
        "## 2. Per-Seed Verification Breakdown",
        "",
        "| σ | Seed 42 PR-AUC | Seed 123 PR-AUC | Seed 456 PR-AUC | Min | Max | Std |",
        "| :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ])

    for pt in suite.tradeoff_points:
        seeds_pr = pt["per_seed_pr_auc"]
        lines.append(
            f"| σ = {pt['noise_multiplier']:.1f} | {seeds_pr.get('42', 0.0):.4f} | "
            f"{seeds_pr.get('123', 0.0):.4f} | {seeds_pr.get('456', 0.0):.4f} | "
            f"{pt['pr_auc_min']:.4f} | {pt['pr_auc_max']:.4f} | {pt['pr_auc_std']:.4f} |"
        )

    lines.extend([
        "",
        "## 3. Methodological Invariants Verified",
        "",
        "- **Genuine Per-Sample DP-SGD**: Evaluated through PyTorch Opacus hooks with gradient clipping norm $C=1.0$.",
        "- **Explicit PRVAccountant**: `PrivacyEngine(accountant='prv')` pins accounting method; no implicit default dependency.",
        "- **Fixed-Sigma Sweep**: Each σ value is passed as `noise_multiplier=σ` to `make_private()`. This is NOT target-ε calibration via `make_private_with_epsilon()`.",
        "- **Matching Non-Private Baseline**: Unperturbed baseline uses identical architecture, optimizer, batch size, and epochs.",
        "- **Live Accountant Tracking**: Reported ε bounds are emitted from the same `PrivacyEngine` instance that observed all 315 DPOptimizer steps (PRVAccountant, Gopi et al. 2021).",
        "- **Sample Std (ddof=1)**: Reported ± is the unbiased sample standard deviation across n=3 seeds, not population std.",
        "- **Zero Static Fixtures**: All reported points are calculated dynamically from model execution.",
        "",
        "## 4. Statistical Limitations",
        "",
        "- **3 training seeds only**: std captures training stochasticity, NOT dataset-sampling uncertainty.",
        "- **Fixed dataset realization**: All runs use the same synthetic dataset generated with `data_seed=42`.",
        "- **84 test fraud examples**: Adequate for relative trend analysis; insufficient for high-precision recall-at-threshold claims.",
        "- **Synthetic dataset**: Results reflect a controlled synthetic distribution; real-world performance may differ.",
        "",
        "## 5. Legacy Prototype Archival Reference",
        "",
        "The legacy 10-feature algebraic centroid prototype (commit `f0705141`) achieved "
        "`PR-AUC 0.6272` (baseline) and `0.1963` (at σ=3.0). These values are preserved solely "
        "as historical artifacts and are not part of the active canonical neural benchmark.",
    ])

    path.write_text("\n".join(lines), encoding="utf-8")


def _generate_figure(suite: DPSweepResults, output_path: Path) -> None:
    """Generate 4-panel publication visualization."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib unavailable — skipping figure generation")
        return

    fig, axes = plt.subplots(2, 2, figsize=(13, 9))
    fig.suptitle(
        "Canonical DP-SGD Noise Calibration & Privacy-Utility Frontier\n"
        "CF-Intelligence Architecture | PyTorch Opacus (accountant='prv') | δ=1e-5 Fixed | N=20,000",
        fontsize=12, fontweight="bold", y=0.98,
    )

    pts = sorted(suite.tradeoff_points, key=lambda x: x["noise_multiplier"])

    # Panel 1: Privacy Loss vs PR-AUC (Pareto Frontier)
    ax1 = axes[0, 0]
    dp_pts = [p for p in pts if p["noise_multiplier"] > 0]
    if dp_pts:
        eps_vals = [p["epsilon"] for p in dp_pts]
        pr_means = [p["pr_auc"] for p in dp_pts]
        pr_errs = [p["pr_auc_std"] for p in dp_pts]
        ax1.errorbar(eps_vals, pr_means, yerr=pr_errs, fmt="o-", color="#2980b9",
                     ecolor="#34495e", elinewidth=1.5, capsize=4, markersize=7,
                     label="Opacus DP-SGD (mean ± sample std, ddof=1)")
        for p in dp_pts:
            ax1.annotate(f"σ={p['noise_multiplier']:.1f}", (p["epsilon"], p["pr_auc"]),
                         textcoords="offset points", xytext=(8, -5), fontsize=8)

    base_pt = next(p for p in pts if p["noise_multiplier"] == 0.0)
    ax1.axhline(base_pt["pr_auc"], color="#27ae60", linestyle="--", linewidth=1.5,
                label=f"Non-Private Baseline ({base_pt['pr_auc']:.4f})")
    ax1.fill_between(
        [0, max([p["epsilon"] for p in dp_pts]) * 1.1] if dp_pts else [0, 1],
        base_pt["pr_auc"] - base_pt["pr_auc_std"],
        base_pt["pr_auc"] + base_pt["pr_auc_std"],
        color="#27ae60", alpha=0.15,
    )
    ax1.set_xlabel("Privacy Loss ε (lower = stronger privacy)", fontsize=9)
    ax1.set_ylabel("Test PR-AUC (higher = better utility)", fontsize=9)
    ax1.set_title("1. Privacy-Utility Frontier (ε vs PR-AUC)", fontweight="bold")
    ax1.grid(True, alpha=0.3)
    ax1.legend(fontsize=8, framealpha=0.8)

    # Panel 2: Utility Loss vs Noise Multiplier
    ax2 = axes[0, 1]
    sigmas = [p["noise_multiplier"] for p in pts]
    rel_losses = [p["relative_loss_pct"] for p in pts]
    colors = ["#27ae60" if s == 0 else "#e74c3c" for s in sigmas]
    bars = ax2.bar([f"σ={s:.1f}" for s in sigmas], rel_losses, color=colors, alpha=0.85, edgecolor="#2c3e50")
    for bar, val in zip(bars, rel_losses, strict=False):
        ax2.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1.0,
                 f"{val:.1f}%", ha="center", va="bottom", fontsize=8, fontweight="bold")
    ax2.set_ylabel("Relative PR-AUC Loss vs Baseline (%)", fontsize=9)
    ax2.set_title("2. Utility Degradation vs Noise Multiplier σ", fontweight="bold")
    ax2.set_ylim(0, max(rel_losses) * 1.2 if max(rel_losses) > 0 else 10)
    ax2.grid(True, alpha=0.3, axis="y")

    # Panel 3: ROC-AUC vs Noise Multiplier
    ax3 = axes[1, 0]
    roc_means = [p["roc_auc"] for p in pts]
    roc_errs = [p["roc_auc_std"] for p in pts]
    ax3.errorbar([p["noise_multiplier"] for p in pts], roc_means, yerr=roc_errs,
                 fmt="s-", color="#8e44ad", ecolor="#2c3e50", elinewidth=1.5,
                 capsize=4, markersize=7, label="ROC-AUC (mean ± std)")
    ax3.set_xlabel("Gaussian Noise Multiplier σ", fontsize=9)
    ax3.set_ylabel("Test ROC-AUC", fontsize=9)
    ax3.set_title("3. ROC-AUC Discriminative Power vs σ", fontweight="bold")
    ax3.grid(True, alpha=0.3)
    ax3.legend(fontsize=8, framealpha=0.8)

    # Panel 4: Per-Seed PR-AUC Convergence
    ax4 = axes[1, 1]
    seed_palette = {"42": "#e67e22", "123": "#2980b9", "456": "#16a085"}
    for seed_str, col in seed_palette.items():
        seed_prs = [p["per_seed_pr_auc"].get(seed_str, 0.0) for p in pts]
        ax4.plot([p["noise_multiplier"] for p in pts], seed_prs, "o--",
                 color=col, linewidth=1.5, markersize=6, label=f"Seed {seed_str}")
    ax4.set_xlabel("Gaussian Noise Multiplier σ", fontsize=9)
    ax4.set_ylabel("Test PR-AUC", fontsize=9)
    ax4.set_title("4. Multi-Seed Replication Trajectories", fontweight="bold")
    ax4.grid(True, alpha=0.3)
    ax4.legend(fontsize=8, framealpha=0.8)

    plt.tight_layout(rect=(0, 0, 1, 0.95))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=300)
    plt.close()
    logger.info("Saved privacy-utility visualization figure to %s", output_path)


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Canonical DP-SGD Noise Calibration & Privacy-Utility Frontier Benchmark"
    )
    parser.add_argument("--sigmas", nargs="+", type=float, default=DEFAULT_SIGMAS)
    parser.add_argument("--seeds", nargs="+", type=int, default=DEFAULT_SEEDS)
    parser.add_argument("--n-samples", type=int, default=N_SAMPLES)
    parser.add_argument("--epochs", type=int, default=EPOCHS)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--lr", type=float, default=LEARNING_RATE)
    parser.add_argument("--clip-norm", type=float, default=MAX_GRAD_NORM)
    parser.add_argument("--delta", type=float, default=DELTA)
    args = parser.parse_args()

    run_canonical_dp_benchmark(
        sigmas=args.sigmas,
        seeds=args.seeds,
        n_samples=args.n_samples,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        max_grad_norm=args.clip_norm,
        delta=args.delta,
    )


if __name__ == "__main__":
    main()
