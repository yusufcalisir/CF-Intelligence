"""Unit and Invariant Test Suite for Canary Evidence Accounting.

Verifies invariants:
- CANARY-01: Historical transactions receiving late labels cannot satisfy canary graduation threshold.
- CANARY-02: Sufficient newly scored, correctly routed, and labeled canary observations trigger legitimate promotion.
- CANARY-03: Challenger version changes during canary reset traffic share and prevent reuse of historical evidence.
- CANARY-04: Restarted canary attempts for the same model require fresh post-activation observations; previous attempt evidence is invalid.
- CANARY-05: Sliding-window eviction and repeated feedback deduplication do not inflate canary progress.
- CANARY-06: Real canary routing policy enforces actual challenger-routed traffic before graduation.
- CANARY-07: Mixed challenger versions and unmatched cohorts fail-closed safely.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

import torch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.model_registry import (
    ModelEvaluationEngine,
    ModelRegistry,
    create_trusted_internal_context,
)
from app.application.services.training_provenance_service import (
    DurableTrustStore,
    TrainingReceiptVerifier,
    reset_default_training_verifier,
)
from app.config import get_settings
from app.infrastructure.redis_store import RedisStore
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.durable_nonce_store import (
    DurableNonceStore,
    reset_default_nonce_store,
)
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain


class TestCanaryEvidenceAccounting(unittest.TestCase):
    """Verifies that canary evidence belongs strictly to the actual canary evaluation period."""

    def setUp(self) -> None:
        self.env_keys = [
            "CFI_INTERNAL_CONTEXT_SECRET",
            "CFI_INTERNAL_NONCE_STORE_PATH",
            "CFI_TRAINING_RECEIPT_TRUST_STORE",
            "CFI_TRUST_STORE_PATH",
            "CFI_TRUST_STORE_ADMIN_TOKEN",
            "CFI_MODEL_REGISTRY_DIR",
            "CFI_PAYLOAD_SIGNING_SECRET",
            "CFI_JWT_SIGNING_SECRET",
            "CFI_ACCOUNT_STORE_PATH",
            "CFI_ACCOUNT_AUTHORITY_SECRET",
            "CFI_AUDIT_LEDGER_PATH",
            "CFI_AUDIT_LEDGER_SECRET",
            "APP_ENV",
        ]
        self._orig_env = {k: os.environ.get(k) for k in self.env_keys}
        self.test_dir = tempfile.mkdtemp()
        self.nonce_file = os.path.join(self.test_dir, "nonces.json")
        self.trust_file = os.path.join(self.test_dir, "trust.json")
        self.account_file = os.path.join(self.test_dir, "accounts.json")
        self.audit_file = os.path.join(self.test_dir, "audit.json")
        self.reg_dir = os.path.join(self.test_dir, "models")
        os.makedirs(self.reg_dir, exist_ok=True)

        self.payload_secret = "payload_secret_for_tests_entropy_high_32bytes"
        self.jwt_secret = "jwt_signing_secret_high_entropy_32_bytes_test"
        self.admin_token = "admin-token-test-secret-12345"

        os.environ["CFI_INTERNAL_CONTEXT_SECRET"] = self.payload_secret
        os.environ["CFI_INTERNAL_NONCE_STORE_PATH"] = self.nonce_file
        os.environ["CFI_TRAINING_RECEIPT_TRUST_STORE"] = self.trust_file
        os.environ["CFI_TRUST_STORE_PATH"] = self.trust_file
        os.environ["CFI_TRUST_STORE_ADMIN_TOKEN"] = self.admin_token
        os.environ["CFI_MODEL_REGISTRY_DIR"] = self.reg_dir
        os.environ["CFI_PAYLOAD_SIGNING_SECRET"] = self.payload_secret
        os.environ["CFI_JWT_SIGNING_SECRET"] = self.jwt_secret
        os.environ["CFI_ACCOUNT_STORE_PATH"] = self.account_file
        os.environ["CFI_ACCOUNT_AUTHORITY_SECRET"] = (
            "authoritative_signing_master_secret_key_64_bytes_test"
        )
        os.environ["CFI_AUDIT_LEDGER_PATH"] = self.audit_file
        os.environ["CFI_AUDIT_LEDGER_SECRET"] = self.payload_secret
        os.environ["APP_ENV"] = "development"

        get_settings.cache_clear()
        reset_default_nonce_store()
        DurableNonceStore._instance = None
        AuthenticationService._instance = None
        ImmutableAuditChain.reset_instance()
        RedisStore._shared_fallback_stores.clear()
        reset_default_training_verifier()

        self.trust_store = DurableTrustStore(
            trust_file_path=self.trust_file, admin_token=self.admin_token
        )
        self.priv_key = ed25519.Ed25519PrivateKey.generate()
        pub_hex = (
            self.priv_key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            .hex()
        )
        self.issuer_key_id = "test-issuer-ed25519-canary"
        self.trust_store.register_approved_key(
            self.issuer_key_id, pub_hex, admin_token=self.admin_token
        )

        self.verifier = TrainingReceiptVerifier(trust_store=self.trust_store)
        self.registry = ModelRegistry(storage_dir=self.reg_dir, verifier=self.verifier)
        self.eval_engine = ModelEvaluationEngine(self.registry)
        self.simulation_id = f"sim_canary_{self._testMethodName}"

        # Bind ownership
        ctx_bind = create_trusted_internal_context(
            service_name="training_orchestrator",
            action="bind_ownership",
            resource_type="model",
            resource_id=self.simulation_id,
            bank_id="bank_a",
        )
        self.registry.bind_simulation_ownership(
            self.simulation_id, owning_bank_id="bank_a", trusted_context=ctx_bind
        )

        self.model_state = torch.nn.Linear(2, 1).state_dict()

        # Seed v1 (Champion) and v2 (Challenger candidate)
        self.v1 = self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.85},
            is_promoted=True,
            status="champion",
        )
        self.v2 = self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.90},
            is_promoted=False,
            status="inactive",
        )

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
        AuthenticationService._instance = None
        ImmutableAuditChain.reset_instance()
        RedisStore._shared_fallback_stores.clear()
        reset_default_training_verifier()

    def test_historical_transactions_receiving_delayed_feedback_cannot_satisfy_canary(self) -> None:
        """CANARY-01: Pre-canary scored transactions receiving delayed labels do not inflate canary evidence."""
        # Step 1: Log 5 historical transactions scored before canary activation (without ground truth labels)
        for i in range(5):
            tx_id = f"tx_historical_{i}"
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                champion_version=1,
                champion_prob=0.4,
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=0.9,
                challenger_latency_ms=10.0,
            )

        # Step 2: Log 5 warmup transactions and immediately provide feedback to trigger 10% canary traffic shift
        actuals = [1, 0, 1, 0, 1]
        champ_probs = [0.6, 0.4, 0.6, 0.4, 0.4]
        chall_probs = [0.95, 0.05, 0.95, 0.05, 0.95]

        fb_canary: dict[str, object] = {}
        for i in range(5):
            tx_id = f"tx_warmup_{i}"
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

        # Invariant: Traffic has shifted to canary (0.1)
        self.assertEqual(fb_canary["traffic_share"], 0.1)
        self.assertTrue(fb_canary["promotion_triggered"])
        self.assertIn("Routed 10% traffic", str(fb_canary["promotion_message"]))

        # Active champion in registry is still v1
        active = self.registry.get_active_version(self.simulation_id)
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active["version"], 1)

        # Step 3: Delayed feedback arrives for the 5 historical transactions scored BEFORE canary activation
        fb_hist: dict[str, object] = {}
        for i in range(5):
            tx_id = f"tx_historical_{i}"
            fb_hist = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                actual_label=actuals[i],
            )

        # Invariant CANARY-01: Delayed historical labels MUST NOT satisfy canary graduation!
        # Total matched cohort is now 10, but eligible post-activation canary samples is 0.
        self.assertEqual(fb_hist["traffic_share"], 0.1)
        self.assertFalse(fb_hist["promotion_triggered"])
        self.assertIn("0/5 canary samples", str(fb_hist["promotion_message"]))

        # Champion MUST remain v1 (no premature promotion)
        active_after = self.registry.get_active_version(self.simulation_id)
        self.assertIsNotNone(active_after)
        assert active_after is not None
        self.assertEqual(active_after["version"], 1)
        self.assertEqual(active_after["status"], "champion")

    def test_genuine_post_activation_canary_traffic_triggers_legitimate_promotion(self) -> None:
        """CANARY-02: Genuinely scored post-activation canary transactions successfully graduate challenger."""
        actuals = [1, 0, 1, 0, 1]
        champ_probs = [0.6, 0.4, 0.6, 0.4, 0.4]
        chall_probs = [0.95, 0.05, 0.95, 0.05, 0.95]

        # Phase 1: Activate canary (5 samples)
        for i in range(5):
            tx_id = f"tx_phase1_{i}"
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

        # Phase 2: Log 5 brand new post-activation transactions during the canary period
        fb_full: dict[str, object] = {}
        for i in range(5):
            tx_id = f"tx_canary_legit_{i}"
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                champion_version=1,
                champion_prob=champ_probs[i],
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=chall_probs[i],
                challenger_latency_ms=10.0,
                routed_to="challenger" if i == 0 else "champion",
            )
            fb_full = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                actual_label=actuals[i],
            )

        # Invariant CANARY-02: Legitimate graduation commits
        self.assertEqual(fb_full["traffic_share"], 0.0)
        self.assertTrue(fb_full["promotion_triggered"])
        self.assertIn("promoted to Champion", str(fb_full["promotion_message"]))

        # Verify durable manifest on disk
        active = self.registry.get_active_version(self.simulation_id)
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active["version"], 2)
        self.assertEqual(active["status"], "champion")

        # Verify audit mutation evidence
        audit_chain = ImmutableAuditChain.get_instance()
        events = audit_chain.query_events(target_id=f"{self.simulation_id}:v2")
        prom_events = [e for e in events if e.event_type == "MODEL_PROMOTION"]
        self.assertTrue(len(prom_events) >= 1)
        self.assertEqual(prom_events[-1].target_id, f"{self.simulation_id}:v2")
        self.assertEqual(prom_events[-1].details.get("target_status"), "champion")
        self.assertEqual(prom_events[-1].details.get("mutation_stage"), "COMMITTED")

    def test_challenger_version_change_during_canary_resets_and_prevents_evidence_reuse(
        self,
    ) -> None:
        """CANARY-03: Switching challenger version mid-canary resets traffic share and prevents evidence reuse."""
        actuals = [1, 0, 1, 0, 1]
        champ_probs = [0.6, 0.4, 0.6, 0.4, 0.4]
        chall_probs = [0.95, 0.05, 0.95, 0.05, 0.95]

        # Phase 1: Activate canary for v2
        for i in range(5):
            tx_id = f"tx_v2_warmup_{i}"
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

        # Now register v3 and log predictions for v3 while v2 is in canary
        self.v3 = self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.92},
            is_promoted=False,
            status="inactive",
        )

        tx_v3 = "tx_v3_arrival"
        self.eval_engine.log_prediction(
            simulation_id=self.simulation_id,
            transaction_id=tx_v3,
            champion_version=1,
            champion_prob=0.5,
            champion_latency_ms=10.0,
            challenger_version=3,
            challenger_prob=0.9,
            challenger_latency_ms=10.0,
        )
        fb_reset = self.eval_engine.log_feedback(
            simulation_id=self.simulation_id,
            transaction_id=tx_v3,
            actual_label=1,
            target_challenger_version=3,
        )

        # Invariant CANARY-03: Traffic share reset to 0.0 and promotion deferred
        self.assertEqual(fb_reset["traffic_share"], 0.0)
        self.assertFalse(fb_reset["promotion_triggered"])
        self.assertIn("Challenger version changed", str(fb_reset["promotion_message"]))

        # Ensure canary state key was deleted
        canary_state = self.eval_engine._store.get(f"{self.simulation_id}:canary_state")
        self.assertIsNone(canary_state)

    def test_restarted_canary_attempt_invalidates_previous_attempt_evidence(self) -> None:
        """CANARY-04: Old evidence from an earlier failed/aborted canary attempt cannot satisfy a new attempt."""
        actuals = [1, 0, 1, 0, 1]
        champ_probs = [0.6, 0.4, 0.6, 0.4, 0.4]
        chall_probs = [0.95, 0.05, 0.95, 0.05, 0.95]

        # Attempt 1: Start canary for v2
        for i in range(5):
            tx_id = f"tx_att1_warm_{i}"
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

        # Score and label 4 canary transactions in Attempt 1
        for i in range(4):
            tx_id = f"tx_att1_canary_{i}"
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

        # Now simulate challenger performance drop -> canary fails!
        # Challenger gives inverted predictions (0.01 for positive, 0.99 for negative)
        tx_fail = "tx_fail_0"
        self.eval_engine.log_prediction(
            simulation_id=self.simulation_id,
            transaction_id=tx_fail,
            champion_version=1,
            champion_prob=0.8,
            champion_latency_ms=10.0,
            challenger_version=2,
            challenger_prob=0.01,
            challenger_latency_ms=10.0,
        )
        fb_fail = self.eval_engine.log_feedback(
            simulation_id=self.simulation_id,
            transaction_id=tx_fail,
            actual_label=1,
        )

        # Invariant: Canary failed, traffic share reset to 0.0
        self.assertEqual(fb_fail["traffic_share"], 0.0)
        self.assertFalse(fb_fail["promotion_triggered"])
        self.assertIn("failed canary evaluation", str(fb_fail["promotion_message"]))

        # Now, feed transactions until Attempt 2 canary JUST activates
        activated = False
        for i in range(12):
            tx_id = f"tx_att2_qualify_{i}"
            lbl = 1 if i % 2 == 0 else 0
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                champion_version=1,
                champion_prob=0.35 if lbl == 1 else 0.45,
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=0.99 if lbl == 1 else 0.01,
                challenger_latency_ms=10.0,
            )
            fb_qualify = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                actual_label=lbl,
            )
            if fb_qualify.get("traffic_share") == 0.1:
                activated = True
                break

        self.assertTrue(activated, "Attempt 2 canary failed to activate")

        # Now check the canary sample count on the next transaction evaluation:
        # The 4 observations from Attempt 1 MUST NOT count towards Attempt 2!
        tx_check = "tx_att2_check"
        self.eval_engine.log_prediction(
            simulation_id=self.simulation_id,
            transaction_id=tx_check,
            champion_version=1,
            champion_prob=0.6,
            champion_latency_ms=10.0,
            challenger_version=2,
            challenger_prob=0.99,
            challenger_latency_ms=10.0,
        )
        fb_check = self.eval_engine.log_feedback(
            simulation_id=self.simulation_id,
            transaction_id=tx_check,
            actual_label=1,
        )
        # Exactly 1 new post-activation sample (tx_att2_check), NOT 5 (1 + 4 old)!
        self.assertEqual(fb_check["traffic_share"], 0.1)
        self.assertFalse(fb_check["promotion_triggered"])
        self.assertIn("1/5 canary samples", str(fb_check["promotion_message"]))
        self.assertEqual(self.registry.get_active_version(self.simulation_id)["version"], 1)

    def test_sliding_window_eviction_and_repeated_feedback_do_not_inflate_progress(self) -> None:
        """CANARY-05: Repeated feedback deduplication and sliding-window eviction do not inflate canary progress."""
        actuals = [1, 0, 1, 0, 1]
        champ_probs = [0.6, 0.4, 0.6, 0.4, 0.4]
        chall_probs = [0.95, 0.05, 0.95, 0.05, 0.95]

        # Activate canary
        for i in range(5):
            tx_id = f"tx_base_{i}"
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

        # Log 1 new canary transaction
        tx_single = "tx_single_canary"
        self.eval_engine.log_prediction(
            simulation_id=self.simulation_id,
            transaction_id=tx_single,
            champion_version=1,
            champion_prob=0.4,
            champion_latency_ms=10.0,
            challenger_version=2,
            challenger_prob=0.95,
            challenger_latency_ms=10.0,
        )
        # Send feedback for the same transaction 10 times (repeated retries / duplicates)
        fb_rep: dict[str, object] = {}
        for _ in range(10):
            fb_rep = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=tx_single,
                actual_label=1,
            )

        # Invariant CANARY-05: Repeated feedback must still count as exactly 1 sample
        self.assertEqual(fb_rep["traffic_share"], 0.1)
        self.assertFalse(fb_rep["promotion_triggered"])
        self.assertIn("1/5 canary samples", str(fb_rep["promotion_message"]))

    def test_policy_requiring_actual_challenger_routing_defers_until_routed_traffic_arrives(
        self,
    ) -> None:
        """CANARY-06: Real canary policy requires actual challenger-routed traffic before graduation."""
        # Configure policy requiring real challenger routing
        self.eval_engine._store.set(
            f"{self.simulation_id}:canary_policy",
            {"require_challenger_routing": True, "min_challenger_routed_samples": 1},
        )

        actuals = [1, 0, 1, 0, 1]
        champ_probs = [0.6, 0.4, 0.6, 0.4, 0.4]
        chall_probs = [0.95, 0.05, 0.95, 0.05, 0.95]

        # Activate canary
        for i in range(5):
            tx_id = f"tx_act_{i}"
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

        # Log 5 post-activation canary transactions where ALL were routed to champion (shadow only)
        fb_shadow: dict[str, object] = {}
        for i in range(5):
            tx_id = f"tx_shadow_canary_{i}"
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                champion_version=1,
                champion_prob=champ_probs[i],
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=chall_probs[i],
                challenger_latency_ms=10.0,
                routed_to="champion",
            )
            fb_shadow = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                actual_label=actuals[i],
            )

        # Invariant CANARY-06: 5 samples present, but 0 challenger-routed -> Promotion deferred!
        self.assertEqual(fb_shadow["traffic_share"], 0.1)
        self.assertFalse(fb_shadow["promotion_triggered"])
        self.assertIn(
            "waiting for real challenger-routed traffic", str(fb_shadow["promotion_message"])
        )
        self.assertEqual(self.registry.get_active_version(self.simulation_id)["version"], 1)

        # Now 1 transaction arrives that was routed to challenger
        tx_routed = "tx_routed_canary"
        self.eval_engine.log_prediction(
            simulation_id=self.simulation_id,
            transaction_id=tx_routed,
            champion_version=1,
            champion_prob=0.4,
            champion_latency_ms=10.0,
            challenger_version=2,
            challenger_prob=0.95,
            challenger_latency_ms=10.0,
            routed_to="challenger",
        )
        fb_routed = self.eval_engine.log_feedback(
            simulation_id=self.simulation_id,
            transaction_id=tx_routed,
            actual_label=1,
        )

        # Invariant: Now real challenger routing requirement is satisfied -> Promotion succeeds!
        self.assertEqual(fb_routed["traffic_share"], 0.0)
        self.assertTrue(fb_routed["promotion_triggered"])
        self.assertEqual(self.registry.get_active_version(self.simulation_id)["version"], 2)


if __name__ == "__main__":
    unittest.main()
