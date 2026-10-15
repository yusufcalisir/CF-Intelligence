"""Focused regression tests verifying artifact preservation and deterministic recovery

after authoritative registration commit followed by audit finalization failure.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import torch
import torch.nn as nn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.model_registry import (
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


class RegressionLinearNet(nn.Module):
    def __init__(self, in_features: int = 10, out_features: int = 2):
        super().__init__()
        self.fc = nn.Linear(in_features, out_features)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.fc(x)


@pytest.fixture
def post_commit_env(tmp_path, monkeypatch):
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
    kid = "issuer_post_commit_01"
    trust_store = DurableTrustStore(trust_file_path=trust_file, admin_token=admin_token)
    trust_store.register_approved_key(key_id=kid, public_key_hex=pub_hex, admin_token=admin_token)

    settings = get_settings()
    orig_env = settings.app_env
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
        settings.app_env = orig_env

    verifier = TrainingReceiptVerifier(trust_store=trust_store)
    registry = ModelRegistry(storage_dir=reg_dir, verifier=verifier)

    monkeypatch.setenv("CFI_TRAINING_RECEIPT_PUBLIC_KEY", pub_hex)
    monkeypatch.setenv("CFI_IDENTITY_STORE_PATH", account_file)
    monkeypatch.setenv("CFI_IDENTITY_STORE_SECRET", authority_secret)

    yield {
        "tmp_path": tmp_path,
        "audit_file": audit_file,
        "reg_dir": reg_dir,
        "registry": registry,
        "tokens": tokens,
        "issuer": issuer,
        "verifier": verifier,
        "pub_hex": pub_hex,
    }

    get_settings.cache_clear()
    AuthenticationService._instance = None
    ImmutableAuditChain._instance = None


def _build_candidate(env: dict, sim_id: str) -> tuple[str, Any]:
    torch.manual_seed(101)
    net = RegressionLinearNet()
    candidate_path = str(env["tmp_path"] / f"cand_{uuid.uuid4().hex}.pt")
    torch.save(net.state_dict(), candidate_path)

    with open(candidate_path, "rb") as f:
        mbytes = f.read()
    msha = hashlib.sha256(mbytes).hexdigest()

    ev = TrainingExecutionEvidence(
        training_run_id=f"run-{sim_id}",
        job_id=f"job-{sim_id}",
        simulation_id=sim_id,
        completed_at=datetime.now(UTC).isoformat(),
        executed_rounds=3,
        executed_epochs=3,
        sample_count=200,
        observed_metrics={"loss": 0.05, "auc": 0.94},
        artifact_sha256=msha,
        architecture="RegressionLinearNet",
        feature_schema_version="2.0",
        feature_names=[f"f{i}" for i in range(10)],
    )
    settings = get_settings()
    orig = settings.app_env
    settings.app_env = "development"
    try:
        receipt = env["issuer"].issue_receipt(
            evidence=ev,
            model_bytes=mbytes,
            owning_bank_id="bank_a",
            architecture="RegressionLinearNet",
            input_dim=10,
            feature_names=[f"f{i}" for i in range(10)],
            feature_schema_version="2.0",
        )
    finally:
        settings.app_env = orig

    return candidate_path, receipt


def test_audit_finalization_failure_after_manifest_persistence_preserves_artifact(post_commit_env):
    """Controlled injection: If finalize_pending_mutation raises an exception

    immediately after _save_manifest, the model artifact and manifest entry
    MUST be preserved, the mutation must NOT be marked FAILED, and later
    reconciliation MUST advance the audit record to COMMITTED.
    """
    env = post_commit_env
    reg: ModelRegistry = env["registry"]
    sim_id = "sim_post_commit_01"
    reg.bind_simulation_ownership(sim_id, "bank_a", auth_token=env["tokens"]["admin"])

    cand_path, receipt = _build_candidate(env, sim_id)

    # Intercept finalize_pending_mutation on the audit chain
    audit_chain = ImmutableAuditChain.get_instance()
    orig_finalize = audit_chain.finalize_pending_mutation

    def mock_finalize_failure(*args, **kwargs):
        raise RuntimeError("Injected transient disk/network error during audit finalization")

    audit_chain.finalize_pending_mutation = mock_finalize_failure

    with pytest.raises((RuntimeError, Exception)):
        reg.save_version(
            simulation_id=sim_id,
            source_model_path=cand_path,
            metrics={"auc": 0.94, "loss": 0.05},
            parameters={"architecture": "RegressionLinearNet"},
            auth_token=env["tokens"]["ml"],
            training_receipt=receipt.to_dict(),
        )

    # Restore original finalizer
    audit_chain.finalize_pending_mutation = orig_finalize

    # 1. Verify artifact exists on disk at expected canonical path
    published_artifact = os.path.join(reg._get_sim_dir(sim_id), "model_v1.pt")
    assert os.path.exists(published_artifact), "Committed model artifact was destroyed by audit failure!"

    # 2. Verify manifest has version 1 durably recorded
    manifest = reg._load_manifest(sim_id)
    assert len(manifest) == 1, "Committed manifest entry was missing or rolled back!"
    entry = manifest[0]
    assert entry["version"] == 1
    assert entry["owning_bank_id"] == "bank_a"
    mutation_id = entry.get("registration_mutation_id") or entry.get("last_mutation_id")
    assert mutation_id is not None

    # 3. Verify audit record is NOT marked FAILED; it should remain in PENDING state
    records = audit_chain.get_snapshot()
    terminal_failures = [
        r for r in records
        if (r.details or {}).get("mutation_stage") == "FAILED"
        and (r.details or {}).get("mutation_id") == mutation_id
    ]
    assert len(terminal_failures) == 0, "Committed registration was falsely marked FAILED!"

    pending_records = [
        r for r in records
        if (r.details or {}).get("mutation_stage") == "PENDING"
        and (r.details or {}).get("mutation_id") == mutation_id
    ]
    assert len(pending_records) == 1, "Pending registration intent was lost!"

    # 4. Run deterministic reconciliation
    recon_result = reg.reconcile_incomplete_mutations()
    assert any(r.get("mutation_id") == mutation_id and r.get("status") == "COMMITTED" for r in recon_result)

    # 5. Verify audit record is now finalized to COMMITTED
    records_after = audit_chain.get_snapshot()
    terminal_committed = [
        r for r in records_after
        if (r.details or {}).get("mutation_stage") == "COMMITTED"
        and (r.details or {}).get("mutation_id") == mutation_id
    ]
    assert len(terminal_committed) == 1, "Reconciliation failed to mark committed registration!"

    # 6. Verify verified model loading succeeds seamlessly
    loaded_sd = reg.load_version(sim_id, 1)
    assert loaded_sd is not None
    assert len(loaded_sd) > 0
