"""Unit tests for the explainability service and SHAP calculations."""

import pytest

from app.application.services.explainability_service import ExplainabilityService
from app.domain.entities_phase2 import Alert
from app.domain.enums import AlertSeverity, AlertStatus


@pytest.fixture
def explainability_service() -> ExplainabilityService:
    return ExplainabilityService()


@pytest.fixture
def sample_txn() -> dict:
    return {
        "transaction_amount": 750.50,
        "merchant_category": "retail",
        "country_code": "US",
        "device_type": "web",
        "velocity": 3.0,
        "hour_of_day": 14,
        "merchant_risk_score": 0.25,
        "customer_history_score": 0.95,
        "chargeback_count": 0,
        "account_age_days": 120,
    }


def test_compute_shap_values_formats_correctly(
    explainability_service: ExplainabilityService,
    sample_txn: dict,
) -> None:
    """Check that SHAP values are successfully computed and properly structured."""
    shap_vals = explainability_service.compute_shap_values(sample_txn)

    assert isinstance(shap_vals, list)
    assert len(shap_vals) == 10  # 10 input features

    # Verify each feature contribution matches the schema
    for entry in shap_vals:
        assert "feature" in entry
        assert "contribution" in entry
        assert isinstance(entry["feature"], str)
        assert isinstance(entry["contribution"], float)

    # Verify sorting (descending absolute contribution values)
    abs_contributions = [abs(entry["contribution"]) for entry in shap_vals]
    assert abs_contributions == sorted(abs_contributions, reverse=True)


def test_explain_alert_generates_valid_report(
    explainability_service: ExplainabilityService,
) -> None:
    """Verify that explain_alert builds a complete ExplainabilityReport."""
    alert = Alert(
        bank_id="bank_a",
        transaction_id="txn_test_123",
        risk_score=750.0,
        severity=AlertSeverity.HIGH,
        status=AlertStatus.NEW,
        reason_codes=["VEL-001", "GEO-RISK"],
        confidence=0.75,
        involved_entity_ids=["cust_123"],
        model_confidence=0.75,
        top_features=[{"feature": "velocity", "contribution": 0.45, "value": 0.45}],
        risk_factors=["High velocity", "High-risk location"],
    )

    report = explainability_service.explain_alert(alert)

    assert report.alert_id == alert.id
    assert report.top_features == alert.top_features
    assert report.risk_factors == alert.risk_factors
    assert report.model_confidence == alert.model_confidence
    assert len(report.risk_score_breakdown) > 0
    assert "HIGH" in report.explanation_text
    assert "VEL-001" in report.explanation_text


def test_shap_additivity_axiom(
    explainability_service: ExplainabilityService,
    sample_txn: dict,
) -> None:
    """Explicitly assert SHAP additivity axiom on real model: sum(phi_i) + base_val == f(x)."""
    shap_vals = explainability_service.compute_shap_values(sample_txn)

    assert len(shap_vals) == 10
    # Verify real SHAP KernelExplainer ran instead of fallback
    for entry in shap_vals:
        assert entry.get("explanation_method") == "shap_kernel_explainer"

    base_value = shap_vals[0]["base_value"]
    model_output = shap_vals[0]["model_output"]
    sum_contributions = sum(entry["contribution"] for entry in shap_vals)

    # SHAP local accuracy (additivity) property within floating point tolerance
    assert abs((base_value + sum_contributions) - model_output) < 1e-5
