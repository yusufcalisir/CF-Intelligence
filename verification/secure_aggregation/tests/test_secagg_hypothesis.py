r"""Comprehensive Hypothesis Property-Based Tests for Secure Aggregation Subsystem.

Verifies 6 core mathematical invariants across hundreds of randomized scenarios:
- P1: Unweighted Zero-Sum Invariant (\sum m_i = 0)
- P2: Weighted Zero-Sum Invariant (\sum p_i m_i = 0)
- P3: Individual Obscuration (||m_i||_2 > 0)
- P4: Single-Client Fallback (n=1 => m_1 = 0)
- P5: HKDF Round Key Isolation (K_t1 != K_t2)
- P6: Layer Shape and Dimension Preservation
"""

import sys
from pathlib import Path

# Add backend directory to sys.path
backend_path = Path(__file__).resolve().parents[3] / "backend"
sys.path.insert(0, str(backend_path))

import os
from typing import Any, cast

import numpy as np
import pytest
from hypothesis import given, settings, strategies as st

from app.application.services.fl_engine import FederatedLearningEngine
from app.application.services.kms_service import KMSService
from app.domain.value_objects import ModelWeights

_engine = FederatedLearningEngine(settings=cast(Any, None), model_service=cast(Any, None), privacy_service=cast(Any, None))


@given(
    n_clients=st.integers(min_value=2, max_value=30),
    n_params=st.integers(min_value=10, max_value=2000),
    seed=st.integers(min_value=1, max_value=1000000),
)
@settings(max_examples=100, deadline=None)
def test_unweighted_zero_sum_invariant(n_clients: int, n_params: int, seed: int):
    r"""P1: Verifies that unweighted masks sum strictly to zero across n clients.
    
    Mathematical Justification:
    For m_n = -\sum_{i=1}^{n-1} m_i, the total sum \sum_{i=1}^n m_i = \mathbf{0}.
    """
    raw_weights = [np.random.randn(n_params) for _ in range(n_clients)]
    model_weights = [ModelWeights(layer_shapes=[(n_params,)], flat_weights=w.tolist()) for w in raw_weights]
    
    rng = np.random.default_rng(seed)
    masked_mw = _engine.apply_secure_aggregation_masks(model_weights, client_samples=None, rng=cast(Any, rng))
    
    masked_matrix = np.array([mw.flat_weights for mw in masked_mw])
    unmasked_matrix = np.array(raw_weights)
    
    extracted_masks = masked_matrix - unmasked_matrix
    residual_sum = np.sum(extracted_masks, axis=0)
    max_residual = np.max(np.abs(residual_sum))
    
    # Bounded by floating-point accumulation error O(\sqrt{n d} \cdot \epsilon)
    assert max_residual < 1e-11


@given(
    samples=st.lists(st.integers(min_value=10, max_value=50000), min_size=2, max_size=20),
    n_params=st.integers(min_value=10, max_value=1500),
    seed=st.integers(min_value=1, max_value=1000000),
)
@settings(max_examples=100, deadline=None)
def test_weighted_zero_sum_invariant(samples: list[int], n_params: int, seed: int):
    r"""P2: Verifies that sample-weighted masks sum strictly to zero (\sum p_i m_i = 0).
    
    Mathematical Justification:
    For m_n = -\frac{1}{p_n}\sum_{i=1}^{n-1} p_i m_i, \sum_{i=1}^n p_i m_i = \mathbf{0}.
    """
    n_clients = len(samples)
    raw_weights = [np.random.randn(n_params) * (i + 1) for i in range(n_clients)]
    model_weights = [ModelWeights(layer_shapes=[(n_params,)], flat_weights=w.tolist()) for w in raw_weights]
    
    rng = np.random.default_rng(seed)
    masked_mw = _engine.apply_secure_aggregation_masks(model_weights, client_samples=samples, rng=cast(Any, rng))
    
    masked_matrix = np.array([mw.flat_weights for mw in masked_mw])
    unmasked_matrix = np.array(raw_weights)
    
    extracted_masks = masked_matrix - unmasked_matrix
    p = np.array(samples) / sum(samples)
    weighted_residual = np.dot(p, extracted_masks)
    max_residual = np.max(np.abs(weighted_residual))
    
    assert max_residual < 1e-12


@given(
    n_clients=st.integers(min_value=2, max_value=20),
    n_params=st.integers(min_value=50, max_value=1000),
    seed=st.integers(min_value=1, max_value=1000000),
)
@settings(max_examples=50, deadline=None)
def test_individual_parameter_obscuration(n_clients: int, n_params: int, seed: int):
    """P3: Verifies that individual client updates are non-zero Gaussian obscured (||m_i||_2 > 0).
    
    Mathematical Justification:
    For m_i ~ N(0, I_d), P(||m_i||_2 = 0) = 0. Individual updates are obscured.
    """
    raw_weights = [np.zeros(n_params) for _ in range(n_clients)]
    model_weights = [ModelWeights(layer_shapes=[(n_params,)], flat_weights=w.tolist()) for w in raw_weights]
    
    rng = np.random.default_rng(seed)
    masked_mw = _engine.apply_secure_aggregation_masks(model_weights, client_samples=None, rng=cast(Any, rng))
    
    for mw in masked_mw:
        mask_norm = np.linalg.norm(mw.flat_weights)
        assert mask_norm > 1.0  # High-dimensional Gaussian norm ||m_i||_2 ~ \sqrt{d} > 1.0


@given(
    n_params=st.integers(min_value=1, max_value=500),
    seed=st.integers(min_value=1, max_value=100000),
)
@settings(max_examples=30, deadline=None)
def test_single_client_fallback(n_params: int, seed: int):
    r"""P4: Single-client federation produces m_1 = 0, returning exact unmasked weights.
    
    Mathematical Justification:
    For n=1, \sum_{i=1}^1 m_i = 0 => m_1 = 0.
    """
    w_raw = np.random.randn(n_params).tolist()
    mw = ModelWeights(layer_shapes=[(n_params,)], flat_weights=w_raw)
    
    rng = np.random.default_rng(seed)
    masked = _engine.apply_secure_aggregation_masks([mw], client_samples=None, rng=cast(Any, rng))
    
    assert masked[0].flat_weights == w_raw


@given(
    round1=st.integers(min_value=1, max_value=10000),
    round2=st.integers(min_value=1, max_value=10000),
)
@settings(max_examples=50, deadline=None)
def test_hkdf_round_key_isolation(round1: int, round2: int):
    r"""P5: Verifies that HKDF-SHA256 derives distinct round keys (K_{t1} != K_{t2} for t1 != t2).
    
    Mathematical Justification:
    HKDF-SHA256 PRF guarantee enforces collision resistance across distinct info contexts.
    """
    kms = KMSService(storage_root=None)
    bank_id = f"test_bank_p5_{os.getpid()}"
    
    key1 = kms.derive_round_mask_seed(bank_id, round1)
    key2 = kms.derive_round_mask_seed(bank_id, round2)
    
    if round1 != round2:
        assert key1 != key2
    else:
        assert key1 == key2


@given(
    n_clients=st.integers(min_value=2, max_value=10),
    layer_d1=st.integers(min_value=5, max_value=100),
    layer_d2=st.integers(min_value=5, max_value=100),
)
@settings(max_examples=30, deadline=None)
def test_layer_shape_and_dimension_preservation(n_clients: int, layer_d1: int, layer_d2: int):
    """P6: Verifies that multi-layer shapes and parameter counts are strictly preserved.
    
    Mathematical Justification:
    Masking is an element-wise isomorphism R^d -> R^d.
    """
    total_d = layer_d1 * layer_d2
    shapes = cast(Any, [(layer_d1, layer_d2)])
    
    raw_weights = [np.random.randn(total_d) for _ in range(n_clients)]
    model_weights = [ModelWeights(layer_shapes=shapes, flat_weights=w.tolist()) for w in raw_weights]
    
    masked_mw = _engine.apply_secure_aggregation_masks(model_weights)
    
    assert len(masked_mw) == n_clients
    for mw in masked_mw:
        assert mw.layer_shapes == shapes
        assert len(mw.flat_weights) == total_d
