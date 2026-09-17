"""Autonomous Agentic AML Copilot Presentation Router.

Exposes endpoints for FinCEN 5-paragraph SAR narrative generation,
4-Eyes supervisor briefings, and cryptographic case evidence assembly.
Supports dual-prefix routing: /api/v1/copilot and /v1/copilot.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status

from app.application.schemas.copilot import (
    AssembledEvidenceResponse,
    CopilotDirectGenerationRequest,
    CopilotQueryRequest,
    CopilotQueryResponse,
    CopilotStatusResponse,
)
from app.application.services.aml_agentic_copilot import AMLAgenticCopilot
from app.application.services.case_service import CaseManagementService, EvidenceRegistryService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/copilot", tags=["AML Copilot"])
api_router = APIRouter(prefix="/api/v1/copilot", tags=["AML Copilot"])

_copilot = AMLAgenticCopilot()
_case_service = CaseManagementService()
_evidence_service = EvidenceRegistryService()


def _generate_sar_handler(payload: CopilotDirectGenerationRequest) -> CopilotQueryResponse:
    """Core handler synthesizing FinCEN SAR narrative and 4-Eyes briefing."""
    case_id = payload.case_id.strip()
    if not case_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="case_id cannot be empty or whitespace",
        )

    c_obj = _case_service.get_case(case_id)
    if payload.require_existing_case and c_obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case '{case_id}' not found in case registry",
        )

    title = c_obj.title if c_obj else f"Case {case_id}"
    case_status = (
        (c_obj.status.value if hasattr(c_obj.status, "value") else str(c_obj.status))
        if c_obj and c_obj.status
        else "UNDER_INVESTIGATION"
    )
    alert_ids = c_obj.alert_ids if c_obj and c_obj.alert_ids else []

    # Risk score precedence: explicit payload > case record > default 750.0
    if payload.risk_score is not None:
        risk_score = payload.risk_score
    elif c_obj and c_obj.total_risk_score and c_obj.total_risk_score > 0.0:
        risk_score = float(c_obj.total_risk_score)
    else:
        risk_score = 750.0

    # Evidence artifacts from registry
    evidence_items = _evidence_service.get_case_evidence(case_id) if c_obj else []
    timeline_events = [e.__dict__ if hasattr(e, "__dict__") else e for e in c_obj.timeline] if c_obj else []
    case_notes = [n.content if hasattr(n, "content") else str(n) for n in c_obj.notes] if c_obj else []
    if payload.custom_investigator_notes:
        case_notes.append(payload.custom_investigator_notes)

    # Graph metadata normalization
    graph_meta = None
    if isinstance(payload.graph_nodes, dict):
        graph_meta = payload.graph_nodes
    elif isinstance(payload.graph_nodes, list):
        graph_meta = {
            "node_count": len(payload.graph_nodes),
            "connected_banks": ["Participating Bank Alpha", "Participating Bank Beta"],
            "nodes": payload.graph_nodes[:10],
        }

    dossier = _copilot.assemble_case_evidence(
        case_id=case_id,
        case_title=title,
        case_status=case_status,
        total_risk_score=risk_score,
        alert_ids=alert_ids,
        timeline_events=timeline_events,
        evidence_artifacts=evidence_items,
        investigator_notes=case_notes,
        shap_drivers=payload.shap_attributions,
        graph_metadata=graph_meta,
    )

    analysis = _copilot.synthesize_from_evidence(
        dossier, investigator_notes=payload.custom_investigator_notes
    )

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


def _assemble_evidence_handler(payload: CopilotDirectGenerationRequest) -> AssembledEvidenceResponse:
    """Core handler assembling cryptographic evidence package."""
    case_id = payload.case_id.strip()
    if not case_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="case_id cannot be empty or whitespace",
        )

    c_obj = _case_service.get_case(case_id)
    if payload.require_existing_case and c_obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case '{case_id}' not found in case registry",
        )

    title = c_obj.title if c_obj else f"Case {case_id}"
    case_status = (
        (c_obj.status.value if hasattr(c_obj.status, "value") else str(c_obj.status))
        if c_obj and c_obj.status
        else "UNDER_INVESTIGATION"
    )
    alert_ids = c_obj.alert_ids if c_obj and c_obj.alert_ids else []
    risk_score = (
        payload.risk_score
        if payload.risk_score is not None
        else (float(c_obj.total_risk_score) if c_obj and c_obj.total_risk_score else 750.0)
    )

    evidence_items = _evidence_service.get_case_evidence(case_id) if c_obj else []
    timeline_events = [e.__dict__ if hasattr(e, "__dict__") else e for e in c_obj.timeline] if c_obj else []
    case_notes = [n.content if hasattr(n, "content") else str(n) for n in c_obj.notes] if c_obj else []
    if payload.custom_investigator_notes:
        case_notes.append(payload.custom_investigator_notes)

    dossier = _copilot.assemble_case_evidence(
        case_id=case_id,
        case_title=title,
        case_status=case_status,
        total_risk_score=risk_score,
        alert_ids=alert_ids,
        timeline_events=timeline_events,
        evidence_artifacts=evidence_items,
        investigator_notes=case_notes,
        shap_drivers=payload.shap_attributions,
    )

    return AssembledEvidenceResponse(
        case_id=dossier.case_id,
        case_title=dossier.case_title,
        case_status=dossier.case_status,
        total_risk_score=dossier.total_risk_score,
        evidence_hash=dossier.evidence_hash,
        evidence_count=len(dossier.evidence_artifacts),
        timeline_event_count=len(dossier.timeline_events),
        pii_sanitized_count=dossier.pii_sanitized_count,
        assembled_at=dossier.assembled_at,
    )


def _get_case_evidence_handler(case_id: str) -> AssembledEvidenceResponse:
    """Helper retrieving assembled evidence for an existing case."""
    clean_id = case_id.strip()
    c_obj = _case_service.get_case(clean_id)
    if c_obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case '{clean_id}' not found in case registry",
        )
    req = CopilotDirectGenerationRequest(case_id=clean_id, require_existing_case=True)
    return _assemble_evidence_handler(req)


def _get_case_narrative_handler(
    case_id: str,
    payload: CopilotQueryRequest | None = None,
) -> CopilotQueryResponse:
    """Helper generating SAR narrative for an existing registered case."""
    clean_id = case_id.strip()
    c_obj = _case_service.get_case(clean_id)
    if c_obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case '{clean_id}' not found in case registry",
        )

    custom_notes = payload.custom_investigator_notes if payload else None
    shap_attr = payload.shap_attributions if payload else None
    graph_meta = payload.graph_metadata if payload else None

    direct_req = CopilotDirectGenerationRequest(
        case_id=clean_id,
        shap_attributions=shap_attr,
        graph_nodes=graph_meta,
        custom_investigator_notes=custom_notes,
        require_existing_case=True,
    )
    return _generate_sar_handler(direct_req)


# ── Status & Health ─────────────────────────────────────────────────────────

@router.get("/status", response_model=CopilotStatusResponse)
@api_router.get("/status", response_model=CopilotStatusResponse)
def get_copilot_status() -> CopilotStatusResponse:
    """Return operational readiness and metric status of AML Copilot."""
    return CopilotStatusResponse(
        status="active",
        zero_pii_engine="operational",
        synthesized_analyses_count=_copilot.synthesized_analyses_count,
        timestamp=datetime.now(UTC).isoformat(),
    )


@router.get("/health", response_model=CopilotStatusResponse)
@api_router.get("/health", response_model=CopilotStatusResponse)
def get_copilot_health() -> CopilotStatusResponse:
    """Health check alias for AML Copilot service."""
    return get_copilot_status()


# ── Direct Generation & Assembly Endpoints ─────────────────────────────────

@router.post("/generate-sar", response_model=CopilotQueryResponse)
def generate_sar_v1(payload: CopilotDirectGenerationRequest) -> CopilotQueryResponse:
    """Generate FinCEN 5-paragraph SAR narrative (v1 legacy prefix)."""
    return _generate_sar_handler(payload)


@api_router.post("/generate-sar", response_model=CopilotQueryResponse)
def generate_sar_api_v1(payload: CopilotDirectGenerationRequest) -> CopilotQueryResponse:
    """Generate FinCEN 5-paragraph SAR narrative (canonical api/v1 prefix)."""
    return _generate_sar_handler(payload)


@router.post("/assemble-evidence", response_model=AssembledEvidenceResponse)
def assemble_evidence_v1(payload: CopilotDirectGenerationRequest) -> AssembledEvidenceResponse:
    """Assemble cryptographic evidence package (v1 legacy prefix)."""
    return _assemble_evidence_handler(payload)


@api_router.post("/assemble-evidence", response_model=AssembledEvidenceResponse)
def assemble_evidence_api_v1(payload: CopilotDirectGenerationRequest) -> AssembledEvidenceResponse:
    """Assemble cryptographic evidence package (canonical api/v1 prefix)."""
    return _assemble_evidence_handler(payload)


# ── Case-Linked Copilot Endpoints (API_REGISTRY.md Section 2.3) ────────────

@router.get("/cases/{case_id}/evidence", response_model=AssembledEvidenceResponse)
@api_router.get("/cases/{case_id}/evidence", response_model=AssembledEvidenceResponse)
def get_copilot_case_evidence(case_id: str) -> AssembledEvidenceResponse:
    """Assembles registered case artifacts into structured evidence dossier."""
    return _get_case_evidence_handler(case_id)


@router.post("/cases/{case_id}/narrative", response_model=CopilotQueryResponse)
@api_router.post("/cases/{case_id}/narrative", response_model=CopilotQueryResponse)
def post_copilot_case_narrative(
    case_id: str,
    payload: CopilotQueryRequest | None = None,
) -> CopilotQueryResponse:
    """Generates LLM draft SAR narrative with zero-PII validation for an active case."""
    return _get_case_narrative_handler(case_id, payload)


@router.get("/cases/{case_id}/summary", response_model=CopilotQueryResponse)
@api_router.get("/cases/{case_id}/summary", response_model=CopilotQueryResponse)
def get_copilot_case_summary(case_id: str) -> CopilotQueryResponse:
    """Retrieves copilot summary briefing and narrative for an active case."""
    return _get_case_narrative_handler(case_id)
