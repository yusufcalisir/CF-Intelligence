"""Pydantic v2 schemas for European AML Monitoring Scenarios & Hybrid Rule Engine.

Defines:
1. Scenario severity, category, and hybrid decision action enums.
2. AML scenario definitions, parameters, and regulatory citations.
3. Transaction context inputs and scenario hit evidence.
4. Hybrid scoring response synthesizing rule penalties with ML/GNN predictions.
5. Scenario library and operational telemetry schemas.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ScenarioSeverity(StrEnum):
    """Severity classification for AML scenario hits."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ScenarioCategory(StrEnum):
    """Typology classification for AML monitoring rules."""

    STRUCTURING = "STRUCTURING"
    VELOCITY = "VELOCITY"
    MULE_ACTIVITY = "MULE_ACTIVITY"
    CORRIDOR_RISK = "CORRIDOR_RISK"
    BEHAVIORAL_ANOMALY = "BEHAVIORAL_ANOMALY"
    CORPORATE_LAYERING = "CORPORATE_LAYERING"
    DIGITAL_ASSET = "DIGITAL_ASSET"
    TRADE_BASED = "TRADE_BASED"


class HybridAction(StrEnum):
    """Enforcement decision produced by the hybrid scoring engine."""

    ALLOW = "ALLOW"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    SUSPEND = "SUSPEND"
    BLOCK = "BLOCK"


class AMLScenarioDefinition(BaseModel):
    """Configuration and regulatory metadata for an AML scenario."""

    model_config = ConfigDict(frozen=True)

    scenario_code: str = Field(..., description="Unique scenario identifier (e.g. SCN_EUR_STRUCTURING_SUB_10K)")
    name: str = Field(..., description="Human-readable scenario name")
    category: ScenarioCategory = Field(..., description="FATF/AMLD typology category")
    severity: ScenarioSeverity = Field(..., description="Inherent severity tier")
    base_penalty: float = Field(..., ge=0.0, le=1000.0, description="Default penalty points on trigger")
    regulatory_basis: str = Field(..., description="European AMLD / FATF regulatory citation")
    description: str = Field(..., description="Detailed typology explanation")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Operational detection thresholds")
    enabled: bool = Field(default=True, description="Whether the scenario is active")


class AMLScenarioHit(BaseModel):
    """Evidence and penalty produced when a transaction triggers an AML scenario."""

    model_config = ConfigDict(frozen=True)

    scenario_code: str
    scenario_name: str
    category: ScenarioCategory
    severity: ScenarioSeverity
    penalty_score: float
    regulatory_citation: str
    description: str
    trigger_rationale: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class TransactionContext(BaseModel):
    """Enriched transaction context evaluated against European AML scenarios."""

    model_config = ConfigDict(populate_by_name=True)

    transaction_id: str = Field(..., description="Unique transaction reference")
    amount: float = Field(..., gt=0.0, description="Transaction amount in currency")
    currency: str = Field(default="EUR", description="ISO 4217 three-letter currency code")
    originator_id: str = Field(..., description="Originating entity or customer identifier")
    beneficiary_id: str = Field(..., description="Beneficiary entity or customer identifier")
    origin_country: str = Field(..., description="ISO 3166-1 alpha-2 country of originator")
    destination_country: str = Field(..., description="ISO 3166-1 alpha-2 country of beneficiary")
    payment_rail: str = Field(default="SEPA_INSTANT", description="Payment rail (SEPA_INSTANT, TARGET2, SWIFT)")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Entity profile and behavioral baselines
    originator_account_age_days: int = Field(default=365, ge=0)
    originator_is_pep: bool = Field(default=False)
    originator_is_sanctioned: bool = Field(default=False)
    beneficiary_is_pep: bool = Field(default=False)
    beneficiary_is_sanctioned: bool = Field(default=False)
    is_dormant_account: bool = Field(default=False, description="True if account was dormant (>90 days)")
    account_average_daily_volume: float = Field(default=1000.0, ge=0.0)

    # Velocity and layering indicators
    inbound_credits_last_1h: float = Field(default=0.0, ge=0.0)
    outbound_debits_last_1h: float = Field(default=0.0, ge=0.0)
    transaction_count_last_1h: int = Field(default=1, ge=1)
    recent_distinct_counterparties_24h: int = Field(default=1, ge=1)
    funds_retention_ratio: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Ratio of inbound funds retained after outbound transfer"
    )

    # Contextual metadata
    merchant_category: str | None = Field(default=None, description="MCC or merchant classification")
    is_nighttime_execution: bool = Field(default=False, description="Executed between 01:00 and 05:00 local time")
    is_crypto_service_provider: bool = Field(default=False, description="Counterparty is CASP or VASP")
    unit_price_deviation_ratio: float | None = Field(
        default=None, description="For trade transactions, unit price divided by benchmark fair value"
    )
    cyclic_mule_hops: int | None = Field(
        default=None, description="Hops in detected circular transaction loop"
    )


class AMLScenarioEvaluationRequest(BaseModel):
    """Request payload for evaluating an activity through the hybrid AML engine."""

    model_config = ConfigDict(populate_by_name=True)

    transaction: TransactionContext
    ml_risk_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Probabilistic fraud/AML score from Federated ML / GNN model (0.0 to 1.0)",
    )
    gnn_anomaly_embedding_norm: float | None = Field(
        default=None,
        ge=0.0,
        description="L2 norm of GNN topological anomaly embedding",
    )
    strict_regulatory_override: bool = Field(
        default=True,
        description="If True, critical sanctions or FATF black-list triggers immediately force BLOCK action",
    )


class HybridScoringResponse(BaseModel):
    """Explainable hybrid risk score combining deterministic compliance rules and ML."""

    model_config = ConfigDict(frozen=True)

    transaction_id: str
    action: HybridAction
    composite_risk_score: float = Field(..., ge=0.0, le=1000.0)
    rule_penalty_score: float = Field(..., ge=0.0, le=1000.0)
    ml_risk_score: float = Field(..., ge=0.0, le=1.0)
    ml_penalty_equivalent: float = Field(..., ge=0.0, le=1000.0)
    regulatory_override_applied: bool
    override_reason: str | None = None
    triggered_scenarios: list[AMLScenarioHit]
    total_scenarios_evaluated: int
    total_scenarios_triggered: int
    explainability_narrative: str
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class BatchScenarioEvaluationRequest(BaseModel):
    """Batch evaluation payload for high-throughput stream processing."""

    evaluations: list[AMLScenarioEvaluationRequest] = Field(..., min_length=1, max_length=100)


class BatchScenarioEvaluationResponse(BaseModel):
    """Batch evaluation summary and result list."""

    total_processed: int
    total_blocked: int
    total_suspended: int
    total_flagged_for_review: int
    total_allowed: int
    results: list[HybridScoringResponse]


class ScenarioLibraryResponse(BaseModel):
    """Listing of all pre-configured European AML scenarios."""

    total_scenarios: int
    scenarios: list[AMLScenarioDefinition]


class ScenarioMetricsResponse(BaseModel):
    """Operational telemetry on scenario trigger frequencies and decision distributions."""

    tenant_id: str
    total_evaluations: int
    action_breakdown: dict[str, int]
    top_triggered_scenarios: dict[str, int]
    average_composite_score: float
    last_evaluation_time: datetime | None = None
