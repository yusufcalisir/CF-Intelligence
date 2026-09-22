"""Pydantic v2 schemas for the Sanctions & PEP Screening API (Phase 108)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_SOURCES = Literal[
    "EU_CONSOLIDATED", "UN_SECURITY_COUNCIL", "OFAC_SDN",
    "HM_TREASURY", "PEP_GLOBAL", "INTERNAL_GOODLIST",
]
_ENTITY_TYPES = Literal["INDIVIDUAL", "LEGAL_ENTITY", "VESSEL", "AIRCRAFT"]
_DISPOSITIONS = Literal["PENDING_REVIEW", "CONFIRMED_MATCH", "FALSE_POSITIVE", "ESCALATED"]


# ── Request schemas ────────────────────────────────────────────────────────────

class WatchlistEntryRequest(BaseModel):
    """Single watchlist entry to load into a source."""

    model_config = ConfigDict(extra="ignore")

    entry_id: str = Field(default="", description="Unique identifier on the source list.")
    source: _SOURCES
    entity_type: _ENTITY_TYPES = "INDIVIDUAL"
    primary_name: str = Field(..., min_length=1, max_length=512)
    aliases: list[str] = Field(default_factory=list)
    date_of_birth: str = Field(default="", max_length=20)
    nationalities: list[str] = Field(default_factory=list)
    listing_date: str = Field(default="", max_length=20)
    listed_by: str = Field(default="", max_length=128)
    additional_info: str = Field(default="", max_length=1024)

    @field_validator("aliases")
    @classmethod
    def _limit_aliases(cls, v: list[str]) -> list[str]:
        if len(v) > 50:
            raise ValueError("Maximum 50 aliases per entry.")
        return v


class LoadWatchlistRequest(BaseModel):
    """Bulk watchlist load request."""

    model_config = ConfigDict(extra="ignore")

    source: _SOURCES
    entries: list[WatchlistEntryRequest] = Field(..., min_length=1)

    @field_validator("entries")
    @classmethod
    def _limit_entries(cls, v: list[WatchlistEntryRequest]) -> list[WatchlistEntryRequest]:
        if len(v) > 100_000:
            raise ValueError("Maximum 100,000 entries per load request.")
        return v


class ScreenEntityRequest(BaseModel):
    """Request to screen a single entity against watchlists."""

    model_config = ConfigDict(extra="ignore")

    query_name: str = Field(..., min_length=1, max_length=512)
    entity_type: _ENTITY_TYPES = "INDIVIDUAL"
    date_of_birth: str = Field(default="", max_length=20)
    nationalities: list[str] = Field(default_factory=list, max_length=10)
    sources: list[_SOURCES] | None = Field(
        default=None,
        description="Watchlist sources to check. Defaults to all non-goodlist sources.",
    )
    alert_threshold: int = Field(
        default=75, ge=0, le=100,
        description="Minimum score (0–100) to generate an alert.",
    )
    actor: str = Field(default="system", max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GoodlistRequest(BaseModel):
    """Request to goodlist (false-positive suppress) a screening hit."""

    model_config = ConfigDict(extra="ignore")

    hit_id: str = Field(..., min_length=1)
    approved_by: str = Field(..., min_length=1, max_length=128)
    notes: str = Field(default="", max_length=512)


class UpdateDispositionRequest(BaseModel):
    """Request to update analyst disposition for a hit."""

    model_config = ConfigDict(extra="ignore")

    hit_id: str = Field(..., min_length=1)
    disposition: _DISPOSITIONS
    reviewed_by: str = Field(..., min_length=1, max_length=128)
    notes: str = Field(default="", max_length=512)


class BulkRescreenRequest(BaseModel):
    """Request to re-screen the full registered portfolio."""

    model_config = ConfigDict(extra="ignore")

    sources: list[_SOURCES] | None = None
    alert_threshold: int | None = Field(default=None, ge=0, le=100)


# ── Response schemas ───────────────────────────────────────────────────────────

class ScreeningHitResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    hit_id: str
    watchlist_entry_id: str
    source: str
    matched_name: str
    matched_alias: str
    score: float
    algorithm: str
    entity_type: str
    disposition: str
    reviewed_by: str
    reviewed_at: datetime | None
    notes: str
    listed_by: str


class ScreeningResultResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    result_id: str
    entity_key_hash: str
    query_name: str
    entity_type: str
    status: str
    sources_checked: list[str]
    hits: list[ScreeningHitResponse]
    top_score: float
    alert_threshold: int
    is_alerted: bool
    goodlisted: bool
    screening_duration_ms: float
    created_at: datetime
    completed_at: datetime | None
    metadata: dict[str, Any]


class ScreeningResultSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    result_id: str
    entity_key_hash: str
    query_name: str
    entity_type: str
    status: str
    top_score: float
    is_alerted: bool
    goodlisted: bool
    hit_count: int
    screening_duration_ms: float
    created_at: datetime


class GoodlistEntryResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    goodlist_id: str
    entity_key_hash: str
    approved_by: str
    approved_at: datetime
    watchlist_entry_id: str
    notes: str


class WatchlistCountsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    counts: dict[str, int]


class ScreeningMetricsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total_screens: int
    total_alerts: int
    alert_rate_pct: float
    goodlist_suppressions: int
    goodlist_size: int
    avg_duration_ms: float
    watchlist_counts: dict[str, int]


class BulkRescreenResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total_rescreened: int
    total_alerted: int
    results: list[ScreeningResultSummaryResponse]
