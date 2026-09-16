"""Domain value objects for Autonomous Agentic AML Copilot & RAG Narrative Generator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AMLCopilotAnalysis:
    """Container holding synthesized BSA/AML copilot findings and regulatory narrative.

    Adheres to FinCEN Guidance FIN-2007-G003 5-paragraph SAR narrative structure.
    """

    case_id: str
    fincen_sar_narrative: str  # 5-paragraph FinCEN SAR narrative in Markdown format
    four_eyes_briefing: str  # Supervisor 4-eyes dual-signoff briefing summary
    recommended_action: str  # e.g., 'CONFIRMED_SAR', 'ESCALATE_TO_FIU', 'MONITOR_ACCOUNT'
    top_risk_drivers: list[dict[str, Any]]  # List of SHAP feature attributions
    graph_topology_summary: dict[str, Any]  # Louvain community, PageRank, layering hops
    zero_pii_verified: bool
    generated_at_timestamp: float
    lineage_hash: str  # SHA-256 block hash for audit lineage
    evidence_count: int = 0
    timeline_event_count: int = 0


@dataclass(frozen=True)
class CaseEvidenceDossier:
    """Assembled evidence package aggregated across timeline events, notes, and registered artifacts."""

    case_id: str
    case_title: str
    case_status: str
    total_risk_score: float
    alert_ids: list[str]
    timeline_events: list[dict[str, Any]]
    evidence_artifacts: list[dict[str, Any]]
    investigator_notes: list[str]
    shap_drivers: list[dict[str, Any]]
    graph_topology: dict[str, Any]
    pii_sanitized_count: int
    evidence_hash: str
    assembled_at: float


@dataclass(frozen=True)
class CopilotQueryRequest:
    """Request payload for triggering AML Copilot synthesis."""

    case_id: str
    include_fincen_narrative: bool = True
    include_four_eyes_briefing: bool = True
    custom_investigator_notes: str | None = None
    shap_attributions: list[dict[str, Any]] | None = None
    graph_metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class CopilotDirectGenerationRequest:
    """Direct generation request payload matching LandingPage and public API spec."""

    case_id: str
    shap_attributions: list[dict[str, Any]] | None = None
    graph_nodes: list[dict[str, Any]] | dict[str, Any] | None = None
    custom_investigator_notes: str | None = None
    risk_score: float | None = None


@dataclass(frozen=True)
class CopilotQueryResponse:
    """API response for AML Copilot synthesis."""

    case_id: str
    fincen_sar_narrative: str
    four_eyes_briefing: str
    recommended_action: str
    top_risk_drivers: list[dict[str, Any]]
    graph_topology_summary: dict[str, Any]
    zero_pii_verified: bool
    generated_at: str
    lineage_hash: str
    evidence_count: int = 0
    timeline_event_count: int = 0
    sar_narrative: str = ""
    supervisor_briefing: str = ""

    def __post_init__(self) -> None:
        if not self.sar_narrative and self.fincen_sar_narrative:
            object.__setattr__(self, "sar_narrative", self.fincen_sar_narrative)
        if not self.supervisor_briefing and self.four_eyes_briefing:
            object.__setattr__(self, "supervisor_briefing", self.four_eyes_briefing)
