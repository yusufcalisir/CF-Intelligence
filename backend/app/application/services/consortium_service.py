"""Consortium Governance Service handling multi-bank alliances, weighted voting quorums, and proposal state machines."""

from __future__ import annotations

import datetime
import logging
import math
import threading
import uuid
from datetime import UTC
from typing import Any

from app.domain.consortium_governance import (
    Consortium,
    ConsortiumMember,
    ConsortiumStatus,
    MemberRole,
    MembershipProposal,
    ProposalAction,
    ProposalStatus,
)

logger = logging.getLogger(__name__)


class ConsortiumGovernanceService:
    """Manages consortium creation, weighted voting proposals, dynamic state machine, and member lifecycle."""

    def __init__(self) -> None:
        self._consortia: dict[str, Consortium] = {}
        self._proposals: dict[str, MembershipProposal] = {}
        self._lock = threading.RLock()

    def create_consortium(
        self,
        consortium_id: str,
        name: str,
        founder_bank_id: str,
        quorum_ratio: float = 0.51,
        max_epsilon: float = 5.0,
        min_members_n: int = 2,
    ) -> Consortium:
        """Initializes a new multi-bank consortium with founder member."""
        with self._lock:
            clean_id = consortium_id.lower().strip()
            clean_name = name.strip()
            clean_founder = founder_bank_id.lower().strip()

            if not clean_id:
                raise ValueError("consortium_id cannot be empty or whitespace.")
            if not clean_name:
                raise ValueError("consortium name cannot be empty or whitespace.")
            if not clean_founder:
                raise ValueError("founder_bank_id cannot be empty or whitespace.")
            if not (0.0 < quorum_ratio <= 1.0) or not math.isfinite(quorum_ratio):
                raise ValueError(f"quorum_ratio must be in (0.0, 1.0], got {quorum_ratio}")
            if max_epsilon <= 0.0 or not math.isfinite(max_epsilon):
                raise ValueError(f"max_epsilon must be positive finite number, got {max_epsilon}")
            if min_members_n < 1:
                raise ValueError(f"min_members_n must be >= 1, got {min_members_n}")

            if clean_id in self._consortia:
                return self._consortia[clean_id]

            founder = ConsortiumMember(
                bank_id=clean_founder,
                role=MemberRole.FOUNDER,
                voting_power=1.0,
            )
            consortium = Consortium(
                consortium_id=clean_id,
                name=clean_name,
                quorum_ratio=quorum_ratio,
                min_members_n=min_members_n,
                max_epsilon=max_epsilon,
                status=ConsortiumStatus.ACTIVE,
                members={clean_founder: founder},
            )

            self._consortia[clean_id] = consortium
            logger.info(
                "Created consortium '%s' (%s) with founder '%s'",
                clean_name,
                clean_id,
                clean_founder,
            )
            return consortium

    def propose_membership_change(
        self,
        consortium_id: str,
        creator_bank_id: str,
        target_bank_id: str,
        action: ProposalAction = ProposalAction.ADD_MEMBER,
        ttl_seconds: int = 86400,
        metadata: dict[str, Any] | None = None,
    ) -> MembershipProposal:
        """Creates a membership voting proposal (e.g. ADD_MEMBER or REMOVE_MEMBER)."""
        with self._lock:
            clean_id = consortium_id.lower().strip()
            clean_creator = creator_bank_id.lower().strip()
            clean_target = target_bank_id.lower().strip()

            if clean_id not in self._consortia:
                raise KeyError(f"Consortium '{clean_id}' does not exist.")

            consortium = self._consortia[clean_id]
            if clean_creator not in consortium.members:
                raise ValueError(
                    f"Bank '{clean_creator}' is not a member of consortium '{clean_id}'."
                )

            creator_member = consortium.members[clean_creator]
            if not creator_member.can_vote:
                raise ValueError(
                    f"Bank '{clean_creator}' has role {creator_member.role.value} or 0 voting power and cannot sponsor proposals."
                )

            # Defensive domain invariants for proposal actions
            if action == ProposalAction.ADD_MEMBER and clean_target in consortium.members:
                raise ValueError(
                    f"Bank '{clean_target}' is already a member of consortium '{clean_id}'."
                )
            if action == ProposalAction.REMOVE_MEMBER and clean_target not in consortium.members:
                raise ValueError(
                    f"Bank '{clean_target}' is not a member of consortium '{clean_id}'."
                )

            proposal_id = f"prop_{uuid.uuid4().hex[:8]}"
            proposal = MembershipProposal(
                proposal_id=proposal_id,
                consortium_id=clean_id,
                creator_bank_id=clean_creator,
                target_bank_id=clean_target,
                action=action,
                required_quorum_ratio=consortium.quorum_ratio,
                ttl_seconds=ttl_seconds,
                metadata=metadata or {},
            )

            # Creator automatically votes FOR the proposal
            proposal.votes_for.add(clean_creator)
            self._proposals[proposal_id] = proposal

            logger.info(
                "Opened governance proposal %s in consortium %s for bank %s (action=%s)",
                proposal_id,
                clean_id,
                clean_target,
                action.value,
            )
            self._evaluate_proposal_quorum(proposal)
            return proposal

    def propose_policy_update(
        self,
        consortium_id: str,
        creator_bank_id: str,
        policy_updates: dict[str, Any],
        ttl_seconds: int = 86400,
    ) -> MembershipProposal:
        """Creates a policy modification voting proposal (e.g. updating quorum ratio, max epsilon, min members)."""
        with self._lock:
            clean_id = consortium_id.lower().strip()
            clean_creator = creator_bank_id.lower().strip()

            if clean_id not in self._consortia:
                raise KeyError(f"Consortium '{clean_id}' does not exist.")

            if not policy_updates:
                raise ValueError("policy_updates cannot be empty.")

            # Validate proposed policy fields
            valid_keys = {"new_quorum_ratio", "new_max_epsilon", "new_min_members_n", "new_status"}
            unknown_keys = set(policy_updates.keys()) - valid_keys
            if unknown_keys:
                raise ValueError(f"Unsupported policy update keys: {sorted(unknown_keys)}")

            if "new_quorum_ratio" in policy_updates:
                q = float(policy_updates["new_quorum_ratio"])
                if not (0.0 < q <= 1.0) or not math.isfinite(q):
                    raise ValueError(f"new_quorum_ratio must be in (0.0, 1.0], got {q}")

            if "new_max_epsilon" in policy_updates:
                eps = float(policy_updates["new_max_epsilon"])
                if eps <= 0.0 or not math.isfinite(eps):
                    raise ValueError(f"new_max_epsilon must be positive finite number, got {eps}")

            if "new_min_members_n" in policy_updates:
                n = int(policy_updates["new_min_members_n"])
                if n < 1:
                    raise ValueError(f"new_min_members_n must be >= 1, got {n}")

            return self.propose_membership_change(
                consortium_id=clean_id,
                creator_bank_id=clean_creator,
                target_bank_id="CONSORTIUM_POLICY",
                action=ProposalAction.UPDATE_POLICY,
                ttl_seconds=ttl_seconds,
                metadata=policy_updates,
            )

    def cast_vote(self, proposal_id: str, bank_id: str, approve: bool) -> MembershipProposal:
        """Casts a vote FOR or AGAINST an active proposal with weighted voting power."""
        with self._lock:
            clean_prop_id = proposal_id.strip()
            clean_bank_id = bank_id.lower().strip()

            if clean_prop_id not in self._proposals:
                raise KeyError(f"Proposal '{clean_prop_id}' does not exist.")

            proposal = self._proposals[clean_prop_id]
            consortium = self._consortia[proposal.consortium_id]

            if clean_bank_id not in consortium.members:
                raise ValueError(
                    f"Bank '{clean_bank_id}' is not a member of consortium '{proposal.consortium_id}'."
                )

            member = consortium.members[clean_bank_id]
            if not member.can_vote:
                raise ValueError(
                    f"Bank '{clean_bank_id}' has role {member.role.value} with voting power {member.voting_power} and cannot cast votes."
                )

            # Lazy expiration check
            if proposal.is_expired():
                proposal.status = ProposalStatus.EXPIRED
                raise ValueError(f"Proposal '{clean_prop_id}' has EXPIRED and is closed for voting.")

            if proposal.status != ProposalStatus.PENDING:
                raise ValueError(f"Proposal '{clean_prop_id}' is already {proposal.status.value}.")

            if approve:
                proposal.votes_for.add(clean_bank_id)
                proposal.votes_against.discard(clean_bank_id)
            else:
                proposal.votes_against.add(clean_bank_id)
                proposal.votes_for.discard(clean_bank_id)

            logger.info(
                "Bank %s voted %s on proposal %s (voting_power=%.2f)",
                clean_bank_id,
                "FOR" if approve else "AGAINST",
                clean_prop_id,
                member.voting_power,
            )
            self._evaluate_proposal_quorum(proposal)
            return proposal

    def cancel_proposal(self, proposal_id: str, creator_bank_id: str) -> MembershipProposal:
        """Allows the sponsoring bank to withdraw/cancel a pending proposal before resolution."""
        with self._lock:
            clean_prop_id = proposal_id.strip()
            clean_creator = creator_bank_id.lower().strip()

            if clean_prop_id not in self._proposals:
                raise KeyError(f"Proposal '{clean_prop_id}' does not exist.")

            proposal = self._proposals[clean_prop_id]
            if proposal.status != ProposalStatus.PENDING:
                raise ValueError(
                    f"Cannot cancel proposal '{clean_prop_id}' in state {proposal.status.value}."
                )

            if proposal.creator_bank_id != clean_creator:
                raise ValueError(
                    f"Only the sponsoring bank '{proposal.creator_bank_id}' can cancel proposal '{clean_prop_id}'."
                )

            proposal.status = ProposalStatus.CANCELLED
            proposal.cancelled_at = datetime.datetime.now(UTC)
            logger.info(
                "Proposal %s was CANCELLED by sponsoring bank %s",
                clean_prop_id,
                clean_creator,
            )
            return proposal

    def set_member_voting_power(
        self, consortium_id: str, bank_id: str, voting_power: float
    ) -> ConsortiumMember:
        """Updates a member institution's voting weight in the consortium."""
        with self._lock:
            clean_id = consortium_id.lower().strip()
            clean_bank = bank_id.lower().strip()

            if clean_id not in self._consortia:
                raise KeyError(f"Consortium '{clean_id}' does not exist.")

            consortium = self._consortia[clean_id]
            if clean_bank not in consortium.members:
                raise ValueError(f"Bank '{clean_bank}' is not a member of consortium '{clean_id}'.")

            if not math.isfinite(voting_power) or voting_power < 0.0:
                raise ValueError(f"voting_power must be non-negative finite number, got {voting_power}")

            consortium.members[clean_bank].voting_power = voting_power
            logger.info(
                "Updated voting power for bank %s in consortium %s to %.2f",
                clean_bank,
                clean_id,
                voting_power,
            )

            # Re-evaluate all pending proposals under the new voting power distribution
            for prop in self._proposals.values():
                if prop.consortium_id == clean_id and prop.status == ProposalStatus.PENDING:
                    self._evaluate_proposal_quorum(prop)

            return consortium.members[clean_bank]

    def set_member_role(
        self, consortium_id: str, bank_id: str, role: MemberRole
    ) -> ConsortiumMember:
        """Updates a member institution's governance role in the consortium."""
        with self._lock:
            clean_id = consortium_id.lower().strip()
            clean_bank = bank_id.lower().strip()

            if clean_id not in self._consortia:
                raise KeyError(f"Consortium '{clean_id}' does not exist.")

            consortium = self._consortia[clean_id]
            if clean_bank not in consortium.members:
                raise ValueError(f"Bank '{clean_bank}' is not a member of consortium '{clean_id}'.")

            consortium.members[clean_bank].role = role
            logger.info(
                "Updated role for bank %s in consortium %s to %s",
                clean_bank,
                clean_id,
                role.value,
            )

            # Re-evaluate all pending proposals
            for prop in self._proposals.values():
                if prop.consortium_id == clean_id and prop.status == ProposalStatus.PENDING:
                    self._evaluate_proposal_quorum(prop)

            return consortium.members[clean_bank]

    def check_expired_proposals(self, consortium_id: str | None = None) -> list[str]:
        """Sweeps proposals and marks expired ones whose voting window has closed."""
        with self._lock:
            expired_ids: list[str] = []
            now = datetime.datetime.now(UTC)

            for prop in self._proposals.values():
                if consortium_id and prop.consortium_id != consortium_id.lower().strip():
                    continue
                if prop.status == ProposalStatus.PENDING and prop.is_expired(now):
                    prop.status = ProposalStatus.EXPIRED
                    expired_ids.append(prop.proposal_id)
                    logger.info("Proposal %s expired after TTL window.", prop.proposal_id)

            return expired_ids

    def _evaluate_proposal_quorum(self, proposal: MembershipProposal) -> None:
        """Evaluates weighted voting quorum ratio and executes proposal action if passed."""
        if proposal.is_expired():
            proposal.status = ProposalStatus.EXPIRED
            return

        consortium = self._consortia[proposal.consortium_id]
        eligible_members = [m for m in consortium.members.values() if m.can_vote]

        total_voting_power = sum(m.voting_power for m in eligible_members)
        if total_voting_power <= 0.0:
            total_voting_power = max(float(len(eligible_members)), 1.0)

        power_for = sum(
            consortium.members[b].voting_power
            for b in proposal.votes_for
            if b in consortium.members and consortium.members[b].can_vote
        )
        power_against = sum(
            consortium.members[b].voting_power
            for b in proposal.votes_against
            if b in consortium.members and consortium.members[b].can_vote
        )

        ratio_for = power_for / total_voting_power
        ratio_against = power_against / total_voting_power

        if ratio_for >= proposal.required_quorum_ratio:
            proposal.status = ProposalStatus.APPROVED
            self._execute_proposal_action(proposal)
            logger.info(
                "Proposal %s APPROVED (weighted ratio: %.3f >= %.3f)",
                proposal.proposal_id,
                ratio_for,
                proposal.required_quorum_ratio,
            )
        elif ratio_against > (1.0 - proposal.required_quorum_ratio):
            proposal.status = ProposalStatus.REJECTED
            proposal.rejection_reason = (
                f"Against votes (ratio {ratio_against:.3f}) prevent reaching required quorum ratio {proposal.required_quorum_ratio:.3f}"
            )
            logger.info("Proposal %s REJECTED: %s", proposal.proposal_id, proposal.rejection_reason)

    def _execute_proposal_action(self, proposal: MembershipProposal) -> None:
        """Applies approved proposal actions to consortium state."""
        consortium = self._consortia[proposal.consortium_id]
        target = proposal.target_bank_id

        if proposal.action == ProposalAction.ADD_MEMBER and target not in consortium.members:
            assigned_role = proposal.metadata.get("role", MemberRole.FULL_MEMBER)
            if isinstance(assigned_role, str):
                assigned_role = MemberRole(assigned_role)
            assigned_power = float(proposal.metadata.get("voting_power", 1.0))

            consortium.members[target] = ConsortiumMember(
                bank_id=target,
                role=assigned_role,
                voting_power=assigned_power,
            )
            logger.info("Bank %s joined consortium %s (role=%s, power=%.2f)", target, consortium.consortium_id, assigned_role.value, assigned_power)

        elif proposal.action == ProposalAction.REMOVE_MEMBER and target in consortium.members:
            del consortium.members[target]
            logger.info("Bank %s evicted from consortium %s", target, consortium.consortium_id)

        elif proposal.action == ProposalAction.UPDATE_POLICY:
            meta = proposal.metadata
            if "new_quorum_ratio" in meta:
                consortium.quorum_ratio = float(meta["new_quorum_ratio"])
                logger.info("Consortium %s quorum ratio updated to %.2f", consortium.consortium_id, consortium.quorum_ratio)
            if "new_max_epsilon" in meta:
                consortium.max_epsilon = float(meta["new_max_epsilon"])
                logger.info("Consortium %s max epsilon updated to %.2f", consortium.consortium_id, consortium.max_epsilon)
            if "new_min_members_n" in meta:
                consortium.min_members_n = int(meta["new_min_members_n"])
                logger.info("Consortium %s min members updated to %d", consortium.consortium_id, consortium.min_members_n)
            if "new_status" in meta:
                new_st = meta["new_status"]
                if isinstance(new_st, str):
                    new_st = ConsortiumStatus(new_st)
                consortium.status = new_st
                logger.info("Consortium %s status updated to %s", consortium.consortium_id, consortium.status.value)

    def get_consortium(self, consortium_id: str) -> Consortium | None:
        """Retrieves consortium by ID."""
        with self._lock:
            return self._consortia.get(consortium_id.lower().strip())

    def get_proposal(self, proposal_id: str) -> MembershipProposal | None:
        """Retrieves a proposal by ID and updates expiration if elapsed."""
        with self._lock:
            clean_id = proposal_id.strip()
            proposal = self._proposals.get(clean_id)
            if proposal and proposal.status == ProposalStatus.PENDING and proposal.is_expired():
                proposal.status = ProposalStatus.EXPIRED
            return proposal

    def list_proposals(
        self, consortium_id: str, status: ProposalStatus | None = None
    ) -> list[MembershipProposal]:
        """Lists proposals for a given consortium, optionally filtered by status."""
        with self._lock:
            clean_id = consortium_id.lower().strip()
            results: list[MembershipProposal] = []
            for prop in self._proposals.values():
                if prop.consortium_id == clean_id:
                    if prop.status == ProposalStatus.PENDING and prop.is_expired():
                        prop.status = ProposalStatus.EXPIRED
                    if status is None or prop.status == status:
                        results.append(prop)
            return sorted(results, key=lambda p: p.created_at, reverse=True)

    def list_members(self, consortium_id: str) -> list[ConsortiumMember]:
        """Lists all registered members of a consortium."""
        with self._lock:
            clean_id = consortium_id.lower().strip()
            consortium = self._consortia.get(clean_id)
            if not consortium:
                return []
            return list(consortium.members.values())

    def add_member(
        self,
        consortium_id: str,
        bank_id: str,
        role: MemberRole = MemberRole.FULL_MEMBER,
        voting_power: float = 1.0,
    ) -> ConsortiumMember:
        """Directly registers a member institution into a consortium."""
        with self._lock:
            clean_id = consortium_id.lower().strip()
            clean_bank = bank_id.lower().strip()
            if clean_id not in self._consortia:
                raise KeyError(f"Consortium '{clean_id}' does not exist.")
            member = ConsortiumMember(bank_id=clean_bank, role=role, voting_power=voting_power)
            self._consortia[clean_id].members[clean_bank] = member
            return member

    def reset(self) -> None:
        """Resets service state for clean test isolation."""
        with self._lock:
            self._consortia.clear()
            self._proposals.clear()
            init_default_consortium(self)


def init_default_consortium(service: ConsortiumGovernanceService) -> Consortium:
    """Initializes canonical consortium with founding and participant institutions."""
    c = service.create_consortium(
        consortium_id="cfi-consortium",
        name="Cross-Bank Federated Intelligence Consortium",
        founder_bank_id="bank_a",
        quorum_ratio=0.51,
        max_epsilon=5.0,
        min_members_n=2,
    )
    service.add_member("cfi-consortium", "bank_b", role=MemberRole.FULL_MEMBER, voting_power=1.0)
    service.add_member("cfi-consortium", "bank_c", role=MemberRole.FULL_MEMBER, voting_power=1.0)
    return c


# Global singleton instance
consortium_governance_service = ConsortiumGovernanceService()
init_default_consortium(consortium_governance_service)

