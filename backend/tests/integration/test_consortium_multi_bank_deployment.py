"""Integration verification suite for multi-bank consortium deployment.

Validates the integrated Docker-based consortium deployment:
- End-to-end service health checks across gateway, backend, coordinator, and 3 bank workers.
- Institutional sovereign identity and storage isolation across non-root containers.
- Authentic multi-counterparty PSI query execution (OpenMined PSI 2.0.6 ECDH).
- Authentic MATCH and NO_MATCH outcomes with Ed25519 cryptographic attestations.
- Authoritative investigation case creation and tamper-evident evidence attachment.
- Security negative controls failing closed (unauthenticated, predictable password rejection,
  tenant mismatch, unknown counterpart, and unavailable counterparty worker).
- Localhost-only port binding boundaries and HTTP reverse proxy gateway reachability.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from tests.integration.conftest import is_docker_running

GATEWAY_URL = "http://127.0.0.1:80"
BACKEND_URL = "http://127.0.0.1:8000"
COORDINATOR_URL = "http://127.0.0.1:8200"
WORKER_A_URL = "http://127.0.0.1:8101"
WORKER_B_URL = "http://127.0.0.1:8102"
WORKER_C_URL = "http://127.0.0.1:8103"
REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_consortium_env() -> dict[str, str]:
    """Load deployment secrets from .env.consortium in repository root."""
    env_path = Path(os.environ.get("CFI_CONSORTIUM_ENV_FILE", REPO_ROOT / ".env.consortium"))
    env: dict[str, str] = {}
    if not env_path.exists():
        return env
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def _get_analyst_password(env_key: str = "ANALYST_BANK_A_PASSWORD") -> str:
    """Resolve non-predictable analyst credential from environment or .env.consortium."""
    env = _load_consortium_env()
    pwd = (os.environ.get(env_key) or env.get(env_key) or "").strip()
    if not pwd or len(pwd) < 16 or pwd.lower().startswith(("replace_with_", "analyst", "admin")):
        pytest.fail(
            f"Missing or predictable credential for '{env_key}'. "
            "Run 'python scripts/generate_consortium_deployment_secrets.py --output .env.consortium' "
            "and 'python scripts/bootstrap_consortium_deployment.py --env-file .env.consortium'."
        )
    return pwd


def _is_strict_live_verification(request: pytest.FixtureRequest) -> bool:
    """Return True when live consortium certification is explicitly invoked."""
    if os.environ.get("CFI_REQUIRE_LIVE_CONSORTIUM", "").strip().lower() in ("1", "true", "yes"):
        return True
    if os.environ.get("CFI_STRICT_CONSORTIUM_VERIFICATION", "").strip().lower() in ("1", "true", "yes"):
        return True
    invoked_args = [str(a) for a in getattr(request.config.invocation_params, "args", ())]
    return any("test_consortium_multi_bank_deployment" in arg for arg in invoked_args)


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
def require_consortium_deployment(request: pytest.FixtureRequest) -> None:
    """Ensure Docker is running and consortium containers are healthy before running tests.

    Fails closed when live consortium verification is intentionally invoked;
    skips only during broad non-deployment CI discovery.
    """
    strict = _is_strict_live_verification(request)

    if not is_docker_running():
        msg = "Docker daemon is not running."
        if strict:
            pytest.fail(f"Live consortium verification failed: {msg}")
        pytest.skip(msg)

    required_probes = [
        ("Gateway", f"{GATEWAY_URL}/gateway-health"),
        ("Backend", f"{BACKEND_URL}/health"),
        ("Coordinator", f"{COORDINATOR_URL}/health"),
        ("Worker Bank A", f"{WORKER_A_URL}/worker/health"),
        ("Worker Bank B", f"{WORKER_B_URL}/worker/health"),
        ("Worker Bank C", f"{WORKER_C_URL}/worker/health"),
    ]
    for name, url in required_probes:
        try:
            code, body = _http_get(url, timeout=3.0)
            if code != 200:
                msg = f"Required consortium service '{name}' is unhealthy (HTTP {code}) at {url}: {body}"
                if strict:
                    pytest.fail(f"Live consortium verification failed: {msg}")
                pytest.skip(msg)
        except Exception as exc:
            msg = f"Required consortium service '{name}' is unreachable at {url}: {exc}"
            if strict:
                pytest.fail(f"Live consortium verification failed: {msg}")
            pytest.skip(msg)


def _login_bank_a_analyst() -> str:
    password = _get_analyst_password("ANALYST_BANK_A_PASSWORD")
    code, res = _http_post(
        f"{BACKEND_URL}/api/v1/auth/login",
        {"username": "analyst_bank_a", "password": password},
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
    """Verify unauthenticated queries and predictable credentials fail closed, and analyst roles are least-privilege."""
    # 1. Unauthenticated request fails closed
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

    # 2. Publicly known / predictable privileged credentials must be rejected with HTTP 401
    for uname, suffix in [
        ("analyst_bank_a", "BankA2026!"),
        ("analyst_bank_b", "BankB2026!"),
        ("analyst_bank_c", "BankC2026!"),
    ]:
        pred_pwd = "Analyst" + suffix
        code_login, _ = _http_post(
            f"{BACKEND_URL}/api/v1/auth/login",
            {"username": uname, "password": pred_pwd},
        )
        assert code_login == 401, f"Predictable credential for {uname} must fail with 401, got {code_login}"

    # 3. Verify authenticated analyst holds least-privilege institutional roles (no admin privilege)
    token_a = _login_bank_a_analyst()
    payload_b64 = token_a.split(".")[1]
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    claims = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    roles = set(claims.get("roles", []))
    assert "admin" not in roles and "super_admin" not in roles
    assert roles == {"compliance", "investigator"}


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
    """Verify unknown target institutions fail closed and unavailable workers report truthful PARTIAL_FAILURE."""
    token_a = _login_bank_a_analyst()

    # 1. Unknown institution fails closed in pre-flight governance check
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

    # 2. Unavailable counterparty worker fails closed for that target without fabricating results
    subprocess.run(["docker", "stop", "cfi-worker-bank-c"], capture_output=True, text=True, check=True)
    try:
        code_partial, resp_partial = _http_post(
            f"{BACKEND_URL}/api/v1/intelligence/cross-bank/query-multi",
            {
                "consortium_id": "consortium_consortium_deployment_01",
                "requesting_bank_id": "bank_a",
                "target_bank_ids": ["bank_b", "bank_c"],
                "query_entity_id": "target_nexus_investigation_01",
                "purpose": "AML_COLLABORATIVE_INVESTIGATION",
                "execution_mode": "VERIFIED_OPERATIONAL",
                "use_established_psi": True,
                "require_established_psi": True,
            },
            token=token_a,
            tenant_id="bank_a",
        )
        assert code_partial == 200 and isinstance(resp_partial, dict)
        assert resp_partial["overall_outcome"] == "PARTIAL_FAILURE"
        assert "bank_b" in resp_partial["results"]
        assert resp_partial["results"]["bank_b"]["match_confirmed"] is True
        assert "bank_c" not in resp_partial["results"]
        assert "bank_c" in resp_partial["failures"]
        assert resp_partial["execution_outcomes"]["bank_c"]["status"] == "FAILED"
    finally:
        subprocess.run(["docker", "start", "cfi-worker-bank-c"], capture_output=True, text=True, check=True)
        for _ in range(20):
            try:
                c_health, _ = _http_get(f"{WORKER_C_URL}/worker/health", timeout=2.0)
                if c_health == 200:
                    break
            except Exception:
                pass
            time.sleep(1.0)


def test_consortium_deployment_frontend_reverse_proxy_reachability(require_consortium_deployment: None) -> None:
    """Verify reverse proxy gateway reachability and strict 127.0.0.1 localhost port exposure boundaries."""
    # 1. Frontend SPA index
    code_fe, body_fe = _http_get(f"{GATEWAY_URL}/")
    assert code_fe == 200
    assert "<title>Collaborative Fraud Intelligence Platform</title>" in str(body_fe)

    # 2. Backend API proxy through gateway
    code_api, body_api = _http_get(f"{GATEWAY_URL}/api/v1/health")
    assert code_api == 200
    assert isinstance(body_api, dict) and body_api.get("status") == "healthy"

    # 3. Verify host port bindings across all consortium containers
    containers = [
        "cfi-gateway",
        "cfi-frontend",
        "cfi-api-server",
        "cfi-coordinator",
        "cfi-worker-bank-a",
        "cfi-worker-bank-b",
        "cfi-worker-bank-c",
        "cfi-consortium-postgres",
        "cfi-consortium-redis",
    ]
    res = subprocess.run(
        ["docker", "inspect"] + containers,
        capture_output=True,
        text=True,
        check=True,
    )
    inspected = json.loads(res.stdout)
    for item in inspected:
        name = item["Name"].lstrip("/")
        ports = item.get("NetworkSettings", {}).get("Ports") or {}
        if name in ("cfi-consortium-postgres", "cfi-consortium-redis"):
            for port_key, bindings in ports.items():
                assert not bindings, f"Internal store '{name}' must not publish host port for {port_key}: {bindings}"
        else:
            if name == "cfi-gateway":
                assert not ports.get("443/tcp"), "Gateway must not publish unused port 443 in HTTP localhost sandbox"
            for port_key, bindings in ports.items():
                if not bindings:
                    continue
                for b in bindings:
                    host_ip = b.get("HostIp", "")
                    assert host_ip == "127.0.0.1", (
                        f"Container '{name}' port {port_key} bound to '{host_ip}' instead of '127.0.0.1'"
                    )


def test_consortium_deployment_operational_recovery(require_consortium_deployment: None) -> None:
    """Verify Gate 5: Redis outage degrades gracefully and auto-reconnects, and worker restart preserves keys."""
    # 1. Baseline healthy state
    code, health_baseline = _http_get(f"{BACKEND_URL}/api/v1/health")
    assert code == 200 and isinstance(health_baseline, dict)
    assert health_baseline.get("status") == "healthy"
    assert health_baseline.get("storage_backend") == "redis"
    assert health_baseline.get("durability") == "durable"

    # 2. Capture Bank A worker public key before restart
    code_a, body_a = _http_get(f"{WORKER_A_URL}/worker/health")
    assert code_a == 200 and isinstance(body_a, dict)
    pub_a_initial = body_a.get("public_key_hex") or body_a.get("signing_public_key_hex")
    assert pub_a_initial is not None

    # 3. Simulate transient Redis outage
    subprocess.run(["docker", "stop", "cfi-consortium-redis"], capture_output=True, text=True, check=True)
    try:
        # After outage, health endpoint truthfully reflects fallback state without process crash
        time.sleep(2.0)
        code_deg, health_deg = _http_get(f"{BACKEND_URL}/api/v1/health")
        assert code_deg == 200 and isinstance(health_deg, dict)
        assert health_deg.get("storage_backend") == "in_memory"
        assert health_deg.get("durability") == "ephemeral"
    finally:
        # 4. Recover Redis service
        subprocess.run(["docker", "start", "cfi-consortium-redis"], capture_output=True, text=True, check=True)
        # Wait for auto-reconnect cooldown and reconnection
        reconnected = False
        for _ in range(25):
            time.sleep(1.0)
            try:
                code_rec, health_rec = _http_get(f"{BACKEND_URL}/api/v1/health", timeout=2.0)
                if (
                    code_rec == 200
                    and isinstance(health_rec, dict)
                    and health_rec.get("storage_backend") == "redis"
                    and health_rec.get("durability") == "durable"
                ):
                    assert health_rec.get("status") == "healthy"
                    reconnected = True
                    break
            except Exception:
                pass
        assert reconnected, "Backend failed to auto-reconnect to Redis after transient outage."

    # 5. Worker restart preserves durable sovereign key custody
    subprocess.run(["docker", "restart", "cfi-worker-bank-a"], capture_output=True, text=True, check=True)
    worker_ready = False
    for _ in range(25):
        try:
            code_w, body_w = _http_get(f"{WORKER_A_URL}/worker/health", timeout=2.0)
            if code_w == 200 and isinstance(body_w, dict) and body_w.get("status") == "ONLINE":
                pub_a_after = body_w.get("public_key_hex") or body_w.get("signing_public_key_hex")
                assert pub_a_after == pub_a_initial, "Worker public key changed after container restart!"
                worker_ready = True
                break
        except Exception:
            pass
        time.sleep(1.0)
    assert worker_ready, "Worker Bank A failed to become ready after container restart."
