"""Unit and Adversarial Test Suite for Model Evaluation Gating and Automated Challenger Promotion.

Tests invariants:
- EVAL-01: Caller-supplied metadata metrics cannot bypass evaluation gating; independent feedback is mandatory.
- EVAL-02: Warmup and undefined PR-AUC safeguards defer promotion safely (fail-closed, 0% traffic shift).
- EVAL-03: Genuine outperformance triggers 10% canary shift, followed by full champion promotion.
- EVAL-04: Automated challenger promotion invokes promote_version with properly scoped trusted context and audit attribution.
- EVAL-05: Automated challenger promotion fails closed if challenger is revoked mid-evaluation.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

import torch

from app.application.services.model_registry import (
    ModelEvaluationEngine,
    ModelRegistry,
    create_trusted_internal_context,
)
from app.config import get_settings
from app.infrastructure.security.durable_nonce_store import (
    DurableNonceStore,
    reset_default_nonce_store,
)
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain


class TestModelEvaluationAdmission(unittest.TestCase):
    """Verifies that automated challenger promotion relies on independent evidence and correct authority."""

    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp()
        self.nonce_file = os.path.join(self.test_dir, "nonces.json")
        self.audit_file = os.path.join(self.test_dir, "audit.json")
        self.reg_dir = os.path.join(self.test_dir, "models")
        os.makedirs(self.reg_dir, exist_ok=True)

        self.payload_secret = "eval_admission_secret_entropy_high_32bytes"
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

        from app.infrastructure.redis_store import RedisStore
        RedisStore._shared_fallback_stores.clear()

        self.registry = ModelRegistry(storage_dir=self.reg_dir)
        self.eval_engine = ModelEvaluationEngine(self.registry)
        self.simulation_id = f"sim_eval_{self._testMethodName}"

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
        shutil.rmtree(self.test_dir, ignore_errors=True)
        get_settings.cache_clear()
        reset_default_nonce_store()
        DurableNonceStore._instance = None
        ImmutableAuditChain.reset_instance()
        from app.infrastructure.security.auth_service import AuthenticationService
        AuthenticationService._instance = None
        from app.infrastructure.redis_store import RedisStore
        RedisStore._shared_fallback_stores.clear()
        from app.application.services.training_provenance_service import (
            reset_default_training_verifier,
        )
        reset_default_training_verifier()

    def test_unverified_caller_metrics_cannot_bypass_evaluation_engine(self) -> None:
        """EVAL-01: Metrics supplied at registration do not automatically shift traffic or promote."""
        # Save champion
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.85, "pr_auc": 0.80},
            is_promoted=True,
            status="champion",
        )
        # Save challenger claiming unrealistic 1.0 metrics
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 1.0, "pr_auc": 1.0},
            is_promoted=False,
            status="challenger",
        )

        # Invariant: Traffic share remains 0.0, champion remains v1
        traffic_key = f"traffic_share:{self.simulation_id}"
        val = self.eval_engine._store.get(traffic_key)
        self.assertEqual(float(val) if val is not None else 0.0, 0.0)
        active = self.registry.get_active_version(self.simulation_id)
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active["version"], 1)

    def test_evaluation_warmup_and_single_class_safeguard(self) -> None:
        """EVAL-02: Insufficient sample count (<5) returns warmup; single-class labels defer promotion."""
        # Log 3 feedback entries (less than 5 threshold)
        for i in range(3):
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_warm_{i}",
                champion_version=1,
                champion_prob=0.6,
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=0.9,
                challenger_latency_ms=10.0,
            )
            feedback = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_warm_{i}",
                actual_label=1,
            )
        self.assertEqual(feedback["status"], "warmup")
        self.assertEqual(feedback["sample_count"], 3)

        # Log 2 more but with all positive actual labels (single class -> PR-AUC undefined)
        for i in range(3, 5):
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_warm_{i}",
                champion_version=1,
                champion_prob=0.6,
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=0.9,
                challenger_latency_ms=10.0,
            )
            feedback = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_warm_{i}",
                actual_label=1,
            )
        # 5 samples reached, but single-class labels mean PR-AUC is None -> promotion deferred
        self.assertEqual(feedback["sample_count"], 5)
        self.assertFalse(feedback["promotion_triggered"])
        self.assertEqual(feedback["traffic_share"], 0.0)
        self.assertIn("deferred", feedback["promotion_message"].lower())

    def test_valid_evaluation_evidence_triggers_canary_then_full_promotion(self) -> None:
        """EVAL-03: Robust 2-class observations trigger 10% canary, then full promotion to champion."""
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

        # Batch 1: 5 balanced samples where challenger outperforms champion
        actuals = [1, 0, 1, 0, 1]
        champ_probs = [0.6, 0.4, 0.6, 0.4, 0.4]
        chall_probs = [0.95, 0.05, 0.95, 0.05, 0.95]

        for i in range(5):
            tx_id = f"tx_canary_{i}"
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                champion_version=1,
                champion_prob=champ_probs[i],
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=chall_probs[i],
                challenger_latency_ms=10.0,
            )
            fb = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                actual_label=actuals[i],
            )

        # Invariant 1: canary traffic shifted to 10%
        self.assertEqual(fb["traffic_share"], 0.1)
        self.assertTrue(fb["promotion_triggered"])

        # Batch 2: next 5 balanced samples with continued outperformance
        for i in range(5):
            tx_id = f"tx_full_{i}"
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                champion_version=1,
                champion_prob=champ_probs[i],
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=chall_probs[i],
                challenger_latency_ms=10.0,
            )
            fb_full = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                actual_label=actuals[i],
            )

        # Invariant 2: full promotion triggered (traffic share reset to 0.0, v2 is active Champion)
        self.assertEqual(fb_full["traffic_share"], 0.0)
        self.assertTrue(fb_full["promotion_triggered"])
        active = self.registry.get_active_version(self.simulation_id)
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active["version"], 2)
        self.assertEqual(active["status"], "champion")

    def test_evaluation_engine_promotes_with_trusted_context_and_promotion_attribution(self) -> None:
        """EVAL-04: Automated promotion uses promote_version (not rollback) and attributes audit trail correctly."""
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

        # Feed 10 transactions to trigger canary then full promotion
        actuals = [1, 0, 1, 0, 1] * 2
        champ_probs = [0.6, 0.4, 0.6, 0.4, 0.4] * 2
        chall_probs = [0.95, 0.05, 0.95, 0.05, 0.95] * 2
        for i in range(10):
            tx_id = f"tx_attr_{i}"
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                champion_version=1,
                champion_prob=champ_probs[i],
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=chall_probs[i],
                challenger_latency_ms=10.0,
            )
            self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                actual_label=actuals[i],
            )

        # Verify audit trail contains MODEL_PROMOTION and MODEL_PROMOTION_PENDING (not rollback)
        audit = ImmutableAuditChain.get_instance()
        events = audit.get_snapshot()
        event_types = [e.event_type for e in events]

        self.assertIn("MODEL_PROMOTION_PENDING", event_types)
        self.assertIn("MODEL_PROMOTION", event_types)
        # Ensure rollback events were NOT erroneously emitted for challenger promotion
        rollback_events = [e for e in event_types if "ROLLBACK" in e]
        self.assertEqual(len(rollback_events), 0)

    def test_evaluation_engine_promotion_aborts_safely_if_challenger_revoked(self) -> None:
        """EVAL-05: If challenger is revoked before promotion, promotion aborts fail-closed."""
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.85},
            is_promoted=True,
            status="champion",
        )
        v2 = self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.90},
            is_promoted=False,
            status="challenger",
        )

        # Shift to canary (5 samples)
        actuals = [1, 0, 1, 0, 1]
        champ_probs = [0.6, 0.4, 0.6, 0.4, 0.4]
        chall_probs = [0.95, 0.05, 0.95, 0.05, 0.95]
        for i in range(5):
            tx_id = f"tx_abort_{i}"
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                champion_version=1,
                champion_prob=champ_probs[i],
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=chall_probs[i],
                challenger_latency_ms=10.0,
            )
            fb = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                actual_label=actuals[i],
            )
        self.assertEqual(fb["traffic_share"], 0.1)

        # Revoke challenger v2 mid-flight
        self.registry.revoke_version(
            simulation_id=self.simulation_id,
            version=v2["version"],
            reason="Material flaw found in challenger weights",
            actor="safety_officer",
        )

        # Next 5 samples attempt full promotion, which should fail closed
        fb_next = None
        for i in range(5):
            tx_id = f"tx_abort_next_{i}"
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                champion_version=1,
                champion_prob=champ_probs[i],
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=chall_probs[i],
                challenger_latency_ms=10.0,
            )
            fb_next = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                actual_label=actuals[i],
            )

        # Invariant: promotion failed closed, champion remains v1
        self.assertIsNotNone(fb_next)
        assert fb_next is not None
        self.assertFalse(fb_next["promotion_triggered"])
        active = self.registry.get_active_version(self.simulation_id)
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active["version"], 1)


if __name__ == "__main__":
    unittest.main()
