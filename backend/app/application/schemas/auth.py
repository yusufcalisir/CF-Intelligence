"""Enterprise Authentication & ABAC Session Schemas.

Pydantic v2 schemas for authentication, token issuance, refresh rotation,
session profile, and brute-force lockout status.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    """User login payload with username, password, and optional tenant identifier."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(
        ...,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-\.@]+$",
        description="Username or user email identifier",
    )
    password: str = Field(
        ...,
        min_length=6,
        max_length=128,
        description="Account password",
    )
    tenant_id: str | None = Field(
        None,
        min_length=2,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Optional target bank or tenant identifier for domain isolation",
    )


class UserProfileResponse(BaseModel):
    """Authenticated user profile with clearance level, roles, and derived permissions."""

    model_config = ConfigDict(extra="ignore")

    user_id: str = Field(..., description="Unique immutable user identifier (e.g. usr_...)")
    username: str = Field(..., description="Login username")
    bank_id: str = Field(..., description="Affiliated bank institution identifier")
    tenant_id: str = Field(..., description="Active tenant identifier")
    roles: list[str] = Field(default_factory=list, description="Assigned RBAC roles")
    clearance_level: int = Field(1, ge=1, le=5, description="Institutional clearance level (1 to 5)")
    permissions: list[str] = Field(default_factory=list, description="Fine-grained ABAC permission tokens")
    is_active: bool = Field(True, description="Account active status")


class LoginResponse(BaseModel):
    """Successful authentication response with JWT access and refresh token bundle."""

    model_config = ConfigDict(extra="ignore")

    access_token: str = Field(..., description="Short-lived HMAC-SHA256 JWT access token")
    refresh_token: str = Field(..., description="Long-lived cryptographically signed refresh token")
    token_type: str = Field("Bearer", description="Token scheme (RFC 6750)")
    expires_in: int = Field(900, description="Access token lifetime in seconds (15 minutes)")
    refresh_expires_in: int = Field(604800, description="Refresh token lifetime in seconds (7 days)")
    tenant_id: str = Field(..., description="Active bank/tenant identifier")
    role: str = Field(..., description="Primary user role")
    user: UserProfileResponse = Field(..., description="Detailed user profile payload")


# Backward compatibility alias
TokenResponse = LoginResponse


class RefreshRequest(BaseModel):
    """Token refresh payload containing the long-lived refresh token."""

    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(
        ...,
        min_length=10,
        max_length=4096,
        description="Signed JWT refresh token",
    )


# Backward compatibility alias
RefreshTokenRequest = RefreshRequest


class TokenVerifyResponse(BaseModel):
    """Access token verification verdict and extracted claims."""

    model_config = ConfigDict(extra="ignore")

    valid: bool = Field(..., description="Token validity status")
    claims: dict[str, Any] | None = Field(None, description="Decoded JWT claims if valid")
    detail: str = Field(..., description="Human-readable verification verdict")


class LogoutRequest(BaseModel):
    """Optional payload specifying refresh or access token to explicitly revoke."""

    model_config = ConfigDict(extra="forbid")

    token: str | None = Field(
        None,
        min_length=10,
        max_length=4096,
        description="Optional token string to revoke",
    )


class LogoutResponse(BaseModel):
    """Standardized logout confirmation payload."""

    model_config = ConfigDict(extra="ignore")

    status: str = Field("logged_out", description="Session state")
    detail: str = Field(
        "Session terminated and token invalidated.",
        description="Logout outcome detail",
    )


class LockoutStatusResponse(BaseModel):
    """Brute-force lockout status for an identifier with dual-naming frontend parity."""

    model_config = ConfigDict(extra="ignore")

    identifier: str = Field(..., description="Queried IP address or username")
    is_locked: bool = Field(..., description="Whether the identifier is actively locked")
    failed_attempts: int = Field(..., ge=0, description="Number of consecutive failed attempts")
    max_attempts: int = Field(..., gt=0, description="Threshold triggering temporary lockout")
    remaining_seconds: float = Field(..., ge=0.0, description="Remaining seconds until lockout expires")
    lockout_duration_seconds: int = Field(..., gt=0, description="Base lockout duration window in seconds")

    # Compatibility fields for frontend e2e and client components
    is_locked_out: bool | None = Field(None, description="Compatibility alias for is_locked")
    remaining_lockout_seconds: float | None = Field(None, description="Compatibility alias for remaining_seconds")
    user_failure_count: int | None = Field(None, description="Compatibility alias for failed_attempts")
    ip_failure_count: int | None = Field(None, description="Compatibility alias for failed_attempts")
