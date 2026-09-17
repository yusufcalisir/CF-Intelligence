"""Pydantic schemas for Entities, Entity Resolution, MinHash LSH, and DH-PSI.

Clean Architecture schema definitions for cross-bank entity lookups,
Diffie-Hellman 2048-bit Private Set Intersection, MinHash LSH fuzzy matching,
Zero-PII type-salted HMAC tokenization, and GDPR right-to-erasure workflows.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Sentinel regex to strip ASCII control characters
_SAFE_TEXT_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _strip_control(value: str) -> str:
    """Remove ASCII control characters."""
    return _SAFE_TEXT_RE.sub("", value)


class EntityResponse(BaseModel):
    """Consortium entity representation with pseudonymized privacy identifier."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., description="Unique entity database identifier")
    entity_type: str = Field(..., description="Entity category: customer, account, merchant, card, device, ip")
    privacy_id: str = Field(..., description="Type-salted HMAC-SHA256 pseudonymized identifier")
    bank_id: str = Field(..., description="Originating bank institution ID")
    display_label: str = Field(..., description="Sanitized display label or pseudonymized mask")
    attributes: dict[str, Any] = Field(default_factory=dict, description="Pseudonymized attribute dictionary")
    risk_level: str = Field(..., description="Assessed risk level: low, medium, high, critical")
    alert_count: int = Field(default=0, ge=0, description="Cumulative fraud alerts associated with entity")
    first_seen: str = Field(..., description="ISO 8601 timestamp of first observation")
    last_seen: str = Field(..., description="ISO 8601 timestamp of last transaction/activity")


class EntityProfileResponse(BaseModel):
    """Deep cross-institutional dossier for an entity."""

    model_config = ConfigDict(extra="ignore")

    entity_id: str
    entity_type: str
    privacy_id: str
    display_label: str
    bank_id: str
    risk_level: str
    alert_count: int = 0
    relationship_count: int = 0
    cross_institution_count: int = 0
    banks_present: list[str] = Field(default_factory=list)
    first_seen: str
    last_seen: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class EntityRelationshipItem(BaseModel):
    """Direct graph edge / relationship linking two entities."""

    model_config = ConfigDict(extra="ignore")

    id: str
    source_entity_id: str
    target_entity_id: str
    relationship_type: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: str = ""
    created_at: str


class EntityDeleteResponse(BaseModel):
    """Confirmation of GDPR Art. 17 right-to-erasure entity purge."""

    model_config = ConfigDict(extra="ignore")

    deleted: bool = True
    entity_id: str
    policy: str = "GDPR Art. 17 Right-to-Erasure Enforced"


class EntityResolveRequest(BaseModel):
    """Request to resolve cross-institution entities via type-salted privacy hash."""

    model_config = ConfigDict(extra="ignore")

    privacy_hash: str = Field(
        ...,
        min_length=16,
        max_length=128,
        pattern=r"^[a-fA-F0-9]+$",
        description="HMAC-SHA256 hex digest of the canonical identifier",
    )


class HMACTokenizeRequest(BaseModel):
    """Request to tokenize a raw identifier under consortium type-salting."""

    model_config = ConfigDict(extra="ignore")

    identifier: str = Field(..., min_length=1, max_length=256, description="Raw customer identifier (IBAN, PAN, phone)")
    tenant_salt: str = Field(default="default_consortium_salt", min_length=1, max_length=128, description="Institution salt")

    @field_validator("identifier", "tenant_salt")
    @classmethod
    def sanitize_strings(cls, v: str) -> str:
        return _strip_control(v.strip())


class HMACTokenizeResponse(BaseModel):
    """Pseudonymized HMAC token response enforcing Zero Raw PII."""

    model_config = ConfigDict(extra="ignore")

    hmac_token: str
    policy: str = "Zero Raw PII Policy Enforced"
    algorithm: str = "HMAC-SHA256"


class PSIRequest(BaseModel):
    """Request parameters to execute Diffie-Hellman Private Set Intersection."""

    model_config = ConfigDict(extra="ignore")

    bank_a_id: str = Field(
        ...,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Source bank node institution ID",
    )
    bank_b_id: str = Field(
        ...,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Target bank node institution ID",
    )
    entity_type: str | None = Field(
        default=None,
        max_length=32,
        pattern=r"^[a-zA-Z_]+$",
        description="Optional entity type filter (e.g. customer, account)",
    )
    enable_fuzzy: bool = Field(default=False, description="Enable multi-attribute MinHash fuzzy matching")
    fuzzy_threshold: int = Field(default=3, ge=1, le=10, description="Minimum overlapping attribute threshold")
    enable_tee: bool = Field(default=False, description="Simulate TEE enclave-backed execution")


class PSIMatch(BaseModel):
    """A matched intersection element between two bank partitions."""

    model_config = ConfigDict(extra="ignore")

    privacy_hash: str
    entity_type: str
    display_label_a: str
    display_label_b: str
    risk_level_a: str
    risk_level_b: str
    matched_attributes: list[str] = Field(default_factory=list)
    similarity_score: float = 1.0


class PSIProtocolStats(BaseModel):
    """Cryptographic telemetry recorded during DH-PSI execution."""

    model_config = ConfigDict(extra="ignore")

    computation_time_ms: float = 0.0
    data_exchanged_bytes: int = 0
    num_entities_a: int = 0
    num_entities_b: int = 0
    prime_bit_length: int = 2048
    enclave_execution: bool = False
    mrenclave: str | None = None
    mrsigner: str | None = None
    attestation_verified: bool | None = None


class PSIResponse(BaseModel):
    """Full results from simulated DH-PSI intersection protocol."""

    model_config = ConfigDict(extra="ignore")

    matches: list[PSIMatch] = Field(default_factory=list)
    stats: PSIProtocolStats = Field(default_factory=PSIProtocolStats)


class PSIMatchDirectRequest(BaseModel):
    """Direct DH-PSI request matching /api/v1/psi/match."""

    model_config = ConfigDict(extra="ignore")

    source_bank_id: str = Field(
        default="bank_alpha",
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Initiating bank participant",
    )
    target_bank_id: str = Field(
        default="bank_beta",
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Counterparty bank participant",
    )
    client_ecdh_blinded_hashes: list[str] = Field(
        default_factory=list,
        description="Client-side blinded elements H(x)^a in hex format",
    )
    enable_fuzzy: bool = Field(default=True, description="Enable fuzzy attribute resolution")


class PSIMatchDirectResponse(BaseModel):
    """Response returned from DH-PSI match endpoints."""

    model_config = ConfigDict(extra="ignore")

    protocol: str = "Commutative Diffie-Hellman (DH-PSI)"
    matched_cardinality: int = 0
    matches: list[dict[str, Any]] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    zero_raw_pii_enforced: bool = True


class EntityFuzzyResolveRequest(BaseModel):
    """Request to resolve customer records via MinHash LSH fuzzy similarity."""

    model_config = ConfigDict(extra="ignore")

    query_name: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description="Entity name or alias to fuzzy-match",
    )
    entity_type: str = Field(
        default="customer",
        description="Entity type filter",
    )
    threshold: float = Field(
        default=0.70,
        ge=0.0,
        le=1.0,
        description="Minimum Jaccard similarity score [0.0, 1.0]",
    )
    bank_id: str | None = Field(
        default=None,
        max_length=64,
        description="Optional bank tenant ID filter",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Maximum matched candidates to return",
    )

    @field_validator("query_name")
    @classmethod
    def sanitize_query_name(cls, v: str) -> str:
        return _strip_control(v.strip())


class EntityFuzzyResolveMatch(BaseModel):
    """Candidate entity match returned by MinHash LSH."""

    model_config = ConfigDict(extra="ignore")

    entity: EntityResponse
    similarity_score: float = Field(..., ge=0.0, le=1.0)


class EntityFuzzyResolveResponse(BaseModel):
    """Results of MinHash LSH fuzzy entity resolution."""

    model_config = ConfigDict(extra="ignore")

    matches: list[EntityFuzzyResolveMatch] = Field(default_factory=list)


class PSIStatsResponse(BaseModel):
    """Public protocol metadata and capabilities of DH-PSI engine."""

    model_config = ConfigDict(extra="ignore")

    protocol: str = "Commutative Diffie-Hellman (DH-PSI 2048-bit)"
    prime_bit_length: int = 2048
    hash_function: str = "HMAC-SHA256"
    supported_modes: list[str] = Field(default_factory=lambda: ["exact", "fuzzy_minhash", "tee_sgx"])
    zero_raw_pii_guarantee: str = "Zero plaintext PII transmission across bank perimeters"
