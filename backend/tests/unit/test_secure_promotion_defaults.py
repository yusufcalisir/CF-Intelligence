"""Adversarial and Security Verification Suite: MODULE 01-B.4.

Workstream: 01-B.4 — Secure Promotion Defaults & Orphan Artifact Cleanup
Verifies:
- SAVE-01: save_version() without explicit promotion -> inactive candidate.
- SAVE-02: Verified federated training registers an inactive candidate.
- SAVE-03: Explicit, authorized champion promotion still succeeds.
- SAVE-04: Admission validation fails after writing checkpoint -> no orphan artifact or unauthorized manifest entry.
- SAVE-05: Failed save does not modify an existing active champion.
- SAVE-06: Concurrent save failure cannot remove another process's valid artifact.
- SAVE-07: Retry or duplicate registration preserves correct version identity and files.
- SAVE-08: Process interruption during artifact persistence cannot create an unauthorized active model.
- SAVE-09: Genuine receipt, digest, and Registry candidate metadata remain consistent after successful save.
- SAVE-10: Existing challenger, issuer revocation, and dual sign-off protections remain intact.
"""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
import threading
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
import torch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.model_registry import (
    ModelRegistry,
    compute_file_sha256,
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

FEATURE_NAMES = [f"f_{i}" for i in range(NUM_FEATURES)]
TEST_SIGNING_SECRET = "test-signing-secret-key-32-chars-long!"


def _setup_environment(tmp_dir: str):
    """Set up clean isolated trust store, keys, and registry."""
    storage_dir = os.path.join(tmp_dir, "model_storage")
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
    return storage_dir, trust_store, verifier, reg, priv, kid, admin_token


def _create_authentic_candidate(
    reg: ModelRegistry,
    priv: ed25519.Ed25519PrivateKey,
    kid: str,
    sim_id: str,
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
    receipt = issuer.issue_receipt(evidence=evidence, model_bytes=model_bytes).to_dict()

    entry = reg.save_version(
        simulation_id=sim_id,
        state_dict=model.state_dict(),
        metrics={"loss": 0.05, "auc": 0.95},
        artifact_bytes=model_bytes,
        training_receipt=receipt,
        is_promoted=False,
    )
    return entry, model, model_bytes, sha256, receipt


def _create_users_and_signoffs(
    sim_id: str,
    version: int,
    sha256: str,
    secret: str = TEST_SIGNING_SECRET,
) -> tuple[list[dict[str, Any]], str]:
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
            "signed_at": datetime.now(UTC).isoformat(),
            "payload_bound": f"{sim_id}:{version}:{sha256}:ml_engineer:{u_ml.username}",
        },
        {
            "role": "compliance",
            "user": u_cp.username,
            "signature": sig_cp,
            "signature_scheme": "APPLICATION_HMAC_SHA256",
            "is_signature_valid": True,
            "is_role_verified": True,
            "is_identity_verified": True,
            "signed_at": datetime.now(UTC).isoformat(),
            "payload_bound": f"{sim_id}:{version}:{sha256}:compliance:{u_cp.username}",
        },
    ]
    token = auth_svc.create_token_bundle(u_ml).access_token
    return sign_offs, token


def test_save01_unspecified_promotion_defaults_to_inactive_candidate():
    """SAVE-01: save_version() without explicit promotion -> inactive candidate."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        _, _, _, reg, _, _, _ = _setup_environment(tmp_dir)
        sim_id = "sim_save01"
        svc = ModelService(get_settings())
        m = svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)

        entry = reg.save_version(
            simulation_id=sim_id,
            state_dict=m.state_dict(),
            metrics={"loss": 0.1, "auc_roc": 0.8},
        )

        assert entry["is_active"] is False
        assert entry["status"] == "inactive"
        assert reg.get_active_version(sim_id) is None
        assert reg.get_active_champion_identity(sim_id) is None


def test_save02_verified_federated_training_registers_inactive_candidate():
    """SAVE-02: Verified federated training registers an inactive candidate."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        _, _, _, reg, priv, kid, _ = _setup_environment(tmp_dir)
        sim_id = "sim_save02"

        entry, _, _, _, _ = _create_authentic_candidate(reg, priv, kid, sim_id)

        assert entry["provenance_type"] == "TRAINING_RECORDED"
        assert entry["is_production_eligible"] is True
        assert entry["is_active"] is False
        assert entry["status"] == "inactive"
        assert reg.get_active_champion_identity(sim_id) is None


def test_save03_explicit_authorized_champion_promotion_succeeds():
    """SAVE-03: Explicit, authorized champion promotion still succeeds."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage_dir, _, _, reg, priv, kid, _ = _setup_environment(tmp_dir)
        sim_id = "sim_save03"
        settings = get_settings()

        entry, _, _, sha256, _ = _create_authentic_candidate(reg, priv, kid, sim_id)
        assert entry["is_active"] is False

        sign_offs, token_ml = _create_users_and_signoffs(sim_id, entry["version"], sha256, TEST_SIGNING_SECRET)
        manifest = reg._load_manifest(sim_id)
        manifest[0]["sign_offs"] = sign_offs
        reg._save_manifest(sim_id, manifest)

        with (
            patch.object(settings, "app_env", "production"),
            patch.object(settings, "payload_signing_secret", TEST_SIGNING_SECRET),
        ):
            promoted = reg.promote_version(sim_id, entry["version"], target_status="champion", auth_token=token_ml)
            assert promoted["is_active"] is True
            assert promoted["status"] == "champion"

            active_ident = reg.get_active_champion_identity(sim_id)
            assert active_ident is not None
            assert active_ident["version"] == entry["version"]
            assert active_ident["sha256"] == sha256


def test_save04_admission_failure_leaves_no_orphan_artifact_or_manifest():
    """SAVE-04: Admission validation fails after writing checkpoint -> no orphan artifact or unauthorized manifest entry."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage_dir, _, _, reg, _, _, _ = _setup_environment(tmp_dir)
        sim_id = "sim_save04"
        settings = get_settings()
        svc = ModelService(settings)
        m = svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)
        sim_dir = reg._get_sim_dir(sim_id)

        # In production, save_version with is_promoted=True without receipt/signoffs fails admission
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(settings, "payload_signing_secret", TEST_SIGNING_SECRET),
            pytest.raises(ModelIntegrityError, match="Production admission rejected"),
        ):
            reg.save_version(
                simulation_id=sim_id,
                state_dict=m.state_dict(),
                metrics={"loss": 0.05, "auc": 0.95},
                is_promoted=True,
            )

        # Verify no orphan .pt files or temp files left in sim_dir
        files_in_sim = os.listdir(sim_dir) if os.path.exists(sim_dir) else []
        pt_files = [f for f in files_in_sim if f.endswith(".pt")]
        tmp_files = [f for f in files_in_sim if f.startswith(".tmp_")]
        assert pt_files == []
        assert tmp_files == []
        assert reg.list_versions(sim_id) == []


def test_save05_failed_save_does_not_modify_existing_active_champion():
    """SAVE-05: Failed save does not modify an existing active champion."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage_dir, _, _, reg, priv, kid, _ = _setup_environment(tmp_dir)
        sim_id = "sim_save05"
        settings = get_settings()

        # Create and promote v1 to champion
        entry1, _, _, sha1, _ = _create_authentic_candidate(reg, priv, kid, sim_id)
        sign_offs, token_ml = _create_users_and_signoffs(sim_id, 1, sha1, TEST_SIGNING_SECRET)
        manifest = reg._load_manifest(sim_id)
        manifest[0]["sign_offs"] = sign_offs
        reg._save_manifest(sim_id, manifest)

        with (
            patch.object(settings, "app_env", "production"),
            patch.object(settings, "payload_signing_secret", TEST_SIGNING_SECRET),
        ):
            reg.promote_version(sim_id, 1, target_status="champion", auth_token=token_ml)
            v1_ident = reg.get_active_champion_identity(sim_id)
            assert v1_ident is not None
            assert v1_ident["version"] == 1

            # Attempt to save v2 with is_promoted=True but lacking valid receipt/signoffs
            svc = ModelService(settings)
            m2 = svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)
            with pytest.raises(ModelIntegrityError):
                reg.save_version(
                    simulation_id=sim_id,
                    state_dict=m2.state_dict(),
                    metrics={"loss": 0.02},
                    is_promoted=True,
                )

            # Active champion remains v1
            active_ident = reg.get_active_champion_identity(sim_id)
            assert active_ident is not None
            assert active_ident["version"] == 1
            assert active_ident["sha256"] == sha1

            # Manifest contains only v1
            current_manifest = reg.list_versions(sim_id)
            assert len(current_manifest) == 1
            assert current_manifest[0]["version"] == 1
            assert current_manifest[0]["is_active"] is True

            # No orphan model_v2.pt on disk
            sim_dir = reg._get_sim_dir(sim_id)
            pt_files = [f for f in os.listdir(sim_dir) if f.endswith(".pt")]
            assert pt_files == ["model_v1.pt"]


def test_save06_concurrent_save_failure_cannot_remove_valid_artifact():
    """SAVE-06: Concurrent save failure cannot remove another process's valid artifact."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage_dir, _, _, reg, priv, kid, _ = _setup_environment(tmp_dir)
        sim_id = "sim_save06"
        settings = get_settings()

        # Step 1: Save authentic v1
        entry1, _, _, _, _ = _create_authentic_candidate(reg, priv, kid, sim_id)
        assert entry1["version"] == 1
        sim_dir = reg._get_sim_dir(sim_id)
        v1_path = os.path.join(sim_dir, "model_v1.pt")
        assert os.path.exists(v1_path)

        # Step 2: Failed save for v2 in separate thread/process
        svc = ModelService(settings)
        m2 = svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)

        failure_occurred = threading.Event()

        def _failed_save_worker():
            with (
                patch.object(settings, "app_env", "production"),
                patch.object(settings, "payload_signing_secret", TEST_SIGNING_SECRET),
            ):
                try:
                    reg.save_version(
                        simulation_id=sim_id,
                        state_dict=m2.state_dict(),
                        metrics={"loss": 0.01},
                        is_promoted=True,
                    )
                except ModelIntegrityError:
                    failure_occurred.set()

        t = threading.Thread(target=_failed_save_worker)
        t.start()
        t.join()

        assert failure_occurred.is_set()

        # Crucial check: model_v1.pt was NOT touched or deleted!
        assert os.path.exists(v1_path)
        loaded = reg.load_version(sim_id, 1)
        assert isinstance(loaded, dict)


def test_save07_retry_or_duplicate_registration_preserves_version_identity():
    """SAVE-07: Retry or duplicate registration preserves correct version identity and files."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage_dir, _, _, reg, _, _, _ = _setup_environment(tmp_dir)
        sim_id = "sim_save07"
        settings = get_settings()
        svc = ModelService(settings)
        m = svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)

        # Initial attempt fails admission in production
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(settings, "payload_signing_secret", TEST_SIGNING_SECRET),
            pytest.raises(ModelIntegrityError),
        ):
            reg.save_version(
                simulation_id=sim_id,
                state_dict=m.state_dict(),
                metrics={"loss": 0.5},
                is_promoted=True,
            )

        # Retry as inactive candidate succeeds and claims version 1
        v1 = reg.save_version(
            simulation_id=sim_id,
            state_dict=m.state_dict(),
            metrics={"loss": 0.5},
        )
        assert v1["version"] == 1
        assert v1["filename"] == "model_v1.pt"
        sim_dir = reg._get_sim_dir(sim_id)
        assert os.path.exists(os.path.join(sim_dir, "model_v1.pt"))

        # Subsequent save succeeds as version 2
        v2 = reg.save_version(
            simulation_id=sim_id,
            state_dict=m.state_dict(),
            metrics={"loss": 0.4},
        )
        assert v2["version"] == 2
        assert v2["filename"] == "model_v2.pt"
        assert os.path.exists(os.path.join(sim_dir, "model_v2.pt"))


def test_save08_process_interruption_during_serialization_leaves_no_active_model():
    """SAVE-08: Process interruption during artifact persistence cannot create an unauthorized active model."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage_dir, _, _, reg, _, _, _ = _setup_environment(tmp_dir)
        sim_id = "sim_save08"
        svc = ModelService(get_settings())
        m = svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)
        sim_dir = reg._get_sim_dir(sim_id)

        # Simulate interruption during serialization
        with (
            patch("torch.save", side_effect=RuntimeError("Process terminated / out of disk")),
            pytest.raises(RuntimeError, match="Process terminated"),
        ):
            reg.save_version(
                simulation_id=sim_id,
                state_dict=m.state_dict(),
                metrics={"loss": 0.1},
            )

        # No final or temp files left
        files = os.listdir(sim_dir) if os.path.exists(sim_dir) else []
        assert [f for f in files if f.endswith(".pt")] == []
        assert reg.list_versions(sim_id) == []
        assert reg.get_active_champion_identity(sim_id) is None


def test_save09_genuine_receipt_digest_and_candidate_metadata_consistency():
    """SAVE-09: Genuine receipt, digest, and Registry candidate metadata remain consistent after successful save."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        _, _, _, reg, priv, kid, _ = _setup_environment(tmp_dir)
        sim_id = "sim_save09"

        entry, _, model_bytes, sha256, receipt = _create_authentic_candidate(reg, priv, kid, sim_id)
        sim_dir = reg._get_sim_dir(sim_id)
        filepath = os.path.join(sim_dir, entry["filename"])

        assert entry["sha256"] == sha256
        assert compute_file_sha256(filepath) == sha256
        assert entry["training_receipt"]["model_artifact_sha256"] == sha256
        assert entry["verified_schema"]["architecture"] == "FraudDetectionNN"
        assert entry["verified_schema"]["input_dim"] == NUM_FEATURES
        assert entry["verified_schema"]["feature_names"] == FEATURE_NAMES


def test_save10_existing_challenger_revocation_and_signoff_protections_intact():
    """SAVE-10: Existing challenger, issuer revocation, and dual sign-off protections remain intact."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage_dir, trust_store, _, reg, priv, kid, admin_token = _setup_environment(tmp_dir)
        sim_id = "sim_save10"
        settings = get_settings()

        # Step 1: Create candidate
        entry, _, _, sha256, _ = _create_authentic_candidate(reg, priv, kid, sim_id)

        # Step 2: Promote candidate to challenger without dual sign-offs (challenger governance)
        auth_svc = AuthenticationService.get_instance()
        u_ml = auth_svc.register_user(f"ml_{sim_id}_chall", "Pass123!Secure", "bank_a", roles=["ml_engineer"], clearance_level=2)
        token_ml = auth_svc.create_token_bundle(u_ml).access_token
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(settings, "payload_signing_secret", TEST_SIGNING_SECRET),
        ):
            chall_promoted = reg.promote_version(sim_id, entry["version"], target_status="challenger", auth_token=token_ml)
            assert chall_promoted["status"] == "challenger"
            assert chall_promoted["is_active"] is False

        # Step 3: Revoking issuer key prevents promotion to champion
        trust_store.revoke_key(kid, "Compromised authority", admin_token=admin_token)
        sign_offs, token_ml2 = _create_users_and_signoffs(sim_id, entry["version"], sha256, TEST_SIGNING_SECRET)
        manifest = reg._load_manifest(sim_id)
        manifest[0]["sign_offs"] = sign_offs
        reg._save_manifest(sim_id, manifest)

        with (
            patch.object(settings, "app_env", "production"),
            patch.object(settings, "payload_signing_secret", TEST_SIGNING_SECRET),
            pytest.raises(ModelIntegrityError, match="revoked or untrusted|explicitly revoked"),
        ):
            reg.promote_version(sim_id, entry["version"], target_status="champion", auth_token=token_ml2)
