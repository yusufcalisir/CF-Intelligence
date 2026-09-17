"""Unit tests for the Dynamic Fraud Rule Engine & Policy API routes.

Tests condition AST validation, CRUD endpoints, dry-run testing with ReDoS guards,
multi-rule transaction screening, and dual routing (/api/v1/rules and /v1/rules).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.application.services.policy_engine import invalidate_policy_cache
from app.dependencies import get_optional_session, get_session
from app.infrastructure.models import BusinessRuleModel
from app.main import app


@pytest.fixture(autouse=True)
def _reset_policy_cache():
    invalidate_policy_cache()
    yield
    invalidate_policy_cache()


def test_list_rules_default_fallback():
    """Verify listing rules without DB session returns the built-in default rules."""
    with TestClient(app) as client:
        res = client.get("/api/v1/rules")
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, list)
        assert len(data) >= 4
        rule_ids = [r["id"] for r in data]
        assert "rule_smurf_004" in rule_ids
        assert "rule_vel_001" in rule_ids


def test_get_rule_by_id_default():
    """Verify single rule lookup by ID for built-in rule."""
    with TestClient(app) as client:
        res = client.get("/api/v1/rules/rule_smurf_004")
        assert res.status_code == 200
        data = res.json()
        assert data["id"] == "rule_smurf_004"
        assert data["action"] == "ESCALATE_TO_SAR"
        assert data["is_active"] is True
        assert data["condition"]["operator"] == "between"


def test_get_rule_by_id_not_found():
    """Verify 404 is returned when rule ID does not exist."""
    with TestClient(app) as client:
        res = client.get("/api/v1/rules/nonexistent_rule_99999")
        assert res.status_code == 404
        assert "not found" in res.json()["detail"].lower()


def test_create_rule_success():
    """Verify registering a valid business rule persists it and returns 201 Created."""
    mock_session = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.refresh = AsyncMock()

    async def _mock_session_gen():
        yield mock_session

    app.dependency_overrides[get_session] = _mock_session_gen

    payload = {
        "rule_name": "Offshore Shell Transfer",
        "condition": {
            "and": [
                {"field": "country_code", "operator": "in", "value": ["VG", "KY", "PA"]},
                {"field": "amount", "operator": ">=", "value": 50000.0},
            ]
        },
        "action": "BLOCK_TRANSACTION",
        "is_active": True,
        "priority": 15,
        "description": "Immediate block on high-value transfers into known tax havens",
    }

    with TestClient(app) as client:
        res = client.post("/api/v1/rules", json=payload)
        assert res.status_code == 201
        data = res.json()
        assert data["rule_name"] == "Offshore Shell Transfer"
        assert data["action"] == "BLOCK_TRANSACTION"
        assert data["is_active"] is True
        assert data["id"] is not None

    app.dependency_overrides.clear()


def test_create_rule_invalid_ast_operator_rejected():
    """Verify condition AST with unsupported operator returns RFC 7807 400 Bad Request."""
    mock_session = MagicMock()
    mock_session.commit = AsyncMock()

    async def _mock_session_gen():
        yield mock_session

    app.dependency_overrides[get_session] = _mock_session_gen

    payload = {
        "rule_name": "Dangerous Eval Injection Rule",
        "condition": {
            "field": "amount",
            "operator": "eval_code",  # Unrecognized / dangerous operator
            "value": "__import__('os').system('ls')",
        },
        "action": "BLOCK_TRANSACTION",
    }

    with TestClient(app) as client:
        res = client.post("/api/v1/rules", json=payload)
        assert res.status_code == 400
        assert "unsupported operator" in res.json()["detail"].lower()

    app.dependency_overrides.clear()


def test_create_rule_redos_pattern_guard():
    """Verify condition AST with regex pattern exceeding 256 chars is rejected with 400."""
    mock_session = MagicMock()
    mock_session.commit = AsyncMock()

    async def _mock_session_gen():
        yield mock_session

    app.dependency_overrides[get_session] = _mock_session_gen

    payload = {
        "rule_name": "Excessive Regex ReDoS Attack",
        "condition": {
            "field": "user_agent",
            "operator": "matches",
            "value": "a" * 300,  # Exceeds 256 limit
        },
        "action": "FLAG_CRITICAL",
    }

    with TestClient(app) as client:
        res = client.post("/api/v1/rules", json=payload)
        assert res.status_code == 400
        assert "256 characters" in res.json()["detail"]

    app.dependency_overrides.clear()


def test_update_rule_lifecycle():
    """Verify updating rule properties or returning 404 if missing."""
    mock_rule = BusinessRuleModel(
        id="rule_mock_777",
        rule_name="Old Rule Name",
        condition={"field": "velocity_1h", "operator": ">", "value": 10},
        action="REQUIRE_MFA",
        is_active=True,
    )

    mock_session = MagicMock()
    mock_session.commit = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_rule
    mock_session.execute = AsyncMock(return_value=mock_result)

    async def _mock_session_gen():
        yield mock_session

    app.dependency_overrides[get_session] = _mock_session_gen

    with TestClient(app) as client:
        # 1. Update rule
        res = client.put(
            "/api/v1/rules/rule_mock_777",
            json={"rule_name": "New Hardened Rule", "action": "FLAG_CRITICAL"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["rule_name"] == "New Hardened Rule"
        assert data["action"] == "FLAG_CRITICAL"

        # 2. Update nonexistent rule
        mock_result.scalar_one_or_none.return_value = None
        res_404 = client.put(
            "/api/v1/rules/rule_nonexistent",
            json={"action": "BLOCK_TRANSACTION"},
        )
        assert res_404.status_code == 404

    app.dependency_overrides.clear()


def test_delete_rule_lifecycle():
    """Verify deleting a rule returns 204 or 404 when absent."""
    mock_rule = BusinessRuleModel(
        id="rule_to_delete",
        rule_name="Temporary Rule",
        condition={"field": "amount", "operator": ">", "value": 1000},
        action="FLAG_HIGH_RISK",
        is_active=True,
    )

    mock_session = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.delete = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_rule
    mock_session.execute = AsyncMock(return_value=mock_result)

    async def _mock_session_gen():
        yield mock_session

    app.dependency_overrides[get_session] = _mock_session_gen

    with TestClient(app) as client:
        res = client.delete("/api/v1/rules/rule_to_delete")
        assert res.status_code == 204

        # 404 case
        mock_result.scalar_one_or_none.return_value = None
        res_404 = client.delete("/api/v1/rules/rule_missing")
        assert res_404.status_code == 404

    app.dependency_overrides.clear()


def test_dry_run_test_rule_match_and_no_match():
    """Verify testing rule AST returns authentic match outcome and matched fields."""
    with TestClient(app) as client:
        # Match case
        match_payload = {
            "condition": {
                "and": [
                    {"field": "velocity_1h", "operator": ">", "value": 5},
                    {"field": "amount", "operator": ">=", "value": 1000.0},
                ]
            },
            "transaction": {
                "velocity_1h": 8,
                "amount": 2500.0,
                "currency": "EUR",
            },
        }
        res = client.post("/api/v1/rules/test", json=match_payload)
        assert res.status_code == 200
        data = res.json()
        assert data["matches"] is True
        assert "velocity_1h" in data["matched_fields"]
        assert "amount" in data["matched_fields"]

        # No match case
        no_match_payload = {
            "condition": {"field": "velocity_1h", "operator": ">", "value": 10},
            "transaction": {"velocity_1h": 3},
        }
        res_no = client.post("/api/v1/rules/test", json=no_match_payload)
        assert res_no.status_code == 200
        data_no = res_no.json()
        assert data_no["matches"] is False
        assert data_no["matched_fields"] == []


def test_dry_run_test_rule_invalid_ast_rejects_400():
    """Verify malformed condition AST returns RFC 7807 400 Bad Request instead of deceptive 200."""
    with TestClient(app) as client:
        malformed_payload = {
            "condition": {
                "and": "this-should-be-a-list-not-a-string",  # Syntax error
            },
            "transaction": {"amount": 500},
        }
        res = client.post("/api/v1/rules/test", json=malformed_payload)
        assert res.status_code == 400
        assert "must contain a non-empty list" in res.json()["detail"].lower()


def test_evaluate_transaction_policies():
    """Verify multi-rule screening endpoint assesses transactions and computes composite decisions."""
    with TestClient(app) as client:
        # High-risk structuring transaction that triggers rule_smurf_004 (amount between 9000 and 9999)
        txn_smurf = {
            "transaction": {
                "amount": 9500.0,
                "velocity_1h": 2,
                "country_risk_score": 0.2,
                "is_new_device": False,
            }
        }
        res = client.post("/api/v1/rules/evaluate", json=txn_smurf)
        assert res.status_code == 200
        data = res.json()
        assert data["evaluated_rules_count"] >= 4
        assert data["triggered_rules_count"] >= 1
        assert data["highest_severity_action"] == "ESCALATE_TO_SAR"
        assert data["decision"] == "BLOCK"
        assert data["risk_score_delta"] >= 0.50
        assert any(r["rule_id"] == "rule_smurf_004" for r in data["triggered_rules"])
        assert data["latency_ms"] >= 0.0


def test_evaluate_transaction_stop_on_first_match():
    """Verify stop_on_first_match short-circuits evaluation."""
    with TestClient(app) as client:
        txn = {
            "transaction": {
                "amount": 9500.0,
                "velocity_1h": 10,
                "country_risk_score": 0.9,
                "is_new_device": True,
            },
            "stop_on_first_match": True,
        }
        res = client.post("/api/v1/rules/evaluate", json=txn)
        assert res.status_code == 200
        data = res.json()
        assert data["triggered_rules_count"] == 1


def test_dual_routing_parity():
    """Verify identical endpoint behaviors across /api/v1/rules and /v1/rules paths."""
    with TestClient(app) as client:
        res1 = client.get("/api/v1/rules")
        res2 = client.get("/v1/rules")
        assert res1.status_code == 200
        assert res2.status_code == 200
        assert res1.json() == res2.json()

        res_single1 = client.get("/api/v1/rules/rule_vel_001")
        res_single2 = client.get("/v1/rules/rule_vel_001")
        assert res_single1.status_code == 200
        assert res_single2.status_code == 200
        assert res_single1.json() == res_single2.json()
