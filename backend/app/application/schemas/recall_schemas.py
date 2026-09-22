"""Pydantic schemas for SEPA Instant Payment Recall API (Phase 107).

Defines request and response models for the ISO 20022 payment recall workflow:
camt.056 initiation, pacs.004 positive resolution, camt.029 negative resolution,
and provisional hold webhook triggering.

Privacy invariants:
- BIC values are accepted as input but stored as SHA-256 hashes on the entity.
- IBAN values are masked before any persistence; responses return masked forms.
- Cleartext IBAN/BIC appear only inside generated XML blobs.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ── Literal type aliases ───────────────────────────────────────────────────────

_RECALL_REASONS = Literal["FRAD", "TECH", "DUPL", "CUST", "UPAY", "COVR"]

_RECALL_STATUSES = Literal[
    "INITIATED", "SENT", "ACKNOWLEDGED_BY_CREDITOR_AGENT",
    "PROVISIONAL_HOLD_ACTIVE", "FUNDS_RETURNED", "UNABLE_TO_RECALL",
    "PARTIALLY_RETURNED", "CANCELLED",
]

_RESOLUTION_CODES = Literal["NOAS", "NOOR", "LEGL", "CUST", "AGNT"]

# BIC validation: 8 or 11 uppercase alphanumeric chars
_BIC_RE = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$")
# IBAN: 15–34 alphanumeric (permissive cross-jurisdiction)
_IBAN_RE = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{1,30}$")
# Amount: positive decimal with up to 2 places
_AMOUNT_RE = re.compile(r"^\d+(\.\d{1,2})?$")
# UETR: UUIDv4 format
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _validate_bic(v: str) -> str:
    v = v.strip().upper()
    if not _BIC_RE.match(v):
        raise ValueError(f"Invalid BIC format: {v!r}. Expected 8 or 11 uppercase alphanumeric chars.")
    return v


def _validate_iban(v: str) -> str:
    v = v.strip().upper().replace(" ", "")
    if not _IBAN_RE.match(v):
        raise ValueError(f"Invalid IBAN format: {v!r}")
    return v


def _validate_amount(v: str) -> str:
    v = v.strip()
    if not _AMOUNT_RE.match(v):
        raise ValueError(f"Amount must be a positive decimal with up to 2 places: {v!r}")
    try:
        d = Decimal(v)
    except InvalidOperation as exc:
        raise ValueError(f"Cannot parse amount: {v!r}") from exc
    if d <= 0:
        raise ValueError(f"Amount must be positive, got {v!r}")
    return v


# ── Request schemas ────────────────────────────────────────────────────────────


class InitiateRecallRequest(BaseModel):
    """Request to initiate a new SEPA payment recall (camt.056)."""

    model_config = ConfigDict(extra="ignore")

    original_msg_id: str = Field(
        ..., min_length=1, max_length=35,
        description="MsgId from the original pacs.008 credit transfer.",
    )
    original_instr_id: str = Field(
        ..., min_length=1, max_length=35,
        description="InstrId from the original pacs.008.",
    )
    original_end_to_end_id: str = Field(
        ..., min_length=1, max_length=35,
        description="EndToEndId from the original pacs.008.",
    )
    original_uetr: str = Field(
        default="",
        description="UETR (UUIDv4) from the original pacs.008. Optional; auto-generated if absent.",
    )
    recall_reason: _RECALL_REASONS = Field(
        ...,
        description="ISO 20022 EPC cancellation reason code.",
    )
    amount_eur: str = Field(
        ...,
        description="Original payment amount in EUR (e.g. '49750.00').",
    )
    instructing_agent_bic: str = Field(
        ...,
        description="BIC of the originating (instructing) institution.",
    )
    creditor_agent_bic: str = Field(
        ...,
        description="BIC of the creditor (receiving) institution.",
    )
    debtor_iban: str = Field(
        ...,
        description="IBAN of the original payment debtor.",
    )
    creditor_iban: str = Field(
        ...,
        description="IBAN of the original payment creditor.",
    )
    actor: str = Field(
        default="compliance_officer",
        max_length=128,
        description="Anonymised officer identifier for the audit trail.",
    )
    provisional_hold_webhook_url: str = Field(
        default="",
        max_length=512,
        description="Optional webhook URL for provisional hold trigger.",
    )

    @field_validator("amount_eur")
    @classmethod
    def _check_amount(cls, v: str) -> str:
        return _validate_amount(v)

    @field_validator("instructing_agent_bic", "creditor_agent_bic")
    @classmethod
    def _check_bic(cls, v: str) -> str:
        return _validate_bic(v)

    @field_validator("debtor_iban", "creditor_iban")
    @classmethod
    def _check_iban(cls, v: str) -> str:
        return _validate_iban(v)

    @field_validator("original_uetr")
    @classmethod
    def _check_uetr(cls, v: str) -> str:
        if v and not _UUID_RE.match(v):
            raise ValueError(f"UETR must be a UUIDv4: {v!r}")
        return v


class PositiveResolutionRequest(BaseModel):
    """Request to resolve a recall positively (funds returned — pacs.004)."""

    model_config = ConfigDict(extra="ignore")

    creditor_agent_bic: str = Field(..., description="BIC of the returning institution.")
    instructing_agent_bic: str = Field(..., description="BIC of the original instructing agent.")
    returned_amount_eur: str = Field(..., description="Actual amount returned (may be partial).")
    actor: str = Field(default="creditor_agent", max_length=128)

    @field_validator("creditor_agent_bic", "instructing_agent_bic")
    @classmethod
    def _check_bic(cls, v: str) -> str:
        return _validate_bic(v)

    @field_validator("returned_amount_eur")
    @classmethod
    def _check_amount(cls, v: str) -> str:
        return _validate_amount(v)


class NegativeResolutionRequest(BaseModel):
    """Request to resolve a recall negatively (unable to return — camt.029)."""

    model_config = ConfigDict(extra="ignore")

    resolution_code: _RESOLUTION_CODES = Field(
        ...,
        description="camt.029 resolution code: NOAS, NOOR, LEGL, CUST, or AGNT.",
    )
    narrative: str = Field(
        default="",
        max_length=140,
        description="Optional explanation (max 140 chars per ISO 20022 AddtlInf).",
    )
    actor: str = Field(default="creditor_agent", max_length=128)


class TriggerHoldRequest(BaseModel):
    """Request to trigger provisional hold webhook."""

    model_config = ConfigDict(extra="ignore")

    actor: str = Field(default="system", max_length=128)


class CancelRecallRequest(BaseModel):
    """Request to cancel an in-flight recall (INITIATED status only)."""

    model_config = ConfigDict(extra="ignore")

    reason: str = Field(default="", max_length=256)
    actor: str = Field(default="compliance_officer", max_length=128)


# ── Response schemas ───────────────────────────────────────────────────────────


class RecallAuditEntryResponse(BaseModel):
    """Single recall audit trail entry."""

    model_config = ConfigDict(extra="ignore")

    seq: int
    actor: str
    action: str
    previous_status: str
    new_status: str
    message_type: str
    event_hash: str
    previous_hash: str
    timestamp: datetime
    metadata: dict[str, Any]


class RecallCaseResponse(BaseModel):
    """Complete SEPA recall case response."""

    model_config = ConfigDict(extra="ignore")

    id: str
    original_msg_id: str
    original_instr_id: str
    original_end_to_end_id: str
    original_uetr: str
    recall_reason: str
    originating_bank_bic_hash: str
    creditor_agent_bic_hash: str
    amount_eur: str
    currency: str
    status: str
    message_type: str
    camt056_xml: str
    pacs004_xml: str
    camt029_xml: str
    resolution_code: str | None
    returned_amount_eur: str | None
    resolution_narrative: str
    provisional_hold_triggered: bool
    provisional_hold_triggered_at: datetime | None
    provisional_hold_response_status: int | None
    sla_hours: int
    sla_deadline: datetime | None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    audit_trail: list[RecallAuditEntryResponse]
    head_hash: str


class RecallCaseSummaryResponse(BaseModel):
    """Lightweight recall case listing response."""

    model_config = ConfigDict(extra="ignore")

    id: str
    original_msg_id: str
    recall_reason: str
    status: str
    amount_eur: str
    currency: str
    provisional_hold_triggered: bool
    sla_hours: int
    sla_deadline: datetime | None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    audit_entry_count: int


class ProvisionalHoldResponse(BaseModel):
    """Response after triggering provisional hold webhook."""

    model_config = ConfigDict(extra="ignore")

    case_id: str
    hold_triggered: bool
    webhook_response_status: int | None
    message: str


class AuditChainVerificationResponse(BaseModel):
    """Hash chain integrity verification response."""

    model_config = ConfigDict(extra="ignore")

    case_id: str
    chain_intact: bool
    entry_count: int
    head_hash: str
    message: str


class RecallMetricsResponse(BaseModel):
    """Aggregate recall service metrics."""

    model_config = ConfigDict(extra="ignore")

    total_cases: int
    by_status: dict[str, int]
    by_reason: dict[str, int]
    provisional_holds_triggered: int
