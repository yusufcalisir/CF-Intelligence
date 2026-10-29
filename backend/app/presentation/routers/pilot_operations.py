"""Pilot Operations API Router — Governed Two-Bank Pilot Lifecycle & Kill Switch.

Exposes REST APIs for controlled pilot operations:
- Multi-prefix: /api/v1/pilots and /v1/pilots
- Explicit durable lifecycle: DRAFT -> PREFLIGHT -> AWAITING_APPROVAL -> AUTHORIZED -> ACTIVE -> PAUSED -> COMPLETED
- Terminal outcomes: ABORTED, FAILED
- Independent bilateral and operator approval tracking
- Operational kill switch (PAUSE, RESUME, ABORT)
- Live telemetry and auditable dossier export
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status

from app.application.schemas.pilot_operations import (
    PilotApprovalItem,
    PilotApprovalRequest,
    PilotKillSwitchRequest,
    PilotPreflightRequest,
    PilotProvisionRequest,
    PilotStatusResponse,
    PilotTelemetrySummary,
    PreflightCheckItem,
)
from app.application.services.pilot_operations_service import PilotOperationsService
from app.domain.pilot_operations import (
    InvalidPilotTransitionError,
    PilotAuthorizationError,
    PilotEntity,
    PilotExpiredError,
    PilotGovernanceError,
    PilotQuotaExceededError,
    PilotStatus,
)
from app.infrastructure.security.principal_auth import (
    AuthenticatedPrincipal,
    resolve_authenticated_principal,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/pilots", tags=["Pilot Operations"])
api_router = APIRouter(prefix="/v1/pilots", tags=["Pilot Operations"])

__all__ = [
    "api_router",
    "router",
]


def _get_pilot_service() -> PilotOperationsService:
    return PilotOperationsService.get_instance()


def _resolve_principal_or_fail(request: Request) -> AuthenticatedPrincipal:
    principal, auth_err, auth_status = resolve_authenticated_principal(request)
    if auth_err:
        raise HTTPException(status_code=auth_status, detail=auth_err)
    if not principal.is_authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required for pilot operations.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


def _enforce_pilot_read_access(principal: AuthenticatedPrincipal, entity: PilotEntity) -> None:
    """Enforce institutional tenant isolation for pilot read access."""
    if principal.is_privileged:
        return
    caller_tenant = (principal.tenant_id or "").lower().strip()
    if caller_tenant in ("consortium", "operator", "admin", "system"):
        return
    participating = [b.lower().strip() for b in entity.config.participating_institutions]
    if caller_tenant not in participating:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Cross-tenant pilot access rejected: Principal institution '{caller_tenant}' "
                   f"is not a participant of pilot '{entity.config.pilot_id}'.",
        )


def _enforce_operator_role(principal: AuthenticatedPrincipal, action: str) -> None:
    """Require operator/admin authority for privileged pilot state transitions."""
    gov_roles = {"admin", "super_admin", "operator", "pilot_operator", "security_officer", "compliance", "system_admin"}
    roles = set(principal.roles or [])
    caller_tenant = (principal.tenant_id or "").lower().strip()
    is_op_tenant = caller_tenant in ("consortium", "operator", "admin", "system")
    if not principal.is_privileged and (not is_op_tenant or not (roles & gov_roles)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Principal '{principal.principal_id}' lacks operator authority to {action} pilot.",
        )


def _enforce_pause_authority(principal: AuthenticatedPrincipal, entity: PilotEntity) -> None:
    """Require emergency pause authority: operator OR participating bank compliance/security."""
    if principal.is_privileged:
        return
    caller_tenant = (principal.tenant_id or "").lower().strip()
    roles = set(principal.roles or [])
    operator_roles = {"admin", "super_admin", "operator", "pilot_operator", "security_officer", "compliance", "system_admin"}
    if caller_tenant in ("consortium", "operator", "admin", "system") and (roles & operator_roles):
        return
    participating = [b.lower().strip() for b in entity.config.participating_institutions]
    bank_pause_roles = {"admin", "compliance", "compliance_officer", "security_officer", "risk_lead"}
    if caller_tenant in participating and (roles & bank_pause_roles):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Principal '{principal.principal_id}' lacks emergency pause authority for pilot '{entity.config.pilot_id}'.",
    )


def _compute_permitted_actions(entity: PilotEntity, principal: AuthenticatedPrincipal | None) -> list[str]:
    """Derive permitted pilot actions strictly from current state, expiry, and principal authority."""
    if not principal or not principal.is_authenticated:
        return []

    actions: list[str] = []
    status = entity.status
    caller_tenant = (principal.tenant_id or "").lower().strip()
    roles = set(principal.roles or [])
    is_op_tenant = caller_tenant in ("consortium", "operator", "admin", "system")
    op_roles = {"admin", "super_admin", "operator", "pilot_operator", "security_officer", "compliance", "system_admin"}
    is_operator = principal.is_privileged or (is_op_tenant and bool(roles & op_roles))

    participating = [b.lower().strip() for b in entity.config.participating_institutions]
    is_participant = is_operator or (caller_tenant in participating)
    has_expired = entity.has_expired()

    # Preflight: participant or operator in pre-activation states
    if status in (PilotStatus.DRAFT, PilotStatus.PREFLIGHT, PilotStatus.AWAITING_APPROVAL) and is_participant:
        actions.append("preflight")

    # Approve: operator or participating bank in pre-activation states, not expired
    if status in (PilotStatus.DRAFT, PilotStatus.PREFLIGHT, PilotStatus.AWAITING_APPROVAL) and not has_expired and (is_operator or caller_tenant in participating):
        actions.append("approve")

    # Activate: operator only, in AUTHORIZED, not expired
    if status == PilotStatus.AUTHORIZED and not has_expired and is_operator:
        actions.append("activate")

    # Pause: emergency pause authority in ACTIVE
    if status == PilotStatus.ACTIVE:
        bank_pause_roles = {"admin", "compliance", "compliance_officer", "security_officer", "risk_lead"}
        can_pause = is_operator or (caller_tenant in participating and bool(roles & bank_pause_roles))
        if can_pause:
            actions.append("pause")

    # Resume: operator in PAUSED, not expired, quota not exceeded
    quota_exhausted = entity.queries_executed >= entity.config.query_quota_total
    if status == PilotStatus.PAUSED and not has_expired and not quota_exhausted and is_operator:
        actions.append("resume")

    # Abort: operator in any non-terminal state
    if status not in (PilotStatus.COMPLETED, PilotStatus.ABORTED, PilotStatus.FAILED) and is_operator:
        actions.append("abort")

    # Complete: operator in ACTIVE or PAUSED
    if status in (PilotStatus.ACTIVE, PilotStatus.PAUSED) and is_operator:
        actions.append("complete")

    # Export dossier: auditor / compliance / operator / admin
    dossier_roles = {"admin", "super_admin", "compliance", "compliance_officer", "auditor", "security_officer", "operator", "pilot_operator"}
    if principal.is_privileged or bool(roles & dossier_roles):
        actions.append("export_dossier")

    return actions


def _entity_to_response(entity: PilotEntity, principal: AuthenticatedPrincipal | None = None) -> PilotStatusResponse:
    complete, missing = entity.has_all_required_approvals()
    permitted_actions = _compute_permitted_actions(entity, principal)
    has_expired = entity.has_expired()

    return PilotStatusResponse(
        pilot_id=entity.config.pilot_id,
        revision=entity.config.revision,
        consortium_id=entity.config.consortium_id,
        status=entity.status.value,
        status_reason=entity.status_reason,
        scope_digest=entity.scope_digest,
        is_fully_approved=entity.is_fully_approved(),
        passed=all(c.passed for c in entity.preflight_checks) if entity.preflight_checks else (entity.status != PilotStatus.FAILED),
        queries_executed=entity.queries_executed,
        queries_denied=entity.queries_denied,
        queries_total_quota=entity.config.query_quota_total,
        participating_institutions=entity.config.participating_institutions,
        allowed_purposes=entity.config.allowed_purposes,
        allowed_jurisdictions=entity.config.allowed_jurisdictions,
        operational_grant_id=entity.config.operational_grant_id,
        starts_at=entity.config.starts_at,
        expires_at=entity.config.expires_at,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
        activated_at=entity.activated_at,
        terminated_at=entity.terminated_at,
        termination_reason=entity.termination_reason,
        preflight_checks=[
            PreflightCheckItem(
                check_name=c.check_name,
                passed=c.passed,
                details=c.details,
                checked_at=c.checked_at,
            )
            for c in entity.preflight_checks
        ],
        approvals={
            k: PilotApprovalItem(
                approver_id=v.approver_id,
                approver_institution=v.approver_institution,
                approver_role=v.approver_role,
                scope_digest=v.scope_digest,
                approved_at=v.approved_at,
                signature=v.signature,
                notes=v.notes,
            )
            for k, v in entity.approvals.items()
        },
        missing_approvals=missing,
        has_expired=has_expired,
        permitted_actions=permitted_actions,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=PilotStatusResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Provision a new two-bank controlled pilot in DRAFT state",
)
@router.post(
    "/",
    response_model=PilotStatusResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Provision a new two-bank controlled pilot in DRAFT state",
)
@api_router.post(
    "",
    response_model=PilotStatusResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Provision a new two-bank controlled pilot in DRAFT state",
)
@api_router.post(
    "/",
    response_model=PilotStatusResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Provision a new two-bank controlled pilot in DRAFT state",
)
@router.post(
    "/provision",
    response_model=PilotStatusResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Provision a new two-bank controlled pilot in DRAFT state",
)
@api_router.post(
    "/provision",
    response_model=PilotStatusResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Provision a new two-bank controlled pilot in DRAFT state",
)
async def provision_pilot(
    payload: PilotProvisionRequest,
    request: Request,
    service: PilotOperationsService = Depends(_get_pilot_service),
) -> PilotStatusResponse:
    """Provision a new two-bank controlled pilot with immutable configuration and scope digest."""
    principal = _resolve_principal_or_fail(request)
    permitted_roles = {"admin", "security_officer", "system_admin", "compliance"}
    if not (principal.is_privileged or any(r in permitted_roles for r in principal.roles)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Principal '{principal.principal_id}' lacks permitted role to provision pilots.",
        )
    try:
        entity = service.provision_pilot(req=payload, principal=principal)
        return _entity_to_response(entity, principal)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Failed to provision pilot: %s", exc, exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Provision failed: {exc}") from exc


@router.post(
    "/{pilot_id}/preflight",
    response_model=PilotStatusResponse,
    summary="Execute preflight readiness verification for a provisioned pilot",
)
@api_router.post(
    "/{pilot_id}/preflight",
    response_model=PilotStatusResponse,
    summary="Execute preflight readiness verification for a provisioned pilot",
)
async def run_preflight(
    request: Request,
    pilot_id: str = Path(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"),
    payload: PilotPreflightRequest | None = None,
    service: PilotOperationsService = Depends(_get_pilot_service),
) -> PilotStatusResponse:
    """Run preflight probes to verify consortium, institutional enrollment, keys, and quotas."""
    principal = _resolve_principal_or_fail(request)
    try:
        entity = service.run_preflight(pilot_id=pilot_id, principal=principal, preflight_req=payload)
        return _entity_to_response(entity, principal)
    except (InvalidPilotTransitionError, PilotGovernanceError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/{pilot_id}/approve",
    response_model=PilotStatusResponse,
    summary="Record independent institutional or operator approval",
)
@api_router.post(
    "/{pilot_id}/approve",
    response_model=PilotStatusResponse,
    summary="Record independent institutional or operator approval",
)
async def approve_pilot(
    payload: PilotApprovalRequest,
    request: Request,
    pilot_id: str = Path(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"),
    service: PilotOperationsService = Depends(_get_pilot_service),
) -> PilotStatusResponse:
    """Record independent approval for Bank A, Bank B, or OPERATOR against current scope digest."""
    principal = _resolve_principal_or_fail(request)
    try:
        entity = service.approve_pilot(
            pilot_id=pilot_id,
            approving_institution=payload.approving_institution,
            principal=principal,
            notes=payload.notes,
            signature=payload.signature,
        )
        return _entity_to_response(entity, principal)
    except (InvalidPilotTransitionError, PilotAuthorizationError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/{pilot_id}/activate",
    response_model=PilotStatusResponse,
    summary="Operator activation of an AUTHORIZED pilot",
)
@api_router.post(
    "/{pilot_id}/activate",
    response_model=PilotStatusResponse,
    summary="Operator activation of an AUTHORIZED pilot",
)
async def activate_pilot(
    request: Request,
    pilot_id: str = Path(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"),
    service: PilotOperationsService = Depends(_get_pilot_service),
) -> PilotStatusResponse:
    """Authorized operator activates the pilot to permit operational matching queries."""
    principal = _resolve_principal_or_fail(request)
    _enforce_operator_role(principal, "activate")
    try:
        entity = service.activate_pilot(pilot_id=pilot_id, principal=principal)
        return _entity_to_response(entity, principal)
    except (InvalidPilotTransitionError, PilotAuthorizationError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/{pilot_id}/pause",
    response_model=PilotStatusResponse,
    summary="Effective kill switch: pause an active pilot immediately",
)
@api_router.post(
    "/{pilot_id}/pause",
    response_model=PilotStatusResponse,
    summary="Effective kill switch: pause an active pilot immediately",
)
async def pause_pilot(
    payload: PilotKillSwitchRequest,
    request: Request,
    pilot_id: str = Path(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"),
    service: PilotOperationsService = Depends(_get_pilot_service),
) -> PilotStatusResponse:
    """Pause an active pilot immediately, denying all new matching queries."""
    principal = _resolve_principal_or_fail(request)
    entity = service.get_pilot(pilot_id)
    if not entity:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Pilot '{pilot_id}' not found.")
    _enforce_pause_authority(principal, entity)
    try:
        entity = service.pause_pilot(pilot_id=pilot_id, reason=payload.reason, principal=principal)
        return _entity_to_response(entity, principal)
    except (InvalidPilotTransitionError, PilotGovernanceError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/{pilot_id}/resume",
    response_model=PilotStatusResponse,
    summary="Resume a paused pilot after health revalidation",
)
@api_router.post(
    "/{pilot_id}/resume",
    response_model=PilotStatusResponse,
    summary="Resume a paused pilot after health revalidation",
)
async def resume_pilot(
    request: Request,
    pilot_id: str = Path(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"),
    service: PilotOperationsService = Depends(_get_pilot_service),
) -> PilotStatusResponse:
    """Resume a paused pilot after checking infrastructure health and unexpired authority."""
    principal = _resolve_principal_or_fail(request)
    _enforce_operator_role(principal, "resume")
    try:
        entity = service.resume_pilot(pilot_id=pilot_id, principal=principal)
        return _entity_to_response(entity, principal)
    except (InvalidPilotTransitionError, PilotAuthorizationError, PilotExpiredError, PilotQuotaExceededError, PilotGovernanceError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/{pilot_id}/abort",
    response_model=PilotStatusResponse,
    summary="Emergency kill switch: abort pilot permanently",
)
@api_router.post(
    "/{pilot_id}/abort",
    response_model=PilotStatusResponse,
    summary="Emergency kill switch: abort pilot permanently",
)
async def abort_pilot(
    payload: PilotKillSwitchRequest,
    request: Request,
    pilot_id: str = Path(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"),
    service: PilotOperationsService = Depends(_get_pilot_service),
) -> PilotStatusResponse:
    """Abort a pilot permanently, preserving complete audit history."""
    principal = _resolve_principal_or_fail(request)
    _enforce_operator_role(principal, "abort")
    try:
        entity = service.abort_pilot(pilot_id=pilot_id, reason=payload.reason, principal=principal)
        return _entity_to_response(entity, principal)
    except (InvalidPilotTransitionError, PilotGovernanceError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/{pilot_id}/complete",
    response_model=PilotStatusResponse,
    summary="Complete a pilot lifecycle normally",
)
@api_router.post(
    "/{pilot_id}/complete",
    response_model=PilotStatusResponse,
    summary="Complete a pilot lifecycle normally",
)
async def complete_pilot(
    request: Request,
    pilot_id: str = Path(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"),
    service: PilotOperationsService = Depends(_get_pilot_service),
) -> PilotStatusResponse:
    """Mark a pilot as successfully completed."""
    principal = _resolve_principal_or_fail(request)
    _enforce_operator_role(principal, "complete")
    try:
        entity = service.complete_pilot(pilot_id=pilot_id, principal=principal)
        return _entity_to_response(entity, principal)
    except (InvalidPilotTransitionError, PilotGovernanceError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get(
    "/{pilot_id}",
    response_model=PilotStatusResponse,
    summary="Get operational status and configuration of a pilot",
)
@api_router.get(
    "/{pilot_id}",
    response_model=PilotStatusResponse,
    summary="Get operational status and configuration of a pilot",
)
async def get_pilot_status(
    request: Request,
    pilot_id: str = Path(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"),
    service: PilotOperationsService = Depends(_get_pilot_service),
) -> PilotStatusResponse:
    """Retrieve operational status, approvals, and preflight results for a pilot."""
    principal = _resolve_principal_or_fail(request)
    entity = service.get_pilot(pilot_id)
    if not entity:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Pilot '{pilot_id}' not found.")
    _enforce_pilot_read_access(principal, entity)
    return _entity_to_response(entity, principal)


@router.get(
    "/{pilot_id}/telemetry",
    response_model=PilotTelemetrySummary,
    summary="Get live operational telemetry and query latencies for a pilot",
)
@api_router.get(
    "/{pilot_id}/telemetry",
    response_model=PilotTelemetrySummary,
    summary="Get live operational telemetry and query latencies for a pilot",
)
async def get_pilot_telemetry(
    request: Request,
    pilot_id: str = Path(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"),
    service: PilotOperationsService = Depends(_get_pilot_service),
) -> PilotTelemetrySummary:
    """Inspect query metrics, mean latency, p95 latency, and remaining quota."""
    principal = _resolve_principal_or_fail(request)
    entity = service.get_pilot(pilot_id)
    if not entity:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Pilot '{pilot_id}' not found.")
    _enforce_pilot_read_access(principal, entity)
    try:
        return service.get_telemetry_summary(pilot_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get(
    "/{pilot_id}/dossier",
    response_model=dict[str, Any],
    summary="Export durable, redacted completion and audit evidence dossier",
)
@api_router.get(
    "/{pilot_id}/dossier",
    response_model=dict[str, Any],
    summary="Export durable, redacted completion and audit evidence dossier",
)
async def export_pilot_dossier(
    request: Request,
    pilot_id: str = Path(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"),
    service: PilotOperationsService = Depends(_get_pilot_service),
) -> dict[str, Any]:
    """Export complete auditable dossier for independent reviewer inspection."""
    principal = _resolve_principal_or_fail(request)
    entity = service.get_pilot(pilot_id)
    if not entity:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Pilot '{pilot_id}' not found.")
    _enforce_pilot_read_access(principal, entity)
    dossier_roles = {"admin", "super_admin", "compliance", "compliance_officer", "auditor", "security_officer", "operator", "pilot_operator"}
    roles = set(principal.roles or [])
    if not principal.is_privileged and not (roles & dossier_roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Principal '{principal.principal_id}' lacks auditor or compliance role to export pilot dossier.",
        )
    try:
        return service.export_audit_dossier(pilot_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get(
    "",
    response_model=list[PilotStatusResponse],
    summary="List all pilots",
)
@api_router.get(
    "",
    response_model=list[PilotStatusResponse],
    summary="List all pilots",
)
async def list_pilots(
    request: Request,
    service: PilotOperationsService = Depends(_get_pilot_service),
) -> list[PilotStatusResponse]:
    """List all pilots in persistent storage with tenant-scoped filtering."""
    principal = _resolve_principal_or_fail(request)
    caller_tenant = (principal.tenant_id or "").lower().strip()
    is_op = principal.is_privileged or caller_tenant in ("consortium", "operator", "admin", "system")
    pilots = service.list_pilots()
    if not is_op:
        pilots = [p for p in pilots if caller_tenant in [b.lower().strip() for b in p.config.participating_institutions]]
    return [_entity_to_response(p, principal) for p in pilots]
