"""Focused regression tests verifying truthful transaction outcomes during registration persistence failures.

Verifies:
Test A: Manifest committed via os.replace before persistence helper raises.
        Distinguishes durable committed mutation from function return status.
        Never records FAILED; reconciliation finalizes to COMMITTED.
Test B: Unreadable manifest during post-commit exception handling.
        Yields INDETERMINATE; no false FAILED/COMMITTED event, no destructive cleanup.
        Recovers successfully once authoritative state is readable.
"""

from __future__ import annotations

import hashlib
import json
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


class LinearModel(nn.Module):
    def __init__(self, in_features: int = 10, out_features: int = 2):
        super().__init__()
        self.fc = nn.Linear(in_features, out_features)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.fc(x)


@pytest.fixture
def commit_outcome_env(tmp_path, monkeypatch):
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
    tokens = {
        "ml": auth_svc.create_token_bundle(u_ml).access_token,
    }

    priv = ed25519.Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    kid = "issuer_commit_outcome_01"
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
    torch.manual_seed(202)
    net = LinearModel()
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
        architecture="LinearModel",
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
            architecture="LinearModel",
            input_dim=10,
            feature_names=[f"f{i}" for i in range(10)],
            feature_schema_version="2.0",
        )
    finally:
        settings.app_env = orig

    return candidate_path, receipt


def test_manifest_committed_before_persistence_helper_raises(commit_outcome_env):
    """Test A: Injects an exception after os.replace() has published the new manifest

    but before the persistence helper returns.
    Verifies that the registration outcome is derived from durable state on disk:
    1. The model artifact still exists.
    2. The manifest contains the exact registration mutation ID.
    3. No MODEL_REGISTRATION_FAILED terminal event is recorded.
    4. Subsequent reconciliation resolves the pending mutation to exactly one COMMITTED event.
    5. Artifact integrity verification succeeds.
    """
    env = commit_outcome_env
    reg: ModelRegistry = env["registry"]
    sim_id = "sim_test_a_commit_01"

    cand_path, receipt = _build_candidate(env, sim_id)

    # Intercept _save_manifest to execute the full atomic replace on disk,
    # then raise before returning to simulate failure in post-replace directory sync or network ACK.
    orig_save_manifest = reg._save_manifest

    def injected_save_manifest(s_id: str, m_list: list[dict[str, Any]]) -> None:
        orig_save_manifest(s_id, m_list)
        raise RuntimeError("Injected transient I/O failure immediately after manifest os.replace")

    reg._save_manifest = injected_save_manifest

    with pytest.raises(RuntimeError):
        reg.save_version(
            simulation_id=sim_id,
            source_model_path=cand_path,
            metrics={"auc": 0.95, "loss": 0.04},
            parameters={"architecture": "LinearModel"},
            auth_token=env["tokens"]["ml"],
            training_receipt=receipt.to_dict(),
        )

    # Restore healthy persistence helper
    reg._save_manifest = orig_save_manifest

    # 1. Model artifact exists on disk at expected canonical path
    sim_dir = reg._get_sim_dir(sim_id)
    published_artifact = os.path.join(sim_dir, "model_v1.pt")
    assert os.path.exists(published_artifact), "Committed artifact was destroyed after persistence helper exception!"

    # 2. Manifest contains the exact registration mutation ID
    raw_manifest_path = os.path.join(sim_dir, "registry.json")
    with open(raw_manifest_path, encoding="utf-8") as f:
        manifest_data = json.load(f)

    assert len(manifest_data) == 1
    entry = manifest_data[0]
    assert entry["version"] == 1
    mutation_id = entry.get("registration_mutation_id") or entry.get("last_mutation_id")
    assert mutation_id is not None
    assert mutation_id in entry.get("applied_mutation_ids", [])

    # 3. Verify audit chain: NO MODEL_REGISTRATION_FAILED was recorded
    audit_chain = ImmutableAuditChain.get_instance()
    records = audit_chain.get_snapshot()

    terminal_failures = [
        r for r in records
        if (r.details or {}).get("mutation_stage") == "FAILED"
        and (r.details or {}).get("mutation_id") == mutation_id
    ]
    assert len(terminal_failures) == 0, "Committed registration was falsely finalized as FAILED in audit log!"

    pending_events = [
        r for r in records
        if (r.details or {}).get("mutation_stage") == "PENDING"
        and (r.details or {}).get("mutation_id") == mutation_id
    ]
    assert len(pending_events) == 1, "Pending registration intent was lost!"

    # 4. Subsequent deterministic reconciliation resolves mutation to COMMITTED
    recon_res = reg.reconcile_incomplete_mutations()
    assert any(
        r.get("mutation_id") == mutation_id and r.get("status") == "COMMITTED"
        for r in recon_res
    ), "Reconciliation failed to finalize durably committed mutation!"

    records_after = audit_chain.get_snapshot()
    committed_events = [
        r for r in records_after
        if (r.details or {}).get("mutation_stage") == "COMMITTED"
        and (r.details or {}).get("mutation_id") == mutation_id
    ]
    assert len(committed_events) == 1, "Expected exactly one terminal COMMITTED audit event!"

    # 5. Verified model loading succeeds seamlessly
    loaded_sd = reg.load_version(sim_id, 1)
    assert loaded_sd is not None
    assert len(loaded_sd) > 0


def test_unreadable_manifest_during_post_commit_exception_handling(commit_outcome_env):
    """Test B: Induces a registration exception while authoritative state cannot be reliably read.

    Verifies:
    1. No false FAILED or COMMITTED terminal event is recorded.
    2. No destructive cleanup of a potentially committed artifact.
    3. Pending evidence remains available in audit log.
    4. Later recovery succeeds once authoritative state becomes readable.
    """
    env = commit_outcome_env
    reg: ModelRegistry = env["registry"]
    sim_id = "sim_test_b_unreadable_01"

    cand_path, receipt = _build_candidate(env, sim_id)
    sim_dir = reg._get_sim_dir(sim_id)
    published_artifact = os.path.join(sim_dir, "model_v1.pt")

    orig_load_manifest = reg._load_manifest
    orig_save_manifest = reg._save_manifest

    # Induce an exception in save_version and simulate unreadable authoritative manifest during cleanup
    def unreadable_load_manifest(s_id: str) -> list[dict[str, Any]]:
        raise ModelIntegrityError(f"Registry manifest for '{s_id}' cannot be read: storage I/O error")

    def failing_save_manifest(s_id: str, m_list: list[dict[str, Any]]) -> None:
        # Commit the manifest file to disk first
        orig_save_manifest(s_id, m_list)
        # Then make manifest unreadable to simulate concurrent storage corruption
        reg._load_manifest = unreadable_load_manifest
        raise RuntimeError("Injected crash while storage medium degraded")

    reg._save_manifest = failing_save_manifest

    with pytest.raises(RuntimeError):
        reg.save_version(
            simulation_id=sim_id,
            source_model_path=cand_path,
            metrics={"auc": 0.94, "loss": 0.05},
            parameters={"architecture": "LinearModel"},
            auth_token=env["tokens"]["ml"],
            training_receipt=receipt.to_dict(),
        )

    # 1. Verify published artifact was NOT destroyed during indeterminate state
    assert os.path.exists(published_artifact), "Potentially committed artifact was deleted during unreadable state!"

    # 2. Verify no terminal audit record (FAILED or COMMITTED) was fabricated
    audit_chain = ImmutableAuditChain.get_instance()
    records = audit_chain.get_snapshot()

    pending_events = [
        r for r in records
        if (r.details or {}).get("mutation_stage") == "PENDING"
        and (r.details or {}).get("simulation_id") == sim_id
    ]
    assert len(pending_events) == 1, "Pending registration intent was lost!"
    mutation_id = (pending_events[0].details or {}).get("mutation_id")
    assert mutation_id is not None

    terminal_events = [
        r for r in records
        if (r.details or {}).get("mutation_stage") in ("FAILED", "COMMITTED")
        and (r.details or {}).get("mutation_id") == mutation_id
    ]
    assert len(terminal_events) == 0, "Fabricated terminal audit record during unreadable manifest state!"

    # 3. Verify reconciliation skips unreadable state safely without deleting artifact
    recon_res_unreadable = reg.reconcile_incomplete_mutations()
    assert any(
        r.get("mutation_id") == mutation_id and r.get("status") == "INDETERMINATE"
        for r in recon_res_unreadable
    ), "Reconciliation must report INDETERMINATE on unreadable manifest!"
    assert os.path.exists(published_artifact), "Reconciliation destroyed artifact during unreadable state!"

    # 4. Restore healthy readable state and verify reconciliation completes COMMITTED
    reg._load_manifest = orig_load_manifest
    reg._save_manifest = orig_save_manifest

    recon_res_healthy = reg.reconcile_incomplete_mutations()
    assert any(
        r.get("mutation_id") == mutation_id and r.get("status") == "COMMITTED"
        for r in recon_res_healthy
    ), "Healthy reconciliation failed to finalize committed mutation!"

    # 5. Verified model loading succeeds
    loaded_sd = reg.load_version(sim_id, 1)
    assert loaded_sd is not None
    assert len(loaded_sd) > 0
