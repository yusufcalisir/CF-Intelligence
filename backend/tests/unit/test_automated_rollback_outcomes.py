"""Unit and Invariant Test Suite for Automated Rollback Outcomes.

Verifies invariants:
- ROLLBACK-OUTCOME-01: Auto-rollback commits and is truthfully reported when champion AUC is undefined and latency > 200ms.
- ROLLBACK-OUTCOME-02: Auto-rollback commits without metric formatting exceptions when champion AUC is undefined and FPR > 0.05.
- ROLLBACK-OUTCOME-03: Registry rollback failure before commit is truthfully reported as failed (no false success).
- ROLLBACK-OUTCOME-04: Post-commit reporting failures cannot undo or misrepresent a confirmed champion transition.
- ROLLBACK-OUTCOME-05: Indeterminate registry state after rollback is preserved truthfully rather than reporting false success.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import torch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.model_registry import (
    ModelEvaluationEngine,
    ModelIntegrityError,
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


class TestAutomatedRollbackOutcomes(unittest.TestCase):
    """Verifies that rollback outcomes are truthfully reported and never misclassified."""

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
        self.issuer_key_id = "test-issuer-ed25519-rb-outcome"
        self.trust_store.register_approved_key(
            self.issuer_key_id, pub_hex, admin_token=self.admin_token
        )

        self.verifier = TrainingReceiptVerifier(trust_store=self.trust_store)
        self.registry = ModelRegistry(storage_dir=self.reg_dir, verifier=self.verifier)
        self.eval_engine = ModelEvaluationEngine(self.registry)
        self.simulation_id = f"sim_rb_outcome_{self._testMethodName}"

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

        # Seed v1 (healthy stable champion)
        self.v1 = self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.90},
            is_promoted=True,
            status="champion",
        )
        # Seed v2 (new champion that will experience performance degradation)
        self.v2 = self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.92},
            is_promoted=True,
            status="champion",
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

    def test_rollback_committed_when_champion_auc_undefined_due_to_high_latency(self) -> None:
        """ROLLBACK-OUTCOME-01: Rollback commits and is truthfully reported when AUC is undefined and latency > 200ms."""
        # Active model is v2
        active_initial = self.registry.get_active_version(self.simulation_id)
        self.assertIsNotNone(active_initial)
        assert active_initial is not None
        self.assertEqual(active_initial["version"], 2)

        # 5 transactions with high latency (250ms > 200ms) and single-class labels (all 0s)
        # safe_roc_auc_score returns None for single-class labels
        res: dict[str, object] = {}
        for i in range(5):
            tx_id = f"tx_lat_{i}"
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                champion_version=2,
                champion_prob=0.1,
                champion_latency_ms=250.0,
            )
            res = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                actual_label=0,
            )

        # Invariant ROLLBACK-OUTCOME-01:
        # Rollback was committed and reported as True without TypeError
        self.assertTrue(res["rollback_triggered"])
        self.assertEqual(res["champion_version"], 1)
        self.assertIn("Auto-rollback triggered to version 1", str(res["rollback_message"]))
        self.assertIn("AUC: None", str(res["rollback_message"]))

        # Check durable persisted manifest on disk
        manifest = self.registry._load_manifest(self.simulation_id)
        v1_entry = next(e for e in manifest if e["version"] == 1)
        v2_entry = next(e for e in manifest if e["version"] == 2)
        self.assertTrue(v1_entry["is_active"])
        self.assertEqual(v1_entry["status"], "champion")
        self.assertFalse(v2_entry["is_active"])
        self.assertEqual(v2_entry["status"], "inactive")

        # Verify audit ledger recorded COMMITTED mutation
        audit_chain = ImmutableAuditChain.get_instance()
        events = audit_chain.query_events(target_id=f"{self.simulation_id}:v1")
        rb_events = [e for e in events if e.event_type == "MODEL_ROLLBACK"]
        self.assertTrue(len(rb_events) >= 1)
        self.assertEqual(rb_events[-1].target_id, f"{self.simulation_id}:v1")
        self.assertEqual(rb_events[-1].details.get("mutation_stage"), "COMMITTED")

    def test_rollback_committed_when_champion_auc_undefined_due_to_high_fpr(self) -> None:
        """ROLLBACK-OUTCOME-02: Rollback commits and formats safely when AUC is undefined and FPR > 0.05."""
        # 5 transactions with all negative class labels (actual_label = 0)
        # All predicted as positive (prob = 0.85 >= 0.5) -> FPR = 1.0 > 0.05
        # Since y_true has only class 0, champ_auc is None!
        res: dict[str, object] = {}
        for i in range(5):
            tx_id = f"tx_fpr_{i}"
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                champion_version=2,
                champion_prob=0.85,
                champion_latency_ms=15.0,
            )
            res = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                actual_label=0,
            )

        # Invariant ROLLBACK-OUTCOME-02:
        # No formatting exception raised, rollback committed truthfully
        self.assertTrue(res["rollback_triggered"])
        self.assertEqual(res["champion_version"], 1)
        self.assertIn("Auto-rollback triggered to version 1", str(res["rollback_message"]))
        self.assertIn("FPR: 1.0000", str(res["rollback_message"]))
        self.assertIn("AUC: None", str(res["rollback_message"]))

        # Verify registry manifest on disk
        active = self.registry.get_active_version(self.simulation_id)
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active["version"], 1)
        self.assertEqual(active["status"], "champion")

    def test_registry_rollback_failure_before_commit_truthfully_reported_as_failed(self) -> None:
        """ROLLBACK-OUTCOME-03: Registry rollback failure before commit reports failure truthfully with no false success."""
        # Mock self.registry.rollback to raise ModelIntegrityError during execution
        with patch.object(
            self.registry,
            "rollback",
            side_effect=ModelIntegrityError(
                "Rollback transaction commit aborted due to storage IO failure"
            ),
        ):
            res: dict[str, object] = {}
            for i in range(5):
                tx_id = f"tx_tamper_lat_{i}"
                self.eval_engine.log_prediction(
                    simulation_id=self.simulation_id,
                    transaction_id=tx_id,
                    champion_version=2,
                    champion_prob=0.1,
                    champion_latency_ms=300.0,
                )
                res = self.eval_engine.log_feedback(
                    simulation_id=self.simulation_id,
                    transaction_id=tx_id,
                    actual_label=0,
                )

        # Invariant ROLLBACK-OUTCOME-03:
        # Rollback was rejected due to error; MUST NOT report success!
        self.assertFalse(res["rollback_triggered"])
        self.assertEqual(res["champion_version"], 2)  # v2 is STILL the champion
        self.assertIn("failed admission/execution", str(res["rollback_message"]))

        # Durable manifest on disk MUST still have v2 as champion
        manifest_after = self.registry._load_manifest(self.simulation_id)
        v2_after = next(e for e in manifest_after if e["version"] == 2)
        v1_after = next(e for e in manifest_after if e["version"] == 1)
        self.assertTrue(v2_after["is_active"])
        self.assertEqual(v2_after["status"], "champion")
        self.assertFalse(v1_after["is_active"])

        # Audit ledger must NOT contain a committed MODEL_ROLLBACK event
        audit_chain = ImmutableAuditChain.get_instance()
        committed_rb = [
            e
            for e in audit_chain.query_events(target_id=f"{self.simulation_id}:v1")
            if e.event_type == "MODEL_ROLLBACK" and e.details.get("mutation_stage") == "COMMITTED"
        ]
        self.assertEqual(len(committed_rb), 0)

    def test_post_commit_reporting_failure_does_not_undo_or_misrepresent_confirmed_rollback(
        self,
    ) -> None:
        """ROLLBACK-OUTCOME-04: Nonessential reporting failure after commit cannot undo or misrepresent confirmed rollback."""
        # Trigger high latency degradation, but monkeypatch _simulation_events.push_list to raise
        with patch(
            "app.presentation.routers.simulation._simulation_events.push_list",
            side_effect=RuntimeError("Secondary broker failure"),
        ):
            res: dict[str, object] = {}
            for i in range(5):
                tx_id = f"tx_broker_fail_{i}"
                self.eval_engine.log_prediction(
                    simulation_id=self.simulation_id,
                    transaction_id=tx_id,
                    champion_version=2,
                    champion_prob=0.1,
                    champion_latency_ms=250.0,
                )
                res = self.eval_engine.log_feedback(
                    simulation_id=self.simulation_id,
                    transaction_id=tx_id,
                    actual_label=0,
                )

        # Invariant ROLLBACK-OUTCOME-04:
        # Rollback succeeded authoritatively; secondary notification failure does not corrupt outcome!
        self.assertTrue(res["rollback_triggered"])
        self.assertEqual(res["champion_version"], 1)

        # Authoritative state on disk confirms transition to v1
        active = self.registry.get_active_version(self.simulation_id)
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active["version"], 1)
        self.assertEqual(active["status"], "champion")

        # Audit chain confirms COMMITTED rollback event
        audit_chain = ImmutableAuditChain.get_instance()
        committed_rb = [
            e
            for e in audit_chain.query_events(target_id=f"{self.simulation_id}:v1")
            if e.event_type == "MODEL_ROLLBACK" and e.details.get("mutation_stage") == "COMMITTED"
        ]
        self.assertTrue(len(committed_rb) >= 1)

    def test_indeterminate_outcome_when_registry_state_cannot_be_verified(self) -> None:
        """ROLLBACK-OUTCOME-05: Indeterminate registry state after rollback reports unverified failure rather than false success."""
        real_get_active = self.registry.get_active_version
        rb_invoked = [False]
        real_rollback = self.registry.rollback

        def tracked_rollback(*args, **kwargs):
            res = real_rollback(*args, **kwargs)
            rb_invoked[0] = True
            return res

        def mock_get_active(sim_id):
            if rb_invoked[0]:
                return {"version": 999, "is_active": True}
            return real_get_active(sim_id)

        with (
            patch.object(self.registry, "rollback", side_effect=tracked_rollback),
            patch.object(self.registry, "get_active_version", side_effect=mock_get_active),
        ):
            res: dict[str, object] = {}
            for i in range(5):
                tx_id = f"tx_unverified_{i}"
                self.eval_engine.log_prediction(
                    simulation_id=self.simulation_id,
                    transaction_id=tx_id,
                    champion_version=2,
                    champion_prob=0.1,
                    champion_latency_ms=250.0,
                )
                res = self.eval_engine.log_feedback(
                    simulation_id=self.simulation_id,
                    transaction_id=tx_id,
                    actual_label=0,
                )

        # Invariant ROLLBACK-OUTCOME-05:
        # Since active version could not be verified as v1, rollback_triggered MUST be False
        self.assertFalse(res["rollback_triggered"])
        self.assertIn(
            "authoritative champion version is 999, expected 1", str(res["rollback_message"])
        )


if __name__ == "__main__":
    unittest.main()
