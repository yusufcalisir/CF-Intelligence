"""Unit and integration test suite for Multi-Bank Consortium Scope and Pairwise Authorization (M6.1).

Verifies:
1. Synthetic 3-bank consortium persistence and reload of independently authorized A-B and A-C grants.
2. Mandatory, institution-bound bilateral approvals specific to current scope revision.
3. Strict rejection of third-party approvals, operator bypasses, and cross-bank impersonation.
4. Immutable scope-per-revision semantics and revision-bump fresh approval enforcement.
5. Service-level authorization resolver failing closed for absent, revoked, removed, or unapproved pairs.
6. Independent pairwise revocation (revoking A-C does not affect A-B).
7. Institutional eviction immediately failing closed for all associated pairwise queries.
8. Authentic Ed25519 cryptographic signature verification on pairwise approvals.
9. Jurisdiction boundaries and cross-border sovereignty enforcement.
10. Full backward compatibility with existing two-bank M4/M5 operational data sharing grants.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.consortium_service import (
    ConsortiumGovernanceService,
    compute_pairwise_grant_scope_digest,
)
from app.domain.consortium_governance import (
    ConsortiumSecurityError,
    PairwiseOperationalGrant,
    ProposalAction,
)
from app.domain.consortium_policy import (
    ConsortiumPolicyEngine,
)
from app.domain.institutional_identity import (
    InstitutionalVerificationRegistry,
    canonical_pairwise_grant_approval_bytes,
)
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore


@pytest.fixture(autouse=True)
def isolate_consortium_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    store_file = tmp_path / "pairwise_consortium_test_store.json"
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", str(store_file))
    DurableConsortiumStore.set_instance(None)
    yield
    DurableConsortiumStore.set_instance(None)


@pytest.fixture
def auth_tokens() -> dict[str, str]:
    auth_svc = AuthenticationService.get_instance()
    u_a = auth_svc.get_user("pairwise_admin_a") or auth_svc.register_user(
        "pairwise_admin_a", "AdminPass123!", "bank_a", roles=["admin"], clearance_level=3
    )
    u_b = auth_svc.get_user("pairwise_admin_b") or auth_svc.register_user(
        "pairwise_admin_b", "AdminPass123!", "bank_b", roles=["admin"], clearance_level=3
    )
    u_c = auth_svc.get_user("pairwise_admin_c") or auth_svc.register_user(
        "pairwise_admin_c", "AdminPass123!", "bank_c", roles=["admin"], clearance_level=3
    )
    return {
        "bank_a": auth_svc.create_token_bundle(u_a).access_token,
        "bank_b": auth_svc.create_token_bundle(u_b).access_token,
        "bank_c": auth_svc.create_token_bundle(u_c).access_token,
    }


def _setup_three_bank_consortium(
    service: ConsortiumGovernanceService, auth_tokens: dict[str, str]
) -> None:
    service.create_consortium(
        consortium_id="cfi-tri-consortium",
        name="Tri-Bank Intelligence Consortium",
        founder_bank_id="bank_a",
        quorum_ratio=0.51,
        auth_token=auth_tokens["bank_a"],
    )
    p_b = service.propose_membership_change(
        consortium_id="cfi-tri-consortium",
        creator_bank_id="bank_a",
        target_bank_id="bank_b",
        action=ProposalAction.ADD_MEMBER,
        auth_token=auth_tokens["bank_a"],
    )
    assert p_b.status.value == "APPROVED"
    p_c = service.propose_membership_change(
        consortium_id="cfi-tri-consortium",
        creator_bank_id="bank_a",
        target_bank_id="bank_c",
        action=ProposalAction.ADD_MEMBER,
        auth_token=auth_tokens["bank_a"],
    )
    service.cast_vote(p_c.proposal_id, "bank_b", approve=True, auth_token=auth_tokens["bank_b"])
    assert p_c.status.value == "APPROVED"


def test_pairwise_domain_model_invariants() -> None:
    """PairwiseOperationalGrant domain model strictly enforces validation rules."""
    # Self-pairwise grant rejected
    with pytest.raises(ValueError, match="requires two distinct member institutions"):
        PairwiseOperationalGrant(
            grant_id="grant_aa",
            consortium_id="c1",
            institution_a="bank_a",
            institution_b="bank_a",
            authority="AUTH",
        )

    # Empty grant ID rejected
    with pytest.raises(ValueError, match="grant_id cannot be empty"):
        PairwiseOperationalGrant(
            grant_id="",
            consortium_id="c1",
            institution_a="bank_a",
            institution_b="bank_b",
            authority="AUTH",
        )

    grant = PairwiseOperationalGrant(
        grant_id="grant_ab_01",
        consortium_id="c1",
        institution_a="bank_a",
        institution_b="bank_b",
        authority="CONSORTIUM_AUTHORITY",
        revision=1,
    )
    assert grant.involves("bank_a")
    assert grant.involves("bank_b")
    assert not grant.involves("bank_c")
    assert grant.counterpart_of("bank_a") == "bank_b"
    assert grant.counterpart_of("bank_b") == "bank_a"

    # Serialization roundtrip
    d = grant.to_dict()
    restored = PairwiseOperationalGrant.from_dict(d)
    assert restored.grant_id == "grant_ab_01"
    assert restored.participating_banks == ["bank_a", "bank_b"]


def test_three_bank_consortium_pairwise_registration_and_bilateral_approval(
    auth_tokens: dict[str, str],
) -> None:
    """A synthetic 3-bank consortium registers and independently authorizes A-B and A-C grants."""
    service = ConsortiumGovernanceService()
    _setup_three_bank_consortium(service, auth_tokens)

    # 1. Bank A registers pairwise grant with Bank B
    g_ab = service.register_pairwise_operational_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_ab_v1",
        institution_a="bank_a",
        institution_b="bank_b",
        authority="BILATERAL_AML_TREATY",
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION", "FRAUD_INVESTIGATION"],
        institution_jurisdictions={"bank_a": "eu-central-1", "bank_b": "eu-central-1"},
        allowed_jurisdictions=["eu-central-1"],
        allow_cross_border=False,
        revision=1,
        auth_token=auth_tokens["bank_a"],
    )
    assert g_ab["grant_id"] == "grant_ab_v1"
    assert "bank_a" in g_ab["approvals"]
    assert "bank_b" not in g_ab["approvals"]

    # 2. Before Bank B approves: unilateral approval fails closed
    is_auth, reason, _ = service.resolve_pairwise_authorization(
        consortium_id="cfi-tri-consortium",
        requesting_bank_id="bank_a",
        counterpart_bank_id="bank_b",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
    )
    assert not is_auth
    assert "Counterpart institution 'bank_b' has not approved" in reason

    with pytest.raises(ConsortiumSecurityError):
        service.check_pairwise_authorization(
            consortium_id="cfi-tri-consortium",
            requesting_bank_id="bank_a",
            counterpart_bank_id="bank_b",
            purpose="AML_COLLABORATIVE_INVESTIGATION",
        )

    # 3. Bank B bilaterally approves grant_ab_v1
    g_ab_approved = service.approve_pairwise_operational_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_ab_v1",
        approving_bank_id="bank_b",
        auth_token=auth_tokens["bank_b"],
    )
    assert "bank_b" in g_ab_approved["approvals"]
    assert g_ab_approved["is_active"] is True

    # Now A-B pairwise query is authorized
    is_auth, reason, resolved_grant = service.resolve_pairwise_authorization(
        consortium_id="cfi-tri-consortium",
        requesting_bank_id="bank_a",
        counterpart_bank_id="bank_b",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
    )
    assert is_auth
    assert reason == "Pairwise authorization verified."
    assert resolved_grant is not None
    assert resolved_grant["grant_id"] == "grant_ab_v1"

    # Symmetric query from Bank B to Bank A is also authorized
    is_auth_ba, _, _ = service.resolve_pairwise_authorization(
        consortium_id="cfi-tri-consortium",
        requesting_bank_id="bank_b",
        counterpart_bank_id="bank_a",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
    )
    assert is_auth_ba

    # 4. Bank A registers and Bank C approves an independent A-C grant
    service.register_pairwise_operational_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_ac_v1",
        institution_a="bank_a",
        institution_b="bank_c",
        authority="BILATERAL_AML_TREATY",
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        institution_jurisdictions={"bank_a": "eu-central-1", "bank_c": "eu-central-1"},
        allowed_jurisdictions=["eu-central-1"],
        allow_cross_border=False,
        revision=1,
        auth_token=auth_tokens["bank_a"],
    )
    service.approve_pairwise_operational_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_ac_v1",
        approving_bank_id="bank_c",
        auth_token=auth_tokens["bank_c"],
    )

    is_auth_ac, _, _ = service.resolve_pairwise_authorization(
        consortium_id="cfi-tri-consortium",
        requesting_bank_id="bank_a",
        counterpart_bank_id="bank_c",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
    )
    assert is_auth_ac

    # 5. Non-existent B-C pair fails closed
    is_auth_bc, reason_bc, _ = service.resolve_pairwise_authorization(
        consortium_id="cfi-tri-consortium",
        requesting_bank_id="bank_b",
        counterpart_bank_id="bank_c",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
    )
    assert not is_auth_bc
    assert "No authorized operational data-sharing grant exists" in reason_bc


def test_durable_consortium_store_persistence_and_reload(
    auth_tokens: dict[str, str],
) -> None:
    """Pairwise operational grants persist across process/store reload."""
    service1 = ConsortiumGovernanceService()
    _setup_three_bank_consortium(service1, auth_tokens)

    service1.register_pairwise_operational_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_ab_persist",
        institution_a="bank_a",
        institution_b="bank_b",
        authority="DURABLE_TEST_AUTHORITY",
        allowed_purposes=["FRAUD_INVESTIGATION"],
        institution_jurisdictions={"bank_a": "eu-central-1", "bank_b": "eu-central-1"},
        allowed_jurisdictions=["eu-central-1"],
        revision=1,
        auth_token=auth_tokens["bank_a"],
    )
    service1.approve_pairwise_operational_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_ab_persist",
        approving_bank_id="bank_b",
        auth_token=auth_tokens["bank_b"],
    )

    # Force a fresh instance of DurableConsortiumStore and service
    DurableConsortiumStore.set_instance(None)
    service2 = ConsortiumGovernanceService()

    # Verify state after reload
    reloaded_c = service2.get_consortium("cfi-tri-consortium")
    assert reloaded_c is not None
    assert "grant_ab_persist" in reloaded_c.pairwise_operational_grants
    persisted_grant = reloaded_c.pairwise_operational_grants["grant_ab_persist"]
    assert persisted_grant["authority"] == "DURABLE_TEST_AUTHORITY"
    assert persisted_grant["revision"] == 1
    assert "bank_a" in persisted_grant["approvals"]
    assert "bank_b" in persisted_grant["approvals"]

    # Resolver succeeds on reloaded store
    is_auth, _, _ = service2.resolve_pairwise_authorization(
        consortium_id="cfi-tri-consortium",
        requesting_bank_id="bank_a",
        counterpart_bank_id="bank_b",
        purpose="FRAUD_INVESTIGATION",
    )
    assert is_auth


def test_third_party_approval_strictly_prohibited(
    auth_tokens: dict[str, str],
) -> None:
    """A third-party bank or unauthorized institution cannot approve on behalf of participants."""
    service = ConsortiumGovernanceService()
    _setup_three_bank_consortium(service, auth_tokens)

    # Bank C attempts to register a grant between Bank A and Bank B
    with pytest.raises(PermissionError, match="Cross-bank authorization rejected: Approving bank 'bank_c' is not a participant"):
        service.register_pairwise_operational_grant(
            consortium_id="cfi-tri-consortium",
            grant_id="grant_ab_unauth",
            institution_a="bank_a",
            institution_b="bank_b",
            authority="ILLEGITIMATE_AUTH",
            auth_token=auth_tokens["bank_c"],
        )

    # Bank A registers A-B
    service.register_pairwise_operational_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_ab_unauth",
        institution_a="bank_a",
        institution_b="bank_b",
        authority="LEGITIMATE_AUTH",
        auth_token=auth_tokens["bank_a"],
    )

    # Bank C attempts to approve A-B grant
    with pytest.raises(PermissionError, match="Third-party approval is strictly prohibited"):
        service.approve_pairwise_operational_grant(
            consortium_id="cfi-tri-consortium",
            grant_id="grant_ab_unauth",
            approving_bank_id="bank_c",
            auth_token=auth_tokens["bank_c"],
        )


def test_scope_immutability_and_revision_bump(
    auth_tokens: dict[str, str],
) -> None:
    """Material changes under the same revision are rejected; incremented revision resets approvals."""
    service = ConsortiumGovernanceService()
    _setup_three_bank_consortium(service, auth_tokens)

    service.register_pairwise_operational_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_ab_scope",
        institution_a="bank_a",
        institution_b="bank_b",
        authority="TREATY_V1",
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        institution_jurisdictions={"bank_a": "eu-central-1", "bank_b": "eu-central-1"},
        allowed_jurisdictions=["eu-central-1"],
        revision=1,
        auth_token=auth_tokens["bank_a"],
    )
    service.approve_pairwise_operational_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_ab_scope",
        approving_bank_id="bank_b",
        auth_token=auth_tokens["bank_b"],
    )

    # Modifying scope (adding FRAUD_INVESTIGATION) without bumping revision is rejected
    with pytest.raises(ValueError, match="Pairwise operational grant scope is immutable per revision"):
        service.register_pairwise_operational_grant(
            consortium_id="cfi-tri-consortium",
            grant_id="grant_ab_scope",
            institution_a="bank_a",
            institution_b="bank_b",
            authority="TREATY_V1",
            allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION", "FRAUD_INVESTIGATION"],
            institution_jurisdictions={"bank_a": "eu-central-1", "bank_b": "eu-central-1"},
            allowed_jurisdictions=["eu-central-1"],
            revision=1,
            auth_token=auth_tokens["bank_a"],
        )

    # Sub-1 revision rejected
    with pytest.raises(ValueError, match="Grant revision must be >= 1"):
        service.register_pairwise_operational_grant(
            consortium_id="cfi-tri-consortium",
            grant_id="grant_ab_scope",
            institution_a="bank_a",
            institution_b="bank_b",
            authority="TREATY_V1",
            revision=0,
            auth_token=auth_tokens["bank_a"],
        )

    # Material update with incremented revision (v2)
    service.register_pairwise_operational_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_ab_scope",
        institution_a="bank_a",
        institution_b="bank_b",
        authority="TREATY_V2",
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION", "FRAUD_INVESTIGATION"],
        institution_jurisdictions={"bank_a": "eu-central-1", "bank_b": "eu-central-1"},
        allowed_jurisdictions=["eu-central-1"],
        revision=2,
        auth_token=auth_tokens["bank_a"],
    )

    # Decreasing revision (re-attempting revision 1 when current revision is 2) rejected
    with pytest.raises(ValueError, match="strictly monotonically increasing"):
        service.register_pairwise_operational_grant(
            consortium_id="cfi-tri-consortium",
            grant_id="grant_ab_scope",
            institution_a="bank_a",
            institution_b="bank_b",
            authority="TREATY_OLD",
            revision=1,
            auth_token=auth_tokens["bank_a"],
        )

    # Bank B has not yet approved revision 2 -> fails closed
    is_auth, reason, _ = service.resolve_pairwise_authorization(
        consortium_id="cfi-tri-consortium",
        requesting_bank_id="bank_a",
        counterpart_bank_id="bank_b",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
    )
    assert not is_auth
    assert "Counterpart institution 'bank_b' has not approved" in reason

    # Bank B approves revision 2 -> succeeds
    service.approve_pairwise_operational_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_ab_scope",
        approving_bank_id="bank_b",
        auth_token=auth_tokens["bank_b"],
    )

    is_auth, reason, grant_v2 = service.resolve_pairwise_authorization(
        consortium_id="cfi-tri-consortium",
        requesting_bank_id="bank_a",
        counterpart_bank_id="bank_b",
        purpose="FRAUD_INVESTIGATION",
        expected_revision=2,
    )
    assert is_auth
    assert grant_v2 is not None
    assert grant_v2["revision"] == 2


def test_independent_pairwise_revocation(
    auth_tokens: dict[str, str],
) -> None:
    """Revoking A-C grant leaves unrelated A-B grant completely unaffected."""
    service = ConsortiumGovernanceService()
    _setup_three_bank_consortium(service, auth_tokens)

    # Setup A-B grant
    service.register_pairwise_operational_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_ab",
        institution_a="bank_a",
        institution_b="bank_b",
        authority="AUTH_AB",
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        institution_jurisdictions={"bank_a": "eu-central-1", "bank_b": "eu-central-1"},
        allowed_jurisdictions=["eu-central-1"],
        revision=1,
        auth_token=auth_tokens["bank_a"],
    )
    service.approve_pairwise_operational_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_ab",
        approving_bank_id="bank_b",
        auth_token=auth_tokens["bank_b"],
    )

    # Setup A-C grant
    service.register_pairwise_operational_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_ac",
        institution_a="bank_a",
        institution_b="bank_c",
        authority="AUTH_AC",
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        institution_jurisdictions={"bank_a": "eu-central-1", "bank_c": "eu-central-1"},
        allowed_jurisdictions=["eu-central-1"],
        revision=1,
        auth_token=auth_tokens["bank_a"],
    )
    service.approve_pairwise_operational_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_ac",
        approving_bank_id="bank_c",
        auth_token=auth_tokens["bank_c"],
    )

    # Both are authorized initially
    assert service.resolve_pairwise_authorization("cfi-tri-consortium", "bank_a", "bank_b", "AML_COLLABORATIVE_INVESTIGATION")[0]
    assert service.resolve_pairwise_authorization("cfi-tri-consortium", "bank_a", "bank_c", "AML_COLLABORATIVE_INVESTIGATION")[0]

    # Bank C unilaterally revokes A-C grant
    service.revoke_pairwise_operational_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_ac",
        revoking_bank_id="bank_c",
        reason="bilateral_treaty_termination",
        auth_token=auth_tokens["bank_c"],
    )

    # A-C fails closed
    is_auth_ac, reason_ac, _ = service.resolve_pairwise_authorization(
        consortium_id="cfi-tri-consortium",
        requesting_bank_id="bank_a",
        counterpart_bank_id="bank_c",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
    )
    assert not is_auth_ac
    assert "revoked" in reason_ac.lower()

    # A-B is completely intact and authorized!
    is_auth_ab, reason_ab, _ = service.resolve_pairwise_authorization(
        consortium_id="cfi-tri-consortium",
        requesting_bank_id="bank_a",
        counterpart_bank_id="bank_b",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
    )
    assert is_auth_ab
    assert reason_ab == "Pairwise authorization verified."


def test_member_eviction_immediately_invalidates_all_its_pairwise_grants(
    auth_tokens: dict[str, str],
) -> None:
    """When an institution is evicted from the consortium, all its pairwise grants fail closed immediately."""
    service = ConsortiumGovernanceService()
    _setup_three_bank_consortium(service, auth_tokens)

    service.register_pairwise_operational_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_ab",
        institution_a="bank_a",
        institution_b="bank_b",
        authority="AUTH_AB",
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        institution_jurisdictions={"bank_a": "eu-central-1", "bank_b": "eu-central-1"},
        allowed_jurisdictions=["eu-central-1"],
        auth_token=auth_tokens["bank_a"],
    )
    service.approve_pairwise_operational_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_ab",
        approving_bank_id="bank_b",
        auth_token=auth_tokens["bank_b"],
    )

    service.register_pairwise_operational_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_ac",
        institution_a="bank_a",
        institution_b="bank_c",
        authority="AUTH_AC",
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        institution_jurisdictions={"bank_a": "eu-central-1", "bank_c": "eu-central-1"},
        allowed_jurisdictions=["eu-central-1"],
        auth_token=auth_tokens["bank_a"],
    )
    service.approve_pairwise_operational_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_ac",
        approving_bank_id="bank_c",
        auth_token=auth_tokens["bank_c"],
    )

    # Evict Bank C
    p_evict = service.propose_membership_change(
        consortium_id="cfi-tri-consortium",
        creator_bank_id="bank_a",
        target_bank_id="bank_c",
        action=ProposalAction.REMOVE_MEMBER,
        auth_token=auth_tokens["bank_a"],
    )
    service.cast_vote(p_evict.proposal_id, "bank_b", approve=True, auth_token=auth_tokens["bank_b"])
    assert p_evict.status.value == "APPROVED"

    # Any query with Bank C now fails closed
    is_auth_ac, reason_ac, _ = service.resolve_pairwise_authorization(
        consortium_id="cfi-tri-consortium",
        requesting_bank_id="bank_a",
        counterpart_bank_id="bank_c",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
    )
    assert not is_auth_ac
    assert "evicted" in reason_ac.lower()

    # Query from Bank C fails closed
    is_auth_ca, reason_ca, _ = service.resolve_pairwise_authorization(
        consortium_id="cfi-tri-consortium",
        requesting_bank_id="bank_c",
        counterpart_bank_id="bank_a",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
    )
    assert not is_auth_ca
    assert "evicted" in reason_ca.lower()

    # A-B remains fully authorized
    is_auth_ab, _, _ = service.resolve_pairwise_authorization(
        consortium_id="cfi-tri-consortium",
        requesting_bank_id="bank_a",
        counterpart_bank_id="bank_b",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
    )
    assert is_auth_ab


def test_ed25519_signature_verification_on_pairwise_approval(
    auth_tokens: dict[str, str],
) -> None:
    """Authentic Ed25519 institutional signatures on pairwise grant approvals are verified."""
    registry = InstitutionalVerificationRegistry()
    service = ConsortiumGovernanceService(institutional_registry=registry)
    _setup_three_bank_consortium(service, auth_tokens)

    # Generate real Ed25519 signing keys for Bank A and Bank B
    priv_a = ed25519.Ed25519PrivateKey.generate()
    pub_a = priv_a.public_key()
    priv_b = ed25519.Ed25519PrivateKey.generate()
    pub_b = priv_b.public_key()

    registry.register_public_key("bank_a", pub_a)
    registry.register_public_key("bank_b", pub_b)

    scope_digest = compute_pairwise_grant_scope_digest(
        grant_id="grant_sig_01",
        consortium_id="cfi-tri-consortium",
        institution_a="bank_a",
        institution_b="bank_b",
        revision=1,
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        institution_jurisdictions={"bank_a": "eu-central-1", "bank_b": "eu-central-1"},
        allowed_jurisdictions=["eu-central-1"],
        allow_cross_border=False,
        allow_institution_identity_sharing=False,
    )

    # Bank A creates authentic Ed25519 signature
    actor_id_a = "user:pairwise_admin_a@bank_a"
    challenge_a = canonical_pairwise_grant_approval_bytes(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_sig_01",
        revision=1,
        scope_digest=scope_digest,
        approving_bank_id="bank_a",
        approver_id=actor_id_a,
    )
    sig_a = priv_a.sign(challenge_a).hex()

    # Register with valid signature succeeds
    service.register_pairwise_operational_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_sig_01",
        institution_a="bank_a",
        institution_b="bank_b",
        authority="CRYPTO_GOVERNANCE",
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        institution_jurisdictions={"bank_a": "eu-central-1", "bank_b": "eu-central-1"},
        allowed_jurisdictions=["eu-central-1"],
        revision=1,
        auth_token=auth_tokens["bank_a"],
        approval_signature=sig_a,
    )

    # Bank B attempts to approve with tampered/invalid signature -> rejected
    actor_id_b = "user:pairwise_admin_b@bank_b"
    fake_sig_b = b"0" * 64
    with pytest.raises(ConsortiumSecurityError, match="Cryptographic signature verification failed"):
        service.approve_pairwise_operational_grant(
            consortium_id="cfi-tri-consortium",
            grant_id="grant_sig_01",
            approving_bank_id="bank_b",
            auth_token=auth_tokens["bank_b"],
            approval_signature=fake_sig_b.hex(),
        )

    # Bank B approves with authentic signature -> succeeds
    challenge_b = canonical_pairwise_grant_approval_bytes(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_sig_01",
        revision=1,
        scope_digest=scope_digest,
        approving_bank_id="bank_b",
        approver_id=actor_id_b,
    )
    sig_b = priv_b.sign(challenge_b).hex()
    service.approve_pairwise_operational_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_sig_01",
        approving_bank_id="bank_b",
        auth_token=auth_tokens["bank_b"],
        approval_signature=sig_b,
    )

    is_auth, _, _ = service.resolve_pairwise_authorization(
        consortium_id="cfi-tri-consortium",
        requesting_bank_id="bank_a",
        counterpart_bank_id="bank_b",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
    )
    assert is_auth


def test_jurisdiction_and_cross_border_sovereignty(
    auth_tokens: dict[str, str],
) -> None:
    """Cross-border data sharing is blocked without bilateral cross-border treaty."""
    service = ConsortiumGovernanceService()
    _setup_three_bank_consortium(service, auth_tokens)

    # Bank A (eu-central-1) and Bank B (us-east-1), allow_cross_border=False
    service.register_pairwise_operational_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_cross_border_test",
        institution_a="bank_a",
        institution_b="bank_b",
        authority="STRICT_SOVEREIGNTY",
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        institution_jurisdictions={"bank_a": "eu-central-1", "bank_b": "us-east-1"},
        allowed_jurisdictions=["eu-central-1", "us-east-1"],
        allow_cross_border=False,
        auth_token=auth_tokens["bank_a"],
    )
    service.approve_pairwise_operational_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_cross_border_test",
        approving_bank_id="bank_b",
        auth_token=auth_tokens["bank_b"],
    )

    # Query fails closed due to cross-border disclosure restriction
    is_auth, reason, _ = service.resolve_pairwise_authorization(
        consortium_id="cfi-tri-consortium",
        requesting_bank_id="bank_a",
        counterpart_bank_id="bank_b",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
    )
    assert not is_auth
    assert "Cross-border intelligence disclosure between 'eu-central-1' and 'us-east-1' is prohibited" in reason

    # Bumping to revision 2 with allow_cross_border=True
    service.register_pairwise_operational_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_cross_border_test",
        institution_a="bank_a",
        institution_b="bank_b",
        authority="CROSS_BORDER_TREATY",
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        institution_jurisdictions={"bank_a": "eu-central-1", "bank_b": "us-east-1"},
        allowed_jurisdictions=["eu-central-1", "us-east-1"],
        allow_cross_border=True,
        revision=2,
        auth_token=auth_tokens["bank_a"],
    )
    service.approve_pairwise_operational_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="grant_cross_border_test",
        approving_bank_id="bank_b",
        auth_token=auth_tokens["bank_b"],
    )

    # Now cross-border query succeeds
    is_auth_cross, _, _ = service.resolve_pairwise_authorization(
        consortium_id="cfi-tri-consortium",
        requesting_bank_id="bank_a",
        counterpart_bank_id="bank_b",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
    )
    assert is_auth_cross


def test_legacy_two_bank_operational_grant_compatibility(
    auth_tokens: dict[str, str],
) -> None:
    """Existing two-bank operational_data_sharing_grant is preserved and supported."""
    service = ConsortiumGovernanceService()
    _setup_three_bank_consortium(service, auth_tokens)

    # Register legacy grant between Bank A and Bank B
    service.register_operational_data_sharing_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="legacy_grant_01",
        authority="LEGACY_TWO_BANK_AUTHORITY",
        participating_banks=["bank_a", "bank_b"],
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        institution_jurisdictions={"bank_a": "eu-central-1", "bank_b": "eu-central-1"},
        allowed_jurisdictions=["eu-central-1"],
        revision=1,
        auth_token=auth_tokens["bank_a"],
    )
    service.approve_operational_data_sharing_grant(
        consortium_id="cfi-tri-consortium",
        grant_id="legacy_grant_01",
        approving_bank_id="bank_b",
        auth_token=auth_tokens["bank_b"],
    )

    # Resolver succeeds via legacy grant fallback for {bank_a, bank_b}
    is_auth_ab, reason_ab, grant_ab = service.resolve_pairwise_authorization(
        consortium_id="cfi-tri-consortium",
        requesting_bank_id="bank_a",
        counterpart_bank_id="bank_b",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
    )
    assert is_auth_ab
    assert grant_ab is not None
    assert grant_ab["grant_id"] == "legacy_grant_01"

    # Resolver fails closed for Bank C (not a participant of legacy grant)
    is_auth_ac, reason_ac, _ = service.resolve_pairwise_authorization(
        consortium_id="cfi-tri-consortium",
        requesting_bank_id="bank_a",
        counterpart_bank_id="bank_c",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
    )
    assert not is_auth_ac
    assert "No authorized operational data-sharing grant exists" in reason_ac

    # ConsortiumPolicyEngine.validate_intelligence_sharing works with legacy grant
    policy_engine = ConsortiumPolicyEngine()
    consortium = service.get_consortium("cfi-tri-consortium")
    assert consortium is not None

    is_valid, reasons, scope = policy_engine.validate_intelligence_sharing(
        consortium=consortium,
        requesting_bank="bank_a",
        target_banks=["bank_b"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        is_operational=True,
    )
    assert is_valid, f"Expected valid, got: {reasons}"

    # Target Bank C fails in policy engine
    is_valid_c, reasons_c, _ = policy_engine.validate_intelligence_sharing(
        consortium=consortium,
        requesting_bank="bank_a",
        target_banks=["bank_c"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        is_operational=True,
    )
    assert not is_valid_c
