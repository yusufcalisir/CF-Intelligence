"""Unit and Adversarial Test Suite for Model Evaluation Version Isolation.

Verifies invariants:
- VERS-01: Multiple challenger versions in an evaluation window are never silently pooled into a single metric.
- VERS-02: Promotion target is never arbitrarily selected based on first record appearance in logged predictions.
- VERS-03: Ambiguous or mixed challenger evaluation windows safely defer promotion fail-closed.
- VERS-04: Explicit target challenger version evaluation isolates exact candidate evidence without cross-version contamination.
- VERS-05: Challenger version changes during canary routing reset traffic share to 0.0 and safely defer promotion.
- VERS-06: Stale historical predictions from previous versions cannot be reused as evidence for a new candidate.
- VERS-07: Legitimate single-challenger evaluation and automated promotion is fully preserved.
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
from app.infrastructure.redis_store import RedisStore
from app.infrastructure.security.durable_nonce_store import (
    DurableNonceStore,
    reset_default_nonce_store,
)
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain


class TestModelEvaluationVersionIsolation(unittest.TestCase):
    """Behavior-oriented tests for strict version isolation in model evaluation and promotion decisions."""

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

        self.payload_secret = "version_isolation_secret_entropy_32bytes_test"
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
        self.simulation_id = f"sim_vers_{self._testMethodName}"

        # Authoritatively bind ownership to bank_a
        ctx_bind = create_trusted_internal_context(
            service_name="training_orchestrator",
            action="bind_ownership",
            resource_type="model",
            resource_id=self.simulation_id,
            bank_id="bank_a",
        )
        self.registry.bind_simulation_ownership(self.simulation_id, owning_bank_id="bank_a", trusted_context=ctx_bind)

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

    def test_mixed_challenger_versions_do_not_pool_metrics_and_defer_promotion(self) -> None:
        """VERS-01 & VERS-03: Mixed challenger versions (v2 and v3) in evaluation window defer promotion without pooling."""
        # v1: Active champion
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.85},
            is_promoted=True,
            status="champion",
        )
        # v2: Poor candidate (low scores)
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.70},
            is_promoted=False,
            status="challenger",
        )
        # v3: Superior candidate (high scores)
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.95},
            is_promoted=False,
            status="challenger",
        )

        # Log 5 predictions for v2 (low accuracy on fraud labels)
        for i in range(5):
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_v2_{i}",
                champion_version=1,
                champion_prob=0.6 if i % 2 == 1 else 0.4,
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=0.1,  # Terrible predictor
                challenger_latency_ms=12.0,
            )
            self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_v2_{i}",
                actual_label=1 if i % 2 == 1 else 0,
            )

        # Log 5 predictions for v3 (high accuracy)
        for i in range(5):
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_v3_{i}",
                champion_version=1,
                champion_prob=0.6 if i % 2 == 1 else 0.4,
                champion_latency_ms=10.0,
                challenger_version=3,
                challenger_prob=0.95 if i % 2 == 1 else 0.05,  # Excellent predictor
                challenger_latency_ms=8.0,
            )
            fb = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_v3_{i}",
                actual_label=1 if i % 2 == 1 else 0,
            )

        # Invariant 1: Promotion must NOT be triggered when mixed versions create an ambiguous window
        self.assertFalse(fb["promotion_triggered"])
        self.assertEqual(fb["traffic_share"], 0.0)
        self.assertIn("ambiguous mixed challenger versions", fb["promotion_message"].lower())
        self.assertIn("[2, 3]", fb["promotion_message"])

        # Invariant 2: Active version must remain champion v1 (neither v2 nor v3 promoted)
        active = self.registry.get_active_version(self.simulation_id)
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active["version"], 1)
        self.assertEqual(active["status"], "champion")

    def test_first_record_heuristic_defect_is_prevented(self) -> None:
        """VERS-02: Ensure candidate v2 is NOT chosen merely because its record appears first in store."""
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
            metrics={"auc": 0.70},
            is_promoted=False,
            status="challenger",
        )
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.95},
            is_promoted=False,
            status="challenger",
        )

        # First record in the database belongs to challenger v2
        self.eval_engine.log_prediction(
            simulation_id=self.simulation_id,
            transaction_id="tx_first_v2",
            champion_version=1,
            champion_prob=0.5,
            champion_latency_ms=10.0,
            challenger_version=2,
            challenger_prob=0.1,
            challenger_latency_ms=10.0,
        )
        self.eval_engine.log_feedback(
            simulation_id=self.simulation_id,
            transaction_id="tx_first_v2",
            actual_label=1,
        )

        # 5 subsequent records for challenger v3
        for i in range(5):
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_subsequent_v3_{i}",
                champion_version=1,
                champion_prob=0.5,
                champion_latency_ms=10.0,
                challenger_version=3,
                challenger_prob=0.9,
                challenger_latency_ms=10.0,
            )
            fb = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_subsequent_v3_{i}",
                actual_label=1 if i % 2 == 1 else 0,
            )

        # The engine must safely detect ambiguous window and NOT promote v2 simply because v2 was first
        self.assertFalse(fb["promotion_triggered"])
        active = self.registry.get_active_version(self.simulation_id)
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active["version"], 1)

    def test_explicit_target_version_isolates_candidate_metrics(self) -> None:
        """VERS-04: Explicit target_challenger_version isolates evaluation to exactly that version's cohort."""
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
            metrics={"auc": 0.70},
            is_promoted=False,
            status="challenger",
        )
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.95},
            is_promoted=False,
            status="challenger",
        )

        # Mix predictions: 5 from v2, 5 from v3
        for i in range(5):
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_v2_{i}",
                champion_version=1,
                champion_prob=0.5,
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=0.1,
                challenger_latency_ms=10.0,
            )
            self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_v2_{i}",
                actual_label=1 if i % 2 == 1 else 0,
            )

        for i in range(5):
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_v3_{i}",
                champion_version=1,
                champion_prob=0.6 if i % 2 == 1 else 0.4,
                champion_latency_ms=10.0,
                challenger_version=3,
                challenger_prob=0.99 if i % 2 == 1 else 0.01,
                challenger_latency_ms=10.0,
            )
            self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_v3_{i}",
                actual_label=1 if i % 2 == 1 else 0,
                target_challenger_version=3,
            )

        # Evaluating specifically target_challenger_version=3:
        metrics_v3 = self.eval_engine.evaluate_performance(
            simulation_id=self.simulation_id,
            target_challenger_version=3,
        )

        # v3 cohort metrics must reflect v3 predictions alone (paired_sample_count == 5, high PR-AUC)
        self.assertEqual(metrics_v3["challenger_version"], 3)
        self.assertEqual(metrics_v3["paired_sample_count"], 5)
        self.assertIsNotNone(metrics_v3["challenger_pr_auc"])
        self.assertGreater(metrics_v3["challenger_pr_auc"], 0.90)

    def test_version_change_during_canary_evaluation_resets_traffic_and_defers(self) -> None:
        """VERS-05: If challenger version changes while canary traffic is active (0.1), traffic share resets to 0.0 and defers."""
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
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.92},
            is_promoted=False,
            status="challenger",
        )

        # 5 samples where challenger v2 outperforms champion v1 -> canary shifts to 10%
        actuals = [1, 0, 1, 0, 1]
        champ_probs = [0.6, 0.4, 0.6, 0.4, 0.4]
        chall_probs = [0.95, 0.05, 0.95, 0.05, 0.95]

        for i in range(5):
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_canary_{i}",
                champion_version=1,
                champion_prob=champ_probs[i],
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=chall_probs[i],
                challenger_latency_ms=10.0,
            )
            fb_canary = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_canary_{i}",
                actual_label=actuals[i],
            )

        self.assertEqual(fb_canary["traffic_share"], 0.1)
        self.assertTrue(fb_canary["promotion_triggered"])
        # Canary is currently assigned to version 2

        # Now, before canary is completed, new predictions for challenger v3 arrive
        # (e.g. rapid deployment of v3 while v2 was in canary)
        # Clear older records to simulate window transitioning to v3
        self.eval_engine._store.set(f"{self.simulation_id}:prediction_keys", [])
        for i in range(5):
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_v3_shift_{i}",
                champion_version=1,
                champion_prob=champ_probs[i],
                champion_latency_ms=10.0,
                challenger_version=3,
                challenger_prob=chall_probs[i],
                challenger_latency_ms=10.0,
            )
            fb_shift = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_v3_shift_{i}",
                actual_label=actuals[i],
            )

        # Invariant: Traffic share must be reset to 0.0 because version changed mid-canary
        self.assertEqual(fb_shift["traffic_share"], 0.0)
        self.assertFalse(fb_shift["promotion_triggered"])
        self.assertIn("version changed from v2 to v3", fb_shift["promotion_message"].lower())

        # Champion must remain v1
        active = self.registry.get_active_version(self.simulation_id)
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active["version"], 1)

    def test_stale_historical_predictions_cannot_justify_new_candidate_promotion(self) -> None:
        """VERS-06: Historical predictions from retired challenger cannot justify promoting a newly registered challenger."""
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.85},
            is_promoted=True,
            status="champion",
        )
        # Stale challenger v2 had excellent metrics
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.95},
            is_promoted=False,
            status="challenger",
        )

        for i in range(10):
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_old_{i}",
                champion_version=1,
                champion_prob=0.5,
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=0.99 if i % 2 == 1 else 0.01,
                challenger_latency_ms=10.0,
            )
            self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_old_{i}",
                actual_label=1 if i % 2 == 1 else 0,
            )

        # Now register new challenger v3 (which only has 1 prediction so far)
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.88},
            is_promoted=False,
            status="challenger",
        )
        self.eval_engine.log_prediction(
            simulation_id=self.simulation_id,
            transaction_id="tx_new_v3",
            champion_version=1,
            champion_prob=0.5,
            champion_latency_ms=10.0,
            challenger_version=3,
            challenger_prob=0.9,
            challenger_latency_ms=10.0,
        )
        fb_new = self.eval_engine.log_feedback(
            simulation_id=self.simulation_id,
            transaction_id="tx_new_v3",
            actual_label=1,
            target_challenger_version=3,
        )

        # Invariant: v3 cannot borrow v2's 10 historical records. It only has 1 matched record (< 5 minimum).
        self.assertFalse(fb_new["promotion_triggered"])
        self.assertEqual(fb_new["traffic_share"], 0.0)
        self.assertIn("insufficient matched observations", fb_new["promotion_message"].lower())
        active = self.registry.get_active_version(self.simulation_id)
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active["version"], 1)

    def test_single_challenger_legitimate_promotion_preserved(self) -> None:
        """VERS-07: Legitimate single-challenger evaluation seamlessly progresses from shadow -> canary -> champion."""
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

        actuals = [1, 0, 1, 0, 1]
        champ_probs = [0.6, 0.4, 0.6, 0.4, 0.4]
        chall_probs = [0.95, 0.05, 0.95, 0.05, 0.95]

        # Phase 1: 5 balanced shadow observations trigger canary
        for i in range(5):
            tx_id = f"tx_legit_shadow_{i}"
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
            fb_phase1 = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                actual_label=actuals[i],
            )

        self.assertEqual(fb_phase1["traffic_share"], 0.1)
        self.assertTrue(fb_phase1["promotion_triggered"])

        # Phase 2: 5 balanced canary observations trigger full champion promotion
        for i in range(5):
            tx_id = f"tx_legit_canary_{i}"
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
            fb_phase2 = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                actual_label=actuals[i],
            )

        self.assertEqual(fb_phase2["traffic_share"], 0.0)
        self.assertTrue(fb_phase2["promotion_triggered"])
        active = self.registry.get_active_version(self.simulation_id)
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active["version"], 2)
        self.assertEqual(active["status"], "champion")
