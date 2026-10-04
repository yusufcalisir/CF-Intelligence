"""Tests for the risk scoring engine."""

import pytest

from app.application.services.risk_engine import RiskScoringEngine
from app.domain.value_objects_investigation import RiskWeightConfig


@pytest.fixture
def engine() -> RiskScoringEngine:
    return RiskScoringEngine()


class TestSignalEvaluation:
    def test_high_risk_transaction_scores_high(self, engine: RiskScoringEngine) -> None:
        txn = {
            "velocity": 12.0,
            "merchant_risk_score": 0.9,
            "merchant_category": "crypto",
            "country_code": "NG",
            "device_type": "web_browser",
            "customer_history_score": 0.1,
            "account_age_days": 5,
            "transaction_amount": 10000,
        }
        score = engine.score_transaction(txn, ml_prediction=0.9)
        assert score.score > 500  # High risk
        assert score.risk_level in ("high", "critical")

    def test_low_risk_transaction_scores_low(self, engine: RiskScoringEngine) -> None:
        txn = {
            "velocity": 1.0,
            "merchant_risk_score": 0.05,
            "merchant_category": "grocery",
            "country_code": "US",
            "device_type": "pos_terminal",
            "customer_history_score": 0.95,
            "account_age_days": 1000,
            "transaction_amount": 50,
        }
        score = engine.score_transaction(txn, ml_prediction=0.1)
        assert score.score < 300  # Low risk
        assert score.risk_level in ("low", "minimal")

    def test_nine_signals_produced(self, engine: RiskScoringEngine) -> None:
        score = engine.score_transaction({}, ml_prediction=0.5)
        assert len(score.signals) == 9

    def test_signal_names(self, engine: RiskScoringEngine) -> None:
        score = engine.score_transaction({}, ml_prediction=0.5)
        signal_names = {s.signal_name for s in score.signals}
        expected = {
            "ml_prediction",
            "velocity_rules",
            "merchant_reputation",
            "country_risk",
            "device_anomaly",
            "customer_history",
            "previous_alerts",
            "chargeback_history",
            "behavior_anomaly",
        }
        assert signal_names == expected


class TestWeightConfiguration:
    def test_custom_weights(self, engine: RiskScoringEngine) -> None:
        # Give all weight to ML prediction
        weights = RiskWeightConfig(
            ml_prediction=1.0,
            velocity_rules=0.0,
            merchant_reputation=0.0,
            country_risk=0.0,
            device_anomaly=0.0,
            customer_history=0.0,
            previous_alerts=0.0,
            chargeback_history=0.0,
            behavior_anomaly=0.0,
        )
        engine.update_weights(weights)

        score_high = engine.score_transaction({}, ml_prediction=0.95)
        score_low = engine.score_transaction({}, ml_prediction=0.05)

        assert score_high.score > score_low.score
        assert score_high.score > 800

    def test_weight_config_to_dict(self) -> None:
        config = RiskWeightConfig()
        d = config.to_dict()
        assert "ml_prediction" in d
        assert sum(d.values()) == pytest.approx(1.0, abs=0.01)


class TestHistoricalTracking:
    def test_previous_alerts_increase_risk(self, engine: RiskScoringEngine) -> None:
        entity = "entity_hash_001"
        score_before = engine.score_transaction({}, entity_hash=entity)

        # Register multiple alerts
        for _ in range(5):
            engine.register_alert(entity)

        score_after = engine.score_transaction({}, entity_hash=entity)
        assert score_after.score > score_before.score

    def test_chargeback_history_increases_risk(self, engine: RiskScoringEngine) -> None:
        entity = "entity_hash_002"
        score_before = engine.score_transaction({}, entity_hash=entity)

        engine.register_chargeback(entity, 0.15)  # 15% chargeback rate
        score_after = engine.score_transaction({}, entity_hash=entity)
        assert score_after.score > score_before.score

    def test_behavior_anomaly_with_baseline(self, engine: RiskScoringEngine) -> None:
        entity = "entity_hash_003"
        engine.register_baseline(entity, {"mean_amount": 100, "std_amount": 20})

        # Normal transaction
        normal_score = engine.score_transaction(
            {"transaction_amount": 110},
            entity_hash=entity,
        )
        # Anomalous transaction (10x the mean)
        anomalous_score = engine.score_transaction(
            {"transaction_amount": 1000},
            entity_hash=entity,
        )
        assert anomalous_score.score > normal_score.score


class TestScoreRange:
    def test_score_bounded(self, engine: RiskScoringEngine) -> None:
        # Maximum risk inputs
        txn = {
            "velocity": 100,
            "merchant_risk_score": 1.0,
            "merchant_category": "gambling",
            "country_code": "NG",
            "customer_history_score": 0.0,
            "account_age_days": 0,
            "transaction_amount": 999999,
        }
        score = engine.score_transaction(txn, ml_prediction=1.0)
        assert 0 <= score.score <= 1000

    def test_zero_risk_inputs(self, engine: RiskScoringEngine) -> None:
        score = engine.score_transaction({}, ml_prediction=0.0)
        assert 0 <= score.score <= 1000
