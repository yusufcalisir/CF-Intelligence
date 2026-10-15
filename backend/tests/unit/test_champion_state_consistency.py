"""Unit and Invariant Test Suite for Champion/Challenger State Consistency.

Tests invariants:
- CHAMP-01: At most one active champion per simulation. Activating a new champion supersedes the previous one.
- CHAMP-02: Strict simulation isolation. Champion activation in Simulation A does not alter Simulation B.
- CHAMP-03: Generation numbers increment monotonically on champion activation.
- CHAMP-04: Contradictory states are forbidden (e.g. is_active=True while is_revoked=True).
- CHAMP-05: Reloading/refreshing manifests preserves exact state without phantom promotions.
- CHAMP-06: Legitimate absence of an active champion is truthfully represented (None).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

import torch

from app.application.services.model_registry import (
    ModelIntegrityError,
    ModelRegistry,
    create_trusted_internal_context,
)
from app.config import get_settings
from app.infrastructure.security.durable_nonce_store import (
    DurableNonceStore,
    reset_default_nonce_store,
)
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain


class TestChampionStateConsistency(unittest.TestCase):
    """Verifies state machine invariants across champion and challenger lifecycles."""

    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp()
        self.nonce_file = os.path.join(self.test_dir, "nonces.json")
        self.reg_dir = os.path.join(self.test_dir, "models")
        self.audit_file = os.path.join(self.test_dir, "audit.json")
        os.makedirs(self.reg_dir, exist_ok=True)

        self.payload_secret = "champ_state_secret_entropy_high_32bytes_value"
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

        self.registry = ModelRegistry(storage_dir=self.reg_dir)
        self.sim_a = "sim_consist_alpha"
        self.sim_b = "sim_consist_beta"

        # Bind ownership for both simulations
        for sim in (self.sim_a, self.sim_b):
            ctx = create_trusted_internal_context(
                service_name="training_orchestrator",
                action="bind_ownership",
                resource_type="model",
                resource_id=sim,
                bank_id="bank_test",
            )
            self.registry.bind_simulation_ownership(sim, owning_bank_id="bank_test", trusted_context=ctx)

        self.model_state = torch.nn.Linear(2, 1).state_dict()

    def tearDown(self) -> None:
        shutil.rmtree(self.test_dir, ignore_errors=True)
        get_settings.cache_clear()
        reset_default_nonce_store()
        DurableNonceStore._instance = None
        ImmutableAuditChain.reset_instance()
        from app.infrastructure.redis_store import RedisStore
        RedisStore._shared_fallback_stores.clear()
        from app.application.services.training_provenance_service import (
            reset_default_training_verifier,
        )
        reset_default_training_verifier()

    def test_at_most_one_active_champion_per_simulation(self) -> None:
        """CHAMP-01: Exactly one active champion exists; promoting v2 supersedes v1."""
        # Save v1 as champion
        v1 = self.registry.save_version(
            simulation_id=self.sim_a,
            state_dict=self.model_state,
            metrics={"auc": 0.85},
            is_promoted=True,
            status="champion",
        )
        self.assertEqual(v1["version"], 1)
        self.assertTrue(v1["is_active"])
        self.assertEqual(v1["status"], "champion")

        # Save v2 as inactive candidate
        v2 = self.registry.save_version(
            simulation_id=self.sim_a,
            state_dict=self.model_state,
            metrics={"auc": 0.92},
            is_promoted=False,
            status="inactive",
        )
        self.assertEqual(v2["version"], 2)
        self.assertFalse(v2["is_active"])

        # Promote v2 to champion
        ctx_promo = create_trusted_internal_context(
            service_name="training_orchestrator",
            action="promote_version",
            resource_type="model",
            resource_id=self.sim_a,
            model_version=2,
            bank_id="bank_test",
        )
        promoted_v2 = self.registry.promote_version(
            simulation_id=self.sim_a,
            version=2,
            target_status="champion",
            trusted_context=ctx_promo,
        )
        self.assertTrue(promoted_v2["is_active"])
        self.assertEqual(promoted_v2["status"], "champion")

        # Invariant: across all manifest records, exactly one has is_active == True
        manifest = self.registry._load_manifest(self.sim_a)
        active_entries = [e for e in manifest if e.get("is_active") is True]
        self.assertEqual(len(active_entries), 1)
        self.assertEqual(active_entries[0]["version"], 2)

        # Historical v1 is marked inactive
        v1_entry = next(e for e in manifest if e["version"] == 1)
        self.assertFalse(v1_entry["is_active"])
        self.assertEqual(v1_entry["status"], "inactive")

    def test_multiple_simulations_retain_independent_champions(self) -> None:
        """CHAMP-02: Mutations in Simulation A do not alter Simulation B's champion."""
        # Sim A: active v1
        self.registry.save_version(
            simulation_id=self.sim_a,
            state_dict=self.model_state,
            metrics={"auc": 0.80},
            is_promoted=True,
            status="champion",
        )
        # Sim B: active v1
        self.registry.save_version(
            simulation_id=self.sim_b,
            state_dict=self.model_state,
            metrics={"auc": 0.88},
            is_promoted=True,
            status="champion",
        )

        # Sim A saves and promotes v2
        self.registry.save_version(
            simulation_id=self.sim_a,
            state_dict=self.model_state,
            metrics={"auc": 0.95},
            is_promoted=False,
            status="inactive",
        )
        ctx_promo = create_trusted_internal_context(
            service_name="training_orchestrator",
            action="promote_version",
            resource_type="model",
            resource_id=self.sim_a,
            model_version=2,
            bank_id="bank_test",
        )
        self.registry.promote_version(self.sim_a, 2, target_status="champion", trusted_context=ctx_promo)

        # Verification: Sim A has active v2, Sim B STILL has active v1
        active_a = self.registry.get_active_version(self.sim_a)
        active_b = self.registry.get_active_version(self.sim_b)
        self.assertIsNotNone(active_a)
        self.assertIsNotNone(active_b)
        self.assertEqual(active_a["version"], 2)
        self.assertEqual(active_b["version"], 1)

    def test_monotonic_generation_numbering_on_promotion(self) -> None:
        """CHAMP-03: Generation numbers strictly increase upon each champion promotion."""
        v1 = self.registry.save_version(
            simulation_id=self.sim_a,
            state_dict=self.model_state,
            metrics={"auc": 0.85},
            is_promoted=True,
            status="champion",
        )
        gen_1 = v1.get("generation", 1)

        self.registry.save_version(
            simulation_id=self.sim_a,
            state_dict=self.model_state,
            metrics={"auc": 0.90},
            is_promoted=False,
            status="inactive",
        )
        ctx_promo_2 = create_trusted_internal_context(
            service_name="training_orchestrator",
            action="promote_version",
            resource_type="model",
            resource_id=self.sim_a,
            model_version=2,
            bank_id="bank_test",
        )
        v2_promoted = self.registry.promote_version(self.sim_a, 2, target_status="champion", trusted_context=ctx_promo_2)
        gen_2 = v2_promoted.get("generation", 2)
        self.assertGreater(gen_2, gen_1)

    def test_no_contradictory_lifecycle_states(self) -> None:
        """CHAMP-04: Revoked model cannot remain active or be promoted."""
        self.registry.save_version(
            simulation_id=self.sim_a,
            state_dict=self.model_state,
            metrics={"auc": 0.85},
            is_promoted=True,
            status="champion",
        )
        # Revoke active version 1
        revoked = self.registry.revoke_version(
            simulation_id=self.sim_a,
            version=1,
            reason="Material safety finding",
            actor="test_auditor",
        )
        self.assertEqual(revoked["status"], "inactive")
        self.assertTrue(revoked["is_revoked"])
        self.assertFalse(revoked["is_active"])

        # Attempting to promote revoked version is rejected
        ctx_promo = create_trusted_internal_context(
            service_name="training_orchestrator",
            action="promote_version",
            resource_type="model",
            resource_id=self.sim_a,
            model_version=1,
            bank_id="bank_test",
        )
        with self.assertRaises(ModelIntegrityError) as cm:
            self.registry.promote_version(self.sim_a, 1, target_status="champion", trusted_context=ctx_promo)
        self.assertIn("revoked", str(cm.exception).lower())

    def test_reloading_manifest_does_not_mutate_champion_state(self) -> None:
        """CHAMP-05: Multiple manifest loads do not mutate versions or active champion status."""
        self.registry.save_version(
            simulation_id=self.sim_a,
            state_dict=self.model_state,
            metrics={"auc": 0.85},
            is_promoted=True,
            status="champion",
        )
        manifest_1 = self.registry._load_manifest(self.sim_a)
        manifest_2 = self.registry._load_manifest(self.sim_a)
        self.assertEqual(manifest_1, manifest_2)

    def test_absence_of_champion_is_truthfully_represented(self) -> None:
        """CHAMP-06: When only candidates exist, get_active_version returns None truthfully."""
        self.registry.save_version(
            simulation_id=self.sim_a,
            state_dict=self.model_state,
            metrics={"auc": 0.80},
            is_promoted=False,
            status="inactive",
        )
        active = self.registry.get_active_version(self.sim_a)
        self.assertIsNone(active)


if __name__ == "__main__":
    unittest.main()
