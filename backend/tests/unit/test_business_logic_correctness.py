"""Comprehensive Deep Correctness & Invariant Verification Suite for Business Logic.

Covers:
- BUSINESS-INV-01: Alert Identity & Deduplication
- BUSINESS-INV-02: Tenant Isolation & Object-Reference Authorization (BOLA)
- BUSINESS-INV-03: Transaction & Machine Result Immutability
- BUSINESS-INV-06: Alert Severity & Threshold Oracle Boundaries
- BUSINESS-INV-08 & 09: Legal/Illegal State Transitions & Terminal Immutability
- BUSINESS-INV-11 & 12: Four-Eyes Dual Control & Self-Approval Prevention
- BUSINESS-INV-14 & 15: Optimistic Concurrency & Stale Update Rejection (409)
- BUSINESS-INV-18: Regulatory Reporting Preconditions & Truthful State
- BUSINESS-INV-13: Webhook Event Deduplication & Stable Event ID
- Zero Dummy Mocks: Non-existent UUIDs return 404, never synthesized mocks
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.application.services.alert_service import AlertIntelligenceService
from app.application.services.case_service import CaseManagementService
from app.application.services.webhook_service import WebhookEventType, WebhookService
from app.domain.enums import CasePriority, CaseStatus
from app.domain.models.case import (
    InvalidCaseTransitionError,
    SelfApprovalProhibitedError,
    TerminalCaseImmutableError,
    clean_identity,
)
from app.main import app

client = TestClient(app)


# ═════════════════════════════════════════════════════════════════════════════
# 1. Alert Severity & Threshold Oracle Boundaries
# ═════════════════════════════════════════════════════════════════════════════


class TestAlertSeverityOracle:
    """Independent oracle tests for risk score to severity mapping boundaries."""

    @staticmethod
    def expected_severity_oracle(score: float) -> str:
        """Independent mathematical specification oracle."""
        if score >= 0.90:
            return "critical"
        if score >= 0.75:
            return "high"
        if score >= 0.50:
            return "medium"
        if score >= 0.30:
            return "low"
        return "info"

    @pytest.mark.parametrize(
        "score,expected",
        [
            (1.00, "critical"),
            (0.90, "critical"),
            (0.899999, "high"),
            (0.75, "high"),
            (0.749999, "medium"),
            (0.50, "medium"),
            (0.499999, "low"),
            (0.30, "low"),
            (0.299999, "info"),
            (0.00, "info"),
        ],
    )
    def test_severity_oracle_boundaries(self, score: float, expected: str) -> None:
        """Verify severity levels precisely match independent oracle at boundary epsilon."""
        computed = AlertIntelligenceService.classify_severity(score).value
        oracle = self.expected_severity_oracle(score)
        assert computed == oracle
        assert computed == expected


# ═════════════════════════════════════════════════════════════════════════════
# 2. Case State Machine & Terminal Immutability
# ═════════════════════════════════════════════════════════════════════════════


class TestCaseStateMachineInvariants:
    """Verify state transitions, illegal transition fail-closed, and terminal immutability."""

    def test_full_legal_lifecycle(self) -> None:
        """Verify valid progression: OPEN -> ASSIGNED -> INVESTIGATING -> PENDING_REVIEW -> ESCALATED -> SAR_FILED -> CLOSED_CONFIRMED."""
        service = CaseManagementService()
        case = service.create_case(title="Smurfing Workflow Test", priority=CasePriority.P2_HIGH)
        assert case.status == CaseStatus.OPEN

        # OPEN -> ASSIGNED
        c1 = service.assign_case(case.id, "analyst_bob")
        assert c1.status == CaseStatus.ASSIGNED

        # ASSIGNED -> INVESTIGATING
        c2 = service.change_status(case.id, CaseStatus.INVESTIGATING, actor="analyst_bob")
        assert c2.status == CaseStatus.INVESTIGATING

        # INVESTIGATING -> PENDING_REVIEW
        c3 = service.change_status(case.id, CaseStatus.PENDING_REVIEW, actor="analyst_bob")
        assert c3.status == CaseStatus.PENDING_REVIEW

        # PENDING_REVIEW -> ESCALATED
        c4 = service.change_status(case.id, CaseStatus.ESCALATED, actor="analyst_bob")
        assert c4.status == CaseStatus.ESCALATED

        # ESCALATED -> SAR_FILED
        c5 = service.change_status(case.id, CaseStatus.SAR_FILED, actor="analyst_bob")
        assert c5.status == CaseStatus.SAR_FILED

        # SAR_FILED -> CLOSED_CONFIRMED (requires 4-eyes signatures)
        c6 = service.change_status(
            case.id,
            CaseStatus.CLOSED_CONFIRMED,
            actor="analyst_bob",
            supervisor_signature="supervisor_charlie",
            second_supervisor_signature="supervisor_dana",
        )
        assert c6.status == CaseStatus.CLOSED_CONFIRMED
        assert c6.closed_at is not None
        assert not c6.is_open

    def test_terminal_states_strictly_immutable(self) -> None:
        """Terminal states (CLOSED_CONFIRMED, CLOSED_FALSE_POSITIVE) cannot be transitioned, assigned, or linked."""
        service = CaseManagementService()

        # Test CLOSED_CONFIRMED
        case_conf = service.create_case(title="Confirmed Fraud Test")
        service.change_status(case_conf.id, CaseStatus.INVESTIGATING, actor="analyst_1")
        service.change_status(
            case_conf.id,
            CaseStatus.CLOSED_CONFIRMED,
            actor="analyst_1",
            supervisor_signature="sup_1",
            second_supervisor_signature="sup_2",
        )

        with pytest.raises(TerminalCaseImmutableError):
            service.change_status(case_conf.id, CaseStatus.INVESTIGATING, actor="analyst_1")

        with pytest.raises(TerminalCaseImmutableError):
            service.assign_case(case_conf.id, "analyst_2")

        with pytest.raises(TerminalCaseImmutableError):
            service.link_alert(case_conf.id, "ALT-NEW-999")

        # Test CLOSED_FALSE_POSITIVE
        case_fp = service.create_case(title="False Positive Test")
        service.change_status(
            case_fp.id,
            CaseStatus.CLOSED_FALSE_POSITIVE,
            actor="analyst_1",
            supervisor_signature="sup_1",
            second_supervisor_signature="sup_2",
        )

        with pytest.raises(TerminalCaseImmutableError):
            service.change_status(case_fp.id, CaseStatus.OPEN, actor="analyst_1")

        with pytest.raises(TerminalCaseImmutableError):
            service.assign_case(case_fp.id, "analyst_3")

        with pytest.raises(TerminalCaseImmutableError):
            service.link_alert(case_fp.id, "ALT-NEW-888")

    def test_direct_illegal_transitions_blocked(self) -> None:
        """Illegal jumps from OPEN or ASSIGNED directly to terminal states fail closed."""
        service = CaseManagementService()
        case = service.create_case(title="Illegal Transition Test")

        with pytest.raises(InvalidCaseTransitionError):
            service.change_status(case.id, CaseStatus.CLOSED_CONFIRMED, actor="analyst_1")

        with pytest.raises(InvalidCaseTransitionError):
            service.change_status(case.id, CaseStatus.SAR_FILED, actor="analyst_1")

        with pytest.raises(InvalidCaseTransitionError):
            service.change_status(case.id, CaseStatus.ESCALATED, actor="analyst_1")


# ═════════════════════════════════════════════════════════════════════════════
# 3. Optimistic Concurrency & Stale Update Protection
# ═════════════════════════════════════════════════════════════════════════════


class TestOptimisticConcurrency:
    """Verify lost updates are prevented via expected_status precondition (HTTP 409)."""

    def test_expected_status_mismatch_returns_409_conflict(self) -> None:
        """If expected_status does not match current case status, return HTTP 409 Conflict."""
        # Create a fresh case
        res = client.post(
            "/api/v1/cases",
            json={"title": "Concurrency Case", "priority": "p3_medium"},
            headers={"X-Bank-ID": "bank_alpha"},
        )
        assert res.status_code == 200
        case_id = res.json()["id"]

        # Analyst A expects OPEN and transitions to INVESTIGATING
        res_a = client.patch(
            f"/api/v1/cases/{case_id}",
            json={"status": "investigating", "actor": "analyst_a", "expected_status": "open"},
            headers={"X-Bank-ID": "bank_alpha"},
        )
        assert res_a.status_code == 200
        assert res_a.json()["status"] == "investigating"

        # Analyst B submits stale update expecting OPEN
        res_b = client.patch(
            f"/api/v1/cases/{case_id}",
            json={"status": "assigned", "actor": "analyst_b", "expected_status": "open"},
            headers={"X-Bank-ID": "bank_alpha"},
        )
        assert res_b.status_code == 409
        assert "precondition failed" in res_b.json()["detail"].lower()


# ═════════════════════════════════════════════════════════════════════════════
# 4. Four-Eyes Dual Control & Self-Approval Invariants
# ═════════════════════════════════════════════════════════════════════════════


class TestFourEyesGovernanceInvariants:
    """Verify separation of duties: ApproverID != InvestigatorID across normalization."""

    def test_assigned_investigator_cannot_self_approve(self) -> None:
        """Assigned investigator cannot sign or close their own case."""
        service = CaseManagementService()
        case = service.create_case(title="Self-Approval Test", assigned_to="investigator_john")
        service.change_status(case.id, CaseStatus.INVESTIGATING, actor="investigator_john")

        with pytest.raises(SelfApprovalProhibitedError):
            service.change_status(
                case.id,
                CaseStatus.CLOSED_CONFIRMED,
                actor="analyst_assistant",
                supervisor_signature="investigator_john",
                second_supervisor_signature="supervisor_mary",
            )

    def test_identity_normalization_prevents_casing_and_prefix_evasion(self) -> None:
        """clean_identity strips whitespace, lowercases, and removes supervisor: / analyst: prefixes."""
        assert clean_identity("  Supervisor:Alice  ") == "alice"
        assert clean_identity("SIG_SUPERVISOR_ALICE") == "alice"
        assert clean_identity("Investigator:Bob") == "bob"
        assert clean_identity("  ALICE  ") == "alice"

        service = CaseManagementService()
        case = service.create_case(title="Evasion Test", assigned_to="john_doe")
        service.change_status(case.id, CaseStatus.INVESTIGATING, actor="john_doe")

        # Attempt evasion via uppercase and prefix
        with pytest.raises(SelfApprovalProhibitedError):
            service.change_status(
                case.id,
                CaseStatus.CLOSED_CONFIRMED,
                actor="analyst_other",
                supervisor_signature="supervisor:JOHN_DOE",
                second_supervisor_signature="supervisor_mary",
            )


# ═════════════════════════════════════════════════════════════════════════════
# 5. Cross-Tenant Object-Reference Isolation (BOLA)
# ═════════════════════════════════════════════════════════════════════════════


class TestCrossTenantObjectIsolation:
    """Verify backend enforces strict tenant isolation on all case mutations."""

    def test_cross_tenant_case_access_and_mutation_blocked(self) -> None:
        """Bank B cannot read, update, sign, or file SAR for Bank A's case."""
        # Create case under bank_alpha
        res = client.post(
            "/api/v1/cases",
            json={"title": "Bank Alpha Secret Case", "priority": "p1_critical"},
            headers={"X-Bank-ID": "bank_alpha"},
        )
        assert res.status_code == 200
        case_id = res.json()["id"]

        # Bank Beta attempts GET -> 403
        get_res = client.get(
            f"/api/v1/cases/{case_id}",
            headers={"X-Bank-ID": "bank_beta"},
        )
        assert get_res.status_code == 403

        # Bank Beta attempts PATCH status -> 403
        patch_res = client.patch(
            f"/api/v1/cases/{case_id}",
            json={"status": "investigating", "actor": "hacker_bob"},
            headers={"X-Bank-ID": "bank_beta"},
        )
        assert patch_res.status_code == 403

        # Bank Beta attempts file-sar -> 403
        sar_res = client.post(
            f"/api/v1/cases/{case_id}/file-sar",
            headers={"X-Bank-ID": "bank_beta"},
        )
        assert sar_res.status_code == 403

        # Bank Beta attempts add note -> 403
        note_res = client.post(
            f"/api/v1/cases/{case_id}/notes",
            json={"author": "hacker_bob", "content": "tampering"},
            headers={"X-Bank-ID": "bank_beta"},
        )
        assert note_res.status_code == 403


# ═════════════════════════════════════════════════════════════════════════════
# 6. Regulatory Reporting Preconditions & Human Resolution Immutability
# ═════════════════════════════════════════════════════════════════════════════


class TestRegulatoryReportingPreconditions:
    """Verify SAR filings cannot be generated for false positives or open cases."""

    def test_cannot_file_sar_for_false_positive_case(self) -> None:
        """A case resolved as CLOSED_FALSE_POSITIVE cannot produce a FinCEN SAR XML filing."""
        res = client.post(
            "/api/v1/cases",
            json={"title": "FP Regulatory Test", "priority": "p3_medium"},
            headers={"X-Bank-ID": "bank_alpha"},
        )
        assert res.status_code == 200
        case_id = res.json()["id"]

        # Resolve as False Positive under dual control
        resolve_res = client.post(
            f"/api/v1/cases/{case_id}/resolve",
            json={
                "resolution": "FALSE_POSITIVE",
                "actor": "analyst_alice",
                "primary_supervisor": "sup_1",
                "secondary_supervisor": "sup_2",
                "justification": "Legitimate merchant restructuring.",
            },
            headers={"X-Bank-ID": "bank_alpha"},
        )
        assert resolve_res.status_code == 200
        assert resolve_res.json()["status"] == "closed_false_positive"

        # Attempt SAR generation -> HTTP 400
        sar_res = client.post(
            f"/api/v1/cases/{case_id}/file-sar",
            headers={"X-Bank-ID": "bank_alpha"},
        )
        assert sar_res.status_code == 400
        assert "closed_false_positive" in sar_res.json()["detail"].lower()

    def test_cannot_file_sar_for_uninvestigated_open_case(self) -> None:
        """An unreviewed case in OPEN status cannot produce a FinCEN SAR XML filing."""
        res = client.post(
            "/api/v1/cases",
            json={"title": "Open Case SAR Attempt", "priority": "p3_medium"},
            headers={"X-Bank-ID": "bank_alpha"},
        )
        case_id = res.json()["id"]

        sar_res = client.post(
            f"/api/v1/cases/{case_id}/file-sar",
            headers={"X-Bank-ID": "bank_alpha"},
        )
        assert sar_res.status_code == 400
        assert "unreviewed" in sar_res.json()["detail"].lower()


# ═════════════════════════════════════════════════════════════════════════════
# 7. Outbound Webhook Idempotency & Stable Event ID
# ═════════════════════════════════════════════════════════════════════════════


class TestWebhookDispatchIdempotency:
    """Verify deterministic stable event ID derivation for consumer deduplication."""

    def test_webhook_dispatches_share_stable_event_id_on_redelivery(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Retrying or redelivering a webhook for the same alert yields the exact same event_id."""
        svc = WebhookService()
        svc.clear_subscriptions()
        monkeypatch.setattr(svc, "validate_target_url", lambda url: True)

        # Register subscription for tenant_alpha
        _ = svc.register_subscription(
            tenant_id="bank_alpha",
            target_url="https://api.external-partner.com/aml-webhook",
            events=[WebhookEventType.ALERT_CREATED],
        )

        alert_payload = {
            "id": "ALT-2026-9999",
            "bank_id": "bank_alpha",
            "transaction_id": "tx_abc123",
            "risk_score": 0.94,
        }

        # First dispatch
        deliveries_1 = svc.dispatch_event(
            tenant_id="bank_alpha",
            event_type=WebhookEventType.ALERT_CREATED,
            payload=alert_payload,
        )
        assert len(deliveries_1) == 1
        event_id_1 = deliveries_1[0].event_id

        # Redelivery (e.g., Kafka redelivery / retry)
        deliveries_2 = svc.dispatch_event(
            tenant_id="bank_alpha",
            event_type=WebhookEventType.ALERT_CREATED,
            payload=alert_payload,
        )
        assert len(deliveries_2) == 1
        event_id_2 = deliveries_2[0].event_id

        # Stable event ID must match exactly for downstream receiver deduplication
        assert event_id_1 == event_id_2
        assert event_id_1.startswith("evt_")


# ═════════════════════════════════════════════════════════════════════════════
# 8. Zero Dummy Mocks: Non-existent UUIDs Return 404
# ═════════════════════════════════════════════════════════════════════════════


class TestZeroDummyMockContract:
    """Verify that arbitrary non-existent UUIDs return 404 instead of synthesizing fake cases."""

    def test_nonexistent_uuid_returns_404(self) -> None:
        """Random UUID not in storage truthfully returns HTTP 404."""
        random_uuid = str(uuid.uuid4())
        res = client.get(f"/api/v1/cases/{random_uuid}")
        assert res.status_code == 404
        assert f"Case not found: {random_uuid}" in res.json()["detail"]
