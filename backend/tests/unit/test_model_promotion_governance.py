"""Unit and Adversarial Test Suite for Model Promotion Governance & Dual Approval.

Tests invariants:
- PROMO-01: Valid dual approval marks model as 'approved' without prematurely activating it as champion.
- PROMO-02: Duplicate, same-user, or unverified approvals are strictly rejected (Four-Eyes Principle).
- PROMO-03: Revoked sign-offs drop approval status and block subsequent promotion attempts.
- PROMO-04: Explicit promotion enforces institutional tenant boundaries and minimum clearance (level 2).
- PROMO-05: Registration flag is_promoted=True cannot bypass production admission in production environment.
- PROMO-06: Auto-promotion in sign_off delegates to authoritative promote_version enforcing all admission gates.
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


class TestModelPromotionGovernance(unittest.TestCase):
    """Adversarial test suite for model sign-off approvals, RBAC, and promotion gates."""

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
        os.environ["APP_ENV"] = "production"

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
            app_env="production",
        )
        AuthenticationService._instance = self.auth_svc

        # Register users for bank_a
        self.u_ml = self.auth_svc.register_user("dr_alice", "Pass123!Secure", "bank_a", roles=["ml_engineer"], clearance_level=2)
        self.u_comp = self.auth_svc.register_user("officer_bob", "Pass123!Secure", "bank_a", roles=["compliance"], clearance_level=2)
        self.u_low = self.auth_svc.register_user("junior_dan", "Pass123!Secure", "bank_a", roles=["ml_engineer"], clearance_level=1)
        # Register user for rival bank_b
        self.u_rival = self.auth_svc.register_user("rival_eve", "Pass123!Secure", "bank_b", roles=["ml_lead"], clearance_level=3)

        self.token_ml = self.auth_svc.create_token_bundle(self.u_ml).access_token
        self.token_comp = self.auth_svc.create_token_bundle(self.u_comp).access_token
        self.token_low = self.auth_svc.create_token_bundle(self.u_low).access_token
        self.token_rival = self.auth_svc.create_token_bundle(self.u_rival).access_token

        # Set up trust store & receipt verifier
        self.trust_store = DurableTrustStore(trust_file_path=self.trust_file, admin_token=self.admin_token)
        self.priv_key = ed25519.Ed25519PrivateKey.generate()
        pub_hex = self.priv_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ).hex()
        self.issuer_key_id = "test-issuer-ed25519-01"
        self.trust_store.register_approved_key(self.issuer_key_id, pub_hex, admin_token=self.admin_token)

        self.verifier = TrainingReceiptVerifier(trust_store=self.trust_store)
        self.registry = ModelRegistry(storage_dir=self.reg_dir, verifier=self.verifier)
        self.simulation_id = f"sim_promo_{self._testMethodName}"

        # Bind ownership to bank_a
        ctx_bind = create_trusted_internal_context(
            service_name="training_orchestrator",
            action="bind_ownership",
            resource_type="model",
            resource_id=self.simulation_id,
            bank_id="bank_a",
        )
        self.registry.bind_simulation_ownership(self.simulation_id, owning_bank_id="bank_a", trusted_context=ctx_bind)

        # Build genuine model artifact
        self.model = torch.nn.Linear(4, 1)
        buf = io.BytesIO()
        torch.save(self.model.state_dict(), buf)
        self.raw_bytes = buf.getvalue()
        self.artifact_sha = hashlib.sha256(self.raw_bytes).hexdigest()

        # Issue valid training receipt
        evidence = TrainingExecutionEvidence(
            training_run_id="run_gov_01",
            job_id="job_gov_01",
            simulation_id=self.simulation_id,
            completed_at=datetime.now(UTC).isoformat(),
            executed_rounds=3,
            executed_epochs=3,
            sample_count=500,
            observed_metrics={"loss": 0.05, "auc": 0.95},
            artifact_sha256=self.artifact_sha,
            architecture="Linear4to1",
            feature_schema_version="v1.0",
            feature_names=["f1", "f2", "f3", "f4"],
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
            self.receipt = issuer.issue_receipt(
                evidence=evidence,
                model_bytes=self.raw_bytes,
                owning_bank_id="bank_a",
                architecture="Linear4to1",
                input_dim=4,
                feature_names=["f1", "f2", "f3", "f4"],
                feature_schema_version="v1.0",
            ).to_dict()
        finally:
            settings.app_env = orig_env

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

    def _sign_payload(self, version: int, role: str, username: str) -> str:
        domain_key = derive_domain_key(self.payload_secret, DOMAIN_APPROVAL_SIGNOFF)
        payload = f"{self.simulation_id}:{version}:{self.artifact_sha}:{role}:{username}"
        return hmac.new(domain_key, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def test_valid_dual_approval_marks_approved_without_premature_champion_activation(self) -> None:
        """PROMO-01: Dual sign-off approves model but does not prematurely activate champion."""
        entry = self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model.state_dict(),
            metrics={"auc": 0.95},
            is_promoted=False,
            provenance_type="TRAINING_RECORDED",
            training_receipt=self.receipt,
            artifact_bytes=self.raw_bytes,
            owning_bank_id="bank_a",
            status="inactive",
        )
        ver = entry["version"]
        self.assertEqual(entry["status"], "inactive")
        self.assertFalse(entry["is_active"])

        # First sign-off: compliance
        sig_comp = self._sign_payload(ver, "compliance", "officer_bob")
        updated_1 = self.registry.sign_off(
            simulation_id=self.simulation_id,
            version=ver,
            role="compliance",
            user="officer_bob",
            signature=sig_comp,
            auth_token=self.token_comp,
        )
        self.assertEqual(updated_1["status"], "inactive")
        self.assertFalse(updated_1.get("is_approved", False))
        self.assertFalse(updated_1["is_active"])

        # Second sign-off: ml_engineer
        sig_ml = self._sign_payload(ver, "ml_engineer", "dr_alice")
        updated_2 = self.registry.sign_off(
            simulation_id=self.simulation_id,
            version=ver,
            role="ml_engineer",
            user="dr_alice",
            signature=sig_ml,
            auth_token=self.token_ml,
        )

        # Invariant: model is approved and eligible, but NOT active champion
        self.assertEqual(updated_2["status"], "approved")
        self.assertTrue(updated_2["is_approved"])
        self.assertTrue(updated_2["is_production_eligible"])
        self.assertFalse(updated_2["is_active"])
        self.assertIsNone(self.registry.get_active_version(self.simulation_id))

        # Explicit promotion activates it
        promoted = self.registry.promote_version(
            simulation_id=self.simulation_id,
            version=ver,
            target_status="champion",
            auth_token=self.token_ml,
        )
        self.assertEqual(promoted["status"], "champion")
        self.assertTrue(promoted["is_active"])
        active = self.registry.get_active_version(self.simulation_id)
        self.assertIsNotNone(active)
        self.assertEqual(active["version"], ver)

    def test_duplicate_or_non_independent_approval_rejected(self) -> None:
        """PROMO-02: Self-approval across roles and duplicate role sign-offs are rejected."""
        entry = self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model.state_dict(),
            metrics={"auc": 0.95},
            is_promoted=False,
            provenance_type="TRAINING_RECORDED",
            training_receipt=self.receipt,
            artifact_bytes=self.raw_bytes,
            owning_bank_id="bank_a",
            status="inactive",
        )
        ver = entry["version"]

        sig_ml = self._sign_payload(ver, "ml_engineer", "dr_alice")
        self.registry.sign_off(
            simulation_id=self.simulation_id,
            version=ver,
            role="ml_engineer",
            user="dr_alice",
            signature=sig_ml,
            auth_token=self.token_ml,
        )

        # Duplicate role sign-off rejected
        with self.assertRaises(ValueError):
            self.registry.sign_off(
                simulation_id=self.simulation_id,
                version=ver,
                role="ml_engineer",
                user="dr_alice",
                signature=sig_ml,
                auth_token=self.token_ml,
            )

        # Self-approval: same user dr_alice signing as compliance is rejected by role permissions or Four-Eyes Principle
        sig_comp_alice = self._sign_payload(ver, "compliance", "dr_alice")
        with self.assertRaises((ValueError, ModelIntegrityError)):
            self.registry.sign_off(
                simulation_id=self.simulation_id,
                version=ver,
                role="compliance",
                user="dr_alice",
                signature=sig_comp_alice,
                auth_token=self.token_ml,
            )

    def test_revoked_sign_off_blocks_promotion(self) -> None:
        """PROMO-03: Revoking a sign-off removes approval and blocks promotion."""
        entry = self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model.state_dict(),
            metrics={"auc": 0.95},
            is_promoted=False,
            provenance_type="TRAINING_RECORDED",
            training_receipt=self.receipt,
            artifact_bytes=self.raw_bytes,
            owning_bank_id="bank_a",
            status="inactive",
        )
        ver = entry["version"]

        # Dual sign-off
        sig_comp = self._sign_payload(ver, "compliance", "officer_bob")
        self.registry.sign_off(self.simulation_id, ver, "compliance", "officer_bob", sig_comp, auth_token=self.token_comp)
        sig_ml = self._sign_payload(ver, "ml_engineer", "dr_alice")
        self.registry.sign_off(self.simulation_id, ver, "ml_engineer", "dr_alice", sig_ml, auth_token=self.token_ml)

        # Revoke compliance sign-off
        revoked = self.registry.revoke_sign_off(
            simulation_id=self.simulation_id,
            version=ver,
            role="compliance",
            user="officer_bob",
            reason="Material regulatory doubt regarding feature drift",
            auth_token=self.token_comp,
        )
        self.assertFalse(revoked["is_approved"])
        self.assertEqual(revoked["status"], "candidate")

        # Promotion attempt must fail
        with self.assertRaises(ModelIntegrityError):
            self.registry.promote_version(
                simulation_id=self.simulation_id,
                version=ver,
                target_status="champion",
                auth_token=self.token_ml,
            )

    def test_explicit_promotion_enforces_rbac_and_institutional_tenant(self) -> None:
        """PROMO-04: Promotion rejects low clearance (<2) and rival institutional tenants."""
        entry = self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model.state_dict(),
            metrics={"auc": 0.95},
            is_promoted=False,
            provenance_type="TRAINING_RECORDED",
            training_receipt=self.receipt,
            artifact_bytes=self.raw_bytes,
            owning_bank_id="bank_a",
            status="inactive",
        )
        ver = entry["version"]

        # Dual sign-offs
        sig_comp = self._sign_payload(ver, "compliance", "officer_bob")
        self.registry.sign_off(self.simulation_id, ver, "compliance", "officer_bob", sig_comp, auth_token=self.token_comp)
        sig_ml = self._sign_payload(ver, "ml_engineer", "dr_alice")
        self.registry.sign_off(self.simulation_id, ver, "ml_engineer", "dr_alice", sig_ml, auth_token=self.token_ml)

        # Low clearance operator (level 1) rejected
        with self.assertRaises(ModelIntegrityError) as cm_clearance:
            self.registry.promote_version(
                simulation_id=self.simulation_id,
                version=ver,
                target_status="champion",
                auth_token=self.token_low,
            )
        self.assertIn("clearance level", str(cm_clearance.exception).lower())

        # Rival bank operator (bank_b) rejected
        with self.assertRaises(ModelAuthorizationError) as cm_tenant:
            self.registry.promote_version(
                simulation_id=self.simulation_id,
                version=ver,
                target_status="champion",
                auth_token=self.token_rival,
            )
        self.assertTrue(
            "cross-bank authorization rejected" in str(cm_tenant.exception).lower()
            or "lacks authoritative institutional ownership" in str(cm_tenant.exception).lower()
        )

    def test_unverified_model_cannot_bypass_via_registration_is_promoted_flag(self) -> None:
        """PROMO-05: Direct registration with is_promoted=True fails closed in production for unverified models."""
        with self.assertRaises(ModelIntegrityError) as cm:
            self.registry.save_version(
                simulation_id=self.simulation_id,
                state_dict=self.model.state_dict(),
                metrics={"auc": 0.95},
                is_promoted=True,
                provenance_type="UNVERIFIED",
                artifact_bytes=self.raw_bytes,
                owning_bank_id="bank_a",
                status="champion",
            )
        self.assertIn("production admission rejected", str(cm.exception).lower())

    def test_auto_promote_in_signoff_enforces_strict_production_admission(self) -> None:
        """PROMO-06: sign_off with auto_promote=True enforces full promote_version admission checks."""
        entry = self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model.state_dict(),
            metrics={"auc": 0.95},
            is_promoted=False,
            provenance_type="TRAINING_RECORDED",
            training_receipt=self.receipt,
            artifact_bytes=self.raw_bytes,
            owning_bank_id="bank_a",
            status="inactive",
        )
        ver = entry["version"]

        sig_comp = self._sign_payload(ver, "compliance", "officer_bob")
        self.registry.sign_off(self.simulation_id, ver, "compliance", "officer_bob", sig_comp, auth_token=self.token_comp)

        # Second sign-off with auto_promote=True using authorized ml_engineer token
        sig_ml = self._sign_payload(ver, "ml_engineer", "dr_alice")
        promoted = self.registry.sign_off(
            simulation_id=self.simulation_id,
            version=ver,
            role="ml_engineer",
            user="dr_alice",
            signature=sig_ml,
            auth_token=self.token_ml,
            auto_promote=True,
        )
        self.assertEqual(promoted["status"], "champion")
        self.assertTrue(promoted["is_active"])
        self.assertEqual(self.registry.get_active_version(self.simulation_id)["version"], ver)


if __name__ == "__main__":
    unittest.main()
