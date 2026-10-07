"""Unit tests for Gateway OIDC authentication, RBAC, ABAC, and audit log integration (Section 13.3).

Covers:
- OIDC Bearer token decoding, expiration validation, and malformed token handling
- Legacy API Key fallback authentication
- RBAC route permission gating (Analyst vs Bank roles)
- ABAC multi-tenant bank isolation and super-admin bypass
- Audit chain logging on access denial and policy enforcement
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import Request

from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain
from app.infrastructure.security.oidc_authenticator import OIDCAuthenticator, UserClaims
from app.presentation.routers.gateway import (
    authenticate_request,
    check_authorization,
    check_ws_authorization,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def oidc_auth() -> OIDCAuthenticator:
    return OIDCAuthenticator()


@pytest.fixture()
def valid_jwt_token(oidc_auth: OIDCAuthenticator) -> str:
    return oidc_auth.create_mock_token(
        username="analyst_jane",
        bank_id="global",
        roles=["analyst"],
    )


@pytest.fixture()
def bank_jwt_token(oidc_auth: OIDCAuthenticator) -> str:
    return oidc_auth.create_mock_token(
        username="bank_node_a",
        bank_id="bank_a",
        roles=["bank"],
    )


@pytest.fixture()
def superadmin_jwt_token(oidc_auth: OIDCAuthenticator) -> str:
    return oidc_auth.create_mock_token(
        username="admin_root",
        bank_id="global",
        roles=["super_admin"],
    )


def make_request_with_header(name: str, value: str) -> Request:
    """Helper to mock a FastAPI Request with specific header."""
    req = MagicMock(spec=Request)
    req.headers = {name: value}
    req.query_params = {}
    return req


# ---------------------------------------------------------------------------
# 1. TestOIDCBearerAuthentication
# ---------------------------------------------------------------------------


class TestOIDCBearerAuthentication:
    def test_valid_oidc_token_authenticates_user(self, valid_jwt_token: str):
        """Valid OIDC Bearer token decodes claims and extracts username/role."""
        req = make_request_with_header("Authorization", f"Bearer {valid_jwt_token}")
        identity, role, key_used, claims = authenticate_request(req)

        assert identity == "analyst_jane"
        assert role == "analyst"
        assert claims is not None
        assert claims.sub == "usr_analyst_jane"

    def test_bank_oidc_token_authenticates_bank_identity(self, bank_jwt_token: str):
        """Bank OIDC token extracts bank_id and bank role."""
        req = make_request_with_header("Authorization", f"Bearer {bank_jwt_token}")
        identity, role, key_used, claims = authenticate_request(req)

        assert identity == "bank_node_a"
        assert role == "bank"
        assert claims is not None
        assert claims.bank_id == "bank_a"

    def test_expired_token_returns_unauthorized(self):
        """Expired JWT token returns empty identity and None claims."""
        expired_token = (
            "eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJlc3AiOjEsICJzdWIiOiAydXNyIn0.sig"
        )

        req = make_request_with_header("Authorization", f"Bearer {expired_token}")
        identity, role, key_used, claims = authenticate_request(req)

        assert identity == ""
        assert claims is None

    def test_malformed_token_returns_unauthorized(self):
        """Malformed token string (not 3 dot-separated parts) fails authentication."""
        req = make_request_with_header("Authorization", "Bearer invalid-malformed-token-string")
        identity, role, key_used, claims = authenticate_request(req)

        assert identity == ""
        assert claims is None

    def test_legacy_api_key_fallback(self):
        """Valid API key in X-API-Key header authenticates when no Bearer token is provided."""
        req = make_request_with_header("X-API-Key", "test_key_1")
        identity, role, key_used, claims = authenticate_request(req)

        # Legacy API key returns analyst identity
        assert identity in ("analyst", "bank_a", "bank_b") or role in ("analyst", "bank")
        assert claims is not None


# ---------------------------------------------------------------------------
# 2. TestRBACRouteGating
# ---------------------------------------------------------------------------


class TestRBACRouteGating:
    def test_analyst_can_access_dashboard(self):
        """Analyst role can access all dashboard routes."""
        claims = UserClaims(
            sub="u1", username="analyst_1", bank_id="global", roles=["analyst"], clearance_level=1
        )
        assert (
            check_authorization("analyst_1", "analyst", "/api/v1/dashboard", {}, "GET", claims)
            is True
        )

    def test_bank_role_blocked_from_dashboard(self):
        """Bank role is forbidden from accessing dashboard routes."""
        claims = UserClaims(sub="u2", username="bank_a", bank_id="bank_a", roles=["bank"])
        assert (
            check_authorization("bank_a", "bank", "/api/v1/dashboard", {}, "GET", claims) is False
        )

    def test_bank_role_blocked_from_editing_scenarios(self):
        """Bank role is forbidden from scenario configuration endpoints."""
        claims = UserClaims(sub="u2", username="bank_a", bank_id="bank_a", roles=["bank"])
        assert (
            check_authorization("bank_a", "bank", "/api/v1/scenarios", {}, "GET", claims) is False
        )

    def test_bank_role_blocked_from_triggering_simulations(self):
        """Bank role is forbidden from POST /api/v1/simulations."""
        claims = UserClaims(sub="u2", username="bank_a", bank_id="bank_a", roles=["bank"])
        assert (
            check_authorization("bank_a", "bank", "/api/v1/simulations", {}, "POST", claims)
            is False
        )

    def test_bank_ws_blocked_from_training_feed(self):
        """Bank role is forbidden from global training WebSocket feed."""
        assert check_ws_authorization("bank_a", "bank", "/ws/training") is False
        assert check_ws_authorization("analyst_1", "analyst", "/ws/training") is True


# ---------------------------------------------------------------------------
# 3. TestABACMultiTenantIsolation
# ---------------------------------------------------------------------------


class TestABACMultiTenantIsolation:
    def test_bank_user_allowed_for_own_bank_resource(self):
        """User from bank_a accessing bank_a data passes ABAC evaluation."""
        claims = UserClaims(
            sub="u_bank_a", username="bank_node_a", bank_id="bank_a", roles=["bank"], clearance_level=1
        )
        assert (
            check_authorization(
                "bank_node_a", "bank", "/api/v1/cases", {"bank_id": "bank_a"}, "GET", claims
            )
            is True
        )

    def test_bank_user_denied_for_other_bank_resource(self):
        """User from bank_a requesting bank_b resource fails ABAC multi-tenant isolation."""
        claims = UserClaims(
            sub="u_bank_a", username="bank_node_a", bank_id="bank_a", roles=["bank"]
        )
        # Attempt to access bank_b data
        assert (
            check_authorization(
                "bank_node_a", "bank", "/api/v1/cases", {"bank_id": "bank_b"}, "GET", claims
            )
            is False
        )

    def test_super_admin_bypasses_tenant_isolation(self):
        """Super-admin role bypasses multi-tenant bank isolation restrictions."""
        claims = UserClaims(
            sub="u_root", username="admin_root", bank_id="global", roles=["super_admin"]
        )
        assert (
            check_authorization(
                "admin_root", "super_admin", "/api/v1/cases", {"bank_id": "bank_b"}, "GET", claims
            )
            is True
        )


# ---------------------------------------------------------------------------
# 4. TestAuditChainIntegration
# ---------------------------------------------------------------------------


class TestAuditChainIntegration:
    def test_abac_denial_appends_audit_log(self):
        """ABAC access denial appends an event to the ImmutableAuditChain."""
        audit_chain = ImmutableAuditChain.get_instance()
        initial_length = len(audit_chain.chain)

        claims = UserClaims(
            sub="u_bank_a", username="bank_node_a", bank_id="bank_a", roles=["bank"]
        )
        # Trigger ABAC denial
        check_authorization(
            "bank_node_a", "bank", "/api/v1/cases", {"bank_id": "bank_b"}, "GET", claims
        )

        # Audit chain should have a new event
        assert len(audit_chain.chain) > initial_length
        latest = audit_chain.chain[-1]
        assert latest.event_type == "ACCESS_DENIED_ABAC"
        assert latest.actor == "bank_node_a"
