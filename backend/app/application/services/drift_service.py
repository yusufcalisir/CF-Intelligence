"""Model Drift & Calibration Analytics Engine.

Computes Feature Drift (Kolmogorov-Smirnov 2-sample test, Wasserstein distance),
Concept Drift (Population Stability Index - PSI), Model Calibration (Brier Score, ECE),
and evaluates Automated Re-training triggers.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import stats

logger = logging.getLogger(__name__)


@dataclass
class FeatureDriftMetrics:
    """Drift metrics for a single feature."""

    feature_name: str
    ks_statistic: float
    ks_p_value: float
    wasserstein_distance: float
    psi: float
    status: str  # "STABLE", "MODERATE_DRIFT", "SEVERE_DRIFT"


@dataclass
class CalibrationBin:
    """A single bin in the model calibration reliability curve."""

    bin_index: int
    prob_min: float
    prob_max: float
    mean_predicted_prob: float
    empirical_fraud_ratio: float
    sample_count: int


@dataclass
class CalibrationReport:
    """Model calibration evaluation metrics."""

    brier_score: float
    expected_calibration_error: float
    max_calibration_error: float
    is_well_calibrated: bool
    evaluated_at: str
    bins: list[CalibrationBin] = field(default_factory=list)


@dataclass
class DriftAnalysisReport:
    """Complete drift and calibration audit report."""

    overall_status: str  # "HEALTHY", "WARNING", "CRITICAL"
    max_psi: float
    mean_ks_p_value: float
    concept_drift_psi: float
    auto_retrain_triggered: bool
    evaluated_at: str
    feature_drifts: list[FeatureDriftMetrics] = field(default_factory=list)
    calibration: CalibrationReport | None = None


class ModelDriftService:
    """Statistical analytics engine for ML feature/concept drift and calibration monitoring."""

    def __init__(
        self, psi_threshold_warning: float = 0.10, psi_threshold_critical: float = 0.20
    ) -> None:
        self.psi_threshold_warning = psi_threshold_warning
        self.psi_threshold_critical = psi_threshold_critical

    @staticmethod
    def _calculate_psi(actual: Any, expected: Any, num_bins: int = 10) -> float:
        """Calculate Population Stability Index (PSI) between actual and expected distributions."""
        if len(actual) == 0 or len(expected) == 0:
            return 0.0

        # Small-sample guard (N < 30): quantile PSI exhibits unstable behavior at N < 30
        if len(actual) < 30 or len(expected) < 30:
            logger.warning(
                "Sample size too small for reliable quantile PSI (actual=%d, expected=%d < 30). Returning 0.0.",
                len(actual),
                len(expected),
            )
            return 0.0

        # Determine bin edges from reference/expected distribution
        quantiles = np.linspace(0, 100, num_bins + 1)
        bins = np.percentile(expected, quantiles)
        bins = np.unique(bins)
        if len(bins) < 2:
            bins = np.linspace(
                min(expected.min(), actual.min()),
                max(expected.max(), actual.max()) + 1e-5,
                num_bins + 1,
            )

        # Count frequencies
        expected_counts, _ = np.histogram(expected, bins=bins)
        actual_counts, _ = np.histogram(actual, bins=bins)

        # Convert to percentages with laplace smoothing (+1e-4) to avoid division by zero
        expected_pct = (expected_counts + 1e-4) / (len(expected) + 1e-4 * len(expected_counts))
        actual_pct = (actual_counts + 1e-4) / (len(actual) + 1e-4 * len(actual_counts))

        # Sum PSI formula: (Actual% - Expected%) * ln(Actual% / Expected%)
        psi_val = float(np.sum((actual_pct - expected_pct) * np.log(actual_pct / expected_pct)))
        return max(0.0, psi_val)

    def analyze_feature_drift(
        self,
        current_data: dict[str, list[float]],
        reference_data: dict[str, list[float]],
    ) -> list[FeatureDriftMetrics]:
        """Compute Kolmogorov-Smirnov test, Wasserstein distance, and PSI for each feature."""
        results: list[FeatureDriftMetrics] = []

        for feature_name in current_data:
            if feature_name not in reference_data:
                continue

            curr_arr = np.array(current_data[feature_name], dtype=float)
            ref_arr = np.array(reference_data[feature_name], dtype=float)

            # Sanitize IEEE 754 Inf/NaN (BUG-DR-02 fix)
            curr_valid = curr_arr[np.isfinite(curr_arr)]
            ref_valid = ref_arr[np.isfinite(ref_arr)]

            if len(curr_valid) == 0 or len(ref_valid) == 0:
                continue

            # 1. Kolmogorov-Smirnov 2-sample test
            ks_res = stats.ks_2samp(curr_valid, ref_valid)
            ks_stat = float(ks_res.statistic)
            ks_p_val = float(ks_res.pvalue)

            # 2. Wasserstein Distance normalized by reference std dev (EMD / sigma)
            ref_std = max(float(np.std(ref_valid)), 1e-8)
            w_dist_raw = float(stats.wasserstein_distance(curr_valid, ref_valid))
            w_dist = w_dist_raw / ref_std

            # 3. PSI Calculation
            psi_val = self._calculate_psi(curr_valid, ref_valid)

            # Classify status
            if psi_val >= self.psi_threshold_critical or ks_p_val < 0.01:
                status_str = "SEVERE_DRIFT"
            elif psi_val >= self.psi_threshold_warning or ks_p_val < 0.05:
                status_str = "MODERATE_DRIFT"
            else:
                status_str = "STABLE"

            results.append(
                FeatureDriftMetrics(
                    feature_name=feature_name,
                    ks_statistic=round(ks_stat, 4),
                    ks_p_value=round(ks_p_val, 4),
                    wasserstein_distance=round(w_dist, 4),
                    psi=round(psi_val, 4),
                    status=status_str,
                )
            )

        return results

    def compute_calibration(
        self,
        y_true: list[int],
        y_prob: list[float],
        num_bins: int = 10,
    ) -> CalibrationReport:
        """Compute Brier Score, ECE, and reliability curve bins for model probability predictions."""
        if len(y_true) == 0 or len(y_prob) == 0 or len(y_true) != len(y_prob):
            return CalibrationReport(
                brier_score=0.0,
                expected_calibration_error=0.0,
                max_calibration_error=0.0,
                is_well_calibrated=True,
                evaluated_at=time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()),
            )

        y_true_arr = np.array(y_true, dtype=float)
        y_prob_arr = np.array(y_prob, dtype=float)

        # Sanitize IEEE 754 NaN/Inf values (BUG-DR-01 fix)
        valid_mask = np.isfinite(y_true_arr) & np.isfinite(y_prob_arr)
        if not np.any(valid_mask):
            return CalibrationReport(
                brier_score=0.0,
                expected_calibration_error=0.0,
                max_calibration_error=0.0,
                is_well_calibrated=True,
                evaluated_at=time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()),
            )

        y_true_arr = y_true_arr[valid_mask]
        y_prob_arr = y_prob_arr[valid_mask]

        # 1. Brier Score: mean squared error between probabilities and binary labels
        brier = float(np.mean((y_prob_arr - y_true_arr) ** 2))

        # 2. Calibration Bins & ECE
        bin_edges = np.linspace(0.0, 1.0, num_bins + 1)
        bins_list: list[CalibrationBin] = []
        ece = 0.0
        max_ce = 0.0
        total_samples = len(y_true_arr)

        for i in range(num_bins):
            p_min, p_max = bin_edges[i], bin_edges[i + 1]
            if i == num_bins - 1:
                mask = (y_prob_arr >= p_min) & (y_prob_arr <= p_max)
            else:
                mask = (y_prob_arr >= p_min) & (y_prob_arr < p_max)

            count = int(np.sum(mask))
            if count > 0:
                mean_prob = float(np.mean(y_prob_arr[mask]))
                empirical_ratio = float(np.mean(y_true_arr[mask]))
                abs_diff = abs(mean_prob - empirical_ratio)
                ece += (count / total_samples) * abs_diff
                max_ce = max(max_ce, abs_diff)
            else:
                mean_prob = round((p_min + p_max) / 2.0, 3)
                empirical_ratio = 0.0

            bins_list.append(
                CalibrationBin(
                    bin_index=i + 1,
                    prob_min=round(float(p_min), 2),
                    prob_max=round(float(p_max), 2),
                    mean_predicted_prob=round(mean_prob, 4),
                    empirical_fraud_ratio=round(empirical_ratio, 4),
                    sample_count=count,
                )
            )

        is_calibrated = brier <= 0.15 and ece <= 0.10

        return CalibrationReport(
            brier_score=round(brier, 4),
            expected_calibration_error=round(ece, 4),
            max_calibration_error=round(max_ce, 4),
            is_well_calibrated=is_calibrated,
            evaluated_at=time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()),
            bins=bins_list,
        )

    def run_full_drift_analysis(
        self,
        current_data: dict[str, list[float]],
        reference_data: dict[str, list[float]],
        current_scores: list[float],
        reference_scores: list[float],
        y_true: list[int] | None = None,
        y_prob: list[float] | None = None,
    ) -> DriftAnalysisReport:
        """Run comprehensive feature drift, concept drift (PSI on risk scores), and calibration audit."""
        feature_drifts = self.analyze_feature_drift(current_data, reference_data)

        # Sanitize score arrays for concept drift
        curr_scores_arr = np.array(current_scores, dtype=float)
        ref_scores_arr = np.array(reference_scores, dtype=float)
        curr_scores_valid = curr_scores_arr[np.isfinite(curr_scores_arr)]
        ref_scores_valid = ref_scores_arr[np.isfinite(ref_scores_arr)]

        # Concept drift on prediction scores
        concept_psi = self._calculate_psi(curr_scores_valid, ref_scores_valid)

        max_psi = (
            max([fd.psi for fd in feature_drifts] + [concept_psi])
            if feature_drifts
            else concept_psi
        )
        mean_p_val = (
            float(np.mean([fd.ks_p_value for fd in feature_drifts])) if feature_drifts else 1.0
        )

        # Benjamini-Hochberg FDR adjustment on feature p-values to control FWER (~40.1% -> 5%)
        significant_features = 0
        if feature_drifts:
            raw_p_values = [fd.ks_p_value for fd in feature_drifts]
            m = len(raw_p_values)
            sorted_indices = np.argsort(raw_p_values)
            for rank, idx in enumerate(sorted_indices, start=1):
                bh_threshold = (rank / m) * 0.05  # alpha = 0.05
                if raw_p_values[idx] <= bh_threshold:
                    significant_features += 1

        # Calibration
        calibration_rpt = None
        if y_true is not None and y_prob is not None:
            calibration_rpt = self.compute_calibration(y_true, y_prob)

        # Classify overall system status
        if (
            max_psi >= self.psi_threshold_critical
            or concept_psi >= self.psi_threshold_critical
            or significant_features >= 2
        ):
            overall_status = "CRITICAL"
        elif (
            max_psi >= self.psi_threshold_warning
            or concept_psi >= self.psi_threshold_warning
            or significant_features == 1
        ):
            overall_status = "WARNING"
        else:
            overall_status = "HEALTHY"

        auto_trigger = overall_status == "CRITICAL"

        return DriftAnalysisReport(
            overall_status=overall_status,
            max_psi=round(max_psi, 4),
            mean_ks_p_value=round(mean_p_val, 4),
            concept_drift_psi=round(concept_psi, 4),
            auto_retrain_triggered=auto_trigger,
            evaluated_at=time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()),
            feature_drifts=feature_drifts,
            calibration=calibration_rpt,
        )
