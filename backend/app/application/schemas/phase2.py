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

from pydantic import BaseModel, Field, field_validator

# ── Shared sentinel regex (strips ASCII control chars) ────────────────────────
_SAFE_TEXT_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _strip_control(value: str) -> str:
    """Remove ASCII control characters that have no legitimate use in API text."""
    return _SAFE_TEXT_RE.sub("", value)


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

_CASE_PRIORITIES = Literal["p1_critical", "p2_high", "p3_medium", "p4_low"]
_CASE_STATUSES = Literal[
    "open", "in_review", "escalated", "pending_sar", "closed", "dismissed"
]


class CaseCreateRequest(BaseModel):
    title: str = Field(
        ...,
        min_length=3,
        max_length=256,
        description="Title of the investigation case",
    )
    priority: _CASE_PRIORITIES = Field(  # type: ignore[valid-type]
        "p3_medium",
        description="Priority level: p1_critical | p2_high | p3_medium | p4_low",
    )
    alert_ids: list[str] = Field(
        default_factory=list,
        description="Associated alert IDs (max 200)",
    )

    @field_validator("title")
    @classmethod
    def sanitize_title(cls, v: str) -> str:
        return _strip_control(v)

    @field_validator("alert_ids")
    @classmethod
    def limit_alert_ids(cls, v: list[str]) -> list[str]:
        if len(v) > 200:
            raise ValueError("alert_ids may not contain more than 200 items")
        return v


class CaseNoteRequest(BaseModel):
    author: str = Field(
        "analyst",
        min_length=1,
        max_length=128,
        description="Author identifier",
        pattern=r"^[a-zA-Z0-9_\-\.@]+$",
    )
    content: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description="Note content text",
    )

    @field_validator("content")
    @classmethod
    def sanitize_content(cls, v: str) -> str:
        return _strip_control(v)


class CaseStatusRequest(BaseModel):
    status: _CASE_STATUSES  # type: ignore[valid-type]
    actor: str = Field(
        "analyst",
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_\-\.@]+$",
    )
    supervisor_signature: str | None = Field(None, max_length=512)


class CaseLinkAlertRequest(BaseModel):
    alert_id: str = Field(
        ...,
        min_length=3,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_\-]+$",
    )


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
    ml_prediction: float = Field(0.25, ge=0.0, le=1.0)
    velocity_rules: float = Field(0.15, ge=0.0, le=1.0)
    merchant_reputation: float = Field(0.10, ge=0.0, le=1.0)
    country_risk: float = Field(0.10, ge=0.0, le=1.0)
    device_anomaly: float = Field(0.08, ge=0.0, le=1.0)
    customer_history: float = Field(0.10, ge=0.0, le=1.0)
    previous_alerts: float = Field(0.08, ge=0.0, le=1.0)
    chargeback_history: float = Field(0.07, ge=0.0, le=1.0)
    behavior_anomaly: float = Field(0.07, ge=0.0, le=1.0)

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
        min_length=2,
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
        description="Minimum Jaro-Winkler similarity score [0.0, 1.0]",
    )

    @field_validator("query_name")
    @classmethod
    def sanitize_query(cls, v: str) -> str:
        return _strip_control(v)


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


# ── Evidence & Audit ──────────────────────────

_EVIDENCE_TYPES = Literal[
    "document", "kyc_profile", "ledger_proof", "screenshot", "log_excerpt"
]


class EvidenceRequest(BaseModel):
    evidence_type: _EVIDENCE_TYPES  # type: ignore[valid-type]
    title: str = Field(
        ...,
        min_length=3,
        max_length=256,
        description="Evidence item title",
    )
    file_path: str = Field(
        ...,
        max_length=512,
        description="Relative storage path (no traversal sequences)",
    )
    content: str = Field(
        ...,
        max_length=65536,
        description="Evidence content or summary text (max 64 KiB)",
    )
    uploaded_by: str = Field(
        "analyst",
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_\-\.@]+$",
    )

    @field_validator("file_path")
    @classmethod
    def no_path_traversal(cls, v: str) -> str:
        if ".." in v or v.startswith("/") or "\\" in v:
            raise ValueError(
                "file_path must be a relative path without traversal sequences"
            )
        return v

    @field_validator("title", "content")
    @classmethod
    def sanitize_text(cls, v: str) -> str:
        return _strip_control(v)


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
    investigator: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_\-\.@]+$",
    )
    duration_seconds: float = Field(
        ...,
        ge=0.0,
        le=86400.0,
        description="Session duration in seconds (max 24 h)",
    )
    time_window_end: str = Field(
        ...,
        max_length=32,
        description="ISO 8601 datetime string",
    )


# ── Business Rules (Policy Engine) ────────────

_RULE_ACTIONS = Literal[
    "BLOCK_TRANSACTION",
    "FLAG_HIGH_RISK",
    "REQUIRE_MFA",
    "ALERT_ANALYST",
    "ALLOW",
    "REVIEW",
]


class BusinessRuleCreateRequest(BaseModel):
    rule_name: str = Field(
        ...,
        min_length=3,
        max_length=128,
        pattern=r"^[a-zA-Z0-9 _\-]+$",
        description="Human-readable rule name",
    )
    condition: dict = Field(
        ...,
        description="Condition JSON AST (e.g. {'and': [...]})",
    )
    action: _RULE_ACTIONS = Field(  # type: ignore[valid-type]
        "BLOCK_TRANSACTION",
        description="Action to trigger",
    )
    is_active: bool = True

    @field_validator("rule_name")
    @classmethod
    def sanitize_rule_name(cls, v: str) -> str:
        return _strip_control(v)


class BusinessRuleUpdateRequest(BaseModel):
    rule_name: str | None = Field(
        None,
        min_length=3,
        max_length=128,
        pattern=r"^[a-zA-Z0-9 _\-]+$",
    )
    condition: dict | None = None
    action: _RULE_ACTIONS | None = None  # type: ignore[valid-type]
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
