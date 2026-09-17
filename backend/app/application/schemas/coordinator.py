"""Pydantic v2 schemas for Federated Learning Coordinator & Consortium Governance API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HandshakeRequest(BaseModel):
    """Client hardware and runtime environment registration payload."""

    model_config = ConfigDict(extra="forbid")

    bank_id: str = Field(
        ...,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Unique bank tenant ID",
    )
    pytorch_version: str = Field(
        ...,
        max_length=32,
        pattern=r"^[0-9]+\.[0-9]+.*$",
        description="PyTorch installation version",
    )
    python_version: str = Field(
        ...,
        max_length=32,
        pattern=r"^[0-9]+\.[0-9]+.*$",
        description="Python execution runtime version",
    )
    hardware_type: str = Field(
        ...,
        max_length=32,
        pattern=r"^[a-zA-Z0-9_]+$",
        description="Available accelerator e.g. cuda or cpu",
    )
    ram_gb: float = Field(
        ...,
        ge=0.5,
        le=8192.0,
        description="Installed RAM in gigabytes [0.5, 8192]",
    )
    device_count: int = Field(
        default=1,
        ge=0,
        le=64,
        description="Number of available GPUs [0, 64]",
    )


class HandshakeResponse(BaseModel):
    """Response returned upon dynamic capability registration."""

    model_config = ConfigDict(extra="forbid")

    registered: bool = Field(..., description="Whether client handshake succeeded")
    status: str = Field(..., description="Registration status code")
    reason: str | None = Field(default=None, description="Detailed failure or warning message")
    client_profile: dict[str, Any] | None = Field(
        default=None, description="Negotiated profile and capabilities"
    )
    registered_at: float = Field(..., description="Epoch registration timestamp")


class HeartbeatRequest(BaseModel):
    """Periodic client heartbeat keep-alive probe."""

    model_config = ConfigDict(extra="forbid")

    bank_id: str = Field(
        ...,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Registered bank node identifier",
    )


class HeartbeatResponse(BaseModel):
    """Response returned upon successful heartbeat."""

    model_config = ConfigDict(extra="forbid")

    success: bool = Field(..., description="Liveness confirmation")
    status: str = Field(default="ONLINE", description="Client status")
    timestamp: float = Field(..., description="Timestamp of heartbeat reception")


class NegotiateRequest(BaseModel):
    """Request hyper-parameters customized for a bank client's runtime capability."""

    model_config = ConfigDict(extra="forbid")

    bank_id: str = Field(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    base_batch_size: int = Field(default=32, ge=1, le=4096)
    base_epochs: int = Field(default=5, ge=1, le=100)
    hardware_type: str | None = Field(default=None, max_length=32)
    available_vram_gb: float | None = Field(default=None, ge=0.0, le=512.0)
    bandwidth_mbps: float | None = Field(default=None, ge=0.0, le=100_000.0)
    local_sample_count: int | None = Field(default=None, ge=1)


class NegotiatedResponse(BaseModel):
    """Negotiated training hyper-parameters customized for bank client runtime."""

    model_config = ConfigDict(extra="forbid")

    bank_id: str = Field(..., description="Bank identifier")
    batch_size: int = Field(..., description="Negotiated local batch size")
    local_epochs: int = Field(..., description="Negotiated local training epochs")
    gradient_accumulation_steps: int = Field(..., description="Gradient accumulation steps")
    use_cuda: bool = Field(..., description="Whether CUDA execution was granted")
    status: str = Field(..., description="Negotiation status")


class ClientCapabilityResponse(BaseModel):
    """Runtime capability profile of a registered bank client."""

    model_config = ConfigDict(extra="forbid")

    bank_id: str = Field(..., description="Bank identifier")
    pytorch_version: str = Field(..., description="PyTorch version")
    python_version: str = Field(..., description="Python runtime version")
    hardware_type: str = Field(..., description="Hardware accelerator type")
    ram_gb: float = Field(..., description="RAM allocation in GB")
    device_count: int = Field(..., description="Number of accelerator devices")
    status: str = Field(..., description="Participant node status")
    last_heartbeat_ago_seconds: float = Field(..., description="Elapsed seconds since last heartbeat")


class AsyncUpdateRequest(BaseModel):
    """Asynchronous parameter update submission."""

    model_config = ConfigDict(extra="forbid")

    bank_id: str = Field(
        ...,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Unique bank tenant ID",
    )
    submitted_round: int = Field(
        ..., ge=1, description="Round number client base model was trained on"
    )
    client_weights: dict[str, list[float]] = Field(
        ..., description="Flattened or 1D list of parameter weights per layer"
    )
    layer_shapes: dict[str, list[int]] | None = Field(
        default=None, description="Optional tensor dimensions to reconstruct multi-dimensional weights"
    )
    sample_count: int = Field(
        default=100, ge=1, description="Number of local training samples"
    )


class AsyncUpdateResponse(BaseModel):
    """Response returned upon receiving asynchronous update."""

    model_config = ConfigDict(extra="forbid")

    success: bool = Field(..., description="Whether update was applied or accepted")
    bank_id: str = Field(..., description="Submitting bank identifier")
    submitted_round: int = Field(..., description="Base round of submission")
    current_round: int = Field(..., description="Current coordinator round")
    staleness_tau: int = Field(..., description="Observed staleness in rounds")
    staleness_attenuation: float = Field(..., description="Calculated attenuation factor")
    effective_alpha: float = Field(..., description="Effective learning rate multiplier")
    layer_keys: list[str] = Field(..., description="Keys of updated model parameter layers")


class QuorumStatusResponse(BaseModel):
    """Progress and countdown metrics for active training round quorum."""

    model_config = ConfigDict(extra="forbid")

    round_number: int = Field(..., description="Round number")
    registered_nodes_count: int = Field(..., description="Total active registered banks")
    submitted_nodes_count: int = Field(..., description="Nodes that submitted gradient updates")
    quorum_threshold_pct: float = Field(..., description="Required participant threshold percentage")
    current_quorum_pct: float = Field(..., description="Current achieved percentage")
    state: str = Field(..., description="Current quorum state")
    start_time: str = Field(..., description="ISO 8601 start timestamp")
    target_window_seconds: int = Field(..., description="Target round window duration in seconds")
    time_remaining_seconds: float = Field(..., description="Remaining window countdown")


class AsyncFLEngineStatusResponse(BaseModel):
    """Runtime staleness damping metrics and hyperparameters of the FedAsync engine."""

    model_config = ConfigDict(extra="forbid")

    current_round: int = Field(..., description="Current global round")
    alpha_staleness: float = Field(..., description="Staleness damping baseline alpha")
    learning_rate: float = Field(..., description="Global coordinator learning rate")
    max_staleness: int = Field(..., description="Maximum allowed staleness tau")
    staleness_function: str = Field(..., description="Damping function type")
    total_updates: int = Field(..., description="Total received updates")
    dropped_updates: int = Field(..., description="Updates dropped due to excessive staleness")
    applied_updates: int = Field(..., description="Successfully aggregated updates")
    average_staleness: float = Field(..., description="Mean observed staleness")
    max_observed_staleness: int = Field(..., description="Peak observed staleness")


class PruneRoundsResponse(BaseModel):
    """Result of pruning historical in-memory round states."""

    model_config = ConfigDict(extra="forbid")

    pruned_rounds_count: int = Field(..., description="Number of pruned round objects")
    keep_last: int = Field(..., description="Number of historical rounds retained")


# ── Consortium Governance Models ──────────────────────────────


class ConsortiumMemberResponse(BaseModel):
    """Participant member profile in a consortium alliance."""

    model_config = ConfigDict(extra="forbid")

    bank_id: str = Field(..., description="Participant bank ID")
    role: str = Field(..., description="Member role: FOUNDER, FULL_MEMBER, OBSERVER")
    voting_power: float = Field(..., description="Assigned voting power weight")
    joined_at: str = Field(..., description="ISO 8601 join timestamp")
    can_vote: bool = Field(..., description="Whether institution can cast governance votes")


class ConsortiumResponse(BaseModel):
    """Consortium governance alliance entity."""

    model_config = ConfigDict(extra="forbid")

    consortium_id: str = Field(..., description="Consortium identifier")
    name: str = Field(..., description="Human-readable consortium name")
    quorum_ratio: float = Field(..., description="Required approval quorum ratio (e.g. 0.51)")
    min_members_n: int = Field(..., description="Minimum member threshold")
    max_epsilon: float = Field(..., description="Maximum allowable privacy loss epsilon")
    status: str = Field(..., description="Consortium status: ACTIVE, DRAFT, SUSPENDED")
    members: list[ConsortiumMemberResponse] = Field(..., description="Current member roster")
    created_at: str = Field(..., description="ISO 8601 creation timestamp")


class CreateConsortiumRequest(BaseModel):
    """Request payload to establish a new multi-bank federated consortium."""

    model_config = ConfigDict(extra="forbid")

    consortium_id: str = Field(
        ...,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Unique consortium slug identifier",
    )
    name: str = Field(..., min_length=3, max_length=128, description="Consortium title")
    founder_bank_id: str = Field(
        ...,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Sponsoring founder bank ID",
    )
    quorum_ratio: float = Field(
        default=0.51,
        gt=0.0,
        le=1.0,
        description="Required vote approval fraction (0.0, 1.0]",
    )
    max_epsilon: float = Field(
        default=5.0,
        gt=0.0,
        le=100.0,
        description="Maximum consortium-wide DP privacy budget",
    )
    min_members_n: int = Field(
        default=2,
        ge=1,
        le=100,
        description="Minimum quorum member count",
    )


class ProposalResponse(BaseModel):
    """Consortium governance proposal details and voting status."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(..., description="Proposal unique ID")
    consortium_id: str = Field(..., description="Target consortium identifier")
    creator_bank_id: str = Field(..., description="Sponsoring bank ID")
    target_bank_id: str = Field(..., description="Target bank or CONSORTIUM_POLICY")
    action: str = Field(..., description="Action: ADD_MEMBER, REMOVE_MEMBER, UPDATE_POLICY")
    required_quorum_ratio: float = Field(..., description="Required threshold ratio")
    votes_for: list[str] = Field(..., description="Bank IDs that voted in favor")
    votes_against: list[str] = Field(..., description="Bank IDs that voted against")
    status: str = Field(..., description="Proposal status: PENDING, APPROVED, REJECTED, EXPIRED, CANCELLED")
    created_at: str = Field(..., description="Creation ISO 8601 timestamp")
    expires_at: str | None = Field(default=None, description="Expiration ISO 8601 timestamp")
    rejection_reason: str | None = Field(default=None, description="Resolution rationale or rejection notice")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Action-specific parameters")


class CreateMembershipProposalRequest(BaseModel):
    """Payload to propose adding or removing a member from the consortium."""

    model_config = ConfigDict(extra="forbid")

    consortium_id: str = Field(
        ..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"
    )
    creator_bank_id: str = Field(
        ..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"
    )
    target_bank_id: str = Field(
        ..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"
    )
    action: str = Field(
        default="ADD_MEMBER",
        pattern=r"^(ADD_MEMBER|REMOVE_MEMBER)$",
        description="Membership action",
    )
    ttl_seconds: int = Field(default=86400, ge=60, le=2_592_000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreatePolicyProposalRequest(BaseModel):
    """Payload to propose modifying consortium-wide parameters."""

    model_config = ConfigDict(extra="forbid")

    consortium_id: str = Field(
        ..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"
    )
    creator_bank_id: str = Field(
        ..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"
    )
    policy_updates: dict[str, Any] = Field(
        ..., min_length=1, description="Policy key-value modifications"
    )
    ttl_seconds: int = Field(default=86400, ge=60, le=2_592_000)


class VoteProposalRequest(BaseModel):
    """Cryptographic bank vote submission for an active proposal."""

    model_config = ConfigDict(extra="forbid")

    bank_id: str = Field(
        ..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"
    )
    approve: bool = Field(..., description="True to vote FOR, False to vote AGAINST")


class VoteProposalResponse(BaseModel):
    """Receipt returned upon successfully recording a governance vote."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(..., description="Voted proposal ID")
    status: str = Field(..., description="Updated proposal status")
    votes_for_count: int = Field(..., description="Total positive votes")
    votes_against_count: int = Field(..., description="Total negative votes")
    votes_for: list[str] = Field(..., description="Institutions voting in favor")
    votes_against: list[str] = Field(..., description="Institutions voting against")
    resolved: bool = Field(..., description="Whether proposal reached resolution threshold")
