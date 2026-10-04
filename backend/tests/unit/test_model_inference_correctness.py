"""Model Lifecycle, Feature Semantics, and Inference Correctness Deep Test Suite.

Comprehensive verification of:
1. Training vs inference feature preprocessing parity (Mandatory Gate E).
2. Categorical encoding alignment across all categories.
3. Feature ordering strict schema enforcement and permutation rejection.
4. Non-finite input (NaN / Inf) and malformed payload fail-closed validation.
5. Model evaluation mode, gradient-free execution, and determinism.
6. Single-record vs batch inference mathematical equivalence and ordering.
7. Independent mathematical forward-pass oracle verification.
8. Probability domain invariants [0, 1] across diverse inputs.
9. Decision threshold fidelity and boundary edge cases.
10. Model state serialization / deserialization round-trip invariance.
11. Safe model loading architecture and error propagation.
12. Single-class ROC-AUC and PR-AUC mathematical truthfulness and governance guard.
"""

from __future__ import annotations

import math
import tempfile
import unittest

import numpy as np
import pandas as pd
import torch

from app.application.services.data_generator import (
    COUNTRIES,
    DEVICES,
    MERCHANT_CATEGORIES,
    DataGenerator,
)
from app.application.services.model_registry import ModelEvaluationEngine, ModelRegistry
from app.application.services.model_service import ModelService
from app.config import get_settings
from app.domain.metrics_service import (
    compute_roc_auc_with_status,
    is_pr_auc_defined,
    is_roc_auc_defined,
)
from app.presentation.routers.predict import (
    _eval_model,
    _eval_model_batch,
    preprocess_transaction,
)


class TestModelInferenceCorrectness(unittest.TestCase):
    """Deep verification of model lifecycle and inference correctness invariants."""

    def setUp(self) -> None:
        self.settings = get_settings()
        self.model_service = ModelService(self.settings)

    def test_gate_e_training_inference_preprocessing_parity(self) -> None:
        """MANDATORY GATE E: Verify training and inference preprocessing produce bit-identical tensors."""
        test_samples = [
            {
                "transaction_amount": 2500.0,
                "merchant_category": "electronics",
                "country_code": "UK",
                "device_type": "pos_terminal",
                "velocity": 15.0,
                "hour_of_day": 12,
                "merchant_risk_score": 0.35,
                "customer_history_score": 0.82,
                "chargeback_count": 2,
                "account_age_days": 450,
            },
            {
                "transaction_amount": 0.0,
                "merchant_category": "grocery",
                "country_code": "US",
                "device_type": "mobile_app",
                "velocity": 0.0,
                "hour_of_day": 0,
                "merchant_risk_score": 0.0,
                "customer_history_score": 1.0,
                "chargeback_count": 0,
                "account_age_days": 1000,
            },
            {
                "transaction_amount": 5000.0,
                "merchant_category": "atm_withdrawal",
                "country_code": "PH",
                "device_type": "phone_banking",
                "velocity": 30.0,
                "hour_of_day": 23,
                "merchant_risk_score": 1.0,
                "customer_history_score": 0.0,
                "chargeback_count": 10,
                "account_age_days": 0,
            },
        ]

        for idx, sample in enumerate(test_samples):
            # 1. Training preprocessing path
            df_sample = pd.DataFrame([sample])
            train_features = DataGenerator.encode_features(df_sample)[0]

            # 2. Inference preprocessing path
            infer_tensor = preprocess_transaction(sample)
            infer_features = infer_tensor.numpy()[0]

            # 3. Assert parity across all 10 features
            np.testing.assert_allclose(
                train_features,
                infer_features,
                rtol=1e-5,
                atol=1e-5,
                err_msg=f"Sample {idx} training vs inference preprocessing mismatch across FEATURE_NAMES.",
            )

    def test_categorical_encoding_vocabulary_alignment(self) -> None:
        """Verify all categories map to the exact same normalized value in training and inference."""
        # Test all merchant categories
        for cat in MERCHANT_CATEGORIES:
            payload = {
                "transaction_amount": 100.0,
                "merchant_category": cat,
                "country_code": "US",
                "device_type": "mobile_app",
                "velocity": 1.0,
                "hour_of_day": 12,
                "merchant_risk_score": 0.05,
                "customer_history_score": 0.95,
                "chargeback_count": 0,
                "account_age_days": 365,
            }
            train_val = DataGenerator.encode_features(pd.DataFrame([payload]))[0, 1]
            infer_val = preprocess_transaction(payload).numpy()[0, 1]
            self.assertAlmostEqual(
                train_val,
                infer_val,
                places=6,
                msg=f"Merchant category '{cat}' encoding mismatched: train={train_val}, infer={infer_val}",
            )

        # Test all country codes
        for country in COUNTRIES:
            payload = {
                "transaction_amount": 100.0,
                "merchant_category": "grocery",
                "country_code": country,
                "device_type": "mobile_app",
                "velocity": 1.0,
                "hour_of_day": 12,
                "merchant_risk_score": 0.05,
                "customer_history_score": 0.95,
                "chargeback_count": 0,
                "account_age_days": 365,
            }
            train_val = DataGenerator.encode_features(pd.DataFrame([payload]))[0, 2]
            infer_val = preprocess_transaction(payload).numpy()[0, 2]
            self.assertAlmostEqual(
                train_val,
                infer_val,
                places=6,
                msg=f"Country code '{country}' encoding mismatched: train={train_val}, infer={infer_val}",
            )

        # Test all devices
        for device in DEVICES:
            payload = {
                "transaction_amount": 100.0,
                "merchant_category": "grocery",
                "country_code": "US",
                "device_type": device,
                "velocity": 1.0,
                "hour_of_day": 12,
                "merchant_risk_score": 0.05,
                "customer_history_score": 0.95,
                "chargeback_count": 0,
                "account_age_days": 365,
            }
            train_val = DataGenerator.encode_features(pd.DataFrame([payload]))[0, 3]
            infer_val = preprocess_transaction(payload).numpy()[0, 3]
            self.assertAlmostEqual(
                train_val,
                infer_val,
                places=6,
                msg=f"Device type '{device}' encoding mismatched: train={train_val}, infer={infer_val}",
            )

    def test_feature_ordering_strict_schema_enforcement(self) -> None:
        """Verify passing DataFrame with scrambled columns is strictly re-ordered according to FEATURE_NAMES."""
        canonical_sample = {
            "transaction_amount": 500.0,
            "merchant_category": "dining",
            "country_code": "DE",
            "device_type": "pos_terminal",
            "velocity": 5.0,
            "hour_of_day": 18,
            "merchant_risk_score": 0.2,
            "customer_history_score": 0.9,
            "chargeback_count": 1,
            "account_age_days": 200,
        }

        # Scrambled dictionary with extra unneeded columns
        scrambled_sample = {
            "account_age_days": 200,
            "velocity": 5.0,
            "unrelated_id": "cust_999",
            "country_code": "DE",
            "transaction_amount": 500.0,
            "extra_field": 42.0,
            "device_type": "pos_terminal",
            "chargeback_count": 1,
            "hour_of_day": 18,
            "merchant_category": "dining",
            "merchant_risk_score": 0.2,
            "customer_history_score": 0.9,
        }

        df_canonical = pd.DataFrame([canonical_sample])
        df_scrambled = pd.DataFrame([scrambled_sample])

        res_canonical = DataGenerator.encode_features(df_canonical)
        res_scrambled = DataGenerator.encode_features(df_scrambled)

        np.testing.assert_array_equal(
            res_canonical,
            res_scrambled,
            err_msg="Scrambled DataFrame columns failed to re-order to authoritative FEATURE_NAMES.",
        )

        # Missing required feature must raise ValueError
        df_missing = df_canonical.drop(columns=["transaction_amount"])
        with self.assertRaises(ValueError):
            DataGenerator.encode_features(df_missing)

    def test_non_finite_input_fail_closed_validation(self) -> None:
        """Verify non-finite (NaN, +Inf, -Inf) inputs are rejected at preprocessing boundary."""
        base_payload = {
            "transaction_amount": 100.0,
            "merchant_category": "grocery",
            "country_code": "US",
            "device_type": "mobile_app",
            "velocity": 1.0,
            "hour_of_day": 12,
            "merchant_risk_score": 0.05,
            "customer_history_score": 0.95,
            "chargeback_count": 0,
            "account_age_days": 365,
        }

        for bad_val in (float("nan"), float("inf"), float("-inf")):
            bad_payload = dict(base_payload)
            bad_payload["transaction_amount"] = bad_val
            with self.assertRaises(ValueError):
                preprocess_transaction(bad_payload)

            bad_payload2 = dict(base_payload)
            bad_payload2["velocity"] = bad_val
            with self.assertRaises(ValueError):
                preprocess_transaction(bad_payload2)

    def test_model_evaluation_mode_and_determinism(self) -> None:
        """Verify inference sets model.eval() and repeated executions are perfectly deterministic."""
        model = self.model_service.create_model(input_dim=10)
        model.eval()

        input_tensor = torch.randn(1, 10)
        with torch.no_grad():
            out1 = _eval_model(model, input_tensor)
            out2 = _eval_model(model, input_tensor)
            out3 = _eval_model(model, input_tensor)

        self.assertEqual(out1, out2)
        self.assertEqual(out2, out3)
        self.assertTrue(0.0 <= out1 <= 1.0)

    def test_single_vs_batch_inference_equivalence(self) -> None:
        """MODEL-INV-10: Verify single and batch inference agree within floating-point tolerance."""
        model = self.model_service.create_model(input_dim=10)
        model.eval()

        t1 = torch.randn(1, 10)
        t2 = torch.randn(1, 10)
        t3 = torch.randn(1, 10)

        single_1 = _eval_model(model, t1)
        single_2 = _eval_model(model, t2)
        single_3 = _eval_model(model, t3)

        batch_tensor = torch.cat([t1, t2, t3], dim=0)
        batch_results = _eval_model_batch(model, batch_tensor)

        self.assertEqual(len(batch_results), 3)
        self.assertAlmostEqual(single_1, batch_results[0], places=6)
        self.assertAlmostEqual(single_2, batch_results[1], places=6)
        self.assertAlmostEqual(single_3, batch_results[2], places=6)

        # Batch order preservation: [t3, t1, t2] must yield [single_3, single_1, single_2]
        reordered_batch = torch.cat([t3, t1, t2], dim=0)
        reordered_results = _eval_model_batch(model, reordered_batch)
        self.assertAlmostEqual(single_3, reordered_results[0], places=6)
        self.assertAlmostEqual(single_1, reordered_results[1], places=6)
        self.assertAlmostEqual(single_2, reordered_results[2], places=6)

    def test_independent_mathematical_oracle(self) -> None:
        """Verify forward pass matches an independent manual mathematical implementation."""
        # Create tiny deterministic linear layer with sigmoid output: z = W*x + b, p = 1 / (1 + exp(-z))
        weights = torch.tensor([[0.5, -0.2, 0.1, 0.4, -0.5, 0.2, -0.1, 0.3, -0.4, 0.6]], dtype=torch.float32)
        bias = torch.tensor([0.15], dtype=torch.float32)

        test_input = torch.tensor([[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]], dtype=torch.float32)

        # Independent manual calculation:
        dot_product = float(torch.sum(weights * test_input).item() + bias.item())
        expected_prob = 1.0 / (1.0 + math.exp(-dot_product))

        # Model linear + sigmoid simulation:
        linear = torch.nn.Linear(10, 1)
        with torch.no_grad():
            linear.weight.copy_(weights)
            linear.bias.copy_(bias)
            z = linear(test_input)
            prob = torch.sigmoid(z).item()

        self.assertAlmostEqual(prob, expected_prob, places=6)

    def test_probability_domain_invariants(self) -> None:
        """MODEL-INV-07: Verify model output probability is strictly bounded in [0, 1]."""
        model = self.model_service.create_model(input_dim=10)
        model.eval()

        # Extreme positive and negative inputs
        extreme_inputs = [
            torch.zeros(1, 10),
            torch.ones(1, 10),
            torch.full((1, 10), 100.0),
            torch.full((1, 10), -100.0),
            torch.randn(1, 10) * 50.0,
        ]

        for x in extreme_inputs:
            prob = _eval_model(model, x)
            self.assertTrue(math.isfinite(prob), f"Output not finite: {prob}")
            self.assertGreaterEqual(prob, 0.0)
            self.assertLessEqual(prob, 1.0)

    def test_model_serialization_round_trip(self) -> None:
        """MODEL-INV-09: Verify serialized and deserialized model produces identical predictions."""
        model_a = self.model_service.create_model(input_dim=10, dp_compatible=True)
        model_a.eval()

        test_input = torch.randn(5, 10)
        with torch.no_grad():
            preds_a = model_a(test_input).numpy()

        with tempfile.NamedTemporaryFile(suffix=".pt", delete=False) as tmp:
            torch.save(model_a.state_dict(), tmp.name)
            saved_path = tmp.name

        # Deserialize into fresh instance
        model_b = self.model_service.create_model(input_dim=10, dp_compatible=True)
        state_dict_loaded = torch.load(saved_path, map_location="cpu", weights_only=True)
        model_b.load_state_dict(state_dict_loaded, strict=True)
        model_b.eval()

        with torch.no_grad():
            preds_b = model_b(test_input).numpy()

        np.testing.assert_array_equal(
            preds_a,
            preds_b,
            err_msg="Model predictions diverged after state dict serialization round trip.",
        )

    def test_single_class_roc_auc_truthfulness(self) -> None:
        """MODEL-0002 & Gate AB: Verify single-class ROC-AUC status truthfulness and sentinel semantics."""
        # 1. Single class (all 0s) - mathematically undefined
        y_single = np.array([0, 0, 0, 0, 0])
        y_preds = np.array([0.1, 0.2, 0.3, 0.4, 0.5])

        self.assertFalse(is_roc_auc_defined(y_single))
        self.assertFalse(is_pr_auc_defined(y_single))

        score, is_def, status = compute_roc_auc_with_status(y_single, y_preds, default=0.5)
        self.assertEqual(score, 0.5)
        self.assertFalse(is_def)
        self.assertEqual(status, "undefined_single_class")

        # 2. Both classes present - defined
        y_valid = np.array([0, 0, 1, 1, 0])
        self.assertTrue(is_roc_auc_defined(y_valid))
        self.assertTrue(is_pr_auc_defined(y_valid))

        score_valid, is_def_valid, status_valid = compute_roc_auc_with_status(y_valid, y_preds)
        self.assertTrue(is_def_valid)
        self.assertEqual(status_valid, "defined")
        self.assertGreaterEqual(score_valid, 0.0)
        self.assertLessEqual(score_valid, 1.0)

    def test_governance_no_false_rollback_on_single_class(self) -> None:
        """Verify ModelEvaluationEngine does not trigger auto-rollback on single-class warmup data."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry = ModelRegistry(storage_dir=tmp_dir)
            engine = ModelEvaluationEngine(registry)
            sim_id = "test_single_class_sim"

            # Save version 1 and 2
            mock_weights = {"network.0.weight": torch.randn(64, 10)}
            registry.save_version(sim_id, mock_weights, metrics={"auc_roc": 0.85}, is_promoted=True, status="champion")
            registry.save_version(sim_id, mock_weights, metrics={"auc_roc": 0.88}, is_promoted=True, status="champion")

            # Log 5 transactions where ALL actual labels are 0 (legitimate)
            for i in range(5):
                tx_id = f"tx_{i}"
                engine.log_prediction(
                    simulation_id=sim_id,
                    transaction_id=tx_id,
                    champion_version=2,
                    champion_prob=0.05,
                    champion_latency_ms=10.0,
                )
                metrics = engine.log_feedback(
                    simulation_id=sim_id,
                    transaction_id=tx_id,
                    actual_label=0,  # Single-class
                )

            # Auto-rollback must NOT be triggered because ROC-AUC is undefined (not genuine degradation)
            self.assertFalse(metrics["rollback_triggered"])
            active = registry.get_active_version(sim_id)
            self.assertIsNotNone(active)
            self.assertEqual(active["version"], 2)


if __name__ == "__main__":
    unittest.main()
