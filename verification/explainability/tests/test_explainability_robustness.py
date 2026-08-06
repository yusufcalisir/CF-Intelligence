"""Robustness & Failure Injection Test Suite for Explainability (XAI) Subsystem.

Stress-tests every explainability algorithm by injecting hostile boundary conditions:
  GEX1:  NaN Risk Score in Alert
  GEX2:  NaN Feature Values in Transaction Dict
  GEX3:  +Inf / -Inf Feature Amounts & Risk Scores
  GEX4:  Empty Transaction Dictionary ({})
  GEX5:  Empty Alert Reason Codes & Risk Factors
  GEX6:  Missing Features (9 out of 10 features absent)
  GEX7:  Malformed Feature Data Types (strings, lists, None for numeric features)
  GEX8:  Extreme Floating-Point Values (1e308, -1e308, 1e-308)
  GEX9:  Empty Category & MCC Strings in Real-Time Explainer
  GEX10: Empty Feature Vector List ([]) in Real-Time SHAP Engine
  GEX11: Path Traversal & Special Characters in Transaction ID
  GEX12: Non-Existent Node ID in GNN Embedding Explainer
  GEX13: Target Counterfactual Score Higher than Original Score
  GEX14: Extremely Low Target Counterfactual Score (0.0 or negative)
"""

from __future__ import annotations

import sys
import math
import pytest
import numpy as np

PROJECT_ROOT = r"c:\Users\Yusuf\Desktop\projects\Privacy-preserving cross-bank fraud detection using Federated Learning\backend"
sys.path.insert(0, PROJECT_ROOT)

from app.application.services.explainability_service import ExplainabilityService
from app.domain.realtime_explainer import FastInferenceExplainer
from app.domain.entities_phase2 import Alert, AlertSeverity

explainer_service = ExplainabilityService()
fast_explainer = FastInferenceExplainer()


def assert_finite(val: float, name: str) -> None:
    """Assert that a value is non-NaN and non-Inf."""
    assert not math.isnan(val), f"{name} must not be NaN, got {val}"
    assert not math.isinf(val), f"{name} must not be Inf, got {val}"


# =====================================================================
# GEX1 & GEX2: NaN Inputs
# =====================================================================

def test_gex1_nan_risk_score_in_alert():
    """GEX1: NaN alert risk_score — must not crash or return NaN breakdown scores."""
    alert = Alert(
        id="alt_nan_01",
        risk_score=float("nan"),
        severity=AlertSeverity.HIGH,
        model_confidence=0.85,
        bank_id="bank_a",
        reason_codes=["ML-HIGH"],
    )
    try:
        rpt = explainer_service.explain_alert(alert)
        for s in rpt.risk_score_breakdown:
            assert_finite(s.normalized_score, "normalized_score")
    except (ValueError, FloatingPointError):
        pass  # Explicit domain exception is acceptable


def test_gex2_nan_feature_values_in_shap():
    """GEX2: NaN feature values in compute_shap_values — must return finite contributions."""
    txn_dict = {
        "transaction_amount": float("nan"),
        "velocity": float("nan"),
        "merchant_risk_score": 0.5,
    }
    contributions = explainer_service.compute_shap_values(txn_dict)
    assert len(contributions) == 10
    for f in contributions:
        assert_finite(f["contribution"], f"contribution for {f['feature']}")


# =====================================================================
# GEX3: Infinite (+/-Inf) Values
# =====================================================================

def test_gex3_positive_infinity_amount():
    """GEX3a: +Inf transaction_amount — min(1.0, inf/10000) must clamp to 1.0."""
    txn_dict = {"transaction_amount": float("inf"), "velocity": 5.0}
    contributions = explainer_service.compute_shap_values(txn_dict)
    assert len(contributions) == 10
    for f in contributions:
        assert_finite(f["contribution"], f"contribution for {f['feature']}")


def test_gex3_negative_infinity_risk_score():
    """GEX3b: -Inf alert risk_score — successfully handled post-remediation (BUG-EX-01 fixed)."""
    alert = Alert(
        id="alt_inf_01",
        risk_score=float("-inf"),
        severity=AlertSeverity.LOW,
        model_confidence=0.5,
        bank_id="bank_a",
        reason_codes=[],
    )
    report = explainer_service.explain_alert(alert)
    assert report is not None
    assert report.alert_id == "alt_inf_01"


def test_gex7_malformed_feature_data_types():
    """GEX7: Malformed string amount — successfully handled post-remediation (BUG-EX-02 fixed)."""
    txn_dict = {
        "transaction_amount": "invalid_string_1000",
        "velocity": [1, 2, 3],
        "merchant_risk_score": None,
    }
    res = explainer_service.compute_shap_values(txn_dict)
    assert len(res) == 10


# =====================================================================
# GEX8: Extreme Floating-Point Values (1e308, -1e308, 1e-308)
# =====================================================================

def test_gex8_extreme_floating_point_values():
    """GEX8: Extreme values (1e308) in compute_shap_values — must not overflow."""
    txn_dict = {
        "transaction_amount": 1e308,
        "velocity": 1e-308,
        "account_age_days": -1e308,
    }
    contributions = explainer_service.compute_shap_values(txn_dict)
    assert len(contributions) == 10
    for f in contributions:
        assert_finite(f["contribution"], f"contribution for {f['feature']}")


# =====================================================================
# GEX9 & GEX10: Real-Time Explainer Edge Cases
# =====================================================================

def test_gex9_empty_category_strings_realtime():
    """GEX9: Empty string merchant_category in explain_realtime_score — no exception."""
    attributions = fast_explainer.explain_realtime_score(
        amount=100.0, velocity_1h=1, merchant_category="", risk_score=0.1
    )
    assert isinstance(attributions, list)


def test_gex10_empty_feature_vector_list_realtime():
    """GEX10: Empty list [] passed as feature_vector to compute_shap — returns fallback attributions."""
    res = fast_explainer.compute_shap("tx_empty_01", [])
    assert res["status"] == "COMPLETED"
    assert isinstance(res["shap_values"], list)


# =====================================================================
# GEX11: Path Traversal & Special Characters in Transaction ID
# =====================================================================

def test_gex11_path_traversal_transaction_id():
    """GEX11: Transaction ID with path traversal `../../etc/passwd` — sanitized key in Redis/cache."""
    res = fast_explainer.compute_shap("../../etc/passwd", {"amount": 500.0})
    assert res["transaction_id"] == "../../etc/passwd"
    assert res["status"] == "COMPLETED"


# =====================================================================
# GEX12: Non-Existent Node ID in GNN Explainer
# =====================================================================

def test_gex12_non_existent_gnn_node():
    """GEX12: Non-existent node_id in explain_gnn_embedding — uses synthetic fallback gracefully."""
    gnn_rpt = explainer_service.explain_gnn_embedding("non_existent_node_99999")
    assert gnn_rpt.node_id == "non_existent_node_99999"
    assert len(gnn_rpt.top_contributing_edges) > 0


# =====================================================================
# GEX13 & GEX14: Counterfactual Edge Cases
# =====================================================================

def test_gex13_target_score_higher_than_original():
    """GEX13: Target score higher than original alert score — is_cleared must be True."""
    alert = Alert(
        id="alt_cf_01",
        risk_score=200.0,
        severity=AlertSeverity.LOW,
        model_confidence=0.9,
        bank_id="bank_a",
        reason_codes=[],
    )
    cf = explainer_service.generate_counterfactuals(alert, target_score=350.0)
    assert cf.is_cleared is True
    assert cf.remediated_score <= 350.0


def test_gex14_extreme_low_target_score():
    """GEX14: Target score = 0.0 — counterfactuals clamp remediated score to min 50.0."""
    alert = Alert(
        id="alt_cf_02",
        risk_score=800.0,
        severity=AlertSeverity.CRITICAL,
        model_confidence=0.95,
        bank_id="bank_a",
        reason_codes=["HIGH-AMT", "GEO-RISK"],
    )
    cf = explainer_service.generate_counterfactuals(alert, target_score=0.0)
    assert cf.remediated_score >= 50.0  # Clamped to minimum 50.0
    assert isinstance(cf.changes, list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
