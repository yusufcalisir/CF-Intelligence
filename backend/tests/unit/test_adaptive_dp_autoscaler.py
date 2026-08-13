"""Unit tests for Adaptive Dynamic Differential Privacy Budget Auto-Scaler Driver."""

from __future__ import annotations

import unittest

from app.domain.value_objects_rdp import DEFAULT_RDP_ORDERS
from app.infrastructure.security.adaptive_dp_autoscaler import AdaptiveDPAutoScaler


class TestAdaptiveDPAutoScaler(unittest.TestCase):
    """Test suite verifying Rényi Differential Privacy (RDP) and dynamic noise auto-scaling."""

    def setUp(self) -> None:
        self.autoscaler = AdaptiveDPAutoScaler(target_epsilon=4.0, target_delta=1e-5)

    def test_rdp_analytical_gaussian_computation(self) -> None:
        """Assert Rényi divergence strictly grows monotonically with order alpha."""
        rdp_alpha2 = self.autoscaler.compute_rdp_gaussian(sigma=1.0, q=0.01, alpha=2.0)
        rdp_alpha4 = self.autoscaler.compute_rdp_gaussian(sigma=1.0, q=0.01, alpha=4.0)
        rdp_alpha16 = self.autoscaler.compute_rdp_gaussian(sigma=1.0, q=0.01, alpha=16.0)

        self.assertGreater(rdp_alpha4, rdp_alpha2)
        self.assertGreater(rdp_alpha16, rdp_alpha4)

    def test_convert_rdp_to_approx_dp_convex_dual(self) -> None:
        """Assert convex dual conversion finds minimum (epsilon, delta) upper bound."""
        rdp_map = {
            alpha: self.autoscaler.compute_rdp_gaussian(sigma=1.2, q=0.02, alpha=alpha) * 50
            for alpha in DEFAULT_RDP_ORDERS
        }
        best_eps, best_alpha = self.autoscaler.convert_rdp_to_approx_dp(rdp_map, delta=1e-5)

        self.assertGreater(best_eps, 0.0)
        self.assertIn(best_alpha, DEFAULT_RDP_ORDERS)
        self.assertLess(best_eps, 10.0)

    def test_dynamic_noise_auto_scaling_response(self) -> None:
        """Assert noise multiplier sigma_t scales dynamically in response to loss velocity."""
        # High loss velocity (early exploration) -> higher noise
        cal_fast = self.autoscaler.auto_scale_noise_multiplier(
            round_id=1,
            current_loss=0.30,
            prev_loss=0.80,  # High delta
            batch_size=256,
            total_samples=10_000,
            total_rounds=50,
        )

        # Low loss velocity (stabilized convergence) -> lower noise for high accuracy
        cal_slow = self.autoscaler.auto_scale_noise_multiplier(
            round_id=45,
            current_loss=0.15,
            prev_loss=0.151,  # Tiny delta
            batch_size=256,
            total_samples=10_000,
            total_rounds=50,
        )

        self.assertGreater(cal_fast.calibrated_sigma, cal_slow.calibrated_sigma)
        self.assertGreater(cal_fast.loss_velocity, cal_slow.loss_velocity)

    def test_accountant_and_telemetry_status(self) -> None:
        """Assert auto-scaler state and telemetry report accurate budget exhaustion percentages."""
        # Run 5 simulated rounds
        for r in range(1, 6):
            self.autoscaler.auto_scale_noise_multiplier(
                round_id=r,
                current_loss=0.5 - (r * 0.05),
                prev_loss=0.5 - ((r - 1) * 0.05),
            )

        telemetry = self.autoscaler.get_telemetry()

        self.assertGreater(telemetry.cumulative_epsilon, 0.0)
        self.assertEqual(telemetry.target_epsilon, 4.0)
        self.assertGreater(telemetry.remaining_budget_pct, 0.0)
        self.assertIn(telemetry.risk_tier, ["OPTIMAL", "CALIBRATING"])
        self.assertEqual(len(telemetry.history), 5)


if __name__ == "__main__":
    unittest.main()
