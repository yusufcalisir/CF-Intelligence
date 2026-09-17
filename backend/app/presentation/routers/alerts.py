"""Alert and intelligence API endpoints.

Manages fraud alerts, triage lifecycle, sliding-window deduplication,
and shared cross-institution intelligence feeds.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query, Request

from app.application.schemas.alerts import (
    AlertDeduplicationStatsResponse,
    AlertResponse,
    AlertStandaloneTriageRequest,
    AlertStatusUpdateRequest,
    AlertTriageEvaluateRequest,
    AlertTriageEvaluateResponse,
)
from app.application.schemas.phase2 import (
    CounterfactualChangeSchema,
    CounterfactualExplanationResponse,
    DecisionReplayResponse,
    EdgeContributionSchema,
    ExplainabilityResponse,
    GNNExplanationResponse,
    IntelligenceStatsResponse,
    LIMEExplanationResponse,
    LIMEFeatureAttributionSchema,
    PolicyRuleEvaluationSchema,
    SharedIntelligenceResponse,
)
from app.application.services.alert_service import AlertIntelligenceService, AlertTriageEngine
from app.application.services.explainability_service import ExplainabilityService
from app.dependencies import TenantDep, enforce_tenant_isolation
from app.domain.enums import AlertSeverity, AlertStatus
from app.infrastructure.security.rate_limiter import limiter

if TYPE_CHECKING:
    from app.domain.entities_phase2 import Alert

logger = logging.getLogger(__name__)

# Dual-routing support for both /api/v1 and /v1 prefixes
router = APIRouter(prefix="/api/v1", tags=["alerts"])
api_router = APIRouter(prefix="/v1", tags=["alerts"])

# Shared service instances (singleton pattern matching Phase 1)
_alert_service = AlertIntelligenceService()
_explainability_service = ExplainabilityService()


def get_alert_service() -> AlertIntelligenceService:
    return _alert_service


def _to_alert_response(a: Alert) -> AlertResponse:
    return AlertResponse(
        id=a.id,
        bank_id=a.bank_id,
        transaction_id=a.transaction_id,
        risk_score=a.risk_score,
        severity=a.severity.value,
        status=a.status.value,
        reason_codes=a.reason_codes,
        confidence=a.confidence,
        involved_entity_ids=a.involved_entity_ids,
        created_at=a.created_at.isoformat(),
        updated_at=a.updated_at.isoformat() if a.updated_at else None,
        top_features=a.top_features,
        risk_factors=a.risk_factors,
        model_confidence=a.model_confidence,
        triage_priority=a.triage_priority.value if hasattr(a.triage_priority, "value") else str(a.triage_priority),
        triage_action=a.triage_action.value if hasattr(a.triage_action, "value") else str(a.triage_action),
        sla_minutes=a.sla_minutes,
        triage_reasons=a.triage_reasons,
        dedup_count=a.dedup_count,
        is_duplicate=a.is_duplicate,
        dedup_key=a.dedup_key,
    )


@router.get("/alerts", response_model=list[AlertResponse])
@api_router.get("/alerts", response_model=list[AlertResponse])
@limiter.limit("120/minute")
async def list_alerts(
    request: Request,
    bank_id: str | None = Query(None),
    severity: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    caller_tenant: TenantDep = None,
) -> list[AlertResponse]:
    """List fraud alerts with optional filters and broken access control checks."""
    if caller_tenant and bank_id:
        enforce_tenant_isolation(caller_tenant, bank_id)
    effective_bank_id = bank_id or caller_tenant
    try:
        sev = AlertSeverity(severity) if severity else None
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid severity value: {severity!r}. "
            f"Valid values: {[e.value for e in AlertSeverity]}",
        )
    try:
        stat = AlertStatus(status) if status else None
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status value: {status!r}. "
            f"Valid values: {[e.value for e in AlertStatus]}",
        )

    alerts = _alert_service.get_alerts(
        bank_id=effective_bank_id,
        severity=sev,
        status=stat,
        limit=limit,
    )

    return [_to_alert_response(a) for a in alerts]


@router.get("/alerts/dedup/stats", response_model=AlertDeduplicationStatsResponse)
@api_router.get("/alerts/dedup/stats", response_model=AlertDeduplicationStatsResponse)
@limiter.limit("120/minute")
async def get_deduplication_stats(request: Request) -> AlertDeduplicationStatsResponse:
    """Get real-time alert deduplication sliding window statistics."""
    stats = _alert_service.get_dedup_stats()
    return AlertDeduplicationStatsResponse(**stats)


@router.post("/alerts/triage/evaluate", response_model=AlertTriageEvaluateResponse)
@api_router.post("/alerts/triage/evaluate", response_model=AlertTriageEvaluateResponse)
@limiter.limit("60/minute")
async def evaluate_standalone_triage(
    request: Request,
    payload: AlertStandaloneTriageRequest,
    caller_tenant: TenantDep = None,
) -> AlertTriageEvaluateResponse:
    """Evaluate multi-factor alert triage rules on arbitrary transaction features."""
    try:
        sev = AlertSeverity(payload.severity)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid alert severity: {payload.severity!r}. Valid values: {[e.value for e in AlertSeverity]}",
        )

    res = AlertTriageEngine.evaluate_triage(
        txn={
            "transaction_amount": payload.transaction_amount,
            "country_code": payload.country_code,
            "velocity": payload.velocity,
        },
        risk_score=payload.risk_score,
        severity=sev,
        dedup_count=payload.dedup_count,
        reason_codes=payload.reason_codes,
        entity_overlap_count=payload.entity_overlap_count,
    )

    return AlertTriageEvaluateResponse(
        alert_id="simulation_eval",
        triage_priority=res.priority.value,
        triage_action=res.action.value,
        sla_minutes=res.sla_minutes,
        triage_reasons=res.reasons,
    )


@router.get("/alerts/{alert_id}", response_model=AlertResponse)
@api_router.get("/alerts/{alert_id}", response_model=AlertResponse)
@limiter.limit("120/minute")
async def get_alert(
    request: Request,
    alert_id: str,
    caller_tenant: TenantDep = None,
) -> AlertResponse:
    """Get alert detail with tenant isolation check."""
    alert = _alert_service.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    if caller_tenant:
        enforce_tenant_isolation(caller_tenant, alert.bank_id)

    return _to_alert_response(alert)


@router.patch("/alerts/{alert_id}/status", response_model=AlertResponse)
@router.put("/alerts/{alert_id}/status", response_model=AlertResponse)
@api_router.patch("/alerts/{alert_id}/status", response_model=AlertResponse)
@api_router.put("/alerts/{alert_id}/status", response_model=AlertResponse)
@limiter.limit("60/minute")
async def update_alert_status(
    request: Request,
    alert_id: str,
    payload: AlertStatusUpdateRequest,
    caller_tenant: TenantDep = None,
) -> AlertResponse:
    """Update an alert's investigation status."""
    alert = _alert_service.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    if caller_tenant:
        enforce_tenant_isolation(caller_tenant, alert.bank_id)

    try:
        target_status = AlertStatus(payload.status)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid alert status: {payload.status!r}",
        )

    updated = _alert_service.update_alert_status(
        alert_id=alert_id,
        status=target_status,
        resolution_notes=payload.resolution_notes,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Alert not found")

    return _to_alert_response(updated)


@router.post("/alerts/{alert_id}/triage", response_model=AlertTriageEvaluateResponse)
@api_router.post("/alerts/{alert_id}/triage", response_model=AlertTriageEvaluateResponse)
@limiter.limit("60/minute")
async def evaluate_alert_triage(
    request: Request,
    alert_id: str,
    payload: AlertTriageEvaluateRequest | None = None,
    caller_tenant: TenantDep = None,
) -> AlertTriageEvaluateResponse:
    """Trigger on-demand multi-factor triage re-evaluation for an alert."""
    alert = _alert_service.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    if caller_tenant:
        enforce_tenant_isolation(caller_tenant, alert.bank_id)

    txn_override = payload.model_dump(exclude_unset=True) if payload else {}
    updated = _alert_service.triage_alert(alert_id, txn_override=txn_override)
    if not updated:
        raise HTTPException(status_code=404, detail="Alert not found")

    return AlertTriageEvaluateResponse(
        alert_id=updated.id,
        triage_priority=updated.triage_priority.value if hasattr(updated.triage_priority, "value") else str(updated.triage_priority),
        triage_action=updated.triage_action.value if hasattr(updated.triage_action, "value") else str(updated.triage_action),
        sla_minutes=updated.sla_minutes,
        triage_reasons=updated.triage_reasons,
    )


@router.get("/alerts/{alert_id}/explain", response_model=ExplainabilityResponse)
@api_router.get("/alerts/{alert_id}/explain", response_model=ExplainabilityResponse)
@limiter.limit("60/minute")
async def explain_alert(
    request: Request,
    alert_id: str,
    caller_tenant: TenantDep = None,
) -> ExplainabilityResponse:
    """Get explainability report for an alert with tenant isolation check."""
    alert = _alert_service.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    if caller_tenant:
        enforce_tenant_isolation(caller_tenant, alert.bank_id)

    report = _explainability_service.explain_alert(alert)
    breakdown_list = report.risk_score_breakdown or []
    total_weighted = sum(getattr(s, "weighted_score", 0.0) for s in breakdown_list)

    return ExplainabilityResponse(
        alert_id=report.alert_id,
        top_features=report.top_features or [],
        risk_factors=report.risk_factors or [],
        historical_evidence=report.historical_evidence or [],
        model_confidence=report.model_confidence or 0.0,
        risk_score_breakdown=[
            {
                "signal_name": getattr(s, "signal_name", "signal"),
                "weight": float(getattr(s, "weight", 0.0)),
                "raw_value": float(getattr(s, "raw_value", 0.0)),
                "normalized_score": float(getattr(s, "normalized_score", 0.0)),
                "explanation": getattr(s, "explanation", ""),
                "contribution": float(getattr(s, "weighted_score", 0.0)) / total_weighted
                if total_weighted > 0
                else 0.0,
            }
            for s in breakdown_list
        ],
        explanation_text=report.explanation_text or "",
    )


@router.get("/explanation/{transaction_id}", response_model=ExplainabilityResponse)
@api_router.get("/explanation/{transaction_id}", response_model=ExplainabilityResponse)
async def explain_transaction(
    transaction_id: str, caller_tenant: TenantDep = None
) -> ExplainabilityResponse:
    """Get explainability report for an alert by transaction ID with tenant isolation check."""
    alert = _alert_service.get_alert_by_transaction_id(transaction_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found for this transaction ID")

    if caller_tenant:
        enforce_tenant_isolation(caller_tenant, alert.bank_id)

    report = _explainability_service.explain_alert(alert)
    breakdown_list = report.risk_score_breakdown or []
    total_weighted = sum(getattr(s, "weighted_score", 0.0) for s in breakdown_list)

    return ExplainabilityResponse(
        alert_id=report.alert_id,
        top_features=report.top_features or [],
        risk_factors=report.risk_factors or [],
        historical_evidence=report.historical_evidence or [],
        model_confidence=report.model_confidence or 0.0,
        risk_score_breakdown=[
            {
                "signal_name": getattr(s, "signal_name", "signal"),
                "weight": float(getattr(s, "weight", 0.0)),
                "raw_value": float(getattr(s, "raw_value", 0.0)),
                "normalized_score": float(getattr(s, "normalized_score", 0.0)),
                "explanation": getattr(s, "explanation", ""),
                "contribution": float(getattr(s, "weighted_score", 0.0)) / total_weighted
                if total_weighted > 0
                else 0.0,
            }
            for s in breakdown_list
        ],
        explanation_text=report.explanation_text or "",
    )


@router.get("/intelligence", response_model=list[SharedIntelligenceResponse])
@api_router.get("/intelligence", response_model=list[SharedIntelligenceResponse])
async def list_intelligence(
    bank_id: str | None = Query(None, description="Filter intelligence NOT from this bank"),
) -> list[SharedIntelligenceResponse]:
    """Get shared intelligence feed."""
    if bank_id:
        items = _alert_service.consume_intelligence(bank_id)
    else:
        items = _alert_service.get_all_intelligence()

    return [
        SharedIntelligenceResponse(
            id=i.id,
            source_bank_id=i.source_bank_id,
            intelligence_type=i.intelligence_type.value,
            privacy_hash=i.privacy_hash,
            risk_indicator=i.risk_indicator,
            description=i.description,
            entity_type=i.entity_type.value if i.entity_type else None,
            related_alert_count=i.related_alert_count,
            created_at=i.created_at.isoformat(),
        )
        for i in items
    ]


@router.get("/intelligence/stats", response_model=IntelligenceStatsResponse)
@api_router.get("/intelligence/stats", response_model=IntelligenceStatsResponse)
async def intelligence_stats() -> IntelligenceStatsResponse:
    """Get shared intelligence statistics."""
    stats = _alert_service.get_intelligence_stats()
    return IntelligenceStatsResponse(**stats)


@router.get("/alerts/{alert_id}/counterfactuals", response_model=CounterfactualExplanationResponse)
@api_router.get("/alerts/{alert_id}/counterfactuals", response_model=CounterfactualExplanationResponse)
async def get_alert_counterfactuals(
    alert_id: str,
    target_score: float = Query(350.0, ge=50.0, le=800.0),
    caller_tenant: TenantDep = None,
) -> CounterfactualExplanationResponse:
    """Get actionable counterfactual remediation paths for an alert."""
    alert = _alert_service.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    if caller_tenant:
        enforce_tenant_isolation(caller_tenant, alert.bank_id)

    cf = _explainability_service.generate_counterfactuals(alert, target_score=target_score)

    return CounterfactualExplanationResponse(
        alert_id=cf.alert_id,
        original_score=cf.original_score,
        remediated_score=cf.remediated_score,
        is_cleared=cf.is_cleared,
        changes=[
            CounterfactualChangeSchema(
                feature=c.feature,
                original_value=c.original_value,
                remediated_value=c.remediated_value,
                delta_explanation=c.delta_explanation,
            )
            for c in cf.changes
        ],
        summary_text=cf.summary_text,
    )


@router.get("/alerts/{alert_id}/decision-replay", response_model=DecisionReplayResponse)
@api_router.get("/alerts/{alert_id}/decision-replay", response_model=DecisionReplayResponse)
async def replay_alert_decision(
    alert_id: str, caller_tenant: TenantDep = None
) -> DecisionReplayResponse:
    """Execute deterministic decision replay for regulatory inference audit."""
    alert = _alert_service.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    if caller_tenant:
        enforce_tenant_isolation(caller_tenant, alert.bank_id)

    audit = _explainability_service.replay_inference_audit(alert)

    return DecisionReplayResponse(
        alert_id=audit.alert_id,
        transaction_id=audit.transaction_id,
        timestamp=audit.timestamp,
        model_version=audit.model_version,
        model_auc=audit.model_auc,
        features_snapshot=audit.features_snapshot,
        graph_snapshot=audit.graph_snapshot,
        policy_rules_evaluated=[
            PolicyRuleEvaluationSchema(
                rule_code=r.rule_code,
                signal_name=r.signal_name,
                weight=r.weight,
                raw_value=r.raw_value,
                normalized_score=r.normalized_score,
                contribution=r.contribution,
                triggered=r.triggered,
            )
            for r in audit.policy_rules_evaluated
        ],
        reconstructed_risk_score=audit.reconstructed_risk_score,
        reproduced_severity=audit.reproduced_severity,
        audit_matched=audit.audit_matched,
    )


@router.get("/alerts/{alert_id}/gnn-explanation", response_model=GNNExplanationResponse)
@api_router.get("/alerts/{alert_id}/gnn-explanation", response_model=GNNExplanationResponse)
async def get_alert_gnn_explanation(
    alert_id: str, caller_tenant: TenantDep = None
) -> GNNExplanationResponse:
    """Compute GNNExplainer graph attribution for the entity associated with an alert."""
    alert = _alert_service.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    if caller_tenant:
        enforce_tenant_isolation(caller_tenant, alert.bank_id)

    node_id = (
        alert.involved_entity_ids[0] if alert.involved_entity_ids else f"entity_{alert.id[:8]}"
    )
    gnn_exp = _explainability_service.explain_gnn_embedding(node_id)

    return GNNExplanationResponse(
        node_id=gnn_exp.node_id,
        target_risk_level=gnn_exp.target_risk_level,
        subgraph_nodes_count=gnn_exp.subgraph_nodes_count,
        subgraph_edges_count=gnn_exp.subgraph_edges_count,
        top_contributing_edges=[
            EdgeContributionSchema(
                source=e.source,
                target=e.target,
                relationship_type=e.relationship_type,
                weight=e.weight,
                contribution_percentage=e.contribution_percentage,
            )
            for e in gnn_exp.top_contributing_edges
        ],
        primary_driver_text=gnn_exp.primary_driver_text,
    )


@router.get("/alerts/{alert_id}/lime-explanation", response_model=LIMEExplanationResponse)
@api_router.get("/alerts/{alert_id}/lime-explanation", response_model=LIMEExplanationResponse)
async def get_alert_lime_explanation(
    alert_id: str,
    kernel_width: float = Query(0.75, ge=0.05, le=5.0),
    num_samples: int = Query(100, ge=20, le=1000),
    caller_tenant: TenantDep = None,
) -> LIMEExplanationResponse:
    """Compute LIME local linear surrogate explanation with exponential kernel weighting."""
    alert = _alert_service.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    if caller_tenant:
        enforce_tenant_isolation(caller_tenant, alert.bank_id)

    lime_report = _explainability_service.compute_lime_explanation(
        alert=alert,
        kernel_width=kernel_width,
        num_samples=num_samples,
    )

    return LIMEExplanationResponse(
        alert_id=lime_report.alert_id,
        transaction_id=alert.transaction_id,
        intercept=lime_report.intercept,
        fidelity_r2=lime_report.fidelity_r2,
        kernel_width=lime_report.kernel_width,
        num_samples=lime_report.num_samples,
        feature_attributions=[
            LIMEFeatureAttributionSchema(
                feature=a.feature,
                weight=a.weight,
                value=a.value,
                direction=a.direction,
            )
            for a in lime_report.feature_attributions
        ],
        explanation_text=lime_report.explanation_text,
    )


@router.get("/explanation/{transaction_id}/lime", response_model=LIMEExplanationResponse)
@api_router.get("/explanation/{transaction_id}/lime", response_model=LIMEExplanationResponse)
async def get_transaction_lime_explanation(
    transaction_id: str,
    kernel_width: float = Query(0.75, ge=0.05, le=5.0),
    num_samples: int = Query(100, ge=20, le=1000),
    caller_tenant: TenantDep = None,
) -> LIMEExplanationResponse:
    """Compute LIME local linear surrogate explanation for an alert by transaction ID."""
    alert = _alert_service.get_alert_by_transaction_id(transaction_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found for this transaction ID")

    if caller_tenant:
        enforce_tenant_isolation(caller_tenant, alert.bank_id)

    lime_report = _explainability_service.compute_lime_explanation(
        alert=alert,
        kernel_width=kernel_width,
        num_samples=num_samples,
    )

    return LIMEExplanationResponse(
        alert_id=lime_report.alert_id,
        transaction_id=alert.transaction_id,
        intercept=lime_report.intercept,
        fidelity_r2=lime_report.fidelity_r2,
        kernel_width=lime_report.kernel_width,
        num_samples=lime_report.num_samples,
        feature_attributions=[
            LIMEFeatureAttributionSchema(
                feature=a.feature,
                weight=a.weight,
                value=a.value,
                direction=a.direction,
            )
            for a in lime_report.feature_attributions
        ],
        explanation_text=lime_report.explanation_text,
    )
