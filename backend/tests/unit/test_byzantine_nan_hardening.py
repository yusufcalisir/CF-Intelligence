"""Unit tests for NaN/Inf weight sanitization and Byzantine quarantine hardening."""

import numpy as np
import pytest

from app.application.services.fl_engine import FederatedLearningEngine, ModelWeights
from app.application.services.model_service import ModelService
from app.application.services.privacy_service import PrivacyService
from app.config import get_settings
from app.domain.spectral_defense import SpectralAnomalyDetector, SpectralDefenseConfig


@pytest.fixture
def fl_engine() -> FederatedLearningEngine:
    settings = get_settings()
    model_service = ModelService(settings)
    privacy_service = PrivacyService()
    return FederatedLearningEngine(settings, model_service, privacy_service)


def test_fl_engine_quarantines_nan_and_inf_client_weights(fl_engine: FederatedLearningEngine):
    """Test that fl_engine filters out NaN/Inf client weights and aggregates remaining clean clients."""
    shapes = [[2, 2]]
    clean_w0 = ModelWeights(layer_shapes=shapes, flat_weights=[1.0, 1.0, 1.0, 1.0])
    clean_w1 = ModelWeights(layer_shapes=shapes, flat_weights=[3.0, 3.0, 3.0, 3.0])
    corrupted_w2 = ModelWeights(layer_shapes=shapes, flat_weights=[float("nan"), 10.0, 10.0, 10.0])

    client_weights = [clean_w0, clean_w1, corrupted_w2]
    client_samples = [100, 100, 100]

    aggregated = fl_engine.aggregate_parameters(
        client_weights=client_weights,
        client_samples=client_samples,
    )

    # Average of w0 (1.0) and w1 (3.0) is 2.0
    assert np.allclose(aggregated.flat_weights, [2.0, 2.0, 2.0, 2.0])
    assert not np.isnan(aggregated.flat_weights).any()


def test_fl_engine_all_nan_fallback_to_global_weights(fl_engine: FederatedLearningEngine):
    """Test that when all clients send NaN weights, fl_engine returns the previous global weights."""
    shapes = [[2, 2]]
    global_w = ModelWeights(layer_shapes=shapes, flat_weights=[5.0, 5.0, 5.0, 5.0])
    corrupted_w0 = ModelWeights(layer_shapes=shapes, flat_weights=[float("nan"), 1.0, 1.0, 1.0])
    corrupted_w1 = ModelWeights(layer_shapes=shapes, flat_weights=[float("inf"), 2.0, 2.0, 2.0])

    aggregated = fl_engine.aggregate_parameters(
        client_weights=[corrupted_w0, corrupted_w1],
        client_samples=[50, 50],
        global_weights=global_w,
    )

    assert np.allclose(aggregated.flat_weights, [5.0, 5.0, 5.0, 5.0])


def test_spectral_defense_quarantines_nan_nodes_immediately():
    """Test that SpectralAnomalyDetector marks nodes with NaN parameters as poisoned immediately."""
    detector = SpectralAnomalyDetector(config=SpectralDefenseConfig(min_clients=2))

    client_updates = {
        "bank_alpha": {"w1": [0.1, 0.2, 0.3], "b1": 0.05},
        "bank_beta": {"w1": [0.12, 0.19, 0.31], "b1": 0.04},
        "bank_gamma_attacker": {"w1": [float("nan"), 0.2, 0.3], "b1": 0.05},
    }

    reports = detector.detect_backdoor_anomalies(client_updates)
    report_dict = {r.node_id: r for r in reports}

    assert report_dict["bank_gamma_attacker"].is_poisoned is True
    assert "nan" in report_dict["bank_gamma_attacker"].reason
    assert report_dict["bank_alpha"].is_poisoned is False
    assert report_dict["bank_beta"].is_poisoned is False
