"""Unit tests for Workstream 01-D.3: Transactional Audit Consistency & Concurrency.

Tests:
  - TX-AUDIT-01: Reproduce cross-worker interleaving defect (verified fixed by Intent/Commit protocol).
  - TX-AUDIT-02: Worker A begins mutation, Worker B appends, Worker A business fails -> no false success.
  - TX-AUDIT-03: Genuine successful mutation records attributable intent and committed evidence.
  - TX-AUDIT-04: Business persistence failure retains truthful failed-mutation evidence.
  - TX-AUDIT-05: Simulated process termination at each stage -> detectable & recoverable.
  - TX-AUDIT-06: Concurrent cross-worker appends preserve ordering, HMAC integrity, and all legitimate events.
  - TX-AUDIT-07: Verify compatibility with governance and historical audit retention invariants.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
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
def tx_audit_env(tmp_path, monkeypatch):
    """Sets up an authoritative isolated environment for transactional audit tests."""
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
    monkeypatch.setenv("CFI_ACCOUNT_AUTHORITY_SECRET", authority_secret)
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

    # Setup training receipt issuer
    priv = ed25519.Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    kid = "issuer_tx_01"
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
        "reg_dir": reg_dir,
        "registry": registry,
        "auth_svc": auth_svc,
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


def _create_sample_model(env: dict, sim_id: str) -> tuple[ModelRegistry, dict]:
    """Helper to register and dual-sign a model owned by bank_a."""
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

    model_bytes = b"authoritative_model_weights_tensor_data_tx"
    model_sha = hashlib.sha256(model_bytes).hexdigest()
    model_path = os.path.join(sim_dir, "global_model_v1.pt")
    with open(model_path, "wb") as f:
        f.write(model_bytes)

    from datetime import UTC, datetime

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
    }
    reg._save_manifest(sim_id, [entry])

    sig_ml = generate_signoff_signature(sim_id, 1, model_sha, "ml_engineer", "dr_alice_ml", signing_secret=env["payload_secret"])
    reg.sign_off(sim_id, 1, "ml_engineer", user="dr_alice_ml", signature=sig_ml, auth_token=env["tokens"]["ml"])

    sig_comp = generate_signoff_signature(sim_id, 1, model_sha, "compliance", "officer_carol_comp", signing_secret=env["payload_secret"])
    reg.sign_off(sim_id, 1, "compliance", user="officer_carol_comp", signature=sig_comp, auth_token=env["tokens"]["comp"])

    reg.promote_version(sim_id, 1, target_status="champion", actor="admin_dan", auth_token=env["tokens"]["admin"])
    return reg, entry


# ── TX-AUDIT-01: Reproduce & Prevent Cross-Worker False-Success Scenario ───────

def test_tx_audit_01_prevent_cross_worker_false_success_interleaving(tx_audit_env):
    """TX-AUDIT-01: Worker B appends between Worker A intent and manifest failure -> no false success."""
    reg, entry = _create_sample_model(tx_audit_env, "sim_tx_01")
    tokens = tx_audit_env["tokens"]
    audit_file = tx_audit_env["audit_file"]
    secret = tx_audit_env["audit_secret"]

    chain = ImmutableAuditChain.get_instance()
    orig_append = chain.append_event

    def interleaving_append(*args, **kwargs):
        res = orig_append(*args, **kwargs)
        # When Worker A emits PENDING intent, Worker B appends an unrelated event
        if kwargs.get("event_type") == "MODEL_SIGNOFF_REVOCATION_PENDING":
            orig_append(
                event_type="UNRELATED_WORKER_B_EVENT",
                actor="worker_b_auditor",
                target_id="loan_audit_42",
                details={"action": "audit_metric", "worker": "B"},
            )
        return res

    chain.append_event = interleaving_append

    # Simulate manifest save failure
    with patch.object(reg, "_save_manifest", side_effect=OSError("Simulated write failure on manifest")):
        with pytest.raises(IOError, match="Simulated write failure on manifest"):
            reg.revoke_sign_off(
                simulation_id="sim_tx_01",
                version=1,
                role="compliance",
                auth_token=tokens["admin"],
                reason="Testing cross-worker failure",
            )

    # Reload fresh audit chain from disk
    chain_fresh = ImmutableAuditChain(store_path=audit_file, signing_secret=secret)
    events = chain_fresh.chain

    # 1. NO falsely committed REVOKED event must exist
    revoked_events = [e for e in events if e.event_type == "MODEL_SIGNOFF_REVOCATION" and e.decision == "REVOKED"]
    assert len(revoked_events) == 0, "Audit chain falsely recorded REVOKED for an unpersisted mutation!"

    # 2. Worker B's event is completely preserved
    b_events = [e for e in events if e.event_type == "UNRELATED_WORKER_B_EVENT"]
    assert len(b_events) == 1
    assert b_events[0].actor == "worker_b_auditor"

    # 3. Failed event is recorded with accurate failure details
    failed_events = [e for e in events if e.event_type == "MODEL_SIGNOFF_REVOCATION_FAILED"]
    assert len(failed_events) == 1
    assert failed_events[0].decision == "FAILED"
    assert failed_events[0].details.get("mutation_committed") is False

    # 4. Chain integrity remains 100% valid
    report = chain_fresh.verify_chain_integrity()
    assert report.is_valid is True


# ── TX-AUDIT-02: Multi-Process Independent OS Worker Interleaving ──────────────

def _independent_worker_b_appender(audit_file: str, secret: str, count: int, q_res: multiprocessing.Queue):
    """Worker B process continuously appending to shared ledger under file locks."""
    try:
        from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain

        chain = ImmutableAuditChain(store_path=audit_file, signing_secret=secret)
        appended = []
        for i in range(count):
            e = chain.append_event(
                event_type="CROSS_PROCESS_INTERLEAVED_EVENT",
                actor="worker_process_b",
                target_id=f"target_b_{i}",
                details={"seq": i},
            )
            appended.append(e.event_id)
        q_res.put({"success": True, "appended": appended})
    except Exception as exc:
        q_res.put({"success": False, "error": str(exc)})


def test_tx_audit_02_independent_os_process_interleaving_consistency(tx_audit_env):
    """TX-AUDIT-02: True independent OS process interleaving preserves ledger integrity and prevents false success."""
    reg, entry = _create_sample_model(tx_audit_env, "sim_tx_02")
    tokens = tx_audit_env["tokens"]
    audit_file = tx_audit_env["audit_file"]
    secret = tx_audit_env["audit_secret"]

    q_res = multiprocessing.Queue()
    worker_b = multiprocessing.Process(
        target=_independent_worker_b_appender,
        args=(audit_file, secret, 5, q_res),
    )
    worker_b.start()

    # Worker A attempts revocation with manifest failure while Worker B is actively appending
    with patch.object(reg, "_save_manifest", side_effect=OSError("Disk full during manifest write")):
        with pytest.raises(OSError, match="Disk full during manifest write"):
            reg.revoke_sign_off(
                simulation_id="sim_tx_02",
                version=1,
                role="compliance",
                auth_token=tokens["admin"],
                reason="Cross-process concurrency stress test",
            )

    worker_res = q_res.get(timeout=20)
    worker_b.join(timeout=10)
    assert not worker_b.is_alive(), "Worker B timed out!"
    assert worker_res["success"] is True, f"Worker B encountered error: {worker_res.get('error')}"

    # Verify audit chain from disk
    chain_check = ImmutableAuditChain(store_path=audit_file, signing_secret=secret)
    events = chain_check.chain

    # Assert no committed false success
    assert not any(e.event_type == "MODEL_SIGNOFF_REVOCATION" and e.decision == "REVOKED" for e in events)

    # Assert failure recorded
    failed_evs = [e for e in events if e.event_type == "MODEL_SIGNOFF_REVOCATION_FAILED"]
    assert len(failed_evs) == 1

    # Assert all 5 events from Worker B are present
    b_evs = [e for e in events if e.event_type == "CROSS_PROCESS_INTERLEAVED_EVENT"]
    assert len(b_evs) == 5

    # Cryptographic HMAC verification must pass
    report = chain_check.verify_chain_integrity()
    assert report.is_valid is True


# ── TX-AUDIT-03: Genuine Successful Mutation Records Attributable Evidence ─────

def test_tx_audit_03_genuine_successful_mutation_records_committed_evidence(tx_audit_env):
    """TX-AUDIT-03: Genuine successful mutation emits both PENDING intent and COMMITTED records with matching mutation_id."""
    reg, entry = _create_sample_model(tx_audit_env, "sim_tx_03")
    tokens = tx_audit_env["tokens"]
    chain = ImmutableAuditChain.get_instance()

    res = reg.revoke_sign_off(
        simulation_id="sim_tx_03",
        version=1,
        role="compliance",
        auth_token=tokens["admin"],
        reason="Model performance degradation detected in production monitoring",
    )
    assert res["status"] == "inactive"

    # Verify audit chain records
    events = chain.chain
    pending_evs = [e for e in events if e.event_type == "MODEL_SIGNOFF_REVOCATION_PENDING"]
    committed_evs = [e for e in events if e.event_type == "MODEL_SIGNOFF_REVOCATION" and e.decision == "REVOKED"]

    assert len(pending_evs) >= 1
    assert len(committed_evs) >= 1

    latest_pending = pending_evs[-1]
    latest_committed = committed_evs[-1]

    # Verify linked mutation identifier
    assert latest_pending.details.get("mutation_id") == latest_committed.details.get("mutation_id")
    assert latest_committed.details.get("mutation_committed") is True
    assert latest_committed.details.get("intent_event_id") == latest_pending.event_id
    assert "admin_dan" in latest_committed.actor

    # Verify manifest state committed
    manifest = reg._load_manifest("sim_tx_03")
    assert manifest[0]["status"] == "inactive"
    assert manifest[0]["is_active"] is False


# ── TX-AUDIT-04: Business Persistence Failure Retains Truthful Failure Evidence ─

def test_tx_audit_04_business_persistence_failure_retains_truthful_failure(tx_audit_env):
    """TX-AUDIT-04: Model promotion failure produces accurate FAILED audit evidence without false promotion."""
    reg, entry = _create_sample_model(tx_audit_env, "sim_tx_04")
    tokens = tx_audit_env["tokens"]
    chain = ImmutableAuditChain.get_instance()

    # Add a v2 candidate
    sim_id = "sim_tx_04"
    manifest = reg._load_manifest(sim_id)
    v2_entry = dict(manifest[0])
    v2_entry["version"] = 2
    v2_entry["status"] = "candidate"
    v2_entry["is_active"] = False
    manifest.append(v2_entry)
    reg._save_manifest(sim_id, manifest)

    # Attempt promotion with manifest write failure
    with patch.object(reg, "_save_manifest", side_effect=PermissionError("Permission denied writing manifest.json")):
        with pytest.raises(PermissionError, match="Permission denied"):
            reg.promote_version(
                simulation_id=sim_id,
                version=2,
                target_status="challenger",
                actor="admin_dan",
                auth_token=tokens["admin"],
            )

    # Verify audit chain records
    events = chain.chain
    prom_pending = [e for e in events if e.event_type == "MODEL_PROMOTION_PENDING" and e.details.get("version") == 2]
    prom_failed = [e for e in events if e.event_type == "MODEL_PROMOTION_FAILED" and e.details.get("version") == 2]
    prom_committed = [e for e in events if e.event_type == "MODEL_PROMOTION" and e.decision == "PROMOTED" and e.details.get("version") == 2]

    assert len(prom_pending) == 1
    assert len(prom_failed) == 1
    assert len(prom_committed) == 0, "Audit log falsely recorded PROMOTED for an unpersisted promotion!"

    assert prom_failed[0].details.get("mutation_committed") is False
    assert prom_failed[0].details.get("mutation_id") == prom_pending[0].details.get("mutation_id")


# ── TX-AUDIT-05: Process Termination Simulation & Crash Recovery Reconciliation ─

def test_tx_audit_05_process_termination_stages_detectable_and_recoverable(tx_audit_env):
    """TX-AUDIT-05: Interrupted mutations at each stage are explicitly detectable and recoverable without falsification."""
    reg, entry = _create_sample_model(tx_audit_env, "sim_tx_05")
    chain = ImmutableAuditChain.get_instance()

    sim_id = "sim_tx_05"

    # Stage A: Simulate process crash right after PENDING intent is appended (before manifest save)
    # Append simulated abandoned intent directly
    mut_a = "mut_crash_stage_a_001"
    chain.append_event(
        event_type="MODEL_SIGNOFF_REVOCATION_PENDING",
        actor="worker_crashed_before_save",
        target_id=f"{sim_id}:v1",
        details={
            "mutation_id": mut_a,
            "mutation_stage": "PENDING",
            "mutation_committed": False,
            "simulation_id": sim_id,
            "version": 1,
            "role": "compliance",
            "action": "revoke_sign_off",
            "decision": "PENDING",
        },
        action="revoke_sign_off",
        decision="PENDING",
    )

    # Check that get_pending_mutations detects this incomplete mutation
    pending_list = chain.get_pending_mutations()
    assert any(p.details.get("mutation_id") == mut_a for p in pending_list)

    # Stage B: Simulate process crash after manifest save succeeds, but before final committed audit event
    # First apply change to manifest on disk
    manifest = reg._load_manifest(sim_id)
    manifest[0]["status"] = "challenger"  # model promoted to challenger
    reg._save_manifest(sim_id, manifest)

    mut_b = "mut_crash_stage_b_002"
    chain.append_event(
        event_type="MODEL_PROMOTION_PENDING",
        actor="worker_crashed_after_save",
        target_id=f"{sim_id}:v1",
        details={
            "mutation_id": mut_b,
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

    # Now run reconciliation
    reconciled = reg.reconcile_incomplete_mutations(simulation_id=sim_id)
    assert len(reconciled) == 2

    res_a = next(r for r in reconciled if r["mutation_id"] == mut_a)
    res_b = next(r for r in reconciled if r["mutation_id"] == mut_b)

    # Mutation A was NOT saved on disk -> reconciled as FAILED
    assert res_a["status"] == "FAILED"

    # Mutation B WAS saved on disk -> reconciled as COMMITTED
    assert res_b["status"] == "COMMITTED"

    # Pending list is now empty!
    assert len(chain.get_pending_mutations()) == 0

    # Chain integrity remains strictly valid
    assert chain.verify_chain_integrity().is_valid is True


# ── TX-AUDIT-06: High-Concurrency Cross-Worker Append Safety ───────────────────

def _concurrent_appender_task(audit_file: str, secret: str, worker_id: int, count: int, q_out: multiprocessing.Queue):
    """Worker function for concurrent audit logging."""
    try:
        from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain

        chain = ImmutableAuditChain(store_path=audit_file, signing_secret=secret)
        for i in range(count):
            chain.append_event(
                event_type="TX_CONCURRENT_EVENT",
                actor=f"worker_{worker_id}",
                target_id=f"target_{worker_id}_{i}",
                details={"w": worker_id, "i": i},
            )
        q_out.put({"success": True, "worker": worker_id})
    except Exception as exc:
        q_out.put({"success": False, "worker": worker_id, "error": str(exc)})


def test_tx_audit_06_concurrent_independent_workers_preserve_integrity(tx_audit_env):
    """TX-AUDIT-06: 4 independent OS worker processes concurrently append events without deadlocks or corruption."""
    audit_file = tx_audit_env["audit_file"]
    secret = tx_audit_env["audit_secret"]

    chain = ImmutableAuditChain(store_path=audit_file, signing_secret=secret)
    initial_count = len(chain.chain)

    num_workers = 4
    events_per_worker = 5
    q_out = multiprocessing.Queue()

    workers = [
        multiprocessing.Process(
            target=_concurrent_appender_task,
            args=(audit_file, secret, w_id, events_per_worker, q_out),
        )
        for w_id in range(num_workers)
    ]

    for w in workers:
        w.start()

    results = [q_out.get(timeout=25) for _ in range(num_workers)]
    for w in workers:
        w.join(timeout=10)
        assert not w.is_alive(), "Worker process timed out!"

    assert all(r["success"] is True for r in results), f"Worker failed: {results}"

    # Verify final ledger count and HMAC integrity
    chain_final = ImmutableAuditChain(store_path=audit_file, signing_secret=secret)
    report = chain_final.verify_chain_integrity()
    assert report.is_valid is True
    assert len(chain_final.chain) == initial_count + (num_workers * events_per_worker)


# ── TX-AUDIT-07: Invariant Retention Regressions ───────────────────────────────

def test_tx_audit_07_governance_and_audit_retention_invariants_pass(tx_audit_env):
    """TX-AUDIT-07: Dual sign-offs, institutional authority, and historical evidence retention remain intact."""
    reg, entry = _create_sample_model(tx_audit_env, "sim_tx_07")
    tokens = tx_audit_env["tokens"]

    # 1. Active model is champion
    manifest = reg._load_manifest("sim_tx_07")
    assert manifest[0]["status"] == "champion"
    assert len(manifest[0]["sign_offs"]) == 2

    # 2. Revoke one sign-off with valid governance principal
    reg.revoke_sign_off(
        simulation_id="sim_tx_07",
        version=1,
        role="ml_engineer",
        auth_token=tokens["admin"],
        reason="Periodic sign-off renewal",
    )

    # 3. Model is demoted to inactive
    manifest_after = reg._load_manifest("sim_tx_07")
    assert manifest_after[0]["status"] == "inactive"
    assert manifest_after[0]["is_active"] is False

    # 4. Historical sign-offs are retained
    assert len(manifest_after[0]["sign_offs"]) == 2
    rev_s = next(s for s in manifest_after[0]["sign_offs"] if s["role"] == "ml_engineer")
    assert rev_s["is_revoked"] is True
    assert rev_s["status"] == "revoked"
