"""Asset Recovery & Collaborative FININT Operational Hub — REST Router.

Exposes the operational ROI metrics and MTTR analytics for the SaaS
investigation dashboard and live telemetry card.

Routes (dual-prefix for backward compatibility):
    GET  /operations/asset-recovery/summary
    GET  /api/v1/operations/asset-recovery/summary
    GET  /operations/asset-recovery/timeline
    GET  /api/v1/operations/asset-recovery/timeline
    GET  /operations/asset-recovery/breakdown-by-typology
    GET  /api/v1/operations/asset-recovery/breakdown-by-typology
    POST /api/v1/operations/asset-recovery/events
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from app.application.services.asset_recovery_service import (
    AssetRecoveryService,
    get_asset_recovery_service,
)

logger = logging.getLogger(__name__)

# ── Pydantic response/request schemas ─────────────────────────────────────────


class AssetRecoverySummaryResponse(BaseModel):
    """Aggregated KPI snapshot for the operational hub dashboard."""

    snapshot_at: str
    total_events: int
    total_eur_frozen: float
    total_eur_recovered: float
    contagion_containment_rate: float = Field(
        description="Fraction of mule chains terminated before second hop (0–1)"
    )
    mttr_mean_minutes: float
    mttr_p50_minutes: float
    mttr_p90_minutes: float
    mttr_p99_minutes: float
    legacy_baseline_minutes: float
    mttr_reduction_pct: float = Field(
        description="MTTR improvement % vs. 48-hour bilateral legacy baseline"
    )
    mule_chains_disrupted: int
    consortium_banks_active: int
    active_provisional_holds: int


class TimelineDataPointResponse(BaseModel):
    """Hourly/daily aggregated data point for chart rendering."""

    period_start: str
    eur_frozen: float
    eur_recovered: float
    event_count: int
    avg_mttr_minutes: float


class TypologyBreakdownResponse(BaseModel):
    """Per-typology ROI and MTTR breakdown row."""

    typology: str
    event_count: int
    total_eur: float
    avg_mttr_minutes: float
    containment_rate: float
    risk_label: str


class RecordRecoveryEventRequest(BaseModel):
    """Request body for recording a new asset recovery event."""

    event_type: str = Field(
        description="RECALL_SUCCESS | PROVISIONAL_HOLD | PARTIAL_RECOVERY"
    )
    amount_eur: str = Field(description="EUR amount with up to 2 decimal places")
    typology: str = Field(description="AML typology label")
    originating_bank_id: str
    receiving_bank_id: str
    recall_message_id: str = ""
    finint_ticket_id: str = ""

    @field_validator("amount_eur")
    @classmethod
    def validate_amount(cls, v: str) -> str:
        try:
            d = Decimal(v)
        except Exception as exc:
            raise ValueError(f"Invalid EUR amount: {v!r}") from exc
        if d <= 0:
            raise ValueError("EUR amount must be positive")
        return v


class RecordRecoveryEventResponse(BaseModel):
    """Response confirming event recording."""

    event_id: str
    event_type: str
    amount_eur: float
    typology: str
    mttr_minutes: float | None
    audit_hash: str
    recorded_at: str


# ── Dependency ─────────────────────────────────────────────────────────────────


def _get_service() -> AssetRecoveryService:
    return get_asset_recovery_service()


# ── Routers ────────────────────────────────────────────────────────────────────

router = APIRouter(
    prefix="/operations/asset-recovery",
    tags=["Asset Recovery & FININT Operations"],
)

api_router = APIRouter(
    prefix="/api/v1/operations/asset-recovery",
    tags=["Asset Recovery & FININT Operations"],
)


# ── Endpoints ──────────────────────────────────────────────────────────────────


def _build_summary_response(svc: AssetRecoveryService) -> AssetRecoverySummaryResponse:
    summary = svc.get_summary()
    return AssetRecoverySummaryResponse(
        snapshot_at=summary.snapshot_at.isoformat(),
        total_events=summary.total_events,
        total_eur_frozen=float(summary.total_eur_frozen),
        total_eur_recovered=float(summary.total_eur_recovered),
        contagion_containment_rate=summary.contagion_containment_rate,
        mttr_mean_minutes=summary.mttr_mean_minutes,
        mttr_p50_minutes=summary.mttr_p50_minutes,
        mttr_p90_minutes=summary.mttr_p90_minutes,
        mttr_p99_minutes=summary.mttr_p99_minutes,
        legacy_baseline_minutes=summary.legacy_baseline_minutes,
        mttr_reduction_pct=summary.mttr_reduction_pct,
        mule_chains_disrupted=summary.mule_chains_disrupted,
        consortium_banks_active=summary.consortium_banks_active,
        active_provisional_holds=summary.active_provisional_holds,
    )


@router.get("/summary", response_model=AssetRecoverySummaryResponse)
async def get_recovery_summary() -> AssetRecoverySummaryResponse:
    """Return aggregated EUR asset recovery KPI snapshot.

    Metrics include total EUR frozen/recovered, MTTR statistics vs. the
    legacy 48-hour bilateral baseline, and mule chain containment rate.
    """
    svc = _get_service()
    return _build_summary_response(svc)


@api_router.get("/summary", response_model=AssetRecoverySummaryResponse)
async def get_recovery_summary_v1() -> AssetRecoverySummaryResponse:
    """Return aggregated EUR asset recovery KPI snapshot (API v1 prefix)."""
    svc = _get_service()
    return _build_summary_response(svc)


def _build_timeline_response(
    svc: AssetRecoveryService, window_hours: int
) -> list[TimelineDataPointResponse]:
    points = svc.get_timeline(window_hours=window_hours)
    return [
        TimelineDataPointResponse(
            period_start=p.period_start,
            eur_frozen=p.eur_frozen,
            eur_recovered=p.eur_recovered,
            event_count=p.event_count,
            avg_mttr_minutes=p.avg_mttr_minutes,
        )
        for p in points
    ]


@router.get("/timeline", response_model=list[TimelineDataPointResponse])
async def get_recovery_timeline(
    window_hours: int = Query(720, ge=1, le=8760, description="Lookback window in hours"),
) -> list[TimelineDataPointResponse]:
    """Return time-series asset recovery data for chart rendering.

    Default window is 720 hours (30 days). Maximum is 8760 hours (1 year).
    """
    svc = _get_service()
    return _build_timeline_response(svc, window_hours)


@api_router.get("/timeline", response_model=list[TimelineDataPointResponse])
async def get_recovery_timeline_v1(
    window_hours: int = Query(720, ge=1, le=8760, description="Lookback window in hours"),
) -> list[TimelineDataPointResponse]:
    """Return time-series asset recovery data (API v1 prefix)."""
    svc = _get_service()
    return _build_timeline_response(svc, window_hours)


def _build_breakdown_response(
    svc: AssetRecoveryService,
) -> list[TypologyBreakdownResponse]:
    breakdown = svc.get_breakdown_by_typology()
    return [
        TypologyBreakdownResponse(
            typology=b.typology,
            event_count=b.event_count,
            total_eur=b.total_eur,
            avg_mttr_minutes=b.avg_mttr_minutes,
            containment_rate=b.containment_rate,
            risk_label=b.risk_label,
        )
        for b in breakdown
    ]


@router.get("/breakdown-by-typology", response_model=list[TypologyBreakdownResponse])
async def get_breakdown_by_typology() -> list[TypologyBreakdownResponse]:
    """Return per-typology ROI breakdown ordered by EUR amount descending."""
    svc = _get_service()
    return _build_breakdown_response(svc)


@api_router.get("/breakdown-by-typology", response_model=list[TypologyBreakdownResponse])
async def get_breakdown_by_typology_v1() -> list[TypologyBreakdownResponse]:
    """Return per-typology ROI breakdown (API v1 prefix)."""
    svc = _get_service()
    return _build_breakdown_response(svc)


@api_router.post("/events", response_model=RecordRecoveryEventResponse, status_code=201)
async def record_recovery_event(
    body: RecordRecoveryEventRequest,
) -> RecordRecoveryEventResponse:
    """Record a new asset recovery or provisional hold event.

    Use this endpoint when a camt.056 recall is confirmed successful or a
    provisional hold is placed via the payment rails webhook dispatcher.
    """
    svc = _get_service()
    try:
        evt = svc.record_recovery_event(
            event_type=body.event_type,
            amount_eur=body.amount_eur,
            typology=body.typology,
            originating_bank_id=body.originating_bank_id,
            receiving_bank_id=body.receiving_bank_id,
            recall_message_id=body.recall_message_id,
            finint_ticket_id=body.finint_ticket_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return RecordRecoveryEventResponse(
        event_id=evt.event_id,
        event_type=evt.event_type,
        amount_eur=float(evt.amount_eur),
        typology=evt.typology,
        mttr_minutes=evt.mttr_minutes,
        audit_hash=evt.audit_hash,
        recorded_at=datetime.now(UTC).isoformat(),
    )
