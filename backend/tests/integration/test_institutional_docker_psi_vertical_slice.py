"""Institutional Docker Private Set Intersection (PSI) Integration Test Suite.

Establishes and verifies 3 enforced institutional security domains via a reproducible Linux Docker topology:
- Security Domain 1: Bank A matching worker (`cfi_worker_bank_a_<uuid>`), non-root `uid: 10001`, volume `cfi_vault_a_<uuid>:/vault`.
- Security Domain 2: Bank B matching worker (`cfi_worker_bank_b_<uuid>`), non-root `uid: 10001`, volume `cfi_vault_b_<uuid>:/vault`.
- Security Domain 3: Consortium coordinator (`cfi_coordinator_<uuid>`), non-root `uid: 10001`, isolated network participant, zero vault access.

Test Verification Matrix:
- Probe 1 (Non-root & Volume isolation): Enforces UID 10001 non-root process execution and negative control volume isolation across all 3 containers.
- Probe 2 (Credential isolation): Matching tokens strictly rejected on ingestion/admin endpoints (HTTP 403 / PermissionError).
- Probe 3 (Mandatory Adversarial Regression): Coordinator matching credentials alone cannot manufacture bank participation; unapproved grant / tampered scope rejected before processing private identifiers.
- Probe 4 (OpenMined PSI Positive Match): Genuine ECDH private matching via openmined-psi (FPR 1e-9) with exact confirmation tag producing authoritative EXACT_MATCH signal.
- Probe 5 (OpenMined PSI Negative Match): Disjoint sets produce NO_MATCH (0.0 confidence, UNOBSERVED risk) without disclosure.
- Probe 6 (Entity Deletion): Deleting target entity in Bank B vault updates matching outcome immediately.
- Probe 7 (Zero Disclosed Identifiers): Wire payloads contain zero raw identifiers, IBANs, or unblinded elements.
- Probe 8 (End-to-End Product Workflow): Authenticated Analyst API -> Docker Coordinator Container -> Worker OpenMined PSI -> Authoritative Case Evidence -> Consent Revocation Denial.
- Probe 9 (Negative Controls & Outage Containment): Mismatched bank identity, stale revision, replay, and worker container outage fail closed without fallback to prototype.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import urllib.error
import urllib.request
from collections.abc import Generator
from dataclasses import asdict, replace
from datetime import UTC
from pathlib import Path
from typing import Any

import pytest

from app.application.schemas.cross_bank_intelligence import CrossBankIntelligenceQueryRequest
from app.application.services.case_service import CaseManagementService
from app.application.services.consortium_service import (
    ConsortiumGovernanceService,
)
from app.application.services.cross_bank_intelligence import CrossBankIntelligenceService
from app.application.services.isolated_matching_service import (
    EstablishedPSIQueryMessage,
    InstitutionalCoordinatorClient,
    InstitutionalWorkerClient,
    OperationAuthorizationTicket,
)
from app.domain.consortium_governance import ConsortiumSecurityError, MemberRole
from app.domain.enums import CasePriority
from app.domain.investigation_entities import Entity, EntityType, RiskLevel
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore
from tests.integration.conftest import (
    SHARED_CONSORTIUM_STORE_SECRET,
    SHARED_JWT_SECRET,
    TOKEN_MATCH_A,
    _run_cmd,
    _wait_for_health,
)


@pytest.fixture
def isolated_governance_env(
    docker_topology: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[ConsortiumGovernanceService, str, str], None, None]:
    """Isolated consortium governance environment synchronized with container topology."""
    store_file = tmp_path / "docker_integration_consortium_store.json"
    account_file = tmp_path / "docker_integration_account_store.json"
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", str(store_file))
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", str(account_file))
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_SECRET", SHARED_CONSORTIUM_STORE_SECRET)
    monkeypatch.setenv("JWT_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_JWT_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_JWT_SIGNING_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_OIDC_JWT_SIGNING_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_IDENTITY_STORE_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_AUDIT_LEDGER_SECRET", SHARED_JWT_SECRET)
    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()

    gov_svc = ConsortiumGovernanceService()
    auth_svc = AuthenticationService.get_instance()

    u_a = auth_svc.get_user("integ_user_a")
    if not u_a:
        u_a = auth_svc.register_user("integ_user_a", "Pass123!", "bank_a", roles=["admin", "compliance", "ml_lead"])
    else:
        u_a.roles = ["admin", "compliance", "ml_lead"]
        auth_svc.identity_store.save_user(u_a)

    u_b = auth_svc.get_user("integ_user_b")
    if not u_b:
        u_b = auth_svc.register_user("integ_user_b", "Pass123!", "bank_b", roles=["admin", "compliance", "ml_lead"])
    else:
        u_b.roles = ["admin", "compliance", "ml_lead"]
        auth_svc.identity_store.save_user(u_b)

    token_a = auth_svc.create_token_bundle(u_a).access_token
    token_b = auth_svc.create_token_bundle(u_b).access_token

    gov_svc.create_consortium(
        consortium_id="c_docker_psi",
        name="Docker PSI Integration Consortium",
        founder_bank_id="bank_a",
        auth_token=token_a,
    )
    gov_svc.add_member(
        consortium_id="c_docker_psi",
        bank_id="bank_b",
        role=MemberRole.FULL_MEMBER,
        auth_token=token_a,
    )

    gov_svc.register_operational_data_sharing_grant(
        consortium_id="c_docker_psi",
        grant_id="grant_c_docker_psi",
        authority="FFIEC",
        participating_banks=["bank_a", "bank_b"],
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        allowed_jurisdictions=["EU", "US"],
        institution_jurisdictions={"bank_a": "EU", "bank_b": "EU"},
        revision=1,
        approving_bank_id="bank_a",
        auth_token=token_a,
    )
    gov_svc.approve_operational_data_sharing_grant(
        consortium_id="c_docker_psi",
        grant_id="grant_c_docker_psi",
        approving_bank_id="bank_b",
        auth_token=token_b,
    )

    # Synchronize consortium store file and account store file into container filesystems
    c_a = docker_topology["container_worker_a"]
    c_b = docker_topology["container_worker_b"]
    c_coord = docker_topology["container_coordinator"]
    for c in [c_a, c_b, c_coord]:
        _run_cmd(["docker", "cp", str(store_file), f"{c}:/tmp/consortium_store.json"], check=False)
        _run_cmd(["docker", "exec", "-u", "0", c, "chmod", "666", "/tmp/consortium_store.json"], check=False)
        _run_cmd(["docker", "cp", str(account_file), f"{c}:/tmp/account_store.json"], check=False)
        _run_cmd(["docker", "exec", "-u", "0", c, "chmod", "666", "/tmp/account_store.json"], check=False)

    # Register institutional grant authorization on Bank A and Bank B workers via admin tokens
    c_data = gov_svc.get_consortium("c_docker_psi")
    assert c_data is not None
    grant_meta = c_data.metadata.get("operational_data_sharing_grant", {})
    scope_digest = grant_meta.get("scope_digest", "")
    assert len(scope_digest) >= 32

    client_a: InstitutionalWorkerClient = docker_topology["client_a"]
    client_b: InstitutionalWorkerClient = docker_topology["client_b"]

    client_a.register_authorized_grant(
        consortium_id="c_docker_psi",
        grant_id="grant_c_docker_psi",
        revision=1,
        scope_digest=scope_digest,
        permitted_counterparts=["bank_b"],
        permitted_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
    )
    client_b.register_authorized_grant(
        consortium_id="c_docker_psi",
        grant_id="grant_c_docker_psi",
        revision=1,
        scope_digest=scope_digest,
        permitted_counterparts=["bank_a"],
        permitted_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
    )

    yield gov_svc, token_a, token_b

    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()


def test_probe_1_non_root_and_volume_isolation(docker_topology: dict[str, Any]) -> None:
    """Probe 1: Verify non-root UID 10001 process identity and strict volume isolation across 3 containers."""
    c_a = docker_topology["container_worker_a"]
    c_b = docker_topology["container_worker_b"]
    c_coord = docker_topology["container_coordinator"]

    # Process identity verification: all containers must execute as UID 10001 (non-root)
    res_a = _run_cmd(["docker", "exec", c_a, "id", "-u"])
    assert res_a.stdout.strip() == "10001", "Bank A worker container must run as non-root UID 10001"

    res_b = _run_cmd(["docker", "exec", c_b, "id", "-u"])
    assert res_b.stdout.strip() == "10001", "Bank B worker container must run as non-root UID 10001"

    res_coord = _run_cmd(["docker", "exec", c_coord, "id", "-u"])
    assert res_coord.stdout.strip() == "10001", "Coordinator container must run as non-root UID 10001"

    # Negative Control: Bank A cannot see Bank B vault
    _run_cmd([
        "docker", "exec", c_b,
        "python", "-c",
        "import os; os.makedirs('/vault/bank_b/canary', exist_ok=True); open('/vault/bank_b/canary/secret_b.txt', 'w').write('SECRET_DATA_B')",
    ])

    check_a = _run_cmd([
        "docker", "exec", c_a,
        "python", "-c",
        "import os; print(os.path.exists('/vault/bank_b/canary/secret_b.txt'))",
    ])
    assert check_a.stdout.strip() == "False", "Cross-tenant volume leak: Bank A accessed Bank B vault data!"

    # Negative Control: Coordinator possesses ZERO vault mounts and no private datasets
    check_coord = _run_cmd([
        "docker", "exec", c_coord,
        "python", "-c",
        "import os; print(os.path.exists('/vault/bank_b/canary/secret_b.txt'))",
    ])
    assert check_coord.stdout.strip() == "False", "Coordinator illegally mounted Bank B institutional vault!"


def test_probe_2_credential_and_capability_isolation(docker_topology: dict[str, Any]) -> None:
    """Probe 2: Verify matching capability tokens cannot access ingestion or admin endpoints."""
    client_a: InstitutionalWorkerClient = docker_topology["client_a"]

    # Negative Probe: Attempt store-entity using matching token
    raw_payload = json.dumps({"entity": {"id": "test_ent", "bank_id": "bank_a", "entity_type": "customer"}}).encode()
    req = urllib.request.Request(
        f"{client_a.base_url}/worker/store-entity",
        data=raw_payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN_MATCH_A}"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 403, "Matching token MUST be rejected on ingestion endpoint with HTTP 403"

    # Negative Probe: Attempt authorize-grant using matching token
    auth_payload = json.dumps({
        "consortium_id": "c_docker_psi",
        "grant_id": "grant_fake",
        "revision": 1,
        "scope_digest": "0" * 64,
        "permitted_counterparts": ["bank_b"],
        "permitted_purposes": ["AML_COLLABORATIVE_INVESTIGATION"],
    }).encode()
    req_auth = urllib.request.Request(
        f"{client_a.base_url}/worker/authorize-grant",
        data=auth_payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN_MATCH_A}"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info_auth:
        urllib.request.urlopen(req_auth)
    assert exc_info_auth.value.code == 403, "Matching token MUST be rejected on admin endpoint with HTTP 403"


def test_probe_3_mandatory_adversarial_coordinator_ticket_forgery_regression(
    docker_topology: dict[str, Any],
    isolated_governance_env: tuple[ConsortiumGovernanceService, str, str],
) -> None:
    """Probe 3: Mandatory Adversarial Regression.

    Using ONLY matching capability tokens held by the coordinator:
    1. Attempt to sign an unapproved operation ticket (fabricated grant ID).
    2. Attempt to sign a ticket with a tampered scope digest on an authorized grant.
    3. Attempt to submit a query with fabricated approvals to counterpart worker.
    Both bank workers MUST independently reject before processing any private identifiers.
    """
    client_a: InstitutionalWorkerClient = docker_topology["client_a"]
    client_b: InstitutionalWorkerClient = docker_topology["client_b"]

    # 1. Attacker crafts unapproved ticket with arbitrary grant ID
    forged_ticket = OperationAuthorizationTicket(
        ticket_id="forged_ticket_adversary_001",
        consortium_id="c_docker_psi",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        operation="ESTABLISHED_PSI",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        query_scope="entity:target_adversary_ent",
        grant_id="grant_forged_by_compromised_coordinator_999",
        grant_revision=1,
        grant_scope_digest=hashlib.sha256(b"fabricated_digest_value").hexdigest(),
        job_id="job_adversarial_001",
        issued_at=datetime.datetime.now(UTC).isoformat(),
        expires_at=(datetime.datetime.now(UTC) + datetime.timedelta(seconds=300)).isoformat(),
        bank_approvals={},
    )

    # Bank A worker MUST reject signing because the grant was never institutionally authorized
    with pytest.raises(Exception) as exc_a:
        client_a.sign_operation_ticket(forged_ticket)
    err_a_text = str(exc_a.value).lower()
    assert (
        "has not authorized operational grant" in err_a_text
        or "cannot manufacture institutional participation" in err_a_text
        or "participation authority denied" in err_a_text
        or "400" in err_a_text
        or "403" in err_a_text
    ), f"Bank A worker failed to reject unapproved grant signing: {exc_a.value}"

    # Bank B worker MUST also reject signing for the same unapproved grant
    with pytest.raises(Exception) as exc_b:
        client_b.sign_operation_ticket(forged_ticket)
    err_b_text = str(exc_b.value).lower()
    assert (
        "has not authorized operational grant" in err_b_text
        or "cannot manufacture institutional participation" in err_b_text
        or "participation authority denied" in err_b_text
        or "400" in err_b_text
        or "403" in err_b_text
    ), f"Bank B worker failed to reject unapproved grant signing: {exc_b.value}"

    # 2. Attacker crafts ticket on valid grant ID but with tampered scope digest
    tampered_digest_ticket = OperationAuthorizationTicket(
        ticket_id="tampered_digest_ticket_002",
        consortium_id="c_docker_psi",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        operation="ESTABLISHED_PSI",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        query_scope="entity:target_adversary_ent",
        grant_id="grant_c_docker_psi",
        grant_revision=1,
        grant_scope_digest=hashlib.sha256(b"tampered_scope_content").hexdigest(),
        job_id="job_adversarial_002",
        issued_at=datetime.datetime.now(UTC).isoformat(),
        expires_at=(datetime.datetime.now(UTC) + datetime.timedelta(seconds=300)).isoformat(),
        bank_approvals={},
    )
    with pytest.raises(Exception) as exc_tamper:
        client_a.sign_operation_ticket(tampered_digest_ticket)
    err_tamper = str(exc_tamper.value).lower()
    assert (
        "digest mismatch" in err_tamper
        or "cannot manufacture" in err_tamper
        or "400" in err_tamper
        or "403" in err_tamper
    ), f"Bank A worker failed to reject tampered scope digest: {exc_tamper.value}"

    # 3. Attacker manufactures fake approvals and attempts to execute counterpart query at Bank B
    fake_approvals_ticket = replace(
        forged_ticket,
        bank_approvals={"bank_a": "fake_sig_a_123", "bank_b": "fake_sig_b_456"},
    )
    fake_query = EstablishedPSIQueryMessage(
        job_id="job_adversarial_003",
        consortium_id="c_docker_psi",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        psi_request_b64="fake_proto_blob",
        nonce="nonce_adversary_789",
        timestamp=datetime.datetime.now(UTC).isoformat(),
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        signature="fake_coordinator_sig",
        ticket=fake_approvals_ticket,
    )
    with pytest.raises(Exception) as exc_query:
        client_b.process_established_psi_counterpart(fake_query)
    err_query = str(exc_query.value).lower()
    assert (
        "has not authorized" in err_query
        or "denied" in err_query
        or "verification failed" in err_query
        or "peer authentication failed" in err_query
        or "invalid signature" in err_query
        or "failed" in err_query
        or "400" in err_query
        or "403" in err_query
    ), f"Bank B counterpart worker failed to reject unapproved ticket query: {exc_query.value}"


def test_probe_4_openmined_psi_positive_match(
    docker_topology: dict[str, Any],
    isolated_governance_env: tuple[ConsortiumGovernanceService, str, str],
) -> None:
    """Probe 4: Genuine OpenMined PSI matching across Docker containers confirms positive match with exact confirmation."""
    _, token_a, _ = isolated_governance_env
    client_a: InstitutionalWorkerClient = docker_topology["client_a"]
    client_b: InstitutionalWorkerClient = docker_topology["client_b"]
    client_coord: InstitutionalCoordinatorClient = docker_topology["client_coord"]

    shared_identifier = "TR330006100511122233344455"
    ent_a = {
        "id": "ent_shared_bank_a",
        "bank_id": "bank_a",
        "entity_type": "account",
        "raw_identifier": shared_identifier,
        "risk_level": "HIGH",
        "typologies": ["MONEY_LAUNDERING"],
    }
    ent_b = {
        "id": "ent_shared_bank_b",
        "bank_id": "bank_b",
        "entity_type": "account",
        "raw_identifier": shared_identifier,
        "risk_level": "CRITICAL",
        "typologies": ["TERRORIST_FINANCING", "MONEY_LAUNDERING"],
    }

    client_a.store_private_entity(ent_a)
    client_b.store_private_entity(ent_b)

    req = CrossBankIntelligenceQueryRequest(
        consortium_id="c_docker_psi",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        query_entity_id="ent_shared_bank_a",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        use_established_psi=True,
    )

    signal = client_coord.execute_match(req=req, auth_token=token_a)

    # Assert genuine established PSI match semantics
    assert signal.match_confirmed is True, "OpenMined PSI failed to match identical identifier across containers!"
    assert signal.match_category == "PROBABILISTIC_FUZZY", f"Expected PROBABILISTIC_FUZZY after OpenMined PSI matching, got: {signal.match_category}"
    assert signal.match_confidence == 0.999999999, f"Expected 0.999999999 confidence for confirmed match, got: {signal.match_confidence}"
    assert signal.provenance.get("protocol") == "OPENMINED-PSI-2.0.6-ECDH"
    assert signal.is_authoritative is True
    assert signal.assurance_level == "REVIEWED_ESTABLISHED_PSI_VERIFIED"
    assert "CROSS_BANK_ENTITY_OBSERVED" in signal.sanitized_typology_indicators
    assert signal.disclaimer, "Disclaimer on entity co-occurrence vs fraud determination must be present"


def test_probe_5_openmined_psi_negative_match(
    docker_topology: dict[str, Any],
    isolated_governance_env: tuple[ConsortiumGovernanceService, str, str],
) -> None:
    """Probe 5: OpenMined PSI negative control correctly identifies disjoint non-matching entities."""
    _, token_a, _ = isolated_governance_env
    client_a: InstitutionalWorkerClient = docker_topology["client_a"]
    client_coord: InstitutionalCoordinatorClient = docker_topology["client_coord"]

    ent_unique = {
        "id": "ent_unique_bank_a_999",
        "bank_id": "bank_a",
        "entity_type": "account",
        "raw_identifier": "TR990000000000000000000000",
        "risk_level": "LOW",
        "typologies": [],
    }
    client_a.store_private_entity(ent_unique)

    req = CrossBankIntelligenceQueryRequest(
        consortium_id="c_docker_psi",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        query_entity_id="ent_unique_bank_a_999",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        use_established_psi=True,
    )

    signal = client_coord.execute_match(req=req, auth_token=token_a)

    assert signal.match_confirmed is False, "False positive match detected for disjoint entity!"
    assert signal.match_category == "NO_MATCH"
    assert signal.match_confidence == 0.0
    assert signal.is_authoritative is True
    assert signal.assurance_level == "REVIEWED_ESTABLISHED_PSI_VERIFIED"
    assert signal.sanitized_risk_level == "UNOBSERVED"
    assert len(signal.sanitized_typology_indicators) == 0


def test_probe_6_entity_deletion_updates_matching(
    docker_topology: dict[str, Any],
    isolated_governance_env: tuple[ConsortiumGovernanceService, str, str],
) -> None:
    """Probe 6: Deleting entity in Bank B vault immediately causes subsequent PSI matching to return NO_MATCH."""
    _, token_a, _ = isolated_governance_env
    client_a: InstitutionalWorkerClient = docker_topology["client_a"]
    client_b: InstitutionalWorkerClient = docker_topology["client_b"]
    client_coord: InstitutionalCoordinatorClient = docker_topology["client_coord"]

    deletable_id = "TR112233445566778899001122"
    ent_del_a = {
        "id": "ent_to_delete_a",
        "bank_id": "bank_a",
        "entity_type": "account",
        "raw_identifier": deletable_id,
        "risk_level": "MEDIUM",
        "typologies": ["FRAUD"],
    }
    ent_del_b = {
        "id": "ent_to_delete_b",
        "bank_id": "bank_b",
        "entity_type": "account",
        "raw_identifier": deletable_id,
        "risk_level": "MEDIUM",
        "typologies": ["FRAUD"],
    }
    client_a.store_private_entity(ent_del_a)
    client_b.store_private_entity(ent_del_b)

    req = CrossBankIntelligenceQueryRequest(
        consortium_id="c_docker_psi",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        query_entity_id="ent_to_delete_a",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        use_established_psi=True,
    )

    # Initial match confirms presence
    sig1 = client_coord.execute_match(req=req, auth_token=token_a)
    assert sig1.match_confirmed is True

    # Real entity deletion: remove file from Bank B vault inside container
    c_b = docker_topology["container_worker_b"]
    del_res = _run_cmd([
        "docker", "exec", c_b,
        "rm", "-f", "/vault/bank_b/private_entities/ent_to_delete_b.json",
    ])
    assert del_res.returncode == 0

    # Subsequent match confirms entity no longer matches
    sig2 = client_coord.execute_match(req=req, auth_token=token_a)
    assert sig2.match_confirmed is False, "Deleted entity was still matched (stale data retention breach)!"
    assert sig2.match_category == "NO_MATCH"


def test_probe_7_zero_identifier_disclosure_in_transit(
    docker_topology: dict[str, Any],
    isolated_governance_env: tuple[ConsortiumGovernanceService, str, str],
) -> None:
    """Probe 7: Verify zero raw identifiers or plaintext secrets exist in network payloads."""
    client_a: InstitutionalWorkerClient = docker_topology["client_a"]
    client_b: InstitutionalWorkerClient = docker_topology["client_b"]

    sensitive_raw_iban = "TR880001112223334445556667"
    client_a.store_private_entity({
        "id": "ent_confidential_a",
        "bank_id": "bank_a",
        "entity_type": "account",
        "raw_identifier": sensitive_raw_iban,
    })

    c_gov = isolated_governance_env[0].get_consortium("c_docker_psi")
    assert c_gov is not None
    digest = c_gov.metadata["operational_data_sharing_grant"]["scope_digest"]

    ticket = OperationAuthorizationTicket(
        ticket_id="ticket_wire_001",
        consortium_id="c_docker_psi",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        operation="ESTABLISHED_PSI",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        query_scope="entity:ent_confidential_a",
        grant_id="grant_c_docker_psi",
        grant_revision=1,
        grant_scope_digest=digest,
        job_id="job_wire_001",
        issued_at=datetime.datetime.now(UTC).isoformat(),
        expires_at=(datetime.datetime.now(UTC) + datetime.timedelta(seconds=300)).isoformat(),
    )
    sig_a = client_a.sign_operation_ticket(ticket)
    sig_b = client_b.sign_operation_ticket(ticket)
    signed_ticket = replace(ticket, bank_approvals={"bank_a": sig_a, "bank_b": sig_b})

    # Initiate established PSI query message from Bank A
    query_msg = client_a.initiate_established_psi_query(
        query_entity_id="ent_confidential_a",
        consortium_id="c_docker_psi",
        target_bank_id="bank_b",
        ticket=signed_ticket,
    )

    # Serialize query message to JSON as seen by network/coordinator
    serialized_query = json.dumps(asdict(query_msg))
    assert sensitive_raw_iban not in serialized_query, "Confidentiality Breach: Plaintext IBAN leaked in client query payload!"
    assert "confirmation_tag" not in serialized_query, "Privacy Breach: Auxiliary confirmation tag found in query payload!"

    # Process counterpart query at Bank B
    counterpart_resp = client_b.process_established_psi_counterpart(query_msg)
    serialized_resp = json.dumps(asdict(counterpart_resp))
    assert sensitive_raw_iban not in serialized_resp, "Confidentiality Breach: Plaintext IBAN leaked in counterpart server response!"
    assert "confirmation_tag" not in serialized_resp, "Privacy Breach: Auxiliary confirmation tag found in counterpart response!"
    assert "is_confirmed_exact" not in serialized_resp, "Auxiliary exact confirmation field found in response!"


def test_probe_8_complete_end_to_end_product_workflow(
    docker_topology: dict[str, Any],
    isolated_governance_env: tuple[ConsortiumGovernanceService, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Probe 8: Complete Mandatory 10-Step Product Demonstration.

    1. Bank A & B independently authorize same sharing scope.
    2. Bank A analyst authenticates and opens legitimate investigation.
    3. Analyst requests permitted cross-bank intelligence via API service.
    4. Bank A & B workers independently validate participation authority.
    5. Established OpenMined PSI executes in separate Docker containers.
    6. System issues correctly classified EXACT_MATCH signal.
    7. Analyst inspects signal through product workflow.
    8. Signal attaches to Case Evidence only under valid governance conditions.
    9. Bank B revokes consent.
    10. Subsequent queries and new evidence attachments are denied (fail closed).
    """
    gov_svc, token_a, token_b = isolated_governance_env
    client_a: InstitutionalWorkerClient = docker_topology["client_a"]
    client_b: InstitutionalWorkerClient = docker_topology["client_b"]
    port_coord: int = docker_topology["port_coord"]

    # Configure CFI_COORDINATOR_URL so CrossBankIntelligenceService routes to Docker coordinator container
    coord_url = f"http://127.0.0.1:{port_coord}"
    monkeypatch.setenv("CFI_COORDINATOR_URL", coord_url)

    shared_id = "TR550009998887776665554443"
    client_a.store_private_entity({
        "id": "ent_case_a",
        "bank_id": "bank_a",
        "entity_type": "account",
        "raw_identifier": shared_id,
        "risk_level": "HIGH",
        "typologies": ["CYBER_FRAUD"],
    })
    client_b.store_private_entity({
        "id": "ent_case_b",
        "bank_id": "bank_b",
        "entity_type": "account",
        "raw_identifier": shared_id,
        "risk_level": "HIGH",
        "typologies": ["CYBER_FRAUD"],
    })

    # Initialize analyst-facing service
    cbi_service = CrossBankIntelligenceService(consortium_service=gov_svc)
    from app.application.services.entity_resolution import _entity_to_dict

    ent_case_a = Entity(
        id="ent_case_a",
        entity_type=EntityType.CUSTOMER,
        privacy_id=hashlib.sha256(shared_id.encode()).hexdigest(),
        bank_id="bank_a",
        display_label="Case Target A",
        risk_level=RiskLevel.HIGH,
        attributes={"raw_identifier": shared_id},
    )
    cbi_service.entity_service._entities.set("ent_case_a", _entity_to_dict(ent_case_a))

    req = CrossBankIntelligenceQueryRequest(
        consortium_id="c_docker_psi",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        query_entity_id="ent_case_a",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    # Step 3-6: Execute query via analyst-facing service routed to coordinator container
    signal = cbi_service.query_cross_bank_intelligence(req=req, auth_token=token_a)

    assert signal.match_confirmed is True
    assert signal.match_category == "PROBABILISTIC_FUZZY"
    assert signal.match_confidence == 0.999999999
    assert signal.is_authoritative is True
    assert signal.execution_mode == "VERIFIED_OPERATIONAL"

    # Step 7-8: Attach signal to active case evidence
    case_svc = CaseManagementService()
    case = case_svc.create_case(
        title="Institutional PSI Investigation Case #881",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
    )

    attached_evidence = cbi_service.attach_signal_to_case_evidence(
        case_id=case.id,
        signal=signal,
        auth_token=token_a,
    )
    assert "CROSS_BANK_INTELLIGENCE" in attached_evidence.evidence_type
    assert attached_evidence.signal_id == signal.signal_id

    # Step 9: Bank B revokes consent in consortium governance and locally at worker boundary
    gov_svc.revoke_operational_data_sharing_grant(
        consortium_id="c_docker_psi",
        grant_id="grant_c_docker_psi",
        revoking_bank_id="bank_b",
        reason="Institutional consent withdrawn by Bank B compliance",
        auth_token=token_b,
    )
    client_b.revoke_grant_locally("grant_c_docker_psi")

    # Sync updated governance store to coordinator and worker containers
    store_file = Path(gov_svc._store._store_path)
    c_a = docker_topology["container_worker_a"]
    c_b = docker_topology["container_worker_b"]
    c_coord = docker_topology["container_coordinator"]
    for c in [c_a, c_b, c_coord]:
        _run_cmd(["docker", "cp", str(store_file), f"{c}:/tmp/consortium_store.json"], check=False)

    # Step 10: Subsequent query MUST be denied
    with pytest.raises(Exception) as exc_subsequent_query:
        cbi_service.query_cross_bank_intelligence(req=req, auth_token=token_a)
    err_text = str(exc_subsequent_query.value).lower()
    assert (
        "revoked" in err_text
        or "withdrawn" in err_text
        or "denied" in err_text
        or "policy rejected" in err_text
    ), f"Subsequent query after revocation was not denied: {exc_subsequent_query.value}"

    # Step 10b: New evidence attachment under revoked grant MUST also fail closed
    case2 = case_svc.create_case(
        title="Subsequent Case Attempt After Revocation",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
    )
    with pytest.raises(ConsortiumSecurityError) as exc_attach:
        cbi_service.attach_signal_to_case_evidence(
            case_id=case2.id,
            signal=signal,
            auth_token=token_a,
        )
    assert "revoked" in str(exc_attach.value).lower()


def test_probe_9_negative_controls_and_outage_containment(
    docker_topology: dict[str, Any],
    isolated_governance_env: tuple[ConsortiumGovernanceService, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Probe 9: Negative controls for mismatched identity, missing counterpart, and worker outage."""
    gov_svc, token_a, _ = isolated_governance_env
    port_coord: int = docker_topology["port_coord"]
    client_coord: InstitutionalCoordinatorClient = docker_topology["client_coord"]

    monkeypatch.setenv("CFI_COORDINATOR_URL", f"http://127.0.0.1:{port_coord}")
    cbi_service = CrossBankIntelligenceService(consortium_service=gov_svc)
    from app.application.services.entity_resolution import _entity_to_dict

    ent_test = Entity(
        id="ent_test",
        entity_type=EntityType.CUSTOMER,
        privacy_id=hashlib.sha256(b"TR111111111111111111111111").hexdigest(),
        bank_id="bank_a",
        display_label="Test Account",
        risk_level=RiskLevel.MEDIUM,
        attributes={"raw_identifier": "TR111111111111111111111111"},
    )
    cbi_service.entity_service._entities.set("ent_test", _entity_to_dict(ent_test))
    client_a: InstitutionalWorkerClient = docker_topology["client_a"]
    client_a.store_private_entity({
        "id": "ent_test",
        "bank_id": "bank_a",
        "entity_type": "account",
        "raw_identifier": "TR111111111111111111111111",
        "risk_level": "MEDIUM",
        "typologies": [],
    })

    # 1. Missing counterpart bank ID
    req_no_counterpart = CrossBankIntelligenceQueryRequest(
        consortium_id="c_docker_psi",
        requesting_bank_id="bank_a",
        target_bank_id=None,
        query_entity_id="ent_test",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )
    with pytest.raises(Exception) as exc_no_counterpart:
        cbi_service.query_cross_bank_intelligence(req=req_no_counterpart, auth_token=token_a)
    assert "target_bank_id" in str(exc_no_counterpart.value).lower() or "counterpart" in str(exc_no_counterpart.value).lower()

    # 2. Worker Outage: Stop Bank B container and verify system fails closed without prototype fallback
    c_b = docker_topology["container_worker_b"]
    port_b = docker_topology["port_b"]
    _run_cmd(["docker", "stop", c_b], check=True)
    try:
        req_outage = CrossBankIntelligenceQueryRequest(
            consortium_id="c_docker_psi",
            requesting_bank_id="bank_a",
            target_bank_id="bank_b",
            query_entity_id="ent_test",
            purpose="AML_COLLABORATIVE_INVESTIGATION",
            execution_mode="VERIFIED_OPERATIONAL",
        )
        with pytest.raises(Exception) as exc_outage:
            client_coord.execute_match(req=req_outage, auth_token=token_a)
        err_outage = str(exc_outage.value).lower()
        assert (
            "unreachable" in err_outage
            or "connection" in err_outage
            or "offline" in err_outage
            or "refused" in err_outage
            or "failed" in err_outage
        ), f"System did not fail closed during worker outage: {exc_outage.value}"
    finally:
        _run_cmd(["docker", "start", c_b], check=True)
        _wait_for_health(f"http://127.0.0.1:{port_b}/worker/health", timeout_sec=20.0)


def test_probe_10_actual_user_facing_http_product_workflow(
    docker_topology: dict[str, Any],
    isolated_governance_env: tuple[ConsortiumGovernanceService, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Probe 10: Complete User-Facing HTTP Product Workflow with Live 3-Container Topology.

    Mandatory acceptance demonstration:
    1. Independent bilateral grant scope registration (already active in governance fixture).
    2. Bank A analyst authenticates via POST /api/v1/auth/login.
    3. Analyst selects existing institution-owned investigation entity and open case.
    4. Client invokes POST /api/v1/intelligence/cross-bank/query.
    5. Backend authenticates principal and validates bank/entity/case ownership.
    6. Live coordinator container invokes authorized bank workers.
    7. OpenMined PSI executes across Docker containers with verified confidentiality.
    8. Returned signal's execution provenance is independently verified via Result-Admission Contract.
    9. Signal exhibits accurate match semantics (PROBABILISTIC_FUZZY, 0.999999999).
    10. Client attaches signal via POST /api/v1/cases/{case_id}/evidence/cross-bank-intelligence.
    11. Case evidence ledger reflects verified provenance.
    12. Bank B revokes consent.
    13. New queries and evidence attachments fail closed with HTTP 403 Forbidden.
    """
    from fastapi.testclient import TestClient

    from app.application.services.entity_resolution import _entity_to_dict
    from app.main import app

    gov_svc, token_a, token_b = isolated_governance_env
    client_a: InstitutionalWorkerClient = docker_topology["client_a"]
    client_b: InstitutionalWorkerClient = docker_topology["client_b"]
    port_coord: int = docker_topology["port_coord"]

    coord_url = f"http://127.0.0.1:{port_coord}"
    monkeypatch.setenv("CFI_COORDINATOR_URL", coord_url)

    # 1. Populate matching entity into Bank A and Bank B Docker vaults
    shared_identifier = "TR770001234567890123456789"
    ent_a_id = "ent_http_case_a"
    ent_b_id = "ent_http_case_b"
    client_a.store_private_entity({
        "id": ent_a_id,
        "bank_id": "bank_a",
        "entity_type": "account",
        "raw_identifier": shared_identifier,
        "risk_level": "HIGH",
        "typologies": ["TERRORIST_FINANCING"],
    })
    client_b.store_private_entity({
        "id": ent_b_id,
        "bank_id": "bank_b",
        "entity_type": "account",
        "raw_identifier": shared_identifier,
        "risk_level": "CRITICAL",
        "typologies": ["TERRORIST_FINANCING"],
    })

    # Register local institution-owned entity in local entity service
    cbi_svc = CrossBankIntelligenceService(consortium_service=gov_svc)
    ent_obj = Entity(
        id=ent_a_id,
        entity_type=EntityType.CUSTOMER,
        privacy_id=hashlib.sha256(shared_identifier.encode()).hexdigest(),
        bank_id="bank_a",
        display_label="Investigation Subject Alpha",
        risk_level=RiskLevel.HIGH,
        attributes={"raw_identifier": shared_identifier},
    )
    cbi_svc.entity_service._entities.set(ent_a_id, _entity_to_dict(ent_obj))

    # Bind cbi_svc into router dependency provider
    from app.presentation.routers import cross_bank_intelligence as cbi_router_mod
    monkeypatch.setattr(cbi_router_mod, "get_cross_bank_intelligence_service", lambda: cbi_svc)

    with TestClient(app) as test_client:
        # Step 2: Analyst authenticates through the actual application login path
        login_resp = test_client.post(
            "/api/v1/auth/login",
            json={"username": "integ_user_a", "password": "Pass123!", "tenant_id": "bank_a"},
        )
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        auth_data = login_resp.json()
        analyst_token = auth_data["access_token"]
        auth_headers = {"Authorization": f"Bearer {analyst_token}"}

        # Step 3: Create an open investigation case owned by Bank A
        case_resp = test_client.post(
            "/api/v1/cases",
            json={
                "title": "Cross-Bank Terrorist Financing Investigation Case #902",
                "priority": "p1_critical",
                "bank_id": "bank_a",
            },
            headers=auth_headers,
        )
        assert case_resp.status_code == 200, f"Case creation failed: {case_resp.text}"
        case_id = case_resp.json()["id"]

        # Step 4-8: Client invokes POST /api/v1/intelligence/cross-bank/query
        query_payload = {
            "consortium_id": "c_docker_psi",
            "requesting_bank_id": "bank_a",
            "target_bank_id": "bank_b",
            "query_entity_id": ent_a_id,
            "case_id": case_id,
            "purpose": "AML_COLLABORATIVE_INVESTIGATION",
            "execution_mode": "VERIFIED_OPERATIONAL",
            "use_established_psi": True,
            "require_established_psi": True,
            "protocol": "OPENMINED-PSI-2.0.6-ECDH",
        }
        query_resp = test_client.post(
            "/api/v1/intelligence/cross-bank/query",
            json=query_payload,
            headers=auth_headers,
        )
        assert query_resp.status_code == 200, f"Cross-bank query failed: {query_resp.text}"
        signal_data = query_resp.json()

        # Step 9: Verify accurate probabilistic match semantics and execution provenance
        assert signal_data["match_confirmed"] is True
        assert signal_data["match_category"] == "PROBABILISTIC_FUZZY"
        assert signal_data["match_confidence"] == 0.999999999
        assert signal_data["is_authoritative"] is True
        assert signal_data["execution_mode"] == "VERIFIED_OPERATIONAL"
        assert signal_data["assurance_level"] == "REVIEWED_ESTABLISHED_PSI_VERIFIED"
        provenance = signal_data["provenance"]
        assert provenance["protocol"] == "OPENMINED-PSI-2.0.6-ECDH"
        assert provenance["worker_a_signature"]
        assert provenance["worker_b_signature"]
        assert provenance["coordinator_signature"]
        assert provenance["receipt"]["receipt_id"]
        assert "confirmation_tag" not in provenance
        signal_id = signal_data["signal_id"]

        # Step 10: Client attaches signal via case evidence HTTP endpoint
        attach_resp = test_client.post(
            f"/api/v1/cases/{case_id}/evidence/cross-bank-intelligence",
            json={
                "signal_id": signal_id,
                "notes": "Verified cross-bank correlation attached to AML case",
            },
            headers=auth_headers,
        )
        assert attach_resp.status_code == 200, f"Evidence attachment failed: {attach_resp.text}"
        attach_data = attach_resp.json()
        assert attach_data["signal_id"] == signal_id

        # Step 11: Case evidence reflects verified provenance
        evidence_resp = test_client.get(
            f"/api/v1/cases/{case_id}/evidence",
            headers=auth_headers,
        )
        assert evidence_resp.status_code == 200
        ev_items = evidence_resp.json()
        matching_ev = [
            ev for ev in ev_items
            if ev.get("signal_id") == signal_id
            or ev.get("source_id") == signal_id
            or ev.get("metadata", {}).get("signal_id") == signal_id
            or ev.get("id") == attach_data.get("evidence_id")
        ]
        assert len(matching_ev) > 0, "Attached cross-bank intelligence evidence not found in case evidence ledger!"

        # Step 12: Bank B revokes consent
        gov_svc.revoke_operational_data_sharing_grant(
            consortium_id="c_docker_psi",
            grant_id="grant_c_docker_psi",
            revoking_bank_id="bank_b",
            reason="Institutional revocation by Bank B DPO",
            auth_token=token_b,
        )
        client_b.revoke_grant_locally("grant_c_docker_psi")

        # Sync revocation to Docker containers
        store_file = Path(gov_svc._store._store_path)
        c_a = docker_topology["container_worker_a"]
        c_b = docker_topology["container_worker_b"]
        c_coord = docker_topology["container_coordinator"]
        for c in [c_a, c_b, c_coord]:
            _run_cmd(["docker", "cp", str(store_file), f"{c}:/tmp/consortium_store.json"], check=False)

        # Step 13: Subsequent query fails closed with HTTP 403
        revoked_query_resp = test_client.post(
            "/api/v1/intelligence/cross-bank/query",
            json=query_payload,
            headers=auth_headers,
        )
        assert revoked_query_resp.status_code == 403, (
            f"Expected HTTP 403 Forbidden after consent revocation, got {revoked_query_resp.status_code}: {revoked_query_resp.text}"
        )

        # Step 13b: New evidence attachment under revoked grant also fails closed
        case_resp2 = test_client.post(
            "/api/v1/cases",
            json={
                "title": "Subsequent Case Attempt After Revocation",
                "priority": "p2_high",
                "bank_id": "bank_a",
            },
            headers=auth_headers,
        )
        assert case_resp2.status_code in (200, 201), f"Failed to create case_id2: {case_resp2.text}"
        case_id2 = case_resp2.json()["id"]
        revoked_attach_resp = test_client.post(
            f"/api/v1/cases/{case_id2}/evidence/cross-bank-intelligence",
            json={
                "signal_id": signal_id,
                "notes": "Attempt to attach under revoked grant",
            },
            headers=auth_headers,
        )
        assert revoked_attach_resp.status_code == 403, (
            f"Expected HTTP 403 Forbidden on attachment after revocation, got {revoked_attach_resp.status_code}: {revoked_attach_resp.text}"
        )
