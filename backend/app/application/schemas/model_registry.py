"""Pydantic v2 schemas for Model Registry, Champion-Challenger Rollout & SR 11-7 Governance."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SignOffRecord(BaseModel):
    """Cryptographic governance sign-off record on a registered model."""

    model_config = ConfigDict(extra="ignore")

    role: str = Field(..., description="Role of the signer ('compliance' or 'ml_engineer')")
    user: str = Field(..., description="Identifier of the signing officer")
    signature: str = Field(..., description="Cryptographic signature string")
    timestamp: str = Field(..., description="ISO 8601 sign-off timestamp")
    fairness_score: float = Field(1.0, ge=0.0, le=1.0, description="Evaluated disparate impact ratio")
    bias_metric: float = Field(0.0, ge=0.0, description="Evaluated demographic disparity metric")
    drift_divergence: float = Field(0.0, ge=0.0, description="Concept drift divergence index")


class ModelSignOffRequest(BaseModel):
    """Payload to record an officer sign-off on a model version."""

    model_config = ConfigDict(extra="ignore")

    role: str = Field(..., description="Signer role ('compliance' or 'ml_engineer')")
    user: str = Field(..., min_length=2, max_length=128, description="User identifier")
    signature: str = Field(..., min_length=8, description="Cryptographic signature or hash digest")
    fairness_score: float = Field(1.0, ge=0.0, le=1.0, description="Evaluated model fairness score")
    bias_metric: float = Field(0.0, ge=0.0, description="Evaluated model bias metric")
    drift_divergence: float = Field(0.0, ge=0.0, description="Evaluated dataset drift divergence")


class SR117ValidationResult(BaseModel):
    """Federal Reserve SR 11-7 Model Risk Management compliance assessment."""

    model_config = ConfigDict(extra="ignore")

    passed: bool = Field(..., description="Whether model satisfies all SR 11-7 quality gates")
    rule_name: str = Field("Federal Reserve SR 11-7 / OCC 2011-12", description="Governance standard")
    checks: dict[str, Any] = Field(default_factory=dict, description="Individual quality gate verification states")
    recommendations: list[str] = Field(default_factory=list, description="Remediation actions if gate failed")


class ModelPromoteRequest(BaseModel):
    """Payload requesting promotion of a model version to champion or challenger."""

    model_config = ConfigDict(extra="ignore")

    target_status: str = Field("champion", description="Target designation ('champion' or 'challenger')")
    enforce_sr11_7: bool = Field(True, description="Enforce Federal Reserve SR 11-7 quality gate before promotion")
    min_auc: float = Field(0.65, ge=0.5, le=1.0, description="Minimum validation AUC required for promotion")
    min_fairness_score: float = Field(0.80, ge=0.0, le=1.0, description="EEOC 80% four-fifths rule threshold")


class ModelPromoteResponse(BaseModel):
    """Result of model version promotion."""

    model_config = ConfigDict(extra="ignore")

    version: int
    target_status: str
    message: str
    is_active: bool
    status: str
    sr11_7_validation: SR117ValidationResult | None = None
    timestamp: str | None = None


class ModelVersionItem(BaseModel):
    """Metadata specification for a single versioned model in the registry."""

    model_config = ConfigDict(extra="ignore")

    version: int
    filename: str
    metrics: dict[str, Any]
    is_active: bool
    status: str = Field("inactive", description="'champion', 'challenger', or 'inactive'")
    git_commit_hash: str = "unknown"
    dataset_hash: str = "unknown"
    dp_noise_profile: dict[str, Any] = Field(
        default_factory=lambda: {"mechanism": "none", "epsilon": 0.0, "delta": 0.0}
    )
    sign_offs: list[SignOffRecord] = Field(default_factory=list)
    created_at: str


class ModelSummary(BaseModel):
    """Consolidated profile of a model or simulation tracked in the registry."""

    model_config = ConfigDict(extra="ignore")

    simulation_id: str
    active_version: int | None = None
    champion_status: str = "inactive"
    total_versions: int = 0
    latest_metrics: dict[str, Any] = Field(default_factory=dict)
    sr11_7_compliant: bool = True
    last_updated: str | None = None


class ModelInventoryResponse(BaseModel):
    """Response cataloging all models and versions across the consortium."""

    model_config = ConfigDict(extra="ignore")

    models: list[ModelSummary]
    total_models: int


class UpgradeInitiateRequest(BaseModel):
    """Payload to initiate a zero-downtime rolling upgrade session."""

    model_config = ConfigDict(extra="ignore")

    target_version: str = Field(..., min_length=2, max_length=32, description="Target version tag (e.g. 'v2.1.0')")
    compatibility_window_hours: int = Field(
        48, ge=1, le=168, description="Dual-version compatibility window in hours"
    )
    initial_connections: int = Field(100, ge=0, description="Initial active connections count")


class DrainConnectionsRequest(BaseModel):
    """Payload to drain active connections during a rolling update."""

    model_config = ConfigDict(extra="ignore")

    batch_size: int = Field(50, ge=1, le=1000, description="Number of connections to drain in this batch")


class DrainConnectionsResponse(BaseModel):
    """Response reflecting current connection draining stage."""

    model_config = ConfigDict(extra="ignore")

    session_id: str
    stage: str
    active_connections_count: int
    drained_connections_count: int


class RollingUpdateRequest(BaseModel):
    """Payload specifying cluster instances to roll update."""

    model_config = ConfigDict(extra="ignore")

    instance_ids: list[str] = Field(..., min_length=1, description="List of cluster instance IDs to update")


class UpgradeAbortRequest(BaseModel):
    """Payload to abort an active rolling deployment session."""

    model_config = ConfigDict(extra="ignore")

    reason: str = Field(
        "Upgrade aborted due to health check failure",
        min_length=3,
        max_length=256,
        description="Reason for aborting deployment session",
    )


class DeploymentSessionResponse(BaseModel):
    """Complete descriptor of a zero-downtime rolling upgrade session."""

    model_config = ConfigDict(extra="ignore")

    session_id: str
    target_version: str
    stage: str
    active_connections_count: int
    drained_connections_count: int
    updated_instances: list[str] = Field(default_factory=list)
    started_at: str
    abort_reason: str | None = None


class UpgradeWindowInfo(BaseModel):
    """Active dual-version upgrade compatibility window."""

    model_config = ConfigDict(extra="ignore")

    current_version: str
    target_version: str
    compatibility_window_hours: int


class DeploymentStatusResponse(BaseModel):
    """Platform deployment state, current active version, and session counts."""

    model_config = ConfigDict(extra="ignore")

    current_version: str
    upgrade_window: UpgradeWindowInfo | None = None
    total_sessions: int
    has_active_session: bool


class CanaryDecisionItem(BaseModel):
    """Canary champion-challenger traffic gating evaluation decision."""

    model_config = ConfigDict(extra="ignore")

    round: int | None = None
    version: int | None = None
    candidate_auc: float | None = None
    promoted_auc: float | None = None
    is_promoted: bool | None = None
    reason: str | None = None


class ShadowMetricsResponse(BaseModel):
    """Real-time champion-challenger shadow evaluation metrics."""

    model_config = ConfigDict(extra="ignore")

    champion_version: int | None = None
    champion_auc: float | None = None
    champion_pr_auc: float | None = None
    champion_fpr: float | None = None
    champion_latency_ms: float | None = None
    challenger_auc: float | None = None
    challenger_pr_auc: float | None = None
    challenger_fpr: float | None = None
    challenger_latency_ms: float | None = None
    traffic_share: float | None = None
    sample_count: int | None = None
    rollback_triggered: bool | None = None
    rollback_message: str | None = None
    promotion_triggered: bool | None = None
    promotion_message: str | None = None
