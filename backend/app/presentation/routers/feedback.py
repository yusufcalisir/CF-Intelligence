"""Feedback and retraining ground-truth endpoints.

Exposes REST APIs for ingesting analyst determinations, monitoring local tenant feedback
buffers, sampling prioritized retraining batches, and computing Differential-Privacy-protected
gradient updates for continuous federated learning.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.application.services.label_feedback_pipeline import (
    LocalLabelFeedbackPipeline,
)
from app.domain.label_privacy_guard import LabelPrivacyViolationError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])
_pipeline = LocalLabelFeedbackPipeline()


class AnalystFeedbackIngestRequest(BaseModel):
    """Payload for submitting an analyst ground-truth determination."""

    tenant_id: str = Field(default="bank_alpha", description="Target bank tenant ID")
    transaction_id_hash: str | None = Field(default=None, description="HMAC-SHA256 hashed transaction ID")
    alert_id: str | None = Field(default=None, description="Investigation alert ID to auto-hash")
    determination: str = Field(default="CONFIRMED_FRAUD", description="Verdict: CONFIRMED_FRAUD or FALSE_POSITIVE")
    priority: int | None = Field(default=None, ge=1, le=3, description="Priority level: 1 (Standard), 2 (High), 3 (Critical)")
    weight: float | None = Field(default=None, gt=0.0, description="Loss/gradient weight")
    feature_vector: list[float] | None = Field(default=None, description="Optional feature vector")
    notes: str | None = Field(default=None, description="Investigator notes")
    raw_attributes: dict[str, Any] | None = Field(default=None, description="Non-sensitive attributes to validate")
    metadata: dict[str, Any] | None = Field(default=None, description="Additional metadata")


class AnalystFeedbackIngestResponse(BaseModel):
    """Response returned upon successful determination ingestion."""

    status: str = "success"
    item: dict[str, Any]


class FeedbackStatsResponse(BaseModel):
    """Summary metrics of a tenant's feedback buffer."""

    tenant_id: str
    total_count: int
    fraud_count: int
    false_positive_count: int
    consumed_count: int
    unconsumed_count: int
    priority_distribution: dict[int, int]


class RetrainingBatchRequest(BaseModel):
    """Request to sample a prioritized retraining batch."""

    tenant_id: str = Field(default="bank_alpha", description="Tenant bank ID")
    batch_size: int = Field(default=32, ge=1, le=1000, description="Number of feedback items to sample")
    mark_consumed: bool = Field(default=False, description="Whether to mark sampled items as consumed")
    min_priority: int = Field(default=1, ge=1, le=3, description="Minimum priority filter")
    stratified: bool = Field(default=True, description="Whether to balance fraud and false positive samples")


class RetrainingBatchResponse(BaseModel):
    """Prioritized batch response for local model fine-tuning."""

    tenant_id: str
    batch_size: int
    items: list[dict[str, Any]]
    fraud_count: int
    false_positive_count: int
    mean_priority: float


class DPGradientRequest(BaseModel):
    """Request to compute DP-noise-protected gradient updates."""

    tenant_id: str = Field(default="bank_alpha", description="Tenant bank ID")
    epsilon: float = Field(default=1.0, gt=0.0, le=2.0, description="Differential privacy budget epsilon")
    delta: float = Field(default=1e-5, gt=0.0, lt=1.0, description="Privacy parameter delta")
    clip_norm: float = Field(default=1.0, gt=0.0, description="L2 gradient clipping norm")


class DPGradientResponse(BaseModel):
    """DP gradient update response."""

    tenant_id: str
    delta_weights: list[float]
    sample_count: int
    epsilon: float
    delta: float
    sigma: float


@router.post(
    "/ingest",
    response_model=AnalystFeedbackIngestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_feedback(payload: AnalystFeedbackIngestRequest) -> AnalystFeedbackIngestResponse:
    """Ingests an analyst determination into the tenant's local label feedback buffer."""
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


@router.get(
    "/stats/{tenant_id}",
    response_model=FeedbackStatsResponse,
    status_code=status.HTTP_200_OK,
)
async def get_feedback_stats(tenant_id: str) -> FeedbackStatsResponse:
    """Retrieves summary metrics and class balance for a tenant's feedback store."""
    stats = _pipeline.get_buffer_stats(tenant_id=tenant_id)
    return FeedbackStatsResponse(**stats)


@router.post(
    "/retraining-batch",
    response_model=RetrainingBatchResponse,
    status_code=status.HTTP_200_OK,
)
async def sample_retraining_batch(payload: RetrainingBatchRequest) -> RetrainingBatchResponse:
    """Samples a prioritized, stratified batch of human-verified feedback items for local model retraining."""
    batch_data = _pipeline.get_priority_retraining_batch(
        tenant_id=payload.tenant_id,
        batch_size=payload.batch_size,
        mark_consumed=payload.mark_consumed,
        min_priority=payload.min_priority,
        stratified=payload.stratified,
    )
    return RetrainingBatchResponse(**batch_data)


@router.post(
    "/dp-gradient",
    response_model=DPGradientResponse,
    status_code=status.HTTP_200_OK,
)
async def compute_dp_gradient(payload: DPGradientRequest) -> DPGradientResponse:
    """Computes Differential-Privacy-protected gradient update for tenant's local model."""
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


@router.delete(
    "/buffer/{tenant_id}",
    status_code=status.HTTP_200_OK,
)
async def clear_feedback_buffer(tenant_id: str) -> dict[str, Any]:
    """Clears all feedback items from the tenant's buffer."""
    cleared = _pipeline.clear_buffer(tenant_id=tenant_id)
    return {"status": "success", "tenant_id": tenant_id, "cleared_count": cleared}
