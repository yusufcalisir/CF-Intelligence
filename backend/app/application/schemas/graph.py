"""Pydantic schemas for Property Graph Traversal & GraphSAGE Ring Analytics.

Clean Architecture schema definitions for entity-relationship subgraphs, fraud rings,
inductive GraphSAGE embeddings, Cypher querying, streaming edge events, and benchmarks.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Sentinel regex to strip ASCII control characters
_SAFE_TEXT_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _strip_control(value: str) -> str:
    """Remove ASCII control characters."""
    return _SAFE_TEXT_RE.sub("", value)


class GraphClusterItem(BaseModel):
    """Cluster item representing connected component of suspicious entities."""

    model_config = ConfigDict(extra="ignore")

    cluster_id: int = Field(..., description="Zero-indexed cluster ID")
    entity_ids: list[str] = Field(..., description="IDs of entities in cluster")
    size: int = Field(..., description="Number of entities in cluster")


class GraphSearchNodeItem(BaseModel):
    """Search/list entity node item."""

    model_config = ConfigDict(extra="ignore")

    id: str
    display_label: str
    entity_type: str
    bank_id: str
    risk_level: str
    alert_count: int


class GraphEdgeItem(BaseModel):
    """Graph edge response representation."""

    model_config = ConfigDict(extra="ignore")

    id: str
    source: str
    target: str
    label: str = ""
    type: str = "smoothstep"
    animated: bool = False
    confidence: float | None = None
    relationship_type: str | None = None
    style: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)


class GraphEdgesResponse(BaseModel):
    """Response containing sampled or filtered graph relationships."""

    model_config = ConfigDict(extra="ignore")

    total_edges: int
    count: int
    edges: list[GraphEdgeItem]


class GraphStatsResponse(BaseModel):
    """Graph statistical metrics for executive dashboard and health checks."""

    model_config = ConfigDict(extra="ignore")

    total_nodes: int
    total_edges: int
    nodes_by_type: dict[str, int]
    nodes_by_risk: dict[str, int]
    cluster_count: int
    database_backend: str = "Redis (in-memory)"


class GraphResponse(BaseModel):
    """Subgraph centered on an entity with bounded k-hop ego-network."""

    model_config = ConfigDict(extra="ignore")

    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    clusters: list[list[str]] = Field(default_factory=list)
    center_entity_id: str = ""
    depth: int = 2


class MuleRingItem(BaseModel):
    """Detected closed cycle of transactions."""

    model_config = ConfigDict(extra="ignore")

    ring_id: str
    length: int
    entity_ids: list[str]
    banks_involved: list[str]
    is_cross_bank: bool
    risk_score: float
    total_volume: float
    detected_at: datetime | str


class MuleRingDetectionResponse(BaseModel):
    """Detection results for circular transaction rings."""

    model_config = ConfigDict(extra="ignore")

    total_rings: int
    cross_bank_rings: int
    max_risk_score: float
    rings: list[MuleRingItem]


class SmurfingPatternItem(BaseModel):
    """Detected smurfing structuring pattern."""

    model_config = ConfigDict(extra="ignore")

    pattern_id: str = Field(..., description="Unique identifier of detected smurfing pattern")
    pattern_type: str = Field(..., description="Smurfing topology pattern type")
    central_entity_id: str = Field(default="", description="Central mule or aggregator entity ID")
    counterparty_ids: list[str] = Field(default_factory=list, description="List of counterparty entity IDs")
    fan_degree: int = Field(default=1, ge=1, description="Number of converging or dispersing counterparties")
    severity: str = Field(default="medium", description="Smurfing severity level")
    risk_score: float = Field(default=0.0, ge=0.0, le=1000.0, description="Assessed risk score")
    detected_at: str = Field(default="", description="ISO 8601 timestamp of detection")

    @classmethod
    def from_raw(cls, data: dict[str, Any]) -> SmurfingPatternItem:
        hub = data.get("central_entity_id") or data.get("hub_entity_id") or ""
        spokes = data.get("counterparty_ids") or data.get("spoke_entity_ids") or []
        det_at = data.get("detected_at")
        if det_at is not None and not isinstance(det_at, str):
            det_at = det_at.isoformat()
        score = float(data.get("risk_score", 0.0))
        severity = data.get("severity")
        if not severity:
            if score >= 0.8:
                severity = "critical"
            elif score >= 0.6:
                severity = "high"
            elif score >= 0.3:
                severity = "medium"
            else:
                severity = "low"

        return cls(
            pattern_id=str(data.get("pattern_id", "")),
            pattern_type=str(data.get("pattern_type", "fan_in")),
            central_entity_id=str(hub),
            counterparty_ids=[str(s) for s in spokes],
            fan_degree=int(data.get("fan_degree", len(spokes) or 1)),
            severity=severity,
            risk_score=score,
            detected_at=str(det_at or ""),
        )


class SmurfingDetectionResponse(BaseModel):
    """Smurfing and structuring pattern detection results."""

    model_config = ConfigDict(extra="ignore")

    patterns: list[SmurfingPatternItem]
    total_patterns: int
    fan_in_count: int = 0
    fan_out_count: int = 0
    layering_count: int = 0


class CypherQueryRequest(BaseModel):
    """Parameterized Cypher query execution request."""

    model_config = ConfigDict(extra="ignore")

    query: str = Field(..., min_length=3, max_length=2048, description="Cypher query string")
    params: dict[str, Any] = Field(default_factory=dict, description="Query parameters")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Alias parameters")
    read_only: bool = Field(default=True, description="Enforce read-only execution")

    @property
    def query_parameters(self) -> dict[str, Any]:
        return self.parameters or self.params or {}

    @field_validator("query")
    @classmethod
    def sanitize_cypher_query(cls, v: str) -> str:
        return _strip_control(v.strip())


class CypherQueryResponse(BaseModel):
    """Results returned from a Cypher query execution."""

    model_config = ConfigDict(extra="ignore")

    results: list[dict[str, Any]]
    count: int = 0
    database_backend: str = "In-memory"
    success: bool = True
    row_count: int = 0
    execution_time_ms: float = 0.0


class RiskPropagationRequest(BaseModel):
    """Request parameters for graph risk score propagation."""

    model_config = ConfigDict(extra="ignore")

    decay_factor: float = Field(default=0.85, ge=0.0, le=1.0, description="Damping decay factor")


class RiskPropagationResponse(BaseModel):
    """Risk propagation execution summary."""

    model_config = ConfigDict(extra="ignore")

    updated_nodes_count: int = 0
    max_score: float = 0.0
    avg_score_change: float = 0.0
    total_entities_evaluated: int = 0
    high_risk_propagations: int = 0
    iterations_run: int = 0
    max_score_delta: float = 0.0
    converged: bool = True


class CommunityAnalyticsResponse(BaseModel):
    """Detailed risk metrics for an isolated entity community."""

    model_config = ConfigDict(extra="ignore")

    community_id: int
    size: int
    risk_score: float = 0.0
    fraud_node_count: int = 0
    external_edges_count: int = 0
    node_ids: list[str] = Field(default_factory=list)
    fraud_density: float = 0.0
    average_risk: float = 0.0


class TemporalAnomalyResponse(BaseModel):
    """Edge velocity anomaly detected in dynamic sliding window."""

    model_config = ConfigDict(extra="ignore")

    subgraph_id: int = 0
    node_ids: list[str] = Field(default_factory=list)
    edges_count: int = 0
    velocity_score: float = 0.0
    time_window_start: str = ""
    anomaly_id: str = ""
    edge_ids: list[str] = Field(default_factory=list)
    velocity_ratio: float = 0.0
    start_time: str = ""
    end_time: str = ""


class GNNTrainRequest(BaseModel):
    """Request to train GraphSAGE locally on a bank entity graph."""

    model_config = ConfigDict(extra="ignore")

    bank_id: str = Field(default="bank_a", min_length=1, max_length=64, description="Target bank institution")
    epochs: int = Field(default=5, ge=1, le=50, description="Training epochs")
    learning_rate: float = Field(default=0.01, gt=0.0, le=1.0, description="Learning rate")


class GNNTrainResponse(BaseModel):
    """Training metrics from GraphSAGE local training."""

    model_config = ConfigDict(extra="ignore")

    bank_id: str
    loss: float
    num_nodes: int
    num_edges: int
    fraud_nodes: int
    embedding_dim: int
    model_parameters: int


class EntityEmbeddingResponse(BaseModel):
    """Learned structural embedding vector for a node."""

    model_config = ConfigDict(extra="ignore")

    entity_id: str
    embedding: list[float]
    dimension: int


class GNNInferEmbeddingRequest(BaseModel):
    """Request to inductively embed a node."""

    model_config = ConfigDict(extra="ignore")

    entity_id: str = Field(..., min_length=1, max_length=128, description="Target entity ID")
    allow_unseen: bool = Field(default=True, description="Whether to infer for unseen nodes")
    dp_noise: bool = Field(default=False, description="Whether to inject DP noise")


class GNNInferEmbeddingResponse(BaseModel):
    """Inductive node embedding output."""

    model_config = ConfigDict(extra="ignore")

    entity_id: str
    embedding: list[float]
    dimension: int
    is_inductive: bool


class GNNSimilarityRequest(BaseModel):
    """Request for structural cosine similarity matching."""

    model_config = ConfigDict(extra="ignore")

    entity_id: str = Field(..., min_length=1, max_length=128, description="Reference entity ID")
    top_k: int = Field(default=10, ge=1, le=100, description="Maximum neighbors to return")
    threshold: float = Field(default=0.7, ge=0.0, le=1.0, description="Cosine similarity threshold")


class GNNSimilarityResponse(BaseModel):
    """Similar entities matching structural fraud patterns."""

    model_config = ConfigDict(extra="ignore")

    query_entity_id: str
    similar_entities: list[dict[str, Any]]
    count: int


class GNNEmbeddingClusterRequest(BaseModel):
    """Request to cluster entities by embedding space."""

    model_config = ConfigDict(extra="ignore")

    similarity_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    min_cluster_size: int = Field(default=3, ge=2, le=50)


class GNNEmbeddingClusterResponse(BaseModel):
    """Clustering output in embedding space."""

    model_config = ConfigDict(extra="ignore")

    clusters: list[list[str]]
    total_clusters: int


class GNNEmbeddingStatsResponse(BaseModel):
    """Summary statistics for learned GraphSAGE space."""

    model_config = ConfigDict(extra="ignore")

    total_embeddings: int
    embedding_dim: int
    model_parameters: int
    coverage_percentage: float = 100.0


class StreamEdgeEventRequest(BaseModel):
    """Real-time transaction edge payload for Flink processor."""

    model_config = ConfigDict(extra="ignore")

    edge_id: str = Field(..., min_length=1, max_length=128)
    source_id: str = Field(..., min_length=1, max_length=128)
    target_id: str = Field(..., min_length=1, max_length=128)
    rel_type: str = Field(default="TRANSACTS_WITH", max_length=64)
    amount: float = Field(default=0.0, ge=0.0)
    bank_id: str = Field(default="bank_a", max_length=64)


class StreamEdgeEventResponse(BaseModel):
    """Telemetry returned after streaming edge ingestion."""

    model_config = ConfigDict(extra="ignore")

    processed_count: int
    latency_ms: float
    window_size_ms: int
    velocity_anomalies: list[dict[str, Any]] = Field(default_factory=list)
    high_risk_entities: list[str] = Field(default_factory=list)
    processed_at: str


class FlinkStreamStatusResponse(BaseModel):
    """Status telemetry for Apache Flink processor."""

    model_config = ConfigDict(extra="ignore")

    status: str = "RUNNING"
    engine: str = "Apache Flink PyFlink DataStream"
    window_size_ms: int = 60000
    velocity_threshold: float = 5.0
    processed_total_edges: int = 0
    avg_latency_ms: float = 0.0
    subsecond_sla_pass: bool = True
    tracked_entity_count: int = 0


class StreamingGNNTrainStepResponse(BaseModel):
    """Result of online streaming GNN training backpropagation step."""

    model_config = ConfigDict(extra="ignore")

    loss: float
    node_count: int
    edge_count: int
    window_size_minutes: int
    training_applied: bool


class EllipticBenchmarkRequest(BaseModel):
    """Parameters to execute Elliptic Bitcoin dataset benchmark."""

    model_config = ConfigDict(extra="ignore")

    n_samples: int = Field(default=200, ge=10, le=5000)
    random_seed: int = Field(default=42)
    epochs: int = Field(default=3, ge=1, le=50)
    learning_rate: float = Field(default=0.01, gt=0.0, le=1.0)
    save_report: bool = Field(default=False)


class EllipticBenchmarkResponse(BaseModel):
    """Results from the Elliptic graph benchmark."""

    model_config = ConfigDict(extra="ignore")

    dataset: str = "Elliptic Bitcoin Dataset"
    source_type: str = "synthetic_fallback"
    total_nodes: int = 0
    total_edges: int = 0
    illicit_node_count: int = 0
    illicit_rate_percent: float = 0.0
    evaluated_test_nodes: int = 0
    metrics: dict[str, Any] = Field(default_factory=dict)
    report_saved: bool = False
    report_path: str | None = None
    status: str = "completed"
    samples_evaluated: int = 0
    accuracy: float = 0.0
    macro_f1: float = 0.0
    illicit_precision: float = 0.0
    illicit_recall: float = 0.0
    auc_roc: float = 0.0
    epochs_trained: int = 0

    @model_validator(mode="before")
    @classmethod
    def populate_convenience_metrics(cls, data: Any) -> Any:
        if isinstance(data, dict):
            metrics = data.get("metrics", {})
            fed = metrics.get("federated_graph_pipeline", {}) if isinstance(metrics, dict) else {}
            if "auc_roc" not in data or data.get("auc_roc") == 0.0:
                data["auc_roc"] = fed.get("roc_auc", 0.0)
            if "samples_evaluated" not in data or data.get("samples_evaluated") == 0:
                data["samples_evaluated"] = data.get("evaluated_test_nodes", 0)
        return data
