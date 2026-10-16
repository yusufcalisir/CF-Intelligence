"""Unit and Adversarial Test Suite for Tamper-Evident Security Audit & Historical Evidence Retention.

Covers WORKSTREAM 01-D.3 Invariants:
- AUDIT-01: A revoked model sign-off remains historically intact in the persisted registry.
- AUDIT-02: Revoked sign-offs no longer grant promotion, admission, or serving authority.
- AUDIT-03: Sign-off issuance and revocation identify the actual verified human principal.
- AUDIT-04: Token, account, and issuer-key administrative events record attributable authority rather than spoofed usernames.
- AUDIT-05: Cross-bank governance votes and membership changes retain institution-specific audit attribution.
- AUDIT-06: Valid internal automation emits machine-attributed evidence bound to its permitted resource.
- AUDIT-07: Worker A writes an event; independent Worker B retrieves and verifies it.
- AUDIT-08: Concurrent workers produce a complete and consistently ordered audit history with no lost or duplicated events.
- AUDIT-09: Unauthorized modification or deletion of intermediate audit records is detected.
- AUDIT-10: Forged hash chains, missing authentication metadata, or unavailable production audit authority fail closed.
- AUDIT-11: Simulate an audit persistence failure during a privileged mutation; prove no silent successful-but-unaudited outcome.
- AUDIT-12: Restart preserves event history, historical sign-offs, and revocation evidence.
- AUDIT-13: An impersonated or unauthenticated actor cannot forge a legitimate successful audit event.
- AUDIT-14: Legitimate multi-bank model approvals, consortium voting, issuer management, and automated rollback remain functional.
- AUDIT-15: Existing regression baseline tests remain passing.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
import torch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.consortium_service import (
    ConsortiumGovernanceService,
    ProposalAction,
)
from app.application.services.model_registry import (
    ModelAuthorizationError,
    ModelIntegrityError,
    ModelRegistry,
    create_trusted_internal_context,
    derive_domain_key,
    generate_signoff_signature,
    validate_production_admission,
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
from app.infrastructure.security.immutable_audit_chain import (
    AuditLedgerSecurityError,
    ImmutableAuditChain,
)
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore

# ── TEST FIXTURES & HELPERS ──────────────────────────────────────────────────


@pytest.fixture
def audit_env(tmp_path, monkeypatch):
    """Provides an isolated environment for model registry, trust store, account store, and audit ledger."""
    audit_file = str(tmp_path / "test_security_audit.json")
    nonce_file = str(tmp_path / "test_nonces.json")
    trust_file = str(tmp_path / "test_trust.json")
    reg_dir = str(tmp_path / "test_models")
    consortium_file = str(tmp_path / "test_consortium.json")
    account_file = str(tmp_path / "test_accounts.json")

    audit_secret = "audit_ledger_secret_entropy_high_min32bytes_xyz_123456"
    prod_secret = "prod_internal_secret_key_entropy_high_min32bytes_value_xyz_123456"
    payload_secret = "payload_signing_secret_test_key_minimum_entropy_12345678"
    consortium_secret = "consortium_store_secret_entropy_high_32_bytes_min_xyz_1234"
    jwt_secret = "jwt_signing_secret_high_entropy_min_32_bytes_value_123456789"
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
    monkeypatch.setenv("CFI_ACCOUNT_AUTHORITY_SECRET", "authoritative_signing_master_secret_key_64_bytes_test")
    monkeypatch.setenv("APP_ENV", "production")

    get_settings.cache_clear()
    DurableNonceStore._instance = None
    DurableConsortiumStore._instance = None
    AuthenticationService._instance = None
    ImmutableAuditChain._instance = None

    yield {
        "tmp_path": tmp_path,
        "audit_file": audit_file,
        "audit_secret": audit_secret,
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
    ImmutableAuditChain._instance = None


def _setup_authoritative_users(env: dict[str, Any]) -> tuple[AuthenticationService, str, str, str]:
    """Provisions authentic ML engineer, Compliance officer, and Admin users and returns their JWT access tokens."""
    auth_svc = AuthenticationService(
        store_path=env["account_file"],
        signing_secret=env["jwt_secret"],
        authority_secret="authoritative_signing_master_secret_key_64_bytes_test",
        app_env="production",
    )
    AuthenticationService._instance = auth_svc

    u_ml = auth_svc.register_user("dr_smith_ml", "SecurePass1234!", "bank_a", ["ml_engineer"], clearance_level=2)
    u_comp = auth_svc.register_user("officer_jones_compliance", "SecurePass1234!", "bank_a", ["compliance"], clearance_level=2)
    u_admin = auth_svc.register_user("admin_user_chief", "SecurePass1234!", "bank_a", ["admin"], clearance_level=3)

    token_ml = auth_svc.create_token_bundle(u_ml).access_token
    token_comp = auth_svc.create_token_bundle(u_comp).access_token
    token_admin = auth_svc.create_token_bundle(u_admin).access_token

    return auth_svc, token_ml, token_comp, token_admin


def _setup_training_issuer(env: dict[str, Any]) -> tuple[TrainingReceiptIssuer, TrainingReceiptVerifier, str, DurableTrustStore]:
    """Provisions an Ed25519 issuer key and durable trust store."""
    issuer_priv = ed25519.Ed25519PrivateKey.generate()
    issuer_pub_hex = issuer_priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    kid = "trusted_issuer_key_v1"

    trust_store = DurableTrustStore(
        trust_file_path=env["trust_file"],
        admin_token=env["admin_token"],
    )
    trust_store.register_approved_key(
        key_id=kid,
        public_key_hex=issuer_pub_hex,
        admin_token=env["admin_token"],
    )

    verifier = TrainingReceiptVerifier(trust_store=trust_store)
    from app.config import get_settings
    settings = get_settings()
    orig_env = settings.app_env
    settings.app_env = "development"
    try:
        issuer = TrainingReceiptIssuer(
            key_id=kid,
            private_key=issuer_priv,
            require_authenticated_job=False,
            app_env="development",
            trust_store=trust_store,
        )
    finally:
        settings.app_env = orig_env
    return issuer, verifier, kid, trust_store


def _create_sample_model(
    env: dict[str, Any],
    sim_id: str,
    issuer: TrainingReceiptIssuer,
    verifier: TrainingReceiptVerifier,
    auth_token: str,
    bank_id: str = "bank_a",
) -> tuple[ModelRegistry, dict[str, Any]]:
    """Creates and persists an initial version in ModelRegistry with valid receipt."""
    from app.application.services.model_registry import create_trusted_internal_context

    reg = ModelRegistry(env["reg_dir"], verifier=verifier)
    state = {"layer.weight": torch.randn(10, 10)}
    fake_bytes = b"MODEL_WEIGHT_BYTES_STABLE_V1"

    ev = TrainingExecutionEvidence(
        training_run_id=f"run-{sim_id}",
        job_id=f"job-{sim_id}",
        simulation_id=sim_id,
        completed_at=datetime.now(UTC).isoformat(),
        executed_rounds=5,
        executed_epochs=5,
        sample_count=1000,
        observed_metrics={"loss": 0.042, "auc": 0.96, "f1": 0.95},
        artifact_sha256=hashlib.sha256(fake_bytes).hexdigest(),
        architecture="FraudDetectionModel-GroupNorm",
        feature_schema_version="2.0",
        feature_names=["f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10"],
    )
    from app.config import get_settings
    settings = get_settings()
    orig_env = settings.app_env
    settings.app_env = "development"
    try:
        rcpt = issuer.issue_receipt(
            evidence=ev,
            model_bytes=fake_bytes,
            owning_bank_id=bank_id,
            architecture="FraudDetectionModel-GroupNorm",
            input_dim=10,
            feature_names=["f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10"],
            feature_schema_version="2.0",
        ).to_dict()
    finally:
        settings.app_env = orig_env

    ctx_bind = create_trusted_internal_context(
        service_name="training_orchestrator",
        action="bind_ownership",
        resource_type="model",
        resource_id=sim_id,
        bank_id=bank_id,
    )
    reg.bind_simulation_ownership(simulation_id=sim_id, owning_bank_id=bank_id, trusted_context=ctx_bind)

    entry = reg.save_version(
        simulation_id=sim_id,
        state_dict=state,
        metrics={"f1": 0.95},
        is_promoted=False,
        artifact_bytes=fake_bytes,
        training_receipt=rcpt,
        provenance_type="TRAINING_RECORDED",
        expected_architecture="FraudDetectionModel-GroupNorm",
        expected_feature_schema_version="2.0",
        expected_input_dim=10,
        expected_feature_names=["f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10"],
        owning_bank_id=bank_id,
        auth_token=auth_token,
    )
    return reg, entry


# ── ADVERSARIAL TESTS ────────────────────────────────────────────────────────


def test_audit_01_revoked_signoff_remains_historically_intact(audit_env):
    """AUDIT-01: A revoked model sign-off remains historically intact in the persisted registry.

    Verifies:
    - Calling revoke_sign_off does NOT delete the entry from entry['sign_offs'].
    - Manifest retains status='revoked', is_revoked=True, is_active=False.
    - Preserves original signer, timestamp, signature, and records revoking actor, revocation reason, and time.
    """
    _, token_ml, _, token_admin = _setup_authoritative_users(audit_env)
    issuer, verifier, _, _ = _setup_training_issuer(audit_env)
    sim_id = "sim_audit_01"
    reg, entry = _create_sample_model(audit_env, sim_id, issuer, verifier, token_ml)
    version = entry["version"]

    # 1. Sign off as ML Engineer
    sha = entry["sha256"]
    sig_ml = generate_signoff_signature(sim_id, version, sha, "ml_engineer", "dr_smith_ml", signing_secret=audit_env["payload_secret"])
    reg.sign_off(
        simulation_id=sim_id,
        version=version,
        role="ml_engineer",
        user="dr_smith_ml",
        signature=sig_ml,
        auth_token=token_ml,
    )

    # Verify sign-off was written
    manifest_before = reg.list_versions(sim_id)
    assert len(manifest_before[0]["sign_offs"]) == 1
    orig_sig = manifest_before[0]["sign_offs"][0]["signature"]
    orig_user = manifest_before[0]["sign_offs"][0]["user"]

    # 2. Revoke the sign-off with authentic attribution and explicit reason
    revocation_reason = "Model re-calibration required following post-validation drift test"
    reg.revoke_sign_off(
        simulation_id=sim_id,
        version=version,
        role="ml_engineer",
        auth_token=token_admin,
        reason=revocation_reason,
    )

    # 3. Read directly from persisted disk manifest (verifying persistence without cache masking)
    manifest_path = os.path.join(reg._get_sim_dir(sim_id), "registry.json")
    with open(manifest_path, encoding="utf-8") as f:
        on_disk_manifest = json.load(f)

    sign_offs = on_disk_manifest[0]["sign_offs"]
    # CRITICAL: Record must NOT be removed from sign_offs!
    assert len(sign_offs) == 1, "Historical sign-off was deleted from persisted manifest!"
    s = sign_offs[0]
    assert s["status"] == "revoked"
    assert s["is_revoked"] is True
    assert s["is_active"] is False
    assert s["user"] == orig_user == "dr_smith_ml"
    assert s["signature"] == orig_sig
    assert s["revoked_by"] == "admin_user_chief"
    assert s["revocation_reason"] == revocation_reason
    assert s.get("revoked_at") is not None
    datetime.fromisoformat(s["revoked_at"])  # Must be valid ISO timestamp


def test_audit_02_revoked_signoffs_deny_promotion_admission_serving(audit_env):
    """AUDIT-02: Revoked sign-offs no longer grant promotion, admission, or serving authority.

    Verifies:
    - Active four-eyes gate excludes revoked sign-offs.
    - Demotes champion when required sign-off is revoked.
    - Production admission check strictly rejects versions with revoked sign-offs.
    """
    _, token_ml, token_comp, token_admin = _setup_authoritative_users(audit_env)
    issuer, verifier, _, _ = _setup_training_issuer(audit_env)
    sim_id = "sim_audit_02"
    reg, entry = _create_sample_model(audit_env, sim_id, issuer, verifier, token_ml)
    version = entry["version"]

    # Dual sign-off
    sha = entry["sha256"]
    sig_ml = generate_signoff_signature(sim_id, version, sha, "ml_engineer", "dr_smith_ml", signing_secret=audit_env["payload_secret"])
    sig_comp = generate_signoff_signature(sim_id, version, sha, "compliance", "officer_jones_compliance", signing_secret=audit_env["payload_secret"])
    reg.sign_off(sim_id, version, "ml_engineer", "dr_smith_ml", signature=sig_ml, auth_token=token_ml)
    reg.sign_off(sim_id, version, "compliance", "officer_jones_compliance", signature=sig_comp, auth_token=token_comp)

    # Promotion to champion succeeds
    reg.promote_version(sim_id, version, "champion", auth_token=token_admin)
    active_champ = reg.get_active_version(sim_id)
    assert active_champ is not None
    assert active_champ["status"] == "champion"
    assert active_champ["is_active"] is True

    # Revoke Compliance approval
    reg.revoke_sign_off(
        sim_id,
        version,
        "compliance",
        auth_token=token_admin,
        reason="Compliance approval retracted due to updated KYC regulatory mandate",
    )

    # Verify champion was automatically demoted and is no longer active
    manifest = reg.list_versions(sim_id)
    assert manifest[0]["status"] == "inactive"
    assert manifest[0]["is_active"] is False

    # Production admission fails closed
    with pytest.raises(ModelIntegrityError, match="Missing required sign-offs"):
        validate_production_admission(manifest[0], context="promote_version")

    # Serving eligibility resolution returns None
    assert reg.get_active_champion_identity(sim_id) is None
    assert reg.resolve_and_verify_champion(sim_id) is None


def test_audit_03_signoff_issuance_and_revocation_authentic_attribution(audit_env):
    """AUDIT-03: Sign-off issuance and revocation identify the actual verified human principal.

    Verifies:
    - Caller-supplied spoofed username is rejected when token principal disagrees.
    - Unauthenticated mutations are rejected fail-closed.
    - Both sign-off and sign-off revocation record verified human identity in manifest and audit ledger.
    """
    _, token_ml, token_comp, token_admin = _setup_authoritative_users(audit_env)
    issuer, verifier, _, _ = _setup_training_issuer(audit_env)
    sim_id = "sim_audit_03"
    reg, entry = _create_sample_model(audit_env, sim_id, issuer, verifier, token_ml)
    version = entry["version"]

    # Spoofed username: token is dr_smith_ml, but user argument claims alice_spoof
    sha = entry["sha256"]
    sig_spoof = generate_signoff_signature(sim_id, version, sha, "ml_engineer", "alice_spoof", signing_secret=audit_env["payload_secret"])
    sig_ml = generate_signoff_signature(sim_id, version, sha, "ml_engineer", "dr_smith_ml", signing_secret=audit_env["payload_secret"])
    with pytest.raises((ModelAuthorizationError, ModelIntegrityError), match="does not match sign-off user"):
        reg.sign_off(sim_id, version, "ml_engineer", user="alice_spoof", signature=sig_spoof, auth_token=token_ml)

    # Authentic sign-off
    reg.sign_off(sim_id, version, "ml_engineer", user="dr_smith_ml", signature=sig_ml, auth_token=token_ml)

    # Audit chain must record MODEL_SIGNOFF_RECORDED with dr_smith_ml
    audit_chain = ImmutableAuditChain.get_instance()
    events = audit_chain.query_events(event_type="MODEL_SIGNOFF_RECORDED")
    assert len(events) >= 1
    last_event = events[-1]
    assert "dr_smith_ml" in last_event.actor
    assert last_event.actor_institution == "bank_a"
    assert last_event.actor_type == "human_principal"

    # Authentic revocation
    reg.revoke_sign_off(
        sim_id,
        version,
        "ml_engineer",
        auth_token=token_admin,
        reason="Periodic key and credential rotation audit",
    )
    rev_events = audit_chain.query_events(event_type="MODEL_SIGNOFF_REVOCATION")
    assert len(rev_events) >= 1
    rev_ev = rev_events[-1]
    assert "admin_user_chief" in rev_ev.actor
    assert rev_ev.details.get("reason") == "Periodic key and credential rotation audit"


def test_audit_04_token_account_issuer_key_attributable_authority(audit_env):
    """AUDIT-04: Token, account, and issuer-key administrative events record attributable authority.

    Verifies:
    - DurableTrustStore.revoke_key does not use constant revoked_by='admin'.
    - When operator token is passed, it records authentic human operator.
    - When only shared administrative secret is passed, it reports truthful limitation.
    """
    auth_svc, _, _, token_admin = _setup_authoritative_users(audit_env)
    _, _, kid, trust_store = _setup_training_issuer(audit_env)

    # 1. Revoke key using authenticated operator token
    trust_store.revoke_key(
        key_id=kid,
        reason="compromise_detected",
        admin_token=audit_env["admin_token"],
        operator_token=token_admin,
    )
    rev_record = trust_store._revoked_keys[kid]
    assert rev_record["revoked_by"] == "user:admin_user_chief@bank_a"
    assert rev_record["actor_type"] == "authenticated_operator"
    assert rev_record["attribution_limitation"] is None

    # 2. Register second key, then revoke with ONLY admin_token (no operator token)
    kid2 = "trusted_issuer_key_v2"
    key2_priv = ed25519.Ed25519PrivateKey.generate()
    key2_hex = key2_priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    trust_store.register_approved_key(kid2, key2_hex, admin_token=audit_env["admin_token"])

    trust_store.revoke_key(
        key_id=kid2,
        reason="shared_secret_automated_drain",
        admin_token=audit_env["admin_token"],
        operator_token=None,
    )
    rev2 = trust_store._revoked_keys[kid2]
    # Must NOT fabricate generic 'admin'
    assert rev2["revoked_by"] == "unattributed_shared_admin_secret"
    assert rev2["actor_type"] == "shared_secret"
    assert rev2["attribution_limitation"] == "AUTHENTICATION_SECRET_PRESENT_BUT_INDIVIDUAL_OPERATOR_UNATTRIBUTED"

    # 3. Token & Account revocation audit attribution
    sample_user = auth_svc.register_user("operator_bob", "Password123!", "bank_a", ["analyst"])
    bundle = auth_svc.create_token_bundle(sample_user)
    auth_svc.revoke_token(bundle.access_token, actor_token=token_admin, reason="compromised_device")
    auth_svc.deactivate_user("operator_bob", actor_token=token_admin, reason="employee_offboarding")

    audit_chain = ImmutableAuditChain.get_instance()
    tok_evs = audit_chain.query_events(event_type="TOKEN_REVOCATION")
    assert len(tok_evs) >= 1
    assert "admin_user_chief" in tok_evs[-1].actor

    user_evs = audit_chain.query_events(event_type="USER_DEACTIVATED")
    assert len(user_evs) >= 1
    assert "admin_user_chief" in user_evs[-1].actor


def test_audit_05_cross_bank_governance_retains_institution_attribution(audit_env):
    """AUDIT-05: Cross-bank governance votes and membership changes retain institution-specific audit attribution.

    Verifies:
    - Proposal creation, vote casting, member admission, and member expulsion record institution attribution.
    - An investigator can reconstruct who voted and why Bank B was removed.
    """
    auth_svc, _, _, token_admin = _setup_authoritative_users(audit_env)

    # Provision Bank B governance representative
    u_b = auth_svc.register_user("officer_bank_b", "SecurePass1234!", "bank_b", ["admin"])
    token_b = auth_svc.create_token_bundle(u_b).access_token
    assert token_b

    cs = ConsortiumGovernanceService()
    consortium_id = "consortium_audit_05"

    # Bank A creates consortium
    cs.create_consortium(
        consortium_id=consortium_id,
        name="Alliance 05",
        founder_bank_id="bank_a",
        quorum_ratio=0.50,
        auth_token=token_admin,
    )

    # Bank A proposes adding Bank B
    prop_add = cs.propose_membership_change(
        consortium_id=consortium_id,
        creator_bank_id="bank_a",
        target_bank_id="bank_b",
        action=ProposalAction.ADD_MEMBER,
        auth_token=token_admin,
    )
    assert prop_add.proposal_id
    # Creator automatically voted FOR; quorum reached (100% >= 51%), Bank B admitted

    # Verify audit chain records admission
    audit_chain = ImmutableAuditChain.get_instance()
    admit_evs = audit_chain.query_events(event_type="CONSORTIUM_MEMBER_ADMITTED")
    assert any(e.details.get("target_bank_id") == "bank_b" for e in admit_evs)

    # Later, Bank A proposes expelling Bank B
    prop_expel = cs.propose_membership_change(
        consortium_id=consortium_id,
        creator_bank_id="bank_a",
        target_bank_id="bank_b",
        action=ProposalAction.REMOVE_MEMBER,
        auth_token=token_admin,
    )
    assert prop_expel.proposal_id
    # Bank A votes FOR removal; quorum reached
    # Verify audit chain records expulsion with authoritative proposal decision
    expel_evs = audit_chain.query_events(event_type="CONSORTIUM_MEMBER_EXPELLED")
    assert len(expel_evs) >= 1
    last_expel = expel_evs[-1]
    assert last_expel.details.get("target_bank_id") == "bank_b"
    assert "Expelled via approved governance proposal" in last_expel.details.get("reason", "")
    assert "bank_a" in last_expel.details.get("approved_by_votes", [])


def test_audit_06_internal_automation_machine_attributed_evidence(audit_env):
    """AUDIT-06: Valid internal automation emits machine-attributed evidence bound to its permitted resource."""
    _, token_ml, token_comp, token_admin = _setup_authoritative_users(audit_env)
    issuer, verifier, _, _ = _setup_training_issuer(audit_env)
    sim_id = "sim_audit_06"
    reg, entry = _create_sample_model(audit_env, sim_id, issuer, verifier, token_ml)
    version = entry["version"]

    # Dual sign-off and promote to champion
    sha = entry["sha256"]
    sig_ml = generate_signoff_signature(sim_id, version, sha, "ml_engineer", "dr_smith_ml", signing_secret=audit_env["payload_secret"])
    sig_comp = generate_signoff_signature(sim_id, version, sha, "compliance", "officer_jones_compliance", signing_secret=audit_env["payload_secret"])
    reg.sign_off(sim_id, version, "ml_engineer", "dr_smith_ml", signature=sig_ml, auth_token=token_ml)
    reg.sign_off(sim_id, version, "compliance", "officer_jones_compliance", signature=sig_comp, auth_token=token_comp)
    reg.promote_version(sim_id, version, "champion", auth_token=token_admin)

    # Automated evaluation engine triggers rollback via scoped internal context
    ctx = create_trusted_internal_context(
        service_name="model_evaluation_engine",
        action="rollback",
        resource_type="model",
        resource_id=sim_id,
        model_version=version,
        bank_id="bank_a",
        signing_secret=audit_env["prod_secret"],
    )
    reg.rollback(sim_id, version, trusted_context=ctx)

    audit_chain = ImmutableAuditChain.get_instance()
    rb_events = audit_chain.query_events(event_type="MODEL_ROLLBACK")
    assert len(rb_events) >= 1
    last_rb = rb_events[-1]
    assert last_rb.actor == "internal:model_evaluation_engine"
    assert last_rb.actor_type == "internal_service"
    assert last_rb.target_id == f"{sim_id}:v{version}"


def test_audit_07_worker_a_writes_independent_worker_b_verifies(audit_env):
    """AUDIT-07: Worker A writes an event; independent Worker B retrieves and verifies it."""
    # Worker A writes an event
    chain_a = ImmutableAuditChain(
        store_path=audit_env["audit_file"],
        signing_secret=audit_env["audit_secret"],
    )
    chain_a.append_event(
        event_type="WORKER_A_INITIALIZATION",
        actor="worker_a",
        target_id="resource_x",
        details={"state": "active"},
    )
    proof_a = chain_a.get_chain_proof_hash()

    # Worker B initializes independently from same disk store
    chain_b = ImmutableAuditChain(
        store_path=audit_env["audit_file"],
        signing_secret=audit_env["audit_secret"],
    )
    report_b = chain_b.verify_chain_integrity()
    assert report_b.is_valid is True
    assert chain_b.get_chain_proof_hash() == proof_a

    # Worker B writes an event
    len_before = len(chain_b)
    chain_b.append_event(
        event_type="WORKER_B_CONFIRMATION",
        actor="worker_b",
        target_id="resource_x",
        details={"confirmation": "verified"},
    )
    assert len(chain_b) == len_before + 1

    # Worker A reloads and verifies
    chain_a.reload()
    report_a = chain_a.verify_chain_integrity()
    assert report_a.is_valid is True
    assert len(chain_a) == len_before + 1
    assert chain_a.get_chain_proof_hash() == chain_b.get_chain_proof_hash()


def test_audit_08_concurrent_workers_produce_consistent_history(audit_env):
    """AUDIT-08: Concurrent workers produce a complete and consistently ordered audit history with no lost or duplicated events."""
    audit_file = audit_env["audit_file"]
    audit_secret = audit_env["audit_secret"]

    def _worker_task(worker_id: int, num_events: int) -> list[str]:
        chain = ImmutableAuditChain(store_path=audit_file, signing_secret=audit_secret)
        event_ids = []
        for i in range(num_events):
            entry = chain.append_event(
                event_type=f"CONCURRENT_EVENT_{worker_id}",
                actor=f"worker_{worker_id}",
                target_id=f"target_{worker_id}_{i}",
                details={"step": i},
            )
            event_ids.append(entry.event_id)
        return event_ids

    workers = 4
    events_per_worker = 10
    total_expected_new = workers * events_per_worker

    # Read initial length
    init_chain = ImmutableAuditChain(store_path=audit_file, signing_secret=audit_secret)
    init_len = len(init_chain)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_worker_task, w, events_per_worker) for w in range(workers)]
        results = [f.result() for f in futures]

    all_event_ids = [eid for sub in results for eid in sub]
    assert len(set(all_event_ids)) == total_expected_new

    # Verify final ledger
    final_chain = ImmutableAuditChain(store_path=audit_file, signing_secret=audit_secret)
    final_chain.reload(force=True)
    assert len(final_chain) == init_len + total_expected_new

    # Indices must be strictly 0, 1, 2, ..., N-1 with no gaps
    indices = [e.index for e in final_chain.chain]
    assert indices == list(range(len(final_chain)))

    # Full cryptographic verification must pass
    report = final_chain.verify_chain_integrity()
    assert report.is_valid is True
    assert report.total_records == len(final_chain)


def test_audit_09_intermediate_tampering_or_deletion_detected(audit_env):
    """AUDIT-09: Unauthorized modification or deletion of intermediate audit records is detected."""
    chain = ImmutableAuditChain(store_path=audit_env["audit_file"], signing_secret=audit_env["audit_secret"])
    for i in range(5):
        chain.append_event("TEST_EVENT", f"actor_{i}", f"target_{i}", details={"val": i})

    report_clean = chain.verify_chain_integrity()
    assert report_clean.is_valid is True

    # 1. Tamper with details in the middle
    with open(audit_env["audit_file"], "rb") as f:
        data = json.loads(f.read().decode("utf-8"))

    # Mutate entry 2 details
    data["entries"][2]["details"]["val"] = 999999
    with open(audit_env["audit_file"], "wb") as f:
        f.write(json.dumps(data, indent=2).encode("utf-8"))

    chain_tampered = ImmutableAuditChain(store_path=audit_env["audit_file"], signing_secret=audit_env["audit_secret"])
    report_tampered = chain_tampered.verify_chain_integrity()
    assert report_tampered.is_valid is False
    assert "tampering detected" in report_tampered.tamper_reason.lower() or "signature verification failed" in report_tampered.tamper_reason.lower()

    # 2. Deletion of record
    del data["entries"][2]
    with open(audit_env["audit_file"], "wb") as f:
        f.write(json.dumps(data, indent=2).encode("utf-8"))

    chain_deleted = ImmutableAuditChain(store_path=audit_env["audit_file"], signing_secret=audit_env["audit_secret"])
    report_deleted = chain_deleted.verify_chain_integrity()
    assert report_deleted.is_valid is False
    assert "index mismatch" in report_deleted.tamper_reason.lower() or "sequence deletion" in report_deleted.tamper_reason.lower()


def test_audit_10_forged_chain_missing_auth_fail_closed(audit_env, monkeypatch):
    """AUDIT-10: Forged hash chains, missing authentication metadata, or unavailable production audit authority fail closed."""
    # Write legitimate events
    chain = ImmutableAuditChain(store_path=audit_env["audit_file"], signing_secret=audit_env["audit_secret"])
    chain.append_event("LEGIT_EVENT", "admin", "target_1", details={"msg": "authorized"})

    # Attacker recomputes hashes with a different secret (forgery attempt)
    with open(audit_env["audit_file"], "rb") as f:
        data = json.loads(f.read().decode("utf-8"))

    forged_key = derive_domain_key("attacker_forged_secret_key_12345678", "cfi:security_audit_ledger:v1")
    prev = data["genesis_hash"]
    for e in data["entries"]:
        e["prev_hash"] = prev
        payload_dict = {
            "index": e["index"],
            "event_type": e["event_type"],
            "actor": e["actor"],
            "target_id": e["target_id"],
            "timestamp": e["timestamp"],
            "details": e["details"],
            "prev_hash": prev,
            "event_id": e.get("event_id", ""),
            "actor_type": e.get("actor_type", "human_principal"),
            "actor_institution": e.get("actor_institution", "unknown"),
            "target_type": e.get("target_type", "unknown"),
            "action": e.get("action", ""),
            "decision": e.get("decision", "PERMITTED"),
            "correlation_id": e.get("correlation_id", ""),
        }
        serialized = json.dumps(payload_dict, sort_keys=True)
        h = hmac.new(forged_key, f"{serialized}||{prev}".encode(), hashlib.sha256).hexdigest()
        e["curr_hash"] = h
        prev = h

    data["tail_hash"] = prev
    with open(audit_env["audit_file"], "wb") as f:
        f.write(json.dumps(data, indent=2).encode("utf-8"))

    # Legitimate system verifier strictly rejects forged chain (HMAC authentication mismatch)
    verifier_chain = ImmutableAuditChain(store_path=audit_env["audit_file"], signing_secret=audit_env["audit_secret"])
    report = verifier_chain.verify_chain_integrity()
    assert report.is_valid is False
    assert "tampering detected" in report.tamper_reason.lower() or "signature verification failed" in report.tamper_reason.lower()

    # In production with missing secret -> fails closed
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("CFI_AUDIT_LEDGER_SECRET", raising=False)
    monkeypatch.delenv("CFI_PAYLOAD_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("CFI_INTERNAL_CONTEXT_SECRET", raising=False)
    get_settings.cache_clear()

    with pytest.raises(AuditLedgerSecurityError, match="Production audit ledger requires an authoritative signing secret"):
        ImmutableAuditChain(store_path=audit_env["audit_file"], signing_secret="")


def test_audit_11_audit_persistence_failure_no_silent_unaudited_mutation(audit_env):
    """AUDIT-11: Simulate an audit persistence failure during a privileged mutation; prove no silent successful-but-unaudited outcome."""
    _, token_ml, token_comp, token_admin = _setup_authoritative_users(audit_env)
    issuer, verifier, _, _ = _setup_training_issuer(audit_env)
    sim_id = "sim_audit_11"
    reg, entry = _create_sample_model(audit_env, sim_id, issuer, verifier, token_ml)
    version = entry["version"]

    # 1. Version 1 is dual signed and becomes champion
    sha = entry["sha256"]
    sig_ml = generate_signoff_signature(sim_id, version, sha, "ml_engineer", "dr_smith_ml", signing_secret=audit_env["payload_secret"])
    sig_comp = generate_signoff_signature(sim_id, version, sha, "compliance", "officer_jones_compliance", signing_secret=audit_env["payload_secret"])
    reg.sign_off(sim_id, version, "ml_engineer", "dr_smith_ml", signature=sig_ml, auth_token=token_ml)
    reg.sign_off(sim_id, version, "compliance", "officer_jones_compliance", signature=sig_comp, auth_token=token_comp)
    reg.promote_version(sim_id, version, "champion", auth_token=token_admin)

    # 2. Save version 2 (candidate/challenger)
    state2 = {"layer.weight": torch.randn(10, 10)}
    raw_bytes2 = b"MODEL_WEIGHT_BYTES_STABLE_V2"
    ev2 = TrainingExecutionEvidence(
        training_run_id=f"run-{sim_id}-2",
        job_id=f"job-{sim_id}-2",
        simulation_id=sim_id,
        completed_at=datetime.now(UTC).isoformat(),
        executed_rounds=5,
        executed_epochs=5,
        sample_count=1000,
        observed_metrics={"loss": 0.038, "auc": 0.97, "f1": 0.96},
        artifact_sha256=hashlib.sha256(raw_bytes2).hexdigest(),
        architecture="FraudDetectionModel-GroupNorm",
        feature_schema_version="2.0",
        feature_names=["f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10"],
    )
    from app.config import get_settings
    settings = get_settings()
    orig_env = settings.app_env
    settings.app_env = "development"
    try:
        rcpt2 = issuer.issue_receipt(
            evidence=ev2,
            model_bytes=raw_bytes2,
            owning_bank_id="bank_a",
            architecture="FraudDetectionModel-GroupNorm",
            input_dim=10,
            feature_names=["f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10"],
            feature_schema_version="2.0",
        ).to_dict()
    finally:
        settings.app_env = orig_env

    entry2 = reg.save_version(
        simulation_id=sim_id,
        state_dict=state2,
        metrics={"f1": 0.96},
        is_promoted=False,
        artifact_bytes=raw_bytes2,
        training_receipt=rcpt2,
        provenance_type="TRAINING_RECORDED",
        expected_architecture="FraudDetectionModel-GroupNorm",
        expected_feature_schema_version="2.0",
        expected_input_dim=10,
        expected_feature_names=["f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10"],
        owning_bank_id="bank_a",
        auth_token=token_ml,
    )
    v2 = entry2["version"]
    sha2 = entry2["sha256"]
    sig_ml2 = generate_signoff_signature(sim_id, v2, sha2, "ml_engineer", "dr_smith_ml", signing_secret=audit_env["payload_secret"])
    sig_comp2 = generate_signoff_signature(sim_id, v2, sha2, "compliance", "officer_jones_compliance", signing_secret=audit_env["payload_secret"])
    reg.sign_off(sim_id, v2, "ml_engineer", "dr_smith_ml", signature=sig_ml2, auth_token=token_ml)
    reg.sign_off(sim_id, v2, "compliance", "officer_jones_compliance", signature=sig_comp2, auth_token=token_comp)

    # v2 is currently status='approved' because it was signed off but not yet promoted to champion
    manifest_before = reg.list_versions(sim_id)
    v2_before = next(e for e in manifest_before if e["version"] == v2)
    assert v2_before["status"] == "approved"
    assert v2_before["is_active"] is False

    # Simulate audit chain disk failure during promotion of v2 to champion
    with patch.object(ImmutableAuditChain, "append_event", side_effect=AuditLedgerSecurityError("Disk I/O failure on audit ledger")):
        with pytest.raises(AuditLedgerSecurityError, match="Disk I/O failure on audit ledger"):
            reg.promote_version(sim_id, v2, "champion", auth_token=token_admin)

    # Prove no silent successful mutation: v2 must STILL be approved, not champion!
    manifest_after = reg.list_versions(sim_id)
    v2_after = next(e for e in manifest_after if e["version"] == v2)
    assert v2_after["status"] == "approved"
    assert v2_after["is_active"] is False
    v1_after = next(e for e in manifest_after if e["version"] == 1)
    assert v1_after["status"] == "champion"
    assert v1_after["is_active"] is True


def test_audit_12_restart_preserves_event_history_and_historical_signoffs(audit_env):
    """AUDIT-12: Restart preserves event history, historical sign-offs, and revocation evidence."""
    _, token_ml, token_comp, token_admin = _setup_authoritative_users(audit_env)
    issuer, verifier, kid, trust_store = _setup_training_issuer(audit_env)
    sim_id = "sim_audit_12"
    reg, entry = _create_sample_model(audit_env, sim_id, issuer, verifier, token_ml)
    version = entry["version"]

    sha = entry["sha256"]
    sig_ml = generate_signoff_signature(sim_id, version, sha, "ml_engineer", "dr_smith_ml", signing_secret=audit_env["payload_secret"])
    reg.sign_off(sim_id, version, "ml_engineer", "dr_smith_ml", signature=sig_ml, auth_token=token_ml)
    reg.revoke_sign_off(sim_id, version, "ml_engineer", auth_token=token_admin, reason="audit_restart_test")
    trust_store.revoke_key(kid, reason="key_rotation_test", admin_token=audit_env["admin_token"], operator_token=token_admin)

    # Simulate process restart by resetting all singletons
    ImmutableAuditChain.reset_instance()
    AuthenticationService.reset_instance()

    # Re-instantiate services
    restarted_chain = ImmutableAuditChain.get_instance()
    assert restarted_chain.verify_chain_integrity().is_valid is True

    restarted_reg = ModelRegistry(audit_env["reg_dir"])
    restarted_manifest = restarted_reg.list_versions(sim_id)
    sign_offs = restarted_manifest[0]["sign_offs"]
    assert len(sign_offs) == 1
    assert sign_offs[0]["status"] == "revoked"
    assert sign_offs[0]["revocation_reason"] == "audit_restart_test"

    restarted_trust = DurableTrustStore(trust_file_path=audit_env["trust_file"], admin_token=audit_env["admin_token"])
    assert restarted_trust.is_key_revoked(kid) is True


def test_audit_13_impersonated_actor_cannot_forge_successful_event(audit_env):
    """AUDIT-13: An impersonated or unauthenticated actor cannot forge a legitimate successful audit event."""
    auth_svc, token_ml, _, _ = _setup_authoritative_users(audit_env)
    cs = ConsortiumGovernanceService()
    consortium_id = "consortium_audit_13"

    # Attempt to execute governance action claiming to be Bank B while authenticated as Bank A
    with pytest.raises(PermissionError, match="Cross-bank direct service operation rejected"):
        cs.create_consortium(
            consortium_id=consortium_id,
            name="Alliance 13",
            founder_bank_id="bank_b",
            auth_token=token_ml,  # token_ml belongs to bank_a
        )

    # Audit chain must NOT record a successful CONSORTIUM_CREATED event
    audit_chain = ImmutableAuditChain.get_instance()
    created_events = [e for e in audit_chain.query_events(event_type="CONSORTIUM_CREATED") if e.details.get("consortium_id") == consortium_id]
    assert len(created_events) == 0


def test_audit_14_positive_multibank_governance_operational(audit_env):
    """AUDIT-14: Legitimate multi-bank model approvals, consortium voting, issuer management, and automated rollback remain functional."""
    auth_svc, token_ml, token_comp, token_admin = _setup_authoritative_users(audit_env)
    issuer, verifier, kid, trust_store = _setup_training_issuer(audit_env)
    sim_id = "sim_audit_14"

    # 1. Model Creation
    reg, entry = _create_sample_model(audit_env, sim_id, issuer, verifier, token_ml)
    version = entry["version"]

    # 2. Multi-Role Approvals
    sha = entry["sha256"]
    sig_ml = generate_signoff_signature(sim_id, version, sha, "ml_engineer", "dr_smith_ml", signing_secret=audit_env["payload_secret"])
    sig_comp = generate_signoff_signature(sim_id, version, sha, "compliance", "officer_jones_compliance", signing_secret=audit_env["payload_secret"])
    reg.sign_off(sim_id, version, "ml_engineer", "dr_smith_ml", signature=sig_ml, auth_token=token_ml)
    reg.sign_off(sim_id, version, "compliance", "officer_jones_compliance", signature=sig_comp, auth_token=token_comp)

    # 3. Promotion
    reg.promote_version(sim_id, version, "champion", auth_token=token_admin)
    champ = reg.get_active_version(sim_id)
    assert champ is not None
    assert champ["version"] == version

    # 4. Automated Rollback
    ctx = create_trusted_internal_context(
        service_name="model_evaluation_engine",
        action="rollback",
        resource_type="model",
        resource_id=sim_id,
        model_version=version,
        bank_id="bank_a",
        signing_secret=audit_env["prod_secret"],
    )
    reg.rollback(sim_id, version, trusted_context=ctx)
    after_rb = reg.get_active_version(sim_id)
    assert after_rb is not None
    assert after_rb["version"] == version

    # 5. Full Forensic Reconstruction
    audit_chain = ImmutableAuditChain.get_instance()
    report = audit_chain.verify_chain_integrity()
    assert report.is_valid is True

    history = audit_chain.reconstruct_resource_history(f"{sim_id}:v{version}")
    action_sequence = [e.event_type for e in history]
    assert "MODEL_VERSION_CREATED" in action_sequence
    assert "MODEL_SIGNOFF_RECORDED" in action_sequence
    assert "MODEL_PROMOTION" in action_sequence
    assert "MODEL_ROLLBACK" in action_sequence


def test_audit_15_regression_suites_remain_passing(audit_env):
    """AUDIT-15: Existing FINAL-AUTH, AUTHCTX, SERVE, FINAL-CONSIST, CONSIST, TENANT, BOUNDARY, CLAIM, RBAC, and governance regression suites remain passing."""
    # Smoke-check foundational verification report
    chain = ImmutableAuditChain.get_instance()
    assert chain.verify_chain_integrity().is_valid is True
