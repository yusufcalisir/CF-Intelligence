"""Pydantic v2 schemas for Consortium Bank Node Directory & Edge Daemon Client API."""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ── Bank Reference & Node Directory Schemas ──────────────────────────────────


class BankConfigItem(BaseModel):
    """Consortium participating bank node profile."""

    model_config = ConfigDict(extra="ignore", from_attributes=True)

    id: str = Field(..., description="Unique bank node identifier")
    name: str = Field(..., description="Legal entity bank name")
    tier: str = Field(..., description="Bank tier designation: large, medium, small, regional, global")
    description: str = Field(..., description="Institution profile description")
    default_fraud_ratio: float = Field(..., ge=0.0, le=1.0, description="Baseline empirical fraud ratio")
    default_transactions: int = Field(..., ge=1, description="Default transaction partition volume")
    fraud_pattern: str = Field(..., description="Primary fraud topology or modus operandi")
    characteristics: list[str] = Field(default_factory=list, description="Operational characteristics")
    hardware_enclave: str = Field("Intel SGX-v2", description="Hardware enclave isolation platform")
    mtls_status: str = Field("ACTIVE", description="Mutual TLS certificate enrollment status")
    status: str = Field("ONLINE", description="Node runtime status: ONLINE, OFFLINE, DEGRADED")


class BankDetailResponse(BankConfigItem):
    """Detailed bank node configuration response."""

    pass


class ConsortiumNodeSummary(BaseModel):
    """Consortium bank node status summary."""

    model_config = ConfigDict(extra="ignore")

    bank_id: str
    name: str
    tier: str
    status: str
    hardware_acceleration: str = "cuda"
    last_heartbeat_timestamp: float | None = None


class ConsortiumStatusResponse(BaseModel):
    """High-level consortium network topology & node registry status."""

    model_config = ConfigDict(extra="ignore")

    consortium_name: str = "Cross-Bank Federated Intelligence Consortium"
    total_registered_nodes: int
    active_nodes_count: int
    hardware_enclave_enabled: bool = True
    mtls_status: str = "ACTIVE"
    nodes: list[ConsortiumNodeSummary]


# ── Non-IID Data Drift & Distributions Schemas ───────────────────────────────


class AmountHistogram(BaseModel):
    """Log-scale transaction amount histogram."""

    model_config = ConfigDict(extra="ignore")

    bins: list[float]
    counts: list[int]
    fraud_counts: list[int]


class HourlyFraudRate(BaseModel):
    """24-hour hourly fraud occurrence and transaction volume."""

    model_config = ConfigDict(extra="ignore")

    hours: list[int]
    total: list[int]
    fraud: list[int]


class MerchantRisk(BaseModel):
    """Merchant category risk and fraud rates."""

    model_config = ConfigDict(extra="ignore")

    categories: list[str]
    fraud_rates: list[float]
    counts: list[int]


class BankDistributionData(BaseModel):
    """Individual bank Non-IID distribution breakdown."""

    model_config = ConfigDict(extra="ignore")

    amount_histogram: AmountHistogram
    hourly_fraud_rate: HourlyFraudRate
    merchant_risk: MerchantRisk


class FeatureDriftDetail(BaseModel):
    """Per-feature drift statistics between bank pairs."""

    model_config = ConfigDict(extra="ignore")

    psi: float
    js_divergence: float
    ks: float | None = None
    status: str


class FeatureDriftSummary(BaseModel):
    """Aggregated feature drift metrics for a bank pair."""

    model_config = ConfigDict(extra="ignore")

    overall_psi: float
    overall_js: float
    features: dict[str, FeatureDriftDetail]


class ModelPredictionDrift(BaseModel):
    """Logistic prediction probability shift metrics."""

    model_config = ConfigDict(extra="ignore")

    psi: float
    js_divergence: float
    status: str


class ConceptDriftSummary(BaseModel):
    """Concept drift assessment for conditional distributions."""

    model_config = ConfigDict(extra="ignore")

    overall_psi: float
    overall_js: float
    model_prediction_drift: ModelPredictionDrift
    conditional_drifts: dict[str, float]


class DivergenceSummary(BaseModel):
    """Consortium-wide Non-IID pairwise statistical divergence summary."""

    model_config = ConfigDict(extra="ignore")

    amount_ks_statistic: dict[str, float]
    overall_non_iid_score: float
    feature_drift: dict[str, FeatureDriftSummary]
    concept_drift: dict[str, ConceptDriftSummary]


class BankDistributionsResponse(BaseModel):
    """Response schema for GET /api/v1/banks/distributions."""

    model_config = ConfigDict(extra="ignore")

    banks: dict[str, BankDistributionData]
    divergence_summary: DivergenceSummary


class ScoringVolumePointResponse(BaseModel):
    """Empirical hourly transaction scoring volume data point."""

    model_config = ConfigDict(extra="ignore")

    time: str = Field(..., description="Timestamp in HH:00 format")
    volume: int = Field(..., ge=0, description="Aggregated transaction scoring volume")


# ── Edge Daemon Client Schemas ───────────────────────────────────────────────


class ModelWeightsSchema(BaseModel):
    """Serialized neural network weights schema."""

    model_config = ConfigDict(extra="ignore")

    layer_shapes: list[list[int]]
    flat_weights: list[float]


class BankInitializeRequest(BaseModel):
    """Request payload to initialize the bank's local data partition."""

    model_config = ConfigDict(extra="ignore")

    bank_id: str = Field(
        ...,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Unique bank node identifier",
    )
    num_transactions: int = Field(
        ...,
        ge=10,
        le=1_000_000,
        description="Number of synthetic transactions to generate [10, 1M]",
    )
    seed: int | None = Field(
        42,
        ge=0,
        le=2**32 - 1,
        description="RNG seed for reproducible data generation",
    )


class BankInitializeResponse(BaseModel):
    """Response returned upon successful dataset initialization."""

    model_config = ConfigDict(extra="ignore")

    status: str = "initialized"
    bank_id: str
    train_samples: int
    test_samples: int


class BankTrainRequest(BaseModel):
    """Request payload to trigger local model training round."""

    model_config = ConfigDict(extra="ignore")

    weights: ModelWeightsSchema
    learning_rate: float = Field(..., gt=0.0, le=1.0)
    batch_size: int = Field(..., ge=1, le=8192)
    epochs: int = Field(..., ge=1, le=200)
    enable_dp: bool
    dp_epsilon: float = Field(..., gt=0.0, le=100.0)
    dp_delta: float = Field(..., gt=0.0, le=1.0)
    dp_max_grad_norm: float = Field(..., gt=0.0, le=100.0)
    dp_mode: str = Field(
        "opacus",
        max_length=32,
        pattern=r"^[a-zA-Z_]+$",
    )
    fedprox_mu: float = Field(0.0, ge=0.0, le=10.0)
    moon_mu: float = Field(0.0, ge=0.0, le=10.0)
    moon_temperature: float = Field(0.5, gt=0.0, le=10.0)
    prev_local_weights: ModelWeightsSchema | None = None


class BankTrainResponse(BaseModel):
    """Response payload returned from local training execution."""

    model_config = ConfigDict(extra="ignore")

    weights: ModelWeightsSchema
    num_samples: int
    loss: float
    actual_epsilon: float | None = None


class BankEvaluateRequest(BaseModel):
    """Request payload to evaluate global model weights locally."""

    model_config = ConfigDict(extra="ignore")

    weights: ModelWeightsSchema


class BankEvaluateResponse(BaseModel):
    """Response payload returned from local model evaluation."""

    model_config = ConfigDict(extra="ignore")

    loss: float
    num_samples: int
    accuracy: float
    precision: float
    recall: float
    f1_score: float
    auc_roc: float
    confusion_matrix: list[list[int]]
    roc_fpr: list[float]
    roc_tpr: list[float]
    roc_thresholds: list[float]


class EdgeHeartbeatRequest(BaseModel):
    """Edge bank node heartbeat & hardware attestation liveness probe."""

    model_config = ConfigDict(extra="ignore")

    bank_id: str = Field(..., min_length=3, max_length=64, description="Bank node identifier")
    hardware_type: str = Field("cuda", description="Detected acceleration platform: cuda, mps, cpu")
    attestation_quote: str | None = Field(None, description="Hardware enclave remote attestation quote")
    metrics: dict[str, Any] | None = Field(None, description="Local resource utilization metrics")
    timestamp: float | None = Field(default_factory=time.time, description="Client unix timestamp")


class EdgeHeartbeatResponse(BaseModel):
    """Heartbeat acknowledgement payload."""

    model_config = ConfigDict(extra="ignore")

    status: str = "ACK"
    bank_id: str
    server_time: float = Field(default_factory=time.time)
    next_heartbeat_seconds: int = 15
    attestation_verified: bool = True


class GradientSubmissionRequest(BaseModel):
    """Encrypted gradient submission payload from edge bank daemon."""

    model_config = ConfigDict(extra="ignore")

    bank_id: str = Field(..., min_length=3, max_length=64, description="Submitting bank node ID")
    round_id: int = Field(..., ge=1, description="Training round sequence ID")
    num_samples: int = Field(..., ge=1, description="Sample count used for local batch")
    loss: float = Field(..., ge=0.0, description="Local empirical loss value")
    encrypted_gradients_b64: str | None = Field(None, description="CKKS homomorphically encrypted gradients")
    flat_gradients: list[float] | None = Field(None, description="Plaintext or DP-perturbed flat gradients")
    privacy_spent_epsilon: float | None = Field(None, ge=0.0, description="Differential privacy spent epsilon")
    zk_proof: str | None = Field(None, description="Zero-knowledge proof of honest execution")


class GradientSubmissionResponse(BaseModel):
    """Gradient submission acknowledgement and receipt hash."""

    model_config = ConfigDict(extra="ignore")

    status: str = "ACCEPTED"
    bank_id: str
    round_id: int
    gradient_hash: str
    timestamp: float = Field(default_factory=time.time)


class BankClientStatusResponse(BaseModel):
    """Current runtime state of the local bank client daemon."""

    model_config = ConfigDict(extra="ignore")

    bank_id: str | None = None
    is_initialized: bool
    train_samples: int
    test_samples: int
    hardware_profile: dict[str, Any]
    status: str = "ONLINE"
