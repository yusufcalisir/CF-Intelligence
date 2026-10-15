"""Unit and Adversarial Test Suite for Automated Governance Action Audit Attribution.

Verifies invariants:
- AUDIT-01: Automated promotion derives actor_institution from verified trusted context (never 'unknown').
- AUDIT-02: Automated rollback derives actor_institution from verified trusted context (never 'unknown').
- AUDIT-03: Human-initiated actions preserve authoritative human principal and institution.
- AUDIT-04: Mismatched institutional context is strictly rejected with ModelAuthorizationError.
- AUDIT-05: PENDING and COMMITTED audit events maintain exact mutation_id, actor, and institutional continuity.
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
from app.domain.model_serving_errors import ModelAuthorizationError
from app.infrastructure.redis_store import RedisStore
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.durable_nonce_store import (
    DurableNonceStore,
    reset_default_nonce_store,
)
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain


class TestAutomatedGovernanceAuditAttribution(unittest.TestCase):
    """Verifies that automated model promotions and rollbacks maintain truthful institutional attribution in audit ledgers."""

    def setUp(self) -> None:
        self.env_keys = [
            "CFI_INTERNAL_CONTEXT_SECRET",
            "CFI_INTERNAL_NONCE_STORE_PATH",
            "CFI_MODEL_REGISTRY_DIR",
            "CFI_AUDIT_LEDGER_PATH",
            "CFI_AUDIT_LEDGER_SECRET",
            "CFI_ACCOUNT_STORE_PATH",
            "CFI_ACCOUNT_AUTHORITY_SECRET",
            "CFI_JWT_SIGNING_SECRET",
            "APP_ENV",
        ]
        self._orig_env = {k: os.environ.get(k) for k in self.env_keys}
        self.test_dir = tempfile.mkdtemp()
        self.nonce_file = os.path.join(self.test_dir, "nonces.json")
        self.audit_file = os.path.join(self.test_dir, "audit.json")
        self.account_file = os.path.join(self.test_dir, "accounts.json")
        self.reg_dir = os.path.join(self.test_dir, "models")
        os.makedirs(self.reg_dir, exist_ok=True)

        self.payload_secret = "audit_attribution_secret_high_entropy_32bytes"
        self.jwt_secret = "jwt_signing_secret_high_entropy_32bytes_test"
        os.environ["CFI_INTERNAL_CONTEXT_SECRET"] = self.payload_secret
        os.environ["CFI_INTERNAL_NONCE_STORE_PATH"] = self.nonce_file
        os.environ["CFI_MODEL_REGISTRY_DIR"] = self.reg_dir
        os.environ["CFI_AUDIT_LEDGER_PATH"] = self.audit_file
        os.environ["CFI_AUDIT_LEDGER_SECRET"] = self.payload_secret
        os.environ["CFI_ACCOUNT_STORE_PATH"] = self.account_file
        os.environ["CFI_ACCOUNT_AUTHORITY_SECRET"] = "authoritative_signing_master_secret_key_64_bytes_test"
        os.environ["CFI_JWT_SIGNING_SECRET"] = self.jwt_secret
        os.environ["APP_ENV"] = "development"

        get_settings.cache_clear()
        reset_default_nonce_store()
        DurableNonceStore._instance = None
        AuthenticationService._instance = None
        ImmutableAuditChain.reset_instance()
        RedisStore._shared_fallback_stores.clear()

        self.auth_svc = AuthenticationService(
            store_path=self.account_file,
            authority_secret="authoritative_signing_master_secret_key_64_bytes_test",
            signing_secret=self.jwt_secret,
            app_env="development",
        )
        AuthenticationService._instance = self.auth_svc

        # Register users for bank_a and bank_b
        self.u_admin_a = self.auth_svc.register_user(
            "admin_alice", "Pass123!Secure", "bank_a", roles=["admin", "ml_lead"], clearance_level=3
        )
        self.token_admin_a = self.auth_svc.create_token_bundle(self.u_admin_a).access_token

        self.registry = ModelRegistry(storage_dir=self.reg_dir)
        self.eval_engine = ModelEvaluationEngine(self.registry)
        self.simulation_id = f"sim_audit_{self._testMethodName}"

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
        AuthenticationService._instance = None
        ImmutableAuditChain.reset_instance()
        RedisStore._shared_fallback_stores.clear()

    def test_automated_promotion_records_authenticated_service_and_institution(self) -> None:
        """AUDIT-01: Automated promotion derives actor_institution='bank_a' from trusted context (never 'unknown')."""
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

        trusted_ctx = create_trusted_internal_context(
            service_name="model_evaluation_engine",
            action="promote_version",
            resource_type="model",
            resource_id=self.simulation_id,
            model_version=v2["version"],
            bank_id="bank_a",
        )

        # Execute automated promotion
        self.registry.promote_version(
            simulation_id=self.simulation_id,
            version=v2["version"],
            target_status="champion",
            trusted_context=trusted_ctx,
        )

        # Inspect immutable audit trail
        audit = ImmutableAuditChain.get_instance()
        self.assertTrue(audit.verify_chain_integrity())
        events = audit.get_snapshot()

        promo_pending = next((e for e in events if e.event_type == "MODEL_PROMOTION_PENDING"), None)
        promo_committed = next((e for e in events if e.event_type == "MODEL_PROMOTION"), None)

        self.assertIsNotNone(promo_pending)
        self.assertIsNotNone(promo_committed)

        # Invariant 1: Actor must be the internal service
        self.assertEqual(promo_pending.actor, "internal:model_evaluation_engine")
        self.assertEqual(promo_committed.actor, "internal:model_evaluation_engine")

        # Invariant 2: Institution must be 'bank_a' (NOT 'unknown')
        self.assertEqual(promo_pending.actor_institution, "bank_a")
        self.assertEqual(promo_committed.actor_institution, "bank_a")
        self.assertEqual(promo_pending.details.get("actor_institution"), "bank_a")
        self.assertEqual(promo_committed.details.get("actor_institution"), "bank_a")

        # Invariant 3: Exact mutation continuity
        self.assertEqual(promo_pending.details["mutation_id"], promo_committed.details["mutation_id"])
        self.assertEqual(promo_pending.target_id, f"{self.simulation_id}:v2")
        self.assertEqual(promo_committed.target_id, f"{self.simulation_id}:v2")

    def test_automated_rollback_records_authenticated_service_and_institution(self) -> None:
        """AUDIT-02: Automated rollback derives actor_institution='bank_a' from trusted context."""
        v1 = self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.model_state,
            metrics={"auc": 0.85},
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

        trusted_ctx = create_trusted_internal_context(
            service_name="model_evaluation_engine",
            action="rollback",
            resource_type="model",
            resource_id=self.simulation_id,
            model_version=v1["version"],
            bank_id="bank_a",
        )

        # Execute rollback to v1
        self.registry.rollback(
            simulation_id=self.simulation_id,
            version=v1["version"],
            trusted_context=trusted_ctx,
        )

        audit = ImmutableAuditChain.get_instance()
        self.assertTrue(audit.verify_chain_integrity())
        events = audit.get_snapshot()

        rb_pending = next((e for e in events if e.event_type == "MODEL_ROLLBACK_PENDING"), None)
        rb_committed = next((e for e in events if e.event_type == "MODEL_ROLLBACK"), None)

        self.assertIsNotNone(rb_pending)
        self.assertIsNotNone(rb_committed)

        self.assertEqual(rb_pending.actor, "internal:model_evaluation_engine")
        self.assertEqual(rb_committed.actor, "internal:model_evaluation_engine")

        # Institution must be 'bank_a'
        self.assertEqual(rb_pending.actor_institution, "bank_a")
        self.assertEqual(rb_committed.actor_institution, "bank_a")
        self.assertEqual(rb_pending.details.get("actor_institution"), "bank_a")
        self.assertEqual(rb_committed.details.get("actor_institution"), "bank_a")

        self.assertEqual(rb_pending.details["mutation_id"], rb_committed.details["mutation_id"])
        self.assertEqual(rb_pending.target_id, f"{self.simulation_id}:v1")

    def test_human_initiated_governance_preserves_human_principal_and_institution(self) -> None:
        """AUDIT-03: Human-initiated promotion preserves authoritative human principal and institution."""
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

        # Human operator promotion with Bearer token
        self.registry.promote_version(
            simulation_id=self.simulation_id,
            version=v2["version"],
            target_status="champion",
            auth_token=self.token_admin_a,
        )

        audit = ImmutableAuditChain.get_instance()
        events = audit.get_snapshot()

        promo_committed = next((e for e in events if e.event_type == "MODEL_PROMOTION"), None)
        self.assertIsNotNone(promo_committed)

        self.assertEqual(promo_committed.actor, "admin_alice")
        self.assertEqual(promo_committed.actor_institution, "bank_a")
        self.assertEqual(promo_committed.details.get("actor_institution"), "bank_a")

    def test_mismatched_institutional_context_strictly_rejected(self) -> None:
        """AUDIT-04: Trusted context claiming bank_b cannot promote a model owned by bank_a."""
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

        # Rogue context with mismatched bank_b
        rogue_ctx = create_trusted_internal_context(
            service_name="model_evaluation_engine",
            action="promote_version",
            resource_type="model",
            resource_id=self.simulation_id,
            model_version=v2["version"],
            bank_id="bank_b",  # Model belongs to bank_a!
        )

        from app.domain.model_serving_errors import ModelIntegrityError
        with self.assertRaises((ModelAuthorizationError, ModelIntegrityError)) as cm:
            self.registry.promote_version(
                simulation_id=self.simulation_id,
                version=v2["version"],
                target_status="champion",
                trusted_context=rogue_ctx,
            )

        self.assertTrue(
            "Cross-tenant authorization violation" in str(cm.exception)
            or "Context bank_id scope mismatch" in str(cm.exception)
        )


        # Invariant: No committed MODEL_PROMOTION event should exist
        audit = ImmutableAuditChain.get_instance()
        events = audit.get_snapshot()
        promo_committed = [e for e in events if e.event_type == "MODEL_PROMOTION"]
        self.assertEqual(len(promo_committed), 0)
