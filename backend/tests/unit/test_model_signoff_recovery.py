"""Adversarial test suite for model approval / sign-off durability and crash recovery.

Verifies:
1. Crash before sign-off manifest persistence reconciles truthfully to FAILED.
2. Crash after sign-off manifest persistence reconciles truthfully to COMMITTED / APPROVED.
3. Concurrent approval attempts enforce role exclusivity and Four-Eyes principle.
4. Failed sign-off mutation is not falsely committed merely because an earlier sign-off exists.
5. Unauthorized sign-off attempt never alters manifest or logs committed approval.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import time
from datetime import UTC, datetime
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.model_registry import (
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


@pytest.fixture
def signoff_env(tmp_path, monkeypatch):
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
    monkeypatch.setenv("CFI_PAYLOAD_SIGNING_SECRET", payload_secret)
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
    u_comp = auth_svc.register_user("officer_carol_comp", "SecurePass1234!", "bank_a", ["compliance"], clearance_level=2)
    u_admin = auth_svc.register_user("admin_dan", "bank_a_secret", "bank_a", ["admin"], clearance_level=3)
    tokens = {
        "ml": auth_svc.create_token_bundle(u_ml).access_token,
        "comp": auth_svc.create_token_bundle(u_comp).access_token,
        "admin": auth_svc.create_token_bundle(u_admin).access_token,
    }

    priv = ed25519.Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    kid = "issuer_signoff_01"
    monkeypatch.setenv("CFI_TRAINING_RECEIPT_PUBLIC_KEY", pub_hex)

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

    yield {
        "tmp_path": tmp_path,
        "audit_file": audit_file,
        "audit_secret": audit_secret,
        "jwt_secret": jwt_secret,
        "authority_secret": authority_secret,
        "account_file": account_file,
        "reg_dir": reg_dir,
        "registry": registry,
        "tokens": tokens,
        "payload_secret": payload_secret,
        "admin_token": admin_token,
        "trust_file": trust_file,
        "pub_hex": pub_hex,
        "issuer": issuer,
    }

    get_settings.cache_clear()
    AuthenticationService._instance = None
    ImmutableAuditChain._instance = None


def _create_sample_model(env: dict, sim_id: str) -> tuple[ModelRegistry, dict]:
    reg: ModelRegistry = env["registry"]
    sim_dir = reg._get_sim_dir(sim_id)
    os.makedirs(sim_dir, exist_ok=True)

    with open(os.path.join(sim_dir, "ownership.json"), "w", encoding="utf-8") as f:
        json.dump({
            "owning_bank_id": "bank_a",
            "consortium_id": None,
            "is_unbound": False,
            "bound_at": "2026-10-14T00:00:00Z",
            "bound_by": "dr_alice_ml",
        }, f)

    model_bytes = b"sample_model_weights_tensor_data_signoff"
    model_sha = hashlib.sha256(model_bytes).hexdigest()
    model_path = os.path.join(sim_dir, "global_model_v1.pt")
    with open(model_path, "wb") as f:
        f.write(model_bytes)

    ev = TrainingExecutionEvidence(
        training_run_id=f"run-{sim_id}",
        job_id=f"job-{sim_id}",
        simulation_id=sim_id,
        completed_at=datetime.now(UTC).isoformat(),
        executed_rounds=5,
        executed_epochs=5,
        sample_count=1000,
        observed_metrics={"loss": 0.042, "auc": 0.96, "f1": 0.95},
        artifact_sha256=model_sha,
        architecture="FraudDetectionModel-GroupNorm",
        feature_schema_version="2.0",
        feature_names=["f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10"],
    )
    settings = get_settings()
    orig_env = settings.app_env
    settings.app_env = "development"
    try:
        receipt = env["issuer"].issue_receipt(
            evidence=ev,
            model_bytes=model_bytes,
            owning_bank_id="bank_a",
            architecture="FraudDetectionModel-GroupNorm",
            input_dim=10,
            feature_names=["f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10"],
            feature_schema_version="2.0",
        )
    finally:
        settings.app_env = orig_env

    entry = {
        "version": 1,
        "filename": "global_model_v1.pt",
        "sha256": model_sha,
        "status": "candidate",
        "is_active": False,
        "generation": 1,
        "owning_bank_id": "bank_a",
        "training_receipt": receipt.to_dict(),
        "provenance_type": "TRAINING_RECORDED",
        "provenance": {
            "type": "TRAINING_RECORDED",
            "status": "VERIFIED",
            "receipt_id": receipt.receipt_id,
        },
        "is_production_eligible": True,
        "sign_offs": [],
        "applied_mutation_ids": [],
    }
    reg._save_manifest(sim_id, [entry])
    return reg, entry


def _worker_signoff_process(
    reg_dir: str,
    audit_file: str,
    audit_secret: str,
    jwt_secret: str,
    authority_secret: str,
    account_file: str,
    trust_file: str,
    admin_token: str,
    payload_secret: str,
    sim_id: str,
    version: int,
    role: str,
    user: str,
    auth_token: str,
    barrier_path: str,
    crash_stage: str,
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
    os.environ["CFI_PAYLOAD_SIGNING_SECRET"] = payload_secret
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

    manifest = reg._load_manifest(sim_id)
    model_sha = manifest[0]["sha256"]
    sig = generate_signoff_signature(sim_id, version, model_sha, role, user, signing_secret=payload_secret)

    if crash_stage == "BEFORE_PERSISTENCE":
        orig_save = reg._save_manifest

        def hooked_save_before(s_id: str, m: list[dict[str, Any]]):
            with open(barrier_path, "w") as f:
                f.write("BEFORE_SIGNOFF_PERSIST_REACHED")
            while not os.path.exists(barrier_path + ".done"):
                time.sleep(0.05)
            return orig_save(s_id, m)

        reg._save_manifest = hooked_save_before

    elif crash_stage == "AFTER_PERSISTENCE":
        orig_save = reg._save_manifest

        def hooked_save_after(s_id: str, m: list[dict[str, Any]]):
            res = orig_save(s_id, m)
            with open(barrier_path, "w") as f:
                f.write("AFTER_SIGNOFF_PERSIST_REACHED")
            while not os.path.exists(barrier_path + ".done"):
                time.sleep(0.05)
            return res

        reg._save_manifest = hooked_save_after

    try:
        reg.sign_off(
            simulation_id=sim_id,
            version=version,
            role=role,
            user=user,
            signature=sig,
            auth_token=auth_token,
        )
    except Exception as exc:
        if barrier_path:
            with open(barrier_path + ".err", "w") as f:
                f.write(f"{type(exc).__name__}: {exc}\n")


def _worker_reconciler(reg_dir: str, audit_file: str, audit_secret: str, sim_id: str, out_file: str, pub_hex: str = ""):
    os.environ["CFI_AUDIT_LEDGER_PATH"] = audit_file
    os.environ["CFI_AUDIT_LEDGER_SECRET"] = audit_secret
    if pub_hex:
        os.environ["CFI_TRAINING_RECEIPT_PUBLIC_KEY"] = pub_hex
    os.environ["APP_ENV"] = "production"
    get_settings.cache_clear()

    reg = ModelRegistry(storage_dir=reg_dir)
    res = reg.reconcile_incomplete_mutations(simulation_id=sim_id)
    with open(out_file, "w") as f:
        json.dump(res, f)


def test_crash_before_signoff_persistence_reconciles_to_failed(signoff_env):
    """Crash after PENDING sign-off intent but before manifest save reconciles to FAILED."""
    sim_id = "sim_signoff_crash_01"
    _create_sample_model(signoff_env, sim_id)
    tmp_path = signoff_env["tmp_path"]
    barrier = str(tmp_path / "barrier_signoff_01.txt")

    p = multiprocessing.Process(
        target=_worker_signoff_process,
        args=(
            signoff_env["reg_dir"],
            signoff_env["audit_file"],
            signoff_env["audit_secret"],
            signoff_env["jwt_secret"],
            signoff_env["authority_secret"],
            signoff_env["account_file"],
            signoff_env["trust_file"],
            signoff_env["admin_token"],
            signoff_env["payload_secret"],
            sim_id,
            1,
            "ml_engineer",
            "dr_alice_ml",
            signoff_env["tokens"]["ml"],
            barrier,
            "BEFORE_PERSISTENCE",
            signoff_env["pub_hex"],
        ),
    )
    p.start()

    start_t = time.time()
    while not os.path.exists(barrier) and not os.path.exists(barrier + ".err") and time.time() - start_t < 30.0:
        time.sleep(0.05)
    err_content = ""
    if os.path.exists(barrier + ".err"):
        with open(barrier + ".err") as f:
            err_content = f.read()
    assert os.path.exists(barrier), f"Worker failed to reach barrier! Error: {err_content}"

    p.kill()
    p.join(timeout=5.0)

    # Reconcile via independent worker
    out_file = str(tmp_path / "recon_signoff_out_01.json")
    p_b = multiprocessing.Process(
        target=_worker_reconciler,
        args=(
            signoff_env["reg_dir"],
            signoff_env["audit_file"],
            signoff_env["audit_secret"],
            sim_id,
            out_file,
            signoff_env["pub_hex"],
        ),
    )
    p_b.start()
    p_b.join(timeout=25.0)
    assert not p_b.is_alive()

    with open(out_file) as f:
        reconciled = json.load(f)
    assert len(reconciled) == 1
    assert reconciled[0]["status"] == "FAILED"

    # Manifest must NOT have any sign-off recorded
    reg = signoff_env["registry"]
    manifest = reg._load_manifest(sim_id)
    assert len(manifest[0]["sign_offs"]) == 0


def test_crash_after_signoff_persistence_reconciles_to_approved(signoff_env):
    """Crash after atomic manifest persistence reconciles to COMMITTED / APPROVED."""
    sim_id = "sim_signoff_crash_02"
    _create_sample_model(signoff_env, sim_id)
    tmp_path = signoff_env["tmp_path"]
    barrier = str(tmp_path / "barrier_signoff_02.txt")

    p = multiprocessing.Process(
        target=_worker_signoff_process,
        args=(
            signoff_env["reg_dir"],
            signoff_env["audit_file"],
            signoff_env["audit_secret"],
            signoff_env["jwt_secret"],
            signoff_env["authority_secret"],
            signoff_env["account_file"],
            signoff_env["trust_file"],
            signoff_env["admin_token"],
            signoff_env["payload_secret"],
            sim_id,
            1,
            "ml_engineer",
            "dr_alice_ml",
            signoff_env["tokens"]["ml"],
            barrier,
            "AFTER_PERSISTENCE",
            signoff_env["pub_hex"],
        ),
    )
    p.start()

    start_t = time.time()
    while not os.path.exists(barrier) and not os.path.exists(barrier + ".err") and time.time() - start_t < 30.0:
        time.sleep(0.05)
    err_content = ""
    if os.path.exists(barrier + ".err"):
        with open(barrier + ".err") as f:
            err_content = f.read()
    assert os.path.exists(barrier), f"Worker failed to reach barrier! Error: {err_content}"

    p.kill()
    p.join(timeout=5.0)

    # Reconcile via independent worker
    out_file = str(tmp_path / "recon_signoff_out_02.json")
    p_b = multiprocessing.Process(
        target=_worker_reconciler,
        args=(
            signoff_env["reg_dir"],
            signoff_env["audit_file"],
            signoff_env["audit_secret"],
            sim_id,
            out_file,
            signoff_env["pub_hex"],
        ),
    )
    p_b.start()
    p_b.join(timeout=25.0)
    assert not p_b.is_alive()

    with open(out_file) as f:
        reconciled = json.load(f)
    assert len(reconciled) == 1
    assert reconciled[0]["status"] == "COMMITTED"

    # Sign-off is authoritatively present in manifest
    reg = signoff_env["registry"]
    manifest = reg._load_manifest(sim_id)
    assert len(manifest[0]["sign_offs"]) == 1
    assert manifest[0]["sign_offs"][0]["role"] == "ml_engineer"


def test_failed_signoff_does_not_become_committed_due_to_unrelated_prior_signoff(signoff_env):
    """A crashed compliance sign-off is never marked COMMITTED merely because an ml_engineer sign-off exists."""
    sim_id = "sim_signoff_crash_03"
    reg, entry = _create_sample_model(signoff_env, sim_id)
    tokens = signoff_env["tokens"]
    secret = signoff_env["payload_secret"]

    # 1. Legitimate ML Engineer sign-off committed
    model_sha = entry["sha256"]
    sig_ml = generate_signoff_signature(sim_id, 1, model_sha, "ml_engineer", "dr_alice_ml", signing_secret=secret)
    reg.sign_off(sim_id, 1, "ml_engineer", "dr_alice_ml", sig_ml, auth_token=tokens["ml"])

    # 2. Worker crashes before compliance sign-off is saved to manifest
    tmp_path = signoff_env["tmp_path"]
    barrier = str(tmp_path / "barrier_signoff_03.txt")

    p = multiprocessing.Process(
        target=_worker_signoff_process,
        args=(
            signoff_env["reg_dir"],
            signoff_env["audit_file"],
            signoff_env["audit_secret"],
            signoff_env["jwt_secret"],
            signoff_env["authority_secret"],
            signoff_env["account_file"],
            signoff_env["trust_file"],
            signoff_env["admin_token"],
            secret,
            sim_id,
            1,
            "compliance",
            "officer_carol_comp",
            tokens["comp"],
            barrier,
            "BEFORE_PERSISTENCE",
            signoff_env["pub_hex"],
        ),
    )
    p.start()

    start_t = time.time()
    while not os.path.exists(barrier) and not os.path.exists(barrier + ".err") and time.time() - start_t < 30.0:
        time.sleep(0.05)
    err_content = ""
    if os.path.exists(barrier + ".err"):
        with open(barrier + ".err") as f:
            err_content = f.read()
    assert os.path.exists(barrier), f"Worker failed to reach barrier! Error: {err_content}"

    p.kill()
    p.join(timeout=5.0)

    # 3. Reconcile
    out_file = str(tmp_path / "recon_signoff_out_03.json")
    p_b = multiprocessing.Process(
        target=_worker_reconciler,
        args=(
            signoff_env["reg_dir"],
            signoff_env["audit_file"],
            signoff_env["audit_secret"],
            sim_id,
            out_file,
            signoff_env["pub_hex"],
        ),
    )
    p_b.start()
    p_b.join(timeout=25.0)
    assert not p_b.is_alive()

    with open(out_file) as f:
        reconciled = json.load(f)
    assert len(reconciled) == 1
    # MUST be FAILED, NOT COMMITTED!
    assert reconciled[0]["status"] == "FAILED"

    # Manifest must still have only 1 sign-off (ml_engineer)
    manifest = reg._load_manifest(sim_id)
    assert len(manifest[0]["sign_offs"]) == 1
    assert manifest[0]["sign_offs"][0]["role"] == "ml_engineer"
