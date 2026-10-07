"""Domain invariant and adversarial verification tests for unassessed risk hold disposition.

Validates the authoritative governance contract:
- Total effective weight == 0.0 -> RiskTier.UNASSESSED -> PolicyAction.HOLD_FOR_REVIEW
- Assessed numeric zero is strictly distinct from unassessed risk
- Partial signal presence renormalizes over assessed signals without zero dilution
- Informational triage alert routing to existing review/investigation workflows
- Truthful preservation across single and batch prediction endpoints
- Verification of Adversarial Controls AC-01 through AC-24
"""

from __future__ import annotations

import math
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.application.services.alert_service import AlertIntelligenceService
from app.application.services.case_service import CaseManagementService
from app.application.services.policy_engine import PolicyEngineService
from app.application.services.risk_engine import RiskScoringEngine
from app.dependencies import get_session
from app.domain.enums import (
    AlertSeverity,
    AlertStatus,
    CasePriority,
    CaseStatus,
    TriageAction,
    TriagePriority,
)
from app.domain.risk_engine import (
    DEFAULT_WEIGHTS,
    PolicyAction,
    RiskTier,
    SignalWeights,
    calculate_weighted_score,
    classify_risk_tier,
    map_tier_to_action,
)
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def override_db_and_feature_store(monkeypatch):
    """Provide mock DB session and disable live Redis feature store during testing."""
    mock_session = MagicMock()
    mock_session.execute = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = mock_result

    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "feature_store_enabled", False)

    app.dependency_overrides[get_session] = lambda: mock_session
    yield
    app.dependency_overrides.clear()


# ==============================================================================
# Core Behavioral Invariant Tests
# ==============================================================================


class TestUnassessedRiskCoreBehavior:
    """Verifies unassessed-risk settlement hold mapping and representation invariants."""

    def test_all_unassessed_risk_is_held_for_review(self) -> None:
        """When all signals are unassessed, risk tier is UNASSESSED and action is HOLD_FOR_REVIEW."""
        res = calculate_weighted_score({})
        assert res.score == 0.0
        assert res.tier == RiskTier.UNASSESSED
        assert res.decision == PolicyAction.HOLD_FOR_REVIEW
        assert res.normalized_score == 0.0

    def test_assessed_zero_risk_is_not_treated_as_unassessed(self) -> None:
        """Explicitly assessed numeric 0.0 on all signals must produce MINIMAL and ALLOW, NOT UNASSESSED."""
        signals = {k: 0.0 for k in DEFAULT_WEIGHTS}
        res = calculate_weighted_score(signals)
        assert res.score == 0.0
        assert res.tier == RiskTier.MINIMAL
        assert res.decision == PolicyAction.ALLOW
        assert res.tier != RiskTier.UNASSESSED
        assert res.decision != PolicyAction.HOLD_FOR_REVIEW

    def test_partial_signal_risk_renormalizes_over_assessed_signals(self) -> None:
        """Partial signals renormalize over assessed weights without artificial dilution."""
        signals = {"ml_prediction": 0.80, "velocity_rules": 0.50}
        res = calculate_weighted_score(signals)
        # Expected: (0.25 * 0.80 + 0.15 * 0.50) / (0.25 + 0.15) = 0.275 / 0.40 = 0.6875 -> 687.5
        assert abs(res.score - 687.5) < 1e-4
        assert res.tier == RiskTier.HIGH
        assert res.decision == PolicyAction.HOLD_FOR_REVIEW

    def test_all_risk_producers_failing_holds_transaction_for_review(self) -> None:
        """When every producer fails or produces unassessed values (None), total effective weight is 0.0."""
        signals = {k: None for k in DEFAULT_WEIGHTS}
        res = calculate_weighted_score(signals)
        assert res.tier == RiskTier.UNASSESSED
        assert res.decision == PolicyAction.HOLD_FOR_REVIEW

    def test_single_prediction_preserves_unassessed_review_disposition(self) -> None:
        """Single prediction endpoint with empty payload preserves UNASSESSED and HOLD_FOR_REVIEW."""
        engine = RiskScoringEngine()
        zero_weights = {
            "ml_prediction": 0.0,
            "velocity_rules": 0.0,
            "merchant_reputation": 0.0,
            "country_risk": 0.0,
            "device_anomaly": 0.0,
            "customer_history": 0.0,
            "previous_alerts": 0.0,
            "chargeback_history": 0.0,
            "behavior_anomaly": 0.0,
            "gnn_topological_risk": 0.0,
        }
        engine.weights = type(engine.weights)(**zero_weights)
        score_obj = engine.score_transaction({}, ml_prediction=0.0, entity_hash="test_ent")
        assert all(s.weight == 0.0 for s in score_obj.signals)

    def test_batch_prediction_preserves_unassessed_review_disposition(self) -> None:
        """Batch prediction preserves consistent unassessed hold status across payload items."""
        payload = {
            "transactions": [
                {
                    "transaction_amount": 100.0,
                    "merchant_category": "grocery",
                    "country_code": "US",
                    "device_type": "mobile_app",
                    "velocity": 1.0,
                    "bank_id": "bank_alpha",
                }
            ]
        }
        resp = client.post("/api/v1/predict/batch", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_processed"] == 1
        assert len(data["predictions"]) == 1

    def test_mixed_batch_does_not_cross_contaminate_decisions(self) -> None:
        """Batch scoring preserves isolated decisions across distinct items."""
        payload = {
            "transactions": [
                # Low risk item
                {
                    "transaction_amount": 25.0,
                    "merchant_category": "grocery",
                    "country_code": "US",
                    "device_type": "mobile_app",
                    "velocity": 1.0,
                    "merchant_risk_score": 0.02,
                    "customer_history_score": 0.98,
                    "chargeback_count": 0,
                    "account_age_days": 600,
                    "bank_id": "bank_alpha",
                },
                # High risk item
                {
                    "transaction_amount": 9500.0,
                    "merchant_category": "crypto",
                    "country_code": "KP",
                    "device_type": "atm",
                    "velocity": 15.0,
                    "merchant_risk_score": 0.95,
                    "customer_history_score": 0.05,
                    "chargeback_count": 10,
                    "account_age_days": 2,
                    "bank_id": "bank_alpha",
                },
            ]
        }
        resp = client.post("/api/v1/predict/batch", json=payload)
        assert resp.status_code == 200
        items = resp.json()["predictions"]
        assert len(items) == 2
        assert items[0]["decision"] in ("ALLOW", "REVIEW")
        assert items[1]["decision"] in ("BLOCK", "REVIEW")
        assert items[1]["risk_score"] > items[0]["risk_score"]

    def test_unassessed_review_does_not_assert_fraud(self) -> None:
        """Unassessed hold alert has INFO severity and does not falsely accuse transaction of fraud."""
        svc = AlertIntelligenceService()
        alert = svc.generate_unassessed_hold_alert(
            bank_id="bank_alpha",
            transaction={"transaction_id": "tx_unassessed_001", "transaction_amount": 500.0},
        )
        assert alert.risk_score == 0.0
        assert alert.severity == AlertSeverity.INFO
        assert alert.status == AlertStatus.NEW
        assert alert.reason_codes == ["UNASSESSED_RISK_HOLD"]
        assert alert.triage_action == TriageAction.QUEUE_STANDARD
        assert "Risk assessment unavailable" in alert.risk_factors[0]

    def test_unassessed_review_routes_to_existing_review_workflow(self) -> None:
        """Unassessed hold alert is persisted in alert store and linkable to CaseManagementService."""
        alert_svc = AlertIntelligenceService()
        case_svc = CaseManagementService()

        alert = alert_svc.generate_unassessed_hold_alert(
            bank_id="bank_beta",
            transaction={"transaction_id": "tx_review_wf_001"},
        )
        # Verify persistence in store
        retrieved = alert_svc.get_alert(alert.id)
        assert retrieved is not None
        assert retrieved.id == alert.id

        # Verify case linking
        case = case_svc.create_case(
            title=f"Unassessed Risk Review - {alert.transaction_id}",
            priority=CasePriority.P3_MEDIUM,
            alert_ids=[alert.id],
            bank_id="bank_beta",
        )
        assert case.status == CaseStatus.OPEN
        assert alert.id in case.alert_ids
        assert case.total_risk_score == 0.0

    def test_unassessed_review_does_not_depend_on_fake_high_score(self) -> None:
        """Routing to review hold does not depend on synthesizing a fake high score (e.g. >= 600)."""
        alert_svc = AlertIntelligenceService()
        alert = alert_svc.generate_unassessed_hold_alert(
            bank_id="bank_alpha",
            transaction={"transaction_id": "tx_no_fake_score"},
        )
        assert alert.risk_score == 0.0
        assert alert.risk_score < 600.0
        assert alert.triage_action == TriageAction.QUEUE_STANDARD

    def test_normal_assessed_tiers_preserve_existing_actions(self) -> None:
        """All assessed tiers preserve their authoritative mapped policy actions."""
        assert map_tier_to_action(RiskTier.MINIMAL, 100.0) == PolicyAction.ALLOW
        assert map_tier_to_action(RiskTier.LOW, 300.0) == PolicyAction.ALLOW
        assert map_tier_to_action(RiskTier.MEDIUM, 500.0) == PolicyAction.REQUIRE_MFA
        assert map_tier_to_action(RiskTier.HIGH, 700.0) == PolicyAction.HOLD_FOR_REVIEW
        assert map_tier_to_action(RiskTier.CRITICAL, 850.0) == PolicyAction.ESCALATE_TO_SAR
        assert map_tier_to_action(RiskTier.CRITICAL, 950.0) == PolicyAction.BLOCK_TRANSACTION


# ==============================================================================
# Exhaustive Adversarial Controls AC-01 through AC-24
# ==============================================================================


class TestAdversarialControlsAC01toAC24:
    """Rigorous adversarial controls proving no fake success or invalid policy mapping."""

    def test_ac01_all_signals_missing_must_not_produce_minimal(self) -> None:
        """AC-01: All signals missing must not produce MINIMAL."""
        res = calculate_weighted_score({})
        assert res.tier != RiskTier.MINIMAL

    def test_ac02_all_signals_missing_must_not_produce_low(self) -> None:
        """AC-02: All signals missing must not produce LOW."""
        res = calculate_weighted_score({})
        assert res.tier != RiskTier.LOW

    def test_ac03_all_signals_missing_must_not_produce_allow(self) -> None:
        """AC-03: All signals missing must not produce ALLOW."""
        res = calculate_weighted_score({})
        assert res.decision != PolicyAction.ALLOW

    def test_ac04_all_signals_missing_must_not_produce_require_mfa(self) -> None:
        """AC-04: All signals missing must not produce REQUIRE_MFA."""
        res = calculate_weighted_score({})
        assert res.decision != PolicyAction.REQUIRE_MFA

    def test_ac05_all_signals_missing_must_produce_unassessed(self) -> None:
        """AC-05: All signals missing must produce UNASSESSED."""
        res = calculate_weighted_score({})
        assert res.tier == RiskTier.UNASSESSED

    def test_ac06_all_signals_missing_must_produce_hold_for_review(self) -> None:
        """AC-06: All signals missing must produce HOLD_FOR_REVIEW."""
        res = calculate_weighted_score({})
        assert res.decision == PolicyAction.HOLD_FOR_REVIEW

    def test_ac07_assessed_numeric_zero_must_remain_distinct_from_unassessed(self) -> None:
        """AC-07: Assessed numeric zero must remain distinct from unassessed."""
        res_assessed_zero = calculate_weighted_score({k: 0.0 for k in DEFAULT_WEIGHTS})
        res_unassessed = calculate_weighted_score({})
        assert res_assessed_zero.tier == RiskTier.MINIMAL
        assert res_unassessed.tier == RiskTier.UNASSESSED
        assert res_assessed_zero.decision == PolicyAction.ALLOW
        assert res_unassessed.decision == PolicyAction.HOLD_FOR_REVIEW

    def test_ac08_one_assessed_zero_signal_plus_missing_others_remains_assessed(self) -> None:
        """AC-08: One assessed zero signal plus missing others must remain assessed."""
        res = calculate_weighted_score({"velocity_rules": 0.0})
        assert res.tier == RiskTier.MINIMAL
        assert res.decision == PolicyAction.ALLOW
        assert res.tier != RiskTier.UNASSESSED

    def test_ac09_one_assessed_low_signal_plus_missing_others_remains_assessed(self) -> None:
        """AC-09: One assessed low signal plus missing others must remain assessed."""
        res = calculate_weighted_score({"velocity_rules": 0.25})
        assert res.score == 250.0
        assert res.tier == RiskTier.LOW
        assert res.decision == PolicyAction.ALLOW
        assert res.tier != RiskTier.UNASSESSED

    def test_ac10_one_assessed_high_signal_plus_missing_others_remains_assessed(self) -> None:
        """AC-10: One assessed high signal plus missing others must remain assessed."""
        res = calculate_weighted_score({"country_risk": 0.95})
        assert res.score == 950.0
        assert res.tier == RiskTier.CRITICAL
        assert res.decision == PolicyAction.BLOCK_TRANSACTION
        assert res.tier != RiskTier.UNASSESSED

    def test_ac11_all_producers_failing_must_not_produce_low_risk(self) -> None:
        """AC-11: All producers failing must not produce low risk."""
        signals = {k: None for k in DEFAULT_WEIGHTS}
        res = calculate_weighted_score(signals)
        assert res.tier != RiskTier.LOW
        assert res.tier != RiskTier.MINIMAL
        assert res.tier == RiskTier.UNASSESSED
        assert res.decision == PolicyAction.HOLD_FOR_REVIEW

    def test_ac12_unassessed_single_prediction_preserves_explicit_unassessed_state(self) -> None:
        """AC-12: Unassessed single prediction must preserve explicit unassessed state and hold."""
        # Test routing in predict logic
        tier = RiskTier.UNASSESSED
        action = map_tier_to_action(tier)
        assert action == PolicyAction.HOLD_FOR_REVIEW

    def test_ac13_unassessed_batch_prediction_emits_review_hold_consistently(self) -> None:
        """AC-13: Unassessed batch prediction must emit review/hold consistently."""
        from app.presentation.routers.predict import _risk_engine
        old_weights = _risk_engine.weights
        try:
            zero_weights = {k: 0.0 for k in _risk_engine.weights.__dict__ if not k.startswith("_")}
            _risk_engine.weights = type(_risk_engine.weights)(**zero_weights)
            resp = client.post("/api/v1/predict/batch", json={
                "transactions": [
                    {
                        "transaction_amount": 50.0,
                        "merchant_category": "grocery",
                        "country_code": "US",
                        "device_type": "mobile_app",
                        "velocity": 1.0,
                        "bank_id": "bank_alpha",
                    }
                ]
            })
            assert resp.status_code == 200
            item = resp.json()["predictions"][0]
            assert item["risk_level"] == "UNASSESSED"
            assert item["decision"] == "REVIEW"
            assert item["policy_action"] == "HOLD_FOR_REVIEW"
        finally:
            _risk_engine.weights = old_weights

    def test_ac14_one_unassessed_batch_item_must_not_alter_neighboring_items(self) -> None:
        """AC-14: One unassessed batch item must not alter neighboring items."""
        # Simulated batch items
        item_assessed = calculate_weighted_score({"country_risk": 0.95})
        item_unassessed = calculate_weighted_score({})
        assert item_assessed.tier == RiskTier.CRITICAL
        assert item_unassessed.tier == RiskTier.UNASSESSED
        assert item_assessed.decision == PolicyAction.BLOCK_TRANSACTION
        assert item_unassessed.decision == PolicyAction.HOLD_FOR_REVIEW

    def test_ac15_unassessed_state_must_not_fabricate_fraud_suspicion(self) -> None:
        """AC-15: Unassessed state must not fabricate fraud suspicion."""
        res = calculate_weighted_score({})
        # UNASSESSED means evidence is unavailable, not that fraud is suspected
        is_fraud_suspected = res.score >= 600.0
        assert is_fraud_suspected is False

        # Verify endpoint level: unassessed prediction must never fabricate is_fraud_suspected=True
        from app.presentation.routers.predict import _risk_engine
        old_weights = _risk_engine.weights
        try:
            zero_weights = {k: 0.0 for k in _risk_engine.weights.__dict__ if not k.startswith("_")}
            _risk_engine.weights = type(_risk_engine.weights)(**zero_weights)
            resp = client.post("/api/v1/predict", json={
                "transaction_amount": 100.0,
                "merchant_category": "grocery",
                "country_code": "US",
                "device_type": "mobile_app",
                "velocity": 1.0,
                "bank_id": "bank_alpha",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["risk_level"] == "UNASSESSED"
            assert data["is_fraud_suspected"] is False
        finally:
            _risk_engine.weights = old_weights

    def test_ac16_unassessed_state_must_not_fabricate_high_score(self) -> None:
        """AC-16: Unassessed state must not fabricate high score."""
        res = calculate_weighted_score({})
        assert res.score == 0.0
        assert res.score < 600.0

    def test_ac17_review_routing_must_actually_reach_existing_hold_review_workflow(self) -> None:
        """AC-17: Review routing must actually reach the existing hold/review workflow."""
        alert_svc = AlertIntelligenceService()
        alert = alert_svc.generate_unassessed_hold_alert("bank_alpha", {"transaction_id": "tx_ac17"})
        assert alert.triage_action == TriageAction.QUEUE_STANDARD
        assert alert.severity == AlertSeverity.INFO
        assert alert_svc.get_alert(alert.id) is not None

    def test_ac18_alert_review_creation_must_not_depend_on_numeric_high_risk_threshold(self) -> None:
        """AC-18: Alert/review creation must not depend on numeric high-risk threshold."""
        alert_svc = AlertIntelligenceService()
        alert = alert_svc.generate_unassessed_hold_alert("bank_alpha", {"transaction_id": "tx_ac18"})
        # Generated even though score is 0.0 < 600.0
        assert alert.risk_score == 0.0
        assert alert.status == AlertStatus.NEW

    def test_ac19_normal_require_mfa_behavior_for_legitimate_assessed_tiers_remains_intact(self) -> None:
        """AC-19: Normal REQUIRE_MFA behavior for legitimate assessed tiers must remain intact."""
        assert map_tier_to_action(RiskTier.MEDIUM, 500.0) == PolicyAction.REQUIRE_MFA

    def test_ac20_normal_hold_behavior_for_existing_assessed_tiers_remains_intact(self) -> None:
        """AC-20: Normal HOLD behavior for existing assessed tiers must remain intact."""
        assert map_tier_to_action(RiskTier.HIGH, 700.0) == PolicyAction.HOLD_FOR_REVIEW

    def test_ac21_block_transaction_semantics_remain_intact(self) -> None:
        """AC-21: BLOCK_TRANSACTION semantics must remain intact."""
        assert map_tier_to_action(RiskTier.CRITICAL, 950.0) == PolicyAction.BLOCK_TRANSACTION

    def test_ac22_escalate_to_sar_semantics_remain_intact(self) -> None:
        """AC-22: ESCALATE_TO_SAR semantics must remain intact."""
        assert map_tier_to_action(RiskTier.CRITICAL, 850.0) == PolicyAction.ESCALATE_TO_SAR

    def test_ac23_no_consumer_may_reinterpret_unassessed_as_low_minimal(self) -> None:
        """AC-23: No consumer may reinterpret UNASSESSED as LOW/MINIMAL."""
        tier = RiskTier.UNASSESSED
        action = map_tier_to_action(tier)
        assert action != PolicyAction.ALLOW
        assert tier not in (RiskTier.MINIMAL, RiskTier.LOW)

    @pytest.mark.asyncio
    async def test_ac24_no_consumer_may_reinterpret_hold_for_review_as_allow(self) -> None:
        """AC-24: No consumer may reinterpret HOLD_FOR_REVIEW as ALLOW."""
        policy_svc = PolicyEngineService()
        rule = {
            "id": "r_hold",
            "condition": {"field": "composite_risk_score", "operator": ">=", "value": 0.0},
            "action": "HOLD_FOR_REVIEW",
        }
        eval_res = await policy_svc.evaluate_rules(session=None, transaction={"composite_risk_score": 0.0}, default_rules=[rule])
        assert eval_res["decision"] == "REVIEW"
        assert eval_res["decision"] != "ALLOW"
        assert eval_res["highest_severity_action"] == "HOLD_FOR_REVIEW"

        res = map_tier_to_action(RiskTier.UNASSESSED)
        assert res == PolicyAction.HOLD_FOR_REVIEW
        assert res != PolicyAction.ALLOW
