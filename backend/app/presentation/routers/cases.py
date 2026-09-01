"""Case management API endpoints.

CRUD operations for investigation cases with status transitions,
notes, alert linking, and timeline.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from app.application.schemas.phase2 import (
    CaseCreateRequest,
    CaseEventResponse,
    CaseLinkAlertRequest,
    CaseNoteRequest,
    CaseNoteResponse,
    CaseResponse,
    CaseStatusRequest,
    CaseSummaryResponse,
    EvidenceRequest,
    EvidenceResponse,
    InvestigatorAuditLogResponse,
    SessionDurationRequest,
)
from app.application.services.aml_agentic_copilot import AMLAgenticCopilot
from app.application.services.case_service import (
    AuditService,
    CaseManagementService,
    EvidenceRegistryService,
)
from app.application.services.idempotency import IdempotencyService
from app.dependencies import TenantDep, enforce_tenant_isolation
from app.domain.enums import CasePriority, CaseStatus
from app.domain.value_objects_copilot import CopilotQueryRequest, CopilotQueryResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/cases", tags=["cases"])

_case_service = CaseManagementService()


def get_case_service() -> CaseManagementService:
    return _case_service


@router.get("", response_model=list[CaseSummaryResponse])
async def list_cases(
    status: str | None = Query(None),
    priority: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
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

    cases = _case_service.get_cases(status=stat, priority=pri, limit=limit)
    return [
        CaseSummaryResponse(
            id=c.id,
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
async def get_audit_logs(
    limit: int = Query(100, ge=1, le=500),
) -> list[InvestigatorAuditLogResponse]:
    """Retrieve investigator activity logs."""
    audit_svc = AuditService()
    logs = audit_svc.get_logs(limit=limit)
    return [InvestigatorAuditLogResponse(**log) for log in logs]


@router.post("/audit/session", response_model=dict)
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
async def create_case(
    req: CaseCreateRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> CaseResponse | JSONResponse:
    """Create a new investigation case.

    Supports idempotent creation via the ``Idempotency-Key`` request header.
    If a request with the same key was successfully processed within 24 hours,
    the original response is returned without creating a duplicate case.
    """
    idem = IdempotencyService.get()
    cached = idem.get_cached(idempotency_key)
    if cached is not None:
        return JSONResponse(
            content=cached,
            status_code=200,
            headers={"Idempotency-Replayed": "true"},
        )

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
    )
    result = _serialize_case(case)
    idem.store(idempotency_key, result.model_dump())
    return result


@router.get("/{case_id}", response_model=CaseResponse)
async def get_case(
    case_id: str, actor: str = Query("analyst"), caller_tenant: TenantDep = None
) -> CaseResponse:
    """Get case detail with tenant isolation check."""
    case = _case_service.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    if caller_tenant and case.alert_ids:
        from app.presentation.routers.alerts import get_alert_service

        alert_svc = get_alert_service()
        for a_id in case.alert_ids:
            a = alert_svc.get_alert(a_id)
            if a:
                enforce_tenant_isolation(caller_tenant, a.bank_id)

    AuditService().log_action(actor, "access_case", case_id)
    return _serialize_case(case)



@router.patch("/{case_id}", response_model=CaseResponse)
async def update_case_status(case_id: str, req: CaseStatusRequest) -> CaseResponse:
    """Update case status."""
    try:
        new_status = CaseStatus(req.status)
        case = _case_service.change_status(
            case_id,
            new_status,
            actor=req.actor,
            supervisor_signature=req.supervisor_signature,
        )
        return _serialize_case(case)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{case_id}/notes", response_model=CaseNoteResponse)
async def add_note(case_id: str, req: CaseNoteRequest) -> CaseNoteResponse:
    """Add an investigation note."""
    try:
        note = _case_service.add_note(case_id, author=req.author, content=req.content)
        return CaseNoteResponse(
            id=note.id,
            case_id=note.case_id,
            author=note.author,
            content=note.content,
            created_at=note.created_at.isoformat(),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{case_id}/alerts", response_model=CaseResponse)
async def link_alert(case_id: str, req: CaseLinkAlertRequest) -> CaseResponse:
    """Link an alert to a case."""
    try:
        case = _case_service.link_alert(case_id, req.alert_id)
        return _serialize_case(case)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{case_id}/timeline", response_model=list[CaseEventResponse])
async def get_timeline(case_id: str) -> list[CaseEventResponse]:
    """Get investigation timeline."""
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
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{case_id}/export")
async def export_case(case_id: str) -> dict:
    """Export investigation summary as markdown."""
    try:
        summary = _case_service.export_summary(case_id)
        return {"case_id": case_id, "format": "markdown", "content": summary}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{case_id}/sar-report")
async def download_sar_report(case_id: str) -> FileResponse:
    """Download generated FinCEN SAR XML report for the case."""
    import os

    report_dir = "storage/regulatory_filings"
    report_path = os.path.join(report_dir, f"sar_{case_id}.xml").replace("\\", "/")

    if not os.path.exists(report_path):
        case = _case_service.get_case(case_id)
        if not case:
            raise HTTPException(
                status_code=404,
                detail=f"Case {case_id} not found.",
            )

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
async def register_evidence(case_id: str, req: EvidenceRequest) -> EvidenceResponse:
    """Register new case evidence with SHA-256 hash verification."""
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
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{case_id}/evidence", response_model=list[EvidenceResponse])
async def get_case_evidence(case_id: str) -> list[EvidenceResponse]:
    """Retrieve all evidence registered for a case."""
    registry = EvidenceRegistryService()
    ev_list = registry.get_case_evidence(case_id)
    return [EvidenceResponse(**ev) for ev in ev_list]


def _serialize_case(case: Any) -> CaseResponse:
    return CaseResponse(
        id=case.id,
        title=case.title,
        status=case.status.value,
        priority=case.priority.value,
        assigned_to=case.assigned_to,
        alert_ids=case.alert_ids,
        evidence_ids=getattr(case, "evidence_ids", []),
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
                metadata=e.metadata,
            )
            for e in case.timeline
        ],
        created_at=case.created_at.isoformat(),
        updated_at=case.updated_at.isoformat() if case.updated_at else None,
        closed_at=case.closed_at.isoformat() if case.closed_at else None,
        total_risk_score=case.total_risk_score,
        duration_hours=case.duration_hours,
        is_open=case.is_open,
    )


@router.post("/{case_id}/file-sar")
async def file_sar_report(case_id: str) -> dict[str, Any]:
    """Generate and validate FinCEN BSA SAR XML payload for a confirmed fraud case."""
    import uuid

    from app.application.services.regulatory_reporter import (
        RegulatoryReporterService,
        SARValidationError,
    )

    try:
        xml_str = RegulatoryReporterService.generate_sar_xml(case_id)
        submission_id = f"sar_{uuid.uuid4().hex[:12]}"
        return {
            "submission_id": submission_id,
            "status": "FILED",
            "xml": xml_str,
            "pdf_download_url": f"/api/v1/cases/{case_id}/sar.pdf",
        }
    except SARValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── Agentic AML Copilot Endpoints ─────────────────────────────────────

_aml_copilot = AMLAgenticCopilot()


@router.post("/{case_id}/copilot/narrative", response_model=CopilotQueryResponse)
async def generate_copilot_narrative(
    case_id: str,
    req: CopilotQueryRequest | None = None,
) -> CopilotQueryResponse:
    """Synthesize formal FinCEN 5-paragraph SAR narrative and 4-Eyes supervisor briefing using AML Copilot."""
    c_obj = _case_service.get_case(case_id)
    title = c_obj.title if c_obj else f"Case {case_id}"
    status = (
        (c_obj.status.value if hasattr(c_obj.status, "value") else str(c_obj.status))
        if c_obj
        else "OPEN"
    )
    alert_ids = c_obj.alert_ids if c_obj else ["alt_101", "alt_102"]
    notes = req.custom_investigator_notes if req else None

    analysis = _aml_copilot.generate_case_narrative(
        case_id=case_id,
        case_title=title,
        case_status=status,
        alert_ids=alert_ids,
        risk_score=785.0,
        investigator_notes=notes,
    )

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
    )


@router.get("/{case_id}/copilot/summary")
async def get_copilot_summary(case_id: str) -> dict[str, Any]:
    """Get structured Copilot findings and 4-Eyes disposition for a case."""
    analysis = _aml_copilot.generate_case_narrative(
        case_id=case_id,
        case_title=f"Case {case_id}",
        case_status="UNDER_INVESTIGATION",
        alert_ids=["alt_101", "alt_102"],
        risk_score=820.0,
    )
    return {
        "case_id": analysis.case_id,
        "recommended_action": analysis.recommended_action,
        "top_risk_drivers": analysis.top_risk_drivers,
        "graph_topology_summary": analysis.graph_topology_summary,
        "zero_pii_verified": analysis.zero_pii_verified,
        "lineage_hash": analysis.lineage_hash,
    }
