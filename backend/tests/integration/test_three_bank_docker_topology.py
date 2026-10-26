"""Three-Bank Institutional Docker Topology Integration Test Suite.

Establishes and verifies a reproducible, isolated 4-container topology:
- Security Domain 1: Bank A matching worker (`cfi_worker_a_<session_id>`), non-root UID 10001, isolated vault `cfi_vault_a_<session_id>:/vault`.
- Security Domain 2: Bank B matching worker (`cfi_worker_b_<session_id>`), non-root UID 10001, isolated vault `cfi_vault_b_<session_id>:/vault`.
- Security Domain 3: Bank C matching worker (`cfi_worker_c_<session_id>`), non-root UID 10001, isolated vault `cfi_vault_c_<session_id>:/vault`.
- Security Domain 4: Consortium coordinator (`cfi_coord_<session_id>`), non-root UID 10001, zero vault mounts.

Verification Matrix:
- Probe 1: Four genuine Docker containers start, pass readiness, and expose authentic health states.
- Probe 2: Independent institutional sovereign identities (Ed25519) and verified cryptographic readiness signatures.
- Probe 3: Coordinator topology metadata registers all 3 institutional workers.
- Probe 4: Strict 3-way vault isolation and coordinator vault denial (positive controls + negative controls across all pairs).
- Probe 5: Non-root process execution (UID 10001) across all four containers.
- Probe 6: Credential and capability scope isolation for Bank C (matching vs ingestion vs admin).
- Probe 7: Full pairwise peer trust configuration across all three institutional workers.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from app.domain.institutional_identity import (
    canonical_worker_readiness_bytes,
)
from tests.integration.conftest import (
    TOKEN_ADMIN_C,
    TOKEN_INGEST_C,
    TOKEN_MATCH_C,
    _run_cmd,
)


def test_four_container_topology_readiness(three_bank_docker_topology: dict[str, Any]) -> None:
    """Verify that all four genuine Docker containers start successfully and report healthy status."""
    containers = three_bank_docker_topology["containers"]
    clients = three_bank_docker_topology["clients"]
    client_coord = three_bank_docker_topology["client_coord"]

    assert len(containers) == 4, f"Expected 4 containers, got {len(containers)}"
    for role, c_name in containers.items():
        res = _run_cmd(["docker", "inspect", "-f", "{{.State.Running}}", c_name])
        assert res.stdout.strip() == "true", f"Container '{c_name}' ({role}) is not running"

    # Verify Bank A, B, C health probes
    for bank_id, client in clients.items():
        health = client.health_check()
        assert health["status"] == "ONLINE", f"{bank_id} status is not ONLINE: {health}"
        assert health["role"] == "worker", f"{bank_id} role is not worker: {health}"
        assert health["bank_id"] == bank_id, f"{bank_id} reported incorrect bank_id: {health}"
        assert health["protocol"] == "OPENMINED-PSI-2.0.6-ECDH"

    # Verify Coordinator health probe
    coord_health = client_coord.health_check()
    assert coord_health["status"] == "ONLINE"
    assert coord_health["role"] == "coordinator"


def test_independent_institutional_identities_and_readiness_signatures(
    three_bank_docker_topology: dict[str, Any],
) -> None:
    """Verify each bank worker exposes an authentic, distinct Ed25519 identity and valid readiness signature."""
    clients = three_bank_docker_topology["clients"]
    idents = three_bank_docker_topology["idents"]

    observed_keys: dict[str, str] = {}

    for bank_id, client in clients.items():
        # Query public key endpoint directly
        pub_key = client.get_public_key()
        assert len(pub_key) == 64, f"Invalid Ed25519 public key hex length: {len(pub_key)}"
        assert pub_key == idents[bank_id].public_key_hex, (
            f"Public key mismatch for {bank_id}: expected {idents[bank_id].public_key_hex}, got {pub_key}"
        )
        observed_keys[bank_id] = pub_key

        # Query health endpoint with fresh nonce to verify readiness signature
        nonce = f"probe_nonce_{bank_id}_9827341"
        url = f"{client.base_url}/worker/health?nonce={nonce}"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        sig_hex = data.get("signature", "")
        assert sig_hex, f"Missing readiness signature in {bank_id} health response"

        # Verify cryptographic signature using Bank's institutional public key
        canonical_stmt = canonical_worker_readiness_bytes(
            bank_id=bank_id,
            nonce=nonce,
            timestamp=data["timestamp"],
            protocol=data["protocol"],
        )
        is_valid = idents[bank_id].verify(
            signature_hex=sig_hex,
            message=canonical_stmt,
        )
        assert is_valid is True, f"Cryptographic readiness signature verification failed for {bank_id}"

    # Verify strict institutional sovereignty: each bank has a unique, distinct key
    unique_keys = set(observed_keys.values())
    assert len(unique_keys) == 3, f"Expected 3 distinct institutional public keys, got {len(unique_keys)}"
    assert observed_keys["bank_a"] != observed_keys["bank_b"]
    assert observed_keys["bank_b"] != observed_keys["bank_c"]
    assert observed_keys["bank_a"] != observed_keys["bank_c"]


def test_coordinator_registered_topology_metadata(
    three_bank_docker_topology: dict[str, Any],
) -> None:
    """Verify coordinator daemon registers all three institutional workers in its topology metadata."""
    client_coord = three_bank_docker_topology["client_coord"]

    topology = client_coord.get_topology()
    assert topology["status"] == "ONLINE"
    assert topology["role"] == "coordinator"

    registered = topology.get("registered_workers", [])
    assert sorted(registered) == ["bank_a", "bank_b", "bank_c"], (
        f"Coordinator does not list all 3 workers: {registered}"
    )


def test_three_way_vault_isolation_and_coordinator_denial(
    three_bank_docker_topology: dict[str, Any],
) -> None:
    """Verify strict three-way vault isolation and confirm coordinator has zero vault mounts."""
    c_a = three_bank_docker_topology["container_worker_a"]
    c_b = three_bank_docker_topology["container_worker_b"]
    c_c = three_bank_docker_topology["container_worker_c"]
    c_coord = three_bank_docker_topology["container_coordinator"]
    session_id = three_bank_docker_topology["session_id"]

    # 1. Seed unique canaries in each institutional vault
    canaries = {
        "bank_a": (c_a, f"/vault/bank_a/canary/secret_a_{session_id}.txt", f"CONFIDENTIAL_A_{session_id}"),
        "bank_b": (c_b, f"/vault/bank_b/canary/secret_b_{session_id}.txt", f"CONFIDENTIAL_B_{session_id}"),
        "bank_c": (c_c, f"/vault/bank_c/canary/secret_c_{session_id}.txt", f"CONFIDENTIAL_C_{session_id}"),
    }

    for b_id, (container, path, content) in canaries.items():
        cmd = f"import os; os.makedirs(os.path.dirname('{path}'), exist_ok=True); open('{path}', 'w').write('{content}')"
        res = _run_cmd(["docker", "exec", container, "python", "-c", cmd])
        assert res.returncode == 0, f"Failed to seed canary in {b_id}: {res.stderr}"

    # 2. Positive Controls: each bank CAN read its own canary
    for b_id, (container, path, content) in canaries.items():
        cmd = f"import os; print(open('{path}').read().strip() if os.path.exists('{path}') else 'MISSING')"
        res = _run_cmd(["docker", "exec", container, "python", "-c", cmd])
        assert res.stdout.strip() == content, f"Positive control failed: {b_id} cannot read its own vault canary"

    # 3. Negative Controls: Cross-bank vault access is completely denied across all pairs
    worker_containers = {"bank_a": c_a, "bank_b": c_b, "bank_c": c_c}
    for observer_bank, observer_container in worker_containers.items():
        for target_bank, (_, target_path, _) in canaries.items():
            if observer_bank == target_bank:
                continue
            cmd = f"import os; print(os.path.exists('{target_path}'))"
            res = _run_cmd(["docker", "exec", observer_container, "python", "-c", cmd])
            assert res.stdout.strip() == "False", (
                f"CRITICAL VAULT LEAK: {observer_bank} was able to access {target_bank} vault data at '{target_path}'"
            )

    # 4. Negative Controls: Coordinator possesses ZERO access to any bank vault
    for target_bank, (_, target_path, _) in canaries.items():
        cmd = f"import os; print(os.path.exists('{target_path}'))"
        res = _run_cmd(["docker", "exec", c_coord, "python", "-c", cmd])
        assert res.stdout.strip() == "False", (
            f"CRITICAL COORDINATOR LEAK: Coordinator illegally accessed {target_bank} vault at '{target_path}'"
        )

    # 5. Confirm coordinator container has no volume mount for /vault
    inspect_res = _run_cmd([
        "docker", "inspect", "-f", "{{range .Mounts}}{{.Destination}} {{end}}", c_coord
    ])
    destinations = inspect_res.stdout.strip().split()
    assert "/vault" not in destinations, f"Coordinator container has illegal /vault mount: {destinations}"


def test_non_root_execution_across_all_four_containers(
    three_bank_docker_topology: dict[str, Any],
) -> None:
    """Verify that all four containers execute strictly as non-root UID 10001."""
    containers = three_bank_docker_topology["containers"]
    for role, c_name in containers.items():
        res = _run_cmd(["docker", "exec", c_name, "id", "-u"])
        assert res.stdout.strip() == "10001", (
            f"Container '{c_name}' ({role}) is not running as non-root UID 10001: got '{res.stdout.strip()}'"
        )


def test_bank_c_credential_and_capability_isolation(
    three_bank_docker_topology: dict[str, Any],
) -> None:
    """Verify capability-based authorization scope enforcement on Bank C worker."""
    client_c = three_bank_docker_topology["client_c"]
    assert client_c.matching_token == TOKEN_MATCH_C
    assert client_c.ingestion_token == TOKEN_INGEST_C
    assert client_c.admin_token == TOKEN_ADMIN_C

    # Negative Probe 1: Matching token cannot store private entity (requires ingestion scope)
    raw_payload = json.dumps({
        "entity": {"id": "ent_c_test", "bank_id": "bank_c", "entity_type": "customer"}
    }).encode("utf-8")
    req_store = urllib.request.Request(
        f"{client_c.base_url}/worker/store-entity",
        data=raw_payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN_MATCH_C}"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_store:
        urllib.request.urlopen(req_store)
    assert exc_store.value.code == 403, (
        f"Bank C matching token on store-entity must return HTTP 403, got {exc_store.value.code}"
    )

    # Negative Probe 2: Matching token cannot authorize grant (requires admin scope)
    auth_payload = json.dumps({
        "consortium_id": "c_three_bank",
        "grant_id": "grant_c_test",
        "revision": 1,
        "scope_digest": "0" * 64,
        "permitted_counterparts": ["bank_a", "bank_b"],
        "permitted_purposes": ["AML_COLLABORATIVE_INVESTIGATION"],
    }).encode("utf-8")
    req_auth = urllib.request.Request(
        f"{client_c.base_url}/worker/authorize-grant",
        data=auth_payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN_MATCH_C}"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_auth:
        urllib.request.urlopen(req_auth)
    assert exc_auth.value.code == 403, (
        f"Bank C matching token on authorize-grant must return HTTP 403, got {exc_auth.value.code}"
    )

    # Positive Probe 1: Ingestion token successfully stores private entity
    ent_id = client_c.store_private_entity({
        "id": "ent_c_valid_001",
        "bank_id": "bank_c",
        "entity_type": "customer",
        "features": {"risk_score": 0.85},
    })
    assert ent_id == "ent_c_valid_001", f"Expected stored entity ID 'ent_c_valid_001', got '{ent_id}'"

    # Positive Probe 2: Admin token successfully registers authorized grant
    grant_resp = client_c.register_authorized_grant(
        consortium_id="c_three_bank",
        grant_id="grant_c_authorized_001",
        revision=1,
        scope_digest="a" * 64,
        permitted_counterparts=["bank_a", "bank_b"],
        permitted_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
    )
    assert grant_resp.get("grant_id") == "grant_c_authorized_001"


def test_pairwise_worker_peer_trust_matrix(
    three_bank_docker_topology: dict[str, Any],
) -> None:
    """Verify each bank worker container is provisioned with sovereign peer keys for both counterparties."""
    c_a = three_bank_docker_topology["container_worker_a"]
    c_b = three_bank_docker_topology["container_worker_b"]
    c_c = three_bank_docker_topology["container_worker_c"]
    idents = three_bank_docker_topology["idents"]

    for c_name, expected_peers in [
        (c_a, {"bank_b": idents["bank_b"].public_key_hex, "bank_c": idents["bank_c"].public_key_hex}),
        (c_b, {"bank_a": idents["bank_a"].public_key_hex, "bank_c": idents["bank_c"].public_key_hex}),
        (c_c, {"bank_a": idents["bank_a"].public_key_hex, "bank_b": idents["bank_b"].public_key_hex}),
    ]:
        cmd = "import os; print(os.environ.get('WORKER_TRUSTED_PEERS_JSON', '{}'))"
        res = _run_cmd(["docker", "exec", c_name, "python", "-c", cmd])
        peers = json.loads(res.stdout.strip())
        assert peers == expected_peers, f"Peer trust matrix mismatch in container {c_name}"
