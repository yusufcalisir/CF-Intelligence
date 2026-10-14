"""Unit tests for Workstream 01-D.3: Final Audit Authorization & Integrity Boundary.

Verifies:
- FINAL-AUDIT-01: Bank B's valid JWT cannot revoke Bank A's sign-off.
- FINAL-AUDIT-02: Same-bank principal lacking the required role or clearance cannot revoke an approval.
- FINAL-AUDIT-03: Authorized governance/compliance principal can revoke an eligible sign-off; original evidence remains intact.
- FINAL-AUDIT-04: Rejected revocation preserves sign-off, champion, generation, and audit accuracy.
- FINAL-AUDIT-05: Modifying an earlier ledger event prevents subsequent authoritative append and privileged mutation.
- FINAL-AUDIT-06: Deleting an intermediate event prevents further authoritative audit operations.
- FINAL-AUDIT-07: A valid cross-worker audit append sequence continues to work without deadlocks or lost records.
- FINAL-AUDIT-08: Failed business persistence cannot be misrepresented as a successfully committed mutation.
- FINAL-AUDIT-09: Correctly scoped V2 internal context successfully performs authorized sign-off revocation; unscoped or mismatched context is rejected.
- FINAL-AUDIT-10: Existing security invariants (receipt verification, dual sign-offs, issuer revocation, live RBAC) remain intact.
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
    create_trusted_internal_context,
    generate_signoff_signature,
)
from app.application.services.training_provenance_service import (
    DurableTrustStore,
    TrainingExecutionEvidence,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
)
from app.config import get_settings
from app.domain.model_serving_errors import (
    ModelAuthorizationError,
    ModelIntegrityError,
)
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.durable_nonce_store import DurableNonceStore
from app.infrastructure.security.immutable_audit_chain import (
    AuditChainTamperError,
    ImmutableAuditChain,
)
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore


@pytest.fixture
def final_audit_env(tmp_path, monkeypatch):
    """Provides isolated environment for testing final audit authorization and integrity boundaries."""
    audit_file = str(tmp_path / "final_audit_ledger.json")
    nonce_file = str(tmp_path / "final_nonces.json")
    trust_file = str(tmp_path / "final_trust.json")
    reg_dir = str(tmp_path / "final_models")
    consortium_file = str(tmp_path / "final_consortium.json")
    account_file = str(tmp_path / "final_accounts.json")

    audit_secret = "audit_ledger_secret_entropy_high_min32bytes_value_123456"
    prod_secret = "prod_internal_secret_key_entropy_high_min32bytes_value_xyz_123456"
    payload_secret = "payload_signing_secret_test_key_minimum_entropy_12345678"
    consortium_secret = "consortium_store_secret_entropy_high_32_bytes_min_xyz_1234"
    jwt_secret = "jwt_signing_secret_high_entropy_min_32_bytes_value_123456789"
    authority_secret = "authoritative_signing_master_secret_key_64_bytes_test_123456"
    admin_token = "admin-secret-token-test-12345"

    monkeypatch.setenv("CFI_AUDIT_LEDGER_PATH", audit_file)
    monkeypatch.setenv("CFI_AUDIT_LEDGER_SECRET", audit_secret)
    monkeypatch.setenv("CFI_INTERNAL_CONTEXT_SECRET", prod_secret)
    monkeypatch.setenv("CFI_INTERNAL_NONCE_STORE_PATH", nonce_file)
    monkeypatch.setenv("CFI_TRAINING_RECEIPT_TRUST_STORE", trust_file)
    monkeypatch.setenv("CFI_TRUST_STORE_PATH", trust_file)
    monkeypatch.setenv("CFI_TRUST_STORE_ADMIN_TOKEN", admin_token)
    monkeypatch.setenv("CFI_MODEL_REGISTRY_DIR", reg_dir)
    monkeypatch.setenv("CFI_PAYLOAD_SIGNING_SECRET", payload_secret)
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", consortium_file)
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_SECRET", consortium_secret)
    monkeypatch.setenv("CFI_JWT_SIGNING_SECRET", jwt_secret)
    monkeypatch.setenv("CFI_OIDC_JWT_SIGNING_SECRET", jwt_secret)
    monkeypatch.setenv("OIDC_JWT_SIGNING_SECRET", jwt_secret)
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

    # Setup standard users
    # Bank A users
    u_ml_a = auth_svc.register_user("dr_alice_ml_a", "SecurePass1234!", "bank_a", ["ml_engineer"], clearance_level=2)
    u_comp_a = auth_svc.register_user("officer_carol_comp_a", "SecurePass1234!", "bank_a", ["compliance"], clearance_level=2)
    u_admin_a = auth_svc.register_user("admin_dan_a", "SecurePass1234!", "bank_a", ["admin"], clearance_level=3)
    u_observer_a = auth_svc.register_user("obs_eve_a", "SecurePass1234!", "bank_a", ["observer"], clearance_level=1)
    u_low_comp_a = auth_svc.register_user("low_frank_comp_a", "SecurePass1234!", "bank_a", ["compliance"], clearance_level=1)
    u_other_ml_a = auth_svc.register_user("other_dave_ml_a", "SecurePass1234!", "bank_a", ["ml_engineer"], clearance_level=2)

    # Bank B users
    u_ml_b = auth_svc.register_user("eng_bob_ml_b", "SecurePass1234!", "bank_b", ["ml_engineer"], clearance_level=2)
    u_comp_b = auth_svc.register_user("officer_grace_comp_b", "SecurePass1234!", "bank_b", ["compliance"], clearance_level=2)
    u_admin_b = auth_svc.register_user("admin_heidi_b", "SecurePass1234!", "bank_b", ["admin"], clearance_level=3)

    tokens = {
        "ml_a": auth_svc.create_token_bundle(u_ml_a).access_token,
        "comp_a": auth_svc.create_token_bundle(u_comp_a).access_token,
        "admin_a": auth_svc.create_token_bundle(u_admin_a).access_token,
        "obs_a": auth_svc.create_token_bundle(u_observer_a).access_token,
        "low_comp_a": auth_svc.create_token_bundle(u_low_comp_a).access_token,
        "other_ml_a": auth_svc.create_token_bundle(u_other_ml_a).access_token,
        "ml_b": auth_svc.create_token_bundle(u_ml_b).access_token,
        "comp_b": auth_svc.create_token_bundle(u_comp_b).access_token,
        "admin_b": auth_svc.create_token_bundle(u_admin_b).access_token,
    }

    # Setup training receipt issuer
    priv = ed25519.Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    kid = "issuer_final_01"
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
        "trust_store": trust_store,
        "admin_token": admin_token,
        "payload_secret": payload_secret,
    }

    get_settings.cache_clear()
    DurableNonceStore._instance = None
    DurableConsortiumStore._instance = None
    AuthenticationService._instance = None
    ImmutableAuditChain._instance = None


def _create_sample_bank_a_model(env: dict, sim_id: str = "sim_final_01") -> tuple[ModelRegistry, dict]:
    """Helper to register and dual-sign a model owned by bank_a."""
    reg: ModelRegistry = env["registry"]
    sim_dir = reg._get_sim_dir(sim_id)
    os.makedirs(sim_dir, exist_ok=True)

    # Establish ownership for bank_a
    with open(os.path.join(sim_dir, "ownership.json"), "w", encoding="utf-8") as f:
        json.dump({
            "owning_bank_id": "bank_a",
            "consortium_id": None,
            "is_unbound": False,
            "bound_at": "2026-10-14T00:00:00Z",
            "bound_by": "dr_alice_ml_a",
        }, f)

    model_bytes = b"authoritative_model_weights_tensor_data_v1"
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

    # Sign off as ML Engineer (dr_alice_ml_a)
    sig_ml = generate_signoff_signature(sim_id, 1, model_sha, "ml_engineer", "dr_alice_ml_a", signing_secret=env["payload_secret"])
    reg.sign_off(sim_id, 1, "ml_engineer", user="dr_alice_ml_a", signature=sig_ml, auth_token=env["tokens"]["ml_a"])

    # Sign off as Compliance Officer (officer_carol_comp_a)
    sig_comp = generate_signoff_signature(sim_id, 1, model_sha, "compliance", "officer_carol_comp_a", signing_secret=env["payload_secret"])
    reg.sign_off(sim_id, 1, "compliance", user="officer_carol_comp_a", signature=sig_comp, auth_token=env["tokens"]["comp_a"])

    # Promote to champion
    reg.promote_version(sim_id, 1, target_status="champion", actor="admin_dan_a", auth_token=env["tokens"]["admin_a"])

    manifest = reg._load_manifest(sim_id)
    assert manifest[0]["status"] == "champion"
    assert manifest[0]["is_active"] is True
    assert len(manifest[0]["sign_offs"]) == 2
    return reg, manifest[0]


# ── FINAL-AUDIT-01: Cross-Bank Sign-Off Revocation Rejected ───────────────────

def test_final_audit_01_cross_bank_jwt_cannot_revoke_signoff(final_audit_env):
    """FINAL-AUDIT-01: Bank B's valid JWT cannot revoke Bank A's sign-off."""
    reg, entry = _create_sample_bank_a_model(final_audit_env, "sim_final_01")
    tokens = final_audit_env["tokens"]

    # Bank B ML Engineer, Compliance Officer, and Admin attempt to revoke Bank A's sign-off
    for b_role, tok in [("ml_engineer", tokens["ml_b"]), ("compliance", tokens["comp_b"]), ("admin", tokens["admin_b"])]:
        with pytest.raises(ModelAuthorizationError, match="Cross-bank authorization rejected|cannot execute 'model sign-off revocation'"):
            reg.revoke_sign_off(
                simulation_id="sim_final_01",
                version=1,
                role="ml_engineer",
                auth_token=tok,
                reason="Malicious cross-tenant revocation attempt",
            )

    # Verify model champion status and sign-offs remain unaffected
    manifest_after = reg._load_manifest("sim_final_01")
    assert manifest_after[0]["status"] == "champion"
    assert manifest_after[0]["is_active"] is True
    assert all(not s.get("is_revoked", False) for s in manifest_after[0]["sign_offs"])


# ── FINAL-AUDIT-02: Same-Bank Lacking Role / Clearance Rejected ───────────────

def test_final_audit_02_insufficient_role_or_clearance_rejected(final_audit_env):
    """FINAL-AUDIT-02: Same-bank principal lacking required role or clearance cannot revoke an approval."""
    reg, entry = _create_sample_bank_a_model(final_audit_env, "sim_final_02")
    tokens = final_audit_env["tokens"]

    # 1. Observer role attempting revocation -> rejected
    with pytest.raises(ModelAuthorizationError, match="observer role is not authorized"):
        reg.revoke_sign_off(
            simulation_id="sim_final_02",
            version=1,
            role="ml_engineer",
            auth_token=tokens["obs_a"],
            reason="Observer unauthorized intervention",
        )

    # 2. Another ML engineer (not the original signatory) attempting supervisory revocation -> rejected
    with pytest.raises(ModelAuthorizationError, match="lacking compliance or lead role authority"):
        reg.revoke_sign_off(
            simulation_id="sim_final_02",
            version=1,
            role="ml_engineer",
            auth_token=tokens["other_ml_a"],
            reason="Peer engineer attempting to revoke colleague's approval",
        )

    # 3. Compliance officer with clearance 1 (insufficient clearance < 2) attempting supervisory revocation -> rejected
    with pytest.raises(ModelAuthorizationError, match="clearance level 1 is insufficient for supervisory sign-off revocation"):
        reg.revoke_sign_off(
            simulation_id="sim_final_02",
            version=1,
            role="ml_engineer",
            auth_token=tokens["low_comp_a"],
            reason="Low clearance officer attempting supervisory revocation",
        )


# ── FINAL-AUDIT-03: Authorized Governance Revocation & Evidence Retention ──────

def test_final_audit_03_authorized_governance_revocation_preserves_evidence(final_audit_env):
    """FINAL-AUDIT-03: Authorized governance/compliance principal can revoke an eligible sign-off; original evidence remains intact."""
    reg, entry = _create_sample_bank_a_model(final_audit_env, "sim_final_03")
    tokens = final_audit_env["tokens"]

    # 1. Compliance officer Carol (clearance 2) revokes dr_alice_ml_a's sign-off
    reason_text = "Post-validation data drift detected during quarterly surveillance"
    result = reg.revoke_sign_off(
        simulation_id="sim_final_03",
        version=1,
        role="ml_engineer",
        auth_token=tokens["comp_a"],
        reason=reason_text,
    )
    assert result["status"] == "inactive"

    # Verify manifest reflects revocation
    manifest = reg._load_manifest("sim_final_03")
    assert manifest[0]["status"] == "inactive"
    assert manifest[0]["is_active"] is False
    assert manifest[0]["generation"] > 1

    # Historical sign-off list still has 2 records!
    sign_offs = manifest[0]["sign_offs"]
    assert len(sign_offs) == 2, "Historical sign-off must NOT be deleted from manifest!"

    revoked_s = next(s for s in sign_offs if s["role"] == "ml_engineer")
    assert revoked_s["is_revoked"] is True
    assert revoked_s["status"] == "revoked"
    assert revoked_s["revoked_by"] == "officer_carol_comp_a"
    assert revoked_s["revocation_reason"] == reason_text
    assert revoked_s["user"] == "dr_alice_ml_a"  # original signer preserved
    assert revoked_s["signature"] != ""  # original cryptographic signature preserved

    # 2. Original signatory self-revocation: officer Carol self-revokes her own compliance sign-off
    reg.revoke_sign_off(
        simulation_id="sim_final_03",
        version=1,
        role="compliance",
        auth_token=tokens["comp_a"],
        reason="Self-revoking compliance sign-off",
    )
    manifest_2 = reg._load_manifest("sim_final_03")
    assert len(manifest_2[0]["sign_offs"]) == 2
    comp_s = next(s for s in manifest_2[0]["sign_offs"] if s["role"] == "compliance")
    assert comp_s["is_revoked"] is True
    assert comp_s["revoked_by"] == "officer_carol_comp_a"


# ── FINAL-AUDIT-04: Rejected Revocation Preserves Champion & Generation ────────

def test_final_audit_04_rejected_revocation_preserves_state_and_generation(final_audit_env):
    """FINAL-AUDIT-04: Rejected revocation preserves sign-off, champion, generation, and audit accuracy."""
    reg, entry = _create_sample_bank_a_model(final_audit_env, "sim_final_04")
    tokens = final_audit_env["tokens"]

    init_manifest = reg._load_manifest("sim_final_04")
    initial_gen = init_manifest[0]["generation"]

    chain = ImmutableAuditChain.get_instance()
    initial_audit_len = len(chain.chain)

    # Attempt rejected cross-bank revocation
    with pytest.raises(ModelAuthorizationError):
        reg.revoke_sign_off(
            simulation_id="sim_final_04",
            version=1,
            role="ml_engineer",
            auth_token=tokens["admin_b"],
            reason="Unauthorized attempt",
        )

    # State must be completely unmodified
    current_manifest = reg._load_manifest("sim_final_04")
    assert current_manifest[0]["status"] == "champion"
    assert current_manifest[0]["is_active"] is True
    assert current_manifest[0]["generation"] == initial_gen
    assert all(not s.get("is_revoked", False) for s in current_manifest[0]["sign_offs"])

    # Audit ledger length must NOT record a successful revocation
    current_chain = ImmutableAuditChain.get_instance()
    assert len(current_chain.chain) == initial_audit_len
    revocation_events = [e for e in current_chain.chain if e.event_type == "MODEL_SIGNOFF_REVOCATION"]
    assert len(revocation_events) == 0


# ── FINAL-AUDIT-05: Modifying Ledger Event Blocks Subsequent Appends ───────────

def test_final_audit_05_modifying_earlier_event_blocks_appends_and_mutations(final_audit_env):
    """FINAL-AUDIT-05: Modifying an earlier ledger event prevents subsequent authoritative append and privileged mutation."""
    reg, entry = _create_sample_bank_a_model(final_audit_env, "sim_final_05")
    audit_file = final_audit_env["audit_file"]
    tokens = final_audit_env["tokens"]

    # Verify chain is valid initially
    chain = ImmutableAuditChain.get_instance()
    report = chain.verify_chain_integrity()
    assert report.is_valid is True

    # Attacker alters an intermediate event on disk without updating HMAC
    with open(audit_file, encoding="utf-8") as f:
        data = json.load(f)

    # Tamper with event details
    target_entry_idx = 1
    data["entries"][target_entry_idx]["details"]["tampered"] = True

    with open(audit_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    # Reset in-memory cache to force reload from disk
    ImmutableAuditChain.reset_instance()

    # Attempt privileged mutation (revoke_sign_off)
    # The audit ledger reload will detect HMAC tampering and fail closed with AuditChainTamperError
    with pytest.raises(AuditChainTamperError, match="Tampering detected|signature verification failed"):
        reg.revoke_sign_off(
            simulation_id="sim_final_05",
            version=1,
            role="ml_engineer",
            auth_token=tokens["admin_a"],
            reason="Authorized revocation on tampered system",
        )

    # Ensure model manifest was NOT mutated because audit fail-closed prevented mutation
    manifest = reg._load_manifest("sim_final_05")
    assert manifest[0]["status"] == "champion"
    assert manifest[0]["is_active"] is True


# ── FINAL-AUDIT-06: Deleting Intermediate Event Blocks Further Operations ─────

def test_final_audit_06_deleting_intermediate_event_blocks_further_operations(final_audit_env):
    """FINAL-AUDIT-06: Deleting an intermediate event prevents further authoritative audit operations."""
    reg, entry = _create_sample_bank_a_model(final_audit_env, "sim_final_06")
    audit_file = final_audit_env["audit_file"]
    tokens = final_audit_env["tokens"]

    with open(audit_file, encoding="utf-8") as f:
        data = json.load(f)

    # Delete intermediate event #1
    assert len(data["entries"]) >= 3
    data["entries"].pop(1)

    with open(audit_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    ImmutableAuditChain.reset_instance()

    # Attempting to append an event or perform a privileged mutation fails closed
    with pytest.raises(AuditChainTamperError, match="Index mismatch|Chain broken"):
        reg.revoke_sign_off(
            simulation_id="sim_final_06",
            version=1,
            role="ml_engineer",
            auth_token=tokens["admin_a"],
            reason="Revocation after deletion attack",
        )


# ── FINAL-AUDIT-07: Concurrent Cross-Worker Appends Work Without Deadlocks ───

def _worker_append_process(audit_file: str, secret: str, worker_id: int, count: int, results_queue: multiprocessing.Queue):
    """Target function for independent OS process appending events concurrently."""
    try:
        from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain

        chain = ImmutableAuditChain(store_path=audit_file, signing_secret=secret)
        appended_ids = []
        for i in range(count):
            entry = chain.append_event(
                event_type="PARALLEL_TEST_EVENT",
                actor=f"worker_{worker_id}",
                target_id=f"target_{worker_id}_{i}",
                details={"worker": worker_id, "seq": i},
            )
            appended_ids.append(entry.event_id)
        results_queue.put({"success": True, "worker_id": worker_id, "appended": appended_ids})
    except Exception as exc:
        results_queue.put({"success": False, "worker_id": worker_id, "error": str(exc)})


def test_final_audit_07_concurrent_cross_worker_appends_consistent(final_audit_env):
    """FINAL-AUDIT-07: A valid cross-worker audit append sequence continues to work without deadlocks or lost records."""
    audit_file = final_audit_env["audit_file"]
    secret = final_audit_env["audit_secret"]

    # Initial append
    chain = ImmutableAuditChain(store_path=audit_file, signing_secret=secret)
    init_len = len(chain.chain)

    num_workers = 3
    events_per_worker = 4
    results_queue = multiprocessing.Queue()

    processes = [
        multiprocessing.Process(
            target=_worker_append_process,
            args=(audit_file, secret, w_id, events_per_worker, results_queue),
        )
        for w_id in range(num_workers)
    ]

    for p in processes:
        p.start()

    results = []
    for _ in range(num_workers):
        res = results_queue.get(timeout=20)
        results.append(res)

    for p in processes:
        p.join(timeout=10)
        assert not p.is_alive(), "Worker process timed out (potential deadlock)!"

    assert all(r["success"] is True for r in results), f"Worker failed: {results}"

    # Verify resulting chain integrity and count
    chain_final = ImmutableAuditChain(store_path=audit_file, signing_secret=secret)
    report = chain_final.verify_chain_integrity()
    assert report.is_valid is True
    expected_total = init_len + (num_workers * events_per_worker)
    assert len(chain_final.chain) == expected_total


# ── FINAL-AUDIT-08: Failed Business Persistence Not Misrepresented ─────────────

def test_final_audit_08_failed_business_persistence_recovers_audit_state(final_audit_env):
    """FINAL-AUDIT-08: Failed business persistence cannot be misrepresented as a successfully committed mutation."""
    reg, entry = _create_sample_bank_a_model(final_audit_env, "sim_final_08")
    tokens = final_audit_env["tokens"]
    chain = ImmutableAuditChain.get_instance()

    # Simulate disk failure / permission error during manifest save
    with patch.object(reg, "_save_manifest", side_effect=OSError("Simulated disk write failure on manifest")):
        with pytest.raises(OSError, match="Simulated disk write failure on manifest"):
            reg.revoke_sign_off(
                simulation_id="sim_final_08",
                version=1,
                role="ml_engineer",
                auth_token=tokens["admin_a"],
                reason="Testing persistence failure rollback",
            )

    # Verify audit chain does NOT have a committed "MODEL_SIGNOFF_REVOCATION" event with decision="REVOKED"!
    # Instead, the premature event was rolled back and a FAILED event was recorded!
    snapshot = chain.get_snapshot()
    revoked_events = [e for e in snapshot if e.event_type == "MODEL_SIGNOFF_REVOCATION" and e.decision == "REVOKED"]
    assert len(revoked_events) == 0, "Audit log falsely recorded REVOKED for an unpersisted mutation!"

    failed_events = [e for e in snapshot if e.event_type == "MODEL_SIGNOFF_REVOCATION_FAILED"]
    assert len(failed_events) == 1
    assert failed_events[0].decision == "FAILED"
    assert failed_events[0].details.get("mutation_committed") is False

    # The chain remains completely valid cryptographically
    report = chain.verify_chain_integrity()
    assert report.is_valid is True


# ── FINAL-AUDIT-09: Scoped V2 Internal Context Authorization & Replay Check ────

def test_final_audit_09_scoped_v2_internal_context_and_replay_rejection(final_audit_env):
    """FINAL-AUDIT-09: Correctly scoped V2 internal context successfully performs authorized sign-off revocation; unscoped or mismatched context is rejected."""
    reg, entry = _create_sample_bank_a_model(final_audit_env, "sim_final_09")
    sim_id = "sim_final_09"

    # 1. Unscoped / mismatched context: wrong simulation ID -> rejected
    ctx_wrong_sim = create_trusted_internal_context(
        service_name="model_evaluation_engine",
        action="revoke_sign_off",
        resource_type="model_version",
        resource_id="wrong_sim:v1",
        model_version=1,
        bank_id="bank_a",
    )
    with pytest.raises(ModelIntegrityError, match="resource_id scope mismatch"):
        reg.revoke_sign_off(
            simulation_id=sim_id,
            version=1,
            role="ml_engineer",
            internal_context=ctx_wrong_sim,
            reason="Automated evaluation failure",
        )

    # 2. Mismatched version -> rejected
    ctx_wrong_ver = create_trusted_internal_context(
        service_name="model_evaluation_engine",
        action="revoke_sign_off",
        resource_type="model_version",
        resource_id=f"{sim_id}:v1",
        model_version=99,
        bank_id="bank_a",
    )
    with pytest.raises(ModelIntegrityError, match="model_version scope mismatch"):
        reg.revoke_sign_off(
            simulation_id=sim_id,
            version=1,
            role="ml_engineer",
            internal_context=ctx_wrong_ver,
            reason="Automated evaluation failure",
        )

    # 3. Mismatched bank_id -> rejected
    ctx_wrong_bank = create_trusted_internal_context(
        service_name="model_evaluation_engine",
        action="revoke_sign_off",
        resource_type="model_version",
        resource_id=f"{sim_id}:v1",
        model_version=1,
        bank_id="bank_b",
    )
    with pytest.raises(ModelIntegrityError, match="bank_id scope mismatch"):
        reg.revoke_sign_off(
            simulation_id=sim_id,
            version=1,
            role="ml_engineer",
            internal_context=ctx_wrong_bank,
            reason="Automated evaluation failure",
        )

    # 4. Correctly scoped V2 internal context -> SUCCEEDS
    valid_ctx = create_trusted_internal_context(
        service_name="model_evaluation_engine",
        action="revoke_sign_off",
        resource_type="model_version",
        resource_id=f"{sim_id}:v1",
        model_version=1,
        bank_id="bank_a",
    )
    res = reg.revoke_sign_off(
        simulation_id=sim_id,
        version=1,
        role="ml_engineer",
        internal_context=valid_ctx,
        reason="Automated regression detected in evaluation engine",
    )
    assert res["status"] == "inactive"
    manifest = reg._load_manifest(sim_id)
    assert manifest[0]["status"] == "inactive"
    rev_s = next(s for s in manifest[0]["sign_offs"] if s["role"] == "ml_engineer")
    assert rev_s["is_revoked"] is True
    assert rev_s["revocation_evidence"]["actor_type"] == "internal_service"
    assert rev_s["revocation_evidence"]["service_name"] == "model_evaluation_engine"

    # 5. Replay attempt using same nonce -> rejected because nonce was consumed
    with pytest.raises(ModelIntegrityError, match=r"(?i)already been consumed|replay"):
        reg.revoke_sign_off(
            simulation_id=sim_id,
            version=1,
            role="compliance",
            internal_context=valid_ctx,
            reason="Replay attack attempt",
        )


# ── FINAL-AUDIT-10: Regressions Remain Intact ─────────────────────────────────

def test_final_audit_10_governance_and_security_invariants_intact(final_audit_env):
    """FINAL-AUDIT-10: Existing security invariants (receipt verification, dual sign-offs, issuer revocation, live RBAC) remain intact."""
    reg, entry = _create_sample_bank_a_model(final_audit_env, "sim_final_10")
    tokens = final_audit_env["tokens"]

    # 1. Active model can be evaluated and loaded
    active_m = next((e for e in reg.list_versions("sim_final_10") if e.get("is_active")), None)
    assert active_m is not None
    assert active_m["version"] == 1

    # 2. Key revocation immediately blocks further promotion
    final_audit_env["trust_store"].revoke_key(
        key_id="issuer_final_01",
        reason="Compromised issuer key test",
        admin_token=final_audit_env["admin_token"],
    )
    assert not final_audit_env["verifier"].is_key_trusted("issuer_final_01")

    # Demote model first
    reg.revoke_sign_off(
        simulation_id="sim_final_10",
        version=1,
        role="compliance",
        auth_token=tokens["admin_a"],
        reason="Demoting for re-promotion test",
    )

    # Re-promotion attempt with revoked receipt key fails closed
    sig_comp_2 = generate_signoff_signature("sim_final_10", 1, entry["sha256"], "compliance", "officer_carol_comp_a", signing_secret=final_audit_env["payload_secret"])
    reg.sign_off("sim_final_10", 1, "compliance", user="officer_carol_comp_a", signature=sig_comp_2, auth_token=tokens["comp_a"])

    with pytest.raises(ModelIntegrityError, match="revoked"):
        reg.promote_version("sim_final_10", 1, target_status="champion", actor="admin_dan_a", auth_token=tokens["admin_a"])
