"""Cross-Border Corporate UBO & Heterogeneous Graph Modeling Schemas.

Pydantic v2 schemas for multi-tiered corporate ownership, Ultimate Beneficial Owner (UBO)
resolution, circular ownership cycle detection, nominee director syndicates, and
heterogeneous graph representation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import UBOAnomalyType, UBONodeType, UBORelationType


class UBONodeCreate(BaseModel):
    """Payload to create or register an entity/person node in the corporate graph."""

    model_config = ConfigDict(populate_by_name=True)

    node_id: str = Field(
        default_factory=lambda: f"UBO-NODE-{uuid.uuid4().hex[:8].upper()}",
        description="Unique identifier for the corporate node (e.g. registration number or hash).",
    )
    node_type: UBONodeType = Field(
        ...,
        description="Heterogeneous entity classification in the corporate network.",
    )
    name: str = Field(
        ...,
        min_length=2,
        max_length=255,
        description="Legal company name or natural person full name.",
    )
    jurisdiction: str = Field(
        default="DE",
        min_length=2,
        max_length=3,
        description="ISO 3166-1 alpha-2 or alpha-3 country code of incorporation / citizenship.",
    )
    registration_number: str | None = Field(
        default=None,
        description="Official commercial register or company house number (e.g. HRB 12345).",
    )
    incorporation_date: str | None = Field(
        default=None,
        description="ISO 8601 date of incorporation or date of birth.",
    )
    is_pep: bool = Field(
        default=False,
        description="Indicates whether the individual is a Politically Exposed Person.",
    )
    is_sanctioned: bool = Field(
        default=False,
        description="Indicates whether the individual or entity appears on global sanction lists.",
    )
    is_shell_suspect: bool = Field(
        default=False,
        description="Flags entity as a suspected letterbox / shell entity lacking economic substance.",
    )
    nominal_capital_eur: float | None = Field(
        default=None,
        ge=0.0,
        description="Declared share capital in EUR.",
    )
    registered_address: str | None = Field(
        default=None,
        description="Official registered office address.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional custom attributes (LEI, tax ID, offshore registry links).",
    )


class UBONodeResponse(BaseModel):
    """Response envelope for a corporate network node."""

    model_config = ConfigDict(populate_by_name=True)

    node_id: str
    node_type: UBONodeType
    name: str
    jurisdiction: str
    registration_number: str | None = None
    incorporation_date: str | None = None
    is_pep: bool = False
    is_sanctioned: bool = False
    is_shell_suspect: bool = False
    nominal_capital_eur: float | None = None
    registered_address: str | None = None
    risk_score: float = 0.0
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class UBORelationCreate(BaseModel):
    """Payload to create a directed edge representing ownership, directorship, or control."""

    model_config = ConfigDict(populate_by_name=True)

    source_id: str = Field(
        ...,
        description="Origin node ID (owner, shareholder, parent holding, or director).",
    )
    target_id: str = Field(
        ...,
        description="Destination node ID (subsidiary, controlled entity, or operated account).",
    )
    relation_type: UBORelationType = Field(
        ...,
        description="Nature of the corporate relationship.",
    )
    ownership_percentage: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Equity / shares ownership percentage (0.0 to 100.0).",
    )
    voting_percentage: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Voting rights percentage (0.0 to 100.0).",
    )
    is_nominee: bool = Field(
        default=False,
        description="Flags the relationship as nominee directorship or fiduciary holding.",
    )
    effective_date: str | None = Field(
        default=None,
        description="ISO 8601 commencement date of the ownership or directorship.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("target_id")
    @classmethod
    def prevent_self_loops(cls, v: str, info: Any) -> str:
        """Prevent self-directed directorship or ownership loops at edge creation."""
        source = info.data.get("source_id")
        if source and source == v:
            raise ValueError("Direct self-loop edges are invalid in corporate ownership graphs.")
        return v


class UBORelationResponse(BaseModel):
    """Response envelope for a corporate network relationship edge."""

    model_config = ConfigDict(populate_by_name=True)

    relation_id: str = Field(
        default_factory=lambda: f"UBO-REL-{uuid.uuid4().hex[:8].upper()}"
    )
    source_id: str
    target_id: str
    relation_type: UBORelationType
    ownership_percentage: float
    voting_percentage: float
    is_nominee: bool = False
    effective_date: str | None = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )


class UBOGraphIngestRequest(BaseModel):
    """Batch ingestion payload for corporate ownership networks."""

    model_config = ConfigDict(populate_by_name=True)

    nodes: list[UBONodeCreate] = Field(
        default_factory=list,
        description="List of corporate entities and natural persons to register.",
    )
    relations: list[UBORelationCreate] = Field(
        default_factory=list,
        description="List of directed ownership and control relations.",
    )


class UBOGraphIngestResponse(BaseModel):
    """Response summary for batch corporate graph ingestion."""

    nodes_created: int
    relations_created: int
    tenant_id: str
    message: str = "Corporate network ingested successfully."


class EffectiveBeneficialOwner(BaseModel):
    """Calculated Ultimate Beneficial Owner (UBO) for a target legal entity."""

    model_config = ConfigDict(populate_by_name=True)

    person_id: str
    name: str
    jurisdiction: str
    direct_ownership_percent: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Direct equity held in the target entity.",
    )
    indirect_ownership_percent: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Compounded indirect equity held through intermediary parent holding companies.",
    )
    total_effective_percentage: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Total cumulative beneficial ownership (direct + indirect).",
    )
    meets_statutory_threshold: bool = Field(
        ...,
        description="True if effective ownership exceeds statutory threshold (e.g. >= 25% under EU AMLD).",
    )
    ownership_paths: list[list[str]] = Field(
        default_factory=list,
        description="Chain of node IDs tracing ownership from the natural person to the target entity.",
    )
    is_pep: bool = False
    is_sanctioned: bool = False


class UBOCalculationResponse(BaseModel):
    """Comprehensive UBO computation result for an audited legal entity."""

    model_config = ConfigDict(populate_by_name=True)

    entity_id: str
    entity_name: str
    statutory_threshold_percent: float = 25.0
    beneficial_owners: list[EffectiveBeneficialOwner]
    total_identified_ownership_percent: float
    unidentified_ownership_percent: float
    max_depth_traversed: int
    calculation_timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )


class UBOAnomalyDetail(BaseModel):
    """Single corporate topology anomaly alert."""

    anomaly_type: UBOAnomalyType
    severity: str = "HIGH"
    title: str
    description: str
    involved_node_ids: list[str]
    risk_score_impact: float = 100.0


class UBOAnomalyDetectionResponse(BaseModel):
    """Consolidated anomaly detection report across corporate graph structures."""

    model_config = ConfigDict(populate_by_name=True)

    entity_id: str | None = None
    anomalies: list[UBOAnomalyDetail] = Field(default_factory=list)
    circular_ownership_cycles: list[list[str]] = Field(
        default_factory=list,
        description="Directed cycle sequences where entities own each other in a loop.",
    )
    nominee_directors: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Natural persons serving as directors for an abnormally large portfolio of companies.",
    )
    shell_company_clusters: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Clusters of entities incorporated in non-cooperative offshore jurisdictions.",
    )
    structural_risk_score: float = Field(
        ...,
        ge=0.0,
        le=1000.0,
        description="Composite structural corporate risk score (0 to 1000).",
    )
    risk_level: str = "LOW"


class UBOSubgraphNode(BaseModel):
    """Node descriptor tailored for React Flow or Cytoscape visualization."""

    id: str
    label: str
    node_type: str
    jurisdiction: str
    risk_score: float = 0.0
    is_pep: bool = False
    is_sanctioned: bool = False
    is_shell_suspect: bool = False


class UBOSubgraphEdge(BaseModel):
    """Edge descriptor tailored for React Flow or Cytoscape visualization."""

    source: str
    target: str
    relation_type: str
    ownership_percentage: float = 0.0
    is_nominee: bool = False


class UBOSubgraphResponse(BaseModel):
    """Visualization-ready multi-tier corporate ego-subgraph."""

    model_config = ConfigDict(populate_by_name=True)

    root_id: str
    nodes: list[UBOSubgraphNode]
    edges: list[UBOSubgraphEdge]
    total_nodes: int
    total_edges: int


class UBOMetricsResponse(BaseModel):
    """Consortium-level corporate registry metrics."""

    total_nodes: int
    total_entities: int
    total_natural_persons: int
    total_relations: int
    total_cycles_detected: int
    nominee_directors_count: int
    high_risk_offshore_count: int
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
