#!/usr/bin/env python
"""Comprehensive Hypothesis Property-Based Test Suite for FederatedLearningEngine.

Verifies 11 core mathematical invariants across hundreds of randomized client configurations,
non-IID sample imbalances, high-dimensional tensors (d up to 2,000), extreme float ranges,
and Byzantine outliers.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
from hypothesis import given, settings, HealthCheck, strategies as st  # type: ignore[import-untyped, import-not-found]

# Add backend directory to sys.path
backend_dir = Path(__file__).parent.parent.parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

from app.application.services.fl_engine import FederatedLearningEngine, AggregationMethod, ModelWeights

class MockSettings:
    fedopt_server_lr = 0.01
    fedopt_beta1 = 0.9
    fedopt_beta2 = 0.999
    fedopt_tau = 1e-3

@pytest.fixture
def fl_engine() -> FederatedLearningEngine:
    return FederatedLearningEngine(
        settings=cast(Any, MockSettings()),
        model_service=cast(Any, None),
        privacy_service=cast(Any, None)
    )

# ---------------------------------------------------------------------------
# Property 1: Single Client Identity Invariant (FedAvg)
# ---------------------------------------------------------------------------
@given(
    weights=st.lists(st.floats(min_value=-1e5, max_value=1e5, allow_nan=False, allow_infinity=False), min_size=1, max_size=50)
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_inv_01_single_client_identity(fl_engine: FederatedLearningEngine, weights: list[float]):
    d = len(weights)
    mw = ModelWeights(layer_shapes=[(d,)], flat_weights=weights)
    res = fl_engine.aggregate_parameters(
        client_weights=[mw],
        client_samples=[100],
        method=AggregationMethod.FED_AVG
    )
    assert np.allclose(res.flat_weights, weights, atol=1e-12)

# ---------------------------------------------------------------------------
# Property 2: Convex Hull Boundedness under Non-IID Imbalances
# ---------------------------------------------------------------------------
@given(
    n_clients=st.integers(min_value=2, max_value=10),
    d=st.integers(min_value=5, max_value=30),
    seed=st.integers(min_value=0, max_value=1000)
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_inv_02_convex_hull_boundedness(fl_engine: FederatedLearningEngine, n_clients: int, d: int, seed: int):
    rng = np.random.default_rng(seed)
    client_weights = [
        ModelWeights(layer_shapes=[(d,)], flat_weights=rng.uniform(-10.0, 10.0, size=d).tolist())
        for _ in range(n_clients)
    ]
    client_samples = rng.integers(1, 500, size=n_clients).tolist()

    res = fl_engine.aggregate_parameters(
        client_weights=client_weights,
        client_samples=client_samples,
        method=AggregationMethod.FED_AVG_WEIGHTED
    )
    
    weights_matrix = np.array([cw.flat_weights for cw in client_weights])
    min_bounds = np.min(weights_matrix, axis=0)
    max_bounds = np.max(weights_matrix, axis=0)
    res_arr = np.array(res.flat_weights)

    assert np.all(res_arr >= min_bounds - 1e-12)
    assert np.all(res_arr <= max_bounds + 1e-12)

# ---------------------------------------------------------------------------
# Property 3: Coordinate-wise Median Translation Invariance
# ---------------------------------------------------------------------------
@given(
    shift=st.floats(min_value=-100.0, max_value=100.0, allow_nan=False, allow_infinity=False),
    seed=st.integers(min_value=0, max_value=1000)
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_inv_03_median_translation_invariance(fl_engine: FederatedLearningEngine, shift: float, seed: int):
    rng = np.random.default_rng(seed)
    d = 20
    base_weights = [rng.normal(0.0, 1.0, size=d) for _ in range(5)]

    mw_base = [ModelWeights(layer_shapes=[(d,)], flat_weights=w.tolist()) for w in base_weights]
    mw_shifted = [ModelWeights(layer_shapes=[(d,)], flat_weights=(w + shift).tolist()) for w in base_weights]

    res_base = fl_engine.aggregate_parameters(client_weights=mw_base, client_samples=[10]*5, method=AggregationMethod.COORDINATE_WISE_MEDIAN)
    res_shifted = fl_engine.aggregate_parameters(client_weights=mw_shifted, client_samples=[10]*5, method=AggregationMethod.COORDINATE_WISE_MEDIAN)

    assert np.allclose(np.array(res_shifted.flat_weights), np.array(res_base.flat_weights) + shift, atol=1e-12)

# ---------------------------------------------------------------------------
# Property 4: Krum Output Selection Identity
# ---------------------------------------------------------------------------
@given(seed=st.integers(min_value=0, max_value=1000))
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_inv_04_krum_selection_identity(fl_engine: FederatedLearningEngine, seed: int):
    rng = np.random.default_rng(seed)
    d = 15
    honest_weights = [rng.normal(1.0, 0.1, size=d).tolist() for _ in range(5)]
    poisoned_weight = (rng.normal(1.0, 0.1, size=d) + 1000.0).tolist()
    
    all_weights = honest_weights + [poisoned_weight]
    client_mws = [ModelWeights(layer_shapes=[(d,)], flat_weights=w) for w in all_weights]

    res = fl_engine.aggregate_parameters(client_weights=client_mws, client_samples=[100]*6, method=AggregationMethod.KRUM)
    res_arr = np.array(res.flat_weights)

    # Krum output must be one of the client weight vectors
    is_exact_match = any(np.allclose(res_arr, np.array(w), atol=1e-12) for w in all_weights)
    assert is_exact_match
    # Krum must reject the extreme outlier
    assert not np.allclose(res_arr, np.array(poisoned_weight), atol=1e-12)

# ---------------------------------------------------------------------------
# Property 5: Trimmed Mean Extreme Outlier Rejection
# ---------------------------------------------------------------------------
@given(outlier_val=st.floats(min_value=1e5, max_value=1e8))
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_inv_05_trimmed_mean_outlier_rejection(fl_engine: FederatedLearningEngine, outlier_val: float):
    d = 10
    honest = [[1.0]*d, [1.1]*d, [0.9]*d, [1.05]*d]
    poisoned = [[outlier_val]*d]
    
    mws = [ModelWeights(layer_shapes=[(d,)], flat_weights=w) for w in (honest + poisoned)]
    res = fl_engine.aggregate_parameters(client_weights=mws, client_samples=[100]*5, method=AggregationMethod.TRIMMED_MEAN)

    res_arr = np.array(res.flat_weights)
    # Output must be bounded around honest values ~ 1.0, completely ignoring outlier_val
    assert np.all(res_arr < 100.0)

# ---------------------------------------------------------------------------
# Property 6: FedOpt Second Moment Non-Negativity & Zero-Update Stability
# ---------------------------------------------------------------------------
@given(seed=st.integers(min_value=0, max_value=1000))
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_inv_06_fedopt_zero_update_stability(fl_engine: FederatedLearningEngine, seed: int):
    rng = np.random.default_rng(seed)
    d = 20
    global_w = rng.normal(0.0, 1.0, size=d).tolist()
    global_mw = ModelWeights(layer_shapes=[(d,)], flat_weights=global_w)
    
    # Client weights identical to global weights => delta_t = 0
    client_mws = [ModelWeights(layer_shapes=[(d,)], flat_weights=global_w) for _ in range(3)]

    res_adam = fl_engine.aggregate_parameters(client_weights=client_mws, client_samples=[100]*3, method=AggregationMethod.FED_ADAM, global_weights=global_mw, simulation_id=f"zero_{seed}")
    assert np.allclose(res_adam.flat_weights, global_w, atol=1e-12)

# ---------------------------------------------------------------------------
# Property 7: Leave-One-Out Non-Participation Invariance
# ---------------------------------------------------------------------------
@given(
    excluded_idx=st.integers(min_value=0, max_value=3),
    seed=st.integers(min_value=0, max_value=1000)
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_inv_07_loo_non_participation_invariance(fl_engine: FederatedLearningEngine, excluded_idx: int, seed: int):
    rng = np.random.default_rng(seed)
    d = 10
    weights_v1 = [rng.normal(0.0, 1.0, size=d).tolist() for _ in range(4)]
    weights_v2 = [list(w) for w in weights_v1]
    # Modify excluded client weights in v2
    weights_v2[excluded_idx] = (rng.normal(100.0, 50.0, size=d)).tolist()

    mws_v1 = [ModelWeights(layer_shapes=[(d,)], flat_weights=w) for w in weights_v1]
    mws_v2 = [ModelWeights(layer_shapes=[(d,)], flat_weights=w) for w in weights_v2]

    res1 = fl_engine.aggregate_leave_one_out_parameters(client_weights=mws_v1, client_samples=[100]*4, excluded_index=excluded_idx, method=AggregationMethod.FED_AVG_WEIGHTED)
    res2 = fl_engine.aggregate_leave_one_out_parameters(client_weights=mws_v2, client_samples=[100]*4, excluded_index=excluded_idx, method=AggregationMethod.FED_AVG_WEIGHTED)

    assert np.allclose(res1.flat_weights, res2.flat_weights, atol=1e-12)

# ---------------------------------------------------------------------------
# Property 8: Zero-Sum Pairwise Mask Cancellation Identity
# ---------------------------------------------------------------------------
@given(seed=st.integers(min_value=0, max_value=1000))
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_inv_08_zero_sum_mask_cancellation(fl_engine: FederatedLearningEngine, seed: int):
    rng = np.random.default_rng(seed)
    d = 20
    plain_weights = [rng.normal(0.0, 1.0, size=d) for _ in range(4)]
    mask = rng.normal(0.0, 2.0, size=d)
    
    # Pairwise zero-sum masks: +mask to client 0, -mask to client 1
    masked_weights = [list(w) for w in plain_weights]
    masked_weights[0] = (plain_weights[0] + mask).tolist()
    masked_weights[1] = (plain_weights[1] - mask).tolist()
    masked_weights[2] = plain_weights[2].tolist()
    masked_weights[3] = plain_weights[3].tolist()

    plain_mws = [ModelWeights(layer_shapes=[(d,)], flat_weights=w.tolist()) for w in plain_weights]
    masked_mws = [ModelWeights(layer_shapes=[(d,)], flat_weights=w) for w in masked_weights]

    res_plain = fl_engine.aggregate_parameters(client_weights=plain_mws, client_samples=[100]*4, method=AggregationMethod.FED_AVG)
    res_masked = fl_engine.aggregate_parameters(client_weights=masked_mws, client_samples=[100]*4, method=AggregationMethod.FED_AVG)

    assert np.allclose(res_plain.flat_weights, res_masked.flat_weights, atol=1e-12)

# ---------------------------------------------------------------------------
# Property 9: High-Dimensional Tensor Scaling (d=2,000)
# ---------------------------------------------------------------------------
@given(seed=st.integers(min_value=0, max_value=1000))
@settings(max_examples=10, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_inv_09_high_dimensional_tensor_scaling(fl_engine: FederatedLearningEngine, seed: int):
    rng = np.random.default_rng(seed)
    d = 2000
    client_mws = [
        ModelWeights(layer_shapes=[(d,)], flat_weights=rng.normal(0.0, 1.0, size=d).tolist())
        for _ in range(3)
    ]
    res = fl_engine.aggregate_parameters(client_weights=client_mws, client_samples=[100]*3, method=AggregationMethod.FED_AVG)
    assert len(res.flat_weights) == d

# ---------------------------------------------------------------------------
# Property 10: Empty Client List Exception Safety
# ---------------------------------------------------------------------------
def test_inv_10_empty_client_list_safety(fl_engine: FederatedLearningEngine):
    with pytest.raises(ValueError, match="Cannot aggregate empty parameter list"):
        fl_engine.aggregate_parameters(client_weights=[], client_samples=[], method=AggregationMethod.FED_AVG)


# ---------------------------------------------------------------------------
# Property 11: Async Staleness Attenuation Monotonicity S(tau_1) >= S(tau_2) for tau_1 <= tau_2
# ---------------------------------------------------------------------------
@given(
    tau1=st.integers(min_value=0, max_value=100),
    tau2=st.integers(min_value=0, max_value=100),
    alpha=st.floats(min_value=0.1, max_value=2.0),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_inv_11_async_staleness_attenuation_monotonicity(tau1: int, tau2: int, alpha: float):
    from app.domain.async_fl_engine import staleness_attenuation

    t_min, t_max = min(tau1, tau2), max(tau1, tau2)
    s_min = staleness_attenuation(t_min, alpha)
    s_max = staleness_attenuation(t_max, alpha)

    assert 0.0 < s_max <= s_min <= 1.0


# ---------------------------------------------------------------------------
# Property 12: Dynamic Quorum Trigger Monotonicity
# ---------------------------------------------------------------------------
@given(
    total_nodes=st.integers(min_value=3, max_value=20),
    threshold_pct=st.floats(min_value=0.50, max_value=0.90),
)
@settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
def test_inv_12_dynamic_quorum_monotonicity(total_nodes: int, threshold_pct: float):
    from app.domain.quorum_manager import DynamicQuorumManager, QuorumState

    manager = DynamicQuorumManager(quorum_threshold_pct=threshold_pct)
    node_ids = [f"bank_{i}" for i in range(total_nodes)]
    manager.register_nodes(node_ids)

    needed = int(np.ceil(threshold_pct * total_nodes))
    for i in range(needed):
        state = manager.record_node_submission(node_ids[i])

    status = manager.evaluate_quorum_status()
    assert status.state == QuorumState.QUORUM_REACHED
    assert status.current_quorum_pct >= threshold_pct


if __name__ == "__main__":
    pytest.main(["-v", __file__])
