"""Pydantic v2 schemas for Privacy Defense, Byzantine Aggregation, and Attack Auditing APIs."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# ── Privacy Attack Audits ──────────────────────────────────────────────


class MIAAuditRequest(BaseModel):
    """Payload to evaluate Membership Inference Attack (MIA) vulnerability via loss distribution gaps."""

    model_config = ConfigDict(extra="forbid")

    train_losses: list[float] = Field(default_factory=list, description="Loss values on training set members")
    test_losses: list[float] = Field(default_factory=list, description="Loss values on non-member test set")


class MIAAuditResponse(BaseModel):
    """Evaluation receipt for Membership Inference Attack audit."""

    model_config = ConfigDict(extra="ignore")

    membership_leakage_asr: float
    risk_tier: str
    num_train_samples_audited: int | None = None
    num_test_samples_audited: int | None = None
    message: str | None = None


class ModelInversionAuditRequest(BaseModel):
    """Payload to evaluate feature reconstruction risk via gradient norm variance."""

    model_config = ConfigDict(extra="forbid")

    gradient_norms: list[float] = Field(
        default_factory=list, description="Per-parameter gradient L2 norms from a training round"
    )


class ModelInversionAuditResponse(BaseModel):
    """Evaluation receipt for Model Inversion Attack audit."""

    model_config = ConfigDict(extra="ignore")

    reconstruction_risk_score: float
    risk_tier: str
    mean_gradient_norm: float | None = None
    std_gradient_norm: float | None = None
    num_gradients_audited: int | None = None
    message: str | None = None


class DLGAuditRequest(BaseModel):
    """Payload to evaluate Deep Leakage from Gradients (DLG) Pearson reconstruction risk."""

    model_config = ConfigDict(extra="forbid")

    original_gradients: list[float] = Field(
        default_factory=list, description="Original gradients before secure aggregation"
    )
    received_gradients: list[float] = Field(
        default_factory=list, description="Gradients received after aggregation (potential reconstruction vector)"
    )


class DLGAuditResponse(BaseModel):
    """Evaluation receipt for Deep Leakage from Gradients audit."""

    model_config = ConfigDict(extra="ignore")

    dlg_leakage_score: float
    risk_tier: str
    pearson_correlation: float | None = None
    params_audited: int | None = None
    num_parameters_audited: int | None = None
    message: str | None = None


# ── Noise Calibration & Differential Privacy Composition ───────────────


class CalibrateNoiseRequest(BaseModel):
    """Payload to calibrate Gaussian mechanism noise scale sigma."""

    model_config = ConfigDict(extra="forbid")

    target_epsilon: float = Field(..., gt=0.0, description="Target DP epsilon")
    target_delta: float = Field(default=1e-5, gt=0.0, lt=1.0, description="Target DP delta")
    sensitivity: float = Field(default=1.0, gt=0.0, description="L2 sensitivity (clipping bound C)")
    mechanism: str = Field(default="gaussian", description="Mechanism type ('gaussian')")


class CalibrateNoiseResponse(BaseModel):
    """Calibrated Gaussian noise scale sigma receipt."""

    model_config = ConfigDict(extra="forbid")

    mechanism: str
    target_epsilon: float
    target_delta: float
    sensitivity: float
    calibrated_sigma: float
    formula: str


class RDPCompositionRequest(BaseModel):
    """Payload to compute exact Rényi Differential Privacy (RDP) composition."""

    model_config = ConfigDict(extra="forbid")

    sigmas: list[float] = Field(
        ..., min_length=1, description="Noise multipliers across training rounds"
    )
    target_delta: float = Field(default=1e-5, gt=0.0, lt=1.0, description="Target delta for (eps, delta)-DP")
    sample_ratio_q: float = Field(default=1.0, gt=0.0, le=1.0, description="Batch sampling ratio q")
    orders: list[float] | None = Field(default=None, description="Optional list of Rényi orders to evaluate")


class RDPCompositionResponse(BaseModel):
    """Rényi DP composition result and optimal (eps, delta)-DP bound."""

    model_config = ConfigDict(extra="forbid")

    total_rounds: int
    cumulative_epsilon: float
    optimal_order_alpha: float
    naive_sum_epsilon: float
    privacy_saving_pct: float
    target_delta: float
    rdp_map: dict[str, float]


# ── Aggregation Catalogue & Budget Telemetry ───────────────────────────


class AggregationMethodItem(BaseModel):
    """Single aggregation method specification in the platform catalogue."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    description: str
    byzantine_robust: bool
    colluding_defense: bool
    paper: str


class BudgetLogEntryResponse(BaseModel):
    """Multi-simulation privacy budget consumption log entry."""

    model_config = ConfigDict(extra="forbid")

    simulation_id: str
    total_epsilon: float
    rdp_total_epsilon: float | None = None
    delta: float
    rounds_spent: int
    epsilon_per_round: float
    epsilon_history: list[float]
    sigma_history: list[float]
    budget_exhausted: bool
    epsilon_limit: float


# ── Rényi DP Consortium & Circuit Breaker Schemas ───────────────────────

class NodeRDPBudgetStatus(BaseModel):
    """Rényi DP accountant budget status for a participating consortium bank node."""

    model_config = ConfigDict(extra="ignore")

    node_id: str
    bank_name: str
    tier: str
    rounds_completed: int
    cumulative_epsilon: float
    target_epsilon: float
    target_delta: float
    budget_exhaustion_pct: float
    is_budget_exceeded: bool
    optimal_alpha_order: float
    calibrated_sigma: float
    risk_tier: str


class BankBudgetsResponse(BaseModel):
    """Consortium-wide Rényi Differential Privacy distribution and safety freeze status."""

    model_config = ConfigDict(extra="ignore")

    consortium_target_epsilon: float
    consortium_target_delta: float
    total_nodes_active: int
    any_budget_exceeded: bool
    training_circuit_breaker_active: bool
    frozen_by_node: str | None = None
    frozen_at: str | None = None
    freeze_reason: str | None = None
    node_budgets: list[NodeRDPBudgetStatus]
    global_cumulative_rdp: dict[str, float]
    updated_at: str


class CircuitBreakerActionRequest(BaseModel):
    """Payload to trigger, disengage, or reset the consortium emergency training freeze."""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(..., description="'freeze' | 'unfreeze' | 'reset_budget'")
    reason: str = Field(default="Manual operator intervention", description="Audit justification")
    actor: str = Field(default="secops_admin", description="Operator identity or service role")
    node_id: str | None = Field(default=None, description="Optional target node for node-specific reset")


class CircuitBreakerActionResponse(BaseModel):
    """Receipt for circuit breaker state modification."""

    model_config = ConfigDict(extra="ignore")

    success: bool
    action: str
    training_circuit_breaker_active: bool
    frozen_by_node: str | None = None
    frozen_at: str | None = None
    message: str
    timestamp: str


class MIASimulationRequest(BaseModel):
    """Payload to run Membership Inference Attack simulation at target DP epsilon."""

    model_config = ConfigDict(extra="forbid")

    test_epsilon: float = Field(default=1.0, ge=0.0, le=100.0, description="DP epsilon level to evaluate")
    num_samples: int = Field(default=100, ge=20, le=1000, description="Shadow transaction sample count")


class MIASimulationResponse(BaseModel):
    """Comprehensive MIA simulation receipt with shadow loss disparities and ROC-AUC."""

    model_config = ConfigDict(extra="ignore")

    test_epsilon: float
    is_dp_enabled: bool
    membership_leakage_asr: float
    mia_roc_auc: float
    risk_tier: str
    mean_train_loss: float
    mean_test_loss: float
    loss_gap: float
    train_loss_distribution: list[float]
    test_loss_distribution: list[float]
    confidence_distribution: list[float]
    attack_summary: str
    evaluated_at: str

