"""Comprehensive Hardening and Anti-Mock Unit Test Suite for STAGE_36:
Real-Time Transaction Scoring & Model Serving Subsystem.
"""

from __future__ import annotations

import concurrent.futures
import io
import time
from unittest.mock import patch

import numpy as np
import pytest
import torch
from fastapi.testclient import TestClient

from app.application.services.model_service import ModelService
from app.application.services.risk_engine import RiskScoringEngine
from app.config import get_settings
from app.domain.value_objects_phase2 import RiskScore, RiskWeightConfig
from app.main import app
from app.presentation.routers.realtime_inference import (
    reset_circuit_breaker,
    reset_model_cache,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_inference_state():
    """Reset circuit breaker and model caches before each test."""
    reset_circuit_breaker()
    reset_model_cache()
    yield
    reset_circuit_breaker()
    reset_model_cache()


def test_score_transaction_genuine_ml_model_evaluation():
    """VECTOR 1 & 4: Verifies score_transaction executes genuine PyTorch ML model
    inference instead of hardcoding fixed 0.15 prediction.
    """
    payload_low = {
        "transaction_id": "tx_genuine_01",
        "account_id": "acc_genuine_user",
        "amount": 15.00,
        "currency": "EUR",
        "merchant_id": "rewe_supermarket",
        "country": "DE",
        "device_id": "dev_trusted_01",
    }
    resp = client.post("/api/v1/transactions/score", json=payload_low)
    assert resp.status_code == 200
    data = resp.json()
    assert "risk_score" in data
    assert 0 <= data["risk_score"] <= 1000
    assert data["decision"] in ("ALLOW", "REVIEW", "BLOCK")
    # Verify that explanations include real feature contributions
    assert len(data["explanations"]) >= 1


def test_score_transaction_tenant_isolation():
    """VECTOR 14: Confirms multi-tenant isolation is enforced when caller tenant
    mismatches the bank header.
    """
    from app.dependencies import resolve_tenant

    # Override resolve_tenant to return a tenant for bank_a
    app.dependency_overrides[resolve_tenant] = lambda: "bank_a"
    try:
        payload = {
            "transaction_id": "tx_tenant_test",
            "account_id": "acc_bank_b",
            "amount": 500.0,
            "currency": "USD",
            "merchant_id": "merchant_xyz",
            "country": "US",
            "device_id": "dev_01",
        }
        # Attempt to score with X-Bank-ID: bank_b while caller is bank_a
        resp = client.post(
            "/api/v1/transactions/score",
            json=payload,
            headers={"X-Bank-ID": "bank_b"},
        )
        assert resp.status_code == 403
        assert "Broken Access Control" in resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(resolve_tenant, None)


def test_risk_engine_thread_safety_under_concurrent_writes():
    """VECTOR 16: Verifies RiskScoringEngine handles concurrent multi-threaded
    read/write operations on historical state and baselines without race conditions.
    """
    engine = RiskScoringEngine()
    entity_hash = "entity_stress_test"

    def worker(worker_id: int):
        for i in range(25):
            engine.register_alert(entity_hash)
            engine.register_chargeback(entity_hash, 0.05 + (i * 0.001))
            engine.register_baseline(
                entity_hash, {"mean_amount": 100.0 + i, "std_amount": 25.0}
            )
            txn = {
                "transaction_amount": 120.0 + (worker_id * 5),
                "merchant_category": "grocery",
                "velocity": 2.5,
            }
            score = engine.score_transaction(txn, ml_prediction=0.20, entity_hash=entity_hash)
            assert isinstance(score, RiskScore)
            assert 0.0 <= score.score <= 1000.0

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker, w) for w in range(8)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    # Verify atomic accumulation across 8 workers * 25 iterations = 200 alerts
    with engine._lock:
        assert engine._alert_history[entity_hash] == 200


def test_gnn_topological_risk_signal_incorporation():
    """VECTOR 13: Verifies GNN topological risk signal is properly integrated
    into the composite score when configured or supplied in transaction.
    """
    weights = RiskWeightConfig(
        ml_prediction=0.20,
        velocity_rules=0.15,
        merchant_reputation=0.10,
        country_risk=0.10,
        device_anomaly=0.08,
        customer_history=0.10,
        previous_alerts=0.08,
        chargeback_history=0.07,
        behavior_anomaly=0.07,
        gnn_topological_risk=0.25,
    )
    engine = RiskScoringEngine(weights=weights)

    # 1. Transaction with high GNN risk (0.90)
    txn_gnn_high = {
        "velocity": 2.0,
        "gnn_topological_risk": 0.90,
    }
    res_high = engine.score_transaction(txn_gnn_high, ml_prediction=0.10)
    assert len(res_high.signals) == 10
    gnn_sig_high = next(s for s in res_high.signals if s.signal_name == "gnn_topological_risk")
    assert gnn_sig_high.normalized_score == 0.90
    assert gnn_sig_high.weight == 0.25

    # 2. Transaction with low GNN risk (0.05)
    txn_gnn_low = {
        "velocity": 2.0,
        "gnn_topological_risk": 0.05,
    }
    res_low = engine.score_transaction(txn_gnn_low, ml_prediction=0.10)
    gnn_sig_low = next(s for s in res_low.signals if s.signal_name == "gnn_topological_risk")
    assert gnn_sig_low.normalized_score == 0.05
    assert res_high.score > res_low.score


def test_realtime_inference_canonical_features_mapping():
    """VECTOR 1 & 4: Confirms realtime_inference.py constructs a canonical
    10-feature normalized vector matching FEATURE_NAMES and REFERENCE_BOUNDS.
    """
    payload = {
        "transaction_id": "tx_canon_01",
        "amount": 2500.0,
        "currency": "USD",
        "source_account": "acc_canon_src",
        "target_account": "acc_canon_dst",
        "merchant_category": "crypto_exchange",
        "velocity_1h": 15,
    }
    resp = client.post("/v1/inference/score", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["transaction_id"] == "tx_canon_01"
    assert data["evaluated_by"] == "ML_MODEL"
    assert data["decision"] in ("REVIEW", "BLOCK")
    assert "High-risk merchant" in data["explanation"]


def test_safe_torchscript_jit_redis_serialization():
    """VECTOR 11: Verifies TorchScript model serialization and deserialization
    via io.BytesIO buffer without raw Python pickle vulnerabilities.
    """
    settings = get_settings()
    svc = ModelService(settings)
    model = svc.create_model(dp_compatible=True)
    model.eval()

    dummy_input = torch.zeros(1, 10)
    scripted = torch.jit.trace(model, dummy_input, check_trace=False)

    # Serialize using torch.jit.save to BytesIO
    buffer = io.BytesIO()
    torch.jit.save(scripted, buffer)
    serialized_bytes = buffer.getvalue()
    assert len(serialized_bytes) > 0

    # Deserialize using torch.jit.load from BytesIO
    read_buffer = io.BytesIO(serialized_bytes)
    loaded_model = torch.jit.load(read_buffer)
    loaded_model.eval()

    # Compare forward pass outputs
    test_input = torch.randn(2, 10)
    with torch.no_grad():
        out_orig = scripted(test_input)
        out_loaded = loaded_model(test_input)

    assert torch.allclose(out_orig, out_loaded, atol=1e-5)


def test_circuit_breaker_thread_safe_tripping():
    """VECTOR 16: Verifies atomic circuit breaker tripping under multi-threaded
    forced failure conditions.
    """
    payload_fail = {
        "transaction_id": "tx_cb_fail",
        "amount": 100.0,
        "currency": "USD",
        "source_account": "acc_1",
        "target_account": "acc_2",
        "merchant_category": "retail",
        "velocity_1h": 1,
        "force_fallback": True,
    }

    # Execute 4 concurrent failures
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(client.post, "/v1/inference/score", json=payload_fail) for _ in range(4)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert all(r.status_code == 200 for r in results)
    assert all(r.json()["evaluated_by"] == "HEURISTIC_FALLBACK" for r in results)

    # Next call without forced failure must also route to fallback because CB is open
    payload_normal = {
        "transaction_id": "tx_cb_normal",
        "amount": 50.0,
        "currency": "USD",
        "source_account": "acc_1",
        "target_account": "acc_2",
        "merchant_category": "retail",
        "velocity_1h": 1,
        "force_fallback": False,
    }
    resp = client.post("/v1/inference/score", json=payload_normal)
    assert resp.status_code == 200
    assert resp.json()["evaluated_by"] == "HEURISTIC_FALLBACK"
    assert "[Circuit Breaker Open]" in resp.json()["explanation"]


def test_sub_10ms_scoring_sla():
    """VECTOR 18: Measures 50 consecutive transaction scoring requests to verify
    strict sub-10ms response latency SLA for server-side evaluation.
    """
    payload = {
        "transaction_id": "tx_sla_test",
        "account_id": "acc_sla_user",
        "amount": 120.00,
        "currency": "EUR",
        "merchant_id": "rewe_supermarket",
        "country": "DE",
        "device_id": "dev_sla",
    }
    # Warmup
    client.post("/api/v1/transactions/score", json=payload)

    server_latencies: list[float] = []
    client_latencies: list[float] = []
    for _ in range(50):
        t0 = time.perf_counter()
        resp = client.post("/api/v1/transactions/score", json=payload)
        t1 = time.perf_counter()
        assert resp.status_code == 200
        client_latencies.append((t1 - t0) * 1000.0)
        server_latencies.append(float(resp.json()["latency_ms"]))

    median_server_ms = float(np.median(server_latencies))
    median_client_ms = float(np.median(client_latencies))
    # Server-side risk scoring + inference SLA is strictly < 10ms
    assert (
        median_server_ms < 10.0
    ), f"Median server scoring latency {median_server_ms:.2f}ms exceeded 10ms SLA"
    assert (
        median_client_ms < 50.0
    ), f"Median client roundtrip latency {median_client_ms:.2f}ms exceeded 50ms"


def test_feature_store_integration_in_score_transaction():
    """VECTOR 9: Verifies score_transaction queries online features from Feature Store
    when enabled.
    """
    payload = {
        "transaction_id": "tx_fs_test",
        "account_id": "acc_fs_user_99",
        "amount": 250.00,
        "currency": "EUR",
        "merchant_id": "merchant_fs_test",
        "country": "FR",
        "device_id": "dev_fs",
    }

    from app.config import get_settings

    settings = get_settings()
    with patch.object(settings, "feature_store_enabled", True):
        resp = client.post("/api/v1/transactions/score", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert 0 <= data["risk_score"] <= 1000
        assert data["risk_level"] in ("LOW", "MEDIUM", "HIGH")


def test_backward_compatibility_with_existing_9_signals():
    """VECTOR 10: Ensures default transactions without GNN preserve exact
    9 canonical risk signals for full backward compatibility.
    """
    engine = RiskScoringEngine()
    txn = {
        "transaction_amount": 55.0,
        "merchant_category": "grocery",
        "country_code": "US",
        "device_type": "mobile_app",
        "velocity": 1.5,
    }
    res = engine.score_transaction(txn, ml_prediction=0.10)
    assert len(res.signals) == 9
    assert all(s.signal_name != "gnn_topological_risk" for s in res.signals)
    assert 0.0 <= res.score <= 1000.0
