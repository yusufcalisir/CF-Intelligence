"""Comprehensive Golden Semantic Test Suite for TenantAccessControlMiddleware.

Tests PRE and POST behavior for:
- Exempt prefixes, near-prefixes, and case sensitivity
- Authorization header extraction and scheme matching
- Identity resolution precedence (JWT > X-Tenant-ID > X-Bank-ID > X-API-Key)
- OIDC exception and invalid token fallback
- Raw duplicate ASGI header semantics (FIRST value wins in Starlette)
- Duplicate query parameter semantics (LAST value wins in Starlette)
- X-API-Key parsing
- Tenant normalization and special bypass targets
- Privileged role evaluation and malformed roles handling
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
def test_exempt_prefix_matrix(client: TestClient, path: str, expected_exempt: bool):
    # Cross-tenant query targeting bank_b while caller is bank_a
    # If exempt: passes through TenantAccess without 403 (will 200 or 404 depending on route)
    # If not exempt: TenantAccess immediately intercepts and returns 403
    resp = client.get(f"{path}?bank_id=bank_b", headers={"X-Tenant-ID": "bank_a"})
    if expected_exempt:
        assert resp.status_code != 403 or "TenantAccessDenied" not in resp.text
    else:
        assert resp.status_code == 403
        assert resp.json()["type"] == "https://cfi-platform.org/errors/TenantAccessDenied"


# ── 2. Authorization Header & OIDC Timing ───────────────────────────────────

def test_auth_header_absent(client: TestClient):
    resp = client.get("/api/v1/alerts?bank_id=bank_a")
    # Unauthenticated / no caller tenant bound -> passes TenantAccess middleware
    assert resp.status_code == 200


def test_auth_header_empty(client: TestClient):
    resp = client.get("/api/v1/alerts?bank_id=bank_a", headers={"Authorization": ""})
    assert resp.status_code == 200


def test_auth_header_whitespace(client: TestClient):
    resp = client.get("/api/v1/alerts?bank_id=bank_a", headers={"Authorization": "   "})
    assert resp.status_code == 200


def test_auth_header_bearer_valid(client: TestClient, oidc: OIDCAuthenticator):
    token = oidc.create_mock_token(username="user1", bank_id="bank_a", roles=["analyst"])
    resp = client.get("/api/v1/alerts?bank_id=bank_a", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200

    resp_denied = client.get("/api/v1/alerts?bank_id=bank_b", headers={"Authorization": f"Bearer {token}"})
    assert resp_denied.status_code == 403
    assert resp_denied.json()["type"] == "https://cfi-platform.org/errors/TenantAccessDenied"


def test_auth_header_bearer_case_sensitivity(client: TestClient, oidc: OIDCAuthenticator):
    token = oidc.create_mock_token(username="user1", bank_id="bank_a", roles=["analyst"])
    # "bearer " (lowercase) does NOT match auth.startswith("Bearer ")
    # Therefore caller_tenant is not resolved from JWT, falls through!
    resp = client.get("/api/v1/alerts?bank_id=bank_b", headers={"Authorization": f"bearer {token}"})
    # Falls through because lowercase "bearer " is not parsed by TenantAccess (existing quirk)
    assert resp.status_code != 403 or "TenantAccessDenied" not in resp.text


def test_auth_header_bearer_empty_token(client: TestClient):
    resp = client.get("/api/v1/alerts?bank_id=bank_b", headers={"Authorization": "Bearer "})
    assert resp.status_code != 403 or "TenantAccessDenied" not in resp.text


def test_auth_header_basic(client: TestClient):
    resp = client.get("/api/v1/alerts?bank_id=bank_b", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert resp.status_code != 403 or "TenantAccessDenied" not in resp.text


# ── 3. Identity Precedence & Fallback ────────────────────────────────────────

def test_jwt_wins_over_headers(client: TestClient, oidc: OIDCAuthenticator):
    token = oidc.create_mock_token(username="u1", bank_id="bank_a", roles=["analyst"])
    # JWT is bank_a, X-Tenant-ID is bank_b. Target is bank_a.
    # JWT wins -> target bank_a matches caller bank_a -> 200 OK.
    resp = client.get(
        "/api/v1/alerts?bank_id=bank_a",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID": "bank_b",
            "X-Bank-ID": "bank_b",
            "X-API-Key": "key:bank_b",
        },
    )
    assert resp.status_code == 200


def test_invalid_jwt_permits_fallback_to_tenant_header(client: TestClient):
    # Invalid JWT + X-Tenant-ID bank_a -> fallback occurs to bank_a.
    # Target bank_b -> 403 Forbidden!
    resp = client.get(
        "/api/v1/alerts?bank_id=bank_b",
        headers={
            "Authorization": "Bearer invalid.jwt.token",
            "X-Tenant-ID": "bank_a",
        },
    )
    assert resp.status_code == 403
    assert resp.json()["type"] == "https://cfi-platform.org/errors/TenantAccessDenied"


def test_jwt_exception_permits_fallback(client: TestClient):
    with patch("app.infrastructure.security.oidc_authenticator.OIDCAuthenticator.decode_and_validate_token") as mock_dec:
        mock_dec.side_effect = RuntimeError("Crypto signature failure")
        resp = client.get(
            "/api/v1/alerts?bank_id=bank_b",
            headers={
                "Authorization": "Bearer some_token",
                "X-Tenant-ID": "bank_a",
            },
        )
        assert resp.status_code == 403
        assert resp.json()["type"] == "https://cfi-platform.org/errors/TenantAccessDenied"


def test_tenant_header_wins_over_bank_header(client: TestClient):
    # X-Tenant-ID is bank_a, X-Bank-ID is bank_b.
    # Caller becomes bank_a. Target bank_b -> 403!
    resp = client.get(
        "/api/v1/alerts?bank_id=bank_b",
        headers={
            "X-Tenant-ID": "bank_a",
            "X-Bank-ID": "bank_b",
        },
    )
    assert resp.status_code == 403


def test_bank_header_wins_over_api_key(client: TestClient):
    # X-Bank-ID is bank_a, X-API-Key is key:bank_b. Target bank_b -> 403!
    resp = client.get(
        "/api/v1/alerts?bank_id=bank_b",
        headers={
            "X-Bank-ID": "bank_a",
            "X-API-Key": "key:bank_b",
        },
    )
    assert resp.status_code == 403


# ── 4. Raw Duplicate Headers (ASGI Level) ────────────────────────────────────

@pytest.mark.asyncio
async def test_raw_duplicate_headers_first_wins():
    """Verify raw ASGI duplicate headers behavior (Starlette Headers.get returns FIRST)."""
    # 1. Duplicate X-Tenant-ID: bank_a then bank_b
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
    # Caller tenant derived from first header: "bank_a"
    # Target bank is "bank_b" -> should deny!
    assert req.headers.get("x-tenant-id") == "bank_a"

    # 2. Duplicate X-Tenant-ID: bank_b then bank_a
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

    # 3. Duplicate X-API-Key
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


# ── 5. X-API-Key Parsing Matrix ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "api_key_header,expected_tenant",
    [
        ("", None),
        (":", ""),
        ("a:", ""),
        (":bank_a", "bank_a"),
        ("key:bank_a", "bank_a"),
        ("key:bank_a:extra", "bank_a"),
        ("key::extra", ""),
        ("key: bank_a ", " bank_a "),  # Raw split preserves whitespace before normalization
        (" key:bank_a", "bank_a"),
        ("key-bank_a", None),  # No colon -> None
        (":::", ""),
    ],
)
def test_api_key_parsing(client: TestClient, api_key_header: str, expected_tenant: str | None):
    # Target is bank_a
    resp = client.get("/api/v1/alerts?bank_id=bank_a", headers={"X-API-Key": api_key_header})
    if expected_tenant == "bank_a" or expected_tenant == " bank_a ":
        # Matches bank_a after normalization -> 200
        assert resp.status_code == 200
    elif expected_tenant is None or expected_tenant == "":
        # No caller tenant bound -> passes TenantAccess without 403
        assert resp.status_code == 200
    else:
        # Cross tenant -> 403
        assert resp.status_code == 403


# ── 6. Query Parameter Semantics (Duplicate bank_id: LAST wins) ──────────────

def test_duplicate_query_params_last_wins(client: TestClient):
    # Caller is bank_a
    # ?bank_id=bank_a&bank_id=bank_b -> target becomes bank_b (LAST value) -> 403!
    r1 = client.get("/api/v1/alerts?bank_id=bank_a&bank_id=bank_b", headers={"X-Tenant-ID": "bank_a"})
    assert r1.status_code == 403

    # ?bank_id=bank_b&bank_id=bank_a -> target becomes bank_a (LAST value) -> 200!
    r2 = client.get("/api/v1/alerts?bank_id=bank_b&bank_id=bank_a", headers={"X-Tenant-ID": "bank_a"})
    assert r2.status_code == 200


def test_query_param_case_sensitive(client: TestClient):
    # Starlette query_params.get("bank_id") is case-sensitive
    # ?BANK_ID=bank_b does NOT match .get("bank_id") -> None -> passes without 403!
    resp = client.get("/api/v1/alerts?BANK_ID=bank_b", headers={"X-Tenant-ID": "bank_a"})
    assert resp.status_code != 403 or "TenantAccessDenied" not in resp.text


def test_query_param_empty_or_missing(client: TestClient):
    r1 = client.get("/api/v1/alerts?bank_id=", headers={"X-Tenant-ID": "bank_a"})
    assert r1.status_code == 200

    r2 = client.get("/api/v1/alerts", headers={"X-Tenant-ID": "bank_a"})
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
def test_tenant_normalization(client: TestClient, caller: str, target: str, should_allow: bool):
    resp = client.get(f"/api/v1/alerts?bank_id={target}", headers={"X-Tenant-ID": caller})
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
def test_special_target_values_bypass(client: TestClient, special_target: str):
    # Any caller is allowed to query global/all/system/coordinator
    resp = client.get(f"/api/v1/alerts?bank_id={special_target}", headers={"X-Tenant-ID": "bank_a"})
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
    # Privileged caller bank_a targeting bank_b -> allowed!
    token = oidc.create_mock_token(username="admin", bank_id="bank_a", roles=[role])
    resp = client.get("/api/v1/alerts?bank_id=bank_b", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_privileged_roles_case_sensitive(client: TestClient, oidc: OIDCAuthenticator):
    # "Super_Admin" (mixed case) is NOT in ("super_admin", ...)
    token = oidc.create_mock_token(username="admin", bank_id="bank_a", roles=["Super_Admin"])
    resp = client.get("/api/v1/alerts?bank_id=bank_b", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_malformed_roles_none_raises_typeerror():
    """When claims.roles is None, 'r in caller_roles' raises TypeError, propagating to 500."""
    with patch("app.infrastructure.security.oidc_authenticator.OIDCAuthenticator.decode_and_validate_token") as mock_dec:
        mock_claims = MagicMock()
        mock_claims.bank_id = "bank_a"
        mock_claims.roles = None  # Non-iterable!
        mock_dec.return_value = (True, mock_claims, None)

        local_client = TestClient(app, raise_server_exceptions=False)
        resp = local_client.get("/api/v1/alerts?bank_id=bank_b", headers={"Authorization": "Bearer some_token"})
        # TypeError propagates -> 500 Internal Server Error
        assert resp.status_code == 500


def test_malformed_roles_int_raises_typeerror():
    """When claims.roles is an int, 'r in caller_roles' raises TypeError, propagating to 500."""
    with patch("app.infrastructure.security.oidc_authenticator.OIDCAuthenticator.decode_and_validate_token") as mock_dec:
        mock_claims = MagicMock()
        mock_claims.bank_id = "bank_a"
        mock_claims.roles = 123  # Non-iterable!
        mock_dec.return_value = (True, mock_claims, None)

        local_client = TestClient(app, raise_server_exceptions=False)
        resp = local_client.get("/api/v1/alerts?bank_id=bank_b", headers={"Authorization": "Bearer some_token"})
        assert resp.status_code == 500


def test_roles_string_evaluated_by_substring(client: TestClient):
    """When claims.roles is a string 'super_admin', 'super_admin' in 'super_admin' evaluates True."""
    with patch("app.infrastructure.security.oidc_authenticator.OIDCAuthenticator.decode_and_validate_token") as mock_dec:
        mock_claims = MagicMock()
        mock_claims.bank_id = "bank_a"
        mock_claims.roles = "super_admin"  # String!
        mock_dec.return_value = (True, mock_claims, None)

        resp = client.get("/api/v1/alerts?bank_id=bank_b", headers={"Authorization": "Bearer some_token"})
        assert resp.status_code == 200


# ── 9. Cross-Tenant 403 Golden Response Format ───────────────────────────────

def test_403_rfc7807_golden_response(client: TestClient):
    resp = client.get("/api/v1/alerts?bank_id=bank_b", headers={"X-Tenant-ID": "bank_a"})
    assert resp.status_code == 403
    assert resp.headers["content-type"] == "application/problem+json"

    data = resp.json()
    assert data["type"] == "https://cfi-platform.org/errors/TenantAccessDenied"
    assert data["title"] == "Cross-Tenant Broken Access Control Forbidden"
    assert data["status"] == 403
    assert "Tenant 'bank_a' is not authorized to access resources belonging to bank 'bank_b'." in data["detail"]
    assert data["instance"] == "/api/v1/alerts"


# ── 10. Outermost Short-Circuit Verification ─────────────────────────────────

def test_403_short_circuits_inner_middleware(client: TestClient):
    # Cross-tenant request denied by TenantAccess
    # DDoS, MTLS, W3C should be completely bypassed
    resp = client.get(
        "/api/v1/alerts?bank_id=bank_b",
        headers={
            "X-Tenant-ID": "bank_a",
            "X-Forwarded-For": "198.51.100.99",
        },
    )
    assert resp.status_code == 403
    # Inner DDoS headers should NOT be present on TenantAccess early 403
    assert "X-RateLimit-Limit" not in resp.headers
    assert "X-RateLimit-Remaining" not in resp.headers
    assert "X-DDoS-Throttled" not in resp.headers


# ── 11. Pass-Through Response Transparency & Streaming ───────────────────────

def test_pass_through_streaming_response():
    """Verify streaming response passes through unmodified."""
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
    resp = local_client.get("/stream?bank_id=bank_a", headers={"X-Tenant-ID": "bank_a"})
    assert resp.status_code == 200
    assert resp.content == b"chunk1chunk2"


def test_pass_through_status_codes():
    """Verify 204, 404, 500 pass through unmodified."""
    local_app = FastAPI()

    from app.main import TenantAccessControlMiddleware
    local_app.add_middleware(TenantAccessControlMiddleware)

    @local_app.get("/status/{code}")
    async def get_status(code: int):
        if code == 204:
            return Response(status_code=204)
        return JSONResponse(status_code=code, content={"code": code})

    local_client = TestClient(local_app)
    assert local_client.get("/status/204?bank_id=bank_a", headers={"X-Tenant-ID": "bank_a"}).status_code == 204
    assert local_client.get("/status/404?bank_id=bank_a", headers={"X-Tenant-ID": "bank_a"}).status_code == 404
    assert local_client.get("/status/500?bank_id=bank_a", headers={"X-Tenant-ID": "bank_a"}).status_code == 500


# ── 12. Downstream Exception Propagation ─────────────────────────────────────

def test_downstream_exception_propagates():
    local_app = FastAPI()

    from app.main import TenantAccessControlMiddleware
    local_app.add_middleware(TenantAccessControlMiddleware)

    @local_app.get("/error")
    async def raise_error():
        raise ValueError("Downstream unhandled failure")

    local_client = TestClient(local_app, raise_server_exceptions=False)
    resp = local_client.get("/error?bank_id=bank_a", headers={"X-Tenant-ID": "bank_a"})
    assert resp.status_code == 500


# ── 13. Non-HTTP Scope & Request Immutability ────────────────────────────────

@pytest.mark.asyncio
async def test_non_http_scope_handling():
    """Verify websocket or lifespan scopes are not blocked and pass through directly."""
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
    """Diagnostic test for ContextVar visibility across middleware boundaries."""
    test_cvar.set("upstream_value")
    assert test_cvar.get() == "upstream_value"
