"""Robustness and Failure Injection Test Suite for Risk Scoring Engine Module.

Attempts to break every scoring rule:
1. Empty Transaction Payload ({})
2. Malformed / Non-Dict Transaction Payloads
3. NaN Floating-Point Value Injection
4. Infinite Floating-Point (+Inf / -Inf) Injection
5. Unknown / Invalid Merchant Categories
6. Unknown Country Codes & Case-Sensitivity Sanctions Bug
7. Unsupported Device Types
8. Invalid Customer History Scores (Negative & Out-of-Bounds)
9. Extremely Large Numerical Values (1e308)
10. Policy Engine AST Malformed Rule Evaluation
"""

import math
import sys
from pathlib import Path
from typing import Any, cast

import pytest

backend_path = Path(__file__).resolve().parents[3] / "backend"
sys.path.insert(0, str(backend_path))

from app.application.services.policy_engine import evaluate_condition  # noqa: E402
from app.application.services.risk_engine import RiskScoringEngine  # noqa: E402
from app.domain.value_objects_phase2 import RiskScore  # noqa: E402

engine = RiskScoringEngine()


# =====================================================================
# ROBUSTNESS TEST SUITE
# =====================================================================


def test_robustness_1_empty_transaction_payload():
    """ROBUSTNESS 1: Empty Transaction Payload ({})."""
    res = engine.score_transaction({}, ml_prediction=0.5, entity_hash="")
    assert isinstance(res, RiskScore)
    assert 0.0 <= res.score <= 1000.0
    assert len(res.signals) == 9


def test_robustness_2_malformed_non_dict_payload():
    """ROBUSTNESS 2: Malformed / Non-Dict Transaction Payloads."""
    # Dict copy behavior in score_transaction requires dict-like payload
    with pytest.raises((AttributeError, TypeError)):
        engine.score_transaction(cast(Any, "not_a_dict"), ml_prediction=0.5)


def test_robustness_3_nan_floating_point_injection():
    """ROBUSTNESS 3: NaN Floating-Point Value Injection."""
    nan_txn = {
        "velocity": float("nan"),
        "merchant_risk_score": float("nan"),
        "transaction_amount": float("nan"),
        "customer_history_score": float("nan"),
    }
    # Test execution doesn't raise unhandled crash
    try:
        res = engine.score_transaction(nan_txn, ml_prediction=0.5, entity_hash="")
        assert isinstance(res, RiskScore)
    except Exception as exc:
        pytest.fail(f"NaN injection caused unhandled exception: {exc}")


def test_robustness_4_infinite_value_injection():
    """ROBUSTNESS 4: Infinite Floating-Point (+Inf / -Inf) Injection."""
    inf_txn = {
        "velocity": float("inf"),
        "merchant_risk_score": float("inf"),
        "transaction_amount": float("inf"),
    }
    res = engine.score_transaction(inf_txn, ml_prediction=float("inf"), entity_hash="")
    assert isinstance(res, RiskScore)
    # min(1.0, inf) caps signal scores to 1.0 gracefully
    assert res.score == 1000.0 or 0.0 <= res.score <= 1000.0


def test_robustness_5_unknown_merchant_category():
    """ROBUSTNESS 5: Unknown / Invalid Merchant Category."""
    txn = {"merchant_category": "illegal_weapons_smuggling"}
    sig = engine._eval_merchant_reputation(txn)
    # Default merchant risk score is 0.10 and default category risk is 0.10 -> 0.6*0.1 + 0.4*0.1 = 0.10
    assert sig.normalized_score == pytest.approx(0.10)
    assert "risk: 10%" in sig.explanation


def test_robustness_6_unknown_country_and_casing_bug():
    """ROBUSTNESS 6: Unknown Country Codes & Case-Sensitivity Sanctions Bug."""
    # Case 6a: Completely unknown country code -> defaults to 0.15
    sig_unknown = engine._eval_country_risk({"country_code": "ZZ"})
    assert sig_unknown.normalized_score == 0.15

    # Case 6b: Sanctioned country lowercase "kp" (North Korea) -> Case-insensitive lookup maps to 1.00!
    sig_lowercase = engine._eval_country_risk({"country_code": "kp"})
    sig_uppercase = engine._eval_country_risk({"country_code": "KP"})

    assert sig_uppercase.normalized_score == 1.00
    assert sig_lowercase.normalized_score == 1.00, (
        "Case-insensitive sanctions lookup verified"
    )


def test_robustness_7_unsupported_device_type():
    """ROBUSTNESS 7: Unsupported Device Types."""
    txn = {"device_type": "quantum_neural_implant"}
    sig = engine._eval_device_anomaly(txn)
    assert sig.normalized_score == 0.20  # Default fallback score


def test_robustness_8_invalid_customer_history_scores():
    """ROBUSTNESS 8: Invalid Customer History Scores (Negative & Out-of-Bounds)."""
    # Case 8a: High customer trust score 1.0 -> min risk
    sig_good = engine._eval_customer_history(
        {"customer_history_score": 1.0, "account_age_days": 365}
    )
    assert sig_good.normalized_score == 0.0

    # Case 8b: Low customer trust score 0.0 -> max risk
    sig_bad = engine._eval_customer_history(
        {"customer_history_score": 0.0, "account_age_days": 365}
    )
    assert sig_bad.normalized_score == 1.0

    # Case 8c: New account penalty (< 30 days) adds +0.30
    sig_new = engine._eval_customer_history(
        {"customer_history_score": 0.5, "account_age_days": 10}
    )
    assert abs(sig_new.normalized_score - 0.8) < 1e-6


def test_robustness_9_extremely_large_numerical_values():
    """ROBUSTNESS 9: Extremely Large Numerical Values (1e308)."""
    huge_txn = {
        "velocity": 1e308,
        "transaction_amount": 1e308,
        "account_age_days": 10**9,
    }
    res = engine.score_transaction(huge_txn, ml_prediction=1.0, entity_hash="")
    assert isinstance(res, RiskScore)
    assert not math.isnan(res.score)
    assert not math.isinf(res.score)


def test_robustness_10_policy_engine_ast_malformed_rules():
    """ROBUSTNESS 10: Policy Engine AST Malformed Rule Evaluation."""
    ctx = {"composite_risk_score": 850.0, "country_code": "US"}

    # Case 10a: Valid AST evaluation
    cond_valid = {"field": "composite_risk_score", "operator": ">=", "value": 800.0}
    assert evaluate_condition(cond_valid, ctx) is True

    # Case 10b: Malformed "and" operator (not a list)
    cond_bad_and = {"and": "invalid_string_not_a_list"}
    assert evaluate_condition(cond_bad_and, ctx) is False

    # Case 10c: Missing field in context
    cond_missing_field = {"field": "non_existent_field", "operator": "==", "value": 100}
    assert evaluate_condition(cond_missing_field, ctx) is False

    # Case 10d: Invalid comparison type (comparing string to float)
    cond_type_mismatch = {"field": "country_code", "operator": ">", "value": 500}
    assert (
        evaluate_condition(cond_type_mismatch, ctx) is False
    )  # Catches exception gracefully and returns False
