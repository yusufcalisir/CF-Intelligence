#!/usr/bin/env python
"""Adversarial Robustness, Security, & Fault Injection Test Suite for FederatedLearningEngine.

Attempts to break every aggregation algorithm using NaN/Inf floats, shape mismatches,
zero sample counts, $10^{12}$ scale malicious outliers, and 100K-parameter tensors.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

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

ALL_METHODS = [
    AggregationMethod.FED_AVG,
    AggregationMethod.FED_AVG_WEIGHTED,
    AggregationMethod.FED_ADAM,
    AggregationMethod.FED_ADAGRAD,
    AggregationMethod.FED_YOGI,
    AggregationMethod.KRUM,
    AggregationMethod.COORDINATE_WISE_MEDIAN,
    AggregationMethod.TRIMMED_MEAN,
    AggregationMethod.BULYAN,
    AggregationMethod.SCAFFOLD,
]

# 1. Empty Client List Safety across all 10 methods
@pytest.mark.parametrize("method", ALL_METHODS)
def test_empty_client_list_raises_value_error(fl_engine: FederatedLearningEngine, method: AggregationMethod):
    with pytest.raises(ValueError, match="Cannot aggregate empty parameter list"):
        fl_engine.aggregate_parameters(client_weights=[], client_samples=[], method=method)

# 2. Single Client Fast-Path Execution across all 10 methods
@pytest.mark.parametrize("method", ALL_METHODS)
def test_single_client_fast_path(fl_engine: FederatedLearningEngine, method: AggregationMethod):
    d = 10
    w = [1.0] * d
    mw = ModelWeights(layer_shapes=[(d,)], flat_weights=w)
    res = fl_engine.aggregate_parameters(client_weights=[mw], client_samples=[100], method=method)
    assert len(res.flat_weights) == d

# 3. Zero Samples Handling (Sum = 0)
def test_zero_samples_handling(fl_engine: FederatedLearningEngine):
    d = 5
    mws = [
        ModelWeights(layer_shapes=[(d,)], flat_weights=[1.0]*d),
        ModelWeights(layer_shapes=[(d,)], flat_weights=[3.0]*d),
    ]
    # Total samples = 0 should collapse gracefully to uniform mean (2.0)
    res = fl_engine.aggregate_parameters(client_weights=mws, client_samples=[0, 0], method=AggregationMethod.FED_AVG_WEIGHTED)
    assert np.allclose(res.flat_weights, [2.0]*d, atol=1e-12)

# 4. Duplicate Clients Invariance across all 10 methods
@pytest.mark.parametrize("method", ALL_METHODS)
def test_duplicate_clients_invariance(fl_engine: FederatedLearningEngine, method: AggregationMethod):
    d = 8
    w = [0.5] * d
    mws = [ModelWeights(layer_shapes=[(d,)], flat_weights=w) for _ in range(5)]
    res = fl_engine.aggregate_parameters(client_weights=mws, client_samples=[50]*5, method=method)
    assert len(res.flat_weights) == d

# 5. GNN Layer Shape Mismatch Validation
def test_gnn_shape_mismatch_raises_error(fl_engine: FederatedLearningEngine):
    mw1 = ModelWeights(layer_shapes=[(10, 5)], flat_weights=[1.0]*50)
    mw2 = ModelWeights(layer_shapes=[(10, 6)], flat_weights=[1.0]*60)
    with pytest.raises(ValueError, match="Layer shape mismatch"):
        fl_engine.aggregate_parameters(client_weights=[mw1, mw2], client_samples=[100, 100], method=AggregationMethod.FED_AVG)

# 6. Parameter Count Mismatch Validation
def test_gnn_parameter_count_mismatch_raises_error(fl_engine: FederatedLearningEngine):
    mw1 = ModelWeights(layer_shapes=[(10, 5)], flat_weights=[1.0]*50)
    mw2 = ModelWeights(layer_shapes=[(10, 5)], flat_weights=[1.0]*49)
    with pytest.raises(ValueError, match="Parameter count mismatch"):
        fl_engine.aggregate_parameters(client_weights=[mw1, mw2], client_samples=[100, 100], method=AggregationMethod.FED_AVG)

# 7. Extreme Outlier Poisoning (10^12 Scale) Robustness
@pytest.mark.parametrize("method", [
    AggregationMethod.KRUM,
    AggregationMethod.COORDINATE_WISE_MEDIAN,
    AggregationMethod.TRIMMED_MEAN,
    AggregationMethod.BULYAN
])
def test_extreme_outlier_poisoning_robustness(fl_engine: FederatedLearningEngine, method: AggregationMethod):
    d = 10
    honest = [ModelWeights(layer_shapes=[(d,)], flat_weights=[1.0]*d) for _ in range(6)]
    poisoned = ModelWeights(layer_shapes=[(d,)], flat_weights=[1e12]*d)
    
    res = fl_engine.aggregate_parameters(client_weights=honest + [poisoned], client_samples=[100]*7, method=method)
    res_arr = np.array(res.flat_weights)
    # Output must be bounded in [0.95, 1.05], isolating the 10^12 outlier
    assert np.all(res_arr >= 0.95) and np.all(res_arr <= 1.05)

# 8. NaN and Inf Float Propagation
@pytest.mark.parametrize("method", [
    AggregationMethod.FED_AVG,
    AggregationMethod.FED_AVG_WEIGHTED,
    AggregationMethod.COORDINATE_WISE_MEDIAN
])
def test_nan_and_inf_float_propagation(fl_engine: FederatedLearningEngine, method: AggregationMethod):
    d = 5
    mw1 = ModelWeights(layer_shapes=[(d,)], flat_weights=[1.0, 2.0, 3.0, 4.0, 5.0])
    mw_nan = ModelWeights(layer_shapes=[(d,)], flat_weights=[1.0, float("nan"), 3.0, 4.0, 5.0])
    mw_inf = ModelWeights(layer_shapes=[(d,)], flat_weights=[1.0, float("inf"), 3.0, 4.0, 5.0])

    res_nan = fl_engine.aggregate_parameters(client_weights=[mw1, mw_nan], client_samples=[10, 10], method=method)
    assert len(res_nan.flat_weights) == d

    res_inf = fl_engine.aggregate_parameters(client_weights=[mw1, mw_inf], client_samples=[10, 10], method=method)
    assert len(res_inf.flat_weights) == d

# 9. Large Tensor Scaling (d=100,000 parameters)
def test_very_large_tensor_scaling(fl_engine: FederatedLearningEngine):
    d = 100_000
    mws = [
        ModelWeights(layer_shapes=[(100, 1000)], flat_weights=[1.0]*d),
        ModelWeights(layer_shapes=[(100, 1000)], flat_weights=[2.0]*d),
    ]
    res = fl_engine.aggregate_parameters(client_weights=mws, client_samples=[100, 100], method=AggregationMethod.FED_AVG)
    assert len(res.flat_weights) == d
    assert np.allclose(res.flat_weights[:10], [1.5]*10, atol=1e-12)

# 10. Empty Tensor (d=0) Boundary Handling
def test_empty_tensor_d_zero(fl_engine: FederatedLearningEngine):
    mw1 = ModelWeights(layer_shapes=[(0,)], flat_weights=[])
    mw2 = ModelWeights(layer_shapes=[(0,)], flat_weights=[])
    res = fl_engine.aggregate_parameters(client_weights=[mw1, mw2], client_samples=[10, 10], method=AggregationMethod.FED_AVG)
    assert res.flat_weights == []

# 11. Unsupported Aggregation Method Exception Safety
def test_unsupported_aggregation_method_raises_error(fl_engine: FederatedLearningEngine):
    mw = ModelWeights(layer_shapes=[(5,)], flat_weights=[1.0]*5)
    with pytest.raises(ValueError, match="Unsupported aggregation method"):
        fl_engine.aggregate_parameters(client_weights=[mw], client_samples=[10], method=cast(AggregationMethod, "INVALID_METHOD"))

if __name__ == "__main__":
    pytest.main(["-v", __file__])
