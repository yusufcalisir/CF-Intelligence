"""Inference Service with Probability Calibration Integration.

Thin application-layer service that wraps model predictions and applies
post-hoc calibration (Platt Scaling or Isotonic Regression) to the raw
probability outputs of the fraud detection model.

Responsibilities:
  - Accept raw logits/probabilities from an ML model registry or upstream scorer
  - Evaluate calibration quality (ECE, MCE, Brier Score) on a reference set
  - Fit and apply Platt Scaling or Isotonic Regression calibration
  - Return CalibrationMetrics to the monitoring router for dashboard display
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from app.domain.calibration import (
    CalibrationFit,
    CalibrationMetrics,
    CalibrationService,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InferencePrediction:
    """A single model inference result."""

    transaction_id: str
    raw_prob: float          # uncalibrated model output in [0, 1]
    calibrated_prob: float   # post-hoc calibrated probability
    risk_label: int          # binary decision: 0 = legitimate, 1 = fraud
    threshold: float         # decision threshold applied


class InferenceService:
    """Application-layer inference service with calibration integration.

    Provides:
      - evaluate_calibration: assess calibration quality of current predictions
      - apply_calibration: fit and apply post-hoc calibration to raw probabilities
      - predict: return calibrated predictions for new transactions

    Typical usage in the monitoring/observability pipeline:
        svc = InferenceService()
        metrics = svc.evaluate_calibration(y_true_val, y_prob_val)
        fit = svc.apply_calibration("platt", y_cal_true, y_cal_prob, y_test_prob)
    """

    def __init__(self, decision_threshold: float = 0.5) -> None:
        self._threshold = decision_threshold
        self._calib_service = CalibrationService()
        self._fitted_calibrated_probs: np.ndarray | None = None
        self._last_metrics: CalibrationMetrics | None = None

    def evaluate_calibration(
        self,
        y_true: np.ndarray,
        y_prob: np.ndarray,
        n_bins: int = 10,
    ) -> CalibrationMetrics:
        """Evaluates ECE, MCE, Brier Score and reliability diagram bins.

        Args:
            y_true: Binary labels for validation/test set.
            y_prob: Model-predicted probabilities for same set.
            n_bins: Number of reliability diagram bins.

        Returns:
            CalibrationMetrics with ECE, MCE, Brier Score, bins.
        """
        metrics = self._calib_service.evaluate(
            np.asarray(y_true, dtype=np.float64),
            np.asarray(y_prob, dtype=np.float64),
            n_bins=n_bins,
        )
        self._last_metrics = metrics
        logger.info(
            "Calibration evaluation: ECE=%.4f MCE=%.4f Brier=%.4f well_calibrated=%s",
            metrics.ece, metrics.mce, metrics.brier_score, metrics.is_well_calibrated,
        )
        return metrics

    def apply_calibration(
        self,
        method: str,
        y_cal_true: np.ndarray,
        y_cal_prob: np.ndarray,
        y_test_prob: np.ndarray,
        y_test_true: np.ndarray | None = None,
        n_bins: int = 10,
    ) -> CalibrationFit:
        """Fits and applies post-hoc probability calibration.

        Args:
            method:       "platt" or "isotonic".
            y_cal_true:   Labels for calibration (fitting) split.
            y_cal_prob:   Raw probabilities for calibration split.
            y_test_prob:  Raw probabilities to calibrate (evaluation split).
            y_test_true:  Labels for evaluation split (optional; used for comparison).
            n_bins:       Number of bins for ECE comparison.

        Returns:
            CalibrationFit with calibrated probabilities and improvement metrics.
        """
        fit = self._calib_service.calibrate(
            method=method,
            y_cal_true=np.asarray(y_cal_true, dtype=np.float64),
            y_cal_prob=np.asarray(y_cal_prob, dtype=np.float64),
            y_test_prob=np.asarray(y_test_prob, dtype=np.float64),
            y_test_true=np.asarray(y_test_true, dtype=np.float64) if y_test_true is not None else None,
            n_bins=n_bins,
        )
        self._fitted_calibrated_probs = fit.calibrated_probs
        logger.info(
            "Post-hoc calibration [%s]: ECE improvement=%.4f Brier improvement=%.4f",
            method, fit.improvement_ece, fit.improvement_brier,
        )
        return fit

    def predict(
        self,
        raw_probs: np.ndarray,
        calibrated_probs: np.ndarray | None = None,
        transaction_ids: list[str] | None = None,
    ) -> list[InferencePrediction]:
        """Generates InferencePrediction records for a batch of transactions.

        Args:
            raw_probs:         Uncalibrated model probabilities, shape (N,).
            calibrated_probs:  Post-hoc calibrated probabilities (optional).
                               If None, raw_probs are used as calibrated.
            transaction_ids:   Optional transaction IDs (generated if not provided).

        Returns:
            List of InferencePrediction with risk labels.
        """
        raw_probs = np.asarray(raw_probs, dtype=np.float64)
        cal_probs = (
            np.asarray(calibrated_probs, dtype=np.float64)
            if calibrated_probs is not None
            else raw_probs.copy()
        )
        N = len(raw_probs)
        ids = transaction_ids or [f"txn_{i:08d}" for i in range(N)]

        predictions = []
        for i in range(N):
            predictions.append(InferencePrediction(
                transaction_id=ids[i],
                raw_prob=round(float(raw_probs[i]), 6),
                calibrated_prob=round(float(cal_probs[i]), 6),
                risk_label=int(cal_probs[i] >= self._threshold),
                threshold=self._threshold,
            ))
        return predictions

    @property
    def last_calibration_metrics(self) -> CalibrationMetrics | None:
        """Returns the most recent calibration evaluation result."""
        return self._last_metrics
