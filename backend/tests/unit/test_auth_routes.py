"""Unit and Integration Tests for Authentication, Token Lifecycle & ABAC Session API Routes.

Validates route discovery, Pydantic v2 schemas, JWT rotation, non-blocking bcrypt,
multi-tenant isolation, authentic RFC error codes, and lockout defenses.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.infrastructure.security.auth_service import AuthenticationService
from app.main import app


@pytest.fixture
def client() -> TestClient:
    """TestClient instance bound to the FastAPI application."""
    return TestClient(app)


@pytest.fixture
def auth_service() -> AuthenticationService:
    """Singleton instance of AuthenticationService for resetting state in tests."""
    svc = AuthenticationService.get_instance()
    # Ensure standard test accounts exist
    svc.register_user(
        username="test_analyst_bank_a",
        plain_password="AnalystSecure2026!",
        bank_id="bank_a",
        roles=["analyst", "investigator"],
        clearance_level=2,
    )
    svc.register_user(
        username="test_admin_bank_a",
        plain_password="AdminSecure2026!",
        bank_id="bank_a",
        roles=["admin", "compliance_officer"],
        clearance_level=3,
    )
    svc.register_user(
        username="test_analyst_bank_b",
        plain_password="AnalystBPassword2026!",
        bank_id="bank_b",
        roles=["analyst"],
        clearance_level=1,
    )
    svc.lockout_manager.reset("test_analyst_bank_a")
    svc.lockout_manager.reset("test_admin_bank_a")
    svc.lockout_manager.reset("test_analyst_bank_b")
    svc.lockout_manager.reset("testclient")
    svc.lockout_manager.reset("127.0.0.1")
    return svc


def test_auth_login_success_and_complete_schema(client: TestClient, auth_service: AuthenticationService):
    """Vector 1, 3, 4: Verify POST /login returns full typed LoginResponse with user profile."""
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "test_analyst_bank_a", "password": "AnalystSecure2026!"},
    )
    assert response.status_code == 200, response.text
    data = response.json()

    # Verify top-level fields
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "Bearer"
    assert data["expires_in"] == 900
    assert data["refresh_expires_in"] == 604800
    assert data["tenant_id"] == "bank_a"
    assert data["role"] == "analyst"

    # Verify nested user profile
    user = data["user"]
    assert user["username"] == "test_analyst_bank_a"
    assert user["bank_id"] == "bank_a"
    assert user["tenant_id"] == "bank_a"
    assert "analyst" in user["roles"]
    assert user["clearance_level"] == 2
    assert "read:cases" in user["permissions"]
    assert user["is_active"] is True


def test_auth_login_with_tenant_id_validation(client: TestClient, auth_service: AuthenticationService):
    """Vector 7: Verify tenant_id in LoginRequest enforces multi-tenant boundary."""
    # Matching tenant should succeed
    res_ok = client.post(
        "/api/v1/auth/login",
        json={
            "username": "test_analyst_bank_a",
            "password": "AnalystSecure2026!",
            "tenant_id": "bank_a",
        },
    )
    assert res_ok.status_code == 200

    # Mismatched tenant must return 403 Forbidden
    res_forbidden = client.post(
        "/api/v1/auth/login",
        json={
            "username": "test_analyst_bank_a",
            "password": "AnalystSecure2026!",
            "tenant_id": "bank_b",
        },
    )
    assert res_forbidden.status_code == 403
    assert "cannot access tenant 'bank_b'" in res_forbidden.json()["detail"]


def test_auth_login_admin_cross_tenant_allowed(client: TestClient, auth_service: AuthenticationService):
    """Vector 7: Admin users can authenticate across tenant boundaries."""
    res = client.post(
        "/api/v1/auth/login",
        json={
            "username": "test_admin_bank_a",
            "password": "AdminSecure2026!",
            "tenant_id": "bank_b",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["user"]["username"] == "test_admin_bank_a"
    assert "admin:all" in data["user"]["permissions"]


def test_auth_login_invalid_password_returns_401(client: TestClient, auth_service: AuthenticationService):
    """Vector 6: Invalid password returns RFC-compliant 401 Unauthorized."""
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "test_analyst_bank_a", "password": "WrongPassword999!"},
    )
    assert response.status_code == 401
    assert "Invalid username or password" in response.json()["detail"]


def test_auth_login_validation_bounds(client: TestClient):
    """Vector 3: Invalid input payload rejected with 422 Unprocessable Entity."""
    # Short username
    res_short_u = client.post("/api/v1/auth/login", json={"username": "a", "password": "ValidPassword!"})
    assert res_short_u.status_code == 422

    # Short password (< 6 chars)
    res_short_p = client.post("/api/v1/auth/login", json={"username": "valid_user", "password": "123"})
    assert res_short_p.status_code == 422

    # Disallowed extra fields
    res_extra = client.post(
        "/api/v1/auth/login",
        json={"username": "valid_user", "password": "ValidPassword123!", "extra_field": "injected"},
    )
    assert res_extra.status_code == 422


def test_auth_me_endpoint_success(client: TestClient, auth_service: AuthenticationService):
    """Vector 1, 4, 7: GET /api/v1/auth/me returns active UserProfileResponse."""
    # 1. Login to obtain access token
    login_res = client.post(
        "/api/v1/auth/login",
        json={"username": "test_analyst_bank_a", "password": "AnalystSecure2026!"},
    )
    token = login_res.json()["access_token"]

    # 2. Query /me
    me_res = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_res.status_code == 200, me_res.text
    profile = me_res.json()
    assert profile["username"] == "test_analyst_bank_a"
    assert profile["bank_id"] == "bank_a"
    assert profile["tenant_id"] == "bank_a"
    assert "analyst" in profile["roles"]
    assert profile["clearance_level"] == 2
    assert "write:cases" in profile["permissions"]
    assert profile["is_active"] is True


def test_auth_me_missing_or_invalid_bearer_token(client: TestClient):
    """Vector 6: GET /me without valid Bearer token returns 401 Unauthorized."""
    # Missing header
    res_no_auth = client.get("/api/v1/auth/me")
    assert res_no_auth.status_code == 401

    # Bad header format
    res_bad_format = client.get("/api/v1/auth/me", headers={"Authorization": "Basic invalid"})
    assert res_bad_format.status_code == 401

    # Invalid token string
    res_invalid_tok = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer invalid.jwt.token"})
    assert res_invalid_tok.status_code == 401


def test_auth_me_tenant_mismatch_forbidden(client: TestClient, auth_service: AuthenticationService):
    """Vector 7: GET /me with mismatched X-Tenant-ID header returns 403 Forbidden."""
    login_res = client.post(
        "/api/v1/auth/login",
        json={"username": "test_analyst_bank_a", "password": "AnalystSecure2026!"},
    )
    token = login_res.json()["access_token"]

    # Matching tenant header succeeds
    res_match = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": "bank_a"},
    )
    assert res_match.status_code == 200

    # Cross-tenant mismatch header returns 403
    res_mismatch = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": "bank_b"},
    )
    assert res_mismatch.status_code == 403
    assert "Cross-tenant access forbidden" in res_mismatch.json()["detail"]


def test_auth_refresh_token_rotation(client: TestClient, auth_service: AuthenticationService):
    """Vector 2, 4, 8: POST /refresh exchanges refresh token and rotates it."""
    login_res = client.post(
        "/api/v1/auth/login",
        json={"username": "test_analyst_bank_a", "password": "AnalystSecure2026!"},
    )
    first_access = login_res.json()["access_token"]
    first_refresh = login_res.json()["refresh_token"]

    # Refresh token
    refresh_res = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": first_refresh},
    )
    assert refresh_res.status_code == 200
    refreshed = refresh_res.json()
    assert refreshed["access_token"] != first_access
    assert refreshed["refresh_token"] != first_refresh
    assert refreshed["user"]["username"] == "test_analyst_bank_a"

    # Reusing the old refresh token MUST fail (Single-use rotation)
    reuse_res = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": first_refresh},
    )
    assert reuse_res.status_code == 401


def test_auth_verify_endpoint(client: TestClient, auth_service: AuthenticationService):
    """Vector 1, 4: POST /verify validates token and returns claims."""
    login_res = client.post(
        "/api/v1/auth/login",
        json={"username": "test_analyst_bank_a", "password": "AnalystSecure2026!"},
    )
    token = login_res.json()["access_token"]

    verify_res = client.post(
        "/api/v1/auth/verify",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert verify_res.status_code == 200
    data = verify_res.json()
    assert data["valid"] is True
    assert data["claims"]["username"] == "test_analyst_bank_a"
    assert data["claims"]["bank_id"] == "bank_a"


def test_auth_logout_revocation(client: TestClient, auth_service: AuthenticationService):
    """Vector 2, 4: POST /logout revokes active token and session."""
    login_res = client.post(
        "/api/v1/auth/login",
        json={"username": "test_analyst_bank_a", "password": "AnalystSecure2026!"},
    )
    token = login_res.json()["access_token"]

    # Logout
    logout_res = client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert logout_res.status_code == 200
    assert logout_res.json()["status"] == "logged_out"

    # Token must now be rejected
    verify_after = client.post(
        "/api/v1/auth/verify",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert verify_after.status_code == 401


def test_auth_lockout_status_query(client: TestClient, auth_service: AuthenticationService):
    """Vector 1, 4: GET /lockout-status returns dual-compatible status schema."""
    username = f"lockout_probe_{int(time.time())}"
    status_res = client.get(f"/api/v1/auth/lockout-status?identifier={username}")
    assert status_res.status_code == 200
    data = status_res.json()
    assert data["identifier"] == username
    assert data["is_locked"] is False
    assert data["failed_attempts"] == 0
    assert data["max_attempts"] == 5
    assert data["lockout_duration_seconds"] == 900
    # Compatibility fields
    assert data["is_locked_out"] is False
    assert data["user_failure_count"] == 0
