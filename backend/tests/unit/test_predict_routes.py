"""Unit and integration test suite for Real-Time Transaction Scoring & Inference API routes.

Validates single scoring, batch inference, SHAP explainability, tenant quota metering,
dual prefix routing, and multi-tenant isolation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_session
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def override_db_and_feature_store(monkeypatch):
    """Provide clean database session and disable live Redis feature store during testing."""
    mock_session = MagicMock()
    mock_session.execute = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = mock_result

    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "feature_store_enabled", False)

    app.dependency_overrides[get_session] = lambda: mock_session
    yield
    app.dependency_overrides.clear()


def test_predict_single_endpoint_flow() -> None:
    """Verify single transaction scoring evaluates model, composite risk, and returns latency."""
    payload = {
        "transaction_amount": 120.00,
        "merchant_category": "retail",
        "country_code": "US",
        "device_type": "mobile_app",
        "velocity": 1.5,
        "hour_of_day": 15,
        "merchant_risk_score": 0.04,
        "customer_history_score": 0.96,
        "chargeback_count": 0,
        "account_age_days": 450,
        "bank_id": "bank_alpha",
    }

    response = client.post("/api/v1/predict", json=payload)
    assert response.status_code == 200, response.text

    data = response.json()
    assert "fraud_probability" in data
    assert 0.0 <= data["fraud_probability"] <= 1.0
    assert "risk_score" in data
    assert 0.0 <= data["risk_score"] <= 1000.0
    assert data["risk_level"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
    assert isinstance(data["breakdown"], list)
    assert len(data["breakdown"]) > 0
    assert "policy_action" in data
    assert "latency_ms" in data
    assert data["latency_ms"] >= 0.0


def test_predict_batch_endpoint_flow() -> None:
    """Verify batch inference scores multiple transactions with batch latency and itemized results."""
    payload = {
        "transactions": [
            {
                "transaction_amount": 35.00,
                "merchant_category": "grocery",
                "country_code": "US",
                "device_type": "web_browser",
                "velocity": 1.0,
                "hour_of_day": 10,
                "merchant_risk_score": 0.02,
                "customer_history_score": 0.99,
                "chargeback_count": 0,
                "account_age_days": 700,
                "bank_id": "bank_alpha",
                "transaction_id": "tx_batch_001",
            },
            {
                "transaction_amount": 48000.00,
                "merchant_category": "crypto",
                "country_code": "NG",
                "device_type": "web_browser",
                "velocity": 18.0,
                "hour_of_day": 3,
                "merchant_risk_score": 0.88,
                "customer_history_score": 0.05,
                "chargeback_count": 6,
                "account_age_days": 2,
                "bank_id": "bank_alpha",
                "transaction_id": "tx_batch_002",
            },
            {
                "transaction_amount": 250.00,
                "merchant_category": "travel",
                "country_code": "DE",
                "device_type": "mobile_app",
                "velocity": 2.0,
                "hour_of_day": 16,
                "merchant_risk_score": 0.15,
                "customer_history_score": 0.90,
                "chargeback_count": 0,
                "account_age_days": 300,
                "bank_id": "bank_alpha",
                "transaction_id": "tx_batch_003",
            },
        ],
        "bank_id": "bank_alpha",
    }

    response = client.post("/api/v1/predict/batch", json=payload)
    assert response.status_code == 200, response.text

    data = response.json()
    assert data["total_processed"] == 3
    assert data["fraud_suspected_count"] >= 0
    assert len(data["predictions"]) == 3
    assert data["batch_latency_ms"] >= 0.0

    tx1 = data["predictions"][0]
    assert tx1["transaction_id"] == "tx_batch_001"
    assert tx1["decision"] in ("ALLOW", "REVIEW", "BLOCK")
    assert tx1["risk_score"] >= 0.0

    tx2 = data["predictions"][1]
    assert tx2["transaction_id"] == "tx_batch_002"
    assert tx2["is_fraud_suspected"] is True


def test_predict_batch_quota_rejection() -> None:
    """Verify batch request returns HTTP 429 when tenant quota is exhausted."""
    payload = {
        "transactions": [
            {"transaction_amount": 50.0, "merchant_category": "retail"} for _ in range(5)
        ],
        "bank_id": "quota_exhausted_bank",
    }

    with patch(
        "app.application.services.tenant_metering.TenantMeteringService.acquire_quota",
        return_value=(False, "Daily inference quota limit of 10000 reached"),
    ):
        response = client.post("/api/v1/predict/batch", json=payload)
        assert response.status_code == 429
        assert "quota" in response.json()["detail"].lower()
        assert response.headers.get("X-Quota-Exceeded") == "true"



def test_predict_explain_shap_and_counterfactuals() -> None:
    """Verify SHAP explainability endpoint returns feature attributions and counterfactual recommendations."""
    payload = {
        "transaction": {
            "transaction_amount": 15000.0,
            "merchant_category": "crypto",
            "country_code": "NG",
            "device_type": "mobile_app",
            "velocity": 12.0,
            "hour_of_day": 2,
            "merchant_risk_score": 0.85,
            "customer_history_score": 0.10,
            "chargeback_count": 3,
            "account_age_days": 10,
            "bank_id": "bank_alpha",
            "transaction_id": "tx_explain_001",
        },
        "method": "SHAP",
    }

    response = client.post("/api/v1/predict/explain", json=payload)
    assert response.status_code == 200, response.text

    data = response.json()
    assert data["transaction_id"] == "tx_explain_001"
    assert data["method"] == "SHAP"
    assert "base_value" in data
    assert "predicted_score" in data
    assert len(data["attributions"]) > 0
    assert any(a["feature"] == "transaction_amount" for a in data["attributions"])
    assert len(data["counterfactual_paths"]) > 0
    assert "latency_ms" in data


def test_realtime_inference_dual_prefix_routing() -> None:
    """Verify realtime inference endpoint is accessible via both /v1/inference and /api/v1/inference."""
    payload = {
        "transaction_id": "tx_dual_001",
        "amount": 120.50,
        "currency": "USD",
        "source_account": "acc_001",
        "target_account": "acc_002",
        "merchant_category": "retail",
        "velocity_1h": 1,
    }

    # Test legacy path /v1/inference/score
    res_v1 = client.post("/v1/inference/score", json=payload)
    assert res_v1.status_code == 200, res_v1.text
    assert res_v1.json()["transaction_id"] == "tx_dual_001"

    # Test standardized API path /api/v1/inference/score
    res_api = client.post("/api/v1/inference/score", json=payload)
    assert res_api.status_code == 200, res_api.text
    assert res_api.json()["transaction_id"] == "tx_dual_001"


def test_inference_quota_telemetry_endpoint() -> None:
    """Verify /api/v1/inference/quota returns configured tenant limits, usage, and reset date."""
    # Test on /api/v1/inference/quota
    res_api = client.get("/api/v1/inference/quota", headers={"X-Tenant-ID": "bank_alpha"})
    assert res_api.status_code == 200, res_api.text

    data = res_api.json()
    assert data["tenant_id"] == "bank_alpha"
    assert data["daily_inferences_limit"] == 10000
    assert data["daily_inferences_remaining"] <= 10000
    assert "monthly_fl_rounds_limit" in data
    assert "reset_date" in data

    # Test on legacy /v1/inference/quota
    res_v1 = client.get("/v1/inference/quota", headers={"X-Tenant-ID": "bank_beta"})
    assert res_v1.status_code == 200, res_v1.text
    assert res_v1.json()["tenant_id"] == "bank_beta"


def test_cross_tenant_isolation_enforcement() -> None:
    """Verify cross-tenant BOLA tampering on predict endpoint is strictly blocked with HTTP 403."""
    payload = {
        "transaction_amount": 500.0,
        "merchant_category": "grocery",
        "bank_id": "bank_victim",
    }

    # Caller identified as bank_attacker attempting to score as bank_victim
    response = client.post(
        "/api/v1/predict",
        json=payload,
        headers={"X-Tenant-ID": "bank_attacker"},
    )
    assert response.status_code == 403
    assert (
        "not authorized" in response.json()["detail"].lower()
        or "broken access control" in response.json()["detail"].lower()
    )


def test_score_transaction_routes_parity() -> None:
    """Verify all aliased endpoints for low-latency score transaction return identical schema."""
    payload = {
        "transaction_id": "tx_alias_001",
        "account_id": "acc_001",
        "amount": 250.0,
        "currency": "EUR",
        "merchant_id": "merch_001",
        "country": "FR",
        "device_id": "dev_001",
    }

    for path in ("/api/v1/score-transaction", "/api/v1/transactions/score", "/api/v1/predict/score"):
        res = client.post(path, json=payload)
        assert res.status_code == 200, f"Failed on {path}: {res.text}"
        data = res.json()
        assert "risk_score" in data
        assert "risk_level" in data
        assert "decision" in data
        assert "latency_ms" in data
