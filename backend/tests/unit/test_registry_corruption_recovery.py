"""Focused regression tests verifying that unreadable or corrupted registry state

yields an INDETERMINATE recovery outcome rather than destructive artifact cleanup
or fabricated terminal failure events.
"""

from __future__ import annotations

import json
import os
import uuid

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
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
)
from app.config import get_settings
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.durable_account_store import DurableAccountStore
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain


class CorruptionLinearNet(nn.Module):
    def __init__(self, in_features: int = 10, out_features: int = 2):
        super().__init__()
        self.fc = nn.Linear(in_features, out_features)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.fc(x)


@pytest.fixture
def corruption_recovery_env(tmp_path, monkeypatch):
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

    u_ml = auth_svc.register_user("dr_alice_ml", "Pass12345678!", "bank_a", ["ml_engineer"], clearance_level=2)
    u_admin = auth_svc.register_user("admin_dan", "Pass12345678!", "bank_a", ["admin"], clearance_level=3)
    tokens = {
        "ml": auth_svc.create_token_bundle(u_ml).access_token,
        "admin": auth_svc.create_token_bundle(u_admin).access_token,
    }

    priv = ed25519.Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    kid = "issuer_corruption_01"
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


def test_corrupted_manifest_during_recovery_yields_indeterminate_without_deletion(corruption_recovery_env):
    """Verify that if registry.json is corrupt or unreadable during reconciliation,

    the recovery outcome is INDETERMINATE: no candidate artifacts are deleted,
    no terminal events are fabricated, and operations fail closed.
    """
    env = corruption_recovery_env
    reg: ModelRegistry = env["registry"]
    sim_id = "sim_corrupt_manifest_01"
    reg.bind_simulation_ownership(sim_id, "bank_a", auth_token=env["tokens"]["admin"])
    sim_dir = reg._get_sim_dir(sim_id, create=True)

    # 1. Create a genuine candidate artifact and publish it to model_v1.pt
    cand_path = str(env["tmp_path"] / "candidate_model.pt")
    torch.save(CorruptionLinearNet().state_dict(), cand_path)

    published_artifact = os.path.join(sim_dir, "model_v1.pt")
    with open(cand_path, "rb") as sf, open(published_artifact, "wb") as df:
        df.write(sf.read())

    mutation_id = f"mut_reg_{uuid.uuid4().hex}"

    # 2. Record an unfinalized PENDING audit mutation for this registration
    audit_chain = ImmutableAuditChain.get_instance()
    audit_chain.append_event(
        event_type="MODEL_REGISTRATION_PENDING",
        actor="dr_alice_ml",
        target_id=f"{sim_id}:v1",
        details={
            "mutation_id": mutation_id,
            "mutation_stage": "PENDING",
            "mutation_committed": False,
            "simulation_id": sim_id,
            "version": 1,
            "owning_bank_id": "bank_a",
            "action": "save_version",
            "decision": "PENDING",
        },
        actor_type="human_principal",
        actor_institution="bank_a",
        target_type="model_version",
        action="save_version",
        decision="PENDING",
    )

    # 3. Corrupt the manifest file (invalid JSON syntax)
    manifest_path = os.path.join(sim_dir, "registry.json")
    with open(manifest_path, "wb") as f:
        f.write(b"CORRUPTED_NON_JSON_DATA_<<<!!!")

    # 4. Verify check_version_mutation_status directly reports INDETERMINATE
    status = reg.check_version_mutation_status(sim_id, 1, mutation_id)
    assert status == "INDETERMINATE", f"Expected INDETERMINATE on corrupt manifest, got {status}"

    # 5. Run reconciliation
    recon_res = reg.reconcile_incomplete_mutations()
    assert not any(r.get("mutation_id") == mutation_id and r.get("status") in ("COMMITTED", "FAILED") for r in recon_res), "Corrupted manifest must NOT be reconciled to FAILED or COMMITTED!"

    # 6. Verify artifacts were NOT deleted
    assert os.path.exists(published_artifact), "Published artifact was destroyed during indeterminate state!"
    assert os.path.exists(cand_path), "Candidate artifact was destroyed during indeterminate state!"

    # 7. Verify no terminal audit record was fabricated
    records = audit_chain.get_snapshot()
    terminal_events = [
        r for r in records
        if (r.details or {}).get("mutation_stage") in ("FAILED", "COMMITTED")
        and (r.details or {}).get("mutation_id") == mutation_id
    ]
    assert len(terminal_events) == 0, "Fabricated terminal audit record was appended during indeterminate state!"

    # 8. Verify serving operations fail closed when manifest is corrupt
    with pytest.raises(ModelIntegrityError):
        reg.load_version(sim_id, 1)

    # 9. Repair the manifest to readable state and verify reconciliation succeeds
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump([
            {
                "version": 1,
                "registration_mutation_id": mutation_id,
                "applied_mutation_ids": [mutation_id],
                "filename": "model_v1.pt",
                "owning_bank_id": "bank_a",
                "consortium_id": None,
                "metrics": {"auc": 0.95},
                "parameters": {},
            }
        ], f)

    recon_res_repaired = reg.reconcile_incomplete_mutations()
    assert any(r.get("mutation_id") == mutation_id and r.get("status") == "COMMITTED" for r in recon_res_repaired)

    records_repaired = audit_chain.get_snapshot()
    committed_events = [
        r for r in records_repaired
        if (r.details or {}).get("mutation_stage") == "COMMITTED" and (r.details or {}).get("mutation_id") == mutation_id
    ]
    assert len(committed_events) == 1
