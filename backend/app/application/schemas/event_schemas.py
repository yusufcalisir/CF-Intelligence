"""CloudEvents 1.0 Specification Schemas for Enterprise Kafka & Financial Event Streaming (Phase 114).

Complies strictly with:
- CNCF CloudEvents v1.0 Core Specification
- Enterprise Cross-Bank Financial Intelligence (CFI) Event Bus
- ISO 20022 and European RegTech payload envelopes
- Dead Letter Queue (DLQ) error isolation models
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ── Canonical Event Type Constants ────────────────────────────────────────────

EVENT_TYPE_TRANSACTION = "org.cfi.finint.transaction.v1"
EVENT_TYPE_ALERT = "org.cfi.finint.alert.v1"
EVENT_TYPE_RECALL = "org.cfi.finint.recall.v1"
EVENT_TYPE_HOLD = "org.cfi.finint.hold.v1"
EVENT_TYPE_DEAD_LETTER = "org.cfi.finint.deadletter.v1"
EVENT_TYPE_FEDERATION_ROUND = "org.cfi.finint.federation.v1"

ALLOWED_EVENT_TYPES = {
    EVENT_TYPE_TRANSACTION,
    EVENT_TYPE_ALERT,
    EVENT_TYPE_RECALL,
    EVENT_TYPE_HOLD,
    EVENT_TYPE_DEAD_LETTER,
    EVENT_TYPE_FEDERATION_ROUND,
}


# ── Structured Domain Event Payloads ──────────────────────────────────────────

class TransactionEventData(BaseModel):
    """Payload data for transaction payment events."""

    model_config = ConfigDict(extra="forbid")

    transaction_id: str = Field(..., description="Unique transaction identifier")
    amount: Decimal = Field(..., description="Transaction amount (EUR/ISO currency)", ge=Decimal("0.01"))
    currency: str = Field("EUR", description="ISO 4217 3-letter currency code")
    originator_iban_hash: str = Field(..., description="HMAC-SHA256 salted hash of originator account")
    beneficiary_iban_hash: str = Field(..., description="HMAC-SHA256 salted hash of beneficiary account")
    origin_country: str = Field("DE", description="ISO 3166-1 alpha-2 origin country code")
    destination_country: str = Field("FR", description="ISO 3166-1 alpha-2 destination country code")
    payment_rail: str = Field("SEPA_INSTANT", description="Payment rail standard")
    risk_score: float | None = Field(None, description="Composite risk score 0-1000", ge=0.0, le=1000.0)
    merchant_category: str = Field("wire_transfer", description="MCC or business classification")
    direction: Literal["INBOUND", "OUTBOUND", "INTERNAL"] = Field("OUTBOUND", description="Flow direction")

    @field_validator("currency")
    @classmethod
    def _validate_currency(cls, v: str) -> str:
        v = v.strip().upper()
        if len(v) != 3:
            raise ValueError(f"Currency code must be 3 uppercase letters, got {v!r}")
        return v


class AlertEventData(BaseModel):
    """Payload data for real-time high-risk fraud alerts."""

    model_config = ConfigDict(extra="forbid")

    alert_id: str = Field(..., description="Unique alert identifier")
    transaction_id: str = Field(..., description="Associated transaction ID")
    bank_id: str = Field(..., description="Originating or observing bank node ID")
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = Field("HIGH", description="Alert severity")
    composite_risk_score: float = Field(..., description="Normalized composite risk score", ge=0.0, le=1000.0)
    triggered_rules: list[str] = Field(default_factory=list, description="Identifiers of triggered rules")
    action_recommended: Literal["ALLOW", "MANUAL_REVIEW", "SAR_ESCALATION", "IMMEDIATE_BLOCK"] = Field(
        "MANUAL_REVIEW", description="Statutory action recommended"
    )


class RecallEventData(BaseModel):
    """Payload data for ISO 20022 camt.056 payment recalls."""

    model_config = ConfigDict(extra="forbid")

    recall_id: str = Field(..., description="SEPA recall tracking identifier")
    message_id: str = Field(..., description="ISO 20022 camt.056 message reference")
    original_instruction_id: str = Field(..., description="Original transaction instruction ID")
    amount_eur: Decimal = Field(..., description="Amount recalled in EUR", ge=Decimal("0.01"))
    reason_code: Literal["FRAD", "TECH", "DUPL", "CUST", "UPAY", "COVR"] = Field(
        "FRAD", description="SEPA SCT Inst recall reason code"
    )
    status: Literal["INITIATED", "SENT", "ACKNOWLEDGED", "PROVISIONAL_HOLD_ACTIVE", "FUNDS_RETURNED", "REJECTED"] = (
        Field("INITIATED", description="Lifecycle status")
    )
    originating_bank: str = Field(..., description="Recalling bank node ID")
    target_bank: str = Field(..., description="Receiving bank node ID")


class FININTTicketEventData(BaseModel):
    """Payload data for encrypted inter-bank FININT case exchange."""

    model_config = ConfigDict(extra="forbid")

    ticket_id: str = Field(..., description="FININT case ticket ID")
    originating_bank: str = Field(..., description="Requesting bank ID")
    target_bank: str = Field(..., description="Recipient bank ID")
    case_type: str = Field("MULE_NETWORK_INVESTIGATION", description="Investigation typology")
    priority: Literal["ROUTINE", "URGENT", "IMMEDIATE"] = Field("URGENT", description="Priority level")
    evidence_hash: str = Field(..., description="SHA-256 hash of unencrypted evidence commitment")


# ── Canonical CloudEvent 1.0 Specification Model ──────────────────────────────

class CloudEvent(BaseModel):
    """CNCF CloudEvents v1.0 compliant envelope for cross-bank event streaming.

    Specification Invariants:
    - specversion MUST be '1.0'
    - id MUST be non-empty and unique per source
    - source MUST be a valid URI-reference identifying the event context
    - type MUST be non-empty string categorizing the event
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    # Mandatory CloudEvents 1.0 Attributes
    specversion: Literal["1.0"] = Field("1.0", description="CloudEvents specification version")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique event identifier")
    source: str = Field(..., description="URI-reference identifying event context (e.g. urn:cfi:bank:ALPHA)")
    type: str = Field(..., description="Event type identifier (e.g. org.cfi.finint.transaction.v1)")

    # Optional / Context CloudEvents Attributes
    time: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp in UTC format RFC 3339",
    )
    datacontenttype: str = Field("application/json", description="Content type of data payload")
    dataschema: str | None = Field(None, description="URI identifying data schema")
    subject: str | None = Field(None, description="Subject of the event in source context")

    # Domain Payload Data
    data: dict[str, Any] | BaseModel = Field(default_factory=dict, description="Event domain payload")

    # Banking & FININT Extension Attributes (ce-*)
    ce_bank_id: str | None = Field(None, description="Bank node identifier", alias="bank_id")
    ce_correlation_id: str | None = Field(None, description="Distributed correlation trace ID", alias="correlation_id")
    ce_tenant_id: str | None = Field(None, description="Multi-tenant schema partition key", alias="tenant_id")
    ce_idempotency_key: str | None = Field(None, description="Deduplication key for idempotency", alias="idempotency_key")
    ce_signature: str | None = Field(None, description="HMAC-SHA256 signature over event data", alias="signature")

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("CloudEvent 'id' must be a non-empty string")
        return v.strip()

    @field_validator("source")
    @classmethod
    def _validate_source(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("CloudEvent 'source' must be a non-empty URI-reference string")
        return v.strip()

    @field_validator("type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("CloudEvent 'type' must be a non-empty string")
        return v.strip()

    def to_cloudevent_dict(self) -> dict[str, Any]:
        """Serialize CloudEvent to canonical JSON-compatible dictionary."""
        data_payload = self.data
        if isinstance(data_payload, BaseModel):
            data_payload = data_payload.model_dump(mode="json")
        elif isinstance(data_payload, dict):
            # Convert any Decimals or nested datetimes to JSON primitives
            data_payload = json.loads(json.dumps(data_payload, default=str))

        result: dict[str, Any] = {
            "specversion": self.specversion,
            "id": self.id,
            "source": self.source,
            "type": self.type,
            "time": self.time.isoformat(),
            "datacontenttype": self.datacontenttype,
            "data": data_payload,
        }
        if self.dataschema:
            result["dataschema"] = self.dataschema
        if self.subject:
            result["subject"] = self.subject
        if self.ce_bank_id:
            result["bank_id"] = self.ce_bank_id
        if self.ce_correlation_id:
            result["correlation_id"] = self.ce_correlation_id
        if self.ce_tenant_id:
            result["tenant_id"] = self.ce_tenant_id
        if self.ce_idempotency_key:
            result["idempotency_key"] = self.ce_idempotency_key
        if self.ce_signature:
            result["signature"] = self.ce_signature

        return result

    def to_json_bytes(self) -> bytes:
        """Serialize event to UTF-8 encoded JSON bytes for Kafka publishing."""
        return json.dumps(self.to_cloudevent_dict()).encode("utf-8")

    @classmethod
    def from_raw_json(cls, raw: str | bytes) -> CloudEvent:
        """Parse raw JSON string or bytes into a validated CloudEvent."""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError(f"Expected JSON object for CloudEvent, got {type(parsed).__name__}")
        return cls.model_validate(parsed)


# ── Dead Letter Queue (DLQ) Envelope ──────────────────────────────────────────

class DLQEnvelope(BaseModel):
    """Envelope for unparseable, malformed, or poisoned messages routed to Dead Letter Queue."""

    model_config = ConfigDict(extra="forbid")

    dead_letter_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="DLQ incident reference")
    original_topic: str = Field(..., description="Source topic where message was consumed")
    dlq_topic: str = Field("cfi.dlq.unparseable", description="Destination DLQ topic")
    raw_payload: str = Field(..., description="Raw message payload string")
    error_type: str = Field(..., description="Exception class name")
    error_message: str = Field(..., description="Human-readable reason for quarantine")
    failed_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Failure timestamp UTC")
    retry_count: int = Field(0, description="Number of delivery attempts made", ge=0)
    can_retry: bool = Field(True, description="Whether message can be retried or requires manual disposition")
    originating_bank_id: str | None = Field(None, description="Identified bank node if extractable")


# ── Kafka Delivery Receipt ───────────────────────────────────────────────────

class PublishReceipt(BaseModel):
    """Receipt returned upon committing an event to Kafka or in-memory streaming bus."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(..., description="CloudEvent ID published")
    topic: str = Field(..., description="Kafka topic committed to")
    partition: int = Field(0, description="Assigned broker partition", ge=0)
    offset: int = Field(0, description="Committed log offset", ge=0)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Broker timestamp UTC")
    status: Literal["COMMITTED", "DUPLICATE_IGNORED", "DLQ_ROUTED", "FAILED"] = Field(
        "COMMITTED", description="Publish status"
    )
    idempotent_duplicate: bool = Field(False, description="True if skipped due to distributed deduplication")
    latency_ms: float = Field(0.0, description="Publish latency in milliseconds", ge=0.0)
