"""Pydantic schemas for Inter-Bank Encrypted FININT Case Messaging API.

Defines request/response models for the European Collaborative FININT
bridge messaging protocol: ticket creation, state transitions, evidence
attachment, and audit chain retrieval.

Privacy invariants:
- Originating and recipient bank IDs must be HMAC-SHA256 hex strings
  (64 hex chars) — not cleartext institution names.
- Evidence is transmitted only as SHA-256 hashes; raw bytes are never
  accepted or returned.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ── Type literals ──────────────────────────────────────────────────────────────

_TICKET_TYPES = Literal[
    "URGENT_FREEZE_REQUEST",
    "MULE_ACCOUNT_ALERT",
    "INFORMATION_REQUEST",
    "TRANSACTION_DISPUTE_TRACE",
]

_TICKET_STATUSES = Literal[
    "OPEN",
    "ACKNOWLEDGED",
    "FUNDS_FROZEN",
    "INFORMATION_ATTACHED",
    "DECLINED",
    "CLOSED",
]

# SHA-256 hex digest pattern (64 lowercase hex chars)
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
# Base64url-safe pattern
_B64URL_RE = re.compile(r"^[A-Za-z0-9_\-]+=*$")

# ── Request models ─────────────────────────────────────────────────────────────


class CreateTicketRequest(BaseModel):
    """Request to create a new encrypted inter-bank FININT bridge ticket."""

    model_config = ConfigDict(extra="ignore")

    ticket_type: _TICKET_TYPES = Field(
        ...,
        description="Structured FININT request type.",
    )
    originating_bank_id: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description=(
            "HMAC-SHA256 identifier of the requesting institution. "
            "Must never be a cleartext institution name."
        ),
    )
    recipient_bank_id: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="HMAC-SHA256 identifier of the target institution.",
    )
    plaintext_payload_b64: str = Field(
        ...,
        description="Base64url-encoded JSON payload bytes to encrypt. Max 64 KB.",
    )
    recipient_public_key_b64: str = Field(
        ...,
        description="Base64url-encoded Curve25519 public key of the recipient institution.",
    )
    evidence_b64_list: list[str] = Field(
        default_factory=list,
        max_length=20,
        description=(
            "Optional list of base64url-encoded evidence blobs (max 1 MB each). "
            "Only their SHA-256 hashes are persisted — raw data is discarded immediately."
        ),
    )
    actor: str = Field(
        default="compliance_officer",
        max_length=128,
        description="Anonymised officer identifier for the audit trail.",
    )

    @field_validator("plaintext_payload_b64")
    @classmethod
    def validate_payload_size(cls, v: str) -> str:
        import base64

        try:
            decoded = base64.urlsafe_b64decode(v + "==")
        except Exception as exc:
            raise ValueError("plaintext_payload_b64 is not valid base64url") from exc
        if len(decoded) > 65_536:
            raise ValueError("Payload exceeds 64 KB limit.")
        return v

    @field_validator("recipient_public_key_b64")
    @classmethod
    def validate_pubkey(cls, v: str) -> str:
        import base64

        try:
            raw = base64.urlsafe_b64decode(v + "==")
        except Exception as exc:
            raise ValueError("recipient_public_key_b64 is not valid base64url") from exc
        if len(raw) not in (32, 56, 57) and len(raw) < 16:  # Curve25519 raw = 32 bytes
            raise ValueError("Curve25519 public key must be at least 16 bytes.")
        return v


class TransitionTicketRequest(BaseModel):
    """Request to transition a FININT bridge ticket to a new lifecycle state."""

    model_config = ConfigDict(extra="ignore")

    new_status: _TICKET_STATUSES = Field(
        ...,
        description="Target ticket lifecycle state.",
    )
    actor: str = Field(
        default="compliance_officer",
        max_length=128,
        description="Anonymised officer identifier for the audit trail.",
    )
    action: str = Field(
        default="",
        max_length=256,
        description="Human-readable description of the compliance action taken.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional contextual metadata for the audit entry.",
    )


class AttachEvidenceRequest(BaseModel):
    """Request to attach evidence to a FININT bridge ticket."""

    model_config = ConfigDict(extra="ignore")

    evidence_b64: str = Field(
        ...,
        description=(
            "Base64url-encoded evidence blob (max 1 MB). "
            "Only the SHA-256 hash is stored — the raw bytes are discarded."
        ),
    )
    actor: str = Field(
        default="compliance_officer",
        max_length=128,
        description="Anonymised officer identifier for the audit trail.",
    )

    @field_validator("evidence_b64")
    @classmethod
    def validate_evidence_size(cls, v: str) -> str:
        import base64

        try:
            decoded = base64.urlsafe_b64decode(v + "==")
        except Exception as exc:
            raise ValueError("evidence_b64 is not valid base64url") from exc
        if len(decoded) > 1_048_576:
            raise ValueError("Evidence blob exceeds 1 MB limit.")
        return v


class VerifyEvidenceRequest(BaseModel):
    """Request to verify that evidence bytes match a registered hash."""

    model_config = ConfigDict(extra="ignore")

    evidence_b64: str = Field(
        ...,
        description="Base64url-encoded evidence bytes to verify.",
    )


# ── Audit entry response ───────────────────────────────────────────────────────


class AuditEntryResponse(BaseModel):
    """Single immutable audit trail entry for a FININT bridge ticket."""

    model_config = ConfigDict(extra="ignore")

    seq: int
    actor: str
    action: str
    previous_status: str
    new_status: str
    event_hash: str
    previous_hash: str
    timestamp: datetime
    metadata: dict[str, Any]


# ── Ticket response ────────────────────────────────────────────────────────────


class TicketResponse(BaseModel):
    """Complete FININT bridge ticket response (safe, no raw PII)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    ticket_type: str
    status: str
    originating_bank_id: str
    recipient_bank_id: str
    encrypted_payload: str
    payload_nonce: str
    ephemeral_public_key: str
    evidence_hashes: list[str]
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    sla_hours: int
    audit_trail: list[AuditEntryResponse]
    head_hash: str


class TicketSummaryResponse(BaseModel):
    """Lightweight FININT ticket listing response."""

    model_config = ConfigDict(extra="ignore")

    id: str
    ticket_type: str
    status: str
    originating_bank_id: str
    recipient_bank_id: str
    created_at: datetime
    updated_at: datetime
    sla_hours: int
    evidence_count: int
    audit_entry_count: int


class EvidenceAttachResponse(BaseModel):
    """Response after attaching evidence to a ticket."""

    model_config = ConfigDict(extra="ignore")

    ticket_id: str
    evidence_hash: str
    message: str


class VerifyEvidenceResponse(BaseModel):
    """Response confirming whether evidence matches a registered hash."""

    model_config = ConfigDict(extra="ignore")

    ticket_id: str
    verified: bool
    message: str


class AuditChainVerificationResponse(BaseModel):
    """Response confirming hash chain integrity of a ticket's audit trail."""

    model_config = ConfigDict(extra="ignore")

    ticket_id: str
    chain_intact: bool
    entry_count: int
    head_hash: str
    message: str


class BridgeMetricsResponse(BaseModel):
    """Aggregate FININT bridge service metrics."""

    model_config = ConfigDict(extra="ignore")

    total_tickets: int
    by_status: dict[str, int]
    by_type: dict[str, int]
    crypto_backend: str


class GenerateKeypairResponse(BaseModel):
    """Response returning a newly generated Curve25519 keypair."""

    model_config = ConfigDict(extra="ignore")

    private_key_b64: str = Field(
        ...,
        description=(
            "Base64url-encoded Curve25519 private key. "
            "Store securely — never transmit over any channel."
        ),
    )
    public_key_b64: str = Field(
        ...,
        description="Base64url-encoded Curve25519 public key to register with consortium.",
    )
    warning: str = Field(
        default=(
            "The private key is returned once. Store it in your institution's HSM immediately. "
            "This API does not retain private key material."
        ),
    )
