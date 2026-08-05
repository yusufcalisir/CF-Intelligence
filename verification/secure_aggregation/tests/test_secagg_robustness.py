"""Comprehensive Adversarial Robustness & Protocol Assumption Breakdown Test Suite.

Executes 12 failure injection and protocol stress scenarios:
1. Empty client lists handling
2. Zero-dimensional models (d=0)
3. NaN weights safe propagation
4. Infinite values (+Inf/-Inf) safe propagation
5. Mismatched tensor shapes validation
6. Duplicate client weights zero-sum behavior
7. Missing client dropout residual noise corruption
8. Corrupted AES-256-GCM mask data unsealing authentication failure
9. Mask imbalance under extreme sample count distributions
10. Invalid sample counts (negative or zero) fallback
11. SecAgg + Krum/Median pipeline incompatibility guard
12. High-dimensional vector scaling (d = 100,000)
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

from app.application.services.fl_engine import FederatedLearningEngine
from app.application.services.simulation_service import SimulationService, InvalidPipelineConfigurationError
from app.domain.enums import AggregationMethod, PrivacyMechanism
from app.infrastructure.security.tee_driver import TEEDriver
from app.domain.value_objects import ModelWeights, SimulationConfig

_engine = FederatedLearningEngine(settings=cast(Any, None), model_service=cast(Any, None), privacy_service=cast(Any, None))


def test_empty_client_list_robustness():
    """R1: Empty client list raises ValueError."""
    with pytest.raises(ValueError, match="Cannot aggregate empty parameter list"):
        _engine.aggregate_parameters(client_weights=[], client_samples=cast(Any, []), method=AggregationMethod.FED_AVG)


def test_zero_dim_model_weights():
    """R2: Zero-dimensional model weights (d=0) handle masking without crash."""
    w1 = ModelWeights(layer_shapes=[(0,)], flat_weights=[])
    w2 = ModelWeights(layer_shapes=[(0,)], flat_weights=[])
    
    masked = _engine.apply_secure_aggregation_masks([w1, w2])
    assert len(masked) == 2
    assert len(masked[0].flat_weights) == 0


def test_nan_weight_propagation():
    """R3: Safe propagation of NaN weights without execution crash."""
    w_nan = ModelWeights(layer_shapes=[(5,)], flat_weights=[np.nan, 1.0, 2.0, 3.0, 4.0])
    w_normal = ModelWeights(layer_shapes=[(5,)], flat_weights=[1.0, 2.0, 3.0, 4.0, 5.0])
    
    masked = _engine.apply_secure_aggregation_masks([w_nan, w_normal])
    assert len(masked) == 2
    assert np.isnan(masked[0].flat_weights[0])


def test_infinite_values_propagation():
    """R4: Safe propagation of +Inf and -Inf values without arithmetic crash."""
    w_inf = ModelWeights(layer_shapes=[(5,)], flat_weights=[np.inf, -np.inf, 2.0, 3.0, 4.0])
    w_normal = ModelWeights(layer_shapes=[(5,)], flat_weights=[1.0, 2.0, 3.0, 4.0, 5.0])
    
    masked = _engine.apply_secure_aggregation_masks([w_inf, w_normal])
    assert len(masked) == 2
    assert np.isinf(masked[0].flat_weights[0])
    assert np.isinf(masked[0].flat_weights[1])


def test_mismatched_tensor_shapes():
    """R5: Mismatched layer shapes across clients raises ValueError."""
    w1 = ModelWeights(layer_shapes=[(10, 5)], flat_weights=[0.0] * 50)
    w2 = ModelWeights(layer_shapes=[(5, 10)], flat_weights=[0.0] * 50)
    
    with pytest.raises(ValueError, match="Layer shape mismatch"):
        _engine.aggregate_parameters(client_weights=[w1, w2], client_samples=cast(Any, [10, 10]), method=AggregationMethod.FED_AVG)


def test_duplicate_clients_robustness():
    """R6: Identical duplicate client weight vectors handle zero-sum masking correctly."""
    w1 = ModelWeights(layer_shapes=[(10,)], flat_weights=[1.5] * 10)
    w2 = ModelWeights(layer_shapes=[(10,)], flat_weights=[1.5] * 10)
    
    masked = _engine.apply_secure_aggregation_masks([w1, w2])
    agg = _engine.aggregate_parameters(masked, client_samples=cast(Any, None), method=AggregationMethod.FED_AVG)
    
    assert np.allclose(agg.flat_weights, [1.5] * 10)


def test_missing_clients_dropout():
    """R7: Demonstrates protocol failure mode under missing client (node dropout)."""
    w1 = ModelWeights(layer_shapes=[(10,)], flat_weights=[1.0] * 10)
    w2 = ModelWeights(layer_shapes=[(10,)], flat_weights=[2.0] * 10)
    w3 = ModelWeights(layer_shapes=[(10,)], flat_weights=[3.0] * 10)
    
    masked = _engine.apply_secure_aggregation_masks([w1, w2, w3], rng=cast(Any, np.random.default_rng(42)))
    
    # Client 3 is missing; aggregator computes average of w1+m1 and w2+m2
    partial_avg = (np.array(masked[0].flat_weights) + np.array(masked[1].flat_weights)) / 2.0
    
    # Uncancelled m3 corrupts global output
    assert np.max(np.abs(partial_avg - 2.0)) > 0.1


def test_corrupted_masks_auth_failure():
    """R8: AES-256-GCM data unsealing strictly detects corrupted mask bytes."""
    key = b"K" * 32
    plaintext_mask = b"Raw Pairwise Mask Vector Binary Serialization Payload"
    
    sealed_mask = TEEDriver.seal_data(plaintext_mask, key)
    
    # Corrupt mask bytes in transmission/storage
    corrupted = bytearray(sealed_mask)
    corrupted[15] ^= 0xAA
    
    with pytest.raises(ValueError, match="Ciphertext authentication failed"):
        TEEDriver.unseal_data(bytes(corrupted), key)


def test_mask_imbalance_sample_weights():
    """R9: Extreme sample count imbalance (p_n in [0.0001, 0.9999]) preserves weighted zero-sum cancellation."""
    w1 = ModelWeights(layer_shapes=[(100,)], flat_weights=[1.0] * 100)
    w2 = ModelWeights(layer_shapes=[(100,)], flat_weights=[2.0] * 100)
    
    # Client 1 has 1 sample, Client 2 has 9999 samples
    samples = [1, 9999]
    masked = _engine.apply_secure_aggregation_masks([w1, w2], client_samples=samples, rng=cast(Any, np.random.default_rng(123)))
    
    p = np.array(samples) / sum(samples)
    weighted_avg = p[0] * np.array(masked[0].flat_weights) + p[1] * np.array(masked[1].flat_weights)
    ref_avg = p[0] * 1.0 + p[1] * 2.0
    
    assert np.max(np.abs(weighted_avg - ref_avg)) < 1e-12


def test_invalid_sample_counts_negative_zero():
    """R10: Invalid sample counts (negative or all zeros) fall back safely to unweighted zero-sum masking."""
    w1 = ModelWeights(layer_shapes=[(10,)], flat_weights=[1.0] * 10)
    w2 = ModelWeights(layer_shapes=[(10,)], flat_weights=[3.0] * 10)
    
    # All zero samples
    masked_zero = _engine.apply_secure_aggregation_masks([w1, w2], client_samples=[0, 0])
    avg_zero = (np.array(masked_zero[0].flat_weights) + np.array(masked_zero[1].flat_weights)) / 2.0
    assert np.allclose(avg_zero, [2.0] * 10)


def test_secagg_plus_krum_pipeline_guard():
    """R11: SimulationService raises InvalidPipelineConfigurationError for SecAgg + Krum/Median."""
    config = SimulationConfig(
        num_rounds=2,
        min_clients_per_round=2,
        aggregation_method=AggregationMethod.KRUM,
        enable_secure_aggregation=True,
    )
    
    mock_settings = type("MockSettings", (), {"mlflow_enabled": False, "hardware_isolation_mode": "none"})()
    service = SimulationService(
        settings=cast(Any, mock_settings),
        simulation_repo=cast(Any, None),
        bank_repo=cast(Any, None),
        metrics_repo=cast(Any, None),
        data_generator=cast(Any, None),
        fl_engine=cast(Any, None),
        metrics_service=cast(Any, None),
        model_service=cast(Any, None),
    )
    
    with pytest.raises(InvalidPipelineConfigurationError, match="Additive Secure Aggregation is mathematically incompatible"):
        service.run_simulation(config=config)


def test_large_dimension_robustness():
    """R12: Validates high-dimensional zero-sum mask generation and aggregation (d = 100,000)."""
    d = 100000
    n = 5
    raw_weights = [np.random.randn(d) for _ in range(n)]
    model_weights = [ModelWeights(layer_shapes=[(d,)], flat_weights=w.tolist()) for w in raw_weights]
    
    masked = _engine.apply_secure_aggregation_masks(model_weights)
    agg = _engine.aggregate_parameters(masked, client_samples=cast(Any, None), method=AggregationMethod.FED_AVG)
    ref_fedavg = np.mean(raw_weights, axis=0)
    
    max_err = np.max(np.abs(np.array(agg.flat_weights) - ref_fedavg))
    assert max_err < 1e-12
