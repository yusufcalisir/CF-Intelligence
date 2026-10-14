"""Unit tests for crash reconciliation and durable mutation tracking consistency.

Validates that:
1. Pre-existing model status is never used as a heuristic for mutation success.
2. Active worker locks prevent premature failure declaration during in-flight mutations.
3. Process terminations before and after persistence are faithfully reconciled.
4. Racing reconcilers produce exactly one terminal record per mutation.
5. Prior failed mutations are not masked by subsequent successful operations.
6. Concurrent audit appends and multi-worker interleavings preserve tamper-evident integrity.
7. All mutation pathways uniformly enforce durable tracking in registry.json.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import threading
import uuid
from datetime import UTC, datetime
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


# ── Test 3: Process Termination Prior to Persistence ───────────────────────────

def test_reconciliation_marks_process_terminated_before_persistence_as_failed(reconciliation_env):
    """Mutations interrupted before business persistence completes are faithfully resolved as FAILED."""
    sim_id = "sim_recon_03"
    reg, entry = _create_sample_model(reconciliation_env, sim_id)
    chain = ImmutableAuditChain.get_instance()

    crashed_mut_id = f"mut_pre_persist_{uuid.uuid4().hex}"
    chain.append_event(
        event_type="MODEL_SIGNOFF_REVOCATION_PENDING",
        actor="worker_crashed_early",
        target_id=f"{sim_id}:v1",
        details={
            "mutation_id": crashed_mut_id,
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

    reconciled = reg.reconcile_incomplete_mutations(simulation_id=sim_id)
    assert len(reconciled) == 1
    assert reconciled[0]["mutation_id"] == crashed_mut_id
    assert reconciled[0]["status"] == "FAILED"

    chain_fresh = ImmutableAuditChain(store_path=reconciliation_env["audit_file"], signing_secret=reconciliation_env["audit_secret"])
    failed_ev = chain_fresh.chain[-1]
    assert failed_ev.event_type == "MODEL_SIGNOFF_REVOCATION_FAILED"
    assert failed_ev.decision == "FAILED"
    assert failed_ev.details.get("reconciled") is True
    assert chain_fresh.verify_chain_integrity().is_valid is True


# ── Test 4: Process Termination After Persistence ──────────────────────────────

def test_reconciliation_finalizes_committed_when_persisted_before_termination(reconciliation_env):
    """Mutations persisted to disk prior to worker termination are accurately finalized as COMMITTED."""
    sim_id = "sim_recon_04"
    reg, entry = _create_sample_model(reconciliation_env, sim_id)
    chain = ImmutableAuditChain.get_instance()

    crashed_post_id = f"mut_post_persist_{uuid.uuid4().hex}"

    # Worker wrote intent
    chain.append_event(
        event_type="MODEL_PROMOTION_PENDING",
        actor="worker_crashed_late",
        target_id=f"{sim_id}:v1",
        details={
            "mutation_id": crashed_post_id,
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

    # Worker persisted business state with durable mutation evidence before crash
    manifest = reg._load_manifest(sim_id)
    manifest[0]["status"] = "challenger"
    manifest[0].setdefault("applied_mutation_ids", []).append(crashed_post_id)
    manifest[0]["last_mutation_id"] = crashed_post_id
    reg._save_manifest(sim_id, manifest)

    # Reconciler runs
    reconciled = reg.reconcile_incomplete_mutations(simulation_id=sim_id)
    assert len(reconciled) == 1
    assert reconciled[0]["mutation_id"] == crashed_post_id
    assert reconciled[0]["status"] == "COMMITTED"

    chain_fresh = ImmutableAuditChain(store_path=reconciliation_env["audit_file"], signing_secret=reconciliation_env["audit_secret"])
    committed_ev = chain_fresh.chain[-1]
    assert committed_ev.event_type == "MODEL_PROMOTION"
    assert committed_ev.decision == "PROMOTED"
    assert committed_ev.details.get("reconciled") is True
    assert chain_fresh.verify_chain_integrity().is_valid is True


# ── Test 5: Racing Reconcilers Produce Exactly One Outcome ─────────────────────

def test_racing_reconcilers_produce_exactly_one_terminal_outcome(reconciliation_env):
    """Multiple concurrent reconcilers execute atomically without duplicate terminal audit records."""
    sim_id = "sim_recon_05"
    reg, entry = _create_sample_model(reconciliation_env, sim_id)
    chain = ImmutableAuditChain.get_instance()

    racing_mut_id = f"mut_race_{uuid.uuid4().hex}"
    chain.append_event(
        event_type="MODEL_ROLLBACK_PENDING",
        actor="crashed_rollback_worker",
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

    num_threads = 4

    def run_reconciliation():
        return reg.reconcile_incomplete_mutations(simulation_id=sim_id)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(run_reconciliation) for _ in range(num_threads)]
        all_results = [f.result() for f in concurrent.futures.as_completed(futures)]

    # Exactly one reconciler must report FAILED; others must report ALREADY_RESOLVED or empty
    statuses = []
    for res_list in all_results:
        for r in res_list:
            if r["mutation_id"] == racing_mut_id:
                statuses.append(r["status"])

    assert statuses.count("FAILED") == 1, f"Expected exactly one FAILED outcome, got: {statuses}"
    assert all(s in ("FAILED", "ALREADY_RESOLVED") for s in statuses)

    # Exactly one terminal event in the ledger
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
