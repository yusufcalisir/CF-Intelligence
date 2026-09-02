"""Enterprise Authentication & Token API Router.

Provides endpoints for:
- User login with bcrypt verification, brute-force defense, and 15-minute temporary lockout.
- Short-lived JWT access token issuance (15-30m) + refresh token rotation.
- Token refresh, session verification, and token revocation.
- Lockout status queries.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.rate_limiter import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])

_auth_service = AuthenticationService.get_instance()


# ── Schemas ───────────────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    """User login payload with username and password."""

    username: str = Field(
        ...,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-\.@]+$",
        description="Username or user email",
    )
    password: str = Field(
        ...,
        min_length=6,
        max_length=128,
        description="Account password",
    )


class LoginResponse(BaseModel):
    """Successful authentication response with JWT access and refresh token bundle."""

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int = Field(900, description="Access token expiration in seconds (15 minutes)")
    refresh_expires_in: int = Field(604800, description="Refresh token expiration in seconds (7 days)")
    user: dict[str, Any]


class RefreshRequest(BaseModel):
    """Token refresh payload containing the long-lived refresh token."""

    refresh_token: str = Field(..., min_length=10, description="Signed JWT refresh token")


class TokenVerifyResponse(BaseModel):
    """Access token verification verdict and extracted claims."""

    valid: bool
    claims: dict[str, Any] | None = None
    detail: str


class LogoutRequest(BaseModel):
    """Optional payload specifying refresh or access token to explicitly revoke."""

    token: str | None = Field(None, description="Optional token to revoke")


class LockoutStatusResponse(BaseModel):
    """Brute-force lockout status for an identifier."""

    identifier: str
    is_locked: bool
    failed_attempts: int
    max_attempts: int
    remaining_seconds: float
    lockout_duration_seconds: int


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/login", response_model=LoginResponse)
@limiter.limit("20/minute")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
) -> LoginResponse:
    """Authenticate with username and password.

    Enforces bcrypt verification and 15-minute temporary lockout after
    5 consecutive failed attempts.
    """
    client_ip = request.client.host if request.client else "127.0.0.1"

    success, user, message, lockout = _auth_service.authenticate(
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
    token_bundle = _auth_service.create_token_bundle(user)

    return LoginResponse(
        access_token=token_bundle.access_token,
        refresh_token=token_bundle.refresh_token,
        token_type=token_bundle.token_type,
        expires_in=token_bundle.expires_in,
        refresh_expires_in=token_bundle.refresh_expires_in,
        user={
            "user_id": user.user_id,
            "username": user.username,
            "bank_id": user.bank_id,
            "roles": user.roles,
            "clearance_level": user.clearance_level,
        },
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

    return LoginResponse(
        access_token=bundle.access_token,
        refresh_token=bundle.refresh_token,
        token_type=bundle.token_type,
        expires_in=bundle.expires_in,
        refresh_expires_in=bundle.refresh_expires_in,
        user={
            "user_id": claims.get("sub", "") if claims else "",
            "username": claims.get("username", "") if claims else "",
            "bank_id": claims.get("bank_id", "") if claims else "",
            "roles": claims.get("roles", []) if claims else [],
            "clearance_level": claims.get("clearance_level", 1) if claims else 1,
        },
    )


@router.post("/verify", response_model=TokenVerifyResponse)
async def verify_token(
    authorization: str = Header(..., description="Bearer <token>"),
) -> TokenVerifyResponse:
    """Verify an access token and return decoded user claims."""
    token_parts = authorization.strip().split()
    if len(token_parts) != 2 or token_parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format. Expected 'Bearer <token>'.",
        )

    token_str = token_parts[1]
    is_valid, claims, detail = _auth_service.verify_access_token(token_str)

    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
        )

    return TokenVerifyResponse(
        valid=True,
        claims=claims,
        detail=detail,
    )


@router.post("/logout")
async def logout(
    payload: LogoutRequest | None = None,
    authorization: str | None = Header(None),
) -> dict[str, str]:
    """Revoke tokens and terminate the session."""
    token_to_revoke = None
    if payload and payload.token:
        token_to_revoke = payload.token
    elif authorization and authorization.startswith("Bearer "):
        token_to_revoke = authorization[7:].strip()

    if token_to_revoke:
        _auth_service.revoke_token(token_to_revoke)

    return {"status": "logged_out", "detail": "Session terminated and token invalidated."}


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
    )
