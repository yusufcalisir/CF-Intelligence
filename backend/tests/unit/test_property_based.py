"""Property-based verification tests using Hypothesis.

Verifies fundamental mathematical invariants across FL aggregation, Secure Aggregation,
Differential Privacy, drift metrics, Risk scoring engine, serialization, and Integrated Gradients.
"""

import os
import sys

import numpy as np
import pytest
import torch
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Configure sys.path so we can import app modules directly
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.application.services.fl_engine import FederatedLearningEngine
from app.application.services.model_service import ModelService
from app.application.services.privacy_service import PrivacyService
from app.application.services.risk_engine import RiskScoringEngine
from app.config import get_settings
from app.domain.enums import AggregationMethod
from app.domain.value_objects import ModelWeights
from app.domain.value_objects_investigation import RiskWeightConfig
from app.presentation.routers.banks import _compute_js_divergence, _compute_psi

settings_obj = get_settings()
model_service = ModelService(settings_obj)
privacy_service = PrivacyService()
engine = FederatedLearningEngine(settings_obj, model_service, privacy_service)


# ── 1. Federated Learning Aggregation Properties ──────────────────


@given(
    st.lists(
        st.lists(
            st.floats(min_value=-1e4, max_value=1e4, allow_nan=False, allow_infinity=False),
            min_size=12,
            max_size=12,
        ),
        min_size=2,
        max_size=20,
    )
)
@settings(suppress_health_check=[HealthCheck.too_slow], deadline=None)
def test_fed_avg_math_property(weights_list) -> None:
    """Invariant: Unweighted FedAvg is mathematically equivalent to coordinate-wise arithmetic mean."""
    client_weights = [
        ModelWeights(layer_shapes=[(4, 2), (4,)], flat_weights=w) for w in weights_list
    ]
    res = engine.aggregate_parameters(
        client_weights, [100] * len(weights_list), method=AggregationMethod.FED_AVG
    )

    expected = np.mean(weights_list, axis=0)
    np.testing.assert_allclose(res.flat_weights, expected, rtol=1e-5, atol=1e-5)


@given(
    st.integers(min_value=2, max_value=20).flatmap(
        lambda n: st.tuples(
            st.lists(
                st.lists(
                    st.floats(min_value=-1e4, max_value=1e4, allow_nan=False, allow_infinity=False),
                    min_size=12,
                    max_size=12,
                ),
                min_size=n,
                max_size=n,
            ),
            st.lists(st.integers(min_value=1, max_value=10000), min_size=n, max_size=n),
        )
    )
)
def test_weighted_fed_avg_property(data) -> None:
    """Invariant: Weighted FedAvg is mathematically equivalent to coordinate-wise weighted average."""
    weights_list, samples = data
    client_weights = [
        ModelWeights(layer_shapes=[(4, 2), (4,)], flat_weights=w) for w in weights_list
    ]
    res = engine.aggregate_parameters(
        client_weights, samples, method=AggregationMethod.FED_AVG_WEIGHTED
    )

    total_samples = sum(samples)
    proportions = [s / total_samples for s in samples]
    expected = np.zeros(12)
    for w, prop in zip(weights_list, proportions, strict=False):
        expected += np.array(w) * prop

    np.testing.assert_allclose(res.flat_weights, expected, rtol=1e-5, atol=1e-5)


@given(
    st.lists(
        st.lists(
            st.floats(min_value=-1e4, max_value=1e4, allow_nan=False, allow_infinity=False),
            min_size=12,
            max_size=12,
        ),
        min_size=2,
        max_size=20,
    )
)
def test_coordinate_median_property(weights_list) -> None:
    """Invariant: Every coordinate of Median output lies between the minimum and maximum coordinates of the inputs."""
    client_weights = [
        ModelWeights(layer_shapes=[(4, 2), (4,)], flat_weights=w) for w in weights_list
    ]
    res = engine.aggregate_parameters(
        client_weights, [100] * len(weights_list), method=AggregationMethod.COORDINATE_WISE_MEDIAN
    )

    W = np.array(weights_list)
    min_coords = W.min(axis=0)
    max_coords = W.max(axis=0)

    for i, val in enumerate(res.flat_weights):
        assert min_coords[i] - 1e-9 <= val <= max_coords[i] + 1e-9


@given(
    st.lists(
        st.lists(
            st.floats(min_value=-1e4, max_value=1e4, allow_nan=False, allow_infinity=False),
            min_size=12,
            max_size=12,
        ),
        min_size=5,  # Blanchard et al. theoretical invariant: n >= 2f + 3 = 5 for f=1 (INV-L / INV-11)
        max_size=20,
    )
)
def test_krum_valid_domain_property(weights_list) -> None:
    """Invariants: In theoretical valid domain (n >= 2f + 3), Krum matches independent oracle and minimizes distance score."""
    client_weights = [
        ModelWeights(layer_shapes=[(4, 2), (4,)], flat_weights=w) for w in weights_list
    ]

    # 1. Determinism
    res1 = engine.aggregate_parameters(
        client_weights, [100] * len(weights_list), method=AggregationMethod.KRUM
    )
    res2 = engine.aggregate_parameters(
        client_weights, [100] * len(weights_list), method=AggregationMethod.KRUM
    )
    assert res1.flat_weights == res2.flat_weights

    # 2. Output is one of the inputs
    assert any(res1.flat_weights == w for w in weights_list)

    # 3. Independent oracle: minimizes sum of squared L2 distances to (n - f - 2) closest neighbors
    W = np.array(weights_list)
    n = len(weights_list)
    f = 1
    num_closest = n - f - 2

    scores = []
    for i in range(n):
        dists = []
        for j in range(n):
            if i != j:
                dists.append(float(np.sum((W[i] - W[j]) ** 2)))
        dists.sort()
        scores.append(sum(dists[:num_closest]))

    best_idx = min(range(n), key=lambda i: (scores[i], tuple(W[i].tolist())))
    np.testing.assert_allclose(res1.flat_weights, W[best_idx], rtol=1e-5, atol=1e-5)


@pytest.mark.parametrize("n", [2, 3, 4])
def test_krum_invalid_domain_fallback(n: int, caplog: pytest.LogCaptureFixture) -> None:
    """Verify that when n < 2f+3, Krum explicitly logs precondition violation and executes fail-safe (INV-12)."""
    weights_list = [[float(i * 10 + j) for j in range(12)] for i in range(n)]
    client_weights = [
        ModelWeights(layer_shapes=[(4, 2), (4,)], flat_weights=w) for w in weights_list
    ]
    with caplog.at_level("WARNING"):
        res = engine.aggregate_parameters(
            client_weights, [100] * n, method=AggregationMethod.KRUM
        )
    assert any("Krum theoretical precondition violated" in r.message for r in caplog.records)
    assert any(res.flat_weights == w for w in weights_list)


# ── 2. Secure Aggregation Properties ──────────────────────────────


@given(
    st.sampled_from([2, 3, 5, 10]).flatmap(
        lambda n: st.tuples(
            st.just(n),
            st.lists(
                st.lists(
                    st.floats(min_value=-1e4, max_value=1e4, allow_nan=False, allow_infinity=False),
                    min_size=5,
                    max_size=5,
                ),
                min_size=n,
                max_size=n,
            ),
            st.lists(st.integers(min_value=1, max_value=1000), min_size=n, max_size=n),
        )
    )
)
def test_secure_aggregation_property(data) -> None:
    """Invariant: Masked aggregation sums cancel out completely under both weighted and unweighted schemes."""
    n_clients, weights_list, samples = data
    weights = [ModelWeights(layer_shapes=[(5,)], flat_weights=w) for w in weights_list]

    total_samples = sum(samples)
    proportions = [s / total_samples for s in samples]
    p_n = proportions[-1]

    # 1. Unweighted masks cancel out
    rng = np.random.default_rng(42)
    masked_unweighted = engine.apply_secure_aggregation_masks(weights, rng=rng)
    orig_unweighted_sum = np.sum(weights_list, axis=0)
    masked_unweighted_sum = np.sum([mw.flat_weights for mw in masked_unweighted], axis=0)
    np.testing.assert_allclose(masked_unweighted_sum, orig_unweighted_sum, rtol=1e-5, atol=1e-5)

    # 2. Weighted masks cancel out (only holds when final client weight p_n > 0)
    if p_n > 0:
        rng2 = np.random.default_rng(42)
        masked_weighted = engine.apply_secure_aggregation_masks(
            weights, client_samples=samples, rng=rng2
        )

        orig_weighted_sum = np.zeros(5)
        masked_weighted_sum = np.zeros(5)
        for i in range(n_clients):
            orig_weighted_sum += np.array(weights_list[i]) * proportions[i]
            masked_weighted_sum += np.array(masked_weighted[i].flat_weights) * proportions[i]

        np.testing.assert_allclose(masked_weighted_sum, orig_weighted_sum, rtol=1e-5, atol=1e-5)


# ── 3. Differential Privacy Properties ───────────────────────────


@given(
    st.lists(
        st.floats(min_value=-1e4, max_value=1e4, allow_nan=False, allow_infinity=False),
        min_size=5,
        max_size=5,
    ),
    st.lists(
        st.floats(min_value=-1e4, max_value=1e4, allow_nan=False, allow_infinity=False),
        min_size=5,
        max_size=5,
    ),
    st.floats(min_value=0.1, max_value=100.0, allow_nan=False, allow_infinity=False),
)
def test_gradient_clipping_property(w_orig, w_upd, max_norm) -> None:
    """Invariant: L2 norm of clipped parameters update is bounded by max_norm."""
    original = ModelWeights(layer_shapes=[(5,)], flat_weights=w_orig)
    updated = ModelWeights(layer_shapes=[(5,)], flat_weights=w_upd)

    clipped = privacy_service.clip_model_update(original, updated, max_norm=max_norm)
    delta = np.array(clipped.flat_weights) - np.array(w_orig)
    norm = np.linalg.norm(delta)
    assert norm <= max_norm + 1e-9


@given(
    st.floats(min_value=0.5, max_value=10.0, allow_nan=False, allow_infinity=False),
    st.floats(min_value=0.1, max_value=2.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=20, deadline=None)
def test_dp_noise_generation_statistics(epsilon, sensitivity) -> None:
    """Statistical Invariant: Generated Gaussian noise has mean ~ 0 and variance ~ sigma^2."""
    delta_val = 1e-5
    ref_sigma = sensitivity * np.sqrt(2 * np.log(1.25 / delta_val)) / epsilon

    original = ModelWeights(layer_shapes=[(10000,)], flat_weights=[0.0] * 10000)
    noised = privacy_service.add_noise_to_weights(
        original, epsilon=epsilon, delta=delta_val, sensitivity=sensitivity
    )

    noise = np.array(noised.flat_weights)
    mean = np.mean(noise)
    var = np.var(noise)

    # Check mean is close to 0 within 4 standard errors
    std_err = ref_sigma / np.sqrt(10000)
    assert abs(mean) <= 4.0 * std_err

    # Check variance matches standard bounds
    assert abs(var - ref_sigma**2) / (ref_sigma**2) <= 0.15


# ── 4. Drift Metrics Properties ───────────────────────────────────


@given(
    st.lists(
        st.floats(min_value=0.0, max_value=1e4, allow_nan=False, allow_infinity=False),
        min_size=5,
        max_size=5,
    ),
    st.lists(
        st.floats(min_value=0.0, max_value=1e4, allow_nan=False, allow_infinity=False),
        min_size=5,
        max_size=5,
    ),
)
def test_js_divergence_mathematical_properties(p_raw, q_raw) -> None:
    """Invariants: JS(P, P) == 0, JS(P, Q) == JS(Q, P), and JS(P, Q) >= 0."""
    p = np.array(p_raw)
    q = np.array(q_raw)

    # Avoid empty distributions to satisfy validation preconditions
    if p.sum() == 0:
        p[0] = 1.0
    if q.sum() == 0:
        q[0] = 1.0

    js_pp = _compute_js_divergence(p, p)
    assert abs(js_pp) < 1e-9

    js_pq = _compute_js_divergence(p, q)
    js_qp = _compute_js_divergence(q, p)
    assert abs(js_pq - js_qp) < 1e-9
    assert js_pq >= -1e-9


@given(
    st.lists(
        st.floats(min_value=-100.0, max_value=100.0, allow_nan=False, allow_infinity=False),
        min_size=50,
        max_size=50,
    ),
    st.lists(
        st.floats(min_value=-100.0, max_value=100.0, allow_nan=False, allow_infinity=False),
        min_size=50,
        max_size=50,
    ),
)
def test_psi_mathematical_properties(ref_list, act_list) -> None:
    """Invariants: PSI(ref, ref) == 0, and PSI(ref, act) >= 0.

    Note: Monotonicity of PSI under larger shifts does not hold globally due to NumPy's
    histogram clipping out-of-range values, which collapses distant distributions to uniform padded bounds.
    """
    ref = np.array(ref_list)
    act = np.array(act_list)
    if len(np.unique(ref)) < 2:
        ref = np.arange(50, dtype=float)
    if len(np.unique(act)) < 2:
        act = np.arange(50, dtype=float) + 1.0

    psi_self = _compute_psi(ref, ref, num_bins=5)
    assert abs(psi_self) < 1e-9

    psi_val = _compute_psi(ref, act, num_bins=5)
    assert psi_val >= -1e-9


# ── 5. Risk Scoring Engine Properties ────────────────────────────


@given(
    st.floats(min_value=0.0, max_value=50.0, allow_nan=False, allow_infinity=False),
    st.sampled_from(["crypto", "grocery", "travel", "dining"]),
    st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    st.sampled_from(["US", "NG", "RU", "UK"]),
    st.sampled_from(["mobile_app", "web_browser", "pos_terminal"]),
    st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    st.integers(min_value=0, max_value=5000),
    st.floats(min_value=0.01, max_value=10000.0, allow_nan=False, allow_infinity=False),
    st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)
def test_risk_scoring_engine_properties(
    velocity, category, merchant_risk, country, device, history, age, amount, ml1, ml2
) -> None:
    """Invariants: Score bounded in [0, 1000], and increasing ML score never decreases risk score."""
    weights = RiskWeightConfig()
    engine = RiskScoringEngine(weights)

    transaction = {
        "velocity": velocity,
        "merchant_category": category,
        "merchant_risk_score": merchant_risk,
        "country_code": country,
        "device_type": device,
        "customer_history_score": history,
        "account_age_days": age,
        "transaction_amount": amount,
    }

    score_obj_ml1 = engine.score_transaction(transaction, ml_prediction=ml1)
    assert 0.0 <= score_obj_ml1.score <= 1000.0

    score_obj_ml2 = engine.score_transaction(transaction, ml_prediction=max(ml1, ml2))
    score_obj_ml1_lower = engine.score_transaction(transaction, ml_prediction=min(ml1, ml2))
    assert score_obj_ml2.score >= score_obj_ml1_lower.score - 1e-9


@given(
    st.floats(min_value=0.01, max_value=10000.0, allow_nan=False, allow_infinity=False),
    st.floats(min_value=0.01, max_value=10000.0, allow_nan=False, allow_infinity=False),
)
def test_risk_scoring_engine_isolation_property(amt1, amt2) -> None:
    """Invariant: Changing amount only affects behavioral anomaly signal score."""
    weights = RiskWeightConfig()
    engine = RiskScoringEngine(weights)

    transaction_template = {
        "velocity": 5.0,
        "merchant_category": "grocery",
        "merchant_risk_score": 0.2,
        "country_code": "US",
        "device_type": "mobile_app",
        "customer_history_score": 0.9,
        "account_age_days": 300,
    }

    t1 = transaction_template.copy()
    t1["transaction_amount"] = amt1
    t2 = transaction_template.copy()
    t2["transaction_amount"] = amt2

    score_obj1 = engine.score_transaction(t1, ml_prediction=0.5)
    score_obj2 = engine.score_transaction(t2, ml_prediction=0.5)

    signals1 = {s.signal_name: s.normalized_score for s in score_obj1.signals}
    signals2 = {s.signal_name: s.normalized_score for s in score_obj2.signals}

    for name in signals1:
        if name != "behavior_anomaly":
            assert signals1[name] == signals2[name]


# ── 6. Model Serialization Properties ────────────────────────────


def test_model_serialization_roundtrip_property() -> None:
    """Invariant: Model state parameters remain identical after serialization roundtrip."""
    settings_obj = get_settings()
    model_service = ModelService(settings_obj)
    model = model_service.create_model(dp_compatible=False)

    weights1 = model_service.get_parameters(model)

    new_model = model_service.create_model(dp_compatible=False)
    restored_model = model_service.set_parameters(new_model, weights1)
    weights2 = model_service.get_parameters(restored_model)

    assert weights1.flat_weights == weights2.flat_weights


# ── 7. Integrated Gradients Properties ───────────────────────────


@given(
    st.lists(
        st.floats(min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False),
        min_size=10,
        max_size=10,
    )
)
@settings(max_examples=10, deadline=None)
def test_integrated_gradients_properties(input_list) -> None:
    """Invariants: Attribution dimensions match input, and zero input with zero baseline yields zero attribution."""
    settings_obj = get_settings()
    model_service = ModelService(settings_obj)
    model = model_service.create_model(dp_compatible=False)

    input_tensor = torch.FloatTensor([input_list]).to(model_service.device)
    attributions = model_service.compute_integrated_gradients(model, input_tensor, steps=20)

    assert attributions.shape == tuple(input_tensor.shape)

    # Zero input and baseline must yield 0 attributions
    zero_tensor = torch.zeros((1, 10)).to(model_service.device)
    attributions_zero = model_service.compute_integrated_gradients(
        model, zero_tensor, baseline_tensor=zero_tensor, steps=20
    )

    np.testing.assert_allclose(attributions_zero, 0.0, atol=1e-9)
