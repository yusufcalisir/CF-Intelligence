"""Production Admission Authorization Security & Sign-Off HMAC Verification Test Suite.

Covers SIGN-01 through SIGN-10:
- SIGN-01: Forged is_signature_valid=True with invalid HMAC -> champion promotion rejected.
- SIGN-02: Forged is_role_verified=True / is_identity_verified=True -> cannot bypass actual authorization.
- SIGN-03: Modified registry.json sign-off signatures -> champion resolution rejected.
- SIGN-04: Incorrect signing secret or missing authoritative signing configuration -> fail closed.
- SIGN-05: Signature copied from a different model version, artifact, or simulation -> rejected.
- SIGN-06: Same individual appearing under both required approval roles -> rejected.
- SIGN-07: Previously accepted champion whose sign-off record is modified after promotion -> subsequent serving rejected.
- SIGN-08: Genuine cryptographically valid dual sign-offs -> legitimate champion promotion and inference succeed.
- SIGN-09: Existing valid challenger workflow continues without inappropriate champion-only approval requirements.
- SIGN-10: Issuer revocation and receipt verification from 01-B.2 continue to reject compromised models.
"""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
import torch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi import HTTPException

import app.application.services.training_provenance_service as prov_service
import app.presentation.routers.predict as predict_module
from app.application.services.model_registry import (
    ModelRegistry,
    generate_signoff_signature,
)
from app.application.services.model_service import NUM_FEATURES, ModelService
from app.application.services.training_provenance_service import (
    DurableTrustStore,
    TrainingExecutionEvidence,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
)
from app.config import get_settings
from app.domain.model_serving_errors import ModelIntegrityError
from app.infrastructure.security.auth_service import AuthenticationService
from app.presentation.routers.realtime_inference import (
    reset_model_cache,
)

FEATURE_NAMES = [f"f_{i}" for i in range(NUM_FEATURES)]
TEST_SIGNING_SECRET = "super-secret-signing-key-32-bytes-minimum-len!"


def _setup_test_env(tmp_dir: str):
    """Initialize isolated test environment with trust store, verifier, and model registry."""
    reset_model_cache()
    predict_module.clear_all_serving_caches()
    storage_dir = os.path.join(tmp_dir, "storage")
    os.makedirs(storage_dir, exist_ok=True)
    trust_file = os.path.join(tmp_dir, "trust_store.json")
    admin_token = "admin-secret-token-test-12345"
    trust_store = DurableTrustStore(trust_file_path=trust_file, admin_token=admin_token)
    priv = ed25519.Ed25519PrivateKey.generate()
    pub = priv.public_key()
    pub_hex = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    kid = "issuer_k1"
    trust_store.register_approved_key(kid, pub_hex, admin_token=admin_token)
    verifier = TrainingReceiptVerifier(trust_store=trust_store)
    reg = ModelRegistry(storage_dir=storage_dir, verifier=verifier)
    auth_file = os.path.join(tmp_dir, "auth_store.json")
    auth_svc = AuthenticationService(
        signing_secret="test-jwt-secret-for-signoff-auth-32bytes!",
        store_path=auth_file,
        authority_secret="test-authority-secret-32bytes-len!",
        app_env="development",
    )
    AuthenticationService._instance = auth_svc
    return storage_dir, trust_store, verifier, reg, priv, kid, admin_token


def _create_authentic_candidate(
    reg: ModelRegistry,
    priv: ed25519.Ed25519PrivateKey,
    kid: str,
    sim_id: str,
    owning_bank_id: str = "bank_a",
):
    """Generate authentic candidate model with genuine receipt in development environment."""
    svc = ModelService(get_settings())
    model = svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)
    buf = io.BytesIO()
    torch.save(model.state_dict(), buf)
    model_bytes = buf.getvalue()
    sha256 = hashlib.sha256(model_bytes).hexdigest()

    issuer = TrainingReceiptIssuer(private_key=priv, key_id=kid, require_authenticated_job=False)
    evidence = TrainingExecutionEvidence(
        training_run_id=f"run_{sim_id}",
        job_id=f"job_{sim_id}",
        simulation_id=sim_id,
        completed_at=datetime.now(UTC).isoformat(),
        executed_rounds=3,
        executed_epochs=3,
        sample_count=100,
        observed_metrics={"loss": 0.05, "auc": 0.95},
        artifact_sha256=sha256,
        architecture="FraudDetectionNN",
        feature_schema_version="v1.0",
        feature_names=FEATURE_NAMES,
    )
    receipt = issuer.issue_receipt(evidence=evidence, model_bytes=model_bytes, owning_bank_id=owning_bank_id).to_dict()

    entry = reg.save_version(
        simulation_id=sim_id,
        state_dict=model.state_dict(),
        metrics={"loss": 0.05, "auc": 0.95},
        artifact_bytes=model_bytes,
        training_receipt=receipt,
        is_promoted=False,
        owning_bank_id=owning_bank_id,
    )
    return entry, model, model_bytes, sha256, receipt


def _create_users_and_signoffs(
    sim_id: str,
    version: int,
    sha256: str,
    secret: str = TEST_SIGNING_SECRET,
):
    """Register authorized approver users and compute authentic HMAC sign-offs."""
    auth_svc = AuthenticationService.get_instance()
    u_ml = auth_svc.register_user(
        f"ml_{sim_id}_{version}", "Pass123!Secure", "bank_a", roles=["ml_engineer"], clearance_level=2
    )
    u_cp = auth_svc.register_user(
        f"cp_{sim_id}_{version}", "Pass123!Secure", "bank_a", roles=["compliance"], clearance_level=3
    )

    sig_ml = generate_signoff_signature(sim_id, version, sha256, "ml_engineer", u_ml.username, signing_secret=secret)
    sig_cp = generate_signoff_signature(sim_id, version, sha256, "compliance", u_cp.username, signing_secret=secret)

    sign_offs = [
        {
            "role": "ml_engineer",
            "user": u_ml.username,
            "signature": sig_ml,
            "signature_scheme": "APPLICATION_HMAC_SHA256",
            "is_signature_valid": True,
            "is_role_verified": True,
            "is_identity_verified": True,
            "payload_bound": f"{sim_id}:{version}:{sha256}:ml_engineer:{u_ml.username}",
            "timestamp": datetime.now(UTC).isoformat(),
            "fairness_score": 1.0,
            "bias_metric": 0.0,
            "drift_divergence": 0.0,
            "is_revoked": False,
        },
        {
            "role": "compliance",
            "user": u_cp.username,
            "signature": sig_cp,
            "signature_scheme": "APPLICATION_HMAC_SHA256",
            "is_signature_valid": True,
            "is_role_verified": True,
            "is_identity_verified": True,
            "payload_bound": f"{sim_id}:{version}:{sha256}:compliance:{u_cp.username}",
            "timestamp": datetime.now(UTC).isoformat(),
            "fairness_score": 1.0,
            "bias_metric": 0.0,
            "drift_divergence": 0.0,
            "is_revoked": False,
        },
    ]
    token_ml = auth_svc.create_token_bundle(u_ml).access_token
    return sign_offs, u_ml.username, u_cp.username, token_ml


def test_sign01_forged_is_signature_valid_rejected():
    """SIGN-01: Forged is_signature_valid=True with invalid HMAC -> champion promotion rejected."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage_dir, _, verifier, reg, priv, kid, _ = _setup_test_env(tmp_dir)
        sim_id = "sim_sign01"
        entry, _, _, sha256, _ = _create_authentic_candidate(reg, priv, kid, sim_id)
        v = entry["version"]

        # Attacker injects forged sign-offs with invalid signatures but is_signature_valid=True
        manifest = reg._load_manifest(sim_id)
        manifest[0]["sign_offs"] = [
            {
                "role": "ml_engineer",
                "user": "attacker_ml",
                "signature": "bogus_signature_not_matching_hmac",
                "is_signature_valid": True,
                "is_role_verified": True,
                "is_identity_verified": True,
            },
            {
                "role": "compliance",
                "user": "attacker_cp",
                "signature": "bogus_signature_not_matching_hmac_2",
                "is_signature_valid": True,
                "is_role_verified": True,
                "is_identity_verified": True,
            },
        ]
        reg._save_manifest(sim_id, manifest)

        settings = get_settings()
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(settings, "payload_signing_secret", TEST_SIGNING_SECRET),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
        ):
            with pytest.raises(ModelIntegrityError, match="lacks valid cryptographic signature"):
                reg.promote_version(sim_id, v, target_status="champion")


def test_sign02_forged_role_verification_cannot_bypass():
    """SIGN-02: Forged is_role_verified=True / is_identity_verified=True -> cannot bypass actual authorization."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage_dir, _, verifier, reg, priv, kid, _ = _setup_test_env(tmp_dir)
        sim_id = "sim_sign02"
        entry, _, _, sha256, _ = _create_authentic_candidate(reg, priv, kid, sim_id)
        v = entry["version"]

        # Case A: Seeded development account claiming verified role
        sig_admin = generate_signoff_signature(sim_id, v, sha256, "ml_engineer", "admin", signing_secret=TEST_SIGNING_SECRET)
        sig_cp = generate_signoff_signature(sim_id, v, sha256, "compliance", "authorized_cp", signing_secret=TEST_SIGNING_SECRET)

        manifest = reg._load_manifest(sim_id)
        manifest[0]["sign_offs"] = [
            {
                "role": "ml_engineer",
                "user": "admin",  # Seeded account
                "signature": sig_admin,
                "is_signature_valid": True,
                "is_role_verified": True,
                "is_identity_verified": True,
            },
            {
                "role": "compliance",
                "user": "authorized_cp",
                "signature": sig_cp,
                "is_signature_valid": True,
                "is_role_verified": True,
                "is_identity_verified": True,
            },
        ]
        reg._save_manifest(sim_id, manifest)

        settings = get_settings()
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(settings, "payload_signing_secret", TEST_SIGNING_SECRET),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
        ):
            with pytest.raises(ModelIntegrityError, match="development seeded account 'admin' cannot authorize"):
                reg.promote_version(sim_id, v, target_status="champion")

            # Case B: Registered user with unauthorized role (analyst attempting ml_engineer approval)
            auth_svc = AuthenticationService.get_instance()
            u_analyst = auth_svc.register_user(f"analyst_{sim_id}", "Pass123!Secure", "bank_a", roles=["analyst"])
            sig_analyst = generate_signoff_signature(sim_id, v, sha256, "ml_engineer", u_analyst.username, signing_secret=TEST_SIGNING_SECRET)

            manifest[0]["sign_offs"][0] = {
                "role": "ml_engineer",
                "user": u_analyst.username,
                "signature": sig_analyst,
                "is_signature_valid": True,
                "is_role_verified": True,  # Forged flag
                "is_identity_verified": True,
            }
            reg._save_manifest(sim_id, manifest)

            with pytest.raises(ModelIntegrityError, match="lacks authoritative ml_engineer role"):
                reg.promote_version(sim_id, v, target_status="champion")


def test_sign03_modified_registry_signatures_champion_resolution_rejected():
    """SIGN-03: Modified registry.json sign-off signatures -> champion resolution rejected."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage_dir, _, verifier, reg, priv, kid, _ = _setup_test_env(tmp_dir)
        sim_id = "sim_sign03"
        entry, _, _, sha256, _ = _create_authentic_candidate(reg, priv, kid, sim_id)
        v = entry["version"]
        sign_offs, _, _, token = _create_users_and_signoffs(sim_id, v, sha256)

        # Legitimate promotion
        manifest = reg._load_manifest(sim_id)
        manifest[0]["sign_offs"] = sign_offs
        reg._save_manifest(sim_id, manifest)

        settings = get_settings()
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(settings, "payload_signing_secret", TEST_SIGNING_SECRET),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
        ):
            promoted = reg.promote_version(sim_id, v, target_status="champion", auth_token=token)
            assert promoted["status"] == "champion"

            # Attacker modifies the signature in registry.json post-promotion
            manifest_tampered = reg._load_manifest(sim_id)
            manifest_tampered[0]["sign_offs"][0]["signature"] = "deadbeef" * 8
            reg._save_manifest(sim_id, manifest_tampered)

            # Champion resolution must fail closed
            with pytest.raises(ModelIntegrityError, match="lacks valid cryptographic signature"):
                reg.resolve_and_verify_champion(sim_id)


def test_sign04_missing_or_invalid_signing_secret_fails_closed():
    """SIGN-04: Incorrect signing secret or missing authoritative signing configuration -> fail closed."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage_dir, _, verifier, reg, priv, kid, _ = _setup_test_env(tmp_dir)
        sim_id = "sim_sign04"
        entry, _, _, sha256, _ = _create_authentic_candidate(reg, priv, kid, sim_id)
        v = entry["version"]
        sign_offs, _, _, _ = _create_users_and_signoffs(sim_id, v, sha256)

        manifest = reg._load_manifest(sim_id)
        manifest[0]["sign_offs"] = sign_offs
        reg._save_manifest(sim_id, manifest)

        settings = get_settings()
        # In production with empty signing secret
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(settings, "payload_signing_secret", ""),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
        ):
            with pytest.raises(ModelIntegrityError, match="authoritative signing configuration is missing or invalid"):
                reg.promote_version(sim_id, v, target_status="champion")

        # In production with too short / insecure signing secret
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(settings, "payload_signing_secret", "short-key"),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
        ):
            with pytest.raises(ModelIntegrityError, match="authoritative signing configuration is missing or invalid"):
                reg.promote_version(sim_id, v, target_status="champion")


def test_sign05_signature_copied_from_different_version_or_sim_rejected():
    """SIGN-05: Signature copied from a different model version, artifact, or simulation -> rejected."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage_dir, _, verifier, reg, priv, kid, _ = _setup_test_env(tmp_dir)
        sim_a = "sim_sign05_a"
        sim_b = "sim_sign05_b"

        # Model A
        entry_a, _, _, sha256_a, _ = _create_authentic_candidate(reg, priv, kid, sim_a)
        sign_offs_a, _, _, _ = _create_users_and_signoffs(sim_a, entry_a["version"], sha256_a)

        # Model B
        entry_b, _, _, sha256_b, _ = _create_authentic_candidate(reg, priv, kid, sim_b)

        # Replay Model A's signatures onto Model B
        manifest_b = reg._load_manifest(sim_b)
        manifest_b[0]["sign_offs"] = sign_offs_a
        reg._save_manifest(sim_b, manifest_b)

        settings = get_settings()
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(settings, "payload_signing_secret", TEST_SIGNING_SECRET),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
        ):
            with pytest.raises(ModelIntegrityError, match="sign-off payload binding mismatch|lacks valid cryptographic signature"):
                reg.promote_version(sim_b, entry_b["version"], target_status="champion")


def test_sign06_four_eyes_same_individual_both_roles_rejected():
    """SIGN-06: Same individual appearing under both required approval roles -> rejected."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage_dir, _, verifier, reg, priv, kid, _ = _setup_test_env(tmp_dir)
        sim_id = "sim_sign06"
        entry, _, _, sha256, _ = _create_authentic_candidate(reg, priv, kid, sim_id)
        v = entry["version"]

        auth_svc = AuthenticationService.get_instance()
        u_dual = auth_svc.register_user(f"dual_{sim_id}", "Pass123!Secure", "bank_a", roles=["ml_engineer", "compliance"])

        sig_ml = generate_signoff_signature(sim_id, v, sha256, "ml_engineer", u_dual.username, signing_secret=TEST_SIGNING_SECRET)
        sig_cp = generate_signoff_signature(sim_id, v, sha256, "compliance", u_dual.username, signing_secret=TEST_SIGNING_SECRET)

        manifest = reg._load_manifest(sim_id)
        manifest[0]["sign_offs"] = [
            {
                "role": "ml_engineer",
                "user": u_dual.username,
                "signature": sig_ml,
                "is_signature_valid": True,
                "is_role_verified": True,
                "is_identity_verified": True,
            },
            {
                "role": "compliance",
                "user": u_dual.username,
                "signature": sig_cp,
                "is_signature_valid": True,
                "is_role_verified": True,
                "is_identity_verified": True,
            },
        ]
        reg._save_manifest(sim_id, manifest)

        settings = get_settings()
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(settings, "payload_signing_secret", TEST_SIGNING_SECRET),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
        ):
            with pytest.raises(ModelIntegrityError, match="Four-Eyes Principle violation"):
                reg.promote_version(sim_id, v, target_status="champion")


def test_sign07_tampered_signoff_record_rejects_subsequent_serving():
    """SIGN-07: Previously accepted champion whose sign-off record is modified after promotion -> subsequent serving rejected."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage_dir, _, verifier, reg, priv, kid, _ = _setup_test_env(tmp_dir)
        sim_id = "sim_sign07"
        entry, _, _, sha256, _ = _create_authentic_candidate(reg, priv, kid, sim_id)
        v = entry["version"]
        sign_offs, _, _, token = _create_users_and_signoffs(sim_id, v, sha256)

        manifest = reg._load_manifest(sim_id)
        manifest[0]["sign_offs"] = sign_offs
        reg._save_manifest(sim_id, manifest)

        settings = get_settings()
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(settings, "payload_signing_secret", TEST_SIGNING_SECRET),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
            patch.object(predict_module, "_registry", reg),
            patch.dict(os.environ, {"CFI_STORAGE_DIR": storage_dir}),
        ):
            promoted = reg.promote_version(sim_id, v, target_status="champion", auth_token=token)
            assert promoted["status"] == "champion"

            # Warm serving cache with valid champion
            model_cached = predict_module._get_cached_serving_model(sim_id)
            assert model_cached is not None

            # Attacker modifies the sign-off record in registry.json post-promotion
            manifest_tampered = reg._load_manifest(sim_id)
            manifest_tampered[0]["sign_offs"][1]["signature"] = "corrupted_signature_123"
            reg._save_manifest(sim_id, manifest_tampered)

            # Active champion identity check must fail
            active_ident = reg.get_active_champion_identity(sim_id)
            assert active_ident is None

            # Subsequent serving must fail closed (evict cache and raise 503)
            with pytest.raises(HTTPException) as exc_info:
                predict_module._get_cached_serving_model(sim_id)
            assert exc_info.value.status_code == 503
            assert "champion authorization" in exc_info.value.detail


def test_sign08_genuine_dual_signoffs_promotion_and_inference_succeed():
    """SIGN-08: Genuine cryptographically valid dual sign-offs -> legitimate champion promotion and inference succeed."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage_dir, _, verifier, reg, priv, kid, _ = _setup_test_env(tmp_dir)
        sim_id = "sim_sign08"
        entry, model, raw_bytes, sha256, rcpt = _create_authentic_candidate(reg, priv, kid, sim_id)
        v = entry["version"]
        sign_offs, u_ml, u_cp, token = _create_users_and_signoffs(sim_id, v, sha256)

        manifest = reg._load_manifest(sim_id)
        manifest[0]["sign_offs"] = sign_offs
        reg._save_manifest(sim_id, manifest)

        settings = get_settings()
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(settings, "payload_signing_secret", TEST_SIGNING_SECRET),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
            patch.object(predict_module, "_registry", reg),
            patch.dict(os.environ, {"CFI_STORAGE_DIR": storage_dir}),
        ):
            # Legitimate promotion succeeds
            promoted = reg.promote_version(sim_id, v, target_status="champion", auth_token=token)
            assert promoted["status"] == "champion"
            assert promoted["is_active"] is True

            # Resolution succeeds
            resolved = reg.resolve_and_verify_champion(sim_id)
            assert resolved is not None
            path, bdata, meta = resolved
            assert meta["authenticity_status"] == "APPLICATION_HMAC_AUTHENTICATED"
            assert meta["sha256"] == sha256

            # Serving loads and evaluates properly
            serving_model = predict_module._get_cached_serving_model(sim_id)
            assert serving_model is not None
            dummy_input = torch.zeros((1, NUM_FEATURES))
            with torch.no_grad():
                pred = serving_model(dummy_input)
            assert pred is not None


def test_sign09_challenger_evaluation_continues_without_champion_dual_signoffs():
    """SIGN-09: Existing valid challenger workflow continues without inappropriate champion-only approval requirements."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage_dir, _, verifier, reg, priv, kid, _ = _setup_test_env(tmp_dir)
        sim_id = "sim_sign09"

        # Create valid candidate with receipt but WITHOUT dual champion sign-offs
        entry, _, _, _, _ = _create_authentic_candidate(reg, priv, kid, sim_id)
        v = entry["version"]

        settings = get_settings()
        auth_svc = AuthenticationService.get_instance()
        u_ml = auth_svc.register_user(f"ml_{sim_id}", "Pass123!Secure", "bank_a", roles=["ml_engineer"], clearance_level=2)
        token_ml = auth_svc.create_token_bundle(u_ml).access_token
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(settings, "payload_signing_secret", TEST_SIGNING_SECRET),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
        ):
            # Challenger promotion must succeed without demanding champion dual sign-offs
            promoted_chall = reg.promote_version(sim_id, v, target_status="challenger", auth_token=token_ml)
            assert promoted_chall["status"] == "challenger"
            assert promoted_chall["is_active"] is False

            # Challenger resolution succeeds
            resolved_chall = reg.resolve_and_verify_challenger(sim_id)
            assert resolved_chall is not None
            path, bdata, meta = resolved_chall
            assert meta["status"] == "challenger"


def test_sign10_issuer_revocation_from_01_b2_continues_to_reject():
    """SIGN-10: Issuer revocation and receipt verification from 01-B.2 continue to reject compromised models."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage_dir, trust_store, verifier, reg, priv, kid, admin_token = _setup_test_env(tmp_dir)
        sim_id = "sim_sign10"
        entry, _, _, sha256, _ = _create_authentic_candidate(reg, priv, kid, sim_id)
        v = entry["version"]
        sign_offs, _, _, token = _create_users_and_signoffs(sim_id, v, sha256)

        manifest = reg._load_manifest(sim_id)
        manifest[0]["sign_offs"] = sign_offs
        reg._save_manifest(sim_id, manifest)

        settings = get_settings()
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(settings, "payload_signing_secret", TEST_SIGNING_SECRET),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
        ):
            promoted = reg.promote_version(sim_id, v, target_status="champion", auth_token=token)
            assert promoted["status"] == "champion"

            # Revoke issuer key
            trust_store.revoke_key(kid, reason="Key compromise test", admin_token=admin_token)

            # Champion resolution must reject despite valid sign-offs
            with pytest.raises(ModelIntegrityError, match="Receipt key_id 'issuer_k1' has been explicitly revoked"):
                reg.resolve_and_verify_champion(sim_id)
