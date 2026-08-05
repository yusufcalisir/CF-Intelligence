#!/usr/bin/env python
"""Adversarial Robustness & Failure Injection Test Suite for Differential Privacy Subsystem.

Attempts to break every DP mechanism by injecting extreme epsilons, invalid deltas, NaNs,
Infs, empty weight arrays, zero clipping norms, huge tensors (100k params), and PII strings.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

# Add backend directory to sys.path
backend_dir = Path(__file__).parent.parent.parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

from app.application.services.privacy_service import PrivacyBudget, PrivacyBudgetExceededError, PrivacyService
from app.application.services.privacy_audit_service import PrivacyAuditService
from app.application.services.psi_service import PSI_PRIME, PSIService
from app.domain.security_evaluator import MIAEvaluator, DLGEvaluator
from app.domain.label_privacy_guard import LabelPrivacyGuard, LabelPrivacyViolationError
from app.domain.value_objects import ModelWeights
from app.domain.value_objects_phase2 import PrivacyPreservingIdentifier

@pytest.fixture
def privacy_service() -> PrivacyService:
    return PrivacyService()

@pytest.fixture
def audit_service() -> PrivacyAuditService:
    return PrivacyAuditService()

# ---------------------------------------------------------------------------
# Stress Test 1: Epsilon Approaching Zero (1e-7)
# ---------------------------------------------------------------------------
def test_stress_01_epsilon_approaching_zero(privacy_service: PrivacyService):
    eps_near_zero = 1e-7
    sig = privacy_service.calculate_gaussian_noise_scale(eps_near_zero, 1e-5, 1.0)
    assert math.isfinite(sig)
    assert sig > 1e6

    mw = ModelWeights(layer_shapes=[(10,)], flat_weights=[1.0]*10)
    mw_noised = privacy_service.add_noise_to_weights(mw, epsilon=eps_near_zero, delta=1e-5)
    assert len(mw_noised.flat_weights) == 10
    assert np.all(np.isfinite(mw_noised.flat_weights))

# ---------------------------------------------------------------------------
# Stress Test 2: Negative or Zero Epsilon Validation
# ---------------------------------------------------------------------------
def test_stress_02_negative_or_zero_epsilon_validation(privacy_service: PrivacyService):
    with pytest.raises(ValueError, match="Epsilon must be positive"):
        privacy_service.calculate_gaussian_noise_scale(0.0, 1e-5)

    with pytest.raises(ValueError, match="Epsilon must be positive"):
        privacy_service.calculate_gaussian_noise_scale(-2.0, 1e-5)

# ---------------------------------------------------------------------------
# Stress Test 3: Extremely Large Epsilon (1e12)
# ---------------------------------------------------------------------------
def test_stress_03_extremely_large_epsilon(privacy_service: PrivacyService):
    huge_eps = 1e12
    sig = privacy_service.calculate_gaussian_noise_scale(huge_eps, 1e-5, 1.0)
    assert math.isfinite(sig)
    assert sig < 1e-10

    mw = ModelWeights(layer_shapes=[(10,)], flat_weights=[1.0]*10)
    mw_noised = privacy_service.add_noise_to_weights(mw, epsilon=huge_eps, delta=1e-5)
    assert np.allclose(mw_noised.flat_weights, [1.0]*10, atol=1e-5)

# ---------------------------------------------------------------------------
# Stress Test 4: Invalid Delta Bounds (delta <= 0 or delta >= 1)
# ---------------------------------------------------------------------------
def test_stress_04_invalid_delta_bounds(privacy_service: PrivacyService):
    with pytest.raises(ValueError, match="Delta must be in \\(0, 1\\)"):
        privacy_service.calculate_gaussian_noise_scale(1.0, 0.0)

    with pytest.raises(ValueError, match="Delta must be in \\(0, 1\\)"):
        privacy_service.calculate_gaussian_noise_scale(1.0, 1.5)

# ---------------------------------------------------------------------------
# Stress Test 5 & 6: NaN & Inf Gradients Handling
# ---------------------------------------------------------------------------
def test_stress_05_nan_and_inf_gradients_handling(privacy_service: PrivacyService):
    mw_zero = ModelWeights(layer_shapes=[(3,)], flat_weights=[0.0, 0.0, 0.0])
    mw_nan = ModelWeights(layer_shapes=[(3,)], flat_weights=[1.0, float('nan'), 3.0])
    mw_inf = ModelWeights(layer_shapes=[(3,)], flat_weights=[1.0, float('inf'), 3.0])

    mw_nan_clipped = privacy_service.clip_model_update(mw_zero, mw_nan, max_norm=1.0)
    assert len(mw_nan_clipped.flat_weights) == 3

    mw_inf_clipped = privacy_service.clip_model_update(mw_zero, mw_inf, max_norm=1.0)
    assert len(mw_inf_clipped.flat_weights) == 3

# ---------------------------------------------------------------------------
# Stress Test 7: Empty Weight Tensors
# ---------------------------------------------------------------------------
def test_stress_07_empty_weight_tensors(privacy_service: PrivacyService):
    mw_empty = ModelWeights(layer_shapes=[(0,)], flat_weights=[])
    mw_clipped = privacy_service.clip_model_update(mw_empty, mw_empty, max_norm=1.0)
    assert mw_clipped.flat_weights == []

    mw_noised = privacy_service.add_noise_to_weights(mw_empty, epsilon=1.0, delta=1e-5)
    assert mw_noised.flat_weights == []

# ---------------------------------------------------------------------------
# Stress Test 8: Zero Clipping Threshold (C = 0.0)
# ---------------------------------------------------------------------------
def test_stress_08_zero_clipping_threshold(privacy_service: PrivacyService):
    mw_zero = ModelWeights(layer_shapes=[(3,)], flat_weights=[0.0, 0.0, 0.0])
    mw_orig = ModelWeights(layer_shapes=[(3,)], flat_weights=[5.0, -10.0, 2.0])

    mw_clipped = privacy_service.clip_model_update(mw_zero, mw_orig, max_norm=0.0)
    assert np.allclose(mw_clipped.flat_weights, [0.0, 0.0, 0.0], atol=1e-12)

# ---------------------------------------------------------------------------
# Stress Test 9: Raw PII Payload Violation
# ---------------------------------------------------------------------------
def test_stress_09_raw_pii_payload_violation():
    guard = LabelPrivacyGuard()

    # Plaintext IBAN key in raw_attributes should trigger privacy violation
    payload_iban = {"iban": "TR330006100511123456789012"}
    with pytest.raises(LabelPrivacyViolationError):
        guard.validate_feedback_identifier("a"*32, raw_attributes=payload_iban)

    # Short hash should trigger privacy violation
    with pytest.raises(LabelPrivacyViolationError):
        guard.validate_feedback_identifier("TR330006100511123456789012")

    # Safe payload should pass
    guard.validate_feedback_identifier("a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6")

# ---------------------------------------------------------------------------
# Stress Test 10: Massive Tensor Dimension Scaling (d = 100,000)
# ---------------------------------------------------------------------------
def test_stress_10_massive_tensor_dimension_scaling(privacy_service: PrivacyService):
    d = 100_000
    rng = np.random.default_rng(42)
    w_large = rng.normal(0.0, 2.0, size=d).tolist()

    mw_zero = ModelWeights(layer_shapes=[(1000, 100)], flat_weights=[0.0]*d)
    mw_orig = ModelWeights(layer_shapes=[(1000, 100)], flat_weights=w_large)

    mw_clipped = privacy_service.clip_model_update(mw_zero, mw_orig, max_norm=10.0)
    assert len(mw_clipped.flat_weights) == d
    assert float(np.linalg.norm(mw_clipped.flat_weights)) <= 10.0 + 1e-10

# ---------------------------------------------------------------------------
# Stress Test 11: Un-clipped Empirical MIA Evaluator Check
# ---------------------------------------------------------------------------
def test_stress_11_unclipped_empirical_mia_evaluator():
    evaluator = MIAEvaluator(seed=42)
    y_true = np.array([1]*50 + [1]*50)
    # Members have low loss (0.99 prob), non-members have high loss (0.05 prob)
    y_pred_prob = np.array([0.99]*50 + [0.05]*50)
    member_mask = np.array([True]*50 + [False]*50)

    res = evaluator.evaluate_membership_inference(
        cast(Any, y_true), cast(Any, y_pred_prob), cast(Any, member_mask), epsilon=1.0
    )
    # Empirical MIA accuracy should be 1.0 (un-clipped)
    assert res.unprotected_attack_acc == 1.0
    assert res.unprotected_advantage == 1.0

# ---------------------------------------------------------------------------
# Stress Test 12: Un-clipped Empirical DLG Evaluator Check
# ---------------------------------------------------------------------------
def test_stress_12_unclipped_empirical_dlg_evaluator():
    evaluator = DLGEvaluator(seed=42)
    x_orig = np.linspace(-2.0, 2.0, 50)
    dummy_grads = np.ones(50)

    res = evaluator.evaluate_gradient_leakage(cast(Any, x_orig), cast(Any, dummy_grads))
    # Unprotected Pearson correlation should be high (~0.80+) without forced clip bounds
    assert res.unprotected_correlation > 0.80
    assert res.secagg_correlation < 0.30

# ---------------------------------------------------------------------------
# Stress Test 13: 2048-bit DH-PSI Zero Exponent Safety
# ---------------------------------------------------------------------------
def test_stress_13_dh_psi_zero_exponent_safety():
    val_int = 123456789
    res0 = pow(val_int, 0, PSI_PRIME)
    assert res0 == 1

# ---------------------------------------------------------------------------
# Stress Test 14: Audit Service Model Inversion Risk
# ---------------------------------------------------------------------------
def test_stress_14_audit_service_model_inversion_risk(audit_service: PrivacyAuditService):
    res_empty = audit_service.audit_model_inversion([])
    assert res_empty["risk_tier"] == "safe"

    # High variance gradient norms -> high risk
    res_high = audit_service.audit_model_inversion([0.01, 100.0, 0.05, 50.0])
    assert res_high["risk_tier"] in ["moderate_risk", "high_risk"]

if __name__ == "__main__":
    pytest.main(["-v", __file__])
