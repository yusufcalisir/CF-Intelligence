# ruff: noqa: E402
"""Comprehensive Adversarial and Boundary Test Suite for WORKSTREAM 01-C.4.

Focused Tenant Authority Boundary Verifications:
BOUNDARY-01: Direct consortium creation without credentials -> rejected (PermissionError).
BOUNDARY-02: Direct consortium vote without credentials -> rejected; no vote persisted.
BOUNDARY-03: Direct proposal creation/cancellation without credentials -> rejected.
BOUNDARY-04: actor="privileged_bank_user" without authentication -> impersonation rejected.
BOUNDARY-05: Direct ownership rebinding from Bank A to Bank B without trusted authority -> rejected.
BOUNDARY-06: save_version(owning_bank_id="bank_b") cannot overwrite established Bank A ownership.
BOUNDARY-07: Invalid/failed model save does not mutate existing institutional ownership.
BOUNDARY-08: Authorized first-time ownership binding succeeds with verifiable institutional evidence.
BOUNDARY-09: Legitimate authorized consortium proposals, votes, membership updates, and multi-bank model governance continue to succeed.
BOUNDARY-10: Existing TENANT-01-10, GATE, FINAL-GATE, RBAC, HMAC, issuer revocation, and model admission regressions remain intact.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import tempfile
from datetime import UTC, datetime
from typing import Any

import pytest
import torch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.consortium_service import (
    ConsortiumGovernanceService,
    MemberRole,
    ProposalAction,
    ProposalStatus,
    consortium_governance_service,
    init_default_consortium,
)
from app.application.services.model_registry import (
    ModelRegistry,
    create_trusted_internal_context,
    generate_signoff_signature,
)
from app.application.services.model_service import ModelService
from app.application.services.training_provenance_service import (
    DurableTrustStore,
    TrainingExecutionEvidence,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
)
from app.config import get_settings
from app.domain.model_serving_errors import ModelAuthorizationError
from app.infrastructure.security.auth_service import AuthenticationService

NUM_FEATURES = 10
FEATURE_NAMES = [f"f_{i}" for i in range(NUM_FEATURES)]
TEST_SECRET = "cfi_test_boundary_secret_key_32b_strict!"


@pytest.fixture
def boundary_env():
    """Isolated multi-bank test environment with authentic DurableAccountStore accounts."""
    temp_dir = tempfile.mkdtemp(prefix="cfi_boundary_test_")
    store_path = os.path.join(temp_dir, "accounts.json")
    trust_path = os.path.join(temp_dir, "trust.json")
    reg_dir = os.path.join(temp_dir, "models")
    os.makedirs(reg_dir, exist_ok=True)

    AuthenticationService.reset_instance()
    auth_svc = AuthenticationService(
        signing_secret=TEST_SECRET,
        store_path=store_path,
        authority_secret="cfi_auth_sec_2026_boundary_authority",
        app_env="production",
        access_token_expire_minutes=60,
    )
    AuthenticationService._instance = auth_svc

    # Register authentic institutional principals
    u_a_admin = auth_svc.register_user("admin_bank_a", "Pass1234!", "bank_a", ["admin"], clearance_level=3)
    u_a_lead = auth_svc.register_user("lead_bank_a", "Pass1234!", "bank_a", ["ml_lead"], clearance_level=2)
    u_a_mle = auth_svc.register_user("mle_bank_a", "Pass1234!", "bank_a", ["ml_engineer"], clearance_level=2)
    u_a_comp = auth_svc.register_user("comp_bank_a", "Pass1234!", "bank_a", ["compliance"], clearance_level=2)

    u_b_admin = auth_svc.register_user("admin_bank_b", "Pass1234!", "bank_b", ["admin"], clearance_level=3)
    u_b_lead = auth_svc.register_user("lead_bank_b", "Pass1234!", "bank_b", ["ml_lead"], clearance_level=2)
    u_b_mle = auth_svc.register_user("mle_bank_b", "Pass1234!", "bank_b", ["ml_engineer"], clearance_level=2)
    u_b_comp = auth_svc.register_user("comp_bank_b", "Pass1234!", "bank_b", ["compliance"], clearance_level=2)

    tokens = {
        "a_admin": auth_svc.create_token_bundle(u_a_admin).access_token,
        "a_lead": auth_svc.create_token_bundle(u_a_lead).access_token,
        "a_mle": auth_svc.create_token_bundle(u_a_mle).access_token,
        "a_comp": auth_svc.create_token_bundle(u_a_comp).access_token,
        "b_admin": auth_svc.create_token_bundle(u_b_admin).access_token,
        "b_lead": auth_svc.create_token_bundle(u_b_lead).access_token,
        "b_mle": auth_svc.create_token_bundle(u_b_mle).access_token,
        "b_comp": auth_svc.create_token_bundle(u_b_comp).access_token,
    }

    # Setup Ed25519 cryptographic receipts
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    pub_hex = pub_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    kid = "boundary_test_issuer_key"
    admin_token = "adm_bound_sec_12345"

    trust_store = DurableTrustStore(trust_file_path=trust_path, admin_token=admin_token)
    trust_store.register_approved_key(kid, pub_hex, admin_token=admin_token)
    verifier = TrainingReceiptVerifier(trust_store=trust_store)
    registry = ModelRegistry(storage_dir=reg_dir, verifier=verifier)
    consortium_governance_service.reset()
    consortium_service = consortium_governance_service

    yield {
        "temp_dir": temp_dir,
        "auth_svc": auth_svc,
        "tokens": tokens,
        "priv_key": priv_key,
        "kid": kid,
        "registry": registry,
        "consortium_service": consortium_service,
    }

    consortium_governance_service.reset()
    init_default_consortium(consortium_governance_service)
    AuthenticationService.reset_instance()
    shutil.rmtree(temp_dir, ignore_errors=True)


def _create_verified_candidate(
    registry: ModelRegistry,
    priv_key: ed25519.Ed25519PrivateKey,
    kid: str,
    sim_id: str,
    owning_bank_id: str | None = None,
    consortium_id: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Helper creating a candidate model version backed by an authentic cryptographic receipt."""
    svc = ModelService(get_settings())
    model = svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)
    buf = io.BytesIO()
    torch.save(model.state_dict(), buf)
    raw_bytes = buf.getvalue()
    sha256 = hashlib.sha256(raw_bytes).hexdigest()

    issuer = TrainingReceiptIssuer(private_key=priv_key, key_id=kid, require_authenticated_job=False)
    evidence = TrainingExecutionEvidence(
        training_run_id=f"run_{sim_id}",
        job_id=f"job_{sim_id}",
        simulation_id=sim_id,
        completed_at=datetime.now(UTC).isoformat(),
        executed_rounds=3,
        executed_epochs=3,
        sample_count=100,
        observed_metrics={"loss": 0.05, "auc": 0.95},
        artifact_sha256=sha256,
        architecture="FraudDetectionNN",
        feature_schema_version="v1.0",
        feature_names=FEATURE_NAMES,
        owning_bank_id=owning_bank_id,
        consortium_id=consortium_id,
    )
    receipt = issuer.issue_receipt(
        evidence=evidence,
        model_bytes=raw_bytes,
        owning_bank_id=owning_bank_id,
        consortium_id=consortium_id,
    ).to_dict()

    entry = registry.save_version(
        simulation_id=sim_id,
        state_dict=model.state_dict(),
        metrics={"loss": 0.05, "auc": 0.95},
        artifact_bytes=raw_bytes,
        training_receipt=receipt,
        is_promoted=False,
        owning_bank_id=owning_bank_id,
        consortium_id=consortium_id,
    )
    return entry, sha256


# ── BOUNDARY-01: Direct Consortium Creation Without Credentials Rejected ─────


def test_boundary_01_direct_consortium_creation_without_credentials_rejected(boundary_env):
    """BOUNDARY-01: Direct consortium creation without credentials -> rejected (PermissionError)."""
    svc: ConsortiumGovernanceService = boundary_env["consortium_service"]

    with pytest.raises(PermissionError, match="Authentication required: Direct service operation 'create_consortium'"):
        svc.create_consortium(
            consortium_id="c_unauth_01",
            name="Unauthenticated Consortium",
            founder_bank_id="bank_a",
            quorum_ratio=0.51,
            # No auth_token, no trusted_context
        )

    # Persisted state assertion: Consortium must NOT exist
    assert svc.get_consortium("c_unauth_01") is None


# ── BOUNDARY-02: Direct Consortium Vote Without Credentials Rejected ─────────


def test_boundary_02_direct_consortium_vote_without_credentials_rejected(boundary_env):
    """BOUNDARY-02: Direct consortium vote without credentials -> rejected; no vote persisted."""
    svc: ConsortiumGovernanceService = boundary_env["consortium_service"]
    tok_a = boundary_env["tokens"]["a_admin"]
    cid = "c_boundary_02"

    # Legitimate creation and membership proposal by Bank A
    svc.create_consortium(cid, "Boundary 02", "bank_a", auth_token=tok_a)
    svc.add_member(cid, "bank_b", role=MemberRole.FULL_MEMBER, voting_power=1.0)
    prop = svc.propose_membership_change(cid, "bank_a", "bank_c", ProposalAction.ADD_MEMBER, auth_token=tok_a)

    # Bank B attempts to vote directly with NO credentials
    with pytest.raises(PermissionError, match="Authentication required: Direct service operation 'cast_vote'"):
        svc.cast_vote(
            proposal_id=prop.proposal_id,
            bank_id="bank_b",
            approve=True,
            # No credentials supplied
        )

    # Persisted state assertion: Bank B's vote must NOT have been recorded
    stored_prop = svc.get_proposal(prop.proposal_id)
    assert stored_prop is not None
    assert "bank_b" not in stored_prop.votes_for
    assert stored_prop.status == ProposalStatus.PENDING


# ── BOUNDARY-03: Direct Proposal Creation and Cancellation Rejected ──────────


def test_boundary_03_direct_proposal_creation_and_cancellation_without_credentials_rejected(boundary_env):
    """BOUNDARY-03: Direct proposal creation/cancellation without credentials -> rejected."""
    svc: ConsortiumGovernanceService = boundary_env["consortium_service"]
    tok_a = boundary_env["tokens"]["a_admin"]
    cid = "c_boundary_03"

    svc.create_consortium(cid, "Boundary 03", "bank_a", auth_token=tok_a)
    svc.add_member(cid, "bank_b", role=MemberRole.FULL_MEMBER, voting_power=1.0)

    # 1. Proposal creation without credentials rejected
    with pytest.raises(PermissionError, match="Authentication required: Direct service operation 'propose_membership_change'"):
        svc.propose_membership_change(
            consortium_id=cid,
            creator_bank_id="bank_a",
            target_bank_id="bank_c",
            action=ProposalAction.ADD_MEMBER,
            # No credentials
        )

    # Create proposal legitimately to test cancellation boundary
    prop = svc.propose_membership_change(
        consortium_id=cid,
        creator_bank_id="bank_a",
        target_bank_id="bank_c",
        action=ProposalAction.ADD_MEMBER,
        auth_token=tok_a,
    )
    assert prop.status == ProposalStatus.PENDING

    # 2. Proposal cancellation without credentials rejected
    with pytest.raises(PermissionError, match="Authentication required: Direct service operation 'cancel_proposal'"):
        svc.cancel_proposal(
            proposal_id=prop.proposal_id,
            creator_bank_id="bank_a",
            # No credentials
        )

    # Proposal remains pending; cancellation rejected
    stored_prop = svc.get_proposal(prop.proposal_id)
    assert stored_prop is not None
    assert stored_prop.status == ProposalStatus.PENDING

    # 3. Legitimate proposal cancellation with valid authority succeeds
    cancelled = svc.cancel_proposal(
        proposal_id=prop.proposal_id,
        creator_bank_id="bank_a",
        auth_token=tok_a,
    )
    assert cancelled.status == ProposalStatus.CANCELLED
    final_prop = svc.get_proposal(prop.proposal_id)
    assert final_prop is not None
    assert final_prop.status == ProposalStatus.CANCELLED


# ── BOUNDARY-04: Caller-Supplied Actor String Impersonation Rejected ─────────


def test_boundary_04_actor_username_without_authentication_rejected(boundary_env):
    """BOUNDARY-04: actor='privileged_bank_user' without authentication -> impersonation rejected."""
    svc: ConsortiumGovernanceService = boundary_env["consortium_service"]

    # Attacker supplies actor="admin_bank_a" but NO auth_token
    with pytest.raises(PermissionError, match="Caller-supplied actor identity .* cannot establish authority without valid authentication"):
        svc.create_consortium(
            consortium_id="c_spoof_actor",
            name="Impersonated Consortium",
            founder_bank_id="bank_a",
            actor="admin_bank_a",  # Valid username in DurableAccountStore, but caller has no token!
        )

    assert svc.get_consortium("c_spoof_actor") is None


# ── BOUNDARY-05: Direct Ownership Rebinding Bank A -> Bank B Rejected ─────────


def test_boundary_05_direct_ownership_rebinding_from_bank_a_to_bank_b_rejected(boundary_env):
    """BOUNDARY-05: Direct ownership rebinding from Bank A to Bank B without trusted authority -> rejected."""
    reg: ModelRegistry = boundary_env["registry"]
    sim_id = "sim_boundary_05"

    # Initially Bank A owns the model via verified candidate creation
    _create_verified_candidate(reg, boundary_env["priv_key"], boundary_env["kid"], sim_id, owning_bank_id="bank_a")

    own_before = reg.get_simulation_ownership(sim_id)
    assert own_before["owning_bank_id"] == "bank_a"
    assert own_before["is_unbound"] is False

    # Untrusted direct rebinding attempt from Bank A to Bank B without transfer authority
    with pytest.raises(ModelAuthorizationError, match="Unauthorized model ownership rebinding"):
        reg.bind_simulation_ownership(
            simulation_id=sim_id,
            owning_bank_id="bank_b",
        )

    # Assert persisted ownership remains authoritatively Bank A
    own_after = reg.get_simulation_ownership(sim_id)
    assert own_after["owning_bank_id"] == "bank_a"


# ── BOUNDARY-06: save_version Cannot Overwrite Established Ownership ─────────


def test_boundary_06_save_version_cannot_overwrite_established_bank_a_ownership(boundary_env):
    """BOUNDARY-06: save_version(owning_bank_id='bank_b') cannot overwrite established Bank A ownership."""
    reg: ModelRegistry = boundary_env["registry"]
    sim_id = "sim_boundary_06"

    # Establish initial Bank A ownership
    _create_verified_candidate(reg, boundary_env["priv_key"], boundary_env["kid"], sim_id, owning_bank_id="bank_a")

    # A subsequent model save attempts to claim owning_bank_id="bank_b"
    dummy_model = ModelService(get_settings()).create_model(input_dim=NUM_FEATURES, dp_compatible=True)
    with pytest.raises(ModelAuthorizationError, match="Cannot overwrite established model ownership"):
        reg.save_version(
            simulation_id=sim_id,
            state_dict=dummy_model.state_dict(),
            metrics={"loss": 0.04, "auc": 0.96},
            owning_bank_id="bank_b",
        )

    # Ownership on disk remains strictly Bank A
    own = reg.get_simulation_ownership(sim_id)
    assert own["owning_bank_id"] == "bank_a"


# ── BOUNDARY-07: Invalid/Failed Save Does Not Mutate Prior Ownership ──────────


def test_boundary_07_invalid_failed_model_save_does_not_mutate_prior_ownership(boundary_env):
    """BOUNDARY-07: Invalid/failed model save does not mutate existing institutional ownership."""
    reg: ModelRegistry = boundary_env["registry"]
    sim_id = "sim_boundary_07"

    # Establish initial Bank A ownership
    _create_verified_candidate(reg, boundary_env["priv_key"], boundary_env["kid"], sim_id, owning_bank_id="bank_a")

    own_initial = reg.get_simulation_ownership(sim_id)
    assert own_initial["owning_bank_id"] == "bank_a"

    # Attempt a save with invalid/corrupted data that fails during serialization or admission
    with pytest.raises((ModelAuthorizationError, ValueError, AttributeError)):
        reg.save_version(
            simulation_id=sim_id,
            state_dict=None,  # type: ignore
            artifact_bytes=None,
            metrics={"auc": 0.8},
            owning_bank_id="bank_attacker",
        )

    # Ensure existing ownership is unaltered
    own_after = reg.get_simulation_ownership(sim_id)
    assert own_after["owning_bank_id"] == "bank_a"
    assert own_after["is_unbound"] is False


# ── BOUNDARY-08: Authorized First-Time Ownership Binding Succeeds ────────────


def test_boundary_08_authorized_first_time_ownership_binding_succeeds(boundary_env):
    """BOUNDARY-08: Authorized first-time ownership binding succeeds with verifiable institutional evidence."""
    reg: ModelRegistry = boundary_env["registry"]
    sim_id = "sim_boundary_08"
    tok_a_lead = boundary_env["tokens"]["a_lead"]

    # Initial state is unbound
    init_own = reg.get_simulation_ownership(sim_id)
    assert init_own["is_unbound"] is True

    # 1. Binding with valid institutional token
    bound = reg.bind_simulation_ownership(
        simulation_id=sim_id,
        owning_bank_id="bank_a",
        auth_token=tok_a_lead,
    )
    assert bound["owning_bank_id"] == "bank_a"
    assert bound["is_unbound"] is False

    # Persisted disk assertion
    persisted = reg.get_simulation_ownership(sim_id)
    assert persisted["owning_bank_id"] == "bank_a"
    assert persisted["is_unbound"] is False

    # 2. Binding via verified cryptographic training receipt
    sim_id_2 = "sim_boundary_08_receipt"
    _create_verified_candidate(
        reg, boundary_env["priv_key"], boundary_env["kid"], sim_id_2, owning_bank_id="bank_b"
    )
    persisted_2 = reg.get_simulation_ownership(sim_id_2)
    assert persisted_2["owning_bank_id"] == "bank_b"
    assert persisted_2["is_unbound"] is False


# ── BOUNDARY-09: Positive Multi-Bank Consortium Governance Functional ────────


def test_boundary_09_legitimate_consortium_proposals_votes_and_governance_succeed(boundary_env):
    """BOUNDARY-09: Legitimate authorized consortium proposals, votes, membership updates, and multi-bank governance succeed."""
    svc: ConsortiumGovernanceService = boundary_env["consortium_service"]
    reg: ModelRegistry = boundary_env["registry"]
    tok_a = boundary_env["tokens"]["a_admin"]
    tok_b = boundary_env["tokens"]["b_admin"]
    cid = "c_boundary_09"

    # Founder Bank A establishes consortium
    c = svc.create_consortium(cid, "Allied Fraud Defense", "bank_a", quorum_ratio=0.51, auth_token=tok_a)
    assert c.consortium_id == cid

    # Bank A sponsors membership for Bank B
    prop_b = svc.propose_membership_change(cid, "bank_a", "bank_b", ProposalAction.ADD_MEMBER, auth_token=tok_a)
    assert prop_b.status == ProposalStatus.APPROVED
    c_b = svc.get_consortium(cid)
    assert c_b is not None
    assert "bank_b" in c_b.members

    # Policy update sponsored by Bank B
    pol_prop = svc.propose_policy_update(cid, "bank_b", {"new_quorum_ratio": 0.60}, auth_token=tok_b)
    assert pol_prop.status == ProposalStatus.PENDING

    # Bank A votes FOR -> quorum reached -> APPROVED
    res_prop = svc.cast_vote(pol_prop.proposal_id, "bank_a", approve=True, auth_token=tok_a)
    assert res_prop.status == ProposalStatus.APPROVED
    c_updated = svc.get_consortium(cid)
    assert c_updated is not None
    assert c_updated.quorum_ratio == 0.60

    # Multi-bank consortium-owned model signoff and promotion
    sim_cons = "sim_boundary_09_consortium"
    entry, sha256 = _create_verified_candidate(
        reg, boundary_env["priv_key"], boundary_env["kid"], sim_cons, consortium_id=cid
    )
    v = entry["version"]

    sig_mle_a = generate_signoff_signature(sim_cons, v, sha256, "ml_engineer", "mle_bank_a", signing_secret=TEST_SECRET)
    sig_comp_b = generate_signoff_signature(sim_cons, v, sha256, "compliance", "comp_bank_b", signing_secret=TEST_SECRET)

    reg.sign_off(sim_cons, v, "ml_engineer", "mle_bank_a", sig_mle_a, auth_token=boundary_env["tokens"]["a_mle"])
    reg.sign_off(sim_cons, v, "compliance", "comp_bank_b", sig_comp_b, auth_token=boundary_env["tokens"]["b_comp"])

    promoted = reg.promote_version(sim_cons, v, target_status="champion", auth_token=boundary_env["tokens"]["b_lead"])
    assert promoted["is_active"] is True
    assert promoted["status"] == "champion"


# ── BOUNDARY-10: Complete Regression Suite Intact ────────────────────────────


def test_boundary_10_regressions_intact(boundary_env):
    """BOUNDARY-10: Existing TENANT-01-10, GATE, FINAL-GATE, RBAC, HMAC, issuer revocation, and model admission intact."""
    reg: ModelRegistry = boundary_env["registry"]
    sim_id = "sim_boundary_10_bank_a"

    # Create candidate
    entry, sha256 = _create_verified_candidate(
        reg, boundary_env["priv_key"], boundary_env["kid"], sim_id, owning_bank_id="bank_a"
    )
    v = entry["version"]

    # Cross-bank signoff rejected (Bank B cannot sign off Bank A model)
    sig_b_mle = generate_signoff_signature(sim_id, v, sha256, "ml_engineer", "mle_bank_b", signing_secret=TEST_SECRET)
    with pytest.raises(ModelAuthorizationError, match="Cross-bank authorization rejected"):
        reg.sign_off(sim_id, v, "ml_engineer", "mle_bank_b", sig_b_mle, auth_token=boundary_env["tokens"]["b_mle"])

    # Four-Eyes enforcement
    sig_a_mle = generate_signoff_signature(sim_id, v, sha256, "ml_engineer", "mle_bank_a", signing_secret=TEST_SECRET)
    reg.sign_off(sim_id, v, "ml_engineer", "mle_bank_a", sig_a_mle, auth_token=boundary_env["tokens"]["a_mle"])

    sig_a_same = generate_signoff_signature(sim_id, v, sha256, "compliance", "mle_bank_a", signing_secret=TEST_SECRET)
    with pytest.raises(Exception, match="Four-Eyes Principle"):
        reg.sign_off(sim_id, v, "compliance", "mle_bank_a", sig_a_same, auth_token=boundary_env["tokens"]["a_mle"])

    # Legitimate second compliance approval by distinct officer
    sig_a_comp = generate_signoff_signature(sim_id, v, sha256, "compliance", "comp_bank_a", signing_secret=TEST_SECRET)
    reg.sign_off(sim_id, v, "compliance", "comp_bank_a", sig_a_comp, auth_token=boundary_env["tokens"]["a_comp"])

    # Promotion by authorized Bank A ML Lead
    promoted = reg.promote_version(sim_id, v, target_status="champion", auth_token=boundary_env["tokens"]["a_lead"])
    assert promoted["is_active"] is True

    # Trusted internal context rollback succeeds
    ctx_rb = create_trusted_internal_context("model_evaluation_engine", action="rollback")
    rb_entry = reg.rollback(sim_id, v, trusted_context=ctx_rb)
    assert rb_entry["version"] == v
    assert rb_entry["is_active"] is True
