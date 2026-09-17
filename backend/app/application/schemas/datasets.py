"""Pydantic v2 schemas for Dataset Ingestion Studio, Pre-Flight Inspection & Great Expectations Gating."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ColumnMappingItem(BaseModel):
    """Mapping descriptor between custom uploaded column and canonical AML feature signal."""

    model_config = ConfigDict(extra="forbid")

    source_column: str = Field(..., description="Uploaded CSV/Parquet header title")
    target_signal: str = Field(..., description="Inferred canonical AML feature name")
    data_type: str = Field(..., description="Inferred primitive column type")
    sample_values: list[Any] = Field(default_factory=list, description="Sanitized preview entries")
    is_required: bool = Field(..., description="True if mandatory for federated training")
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Heuristic matching confidence")


class DatasetPreviewRequest(BaseModel):
    """Payload to trigger pre-flight column inference, PII scanning, and schema compliance checking."""

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(..., min_length=1, max_length=256, description="Uploaded file name")
    file_format: Literal["csv", "parquet", "tsv", "gz"] = Field(..., description="Container format")
    raw_header: list[str] = Field(default_factory=list, description="Extracted column titles")
    sample_rows: list[dict[str, Any]] = Field(default_factory=list, description="First N records for sandbox inspection")
    total_bytes: int = Field(0, ge=0, description="Total byte size of input file")


class DatasetPreviewResponse(BaseModel):
    """Inspection receipt summarizing inferred column roles, delimiter, and PII masking status."""

    model_config = ConfigDict(extra="forbid")

    preview_id: str = Field(..., description="Unique sandbox inspection session token")
    filename: str
    file_format: str
    inferred_delimiter: str
    row_count_estimate: int
    detected_columns: list[str]
    column_mappings: list[ColumnMappingItem]
    schema_compliance_ratio: float = Field(..., ge=0.0, le=1.0)
    pii_violations_detected: int = Field(..., ge=0)
    pii_masked_receipt: str


class ExpectationCheckResult(BaseModel):
    """Result of an individual Great Expectations / Pandera statistical contract rule."""

    model_config = ConfigDict(extra="forbid")

    expectation_name: str
    column: str
    status: Literal["passed", "failed", "warning"]
    observed_value: Any
    expected_threshold: str
    details: str


class DatasetContractAuditRequest(BaseModel):
    """Payload to trigger formal Great Expectations 1.x data contract evaluation."""

    model_config = ConfigDict(extra="forbid")

    preview_id: str = Field(..., min_length=3, description="Sandbox preview session identifier")
    bank_id: str = Field(default="bank_alpha", description="Target bank partition identifier")
    column_mapping: dict[str, str] = Field(default_factory=dict, description="User overrides for column mappings")
    quarantine_threshold_pct: float = Field(0.05, ge=0.0, le=1.0, description="Max malformed row ratio before rejection")


class DatasetContractAuditResponse(BaseModel):
    """Official data contract audit receipt determining candidate partition eligibility."""

    model_config = ConfigDict(extra="forbid")

    audit_id: str = Field(..., description="Cryptographic audit run identifier")
    bank_id: str
    status: Literal["passed", "quarantined", "rejected"]
    total_records: int
    passed_records: int
    quarantined_records: int
    contract_checks: list[ExpectationCheckResult]
    overall_compliance_score: float
    fraud_ratio_detected: float
    dirichlet_alpha_estimate: float
    drift_ks_score: float
    quarantine_csv_download_url: str | None = None
    audit_message: str


class DatasetConsortiumEnrollRequest(BaseModel):
    """Payload to assign an audited partition into the local federated learning engine."""

    model_config = ConfigDict(extra="forbid")

    audit_id: str = Field(..., min_length=3, description="Passed audit session identifier")
    target_bank_id: str = Field(default="bank_alpha", description="Target bank node ID")
    allocation_mode: Literal["replace_partition", "append_partition", "guest_node"] = Field(
        default="replace_partition"
    )
    trigger_fl_round: bool = Field(default=False, description="Automatically initiate FL training round upon enrollment")


class DatasetConsortiumEnrollResponse(BaseModel):
    """Receipt confirming local partition assignment and readiness for training."""

    model_config = ConfigDict(extra="forbid")

    enrollment_id: str
    bank_id: str
    node_status: str
    records_enrolled: int
    features_dimension: int
    partition_assigned: str
    next_action_url: str
