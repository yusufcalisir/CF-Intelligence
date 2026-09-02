"""Unit tests for the Dynamic Policy & Business Rules Engine."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.application.services.policy_engine import PolicyEngineService, evaluate_condition
from app.infrastructure.models import BusinessRuleModel


class TestASTEvaluator:
    """Test standard logical condition evaluations against transaction variables."""

    @pytest.fixture
    def mock_context(self) -> dict:
        return {
            "composite_risk_score": 850,
            "country_code": "NG",
            "velocity": 6.5,
            "transaction_amount": 1500.0,
            "merchant_category": "crypto",
            "device_type": "mobile_app",
            "fraud_probability": 0.92,
        }

    def test_single_condition_equality(self, mock_context: dict) -> None:
        cond = {"field": "country_code", "operator": "==", "value": "NG"}
        assert evaluate_condition(cond, mock_context) is True

    def test_single_condition_comparison(self, mock_context: dict) -> None:
        cond = {"field": "composite_risk_score", "operator": ">=", "value": 800}
        assert evaluate_condition(cond, mock_context) is True

    def test_logical_and(self, mock_context: dict) -> None:
        cond = {
            "and": [
                {"field": "composite_risk_score", "operator": ">=", "value": 800},
                {"field": "country_code", "operator": "in", "value": ["NG", "RU", "PH"]},
                {"field": "velocity", "operator": ">", "value": 5.0},
            ]
        }
        assert evaluate_condition(cond, mock_context) is True

    def test_logical_or(self, mock_context: dict) -> None:
        cond = {
            "or": [
                {"field": "transaction_amount", "operator": ">", "value": 10000.0},
                {"field": "merchant_category", "operator": "==", "value": "crypto"},
            ]
        }
        assert evaluate_condition(cond, mock_context) is True

    def test_logical_not(self, mock_context: dict) -> None:
        cond = {"not": {"field": "device_type", "operator": "==", "value": "pos_terminal"}}
        assert evaluate_condition(cond, mock_context) is True


class TestPolicyEngineMocked:
    """Test policy rule operations with database mocking."""

    @pytest.mark.anyio
    async def test_rule_crud_lifecycle(self) -> None:
        session = MagicMock()
        session.commit = AsyncMock()
        session.delete = AsyncMock()
        session.execute = AsyncMock()

        # Instantiate service
        service = PolicyEngineService()

        # Mock query return values
        mock_rule = BusinessRuleModel(
            id="rule-123",
            rule_name="test_velocity_rule",
            condition={"field": "velocity", "operator": ">", "value": 10.0},
            action="BLOCK_TRANSACTION",
            is_active=True,
        )

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_rule]
        mock_result.scalar_one_or_none.return_value = mock_rule
        session.execute.return_value = mock_result

        # 1. Create Rule
        rule = await service.create_rule(
            session=session,
            rule_name="test_velocity_rule",
            condition={"field": "velocity", "operator": ">", "value": 10.0},
            action="BLOCK_TRANSACTION",
            is_active=True,
        )
        assert rule.rule_name == "test_velocity_rule"
        assert session.commit.called

        # 2. List Rules
        all_rules = await service.list_rules(session)
        assert len(all_rules) == 1
        assert all_rules[0].rule_name == "test_velocity_rule"

        # 3. Retrieve Active Rules
        active_rules = await service.get_active_rules(session)
        assert len(active_rules) == 1
        assert active_rules[0].is_active is True

        # 4. Update Rule
        updated = await service.update_rule(
            session=session,
            rule_id="rule-123",
            is_active=False,
        )
        assert updated is not None
        assert updated.is_active is False

        # 5. Delete Rule
        deleted = await service.delete_rule(session, "rule-123")
        assert deleted is True


class TestPolicyGatewayScreening:
    """Test endpoint-level transaction screening and blocking."""

    @pytest.mark.anyio
    async def test_api_predict_dynamic_rules(self) -> None:
        from app.application.services.policy_engine import invalidate_policy_cache
        from app.dependencies import get_session
        from app.main import app

        invalidate_policy_cache()

        mock_rule = BusinessRuleModel(
            id="rule-456",
            rule_name="api_blocking_test_rule",
            condition={"field": "merchant_category", "operator": "==", "value": "crypto"},
            action="BLOCK_TRANSACTION",
            is_active=True,
        )

        mock_session = MagicMock()
        mock_session.execute = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_rule]
        mock_session.execute.return_value = mock_result

        async def _mock_session_gen():
            yield mock_session

        app.dependency_overrides[get_session] = _mock_session_gen

        with TestClient(app) as test_client:
            payload = {
                "transaction_amount": 100.0,
                "merchant_category": "crypto",
                "country_code": "NG",
                "device_type": "web_browser",
                "velocity": 2.0,
                "hour_of_day": 12,
                "merchant_risk_score": 0.8,
                "customer_history_score": 0.2,
                "chargeback_count": 5,
                "account_age_days": 10,
            }
            res = test_client.post("/api/v1/predict", json=payload)
            data = res.json()
            assert data["policy_action"] == "BLOCK_TRANSACTION"

        app.dependency_overrides.clear()
