"""Comprehensive adversarial and regression test suite for WORKSTREAM 01-C.4.

Tenant Isolation & Consortium Governance Authorization:
TENANT-01: Bank B attempts Bank A model sign-off → rejected (HTTP 403 / ModelAuthorizationError).
TENANT-02: Bank B attempts Bank A promotion or rollback → rejected (HTTP 403 / ModelAuthorizationError).
TENANT-03: Forged creator_bank_id, founder_bank_id, or bank_id → rejected (HTTP 403 / PermissionError).
TENANT-04: Bank B attempts to cast Bank A's consortium vote → rejected (HTTP 403 / PermissionError).
TENANT-05: Non-member or removed member attempts governance action → rejected (HTTP 403 / PermissionError).
TENANT-06: Unauthorized consortium policy or membership change → rejected (HTTP 403 / PermissionError).
TENANT-07: Authorized same-bank model governance succeeds.
TENANT-08: Authorized multi-bank consortium proposal, independent votes, and quorum resolution succeed.
TENANT-09: Unbound or ambiguous model ownership fails closed without fabricating institutional ownership.
TENANT-10: Existing authentication, live RBAC, clearance, Four-Eyes, model admission, and authorized internal orchestration regressions remain intact.
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
from fastapi.testclient import TestClient

from app.application.services.consortium_service import (
    consortium_governance_service,
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
from app.domain.consortium_governance import MemberRole, ProposalAction, ProposalStatus
from app.domain.model_serving_errors import ModelAuthorizationError, ModelIntegrityError
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore
from app.main import app

NUM_FEATURES = 10
FEATURE_NAMES = [f"f_{i}" for i in range(NUM_FEATURES)]
TEST_SIGNING_SECRET = "cfi_test_tenant_isolation_secret_key_32b!"


@pytest.fixture
def tenant_env():
    """Isolated test environment with multi-bank principals and fresh storage."""
    temp_dir = tempfile.mkdtemp(prefix="cfi_tenant_test_")
    store_path = os.path.join(temp_dir, "accounts.json")
    trust_path = os.path.join(temp_dir, "trust.json")
    reg_dir = os.path.join(temp_dir, "models")
    os.makedirs(reg_dir, exist_ok=True)

    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    pub_hex = pub_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    kid = "issuer_tenant_key"
    admin_token = "adm_tenant_sec_12345"

    trust_store = DurableTrustStore(trust_file_path=trust_path, admin_token=admin_token)
    trust_store.register_approved_key(kid, pub_hex, admin_token=admin_token)
    verifier = TrainingReceiptVerifier(trust_store=trust_store)

    registry = ModelRegistry(storage_dir=reg_dir, verifier=verifier)

    consortium_store_path = os.path.join(temp_dir, "consortium_store.json")
    test_consortium_store = DurableConsortiumStore(
        store_path=consortium_store_path,
        authority_secret="cfi_test_consortium_store_secret_32b_key!",
        app_env="production",
    )
    old_consortium_store = DurableConsortiumStore._instance
    old_service_store = consortium_governance_service._store
    DurableConsortiumStore.set_instance(test_consortium_store)
    consortium_governance_service._store = test_consortium_store

    from app.infrastructure.security.durable_nonce_store import reset_default_nonce_store

    settings = get_settings()
    nonce_path = os.path.join(temp_dir, "nonces.json")
    old_nonce_path = settings.internal_nonce_store_path
    old_internal_secret = settings.internal_context_signing_secret
    settings.internal_nonce_store_path = nonce_path
    settings.internal_context_signing_secret = TEST_SIGNING_SECRET
    reset_default_nonce_store()

    auth_svc = AuthenticationService(
        signing_secret=TEST_SIGNING_SECRET,
        store_path=store_path,
        authority_secret="authoritative_master_secret_64_bytes_test_suite_tenant",
        app_env="production",
        access_token_expire_minutes=60,
    )
    AuthenticationService._instance = auth_svc

    # Register Bank A users
    u_a_mle = auth_svc.register_user("mle_bank_a", "Pass123!Secure", "bank_a", roles=["ml_engineer"], clearance_level=2)
    u_a_comp = auth_svc.register_user("comp_bank_a", "Pass123!Secure", "bank_a", roles=["compliance"], clearance_level=3)
    u_a_lead = auth_svc.register_user("lead_bank_a", "Pass123!Secure", "bank_a", roles=["ml_lead"], clearance_level=3)
    u_a_admin = auth_svc.register_user("admin_bank_a", "Pass123!Secure", "bank_a", roles=["admin"], clearance_level=3)

    # Register Bank B users
    u_b_mle = auth_svc.register_user("mle_bank_b", "Pass123!Secure", "bank_b", roles=["ml_engineer"], clearance_level=2)
    u_b_comp = auth_svc.register_user("comp_bank_b", "Pass123!Secure", "bank_b", roles=["compliance"], clearance_level=3)
    u_b_lead = auth_svc.register_user("lead_bank_b", "Pass123!Secure", "bank_b", roles=["ml_lead"], clearance_level=3)
    u_b_admin = auth_svc.register_user("admin_bank_b", "Pass123!Secure", "bank_b", roles=["admin"], clearance_level=3)

    # Register Bank C (Observer / Non-member)
    u_c_mle = auth_svc.register_user("mle_bank_c", "Pass123!Secure", "bank_c", roles=["ml_engineer"], clearance_level=2)

    tokens = {
        "a_mle": auth_svc.create_token_bundle(u_a_mle).access_token,
        "a_comp": auth_svc.create_token_bundle(u_a_comp).access_token,
        "a_lead": auth_svc.create_token_bundle(u_a_lead).access_token,
        "a_admin": auth_svc.create_token_bundle(u_a_admin).access_token,
        "b_mle": auth_svc.create_token_bundle(u_b_mle).access_token,
        "b_comp": auth_svc.create_token_bundle(u_b_comp).access_token,
        "b_lead": auth_svc.create_token_bundle(u_b_lead).access_token,
        "b_admin": auth_svc.create_token_bundle(u_b_admin).access_token,
        "c_mle": auth_svc.create_token_bundle(u_c_mle).access_token,
    }

    from app.presentation.routers import model_registry as mr_router

    old_reg = mr_router.registry
    mr_router.registry = registry

    client = TestClient(app)

    yield {
        "temp_dir": temp_dir,
        "registry": registry,
        "auth_svc": auth_svc,
        "priv_key": priv_key,
        "kid": kid,
        "tokens": tokens,
        "client": client,
        "users": {
            "a_mle": u_a_mle,
            "a_comp": u_a_comp,
            "a_lead": u_a_lead,
            "a_admin": u_a_admin,
            "b_mle": u_b_mle,
            "b_comp": u_b_comp,
            "b_lead": u_b_lead,
            "b_admin": u_b_admin,
            "c_mle": u_c_mle,
        },
    }

    mr_router.registry = old_reg
    AuthenticationService.reset_instance()
    DurableConsortiumStore.set_instance(old_consortium_store)
    consortium_governance_service._store = old_service_store
    settings.internal_nonce_store_path = old_nonce_path
    settings.internal_context_signing_secret = old_internal_secret
    reset_default_nonce_store()
    shutil.rmtree(temp_dir, ignore_errors=True)


def _create_candidate(
    registry: ModelRegistry,
    priv_key: ed25519.Ed25519PrivateKey,
    kid: str,
    sim_id: str,
    owning_bank_id: str | None = None,
    consortium_id: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Helper creating a genuine candidate model."""
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
        sample_count=150,
        observed_metrics={"loss": 0.03, "auc": 0.96},
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
        metrics={"loss": 0.03, "auc": 0.96},
        artifact_bytes=raw_bytes,
        training_receipt=receipt,
        is_promoted=False,
        owning_bank_id=owning_bank_id,
        consortium_id=consortium_id,
    )
    return entry, sha256


# ── TENANT-01: Cross-Bank Model Sign-off Rejected ──────────────────────────


def test_tenant_01_bank_b_attempts_bank_a_model_signoff_rejected(tenant_env):
    """TENANT-01: Bank B approver attempts to sign off on Bank A's model → rejected (HTTP 403 / ModelAuthorizationError)."""
    reg = tenant_env["registry"]
    client = tenant_env["client"]
    sim_id = "sim_tenant_01_bank_a"
    entry, sha256 = _create_candidate(reg, tenant_env["priv_key"], tenant_env["kid"], sim_id, owning_bank_id="bank_a")
    v = entry["version"]

    # 1. REST Endpoint: Bank B ML engineer attempts sign-off on Bank A model
    sig_b = generate_signoff_signature(sim_id, v, sha256, "ml_engineer", "mle_bank_b", signing_secret=TEST_SIGNING_SECRET)
    resp = client.post(
        f"/api/v1/registry/{sim_id}/versions/{v}/signoff",
        headers={"Authorization": f"Bearer {tenant_env['tokens']['b_mle']}"},
        json={"role": "ml_engineer", "user": "mle_bank_b", "signature": sig_b},
    )
    assert resp.status_code == 403
    assert "Cross-bank authorization rejected" in resp.json()["detail"]
    assert "bank_b" in resp.json()["detail"]
    assert "bank_a" in resp.json()["detail"]

    # Negative test: Verify zero unauthorized state mutation
    stored_meta = reg.get_version_metadata(sim_id, v)
    assert len(stored_meta["sign_offs"]) == 0

    # 2. Direct Service Call: Bank B approver calls reg.sign_off directly
    with pytest.raises(ModelAuthorizationError, match="Cross-bank authorization rejected"):
        reg.sign_off(
            simulation_id=sim_id,
            version=v,
            role="ml_engineer",
            user="mle_bank_b",
            signature=sig_b,
            auth_token=tenant_env["tokens"]["b_mle"],
        )

    # Persisted manifest still completely unchanged
    assert len(reg.get_version_metadata(sim_id, v)["sign_offs"]) == 0


# ── TENANT-02: Cross-Bank Promotion and Rollback Rejected ───────────────────


def test_tenant_02_bank_b_attempts_bank_a_promotion_or_rollback_rejected(tenant_env):
    """TENANT-02: Bank B attempts promotion or rollback of Bank A's model → rejected (HTTP 403 / ModelAuthorizationError)."""
    reg = tenant_env["registry"]
    client = tenant_env["client"]
    sim_id = "sim_tenant_02_bank_a"

    # Candidate 1
    e1, sha1 = _create_candidate(reg, tenant_env["priv_key"], tenant_env["kid"], sim_id, owning_bank_id="bank_a")
    v1 = e1["version"]
    # Add legitimate Bank A dual approvals
    sig_a_mle = generate_signoff_signature(sim_id, v1, sha1, "ml_engineer", "mle_bank_a", signing_secret=TEST_SIGNING_SECRET)
    sig_a_comp = generate_signoff_signature(sim_id, v1, sha1, "compliance", "comp_bank_a", signing_secret=TEST_SIGNING_SECRET)
    reg.sign_off(sim_id, v1, "ml_engineer", "mle_bank_a", sig_a_mle, auth_token=tenant_env["tokens"]["a_mle"])
    reg.sign_off(sim_id, v1, "compliance", "comp_bank_a", sig_a_comp, auth_token=tenant_env["tokens"]["a_comp"])

    # Legitimate promotion by Bank A ML lead
    reg.promote_version(sim_id, v1, target_status="champion", auth_token=tenant_env["tokens"]["a_lead"])

    # Candidate 2
    e2, sha2 = _create_candidate(reg, tenant_env["priv_key"], tenant_env["kid"], sim_id, owning_bank_id="bank_a")
    v2 = e2["version"]
    sig2_mle = generate_signoff_signature(sim_id, v2, sha2, "ml_engineer", "mle_bank_a", signing_secret=TEST_SIGNING_SECRET)
    sig2_comp = generate_signoff_signature(sim_id, v2, sha2, "compliance", "comp_bank_a", signing_secret=TEST_SIGNING_SECRET)
    reg.sign_off(sim_id, v2, "ml_engineer", "mle_bank_a", sig2_mle, auth_token=tenant_env["tokens"]["a_mle"])
    reg.sign_off(sim_id, v2, "compliance", "comp_bank_a", sig2_comp, auth_token=tenant_env["tokens"]["a_comp"])

    # 1. REST Promotion: Bank B privileged lead attempts to promote Bank A candidate v2
    resp_promo = client.post(
        f"/api/v1/registry/{sim_id}/versions/{v2}/promote",
        headers={"Authorization": f"Bearer {tenant_env['tokens']['b_lead']}"},
        json={"target_status": "champion", "enforce_sr11_7": False},
    )
    assert resp_promo.status_code == 403
    assert "Cross-bank authorization rejected" in resp_promo.json()["detail"]

    # Champion must still remain v1
    active = reg.get_active_version(sim_id)
    assert active["version"] == v1

    # 2. Direct Service Promotion: Bank B attempts promote_version
    with pytest.raises(ModelAuthorizationError, match="Cross-bank authorization rejected"):
        reg.promote_version(sim_id, v2, target_status="champion", auth_token=tenant_env["tokens"]["b_lead"])

    # 3. REST Rollback: Bank B attempts rollback of Bank A model
    resp_rb = client.post(
        f"/api/v1/registry/{sim_id}/rollback/{v1}",
        headers={"Authorization": f"Bearer {tenant_env['tokens']['b_comp']}"},
    )
    assert resp_rb.status_code == 403
    assert "Cross-bank authorization rejected" in resp_rb.json()["detail"]

    # 4. Direct Service Rollback: Bank B attempts rollback directly
    with pytest.raises(ModelAuthorizationError, match="Cross-bank authorization rejected"):
        reg.rollback(sim_id, v1, auth_token=tenant_env["tokens"]["b_comp"])

    # Active model state unchanged
    active_now = reg.get_active_version(sim_id)
    assert active_now is not None
    assert active_now["version"] == v1


# ── TENANT-03: Forged Identifiers Rejected ──────────────────────────────────


def test_tenant_03_forged_identifiers_rejected(tenant_env):
    """TENANT-03: Forged creator_bank_id, founder_bank_id, or bank_id → rejected (HTTP 403 / PermissionError)."""
    client = tenant_env["client"]
    token_a = tenant_env["tokens"]["a_admin"]

    # 1. Forged founder_bank_id on consortium creation
    resp_create = client.post(
        "/api/v1/coordinator/consortium",
        headers={"Authorization": f"Bearer {token_a}"},
        json={
            "consortium_id": "c_spoof_01",
            "name": "Spoofed Consortium",
            "founder_bank_id": "bank_b",  # Token is Bank A, body claims Bank B
            "quorum_ratio": 0.51,
        },
    )
    assert resp_create.status_code == 403
    assert "Cross-bank institutional impersonation rejected" in resp_create.json()["detail"]

    # Legitimate consortium creation with Bank A
    c_ok = client.post(
        "/api/v1/coordinator/consortium",
        headers={"Authorization": f"Bearer {token_a}"},
        json={
            "consortium_id": "c_legit_03",
            "name": "Legit Consortium 03",
            "founder_bank_id": "bank_a",
            "quorum_ratio": 0.51,
        },
    )
    assert c_ok.status_code == 201

    # 2. Forged creator_bank_id on membership proposal
    resp_prop = client.post(
        "/api/v1/coordinator/proposals/membership",
        headers={"Authorization": f"Bearer {token_a}"},
        json={
            "consortium_id": "c_legit_03",
            "creator_bank_id": "bank_b",  # Forged creator
            "target_bank_id": "bank_c",
            "action": "ADD_MEMBER",
        },
    )
    assert resp_prop.status_code == 403
    assert "Cross-bank institutional impersonation rejected" in resp_prop.json()["detail"]

    # 3. Forged creator_bank_id on policy proposal
    resp_pol = client.post(
        "/api/v1/coordinator/proposals/policy",
        headers={"Authorization": f"Bearer {token_a}"},
        json={
            "consortium_id": "c_legit_03",
            "creator_bank_id": "bank_b",  # Forged creator
            "policy_updates": {"new_quorum_ratio": 0.65},
        },
    )
    assert resp_pol.status_code == 403
    assert "Cross-bank institutional impersonation rejected" in resp_pol.json()["detail"]

    # 4. Direct Service: Forged founder with auth_token raises PermissionError
    with pytest.raises(PermissionError, match="Cross-bank direct service operation rejected"):
        consortium_governance_service.create_consortium(
            consortium_id="c_direct_forged",
            name="Direct Forged",
            founder_bank_id="bank_b",
            auth_token=token_a,
        )


# ── TENANT-04: Bank B Cannot Cast Bank A's Vote ────────────────────────────


def test_tenant_04_bank_b_attempts_to_cast_bank_a_vote_rejected(tenant_env):
    """TENANT-04: Bank B attempts to cast Bank A's consortium vote → rejected (HTTP 403)."""
    from app.domain.consortium_governance import ConsortiumMember

    client = tenant_env["client"]
    token_a = tenant_env["tokens"]["a_admin"]
    token_b = tenant_env["tokens"]["b_admin"]

    cid = "c_tenant_04"
    # Create consortium with founder Bank A
    consortium_governance_service.create_consortium(
        consortium_id=cid,
        name="Consortium 04",
        founder_bank_id="bank_a",
        auth_token=token_a,
    )
    # Add Bank B as member
    c = consortium_governance_service.get_consortium(cid)
    assert c is not None
    c.members["bank_b"] = ConsortiumMember(bank_id="bank_b", role=MemberRole.FULL_MEMBER, voting_power=1.0)

    # Bank A creates membership proposal
    prop = consortium_governance_service.propose_membership_change(
        consortium_id=cid,
        creator_bank_id="bank_a",
        target_bank_id="bank_c",
        action=ProposalAction.ADD_MEMBER,
        auth_token=token_a,
    )

    # Bank B attempts to cast a vote stating bank_id="bank_a"
    resp_spoof = client.post(
        f"/api/v1/coordinator/proposals/{prop.proposal_id}/vote",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"bank_id": "bank_a", "approve": True},
    )
    assert resp_spoof.status_code == 403
    assert "Cross-bank voting rejected" in resp_spoof.json()["detail"]

    # Negative test: Bank B's vote was NOT cast
    stored_prop = consortium_governance_service.get_proposal(prop.proposal_id)
    assert stored_prop is not None
    assert "bank_b" not in stored_prop.votes_for

    # Direct service call also fails
    with pytest.raises(PermissionError, match="Cross-bank direct service operation rejected"):
        consortium_governance_service.cast_vote(
            proposal_id=prop.proposal_id,
            bank_id="bank_a",
            approve=True,
            auth_token=token_b,
        )


# ── TENANT-05: Non-Member or Removed Member Rejected ────────────────────────


def test_tenant_05_non_member_or_removed_member_rejected(tenant_env):
    """TENANT-05: Non-member or removed member attempts governance action → rejected (HTTP 403)."""
    client = tenant_env["client"]
    token_a = tenant_env["tokens"]["a_admin"]
    token_b = tenant_env["tokens"]["b_admin"]
    token_c = tenant_env["tokens"]["c_mle"]

    cid = "c_tenant_05"
    consortium_governance_service.create_consortium(
        consortium_id=cid,
        name="Consortium 05",
        founder_bank_id="bank_a",
        auth_token=token_a,
    )

    # 1. Non-member Bank C attempts to propose membership change
    resp_non_member = client.post(
        "/api/v1/coordinator/proposals/membership",
        headers={"Authorization": f"Bearer {token_c}"},
        json={
            "consortium_id": cid,
            "creator_bank_id": "bank_c",
            "target_bank_id": "bank_d",
            "action": "ADD_MEMBER",
        },
    )
    assert resp_non_member.status_code == 403
    assert "not an active member" in resp_non_member.json()["detail"]

    # 2. Add Bank B, then evict Bank B
    from app.domain.consortium_governance import ConsortiumMember

    c = consortium_governance_service.get_consortium(cid)
    assert c is not None
    c.members["bank_b"] = ConsortiumMember(bank_id="bank_b", role=MemberRole.FULL_MEMBER, voting_power=1.0)
    assert "bank_b" in c.members
    c.members.pop("bank_b", None)
    assert "bank_b" not in c.members

    # Bank A creates a proposal
    prop = consortium_governance_service.propose_policy_update(
        consortium_id=cid,
        creator_bank_id="bank_a",
        policy_updates={"new_max_epsilon": 4.0},
        auth_token=token_a,
    )

    # Evicted Bank B attempts to vote on proposal
    resp_evicted_vote = client.post(
        f"/api/v1/coordinator/proposals/{prop.proposal_id}/vote",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"bank_id": "bank_b", "approve": True},
    )
    assert resp_evicted_vote.status_code == 403
    assert "not an active member" in resp_evicted_vote.json()["detail"]


# ── TENANT-06: Unauthorized Policy/Membership Change & Cancellation ─────────


def test_tenant_06_unauthorized_policy_or_membership_change_rejected(tenant_env):
    """TENANT-06: Unauthorized consortium policy/membership change or cancellation → rejected (HTTP 403)."""
    from app.domain.consortium_governance import ConsortiumMember

    client = tenant_env["client"]
    token_a = tenant_env["tokens"]["a_admin"]
    token_b = tenant_env["tokens"]["b_admin"]

    cid = "c_tenant_06"
    consortium_governance_service.create_consortium(
        consortium_id=cid,
        name="Consortium 06",
        founder_bank_id="bank_a",
        auth_token=token_a,
    )
    c = consortium_governance_service.get_consortium(cid)
    assert c is not None
    # Bank B added as OBSERVER (no voting rights, cannot sponsor proposals)
    c.members["bank_b"] = ConsortiumMember(bank_id="bank_b", role=MemberRole.OBSERVER, voting_power=0.0)
    # Bank C added as FULL_MEMBER so total voting power is 2.0 (50% < 51% quorum)
    c.members["bank_c"] = ConsortiumMember(bank_id="bank_c", role=MemberRole.FULL_MEMBER, voting_power=1.0)

    # 1. Observer attempts to sponsor policy update
    resp_obs = client.post(
        "/api/v1/coordinator/proposals/policy",
        headers={"Authorization": f"Bearer {token_b}"},
        json={
            "consortium_id": cid,
            "creator_bank_id": "bank_b",
            "policy_updates": {"new_max_epsilon": 3.0},
        },
    )
    assert resp_obs.status_code == 403
    assert "has role 'observer' with no governance voting power" in resp_obs.json()["detail"].lower()

    # Bank A creates proposal (with 2 members, Bank A's automatic vote is 50%, remaining PENDING)
    prop = consortium_governance_service.propose_membership_change(
        consortium_id=cid,
        creator_bank_id="bank_a",
        target_bank_id="bank_d",
        action=ProposalAction.ADD_MEMBER,
        auth_token=token_a,
    )

    # 2. Bank B attempts to cancel Bank A's proposal
    resp_cancel = client.post(
        f"/api/v1/coordinator/proposals/{prop.proposal_id}/cancel?creator_bank_id=bank_b",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp_cancel.status_code == 403
    assert "Unauthorized proposal cancellation" in resp_cancel.json()["detail"]

    # Proposal remains open
    stored_prop = consortium_governance_service.get_proposal(prop.proposal_id)
    assert stored_prop is not None
    assert stored_prop.status == ProposalStatus.PENDING


# ── TENANT-07: Authorized Same-Bank Governance Succeeds ─────────────────────


def test_tenant_07_authorized_same_bank_model_governance_succeeds(tenant_env):
    """TENANT-07: Authorized same-bank model governance succeeds across all lifecycles."""
    reg = tenant_env["registry"]
    client = tenant_env["client"]
    sim_id = "sim_tenant_07_bank_a"

    e1, sha1 = _create_candidate(reg, tenant_env["priv_key"], tenant_env["kid"], sim_id, owning_bank_id="bank_a")
    v1 = e1["version"]

    # 1. Bank A ML engineer signs off
    sig_mle = generate_signoff_signature(sim_id, v1, sha1, "ml_engineer", "mle_bank_a", signing_secret=TEST_SIGNING_SECRET)
    resp_s1 = client.post(
        f"/api/v1/registry/{sim_id}/versions/{v1}/signoff",
        headers={"Authorization": f"Bearer {tenant_env['tokens']['a_mle']}"},
        json={"role": "ml_engineer", "user": "mle_bank_a", "signature": sig_mle},
    )
    assert resp_s1.status_code == 200

    # 2. Bank A Compliance signs off
    sig_comp = generate_signoff_signature(sim_id, v1, sha1, "compliance", "comp_bank_a", signing_secret=TEST_SIGNING_SECRET)
    resp_s2 = client.post(
        f"/api/v1/registry/{sim_id}/versions/{v1}/signoff",
        headers={"Authorization": f"Bearer {tenant_env['tokens']['a_comp']}"},
        json={"role": "compliance", "user": "comp_bank_a", "signature": sig_comp},
    )
    assert resp_s2.status_code == 200

    # 3. Bank A ML lead promotes to champion
    resp_promo = client.post(
        f"/api/v1/registry/{sim_id}/versions/{v1}/promote",
        headers={"Authorization": f"Bearer {tenant_env['tokens']['a_lead']}"},
        json={"target_status": "champion", "enforce_sr11_7": False},
    )
    assert resp_promo.status_code == 200
    assert resp_promo.json()["status"] == "champion"
    assert resp_promo.json()["is_active"] is True

    # 4. Bank A Compliance rolls back
    resp_rb = client.post(
        f"/api/v1/registry/{sim_id}/rollback/{v1}",
        headers={"Authorization": f"Bearer {tenant_env['tokens']['a_comp']}"},
    )
    assert resp_rb.status_code == 200
    assert resp_rb.json()["status"] == "champion"


# ── TENANT-08: Authorized Multi-Bank Consortium Governance ─────────────────


def test_tenant_08_authorized_multi_bank_consortium_governance_succeeds(tenant_env):
    """TENANT-08: Authorized multi-bank consortium proposal, independent votes, and quorum resolution succeed."""
    client = tenant_env["client"]
    token_a = tenant_env["tokens"]["a_admin"]
    token_b = tenant_env["tokens"]["b_admin"]

    cid = "c_tenant_08"
    # 1. Founder Bank A creates consortium
    resp_create = client.post(
        "/api/v1/coordinator/consortium",
        headers={"Authorization": f"Bearer {token_a}"},
        json={
            "consortium_id": cid,
            "name": "Consortium 08",
            "founder_bank_id": "bank_a",
            "quorum_ratio": 0.51,
        },
    )
    assert resp_create.status_code == 201

    # 2. Bank A proposes to ADD Bank B
    resp_prop = client.post(
        "/api/v1/coordinator/proposals/membership",
        headers={"Authorization": f"Bearer {token_a}"},
        json={
            "consortium_id": cid,
            "creator_bank_id": "bank_a",
            "target_bank_id": "bank_b",
            "action": "ADD_MEMBER",
        },
    )
    assert resp_prop.status_code == 201
    prop_data = resp_prop.json()
    # In single-member consortium, proposer's automatic vote achieves 100% quorum immediately
    assert prop_data["status"] == "APPROVED"

    # Verify Bank B is now an active member
    c = consortium_governance_service.get_consortium(cid)
    assert c is not None
    assert "bank_b" in c.members
    assert c.members["bank_b"].role == MemberRole.FULL_MEMBER

    # 3. Multi-bank voting: Now that Bank A and Bank B are members (total 2 votes)
    # Bank B creates policy proposal to increase quorum ratio to 0.60
    resp_pol = client.post(
        "/api/v1/coordinator/proposals/policy",
        headers={"Authorization": f"Bearer {token_b}"},
        json={
            "consortium_id": cid,
            "creator_bank_id": "bank_b",
            "policy_updates": {"new_quorum_ratio": 0.60},
        },
    )
    assert resp_pol.status_code == 201
    pol_data = resp_pol.json()
    pol_id = pol_data["proposal_id"]
    # Bank B's automatic vote represents 1/2 = 50% (< 51%), so status is PENDING
    assert pol_data["status"] == "PENDING"

    # Bank A independently casts vote FOR -> reaches 2/2 = 100% (>= 51%) -> APPROVED
    resp_va = client.post(
        f"/api/v1/coordinator/proposals/{pol_id}/vote",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"bank_id": "bank_a", "approve": True},
    )
    assert resp_va.status_code == 200
    assert resp_va.json()["status"] == "APPROVED"

    # Policy update is applied authoritatively to the consortium
    c_updated = consortium_governance_service.get_consortium(cid)
    assert c_updated is not None
    assert c_updated.quorum_ratio == 0.60


# ── TENANT-09: Unbound/Ambiguous Model Ownership Fails Closed ───────────────


def test_tenant_09_unbound_model_ownership_fails_closed(tenant_env):
    """TENANT-09: Unbound or ambiguous model ownership fails closed without fabricating institutional ownership."""
    reg = tenant_env["registry"]
    client = tenant_env["client"]
    sim_id = "sim_tenant_09_unbound"

    # Save model with NO owning_bank_id and NO consortium_id
    entry, sha256 = _create_candidate(
        reg, tenant_env["priv_key"], tenant_env["kid"], sim_id, owning_bank_id=None, consortium_id=None
    )
    v = entry["version"]

    ownership = reg.get_simulation_ownership(sim_id)
    assert ownership["is_unbound"] is True
    assert ownership["owning_bank_id"] is None
    assert ownership["consortium_id"] is None

    # Bank A attempts sign-off on unbound model → FAILS CLOSED (403 Forbidden)
    sig_a = generate_signoff_signature(sim_id, v, sha256, "ml_engineer", "mle_bank_a", signing_secret=TEST_SIGNING_SECRET)
    resp_a = client.post(
        f"/api/v1/registry/{sim_id}/versions/{v}/signoff",
        headers={"Authorization": f"Bearer {tenant_env['tokens']['a_mle']}"},
        json={"role": "ml_engineer", "user": "mle_bank_a", "signature": sig_a},
    )
    assert resp_a.status_code == 403
    assert "lacks authoritative institutional ownership binding" in resp_a.json()["detail"]

    # Bank B attempts sign-off on unbound model → FAILS CLOSED (403 Forbidden)
    sig_b = generate_signoff_signature(sim_id, v, sha256, "ml_engineer", "mle_bank_b", signing_secret=TEST_SIGNING_SECRET)
    resp_b = client.post(
        f"/api/v1/registry/{sim_id}/versions/{v}/signoff",
        headers={"Authorization": f"Bearer {tenant_env['tokens']['b_mle']}"},
        json={"role": "ml_engineer", "user": "mle_bank_b", "signature": sig_b},
    )
    assert resp_b.status_code == 403
    assert "lacks authoritative institutional ownership binding" in resp_b.json()["detail"]

    # Bank A attempts promotion on unbound model → FAILS CLOSED
    resp_promo = client.post(
        f"/api/v1/registry/{sim_id}/versions/{v}/promote",
        headers={"Authorization": f"Bearer {tenant_env['tokens']['a_lead']}"},
        json={"target_status": "champion", "enforce_sr11_7": False},
    )
    assert resp_promo.status_code == 403
    assert "lacks authoritative institutional ownership binding" in resp_promo.json()["detail"]

    # Direct service promotion on unbound model → FAILS CLOSED
    with pytest.raises(ModelAuthorizationError, match="lacks authoritative institutional ownership binding"):
        reg.promote_version(sim_id, v, target_status="champion", auth_token=tenant_env["tokens"]["a_lead"])

    # Verify zero mutation in manifest
    assert len(reg.get_version_metadata(sim_id, v)["sign_offs"]) == 0
    assert reg.get_version_metadata(sim_id, v)["is_active"] is False


# ── TENANT-10: Existing Authentication, RBAC, Clearance, & Four-Eyes ────────


def test_tenant_10_regressions_intact(tenant_env):
    """TENANT-10: Existing authentication, live RBAC, clearance, Four-Eyes, and internal orchestration regressions intact."""
    reg = tenant_env["registry"]
    client = tenant_env["client"]
    sim_id = "sim_tenant_10_bank_a"

    # Candidate 1
    e1, sha1 = _create_candidate(reg, tenant_env["priv_key"], tenant_env["kid"], sim_id, owning_bank_id="bank_a")
    v1 = e1["version"]

    # 1. Four-Eyes Principle: Same user cannot perform both sign-offs
    sig1_mle = generate_signoff_signature(sim_id, v1, sha1, "ml_engineer", "mle_bank_a", signing_secret=TEST_SIGNING_SECRET)
    reg.sign_off(sim_id, v1, "ml_engineer", "mle_bank_a", sig1_mle, auth_token=tenant_env["tokens"]["a_mle"])

    # Same user attempts compliance sign-off → rejected
    sig1_comp_same = generate_signoff_signature(sim_id, v1, sha1, "compliance", "mle_bank_a", signing_secret=TEST_SIGNING_SECRET)
    with pytest.raises((ValueError, ModelIntegrityError), match="Four-Eyes Principle"):
        reg.sign_off(sim_id, v1, "compliance", "mle_bank_a", sig1_comp_same, auth_token=tenant_env["tokens"]["a_mle"])

    # Legitimate second sign-off by distinct compliance officer
    sig1_comp = generate_signoff_signature(sim_id, v1, sha1, "compliance", "comp_bank_a", signing_secret=TEST_SIGNING_SECRET)
    reg.sign_off(sim_id, v1, "compliance", "comp_bank_a", sig1_comp, auth_token=tenant_env["tokens"]["a_comp"])

    # 2. RBAC & Clearance: Operator with insufficient clearance (level 1) rejected for promotion
    u_low = tenant_env["auth_svc"].register_user("mle_low", "Pass123!", "bank_a", roles=["ml_engineer"], clearance_level=1)
    tok_low = tenant_env["auth_svc"].create_token_bundle(u_low).access_token
    resp_low = client.post(
        f"/api/v1/registry/{sim_id}/versions/{v1}/promote",
        headers={"Authorization": f"Bearer {tok_low}"},
        json={"target_status": "champion", "enforce_sr11_7": False},
    )
    assert resp_low.status_code == 403
    assert "insufficient" in resp_low.json()["detail"].lower()
    assert "clearance" in resp_low.json()["detail"].lower()

    # 3. Legitimate promotion with clearance level >= 2 succeeds
    reg.promote_version(sim_id, v1, target_status="champion", auth_token=tenant_env["tokens"]["a_lead"])
    active_prom = reg.get_active_version(sim_id)
    assert active_prom is not None
    assert active_prom["version"] == v1

    # 4. Trusted internal orchestration context rollback succeeds
    ctx_rb = create_trusted_internal_context(
        service_name="model_evaluation_engine",
        action="rollback",
        resource_type="model",
        resource_id=sim_id,
        model_version=v1,
        bank_id="bank_a",
    )
    rb_entry = reg.rollback(sim_id, v1, trusted_context=ctx_rb)
    assert rb_entry["version"] == v1
    assert rb_entry["is_active"] is True
