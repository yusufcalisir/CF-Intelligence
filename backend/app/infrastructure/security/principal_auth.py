"""Unified Request Principal & Tenant Identity Authority.

Provides authoritative authenticated principal resolution shared across:
- TenantAccessControlMiddleware
- resolve_tenant() dependency
- enforce_tenant_quota() dependency
- realtime_inference.py quota routes

Guarantees runtime truth:
- No header-based identity impersonation.
- No parsing of tenant identity from unverified X-API-Key text.
- Fail-closed validation for invalid, expired, revoked, or malformed Bearer tokens.
- Strict detection and rejection of conflicting tenant selectors (X-Tenant-ID vs X-Bank-ID).
- Cryptographic verification via platform AuthenticationService or external OIDCAuthenticator.
- Request-local ContextVar isolation without cross-request state leakage.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import jwt
from fastapi import Request

logger = logging.getLogger(__name__)

PRIVILEGED_CROSS_BANK_ROLES = frozenset({
    "super_admin",
    "cross_bank_investigator",
    "compliance_auditor",
})

PRIVILEGED_ROLES = PRIVILEGED_CROSS_BANK_ROLES


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """Represents the cryptographically verified or authoritative identity of an incoming request."""

    principal_id: str
    tenant_id: str | None
    roles: list[str] = field(default_factory=list)
    auth_method: str = "unauthenticated"
    is_authenticated: bool = False
    clearance_level: int = 0
    token_claims: dict[str, Any] = field(default_factory=dict)

    @property
    def is_privileged(self) -> bool:
        """Indicates whether this principal possesses verified cross-bank administrative authority.

        Enforces exact canonical role matching:
        - Exact matching against PRIVILEGED_CROSS_BANK_ROLES only.
        - No substring, prefix, or suffix matching.
        - Malformed or non-string role types are rejected and cannot escalate privilege.
        - Generic "admin" or "bank_admin" roles are institution-scoped, NOT cross-bank privileged.
        """
        if not isinstance(self.roles, (list, tuple, set, frozenset)):
            return False
        return any(isinstance(r, str) and r in PRIVILEGED_CROSS_BANK_ROLES for r in self.roles)


ANONYMOUS_PRINCIPAL = AuthenticatedPrincipal(
    principal_id="anonymous",
    tenant_id=None,
    roles=[],
    auth_method="unauthenticated",
    is_authenticated=False,
)


def check_header_conflicts(request: Request) -> tuple[str | None, int]:
    """Inspects raw ASGI and parsed headers for conflicting or duplicate tenant selectors.

    Returns:
        tuple: (error_message, http_status_code) if conflict exists, else (None, 200).
    """
    headers_obj = getattr(request, "headers", None)
    raw_list: list[tuple[bytes, bytes]] = (
        headers_obj.raw
        if headers_obj is not None and hasattr(headers_obj, "raw")
        else request.scope.get("headers", [])
    )

    tenant_vals: set[str] = set()
    bank_vals: set[str] = set()
    api_key_vals: set[str] = set()

    for k, v in raw_list:
        k_lower = k.lower()
        if k_lower == b"x-tenant-id":
            tenant_vals.add(v.decode("latin1").strip())
        elif k_lower == b"x-bank-id":
            bank_vals.add(v.decode("latin1").strip())
        elif k_lower == b"x-api-key":
            api_key_vals.add(v.decode("latin1").strip())

    if len(tenant_vals) > 1:
        return "Conflicting duplicate X-Tenant-ID headers detected.", 400
    if len(bank_vals) > 1:
        return "Conflicting duplicate X-Bank-ID headers detected.", 400
    if len(api_key_vals) > 1:
        return "Conflicting duplicate X-API-Key headers detected.", 400

    x_tenant = next(iter(tenant_vals), None) if tenant_vals else None
    x_bank = next(iter(bank_vals), None) if bank_vals else None

    if x_tenant and x_bank:
        norm_t = x_tenant.lower().replace("-", "_")
        norm_b = x_bank.lower().replace("-", "_")
        if norm_t != norm_b:
            return "Conflicting tenant selector headers (X-Tenant-ID vs X-Bank-ID).", 400

    return None, 200


def resolve_authenticated_principal(
    request: Request,
) -> tuple[AuthenticatedPrincipal, str | None, int]:
    """Authoritatively validates credentials and resolves the request principal.

    Checks:
    1. Conflicting / duplicate security-sensitive headers (fails with 400).
    2. Bearer JWT tokens:
       - Platform AuthenticationService (HS256, active user, token-type, revocation).
       - External OIDCAuthenticator (signature, expiry, audience, issuer).
       - Fail closed on invalid/expired/revoked tokens (fails with 401).
    3. Gateway API keys:
       - Validated against configured authoritative keys in settings.gateway_api_keys.
       - Fail closed on unverified key text (fails with 401).
    4. Unauthenticated callers: returns ANONYMOUS_PRINCIPAL.

    Returns:
        tuple: (principal, error_message, http_status_code)
    """
    # 1. Header conflict / duplicate check
    conflict_err, status_code = check_header_conflicts(request)
    if conflict_err:
        return ANONYMOUS_PRINCIPAL, conflict_err, status_code

    auth_header = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    api_key_header = request.headers.get("X-API-Key") or request.headers.get("x-api-key") or ""

    # 2. Bearer Token Authentication
    if auth_header.strip():
        auth_clean = auth_header.strip()
        if not auth_clean.lower().startswith("bearer "):
            return ANONYMOUS_PRINCIPAL, "Invalid authorization scheme. Expected Bearer token.", 401

        token = auth_clean[7:].strip()
        if not token:
            return ANONYMOUS_PRINCIPAL, "Empty Bearer token provided.", 401

        # A. Attempt Platform-issued access token
        from app.infrastructure.security.auth_service import AuthenticationService

        try:
            auth_svc = AuthenticationService.get_instance()
            is_valid_plat, plat_claims, plat_msg = auth_svc.verify_access_token(token)
        except Exception as e:
            logger.warning(f"Platform token verification raised exception: {e}")
            is_valid_plat, plat_claims, plat_msg = False, None, f"Token verification failed: {e}"

        if is_valid_plat and plat_claims:
            username = str(plat_claims.get("username") or plat_claims.get("sub") or "")
            tenant_id = plat_claims.get("bank_id")
            raw_plat_roles = plat_claims.get("roles")
            if isinstance(raw_plat_roles, str):
                roles = [raw_plat_roles]
            elif isinstance(raw_plat_roles, (list, tuple, set, frozenset)):
                roles = [str(r) for r in raw_plat_roles if isinstance(r, str)]
            else:
                roles = []
            clearance = int(plat_claims.get("clearance_level", 1))
            principal = AuthenticatedPrincipal(
                principal_id=username,
                tenant_id=tenant_id,
                roles=roles,
                auth_method="bearer_platform",
                is_authenticated=True,
                clearance_level=clearance,
                token_claims=plat_claims,
            )
            return principal, None, 200

        # Fail closed immediately if token was identified as a platform-issued token
        try:
            unverified_claims = jwt.decode(token, options={"verify_signature": False})
            is_platform_token = (
                unverified_claims.get("token_type") == "access"
                or ("jti" in unverified_claims and "bank_id" in unverified_claims)
            )
        except Exception:
            is_platform_token = False

        if is_platform_token and not is_valid_plat:
            return ANONYMOUS_PRINCIPAL, plat_msg or "Platform access token verification failed.", 401

        # If platform identified token was revoked or associated user suspended, fail closed immediately
        if any(term in (plat_msg or "").lower() for term in ("revoked", "suspended", "deactivated", "disabled")):
            return ANONYMOUS_PRINCIPAL, plat_msg, 401

        # B. Attempt External OIDC token
        from app.infrastructure.security.oidc_authenticator import OIDCAuthenticator

        try:
            oidc_auth = OIDCAuthenticator()
            is_valid_oidc, oidc_claims, oidc_msg = oidc_auth.decode_and_validate_token(token)
        except Exception as e:
            logger.warning(f"OIDC token verification raised exception: {e}")
            is_valid_oidc, oidc_claims, oidc_msg = False, None, f"Token validation failed: {e}"

        if is_valid_oidc and oidc_claims:
            tenant_id = oidc_claims.bank_id.strip() if oidc_claims.bank_id else None
            raw_oidc_roles = getattr(oidc_claims, "roles", None)
            if isinstance(raw_oidc_roles, str):
                roles = [raw_oidc_roles]
            elif isinstance(raw_oidc_roles, (list, tuple, set, frozenset)):
                roles = [str(r) for r in raw_oidc_roles if isinstance(r, str)]
            else:
                roles = []
            principal = AuthenticatedPrincipal(
                principal_id=oidc_claims.username or oidc_claims.sub,
                tenant_id=tenant_id,
                roles=roles,
                auth_method="bearer_oidc",
                is_authenticated=True,
                clearance_level=oidc_claims.clearance_level,
                token_claims={
                    "sub": oidc_claims.sub,
                    "username": oidc_claims.username,
                    "bank_id": tenant_id,
                    "roles": oidc_claims.roles,
                    "exp": oidc_claims.exp,
                },
            )
            return principal, None, 200

        # Neither validator accepted the token -> fail closed
        err_msg = plat_msg if "expired" in (plat_msg or "").lower() else (oidc_msg or "Invalid or unverifiable Bearer token.")
        return ANONYMOUS_PRINCIPAL, err_msg, 401

    # 3. Gateway API Key Authentication
    if api_key_header.strip():
        from app.presentation.routers.gateway import get_api_keys

        keys_map = get_api_keys()
        raw_key = api_key_header.strip()

        if raw_key in keys_map:
            identity, role, clearance = keys_map[raw_key]
            tenant = None
            if role == "bank" or identity.startswith("bank_"):
                tenant = identity
            else:
                from app.infrastructure.security.auth_service import AuthenticationService

                user = AuthenticationService.get_instance().get_user(identity)
                if user:
                    tenant = user.bank_id
                elif identity.startswith("bank_"):
                    tenant = identity

            principal = AuthenticatedPrincipal(
                principal_id=identity,
                tenant_id=tenant,
                roles=[role],
                auth_method="gateway_api_key",
                is_authenticated=True,
                clearance_level=clearance,
                token_claims={"key_identity": identity, "role": role},
            )
            return principal, None, 200
        else:
            return ANONYMOUS_PRINCIPAL, "Invalid or unverified API key.", 401

    # 4. No credentials provided
    return ANONYMOUS_PRINCIPAL, None, 200


def resolve_authorized_tenant(
    principal: AuthenticatedPrincipal,
    request: Request,
) -> tuple[str | None, str | None, int]:
    """Determines the authorized tenant identity for the request, validating resource selectors against caller authority.

    Returns:
        tuple: (authorized_tenant_or_selector, error_detail, http_status_code)
    """
    x_tenant = request.headers.get("X-Tenant-ID") or request.headers.get("x-tenant-id")
    x_bank = request.headers.get("X-Bank-ID") or request.headers.get("x-bank-id")
    query_bank = request.query_params.get("bank_id")

    selector = (x_tenant or x_bank or "").strip()
    target_selector = selector or (query_bank.strip() if query_bank else "")

    if not principal.is_authenticated:
        # Unauthenticated:
        # A caller without valid credentials CANNOT claim any tenant authority via headers!
        if selector or query_bank:
            return None, "Unauthenticated request cannot specify or assume a tenant identity.", 401
        return None, None, 200

    caller_tenant = principal.tenant_id

    # Privileged cross-bank authority
    if principal.is_privileged:
        target = target_selector or caller_tenant
        return (target if target else None), None, 200

    # Ordinary caller
    if target_selector:
        norm_target = target_selector.lower().replace("-", "_").strip()
        norm_caller = (caller_tenant or "").lower().replace("-", "_").strip()

        # Reject ordinary callers attempting to assume privileged system or cross-bank namespaces
        if norm_target in ("global", "all", "system", "coordinator"):
            return (
                None,
                f"Tenant '{caller_tenant}' is not authorized to assume privileged system namespace '{target_selector}'.",
                403,
            )

        if norm_caller and norm_target != norm_caller:
            # Caller attempted cross-tenant access without privilege!
            return (
                None,
                f"Tenant '{caller_tenant}' is not authorized to access resources belonging to bank '{target_selector}'.",
                403,
            )

    return caller_tenant, None, 200
