"""Pydantic schemas for Autonomous Agentic AML Copilot & Evidence Assembly.

Clean Architecture schema definitions for FinCEN 5-paragraph SAR generation,
4-Eyes supervisor briefings, and cryptographic case evidence packaging.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Sentinel regex to strip ASCII control characters
_SAFE_TEXT_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _strip_control(value: str) -> str:
    """Remove ASCII control characters that have no legitimate use in API text."""
    return _SAFE_TEXT_RE.sub("", value)


class CopilotDirectGenerationRequest(BaseModel):
    """Direct generation request payload matching LandingPage and public API spec."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    case_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Case identifier for SAR synthesis",
    )
    shap_attributions: list[dict[str, Any]] | None = Field(
        default=None,
        description="Explainability feature impact attributions",
    )
    graph_nodes: list[dict[str, Any]] | dict[str, Any] | None = Field(
        default=None,
        description="Graph topology metadata or node list",
    )
    custom_investigator_notes: str | None = Field(
        default=None,
        max_length=10000,
        description="Analyst observations to be sanitized and integrated",
    )
    risk_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1000.0,
        description="Composite ML risk score calibrated from 0.0 to 1000.0",
    )
    require_existing_case: bool = Field(
        default=False,
        description="If True, validates that case_id exists in registry and raises 404 if missing",
    )

    @field_validator("case_id")
    @classmethod
    def validate_case_id(cls, v: str) -> str:
        sanitized = _strip_control(v).strip()
        if not sanitized:
            raise ValueError("case_id must not be empty or whitespace")
        return sanitized

    @field_validator("custom_investigator_notes")
    @classmethod
    def sanitize_notes(cls, v: str | None) -> str | None:
        if v is not None:
            return _strip_control(v)
        return v


class CopilotQueryRequest(BaseModel):
    """Request payload for triggering AML Copilot synthesis on an existing case."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    case_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Target case identifier",
    )
    include_fincen_narrative: bool = Field(
        default=True,
        description="Whether to generate 5-paragraph FinCEN SAR narrative",
    )
    include_four_eyes_briefing: bool = Field(
        default=True,
        description="Whether to generate 4-Eyes supervisor signoff briefing",
    )
    custom_investigator_notes: str | None = Field(
        default=None,
        max_length=10000,
        description="Optional investigator notes to append",
    )
    shap_attributions: list[dict[str, Any]] | None = Field(
        default=None,
        description="Explicit SHAP feature attributions override",
    )
    graph_metadata: dict[str, Any] | None = Field(
        default=None,
        description="Explicit graph topology metadata override",
    )

    @field_validator("case_id")
    @classmethod
    def validate_case_id(cls, v: str) -> str:
        sanitized = _strip_control(v).strip()
        if not sanitized:
            raise ValueError("case_id must not be empty or whitespace")
        return sanitized

    @field_validator("custom_investigator_notes")
    @classmethod
    def sanitize_notes(cls, v: str | None) -> str | None:
        if v is not None:
            return _strip_control(v)
        return v


class CopilotQueryResponse(BaseModel):
    """API response for AML Copilot synthesis."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    case_id: str
    fincen_sar_narrative: str
    four_eyes_briefing: str
    recommended_action: str
    top_risk_drivers: list[dict[str, Any]] = Field(default_factory=list)
    graph_topology_summary: dict[str, Any] = Field(default_factory=dict)
    zero_pii_verified: bool
    generated_at: str
    lineage_hash: str
    evidence_count: int = 0
    timeline_event_count: int = 0
    sar_narrative: str = ""
    supervisor_briefing: str = ""

    @model_validator(mode="after")
    def populate_aliases(self) -> CopilotQueryResponse:
        if not self.sar_narrative and self.fincen_sar_narrative:
            self.sar_narrative = self.fincen_sar_narrative
        if not self.supervisor_briefing and self.four_eyes_briefing:
            self.supervisor_briefing = self.four_eyes_briefing
        return self


class AssembledEvidenceResponse(BaseModel):
    """API response for cryptographic evidence packaging."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    case_id: str
    case_title: str
    case_status: str
    total_risk_score: float
    evidence_hash: str
    evidence_count: int
    timeline_event_count: int
    pii_sanitized_count: int
    assembled_at: float


class CopilotStatusResponse(BaseModel):
    """Operational health and metric status of AML Copilot."""

    model_config = ConfigDict(extra="ignore")

    status: str = "active"
    zero_pii_engine: str = "operational"
    synthesized_analyses_count: int = 0
    timestamp: str
