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
        self.assertTrue(telemetry.audit_chain_valid)
        self.assertGreater(len(telemetry.audit_events), 0)

    def test_input_validation_init_guards(self) -> None:
        """Assert invalid initialization parameters raise ValueError."""
        with self.assertRaises(ValueError):
            AdaptiveDPAutoScaler(target_epsilon=-1.0)
        with self.assertRaises(ValueError):
            AdaptiveDPAutoScaler(target_delta=0.0)
        with self.assertRaises(ValueError):
            AdaptiveDPAutoScaler(target_delta=1.5)
        with self.assertRaises(ValueError):
            AdaptiveDPAutoScaler(nominal_sigma=-0.5)
        with self.assertRaises(ValueError):
            AdaptiveDPAutoScaler(nominal_clip=0.0)

    def test_input_validation_autoscale_guards(self) -> None:
        """Assert invalid autoscale runtime parameters raise ValueError."""
        with self.assertRaises(ValueError):
            self.autoscaler.auto_scale_noise_multiplier(round_id=-1, current_loss=0.5)
        with self.assertRaises(ValueError):
            self.autoscaler.auto_scale_noise_multiplier(round_id=1, current_loss=-0.1)
        with self.assertRaises(ValueError):
            self.autoscaler.auto_scale_noise_multiplier(round_id=1, current_loss=0.5, prev_loss=-0.1)
        with self.assertRaises(ValueError):
            self.autoscaler.auto_scale_noise_multiplier(round_id=1, current_loss=0.5, batch_size=0)
        with self.assertRaises(ValueError):
            self.autoscaler.auto_scale_noise_multiplier(round_id=1, current_loss=0.5, total_samples=0)
        with self.assertRaises(ValueError):
            self.autoscaler.auto_scale_noise_multiplier(round_id=1, current_loss=0.5, total_rounds=0)

    def test_hard_budget_abort_enforcement(self) -> None:
        """Assert hard budget abort raises PrivacyBudgetExceededError when limit exceeded."""
        from app.application.services.privacy_service import PrivacyBudgetExceededError

        # Create autoscaler with low target epsilon
        strict_scaler = AdaptiveDPAutoScaler(target_epsilon=0.1, fail_on_exhaustion=True)

        with self.assertRaises(PrivacyBudgetExceededError):
            # First round already consumes more than 0.1 epsilon
            strict_scaler.auto_scale_noise_multiplier(
                round_id=1,
                current_loss=0.5,
                batch_size=512,
                total_samples=1000,
            )

    def test_multi_node_bank_isolation(self) -> None:
        """Assert multiple bank nodes track distinct cumulative budgets and isolation."""
        # Node Bank_A performs 3 rounds
        for r in range(1, 4):
            self.autoscaler.auto_scale_noise_multiplier(
                round_id=r,
                current_loss=0.4,
                node_id="bank_a",
            )

        # Node Bank_B performs 1 round
        self.autoscaler.auto_scale_noise_multiplier(
            round_id=1,
            current_loss=0.4,
            node_id="bank_b",
        )

        state_a = self.autoscaler.get_accountant_state(node_id="bank_a")
        state_b = self.autoscaler.get_accountant_state(node_id="bank_b")

        self.assertEqual(state_a.total_rounds, 3)
        self.assertEqual(state_b.total_rounds, 1)
        self.assertGreater(state_a.current_epsilon_at_delta, state_b.current_epsilon_at_delta)

        # Verify cluster summary
        summaries = self.autoscaler.get_all_nodes_summary()
        self.assertEqual(len(summaries), 2)
        node_ids = [s["node_id"] for s in summaries]
        self.assertIn("bank_a", node_ids)
        self.assertIn("bank_b", node_ids)

    def test_cryptographic_audit_hash_chain_integrity(self) -> None:
        """Assert SHA-256 audit hash chain correctly detects tampering."""
        self.autoscaler.auto_scale_noise_multiplier(round_id=1, current_loss=0.4)
        self.autoscaler.auto_scale_noise_multiplier(round_id=2, current_loss=0.35)

        self.assertTrue(self.autoscaler.verify_audit_chain())

        # Tamper with an event in the chain
        original_event = self.autoscaler._audit_events[1]  # noqa: SLF001
        from app.domain.value_objects_rdp import PrivacyAuditEvent

        tampered_event = PrivacyAuditEvent(
            step=original_event.step,
            timestamp_utc=original_event.timestamp_utc,
            round_id=original_event.round_id,
            node_id=original_event.node_id,
            calibrated_sigma=999.999,  # tampered
            gradient_clip_c=original_event.gradient_clip_c,
            cumulative_epsilon=original_event.cumulative_epsilon,
            target_epsilon=original_event.target_epsilon,
            risk_tier=original_event.risk_tier,
            previous_hash=original_event.previous_hash,
            block_hash=original_event.block_hash,
        )
        self.autoscaler._audit_events[1] = tampered_event  # noqa: SLF001

        # Chain verification must now fail
        self.assertFalse(self.autoscaler.verify_audit_chain())

    def test_thread_safe_concurrent_autoscaling(self) -> None:
        """Assert concurrent calibration calls across threads do not corrupt accountant state."""
        import concurrent.futures

        def worker(thread_idx: int) -> None:
            self.autoscaler.auto_scale_noise_multiplier(
                round_id=thread_idx,
                current_loss=0.3 + (thread_idx * 0.01),
                node_id=f"thread_bank_{thread_idx % 3}",
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker, i) for i in range(1, 25)]
            concurrent.futures.wait(futures)

        self.assertTrue(self.autoscaler.verify_audit_chain())
        global_state = self.autoscaler.get_accountant_state("global")
        self.assertEqual(global_state.total_rounds, 24)

    def test_reset_functionality(self) -> None:
        """Assert reset cleanly restores initial accountant state and valid genesis hash."""
        self.autoscaler.auto_scale_noise_multiplier(round_id=1, current_loss=0.4)
        self.assertGreater(len(self.autoscaler.calibration_history), 0)

        self.autoscaler.reset()
        self.assertEqual(len(self.autoscaler.calibration_history), 0)
        self.assertTrue(self.autoscaler.verify_audit_chain())
        state = self.autoscaler.get_accountant_state()
        self.assertEqual(state.total_rounds, 0)
        self.assertAlmostEqual(state.current_epsilon_at_delta, 0.0, places=3)


class TestAdaptiveDPAutoScalerAPI(unittest.TestCase):
    """Test suite verifying FastAPI security router RDP endpoints."""

    def setUp(self) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        self.client = TestClient(app)

    def test_rdp_calibrate_endpoint_success(self) -> None:
        resp = self.client.post(
            "/api/v1/security/rdp/calibrate",
            json={
                "round_id": 1,
                "current_loss": 0.45,
                "prev_loss": 0.60,
                "batch_size": 256,
                "total_samples": 10000,
                "target_epsilon": 4.0,
                "total_rounds": 50,
                "node_id": "bank_api_test",
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["round_id"], 1)
        self.assertEqual(data["node_id"], "bank_api_test")
        self.assertGreater(data["calibrated_sigma"], 0.0)
        self.assertIn("cumulative_epsilon", data)
        self.assertIn("budget_exhaustion_pct", data)

    def test_rdp_calibrate_endpoint_budget_exhaustion_abort(self) -> None:
        resp = self.client.post(
            "/api/v1/security/rdp/calibrate",
            json={
                "round_id": 1,
                "current_loss": 0.45,
                "batch_size": 512,
                "total_samples": 1000,
                "target_epsilon": 0.01,
                "node_id": "bank_exhaustion_test",
                "enforce_budget_limit": True,
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Cumulative privacy budget exceeded", resp.json()["detail"])

    def test_rdp_status_endpoint_telemetry(self) -> None:
        resp = self.client.get("/api/v1/security/rdp/status?node_id=bank_api_test")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["node_id"], "bank_api_test")
        self.assertTrue(data["audit_chain_valid"])
        self.assertIn("active_sigma", data)
        self.assertIn("risk_tier", data)
        self.assertIn("audit_events", data)


if __name__ == "__main__":
    unittest.main()
