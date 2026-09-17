"""Design Partner Bank/Fintech Pilot Application Schemas.

Strict validation models for data ingestion PII scanning, benchmark sandbox evaluation,
distribution fidelity analysis, and pilot readiness checklists.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class IngestionValidationRequest(BaseModel):
    """Request payload to scan sample records for PII leakage."""

    model_config = ConfigDict(extra="forbid")

    partner_name: str = Field(..., min_length=2, max_length=128, description="Name of the design partner bank or fintech")
    schema_format: str = Field(
        "ISO_20022", description="Data standard format: ISO_20022 | OPEN_BANKING_PSD2 | CUSTOM_CSV"
    )
    sample_records: list[dict[str, Any]] = Field(
        ..., min_length=1, max_length=1000, description="Sample un-hashed transaction records to scan for PII"
    )


class PiiViolationDetail(BaseModel):
    """Detailed record of an identified PII pattern violation."""

    model_config = ConfigDict(extra="forbid")

    column: str = Field(..., description="DataFrame column name where PII was identified")
    pii_type: str = Field(..., description="Detected PII category (e.g. credit_card, iban, ssn_tckn, email, phone)")
    sample_count: int = Field(..., ge=1, description="Number of sample values violating privacy")
    remediation: str = Field(..., description="Actionable remediation guidance for partner engineering team")


class IngestionValidationResponse(BaseModel):
    """Response returned from automated Zero-Raw-PII scanner."""

    model_config = ConfigDict(extra="forbid")

    partner_name: str = Field(..., description="Name of the design partner institution")
    schema_format: str = Field(..., description="Evaluated data standard schema")
    is_clean_zero_pii: bool = Field(..., description="True if zero raw un-hashed PII tokens were detected")
    total_records_scanned: int = Field(..., ge=0, description="Count of transaction records analyzed")
    violations: list[PiiViolationDetail] = Field(default_factory=list, description="List of detected PII violations")
    status: str = Field(..., description="READY_FOR_LOCAL_EDGE_TRAINING | REMEDIATION_REQUIRED")
    guidance: str = Field(..., description="Technical instructions for edge client configuration")


class BenchmarkEvaluationResponse(BaseModel):
    """Response returned from synthetic reference benchmark evaluation."""

    model_config = ConfigDict(extra="ignore")

    dataset_name: str = Field(..., description="Evaluated reference benchmark dataset")
    total_samples: int = Field(..., ge=1, description="Sample volume evaluated")
    daily_volume: int = Field(..., ge=1, description="Modeled institutional daily transaction volume")
    performance_comparison: dict[str, Any] = Field(..., description="FL vs Single-Bank comparative metrics")
    distribution_fidelity: dict[str, Any] = Field(..., description="Degradation and fidelity metrics")
    multi_threshold_confusion_matrices: list[dict[str, Any]] | dict[str, Any] = Field(
        default_factory=list, description="Confusion matrices across thresholds"
    )
    bank_partitions: list[dict[str, Any]] | dict[str, Any] = Field(
        default_factory=list, description="Non-IID bank partition metadata"
    )


class DistributionFidelityResponse(BaseModel):
    """Response detailing statistical distribution shift and Wasserstein degradation."""

    model_config = ConfigDict(extra="ignore")

    dataset_name: str = Field(..., description="Evaluated dataset name")
    degradation_metrics: dict[str, Any] = Field(default_factory=dict, description="Measured empirical differences")
    feature_shifts: dict[str, Any] = Field(default_factory=dict, description="Per-feature Wasserstein distance metrics")


class PilotComplianceItem(BaseModel):
    """Compliance assessment line item."""

    model_config = ConfigDict(extra="forbid")

    standard: str = Field(..., description="Regulatory or architectural standard")
    clause: str = Field(..., description="Statutory law or technical clause (GDPR, KVKK, Basel III)")
    status: str = Field(..., description="PASSED | CONDITIONAL | FAILED")
    evidence: str = Field(..., description="Cryptographic or architectural proof")


class PilotComplianceChecklistResponse(BaseModel):
    """Institutional compliance and readiness assessment report."""

    model_config = ConfigDict(extra="forbid")

    partner_name: str = Field(..., description="Design partner institution name")
    jurisdiction: str = Field(..., description="Applicable regulatory jurisdiction (e.g. EU/TR/US)")
    overall_readiness_score: float = Field(..., ge=0.0, le=100.0, description="Overall institutional readiness percentage")
    status: str = Field(..., description="APPROVED_FOR_PILOT | CONDITIONAL_APPROVAL | REJECTED")
    compliance_items: list[PilotComplianceItem] = Field(..., description="List of evaluated compliance standards")
    cryptographic_guarantees: dict[str, str] = Field(..., description="Cryptographic security primitives")


class PartnerLeadRequest(BaseModel):
    """Inquiry registration for a prospective design partner institution."""

    model_config = ConfigDict(extra="forbid")

    partner_name: str = Field(..., min_length=2, max_length=128, description="Institution legal name")
    contact_email: str = Field(..., min_length=5, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", description="Primary contact email")
    jurisdiction: str = Field(..., min_length=2, max_length=32, description="Operating jurisdiction")
    institution_type: str = Field("COMMERCIAL_BANK", description="COMMERCIAL_BANK | FINTECH | PAYMENT_PROCESSOR | CENTRAL_BANK")
    daily_volume: int = Field(100_000, ge=1_000, description="Estimated daily transaction volume")


class PartnerLeadResponse(BaseModel):
    """Confirmation returned upon registering design partner inquiry."""

    model_config = ConfigDict(extra="forbid")

    lead_id: str = Field(..., description="Unique lead registration identifier")
    partner_name: str = Field(..., description="Institution name")
    status: str = Field("PENDING_REVIEW", description="Lead processing status")
    assigned_tier: str = Field(..., description="Suggested consortium tier")
    onboarding_wizard_url: str = Field(..., description="Deep link to consortium onboarding wizard")


class PilotStatusResponse(BaseModel):
    """Overview of design partner sandbox environments and benchmark readiness."""

    model_config = ConfigDict(extra="forbid")

    partner_name: str = Field(..., description="Partner name")
    status: str = Field(..., description="Pilot program active status")
    supported_schemas: list[str] = Field(..., description="Supported financial message formats")
    benchmarks_available: list[str] = Field(..., description="Calibrated reference datasets available for sandbox trials")
    sandbox_mode: str = Field("AIRGAPPED_CONTAINER", description="Deployment architecture mode")


class PilotFeedbackRequest(BaseModel):
    """Feedback submitted by banking IT committees during sandbox trial."""

    model_config = ConfigDict(extra="forbid")

    partner_name: str = Field(..., min_length=2, max_length=128, description="Institution name")
    contact_email: str = Field(..., min_length=5, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", description="Submitter email")
    satisfaction_rating: int = Field(..., ge=1, le=5, description="Satisfaction score from 1 (poor) to 5 (excellent)")
    category: str = Field("ACCURACY_VS_PRIVACY", description="ACCURACY_VS_PRIVACY | INTEGRATION_EASE | COMPLIANCE_SECURITY | LATENCY")
    comments: str = Field(..., min_length=5, max_length=2000, description="Qualitative feedback comments")


class PilotFeedbackResponse(BaseModel):
    """Confirmation of recorded design partner trial feedback."""

    model_config = ConfigDict(extra="forbid")

    feedback_id: str = Field(..., description="Unique feedback confirmation receipt")
    partner_name: str = Field(..., description="Institution name")
    recorded_at: str = Field(..., description="ISO timestamp of receipt")
    status: str = Field("ACKNOWLEDGED", description="Processing status")
