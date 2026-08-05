"""Unit tests for the federated learning engine."""

import numpy as np
import pytest

from app.application.services.fl_engine import FederatedLearningEngine
from app.application.services.model_service import ModelService
from app.application.services.privacy_service import PrivacyService
from app.config import get_settings
from app.domain.enums import AggregationMethod, ClientStatus
from app.domain.value_objects import ModelWeights


@pytest.fixture
def fl_engine() -> FederatedLearningEngine:
    settings = get_settings()
    model_service = ModelService(settings)
    privacy_service = PrivacyService()
    return FederatedLearningEngine(settings, model_service, privacy_service)


@pytest.fixture
def sample_weights() -> list[ModelWeights]:
    """Create sample model weights for testing aggregation."""
    shapes: list[tuple[int, ...]] = [(4, 2), (4,)]
    return [
        ModelWeights(layer_shapes=shapes, flat_weights=[1.0] * 12),
        ModelWeights(layer_shapes=shapes, flat_weights=[3.0] * 12),
        ModelWeights(layer_shapes=shapes, flat_weights=[2.0] * 12),
    ]


class TestAggregation:
    def test_fed_avg_unweighted(
        self,
        fl_engine: FederatedLearningEngine,
        sample_weights: list[ModelWeights],
    ) -> None:
        result = fl_engine.aggregate_parameters(
            sample_weights,
            client_samples=[100, 100, 100],
            method=AggregationMethod.FED_AVG,
        )
        # Unweighted mean of [1, 3, 2] = 2.0
        assert all(abs(w - 2.0) < 1e-6 for w in result.flat_weights)

    def test_fed_avg_weighted(
        self,
        fl_engine: FederatedLearningEngine,
        sample_weights: list[ModelWeights],
    ) -> None:
        result = fl_engine.aggregate_parameters(
            sample_weights,
            client_samples=[1000, 100, 100],  # Bank A has 10x more data
            method=AggregationMethod.FED_AVG_WEIGHTED,
        )
        # Weighted toward bank A (value 1.0)
        assert all(w < 2.0 for w in result.flat_weights)

    def test_single_client_returns_unchanged(
        self,
        fl_engine: FederatedLearningEngine,
    ) -> None:
        weights = ModelWeights(
            layer_shapes=[(2, 2)],
            flat_weights=[1.0, 2.0, 3.0, 4.0],
        )
        result = fl_engine.aggregate_parameters([weights], [100])
        assert result.flat_weights == weights.flat_weights

    def test_empty_clients_raises(self, fl_engine: FederatedLearningEngine) -> None:
        with pytest.raises(ValueError, match="empty"):
            fl_engine.aggregate_parameters([], [])

    def test_preserves_layer_shapes(
        self,
        fl_engine: FederatedLearningEngine,
        sample_weights: list[ModelWeights],
    ) -> None:
        result = fl_engine.aggregate_parameters(
            sample_weights,
            [100, 100, 100],
        )
        assert result.layer_shapes == sample_weights[0].layer_shapes


class TestClientAvailability:
    def test_all_active_when_no_dropout(self, fl_engine: FederatedLearningEngine) -> None:
        statuses = fl_engine.simulate_client_availability(
            bank_ids=["a", "b", "c"],
            dropout_probability=0.0,
            rng=np.random.default_rng(42),
        )
        assert all(s == ClientStatus.ACTIVE for s in statuses.values())

    def test_all_dropped_with_high_probability(
        self,
        fl_engine: FederatedLearningEngine,
    ) -> None:
        statuses = fl_engine.simulate_client_availability(
            bank_ids=["a", "b", "c"],
            dropout_probability=1.0,
            rng=np.random.default_rng(42),
        )
        for s in statuses.values():
            assert s in (ClientStatus.DROPPED, ClientStatus.OFFLINE)

    def test_reconnection_possible(self, fl_engine: FederatedLearningEngine) -> None:
        """Previously dropped banks should have a chance to reconnect."""
        rng = np.random.default_rng(42)
        reconnected_count = 0
        trials = 100

        for _ in range(trials):
            statuses = fl_engine.simulate_client_availability(
                bank_ids=["a"],
                dropout_probability=0.0,
                previously_dropped={"a"},
                enable_reconnect=True,
                rng=rng,
            )
            if statuses["a"] == ClientStatus.RECONNECTED:
                reconnected_count += 1

        # Should reconnect ~70% of the time
        assert reconnected_count > 50


class TestSecureAggregation:
    def test_masks_preserve_aggregate(
        self,
        fl_engine: FederatedLearningEngine,
    ) -> None:
        """Masked aggregation should produce the same result as plaintext."""
        shapes: list[tuple[int, ...]] = [(3,)]
        weights = [
            ModelWeights(layer_shapes=shapes, flat_weights=[1.0, 2.0, 3.0]),
            ModelWeights(layer_shapes=shapes, flat_weights=[4.0, 5.0, 6.0]),
        ]

        rng = np.random.default_rng(42)
        masked = fl_engine.apply_secure_aggregation_masks(weights, rng=rng)

        # Individual masked weights should differ from original
        assert masked[0].flat_weights != weights[0].flat_weights

        # But the mean should be the same (masks cancel out)
        plain_avg = np.mean([w.flat_weights for w in weights], axis=0)
        masked_avg = np.mean([w.flat_weights for w in masked], axis=0)

        np.testing.assert_allclose(plain_avg, masked_avg, atol=1e-10)

    def test_weighted_masks_preserve_aggregate(
        self,
        fl_engine: FederatedLearningEngine,
    ) -> None:
        """Masked weighted aggregation should produce the same result as plaintext."""
        shapes: list[tuple[int, ...]] = [(3,)]
        weights = [
            ModelWeights(layer_shapes=shapes, flat_weights=[1.0, 2.0, 3.0]),
            ModelWeights(layer_shapes=shapes, flat_weights=[4.0, 5.0, 6.0]),
        ]
        samples = [100, 200]

        rng = np.random.default_rng(42)
        masked = fl_engine.apply_secure_aggregation_masks(weights, client_samples=samples, rng=rng)

        # Individual masked weights should differ from original
        assert masked[0].flat_weights != weights[0].flat_weights

        # But the weighted average should be the same
        plain_avg = fl_engine.aggregate_parameters(
            weights, samples, method=AggregationMethod.FED_AVG_WEIGHTED
        )
        masked_avg = fl_engine.aggregate_parameters(
            masked, samples, method=AggregationMethod.FED_AVG_WEIGHTED
        )

        np.testing.assert_allclose(plain_avg.flat_weights, masked_avg.flat_weights, atol=1e-10)


class TestByzantineRobustness:
    def test_krum_robustness_selects_closest(
        self,
        fl_engine: FederatedLearningEngine,
    ) -> None:
        """Krum should select the honest client closest to all others, rejecting the outlier."""
        shapes: list[tuple[int, ...]] = [(3,)]
        # Two clients are close to each other (honest), one is far away (Byzantine attacker)
        weights = [
            ModelWeights(layer_shapes=shapes, flat_weights=[1.0, 1.0, 1.0]),  # Honest 1
            ModelWeights(layer_shapes=shapes, flat_weights=[1.1, 1.1, 1.1]),  # Honest 2
            ModelWeights(
                layer_shapes=shapes, flat_weights=[100.0, 100.0, 100.0]
            ),  # Attacker (Poisoned)
        ]

        result = fl_engine.aggregate_parameters(
            weights,
            client_samples=[100, 100, 100],
            method=AggregationMethod.KRUM,
        )

        # Krum should choose one of the honest weights (closest to most others), not the attacker
        assert max(result.flat_weights) < 2.0

    def test_coordinate_wise_median(
        self,
        fl_engine: FederatedLearningEngine,
    ) -> None:
        """Coordinate-wise median should take the element-wise median across clients."""
        shapes: list[tuple[int, ...]] = [(3,)]
        weights = [
            ModelWeights(layer_shapes=shapes, flat_weights=[1.0, 5.0, 10.0]),
            ModelWeights(layer_shapes=shapes, flat_weights=[2.0, 4.0, 20.0]),
            ModelWeights(
                layer_shapes=shapes, flat_weights=[3.0, 3.0, 100.0]
            ),  # Attacker has large outlier in index 2
        ]

        result = fl_engine.aggregate_parameters(
            weights,
            client_samples=[100, 100, 100],
            method=AggregationMethod.COORDINATE_WISE_MEDIAN,
        )

        # Median of [1, 2, 3] = 2.0
        # Median of [5, 4, 3] = 4.0
        # Median of [10, 20, 100] = 20.0
        assert result.flat_weights == [2.0, 4.0, 20.0]

    def test_apply_model_poisoning(
        self,
        fl_engine: FederatedLearningEngine,
    ) -> None:
        """Model poisoning should corrupt honest weights with noise scaling."""
        shapes: list[tuple[int, ...]] = [(3,)]
        honest = ModelWeights(layer_shapes=shapes, flat_weights=[1.0, 1.0, 1.0])

        rng = np.random.default_rng(42)
        poisoned = fl_engine.apply_model_poisoning(honest, scale=5.0, rng=rng)

        # Poisoned weights should be different from honest weights
        assert poisoned.flat_weights != honest.flat_weights
        assert poisoned.layer_shapes == honest.layer_shapes

    def test_fed_adam_aggregation(
        self,
        fl_engine: FederatedLearningEngine,
        sample_weights: list[ModelWeights],
    ) -> None:
        """Verify FedAdam server optimizer updates correctly with running momentum/variance."""
        global_weights = ModelWeights(
            layer_shapes=sample_weights[0].layer_shapes,
            flat_weights=[0.0] * 12,
        )

        result = fl_engine.aggregate_parameters(
            sample_weights,
            client_samples=[100, 100, 100],
            method=AggregationMethod.FED_ADAM,
            global_weights=global_weights,
            simulation_id="test_adam_sim",
        )

        # Standard average of client weights is 2.0.
        # w_t = 0.0, delta_t = 2.0 - 0.0 = 2.0.
        # Since running states are initialized to 0:
        # m_1 = (1 - beta1) * delta_t = 0.1 * 2.0 = 0.2
        # With bias correction:
        # m_hat = 0.2 / 0.1 = 2.0, v_hat = 0.004 / 0.001 = 4.0
        # w_1 = 0.0 + 0.01 * 2.0 / (sqrt(4.0) + 0.001) = 0.02 / 2.001 ~ 0.009995
        assert len(result.flat_weights) == 12
        assert all(0.0095 < w < 0.0105 for w in result.flat_weights)

    def test_fed_adagrad_aggregation(
        self,
        fl_engine: FederatedLearningEngine,
        sample_weights: list[ModelWeights],
    ) -> None:
        """Verify FedAdaGrad server optimizer updates correctly with squared updates."""
        global_weights = ModelWeights(
            layer_shapes=sample_weights[0].layer_shapes,
            flat_weights=[0.0] * 12,
        )

        result = fl_engine.aggregate_parameters(
            sample_weights,
            client_samples=[100, 100, 100],
            method=AggregationMethod.FED_ADAGRAD,
            global_weights=global_weights,
            simulation_id="test_adagrad_sim",
        )

        # delta_t = 2.0
        # v_1 = delta_t^2 = 4.0
        # w_1 = w_0 + eta * delta_t / (sqrt(v_1) + tau) = 0.0 + 0.01 * 2.0 / (sqrt(4.0) + 0.001)
        # w_1 = 0.02 / 2.001 = ~0.009995
        assert len(result.flat_weights) == 12
        assert all(0.009 < w < 0.011 for w in result.flat_weights)

    def test_trimmed_mean_eliminates_outlier(
        self,
        fl_engine: FederatedLearningEngine,
    ) -> None:
        """Trimmed Mean must discard the extreme outlier and converge to the honest cluster."""
        shapes: list[tuple[int, ...]] = [(4,)]
        # Honest clients cluster at ~2.0, poisoned client at 100.0
        weights = [
            ModelWeights(layer_shapes=shapes, flat_weights=[2.0, 2.0, 2.0, 2.0]),
            ModelWeights(layer_shapes=shapes, flat_weights=[2.0, 2.0, 2.0, 2.0]),
            ModelWeights(layer_shapes=shapes, flat_weights=[2.0, 2.0, 2.0, 2.0]),
            ModelWeights(
                layer_shapes=shapes, flat_weights=[100.0, 100.0, 100.0, 100.0]
            ),  # Byzantine
        ]
        result = fl_engine.aggregate_parameters(
            weights,
            client_samples=[100, 100, 100, 100],
            method=AggregationMethod.TRIMMED_MEAN,
        )
        # After trimming f=1 extremes: only honest [2.0, 2.0] clients remain
        # Result should be close to 2.0, not inflated by the Byzantine 100.0 client
        assert all(abs(w - 2.0) < 0.5 for w in result.flat_weights), (
            f"Trimmed Mean should suppress outlier: got {result.flat_weights}"
        )

    def test_trimmed_mean_fallback_with_too_few_clients(
        self,
        fl_engine: FederatedLearningEngine,
    ) -> None:
        """With only 2 clients (= 2*f), Trimmed Mean falls back to plain FedAvg gracefully."""
        shapes: list[tuple[int, ...]] = [(3,)]
        weights = [
            ModelWeights(layer_shapes=shapes, flat_weights=[1.0, 1.0, 1.0]),
            ModelWeights(layer_shapes=shapes, flat_weights=[3.0, 3.0, 3.0]),
        ]
        result = fl_engine.aggregate_parameters(
            weights,
            client_samples=[100, 100],
            method=AggregationMethod.TRIMMED_MEAN,
        )
        # Falls back to mean: (1+3)/2 = 2.0
        assert all(abs(w - 2.0) < 1e-6 for w in result.flat_weights)

    def test_bulyan_defends_against_colluding_byzantine(
        self,
        fl_engine: FederatedLearningEngine,
    ) -> None:
        """Bulyan must suppress a poisoning client and converge near honest values."""
        shapes: list[tuple[int, ...]] = [(4,)]
        # 3 honest clients at 2.0, 1 poisoned at 50.0
        weights = [
            ModelWeights(layer_shapes=shapes, flat_weights=[2.0, 2.0, 2.0, 2.0]),
            ModelWeights(layer_shapes=shapes, flat_weights=[2.0, 2.0, 2.0, 2.0]),
            ModelWeights(layer_shapes=shapes, flat_weights=[2.0, 2.0, 2.0, 2.0]),
            ModelWeights(layer_shapes=shapes, flat_weights=[50.0, 50.0, 50.0, 50.0]),  # Byzantine
        ]
        result = fl_engine.aggregate_parameters(
            weights,
            client_samples=[100, 100, 100, 100],
            method=AggregationMethod.BULYAN,
        )
        # Bulyan Krum step should reject the poisoned client
        # Result must be close to honest cluster value 2.0
        assert all(abs(w - 2.0) < 5.0 for w in result.flat_weights), (
            f"Bulyan should suppress Byzantine client: got {result.flat_weights}"
        )

    def test_bulyan_preserves_layer_shapes(
        self,
        fl_engine: FederatedLearningEngine,
        sample_weights: list[ModelWeights],
    ) -> None:
        """Bulyan aggregation must preserve the original layer shapes."""
        result = fl_engine.aggregate_parameters(
            sample_weights,
            client_samples=[100, 100, 100],
            method=AggregationMethod.BULYAN,
        )
        assert result.layer_shapes == sample_weights[0].layer_shapes
        assert len(result.flat_weights) == len(sample_weights[0].flat_weights)
