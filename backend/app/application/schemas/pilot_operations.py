"""Pydantic schemas for Controlled Two-Bank Pilot Operations API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PilotProvisionRequest(BaseModel):
    """Payload to provision a new controlled two-bank pilot in DRAFT state."""

    pilot_id: str = Field(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    consortium_id: str = Field(..., min_length=3, max_length=64)
    participating_institutions: list[str] = Field(default_factory=lambda: ["bank_a", "bank_b"], min_length=2)
    allowed_purposes: list[str] = Field(default_factory=lambda: ["AML_COLLABORATIVE_INVESTIGATION"])
    allowed_jurisdictions: list[str] = Field(default_factory=lambda: ["eu-central-1"])
    approved_datasets: dict[str, Any] = Field(default_factory=dict)
    authorized_operators: list[str] = Field(default_factory=list)
    operational_grant_id: str = Field(default="")
    topology: dict[str, Any] = Field(default_factory=dict)
    starts_at: str = Field(default="")
    expires_at: str = Field(default="")
    query_quota_total: int = Field(default=50, ge=1)
    query_quota_per_institution: int = Field(default=25, ge=1)


class PilotPreflightRequest(BaseModel):
    """Optional parameters for preflight readiness evaluation."""

    coordinator_url: str | None = None
    worker_endpoints: dict[str, str] | None = None


class PilotApprovalRequest(BaseModel):
    """Payload for institutional or operator approval."""

    approving_institution: str = Field(..., min_length=2, max_length=32)
    notes: str = Field(default="", max_length=500)
    signature: str = Field(default="")


class PilotKillSwitchRequest(BaseModel):
    """Payload to pause, abort, or terminate a pilot."""

    reason: str = Field(..., min_length=3, max_length=500)


class PreflightCheckItem(BaseModel):
    check_name: str
    passed: bool
    details: str
    checked_at: str


class PilotApprovalItem(BaseModel):
    approver_id: str
    approver_institution: str
    approver_role: str
    scope_digest: str
    approved_at: str
    signature: str = ""
    notes: str = ""


class PilotStatusResponse(BaseModel):
    """Detailed operational status of a pilot."""

    pilot_id: str
    revision: int
    consortium_id: str
    status: str
    status_reason: str
    scope_digest: str
    is_fully_approved: bool
    queries_executed: int
    queries_denied: int
    queries_total_quota: int
    participating_institutions: list[str]
    allowed_purposes: list[str]
    allowed_jurisdictions: list[str]
    operational_grant_id: str
    starts_at: str
    expires_at: str
    created_at: str
    updated_at: str
    activated_at: str | None = None
    terminated_at: str | None = None
    termination_reason: str | None = None
    preflight_checks: list[PreflightCheckItem] = Field(default_factory=list)
    approvals: dict[str, PilotApprovalItem] = Field(default_factory=dict)


class PilotTelemetrySummary(BaseModel):
    """Telemetry and operational metrics for an active or completed pilot."""

    pilot_id: str
    status: str
    queries_executed: int
    queries_denied: int
    query_quota_total: int
    quota_remaining: int
    mean_latency_ms: float
    p95_latency_ms: float
    institution_query_counts: dict[str, int]
    last_operation_at: str | None = None
    bilateral_authorization_valid: bool
