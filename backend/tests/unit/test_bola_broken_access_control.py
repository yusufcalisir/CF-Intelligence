"""Unit tests for Broken Object-Level Authorization (BOLA/IDOR) and Multi-Tenant Isolation (Section 13 / OWASP API Security)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain.entities_phase2 import Alert
from app.domain.enums import AlertSeverity, AlertStatus, EntityType
from app.infrastructure.security.oidc_authenticator import OIDCAuthenticator
from app.main import app, seed_mock_data
from app.presentation.routers.alerts import get_alert_service
from app.presentation.routers.entities import get_entity_service


@pytest.fixture(scope="module", autouse=True)
def setup_seed_data():
    """Ensure mock seed data exists in the in-memory test databases."""
    seed_mock_data()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def oidc():
    return OIDCAuthenticator()


def test_bank_a_cannot_access_bank_b_alerts_via_query_param(client: TestClient):
    """Test BOLA prevention: Bank A caller passing ?bank_id=bank_b gets HTTP 403 Forbidden."""
    # Bank A caller requests Bank B's alerts
    resp = client.get(
        "/api/v1/alerts?bank_id=bank_b",
        headers={"X-Tenant-ID": "bank_a"},
    )
    assert resp.status_code == 403
    data = resp.json()
    assert "Broken Access Control" in data.get("detail", "") or "TenantAccessDenied" in data.get("type", "")


def test_bank_a_cannot_access_bank_b_alert_by_id(client: TestClient):
    """Test BOLA / IDOR prevention: Bank A caller cannot view Bank B's alert detail by guessing alert_id."""
    alert_svc = get_alert_service()
    bank_b_alert = Alert(
        bank_id="bank_b",
        transaction_id="tx_bank_b_secret_99",
        risk_score=900.0,
        severity=AlertSeverity.CRITICAL,
        status=AlertStatus.NEW,
        reason_codes=["VEL-001"],
        confidence=0.9,
    )
    alert_svc._alert_store.set(bank_b_alert.id, {
        "id": bank_b_alert.id,
        "bank_id": "bank_b",
        "transaction_id": bank_b_alert.transaction_id,
        "risk_score": bank_b_alert.risk_score,
        "severity": bank_b_alert.severity.value,
        "status": bank_b_alert.status.value,
        "reason_codes": bank_b_alert.reason_codes,
        "confidence": bank_b_alert.confidence,
        "involved_entity_ids": [],
        "created_at": bank_b_alert.created_at.isoformat(),
        "top_features": [],
        "risk_factors": [],
        "model_confidence": 0.9,
    })

    # Bank A attempts to fetch Bank B's alert detail
    resp = client.get(
        f"/api/v1/alerts/{bank_b_alert.id}",
        headers={"X-Tenant-ID": "bank_a"},
    )
    assert resp.status_code == 403
    assert "Broken Access Control Prevention" in resp.json().get("detail", "")


def test_bank_a_cannot_access_bank_b_alert_explainability(client: TestClient):
    """Test BOLA prevention on explainability endpoint."""
    alert_svc = get_alert_service()
    bank_b_alert = Alert(
        bank_id="bank_b",
        transaction_id="tx_bank_b_secret_100",
        risk_score=750.0,
        severity=AlertSeverity.HIGH,
        status=AlertStatus.NEW,
        reason_codes=["DEV-ANOM"],
        confidence=0.85,
    )
    alert_svc._alert_store.set(bank_b_alert.id, {
        "id": bank_b_alert.id,
        "bank_id": "bank_b",
        "transaction_id": bank_b_alert.transaction_id,
        "risk_score": bank_b_alert.risk_score,
        "severity": bank_b_alert.severity.value,
        "status": bank_b_alert.status.value,
        "reason_codes": bank_b_alert.reason_codes,
        "confidence": bank_b_alert.confidence,
        "involved_entity_ids": [],
        "created_at": bank_b_alert.created_at.isoformat(),
        "top_features": [],
        "risk_factors": [],
        "model_confidence": 0.85,
    })

    resp = client.get(
        f"/api/v1/alerts/{bank_b_alert.id}/explain",
        headers={"X-Tenant-ID": "bank_a"},
    )
    assert resp.status_code == 403


def test_bank_a_cannot_access_bank_b_entity(client: TestClient):
    """Test BOLA prevention on entity profile endpoint."""
    ent_svc = get_entity_service()
    e_bank_b = ent_svc.create_entity(
        entity_type=EntityType.CUSTOMER,
        raw_identifier="customer_bank_b_private_account_01",
        bank_id="bank_b",
        attributes={"risk_score": 0.72},
    )

    resp = client.get(
        f"/api/v1/entities/{e_bank_b.id}",
        headers={"X-Tenant-ID": "bank_a"},
    )
    assert resp.status_code == 403
    assert "Broken Access Control Prevention" in resp.json().get("detail", "")


def test_unscoped_query_defaults_to_caller_tenant(client: TestClient):
    """Test that if Bank A caller passes no bank_id, alerts are scoped to Bank A."""
    resp = client.get(
        "/api/v1/alerts",
        headers={"X-Tenant-ID": "bank_a"},
    )
    assert resp.status_code == 200
    alerts = resp.json()
    for a in alerts:
        assert a["bank_id"] == "bank_a"


def test_cross_bank_investigator_can_access_cross_institution(client: TestClient, oidc: OIDCAuthenticator):
    """Test that privileged role (cross_bank_investigator) bypasses tenant restriction."""
    token = oidc.create_mock_token(
        username="super_investigator",
        bank_id="bank_a",
        roles=["cross_bank_investigator"],
    )

    resp = client.get(
        "/api/v1/alerts?bank_id=bank_b",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
