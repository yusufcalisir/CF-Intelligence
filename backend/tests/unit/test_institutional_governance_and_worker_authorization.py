"""Security unit tests for institutional governance, grant scope immutability,
stale approval gating, and worker-side operation authorization.

Verifies:
1. Operational grant scope immutability per revision:
   - Any material modification within the same revision is rejected.
   - Incrementing the revision resets approvals and binds to the new canonical digest.
2. Bilateral consent gating across scope revisions:
   - When Bank A changes permitted jurisdictions, Bank B's prior approval becomes stale.
   - Matching remains denied until Bank B independently reapproves the new revision.
3. Worker-side independent operation authorization validation:
   - Workers reject missing, expired, wrong-bank, or tampered authorization tickets.
   - Workers verify target bank and requesting bank approval signatures independently.
4. Local grant revocation at institutional boundary:
   - Worker-side revocation immediately halts matching even if coordinator attempts dispatch.
5. Revalidation of bilateral consent before case evidence attachment:
   - Stale or revoked grants reject evidence attachment to cases.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import hmac
import json
import uuid
from datetime import UTC
from pathlib import Path

import pytest

from app.application.services.case_service import (
    CaseManagementService,
    EvidenceRegistryService,
)
from app.application.services.consortium_service import (
    ConsortiumGovernanceService,
    MemberRole,
)
from app.application.services.cross_bank_intelligence import CrossBankIntelligenceService
from app.application.services.isolated_matching_service import (
    IsolatedBankWorkerNode,
    OperationAuthorizationTicket,
)
from app.domain.consortium_governance import (
    ConsortiumSecurityError,
)
from app.domain.consortium_policy import ConsortiumPolicyEngine
from app.infrastructure.redis_store import RedisStore
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore


@pytest.fixture(autouse=True)
def clean_redis_and_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Ensure clean isolated stores for each test."""
    store_file = tmp_path / f"consortium_test_store_{uuid.uuid4().hex[:8]}.json"
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", str(store_file))
    DurableConsortiumStore.set_instance(None)
    RedisStore("authoritative_signals").clear()
    RedisStore("research_prototype_signals").clear()
    RedisStore("consortium_records").clear()
    RedisStore("operational_grants").clear()
    RedisStore("cases").clear()
    RedisStore("case").clear()
    RedisStore("evidence").clear()
    yield
    DurableConsortiumStore.set_instance(None)
    RedisStore("authoritative_signals").clear()
    RedisStore("research_prototype_signals").clear()
    RedisStore("consortium_records").clear()
    RedisStore("operational_grants").clear()
    RedisStore("cases").clear()
    RedisStore("case").clear()
    RedisStore("evidence").clear()


def _get_or_create_token(username: str, bank_id: str, roles: list[str]) -> str:
    auth_svc = AuthenticationService.get_instance()
    user = auth_svc.get_user(username)
    if user:
        user.roles = roles
        auth_svc.identity_store.save_user(user)
    else:
        user = auth_svc.register_user(
            username=username,
            plain_password="SecureTestPassword123!",
            bank_id=bank_id,
            roles=roles,
            clearance_level=3,
        )
    return auth_svc.create_token_bundle(user).access_token


def test_grant_scope_immutability_per_revision(tmp_path):
    """Test that operational grant scope cannot be mutated within the same revision."""
    gov_svc = ConsortiumGovernanceService()
    token_a = _get_or_create_token(
        username="officer_immutability_a", bank_id="bank_a", roles=["admin", "compliance"]
    )

    consortium = gov_svc.create_consortium(
        consortium_id="consortium_immutability_test",
        name="Security Test Consortium",
        founder_bank_id="bank_a",
        auth_token=token_a,
    )
    gov_svc.add_member(
        consortium_id=consortium.consortium_id,
        bank_id="bank_b",
        role=MemberRole.FULL_MEMBER,
        auth_token=token_a,
    )

    # Register initial grant at revision 1
    grant = gov_svc.register_operational_data_sharing_grant(
        consortium_id=consortium.consortium_id,
        grant_id="grant_scope_test",
        authority="FFIEC",
        participating_banks=["bank_a", "bank_b"],
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        institution_jurisdictions={"bank_a": "EU", "bank_b": "EU"},
        revision=1,
        approving_bank_id="bank_a",
        auth_token=token_a,
    )
    assert grant["revision"] == 1
    initial_digest = grant["scope_digest"]
    assert initial_digest is not None

    # Attempting to mutate scope at the same revision must be rejected
    with pytest.raises(ValueError, match="is immutable"):
        gov_svc.register_operational_data_sharing_grant(
            consortium_id=consortium.consortium_id,
            grant_id="grant_scope_test",
            authority="FFIEC",
            participating_banks=["bank_a", "bank_b"],
            allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
            allowed_jurisdictions=["EU", "US"],  # modified scope!
            institution_jurisdictions={"bank_a": "EU", "bank_b": "US"},
            revision=1,
            approving_bank_id="bank_a",
            auth_token=token_a,
        )


def test_stale_approval_rejected_after_scope_revision(tmp_path):
    """Test that material change to grant scope requires fresh approval from counterpart.

    Bank A alters permitted jurisdiction; matching must remain denied until Bank B
    independently reapproves the new revision.
    """
    gov_svc = ConsortiumGovernanceService()
    policy_engine = ConsortiumPolicyEngine()
    token_a = _get_or_create_token(
        username="officer_jurisdiction_a", bank_id="bank_a", roles=["admin", "compliance"]
    )
    token_b = _get_or_create_token(
        username="officer_jurisdiction_b", bank_id="bank_b", roles=["admin", "compliance"]
    )

    consortium = gov_svc.create_consortium(
        consortium_id="consortium_jurisdiction_test",
        name="Bilateral Governance Consortium",
        founder_bank_id="bank_a",
        auth_token=token_a,
    )
    gov_svc.add_member(
        consortium_id=consortium.consortium_id,
        bank_id="bank_b",
        role=MemberRole.FULL_MEMBER,
        auth_token=token_a,
    )

    # 1. Bank A registers operational grant at revision 1 (EU only)
    gov_svc.register_operational_data_sharing_grant(
        consortium_id=consortium.consortium_id,
        grant_id="grant_jurisdiction_test",
        authority="FFIEC",
        participating_banks=["bank_a", "bank_b"],
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        institution_jurisdictions={"bank_a": "EU", "bank_b": "EU"},
        allow_institution_identity_sharing=True,
        revision=1,
        approving_bank_id="bank_a",
        auth_token=token_a,
    )

    # 2. Bank B approves revision 1
    gov_svc.approve_operational_data_sharing_grant(
        consortium_id=consortium.consortium_id,
        grant_id="grant_jurisdiction_test",
        approving_bank_id="bank_b",
        auth_token=token_b,
    )

    consortium = gov_svc.get_consortium(consortium.consortium_id)
    assert consortium is not None
    is_valid, reasons, _ = policy_engine.validate_intelligence_sharing(
        consortium=consortium,
        requesting_bank="bank_a",
        target_banks=["bank_b"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        is_operational=True,
    )
    assert is_valid, f"Expected valid policy under revision 1: {reasons}"

    # 3. Bank A modifies permitted jurisdiction to include "US" as revision 2
    gov_svc.register_operational_data_sharing_grant(
        consortium_id=consortium.consortium_id,
        grant_id="grant_jurisdiction_test",
        authority="FFIEC",
        participating_banks=["bank_a", "bank_b"],
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        allowed_jurisdictions=["EU", "US"],
        institution_jurisdictions={"bank_a": "EU", "bank_b": "US"},
        allow_cross_border=True,
        allow_institution_identity_sharing=True,
        revision=2,
        approving_bank_id="bank_a",
        auth_token=token_a,
    )

    # 4. Bank B has NOT approved revision 2 yet. Validation must FAIL CLOSED!
    consortium_r2 = gov_svc.get_consortium(consortium.consortium_id)
    assert consortium_r2 is not None
    is_valid_r2, reasons_r2, _ = policy_engine.validate_intelligence_sharing(
        consortium=consortium_r2,
        requesting_bank="bank_a",
        target_banks=["bank_b"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        is_operational=True,
    )
    assert not is_valid_r2, "Matching must be denied because Bank B has not approved revision 2"
    assert any(
        "has not approved" in r or "reapproves the new revision" in r
        for r in reasons_r2
    )

    # 5. Bank B independently reapproves revision 2
    gov_svc.approve_operational_data_sharing_grant(
        consortium_id=consortium.consortium_id,
        grant_id="grant_jurisdiction_test",
        approving_bank_id="bank_b",
        auth_token=token_b,
    )

    # 6. Now matching is authorized under revision 2
    consortium_approved_r2 = gov_svc.get_consortium(consortium.consortium_id)
    assert consortium_approved_r2 is not None
    is_valid_final, reasons_final, _ = policy_engine.validate_intelligence_sharing(
        consortium=consortium_approved_r2,
        requesting_bank="bank_a",
        target_banks=["bank_b"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        is_operational=True,
    )
    assert is_valid_final, f"Expected authorized matching after Bank B reapproval: {reasons_final}"


def test_worker_side_authorization_ticket_validation(tmp_path):
    """Test that bank worker independently validates operation authorization tickets."""
    storage_root = str(tmp_path / "storage")
    secret_a = "secret_key_bank_a_32bytes_minimum!!"
    secret_b = "secret_key_bank_b_32bytes_minimum!!"

    worker_b = IsolatedBankWorkerNode(
        bank_id="bank_b",
        storage_root=storage_root,
        auth_secret=secret_b,
        trusted_peer_keys={"bank_a": secret_a},
        require_operation_ticket=True,
    )

    # Store entity in worker B
    worker_b.store_private_entity({
        "id": "ent_b1",
        "bank_id": "bank_b",
        "privacy_id": "priv_hash_b1",
    })

    job_id = "job_test_123"
    grant_id = "grant_operational_456"
    scope_digest = hashlib.sha256(b"canonical_grant_scope").hexdigest()
    now = datetime.datetime.now(UTC)
    expires_at = (now + datetime.timedelta(seconds=300)).isoformat()

    ticket = OperationAuthorizationTicket(
        ticket_id="ticket_1",
        consortium_id="consortium_test",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        operation="PRIVATE_SET_INTERSECTION",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        query_scope="entity:ent_a1",
        grant_id=grant_id,
        grant_revision=1,
        grant_scope_digest=scope_digest,
        job_id=job_id,
        issued_at=now.isoformat(),
        expires_at=expires_at,
        bank_approvals={},
    )

    # 1. Missing ticket verification on worker B
    with pytest.raises(ConsortiumSecurityError, match="Missing mandatory operation authorization ticket"):
        worker_b.verify_operation_authorization(
            ticket=None,
            expected_job_id=job_id,
            expected_requesting_bank="bank_a",
            expected_purpose="AML_COLLABORATIVE_INVESTIGATION",
        )

    # 2. Ticket missing target bank approval signature
    with pytest.raises(ConsortiumSecurityError, match="Target institution 'bank_b' participation authority verification failed"):
        worker_b.verify_operation_authorization(
            ticket=ticket,
            expected_job_id=job_id,
            expected_requesting_bank="bank_a",
            expected_purpose="AML_COLLABORATIVE_INVESTIGATION",
        )

    # 3. Add valid target bank signature but missing requesting bank signature
    sig_b = worker_b.sign_operation_authorization_ticket(ticket)
    ticket_with_b = dataclasses.replace(ticket, bank_approvals={"bank_b": sig_b})
    with pytest.raises(ConsortiumSecurityError, match="Requesting institution 'bank_a' authorization verification failed"):
        worker_b.verify_operation_authorization(
            ticket=ticket_with_b,
            expected_job_id=job_id,
            expected_requesting_bank="bank_a",
            expected_purpose="AML_COLLABORATIVE_INVESTIGATION",
        )

    # 4. Add valid requesting bank signature
    canonical_a = f"{ticket.canonical_digest()}:bank_a"
    sig_a = hmac.new(secret_a.encode(), canonical_a.encode(), hashlib.sha256).hexdigest()
    valid_ticket = dataclasses.replace(ticket, bank_approvals={"bank_a": sig_a, "bank_b": sig_b})

    # Verification must succeed!
    worker_b.verify_operation_authorization(
        ticket=valid_ticket,
        expected_job_id=job_id,
        expected_requesting_bank="bank_a",
        expected_purpose="AML_COLLABORATIVE_INVESTIGATION",
    )

    # 5. Expired ticket is rejected
    expired_ticket = dataclasses.replace(
        valid_ticket, expires_at=(now - datetime.timedelta(seconds=10)).isoformat()
    )
    with pytest.raises(ConsortiumSecurityError, match="has expired"):
        worker_b.verify_operation_authorization(
            ticket=expired_ticket,
            expected_job_id=job_id,
            expected_requesting_bank="bank_a",
            expected_purpose="AML_COLLABORATIVE_INVESTIGATION",
        )

    # 6. Wrong target bank is rejected
    wrong_bank_ticket = dataclasses.replace(valid_ticket, target_bank_id="bank_c")
    with pytest.raises(ConsortiumSecurityError, match="Target institution mismatch"):
        worker_b.verify_operation_authorization(
            ticket=wrong_bank_ticket,
            expected_job_id=job_id,
            expected_requesting_bank="bank_a",
            expected_purpose="AML_COLLABORATIVE_INVESTIGATION",
        )


def test_local_grant_revocation_at_worker_boundary(tmp_path):
    """Test that local worker grant revocation immediately halts processing."""
    storage_root = str(tmp_path / "storage")
    secret_a = "secret_key_bank_a_32bytes_minimum!!"
    secret_b = "secret_key_bank_b_32bytes_minimum!!"

    worker_b = IsolatedBankWorkerNode(
        bank_id="bank_b",
        storage_root=storage_root,
        auth_secret=secret_b,
        trusted_peer_keys={"bank_a": secret_a},
        require_operation_ticket=True,
    )

    job_id = "job_revocation_test"
    grant_id = "grant_to_revoke"
    scope_digest = hashlib.sha256(b"scope_revocation").hexdigest()
    now = datetime.datetime.now(UTC)

    ticket = OperationAuthorizationTicket(
        ticket_id="ticket_rev_1",
        consortium_id="consortium_test",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        operation="PRIVATE_SET_INTERSECTION",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        query_scope="entity:ent_a1",
        grant_id=grant_id,
        grant_revision=1,
        grant_scope_digest=scope_digest,
        job_id=job_id,
        issued_at=now.isoformat(),
        expires_at=(now + datetime.timedelta(seconds=300)).isoformat(),
        bank_approvals={},
    )
    sig_b = worker_b.sign_operation_authorization_ticket(ticket)
    sig_a = hmac.new(
        secret_a.encode(), f"{ticket.canonical_digest()}:bank_a".encode(), hashlib.sha256
    ).hexdigest()
    valid_ticket = dataclasses.replace(ticket, bank_approvals={"bank_a": sig_a, "bank_b": sig_b})

    # Before revocation: valid
    worker_b.verify_operation_authorization(
        ticket=valid_ticket,
        expected_job_id=job_id,
        expected_requesting_bank="bank_a",
        expected_purpose="AML_COLLABORATIVE_INVESTIGATION",
    )

    # Local revocation executed by Bank B officer
    worker_b.record_local_grant_revocation(grant_id)

    # After revocation: immediately rejected fail closed
    with pytest.raises(ConsortiumSecurityError, match="has been locally revoked by institution 'bank_b'"):
        worker_b.verify_operation_authorization(
            ticket=valid_ticket,
            expected_job_id=job_id,
            expected_requesting_bank="bank_a",
            expected_purpose="AML_COLLABORATIVE_INVESTIGATION",
        )


def test_consent_revocation_before_evidence_attachment(tmp_path):
    """Test that consent revocation halts evidence attachment even if signal was previously issued."""
    gov_svc = ConsortiumGovernanceService()
    case_svc = CaseManagementService()
    ev_svc = EvidenceRegistryService(case_service=case_svc)
    intel_svc = CrossBankIntelligenceService(
        consortium_service=gov_svc,
        case_service=case_svc,
        evidence_service=ev_svc,
    )

    token_a = _get_or_create_token(
        username="officer_ev_a", bank_id="bank_a", roles=["admin", "compliance", "investigator"]
    )
    token_b = _get_or_create_token(
        username="officer_ev_b", bank_id="bank_b", roles=["admin", "compliance"]
    )

    consortium = gov_svc.create_consortium(
        consortium_id="consortium_ev_test",
        name="Revocation Case Consortium",
        founder_bank_id="bank_a",
        auth_token=token_a,
    )
    gov_svc.add_member(consortium.consortium_id, "bank_b", MemberRole.FULL_MEMBER, auth_token=token_a)

    gov_svc.register_operational_data_sharing_grant(
        consortium_id=consortium.consortium_id,
        grant_id="grant_ev_test",
        authority="FFIEC",
        participating_banks=["bank_a", "bank_b"],
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        institution_jurisdictions={"bank_a": "EU", "bank_b": "EU"},
        allow_institution_identity_sharing=True,
        revision=1,
        approving_bank_id="bank_a",
        auth_token=token_a,
    )
    gov_svc.approve_operational_data_sharing_grant(
        consortium_id=consortium.consortium_id,
        grant_id="grant_ev_test",
        approving_bank_id="bank_b",
        auth_token=token_b,
    )

    # Create a case for bank_a
    case = case_svc.create_case(
        title="Investigation Case 101",
        bank_id="bank_a",
    )

    # Register an authoritative signal bound to grant_ev_test
    signal_id = "sig_auth_123"
    signal_data = {
        "signal_id": signal_id,
        "consortium_id": consortium.consortium_id,
        "requesting_bank_id": "bank_a",
        "target_bank_id": "bank_b",
        "query_entity_id": "ent_a1",
        "match_confirmed": True,
        "match_category": "EXACT_MATCH",
        "match_confidence": 1.0,
        "contributing_institutions_count": 1,
        "contributing_institutions": ["bank_b"],
        "disclosure_mode": "ATTRIBUTED_INSTITUTIONS",
        "sanitized_risk_level": "HIGH",
        "sanitized_typology_indicators": ["CROSS_BANK_ENTITY_OBSERVED"],
        "graph_context": {"has_incident_cross_bank_correlations": True},
        "provenance": {
            "grant_id": "grant_ev_test",
            "grant_revision": 1,
            "target_bank_id": "bank_b",
            "protocol": "OPENMINED-PSI-2.0.6-ECDH",
            "assurance_level": "REVIEWED_ESTABLISHED_PSI_VERIFIED",
        },
        "execution_mode": "VERIFIED_OPERATIONAL",
        "is_authoritative": True,
        "is_synthetic": False,
        "assurance_level": "REVIEWED_ESTABLISHED_PSI_VERIFIED",
    }
    content_hash = hashlib.sha256(
        json.dumps(signal_data, sort_keys=True).encode("utf-8")
    ).hexdigest()

    now = datetime.datetime.now(UTC)
    record = {
        "signal_id": signal_id,
        "signal_data": signal_data,
        "requesting_bank_id": "bank_a",
        "target_bank_id": "bank_b",
        "consortium_id": consortium.consortium_id,
        "grant_id": "grant_ev_test",
        "grant_revision": 1,
        "query_entity_id": "ent_a1",
        "issued_to_principal": "officer_a",
        "issued_at": now.isoformat(),
        "expires_at": (now + datetime.timedelta(hours=2)).isoformat(),
        "content_hash": content_hash,
        "is_authoritative": True,
        "is_synthetic": False,
        "assurance_level": "REVIEWED_ESTABLISHED_PSI_VERIFIED",
        "execution_mode": "VERIFIED_OPERATIONAL",
    }
    RedisStore("authoritative_signals").set(signal_id, record)

    # 1. Attachment succeeds while grant is active
    resp = intel_svc.attach_signal_to_case_evidence(
        case_id=case.id,
        signal_id=signal_id,
        auth_token=token_a,
    )
    assert resp.evidence_id is not None

    # 2. Revoke grant consent in consortium
    gov_svc.revoke_operational_data_sharing_grant(
        consortium_id=consortium.consortium_id,
        grant_id="grant_ev_test",
        revoking_bank_id="bank_b",
        reason="Counterpart consent withdrawn",
        auth_token=token_b,
    )

    # Create new open case
    case2 = case_svc.create_case(
        title="Investigation Case 102",
        bank_id="bank_a",
    )

    # 3. Attachment must fail closed with ConsortiumSecurityError after consent revocation!
    with pytest.raises(ConsortiumSecurityError, match="has been revoked or is no longer active"):
        intel_svc.attach_signal_to_case_evidence(
            case_id=case2.id,
            signal_id=signal_id,
            auth_token=token_a,
        )
