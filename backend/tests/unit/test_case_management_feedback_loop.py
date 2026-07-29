"""Unit tests for End-to-End Case Management & Human-in-the-Loop Feedback Loop (Section 5.2)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.application.services.case_service import CaseManagementService
from app.domain.enums import CasePriority, CaseStatus
from app.main import app

client = TestClient(app)
case_service = CaseManagementService()


def test_case_escalation_and_assignment() -> None:
    """Verifies escalation of alerts into an investigation case and investigator assignment."""
    case = case_service.create_case(
        title="Suspicious Structuring Pattern",
        priority=CasePriority.P2_HIGH,
        alert_ids=["alt_1001", "alt_1002"],
    )
    assert case.id is not None
    assert case.status.value == "open"

    # Assign case to compliance investigator
    assigned_case = case_service.assign_case(case.id, investigator="investigator_bob")
    assert assigned_case.assigned_to == "investigator_bob"
    assert assigned_case.status.value == "assigned"


def test_analyst_determination_closed_confirmed_feedback_loop() -> None:
    """Verifies closed_confirmed verdict records label 1 retraining feedback and generates SAR XML."""
    case = case_service.create_case(
        title="Confirmed Cross-Bank Fraud Ring",
        priority=CasePriority.P1_CRITICAL,
        alert_ids=["alt_2001"],
    )
    case_service.assign_case(case.id, investigator="analyst_alice")
    case_service.change_status(case.id, new_status=CaseStatus.INVESTIGATING, actor="analyst_alice")

    # Transition to closed_confirmed with Four-Eyes supervisor signature
    updated_case = case_service.change_status(
        case.id,
        new_status=CaseStatus.CLOSED_CONFIRMED,
        actor="analyst_alice",
        supervisor_signature="supervisor_carol",
    )
    assert updated_case.status.value == "closed_confirmed"

    # Verify timeline event recorded retraining feedback
    timeline_events = case_service.get_timeline(case.id)
    status_events = [e for e in timeline_events if e.event_type == "status_changed"]
    assert len(status_events) > 0
    last_event = status_events[-1]
    assert last_event.metadata.get("retraining_feedback_label") == 1
    assert last_event.metadata.get("retraining_feedback_recorded") is True


def test_analyst_determination_closed_false_positive_feedback_loop() -> None:
    """Verifies closed_false_positive verdict records label 0 retraining feedback."""
    case = case_service.create_case(
        title="Legitimate High-Volume Holiday Purchase",
        priority=CasePriority.P3_MEDIUM,
        alert_ids=["alt_3001"],
    )
    case_service.assign_case(case.id, investigator="analyst_alice")
    case_service.change_status(case.id, new_status=CaseStatus.INVESTIGATING, actor="analyst_alice")

    updated_case = case_service.change_status(
        case.id,
        new_status=CaseStatus.CLOSED_FALSE_POSITIVE,
        actor="analyst_alice",
        supervisor_signature="supervisor_carol",
    )
    assert updated_case.status.value == "closed_false_positive"

    timeline_events = case_service.get_timeline(case.id)
    status_events = [e for e in timeline_events if e.event_type == "status_changed"]
    assert len(status_events) > 0
    last_event = status_events[-1]
    assert last_event.metadata.get("retraining_feedback_label") == 0


def test_fincen_sar_report_generation_and_download() -> None:
    """Verifies SAR report endpoint returns valid FinCEN XML payload."""
    case = case_service.create_case(
        title="FinCEN Filing Verification Case",
        priority=CasePriority.P2_HIGH,
        alert_ids=["alt_4001"],
    )
    case_service.assign_case(case.id, investigator="analyst_alice")
    case_service.change_status(case.id, new_status=CaseStatus.INVESTIGATING, actor="analyst_alice")
    case_service.change_status(
        case.id,
        new_status=CaseStatus.CLOSED_CONFIRMED,
        actor="analyst_alice",
        supervisor_signature="supervisor_carol",
    )

    response = client.get(f"/api/v1/cases/{case.id}/sar-report")
    assert response.status_code == 200
    assert "application/xml" in response.headers["content-type"]
    assert "EFilingSubmission" in response.text
    assert "FinCEN" in response.text or "bsa" in response.text
