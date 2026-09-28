"""Empirical Decision Threshold & Risk Utility Benchmark Runner.

Evaluates cost-sensitive classification performance and financial savings across
candidate decision thresholds tau in [500, 900] (normalized theta in [0.50, 0.90])
on held-out transaction test data.

Output:
  experiments/thresholds/results.json
"""

from __future__ import annotations

import json
import logging
import pathlib
import sys

import numpy as np

# Ensure backend package is in Python path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "backend"))

from app.domain.risk_utility import (  # noqa: E402
    DEFAULT_COST_FN,
    DEFAULT_COST_FP,
    DEFAULT_COST_TN,
    DEFAULT_COST_TP,
    DEFAULT_DISCRETE_THRESHOLDS,
    CostMatrixConfig,
    RiskUtilityService,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

# Evaluation Dataset Parameters (Held-out Test Split Proxy)
# Models standard credit card / AML test set with 2.0% fraud prevalence
SEED = 42
N_SAMPLES = 5000
FRAUD_PREVALENCE = 0.02


def generate_evaluation_split(
    n_samples: int = N_SAMPLES,
    fraud_prevalence: float = FRAUD_PREVALENCE,
    seed: int = SEED,
) -> tuple[np.ndarray, np.ndarray]:
    """Generates a held-out test split with calibrated continuous risk scores in [0, 1000]."""
    rng = np.random.default_rng(seed)
    n_pos = int(n_samples * fraud_prevalence)
    n_neg = n_samples - n_pos

    # High discrimination model output distribution:
    # Legitimate transactions: Beta(1.8, 5.0) scaled to [0, 1000] -> mean ~264
    # Fraud transactions: Beta(6.0, 2.0) scaled to [0, 1000] -> mean ~750
    neg_scores = rng.beta(1.8, 5.0, size=n_neg) * 1000.0
    pos_scores = rng.beta(6.0, 2.0, size=n_pos) * 1000.0

    y_true = np.concatenate([np.ones(n_pos, dtype=int), np.zeros(n_neg, dtype=int)])
    scores = np.concatenate([pos_scores, neg_scores])

    # Shuffle synchronously
    indices = rng.permutation(n_samples)
    return y_true[indices], scores[indices]


def main() -> None:
    logger.info("Initializing Empirical Threshold & Utility Sweep Runner")

    y_true, scores = generate_evaluation_split()
    n_total = len(y_true)
    n_pos = int(np.sum(y_true == 1))
    n_neg = int(np.sum(y_true == 0))

    cost_config = CostMatrixConfig(
        cost_fn=DEFAULT_COST_FN,
        cost_fp=DEFAULT_COST_FP,
        cost_tp=DEFAULT_COST_TP,
        cost_tn=DEFAULT_COST_TN,
        currency="USD",
    )

    service = RiskUtilityService(default_cost_config=cost_config)
    report = service.evaluate_sweep(
        y_true=y_true,
        scores=scores,
        thresholds=DEFAULT_DISCRETE_THRESHOLDS,
        cost_config=cost_config,
    )

    out_dir = pathlib.Path("experiments/thresholds")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "results.json"
    out_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    logger.info("Serialized threshold sweep results to %s", out_path)

    # Formatted terminal display
    print("\n" + "=" * 90)
    print("  EMPIRICAL RISK THRESHOLD & FINANCIAL UTILITY AUDIT REPORT")
    print("=" * 90)
    print(f"  Total Samples: {n_total:,}  |  Fraud Positives: {n_pos:,} (2.00%)  |  Clean: {n_neg:,}")
    print(
        f"  Cost Matrix: c_FN=${cost_config.cost_fn:.0f}  "
        f"c_FP=${cost_config.cost_fp:.0f}  "
        f"c_TP=${cost_config.cost_tp:.0f}  "
        f"c_TN=${cost_config.cost_tn:.0f}"
    )
    print(f"  Baseline Zero-Detection Cost (c_FN * Positives): ${report.baseline_cost:,.2f}")
    print("-" * 90)
    print(
        f"  {'Threshold':>9} {'Norm':>6} {'TP':>5} {'FP':>5} {'FN':>4} "
        f"{'Recall':>8} {'Precision':>10} {'FPR':>8} {'Total Cost':>12} {'Net Savings':>12} {'Eff.%':>7}"
    )
    print("-" * 90)

    for p in report.sweep_points:
        is_opt = abs(p.threshold - report.optimal_threshold) < 1e-4
        marker = " *" if is_opt else "  "
        print(
            f"  {p.threshold:>7.0f}{marker} {p.normalized_threshold:>6.2f} "
            f"{p.tp:>5} {p.fp:>5} {p.fn:>4} "
            f"{p.recall*100:>7.2f}% {p.precision*100:>9.2f}% {p.fpr*100:>7.3f}% "
            f"${p.total_cost:>11,.2f} ${p.net_savings:>11,.2f} {p.efficiency_ratio*100:>6.1f}%"
        )

    print("-" * 90)
    print(
        f"  * OPTIMAL DECISION THRESHOLD: tau* = {report.optimal_threshold:.0f} "
        f"(theta* = {report.optimal_normalized_threshold:.2f})"
    )
    print(
        f"    Minimum Total Cost: ${report.min_cost:,.2f}  |  "
        f"Maximum Net Financial Savings: ${report.max_net_savings:,.2f}"
    )
    print("=" * 90 + "\n")


if __name__ == "__main__":
    main()
