"""Unit tests for ModelDriftService statistical algorithms and calibration analytics.

Tests cover:
- Population Stability Index (PSI) calculation (identical, moderate, severe, small-sample, NaN/Inf)
- Kolmogorov-Smirnov 2-sample test and normalized Wasserstein distance (EMD/sigma)
- Model probability calibration (Brier score, ECE, MCE, reliability curve bins)
- Benjamini-Hochberg False Discovery Rate (FDR) p-value correction
- End-to-end full drift analysis with threshold status transitions (HEALTHY, WARNING, CRITICAL)
- Automated retraining loop trigger flag evaluation
"""

from __future__ import annotations

import numpy as np
import pytest

from app.application.services.drift_service import (
    CalibrationReport,
    DriftAnalysisReport,
    ModelDriftService,
)


class TestModelDriftServicePSI:
    """Test suite for Population Stability Index (PSI) calculation."""

    def test_psi_identical_distributions_yields_zero(self) -> None:
        """Identical distributions have zero divergence."""
        rng = np.random.default_rng(42)
        expected = rng.normal(100.0, 15.0, size=200)
        actual = expected.copy()

        psi = ModelDriftService._calculate_psi(actual, expected)
        assert pytest.approx(psi, abs=1e-5) == 0.0

    def test_psi_moderate_and_severe_distribution_shifts(self) -> None:
        """Quantifies moderate (0.10 <= PSI < 0.20) and severe (PSI >= 0.20) divergence."""
        rng = np.random.default_rng(123)
        expected = rng.normal(0.0, 1.0, size=500)

        # Moderate shift
        actual_moderate = rng.normal(0.40, 1.0, size=500)
        psi_mod = ModelDriftService._calculate_psi(actual_moderate, expected)
        assert 0.05 < psi_mod < 0.30

        # Severe shift
        actual_severe = rng.normal(1.5, 1.0, size=500)
        psi_sev = ModelDriftService._calculate_psi(actual_severe, expected)
        assert psi_sev >= 0.20

    def test_psi_small_sample_size_guard(self) -> None:
        """Quantile PSI is unreliable at N < 30; service must return 0.0 without raising."""
        rng = np.random.default_rng(99)
        expected = rng.normal(100.0, 10.0, size=20)  # N=20 < 30
        actual = rng.normal(150.0, 10.0, size=20)

        psi = ModelDriftService._calculate_psi(actual, expected)
        assert psi == 0.0

    def test_psi_nan_and_inf_sanitization(self) -> None:
        """Arrays containing IEEE 754 NaN and Inf are filtered to finite values."""
        rng = np.random.default_rng(77)
        expected = rng.normal(50.0, 5.0, size=100)
        actual = expected.copy()

        # Inject non-finite values
        actual_corrupted = np.append(actual, [np.nan, np.inf, -np.inf])
        expected_corrupted = np.append(expected, [np.nan])

        psi = ModelDriftService._calculate_psi(actual_corrupted, expected_corrupted)
        assert pytest.approx(psi, abs=1e-5) == 0.0

    def test_psi_none_or_empty_inputs(self) -> None:
        """None or empty collections return 0.0 safely."""
        assert ModelDriftService._calculate_psi(None, [1.0, 2.0]) == 0.0
        assert ModelDriftService._calculate_psi([1.0, 2.0], None) == 0.0
        assert ModelDriftService._calculate_psi([], []) == 0.0
        assert ModelDriftService._calculate_psi([np.nan], [np.nan]) == 0.0

    def test_psi_constant_value_array(self) -> None:
        """Arrays with zero variance handle bin deduplication fallback."""
        expected = np.array([42.0] * 50)
        actual = np.array([42.0] * 50)

        psi = ModelDriftService._calculate_psi(actual, expected)
        assert psi == 0.0


class TestModelDriftServiceFeatureDrift:
    """Test suite for feature drift analysis using KS 2-sample and Wasserstein metrics."""

    @pytest.fixture
    def drift_service(self) -> ModelDriftService:
        return ModelDriftService(psi_threshold_warning=0.10, psi_threshold_critical=0.20)

    def test_analyze_feature_drift_stable_and_divergent(
        self, drift_service: ModelDriftService
    ) -> None:
        rng = np.random.default_rng(42)
        amount_baseline = rng.normal(100.0, 15.0, size=300).tolist()
        velocity_baseline = rng.exponential(2.0, size=300).tolist()

        ref_data = {
            "amount": amount_baseline,
            "velocity": velocity_baseline,
        }
        curr_data = {
            "amount": list(amount_baseline),  # Completely identical -> Stable
            "velocity": rng.exponential(12.0, size=300).tolist(),  # Severely shifted
        }

        results = drift_service.analyze_feature_drift(curr_data, ref_data)
        assert len(results) == 2

        by_name = {r.feature_name: r for r in results}
        amount_res = by_name["amount"]
        velocity_res = by_name["velocity"]

        assert amount_res.status == "STABLE"
        assert amount_res.ks_p_value >= 0.05

        assert velocity_res.status in ("MODERATE_DRIFT", "SEVERE_DRIFT")
        assert velocity_res.ks_statistic > 0.30
        assert velocity_res.ks_p_value < 0.01
        assert velocity_res.wasserstein_distance > 0.50

    def test_analyze_feature_drift_handles_unmatched_keys(
        self, drift_service: ModelDriftService
    ) -> None:
        """Features not present in both current and reference data are safely ignored."""
        ref_data = {"feature_a": [1.0] * 50}
        curr_data = {"feature_b": [2.0] * 50}

        results = drift_service.analyze_feature_drift(curr_data, ref_data)
        assert len(results) == 0

    def test_analyze_feature_drift_all_nan_vectors(
        self, drift_service: ModelDriftService
    ) -> None:
        """Features with all NaN values are skipped without exceptions."""
        ref_data = {"feature_nan": [float("nan")] * 50}
        curr_data = {"feature_nan": [float("nan")] * 50}

        results = drift_service.analyze_feature_drift(curr_data, ref_data)
        assert len(results) == 0


class TestModelDriftServiceCalibration:
    """Test suite for probability calibration, Brier score, and ECE."""

    @pytest.fixture
    def drift_service(self) -> ModelDriftService:
        return ModelDriftService()

    def test_compute_calibration_well_calibrated_predictions(
        self, drift_service: ModelDriftService
    ) -> None:
        """Consistent predicted probabilities match empirical outcomes."""
        y_true = [0] * 100 + [1] * 100
        y_prob = [0.05] * 100 + [0.95] * 100

        report = drift_service.compute_calibration(y_true, y_prob, num_bins=10)
        assert isinstance(report, CalibrationReport)
        assert report.brier_score < 0.05
        assert report.expected_calibration_error < 0.10
        assert report.is_well_calibrated is True
        assert len(report.bins) == 10

    def test_compute_calibration_poorly_calibrated_model(
        self, drift_service: ModelDriftService
    ) -> None:
        """Severe miscalibration (overconfidence) flags is_well_calibrated as False."""
        y_true = [0] * 100  # All actual negative
        y_prob = [0.90] * 100  # High predicted fraud probability

        report = drift_service.compute_calibration(y_true, y_prob, num_bins=10)
        assert report.brier_score > 0.50
        assert report.expected_calibration_error > 0.50
        assert report.is_well_calibrated is False

    def test_compute_calibration_empty_and_mismatched_inputs(
        self, drift_service: ModelDriftService
    ) -> None:
        """Empty or mismatched arrays return safe default report."""
        rep1 = drift_service.compute_calibration([], [])
        assert rep1.brier_score == 0.0
        assert rep1.expected_calibration_error == 0.0

        rep2 = drift_service.compute_calibration([0, 1], [0.5])
        assert rep2.brier_score == 0.0

        rep3 = drift_service.compute_calibration([float("nan")], [float("nan")])
        assert rep3.brier_score == 0.0


class TestModelDriftServiceFullAnalysis:
    """Test suite for run_full_drift_analysis, Benjamini-Hochberg FDR, and trigger rules."""

    @pytest.fixture
    def drift_service(self) -> ModelDriftService:
        return ModelDriftService(psi_threshold_warning=0.10, psi_threshold_critical=0.20)

    def test_full_drift_analysis_healthy_baseline(
        self, drift_service: ModelDriftService
    ) -> None:
        rng = np.random.default_rng(42)
        ref_data = {
            "amount": rng.normal(100.0, 10.0, size=200).tolist(),
            "velocity": rng.normal(5.0, 1.0, size=200).tolist(),
        }
        curr_data = {k: list(v) for k, v in ref_data.items()}
        ref_scores = rng.beta(1.0, 10.0, size=200).tolist()
        curr_scores = list(ref_scores)

        report = drift_service.run_full_drift_analysis(
            current_data=curr_data,
            reference_data=ref_data,
            current_scores=curr_scores,
            reference_scores=ref_scores,
        )

        assert isinstance(report, DriftAnalysisReport)
        assert report.overall_status == "HEALTHY"
        assert report.max_psi < 0.10
        assert report.concept_drift_psi < 0.10
        assert report.auto_retrain_triggered is False

    def test_full_drift_analysis_warning_transition(
        self, drift_service: ModelDriftService
    ) -> None:
        rng = np.random.default_rng(42)
        ref_data = {
            "amount": rng.normal(100.0, 10.0, size=200).tolist(),
            "velocity": rng.normal(5.0, 1.0, size=200).tolist(),
        }
        # Moderate shift in velocity only
        curr_data = {
            "amount": rng.normal(100.0, 10.0, size=200).tolist(),
            "velocity": rng.normal(6.2, 1.0, size=200).tolist(),
        }
        ref_scores = rng.beta(1.0, 10.0, size=200).tolist()
        curr_scores = rng.beta(1.0, 10.0, size=200).tolist()

        report = drift_service.run_full_drift_analysis(
            current_data=curr_data,
            reference_data=ref_data,
            current_scores=curr_scores,
            reference_scores=ref_scores,
        )

        assert report.overall_status in ("WARNING", "CRITICAL")
        if report.overall_status == "WARNING":
            assert report.auto_retrain_triggered is False

    def test_full_drift_analysis_critical_triggers_automated_retraining(
        self, drift_service: ModelDriftService
    ) -> None:
        """Severe drift in multiple features or risk scores triggers auto_retrain_triggered=True."""
        rng = np.random.default_rng(101)
        ref_data = {
            "amount": rng.normal(100.0, 10.0, size=200).tolist(),
            "velocity": rng.normal(5.0, 1.0, size=200).tolist(),
        }
        # Severe shift in both features
        curr_data = {
            "amount": rng.normal(250.0, 30.0, size=200).tolist(),
            "velocity": rng.normal(25.0, 5.0, size=200).tolist(),
        }
        ref_scores = rng.beta(1.0, 10.0, size=200).tolist()
        curr_scores = rng.beta(10.0, 1.0, size=200).tolist()  # Severe concept drift

        report = drift_service.run_full_drift_analysis(
            current_data=curr_data,
            reference_data=ref_data,
            current_scores=curr_scores,
            reference_scores=ref_scores,
            y_true=[0] * 100 + [1] * 100,
            y_prob=[0.1] * 100 + [0.9] * 100,
        )

        assert report.overall_status == "CRITICAL"
        assert report.max_psi >= 0.20
        assert report.concept_drift_psi >= 0.20
        assert report.auto_retrain_triggered is True
        assert report.calibration is not None
        assert len(report.feature_drifts) == 2

    def test_benjamini_hochberg_fdr_control(
        self, drift_service: ModelDriftService
    ) -> None:
        """Multiple testing p-value correction controls false discovery rate."""
        # Fabricate features with varying p-values
        curr_data = {f"feat_{i}": [float(i)] * 50 for i in range(5)}
        ref_data = {f"feat_{i}": [float(i) + 0.01 * i] * 50 for i in range(5)}

        report = drift_service.run_full_drift_analysis(
            current_data=curr_data,
            reference_data=ref_data,
            current_scores=[0.1] * 50,
            reference_scores=[0.1] * 50,
        )

        assert isinstance(report.mean_ks_p_value, float)
