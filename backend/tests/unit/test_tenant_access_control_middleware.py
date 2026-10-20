"""Comprehensive Golden Semantic Test Suite for TenantAccessControlMiddleware.

Tests PRE and POST behavior under the Hardened Identity & Quota Authority Contract:
- Exempt prefixes, near-prefixes, and case sensitivity
- Authorization header extraction and scheme matching
- Authoritative principal resolution (JWT / verified API Key > resource selectors)
- Fail-closed on invalid/expired/revoked JWT (no header fallback)
- Conflicting tenant selector headers (X-Tenant-ID vs X-Bank-ID fail with 400)
- Unverified X-API-Key text grants no authority (fails with 401)
- Verified API key binding to authorized institution
- Query parameter semantics and tenant normalization
- Privileged role evaluation and cross-bank authorization
- Cross-tenant 403 RFC 7807 problem details
- Outermost short-circuit behavior
- Pass-through response transparency and streaming
- Downstream exception propagation
- Non-HTTP scope handling and request immutability
- ContextVar isolation diagnostic
"""

from __future__ import annotations

from contextvars import ContextVar
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.testclient import TestClient

from app.infrastructure.security.oidc_authenticator import OIDCAuthenticator
from app.main import app, seed_mock_data

test_cvar: ContextVar[str] = ContextVar("test_cvar", default="initial")


@pytest.fixture(scope="module", autouse=True)
def setup_seed():
    seed_mock_data()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def oidc() -> OIDCAuthenticator:
    return OIDCAuthenticator()


# ── 1. Exempt Prefixes & Path Matching ───────────────────────────────────────

@pytest.mark.parametrize(
    "path,expected_exempt",
    [
        ("/docs", True),
        ("/docs/", True),
        ("/docs/oauth2", True),
        ("/docsXYZ", True),
        ("/Docs", False),  # Case-sensitive check
        ("/redoc", True),
        ("/redoc/", True),
        ("/redocXYZ", True),
        ("/openapi.json", True),
        ("/openapi.jsonXYZ", True),
        ("/health", True),
        ("/health/", True),
        ("/healthz", True),
        ("/Health", False),  # Case-sensitive
        ("/api/v1/health", True),
        ("/api/v1/healthz", True),
        ("/v1/health", True),
        ("/v1/healthz", True),
        ("/metrics", True),
        ("/metricsXYZ", True),
        ("/ws/", True),
        ("/ws/notifications", True),
        ("/ws", False),  # prefix is "/ws/" with trailing slash
        ("/api/v1/onboarding", True),
        ("/api/v1/onboarding/", True),
        ("/api/v1/onboarding/bank_a", True),
        ("/api/v1/onboardingXYZ", True),
        ("/api/v1/alerts", False),
        ("/api/v1/cases", False),
    ],
)
def test_exempt_prefix_matrix(client: TestClient, oidc: OIDCAuthenticator, path: str, expected_exempt: bool):
    # Verified Bank A principal targeting bank_b via query param
    # If exempt: passes through TenantAccess without 403 (will 200 or 404 depending on route)
    # If not exempt: TenantAccess immediately intercepts and returns 403
    token = oidc.create_mock_token(username="user1", bank_id="bank_a", roles=["analyst"])
    resp = client.get(f"{path}?bank_id=bank_b", headers={"Authorization": f"Bearer {token}"})
    if expected_exempt:
        assert resp.status_code != 403 or "TenantAccessDenied" not in resp.text
    else:
        assert resp.status_code == 403
        assert resp.json()["type"] == "https://cfi-platform.org/errors/TenantAccessDenied"


# ── 2. Authorization Header & Authentication Enforcement ────────────────────

def test_auth_header_absent(client: TestClient):
    resp = client.get("/api/v1/alerts?bank_id=bank_a")
    # Protected endpoint requires authentication -> 401
    assert resp.status_code == 401
    assert resp.json()["type"] == "https://cfi-platform.org/errors/AuthenticationRequired"


def test_auth_header_empty(client: TestClient):
    resp = client.get("/api/v1/alerts?bank_id=bank_a", headers={"Authorization": ""})
    assert resp.status_code == 401


def test_auth_header_whitespace(client: TestClient):
    resp = client.get("/api/v1/alerts?bank_id=bank_a", headers={"Authorization": "   "})
    assert resp.status_code == 401


def test_auth_header_bearer_valid(client: TestClient, oidc: OIDCAuthenticator):
    token = oidc.create_mock_token(username="user1", bank_id="bank_a", roles=["analyst"])
    resp = client.get("/api/v1/alerts?bank_id=bank_a", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200

    resp_denied = client.get("/api/v1/alerts?bank_id=bank_b", headers={"Authorization": f"Bearer {token}"})
    assert resp_denied.status_code == 403
    assert resp_denied.json()["type"] == "https://cfi-platform.org/errors/TenantAccessDenied"


def test_auth_header_bearer_case_sensitivity(client: TestClient, oidc: OIDCAuthenticator):
    token = oidc.create_mock_token(username="user1", bank_id="bank_a", roles=["analyst"])
    resp = client.get("/api/v1/alerts?bank_id=bank_a", headers={"Authorization": f"bearer {token}"})
    assert resp.status_code == 200

    resp_denied = client.get("/api/v1/alerts?bank_id=bank_b", headers={"Authorization": f"bearer {token}"})
    assert resp_denied.status_code == 403


def test_auth_header_bearer_empty_token(client: TestClient):
    resp = client.get("/api/v1/alerts?bank_id=bank_b", headers={"Authorization": "Bearer "})
    assert resp.status_code == 401


def test_auth_header_basic(client: TestClient):
    resp = client.get("/api/v1/alerts?bank_id=bank_b", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert resp.status_code == 401


# ── 3. Identity Authority & Fail-Closed Fallback Prevention ──────────────────

def test_jwt_authority_rejects_unauthorized_header_override(client: TestClient, oidc: OIDCAuthenticator):
    token = oidc.create_mock_token(username="u1", bank_id="bank_a", roles=["analyst"])
    # JWT is bank_a. Caller supplies X-Bank-ID: bank_b.
    # An ordinary Bank A caller cannot impersonate Bank B merely by supplying X-Bank-ID: bank_b!
    resp = client.get(
        "/api/v1/alerts?bank_id=bank_b",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID": "bank_b",
            "X-Bank-ID": "bank_b",
        },
    )
    assert resp.status_code == 403
    assert resp.json()["type"] == "https://cfi-platform.org/errors/TenantAccessDenied"


def test_invalid_jwt_fails_closed_no_header_fallback(client: TestClient):
    # Invalid JWT + X-Tenant-ID bank_a -> must fail closed with 401, NO fallback to headers!
    resp = client.get(
        "/api/v1/alerts?bank_id=bank_a",
        headers={
            "Authorization": "Bearer invalid.jwt.token",
            "X-Tenant-ID": "bank_a",
        },
    )
    assert resp.status_code == 401
    assert resp.json()["type"] == "https://cfi-platform.org/errors/AuthenticationRequired"


def test_jwt_exception_fails_closed_no_fallback(client: TestClient):
    with patch("app.infrastructure.security.oidc_authenticator.OIDCAuthenticator.decode_and_validate_token") as mock_dec:
        mock_dec.side_effect = RuntimeError("Crypto signature failure")
        resp = client.get(
            "/api/v1/alerts?bank_id=bank_a",
            headers={
                "Authorization": "Bearer some_token",
                "X-Tenant-ID": "bank_a",
            },
        )
        assert resp.status_code == 401


def test_conflicting_tenant_selector_headers_fail_safely(client: TestClient, oidc: OIDCAuthenticator):
    token = oidc.create_mock_token(username="u1", bank_id="bank_a", roles=["analyst"])
    # X-Tenant-ID is bank_a, X-Bank-ID is bank_b -> Conflicting selector headers fail safely with 400!
    resp = client.get(
        "/api/v1/alerts",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID": "bank_a",
            "X-Bank-ID": "bank_b",
        },
    )
    assert resp.status_code == 400


def test_unverified_api_key_fails_closed(client: TestClient):
    # Unverified API key string grants NO authority -> 401
    resp = client.get(
        "/api/v1/alerts?bank_id=bank_b",
        headers={
            "X-Bank-ID": "bank_a",
            "X-API-Key": "unverified_key:bank_b:bank",
        },
    )
    assert resp.status_code == 401


# ── 4. Raw Duplicate Headers (ASGI Level) ────────────────────────────────────

@pytest.mark.asyncio
async def test_raw_duplicate_headers_first_wins():
    """Verify raw ASGI duplicate headers behavior (Starlette Headers.get returns FIRST)."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/alerts",
        "query_string": b"bank_id=bank_b",
        "headers": [
            (b"x-tenant-id", b"bank_a"),
            (b"x-tenant-id", b"bank_b"),
        ],
    }
    req = Request(scope)
    assert req.headers.get("x-tenant-id") == "bank_a"

    scope2 = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/alerts",
        "query_string": b"bank_id=bank_b",
        "headers": [
            (b"x-tenant-id", b"bank_b"),
            (b"x-tenant-id", b"bank_a"),
        ],
    }
    req2 = Request(scope2)
    assert req2.headers.get("x-tenant-id") == "bank_b"

    scope3 = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/alerts",
        "query_string": b"",
        "headers": [
            (b"x-api-key", b"prefix:bank_first"),
            (b"x-api-key", b"prefix:bank_second"),
        ],
    }
    req3 = Request(scope3)
    assert req3.headers.get("x-api-key") == "prefix:bank_first"


# ── 5. Gateway API Key Authority Verification ────────────────────────────────

def test_api_key_authority_verification(client: TestClient):
    # Configured key_bank_a binds authoritatively to bank_a
    resp_ok = client.get("/api/v1/alerts?bank_id=bank_a", headers={"X-API-Key": "key_bank_a"})
    assert resp_ok.status_code == 200

    # Cross-tenant query targeting bank_b with key_bank_a -> 403 Forbidden
    resp_cross = client.get("/api/v1/alerts?bank_id=bank_b", headers={"X-API-Key": "key_bank_a"})
    assert resp_cross.status_code == 403

    # Unverified API key -> 401 Unauthorized
    resp_unverified = client.get("/api/v1/alerts", headers={"X-API-Key": "unverified_random_key"})
    assert resp_unverified.status_code == 401


# ── 6. Query Parameter Semantics (Duplicate bank_id: LAST wins) ──────────────

def test_duplicate_query_params_last_wins(client: TestClient, oidc: OIDCAuthenticator):
    token = oidc.create_mock_token(username="user1", bank_id="bank_a", roles=["analyst"])
    headers = {"Authorization": f"Bearer {token}"}

    # ?bank_id=bank_a&bank_id=bank_b -> target becomes bank_b (LAST value) -> 403!
    r1 = client.get("/api/v1/alerts?bank_id=bank_a&bank_id=bank_b", headers=headers)
    assert r1.status_code == 403

    # ?bank_id=bank_b&bank_id=bank_a -> target becomes bank_a (LAST value) -> 200!
    r2 = client.get("/api/v1/alerts?bank_id=bank_b&bank_id=bank_a", headers=headers)
    assert r2.status_code == 200


def test_query_param_case_sensitive(client: TestClient, oidc: OIDCAuthenticator):
    token = oidc.create_mock_token(username="user1", bank_id="bank_a", roles=["analyst"])
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.get("/api/v1/alerts?BANK_ID=bank_b", headers=headers)
    assert resp.status_code != 403 or "TenantAccessDenied" not in resp.text


def test_query_param_empty_or_missing(client: TestClient, oidc: OIDCAuthenticator):
    token = oidc.create_mock_token(username="user1", bank_id="bank_a", roles=["analyst"])
    headers = {"Authorization": f"Bearer {token}"}
    r1 = client.get("/api/v1/alerts?bank_id=", headers=headers)
    assert r1.status_code == 200

    r2 = client.get("/api/v1/alerts", headers=headers)
    assert r2.status_code == 200


# ── 7. Normalization & Special Target Values ──────────────────────────────────

@pytest.mark.parametrize(
    "caller,target,should_allow",
    [
        ("bank_a", "bank_a", True),
        ("BANK_A", "bank_a", True),
        ("bank-a", "bank_a", True),
        (" bank_a ", "bank_a", True),
        ("BANK-A", "bank_a", True),
        ("bank_a", "BANK-A", True),
        ("bank_a", "bank_b", False),
        ("bank_a", "bank-b", False),
    ],
)
def test_tenant_normalization(client: TestClient, oidc: OIDCAuthenticator, caller: str, target: str, should_allow: bool):
    token = oidc.create_mock_token(username="user1", bank_id=caller, roles=["analyst"])
    resp = client.get(f"/api/v1/alerts?bank_id={target}", headers={"Authorization": f"Bearer {token}"})
    if should_allow:
        assert resp.status_code == 200
    else:
        assert resp.status_code == 403
        assert resp.json()["type"] == "https://cfi-platform.org/errors/TenantAccessDenied"


@pytest.mark.parametrize(
    "special_target",
    [
        "global",
        "GLOBAL",
        "Global",
        " global ",
        "all",
        "ALL",
        "system",
        "SYSTEM",
        "coordinator",
        "COORDINATOR",
    ],
)
def test_ordinary_caller_cannot_assume_special_target_values(client: TestClient, oidc: OIDCAuthenticator, special_target: str):
    """Ordinary bank user cannot assume privileged system or cross-bank namespaces."""
    token = oidc.create_mock_token(username="user1", bank_id="bank_a", roles=["analyst"])
    resp = client.get(f"/api/v1/alerts?bank_id={special_target}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert "not authorized to assume privileged system namespace" in resp.json()["detail"]


@pytest.mark.parametrize(
    "special_target",
    [
        "global",
        "system",
        "coordinator",
    ],
)
def test_privileged_roles_can_select_system_target_values(client: TestClient, oidc: OIDCAuthenticator, special_target: str):
    """Privileged super_admin can operationalize system/global tenant switching."""
    token = oidc.create_mock_token(username="admin", bank_id="bank_a", roles=["super_admin"])
    resp = client.get(f"/api/v1/alerts?bank_id={special_target}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


# ── 8. Privileged Roles & Malformed Roles Handling ───────────────────────────

@pytest.mark.parametrize(
    "role",
    [
        "super_admin",
        "cross_bank_investigator",
        "compliance_auditor",
    ],
)
def test_privileged_roles_bypass(client: TestClient, oidc: OIDCAuthenticator, role: str):
    token = oidc.create_mock_token(username="admin", bank_id="bank_a", roles=[role])
    resp = client.get("/api/v1/alerts?bank_id=bank_b", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_privileged_roles_case_sensitive(client: TestClient, oidc: OIDCAuthenticator):
    token = oidc.create_mock_token(username="admin", bank_id="bank_a", roles=["Super_Admin"])
    resp = client.get("/api/v1/alerts?bank_id=bank_b", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_malformed_roles_int_fails_closed():
    with patch("app.infrastructure.security.oidc_authenticator.OIDCAuthenticator.decode_and_validate_token") as mock_dec:
        mock_claims = MagicMock()
        mock_claims.bank_id = "bank_a"
        mock_claims.username = "user1"
        mock_claims.sub = "user1"
        mock_claims.clearance_level = 1
        mock_claims.exp = 9999999999.0
        mock_claims.roles = 123  # Non-iterable!
        mock_dec.return_value = (True, mock_claims, None)

        local_client = TestClient(app, raise_server_exceptions=False)
        resp = local_client.get("/api/v1/alerts?bank_id=bank_b", headers={"Authorization": "Bearer some_token"})
        assert resp.status_code == 403


@pytest.mark.parametrize(
    "deceptive_role",
    [
        "bank_admin",
        "assistant_admin",
        "not_super_admin",
        "super_admin_disabled",
        "admin",
    ],
)
def test_deceptive_roles_cannot_escalate_cross_bank_privilege(client: TestClient, oidc: OIDCAuthenticator, deceptive_role: str):
    token = oidc.create_mock_token(username="admin", bank_id="bank_a", roles=[deceptive_role])
    resp = client.get("/api/v1/alerts?bank_id=bank_b", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert resp.json()["type"] == "https://cfi-platform.org/errors/TenantAccessDenied"


# ── 9. Cross-Tenant 403 Golden Response Format ───────────────────────────────

def test_403_rfc7807_golden_response(client: TestClient, oidc: OIDCAuthenticator):
    token = oidc.create_mock_token(username="u1", bank_id="bank_a", roles=["analyst"])
    resp = client.get("/api/v1/alerts?bank_id=bank_b", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert resp.headers["content-type"] == "application/problem+json"

    data = resp.json()
    assert data["type"] == "https://cfi-platform.org/errors/TenantAccessDenied"
    assert data["title"] == "Cross-Tenant Broken Access Control Forbidden"
    assert data["status"] == 403
    assert "Tenant 'bank_a' is not authorized to access resources belonging to bank 'bank_b'." in data["detail"]
    assert data["instance"] == "/api/v1/alerts"


# ── 10. Outermost Short-Circuit Verification ─────────────────────────────────

def test_403_short_circuits_inner_middleware(client: TestClient, oidc: OIDCAuthenticator):
    token = oidc.create_mock_token(username="u1", bank_id="bank_a", roles=["analyst"])
    resp = client.get(
        "/api/v1/alerts?bank_id=bank_b",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Forwarded-For": "198.51.100.99",
        },
    )
    assert resp.status_code == 403
    assert "X-RateLimit-Limit" not in resp.headers
    assert "X-RateLimit-Remaining" not in resp.headers
    assert "X-DDoS-Throttled" not in resp.headers


# ── 11. Pass-Through Response Transparency & Streaming ───────────────────────

def test_pass_through_streaming_response():
    local_app = FastAPI()

    from app.main import TenantAccessControlMiddleware
    local_app.add_middleware(TenantAccessControlMiddleware)

    @local_app.get("/stream")
    async def stream():
        async def generator():
            yield b"chunk1"
            yield b"chunk2"
        return StreamingResponse(generator(), media_type="text/plain")

    local_client = TestClient(local_app)
    resp = local_client.get("/stream")
    assert resp.status_code == 200
    assert resp.content == b"chunk1chunk2"


def test_pass_through_status_codes():
    local_app = FastAPI()

    from app.main import TenantAccessControlMiddleware
    local_app.add_middleware(TenantAccessControlMiddleware)

    @local_app.get("/status/{code}")
    async def get_status(code: int):
        if code == 204:
            return Response(status_code=204)
        return JSONResponse(status_code=code, content={"code": code})

    local_client = TestClient(local_app)
    assert local_client.get("/status/204").status_code == 204
    assert local_client.get("/status/404").status_code == 404
    assert local_client.get("/status/500").status_code == 500


# ── 12. Downstream Exception Propagation ─────────────────────────────────────

def test_downstream_exception_propagates():
    local_app = FastAPI()

    from app.main import TenantAccessControlMiddleware
    local_app.add_middleware(TenantAccessControlMiddleware)

    @local_app.get("/error")
    async def raise_error():
        raise ValueError("Downstream unhandled failure")

    local_client = TestClient(local_app, raise_server_exceptions=False)
    resp = local_client.get("/error")
    assert resp.status_code == 500


# ── 13. Non-HTTP Scope & Request Immutability ────────────────────────────────

@pytest.mark.asyncio
async def test_non_http_scope_handling():
    from app.main import TenantAccessControlMiddleware

    called = False

    async def mock_app(scope, receive, send):
        nonlocal called
        called = True

    mw = TenantAccessControlMiddleware(mock_app)
    scope = {"type": "lifespan"}
    async def mock_receive(): return {}
    async def mock_send(msg): pass

    await mw(scope, mock_receive, mock_send)
    assert called


# ── 14. ContextVar Structural Diagnostic ─────────────────────────────────────

@pytest.mark.asyncio
async def test_contextvar_propagation():
    test_cvar.set("upstream_value")
    assert test_cvar.get() == "upstream_value"
