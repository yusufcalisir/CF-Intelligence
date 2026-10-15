"""Focused regression tests verifying that authoritative ownership state in ownership.json

is reconciled and synchronized into derived manifest metadata following interrupted transfers.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime

import pytest
import torch
import torch.nn as nn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.model_registry import (
    ModelAuthorizationError,
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


class OwnershipNet(nn.Module):
    def __init__(self, in_features: int = 10, out_features: int = 2):
        super().__init__()
        self.fc = nn.Linear(in_features, out_features)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.fc(x)


@pytest.fixture
def ownership_recon_env(tmp_path, monkeypatch):
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

    u_bank_a = auth_svc.register_user("alice_bank_a", "Pass12345678!", "bank_a", ["ml_engineer"], clearance_level=2)
    u_admin_a = auth_svc.register_user("admin_bank_a", "Pass12345678!", "bank_a", ["admin"], clearance_level=3)
    u_bank_b = auth_svc.register_user("bob_bank_b", "Pass12345678!", "bank_b", ["ml_engineer"], clearance_level=2)
    u_admin_b = auth_svc.register_user("admin_bank_b", "Pass12345678!", "bank_b", ["admin"], clearance_level=3)

    tokens = {
        "bank_a": auth_svc.create_token_bundle(u_bank_a).access_token,
        "admin_bank_a": auth_svc.create_token_bundle(u_admin_a).access_token,
        "bank_b": auth_svc.create_token_bundle(u_bank_b).access_token,
        "admin_bank_b": auth_svc.create_token_bundle(u_admin_b).access_token,
    }

    priv = ed25519.Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    kid = "issuer_owner_recon_01"
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
        "issuer": issuer,
        "verifier": verifier,
        "pub_hex": pub_hex,
    }

    get_settings.cache_clear()
    AuthenticationService._instance = None
    ImmutableAuditChain._instance = None


def _register_initial_model(env: dict, sim_id: str, owning_bank: str) -> None:
    reg: ModelRegistry = env["registry"]
    admin_token_key = f"admin_{owning_bank}"
    reg.bind_simulation_ownership(sim_id, owning_bank, auth_token=env["tokens"][admin_token_key])

    torch.manual_seed(101)
    net = OwnershipNet()
    cand_path = str(env["tmp_path"] / f"cand_{sim_id}.pt")
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
        observed_metrics={"loss": 0.05, "auc": 0.94},
        artifact_sha256=msha,
        architecture="OwnershipNet",
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
            owning_bank_id=owning_bank,
            architecture="OwnershipNet",
            input_dim=10,
            feature_names=[f"f{i}" for i in range(10)],
            feature_schema_version="2.0",
        )
    finally:
        settings.app_env = orig

    reg.save_version(
        simulation_id=sim_id,
        source_model_path=cand_path,
        metrics={"auc": 0.94, "loss": 0.05},
        parameters={"architecture": "OwnershipNet"},
        auth_token=env["tokens"][owning_bank],
        training_receipt=receipt.to_dict(),
    )


def test_interrupted_ownership_transfer_reconciles_derived_manifest_metadata(ownership_recon_env):
    """Verify that if an authorized ownership transfer writes ownership.json

    but crashes or fails before updating registry.json, subsequent manifest loading
    and reconciliation automatically repairs the derived manifest entry under lock,
    preventing silent reuse of stale cross-bank ownership metadata.
    """
    env = ownership_recon_env
    reg: ModelRegistry = env["registry"]
    sim_id = "sim_ownership_interrupted_01"

    # 1. Register initial model under bank_a
    _register_initial_model(env, sim_id, "bank_a")
    sim_dir = reg._get_sim_dir(sim_id, create=False)

    manifest_init = reg._load_manifest(sim_id)
    assert len(manifest_init) == 1
    assert manifest_init[0]["owning_bank_id"] == "bank_a"

    # 2. Simulate interrupted transfer:
    # Durably update authoritative ownership.json to bank_b, but leave registry.json with bank_a
    ownership_file = os.path.join(sim_dir, "ownership.json")
    with open(ownership_file, "w", encoding="utf-8") as f:
        json.dump({
            "owning_bank_id": "bank_b",
            "consortium_id": None,
            "updated_at": datetime.now(UTC).isoformat(),
            "transfer_reason": "Authorized portfolio migration",
        }, f)

    # Verify that before repair, raw registry.json contains stale bank_a
    raw_manifest_path = os.path.join(sim_dir, "registry.json")
    with open(raw_manifest_path, encoding="utf-8") as f:
        raw_manifest = json.load(f)
    assert raw_manifest[0]["owning_bank_id"] == "bank_a"

    # 3. Simulate process restart / new worker loading the manifest
    repaired_manifest = reg._load_manifest(sim_id)
    assert repaired_manifest[0]["owning_bank_id"] == "bank_b", "Derived manifest failed to synchronize with authoritative ownership.json!"

    # 4. Verify durable persistence of the repair
    with open(raw_manifest_path, encoding="utf-8") as f:
        persisted_after_repair = json.load(f)
    assert persisted_after_repair[0]["owning_bank_id"] == "bank_b", "Repaired manifest was not persisted under lock!"

    # 5. Verify bank_a is now rejected from performing owner-restricted actions
    cand_path_2 = str(env["tmp_path"] / "cand_unauth_a.pt")
    torch.save(OwnershipNet().state_dict(), cand_path_2)

    with pytest.raises(ModelAuthorizationError, match="Cannot overwrite established model ownership"):
        reg.save_version(
            simulation_id=sim_id,
            model_path=cand_path_2,
            metrics={"auc": 0.95},
            parameters={},
            owning_bank_id="bank_a",
            auth_token=env["tokens"]["bank_a"],
        )

    # 6. Verify anti-rebinding guarantees: attempting to rebind ownership directly raises ModelAuthorizationError
    with pytest.raises(ModelAuthorizationError, match="Unauthorized model ownership rebinding"):
        reg.bind_simulation_ownership(sim_id, "bank_a", auth_token=env["tokens"]["admin_bank_a"])
