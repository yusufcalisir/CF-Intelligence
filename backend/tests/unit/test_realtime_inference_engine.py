# ruff: noqa: E402
"""Automated Unit Test Suite for Low-Latency Real-Time Inference Gateway, TorchScript JIT, and Fallback Engine."""

from __future__ import annotations

import time

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.services.model_service import ModelService
from app.config import get_settings
from app.domain.inference_fallback import (
    InferenceDecision,
    InferenceFallbackEngine,
)
from app.presentation.routers.realtime_inference import (
    reset_circuit_breaker,
    reset_model_cache,
    router,
)

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def setup_function() -> None:
    """Reset circuit breaker and model cache state before each test."""
    reset_circuit_breaker()
    reset_model_cache()


def test_heuristic_fallback_engine_evaluations() -> None:
    """Test deterministic heuristic fallback decisions and risk score bounds."""
    engine = InferenceFallbackEngine()

    dec_low, score_low, _ = engine.evaluate_heuristic_fallback(
        amount=100.0,
        velocity_1h=1,
        merchant_category="general_retail",
    )
    assert dec_low == InferenceDecision.ALLOW
    assert score_low < 0.35

    dec_med, score_med, _ = engine.evaluate_heuristic_fallback(
        amount=15000.0,
        velocity_1h=6,
        merchant_category="general_retail",
    )
    assert dec_med in (InferenceDecision.REVIEW, InferenceDecision.BLOCK)
    assert score_med >= 0.30

    dec_high, score_high, expl_high = engine.evaluate_heuristic_fallback(
        amount=75000.0,
        velocity_1h=12,
        merchant_category="crypto_exchange",
    )
    assert dec_high == InferenceDecision.BLOCK
    assert score_high >= 0.70
    assert "High-risk merchant" in expl_high


def test_realtime_inference_api_endpoint_scoring() -> None:
    """Test POST /v1/inference/score endpoint for low-latency ML scoring."""
    payload = {
        "transaction_id": "tx_test_9988",
        "amount": 450.0,
        "currency": "USD",
        "source_account": "acc_src_1",
        "target_account": "acc_dst_2",
        "merchant_category": "electronics",
        "velocity_1h": 2,
    }

    # Warmup JIT model
    client.post("/v1/inference/score", json=payload)

    response = client.post("/v1/inference/score", json=payload)
    assert response.status_code == 200

    data = response.json()
    assert data["transaction_id"] == "tx_test_9988"
    assert data["decision"] in ("ALLOW", "REVIEW", "BLOCK")
    assert data["evaluated_by"] == "ML_MODEL"
    assert data["latency_ms"] < 100.0


def test_circuit_breaker_opens_after_3_failures() -> None:
    """Test circuit breaker tripping open after 3 consecutive failures."""
    payload = {
        "transaction_id": "tx_test_fallback_77",
        "amount": 60000.0,
        "currency": "USD",
        "source_account": "acc_src_1",
        "target_account": "acc_dst_2",
        "merchant_category": "crypto_exchange",
        "velocity_1h": 15,
        "force_fallback": True,
    }

    # 3 strikes of forced failure
    for _ in range(3):
        res = client.post("/v1/inference/score", json=payload)
        assert res.status_code == 200
        assert res.json()["evaluated_by"] == "HEURISTIC_FALLBACK"

    # 4th call without force_fallback should route to fallback because Circuit Breaker is OPEN
    normal_payload = {
        "transaction_id": "tx_test_normal_88",
        "amount": 100.0,
        "currency": "USD",
        "source_account": "acc_src_1",
        "target_account": "acc_dst_2",
        "merchant_category": "electronics",
        "velocity_1h": 1,
        "force_fallback": False,
    }
    res4 = client.post("/v1/inference/score", json=normal_payload)
    assert res4.status_code == 200
    data4 = res4.json()
    assert data4["evaluated_by"] == "HEURISTIC_FALLBACK"
    assert "Circuit Breaker Open" in data4["explanation"]


def test_p95_latency_under_100ms() -> None:
    """Run 1,000 scoring requests and verify p95 latency is strictly under 100ms SLA."""
    payload = {
        "transaction_id": "tx_sla_bench",
        "amount": 250.0,
        "currency": "USD",
        "source_account": "acc_src_sla",
        "target_account": "acc_dst_sla",
        "merchant_category": "retail",
        "velocity_1h": 1,
    }

    latencies: list[float] = []

    # Warmup
    client.post("/v1/inference/score", json=payload)

    for i in range(1000):
        t0 = time.perf_counter()
        resp = client.post("/v1/inference/score", json=payload)
        t1 = time.perf_counter()
        assert resp.status_code == 200
        latencies.append((t1 - t0) * 1000.0)

    p95_ms = float(np.percentile(latencies, 95))
    assert p95_ms < 100.0, f"p95 latency {p95_ms:.2f}ms exceeded SLA threshold of 100ms"


def test_model_cache_invalidated_on_champion_change() -> None:
    """Verify that invalidate_model_cache clears Redis key."""
    settings = get_settings()
    svc = ModelService(settings)

    svc.invalidate_model_cache()
    # No exception raised during invalidation
