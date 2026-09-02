"""Unit tests for Enterprise Authentication, Bcrypt Hashing, JWT Tokens & Brute-Force Lockout Defense."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.password_hasher import (
    hash_password,
    is_bcrypt_hash,
    verify_password,
)
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def auth_svc() -> AuthenticationService:
    # Reset singleton/instances for isolated tests
    svc = AuthenticationService(
        signing_secret="test_secret_2026_auth_test_key_32_bytes_long_rfc7518_compliant",
        access_token_expire_minutes=15,
        refresh_token_expire_days=7,
        max_auth_failures=5,
        lockout_duration_seconds=900,
    )
    return svc


# ── Bcrypt Password Hashing Tests ─────────────────────────────────────────────


def test_bcrypt_password_hashing_and_verification():
    """Verify passwords are hashed with bcrypt (cost=12) and verified safely."""
    raw_password = "SuperSecretBankPassword2026!"
    hashed = hash_password(raw_password, rounds=12)

    # Must be valid bcrypt hash format
    assert is_bcrypt_hash(hashed)
    assert hashed.startswith("$2b$12$")
    assert len(hashed) == 60

    # Verification must succeed for exact password
    assert verify_password(raw_password, hashed) is True

    # Verification must fail for incorrect password
    assert verify_password("WrongPassword123!", hashed) is False
    assert verify_password("", hashed) is False
    assert verify_password(raw_password, "") is False


def test_bcrypt_rejects_empty_passwords():
    """Verify hashing rejects empty strings."""
    with pytest.raises(ValueError):
        hash_password("")


# ── JWT Token Lifecycle & Rotation Tests ──────────────────────────────────────


def test_jwt_access_and_refresh_token_bundle(auth_svc: AuthenticationService):
    """Verify short-lived access token (15m) and long-lived refresh token generation."""
    user = auth_svc.register_user(
        username="test_analyst",
        plain_password="ValidPassword2026!",
        bank_id="bank_a",
        roles=["analyst", "investigator"],
        clearance_level=2,
    )

    bundle = auth_svc.create_token_bundle(user)
    assert bundle.access_token is not None
    assert bundle.refresh_token is not None
    assert bundle.expires_in == 15 * 60  # 900 seconds (15 minutes)
    assert bundle.refresh_expires_in == 7 * 86400  # 7 days

    # Verify access token claims
    is_valid, claims, msg = auth_svc.verify_access_token(bundle.access_token)
    assert is_valid is True
    assert claims is not None
    assert claims["username"] == "test_analyst"
    assert claims["bank_id"] == "bank_a"
    assert claims["roles"] == ["analyst", "investigator"]
    assert claims["token_type"] == "access"

    # Refresh token rotation: exchange old refresh token for new bundle
    refresh_ok, new_bundle, r_msg = auth_svc.refresh_access_token(bundle.refresh_token)
    assert refresh_ok is True
    assert new_bundle is not None
    assert new_bundle.access_token != bundle.access_token
    assert new_bundle.refresh_token != bundle.refresh_token

    # Reusing the old refresh token MUST fail (Token Rotation Defense)
    reuse_ok, _, err_msg = auth_svc.refresh_access_token(bundle.refresh_token)
    assert reuse_ok is False
    assert "invalid" in err_msg.lower() or "revoked" in err_msg.lower() or "used" in err_msg.lower()


def test_revoked_token_rejection(auth_svc: AuthenticationService):
    """Verify revoked tokens are immediately rejected."""
    user = auth_svc.register_user(
        username="analyst_revokable",
        plain_password="ValidPassword2026!",
    )
    bundle = auth_svc.create_token_bundle(user)

    # Token is valid initially
    valid, _, _ = auth_svc.verify_access_token(bundle.access_token)
    assert valid is True

    # Revoke token
    auth_svc.revoke_token(bundle.access_token)

    # Token must now be rejected
    valid_after, _, err = auth_svc.verify_access_token(bundle.access_token)
    assert valid_after is False
    assert "revoked" in err.lower()


# ── Brute-Force Lockout Defense Tests ─────────────────────────────────────────


def test_brute_force_lockout_after_5_failures(auth_svc: AuthenticationService):
    """Verify 5 consecutive failed attempts trigger 15-minute (900s) account lockout."""
    username = "victim_analyst"
    auth_svc.register_user(
        username=username,
        plain_password="CorrectPassword2026!",
    )

    # Attempts 1 to 4: Failures recorded, not yet locked
    for i in range(1, 5):
        ok, u, msg, status = auth_svc.authenticate(username, "WrongPass!", client_ip="198.51.100.1")
        assert ok is False
        assert status.is_locked is False
        assert status.failed_attempts == i

    # 5th attempt: Triggers lockout
    ok_5, _, msg_5, status_5 = auth_svc.authenticate(username, "WrongPass!", client_ip="198.51.100.1")
    assert ok_5 is False
    assert status_5.is_locked is True
    assert status_5.failed_attempts == 5
    assert status_5.remaining_seconds > 850.0  # Approx 900 seconds remaining

    # 6th attempt: Even with the CORRECT password, login is blocked due to active lockout
    ok_6, _, msg_6, status_6 = auth_svc.authenticate(username, "CorrectPassword2026!", client_ip="198.51.100.1")
    assert ok_6 is False
    assert "locked out" in msg_6.lower()


def test_lockout_reset_on_successful_login(auth_svc: AuthenticationService):
    """Verify failed attempt counter is reset after successful authentication."""
    username = "careful_analyst"
    auth_svc.register_user(
        username=username,
        plain_password="CorrectPassword2026!",
    )

    # 3 failed attempts
    for _ in range(3):
        auth_svc.authenticate(username, "WrongPassword!", client_ip="10.0.0.99")

    status_mid = auth_svc.lockout_manager.check_lockout(username)
    assert status_mid.failed_attempts == 3

    # 4th attempt: Success!
    ok, user, _, status_success = auth_svc.authenticate(username, "CorrectPassword2026!", client_ip="10.0.0.99")
    assert ok is True
    assert user is not None
    assert status_success.failed_attempts == 0

    # Counter should be fully reset
    status_after = auth_svc.lockout_manager.check_lockout(username)
    assert status_after.failed_attempts == 0
    assert status_after.is_locked is False


# ── Full HTTP Endpoint Integration Tests ──────────────────────────────────────


def test_auth_login_api_endpoint(client: TestClient):
    """Test POST /api/v1/auth/login endpoint with seeded credentials."""
    # Successful login with seeded demo analyst
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "analyst_a1", "password": "AnalystSecure2026!"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "Bearer"
    assert data["expires_in"] == 900  # 15 minutes
    assert data["user"]["username"] == "analyst_a1"
    assert data["user"]["bank_id"] == "bank_a"

    access_token = data["access_token"]
    refresh_token = data["refresh_token"]

    # Verify access token via /api/v1/auth/verify
    verify_res = client.post(
        "/api/v1/auth/verify",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert verify_res.status_code == 200
    assert verify_res.json()["valid"] is True
    assert verify_res.json()["claims"]["username"] == "analyst_a1"

    # Refresh token via /api/v1/auth/refresh
    refresh_res = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert refresh_res.status_code == 200
    refreshed_data = refresh_res.json()
    assert "access_token" in refreshed_data
    assert refreshed_data["access_token"] != access_token


def test_auth_login_invalid_password_returns_401(client: TestClient):
    """Test login with incorrect password returns 401 Unauthorized."""
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "IncorrectPassword999!"},
    )
    assert response.status_code == 401
    assert "Invalid username or password" in response.json()["detail"]


def test_auth_lockout_endpoint_triggers_429(client: TestClient):
    """Test 5 consecutive failed logins trigger HTTP 429 Too Many Requests."""
    target_user = f"brute_target_{int(time.time())}"
    svc = AuthenticationService.get_instance()
    svc.lockout_manager.reset("testclient")
    svc.lockout_manager.reset(target_user)

    # Register user via auth service
    svc.register_user(
        username=target_user,
        plain_password="TargetPassword2026!",
    )

    for i in range(4):
        res = client.post(
            "/api/v1/auth/login",
            json={"username": target_user, "password": "BadPassword123!"},
        )
        assert res.status_code == 401

    # 5th attempt returns 429 Too Many Requests with Retry-After header
    res_5 = client.post(
        "/api/v1/auth/login",
        json={"username": target_user, "password": "BadPassword123!"},
    )
    assert res_5.status_code == 429
    assert "Retry-After" in res_5.headers
    assert "locked out" in res_5.json()["detail"].lower()
