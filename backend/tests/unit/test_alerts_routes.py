"""Unit and integration tests for Alert Management, Triage & Deduplication API.

Validates:
1. Dual-prefix route discovery parity (/api/v1/alerts and /v1/alerts)
2. Alert listing with bank_id, severity, and status filtering
3. Single alert detail retrieval (200 OK and 404 Not Found)
4. Sliding-window deduplication statistics endpoint (/alerts/dedup/stats)
5. Alert status update via both PATCH and PUT semantics with validation
6. On-demand alert triage re-evaluation (/alerts/{alert_id}/triage)
7. Standalone triage rule evaluation without persistence (/alerts/triage/evaluate)
8. Multi-tenant access control and BOLA enforcement (403 Forbidden)
9. Explainability reports, counterfactuals, decision-replay, and GNN attribution
10. Shared intelligence feed and aggregate statistics (/intelligence and /intelligence/stats)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from app.dependencies import resolve_tenant
from app.domain.entities_phase2 import Alert
from app.domain.enums import AlertSeverity, AlertStatus, TriageAction, TriagePriority
from app.main import app
from app.presentation.routers.alerts import get_alert_service

if TYPE_CHECKING:
    from app.application.services.alert_service import AlertIntelligenceService


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[resolve_tenant] = lambda: None
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def populated_alert_service() -> AlertIntelligenceService:
    svc = get_alert_service()
    # Seed a known alert for testing
    test_alert = Alert(
        id="alert_test_001",
        bank_id="bank_a",
        transaction_id="txn_test_001",
        risk_score=850.0,
        severity=AlertSeverity.HIGH,
        status=AlertStatus.NEW,
        reason_codes=["VEL-001", "GEO-RISK"],
        confidence=0.85,
        involved_entity_ids=["entity_001", "entity_002"],
        triage_priority=TriagePriority.P2_HIGH,
        triage_action=TriageAction.INVESTIGATE_CASE,
        sla_minutes=120,
        triage_reasons=["High risk score threshold (850.0/1000)"],
    )
    svc.create_alert(test_alert)
    return svc


def test_alerts_list_dual_routing(client: TestClient, populated_alert_service: AlertIntelligenceService) -> None:
    """Verify both /api/v1/alerts and /v1/alerts return status 200 with list of alerts."""
    r1 = client.get("/api/v1/alerts")
    assert r1.status_code == 200
    assert isinstance(r1.json(), list)

    r2 = client.get("/v1/alerts")
    assert r2.status_code == 200
    assert isinstance(r2.json(), list)
    assert len(r1.json()) == len(r2.json())


def test_alerts_list_filters(client: TestClient, populated_alert_service: AlertIntelligenceService) -> None:
    """Verify bank_id, severity, and status query filters."""
    # Filter by existing bank
    r = client.get("/api/v1/alerts", params={"bank_id": "bank_a", "severity": "high", "status": "new"})
    assert r.status_code == 200
    alerts = r.json()
    assert len(alerts) >= 1
    assert all(a["bank_id"] == "bank_a" for a in alerts)
    assert all(a["severity"] == "high" for a in alerts)

    # Filter with invalid severity returns 422
    r_bad_sev = client.get("/api/v1/alerts", params={"severity": "catastrophic"})
    assert r_bad_sev.status_code == 422

    # Filter with invalid status returns 422
    r_bad_stat = client.get("/api/v1/alerts", params={"status": "invalid_status"})
    assert r_bad_stat.status_code == 422


def test_alert_detail_success_and_not_found(client: TestClient, populated_alert_service: AlertIntelligenceService) -> None:
    """Verify single alert lookup succeeds for known alert and returns 404 for unknown."""
    r_ok = client.get("/api/v1/alerts/alert_test_001")
    assert r_ok.status_code == 200
    body = r_ok.json()
    assert body["id"] == "alert_test_001"
    assert body["bank_id"] == "bank_a"
    assert body["risk_score"] == 850.0

    # /v1 prefix parity
    r_v1 = client.get("/v1/alerts/alert_test_001")
    assert r_v1.status_code == 200
    assert r_v1.json()["id"] == "alert_test_001"

    # Non-existent ID returns RFC compliant 404
    r_404 = client.get("/api/v1/alerts/non_existent_alert_id_xyz")
    assert r_404.status_code == 404
    assert "not found" in r_404.json()["detail"].lower()


def test_alert_deduplication_stats(client: TestClient, populated_alert_service: AlertIntelligenceService) -> None:
    """Verify sliding-window deduplication statistics are returned under both prefixes."""
    r1 = client.get("/api/v1/alerts/dedup/stats")
    assert r1.status_code == 200
    stats = r1.json()
    assert "total_processed" in stats
    assert "duplicates_detected" in stats
    assert "deduplication_ratio" in stats
    assert "window_seconds" in stats

    r2 = client.get("/v1/alerts/dedup/stats")
    assert r2.status_code == 200
    assert r2.json() == stats


def test_alert_status_update_patch_and_put(client: TestClient, populated_alert_service: AlertIntelligenceService) -> None:
    """Verify status update works via both PATCH and PUT with authentic 200, 404, 422 responses."""
    # PATCH update to investigating
    r_patch = client.patch(
        "/api/v1/alerts/alert_test_001/status",
        json={"status": "investigating", "resolution_notes": "Analyst assigned to review"},
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["status"] == "investigating"

    # PUT update via /v1 to escalated
    r_put = client.put(
        "/v1/alerts/alert_test_001/status",
        json={"status": "escalated", "resolution_notes": "Escalated to ML compliance"},
    )
    assert r_put.status_code == 200
    assert r_put.json()["status"] == "escalated"

    # 404 on nonexistent alert
    r_404 = client.patch(
        "/api/v1/alerts/ghost_alert/status",
        json={"status": "closed"},
    )
    assert r_404.status_code == 404

    # 422 on invalid status value
    r_invalid = client.patch(
        "/api/v1/alerts/alert_test_001/status",
        json={"status": "bogus_status"},
    )
    assert r_invalid.status_code == 422


def test_alert_triage_on_demand_evaluation(client: TestClient, populated_alert_service: AlertIntelligenceService) -> None:
    """Verify on-demand triage re-evaluation on existing alert."""
    r = client.post(
        "/api/v1/alerts/alert_test_001/triage",
        json={"transaction_amount": 15000.0, "country_code": "NG", "velocity": 8.0},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["alert_id"] == "alert_test_001"
    assert body["triage_priority"] in ("p1_critical", "p2_high")
    assert len(body["triage_reasons"]) > 0

    # /v1 parity
    r_v1 = client.post(
        "/v1/alerts/alert_test_001/triage",
        json={"transaction_amount": 50.0},
    )
    assert r_v1.status_code == 200

    # 404 for unknown alert
    r_404 = client.post("/api/v1/alerts/unknown_alert_999/triage", json={})
    assert r_404.status_code == 404


def test_standalone_triage_rule_evaluation(client: TestClient) -> None:
    """Verify standalone triage evaluation without alert persistence."""
    r = client.post(
        "/api/v1/alerts/triage/evaluate",
        json={
            "transaction_amount": 25000.0,
            "country_code": "RU",
            "velocity": 9.0,
            "risk_score": 920.0,
            "severity": "critical",
            "reason_codes": ["VEL-001", "GEO-RISK"],
            "dedup_count": 3,
            "entity_overlap_count": 2,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["triage_priority"] == "p1_critical"
    assert body["triage_action"] == "escalate_immediate"
    assert body["sla_minutes"] == 15
    assert len(body["triage_reasons"]) >= 3

    # /v1 prefix parity
    r_v1 = client.post(
        "/v1/alerts/triage/evaluate",
        json={"risk_score": 200.0, "severity": "low"},
    )
    assert r_v1.status_code == 200
    assert r_v1.json()["triage_priority"] == "p4_low"


def test_explainability_and_surrogate_routes(client: TestClient, populated_alert_service: AlertIntelligenceService) -> None:
    """Verify explainability, counterfactuals, decision replay, and surrogate endpoints."""
    # /alerts/{alert_id}/explain
    r_exp = client.get("/api/v1/alerts/alert_test_001/explain")
    assert r_exp.status_code == 200
    body_exp = r_exp.json()
    assert body_exp["alert_id"] == "alert_test_001"
    assert "risk_score_breakdown" in body_exp

    # /alerts/{alert_id}/counterfactuals
    r_cf = client.get("/api/v1/alerts/alert_test_001/counterfactuals")
    assert r_cf.status_code == 200
    assert "remediated_score" in r_cf.json()

    # /alerts/{alert_id}/decision-replay
    r_dr = client.get("/api/v1/alerts/alert_test_001/decision-replay")
    assert r_dr.status_code == 200
    assert "model_version" in r_dr.json()

    # /alerts/{alert_id}/gnn-explanation
    r_gnn = client.get("/api/v1/alerts/alert_test_001/gnn-explanation")
    assert r_gnn.status_code == 200
    assert "node_id" in r_gnn.json()

    # /alerts/{alert_id}/lime-explanation
    r_lime = client.get("/api/v1/alerts/alert_test_001/lime-explanation")
    assert r_lime.status_code == 200
    assert "feature_attributions" in r_lime.json()

    # 404 for unknown alert on explainability endpoints
    assert client.get("/api/v1/alerts/phantom_alert/explain").status_code == 404
    assert client.get("/api/v1/alerts/phantom_alert/counterfactuals").status_code == 404
    assert client.get("/api/v1/alerts/phantom_alert/decision-replay").status_code == 404


def test_shared_intelligence_endpoints(client: TestClient, populated_alert_service: AlertIntelligenceService) -> None:
    """Verify shared cross-institution intelligence feeds and aggregate stats."""
    r_intel = client.get("/api/v1/intelligence")
    assert r_intel.status_code == 200
    assert isinstance(r_intel.json(), list)

    r_intel_v1 = client.get("/v1/intelligence")
    assert r_intel_v1.status_code == 200
    assert isinstance(r_intel_v1.json(), list)

    r_stats = client.get("/api/v1/intelligence/stats")
    assert r_stats.status_code == 200
    assert "total_items" in r_stats.json()
    assert "avg_risk_indicator" in r_stats.json()


def test_tenant_isolation_enforcement(client: TestClient, populated_alert_service: AlertIntelligenceService) -> None:
    """Verify BOLA enforcement: caller bound to bank_b is forbidden from accessing bank_a alert."""
    app.dependency_overrides[resolve_tenant] = lambda: "bank_b"

    # Direct detail lookup of bank_a alert should return 403 Forbidden
    r = client.get("/api/v1/alerts/alert_test_001")
    assert r.status_code == 403

    # Status update attempt by foreign tenant returns 403 Forbidden
    r_patch = client.patch(
        "/api/v1/alerts/alert_test_001/status",
        json={"status": "confirmed_fraud"},
    )
    assert r_patch.status_code == 403

    app.dependency_overrides.clear()
