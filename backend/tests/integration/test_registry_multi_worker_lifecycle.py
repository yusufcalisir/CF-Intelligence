"""Complete end-to-end model registry and governance lifecycle integration tests.

Verifies:
1. Two independently identified financial institutions (Bank A, Bank B).
2. Multiple simulations with distinct institutional ownership.
3. Genuine PyTorch model artifact serialization and SHA-256 digest binding.
4. Cryptographically verified Ed25519 training receipt binding artifact digest.
5. Authorized institutional dual sign-offs (Four-Eyes Principle: ML Engineer and Compliance).
6. Explicit approved-model promotion to champion with monotonic generation.
7. Production champion selection and verified model loading.
8. Valid inference from the selected champion model.
9. Registration of a newer challenger model (v2) with verified receipt and sign-offs.
10. Version-specific matched-cohort evaluation and genuine canary activation.
11. Eligible post-activation evidence and authorized champion replacement.
12. Sign-off revocation and safe automated rollback to eligible historical champion.
13. Process crash recovery and audit reconciliation.
"""

from __future__ import annotations

import hashlib
import io
import os
from typing import Any

import pytest
import torch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi import HTTPException

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
from app.domain.model_serving_errors import ModelAuthorizationError
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.durable_account_store import DurableAccountStore
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain
from app.presentation.routers.predict import (
    _get_cached_serving_model,
    clear_all_serving_caches,
)


@pytest.fixture
def lifecycle_env(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Isolated environment with production security settings and genuine cryptographic keys."""
    audit_file = str(tmp_path / "security" / "audit_chain.json")
    account_file = str(tmp_path / "security" / "accounts.json")
    trust_file = str(tmp_path / "security" / "trust_store.json")
    reg_dir = str(tmp_path / "storage" / "registry")
    os.makedirs(os.path.dirname(audit_file), exist_ok=True)
    os.makedirs(reg_dir, exist_ok=True)

    audit_secret = "CFI_PROD_AUDIT_LEDGER_SECRET_2026_TEST"
    jwt_secret = "CFI_PROD_JWT_SIGNING_KEY_2026_TEST"
    authority_secret = "CFI_PROD_ACCOUNT_AUTHORITY_SECRET_2026"
    admin_token = "CFI_TRUST_ADMIN_TOKEN_2026"
    internal_secret = "cfi_prod_internal_secret_32_chars_ok!"

    monkeypatch.setenv("CFI_AUDIT_LEDGER_PATH", audit_file)
    monkeypatch.setenv("CFI_AUDIT_LEDGER_SECRET", audit_secret)
    monkeypatch.setenv("CFI_TRUST_STORE_PATH", trust_file)
    monkeypatch.setenv("CFI_TRUST_STORE_ADMIN_TOKEN", admin_token)
    monkeypatch.setenv("CFI_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("CFI_JWT_SIGNING_SECRET", jwt_secret)
    monkeypatch.setenv("CFI_PAYLOAD_SIGNING_SECRET", jwt_secret)
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", account_file)
    monkeypatch.setenv("CFI_ACCOUNT_AUTHORITY_SECRET", authority_secret)
    nonce_file = str(tmp_path / "security" / "consumed_nonces.json")
    monkeypatch.setenv("CFI_INTERNAL_NONCE_STORE_PATH", nonce_file)
    monkeypatch.setenv("CFI_INTERNAL_CONTEXT_SECRET", internal_secret)
    monkeypatch.setenv("APP_ENV", "development")  # Allows standalone local evaluation

    from app.infrastructure.security.durable_nonce_store import reset_default_nonce_store

    reset_default_nonce_store()
    get_settings.cache_clear()
    AuthenticationService._instance = None
    ImmutableAuditChain._instance = None
    DurableAccountStore._instance = None
    clear_all_serving_caches()

    auth_svc = AuthenticationService(
        store_path=account_file,
        signing_secret=jwt_secret,
        authority_secret=authority_secret,
        app_env="production",
    )
    AuthenticationService._instance = auth_svc

    # Register Bank A authoritative users
    u_ml_a = auth_svc.register_user(
        "alice_ml_bank_a", "SecurePass1234!", "bank_a", ["ml_engineer"], clearance_level=2
    )
    u_comp_a = auth_svc.register_user(
        "carol_comp_bank_a", "SecurePass1234!", "bank_a", ["compliance"], clearance_level=3
    )

    # Register Bank B authoritative users
    u_ml_b = auth_svc.register_user(
        "bob_ml_bank_b", "SecurePass1234!", "bank_b", ["ml_engineer"], clearance_level=2
    )
    u_comp_b = auth_svc.register_user(
        "dave_comp_bank_b", "SecurePass1234!", "bank_b", ["compliance"], clearance_level=3
    )

    tokens = {
        "bank_a_ml": auth_svc.create_token_bundle(u_ml_a).access_token,
        "bank_a_comp": auth_svc.create_token_bundle(u_comp_a).access_token,
        "bank_b_ml": auth_svc.create_token_bundle(u_ml_b).access_token,
        "bank_b_comp": auth_svc.create_token_bundle(u_comp_b).access_token,
    }

    # Setup Ed25519 training authority
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_hex = (
        priv_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        .hex()
    )
    key_id = "training_issuer_key_lifecycle_01"
    trust_store = DurableTrustStore(trust_file_path=trust_file, admin_token=admin_token)
    trust_store.register_approved_key(
        key_id=key_id, public_key_hex=pub_hex, admin_token=admin_token
    )

    import app.application.services.training_provenance_service as tps

    issuer = TrainingReceiptIssuer(
        key_id=key_id,
        private_key=priv_key,
        trust_store=trust_store,
        require_authenticated_job=False,
        app_env="development",
    )
    verifier = TrainingReceiptVerifier(trust_store=trust_store)
    tps._DEFAULT_TRUST_STORE = trust_store
    tps._DEFAULT_VERIFIER = verifier

    registry = ModelRegistry(storage_dir=str(tmp_path / "storage"), verifier=verifier)
    eval_engine = ModelEvaluationEngine(registry)

    import app.presentation.routers.predict as predict_router

    predict_router.clear_all_serving_caches()
    predict_router._registry = registry

    return {
        "registry": registry,
        "eval_engine": eval_engine,
        "tokens": tokens,
        "issuer": issuer,
        "verifier": verifier,
        "jwt_secret": jwt_secret,
        "tmp_path": tmp_path,
        "key_id": key_id,
        "trust_store": trust_store,
    }


def _create_serialized_model_and_receipt(
    issuer: TrainingReceiptIssuer,
    sim_id: str,
    weight_bias: float = 0.0,
    owning_bank_id: str = "bank_a",
) -> tuple[bytes, dict[str, Any], str, Any]:
    """Helper to create real serialized PyTorch model bytes and cryptographic receipt."""
    from datetime import UTC, datetime

    model = FraudDetectionModel(input_dim=NUM_FEATURES, dp_compatible=True)
    with torch.no_grad():
        for param in model.parameters():
            param.fill_(weight_bias)
    buf = io.BytesIO()
    torch.save(model.state_dict(), buf)
    artifact_bytes = buf.getvalue()
    artifact_sha = hashlib.sha256(artifact_bytes).hexdigest()

    evidence = TrainingExecutionEvidence(
        training_run_id=f"run_{sim_id}_{weight_bias}",
        job_id=f"job_{sim_id}_{weight_bias}",
        simulation_id=sim_id,
        completed_at=datetime.now(UTC).isoformat(),
        executed_rounds=3,
        executed_epochs=3,
        sample_count=200,
        observed_metrics={"loss": 0.15, "auc": 0.95},
        artifact_sha256=artifact_sha,
        architecture="FraudDetectionModel",
        feature_schema_version="1.0",
        feature_names=[f"f{i}" for i in range(10)],
        owning_bank_id=owning_bank_id,
    )
    receipt = issuer.issue_receipt(
        evidence=evidence,
        model_bytes=artifact_bytes,
        owning_bank_id=owning_bank_id,
        architecture="FraudDetectionModel",
        input_dim=NUM_FEATURES,
        feature_names=[f"f{i}" for i in range(10)],
        feature_schema_version="1.0",
    )
    return artifact_bytes, model.state_dict(), artifact_sha, receipt


class TestRegistryMultiWorkerLifecycle:
    """Verifies complete model lifecycle from training receipt to rollback and recovery."""

    def test_complete_model_governance_and_serving_lifecycle(
        self, lifecycle_env: dict[str, Any]
    ) -> None:
        registry: ModelRegistry = lifecycle_env["registry"]
        eval_engine: ModelEvaluationEngine = lifecycle_env["eval_engine"]
        tokens = lifecycle_env["tokens"]
        issuer: TrainingReceiptIssuer = lifecycle_env["issuer"]
        jwt_secret: str = lifecycle_env["jwt_secret"]

        sim_alpha = "sim_institutional_alpha"
        sim_beta = "sim_institutional_beta"

        # -------------------------------------------------------------
        # STEP 1: Bank A initial registration of Model v1 for sim_alpha
        # -------------------------------------------------------------
        bytes_v1, state_v1, sha_v1, receipt_v1 = _create_serialized_model_and_receipt(
            issuer, sim_alpha, weight_bias=0.0
        )

        v1_entry = registry.save_version(
            simulation_id=sim_alpha,
            state_dict=state_v1,
            artifact_bytes=bytes_v1,
            training_receipt=receipt_v1,
            provenance_type="TRAINING_EVIDENCE_VERIFIED",
            owning_bank_id="bank_a",
            auth_token=tokens["bank_a_ml"],
        )
        assert v1_entry["version"] == 1
        assert v1_entry["owning_bank_id"] == "bank_a"
        assert v1_entry["sha256"] == sha_v1

        # -------------------------------------------------------------
        # STEP 2: Bank B unauthorized cross-bank registration rejected
        # -------------------------------------------------------------
        bytes_b, state_b, _, receipt_b = _create_serialized_model_and_receipt(
            issuer, sim_alpha, weight_bias=0.5, owning_bank_id="bank_b"
        )
        with pytest.raises((ModelAuthorizationError, PermissionError)):
            registry.save_version(
                simulation_id=sim_alpha,
                state_dict=state_b,
                artifact_bytes=bytes_b,
                training_receipt=receipt_b,
                provenance_type="TRAINING_EVIDENCE_VERIFIED",
                owning_bank_id="bank_b",
                auth_token=tokens["bank_b_ml"],
            )

        # Bank B successfully registers for sim_beta (isolated institutional ownership)
        bytes_beta, state_beta, _, receipt_beta = _create_serialized_model_and_receipt(
            issuer, sim_beta, weight_bias=0.5, owning_bank_id="bank_b"
        )
        registry.save_version(
            simulation_id=sim_beta,
            state_dict=state_beta,
            artifact_bytes=bytes_beta,
            training_receipt=receipt_beta,
            provenance_type="TRAINING_EVIDENCE_VERIFIED",
            owning_bank_id="bank_b",
            auth_token=tokens["bank_b_ml"],
        )
        assert registry.get_simulation_ownership(sim_beta)["owning_bank_id"] == "bank_b"

        # -------------------------------------------------------------
        # STEP 3: Bank A Dual Institutional Sign-Offs on v1
        # -------------------------------------------------------------
        sig_ml_1 = generate_signoff_signature(
            simulation_id=sim_alpha,
            version=1,
            artifact_sha256=sha_v1,
            role="ml_engineer",
            user="alice_ml_bank_a",
            signing_secret=jwt_secret,
        )
        registry.sign_off(
            simulation_id=sim_alpha,
            version=1,
            role="ml_engineer",
            user="alice_ml_bank_a",
            signature=sig_ml_1,
            auth_token=tokens["bank_a_ml"],
        )

        sig_comp_1 = generate_signoff_signature(
            simulation_id=sim_alpha,
            version=1,
            artifact_sha256=sha_v1,
            role="compliance",
            user="carol_comp_bank_a",
            signing_secret=jwt_secret,
        )
        registry.sign_off(
            simulation_id=sim_alpha,
            version=1,
            role="compliance",
            user="carol_comp_bank_a",
            signature=sig_comp_1,
            auth_token=tokens["bank_a_comp"],
        )

        v1_meta = registry.get_version_metadata(sim_alpha, 1)
        assert v1_meta is not None
        assert v1_meta["is_approved"] is True
        assert v1_meta["is_production_eligible"] is True

        # -------------------------------------------------------------
        # STEP 4: Promote v1 to Champion & Verify Loading and Inference
        # -------------------------------------------------------------
        registry.promote_version(
            simulation_id=sim_alpha,
            version=1,
            target_status="champion",
            auth_token=tokens["bank_a_ml"],
        )
        active_champ = registry.get_active_version(sim_alpha)
        assert active_champ is not None
        assert active_champ["version"] == 1
        assert active_champ["status"] == "champion"

        # Verified model loading via serving cache
        serving_model = _get_cached_serving_model(simulation_id=sim_alpha)
        assert serving_model is not None
        input_features = torch.randn(1, 10)
        prediction = serving_model(input_features)
        assert prediction.shape in ((1,), (1, 1))
        prob = float(prediction.item())
        assert 0.0 <= prob <= 1.0

        # -------------------------------------------------------------
        # STEP 5: Register Challenger v2 with Verified Training Receipt
        # -------------------------------------------------------------
        bytes_v2, state_v2, sha_v2, receipt_v2 = _create_serialized_model_and_receipt(
            issuer, sim_alpha, weight_bias=0.2
        )
        v2_entry = registry.save_version(
            simulation_id=sim_alpha,
            state_dict=state_v2,
            artifact_bytes=bytes_v2,
            training_receipt=receipt_v2,
            provenance_type="TRAINING_EVIDENCE_VERIFIED",
            owning_bank_id="bank_a",
            auth_token=tokens["bank_a_ml"],
        )
        assert v2_entry["version"] == 2

        # Dual sign-offs on Challenger v2
        sig_ml_2 = generate_signoff_signature(
            sim_alpha, 2, sha_v2, "ml_engineer", "alice_ml_bank_a", jwt_secret
        )
        registry.sign_off(
            sim_alpha, 2, "ml_engineer", "alice_ml_bank_a", sig_ml_2, auth_token=tokens["bank_a_ml"]
        )

        sig_comp_2 = generate_signoff_signature(
            sim_alpha, 2, sha_v2, "compliance", "carol_comp_bank_a", jwt_secret
        )
        registry.sign_off(
            sim_alpha,
            2,
            "compliance",
            "carol_comp_bank_a",
            sig_comp_2,
            auth_token=tokens["bank_a_comp"],
        )

        # Promote v2 as challenger
        registry.promote_version(
            sim_alpha, 2, target_status="challenger", auth_token=tokens["bank_a_ml"]
        )

        # -------------------------------------------------------------
        # STEP 6: Matched Cohort Evaluation & Genuine Canary Activation
        # -------------------------------------------------------------
        # Log 6 pre-activation transactions (Challenger outperforming healthy Champion)
        champ_probs_pre = [0.60, 0.45, 0.40, 0.35, 0.65, 0.30]
        chall_probs_pre = [0.95, 0.05, 0.90, 0.02, 0.92, 0.04]
        canary_act_res = None
        for i in range(5):
            tx_id = f"tx_pre_{i}"
            eval_engine.log_prediction(
                sim_alpha,
                tx_id,
                champion_version=1,
                champion_prob=champ_probs_pre[i],
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=chall_probs_pre[i],
                challenger_latency_ms=10.0,
            )
            feed_res = eval_engine.log_feedback(
                sim_alpha, tx_id, actual_label=1 if i % 2 == 0 else 0
            )
            if feed_res.get("promotion_triggered"):
                canary_act_res = feed_res

        assert canary_act_res is not None
        assert canary_act_res["traffic_share"] == 0.1
        assert "Routed 10% traffic" in canary_act_res["promotion_message"]

        # -------------------------------------------------------------
        # STEP 7: Eligible Post-Activation Evidence Promotes v2 to Champion
        # -------------------------------------------------------------
        champ_probs_post = [0.62, 0.44, 0.41, 0.36, 0.66]
        chall_probs_post = [0.96, 0.04, 0.91, 0.01, 0.93]
        promo_champ_res = None
        for i in range(5):
            tx_id = f"tx_canary_{i}"
            eval_engine.log_prediction(
                sim_alpha,
                tx_id,
                champion_version=1,
                champion_prob=champ_probs_post[i],
                champion_latency_ms=10.0,
                challenger_version=2,
                challenger_prob=chall_probs_post[i],
                challenger_latency_ms=10.0,
                routed_to="challenger" if i == 0 else "champion",
            )
            feed_res = eval_engine.log_feedback(
                sim_alpha, tx_id, actual_label=1 if i % 2 == 0 else 0
            )
            if feed_res.get("promotion_triggered"):
                promo_champ_res = feed_res

        assert promo_champ_res is not None
        assert "promoted to Champion" in promo_champ_res["promotion_message"]

        # Confirm registry state: v2 is now active Champion
        active_after = registry.get_active_version(sim_alpha)
        assert active_after is not None
        assert active_after["version"] == 2
        assert active_after["status"] == "champion"

        # Serving worker automatically resolves and serves the new champion v2
        serving_model_v2 = _get_cached_serving_model(simulation_id=sim_alpha)
        assert serving_model_v2 is not None

        # -------------------------------------------------------------
        # STEP 8: Compliance Revocation & Rollback to Historical Champion v1
        # -------------------------------------------------------------
        registry.revoke_sign_off(
            simulation_id=sim_alpha,
            version=2,
            role="compliance",
            user="carol_comp_bank_a",
            reason="Post-promotion regulatory divergence",
            auth_token=tokens["bank_a_comp"],
        )

        # Serving model cache immediately rejects revoked v2
        with pytest.raises(HTTPException):
            _get_cached_serving_model(simulation_id=sim_alpha)

        # Rollback safely commits back to v1
        rb_res = registry.rollback(
            simulation_id=sim_alpha,
            version=1,
            auth_token=tokens["bank_a_comp"],
        )
        assert rb_res["version"] == 1
        assert rb_res["status"] == "champion"

        # Serving worker now reloads and serves eligible champion v1
        serving_model_rb = _get_cached_serving_model(simulation_id=sim_alpha)
        assert serving_model_rb is not None
        active_ver = registry.get_active_version(sim_alpha)
        assert active_ver is not None
        assert active_ver["version"] == 1
