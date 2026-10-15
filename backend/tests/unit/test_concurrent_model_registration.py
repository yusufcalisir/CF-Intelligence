"""Adversarial test suite for concurrent model registrations and racing crash reconcilers.

Verifies:
1. Two concurrent OS processes registering models for the same simulation produce distinct, sequential, non-colliding versions.
2. Concurrent registrations never overwrite each other's model weights or manifest records.
3. Three independent OS reconciler processes racing on the same unfinished mutation produce exactly one terminal audit outcome.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
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


class SmallModel(nn.Module):
    def __init__(self, val: float = 1.0):
        super().__init__()
        self.fc = nn.Linear(4, 1)
        nn.init.constant_(self.fc.weight, val)

    def forward(self, x):
        return self.fc(x)


@pytest.fixture
def concur_env(tmp_path, monkeypatch):
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

    u_ml1 = auth_svc.register_user("dr_alice_ml", "SecurePass1234!", "bank_a", ["ml_engineer"], clearance_level=2)
    u_ml2 = auth_svc.register_user("dr_bob_ml", "SecurePass1234!", "bank_a", ["ml_engineer"], clearance_level=2)
    u_admin = auth_svc.register_user("admin_dan", "SecurePass1234!", "bank_a", ["admin"], clearance_level=3)
    tokens = {
        "ml1": auth_svc.create_token_bundle(u_ml1).access_token,
        "ml2": auth_svc.create_token_bundle(u_ml2).access_token,
        "admin": auth_svc.create_token_bundle(u_admin).access_token,
    }

    priv = ed25519.Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    kid = "issuer_concur_01"
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

    monkeypatch.setenv("CFI_TRAINING_RECEIPT_PUBLIC_KEY", pub_hex)
    monkeypatch.setenv("CFI_IDENTITY_STORE_PATH", account_file)
    monkeypatch.setenv("CFI_IDENTITY_STORE_SECRET", authority_secret)

    yield {
        "tmp_path": tmp_path,
        "audit_file": audit_file,
        "audit_secret": audit_secret,
        "jwt_secret": jwt_secret,
        "authority_secret": authority_secret,
        "account_file": account_file,
        "trust_file": trust_file,
        "admin_token": admin_token,
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


def _make_candidate(env: dict, sim_id: str, seed: float) -> tuple[str, Any]:
    net = SmallModel(seed)
    path = str(env["tmp_path"] / f"cand_{uuid.uuid4().hex}.pt")
    torch.save(net.state_dict(), path)
    with open(path, "rb") as f:
        data = f.read()
    sha = hashlib.sha256(data).hexdigest()

    ev = TrainingExecutionEvidence(
        training_run_id=f"run-{sim_id}-{seed}",
        job_id=f"job-{sim_id}-{seed}",
        simulation_id=sim_id,
        completed_at=datetime.now(UTC).isoformat(),
        executed_rounds=2,
        executed_epochs=2,
        sample_count=200,
        observed_metrics={"loss": 0.042, "auc": 0.90 + seed * 0.01},
        artifact_sha256=sha,
        architecture="SmallModel",
        feature_schema_version="2.0",
        feature_names=["f1", "f2", "f3", "f4"],
    )
    settings = get_settings()
    orig = settings.app_env
    settings.app_env = "development"
    try:
        receipt = env["issuer"].issue_receipt(
            evidence=ev,
            model_bytes=data,
            owning_bank_id="bank_a",
            architecture="SmallModel",
            input_dim=4,
            feature_names=["f1", "f2", "f3", "f4"],
            feature_schema_version="2.0",
        )
    finally:
        settings.app_env = orig

    return path, receipt


def _worker_register(
    reg_dir: str,
    audit_file: str,
    audit_secret: str,
    jwt_secret: str,
    authority_secret: str,
    account_file: str,
    trust_file: str,
    admin_token: str,
    sim_id: str,
    cand_path: str,
    receipt_dict: dict[str, Any],
    auth_token: str,
    actor: str,
    out_file: str,
    pub_hex: str = "",
):
    os.environ["CFI_AUDIT_LEDGER_PATH"] = audit_file
    os.environ["CFI_AUDIT_LEDGER_SECRET"] = audit_secret
    os.environ["CFI_JWT_SIGNING_SECRET"] = jwt_secret
    os.environ["CFI_IDENTITY_STORE_PATH"] = account_file
    os.environ["CFI_ACCOUNT_STORE_PATH"] = account_file
    os.environ["CFI_ACCOUNT_AUTHORITY_SECRET"] = authority_secret
    os.environ["CFI_IDENTITY_STORE_SECRET"] = authority_secret
    os.environ["CFI_TRUST_STORE_PATH"] = trust_file
    os.environ["CFI_TRUST_STORE_ADMIN_TOKEN"] = admin_token
    os.environ["CFI_MODEL_REGISTRY_DIR"] = reg_dir
    if pub_hex:
        os.environ["CFI_TRAINING_RECEIPT_PUBLIC_KEY"] = pub_hex
    os.environ["APP_ENV"] = "production"
    get_settings.cache_clear()

    auth_svc = AuthenticationService(
        store_path=account_file,
        signing_secret=jwt_secret,
        authority_secret=authority_secret,
        app_env="production",
    )
    AuthenticationService._instance = auth_svc

    trust_store = DurableTrustStore(trust_file_path=trust_file, admin_token=admin_token)
    verifier = TrainingReceiptVerifier(trust_store=trust_store)
    reg = ModelRegistry(storage_dir=reg_dir, verifier=verifier)

    try:
        res = reg.save_version(
            simulation_id=sim_id,
            source_model_path=cand_path,
            metrics={"auc": 0.90},
            receipt=receipt_dict,
            auth_token=auth_token,
            actor=actor,
        )
        with open(out_file, "w") as f:
            json.dump({"success": True, "res": res}, f)
    except Exception as exc:
        with open(out_file, "w") as f:
            json.dump({"success": False, "error": str(exc)}, f)


def _worker_reconcile(
    reg_dir: str,
    audit_file: str,
    audit_secret: str,
    sim_id: str,
    out_file: str,
):
    os.environ["CFI_AUDIT_LEDGER_PATH"] = audit_file
    os.environ["CFI_AUDIT_LEDGER_SECRET"] = audit_secret
    os.environ["APP_ENV"] = "production"
    get_settings.cache_clear()

    reg = ModelRegistry(storage_dir=reg_dir)
    res = reg.reconcile_incomplete_mutations(simulation_id=sim_id)
    with open(out_file, "w") as f:
        json.dump(res, f)


def test_concurrent_registrations_produce_distinct_sequential_versions(concur_env):
    """Two concurrent OS workers produce version 1 and version 2 without overwriting."""
    sim_id = "sim_concur_01"
    reg = concur_env["registry"]
    reg.bind_simulation_ownership(sim_id, "bank_a", auth_token=concur_env["tokens"]["admin"])

    cand1, rec1 = _make_candidate(concur_env, sim_id, 1.0)
    cand2, rec2 = _make_candidate(concur_env, sim_id, 2.0)

    tmp_path = concur_env["tmp_path"]
    out1 = str(tmp_path / "reg_out_1.json")
    out2 = str(tmp_path / "reg_out_2.json")

    p1 = multiprocessing.Process(
        target=_worker_register,
        args=(
            concur_env["reg_dir"],
            concur_env["audit_file"],
            concur_env["audit_secret"],
            concur_env["jwt_secret"],
            concur_env["authority_secret"],
            concur_env["account_file"],
            concur_env["trust_file"],
            concur_env["admin_token"],
            sim_id,
            cand1,
            rec1.to_dict(),
            concur_env["tokens"]["ml1"],
            "dr_alice_ml",
            out1,
            concur_env["pub_hex"],
        ),
    )
    p2 = multiprocessing.Process(
        target=_worker_register,
        args=(
            concur_env["reg_dir"],
            concur_env["audit_file"],
            concur_env["audit_secret"],
            concur_env["jwt_secret"],
            concur_env["authority_secret"],
            concur_env["account_file"],
            concur_env["trust_file"],
            concur_env["admin_token"],
            sim_id,
            cand2,
            rec2.to_dict(),
            concur_env["tokens"]["ml2"],
            "dr_bob_ml",
            out2,
            concur_env["pub_hex"],
        ),
    )

    p1.start()
    p2.start()

    p1.join(timeout=25.0)
    p2.join(timeout=25.0)

    assert not p1.is_alive()
    assert not p2.is_alive()

    with open(out1) as f:
        r1 = json.load(f)
    with open(out2) as f:
        r2 = json.load(f)

    assert r1.get("success") is True, f"Worker 1 failed: {r1}"
    assert r2.get("success") is True, f"Worker 2 failed: {r2}"

    v1 = r1["res"]["version"]
    v2 = r2["res"]["version"]

    # Distinct versions allocated
    assert {v1, v2} == {1, 2}

    # Manifest contains both versions
    manifest = reg._load_manifest(sim_id)
    assert len(manifest) == 2
    versions = {e["version"] for e in manifest}
    assert versions == {1, 2}

    # Files on disk both exist and are distinct
    sim_dir = reg._get_sim_dir(sim_id)
    f1 = os.path.join(sim_dir, "model_v1.pt")
    f2 = os.path.join(sim_dir, "model_v2.pt")
    assert os.path.exists(f1)
    assert os.path.exists(f2)

    with open(f1, "rb") as fp:
        d1 = fp.read()
    with open(f2, "rb") as fp:
        d2 = fp.read()
    assert d1 != d2, "Artifacts have identical content - collision detected!"

    # Both models load successfully
    m1 = reg.load_version(sim_id, 1, target_class=SmallModel)
    m2 = reg.load_version(sim_id, 2, target_class=SmallModel)
    assert isinstance(m1, SmallModel)
    assert isinstance(m2, SmallModel)


def test_three_racing_reconcilers_produce_exactly_one_terminal_outcome(concur_env):
    """Three independent OS reconcilers racing on an unfinished mutation produce exactly one terminal record."""
    sim_id = "sim_concur_02"
    reg = concur_env["registry"]
    reg.bind_simulation_ownership(sim_id, "bank_a", auth_token=concur_env["tokens"]["admin"])

    # Manually append a genuine PENDING mutation in audit ledger (simulating an unfinished registration)
    chain = ImmutableAuditChain(store_path=concur_env["audit_file"], signing_secret=concur_env["audit_secret"])
    mut_id = f"mut_race_{uuid.uuid4().hex}"
    chain.append_event(
        event_type="MODEL_REGISTRATION_PENDING",
        actor="crashed_trainer",
        target_id=f"{sim_id}:v1",
        details={
            "mutation_id": mut_id,
            "mutation_stage": "PENDING",
            "mutation_committed": False,
            "simulation_id": sim_id,
            "version": 1,
            "action": "save_version",
            "decision": "PENDING",
        },
        action="save_version",
        decision="PENDING",
    )

    tmp_path = concur_env["tmp_path"]
    out_files = [str(tmp_path / f"recon_race_{i}.json") for i in range(3)]

    workers = [
        multiprocessing.Process(
            target=_worker_reconcile,
            args=(
                concur_env["reg_dir"],
                concur_env["audit_file"],
                concur_env["audit_secret"],
                sim_id,
                out_files[i],
            ),
        )
        for i in range(3)
    ]

    for w in workers:
        w.start()

    for w in workers:
        w.join(timeout=25.0)
        assert not w.is_alive()

    # Read output results
    reconciled_results = []
    for fpath in out_files:
        with open(fpath) as f:
            reconciled_results.append(json.load(f))

    # Exactly ONE reconciler should have finalized the mutation (status == FAILED),
    # while the other two should find it ALREADY_RESOLVED or empty
    resolved_counts = 0
    already_resolved_counts = 0
    for r_list in reconciled_results:
        for r in r_list:
            if r.get("mutation_id") == mut_id:
                if r.get("status") == "FAILED":
                    resolved_counts += 1
                elif r.get("status") == "ALREADY_RESOLVED":
                    already_resolved_counts += 1

    assert resolved_counts == 1, f"Expected exactly one FAILED finalization, got {resolved_counts}"

    # Audit ledger MUST have exactly ONE terminal event for this mutation_id
    chain_fresh = ImmutableAuditChain(store_path=concur_env["audit_file"], signing_secret=concur_env["audit_secret"])
    terminal_events = [
        e for e in chain_fresh.chain
        if e.details.get("mutation_id") == mut_id and e.details.get("mutation_stage") in ("COMMITTED", "FAILED")
    ]
    assert len(terminal_events) == 1, f"Expected exactly one terminal event, found {len(terminal_events)}"
    assert terminal_events[0].decision == "FAILED"
    assert chain_fresh.verify_chain_integrity().is_valid is True
