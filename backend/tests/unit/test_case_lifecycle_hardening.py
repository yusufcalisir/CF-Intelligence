# ruff: noqa: E402
"""Automated Hardening Unit Tests for Case Lifecycle, Dual-Control Signoff & Timeline Integrity."""

from __future__ import annotations

import concurrent.futures
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.application.services.case_service import CaseManagementService
from app.application.services.case_workbench import InvestigatorCaseWorkbenchService
from app.domain.case_management import (
    CaseLifecycleStateMachine,
    FraudCaseRecord,
    InvalidCaseTransitionError,
    InvestigatorCaseStatus,
    validate_distinct_supervisors,
)
from app.domain.enums import CasePriority, CaseStatus
from app.infrastructure.models import CaseModel
from app.infrastructure.repositories.case_repository import CaseRepository


def test_four_eyes_closure_requires_supervisor_signature() -> None:
    """Case closure to terminal confirmed/false-positive without supervisor signature must be rejected."""
    service = CaseManagementService()
    case = service.create_case(title="Suspicious Structuring Pattern", priority=CasePriority.P2_HIGH)
    service.change_status(case.id, CaseStatus.INVESTIGATING, actor="analyst_1")

    # Attempt closure without supervisor signature
    with pytest.raises(ValueError, match="secondary supervisor signature"):
        service.change_status(case.id, CaseStatus.CLOSED_CONFIRMED, actor="analyst_1")

    with pytest.raises(ValueError, match="secondary supervisor signature"):
        service.change_status(case.id, CaseStatus.CLOSED_FALSE_POSITIVE, actor="analyst_1")


def test_four_eyes_rejection_of_self_approval() -> None:
    """An analyst cannot self-approve a case closure as the supervisor (Four-Eyes Principle)."""
    service = CaseManagementService()
    case = service.create_case(title="Mule Account Inflow", priority=CasePriority.P1_CRITICAL)
    service.change_status(case.id, CaseStatus.INVESTIGATING, actor="analyst_alice")

    # Same identity in supervisor signature and actor
    with pytest.raises(ValueError, match="different from the analyst actor"):
        service.change_status(
            case.id,
            CaseStatus.CLOSED_CONFIRMED,
            actor="analyst_alice",
            supervisor_signature="analyst_alice",
        )

    with pytest.raises(ValueError, match="different from the analyst actor"):
        service.change_status(
            case.id,
            CaseStatus.CLOSED_CONFIRMED,
            actor="analyst_alice",
            supervisor_signature="supervisor:analyst_alice",
        )


def test_four_eyes_rejection_of_duplicate_supervisors() -> None:
    """Rejection when duplicate supervisor identities are passed under dual-control signoff."""
    with pytest.raises(InvalidCaseTransitionError, match="duplicate signer identity"):
        validate_distinct_supervisors(["SIG_SUPERVISOR_ALICE", "SIG_SUPERVISOR_ALICE"])

    service = CaseManagementService()
    case = service.create_case(title="Crypto Outflow Surge", priority=CasePriority.P2_HIGH)
    service.change_status(case.id, CaseStatus.INVESTIGATING, actor="analyst_bob")

    with pytest.raises(ValueError, match="Duplicate supervisor signatures rejected"):
        service.change_status(
            case.id,
            CaseStatus.CLOSED_CONFIRMED,
            actor="analyst_bob",
            supervisor_signatures=["supervisor:carol", "supervisor:carol"],
        )


def test_four_eyes_distinct_supervisors_success_and_retraining_label() -> None:
    """Distinct supervisor signoff closes the case, sets closed_at, and records retraining feedback."""
    service = CaseManagementService()
    case = service.create_case(
        title="Account Takeover via Multi-Bank Device",
        priority=CasePriority.P1_CRITICAL,
        alert_ids=["alt_hardening_01"],
    )
    service.change_status(case.id, CaseStatus.INVESTIGATING, actor="analyst_dave")

    # Close with two distinct supervisors
    closed_case = service.change_status(
        case.id,
        CaseStatus.CLOSED_CONFIRMED,
        actor="analyst_dave",
        supervisor_signature="supervisor:alice",
        second_supervisor_signature="supervisor:bob",
    )

    assert closed_case.status == CaseStatus.CLOSED_CONFIRMED
    assert closed_case.closed_at is not None
    assert len(closed_case.supervisor_signatures) == 2
    assert "supervisor:alice" in closed_case.supervisor_signatures
    assert "supervisor:bob" in closed_case.supervisor_signatures

    # Verify timeline metadata records feedback label
    last_event = closed_case.timeline[-1]
    assert last_event.event_type == "status_changed"
    assert last_event.metadata.get("retraining_feedback_label") == 1
    assert last_event.metadata.get("retraining_feedback_recorded") is True


def test_workbench_stepwise_review_and_dual_signoff() -> None:
    """Stepwise workbench review: escalation, supervisor approval, and resolution."""
    service = InvestigatorCaseWorkbenchService()
    case = service.create_case("Synthetic Identity Fraud", alert_ids=["alt_synth_01"])
    service.assign_investigator(case.case_id, "analyst_emma")
    service.transition_to_investigation(case.case_id, "analyst_emma")

    # Escalate to ESCALATED
    service.escalate_case(case.case_id, "High synthetic score", "analyst_emma")
    assert service.get_case(case.case_id).status == InvestigatorCaseStatus.ESCALATED

    # Two distinct supervisor signatures required for resolution
    resolved = service.resolve_case(
        case_id=case.case_id,
        determination=InvestigatorCaseStatus.RESOLVED_CONFIRMED_FRAUD,
        supervisor_signature="SIG_SUPERVISOR_FRANK",
        second_supervisor_signature="SIG_SUPERVISOR_GRACE",
        actor_id="supervisor_frank",
    )

    assert resolved.status == InvestigatorCaseStatus.RESOLVED_CONFIRMED_FRAUD
    assert "SIG_SUPERVISOR_FRANK" in resolved.supervisor_signatures
    assert "SIG_SUPERVISOR_GRACE" in resolved.supervisor_signatures
    assert len(resolved.supervisor_signatures) == 2


def test_workbench_state_machine_illegal_transition_blocked() -> None:
    """Direct jump from NEW to RESOLVED without investigation is strictly blocked."""
    machine = CaseLifecycleStateMachine()
    case = FraudCaseRecord(
        case_id="case_illegal_test",
        title="Test Illegal Jump",
        alert_ids=["alt_001"],
        status=InvestigatorCaseStatus.NEW,
    )

    with pytest.raises(InvalidCaseTransitionError, match="Illegal transition for case"):
        machine.transition_case(
            record=case,
            target_status=InvestigatorCaseStatus.RESOLVED_CONFIRMED_FRAUD,
            actor_id="analyst_hacker",
            supervisor_signatures=["SIG_SUPERVISOR_A", "SIG_SUPERVISOR_B"],
        )


def test_timeline_cryptographic_hash_integrity_verification() -> None:
    """Investigation timeline maintains a tamper-evident SHA-256 parent hash chain."""
    service = CaseManagementService()
    case = service.create_case(title="Smurfing Ring", priority=CasePriority.P3_MEDIUM)
    service.assign_case(case.id, "analyst_ivan")
    service.add_note(case.id, "analyst_ivan", "Analyzing 14 linked transactions.")
    service.change_status(case.id, CaseStatus.INVESTIGATING, actor="analyst_ivan")

    # Verification passes on clean timeline
    verification = service.verify_timeline_integrity(case.id)
    assert verification["is_valid"] is True
    assert verification["event_count"] >= 4
    assert verification["corrupted_index"] is None
    assert len(verification["chain_hashes"]) == verification["event_count"]


def test_timeline_hash_corruption_detection() -> None:
    """Tampering with an event hash or payload triggers cryptographic corruption detection."""
    service = CaseManagementService()
    case = service.create_case(title="Integrity Tamper Case", priority=CasePriority.P4_LOW)
    service.assign_case(case.id, "analyst_judy")
    service.add_note(case.id, "analyst_judy", "Initial observation.")

    # Tamper with event 1
    fetched_case = service.get_case(case.id)
    assert fetched_case is not None
    fetched_case.timeline[1].metadata["hash"] = "deadbeef" * 8
    service._cases.set(case.id, service._cases.get(case.id))  # ensure key exists
    # Update persisted store with tampered event
    from app.application.services.case_service import _case_to_dict

    service._cases.set(case.id, _case_to_dict(fetched_case))

    # Verification must flag corruption at index 1
    verification = service.verify_timeline_integrity(case.id)
    assert verification["is_valid"] is False
    assert verification["corrupted_index"] == 1
    assert "mismatch" in verification["message"].lower()


def test_case_service_thread_safety_under_concurrent_writes() -> None:
    """Concurrent notes and timeline mutations execute safely under RLock."""
    service = CaseManagementService()
    case = service.create_case(title="Concurrency Stress Case", priority=CasePriority.P2_HIGH)

    def add_concurrent_note(i: int) -> None:
        service.add_note(case.id, f"analyst_{i}", f"Concurrent note payload {i}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(add_concurrent_note, i) for i in range(20)]
        concurrent.futures.wait(futures)

    updated_case = service.get_case(case.id)
    assert updated_case is not None
    assert len(updated_case.notes) == 20
    # Timeline should have 1 created event + 20 note_added events
    assert len(updated_case.timeline) == 21
    # Hash chain must remain valid
    verification = service.verify_timeline_integrity(case.id)
    assert verification["is_valid"] is True


@pytest.mark.asyncio
async def test_case_repository_terminal_status_and_linking() -> None:
    """CaseRepository properly sets closed_at for terminal statuses and appends linked alerts."""
    mock_session = AsyncMock()

    repo = CaseRepository(session=mock_session)

    # 1. Mock get_by_id return
    mock_model = CaseModel(
        id="case_repo_test_01",
        title="Repo Test Case",
        status="open",
        priority="p3_medium",
        alert_ids=["alt_init_01"],
        evidence_ids=[],
        notes=[],
        timeline=[],
        created_at=datetime.now(UTC),
    )
    repo.get_by_id = AsyncMock(return_value=mock_model)  # type: ignore[assignment]

    # 2. Test update_status with closed_confirmed
    updated = await repo.update_status("case_repo_test_01", "closed_confirmed")
    assert updated is not None
    assert mock_session.execute.called
    assert mock_session.commit.called

    # 3. Test link_alert
    linked = await repo.link_alert("case_repo_test_01", "alt_new_02")
    assert linked is not None
    assert "alt_new_02" in mock_model.alert_ids
