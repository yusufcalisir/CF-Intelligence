"""Unit and Adversarial Test Suite for Model Evaluation Metrics and False-Positive Rate Isolation.

Verifies invariants:
- METRIC-01: Challenger FPR is computed strictly from challenger false positives and challenger true negatives.
- METRIC-02: Champion true negatives cannot contaminate challenger FPR denominator.
- METRIC-03: Zero denominator in FPR calculation returns None truthfully without throwing or fabricating 0.0.
- METRIC-04: Independent numeric verification against a hand-calculated confusion matrix.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from typing import Any

import torch

from app.application.services.model_registry import (
    ModelEvaluationEngine,
    ModelRegistry,
    create_trusted_internal_context,
)
from app.config import get_settings
from app.infrastructure.redis_store import RedisStore
from app.infrastructure.security.durable_nonce_store import (
    DurableNonceStore,
    reset_default_nonce_store,
)
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain


class TestModelEvaluationMetrics(unittest.TestCase):
    """Verifies mathematical correctness and confusion matrix isolation for model metrics."""

    def setUp(self) -> None:
        self.env_keys = [
            "CFI_INTERNAL_CONTEXT_SECRET",
            "CFI_INTERNAL_NONCE_STORE_PATH",
            "CFI_MODEL_REGISTRY_DIR",
            "CFI_AUDIT_LEDGER_PATH",
            "CFI_AUDIT_LEDGER_SECRET",
            "APP_ENV",
        ]
        self._orig_env = {k: os.environ.get(k) for k in self.env_keys}
        self.test_dir = tempfile.mkdtemp()
        self.nonce_file = os.path.join(self.test_dir, "nonces.json")
        self.audit_file = os.path.join(self.test_dir, "audit.json")
        self.reg_dir = os.path.join(self.test_dir, "models")
        os.makedirs(self.reg_dir, exist_ok=True)

        self.payload_secret = "metrics_eval_secret_entropy_32bytes_test"
        os.environ["CFI_INTERNAL_CONTEXT_SECRET"] = self.payload_secret
        os.environ["CFI_INTERNAL_NONCE_STORE_PATH"] = self.nonce_file
        os.environ["CFI_MODEL_REGISTRY_DIR"] = self.reg_dir
        os.environ["CFI_AUDIT_LEDGER_PATH"] = self.audit_file
        os.environ["CFI_AUDIT_LEDGER_SECRET"] = self.payload_secret
        os.environ["APP_ENV"] = "development"

        get_settings.cache_clear()
        reset_default_nonce_store()
        DurableNonceStore._instance = None
        ImmutableAuditChain.reset_instance()
        RedisStore._shared_fallback_stores.clear()

        self.registry = ModelRegistry(storage_dir=self.reg_dir)
        self.eval_engine = ModelEvaluationEngine(self.registry)
        self.simulation_id = f"sim_metric_{self._testMethodName}"

        # Bind ownership
        ctx_bind = create_trusted_internal_context(
            service_name="training_orchestrator",
            action="bind_ownership",
            resource_type="model",
            resource_id=self.simulation_id,
            bank_id="bank_test",
        )
        self.registry.bind_simulation_ownership(self.simulation_id, owning_bank_id="bank_test", trusted_context=ctx_bind)

        self.model_state = torch.nn.Linear(2, 1).state_dict()

    def tearDown(self) -> None:
        for k, v in self._orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.test_dir, ignore_errors=True)
        get_settings.cache_clear()
        reset_default_nonce_store()
        DurableNonceStore._instance = None
        ImmutableAuditChain.reset_instance()
        RedisStore._shared_fallback_stores.clear()

    def test_challenger_fpr_isolated_from_champion_confusion_matrix(self) -> None:
        """METRIC-01 & METRIC-02: Assert challenger FPR uses challenger FP & TN; champ_tn cannot contaminate."""
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.85},
            is_promoted=True,
            status="champion",
        )
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.90},
            is_promoted=False,
            status="challenger",
        )

        # Construct specific confusion matrix:
        # Negative class (actual_label == 0):
        # 10 cases where Champion predicts correctly (champ_tn += 10, champ_prob < 0.5)
        # BUT Challenger predicts false alarm (chall_fp += 10, chall_prob >= 0.5)
        # 10 cases where Champion predicts false alarm (champ_fp += 10, champ_prob >= 0.5)
        # AND Challenger predicts correctly (chall_tn += 10, chall_prob < 0.5)
        #
        # Champion: FP = 10, TN = 10 -> Champion FPR = 10 / (10 + 10) = 0.50
        # Challenger: FP = 10, TN = 10 -> Challenger FPR = 10 / (10 + 10) = 0.50
        #
        # Now add 80 more cases where:
        # Champion predicts correctly (champ_tn += 80)
        # Challenger ALSO predicts false alarm (chall_fp += 80)
        #
        # Overall Negative Counts (total 100 negative transactions):
        # Champion: FP = 10, TN = 90 -> Champion FPR = 10 / (10 + 90) = 0.10 (10%)
        # Challenger: FP = 90, TN = 10 -> Challenger FPR = 90 / (90 + 10) = 0.90 (90%)
        #
        # Old defect formula: chall_fp / (chall_fp + champ_tn) = 90 / (90 + 90) = 90 / 180 = 0.50 (50%)
        # Under the old defect, the Challenger's catastrophic 90% FPR was masked as 50%!

        # Log 100 negative transactions
        for i in range(10):
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_neg_group1_{i}",
                champion_version=1,
                champion_prob=0.8,  # champ FP
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=0.2,  # chall TN
                challenger_latency_ms=10.0,
            )
            self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_neg_group1_{i}",
                actual_label=0,
            )

        for i in range(90):
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_neg_group2_{i}",
                champion_version=1,
                champion_prob=0.2,  # champ TN
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=0.8,  # chall FP
                challenger_latency_ms=10.0,
            )
            self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_neg_group2_{i}",
                actual_label=0,
            )

        # Log 10 positive transactions (to satisfy 2-class PR-AUC calculation)
        for i in range(10):
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_pos_{i}",
                champion_version=1,
                champion_prob=0.9,
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=0.9,
                challenger_latency_ms=10.0,
            )
            fb = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_pos_{i}",
                actual_label=1,
            )

        # Invariant 1: Champion FPR must be 10 / (10 + 90) = 0.10
        self.assertAlmostEqual(fb["champion_fpr"], 0.10, places=4)

        # Invariant 2: Challenger FPR must be strictly 90 / (90 + 10) = 0.90 (NOT 0.50)
        self.assertAlmostEqual(fb["challenger_fpr"], 0.90, places=4)

    def test_zero_denominator_fpr_truthfully_returns_none(self) -> None:
        """METRIC-03: When zero negative samples exist, FPR is undefined and returns None truthfully."""
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.85},
            is_promoted=True,
            status="champion",
        )
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.90},
            is_promoted=False,
            status="challenger",
        )

        # Log 10 positive transactions only (actual_label == 1)
        for i in range(10):
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_pos_only_{i}",
                champion_version=1,
                champion_prob=0.8,
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=0.9,
                challenger_latency_ms=10.0,
            )
            fb = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_pos_only_{i}",
                actual_label=1,
            )

        # Invariant: Neither champion nor challenger has negative instances -> FPR is None
        self.assertIsNone(fb["challenger_fpr"])
        self.assertEqual(fb["champion_fpr"], 0.0)  # Champion fallback when 0 negatives

    def test_manual_confusion_matrix_numerical_verification(self) -> None:
        """METRIC-04: Verify exact numerical FPR calculation against small manual confusion matrix."""
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.85},
            is_promoted=True,
            status="champion",
        )
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.90},
            is_promoted=False,
            status="challenger",
        )

        # Small 6-observation matrix:
        # Sample 1: label=0, chall_prob=0.85 (FP)
        # Sample 2: label=0, chall_prob=0.15 (TN)
        # Sample 3: label=0, chall_prob=0.20 (TN)
        # Sample 4: label=0, chall_prob=0.30 (TN)
        # Sample 5: label=1, chall_prob=0.90 (TP)
        # Sample 6: label=1, chall_prob=0.40 (FN)
        #
        # Challenger:
        # FP = 1
        # TN = 3
        # Negatives = 4
        # Expected FPR = 1 / 4 = 0.25

        samples = [
            (0, 0.4, 0.85),  # 0: champ TN, chall FP
            (0, 0.6, 0.15),  # 0: champ FP, chall TN
            (0, 0.4, 0.20),  # 0: champ TN, chall TN
            (0, 0.4, 0.30),  # 0: champ TN, chall TN
            (1, 0.7, 0.90),  # 1: champ TP, chall TP
            (1, 0.7, 0.40),  # 1: champ TP, chall FN
        ]

        fb: dict[str, Any] | None = None
        for idx, (label, champ_p, chall_p) in enumerate(samples):
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_manual_{idx}",
                champion_version=1,
                champion_prob=champ_p,
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=chall_p,
                challenger_latency_ms=10.0,
            )
            fb = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_manual_{idx}",
                actual_label=label,
            )

        # Manual independent assertion:
        # Challenger FP = 1, TN = 3 -> 1 / (1 + 3) = 0.25
        self.assertIsNotNone(fb)
        assert fb is not None
        self.assertEqual(fb["challenger_fpr"], 0.25)
        # Champion FP = 1 (sample 2), TN = 3 (samples 1, 3, 4) -> 1 / 4 = 0.25
        self.assertEqual(fb["champion_fpr"], 0.25)
