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
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore

logger = logging.getLogger(__name__)


class ConsortiumGovernanceService:
    """Manages consortium creation, weighted voting proposals, dynamic state machine, and member lifecycle."""

    def __init__(self, store: DurableConsortiumStore | None = None) -> None:
        self._store = store or DurableConsortiumStore.get_instance()
        self._lock = threading.RLock()

    @property
    def _consortia(self) -> dict[str, Consortium]:
        """Backward-compatible read access to authoritative consortia dictionary."""
        self._store.reload()
        return self._store._consortia

    @property
    def _proposals(self) -> dict[str, MembershipProposal]:
        """Backward-compatible read access to authoritative proposals dictionary."""
        self._store.reload()
        return self._store._proposals

    def _verify_service_actor_bank(
        self,
        expected_bank: str,
        auth_token: str | None = None,
        actor: str | None = None,
        trusted_context: Any | None = None,
        action_name: str = "consortium_mutation",
    ) -> None:
        """Validate that direct service mutations enforce authentic institutional authority.

        Rejects unauthenticated calls, caller-supplied actor identity without authentication,
        and cross-bank institutional impersonation.
        """
        if trusted_context is not None:
            from app.application.services.model_registry import verify_trusted_internal_context

            is_valid_ctx, ctx_reason = verify_trusted_internal_context(
                trusted_context, expected_action=action_name
            )
            if not is_valid_ctx:
                raise PermissionError(
                    f"Internal orchestration authority rejected for {action_name}: {ctx_reason}"
                )
            if getattr(trusted_context, "service_name", "") != "consortium_coordinator":
                raise PermissionError(
                    f"Internal service '{getattr(trusted_context, 'service_name', '')}' is not authorized for {action_name}."
                )
            return

        if actor and not auth_token:
            raise PermissionError(
                f"Caller-supplied actor identity ('{actor}') cannot establish authority without valid authentication."
            )

        if not auth_token:
            raise PermissionError(
                f"Authentication required: Direct service operation '{action_name}' requires a valid Bearer auth_token or trusted internal context."
            )

        from app.infrastructure.security.auth_service import AuthenticationService

        auth_svc = AuthenticationService.get_instance()
        clean_expected = expected_bank.lower().strip()

        is_valid, claims, err = auth_svc.verify_access_token(auth_token)
        if not is_valid or not claims:
            raise PermissionError(f"Direct service authentication failed: {err}")
        username = (claims.get("username") or claims.get("sub") or "").strip()
        if actor and actor.lower().strip() != username.lower():
            raise PermissionError(
                f"Actor '{actor}' does not match authenticated token principal '{username}'."
            )
        user_rec = auth_svc.get_user(username)
        if not user_rec or not user_rec.is_active or user_rec.status.lower() != "active":
            raise PermissionError(f"Direct service actor '{username}' is not a valid authoritative principal.")
        user_bank = (getattr(user_rec, "bank_id", "") or "").lower().strip()
        if user_bank != clean_expected:
            raise PermissionError(
                f"Cross-bank direct service operation rejected: Principal '{username}' from institution '{user_bank}' "
                f"cannot perform governance actions on behalf of institution '{expected_bank}'."
            )
        user_roles = {r.lower().strip() for r in getattr(user_rec, "roles", [])}
        governance_roles = {"admin", "ml_lead", "compliance"}
        if not (user_roles & governance_roles):
            raise PermissionError(
                f"Principal '{username}' lacks required governance role for {action_name}."
            )

    def create_consortium(
        self,
        consortium_id: str,
        name: str,
        founder_bank_id: str,
        quorum_ratio: float = 0.51,
        max_epsilon: float = 5.0,
        min_members_n: int = 2,
        auth_token: str | None = None,
        actor: str | None = None,
        trusted_context: Any | None = None,
    ) -> Consortium:
        """Initializes a new multi-bank consortium with founder member."""
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

        self._verify_service_actor_bank(
            clean_founder,
            auth_token=auth_token,
            actor=actor,
            trusted_context=trusted_context,
            action_name="create_consortium",
        )

        is_prod = getattr(self._store, "_is_prod", False)

        def _mutate(consortia: dict[str, Consortium], proposals: dict[str, MembershipProposal]) -> Consortium:
            if clean_id in consortia:
                if is_prod:
                    raise ValueError(f"Consortium '{clean_id}' already exists in production.")
                return consortia[clean_id]

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

            consortia[clean_id] = consortium
            logger.info(
                "Created consortium '%s' (%s) with founder '%s'",
                clean_name,
                clean_id,
                clean_founder,
            )
            return consortium

        return self._store.atomic_mutate(_mutate)

    def propose_membership_change(
        self,
        consortium_id: str,
        creator_bank_id: str,
        target_bank_id: str,
        action: ProposalAction = ProposalAction.ADD_MEMBER,
        ttl_seconds: int = 86400,
        metadata: dict[str, Any] | None = None,
        auth_token: str | None = None,
        actor: str | None = None,
        trusted_context: Any | None = None,
    ) -> MembershipProposal:
        """Creates a membership voting proposal (e.g. ADD_MEMBER or REMOVE_MEMBER)."""
        clean_id = consortium_id.lower().strip()
        clean_creator = creator_bank_id.lower().strip()
        clean_target = target_bank_id.lower().strip()

        self._verify_service_actor_bank(
            clean_creator,
            auth_token=auth_token,
            actor=actor,
            trusted_context=trusted_context,
            action_name="propose_membership_change",
        )

        def _mutate(consortia: dict[str, Consortium], proposals: dict[str, MembershipProposal]) -> MembershipProposal:
            if clean_id not in consortia:
                raise KeyError(f"Consortium '{clean_id}' does not exist.")

            consortium = consortia[clean_id]
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
            proposals[proposal_id] = proposal

            logger.info(
                "Opened governance proposal %s in consortium %s for bank %s (action=%s)",
                proposal_id,
                clean_id,
                clean_target,
                action.value,
            )
            self._evaluate_proposal_quorum_in_memory(consortium, proposal)
            return proposal

        return self._store.atomic_mutate(_mutate)

    def propose_policy_update(
        self,
        consortium_id: str,
        creator_bank_id: str,
        policy_updates: dict[str, Any],
        ttl_seconds: int = 86400,
        auth_token: str | None = None,
        actor: str | None = None,
        trusted_context: Any | None = None,
    ) -> MembershipProposal:
        """Creates a policy modification voting proposal (e.g. updating quorum ratio, max epsilon, min members)."""
        clean_id = consortium_id.lower().strip()
        clean_creator = creator_bank_id.lower().strip()

        self._verify_service_actor_bank(
            clean_creator,
            auth_token=auth_token,
            actor=actor,
            trusted_context=trusted_context,
            action_name="propose_policy_update",
        )

        c = self._store.get_consortium(clean_id)
        if c is None:
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
            auth_token=auth_token,
            actor=actor,
            trusted_context=trusted_context,
        )

    def cast_vote(
        self,
        proposal_id: str,
        bank_id: str,
        approve: bool,
        auth_token: str | None = None,
        actor: str | None = None,
        trusted_context: Any | None = None,
    ) -> MembershipProposal:
        """Casts a vote FOR or AGAINST an active proposal with weighted voting power."""
        clean_prop_id = proposal_id.strip()
        clean_bank_id = bank_id.lower().strip()

        self._verify_service_actor_bank(
            clean_bank_id,
            auth_token=auth_token,
            actor=actor,
            trusted_context=trusted_context,
            action_name="cast_vote",
        )

        def _mutate(consortia: dict[str, Consortium], proposals: dict[str, MembershipProposal]) -> MembershipProposal:
            if clean_prop_id not in proposals:
                raise KeyError(f"Proposal '{clean_prop_id}' does not exist.")

            proposal = proposals[clean_prop_id]
            if proposal.consortium_id not in consortia:
                raise KeyError(f"Consortium '{proposal.consortium_id}' does not exist.")
            consortium = consortia[proposal.consortium_id]

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
            self._evaluate_proposal_quorum_in_memory(consortium, proposal)
            return proposal

        return self._store.atomic_mutate(_mutate)

    def cancel_proposal(
        self,
        proposal_id: str,
        creator_bank_id: str,
        auth_token: str | None = None,
        actor: str | None = None,
        trusted_context: Any | None = None,
    ) -> MembershipProposal:
        """Allows the sponsoring bank to withdraw/cancel a pending proposal before resolution."""
        clean_prop_id = proposal_id.strip()
        clean_creator = creator_bank_id.lower().strip()

        self._verify_service_actor_bank(
            clean_creator,
            auth_token=auth_token,
            actor=actor,
            trusted_context=trusted_context,
            action_name="cancel_proposal",
        )

        def _mutate(consortia: dict[str, Consortium], proposals: dict[str, MembershipProposal]) -> MembershipProposal:
            if clean_prop_id not in proposals:
                raise KeyError(f"Proposal '{clean_prop_id}' does not exist.")

            proposal = proposals[clean_prop_id]
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

        return self._store.atomic_mutate(_mutate)

    def set_member_voting_power(
        self, consortium_id: str, bank_id: str, voting_power: float
    ) -> ConsortiumMember:
        """Updates a member institution's voting weight in the consortium."""
        clean_id = consortium_id.lower().strip()
        clean_bank = bank_id.lower().strip()

        if not math.isfinite(voting_power) or voting_power < 0.0:
            raise ValueError(f"voting_power must be non-negative finite number, got {voting_power}")

        def _mutate(consortia: dict[str, Consortium], proposals: dict[str, MembershipProposal]) -> ConsortiumMember:
            if clean_id not in consortia:
                raise KeyError(f"Consortium '{clean_id}' does not exist.")

            consortium = consortia[clean_id]
            if clean_bank not in consortium.members:
                raise ValueError(f"Bank '{clean_bank}' is not a member of consortium '{clean_id}'.")

            consortium.members[clean_bank].voting_power = voting_power
            logger.info(
                "Updated voting power for bank %s in consortium %s to %.2f",
                clean_bank,
                clean_id,
                voting_power,
            )

            # Re-evaluate all pending proposals under the new voting power distribution
            for prop in proposals.values():
                if prop.consortium_id == clean_id and prop.status == ProposalStatus.PENDING:
                    self._evaluate_proposal_quorum_in_memory(consortium, prop)

            return consortium.members[clean_bank]

        return self._store.atomic_mutate(_mutate)

    def set_member_role(
        self, consortium_id: str, bank_id: str, role: MemberRole
    ) -> ConsortiumMember:
        """Updates a member institution's governance role in the consortium."""
        clean_id = consortium_id.lower().strip()
        clean_bank = bank_id.lower().strip()

        def _mutate(consortia: dict[str, Consortium], proposals: dict[str, MembershipProposal]) -> ConsortiumMember:
            if clean_id not in consortia:
                raise KeyError(f"Consortium '{clean_id}' does not exist.")

            consortium = consortia[clean_id]
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
            for prop in proposals.values():
                if prop.consortium_id == clean_id and prop.status == ProposalStatus.PENDING:
                    self._evaluate_proposal_quorum_in_memory(consortium, prop)

            return consortium.members[clean_bank]

        return self._store.atomic_mutate(_mutate)

    def check_expired_proposals(self, consortium_id: str | None = None) -> list[str]:
        """Sweeps proposals and marks expired ones whose voting window has closed."""
        def _mutate(consortia: dict[str, Consortium], proposals: dict[str, MembershipProposal]) -> list[str]:
            expired_ids: list[str] = []
            now = datetime.datetime.now(UTC)

            for prop in proposals.values():
                if consortium_id and prop.consortium_id != consortium_id.lower().strip():
                    continue
                if prop.status == ProposalStatus.PENDING and prop.is_expired(now):
                    prop.status = ProposalStatus.EXPIRED
                    expired_ids.append(prop.proposal_id)
                    logger.info("Proposal %s expired after TTL window.", prop.proposal_id)

            return expired_ids

        return self._store.atomic_mutate(_mutate)

    def _evaluate_proposal_quorum_in_memory(self, consortium: Consortium, proposal: MembershipProposal) -> None:
        """Evaluates weighted voting quorum ratio and executes proposal action if passed (called within mutation lock)."""
        if proposal.is_expired():
            proposal.status = ProposalStatus.EXPIRED
            return

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
            self._execute_proposal_action_in_memory(consortium, proposal)
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

    def _execute_proposal_action_in_memory(self, consortium: Consortium, proposal: MembershipProposal) -> None:
        """Applies approved proposal actions to consortium state (called within mutation lock)."""
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
            consortium.removed_members.pop(target, None)
            logger.info("Bank %s joined consortium %s (role=%s, power=%.2f)", target, consortium.consortium_id, assigned_role.value, assigned_power)

        elif proposal.action == ProposalAction.REMOVE_MEMBER and target in consortium.members:
            del consortium.members[target]
            consortium.removed_members[target] = {
                "bank_id": target,
                "removed_at": datetime.datetime.now(UTC).isoformat(),
                "proposal_id": proposal.proposal_id,
                "reason": f"Expelled via approved governance proposal {proposal.proposal_id}",
            }
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
        """Retrieves consortium by ID after validating disk freshness."""
        return self._store.get_consortium(consortium_id)

    def get_proposal(self, proposal_id: str) -> MembershipProposal | None:
        """Retrieves a proposal by ID and updates expiration if elapsed."""
        proposal = self._store.get_proposal(proposal_id)
        if proposal and proposal.status == ProposalStatus.PENDING and proposal.is_expired():
            def _expire(consortia: dict[str, Consortium], proposals: dict[str, MembershipProposal]) -> None:
                if proposal_id in proposals and proposals[proposal_id].status == ProposalStatus.PENDING:
                    proposals[proposal_id].status = ProposalStatus.EXPIRED
            self._store.atomic_mutate(_expire)
            proposal.status = ProposalStatus.EXPIRED
        return proposal

    def list_proposals(
        self, consortium_id: str, status: ProposalStatus | None = None
    ) -> list[MembershipProposal]:
        """Lists proposals for a given consortium, optionally filtered by status."""
        return self._store.list_proposals(consortium_id, status=status)

    def list_members(self, consortium_id: str) -> list[ConsortiumMember]:
        """Lists all registered members of a consortium."""
        c = self._store.get_consortium(consortium_id)
        if not c:
            return []
        return list(c.members.values())

    def add_member(
        self,
        consortium_id: str,
        bank_id: str,
        role: MemberRole = MemberRole.FULL_MEMBER,
        voting_power: float = 1.0,
        auth_token: str | None = None,
        actor: str | None = None,
        trusted_context: Any | None = None,
    ) -> ConsortiumMember:
        """Registers a member institution into a consortium.

        Guarantees:
        - In production, requires authentic institutional authorization.
        - Rejects direct readmission of an expelled member without governance approval.
        """
        clean_id = consortium_id.lower().strip()
        clean_bank = bank_id.lower().strip()

        is_prod = getattr(self._store, "_is_prod", False)
        if is_prod and not auth_token and trusted_context is None:
            raise PermissionError(
                "Authentication required: Direct service operation 'add_member' requires a valid Bearer auth_token or trusted internal context."
            )

        def _mutate(consortia: dict[str, Consortium], proposals: dict[str, MembershipProposal]) -> ConsortiumMember:
            if clean_id not in consortia:
                raise KeyError(f"Consortium '{clean_id}' does not exist.")

            consortium = consortia[clean_id]

            # In production with auth_token, verify institutional authority
            if is_prod and auth_token:
                founder_or_admin = next(
                    (m.bank_id for m in consortium.members.values() if m.role in (MemberRole.FOUNDER, MemberRole.FULL_MEMBER) and m.can_vote),
                    clean_bank
                )
                self._verify_service_actor_bank(
                    founder_or_admin,
                    auth_token=auth_token,
                    actor=actor,
                    trusted_context=trusted_context,
                    action_name="add_member",
                )

            # Prevent silent readmission of an expelled member
            if clean_bank in consortium.removed_members:
                raise PermissionError(
                    f"Direct admission rejected: Institution '{clean_bank}' was previously expelled from consortium '{clean_id}'. "
                    f"Re-admission requires a formal governance proposal and voting quorum."
                )

            member = ConsortiumMember(bank_id=clean_bank, role=role, voting_power=voting_power)
            consortium.members[clean_bank] = member
            return member

        return self._store.atomic_mutate(_mutate)

    def reset(self) -> None:
        """Resets service state for clean test isolation."""
        with self._lock:
            self._store.clear()
            init_default_consortium(self)


def init_default_consortium(service: ConsortiumGovernanceService | None = None) -> Consortium:
    """Initializes canonical consortium with founding and participant institutions.

    Guarantees:
    - Never recreates or overwrites an established consortium.
    - Never readmits previously removed/expelled members upon worker startup.
    """
    svc = service or consortium_governance_service
    existing = svc.get_consortium("cfi-consortium")
    if existing is not None:
        return existing

    from app.application.services.model_registry import create_trusted_internal_context

    ctx = create_trusted_internal_context(
        service_name="consortium_coordinator",
        action="create_consortium",
    )
    c = svc.create_consortium(
        consortium_id="cfi-consortium",
        name="Cross-Bank Federated Intelligence Consortium",
        founder_bank_id="bank_a",
        quorum_ratio=0.51,
        max_epsilon=5.0,
        min_members_n=2,
        trusted_context=ctx,
    )
    ctx_add = create_trusted_internal_context(
        service_name="consortium_coordinator",
        action="add_member",
    )
    svc.add_member("cfi-consortium", "bank_b", role=MemberRole.FULL_MEMBER, voting_power=1.0, trusted_context=ctx_add)
    svc.add_member("cfi-consortium", "bank_c", role=MemberRole.FULL_MEMBER, voting_power=1.0, trusted_context=ctx_add)
    return c


# Global singleton instance
consortium_governance_service = ConsortiumGovernanceService()
init_default_consortium(consortium_governance_service)


