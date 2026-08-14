"""Pydantic schemas for simulation API endpoints."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any

from pydantic import BaseModel, Field

from app.domain.enums import PrivacyMechanism, SimulationStatus


class SimulationConfigRequest(BaseModel):
    """Request body for creating a new simulation."""

    num_rounds: int = Field(default=10, ge=1, le=100, description="Number of federated rounds")
    local_epochs: int = Field(default=3, ge=1, le=20, description="Local training epochs per round")
    learning_rate: float = Field(default=0.001, gt=0, le=1.0)
    batch_size: int = Field(default=64, ge=8, le=512)
    min_clients_per_round: int = Field(default=2, ge=1, le=3)

    # Failure simulation
    enable_latency_simulation: bool = False
    latency_min_ms: int = Field(default=50, ge=0, le=5000)
    latency_max_ms: int = Field(default=500, ge=0, le=5000)
    enable_dropout_simulation: bool = False
    dropout_probability: float = Field(default=0.2, ge=0, le=0.8)
    enable_reconnect_simulation: bool = True

    # Privacy
    privacy_mechanism: PrivacyMechanism = PrivacyMechanism.NONE
    dp_epsilon: float = Field(default=1.0, gt=0)
    dp_epsilon_limit: float = Field(
        default=8.0, gt=0, description="Strict cumulative privacy budget epsilon limit"
    )
    dp_delta: float = Field(default=1e-5, gt=0)
    dp_max_grad_norm: float = Field(default=1.0, gt=0)
    dp_mode: str = Field(
        default="post_hoc",
        description="DP implementation mode: post_hoc (clip+noise after training) or opacus (per-sample gradient privacy)",
    )

    # Data volume & benchmark dataset selection
    dataset: str = Field(
        default="synthetic",
        description="Dataset source: synthetic, paysim, ieee_cis, elliptic, creditcard",
    )
    bank_a_transactions: int = Field(default=50000, ge=1000, le=200000)
    bank_b_transactions: int = Field(default=30000, ge=1000, le=200000)
    bank_c_transactions: int = Field(default=20000, ge=1000, le=200000)

    # Aggregation strategy
    aggregation_method: str = Field(
        default="fed_avg_weighted",
        description=(
            "Aggregation algorithm: fed_avg_weighted, fed_avg, krum, coordinate_wise_median, "
            "trimmed_mean (Yin et al. 2018), bulyan (El Mhamdi et al. 2018)"
        ),
    )

    # FL engine selection
    fl_engine_type: str = Field(
        default="custom",
        description="FL engine: custom (built-in simulator) or flower (Flower framework via Ray simulation)",
    )

    # Adversarial / poisoning simulation
    enable_poisoning_simulation: bool = False
    poisoning_bank_id: str = Field(default="bank_c", description="Bank to act as malicious client")
    poisoning_scale: float = Field(
        default=5.0, ge=1.0, le=20.0, description="Poisoning noise magnitude"
    )
    byzantine_defense: str = Field(
        default="none",
        description="Byzantine defense strategy: none, krum, coordinate_wise_median",
    )

    # Advanced Federated Optimization
    fedprox_mu: float = Field(
        default=0.0, ge=0.0, le=10.0, description="FedProx proximal coefficient"
    )
    moon_mu: float = Field(
        default=0.0, ge=0.0, le=10.0, description="MOON model-contrastive loss coefficient"
    )
    moon_temperature: float = Field(
        default=0.5, gt=0.0, le=2.0, description="MOON contrastive loss temperature"
    )
    fedopt_server_lr: float = Field(
        default=0.01, gt=0.0, le=1.0, description="FedOpt server learning rate"
    )
    fedopt_beta1: float = Field(
        default=0.9, ge=0.0, le=1.0, description="FedOpt beta1 momentum parameter"
    )
    fedopt_beta2: float = Field(
        default=0.999, ge=0.0, le=1.0, description="FedOpt beta2 momentum parameter"
    )
    fedopt_tau: float = Field(
        default=1e-3, gt=0.0, description="FedOpt server optimizer epsilon equivalent"
    )
    # Bias mitigation & fairness compliance (EU AI Act)
    enable_bias_mitigation: bool = False
    fairness_lambda: float = Field(
        default=0.5, ge=0.0, le=2.0, description="Weight on covariance constraint for debiasing"
    )
    hardware_isolation_mode: str = Field(
        default="none",
        description="Hardware isolation / Cryptographic mode: none, tee, fhe",
    )
    enable_streaming_gnn: bool = Field(
        default=False,
        description="Enable real-time streaming GNN (Graph Attention Network) online training",
    )
    enable_web3_settlement: bool = Field(
        default=False,
        description="Enable Web3 & CBDC Smart Contract automated incentive settlement",
    )
    settlement_currency: str = Field(
        default="wCBDC",
        description="Settlement currency: wCBDC, USDC, or e-TRY",
    )
    smart_contract_address: str = Field(
        default="0x71C7656EC7ab88b098defB751B7401B5f6d8976F",
        description="Deployed Consortium Settlement Smart Contract Address",
    )
    # Active Defense & Adversarial Training (FGSM/PGD)
    enable_adversarial_training: bool = Field(
        default=False,
        description="Enable active defense adversarial training against evasion attacks",
    )
    adversarial_attack_type: str = Field(
        default="fgsm",
        description="Adversarial attack algorithm for training: fgsm, pgd",
    )
    adversarial_epsilon: float = Field(
        default=0.05, ge=0.0, le=0.3, description="Adversarial perturbation magnitude (L_inf ball)"
    )
    adversarial_alpha: float = Field(
        default=0.01, ge=0.001, le=0.1, description="PGD step size for multi-step attack"
    )
    adversarial_steps: int = Field(default=5, ge=1, le=20, description="PGD iteration steps")
    adversarial_loss_weight: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Weight on clean loss vs adversarial loss"
    )


class SimulationSummaryResponse(BaseModel):
    """Abbreviated simulation info for list views."""

    id: str
    status: SimulationStatus
    current_round: int
    total_rounds: int
    progress_pct: float
    created_at: datetime
    completed_at: datetime | None = None
    duration_seconds: float | None = None


class SimulationDetailResponse(BaseModel):
    """Full simulation details including per-bank metrics."""

    id: str
    status: SimulationStatus
    config: SimulationConfigRequest
    current_round: int
    total_rounds: int
    progress_pct: float
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    error_message: str | None = None
    banks: list[BankResponse] = []
    rounds: list[RoundResponse] = []

    # Hardware/Cryptographic Isolation telemetry
    tee_mrenclave: str | None = None
    tee_mrsigner: str | None = None
    tee_attestation_signature: str | None = None
    fhe_poly_degree: int | None = None
    fhe_noise_bound: float | None = None
    fhe_key_id: str | None = None

    # Streaming GNN Telemetry
    streaming_gnn_node_count: int = 0
    streaming_gnn_edge_count: int = 0
    streaming_gnn_loss_history: list[float] = []

    # Web3 & CBDC Settlement Telemetry
    settlement_tx_hash: str | None = None
    settlement_block_number: int | None = None
    settlement_status: str | None = None
    on_chain_payouts: list[dict[str, Any]] = []


class SimulationCreateResponse(BaseModel):
    """Response after initiating a simulation."""

    id: str
    status: SimulationStatus
    message: str


# Forward reference resolution
class BankResponse(BaseModel):
    """Bank details within a simulation response."""

    id: str
    name: str
    tier: str
    fraud_ratio: float
    num_transactions: int
    status: str
    local_metrics: MetricsResponse | None = None
    federated_metrics: MetricsResponse | None = None
    improvement: dict[str, float] | None = None
    data_profile: DataProfileResponse | None = None
    contribution_score: float = 0.0
    quarantined: bool = False


class MetricsResponse(BaseModel):
    """Evaluation metrics for a model."""

    accuracy: float
    precision: float
    recall: float
    f1_score: float
    auc_roc: float
    loss: float
    confusion_matrix: list[list[int]]
    roc_fpr: list[float]
    roc_tpr: list[float]
    roc_thresholds: list[float]
    feature_importance: dict[str, float] = {}

    # Fairness metrics
    disparate_impact: float = 1.0
    equal_opportunity_diff: float = 0.0
    protected_selection_rate: float = 1.0
    reference_selection_rate: float = 1.0

    # Active Defense & Adversarial metrics
    adversarial_robustness_score: float = 1.0
    clean_accuracy: float = 0.0
    robust_accuracy: float = 0.0
    fgsm_evasion_rate: float = 0.0
    pgd_evasion_rate: float = 0.0


class DataProfileResponse(BaseModel):
    """Statistical profile of a bank's dataset."""

    bank_name: str
    num_transactions: int
    fraud_ratio: float
    mean_transaction_amount: float
    std_transaction_amount: float
    top_merchant_categories: list[str]
    top_countries: list[str]
    mean_account_age_days: float
    mean_velocity: float


class RoundResponse(BaseModel):
    """Details of a single federated training round."""

    round_number: int
    global_loss: float
    participating_banks: list[str]
    dropped_banks: list[str]
    per_bank_loss: dict[str, float]
    per_bank_samples: dict[str, int]
    aggregation_time_ms: float
    round_duration_ms: float
    privacy_budget_spent: float = 0.0


class ComparisonResponse(BaseModel):
    """Side-by-side local vs federated comparison across all banks."""

    simulation_id: str
    banks: list[BankComparisonResponse]
    aggregate_improvement: dict[str, float]


class BankComparisonResponse(BaseModel):
    """Per-bank local vs federated comparison."""

    bank_id: str
    bank_name: str
    local_metrics: MetricsResponse
    federated_metrics: MetricsResponse
    improvement: dict[str, float]


# Rebuild models to resolve forward references
SimulationDetailResponse.model_rebuild()
