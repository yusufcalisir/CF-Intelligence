"""Adversarial test suite for transactional model registration and crash recovery.

Verifies:
1. Process termination after registration PENDING but before artifact publication.
2. Process termination after artifact publication but before manifest persistence.
3. Process termination after manifest persistence but before audit finalization.
4. Full restart and artifact loading verification following committed registration.
5. Injected manifest persistence failure never yields a falsely registered model.
6. Reconciliation never deletes legitimate artifacts from previously completed versions.
7. Cryptographic training receipt SHA-256 mismatch rejects candidate fail-closed.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import time
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

mp_ctx = multiprocessing.get_context("spawn")


class SimpleLinearNet(nn.Module):
    def __init__(self, in_features: int = 10, out_features: int = 2):
        super().__init__()
        self.fc = nn.Linear(in_features, out_features)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.fc(x)


@pytest.fixture
def reg_crash_env(tmp_path, monkeypatch):
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

    # Setup Ed25519 training receipt issuer
    priv = ed25519.Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    kid = "issuer_reg_crash_01"
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
        "audit_secret": audit_secret,
        "jwt_secret": jwt_secret,
        "authority_secret": authority_secret,
        "account_file": account_file,
        "trust_file": trust_file,
        "admin_token": admin_token,
        "reg_dir": reg_dir,
        "registry": registry,
        "auth_svc": auth_svc,
        "tokens": tokens,
        "issuer": issuer,
        "verifier": verifier,
        "pub_hex": pub_hex,
    }

    get_settings.cache_clear()
    AuthenticationService._instance = None
    ImmutableAuditChain._instance = None


def _make_candidate_artifact(env: dict, sim_id: str, seed: int = 42) -> tuple[str, Any, bytes]:
    """Create a temporary serialized candidate model and valid cryptographic receipt."""
    torch.manual_seed(seed)
    net = SimpleLinearNet()
    tmp_path = str(env["tmp_path"] / f"candidate_{uuid.uuid4().hex}.pt")
    torch.save(net.state_dict(), tmp_path)

    with open(tmp_path, "rb") as f:
        mbytes = f.read()
    msha = hashlib.sha256(mbytes).hexdigest()

    ev = TrainingExecutionEvidence(
        training_run_id=f"run-{sim_id}",
        job_id=f"job-{sim_id}",
        simulation_id=sim_id,
        completed_at=datetime.now(UTC).isoformat(),
        executed_rounds=3,
        executed_epochs=3,
        sample_count=500,
        observed_metrics={"loss": 0.042, "auc": 0.95},
        artifact_sha256=msha,
        architecture="SimpleLinearNet",
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
            architecture="SimpleLinearNet",
            input_dim=10,
            feature_names=[f"f{i}" for i in range(10)],
            feature_schema_version="2.0",
        )
    finally:
        settings.app_env = orig

    return tmp_path, receipt, mbytes


def _worker_register_process(
    reg_dir: str,
    audit_file: str,
    audit_secret: str,
    jwt_secret: str,
    authority_secret: str,
    account_file: str,
    trust_file: str,
    admin_token: str,
    sim_id: str,
    source_model_path: str,
    receipt_dict: dict[str, Any],
    auth_token: str,
    barrier_path: str,
    crash_stage: str,
    pub_hex: str = "",
):
    """Child process that executes save_version with deterministic crash barriers."""
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

    if crash_stage == "BEFORE_PUBLISH":
        orig_replace = os.replace

        def hooked_replace(
            src: os.PathLike[str] | str | bytes,
            dst: os.PathLike[str] | str | bytes,
            *,
            src_dir_fd: int | None = None,
            dst_dir_fd: int | None = None,
        ) -> None:
            dst_str = os.fspath(dst) if not isinstance(dst, bytes) else dst.decode("utf-8", "ignore")
            if "model_v" in dst_str and not os.path.basename(dst_str).startswith(".tmp_"):
                with open(barrier_path, "w") as f:
                    f.write("BEFORE_PUBLISH_REACHED")
                while not os.path.exists(barrier_path + ".done"):
                    time.sleep(0.05)
            orig_replace(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

        os.replace = hooked_replace  # type: ignore[assignment]

    elif crash_stage == "BEFORE_MANIFEST":
        orig_save_manifest = reg._save_manifest

        def hooked_save_manifest(simulation_id: str, manifest: list[dict[str, Any]]) -> None:
            with open(barrier_path, "w") as f:
                f.write("BEFORE_MANIFEST_REACHED")
            while not os.path.exists(barrier_path + ".done"):
                time.sleep(0.05)
            orig_save_manifest(simulation_id, manifest)

        reg._save_manifest = hooked_save_manifest

    elif crash_stage == "AFTER_MANIFEST":
        orig_save_manifest = reg._save_manifest

        def hooked_save_manifest_after(simulation_id: str, manifest: list[dict[str, Any]]) -> None:
            orig_save_manifest(simulation_id, manifest)
            with open(barrier_path, "w") as f:
                f.write("AFTER_MANIFEST_REACHED")
            while not os.path.exists(barrier_path + ".done"):
                time.sleep(0.05)

        reg._save_manifest = hooked_save_manifest_after

    try:
        reg.save_version(
            simulation_id=sim_id,
            source_model_path=source_model_path,
            metrics={"auc": 0.95},
            receipt=receipt_dict,
            auth_token=auth_token,
            actor="dr_alice_ml",
        )
    except Exception as exc:
        if not os.path.exists(barrier_path):
            with open(barrier_path + ".err", "w") as f:
                f.write(f"{type(exc).__name__}: {exc}\n")


def _worker_reconciler(reg_dir: str, audit_file: str, audit_secret: str, sim_id: str, out_file: str, pub_hex: str = ""):
    os.environ["CFI_AUDIT_LEDGER_PATH"] = audit_file
    os.environ["CFI_AUDIT_LEDGER_SECRET"] = audit_secret
    os.environ["CFI_MODEL_REGISTRY_DIR"] = reg_dir
    if pub_hex:
        os.environ["CFI_TRAINING_RECEIPT_PUBLIC_KEY"] = pub_hex
    os.environ["APP_ENV"] = "production"
    get_settings.cache_clear()

    reg = ModelRegistry(storage_dir=reg_dir)
    res = reg.reconcile_incomplete_mutations(simulation_id=sim_id)
    with open(out_file, "w") as f:
        json.dump(res, f)


def test_crash_before_artifact_publication_reconciles_to_failed(reg_crash_env):
    """Crash after PENDING audit record but before artifact is published to canonical location."""
    sim_id = "sim_reg_crash_01"
    reg = reg_crash_env["registry"]
    reg.bind_simulation_ownership(sim_id, "bank_a", auth_token=reg_crash_env["tokens"]["admin"])

    cand_path, receipt, _ = _make_candidate_artifact(reg_crash_env, sim_id)
    tmp_path = reg_crash_env["tmp_path"]
    barrier = str(tmp_path / "barrier_01.txt")

    p = mp_ctx.Process(
        target=_worker_register_process,
        args=(
            reg_crash_env["reg_dir"],
            reg_crash_env["audit_file"],
            reg_crash_env["audit_secret"],
            reg_crash_env["jwt_secret"],
            reg_crash_env["authority_secret"],
            reg_crash_env["account_file"],
            reg_crash_env["trust_file"],
            reg_crash_env["admin_token"],
            sim_id,
            cand_path,
            receipt.to_dict(),
            reg_crash_env["tokens"]["ml"],
            barrier,
            "BEFORE_PUBLISH",
            reg_crash_env["pub_hex"],
        ),
    )
    p.start()

    start_t = time.time()
    while not os.path.exists(barrier) and not os.path.exists(barrier + ".err") and time.time() - start_t < 40.0:
        time.sleep(0.05)
    err_info = ""
    if os.path.exists(barrier + ".err"):
        with open(barrier + ".err") as f:
            err_info = f.read()
    assert os.path.exists(barrier), f"Worker failed to reach BEFORE_PUBLISH barrier! Error: {err_info}"

    p.kill()
    p.join(timeout=5.0)

    # Reconcile via independent worker
    out_file = str(tmp_path / "recon_out_01.json")
    p_b = mp_ctx.Process(
        target=_worker_reconciler,
        args=(
            reg_crash_env["reg_dir"],
            reg_crash_env["audit_file"],
            reg_crash_env["audit_secret"],
            sim_id,
            out_file,
            reg_crash_env["pub_hex"],
        ),
    )
    p_b.start()
    p_b.join(timeout=40.0)
    assert not p_b.is_alive()

    with open(out_file) as f:
        reconciled = json.load(f)
    assert len(reconciled) == 1
    assert reconciled[0]["status"] == "FAILED"

    # Manifest must NOT have any registered version
    manifest = reg._load_manifest(sim_id)
    assert len(manifest) == 0

    # Published artifact must not exist
    sim_dir = reg._get_sim_dir(sim_id)
    assert not os.path.exists(os.path.join(sim_dir, "model_v1.pt"))


def test_crash_after_artifact_publish_before_manifest_cleans_unregistered_file(reg_crash_env):
    """Crash after artifact rename but before manifest persistence: artifact is cleaned up."""
    sim_id = "sim_reg_crash_02"
    reg = reg_crash_env["registry"]
    reg.bind_simulation_ownership(sim_id, "bank_a", auth_token=reg_crash_env["tokens"]["admin"])

    cand_path, receipt, _ = _make_candidate_artifact(reg_crash_env, sim_id)
    tmp_path = reg_crash_env["tmp_path"]
    barrier = str(tmp_path / "barrier_02.txt")

    p = mp_ctx.Process(
        target=_worker_register_process,
        args=(
            reg_crash_env["reg_dir"],
            reg_crash_env["audit_file"],
            reg_crash_env["audit_secret"],
            reg_crash_env["jwt_secret"],
            reg_crash_env["authority_secret"],
            reg_crash_env["account_file"],
            reg_crash_env["trust_file"],
            reg_crash_env["admin_token"],
            sim_id,
            cand_path,
            receipt.to_dict(),
            reg_crash_env["tokens"]["ml"],
            barrier,
            "BEFORE_MANIFEST",
            reg_crash_env["pub_hex"],
        ),
    )
    p.start()

    start_t = time.time()
    while not os.path.exists(barrier) and not os.path.exists(barrier + ".err") and time.time() - start_t < 40.0:
        time.sleep(0.05)
    err_info = ""
    if os.path.exists(barrier + ".err"):
        with open(barrier + ".err") as f:
            err_info = f.read()
    assert os.path.exists(barrier), f"Worker failed to reach BEFORE_MANIFEST barrier! Error: {err_info}"

    p.kill()
    p.join(timeout=5.0)

    # Published artifact temporarily exists before reconciliation
    sim_dir = reg._get_sim_dir(sim_id)
    cand_artifact = os.path.join(sim_dir, "model_v1.pt")

    # Run reconciler
    out_file = str(tmp_path / "recon_out_02.json")
    p_b = mp_ctx.Process(
        target=_worker_reconciler,
        args=(
            reg_crash_env["reg_dir"],
            reg_crash_env["audit_file"],
            reg_crash_env["audit_secret"],
            sim_id,
            out_file,
            reg_crash_env["pub_hex"],
        ),
    )
    p_b.start()
    p_b.join(timeout=40.0)
    assert not p_b.is_alive()

    with open(out_file) as f:
        reconciled = json.load(f)
    assert len(reconciled) == 1
    assert reconciled[0]["status"] == "FAILED"

    # Manifest has 0 versions
    assert len(reg._load_manifest(sim_id)) == 0

    # The uncommitted published artifact MUST be cleaned up by reconciliation
    assert not os.path.exists(cand_artifact), "Abandoned unregistered artifact was not cleaned up!"


def test_crash_after_manifest_persistence_reconciles_to_committed(reg_crash_env):
    """Crash after atomic manifest persistence: reconciles to COMMITTED and model is loadable."""
    sim_id = "sim_reg_crash_03"
    reg = reg_crash_env["registry"]
    reg.bind_simulation_ownership(sim_id, "bank_a", auth_token=reg_crash_env["tokens"]["admin"])

    cand_path, receipt, _ = _make_candidate_artifact(reg_crash_env, sim_id)
    tmp_path = reg_crash_env["tmp_path"]
    barrier = str(tmp_path / "barrier_03.txt")

    p = mp_ctx.Process(
        target=_worker_register_process,
        args=(
            reg_crash_env["reg_dir"],
            reg_crash_env["audit_file"],
            reg_crash_env["audit_secret"],
            reg_crash_env["jwt_secret"],
            reg_crash_env["authority_secret"],
            reg_crash_env["account_file"],
            reg_crash_env["trust_file"],
            reg_crash_env["admin_token"],
            sim_id,
            cand_path,
            receipt.to_dict(),
            reg_crash_env["tokens"]["ml"],
            barrier,
            "AFTER_MANIFEST",
            reg_crash_env["pub_hex"],
        ),
    )
    p.start()

    start_t = time.time()
    while not os.path.exists(barrier) and not os.path.exists(barrier + ".err") and time.time() - start_t < 40.0:
        time.sleep(0.05)
    err_info = ""
    if os.path.exists(barrier + ".err"):
        with open(barrier + ".err") as f:
            err_info = f.read()
    assert os.path.exists(barrier), f"Worker failed to reach AFTER_MANIFEST barrier! Error: {err_info}"

    p.kill()
    p.join(timeout=5.0)

    # Manifest contains version 1 with last_mutation_id
    manifest = reg._load_manifest(sim_id)
    assert len(manifest) == 1
    assert manifest[0]["version"] == 1
    mut_id = manifest[0]["last_mutation_id"]

    # Ledger still has PENDING mutation
    chain = ImmutableAuditChain.get_instance()
    pending = chain.get_pending_mutations()
    assert len(pending) == 1
    assert pending[0].details["mutation_id"] == mut_id

    # Run reconciler
    out_file = str(tmp_path / "recon_out_03.json")
    p_b = mp_ctx.Process(
        target=_worker_reconciler,
        args=(
            reg_crash_env["reg_dir"],
            reg_crash_env["audit_file"],
            reg_crash_env["audit_secret"],
            sim_id,
            out_file,
            reg_crash_env["pub_hex"],
        ),
    )
    p_b.start()
    p_b.join(timeout=40.0)
    assert not p_b.is_alive()

    with open(out_file) as f:
        reconciled = json.load(f)
    assert len(reconciled) == 1
    assert reconciled[0]["status"] == "COMMITTED"

    # Pending list is now empty and terminal event is recorded
    chain_fresh = ImmutableAuditChain(store_path=reg_crash_env["audit_file"], signing_secret=reg_crash_env["audit_secret"])
    assert len(chain_fresh.get_pending_mutations()) == 0
    terminal_ev = chain_fresh.chain[-1]
    assert terminal_ev.event_type == "MODEL_VERSION_CREATED"
    assert terminal_ev.decision == "VERSION_CREATED"

    # Model is verified and loadable
    loaded_model = reg.load_version(sim_id, 1, target_class=SimpleLinearNet)
    assert isinstance(loaded_model, SimpleLinearNet)


def test_reconciliation_never_deletes_legitimate_prior_model_artifact(reg_crash_env):
    """Reconciliation of a failed version registration never deletes valid prior artifacts."""
    sim_id = "sim_reg_crash_04"
    reg = reg_crash_env["registry"]
    reg.bind_simulation_ownership(sim_id, "bank_a", auth_token=reg_crash_env["tokens"]["admin"])

    # Register Version 1 successfully
    cand_path1, receipt1, _ = _make_candidate_artifact(reg_crash_env, sim_id, seed=1)
    res_v1 = reg.save_version(
        simulation_id=sim_id,
        source_model_path=cand_path1,
        metrics={"auc": 0.91},
        receipt=receipt1.to_dict(),
        auth_token=reg_crash_env["tokens"]["ml"],
        actor="dr_alice_ml",
    )
    assert res_v1["version"] == 1
    sim_dir = reg._get_sim_dir(sim_id)
    v1_path = os.path.join(sim_dir, "model_v1.pt")
    assert os.path.exists(v1_path)

    # Now attempt to register Version 2, but worker crashes before manifest commit
    cand_path2, receipt2, _ = _make_candidate_artifact(reg_crash_env, sim_id, seed=2)
    tmp_path = reg_crash_env["tmp_path"]
    barrier = str(tmp_path / "barrier_04.txt")

    p = mp_ctx.Process(
        target=_worker_register_process,
        args=(
            reg_crash_env["reg_dir"],
            reg_crash_env["audit_file"],
            reg_crash_env["audit_secret"],
            reg_crash_env["jwt_secret"],
            reg_crash_env["authority_secret"],
            reg_crash_env["account_file"],
            reg_crash_env["trust_file"],
            reg_crash_env["admin_token"],
            sim_id,
            cand_path2,
            receipt2.to_dict(),
            reg_crash_env["tokens"]["ml"],
            barrier,
            "BEFORE_MANIFEST",
            reg_crash_env["pub_hex"],
        ),
    )
    p.start()

    start_t = time.time()
    while not os.path.exists(barrier) and not os.path.exists(barrier + ".err") and time.time() - start_t < 40.0:
        time.sleep(0.05)
    err_info = ""
    if os.path.exists(barrier + ".err"):
        with open(barrier + ".err") as f:
            err_info = f.read()
    assert os.path.exists(barrier), f"Worker failed to reach BEFORE_MANIFEST barrier! Error: {err_info}"

    p.kill()
    p.join(timeout=5.0)

    # Run reconciler
    out_file = str(tmp_path / "recon_out_04.json")
    p_b = mp_ctx.Process(
        target=_worker_reconciler,
        args=(
            reg_crash_env["reg_dir"],
            reg_crash_env["audit_file"],
            reg_crash_env["audit_secret"],
            sim_id,
            out_file,
            reg_crash_env["pub_hex"],
        ),
    )
    p_b.start()
    p_b.join(timeout=40.0)
    assert not p_b.is_alive()

    # CRITICAL CHECK: Version 1 artifact MUST REMAIN completely intact and loadable!
    assert os.path.exists(v1_path), "Committed v1 artifact was deleted by reconciler!"
    loaded_v1 = reg.load_version(sim_id, 1, target_class=SimpleLinearNet)
    assert isinstance(loaded_v1, SimpleLinearNet)

    # Uncommitted Version 2 artifact MUST be cleaned
    v2_path = os.path.join(sim_dir, "model_v2.pt")
    assert not os.path.exists(v2_path), "Uncommitted v2 artifact was not cleaned!"


def test_mismatched_receipt_digest_fails_closed_without_registration(reg_crash_env):
    """A structurally valid receipt whose signed artifact digest does not match candidate is rejected."""
    sim_id = "sim_reg_crash_05"
    reg = reg_crash_env["registry"]
    reg.bind_simulation_ownership(sim_id, "bank_a", auth_token=reg_crash_env["tokens"]["admin"])

    # Create model artifact A and receipt for model artifact A
    cand_path_a, receipt_a, _ = _make_candidate_artifact(reg_crash_env, sim_id, seed=10)

    # Create different model artifact B
    cand_path_b, _, _ = _make_candidate_artifact(reg_crash_env, sim_id, seed=20)

    # Supply receipt_a while submitting cand_path_b -> MUST FAIL CLOSED
    with pytest.raises(ModelIntegrityError, match="incompatible with candidate artifact bytes|does not match"):
        reg.save_version(
            simulation_id=sim_id,
            source_model_path=cand_path_b,
            metrics={"auc": 0.95},
            receipt=receipt_a.to_dict(),
            auth_token=reg_crash_env["tokens"]["ml"],
            actor="dr_alice_ml",
        )

    # Verify no version was registered in manifest
    manifest = reg._load_manifest(sim_id)
    assert len(manifest) == 0

    # Verify no SUCCESS audit event was emitted
    chain = ImmutableAuditChain.get_instance()
    assert not any(e.event_type == "MODEL_VERSION_CREATED" for e in chain.chain)
