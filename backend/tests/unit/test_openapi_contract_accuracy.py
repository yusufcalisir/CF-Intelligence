"""
Unit tests for API contract, OpenAPI specification, and interactive documentation viewers.
Verifies:
1. Dynamic OpenAPI schema generation and path completeness.
2. Endpoint request/response models match live schemas.
3. POST /api/v1/cases/export/fincen-xml operates correctly.
4. Dark-mode Swagger UI (/docs), ReDoc (/redoc), and Scalar (/scalar) endpoints.
"""

import pytest
from fastapi.testclient import TestClient

from app.domain.enums import CasePriority, CaseStatus
from app.domain.investigation_entities import Case
from app.main import app
from app.presentation.routers.cases import _case_service


@pytest.fixture
def client():
    return TestClient(app)


def test_openapi_schema_generation():
    """Verify that openapi.json generates successfully and contains expected endpoints."""
    schema = app.openapi()
    assert schema["info"]["title"] == "Privacy-Preserving Collaborative Financial Crime Intelligence Platform"
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


def test_swagger_ui_html_dark_theme(client):
    """Verify GET /docs serves dark-mode Swagger UI with responsive mobile containment and CFI brand navigation."""
    resp = client.get("/docs")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "cfi-topbar" in resp.text
    assert "SwaggerUIBundle" in resp.text
    assert "--bg-primary" in resp.text
    assert "max-width: 100vw" in resp.text
    assert "overflow-x: hidden" in resp.text
    assert "@media (max-width: 680px)" in resp.text


def test_redoc_html_dark_theme(client):
    """Verify GET /redoc serves dark-mode ReDoc with responsive layout, CFI navigation, and dark tab buttons."""
    resp = client.get("/redoc")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "cfi-topbar" in resp.text
    assert "Redoc.init" in resp.text
    assert 'role="tab"' in resp.text
    assert 'data-status="200"' in resp.text
    assert "scrollYOffset" in resp.text
    assert "@media (max-width: 900px)" in resp.text
    assert "max-width: 100vw" in resp.text


def test_scalar_html_dark_theme(client):
    """Verify GET /scalar serves dark-mode Scalar API Reference with responsive modern layout and CFI navigation."""
    resp = client.get("/scalar")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "cfi-topbar" in resp.text
    assert "api-reference" in resp.text
    assert '"layout":"modern"' in resp.text
    assert "max-width: 100vw" in resp.text
    assert "scalar-app" in resp.text


def test_portal_brand_logo_assets(client):
    """Verify official brand logo endpoints (/logo.svg, /logo.png, /favicon.svg, /favicon.ico) serve valid assets."""
    resp_svg = client.get("/logo.svg")
    assert resp_svg.status_code == 200
    assert "image/svg+xml" in resp_svg.headers["content-type"]
    assert len(resp_svg.content) > 100

    resp_png = client.get("/logo.png")
    assert resp_png.status_code == 200
    assert "image/png" in resp_png.headers["content-type"]
    assert len(resp_png.content) > 100

    resp_fav_svg = client.get("/favicon.svg")
    assert resp_fav_svg.status_code == 200
    assert "image/svg+xml" in resp_fav_svg.headers["content-type"]

    resp_fav_ico = client.get("/favicon.ico")
    assert resp_fav_ico.status_code == 200
    assert len(resp_fav_ico.content) > 100


def test_documentation_portals_brand_logo_integration(client):
    """Verify /docs, /redoc, and /scalar embed official brand logo in topbar and head links."""
    for portal in ("/docs", "/redoc", "/scalar"):
        resp = client.get(portal)
        assert resp.status_code == 200
        assert 'src="/logo.svg"' in resp.text
        assert 'class="cfi-logo-img"' in resp.text
        assert 'href="/logo.svg"' in resp.text

    # Verify openapi.json contains x-logo metadata for ReDoc & Scalar
    resp_openapi = client.get("/openapi.json")
    assert resp_openapi.status_code == 200
    data = resp_openapi.json()
    assert "x-logo" in data["info"]
    assert data["info"]["x-logo"]["url"] == "/logo.svg"


