"""Unit tests for compliance-aligned Enterprise Model Governance and MLOps Shadowing."""

from __future__ import annotations

import shutil
import tempfile
import unittest

import torch

from app.application.services.model_registry import ModelEvaluationEngine, ModelRegistry


class TestModelGovernance(unittest.TestCase):
    """Test suite for SR 11-7 model registry metadata, sign-off gating, shadowing, and auto promotion/rollback."""

    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp()
        self.registry = ModelRegistry(storage_dir=self.test_dir)
        self.evaluation_engine = ModelEvaluationEngine(self.registry)
        import uuid
        self.simulation_id = f"gov_sim_{uuid.uuid4().hex[:8]}"
        from app.application.services.model_registry import create_trusted_internal_context
        ctx = create_trusted_internal_context(
            service_name="training_orchestrator",
            action="bind_ownership",
            resource_type="model",
            resource_id=self.simulation_id,
            bank_id="bank_test",
        )
        self.registry.bind_simulation_ownership(self.simulation_id, owning_bank_id="bank_test", trusted_context=ctx)

        # Register authoritative test users
        from app.infrastructure.security.auth_service import AuthenticationService
        auth_svc = AuthenticationService.get_instance()
        auth_svc.register_user("officer_alice", "StrongPass123!", "bank_test", roles=["compliance"], clearance_level=2)
        auth_svc.register_user("engineer_charlie", "StrongPass123!", "bank_test", roles=["ml_engineer"], clearance_level=2)
        auth_svc.register_user("alice", "StrongPass123!", "bank_test", roles=["compliance"], clearance_level=2)
        auth_svc.register_user("bob", "StrongPass123!", "bank_test", roles=["ml_engineer"], clearance_level=2)

        # PyTorch model state mock
        self.mock_state_dict = {
            "weight": torch.FloatTensor([0.1, 0.2]),
            "bias": torch.FloatTensor([0.5]),
        }
        self.mock_metrics = {"auc_roc": 0.85, "f1_score": 0.82, "loss": 0.15}
        from app.infrastructure.redis_store import RedisStore
        RedisStore("model_evaluation").clear()
        RedisStore._shared_fallback_stores.clear()

    def tearDown(self) -> None:
        shutil.rmtree(self.test_dir, ignore_errors=True)
        from app.config import get_settings
        get_settings.cache_clear()
        from app.infrastructure.security.durable_nonce_store import (
            DurableNonceStore,
            reset_default_nonce_store,
        )
        reset_default_nonce_store()
        DurableNonceStore._instance = None
        from app.infrastructure.security.auth_service import AuthenticationService
        AuthenticationService._instance = None
        from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain
        ImmutableAuditChain.reset_instance()
        from app.infrastructure.redis_store import RedisStore
        RedisStore("model_evaluation").clear()
        RedisStore._shared_fallback_stores.clear()
        from app.application.services.training_provenance_service import (
            reset_default_training_verifier,
        )
        reset_default_training_verifier()

    def test_lineage_metadata_on_save(self) -> None:
        """Verify model lineage details are correctly captured on version registry save."""
        entry = self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.mock_state_dict,
            metrics=self.mock_metrics,
            is_promoted=True,
            git_commit_hash="abc123commit",
            dataset_hash="hash456data",
            dp_noise_profile={"mechanism": "laplace", "epsilon": 0.5, "delta": 1e-5},
            status="champion",
        )

        self.assertEqual(entry["version"], 1)
        self.assertEqual(entry["git_commit_hash"], "abc123commit")
        self.assertEqual(entry["dataset_hash"], "hash456data")
        self.assertEqual(entry["dp_noise_profile"]["mechanism"], "laplace")
        self.assertEqual(entry["dp_noise_profile"]["epsilon"], 0.5)
        self.assertEqual(entry["status"], "champion")
        self.assertEqual(entry["sign_offs"], [])

    def test_cryptographic_signoff_and_gating(self) -> None:
        """Assert dual sign-off workflow gates model promotion to champion or challenger."""
        # Create an inactive model version (e.g. initial training round candidate)
        entry = self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.mock_state_dict,
            metrics=self.mock_metrics,
            is_promoted=False,
            status="inactive",
        )
        version = entry["version"]
        self.assertEqual(entry["status"], "inactive")

        # 1. Compliance officer signoff
        updated = self.registry.sign_off(
            simulation_id=self.simulation_id,
            version=version,
            role="compliance",
            user="officer_alice",
            signature="compliance_signature_123",
            fairness_score=0.98,
        )
        self.assertEqual(len(updated["sign_offs"]), 1)
        self.assertEqual(updated["sign_offs"][0]["role"], "compliance")
        # Should still be inactive since dual role signoff is not yet met
        self.assertEqual(updated["status"], "inactive")

        # Cannot sign off again with same role
        with self.assertRaises(ValueError):
            self.registry.sign_off(
                simulation_id=self.simulation_id,
                version=version,
                role="compliance",
                user="officer_bob",
                signature="another_signature",
            )

        # Invalid role parameter
        with self.assertRaises(ValueError):
            self.registry.sign_off(
                simulation_id=self.simulation_id,
                version=version,
                role="auditor",
                user="auditor_jane",
                signature="sig",
            )

        # 2. ML engineer signoff (meeting dual role condition)
        final_entry = self.registry.sign_off(
            simulation_id=self.simulation_id,
            version=version,
            role="ml_engineer",
            user="engineer_charlie",
            signature="ml_signature_456",
            fairness_score=0.96,
        )
        self.assertEqual(len(final_entry["sign_offs"]), 2)
        # Dual signoff approves model for promotion, but does not auto-activate champion
        self.assertEqual(final_entry["status"], "approved")
        self.assertTrue(final_entry["is_approved"])
        self.assertFalse(final_entry["is_active"])

        # Explicit promotion promotes the approved model to operational champion
        promoted = self.registry.promote_version(
            simulation_id=self.simulation_id,
            version=version,
            target_status="champion",
        )
        self.assertEqual(promoted["status"], "champion")
        self.assertTrue(promoted["is_active"])

    def test_challenger_promotion_gating(self) -> None:
        """Verify model becomes challenger via authorized promotion after institutional approval."""
        # Save version 1 as active Champion
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.mock_state_dict,
            metrics=self.mock_metrics,
            is_promoted=True,
            status="champion",
        )

        # Save version 2 as inactive candidate
        v2_entry = self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.mock_state_dict,
            metrics=self.mock_metrics,
            is_promoted=False,
            status="inactive",
        )

        # Submit dual signoffs for version 2
        self.registry.sign_off(
            self.simulation_id, v2_entry["version"], "compliance", "alice", "sig1"
        )
        v2_updated = self.registry.sign_off(
            self.simulation_id, v2_entry["version"], "ml_engineer", "bob", "sig2"
        )
        self.assertEqual(v2_updated["status"], "approved")
        self.assertTrue(v2_updated["is_approved"])

        # Explicitly promote approved version 2 to Challenger status
        v2_promoted = self.registry.promote_version(
            self.simulation_id,
            v2_entry["version"],
            target_status="challenger",
        )
        self.assertEqual(v2_promoted["status"], "challenger")
        self.assertFalse(v2_promoted["is_active"])

    def test_shadow_routing_metrics_log(self) -> None:
        """Verify shadow latency metrics and prediction outcomes are logged correctly."""
        # 1. Log a set of mock predictions
        self.evaluation_engine.log_prediction(
            simulation_id=self.simulation_id,
            transaction_id="txn_1",
            champion_version=1,
            champion_prob=0.8,
            champion_latency_ms=15.2,
            challenger_version=2,
            challenger_prob=0.85,
            challenger_latency_ms=18.5,
            routed_to="champion",
        )

        # 2. Add ground truth labels feedback
        metrics = self.evaluation_engine.log_feedback(
            simulation_id=self.simulation_id,
            transaction_id="txn_1",
            actual_label=1,
        )

        # Small sample size should return warmup
        self.assertEqual(metrics["status"], "warmup")
        self.assertEqual(metrics["sample_count"], 1)

    def test_auto_promotion_canary_shift(self) -> None:
        """Verify challenger is promoted to traffic-shift (10%) and full Champion when outperforming."""
        # Save version 1 as active Champion
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.mock_state_dict,
            metrics=self.mock_metrics,
            is_promoted=True,
            status="champion",
        )
        # Save version 2 as Challenger
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.mock_state_dict,
            metrics=self.mock_metrics,
            is_promoted=False,
            status="challenger",
        )

        # Ground truth labels: [1, 0, 1, 0, 1]
        # Champion: [0.6, 0.4, 0.6, 0.4, 0.4] -> FPR = 0.0, AUC-ROC = 0.833, PR-AUC = 0.833
        # Challenger: [0.9, 0.1, 0.9, 0.1, 0.9] -> FPR = 0.0, AUC-ROC = 1.0, PR-AUC = 1.0
        actuals_1 = [1, 0, 1, 0, 1]
        champ_probs_1 = [0.6, 0.4, 0.6, 0.4, 0.4]
        chall_probs_1 = [0.9, 0.1, 0.9, 0.1, 0.9]

        # Feed 5 transactions where Challenger outperforms Champion (higher PR-AUC / exact prediction)
        for i in range(5):
            tx_id = f"test_tx_{i}"
            actual = actuals_1[i]
            self.evaluation_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                champion_version=1,
                champion_prob=champ_probs_1[i],
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=chall_probs_1[i],
                challenger_latency_ms=12.0,
            )
            # Submit ground truth outcome
            metrics = self.evaluation_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                actual_label=actual,
            )

        # Verify traffic share shifted to 10% (0.1) due to Challenger outperformance
        self.assertEqual(metrics["traffic_share"], 0.1)
        self.assertTrue(metrics["promotion_triggered"])

        # Feed 5 more transactions with same high performance to trigger full promotion from Challenger to Champion
        actuals_2 = [1, 0, 1, 0, 1]
        champ_probs_2 = [0.6, 0.4, 0.6, 0.4, 0.4]
        chall_probs_2 = [0.9, 0.1, 0.9, 0.1, 0.9]

        for i in range(5):
            tx_id = f"test_tx_{i + 5}"
            actual = actuals_2[i]
            self.evaluation_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                champion_version=1,
                champion_prob=champ_probs_2[i],
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=chall_probs_2[i],
                challenger_latency_ms=12.0,
            )
            metrics = self.evaluation_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                actual_label=actual,
            )

        # Verify full promotion triggered (traffic share reset to 0, v2 promoted as active Champion)
        self.assertEqual(metrics["traffic_share"], 0.0)
        active_ver = self.registry.get_active_version(self.simulation_id)
        self.assertIsNotNone(active_ver)
        if active_ver:
            self.assertEqual(active_ver["version"], 2)

    def test_auto_rollback_triggers(self) -> None:
        """Verify automated rollback to last stable Champion version triggers under performance degradation."""
        # Save version 1 as stable Champion
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.mock_state_dict,
            metrics={"auc_roc": 0.85, "f1_score": 0.82, "loss": 0.1},
            is_promoted=True,
            status="champion",
        )

        # Save version 2 as promoted Champion (now the active model)
        self.registry.save_version(
            simulation_id=self.simulation_id,
            state_dict=self.mock_state_dict,
            metrics={"auc_roc": 0.90, "f1_score": 0.88, "loss": 0.08},
            is_promoted=True,
            status="champion",
        )

        # Log 5 degraded predictions for the active version (v2) to trigger rollback
        # We simulate: 1. AUC drops below 0.65 (bad probabilities) OR latency > 200ms
        for i in range(5):
            tx_id = f"deg_tx_{i}"
            actual = i % 2
            self.evaluation_engine.log_prediction(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                champion_version=2,
                champion_prob=0.5,  # Flat predictions will yield bad AUC
                champion_latency_ms=250.0,  # Latency > 200ms
                challenger_version=None,
                challenger_prob=None,
                challenger_latency_ms=None,
            )
            metrics = self.evaluation_engine.log_feedback(
                simulation_id=self.simulation_id,
                transaction_id=tx_id,
                actual_label=actual,
            )

        # Verify auto-rollback was triggered
        self.assertTrue(metrics["rollback_triggered"])
        # Active version should have rolled back to v1
        active_ver = self.registry.get_active_version(self.simulation_id)
        self.assertIsNotNone(active_ver)
        if active_ver:
            self.assertEqual(active_ver["version"], 1)


if __name__ == "__main__":
    unittest.main()
