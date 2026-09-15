"""Unit tests for the privacy service."""

import numpy as np
import pytest

from app.application.services.privacy_service import (
    PrivacyBudget,
    PrivacyBudgetExceededError,
    PrivacyService,
)
from app.domain.value_objects import ModelWeights


@pytest.fixture
def privacy_service() -> PrivacyService:
    return PrivacyService()


@pytest.fixture
def sample_weights() -> ModelWeights:
    return ModelWeights(
        layer_shapes=[(5,)],
        flat_weights=[1.0, 2.0, 3.0, 4.0, 5.0],
    )


class TestPrivacyBudget:
    def test_initial_budget(self) -> None:
        budget = PrivacyBudget(epsilon_per_round=1.0, delta=1e-5)
        assert budget.total_epsilon == 0.0
        assert budget.rounds_spent == 0

    def test_spending_accumulates(self) -> None:
        budget = PrivacyBudget(epsilon_per_round=1.0, delta=1e-5)
        budget.spend(1.0)
        budget.spend(1.0)
        assert budget.rounds_spent == 2
        assert budget.total_epsilon == 2.0

    def test_history_tracked(self) -> None:
        budget = PrivacyBudget(epsilon_per_round=1.0)
        budget.spend(0.5)
        budget.spend(0.8)
        assert budget.history == [0.5, 0.8]


class TestDifferentialPrivacy:
    def test_noise_changes_weights(
        self,
        privacy_service: PrivacyService,
        sample_weights: ModelWeights,
    ) -> None:
        noised = privacy_service.add_noise_to_weights(
            sample_weights,
            epsilon=1.0,
            rng=np.random.default_rng(42),
        )
        assert noised.flat_weights != sample_weights.flat_weights

    def test_lower_epsilon_adds_more_noise(
        self,
        privacy_service: PrivacyService,
        sample_weights: ModelWeights,
    ) -> None:
        rng = np.random.default_rng(42)
        noised_high_eps = privacy_service.add_noise_to_weights(
            sample_weights,
            epsilon=10.0,
            rng=rng,
        )
        rng = np.random.default_rng(42)
        noised_low_eps = privacy_service.add_noise_to_weights(
            sample_weights,
            epsilon=0.1,
            rng=rng,
        )

        # Lower epsilon should produce larger deviations
        dev_high = np.std(
            np.array(noised_high_eps.flat_weights) - np.array(sample_weights.flat_weights),
        )
        dev_low = np.std(
            np.array(noised_low_eps.flat_weights) - np.array(sample_weights.flat_weights),
        )
        assert dev_low > dev_high

    def test_preserves_shape(
        self,
        privacy_service: PrivacyService,
        sample_weights: ModelWeights,
    ) -> None:
        noised = privacy_service.add_noise_to_weights(sample_weights, epsilon=1.0)
        assert noised.layer_shapes == sample_weights.layer_shapes
        assert len(noised.flat_weights) == len(sample_weights.flat_weights)


class TestGradientClipping:
    def test_clips_large_update(self, privacy_service: PrivacyService) -> None:
        original = ModelWeights(layer_shapes=[(3,)], flat_weights=[0.0, 0.0, 0.0])
        updated = ModelWeights(layer_shapes=[(3,)], flat_weights=[10.0, 10.0, 10.0])

        clipped = privacy_service.clip_model_update(original, updated, max_norm=1.0)

        # The L2 norm of the clipped update should be <= 1.0
        delta = np.array(clipped.flat_weights) - np.array(original.flat_weights)
        assert np.linalg.norm(delta) <= 1.0 + 1e-6

    def test_small_update_unchanged(self, privacy_service: PrivacyService) -> None:
        original = ModelWeights(layer_shapes=[(3,)], flat_weights=[0.0, 0.0, 0.0])
        updated = ModelWeights(layer_shapes=[(3,)], flat_weights=[0.1, 0.1, 0.1])

        clipped = privacy_service.clip_model_update(original, updated, max_norm=10.0)

        np.testing.assert_allclose(
            clipped.flat_weights,
            updated.flat_weights,
            atol=1e-10,
        )


class TestBudgetManagement:
    def test_create_and_retrieve_budget(self, privacy_service: PrivacyService) -> None:
        budget = privacy_service.get_or_create_budget("sim_1", epsilon=2.0)
        assert budget.epsilon_per_round == 2.0

        # Should return the same budget
        budget2 = privacy_service.get_or_create_budget("sim_1")
        assert budget is budget2

    def test_clear_budget(self, privacy_service: PrivacyService) -> None:
        privacy_service.get_or_create_budget("sim_1")
        privacy_service.clear_budget("sim_1")

        # Creating again should be a fresh budget
        budget = privacy_service.get_or_create_budget("sim_1", epsilon=5.0)
        assert budget.epsilon_per_round == 5.0
        assert budget.rounds_spent == 0


class TestOpacusRecording:
    def test_record_opacus_epsilon(self, privacy_service: PrivacyService) -> None:
        privacy_service.record_opacus_epsilon("sim_opacus", 0.6)
        budget = privacy_service.get_or_create_budget("sim_opacus")
        assert budget.rounds_spent == 1
        assert budget.history == [0.6]

    def test_record_opacus_epsilon_accumulates(self, privacy_service: PrivacyService) -> None:
        privacy_service.record_opacus_epsilon("sim_opacus_2", 0.5)
        privacy_service.record_opacus_epsilon("sim_opacus_2", 0.7)
        budget = privacy_service.get_or_create_budget("sim_opacus_2")
        assert budget.rounds_spent == 2
        assert budget.history == [0.5, 0.7]


class TestPrivacyBudgetLimits:
    def test_spend_within_limit_succeeds(self) -> None:
        budget = PrivacyBudget(epsilon_per_round=1.0)
        budget.spend(2.0, limit=8.0)
        budget.spend(4.5, limit=8.0)
        assert budget.total_epsilon == 6.5

    def test_spend_exceeding_limit_raises_error(self) -> None:
        budget = PrivacyBudget(epsilon_per_round=1.0)
        budget.spend(5.0, limit=8.0)
        with pytest.raises(PrivacyBudgetExceededError) as exc_info:
            budget.spend(4.0, limit=8.0)
        assert "Cumulative privacy budget exceeded" in str(exc_info.value)
        assert budget.total_epsilon == 9.0

    def test_record_opacus_exceeding_limit_raises_error(
        self, privacy_service: PrivacyService
    ) -> None:
        privacy_service.record_opacus_epsilon("sim_limit", 5.0, limit=8.0)
        with pytest.raises(PrivacyBudgetExceededError) as exc_info:
            privacy_service.record_opacus_epsilon("sim_limit", 3.5, limit=8.0)
        assert "Cumulative privacy budget exceeded" in str(exc_info.value)


class TestInputValidationAndZeroNoiseDefense:
    """Verifies that all zero-noise bypasses, invalid bounds, and negative sensitivities are rejected."""

    def test_noise_scale_invalid_parameters(self, privacy_service: PrivacyService) -> None:
        with pytest.raises(ValueError, match="Epsilon must be positive"):
            privacy_service.calculate_gaussian_noise_scale(epsilon=0.0)
        with pytest.raises(ValueError, match="Epsilon must be positive"):
            privacy_service.calculate_gaussian_noise_scale(epsilon=-1.0)
        with pytest.raises(ValueError, match="Delta must be in"):
            privacy_service.calculate_gaussian_noise_scale(delta=0.0)
        with pytest.raises(ValueError, match="Delta must be in"):
            privacy_service.calculate_gaussian_noise_scale(delta=1.0)
        with pytest.raises(ValueError, match="Sensitivity must be positive"):
            privacy_service.calculate_gaussian_noise_scale(sensitivity=0.0)
        with pytest.raises(ValueError, match="Sensitivity must be positive"):
            privacy_service.calculate_gaussian_noise_scale(sensitivity=-0.5)

    def test_add_noise_invalid_parameters(
        self, privacy_service: PrivacyService, sample_weights: ModelWeights
    ) -> None:
        with pytest.raises(ValueError, match="Epsilon must be positive"):
            privacy_service.add_noise_to_weights(sample_weights, epsilon=0.0)
        with pytest.raises(ValueError, match="Delta must be in"):
            privacy_service.add_noise_to_weights(sample_weights, delta=1.5)
        with pytest.raises(ValueError, match="max_grad_norm must be positive"):
            privacy_service.add_noise_to_weights(sample_weights, max_grad_norm=0.0)
        with pytest.raises(ValueError, match="sensitivity must be positive"):
            privacy_service.add_noise_to_weights(sample_weights, sensitivity=-1.0)

    def test_clip_model_update_negative_max_norm(
        self, privacy_service: PrivacyService, sample_weights: ModelWeights
    ) -> None:
        with pytest.raises(ValueError, match="max_norm must be strictly positive"):
            privacy_service.clip_model_update(sample_weights, sample_weights, max_norm=0.0)
        with pytest.raises(ValueError, match="max_norm must be strictly positive"):
            privacy_service.clip_model_update(sample_weights, sample_weights, max_norm=-1.0)

    def test_clip_model_update_nan_and_inf_robustness(
        self, privacy_service: PrivacyService
    ) -> None:
        original = ModelWeights(layer_shapes=[(3,)], flat_weights=[0.0, 0.0, 0.0])
        # Update with NaN and Inf coordinates
        updated = ModelWeights(layer_shapes=[(3,)], flat_weights=[float("nan"), float("inf"), -float("inf")])

        clipped = privacy_service.clip_model_update(original, updated, max_norm=2.0)
        clipped_arr = np.array(clipped.flat_weights)

        # Assert no NaNs or Infs survive
        assert not np.isnan(clipped_arr).any()
        assert not np.isinf(clipped_arr).any()
        # Assert strictly bounded by max_norm
        delta_norm = float(np.linalg.norm(clipped_arr - np.array(original.flat_weights)))
        assert delta_norm <= 2.0 + 1e-6


class TestRenyiDifferentialPrivacyAccountant:
    """Verifies analytical Rényi Differential Privacy (RDP) computation and composition."""

    def test_compute_rdp_gaussian(self, privacy_service: PrivacyService) -> None:
        # For sigma=1.0, q=1.0, alpha=2.0: RDP = 2.0 / (2 * 1.0) = 1.0
        rdp = privacy_service.compute_rdp_gaussian(sigma=1.0, q=1.0, alpha=2.0)
        assert abs(rdp - 1.0) < 1e-6

        # Zero sigma returns infinity
        assert privacy_service.compute_rdp_gaussian(sigma=0.0, q=1.0, alpha=2.0) == float("inf")
        # Zero q returns 0.0
        assert privacy_service.compute_rdp_gaussian(sigma=1.0, q=0.0, alpha=2.0) == 0.0

    def test_convert_rdp_to_approx_dp(self, privacy_service: PrivacyService) -> None:
        rdp_map = {2.0: 1.0, 4.0: 2.0, 8.0: 4.0}
        eps, alpha = privacy_service.convert_rdp_to_approx_dp(rdp_map, delta=1e-5)
        assert eps > 0.0
        assert alpha in rdp_map

    def test_compose_rdp_tighter_than_naive_linear_sum(
        self, privacy_service: PrivacyService
    ) -> None:
        # 10 rounds with sigma=1.5 and delta=1e-5
        sigmas = [1.5] * 10
        target_delta = 1e-5
        rdp_eps, opt_alpha, rdp_map = privacy_service.compose_rdp(
            sigmas=sigmas, delta=target_delta, q=0.05
        )

        # Individual analytical eps per round: eps_i = (q * sqrt(2 * ln(1.25 / delta))) / sigma
        indiv_eps = (0.05 * np.sqrt(2.0 * np.log(1.25 / target_delta))) / 1.5
        naive_linear_sum = indiv_eps * 10

        assert rdp_eps > 0.0
        assert opt_alpha > 1.0
        assert len(rdp_map) > 0
        # RDP composition with subsampling is mathematically tighter than naive linear summation
        assert rdp_eps < naive_linear_sum

    def test_privacy_budget_rdp_total_epsilon(self) -> None:
        budget = PrivacyBudget(epsilon_per_round=1.0, delta=1e-5)
        # Without sigmas, rdp_total_epsilon returns None
        assert budget.rdp_total_epsilon() is None

        # Spend 3 rounds recording noise scale
        budget.spend(epsilon=1.0, limit=8.0, sigma=1.2, q=0.1)
        budget.spend(epsilon=1.0, limit=8.0, sigma=1.2, q=0.1)
        budget.spend(epsilon=1.0, limit=8.0, sigma=1.2, q=0.1)

        assert budget.total_epsilon == 3.0
        rdp_eps = budget.rdp_total_epsilon()
        assert rdp_eps is not None
        assert 0.0 < rdp_eps < 3.0


class TestPrivacyServiceThreadSafetyAndEviction:
    """Verifies thread safety and bounded FIFO eviction of PrivacyService budget storage."""

    def test_budget_storage_lru_eviction(self, privacy_service: PrivacyService) -> None:
        # Fill beyond MAX_TRACKED_BUDGETS
        for i in range(privacy_service.MAX_TRACKED_BUDGETS + 10):
            privacy_service.get_or_create_budget(f"sim_evict_{i}", epsilon=1.0)

        # Storage length should be bounded to MAX_TRACKED_BUDGETS
        assert len(privacy_service._budgets) == privacy_service.MAX_TRACKED_BUDGETS
        # Oldest items should have been evicted
        assert "sim_evict_0" not in privacy_service._budgets
        assert f"sim_evict_{privacy_service.MAX_TRACKED_BUDGETS + 9}" in privacy_service._budgets

    def test_concurrent_budget_access_thread_safe(
        self, privacy_service: PrivacyService
    ) -> None:
        import concurrent.futures

        def access_budget(idx: int) -> float:
            b = privacy_service.get_or_create_budget(f"sim_concurrent_{idx % 10}")
            b.spend(0.1, limit=50.0, sigma=1.0, q=0.05)
            return b.total_epsilon

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(access_budget, i) for i in range(40)]
            results = [f.result() for f in futures]

        assert len(results) == 40
        summary = privacy_service.get_all_budgets_summary()
        assert len(summary) > 0
