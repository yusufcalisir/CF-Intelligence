"""Pydantic v2 schemas for Regulatory Compliance, FinCEN SAR 2.0, and GDPR Erasure API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ── Algorithmic Fairness & Model Governance ───────────────────────────


class FairnessAuditRequest(BaseModel):
    """Request model for algorithmic fairness and non-discrimination audits."""

    model_config = ConfigDict(extra="forbid")

    y_pred_probs: list[float] = Field(
        ..., min_length=1, description="List of predicted model scores / probabilities"
    )
    sensitive_attributes: list[int] = Field(
        ...,
        min_length=1,
        description="Binary demographic attribute flags (1=protected group, 0=reference group)",
    )
    y_true: list[int] | None = Field(
        None, description="Optional ground truth labels for TPR/FPR fairness parity analysis"
    )
    threshold: float = Field(
        0.50, ge=0.0, le=1.0, description="Classification decision threshold"
    )


class FairnessAuditResponse(BaseModel):
    """Result of EEOC 80% rule disparate impact and fairness parity analysis."""

    model_config = ConfigDict(extra="forbid")

    threshold: float
    sample_count: int
    protected_count: int
    reference_count: int
    protected_selection_rate: float
    reference_selection_rate: float
    disparate_impact_ratio: float
    demographic_parity_difference: float
    eeoc_80_percent_rule: str
    overall_fairness_status: str
    equal_opportunity_difference: float | None = None
    average_odds_difference: float | None = None
    protected_tpr: float | None = None
    reference_tpr: float | None = None
    protected_fpr: float | None = None
    reference_fpr: float | None = None
    status: str | None = None


class CanaryGateRequest(BaseModel):
    """Request model for evaluating candidate model promotion via CanaryQualityGate."""

    model_config = ConfigDict(extra="forbid")

    candidate_metrics: dict[str, Any] = Field(
        ...,
        description="Candidate model performance metrics (auc_roc, disparate_impact_ratio, p99_latency_ms, fpr)",
    )
    champion_metrics: dict[str, Any] | None = Field(
        None, description="Optional champion baseline metrics for comparative delta validation"
    )


class CanaryGateResponse(BaseModel):
    """Automated canary deployment decision and threshold evaluation."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    decision: str
    checks: dict[str, bool]
    reasons: list[str]
    metrics: dict[str, Any]
    evaluated_at: str | None = None


# ── SOC 2 Type II & SR 11-7 Model Governance ──────────────────────────


class SOC2ControlEvidenceItem(BaseModel):
    """Single SOC 2 Trust Services Criteria control evidence status."""

    model_config = ConfigDict(extra="forbid")

    title: str
    status: str
    evidence: str


class SOC2EvidenceReportResponse(BaseModel):
    """Attestation evidence collection report covering SOC 2 Trust Services Criteria."""

    model_config = ConfigDict(extra="forbid")

    report_id: str
    timestamp: str
    compliance_status: str
    total_controls_audited: int
    passed_controls: int
    failed_controls: int
    controls: dict[str, SOC2ControlEvidenceItem]


class SR117ClauseItem(BaseModel):
    """Federal Reserve SR 11-7 model risk management clause."""

    model_config = ConfigDict(extra="forbid")

    clause_id: str
    title: str
    status: str
    specification: str
    evidence: str


class SR117AuditReportResponse(BaseModel):
    """SR 11-7 Pillar I Conceptual Soundness audit report."""

    model_config = ConfigDict(extra="forbid")

    pillar: str
    framework: str
    compliance_score_pct: float
    overall_status: str
    total_clauses: int
    passed_clauses: int
    clauses: list[SR117ClauseItem]
    audited_at: str


class SR117MilestoneItem(BaseModel):
    """4-quarter SR 11-7 validation schedule milestone."""

    model_config = ConfigDict(extra="forbid")

    quarter: str
    milestone: str
    focus_areas: list[str]
    deadline: str
    status: str
    lead_auditor: str


class SR117ScheduleResponse(BaseModel):
    """Federal Reserve SR 11-7 4-quarter independent model validation schedule."""

    model_config = ConfigDict(extra="forbid")

    framework: str
    reference_year: int
    overall_mrm_status: str
    next_audit_milestone: str
    next_audit_deadline: str
    cadence: str
    milestones: list[SR117MilestoneItem]


# ── FinCEN SAR 2.0 e-Filing ───────────────────────────────────────────


class SARValidateRequest(BaseModel):
    """Request model for validating arbitrary XML payload against FinCEN SAR 2.0 schema."""

    model_config = ConfigDict(extra="forbid")

    xml_content: str = Field(..., min_length=10, description="Raw FinCEN SAR XML content")


class SARValidationResponse(BaseModel):
    """Verification receipt indicating syntax, tag hierarchy, and XSD conformity."""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    sha256_hash: str
    root_element: str = "EFilingSubmission"
    schema_version: str = "FinCEN_SAR_2.0"
    byte_size: int


class SARGenerateRequest(BaseModel):
    """Request model for compiling and filing an official FinCEN SAR report."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(..., description="Confirmed fraud case ID to compile SAR filing for")
    institution_name: str | None = Field(
        None, description="Optional reporting financial institution name"
    )
    tin_type: str | None = Field(None, description="TIN Type (EIN/SSN)")
    narrative_override: str | None = Field(None, description="Optional custom narrative summary")


class SARFilingRecordResponse(BaseModel):
    """Immutable audit record representing an official FinCEN SAR regulatory submission."""

    model_config = ConfigDict(extra="forbid")

    filing_id: str
    submission_id: str
    case_id: str
    sha256_hash: str
    status: str = "FILED"
    institution_name: str
    created_at: str
    xml_path: str
    alert_count: int
    subject_count: int
    total_risk_score: float


class SARFilingDetailResponse(BaseModel):
    """Detailed FinCEN SAR filing representation including raw XML payload."""

    model_config = ConfigDict(extra="forbid")

    filing_id: str
    submission_id: str
    case_id: str
    sha256_hash: str
    status: str
    institution_name: str
    created_at: str
    xml_path: str
    alert_count: int
    subject_count: int
    total_risk_score: float
    xml_content: str


# ── Enterprise Data Retention & GDPR Art. 17 Erasure ──────────────────


class RetentionPolicyRequest(BaseModel):
    """Request model for configuring per-tenant retention TTL policy."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(..., min_length=2, max_length=64, description="Target bank institution or tenant ID")
    category: str = Field(
        ...,
        description="Data category (TRANSACTION_LOGS, INFERENCE_AUDITS, GRAPH_EDGES, EXPLAINABILITY_REPORTS, CUSTOMER_ENTITIES)",
    )
    ttl_days: int = Field(..., gt=0, le=3650, description="Time-to-live schedule in days (must be positive)")
    erasure_method: str = Field(
        "CRYPTOGRAPHIC_ZEROIZATION",
        description="Sanitization method (HARD_DELETE, CRYPTOGRAPHIC_ZEROIZATION, ANONYMIZATION)",
    )


class RetentionPolicyResponse(BaseModel):
    """Response model for configured retention policy."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    category: str
    ttl_days: int
    erasure_method: str


class RetentionPurgeRequest(BaseModel):
    """Request model for triggering automated TTL purge across expired tenant records."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(..., min_length=2, max_length=64, description="Tenant institution ID to scan and purge")


class GDPRErasureRequest(BaseModel):
    """Request model for executing GDPR Article 17 Right-to-be-Forgotten erasure."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(..., min_length=2, max_length=64, description="Tenant institution ID")
    entity_id_hash: str = Field(
        ..., min_length=8, max_length=128, description="Cryptographic privacy hash or primary identifier of entity"
    )
    category: str | None = Field(None, description="Optional target category (defaults to CUSTOMER_ENTITIES)")


class ErasureAuditRecordResponse(BaseModel):
    """Response model representing a verifiable cryptographic erasure audit record."""

    model_config = ConfigDict(extra="forbid")

    erasure_id: str
    tenant_id: str
    category: str
    records_erased_count: int
    erasure_hash: str
    timestamp: str
    status: str = "VERIFIED_ERASED"
    prev_erasure_hash: str | None = None
    affected_tables: list[str] = Field(default_factory=list)


class ErasureChainVerificationResponse(BaseModel):
    """Response model for cryptographic verification of tenant erasure audit ledger."""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    total_records: int
    last_hash: str
    tamper_reason: str | None = None
