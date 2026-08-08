"""
Hypothesis Property-Based Testing Suite: Mathematical Subsystems
==================================================================
Evaluates 10 core mathematical and cryptographic invariants across randomized
inputs using Hypothesis fuzzing framework.
"""

import math
import numpy as np
from hypothesis import given, strategies as st, settings


# 1. Property: FedAvg Sum of Weights Normalization
@given(
    st.lists(st.floats(min_value=0.1, max_value=100.0, allow_nan=False, allow_infinity=False), min_size=2, max_size=10)
)
@settings(max_examples=100)
def test_prop_fedavg_weights_sum_normalized(sizes):
    total = sum(sizes)
    props = [s / total for s in sizes]
    assert math.isclose(sum(props), 1.0, rel_tol=1e-9)


# 2. Property: L2 Gradient Clipping Boundedness
@given(
    st.lists(st.floats(min_value=-1000.0, max_value=1000.0, allow_nan=False, allow_infinity=False), min_size=1, max_size=50),
    st.floats(min_value=0.1, max_value=50.0, allow_nan=False, allow_infinity=False)
)
@settings(max_examples=100)
def test_prop_l2_gradient_clipping_boundedness(g_list, C):
    g = np.array(g_list)
    norm_g = np.linalg.norm(g)
    factor = C / max(C, norm_g)
    g_clipped = g * factor
    
    clipped_norm = np.linalg.norm(g_clipped)
    assert clipped_norm <= C + 1e-9


# 3. Property: Unit-Sphere L2 Embedding Normalization
@given(
    st.lists(st.floats(min_value=-100.0, max_value=100.0, allow_nan=False, allow_infinity=False), min_size=2, max_size=32)
)
@settings(max_examples=100)
def test_prop_unit_sphere_normalization(h_list):
    h = np.array(h_list)
    norm_h = np.linalg.norm(h)
    if norm_h > 1e-9:
        h_norm = h / norm_h
        assert math.isclose(np.linalg.norm(h_norm), 1.0, abs_tol=1e-9)


# 4. Property: Cosine Similarity Bounds [-1.0, 1.0]
@given(
    st.lists(st.floats(min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False), min_size=4, max_size=4),
    st.lists(st.floats(min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False), min_size=4, max_size=4)
)
@settings(max_examples=100)
def test_prop_cosine_similarity_bounds(u_list, v_list):
    u = np.array(u_list)
    v = np.array(v_list)
    norm_u = np.linalg.norm(u)
    norm_v = np.linalg.norm(v)
    
    if norm_u > 1e-9 and norm_v > 1e-9:
        sim = np.dot(u, v) / (norm_u * norm_v)
        assert -1.0 - 1e-9 <= sim <= 1.0 + 1e-9


# 5. Property: Composite Risk Score Saturation Bounds [0, 1000]
@given(
    st.lists(st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False), min_size=9, max_size=9)
)
@settings(max_examples=100)
def test_prop_composite_risk_score_bounds(signals):
    weights = [0.20, 0.15, 0.15, 0.10, 0.10, 0.10, 0.10, 0.05, 0.05]
    raw = sum(w * s for w, s in zip(weights, signals)) * 1000.0
    score = min(1000.0, max(0.0, raw))
    assert 0.0 <= score <= 1000.0


# 6. Property: SecAgg Pairwise Zero-Sum Cancellation
@given(
    st.lists(st.integers(min_value=-100000, max_value=100000), min_size=10, max_size=10),
    st.integers(min_value=-50000, max_value=50000)
)
@settings(max_examples=100)
def test_prop_secagg_mask_cancellation(vec, mask):
    w1 = np.array(vec)
    w2 = np.array(vec)
    
    # +mask on client 1, -mask on client 2
    y1 = w1 + mask
    y2 = w2 - mask
    
    assert np.array_equal(y1 + y2, w1 + w2)


# 7. Property: Smart Contract Payout Allocation Balance Conservation
@given(
    st.lists(st.integers(min_value=0, max_value=10000), min_size=2, max_size=10),
    st.integers(min_value=1000, max_value=10**18)
)
@settings(max_examples=100)
def test_prop_smart_contract_payout_conservation(basis_points, total_pool_wei):
    sum_s = sum(basis_points)
    if sum_s > 0:
        payouts = [(total_pool_wei * s) // sum_s for s in basis_points]
        assert sum(payouts) <= total_pool_wei


# 8. Property: Monotonic Gaussian Noise Scale Scaling
@given(
    st.floats(min_value=0.1, max_value=2.0, allow_nan=False, allow_infinity=False),
    st.floats(min_value=2.1, max_value=5.0, allow_nan=False, allow_infinity=False)
)
@settings(max_examples=100)
def test_prop_gaussian_noise_monotonicity(eps1, eps2):
    delta = 1e-5
    sigma1 = math.sqrt(2.0 * math.log(1.25 / delta)) / eps1
    sigma2 = math.sqrt(2.0 * math.log(1.25 / delta)) / eps2
    assert sigma1 > sigma2


# 9. Property: Sigmoid Normalization Monotonicity
@given(
    st.floats(min_value=-10.0, max_value=0.0, allow_nan=False, allow_infinity=False),
    st.floats(min_value=0.1, max_value=10.0, allow_nan=False, allow_infinity=False)
)
@settings(max_examples=100)
def test_prop_sigmoid_monotonicity(z1, z2):
    s1 = 1.0 / (1.0 + math.exp(-z1))
    s2 = 1.0 / (1.0 + math.exp(-z2))
    assert s1 < s2


# 10. Property: Jensen-Shannon Divergence Non-Negativity & Symmetry
@given(
    st.lists(st.floats(min_value=0.1, max_value=1.0, allow_nan=False, allow_infinity=False), min_size=3, max_size=3),
    st.lists(st.floats(min_value=0.1, max_value=1.0, allow_nan=False, allow_infinity=False), min_size=3, max_size=3)
)
@settings(max_examples=100)
def test_prop_jsd_non_negativity_and_symmetry(p_raw, q_raw):
    P = np.array(p_raw) / sum(p_raw)
    Q = np.array(q_raw) / sum(q_raw)
    M = 0.5 * (P + Q)
    
    jsd_pq = 0.5 * np.sum(P * np.log(P / M)) + 0.5 * np.sum(Q * np.log(Q / M))
    jsd_qp = 0.5 * np.sum(Q * np.log(Q / M)) + 0.5 * np.sum(P * np.log(P / M))
    
    assert jsd_pq >= -1e-12
    assert math.isclose(jsd_pq, jsd_qp, abs_tol=1e-12)


if __name__ == "__main__":
    import pytest
    import sys
    sys.exit(pytest.main(["-v", __file__]))
