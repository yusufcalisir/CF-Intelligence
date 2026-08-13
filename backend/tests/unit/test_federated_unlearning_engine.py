"""Unit tests for Confidential Federated Unlearning & Anti-Poisoning Erasure Engine."""

from __future__ import annotations

import unittest

import numpy as np

from app.application.services.federated_unlearning_engine import FederatedUnlearningEngine
from app.domain.value_objects_unlearning import (
    FederatedUnlearningResult,
    UnlearningMethod,
)


class TestFederatedUnlearningEngine(unittest.TestCase):
    """Test suite verifying exact & approximate federated model weight unlearning mechanics."""

    def setUp(self) -> None:
        self.engine = FederatedUnlearningEngine()
        np.random.seed(42)
        self.sample_weights = np.random.randn(512).astype(np.float32) * 0.1

    def test_first_order_hessian_inversion_unlearning(self) -> None:
        """Assert First-Order Hessian Inversion alters weights, reduces MIA risk, and generates audit hash."""
        res = self.engine.unlearn_bank_contributions(
            target_bank_id="bank_gamma",
            method=UnlearningMethod.FIRST_ORDER_HESSIAN_INVERSION,
            flat_weights=self.sample_weights,
        )

        self.assertIsInstance(res, FederatedUnlearningResult)
        self.assertEqual(res.target_bank_id, "bank_gamma")
        self.assertEqual(res.unlearning_method, "FIRST_ORDER_HESSIAN_INVERSION")
        self.assertGreater(res.parameter_drift_delta, 0.0)
        self.assertLessEqual(res.mia_membership_probability, 0.52)
        self.assertTrue(res.erasure_verified)
        self.assertIsNotNone(res.lineage_hash)
        self.assertEqual(len(res.audit_log), 4)

    def test_exact_lineage_subtraction_unlearning(self) -> None:
        """Assert Direct Gradient Lineage Subtraction computes valid parameter drift."""
        res = self.engine.unlearn_bank_contributions(
            target_bank_id="bank_beta",
            method=UnlearningMethod.EXACT_LINEAGE_SUBTRACTION,
            flat_weights=self.sample_weights,
        )

        self.assertEqual(res.target_bank_id, "bank_beta")
        self.assertEqual(res.unlearning_method, "EXACT_LINEAGE_SUBTRACTION")
        self.assertGreater(res.parameter_drift_delta, 0.0)
        self.assertTrue(res.erasure_verified)

    def test_sub_sampled_newton_steps_unlearning(self) -> None:
        """Assert Sub-sampled Newton Steps Hessian contraction yields bounded spectral radius."""
        res = self.engine.unlearn_bank_contributions(
            target_bank_id="bank_alpha",
            method=UnlearningMethod.SUB_SAMPLED_NEWTON_STEPS,
            flat_weights=self.sample_weights,
        )

        self.assertEqual(res.unlearning_method, "SUB_SAMPLED_NEWTON_STEPS")
        self.assertGreater(res.hessian_spectral_radius, 0.5)
        self.assertLessEqual(res.mia_membership_probability, 0.52)

    def test_mia_audit_probability(self) -> None:
        """Assert Membership Inference Attack auditor returns random guessing equivalence (<= 0.52)."""
        mia_p = self.engine.compute_mia_membership_probability(self.sample_weights, "bank_gamma")
        self.assertLessEqual(mia_p, 0.52)
        self.assertGreaterEqual(mia_p, 0.45)


if __name__ == "__main__":
    unittest.main()
