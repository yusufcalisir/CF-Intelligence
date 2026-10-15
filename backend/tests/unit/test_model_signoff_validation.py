"""Focused regression tests verifying that invalid cryptographic sign-offs

and unauthorized approvals are rejected fail-closed without modifying manifest state
or recording false approvals in audit reconciliation.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime

import pytest
import torch
import torch.nn as nn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.model_registry import (
    ModelIntegrityError,
    ModelRegistry,
    generate_signoff_signature,
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


class SignoffNet(nn.Module):
    def __init__(self, in_features: int = 10, out_features: int = 2):
        super().__init__()
        self.fc = nn.Linear(in_features, out_features)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.fc(x)


@pytest.fixture
def signoff_validation_env(tmp_path, monkeypatch):
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
    payload_secret = "CFI_PAYLOAD_SIGNING_SECRET_2026_TEST"

    monkeypatch.setenv("CFI_AUDIT_LEDGER_PATH", audit_file)
    monkeypatch.setenv("CFI_AUDIT_LEDGER_SECRET", audit_secret)
    monkeypatch.setenv("CFI_TRUST_STORE_PATH", trust_file)
    monkeypatch.setenv("CFI_TRUST_STORE_ADMIN_TOKEN", admin_token)
    monkeypatch.setenv("CFI_MODEL_REGISTRY_DIR", reg_dir)
    monkeypatch.setenv("CFI_JWT_SIGNING_SECRET", jwt_secret)
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", account_file)
    monkeypatch.setenv("CFI_ACCOUNT_AUTHORITY_SECRET", authority_secret)
    monkeypatch.setenv("CFI_PAYLOAD_SIGNING_SECRET", payload_secret)
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

    u_ml = auth_svc.register_user("alice_ml", "Pass12345678!", "bank_a", ["ml_engineer"], clearance_level=2)
    u_comp = auth_svc.register_user("bob_compliance", "Pass12345678!", "bank_a", ["compliance_officer"], clearance_level=2)
    u_unauth = auth_svc.register_user("charlie_viewer", "Pass12345678!", "bank_a", ["auditor"], clearance_level=1)
    u_admin = auth_svc.register_user("admin_dan", "Pass12345678!", "bank_a", ["admin"], clearance_level=3)

    tokens = {
        "ml": auth_svc.create_token_bundle(u_ml).access_token,
        "compliance": auth_svc.create_token_bundle(u_comp).access_token,
        "unauth": auth_svc.create_token_bundle(u_unauth).access_token,
        "admin": auth_svc.create_token_bundle(u_admin).access_token,
    }

    priv = ed25519.Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    kid = "issuer_signoff_val_01"
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
        "reg_dir": reg_dir,
        "registry": registry,
        "tokens": tokens,
        "payload_secret": payload_secret,
        "issuer": issuer,
        "verifier": verifier,
        "pub_hex": pub_hex,
    }

    get_settings.cache_clear()
    AuthenticationService._instance = None
    ImmutableAuditChain._instance = None


def _register_test_model(env: dict, sim_id: str) -> None:
    reg: ModelRegistry = env["registry"]
    reg.bind_simulation_ownership(sim_id, "bank_a", auth_token=env["tokens"]["admin"])

    torch.manual_seed(42)
    net = SignoffNet()
    cand_path = str(env["tmp_path"] / f"signoff_model_{sim_id}.pt")
    torch.save(net.state_dict(), cand_path)

    with open(cand_path, "rb") as f:
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
        observed_metrics={"loss": 0.05, "auc": 0.93},
        artifact_sha256=msha,
        architecture="SignoffNet",
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
            architecture="SignoffNet",
            input_dim=10,
            feature_names=[f"f{i}" for i in range(10)],
            feature_schema_version="2.0",
        )
    finally:
        settings.app_env = orig

    reg.save_version(
        simulation_id=sim_id,
        source_model_path=cand_path,
        metrics={"auc": 0.93, "loss": 0.05},
        parameters={"architecture": "SignoffNet"},
        auth_token=env["tokens"]["ml"],
        training_receipt=receipt.to_dict(),
    )


def test_invalid_second_signoff_signature_is_rejected_without_persistence(signoff_validation_env):
    """Verify that submitting an invalid cryptographic signature on a second sign-off

    in production mode fails closed, does not persist the invalid sign-off to the
    manifest, does not record the mutation in applied_mutation_ids, and leaves no
    reconcilable approval.
    """
    env = signoff_validation_env
    reg: ModelRegistry = env["registry"]
    sim_id = "sim_signoff_val_01"
    _register_test_model(env, sim_id)

    # 1. First sign-off: ML engineer with valid signature
    manifest_init = reg._load_manifest(sim_id)
    model_sha = manifest_init[0]["sha256"]
    valid_sig_ml = generate_signoff_signature(
        simulation_id=sim_id,
        version=1,
        artifact_sha256=model_sha,
        role="ml_engineer",
        user="alice_ml",
        signing_secret=env["payload_secret"],
    )
    res_1 = reg.sign_off(
        sim_id,
        1,
        "ml_engineer",
        "alice_ml",
        valid_sig_ml,
        auth_token=env["tokens"]["ml"],
    )
    assert len(res_1.get("sign_offs", [])) == 1

    manifest_before = reg._load_manifest(sim_id)
    entry_before = manifest_before[0]
    assert len(entry_before.get("sign_offs", [])) == 1
    applied_mutations_before = list(entry_before.get("applied_mutation_ids", []))

    # 2. Second sign-off: Compliance officer with INVALID signature
    invalid_sig = "invalidsignature000000000000000000000000000000000000000000000000000000"

    with pytest.raises(ModelIntegrityError, match="sign-offs lack valid cryptographic signatures|Cryptographic signature verification failed"):
        reg.sign_off(
            sim_id,
            1,
            "compliance_officer",
            "bob_compliance",
            invalid_sig,
            auth_token=env["tokens"]["compliance"],
        )

    # 3. Verify manifest was NOT mutated by the rejected sign-off
    manifest_after = reg._load_manifest(sim_id)
    entry_after = manifest_after[0]

    # Must still only have 1 active sign-off (the valid ML sign-off)
    active_sign_offs = [s for s in entry_after.get("sign_offs", []) if s.get("is_active", True) is not False]
    assert len(active_sign_offs) == 1, "Invalid sign-off was appended to active sign_offs in manifest!"
    assert active_sign_offs[0]["role"] == "ml_engineer"

    # Must not contain any entry for compliance_officer
    compliance_entries = [s for s in entry_after.get("sign_offs", []) if s.get("role") == "compliance_officer"]
    assert len(compliance_entries) == 0, "Rejected sign-off was persisted to manifest!"

    # Applied mutation IDs must not have added the rejected sign-off mutation ID
    assert len(entry_after.get("applied_mutation_ids", [])) == len(applied_mutations_before)

    # 4. Verify audit ledger does NOT record MODEL_SIGNOFF_COMMITTED
    audit_chain = ImmutableAuditChain.get_instance()
    audit_records = audit_chain.get_snapshot()
    false_committed = [
        r for r in audit_records
        if (r.details or {}).get("mutation_stage") == "COMMITTED"
        and (r.details or {}).get("role") == "compliance_officer"
    ]
    assert len(false_committed) == 0, "Audit ledger falsely recorded MODEL_SIGNOFF_COMMITTED!"

    # 5. Run reconciliation and verify no false approval is created
    recon_res = reg.reconcile_incomplete_mutations()
    assert not any(r.get("status") == "COMMITTED" for r in recon_res), "False approval was reconciled to COMMITTED!"

    manifest_final = reg._load_manifest(sim_id)
    assert len(manifest_final[0].get("sign_offs", [])) == 1

    # 6. Verify subsequent valid signature by compliance officer works properly
    valid_sig_comp = generate_signoff_signature(
        simulation_id=sim_id,
        version=1,
        artifact_sha256=model_sha,
        role="compliance_officer",
        user="bob_compliance",
        signing_secret=env["payload_secret"],
    )
    res_2 = reg.sign_off(
        sim_id,
        1,
        "compliance_officer",
        "bob_compliance",
        valid_sig_comp,
        auth_token=env["tokens"]["compliance"],
    )
    assert res_2["status"] in ("champion", "approved", "active")
    manifest_approved = reg._load_manifest(sim_id)
    assert len(manifest_approved[0].get("sign_offs", [])) == 2
