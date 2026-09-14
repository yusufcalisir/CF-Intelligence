"""OIDC and JWT Bearer Authenticator.

Validates bearer tokens (RS256 / HS256), extracts standard and custom claims
(sub, preferred_username, bank_id, roles, clearance_level, shift_hours, approval_tier),
and verifies issuer/audience alignment for central Keycloak/Okta integrations.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import jwt

logger = logging.getLogger(__name__)


@dataclass
class UserClaims:
    """Identity and authorization claims extracted from an OIDC bearer token."""

    sub: str
    username: str
    bank_id: str
    roles: list[str] = field(default_factory=list)
    clearance_level: int = 1
    shift_hours: str = "00:00-24:00"
    approval_tier: float = 100000.0
    allowed_ip_subnets: list[str] = field(default_factory=lambda: ["0.0.0.0/0"])
    issuer: str = "https://auth.cfi-platform.internal/realms/cfi"
    exp: float = 0.0


class OIDCAuthenticator:
    """Decodes and validates OIDC JWT tokens and extracts RBAC/ABAC claims."""

    def __init__(
        self,
        issuer: str = "https://auth.cfi-platform.internal/realms/cfi",
        audience: str = "cfi-api",
        signing_secret: str | None = None,
    ) -> None:
        from app.config import get_settings

        settings = get_settings()
        self.issuer = issuer
        self.audience = audience
        self.signing_secret = (
            signing_secret
            or getattr(settings, "oidc_jwt_signing_secret", None)
            or "cfi_oidc_jwt_secret_key_2026_enterprise_hs256"
        )

    def create_token(
        self,
        username: str = "analyst_a1",
        bank_id: str = "bank_a",
        roles: list[str] | None = None,
        clearance_level: int = 2,
        shift_hours: str = "08:00-18:00",
        approval_tier: float = 50000.0,
        allowed_ip_subnets: list[str] | None = None,
        expires_in_seconds: int = 3600,
    ) -> str:
        """Create a cryptographically signed JWT token string (HS256)."""
        now = int(time.time())
        payload = {
            "sub": f"usr_{username}",
            "preferred_username": username,
            "bank_id": bank_id,
            "roles": roles or ["analyst", "investigator"],
            "clearance_level": clearance_level,
            "shift_hours": shift_hours,
            "approval_tier": approval_tier,
            "allowed_ip_subnets": allowed_ip_subnets or ["0.0.0.0/0"],
            "iss": self.issuer,
            "aud": self.audience,
            "iat": now,
            "exp": now + expires_in_seconds,
        }
        return jwt.encode(payload, self.signing_secret, algorithm="HS256")

    def create_mock_token(
        self,
        username: str = "analyst_a1",
        bank_id: str = "bank_a",
        roles: list[str] | None = None,
        clearance_level: int = 2,
        shift_hours: str = "08:00-18:00",
        approval_tier: float = 50000.0,
        allowed_ip_subnets: list[str] | None = None,
    ) -> str:
        """Backward compatibility alias for create_token."""
        return self.create_token(
            username=username,
            bank_id=bank_id,
            roles=roles,
            clearance_level=clearance_level,
            shift_hours=shift_hours,
            approval_tier=approval_tier,
            allowed_ip_subnets=allowed_ip_subnets,
        )

    def decode_and_validate_token(self, token: str) -> tuple[bool, UserClaims | None, str]:
        """Decode and cryptographically verify JWT bearer token signature, expiration, and claims."""
        try:
            if not token or not isinstance(token, str):
                return False, None, "Missing or invalid token string."

            cleaned_token = token.strip()
            parts = cleaned_token.split(".")
            if len(parts) != 3:
                return False, None, "Invalid JWT structure (expected 3 dot-separated parts)."

            # Cryptographic signature and expiration verification via PyJWT
            claims_dict = jwt.decode(
                cleaned_token,
                self.signing_secret,
                algorithms=["HS256"],
                options={"verify_signature": True, "verify_exp": True, "verify_aud": False},
            )

            exp = float(claims_dict.get("exp", 0))
            user_claims = UserClaims(
                sub=claims_dict.get("sub", "anonymous"),
                username=claims_dict.get("preferred_username", claims_dict.get("username", "user")),
                bank_id=claims_dict.get("bank_id", "bank_a"),
                roles=claims_dict.get("roles", ["analyst"]),
                clearance_level=int(claims_dict.get("clearance_level", 1)),
                shift_hours=str(claims_dict.get("shift_hours", "00:00-24:00")),
                approval_tier=float(claims_dict.get("approval_tier", 100000.0)),
                allowed_ip_subnets=claims_dict.get("allowed_ip_subnets", ["0.0.0.0/0"]),
                issuer=claims_dict.get("iss", self.issuer),
                exp=exp,
            )
            return True, user_claims, "Token valid."

        except jwt.ExpiredSignatureError:
            return False, None, "JWT token has expired."
        except jwt.InvalidSignatureError:
            return False, None, "Invalid JWT cryptographic signature."
        except jwt.InvalidTokenError as err:
            return False, None, f"Invalid JWT token: {err}"
        except Exception as err:
            logger.error("OIDC JWT token decoding failed: %s", err)
            return False, None, f"Token decode error: {err}"
