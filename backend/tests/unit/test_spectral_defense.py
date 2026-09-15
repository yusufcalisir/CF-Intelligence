"""Unit tests for Spectral Anomaly Detection & Backdoor Poisoning Defense (Section 6.5)."""

from __future__ import annotations

import pytest

from app.application.services.fl_engine import FederatedLearningEngine
from app.domain.spectral_defense import (
    SpectralAnomalyDetector,
    SpectralDefenseConfig,
)
from app.domain.value_objects import ModelWeights

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


def _make_honest_update(value: float, n_params: int = 10) -> dict[str, float]:
    """Generate a normal-magnitude honest client parameter update."""
    return {f"param_{i}": value + (i * 0.01) for i in range(n_params)}


def _make_poisoned_update(scale: float = 100.0, n_params: int = 10) -> dict[str, float]:
    """Generate a backdoor-poisoned update with anomalously large magnitude in a specific subspace."""
    update: dict[str, float] = {}
    for i in range(n_params):
        # Backdoor attack: inject a dominant low-rank perturbation along param_0 axis
        if i == 0:
            update[f"param_{i}"] = scale
        else:
            update[f"param_{i}"] = 0.01
    return update


# ---------------------------------------------------------------------------
# Test: compute_spectral_scores
# ---------------------------------------------------------------------------


def test_spectral_score_computation() -> None:
    """Verifies SVD projection scores are computed and all non-negative."""
    config = SpectralDefenseConfig(min_clients=2)
    detector = SpectralAnomalyDetector(config=config)

    client_updates = {
        "bank_a": _make_honest_update(0.1),
        "bank_b": _make_honest_update(0.12),
        "bank_c": _make_honest_update(0.09),
    }

    scores = detector.compute_spectral_scores(client_updates)

    assert set(scores.keys()) == {"bank_a", "bank_b", "bank_c"}
    for bank_id, score in scores.items():
        assert score >= 0.0, f"Spectral score must be non-negative for {bank_id}"


# ---------------------------------------------------------------------------
# Test: detect_backdoor_poisoning_attack
# ---------------------------------------------------------------------------


def test_detect_backdoor_poisoning_attack() -> None:
    """Verifies that a stealthy low-rank backdoor injection is detected and quarantined."""
    config = SpectralDefenseConfig(
        spectral_threshold_multiplier=0.5,  # Tighter threshold to ensure detection
        min_clients=3,
    )
    detector = SpectralAnomalyDetector(config=config)

    client_updates = {
        "bank_honest_a": _make_honest_update(0.1),
        "bank_honest_b": _make_honest_update(0.12),
        "bank_honest_c": _make_honest_update(0.09),
        "bank_poisoned": _make_poisoned_update(scale=500.0),  # Extreme backdoor
    }

    reports = detector.detect_backdoor_anomalies(client_updates)

    assert len(reports) == 4

    report_map = {r.node_id: r for r in reports}

    # Poisoned node must be detected
    assert report_map["bank_poisoned"].is_poisoned is True, (
        f"Backdoor node not detected. Score={report_map['bank_poisoned'].spectral_score}"
    )

    # Honest nodes must pass
    for honest_id in ["bank_honest_a", "bank_honest_b", "bank_honest_c"]:
        assert report_map[honest_id].is_poisoned is False, (
            f"Honest node {honest_id} incorrectly quarantined"
        )


# ---------------------------------------------------------------------------
# Test: aggregate_robust_spectral
# ---------------------------------------------------------------------------


def test_robust_spectral_aggregation() -> None:
    """Verifies poisoned updates are excluded from robustly aggregated global weights."""
    config = SpectralDefenseConfig(
        spectral_threshold_multiplier=0.5,
        min_clients=3,
    )
    detector = SpectralAnomalyDetector(config=config)

    poisoned_value = 999.0
    honest_value = 0.1

    client_updates = {
        "bank_a": _make_honest_update(honest_value),
        "bank_b": _make_honest_update(honest_value + 0.01),
        "bank_c": _make_honest_update(honest_value - 0.01),
        "bank_evil": _make_poisoned_update(scale=poisoned_value),
    }

    aggregated = detector.aggregate_robust_spectral(client_updates)

    assert len(aggregated) > 0, "Aggregated result must not be empty"

    # Aggregated param_0 should be close to honest range (~0.1), far from poisoned (999.0)
    param_0 = aggregated.get("param_0", 0.0)
    assert param_0 < 50.0, (
        f"Aggregated param_0={param_0} too large; poisoned update likely leaked into aggregation"
    )


# ---------------------------------------------------------------------------
# Test: insufficient clients fallback
# ---------------------------------------------------------------------------


def test_spectral_defense_insufficient_clients_fallback() -> None:
    """Verifies graceful fallback behavior when fewer than min_clients submit updates."""
    config = SpectralDefenseConfig(min_clients=5)
    detector = SpectralAnomalyDetector(config=config)

    client_updates = {
        "bank_a": _make_honest_update(0.1),
        "bank_b": _make_honest_update(0.12),
    }

    reports = detector.detect_backdoor_anomalies(client_updates)

    assert len(reports) == 2
    for report in reports:
        assert report.is_poisoned is False
        assert report.reason == "insufficient_clients_fallback"
        assert report.spectral_score == 0.0


# ---------------------------------------------------------------------------
# Test: SpectralDefenseConfig validations
# ---------------------------------------------------------------------------


def test_spectral_defense_config_validations() -> None:
    """Verifies that invalid configuration parameters raise descriptive ValueErrors."""
    with pytest.raises(ValueError, match="spectral_threshold_multiplier must be positive"):
        SpectralDefenseConfig(spectral_threshold_multiplier=0.0)

    with pytest.raises(ValueError, match="spectral_threshold_multiplier must be positive"):
        SpectralDefenseConfig(spectral_threshold_multiplier=-1.5)

    with pytest.raises(ValueError, match="min_clients must be at least 2"):
        SpectralDefenseConfig(min_clients=1)

    with pytest.raises(ValueError, match="singular_value_rank must be at least 1"):
        SpectralDefenseConfig(singular_value_rank=0)


# ---------------------------------------------------------------------------
# Test: Zero variance protection (homogeneous clients)
# ---------------------------------------------------------------------------


def test_spectral_defense_zero_variance_homogeneous_clients() -> None:
    """Verifies that identical client updates do not trigger false-positive quarantines."""
    config = SpectralDefenseConfig(min_clients=3)
    detector = SpectralAnomalyDetector(config=config)

    identical_update = _make_honest_update(0.15)
    client_updates = {
        "bank_a": identical_update,
        "bank_b": identical_update,
        "bank_c": identical_update,
    }

    reports = detector.detect_backdoor_anomalies(client_updates)
    assert len(reports) == 3
    for report in reports:
        assert report.is_poisoned is False
        assert report.reason == "homogeneous_spectrum_no_variance"


# ---------------------------------------------------------------------------
# Test: Vector-aware parameter aggregation
# ---------------------------------------------------------------------------


def test_spectral_defense_vector_parameter_aggregation() -> None:
    """Verifies that vector/list parameters are correctly aggregated element-wise."""
    config = SpectralDefenseConfig(min_clients=2)
    detector = SpectralAnomalyDetector(config=config)

    client_updates = {
        "bank_a": {"layer_weights": [1.0, 2.0, 3.0], "bias": 0.5},
        "bank_b": {"layer_weights": [3.0, 4.0, 5.0], "bias": 1.5},
    }

    aggregated = detector.aggregate_robust_spectral(client_updates)
    assert isinstance(aggregated["layer_weights"], list)
    assert aggregated["layer_weights"] == [2.0, 3.0, 4.0]
    assert aggregated["bias"] == 1.0


# ---------------------------------------------------------------------------
# Test: FL Engine Byzantine defense integration with spectral
# ---------------------------------------------------------------------------


def test_fl_engine_apply_byzantine_defense_spectral() -> None:
    """Verifies FederatedLearningEngine.apply_byzantine_defense filters backdoor weights via spectral defense."""
    from app.application.services.model_service import ModelService
    from app.application.services.privacy_service import PrivacyService
    from app.config import get_settings

    settings = get_settings()
    model_service = ModelService(settings)
    privacy_service = PrivacyService()
    engine = FederatedLearningEngine(settings, model_service, privacy_service)

    honest_weights_1 = ModelWeights(layer_shapes=[(5,)], flat_weights=[0.1, 0.12, 0.09, 0.11, 0.10])
    honest_weights_2 = ModelWeights(layer_shapes=[(5,)], flat_weights=[0.11, 0.10, 0.08, 0.12, 0.09])
    honest_weights_3 = ModelWeights(layer_shapes=[(5,)], flat_weights=[0.09, 0.11, 0.10, 0.10, 0.11])
    poisoned_weights = ModelWeights(layer_shapes=[(5,)], flat_weights=[500.0, 0.01, 0.01, 0.01, 0.01])

    all_weights = [honest_weights_1, honest_weights_2, honest_weights_3, poisoned_weights]

    filtered = engine.apply_byzantine_defense(all_weights, defense_type="spectral")

    assert len(filtered) == 3
    # Poisoned weights should have been excluded
    for w in filtered:
        assert w.flat_weights[0] < 10.0
