# ruff: noqa: E402
"""Comprehensive unit test suite for hardened Consortium Governance & Weighted Quorum State Machine."""

from __future__ import annotations

import datetime
import threading
from datetime import UTC

import pytest

from app.application.services.consortium_service import ConsortiumGovernanceService
from app.domain.consortium_governance import (
    ConsortiumMember,
    ConsortiumStatus,
    MemberRole,
    MembershipProposal,
    ProposalAction,
    ProposalStatus,
)
from app.domain.quorum_manager import DynamicQuorumManager


def test_consortium_input_validations() -> None:
    """Validates that Consortium creation rejects empty, negative, or non-finite inputs."""
    service = ConsortiumGovernanceService()

    with pytest.raises(ValueError, match="consortium_id cannot be empty"):
        service.create_consortium(consortium_id="", name="Net", founder_bank_id="bank_a")

    with pytest.raises(ValueError, match="consortium name cannot be empty"):
        service.create_consortium(consortium_id="eu_net", name="   ", founder_bank_id="bank_a")

    with pytest.raises(ValueError, match="founder_bank_id cannot be empty"):
        service.create_consortium(consortium_id="eu_net", name="Net", founder_bank_id="")

    with pytest.raises(ValueError, match="quorum_ratio must be in"):
        service.create_consortium(consortium_id="eu_net", name="Net", founder_bank_id="bank_a", quorum_ratio=0.0)

    with pytest.raises(ValueError, match="quorum_ratio must be in"):
        service.create_consortium(consortium_id="eu_net", name="Net", founder_bank_id="bank_a", quorum_ratio=1.5)

    with pytest.raises(ValueError, match="max_epsilon must be positive finite number"):
        service.create_consortium(consortium_id="eu_net", name="Net", founder_bank_id="bank_a", max_epsilon=-1.0)

    with pytest.raises(ValueError, match="min_members_n must be >= 1"):
        service.create_consortium(consortium_id="eu_net", name="Net", founder_bank_id="bank_a", min_members_n=0)


def test_consortium_member_voting_rights_and_roles() -> None:
    """Validates ConsortiumMember voting power properties and role-based permissions."""
    m_founder = ConsortiumMember(bank_id="bank_a", role=MemberRole.FOUNDER, voting_power=1.0)
    assert m_founder.can_vote is True

    m_full = ConsortiumMember(bank_id="bank_b", role=MemberRole.FULL_MEMBER, voting_power=2.5)
    assert m_full.can_vote is True

    m_observer = ConsortiumMember(bank_id="bank_c", role=MemberRole.OBSERVER, voting_power=1.0)
    assert m_observer.can_vote is False

    m_zero_power = ConsortiumMember(bank_id="bank_d", role=MemberRole.FULL_MEMBER, voting_power=0.0)
    assert m_zero_power.can_vote is False

    with pytest.raises(ValueError, match="bank_id cannot be empty"):
        ConsortiumMember(bank_id="   ")

    with pytest.raises(ValueError, match="voting_power must be non-negative"):
        ConsortiumMember(bank_id="bank_e", voting_power=-0.5)


def test_membership_proposal_invariants() -> None:
    """Validates MembershipProposal validation bounds and expiration checking."""
    prop = MembershipProposal(
        proposal_id="prop_001",
        consortium_id="c_eu",
        creator_bank_id="bank_a",
        target_bank_id="bank_b",
        action=ProposalAction.ADD_MEMBER,
        required_quorum_ratio=0.51,
        ttl_seconds=3600,
    )
    assert prop.is_expired() is False

    future_time = datetime.datetime.now(UTC) + datetime.timedelta(hours=2)
    assert prop.is_expired(future_time) is True

    with pytest.raises(ValueError, match="proposal_id cannot be empty"):
        MembershipProposal(
            proposal_id="",
            consortium_id="c_eu",
            creator_bank_id="bank_a",
            target_bank_id="bank_b",
            action=ProposalAction.ADD_MEMBER,
        )

    with pytest.raises(ValueError, match="required_quorum_ratio must be strictly between"):
        MembershipProposal(
            proposal_id="prop_002",
            consortium_id="c_eu",
            creator_bank_id="bank_a",
            target_bank_id="bank_b",
            action=ProposalAction.ADD_MEMBER,
            required_quorum_ratio=0.0,
        )


def test_weighted_voting_quorum_calculation() -> None:
    """Validates mathematical weighted quorum evaluation across banks with different voting power."""
    service = ConsortiumGovernanceService()
    service.create_consortium(
        consortium_id="weighted_c",
        name="Weighted Consortium",
        founder_bank_id="bank_large",
        quorum_ratio=0.60,  # 60% of total voting power required
    )
    # Founder has voting_power=5.0
    service.set_member_voting_power("weighted_c", "bank_large", voting_power=5.0)

    # Admit bank_mid (power 2.0) and bank_small (power 1.0)
    # bank_large (5.0 / 5.0 = 100% >= 60%) approves bank_mid
    service.propose_membership_change(
        consortium_id="weighted_c",
        creator_bank_id="bank_large",
        target_bank_id="bank_mid",
        metadata={"voting_power": 2.0},
    )
    # bank_large (5.0 / 7.0 = 71.4% >= 60%) approves bank_small
    service.propose_membership_change(
        consortium_id="weighted_c",
        creator_bank_id="bank_large",
        target_bank_id="bank_small",
        metadata={"voting_power": 1.0},
    )

    consortium = service.get_consortium("weighted_c")
    assert consortium is not None
    assert len(consortium.members) == 3
    # Total voting power = 5.0 + 2.0 + 1.0 = 8.0

    # Bank Small (1.0) proposes adding Bank D
    # 1.0 / 8.0 = 12.5% < 60% -> PENDING
    prop = service.propose_membership_change(
        consortium_id="weighted_c",
        creator_bank_id="bank_small",
        target_bank_id="bank_d",
    )
    assert prop.status == ProposalStatus.PENDING

    # Bank Mid votes FOR: (1.0 + 2.0) / 8.0 = 37.5% < 60% -> PENDING
    service.cast_vote(prop.proposal_id, "bank_mid", approve=True)
    assert prop.status == ProposalStatus.PENDING

    # Bank Large votes FOR: (1.0 + 2.0 + 5.0) / 8.0 = 100% >= 60% -> APPROVED
    service.cast_vote(prop.proposal_id, "bank_large", approve=True)
    assert prop.status == ProposalStatus.APPROVED
    assert "bank_d" in consortium.members


def test_observer_bank_cannot_sponsor_or_vote() -> None:
    """Validates that Observer institutions cannot sponsor proposals or cast votes."""
    service = ConsortiumGovernanceService()
    service.create_consortium(
        consortium_id="obs_c",
        name="Observer Consortium",
        founder_bank_id="bank_a",
        quorum_ratio=0.51,
    )

    # Admit bank_obs as OBSERVER
    service.propose_membership_change(
        consortium_id="obs_c",
        creator_bank_id="bank_a",
        target_bank_id="bank_obs",
        metadata={"role": MemberRole.OBSERVER, "voting_power": 0.0},
    )
    consortium = service.get_consortium("obs_c")
    assert consortium is not None
    assert consortium.members["bank_obs"].role == MemberRole.OBSERVER

    # Observer attempts to propose -> rejected
    with pytest.raises(ValueError, match="cannot sponsor proposals"):
        service.propose_membership_change(
            consortium_id="obs_c",
            creator_bank_id="bank_obs",
            target_bank_id="bank_e",
        )

    # Full member proposes
    prop = service.propose_membership_change(
        consortium_id="obs_c",
        creator_bank_id="bank_a",
        target_bank_id="bank_f",
    )
    # Creator bank_a has 1.0 power, total eligible power is 1.0 (observer excluded) -> 100% >= 51% -> APPROVED
    assert prop.status == ProposalStatus.APPROVED

    # Now test casting vote with observer on pending proposal
    c2 = service.create_consortium(
        consortium_id="obs_c2",
        name="Observer Consortium 2",
        founder_bank_id="bank_1",
        quorum_ratio=0.90,
    )
    # Directly register full member bank_2 and observer bank_obs2
    c2.members["bank_2"] = ConsortiumMember(bank_id="bank_2", role=MemberRole.FULL_MEMBER, voting_power=1.0)
    c2.members["bank_obs2"] = ConsortiumMember(bank_id="bank_obs2", role=MemberRole.OBSERVER, voting_power=0.0)

    # bank_1 proposes bank_target (1/2 eligible = 50% < 90% -> PENDING)
    prop2 = service.propose_membership_change(
        consortium_id="obs_c2",
        creator_bank_id="bank_1",
        target_bank_id="bank_target",
    )
    assert prop2.status == ProposalStatus.PENDING

    # Observer attempts to cast vote on prop2 -> rejected
    with pytest.raises(ValueError, match="cannot cast votes"):
        service.cast_vote(prop2.proposal_id, "bank_obs2", approve=True)


def test_proposal_policy_update_action() -> None:
    """Validates UPDATE_POLICY governance proposal execution and bounds enforcement."""
    service = ConsortiumGovernanceService()
    consortium = service.create_consortium(
        consortium_id="policy_c",
        name="Policy Consortium",
        founder_bank_id="bank_gov",
        quorum_ratio=0.51,
        max_epsilon=5.0,
        min_members_n=2,
    )

    # Propose policy update by founder (100% >= 51% -> APPROVED immediately)
    policy_prop = service.propose_policy_update(
        consortium_id="policy_c",
        creator_bank_id="bank_gov",
        policy_updates={
            "new_quorum_ratio": 0.75,
            "new_max_epsilon": 2.0,
            "new_min_members_n": 3,
            "new_status": ConsortiumStatus.ACTIVE,
        },
    )

    assert policy_prop.status == ProposalStatus.APPROVED
    assert consortium.quorum_ratio == 0.75
    assert consortium.max_epsilon == 2.0
    assert consortium.min_members_n == 3

    # Reject unsupported keys
    with pytest.raises(ValueError, match="Unsupported policy update keys"):
        service.propose_policy_update(
            consortium_id="policy_c",
            creator_bank_id="bank_gov",
            policy_updates={"invalid_key": 123},
        )

    # Reject invalid bounds
    with pytest.raises(ValueError, match="new_quorum_ratio must be in"):
        service.propose_policy_update(
            consortium_id="policy_c",
            creator_bank_id="bank_gov",
            policy_updates={"new_quorum_ratio": 1.5},
        )


def test_cancel_proposal_lifecycle() -> None:
    """Validates that a proposal can only be cancelled by the sponsoring bank while pending."""
    service = ConsortiumGovernanceService()
    service.create_consortium(
        consortium_id="cancel_c",
        name="Cancel Consortium",
        founder_bank_id="bank_a",
        quorum_ratio=0.66,
    )
    # Add bank_b
    service.propose_membership_change(
        consortium_id="cancel_c",
        creator_bank_id="bank_a",
        target_bank_id="bank_b",
    )

    # Bank A proposes adding Bank C (1/2 = 50% < 66% -> PENDING)
    prop = service.propose_membership_change(
        consortium_id="cancel_c",
        creator_bank_id="bank_a",
        target_bank_id="bank_c",
    )
    assert prop.status == ProposalStatus.PENDING

    # Bank B attempts to cancel Bank A's proposal -> rejected
    with pytest.raises(ValueError, match="Only the sponsoring bank"):
        service.cancel_proposal(prop.proposal_id, "bank_b")

    # Bank A cancels own proposal -> CANCELLED
    cancelled = service.cancel_proposal(prop.proposal_id, "bank_a")
    assert cancelled.status == ProposalStatus.CANCELLED
    assert cancelled.cancelled_at is not None

    # Voting on cancelled proposal is rejected
    with pytest.raises(ValueError, match="already CANCELLED"):
        service.cast_vote(prop.proposal_id, "bank_b", approve=True)


def test_proposal_rejection_due_to_unreachable_quorum() -> None:
    """Validates mathematical proposal rejection when AGAINST votes make quorum impossible."""
    service = ConsortiumGovernanceService()
    service.create_consortium(
        consortium_id="reject_c",
        name="Reject Consortium",
        founder_bank_id="bank_a",
        quorum_ratio=0.75,  # 75% required
    )
    # Admit bank_b and bank_c
    service.propose_membership_change(
        consortium_id="reject_c",
        creator_bank_id="bank_a",
        target_bank_id="bank_b",
    )
    # bank_a (1/2 = 50% < 75%) -> PENDING
    prop_c = service.propose_membership_change(
        consortium_id="reject_c",
        creator_bank_id="bank_a",
        target_bank_id="bank_c",
    )
    service.cast_vote(prop_c.proposal_id, "bank_b", approve=True)
    # Now 3 equal members (bank_a, bank_b, bank_c)

    # Bank A proposes adding Bank D (1/3 = 33.3% < 75% -> PENDING)
    prop_d = service.propose_membership_change(
        consortium_id="reject_c",
        creator_bank_id="bank_a",
        target_bank_id="bank_d",
    )
    assert prop_d.status == ProposalStatus.PENDING

    # Bank B votes AGAINST (1/3 = 33.3% against > 1.0 - 0.75 = 25%)
    # Mathematically impossible to reach 75% (max possible for = 2/3 = 66.7%)
    service.cast_vote(prop_d.proposal_id, "bank_b", approve=False)
    assert prop_d.status == ProposalStatus.REJECTED
    assert prop_d.rejection_reason is not None


def test_duplicate_and_invalid_member_actions() -> None:
    """Validates domain protection against adding existing members or removing non-existent members."""
    service = ConsortiumGovernanceService()
    service.create_consortium(
        consortium_id="dup_c",
        name="Dup Consortium",
        founder_bank_id="bank_a",
    )

    # Propose adding already existing founder
    with pytest.raises(ValueError, match="already a member"):
        service.propose_membership_change(
            consortium_id="dup_c",
            creator_bank_id="bank_a",
            target_bank_id="bank_a",
            action=ProposalAction.ADD_MEMBER,
        )

    # Propose removing bank that is not in consortium
    with pytest.raises(ValueError, match="not a member"):
        service.propose_membership_change(
            consortium_id="dup_c",
            creator_bank_id="bank_a",
            target_bank_id="ghost_bank",
            action=ProposalAction.REMOVE_MEMBER,
        )


def test_dynamic_quorum_manager_validations() -> None:
    """Validates parameter bound checks in DynamicQuorumManager."""
    with pytest.raises(ValueError, match="quorum_threshold_pct must be strictly between"):
        DynamicQuorumManager(quorum_threshold_pct=0.0)

    with pytest.raises(ValueError, match="quorum_threshold_pct must be strictly between"):
        DynamicQuorumManager(quorum_threshold_pct=1.2)

    with pytest.raises(ValueError, match="target_window_seconds must be positive integer"):
        DynamicQuorumManager(target_window_seconds=0)

    # Valid initialization
    qm = DynamicQuorumManager(quorum_threshold_pct=0.75, target_window_seconds=120)
    assert qm.quorum_threshold_pct == 0.75
    assert qm.target_window_seconds == 120


def test_consortium_service_thread_safety() -> None:
    """Validates thread-safe concurrent vote casting and querying without data races."""
    service = ConsortiumGovernanceService()
    consortium = service.create_consortium(
        consortium_id="concurrent_c",
        name="Concurrent Consortium",
        founder_bank_id="bank_founder",
        quorum_ratio=0.99,
    )

    # Pre-register 10 banks directly into membership roster
    for i in range(10):
        b_id = f"bank_{i}"
        consortium.members[b_id] = ConsortiumMember(
            bank_id=b_id, role=MemberRole.FULL_MEMBER, voting_power=1.0
        )

    prop = service.propose_membership_change(
        consortium_id="concurrent_c",
        creator_bank_id="bank_founder",
        target_bank_id="new_bank",
    )
    assert prop.status == ProposalStatus.PENDING

    def worker_vote(b_id: str) -> None:
        service.cast_vote(prop.proposal_id, b_id, approve=True)

    threads = [threading.Thread(target=worker_vote, args=(f"bank_{i}",)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All 10 votes recorded plus founder = 11 votes
    assert len(prop.votes_for) == 11


def test_list_proposals_and_members() -> None:
    """Validates proposal listing, status filtering, and member directory queries."""
    service = ConsortiumGovernanceService()
    service.create_consortium(
        consortium_id="list_c",
        name="Listing Consortium",
        founder_bank_id="bank_1",
    )

    p1 = service.propose_membership_change(
        consortium_id="list_c",
        creator_bank_id="bank_1",
        target_bank_id="bank_2",
    )
    assert p1.status == ProposalStatus.APPROVED

    # List all proposals
    all_props = service.list_proposals("list_c")
    assert len(all_props) >= 1

    # Filter by APPROVED
    approved_props = service.list_proposals("list_c", status=ProposalStatus.APPROVED)
    assert len(approved_props) == 1
    assert approved_props[0].proposal_id == p1.proposal_id

    # Filter by REJECTED (empty)
    rejected_props = service.list_proposals("list_c", status=ProposalStatus.REJECTED)
    assert len(rejected_props) == 0

    # List members
    members = service.list_members("list_c")
    member_ids = {m.bank_id for m in members}
    assert "bank_1" in member_ids
    assert "bank_2" in member_ids
