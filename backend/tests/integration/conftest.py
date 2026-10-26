"""Shared integration test fixtures and Docker topology for institutional testing."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from app.application.services.isolated_matching_service import (
    InstitutionalCoordinatorClient,
    InstitutionalWorkerClient,
)
from app.domain.institutional_identity import InstitutionalSigningIdentity

if TYPE_CHECKING:
    from collections.abc import Generator

IMAGE_NAME = "cfi-institutional-worker:latest"

SECRET_A = "auth_sec_bank_a_87234691823749123847"
SECRET_B = "auth_sec_bank_b_98127394812739481273"
SECRET_C = "auth_sec_bank_c_73629104826194726194"
TOKEN_MATCH_A = "matching_token_bank_a_3847291847"
TOKEN_MATCH_B = "matching_token_bank_b_9182739182"
TOKEN_MATCH_C = "matching_token_bank_c_4719283741"
TOKEN_INGEST_A = "ingestion_token_bank_a_12938471"
TOKEN_INGEST_B = "ingestion_token_bank_b_58271947"
TOKEN_INGEST_C = "ingestion_token_bank_c_91827364"
TOKEN_ADMIN_A = "admin_token_bank_a_987123984"
TOKEN_ADMIN_B = "admin_token_bank_b_123987123"
TOKEN_ADMIN_C = "admin_token_bank_c_827361928"
SECRET_COORD = "auth_sec_coord_99912384719283749123"
SHARED_CONSORTIUM_STORE_SECRET = "consortium_store_hmac_shared_sec_888777666555444333"
SHARED_JWT_SECRET = "jwt_secret_test_suite_production_hardened_98712349812734918237"

INSTITUTION_CREDENTIALS: dict[str, dict[str, str]] = {
    "bank_a": {
        "secret": SECRET_A,
        "token_match": TOKEN_MATCH_A,
        "token_ingest": TOKEN_INGEST_A,
        "token_admin": TOKEN_ADMIN_A,
    },
    "bank_b": {
        "secret": SECRET_B,
        "token_match": TOKEN_MATCH_B,
        "token_ingest": TOKEN_INGEST_B,
        "token_admin": TOKEN_ADMIN_B,
    },
    "bank_c": {
        "secret": SECRET_C,
        "token_match": TOKEN_MATCH_C,
        "token_ingest": TOKEN_INGEST_C,
        "token_admin": TOKEN_ADMIN_C,
    },
}

# Mutable globals populated per session to guarantee disposable uniqueness
NETWORK_NAME: str = ""
CONTAINER_WORKER_A: str = ""
CONTAINER_WORKER_B: str = ""
CONTAINER_WORKER_C: str = ""
CONTAINER_COORDINATOR: str = ""
VOLUME_BANK_A: str = ""
VOLUME_BANK_B: str = ""
VOLUME_BANK_C: str = ""
PORT_WORKER_A: int = 0
PORT_WORKER_B: int = 0
PORT_WORKER_C: int = 0
PORT_COORDINATOR: int = 0


def _get_free_port() -> int:
    """Acquire an ephemeral port from the host OS."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


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


def _wait_for_health(url: str, timeout_sec: float = 30.0) -> bool:
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


def _teardown_disposable_resources() -> None:
    """Tears down ONLY the test-owned disposable resources created for this test run."""
    for c in [CONTAINER_WORKER_A, CONTAINER_WORKER_B, CONTAINER_WORKER_C, CONTAINER_COORDINATOR]:
        if c:
            _run_cmd(["docker", "rm", "-f", c], check=False)
    if NETWORK_NAME:
        _run_cmd(["docker", "network", "rm", NETWORK_NAME], check=False)
    for v in [VOLUME_BANK_A, VOLUME_BANK_B, VOLUME_BANK_C]:
        if v:
            _run_cmd(["docker", "volume", "rm", v], check=False)


def _launch_topology_session(banks: list[str]) -> Generator[dict[str, Any], None, None]:
    """Launch disposable Docker topology for specified banks plus one coordinator."""
    if not is_docker_running():
        pytest.skip("Docker daemon is not reachable or running.")

    if not ensure_docker_image(IMAGE_NAME):
        pytest.skip(
            f"Required Docker image '{IMAGE_NAME}' is not available and could not be prepared automatically."
        )

    session_id = uuid.uuid4().hex[:8]
    network_name = f"cfi_mesh_{session_id}"
    container_coordinator = f"cfi_coord_{session_id}"
    port_coordinator = _get_free_port()

    created_containers: list[str] = []
    created_volumes: list[str] = []
    created_networks: list[str] = []

    def _teardown_session() -> None:
        for c in created_containers:
            _run_cmd(["docker", "rm", "-f", c], check=False)
        if network_name in created_networks:
            _run_cmd(["docker", "network", "rm", network_name], check=False)
        for v in created_volumes:
            _run_cmd(["docker", "volume", "rm", v], check=False)

    # 1. Create isolated bridge mesh network
    net_res = _run_cmd(["docker", "network", "create", network_name], check=False)
    if net_res.returncode != 0:
        pytest.skip(f"Failed to create docker network '{network_name}': {net_res.stderr.strip()}")
    created_networks.append(network_name)

    # 2. Setup volumes and sovereign identities for each bank
    bank_containers: dict[str, str] = {}
    bank_volumes: dict[str, str] = {}
    bank_ports: dict[str, int] = {}
    bank_idents: dict[str, InstitutionalSigningIdentity] = {}

    for b in banks:
        letter = b[-1]
        vol_name = f"cfi_vault_{letter}_{session_id}"
        vol_res = _run_cmd(["docker", "volume", "create", vol_name], check=False)
        if vol_res.returncode != 0:
            _teardown_session()
            pytest.skip(f"Failed to create docker volume '{vol_name}': {vol_res.stderr.strip()}")
        created_volumes.append(vol_name)
        bank_volumes[b] = vol_name
        bank_containers[b] = f"cfi_worker_{letter}_{session_id}"
        bank_ports[b] = _get_free_port()
        bank_idents[b] = InstitutionalSigningIdentity.generate(b)

    backend_dir = Path(__file__).resolve().parents[2]

    # 3. Start each institutional worker container
    for b in banks:
        ident = bank_idents[b]
        creds = INSTITUTION_CREDENTIALS[b]
        c_name = bank_containers[b]
        vol_name = bank_volumes[b]
        port = bank_ports[b]
        peers = {other: bank_idents[other].public_key_hex for other in banks if other != b}
        peers_json = json.dumps(peers)

        cmd_worker = [
            "docker", "run", "-d",
            "--name", c_name,
            "--network", network_name,
            "-v", f"{vol_name}:/vault",
            "-v", f"{backend_dir.as_posix()}:/app/backend:ro",
            "-p", f"{port}:8100",
            "-e", "APP_ENV=production",
            "-e", f"JWT_SECRET={SHARED_JWT_SECRET}",
            "-e", f"CFI_JWT_SECRET={SHARED_JWT_SECRET}",
            "-e", f"CFI_JWT_SIGNING_SECRET={SHARED_JWT_SECRET}",
            "-e", f"CFI_OIDC_JWT_SIGNING_SECRET={SHARED_JWT_SECRET}",
            "-e", f"CFI_IDENTITY_STORE_SECRET={SHARED_JWT_SECRET}",
            "-e", f"CFI_AUDIT_LEDGER_SECRET={SHARED_JWT_SECRET}",
            "-e", "CFI_ACCOUNT_STORE_PATH=/tmp/account_store.json",
            "-e", "CFI_IDENTITY_STORE_PATH=/tmp/account_store.json",
            "-e", f"BANK_ID={b}",
            "-e", "STORAGE_ROOT=/vault",
            "-e", "CFI_STORAGE_DIR=/vault",
            "-e", "CFI_CONSORTIUM_STORE_PATH=/tmp/consortium_store.json",
            "-e", f"CFI_CONSORTIUM_STORE_SECRET={SHARED_CONSORTIUM_STORE_SECRET}",
            "-e", "WORKER_HOST=0.0.0.0",
            "-e", "WORKER_PORT=8100",
            "-e", f"WORKER_AUTH_SECRET={creds['secret']}",
            "-e", f"WORKER_SIGNING_KEY={ident.private_key_hex}",
            "-e", f"WORKER_MATCHING_TOKEN={creds['token_match']}",
            "-e", f"WORKER_INGESTION_TOKEN={creds['token_ingest']}",
            "-e", f"WORKER_ADMIN_TOKEN={creds['token_admin']}",
            "-e", f"WORKER_TRUSTED_PEERS_JSON={peers_json}",
            IMAGE_NAME,
        ]
        res_worker = _run_cmd(cmd_worker, check=False)
        if res_worker.returncode != 0:
            _teardown_session()
            pytest.skip(f"Failed to start container '{c_name}': {res_worker.stderr.strip()}")
        created_containers.append(c_name)

    # 4. Start Coordinator container
    cmd_coordinator = [
        "docker", "run", "-d",
        "--name", container_coordinator,
        "--network", network_name,
        "-v", f"{backend_dir.as_posix()}:/app/backend:ro",
        "-p", f"{port_coordinator}:8200",
        "-e", "APP_ENV=production",
        "-e", f"JWT_SECRET={SHARED_JWT_SECRET}",
        "-e", f"CFI_JWT_SECRET={SHARED_JWT_SECRET}",
        "-e", f"CFI_JWT_SIGNING_SECRET={SHARED_JWT_SECRET}",
        "-e", f"CFI_OIDC_JWT_SIGNING_SECRET={SHARED_JWT_SECRET}",
        "-e", f"CFI_IDENTITY_STORE_SECRET={SHARED_JWT_SECRET}",
        "-e", f"CFI_AUDIT_LEDGER_SECRET={SHARED_JWT_SECRET}",
        "-e", "CFI_ACCOUNT_STORE_PATH=/tmp/account_store.json",
        "-e", "CFI_IDENTITY_STORE_PATH=/tmp/account_store.json",
        "-e", "CFI_CONSORTIUM_STORE_PATH=/tmp/consortium_store.json",
        "-e", f"CFI_CONSORTIUM_STORE_SECRET={SHARED_CONSORTIUM_STORE_SECRET}",
        "-e", f"CFI_COORDINATOR_SECRET={SECRET_COORD}",
        IMAGE_NAME,
        "python", "-m", "app.application.services.isolated_matching_service",
        "--role", "coordinator",
        "--host", "0.0.0.0",
        "--port", "8200",
    ]
    for b in banks:
        letter = b[-1]
        cmd_coordinator.extend([
            f"--worker-{letter}-bank", b,
            f"--worker-{letter}-url", f"http://{bank_containers[b]}:8100",
            f"--worker-{letter}-token", INSTITUTION_CREDENTIALS[b]["token_match"],
        ])

    res_coordinator = _run_cmd(cmd_coordinator, check=False)
    if res_coordinator.returncode != 0:
        _teardown_session()
        pytest.skip(f"Failed to start container '{container_coordinator}': {res_coordinator.stderr.strip()}")
    created_containers.append(container_coordinator)

    # 5. Wait for all daemons to become healthy
    worker_health_status: dict[str, bool] = {}
    for b in banks:
        worker_health_status[b] = _wait_for_health(f"http://127.0.0.1:{bank_ports[b]}/worker/health")
    coord_health_status = _wait_for_health(f"http://127.0.0.1:{port_coordinator}/health")

    if not (all(worker_health_status.values()) and coord_health_status):
        _teardown_session()
        pytest.skip(
            f"Containers failed to become healthy within timeout: workers={worker_health_status}, coordinator={coord_health_status}"
        )

    # 6. Instantiate clients
    clients: dict[str, InstitutionalWorkerClient] = {}
    for b in banks:
        clients[b] = InstitutionalWorkerClient(
            bank_id=b,
            base_url=f"http://127.0.0.1:{bank_ports[b]}",
            matching_token=INSTITUTION_CREDENTIALS[b]["token_match"],
            ingestion_token=INSTITUTION_CREDENTIALS[b]["token_ingest"],
            admin_token=INSTITUTION_CREDENTIALS[b]["token_admin"],
        )
    client_coord = InstitutionalCoordinatorClient(
        base_url=f"http://127.0.0.1:{port_coordinator}",
    )

    all_containers = {**bank_containers, "coordinator": container_coordinator}
    topology_ctx: dict[str, Any] = {
        "client_coord": client_coord,
        "port_coord": port_coordinator,
        "container_coordinator": container_coordinator,
        "network_name": network_name,
        "session_id": session_id,
        "clients": clients,
        "ports": bank_ports,
        "worker_containers": bank_containers,
        "containers": all_containers,
        "volumes": bank_volumes,
        "idents": bank_idents,
    }
    # Backward-compatible individual keys
    for b in banks:
        letter = b[-1]
        topology_ctx[f"client_{letter}"] = clients[b]
        topology_ctx[f"port_{letter}"] = bank_ports[b]
        topology_ctx[f"container_worker_{letter}"] = bank_containers[b]
        topology_ctx[f"volume_bank_{letter}"] = bank_volumes[b]
        topology_ctx[f"ident_{letter}"] = bank_idents[b]

    # Provision backend service verification public keys
    for b in banks:
        os.environ[f"CFI_INSTITUTION_PUBLIC_KEY_{b.upper()}"] = bank_idents[b].public_key_hex
    os.environ["CFI_COORDINATOR_SECRET"] = SECRET_COORD

    try:
        yield topology_ctx
    finally:
        for b in banks:
            os.environ.pop(f"CFI_INSTITUTION_PUBLIC_KEY_{b.upper()}", None)
        os.environ.pop("CFI_COORDINATOR_SECRET", None)
        _teardown_session()


@pytest.fixture(scope="module")
def docker_topology() -> Generator[dict[str, Any], None, None]:
    """Sets up genuine 3-container topology on isolated bridge network using disposable test-owned resources."""
    global NETWORK_NAME, CONTAINER_WORKER_A, CONTAINER_WORKER_B, CONTAINER_COORDINATOR
    global VOLUME_BANK_A, VOLUME_BANK_B, PORT_WORKER_A, PORT_WORKER_B, PORT_COORDINATOR

    for topology_ctx in _launch_topology_session(banks=["bank_a", "bank_b"]):
        NETWORK_NAME = topology_ctx["network_name"]
        CONTAINER_WORKER_A = topology_ctx["container_worker_a"]
        CONTAINER_WORKER_B = topology_ctx["container_worker_b"]
        CONTAINER_COORDINATOR = topology_ctx["container_coordinator"]
        VOLUME_BANK_A = topology_ctx["volume_bank_a"]
        VOLUME_BANK_B = topology_ctx["volume_bank_b"]
        PORT_WORKER_A = topology_ctx["port_a"]
        PORT_WORKER_B = topology_ctx["port_b"]
        PORT_COORDINATOR = topology_ctx["port_coord"]
        yield topology_ctx


@pytest.fixture(scope="module")
def three_bank_docker_topology() -> Generator[dict[str, Any], None, None]:
    """Sets up genuine 4-container topology (Bank A, B, C + Coordinator) on isolated bridge network."""
    global NETWORK_NAME, CONTAINER_WORKER_A, CONTAINER_WORKER_B, CONTAINER_WORKER_C, CONTAINER_COORDINATOR
    global VOLUME_BANK_A, VOLUME_BANK_B, VOLUME_BANK_C, PORT_WORKER_A, PORT_WORKER_B, PORT_WORKER_C, PORT_COORDINATOR

    for topology_ctx in _launch_topology_session(banks=["bank_a", "bank_b", "bank_c"]):
        NETWORK_NAME = topology_ctx["network_name"]
        CONTAINER_WORKER_A = topology_ctx["container_worker_a"]
        CONTAINER_WORKER_B = topology_ctx["container_worker_b"]
        CONTAINER_WORKER_C = topology_ctx["container_worker_c"]
        CONTAINER_COORDINATOR = topology_ctx["container_coordinator"]
        VOLUME_BANK_A = topology_ctx["volume_bank_a"]
        VOLUME_BANK_B = topology_ctx["volume_bank_b"]
        VOLUME_BANK_C = topology_ctx["volume_bank_c"]
        PORT_WORKER_A = topology_ctx["port_a"]
        PORT_WORKER_B = topology_ctx["port_b"]
        PORT_WORKER_C = topology_ctx["port_c"]
        PORT_COORDINATOR = topology_ctx["port_coord"]
        yield topology_ctx

