"""Unit and Adversarial Test Suite for Model Evaluation Cohort Consistency.

Verifies invariants:
- COHORT-01: Incomparable populations (disjoint or non-overlapping transactions) cannot trigger challenger promotion.
- COHORT-02: Observations missing valid scores or ground-truth labels are excluded from paired promotion cohort.
- COHORT-03: Single-class observation windows yield undefined PR-AUC and defer promotion safely.
- COHORT-04: Insufficient matched samples (< 5) defer promotion truthfully.
- COHORT-05: Global descriptive champion monitoring metrics are disentangled from paired challenger promotion metrics.
- COHORT-06: Fairly matched, comparable evaluation cohort allows legitimate automated promotion.
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


class TestModelEvaluationCohortConsistency(unittest.TestCase):
    """Behavior-oriented tests ensuring promotion compares champion and challenger on valid, matched cohorts."""

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

        self.payload_secret = "cohort_consistency_secret_entropy_32bytes"
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
        self.simulation_id = f"sim_cohort_{self._testMethodName}"

        # Authoritatively bind ownership
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

    def test_incomparable_disjoint_cohorts_cannot_promote_challenger(self) -> None:
        """COHORT-01: Disjoint transaction populations cannot trigger promotion via unmatched PR-AUC."""
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

        # Champion scored on 5 difficult edge transactions (challenger not scored on these)
        for i in range(5):
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_champ_hard_{i}",
                champion_version=1,
                champion_prob=0.45,
                champion_latency_ms=10.0,
                challenger_version=None,
                challenger_prob=None,
            )
            self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_champ_hard_{i}",
                actual_label=1 if i % 2 == 1 else 0,
            )

        # Challenger scored on 5 easy transactions (champion not scored on these)
        for i in range(5):
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_chall_easy_{i}",
                champion_version=None,
                champion_prob=None,
                champion_latency_ms=0.0,
                challenger_version=2,
                challenger_prob=0.99 if i % 2 == 1 else 0.01,
                challenger_latency_ms=10.0,
            )
            fb = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_chall_easy_{i}",
                actual_label=1 if i % 2 == 1 else 0,
            )

        # Invariant: Matched cohort size is 0 (< 5 minimum). Promotion must be deferred.
        self.assertFalse(fb["promotion_triggered"])
        self.assertEqual(fb["traffic_share"], 0.0)
        self.assertEqual(fb["paired_sample_count"], 0)
        self.assertIn("insufficient matched observations", fb["promotion_message"].lower())
        self.assertEqual(self.registry.get_active_version(self.simulation_id)["version"], 1)

    def test_missing_ground_truth_labels_excluded_from_matched_cohort(self) -> None:
        """COHORT-02: Records without confirmed ground truth labels are excluded from paired promotion."""
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

        # Log 5 predictions with paired scores, but ONLY 3 get ground truth labels
        for i in range(5):
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_unlabeled_{i}",
                champion_version=1,
                champion_prob=0.6 if i % 2 == 1 else 0.4,
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=0.95 if i % 2 == 1 else 0.05,
                challenger_latency_ms=10.0,
            )
            if i < 3:
                self.eval_engine.log_feedback(
                    simulation_id=self.simulation_id,
                    transaction_id=f"tx_unlabeled_{i}",
                    actual_label=1 if i % 2 == 1 else 0,
                )

        # 2 additional champion-only labeled transactions to pass global fleet warmup (5 labeled total)
        for j in range(2):
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_champ_warmup_{j}",
                champion_version=1,
                champion_prob=0.5,
                champion_latency_ms=10.0,
                challenger_version=None,
            )
            self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_champ_warmup_{j}",
                actual_label=j % 2,
            )

        # Total labeled observations in window = 5 (past warmup), but only 3 are labeled matched observations
        fb = self.eval_engine.evaluate_performance(self.simulation_id)

        # Invariant: Only 3 labeled matched observations -> promotion deferred (< 5 minimum)
        self.assertFalse(fb["promotion_triggered"])
        self.assertEqual(fb["paired_sample_count"], 3)
        self.assertIn("insufficient matched observations (3 < 5 minimum)", fb["promotion_message"])

    def test_single_class_labels_fail_closed_with_deferred_promotion(self) -> None:
        """COHORT-03: Single class windows (all 1s or all 0s) yield undefined PR-AUC and defer promotion safely."""
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

        # 5 matched records, but ALL actual labels are 1 (degenerate single-class evaluation window)
        for i in range(5):
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_all_ones_{i}",
                champion_version=1,
                champion_prob=0.5,
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=0.9,
                challenger_latency_ms=10.0,
            )
            fb = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_all_ones_{i}",
                actual_label=1,
            )

        # Invariant: PR-AUC is undefined (None) on single-class window -> fail closed
        self.assertFalse(fb["promotion_triggered"])
        self.assertEqual(fb["traffic_share"], 0.0)
        self.assertIsNone(fb["challenger_pr_auc"])
        self.assertIsNone(fb["paired_champion_pr_auc"])
        self.assertIn("pr-auc undefined on matched cohort", fb["promotion_message"].lower())

    def test_disentangled_global_champion_monitoring_vs_paired_promotion_metrics(self) -> None:
        """COHORT-05: Global fleet champion metrics and paired challenger promotion metrics are clearly distinguished."""
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

        # 10 global transactions where Champion was evaluated in production (challenger not evaluated)
        for i in range(10):
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_global_champ_{i}",
                champion_version=1,
                champion_prob=0.7 if i % 2 == 1 else 0.3,
                champion_latency_ms=10.0,
                challenger_version=None,
                challenger_prob=None,
            )
            self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_global_champ_{i}",
                actual_label=1 if i % 2 == 1 else 0,
            )

        # 5 shadow transactions where both Champion and Challenger were evaluated
        actuals = [1, 0, 1, 0, 1]
        champ_probs = [0.6, 0.4, 0.6, 0.4, 0.4]
        chall_probs = [0.95, 0.05, 0.95, 0.05, 0.95]

        for i in range(5):
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_shadow_pair_{i}",
                champion_version=1,
                champion_prob=champ_probs[i],
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=chall_probs[i],
                challenger_latency_ms=10.0,
            )
            fb = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=f"tx_shadow_pair_{i}",
                actual_label=actuals[i],
            )


        # Invariant: Global champion metrics cover all 15 records
        self.assertEqual(fb["sample_count"], 15)
        self.assertIsNotNone(fb["champion_pr_auc"])

        # Paired promotion metrics cover only the 5 matched pairs
        self.assertEqual(fb["paired_sample_count"], 5)
        self.assertEqual(fb["challenger_version"], 2)
        self.assertIsNotNone(fb["paired_champion_pr_auc"])
        self.assertIsNotNone(fb["challenger_pr_auc"])
        # Outperformance on matched cohort shifts traffic to canary
        self.assertTrue(fb["promotion_triggered"])
        self.assertEqual(fb["traffic_share"], 0.1)

    def test_valid_matched_cohort_allows_legitimate_automated_promotion(self) -> None:
        """COHORT-06: Comparable paired observation cohorts enable valid automated promotion."""
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

        # 5 shadow samples
        for i in range(5):
            tx_id = f"tx_matched_shadow_{i}"
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
            fb_shadow = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                actual_label=actuals[i],
            )
        self.assertEqual(fb_shadow["traffic_share"], 0.1)

        # 5 canary samples
        for i in range(5):
            tx_id = f"tx_matched_canary_{i}"
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
            fb_canary = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                actual_label=actuals[i],
            )

        self.assertEqual(fb_canary["traffic_share"], 0.0)
        self.assertTrue(fb_canary["promotion_triggered"])
        active = self.registry.get_active_version(self.simulation_id)
        self.assertEqual(active["version"], 2)
        self.assertEqual(active["status"], "champion")
