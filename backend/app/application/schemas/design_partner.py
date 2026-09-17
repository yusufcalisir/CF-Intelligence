"""Pydantic v2 schemas for Design Partner and Real-World Benchmark API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class IngestionValidationRequest(BaseModel):
    """Request payload to validate design partner data ingestion and zero-PII compliance."""

    model_config = ConfigDict(extra="forbid")

    partner_name: str = Field(..., min_length=2, max_length=128, description="Name of the design partner bank or fintech")
    schema_format: str = Field(
        "ISO_20022",
        pattern=r"^(ISO_20022|OPEN_BANKING_PSD2|CUSTOM_CSV)$",
        description="ISO_20022 | OPEN_BANKING_PSD2 | CUSTOM_CSV",
    )
    sample_records: list[dict[str, Any]] = Field(
        ..., min_length=1, description="Sample un-hashed transaction records to scan for PII"
    )


class PiiViolationItem(BaseModel):
    """Detected PII violation detail."""

    column: str
    pii_type: str
    sample_count: int
    remediation: str


class IngestionValidationResponse(BaseModel):
    """Response returned from Zero-PII scanner."""

    partner_name: str
    schema_format: str
    is_clean_zero_pii: bool
    total_records_scanned: int
    violations: list[PiiViolationItem]
    status: str
    guidance: str


class CostReportResponse(BaseModel):
    """Financial and alert fatigue cost report."""

    baseline_total_cost_dollars: float
    fl_total_cost_dollars: float
    total_saved_dollars: float
    operational_fte_hours_saved: float
    roi_multiple: float


class BenchmarkModelPerformanceResponse(BaseModel):
    """Model performance metrics on benchmark evaluation."""

    roc_auc: float
    pr_auc: float
    recall_at_01_fpr: float
    cost_report: CostReportResponse


class FederatedAdvantageResponse(BaseModel):
    """Quantified economic and statistical advantage of federated model."""

    pr_auc_gain: float
    recall_at_01_fpr_gain: float
    daily_fraud_loss_saved_dollars: float
    daily_investigation_saved_dollars: float
    net_daily_economic_benefit_dollars: float


class BenchmarkPerformanceComparison(BaseModel):
    """Comparison of Federated Learning vs Local Isolated model."""

    federated_learning: BenchmarkModelPerformanceResponse
    isolated_local_model: BenchmarkModelPerformanceResponse
    federated_advantage: FederatedAdvantageResponse


class BankPartitionItem(BaseModel):
    """Bank partition sample distribution in benchmark."""

    bank_id: str
    samples: int
    fraud_count: int
    fraud_ratio: float


class DistributionFidelityResponse(BaseModel):
    """Distribution fidelity, shift, and Wasserstein distance report."""

    wasserstein_distance: float
    ks_statistic: float
    ks_pvalue: float
    js_divergence: float
    drift_detected: bool
    fidelity_score: float
    metrics: dict[str, Any] = Field(default_factory=dict)


class BenchmarkEvaluationResponse(BaseModel):
    """Complete institutional benchmark evaluation response."""

    dataset_name: str
    source_type: str
    total_transactions_evaluated: int
    actual_fraud_count: int
    actual_fraud_rate_percent: float
    performance_comparison: BenchmarkPerformanceComparison
    multi_threshold_confusion_matrices: list[dict[str, Any]]
    distribution_fidelity: DistributionFidelityResponse
    bank_partitions: list[BankPartitionItem]


class ComplianceCheckItem(BaseModel):
    """Individual regulatory compliance check item."""

    standard: str
    clause: str
    status: str
    evidence: str


class PilotComplianceChecklistResponse(BaseModel):
    """Institutional readiness and compliance assessment response."""

    partner_name: str
    jurisdiction: str
    overall_readiness_score: float
    status: str
    compliance_items: list[ComplianceCheckItem]
    cryptographic_guarantees: dict[str, str]


class PilotLeadRequest(BaseModel):
    """Request to enroll an institution in the commercial pilot program."""

    model_config = ConfigDict(extra="forbid")

    institution_name: str = Field(..., min_length=2, max_length=128, description="Legal institution name")
    contact_name: str = Field(..., min_length=2, max_length=128, description="Primary contact person")
    contact_email: str = Field(
        ..., min_length=5, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", description="Official business email"
    )
    tier: str = Field("TIER_1", pattern=r"^(TIER_1|TIER_2|COMMUNITY_BANK|FINTECH)$", description="Participant tier")
    jurisdiction: str = Field("US", min_length=2, max_length=10, description="Operating jurisdiction")
    monthly_tx_volume: int = Field(1_000_000, ge=1000, description="Estimated monthly transaction volume")
    notes: str | None = Field(None, max_length=1024, description="Additional pilot requirements")


class PilotLeadResponse(BaseModel):
    """Confirmation response for enrolled pilot partner."""

    lead_id: str
    institution_name: str
    status: str
    assigned_tier: str
    sandbox_provisioned: bool
    created_at: str
