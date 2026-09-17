"""Feedback and retraining ground-truth endpoints.

Exposes REST APIs for ingesting analyst determinations, monitoring local tenant feedback
buffers, sampling prioritized retraining batches, and computing Differential-Privacy-protected
gradient updates for continuous federated learning.
Supports dual routing: /api/v1/feedback and /v1/feedback.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from app.application.schemas.feedback import (
    AnalystFeedbackIngestRequest,
    AnalystFeedbackIngestResponse,
    ClearFeedbackBufferResponse,
    DPGradientRequest,
    DPGradientResponse,
    FeedbackStatsResponse,
    RetrainingBatchRequest,
    RetrainingBatchResponse,
)
from app.application.services.label_feedback_pipeline import (
    LocalLabelFeedbackPipeline,
)
from app.domain.label_privacy_guard import LabelPrivacyViolationError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])
api_router = APIRouter(prefix="/v1/feedback", tags=["feedback"])

_pipeline = LocalLabelFeedbackPipeline()


# ── Ingestion & Submission ──────────────────────────────────────────────────

async def _ingest_handler(payload: AnalystFeedbackIngestRequest) -> AnalystFeedbackIngestResponse:
    """Core handler ingesting an analyst determination into local tenant feedback buffer."""
    try:
        item = _pipeline.ingest_analyst_determination(
            tenant_id=payload.tenant_id,
            transaction_id_hash=payload.transaction_id_hash,
            determination=payload.determination,
            alert_id=payload.alert_id,
            priority=payload.priority,
            weight=payload.weight,
            feature_vector=payload.feature_vector,
            notes=payload.notes,
            raw_attributes=payload.raw_attributes,
            metadata=payload.metadata,
        )
        return AnalystFeedbackIngestResponse(status="success", item=item.to_dict())
    except LabelPrivacyViolationError as exc:
        logger.warning("Zero-PII violation rejected in feedback ingestion: %s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.error("Failed to ingest feedback: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to ingest feedback: {exc}",
        )


@router.post(
    "/ingest",
    response_model=AnalystFeedbackIngestResponse,
    status_code=status.HTTP_201_CREATED,
)
@api_router.post(
    "/ingest",
    response_model=AnalystFeedbackIngestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_feedback(payload: AnalystFeedbackIngestRequest) -> AnalystFeedbackIngestResponse:
    """Ingest an analyst determination into the tenant's local label feedback buffer."""
    return await _ingest_handler(payload)


@router.post(
    "/submit",
    response_model=AnalystFeedbackIngestResponse,
    status_code=status.HTTP_201_CREATED,
)
@api_router.post(
    "/submit",
    response_model=AnalystFeedbackIngestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_feedback(payload: AnalystFeedbackIngestRequest) -> AnalystFeedbackIngestResponse:
    """Confirm ground truth fraud/clean labels and push to retraining priority queue (API_REGISTRY.md alias)."""
    return await _ingest_handler(payload)


# ── Statistics & Monitoring ─────────────────────────────────────────────────

def _get_stats_handler(tenant_id: str) -> FeedbackStatsResponse:
    """Core handler retrieving summary metrics for a tenant's feedback store."""
    clean_tenant = tenant_id.strip() or "bank_alpha"
    stats = _pipeline.get_buffer_stats(tenant_id=clean_tenant)
    return FeedbackStatsResponse(**stats)


@router.get(
    "/stats",
    response_model=FeedbackStatsResponse,
    status_code=status.HTTP_200_OK,
)
@api_router.get(
    "/stats",
    response_model=FeedbackStatsResponse,
    status_code=status.HTTP_200_OK,
)
async def get_feedback_stats_query(
    tenant_id: str = Query(default="bank_alpha", description="Target bank tenant ID"),
) -> FeedbackStatsResponse:
    """Return label distribution and ground truth buffer occupancy (API_REGISTRY.md endpoint)."""
    return _get_stats_handler(tenant_id)


@router.get(
    "/stats/{tenant_id}",
    response_model=FeedbackStatsResponse,
    status_code=status.HTTP_200_OK,
)
@api_router.get(
    "/stats/{tenant_id}",
    response_model=FeedbackStatsResponse,
    status_code=status.HTTP_200_OK,
)
async def get_feedback_stats(tenant_id: str) -> FeedbackStatsResponse:
    """Retrieve summary metrics and class balance for a tenant's feedback store by path."""
    return _get_stats_handler(tenant_id)


# ── Retraining Batch Sampling ───────────────────────────────────────────────

@router.post(
    "/retraining-batch",
    response_model=RetrainingBatchResponse,
    status_code=status.HTTP_200_OK,
)
@api_router.post(
    "/retraining-batch",
    response_model=RetrainingBatchResponse,
    status_code=status.HTTP_200_OK,
)
async def sample_retraining_batch(payload: RetrainingBatchRequest) -> RetrainingBatchResponse:
    """Sample a prioritized, stratified batch of human-verified feedback items for local model retraining."""
    batch_data = _pipeline.get_priority_retraining_batch(
        tenant_id=payload.tenant_id,
        batch_size=payload.batch_size,
        mark_consumed=payload.mark_consumed,
        min_priority=payload.min_priority,
        stratified=payload.stratified,
    )
    return RetrainingBatchResponse(**batch_data)


# ── Differential Privacy Gradients ─────────────────────────────────────────

@router.post(
    "/dp-gradient",
    response_model=DPGradientResponse,
    status_code=status.HTTP_200_OK,
)
@api_router.post(
    "/dp-gradient",
    response_model=DPGradientResponse,
    status_code=status.HTTP_200_OK,
)
async def compute_dp_gradient(payload: DPGradientRequest) -> DPGradientResponse:
    """Compute Differential-Privacy-protected gradient update for tenant's local model."""
    try:
        grad_data = _pipeline.compute_dp_gradient_update(
            tenant_id=payload.tenant_id,
            epsilon=payload.epsilon,
            delta=payload.delta,
            clip_norm=payload.clip_norm,
        )
        return DPGradientResponse(**grad_data)
    except LabelPrivacyViolationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.error("Failed to compute DP gradient update: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to compute DP gradient update: {exc}",
        )


# ── Buffer Management ───────────────────────────────────────────────────────

@router.delete(
    "/buffer/{tenant_id}",
    response_model=ClearFeedbackBufferResponse,
    status_code=status.HTTP_200_OK,
)
@api_router.delete(
    "/buffer/{tenant_id}",
    response_model=ClearFeedbackBufferResponse,
    status_code=status.HTTP_200_OK,
)
async def clear_feedback_buffer(tenant_id: str) -> ClearFeedbackBufferResponse:
    """Clear all feedback items from the tenant's buffer."""
    cleared = _pipeline.clear_buffer(tenant_id=tenant_id)
    return ClearFeedbackBufferResponse(status="success", tenant_id=tenant_id, cleared_count=cleared)
