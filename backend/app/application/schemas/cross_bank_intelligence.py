"""Pydantic schemas for Authorized Cross-Bank Intelligence and Evidence Workflow."""

from __future__ import annotations

import math
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

_SAFE_TEXT_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _strip_control(value: str) -> str:
    return _SAFE_TEXT_RE.sub("", value)


MatchCategory = Literal["EXACT_MATCH", "PROBABILISTIC_FUZZY", "NO_MATCH"]
SanitizedRiskLevel = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNOBSERVED", "UNAVAILABLE"]
DisclosureMode = Literal["ATTRIBUTED_INSTITUTIONS", "ANONYMIZED_SUMMARY"]
AssuranceLevel = Literal[
    "PROTOCOL_PROTOTYPE",
    "ISOLATED_INTEGRATION_VERIFIED",
    "OPERATIONAL_PRIVACY_VERIFIED",
    "REVIEWED_ESTABLISHED_PSI_VERIFIED",
]


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
    case_id: str | None = Field(
        default=None,
        max_length=128,
        description="Optional active case ID to bind and verify against the query.",
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
    use_established_psi: bool = Field(
        default=False,
        description="Whether to execute reviewed established PSI protocol (OpenMined PSI ECDH) instead of protocol prototype.",
    )
    require_established_psi: bool = Field(
        default=False,
        description="If True, strictly fails closed if reviewed established PSI is unavailable.",
    )
    protocol: str = Field(
        default="",
        max_length=64,
        description="Optional explicit protocol selection (e.g. OPENMINED_PSI, DH-PSI-2048-MODP).",
    )
    pilot_id: str | None = Field(
        default=None,
        max_length=64,
        description="Optional active controlled two-bank pilot ID governing this execution.",
    )
    timeout_seconds: float | None = Field(
        default=None,
        ge=1.0,
        le=60.0,
        description="Optional caller-specified execution timeout in seconds allocated for this counterparty query (bounded [1.0, 60.0]).",
    )
    circuit_breaker_enabled: bool | None = Field(
        default=None,
        description="Optional explicit override to enable or disable circuit breaker enforcement for this counterpart.",
    )



class MultiCounterpartyIntelligenceQueryRequest(BaseModel):
    """Request payload for authorized multi-counterparty cross-bank intelligence query."""

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
    target_bank_ids: list[str] = Field(
        ...,
        min_length=1,
        max_length=10,
        description="Explicit, non-empty list of distinct counterpart institution IDs (max 10).",
    )
    case_id: str | None = Field(
        default=None,
        max_length=128,
        description="Optional active case ID to bind and verify against the query.",
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
    use_established_psi: bool = Field(
        default=False,
        description="Whether to execute reviewed established PSI protocol (OpenMined PSI ECDH) instead of protocol prototype.",
    )
    require_established_psi: bool = Field(
        default=False,
        description="If True, strictly fails closed if reviewed established PSI is unavailable.",
    )
    protocol: str = Field(
        default="",
        max_length=64,
        description="Optional explicit protocol selection (e.g. OPENMINED_PSI, DH-PSI-2048-MODP).",
    )
    pilot_id: str | None = Field(
        default=None,
        max_length=64,
        description="Optional active controlled two-bank pilot ID governing this execution.",
    )
    idempotency_key: str | None = Field(
        default=None,
        max_length=256,
        description="Optional client-specified idempotency key to prevent duplicate execution.",
    )
    per_counterparty_timeout_seconds: float | None = Field(
        default=None,
        ge=1.0,
        le=60.0,
        description="Optional explicit execution timeout in seconds allocated per counterpart institution (bounded [1.0, 60.0]).",
    )
    total_batch_timeout_seconds: float | None = Field(
        default=None,
        ge=1.0,
        le=300.0,
        description="Optional total wall-clock execution deadline in seconds for the complete query batch (bounded [1.0, 300.0]).",
    )
    circuit_breaker_enabled: bool | None = Field(
        default=None,
        description="Optional explicit override to enable or disable per-counterparty circuit breaker enforcement.",
    )
    circuit_breaker_failure_threshold: int | None = Field(
        default=None,
        ge=1,
        le=50,
        description="Optional failure threshold count before circuit breaker opens (bounded [1, 50]).",
    )
    circuit_breaker_cooldown_seconds: float | None = Field(
        default=None,
        ge=0.001,
        le=3600.0,
        description="Optional cooldown period in seconds before circuit breaker transitions to HALF_OPEN (bounded [0.001, 3600.0]).",
    )

    @model_validator(mode="after")
    def validate_multi_targets(self) -> MultiCounterpartyIntelligenceQueryRequest:
        if not self.target_bank_ids:
            raise ValueError("target_bank_ids must contain at least one counterpart institution.")
        if len(self.target_bank_ids) > 10:
            raise ValueError("target_bank_ids exceeds maximum permitted counterpart institutions (10).")

        seen: set[str] = set()
        cleaned_targets: list[str] = []
        clean_req = self.requesting_bank_id.lower().strip()
        for t in self.target_bank_ids:
            clean_t = t.lower().strip()
            if not clean_t:
                raise ValueError("Target bank ID must be non-empty.")
            if clean_t in seen:
                raise ValueError(f"Duplicate target bank ID '{clean_t}' detected in query request.")
            if clean_t == clean_req:
                raise ValueError(
                    f"Self-targeting is prohibited: requesting institution '{clean_req}' cannot be in target_bank_ids."
                )
            seen.add(clean_t)
            cleaned_targets.append(clean_t)

        self.target_bank_ids = cleaned_targets

        if self.per_counterparty_timeout_seconds is not None and (
            not math.isfinite(self.per_counterparty_timeout_seconds)
            or self.per_counterparty_timeout_seconds < 1.0
            or self.per_counterparty_timeout_seconds > 60.0
        ):
            raise ValueError(
                "per_counterparty_timeout_seconds must be a finite float between 1.0 and 60.0 seconds."
            )

        if self.total_batch_timeout_seconds is not None and (
            not math.isfinite(self.total_batch_timeout_seconds)
            or self.total_batch_timeout_seconds < 1.0
            or self.total_batch_timeout_seconds > 300.0
        ):
            raise ValueError(
                "total_batch_timeout_seconds must be a finite float between 1.0 and 300.0 seconds."
            )

        return self


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
    assurance_level: AssuranceLevel = Field(
        default="ISOLATED_INTEGRATION_VERIFIED",
        description="Verifiable security assurance level based on actual runtime environment.",
    )
    disclaimer: str = Field(
        default=(
            "Cross-bank intelligence is advisory and privacy-preserving. "
            "It does not constitute a definitive accusation of fraud and must not be used "
            "to automatically penalize or close cases without human Four-Eyes verification."
        ),
        description="Mandatory regulatory disclaimer.",
    )


OverallQueryOutcome = Literal["COMPLETED", "PARTIAL_FAILURE", "FAILED"]
CounterpartyOutcomeStatus = Literal["SUCCESS", "FAILED"]


class CounterpartyExecutionFailure(BaseModel):
    """Sanitized failure details for an execution against an authorized counterpart."""

    model_config = ConfigDict(str_strip_whitespace=True)

    target_bank_id: str = Field(
        ..., description="Target counterpart institution ID that experienced execution failure."
    )
    error_category: str = Field(
        ...,
        description="Sanitized failure category (e.g. WORKER_UNAVAILABLE, TRANSPORT_FAILURE, TIMEOUT, EXECUTION_ERROR).",
    )
    error_message: str = Field(
        ...,
        description="Sanitized, non-sensitive failure summary without internal credentials, IPs, or private data.",
    )
    failed_at: str = Field(..., description="ISO-8601 failure timestamp.")
    retryable: bool = Field(
        default=True,
        description="Indicates whether this failure might be transient and potentially retryable.",
    )


class CounterpartyExecutionOutcome(BaseModel):
    """Typed execution outcome for an individual counterpart institution."""

    model_config = ConfigDict(str_strip_whitespace=True)

    target_bank_id: str = Field(..., description="Counterpart institution ID.")
    status: CounterpartyOutcomeStatus = Field(
        ...,
        description="Outcome status for this counterpart ('SUCCESS' or 'FAILED').",
    )
    signal: CrossBankIntelligenceSignal | None = Field(
        default=None,
        description="Verifiable cross-bank intelligence signal when status is SUCCESS; None on failure.",
    )
    failure: CounterpartyExecutionFailure | None = Field(
        default=None,
        description="Sanitized failure information when status is FAILED; None on success.",
    )


class MultiCounterpartyIntelligenceQueryResponse(BaseModel):
    """Response payload for multi-counterparty cross-bank intelligence query."""

    model_config = ConfigDict(str_strip_whitespace=True)

    query_id: str = Field(..., description="Unique query execution batch identifier.")
    consortium_id: str = Field(..., description="Authoritative consortium ID.")
    requesting_bank_id: str = Field(..., description="Authoritative requesting institution ID.")
    query_entity_id: str = Field(..., description="Requesting bank's entity ID.")
    counterpart_count: int = Field(
        ..., ge=1, description="Number of distinct counterpart institutions queried."
    )
    overall_outcome: OverallQueryOutcome = Field(
        default="COMPLETED",
        description="Overall execution outcome across all requested counterparts: COMPLETED, PARTIAL_FAILURE, or FAILED.",
    )
    outcome: OverallQueryOutcome = Field(
        default="COMPLETED",
        description="Direct alias for overall_outcome to support consumer conventions.",
    )
    results: dict[str, CrossBankIntelligenceSignal] = Field(
        default_factory=dict,
        description="Map of counterpart bank ID to separately verifiable, sanitized cross-bank intelligence signal for successful executions.",
    )
    failures: dict[str, CounterpartyExecutionFailure] = Field(
        default_factory=dict,
        description="Map of counterpart bank ID to sanitized execution failure details for failed executions.",
    )
    execution_outcomes: dict[str, CounterpartyExecutionOutcome] = Field(
        default_factory=dict,
        description="Typed per-counterparty execution outcomes mapping target_bank_id to outcome status and details.",
    )
    executed_at: str = Field(..., description="ISO-8601 execution timestamp.")
    _idempotency_replayed: bool = PrivateAttr(default=False)


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
