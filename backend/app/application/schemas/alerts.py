"""Pydantic v2 schemas for Alert Management, Triage & Deduplication API.

Defines strict request and response contracts for:
- Alert listing and entity inspection
- Real-time sliding-window deduplication statistics
- Multi-factor algorithmic triage evaluation and SLA assignment
- Alert lifecycle disposition and status transitions
- Privacy-preserving shared intelligence feeds
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ── Sanitization Regex (Strips non-printable control characters) ──────────────
_SAFE_TEXT_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _strip_control(value: str) -> str:
    """Remove ASCII control characters that have no legitimate use in API text."""
    return _SAFE_TEXT_RE.sub("", value)


class AlertResponse(BaseModel):
    """Public contract for fraud alerts with triage and deduplication metadata."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., description="Unique alert identifier")
    bank_id: str = Field(..., description="Reporting bank identifier (tenant)")
    transaction_id: str = Field(..., description="Associated transaction ID")
    risk_score: float = Field(..., ge=0.0, le=1000.0, description="Composite risk score (0-1000)")
    severity: str = Field(..., description="Alert severity: critical, high, medium, low, info")
    status: str = Field(..., description="Investigation status")
    reason_codes: list[str] = Field(default_factory=list, description="Triggered fraud reason codes")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Model inference confidence score")
    involved_entity_ids: list[str] = Field(default_factory=list, description="Hashed privacy entity identifiers")
    created_at: str = Field(..., description="ISO 8601 creation timestamp")
    updated_at: str | None = Field(None, description="ISO 8601 update timestamp")
    top_features: list[dict[str, Any]] = Field(default_factory=list, description="Top SHAP feature attributions")
    risk_factors: list[str] = Field(default_factory=list, description="Human-readable risk factors")
    model_confidence: float = Field(0.0, ge=0.0, le=1.0, description="Underlying model confidence")
    triage_priority: str = Field("p3_medium", description="Triage priority: p1_critical, p2_high, p3_medium, p4_low")
    triage_action: str = Field("queue_standard", description="Recommended operational triage action")
    sla_minutes: int = Field(1440, ge=1, le=10080, description="Investigation SLA in minutes")
    triage_reasons: list[str] = Field(default_factory=list, description="Algorithmic rationale for triage assignment")
    dedup_count: int = Field(1, ge=1, description="Duplicate occurrence count within sliding window")
    is_duplicate: bool = False
    dedup_key: str | None = Field(None, description="Sliding window deduplication hash")


class AlertStatusUpdateRequest(BaseModel):
    """Payload for updating an alert's investigation status."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["new", "investigating", "confirmed_fraud", "false_positive", "escalated", "closed"] = Field(
        ..., description="Target alert status"
    )
    resolution_notes: str | None = Field(None, max_length=1000, description="Optional analyst resolution notes")

    @field_validator("resolution_notes")
    @classmethod
    def sanitize_notes(cls, v: str | None) -> str | None:
        if v is not None:
            return _strip_control(v).strip()
        return None


class AlertTriageEvaluateRequest(BaseModel):
    """Optional transaction override attributes for on-demand alert triage re-evaluation."""

    model_config = ConfigDict(extra="forbid")

    transaction_amount: float | None = Field(None, ge=0.0, le=100_000_000.0, description="Transaction amount in USD")
    country_code: str | None = Field(None, min_length=2, max_length=3, description="ISO alpha country code")
    velocity: float | None = Field(None, ge=0.0, le=10_000.0, description="Transaction velocity (txns/hour)")

    @field_validator("country_code")
    @classmethod
    def sanitize_country(cls, v: str | None) -> str | None:
        if v is not None:
            return v.strip().upper()
        return None


class AlertStandaloneTriageRequest(BaseModel):
    """Payload for evaluating triage rules on arbitrary transaction features without persisting."""

    model_config = ConfigDict(extra="ignore")

    transaction_amount: float = Field(0.0, ge=0.0, le=100_000_000.0)
    country_code: str = Field("US", min_length=2, max_length=3)
    velocity: float = Field(1.0, ge=0.0, le=10_000.0)
    risk_score: float = Field(500.0, ge=0.0, le=1000.0)
    severity: Literal["critical", "high", "medium", "low", "info"] = Field("medium")
    reason_codes: list[str] = Field(default_factory=list)
    entity_overlap_count: int = Field(0, ge=0, le=100)
    dedup_count: int = Field(1, ge=1, le=10_000)

    @field_validator("country_code")
    @classmethod
    def sanitize_country(cls, v: str) -> str:
        return v.strip().upper()


class AlertTriageEvaluateResponse(BaseModel):
    """Response returned from triage evaluation."""

    model_config = ConfigDict(extra="ignore")

    alert_id: str
    triage_priority: str
    triage_action: str
    sla_minutes: int
    triage_reasons: list[str]


class AlertDeduplicationStatsResponse(BaseModel):
    """Real-time sliding-window deduplication metrics."""

    model_config = ConfigDict(extra="ignore")

    total_processed: int = Field(..., ge=0)
    duplicates_detected: int = Field(..., ge=0)
    deduplication_ratio: float = Field(..., ge=0.0, le=1.0)
    active_sliding_window_keys: int = Field(..., ge=0)
    window_seconds: float = Field(..., gt=0.0)
