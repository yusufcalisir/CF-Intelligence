"""Unit tests for hardened Consortium Policy & Dynamic Sharing Rules Engine (Stage 25 / Phase 44)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.application.services.consortium_service import ConsortiumGovernanceService
from app.application.services.policy_engine import (
    PolicyEngineService,
    evaluate_condition,
)
from app.domain.consortium_policy import (
    ConsortiumPolicyConfig,
    ConsortiumPolicyEngine,
    ConsortiumPolicyViolation,
)

# ==============================================================================
# 1. ConsortiumPolicyConfig & Input Validation Tests
# ==============================================================================


def test_consortium_policy_config_validations() -> None:
    """Verifies that ConsortiumPolicyConfig validates member, epsilon, and sample bounds."""
    # Valid default config
    cfg = ConsortiumPolicyConfig()
    assert cfg.min_active_members == 2
    assert cfg.allow_cross_border_sharing is False

    with pytest.raises(ValueError, match="min_active_members must be >= 1"):
        ConsortiumPolicyConfig(min_active_members=0)

    with pytest.raises(ValueError, match="max_epsilon_budget must be positive"):
        ConsortiumPolicyConfig(max_epsilon_budget=-1.0)

    with pytest.raises(ValueError, match="min_data_samples_per_member must be >= 1"):
        ConsortiumPolicyConfig(min_data_samples_per_member=0)


def test_consortium_policy_rejects_empty_bank_roster() -> None:
    """Verifies that an empty participating bank list is rejected immediately."""
    service = ConsortiumGovernanceService()
    consortium = service.create_consortium("c_empty", "Empty Test", "bank_a")
    engine = ConsortiumPolicyEngine()

    is_valid, reasons = engine.validate_fl_round_preconditions(
        consortium=consortium,
        participating_banks=[],
        round_epsilon=1.0,
    )
    assert is_valid is False
    assert any("cannot be empty" in r for r in reasons)


def test_consortium_policy_detects_duplicate_bank_participants() -> None:
    """Verifies that duplicate bank IDs in participating roster are rejected and cannot fake quorum."""
    service = ConsortiumGovernanceService()
    consortium = service.create_consortium("c_dup", "Dup Test", "bank_a")
    engine = ConsortiumPolicyEngine(config=ConsortiumPolicyConfig(min_active_members=2))

    # Single bank duplicated to look like 2 members
    is_valid, reasons = engine.validate_fl_round_preconditions(
        consortium=consortium,
        participating_banks=["bank_a", "bank_a"],
        round_epsilon=1.0,
    )
    assert is_valid is False
    assert any("Duplicate bank participant IDs" in r for r in reasons)
    assert any("Insufficient participating members" in r for r in reasons)


def test_consortium_policy_rejects_invalid_epsilon() -> None:
    """Verifies rejection of non-positive, non-finite or NaN epsilon parameters."""
    service = ConsortiumGovernanceService()
    consortium = service.create_consortium("c_eps", "Eps Test", "bank_a")
    service.propose_membership_change("c_eps", "bank_a", "bank_b")
    engine = ConsortiumPolicyEngine(config=ConsortiumPolicyConfig(min_active_members=2))

    # Negative epsilon
    is_valid, reasons = engine.validate_fl_round_preconditions(
        consortium=consortium,
        participating_banks=["bank_a", "bank_b"],
        round_epsilon=-0.5,
    )
    assert is_valid is False
    assert any("invalid. Must be strictly positive" in r for r in reasons)

    # NaN epsilon
    is_valid, reasons = engine.validate_fl_round_preconditions(
        consortium=consortium,
        participating_banks=["bank_a", "bank_b"],
        round_epsilon=float("nan"),
    )
    assert is_valid is False
    assert any("invalid. Must be strictly positive" in r for r in reasons)


# ==============================================================================
# 2. Cross-Border Sovereignty & Feature Sharing Tests
# ==============================================================================


def test_cross_border_sharing_blocked_without_waiver() -> None:
    """Verifies that multi-region FL rounds are blocked when cross-border sharing is disabled."""
    service = ConsortiumGovernanceService()
    consortium = service.create_consortium("c_cross", "Cross-Border Test", "bank_eu")
    service.propose_membership_change("c_cross", "bank_eu", "bank_us")

    engine = ConsortiumPolicyEngine(
        config=ConsortiumPolicyConfig(
            min_active_members=2,
            allow_cross_border_sharing=False,
        )
    )

    regions = {
        "bank_eu": "eu-central-1",
        "bank_us": "us-east-1",
    }

    is_valid, reasons = engine.validate_fl_round_preconditions(
        consortium=consortium,
        participating_banks=["bank_eu", "bank_us"],
        round_epsilon=1.0,
        participant_regions=regions,
    )
    assert is_valid is False
    assert any("Cross-border feature and model sharing" in r for r in reasons)


def test_cross_border_sharing_allowed_with_sovereignty_waiver() -> None:
    """Verifies that multi-region FL rounds pass when cross-border sharing is explicitly enabled."""
    service = ConsortiumGovernanceService()
    consortium = service.create_consortium("c_cross_ok", "Cross-Border Allowed", "bank_eu")
    service.propose_membership_change("c_cross_ok", "bank_eu", "bank_us")

    engine = ConsortiumPolicyEngine(
        config=ConsortiumPolicyConfig(
            min_active_members=2,
            allow_cross_border_sharing=True,
        )
    )

    regions = {
        "bank_eu": "eu-central-1",
        "bank_us": "us-east-1",
    }

    is_valid, reasons = engine.validate_fl_round_preconditions(
        consortium=consortium,
        participating_banks=["bank_eu", "bank_us"],
        round_epsilon=1.0,
        participant_regions=regions,
    )
    assert is_valid is True
    assert len(reasons) == 0


def test_unapproved_jurisdiction_blocked() -> None:
    """Verifies that a bank in an unauthorized jurisdiction is flagged by policy."""
    service = ConsortiumGovernanceService()
    consortium = service.create_consortium("c_unauth_geo", "Jurisdiction Test", "bank_eu")
    service.propose_membership_change("c_unauth_geo", "bank_eu", "bank_unknown")

    engine = ConsortiumPolicyEngine(
        config=ConsortiumPolicyConfig(
            min_active_members=2,
            allowed_regions=["eu-central-1", "us-east-1"],
        )
    )

    regions = {
        "bank_eu": "eu-central-1",
        "bank_unknown": "sanctioned-zone-9",
    }

    is_valid, reasons = engine.validate_fl_round_preconditions(
        consortium=consortium,
        participating_banks=["bank_eu", "bank_unknown"],
        round_epsilon=1.0,
        participant_regions=regions,
    )
    assert is_valid is False
    assert any("operates in unapproved jurisdiction" in r for r in reasons)


def test_restricted_features_governance() -> None:
    """Verifies that sharing forbidden PII features (SSN, IBAN, Tax ID) is blocked."""
    service = ConsortiumGovernanceService()
    consortium = service.create_consortium("c_feat", "Features Test", "bank_a")
    service.propose_membership_change("c_feat", "bank_a", "bank_b")

    engine = ConsortiumPolicyEngine(config=ConsortiumPolicyConfig(min_active_members=2))

    # Round attempting to share restricted features
    is_valid, reasons = engine.validate_fl_round_preconditions(
        consortium=consortium,
        participating_banks=["bank_a", "bank_b"],
        round_epsilon=1.0,
        shared_features=["velocity_1h", "raw_iban", "country_risk"],
    )
    assert is_valid is False
    assert any("Feature 'raw_iban' is restricted" in r for r in reasons)


def test_minimum_data_sample_contribution_enforcement() -> None:
    """Verifies that banks with insufficient local sample counts are blocked from round."""
    service = ConsortiumGovernanceService()
    consortium = service.create_consortium("c_samples", "Sample Count Test", "bank_a")
    service.propose_membership_change("c_samples", "bank_a", "bank_b")

    engine = ConsortiumPolicyEngine(
        config=ConsortiumPolicyConfig(
            min_active_members=2,
            min_data_samples_per_member=500,
        )
    )

    sample_counts = {
        "bank_a": 1200,
        "bank_b": 50,  # Below 500
    }

    is_valid, reasons = engine.validate_fl_round_preconditions(
        consortium=consortium,
        participating_banks=["bank_a", "bank_b"],
        round_epsilon=1.0,
        member_sample_counts=sample_counts,
    )
    assert is_valid is False
    assert any("provides insufficient data samples (50 < min 500)" in r for r in reasons)


def test_validate_cross_border_sharing_direct_helper() -> None:
    """Verifies direct evaluate helper for Schrems II / GDPR Article 22 compliance."""
    engine = ConsortiumPolicyEngine(
        config=ConsortiumPolicyConfig(
            allowed_regions=["eu-central-1", "us-east-1"],
            allow_cross_border_sharing=False,
        )
    )

    # 1. Unapproved destination
    valid, reasons = engine.validate_cross_border_sharing(
        source_region="eu-central-1",
        destination_region="unapproved-zone",
        features=["velocity"],
    )
    assert valid is False
    assert any("Destination region 'unapproved-zone' is not in approved" in r for r in reasons)

    # 2. Cross-border raw transfer without DP scrubbing
    valid, reasons = engine.validate_cross_border_sharing(
        source_region="eu-central-1",
        destination_region="us-east-1",
        features=["velocity"],
        is_dp_enforced=False,
    )
    assert valid is False
    assert any("without Differential Privacy scrubbing is strictly prohibited" in r for r in reasons)

    # 3. Restricted feature crossing border
    valid, reasons = engine.validate_cross_border_sharing(
        source_region="eu-central-1",
        destination_region="us-east-1",
        features=["velocity", "raw_ssn"],
        is_dp_enforced=True,
    )
    assert valid is False
    assert any("Restricted feature 'raw_ssn' cannot cross" in r for r in reasons)

    # 4. Fully compliant cross-border DP transfer
    engine_waiver = ConsortiumPolicyEngine(
        config=ConsortiumPolicyConfig(
            allowed_regions=["eu-central-1", "us-east-1"],
            allow_cross_border_sharing=True,
        )
    )
    valid, reasons = engine_waiver.validate_cross_border_sharing(
        source_region="eu-central-1",
        destination_region="us-east-1",
        features=["velocity", "risk_score"],
        is_dp_enforced=True,
    )
    assert valid is True
    assert len(reasons) == 0


def test_enforce_fl_round_preconditions_raises_violation() -> None:
    """Verifies enforce_fl_round_preconditions raises ConsortiumPolicyViolation with all combined reasons."""
    service = ConsortiumGovernanceService()
    consortium = service.create_consortium("c_viol", "Violation Test", "bank_a")
    engine = ConsortiumPolicyEngine(config=ConsortiumPolicyConfig(min_active_members=2))

    with pytest.raises(ConsortiumPolicyViolation) as exc_info:
        engine.enforce_fl_round_preconditions(
            consortium=consortium,
            participating_banks=["bank_a"],  # Insufficient members
            round_epsilon=10.0,  # Exceeds max 5.0
            architecture="Unsupported_CNN",  # Unsupported
        )

    err = str(exc_info.value)
    assert "Insufficient participating members" in err
    assert "exceeds max allowed limit" in err
    assert "Model architecture 'Unsupported_CNN' is not allowed" in err


# ==============================================================================
# 3. Dynamic Policy AST Operator Extensions & PolicyEngineService Tests
# ==============================================================================


def test_ast_between_operator_variations() -> None:
    """Verifies evaluate_condition correctly evaluates 'between' range operators across formats."""
    ctx = {"amount": 9500, "velocity": 4.5}

    # 1. Format with min_value and max_value
    cond_min_max = {
        "field": "amount",
        "operator": "between",
        "min_value": 9000,
        "max_value": 9999,
    }
    assert evaluate_condition(cond_min_max, ctx) is True

    # Out of range
    assert evaluate_condition(cond_min_max, {"amount": 8999}) is False
    assert evaluate_condition(cond_min_max, {"amount": 10000}) is False

    # 2. Format with value as list [min, max]
    cond_list = {"field": "amount", "operator": "between", "value": [9000, 9999]}
    assert evaluate_condition(cond_list, ctx) is True
    assert evaluate_condition(cond_list, {"amount": 8000}) is False

    # 3. Format with value as dict {"min": ..., "max": ...}
    cond_dict = {"field": "amount", "operator": "between", "value": {"min": 9000, "max": 9999}}
    assert evaluate_condition(cond_dict, ctx) is True

    # 4. Invalid or malformed between inputs
    assert evaluate_condition({"field": "amount", "operator": "between", "value": "not-a-range"}, ctx) is False


def test_ast_contains_and_regex_operators() -> None:
    """Verifies 'contains', 'not contains', and 'regex' / 'matches' operators."""
    ctx = {
        "description": "Suspicious rapid wire layering to offshore entity",
        "account_tags": ["high_velocity", "pep_adjacent", "cross_border"],
        "iban": "GB82WEST12345698765432",
    }

    # 'contains' substring
    assert evaluate_condition(
        {"field": "description", "operator": "contains", "value": "wire layering"}, ctx
    ) is True
    assert evaluate_condition(
        {"field": "description", "operator": "contains", "value": "clean retail"}, ctx
    ) is False

    # 'contains' in list
    assert evaluate_condition(
        {"field": "account_tags", "operator": "contains", "value": "pep_adjacent"}, ctx
    ) is True
    assert evaluate_condition(
        {"field": "account_tags", "operator": "not contains", "value": "clean_flow"}, ctx
    ) is True

    # 'matches' / 'regex'
    assert evaluate_condition(
        {"field": "iban", "operator": "regex", "value": r"^GB\d{2}[A-Z]{4}\d{14}$"}, ctx
    ) is True
    assert evaluate_condition(
        {"field": "iban", "operator": "matches", "value": r"^DE\d{20}$"}, ctx
    ) is False


def test_ast_boolean_value_comparison() -> None:
    """Verifies clean boolean type comparison against booleans and string booleans."""
    assert evaluate_condition(
        {"field": "is_mule", "operator": "==", "value": True},
        {"is_mule": True},
    ) is True

    assert evaluate_condition(
        {"field": "is_mule", "operator": "==", "value": True},
        {"is_mule": "true"},
    ) is True

    assert evaluate_condition(
        {"field": "is_mule", "operator": "!=", "value": True},
        {"is_mule": False},
    ) is True


@pytest.mark.asyncio
async def test_policy_engine_service_input_validations() -> None:
    """Verifies PolicyEngineService validates rule names, conditions, and actions."""
    service = PolicyEngineService()
    session = AsyncMock()
    session.add = MagicMock()

    # Empty rule_name
    with pytest.raises(ValueError, match="Rule name must be a non-empty string"):
        await service.create_rule(session, "", {"field": "x", "operator": "==", "value": 1})

    # Empty or non-dict condition
    with pytest.raises(ValueError, match="Condition must be a non-empty dictionary AST"):
        await service.create_rule(session, "Valid Name", {})

    # Empty action
    with pytest.raises(ValueError, match="Action must be a non-empty string"):
        await service.create_rule(session, "Valid Name", {"field": "x", "operator": "==", "value": 1}, action="")

    # Update validations
    with pytest.raises(ValueError, match="Rule name cannot be empty"):
        await service.update_rule(session, "rule-1", rule_name="")

    with pytest.raises(ValueError, match="Condition must be a non-empty dictionary AST"):
        await service.update_rule(session, "rule-1", condition={})
