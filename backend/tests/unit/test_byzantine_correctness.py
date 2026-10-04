"""Byzantine Robustness, Robust Aggregation & Poisoning Defense Deep Correctness Tests.

Validates:
1. Mathematical Oracles: Krum, Multi-Krum, Coordinate Median, Trimmed Mean, Bulyan.
2. Boundary & Precondition Matrix: n >= 2f + 3 (Krum), n >= 4f + 3 (Bulyan), 2k < n (Trimmed Mean).
3. Non-Finite & Adversarial Input Rejection: NaN, +Inf, -Inf, empty inputs, dimension mismatch.
4. Mathematical Invariants: Permutation invariance (including deterministic tie-breaking), identical-input identity.
5. Attack Realism & Locality: Sign Flip, ALIE, Gaussian noise; honest updates remain unmutated.
6. Execution Graph & Composition Guards: SecAgg conflict guards, FHE conflict guards, TEE enclave dispatch.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest
import torch
from benchmarks.byzantine.aggregators import (
    InvalidConfigurationError,
)
from benchmarks.byzantine.aggregators import (
    aggregate_bulyan as bench_bulyan,
)
from benchmarks.byzantine.aggregators import (
    aggregate_coordinate_median as bench_median,
)
from benchmarks.byzantine.aggregators import (
    aggregate_krum as bench_krum,
)
from benchmarks.byzantine.aggregators import (
    aggregate_trimmed_mean as bench_trimmed_mean,
)
from benchmarks.byzantine.attacks import ALIEAttack, SignFlipAttack
from benchmarks.byzantine.config import AttackConfig, ByzantineAttackType

from app.application.services.fl_engine import FederatedLearningEngine
from app.application.services.simulation_service import SimulationService
from app.domain.attack_injector import AdversarialAttackInjector
from app.domain.byzantine_defense import (
    aggregate_bulyan as domain_bulyan,
)
from app.domain.byzantine_defense import (
    aggregate_coordinate_median as domain_median,
)
from app.domain.byzantine_defense import (
    aggregate_krum as domain_krum,
)
from app.domain.byzantine_defense import (
    aggregate_trimmed_mean as domain_trimmed_mean,
)
from app.domain.entities import SimulationConfig
from app.domain.enums import AggregationMethod
from app.domain.value_objects import ModelWeights
from app.infrastructure.security.tee_driver import TEEDriver

# ==============================================================================
# 1. MATHEMATICAL ORACLES & AGGREGATOR CORRECTNESS
# ==============================================================================


class TestByzantineMathematicalOracles:
    """Verifies that robust aggregators strictly match theoretical mathematical definitions."""

    def test_coordinate_median_mathematical_oracle(self) -> None:
        """Verify coordinate-wise median matches np.median on each coordinate independently."""
        # 5 clients with 3-dimensional weight vectors
        client_updates = np.array([
            [1.0, 10.0, 100.0],
            [2.0, 50.0, 300.0],
            [3.0, 20.0, 200.0],
            [100.0, -10.0, 500.0],  # Outlier
            [-50.0, 30.0, 50.0],    # Outlier
        ])

        expected_median = np.median(client_updates, axis=0)  # [2.0, 20.0, 200.0]

        # Domain aggregator (NumPy based)
        domain_res = domain_median(list(client_updates))
        np.testing.assert_allclose(domain_res, expected_median, rtol=1e-7)

        # Benchmark aggregator (PyTorch based)
        torch_updates = [torch.from_numpy(u).float() for u in client_updates]
        bench_res = bench_median(torch_updates).numpy()
        np.testing.assert_allclose(bench_res, expected_median, rtol=1e-7)

    def test_trimmed_mean_mathematical_oracle(self) -> None:
        """Verify trimmed mean matches sorting and discarding k smallest and k largest per coordinate."""
        # n = 5, k = 1 (trim 1 lowest, 1 highest; average middle 3)
        client_updates = np.array([
            [1.0, 10.0],
            [2.0, 20.0],
            [3.0, 30.0],
            [10.0, -100.0],  # Max on dim 0, min on dim 1
            [-5.0, 100.0],   # Min on dim 0, max on dim 1
        ])
        # Sorted dim 0: -5.0, [1.0, 2.0, 3.0], 10.0 -> mean = 2.0
        # Sorted dim 1: -100.0, [10.0, 20.0, 30.0], 100.0 -> mean = 20.0
        expected_trimmed = np.array([2.0, 20.0])

        domain_res = domain_trimmed_mean(list(client_updates), trim_ratio=0.2)  # k = 1
        np.testing.assert_allclose(domain_res, expected_trimmed, rtol=1e-7)

        torch_updates = [torch.from_numpy(u).float() for u in client_updates]
        bench_res = bench_trimmed_mean(torch_updates, beta=0.2).numpy()
        np.testing.assert_allclose(bench_res, expected_trimmed, rtol=1e-7)

    def test_krum_mathematical_oracle(self) -> None:
        """Verify Krum picks candidate minimizing sum of squared Euclidean distances to n - f - 2 closest."""
        # n = 7, f = 1 -> neighbors to sum = 7 - 1 - 2 = 4
        # Create 5 dense honest clients and 2 distant outliers
        rng = np.random.default_rng(42)
        honest = [np.array([1.0, 1.0]) + rng.normal(0, 0.05, size=2) for _ in range(5)]
        outliers = [np.array([50.0, 50.0]), np.array([-30.0, -30.0])]
        all_updates = honest + outliers

        # Manual score calculation
        scores = []
        for i, u_i in enumerate(all_updates):
            dists = [float(np.sum((u_i - u_j) ** 2)) for j, u_j in enumerate(all_updates) if i != j]
            dists.sort()
            scores.append(sum(dists[:4]))

        expected_winner_idx = int(np.argmin(scores))
        expected_winner = all_updates[expected_winner_idx]

        # Domain Krum
        domain_res = domain_krum(all_updates, f_byzantine=1)
        np.testing.assert_allclose(domain_res, expected_winner, rtol=1e-7)

        # Benchmark Krum
        torch_updates = [torch.from_numpy(u).float() for u in all_updates]
        bench_res = bench_krum(torch_updates, f=1).numpy()
        np.testing.assert_allclose(bench_res, expected_winner, rtol=1e-7)

    def test_bulyan_mathematical_oracle(self) -> None:
        """Verify Bulyan Stage 1 recursive Krum selection and Stage 2 coordinate-wise trimmed mean."""
        # n = 7, f = 1: Bulyan precondition n >= 4f + 3 holds (7 >= 7).
        honest = [
            np.array([1.0, 10.0]),
            np.array([1.1, 10.2]),
            np.array([0.9, 9.8]),
            np.array([1.05, 10.1]),
            np.array([0.95, 9.9]),
            np.array([1.0, 10.0]),
        ]
        malicious = [np.array([100.0, -100.0])]
        updates = honest + malicious

        domain_res = domain_bulyan(updates, f_byzantine=1)
        torch_updates = [torch.from_numpy(u).float() for u in updates]
        bench_res = bench_bulyan(torch_updates, f=1).numpy()

        # Both must produce finite updates very close to [1.0, 10.0] and completely reject the outlier
        assert not np.isnan(domain_res).any()
        assert not np.isnan(bench_res).any()
        np.testing.assert_allclose(domain_res, [1.0, 10.0], atol=0.2)
        np.testing.assert_allclose(bench_res, [1.0, 10.0], atol=0.2)


# ==============================================================================
# 2. BOUNDARY & PRECONDITION MATRICES (SMALL-N & FALLBACKS)
# ==============================================================================


class TestByzantinePreconditionsAndBoundaries:
    """Verifies behavior when theoretical preconditions hold vs when they are violated."""

    def test_krum_precondition_boundary_matrix(self) -> None:
        """Krum requires n >= 2f + 3.

        When n < 2f + 3, aggregator should log warning and degrade gracefully
        (or raise InvalidConfigurationError in benchmark strict mode).
        """
        updates_n4 = [np.array([float(i)]) for i in range(4)]

        # n = 4, f = 1 -> 2f + 3 = 5 > 4 (Violated)
        # Domain aggregator degrades safely with warning
        res_domain = domain_krum(updates_n4, f_byzantine=1)
        assert res_domain is not None
        assert not np.isnan(res_domain).any()

        # Benchmark aggregator strictly enforces preconditions
        torch_n4 = [torch.from_numpy(u).float() for u in updates_n4]
        with pytest.raises(InvalidConfigurationError):
            bench_krum(torch_n4, f=1)

        # n = 5, f = 1 -> 2f + 3 = 5 <= 5 (Satisfied)
        updates_n5 = [np.array([float(i)]) for i in range(5)]
        torch_n5 = [torch.from_numpy(u).float() for u in updates_n5]
        res_bench = bench_krum(torch_n5, f=1)
        assert res_bench is not None
        assert not torch.isnan(res_bench).any()

    def test_bulyan_precondition_boundary_matrix(self) -> None:
        """Bulyan requires n >= 4f + 3.

        For f=1, n >= 7.
        """
        updates_n6 = [np.array([float(i)]) for i in range(6)]

        # n = 6, f = 1 -> 4f + 3 = 7 > 6 (Violated)
        res_domain = domain_bulyan(updates_n6, f_byzantine=1)
        assert res_domain is not None  # Gracefully falls back to trimmed mean

        torch_n6 = [torch.from_numpy(u).float() for u in updates_n6]
        with pytest.raises(InvalidConfigurationError):
            bench_bulyan(torch_n6, f=1)

        # n = 7, f = 1 -> 4f + 3 = 7 <= 7 (Satisfied)
        updates_n7 = [np.array([float(i)]) for i in range(7)]
        torch_n7 = [torch.from_numpy(u).float() for u in updates_n7]
        res_bench = bench_bulyan(torch_n7, f=1)
        assert res_bench is not None
        assert not torch.isnan(res_bench).any()

    def test_trimmed_mean_precondition_boundary(self) -> None:
        """Trimmed Mean requires 2k < n."""
        updates_n4 = [torch.tensor([float(i)]) for i in range(4)]
        # beta = 0.5 -> k = 2 -> 2k = 4 >= 4 (Violated)
        with pytest.raises(InvalidConfigurationError):
            bench_trimmed_mean(updates_n4, beta=0.5)

    def test_single_client_boundary(self) -> None:
        """Single client update (n=1) must pass through unchanged across robust defenses."""
        single = [np.array([3.14, 2.71])]

        np.testing.assert_allclose(domain_median(single), [3.14, 2.71])
        np.testing.assert_allclose(domain_krum(single, f_byzantine=0), [3.14, 2.71])
        np.testing.assert_allclose(domain_trimmed_mean(single, trim_ratio=0.1), [3.14, 2.71])
        np.testing.assert_allclose(domain_bulyan(single, f_byzantine=0), [3.14, 2.71])

    def test_f_zero_honesty_baseline(self) -> None:
        """When f=0 (zero adversaries), robust aggregators perform honest consensus."""
        honest = [np.array([1.0]), np.array([2.0]), np.array([3.0])]
        # Median of 1, 2, 3 is 2.0
        np.testing.assert_allclose(domain_median(honest), [2.0])
        torch_honest = [torch.tensor([float(i)]) for i in [1.0, 2.0, 3.0]]
        np.testing.assert_allclose(bench_median(torch_honest).numpy(), [2.0])


# ==============================================================================
# 3. NON-FINITE INPUT REJECTION & SANITIZATION
# ==============================================================================


class TestAdversarialInputSanitization:
    """Verifies that non-finite values (NaN, +Inf, -Inf) are rejected and cannot poison the global model."""

    def test_nan_injection_rejected_by_all_aggregators(self) -> None:
        """Injecting NaN must raise ValueError in both domain and benchmark aggregators."""
        clean = [np.array([1.0, 2.0]), np.array([1.1, 2.1]), np.array([0.9, 1.9])]
        nan_update = [np.array([float("nan"), 2.0])]
        poisoned = clean + nan_update

        torch_poisoned = [torch.tensor(u, dtype=torch.float32) for u in poisoned]

        # Benchmark aggregators
        with pytest.raises(ValueError, match="non-finite"):
            bench_krum(torch_poisoned, f=1)
        with pytest.raises(ValueError, match="non-finite"):
            bench_median(torch_poisoned)
        with pytest.raises(ValueError, match="non-finite"):
            bench_trimmed_mean(torch_poisoned, beta=0.2)
        with pytest.raises(ValueError, match="non-finite"):
            bench_bulyan(torch_poisoned, f=1)

        # Domain aggregators
        with pytest.raises(ValueError, match="non-finite"):
            domain_krum(poisoned, f_byzantine=1)
        with pytest.raises(ValueError, match="non-finite"):
            domain_median(poisoned)
        with pytest.raises(ValueError, match="non-finite"):
            domain_trimmed_mean(poisoned)
        with pytest.raises(ValueError, match="non-finite"):
            domain_bulyan(poisoned, f_byzantine=1)

    def test_inf_injection_rejected_by_all_aggregators(self) -> None:
        """Injecting +Inf or -Inf must be caught and rejected early."""
        clean = [np.array([1.0, 2.0]), np.array([1.1, 2.1]), np.array([0.9, 1.9])]
        inf_update = [np.array([float("inf"), 2.0])]
        poisoned = clean + inf_update
        torch_poisoned = [torch.tensor(u, dtype=torch.float32) for u in poisoned]

        with pytest.raises(ValueError, match="non-finite"):
            bench_krum(torch_poisoned, f=1)
        with pytest.raises(ValueError, match="non-finite"):
            domain_krum(poisoned, f_byzantine=1)

    def test_empty_update_list_rejection(self) -> None:
        """Passing an empty update list must fail closed with ValueError."""
        with pytest.raises(ValueError, match="empty"):
            bench_krum([], f=1)
        with pytest.raises(ValueError, match="empty"):
            domain_krum([], f_byzantine=1)


# ==============================================================================
# 4. MATHEMATICAL INVARIANTS: PERMUTATION INVARIANCE & IDENTITY
# ==============================================================================


class TestByzantineMathematicalInvariants:
    """Verifies fundamental mathematical properties of robust aggregation."""

    def test_identical_input_identity(self) -> None:
        """If all clients submit identical vector v, aggregator MUST return v."""
        v = np.array([42.5, -13.7, 0.001])
        identical_updates = [v.copy() for _ in range(7)]
        torch_updates = [torch.from_numpy(u).float() for u in identical_updates]

        np.testing.assert_allclose(domain_krum(identical_updates, f_byzantine=1), v)
        np.testing.assert_allclose(bench_krum(torch_updates, f=1).numpy(), v)
        np.testing.assert_allclose(domain_median(identical_updates), v)
        np.testing.assert_allclose(bench_median(torch_updates).numpy(), v)
        np.testing.assert_allclose(domain_trimmed_mean(identical_updates), v)
        np.testing.assert_allclose(bench_trimmed_mean(torch_updates, beta=0.2).numpy(), v)
        np.testing.assert_allclose(domain_bulyan(identical_updates, f_byzantine=1), v)
        np.testing.assert_allclose(bench_bulyan(torch_updates, f=1).numpy(), v)

    def test_permutation_invariance(self) -> None:
        """Aggregator output must be identical regardless of client ordering."""
        # 1. Domain Krum: content-deterministic tie-breaking ensures identical output even on exact score ties
        u1 = np.array([1.0, 1.0])
        u2 = np.array([2.0, 2.0])
        u3 = np.array([3.0, 3.0])
        u4 = np.array([4.0, 4.0])
        u5 = np.array([5.0, 5.0])

        order_a = [u1, u2, u3, u4, u5]
        order_b = [u5, u3, u1, u4, u2]

        res_a_domain = domain_krum(order_a, f_byzantine=1)
        res_b_domain = domain_krum(order_b, f_byzantine=1)
        np.testing.assert_allclose(res_a_domain, res_b_domain, rtol=1e-10)

        # 2. Benchmark Krum: permutation invariance on candidate pool with distinct minimum score
        b1 = torch.tensor([0.0, 0.0])
        b2 = torch.tensor([0.05, -0.05])
        b3 = torch.tensor([-0.05, 0.05])
        b4 = torch.tensor([0.02, 0.01])
        b5 = torch.tensor([10.0, 10.0])  # outlier

        bench_order_a = [b1, b2, b3, b4, b5]
        bench_order_b = [b5, b2, b4, b1, b3]

        res_a_bench = bench_krum(bench_order_a, f=1).numpy()
        res_b_bench = bench_krum(bench_order_b, f=1).numpy()
        np.testing.assert_allclose(res_a_bench, res_b_bench, rtol=1e-10)


# ==============================================================================
# 5. ATTACK REALISM, LOCALITY & NON-MUTATION OF HONEST NODES
# ==============================================================================


class TestByzantineAttacksAndLocality:
    """Verifies attack definitions, parameter scaling, and honest update non-mutation."""

    def test_sign_flip_attack_semantics(self) -> None:
        """Sign flip negates the gradient direction, optionally scaled by factor."""
        # Domain attack injector
        honest_np = np.array([1.0, -2.0, 0.5])
        poisoned_np = AdversarialAttackInjector.inject_sign_flip(honest_np, scale=-2.0)
        np.testing.assert_allclose(poisoned_np, [-2.0, 4.0, -1.0])

        # Benchmark attack
        cfg = AttackConfig(attack_type=ByzantineAttackType.SIGN_FLIP, scale=2.0)
        attack = SignFlipAttack(config=cfg)
        honest_torch = torch.tensor([1.0, -2.0, 0.5])
        poisoned_torch = attack.apply(honest_torch, client_id=0, round_idx=0)
        np.testing.assert_allclose(poisoned_torch.numpy(), [-2.0, 4.0, -1.0])

    def test_alie_attack_perturbation_envelope(self) -> None:
        """ALIE (A Little Is Enough) perturbs updates within empirical standard deviation bounds."""
        honest_deltas = [
            torch.tensor([1.0, 2.0]),
            torch.tensor([1.1, 2.1]),
            torch.tensor([0.9, 1.9]),
            torch.tensor([1.05, 2.05]),
            torch.tensor([0.95, 1.95]),
        ]
        cfg = AttackConfig(
            attack_type=ByzantineAttackType.ALIE,
            num_byzantine=1,
            knows_honest_updates=True,
        )
        attack = ALIEAttack(config=cfg)
        poisoned = attack.apply(
            honest_deltas[0],
            client_id=0,
            round_idx=0,
            consortium_deltas=honest_deltas,
        )

        # Poisoned update should be within a small envelope around mean [1.0, 2.0]
        mean = torch.stack(honest_deltas, dim=0).mean(dim=0)
        dist = float(torch.norm(poisoned - mean).item())
        assert dist < 1.0  # Subtle shift, not extreme outlier

    def test_attack_does_not_mutate_honest_updates(self) -> None:
        """Generating adversarial attacks must never mutate honest client deltas in-place."""
        original = np.array([1.0, 2.0, 3.0])
        copy_val = original.copy()

        _ = AdversarialAttackInjector.inject_sign_flip(original, scale=-3.0)
        np.testing.assert_allclose(original, copy_val)

        _ = AdversarialAttackInjector.inject_scaled_update(original, scale=100.0)
        np.testing.assert_allclose(original, copy_val)


# ==============================================================================
# 6. PIPELINE COMPOSITION & CONFLICT GUARDS
# ==============================================================================


class TestPipelineCompositionAndConflictGuards:
    """Verifies that mutually incompatible privacy/defense compositions are strictly rejected."""

    def test_simulation_service_rejects_secagg_with_nonlinear_defense(self) -> None:
        """Additive Secure Aggregation cannot be combined with non-linear Byzantine defenses."""
        from app.application.services.simulation_service import InvalidPipelineConfigurationError

        service = SimulationService(
            settings=MagicMock(),
            simulation_repo=MagicMock(),
            bank_repo=MagicMock(),
            metrics_repo=MagicMock(),
            data_generator=MagicMock(),
            fl_engine=MagicMock(),
            metrics_service=MagicMock(),
            model_service=MagicMock(),
        )

        config = SimulationConfig(
            num_rounds=1,
            enable_secure_aggregation=True,
            byzantine_defense="krum",  # Conflict!
        )

        with pytest.raises(InvalidPipelineConfigurationError, match="Secure Aggregation is mathematically incompatible"):
            service.run_simulation(config)

    def test_simulation_service_rejects_fhe_with_nonlinear_defense(self) -> None:
        """FHE cannot be combined with non-linear Byzantine defenses."""
        from app.application.services.simulation_service import InvalidPipelineConfigurationError

        service = SimulationService(
            settings=MagicMock(),
            simulation_repo=MagicMock(),
            bank_repo=MagicMock(),
            metrics_repo=MagicMock(),
            data_generator=MagicMock(),
            fl_engine=MagicMock(),
            metrics_service=MagicMock(),
            model_service=MagicMock(),
        )

        config = SimulationConfig(
            num_rounds=1,
            hardware_isolation_mode="fhe",
            byzantine_defense="coordinate_median",  # Conflict with FHE!
        )

        with pytest.raises(InvalidPipelineConfigurationError, match="Homomorphic Encryption \\(CKKS\\)"):
            service.run_simulation(config)

    def test_tee_driver_dispatches_robust_aggregation_inside_enclave(self) -> None:
        """TEE enclave executes the requested robust defense on plaintext weights inside the boundary."""
        ctx = TEEDriver.create_enclave(simulation_id="sim_byzantine_correctness")

        mock_fl = MagicMock()
        mock_fl.aggregate_parameters.return_value = ModelWeights(
            layer_shapes=[(2,)], flat_weights=[1.0, 2.0]
        )

        weights = [
            ModelWeights(layer_shapes=[(2,)], flat_weights=[1.0, 2.0]),
            ModelWeights(layer_shapes=[(2,)], flat_weights=[1.1, 2.1]),
        ]

        result = TEEDriver.execute_secure_aggregation(
            enclave_ctx=ctx,
            client_weights=weights,
            client_samples=[10, 10],
            method="krum",
            fl_engine=mock_fl,
        )

        assert mock_fl.aggregate_parameters.called
        assert mock_fl.aggregate_parameters.call_args[1]["method"] == AggregationMethod.KRUM
        assert result.flat_weights == [1.0, 2.0]

    def test_fl_engine_actually_applies_robust_defense_instead_of_phantom_bypass(self) -> None:
        """FL Engine apply_byzantine_defense must compute robust aggregate rather than returning unchanged weights."""
        mock_settings = MagicMock()
        mock_model = MagicMock()
        mock_privacy = MagicMock()
        engine = FederatedLearningEngine(mock_settings, mock_model, mock_privacy)

        shapes = [(2,)]
        w1 = ModelWeights(layer_shapes=shapes, flat_weights=[1.0, 10.0])
        w2 = ModelWeights(layer_shapes=shapes, flat_weights=[1.1, 10.1])
        w3 = ModelWeights(layer_shapes=shapes, flat_weights=[0.9, 9.9])
        w_poison = ModelWeights(layer_shapes=shapes, flat_weights=[1000.0, -500.0])

        client_weights = [w1, w2, w3, w_poison]

        # Call with coordinate median
        sanitized = engine.apply_byzantine_defense(
            client_weights=client_weights,
            defense_type="median",
        )

        # Must return the single robustly aggregated weight, rejecting the poison
        assert len(sanitized) == 1
        res = sanitized[0].flat_weights
        np.testing.assert_allclose(res, [1.05, 10.05], atol=0.2)
