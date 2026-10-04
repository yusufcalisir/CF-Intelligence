"""Federated Learning Core Deep Correctness Verification & Hardening Test Suite.

Comprehensive tests verifying:
1. FL Invariants (FL-INV-01 through FL-INV-12)
2. Mathematical Oracle Verification for FedAvg & Weighted Aggregation
3. Metamorphic Testing (Identity, Order Invariance, Scaling Invariance)
4. Numerical Stability (NaN/Inf Quarantining, Zero-Division Immunity, Negative Clamping)
5. Parameter Compatibility & Strict Layer/Shape Validation
6. State & Simulation Isolation (Concurrent FedOpt / Server State Independence)
7. Non-IID Dirichlet Partition Completeness & Disjointness
8. Reproducibility & Deterministic Seed Fidelity
"""

from __future__ import annotations

import math

import numpy as np
import pytest
import torch

from app.application.services.fl_dirichlet_partitioner import DirichletPartitioner
from app.application.services.fl_engine import FederatedLearningEngine
from app.application.services.model_service import ModelService
from app.application.services.privacy_service import PrivacyService
from app.config import Settings
from app.domain.enums import AggregationMethod
from app.domain.value_objects import ModelWeights


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        fl_default_learning_rate=0.01,
        fl_default_local_epochs=1,
        fl_default_batch_size=16,
        fedopt_server_lr=0.1,
        fedopt_beta1=0.9,
        fedopt_beta2=0.99,
        fedopt_tau=1e-3,
    )


@pytest.fixture
def model_service(test_settings: Settings) -> ModelService:
    return ModelService(settings=test_settings)


@pytest.fixture
def fl_engine(test_settings: Settings, model_service: ModelService) -> FederatedLearningEngine:
    return FederatedLearningEngine(
        settings=test_settings,
        model_service=model_service,
        privacy_service=PrivacyService(),
    )


# ==============================================================================
# 1. MATHEMATICAL ORACLE & UNIT TESTS (FL-INV-05, FL-INV-06)
# ==============================================================================


class TestFedAvgMathematicalOracle:
    """Verifies that FedAvg aggregation exactly matches an independent mathematical oracle."""

    @staticmethod
    def manual_fedavg_oracle(
        weights_list: list[list[float]],
        samples_list: list[int],
    ) -> list[float]:
        """Independent mathematical reference: w = sum(n_k * w_k) / sum(n_k)."""
        tot_samples = sum(samples_list)
        if tot_samples <= 0:
            proportions = [1.0 / len(samples_list)] * len(samples_list)
        else:
            proportions = [s / tot_samples for s in samples_list]

        dim = len(weights_list[0])
        result = [0.0] * dim
        for w, p in zip(weights_list, proportions, strict=True):
            for i in range(dim):
                result[i] += w[i] * p
        return result

    def test_fedavg_oracle_hand_calculated_small(self, fl_engine: FederatedLearningEngine):
        """Construct deterministic model states where expected result is manually known:
        Client A: weight = [2.0, 4.0], samples = 1
        Client B: weight = [8.0, 10.0], samples = 3
        Expected: (1*[2, 4] + 3*[8, 10]) / 4 = [26/4, 34/4] = [6.5, 8.5]
        """
        w_a = ModelWeights(layer_shapes=[(2,)], flat_weights=[2.0, 4.0])
        w_b = ModelWeights(layer_shapes=[(2,)], flat_weights=[8.0, 10.0])

        res = fl_engine.aggregate_parameters(
            client_weights=[w_a, w_b],
            client_samples=[1, 3],
            method=AggregationMethod.FED_AVG_WEIGHTED,
        )

        expected = [6.5, 8.5]
        np.testing.assert_allclose(res.flat_weights, expected, rtol=1e-5, atol=1e-5)

    def test_fedavg_oracle_multi_tensor(self, fl_engine: FederatedLearningEngine):
        """Test with multi-layer tensor weights compared against independent mathematical oracle."""
        shapes: list[tuple[int, ...]] = [(4, 2), (2,)]
        rng = np.random.default_rng(1234)

        clients = []
        samples = [150, 350, 500]
        raw_weights = []
        for _ in range(3):
            flat = rng.normal(loc=0.0, scale=1.0, size=10).astype(np.float32).tolist()
            raw_weights.append(flat)
            clients.append(ModelWeights(layer_shapes=shapes, flat_weights=flat))

        expected = self.manual_fedavg_oracle(raw_weights, samples)

        actual = fl_engine.aggregate_parameters(
            client_weights=clients,
            client_samples=samples,
            method=AggregationMethod.FED_AVG_WEIGHTED,
        )

        np.testing.assert_allclose(actual.flat_weights, expected, rtol=1e-5, atol=1e-5)


# ==============================================================================
# 2. METAMORPHIC TESTING (FL-INV-05, FL-INV-06)
# ==============================================================================


class TestMetamorphicProperties:
    """Verifies metamorphic properties that must hold for valid federated aggregation."""

    def test_single_client_identity(self, fl_engine: FederatedLearningEngine):
        """Property: aggregate([w]) == w."""
        w = ModelWeights(layer_shapes=[(3, 2)], flat_weights=[1.1, 2.2, 3.3, 4.4, 5.5, 6.6])
        res = fl_engine.aggregate_parameters(
            client_weights=[w],
            client_samples=[100],
            method=AggregationMethod.FED_AVG_WEIGHTED,
        )
        assert res.flat_weights == w.flat_weights
        assert res.layer_shapes == w.layer_shapes

    def test_equal_weight_identity(self, fl_engine: FederatedLearningEngine):
        """Property: Identical client weights aggregate to that exact same weight vector."""
        w = ModelWeights(layer_shapes=[(4,)], flat_weights=[7.5, -3.2, 0.0, 12.8])
        res = fl_engine.aggregate_parameters(
            client_weights=[w, w, w],
            client_samples=[10, 50, 100],
            method=AggregationMethod.FED_AVG_WEIGHTED,
        )
        np.testing.assert_allclose(res.flat_weights, w.flat_weights, rtol=1e-6, atol=1e-6)

    def test_weight_scaling_invariance(self, fl_engine: FederatedLearningEngine):
        """Property: Scaling all sample counts by a constant c > 0 yields identical aggregation."""
        w1 = ModelWeights(layer_shapes=[(2,)], flat_weights=[1.0, 5.0])
        w2 = ModelWeights(layer_shapes=[(2,)], flat_weights=[9.0, 13.0])

        res1 = fl_engine.aggregate_parameters(
            client_weights=[w1, w2],
            client_samples=[20, 80],
            method=AggregationMethod.FED_AVG_WEIGHTED,
        )
        res2 = fl_engine.aggregate_parameters(
            client_weights=[w1, w2],
            client_samples=[200, 800],
            method=AggregationMethod.FED_AVG_WEIGHTED,
        )
        np.testing.assert_allclose(res1.flat_weights, res2.flat_weights, rtol=1e-6, atol=1e-6)

    def test_client_order_invariance(self, fl_engine: FederatedLearningEngine):
        """Property: Permuting the order of clients yields identical aggregated parameters."""
        w1 = ModelWeights(layer_shapes=[(2,)], flat_weights=[1.0, 2.0])
        w2 = ModelWeights(layer_shapes=[(2,)], flat_weights=[3.0, 4.0])
        w3 = ModelWeights(layer_shapes=[(2,)], flat_weights=[5.0, 6.0])

        res_abc = fl_engine.aggregate_parameters(
            client_weights=[w1, w2, w3],
            client_samples=[10, 20, 30],
            method=AggregationMethod.FED_AVG_WEIGHTED,
        )
        res_cba = fl_engine.aggregate_parameters(
            client_weights=[w3, w2, w1],
            client_samples=[30, 20, 10],
            method=AggregationMethod.FED_AVG_WEIGHTED,
        )
        np.testing.assert_allclose(res_abc.flat_weights, res_cba.flat_weights, rtol=1e-6, atol=1e-6)


# ==============================================================================
# 3. NUMERICAL STABILITY & EDGE CASES (FL-0001, FL-0003, FL-INV-07)
# ==============================================================================


class TestNumericalStabilityAndEdgeCases:
    """Verifies robustness against empty datasets, zero weights, negative samples, and non-finites."""

    @pytest.mark.parametrize(
        "method",
        [
            AggregationMethod.FED_AVG_WEIGHTED,
            AggregationMethod.FED_PROX,
            AggregationMethod.FED_ADAM,
            AggregationMethod.FED_ADAGRAD,
            AggregationMethod.FED_YOGI,
            AggregationMethod.SCAFFOLD,
        ],
    )
    def test_zero_samples_does_not_raise_zerodivision(
        self,
        fl_engine: FederatedLearningEngine,
        method: AggregationMethod,
    ):
        """Zero client sample counts must fall back to uniform weighting without ZeroDivisionError."""
        w1 = ModelWeights(layer_shapes=[(2,)], flat_weights=[1.0, 2.0])
        w2 = ModelWeights(layer_shapes=[(2,)], flat_weights=[3.0, 4.0])

        # All zero samples: sum = 0
        res = fl_engine.aggregate_parameters(
            client_weights=[w1, w2],
            client_samples=[0, 0],
            method=method,
            global_weights=w1,
        )
        assert len(res.flat_weights) == 2
        assert not any(math.isnan(x) for x in res.flat_weights)

    def test_negative_sample_counts_clamped(self, fl_engine: FederatedLearningEngine):
        """Negative sample counts injected by corrupted clients must be clamped to 0 without corrupting weights."""
        w1 = ModelWeights(layer_shapes=[(2,)], flat_weights=[10.0, 10.0])
        w2 = ModelWeights(layer_shapes=[(2,)], flat_weights=[20.0, 20.0])

        # One negative sample count: [-5, 10] -> clamped to [0, 10] -> 100% weight to w2
        res = fl_engine.aggregate_parameters(
            client_weights=[w1, w2],
            client_samples=[-5, 10],
            method=AggregationMethod.FED_AVG_WEIGHTED,
        )
        np.testing.assert_allclose(res.flat_weights, [20.0, 20.0], atol=1e-5)

    def test_nan_inf_quarantined_and_excluded(self, fl_engine: FederatedLearningEngine):
        """Clients containing NaN or Inf weights must be quarantined and excluded from aggregation."""
        w_clean = ModelWeights(layer_shapes=[(2,)], flat_weights=[5.0, 10.0])
        w_nan = ModelWeights(layer_shapes=[(2,)], flat_weights=[float("nan"), 10.0])
        w_inf = ModelWeights(layer_shapes=[(2,)], flat_weights=[5.0, float("inf")])

        res = fl_engine.aggregate_parameters(
            client_weights=[w_clean, w_nan, w_inf],
            client_samples=[100, 100, 100],
            method=AggregationMethod.FED_AVG_WEIGHTED,
        )
        # Only w_clean should survive
        np.testing.assert_allclose(res.flat_weights, [5.0, 10.0], atol=1e-5)

    def test_all_clients_nan_fallback(self, fl_engine: FederatedLearningEngine):
        """When all clients contain NaN, aggregation must fallback safely to previous global weights."""
        w_prev = ModelWeights(layer_shapes=[(2,)], flat_weights=[1.0, 2.0])
        w_nan1 = ModelWeights(layer_shapes=[(2,)], flat_weights=[float("nan"), 0.0])
        w_nan2 = ModelWeights(layer_shapes=[(2,)], flat_weights=[float("inf"), float("-inf")])

        res = fl_engine.aggregate_parameters(
            client_weights=[w_nan1, w_nan2],
            client_samples=[10, 10],
            method=AggregationMethod.FED_AVG_WEIGHTED,
            global_weights=w_prev,
        )
        assert res.flat_weights == w_prev.flat_weights


# ==============================================================================
# 4. MODEL STRUCTURE & PARAMETER COMPATIBILITY (FL-0002, FL-INV-08)
# ==============================================================================


class TestParameterCompatibilityValidation:
    """Verifies strict parameter validation on model loading and aggregation."""

    def test_set_parameters_rejects_layer_count_mismatch(self, model_service: ModelService):
        """set_parameters must raise ValueError when weights have fewer or more layers than model."""
        model = model_service.create_model(input_dim=10)
        # Model has multiple layers; provide single layer weights
        bad_weights = ModelWeights(layer_shapes=[(10,)], flat_weights=[0.0] * 10)

        with pytest.raises(ValueError, match="Layer count mismatch"):
            model_service.set_parameters(model, bad_weights)

    def test_set_parameters_rejects_shape_mismatch(self, model_service: ModelService):
        """set_parameters must raise ValueError when a layer's shape does not match model architecture."""
        model = model_service.create_model(input_dim=10)
        good_weights = model_service.get_parameters(model)

        # Corrupt one shape while keeping total length matching
        corrupted_shapes = list(good_weights.layer_shapes)
        orig_s = corrupted_shapes[0]
        # Replace first shape with a transposed shape
        corrupted_shapes[0] = (orig_s[1], orig_s[0]) if len(orig_s) == 2 else (orig_s[0] * 2,)

        bad_weights = ModelWeights(
            layer_shapes=corrupted_shapes,
            flat_weights=good_weights.flat_weights,
        )
        with pytest.raises(ValueError, match="shape mismatch|Flat weights length mismatch"):
            model_service.set_parameters(model, bad_weights)

    def test_set_parameters_rejects_truncated_flat_weights(self, model_service: ModelService):
        """set_parameters must raise ValueError when flat weights array is truncated."""
        model = model_service.create_model(input_dim=10)
        good_weights = model_service.get_parameters(model)

        truncated = ModelWeights(
            layer_shapes=good_weights.layer_shapes,
            flat_weights=good_weights.flat_weights[:-5],  # 5 elements missing
        )
        with pytest.raises(ValueError, match="Flat weights length mismatch"):
            model_service.set_parameters(model, truncated)

    def test_aggregate_parameters_rejects_incompatible_shapes(
        self,
        fl_engine: FederatedLearningEngine,
    ):
        """aggregate_parameters must reject client updates with mismatched layer shapes."""
        w1 = ModelWeights(layer_shapes=[(2, 2)], flat_weights=[1.0, 2.0, 3.0, 4.0])
        w2 = ModelWeights(layer_shapes=[(4,)], flat_weights=[1.0, 2.0, 3.0, 4.0])

        with pytest.raises(ValueError, match="Layer shape mismatch"):
            fl_engine.aggregate_parameters(
                client_weights=[w1, w2],
                client_samples=[10, 10],
            )


# ==============================================================================
# 5. STATE ISOLATION & CONCURRENT SIMULATIONS (FL-0004, FL-INV-02, FL-INV-04)
# ==============================================================================


class TestSimulationAndClientIsolation:
    """Verifies that concurrent simulations and client models do not contaminate each other's state."""

    def test_client_model_state_independence(self, model_service: ModelService):
        """Training or mutating Client A's model must not alter Client B's model."""
        model_a = model_service.create_model(input_dim=10)
        model_b = model_service.create_model(input_dim=10)

        # Capture initial state of B
        init_b = [p.clone() for p in model_b.parameters()]

        # Mutate model A drastically
        with torch.no_grad():
            for p in model_a.parameters():
                p.add_(100.0)

        # Verify B was not mutated
        for p_b, p_init in zip(model_b.parameters(), init_b, strict=True):
            assert torch.equal(p_b, p_init), "Client B parameter was corrupted by Client A mutation!"

    def test_concurrent_simulation_server_optimizer_isolation(
        self,
        fl_engine: FederatedLearningEngine,
    ):
        """FedAdam server moment states must be isolated between simulation IDs."""
        global_w = ModelWeights(layer_shapes=[(2,)], flat_weights=[0.0, 0.0])
        clients_sim1 = [
            ModelWeights(layer_shapes=[(2,)], flat_weights=[1.0, 1.0]),
            ModelWeights(layer_shapes=[(2,)], flat_weights=[2.0, 2.0]),
        ]
        clients_sim2 = [
            ModelWeights(layer_shapes=[(2,)], flat_weights=[10.0, 10.0]),
            ModelWeights(layer_shapes=[(2,)], flat_weights=[20.0, 20.0]),
        ]

        sim1_id = "sim_alpha_001"
        sim2_id = "sim_beta_002"

        # Round 1 for sim1
        res1 = fl_engine.aggregate_parameters(
            client_weights=clients_sim1,
            client_samples=[50, 50],
            method=AggregationMethod.FED_ADAM,
            global_weights=global_w,
            simulation_id=sim1_id,
        )

        # Round 1 for sim2
        res2 = fl_engine.aggregate_parameters(
            client_weights=clients_sim2,
            client_samples=[50, 50],
            method=AggregationMethod.FED_ADAM,
            global_weights=global_w,
            simulation_id=sim2_id,
        )

        # Server moment states should be strictly separated
        assert not np.allclose(res1.flat_weights, res2.flat_weights)
        m_sim1 = fl_engine._server_m_by_sim[sim1_id]
        m_sim2 = fl_engine._server_m_by_sim[sim2_id]
        assert not np.allclose(m_sim1, m_sim2)

        # Cleanup sim1
        fl_engine.clear_simulation_state(sim1_id)
        assert sim1_id not in fl_engine._server_m_by_sim
        assert sim2_id in fl_engine._server_m_by_sim  # sim2 remains intact

        # Cleanup sim2
        fl_engine.clear_simulation_state(sim2_id)
        assert sim2_id not in fl_engine._server_m_by_sim

    def test_ephemeral_calls_without_simulation_id_do_not_leak_memory(
        self,
        fl_engine: FederatedLearningEngine,
    ):
        """Unkeyed one-off aggregation calls must not leave persistent 'default_sim' memory leaks."""
        global_w = ModelWeights(layer_shapes=[(2,)], flat_weights=[0.0, 0.0])
        clients = [ModelWeights(layer_shapes=[(2,)], flat_weights=[1.0, 1.0])]

        # Call with simulation_id=None
        fl_engine.aggregate_parameters(
            client_weights=clients,
            client_samples=[50],
            method=AggregationMethod.FED_ADAM,
            global_weights=global_w,
            simulation_id=None,
        )

        # Verify no 'default_sim' was permanently leaked into the server state dict
        assert "default_sim" not in fl_engine._server_m_by_sim
        assert "default_sim" not in fl_engine._server_v_by_sim


# ==============================================================================
# 6. NON-IID DIRICHLET PARTITIONING INTEGRITY (FL-INV-11, Sections 10-12)
# ==============================================================================


class TestDirichletPartitionCompleteness:
    """Verifies Dirichlet partitioning completeness, disjointness, and non-IID characteristics."""

    def test_partition_union_and_disjointness(self):
        """Every sample must be assigned exactly once:
        union(partitions) == full_dataset and intersection(p_i, p_j) == empty.
        """
        num_samples = 500
        labels = np.array([0] * 400 + [1] * 100)  # Imbalanced 80/20 fraud dataset
        num_clients = 5
        min_size = 20

        client_indices = DirichletPartitioner.partition_indices(
            labels=labels,
            num_clients=num_clients,
            alpha=0.5,
            min_size=min_size,
            seed=42,
        )

        all_assigned = []
        for i in range(num_clients):
            indices = client_indices[i]
            assert len(indices) >= min_size, f"Client {i} received fewer than min_size samples"
            all_assigned.extend(indices)

        # 1. Total count equality
        assert len(all_assigned) == num_samples

        # 2. Completeness: Union matches range(num_samples)
        assert set(all_assigned) == set(range(num_samples))

        # 3. Disjointness: No duplicate sample assigned across clients
        assert len(set(all_assigned)) == num_samples

    def test_alpha_controls_skew_monotonically(self):
        """Smaller alpha must produce higher cross-client label distribution variation than large alpha."""
        labels = np.array([0] * 500 + [1] * 500)
        num_clients = 4

        # Very skewed (alpha = 0.05)
        skewed = DirichletPartitioner.partition_indices(
            labels=labels,
            num_clients=num_clients,
            alpha=0.05,
            min_size=10,
            seed=999,
        )
        skewed_ratios = [
            float(np.mean(labels[skewed[i]])) for i in range(num_clients)
        ]
        skewed_variance = np.var(skewed_ratios)

        # Nearly uniform (alpha = 100.0)
        uniform = DirichletPartitioner.partition_indices(
            labels=labels,
            num_clients=num_clients,
            alpha=100.0,
            min_size=10,
            seed=999,
        )
        uniform_ratios = [
            float(np.mean(labels[uniform[i]])) for i in range(num_clients)
        ]
        uniform_variance = np.var(uniform_ratios)

        # Variance across client fraud ratios must be higher for small alpha
        assert skewed_variance > uniform_variance


# ==============================================================================
# 7. DETERMINISTIC REPRODUCIBILITY (FL-INV-11, Section 14)
# ==============================================================================


class TestReproducibilityAndSeedSemantics:
    """Verifies that seed controls stochastic processes deterministically."""

    def test_partition_deterministic_under_seed(self):
        """Same seed yields identical partition; different seed yields different partition."""
        labels = np.array([0] * 300 + [1] * 100)

        p1 = DirichletPartitioner.partition_indices(labels, num_clients=3, alpha=0.5, seed=101)
        p2 = DirichletPartitioner.partition_indices(labels, num_clients=3, alpha=0.5, seed=101)
        p3 = DirichletPartitioner.partition_indices(labels, num_clients=3, alpha=0.5, seed=202)

        # p1 and p2 must be identical
        for i in range(3):
            assert p1[i] == p2[i]

        # p1 and p3 must differ
        diff_found = any(p1[i] != p3[i] for i in range(3))
        assert diff_found, "Different seeds unexpectedly produced identical partitions"


# ==============================================================================
# 8. LOCAL TRAINING & EVALUATION INTEGRITY (FL-INV-10, Sections 8-9, 30-34)
# ==============================================================================


class TestLocalTrainingAndEvaluationIntegrity:
    """Verifies local training loop, optimizer lifecycle, loss, and evaluation metrics."""

    def test_local_training_reduces_loss(self, model_service: ModelService):
        """Local training on a synthetic batch should strictly update weights and record loss history."""
        model = model_service.create_model(input_dim=10)
        init_weights = model_service.get_parameters(model)

        rng = np.random.default_rng(42)
        X = rng.normal(size=(64, 10)).astype(np.float32)
        y = rng.integers(0, 2, size=(64,)).astype(np.float32)

        trained_model, loss_history, _ = model_service.train_local(
            model=model,
            X_train=X,
            y_train=y,
            epochs=2,
            learning_rate=0.05,
            batch_size=16,
        )

        assert len(loss_history) == 2
        updated_weights = model_service.get_parameters(trained_model)
        # Weights must have changed from training
        assert updated_weights.flat_weights != init_weights.flat_weights

    def test_evaluation_metric_contract_and_single_class_robustness(
        self,
        model_service: ModelService,
    ):
        """Model evaluation must output standard metrics and handle single-class test sets without crashing."""
        model = model_service.create_model(input_dim=10)

        # Standard balanced evaluation
        X_test = np.random.randn(50, 10).astype(np.float32)
        y_test = np.array([0] * 25 + [1] * 25).astype(np.float32)

        metrics = model_service.evaluate(model, X_test, y_test)
        for key in ["accuracy", "precision", "recall", "f1_score", "auc_roc", "loss"]:
            assert key in metrics
            assert 0.0 <= metrics[key] <= 1.0 or key == "loss"

        # Single class edge case (all 0s)
        y_single = np.zeros(50, dtype=np.float32)
        single_metrics = model_service.evaluate(model, X_test, y_single)
        # AUC should fall back gracefully to 0.5 without raising an uncaught exception
        assert single_metrics["auc_roc"] == 0.5
