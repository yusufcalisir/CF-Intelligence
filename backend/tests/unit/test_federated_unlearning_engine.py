"""Unit tests for Confidential Federated Unlearning & Anti-Poisoning Erasure Engine."""

from __future__ import annotations

import unittest

import numpy as np

from app.application.services.federated_unlearning_engine import FederatedUnlearningEngine
from app.domain.security_evaluator import MIAEvaluator
from app.domain.value_objects_unlearning import (
    FederatedUnlearningResult,
    UnlearningMethod,
)


class TestFederatedUnlearningEngine(unittest.TestCase):
    """Test suite verifying exact & re-aggregation federated model weight unlearning mechanics."""

    def setUp(self) -> None:
        self.engine = FederatedUnlearningEngine()
        np.random.seed(42)
        self.sample_weights = np.random.randn(512).astype(np.float32) * 0.1

    def test_exact_reaggregation_with_client_contributions(self) -> None:
        """Assert Exact Re-Aggregation mathematically recomputes global weights excluding target bank."""
        w_alpha = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)
        w_beta = np.array([3.0, 4.0, 5.0, 6.0], dtype=np.float32)
        w_gamma = np.array([10.0, 20.0, 30.0, 40.0], dtype=np.float32)

        contributions = {
            "bank_alpha": w_alpha,
            "bank_beta": w_beta,
            "bank_gamma": w_gamma,
        }

        res = self.engine.unlearn_bank_contributions(
            target_bank_id="bank_gamma",
            method=UnlearningMethod.EXACT_REAGGREGATION,
            client_contributions=contributions,
        )

        self.assertIsInstance(res, FederatedUnlearningResult)
        self.assertEqual(res.target_bank_id, "bank_gamma")
        self.assertEqual(res.unlearning_method, "EXACT_REAGGREGATION")
        self.assertEqual(res.retained_banks, ["bank_alpha", "bank_beta"])

        # Retained participants mean: (w_alpha + w_beta) / 2 = [2.0, 3.0, 4.0, 5.0]
        expected_unlearned = (w_alpha + w_beta) / 2.0
        self.assertIsNotNone(res.unlearned_weights)
        np.testing.assert_allclose(res.unlearned_weights, expected_unlearned, rtol=1e-5)

        self.assertGreater(res.parameter_drift_delta, 0.0)
        self.assertTrue(res.erasure_verified)
        self.assertIsNone(res.mia_membership_probability)
        self.assertIn("structural exclusion guaranteed", res.audit_log[-1]["name"])
        self.assertIsNotNone(res.lineage_hash)
        self.assertEqual(len(res.audit_log), 4)
        # Ensure zero fake Hessian claims in audit log
        for step in res.audit_log:
            self.assertNotIn("Hessian", step["name"])
            self.assertNotIn("Conjugate Gradient", step["name"])

    def test_exact_reaggregation_with_round_history(self) -> None:
        """Assert Multi-round Re-Aggregation extracts per-round contributions excluding target bank."""
        round_hist = [
            {
                "round": 1,
                "contributions": {
                    "bank_alpha": np.array([1.0, 1.0], dtype=np.float32),
                    "bank_gamma": np.array([5.0, 5.0], dtype=np.float32),
                },
            },
            {
                "round": 2,
                "contributions": {
                    "bank_alpha": np.array([2.0, 2.0], dtype=np.float32),
                    "bank_beta": np.array([4.0, 4.0], dtype=np.float32),
                    "bank_gamma": np.array([9.0, 9.0], dtype=np.float32),
                },
            },
        ]

        res = self.engine.unlearn_bank_contributions(
            target_bank_id="bank_gamma",
            round_history=round_hist,
        )

        self.assertEqual(res.unlearning_method, "EXACT_REAGGREGATION")
        self.assertIn("bank_alpha", res.retained_banks)
        self.assertNotIn("bank_gamma", res.retained_banks)
        self.assertTrue(res.erasure_verified)
        self.assertIsNone(res.mia_membership_probability)

    def test_exact_lineage_subtraction_unlearning(self) -> None:
        """Assert Direct Gradient Lineage Subtraction computes valid parameter drift."""
        target_grad = np.array([0.05, -0.02, 0.03, 0.01], dtype=np.float32)
        initial_w = np.array([0.10, 0.20, 0.30, 0.40], dtype=np.float32)

        res = self.engine.unlearn_bank_contributions(
            target_bank_id="bank_beta",
            method=UnlearningMethod.EXACT_LINEAGE_SUBTRACTION,
            flat_weights=initial_w,
            target_bank_weights=target_grad,
        )

        self.assertEqual(res.target_bank_id, "bank_beta")
        self.assertEqual(res.unlearning_method, "EXACT_LINEAGE_SUBTRACTION")
        self.assertGreater(res.parameter_drift_delta, 0.0)
        self.assertTrue(res.erasure_verified)
        self.assertIsNone(res.mia_membership_probability)
        self.assertIsNotNone(res.unlearned_weights)
        assert res.unlearned_weights is not None
        np.testing.assert_allclose(res.unlearned_weights, initial_w - target_grad, rtol=1e-5)

    def test_standalone_illustrative_simulator_honest_audit(self) -> None:
        """Assert standalone fallback without stored client weights returns honest simulator status."""
        res = self.engine.unlearn_bank_contributions(
            target_bank_id="bank_alpha",
            flat_weights=self.sample_weights,
        )

        self.assertIn("Placeholder Unlearning Simulator", res.unlearning_method)
        self.assertGreater(res.parameter_drift_delta, 0.0)
        self.assertIsNone(res.mia_membership_probability)
        self.assertTrue(res.erasure_verified)
        # Ensure zero fake Hessian claims in audit log
        for step in res.audit_log:
            self.assertNotIn("Conjugate Gradient Hessian Inversion", step["name"])

    def test_unlearning_with_genuine_mia_evaluator_wiring(self) -> None:
        """Assert providing evaluation samples genuinely computes MIA risk via MIAEvaluator."""
        rng = np.random.default_rng(42)
        n_samples = 200
        y_true = rng.integers(0, 2, size=n_samples)
        member_mask = np.array([1] * (n_samples // 2) + [0] * (n_samples // 2), dtype=bool)
        y_pred_prob = rng.uniform(0.1, 0.9, size=n_samples)

        contributions = {
            "bank_alpha": np.array([1.0, 2.0], dtype=np.float32),
            "bank_gamma": np.array([5.0, 6.0], dtype=np.float32),
        }

        eval_samples = (y_true, y_pred_prob, member_mask)
        res = self.engine.unlearn_bank_contributions(
            target_bank_id="bank_gamma",
            client_contributions=contributions,
            eval_samples=eval_samples,
        )

        expected_mia = float(
            MIAEvaluator(seed=42)
            .evaluate_membership_inference(y_true, y_pred_prob, member_mask)
            .unprotected_attack_acc
        )
        self.assertIsNotNone(res.mia_membership_probability)
        self.assertAlmostEqual(res.mia_membership_probability, expected_mia, places=4)
        self.assertTrue(res.erasure_verified)
        self.assertIn("MIAEvaluator", res.audit_log[-1]["name"])

    def test_mia_audit_probability_with_real_evaluator(self) -> None:
        """Assert Membership Inference Attack auditor calls genuine MIAEvaluator."""
        rng = np.random.default_rng(42)
        n_samples = 300
        y_true = rng.integers(0, 2, size=n_samples)
        member_mask = np.array([1] * (n_samples // 2) + [0] * (n_samples // 2), dtype=bool)
        y_pred_prob = rng.uniform(0.1, 0.9, size=n_samples)

        mia_p = self.engine.compute_mia_membership_probability(
            y_true=y_true,
            y_pred_prob=y_pred_prob,
            member_mask=member_mask,
        )
        expected_mia = float(
            MIAEvaluator(seed=42)
            .evaluate_membership_inference(y_true, y_pred_prob, member_mask)
            .unprotected_attack_acc
        )
        self.assertEqual(mia_p, expected_mia)
        self.assertGreaterEqual(mia_p, 0.0)
        self.assertLessEqual(mia_p, 1.0)

    def test_projected_gradient_ascent_unlearning_with_target_gradients(self) -> None:
        """Assert Projected Gradient Ascent (PGA) unlearning computes bounded parameter drift."""
        initial_w = np.array([1.0, -2.0, 3.0, -4.0], dtype=np.float32)
        target_grad = np.array([0.1, 0.2, -0.1, 0.3], dtype=np.float32)

        res = self.engine.projected_gradient_ascent_unlearning(
            target_bank_id="bank_gamma",
            flat_weights=initial_w,
            target_gradients=target_grad,
            ascent_lr=0.05,
            ascent_steps=2,
            projection_radius=0.20,
        )

        self.assertEqual(res.target_bank_id, "bank_gamma")
        self.assertEqual(res.unlearning_method, "PROJECTED_GRADIENT_ASCENT")
        self.assertGreater(res.parameter_drift_delta, 0.0)
        self.assertTrue(res.erasure_verified)
        self.assertIsNotNone(res.unlearned_weights)
        self.assertGreater(res.hessian_spectral_radius, 0.0)

        # Ensure drift does not breach projection radius bound
        max_allowed_drift = 0.20 * float(np.linalg.norm(initial_w))
        self.assertLessEqual(res.parameter_drift_delta, max_allowed_drift + 1e-5)

    def test_projected_gradient_ascent_projection_clamping(self) -> None:
        """Assert PGA clamps excessive gradient ascent steps to the exact L2 boundary."""
        initial_w = np.array([1.0, 1.0, 1.0, 1.0], dtype=np.float32)
        # Very large ascent gradient that will exceed projection radius
        huge_grad = np.array([100.0, 100.0, 100.0, 100.0], dtype=np.float32)
        radius = 0.10

        res = self.engine.unlearn_bank_contributions(
            target_bank_id="bank_rogue",
            method=UnlearningMethod.PROJECTED_GRADIENT_ASCENT,
            flat_weights=initial_w,
            target_bank_weights=huge_grad,
            ascent_lr=1.0,
            ascent_steps=1,
            projection_radius=radius,
        )

        expected_max_drift = radius * float(np.linalg.norm(initial_w))
        self.assertAlmostEqual(res.parameter_drift_delta, expected_max_drift, places=4)
        self.assertEqual(res.unlearning_method, "PROJECTED_GRADIENT_ASCENT")
        self.assertTrue(res.erasure_verified)

    def test_compute_spectral_radius_dynamic_calculation(self) -> None:
        """Assert spectral radius is dynamically computed from drift and reference norms."""
        w0 = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        w1 = np.array([1.2, 0.0, 0.0, 0.0], dtype=np.float32)

        rho = self.engine.compute_spectral_radius(w0, w1)
        # drift = 0.2, init_norm = 1.0 => rho = 0.2
        self.assertAlmostEqual(rho, 0.2, places=4)

        # Zero init norm fallback
        w_zero = np.zeros(4, dtype=np.float32)
        rho_zero = self.engine.compute_spectral_radius(w_zero, w1)
        self.assertAlmostEqual(rho_zero, float(np.linalg.norm(w1)), places=4)

    def test_unlearning_validations_and_edge_cases(self) -> None:
        """Assert unlearning engine strictly validates input preconditions and raises descriptive ValueErrors."""
        # Empty target bank ID
        with self.assertRaises(ValueError):
            self.engine.unlearn_bank_contributions(target_bank_id="")

        # Missing target bank in client contributions
        contributions = {
            "bank_alpha": np.array([1.0, 2.0], dtype=np.float32),
            "bank_beta": np.array([3.0, 4.0], dtype=np.float32),
        }
        with self.assertRaises(ValueError):
            self.engine.unlearn_bank_contributions(
                target_bank_id="bank_missing",
                method=UnlearningMethod.EXACT_REAGGREGATION,
                client_contributions=contributions,
            )

        # Shape mismatch in exact lineage subtraction
        with self.assertRaises(ValueError):
            self.engine.unlearn_bank_contributions(
                target_bank_id="bank_alpha",
                method=UnlearningMethod.EXACT_LINEAGE_SUBTRACTION,
                flat_weights=np.array([1.0, 2.0], dtype=np.float32),
                target_bank_weights=np.array([1.0, 2.0, 3.0], dtype=np.float32),
            )

        # Invalid PGA parameters
        with self.assertRaises(ValueError):
            self.engine.projected_gradient_ascent_unlearning(
                target_bank_id="bank_alpha",
                ascent_lr=-0.01,
            )
        with self.assertRaises(ValueError):
            self.engine.projected_gradient_ascent_unlearning(
                target_bank_id="bank_alpha",
                ascent_steps=0,
            )
        with self.assertRaises(ValueError):
            self.engine.projected_gradient_ascent_unlearning(
                target_bank_id="bank_alpha",
                projection_radius=-0.1,
            )

    def test_rest_unlearning_security_router(self) -> None:
        """Assert unlearning REST endpoints handle PGA requests and status queries."""
        import asyncio

        from app.presentation.routers.security import (
            UnlearnBankRequest,
            get_unlearning_status,
        )
        from app.presentation.routers.security import (
            unlearn_bank_contributions as router_unlearn,
        )

        async def run_async_tests():
            # 1. Status query
            status = await get_unlearning_status()
            self.assertEqual(status.engine_status, "ACTIVE")
            self.assertIn("PROJECTED_GRADIENT_ASCENT", status.supported_methods)

            # 2. Trigger PGA unlearning via router
            req = UnlearnBankRequest(
                target_bank_id="bank_test",
                unlearning_method="PROJECTED_GRADIENT_ASCENT",
                ascent_lr=0.02,
                ascent_steps=2,
                projection_radius=0.10,
            )
            res = await router_unlearn(req)
            self.assertEqual(res.target_bank_id, "bank_test")
            self.assertEqual(res.unlearning_method, "PROJECTED_GRADIENT_ASCENT")
            self.assertTrue(res.erasure_verified)
            self.assertGreater(res.parameter_drift_delta, 0.0)

        asyncio.run(run_async_tests())


if __name__ == "__main__":
    unittest.main()
