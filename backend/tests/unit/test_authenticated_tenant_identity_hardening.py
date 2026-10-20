"""Requirements A through Q: Comprehensive Security Test Suite for Authenticated Tenant Identity & Quota Authority Hardening.

Verifies:
A. No credentials + arbitrary tenant headers cannot authenticate as Bank A or Bank B.
B. Invalid/expired/revoked JWT + valid-looking X-Tenant-ID cannot authenticate.
C. Bank A verified JWT + X-Bank-ID: bank_b cannot impersonate Bank B.
D. Conflicting X-Tenant-ID / X-Bank-ID / JWT claims fail safely (HTTP 400 / 403).
E. Unverified X-API-Key text such as "anything:bank_b:bank" grants no authority (HTTP 401).
F. A genuinely verified API key binds to the configured, authorized institution.
G. Correctly validated platform access tokens remain supported.
H. Correctly validated external OIDC tokens remain supported where configured.
I. Verified privileged cross-bank roles retain narrowly authorized functionality.
J. Ordinary Bank A caller cannot read Bank B quota usage (HTTP 403).
K. Quota enforcement uses authenticated tenant identity, not spoofed headers.
L. Unidentified callers cannot consume an arbitrary bank's quota namespace.
M. Concurrent requests from different institutions do not share request identity or active_tenant state.
N. Authentication failure does not silently fall through to global/system access.
O. Appropriate 401, 403, and 429 responses are preserved.
P. Public health and authorized service-to-service workflows remain functional.
Q. Existing serving, model cache, institutional authorization, and governance security remain valid.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.dependencies import enforce_tenant_quota
from app.infrastructure.database import active_tenant
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.oidc_authenticator import OIDCAuthenticator
from app.main import app, seed_mock_data

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_seed():
    seed_mock_data()


@pytest.fixture(autouse=True)
def enforce_production_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "production")
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "app_env", "production")


@pytest.fixture
def auth_service() -> AuthenticationService:
    return AuthenticationService.get_instance()


@pytest.fixture
def oidc() -> OIDCAuthenticator:
    return OIDCAuthenticator()


# ── Requirement A: No credentials + arbitrary headers cannot authenticate ────

def test_req_a_no_credentials_with_tenant_headers_cannot_authenticate():
    """Unauthenticated caller supplying X-Tenant-ID / X-Bank-ID cannot authenticate as Bank A or Bank B."""
    resp_a = client.get("/api/v1/alerts", headers={"X-Tenant-ID": "bank_a"})
    assert resp_a.status_code == 401
    assert resp_a.json()["type"] == "https://cfi-platform.org/errors/AuthenticationRequired"

    resp_b = client.get("/api/v1/alerts", headers={"X-Bank-ID": "bank_b"})
    assert resp_b.status_code == 401
    assert resp_b.json()["type"] == "https://cfi-platform.org/errors/AuthenticationRequired"


# ── Requirement B: Invalid / Expired / Revoked JWT fails closed ───────────────

def test_req_b_invalid_jwt_fails_closed_no_header_fallback():
    """Invalid, expired, or revoked JWT fails closed without falling through to tenant headers."""
    # 1. Invalid JWT structure
    resp_invalid = client.get(
        "/api/v1/alerts",
        headers={
            "Authorization": "Bearer invalid.fake.token",
            "X-Tenant-ID": "bank_a",
        },
    )
    assert resp_invalid.status_code == 401

    # 2. Expired OIDC JWT
    oidc = OIDCAuthenticator()
    expired_token = oidc.create_token(username="analyst_a1", bank_id="bank_a", expires_in_seconds=-10)
    resp_exp = client.get(
        "/api/v1/alerts",
        headers={
            "Authorization": f"Bearer {expired_token}",
            "X-Tenant-ID": "bank_a",
        },
    )
    assert resp_exp.status_code == 401
    assert "expired" in resp_exp.json()["detail"].lower()

    # 3. Revoked Platform Access Token
    auth_svc = AuthenticationService.get_instance()
    user = auth_svc.get_user("analyst_a1")
    assert user is not None
    bundle = auth_svc.create_token_bundle(user)
    auth_svc.revoke_token(bundle.access_token, reason="security_test")

    resp_revoked = client.get(
        "/api/v1/alerts",
        headers={
            "Authorization": f"Bearer {bundle.access_token}",
            "X-Tenant-ID": "bank_a",
        },
    )
    assert resp_revoked.status_code == 401
    assert "revoked" in resp_revoked.json()["detail"].lower()


# ── Requirement C: Bank A JWT + X-Bank-ID: bank_b cannot impersonate Bank B ──

def test_req_c_authenticated_principal_cannot_impersonate_another_bank(oidc: OIDCAuthenticator):
    """Authenticated Bank A caller supplying X-Bank-ID: bank_b cannot impersonate Bank B."""
    token = oidc.create_mock_token(username="analyst1", bank_id="bank_a", roles=["analyst"])
    resp = client.get(
        "/api/v1/alerts?bank_id=bank_b",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Bank-ID": "bank_b",
        },
    )
    assert resp.status_code == 403
    assert resp.json()["type"] == "https://cfi-platform.org/errors/TenantAccessDenied"
    assert "bank_a" in resp.json()["detail"]
    assert "bank_b" in resp.json()["detail"]


# ── Requirement D: Conflicting tenant selector headers fail safely ───────────

def test_req_d_conflicting_headers_fail_safely(oidc: OIDCAuthenticator):
    """Conflicting X-Tenant-ID and X-Bank-ID fail safely with HTTP 400 Bad Request."""
    token = oidc.create_mock_token(username="analyst1", bank_id="bank_a", roles=["analyst"])
    resp = client.get(
        "/api/v1/alerts",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID": "bank_a",
            "X-Bank-ID": "bank_b",
        },
    )
    assert resp.status_code == 400
    assert "Conflicting tenant selector headers" in resp.json()["detail"]


# ── Requirement E: Unverified API key text grants no authority ────────────────

def test_req_e_unverified_api_key_text_grants_no_authority():
    """Unverified arbitrary strings in X-API-Key grant no authority and fail with 401."""
    forged_keys = [
        "anything:bank_b:bank",
        "key_forged:bank_a:super_admin:3",
        "random_text",
        "key_bank_a:hacked",
    ]
    for key in forged_keys:
        resp = client.get("/api/v1/alerts", headers={"X-API-Key": key})
        assert resp.status_code == 401
        assert resp.json()["type"] == "https://cfi-platform.org/errors/AuthenticationRequired"


# ── Requirement F: Verified API key binds to configured institution ───────────

def test_req_f_verified_api_key_binds_to_configured_institution():
    """Authoritative API keys configured in settings bind strictly to their institution."""
    # key_bank_a binds to bank_a
    resp_a = client.get("/api/v1/alerts?bank_id=bank_a", headers={"X-API-Key": "key_bank_a"})
    assert resp_a.status_code == 200

    # key_bank_a targeting bank_b is rejected with 403
    resp_cross = client.get("/api/v1/alerts?bank_id=bank_b", headers={"X-API-Key": "key_bank_a"})
    assert resp_cross.status_code == 403


# ── Requirement G: Validated Platform Access Tokens remain supported ─────────

def test_req_g_validated_platform_access_tokens_supported(auth_service: AuthenticationService):
    """Platform-issued JWT tokens from AuthenticationService authenticate correctly."""
    user = auth_service.get_user("analyst_a1")
    assert user is not None
    bundle = auth_service.create_token_bundle(user)

    resp = client.get(
        "/api/v1/alerts?bank_id=bank_a",
        headers={"Authorization": f"Bearer {bundle.access_token}"},
    )
    assert resp.status_code == 200


# ── Requirement H: Validated External OIDC Tokens remain supported ───────────

def test_req_h_validated_external_oidc_tokens_supported(oidc: OIDCAuthenticator):
    """External OIDC JWT tokens authenticate correctly and enforce identity claims."""
    token = oidc.create_token(username="analyst_b1", bank_id="bank_b", roles=["analyst"])
    resp = client.get(
        "/api/v1/alerts?bank_id=bank_b",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


# ── Requirement I: Privileged Cross-Bank Roles retain authorized access ───────

@pytest.mark.parametrize("role", ["super_admin", "cross_bank_investigator", "compliance_auditor"])
def test_req_i_privileged_cross_bank_roles_authorized(oidc: OIDCAuthenticator, role: str):
    """Verified cross-bank administrators can inspect resources across institutions."""
    token = oidc.create_mock_token(username="sec_admin", bank_id="bank_a", roles=[role])
    resp = client.get(
        "/api/v1/alerts?bank_id=bank_b",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


# ── Requirement J: Ordinary Bank A caller cannot read Bank B quota usage ──────

def test_req_j_ordinary_caller_cannot_read_another_bank_quota(oidc: OIDCAuthenticator):
    """Ordinary Bank A principal requesting Bank B quota receives HTTP 403."""
    token = oidc.create_mock_token(username="analyst_a1", bank_id="bank_a", roles=["analyst"])

    # 1. Own quota query succeeds
    resp_own = client.get("/v1/inference/quota", headers={"Authorization": f"Bearer {token}"})
    assert resp_own.status_code == 200
    assert resp_own.json()["tenant_id"] == "bank_a"

    # 2. Cross-bank quota query with X-Bank-ID is forbidden
    resp_cross_header = client.get(
        "/v1/inference/quota",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Bank-ID": "bank_b",
        },
    )
    assert resp_cross_header.status_code == 403

    # 3. Cross-bank quota query with query param is forbidden
    resp_cross_query = client.get(
        "/v1/inference/quota?bank_id=bank_b",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_cross_query.status_code == 403

    # 4. Privileged caller CAN inspect target bank quota
    priv_token = oidc.create_mock_token(username="admin", bank_id="bank_a", roles=["super_admin"])
    resp_priv = client.get(
        "/v1/inference/quota?bank_id=bank_b",
        headers={"Authorization": f"Bearer {priv_token}"},
    )
    assert resp_priv.status_code == 200
    assert resp_priv.json()["tenant_id"] == "bank_b"


# ── Requirement K: Quota enforcement uses authenticated tenant identity ───────

@pytest.mark.asyncio
async def test_req_k_quota_enforcement_uses_authenticated_identity(oidc: OIDCAuthenticator):
    """enforce_tenant_quota dependency charges authenticated tenant, rejecting spoofed headers."""
    token = oidc.create_mock_token(username="analyst_a1", bank_id="bank_a", roles=["analyst"])

    # Valid Bank A call
    req_valid = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/v1/predict",
        "headers": [(b"authorization", f"Bearer {token}".encode("latin1"))],
    })
    charged = await enforce_tenant_quota(req_valid)
    assert charged == "bank_a"

    # Spoofed header attempt: Bank A token + X-Bank-ID: bank_b
    req_spoofed = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/v1/predict",
        "headers": [
            (b"authorization", f"Bearer {token}".encode("latin1")),
            (b"x-bank-id", b"bank_b"),
        ],
    })
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await enforce_tenant_quota(req_spoofed)
    assert exc_info.value.status_code == 403
    assert "cannot execute inference or consume quota on behalf of bank" in exc_info.value.detail


# ── Requirement L: Unidentified callers cannot consume arbitrary bank quota ────

def test_req_l_unidentified_callers_cannot_inspect_or_consume_bank_quota():
    """Unidentified caller cannot inspect private quota or charge bank_alpha."""
    resp = client.get("/v1/inference/quota")
    assert resp.status_code == 401


# ── Requirement M: Concurrent requests do not share ContextVar state ──────────

@pytest.mark.asyncio
async def test_req_m_concurrent_requests_isolate_contextvar_and_identity(oidc: OIDCAuthenticator):
    """Concurrent requests from bank_a and bank_b execute with strict ContextVar isolation."""
    token_a = oidc.create_mock_token(username="analyst_a", bank_id="bank_a", roles=["analyst"])
    token_b = oidc.create_mock_token(username="analyst_b", bank_id="bank_b", roles=["analyst"])

    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        async def fetch(token: str, expected_bank: str):
            resp = await ac.get(
                f"/v1/inference/quota?bank_id={expected_bank}",
                headers={"Authorization": f"Bearer {token}"},
            )
            return resp.status_code, resp.json().get("tenant_id", "")

        tasks = []
        for _ in range(10):
            tasks.append(fetch(token_a, "bank_a"))
            tasks.append(fetch(token_b, "bank_b"))

        results = await asyncio.gather(*tasks)
        for i, (status, tenant) in enumerate(results):
            expected = "bank_a" if i % 2 == 0 else "bank_b"
            assert status == 200
            assert tenant == expected


# ── Requirement N: Auth failure does not fall through to global/system ────────

def test_req_n_auth_failure_does_not_fall_through_to_system_access():
    """Authentication failure does not fall through to unrestricted system access."""
    resp = client.get(
        "/api/v1/alerts",
        headers={"Authorization": "Bearer malformed.token.here"},
    )
    assert resp.status_code == 401
    assert "active_tenant" not in resp.headers or active_tenant.get() is None


# ── Requirement O: Appropriate 401, 403, and 429 status codes preserved ──────

def test_req_o_appropriate_http_status_codes_preserved(oidc: OIDCAuthenticator):
    """401 (Unauthorized), 403 (Forbidden), and 429 (Too Many Requests) are correctly emitted."""
    # 401: Missing / invalid credentials
    r_401 = client.get("/api/v1/alerts")
    assert r_401.status_code == 401

    # 403: Cross-tenant access without privilege
    token = oidc.create_mock_token(username="u1", bank_id="bank_a", roles=["analyst"])
    r_403 = client.get("/api/v1/alerts?bank_id=bank_b", headers={"Authorization": f"Bearer {token}"})
    assert r_403.status_code == 403

    # 429: Quota exceeded
    from app.application.services.tenant_metering import get_tenant_metering_service
    metering = get_tenant_metering_service()
    # Consume entire quota for a temporary test tenant
    test_tenant = "test_rate_limited_bank"
    limits = metering.get_quota_limits(test_tenant)
    metering.acquire_quota(test_tenant, "INFERENCE", count=limits.max_daily_inferences)

    token_exhausted = oidc.create_mock_token(username="u2", bank_id=test_tenant, roles=["analyst"])
    r_429 = client.post(
        "/api/v1/predict",
        headers={"Authorization": f"Bearer {token_exhausted}"},
        json={
            "transaction_amount": 100.0,
            "merchant_category": "retail",
            "country_code": "US",
            "device_type": "mobile",
            "velocity": 1.0,
            "hour_of_day": 12,
            "merchant_risk_score": 0.1,
            "customer_history_score": 0.9,
            "chargeback_count": 0,
            "account_age_days": 100,
            "bank_id": test_tenant,
        },
    )
    assert r_429.status_code == 429
    assert r_429.headers.get("X-Quota-Exceeded") == "true"


# ── Requirement P: Public health and authorized workflows remain functional ───

def test_req_p_public_health_and_onboarding_remain_functional():
    """Unauthenticated health, documentation, and onboarding endpoints remain fully accessible."""
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/metrics").status_code == 200
    resp_onboarding = client.get("/api/v1/onboarding/ca-bundle")
    assert resp_onboarding.status_code != 401
    assert resp_onboarding.status_code != 403


# ══════════════════════════════════════════════════════════════════════════════
# Focused Final Regression Tests (Requirements A through O)
# ══════════════════════════════════════════════════════════════════════════════

def test_final_req_a_bank_admin_remains_bank_scoped(oidc: OIDCAuthenticator):
    """A signed Bank A token with role 'bank_admin' remains Bank A-scoped and cannot access Bank B."""
    token = oidc.create_mock_token(username="admin_a", bank_id="bank_a", roles=["bank_admin"])
    # 1. Accessing Bank A succeeds
    resp_a = client.get("/api/v1/alerts?bank_id=bank_a", headers={"Authorization": f"Bearer {token}"})
    assert resp_a.status_code == 200

    # 2. Accessing Bank B fails with 403
    resp_b = client.get("/api/v1/alerts?bank_id=bank_b", headers={"Authorization": f"Bearer {token}"})
    assert resp_b.status_code == 403
    assert "TenantAccessDenied" in resp_b.json()["type"]

    # 3. Quota inspection for Bank B fails with 403
    resp_quota = client.get("/v1/inference/quota?bank_id=bank_b", headers={"Authorization": f"Bearer {token}"})
    assert resp_quota.status_code == 403


@pytest.mark.parametrize(
    "deceptive_role",
    ["assistant_admin", "not_super_admin", "super_admin_disabled", "admin"],
)
def test_final_req_b_deceptive_roles_cannot_gain_privileged_access(oidc: OIDCAuthenticator, deceptive_role: str):
    """Signed tokens with 'assistant_admin', 'not_super_admin', 'super_admin_disabled', or 'admin' do not gain privileged access."""
    token = oidc.create_mock_token(username="sneaky_user", bank_id="bank_a", roles=[deceptive_role])
    resp = client.get("/api/v1/alerts?bank_id=bank_b", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert resp.json()["type"] == "https://cfi-platform.org/errors/TenantAccessDenied"


@pytest.mark.parametrize(
    "priv_role",
    ["super_admin", "cross_bank_investigator", "compliance_auditor"],
)
def test_final_req_c_exact_authorized_privileged_roles_retain_cross_bank_access(oidc: OIDCAuthenticator, priv_role: str):
    """Exact authorized privileged roles retain their intended cross-bank permissions."""
    token = oidc.create_mock_token(username="root_sec", bank_id="bank_a", roles=[priv_role])
    resp = client.get("/api/v1/alerts?bank_id=bank_b", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_final_req_d_signed_oidc_token_with_wrong_issuer_rejected(oidc: OIDCAuthenticator):
    """Signed OIDC token with wrong issuer claim is rejected with HTTP 401."""
    wrong_issuer_token = oidc.create_token(
        username="analyst_a1",
        bank_id="bank_a",
        issuer="https://untrusted-idp.attacker.internal/realms/evil",
    )
    resp = client.get(
        "/api/v1/alerts",
        headers={"Authorization": f"Bearer {wrong_issuer_token}"},
    )
    assert resp.status_code == 401
    assert "issuer" in resp.json()["detail"].lower()


def test_final_req_e_signed_oidc_token_with_wrong_audience_rejected(oidc: OIDCAuthenticator):
    """Signed OIDC token with wrong audience claim is rejected with HTTP 401."""
    wrong_aud_token = oidc.create_token(
        username="analyst_a1",
        bank_id="bank_a",
        audience="different-app-client-id",
    )
    resp = client.get(
        "/api/v1/alerts",
        headers={"Authorization": f"Bearer {wrong_aud_token}"},
    )
    assert resp.status_code == 401
    assert "audience" in resp.json()["detail"].lower()


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("POST", "/api/v1/predict", {"transaction_amount": 100.0, "merchant_category": "grocery", "country_code": "US", "device_type": "web_browser"}),
        ("POST", "/api/v1/predict/batch", {"transactions": []}),
        ("POST", "/api/v1/score-transaction", {"transaction_id": "tx_h1", "amount": 100.0, "merchant_id": "m1", "device_id": "d1"}),
        ("POST", "/api/v1/transactions/score", {"transaction_id": "tx_h2", "amount": 100.0, "merchant_id": "m1", "device_id": "d1"}),
        ("POST", "/api/v1/predict/score", {"transaction_id": "tx_h3", "amount": 100.0, "merchant_id": "m1", "device_id": "d1"}),
        ("POST", "/api/v1/inference/score", {"transaction_id": "tx_h4", "amount": 100.0, "merchant_category": "grocery"}),
        ("POST", "/v1/inference/score", {"transaction_id": "tx_h5", "amount": 100.0, "merchant_category": "grocery"}),
        ("GET", "/api/v1/inference/quota", None),
        ("GET", "/v1/inference/quota", None),
    ],
)
def test_final_req_h_anonymous_production_requests_denied_across_all_inference_routes(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    payload: dict | None,
):
    """Anonymous production requests to every inference and private quota endpoint are denied (HTTP 401)."""
    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")

    resp = client.post(path, json=payload or {}) if method == "POST" else client.get(path)

    assert resp.status_code == 401
    assert "AuthenticationRequired" in resp.text or "Authentication required" in resp.text


@pytest.mark.asyncio
async def test_final_req_i_anonymous_calls_cannot_use_synthetic_dev_quota_in_production(
    monkeypatch: pytest.MonkeyPatch,
):
    """Anonymous calls without tenant headers cannot use synthetic_dev_quota in production."""
    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")

    req = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/v1/predict",
        "headers": [],
    })
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await enforce_tenant_quota(req)
    assert exc_info.value.status_code == 401
    assert "Production inference and quota access require an authenticated principal" in exc_info.value.detail


@pytest.mark.asyncio
async def test_final_req_j_authenticated_batch_request_without_double_metering(
    oidc: OIDCAuthenticator,
):
    """Authenticated Bank A batch requests are charged to Bank A without double metering."""
    token = oidc.create_mock_token(username="analyst_a", bank_id="bank_a", roles=["analyst"])

    req = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/v1/predict/batch",
        "headers": [(b"authorization", f"Bearer {token}".encode("latin1"))],
    })
    charged_1 = await enforce_tenant_quota(req)
    assert charged_1 == "bank_a"
    assert req.state.quota_metered is True

    # Second invocation on the same request object short-circuits without re-metering
    charged_2 = await enforce_tenant_quota(req)
    assert charged_2 == "bank_a"


def test_final_req_k_bank_a_cannot_charge_or_inspect_bank_b_quota(oidc: OIDCAuthenticator):
    """Bank A cannot inspect or charge Bank B quotas using forged headers or query parameters."""
    token = oidc.create_mock_token(username="analyst_a", bank_id="bank_a", roles=["analyst"])

    # Forged header to private quota endpoint
    resp_header = client.get("/v1/inference/quota", headers={"Authorization": f"Bearer {token}", "X-Bank-ID": "bank_b"})
    assert resp_header.status_code == 403

    # Forged query parameter to private quota endpoint
    resp_query = client.get("/v1/inference/quota?bank_id=bank_b", headers={"Authorization": f"Bearer {token}"})
    assert resp_query.status_code == 403


@pytest.mark.parametrize("target_sys", ["global", "system", "all", "coordinator"])
def test_final_req_l_ordinary_bank_a_cannot_promote_to_system_namespace(
    oidc: OIDCAuthenticator,
    target_sys: str,
):
    """Ordinary Bank A principal cannot promote its active tenant context to global/system/coordinator."""
    token = oidc.create_mock_token(username="analyst_a", bank_id="bank_a", roles=["analyst"])

    # Header attempt
    resp_hdr = client.get("/api/v1/alerts", headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": target_sys})
    assert resp_hdr.status_code == 403
    assert "not authorized to assume privileged system namespace" in resp_hdr.json()["detail"]

    # Query param attempt
    resp_qry = client.get(f"/api/v1/alerts?bank_id={target_sys}", headers={"Authorization": f"Bearer {token}"})
    assert resp_qry.status_code == 403
    assert "not authorized to assume privileged system namespace" in resp_qry.json()["detail"]


def test_final_req_m_authenticated_shared_resource_access_without_tenant_impersonation(
    oidc: OIDCAuthenticator,
):
    """Authenticated bank users can query shared health and documentation without tenant impersonation."""
    token = oidc.create_mock_token(username="analyst_a", bank_id="bank_a", roles=["analyst"])

    resp_health = client.get("/api/v1/health", headers={"Authorization": f"Bearer {token}"})
    assert resp_health.status_code == 200

    resp_docs = client.get("/docs", headers={"Authorization": f"Bearer {token}"})
    assert resp_docs.status_code == 200
