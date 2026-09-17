"""Unit and contract tests for Case Management, Evidence, and Four-Eyes Disposition API routes.

Validates dual routing (/api/v1/cases and /v1/cases), idempotent creation,
Four-Eyes dual control supervisor signoff, tenant isolation, and RFC 7807 status codes.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain.entities_phase2 import Alert
from app.domain.enums import AlertSeverity, AlertStatus, CasePriority, CaseStatus
from app.main import app
from app.presentation.routers.alerts import get_alert_service
from app.presentation.routers.cases import get_case_service


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def clean_case_service():
    svc = get_case_service()
    svc._cases.clear()
    return svc


class TestCasesRoutes:
    """Test suite for investigation case management endpoints."""

    def test_list_cases_dual_route_and_filters(self, client: TestClient, clean_case_service):
        # Seed test cases
        c1 = clean_case_service.create_case(
            title="Structuring ring in North Region",
            priority=CasePriority.P1_CRITICAL,
        )
        c2 = clean_case_service.create_case(
            title="Card testing fraud anomaly",
            priority=CasePriority.P3_MEDIUM,
        )

        for prefix in ("/api/v1/cases", "/v1/cases"):
            resp = client.get(prefix)
            assert resp.status_code == 200, f"Failed on prefix {prefix}: {resp.text}"
            data = resp.json()
            assert isinstance(data, list)
            assert len(data) >= 2
            ids = [item["id"] for item in data]
            assert c1.id in ids
            assert c2.id in ids

        # Filter by priority
        resp_p1 = client.get("/api/v1/cases", params={"priority": "p1_critical"})
        assert resp_p1.status_code == 200
        data_p1 = resp_p1.json()
        assert all(item["priority"] == "p1_critical" for item in data_p1)

        # Invalid priority value returns 422
        resp_invalid = client.get("/api/v1/cases", params={"priority": "invalid_pri"})
        assert resp_invalid.status_code == 422

        # Invalid status value returns 422
        resp_invalid_st = client.get("/api/v1/cases", params={"status": "invalid_status"})
        assert resp_invalid_st.status_code == 422

    def test_create_case_and_idempotency(self, client: TestClient, clean_case_service):
        payload = {
            "title": "Account takeover investigation across multi-bank cluster",
            "priority": "p2_high",
            "alert_ids": ["ALT-001", "ALT-002"],
        }
        headers = {"Idempotency-Key": "case-test-key-9999"}

        # First request: creates case
        resp1 = client.post("/api/v1/cases", json=payload, headers=headers)
        assert resp1.status_code == 200
        case_data1 = resp1.json()
        assert case_data1["title"] == payload["title"]
        assert case_data1["priority"] == "p2_high"
        assert len(case_data1["alert_ids"]) == 2

        # Second request with same idempotency key: replayed response
        resp2 = client.post("/api/v1/cases", json=payload, headers=headers)
        assert resp2.status_code == 200
        assert resp2.headers.get("Idempotency-Replayed") == "true"
        case_data2 = resp2.json()
        assert case_data2["id"] == case_data1["id"]

    def test_get_case_detail_and_not_found(self, client: TestClient, clean_case_service):
        case = clean_case_service.create_case(title="Mule network detection")

        for prefix in (f"/api/v1/cases/{case.id}", f"/v1/cases/{case.id}"):
            resp = client.get(prefix)
            assert resp.status_code == 200
            data = resp.json()
            assert data["id"] == case.id
            assert data["title"] == "Mule network detection"
            assert data["is_open"] is True

        # Nonexistent case returns 404
        resp_404 = client.get("/api/v1/cases/nonexistent-case-id-12345")
        assert resp_404.status_code == 404

    def test_tenant_isolation_on_case_access(self, client: TestClient, clean_case_service):
        alert_svc = get_alert_service()
        from app.application.services.alert_service import _alert_to_dict

        alert = Alert(
            id="ALT-ISOLATION-01",
            bank_id="bank_alpha",
            severity=AlertSeverity.HIGH,
            status=AlertStatus.NEW,
        )
        alert_svc._alert_store.set(alert.id, _alert_to_dict(alert))

        case = clean_case_service.create_case(
            title="Alpha Bank Isolated Case",
            alert_ids=[alert.id],
        )

        # Cross-tenant access from bank_beta must be forbidden (403)
        resp_denied = client.get(
            f"/api/v1/cases/{case.id}",
            headers={"X-Tenant-ID": "bank_beta", "X-Bank-ID": "bank_beta"},
        )
        assert resp_denied.status_code == 403

    def test_update_case_status_valid_and_invalid(self, client: TestClient, clean_case_service):
        case = clean_case_service.create_case(title="Status transition test case")

        # Valid transition: open -> investigating
        resp = client.patch(
            f"/api/v1/cases/{case.id}",
            json={"status": "investigating", "actor": "analyst_01"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "investigating"

        # PUT alias: /v1/cases/{id}/status
        resp_put = client.put(
            f"/v1/cases/{case.id}/status",
            json={"status": "pending_review", "actor": "analyst_01"},
        )
        assert resp_put.status_code == 200
        assert resp_put.json()["status"] == "pending_review"

        # Invalid transition directly to closed_confirmed without supervisor signoff -> 400
        resp_bad = client.patch(
            f"/api/v1/cases/{case.id}",
            json={"status": "closed_confirmed", "actor": "analyst_01"},
        )
        assert resp_bad.status_code == 400

        # Nonexistent case returns 404
        resp_404 = client.patch(
            "/api/v1/cases/missing-case-id-8888",
            json={"status": "investigating", "actor": "analyst_01"},
        )
        assert resp_404.status_code == 404

    def test_escalate_and_sign_case(self, client: TestClient, clean_case_service):
        case = clean_case_service.create_case(title="Escalation test case")
        clean_case_service.change_status(case.id, CaseStatus.INVESTIGATING)

        # Escalate case
        resp_esc = client.post(
            f"/api/v1/cases/{case.id}/escalate",
            json={"reason": "Suspicious high-frequency mule velocity confirmed", "actor": "analyst_01"},
        )
        assert resp_esc.status_code == 200
        assert resp_esc.json()["status"] == "pending_review"

        # Supervisor signs approval
        resp_sign1 = client.post(
            f"/api/v1/cases/{case.id}/sign",
            json={"supervisor_id": "supervisor_alice", "action": "APPROVE", "notes": "Approved for closure review"},
        )
        assert resp_sign1.status_code == 200
        data1 = resp_sign1.json()
        assert "supervisor:supervisor_alice" in data1["supervisor_signatures"]

        # Duplicate signature by same supervisor is rejected (400)
        resp_dup = client.post(
            f"/api/v1/cases/{case.id}/sign",
            json={"supervisor_id": "supervisor_alice", "action": "APPROVE"},
        )
        assert resp_dup.status_code == 400

        # Second supervisor signs
        resp_sign2 = client.post(
            f"/v1/cases/{case.id}/sign",
            json={"supervisor_id": "supervisor_bob", "action": "APPROVE"},
        )
        assert resp_sign2.status_code == 200
        data2 = resp_sign2.json()
        assert len(data2["supervisor_signatures"]) == 2

    def test_four_eyes_resolution_invariants(self, client: TestClient, clean_case_service):
        case = clean_case_service.create_case(title="Four-Eyes Resolution Test")
        clean_case_service.change_status(case.id, CaseStatus.INVESTIGATING)
        clean_case_service.change_status(case.id, CaseStatus.PENDING_REVIEW)

        # Rejection when primary and secondary supervisors are identical (Four-Eyes dual control violation)
        resp_same_sup = client.post(
            f"/api/v1/cases/{case.id}/resolve",
            json={
                "resolution": "CONFIRMED_FRAUD",
                "primary_supervisor": "supervisor_1",
                "secondary_supervisor": "supervisor_1",
                "actor": "analyst_99",
            },
        )
        assert resp_same_sup.status_code == 400
        assert "distinct" in resp_same_sup.json()["detail"].lower()

        # Rejection when supervisor is the same as the analyst actor (Self-approval violation)
        resp_self_approval = client.post(
            f"/api/v1/cases/{case.id}/resolve",
            json={
                "resolution": "CONFIRMED_FRAUD",
                "primary_supervisor": "analyst_99",
                "secondary_supervisor": "supervisor_2",
                "actor": "analyst_99",
            },
        )
        assert resp_self_approval.status_code == 400
        assert "different from the analyst actor" in resp_self_approval.json()["detail"].lower()

        # Success with distinct supervisors and distinct actor
        resp_success = client.post(
            f"/api/v1/cases/{case.id}/resolve",
            json={
                "resolution": "CONFIRMED_FRAUD",
                "primary_supervisor": "supervisor_alpha",
                "secondary_supervisor": "supervisor_beta",
                "actor": "analyst_99",
            },
        )
        assert resp_success.status_code == 200
        res_data = resp_success.json()
        assert res_data["status"] == "closed_confirmed"
        assert res_data["is_open"] is False

    def test_notes_alerts_and_timeline_hash_verification(self, client: TestClient, clean_case_service):
        case = clean_case_service.create_case(title="Evidence Chaining Case")

        # Add note
        resp_note = client.post(
            f"/api/v1/cases/{case.id}/notes",
            json={"author": "analyst_lead", "content": "Initial scoping completed; mule syndicate identified."},
        )
        assert resp_note.status_code == 200
        assert resp_note.json()["content"].startswith("Initial scoping")

        # Link alert
        resp_link = client.post(
            f"/api/v1/cases/{case.id}/alerts",
            json={"alert_id": "ALT-HASH-CHAIN-101"},
        )
        assert resp_link.status_code == 200
        assert "ALT-HASH-CHAIN-101" in resp_link.json()["alert_ids"]

        # Get timeline
        resp_time = client.get(f"/v1/cases/{case.id}/timeline")
        assert resp_time.status_code == 200
        events = resp_time.json()
        assert len(events) >= 3

        # Verify cryptographic SHA-256 parent hash chain integrity
        resp_verify = client.get(f"/api/v1/cases/{case.id}/timeline/verify")
        assert resp_verify.status_code == 200
        verify_data = resp_verify.json()
        assert verify_data["is_valid"] is True
        assert verify_data["corrupted_index"] is None
        assert len(verify_data["chain_hashes"]) >= 3

    def test_evidence_registration_and_retrieval(self, client: TestClient, clean_case_service):
        case = clean_case_service.create_case(title="Forensics Evidence Registry Case")

        # Register evidence with SHA-256 hash
        ev_payload = {
            "evidence_type": "document",
            "title": "Chainalysis Mule Graph Report",
            "file_path": "evidence/mule_report_001.pdf",
            "content": "Cryptographic clustering proof showing rapid hops into privacy mixer.",
            "uploaded_by": "forensics_analyst",
        }
        resp_reg = client.post(f"/api/v1/cases/{case.id}/evidence", json=ev_payload)
        assert resp_reg.status_code == 200
        ev_data = resp_reg.json()
        assert ev_data["title"] == ev_payload["title"]
        assert len(ev_data["content_hash"]) == 64

        # Retrieve evidence listing
        resp_list = client.get(f"/v1/cases/{case.id}/evidence")
        assert resp_list.status_code == 200
        items = resp_list.json()
        assert len(items) >= 1
        assert items[0]["id"] == ev_data["id"]

    def test_fincen_sar_export_and_audit_logs(self, client: TestClient, clean_case_service):
        case = clean_case_service.create_case(title="SAR XML Filing Case")
        clean_case_service.change_status(case.id, CaseStatus.INVESTIGATING)
        clean_case_service.change_status(case.id, CaseStatus.PENDING_REVIEW)
        clean_case_service.change_status(
            case.id,
            CaseStatus.CLOSED_CONFIRMED,
            actor="analyst_lead",
            supervisor_signature="supervisor:lead_1",
            second_supervisor_signature="supervisor:lead_2",
        )

        # FinCEN SAR XML Export
        fincen_payload = {
            "case_id": case.id,
            "institution_name": "Consortium Alpha Bank AML Unit",
            "narrative_override": "Confirmed cross-border structuring activity exceeding FinCEN BSA thresholds.",
        }
        resp_fincen = client.post("/api/v1/cases/export/fincen-xml", json=fincen_payload)
        assert resp_fincen.status_code == 200
        sar_res = resp_fincen.json()
        assert sar_res["status"] == "FILED"
        assert len(sar_res["sha256_hash"]) == 64

        # Session duration logging
        resp_session = client.post(
            "/api/v1/cases/audit/session",
            json={"investigator": "analyst_lead", "duration_seconds": 1250.0},
        )
        assert resp_session.status_code == 200
        assert resp_session.json()["status"] == "success"

        # Retrieve investigator audit logs
        resp_logs = client.get("/v1/cases/audit/logs")
        assert resp_logs.status_code == 200
        logs = resp_logs.json()
        assert len(logs) >= 1
