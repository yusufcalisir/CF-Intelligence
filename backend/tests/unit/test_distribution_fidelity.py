"""Unit tests for Synthetic-to-Real Distribution Fidelity Service."""

import numpy as np
import pytest

from app.domain.distribution_fidelity_service import (
    audit_distribution_fidelity,
    compute_covariance_drift,
    compute_js_divergence,
    compute_ks_test,
    compute_wasserstein_distance,
)


def test_wasserstein_distance_identical_and_shifted():
    u = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    v = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    w = np.array([10.0, 20.0, 30.0, 40.0, 50.0])

    assert compute_wasserstein_distance(u, v) == pytest.approx(0.0, abs=1e-5)
    assert compute_wasserstein_distance(u, w) > 10.0


def test_js_divergence_bounded():
    rng = np.random.default_rng(42)
    u = rng.standard_normal(1000)
    v = rng.standard_normal(1000)
    w = rng.uniform(50, 100, size=1000)

    js_same = compute_js_divergence(u, v)
    assert 0.0 <= js_same <= 1.0

    js_diff = compute_js_divergence(u, w)
    assert js_diff > js_same


def test_ks_test_p_value():
    rng = np.random.default_rng(42)
    u = rng.standard_normal(500)
    v = rng.standard_normal(500)
    stat, pval = compute_ks_test(u, v)
    assert 0.0 <= stat <= 1.0
    assert 0.0 <= pval <= 1.0


def test_covariance_drift():
    rng = np.random.default_rng(42)
    X1 = rng.standard_normal((500, 4))
    X2 = rng.standard_normal((500, 4))
    drift = compute_covariance_drift(X1, X2)
    assert isinstance(drift, float)
    assert drift >= 0.0


def test_audit_distribution_fidelity_report():
    rng = np.random.default_rng(42)
    X_real = rng.standard_normal((1000, 5))
    y_real = (rng.random(1000) < 0.02).astype(int)
    X_synth = rng.standard_normal((1000, 5)) + 0.1
    y_synth = (rng.random(1000) < 0.02).astype(int)

    report = audit_distribution_fidelity(
        X_real=X_real,
        y_real=y_real,
        X_synth=X_synth,
        y_synth=y_synth,
        dataset_name="Test Benchmark",
    )
    assert report.dataset_name == "Test Benchmark"
    assert 0.0 <= report.overall_fidelity_score <= 1.0
    assert len(report.feature_metrics) == 5
    assert report.summary_verdict in ("HIGH_FIDELITY", "MODERATE_SHIFT", "EXTREME_SHIFT")
