"""
Unit test for Phase 18: API Contract & OpenAPI Spec Accuracy.
Verifies:
1. Dynamic OpenAPI schema generation and path completeness.
2. Endpoint request/response models match live schemas.
3. POST /api/v1/cases/export/fincen-xml operates correctly.
"""

from fastapi.testclient import TestClient
import pytest
from app.main import app
from app.domain.entities_phase2 import Case
from app.domain.enums import CasePriority, CaseStatus
from app.presentation.routers.cases import _case_service


@pytest.fixture
def client():
    return TestClient(app)


def test_openapi_schema_generation():
    """Verify that openapi.json generates successfully and contains expected endpoints."""
    schema = app.openapi()
    assert schema["info"]["title"] == "Collaborative Fraud Intelligence Simulator"
    assert len(schema["paths"]) >= 140

    required_endpoints = [
        ("/api/v1/predict", "post"),
        ("/api/v1/security/status", "get"),
        ("/api/v1/cases", "get"),
        ("/api/v1/cases/export/fincen-xml", "post"),
        ("/v1/cron/rotate-keys", "post"),
        ("/api/v1/score-transaction", "post"),
    ]

    for path, method in required_endpoints:
        assert path in schema["paths"], f"Missing endpoint {path} in OpenAPI paths"
        assert method in schema["paths"][path], f"Missing method {method} for {path} in OpenAPI paths"


def test_fincen_xml_export_endpoint(client):
    """Verify POST /api/v1/cases/export/fincen-xml contract execution."""
    # Register a confirmed fraud case
    test_case = Case(
        id="CASE-OPENAPI-001",
        title="OpenAPI Contract Test Case",
        status=CaseStatus.CLOSED_CONFIRMED,
        priority=CasePriority.P2_HIGH,
        total_risk_score=0.91,
        alert_ids=["ALT-OPENAPI-1"],
        assigned_to="lead_auditor",
    )
    from app.application.services.case_service import _case_to_dict

    _case_service._cases.set(test_case.id, _case_to_dict(test_case))

    resp = client.post(
        "/api/v1/cases/export/fincen-xml",
        json={"case_id": "CASE-OPENAPI-001"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "FILED"
    assert "submission_id" in data
    assert "<EFilingSubmission" in data["xml"]
    assert "<ReportingInstitution>" in data["xml"]
