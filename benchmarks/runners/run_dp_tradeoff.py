"""Differential Privacy Privacy-Utility Frontier Benchmark (Opacus DP-SGD).

Executes the canonical DP-SGD evaluation pipeline:
  - Canonical synthetic fraud dataset: N=20,000 transactions, 15 features, 2% fraud rate
  - PyTorch Opacus PrivacyEngine(accountant='prv') with per-sample clipping (C=1.0) and Gaussian mechanism
  - Noise multipliers σ ∈ {3.0, 2.0, 1.0, 0.5, 0.0} across seeds [42, 123, 456]
  - Numerical Privacy Random Variables (PRV) accounting (Gopi et al., 2021) directly from Opacus
  - Fixed-noise-multiplier sweep reporting mean ± sample standard deviation (ddof=1)
  - Non-private baseline evaluated under identical neural architecture and schedule

Results are written to:
  experiments/dp_evaluation/dp_sweep_results.json
  benchmarks/results/raw/dp_privacy_utility_tradeoff.json
  experiments/dp_evaluation/audit_dossier.md
  docs/figures/benchmark_privacy_utility.png
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "backend"))
sys.path.insert(0, str(_REPO_ROOT))

from experiments.dp_evaluation.run_dp_noise_sweep import (  # noqa: E402
    DEFAULT_SEEDS,
    DEFAULT_SIGMAS,
    DELTA,
    MAX_GRAD_NORM,
    run_canonical_dp_benchmark,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    """Run canonical DP benchmark and write all artifacts."""
    logger.info("Canonical Opacus DP benchmark starting — σ=%s, seeds=%s", DEFAULT_SIGMAS, DEFAULT_SEEDS)
    suite = run_canonical_dp_benchmark(
        sigmas=DEFAULT_SIGMAS,
        seeds=DEFAULT_SEEDS,
        delta=DELTA,
        max_grad_norm=MAX_GRAD_NORM,
        base_dir=_REPO_ROOT,
    )
    n = len(suite.tradeoff_points)
    base_pr = suite.tradeoff_points[-1]["pr_auc"]
    hi_dp_pr = suite.tradeoff_points[0]["pr_auc"]
    logger.info(
        "Canonical DP benchmark complete: %d tradeoff points, Baseline PR-AUC=%.4f, High-Noise (σ=3.0) PR-AUC=%.4f",
        n, base_pr, hi_dp_pr,
    )


if __name__ == "__main__":
    main()

