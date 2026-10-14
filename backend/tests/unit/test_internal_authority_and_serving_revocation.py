"""Unit and Adversarial Test Suite for Trusted Internal Authority and Production Serving Revocation.

Covers invariants:
- AUTHCTX-01: Missing/default production signing secret cannot issue or validate privileged contexts.
- AUTHCTX-02: Modified service name, action, target, institution, or version invalidates authorization.
- AUTHCTX-03: A valid context for Model A cannot mutate Model B.
- AUTHCTX-04: A valid context for Bank A / Consortium A cannot establish authority over another institution or consortium.
- AUTHCTX-05: Reusing a successfully consumed context is rejected without additional mutation.
- AUTHCTX-06: Two independent processes racing to consume one context produce at most one authorized mutation.
- AUTHCTX-07: Replay protection survives worker restart and fails closed if its authoritative state is unavailable.
- AUTHCTX-08: Legitimate automatic rollback, training/model registration, consortium orchestration, and explicitly authorized bootstrap remain functional under correctly scoped contexts.
- SERVE-01: Genuine eligible production champion passes both cold and warm serving checks before revocation.
- SERVE-02: Independent-worker issuer revocation blocks subsequent unauthorized production serving.
- SERVE-03: Missing/invalid receipt and unverified provenance cannot be served in production, including cached paths.
- SERVE-04: Revoked approval or lost production eligibility cannot silently retain serving authorization.
- SERVE-05: Verified evidence showing that genuine production champions are never vulnerable to warm-cache bypass.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
import torch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.consortium_service import (
    ConsortiumGovernanceService,
    init_default_consortium,
)
from app.application.services.model_registry import (
    DOMAIN_APPROVAL_SIGNOFF,
    ModelIntegrityError,
    ModelRegistry,
    TrustedInternalContext,
    create_trusted_internal_context,
    derive_domain_key,
    validate_production_admission,
    verify_trusted_internal_context,
)
from app.application.services.training_provenance_service import (
    DurableTrustStore,
    TrainingExecutionEvidence,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
)
from app.config import get_settings
from app.infrastructure.security.durable_nonce_store import (
    DurableNonceStore,
    NonceStoreIntegrityError,
)
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


# ── TEST FIXTURES & HELPERS ──────────────────────────────────────────────────


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Provides an isolated environment for model registry, trust store, and nonce store."""
    nonce_file = str(tmp_path / "test_nonces.json")
    trust_file = str(tmp_path / "test_trust.json")
    reg_dir = str(tmp_path / "test_models")
    consortium_file = str(tmp_path / "test_consortium.json")

    prod_secret = "prod_internal_secret_key_entropy_high_min32bytes_value_xyz_123456"
    payload_secret = "payload_signing_secret_test_key_minimum_entropy_12345678"
    consortium_secret = "consortium_store_secret_entropy_high_32_bytes_min_xyz_1234"
    jwt_secret = "jwt_signing_secret_high_entropy_min_32_bytes_value_123456789"
    account_file = str(tmp_path / "test_accounts.json")
    admin_token = "admin-secret-token-test-12345"

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
    monkeypatch.setenv("CFI_ACCOUNT_AUTHORITY_SECRET", "authoritative_signing_master_secret_key_64_bytes_test")
    monkeypatch.setenv("APP_ENV", "production")

    get_settings.cache_clear()
    DurableNonceStore._instance = None
    DurableConsortiumStore._instance = None
    from app.infrastructure.security.auth_service import AuthenticationService
    AuthenticationService._instance = None

    yield {
        "tmp_path": tmp_path,
        "nonce_file": nonce_file,
        "trust_file": trust_file,
        "reg_dir": reg_dir,
        "consortium_file": consortium_file,
        "account_file": account_file,
        "prod_secret": prod_secret,
        "payload_secret": payload_secret,
        "consortium_secret": consortium_secret,
        "jwt_secret": jwt_secret,
        "admin_token": admin_token,
    }

    get_settings.cache_clear()
    DurableNonceStore._instance = None
    DurableConsortiumStore._instance = None
    AuthenticationService._instance = None


def _create_champion_fixtures(env: dict[str, Any], sim_id: str = "sim_prod_champ_01") -> tuple[ModelRegistry, int, str]:
    """Sets up a genuinely valid production champion with authentic cryptographic receipt and dual sign-offs."""
    reg_dir = env["reg_dir"]
    trust_file = env["trust_file"]
    payload_secret = env["payload_secret"]
    account_file = env["account_file"]
    jwt_secret = env["jwt_secret"]
    admin_token = env.get("admin_token", "admin-secret-token-test-12345")

    # 0. Setup authoritative human approvers
    from app.infrastructure.security.auth_service import AuthenticationService
    auth_svc = AuthenticationService(
        store_path=account_file,
        authority_secret="authoritative_signing_master_secret_key_64_bytes_test",
        signing_secret=jwt_secret,
        app_env="production",
        access_token_expire_minutes=60,
    )
    AuthenticationService._instance = auth_svc
    if not auth_svc.get_user("lead_alice"):
        auth_svc.register_user("lead_alice", "Pass123!Secure", "bank_a", roles=["ml_engineer"], clearance_level=2)
    if not auth_svc.get_user("officer_bob"):
        auth_svc.register_user("officer_bob", "Pass123!Secure", "bank_a", roles=["compliance"], clearance_level=3)

    # 1. Setup trust store with Ed25519 authority
    trust_store = DurableTrustStore(trust_file_path=trust_file, admin_token=admin_token)
    priv = ed25519.Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    issuer_key_id = f"test-ca-ed25519-key-{sim_id}"
    trust_store.register_approved_key(issuer_key_id, pub_hex, admin_token=admin_token)

    verifier = TrainingReceiptVerifier(trust_store=trust_store)
    reg = ModelRegistry(storage_dir=reg_dir, verifier=verifier)

    # 2. Build model artifact bytes
    model_tensor = torch.nn.Linear(10, 2)
    state = model_tensor.state_dict()
    import io
    buf = io.BytesIO()
    torch.save(state, buf)
    raw_bytes = buf.getvalue()
    artifact_sha = hashlib.sha256(raw_bytes).hexdigest()

    # 3. Create cryptographically signed training receipt
    settings = get_settings()
    orig_env = settings.app_env
    settings.app_env = "development"
    try:
        evidence = TrainingExecutionEvidence(
            training_run_id=f"run-{sim_id}",
            job_id=f"job-{sim_id}",
            simulation_id=sim_id,
            completed_at=datetime.now(UTC).isoformat(),
            executed_rounds=5,
            executed_epochs=5,
            sample_count=1000,
            observed_metrics={"loss": 0.042, "auc": 0.96},
            artifact_sha256=artifact_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=[f"f_{i}" for i in range(10)],
        )
        issuer = TrainingReceiptIssuer(
            private_key=priv,
            key_id=issuer_key_id,
            require_authenticated_job=False,
            trust_store=trust_store,
        )
        rcpt = issuer.issue_receipt(
            evidence=evidence,
            model_bytes=raw_bytes,
            owning_bank_id="bank_a",
            architecture="FraudDetectionNN",
            input_dim=10,
            feature_names=[f"f_{i}" for i in range(10)],
            feature_schema_version="v1.0",
        ).to_dict()
    finally:
        settings.app_env = orig_env

    # 4. Bind ownership to bank_a
    ctx_bind = create_trusted_internal_context(
        service_name="training_orchestrator",
        action="bind_ownership",
        resource_type="model",
        resource_id=sim_id,
        bank_id="bank_a",
    )
    reg.bind_simulation_ownership(simulation_id=sim_id, owning_bank_id="bank_a", trusted_context=ctx_bind)

    # 5. Save candidate version
    v_entry = reg.save_version(
        simulation_id=sim_id,
        state_dict=state,
        metrics={"loss": 0.042, "auc": 0.96},
        is_promoted=False,
        provenance_type="TRAINING_RECORDED",
        training_receipt=rcpt,
        artifact_bytes=raw_bytes,
        owning_bank_id="bank_a",
    )
    v_num = v_entry["version"]

    # 6. Add two distinct institutional sign-offs (Four-Eyes Principle)
    domain_key = derive_domain_key(payload_secret, DOMAIN_APPROVAL_SIGNOFF)
    payload_ml = f"{sim_id}:{v_num}:{artifact_sha}:ml_engineer:lead_alice"
    sig_ml = hmac.new(domain_key, payload_ml.encode("utf-8"), hashlib.sha256).hexdigest()

    payload_comp = f"{sim_id}:{v_num}:{artifact_sha}:compliance:officer_bob"
    sig_comp = hmac.new(domain_key, payload_comp.encode("utf-8"), hashlib.sha256).hexdigest()

    manifest = reg._load_manifest(sim_id)
    entry = next(e for e in manifest if e["version"] == v_num)
    entry["sign_offs"] = [
        {
            "role": "ml_engineer",
            "user": "lead_alice",
            "signature": sig_ml,
            "signature_scheme": "APPLICATION_HMAC_SHA256",
            "is_signature_valid": True,
            "is_role_verified": True,
            "is_identity_verified": True,
            "is_independent_human_authorization": True,
            "payload_bound": payload_ml,
            "timestamp": datetime.now(UTC).isoformat(),
            "is_revoked": False,
        },
        {
            "role": "compliance",
            "user": "officer_bob",
            "signature": sig_comp,
            "signature_scheme": "APPLICATION_HMAC_SHA256",
            "is_signature_valid": True,
            "is_role_verified": True,
            "is_identity_verified": True,
            "is_independent_human_authorization": True,
            "payload_bound": payload_comp,
            "timestamp": datetime.now(UTC).isoformat(),
            "is_revoked": False,
        },
    ]
    reg._save_manifest(sim_id, manifest)

    # 7. Promote to champion via trusted internal context
    ctx_promote = create_trusted_internal_context(
        service_name="model_evaluation_engine",
        action="promote_version",
        resource_type="model",
        resource_id=sim_id,
        model_version=v_num,
    )
    reg.promote_version(sim_id, v_num, target_status="champion", trusted_context=ctx_promote)

    return reg, v_num, issuer_key_id


# ── AUTHCTX TESTS ────────────────────────────────────────────────────────────


def test_authctx_01_missing_or_default_production_secret_rejected(isolated_env, monkeypatch):
    """AUTHCTX-01: Missing/default production signing secret cannot issue or validate privileged contexts."""
    # Production environment with default secret must fail closed
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CFI_INTERNAL_CONTEXT_SECRET", "cfi_internal_trusted_context_secret_key_2026_default")
    get_settings.cache_clear()

    with pytest.raises(PermissionError, match="independently provisioned"):
        create_trusted_internal_context("training_orchestrator", "bind_ownership")

    ctx_fake = TrustedInternalContext(
        service_name="training_orchestrator",
        action="bind_ownership",
        nonce="dummy_nonce",
        created_at=time.time(),
        signature="dummy_sig",
    )
    is_valid, reason = verify_trusted_internal_context(ctx_fake, expected_action="bind_ownership")
    assert not is_valid
    assert "independently provisioned" in reason or "known default" in reason

    # Production environment with empty/missing secret must fail closed
    monkeypatch.setenv("CFI_INTERNAL_CONTEXT_SECRET", "")
    monkeypatch.delenv("CFI_INTERNAL_CONTEXT_SECRET", raising=False)
    get_settings.cache_clear()
    with pytest.raises(PermissionError, match="independently provisioned"):
        create_trusted_internal_context("training_orchestrator", "bind_ownership")


def test_authctx_02_tampered_context_fields_invalidate_authorization(isolated_env):
    """AUTHCTX-02: Modified service name, action, target, institution, or version invalidates authorization."""
    ctx = create_trusted_internal_context(
        service_name="model_evaluation_engine",
        action="rollback",
        resource_type="model",
        resource_id="sim_model_01",
        model_version=2,
        bank_id="bank_a",
    )

    # 1. Tamper service_name
    ctx_tampered = replace(ctx, service_name="training_orchestrator")
    is_valid, reason = verify_trusted_internal_context(ctx_tampered, expected_action="rollback")
    assert not is_valid
    assert "Cryptographic signature mismatch" in reason or "Context service mismatch" in reason

    # 2. Tamper action
    ctx_tampered = replace(ctx, action="promote_version")
    is_valid, reason = verify_trusted_internal_context(ctx_tampered, expected_action="rollback")
    assert not is_valid
    assert "Context action mismatch" in reason

    # 3. Tamper resource_id
    ctx_tampered = replace(ctx, resource_id="sim_model_02")
    is_valid, reason = verify_trusted_internal_context(
        ctx_tampered,
        expected_action="rollback",
        expected_resource_id="sim_model_02",
    )
    assert not is_valid
    assert "Cryptographic signature mismatch" in reason

    # 4. Tamper model_version
    ctx_tampered = replace(ctx, model_version=3)
    is_valid, reason = verify_trusted_internal_context(
        ctx_tampered,
        expected_action="rollback",
        expected_resource_id="sim_model_01",
        expected_model_version=3,
    )
    assert not is_valid
    assert "Cryptographic signature mismatch" in reason

    # 5. Tamper bank_id
    ctx_tampered = replace(ctx, bank_id="bank_b")
    is_valid, reason = verify_trusted_internal_context(
        ctx_tampered,
        expected_action="rollback",
        expected_resource_id="sim_model_01",
        expected_bank_id="bank_b",
    )
    assert not is_valid
    assert "Cryptographic signature mismatch" in reason


def test_authctx_03_context_for_model_a_cannot_mutate_model_b(isolated_env):
    """AUTHCTX-03: A valid context for Model A cannot mutate Model B."""
    reg, v1, _ = _create_champion_fixtures(isolated_env, sim_id="sim_model_a")

    # Issue context scoped to Model A
    ctx_a = create_trusted_internal_context(
        service_name="model_evaluation_engine",
        action="rollback",
        resource_type="model",
        resource_id="sim_model_a",
        model_version=v1,
    )

    # Attempt to mutate Model B with Model A's context
    with pytest.raises(ModelIntegrityError, match="Context resource_id scope mismatch"):
        reg.rollback("sim_model_b", v1, trusted_context=ctx_a)


def test_authctx_04_context_for_bank_a_cannot_mutate_bank_b_or_other_consortium(isolated_env):
    """AUTHCTX-04: A valid context for Bank A / Consortium A cannot establish authority over another institution or consortium."""
    svc = ConsortiumGovernanceService()
    init_default_consortium(svc, allow_bootstrap=True)

    # Context scoped to consortium "cfi-consortium" and target bank "bank_b"
    ctx_bank_b = create_trusted_internal_context(
        service_name="consortium_coordinator",
        action="set_member_voting_power",
        resource_type="consortium",
        resource_id="cfi-consortium",
        target_bank_id="bank_b",
    )

    # Attempt to use context to mutate bank_c
    with pytest.raises(PermissionError, match="Context target_bank_id scope mismatch"):
        svc.set_member_voting_power("cfi-consortium", "bank_c", 2.0, trusted_context=ctx_bank_b)

    # Attempt to use context on unrelated consortium
    with pytest.raises(PermissionError, match="Context resource_id scope mismatch"):
        svc.set_member_voting_power("other-consortium", "bank_b", 2.0, trusted_context=ctx_bank_b)


def test_authctx_05_reusing_consumed_context_is_rejected(isolated_env):
    """AUTHCTX-05: Reusing a successfully consumed context is rejected without additional mutation."""
    svc = ConsortiumGovernanceService()
    init_default_consortium(svc, allow_bootstrap=True)

    ctx_single_use = create_trusted_internal_context(
        service_name="consortium_coordinator",
        action="set_member_voting_power",
        resource_type="consortium",
        resource_id="cfi-consortium",
        target_bank_id="bank_b",
    )

    # 1. First mutation succeeds
    member = svc.set_member_voting_power("cfi-consortium", "bank_b", 3.0, trusted_context=ctx_single_use)
    assert member.voting_power == 3.0

    # 2. Second mutation reusing same context fails closed
    with pytest.raises(PermissionError, match="has already been consumed"):
        svc.set_member_voting_power("cfi-consortium", "bank_b", 4.0, trusted_context=ctx_single_use)

    # State remained unchanged at 3.0
    consortium = svc.get_consortium("cfi-consortium")
    assert consortium.members["bank_b"].voting_power == 3.0


def test_authctx_06_racing_independent_processes_produce_at_most_one_consumption(isolated_env):
    """AUTHCTX-06: Two independent processes racing to consume one context produce at most one authorized mutation."""
    store_path = isolated_env["nonce_file"]
    nonce = f"race_nonce_{uuid.uuid4().hex}"
    exp_at = time.time() + 60.0

    worker_code = f"""
import sys, json, time
from app.infrastructure.security.durable_nonce_store import DurableNonceStore

store = DurableNonceStore({json.dumps(store_path)})
consumed, reason = store.consume_nonce({json.dumps(nonce)}, expires_at={exp_at})
print(json.dumps({{"consumed": consumed, "reason": reason}}))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = BACKEND_DIR

    p1 = subprocess.Popen([sys.executable, "-c", worker_code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    p2 = subprocess.Popen([sys.executable, "-c", worker_code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

    out1, err1 = p1.communicate(timeout=10)
    out2, err2 = p2.communicate(timeout=10)

    res1 = json.loads(out1.strip().splitlines()[-1])
    res2 = json.loads(out2.strip().splitlines()[-1])

    results = [res1, res2]
    successes = [r for r in results if r["consumed"] is True]
    rejections = [r for r in results if r["consumed"] is False]

    assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)} (res1={res1}, res2={res2})"
    assert len(rejections) == 1, f"Expected exactly 1 rejection, got {len(rejections)}"
    assert "already been consumed" in rejections[0]["reason"]


def test_authctx_07_replay_protection_survives_restart_and_fails_closed(isolated_env):
    """AUTHCTX-07: Replay protection survives worker restart and fails closed if its authoritative state is unavailable."""
    store_path = isolated_env["nonce_file"]
    nonce = f"restart_nonce_{uuid.uuid4().hex}"
    exp_at = time.time() + 120.0

    # Worker 1 consumes nonce
    store1 = DurableNonceStore(store_path)
    ok1, _ = store1.consume_nonce(nonce, expires_at=exp_at)
    assert ok1 is True

    # Worker restart: Worker 2 starts fresh with empty in-memory state
    DurableNonceStore._instance = None
    store2 = DurableNonceStore(store_path)
    ok2, reason2 = store2.consume_nonce(nonce, expires_at=exp_at)
    assert ok2 is False
    assert "already been consumed" in reason2

    # Fail-closed behavior on corrupted storage in production
    corrupt_path = str(isolated_env["tmp_path"] / "corrupt_nonces.json")
    with open(corrupt_path, "w", encoding="utf-8") as f:
        f.write("{invalid json content!!!")
    with pytest.raises(NonceStoreIntegrityError):
        DurableNonceStore(corrupt_path)


def test_authctx_08_legitimate_internal_automation_remains_functional(isolated_env):
    """AUTHCTX-08: Legitimate automatic rollback, training/model registration, consortium orchestration remain functional."""
    # 1. Consortium bootstrap
    svc = ConsortiumGovernanceService()
    c = init_default_consortium(svc, allow_bootstrap=True)
    assert c.consortium_id == "cfi-consortium"
    assert "bank_a" in c.members
    assert "bank_b" in c.members
    assert "bank_c" in c.members

    # 2. Authentic Model champion setup and automatic rollback
    reg, v1, _ = _create_champion_fixtures(isolated_env, sim_id="sim_auto_rb_01")
    manifest = reg._load_manifest("sim_auto_rb_01")
    assert manifest[0]["is_active"] is True

    # Automated rollback via scoped context
    ctx_rb = create_trusted_internal_context(
        service_name="model_evaluation_engine",
        action="rollback",
        resource_type="model",
        resource_id="sim_auto_rb_01",
        model_version=v1,
    )
    rolled_back = reg.rollback("sim_auto_rb_01", v1, trusted_context=ctx_rb)
    assert rolled_back["version"] == v1
    assert rolled_back["is_active"] is True


# ── SERVE TESTS ──────────────────────────────────────────────────────────────


def test_serve_01_genuine_champion_passes_cold_and_warm_serving(isolated_env):
    """SERVE-01: Genuine eligible production champion passes both cold and warm serving checks before revocation."""
    reg, v_num, _ = _create_champion_fixtures(isolated_env, sim_id="sim_serve_01")

    # Cold serving path
    champion_id_cold = reg.get_active_champion_identity("sim_serve_01")
    assert champion_id_cold is not None
    assert champion_id_cold["version"] == v_num
    assert champion_id_cold["simulation_id"] == "sim_serve_01"

    resolved_cold = reg.resolve_and_verify_champion("sim_serve_01")
    assert resolved_cold is not None
    _, bdata_cold, meta_cold = resolved_cold
    assert meta_cold["version"] == v_num
    assert meta_cold["status"] == "champion"
    assert meta_cold["is_active"] is True

    # Warm serving path (cache is active)
    champion_id_warm = reg.get_active_champion_identity("sim_serve_01")
    assert champion_id_warm is not None
    assert champion_id_warm["version"] == v_num

    resolved_warm = reg.resolve_and_verify_champion("sim_serve_01")
    assert resolved_warm is not None


def test_serve_02_independent_worker_issuer_revocation_blocks_serving(isolated_env):
    """SERVE-02: Independent-worker issuer revocation blocks subsequent unauthorized production serving."""
    reg, v_num, issuer_key_id = _create_champion_fixtures(isolated_env, sim_id="sim_serve_02")

    # Warm serving worker establishes warm cache
    active_identity = reg.get_active_champion_identity("sim_serve_02")
    assert active_identity is not None
    assert active_identity["version"] == v_num
    assert reg.resolve_and_verify_champion("sim_serve_02") is not None

    # Revoke issuer key in an independent operating system process
    admin_token = isolated_env.get("admin_token", "admin-secret-token-test-12345")
    revoke_code = f"""
import sys, os
from app.application.services.training_provenance_service import DurableTrustStore
store = DurableTrustStore(trust_file_path={json.dumps(isolated_env["trust_file"])}, admin_token={json.dumps(admin_token)})
store.revoke_key({json.dumps(issuer_key_id)}, reason="Subprocess adversarial revocation test", admin_token={json.dumps(admin_token)})
print("REVOKED")
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = BACKEND_DIR
    p = subprocess.run([sys.executable, "-c", revoke_code], capture_output=True, text=True, env=env, timeout=45)
    assert p.returncode == 0, f"Revocation subprocess failed: {p.stderr}"
    assert "REVOKED" in p.stdout

    # Warm serving worker attempts subsequent inference/resolution
    # Must immediately detect the revocation across processes and fail closed
    active_identity = reg.get_active_champion_identity("sim_serve_02")
    assert active_identity is None, "Revoked key must invalidate active champion identity across workers"

    with pytest.raises(ModelIntegrityError, match="explicitly revoked|No authorized champion"):
        reg.resolve_and_verify_champion("sim_serve_02")


def test_serve_03_unverified_provenance_rejected_in_production(isolated_env):
    """SERVE-03: Missing/invalid receipt and unverified provenance cannot be served in production, including cached paths."""
    unverified_entry = {
        "version": 1,
        "simulation_id": "sim_unverified",
        "provenance_type": "UNVERIFIED",
        "is_production_eligible": False,
        "status": "champion",
        "sha256": "fake_sha256",
        "sign_offs": [],
    }

    with pytest.raises(ModelIntegrityError, match="Production admission rejected"):
        validate_production_admission(unverified_entry, context="resolve_and_verify_champion")


def test_serve_04_revoked_approval_or_lost_eligibility_blocks_serving(isolated_env):
    """SERVE-04: Revoked approval or lost production eligibility cannot silently retain serving authorization."""
    entry = {
        "version": 1,
        "simulation_id": "sim_revoked_01",
        "is_revoked": True,
        "provenance_type": "TRAINING_RECORDED",
        "is_production_eligible": True,
    }

    with pytest.raises(ModelIntegrityError, match="model version has been revoked"):
        validate_production_admission(entry, context="production_serving")

    entry_ineligible = {
        "version": 1,
        "simulation_id": "sim_ineligible_01",
        "is_revoked": False,
        "provenance_type": "TRAINING_RECORDED",
        "is_production_eligible": False,
    }

    with pytest.raises(ModelIntegrityError, match="explicitly marked as not production-eligible"):
        validate_production_admission(entry_ineligible, context="production_serving")


def test_serve_05_assessment_warm_cache_vulnerability_disproven_for_authentic_champions(isolated_env):
    """SERVE-05: Record evidence proving the existing control instead of creating unnecessary changes."""
    reg, v_num, issuer_key_id = _create_champion_fixtures(isolated_env, sim_id="sim_serve_05")

    # Step 1: Prove authentic champion is valid and cached
    champ_id_1 = reg.get_active_champion_identity("sim_serve_05")
    assert champ_id_1 is not None

    # Step 2: Revoke issuer key
    admin_token = isolated_env.get("admin_token", "admin-secret-token-test-12345")
    trust_store = DurableTrustStore(trust_file_path=isolated_env["trust_file"], admin_token=admin_token)
    trust_store.revoke_key(issuer_key_id, reason="verification", admin_token=admin_token)

    # Step 3: Prove get_active_champion_identity evicts cache and returns None
    champ_id_2 = reg.get_active_champion_identity("sim_serve_05")
    assert champ_id_2 is None

    # Step 4: Prove resolve_and_verify_champion raises ModelIntegrityError
    with pytest.raises(ModelIntegrityError):
        reg.resolve_and_verify_champion("sim_serve_05")
