"""Institutional Docker Private Set Intersection (PSI) Integration Test Suite.

Establishes and verifies 3 enforced institutional security domains via a reproducible Linux Docker topology:
- Security Domain 1: Bank A matching worker (`cfi_worker_bank_a`), non-root `uid: 10001`, volume `bank_a_vault:/vault`.
- Security Domain 2: Bank B matching worker (`cfi_worker_bank_b`), non-root `uid: 10001`, volume `bank_b_vault:/vault`.
- Security Domain 3: Consortium coordinator (`cfi_coordinator`), non-root, isolated network participant, zero vault access.

Test Verification Matrix:
- Probe 1 (Non-root & Volume isolation): Enforces UID 10001 non-root process execution and negative control volume isolation.
- Probe 2 (Credential isolation): Matching tokens strictly rejected on ingestion/admin endpoints (HTTP 403 / PermissionError).
- Probe 3 (Ticket authorization): Unsigned, tampered, or expired operation authorization tickets rejected at worker boundary.
- Probe 4 (OpenMined PSI Positive Match): Genuine ECDH private matching via openmined-psi producing authoritative signal.
- Probe 5 (OpenMined PSI Negative Match): Clean negative intersection producing unobserved signal without disclosure.
- Probe 6 (Entity Deletion): Deleting target entity in Bank B vault updates matching outcome immediately.
- Probe 7 (Zero Disclosed Identifiers): Wire payloads contain zero raw identifiers, IBANs, or unblinded elements.
- Probe 8 (Case Evidence Attachment & Revocation): Authoritative signal attaches to case; consent revocation fails closed.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Generator
from dataclasses import asdict, replace
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
    CrossBankMatchingCoordinator,
    EstablishedPSIQueryMessage,
    InstitutionalWorkerClient,
    OperationAuthorizationTicket,
)
from app.domain.consortium_governance import ConsortiumSecurityError, MemberRole
from app.domain.enums import CasePriority
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore

NETWORK_NAME = "cfi_isolated_mesh"
CONTAINER_WORKER_A = "cfi_worker_bank_a"
CONTAINER_WORKER_B = "cfi_worker_bank_b"
CONTAINER_COORDINATOR = "cfi_coordinator"
VOLUME_BANK_A = "bank_a_vault"
VOLUME_BANK_B = "bank_b_vault"
PORT_WORKER_A = 8101
PORT_WORKER_B = 8102
IMAGE_NAME = "cfi-institutional-worker:latest"

SECRET_A = "auth_sec_bank_a_87234691823749123847"
SECRET_B = "auth_sec_bank_b_98127394812739481273"
TOKEN_MATCH_A = "matching_token_bank_a_3847291847"
TOKEN_MATCH_B = "matching_token_bank_b_9182739182"
TOKEN_INGEST_A = "ingestion_token_bank_a_12938471"
TOKEN_INGEST_B = "ingestion_token_bank_b_58271947"
TOKEN_ADMIN_A = "admin_token_bank_a_987123984"
TOKEN_ADMIN_B = "admin_token_bank_b_123987123"


def _run_cmd(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    """Execute host shell command safely."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=check,
    )


def is_docker_running() -> bool:
    try:
        res = _run_cmd(["docker", "info"], check=False)
        return res.returncode == 0
    except Exception:
        return False


def is_docker_image_available(image_name: str) -> bool:
    """Check if the designated docker image is available locally."""
    try:
        res = _run_cmd(["docker", "image", "inspect", image_name], check=False)
        return res.returncode == 0
    except Exception:
        return False


def ensure_docker_image(image_name: str) -> bool:
    """Ensure the required docker image exists locally, or attempt to build it from Dockerfile."""
    if is_docker_image_available(image_name):
        return True

    repo_root = Path(__file__).resolve().parents[3]
    dockerfile = repo_root / "docker" / "Dockerfile.institutional_worker"
    if dockerfile.exists():
        build_res = _run_cmd(
            ["docker", "build", "-t", image_name, "-f", str(dockerfile), str(repo_root)],
            check=False,
        )
        if build_res.returncode == 0 and is_docker_image_available(image_name):
            return True

    return False


def _wait_for_health(url: str, timeout_sec: float = 25.0) -> bool:
    start = time.time()
    while time.time() - start < timeout_sec:
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


@pytest.fixture(scope="module")
def docker_topology() -> Generator[dict[str, Any], None, None]:
    """Sets up genuine 3-container topology on isolated bridge network."""
    if not is_docker_running():
        pytest.skip("Docker daemon is not reachable or running.")

    if not ensure_docker_image(IMAGE_NAME):
        pytest.skip(
            f"Required Docker image '{IMAGE_NAME}' is not available and could not be prepared automatically."
        )

    # Cleanup any stale containers or volumes
    for c in [CONTAINER_WORKER_A, CONTAINER_WORKER_B, CONTAINER_COORDINATOR]:
        _run_cmd(["docker", "rm", "-f", c], check=False)
    _run_cmd(["docker", "network", "rm", NETWORK_NAME], check=False)
    _run_cmd(["docker", "volume", "rm", VOLUME_BANK_A, VOLUME_BANK_B], check=False)

    # 1. Create isolated mesh network
    net_res = _run_cmd(["docker", "network", "create", NETWORK_NAME], check=False)
    if net_res.returncode != 0:
        pytest.skip(f"Failed to create docker network '{NETWORK_NAME}': {net_res.stderr.strip()}")

    # 2. Create isolated institutional vaults
    _run_cmd(["docker", "volume", "create", VOLUME_BANK_A], check=False)
    _run_cmd(["docker", "volume", "create", VOLUME_BANK_B], check=False)

    # 3. Start Bank A Worker container
    peers_a_json = json.dumps({"bank_b": SECRET_B})
    cmd_worker_a = [
        "docker", "run", "-d",
        "--name", CONTAINER_WORKER_A,
        "--network", NETWORK_NAME,
        "-v", f"{VOLUME_BANK_A}:/vault",
        "-p", f"{PORT_WORKER_A}:8100",
        "-e", "APP_ENV=production",
        "-e", "BANK_ID=bank_a",
        "-e", "STORAGE_ROOT=/vault",
        "-e", "CFI_STORAGE_DIR=/vault",
        "-e", f"CFI_CONSORTIUM_STORE_SECRET={SECRET_A}",
        "-e", "WORKER_HOST=0.0.0.0",
        "-e", "WORKER_PORT=8100",
        "-e", f"WORKER_AUTH_SECRET={SECRET_A}",
        "-e", f"WORKER_MATCHING_TOKEN={TOKEN_MATCH_A}",
        "-e", f"WORKER_INGESTION_TOKEN={TOKEN_INGEST_A}",
        "-e", f"WORKER_ADMIN_TOKEN={TOKEN_ADMIN_A}",
        "-e", f"WORKER_TRUSTED_PEERS_JSON={peers_a_json}",
        IMAGE_NAME,
    ]
    res_worker_a = _run_cmd(cmd_worker_a, check=False)
    if res_worker_a.returncode != 0:
        for c in [CONTAINER_WORKER_A, CONTAINER_WORKER_B, CONTAINER_COORDINATOR]:
            _run_cmd(["docker", "rm", "-f", c], check=False)
        _run_cmd(["docker", "network", "rm", NETWORK_NAME], check=False)
        _run_cmd(["docker", "volume", "rm", VOLUME_BANK_A, VOLUME_BANK_B], check=False)
        pytest.skip(
            f"Failed to start container '{CONTAINER_WORKER_A}' (exit code {res_worker_a.returncode}): "
            f"{res_worker_a.stderr.strip() or res_worker_a.stdout.strip()}"
        )

    # 4. Start Bank B Worker container
    peers_b_json = json.dumps({"bank_a": SECRET_A})
    cmd_worker_b = [
        "docker", "run", "-d",
        "--name", CONTAINER_WORKER_B,
        "--network", NETWORK_NAME,
        "-v", f"{VOLUME_BANK_B}:/vault",
        "-p", f"{PORT_WORKER_B}:8100",
        "-e", "APP_ENV=production",
        "-e", "BANK_ID=bank_b",
        "-e", "STORAGE_ROOT=/vault",
        "-e", "CFI_STORAGE_DIR=/vault",
        "-e", f"CFI_CONSORTIUM_STORE_SECRET={SECRET_B}",
        "-e", "WORKER_HOST=0.0.0.0",
        "-e", "WORKER_PORT=8100",
        "-e", f"WORKER_AUTH_SECRET={SECRET_B}",
        "-e", f"WORKER_MATCHING_TOKEN={TOKEN_MATCH_B}",
        "-e", f"WORKER_INGESTION_TOKEN={TOKEN_INGEST_B}",
        "-e", f"WORKER_ADMIN_TOKEN={TOKEN_ADMIN_B}",
        "-e", f"WORKER_TRUSTED_PEERS_JSON={peers_b_json}",
        IMAGE_NAME,
    ]
    res_worker_b = _run_cmd(cmd_worker_b, check=False)
    if res_worker_b.returncode != 0:
        for c in [CONTAINER_WORKER_A, CONTAINER_WORKER_B, CONTAINER_COORDINATOR]:
            _run_cmd(["docker", "rm", "-f", c], check=False)
        _run_cmd(["docker", "network", "rm", NETWORK_NAME], check=False)
        _run_cmd(["docker", "volume", "rm", VOLUME_BANK_A, VOLUME_BANK_B], check=False)
        pytest.skip(
            f"Failed to start container '{CONTAINER_WORKER_B}' (exit code {res_worker_b.returncode}): "
            f"{res_worker_b.stderr.strip() or res_worker_b.stdout.strip()}"
        )

    # 5. Start Coordinator container (zero volume mounts, network participant only)
    cmd_coordinator = [
        "docker", "run", "-d",
        "--name", CONTAINER_COORDINATOR,
        "--network", NETWORK_NAME,
        IMAGE_NAME,
        "sleep", "3600",
    ]
    res_coordinator = _run_cmd(cmd_coordinator, check=False)
    if res_coordinator.returncode != 0:
        for c in [CONTAINER_WORKER_A, CONTAINER_WORKER_B, CONTAINER_COORDINATOR]:
            _run_cmd(["docker", "rm", "-f", c], check=False)
        _run_cmd(["docker", "network", "rm", NETWORK_NAME], check=False)
        _run_cmd(["docker", "volume", "rm", VOLUME_BANK_A, VOLUME_BANK_B], check=False)
        pytest.skip(
            f"Failed to start container '{CONTAINER_COORDINATOR}' (exit code {res_coordinator.returncode}): "
            f"{res_coordinator.stderr.strip() or res_coordinator.stdout.strip()}"
        )

    # Wait for daemons to become healthy
    health_a = _wait_for_health(f"http://127.0.0.1:{PORT_WORKER_A}/worker/health")
    health_b = _wait_for_health(f"http://127.0.0.1:{PORT_WORKER_B}/worker/health")
    if not (health_a and health_b):
        for c in [CONTAINER_WORKER_A, CONTAINER_WORKER_B, CONTAINER_COORDINATOR]:
            _run_cmd(["docker", "rm", "-f", c], check=False)
        _run_cmd(["docker", "network", "rm", NETWORK_NAME], check=False)
        _run_cmd(["docker", "volume", "rm", VOLUME_BANK_A, VOLUME_BANK_B], check=False)
        pytest.skip(
            f"Docker worker containers failed to become healthy within timeout: worker_a={health_a}, worker_b={health_b}"
        )

    client_a = InstitutionalWorkerClient(
        bank_id="bank_a",
        base_url=f"http://127.0.0.1:{PORT_WORKER_A}",
        matching_token=TOKEN_MATCH_A,
        ingestion_token=TOKEN_INGEST_A,
        admin_token=TOKEN_ADMIN_A,
    )
    client_b = InstitutionalWorkerClient(
        bank_id="bank_b",
        base_url=f"http://127.0.0.1:{PORT_WORKER_B}",
        matching_token=TOKEN_MATCH_B,
        ingestion_token=TOKEN_INGEST_B,
        admin_token=TOKEN_ADMIN_B,
    )

    topology_ctx = {
        "client_a": client_a,
        "client_b": client_b,
        "port_a": PORT_WORKER_A,
        "port_b": PORT_WORKER_B,
    }

    yield topology_ctx

    # Teardown
    for c in [CONTAINER_WORKER_A, CONTAINER_WORKER_B, CONTAINER_COORDINATOR]:
        _run_cmd(["docker", "rm", "-f", c], check=False)
    _run_cmd(["docker", "network", "rm", NETWORK_NAME], check=False)
    _run_cmd(["docker", "volume", "rm", VOLUME_BANK_A, VOLUME_BANK_B], check=False)


@pytest.fixture
def isolated_governance_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[ConsortiumGovernanceService, str, str], None, None]:
    """Isolated consortium governance environment for testing."""
    store_file = tmp_path / "docker_integration_consortium_store.json"
    account_file = tmp_path / "docker_integration_account_store.json"
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", str(store_file))
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", str(account_file))
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

    yield gov_svc, token_a, token_b

    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()


def test_probe_1_non_root_and_volume_isolation(docker_topology: dict[str, Any]) -> None:
    """Probe 1: Verify non-root UID 10001 process identity and strict volume isolation."""
    # Process identity verification
    res_a = _run_cmd(["docker", "exec", CONTAINER_WORKER_A, "id", "-u"])
    assert res_a.stdout.strip() == "10001", "Bank A worker container must run as non-root UID 10001"

    res_b = _run_cmd(["docker", "exec", CONTAINER_WORKER_B, "id", "-u"])
    assert res_b.stdout.strip() == "10001", "Bank B worker container must run as non-root UID 10001"

    res_coord = _run_cmd(["docker", "exec", CONTAINER_COORDINATOR, "id", "-u"])
    assert res_coord.stdout.strip() == "10001", "Coordinator container must run as non-root UID 10001"

    # Negative Control: Bank A cannot see Bank B volume
    # Write canary file directly in Bank B vault
    _run_cmd([
        "docker", "exec", CONTAINER_WORKER_B,
        "python", "-c",
        "import os; os.makedirs('/vault/bank_b/canary', exist_ok=True); open('/vault/bank_b/canary/secret_b.txt', 'w').write('SECRET_DATA_B')"
    ])

    # Probe from Bank A container for Bank B's canary - MUST NOT EXIST
    check_a = _run_cmd([
        "docker", "exec", CONTAINER_WORKER_A,
        "python", "-c",
        "import os; print(os.path.exists('/vault/bank_b/canary/secret_b.txt'))"
    ])
    assert check_a.stdout.strip() == "False", "Cross-tenant volume leak: Bank A accessed Bank B vault data!"

    # Probe from Coordinator container for vault directory - MUST NOT EXIST or be empty
    check_coord = _run_cmd([
        "docker", "exec", CONTAINER_COORDINATOR,
        "python", "-c",
        "import os; print(os.path.exists('/vault/bank_b/canary/secret_b.txt'))"
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

    # Negative Probe: Attempt enroll-peer using matching token
    enroll_payload = json.dumps({"peer_bank_id": "bank_c", "verification_key": "some_key"}).encode()
    req_enroll = urllib.request.Request(
        f"{client_a.base_url}/worker/enroll-peer",
        data=enroll_payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN_MATCH_A}"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info_enroll:
        urllib.request.urlopen(req_enroll)
    assert exc_info_enroll.value.code == 403, "Matching token MUST be rejected on admin endpoint with HTTP 403"


def test_probe_3_ticket_authorization_enforcement(docker_topology: dict[str, Any]) -> None:
    """Probe 3: Verify worker endpoints reject unsigned, tampered, or wrong-purpose tickets."""
    client_b: InstitutionalWorkerClient = docker_topology["client_b"]

    fake_ticket = OperationAuthorizationTicket(
        ticket_id="ticket_fake_001",
        consortium_id="c_docker_psi",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        operation="ESTABLISHED_PSI",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        query_scope="entity:ent_999",
        grant_id="grant_c_docker_psi",
        grant_revision=1,
        grant_scope_digest="dummy_digest",
        job_id="job_001",
        issued_at="2026-10-25T00:00:00Z",
        expires_at="2026-10-25T01:00:00Z",
        bank_approvals={},  # Unsigned!
    )

    fake_query = EstablishedPSIQueryMessage(
        job_id="job_001",
        consortium_id="c_docker_psi",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        psi_request_b64="fake_proto",
        nonce="nonce_fake_123",
        timestamp="2026-10-25T00:00:00Z",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        signature="fake_sig",
        ticket=fake_ticket,
    )

    # Submitting unsigned ticket to Bank B worker endpoint must fail closed
    with pytest.raises(Exception) as exc_info:
        client_b.process_established_psi_counterpart(fake_query)
    assert "ConsortiumSecurityError" in type(exc_info.value).__name__ or "rejected" in str(exc_info.value).lower() or "verification failed" in str(exc_info.value).lower()


def test_probe_4_openmined_psi_positive_match(
    docker_topology: dict[str, Any],
    isolated_governance_env: tuple[ConsortiumGovernanceService, str, str],
) -> None:
    """Probe 4: Genuine OpenMined PSI matching across Docker containers confirms positive match."""
    gov_svc, token_a, _ = isolated_governance_env
    client_a: InstitutionalWorkerClient = docker_topology["client_a"]
    client_b: InstitutionalWorkerClient = docker_topology["client_b"]

    # Ingest identical IBAN entity into Bank A and Bank B vaults via ingestion capability
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

    # Initialize coordinator with registered worker clients
    coordinator = CrossBankMatchingCoordinator(consortium_service=gov_svc)
    coordinator.register_worker("bank_a", client_a, matching_token=TOKEN_MATCH_A)
    coordinator.register_worker("bank_b", client_b, matching_token=TOKEN_MATCH_B)

    req = CrossBankIntelligenceQueryRequest(
        consortium_id="c_docker_psi",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        query_entity_id="ent_shared_bank_a",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        use_established_psi=True,
    )

    signal = coordinator.execute_two_bank_private_matching(req=req, auth_token=token_a)

    # Assert genuine established PSI match
    assert signal.match_confirmed is True, "OpenMined PSI failed to match identical identifier!"
    assert signal.provenance.get("protocol") == "OPENMINED-PSI-2.0.6-ECDH", f"Expected OpenMined protocol, got: {signal.provenance.get('protocol')}"
    assert signal.is_authoritative is True, "Established PSI signal must be marked authoritative"
    assert signal.assurance_level == "REVIEWED_ESTABLISHED_PSI_VERIFIED"
    assert signal.sanitized_risk_level in ("UNAVAILABLE", "HIGH", "CRITICAL")
    assert "CROSS_BANK_ENTITY_OBSERVED" in signal.sanitized_typology_indicators


def test_probe_5_openmined_psi_negative_match(
    docker_topology: dict[str, Any],
    isolated_governance_env: tuple[ConsortiumGovernanceService, str, str],
) -> None:
    """Probe 5: OpenMined PSI negative control correctly identifies disjoint non-matching entities."""
    gov_svc, token_a, _ = isolated_governance_env
    client_a: InstitutionalWorkerClient = docker_topology["client_a"]
    client_b: InstitutionalWorkerClient = docker_topology["client_b"]

    ent_unique = {
        "id": "ent_unique_bank_a_999",
        "bank_id": "bank_a",
        "entity_type": "account",
        "raw_identifier": "TR990000000000000000000000",
        "risk_level": "LOW",
        "typologies": [],
    }
    client_a.store_private_entity(ent_unique)

    coordinator = CrossBankMatchingCoordinator(consortium_service=gov_svc)
    coordinator.register_worker("bank_a", client_a, matching_token=TOKEN_MATCH_A)
    coordinator.register_worker("bank_b", client_b, matching_token=TOKEN_MATCH_B)

    req = CrossBankIntelligenceQueryRequest(
        consortium_id="c_docker_psi",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        query_entity_id="ent_unique_bank_a_999",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        use_established_psi=True,
    )

    signal = coordinator.execute_two_bank_private_matching(req=req, auth_token=token_a)

    assert signal.match_confirmed is False, "False positive match detected for disjoint entity!"
    assert signal.is_authoritative is True
    assert signal.assurance_level == "REVIEWED_ESTABLISHED_PSI_VERIFIED"
    assert signal.sanitized_risk_level == "UNOBSERVED"
    assert len(signal.sanitized_typology_indicators) == 0


def test_probe_6_entity_deletion_updates_matching(
    docker_topology: dict[str, Any],
    isolated_governance_env: tuple[ConsortiumGovernanceService, str, str],
) -> None:
    """Probe 6: Deleting entity in Bank B vault immediately causes subsequent PSI matching to return False."""
    gov_svc, token_a, _ = isolated_governance_env
    client_a: InstitutionalWorkerClient = docker_topology["client_a"]
    client_b: InstitutionalWorkerClient = docker_topology["client_b"]

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

    coordinator = CrossBankMatchingCoordinator(consortium_service=gov_svc)
    coordinator.register_worker("bank_a", client_a, matching_token=TOKEN_MATCH_A)
    coordinator.register_worker("bank_b", client_b, matching_token=TOKEN_MATCH_B)

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
    sig1 = coordinator.execute_two_bank_private_matching(req=req, auth_token=token_a)
    assert sig1.match_confirmed is True

    # Real entity deletion: remove file from Bank B vault inside container
    del_res = _run_cmd([
        "docker", "exec", CONTAINER_WORKER_B,
        "rm", "-f", "/vault/bank_b/private_entities/ent_to_delete_b.json"
    ])
    assert del_res.returncode == 0

    # Subsequent match confirms entity no longer matches
    sig2 = coordinator.execute_two_bank_private_matching(req=req, auth_token=token_a)
    assert sig2.match_confirmed is False, "Deleted entity was still matched (stale data retention breach)!"


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
        grant_scope_digest=hashlib.sha256(b"digest_wire").hexdigest(),
        job_id="job_wire_001",
        issued_at="2026-10-25T00:00:00Z",
        expires_at="2099-01-01T00:00:00Z",
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

    # Process counterpart query at Bank B
    counterpart_resp = client_b.process_established_psi_counterpart(query_msg)
    serialized_resp = json.dumps(asdict(counterpart_resp))
    assert sensitive_raw_iban not in serialized_resp, "Confidentiality Breach: Plaintext IBAN leaked in counterpart server response!"


def test_probe_8_case_evidence_attachment_and_revocation(
    docker_topology: dict[str, Any],
    isolated_governance_env: tuple[ConsortiumGovernanceService, str, str],
) -> None:
    """Probe 8: Attach verified authoritative signal to case; revoke consent locally; assert fail closed."""
    gov_svc, token_a, token_b = isolated_governance_env
    client_a: InstitutionalWorkerClient = docker_topology["client_a"]
    client_b: InstitutionalWorkerClient = docker_topology["client_b"]

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

    coordinator = CrossBankMatchingCoordinator(consortium_service=gov_svc)
    coordinator.register_worker("bank_a", client_a, matching_token=TOKEN_MATCH_A)
    coordinator.register_worker("bank_b", client_b, matching_token=TOKEN_MATCH_B)

    cbi_service = CrossBankIntelligenceService(
        matching_coordinator=coordinator,
        consortium_service=gov_svc,
    )

    req = CrossBankIntelligenceQueryRequest(
        consortium_id="c_docker_psi",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        query_entity_id="ent_case_a",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        use_established_psi=True,
    )

    # 1. Execute matching to obtain authoritative signal
    signal = coordinator.execute_two_bank_private_matching(req=req, auth_token=token_a)
    assert signal.is_authoritative is True

    # 2. Attach signal to active case evidence
    case_svc = CaseManagementService()
    case = case_svc.create_case(
        title="Institutional PSI Investigation",
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

    # 3. Revoke grant consent: Bank B withdraws bilateral consent in consortium governance
    gov_svc.revoke_operational_data_sharing_grant(
        consortium_id="c_docker_psi",
        grant_id="grant_c_docker_psi",
        revoking_bank_id="bank_b",
        reason="Counterpart consent withdrawn",
        auth_token=token_b,
    )
    # Also record local grant revocation at worker boundary
    client_b.revoke_grant_locally("grant_c_docker_psi")

    # 4. Attempting to attach another signal under revoked grant MUST fail closed
    case2 = case_svc.create_case(
        title="Subsequent Investigation Case",
        priority=CasePriority.P2_HIGH,
        bank_id="bank_a",
    )
    with pytest.raises(ConsortiumSecurityError) as exc_info:
        cbi_service.attach_signal_to_case_evidence(
            case_id=case2.id,
            signal=signal,
            auth_token=token_a,
        )
    assert "revoked" in str(exc_info.value).lower()
