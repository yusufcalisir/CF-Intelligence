"""Unit tests for Counterfactual Service Orchestration & End-to-End Integration.

Tests:
- Integration between CounterfactualService, RiskScoringEngine, and ExplainabilityService
- Custom domain constraints injection and tenant isolation
- Edge case alert representations (minimal reason codes, extreme monetary bounds)
- Transaction overrides handling in interactive workbench scenarios
"""

from __future__ import annotations

from app.application.services.counterfactual_service import (
    CounterfactualService,
    DomainConstraint,
)
from app.application.services.explainability_service import ExplainabilityService
from app.application.services.risk_engine import RiskScoringEngine
from app.domain.entities_phase2 import Alert
from app.domain.enums import AlertSeverity, AlertStatus


def _create_sample_alert(
    alert_id: str = "alt_integ_01",
    bank_id: str = "bank_alpha",
    risk_score: float = 790.0,
    amount: float = 8500.0,
) -> Alert:
    return Alert(
        id=alert_id,
        bank_id=bank_id,
        transaction_id=f"tx_{alert_id}",
        risk_score=risk_score,
        severity=AlertSeverity.HIGH if risk_score >= 600.0 else AlertSeverity.MEDIUM,
        status=AlertStatus.NEW,
        reason_codes=["HIGH-AMT", "MERCH-RISK", "GEO-RISK", "VEL-001"],
        confidence=0.89,
        involved_entity_ids=[f"entity_{bank_id}_cust_01"],
        model_confidence=0.85,
        top_features=[
            {"feature": "transaction_amount", "contribution": 0.45, "value": amount},
            {"feature": "merchant_category", "contribution": 0.30, "value": "gambling"},
            {"feature": "country_code", "contribution": 0.25, "value": "KP"},
        ],
        risk_factors=["High amount transfer", "Gambling merchant category", "High risk corridor"],
    )


class TestCounterfactualServiceIntegration:
    """Verify CounterfactualService integration across application layers."""

    def test_end_to_end_service_instantiation_and_execution(self):
        """CounterfactualService instantiates cleanly and clears standard alert."""
        engine = RiskScoringEngine()
        service = CounterfactualService(risk_engine=engine)
        alert = _create_sample_alert()

        result = service.generate_counterfactual(alert, target_score=350.0)

        assert result.alert_id == alert.id
        assert result.original_score == 790.0
        assert result.remediated_score <= 350.0
        assert result.is_cleared is True
        assert len(result.changes) >= 1
        assert "CLEARED" in result.summary_text

    def test_transaction_override_support(self):
        """Verify transaction overrides (e.g. from interactive workbench) take precedence."""
        service = CounterfactualService()
        alert = _create_sample_alert()

        custom_txn = {
            "transaction_amount": 18000.0,
            "velocity": 30.0,
            "merchant_category": "crypto",
            "country_code": "IR",
            "device_type": "phone_banking",
            "account_age_days": 10,
        }

        result = service.generate_counterfactual(alert, target_score=350.0, transaction=custom_txn)

        assert result.original_score == 790.0
        assert result.remediated_score < result.original_score
        changed_features = [c.feature for c in result.changes]
        assert len(result.changes) >= 1
        assert any(f in changed_features for f in ["velocity", "country_code", "transaction_amount"])

    def test_explainability_service_facade_delegation(self):
        """ExplainabilityService.generate_counterfactuals delegates seamlessly to CounterfactualService."""
        exp_service = ExplainabilityService()
        alert = _create_sample_alert()

        cf = exp_service.generate_counterfactuals(alert, target_score=350.0)

        assert cf.alert_id == alert.id
        assert cf.is_cleared is True
        assert cf.remediated_score <= 350.0
        assert len(cf.changes) >= 1

    def test_custom_domain_constraint_injection(self):
        """Service respects custom domain constraints (e.g., maximum cap on transaction amount)."""
        strict_constraints = {
            "transaction_amount": DomainConstraint(
                feature="transaction_amount",
                min_value=0.01,
                max_value=500.0,  # Strict $500 ceiling
                must_be_positive=True,
                description="Strict retail card limit",
            ),
        }
        service = CounterfactualService(constraints=strict_constraints)
        assert service.constraints == strict_constraints

        # $1000 is rejected by strict $500 ceiling
        ok, err = strict_constraints["transaction_amount"].validate(1000.0)
        assert ok is False
        assert "exceeds maximum" in err

        # $250 is accepted
        ok, _ = strict_constraints["transaction_amount"].validate(250.0)
        assert ok is True

    def test_extreme_high_amount_edge_case(self):
        """Handles extreme high-amount transactions ($1,000,000) without numerical overflow."""
        service = CounterfactualService()
        alert = _create_sample_alert(risk_score=950.0, amount=1_000_000.0)

        cf = service.generate_counterfactual(
            alert,
            target_score=350.0,
            transaction={"transaction_amount": 1_000_000.0, "country_code": "KP", "velocity": 10.0},
        )

        assert cf.original_score == 950.0
        assert cf.remediated_score < 950.0
        assert len(cf.changes) >= 1

    def test_multi_tenant_bank_isolation(self):
        """Counterfactual generation operates independently for Bank Alpha and Bank Beta."""
        service = CounterfactualService()
        alert_a = _create_sample_alert(alert_id="alt_bank_a", bank_id="bank_alpha", risk_score=780.0)
        alert_b = _create_sample_alert(alert_id="alt_bank_b", bank_id="bank_beta", risk_score=780.0)

        cf_a = service.generate_counterfactual(alert_a, target_score=350.0)
        cf_b = service.generate_counterfactual(alert_b, target_score=350.0)

        assert cf_a.alert_id == "alt_bank_a"
        assert cf_b.alert_id == "alt_bank_b"
        assert cf_a.is_cleared is True
        assert cf_b.is_cleared is True
