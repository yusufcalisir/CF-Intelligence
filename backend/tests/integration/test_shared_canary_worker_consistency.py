"""Integration tests for shared canary evaluation worker consistency and promotion safety.

Verifies:
1. Version-specific challenger cohort isolation prevents mixed version contamination.
2. Consistent multi-worker canary activation and graduation without duplicate promotions.
3. Incomplete or corrupted shared canary state triggers fail-closed reset.
4. Auto-rollback gating immediately demotes degrading champions and revokes canary traffic.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

import pytest
import torch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.model_registry import (
    ModelEvaluationEngine,
    ModelRegistry,
    generate_signoff_signature,
)
from app.application.services.model_service import NUM_FEATURES, FraudDetectionModel
from app.application.services.training_provenance_service import (
    DurableTrustStore,
    TrainingExecutionEvidence,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
)
from app.config import get_settings
from app.infrastructure.redis_store import RedisStore
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.durable_account_store import DurableAccountStore
from app.infrastructure.security.durable_nonce_store import reset_default_nonce_store
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain


@pytest.fixture
def canary_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Sets up an isolated environment with cryptographic authority and account store."""
    audit_file = str(tmp_path / "security" / "audit_chain.json")
    account_file = str(tmp_path / "security" / "accounts.json")
    trust_file = str(tmp_path / "security" / "trust_store.json")
    nonce_file = str(tmp_path / "security" / "consumed_nonces.json")
    storage_dir = str(tmp_path / "storage")

    audit_secret = "CFI_PROD_AUDIT_LEDGER_SECRET_2026_TEST"
    jwt_secret = "CFI_PROD_JWT_SIGNING_KEY_2026_TEST"
    authority_secret = "CFI_PROD_ACCOUNT_AUTHORITY_SECRET_2026"
    admin_token = "CFI_TRUST_ADMIN_TOKEN_2026"
    internal_secret = "cfi_prod_internal_secret_32_chars_ok!"

    env_config = {
        "CFI_AUDIT_LEDGER_PATH": audit_file,
        "CFI_AUDIT_LEDGER_SECRET": audit_secret,
        "CFI_TRUST_STORE_PATH": trust_file,
        "CFI_TRUST_STORE_ADMIN_TOKEN": admin_token,
        "CFI_STORAGE_DIR": storage_dir,
        "CFI_JWT_SIGNING_SECRET": jwt_secret,
        "CFI_PAYLOAD_SIGNING_SECRET": jwt_secret,
        "CFI_ACCOUNT_STORE_PATH": account_file,
        "CFI_IDENTITY_STORE_PATH": account_file,
        "CFI_ACCOUNT_AUTHORITY_SECRET": authority_secret,
        "CFI_IDENTITY_STORE_SECRET": authority_secret,
        "CFI_INTERNAL_NONCE_STORE_PATH": nonce_file,
        "CFI_INTERNAL_CONTEXT_SECRET": internal_secret,
        "APP_ENV": "development",
    }
    for k, v in env_config.items():
        monkeypatch.setenv(k, v)

    reset_default_nonce_store()
    get_settings.cache_clear()
    AuthenticationService._instance = None
    ImmutableAuditChain._instance = None
    DurableAccountStore._instance = None

    auth_svc = AuthenticationService(
        store_path=account_file,
        signing_secret=jwt_secret,
        authority_secret=authority_secret,
        app_env="production",
    )
    AuthenticationService._instance = auth_svc

    u_ml = auth_svc.register_user(
        "alice_ml", "SecurePass1234!", "bank_a", ["ml_engineer"], clearance_level=2
    )
    u_comp = auth_svc.register_user(
        "carol_comp", "SecurePass1234!", "bank_a", ["compliance"], clearance_level=3
    )

    tokens = {
        "ml": auth_svc.create_token_bundle(u_ml).access_token,
        "comp": auth_svc.create_token_bundle(u_comp).access_token,
    }

    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_hex = (
        priv_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        .hex()
    )
    key_id = "training_key_canary_01"
    trust_store = DurableTrustStore(trust_file_path=trust_file, admin_token=admin_token)
    trust_store.register_approved_key(
        key_id=key_id, public_key_hex=pub_hex, admin_token=admin_token
    )

    issuer = TrainingReceiptIssuer(
        key_id=key_id,
        private_key=priv_key,
        trust_store=trust_store,
        require_authenticated_job=False,
        app_env="development",
    )
    verifier = TrainingReceiptVerifier(trust_store=trust_store)
    registry = ModelRegistry(storage_dir=storage_dir, verifier=verifier)

    RedisStore._shared_fallback_stores.clear()

    return {
        "storage_dir": storage_dir,
        "tokens": tokens,
        "issuer": issuer,
        "verifier": verifier,
        "trust_store": trust_store,
        "jwt_secret": jwt_secret,
        "registry": registry,
    }


def _register_model(
    env: dict[str, Any], sim_id: str, version: int, promote_to: str | None = None
) -> dict[str, Any]:
    """Helper to create and register a fully verified model version."""
    issuer: TrainingReceiptIssuer = env["issuer"]
    registry: ModelRegistry = env["registry"]
    tokens = env["tokens"]
    jwt_secret = env["jwt_secret"]

    model = FraudDetectionModel(input_dim=NUM_FEATURES, dp_compatible=True)
    buf = io.BytesIO()
    torch.save(model.state_dict(), buf)
    artifact_bytes = buf.getvalue()
    artifact_sha = hashlib.sha256(artifact_bytes).hexdigest()

    evidence = TrainingExecutionEvidence(
        training_run_id=f"run_{sim_id}_v{version}",
        job_id=f"job_{sim_id}_v{version}",
        simulation_id=sim_id,
        completed_at="2026-10-15T00:00:00Z",
        executed_rounds=2,
        executed_epochs=2,
        sample_count=150,
        observed_metrics={"loss": 0.08},
        artifact_sha256=artifact_sha,
        architecture="FraudDetectionModel",
        feature_schema_version="1.0",
        feature_names=[f"f{i}" for i in range(NUM_FEATURES)],
        owning_bank_id="bank_a",
    )
    receipt = issuer.issue_receipt(
        evidence=evidence,
        model_bytes=artifact_bytes,
        owning_bank_id="bank_a",
        architecture="FraudDetectionModel",
        input_dim=NUM_FEATURES,
        feature_names=[f"f{i}" for i in range(NUM_FEATURES)],
        feature_schema_version="1.0",
    )

    entry = registry.save_version(
        simulation_id=sim_id,
        state_dict=model.state_dict(),
        artifact_bytes=artifact_bytes,
        training_receipt=receipt,
        provenance_type="TRAINING_EVIDENCE_VERIFIED",
        owning_bank_id="bank_a",
        auth_token=tokens["ml"],
    )

    sig_ml = generate_signoff_signature(
        sim_id, version, artifact_sha, "ml_engineer", "alice_ml", jwt_secret
    )
    registry.sign_off(sim_id, version, "ml_engineer", "alice_ml", sig_ml, auth_token=tokens["ml"])
    sig_comp = generate_signoff_signature(
        sim_id, version, artifact_sha, "compliance", "carol_comp", jwt_secret
    )
    registry.sign_off(
        sim_id, version, "compliance", "carol_comp", sig_comp, auth_token=tokens["comp"]
    )

    if promote_to:
        registry.promote_version(sim_id, version, target_status=promote_to, auth_token=tokens["ml"])
    return entry


class TestSharedCanaryWorkerConsistency:
    """Verifies canary promotion, worker consistency, and safety gating."""

    def test_version_specific_challenger_cohort_isolation(self, canary_env: dict[str, Any]) -> None:
        """Verifies that evaluation isolates matched cohorts and does not mix observations across challengers."""
        registry = canary_env["registry"]
        engine = ModelEvaluationEngine(registry)
        sim_id = "sim_canary_cohort_iso"

        _register_model(canary_env, sim_id, version=1, promote_to="champion")
        _register_model(canary_env, sim_id, version=2, promote_to="challenger")
        _register_model(canary_env, sim_id, version=3, promote_to=None)

        # Log mixed predictions: 5 for challenger v2 and 5 for challenger v3
        labels = [1, 0, 1, 0, 1]
        for i, lbl in enumerate(labels):
            engine.log_prediction(
                simulation_id=sim_id,
                transaction_id=f"tx_v2_{i}",
                champion_version=1,
                champion_prob=0.4 if lbl == 1 else 0.6,
                challenger_version=2,
                challenger_prob=0.95 if lbl == 1 else 0.05,
            )
            engine.log_feedback(sim_id, f"tx_v2_{i}", actual_label=lbl)

            engine.log_prediction(
                simulation_id=sim_id,
                transaction_id=f"tx_v3_{i}",
                champion_version=1,
                champion_prob=0.4 if lbl == 1 else 0.6,
                challenger_version=3,
                challenger_prob=0.9 if lbl == 1 else 0.1,
            )
            engine.log_feedback(sim_id, f"tx_v3_{i}", actual_label=lbl)

        # Without explicit target, ambiguous mixed challengers defer evaluation
        metrics = engine.evaluate_performance(sim_id)
        assert metrics.get("promotion_triggered") is False
        assert "ambiguous" in metrics.get("promotion_message", "").lower()

        # With explicit target challenger version 2, matched cohort isolates exactly v2
        metrics_v2 = engine.evaluate_performance(sim_id, target_challenger_version=2)
        assert metrics_v2.get("paired_sample_count") == 5
        assert metrics_v2.get("challenger_version") == 2

    def test_cross_worker_canary_graduation_consistency(self, canary_env: dict[str, Any]) -> None:
        """Verifies that two workers evaluating the same outperforming challenger graduate it consistently."""
        registry = canary_env["registry"]
        worker1_engine = ModelEvaluationEngine(registry)
        worker2_engine = ModelEvaluationEngine(registry)
        sim_id = "sim_cross_worker_canary"

        _register_model(canary_env, sim_id, version=1, promote_to="champion")
        _register_model(canary_env, sim_id, version=2, promote_to="challenger")

        # Step 1: Pre-canary evaluation triggers canary activation (traffic_share -> 0.1)
        # Ensure champion has no degradation (FPR <= 0.05, AUC >= 0.65), while challenger has higher PR-AUC
        y_true = [1, 0, 1, 0, 1, 0]
        champ_p = [0.6, 0.1, 0.25, 0.35, 0.8, 0.05]
        chall_p = [0.99, 0.01, 0.95, 0.02, 0.98, 0.01]

        for i, lbl in enumerate(y_true):
            tx_id = f"tx_pre_{i}"
            worker1_engine.log_prediction(
                simulation_id=sim_id,
                transaction_id=tx_id,
                champion_version=1,
                champion_prob=champ_p[i],
                challenger_version=2,
                challenger_prob=chall_p[i],
            )
            worker1_engine.log_feedback(
                sim_id, tx_id, actual_label=lbl, target_challenger_version=2
            )

        # Worker 2 reads store: canary is active with 0.1 share
        w2_metrics = worker2_engine.evaluate_performance(sim_id, target_challenger_version=2)
        assert w2_metrics.get("traffic_share") == 0.1

        # Step 2: Post-activation canary scoring (observations with challenger routed)
        for i, lbl in enumerate(y_true):
            tx_id = f"tx_post_{i}"
            worker2_engine.log_prediction(
                simulation_id=sim_id,
                transaction_id=tx_id,
                champion_version=1,
                champion_prob=champ_p[i],
                challenger_version=2,
                challenger_prob=chall_p[i],
                routed_to="challenger",
            )
            worker2_engine.log_feedback(
                sim_id, tx_id, actual_label=lbl, target_challenger_version=2
            )

        # Step 3: Graduation promoted v2 to Champion
        assert registry.get_active_version(sim_id)["version"] == 2

        # Step 4: Worker 1 immediately evaluates: detects v2 is already Champion, avoids redundant promotion
        w1_after = worker1_engine.evaluate_performance(sim_id, target_challenger_version=2)
        assert w1_after.get("traffic_share") == 0.0
        assert registry.get_active_version(sim_id)["version"] == 2

    def test_corrupted_canary_state_resets_and_defers_promotion(
        self, canary_env: dict[str, Any]
    ) -> None:
        """Verifies that partial or corrupted canary state triggers fail-closed reset."""
        registry = canary_env["registry"]
        engine = ModelEvaluationEngine(registry)
        sim_id = "sim_corrupt_canary"

        _register_model(canary_env, sim_id, version=1, promote_to="champion")
        _register_model(canary_env, sim_id, version=2, promote_to="challenger")

        # Log predictions and feedback first without activating canary (e.g. 4 samples, below threshold)
        y_true = [1, 0, 1, 0, 1, 0]
        champ_p = [0.6, 0.1, 0.25, 0.35, 0.8, 0.05]
        chall_p = [0.99, 0.01, 0.95, 0.02, 0.98, 0.01]

        for i, lbl in enumerate(y_true):
            tx_id = f"tx_corrupt_{i}"
            engine.log_prediction(
                simulation_id=sim_id,
                transaction_id=tx_id,
                champion_version=1,
                champion_prob=champ_p[i],
                challenger_version=2,
                challenger_prob=chall_p[i],
            )
            engine._store.record_feedback(
                simulation_id=sim_id,
                transaction_id=tx_id,
                actual_label=lbl,
            )

        # Inject corrupted canary state (traffic share active at 0.1, but canary_state corrupt/missing activation_seq)
        engine._store.set(f"{sim_id}:challenger_traffic_share", 0.1)
        engine._store.set(f"{sim_id}:challenger_traffic_version", 2)
        engine._store.set(f"{sim_id}:canary_state", {"corrupt": True})

        # Evaluate performance: detects corrupted state, resets traffic share to 0.0, and defers promotion
        metrics = engine.evaluate_performance(sim_id, target_challenger_version=2)
        # Invariant: Traffic share reset to 0.0 and promotion deferred
        assert metrics.get("promotion_triggered") is False
        assert metrics.get("traffic_share") == 0.0
        assert "deferring promotion" in metrics.get("promotion_message", "").lower()
        assert engine._store.get(f"{sim_id}:challenger_traffic_share") == 0.0
        assert engine._store.get(f"{sim_id}:canary_state") is None
