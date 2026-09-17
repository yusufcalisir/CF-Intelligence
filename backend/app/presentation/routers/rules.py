"""Business Rules API Router.

Provides dynamic CRUD, test execution, and real-time transaction screening endpoints
for AML & fraud policy rules. Supports both /api/v1/rules and /v1/rules paths.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.application.schemas.rules import (
    BusinessRuleCreateRequest,
    BusinessRuleResponse,
    BusinessRuleTestRequest,
    BusinessRuleTestResponse,
    BusinessRuleUpdateRequest,
    RuleEvaluationMatchItem,
    RuleEvaluationRequest,
    RuleEvaluationResponse,
)
from app.application.services.policy_engine import (
    PolicyEngineService,
    validate_condition_ast,
)
from app.dependencies import OptionalSessionDep, SessionDep  # noqa: TC001

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/rules", tags=["rules"])
api_router = APIRouter(prefix="/v1/rules", tags=["rules"])

_policy_service = PolicyEngineService()


def _to_rule_response(model: Any) -> BusinessRuleResponse:
    """Helper mapper to convert database model to schema response."""
    return BusinessRuleResponse(
        id=model.id,
        rule_name=model.rule_name,
        condition=model.condition,
        action=model.action,
        is_active=bool(model.is_active),
        description=getattr(model, "description", None),
        priority=getattr(model, "priority", 100),
        created_at=model.created_at.isoformat()
        if hasattr(model.created_at, "isoformat")
        else str(model.created_at),
        updated_at=model.updated_at.isoformat() if getattr(model, "updated_at", None) else None,
    )


_DEFAULT_RULES = [
    BusinessRuleResponse(
        id="rule_vel_001",
        rule_name="High Velocity Transfer Detection",
        condition={"field": "velocity_1h", "operator": ">", "value": 5},
        action="FLAG_HIGH_RISK",
        is_active=True,
        description="Flags accounts with more than 5 transfers within an hour",
        priority=20,
        created_at="2026-01-01T00:00:00Z",
    ),
    BusinessRuleResponse(
        id="rule_dev_002",
        rule_name="Unrecognized Device Anomaly",
        condition={"field": "is_new_device", "operator": "==", "value": True},
        action="REQUIRE_MFA",
        is_active=True,
        description="Requires secondary biometric MFA challenge on unrecognized hardware ID",
        priority=30,
        created_at="2026-01-01T00:00:00Z",
    ),
    BusinessRuleResponse(
        id="rule_geo_003",
        rule_name="Cross-Border High Risk Jurisdiction",
        condition={"field": "country_risk_score", "operator": ">", "value": 0.8},
        action="FLAG_CRITICAL",
        is_active=True,
        description="Critical alert when originating or destination country risk score > 0.8",
        priority=10,
        created_at="2026-01-01T00:00:00Z",
    ),
    BusinessRuleResponse(
        id="rule_smurf_004",
        rule_name="Structuring / Smurfing Pattern",
        condition={"field": "amount", "operator": "between", "min_value": 9000, "max_value": 9999},
        action="ESCALATE_TO_SAR",
        is_active=True,
        description="Flags transactions just below the mandatory $10k reporting ceiling",
        priority=5,
        created_at="2026-01-01T00:00:00Z",
    ),
]


@router.post("", response_model=BusinessRuleResponse, status_code=status.HTTP_201_CREATED)
@api_router.post("", response_model=BusinessRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_business_rule(
    payload: BusinessRuleCreateRequest,
    session: SessionDep,
) -> BusinessRuleResponse:
    """Create and hot-reload a new business logic transaction screening rule."""
    try:
        rule = await _policy_service.create_rule(
            session=session,
            rule_name=payload.rule_name,
            condition=payload.condition,
            action=payload.action,
            is_active=payload.is_active,
        )
        return _to_rule_response(rule)
    except ValueError as exc:
        logger.warning("Validation error creating rule %s: %s", payload.rule_name, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Rule validation failure: {exc}",
        ) from exc
    except Exception as exc:
        logger.error("Failed to create rule %s: %s", payload.rule_name, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error registering business rule: {exc}",
        ) from exc


@router.get("", response_model=list[BusinessRuleResponse])
@api_router.get("", response_model=list[BusinessRuleResponse])
async def list_business_rules(session: OptionalSessionDep = None) -> list[BusinessRuleResponse]:
    """Retrieve all business rules configured in the active tenant."""
    if session is not None:
        try:
            rules = await _policy_service.list_rules(session)
            if rules:
                return [_to_rule_response(r) for r in rules]
        except Exception as exc:
            logger.warning(
                "Database query failed for list_business_rules (%s), returning default rules fallback",
                exc,
            )
    return _DEFAULT_RULES


@router.get("/{rule_id}", response_model=BusinessRuleResponse)
@api_router.get("/{rule_id}", response_model=BusinessRuleResponse)
async def get_business_rule(
    rule_id: str,
    session: OptionalSessionDep = None,
) -> BusinessRuleResponse:
    """Retrieve a single business rule by its unique ID."""
    if session is not None:
        try:
            rule = await _policy_service.get_rule(session, rule_id)
            if rule:
                return _to_rule_response(rule)
        except Exception as exc:
            logger.warning("Database query failed for get_business_rule (%s)", exc)

    # Check default fallback rules
    for r in _DEFAULT_RULES:
        if r.id == rule_id:
            return r

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Business rule with ID '{rule_id}' not found",
    )


@router.put("/{rule_id}", response_model=BusinessRuleResponse)
@api_router.put("/{rule_id}", response_model=BusinessRuleResponse)
async def update_business_rule(
    rule_id: str,
    payload: BusinessRuleUpdateRequest,
    session: SessionDep,
) -> BusinessRuleResponse:
    """Update condition, action, or active state of an existing rule."""
    try:
        rule = await _policy_service.update_rule(
            session=session,
            rule_id=rule_id,
            rule_name=payload.rule_name,
            condition=payload.condition,
            action=payload.action,
            is_active=payload.is_active,
        )
        if not rule:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Business rule with ID '{rule_id}' not found",
            )
        return _to_rule_response(rule)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid rule update parameters: {exc}",
        ) from exc
    except Exception as exc:
        logger.error("Failed to update rule %s: %s", rule_id, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error updating rule settings: {exc}",
        ) from exc


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
@api_router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_business_rule(rule_id: str, session: SessionDep) -> None:
    """Delete a business rule permanently from the database."""
    success = await _policy_service.delete_rule(session, rule_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Business rule with ID '{rule_id}' not found",
        )


@router.post("/test", response_model=BusinessRuleTestResponse)
@api_router.post("/test", response_model=BusinessRuleTestResponse)
async def test_business_rule(payload: BusinessRuleTestRequest) -> BusinessRuleTestResponse:
    """Test a condition AST configuration against a mock transaction dry-run payload.

    Rejects invalid AST syntax with authentic RFC 7807 400 Bad Request instead of deceptive HTTP 200.
    """
    try:
        matches, matched_fields = _policy_service.test_rule_detailed(payload.condition, payload.transaction)
        return BusinessRuleTestResponse(
            matches=matches,
            message="Rule matched transaction parameters"
            if matches
            else "Rule did not match transaction parameters",
            matched_fields=matched_fields,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid condition AST syntax: {exc}",
        ) from exc
    except Exception as exc:
        logger.error("Condition AST execution error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Condition evaluation error: {exc}",
        ) from exc


@router.post("/evaluate", response_model=RuleEvaluationResponse)
@api_router.post("/evaluate", response_model=RuleEvaluationResponse)
async def evaluate_transaction_policies(
    payload: RuleEvaluationRequest,
    session: OptionalSessionDep = None,
) -> RuleEvaluationResponse:
    """Screen an incoming transaction against all active fraud policy rules.

    Computes composite decisions ('ALLOW', 'REVIEW', 'BLOCK'), risk score adjustments,
    and returns matched rule telemetry with sub-millisecond evaluation latency.
    """
    try:
        res = await _policy_service.evaluate_rules(
            session=session,
            transaction=payload.transaction,
            stop_on_first_match=payload.stop_on_first_match,
            tenant_id=payload.tenant_id,
            default_rules=_DEFAULT_RULES,
        )
        return RuleEvaluationResponse(
            evaluated_rules_count=res["evaluated_rules_count"],
            triggered_rules_count=res["triggered_rules_count"],
            highest_severity_action=res["highest_severity_action"],
            decision=res["decision"],
            risk_score_delta=res["risk_score_delta"],
            triggered_rules=[RuleEvaluationMatchItem(**item) for item in res["triggered_rules"]],
            latency_ms=res["latency_ms"],
        )
    except Exception as exc:
        logger.error("Policy evaluation screening failure: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Rule evaluation failure: {exc}",
        ) from exc
