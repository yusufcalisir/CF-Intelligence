"""Integration tests for cross-worker model revocation and serving consistency.

Verifies:
1. Cross-process model-version revocation immediately rejects serving from previously cached models.
2. Cross-process institutional sign-off revocation demotes champion and blocks inference.
3. Training issuer key revocation in shared trust store invalidates cached serving models.
4. Champion promotion on one worker immediately invalidates stale cached older versions on other workers.
"""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
from typing import Any

import pytest
import torch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi import HTTPException

import app.presentation.routers.predict as predict_router
from app.application.services.model_registry import (
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
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.durable_account_store import DurableAccountStore
from app.infrastructure.security.durable_nonce_store import reset_default_nonce_store
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain


@pytest.fixture
def revocation_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Sets up a complete production-parity environment for cross-worker revocation tests."""
    audit_file = str(tmp_path / "security" / "audit_chain.json")
    account_file = str(tmp_path / "security" / "accounts.json")
    trust_file = str(tmp_path / "security" / "trust_store.json")
    nonce_file = str(tmp_path / "security" / "consumed_nonces.json")
    storage_dir = str(tmp_path / "storage")
    os.makedirs(os.path.dirname(audit_file), exist_ok=True)
    os.makedirs(storage_dir, exist_ok=True)

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
        "alice_ml_bank_a", "SecurePass1234!", "bank_a", ["ml_engineer"], clearance_level=2
    )
    u_comp = auth_svc.register_user(
        "carol_comp_bank_a", "SecurePass1234!", "bank_a", ["compliance"], clearance_level=3
    )

    tokens = {
        "ml": auth_svc.create_token_bundle(u_ml).access_token,
        "comp": auth_svc.create_token_bundle(u_comp).access_token,
    }

    # Setup training authority key
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_hex = (
        priv_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        .hex()
    )
    key_id = "training_key_rev_01"
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

    old_registry = predict_router._registry
    predict_router.clear_all_serving_caches()
    predict_router._registry = registry

    try:
        yield {
            "storage_dir": storage_dir,
            "tokens": tokens,
            "issuer": issuer,
            "verifier": verifier,
            "trust_store": trust_store,
            "key_id": key_id,
            "admin_token": admin_token,
            "jwt_secret": jwt_secret,
            "registry": registry,
            "auth_svc": auth_svc,
        }
    finally:
        predict_router._registry = old_registry
        predict_router.clear_all_serving_caches()


def _create_and_register_champion(
    env: dict[str, Any], sim_id: str, version: int = 1, weight_bias: float = 0.0
) -> dict[str, Any]:
    """Helper to create, register, sign off, and promote a model version to champion."""
    issuer: TrainingReceiptIssuer = env["issuer"]
    registry: ModelRegistry = env["registry"]
    tokens = env["tokens"]
    jwt_secret = env["jwt_secret"]

    model = FraudDetectionModel(input_dim=NUM_FEATURES, dp_compatible=True)
    with torch.no_grad():
        for param in model.parameters():
            param.add_(weight_bias)

    buf = io.BytesIO()
    torch.save(model.state_dict(), buf)
    artifact_bytes = buf.getvalue()
    artifact_sha = hashlib.sha256(artifact_bytes).hexdigest()

    evidence = TrainingExecutionEvidence(
        training_run_id=f"run_{sim_id}_v{version}",
        job_id=f"job_{sim_id}_v{version}",
        simulation_id=sim_id,
        completed_at="2026-10-15T00:00:00Z",
        executed_rounds=3,
        executed_epochs=3,
        sample_count=200,
        observed_metrics={"loss": 0.1},
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
        sim_id, version, artifact_sha, "ml_engineer", "alice_ml_bank_a", jwt_secret
    )
    registry.sign_off(
        sim_id, version, "ml_engineer", "alice_ml_bank_a", sig_ml, auth_token=tokens["ml"]
    )

    sig_comp = generate_signoff_signature(
        sim_id, version, artifact_sha, "compliance", "carol_comp_bank_a", jwt_secret
    )
    registry.sign_off(
        sim_id, version, "compliance", "carol_comp_bank_a", sig_comp, auth_token=tokens["comp"]
    )

    registry.promote_version(sim_id, version, target_status="champion", auth_token=tokens["ml"])
    return entry


class TestCrossWorkerModelRevocation:
    """Verifies that model revocation immediately invalidates cached models and prevents serving."""

    def test_cross_worker_model_version_revocation(self, revocation_env: dict[str, Any]) -> None:
        """Verifies that when a model version is revoked in registry, serving worker immediately refuses to serve it."""
        sim_id = "sim_rev_version"
        _create_and_register_champion(revocation_env, sim_id, version=1)

        # Worker 1: Warm serving cache loads model v1 and produces valid inference
        model_v1 = predict_router._get_cached_serving_model(simulation_id=sim_id)
        assert model_v1 is not None
        inp = torch.randn(1, NUM_FEATURES)
        out1 = model_v1(inp)
        assert out1 is not None

        # Worker 2: Authoritative registry revokes model version 1
        registry: ModelRegistry = revocation_env["registry"]
        token_comp = revocation_env["tokens"]["comp"]
        rev_entry = registry.revoke_version(
            simulation_id=sim_id,
            version=1,
            reason="Material security vulnerability in model weights",
            auth_token=token_comp,
        )
        assert rev_entry["is_revoked"] is True
        assert rev_entry["is_active"] is False
        assert rev_entry["status"] == "inactive"

        # Worker 1: Next serving request MUST fail closed (503 Service Unavailable)
        with pytest.raises(HTTPException) as exc_info:
            predict_router._get_cached_serving_model(simulation_id=sim_id)
        assert exc_info.value.status_code == 503
        assert (
            "no verified active champion model artifact found" in exc_info.value.detail.lower()
            or "champion manifest resolution failed" in exc_info.value.detail.lower()
        )

    def test_cross_worker_signoff_revocation(self, revocation_env: dict[str, Any]) -> None:
        """Verifies that revoking an institutional sign-off demotes the champion and halts serving."""
        sim_id = "sim_rev_signoff"
        _create_and_register_champion(revocation_env, sim_id, version=1)

        # Worker 1: Model is cached and active
        model_v1 = predict_router._get_cached_serving_model(simulation_id=sim_id)
        assert model_v1 is not None

        # Worker 2: Compliance revokes approval
        registry: ModelRegistry = revocation_env["registry"]
        token_comp = revocation_env["tokens"]["comp"]
        registry.revoke_sign_off(
            simulation_id=sim_id,
            version=1,
            role="compliance",
            user="carol_comp_bank_a",
            reason="Post-deployment regulatory compliance failure",
            auth_token=token_comp,
        )

        # In production mode, get_active_champion_identity requires valid dual sign-offs
        # Set production environment to verify strict governance gating
        from app.config import get_settings

        os.environ["APP_ENV"] = "production"
        get_settings.cache_clear()

        try:
            with pytest.raises(HTTPException) as exc_info:
                predict_router._get_cached_serving_model(simulation_id=sim_id)
            assert exc_info.value.status_code == 503
        finally:
            os.environ["APP_ENV"] = "development"
            get_settings.cache_clear()

    def test_cross_worker_training_issuer_key_revocation(
        self, revocation_env: dict[str, Any]
    ) -> None:
        """Verifies that revoking training issuer key in TrustStore invalidates cached models and halts serving."""
        sim_id = "sim_rev_issuer_key"
        _create_and_register_champion(revocation_env, sim_id, version=1)

        # Worker 1: Warm serving cache has model loaded
        model_v1 = predict_router._get_cached_serving_model(simulation_id=sim_id)
        assert model_v1 is not None

        # Trust store revokes the signing key
        trust_store: DurableTrustStore = revocation_env["trust_store"]
        key_id = revocation_env["key_id"]
        admin_token = revocation_env["admin_token"]
        trust_store.revoke_key(key_id=key_id, admin_token=admin_token)

        # Worker 1: Serving cache revalidation detects revoked issuer key and evicts cache
        with pytest.raises(HTTPException) as exc_info:
            predict_router._get_cached_serving_model(simulation_id=sim_id)
        assert exc_info.value.status_code == 503
        assert (
            "model authority or signing key has been revoked" in exc_info.value.detail.lower()
            or "no verified active champion model artifact found" in exc_info.value.detail.lower()
        )

    def test_cross_worker_champion_promotion_invalidates_stale_cache(
        self, revocation_env: dict[str, Any]
    ) -> None:
        """Verifies that promoting a new champion immediately causes other workers to discard stale older models."""
        sim_id = "sim_promo_invalidation"
        _create_and_register_champion(revocation_env, sim_id, version=1, weight_bias=0.0)

        # Worker 1 caches v1
        model_v1 = predict_router._get_cached_serving_model(simulation_id=sim_id)
        inp = torch.ones(1, NUM_FEATURES)
        out1 = model_v1(inp).item()

        # Worker 2 registers and promotes v2 with higher weight bias
        _create_and_register_champion(revocation_env, sim_id, version=2, weight_bias=2.0)

        # Worker 1 requests model: must observe new version 2, not stale v1
        model_v2 = predict_router._get_cached_serving_model(simulation_id=sim_id)
        out2 = model_v2(inp).item()

        # Invariant: Output must reflect genuine updated weights of v2
        assert out1 != out2, f"Expected output change between v1 and v2, got {out1} == {out2}"
        active = revocation_env["registry"].get_active_version(sim_id)
        assert active["version"] == 2
