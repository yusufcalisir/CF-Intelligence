"""Shared integration test fixtures and Docker topology for institutional testing."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import time
from typing import TYPE_CHECKING, Any
import urllib.request
import uuid

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
TOKEN_MATCH_A = "matching_token_bank_a_3847291847"
TOKEN_MATCH_B = "matching_token_bank_b_9182739182"
TOKEN_INGEST_A = "ingestion_token_bank_a_12938471"
TOKEN_INGEST_B = "ingestion_token_bank_b_58271947"
TOKEN_ADMIN_A = "admin_token_bank_a_987123984"
TOKEN_ADMIN_B = "admin_token_bank_b_123987123"
SECRET_COORD = "auth_sec_coord_99912384719283749123"
SHARED_CONSORTIUM_STORE_SECRET = "consortium_store_hmac_shared_sec_888777666555444333"
SHARED_JWT_SECRET = "jwt_secret_test_suite_production_hardened_98712349812734918237"

# Mutable globals populated per session to guarantee disposable uniqueness
NETWORK_NAME: str = ""
CONTAINER_WORKER_A: str = ""
CONTAINER_WORKER_B: str = ""
CONTAINER_COORDINATOR: str = ""
VOLUME_BANK_A: str = ""
VOLUME_BANK_B: str = ""
PORT_WORKER_A: int = 0
PORT_WORKER_B: int = 0
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
    for c in [CONTAINER_WORKER_A, CONTAINER_WORKER_B, CONTAINER_COORDINATOR]:
        if c:
            _run_cmd(["docker", "rm", "-f", c], check=False)
    if NETWORK_NAME:
        _run_cmd(["docker", "network", "rm", NETWORK_NAME], check=False)
    for v in [VOLUME_BANK_A, VOLUME_BANK_B]:
        if v:
            _run_cmd(["docker", "volume", "rm", v], check=False)


@pytest.fixture(scope="module")
def docker_topology() -> Generator[dict[str, Any], None, None]:
    """Sets up genuine 3-container topology on isolated bridge network using disposable test-owned resources."""
    global NETWORK_NAME, CONTAINER_WORKER_A, CONTAINER_WORKER_B, CONTAINER_COORDINATOR
    global VOLUME_BANK_A, VOLUME_BANK_B, PORT_WORKER_A, PORT_WORKER_B, PORT_COORDINATOR

    if not is_docker_running():
        pytest.skip("Docker daemon is not reachable or running.")

    if not ensure_docker_image(IMAGE_NAME):
        pytest.skip(
            f"Required Docker image '{IMAGE_NAME}' is not available and could not be prepared automatically."
        )

    # Generate disposable resource identifiers unique to this test run
    session_id = uuid.uuid4().hex[:8]
    NETWORK_NAME = f"cfi_mesh_{session_id}"
    CONTAINER_WORKER_A = f"cfi_worker_a_{session_id}"
    CONTAINER_WORKER_B = f"cfi_worker_b_{session_id}"
    CONTAINER_COORDINATOR = f"cfi_coord_{session_id}"
    VOLUME_BANK_A = f"cfi_vault_a_{session_id}"
    VOLUME_BANK_B = f"cfi_vault_b_{session_id}"

    PORT_WORKER_A = _get_free_port()
    PORT_WORKER_B = _get_free_port()
    PORT_COORDINATOR = _get_free_port()

    # 1. Create isolated bridge mesh network
    net_res = _run_cmd(["docker", "network", "create", NETWORK_NAME], check=False)
    if net_res.returncode != 0:
        pytest.skip(f"Failed to create docker network '{NETWORK_NAME}': {net_res.stderr.strip()}")

    # 2. Create isolated institutional disposable vaults
    _run_cmd(["docker", "volume", "create", VOLUME_BANK_A], check=False)
    _run_cmd(["docker", "volume", "create", VOLUME_BANK_B], check=False)

    # 2b. Generate sovereign asymmetric signing identities for Bank A and Bank B
    ident_a = InstitutionalSigningIdentity.generate("bank_a")
    ident_b = InstitutionalSigningIdentity.generate("bank_b")

    # 3. Start Bank A Worker container
    peers_a_json = json.dumps({"bank_b": ident_b.public_key_hex})
    cmd_worker_a = [
        "docker", "run", "-d",
        "--name", CONTAINER_WORKER_A,
        "--network", NETWORK_NAME,
        "-v", f"{VOLUME_BANK_A}:/vault",
        "-p", f"{PORT_WORKER_A}:8100",
        "-e", "APP_ENV=production",
        "-e", f"JWT_SECRET={SHARED_JWT_SECRET}",
        "-e", f"CFI_JWT_SECRET={SHARED_JWT_SECRET}",
        "-e", f"CFI_JWT_SIGNING_SECRET={SHARED_JWT_SECRET}",
        "-e", f"CFI_OIDC_JWT_SIGNING_SECRET={SHARED_JWT_SECRET}",
        "-e", f"CFI_IDENTITY_STORE_SECRET={SHARED_JWT_SECRET}",
        "-e", f"CFI_AUDIT_LEDGER_SECRET={SHARED_JWT_SECRET}",
        "-e", "CFI_ACCOUNT_STORE_PATH=/tmp/account_store.json",
        "-e", "CFI_IDENTITY_STORE_PATH=/tmp/account_store.json",
        "-e", "BANK_ID=bank_a",
        "-e", "STORAGE_ROOT=/vault",
        "-e", "CFI_STORAGE_DIR=/vault",
        "-e", "CFI_CONSORTIUM_STORE_PATH=/tmp/consortium_store.json",
        "-e", f"CFI_CONSORTIUM_STORE_SECRET={SHARED_CONSORTIUM_STORE_SECRET}",
        "-e", "WORKER_HOST=0.0.0.0",
        "-e", "WORKER_PORT=8100",
        "-e", f"WORKER_AUTH_SECRET={SECRET_A}",
        "-e", f"WORKER_SIGNING_KEY={ident_a.private_key_hex}",
        "-e", f"WORKER_MATCHING_TOKEN={TOKEN_MATCH_A}",
        "-e", f"WORKER_INGESTION_TOKEN={TOKEN_INGEST_A}",
        "-e", f"WORKER_ADMIN_TOKEN={TOKEN_ADMIN_A}",
        "-e", f"WORKER_TRUSTED_PEERS_JSON={peers_a_json}",
        IMAGE_NAME,
    ]
    res_worker_a = _run_cmd(cmd_worker_a, check=False)
    if res_worker_a.returncode != 0:
        _teardown_disposable_resources()
        pytest.skip(f"Failed to start container '{CONTAINER_WORKER_A}': {res_worker_a.stderr.strip()}")

    # 4. Start Bank B Worker container
    peers_b_json = json.dumps({"bank_a": ident_a.public_key_hex})
    cmd_worker_b = [
        "docker", "run", "-d",
        "--name", CONTAINER_WORKER_B,
        "--network", NETWORK_NAME,
        "-v", f"{VOLUME_BANK_B}:/vault",
        "-p", f"{PORT_WORKER_B}:8100",
        "-e", "APP_ENV=production",
        "-e", f"JWT_SECRET={SHARED_JWT_SECRET}",
        "-e", f"CFI_JWT_SECRET={SHARED_JWT_SECRET}",
        "-e", f"CFI_JWT_SIGNING_SECRET={SHARED_JWT_SECRET}",
        "-e", f"CFI_OIDC_JWT_SIGNING_SECRET={SHARED_JWT_SECRET}",
        "-e", f"CFI_IDENTITY_STORE_SECRET={SHARED_JWT_SECRET}",
        "-e", f"CFI_AUDIT_LEDGER_SECRET={SHARED_JWT_SECRET}",
        "-e", "CFI_ACCOUNT_STORE_PATH=/tmp/account_store.json",
        "-e", "CFI_IDENTITY_STORE_PATH=/tmp/account_store.json",
        "-e", "BANK_ID=bank_b",
        "-e", "STORAGE_ROOT=/vault",
        "-e", "CFI_STORAGE_DIR=/vault",
        "-e", "CFI_CONSORTIUM_STORE_PATH=/tmp/consortium_store.json",
        "-e", f"CFI_CONSORTIUM_STORE_SECRET={SHARED_CONSORTIUM_STORE_SECRET}",
        "-e", "WORKER_HOST=0.0.0.0",
        "-e", "WORKER_PORT=8100",
        "-e", f"WORKER_AUTH_SECRET={SECRET_B}",
        "-e", f"WORKER_SIGNING_KEY={ident_b.private_key_hex}",
        "-e", f"WORKER_MATCHING_TOKEN={TOKEN_MATCH_B}",
        "-e", f"WORKER_INGESTION_TOKEN={TOKEN_INGEST_B}",
        "-e", f"WORKER_ADMIN_TOKEN={TOKEN_ADMIN_B}",
        "-e", f"WORKER_TRUSTED_PEERS_JSON={peers_b_json}",
        IMAGE_NAME,
    ]
    res_worker_b = _run_cmd(cmd_worker_b, check=False)
    if res_worker_b.returncode != 0:
        _teardown_disposable_resources()
        pytest.skip(f"Failed to start container '{CONTAINER_WORKER_B}': {res_worker_b.stderr.strip()}")

    # 5. Start Coordinator container
    cmd_coordinator = [
        "docker", "run", "-d",
        "--name", CONTAINER_COORDINATOR,
        "--network", NETWORK_NAME,
        "-p", f"{PORT_COORDINATOR}:8200",
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
        "--worker-a-bank", "bank_a",
        "--worker-a-url", f"http://{CONTAINER_WORKER_A}:8100",
        "--worker-a-token", TOKEN_MATCH_A,
        "--worker-b-bank", "bank_b",
        "--worker-b-url", f"http://{CONTAINER_WORKER_B}:8100",
        "--worker-b-token", TOKEN_MATCH_B,
    ]
    res_coordinator = _run_cmd(cmd_coordinator, check=False)
    if res_coordinator.returncode != 0:
        _teardown_disposable_resources()
        pytest.skip(f"Failed to start container '{CONTAINER_COORDINATOR}': {res_coordinator.stderr.strip()}")

    # Wait for daemons to become healthy
    health_a = _wait_for_health(f"http://127.0.0.1:{PORT_WORKER_A}/worker/health")
    health_b = _wait_for_health(f"http://127.0.0.1:{PORT_WORKER_B}/worker/health")
    health_coord = _wait_for_health(f"http://127.0.0.1:{PORT_COORDINATOR}/health")

    if not (health_a and health_b and health_coord):
        _teardown_disposable_resources()
        pytest.skip(
            f"Containers failed to become healthy within timeout: worker_a={health_a}, worker_b={health_b}, coordinator={health_coord}"
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
    client_coord = InstitutionalCoordinatorClient(
        base_url=f"http://127.0.0.1:{PORT_COORDINATOR}",
    )

    topology_ctx = {
        "client_a": client_a,
        "client_b": client_b,
        "client_coord": client_coord,
        "port_a": PORT_WORKER_A,
        "port_b": PORT_WORKER_B,
        "port_coord": PORT_COORDINATOR,
        "container_worker_a": CONTAINER_WORKER_A,
        "container_worker_b": CONTAINER_WORKER_B,
        "container_coordinator": CONTAINER_COORDINATOR,
        "network_name": NETWORK_NAME,
        "volume_bank_a": VOLUME_BANK_A,
        "volume_bank_b": VOLUME_BANK_B,
        "ident_a": ident_a,
        "ident_b": ident_b,
    }

    # Provision backend service verification public keys (ZERO private key material on analyst host)
    os.environ["CFI_INSTITUTION_PUBLIC_KEY_BANK_A"] = ident_a.public_key_hex
    os.environ["CFI_INSTITUTION_PUBLIC_KEY_BANK_B"] = ident_b.public_key_hex
    os.environ["CFI_COORDINATOR_SECRET"] = SECRET_COORD

    try:
        yield topology_ctx
    finally:
        os.environ.pop("CFI_INSTITUTION_PUBLIC_KEY_BANK_A", None)
        os.environ.pop("CFI_INSTITUTION_PUBLIC_KEY_BANK_B", None)
        os.environ.pop("CFI_COORDINATOR_SECRET", None)
        _teardown_disposable_resources()
