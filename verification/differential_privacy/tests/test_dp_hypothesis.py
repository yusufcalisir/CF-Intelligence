#!/usr/bin/env python
"""Hypothesis Property-Based Test Suite for Differential Privacy Subsystem.

Verifies fundamental mathematical invariants across hundreds of randomized scenarios,
high-dimensional tensors, extreme floating-point boundaries, and zero/negative updates.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st  # type: ignore[import-untyped, import-not-found]

# Add backend directory to sys.path
backend_dir = Path(__file__).parent.parent.parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

from app.application.services.privacy_service import PrivacyBudget, PrivacyBudgetExceededError, PrivacyService
from app.domain.value_objects import ModelWeights
from app.domain.value_objects_phase2 import PrivacyPreservingIdentifier

@pytest.fixture
def privacy_service() -> PrivacyService:
    return PrivacyService()

# ---------------------------------------------------------------------------
# Property 1: L2 Norm Boundedness Invariant
# ---------------------------------------------------------------------------
@given(
    weights=st.lists(st.floats(min_value=-1e5, max_value=1e5, allow_nan=False, allow_infinity=False), min_size=1, max_size=100),
    max_norm=st.floats(min_value=0.01, max_value=500.0, allow_nan=False, allow_infinity=False)
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_prop_01_l2_norm_bounding(privacy_service: PrivacyService, weights: list[float], max_norm: float):
    d = len(weights)
    mw_zero = ModelWeights(layer_shapes=[(d,)], flat_weights=[0.0]*d)
    mw_orig = ModelWeights(layer_shapes=[(d,)], flat_weights=weights)

    mw_clipped = privacy_service.clip_model_update(original_weights=mw_zero, updated_weights=mw_orig, max_norm=max_norm)
    clipped_norm = float(np.linalg.norm(mw_clipped.flat_weights))

    # Invariant: ||dW_clipped||_2 <= C + 1e-12
    assert clipped_norm <= max_norm + 1e-12

# ---------------------------------------------------------------------------
# Property 2: Unclipped Vector Identity Invariant
# ---------------------------------------------------------------------------
@given(
    scale=st.floats(min_value=0.01, max_value=0.99, allow_nan=False, allow_infinity=False),
    max_norm=st.floats(min_value=1.0, max_value=100.0, allow_nan=False, allow_infinity=False)
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_prop_02_unclipped_identity(privacy_service: PrivacyService, scale: float, max_norm: float):
    d = 20
    # Construct vector with norm strictly less than max_norm
    raw_vec = np.ones(d)
    raw_norm = float(np.linalg.norm(raw_vec))
    scaled_vec = (raw_vec / raw_norm * (max_norm * scale)).tolist()

    mw_zero = ModelWeights(layer_shapes=[(d,)], flat_weights=[0.0]*d)
    mw_orig = ModelWeights(layer_shapes=[(d,)], flat_weights=scaled_vec)

    mw_clipped = privacy_service.clip_model_update(original_weights=mw_zero, updated_weights=mw_orig, max_norm=max_norm)

    # Invariant: ||dW||_2 < C => dW_clipped == dW
    assert np.allclose(mw_clipped.flat_weights, scaled_vec, atol=1e-12)

# ---------------------------------------------------------------------------
# Property 3: Gradient Directional Invariance
# ---------------------------------------------------------------------------
@given(
    weights=st.lists(st.floats(min_value=-1e4, max_value=1e4, allow_nan=False, allow_infinity=False), min_size=5, max_size=50),
    max_norm=st.floats(min_value=0.1, max_value=10.0, allow_nan=False, allow_infinity=False)
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_prop_03_directional_invariance(privacy_service: PrivacyService, weights: list[float], max_norm: float):
    orig_norm = float(np.linalg.norm(weights))
    if orig_norm < 1e-12:
        return

    d = len(weights)
    mw_zero = ModelWeights(layer_shapes=[(d,)], flat_weights=[0.0]*d)
    mw_orig = ModelWeights(layer_shapes=[(d,)], flat_weights=weights)

    mw_clipped = privacy_service.clip_model_update(original_weights=mw_zero, updated_weights=mw_orig, max_norm=max_norm)

    w_arr = np.array(weights)
    clip_arr = np.array(mw_clipped.flat_weights)
    cos_sim = float(np.dot(w_arr, clip_arr) / (np.linalg.norm(w_arr) * np.linalg.norm(clip_arr)))

    # Invariant: Cosine similarity == 1.0 (vector direction unchanged)
    assert math.isclose(cos_sim, 1.0, abs_tol=1e-10)

# ---------------------------------------------------------------------------
# Property 4: Monotonic Noise Scale Calibration
# ---------------------------------------------------------------------------
@given(
    eps1=st.floats(min_value=0.01, max_value=1.0, allow_nan=False, allow_infinity=False),
    eps2=st.floats(min_value=1.01, max_value=50.0, allow_nan=False, allow_infinity=False),
    delta=st.floats(min_value=1e-8, max_value=1e-2, allow_nan=False, allow_infinity=False)
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_prop_04_monotonic_noise_scaling(privacy_service: PrivacyService, eps1: float, eps2: float, delta: float):
    sig1 = privacy_service.calculate_gaussian_noise_scale(eps1, delta, 1.0)
    sig2 = privacy_service.calculate_gaussian_noise_scale(eps2, delta, 1.0)

    # Invariant: eps1 < eps2 => sigma1 > sigma2
    assert sig1 > sig2

# ---------------------------------------------------------------------------
# Property 5: Privacy Budget Monotonicity
# ---------------------------------------------------------------------------
@given(
    eps_list=st.lists(st.floats(min_value=0.1, max_value=1.0, allow_nan=False, allow_infinity=False), min_size=1, max_size=5)
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_prop_05_privacy_budget_monotonicity(eps_list: list[float]):
    budget = PrivacyBudget()
    prev_total = 0.0

    for eps in eps_list:
        budget.spend(eps, limit=100.0)
        assert budget.total_epsilon > prev_total
        prev_total = budget.total_epsilon

# ---------------------------------------------------------------------------
# Property 6: Privacy Budget Exhaustion Boundary Guard
# ---------------------------------------------------------------------------
@given(limit=st.floats(min_value=1.0, max_value=10.0, allow_nan=False, allow_infinity=False))
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_prop_06_budget_exhaustion_boundary_guard(limit: float):
    budget = PrivacyBudget()
    budget.spend(limit * 0.5, limit=limit)
    
    with pytest.raises(PrivacyBudgetExceededError, match="Cumulative privacy budget exceeded"):
        budget.spend(limit * 0.6, limit=limit)

# ---------------------------------------------------------------------------
# Property 7: 128-bit HMAC Identifier Determinism & Isolation
# ---------------------------------------------------------------------------
@given(
    raw_str=st.text(min_size=1, max_size=50),
    entity_type=st.sampled_from(["customer", "account", "transaction"])
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_prop_07_hmac_128bit_determinism(raw_str: str, entity_type: str):
    h1 = PrivacyPreservingIdentifier.compute(raw_str, entity_type, "key_a")
    h2 = PrivacyPreservingIdentifier.compute(raw_str, entity_type, "key_a")
    h3 = PrivacyPreservingIdentifier.compute(raw_str, entity_type, "key_b")

    # Invariant: 128-bit hex string length
    assert len(h1) == 32
    # Invariant: Determinism (h1 == h2)
    assert h1 == h2
    # Invariant: Tenant Isolation (key_a != key_b => h1 != h3)
    assert h1 != h3

# ---------------------------------------------------------------------------
# Property 8: Zero-Gradient Clipping Stability
# ---------------------------------------------------------------------------
@given(max_norm=st.floats(min_value=0.1, max_value=10.0, allow_nan=False, allow_infinity=False))
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_prop_08_zero_gradient_clipping_stability(privacy_service: PrivacyService, max_norm: float):
    d = 10
    mw_zero = ModelWeights(layer_shapes=[(d,)], flat_weights=[0.0]*d)
    mw_clipped = privacy_service.clip_model_update(original_weights=mw_zero, updated_weights=mw_zero, max_norm=max_norm)
    assert np.allclose(mw_clipped.flat_weights, [0.0]*d, atol=1e-12)

# ---------------------------------------------------------------------------
# Property 9: High-Dimensional Tensor Scaling (d=2,000)
# ---------------------------------------------------------------------------
@given(seed=st.integers(min_value=0, max_value=1000))
@settings(max_examples=10, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_prop_09_high_dimensional_tensor_scaling(privacy_service: PrivacyService, seed: int):
    rng = np.random.default_rng(seed)
    d = 2000
    w_orig = rng.normal(0.0, 5.0, size=d).tolist()

    mw_zero = ModelWeights(layer_shapes=[(50, 40)], flat_weights=[0.0]*d)
    mw_orig = ModelWeights(layer_shapes=[(50, 40)], flat_weights=w_orig)

    mw_clipped = privacy_service.clip_model_update(original_weights=mw_zero, updated_weights=mw_orig, max_norm=5.0)
    assert len(mw_clipped.flat_weights) == d
    assert float(np.linalg.norm(mw_clipped.flat_weights)) <= 5.0 + 1e-12

if __name__ == "__main__":
    pytest.main(["-v", __file__])
