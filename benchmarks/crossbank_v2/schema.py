"""Artifact and Metadata Schema for CrossBank v2 (CFI-CrossBank-02).

Pydantic v2 schemas enforcing strict typing, threshold provenance, exposure isolation,
and training/information budget accounting.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LowFPRResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_fpr: float = Field(0.001, description="Requested low-FPR operating point (e.g. 0.1%)")
    test_negative_count: int = Field(..., ge=1, description="Number of negative legitimate test samples")
    minimum_nonzero_fpr: float = Field(..., description="1 / test_negative_count (minimum measurable FPR step)")
    is_target_fpr_achievable: bool = Field(..., description="True if test_negative_count >= 1 / target_fpr")
    allowed_fp_at_target: int = Field(..., ge=0, description="Floor(target_fpr * test_negative_count)")


class ThresholdProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threshold_value: float = Field(..., ge=0.0, le=1.0)
    threshold_source_split: str = Field("VALIDATION", description="Split used to select operating threshold")
    target_validation_fpr: float = Field(0.001)
    achieved_validation_fpr: float = Field(..., ge=0.0, le=1.0)
    achieved_test_fpr: float = Field(..., ge=0.0, le=1.0)
    achieved_test_precision: float = Field(..., ge=0.0, le=1.0)
    achieved_test_recall: float = Field(..., ge=0.0, le=1.0)


class ConfusionMatrixCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tp: int = Field(..., ge=0)
    fp: int = Field(..., ge=0)
    tn: int = Field(..., ge=0)
    fn: int = Field(..., ge=0)


class TrainingBudgetAccounting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nominal_effective_passes: float = Field(10.0, ge=0.0)
    optimizer_steps: int = Field(..., ge=0)
    examples_processed: int = Field(..., ge=0)
    effective_passes: float = Field(..., ge=0.0)
    rounds: int = Field(..., ge=0)
    local_epochs_per_round: int = Field(..., ge=0)
    batch_size: int = Field(..., ge=1)
    unique_information_rows: int = Field(default=0, ge=0)
    local_client_view_rows: int = Field(default=0, ge=0)
    budget_parity_classification: str = Field(default="EXACT_NOMINAL_PASS_PARITY")


class InformationBudgetAccounting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unique_training_rows: int = Field(..., ge=0)
    unique_positive_examples: int = Field(..., ge=0)
    unique_entities: int = Field(..., ge=0)
    unique_scenarios: int = Field(..., ge=0)
    unique_institutions: int = Field(..., ge=1)


class EvaluationMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    average_precision: float = Field(..., ge=0.0, le=1.0, description="sklearn.metrics.average_precision_score")
    roc_auc: float = Field(..., ge=0.0, le=1.0, description="sklearn.metrics.roc_auc_score")
    recall_at_validation_fpr: float = Field(..., ge=0.0, le=1.0)
    precision_at_validation_fpr: float = Field(..., ge=0.0, le=1.0)
    f1_at_validation_fpr: float = Field(..., ge=0.0, le=1.0)
    threshold_provenance: ThresholdProvenance
    confusion_matrix: ConfusionMatrixCounts


class ScenarioMetricResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    scenario_title: str
    typology: str
    test_transaction_count: int = Field(..., ge=0)
    test_incident_count: int = Field(..., ge=0)
    test_positive_count: int = Field(..., ge=0)
    detected_transaction_count: int = Field(..., ge=0)
    detected_incident_count: int = Field(..., ge=0)
    transaction_detection_rate: float = Field(..., ge=0.0, le=1.0)
    incident_detection_rate: float = Field(..., ge=0.0, le=1.0)
    synthetic_total_exposure_usd: float = Field(..., ge=0.0)
    synthetic_detected_exposure_usd: float = Field(..., ge=0.0)
    synthetic_missed_exposure_usd: float = Field(..., ge=0.0)


class ConditionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition_id: str
    condition_type: str
    feature_regime: str
    description: str
    training_budget: TrainingBudgetAccounting
    information_budget: InformationBudgetAccounting
    overall_metrics: EvaluationMetrics
    per_bank_metrics: dict[str, EvaluationMetrics]
    scenario_metrics: dict[str, ScenarioMetricResult]


class SplitSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    train_step_range: tuple[int, int]
    val_step_range: tuple[int, int]
    test_step_range: tuple[int, int]
    train_n: int
    train_pos: int
    train_prevalence: float
    val_n: int
    val_pos: int
    val_prevalence: float
    test_n: int
    test_pos: int
    test_prevalence: float
    bank_c_train_pos: int = Field(default=0, ge=0)
    bank_c_val_pos: int = Field(default=0, ge=0)
    bank_c_test_pos: int = Field(default=0, ge=0)
    bank_c_scenario7_train_pos: int = Field(default=0, ge=0)


class CrossBankV2Artifact(BaseModel):
    """Canonical machine-readable artifact for CrossBank v2 evaluation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field("2.1.0")
    benchmark_id: str = Field("CFI-CrossBank-02")
    data_provenance: str = Field("PROJECT_SYNTHETIC")
    federation_type: str = Field("SIMULATED_FEDERATION")
    real_world_validation: str = Field("NONE")
    currency_unit: str = Field("SYNTHETIC_USD")
    cold_start_estimand: str = Field("ZERO_POSITIVE_INSTITUTION")
    preprocessing_regime: str = Field("LOCAL_ONLY_PREPROCESSING")
    initial_model_state_hash: str = Field(default="")
    supersedes_manifest_sha256: str = Field("3aeabcc8f2455ff5e7ab49f0bc1d8e809546786cdfcd2b91ac58eb3cb10ed81f")
    phase2e_candidate_manifest_sha256: str = Field("2d12cdde3a1171cf5de3ba773eb0a64c55318e5d9acf3340bbdd8844fcc4190a")

    # Cryptographic & Protocol Hashes
    protocol_config_hash: str
    experiment_matrix_hash: str
    generator_source_hash: str
    feature_schema_hash: str
    dataset_content_hash: str

    timestamp_utc: str
    seed: int
    is_smoke_run: bool = False

    split_summary: SplitSummary
    low_fpr_resolution: LowFPRResolution
    conditions: dict[str, ConditionResult]

    limitations: list[str] = Field(
        default_factory=lambda: [
            "Project-generated synthetic data: not validated against real banking transaction logs.",
            "Prevalence is intentionally enriched relative to real financial fraud (nominal 1.2% vs ~0.05%).",
            "Monetary amounts represent synthetic testbed exposure, NOT live financial losses or money saved.",
            "Simulated in-memory federation: demonstrates mathematical model parameter aggregation, not physical network mTLS or SGX enclave execution.",
        ]
    )
