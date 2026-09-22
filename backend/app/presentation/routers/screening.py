"""Sanctions & PEP Screening API Router (Phase 108).

Endpoints:
- POST   /api/v1/screening/watchlists          — load watchlist entries
- GET    /api/v1/screening/watchlists/counts   — counts per source
- POST   /api/v1/screening/screen              — screen single entity
- GET    /api/v1/screening/results             — list results (alerted/source filter)
- GET    /api/v1/screening/results/{id}        — get full result
- POST   /api/v1/screening/results/{id}/goodlist        — goodlist a hit
- POST   /api/v1/screening/results/{id}/disposition     — update hit disposition
- POST   /api/v1/screening/bulk-rescreen       — portfolio re-screening
- GET    /api/v1/screening/metrics             — aggregate metrics

All endpoints also available under /v1/screening for compatibility.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.application.schemas.screening_schemas import (
    BulkRescreenRequest,
    BulkRescreenResponse,
    GoodlistEntryResponse,
    GoodlistRequest,
    LoadWatchlistRequest,
    ScreenEntityRequest,
    ScreeningHitResponse,
    ScreeningMetricsResponse,
    ScreeningResultResponse,
    ScreeningResultSummaryResponse,
    UpdateDispositionRequest,
    WatchlistCountsResponse,
)
from app.application.services.screening_service import (
    ScreeningResult,
    ScreeningService,
    WatchlistEntry,
    get_screening_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/screening", tags=["sanctions-pep-screening"])
api_router = APIRouter(prefix="/v1/screening", tags=["sanctions-pep-screening"])


# ── Dependency ─────────────────────────────────────────────────────────────────

def _svc() -> ScreeningService:
    return get_screening_service()


# ── Serialisation helpers ──────────────────────────────────────────────────────

def _hit_resp(h) -> ScreeningHitResponse:
    return ScreeningHitResponse(
        hit_id=h.hit_id, watchlist_entry_id=h.watchlist_entry_id,
        source=h.source, matched_name=h.matched_name, matched_alias=h.matched_alias,
        score=h.score, algorithm=h.algorithm, entity_type=h.entity_type,
        disposition=h.disposition, reviewed_by=h.reviewed_by, reviewed_at=h.reviewed_at,
        notes=h.notes, listed_by=h.listed_by,
    )


def _result_resp(r: ScreeningResult) -> ScreeningResultResponse:
    return ScreeningResultResponse(
        result_id=r.result_id, entity_key_hash=r.entity_key_hash,
        query_name=r.query_name, entity_type=r.entity_type,
        status=r.status, sources_checked=r.sources_checked,
        hits=[_hit_resp(h) for h in r.hits],
        top_score=r.top_score, alert_threshold=r.alert_threshold,
        is_alerted=r.is_alerted, goodlisted=r.goodlisted,
        screening_duration_ms=r.screening_duration_ms,
        created_at=r.created_at, completed_at=r.completed_at,
        metadata=r.metadata,
    )


def _result_summary(r: ScreeningResult) -> ScreeningResultSummaryResponse:
    return ScreeningResultSummaryResponse(
        result_id=r.result_id, entity_key_hash=r.entity_key_hash,
        query_name=r.query_name, entity_type=r.entity_type,
        status=r.status, top_score=r.top_score,
        is_alerted=r.is_alerted, goodlisted=r.goodlisted,
        hit_count=len(r.hits), screening_duration_ms=r.screening_duration_ms,
        created_at=r.created_at,
    )


# ── Route factory ──────────────────────────────────────────────────────────────

def _register_routes(r: APIRouter) -> None:

    @r.post(
        "/watchlists",
        status_code=201,
        summary="Load Watchlist Entries",
        description=(
            "Load or replace sanctions/PEP entries for a given source. "
            "Supports EU Consolidated, UN, OFAC SDN, HM Treasury, PEP Global."
        ),
    )
    async def load_watchlist(request: LoadWatchlistRequest) -> dict:
        svc = _svc()
        entries = [
            WatchlistEntry(
                entry_id=e.entry_id or e.primary_name[:36],
                source=e.source,
                entity_type=e.entity_type,
                primary_name=e.primary_name,
                aliases=e.aliases,
                date_of_birth=e.date_of_birth,
                nationalities=e.nationalities,
                listing_date=e.listing_date,
                listed_by=e.listed_by,
                additional_info=e.additional_info,
            )
            for e in request.entries
        ]
        count = svc.load_watchlist_entries(request.source, entries)
        return {"source": request.source, "loaded": count}

    @r.get(
        "/watchlists/counts",
        response_model=WatchlistCountsResponse,
        summary="Watchlist Entry Counts",
    )
    async def watchlist_counts() -> WatchlistCountsResponse:
        return WatchlistCountsResponse(counts=_svc().get_watchlist_counts())

    @r.post(
        "/screen",
        response_model=ScreeningResultResponse,
        status_code=200,
        summary="Screen Entity Against Sanctions & PEP Lists",
        description=(
            "Screens an entity name against selected watchlist sources using "
            "Levenshtein, Jaro-Winkler, Double Metaphone, and transliteration algorithms. "
            "Returns all hits with scores 40–100; alerts if top score ≥ threshold (default 75)."
        ),
    )
    async def screen_entity(request: ScreenEntityRequest) -> ScreeningResultResponse:
        svc = _svc()
        try:
            result = svc.screen_entity(
                query_name=request.query_name,
                entity_type=request.entity_type,
                date_of_birth=request.date_of_birth,
                nationalities=request.nationalities,
                sources=list(request.sources) if request.sources else None,
                alert_threshold=request.alert_threshold,
                actor=request.actor,
                metadata=request.metadata,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _result_resp(result)

    @r.get(
        "/results",
        response_model=list[ScreeningResultSummaryResponse],
        summary="List Screening Results",
    )
    async def list_results(
        alerted_only: bool = Query(default=False),
        source: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> list[ScreeningResultSummaryResponse]:
        return [_result_summary(r) for r in _svc().list_results(alerted_only, source, limit)]

    @r.get(
        "/results/{result_id}",
        response_model=ScreeningResultResponse,
        summary="Get Full Screening Result",
    )
    async def get_result(result_id: str) -> ScreeningResultResponse:
        try:
            return _result_resp(_svc().get_result(result_id))
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Screening result '{result_id}' not found.")

    @r.post(
        "/results/{result_id}/goodlist",
        response_model=GoodlistEntryResponse,
        status_code=200,
        summary="Goodlist a Screening Hit (False-Positive Suppression)",
    )
    async def goodlist_hit(result_id: str, request: GoodlistRequest) -> GoodlistEntryResponse:
        try:
            entry = _svc().goodlist_entity(result_id, request.hit_id, request.approved_by, request.notes)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return GoodlistEntryResponse(
            goodlist_id=entry.goodlist_id, entity_key_hash=entry.entity_key_hash,
            approved_by=entry.approved_by, approved_at=entry.approved_at,
            watchlist_entry_id=entry.watchlist_entry_id, notes=entry.notes,
        )

    @r.post(
        "/results/{result_id}/disposition",
        response_model=ScreeningHitResponse,
        status_code=200,
        summary="Update Screening Hit Disposition",
    )
    async def update_disposition(result_id: str, request: UpdateDispositionRequest) -> ScreeningHitResponse:
        try:
            hit = _svc().update_hit_disposition(
                result_id, request.hit_id, request.disposition, request.reviewed_by, request.notes
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _hit_resp(hit)

    @r.post(
        "/bulk-rescreen",
        response_model=BulkRescreenResponse,
        status_code=200,
        summary="Portfolio Bulk Re-Screening",
        description=(
            "Re-screens all registered entities against the current watchlist state. "
            "Useful after a new watchlist publication cycle (e.g., monthly EU Consolidated refresh)."
        ),
    )
    async def bulk_rescreen(request: BulkRescreenRequest) -> BulkRescreenResponse:
        svc = _svc()
        results = svc.bulk_rescreen(
            sources=list(request.sources) if request.sources else None,
            alert_threshold=request.alert_threshold,
        )
        summaries = [_result_summary(r) for r in results.values()]
        return BulkRescreenResponse(
            total_rescreened=len(summaries),
            total_alerted=sum(1 for s in summaries if s.is_alerted),
            results=summaries,
        )

    @r.get(
        "/metrics",
        response_model=ScreeningMetricsResponse,
        summary="Screening Engine Metrics",
    )
    async def get_metrics() -> ScreeningMetricsResponse:
        return ScreeningMetricsResponse(**_svc().get_metrics())


_register_routes(router)
_register_routes(api_router)
