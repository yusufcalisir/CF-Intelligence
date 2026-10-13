"""Federated Learning Coordinator & Consortium Governance Endpoints.

Exposes REST APIs for dynamic bank client handshake, status checks, capability negotiation,
registration lookup, weighted consortium governance proposals, and dynamic quorum verification.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Path, Query, status

from app.application.schemas.coordinator import (
    AsyncFLEngineStatusResponse,
    AsyncUpdateRequest,
    AsyncUpdateResponse,
    ClientCapabilityResponse,
    ConsortiumMemberResponse,
    ConsortiumResponse,
    CreateConsortiumRequest,
    CreateMembershipProposalRequest,
    CreatePolicyProposalRequest,
    HandshakeRequest,
    HandshakeResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    NegotiatedResponse,
    NegotiateRequest,
    ProposalResponse,
    PruneRoundsResponse,
    QuorumStatusResponse,
    VoteProposalRequest,
    VoteProposalResponse,
)
from app.application.services.consortium_service import consortium_governance_service
from app.application.services.coordinator_service import coordinator_service
from app.domain.consortium_governance import (
    Consortium,
    MembershipProposal,
    ProposalAction,
    ProposalStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/coordinator", tags=["coordinator"])
api_router = APIRouter(prefix="/v1/coordinator", tags=["coordinator"])

CONSORTIUM_ADMIN_ROLES = {"admin", "compliance", "compliance_officer", "ml_lead", "ml_engineer", "consortium_admin"}
CONSORTIUM_POLICY_ROLES = {"admin", "compliance", "compliance_officer", "ml_lead", "consortium_admin"}
CONSORTIUM_VOTING_ROLES = {
    "admin",
    "compliance",
    "compliance_officer",
    "ml_lead",
    "ml_engineer",
    "analyst",
    "investigator",
    "consortium_admin",
}


def _authenticate_and_authorize_consortium_actor(
    authorization: str | None,
    allowed_roles: set[str],
    operation_name: str,
    min_clearance: int = 1,
) -> tuple[Any, dict[str, Any], str]:
    """Authenticate Bearer JWT and verify authoritative active identity, live RBAC, and clearance level."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header. Expected 'Bearer <token>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_parts = authorization.strip().split()
    if len(token_parts) != 2 or token_parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format. Expected 'Bearer <token>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_str = token_parts[1]
    from app.infrastructure.security.auth_service import AuthenticationService

    auth_svc = AuthenticationService.get_instance()
    is_valid, claims, err_detail = auth_svc.verify_access_token(token_str)
    if not is_valid or not claims:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=err_detail or "Authentication token is invalid or has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    authenticated_user = (claims.get("username") or claims.get("sub") or "").strip()
    authoritative_user = auth_svc.get_user(authenticated_user)
    if not authoritative_user or not authoritative_user.is_active or authoritative_user.status.lower() != "active":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Actor principal '{authenticated_user}' is not an active authoritative user.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    norm_allowed = {r.lower().strip() for r in allowed_roles}
    authoritative_roles = {r.lower().strip() for r in authoritative_user.roles}
    if not (authoritative_roles & norm_allowed):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Authenticated principal '{authoritative_user.username}' is not authorized to execute {operation_name}. "
                f"Requires one of: {sorted(norm_allowed)}."
            ),
        )

    user_clearance = getattr(authoritative_user, "clearance_level", 1)
    if user_clearance < min_clearance:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Authenticated principal '{authoritative_user.username}' has clearance level {user_clearance}, "
                f"which is insufficient for {operation_name} (minimum clearance level {min_clearance} required)."
            ),
        )

    return authoritative_user, claims, token_str


# ── Domain Mapping Helpers ────────────────────────────────────


def _proposal_to_response(prop: MembershipProposal) -> ProposalResponse:
    return ProposalResponse(
        proposal_id=prop.proposal_id,
        consortium_id=prop.consortium_id,
        creator_bank_id=prop.creator_bank_id,
        target_bank_id=prop.target_bank_id,
        action=prop.action.value if hasattr(prop.action, "value") else str(prop.action),
        required_quorum_ratio=prop.required_quorum_ratio,
        votes_for=sorted(prop.votes_for),
        votes_against=sorted(prop.votes_against),
        status=prop.status.value if hasattr(prop.status, "value") else str(prop.status),
        created_at=prop.created_at.isoformat() if hasattr(prop.created_at, "isoformat") else str(prop.created_at),
        expires_at=prop.expires_at.isoformat() if prop.expires_at and hasattr(prop.expires_at, "isoformat") else (str(prop.expires_at) if prop.expires_at else None),
        rejection_reason=prop.rejection_reason,
        metadata=prop.metadata,
    )


def _consortium_to_response(c: Consortium) -> ConsortiumResponse:
    return ConsortiumResponse(
        consortium_id=c.consortium_id,
        name=c.name,
        quorum_ratio=c.quorum_ratio,
        min_members_n=c.min_members_n,
        max_epsilon=c.max_epsilon,
        status=c.status.value if hasattr(c.status, "value") else str(c.status),
        members=[
            ConsortiumMemberResponse(
                bank_id=m.bank_id,
                role=m.role.value if hasattr(m.role, "value") else str(m.role),
                voting_power=m.voting_power,
                joined_at=m.joined_at.isoformat() if hasattr(m.joined_at, "isoformat") else str(m.joined_at),
                can_vote=m.can_vote,
            )
            for m in sorted(c.members.values(), key=lambda m: m.bank_id)
        ],
        created_at=c.created_at.isoformat() if hasattr(c.created_at, "isoformat") else str(c.created_at),
    )


# ── Client Registration & Heartbeat Endpoints ─────────────────


async def perform_handshake(req: HandshakeRequest) -> HandshakeResponse:
    """Register client capabilities dynamically to negotiate compatible execution params."""
    res = coordinator_service.register_client(
        bank_id=req.bank_id,
        pytorch_version=req.pytorch_version,
        python_version=req.python_version,
        hardware_type=req.hardware_type,
        ram_gb=req.ram_gb,
        device_count=req.device_count,
        bank_name=req.bank_name,
    )
    profile = res.get("client_profile")
    profile_dict: dict[str, Any] | None = None
    if profile is not None:
        if hasattr(profile, "__dict__"):
            profile_dict = dict(profile.__dict__)
        elif isinstance(profile, dict):
            profile_dict = profile

    return HandshakeResponse(
        registered=res["registered"],
        status=res["status"],
        reason=res.get("reason"),
        client_profile=profile_dict,
        registered_at=time.time(),
    )



async def post_heartbeat(req: HeartbeatRequest) -> HeartbeatResponse:
    """Post heartbeat check-in to remain in the active participant registry."""
    success = coordinator_service.record_heartbeat(req.bank_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bank {req.bank_id} is not registered. Perform handshake first.",
        )
    return HeartbeatResponse(
        success=True,
        status="ONLINE",
        timestamp=time.time(),
    )


async def list_registered_clients() -> list[ClientCapabilityResponse]:
    """Retrieve capability profiles of all dynamically registered banks."""
    if not coordinator_service.registry:
        coordinator_service.seed_consortium_nodes()
    _ = coordinator_service.get_active_clients()

    now = time.time()
    results: list[ClientCapabilityResponse] = []
    for client in coordinator_service.registry.values():
        results.append(
            ClientCapabilityResponse(
                bank_id=client.bank_id,
                bank_name=getattr(client, "bank_name", None),
                pytorch_version=client.pytorch_version,
                python_version=client.python_version,
                hardware_type=client.hardware_type,
                ram_gb=client.ram_gb,
                device_count=client.device_count,
                status=client.status,
                last_heartbeat_ago_seconds=round(max(0.0, now - client.last_heartbeat), 1),
            )
        )
    return sorted(results, key=lambda c: c.bank_id)


async def negotiate_training_params(
    bank_id: str = Query(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"),
    base_batch_size: int = Query(default=32, ge=1, le=4096),
    base_epochs: int = Query(default=5, ge=1, le=100),
) -> NegotiatedResponse:
    """Get training hyper-parameters customized for a bank client's runtime capability."""
    neg = coordinator_service.negotiate_parameters(bank_id, base_batch_size, base_epochs)
    return NegotiatedResponse(
        bank_id=bank_id,
        batch_size=neg.batch_size,
        local_epochs=neg.local_epochs,
        gradient_accumulation_steps=neg.gradient_accumulation_steps,
        use_cuda=neg.use_cuda,
        status=neg.status,
    )


async def negotiate_training_params_post(
    req: NegotiateRequest,
) -> NegotiatedResponse:
    """Negotiate training hyper-parameters customized for a bank client via JSON payload."""
    neg = coordinator_service.negotiate_parameters(
        req.bank_id, req.base_batch_size, req.base_epochs
    )
    return NegotiatedResponse(
        bank_id=req.bank_id,
        batch_size=neg.batch_size,
        local_epochs=neg.local_epochs,
        gradient_accumulation_steps=neg.gradient_accumulation_steps,
        use_cuda=neg.use_cuda,
        status=neg.status,
    )


async def submit_async_update(req: AsyncUpdateRequest) -> AsyncUpdateResponse:
    """Submit an asynchronous parameter update attenuated by staleness S(tau)."""
    import numpy as np

    numpy_weights: dict[str, np.ndarray] = {}
    for layer, vals in req.client_weights.items():
        arr = np.array(vals, dtype=np.float32)
        if req.layer_shapes and layer in req.layer_shapes:
            try:
                arr = arr.reshape(req.layer_shapes[layer])
            except ValueError as e:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"Shape mismatch reshaping layer '{layer}': {e}",
                ) from e
        numpy_weights[layer] = arr

    try:
        res = coordinator_service.submit_async_update(
            bank_id=req.bank_id,
            submitted_round=req.submitted_round,
            client_weights=numpy_weights,
            sample_count=req.sample_count,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    return AsyncUpdateResponse(**res)


async def get_async_engine_status() -> AsyncFLEngineStatusResponse:
    """Retrieve runtime staleness damping metrics and hyperparameters of the FedAsync engine."""
    metrics = coordinator_service.async_fl_engine.get_staleness_metrics()
    return AsyncFLEngineStatusResponse(**metrics)


async def get_dynamic_quorum_status(
    round_id: int | None = Query(default=None, ge=1),
) -> QuorumStatusResponse:
    """Inspect dynamic quorum threshold progress and countdown for active/specified training round."""
    q_status = coordinator_service.get_quorum_status(round_id)
    return QuorumStatusResponse(
        round_number=q_status.round_number,
        registered_nodes_count=q_status.registered_nodes_count,
        submitted_nodes_count=q_status.submitted_nodes_count,
        quorum_threshold_pct=q_status.quorum_threshold_pct,
        current_quorum_pct=q_status.current_quorum_pct,
        state=q_status.state.value if hasattr(q_status.state, "value") else str(q_status.state),
        start_time=q_status.start_time,
        target_window_seconds=q_status.target_window_seconds,
        time_remaining_seconds=q_status.time_remaining_seconds,
    )


async def prune_historical_rounds(
    keep_last: int = Query(default=50, ge=1, le=1000),
) -> PruneRoundsResponse:
    """Prune historical in-memory round states to prevent heap memory accumulation."""
    pruned = coordinator_service.prune_completed_rounds(keep_last=keep_last)
    return PruneRoundsResponse(pruned_rounds_count=pruned, keep_last=keep_last)


# ── Consortium Governance & Proposal Endpoints ────────────────


async def get_consortium_by_id(
    consortium_id: str = Path(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"),
) -> ConsortiumResponse:
    """Retrieve details, status and membership roster of a consortium alliance."""
    c = consortium_governance_service.get_consortium(consortium_id)
    if not c:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Consortium '{consortium_id}' does not exist.",
        )
    return _consortium_to_response(c)


async def create_new_consortium(
    req: CreateConsortiumRequest,
    authorization: str | None = Header(None, description="Bearer <access_token>"),
) -> ConsortiumResponse:
    """Establish a new multi-bank federated consortium alliance."""
    actor, claims, token_str = _authenticate_and_authorize_consortium_actor(
        authorization, CONSORTIUM_ADMIN_ROLES, "consortium creation", min_clearance=2
    )
    actor_bank = (getattr(actor, "bank_id", "") or "").strip()
    if not actor_bank:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Actor principal '{actor.username}' has no authoritative institution assignment.",
        )

    # TENANT-03: Reject forged founder_bank_id
    if req.founder_bank_id and req.founder_bank_id.lower().strip() != actor_bank.lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Cross-bank institutional impersonation rejected: Authenticated actor '{actor.username}' "
                f"from institution '{actor_bank}' cannot establish a consortium as founder '{req.founder_bank_id}'."
            ),
        )

    try:
        c = consortium_governance_service.create_consortium(
            consortium_id=req.consortium_id,
            name=req.name,
            founder_bank_id=actor_bank,
            quorum_ratio=req.quorum_ratio,
            max_epsilon=req.max_epsilon,
            min_members_n=req.min_members_n,
            auth_token=token_str,
            actor=actor.username,
        )
        return _consortium_to_response(c)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


async def list_consortium_members(
    consortium_id: str = Path(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"),
) -> list[ConsortiumMemberResponse]:
    """Retrieve full list of registered bank members for a consortium."""
    c = consortium_governance_service.get_consortium(consortium_id)
    if not c:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Consortium '{consortium_id}' does not exist.",
        )
    members = consortium_governance_service.list_members(consortium_id)
    return [
        ConsortiumMemberResponse(
            bank_id=m.bank_id,
            role=m.role.value if hasattr(m.role, "value") else str(m.role),
            voting_power=m.voting_power,
            joined_at=m.joined_at.isoformat() if hasattr(m.joined_at, "isoformat") else str(m.joined_at),
            can_vote=m.can_vote,
        )
        for m in sorted(members, key=lambda m: m.bank_id)
    ]


async def list_governance_proposals(
    consortium_id: str = Query(default="cfi-consortium", min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"),
    status_filter: str | None = Query(default=None, alias="status", pattern=r"^(PENDING|APPROVED|REJECTED|EXPIRED|CANCELLED)$"),
) -> list[ProposalResponse]:
    """List active and historical voting proposals for a consortium alliance."""
    st = ProposalStatus(status_filter) if status_filter else None
    props = consortium_governance_service.list_proposals(consortium_id, status=st)
    return [_proposal_to_response(p) for p in props]


async def get_governance_proposal_by_id(
    proposal_id: str = Path(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"),
) -> ProposalResponse:
    """Retrieve specific proposal details, vote counts and resolution state."""
    prop = consortium_governance_service.get_proposal(proposal_id)
    if not prop:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Proposal '{proposal_id}' does not exist.",
        )
    return _proposal_to_response(prop)


async def create_membership_proposal(
    req: CreateMembershipProposalRequest,
    authorization: str | None = Header(None, description="Bearer <access_token>"),
) -> ProposalResponse:
    """Open a member onboarding or removal voting proposal."""
    actor, claims, token_str = _authenticate_and_authorize_consortium_actor(
        authorization, CONSORTIUM_ADMIN_ROLES, "membership proposal creation", min_clearance=2
    )
    actor_bank = (getattr(actor, "bank_id", "") or "").strip()
    if not actor_bank:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Actor principal '{actor.username}' has no authoritative institution assignment.",
        )

    # TENANT-03: Reject forged creator_bank_id
    if req.creator_bank_id and req.creator_bank_id.lower().strip() != actor_bank.lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Cross-bank institutional impersonation rejected: Authenticated actor '{actor.username}' "
                f"from institution '{actor_bank}' cannot sponsor proposal as '{req.creator_bank_id}'."
            ),
        )

    c = consortium_governance_service.get_consortium(req.consortium_id)
    if not c:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Consortium '{req.consortium_id}' does not exist.",
        )

    clean_actor_bank = actor_bank.lower()
    # TENANT-05: Non-member or removed member cannot create proposals
    if clean_actor_bank not in c.members:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Institution '{actor_bank}' is not an active member of consortium '{req.consortium_id}'.",
        )

    member = c.members[clean_actor_bank]
    # TENANT-06: Member lacking voting/governance rights cannot create proposals
    if not member.can_vote:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Institution '{actor_bank}' has role '{member.role.value}' with no governance voting power in consortium '{req.consortium_id}'.",
        )

    try:
        action = ProposalAction(req.action)
        prop = consortium_governance_service.propose_membership_change(
            consortium_id=req.consortium_id,
            creator_bank_id=actor_bank,
            target_bank_id=req.target_bank_id,
            action=action,
            ttl_seconds=req.ttl_seconds,
            metadata=req.metadata,
            auth_token=token_str,
            actor=actor.username,
        )
        return _proposal_to_response(prop)
    except KeyError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


async def create_policy_proposal(
    req: CreatePolicyProposalRequest,
    authorization: str | None = Header(None, description="Bearer <access_token>"),
) -> ProposalResponse:
    """Open a policy modification voting proposal (e.g. quorum, epsilon)."""
    actor, claims, token_str = _authenticate_and_authorize_consortium_actor(
        authorization, CONSORTIUM_POLICY_ROLES, "policy proposal creation", min_clearance=2
    )
    actor_bank = (getattr(actor, "bank_id", "") or "").strip()
    if not actor_bank:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Actor principal '{actor.username}' has no authoritative institution assignment.",
        )

    # TENANT-03: Reject forged creator_bank_id
    if req.creator_bank_id and req.creator_bank_id.lower().strip() != actor_bank.lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Cross-bank institutional impersonation rejected: Authenticated actor '{actor.username}' "
                f"from institution '{actor_bank}' cannot sponsor policy proposal as '{req.creator_bank_id}'."
            ),
        )

    c = consortium_governance_service.get_consortium(req.consortium_id)
    if not c:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Consortium '{req.consortium_id}' does not exist.",
        )

    clean_actor_bank = actor_bank.lower()
    # TENANT-05: Non-member cannot create proposals
    if clean_actor_bank not in c.members:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Institution '{actor_bank}' is not an active member of consortium '{req.consortium_id}'.",
        )

    member = c.members[clean_actor_bank]
    # TENANT-06: Member lacking voting/governance rights cannot create policy proposals
    if not member.can_vote:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Institution '{actor_bank}' has role '{member.role.value}' with no governance voting power in consortium '{req.consortium_id}'.",
        )

    try:
        prop = consortium_governance_service.propose_policy_update(
            consortium_id=req.consortium_id,
            creator_bank_id=actor_bank,
            policy_updates=req.policy_updates,
            ttl_seconds=req.ttl_seconds,
            auth_token=token_str,
            actor=actor.username,
        )
        return _proposal_to_response(prop)
    except KeyError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


async def cast_governance_vote(
    req: VoteProposalRequest,
    proposal_id: str = Path(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"),
    authorization: str | None = Header(None, description="Bearer <access_token>"),
) -> VoteProposalResponse:
    """Cast a weighted vote FOR or AGAINST an active consortium proposal."""
    actor, claims, token_str = _authenticate_and_authorize_consortium_actor(
        authorization, CONSORTIUM_VOTING_ROLES, "consortium voting", min_clearance=1
    )
    actor_bank = (getattr(actor, "bank_id", "") or "").strip()
    if not actor_bank:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Actor principal '{actor.username}' has no authoritative institution assignment.",
        )

    # TENANT-04: Reject voting on behalf of another institution
    if req.bank_id and req.bank_id.lower().strip() != actor_bank.lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Cross-bank voting rejected: Authenticated actor '{actor.username}' from institution '{actor_bank}' "
                f"cannot cast a vote on behalf of institution '{req.bank_id}'."
            ),
        )

    prop = consortium_governance_service.get_proposal(proposal_id)
    if not prop:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Proposal '{proposal_id}' does not exist.",
        )

    c = consortium_governance_service.get_consortium(prop.consortium_id)
    if not c:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Consortium '{prop.consortium_id}' does not exist.",
        )

    clean_actor_bank = actor_bank.lower()
    # TENANT-05: Non-member or removed member cannot vote
    if clean_actor_bank not in c.members:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Institution '{actor_bank}' is not an active member of consortium '{prop.consortium_id}'.",
        )

    member = c.members[clean_actor_bank]
    if not member.can_vote:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Institution '{actor_bank}' has non-voting role '{member.role.value}' in consortium '{prop.consortium_id}'.",
        )

    try:
        resolved_prop = consortium_governance_service.cast_vote(
            proposal_id=proposal_id,
            bank_id=actor_bank,
            approve=req.approve,
            auth_token=token_str,
            actor=actor.username,
        )
        return VoteProposalResponse(
            proposal_id=resolved_prop.proposal_id,
            status=resolved_prop.status.value if hasattr(resolved_prop.status, "value") else str(resolved_prop.status),
            votes_for_count=len(resolved_prop.votes_for),
            votes_against_count=len(resolved_prop.votes_against),
            votes_for=sorted(resolved_prop.votes_for),
            votes_against=sorted(resolved_prop.votes_against),
            resolved=resolved_prop.status in (ProposalStatus.APPROVED, ProposalStatus.REJECTED),
        )
    except KeyError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


async def cancel_governance_proposal(
    proposal_id: str = Path(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"),
    creator_bank_id: str = Query(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"),
    authorization: str | None = Header(None, description="Bearer <access_token>"),
) -> ProposalResponse:
    """Cancel an open voting proposal by the original sponsoring institution."""
    actor, claims, token_str = _authenticate_and_authorize_consortium_actor(
        authorization, CONSORTIUM_ADMIN_ROLES, "proposal cancellation", min_clearance=2
    )
    actor_bank = (getattr(actor, "bank_id", "") or "").strip()
    if not actor_bank:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Actor principal '{actor.username}' has no authoritative institution assignment.",
        )

    # TENANT-03: Creator bank identifier must match authenticated institution
    if creator_bank_id and creator_bank_id.lower().strip() != actor_bank.lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Cross-bank cancellation rejected: Authenticated actor '{actor.username}' from institution '{actor_bank}' "
                f"cannot cancel proposal on behalf of sponsoring institution '{creator_bank_id}'."
            ),
        )

    prop = consortium_governance_service.get_proposal(proposal_id)
    if not prop:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Proposal '{proposal_id}' does not exist.",
        )

    # TENANT-06: Only the sponsoring bank can cancel
    if prop.creator_bank_id.lower().strip() != actor_bank.lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Unauthorized proposal cancellation: Only the original sponsoring institution "
                f"'{prop.creator_bank_id}' can withdraw proposal '{proposal_id}', not '{actor_bank}'."
            ),
        )

    try:
        cancelled = consortium_governance_service.cancel_proposal(
            proposal_id=proposal_id,
            creator_bank_id=actor_bank,
            auth_token=token_str,
            actor=actor.username,
        )
        return _proposal_to_response(cancelled)
    except KeyError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


# ── Route Binding to Router Variants ──────────────────────────

for r in (router, api_router):
    r.add_api_route("/handshake", perform_handshake, methods=["POST"], response_model=HandshakeResponse)
    r.add_api_route("/heartbeat", post_heartbeat, methods=["POST"], response_model=HeartbeatResponse)
    r.add_api_route("/clients", list_registered_clients, methods=["GET"], response_model=list[ClientCapabilityResponse])
    r.add_api_route("/negotiate", negotiate_training_params, methods=["GET"], response_model=NegotiatedResponse)
    r.add_api_route("/negotiate", negotiate_training_params_post, methods=["POST"], response_model=NegotiatedResponse)
    r.add_api_route("/async-update", submit_async_update, methods=["POST"], response_model=AsyncUpdateResponse)
    r.add_api_route("/async-status", get_async_engine_status, methods=["GET"], response_model=AsyncFLEngineStatusResponse)
    r.add_api_route("/quorum-status", get_dynamic_quorum_status, methods=["GET"], response_model=QuorumStatusResponse)
    r.add_api_route("/quorum", get_dynamic_quorum_status, methods=["GET"], response_model=QuorumStatusResponse)
    r.add_api_route("/rounds/prune", prune_historical_rounds, methods=["POST"], response_model=PruneRoundsResponse)

    # Governance routes
    r.add_api_route("/consortium/{consortium_id}", get_consortium_by_id, methods=["GET"], response_model=ConsortiumResponse)
    r.add_api_route("/consortium", create_new_consortium, methods=["POST"], response_model=ConsortiumResponse, status_code=status.HTTP_201_CREATED)
    r.add_api_route("/consortium/{consortium_id}/members", list_consortium_members, methods=["GET"], response_model=list[ConsortiumMemberResponse])
    r.add_api_route("/proposals", list_governance_proposals, methods=["GET"], response_model=list[ProposalResponse])
    r.add_api_route("/proposals/{proposal_id}", get_governance_proposal_by_id, methods=["GET"], response_model=ProposalResponse)
    r.add_api_route("/proposals/membership", create_membership_proposal, methods=["POST"], response_model=ProposalResponse, status_code=status.HTTP_201_CREATED)
    r.add_api_route("/proposals/policy", create_policy_proposal, methods=["POST"], response_model=ProposalResponse, status_code=status.HTTP_201_CREATED)
    r.add_api_route("/proposals/{proposal_id}/vote", cast_governance_vote, methods=["POST"], response_model=VoteProposalResponse)
    r.add_api_route("/proposals/{proposal_id}/cancel", cancel_governance_proposal, methods=["POST"], response_model=ProposalResponse)
