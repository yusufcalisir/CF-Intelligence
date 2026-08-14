"""Targeted Branch Coverage Tests for Spectral Byzantine Defense and Robust FL Aggregation."""

from unittest.mock import MagicMock
import numpy as np
import pytest

from app.domain.byzantine_defense import SpectralByzantineDefense
from app.application.services.fl_engine import FederatedLearningEngine
from app.domain.enums import AggregationMethod
from app.domain.value_objects import ModelWeights


class TestByzantineDefenseBranches:
    """Test every branch and threshold condition in Byzantine defense algorithms."""

    def test_spectral_byzantine_defense_edge_branches(self):
        defense = SpectralByzantineDefense(contamination_ratio=0.33)

        # 1. Branch: len(updates) <= 2 -> immediate bypass
        short_updates = {
            "bank_a": np.array([0.1, 0.2]),
            "bank_b": np.array([0.15, 0.25]),
        }
        sanitized, anomalies = defense.filter_anomalous_updates(short_updates)
        assert sanitized == short_updates
        assert anomalies == []

        # 2. Branch: Zero MAD (all nodes send identical gradient vectors)
        identical_updates = {
            "bank_a": np.array([1.0, 1.0, 1.0]),
            "bank_b": np.array([1.0, 1.0, 1.0]),
            "bank_c": np.array([1.0, 1.0, 1.0]),
        }
        sanitized, anomalies = defense.filter_anomalous_updates(identical_updates)
        assert len(sanitized) == 3
        assert anomalies == []

        # 3. Branch: Extreme Byzantine Gradient Poisoning (norm outlier)
        poisoned_updates = {
            "bank_a": np.array([0.5, 0.5, 0.5]),
            "bank_b": np.array([0.52, 0.48, 0.51]),
            "bank_c": np.array([0.49, 0.53, 0.50]),
            "bank_malicious": np.array([500.0, -400.0, 350.0]),
        }
        sanitized, anomalies = defense.filter_anomalous_updates(poisoned_updates)
        assert "bank_malicious" in anomalies
        assert "bank_malicious" not in sanitized
        assert len(sanitized) == 3

    def test_fl_engine_aggregation_branches(self):
        mock_settings = MagicMock()
        mock_model_service = MagicMock()
        mock_privacy_service = MagicMock()
        engine = FederatedLearningEngine(mock_settings, mock_model_service, mock_privacy_service)

        shapes = [(3,)]
        w1 = ModelWeights(layer_shapes=shapes, flat_weights=[1.0, 2.0, 3.0])
        w2 = ModelWeights(layer_shapes=shapes, flat_weights=[1.1, 1.9, 3.1])
        w3 = ModelWeights(layer_shapes=shapes, flat_weights=[0.9, 2.1, 2.9])
        w_poison = ModelWeights(layer_shapes=shapes, flat_weights=[100.0, 200.0, 300.0])

        weights = [w1, w2, w3, w_poison]
        samples = [100, 100, 100, 100]

        # 1. KRUM branch
        krum_res = engine.aggregate_parameters(weights, samples, method=AggregationMethod.KRUM)
        assert len(krum_res.flat_weights) == 3
        assert krum_res.flat_weights[0] < 10.0

        # 2. COORDINATE_WISE_MEDIAN branch
        median_res = engine.aggregate_parameters(weights, samples, method=AggregationMethod.COORDINATE_WISE_MEDIAN)
        assert len(median_res.flat_weights) == 3
        assert 0.9 <= median_res.flat_weights[0] <= 1.5

        # 3. TRIMMED_MEAN branch (n <= 2*f fallback branch with 2 clients)
        two_weights = [w1, w2]
        two_samples = [100, 100]
        trimmed_small = engine.aggregate_parameters(two_weights, two_samples, method=AggregationMethod.TRIMMED_MEAN)
        assert len(trimmed_small.flat_weights) == 3

        # TRIMMED_MEAN with 4 clients
        trimmed_res = engine.aggregate_parameters(weights, samples, method=AggregationMethod.TRIMMED_MEAN)
        assert len(trimmed_res.flat_weights) == 3
        assert trimmed_res.flat_weights[0] < 10.0

        # 4. BULYAN branch
        bulyan_res = engine.aggregate_parameters(weights, samples, method=AggregationMethod.BULYAN)
        assert len(bulyan_res.flat_weights) == 3
        assert bulyan_res.flat_weights[0] < 10.0
