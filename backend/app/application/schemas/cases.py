"""Pydantic schemas for Case Management, Evidence, and Four-Eyes Disposition.

Clean Architecture schema definitions for case lifecycle, investigator activity,
evidence registry, and FinCEN SAR regulatory filings.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Sentinel regex to strip control characters
_SAFE_TEXT_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _strip_control(value: str) -> str:
    """Remove ASCII control characters that have no legitimate use in API text."""
    return _SAFE_TEXT_RE.sub("", value)


_CASE_PRIORITIES = Literal["p1_critical", "p2_high", "p3_medium", "p4_low"]
_CASE_STATUSES = Literal[
    "open",
    "assigned",
    "investigating",
    "pending_review",
    "escalated",
    "sar_filed",
    "closed_confirmed",
    "closed_false_positive",
]
_EVIDENCE_TYPES = Literal[
    "document",
    "kyc_profile",
    "ledger_proof",
    "TRANSACTION_RECORD",
    "TRANSACTION_LEDGER",
    "IP_INTELLIGENCE",
    "KYC_DOCUMENT",
]


class CaseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(
        ...,
        min_length=3,
        max_length=256,
        description="Title of the investigation case",
    )
    priority: _CASE_PRIORITIES = Field(  # type: ignore[valid-type]
        "p3_medium",
        description="Priority level: p1_critical | p2_high | p3_medium | p4_low",
    )
    alert_ids: list[str] = Field(
        default_factory=list,
        description="Associated alert IDs (max 200)",
    )
    total_risk_score: float = Field(
        0.0,
        ge=0.0,
        le=1000.0,
        description="Total risk score composite",
    )
    assigned_to: str | None = Field(
        None,
        max_length=128,
        description="Assigned investigator identity",
    )
    bank_id: str | None = Field(
        None,
        max_length=64,
        description="Authoritative bank tenant identifier",
    )

    @field_validator("title")
    @classmethod
    def sanitize_title(cls, v: str) -> str:
        return _strip_control(v)

    @field_validator("alert_ids")
    @classmethod
    def limit_alert_ids(cls, v: list[str]) -> list[str]:
        if len(v) > 200:
            raise ValueError("alert_ids may not contain more than 200 items")
        return v


class CaseNoteRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    author: str = Field(
        "analyst",
        min_length=1,
        max_length=128,
        description="Author identifier",
        pattern=r"^[a-zA-Z0-9 _\-\.@:]+$",
    )
    content: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description="Note content text",
    )

    @field_validator("content")
    @classmethod
    def sanitize_content(cls, v: str) -> str:
        return _strip_control(v)


class CaseStatusRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: _CASE_STATUSES  # type: ignore[valid-type]
    actor: str = Field(
        "analyst",
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_\-\.@:]+$",
    )
    supervisor_signature: str | None = Field(None, max_length=512)
    second_supervisor_signature: str | None = Field(None, max_length=512)
    supervisor_signatures: list[str] = Field(default_factory=list)
    expected_status: _CASE_STATUSES | None = Field(  # type: ignore[valid-type]
        None,
        description="Optional precondition: expected current case status for optimistic concurrency control",
    )
    expected_version: int | None = Field(
        None,
        description="Optional precondition: expected case dossier version for optimistic concurrency control",
    )
    expected_timeline_hash: str | None = Field(
        None,
        description="Optional precondition: expected timeline head hash for optimistic concurrency control",
    )


class CaseEscalateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    reason: str = Field(..., min_length=3, max_length=512)
    actor: str = Field(
        "analyst",
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_\-\.@:]+$",
    )


class CaseSignRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    supervisor_id: str = Field(..., min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_\-\.@:]+$")
    action: str = Field("APPROVE", pattern=r"^(APPROVE|REJECT)$")
    notes: str | None = Field(None, max_length=512)
    expected_status: str | None = Field(
        None,
        description="Optional precondition: expected case status",
    )
    expected_version: int | None = Field(
        None,
        description="Optional precondition: expected case dossier version to prevent stale review approval",
    )
    expected_timeline_hash: str | None = Field(
        None,
        description="Optional precondition: expected timeline head hash to prevent stale review approval",
    )


class CaseResolveRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    resolution: str = Field(..., pattern=r"^(CONFIRMED_FRAUD|FALSE_POSITIVE)$")
    primary_supervisor: str = Field(..., min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_\-\.@:]+$")
    secondary_supervisor: str = Field(..., min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_\-\.@:]+$")
    actor: str = Field(
        "analyst",
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_\-\.@:]+$",
    )
    notes: str | None = Field(
        None,
        max_length=1024,
        description="Optional consensus rationale recorded with Four-Eyes resolution",
    )
    expected_status: str | None = Field(
        None,
        description="Optional precondition: expected case status",
    )
    expected_version: int | None = Field(
        None,
        description="Optional precondition: expected case dossier version to prevent stale review approval",
    )
    expected_timeline_hash: str | None = Field(
        None,
        description="Optional precondition: expected timeline head hash to prevent stale review approval",
    )


class CaseValidateTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    target_status: str | None = None
    target_state: str | None = None
    actor: str = Field(
        "analyst",
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_\-\.@:]+$",
    )
    actor_id: str | None = Field(None, max_length=128, pattern=r"^[a-zA-Z0-9_\-\.@:]+$")
    supervisor_signatures: list[str] = Field(default_factory=list)


class TimelineVerificationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    case_id: str
    is_valid: bool
    event_count: int
    corrupted_index: int | None = None
    chain_hashes: list[str] = []
    message: str


class CaseLinkAlertRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    alert_id: str = Field(
        ...,
        min_length=3,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_\-]+$",
    )


class CaseNoteResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    case_id: str
    author: str
    content: str
    created_at: str


class CaseEventResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_type: str
    description: str
    actor: str
    timestamp: str
    metadata: dict[str, Any] = {}


class CaseResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    bank_id: str | None = None
    title: str
    status: str
    priority: str
    assigned_to: str | None = None
    alert_ids: list[str] = []
    evidence_ids: list[str] = []
    notes: list[CaseNoteResponse] = []
    timeline: list[CaseEventResponse] = []
    created_at: str
    updated_at: str | None = None
    closed_at: str | None = None
    total_risk_score: float = 0.0
    duration_hours: float | None = None
    is_open: bool = True
    supervisor_signatures: list[str] = []
    supervisor_signature: str | None = None
    version: int = 1
    timeline_hash: str | None = None


class CaseSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    bank_id: str | None = None
    title: str
    status: str
    priority: str
    assigned_to: str | None = None
    alert_count: int
    created_at: str
    is_open: bool = True


class EvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    evidence_type: _EVIDENCE_TYPES = "document"  # type: ignore[valid-type]
    title: str = Field(
        ...,
        min_length=3,
        max_length=256,
        description="Evidence item title",
    )
    file_path: str = Field(
        ...,
        max_length=512,
        description="Relative storage path (no traversal sequences)",
    )
    content: str = Field(
        ...,
        max_length=65536,
        description="Evidence content or summary text (max 64 KiB)",
    )
    uploaded_by: str = Field(
        "analyst",
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9 _\-\.@]+$",
    )

    @field_validator("file_path")
    @classmethod
    def no_path_traversal(cls, v: str) -> str:
        if ".." in v or v.startswith("/") or "\\" in v:
            raise ValueError(
                "file_path must be a relative path without traversal sequences"
            )
        return v

    @field_validator("title", "content")
    @classmethod
    def sanitize_text(cls, v: str) -> str:
        return _strip_control(v)


class EvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    case_id: str
    evidence_type: str
    title: str
    file_path: str
    content_hash: str
    uploaded_by: str
    uploaded_at: str
    metadata: dict[str, Any] = {}


class InvestigatorAuditLogResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    investigator: str
    action: str
    target_id: str
    timestamp: str
    session_duration_sec: float | None = None
    metadata: dict[str, Any] = {}


class SessionDurationRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    investigator: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9 _\-\.@]+$",
    )
    duration_seconds: float = Field(
        ...,
        ge=0.0,
        le=86400.0,
        description="Session duration in seconds (max 24 h)",
    )
    time_window_end: str | None = Field(
        None,
        max_length=32,
        description="ISO 8601 datetime string",
    )


class ExportFinCENXmlRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    case_id: str = Field(..., description="ID of confirmed fraud case to compile SAR XML for")
    filer_id: str | None = Field(None, description="Optional compliance officer or filer identifier")
    narrative_override: str | None = Field(None, description="Optional custom SAR narrative override")
    institution_name: str | None = Field(None, description="Optional reporting financial institution override")


class ExportFinCENXmlResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    submission_id: str
    status: str
    xml: str
    xml_payload: str | None = None
    sha256_hash: str | None = None
    filing_status: str | None = None
    pdf_download_url: str


class CaseEligibleEntityResponse(BaseModel):
    """Authoritative institution-owned entity linked to an investigation case."""

    model_config = ConfigDict(extra="ignore")

    entity_id: str = Field(..., description="Unique entity identifier.")
    display_label: str = Field(..., description="Masked/sanitized display label.")
    entity_type: str = Field(..., description="Entity domain type (customer, merchant, device, card).")
    risk_level: str = Field(..., description="Local entity risk level classification.")
    bank_id: str = Field(..., description="Authoritative owning bank ID.")
    source_alert_id: str | None = Field(
        None, description="Source alert ID from which this entity was linked, if applicable."
    )


class CaseIntelligenceEvidenceResponse(BaseModel):
    """Sanitized, investigator-facing read projection of verified cross-bank intelligence evidence."""

    model_config = ConfigDict(extra="ignore")

    evidence_id: str = Field(..., description="Authoritative case evidence record ID.")
    case_id: str = Field(..., description="Target investigation case ID.")
    title: str = Field(..., description="Evidence item title.")
    content_hash: str = Field(..., description="Cryptographic SHA-256 digest of persisted evidence payload.")
    batch_query_id: str | None = Field(
        None, description="Authoritative server-issued query execution batch ID."
    )
    uploaded_at: str = Field(..., description="ISO-8601 registration timestamp.")
    uploaded_by: str = Field(..., description="Investigator or system actor identity.")
    operational_status: str = Field(
        ...,
        description="Cryptographic assurance status (OPERATIONAL, HISTORICAL_RECORD, CONSENT_REVOKED, UNADMITTED, UNAVAILABLE, TAMPERED, CORRUPT).",
    )
    is_authoritative: bool = Field(
        ..., description="Indicates server-verified authentic issuance and admission."
    )
    disclosure_mode: Literal["ATTRIBUTED_INSTITUTIONS", "ANONYMIZED_SUMMARY"] = Field(
        ..., description="Disclosure mode governing this signal (ATTRIBUTED_INSTITUTIONS or ANONYMIZED_SUMMARY)."
    )
    target_bank_id: str | None = Field(
        None,
        description="Responding counterpart institution ID (strictly masked to None under ANONYMIZED_SUMMARY).",
    )
    match_confirmed: bool | None = Field(
        None, description="Whether private set intersection confirmed a match."
    )
    match_category: str | None = Field(
        None, description="Match category (e.g. EXACT_MATCH, PROBABILISTIC_FUZZY, NO_MATCH)."
    )
    confidence: float | None = Field(
        None, description="Match confidence or calibrated score."
    )
    risk_level: str | None = Field(
        None, description="Sanitized counterpart risk level."
    )
    typology_indicators: list[str] = Field(
        default_factory=list, description="Sanitized AML/fraud typology indicators."
    )
    analyst_notes: str | None = Field(
        None, description="Analyst investigative notes captured upon attachment."
    )
    verification_state: Literal["VERIFIED", "INTEGRITY_VERIFIED_ONLY", "INDETERMINATE", "FAILED_VERIFICATION"] = Field(
        ..., description="Cryptographic admission verification state (VERIFIED, INTEGRITY_VERIFIED_ONLY, INDETERMINATE, FAILED_VERIFICATION)."
    )
    retention_status: str = Field(
        "ACTIVE",
        description="Query batch retention status (ACTIVE or RETENTION_EXPIRED). Evidence ledger is permanent.",
    )
    batch_expires_at: str | None = Field(
        None, description="ISO-8601 expiration timestamp for intermediate batch store record."
    )
    explanation: str | None = Field(
        None, description="Human-readable provenance and cryptographic audit explanation."
    )

