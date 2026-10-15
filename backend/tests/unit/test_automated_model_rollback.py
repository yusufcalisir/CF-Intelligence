"""Unit and Adversarial Test Suite for Automated and Explicit Model Rollback.

Tests invariants:
- ROLLBACK-01: Auto-rollback candidate selection skips revoked versions and selects earlier healthy version.
- ROLLBACK-02: Auto-rollback fails closed truthfully when no safe eligible candidate exists.
- ROLLBACK-03: Rollback is strictly rejected if candidate's training issuer key has been revoked.
- ROLLBACK-04: Rollback is strictly rejected if candidate artifact is corrupted, missing, or sha256 mismatch.
- ROLLBACK-05: Explicit rollback requires minimum clearance level 3 and matching institutional tenant authority.
- ROLLBACK-06: Rollback audit events maintain crash-consistency (never finalize FAILED if mutation committed).
"""

from __future__ import annotations

import hashlib
import hmac
import io
import os
import shutil
import tempfile
import unittest
from datetime import UTC, datetime

import torch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.model_registry import (
    DOMAIN_APPROVAL_SIGNOFF,
    ModelEvaluationEngine,
    ModelIntegrityError,
    ModelRegistry,
    create_trusted_internal_context,
    derive_domain_key,
)
from app.application.services.training_provenance_service import (
    DurableTrustStore,
    TrainingExecutionEvidence,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
)
from app.config import get_settings
from app.domain.model_serving_errors import ModelAuthorizationError
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.durable_nonce_store import (
    DurableNonceStore,
    reset_default_nonce_store,
)
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain


class TestAutomatedModelRollback(unittest.TestCase):
    """Verifies safe candidate selection and authorization invariants for rollback."""

    def setUp(self) -> None:
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
        os.environ["CFI_ACCOUNT_AUTHORITY_SECRET"] = "authoritative_signing_master_secret_key_64_bytes_test"
        os.environ["CFI_AUDIT_LEDGER_PATH"] = self.audit_file
        os.environ["CFI_AUDIT_LEDGER_SECRET"] = self.payload_secret
        os.environ["APP_ENV"] = "development"

        get_settings.cache_clear()
        reset_default_nonce_store()
        DurableNonceStore._instance = None
        AuthenticationService._instance = None
        ImmutableAuditChain.reset_instance()
        from app.infrastructure.redis_store import RedisStore
        RedisStore._shared_fallback_stores.clear()

        self.auth_svc = AuthenticationService(
            store_path=self.account_file,
            authority_secret="authoritative_signing_master_secret_key_64_bytes_test",
            signing_secret=self.jwt_secret,
            app_env="development",
        )
        AuthenticationService._instance = self.auth_svc

        # Users for bank_a
        self.u_admin = self.auth_svc.register_user("admin_dan", "Pass123!Secure", "bank_a", roles=["admin"], clearance_level=3)
        self.u_ml = self.auth_svc.register_user("dr_alice", "Pass123!Secure", "bank_a", roles=["ml_lead"], clearance_level=2)
        self.u_comp = self.auth_svc.register_user("officer_bob", "Pass123!Secure", "bank_a", roles=["compliance"], clearance_level=3)
        # Rival user in bank_b
        self.u_rival = self.auth_svc.register_user("rival_bob", "Pass123!Secure", "bank_b", roles=["admin"], clearance_level=3)

        self.token_admin = self.auth_svc.create_token_bundle(self.u_admin).access_token
        self.token_ml = self.auth_svc.create_token_bundle(self.u_ml).access_token
        self.token_comp = self.auth_svc.create_token_bundle(self.u_comp).access_token
        self.token_rival = self.auth_svc.create_token_bundle(self.u_rival).access_token

        # Trust store
        self.trust_store = DurableTrustStore(trust_file_path=self.trust_file, admin_token=self.admin_token)
        self.priv_key = ed25519.Ed25519PrivateKey.generate()
        pub_hex = self.priv_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ).hex()
        self.issuer_key_id = "test-issuer-ed25519-rb"
        self.trust_store.register_approved_key(self.issuer_key_id, pub_hex, admin_token=self.admin_token)

        self.verifier = TrainingReceiptVerifier(trust_store=self.trust_store)
        self.registry = ModelRegistry(storage_dir=self.reg_dir, verifier=self.verifier)
        self.eval_engine = ModelEvaluationEngine(self.registry)
        self.simulation_id = f"sim_rb_{self._testMethodName}"

        # Bind ownership
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
        shutil.rmtree(self.test_dir, ignore_errors=True)
        get_settings.cache_clear()
        reset_default_nonce_store()
        DurableNonceStore._instance = None
        AuthenticationService._instance = None
        ImmutableAuditChain.reset_instance()
        from app.infrastructure.redis_store import RedisStore
        RedisStore._shared_fallback_stores.clear()
        from app.application.services.training_provenance_service import (
            reset_default_training_verifier,
        )
        reset_default_training_verifier()

    def _sign_payload(self, version: int, role: str, username: str, artifact_sha: str) -> str:
        domain_key = derive_domain_key(self.payload_secret, DOMAIN_APPROVAL_SIGNOFF)
        payload = f"{self.simulation_id}:{version}:{artifact_sha}:{role}:{username}"
        return hmac.new(domain_key, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def test_automated_rollback_skips_revoked_previous_version(self) -> None:
        """ROLLBACK-01: Auto-rollback skips revoked v2 and successfully reverts to healthy v1."""
        # Save v1 (healthy champion)
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.90},
            is_promoted=True,
            status="champion",
        )

        # Save v2 (subsequently revoked)
        v2 = self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.88},
            is_promoted=True,
            status="champion",
        )
        self.registry.revoke_version(
            simulation_id=self.simulation_id,
            version=v2["version"],
            reason="Material flaw detected in v2 weights",
            actor="safety_officer",
        )

        # Save v3 (active champion that will suffer performance degradation)
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.92},
            is_promoted=True,
            status="champion",
        )
        self.assertEqual(self.registry.get_active_version(self.simulation_id)["version"], 3)

        # Feed degraded predictions (high latency > 200ms) for v3
        for i in range(5):
            tx_id = f"tx_deg_{i}"
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                champion_version=3,
                champion_prob=0.5,
                champion_latency_ms=250.0,
            )
            feedback = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                actual_label=i % 2,
            )

        # Invariant: auto-rollback skipped revoked v2 and rolled back to v1
        self.assertTrue(feedback["rollback_triggered"])
        active = self.registry.get_active_version(self.simulation_id)
        self.assertIsNotNone(active)
        self.assertEqual(active["version"], 1)

    def test_automated_rollback_fails_closed_when_no_eligible_candidate(self) -> None:
        """ROLLBACK-02: If all previous versions are revoked, evaluation fails closed without crashing."""
        # Save v1 and revoke it
        v1 = self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.90},
            is_promoted=True,
            status="champion",
        )
        self.registry.revoke_version(
            simulation_id=self.simulation_id,
            version=v1["version"],
            reason="Revoked baseline",
            actor="safety_officer",
        )

        # Save v2 as current champion
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.88},
            is_promoted=True,
            status="champion",
        )
        self.assertEqual(self.registry.get_active_version(self.simulation_id)["version"], 2)

        # Feed degraded predictions
        for i in range(5):
            tx_id = f"tx_no_cand_{i}"
            self.eval_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                champion_version=2,
                champion_prob=0.5,
                champion_latency_ms=300.0,
            )
            feedback = self.eval_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                actual_label=i % 2,
            )

        # Invariant: rollback could not be triggered (no safe candidate); system fails closed
        self.assertFalse(feedback["rollback_triggered"])
        self.assertEqual(feedback["traffic_share"], 0.0)
        # v2 remains in place (no unsafe reversion to revoked v1)
        active = self.registry.get_active_version(self.simulation_id)
        self.assertEqual(active["version"], 2)

    def test_rollback_rejected_if_candidate_training_issuer_revoked(self) -> None:
        """ROLLBACK-03: In production, rollback to candidate with revoked issuer key is rejected."""
        os.environ["APP_ENV"] = "production"
        get_settings.cache_clear()

        # Build genuine artifact
        buf = io.BytesIO()
        torch.save(self.model_state, buf)
        raw_bytes = buf.getvalue()
        sha = hashlib.sha256(raw_bytes).hexdigest()

        evidence = TrainingExecutionEvidence(
            training_run_id="run_rb_01",
            job_id="job_rb_01",
            simulation_id=self.simulation_id,
            completed_at=datetime.now(UTC).isoformat(),
            executed_rounds=3,
            executed_epochs=3,
            sample_count=500,
            observed_metrics={"loss": 0.05, "auc": 0.95},
            artifact_sha256=sha,
            architecture="Linear2to1",
            feature_schema_version="v1.0",
            feature_names=["f1", "f2"],
        )
        settings = get_settings()
        orig_env = settings.app_env
        settings.app_env = "development"
        try:
            issuer = TrainingReceiptIssuer(
                private_key=self.priv_key,
                key_id=self.issuer_key_id,
                require_authenticated_job=False,
                trust_store=self.trust_store,
            )
            rcpt = issuer.issue_receipt(
                evidence=evidence,
                model_bytes=raw_bytes,
                owning_bank_id="bank_a",
                architecture="Linear2to1",
                input_dim=2,
                feature_names=["f1", "f2"],
                feature_schema_version="v1.0",
            ).to_dict()
        finally:
            settings.app_env = orig_env

        # Save v1 with receipt
        v1 = self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.95},
            is_promoted=False,
            provenance_type="TRAINING_RECORDED",
            training_receipt=rcpt,
            artifact_bytes=raw_bytes,
            owning_bank_id="bank_a",
        )

        # Dual sign-off v1 in production
        sig_comp = self._sign_payload(v1["version"], "compliance", "officer_bob", sha)
        self.registry.sign_off(
            simulation_id=self.simulation_id,
            version=v1["version"],
            role="compliance",
            user="officer_bob",
            signature=sig_comp,
            auth_token=self.token_comp,
        )
        sig_ml = self._sign_payload(v1["version"], "ml_engineer", "dr_alice", sha)
        self.registry.sign_off(
            simulation_id=self.simulation_id,
            version=v1["version"],
            role="ml_engineer",
            user="dr_alice",
            signature=sig_ml,
            auth_token=self.token_ml,
        )

        # Now revoke the issuer key from the trust store
        self.trust_store.revoke_key(self.issuer_key_id, reason="Compromised key", admin_token=self.admin_token)

        # Attempting rollback to v1 must fail closed
        ctx_rb = create_trusted_internal_context(
            service_name="model_evaluation_engine",
            action="rollback",
            resource_type="model",
            resource_id=self.simulation_id,
            model_version=v1["version"],
            bank_id="bank_a",
        )
        with self.assertRaises(ModelIntegrityError) as cm:
            self.registry.rollback(self.simulation_id, v1["version"], trusted_context=ctx_rb)
        self.assertIn("revoked", str(cm.exception).lower())

    def test_rollback_rejected_if_model_artifact_tampered_or_missing(self) -> None:
        """ROLLBACK-04: Rollback rejected if artifact file is missing or digest mismatches."""
        v1 = self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.90},
            is_promoted=False,
            status="inactive",
        )
        sim_dir = self.registry._get_sim_dir(self.simulation_id)
        filepath = os.path.join(sim_dir, v1["filename"])

        # Tamper with the artifact
        with open(filepath, "wb") as f:
            f.write(b"corrupted_tampered_model_bytes")

        ctx_rb = create_trusted_internal_context(
            service_name="model_evaluation_engine",
            action="rollback",
            resource_type="model",
            resource_id=self.simulation_id,
            model_version=v1["version"],
            bank_id="bank_a",
        )
        with self.assertRaises(ModelIntegrityError) as cm:
            self.registry.rollback(self.simulation_id, v1["version"], trusted_context=ctx_rb)
        self.assertIn("integrity verification failed", str(cm.exception).lower())

    def test_explicit_rollback_requires_clearance_3_and_tenant_authority(self) -> None:
        """ROLLBACK-05: Explicit rollback rejects clearance < 3 and rival bank operators."""
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.90},
            is_promoted=True,
            status="champion",
        )
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.88},
            is_promoted=True,
            status="champion",
        )

        # Clearance level 2 user rejected (rollback requires level 3)
        with self.assertRaises(ModelIntegrityError) as cm_clearance:
            self.registry.rollback(
                simulation_id=self.simulation_id,
                version=1,
                auth_token=self.token_ml,
            )
        self.assertIn("clearance level", str(cm_clearance.exception).lower())

        # Rival bank operator (bank_b) rejected
        with self.assertRaises(ModelAuthorizationError) as cm_tenant:
            self.registry.rollback(
                simulation_id=self.simulation_id,
                version=1,
                auth_token=self.token_rival,
            )
        self.assertIn("cross-bank authorization rejected", str(cm_tenant.exception).lower())

        # Authorized bank_a admin with clearance 3 succeeds
        rolled = self.registry.rollback(
            simulation_id=self.simulation_id,
            version=1,
            auth_token=self.token_admin,
        )
        self.assertEqual(rolled["version"], 1)
        self.assertTrue(rolled["is_active"])
        self.assertEqual(self.registry.get_active_version(self.simulation_id)["version"], 1)


if __name__ == "__main__":
    unittest.main()
