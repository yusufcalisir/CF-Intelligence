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
        pattern=r"^[a-zA-Z0-9_\-\.@]+$",
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
        pattern=r"^[a-zA-Z0-9_\-\.@]+$",
    )
    supervisor_signature: str | None = Field(None, max_length=512)
    second_supervisor_signature: str | None = Field(None, max_length=512)
    supervisor_signatures: list[str] = Field(default_factory=list)


class CaseEscalateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    reason: str = Field(..., min_length=3, max_length=512)
    actor: str = Field(
        "analyst",
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_\-\.@]+$",
    )


class CaseSignRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    supervisor_id: str = Field(..., min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_\-\.@]+$")
    action: str = Field("APPROVE", pattern=r"^(APPROVE|REJECT)$")
    notes: str | None = Field(None, max_length=512)


class CaseResolveRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    resolution: str = Field(..., pattern=r"^(CONFIRMED_FRAUD|FALSE_POSITIVE)$")
    primary_supervisor: str = Field(..., min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_\-\.@]+$")
    secondary_supervisor: str = Field(..., min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_\-\.@]+$")
    actor: str = Field(
        "analyst",
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_\-\.@]+$",
    )


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


class CaseSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
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
