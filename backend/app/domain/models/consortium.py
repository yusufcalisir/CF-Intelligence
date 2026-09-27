"""Domain models for Cross-Bank Synthetic Benchmark (CFI-CrossBank-01).

Defines institutions, multi-hop financial crime scenarios, transaction schemas,
and evaluation metrics for multi-bank consortium benchmarking.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class InstitutionType(StrEnum):
    """Institutional banking profile category."""
    RETAIL = "retail"
    COMMERCIAL = "commercial"
    CROSS_BORDER = "cross_border"
    DIGITAL_CHALLENGER = "digital_challenger"


class ConsortiumNode(BaseModel):
    """Consortium member institution participating in federated learning."""
    bank_id: str = Field(..., description="Unique bank node identifier (e.g. 'bank_a')")
    name: str = Field(..., description="Human-readable bank name (e.g. 'Bank Alpha')")
    institution_type: InstitutionType = Field(
        InstitutionType.RETAIL, description="Institutional business archetype"
    )
    volume_share: float = Field(..., ge=0.0, le=1.0, description="Proportion of total consortium transaction volume")
    account_count: int = Field(..., ge=1, description="Number of customer accounts under management")
    positive_prevalence: float = Field(0.0, ge=0.0, le=1.0, description="Historical fraud/money laundering prevalence")
    is_zero_positive: bool = Field(False, description="Flag indicating extreme cold-start with zero historical fraud examples")
    public_key: str | None = Field(None, description="PQC / Curve25519 node public identity key")


class ConsortiumScenarioType(StrEnum):
    """The 7 canonical cross-bank synthetic fraud topologies."""
    SCENARIO_1_LOCALIZED = "scenario_1_localized"
    SCENARIO_2_TWO_BANK_LAYERING = "scenario_2_two_bank_layering"
    SCENARIO_3_THREE_BANK_CYCLE = "scenario_3_three_bank_cycle"
    SCENARIO_4_BEHAVIOR_SHIFTING = "scenario_4_behavior_shifting"
    SCENARIO_5_NON_IID_PROFILES = "scenario_5_non_iid_profiles"
    SCENARIO_6_EXTREME_RARITY = "scenario_6_extreme_rarity"
    SCENARIO_7_ZERO_POSITIVE_TRANSFER = "scenario_7_zero_positive_transfer"


class ScenarioDefinition(BaseModel):
    """Specification of a ground-truth synthetic financial crime scenario."""
    scenario_id: str = Field(..., description="Scenario identifier (e.g. 'SCENARIO_3')")
    scenario_type: ConsortiumScenarioType = Field(..., description="Canonical typology enum")
    title: str = Field(..., description="Descriptive scenario title")
    description: str = Field(..., description="Detailed narrative and operational topology")
    risk_typology: str = Field(..., description="Regulatory typology classification")
    participating_banks: list[str] = Field(..., min_length=1, description="List of involved bank IDs")
    hop_count: int = Field(1, ge=1, description="Number of transfer hops in laundering chain")
    expected_isolated_vulnerability: str = Field(..., description="Explanation of why isolated silos fail")
    collaborative_advantage: str = Field(..., description="Empirical mechanism of federated detection uplift")


class CrossBankTransaction(BaseModel):
    """Transaction record within the multi-bank synthetic network."""
    transaction_id: str = Field(..., description="Deterministic transaction UUID")
    step: int = Field(..., ge=0, description="Discrete simulation timestamp (hours)")
    source_bank: str = Field(..., description="Originating bank node ID")
    target_bank: str = Field(..., description="Destination bank node ID")
    source_account: str = Field(..., description="Originating account identifier")
    target_account: str = Field(..., description="Destination account identifier")
    amount: float = Field(..., gt=0.0, description="Monetary transfer value in base currency")
    payment_rail: str = Field("SEPA_INSTANT", description="Payment rail (SEPA_INSTANT, SWIFT, CHAPS, ACH, WIRE)")
    is_laundering: int = Field(0, ge=0, le=1, description="Ground truth indicator (1 = illicit/laundering, 0 = benign)")
    scenario_id: str | None = Field(None, description="Scenario ID if part of an illicit ring")
    hop_index: int | None = Field(None, description="Hop position in multi-hop chain (0, 1, 2...)")
    features: dict[str, float] = Field(default_factory=dict, description="Pre-computed tabular features")


class ScenarioMetrics(BaseModel):
    """Comparative benchmark metrics for a single scenario."""
    scenario_id: str
    scenario_name: str
    isolated_detection_rate: float = Field(..., ge=0.0, le=1.0)
    federated_detection_rate: float = Field(..., ge=0.0, le=1.0)
    pooled_detection_rate: float = Field(..., ge=0.0, le=1.0)
    delta_detection_rate: float = Field(..., description="Federated - Isolated detection uplift")
    isolated_pr_auc: float = Field(0.0, ge=0.0, le=1.0)
    federated_pr_auc: float = Field(0.0, ge=0.0, le=1.0)
    delta_pr_auc: float = Field(0.0)
    isolated_recall_at_01_fpr: float = Field(0.0, ge=0.0, le=1.0)
    federated_recall_at_01_fpr: float = Field(0.0, ge=0.0, le=1.0)
    rounds_to_detection: int = Field(1, ge=1)
    participating_institutions: int = Field(3, ge=1)


class ConsortiumBenchmarkResult(BaseModel):
    """Complete serialized benchmark result for CFI-CrossBank-01."""
    benchmark_id: str = Field("CFI-CrossBank-01", description="Unique benchmark registration ID")
    timestamp: str = Field(..., description="ISO 8601 execution timestamp")
    total_transactions: int = Field(..., ge=1)
    total_accounts: int = Field(..., ge=1)
    scenarios_evaluated: int = Field(7)
    overall_isolated_detection_rate: float = Field(..., ge=0.0, le=1.0)
    overall_federated_detection_rate: float = Field(..., ge=0.0, le=1.0)
    overall_pooled_detection_rate: float = Field(..., ge=0.0, le=1.0)
    overall_delta_detection_rate: float = Field(..., description="Mean collaborative uplift")
    scenarios: dict[str, ScenarioMetrics] = Field(default_factory=dict)
    institution_metrics: dict[str, dict[str, float]] = Field(default_factory=dict)
    zero_positive_transfer_recall: float = Field(0.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)
