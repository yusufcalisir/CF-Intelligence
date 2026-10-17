"""Unit tests for crash reconciliation and durable mutation tracking consistency.

Validates that:
1. Pre-existing model status is never used as a heuristic for mutation success.
2. Active worker locks prevent premature failure declaration during in-flight mutations.
3. Process terminations before and after persistence are faithfully reconciled.
4. Racing reconcilers produce exactly one terminal record per mutation.
5. Prior failed mutations are not masked by subsequent successful operations.
6. Concurrent audit appends and multi-worker interleavings preserve tamper-evident integrity.
7. All mutation pathways uniformly enforce durable tracking in registry.json.
8. Concurrent mutation and reconciliation avoid lock-order inversion and deadlock.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import threading
import time
import uuid

mp_ctx = multiprocessing.get_context("spawn")
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

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
from app.infrastructure.security.durable_nonce_store import DurableNonceStore
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore


@pytest.fixture
def reconciliation_env(tmp_path, monkeypatch):
    """Authoritative test environment for crash reconciliation and mutation verification."""
    audit_file = str(tmp_path / "security" / "audit_chain.json")
    account_file = str(tmp_path / "security" / "accounts.json")
    trust_file = str(tmp_path / "security" / "trust_store.json")
    consortium_file = str(tmp_path / "security" / "consortium.json")
    reg_dir = str(tmp_path / "models")
    os.makedirs(os.path.dirname(audit_file), exist_ok=True)
    os.makedirs(reg_dir, exist_ok=True)

    audit_secret = "CFI_PROD_AUDIT_LEDGER_SECRET_2026_AUTHORITATIVE"
    jwt_secret = "CFI_PROD_JWT_SIGNING_KEY_2026_TEST_SECURE_KEY"
    authority_secret = "CFI_PROD_ACCOUNT_AUTHORITY_SECRET_2026_TEST"
    payload_secret = "CFI_PAYLOAD_SIGNING_SECRET_2026_TEST"
    admin_token = "CFI_TRUST_ADMIN_TOKEN_2026_TEST"
    consortium_secret = "CFI_CONSORTIUM_STORE_SECRET_2026_TEST"

    monkeypatch.setenv("CFI_AUDIT_LEDGER_PATH", audit_file)
    monkeypatch.setenv("CFI_AUDIT_LEDGER_SECRET", audit_secret)
    monkeypatch.setenv("CFI_TRUST_STORE_PATH", trust_file)
    monkeypatch.setenv("CFI_TRUST_STORE_ADMIN_TOKEN", admin_token)
    monkeypatch.setenv("CFI_MODEL_REGISTRY_DIR", reg_dir)
    monkeypatch.setenv("CFI_PAYLOAD_SIGNING_SECRET", payload_secret)
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", consortium_file)
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_SECRET", consortium_secret)
    monkeypatch.setenv("CFI_JWT_SIGNING_SECRET", jwt_secret)
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", account_file)
    monkeypatch.setenv("CFI_IDENTITY_STORE_PATH", account_file)
    monkeypatch.setenv("CFI_ACCOUNT_AUTHORITY_SECRET", authority_secret)
    monkeypatch.setenv("CFI_IDENTITY_STORE_SECRET", authority_secret)
    monkeypatch.setenv("APP_ENV", "production")

    get_settings.cache_clear()
    DurableNonceStore._instance = None
    DurableConsortiumStore._instance = None
    AuthenticationService._instance = None
    ImmutableAuditChain._instance = None

    auth_svc = AuthenticationService(
        store_path=account_file,
        signing_secret=jwt_secret,
        authority_secret=authority_secret,
        app_env="production",
    )
    AuthenticationService._instance = auth_svc

    u_ml = auth_svc.register_user("dr_alice_ml", "SecurePass1234!", "bank_a", ["ml_engineer"], clearance_level=2)
    u_comp = auth_svc.register_user("officer_carol_comp", "SecurePass1234!", "bank_a", ["compliance"], clearance_level=2)
    u_admin = auth_svc.register_user("admin_dan", "SecurePass1234!", "bank_a", ["admin"], clearance_level=3)

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
    kid = "issuer_recon_01"
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
        "trust_file": trust_file,
        "admin_token": admin_token,
        "reg_dir": reg_dir,
        "registry": registry,
        "tokens": tokens,
        "issuer": issuer,
        "verifier": verifier,
        "payload_secret": payload_secret,
    }

    get_settings.cache_clear()
    DurableNonceStore._instance = None
    DurableConsortiumStore._instance = None
    AuthenticationService._instance = None
    ImmutableAuditChain._instance = None


def _worker_mutation_process(
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
    auth_token: str,
    crash_stage: str,
    barrier_reached_path: str,
):
    """Independent OS worker process executing genuine mutation and pausing at deterministic test barrier."""
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
    orig_save = reg._save_manifest

    if crash_stage == "BEFORE_PERSISTENCE":
        def hooked_save_before(simulation_id: str, manifest: list[dict[str, Any]]) -> None:
            with open(barrier_reached_path, "w") as f:
                f.write("PENDING_WRITTEN")
            while not os.path.exists(barrier_reached_path + ".done"):
                time.sleep(0.05)

        reg._save_manifest = hooked_save_before

    elif crash_stage == "AFTER_PERSISTENCE":
        def hooked_save_after(simulation_id: str, manifest: list[dict[str, Any]]) -> None:
            orig_save(simulation_id, manifest)
            with open(barrier_reached_path, "w") as f:
                f.write("MANIFEST_PERSISTED")
            while not os.path.exists(barrier_reached_path + ".done"):
                time.sleep(0.05)

        reg._save_manifest = hooked_save_after

    try:
        reg.revoke_version(
            simulation_id=sim_id,
            version=1,
            reason="Real process termination verification",
            actor="admin_dan",
            auth_token=auth_token,
        )
    except Exception as e:
        with open(barrier_reached_path + ".err", "w") as f:
            f.write(f"{type(e).__name__}: {e}\n")


def _worker_reconciler_process(
    reg_dir: str,
    audit_file: str,
    audit_secret: str,
    sim_id: str,
    out_file: str,
):
    """Independent OS worker process executing reconciliation."""
    os.environ["CFI_AUDIT_LEDGER_PATH"] = audit_file
    os.environ["CFI_AUDIT_LEDGER_SECRET"] = audit_secret
    os.environ["APP_ENV"] = "production"
    get_settings.cache_clear()

    reg = ModelRegistry(storage_dir=reg_dir)
    reconciled = reg.reconcile_incomplete_mutations(simulation_id=sim_id)
    with open(out_file, "w") as f:
        json.dump(reconciled, f)


def _create_sample_model(env: dict, sim_id: str) -> tuple[ModelRegistry, dict]:
    """Helper to initialize a signed candidate model for bank_a."""
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

    model_bytes = b"sample_model_weights_tensor_data_recon"
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

    sig_ml = generate_signoff_signature(sim_id, 1, model_sha, "ml_engineer", "dr_alice_ml", signing_secret=env["payload_secret"])
    reg.sign_off(sim_id, 1, "ml_engineer", user="dr_alice_ml", signature=sig_ml, auth_token=env["tokens"]["ml"])

    sig_comp = generate_signoff_signature(sim_id, 1, model_sha, "compliance", "officer_carol_comp", signing_secret=env["payload_secret"])
    reg.sign_off(sim_id, 1, "compliance", user="officer_carol_comp", signature=sig_comp, auth_token=env["tokens"]["comp"])

    return reg, entry


# ── Test 1: Ignore Pre-Existing State Heuristic ─────────────────────────────────

def test_reconciliation_ignores_preexisting_model_status_for_unrelated_pending_mutation(reconciliation_env):
    """Reconciliation never infers mutation success from preexisting model status without durable mutation tracking."""
    sim_id = "sim_recon_01"
    reg, entry = _create_sample_model(reconciliation_env, sim_id)
    tokens = reconciliation_env["tokens"]
    chain = ImmutableAuditChain.get_instance()

    # Step 1: Promote model legitimately to challenger
    reg.promote_version(sim_id, 1, target_status="challenger", actor="admin_dan", auth_token=tokens["admin"])
    manifest_after_promo = reg._load_manifest(sim_id)
    assert manifest_after_promo[0]["status"] == "challenger"

    # Step 2: An unrelated/crashed worker logs a PENDING promotion to "challenger", but crashes before writing to disk
    unrelated_mutation_id = f"mut_unrelated_fake_{uuid.uuid4().hex}"
    chain.append_event(
        event_type="MODEL_PROMOTION_PENDING",
        actor="crashed_unrelated_worker",
        target_id=f"{sim_id}:v1",
        details={
            "mutation_id": unrelated_mutation_id,
            "mutation_stage": "PENDING",
            "mutation_committed": False,
            "simulation_id": sim_id,
            "version": 1,
            "target_status": "challenger",
            "action": "promote_version",
            "decision": "PENDING",
        },
        action="promote_version",
        decision="PENDING",
    )

    # Step 3: Run crash reconciliation
    # The model status IS 'challenger', but unrelated_mutation_id is NOT in applied_mutation_ids!
    reconciled = reg.reconcile_incomplete_mutations(simulation_id=sim_id)

    target_res = next(r for r in reconciled if r["mutation_id"] == unrelated_mutation_id)
    assert target_res["status"] == "FAILED", (
        "Reconciler falsely marked mutation as COMMITTED using status heuristic instead of durable mutation evidence!"
    )

    # Step 4: Verify terminal event is strictly FAILED
    chain_fresh = ImmutableAuditChain(store_path=reconciliation_env["audit_file"], signing_secret=reconciliation_env["audit_secret"])
    failed_evs = [
        e for e in chain_fresh.chain
        if e.details.get("mutation_id") == unrelated_mutation_id and e.details.get("mutation_stage") == "FAILED"
    ]
    assert len(failed_evs) == 1
    assert failed_evs[0].event_type == "MODEL_PROMOTION_FAILED"
    assert failed_evs[0].decision == "FAILED"
    assert chain_fresh.verify_chain_integrity().is_valid is True


# ── Test 2: Active Worker Lock Synchronization ─────────────────────────────────

def test_reconciliation_waits_for_active_worker_lock_without_premature_failure(reconciliation_env):
    """Reconciler does not prematurely mark an in-flight mutation as FAILED while worker holds file lock."""
    sim_id = "sim_recon_02"
    reg, entry = _create_sample_model(reconciliation_env, sim_id)
    chain = ImmutableAuditChain.get_instance()

    in_flight_mut_id = f"mut_inflight_{uuid.uuid4().hex}"
    chain.append_event(
        event_type="MODEL_VERSION_REVOCATION_PENDING",
        actor="active_worker",
        target_id=f"{sim_id}:v1",
        details={
            "mutation_id": in_flight_mut_id,
            "mutation_stage": "PENDING",
            "mutation_committed": False,
            "simulation_id": sim_id,
            "version": 1,
            "action": "revoke_version",
            "decision": "PENDING",
        },
        action="revoke_version",
        decision="PENDING",
    )

    from filelock import FileLock

    sim_dir = reg._get_sim_dir(sim_id)
    lock_path = os.path.abspath(os.path.join(sim_dir, "registry.lock"))
    worker_lock = FileLock(lock_path)

    worker_holding = threading.Event()
    worker_release = threading.Event()

    def worker_holds_lock():
        with worker_lock:
            worker_holding.set()
            worker_release.wait(timeout=10)

    t = threading.Thread(target=worker_holds_lock)
    t.start()
    assert worker_holding.wait(timeout=5)

    try:
        # Reconciler runs while worker actively holds lock with a tight timeout
        reconciled = reg.reconcile_incomplete_mutations(simulation_id=sim_id, lock_timeout_seconds=0.2)
        assert len(reconciled) == 1
        assert reconciled[0]["mutation_id"] == in_flight_mut_id
        assert reconciled[0]["status"] == "INDETERMINATE_IN_PROGRESS"

        # Ledger MUST NOT have been updated with a premature failure event!
        chain_now = ImmutableAuditChain.get_instance()
        terminal_evs = [
            e for e in chain_now.chain
            if e.details.get("mutation_id") == in_flight_mut_id and e.details.get("mutation_stage") in ("COMMITTED", "FAILED")
        ]
        assert len(terminal_evs) == 0, "Reconciler prematurely appended terminal event for active worker!"

        # Mutation remains in pending set
        pending = chain_now.get_pending_mutations()
        assert any(p.details.get("mutation_id") == in_flight_mut_id for p in pending)
    finally:
        worker_release.set()
        t.join(timeout=5)

    # After active worker releases lock and persists business state:
    manifest = reg._load_manifest(sim_id)
    manifest[0]["is_revoked"] = True
    manifest[0].setdefault("applied_mutation_ids", []).append(in_flight_mut_id)
    manifest[0]["last_mutation_id"] = in_flight_mut_id
    manifest[0]["revocation_mutation_id"] = in_flight_mut_id
    reg._save_manifest(sim_id, manifest)

    # Reconciler now successfully finalizes as COMMITTED
    reconciled_after = reg.reconcile_incomplete_mutations(simulation_id=sim_id)
    assert len(reconciled_after) == 1
    assert reconciled_after[0]["status"] == "COMMITTED"
    assert len(chain.get_pending_mutations()) == 0


# ── Test 3: Process Termination Prior to Persistence (Real OS Process) ─────────

def test_reconciliation_recovers_process_terminated_before_manifest_persistence(reconciliation_env):
    """Real OS worker terminated after PENDING but before manifest persistence reconciles truthfully to FAILED."""
    sim_id = "sim_recon_03"
    _create_sample_model(reconciliation_env, sim_id)
    tmp_path = reconciliation_env["tmp_path"]
    barrier_path = str(tmp_path / "barrier_before.txt")

    p = mp_ctx.Process(
        target=_worker_mutation_process,
        args=(
            reconciliation_env["reg_dir"],
            reconciliation_env["audit_file"],
            reconciliation_env["audit_secret"],
            reconciliation_env["jwt_secret"],
            reconciliation_env["authority_secret"],
            reconciliation_env["account_file"],
            reconciliation_env["trust_file"],
            reconciliation_env["admin_token"],
            reconciliation_env["payload_secret"],
            sim_id,
            reconciliation_env["tokens"]["admin"],
            "BEFORE_PERSISTENCE",
            barrier_path,
        ),
    )
    p.start()

    # Wait deterministically for Worker A to reach PENDING barrier
    start_t = time.time()
    while not os.path.exists(barrier_path) and not os.path.exists(barrier_path + ".err") and time.time() - start_t < 30.0:
        time.sleep(0.05)
    err_content = ""
    if os.path.exists(barrier_path + ".err"):
        with open(barrier_path + ".err") as f:
            err_content = f.read()
    assert os.path.exists(barrier_path), f"Worker A failed to reach PENDING barrier! Error: {err_content}"

    # Verify PENDING is recorded in audit ledger on disk
    chain = ImmutableAuditChain(store_path=reconciliation_env["audit_file"], signing_secret=reconciliation_env["audit_secret"])
    pending = chain.get_pending_mutations()
    assert len(pending) == 1
    mut_id = pending[0].details["mutation_id"]

    # Terminate Worker A OS process
    p.kill()
    p.join(timeout=5.0)
    assert not p.is_alive()
    assert p.exitcode != 0

    # Verify mutation is NOT persisted in manifest on disk
    reg = reconciliation_env["registry"]
    manifest = reg._load_manifest(sim_id)
    assert mut_id not in manifest[0].get("applied_mutation_ids", [])
    assert manifest[0].get("is_revoked") is not True

    # Start independent Worker B process to reconcile
    out_file = str(tmp_path / "recon_before_out.json")
    p_b = mp_ctx.Process(
        target=_worker_reconciler_process,
        args=(
            reconciliation_env["reg_dir"],
            reconciliation_env["audit_file"],
            reconciliation_env["audit_secret"],
            sim_id,
            out_file,
        ),
    )
    p_b.start()
    p_b.join(timeout=25.0)
    assert not p_b.is_alive()

    with open(out_file) as f:
        reconciled = json.load(f)
    assert len(reconciled) == 1
    assert reconciled[0]["mutation_id"] == mut_id
    assert reconciled[0]["status"] == "FAILED"

    # Verify audit ledger has terminal FAILED event with intact HMAC chain
    chain_fresh = ImmutableAuditChain(store_path=reconciliation_env["audit_file"], signing_secret=reconciliation_env["audit_secret"])
    assert len(chain_fresh.get_pending_mutations()) == 0
    failed_ev = chain_fresh.chain[-1]
    assert failed_ev.event_type == "MODEL_VERSION_REVOCATION_FAILED"
    assert failed_ev.decision == "FAILED"
    assert failed_ev.details.get("reconciled") is True
    assert chain_fresh.verify_chain_integrity().is_valid is True


# ── Test 4: Process Termination After Persistence (Real OS Process) ────────────

def test_reconciliation_recovers_process_terminated_after_manifest_persistence(reconciliation_env):
    """Real OS worker terminated after atomic manifest persistence reconciles truthfully to COMMITTED."""
    sim_id = "sim_recon_04"
    _create_sample_model(reconciliation_env, sim_id)
    tmp_path = reconciliation_env["tmp_path"]
    barrier_path = str(tmp_path / "barrier_after.txt")

    p = mp_ctx.Process(
        target=_worker_mutation_process,
        args=(
            reconciliation_env["reg_dir"],
            reconciliation_env["audit_file"],
            reconciliation_env["audit_secret"],
            reconciliation_env["jwt_secret"],
            reconciliation_env["authority_secret"],
            reconciliation_env["account_file"],
            reconciliation_env["trust_file"],
            reconciliation_env["admin_token"],
            reconciliation_env["payload_secret"],
            sim_id,
            reconciliation_env["tokens"]["admin"],
            "AFTER_PERSISTENCE",
            barrier_path,
        ),
    )
    p.start()

    # Wait deterministically for Worker A to reach MANIFEST_PERSISTED barrier
    start_t = time.time()
    while not os.path.exists(barrier_path) and not os.path.exists(barrier_path + ".err") and time.time() - start_t < 30.0:
        time.sleep(0.05)
    err_content = ""
    if os.path.exists(barrier_path + ".err"):
        with open(barrier_path + ".err") as f:
            err_content = f.read()
    assert os.path.exists(barrier_path), f"Worker A failed to reach MANIFEST_PERSISTED barrier! Error: {err_content}"

    # Terminate Worker A OS process immediately after manifest persistence before audit finalization
    p.kill()
    p.join(timeout=5.0)
    assert not p.is_alive()
    assert p.exitcode != 0

    # Verify mutation IS authoritatively persisted in manifest on disk
    reg = reconciliation_env["registry"]
    manifest = reg._load_manifest(sim_id)
    mut_id = manifest[0].get("last_mutation_id")
    assert mut_id is not None
    assert mut_id in manifest[0].get("applied_mutation_ids", [])
    assert manifest[0].get("is_revoked") is True

    # Verify audit ledger has pending mutation and NOT yet terminal COMMITTED event
    chain = ImmutableAuditChain(store_path=reconciliation_env["audit_file"], signing_secret=reconciliation_env["audit_secret"])
    pending = chain.get_pending_mutations()
    assert len(pending) == 1
    assert pending[0].details["mutation_id"] == mut_id
    assert not any(e.event_type == "MODEL_VERSION_REVOCATION" and e.decision == "REVOKED" for e in chain.chain)

    # Start independent Worker B process to reconcile
    out_file = str(tmp_path / "recon_after_out.json")
    p_b = mp_ctx.Process(
        target=_worker_reconciler_process,
        args=(
            reconciliation_env["reg_dir"],
            reconciliation_env["audit_file"],
            reconciliation_env["audit_secret"],
            sim_id,
            out_file,
        ),
    )
    p_b.start()
    p_b.join(timeout=25.0)
    assert not p_b.is_alive()

    with open(out_file) as f:
        reconciled = json.load(f)
    assert len(reconciled) == 1
    assert reconciled[0]["mutation_id"] == mut_id
    assert reconciled[0]["status"] == "COMMITTED"

    # Verify ledger terminal state
    chain_fresh = ImmutableAuditChain(store_path=reconciliation_env["audit_file"], signing_secret=reconciliation_env["audit_secret"])
    assert len(chain_fresh.get_pending_mutations()) == 0
    committed_ev = chain_fresh.chain[-1]
    assert committed_ev.event_type == "MODEL_VERSION_REVOCATION"
    assert committed_ev.decision == "REVOKED"
    assert committed_ev.details.get("reconciled") is True
    assert chain_fresh.verify_chain_integrity().is_valid is True


# ── Test 5: Multi-Process Racing Reconcilers ───────────────────────────────────

def test_concurrent_multiprocess_reconciliation_produces_single_terminal_outcome(reconciliation_env):
    """Multiple independent OS processes racing reconciliation produce exactly one terminal outcome."""
    sim_id = "sim_recon_05"
    _create_sample_model(reconciliation_env, sim_id)
    tmp_path = reconciliation_env["tmp_path"]

    racing_mut_id = f"mut_race_proc_{uuid.uuid4().hex}"
    chain = ImmutableAuditChain(store_path=reconciliation_env["audit_file"], signing_secret=reconciliation_env["audit_secret"])
    chain.append_event(
        event_type="MODEL_ROLLBACK_PENDING",
        actor="crashed_rollback_process",
        target_id=f"{sim_id}:v1",
        details={
            "mutation_id": racing_mut_id,
            "mutation_stage": "PENDING",
            "mutation_committed": False,
            "simulation_id": sim_id,
            "version": 1,
            "target_version": 1,
            "action": "rollback",
            "decision": "PENDING",
        },
        action="rollback",
        decision="PENDING",
    )

    num_processes = 3
    out_files = [str(tmp_path / f"recon_race_out_{i}.json") for i in range(num_processes)]
    processes = [
        mp_ctx.Process(
            target=_worker_reconciler_process,
            args=(
                reconciliation_env["reg_dir"],
                reconciliation_env["audit_file"],
                reconciliation_env["audit_secret"],
                sim_id,
                out_files[i],
            ),
        )
        for i in range(num_processes)
    ]

    for p in processes:
        p.start()
    for p in processes:
        p.join(timeout=35.0)
        assert not p.is_alive()

    all_results = []
    for fpath in out_files:
        if os.path.exists(fpath):
            with open(fpath) as f:
                all_results.append(json.load(f))

    statuses = []
    for r_list in all_results:
        for r in r_list:
            if r.get("mutation_id") == racing_mut_id:
                statuses.append(r.get("status"))

    assert statuses.count("FAILED") == 1, f"Expected exactly one FAILED outcome, got: {statuses}"
    assert all(s in ("FAILED", "ALREADY_RESOLVED") for s in statuses)

    chain_fresh = ImmutableAuditChain(store_path=reconciliation_env["audit_file"], signing_secret=reconciliation_env["audit_secret"])
    terminal_events = [
        e for e in chain_fresh.chain
        if e.details.get("mutation_id") == racing_mut_id and e.details.get("mutation_stage") in ("COMMITTED", "FAILED")
    ]
    assert len(terminal_events) == 1
    assert chain_fresh.verify_chain_integrity().is_valid is True


# ── Test 6: Subsequent Legitimate Mutation Isolation ───────────────────────────

def test_subsequent_legitimate_mutation_does_not_mask_prior_interrupted_mutation(reconciliation_env):
    """A subsequent successful mutation to the same model never masks an earlier unpersisted mutation."""
    sim_id = "sim_recon_06"
    reg, entry = _create_sample_model(reconciliation_env, sim_id)
    tokens = reconciliation_env["tokens"]
    chain = ImmutableAuditChain.get_instance()

    # Worker 1 intent recorded, but process crashed before manifest save
    interrupted_id = f"mut_interrupted_{uuid.uuid4().hex}"
    chain.append_event(
        event_type="MODEL_PROMOTION_PENDING",
        actor="worker_1_crashed",
        target_id=f"{sim_id}:v1",
        details={
            "mutation_id": interrupted_id,
            "mutation_stage": "PENDING",
            "mutation_committed": False,
            "simulation_id": sim_id,
            "version": 1,
            "target_status": "champion",
            "action": "promote_version",
            "decision": "PENDING",
        },
        action="promote_version",
        decision="PENDING",
    )

    # Worker 2 executes a legitimate promotion to champion
    reg.promote_version(sim_id, 1, target_status="champion", actor="admin_dan", auth_token=tokens["admin"])

    manifest = reg._load_manifest(sim_id)
    assert manifest[0]["status"] == "champion"
    assert interrupted_id not in manifest[0].get("applied_mutation_ids", [])

    # Reconciler runs to reconcile pending mutations
    reconciled = reg.reconcile_incomplete_mutations(simulation_id=sim_id)
    interrupted_res = next(r for r in reconciled if r["mutation_id"] == interrupted_id)

    # Must be marked FAILED despite the model currently being 'champion'
    assert interrupted_res["status"] == "FAILED"

    # Integrity verification
    chain_fresh = ImmutableAuditChain(store_path=reconciliation_env["audit_file"], signing_secret=reconciliation_env["audit_secret"])
    assert chain_fresh.verify_chain_integrity().is_valid is True


# ── Test 7: Concurrent Append Interleaving Safety ──────────────────────────────

def test_concurrent_audit_append_interleaving_preserves_failure_truth(reconciliation_env):
    """Concurrent ledger writes by other actors during failure recovery preserve tamper evidence without false success."""
    sim_id = "sim_recon_07"
    reg, entry = _create_sample_model(reconciliation_env, sim_id)
    tokens = reconciliation_env["tokens"]
    audit_file = reconciliation_env["audit_file"]
    secret = reconciliation_env["audit_secret"]

    chain = ImmutableAuditChain.get_instance()
    orig_append = chain.append_event

    def interleaving_append(*args, **kwargs):
        res = orig_append(*args, **kwargs)
        if kwargs.get("event_type") == "MODEL_SIGNOFF_REVOCATION_PENDING":
            # Interleave unrelated events before Worker A attempts persistence
            orig_append(
                event_type="EXTERNAL_AUDIT_LOG",
                actor="independent_compliance_monitor",
                target_id="monitor_target_42",
                details={"check": "routine_telemetry"},
            )
        return res

    chain.append_event = interleaving_append

    with patch.object(reg, "_save_manifest", side_effect=OSError("Disk write fault")):
        with pytest.raises(OSError, match="Disk write fault"):
            reg.revoke_sign_off(
                simulation_id=sim_id,
                version=1,
                role="compliance",
                auth_token=tokens["admin"],
                reason="Testing interleaving resilience",
            )

    chain_fresh = ImmutableAuditChain(store_path=audit_file, signing_secret=secret)
    events = chain_fresh.chain

    # Verify no committed event for revocation
    assert not any(e.event_type == "MODEL_SIGNOFF_REVOCATION" and e.decision == "REVOKED" for e in events)

    # Verify failure event recorded truthfully
    failed_evs = [e for e in events if e.event_type == "MODEL_SIGNOFF_REVOCATION_FAILED"]
    assert len(failed_evs) == 1
    assert failed_evs[0].decision == "FAILED"

    # Verify interleaved monitor event remains intact
    monitor_evs = [e for e in events if e.event_type == "EXTERNAL_AUDIT_LOG"]
    assert len(monitor_evs) == 1

    assert chain_fresh.verify_chain_integrity().is_valid is True


# ── Test 8: All Mutation Pathways Enforce Durable Evidence ────────────────────

def test_all_mutation_pathways_enforce_consistent_durable_evidence(reconciliation_env):
    """Every mutation pathway (promote, rollback, signoff-revoke, version-revoke) writes durable mutation tracking."""
    sim_id = "sim_recon_08"
    reg, entry = _create_sample_model(reconciliation_env, sim_id)
    tokens = reconciliation_env["tokens"]

    # Pathway 1: promote_version
    reg.promote_version(sim_id, 1, target_status="challenger", actor="admin_dan", auth_token=tokens["admin"])
    m1 = reg._load_manifest(sim_id)[0]
    promo_mut_id = m1.get("last_mutation_id")
    assert promo_mut_id is not None
    assert promo_mut_id in m1.get("applied_mutation_ids", [])
    assert reg.is_mutation_committed_for_version(sim_id, 1, promo_mut_id) is True

    # Pathway 2: revoke_sign_off
    reg.revoke_sign_off(sim_id, 1, "compliance", auth_token=tokens["admin"], reason="Dual signoff recheck")
    m2 = reg._load_manifest(sim_id)[0]
    signoff_mut_id = m2.get("last_mutation_id")
    assert signoff_mut_id is not None
    assert signoff_mut_id in m2.get("applied_mutation_ids", [])
    assert reg.is_mutation_committed_for_version(sim_id, 1, signoff_mut_id) is True
    # Sign-off entry also records revocation mutation id
    comp_signoffs = [s for s in m2["sign_offs"] if s["role"] == "compliance"]
    assert any(s.get("revocation_mutation_id") == signoff_mut_id for s in comp_signoffs)

    # Re-sign for rollback test
    model_sha = m2["sha256"]
    sig_comp = generate_signoff_signature(sim_id, 1, model_sha, "compliance", "officer_carol_comp", signing_secret=reconciliation_env["payload_secret"])
    reg.sign_off(sim_id, 1, "compliance", user="officer_carol_comp", signature=sig_comp, auth_token=tokens["comp"])

    # Pathway 3: rollback
    reg.rollback(sim_id, 1, actor="admin_dan", auth_token=tokens["admin"])
    m3 = reg._load_manifest(sim_id)[0]
    rollback_mut_id = m3.get("last_mutation_id")
    assert rollback_mut_id is not None
    assert rollback_mut_id in m3.get("applied_mutation_ids", [])
    assert reg.is_mutation_committed_for_version(sim_id, 1, rollback_mut_id) is True

    # Pathway 4: revoke_version
    reg.revoke_version(sim_id, 1, reason="Emergency decommission", actor="admin_dan", auth_token=tokens["admin"])
    m4 = reg._load_manifest(sim_id)[0]
    ver_revoke_mut_id = m4.get("last_mutation_id")
    assert ver_revoke_mut_id is not None
    assert ver_revoke_mut_id in m4.get("applied_mutation_ids", [])
    assert m4.get("revocation_mutation_id") == ver_revoke_mut_id
    assert reg.is_mutation_committed_for_version(sim_id, 1, ver_revoke_mut_id) is True

    # Ledger audit verification
    chain = ImmutableAuditChain.get_instance()
    assert chain.verify_chain_integrity().is_valid is True
    assert len(chain.get_pending_mutations()) == 0


# ── Test 9: Lock Order Inversion Elimination ──────────────────────────────────

def test_concurrent_mutation_and_reconciliation_avoids_lock_order_inversion(reconciliation_env):
    """Concurrent mutation operations and reconciliation do not deadlock from lock-order inversion."""
    sim_id = "sim_recon_09"
    reg, entry = _create_sample_model(reconciliation_env, sim_id)
    tokens = reconciliation_env["tokens"]

    iterations = 10
    errors: list[str] = []

    def mutation_worker():
        try:
            for i in range(iterations):
                status = "challenger" if i % 2 == 0 else "champion"
                reg.promote_version(
                    simulation_id=sim_id,
                    version=1,
                    target_status=status,
                    actor="admin_dan",
                    auth_token=tokens["admin"],
                )
        except Exception as exc:
            errors.append(f"mutation_worker: {exc}")

    def reconciler_worker():
        try:
            for _ in range(iterations):
                reg.reconcile_incomplete_mutations(simulation_id=sim_id, lock_timeout_seconds=2.0)
                time.sleep(0.005)
        except Exception as exc:
            errors.append(f"reconciler_worker: {exc}")

    with (
        patch("app.presentation.routers.realtime_inference.reset_model_cache"),
        patch("app.application.services.model_service.ModelService.invalidate_model_cache"),
    ):
        t_mut = threading.Thread(target=mutation_worker, name="MutationWorker")
        t_rec = threading.Thread(target=reconciler_worker, name="ReconcilerWorker")
        t_mut.start()
        t_rec.start()

        t_mut.join(timeout=10.0)
        t_rec.join(timeout=10.0)

        assert not t_mut.is_alive(), "Mutation worker deadlocked!"
        assert not t_rec.is_alive(), "Reconciler worker deadlocked!"
        assert len(errors) == 0, f"Concurrent workers encountered errors: {errors}"

    chain_fresh = ImmutableAuditChain(
        store_path=reconciliation_env["audit_file"],
        signing_secret=reconciliation_env["audit_secret"],
    )
    assert chain_fresh.verify_chain_integrity().is_valid is True

