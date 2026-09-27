"""Phase 15.1 — DP-SGD Noise Calibration & Privacy-Utility Frontier Benchmark.

Executes the full Phase 15.1 evaluation pipeline:
  - Rényi DP moments accounting for σ ∈ {0.5, 1.0, 1.5, 2.0}
  - Federation rounds T ∈ {5, 10, 20, 50}
  - Utility measurement (PR-AUC, ROC-AUC) under calibrated Gaussian noise
  - Sigma calibration: find σ* s.t. ε(σ*, T=50, δ=1e-5) ≤ 2.0
  - Pareto frontier serialisation and 4-panel visualization

Results are written to:
  experiments/dp_evaluation/dp_sweep_results.json
  benchmarks/results/raw/dp_privacy_utility_tradeoff.json
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
    DELTA,
    ROUNDS_GRID,
    SEED,
    SIGMA_GRID,
    SUBSAMPLING_Q,
    TARGET_EPSILON,
    run_dp_evaluation,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    """Run Phase 15.1 DP benchmark and write all artifacts."""
    logger.info("Phase 15.1 DP benchmark starting — σ=%s, T=%s", SIGMA_GRID, ROUNDS_GRID)
    suite = run_dp_evaluation(
        sigmas=SIGMA_GRID,
        rounds_list=ROUNDS_GRID,
        delta=DELTA,
        target_epsilon=TARGET_EPSILON,
        q=SUBSAMPLING_Q,
        seed=SEED,
        base_dir=_REPO_ROOT,
    )
    n = len(suite.configurations)
    sigma_star = f"{suite.calibrated_sigma:.4f}" if suite.calibrated_sigma else "N/A"
    logger.info(
        "Phase 15.1 complete: %d configurations, calibrated σ*=%s "
        "for ε≤%.1f (δ=%.0e, T=%d)",
        n, sigma_star, TARGET_EPSILON, DELTA, max(ROUNDS_GRID),
    )


if __name__ == "__main__":
    main()
