"""Investigation dashboard API endpoints.

Aggregates data from alerts, cases, entities, and shared intelligence
into executive dashboard statistics and high-dimensional risk visualizations.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from fastapi import APIRouter, Query, status

from app.application.schemas.dashboard import (
    DashboardStatsResponse,
    MerchantRiskItem,
    RiskWeightsResponse,
    RiskWeightsUpdateRequest,
)
from app.application.services.risk_engine import RiskScoringEngine
from app.domain.enums import EntityType
from app.domain.value_objects_phase2 import RiskWeightConfig
from app.presentation.routers.alerts import get_alert_service
from app.presentation.routers.cases import get_case_service
from app.presentation.routers.entities import get_entity_service
from app.presentation.routers.graph import get_graph_engine
from app.presentation.routers.scenarios import get_streaming_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])

_risk_engine = RiskScoringEngine()


def get_risk_engine() -> RiskScoringEngine:
    """Dependency provider returning singleton RiskScoringEngine instance."""
    return _risk_engine


@router.get(
    "/stats",
    response_model=DashboardStatsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get aggregated dashboard statistics",
)
async def dashboard_stats(
    bank_id: str | None = Query(None, min_length=2, max_length=64, description="Optional bank tenant filter"),
) -> DashboardStatsResponse:
    """Get aggregated executive dashboard statistics across consortium institutions."""
    alert_svc = get_alert_service()
    case_svc = get_case_service()
    entity_svc = get_entity_service()
    graph = get_graph_engine()
    streaming = get_streaming_engine()

    raw_alerts = alert_svc.get_alerts(limit=1000)
    all_alerts = [a for a in raw_alerts if not bank_id or a.bank_id == bank_id]
    critical_alerts = [a for a in all_alerts if getattr(a.severity, "value", str(a.severity)).lower() in ("critical", "high")]

    raw_cases = case_svc.get_cases(limit=1000)
    open_cases = [c for c in raw_cases if c.is_open and (not bank_id or getattr(c, "bank_id", None) == bank_id)]

    raw_entities = entity_svc.get_entities(limit=1000)
    all_entities = [e for e in raw_entities if not bank_id or e.bank_id == bank_id]

    intel_stats = alert_svc.get_intelligence_stats()
    clusters = graph.detect_clusters(min_size=2)
    active = streaming.get_active_scenarios()

    # Dynamic cross-institution matching:
    # 1. Privacy IDs resolved across multiple distinct bank tenants
    privacy_to_banks: dict[str, set[str]] = defaultdict(set)
    for e in raw_entities:
        if e.privacy_id and e.bank_id:
            privacy_to_banks[e.privacy_id].add(e.bank_id)

    cross_matches = 0
    for p_id, banks in privacy_to_banks.items():
        if len(banks) > 1 and (not bank_id or bank_id in banks):
            cross_matches += 1

    # 2. Add clusters with entities spanning distinct bank tenants
    entity_map = {e.id: e for e in raw_entities}
    for cluster in clusters:
        cluster_banks = {entity_map[node_id].bank_id for node_id in cluster if node_id in entity_map and entity_map[node_id].bank_id}
        if len(cluster_banks) > 1 and (not bank_id or bank_id in cluster_banks):
            cross_matches += 1

    return DashboardStatsResponse(
        total_alerts=len(all_alerts),
        critical_alerts=len(critical_alerts),
        open_cases=len(open_cases),
        total_entities=len(all_entities),
        shared_intelligence_items=intel_stats.get("total_items", 0),
        cross_institution_matches=cross_matches,
        active_scenarios=len(active),
        graph_clusters=len(clusters),
    )


@router.get(
    "/alerts-by-severity",
    response_model=dict[str, int],
    status_code=status.HTTP_200_OK,
    summary="Alert count grouped by severity",
)
async def alerts_by_severity(
    bank_id: str | None = Query(None, min_length=2, max_length=64, description="Optional bank tenant filter"),
    limit: int = Query(1000, ge=1, le=10000, description="Max alerts to inspect"),
) -> dict[str, int]:
    """Alert count grouped by severity level."""
    alert_svc = get_alert_service()
    raw_alerts = alert_svc.get_alerts(limit=limit)
    alerts = [a for a in raw_alerts if not bank_id or a.bank_id == bank_id]

    counts: dict[str, int] = defaultdict(int)
    for a in alerts:
        sev = getattr(a.severity, "value", str(a.severity)).lower()
        counts[sev] += 1
    return dict(counts)


@router.get(
    "/alerts-by-bank",
    response_model=dict[str, int],
    status_code=status.HTTP_200_OK,
    summary="Alert count grouped by bank",
)
async def alerts_by_bank(
    limit: int = Query(1000, ge=1, le=10000, description="Max alerts to inspect"),
) -> dict[str, int]:
    """Alert count grouped by bank institution."""
    alert_svc = get_alert_service()
    alerts = alert_svc.get_alerts(limit=limit)
    counts: dict[str, int] = defaultdict(int)
    for a in alerts:
        b_id = a.bank_id or "unknown"
        counts[b_id] += 1
    return dict(counts)


@router.get(
    "/entities-by-risk",
    response_model=dict[str, int],
    status_code=status.HTTP_200_OK,
    summary="Entity count grouped by risk level",
)
async def entities_by_risk(
    bank_id: str | None = Query(None, min_length=2, max_length=64, description="Optional bank tenant filter"),
    limit: int = Query(1000, ge=1, le=10000, description="Max entities to inspect"),
) -> dict[str, int]:
    """Entity count grouped by risk level."""
    entity_svc = get_entity_service()
    raw_entities = entity_svc.get_entities(limit=limit)
    entities = [e for e in raw_entities if not bank_id or e.bank_id == bank_id]

    counts: dict[str, int] = defaultdict(int)
    for e in entities:
        lvl = getattr(e.risk_level, "value", str(e.risk_level)).lower()
        counts[lvl] += 1
    return dict(counts)


@router.get(
    "/top-risky-merchants",
    response_model=list[MerchantRiskItem],
    status_code=status.HTTP_200_OK,
    summary="Top merchants by alert involvement",
)
async def top_risky_merchants(
    bank_id: str | None = Query(None, min_length=2, max_length=64, description="Optional bank tenant filter"),
    limit: int = Query(10, ge=1, le=100, description="Maximum number of top risky merchants to return"),
) -> list[MerchantRiskItem]:
    """Top merchants by alert involvement and risk severity."""
    alert_svc = get_alert_service()
    entity_svc = get_entity_service()

    raw_alerts = alert_svc.get_alerts(limit=1000)
    alerts = [a for a in raw_alerts if not bank_id or a.bank_id == bank_id]

    raw_entities = entity_svc.get_entities(limit=1000)
    entities = {e.id: e for e in raw_entities}

    merchant_counts: dict[str, int] = defaultdict(int)
    for a in alerts:
        # Check involved entities for merchant type
        matched_merchant = False
        for entity_id in a.involved_entity_ids:
            ent = entities.get(entity_id)
            if ent and ent.entity_type == EntityType.MERCHANT:
                label = ent.attributes.get("merchant_name") or ent.display_label or f"MERCH-{ent.id[:8]}"
                merchant_counts[str(label)] += 1
                matched_merchant = True

        # If not matched via entity graph, inspect top features
        if not matched_merchant:
            for feat in getattr(a, "top_features", []):
                if isinstance(feat, dict) and feat.get("feature") in ("merchant_id", "merchant_category", "merchant"):
                    val = feat.get("value")
                    if val:
                        merchant_counts[str(val)] += 1
                        matched_merchant = True
                        break

        # Fallback to specific merchant risk reason codes
        if not matched_merchant:
            for code in getattr(a, "reason_codes", []):
                if "MERCH" in code:
                    merchant_counts[f"Merchant Group ({code})"] += 1

    sorted_items = sorted(merchant_counts.items(), key=lambda x: x[1], reverse=True)[:limit]
    return [MerchantRiskItem(merchant=k, alert_count=v) for k, v in sorted_items]


@router.get(
    "/risk-weights",
    response_model=RiskWeightsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get current risk scoring weights",
)
async def get_risk_weights() -> RiskWeightsResponse:
    """Get current composite risk scoring weights."""
    w = _risk_engine.weights
    data = w.to_dict()
    return RiskWeightsResponse(**data)


@router.put(
    "/risk-weights",
    response_model=RiskWeightsResponse,
    status_code=status.HTTP_200_OK,
    summary="Update risk scoring weights",
)
async def update_risk_weights(req: RiskWeightsUpdateRequest) -> RiskWeightsResponse:
    """Update composite risk scoring weights in the scoring engine."""
    new_weights = RiskWeightConfig(
        ml_prediction=req.ml_prediction,
        velocity_rules=req.velocity_rules,
        merchant_reputation=req.merchant_reputation,
        country_risk=req.country_risk,
        device_anomaly=req.device_anomaly,
        customer_history=req.customer_history,
        previous_alerts=req.previous_alerts,
        chargeback_history=req.chargeback_history,
        behavior_anomaly=req.behavior_anomaly,
        gnn_topological_risk=req.gnn_topological_risk,
    )
    _risk_engine.update_weights(new_weights)
    return RiskWeightsResponse(**new_weights.to_dict())
