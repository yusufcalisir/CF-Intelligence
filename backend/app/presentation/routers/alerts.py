"""Alert and intelligence API endpoints.

Manages fraud alerts and shared cross-institution intelligence.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.application.schemas.phase2 import (
    AlertResponse,
    CounterfactualChangeSchema,
    CounterfactualExplanationResponse,
    DecisionReplayResponse,
    EdgeContributionSchema,
    ExplainabilityResponse,
    GNNExplanationResponse,
    IntelligenceStatsResponse,
    PolicyRuleEvaluationSchema,
    SharedIntelligenceResponse,
)
from app.application.services.alert_service import AlertIntelligenceService
from app.application.services.explainability_service import ExplainabilityService
from app.domain.enums import AlertSeverity, AlertStatus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["alerts"])

# Shared service instances (singleton pattern matching Phase 1)
_alert_service = AlertIntelligenceService()
_explainability_service = ExplainabilityService()


def get_alert_service() -> AlertIntelligenceService:
    return _alert_service


@router.get("/alerts", response_model=list[AlertResponse])
async def list_alerts(
    bank_id: str | None = Query(None),
    severity: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> list[AlertResponse]:
    """List fraud alerts with optional filters."""
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
        bank_id=bank_id,
        severity=sev,
        status=stat,
        limit=limit,
    )

    return [
        AlertResponse(
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
            top_features=a.top_features,
            risk_factors=a.risk_factors,
            model_confidence=a.model_confidence,
        )
        for a in alerts
    ]


@router.get("/alerts/{alert_id}", response_model=AlertResponse)
async def get_alert(alert_id: str) -> AlertResponse:
    """Get alert detail."""
    alert = _alert_service.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    return AlertResponse(
        id=alert.id,
        bank_id=alert.bank_id,
        transaction_id=alert.transaction_id,
        risk_score=alert.risk_score,
        severity=alert.severity.value,
        status=alert.status.value,
        reason_codes=alert.reason_codes,
        confidence=alert.confidence,
        involved_entity_ids=alert.involved_entity_ids,
        created_at=alert.created_at.isoformat(),
        top_features=alert.top_features,
        risk_factors=alert.risk_factors,
        model_confidence=alert.model_confidence,
    )


@router.get("/alerts/{alert_id}/explain", response_model=ExplainabilityResponse)
async def explain_alert(alert_id: str) -> ExplainabilityResponse:
    """Get explainability report for an alert."""
    alert = _alert_service.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

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
async def explain_transaction(transaction_id: str) -> ExplainabilityResponse:
    """Get explainability report for an alert by transaction ID."""
    alert = _alert_service.get_alert_by_transaction_id(transaction_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found for this transaction ID")

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
async def intelligence_stats() -> IntelligenceStatsResponse:
    """Get shared intelligence statistics."""
    stats = _alert_service.get_intelligence_stats()
    return IntelligenceStatsResponse(**stats)


@router.get("/alerts/{alert_id}/counterfactuals", response_model=CounterfactualExplanationResponse)
async def get_alert_counterfactuals(
    alert_id: str,
    target_score: float = Query(350.0, ge=50.0, le=800.0),
) -> CounterfactualExplanationResponse:
    """Get actionable counterfactual remediation paths for an alert."""
    alert = _alert_service.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

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
async def replay_alert_decision(alert_id: str) -> DecisionReplayResponse:
    """Execute deterministic decision replay for regulatory inference audit."""
    alert = _alert_service.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

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
async def get_alert_gnn_explanation(alert_id: str) -> GNNExplanationResponse:
    """Compute GNNExplainer graph attribution for the entity associated with an alert."""
    alert = _alert_service.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

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
