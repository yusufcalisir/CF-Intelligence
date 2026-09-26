"""Executive KPI Dashboard schemas and response models.

Provides strictly validated Pydantic v2 schemas for executive overview metrics,
risk scoring weights, and aggregated alert/entity distribution analytics.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DashboardStatsResponse(BaseModel):
    """Consolidated KPI card metrics for the Executive Dashboard."""

    model_config = ConfigDict(extra="ignore")

    total_alerts: int = Field(..., ge=0, description="Total ingested alerts count")
    critical_alerts: int = Field(..., ge=0, description="High and critical priority alerts requiring triage")
    open_cases: int = Field(..., ge=0, description="Currently unresolved investigation cases")
    total_entities: int = Field(..., ge=0, description="Unique pseudonymized entities resolved in graph")
    shared_intelligence_items: int = Field(..., ge=0, description="Total shared threat intelligence signals")
    cross_institution_matches: int = Field(..., ge=0, description="Entities or clusters spanning multiple institutions")
    active_scenarios: int = Field(..., ge=0, description="Simulations currently streaming events")
    graph_clusters: int = Field(..., ge=0, description="Detected syndicates or mule rings in knowledge graph")


class MerchantRiskItem(BaseModel):
    """Merchant entity risk profile aggregated from alerts."""

    model_config = ConfigDict(extra="ignore")

    merchant: str = Field(..., description="Merchant identifier, pseudonym, or category label")
    alert_count: int = Field(..., ge=0, description="Total alerts associated with this merchant")


class RiskWeightsResponse(BaseModel):
    """Configured composite risk scoring weights."""

    model_config = ConfigDict(extra="ignore")

    ml_prediction: float = Field(..., ge=0.0, le=1.0)
    velocity_rules: float = Field(..., ge=0.0, le=1.0)
    merchant_reputation: float = Field(..., ge=0.0, le=1.0)
    country_risk: float = Field(..., ge=0.0, le=1.0)
    device_anomaly: float = Field(..., ge=0.0, le=1.0)
    customer_history: float = Field(..., ge=0.0, le=1.0)
    previous_alerts: float = Field(..., ge=0.0, le=1.0)
    chargeback_history: float = Field(..., ge=0.0, le=1.0)
    behavior_anomaly: float = Field(..., ge=0.0, le=1.0)
    gnn_topological_risk: float = Field(default=0.0, ge=0.0, le=1.0)


class RiskWeightsUpdateRequest(BaseModel):
    """Request payload to adjust composite risk scoring weights."""

    model_config = ConfigDict(extra="forbid")

    ml_prediction: float = Field(0.25, ge=0.0, le=1.0, description="ML inference confidence weight")
    velocity_rules: float = Field(0.15, ge=0.0, le=1.0, description="Transaction frequency/velocity rule weight")
    merchant_reputation: float = Field(0.10, ge=0.0, le=1.0, description="Merchant historical risk factor weight")
    country_risk: float = Field(0.10, ge=0.0, le=1.0, description="Jurisdictional cross-border risk weight")
    device_anomaly: float = Field(0.08, ge=0.0, le=1.0, description="Device fingerprint anomaly weight")
    customer_history: float = Field(0.10, ge=0.0, le=1.0, description="Account tenure and KYC profile weight")
    previous_alerts: float = Field(0.08, ge=0.0, le=1.0, description="Prior SAR or alert recurrence weight")
    chargeback_history: float = Field(0.07, ge=0.0, le=1.0, description="Historical dispute/chargeback rate weight")
    behavior_anomaly: float = Field(0.07, ge=0.0, le=1.0, description="Baseline transaction deviation weight")
    gnn_topological_risk: float = Field(0.0, ge=0.0, le=1.0, description="GNN topological graph risk weight")

    @field_validator(
        "ml_prediction",
        "velocity_rules",
        "merchant_reputation",
        "country_risk",
        "device_anomaly",
        "customer_history",
        "previous_alerts",
        "chargeback_history",
        "behavior_anomaly",
        "gnn_topological_risk",
    )
    @classmethod
    def validate_weight_precision(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("Risk weight must be strictly bounded within [0.0, 1.0]")
        return round(v, 6)


class ComparativeModelItemSchema(BaseModel):
    """Single model entry in the multi-paradigm comparative benchmark."""

    model_config = ConfigDict(extra="ignore")

    paradigm: str = Field(..., description="Name or identifier of the evaluated paradigm")
    category: str = Field(..., description="Model category: THEORETICAL_UPPER_BOUND, PRODUCTION_CHAMPION, ISOLATED_SILO, CLASSICAL_BASELINE")
    pr_auc: float = Field(..., ge=0.0, le=1.0)
    roc_auc: float = Field(..., ge=0.0, le=1.0)
    recall_at_01_fpr: float = Field(..., ge=0.0, le=1.0)
    f1_score: float = Field(..., ge=0.0, le=1.0)
    brier_score: float = Field(..., ge=0.0, le=1.0)
    latency_ms: float = Field(..., ge=0.0)
    delta_pr_auc_vs_fed: float = Field(..., description="PR-AUC difference vs Federated Champion")
    privacy_guarantee: str = Field(..., description="Level of privacy preservation")
    legal_compliance: str = Field(..., description="Regulatory viability (GDPR/KVKK/Banking Secrecy)")
    description: str = Field(..., description="Technical summary of the paradigm configuration")


class CentralizationGapAnalysisSchema(BaseModel):
    """Quantification of the gap between pooled upper bound and federated consensus."""

    model_config = ConfigDict(extra="ignore")

    pooled_champion_model: str
    pooled_pr_auc: float
    pooled_roc_auc: float
    pooled_recall_at_01_fpr: float
    centralization_gap_pr_auc: float
    centralization_gap_roc_auc: float
    federated_efficiency_pct: float


class SiloDeficitAnalysisSchema(BaseModel):
    """Aggregate statistics quantifying isolated single-bank fraud blindness."""

    model_config = ConfigDict(extra="ignore")

    mean_pr_auc: float
    mean_roc_auc: float
    mean_recall_at_01_fpr: float
    silo_count: int
    collaborative_uplift_pr_auc: float
    collaborative_uplift_roc_auc: float


class ComparativeBenchmarkResponseSchema(BaseModel):
    """Full payload for the Multi-Paradigm Comparative Benchmark Widget."""

    model_config = ConfigDict(extra="ignore")

    dataset_name: str
    generated_at_utc: str
    random_state: int
    bank_count: int
    total_training_samples: int
    global_test_samples: int
    fraud_prevalence_pct: float
    comparison_matrix: list[ComparativeModelItemSchema]
    centralization_gap_analysis: CentralizationGapAnalysisSchema
    silo_deficit_analysis: SiloDeficitAnalysisSchema

