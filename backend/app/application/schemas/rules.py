"""Dynamic Business Rule Engine & Policy Application Schemas.

Defines Pydantic v2 validation models for declarative AST conditions, rule CRUD,
dry-run testing, and real-time transaction screening evaluation.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Standard supported fraud actions
RuleActionType = Literal[
    "BLOCK_TRANSACTION",
    "ESCALATE_TO_SAR",
    "FLAG_CRITICAL",
    "FLAG_HIGH_RISK",
    "REQUIRE_MFA",
    "HOLD_FOR_REVIEW",
    "ALLOW",
]

_RULE_ACTIONS_TUPLE = (
    "BLOCK_TRANSACTION",
    "ESCALATE_TO_SAR",
    "FLAG_CRITICAL",
    "FLAG_HIGH_RISK",
    "REQUIRE_MFA",
    "HOLD_FOR_REVIEW",
    "ALLOW",
)


def _strip_control_chars(v: str) -> str:
    """Strip dangerous control characters from string values."""
    if not isinstance(v, str):
        return v
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "", v).strip()


class BusinessRuleCreateRequest(BaseModel):
    """Request payload for registering a new business screening rule."""

    rule_name: str = Field(
        ...,
        min_length=3,
        max_length=128,
        pattern=r"^[a-zA-Z0-9 _\-]+$",
        description="Unique, human-readable name of the screening rule",
    )
    condition: dict[str, Any] = Field(
        ...,
        description="Declarative condition JSON AST (e.g. {'and': [{'field': 'amount', 'operator': '>', 'value': 10000}]})",
    )
    action: str = Field(
        default="BLOCK_TRANSACTION",
        description="Policy action to trigger when conditions match",
    )
    is_active: bool = Field(
        default=True,
        description="Whether the rule is hot-loaded into active screening immediately",
    )
    description: str | None = Field(
        default=None,
        max_length=512,
        description="Optional documentation of the rule purpose or compliance mandate",
    )
    priority: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Evaluation priority order (lower number = higher priority)",
    )

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("rule_name")
    @classmethod
    def sanitize_rule_name(cls, v: str) -> str:
        return _strip_control_chars(v)

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        clean = v.strip().upper()
        if clean not in _RULE_ACTIONS_TUPLE and not re.match(r"^[A-Z0-9_]{3,50}$", clean):
            raise ValueError(
                f"Action must be one of {_RULE_ACTIONS_TUPLE} or a valid uppercase identifier (3-50 chars)."
            )
        return clean


class BusinessRuleUpdateRequest(BaseModel):
    """Request payload for updating an existing business screening rule."""

    rule_name: str | None = Field(
        default=None,
        min_length=3,
        max_length=128,
        pattern=r"^[a-zA-Z0-9 _\-]+$",
        description="Updated rule name",
    )
    condition: dict[str, Any] | None = Field(
        default=None,
        description="Updated declarative condition AST",
    )
    action: str | None = Field(
        default=None,
        description="Updated policy action",
    )
    is_active: bool | None = Field(
        default=None,
        description="Updated activation state",
    )
    description: str | None = Field(
        default=None,
        max_length=512,
        description="Updated description",
    )
    priority: int | None = Field(
        default=None,
        ge=1,
        le=1000,
        description="Updated priority order",
    )

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("rule_name")
    @classmethod
    def sanitize_name(cls, v: str | None) -> str | None:
        if v is not None:
            return _strip_control_chars(v)
        return None

    @field_validator("action")
    @classmethod
    def validate_action_update(cls, v: str | None) -> str | None:
        if v is not None:
            clean = v.strip().upper()
            if clean not in _RULE_ACTIONS_TUPLE and not re.match(r"^[A-Z0-9_]{3,50}$", clean):
                raise ValueError(f"Invalid rule action: '{v}'")
            return clean
        return None


class BusinessRuleResponse(BaseModel):
    """Response payload representing a registered policy screening rule."""

    id: str = Field(..., description="Unique rule identifier UUID")
    rule_name: str = Field(..., description="Human-readable rule name")
    condition: dict[str, Any] = Field(..., description="Condition JSON AST")
    action: str = Field(..., description="Configured policy action")
    is_active: bool = Field(..., description="Whether rule is actively enforced")
    description: str | None = Field(default=None, description="Rule description")
    priority: int = Field(default=100, description="Evaluation priority")
    created_at: str = Field(..., description="ISO 8601 creation timestamp")
    updated_at: str | None = Field(default=None, description="ISO 8601 last update timestamp")

    model_config = ConfigDict(extra="ignore")


class BusinessRuleTestRequest(BaseModel):
    """Request payload for dry-running a rule condition against sample transaction data."""

    condition: dict[str, Any] = Field(
        ...,
        description="Condition AST to evaluate",
    )
    transaction: dict[str, Any] = Field(
        ...,
        description="Mock or historical transaction feature context",
    )

    model_config = ConfigDict(extra="forbid")


class BusinessRuleTestResponse(BaseModel):
    """Result of testing a declarative condition AST against test transaction context."""

    matches: bool = Field(..., description="Whether the condition evaluated to True")
    message: str = Field(..., description="Human-readable explanation of the test outcome")
    matched_fields: list[str] = Field(
        default_factory=list,
        description="List of context fields that contributed to condition match",
    )

    model_config = ConfigDict(extra="ignore")


class RuleEvaluationRequest(BaseModel):
    """Request payload for evaluating an incoming transaction against all active policy rules."""

    transaction: dict[str, Any] = Field(
        ...,
        description="Transaction feature dictionary (amount, velocity_1h, country_risk, etc.)",
    )
    tenant_id: str | None = Field(
        default=None,
        description="Optional bank tenant identifier to isolate bank-specific rules",
    )
    stop_on_first_match: bool = Field(
        default=False,
        description="Whether to short-circuit evaluation after first matching rule",
    )

    model_config = ConfigDict(extra="forbid")


class RuleEvaluationMatchItem(BaseModel):
    """Item representing a single rule that matched during transaction screening."""

    rule_id: str = Field(..., description="Identifier of matching rule")
    rule_name: str = Field(..., description="Name of matching rule")
    action: str = Field(..., description="Triggered action")
    priority: int = Field(default=100, description="Rule priority")
    matched_condition: dict[str, Any] = Field(..., description="Condition AST that matched")

    model_config = ConfigDict(extra="ignore")


class RuleEvaluationResponse(BaseModel):
    """Response payload representing comprehensive rule engine screening output."""

    evaluated_rules_count: int = Field(..., description="Total active rules evaluated")
    triggered_rules_count: int = Field(..., description="Number of rules that triggered")
    highest_severity_action: str = Field(
        ...,
        description="Highest severity action among all triggered rules ('ALLOW', 'REQUIRE_MFA', 'HOLD_FOR_REVIEW', 'FLAG_HIGH_RISK', 'FLAG_CRITICAL', 'ESCALATE_TO_SAR', 'BLOCK_TRANSACTION')",
    )
    decision: Literal["ALLOW", "REVIEW", "BLOCK"] = Field(
        ...,
        description="Aggregated transaction decision",
    )
    risk_score_delta: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Additive risk score adjustment computed from matched policy rules",
    )
    triggered_rules: list[RuleEvaluationMatchItem] = Field(
        default_factory=list,
        description="Detailed list of matched rules",
    )
    latency_ms: float = Field(..., description="AST evaluation latency in milliseconds")

    model_config = ConfigDict(extra="ignore")
