"""Pydantic schemas for Authorized Cross-Bank Intelligence and Evidence Workflow."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_SAFE_TEXT_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _strip_control(value: str) -> str:
    return _SAFE_TEXT_RE.sub("", value)


MatchCategory = Literal["EXACT_MATCH", "PROBABILISTIC_FUZZY", "NO_MATCH"]
SanitizedRiskLevel = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNOBSERVED"]
DisclosureMode = Literal["ATTRIBUTED_INSTITUTIONS", "ANONYMIZED_SUMMARY"]


class CrossBankIntelligenceQueryRequest(BaseModel):
    """Request payload for authorized cross-bank intelligence query."""

    model_config = ConfigDict(str_strip_whitespace=True)

    requesting_bank_id: str = Field(
        ...,
        min_length=2,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Authoritative requesting institution ID.",
    )
    query_entity_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Authoritative requesting bank internal entity ID. Must belong to requesting bank.",
    )
    privacy_id: str | None = Field(
        default=None,
        max_length=256,
        description="Optional expected privacy hash to verify against local entity.",
    )
    consortium_id: str = Field(
        default="consortium_alpha",
        min_length=2,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Consortium ID governing cross-bank intelligence sharing.",
    )
    target_bank_id: str | None = Field(
        default=None,
        max_length=64,
        description="Optional specific counterpart bank ID. If omitted, checks all active consortium participants.",
    )
    purpose: str = Field(
        default="AML_COLLABORATIVE_INVESTIGATION",
        max_length=128,
        description="Permitted intelligence query purpose under consortium sharing treaty.",
    )
    execution_mode: Literal["VERIFIED_OPERATIONAL", "SIMULATED_RESEARCH"] = Field(
        default="SIMULATED_RESEARCH",
        description="Execution mode: VERIFIED_OPERATIONAL requires hardware enclave and multi-party PSI; SIMULATED_RESEARCH is software emulation.",
    )
    require_hardware_attestation: bool = Field(
        default=False,
        description="If True, strictly requires physical hardware enclave attestation; fails closed if unavailable.",
    )


class CrossBankIntelligenceSignal(BaseModel):
    """Sanitized, institution-scoped cross-bank intelligence signal."""

    model_config = ConfigDict(str_strip_whitespace=True)

    signal_id: str = Field(..., description="Unique immutable signal ID.")
    consortium_id: str = Field(..., description="Authoritative consortium ID.")
    requesting_bank_id: str = Field(..., description="Requesting bank ID.")
    query_entity_id: str | None = Field(default=None, description="Requesting bank's entity ID.")
    privacy_hash_truncated: str = Field(..., description="Truncated privacy-preserving hash.")
    match_confirmed: bool = Field(
        ..., description="True if counterpart institution observes this entity."
    )
    match_category: MatchCategory = Field(..., description="Matching classification category.")
    match_confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Match confidence score [0.0, 1.0]."
    )
    contributing_institutions_count: int = Field(
        ..., ge=0, description="Number of distinct independent institutions observing this entity."
    )
    contributing_institutions: list[str] = Field(
        default_factory=list,
        description="List of participating institution IDs if disclosure policy allows.",
    )
    disclosure_mode: DisclosureMode = Field(
        default="ANONYMIZED_SUMMARY",
        description="Disclosure granularity mode authorized by consortium policy.",
    )
    sanitized_risk_level: SanitizedRiskLevel = Field(
        ..., description="Sanitized multi-institutional risk classification."
    )
    sanitized_typology_indicators: list[str] = Field(
        default_factory=list,
        description="Sanitized typology indicators (e.g. MULTI_INSTITUTION_EXPOSURE, RAPID_HOP_OVERLAP).",
    )
    graph_context: dict[str, Any] = Field(
        default_factory=dict,
        description="Institution-scoped sanitized graph signals without counterpart raw topologies.",
    )
    provenance: dict[str, Any] = Field(
        default_factory=dict,
        description="Observation provenance timestamps and audit integrity metadata.",
    )
    execution_mode: str = Field(
        ..., description="Execution mode (VERIFIED_OPERATIONAL or SIMULATED_RESEARCH)."
    )
    attestation_status: str = Field(
        ...,
        description="Hardware attestation status (UNAVAILABLE_NO_HARDWARE_ENCLAVE or SOFTWARE_EMULATED).",
    )
    is_authoritative: bool = Field(
        default=True,
        description="True if signal represents authoritative operational intelligence; False for research simulations.",
    )
    is_synthetic: bool = Field(
        default=False,
        description="True if signal was derived from synthetic or research simulation datasets.",
    )
    disclaimer: str = Field(
        default=(
            "Cross-bank intelligence is advisory and privacy-preserving. "
            "It does not constitute a definitive accusation of fraud and must not be used "
            "to automatically penalize or close cases without human Four-Eyes verification."
        ),
        description="Mandatory regulatory disclaimer.",
    )


class CrossBankEvidenceAttachRequest(BaseModel):
    """Request payload to bind a verified intelligence signal to a case evidence ledger."""

    model_config = ConfigDict(str_strip_whitespace=True)

    case_id: str = Field(
        ..., min_length=2, max_length=64, description="Target investigation case ID."
    )
    signal_id: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Authoritative, server-issued cross-bank intelligence signal ID.",
    )
    notes: str | None = Field(
        default=None, max_length=1000, description="Optional analyst investigation note."
    )
    idempotency_key: str | None = Field(
        default=None, max_length=128, description="Optional idempotency key."
    )


class CrossBankEvidenceAttachResponse(BaseModel):
    """Response returned upon successfully registering cross-bank evidence."""

    evidence_id: str
    case_id: str
    signal_id: str
    evidence_type: str
    title: str
    content_hash: str
    uploaded_at: str
    uploaded_by: str
    case_version: int
    timeline_event_recorded: bool
    is_duplicate_submission: bool = False
