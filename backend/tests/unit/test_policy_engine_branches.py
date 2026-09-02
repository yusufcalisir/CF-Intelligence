"""Targeted Branch Coverage Tests for Policy Engine AST & Service Logic."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.application.services.policy_engine import (
    PolicyEngineService,
    evaluate_condition,
)


class TestPolicyEngineASTBranches:
    """Test every branch and conditional leaf in AST evaluate_condition."""

    def test_and_branch_variations(self):
        # Malformed 'and' (not a list)
        assert evaluate_condition({"and": "not-a-list"}, {"amount": 100}) is False
        assert evaluate_condition({"and": 123}, {"amount": 100}) is False

        # Valid 'and' with all true
        cond_true = {
            "and": [
                {"field": "amount", "operator": ">=", "value": 5000},
                {"field": "country", "operator": "==", "value": "US"},
            ]
        }
        assert evaluate_condition(cond_true, {"amount": 6000, "country": "US"}) is True
        assert evaluate_condition(cond_true, {"amount": 4000, "country": "US"}) is False

    def test_or_branch_variations(self):
        # Malformed 'or' (not a list)
        assert evaluate_condition({"or": 42}, {"risk_score": 900}) is False

        # Valid 'or'
        cond_or = {
            "or": [
                {"field": "risk_score", "operator": ">", "value": 850},
                {"field": "is_pep", "operator": "==", "value": "true"},
            ]
        }
        assert evaluate_condition(cond_or, {"risk_score": 900, "is_pep": "false"}) is True
        assert evaluate_condition(cond_or, {"risk_score": 500, "is_pep": "true"}) is True
        assert evaluate_condition(cond_or, {"risk_score": 500, "is_pep": "false"}) is False

    def test_not_branch_variations(self):
        # Malformed 'not' (not a dict)
        assert evaluate_condition({"not": ["list"]}, {"amount": 100}) is False

        # Valid 'not'
        cond_not = {
            "not": {"field": "status", "operator": "==", "value": "whitelisted"}
        }
        assert evaluate_condition(cond_not, {"status": "suspicious"}) is True
        assert evaluate_condition(cond_not, {"status": "whitelisted"}) is False

    def test_missing_or_none_fields_and_operators(self):
        # Missing field or operator
        assert evaluate_condition({}, {"amount": 100}) is False
        assert evaluate_condition({"field": "amount"}, {"amount": 100}) is False
        assert evaluate_condition({"operator": ">"}, {"amount": 100}) is False

        # Context missing field or target value is None
        assert evaluate_condition({"field": "amount", "operator": ">", "value": 100}, {}) is False
        assert evaluate_condition({"field": "amount", "operator": ">", "value": None}, {"amount": 100}) is False

    def test_comparison_operators(self):
        ctx = {"amount": 5000, "code": "TX99", "ratio": 0.05}

        # Equality & Inequality
        assert evaluate_condition({"field": "code", "operator": "==", "value": "tx99"}, ctx) is True
        assert evaluate_condition({"field": "code", "operator": "==", "value": "TX100"}, ctx) is False
        assert evaluate_condition({"field": "code", "operator": "!=", "value": "TX100"}, ctx) is True
        assert evaluate_condition({"field": "code", "operator": "!=", "value": "tx99"}, ctx) is False

        # Numeric comparisons
        assert evaluate_condition({"field": "amount", "operator": ">", "value": 4000}, ctx) is True
        assert evaluate_condition({"field": "amount", "operator": ">", "value": 6000}, ctx) is False

        assert evaluate_condition({"field": "amount", "operator": ">=", "value": 5000}, ctx) is True
        assert evaluate_condition({"field": "amount", "operator": ">=", "value": 5001}, ctx) is False

        assert evaluate_condition({"field": "ratio", "operator": "<", "value": 0.10}, ctx) is True
        assert evaluate_condition({"field": "ratio", "operator": "<", "value": 0.01}, ctx) is False

        assert evaluate_condition({"field": "ratio", "operator": "<=", "value": 0.05}, ctx) is True
        assert evaluate_condition({"field": "ratio", "operator": "<=", "value": 0.04}, ctx) is False

    def test_in_and_not_in_operators(self):
        ctx_list = {"jurisdiction": "KY", "tag": "layering"}

        # 'in' list
        cond_in_list = {"field": "jurisdiction", "operator": "in", "value": ["ky", "vg", "pa"]}
        assert evaluate_condition(cond_in_list, ctx_list) is True

        cond_in_list_fail = {"field": "jurisdiction", "operator": "in", "value": ["us", "gb"]}
        assert evaluate_condition(cond_in_list_fail, ctx_list) is False

        # 'in' substring
        cond_in_substr = {"field": "tag", "operator": "in", "value": "rapid_layering_scheme"}
        assert evaluate_condition(cond_in_substr, ctx_list) is True

        # 'not in' list
        cond_not_in_list = {"field": "jurisdiction", "operator": "not in", "value": ["us", "ca"]}
        assert evaluate_condition(cond_not_in_list, ctx_list) is True

        cond_not_in_list_fail = {"field": "jurisdiction", "operator": "not in", "value": ["ky", "vg"]}
        assert evaluate_condition(cond_not_in_list_fail, ctx_list) is False

        # 'not in' substring
        cond_not_in_substr = {"field": "tag", "operator": "not in", "value": "clean_retail_flow"}
        assert evaluate_condition(cond_not_in_substr, ctx_list) is True

    def test_exception_and_unknown_operator_branches(self):
        # Invalid numerical comparison that triggers float() ValueError
        ctx = {"amount": "non-numeric-value"}
        assert evaluate_condition({"field": "amount", "operator": ">", "value": 1000}, ctx) is False

        # Unknown operator fallback
        assert evaluate_condition({"field": "amount", "operator": "INVALID_OP", "value": 1000}, {"amount": 2000}) is False


@pytest.mark.asyncio
class TestPolicyEngineServiceBranches:
    """Test PolicyEngineService CRUD methods and local evaluation."""

    async def test_policy_engine_service_crud(self):
        service = PolicyEngineService()
        mock_session = AsyncMock()
        mock_session.add = MagicMock()

        # 1. create_rule
        rule = await service.create_rule(
            session=mock_session,
            rule_name="Test Rule",
            condition={"field": "amount", "operator": ">", "value": 10000},
            action="BLOCK_TRANSACTION",
            is_active=True,
        )
        assert rule.rule_name == "Test Rule"
        assert rule.action == "BLOCK_TRANSACTION"
        assert rule.is_active is True
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called()

        # 2. list_rules
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [rule]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        rules = await service.list_rules(mock_session)
        assert len(rules) == 1
        assert rules[0].rule_name == "Test Rule"

        # 3. get_active_rules
        active_rules = await service.get_active_rules(mock_session)
        assert len(active_rules) == 1

        # 4. update_rule existing
        mock_session.execute.return_value.scalar_one_or_none.return_value = rule
        updated = await service.update_rule(
            session=mock_session,
            rule_id=rule.id,
            rule_name="Updated Rule",
            condition={"field": "amount", "operator": ">", "value": 20000},
            action="ALERT_ONLY",
            is_active=False,
        )
        assert updated is not None
        assert updated.rule_name == "Updated Rule"
        assert updated.action == "ALERT_ONLY"
        assert updated.is_active is False

        # 5. update_rule not found
        mock_session.execute.return_value.scalar_one_or_none.return_value = None
        not_found_update = await service.update_rule(mock_session, rule_id="unknown-id")
        assert not_found_update is None

        # 6. delete_rule not found
        mock_session.execute.return_value.scalar_one_or_none.return_value = None
        assert await service.delete_rule(mock_session, rule_id="unknown-id") is False

        # 7. delete_rule existing
        mock_session.execute.return_value.scalar_one_or_none.return_value = rule
        assert await service.delete_rule(mock_session, rule_id=rule.id) is True

        # 8. test_rule local execution
        passed = service.test_rule(
            condition={"field": "amount", "operator": ">", "value": 500},
            transaction={"amount": 1000},
        )
        assert passed is True
