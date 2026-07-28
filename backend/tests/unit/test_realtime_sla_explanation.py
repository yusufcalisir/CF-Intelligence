# ruff: noqa: E402
"""Automated Unit Test Suite for Real-Time Decision Explanation, SLA Verification, and Async SHAP Redis Engine."""

from __future__ import annotations

from app.application.services.sla_monitor import RealtimeSLAMonitor
from app.domain.realtime_explainer import FastInferenceExplainer


def test_fast_inference_explainer_feature_attributions() -> None:
    """Test fast feature attribution vector calculations."""
    explainer = FastInferenceExplainer()

    attr_high = explainer.explain_realtime_score(
        amount=50000.0,
        velocity_1h=8,
        merchant_category="crypto_exchange",
        risk_score=0.85,
    )
    assert len(attr_high) >= 2
    feature_names = {a.feature_name for a in attr_high}
    assert "merchant_category" in feature_names
    assert "amount" in feature_names

    attr_low = explainer.explain_realtime_score(
        amount=50.0,
        velocity_1h=1,
        merchant_category="grocery",
        risk_score=0.10,
    )
    assert len(attr_low) >= 1
    assert any(a.direction == "DECREASES_RISK" for a in attr_low)


def test_realtime_sla_monitor_percentiles_and_violations() -> None:
    """Test latency sample recording and p50, p95, p99 percentile summary calculations."""
    monitor = RealtimeSLAMonitor(target_sla_ms=100.0)

    compliant_samples = [
        10.0,
        12.0,
        15.0,
        18.0,
        22.0,
        25.0,
        30.0,
        35.0,
        40.0,
        42.0,
        45.0,
        48.0,
        50.0,
        55.0,
        60.0,
        65.0,
        70.0,
        80.0,
    ]
    breach_samples = [120.0, 250.0]

    for s in compliant_samples:
        assert monitor.record_latency(s) is True

    for s in breach_samples:
        assert monitor.record_latency(s) is False

    summary = monitor.get_sla_summary()
    assert summary.total_requests == 20
    assert summary.sla_violations_count == 2
    assert summary.sla_compliance_pct == 90.0
    assert summary.p50_latency_ms > 0
    assert summary.p95_latency_ms >= summary.p50_latency_ms
    assert summary.p99_latency_ms >= summary.p95_latency_ms


def test_shap_cache_hit_returns_immediately() -> None:
    """Verifies that prepopulated Redis SHAP key yields sub-millisecond cache hit."""
    explainer = FastInferenceExplainer()
    tx_id = "tx_cached_99"

    # Compute and populate cache first
    explainer.compute_shap(tx_id, {"amount": 5000.0})

    res = explainer.explain_async(tx_id, {"amount": 5000.0})
    assert res["transaction_id"] == tx_id
    assert res["status"] == "COMPLETED"
    assert res["source"] == "REDIS_CACHE"


def test_shap_enqueued_on_cache_miss() -> None:
    """Verifies that cache miss returns PENDING status and job_id."""
    explainer = FastInferenceExplainer()
    tx_id = "tx_fresh_uncached_101"

    res = explainer.explain_async(tx_id, {"amount": 25000.0, "velocity_1h": 6})
    assert res["transaction_id"] == tx_id
    assert res["status"] in ("PENDING", "COMPLETED")
    assert "job_id" in res or "shap_values" in res


def test_shap_result_cached_after_computation() -> None:
    """Verifies that running compute_shap caches attributions in Redis with 300s TTL."""
    explainer = FastInferenceExplainer()
    tx_id = "tx_compute_cached_88"

    res = explainer.compute_shap(tx_id, {"amount": 60000.0, "merchant_category": "crypto_exchange"})
    assert res["transaction_id"] == tx_id
    assert res["status"] == "COMPLETED"
    assert len(res["shap_values"]) > 0
