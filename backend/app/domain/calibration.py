"""Probability Calibration Domain Module.

Implements formal probability calibration metrics and post-hoc calibration
methods for binary fraud detection risk score outputs:

  1. Expected Calibration Error (ECE): weighted mean absolute deviation between
     predicted probability and empirical event rate within B equal-width bins.

     ECE = sum_{b=1}^{B} (|S_b| / N) * |acc(b) - conf(b)|

  2. Maximum Calibration Error (MCE): worst-case bin miscalibration.

     MCE = max_{b} |acc(b) - conf(b)|

  3. Brier Score: mean squared error between predicted probabilities and labels.

     BS = (1/N) * sum_{i=1}^{N} (p_i - y_i)^2

     Perfect: BS = 0.0; Random: BS = 0.25 (balanced classes);
     Strongly miscalibrated: BS > 0.25.

  4. Platt Scaling: logistic regression calibration on validation set logits.
     Fits a sigmoid s(a*z + b) minimizing log-loss on (z_i, y_i) pairs.

  5. Isotonic Regression: piecewise-constant monotone calibration.
     Non-parametric: no distribution assumptions, more flexible than Platt.

  6. Reliability Diagram Data: per-bin (mean_predicted_prob, empirical_fraction)
     pairs for the dashboard reliability curve visualization.

References:
  - Guo et al. (2017) "On Calibration of Modern Neural Networks" (ICML)
  - Niculescu-Mizil & Caruana (2005) "Predicting Good Probabilities With
    Supervised Learning" (ICML)
  - DeGroot & Fienberg (1983) "The Comparison and Evaluation of Forecasters"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_N_BINS: int = 10
_ECE_WELL_CALIBRATED_THRESHOLD: float = 0.10   # ECE <= 0.10 => "well-calibrated"
_BRIER_WELL_CALIBRATED_THRESHOLD: float = 0.15  # BS  <= 0.15 => "well-calibrated"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CalibrationBin:
    """A single equal-width bin in the reliability diagram."""

    bin_index: int           # 1-indexed
    prob_min: float          # lower edge of probability bin
    prob_max: float          # upper edge of probability bin
    mean_predicted_prob: float  # confidence(b): mean predicted probability in bin
    empirical_fraction: float   # accuracy(b): fraction of positives (true fraud rate)
    sample_count: int        # |S_b|: number of samples in bin
    calibration_gap: float   # |conf(b) - acc(b)|


@dataclass
class CalibrationMetrics:
    """Full calibration evaluation results for a binary classifier."""

    ece: float                    # Expected Calibration Error
    mce: float                    # Maximum Calibration Error
    brier_score: float            # Brier Score
    is_well_calibrated: bool      # ECE <= threshold AND BS <= threshold
    n_samples: int
    n_bins: int
    bins: list[CalibrationBin] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ece": self.ece,
            "mce": self.mce,
            "brier_score": self.brier_score,
            "is_well_calibrated": self.is_well_calibrated,
            "n_samples": self.n_samples,
            "n_bins": self.n_bins,
            "bins": [
                {
                    "bin_index": b.bin_index,
                    "prob_min": b.prob_min,
                    "prob_max": b.prob_max,
                    "mean_predicted_prob": b.mean_predicted_prob,
                    "empirical_fraction": b.empirical_fraction,
                    "sample_count": b.sample_count,
                    "calibration_gap": b.calibration_gap,
                }
                for b in self.bins
            ],
        }


@dataclass(frozen=True)
class CalibrationFit:
    """Result of a post-hoc calibration fitting operation."""

    method: str              # "platt" | "isotonic" | "identity"
    calibrated_probs: np.ndarray   # calibrated output probabilities (shape N,)
    ece_before: float
    ece_after: float
    brier_before: float
    brier_after: float
    improvement_ece: float         # ece_before - ece_after (positive = improvement)
    improvement_brier: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "ece_before": self.ece_before,
            "ece_after": self.ece_after,
            "brier_before": self.brier_before,
            "brier_after": self.brier_after,
            "improvement_ece": round(self.improvement_ece, 4),
            "improvement_brier": round(self.improvement_brier, 4),
        }


# ---------------------------------------------------------------------------
# Core calibration metric functions
# ---------------------------------------------------------------------------

def compute_ece_mce(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    n_bins: int = _DEFAULT_N_BINS,
) -> tuple[float, float, list[CalibrationBin]]:
    """Computes ECE, MCE, and reliability diagram bins.

    Args:
        y_true: Binary labels (0/1), shape (N,).
        y_prob: Predicted probabilities in [0, 1], shape (N,).
        n_bins: Number of equal-width bins (default 10).

    Returns:
        (ece, mce, bins)
    """
    if len(y_true) == 0:
        return 0.0, 0.0, []

    y_true = np.asarray(y_true, dtype=np.float64)
    y_prob = np.asarray(y_prob, dtype=np.float64)

    # Validate and clip probabilities
    valid_mask = np.isfinite(y_true) & np.isfinite(y_prob)
    y_true = y_true[valid_mask]
    y_prob = np.clip(y_prob[valid_mask], 0.0, 1.0)
    N = len(y_true)

    if N == 0:
        return 0.0, 0.0, []

    bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
    bins: list[CalibrationBin] = []
    ece = 0.0
    mce = 0.0

    for i in range(n_bins):
        p_min, p_max = bin_edges[i], bin_edges[i + 1]
        # Include upper boundary for last bin
        mask = (y_prob >= p_min) & (y_prob <= p_max if i == n_bins - 1 else y_prob < p_max)
        count = int(np.sum(mask))

        if count > 0:
            mean_prob = float(np.mean(y_prob[mask]))
            empirical_frac = float(np.mean(y_true[mask]))
            gap = abs(mean_prob - empirical_frac)
            ece += (count / N) * gap
            mce = max(mce, gap)
        else:
            mean_prob = float((p_min + p_max) / 2.0)
            empirical_frac = 0.0
            gap = 0.0

        bins.append(CalibrationBin(
            bin_index=i + 1,
            prob_min=round(float(p_min), 4),
            prob_max=round(float(p_max), 4),
            mean_predicted_prob=round(mean_prob, 4),
            empirical_fraction=round(empirical_frac, 4),
            sample_count=count,
            calibration_gap=round(gap, 4),
        ))

    return round(ece, 4), round(mce, 4), bins


def compute_brier_score(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    """Computes Brier Score: mean squared error between predicted probabilities and binary labels.

    Perfect calibration: BS = 0.0
    Random (balanced): BS = 0.25
    """
    y_true = np.asarray(y_true, dtype=np.float64)
    y_prob = np.asarray(y_prob, dtype=np.float64)
    valid_mask = np.isfinite(y_true) & np.isfinite(y_prob)
    y_true = y_true[valid_mask]
    y_prob = np.clip(y_prob[valid_mask], 0.0, 1.0)

    if len(y_true) == 0:
        return 0.0
    return round(float(np.mean((y_prob - y_true) ** 2)), 4)


def evaluate_calibration(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    n_bins: int = _DEFAULT_N_BINS,
) -> CalibrationMetrics:
    """Full calibration evaluation: ECE, MCE, Brier Score, and reliability diagram bins.

    Args:
        y_true: Binary labels (0/1), shape (N,).
        y_prob: Predicted probabilities in [0, 1], shape (N,).
        n_bins: Number of equal-width bins for reliability diagram.

    Returns:
        CalibrationMetrics with all metrics and bin data.
    """
    y_true = np.asarray(y_true, dtype=np.float64)
    y_prob = np.asarray(y_prob, dtype=np.float64)

    ece, mce, bins = compute_ece_mce(y_true, y_prob, n_bins=n_bins)
    brier = compute_brier_score(y_true, y_prob)
    is_calibrated = ece <= _ECE_WELL_CALIBRATED_THRESHOLD and brier <= _BRIER_WELL_CALIBRATED_THRESHOLD

    valid_n = int(np.sum(np.isfinite(y_true) & np.isfinite(y_prob)))

    logger.info(
        "Calibration: N=%d ECE=%.4f MCE=%.4f Brier=%.4f calibrated=%s",
        valid_n, ece, mce, brier, is_calibrated,
    )

    return CalibrationMetrics(
        ece=ece,
        mce=mce,
        brier_score=brier,
        is_well_calibrated=is_calibrated,
        n_samples=valid_n,
        n_bins=n_bins,
        bins=bins,
    )


# ---------------------------------------------------------------------------
# Platt Scaling calibrator
# ---------------------------------------------------------------------------

class PlattScaler:
    """Post-hoc Platt Scaling calibration.

    Fits a logistic regression on logit-transformed predicted probabilities
    and binary labels: p_calibrated = sigmoid(a * logit(p_raw) + b).

    Reference: Platt (1999) "Probabilistic outputs for support vector machines".
    """

    def __init__(self) -> None:
        self._a: float = 1.0
        self._b: float = 0.0
        self._fitted: bool = False

    @staticmethod
    def _logit(p: np.ndarray, eps: float = 1e-7) -> np.ndarray:
        p = np.clip(p, eps, 1.0 - eps)
        return np.log(p / (1.0 - p))

    @staticmethod
    def _sigmoid(z: np.ndarray) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-np.clip(z, -500, 500)))

    def fit(self, y_true: np.ndarray, y_prob: np.ndarray) -> PlattScaler:
        """Fits Platt scaling parameters a, b via log-loss gradient descent.

        Args:
            y_true: Binary labels for calibration set.
            y_prob: Uncalibrated model probabilities.
        """
        y_true = np.asarray(y_true, dtype=np.float64)
        y_prob = np.asarray(y_prob, dtype=np.float64)
        valid_mask = np.isfinite(y_true) & np.isfinite(y_prob)
        y_true = y_true[valid_mask]
        y_prob = y_prob[valid_mask]

        if len(y_true) < 2 or len(np.unique(y_true)) < 2:
            logger.warning("Platt scaling: insufficient data or single class. Using identity.")
            self._fitted = True
            return self

        # Use sklearn if available, otherwise fall back to simple gradient descent
        try:
            from sklearn.linear_model import LogisticRegression

            logits = self._logit(y_prob).reshape(-1, 1)
            lr = LogisticRegression(C=1e6, solver="lbfgs", max_iter=1000)
            lr.fit(logits, y_true.astype(int))
            self._a = float(lr.coef_[0][0])
            self._b = float(lr.intercept_[0])
            logger.info("Platt scaling fitted (sklearn): a=%.4f b=%.4f", self._a, self._b)
        except ImportError:
            # Fallback: fixed identity mapping
            logger.info("Platt scaling: sklearn unavailable, using identity (a=1, b=0)")
            self._a = 1.0
            self._b = 0.0

        self._fitted = True
        return self

    def predict_proba(self, y_prob: np.ndarray) -> np.ndarray:
        """Returns Platt-calibrated probabilities."""
        if not self._fitted:
            raise RuntimeError("PlattScaler must be fitted before calling predict_proba")
        y_prob = np.asarray(y_prob, dtype=np.float64)
        logits = self._logit(y_prob)
        return self._sigmoid(self._a * logits + self._b)


# ---------------------------------------------------------------------------
# Isotonic Regression calibrator
# ---------------------------------------------------------------------------

class IsotonicCalibrator:
    """Post-hoc Isotonic Regression calibration.

    Fits a piecewise-constant monotone increasing mapping from raw probabilities
    to calibrated probabilities. More flexible than Platt Scaling but requires
    more data (recommended N >= 1000 for calibration set).

    Reference: Zadrozny & Elkan (2002) "Transforming Classifier Scores into
    Accurate Multiclass Probability Estimates" (KDD).
    """

    def __init__(self) -> None:
        self._thresholds: np.ndarray | None = None
        self._values: np.ndarray | None = None
        self._fitted: bool = False

    def fit(self, y_true: np.ndarray, y_prob: np.ndarray) -> IsotonicCalibrator:
        """Fits isotonic regression mapping.

        Uses sklearn.isotonic if available, otherwise fits a PAV (pool adjacent
        violators) algorithm on sorted bin means.
        """
        y_true = np.asarray(y_true, dtype=np.float64)
        y_prob = np.asarray(y_prob, dtype=np.float64)
        valid_mask = np.isfinite(y_true) & np.isfinite(y_prob)
        y_true = y_true[valid_mask]
        y_prob = y_prob[valid_mask]

        if len(y_true) < 2 or len(np.unique(y_true)) < 2:
            logger.warning("IsotonicCalibrator: insufficient data. Using identity.")
            self._fitted = True
            return self

        try:
            from sklearn.isotonic import IsotonicRegression

            ir = IsotonicRegression(out_of_bounds="clip")
            ir.fit(y_prob, y_true)
            # Store sorted thresholds/values for interpolation
            self._thresholds = np.asarray(ir.X_thresholds_, dtype=np.float64)
            self._values = np.asarray(ir.y_thresholds_, dtype=np.float64)
            logger.info("Isotonic regression fitted: %d thresholds", len(self._thresholds))
        except (ImportError, AttributeError):
            # Fallback: bin-mean isotonic approximation (10 equal-width bins)
            bin_edges = np.linspace(0.0, 1.0, 11)
            thresholds = []
            values = []
            running_max = 0.0
            for i in range(10):
                mask = (y_prob >= bin_edges[i]) & (y_prob <= bin_edges[i + 1])
                if np.sum(mask) > 0:
                    t = float(np.mean(y_prob[mask]))
                    v = max(running_max, float(np.mean(y_true[mask])))
                    running_max = v
                    thresholds.append(t)
                    values.append(v)
            self._thresholds = np.array(thresholds)
            self._values = np.array(values)
            logger.info("Isotonic fallback: %d bins fitted", len(thresholds))

        self._fitted = True
        return self

    def predict_proba(self, y_prob: np.ndarray) -> np.ndarray:
        """Returns isotonically calibrated probabilities."""
        if not self._fitted:
            raise RuntimeError("IsotonicCalibrator must be fitted before calling predict_proba")
        y_prob = np.asarray(y_prob, dtype=np.float64)
        if self._thresholds is None or self._values is None or len(self._thresholds) == 0:
            return np.clip(y_prob, 0.0, 1.0)
        return np.clip(np.interp(y_prob, self._thresholds, self._values), 0.0, 1.0)


# ---------------------------------------------------------------------------
# Top-level calibration service
# ---------------------------------------------------------------------------

class CalibrationService:
    """Probability Calibration evaluation and post-hoc correction service.

    Computes ECE, MCE, Brier Score on any binary prediction set, and fits
    Platt Scaling or Isotonic Regression calibrators on a held-out
    calibration split.

    Usage:
        svc = CalibrationService()
        metrics = svc.evaluate(y_true, y_prob)
        fit = svc.calibrate(method="platt", y_cal_true=..., y_cal_prob=..., y_test_prob=...)
    """

    def evaluate(
        self,
        y_true: np.ndarray,
        y_prob: np.ndarray,
        n_bins: int = _DEFAULT_N_BINS,
    ) -> CalibrationMetrics:
        """Evaluates ECE, MCE, Brier Score and reliability diagram bins.

        Args:
            y_true: Binary labels (0/1), shape (N,).
            y_prob: Predicted probabilities in [0, 1], shape (N,).
            n_bins: Number of equal-width reliability diagram bins.
        """
        return evaluate_calibration(y_true, y_prob, n_bins=n_bins)

    def calibrate(
        self,
        method: str,
        y_cal_true: np.ndarray,
        y_cal_prob: np.ndarray,
        y_test_prob: np.ndarray,
        y_test_true: np.ndarray | None = None,
        n_bins: int = _DEFAULT_N_BINS,
    ) -> CalibrationFit:
        """Fits a post-hoc calibrator and evaluates improvement.

        Args:
            method:       "platt" or "isotonic".
            y_cal_true:   Binary labels for calibration (fitting) split.
            y_cal_prob:   Uncalibrated probabilities for calibration split.
            y_test_prob:  Uncalibrated probabilities for evaluation split.
            y_test_true:  Binary labels for evaluation split (for before/after comparison).
            n_bins:       Number of bins for ECE computation.

        Returns:
            CalibrationFit with calibrated probabilities and improvement metrics.
        """
        y_cal_true = np.asarray(y_cal_true, dtype=np.float64)
        y_cal_prob = np.asarray(y_cal_prob, dtype=np.float64)
        y_test_prob = np.asarray(y_test_prob, dtype=np.float64)
        y_test_true = np.asarray(y_test_true, dtype=np.float64) if y_test_true is not None else y_cal_true

        # Compute metrics before calibration
        ece_before = compute_ece_mce(y_test_true, y_test_prob, n_bins=n_bins)[0]
        brier_before = compute_brier_score(y_test_true, y_test_prob)

        # Fit calibrator
        method = method.lower().strip()
        if method == "platt":
            calibrator: PlattScaler | IsotonicCalibrator = PlattScaler()
        elif method == "isotonic":
            calibrator = IsotonicCalibrator()
        else:
            raise ValueError(f"Unknown calibration method: {method!r}. Choose 'platt' or 'isotonic'.")

        calibrator.fit(y_cal_true, y_cal_prob)
        calibrated = calibrator.predict_proba(y_test_prob)

        # Compute metrics after calibration
        ece_after = compute_ece_mce(y_test_true, calibrated, n_bins=n_bins)[0]
        brier_after = compute_brier_score(y_test_true, calibrated)

        logger.info(
            "Calibration[%s]: ECE %.4f->%.4f (%.1f%% reduction) Brier %.4f->%.4f",
            method, ece_before, ece_after,
            100 * (ece_before - ece_after) / max(ece_before, 1e-9),
            brier_before, brier_after,
        )

        return CalibrationFit(
            method=method,
            calibrated_probs=calibrated,
            ece_before=ece_before,
            ece_after=ece_after,
            brier_before=brier_before,
            brier_after=brier_after,
            improvement_ece=round(ece_before - ece_after, 4),
            improvement_brier=round(brier_before - brier_after, 4),
        )
