"""Adversarial test suite for atomic registry persistence, fsync durability, and fail-closed integrity.

Verifies:
1. Artifact serialization failure cleans up temporary staged files immediately.
2. Injected disk/fsync/replace errors during manifest persistence fail closed and clean tmp files.
3. Injected disk/fsync/replace errors during ownership persistence fail closed and clean tmp files.
4. Abandoned temporary staging files (.tmp_model_*) are recovered and cleaned up by reconciler.
5. Corrupted, non-JSON, or truncated registry.json fails closed with ModelIntegrityError.
6. Previously registered valid model remains intact and loadable after another registration operation fails.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
import torch
import torch.nn as nn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.model_registry import (
    ModelIntegrityError,
    ModelRegistry,
)
from app.application.services.training_provenance_service import (
    DurableTrustStore,
    TrainingExecutionEvidence,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
)
from app.config import get_settings
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.durable_account_store import DurableAccountStore
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain


class LinearNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.fc = nn.Linear(5, 1)

    def forward(self, x):
        return self.fc(x)


@pytest.fixture
def persist_env(tmp_path, monkeypatch):
    audit_file = str(tmp_path / "security" / "audit_chain.json")
    account_file = str(tmp_path / "security" / "accounts.json")
    trust_file = str(tmp_path / "security" / "trust_store.json")
    reg_dir = str(tmp_path / "models")
    os.makedirs(os.path.dirname(audit_file), exist_ok=True)
    os.makedirs(reg_dir, exist_ok=True)

    audit_secret = "CFI_PROD_AUDIT_LEDGER_SECRET_2026_TEST"
    jwt_secret = "CFI_PROD_JWT_SIGNING_KEY_2026_TEST"
    authority_secret = "CFI_PROD_ACCOUNT_AUTHORITY_SECRET_2026"
    admin_token = "CFI_TRUST_ADMIN_TOKEN_2026"

    monkeypatch.setenv("CFI_AUDIT_LEDGER_PATH", audit_file)
    monkeypatch.setenv("CFI_AUDIT_LEDGER_SECRET", audit_secret)
    monkeypatch.setenv("CFI_TRUST_STORE_PATH", trust_file)
    monkeypatch.setenv("CFI_TRUST_STORE_ADMIN_TOKEN", admin_token)
    monkeypatch.setenv("CFI_MODEL_REGISTRY_DIR", reg_dir)
    monkeypatch.setenv("CFI_JWT_SIGNING_SECRET", jwt_secret)
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", account_file)
    monkeypatch.setenv("CFI_ACCOUNT_AUTHORITY_SECRET", authority_secret)
    monkeypatch.setenv("APP_ENV", "production")

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

    u_ml = auth_svc.register_user("dr_alice_ml", "SecurePass1234!", "bank_a", ["ml_engineer"], clearance_level=2)
    u_admin = auth_svc.register_user("admin_dan", "SecurePass1234!", "bank_a", ["admin"], clearance_level=3)
    tokens = {
        "ml": auth_svc.create_token_bundle(u_ml).access_token,
        "admin": auth_svc.create_token_bundle(u_admin).access_token,
    }

    priv = ed25519.Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    kid = "issuer_persist_01"
    trust_store = DurableTrustStore(trust_file_path=trust_file, admin_token=admin_token)
    trust_store.register_approved_key(key_id=kid, public_key_hex=pub_hex, admin_token=admin_token)

    settings = get_settings()
    orig = settings.app_env
    settings.app_env = "development"
    try:
        issuer = TrainingReceiptIssuer(
            key_id=kid,
            private_key=priv,
            require_authenticated_job=False,
            app_env="development",
            trust_store=trust_store,
        )
    finally:
        settings.app_env = orig

    verifier = TrainingReceiptVerifier(trust_store=trust_store)
    registry = ModelRegistry(storage_dir=reg_dir, verifier=verifier)

    yield {
        "tmp_path": tmp_path,
        "audit_file": audit_file,
        "audit_secret": audit_secret,
        "reg_dir": reg_dir,
        "registry": registry,
        "tokens": tokens,
        "issuer": issuer,
    }

    get_settings.cache_clear()
    AuthenticationService._instance = None
    ImmutableAuditChain._instance = None


def _make_candidate(env: dict, sim_id: str) -> tuple[str, Any]:
    net = LinearNet()
    path = str(env["tmp_path"] / f"cand_{uuid.uuid4().hex}.pt")
    torch.save(net.state_dict(), path)
    with open(path, "rb") as f:
        data = f.read()
    sha = hashlib.sha256(data).hexdigest()

    ev = TrainingExecutionEvidence(
        training_run_id=f"run-{sim_id}",
        job_id=f"job-{sim_id}",
        simulation_id=sim_id,
        completed_at=datetime.now(UTC).isoformat(),
        executed_rounds=2,
        executed_epochs=2,
        sample_count=200,
        observed_metrics={"loss": 0.042, "auc": 0.90},
        artifact_sha256=sha,
        architecture="LinearNet",
        feature_schema_version="2.0",
        feature_names=["f1", "f2", "f3", "f4", "f5"],
    )
    settings = get_settings()
    orig = settings.app_env
    settings.app_env = "development"
    try:
        receipt = env["issuer"].issue_receipt(
            evidence=ev,
            model_bytes=data,
            owning_bank_id="bank_a",
            architecture="LinearNet",
            input_dim=5,
            feature_names=["f1", "f2", "f3", "f4", "f5"],
            feature_schema_version="2.0",
        )
    finally:
        settings.app_env = orig

    return path, receipt


def test_artifact_serialization_failure_cleans_temporary_files(persist_env):
    """If serialization fails during save_version, staging files are cleaned up immediately."""
    sim_id = "sim_persist_01"
    reg: ModelRegistry = persist_env["registry"]
    reg.bind_simulation_ownership(sim_id, "bank_a", auth_token=persist_env["tokens"]["admin"])
    sim_dir = reg._get_sim_dir(sim_id)

    cand_path, receipt = _make_candidate(persist_env, sim_id)

    # Inject an OSError during torch.save
    with patch("torch.save", side_effect=OSError("Simulated ENOSPC: disk full during serialization")):
        with pytest.raises(ModelIntegrityError, match="Artifact serialization failed"):
            reg.save_version(
                simulation_id=sim_id,
                source_model_path=cand_path,
                metrics={"auc": 0.90},
                receipt=receipt.to_dict(),
                auth_token=persist_env["tokens"]["ml"],
                actor="dr_alice_ml",
            )

    # Check that no temporary staging files linger in sim_dir
    files = os.listdir(sim_dir)
    tmp_files = [f for f in files if f.startswith(".tmp_model_")]
    assert len(tmp_files) == 0, f"Found lingering temporary files: {tmp_files}"


def test_injected_fsync_failure_in_manifest_persistence_fails_closed(persist_env):
    """An injected fsync or I/O failure during _save_manifest fails closed and cleans tmp file."""
    sim_id = "sim_persist_02"
    reg: ModelRegistry = persist_env["registry"]
    reg.bind_simulation_ownership(sim_id, "bank_a", auth_token=persist_env["tokens"]["admin"])
    sim_dir = reg._get_sim_dir(sim_id)

    manifest_data = [{"version": 1, "filename": "global_model_v1.pt"}]

    def failing_fsync(fd):
        raise OSError("Simulated EIO: hardware flush failed")

    with patch("os.fsync", side_effect=failing_fsync):
        with pytest.raises(ModelIntegrityError, match="Authoritative manifest persistence failed"):
            reg._save_manifest(sim_id, manifest_data)

    # Verify no tmp files remain
    tmp_files = [f for f in os.listdir(sim_dir) if f.endswith(".tmp")]
    assert len(tmp_files) == 0, f"Found lingering manifest tmp files: {tmp_files}"


def test_injected_fsync_failure_in_ownership_persistence_fails_closed(persist_env):
    """An injected fsync or I/O failure during _save_ownership fails closed and cleans tmp file."""
    sim_id = "sim_persist_03"
    reg: ModelRegistry = persist_env["registry"]
    sim_dir = reg._get_sim_dir(sim_id, create=True)

    with patch("os.fsync", side_effect=OSError("Simulated EIO: hardware flush failed")):
        with pytest.raises(ModelIntegrityError, match="Authoritative ownership persistence failed"):
            reg._save_ownership(sim_id, {"owning_bank_id": "bank_a"})

    tmp_files = [f for f in os.listdir(sim_dir) if f.endswith(".tmp")]
    assert len(tmp_files) == 0, f"Found lingering ownership tmp files: {tmp_files}"


def test_reconciliation_cleans_abandoned_staging_artifacts(persist_env):
    """Abandoned .tmp_model_*.pt files from terminated workers are cleaned up by reconciler."""
    sim_id = "sim_persist_04"
    reg: ModelRegistry = persist_env["registry"]
    reg.bind_simulation_ownership(sim_id, "bank_a", auth_token=persist_env["tokens"]["admin"])
    sim_dir = reg._get_sim_dir(sim_id)

    # Create abandoned staging files simulating killed workers
    abandoned_file_1 = os.path.join(sim_dir, ".tmp_model_abandoned_1.pt")
    abandoned_file_2 = os.path.join(sim_dir, ".tmp_model_abandoned_2.pt")
    with open(abandoned_file_1, "wb") as f:
        f.write(b"abandoned_weights_1")
    with open(abandoned_file_2, "wb") as f:
        f.write(b"abandoned_weights_2")

    assert os.path.exists(abandoned_file_1)
    assert os.path.exists(abandoned_file_2)

    # Reconciler runs
    reg.reconcile_incomplete_mutations(simulation_id=sim_id)

    # Staging files MUST be cleaned
    assert not os.path.exists(abandoned_file_1)
    assert not os.path.exists(abandoned_file_2)


def test_corrupted_manifest_fails_closed(persist_env):
    """A corrupted or truncated registry.json fails closed with ModelIntegrityError."""
    sim_id = "sim_persist_05"
    reg: ModelRegistry = persist_env["registry"]
    sim_dir = reg._get_sim_dir(sim_id, create=True)
    manifest_path = os.path.join(sim_dir, "registry.json")

    # Corrupt JSON content
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("{invalid_json_manifest_content")

    with pytest.raises(ModelIntegrityError, match="Registry manifest.*is corrupted or unreadable"):
        reg._load_manifest(sim_id)


def test_prior_model_loadable_after_subsequent_registration_fails(persist_env):
    """A valid registered version remains fully loadable when a subsequent registration fails."""
    sim_id = "sim_persist_06"
    reg: ModelRegistry = persist_env["registry"]
    reg.bind_simulation_ownership(sim_id, "bank_a", auth_token=persist_env["tokens"]["admin"])

    # 1. Register Version 1 successfully
    cand1, receipt1 = _make_candidate(persist_env, sim_id)
    reg.save_version(
        simulation_id=sim_id,
        source_model_path=cand1,
        metrics={"auc": 0.90},
        receipt=receipt1.to_dict(),
        auth_token=persist_env["tokens"]["ml"],
        actor="dr_alice_ml",
    )

    # Verify Version 1 is loadable
    m1 = reg.load_version(sim_id, 1, target_class=LinearNet)
    assert isinstance(m1, LinearNet)

    # 2. Attempt to register Version 2, but fail during serialization
    cand2, receipt2 = _make_candidate(persist_env, sim_id)
    with patch("torch.save", side_effect=OSError("Disk full")):
        with pytest.raises(ModelIntegrityError):
            reg.save_version(
                simulation_id=sim_id,
                source_model_path=cand2,
                metrics={"auc": 0.92},
                receipt=receipt2.to_dict(),
                auth_token=persist_env["tokens"]["ml"],
                actor="dr_alice_ml",
            )

    # Version 1 MUST still be completely loadable and valid!
    m1_again = reg.load_version(sim_id, 1, target_class=LinearNet)
    assert isinstance(m1_again, LinearNet)
    manifest = reg._load_manifest(sim_id)
    assert len(manifest) == 1
    assert manifest[0]["version"] == 1
