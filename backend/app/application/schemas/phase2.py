"""Pydantic schemas for Phase 2 API endpoints.

Request/response models for alerts, cases, entities, graph,
scenarios, and intelligence.
"""

from typing import Any

from pydantic import BaseModel, Field

# ── Alerts ────────────────────────────────────


class AlertResponse(BaseModel):
    id: str
    bank_id: str
    transaction_id: str
    risk_score: float
    severity: str
    status: str
    reason_codes: list[str]
    confidence: float
    involved_entity_ids: list[str]
    created_at: str
    top_features: list[dict] = []
    risk_factors: list[str] = []
    model_confidence: float = 0.0


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


class CaseCreateRequest(BaseModel):
    title: str = Field(..., max_length=256, description="Title of the investigation case")
    priority: str = Field("p3_medium", max_length=64, description="Priority level of the case")
    alert_ids: list[str] = Field(default_factory=list, description="Associated alert IDs")


class CaseNoteRequest(BaseModel):
    author: str = Field("analyst", max_length=128, description="Author identifier")
    content: str = Field(..., max_length=4096, description="Note content text")


class CaseStatusRequest(BaseModel):
    status: str
    actor: str = "analyst"
    supervisor_signature: str | None = None


class CaseLinkAlertRequest(BaseModel):
    alert_id: str


class CaseNoteResponse(BaseModel):
    id: str
    case_id: str
    author: str
    content: str
    created_at: str


class CaseEventResponse(BaseModel):
    event_type: str
    description: str
    actor: str
    timestamp: str
    metadata: dict = {}


class CaseResponse(BaseModel):
    id: str
    title: str
    status: str
    priority: str
    assigned_to: str | None = None
    alert_ids: list[str]
    evidence_ids: list[str] = []
    notes: list[CaseNoteResponse] = []
    timeline: list[CaseEventResponse] = []
    created_at: str
    updated_at: str | None = None
    closed_at: str | None = None
    total_risk_score: float = 0.0
    duration_hours: float | None = None
    is_open: bool = True


class CaseSummaryResponse(BaseModel):
    id: str
    title: str
    status: str
    priority: str
    assigned_to: str | None = None
    alert_count: int
    created_at: str
    is_open: bool = True


# ── Entities ──────────────────────────────────


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
    privacy_hash: str


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
    scenario_type: str
    speed_multiplier: float = Field(default=1.0, ge=0.1, le=10.0)


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


class RiskWeightsUpdateRequest(BaseModel):
    ml_prediction: float = 0.25
    velocity_rules: float = 0.15
    merchant_reputation: float = 0.10
    country_risk: float = 0.10
    device_anomaly: float = 0.08
    customer_history: float = 0.10
    previous_alerts: float = 0.08
    chargeback_history: float = 0.07
    behavior_anomaly: float = 0.07


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
    bank_a_id: str
    bank_b_id: str
    entity_type: str | None = None
    enable_fuzzy: bool = False
    fuzzy_threshold: int = 3


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
    query_name: str
    entity_type: str = "customer"
    threshold: float = 0.70


class EntityFuzzyResolveMatch(BaseModel):
    entity: EntityResponse
    similarity_score: float


class EntityFuzzyResolveResponse(BaseModel):
    matches: list[EntityFuzzyResolveMatch]


# ── Graph-Based Fraud Detection ──


class RiskPropagationRequest(BaseModel):
    decay_factor: float = 0.85


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


# ── Evidence & Audit ──────────────────────────


class EvidenceRequest(BaseModel):
    evidence_type: str  # "document" | "kyc_profile" | "ledger_proof"
    title: str
    file_path: str
    content: str
    uploaded_by: str = "analyst"


class EvidenceResponse(BaseModel):
    id: str
    case_id: str
    evidence_type: str
    title: str
    file_path: str
    content_hash: str
    uploaded_by: str
    uploaded_at: str


class InvestigatorAuditLogResponse(BaseModel):
    id: str
    investigator: str
    action: str
    target_id: str
    timestamp: str
    session_duration_sec: float | None = None
    metadata: dict = {}


class SessionDurationRequest(BaseModel):
    investigator: str
    duration_seconds: float
    time_window_end: str


# ── Business Rules (Policy Engine) ────────────


class BusinessRuleCreateRequest(BaseModel):
    rule_name: str
    condition: dict = Field(..., description="Condition JSON AST (e.g. {'and': [...]})")
    action: str = Field(
        "BLOCK_TRANSACTION", description="Action to trigger (e.g. BLOCK_TRANSACTION)"
    )
    is_active: bool = True


class BusinessRuleUpdateRequest(BaseModel):
    rule_name: str | None = None
    condition: dict | None = None
    action: str | None = None
    is_active: bool | None = None


class BusinessRuleResponse(BaseModel):
    id: str
    rule_name: str
    condition: dict
    action: str
    is_active: bool
    created_at: str
    updated_at: str | None = None


class BusinessRuleTestRequest(BaseModel):
    condition: dict
    transaction: dict


class BusinessRuleTestResponse(BaseModel):
    matches: bool
    message: str


# ── Advanced Explainability (Counterfactuals, Decision Replay, GNNExplainer) ──


class CounterfactualChangeSchema(BaseModel):
    feature: str
    original_value: Any
    remediated_value: Any
    delta_explanation: str


class CounterfactualExplanationResponse(BaseModel):
    alert_id: str
    original_score: float
    remediated_score: float
    is_cleared: bool
    changes: list[CounterfactualChangeSchema] = []
    summary_text: str = ""


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
