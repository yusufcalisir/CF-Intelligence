"""Business Rules API Router.

Provides dynamic CRUD and test execution endpoints for AML policy rules.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.application.schemas.phase2 import (
    BusinessRuleCreateRequest,
    BusinessRuleResponse,
    BusinessRuleTestRequest,
    BusinessRuleTestResponse,
    BusinessRuleUpdateRequest,
)
from app.application.services.policy_engine import PolicyEngineService
from app.dependencies import SessionDep  # noqa: TC001

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/rules", tags=["rules"])

_policy_service = PolicyEngineService()


def _to_rule_response(model: Any) -> BusinessRuleResponse:
    """Helper mapper to convert database model to schema response."""
    return BusinessRuleResponse(
        id=model.id,
        rule_name=model.rule_name,
        condition=model.condition,
        action=model.action,
        is_active=bool(model.is_active),
        created_at=model.created_at.isoformat()
        if hasattr(model.created_at, "isoformat")
        else str(model.created_at),
        updated_at=model.updated_at.isoformat() if getattr(model, "updated_at", None) else None,
    )


@router.post("", response_model=BusinessRuleResponse, status_code=status.HTTP_201_CREATED)
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
    except Exception as exc:
        logger.error("Failed to create rule %s: %s", payload.rule_name, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error registering business rule: {exc}",
        )


_DEFAULT_RULES = [
    BusinessRuleResponse(
        id="rule_vel_001",
        rule_name="High Velocity Transfer Detection",
        condition={"field": "velocity_1h", "operator": ">", "value": 5, "min_amount": 1000},
        action="FLAG_HIGH_RISK",
        is_active=True,
        created_at="2026-01-01T00:00:00Z",
    ),
    BusinessRuleResponse(
        id="rule_dev_002",
        rule_name="Unrecognized Device Anomaly",
        condition={"field": "is_new_device", "operator": "==", "value": True, "min_amount": 5000},
        action="REQUIRE_MFA",
        is_active=True,
        created_at="2026-01-01T00:00:00Z",
    ),
    BusinessRuleResponse(
        id="rule_geo_003",
        rule_name="Cross-Border High Risk Jurisdiction",
        condition={"field": "country_risk_score", "operator": ">", "value": 0.8},
        action="FLAG_CRITICAL",
        is_active=True,
        created_at="2026-01-01T00:00:00Z",
    ),
    BusinessRuleResponse(
        id="rule_smurf_004",
        rule_name="Structuring / Smurfing Pattern",
        condition={"field": "amount", "operator": "between", "min_value": 9000, "max_value": 9999},
        action="ESCALATE_TO_SAR",
        is_active=True,
        created_at="2026-01-01T00:00:00Z",
    ),
]


@router.get("", response_model=list[BusinessRuleResponse])
async def list_business_rules(session: SessionDep) -> list[BusinessRuleResponse]:
    """Retrieve all business rules configured in the active tenant."""
    try:
        rules = await _policy_service.list_rules(session)
        if rules:
            return [_to_rule_response(r) for r in rules]
        return _DEFAULT_RULES
    except Exception as exc:
        logger.warning("Database unavailable for list_business_rules (%s), returning default rules fallback", exc)
        return _DEFAULT_RULES


@router.put("/{rule_id}", response_model=BusinessRuleResponse)
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
                detail=f"Business rule with ID {rule_id} not found",
            )
        return _to_rule_response(rule)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to update rule %s: %s", rule_id, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error updating rule settings: {exc}",
        )


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_business_rule(rule_id: str, session: SessionDep) -> None:
    """Delete a business rule permanently from the database."""
    success = await _policy_service.delete_rule(session, rule_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Business rule with ID {rule_id} not found",
        )


@router.post("/test", response_model=BusinessRuleTestResponse)
async def test_business_rule(payload: BusinessRuleTestRequest) -> BusinessRuleTestResponse:
    """Test a condition AST configuration against a mock transaction dry-run payload."""
    try:
        matches = _policy_service.test_rule(payload.condition, payload.transaction)
        return BusinessRuleTestResponse(
            matches=matches,
            message="Rule matched transaction parameters"
            if matches
            else "Rule did not match transaction parameters",
        )
    except Exception as exc:
        return BusinessRuleTestResponse(
            matches=False,
            message=f"Evaluation failed: {exc}",
        )
