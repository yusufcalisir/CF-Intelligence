"""Unit tests for Counterfactual Engine Domain Constraints, Immutability & Re-Inference Verification.

Validates Sub-Plan 23.1 requirements:
- Immutability of immutable attributes (timestamps, age, jurisdiction, account tenure, historical KYC)
- Domain feasibility bounds (monetary amount >= $0.01, velocity >= 0.0, valid categorical vocabularies)
- Model re-inference verification asserting prediction flip below fraud threshold
- Sparsity and minimal L0-norm perturbations
"""

from __future__ import annotations

import pytest

from app.application.services.counterfactual_service import (
    IMMUTABLE_FEATURES,
    MUTABLE_FEATURES,
    STANDARD_DOMAIN_CONSTRAINTS,
    CounterfactualService,
    validate_counterfactual_transition,
    validate_feature_perturbation,
)
from app.domain.enums import AlertSeverity, AlertStatus
from app.domain.investigation_entities import Alert
from app.domain.risk_engine import (
    PolicyAction,
    RiskTier,
    classify_risk_tier,
    map_tier_to_action,
)


def _make_test_alert(
    alert_id: str = "alt_test_cf_01",
    risk_score: float = 820.0,
    severity: AlertSeverity = AlertSeverity.HIGH,
    reason_codes: list[str] | None = None,
) -> Alert:
    return Alert(
        id=alert_id,
        bank_id="bank_alpha",
        transaction_id=f"tx_{alert_id}",
        risk_score=risk_score,
        severity=severity,
        status=AlertStatus.NEW,
        reason_codes=reason_codes or ["HIGH-AMT", "GEO-RISK", "VEL-001"],
        confidence=0.91,
        involved_entity_ids=["entity_cf_user_01"],
        model_confidence=0.88,
        top_features=[
            {"feature": "transaction_amount", "contribution": 0.38, "value": 4500.0},
            {"feature": "country_code", "contribution": 0.28, "value": "KP"},
            {"feature": "velocity", "contribution": 0.22, "value": 12.0},
        ],
        risk_factors=["High transaction amount", "Sanctioned jurisdiction", "High burst velocity"],
        historical_evidence=["Previous alert 48h ago"],
    )


# ---------------------------------------------------------------------------
# 1. Immutable Attribute Constraints
# ---------------------------------------------------------------------------


class TestImmutableAttributeConstraints:
    """Verify that immutable KYC, temporal, and identity features can NEVER be modified."""

    @pytest.mark.parametrize(
        "immutable_feature,original_val,tampered_val",
        [
            ("timestamp", "2026-09-28T04:00:00Z", "2026-09-27T12:00:00Z"),
            ("account_created_at", "2026-01-01T00:00:00Z", "2020-01-01T00:00:00Z"),
            ("account_age_days", 15, 365),
            ("customer_id", "cust_12345", "cust_99999"),
            ("customer_national_id", "TR12345678901", "TR99999999999"),
            ("entity_hash", "hash_abc123", "hash_clean"),
            ("date_of_birth", "1990-05-15", "1980-05-15"),
            ("age", 25, 45),
            ("chargeback_count", 3, 0),
            ("bank_id", "bank_alpha", "bank_beta"),
            ("customer_risk_profile", "HIGH", "LOW"),
            ("prior_sar_filings", 2, 0),
            ("jurisdiction", "KP", "US"),
            ("origin_country_code", "KP", "US"),
        ],
    )
    def test_immutable_attributes_prohibited_from_modification(
        self, immutable_feature: str, original_val: object, tampered_val: object
    ):
        """Assert that modifying any immutable attribute fails validation."""
        assert immutable_feature in IMMUTABLE_FEATURES

        is_valid, err = validate_feature_perturbation(
            immutable_feature, original_val, tampered_val
        )
        assert is_valid is False
        assert err is not None
        assert "immutable" in err.lower()

    def test_search_never_modifies_immutable_attributes_across_diverse_alerts(self):
        """Run counterfactual search over 25 diverse alert profiles; assert 0% immutable modifications."""
        service = CounterfactualService()

        test_profiles = [
            (f"alt_prof_{i}", 650.0 + i * 12.0, ["HIGH-AMT"] if i % 2 == 0 else ["GEO-RISK", "VEL-001"])
            for i in range(25)
        ]

        for aid, score, reasons in test_profiles:
            alert = _make_test_alert(alert_id=aid, risk_score=min(980.0, score), reason_codes=reasons)
            cf = service.generate_counterfactual(alert, target_score=350.0)

            for change in cf.changes:
                assert (
                    change.feature not in IMMUTABLE_FEATURES
                ), f"Search illegally modified immutable feature '{change.feature}' in alert {aid}!"
                assert (
                    change.feature in MUTABLE_FEATURES
                ), f"Feature '{change.feature}' is not recognized in MUTABLE_FEATURES!"

    def test_candidate_validation_catches_tampered_immutable_fields(self):
        """Construct candidate transactions with altered KYC tenure; assert transition is rejected."""
        orig_txn = {
            "transaction_amount": 5000.0,
            "account_age_days": 20,
            "customer_id": "cust_victim_99",
            "country_code": "KP",
            "velocity": 10.0,
        }

        # Tampered: illegally inflating account age from 20 days to 730 days
        tampered_txn = orig_txn.copy()
        tampered_txn["account_age_days"] = 730

        is_valid, violations = validate_counterfactual_transition(orig_txn, tampered_txn)
        assert is_valid is False
        assert any("account_age_days" in v for v in violations)


# ---------------------------------------------------------------------------
# 2. Domain Feasibility Guardrails
# ---------------------------------------------------------------------------


class TestDomainFeasibilityGuardrails:
    """Verify physical, legal, and operational domain bounds on perturbed attributes."""

    def test_monetary_amount_must_be_strictly_positive_real(self):
        """Monetary amounts must be strictly positive (amount >= $0.01)."""
        amt_constraint = STANDARD_DOMAIN_CONSTRAINTS["transaction_amount"]

        # Valid amounts
        for valid_amt in [0.01, 1.0, 50.0, 4999.99, 1000000.0]:
            ok, err = amt_constraint.validate(valid_amt)
            assert ok is True, f"Valid amount {valid_amt} rejected: {err}"

        # Invalid: zero or negative
        for invalid_amt in [0.0, -0.01, -500.0, -10000.0]:
            ok, err = amt_constraint.validate(invalid_amt)
            assert ok is False, f"Invalid amount {invalid_amt} was accepted!"
            assert "strictly positive" in err or "below minimum" in err

        # Invalid: NaN or Inf
        for bad_float in [float("nan"), float("inf"), float("-inf")]:
            ok, err = amt_constraint.validate(bad_float)
            assert ok is False

    def test_velocity_must_be_non_negative_real(self):
        """Hourly velocity must be >= 0.0."""
        vel_constraint = STANDARD_DOMAIN_CONSTRAINTS["velocity"]

        # Valid velocities
        for valid_vel in [0.0, 1.0, 5.5, 20.0, 100.0]:
            ok, err = vel_constraint.validate(valid_vel)
            assert ok is True

        # Invalid negative velocity
        for neg_vel in [-1.0, -0.5, -50.0]:
            ok, err = vel_constraint.validate(neg_vel)
            assert ok is False
            assert "below minimum" in err

    def test_categorical_domain_vocabularies(self):
        """Categoricals must belong strictly to defined domain vocabularies."""
        country_constraint = STANDARD_DOMAIN_CONSTRAINTS["country_code"]
        assert country_constraint.validate("US")[0] is True
        assert country_constraint.validate("GB")[0] is True
        assert country_constraint.validate("INVALID_CORRIDOR")[0] is False
        assert country_constraint.validate("99")[0] is False

        merch_constraint = STANDARD_DOMAIN_CONSTRAINTS["merchant_category"]
        assert merch_constraint.validate("retail")[0] is True
        assert merch_constraint.validate("grocery")[0] is True
        assert merch_constraint.validate("gambling")[0] is True
        assert merch_constraint.validate("illicit_darkweb_arms")[0] is False

        device_constraint = STANDARD_DOMAIN_CONSTRAINTS["device_type"]
        assert device_constraint.validate("mobile_app")[0] is True
        assert device_constraint.validate("web_browser")[0] is True
        assert device_constraint.validate("compromised_botnet_proxy")[0] is False

    def test_hour_of_day_bounded_integer(self):
        """Hour of day must be an integer in [0, 23]."""
        hour_constraint = STANDARD_DOMAIN_CONSTRAINTS["hour_of_day"]
        assert hour_constraint.validate(0)[0] is True
        assert hour_constraint.validate(14)[0] is True
        assert hour_constraint.validate(23)[0] is True
        assert hour_constraint.validate(24)[0] is False
        assert hour_constraint.validate(-1)[0] is False
        assert hour_constraint.validate(12.5)[0] is False


# ---------------------------------------------------------------------------
# 3. Model Re-Inference & Prediction Flip Verification
# ---------------------------------------------------------------------------


class TestModelReInferencePredictionFlip:
    """Verify that proposed counterfactuals undergo live re-inference and achieve prediction flip."""

    def test_live_re_inference_verifies_score_reduction_and_target_clearance(self):
        """Proposed counterfactual passes through RiskScoringEngine; score drops below 350.0."""
        orig_txn = {
            "transaction_amount": 6500.0,
            "country_code": "KP",
            "velocity": 15.0,
            "merchant_category": "gambling",
            "merchant_risk_score": 0.95,
            "device_type": "phone_banking",
            "account_age_days": 10,
            "customer_history_score": 0.20,
        }

        # Proposed remediation: domestic US, standard retail, 2FA mobile app, $50 amount, velocity 1.0
        remed_txn = orig_txn.copy()
        remed_txn["country_code"] = "US"
        remed_txn["transaction_amount"] = 50.0
        remed_txn["merchant_category"] = "retail"
        remed_txn["merchant_risk_score"] = 0.05
        remed_txn["device_type"] = "mobile_app"
        remed_txn["velocity"] = 1.0

        service = CounterfactualService()
        entity_key = "entity_test_live_01"
        service.engine.register_alert(entity_key)
        service.engine.register_alert(entity_key)

        res = service.verify_re_inference(
            original_txn=orig_txn,
            counterfactual_txn=remed_txn,
            target_score=350.0,
            base_ml=0.92,
            entity_hash=entity_key,
        )

        assert res.is_verified is True
        assert res.original_score > 600.0
        assert res.counterfactual_score <= 350.0
        assert res.score_reduction > 300.0
        assert res.prediction_flipped is True
        assert res.original_tier in (RiskTier.HIGH, RiskTier.CRITICAL)
        assert res.counterfactual_tier in (RiskTier.LOW, RiskTier.MINIMAL)
        assert res.counterfactual_action == PolicyAction.ALLOW
        assert len(res.violations) == 0

    def test_policy_action_flip_from_blocking_to_allowable(self):
        """Verify that policy disposition flips from BLOCK/HOLD to ALLOW."""
        service = CounterfactualService()
        alert = _make_test_alert(risk_score=850.0)

        cf = service.generate_counterfactual(alert, target_score=350.0)
        assert cf.is_cleared is True
        assert cf.remediated_score <= 350.0

        # Re-score through risk engine domain classifier
        remed_tier = classify_risk_tier(cf.remediated_score)
        remed_action = map_tier_to_action(remed_tier, cf.remediated_score)

        assert remed_action == PolicyAction.ALLOW
        assert remed_tier in (RiskTier.LOW, RiskTier.MINIMAL)

    def test_re_inference_rejects_counterfactual_with_domain_violation(self):
        """If a candidate counterfactual has a negative transaction amount, re-inference rejects it."""
        service = CounterfactualService()
        orig_txn = {"transaction_amount": 5000.0, "country_code": "US", "velocity": 5.0}

        # Illegal: negative amount
        illegal_txn = orig_txn.copy()
        illegal_txn["transaction_amount"] = -100.0

        res = service.verify_re_inference(
            original_txn=orig_txn,
            counterfactual_txn=illegal_txn,
            target_score=350.0,
        )

        assert res.is_verified is False
        assert len(res.violations) >= 1
        assert any("strictly positive" in v or "below minimum" in v for v in res.violations)

    def test_honest_re_inference_reporting_when_unreachable(self):
        """When target threshold is impossible (e.g. 5.0 pts), engine honestly reports is_cleared=False."""
        service = CounterfactualService()
        alert = _make_test_alert(risk_score=850.0)

        cf = service.generate_counterfactual(alert, target_score=5.0)

        assert cf.is_cleared is False
        assert cf.remediated_score > 5.0
        assert "did not reach" in cf.summary_text


# ---------------------------------------------------------------------------
# 4. Minimality & Sparsity
# ---------------------------------------------------------------------------


class TestMinimalityAndSparsity:
    """Verify L0-norm minimality, monotonicity, and deterministic reproducibility."""

    def test_sparsity_l0_norm_minimal(self):
        """Counterfactual explanations alter minimal features (<= 4 features)."""
        service = CounterfactualService()
        alert = _make_test_alert(risk_score=780.0)

        cf = service.generate_counterfactual(alert, target_score=350.0)

        assert len(cf.changes) >= 1
        assert len(cf.changes) <= 4, f"Excessive changes proposed: {len(cf.changes)}"

    def test_stepwise_monotonic_score_reduction(self):
        """Greedy coordinate search monotonically reduces risk score with each step."""
        service = CounterfactualService()
        alert = _make_test_alert(risk_score=880.0)

        cf = service.generate_counterfactual(alert, target_score=300.0)

        assert cf.remediated_score < cf.original_score

    def test_deterministic_reproducibility(self):
        """Identical inputs produce bit-identical counterfactual changes and remediated scores."""
        service = CounterfactualService()
        alert = _make_test_alert(risk_score=800.0)

        cf1 = service.generate_counterfactual(alert, target_score=350.0)
        cf2 = service.generate_counterfactual(alert, target_score=350.0)

        assert cf1.remediated_score == cf2.remediated_score
        assert cf1.is_cleared == cf2.is_cleared
        assert len(cf1.changes) == len(cf2.changes)

        for c1, c2 in zip(cf1.changes, cf2.changes):
            assert c1.feature == c2.feature
            assert c1.original_value == c2.original_value
            assert c1.remediated_value == c2.remediated_value
            assert c1.delta_explanation == c2.delta_explanation
