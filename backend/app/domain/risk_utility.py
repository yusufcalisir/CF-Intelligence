"""Cost-Sensitive Risk Utility & Empirical Threshold Optimization Domain Module.

Implements formal business objective functions, financial cost matrices,
and empirical decision threshold sweeps (500 to 900 risk score band / 0.50 to 0.90 normalized):

  1. Cost-Sensitive Financial Objective Formulation:
     Balances undetected fraud losses (False Negatives) against operational
     investigation overhead and customer friction (False Positives):

     C(tau) = c_FN * FN(tau) + c_FP * FP(tau) + c_TP * TP(tau) + c_TN * TN(tau)

     where:
       c_FN: average loss per undetected fraud (direct chargeback + penalty, e.g. $850)
       c_FP: compliance investigation overhead per false alarm (analyst review, e.g. $45)
       c_TP: operational SAR filing / account hold execution cost (e.g. $15)
       c_TN: legitimate automated transaction clearing cost ($0)

  2. Net Financial Utility (Illicit Loss Averted):
     S(tau) = C_baseline - C(tau)
     where C_baseline = c_FN * P represents the zero-detection default.

  3. Empirical Threshold Sweeps:
     Evaluates confusion matrices (TP, FP, TN, FN), Precision, Recall, FPR, FNR,
     F1, F2, F0.5, and Net Financial Utility across discrete decision thresholds
     tau in [500, 900] (normalized theta in [0.50, 0.90]) on held-out test splits.

  4. Optimal Operating Threshold Determination:
     tau* = argmin_{tau} C(tau) == argmax_{tau} S(tau)

References:
  - Elkan (2001) "The Foundations of Cost-Sensitive Learning" (IJCAI)
  - Hand (2009) "Measuring classifier performance: a coherent alternative to AUC"
  - EU AI Act Article 14 / SR 11-7 Operational Threshold Governance
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default Constants
# ---------------------------------------------------------------------------

DEFAULT_DISCRETE_THRESHOLDS: tuple[float, ...] = (
    500.0, 550.0, 600.0, 650.0, 700.0, 750.0, 800.0, 850.0, 900.0,
)
DEFAULT_COST_FN: float = 850.0   # $850 average chargeback / fraud loss
DEFAULT_COST_FP: float = 45.0    # $45 compliance analyst review + customer friction
DEFAULT_COST_TP: float = 15.0    # $15 SAR filing & provisional restriction processing
DEFAULT_COST_TN: float = 0.0     # $0 automated clean pass-through


# ---------------------------------------------------------------------------
# Value Objects & Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CostMatrixConfig:
    """Financial cost parameters associated with classification outcomes."""

    cost_fn: float = DEFAULT_COST_FN
    cost_fp: float = DEFAULT_COST_FP
    cost_tp: float = DEFAULT_COST_TP
    cost_tn: float = DEFAULT_COST_TN
    currency: str = "USD"

    def __post_init__(self) -> None:
        if self.cost_fn < 0.0:
            raise ValueError(f"cost_fn must be non-negative, got {self.cost_fn}")
        if self.cost_fp < 0.0:
            raise ValueError(f"cost_fp must be non-negative, got {self.cost_fp}")
        if self.cost_tp < 0.0:
            raise ValueError(f"cost_tp must be non-negative, got {self.cost_tp}")
        if self.cost_tn < 0.0:
            raise ValueError(f"cost_tn must be non-negative, got {self.cost_tn}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "cost_fn": self.cost_fn,
            "cost_fp": self.cost_fp,
            "cost_tp": self.cost_tp,
            "cost_tn": self.cost_tn,
            "currency": self.currency,
        }


@dataclass(frozen=True)
class ThresholdMetrics:
    """Performance and financial metrics evaluated at a discrete decision threshold."""

    threshold: float                 # integer-scale score (e.g. 750.0 in [0, 1000])
    normalized_threshold: float      # normalized threshold in [0.0, 1.0] (e.g. 0.75)
    tp: int                          # True Positives
    fp: int                          # False Positives
    tn: int                          # True Negatives
    fn: int                          # False Negatives
    precision: float                 # TP / (TP + FP)
    recall: float                    # TP / (TP + FN)
    fpr: float                       # FP / (FP + TN)
    fnr: float                       # FN / (TP + FN)
    f1_score: float                  # 2 * P * R / (P + R)
    f2_score: float                  # Recall-weighted F-beta (beta=2.0)
    f05_score: float                 # Precision-weighted F-beta (beta=0.5)
    total_cost: float                # c_FN*FN + c_FP*FP + c_TP*TP + c_TN*TN
    net_savings: float               # Baseline_Cost - Total_Cost
    efficiency_ratio: float          # Net Savings / Baseline Cost

    def to_dict(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "normalized_threshold": self.normalized_threshold,
            "tp": self.tp,
            "fp": self.fp,
            "tn": self.tn,
            "fn": self.fn,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "fpr": round(self.fpr, 6),
            "fnr": round(self.fnr, 6),
            "f1_score": round(self.f1_score, 4),
            "f2_score": round(self.f2_score, 4),
            "f05_score": round(self.f05_score, 4),
            "total_cost": round(self.total_cost, 2),
            "net_savings": round(self.net_savings, 2),
            "efficiency_ratio": round(self.efficiency_ratio, 4),
        }


@dataclass
class ThresholdSweepReport:
    """Consolidated report across a spectrum of candidate decision thresholds."""

    optimal_threshold: float
    optimal_normalized_threshold: float
    min_cost: float
    max_net_savings: float
    baseline_cost: float
    n_samples: int
    n_positives: int
    n_negatives: int
    cost_config: CostMatrixConfig
    sweep_points: list[ThresholdMetrics] = field(default_factory=list)
    evaluated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def get_point(self, threshold: float) -> ThresholdMetrics | None:
        """Find metrics for an exact threshold."""
        for p in self.sweep_points:
            if abs(p.threshold - threshold) < 1e-4:
                return p
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "optimal_threshold": self.optimal_threshold,
            "optimal_normalized_threshold": self.optimal_normalized_threshold,
            "min_cost": round(self.min_cost, 2),
            "max_net_savings": round(self.max_net_savings, 2),
            "baseline_cost": round(self.baseline_cost, 2),
            "n_samples": self.n_samples,
            "n_positives": self.n_positives,
            "n_negatives": self.n_negatives,
            "cost_config": self.cost_config.to_dict(),
            "evaluated_at": self.evaluated_at,
            "sweep_points": [p.to_dict() for p in self.sweep_points],
        }


# ---------------------------------------------------------------------------
# Pure Calculation Helpers
# ---------------------------------------------------------------------------

def calculate_confusion_matrix(
    y_true: np.ndarray,
    scores: np.ndarray,
    threshold: float,
) -> tuple[int, int, int, int]:
    """Computes (TP, FP, TN, FN) for given continuous scores and threshold.

    Handles scores on either [0, 1] probability scale or [0, 1000] integer scale:
      If threshold > 1.0, compares directly against raw scores.
      If threshold <= 1.0 and scores max > 1.0, normalizes threshold or scores.
    """
    y_true_arr = np.asarray(y_true, dtype=np.int32)
    scores_arr = np.asarray(scores, dtype=np.float64)

    if len(y_true_arr) != len(scores_arr):
        raise ValueError(
            f"Dimension mismatch: y_true length {len(y_true_arr)} != scores length {len(scores_arr)}"
        )

    # Determine scale alignment
    if threshold > 1.0 and np.all(scores_arr <= 1.0) and len(scores_arr) > 0:
        # threshold is [0, 1000] scale, scores are [0, 1] scale -> scale scores up
        effective_scores = scores_arr * 1000.0
        effective_thresh = threshold
    elif threshold <= 1.0 and np.any(scores_arr > 1.0):
        # threshold is [0, 1] scale, scores are [0, 1000] scale -> scale threshold up
        effective_scores = scores_arr
        effective_thresh = threshold * 1000.0
    else:
        effective_scores = scores_arr
        effective_thresh = threshold

    predicted_pos = effective_scores >= effective_thresh

    tp = int(np.sum((predicted_pos == 1) & (y_true_arr == 1)))
    fp = int(np.sum((predicted_pos == 1) & (y_true_arr == 0)))
    tn = int(np.sum((predicted_pos == 0) & (y_true_arr == 0)))
    fn = int(np.sum((predicted_pos == 0) & (y_true_arr == 1)))

    return tp, fp, tn, fn


def compute_threshold_metrics(
    y_true: np.ndarray,
    scores: np.ndarray,
    threshold: float,
    cost_config: CostMatrixConfig | None = None,
) -> ThresholdMetrics:
    """Computes full classification and financial utility metrics at a threshold."""
    if cost_config is None:
        cost_config = CostMatrixConfig()

    tp, fp, tn, fn = calculate_confusion_matrix(y_true, scores, threshold)
    total_pos = tp + fn
    total_neg = fp + tn

    precision = float(tp / (tp + fp)) if (tp + fp) > 0 else 0.0
    recall = float(tp / total_pos) if total_pos > 0 else 0.0
    fpr = float(fp / total_neg) if total_neg > 0 else 0.0
    fnr = float(fn / total_pos) if total_pos > 0 else 0.0

    # F-measures
    denom_f1 = precision + recall
    f1_score = (2.0 * precision * recall / denom_f1) if denom_f1 > 0 else 0.0

    denom_f2 = (4.0 * precision) + recall
    f2_score = (5.0 * precision * recall / denom_f2) if denom_f2 > 0 else 0.0

    denom_f05 = (0.25 * precision) + recall
    f05_score = (1.25 * precision * recall / denom_f05) if denom_f05 > 0 else 0.0

    # Financial Cost Objective
    total_cost = (
        cost_config.cost_fn * fn
        + cost_config.cost_fp * fp
        + cost_config.cost_tp * tp
        + cost_config.cost_tn * tn
    )

    baseline_cost = cost_config.cost_fn * total_pos
    net_savings = baseline_cost - total_cost
    efficiency_ratio = (net_savings / baseline_cost) if baseline_cost > 0 else 0.0

    # Normalized threshold in [0, 1]
    norm_thresh = threshold / 1000.0 if threshold > 1.0 else threshold

    return ThresholdMetrics(
        threshold=threshold,
        normalized_threshold=round(norm_thresh, 4),
        tp=tp,
        fp=fp,
        tn=tn,
        fn=fn,
        precision=precision,
        recall=recall,
        fpr=fpr,
        fnr=fnr,
        f1_score=f1_score,
        f2_score=f2_score,
        f05_score=f05_score,
        total_cost=total_cost,
        net_savings=net_savings,
        efficiency_ratio=efficiency_ratio,
    )


# ---------------------------------------------------------------------------
# Domain Service: RiskUtilityService
# ---------------------------------------------------------------------------

class RiskUtilityService:
    """Domain service for cost-sensitive threshold analysis and operating point optimization."""

    def __init__(self, default_cost_config: CostMatrixConfig | None = None) -> None:
        self._default_cost_config = default_cost_config or CostMatrixConfig()

    def evaluate_sweep(
        self,
        y_true: np.ndarray,
        scores: np.ndarray,
        thresholds: tuple[float, ...] | list[float] | None = None,
        cost_config: CostMatrixConfig | None = None,
    ) -> ThresholdSweepReport:
        """Executes full empirical threshold sweep across candidate cutoffs.

        Args:
            y_true: True binary labels (0 = clean, 1 = fraud).
            scores: Model prediction scores (in [0, 1] or [0, 1000]).
            thresholds: Discrete candidate thresholds (defaults to 500 to 900 in step 50).
            cost_config: Cost matrix parameters.

        Returns:
            ThresholdSweepReport with optimal threshold and per-cutoff metrics.
        """
        cfg = cost_config or self._default_cost_config
        thresh_list = tuple(thresholds) if thresholds is not None else DEFAULT_DISCRETE_THRESHOLDS

        y_true_arr = np.asarray(y_true, dtype=np.int32)
        scores_arr = np.asarray(scores, dtype=np.float64)

        n_samples = len(y_true_arr)
        n_pos = int(np.sum(y_true_arr == 1))
        n_neg = int(np.sum(y_true_arr == 0))
        baseline_cost = cfg.cost_fn * n_pos

        points: list[ThresholdMetrics] = []
        for t in thresh_list:
            m = compute_threshold_metrics(y_true_arr, scores_arr, t, cfg)
            points.append(m)

        if not points:
            # Fallback for empty sweep
            return ThresholdSweepReport(
                optimal_threshold=750.0,
                optimal_normalized_threshold=0.75,
                min_cost=baseline_cost,
                max_net_savings=0.0,
                baseline_cost=baseline_cost,
                n_samples=n_samples,
                n_positives=n_pos,
                n_negatives=n_neg,
                cost_config=cfg,
                sweep_points=[],
            )

        # Optimal threshold minimizes total cost (equivalently maximizes net savings)
        best_point = min(points, key=lambda p: p.total_cost)

        logger.info(
            "Threshold sweep completed: N=%d (P=%d, N=%d). Optimal threshold=%.1f "
            "(Norm=%.2f, MinCost=$%.2f, MaxSavings=$%.2f, Recall=%.2f%%, Precision=%.2f%%)",
            n_samples, n_pos, n_neg,
            best_point.threshold, best_point.normalized_threshold,
            best_point.total_cost, best_point.net_savings,
            best_point.recall * 100.0, best_point.precision * 100.0,
        )

        return ThresholdSweepReport(
            optimal_threshold=best_point.threshold,
            optimal_normalized_threshold=best_point.normalized_threshold,
            min_cost=best_point.total_cost,
            max_net_savings=best_point.net_savings,
            baseline_cost=baseline_cost,
            n_samples=n_samples,
            n_positives=n_pos,
            n_negatives=n_neg,
            cost_config=cfg,
            sweep_points=points,
        )

    def recommend_bayes_threshold(
        self,
        cost_fn: float | None = None,
        cost_fp: float | None = None,
    ) -> float:
        """Computes theoretical Bayesian risk threshold from the cost ratio.

        Under standard cost-sensitive decision theory (Elkan 2001):
            theta* = c_FP / (c_FP + c_FN)
            tau* = theta* * 1000

        If c_FN = 850 and c_FP = 45:
            theta* = 45 / (45 + 850) = 45 / 895 = 0.0503 (prob)
            tau* on risk scale: ~500 to 750 depending on baseline prevalence.
        """
        c_fn = cost_fn if cost_fn is not None else self._default_cost_config.cost_fn
        c_fp = cost_fp if cost_fp is not None else self._default_cost_config.cost_fp

        if (c_fp + c_fn) <= 0.0:
            return 750.0

        theta_star = c_fp / (c_fp + c_fn)
        return float(np.clip(theta_star * 1000.0, 100.0, 950.0))
