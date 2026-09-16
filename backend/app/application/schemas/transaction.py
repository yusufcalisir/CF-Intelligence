"""Transaction and Real-Time Inference Application Schemas.

Clean Architecture Pydantic v2 schemas for real-time transaction scoring,
batch prediction, SHAP/LIME explainability, and tenant inference quota telemetry.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SignalBreakdown(BaseModel):
    """Signal breakdown item evaluated by the composite risk engine."""

    model_config = ConfigDict(extra="ignore")

    signal_name: str = Field(..., description="Risk signal identifier")
    weight: float = Field(..., description="Signal weight in composite scoring")
    raw_value: float = Field(..., description="Raw signal value")
    normalized_score: float = Field(..., description="Normalized score [0.0, 1.0]")
    explanation: str = Field(..., description="Human-readable explanation of the signal")


class AlertDetails(BaseModel):
    """Details of an intelligence alert generated from a high-risk prediction."""

    model_config = ConfigDict(extra="ignore")

    alert_id: str = Field(..., description="Unique alert UUID")
    severity: str = Field(..., description="Alert severity: low, medium, high, critical")
    status: str = Field(..., description="Current alert lifecycle status")
    reason_codes: list[str] = Field(default_factory=list, description="Triggered reason codes")
    explanation: str = Field(..., description="Synthesized narrative explanation")
    top_features: list[dict[str, Any]] = Field(
        default_factory=list, description="Top contributing feature attributions"
    )
    risk_factors: list[str] = Field(default_factory=list, description="Identified risk factor tags")


class TransactionPredictRequest(BaseModel):
    """Single transaction payload submitted for real-time risk scoring."""

    model_config = ConfigDict(extra="ignore")

    transaction_amount: float = Field(..., ge=0.0, description="Amount of the transaction")
    merchant_category: str = Field(
        "grocery",
        max_length=256,
        description="Merchant category name (e.g. crypto, grocery, travel)",
    )
    country_code: str = Field(
        "US", max_length=256, description="Originating ISO country code (e.g. US, NG, TR)"
    )
    device_type: str = Field(
        "web_browser", max_length=256, description="Type of device (e.g. web_browser, mobile_app)"
    )
    velocity: float = Field(1.0, ge=0.0, description="Transaction velocity (txns/hr)")
    hour_of_day: int = Field(12, ge=0, le=23, description="Hour of the transaction")
    merchant_risk_score: float = Field(
        0.05, ge=0.0, le=1.0, description="Historical fraud rate of the merchant"
    )
    customer_history_score: float = Field(
        0.95, ge=0.0, le=1.0, description="Trustworthiness score of the customer"
    )
    chargeback_count: int = Field(0, ge=0, description="Customer chargeback count")
    account_age_days: int = Field(365, ge=0, description="Age of the customer account in days")
    bank_id: str | None = Field(
        None, max_length=256, description="Optional bank identifier. Defaults to gateway ID."
    )
    simulation_id: str | None = Field(
        None, max_length=256, description="Optional simulation run ID to resolve versioned models."
    )
    transaction_id: str | None = Field(
        None, max_length=256, description="Optional client-provided transaction identifier"
    )


class TransactionPredictResponse(BaseModel):
    """Response payload containing composite risk score, decision, and breakdown."""

    model_config = ConfigDict(extra="ignore")

    transaction_id: str | None = Field(None, description="Transaction identifier")
    fraud_probability: float = Field(
        ..., description="ML model inferred fraud probability [0.0, 1.0]"
    )
    risk_score: float = Field(..., description="Composite risk score [0.0, 1000.0]")
    is_fraud_suspected: bool = Field(
        ..., description="Whether transaction exceeds fraud suspicion threshold"
    )
    risk_level: str = Field(..., description="Risk category: LOW, MEDIUM, HIGH, CRITICAL")
    breakdown: list[SignalBreakdown] = Field(
        default_factory=list, description="Risk signal breakdown"
    )
    alert_details: AlertDetails | None = Field(
        None, description="Detailed alert information if fraud suspected"
    )
    policy_action: str = Field("ALLOW", description="Evaluated action from dynamic policy engine")
    triggered_rules: list[str] = Field(
        default_factory=list, description="List of triggered policy rules"
    )
    latency_ms: float = Field(0.0, description="End-to-end evaluation latency in milliseconds")


class BatchTransactionPredictRequest(BaseModel):
    """Batch scoring request payload."""

    model_config = ConfigDict(extra="ignore")

    transactions: list[TransactionPredictRequest] = Field(
        ..., min_length=1, max_length=1000, description="List of transactions to score (up to 1,000)"
    )
    bank_id: str | None = Field(None, max_length=256, description="Optional tenant bank ID")
    simulation_id: str | None = Field(None, max_length=256, description="Optional simulation ID")


class BatchPredictionItem(BaseModel):
    """Individual item result within a batch prediction response."""

    model_config = ConfigDict(extra="ignore")

    transaction_id: str = Field(..., description="Transaction identifier")
    fraud_probability: float = Field(..., description="Model fraud probability")
    risk_score: float = Field(..., description="Composite risk score [0.0, 1000.0]")
    decision: str = Field(..., description="ALLOW, REVIEW, or BLOCK")
    risk_level: str = Field(..., description="LOW, MEDIUM, HIGH, or CRITICAL")
    is_fraud_suspected: bool = Field(..., description="Fraud suspicion flag")
    policy_action: str = Field("ALLOW", description="Dynamic policy evaluation action")
    latency_ms: float = Field(..., description="Processing latency in ms")


class BatchPredictionResponse(BaseModel):
    """Summary and itemized results for batch prediction."""

    model_config = ConfigDict(extra="ignore")

    total_processed: int = Field(..., description="Total transactions evaluated in batch")
    fraud_suspected_count: int = Field(
        ..., description="Number of transactions flagged as fraud suspected"
    )
    predictions: list[BatchPredictionItem] = Field(
        ..., description="Individual transaction evaluation results"
    )
    batch_latency_ms: float = Field(..., description="Total batch evaluation latency in ms")


class FeatureAttributionItem(BaseModel):
    """Attribution item from SHAP/LIME explanation."""

    model_config = ConfigDict(extra="ignore")

    feature: str = Field(..., description="Feature name")
    value: float = Field(..., description="Normalized feature value evaluated")
    contribution: float = Field(..., description="Shapley value or feature contribution")
    direction: str = Field(..., description="INCREASES_RISK or DECREASES_RISK")
    description: str = Field(..., description="Human-readable attribution summary")


class CounterfactualPathItem(BaseModel):
    """Actionable counterfactual feature adjustment."""

    model_config = ConfigDict(extra="ignore")

    feature: str = Field(..., description="Feature to modify")
    original_value: float = Field(..., description="Original value")
    target_value: float = Field(..., description="Remediated value to clear alert")
    description: str = Field(..., description="Actionable recommendation")


class ExplainTransactionRequest(BaseModel):
    """Request payload for model explainability report."""

    model_config = ConfigDict(extra="ignore")

    transaction: TransactionPredictRequest = Field(
        ..., description="Transaction payload to explain"
    )
    transaction_id: str | None = Field(None, max_length=256, description="Optional transaction ID")
    simulation_id: str | None = Field(None, max_length=256, description="Optional simulation ID")
    method: str = Field(
        "SHAP",
        description="Explainability method: SHAP, LIME, or COUNTERFACTUAL",
    )


class ExplainTransactionResponse(BaseModel):
    """Detailed explainability response with attributions and counterfactual paths."""

    model_config = ConfigDict(extra="ignore")

    transaction_id: str = Field(..., description="Transaction identifier")
    method: str = Field(..., description="Method used: SHAP, LIME, or COUNTERFACTUAL")
    base_value: float = Field(..., description="Expected baseline model score E[f(x)]")
    predicted_score: float = Field(..., description="Actual model score f(x)")
    attributions: list[FeatureAttributionItem] = Field(
        default_factory=list, description="Feature attribution list"
    )
    summary: str = Field(..., description="Natural language explainability summary")
    counterfactual_paths: list[CounterfactualPathItem] = Field(
        default_factory=list, description="Actionable remediation paths"
    )
    latency_ms: float = Field(..., description="Explanation computation latency in ms")


class ScoreTransactionRequest(BaseModel):
    """Request payload for ultra low-latency risk score endpoint."""

    model_config = ConfigDict(extra="ignore")

    transaction_id: str = Field(..., max_length=256, description="Unique transaction identifier")
    account_id: str = Field(..., max_length=256, description="Source account identifier")
    amount: float = Field(..., ge=0.0, description="Transaction amount")
    currency: str = Field("EUR", max_length=16, description="ISO 4217 currency code")
    merchant_id: str = Field(..., max_length=256, description="Target merchant identifier")
    country: str = Field("EE", max_length=16, description="ISO 3166-1 alpha-2 origin country code")
    device_id: str = Field(..., max_length=256, description="Device fingerprint identifier")


class FeatureContributionItem(BaseModel):
    """Individual feature contribution to the score."""

    model_config = ConfigDict(extra="ignore")

    feature: str = Field(..., description="Feature or signal identifier")
    contribution: float = Field(..., description="Weight-adjusted score contribution")


class RelatedEntityItem(BaseModel):
    """Related entity risk evaluation."""

    model_config = ConfigDict(extra="ignore")

    entity_type: str = Field(..., description="Linked entity type (e.g. device, ip, merchant)")
    risk: str = Field(..., description="Risk tier: LOW, MEDIUM, HIGH")


class ScoreTransactionResponse(BaseModel):
    """Response payload for sub-10ms risk score endpoint."""

    model_config = ConfigDict(extra="ignore")

    risk_score: int = Field(..., ge=0, le=1000, description="Normalized risk score [0, 1000]")
    risk_level: str = Field(..., description="LOW, MEDIUM, or HIGH risk classification")
    decision: str = Field(..., description="Automated decision: ALLOW, REVIEW, or BLOCK")
    model_version: str = Field("v2.4.1", description="Active global model version")
    explanations: list[FeatureContributionItem] = Field(default_factory=list)
    related_entities: list[RelatedEntityItem] = Field(default_factory=list)
    latency_ms: float = Field(..., description="Response latency in milliseconds")


class TransactionFeedbackRequest(BaseModel):
    """Ground truth feedback for model calibration."""

    model_config = ConfigDict(extra="ignore")

    transaction_id: str = Field(
        ...,
        min_length=3,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Unique transaction identifier",
    )
    actual_label: int = Field(
        ..., ge=0, le=1, description="Actual outcome (0 for legitimate, 1 for fraud)"
    )
    simulation_id: str | None = Field(None, max_length=128, pattern=r"^[a-zA-Z0-9_\-\.]*$")
    simulationId: str | None = Field(None, max_length=128, pattern=r"^[a-zA-Z0-9_\-\.]*$")  # noqa: N815

    @property
    def effective_simulation_id(self) -> str:
        return self.simulation_id or self.simulationId or "live_prod_v2"


class RealtimeInferenceRequest(BaseModel):
    """Schema for online transaction authorization requests."""

    model_config = ConfigDict(extra="ignore")

    transaction_id: str = Field(
        ...,
        min_length=3,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        json_schema_extra={"example": "tx_88992211"},
    )
    amount: float = Field(
        ...,
        ge=0.0,
        le=1_000_000_000.0,
        json_schema_extra={"example": 1250.50},
    )
    currency: str = Field(
        "USD",
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
        json_schema_extra={"example": "USD"},
    )
    source_account: str = Field(
        ...,
        min_length=3,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        json_schema_extra={"example": "acc_src_991"},
    )
    target_account: str = Field(
        ...,
        min_length=3,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        json_schema_extra={"example": "acc_dst_002"},
    )
    merchant_category: str = Field(
        "general_retail",
        min_length=2,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        json_schema_extra={"example": "crypto_exchange"},
    )
    velocity_1h: int = Field(
        1,
        ge=0,
        le=10_000,
        json_schema_extra={"example": 3},
    )
    force_fallback: bool = Field(
        False,
        description="Simulate model timeout/failure to test fallback engine.",
    )


class RealtimeInferenceResponse(BaseModel):
    """Schema for online transaction authorization decision responses."""

    model_config = ConfigDict(extra="ignore")

    transaction_id: str
    risk_score: float
    decision: str  # ALLOW, REVIEW, or BLOCK
    latency_ms: float
    evaluated_by: str  # "ML_MODEL" or "HEURISTIC_FALLBACK"
    explanation: str


class InferenceQuotaResponse(BaseModel):
    """Tenant inference quota and consumption telemetry response."""

    model_config = ConfigDict(extra="ignore")

    tenant_id: str = Field(..., description="Tenant institution identifier")
    tier: str = Field("ENTERPRISE", description="Tenant subscription tier")
    daily_inferences_limit: int = Field(..., description="Configured daily max inference requests")
    daily_inferences_used: int = Field(..., description="Inference count used today")
    daily_inferences_remaining: int = Field(..., description="Remaining inference quota for today")
    monthly_fl_rounds_limit: int = Field(..., description="Configured monthly FL rounds limit")
    monthly_fl_rounds_used: int = Field(..., description="FL rounds used this month")
    monthly_fl_rounds_remaining: int = Field(..., description="Remaining FL rounds this month")
    storage_used_mb: float = Field(..., description="Storage currently consumed in MB")
    max_storage_mb: float = Field(..., description="Storage quota limit in MB")
    reset_date: str = Field(..., description="UTC date when daily quota resets")
