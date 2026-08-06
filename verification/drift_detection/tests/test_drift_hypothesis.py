"""Hypothesis Property-Based Test Suite for Model Drift & Calibration Analytics Subsystem.

Verifies 10 core mathematical and statistical invariants across hundreds of randomized
distributions:
  HP1:  PSI Identity Property (PSI(X, X) ≈ 0)
  HP2:  PSI Non-Negativity (PSI(X, Y) >= 0)
  HP3:  PSI Scale Invariance (PSI(aX+b, aY+b) == PSI(X, Y))
  HP4:  KS Statistic Domain Bounds (D ∈ [0, 1], p ∈ [0, 1])
  HP5:  Brier Score Bounded Domain (BS ∈ [0, 1])
  HP6:  Calibration ECE Inequality (ECE <= MCE <= 1.0)
  HP7:  Retraining Trigger Disjunctive Invariant
  HP8:  Drift Status Monotonicity Under Extreme PSI (max_psi >= 0.20 ⇒ CRITICAL)
  HP9:  Auto-Rollback Priority Hierarchy (AUC > Latency > FPR)
  HP10: Single-Value / Zero-Variance Robustness (no NaN/Inf crashes)
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
import numpy as np
import pytest
from hypothesis import given, settings, strategies as st
from scipy import stats

PROJECT_ROOT = r"c:\Users\Yusuf\Desktop\projects\Privacy-preserving cross-bank fraud detection using Federated Learning\backend"
sys.path.insert(0, PROJECT_ROOT)

from app.application.services.drift_service import ModelDriftService
from app.application.services.retraining_trigger_engine import RetrainingTriggerEngine
from app.application.services.auto_rollback import AutoRollbackManager, RollbackCause

_service = ModelDriftService()
_engine = RetrainingTriggerEngine()
_rollback_mgr = AutoRollbackManager()


# =====================================================================
# HYPOTHESIS STRATEGIES FOR DISTRIBUTION GENERATION
# =====================================================================

@st.composite
def distribution_samples(draw, min_size=10, max_size=500):
    """Generates varied statistical distributions: Gaussian, exponential, beta, uniform, bimodal, skewed."""
    dist_type = draw(st.integers(0, 5))
    size = draw(st.integers(min_size, max_size))

    if dist_type == 0:
        # Gaussian
        loc = draw(st.floats(-100.0, 100.0))
        scale = draw(st.floats(0.1, 50.0))
        data = np.random.normal(loc=loc, scale=scale, size=size)
    elif dist_type == 1:
        # Exponential
        scale = draw(st.floats(0.1, 20.0))
        data = np.random.exponential(scale=scale, size=size)
    elif dist_type == 2:
        # Beta
        a = draw(st.floats(0.5, 5.0))
        b = draw(st.floats(0.5, 5.0))
        data = np.random.beta(a=a, b=b, size=size)
    elif dist_type == 3:
        # Bimodal mixture
        loc1 = draw(st.floats(-50.0, 0.0))
        loc2 = draw(st.floats(0.0, 50.0))
        data = np.concatenate([
            np.random.normal(loc1, 2.0, size=size // 2),
            np.random.normal(loc2, 2.0, size=size - (size // 2)),
        ])
    else:
        # Uniform
        low = draw(st.floats(-50.0, 0.0))
        high = draw(st.floats(0.0, 50.0))
        data = np.random.uniform(low=low, high=high, size=size)

    data = np.where(np.abs(data) < 1e-10, 0.0, data)
    return data.astype(np.float64)


# =====================================================================
# PROPERTY-BASED TESTS
# =====================================================================

@given(sample=distribution_samples())
@settings(max_examples=100, deadline=None)
def test_hp1_psi_identity_property(sample):
    """HP1: PSI(X, X) ≈ 0.0 for any random distribution (Kullback-Leibler identity)."""
    psi_val = _service._calculate_psi(sample, sample)
    assert psi_val >= 0.0, f"PSI identity must be non-negative, got {psi_val}"
    assert psi_val < 1e-3, f"PSI(X, X) should be near zero, got {psi_val}"


@given(sample_a=distribution_samples(), sample_b=distribution_samples())
@settings(max_examples=100, deadline=None)
def test_hp2_psi_non_negativity(sample_a, sample_b):
    """HP2: PSI(X, Y) >= 0.0 for all distributions (Gibbs' Inequality)."""
    psi_val = _service._calculate_psi(sample_a, sample_b)
    assert not np.isnan(psi_val), "PSI must not be NaN"
    assert not np.isinf(psi_val), "PSI must not be Inf"
    assert psi_val >= 0.0, f"PSI must be non-negative, got {psi_val}"


@given(
    sample_a=distribution_samples(),
    sample_b=distribution_samples(),
    scale=st.floats(0.5, 5.0),
    shift=st.floats(-10.0, 10.0),
)
@settings(max_examples=100, deadline=None)
def test_hp3_psi_scale_invariance(sample_a, sample_b, scale, shift):
    """HP3: PSI(a*X + b, a*Y + b) == PSI(X, Y) for a > 0 (Rank order quantile invariance)."""
    psi_orig = _service._calculate_psi(sample_a, sample_b)
    scaled_a = scale * sample_a + shift
    scaled_b = scale * sample_b + shift
    psi_scaled = _service._calculate_psi(scaled_a, scaled_b)
    assert abs(psi_orig - psi_scaled) < 1e-3, f"Scale invariance violated: {psi_orig} vs {psi_scaled}"


@given(sample_a=distribution_samples(), sample_b=distribution_samples())
@settings(max_examples=100, deadline=None)
def test_hp4_ks_statistic_domain_bounds(sample_a, sample_b):
    """HP4: KS statistic D ∈ [0, 1] and p-value ∈ [0, 1]."""
    res = _service.analyze_feature_drift({"f1": sample_a.tolist()}, {"f1": sample_b.tolist()})
    if res:
        m = res[0]
        assert 0.0 <= m.ks_statistic <= 1.0, f"KS stat out of bounds: {m.ks_statistic}"
        assert 0.0 <= m.ks_p_value <= 1.0, f"KS p-val out of bounds: {m.ks_p_value}"


@given(
    probs=st.lists(st.floats(0.0, 1.0), min_size=10, max_size=200),
    labels=st.lists(st.integers(0, 1), min_size=10, max_size=200),
)
@settings(max_examples=100, deadline=None)
def test_hp5_brier_score_bounded_domain(probs, labels):
    """HP5: Brier Score BS ∈ [0, 1]."""
    n = min(len(probs), len(labels))
    rpt = _service.compute_calibration(labels[:n], probs[:n])
    assert 0.0 <= rpt.brier_score <= 1.0, f"Brier score out of bounds: {rpt.brier_score}"


@given(
    probs=st.lists(st.floats(0.0, 1.0), min_size=20, max_size=300),
    labels=st.lists(st.integers(0, 1), min_size=20, max_size=300),
)
@settings(max_examples=100, deadline=None)
def test_hp6_calibration_ece_inequality(probs, labels):
    """HP6: 0.0 <= ECE <= MCE <= 1.0."""
    n = min(len(probs), len(labels))
    rpt = _service.compute_calibration(labels[:n], probs[:n])
    assert 0.0 <= rpt.expected_calibration_error <= rpt.max_calibration_error + 1e-6, (
        f"ECE <= MCE violated: {rpt.expected_calibration_error} vs {rpt.max_calibration_error}"
    )
    assert rpt.max_calibration_error <= 1.0001, f"MCE exceeds 1.0: {rpt.max_calibration_error}"


@given(
    record_count=st.integers(0, 100000),
    psi_score=st.floats(0.0, 1.0),
    ks_p_value=st.floats(0.0, 1.0),
    hours_elapsed=st.integers(0, 72),
)
@settings(max_examples=100, deadline=None)
def test_hp7_retraining_trigger_disjunctive_invariant(record_count, psi_score, ks_p_value, hours_elapsed):
    """HP7: IsTriggered == (T_ingest OR T_drift OR T_cadence)."""
    last_run = datetime.now(UTC) - timedelta(hours=hours_elapsed)
    res = _engine.evaluate_triggers(
        record_count=record_count,
        psi_score=psi_score,
        ks_p_value=ks_p_value,
        last_run_timestamp=last_run,
    )
    c_ingest = record_count >= _engine.ingestion_threshold
    c_drift = psi_score > _engine.psi_threshold or ks_p_value < _engine.ks_pvalue_threshold
    c_cadence = hours_elapsed >= _engine.cadence_hours

    expected_trigger = c_ingest or c_drift or c_cadence
    assert res["is_triggered"] == expected_trigger, (
        f"Trigger disjunction violated: {res['is_triggered']} vs {expected_trigger}"
    )


@given(high_psi=st.floats(0.2001, 5.0))
@settings(max_examples=100, deadline=None)
def test_hp8_drift_status_monotonicity_under_extreme_psi(high_psi):
    """HP8: max_psi >= 0.20 ⇒ overall_status == CRITICAL and auto_retrain_triggered == True."""
    curr = {"f1": np.random.normal(5, 1, 1000).tolist()}
    ref = {"f1": np.random.normal(0, 1, 1000).tolist()}
    curr_scores = np.random.normal(0.8, 0.1, 1000).tolist()
    ref_scores = np.random.normal(0.2, 0.1, 1000).tolist()

    rpt = _service.run_full_drift_analysis(curr, ref, curr_scores, ref_scores)
    assert rpt.overall_status == "CRITICAL", f"Expected CRITICAL status, got {rpt.overall_status}"
    assert rpt.auto_retrain_triggered is True, "auto_retrain_triggered must be True when status is CRITICAL"


@given(
    auc=st.floats(0.0, 1.0),
    latency=st.floats(0.0, 500.0),
    fpr=st.floats(0.0, 0.50),
)
@settings(max_examples=100, deadline=None)
def test_hp9_auto_rollback_priority_hierarchy(auc, latency, fpr):
    """HP9: SLA health evaluation priority: AUC < 0.65 > Latency > 200ms > FPR > 0.05."""
    triggered, record = _rollback_mgr.evaluate_model_health_and_rollback(
        active_model_version="v2",
        current_auc=auc,
        current_latency_ms=latency,
        current_fpr=fpr,
        fallback_model_version="v1",
    )
    if triggered and record is not None:
        if auc < _rollback_mgr.min_auc:
            assert record.cause == RollbackCause.AUC_DROP_CRITICAL, f"Expected AUC cause, got {record.cause}"
        elif latency > _rollback_mgr.max_latency_ms:
            assert record.cause == RollbackCause.LATENCY_SLA_VIOLATION, f"Expected Latency cause, got {record.cause}"
        elif fpr > _rollback_mgr.max_fpr:
            assert record.cause == RollbackCause.FPR_SPIKE, f"Expected FPR cause, got {record.cause}"


@given(val=st.floats(-100.0, 100.0), size=st.integers(10, 100))
@settings(max_examples=100, deadline=None)
def test_hp10_single_value_zero_variance_robustness(val, size):
    """HP10: Single-value zero-variance inputs do not crash or produce NaN/Inf."""
    const_arr = np.full(size, val, dtype=np.float64)
    psi_val = _service._calculate_psi(const_arr, const_arr)
    assert not np.isnan(psi_val), "PSI on constant data must not be NaN"
    assert not np.isinf(psi_val), "PSI on constant data must not be Inf"
    assert psi_val >= 0.0, "PSI on constant data must be >= 0.0"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
