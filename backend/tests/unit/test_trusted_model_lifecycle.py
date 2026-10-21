"""Trusted model lifecycle verification test suite.

Validates end-to-end security and correctness contracts for machine learning models:
- Cryptographic training provenance and receipt authority verification
- Four-eyes principle dual-role human sign-off production admission
- Atomic manifest persistence, monotonic versioning, and concurrent registry safety
- Authenticated model cache envelopes, generation tracking, and invalidation
- Production inference feature completeness and canonical preprocessing parity
- End-to-end model promotion, rollback, and serving integrity
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import hmac
import io
import json
import os
import shutil
import tempfile
from unittest.mock import patch

import numpy as np
import pytest
import torch
from fastapi.testclient import TestClient

from app.application.services.data_generator import FEATURE_NAMES, preprocess_transaction
from app.application.services.model_registry import (
    ModelRegistry,
    generate_signoff_signature,
    validate_production_admission,
)
from app.application.services.model_service import NUM_FEATURES, ModelService
from app.application.services.training_provenance_service import (
    DOMAIN_MODEL_CACHE,
    TrainingReceiptAuthority,
    derive_domain_key,
)
from app.config import get_settings
from app.domain.model_serving_errors import (
    ModelIntegrityError,
    ModelNotAvailableError,
)
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.oidc_authenticator import OIDCAuthenticator
from app.main import app
from app.presentation.routers.realtime_inference import (
    LocalModelCache,
    reset_circuit_breaker,
    reset_model_cache,
)


def _auth_headers() -> dict[str, str]:
    token = OIDCAuthenticator().create_mock_token(username="analyst_a", bank_id="bank_a", roles=["analyst"])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def temp_reg_dir():
    d = tempfile.mkdtemp(prefix="cfi_test_model_lifecycle_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain
    reset_circuit_breaker()
    reset_model_cache()
    temp_audit_dir = tempfile.mkdtemp(prefix="cfi_audit_lifecycle_")
    monkeypatch.setenv("CFI_AUDIT_LEDGER_PATH", os.path.join(temp_audit_dir, "audit.json"))
    monkeypatch.setenv("CFI_AUDIT_LEDGER_SECRET", "cfi_prod_audit_ledger_signing_secret_64bytes!")
    settings = get_settings()
    orig_payload_sec = settings.payload_signing_secret
    orig_jwt_sec = settings.oidc_jwt_signing_secret
    settings.payload_signing_secret = "cfi_prod_payload_signing_secret_64bytes!"
    settings.oidc_jwt_signing_secret = "cfi_prod_jwt_signing_secret_64bytes_entropy!"
    ImmutableAuditChain.reset_instance()
    AuthenticationService.reset_instance()
    AuthenticationService.get_instance()
    yield
    settings.payload_signing_secret = orig_payload_sec
    settings.oidc_jwt_signing_secret = orig_jwt_sec
    reset_circuit_breaker()
    reset_model_cache()
    AuthenticationService.reset_instance()
    ImmutableAuditChain.reset_instance()
    shutil.rmtree(temp_audit_dir, ignore_errors=True)


def _bind_sim_for_test(reg: ModelRegistry, sim_id: str, bank_id: str = "bank_a") -> None:
    auth_svc = AuthenticationService.get_instance()
    admin_user = auth_svc.get_user("admin")
    if admin_user:
        admin_tok = auth_svc.create_token_bundle(admin_user).access_token
        reg.bind_simulation_ownership(sim_id, bank_id, auth_token=admin_tok)


# ==============================================================================
# SECTION 1: CRYPTOGRAPHIC TRAINING PROVENANCE & RECEIPT VERIFICATION
# ==============================================================================


def test_random_checkpoint_with_fabricated_run_id_classified_unverified(temp_reg_dir: str):
    """Checks that a random checkpoint submitted with a fabricated training run ID is classified as UNVERIFIED."""
    svc = ModelService(get_settings())
    reg = ModelRegistry(storage_dir=temp_reg_dir)
    m = svc.create_model(dp_compatible=True)

    entry = reg.save_version(
        "sim_unverified_run",
        m.state_dict(),
        metrics={"auc": 0.50},
        is_promoted=False,
        training_run_id="fabricated_run_123",
        provenance_type="TRAINED",
    )
    assert entry["provenance_type"] == "UNVERIFIED"
    assert entry["is_production_eligible"] is False


def test_random_checkpoint_with_plausible_metadata_classified_unverified(temp_reg_dir: str):
    """Checks that a random checkpoint with caller-invented finite loss and epoch metadata is classified as UNVERIFIED."""
    svc = ModelService(get_settings())
    reg = ModelRegistry(storage_dir=temp_reg_dir)
    m = svc.create_model(dp_compatible=True)

    entry = reg.save_version(
        "sim_plausible_meta",
        m.state_dict(),
        metrics={"loss": 0.12, "auc": 0.95},
        is_promoted=False,
        training_run_id="run_fake_99",
        training_evidence={"loss": 0.12, "epochs": 5, "batch_size": 64},
        provenance_type="UNVERIFIED",
    )
    assert entry["provenance_type"] == "UNVERIFIED"


def test_receipt_with_forged_signature_rejected_by_authority(temp_reg_dir: str):
    """Checks that a receipt with a forged or invalid signature is rejected by the verification authority."""
    auth = TrainingReceiptAuthority("secret_auth_1")
    fake_receipt = {
        "receipt_id": "rcpt_fake_1",
        "training_run_id": "run_fake",
        "job_id": "job_fake",
        "model_artifact_sha256": "0" * 64,
        "architecture": "FraudDetectionModel-GroupNorm",
        "feature_schema_version": "v1.0",
        "input_dim": 10,
        "feature_names": FEATURE_NAMES,
        "simulation_id": "sim_forged_sig",
        "status": "COMPLETED",
        "executed_rounds": 5,
        "executed_epochs": 1,
        "sample_count": 1000,
        "metrics": {"loss": 0.2},
        "producer_id": "FORGED",
        "schema_version": "1.0",
        "created_at": "2026-10-10T00:00:00Z",
        "signature": "bad_signature_hex",
        "key_id": auth.key_id,
    }
    is_valid, reason = auth.verify_receipt(fake_receipt, candidate_artifact_sha256="0" * 64, simulation_id="sim_forged_sig")
    assert not is_valid
    assert "Cryptographic signature mismatch" in reason or "untrusted" in reason.lower()


def test_modified_artifact_bytes_fails_receipt_digest_verification(temp_reg_dir: str):
    """Checks that presenting a genuine receipt against modified model artifact bytes fails SHA-256 binding."""
    auth = TrainingReceiptAuthority("secret_auth_1")
    real_sha = hashlib.sha256(b"original_model_bytes").hexdigest()
    receipt = auth.issue_receipt(
        training_run_id="run_real_1",
        job_id="job_real_1",
        model_artifact_sha256=real_sha,
        simulation_id="sim_modified_bytes",
        executed_rounds=3,
        executed_epochs=1,
        sample_count=500,
        metrics={"loss": 0.15},
    )
    tampered_sha = hashlib.sha256(b"modified_model_bytes").hexdigest()
    is_valid, reason = auth.verify_receipt(receipt, candidate_artifact_sha256=tampered_sha, simulation_id="sim_modified_bytes")
    assert not is_valid
    assert "Artifact digest mismatch" in reason


def test_receipt_for_different_simulation_rejected(temp_reg_dir: str):
    """Checks that a receipt issued for simulation A cannot be replayed for simulation B."""
    auth = TrainingReceiptAuthority("secret_auth_1")
    real_sha = hashlib.sha256(b"model_bytes").hexdigest()
    receipt = auth.issue_receipt(
        training_run_id="run_sim_a",
        job_id="job_1",
        model_artifact_sha256=real_sha,
        simulation_id="sim_bank_alpha",
        executed_rounds=3,
        executed_epochs=1,
        sample_count=500,
        metrics={"loss": 0.15},
    )
    is_valid, reason = auth.verify_receipt(receipt, candidate_artifact_sha256=real_sha, simulation_id="sim_bank_beta")
    assert not is_valid
    assert "Simulation scope mismatch" in reason


def test_receipt_signed_by_untrusted_key_rejected(temp_reg_dir: str):
    """Checks that a receipt signed with an untrusted key is rejected by the production authority."""
    untrusted_auth = TrainingReceiptAuthority("attacker_private_key")
    real_sha = hashlib.sha256(b"model_bytes").hexdigest()
    receipt = untrusted_auth.issue_receipt(
        training_run_id="run_attacker",
        job_id="job_1",
        model_artifact_sha256=real_sha,
        simulation_id="sim_untrusted_key",
        executed_rounds=3,
        executed_epochs=1,
        sample_count=500,
        metrics={"loss": 0.15},
    )
    prod_auth = TrainingReceiptAuthority("official_system_signing_key")
    is_valid, reason = prod_auth.verify_receipt(receipt, candidate_artifact_sha256=real_sha, simulation_id="sim_untrusted_key")
    assert not is_valid
    assert "signature mismatch" in reason.lower() or "not present in trusted" in reason.lower()


def test_receipt_with_tampered_metadata_rejected(temp_reg_dir: str):
    """Checks that tampering with signed receipt fields invalidates HMAC signature verification."""
    auth = TrainingReceiptAuthority("secret_auth_1")
    real_sha = hashlib.sha256(b"model_bytes").hexdigest()
    receipt = auth.issue_receipt(
        training_run_id="run_orig",
        job_id="job_orig",
        model_artifact_sha256=real_sha,
        simulation_id="sim_tampered_meta",
        executed_rounds=2,
        executed_epochs=1,
        sample_count=100,
        metrics={"loss": 0.40},
    )
    receipt_dict = receipt.to_dict()
    receipt_dict["metrics"]["loss"] = 0.05
    is_valid, reason = auth.verify_receipt(receipt_dict, candidate_artifact_sha256=real_sha, simulation_id="sim_tampered_meta")
    assert not is_valid
    assert "signature mismatch" in reason.lower()


def test_genuine_local_training_produces_verifiable_record(temp_reg_dir: str):
    """Verifies that legitimate local training produces a verifiable model artifact and receipt."""
    svc = ModelService(get_settings())
    reg = ModelRegistry(storage_dir=temp_reg_dir)
    m = svc.create_model(dp_compatible=True)
    X = np.random.randn(64, NUM_FEATURES).astype(np.float32)
    y = np.random.randint(0, 2, size=(64,)).astype(np.float32)
    trained, losses, _ = svc.train_local(m, X, y, epochs=2, batch_size=32)

    buf = io.BytesIO()
    torch.save(trained.state_dict(), buf)
    art_sha = hashlib.sha256(buf.getvalue()).hexdigest()

    auth = TrainingReceiptAuthority()
    rcpt = auth.issue_receipt(
        training_run_id="run_local_08",
        job_id="job_local_08",
        model_artifact_sha256=art_sha,
        simulation_id="sim_genuine_local",
        executed_rounds=1,
        executed_epochs=2,
        sample_count=64,
        metrics={"loss": float(losses[-1])},
    )

    entry = reg.save_version(
        "sim_genuine_local",
        trained.state_dict(),
        metrics={"loss": float(losses[-1])},
        is_promoted=False,
        training_run_id="run_local_08",
        training_receipt=rcpt,
        provenance_type="TRAINING_RECORDED",
        artifact_bytes=buf.getvalue(),
    )
    assert entry["provenance_type"] == "TRAINING_RECORDED"
    assert entry["is_production_eligible"] is True
    assert entry["training_receipt"]["signature"] == rcpt.signature


def test_federated_training_registers_through_trusted_path(temp_reg_dir: str):
    """Verifies that genuine federated aggregation output binds execution metadata in a signed receipt."""
    svc = ModelService(get_settings())
    m1 = svc.create_model(dp_compatible=True)
    m2 = svc.create_model(dp_compatible=True)

    X1 = np.random.randn(64, NUM_FEATURES).astype(np.float32)
    y1 = np.random.randint(0, 2, size=(64,)).astype(np.float32)
    X2 = np.random.randn(64, NUM_FEATURES).astype(np.float32)
    y2 = np.random.randint(0, 2, size=(64,)).astype(np.float32)

    trained1, losses1, _ = svc.train_local(m1, X1, y1, epochs=1, batch_size=32)
    trained2, losses2, _ = svc.train_local(m2, X2, y2, epochs=1, batch_size=32)

    # Genuine federated aggregation (FedAvg)
    sd1 = trained1.state_dict()
    sd2 = trained2.state_dict()
    agg_sd = {}
    for k in sd1:
        agg_sd[k] = (sd1[k].float() + sd2[k].float()) / 2.0

    buf = io.BytesIO()
    torch.save(agg_sd, buf)
    model_bytes = buf.getvalue()
    art_sha = hashlib.sha256(model_bytes).hexdigest()

    auth = TrainingReceiptAuthority()
    rcpt = auth.issue_receipt(
        training_run_id="fl_sim_round_10",
        job_id="fl_agg_job_42",
        model_artifact_sha256=art_sha,
        simulation_id="sim_federated_path",
        executed_rounds=10,
        executed_epochs=3,
        sample_count=150000,
        metrics={"loss": 0.08, "auc_roc": 0.88},
        feature_names=FEATURE_NAMES,
        producer_id="FEDERATED_AGGREGATION_ENGINE",
    )
    valid, msg = auth.verify_receipt(rcpt, candidate_artifact_sha256=art_sha, simulation_id="sim_federated_path")
    assert valid
    assert rcpt.executed_rounds == 10
    assert rcpt.producer_id == "FEDERATED_AGGREGATION_ENGINE"


def test_recorded_evidence_cannot_upgrade_to_hardware_verified(temp_reg_dir: str):
    """Checks that caller-supplied numeric metadata cannot establish trusted provenance without a receipt."""
    reg = ModelRegistry(storage_dir=temp_reg_dir)
    svc = ModelService(get_settings())
    m = svc.create_model(dp_compatible=True)

    entry = reg.save_version(
        "sim_evidence_attestation",
        m.state_dict(),
        metrics={"auc": 0.85},
        is_promoted=False,
        provenance_type="TRAINING_RECORDED",
        training_run_id="run_10",
        training_evidence={"loss": 0.2, "epochs": 1, "batch_size": 32},
    )
    assert entry["provenance_type"] != "TRAINING_EVIDENCE_VERIFIED"
    assert entry["provenance_type"] == "UNVERIFIED"
    assert entry["is_production_eligible"] is False


# ==============================================================================
# SECTION 2: PRODUCTION ADMISSION & DUAL-ROLE AUTHORIZATION
# ==============================================================================


def test_direct_production_save_unauthorized_promotion_rejected(temp_reg_dir: str):
    """Verifies that saving an unverified model with is_promoted=True fails closed in production."""
    reg = ModelRegistry(storage_dir=temp_reg_dir)
    svc = ModelService(get_settings())
    m = svc.create_model(dp_compatible=True)

    with (
        patch.object(get_settings(), "app_env", "production"),
        pytest.raises(ModelIntegrityError, match="Production admission rejected: model provenance 'UNVERIFIED'"),
    ):
        reg.save_version(
            "sim_unauthorized_save",
            m.state_dict(),
            metrics={"auc": 0.80},
            is_promoted=True,
            provenance_type="UNVERIFIED",
        )


def test_direct_promote_version_of_unverified_model_rejected(temp_reg_dir: str):
    """Verifies that direct promote_version() of an unverified model in production is rejected."""
    reg = ModelRegistry(storage_dir=temp_reg_dir)
    svc = ModelService(get_settings())
    m = svc.create_model(dp_compatible=True)

    reg.save_version("sim_unverified_promote", m.state_dict(), metrics={"auc": 0.80}, is_promoted=False, provenance_type="UNVERIFIED")

    with (
        patch.object(get_settings(), "app_env", "production"),
        pytest.raises(ModelIntegrityError, match="Production admission rejected: model provenance 'UNVERIFIED'"),
    ):
        reg.promote_version("sim_unverified_promote", version=1, target_status="champion")


def test_direct_rollback_to_unverified_model_rejected(temp_reg_dir: str):
    """Verifies that rolling back to an unverified historical model in production is rejected."""
    reg = ModelRegistry(storage_dir=temp_reg_dir)
    svc = ModelService(get_settings())
    m = svc.create_model(dp_compatible=True)

    reg.save_version("sim_unverified_rollback", m.state_dict(), metrics={"auc": 0.70}, is_promoted=False, provenance_type="UNVERIFIED")

    with (
        patch.object(get_settings(), "app_env", "production"),
        pytest.raises(ModelIntegrityError, match="Production admission rejected: model provenance 'UNVERIFIED'"),
    ):
        reg.rollback("sim_unverified_rollback", version=1)


def test_signoff_with_unregistered_username_marked_unverified(temp_reg_dir: str):
    """Checks that sign-off with a username not in AuthenticationService is rejected fail-closed."""
    reg = ModelRegistry(storage_dir=temp_reg_dir)
    _bind_sim_for_test(reg, "sim_ghost_user")
    svc = ModelService(get_settings())
    m = svc.create_model(dp_compatible=True)

    entry = reg.save_version("sim_ghost_user", m.state_dict(), {"auc": 0.80}, is_promoted=False)
    sig = generate_signoff_signature("sim_ghost_user", 1, entry["sha256"], "ml_engineer", "ghost_user")
    with pytest.raises(ModelIntegrityError, match="not an active authoritative user"):
        reg.sign_off("sim_ghost_user", 1, "ml_engineer", user="ghost_user", signature=sig)


def test_unauthenticated_signoff_caller_cannot_promote_in_production(temp_reg_dir: str):
    """Checks that in production, sign-offs without verified authenticated token cannot promote a model."""
    reg = ModelRegistry(storage_dir=temp_reg_dir)
    _bind_sim_for_test(reg, "sim_unauth_promote")
    svc = ModelService(get_settings())
    m = svc.create_model(dp_compatible=True)

    entry = reg.save_version(
        "sim_unauth_promote",
        m.state_dict(),
        {"loss": 0.1},
        is_promoted=False,
        provenance_type="TRAINING_RECORDED",
        training_run_id="run_15",
        training_evidence={"loss": 0.1, "epochs": 1, "batch_size": 32},
    )
    sig_ml = generate_signoff_signature("sim_unauth_promote", 1, entry["sha256"], "ml_engineer", "fake_user_1")

    with (
        patch.object(get_settings(), "app_env", "production"),
        pytest.raises(ModelIntegrityError, match="authenticated session token is required for institutional sign-off"),
    ):
        reg.sign_off("sim_unauth_promote", 1, "ml_engineer", user="fake_user_1", signature=sig_ml)


def test_user_lacking_required_role_rejected_from_signoff(temp_reg_dir: str):
    """Checks that a user lacking the compliance role cannot approve a compliance sign-off in production."""
    auth_svc = AuthenticationService.get_instance()
    u_ana = auth_svc.register_user("tech_analyst", "Secret123!", "bank_a", roles=["analyst"])
    u_mle = auth_svc.register_user("real_mle", "Secret123!", "bank_a", roles=["ml_engineer"])

    tok_mle = auth_svc.create_token_bundle(u_mle).access_token
    tok_ana = auth_svc.create_token_bundle(u_ana).access_token

    reg = ModelRegistry(storage_dir=temp_reg_dir)
    _bind_sim_for_test(reg, "sim_lacking_role")
    svc = ModelService(get_settings())
    m = svc.create_model(dp_compatible=True)

    entry = reg.save_version(
        "sim_lacking_role",
        m.state_dict(),
        {"loss": 0.2},
        is_promoted=False,
        provenance_type="TRAINING_RECORDED",
        training_run_id="run_16",
        training_evidence={"loss": 0.2, "epochs": 1, "batch_size": 32},
    )
    sig_mle = generate_signoff_signature("sim_lacking_role", 1, entry["sha256"], "ml_engineer", "real_mle")
    sig_ana = generate_signoff_signature("sim_lacking_role", 1, entry["sha256"], "compliance", "tech_analyst")

    reg.sign_off("sim_lacking_role", 1, "ml_engineer", user="real_mle", signature=sig_mle, auth_token=tok_mle)
    with (
        patch.object(get_settings(), "app_env", "production"),
        pytest.raises(ModelIntegrityError, match="token claims lack compliance role authority"),
    ):
        reg.sign_off("sim_lacking_role", 1, "compliance", user="tech_analyst", signature=sig_ana, auth_token=tok_ana)


def test_single_principal_attempting_both_signoff_roles_rejected(temp_reg_dir: str):
    """Enforces the four-eyes principle preventing a single user from approving both engineering and compliance roles."""
    auth_svc = AuthenticationService.get_instance()
    auth_svc.register_user("super_user", "Secret123!", "bank_a", roles=["ml_engineer", "compliance"])

    reg = ModelRegistry(storage_dir=temp_reg_dir)
    _bind_sim_for_test(reg, "sim_four_eyes")
    svc = ModelService(get_settings())
    m = svc.create_model(dp_compatible=True)

    entry = reg.save_version("sim_four_eyes", m.state_dict(), {"loss": 0.2}, is_promoted=False)
    sig_ml = generate_signoff_signature("sim_four_eyes", 1, entry["sha256"], "ml_engineer", "super_user")
    sig_comp = generate_signoff_signature("sim_four_eyes", 1, entry["sha256"], "compliance", "super_user")

    reg.sign_off("sim_four_eyes", 1, "ml_engineer", user="super_user", signature=sig_ml)
    with pytest.raises(ValueError, match="Four-Eyes Principle violation: user 'super_user' cannot approve multiple roles"):
        reg.sign_off("sim_four_eyes", 1, "compliance", user="super_user", signature=sig_comp)


def test_signoff_replay_for_different_model_version_rejected(temp_reg_dir: str):
    """Checks that a sign-off signature for model v1 is invalid when submitted for model v2."""
    reg = ModelRegistry(storage_dir=temp_reg_dir)
    _bind_sim_for_test(reg, "sim_version_replay")
    auth_svc = AuthenticationService.get_instance()
    auth_svc.register_user("mle1", "Secret123!", "bank_a", roles=["ml_engineer"])
    svc = ModelService(get_settings())
    m = svc.create_model(dp_compatible=True)

    entry1 = reg.save_version("sim_version_replay", m.state_dict(), {"auc": 0.8}, is_promoted=False)
    reg.save_version("sim_version_replay", m.state_dict(), {"auc": 0.9}, is_promoted=False)

    sig_for_v1 = generate_signoff_signature("sim_version_replay", 1, entry1["sha256"], "ml_engineer", "mle1")
    updated = reg.sign_off("sim_version_replay", 2, "ml_engineer", user="mle1", signature=sig_for_v1)
    assert updated["sign_offs"][0]["is_signature_valid"] is False


def test_signoff_replay_for_different_simulation_rejected(temp_reg_dir: str):
    """Checks that a sign-off signature for simulation A is invalid when submitted for simulation B."""
    reg = ModelRegistry(storage_dir=temp_reg_dir)
    _bind_sim_for_test(reg, "sim_bank_x")
    _bind_sim_for_test(reg, "sim_bank_y")
    auth_svc = AuthenticationService.get_instance()
    auth_svc.register_user("mle1", "Secret123!", "bank_a", roles=["ml_engineer"])
    svc = ModelService(get_settings())
    m = svc.create_model(dp_compatible=True)

    entry1 = reg.save_version("sim_bank_x", m.state_dict(), {"auc": 0.8}, is_promoted=False)
    reg.save_version("sim_bank_y", m.state_dict(), {"auc": 0.8}, is_promoted=False)

    sig_for_x = generate_signoff_signature("sim_bank_x", 1, entry1["sha256"], "ml_engineer", "mle1")
    updated = reg.sign_off("sim_bank_y", 1, "ml_engineer", user="mle1", signature=sig_for_x)
    assert updated["sign_offs"][0]["is_signature_valid"] is False


def test_revoked_model_cannot_be_admitted_to_production():
    """Verifies that a revoked champion model cannot be served in production."""
    entry = {
        "version": 1,
        "simulation_id": "sim_revoked_model",
        "sha256": "a" * 64,
        "is_active": True,
        "status": "champion",
        "is_revoked": True,
        "provenance_type": "TRAINING_RECORDED",
    }
    with (
        patch.object(get_settings(), "app_env", "production"),
        pytest.raises(ModelIntegrityError, match="model version has been revoked"),
    ):
        validate_production_admission(entry, context="resolve_and_verify_champion")


def test_development_seeded_account_blocked_from_production_promotion(temp_reg_dir: str):
    """Verifies that development seeded accounts cannot authorize production model promotions."""
    reg = ModelRegistry(storage_dir=temp_reg_dir)
    svc = ModelService(get_settings())
    m = svc.create_model(dp_compatible=True)

    entry = reg.save_version("sim_seeded_account", m.state_dict(), {"auc": 0.8}, is_promoted=False)
    sig = generate_signoff_signature("sim_seeded_account", 1, entry["sha256"], "ml_engineer", "admin")

    with (
        patch.object(get_settings(), "app_env", "production"),
        pytest.raises(ModelIntegrityError, match="development seeded account 'admin' cannot authorize promotion"),
    ):
        reg.sign_off("sim_seeded_account", 1, "ml_engineer", user="admin", signature=sig)


def test_unauthorized_champion_fails_closed_in_production(temp_reg_dir: str):
    """Verifies that an active champion with UNVERIFIED provenance is rejected when resolving active identity."""
    reg = ModelRegistry(storage_dir=temp_reg_dir)
    manifest = [
        {
            "version": 1,
            "filename": "model_v1.pt",
            "is_active": True,
            "status": "champion",
            "sha256": "f" * 64,
            "provenance_type": "UNVERIFIED",
        }
    ]
    reg._save_manifest("sim_unauth_champion", manifest)

    with patch.object(get_settings(), "app_env", "production"):
        ident = reg.get_active_champion_identity("sim_unauth_champion")
        assert ident is None


# ==============================================================================
# SECTION 3: REGISTRY DURABILITY & CONCURRENCY
# ==============================================================================


def test_manifest_write_failure_propagates_error_without_swallowing(temp_reg_dir: str):
    """Verifies that an I/O exception during manifest persistence is propagated without being swallowed."""
    reg = ModelRegistry(storage_dir=temp_reg_dir)

    with (
        patch("builtins.open", side_effect=OSError("Disk write error")),
        pytest.raises(ModelIntegrityError, match="Authoritative manifest persistence failed"),
    ):
        reg._save_manifest("sim_manifest_error", [{"version": 1}])


def test_atomic_replace_failure_cleans_up_temporary_file(temp_reg_dir: str):
    """Verifies that a failure during atomic file replacement cleans up the temporary file."""
    reg = ModelRegistry(storage_dir=temp_reg_dir)
    sim_dir = reg._get_sim_dir("sim_atomic_cleanup")

    with (
        patch("os.replace", side_effect=OSError("Atomic swap failed")),
        pytest.raises(ModelIntegrityError, match="Authoritative manifest persistence failed"),
    ):
        reg._save_manifest("sim_atomic_cleanup", [{"version": 1}])

    files = os.listdir(sim_dir)
    tmp_files = [f for f in files if f.endswith(".tmp")]
    assert len(tmp_files) == 0


def test_concurrent_saves_acquire_unique_monotonic_versions(temp_reg_dir: str):
    """Verifies that concurrent save operations across threads acquire sequential monotonic versions."""
    reg = ModelRegistry(storage_dir=temp_reg_dir)
    svc = ModelService(get_settings())
    m = svc.create_model(dp_compatible=True)

    def worker(idx: int):
        return reg.save_version("sim_concurrent_saves", m.state_dict(), {"idx": idx}, is_promoted=False)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(worker, i) for i in range(8)]
        results = [f.result() for f in futures]

    versions = [r["version"] for r in results]
    assert len(set(versions)) == 8
    assert sorted(versions) == list(range(1, 9))


def test_concurrent_promotions_preserve_single_active_champion(temp_reg_dir: str):
    """Verifies that concurrent promote_version calls leave exactly one active champion."""
    reg = ModelRegistry(storage_dir=temp_reg_dir)
    svc = ModelService(get_settings())
    m = svc.create_model(dp_compatible=True)

    for i in range(1, 4):
        reg.save_version("sim_concurrent_promotions", m.state_dict(), {"i": i}, is_promoted=False)

    def promote_worker(v: int):
        return reg.promote_version("sim_concurrent_promotions", version=v)

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        futures = [ex.submit(promote_worker, v) for v in (1, 2, 3)]
        for f in futures:
            f.result()

    manifest = reg._load_manifest("sim_concurrent_promotions")
    champions = [e for e in manifest if e.get("status") == "champion" and e.get("is_active")]
    assert len(champions) == 1


def test_concurrent_promotion_and_rollback_preserves_single_champion(temp_reg_dir: str):
    """Verifies that concurrent promotion and rollback operations leave a deterministic single champion."""
    reg = ModelRegistry(storage_dir=temp_reg_dir)
    svc = ModelService(get_settings())
    m = svc.create_model(dp_compatible=True)

    for i in range(1, 4):
        reg.save_version("sim_promotion_rollback", m.state_dict(), {"i": i}, is_promoted=False)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(reg.promote_version, "sim_promotion_rollback", 3)
        f2 = ex.submit(reg.rollback, "sim_promotion_rollback", 1)
        f1.result()
        f2.result()

    manifest = reg._load_manifest("sim_promotion_rollback")
    champions = [e for e in manifest if e.get("status") == "champion" and e.get("is_active")]
    assert len(champions) == 1


def test_cache_invalidation_failure_preserves_durable_registry_state(temp_reg_dir: str):
    """Verifies that a failure during cache invalidation does not corrupt or roll back manifest persistence."""
    reg = ModelRegistry(storage_dir=temp_reg_dir)
    svc = ModelService(get_settings())
    m = svc.create_model(dp_compatible=True)

    with patch.object(ModelService, "invalidate_model_cache", side_effect=RuntimeError("Redis down")):
        entry = reg.save_version("sim_cache_failure", m.state_dict(), {"auc": 0.85}, is_promoted=True)

    manifest = reg._load_manifest("sim_cache_failure")
    assert len(manifest) == 1
    assert manifest[0]["is_active"] is True
    assert manifest[0]["version"] == entry["version"]


def test_ambiguous_champions_in_manifest_fails_closed(temp_reg_dir: str):
    """Verifies that a manifest containing multiple active champions fails closed on resolution."""
    reg = ModelRegistry(storage_dir=temp_reg_dir)
    manifest = [
        {"version": 1, "status": "champion", "is_active": True, "filename": "m1.pt", "sha256": "a" * 64},
        {"version": 2, "status": "champion", "is_active": True, "filename": "m2.pt", "sha256": "b" * 64},
    ]
    reg._save_manifest("sim_ambiguous_champions", manifest)

    with pytest.raises(ModelIntegrityError, match="multiple active champion entries found"):
        reg.resolve_and_verify_champion("sim_ambiguous_champions")


def test_manifest_referencing_missing_artifact_fails_closed(temp_reg_dir: str):
    """Verifies that a manifest referencing a missing checkpoint file on disk fails closed."""
    reg = ModelRegistry(storage_dir=temp_reg_dir)
    manifest = [
        {"version": 1, "status": "champion", "is_active": True, "filename": "missing.pt", "sha256": "a" * 64}
    ]
    reg._save_manifest("sim_missing_artifact", manifest)

    with pytest.raises(ModelNotAvailableError, match="does not exist on disk"):
        reg.resolve_and_verify_champion("sim_missing_artifact")


# ==============================================================================
# SECTION 4: AUTHENTICATED MODEL CACHE & SERVING
# ==============================================================================


def test_cache_envelope_rejects_missing_mandatory_fields():
    """Verifies that omitting any mandatory envelope field causes validation rejection and cache eviction."""
    from unittest.mock import MagicMock

    from app.presentation.routers.realtime_inference import get_scripted_model

    mock_redis = MagicMock()
    bad_envelope = {
        "signature": "bad",
        "key_id": "cfi_model_cache_key_v1",
        "payload": {"version": 1},
    }
    mock_redis.get.side_effect = lambda k: b"cached_model_bytes" if not k.endswith(":auth") else json.dumps(bad_envelope)

    meta = {
        "version": 1,
        "generation": 1,
        "simulation_id": "sim_test_missing_fields",
        "sha256": "fake_sha",
        "status": "champion",
        "is_active": True,
    }
    with patch("app.presentation.routers.realtime_inference._get_redis_client", return_value=mock_redis), \
         patch("app.application.services.model_registry.ModelRegistry.get_champion_metadata", return_value=meta), \
         patch("torch.jit.load") as mock_jit_load:
        with pytest.raises((ModelIntegrityError, ModelNotAvailableError)):
            get_scripted_model("sim_test_missing_fields")
        mock_jit_load.assert_not_called()
        assert mock_redis.delete.called


def test_cached_envelope_with_tampered_artifact_identity_evicted():
    """Verifies that a cached envelope with a mismatched artifact SHA-256 is rejected and evicted."""
    from unittest.mock import MagicMock

    from app.presentation.routers.realtime_inference import get_scripted_model

    mock_redis = MagicMock()
    settings = get_settings()
    domain_key = derive_domain_key(settings.payload_signing_secret, DOMAIN_MODEL_CACHE)

    meta_payload = {
        "version": 1,
        "generation": 1,
        "simulation_id": "sim_tampered_artifact",
        "artifact_sha256": "expected_artifact_sha",
        "model_sha256": "tampered_sha256",
        "architecture": "FraudDetectionModel-GroupNorm",
        "input_dim": 10,
        "feature_schema_version": "v1.0",
        "feature_names": FEATURE_NAMES,
        "provenance_type": "TRAINING_RECORDED",
        "created_at": "2026-10-10T00:00:00Z",
    }
    raw_bytes = b"real_model_bytes"
    sig = hmac.new(domain_key, json.dumps(meta_payload, sort_keys=True).encode() + b":" + raw_bytes, hashlib.sha256).hexdigest()
    envelope = {
        "serialization_version": 2,
        "key_id": "cfi_model_cache_key_v1",
        "signature": sig,
        "payload": meta_payload,
    }
    mock_redis.get.side_effect = lambda k: raw_bytes if not k.endswith(":auth") else json.dumps(envelope)

    meta = {
        "version": 1,
        "generation": 1,
        "simulation_id": "sim_tampered_artifact",
        "sha256": "expected_artifact_sha",
        "status": "champion",
        "is_active": True,
    }
    with patch("app.presentation.routers.realtime_inference._get_redis_client", return_value=mock_redis), \
         patch("app.application.services.model_registry.ModelRegistry.get_champion_metadata", return_value=meta), \
         patch("torch.jit.load") as mock_jit_load:
        with pytest.raises((ModelIntegrityError, ModelNotAvailableError)):
            get_scripted_model("sim_tampered_artifact")
        mock_jit_load.assert_not_called()
        assert mock_redis.delete.called


def test_cached_envelope_with_untrusted_key_id_rejected():
    """Verifies that an envelope signed with an untrusted key identifier is rejected and evicted."""
    from unittest.mock import MagicMock

    from app.presentation.routers.realtime_inference import get_scripted_model

    mock_redis = MagicMock()
    bad_envelope = {
        "serialization_version": 2,
        "key_id": "untrusted_compromised_key_v99",
        "signature": "bad",
        "payload": {},
    }
    mock_redis.get.side_effect = lambda k: b"bytes" if not k.endswith(":auth") else json.dumps(bad_envelope)

    meta = {
        "version": 1,
        "generation": 1,
        "simulation_id": "sim_untrusted_key",
        "sha256": "fake_sha",
        "status": "champion",
        "is_active": True,
    }
    with patch("app.presentation.routers.realtime_inference._get_redis_client", return_value=mock_redis), \
         patch("app.application.services.model_registry.ModelRegistry.get_champion_metadata", return_value=meta), \
         patch("torch.jit.load") as mock_jit_load:
        with pytest.raises((ModelIntegrityError, ModelNotAvailableError)):
            get_scripted_model("sim_untrusted_key")
        mock_jit_load.assert_not_called()
        assert mock_redis.delete.called


def test_local_model_cache_evicted_on_generation_bump():
    """Verifies that the in-process model cache invalidates immediately when champion generation changes."""
    cache = LocalModelCache()
    cache.set(model="mock_model", version=1, simulation_id="sim_1", artifact_sha256="hash1", generation=1)

    assert cache.get({"version": 1, "simulation_id": "sim_1", "sha256": "hash1", "generation": 1}) is not None
    assert cache.get({"version": 1, "simulation_id": "sim_1", "sha256": "hash1", "generation": 2}) is None


def test_warm_cache_avoids_heavy_file_hashing():
    """Verifies that warm cache hits read lightweight identity without re-hashing model artifacts on disk."""
    cache = LocalModelCache()
    cache.set(model="jit_model", version=2, simulation_id="sim_warm", artifact_sha256="hash2", generation=1)

    lightweight_ident = {"version": 2, "simulation_id": "sim_warm", "sha256": "hash2", "generation": 1}
    cached_hit = cache.get(lightweight_ident)
    assert cached_hit is not None
    assert cached_hit[0] == "jit_model"


def test_torchscript_loading_requires_prior_authentication():
    """Verifies that model deserialization is executed only after successful cryptographic authentication."""
    from unittest.mock import MagicMock

    from app.presentation.routers.realtime_inference import get_scripted_model

    mock_redis = MagicMock()
    envelope = {
        "serialization_version": 2,
        "key_id": "cfi_model_cache_key_v1",
        "signature": "invalid_forged_hmac_hex",
        "payload": {
            "version": 1,
            "generation": 1,
            "simulation_id": "sim_auth_test",
            "artifact_sha256": "sha",
            "model_sha256": hashlib.sha256(b"fake").hexdigest(),
            "architecture": "FraudDetectionModel-GroupNorm",
            "input_dim": 10,
        },
    }
    mock_redis.get.side_effect = lambda k: b"fake" if not k.endswith(":auth") else json.dumps(envelope)

    meta = {
        "version": 1,
        "generation": 1,
        "simulation_id": "sim_auth_test",
        "sha256": "sha",
        "status": "champion",
        "is_active": True,
    }
    with patch("app.presentation.routers.realtime_inference._get_redis_client", return_value=mock_redis), \
         patch("app.application.services.model_registry.ModelRegistry.get_champion_metadata", return_value=meta), \
         patch("torch.jit.load") as mock_jit_load:
        with pytest.raises((ModelIntegrityError, ModelNotAvailableError)):
            get_scripted_model("sim_auth_test")
        mock_jit_load.assert_not_called()
        assert mock_redis.delete.called



# ==============================================================================
# SECTION 5: REAL-TIME FEATURE INTEGRITY & SCHEMA PARITY
# ==============================================================================


def test_production_request_missing_customer_history_returns_422():
    """Verifies that missing customer_history_score in production returns HTTP 422 MISSING_REQUIRED_FEATURES."""
    client = TestClient(app)
    req = {
        "transaction_id": "tx_req_01",
        "amount": 100.0,
        "merchant_category": "grocery",
        "velocity_1h": 1,
        "country_code": "US",
        "device_type": "mobile_app",
        "chargeback_count": 0.0,
        "account_age_days": 100.0,
        "merchant_risk_score": 0.1,
        "hour_of_day": 12.0,
    }
    with patch.object(get_settings(), "app_env", "production"):
        resp = client.post("/v1/inference/score", json=req, headers=_auth_headers())
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "MISSING_REQUIRED_FEATURES"
        assert "customer_history_score" in resp.json()["detail"]["message"]


def test_production_request_missing_chargeback_count_returns_422():
    """Verifies that missing chargeback_count in production returns HTTP 422."""
    client = TestClient(app)
    req = {
        "transaction_id": "tx_req_02",
        "amount": 100.0,
        "merchant_category": "grocery",
        "velocity_1h": 1,
        "country_code": "US",
        "device_type": "mobile_app",
        "customer_history_score": 0.9,
        "account_age_days": 100.0,
        "merchant_risk_score": 0.1,
        "hour_of_day": 12.0,
    }
    with patch.object(get_settings(), "app_env", "production"):
        resp = client.post("/v1/inference/score", json=req, headers=_auth_headers())
        assert resp.status_code == 422
        assert "chargeback_count" in resp.json()["detail"]["message"]


def test_production_request_missing_account_age_returns_422():
    """Verifies that missing account_age_days in production returns HTTP 422."""
    client = TestClient(app)
    req = {
        "transaction_id": "tx_req_03",
        "amount": 100.0,
        "merchant_category": "grocery",
        "velocity_1h": 1,
        "country_code": "US",
        "device_type": "mobile_app",
        "customer_history_score": 0.9,
        "chargeback_count": 0.0,
        "merchant_risk_score": 0.1,
        "hour_of_day": 12.0,
    }
    with patch.object(get_settings(), "app_env", "production"):
        resp = client.post("/v1/inference/score", json=req, headers=_auth_headers())
        assert resp.status_code == 422
        assert "account_age_days" in resp.json()["detail"]["message"]


def test_canonical_feature_preprocessing_shape_and_finiteness():
    """Verifies that transaction features preprocess through the canonical data preprocessor into a finite tensor."""
    sample_txn = {
        "transaction_amount": 500.0,
        "merchant_category": "electronics",
        "country_code": "US",
        "device_type": "mobile_app",
        "velocity": 5.0,
        "hour_of_day": 14.0,
        "merchant_risk_score": 0.15,
        "customer_history_score": 0.95,
        "chargeback_count": 0.0,
        "account_age_days": 365.0,
    }
    tensor = preprocess_transaction(sample_txn)
    assert isinstance(tensor, torch.Tensor)
    assert tensor.shape == (1, 10)
    assert torch.isfinite(tensor).all()


def test_missing_feature_in_canonical_preprocess_raises_value_error():
    """Verifies that preprocess_transaction fails closed if any required feature is missing."""
    incomplete = {"transaction_amount": 100.0}
    with pytest.raises(ValueError, match="is missing or None"):
        preprocess_transaction(incomplete)


def test_non_finite_feature_in_canonical_preprocess_raises_value_error():
    """Verifies that preprocess_transaction rejects non-finite values (NaN and Inf)."""
    bad_txn = {
        "transaction_amount": float("nan"),
        "merchant_category": "grocery",
        "country_code": "US",
        "device_type": "mobile_app",
        "velocity": 1.0,
        "hour_of_day": 12.0,
        "merchant_risk_score": 0.1,
        "customer_history_score": 0.9,
        "chargeback_count": 0.0,
        "account_age_days": 100.0,
    }
    with pytest.raises(ValueError, match="non-finite value"):
        preprocess_transaction(bad_txn)


def test_development_partial_features_labeled_as_simulated():
    """Verifies that partial features in development mode are labeled with [Simulated Features]."""
    svc = ModelService(get_settings())
    m = svc.create_model(dp_compatible=True)
    client = TestClient(app)
    req = {
        "transaction_id": "tx_dev_simulated",
        "amount": 50.0,
        "merchant_category": "grocery",
        "velocity_1h": 1,
    }
    with (
        patch("app.presentation.routers.realtime_inference.get_scripted_model", return_value=(m, False)),
        patch.object(get_settings(), "app_env", "development"),
    ):
        resp = client.post("/v1/inference/score", json=req)
        assert resp.status_code == 200
        assert "[Simulated Features]" in resp.json()["explanation"]


def test_complete_production_features_scored_as_authoritative_ml_model(temp_reg_dir: str):
    """Verifies that complete feature vectors in production return ML_MODEL when champion is active."""
    svc = ModelService(get_settings())
    m = svc.create_model(dp_compatible=True)

    client = TestClient(app)
    req = {
        "transaction_id": "tx_prod_authoritative",
        "amount": 200.0,
        "merchant_category": "dining",
        "velocity_1h": 2,
        "country_code": "US",
        "device_type": "mobile_app",
        "customer_history_score": 0.92,
        "chargeback_count": 0.0,
        "account_age_days": 400.0,
        "merchant_risk_score": 0.10,
        "hour_of_day": 19.0,
    }
    with (
        patch("app.presentation.routers.realtime_inference.get_scripted_model", return_value=(m, False)),
        patch("app.presentation.routers.realtime_inference.authorize_model_serving", return_value=("sim_valid", {"simulation_id": "sim_valid", "bank_id": "bank_a"})),
        patch.object(get_settings(), "app_env", "production"),
    ):
        resp = client.post("/v1/inference/score", json=req, headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["evaluated_by"] == "ML_MODEL"
        assert "[Simulated Features]" not in data["explanation"]
        assert 0.0 <= data["risk_score"] <= 1.0


# ==============================================================================
# SECTION 6: END-TO-END MODEL GOVERNANCE & LIFECYCLE WORKFLOWS
# ==============================================================================


def test_end_to_end_trusted_champion_lifecycle(temp_reg_dir: str):
    """Verifies the full lifecycle: training, receipt issuance, registration, dual sign-off, promotion, and serving."""
    svc = ModelService(get_settings())
    reg = ModelRegistry(storage_dir=temp_reg_dir)
    m = svc.create_model(dp_compatible=True)
    X = np.random.randn(128, NUM_FEATURES).astype(np.float32)
    y = np.random.randint(0, 2, size=(128,)).astype(np.float32)
    trained, losses, _ = svc.train_local(m, X, y, epochs=2, batch_size=32)

    buf = io.BytesIO()
    torch.save(trained.state_dict(), buf)
    art_sha = hashlib.sha256(buf.getvalue()).hexdigest()

    auth = TrainingReceiptAuthority()
    rcpt = auth.issue_receipt(
        training_run_id="run_lifecycle_full",
        job_id="job_lifecycle_full",
        model_artifact_sha256=art_sha,
        simulation_id="sim_e2e_champion",
        executed_rounds=1,
        executed_epochs=2,
        sample_count=128,
        metrics={"loss": float(losses[-1]), "auc_roc": 0.86},
    )

    _bind_sim_for_test(reg, "sim_e2e_champion")
    candidate = reg.save_version(
        "sim_e2e_champion",
        trained.state_dict(),
        metrics={"loss": float(losses[-1]), "auc_roc": 0.86},
        is_promoted=False,
        status="inactive",
        provenance_type="TRAINING_RECORDED",
        training_run_id="run_lifecycle_full",
        training_receipt=rcpt,
        artifact_bytes=buf.getvalue(),
    )
    assert candidate["provenance_type"] == "TRAINING_RECORDED"

    auth_svc = AuthenticationService.get_instance()
    u_mle = auth_svc.register_user("mle_governance", "Secret123!", "bank_a", roles=["ml_engineer"], clearance_level=2)
    u_cmp = auth_svc.register_user("comp_governance", "Secret123!", "bank_a", roles=["compliance"], clearance_level=2)
    tok_mle = auth_svc.create_token_bundle(u_mle).access_token
    tok_cmp = auth_svc.create_token_bundle(u_cmp).access_token

    sig_ml = generate_signoff_signature("sim_e2e_champion", 1, candidate["sha256"], "ml_engineer", "mle_governance")
    sig_comp = generate_signoff_signature("sim_e2e_champion", 1, candidate["sha256"], "compliance", "comp_governance")

    reg.sign_off("sim_e2e_champion", 1, "ml_engineer", user="mle_governance", signature=sig_ml, auth_token=tok_mle)
    reg.sign_off("sim_e2e_champion", 1, "compliance", user="comp_governance", signature=sig_comp, auth_token=tok_cmp)
    promoted = reg.promote_version("sim_e2e_champion", 1, auth_token=tok_mle)
    assert promoted["status"] == "champion"
    assert promoted["is_active"] is True

    resolved = reg.resolve_and_verify_champion("sim_e2e_champion")
    assert resolved is not None
    fpath, bdata, meta = resolved
    assert meta["provenance_type"] == "TRAINING_RECORDED"
    assert meta["status"] == "champion"


def test_unverified_model_fails_production_admission_gate(temp_reg_dir: str):
    """Verifies that an unverified model fails closed when attempting production admission."""
    svc = ModelService(get_settings())
    reg = ModelRegistry(storage_dir=temp_reg_dir)
    m = svc.create_model(dp_compatible=True)

    with (
        patch.object(get_settings(), "app_env", "production"),
        pytest.raises(ModelIntegrityError, match="Production admission rejected: model provenance 'UNVERIFIED'"),
    ):
        reg.save_version(
            "sim_unverified_gate",
            m.state_dict(),
            metrics={"loss": 0.01, "auc_roc": 0.99},
            is_promoted=True,
            provenance_type="UNVERIFIED",
        )


def test_unauthorized_signoff_identities_fail_production_activation(temp_reg_dir: str):
    """Verifies that a valid artifact cannot become a production champion using unverified sign-off identities."""
    svc = ModelService(get_settings())
    reg = ModelRegistry(storage_dir=temp_reg_dir)
    _bind_sim_for_test(reg, "sim_unauth_activation")
    m = svc.create_model(dp_compatible=True)

    entry = reg.save_version(
        "sim_unauth_activation",
        m.state_dict(),
        metrics={"loss": 0.2},
        is_promoted=False,
        provenance_type="TRAINING_RECORDED",
        training_run_id="run_c",
        training_evidence={"loss": 0.2, "epochs": 1, "batch_size": 32},
    )
    sig_ml = generate_signoff_signature("sim_unauth_activation", 1, entry["sha256"], "ml_engineer", "invented_mle")

    with (
        patch.object(get_settings(), "app_env", "production"),
        pytest.raises(ModelIntegrityError, match="authenticated session token is required for institutional sign-off"),
    ):
        reg.sign_off("sim_unauth_activation", 1, "ml_engineer", user="invented_mle", signature=sig_ml)


def test_champion_promotion_and_rollback_generation_consistency(temp_reg_dir: str):
    """Verifies that consecutive promotions and rollbacks maintain strictly increasing generations."""
    reg = ModelRegistry(storage_dir=temp_reg_dir)
    svc = ModelService(get_settings())
    m = svc.create_model(dp_compatible=True)

    v1 = reg.save_version("sim_champion_lifecycle", m.state_dict(), {"auc": 0.8}, is_promoted=True)
    assert v1["version"] == 1
    assert v1["generation"] == 1

    v2 = reg.save_version("sim_champion_lifecycle", m.state_dict(), {"auc": 0.9}, is_promoted=True)
    assert v2["version"] == 2
    assert v2["generation"] == 2

    active = reg.get_active_version("sim_champion_lifecycle")
    assert active is not None
    assert active["version"] == 2

    rolled = reg.rollback("sim_champion_lifecycle", version=1)
    assert rolled["version"] == 1
    assert rolled["generation"] == 3

    active_after = reg.get_active_version("sim_champion_lifecycle")
    assert active_after is not None
    assert active_after["version"] == 1
    assert active_after["generation"] == 3


def test_production_feature_validation_and_scoring_contract():
    """Verifies that incomplete requests are rejected with HTTP 422 and complete requests score with HTTP 200."""
    client = TestClient(app)
    incomplete_req = {
        "transaction_id": "tx_contract_incomplete",
        "amount": 150.0,
        "merchant_category": "dining",
        "velocity_1h": 1,
    }
    with patch.object(get_settings(), "app_env", "production"):
        resp = client.post("/v1/inference/score", json=incomplete_req, headers=_auth_headers())
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "MISSING_REQUIRED_FEATURES"

    complete_req = {
        "transaction_id": "tx_contract_complete",
        "amount": 150.0,
        "merchant_category": "dining",
        "velocity_1h": 1,
        "country_code": "US",
        "device_type": "mobile_app",
        "customer_history_score": 0.95,
        "chargeback_count": 0.0,
        "account_age_days": 300.0,
        "merchant_risk_score": 0.10,
        "hour_of_day": 14.0,
    }
    svc = ModelService(get_settings())
    m = svc.create_model(dp_compatible=True)
    with (
        patch("app.presentation.routers.realtime_inference.get_scripted_model", return_value=(m, False)),
        patch("app.presentation.routers.realtime_inference.authorize_model_serving", return_value=("sim_valid", {"simulation_id": "sim_valid", "bank_id": "bank_a"})),
        patch.object(get_settings(), "app_env", "production"),
    ):
        resp2 = client.post("/v1/inference/score", json=complete_req, headers=_auth_headers())
        assert resp2.status_code == 200
        assert resp2.json()["evaluated_by"] == "ML_MODEL"
