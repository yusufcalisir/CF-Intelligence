# ruff: noqa: E402
"""Automated Unit Test Suite for Alert Lifecycle & Investigator Case Workbench."""

from __future__ import annotations

import pytest

from app.application.services.case_workbench import InvestigatorCaseWorkbenchService
from app.domain.case_management import (
    InvalidCaseTransitionError,
    InvestigatorCaseStatus,
)


def test_investigator_case_lifecycle_and_assignment() -> None:
    """Test 6-stage case lifecycle progression and analyst assignment."""
    service = InvestigatorCaseWorkbenchService()

    # 1. Create case from alerts
    record = service.create_case(
        title="Suspicious Structuring Batch",
        alert_ids=["alt_101", "alt_102"],
    )
    assert record.status == InvestigatorCaseStatus.NEW
    assert record.assigned_to is None

    # 2. Assign investigator -> ASSIGNED
    service.assign_investigator(record.case_id, "analyst_john")
    assert record.status == InvestigatorCaseStatus.ASSIGNED
    assert record.assigned_to == "analyst_john"

    # 3. Transition to active investigation -> UNDER_INVESTIGATION
    service.transition_to_investigation(record.case_id, "analyst_john")
    assert record.status == InvestigatorCaseStatus.UNDER_INVESTIGATION

    # 4. Escalate case -> ESCALATED
    service.escalate_case(
        record.case_id,
        reason="Cross-border wire exceeding threshold",
        actor_id="analyst_john",
    )
    assert record.status == InvestigatorCaseStatus.ESCALATED


def test_case_resolution_requires_four_eyes_supervisor_signature() -> None:
    """Test blocking case resolution when Four-Eyes supervisor signature is missing or single."""
    service = InvestigatorCaseWorkbenchService()
    record = service.create_case(
        title="Card Testing Ring",
        alert_ids=["alt_501"],
    )
    service.assign_investigator(record.case_id, "analyst_sarah")
    service.transition_to_investigation(record.case_id, "analyst_sarah")

    # 1. Attempt resolution with invalid format -> Fails
    with pytest.raises(InvalidCaseTransitionError) as exc_info:
        service.resolve_case(
            case_id=record.case_id,
            determination=InvestigatorCaseStatus.RESOLVED_CONFIRMED_FRAUD,
            supervisor_signature="INVALID_SIG",
            actor_id="analyst_sarah",
        )
    assert "Four-Eyes supervisor dual-authorization signature required" in str(exc_info.value)

    # 2. Attempt resolution with only ONE supervisor signature -> Fails (Strict Dual-Control)
    with pytest.raises(InvalidCaseTransitionError) as exc_info_single:
        service.resolve_case(
            case_id=record.case_id,
            determination=InvestigatorCaseStatus.RESOLVED_CONFIRMED_FRAUD,
            supervisor_signature="SIG_SUPERVISOR_99001",
            actor_id="supervisor_mike",
        )
    assert "requires 2 distinct supervisor signatures (got 1" in str(exc_info_single.value)

    # 3. Attempt resolution with the SAME supervisor signing twice -> Fails (Duplicate Signer)
    with pytest.raises(InvalidCaseTransitionError) as exc_info_dup:
        service.resolve_case(
            case_id=record.case_id,
            determination=InvestigatorCaseStatus.RESOLVED_CONFIRMED_FRAUD,
            supervisor_signature="SIG_SUPERVISOR_99001",
            second_supervisor_signature="SIG_SUPERVISOR_99001",
            actor_id="supervisor_mike",
        )
    assert "duplicate signer identity '99001' rejected" in str(exc_info_dup.value)

    # 4. Resolution with TWO DISTINCT supervisor signatures -> Succeeds
    resolved = service.resolve_case(
        case_id=record.case_id,
        determination=InvestigatorCaseStatus.RESOLVED_CONFIRMED_FRAUD,
        supervisor_signature="SIG_SUPERVISOR_99001",
        second_supervisor_signature="SIG_SUPERVISOR_88002",
        actor_id="supervisor_dan",
    )
    assert resolved.status == InvestigatorCaseStatus.RESOLVED_CONFIRMED_FRAUD
    assert "SIG_SUPERVISOR_99001" in resolved.supervisor_signatures
    assert "SIG_SUPERVISOR_88002" in resolved.supervisor_signatures
    assert len(resolved.supervisor_signatures) == 2


def test_case_dual_control_stepwise_workflow() -> None:
    """Test asynchronous dual-control signing with intermediate PENDING_SECOND_SIGNATURE state."""
    service = InvestigatorCaseWorkbenchService()
    record = service.create_case(
        title="Layering Wire Scheme",
        alert_ids=["alt_601", "alt_602"],
    )
    service.assign_investigator(record.case_id, "analyst_bob")
    service.transition_to_investigation(record.case_id, "analyst_bob")

    # Step 1: Supervisor Alice applies first signature -> Transitions to PENDING_SECOND_SIGNATURE
    step1 = service.add_supervisor_signature(
        case_id=record.case_id,
        supervisor_signature="SIG_SUPERVISOR_ALICE",
        actor_id="supervisor_alice",
    )
    assert step1.status == InvestigatorCaseStatus.PENDING_SECOND_SIGNATURE
    assert step1.supervisor_signatures == ["SIG_SUPERVISOR_ALICE"]

    # Step 2: Supervisor Bob applies second signature and resolves
    resolved = service.resolve_case(
        case_id=record.case_id,
        determination=InvestigatorCaseStatus.RESOLVED_CONFIRMED_FRAUD,
        second_supervisor_signature="SIG_SUPERVISOR_BOB",
        actor_id="supervisor_bob",
    )
    assert resolved.status == InvestigatorCaseStatus.RESOLVED_CONFIRMED_FRAUD
    assert resolved.supervisor_signatures == ["SIG_SUPERVISOR_ALICE", "SIG_SUPERVISOR_BOB"]


def test_case_blocks_illegal_stage_jumps() -> None:
    """Test blocking illegal direct status jumps (e.g. NEW directly to RESOLVED)."""
    service = InvestigatorCaseWorkbenchService()
    record = service.create_case(
        title="Direct Jump Test",
        alert_ids=["alt_999"],
    )

    with pytest.raises(InvalidCaseTransitionError) as exc_info:
        service.resolve_case(
            case_id=record.case_id,
            determination=InvestigatorCaseStatus.RESOLVED_FALSE_POSITIVE,
            supervisor_signature="SIG_SUPERVISOR_123",
            actor_id="analyst_bob",
        )
    assert "Illegal transition for case" in str(exc_info.value)
