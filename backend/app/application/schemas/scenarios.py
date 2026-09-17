"""Scenario replay and adversarial attack injection schemas.

Provides strictly validated Pydantic v2 schemas for simulation replay controls,
streaming progress, and Byzantine/sybil attack injection endpoints.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ScenarioInfoResponse(BaseModel):
    """Metadata describing a pre-built cross-bank fraud scenario."""

    model_config = ConfigDict(extra="ignore")

    type: str = Field(..., description="Scenario unique type identifier")
    name: str = Field(..., description="Human-readable scenario title")
    description: str = Field(..., description="Detailed description of attack topology")
    banks_involved: list[str] = Field(default_factory=list, description="List of participant bank identifiers")
    estimated_events: int = Field(..., ge=0, description="Estimated total count of transaction events")
    estimated_duration_seconds: float = Field(..., ge=0.0, description="Estimated playback time at 1.0x speed")


class ScenarioStartRequest(BaseModel):
    """Request parameters to trigger scenario event streaming."""

    model_config = ConfigDict(extra="forbid")

    scenario_type: str = Field(
        ...,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Scenario type identifier (e.g. fraud_ring, account_takeover, money_laundering, card_testing)",
    )
    speed_multiplier: float = Field(
        default=1.0,
        ge=0.1,
        le=10.0,
        description="Playback speed multiplier between 0.1x and 10.0x",
    )


class ScenarioStartResponse(BaseModel):
    """Confirmation payload returned when scenario streaming initiates."""

    model_config = ConfigDict(extra="ignore")

    scenario_id: str = Field(..., description="Unique UUID assigned to this streaming execution")
    scenario_type: str = Field(..., description="Underlying scenario archetype")
    name: str = Field(..., description="Scenario descriptive name")
    total_events: int = Field(..., ge=0, description="Total queued transaction events")
    status: str = Field(default="running", description="Initial stream lifecycle status")


class ScenarioStatusResponse(BaseModel):
    """Real-time streaming status of an active or completed scenario."""

    model_config = ConfigDict(extra="ignore")

    scenario_id: str = Field(..., description="UUID of the queried scenario")
    status: str = Field(..., description="Current status: running, completed, or stopped")
    total_events: int = Field(..., ge=0, description="Total events in scenario sequence")
    delivered_events: int = Field(..., ge=0, description="Events pushed to stream so far")
    speed_multiplier: float = Field(..., ge=0.1, description="Configured replay speed")
    started_at: str = Field(..., description="ISO 8601 timestamp of execution initiation")


class ScenarioStopResponse(BaseModel):
    """Result returned when halting an active scenario stream."""

    model_config = ConfigDict(extra="ignore")

    scenario_id: str = Field(..., description="UUID of the stopped scenario")
    status: str = Field(default="stopped", description="Terminal state of the stream")


class ActiveScenarioItem(BaseModel):
    """Summary of a currently running streaming scenario."""

    model_config = ConfigDict(extra="ignore")

    scenario_id: str = Field(..., description="Unique scenario run identifier")
    status: str = Field(..., description="Execution status")
    total_events: int = Field(..., ge=0, description="Total events planned")
    delivered_events: int = Field(..., ge=0, description="Delivered events count")
    speed_multiplier: float = Field(..., ge=0.1, description="Replay speed")
    started_at: str = Field(..., description="Start timestamp")


class AttackInjectionRequest(BaseModel):
    """Payload to trigger adversarial attack injection and defensive shield verification."""

    model_config = ConfigDict(extra="forbid")

    attack_type: Literal["smurfing_layering", "byzantine_poisoning", "sybil_ring"] = Field(
        ...,
        description="Category of chaos threat injected into consortium",
    )
    adversary_bank: str = Field(
        default="bank_gamma",
        min_length=2,
        max_length=64,
        description="Bank node or malicious insider propagating the attack",
    )
    target_bank: str = Field(
        default="bank_alpha",
        min_length=2,
        max_length=64,
        description="Target institution receiving illicit funds or aggregating gradients",
    )
    intensity_rate: int = Field(
        default=500,
        ge=10,
        le=5000,
        description="Burst transaction rate or gradient poisoning scaling intensity",
    )
    defense_strategy: Literal["krum", "trimmed_mean", "bulyan", "spectral", "spectral_svd", "psi_graph"] = Field(
        default="krum",
        description="Algorithmic shield applied to reject or quarantine the malicious vector",
    )


class AttackInjectionResponse(BaseModel):
    """Empirical mitigation results following adversarial injection."""

    model_config = ConfigDict(extra="ignore")

    attack_id: str = Field(..., description="Unique tracking identifier for the attack event")
    attack_type: str = Field(..., description="Injected attack category")
    status: str = Field(..., description="Mitigation status: intercepted, quarantined, or mitigated")
    defense_activated: str = Field(..., description="Name of the active defense mechanism deployed")
    adversary_quarantined: str | None = Field(default=None, description="Malicious node isolated from consensus")
    euclidean_distance: float = Field(default=0.0, description="Calculated L2 distance from consensus mean")
    distance_threshold: float = Field(default=0.0, description="Statistical quarantine dispersion cutoff")
    packets_blocked: int = Field(default=0, description="Total malicious packets or structured transactions filtered")
    mitigation_latency_ms: float = Field(default=0.0, description="Mitigation and isolation processing time in ms")
    auc_protected: float = Field(..., description="Continuous live demo proxy metric of model resilience")
    auc_compromised_baseline: float = Field(..., description="Hypothetical baseline degradation without defenses")
    log_entry: str = Field(default="", description="Human-readable tamper-evident audit log entry")
