"""Integration tests for AML case lifecycle transitions and Four-Eyes dual control governance.

Covers:
- End-to-end API lifecycle progression (/api/v1/cases).
- Self-approval prevention with HTTP 403 Forbidden for assigned investigators.
- Dual distinct supervisor enforcement with HTTP 400 for duplicate signers.
- Rejection of illegal shortcut transitions via API.
- Terminal state immutability (replay attack resistance).
- Pre-validation endpoint (/validate-transition) contract and non-mutating behaviour.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.presentation.routers.cases import get_case_service


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def case_service():
    svc = get_case_service()
    svc._cases.clear()
    return svc


class TestCaseTransitionsIntegration:
    """Integration test suite for case status machine and Four-Eyes governance."""

    def test_e2e_valid_lifecycle_progression(self, client: TestClient, case_service):
        # 1. Create Case
        create_resp = client.post(
            "/api/v1/cases",
            json={
                "title": "Cross-Border Layering Scheme",
                "priority": "p1_critical",
                "assigned_to": "investigator_john",
            },
        )
        assert create_resp.status_code == 200
        case_data = create_resp.json()
        case_id = case_data["id"]
        assert case_data["status"] == "open"
        assert case_data["assigned_to"] == "investigator_john"

        # 2. Check Pre-validation for INVESTIGATING
        val_resp = client.post(
            f"/api/v1/cases/{case_id}/validate-transition",
            json={"target_state": "investigating", "actor_id": "investigator_john"},
        )
        assert val_resp.status_code == 200
        val_data = val_resp.json()
        assert val_data["allowed"] is True

        # 3. Transition to INVESTIGATING
        status_resp = client.patch(
            f"/api/v1/cases/{case_id}",
            json={"status": "investigating", "actor": "investigator_john"},
        )
        assert status_resp.status_code == 200
        assert status_resp.json()["status"] == "investigating"

        # 4. First Supervisor Signature
        sig1_resp = client.post(
            f"/api/v1/cases/{case_id}/sign",
            json={
                "supervisor_id": "supervisor:alice",
                "action": "APPROVE",
                "notes": "Evidence verified against SWIFT telemetry.",
            },
        )
        assert sig1_resp.status_code == 200
        assert "supervisor:supervisor:alice" in sig1_resp.json()["supervisor_signatures"] or "alice" in str(sig1_resp.json()["supervisor_signatures"])

        # 5. Second Distinct Supervisor Signature
        sig2_resp = client.post(
            f"/api/v1/cases/{case_id}/sign",
            json={
                "supervisor_id": "supervisor:bob",
                "action": "APPROVE",
                "notes": "Counterparty risk assessed, secondary approval granted.",
            },
        )
        assert sig2_resp.status_code == 200
        sigs = sig2_resp.json()["supervisor_signatures"]
        assert len(sigs) == 2

        # 6. Resolve Case to CLOSED_CONFIRMED with two distinct supervisors
        resolve_resp = client.post(
            f"/api/v1/cases/{case_id}/resolve",
            json={
                "resolution": "CONFIRMED_FRAUD",
                "primary_supervisor": "supervisor:carol",
                "secondary_supervisor": "supervisor:david",
                "actor": "analyst_bob",
            },
        )
        assert resolve_resp.status_code == 200
        resolved_data = resolve_resp.json()
        assert resolved_data["status"] == "closed_confirmed"
        assert resolved_data["closed_at"] is not None

    def test_prevent_assigned_investigator_self_approval_on_sign(self, client: TestClient, case_service):
        # Create case assigned to 'investigator_sam'
        create_resp = client.post(
            "/api/v1/cases",
            json={
                "title": "Self-approval test case",
                "assigned_to": "investigator_sam",
            },
        )
        case_id = create_resp.json()["id"]

        # Attempt to sign as assigned investigator -> 403 Forbidden
        sign_resp = client.post(
            f"/api/v1/cases/{case_id}/sign",
            json={
                "supervisor_id": "investigator_sam",
                "action": "APPROVE",
                "notes": "Attempting unauthorized self-signoff",
            },
        )
        assert sign_resp.status_code == 403
        assert "Self-approval prohibited" in sign_resp.json()["detail"]

    def test_prevent_assigned_investigator_self_approval_on_resolve(self, client: TestClient, case_service):
        create_resp = client.post(
            "/api/v1/cases",
            json={
                "title": "Self-resolve test case",
                "assigned_to": "investigator:diane",
            },
        )
        case_id = create_resp.json()["id"]

        # Advance to investigating
        client.patch(
            f"/api/v1/cases/{case_id}",
            json={"status": "investigating"},
        )

        # Attempt to resolve where primary supervisor is assigned investigator -> 403 Forbidden
        resolve_resp = client.post(
            f"/api/v1/cases/{case_id}/resolve",
            json={
                "resolution": "CONFIRMED_FRAUD",
                "primary_supervisor": "supervisor:diane",
                "secondary_supervisor": "supervisor:elena",
                "actor": "analyst_01",
            },
        )
        assert resolve_resp.status_code == 403
        assert "Self-approval prohibited" in resolve_resp.json()["detail"]

    def test_prevent_duplicate_supervisor_signatures(self, client: TestClient, case_service):
        create_resp = client.post(
            "/api/v1/cases",
            json={"title": "Duplicate supervisor test"},
        )
        case_id = create_resp.json()["id"]

        # First sign by supervisor:elena
        resp1 = client.post(
            f"/api/v1/cases/{case_id}/sign",
            json={"supervisor_id": "supervisor:elena", "action": "APPROVE"},
        )
        assert resp1.status_code == 200

        # Second sign attempt by same supervisor with different prefix -> 400 Bad Request
        resp2 = client.post(
            f"/api/v1/cases/{case_id}/sign",
            json={"supervisor_id": "SIG_SUPERVISOR_elena", "action": "APPROVE"},
        )
        assert resp2.status_code == 400
        assert "Supervisor has already signed" in resp2.json()["detail"] or "Duplicate" in resp2.json()["detail"]

    def test_reject_illegal_shortcut_transition(self, client: TestClient, case_service):
        create_resp = client.post(
            "/api/v1/cases",
            json={"title": "Shortcut test"},
        )
        case_id = create_resp.json()["id"]
        assert create_resp.json()["status"] == "open"

        # Attempt to jump directly from OPEN to CLOSED_CONFIRMED -> 400 Bad Request
        patch_resp = client.patch(
            f"/api/v1/cases/{case_id}",
            json={"status": "closed_confirmed", "supervisor_signature": "supervisor_bob"},
        )
        assert patch_resp.status_code == 400
        assert "Invalid transition" in patch_resp.json()["detail"] or "Illegal transition" in patch_resp.json()["detail"]

    def test_terminal_state_immutability(self, client: TestClient, case_service):
        create_resp = client.post(
            "/api/v1/cases",
            json={"title": "Terminal immutability test"},
        )
        case_id = create_resp.json()["id"]

        # Move OPEN -> INVESTIGATING -> CLOSED_CONFIRMED
        client.patch(
            f"/api/v1/cases/{case_id}",
            json={"status": "investigating"},
        )
        close_resp = client.patch(
            f"/api/v1/cases/{case_id}",
            json={"status": "closed_confirmed", "supervisor_signature": "supervisor_carol"},
        )
        assert close_resp.status_code == 200
        assert close_resp.json()["status"] == "closed_confirmed"

        # Attempt to re-open or transition finalized case -> 400 Bad Request
        reopen_resp = client.patch(
            f"/api/v1/cases/{case_id}",
            json={"status": "open"},
        )
        assert reopen_resp.status_code == 400
        assert "terminal state" in reopen_resp.json()["detail"]

        # Attempt to sign finalized case -> 400 Bad Request
        sign_resp = client.post(
            f"/api/v1/cases/{case_id}/sign",
            json={"supervisor_id": "supervisor_dave", "action": "APPROVE"},
        )
        assert sign_resp.status_code == 400
        assert "finalized in terminal state" in sign_resp.json()["detail"]

    def test_validate_transition_non_mutating(self, client: TestClient, case_service):
        create_resp = client.post(
            "/api/v1/cases",
            json={"title": "Validation endpoint test"},
        )
        case_id = create_resp.json()["id"]

        # Check valid transition
        resp_valid = client.post(
            f"/api/v1/cases/{case_id}/validate-transition",
            json={"target_state": "investigating", "actor_id": "analyst_1"},
        )
        assert resp_valid.status_code == 200
        assert resp_valid.json()["allowed"] is True

        # Check invalid transition
        resp_invalid = client.post(
            f"/api/v1/cases/{case_id}/validate-transition",
            json={"target_state": "sar_filed", "actor_id": "analyst_1"},
        )
        assert resp_invalid.status_code == 200
        assert resp_invalid.json()["allowed"] is False
        assert "Invalid transition" in resp_invalid.json()["reason"] or "Illegal transition" in resp_invalid.json()["reason"]

        # Verify state was not mutated
        get_resp = client.get(f"/api/v1/cases/{case_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "open"
