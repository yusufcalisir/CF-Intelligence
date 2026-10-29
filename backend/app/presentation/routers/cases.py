"""Case management API endpoints.

CRUD operations for investigation cases with status transitions,
notes, alert linking, evidence registry, Four-Eyes dual control,
and FinCEN SAR regulatory filings.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from app.application.schemas.cases import (
    CaseCreateRequest,
    CaseEligibleEntityResponse,
    CaseEscalateRequest,
    CaseEventResponse,
    CaseIntelligenceEvidenceResponse,
    CaseLinkAlertRequest,
    CaseNoteRequest,
    CaseNoteResponse,
    CaseResolveRequest,
    CaseResponse,
    CaseSignRequest,
    CaseStatusRequest,
    CaseSummaryResponse,
    CaseValidateTransitionRequest,
    EvidenceRequest,
    EvidenceResponse,
    ExportFinCENXmlRequest,
    ExportFinCENXmlResponse,
    InvestigatorAuditLogResponse,
    SessionDurationRequest,
    TimelineVerificationResponse,
)
from app.application.services.aml_agentic_copilot import AMLAgenticCopilot
from app.application.services.case_service import (
    AuditService,
    CaseManagementService,
    CaseNotFoundError,
    EvidenceRegistryService,
    _case_to_dict,
)
from app.application.services.idempotency import IdempotencyService
from app.dependencies import PrincipalDep, TenantDep, enforce_tenant_isolation
from app.domain.enums import CasePriority, CaseStatus
from app.domain.models.case import (
    DuplicateSupervisorSignatureError,
    FourEyesVerificationError,
    InvalidCaseTransitionError,
    SelfApprovalProhibitedError,
    TerminalCaseImmutableError,
    clean_identity,
)
from app.domain.value_objects_copilot import CopilotQueryRequest, CopilotQueryResponse

logger = logging.getLogger(__name__)

# Dual-routing support for both /api/v1/cases and /v1/cases prefixes
router = APIRouter(prefix="/api/v1/cases", tags=["cases"])
api_router = APIRouter(prefix="/v1/cases", tags=["cases"])

_case_service = CaseManagementService()
_evidence_service = EvidenceRegistryService()


def get_case_service() -> CaseManagementService:
    return _case_service


def get_evidence_service() -> EvidenceRegistryService:
    return _evidence_service


def _enforce_case_tenant(case: Any, caller_tenant: str | None) -> None:
    """Enforce tenant isolation on a case and all linked alerts."""
    if not caller_tenant:
        return
    case_bank = getattr(case, "bank_id", None)
    if case_bank:
        enforce_tenant_isolation(caller_tenant, case_bank)
    if getattr(case, "alert_ids", None):
        from app.presentation.routers.alerts import get_alert_service

        alert_svc = get_alert_service()
        for a_id in case.alert_ids:
            a = alert_svc.get_alert(a_id)
            if a and getattr(a, "bank_id", None):
                enforce_tenant_isolation(caller_tenant, a.bank_id)


@router.get("", response_model=list[CaseSummaryResponse])
@api_router.get("", response_model=list[CaseSummaryResponse])
async def list_cases(
    status: str | None = Query(None),
    priority: str | None = Query(None),
    alert_id: str | None = Query(None, description="Filter cases linking a specific alert"),
    limit: int = Query(50, ge=1, le=200),
    caller_tenant: TenantDep = None,
) -> list[CaseSummaryResponse]:
    """List investigation cases."""
    try:
        stat = CaseStatus(status) if status else None
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status value: {status!r}. "
            f"Valid values: {[e.value for e in CaseStatus]}",
        )
    try:
        pri = CasePriority(priority) if priority else None
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid priority value: {priority!r}. "
            f"Valid values: {[e.value for e in CasePriority]}",
        )

    cases = _case_service.get_cases(status=stat, priority=pri, alert_id=alert_id, limit=limit)
    if caller_tenant:
        cases = [
            c
            for c in cases
            if not getattr(c, "bank_id", None) or c.bank_id == caller_tenant
        ]
    return [
        CaseSummaryResponse(
            id=c.id,
            bank_id=getattr(c, "bank_id", None),
            title=c.title,
            status=c.status.value,
            priority=c.priority.value,
            assigned_to=c.assigned_to,
            alert_count=len(c.alert_ids),
            created_at=c.created_at.isoformat(),
            is_open=c.is_open,
        )
        for c in cases
    ]


@router.get("/audit/logs", response_model=list[InvestigatorAuditLogResponse])
@api_router.get("/audit/logs", response_model=list[InvestigatorAuditLogResponse])
async def get_audit_logs(
    limit: int = Query(100, ge=1, le=500),
) -> list[InvestigatorAuditLogResponse]:
    """Retrieve investigator activity logs."""
    audit_svc = AuditService()
    logs = audit_svc.get_logs(limit=limit)
    return [InvestigatorAuditLogResponse(**log) for log in logs]


@router.post("/audit/session", response_model=dict)
@api_router.post("/audit/session", response_model=dict)
async def log_session_duration(req: SessionDurationRequest) -> dict:
    """Log investigator session duration."""
    audit_svc = AuditService()
    log = audit_svc.log_action(
        investigator=req.investigator,
        action="session_duration",
        target_id="session",
        session_duration_sec=req.duration_seconds,
    )
    return {"status": "success", "log_id": log["id"]}


@router.post("", response_model=CaseResponse)
@api_router.post("", response_model=CaseResponse)
async def create_case(
    req: CaseCreateRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    caller_tenant: TenantDep = None,
) -> CaseResponse | JSONResponse:
    """Create a new investigation case.

    Supports idempotent creation via the ``Idempotency-Key`` request header.
    If a request with the same key was successfully processed within 24 hours,
    the original response is returned without creating a duplicate case.
    """
    if caller_tenant and req.bank_id:
        enforce_tenant_isolation(caller_tenant, req.bank_id)
    tenant_id = caller_tenant or getattr(req, "bank_id", None)

    if req.alert_ids:
        from app.presentation.routers.alerts import get_alert_service

        alert_svc = get_alert_service()
        for a_id in req.alert_ids:
            a = alert_svc.get_alert(a_id)
            if a:
                a_bank = getattr(a, "bank_id", None)
                if a_bank:
                    if caller_tenant:
                        enforce_tenant_isolation(caller_tenant, a_bank)
                    if tenant_id:
                        enforce_tenant_isolation(tenant_id, a_bank)
                    else:
                        tenant_id = a_bank

    payload_hash = IdempotencyService.canonical_payload_hash(req)
    idem = IdempotencyService.get()
    status_or_hit, cached = idem.acquire(
        idempotency_key, tenant_id=tenant_id, payload_hash=payload_hash
    )
    if status_or_hit == "HIT":
        return JSONResponse(
            content=cached,
            status_code=200,
            headers={"Idempotency-Replayed": "true"},
        )
    if status_or_hit == "MISMATCH":
        return JSONResponse(
            content={"detail": "Conflicting payload under existing Idempotency-Key."},
            status_code=409,
        )
    if status_or_hit == "IN_PROGRESS":
        return JSONResponse(
            content={"detail": "A request with this Idempotency-Key is currently being processed."},
            status_code=409,
            headers={"Retry-After": "2"},
        )

    try:
        try:
            priority = CasePriority(req.priority)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid priority value: {req.priority!r}. "
                f"Valid values: {[e.value for e in CasePriority]}",
            )
        case = _case_service.create_case(
            title=req.title,
            priority=priority,
            alert_ids=req.alert_ids,
            total_risk_score=req.total_risk_score,
            assigned_to=req.assigned_to,
            bank_id=tenant_id,
        )
        result = _serialize_case(case)
        idem.complete(
            idempotency_key, result.model_dump(), tenant_id=tenant_id, payload_hash=payload_hash
        )
        return result
    except Exception:
        idem.release(idempotency_key, tenant_id=tenant_id)
        raise


@router.get("/{case_id}", response_model=CaseResponse)
@api_router.get("/{case_id}", response_model=CaseResponse)
async def get_case(
    case_id: str, actor: str = Query("analyst"), caller_tenant: TenantDep = None
) -> CaseResponse:
    """Get case detail with tenant isolation check."""
    case = _case_service.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail=f"Case not found: {case_id}")

    _enforce_case_tenant(case, caller_tenant)

    AuditService().log_action(actor, "access_case", case_id)
    return _serialize_case(case)


@router.patch("/{case_id}", response_model=CaseResponse)
@api_router.patch("/{case_id}", response_model=CaseResponse)
@router.put("/{case_id}/status", response_model=CaseResponse)
@api_router.put("/{case_id}/status", response_model=CaseResponse)
async def update_case_status(
    case_id: str,
    req: CaseStatusRequest,
    caller_tenant: TenantDep = None,
) -> CaseResponse:
    """Update case status with transition validation, optimistic concurrency, and dual-control signoff."""
    case = _case_service.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail=f"Case not found: {case_id}")
    _enforce_case_tenant(case, caller_tenant)

    try:
        new_status = CaseStatus(req.status)
        expected_status = CaseStatus(req.expected_status) if req.expected_status else None
        case = _case_service.change_status(
            case_id,
            new_status,
            actor=req.actor,
            supervisor_signature=req.supervisor_signature,
            second_supervisor_signature=req.second_supervisor_signature,
            supervisor_signatures=req.supervisor_signatures,
            expected_status=expected_status,
            expected_version=req.expected_version,
            expected_timeline_hash=req.expected_timeline_hash,
        )
        return _serialize_case(case)
    except CaseNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except SelfApprovalProhibitedError as e:
        if "assigned investigator" in str(e).lower():
            raise HTTPException(status_code=403, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except InvalidCaseTransitionError as e:
        if "precondition failed" in str(e).lower():
            raise HTTPException(status_code=409, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except (
        DuplicateSupervisorSignatureError,
        FourEyesVerificationError,
        TerminalCaseImmutableError,
    ) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{case_id}/escalate", response_model=CaseResponse)
@api_router.post("/{case_id}/escalate", response_model=CaseResponse)
async def escalate_case(
    case_id: str, req: CaseEscalateRequest, caller_tenant: TenantDep = None
) -> CaseResponse:
    """Escalate a case to PENDING_REVIEW for Four-Eyes supervisor evaluation."""
    case = _case_service.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail=f"Case not found: {case_id}")
    _enforce_case_tenant(case, caller_tenant)
    try:
        _case_service.add_note(case_id, author=req.actor, content=f"Escalation justification: {req.reason}")
        case = _case_service.change_status(case_id, CaseStatus.PENDING_REVIEW, actor=req.actor)
        return _serialize_case(case)
    except CaseNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (TerminalCaseImmutableError, InvalidCaseTransitionError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{case_id}/sign", response_model=CaseResponse)
@api_router.post("/{case_id}/sign", response_model=CaseResponse)
async def sign_case(
    case_id: str, req: CaseSignRequest, caller_tenant: TenantDep = None
) -> CaseResponse:
    """Record a supervisor dual-control signature on a case under review."""
    try:
        case = _case_service.get_case(case_id)
        if not case:
            raise HTTPException(status_code=404, detail=f"Case not found: {case_id}")
        _enforce_case_tenant(case, caller_tenant)

        # Optimistic preconditions check
        if req.expected_status:
            exp_st = req.expected_status.lower()
            cur_st = case.status.value.lower()
            if cur_st != exp_st:
                raise HTTPException(
                    status_code=409,
                    detail=f"Precondition failed: Expected status '{exp_st}', got '{cur_st}' (Lost Update Prevention).",
                )
        if req.expected_version is not None and getattr(case, "version", 1) != req.expected_version:
            raise HTTPException(
                status_code=409,
                detail=f"Precondition failed: Expected case version {req.expected_version}, but current version is {getattr(case, 'version', 1)} (Stale Approval Invariant).",
            )
        if req.expected_timeline_hash is not None and case.timeline_hash != req.expected_timeline_hash:
            raise HTTPException(
                status_code=409,
                detail=f"Precondition failed: Expected timeline hash '{req.expected_timeline_hash}', but current timeline hash is '{case.timeline_hash}' (Stale Approval Invariant).",
            )

        if not case.is_open:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid operation: Case '{case_id}' is finalized in terminal state '{case.status.value}' and cannot receive additional signatures.",
            )

        # Invariant: Assigned investigator cannot self-approve / sign as supervisor
        clean_sup = clean_identity(req.supervisor_id)
        if case.assigned_to and clean_sup == clean_identity(case.assigned_to):
            raise HTTPException(
                status_code=403,
                detail=f"Self-approval prohibited: Assigned investigator '{case.assigned_to}' "
                f"cannot sign as supervisor on their own case under Four-Eyes dual control governance.",
            )

        if req.action == "REJECT":
            case = _case_service.change_status(
                case_id,
                CaseStatus.INVESTIGATING,
                actor=req.supervisor_id,
                expected_version=req.expected_version,
                expected_timeline_hash=req.expected_timeline_hash,
            )
            _case_service.add_note(
                case_id,
                author=req.supervisor_id,
                content=f"Supervisor rejection: {req.notes or 'No reason specified'}",
            )
            return _serialize_case(case)

        sig = f"supervisor:{clean_sup}"
        existing_sigs = list(getattr(case, "supervisor_signatures", []) or [])
        clean_existing = [clean_identity(s) for s in existing_sigs]
        if clean_sup in clean_existing:
            raise HTTPException(status_code=400, detail="Supervisor has already signed this case.")

        old_version = getattr(case, "version", 1)
        sig_data = {
            "supervisor_id": req.supervisor_id,
            "signed_version": old_version,
            "signed_timeline_hash": case.timeline_hash,
            "signed_at": datetime.now(UTC).isoformat(),
            "notes": req.notes,
        }
        if not hasattr(case, "signature_metadata") or case.signature_metadata is None:
            case.signature_metadata = {}
        case.signature_metadata[sig] = sig_data

        existing_sigs.append(sig)
        case.supervisor_signatures = existing_sigs
        _case_service._add_event(
            case,
            "supervisor_signed",
            f"Supervisor signature recorded by {req.supervisor_id}. Total signatures: {len(existing_sigs)}",
            req.supervisor_id,
            {
                "supervisor_id": req.supervisor_id,
                "notes": req.notes,
                "signed_version": old_version,
                "signed_timeline_hash": sig_data["signed_timeline_hash"],
            },
        )
        success = _case_service._cases.update_conditional(
            case.id,
            _case_to_dict(case),
            expected_version=old_version,
        )
        if not success:
            raise HTTPException(
                status_code=409,
                detail=f"Precondition failed: Case '{case_id}' was concurrently modified while recording supervisor signature.",
            )
        return _serialize_case(case)
    except CaseNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{case_id}/resolve", response_model=CaseResponse)
@api_router.post("/{case_id}/resolve", response_model=CaseResponse)
async def resolve_case(
    case_id: str, req: CaseResolveRequest, caller_tenant: TenantDep = None
) -> CaseResponse:
    """Resolve and close a case under strict Four-Eyes dual control."""
    case = _case_service.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail=f"Case not found: {case_id}")
    _enforce_case_tenant(case, caller_tenant)

    # Optimistic preconditions check
    if req.expected_status:
        exp_st = req.expected_status.lower()
        cur_st = case.status.value.lower()
        if cur_st != exp_st:
            raise HTTPException(
                status_code=409,
                detail=f"Precondition failed: Expected status '{exp_st}', got '{cur_st}' (Lost Update Prevention).",
            )
    if req.expected_version is not None and getattr(case, "version", 1) != req.expected_version:
        raise HTTPException(
            status_code=409,
            detail=f"Precondition failed: Expected case version {req.expected_version}, but current version is {getattr(case, 'version', 1)} (Stale Approval Invariant).",
        )
    if req.expected_timeline_hash is not None and case.timeline_hash != req.expected_timeline_hash:
        raise HTTPException(
            status_code=409,
            detail=f"Precondition failed: Expected timeline hash '{req.expected_timeline_hash}', but current timeline hash is '{case.timeline_hash}' (Stale Approval Invariant).",
        )

    # Check that supervisors are distinct identities
    if clean_identity(req.primary_supervisor) == clean_identity(req.secondary_supervisor):
        raise HTTPException(
            status_code=400,
            detail="Primary and secondary supervisors must be distinct individuals (Four-Eyes dual control).",
        )

    # Check separation of duties: supervisor signatures must not match analyst actor
    analyst_clean = clean_identity(req.actor)
    for s_id in (req.primary_supervisor, req.secondary_supervisor):
        if clean_identity(s_id) == analyst_clean:
            raise HTTPException(
                status_code=400,
                detail="Supervisor signature must be different from the analyst actor (Four-Eyes Principle).",
            )

    # Check separation of duties: supervisor signatures must not match assigned investigator (ApproverID != InvestigatorID)
    if case.assigned_to:
        assigned_clean = clean_identity(case.assigned_to)
        for s_id in (req.primary_supervisor, req.secondary_supervisor):
            if clean_identity(s_id) == assigned_clean:
                raise HTTPException(
                    status_code=403,
                    detail=f"Self-approval prohibited: Assigned investigator '{case.assigned_to}' "
                    f"cannot approve their own case (Four-Eyes Principle: ApproverID != InvestigatorID).",
                )

    # Check staleness of any prior supervisor signatures under Four-Eyes material dossier governance
    from app.application.services.case_service import is_signature_stale

    for s_id in (req.primary_supervisor, req.secondary_supervisor):
        sig = f"supervisor:{clean_identity(s_id)}"
        stale, reason = is_signature_stale(case, sig)
        if stale:
            raise HTTPException(
                status_code=409,
                detail=f"Precondition failed: Prior approval by supervisor '{s_id}' is stale: {reason}. "
                f"The case dossier was modified after review and must be re-evaluated under Four-Eyes governance.",
            )

    try:
        target_status = (
            CaseStatus.CLOSED_CONFIRMED
            if req.resolution == "CONFIRMED_FRAUD"
            else CaseStatus.CLOSED_FALSE_POSITIVE
        )
        case = _case_service.change_status(
            case_id,
            target_status,
            actor=req.actor,
            supervisor_signature=f"supervisor:{req.primary_supervisor}",
            second_supervisor_signature=f"supervisor:{req.secondary_supervisor}",
            expected_status=req.expected_status,
            expected_version=req.expected_version,
            expected_timeline_hash=req.expected_timeline_hash,
            resolution_notes=req.notes,
            resolution_notes_author=f"supervisor:{req.primary_supervisor}",
        )
        return _serialize_case(case)
    except CaseNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except SelfApprovalProhibitedError as e:
        if "assigned investigator" in str(e).lower():
            raise HTTPException(status_code=403, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except (
        DuplicateSupervisorSignatureError,
        FourEyesVerificationError,
        InvalidCaseTransitionError,
        TerminalCaseImmutableError,
    ) as e:
        if "precondition failed" in str(e).lower():
            raise HTTPException(status_code=409, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{case_id}/validate-transition", response_model=dict)
@api_router.post("/{case_id}/validate-transition", response_model=dict)
async def validate_case_transition(
    case_id: str,
    req: CaseValidateTransitionRequest | None = None,
    target_status: str | None = Query(None),
    actor: str = Query("analyst"),
    supervisor_signatures: list[str] | None = Query(None),
    caller_tenant: TenantDep = None,
) -> dict[str, Any]:
    """Pre-validate a proposed status transition and Four-Eyes authorization requirements."""
    case = _case_service.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail=f"Case not found: {case_id}")
    _enforce_case_tenant(case, caller_tenant)

    resolved_target = (req.target_status or req.target_state if req else None) or target_status
    if not resolved_target:
        raise HTTPException(
            status_code=422,
            detail="target_status or target_state must be provided either in request body or as query parameter.",
        )
    resolved_actor = (req.actor_id or req.actor if req else None) or actor
    resolved_sigs = (req.supervisor_signatures if req and req.supervisor_signatures else None) or supervisor_signatures

    try:
        new_status = CaseStatus(resolved_target.lower())
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status value: {resolved_target!r}. Valid values: {[e.value for e in CaseStatus]}",
        )
    return _case_service.validate_transition(
        case_id=case_id,
        new_status=new_status,
        actor=resolved_actor,
        supervisor_signatures=resolved_sigs,
    )


@router.get("/{case_id}/timeline/verify", response_model=TimelineVerificationResponse)
@api_router.get("/{case_id}/timeline/verify", response_model=TimelineVerificationResponse)
async def verify_case_timeline(
    case_id: str, caller_tenant: TenantDep = None
) -> TimelineVerificationResponse:
    """Verify cryptographic SHA-256 parent hash chain of the case investigation timeline."""
    case = _case_service.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail=f"Case not found: {case_id}")
    _enforce_case_tenant(case, caller_tenant)

    try:
        result = _case_service.verify_timeline_integrity(case_id)
        return TimelineVerificationResponse(
            case_id=case_id,
            is_valid=result["is_valid"],
            event_count=result["event_count"],
            corrupted_index=result["corrupted_index"],
            chain_hashes=result["chain_hashes"],
            message=result["message"],
        )
    except CaseNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{case_id}/notes", response_model=CaseNoteResponse)
@api_router.post("/{case_id}/notes", response_model=CaseNoteResponse)
async def add_note(
    case_id: str, req: CaseNoteRequest, caller_tenant: TenantDep = None
) -> CaseNoteResponse:
    """Add an investigation note."""
    case = _case_service.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail=f"Case not found: {case_id}")
    _enforce_case_tenant(case, caller_tenant)

    try:
        note = _case_service.add_note(case_id, author=req.author, content=req.content)
        return CaseNoteResponse(
            id=note.id,
            case_id=note.case_id,
            author=note.author,
            content=note.content,
            created_at=note.created_at.isoformat(),
        )
    except CaseNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{case_id}/alerts", response_model=CaseResponse)
@api_router.post("/{case_id}/alerts", response_model=CaseResponse)
async def link_alert(
    case_id: str, req: CaseLinkAlertRequest, caller_tenant: TenantDep = None
) -> CaseResponse:
    """Link an alert to a case."""
    case = _case_service.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail=f"Case not found: {case_id}")
    _enforce_case_tenant(case, caller_tenant)

    from app.presentation.routers.alerts import get_alert_service

    alert_svc = get_alert_service()
    alert = alert_svc.get_alert(req.alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert not found: {req.alert_id}")

    alert_bank = getattr(alert, "bank_id", None)
    if alert_bank:
        if caller_tenant:
            enforce_tenant_isolation(caller_tenant, alert_bank)
        if getattr(case, "bank_id", None):
            enforce_tenant_isolation(case.bank_id, alert_bank)

    try:
        case = _case_service.link_alert(case_id, req.alert_id, bank_id=alert_bank)
        return _serialize_case(case)
    except TerminalCaseImmutableError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except CaseNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        if "cross-tenant" in str(e).lower():
            raise HTTPException(status_code=403, detail=str(e))
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{case_id}/timeline", response_model=list[CaseEventResponse])
@api_router.get("/{case_id}/timeline", response_model=list[CaseEventResponse])
async def get_timeline(
    case_id: str, caller_tenant: TenantDep = None
) -> list[CaseEventResponse]:
    """Get investigation timeline."""
    case = _case_service.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail=f"Case not found: {case_id}")
    _enforce_case_tenant(case, caller_tenant)

    try:
        events = _case_service.get_timeline(case_id)
        return [
            CaseEventResponse(
                event_type=e.event_type,
                description=e.description,
                actor=e.actor,
                timestamp=e.timestamp.isoformat(),
                metadata=e.metadata,
            )
            for e in events
        ]
    except CaseNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{case_id}/export")
@api_router.get("/{case_id}/export")
async def export_case(
    case_id: str, caller_tenant: TenantDep = None
) -> dict:
    """Export investigation summary as markdown."""
    case = _case_service.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail=f"Case not found: {case_id}")
    _enforce_case_tenant(case, caller_tenant)

    try:
        summary = _case_service.export_summary(case_id)
        return {"case_id": case_id, "format": "markdown", "content": summary}
    except CaseNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{case_id}/sar-report")
@api_router.get("/{case_id}/sar-report")
async def download_sar_report(
    case_id: str, caller_tenant: TenantDep = None
) -> FileResponse:
    """Download generated FinCEN SAR XML report for the case."""
    import os

    case = _case_service.get_case(case_id)
    if not case:
        raise HTTPException(
            status_code=404,
            detail=f"Case {case_id} not found.",
        )
    _enforce_case_tenant(case, caller_tenant)

    report_dir = "storage/regulatory_filings"
    report_path = os.path.join(report_dir, f"sar_{case_id}.xml").replace("\\", "/")

    if not os.path.exists(report_path):
        from app.application.services.alert_service import AlertIntelligenceService
        from app.application.services.regulatory_reporter import RegulatoryReporterService

        alert_service = AlertIntelligenceService()
        alerts = [a for aid in case.alert_ids if (a := alert_service.get_alert(aid)) is not None]

        os.makedirs(report_dir, exist_ok=True)
        xml_content = RegulatoryReporterService.generate_fincen_sar_xml(case, alerts)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(xml_content)

    return FileResponse(
        path=report_path,
        media_type="application/xml",
        filename=f"sar_report_{case_id[:8]}.xml",
    )


@router.post("/{case_id}/evidence", response_model=EvidenceResponse)
@api_router.post("/{case_id}/evidence", response_model=EvidenceResponse)
async def register_evidence(
    case_id: str, req: EvidenceRequest, caller_tenant: TenantDep = None
) -> EvidenceResponse:
    """Register new case evidence with SHA-256 hash verification."""
    case = _case_service.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail=f"Case not found: {case_id}")
    _enforce_case_tenant(case, caller_tenant)

    try:
        registry = EvidenceRegistryService()
        ev = registry.register_evidence(
            case_id=case_id,
            evidence_type=req.evidence_type,
            title=req.title,
            file_path=req.file_path,
            content=req.content,
            uploaded_by=req.uploaded_by,
        )
        return EvidenceResponse(**ev)
    except CaseNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{case_id}/evidence", response_model=list[EvidenceResponse])
@api_router.get("/{case_id}/evidence", response_model=list[EvidenceResponse])
async def get_case_evidence(
    case_id: str, caller_tenant: TenantDep = None
) -> list[EvidenceResponse]:
    """Retrieve all evidence registered for a case."""
    case = _case_service.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail=f"Case not found: {case_id}")
    _enforce_case_tenant(case, caller_tenant)
    registry = EvidenceRegistryService()
    ev_list = registry.get_case_evidence(case_id)
    return [EvidenceResponse(**ev) for ev in ev_list]


@router.post(
    "/{case_id}/evidence/cross-bank-intelligence",
    response_model=dict[str, Any],
    status_code=200,
)
@api_router.post(
    "/{case_id}/evidence/cross-bank-intelligence",
    response_model=dict[str, Any],
    status_code=200,
)
async def attach_cross_bank_intelligence_to_case_subresource(
    case_id: str,
    payload: dict[str, Any],
    principal: PrincipalDep,
) -> dict[str, Any]:
    """Attach authoritative cross-bank intelligence signal into case evidence ledger."""
    from app.domain.consortium_governance import ConsortiumSecurityError
    from app.presentation.routers.cross_bank_intelligence import get_cross_bank_intelligence_service

    svc = get_cross_bank_intelligence_service()
    raw_signal_id = payload.get("signal_id")
    if not raw_signal_id and isinstance(payload.get("signal"), dict):
        raw_signal_id = payload["signal"].get("signal_id")

    if not raw_signal_id or not isinstance(raw_signal_id, str):
        raise HTTPException(
            status_code=400,
            detail="Missing required 'signal_id' in attachment payload. Direct client signal injection is prohibited.",
        )

    notes = payload.get("notes") or payload.get("analyst_notes")
    idempotency_key = payload.get("idempotency_key")

    try:
        resp = svc.attach_signal_to_case_evidence(
            case_id=case_id,
            signal_id=raw_signal_id,
            principal=principal,
            notes=notes,
            idempotency_key=idempotency_key,
        )
        return resp.model_dump()
    except CaseNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except TerminalCaseImmutableError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    except ConsortiumSecurityError as err:
        raise HTTPException(status_code=403, detail=str(err)) from err
    except PermissionError as err:
        raise HTTPException(status_code=403, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


ALLOWED_INVESTIGATION_ROLES = {
    "investigator",
    "compliance_officer",
    "compliance_auditor",
    "analyst",
    "risk_officer",
    "supervisor",
    "fraud_analyst",
    "bank_admin",
    "admin",
    "super_admin",
    "cross_bank_investigator",
}


def _verify_investigator_authorization(principal: PrincipalDep, case: Any, caller_tenant: str | None) -> str:
    """Enforce mandatory institutional authentication, role clearance, and strict case ownership."""
    if not principal.is_authenticated or principal.principal_id == "anonymous":
        raise HTTPException(
            status_code=401,
            detail="Institutional authentication required. Bearer token or authorized workload identity must be provided.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    clean_roles = {r.lower().strip() for r in (principal.roles or [])}
    if not any(r in ALLOWED_INVESTIGATION_ROLES for r in clean_roles):
        raise HTTPException(
            status_code=403,
            detail="Investigator or compliance clearance required to access case intelligence workspace.",
        )

    case_bank = (getattr(case, "bank_id", None) or "").lower().strip()
    eff_tenant = (principal.tenant_id or caller_tenant or "").lower().strip()
    if case_bank and eff_tenant and eff_tenant != case_bank:
        raise HTTPException(
            status_code=403,
            detail=f"Broken Access Control Prevention: Tenant '{eff_tenant}' is not authorized to access resources belonging to '{case_bank}'.",
        )
    return case_bank or eff_tenant


@router.get("/{case_id}/eligible-entities", response_model=list[CaseEligibleEntityResponse])
@api_router.get("/{case_id}/eligible-entities", response_model=list[CaseEligibleEntityResponse])
async def get_case_eligible_entities(
    case_id: str,
    principal: PrincipalDep,
    caller_tenant: TenantDep = None,
) -> list[CaseEligibleEntityResponse]:
    """Retrieve legitimate institution-owned entities eligible for cross-bank investigation on this case."""
    case = _case_service.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail=f"Case not found: {case_id}")

    case_bank = _verify_investigator_authorization(principal, case, caller_tenant)

    from app.application.services.entity_resolution import EntityResolutionService
    from app.presentation.routers.alerts import get_alert_service

    alert_svc = get_alert_service()
    entity_svc = EntityResolutionService()

    results: list[CaseEligibleEntityResponse] = []
    seen_entity_ids: set[str] = set()

    # Authoritative entity derivation strictly from case-linked alerts
    if getattr(case, "alert_ids", None):
        for a_id in case.alert_ids:
            alert = alert_svc.get_alert(a_id)
            if not alert or not getattr(alert, "involved_entity_ids", None):
                continue
            for eid in alert.involved_entity_ids:
                if eid in seen_entity_ids:
                    continue
                ent = entity_svc.get_entity(eid)
                if ent:
                    # Enforce local institution ownership
                    if case_bank and ent.bank_id.lower().strip() != case_bank:
                        continue
                    seen_entity_ids.add(eid)
                    results.append(
                        CaseEligibleEntityResponse(
                            entity_id=ent.id,
                            display_label=ent.display_label,
                            entity_type=ent.entity_type.value,
                            risk_level=ent.risk_level.value,
                            bank_id=ent.bank_id,
                            source_alert_id=a_id,
                        )
                    )

    return results


@router.get("/{case_id}/intelligence-evidence", response_model=list[CaseIntelligenceEvidenceResponse])
@api_router.get("/{case_id}/intelligence-evidence", response_model=list[CaseIntelligenceEvidenceResponse])
async def get_case_intelligence_evidence(
    case_id: str,
    principal: PrincipalDep,
    caller_tenant: TenantDep = None,
) -> list[CaseIntelligenceEvidenceResponse]:
    """Retrieve verified cross-bank intelligence evidence items for an investigation case."""
    case = _case_service.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail=f"Case not found: {case_id}")

    _verify_investigator_authorization(principal, case, caller_tenant)

    import json
    import re

    from app.application.services.case_service import EvidencePersistenceError
    from app.application.services.consortium_service import ConsortiumGovernanceService
    from app.application.services.cross_bank_intelligence import CrossBankIntelligenceService
    from app.infrastructure.storage.durable_batch_store import DurableCrossBankBatchStore

    batch_store = DurableCrossBankBatchStore.get_instance()
    intel_svc = CrossBankIntelligenceService.get_instance()
    consortium_svc = ConsortiumGovernanceService()

    all_evidence = _evidence_service.get_case_evidence(case_id)
    projected: list[CaseIntelligenceEvidenceResponse] = []

    for ev in all_evidence:
        if ev.get("evidence_type") != "CROSS_BANK_INTELLIGENCE":
            continue

        ev_id = ev["id"]
        ev_meta = ev.get("metadata") or {}
        expected_content_hash = ev.get("content_hash") or ""
        raw_content: str | None = None
        payload: dict[str, Any] = {}

        content_integrity_valid = False
        is_tampered = False
        is_corrupt_json = False

        try:
            raw_content = _evidence_service.get_evidence_content(ev_id)
            payload = json.loads(raw_content)
            content_integrity_valid = True
        except EvidencePersistenceError as err:
            err_msg = str(err).lower()
            if "tampering detected" in err_msg or "does not match authoritative digest" in err_msg:
                is_tampered = True
        except json.JSONDecodeError:
            is_corrupt_json = True
        except Exception:
            pass

        provenance = payload.get("provenance") or {}
        signal_content = payload.get("signal_content") or {}

        # ── Gate B: Real Accepted Disclosure Modes (ATTRIBUTED_INSTITUTIONS vs ANONYMIZED_SUMMARY) ──
        raw_disclosure = (
            provenance.get("disclosure_mode")
            or ev_meta.get("disclosure_mode")
            or "ANONYMIZED_SUMMARY"
        )
        disclosure_mode: Literal["ATTRIBUTED_INSTITUTIONS", "ANONYMIZED_SUMMARY"]
        if raw_disclosure == "ATTRIBUTED_INSTITUTIONS":
            disclosure_mode = "ATTRIBUTED_INSTITUTIONS"
            target_bank = provenance.get("target_bank_id") or ev_meta.get("target_bank_id")
        else:
            disclosure_mode = "ANONYMIZED_SUMMARY"
            target_bank = None  # Strictly masked under ANONYMIZED_SUMMARY

        batch_qid = ev_meta.get("batch_query_id") or payload.get("batch_query_id")
        batch_record = batch_store.get_batch(batch_qid) if batch_qid else None

        if batch_record:
            retention_status = "ACTIVE"
            batch_expires_at = batch_record.get("expires_at")
        else:
            retention_status = "RETENTION_EXPIRED"
            batch_expires_at = None

        signal_id = payload.get("signal_id") or ev_meta.get("signal_id")
        consortium_id = payload.get("consortium_id") or ev_meta.get("consortium_id")

        # ── Gate A: Authentic Evidence Verification State Evaluation ──
        verification_state: Literal[
            "VERIFIED", "INTEGRITY_VERIFIED_ONLY", "INDETERMINATE", "FAILED_VERIFICATION"
        ]
        if not content_integrity_valid:
            if is_tampered:
                verification_state = "FAILED_VERIFICATION"
                operational_status = "TAMPERED"
                is_authoritative = False
                explanation = (
                    f"Cryptographic verification failed: Persisted evidence artifact digest does not match "
                    f"authoritative registered hash '{expected_content_hash[:16]}...'."
                )
            elif is_corrupt_json:
                verification_state = "FAILED_VERIFICATION"
                operational_status = "CORRUPT"
                is_authoritative = False
                explanation = "Cryptographic verification failed: Evidence artifact content cannot be parsed as JSON."
            else:
                verification_state = "INDETERMINATE"
                operational_status = "UNAVAILABLE"
                is_authoritative = False
                explanation = "Authoritative evidence artifact is missing from durable storage or unreadable."
        else:
            # Content is cryptographically intact on disk; now evaluate issuance provenance
            receipt_id = provenance.get("receipt_id")
            target_bank_from_batch: str | None = None
            if not receipt_id and batch_record:
                succ_signals = batch_record.get("successful_signal_ids") or {}
                rcp_ids = batch_record.get("receipt_ids") or {}
                for t_bank, s_id in succ_signals.items():
                    if s_id == signal_id:
                        receipt_id = rcp_ids.get(t_bank)
                        target_bank_from_batch = t_bank
                        break

            admitted_rcp = intel_svc._admitted_receipts_store.get(receipt_id) if receipt_id else None

            # Evaluate full chain of evidence for authentic issuance verification
            issuance_verified = False
            issuance_mismatch_reason: str | None = None

            if not admitted_rcp:
                issuance_mismatch_reason = (
                    "lacks authentic operational issuance receipt and lacks authentic operational issuance proof in cryptographic admission registry"
                )
            else:
                # 1. Authoritative signal binding
                adm_signal_id = admitted_rcp.get("signal_id")
                adm_receipt_id = admitted_rcp.get("receipt_id")
                adm_req_bank = (admitted_rcp.get("requesting_bank_id") or "").lower().strip()
                adm_consortium = (admitted_rcp.get("consortium_id") or "").lower().strip()
                adm_entity = (admitted_rcp.get("query_entity_id") or "").strip()
                adm_target = (admitted_rcp.get("target_bank_id") or "").lower().strip()
                trusted_digest = admitted_rcp.get("content_hash")
                case_bank = (case.bank_id or "").lower().strip()
                payload_entity = (payload.get("query_entity_id") or "").strip()
                eff_consortium = (consortium_id or "").lower().strip()

                if not adm_signal_id or adm_signal_id != signal_id:
                    issuance_mismatch_reason = f"receipt '{receipt_id}' signal binding mismatch (bound to '{adm_signal_id}', got '{signal_id}')"
                elif not adm_receipt_id or adm_receipt_id != receipt_id:
                    issuance_mismatch_reason = f"receipt ID mismatch (expected '{receipt_id}', got '{adm_receipt_id}')"
                elif not adm_req_bank or adm_req_bank != case_bank:
                    issuance_mismatch_reason = f"receipt requesting bank scope mismatch ('{adm_req_bank}' != case bank '{case_bank}')"
                elif eff_consortium and adm_consortium != eff_consortium:
                    issuance_mismatch_reason = f"receipt consortium scope mismatch ('{adm_consortium}' != '{eff_consortium}')"
                elif payload_entity and adm_entity != payload_entity:
                    issuance_mismatch_reason = f"receipt query entity scope mismatch ('{adm_entity}' != '{payload_entity}')"
                elif target_bank is not None and adm_target != str(target_bank).lower().strip():
                    issuance_mismatch_reason = f"receipt counterpart scope mismatch ('{adm_target}' != '{target_bank}')"
                elif not trusted_digest or not isinstance(trusted_digest, str) or len(trusted_digest) != 64:
                    issuance_mismatch_reason = "admitted receipt lacks valid 64-char SHA-256 issuance digest"
                elif admitted_rcp.get("execution_mode") != "VERIFIED_OPERATIONAL":
                    issuance_mismatch_reason = f"admitted receipt execution mode '{admitted_rcp.get('execution_mode')}' is not VERIFIED_OPERATIONAL"
                else:
                    # Optional signal store digest check
                    sig_rec = (
                        intel_svc._signal_store.get(signal_id)
                        if isinstance(signal_id, str) and hasattr(intel_svc, "_signal_store")
                        else None
                    )
                    if sig_rec and sig_rec.get("content_hash") and sig_rec.get("content_hash") != trusted_digest:
                        issuance_mismatch_reason = "signal store content hash diverges from cryptographically admitted issuance digest"
                    elif batch_record:
                        # Batch membership checks
                        b_signals = batch_record.get("successful_signal_ids") or {}
                        b_rcps = batch_record.get("receipt_ids") or {}
                        b_req = (batch_record.get("requesting_bank_id") or "").lower().strip()
                        b_entity = (batch_record.get("query_entity_id") or "").strip()
                        effective_target = target_bank_from_batch or adm_target

                        if signal_id not in b_signals.values():
                            issuance_mismatch_reason = f"signal '{signal_id}' not found in batch successful signals"
                        elif b_rcps.get(effective_target) != receipt_id:
                            issuance_mismatch_reason = f"batch receipt ID for counterpart '{effective_target}' diverges from admitted receipt"
                        elif b_req and b_req != case_bank:
                            issuance_mismatch_reason = f"batch requesting bank '{b_req}' does not match case bank '{case_bank}'"
                        elif payload_entity and b_entity and b_entity != payload_entity:
                            issuance_mismatch_reason = f"batch query entity '{b_entity}' does not match payload entity '{payload_entity}'"
                        else:
                            issuance_verified = True
                    else:
                        # Original batch expired or unavailable, but independent issuance receipt is intact and verified
                        issuance_verified = True

            # Check current consortium status and pairwise grant revocation
            grant_revoked = False
            if consortium_id and admitted_rcp:
                g_id = admitted_rcp.get("grant_id")
                if g_id:
                    grant = consortium_svc.get_pairwise_operational_grant(consortium_id, g_id)
                    if grant and (grant.get("revoked_at") or grant.get("status") == "REVOKED" or not grant.get("is_active", True)):
                        grant_revoked = True

            if not issuance_verified:
                verification_state = "INTEGRITY_VERIFIED_ONLY"
                operational_status = "UNADMITTED" if not admitted_rcp else "PROVENANCE_MISMATCH"
                is_authoritative = False
                explanation = (
                    f"Evidence payload integrity verified ({expected_content_hash[:16]}...), "
                    f"but {issuance_mismatch_reason}."
                )
            else:
                # Authentic issuance is cryptographically verified
                verification_state = "VERIFIED"
                is_authoritative = True

                # Current authorization state vs historical facts
                if grant_revoked:
                    operational_status = "CONSENT_REVOKED"
                    explanation = (
                        f"Historical operational evidence cryptographically verified ({expected_content_hash[:16]}...). "
                        "Pairwise data sharing authorization has been revoked; current operational consent is inactive."
                    )
                elif retention_status == "RETENTION_EXPIRED":
                    operational_status = "HISTORICAL_RECORD"
                    explanation = (
                        f"Historical operational evidence cryptographically verified ({expected_content_hash[:16]}...). "
                        "Intermediate query batch cache expired (>24h TTL); immutable dossier evidence retained."
                    )
                else:
                    operational_status = "OPERATIONAL"
                    explanation = (
                        f"Authentic cross-bank operational signal verified ({consortium_id.upper() if consortium_id else 'CONSORTIUM'}). "
                        f"Cryptographic payload digest: {expected_content_hash[:16]}... "
                        "Independent admission and audit proofs verified."
                    )

        # Signal fields
        match_confirmed = signal_content.get("match_confirmed")
        if match_confirmed is None and "match_confirmed" in ev_meta:
            match_confirmed = ev_meta["match_confirmed"]

        match_category = signal_content.get("match_category")
        confidence = signal_content.get("confidence") or signal_content.get("match_confidence")
        risk_level = signal_content.get("risk_level") or signal_content.get("sanitized_risk_level")
        typology_indicators = (
            signal_content.get("typology_indicators")
            or signal_content.get("sanitized_typology_indicators")
            or []
        )
        notes = payload.get("analyst_notes") or ev_meta.get("notes")
        title = ev.get("title", "Consortium Intelligence Evidence")

        # Under ANONYMIZED_SUMMARY, sanitize notes and titles to prevent counterpart bank leaks
        if disclosure_mode == "ANONYMIZED_SUMMARY":
            if notes:
                notes = re.sub(r"\bbank_[a-zA-Z0-9_\-]+\b", "[REDACTED_INSTITUTION]", str(notes), flags=re.IGNORECASE)
            title = re.sub(r"\bbank_[a-zA-Z0-9_\-]+\b", "Anonymous Peer", str(title), flags=re.IGNORECASE)

        projected.append(
            CaseIntelligenceEvidenceResponse(
                evidence_id=ev_id,
                case_id=case_id,
                title=title,
                content_hash=expected_content_hash,
                batch_query_id=batch_qid,
                uploaded_at=ev.get("uploaded_at", datetime.now(UTC).isoformat()),
                uploaded_by=ev.get("uploaded_by", "system"),
                operational_status=operational_status,
                is_authoritative=is_authoritative,
                disclosure_mode=disclosure_mode,
                target_bank_id=target_bank,
                match_confirmed=match_confirmed,
                match_category=match_category,
                confidence=confidence,
                risk_level=risk_level,
                typology_indicators=typology_indicators,
                analyst_notes=notes,
                verification_state=verification_state,
                retention_status=retention_status,
                batch_expires_at=batch_expires_at,
                explanation=explanation,
            )
        )

    return projected


def _serialize_case(case: Any) -> CaseResponse:
    return CaseResponse(
        id=case.id,
        bank_id=getattr(case, "bank_id", None),
        title=case.title,
        status=case.status.value,
        priority=case.priority.value,
        assigned_to=case.assigned_to,
        alert_ids=case.alert_ids,
        evidence_ids=getattr(case, "evidence_ids", []) or [],
        notes=[
            CaseNoteResponse(
                id=n.id,
                case_id=n.case_id,
                author=n.author,
                content=n.content,
                created_at=n.created_at.isoformat(),
            )
            for n in case.notes
        ],
        timeline=[
            CaseEventResponse(
                event_type=e.event_type,
                description=e.description,
                actor=e.actor,
                timestamp=e.timestamp.isoformat(),
                metadata=e.metadata or {},
            )
            for e in case.timeline
        ],
        created_at=case.created_at.isoformat(),
        updated_at=case.updated_at.isoformat() if case.updated_at else None,
        closed_at=case.closed_at.isoformat() if case.closed_at else None,
        total_risk_score=case.total_risk_score,
        duration_hours=case.duration_hours,
        is_open=case.is_open,
        supervisor_signatures=getattr(case, "supervisor_signatures", []) or [],
        supervisor_signature=getattr(case, "supervisor_signature", None),
        version=getattr(case, "version", 1),
        timeline_hash=case.timeline_hash,
    )


@router.post("/{case_id}/file-sar")
@api_router.post("/{case_id}/file-sar")
async def file_sar_report(
    case_id: str,
    institution_name: str | None = None,
    narrative_override: str | None = None,
    caller_tenant: TenantDep = None,
) -> dict[str, Any]:
    """Generate and validate FinCEN BSA SAR XML payload with SHA-256 integrity hash for a confirmed fraud case."""
    from app.application.services.regulatory_reporter import (
        RegulatoryReporterService,
        SARValidationError,
    )

    case = _case_service.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found.")
    _enforce_case_tenant(case, caller_tenant)

    # Invariant: SAR filings cannot be generated for false positives or unreviewed open cases
    if case.status == CaseStatus.CLOSED_FALSE_POSITIVE:
        raise HTTPException(
            status_code=400,
            detail="Cannot generate SAR filing for a case resolved as CLOSED_FALSE_POSITIVE.",
        )
    if case.status in (CaseStatus.OPEN, CaseStatus.ASSIGNED):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot generate SAR filing for unreviewed case in status '{case.status.value}'. Must be escalated or confirmed fraud.",
        )

    try:
        from app.application.services.alert_service import AlertIntelligenceService

        alert_service = AlertIntelligenceService()
        alerts = [
            a
            for aid in (case.alert_ids or [])
            if (a := alert_service.get_alert(aid)) is not None
        ]

        filing_record = RegulatoryReporterService.generate_and_store_sar_filing(
            case_id=case_id,
            case_obj=case,
            alerts=alerts,
            institution_name=institution_name or "Consortium AML Joint Investigation Unit",
            narrative_override=narrative_override,
        )

        with open(filing_record.xml_path, encoding="utf-8") as f:
            xml_str = f.read()

        return {
            "submission_id": filing_record.submission_id,
            "status": "FILED",
            "xml": xml_str,
            "xml_payload": xml_str,
            "sha256_hash": filing_record.sha256_hash,
            "filing_status": "FILED",
            "pdf_download_url": f"/api/v1/cases/{case_id}/sar.pdf",
        }
    except SARValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/export/fincen-xml", response_model=ExportFinCENXmlResponse)
@api_router.post("/export/fincen-xml", response_model=ExportFinCENXmlResponse)
async def export_fincen_xml_endpoint(
    payload: ExportFinCENXmlRequest, caller_tenant: TenantDep = None
) -> dict[str, Any]:
    """Compile and validate FinCEN BSA SAR XML payload with SHA-256 hash (Developer Portal & SIEM)."""
    target_case_id = (payload.case_id or "").strip()
    if not target_case_id:
        avail_cases = _case_service.get_cases(limit=5)
        avail_ids = [c.id for c in avail_cases]
        raise HTTPException(
            status_code=400,
            detail=f"Missing required case_id for FinCEN SAR XML export. Please select a valid case. Available cases: {avail_ids}",
        )

    # Resolve active/demo aliases if provided
    if target_case_id.lower() in ("demo", "latest", "active"):
        avail_cases = _case_service.get_cases(limit=1)
        if avail_cases:
            target_case_id = avail_cases[0].id

    case = _case_service.get_case(target_case_id)
    if not case:
        avail_cases = _case_service.get_cases(limit=5)
        avail_ids = [c.id for c in avail_cases]
        raise HTTPException(
            status_code=404,
            detail=f"Case '{target_case_id}' not found. Please select an active case from: {avail_ids}",
        )
    _enforce_case_tenant(case, caller_tenant)

    return await file_sar_report(
        target_case_id,
        institution_name=payload.institution_name,
        narrative_override=payload.narrative_override,
        caller_tenant=caller_tenant,
    )


# ── Agentic AML Copilot Endpoints ─────────────────────────────────────

_aml_copilot = AMLAgenticCopilot()


@router.post("/{case_id}/copilot/narrative", response_model=CopilotQueryResponse)
@api_router.post("/{case_id}/copilot/narrative", response_model=CopilotQueryResponse)
async def generate_copilot_narrative(
    case_id: str,
    req: CopilotQueryRequest | None = None,
    caller_tenant: TenantDep = None,
) -> CopilotQueryResponse:
    """Synthesize formal FinCEN 5-paragraph SAR narrative and 4-Eyes supervisor briefing using AML Copilot."""
    c_obj = _case_service.get_case(case_id)
    if not c_obj:
        raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found.")
    _enforce_case_tenant(c_obj, caller_tenant)

    title = c_obj.title or f"Case {case_id}"
    status_str = (
        (c_obj.status.value if hasattr(c_obj.status, "value") else str(c_obj.status))
        if c_obj.status
        else "OPEN"
    )
    alert_ids = c_obj.alert_ids or []
    notes = req.custom_investigator_notes if req else None

    # Retrieve registered evidence artifacts
    evidence_items = _evidence_service.get_case_evidence(case_id)

    # Normalize timeline events and existing notes
    timeline_events = [e.__dict__ if hasattr(e, "__dict__") else e for e in c_obj.timeline]
    case_notes = [n.content if hasattr(n, "content") else str(n) for n in c_obj.notes]
    if notes:
        case_notes.append(notes)

    # Dynamically extract risk score
    risk_score = float(c_obj.total_risk_score) if c_obj.total_risk_score and c_obj.total_risk_score > 0.0 else 750.0

    dossier = _aml_copilot.assemble_case_evidence(
        case_id=case_id,
        case_title=title,
        case_status=status_str,
        total_risk_score=risk_score,
        alert_ids=alert_ids,
        timeline_events=timeline_events,
        evidence_artifacts=evidence_items,
        investigator_notes=case_notes,
        shap_drivers=req.shap_attributions if req else None,
        graph_metadata=req.graph_metadata if req else None,
    )

    analysis = _aml_copilot.synthesize_from_evidence(dossier, investigator_notes=notes)

    from datetime import UTC, datetime

    return CopilotQueryResponse(
        case_id=analysis.case_id,
        fincen_sar_narrative=analysis.fincen_sar_narrative,
        four_eyes_briefing=analysis.four_eyes_briefing,
        recommended_action=analysis.recommended_action,
        top_risk_drivers=analysis.top_risk_drivers,
        graph_topology_summary=analysis.graph_topology_summary,
        zero_pii_verified=analysis.zero_pii_verified,
        generated_at=datetime.fromtimestamp(analysis.generated_at_timestamp, tz=UTC).isoformat(),
        lineage_hash=analysis.lineage_hash,
        evidence_count=analysis.evidence_count,
        timeline_event_count=analysis.timeline_event_count,
    )


@router.get("/{case_id}/copilot/summary")
@api_router.get("/{case_id}/copilot/summary")
async def get_copilot_summary(
    case_id: str, caller_tenant: TenantDep = None
) -> dict[str, Any]:
    """Get structured Copilot findings and 4-Eyes disposition for a case."""
    c_obj = _case_service.get_case(case_id)
    if not c_obj:
        raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found.")
    _enforce_case_tenant(c_obj, caller_tenant)

    title = c_obj.title or f"Case {case_id}"
    status_str = (
        (c_obj.status.value if hasattr(c_obj.status, "value") else str(c_obj.status))
        if c_obj.status
        else "OPEN"
    )
    alert_ids = c_obj.alert_ids or []
    risk_score = float(c_obj.total_risk_score) if c_obj.total_risk_score and c_obj.total_risk_score > 0.0 else 750.0

    evidence_items = _evidence_service.get_case_evidence(case_id)
    timeline_events = [e.__dict__ if hasattr(e, "__dict__") else e for e in c_obj.timeline]
    case_notes = [n.content if hasattr(n, "content") else str(n) for n in c_obj.notes]

    dossier = _aml_copilot.assemble_case_evidence(
        case_id=case_id,
        case_title=title,
        case_status=status_str,
        total_risk_score=risk_score,
        alert_ids=alert_ids,
        timeline_events=timeline_events,
        evidence_artifacts=evidence_items,
        investigator_notes=case_notes,
    )
    analysis = _aml_copilot.synthesize_from_evidence(dossier)

    return {
        "case_id": analysis.case_id,
        "recommended_action": analysis.recommended_action,
        "top_risk_drivers": analysis.top_risk_drivers,
        "graph_topology_summary": analysis.graph_topology_summary,
        "zero_pii_verified": analysis.zero_pii_verified,
        "lineage_hash": analysis.lineage_hash,
        "evidence_count": analysis.evidence_count,
        "timeline_event_count": analysis.timeline_event_count,
        "evidence_hash": dossier.evidence_hash,
    }


@router.get("/{case_id}/copilot/evidence")
@api_router.get("/{case_id}/copilot/evidence")
async def get_copilot_case_evidence(
    case_id: str, caller_tenant: TenantDep = None
) -> dict[str, Any]:
    """Get assembled cryptographic case evidence dossier."""
    c_obj = _case_service.get_case(case_id)
    if not c_obj:
        raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found.")
    _enforce_case_tenant(c_obj, caller_tenant)

    title = c_obj.title or f"Case {case_id}"
    status_str = (
        (c_obj.status.value if hasattr(c_obj.status, "value") else str(c_obj.status))
        if c_obj.status
        else "OPEN"
    )
    alert_ids = c_obj.alert_ids or []
    risk_score = float(c_obj.total_risk_score) if c_obj.total_risk_score and c_obj.total_risk_score > 0.0 else 750.0

    evidence_items = _evidence_service.get_case_evidence(case_id)
    timeline_events = [e.__dict__ if hasattr(e, "__dict__") else e for e in c_obj.timeline]
    case_notes = [n.content if hasattr(n, "content") else str(n) for n in c_obj.notes]

    dossier = _aml_copilot.assemble_case_evidence(
        case_id=case_id,
        case_title=title,
        case_status=status_str,
        total_risk_score=risk_score,
        alert_ids=alert_ids,
        timeline_events=timeline_events,
        evidence_artifacts=evidence_items,
        investigator_notes=case_notes,
    )

    return {
        "case_id": dossier.case_id,
        "case_title": dossier.case_title,
        "case_status": dossier.case_status,
        "total_risk_score": dossier.total_risk_score,
        "alert_ids": dossier.alert_ids,
        "timeline_events": dossier.timeline_events,
        "evidence_artifacts": dossier.evidence_artifacts,
        "investigator_notes": dossier.investigator_notes,
        "shap_drivers": dossier.shap_drivers,
        "graph_topology": dossier.graph_topology,
        "pii_sanitized_count": dossier.pii_sanitized_count,
        "evidence_hash": dossier.evidence_hash,
        "assembled_at": dossier.assembled_at,
    }
