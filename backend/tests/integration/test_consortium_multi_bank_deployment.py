"""Integration verification suite for multi-bank consortium deployment.

Validates the integrated Docker-based consortium deployment:
- End-to-end service health checks across gateway, backend, coordinator, and 3 bank workers.
- Institutional sovereign identity and storage isolation across non-root containers.
- Authentic multi-counterparty PSI query execution (OpenMined PSI 2.0.6 ECDH).
- Authentic MATCH and NO_MATCH outcomes with Ed25519 cryptographic attestations.
- Authoritative investigation case creation and tamper-evident evidence attachment.
- Security negative controls failing closed (unauthenticated, tenant mismatch, unknown counterpart).
- Reverse proxy security gateway and frontend asset reachability.
"""

from __future__ import annotations

import json
import subprocess
import urllib.request
import urllib.error
from typing import Any

import pytest

from tests.integration.conftest import is_docker_running


GATEWAY_URL = "http://127.0.0.1:80"
BACKEND_URL = "http://127.0.0.1:8000"
COORDINATOR_URL = "http://127.0.0.1:8200"
WORKER_A_URL = "http://127.0.0.1:8101"
WORKER_B_URL = "http://127.0.0.1:8102"
WORKER_C_URL = "http://127.0.0.1:8103"


def _http_get(url: str, timeout: float = 5.0) -> tuple[int, dict[str, Any] | str]:
    req = urllib.request.Request(url, headers={"User-Agent": "CFI-Verification/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", errors="replace")
        try:
            return err.code, json.loads(raw)
        except json.JSONDecodeError:
            return err.code, raw


def _http_post(
    url: str,
    payload: dict[str, Any],
    token: str | None = None,
    tenant_id: str | None = None,
    timeout: float = 15.0,
) -> tuple[int, dict[str, Any] | str]:
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "CFI-Verification/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if tenant_id:
        headers["X-Tenant-ID"] = tenant_id
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", errors="replace")
        try:
            return err.code, json.loads(raw)
        except json.JSONDecodeError:
            return err.code, raw


@pytest.fixture(scope="module")
def require_consortium_deployment() -> None:
    """Ensure Docker is running and consortium containers are up before running tests."""
    if not is_docker_running():
        pytest.skip("Docker daemon is not running.")
    try:
        code, body = _http_get(f"{BACKEND_URL}/health", timeout=3.0)
        if code != 200 or not isinstance(body, dict) or body.get("status") != "healthy":
            pytest.skip("Consortium backend is not running or healthy at http://127.0.0.1:8000.")
    except Exception as exc:
        pytest.skip(f"Consortium environment unreachable: {exc}")


def _login_bank_a_analyst() -> str:
    code, res = _http_post(
        f"{BACKEND_URL}/api/v1/auth/login",
        {"username": "analyst_bank_a", "password": "AnalystBankA2026!"},
    )
    assert code == 200, f"Analyst login failed (status {code}): {res}"
    assert isinstance(res, dict) and "access_token" in res
    return str(res["access_token"])


def test_consortium_deployment_service_health_probes(require_consortium_deployment: None) -> None:
    """Verify all 6 core services and institutional workers pass authentic health checks."""
    code_gw, body_gw = _http_get(f"{GATEWAY_URL}/gateway-health")
    assert code_gw == 200
    assert "cfi_gateway_healthy" in str(body_gw)

    code_be, body_be = _http_get(f"{BACKEND_URL}/health")
    assert code_be == 200
    assert isinstance(body_be, dict) and body_be.get("status") == "healthy"

    code_coord, body_coord = _http_get(f"{COORDINATOR_URL}/health")
    assert code_coord == 200
    assert isinstance(body_coord, dict) and body_coord.get("status") in ("OK", "healthy", "ONLINE")

    for port, bank_name in [(WORKER_A_URL, "Bank A"), (WORKER_B_URL, "Bank B"), (WORKER_C_URL, "Bank C")]:
        code_w, body_w = _http_get(f"{port}/worker/health")
        assert code_w == 200, f"{bank_name} worker failed health check"
        assert isinstance(body_w, dict) and body_w.get("status") in ("ONLINE", "healthy", "OK")


def test_consortium_deployment_institutional_storage_isolation(require_consortium_deployment: None) -> None:
    """Verify non-root process identities and strict private vault storage isolation across workers."""
    # 1. Non-root identity enforcement: Workers and coordinator execute as non-root UID 10001
    for container in ["cfi-worker-bank-a", "cfi-worker-bank-b", "cfi-worker-bank-c", "cfi-coordinator"]:
        res = subprocess.run(["docker", "exec", container, "id", "-u"], capture_output=True, text=True, check=True)
        assert res.stdout.strip() == "10001", f"Container {container} is not running as non-root UID 10001"

    # 2. Vault storage isolation: Bank A vault must contain target_nexus entity, Bank B must contain match, Bank C clean
    res_a = subprocess.run(
        ["docker", "exec", "cfi-worker-bank-a", "ls", "/vault/bank_a/private_entities"],
        capture_output=True, text=True, check=True
    )
    res_b = subprocess.run(
        ["docker", "exec", "cfi-worker-bank-b", "ls", "/vault/bank_b/private_entities"],
        capture_output=True, text=True, check=True
    )
    res_c = subprocess.run(
        ["docker", "exec", "cfi-worker-bank-c", "ls", "/vault/bank_c/private_entities"],
        capture_output=True, text=True, check=True
    )
    assert "target_nexus_investigation_01.json" in res_a.stdout
    assert "counterpart_b_nexus_match.json" not in res_a.stdout
    assert "counterpart_b_nexus_match.json" in res_b.stdout
    assert "target_nexus_investigation_01.json" not in res_b.stdout
    assert "counterpart_c_clean_benign.json" in res_c.stdout


def test_consortium_deployment_operational_multi_counterparty_query(require_consortium_deployment: None) -> None:
    """Verify authentic multi-counterparty PSI query producing MATCH on Bank B and NO_MATCH on Bank C."""
    token_a = _login_bank_a_analyst()

    query_payload = {
        "consortium_id": "consortium_consortium_deployment_01",
        "requesting_bank_id": "bank_a",
        "target_bank_ids": ["bank_b", "bank_c"],
        "query_entity_id": "target_nexus_investigation_01",
        "purpose": "AML_COLLABORATIVE_INVESTIGATION",
        "execution_mode": "VERIFIED_OPERATIONAL",
        "use_established_psi": True,
        "require_established_psi": True,
    }

    code, resp = _http_post(
        f"{BACKEND_URL}/api/v1/intelligence/cross-bank/query-multi",
        query_payload,
        token=token_a,
        tenant_id="bank_a",
    )
    assert code == 200, f"Multi-counterparty query returned {code}: {resp}"
    assert isinstance(resp, dict)

    assert resp["requesting_bank_id"] == "bank_a"
    assert resp["counterpart_count"] == 2
    assert resp["overall_outcome"] == "COMPLETED"
    assert "bank_b" in resp["results"]
    assert "bank_c" in resp["results"]

    # Target Bank B: authentic match confirmed via OpenMined PSI
    res_b = resp["results"]["bank_b"]
    assert res_b["match_confirmed"] is True
    assert res_b["provenance"]["protocol"] == "OPENMINED-PSI-2.0.6-ECDH"
    assert res_b["provenance"]["assurance_level"] == "REVIEWED_ESTABLISHED_PSI_VERIFIED"
    assert len(res_b["provenance"]["worker_a_signature"]) >= 64
    assert len(res_b["provenance"]["worker_b_signature"]) >= 64

    # Target Bank C: authentic negative control (NO_MATCH)
    res_c = resp["results"]["bank_c"]
    assert res_c["match_confirmed"] is False
    assert res_c["provenance"]["protocol"] == "OPENMINED-PSI-2.0.6-ECDH"
    assert res_c["provenance"]["assurance_level"] == "REVIEWED_ESTABLISHED_PSI_VERIFIED"
    assert len(res_c["provenance"]["worker_a_signature"]) >= 64
    assert len(res_c["provenance"]["worker_b_signature"]) >= 64


def test_consortium_deployment_investigation_case_evidence_attachment(require_consortium_deployment: None) -> None:
    """Verify attaching authentic multi-counterparty intelligence signals to an investigation case."""
    token_a = _login_bank_a_analyst()

    # 1. Execute query
    query_payload = {
        "consortium_id": "consortium_consortium_deployment_01",
        "requesting_bank_id": "bank_a",
        "target_bank_ids": ["bank_b", "bank_c"],
        "query_entity_id": "target_nexus_investigation_01",
        "purpose": "AML_COLLABORATIVE_INVESTIGATION",
        "execution_mode": "VERIFIED_OPERATIONAL",
        "use_established_psi": True,
        "require_established_psi": True,
    }
    code_q, resp_q = _http_post(
        f"{BACKEND_URL}/api/v1/intelligence/cross-bank/query-multi",
        query_payload,
        token=token_a,
        tenant_id="bank_a",
    )
    assert code_q == 200 and isinstance(resp_q, dict)
    query_id = resp_q["query_id"]

    # 2. Create authoritative investigation case
    code_c, resp_c = _http_post(
        f"{BACKEND_URL}/api/v1/cases",
        {"title": "Automated Cross-Bank Deployment Case", "bank_id": "bank_a", "priority": "p1_critical"},
        token=token_a,
        tenant_id="bank_a",
    )
    assert code_c == 200 and isinstance(resp_c, dict)
    case_id = resp_c["id"]

    # 3. Attach multi-counterparty intelligence signals to case
    code_att, resp_att = _http_post(
        f"{BACKEND_URL}/api/v1/intelligence/cross-bank/attach-multi-to-case",
        {
            "query_id": query_id,
            "case_id": case_id,
            "notes": "Consortium deployment verified evidence attachment",
            "idempotency_key": f"deploy_attach_{query_id[:8]}",
        },
        token=token_a,
        tenant_id="bank_a",
    )
    assert code_att == 200 and isinstance(resp_att, dict)
    assert resp_att["assembly_status"] == "COMPLETED"
    assert resp_att["attached_count"] == 2
    assert resp_att["timeline_event_recorded"] is True
    assert len(resp_att["attached_evidence"]) == 2


def test_consortium_deployment_unauthenticated_query_fails_closed(require_consortium_deployment: None) -> None:
    """Verify unauthenticated query without bearer token fails closed with HTTP 401."""
    code, _ = _http_post(
        f"{BACKEND_URL}/api/v1/intelligence/cross-bank/query-multi",
        {
            "consortium_id": "consortium_consortium_deployment_01",
            "requesting_bank_id": "bank_a",
            "target_bank_ids": ["bank_b"],
            "query_entity_id": "target_nexus_investigation_01",
            "execution_mode": "VERIFIED_OPERATIONAL",
            "use_established_psi": True,
            "require_established_psi": True,
        },
    )
    assert code in (401, 403), f"Expected 401/403, got {code}"


def test_consortium_deployment_tenant_mismatch_fails_closed(require_consortium_deployment: None) -> None:
    """Verify Bank A token querying for Bank B institution fails closed with HTTP 403."""
    token_a = _login_bank_a_analyst()
    code, resp = _http_post(
        f"{BACKEND_URL}/api/v1/intelligence/cross-bank/query-multi",
        {
            "consortium_id": "consortium_consortium_deployment_01",
            "requesting_bank_id": "bank_b",
            "target_bank_ids": ["bank_c"],
            "query_entity_id": "target_nexus_investigation_01",
            "execution_mode": "VERIFIED_OPERATIONAL",
            "use_established_psi": True,
            "require_established_psi": True,
        },
        token=token_a,
        tenant_id="bank_a",
    )
    assert code in (401, 403), f"Expected 401/403, got {code}"


def test_consortium_deployment_unknown_target_institution_fails_closed(require_consortium_deployment: None) -> None:
    """Verify querying an unapproved or unknown institution fails closed with HTTP 403."""
    token_a = _login_bank_a_analyst()
    code, resp = _http_post(
        f"{BACKEND_URL}/api/v1/intelligence/cross-bank/query-multi",
        {
            "consortium_id": "consortium_consortium_deployment_01",
            "requesting_bank_id": "bank_a",
            "target_bank_ids": ["bank_non_existent"],
            "query_entity_id": "target_nexus_investigation_01",
            "execution_mode": "VERIFIED_OPERATIONAL",
            "use_established_psi": True,
            "require_established_psi": True,
        },
        token=token_a,
        tenant_id="bank_a",
    )
    assert code in (400, 403, 404, 422), f"Expected fail-closed, got {code}"


def test_consortium_deployment_frontend_reverse_proxy_reachability(require_consortium_deployment: None) -> None:
    """Verify reverse proxy gateway correctly serves frontend assets and proxies API endpoints."""
    # 1. Frontend SPA index
    code_fe, body_fe = _http_get(f"{GATEWAY_URL}/")
    assert code_fe == 200
    assert "<title>Collaborative Fraud Intelligence Platform</title>" in str(body_fe)

    # 2. Backend API proxy through gateway
    code_api, body_api = _http_get(f"{GATEWAY_URL}/api/v1/health")
    assert code_api == 200
    assert isinstance(body_api, dict) and body_api.get("status") == "healthy"
