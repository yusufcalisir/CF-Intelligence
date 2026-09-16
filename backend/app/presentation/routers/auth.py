"""Enterprise Authentication & Token API Router.

Provides endpoints for:
- User login with bcrypt verification, brute-force defense, and 15-minute temporary lockout.
- Short-lived JWT access token issuance (15-30m) + refresh token rotation.
- Token refresh, session verification, and token revocation.
- Current user profile queries (/me) with multi-tenant ABAC isolation.
- Lockout status queries.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Header, HTTPException, Request, Response, status

from app.application.schemas.auth import (
    LockoutStatusResponse,
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    LogoutResponse,
    RefreshRequest,
    RefreshTokenRequest,
    TokenResponse,
    TokenVerifyResponse,
    UserProfileResponse,
)
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.rate_limiter import limiter

__all__ = [
    "LockoutStatusResponse",
    "LoginRequest",
    "LoginResponse",
    "LogoutRequest",
    "LogoutResponse",
    "RefreshRequest",
    "RefreshTokenRequest",
    "TokenResponse",
    "TokenVerifyResponse",
    "UserProfileResponse",
    "router",
]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])

_auth_service = AuthenticationService.get_instance()


def _derive_permissions(roles: list[str]) -> list[str]:
    """Derive fine-grained ABAC permissions from assigned user roles."""
    perms: set[str] = set()
    for role in roles:
        r = role.lower().strip()
        if r in ("admin", "super_admin"):
            perms.update([
                "read:all",
                "write:all",
                "admin:all",
                "cases:manage",
                "alerts:manage",
                "models:manage",
                "governance:vote",
            ])
        elif r in ("compliance_officer", "compliance_auditor"):
            perms.update([
                "read:all",
                "cases:review",
                "alerts:review",
                "compliance:file_sar",
                "audit:read",
            ])
        elif r in ("analyst", "investigator"):
            perms.update([
                "read:cases",
                "write:cases",
                "read:alerts",
                "write:alerts",
                "copilot:query",
            ])
        elif r == "auditor":
            perms.update(["read:all", "audit:read"])
        else:
            perms.add(f"read:{r}")
    return sorted(perms)


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/login", response_model=LoginResponse)
@limiter.limit("20/minute")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
) -> LoginResponse:
    """Authenticate with username and password.

    Enforces bcrypt verification offloaded to a worker thread to prevent event-loop
    blocking, multi-tenant isolation, and a 15-minute temporary lockout after
    5 consecutive failed attempts.
    """
    client_ip = request.client.host if request.client else "127.0.0.1"

    # Execute CPU-intensive bcrypt hashing in threadpool (Vector 13: Non-Blocking I/O)
    success, user, message, lockout = await asyncio.to_thread(
        _auth_service.authenticate,
        username=payload.username,
        plain_password=payload.password,
        client_ip=client_ip,
    )

    if not success:
        if lockout.is_locked:
            # 429 Too Many Requests with Retry-After header
            response.headers["Retry-After"] = str(int(lockout.remaining_seconds))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=message,
                headers={"Retry-After": str(int(lockout.remaining_seconds))},
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=message,
        )

    assert user is not None

    # Multi-tenant domain isolation check (Vector 7)
    if payload.tenant_id:
        target_tenant = payload.tenant_id.lower().replace("-", "_").strip()
        user_tenant = user.bank_id.lower().replace("-", "_").strip()
        is_admin = any(r in user.roles for r in ("admin", "super_admin"))
        if not is_admin and target_tenant != user_tenant:
            logger.warning(
                "Multi-Tenant Isolation: User '%s' (bank '%s') attempted login to tenant '%s'",
                user.username,
                user.bank_id,
                payload.tenant_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"User '{user.username}' is assigned to bank '{user.bank_id}' and cannot access tenant '{payload.tenant_id}'.",
            )

    token_bundle = _auth_service.create_token_bundle(user)
    derived_perms = _derive_permissions(user.roles)
    primary_role = user.roles[0] if user.roles else "analyst"

    user_profile = UserProfileResponse(
        user_id=user.user_id,
        username=user.username,
        bank_id=user.bank_id,
        tenant_id=user.bank_id,
        roles=user.roles,
        clearance_level=user.clearance_level,
        permissions=derived_perms,
        is_active=user.is_active,
    )

    return LoginResponse(
        access_token=token_bundle.access_token,
        refresh_token=token_bundle.refresh_token,
        token_type=token_bundle.token_type,
        expires_in=token_bundle.expires_in,
        refresh_expires_in=token_bundle.refresh_expires_in,
        tenant_id=user.bank_id,
        role=primary_role,
        user=user_profile,
    )


@router.post("/refresh", response_model=LoginResponse)
@limiter.limit("30/minute")
async def refresh_token(
    payload: RefreshRequest,
    request: Request,
) -> LoginResponse:
    """Exchange a valid refresh token for a new access token and rotated refresh token."""
    success, bundle, detail = _auth_service.refresh_access_token(payload.refresh_token)

    if not success or bundle is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
        )

    # Decode claims to return user details
    _, claims, _ = _auth_service.verify_access_token(bundle.access_token)
    roles = claims.get("roles", ["analyst"]) if claims else ["analyst"]
    bank_id = claims.get("bank_id", "bank_a") if claims else "bank_a"
    username = claims.get("username", "") if claims else ""
    user_id = claims.get("sub", "") if claims else ""
    clearance = claims.get("clearance_level", 1) if claims else 1
    primary_role = roles[0] if roles else "analyst"
    derived_perms = _derive_permissions(roles)

    user_profile = UserProfileResponse(
        user_id=user_id,
        username=username,
        bank_id=bank_id,
        tenant_id=bank_id,
        roles=roles,
        clearance_level=clearance,
        permissions=derived_perms,
        is_active=True,
    )

    return LoginResponse(
        access_token=bundle.access_token,
        refresh_token=bundle.refresh_token,
        token_type=bundle.token_type,
        expires_in=bundle.expires_in,
        refresh_expires_in=bundle.refresh_expires_in,
        tenant_id=bank_id,
        role=primary_role,
        user=user_profile,
    )


@router.get("/me", response_model=UserProfileResponse)
async def get_current_user(
    authorization: str | None = Header(None, description="Bearer <access_token>"),
    x_tenant_id: str | None = Header(None, alias="X-Tenant-ID", description="Active tenant header"),
) -> UserProfileResponse:
    """Retrieve profile and fine-grained permissions for the active authenticated user."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header. Expected 'Bearer <token>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_parts = authorization.strip().split()
    if len(token_parts) != 2 or token_parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format. Expected 'Bearer <token>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_str = token_parts[1]
    is_valid, claims, detail = _auth_service.verify_access_token(token_str)

    if not is_valid or not claims:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail or "Authentication token is invalid or has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    username = claims.get("username", "")
    token_bank_id = claims.get("bank_id", "")
    roles = claims.get("roles", [])

    # Multi-tenant cross-tenant header validation (Vector 7)
    if x_tenant_id:
        norm_header_tenant = x_tenant_id.lower().replace("-", "_").strip()
        norm_token_tenant = token_bank_id.lower().replace("-", "_").strip()
        is_cross_bank_authorized = any(
            r in roles for r in ("admin", "super_admin", "cross_bank_investigator")
        )
        if not is_cross_bank_authorized and norm_header_tenant != norm_token_tenant:
            logger.warning(
                "ABAC Multi-Tenant Violation: Token bank '%s' mismatched X-Tenant-ID '%s'",
                token_bank_id,
                x_tenant_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Cross-tenant access forbidden. Token bank '{token_bank_id}' does not match requested tenant '{x_tenant_id}'.",
            )

    user = _auth_service.get_user(username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User account '{username}' was not found or is inactive.",
        )

    return UserProfileResponse(
        user_id=user.user_id,
        username=user.username,
        bank_id=user.bank_id,
        tenant_id=user.bank_id,
        roles=user.roles,
        clearance_level=user.clearance_level,
        permissions=_derive_permissions(user.roles),
        is_active=user.is_active,
    )


@router.post("/verify", response_model=TokenVerifyResponse)
async def verify_token(
    authorization: str | None = Header(None, description="Bearer <token>"),
) -> TokenVerifyResponse:
    """Verify an access token and return decoded user claims."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header. Expected 'Bearer <token>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_parts = authorization.strip().split()
    if len(token_parts) != 2 or token_parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format. Expected 'Bearer <token>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_str = token_parts[1]
    is_valid, claims, detail = _auth_service.verify_access_token(token_str)

    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )

    return TokenVerifyResponse(
        valid=True,
        claims=claims,
        detail=detail,
    )


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    payload: LogoutRequest | None = None,
    authorization: str | None = Header(None),
) -> LogoutResponse:
    """Revoke tokens and terminate the session."""
    token_to_revoke = None
    if payload and payload.token:
        token_to_revoke = payload.token
    elif authorization and authorization.startswith("Bearer "):
        token_to_revoke = authorization[7:].strip()

    if token_to_revoke:
        _auth_service.revoke_token(token_to_revoke)

    return LogoutResponse(
        status="logged_out",
        detail="Session terminated and token invalidated.",
    )


@router.get("/lockout-status", response_model=LockoutStatusResponse)
async def get_lockout_status(
    identifier: str,
) -> LockoutStatusResponse:
    """Query lockout status for an IP address or username account."""
    status_obj = _auth_service.lockout_manager.check_lockout(identifier)
    return LockoutStatusResponse(
        identifier=identifier,
        is_locked=status_obj.is_locked,
        failed_attempts=status_obj.failed_attempts,
        max_attempts=status_obj.max_attempts,
        remaining_seconds=status_obj.remaining_seconds,
        lockout_duration_seconds=status_obj.lockout_duration_seconds,
        is_locked_out=status_obj.is_locked,
        remaining_lockout_seconds=status_obj.remaining_seconds,
        user_failure_count=status_obj.failed_attempts,
        ip_failure_count=status_obj.failed_attempts,
    )
