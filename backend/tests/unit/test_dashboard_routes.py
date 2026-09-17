"""Unit tests for Executive KPI Dashboard API routes.

Validates consolidated KPI card metrics, severity and bank groupings,
merchant risk entity resolution, and dynamic risk scoring weights.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain.entities_phase2 import Alert, Entity
from app.domain.enums import AlertSeverity, EntityType, RiskLevel
from app.main import app
from app.presentation.routers.alerts import get_alert_service
from app.presentation.routers.dashboard import get_risk_engine
from app.presentation.routers.entities import get_entity_service


@pytest.fixture
def client() -> TestClient:
    """TestClient bound to main application."""
    return TestClient(app)


def test_get_dashboard_stats_success(client: TestClient) -> None:
    """Verify GET /api/v1/dashboard/stats returns all consolidated KPI fields."""
    response = client.get("/api/v1/dashboard/stats")
    assert response.status_code == 200
    data = response.json()

    # All required KPI fields present and non-negative
    required_fields = [
        "total_alerts",
        "critical_alerts",
        "open_cases",
        "total_entities",
        "shared_intelligence_items",
        "cross_institution_matches",
        "active_scenarios",
        "graph_clusters",
    ]
    for field in required_fields:
        assert field in data
        assert isinstance(data[field], int)
        assert data[field] >= 0


def test_get_dashboard_stats_with_tenant_filter(client: TestClient) -> None:
    """Verify GET /api/v1/dashboard/stats scopes metrics when bank_id is provided."""
    response = client.get("/api/v1/dashboard/stats", params={"bank_id": "bank_alpha"})
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["total_alerts"], int)
    assert isinstance(data["cross_institution_matches"], int)


def test_alerts_by_severity(client: TestClient) -> None:
    """Verify GET /api/v1/dashboard/alerts-by-severity returns typed counts."""
    response = client.get("/api/v1/dashboard/alerts-by-severity")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)
    for k, v in data.items():
        assert isinstance(k, str)
        assert isinstance(v, int)
        assert v >= 0


def test_alerts_by_bank(client: TestClient) -> None:
    """Verify GET /api/v1/dashboard/alerts-by-bank returns counts grouped by bank."""
    response = client.get("/api/v1/dashboard/alerts-by-bank")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)
    for bank, count in data.items():
        assert isinstance(bank, str)
        assert isinstance(count, int)
        assert count >= 0


def test_entities_by_risk(client: TestClient) -> None:
    """Verify GET /api/v1/dashboard/entities-by-risk returns counts grouped by risk level."""
    response = client.get("/api/v1/dashboard/entities-by-risk")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)
    for risk_lvl, count in data.items():
        assert isinstance(risk_lvl, str)
        assert isinstance(count, int)
        assert count >= 0


def test_top_risky_merchants_returns_typed_list(client: TestClient) -> None:
    """Verify GET /api/v1/dashboard/top-risky-merchants returns MerchantRiskItem models."""
    response = client.get("/api/v1/dashboard/top-risky-merchants")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    for item in data:
        assert "merchant" in item
        assert "alert_count" in item
        assert isinstance(item["merchant"], str)
        assert isinstance(item["alert_count"], int)
        assert item["alert_count"] >= 0
        # Verify zero mock fallback "high_risk_merchant"
        assert item["merchant"] != "high_risk_merchant"


def test_top_risky_merchants_with_registered_entities(client: TestClient) -> None:
    """Verify top merchants resolves real merchant entities from entity and alert services."""
    entity_svc = get_entity_service()
    alert_svc = get_alert_service()

    # Register merchant entity
    merchant_ent = entity_svc.create_entity(
        entity_type=EntityType.MERCHANT,
        raw_identifier="Apex Electronics Global",
        bank_id="bank_alpha",
        attributes={"merchant_name": "Apex Electronics Global"},
    )

    # Register alert linking to this merchant
    alert = Alert(
        bank_id="bank_alpha",
        severity=AlertSeverity.HIGH,
        involved_entity_ids=[merchant_ent.id],
        reason_codes=["MERCH-HIGH-CHARGEBACK"],
    )
    alert_svc.create_alert(alert)

    response = client.get("/api/v1/dashboard/top-risky-merchants", params={"limit": 5})
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    merchants = [m["merchant"] for m in data]
    assert "Apex Electronics Global" in merchants


def test_top_risky_merchants_limit_query_param(client: TestClient) -> None:
    """Verify top merchants endpoint respects the limit parameter."""
    response = client.get("/api/v1/dashboard/top-risky-merchants", params={"limit": 3})
    assert response.status_code == 200
    data = response.json()
    assert len(data) <= 3


def test_get_risk_weights(client: TestClient) -> None:
    """Verify GET /api/v1/dashboard/risk-weights returns all 10 composite weights."""
    response = client.get("/api/v1/dashboard/risk-weights")
    assert response.status_code == 200
    data = response.json()

    expected_keys = [
        "ml_prediction",
        "velocity_rules",
        "merchant_reputation",
        "country_risk",
        "device_anomaly",
        "customer_history",
        "previous_alerts",
        "chargeback_history",
        "behavior_anomaly",
        "gnn_topological_risk",
    ]
    for key in expected_keys:
        assert key in data
        assert isinstance(data[key], (int, float))
        assert 0.0 <= data[key] <= 1.0


def test_update_risk_weights_valid(client: TestClient) -> None:
    """Verify PUT /api/v1/dashboard/risk-weights successfully updates scoring weights."""
    update_payload = {
        "ml_prediction": 0.30,
        "velocity_rules": 0.10,
        "merchant_reputation": 0.10,
        "country_risk": 0.10,
        "device_anomaly": 0.10,
        "customer_history": 0.10,
        "previous_alerts": 0.05,
        "chargeback_history": 0.05,
        "behavior_anomaly": 0.05,
        "gnn_topological_risk": 0.05,
    }
    response = client.put("/api/v1/dashboard/risk-weights", json=update_payload)
    assert response.status_code == 200
    data = response.json()
    for k, v in update_payload.items():
        assert data[k] == pytest.approx(v, abs=1e-4)

    # Verify risk engine internal weights were updated
    risk_engine = get_risk_engine()
    assert risk_engine.weights.ml_prediction == pytest.approx(0.30, abs=1e-4)
    assert risk_engine.weights.gnn_topological_risk == pytest.approx(0.05, abs=1e-4)


def test_update_risk_weights_invalid_bounds_rejected(client: TestClient) -> None:
    """Verify PUT /api/v1/dashboard/risk-weights rejects out-of-bound weights with 422."""
    invalid_payload = {
        "ml_prediction": 1.5,  # Invalid: > 1.0
        "velocity_rules": 0.10,
        "merchant_reputation": 0.10,
        "country_risk": 0.10,
        "device_anomaly": 0.10,
        "customer_history": 0.10,
        "previous_alerts": 0.05,
        "chargeback_history": 0.05,
        "behavior_anomaly": 0.05,
        "gnn_topological_risk": 0.05,
    }
    response = client.put("/api/v1/dashboard/risk-weights", json=invalid_payload)
    assert response.status_code == 422


def test_update_risk_weights_forbid_extra_fields(client: TestClient) -> None:
    """Verify extra attributes in PUT /api/v1/dashboard/risk-weights trigger 422."""
    payload_with_extra = {
        "ml_prediction": 0.25,
        "velocity_rules": 0.15,
        "merchant_reputation": 0.10,
        "country_risk": 0.10,
        "device_anomaly": 0.08,
        "customer_history": 0.10,
        "previous_alerts": 0.08,
        "chargeback_history": 0.07,
        "behavior_anomaly": 0.07,
        "gnn_topological_risk": 0.0,
        "unauthorized_field": 99.9,
    }
    response = client.put("/api/v1/dashboard/risk-weights", json=payload_with_extra)
    assert response.status_code == 422
