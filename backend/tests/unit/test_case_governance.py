"""Unit tests for Case Domain Model and Four-Eyes Governance invariants.

Covers:
- State machine transition integrity across all lifecycle states.
- Rejection of illegal shortcut transitions.
- Terminal state immutability and replay resistance.
- Cryptographic Four-Eyes dual control separation of duties (ApproverID != InvestigatorID, ApproverID != ActorID).
- Distinct supervisor signature verification (Supervisor_1 != Supervisor_2).
- Case-insensitive and prefix-stripped identity normalization.
- Cryptographic timeline hash chaining and tamper-evident auditing.
"""

from datetime import UTC, datetime

import pytest

from app.domain.models.case import (
    CaseDomainModel,
    CaseState,
    DuplicateSupervisorSignatureError,
    FourEyesVerificationError,
    InvalidCaseTransitionError,
    SelfApprovalProhibitedError,
    TerminalCaseImmutableError,
    clean_identity,
    compute_timeline_hash,
    validate_four_eyes_authorization,
)


class TestCleanIdentity:
    """Test suite for identity normalization and prefix sanitization."""

    def test_strip_common_role_prefixes(self) -> None:
        assert clean_identity("supervisor:alice") == "alice"
        assert clean_identity("investigator:bob") == "bob"
        assert clean_identity("analyst:charlie") == "charlie"
        assert clean_identity("sig_supervisor_diana") == "diana"
        assert clean_identity("SIG_SUPERVISOR_diana") == "diana"

    def test_case_insensitivity_and_whitespace(self) -> None:
        assert clean_identity("  Supervisor:ALICE  ") == "alice"
        assert clean_identity("SIG_SUPERVISOR_BOB  ") == "bob"
        assert clean_identity("  INVESTIGATOR:Charlie") == "charlie"

    def test_empty_and_none_handling(self) -> None:
        assert clean_identity("") == ""
        assert clean_identity("   ") == ""
        assert clean_identity(None) == ""


class TestTimelineHashChaining:
    """Test suite for SHA-256 tamper-evident timeline audit trail."""

    def test_deterministic_timeline_hash(self) -> None:
        ts = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
        h1 = compute_timeline_hash(
            timestamp=ts,
            event_type="status_changed",
            description="Status: open -> investigating",
            actor="analyst_1",
            parent_hash="0" * 64,
        )
        h2 = compute_timeline_hash(
            timestamp=ts,
            event_type="status_changed",
            description="Status: open -> investigating",
            actor="analyst_1",
            parent_hash="0" * 64,
        )
        assert h1 == h2
        assert len(h1) == 64

    def test_tamper_detection_in_payload_or_actor(self) -> None:
        ts = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
        h_base = compute_timeline_hash(
            timestamp=ts,
            event_type="status_changed",
            description="Status: open -> investigating",
            actor="analyst_1",
            parent_hash="0" * 64,
        )
        h_tampered = compute_timeline_hash(
            timestamp=ts,
            event_type="status_changed",
            description="Status: open -> investigating",
            actor="attacker_1",  # Tampered actor
            parent_hash="0" * 64,
        )
        assert h_base != h_tampered

    def test_timeline_chain_continuity(self) -> None:
        model = CaseDomainModel(
            case_id="CASE-2026-001",
            title="Suspicious structuring",
            status=CaseState.OPEN,
            assigned_to="investigator_alice",
        )
        evt1 = model.append_event(
            event_type="status_changed",
            description="Status: open -> investigating",
            actor="investigator_alice",
            metadata={"new_state": "investigating"},
        )
        evt2 = model.append_event(
            event_type="status_changed",
            description="Status: investigating -> pending_review",
            actor="investigator_alice",
            metadata={"new_state": "pending_review"},
        )

        assert evt1.metadata["parent_hash"] == "0" * 64
        assert evt2.metadata["parent_hash"] == evt1.metadata["hash"]
        assert len(evt2.metadata["hash"]) == 64


class TestLifecycleStateMachineTransitions:
    """Test suite for valid and invalid lifecycle transitions."""

    def test_valid_sequential_lifecycle_progression(self) -> None:
        model = CaseDomainModel(
            case_id="CASE-2026-002",
            title="Smurfing ring",
            status=CaseState.OPEN,
            assigned_to="investigator_alice",
        )
        # OPEN -> INVESTIGATING
        assert model.can_transition_to(CaseState.INVESTIGATING) is True
        model.transition_to(CaseState.INVESTIGATING, actor_id="investigator_alice")
        assert model.status == CaseState.INVESTIGATING

        # INVESTIGATING -> PENDING_REVIEW
        assert model.can_transition_to(CaseState.PENDING_REVIEW) is True
        model.transition_to(CaseState.PENDING_REVIEW, actor_id="investigator_alice")
        assert model.status == CaseState.PENDING_REVIEW

        # PENDING_REVIEW -> CLOSED_CONFIRMED with 2 distinct supervisor signatures
        assert model.can_transition_to(CaseState.CLOSED_CONFIRMED) is True
        model.transition_to(
            CaseState.CLOSED_CONFIRMED,
            actor_id="analyst_bob",
            supervisor_signatures=["supervisor:carol", "supervisor:diana"],
        )
        assert model.status == CaseState.CLOSED_CONFIRMED
        assert model.is_terminal() is True
        assert model.closed_at is not None

    def test_reject_illegal_shortcut_transitions(self) -> None:
        model = CaseDomainModel(
            case_id="CASE-2026-003",
            title="Shortcut attempt",
            status=CaseState.OPEN,
        )
        # Cannot transition OPEN -> CLOSED_CONFIRMED directly
        assert model.can_transition_to(CaseState.CLOSED_CONFIRMED) is False
        with pytest.raises(InvalidCaseTransitionError, match="Illegal transition"):
            model.transition_to(CaseState.CLOSED_CONFIRMED, actor_id="analyst_1")

        # Cannot transition OPEN -> SAR_FILED directly
        assert model.can_transition_to(CaseState.SAR_FILED) is False
        with pytest.raises(InvalidCaseTransitionError, match="Illegal transition"):
            model.transition_to(CaseState.SAR_FILED, actor_id="analyst_1")

    def test_terminal_state_immutability(self) -> None:
        model = CaseDomainModel(
            case_id="CASE-2026-004",
            title="Finalized case",
            status=CaseState.CLOSED_CONFIRMED,
        )
        assert model.is_terminal() is True

        # Terminal state cannot transition to ANY state
        for target in [CaseState.OPEN, CaseState.INVESTIGATING, CaseState.PENDING_REVIEW, CaseState.CLOSED_FALSE_POSITIVE]:
            assert model.can_transition_to(target) is False
            with pytest.raises(TerminalCaseImmutableError, match="finalized in terminal state"):
                model.transition_to(target, actor_id="supervisor_1")


class TestFourEyesDualControlGovernance:
    """Test suite for dual-control separation of duties and self-approval prevention."""

    def test_prevent_assigned_investigator_self_approval(self) -> None:
        with pytest.raises(SelfApprovalProhibitedError, match="cannot approve their own case"):
            validate_four_eyes_authorization(
                actor_id="analyst_charlie",
                assigned_investigator="investigator:alice",
                supervisor_signatures=["supervisor:alice"],  # Alice attempts to self-approve
                require_dual_supervisors=False,
            )

    def test_prevent_analyst_actor_self_approval(self) -> None:
        with pytest.raises(SelfApprovalProhibitedError, match="cannot provide supervisor signoff"):
            validate_four_eyes_authorization(
                actor_id="analyst:charlie",
                assigned_investigator="investigator_bob",
                supervisor_signatures=["charlie"],  # Charlie is the current actor attempting self-approval
                require_dual_supervisors=False,
            )

    def test_prevent_duplicate_supervisor_signatures(self) -> None:
        with pytest.raises(DuplicateSupervisorSignatureError, match="Duplicate supervisor signature"):
            validate_four_eyes_authorization(
                actor_id="analyst_bob",
                assigned_investigator="investigator_alice",
                supervisor_signatures=["supervisor:carol", "SIG_SUPERVISOR_carol"],  # Same person with different prefix
                require_dual_supervisors=True,
            )

    def test_case_insensitive_self_approval_detection(self) -> None:
        with pytest.raises(SelfApprovalProhibitedError, match="cannot approve their own case"):
            validate_four_eyes_authorization(
                actor_id="analyst_bob",
                assigned_investigator="Investigator:ALICE",
                supervisor_signatures=["alice"],
                require_dual_supervisors=False,
            )

    def test_insufficient_signatures_raises(self) -> None:
        with pytest.raises(FourEyesVerificationError, match="requires at least 2 distinct supervisor signatures"):
            validate_four_eyes_authorization(
                actor_id="analyst_bob",
                assigned_investigator="investigator_alice",
                supervisor_signatures=["supervisor:carol"],  # Only 1 signature provided when 2 required
                require_dual_supervisors=True,
            )

    def test_valid_two_distinct_supervisor_approval(self) -> None:
        cleaned = validate_four_eyes_authorization(
            actor_id="analyst_bob",
            assigned_investigator="investigator_alice",
            supervisor_signatures=["supervisor:carol", "supervisor:diana"],
            require_dual_supervisors=True,
        )
        assert cleaned == ["supervisor:carol", "supervisor:diana"]
