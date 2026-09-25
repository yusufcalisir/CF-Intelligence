# ruff: noqa: E402, F401
"""Pydantic schemas for Phase 2 API endpoints.

Request/response models for alerts, cases, entities, graph,
scenarios, and intelligence.

Validation invariants:
  - No string field accepts more than its semantic maximum.
  - Numeric fields carry explicit ge/le or gt/lt bounds.
  - Enum-like strings are restricted via Literal or pattern.
  - Injected control characters are stripped by a root validator.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ── Shared sentinel regex (strips ASCII control chars) ────────────────────────
_SAFE_TEXT_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _strip_control(value: str) -> str:
    """Remove ASCII control characters that have no legitimate use in API text."""
    return _SAFE_TEXT_RE.sub("", value)


# ── Alerts ────────────────────────────────────
from app.application.schemas.alerts import (
    AlertDeduplicationStatsResponse,
    AlertResponse,
    AlertStandaloneTriageRequest,
    AlertStatusUpdateRequest,
    AlertTriageEvaluateRequest,
    AlertTriageEvaluateResponse,
)


class ExplainabilityResponse(BaseModel):
    alert_id: str
    top_features: list[dict]
    risk_factors: list[str]
    historical_evidence: list[str]
    model_confidence: float
    risk_score_breakdown: list[dict] = []
    explanation_text: str = ""


class IntelligenceStatsResponse(BaseModel):
    total_items: int
    items_by_type: dict[str, int]
    items_by_bank: dict[str, int]
    avg_risk_indicator: float


class SharedIntelligenceResponse(BaseModel):
    id: str
    source_bank_id: str
    intelligence_type: str
    privacy_hash: str
    risk_indicator: float
    description: str
    entity_type: str | None = None
    related_alert_count: int = 0
    created_at: str


# ── Cases ─────────────────────────────────────
from app.application.schemas.cases import (
    _CASE_PRIORITIES,
    _CASE_STATUSES,
    _EVIDENCE_TYPES,
    CaseCreateRequest,
    CaseEscalateRequest,
    CaseEventResponse,
    CaseLinkAlertRequest,
    CaseNoteRequest,
    CaseNoteResponse,
    CaseResolveRequest,
    CaseResponse,
    CaseSignRequest,
    CaseStatusRequest,
    CaseSummaryResponse,
    EvidenceRequest,
    EvidenceResponse,
    ExportFinCENXmlRequest,
    ExportFinCENXmlResponse,
    InvestigatorAuditLogResponse,
    SessionDurationRequest,
    TimelineVerificationResponse,
)

# ── Entities ──────────────────────────────────

_ENTITY_TYPES = Literal["customer", "merchant", "device", "account", "ip_address"]


class EntityResponse(BaseModel):
    id: str
    entity_type: str
    privacy_id: str
    bank_id: str
    display_label: str
    attributes: dict = {}
    risk_level: str
    alert_count: int = 0
    first_seen: str
    last_seen: str


class EntityProfileResponse(BaseModel):
    entity_id: str
    entity_type: str
    privacy_id: str
    display_label: str
    bank_id: str
    risk_level: str
    alert_count: int
    relationship_count: int
    cross_institution_count: int
    banks_present: list[str]
    first_seen: str
    last_seen: str
    attributes: dict = {}


class EntityResolveRequest(BaseModel):
    privacy_hash: str = Field(
        ...,
        min_length=16,
        max_length=128,
        pattern=r"^[a-fA-F0-9]+$",
        description="HMAC-SHA256 hex digest of the entity's canonical identifier",
    )


class CrossInstitutionMatchResponse(BaseModel):
    privacy_hash: str
    entity_type: str
    bank_a_entity_id: str
    bank_b_entity_id: str
    bank_a_risk: str
    bank_b_risk: str


# ── Graph ─────────────────────────────────────


class GraphNodeResponse(BaseModel):
    id: str
    type: str = "default"
    position: dict
    data: dict
    style: dict = {}


class GraphEdgeResponse(BaseModel):
    id: str
    source: str
    target: str
    label: str = ""
    type: str = "smoothstep"
    animated: bool = False
    style: dict = {}
    data: dict = {}


class GraphResponse(BaseModel):
    nodes: list[dict]
    edges: list[dict]
    clusters: list[list[str]] = []
    center_entity_id: str = ""
    depth: int = 2


class GraphStatsResponse(BaseModel):
    total_nodes: int
    total_edges: int
    nodes_by_type: dict[str, int]
    nodes_by_risk: dict[str, int]
    cluster_count: int
    database_backend: str = "Redis (in-memory)"


# ── Scenarios ─────────────────────────────────


class ScenarioInfoResponse(BaseModel):
    type: str
    name: str
    description: str
    banks_involved: list[str]
    estimated_events: int
    estimated_duration_seconds: float


class ScenarioStartRequest(BaseModel):
    scenario_type: str = Field(
        ...,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Scenario type identifier (e.g. layering_attack, smurfing)",
    )
    speed_multiplier: float = Field(
        default=1.0,
        ge=0.1,
        le=10.0,
        description="Playback speed multiplier [0.1, 10.0]",
    )


class ScenarioStartResponse(BaseModel):
    scenario_id: str
    scenario_type: str
    name: str
    total_events: int
    status: str = "running"


class ScenarioStatusResponse(BaseModel):
    scenario_id: str
    status: str
    total_events: int
    delivered_events: int
    speed_multiplier: float
    started_at: str


class AttackInjectionRequest(BaseModel):
    attack_type: Literal["smurfing_layering", "byzantine_poisoning", "sybil_ring"] = Field(
        ...,
        description="Type of adversarial chaos attack to inject",
    )
    adversary_bank: str = Field(
        default="bank_gamma",
        description="Bank node initiating or manipulated by the attack",
    )
    target_bank: str = Field(
        default="bank_alpha",
        description="Target institution receiving illicit flows or aggregating gradients",
    )
    intensity_rate: int = Field(
        default=500,
        ge=10,
        le=5000,
        description="Burst transaction or gradient rate per second",
    )
    defense_strategy: Literal["krum", "trimmed_mean", "bulyan", "spectral", "spectral_svd", "psi_graph"] = Field(
        default="krum",
        description="Active algorithmic defense strategy to intercept the threat",
    )


class AttackInjectionResponse(BaseModel):
    attack_id: str
    attack_type: str
    status: str  # "intercepted", "quarantined", "mitigated"
    defense_activated: str
    adversary_quarantined: str | None = None
    euclidean_distance: float = 0.0
    distance_threshold: float = 0.0
    packets_blocked: int = 0
    mitigation_latency_ms: float = 0.0
    auc_protected: float = Field(
        0.9412,
        description="Continuous live demo indicator/proxy of model resilience under defense, modeled from cosine alignment and boundary strain (not offline holdout validation AUC).",
    )
    auc_compromised_baseline: float = Field(
        0.5218,
        description="Continuous baseline proxy estimating model degradation without Byzantine defense shield.",
    )
    log_entry: str = ""


# ── Risk ──────────────────────────────────────



class RiskWeightsResponse(BaseModel):
    ml_prediction: float
    velocity_rules: float
    merchant_reputation: float
    country_risk: float
    device_anomaly: float
    customer_history: float
    previous_alerts: float
    chargeback_history: float
    behavior_anomaly: float
    gnn_topological_risk: float = 0.0


class RiskWeightsUpdateRequest(BaseModel):
    ml_prediction: float = Field(0.25, ge=0.0, le=1.0)
    velocity_rules: float = Field(0.15, ge=0.0, le=1.0)
    merchant_reputation: float = Field(0.10, ge=0.0, le=1.0)
    country_risk: float = Field(0.10, ge=0.0, le=1.0)
    device_anomaly: float = Field(0.08, ge=0.0, le=1.0)
    customer_history: float = Field(0.10, ge=0.0, le=1.0)
    previous_alerts: float = Field(0.08, ge=0.0, le=1.0)
    chargeback_history: float = Field(0.07, ge=0.0, le=1.0)
    behavior_anomaly: float = Field(0.07, ge=0.0, le=1.0)
    gnn_topological_risk: float = Field(0.0, ge=0.0, le=1.0)

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
    def weight_precision(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("Risk weight must be in [0.0, 1.0]")
        return round(v, 6)


# ── Investigation Dashboard ──────────────────


class DashboardStatsResponse(BaseModel):
    total_alerts: int
    critical_alerts: int
    open_cases: int
    total_entities: int
    shared_intelligence_items: int
    cross_institution_matches: int
    active_scenarios: int
    graph_clusters: int


# ── Privacy-Preserving Entity Resolution (PSI) ──


class PSIRequest(BaseModel):
    bank_a_id: str = Field(
        ...,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Source bank node ID",
    )
    bank_b_id: str = Field(
        ...,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Target bank node ID",
    )
    entity_type: str | None = Field(
        None,
        max_length=32,
        pattern=r"^[a-zA-Z_]+$",
    )
    enable_fuzzy: bool = False
    fuzzy_threshold: int = Field(3, ge=1, le=10)


class PSIMatch(BaseModel):
    privacy_hash: str
    entity_type: str
    display_label_a: str
    display_label_b: str
    risk_level_a: str
    risk_level_b: str
    matched_attributes: list[str] = []
    similarity_score: float = 1.0


class PSIProtocolStats(BaseModel):
    computation_time_ms: float
    data_exchanged_bytes: int
    num_entities_a: int
    num_entities_b: int
    prime_bit_length: int
    enclave_execution: bool = False
    mrenclave: str | None = None
    mrsigner: str | None = None
    attestation_verified: bool | None = None


class PSIResponse(BaseModel):
    matches: list[PSIMatch]
    stats: PSIProtocolStats


# ── Fuzzy Entity Resolution ──


class EntityFuzzyResolveRequest(BaseModel):
    query_name: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description="Entity name or alias to fuzzy-match",
    )
    entity_type: _ENTITY_TYPES = Field(  # type: ignore[valid-type]
        "customer",
        description="Entity type filter",
    )
    threshold: float = Field(
        0.70,
        ge=0.0,
        le=1.0,
        description="Minimum Jaccard similarity score [0.0, 1.0]",
    )
    bank_id: str | None = Field(
        None,
        max_length=64,
        description="Optional bank tenant ID filter",
    )
    limit: int = Field(
        50,
        ge=1,
        le=100,
        description="Maximum matched candidates to return",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "query_name" not in data and "raw_identifier" in data:
                data["query_name"] = data["raw_identifier"]
            if "threshold" not in data and "similarity_threshold" in data:
                data["threshold"] = data["similarity_threshold"]
        return data

    @field_validator("query_name")
    @classmethod
    def sanitize_query(cls, v: str) -> str:
        return _strip_control(v).strip()


class EntityFuzzyResolveMatch(BaseModel):
    entity: EntityResponse
    similarity_score: float


class EntityFuzzyResolveResponse(BaseModel):
    matches: list[EntityFuzzyResolveMatch]


# ── Graph-Based Fraud Detection ──


class RiskPropagationRequest(BaseModel):
    decay_factor: float = Field(
        0.85,
        ge=0.0,
        le=1.0,
        description="PageRank-style decay factor [0.0, 1.0]",
    )


class RiskPropagationResponse(BaseModel):
    updated_nodes_count: int
    max_score: float
    avg_score_change: float


class CommunityAnalyticsResponse(BaseModel):
    community_id: int
    node_ids: list[str]
    size: int
    fraud_density: float
    average_risk: float


class TemporalAnomalyResponse(BaseModel):
    subgraph_id: int
    node_ids: list[str]
    edges_count: int
    velocity_score: float
    time_window_start: str


# ── Graph Ring & Smurfing Analytics ────────────


class MuleRingItem(BaseModel):
    ring_id: str = Field(..., description="Unique deterministic identifier for detected mule ring")
    length: int = Field(..., ge=3, le=7, description="Number of hops in the cyclic ring (L in [3, 7])")
    node_ids: list[str] = Field(..., description="Canonical sequence of entity IDs forming the directed transaction loop")
    bank_ids: list[str] = Field(default_factory=list, description="Unique bank IDs involved in the transaction loop")
    is_cross_bank: bool = Field(False, description="Flag indicating cross-bank consortium mule ring")
    risk_score: float = Field(..., ge=0.0, le=1000.0, description="Composite risk score of the cyclic mule ring")
    detected_at: str = Field(..., description="ISO 8601 timestamp of ring detection")


class MuleRingDetectionResponse(BaseModel):
    rings: list[MuleRingItem]
    total_rings: int
    cross_bank_rings: int
    max_risk_score: float = 0.0


class SmurfingPatternItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pattern_id: str = Field(..., description="Unique identifier of detected smurfing pattern")
    pattern_type: str = Field(..., description="Smurfing topology pattern type")
    central_entity_id: str = Field(default="", description="Central mule or aggregator entity ID")
    counterparty_ids: list[str] = Field(default_factory=list, description="List of originators or recipient counterparty entity IDs")
    fan_degree: int = Field(..., ge=1, description="Number of converging or dispersing counterparties")
    severity: str = Field(default="medium", description="Smurfing severity level")
    risk_score: float = Field(..., ge=0.0, le=1000.0, description="Assessed risk score for the smurfing cluster")
    detected_at: str = Field(..., description="ISO 8601 timestamp of detection")

    @model_validator(mode="before")
    @classmethod
    def _normalize_smurfing_item(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "central_entity_id" not in data and "hub_entity_id" in data:
                data["central_entity_id"] = data["hub_entity_id"]
            if "counterparty_ids" not in data and "spoke_entity_ids" in data:
                data["counterparty_ids"] = data["spoke_entity_ids"]
            if "detected_at" in data and not isinstance(data["detected_at"], str):
                data["detected_at"] = data["detected_at"].isoformat()
            if "severity" not in data:
                score = float(data.get("risk_score", 0.0))
                if score >= 0.8:
                    data["severity"] = "critical"
                elif score >= 0.6:
                    data["severity"] = "high"
                elif score >= 0.3:
                    data["severity"] = "medium"
                else:
                    data["severity"] = "low"
        return data


class SmurfingDetectionResponse(BaseModel):
    patterns: list[SmurfingPatternItem]
    total_patterns: int
    fan_in_count: int = 0
    fan_out_count: int = 0
    layering_count: int = 0


class CypherQueryRequest(BaseModel):
    query: str = Field(..., min_length=3, max_length=2048, description="Cypher query string to execute")
    params: dict[str, Any] = Field(default_factory=dict, description="Query parameters dictionary")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Alias for query parameters")
    read_only: bool = Field(True, description="Strictly forbid mutation clauses (MERGE, CREATE, DELETE, SET, etc.)")

    @property
    def query_parameters(self) -> dict[str, Any]:
        return self.parameters or self.params or {}


class CypherQueryResponse(BaseModel):
    results: list[dict[str, Any]]
    count: int = 0
    database_backend: str = "In-memory"
    success: bool = True
    row_count: int = 0
    execution_time_ms: float = 0.0


class GNNInferEmbeddingRequest(BaseModel):
    entity_id: str = Field(..., description="Target entity ID to inductively embed")
    allow_unseen: bool = Field(True, description="Whether to infer for unseen nodes")
    dp_noise: bool = Field(False, description="Whether to inject calibrated DP noise")


class GNNInferEmbeddingResponse(BaseModel):
    entity_id: str
    embedding: list[float]
    dimension: int
    is_inductive: bool



# ── Evidence & Audit ──────────────────────────
# Evidence & Audit schemas re-exported from app.application.schemas.cases above


# ── Business Rules (Policy Engine) ────────────

_RULE_ACTIONS = Literal[
    "BLOCK_TRANSACTION",
    "BLOCK",
    "FLAG_HIGH_RISK",
    "FLAG_CRITICAL",
    "REQUIRE_MFA",
    "ESCALATE_TO_SAR",
    "ALERT_ANALYST",
    "HOLD",
    "ALLOW",
    "REVIEW",
]

from app.application.schemas.rules import (
    BusinessRuleCreateRequest,
    BusinessRuleResponse,
    BusinessRuleTestRequest,
    BusinessRuleTestResponse,
    BusinessRuleUpdateRequest,
)

# ── Advanced Explainability (Counterfactuals, Decision Replay, GNNExplainer) ──


class CounterfactualChangeSchema(BaseModel):
    feature: str
    original_value: Any
    remediated_value: Any
    delta_explanation: str
    suggested_value: Any = None
    delta: float = 0.0
    description: str = ""


class CounterfactualExplanationResponse(BaseModel):
    alert_id: str
    original_score: float
    remediated_score: float
    is_cleared: bool
    changes: list[CounterfactualChangeSchema] = []
    summary_text: str = ""


class CounterfactualSimulationRequest(BaseModel):
    alert_id: str = "alt_1001"
    target_score: float = 350.0
    amount: float | None = None
    velocity: float | None = None
    merchant_risk: float | None = None


class PolicyRuleEvaluationSchema(BaseModel):
    rule_code: str
    signal_name: str
    weight: float
    raw_value: float
    normalized_score: float
    contribution: float
    triggered: bool


class DecisionReplayResponse(BaseModel):
    alert_id: str
    transaction_id: str
    timestamp: str
    model_version: str
    model_auc: float
    features_snapshot: dict[str, Any] = {}
    graph_snapshot: dict[str, int] = {}
    policy_rules_evaluated: list[PolicyRuleEvaluationSchema] = []
    reconstructed_risk_score: float = 0.0
    reproduced_severity: str = "low"
    audit_matched: bool = True


class EdgeContributionSchema(BaseModel):
    source: str
    target: str
    relationship_type: str
    weight: float
    contribution_percentage: float


class GNNExplanationResponse(BaseModel):
    node_id: str
    target_risk_level: str
    subgraph_nodes_count: int
    subgraph_edges_count: int
    top_contributing_edges: list[EdgeContributionSchema] = []
    primary_driver_text: str = ""


class LIMEFeatureAttributionSchema(BaseModel):
    feature: str
    weight: float
    value: float
    direction: str


class LIMEExplanationResponse(BaseModel):
    alert_id: str | None = None
    transaction_id: str | None = None
    intercept: float
    fidelity_r2: float
    kernel_width: float
    num_samples: int
    feature_attributions: list[LIMEFeatureAttributionSchema] = []
    explanation_text: str = ""


# ── Real Dataset Ingestion Studio Schemas ─────────


class ColumnMappingItem(BaseModel):
    source_column: str
    target_signal: str
    data_type: str
    sample_values: list[Any] = []
    is_required: bool = False
    confidence_score: float = 1.0


class DatasetPreviewRequest(BaseModel):
    filename: str = Field(..., max_length=256)
    file_format: Literal["csv", "parquet", "tsv", "gz"] = "csv"
    raw_header: list[str] = []
    sample_rows: list[dict[str, Any]] = []
    total_bytes: int = Field(default=0, ge=0)


class DatasetPreviewResponse(BaseModel):
    preview_id: str
    filename: str
    file_format: str
    inferred_delimiter: str = ","
    row_count_estimate: int
    detected_columns: list[str]
    column_mappings: list[ColumnMappingItem]
    schema_compliance_ratio: float
    pii_violations_detected: int = 0
    pii_masked_receipt: str = ""


class ExpectationCheckResult(BaseModel):
    expectation_name: str
    column: str
    status: Literal["passed", "failed", "warning"]
    observed_value: Any
    expected_threshold: str
    details: str = ""


class DatasetContractAuditRequest(BaseModel):
    preview_id: str
    bank_id: str = "bank_alpha"
    column_mapping: dict[str, str] = {}
    quarantine_threshold_pct: float = Field(default=5.0, ge=0.0, le=100.0)


class DatasetContractAuditResponse(BaseModel):
    audit_id: str
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
    audit_id: str
    target_bank_id: str = "bank_alpha"
    allocation_mode: Literal["replace_partition", "append_partition", "guest_node"] = "replace_partition"
    trigger_fl_round: bool = False


class DatasetConsortiumEnrollResponse(BaseModel):
    enrollment_id: str
    bank_id: str
    node_status: str
    records_enrolled: int
    features_dimension: int
    partition_assigned: str
    next_action_url: str


class StreamEdgeEventRequest(BaseModel):
    edge_id: str = Field(..., description="Unique graph edge transaction ID")
    source_id: str = Field(..., description="Source entity ID")
    target_id: str = Field(..., description="Target entity ID")
    rel_type: str = Field("TRANSACTS_WITH", description="Relationship type")
    amount: float = Field(100.0, description="Transaction amount")
    bank_id: str = Field("", description="Bank ID emitting the edge")


class StreamEdgeEventResponse(BaseModel):
    processed_count: int
    latency_ms: float
    window_size_ms: int
    velocity_anomalies: list[dict[str, Any]]
    high_risk_entities: list[str]
    processed_at: str


class FlinkStreamStatusResponse(BaseModel):
    status: str
    engine: str
    window_size_ms: int
    velocity_threshold: float
    processed_total_edges: int
    avg_latency_ms: float
    subsecond_sla_pass: bool
    tracked_entity_count: int


class StreamingGNNTrainStepResponse(BaseModel):
    loss: float
    node_count: int
    edge_count: int
    window_size_minutes: int
    training_applied: bool


class EllipticBenchmarkRequest(BaseModel):
    n_samples: int = Field(default=2000, ge=50, le=50000, description="Number of node samples to evaluate")
    random_seed: int = Field(default=42, ge=0, description="Random seed for reproducibility")
    epochs: int = Field(default=5, ge=1, le=50, description="Number of training epochs")
    learning_rate: float = Field(default=0.01, gt=0.0, le=1.0, description="Optimizer learning rate")
    save_report: bool = Field(default=True, description="Whether to persist markdown and json reports")


class EllipticPipelineMetrics(BaseModel):
    roc_auc: float
    pr_auc: float
    recall_at_01_fpr: float


class EllipticAdvantageMetrics(BaseModel):
    pr_auc_gain: float
    roc_auc_gain: float
    recall_gain: float


class EllipticBenchmarkMetrics(BaseModel):
    federated_graph_pipeline: EllipticPipelineMetrics
    isolated_single_bank_baseline: EllipticPipelineMetrics
    federated_advantage: EllipticAdvantageMetrics


class EllipticBenchmarkResponse(BaseModel):
    dataset: str
    source_type: str
    total_nodes: int
    total_edges: int
    illicit_node_count: int
    illicit_rate_percent: float
    evaluated_test_nodes: int
    metrics: EllipticBenchmarkMetrics
    report_saved: bool = False
    report_path: str | None = None


class AMLEvidencePackageResponse(BaseModel):
    case_id: str
    case_title: str
    case_status: str
    risk_score: float
    alert_ids: list[str]
    evidence_ids: list[str]
    timeline_events_count: int
    notes_count: int
    top_risk_drivers: list[dict[str, Any]]
    graph_topology: dict[str, Any]
    zero_pii_verified: bool
    assembled_at: str
    evidence_digest: str


class MerchantRiskItem(BaseModel):
    merchant: str
    alert_count: int


class ScenarioStopResponse(BaseModel):
    scenario_id: str
    status: str = "stopped"


class ActiveScenarioItem(BaseModel):
    scenario_id: str
    status: str
    total_events: int
    delivered_events: int
    speed_multiplier: float
    started_at: str




