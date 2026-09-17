"""Pydantic schemas for Analyst Ground-Truth Feedback & Retraining Store.

Clean Architecture schema definitions for human-in-the-loop analyst determinations,
priority retraining batch sampling, and DP-noise-protected gradient updates.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Sentinel regex to strip ASCII control characters
_SAFE_TEXT_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _strip_control(value: str) -> str:
    """Remove ASCII control characters that have no legitimate use in API text."""
    return _SAFE_TEXT_RE.sub("", value)


class AnalystFeedbackIngestRequest(BaseModel):
    """Payload for submitting an analyst ground-truth determination."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    tenant_id: str = Field(
        default="bank_alpha",
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Target bank tenant ID",
    )
    transaction_id_hash: str | None = Field(
        default=None,
        max_length=128,
        description="HMAC-SHA256 hashed transaction ID",
    )
    alert_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Investigation alert ID to auto-hash",
    )
    determination: Literal["CONFIRMED_FRAUD", "FALSE_POSITIVE"] = Field(
        default="CONFIRMED_FRAUD",
        description="Verdict: CONFIRMED_FRAUD or FALSE_POSITIVE",
    )
    priority: int | None = Field(
        default=None,
        ge=1,
        le=3,
        description="Priority level: 1 (Standard), 2 (High), 3 (Critical)",
    )
    weight: float | None = Field(
        default=None,
        gt=0.0,
        le=100.0,
        description="Loss/gradient weight",
    )
    feature_vector: list[float] | None = Field(
        default=None,
        description="Optional pre-extracted feature vector",
    )
    notes: str | None = Field(
        default=None,
        max_length=4096,
        description="Investigator notes",
    )
    raw_attributes: dict[str, Any] | None = Field(
        default=None,
        description="Non-sensitive attributes to validate against Zero-PII rules",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="Additional operational metadata",
    )

    @field_validator("notes")
    @classmethod
    def sanitize_notes(cls, v: str | None) -> str | None:
        if v is not None:
            return _strip_control(v)
        return v


class AnalystFeedbackIngestResponse(BaseModel):
    """Response returned upon successful determination ingestion."""

    model_config = ConfigDict(extra="ignore")

    status: str = "success"
    item: dict[str, Any]


class FeedbackStatsResponse(BaseModel):
    """Summary metrics of a tenant's feedback buffer."""

    model_config = ConfigDict(extra="ignore")

    tenant_id: str
    total_count: int
    fraud_count: int
    false_positive_count: int
    consumed_count: int
    unconsumed_count: int
    priority_distribution: dict[int, int]


class RetrainingBatchRequest(BaseModel):
    """Request to sample a prioritized retraining batch."""

    model_config = ConfigDict(extra="ignore")

    tenant_id: str = Field(
        default="bank_alpha",
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Tenant bank ID",
    )
    batch_size: int = Field(
        default=32,
        ge=1,
        le=1000,
        description="Number of feedback items to sample",
    )
    mark_consumed: bool = Field(
        default=False,
        description="Whether to mark sampled items as consumed",
    )
    min_priority: int = Field(
        default=1,
        ge=1,
        le=3,
        description="Minimum priority filter (1-3)",
    )
    stratified: bool = Field(
        default=True,
        description="Whether to balance fraud and false positive samples",
    )


class RetrainingBatchResponse(BaseModel):
    """Prioritized batch response for local model fine-tuning."""

    model_config = ConfigDict(extra="ignore")

    tenant_id: str
    batch_size: int
    items: list[dict[str, Any]]
    fraud_count: int
    false_positive_count: int
    mean_priority: float


class DPGradientRequest(BaseModel):
    """Request to compute DP-noise-protected gradient updates."""

    model_config = ConfigDict(extra="ignore")

    tenant_id: str = Field(
        default="bank_alpha",
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Tenant bank ID",
    )
    epsilon: float = Field(
        default=1.0,
        gt=0.0,
        le=2.0,
        description="Differential privacy budget epsilon",
    )
    delta: float = Field(
        default=1e-5,
        gt=0.0,
        lt=1.0,
        description="Privacy parameter delta",
    )
    clip_norm: float = Field(
        default=1.0,
        gt=0.0,
        le=100.0,
        description="L2 gradient clipping norm",
    )


class DPGradientResponse(BaseModel):
    """DP gradient update response."""

    model_config = ConfigDict(extra="ignore")

    tenant_id: str
    delta_weights: list[float]
    sample_count: int
    epsilon: float
    delta: float
    sigma: float


class ClearFeedbackBufferResponse(BaseModel):
    """Response returned when a tenant's feedback buffer is cleared."""

    model_config = ConfigDict(extra="ignore")

    status: str = "success"
    tenant_id: str
    cleared_count: int
