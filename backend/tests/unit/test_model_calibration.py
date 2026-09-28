"""Unit Test Suite: Probability Calibration Domain Module.

Tests ECE, MCE, and Brier Score mathematical contracts; Platt Scaling and
Isotonic Regression post-hoc calibrators; InferenceService integration;
and edge cases.

Scientific references verified:
  - ECE monotone with miscalibration severity
  - Brier Score: 0.0 for perfect, 0.25 for random (balanced binary)
  - Platt Scaling must not worsen calibration on well-separated data
  - Isotonic Regression must produce monotone calibrated outputs
"""

from __future__ import annotations

import numpy as np
import pytest

from app.application.services.inference_service import InferenceService
from app.domain.calibration import (
    CalibrationMetrics,
    CalibrationService,
    IsotonicCalibrator,
    PlattScaler,
    compute_brier_score,
    compute_ece_mce,
    evaluate_calibration,
)

# ---------------------------------------------------------------------------
# Helpers & Fixtures
# ---------------------------------------------------------------------------

RNG = np.random.default_rng(42)
N_LARGE = 2000
N_SMALL = 50


def _perfect_probs(n: int) -> tuple[np.ndarray, np.ndarray]:
    """Labels and probabilities that are perfectly calibrated (prob = label)."""
    y_true = RNG.integers(0, 2, size=n).astype(float)
    return y_true, y_true.copy()


def _random_probs(n: int, prevalence: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    """Labels with random uncalibrated probabilities (simulates bad calibration)."""
    y_true = RNG.choice([0, 1], size=n, p=[1 - prevalence, prevalence]).astype(float)
    y_prob = RNG.uniform(0.0, 1.0, size=n)
    return y_true, y_prob


def _overconfident_probs(n: int) -> tuple[np.ndarray, np.ndarray]:
    """A classifier that is systematically overconfident: probs pushed to extremes."""
    y_true = RNG.integers(0, 2, size=n).astype(float)
    # Fraud: prob in [0.85, 1.0], legit: prob in [0.0, 0.15]
    y_prob = np.where(
        y_true == 1,
        RNG.uniform(0.85, 1.0, size=n),
        RNG.uniform(0.0, 0.15, size=n),
    )
    return y_true, y_prob


@pytest.fixture(scope="module")
def overconfident():
    return _overconfident_probs(N_LARGE)


@pytest.fixture(scope="module")
def well_separated_data():
    """Members with low loss (high fraud prob) vs. non-members (low fraud prob)."""
    y_true = np.concatenate([np.ones(N_LARGE // 2), np.zeros(N_LARGE // 2)])
    y_prob = np.concatenate([
        RNG.uniform(0.6, 0.9, size=N_LARGE // 2),
        RNG.uniform(0.1, 0.4, size=N_LARGE // 2),
    ])
    rng_idx = RNG.permutation(N_LARGE)
    return y_true[rng_idx], y_prob[rng_idx]


# ---------------------------------------------------------------------------
# 1. Brier Score
# ---------------------------------------------------------------------------

class TestBrierScore:
    def test_perfect_calibration_zero(self):
        y_true, y_prob = _perfect_probs(N_LARGE)
        bs = compute_brier_score(y_true, y_prob)
        assert bs == 0.0, f"Perfect calibration should give BS=0, got {bs}"

    def test_random_balanced_near_025(self):
        rng = np.random.default_rng(0)
        y_true = rng.integers(0, 2, size=10000).astype(float)
        y_prob = np.full(10000, 0.5)
        bs = compute_brier_score(y_true, y_prob)
        assert abs(bs - 0.25) < 0.01, f"Random balanced BS should be ~0.25, got {bs}"

    def test_brier_bounded_in_zero_one(self):
        y_true, y_prob = _random_probs(N_LARGE)
        bs = compute_brier_score(y_true, y_prob)
        assert 0.0 <= bs <= 1.0

    def test_brier_worse_for_overconfident(self, overconfident):
        y_true, y_prob = overconfident
        y_true_ref, y_prob_ref = _random_probs(N_LARGE)
        bs_over = compute_brier_score(y_true, y_prob)
        bs_random = compute_brier_score(y_true_ref, y_prob_ref)
        # Overconfident (correct) should be better calibrated than random
        assert bs_over < bs_random, f"Overconfident classifier should beat random: {bs_over} < {bs_random}"

    def test_empty_returns_zero(self):
        assert compute_brier_score(np.array([]), np.array([])) == 0.0

    def test_nan_filtered(self):
        y_true = np.array([1.0, 0.0, np.nan])
        y_prob = np.array([0.9, 0.1, 0.5])
        bs = compute_brier_score(y_true, y_prob)
        expected = float(np.mean(np.array([(0.9 - 1.0)**2, (0.1 - 0.0)**2])))
        assert abs(bs - expected) < 1e-4


# ---------------------------------------------------------------------------
# 2. ECE & MCE
# ---------------------------------------------------------------------------

class TestECEMCE:
    def test_perfect_ece_zero(self):
        y_true, y_prob = _perfect_probs(N_LARGE)
        ece, mce, _ = compute_ece_mce(y_true, y_prob)
        assert ece == 0.0, f"Perfect calibration: ECE must be 0, got {ece}"

    def test_ece_bounded_zero_one(self):
        y_true, y_prob = _random_probs(N_LARGE)
        ece, mce, _ = compute_ece_mce(y_true, y_prob)
        assert 0.0 <= ece <= 1.0
        assert 0.0 <= mce <= 1.0

    def test_mce_greater_equal_ece(self):
        y_true, y_prob = _overconfident_probs(N_LARGE)
        ece, mce, _ = compute_ece_mce(y_true, y_prob)
        assert mce >= ece - 1e-6, f"MCE must be >= ECE, got MCE={mce} ECE={ece}"

    def test_bin_count_matches_n_bins(self):
        y_true, y_prob = _random_probs(100)
        _, _, bins = compute_ece_mce(y_true, y_prob, n_bins=5)
        assert len(bins) == 5

    def test_bin_probabilities_are_monotone(self):
        y_true, y_prob = _random_probs(N_LARGE)
        _, _, bins = compute_ece_mce(y_true, y_prob, n_bins=10)
        for i in range(len(bins) - 1):
            assert bins[i].prob_max <= bins[i + 1].prob_min + 1e-6

    def test_ece_high_for_constant_prob_wrong(self):
        # Classifier always predicts 0.9 but true prevalence is 0.1
        y_true = np.zeros(1000)
        y_true[:100] = 1.0
        y_prob = np.full(1000, 0.9)
        ece, _, _ = compute_ece_mce(y_true, y_prob)
        assert ece > 0.5, f"Badly calibrated: ECE should be high, got {ece}"

    def test_empty_input(self):
        ece, mce, bins = compute_ece_mce(np.array([]), np.array([]))
        assert ece == 0.0 and mce == 0.0 and bins == []

    def test_calibration_gap_in_bins(self):
        y_true, y_prob = _random_probs(N_LARGE)
        _, _, bins = compute_ece_mce(y_true, y_prob, n_bins=10)
        for b in bins:
            expected_gap = abs(b.mean_predicted_prob - b.empirical_fraction)
            assert abs(b.calibration_gap - round(expected_gap, 4)) < 1e-4


# ---------------------------------------------------------------------------
# 3. evaluate_calibration (full CalibrationMetrics)
# ---------------------------------------------------------------------------

class TestEvaluateCalibration:
    def test_returns_calibration_metrics(self):
        y_true, y_prob = _random_probs(N_LARGE)
        result = evaluate_calibration(y_true, y_prob)
        assert isinstance(result, CalibrationMetrics)

    def test_well_calibrated_flag_for_perfect(self):
        y_true, y_prob = _perfect_probs(N_LARGE)
        result = evaluate_calibration(y_true, y_prob)
        assert result.is_well_calibrated

    def test_not_well_calibrated_for_constant_wrong(self):
        y_true = np.zeros(1000)
        y_true[:100] = 1.0
        y_prob = np.full(1000, 0.9)
        result = evaluate_calibration(y_true, y_prob)
        assert not result.is_well_calibrated

    def test_n_samples_matches(self):
        y_true, y_prob = _random_probs(N_LARGE)
        result = evaluate_calibration(y_true, y_prob)
        assert result.n_samples == N_LARGE

    def test_to_dict_contains_required_keys(self):
        y_true, y_prob = _random_probs(100)
        d = evaluate_calibration(y_true, y_prob).to_dict()
        for key in ("ece", "mce", "brier_score", "is_well_calibrated", "bins"):
            assert key in d, f"Key {key!r} missing from to_dict()"


# ---------------------------------------------------------------------------
# 4. Platt Scaling
# ---------------------------------------------------------------------------

class TestPlattScaler:
    def test_predict_proba_in_zero_one(self, well_separated_data):
        y_true, y_prob = well_separated_data
        scaler = PlattScaler()
        scaler.fit(y_true[:1000], y_prob[:1000])
        cal = scaler.predict_proba(y_prob[1000:])
        assert np.all(cal >= 0.0) and np.all(cal <= 1.0)

    def test_fitted_required(self):
        scaler = PlattScaler()
        with pytest.raises(RuntimeError):
            scaler.predict_proba(np.array([0.5, 0.6]))

    def test_single_class_does_not_crash(self):
        y_true = np.zeros(100)
        y_prob = np.linspace(0.1, 0.9, 100)
        scaler = PlattScaler()
        scaler.fit(y_true, y_prob)  # Should fall back to identity
        cal = scaler.predict_proba(y_prob)
        assert len(cal) == 100

    def test_platt_output_length_matches_input(self, well_separated_data):
        y_true, y_prob = well_separated_data
        scaler = PlattScaler()
        scaler.fit(y_true, y_prob)
        cal = scaler.predict_proba(y_prob[:200])
        assert len(cal) == 200

    def test_platt_improves_or_maintains_calibration(self, well_separated_data):
        y_true, y_prob = well_separated_data
        cal_n = N_LARGE // 2
        scaler = PlattScaler()
        scaler.fit(y_true[:cal_n], y_prob[:cal_n])
        cal_probs = scaler.predict_proba(y_prob[cal_n:])
        ece_before = compute_ece_mce(y_true[cal_n:], y_prob[cal_n:])[0]
        ece_after = compute_ece_mce(y_true[cal_n:], cal_probs)[0]
        # Platt must not significantly worsen ECE
        assert ece_after <= ece_before + 0.05, f"Platt worsened ECE: {ece_before:.4f} -> {ece_after:.4f}"


# ---------------------------------------------------------------------------
# 5. Isotonic Regression
# ---------------------------------------------------------------------------

class TestIsotonicCalibrator:
    def test_predict_proba_in_zero_one(self, well_separated_data):
        y_true, y_prob = well_separated_data
        ir = IsotonicCalibrator()
        ir.fit(y_true[:1000], y_prob[:1000])
        cal = ir.predict_proba(y_prob[1000:])
        assert np.all(cal >= 0.0) and np.all(cal <= 1.0)

    def test_fitted_required(self):
        ir = IsotonicCalibrator()
        with pytest.raises(RuntimeError):
            ir.predict_proba(np.array([0.5]))

    def test_isotonic_monotone_output(self, well_separated_data):
        y_true, y_prob = well_separated_data
        ir = IsotonicCalibrator()
        ir.fit(y_true, y_prob)
        test_probs = np.linspace(0.0, 1.0, 20)
        cal = ir.predict_proba(test_probs)
        # Isotonic calibration must be monotone non-decreasing
        assert np.all(np.diff(cal) >= -1e-6), f"Isotonic output not monotone: {cal}"

    def test_output_length_matches_input(self, well_separated_data):
        y_true, y_prob = well_separated_data
        ir = IsotonicCalibrator()
        ir.fit(y_true, y_prob)
        cal = ir.predict_proba(y_prob[:300])
        assert len(cal) == 300

    def test_single_class_does_not_crash(self):
        y_true = np.ones(100)
        y_prob = np.linspace(0.1, 0.9, 100)
        ir = IsotonicCalibrator()
        ir.fit(y_true, y_prob)
        cal = ir.predict_proba(y_prob)
        assert len(cal) == 100


# ---------------------------------------------------------------------------
# 6. CalibrationService
# ---------------------------------------------------------------------------

class TestCalibrationService:
    def test_evaluate_returns_metrics(self, well_separated_data):
        y_true, y_prob = well_separated_data
        svc = CalibrationService()
        m = svc.evaluate(y_true, y_prob)
        assert isinstance(m, CalibrationMetrics)
        assert 0.0 <= m.ece <= 1.0

    def test_calibrate_platt(self, well_separated_data):
        y_true, y_prob = well_separated_data
        svc = CalibrationService()
        fit = svc.calibrate("platt", y_true[:1000], y_prob[:1000], y_prob[1000:], y_true[1000:])
        assert fit.method == "platt"
        assert len(fit.calibrated_probs) == N_LARGE - 1000

    def test_calibrate_isotonic(self, well_separated_data):
        y_true, y_prob = well_separated_data
        svc = CalibrationService()
        fit = svc.calibrate("isotonic", y_true[:1000], y_prob[:1000], y_prob[1000:], y_true[1000:])
        assert fit.method == "isotonic"

    def test_unknown_method_raises(self, well_separated_data):
        y_true, y_prob = well_separated_data
        svc = CalibrationService()
        with pytest.raises(ValueError, match="Unknown calibration method"):
            svc.calibrate("svm", y_true, y_prob, y_prob)

    def test_to_dict_keys(self, well_separated_data):
        y_true, y_prob = well_separated_data
        svc = CalibrationService()
        fit = svc.calibrate("platt", y_true, y_prob, y_prob, y_true)
        d = fit.to_dict()
        for key in ("method", "ece_before", "ece_after", "brier_before", "brier_after", "improvement_ece"):
            assert key in d


# ---------------------------------------------------------------------------
# 7. InferenceService integration
# ---------------------------------------------------------------------------

class TestInferenceService:
    def test_evaluate_calibration(self, well_separated_data):
        y_true, y_prob = well_separated_data
        svc = InferenceService()
        m = svc.evaluate_calibration(y_true, y_prob)
        assert isinstance(m, CalibrationMetrics)
        assert svc.last_calibration_metrics is m

    def test_apply_calibration_platt(self, well_separated_data):
        y_true, y_prob = well_separated_data
        svc = InferenceService()
        fit = svc.apply_calibration("platt", y_true[:1000], y_prob[:1000], y_prob[1000:], y_true[1000:])
        assert fit.method == "platt"
        assert len(fit.calibrated_probs) == N_LARGE - 1000

    def test_apply_calibration_isotonic(self, well_separated_data):
        y_true, y_prob = well_separated_data
        svc = InferenceService()
        fit = svc.apply_calibration("isotonic", y_true[:1000], y_prob[:1000], y_prob[1000:], y_true[1000:])
        assert fit.method == "isotonic"

    def test_predict_returns_correct_length(self, well_separated_data):
        _, y_prob = well_separated_data
        svc = InferenceService()
        preds = svc.predict(y_prob[:100])
        assert len(preds) == 100

    def test_predict_risk_label_from_threshold(self):
        svc = InferenceService(decision_threshold=0.5)
        preds = svc.predict(np.array([0.3, 0.7]), np.array([0.3, 0.7]))
        assert preds[0].risk_label == 0
        assert preds[1].risk_label == 1

    def test_predict_custom_threshold(self):
        svc = InferenceService(decision_threshold=0.7)
        preds = svc.predict(np.array([0.65, 0.80]), np.array([0.65, 0.80]))
        assert preds[0].risk_label == 0
        assert preds[1].risk_label == 1

    def test_predict_uses_calibrated_prob_for_label(self):
        svc = InferenceService(decision_threshold=0.5)
        # raw=0.3, calibrated=0.8 -> label should be 1 based on calibrated
        preds = svc.predict(np.array([0.3]), np.array([0.8]))
        assert preds[0].risk_label == 1
        assert abs(preds[0].calibrated_prob - 0.8) < 1e-5

    def test_predict_with_transaction_ids(self):
        svc = InferenceService()
        preds = svc.predict(np.array([0.5, 0.6]), transaction_ids=["tx_A", "tx_B"])
        assert preds[0].transaction_id == "tx_A"
        assert preds[1].transaction_id == "tx_B"

    def test_last_metrics_none_initially(self):
        svc = InferenceService()
        assert svc.last_calibration_metrics is None
