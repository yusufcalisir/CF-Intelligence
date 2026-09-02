"""Unit tests for Connector Diagnostics Service & API Router."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.application.services.connector_diagnostics_service import (
    ConnectorDiagnosticsService,
)
from app.main import app


@pytest.fixture
def diagnostics_service() -> ConnectorDiagnosticsService:
    return ConnectorDiagnosticsService()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_get_all_connector_statuses(diagnostics_service: ConnectorDiagnosticsService) -> None:
    """Verify all 7 enterprise infrastructure connectors are listed and healthy."""
    statuses = diagnostics_service.get_all_connector_statuses()
    assert len(statuses) == 7

    ids = {s.connector_id for s in statuses}
    assert ids == {"kafka", "vault", "kms", "splunk", "redis", "database", "iso20022"}

    for s in statuses:
        assert s.status == "HEALTHY"
        assert s.latency_ms > 0
        assert s.protocol != ""
        assert s.endpoint != ""
        assert isinstance(s.details, dict)


@pytest.mark.parametrize(
    "connector_id",
    ["kafka", "vault", "kms", "splunk", "redis", "database", "iso20022"],
)
def test_test_connector_probes(
    diagnostics_service: ConnectorDiagnosticsService,
    connector_id: str,
) -> None:
    """Verify on-demand active test probes succeed with diagnostics logs for each connector."""
    result = diagnostics_service.test_connector(connector_id)
    assert result.connector_id == connector_id
    assert result.success is True
    assert result.status_code == 200
    assert result.round_trip_ms > 0
    assert len(result.diagnostics_log) >= 2
    assert isinstance(result.payload_sample, dict)


def test_api_get_connectors_overview(client: TestClient) -> None:
    """Verify GET /api/v1/diagnostics/connectors endpoint."""
    response = client.get("/api/v1/diagnostics/connectors")
    assert response.status_code == 200

    data = response.json()
    assert data["total_connectors"] == 7
    assert data["healthy_connectors"] == 7
    assert data["avg_latency_ms"] > 0
    assert len(data["connectors"]) == 7


def test_api_test_connector_endpoint(client: TestClient) -> None:
    """Verify POST /api/v1/diagnostics/test-connector endpoint."""
    response = client.post(
        "/api/v1/diagnostics/test-connector",
        json={"connector_id": "vault"},
    )
    assert response.status_code == 200

    data = response.json()
    assert data["connector_id"] == "vault"
    assert data["success"] is True
    assert "Vault" in data["handshake_summary"]
    assert len(data["diagnostics_log"]) > 0


def test_api_test_connector_empty_id(client: TestClient) -> None:
    """Verify POST /api/v1/diagnostics/test-connector rejects blank connector ID."""
    response = client.post(
        "/api/v1/diagnostics/test-connector",
        json={"connector_id": "   "},
    )
    assert response.status_code == 400
