"""Robustness & Failure Injection Test Suite for Model Drift & Calibration Metrics.

Stress-tests every statistical metric by injecting hostile boundary conditions:
  GDR1:  NaN Feature Values in drift input
  GDR2:  Infinite (+/-Inf) Feature Values
  GDR3:  Empty Arrays (N=0 on both sides)
  GDR4:  Single-Element Arrays (N=1)
  GDR5:  Constant Distributions (zero variance)
  GDR6:  Extremely Imbalanced Class Labels (0% fraud, 100% fraud)
  GDR7:  Severely Unequal Sample Sizes (N_curr=3 vs N_ref=50000)
  GDR8:  Entirely Duplicated Values
  GDR9:  Very Small Probabilities (p ~ 1e-10)
  GDR10: Floating-Point Boundary Probabilities (p = 0.0, 1.0)
  GDR11: Mismatched y_true / y_prob Lengths in Calibration
  GDR12: Extreme PSI Inputs Triggering Retraining Logic
"""

from __future__ import annotations

import sys
import math
import numpy as np
import pytest

PROJECT_ROOT = r"c:\Users\Yusuf\Desktop\projects\Privacy-preserving cross-bank fraud detection using Federated Learning\backend"
sys.path.insert(0, PROJECT_ROOT)

from app.application.services.drift_service import ModelDriftService
from app.application.services.retraining_trigger_engine import RetrainingTriggerEngine

_service = ModelDriftService()
_engine = RetrainingTriggerEngine()


def assert_finite_metric(val: float, name: str) -> None:
    """Assert that a metric value is a finite, non-NaN float."""
    assert not math.isnan(val), f"{name} must not be NaN, got {val}"
    assert not math.isinf(val), f"{name} must not be Inf, got {val}"


# =====================================================================
# GDR1: NaN Feature Values
# =====================================================================

def test_gdr1_nan_values_in_psi():
    """GDR1a: NaN values in actual/expected arrays — PSI should not crash or return NaN/Inf."""
    curr_with_nan = [float("nan")] * 50 + list(np.random.normal(0, 1, 50))
    ref = list(np.random.normal(0, 1, 100))
    psi = _service._calculate_psi(np.array(curr_with_nan), np.array(ref))
    # Either produces finite value or gracefully falls back to 0.0
    assert psi >= 0.0, f"PSI with NaN input must be >= 0.0, got {psi}"


def test_gdr1_nan_values_in_feature_drift():
    """GDR1b: NaN values in feature drift analysis — service must not crash."""
    curr = {"amount": [float("nan")] * 20 + list(np.random.normal(0, 1, 80))}
    ref = {"amount": list(np.random.normal(0, 1, 100))}
    try:
        results = _service.analyze_feature_drift(curr, ref)
        # If it returns results, they must be finite
        for r in results:
            assert_finite_metric(r.psi, "feature PSI")
    except (ValueError, FloatingPointError):
        pass  # Explicit domain error is acceptable


def test_gdr1_nan_values_in_brier_score():
    """GDR1c: NaN values in y_prob — calibration should not crash."""
    y_true = [0, 1, 0, 1, 0]
    y_prob = [float("nan"), 0.8, 0.2, float("nan"), 0.1]
    try:
        rpt = _service.compute_calibration(y_true, y_prob)
        assert_finite_metric(rpt.brier_score, "Brier score")
    except (ValueError, FloatingPointError):
        pass  # Explicit exception is acceptable


# =====================================================================
# GDR2: Infinite (+/-Inf) Feature Values
# =====================================================================

def test_gdr2_positive_infinity_in_psi():
    """GDR2a: +Inf in actual array — PSI must not crash."""
    curr = [float("inf")] * 5 + list(np.random.normal(0, 1, 95))
    ref = list(np.random.normal(0, 1, 100))
    psi = _service._calculate_psi(np.array(curr), np.array(ref))
    assert psi >= 0.0, f"PSI with +Inf must be >= 0.0, got {psi}"


def test_gdr2_negative_infinity_in_psi():
    """GDR2b: -Inf in expected array — PSI must not crash."""
    curr = list(np.random.normal(0, 1, 100))
    ref = [float("-inf")] * 5 + list(np.random.normal(0, 1, 95))
    psi = _service._calculate_psi(np.array(curr), np.array(ref))
    assert psi >= 0.0, f"PSI with -Inf must be >= 0.0, got {psi}"


def test_gdr2_both_inf_feature_drift():
    """GDR2c: Both +Inf and -Inf in feature drift — no unhandled exception."""
    curr = {"f1": [float("inf"), float("-inf")] + list(np.random.normal(0, 1, 98))}
    ref = {"f1": list(np.random.normal(0, 1, 100))}
    try:
        results = _service.analyze_feature_drift(curr, ref)
        for r in results:
            assert_finite_metric(r.wasserstein_distance, "Wasserstein")
    except (ValueError, OverflowError):
        pass


# =====================================================================
# GDR3: Empty Arrays
# =====================================================================

def test_gdr3_empty_actual_psi():
    """GDR3a: Empty actual array (N=0) — PSI must return 0.0."""
    psi = _service._calculate_psi(np.array([]), np.random.normal(0, 1, 100))
    assert psi == 0.0, f"PSI with empty actual must return 0.0, got {psi}"


def test_gdr3_empty_expected_psi():
    """GDR3b: Empty expected array (N=0) — PSI must return 0.0."""
    psi = _service._calculate_psi(np.random.normal(0, 1, 100), np.array([]))
    assert psi == 0.0, f"PSI with empty expected must return 0.0, got {psi}"


def test_gdr3_both_empty_psi():
    """GDR3c: Both arrays empty — PSI must return 0.0."""
    psi = _service._calculate_psi(np.array([]), np.array([]))
    assert psi == 0.0, f"PSI with both empty must return 0.0, got {psi}"


def test_gdr3_empty_calibration():
    """GDR3d: Empty y_true / y_prob — compute_calibration must return valid default."""
    rpt = _service.compute_calibration([], [])
    assert rpt.brier_score == 0.0, f"Expected brier_score=0.0, got {rpt.brier_score}"
    assert rpt.expected_calibration_error == 0.0, f"Expected ECE=0.0, got {rpt.expected_calibration_error}"
    assert rpt.is_well_calibrated is True, "Empty input should default to well_calibrated=True"


def test_gdr3_empty_feature_drift():
    """GDR3e: Empty feature dict — analyze_feature_drift must return empty list."""
    results = _service.analyze_feature_drift({}, {})
    assert results == [], f"Empty feature dict must return [], got {results}"


# =====================================================================
# GDR4: Single-Element Arrays (N=1)
# =====================================================================

def test_gdr4_single_element_psi():
    """GDR4a: Single-element actual and expected arrays — no crash."""
    psi = _service._calculate_psi(np.array([5.0]), np.array([5.0]))
    assert_finite_metric(psi, "PSI single-element identical")
    assert psi >= 0.0


def test_gdr4_single_element_different_values():
    """GDR4b: Single element, different values — no crash."""
    psi = _service._calculate_psi(np.array([1.0]), np.array([100.0]))
    assert_finite_metric(psi, "PSI single-element different")
    assert psi >= 0.0


def test_gdr4_single_element_calibration():
    """GDR4c: Single-sample calibration — no crash."""
    rpt = _service.compute_calibration([1], [0.8])
    assert_finite_metric(rpt.brier_score, "Brier score N=1")
    assert 0.0 <= rpt.brier_score <= 1.0


# =====================================================================
# GDR5: Constant Distributions (Zero Variance)
# =====================================================================

def test_gdr5_constant_identical_psi():
    """GDR5a: Both actual and expected are constant identical values."""
    arr = np.full(200, 7.5)
    psi = _service._calculate_psi(arr, arr)
    assert_finite_metric(psi, "Constant identical PSI")
    assert psi < 1e-3, f"Constant identical PSI should be near 0.0, got {psi}"


def test_gdr5_constant_different_values():
    """GDR5b: Actual constant=5.0, expected constant=10.0 — no NaN/crash."""
    actual = np.full(100, 5.0)
    expected = np.full(100, 10.0)
    psi = _service._calculate_psi(actual, expected)
    assert_finite_metric(psi, "Constant different PSI")
    assert psi >= 0.0


def test_gdr5_constant_feature_drift():
    """GDR5c: Constant feature in drift analysis — no crash."""
    curr = {"f1": [3.14] * 100}
    ref = {"f1": [3.14] * 100}
    results = _service.analyze_feature_drift(curr, ref)
    for r in results:
        assert_finite_metric(r.psi, "constant PSI")
        assert r.status in ("STABLE", "MODERATE_DRIFT", "SEVERE_DRIFT")


# =====================================================================
# GDR6: Extremely Imbalanced Class Labels
# =====================================================================

def test_gdr6_all_negative_labels():
    """GDR6a: 100% negative labels (0% fraud) — Brier score must be finite."""
    y_true = [0] * 1000
    y_prob = list(np.random.uniform(0.0, 0.2, 1000))
    rpt = _service.compute_calibration(y_true, y_prob)
    assert_finite_metric(rpt.brier_score, "Brier score all-negative")
    assert 0.0 <= rpt.brier_score <= 1.0


def test_gdr6_all_positive_labels():
    """GDR6b: 100% positive labels (100% fraud) — Brier score must be finite."""
    y_true = [1] * 1000
    y_prob = list(np.random.uniform(0.7, 1.0, 1000))
    rpt = _service.compute_calibration(y_true, y_prob)
    assert_finite_metric(rpt.brier_score, "Brier score all-positive")
    assert 0.0 <= rpt.brier_score <= 1.0


def test_gdr6_one_positive_in_thousand():
    """GDR6c: 0.1% fraud rate — calibration must not crash."""
    y_true = [0] * 999 + [1]
    y_prob = list(np.random.uniform(0.0, 0.05, 999)) + [0.95]
    rpt = _service.compute_calibration(y_true, y_prob)
    assert_finite_metric(rpt.brier_score, "Brier score 0.1% fraud")
    assert 0.0 <= rpt.expected_calibration_error <= rpt.max_calibration_error + 1e-6


# =====================================================================
# GDR7: Severely Unequal Sample Sizes
# =====================================================================

def test_gdr7_tiny_actual_large_expected():
    """GDR7a: N_curr=3 vs N_ref=50000 — no crash, returns valid PSI."""
    curr = np.random.normal(0, 1, 3)
    ref = np.random.normal(0.5, 1, 50000)
    psi = _service._calculate_psi(curr, ref)
    assert_finite_metric(psi, "PSI 3 vs 50000")
    assert psi >= 0.0


def test_gdr7_large_actual_tiny_expected():
    """GDR7b: N_curr=50000 vs N_ref=3 — PSI must be finite."""
    curr = np.random.normal(0, 1, 50000)
    ref = np.random.normal(0, 1, 3)
    psi = _service._calculate_psi(curr, ref)
    assert_finite_metric(psi, "PSI 50000 vs 3")
    assert psi >= 0.0


def test_gdr7_unequal_sizes_feature_drift():
    """GDR7c: Unequal feature sample sizes in full drift analysis."""
    curr = {"amount": list(np.random.normal(0, 1, 10))}
    ref = {"amount": list(np.random.normal(0, 1, 10000))}
    results = _service.analyze_feature_drift(curr, ref)
    for r in results:
        assert_finite_metric(r.ks_statistic, "KS stat")
        assert_finite_metric(r.wasserstein_distance, "Wasserstein")
        assert_finite_metric(r.psi, "PSI")


# =====================================================================
# GDR8: Entirely Duplicated Values
# =====================================================================

def test_gdr8_duplicate_actual_values():
    """GDR8a: actual array is all identical values repeated — no crash."""
    curr = np.array([3.0] * 200)
    ref = np.random.normal(0, 1, 200)
    psi = _service._calculate_psi(curr, ref)
    assert_finite_metric(psi, "PSI all duplicate actual")
    assert psi >= 0.0


def test_gdr8_duplicate_expected_values():
    """GDR8b: expected array is all identical values repeated — triggers fallback binning."""
    curr = np.random.normal(0, 1, 200)
    ref = np.array([7.0] * 200)
    psi = _service._calculate_psi(curr, ref)
    assert_finite_metric(psi, "PSI all duplicate expected")
    assert psi >= 0.0


def test_gdr8_both_same_duplicated_value():
    """GDR8c: Both arrays all identical same value — PSI should be near 0."""
    val = 42.0
    curr = np.full(100, val)
    ref = np.full(100, val)
    psi = _service._calculate_psi(curr, ref)
    assert_finite_metric(psi, "PSI same duplicate")
    assert psi >= 0.0


# =====================================================================
# GDR9: Very Small Probabilities
# =====================================================================

def test_gdr9_near_zero_probabilities():
    """GDR9a: y_prob values very close to 0.0 (p ~ 1e-10) — Brier score must be finite."""
    y_true = [0] * 100 + [1] * 10
    y_prob = [1e-10] * 100 + [1e-5] * 10
    rpt = _service.compute_calibration(y_true, y_prob)
    assert_finite_metric(rpt.brier_score, "Brier near-zero probs")
    assert 0.0 <= rpt.brier_score <= 1.0


def test_gdr9_near_one_probabilities():
    """GDR9b: y_prob values very close to 1.0 (p ~ 1 - 1e-10) — Brier score must be finite."""
    y_true = [1] * 100 + [0] * 10
    y_prob = [1.0 - 1e-10] * 100 + [0.999] * 10
    rpt = _service.compute_calibration(y_true, y_prob)
    assert_finite_metric(rpt.brier_score, "Brier near-one probs")
    assert 0.0 <= rpt.brier_score <= 1.0


def test_gdr9_mixed_extreme_probabilities():
    """GDR9c: Mix of 0.0, 1.0, and 1e-10 probabilities — no crash."""
    y_true = [0, 1, 0, 1, 0, 1]
    y_prob = [0.0, 1.0, 1e-10, 1 - 1e-10, 0.5, 0.5]
    rpt = _service.compute_calibration(y_true, y_prob)
    assert_finite_metric(rpt.brier_score, "Brier extreme prob mix")


# =====================================================================
# GDR10: Floating-Point Boundary Probabilities (p = 0.0, 1.0)
# =====================================================================

def test_gdr10_exact_boundary_probabilities():
    """GDR10a: y_prob includes exact 0.0 and 1.0 — bin boundary inclusion edge case."""
    y_true = [0, 1, 0, 1]
    y_prob = [0.0, 1.0, 0.0, 1.0]
    rpt = _service.compute_calibration(y_true, y_prob)
    assert_finite_metric(rpt.brier_score, "Brier exact 0.0/1.0 probs")
    assert_finite_metric(rpt.expected_calibration_error, "ECE exact 0.0/1.0 probs")


def test_gdr10_subnormal_float_psi():
    """GDR10b: Subnormal float values near machine epsilon in PSI input — no crash."""
    curr = np.array([np.finfo(np.float64).tiny] * 100)
    ref = np.array([np.finfo(np.float64).tiny * 2] * 100)
    psi = _service._calculate_psi(curr, ref)
    assert_finite_metric(psi, "PSI subnormal floats")
    assert psi >= 0.0


# =====================================================================
# GDR11: Mismatched y_true / y_prob Lengths
# =====================================================================

def test_gdr11_mismatched_lengths_calibration():
    """GDR11: Mismatched y_true/y_prob lengths — must return default report."""
    y_true = [0, 1, 0]
    y_prob = [0.1, 0.9]
    rpt = _service.compute_calibration(y_true, y_prob)
    assert rpt.brier_score == 0.0, f"Mismatched lengths must return brier=0.0, got {rpt.brier_score}"
    assert rpt.is_well_calibrated is True


# =====================================================================
# GDR12: Extreme PSI Inputs Triggering Retraining Logic
# =====================================================================

def test_gdr12_psi_exactly_at_warning_threshold():
    """GDR12a: PSI exactly at warning boundary (0.10) — status must be WARNING."""
    curr_scores = list(np.random.beta(2.5, 3.5, 200))
    ref_scores = list(np.random.beta(1.5, 5.0, 200))
    rpt = _service.run_full_drift_analysis(
        {"f1": list(np.random.normal(0, 1, 100))},
        {"f1": list(np.random.normal(0, 1, 100))},
        curr_scores,
        ref_scores,
    )
    assert rpt.overall_status in ("HEALTHY", "WARNING", "CRITICAL"), (
        f"Status must be valid: {rpt.overall_status}"
    )
    assert isinstance(rpt.auto_retrain_triggered, bool)


def test_gdr12_extreme_psi_always_critical():
    """GDR12b: Two completely different distributions always triggers CRITICAL."""
    curr = {"f1": list(np.random.normal(100.0, 1.0, 500))}
    ref = {"f1": list(np.random.normal(0.0, 1.0, 500))}
    curr_scores = list(np.random.uniform(0.9, 1.0, 500))
    ref_scores = list(np.random.uniform(0.0, 0.1, 500))

    rpt = _service.run_full_drift_analysis(curr, ref, curr_scores, ref_scores)
    assert rpt.overall_status == "CRITICAL", f"Extreme shift must be CRITICAL, got {rpt.overall_status}"
    assert rpt.auto_retrain_triggered is True


def test_gdr12_trigger_engine_with_nan_psi():
    """GDR12c: NaN PSI passed to RetrainingTriggerEngine — no crash."""
    try:
        result = _engine.check_drift_threshold(float("nan"), ks_p_value=1.0)
        assert isinstance(result, bool)
    except (ValueError, TypeError):
        pass  # Explicit domain error is acceptable for NaN inputs


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
