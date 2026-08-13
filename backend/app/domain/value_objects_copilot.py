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
    recommended_action: str  # e.g., 'CONFIRMED_SAR', 'ESCALATE_TO_FIU', 'FALSE_POSITIVE'
    top_risk_drivers: list[dict[str, Any]]  # List of SHAP feature attributions
    graph_topology_summary: dict[str, Any]  # Louvain community, PageRank, layering hops
    zero_pii_verified: bool
    generated_at_timestamp: float
    lineage_hash: str  # SHA-256 block hash for audit lineage


@dataclass(frozen=True)
class CopilotQueryRequest:
    """Request payload for trigger AML Copilot synthesis."""

    case_id: str
    include_fincen_narrative: bool = True
    include_four_eyes_briefing: bool = True
    custom_investigator_notes: str | None = None


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
