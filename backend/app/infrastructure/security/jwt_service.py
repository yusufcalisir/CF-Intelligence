"""Enterprise JWT Service Facade & Token Manager.

Provides centralized helpers for signing, verifying, and decoding JWT access
and refresh tokens across services with HMAC-SHA256 and RFC 7518 compliance.
"""

from __future__ import annotations

import logging
from typing import Any

from app.infrastructure.security.auth_service import (
    AuthenticationService,
    TokenBundle,
    UserRecord,
)

logger = logging.getLogger(__name__)


class JWTService:
    """Enterprise JWT Service providing token lifecycle management."""

    def __init__(self, auth_service: AuthenticationService | None = None) -> None:
        self._auth_service = auth_service or AuthenticationService.get_instance()

    def create_token_bundle(self, user: UserRecord) -> TokenBundle:
        """Issue access token and refresh token bundle for a user."""
        return self._auth_service.create_token_bundle(user)

    def verify_access_token(self, token: str) -> tuple[bool, dict[str, Any] | None, str]:
        """Verify an HMAC-SHA256 signed access token and return claims."""
        return self._auth_service.verify_access_token(token)

    def refresh_access_token(self, refresh_token: str) -> tuple[bool, TokenBundle | None, str]:
        """Validate a refresh token and issue a newly rotated token bundle."""
        return self._auth_service.refresh_access_token(refresh_token)

    def revoke_token(self, token: str) -> bool:
        """Revoke a token by adding its JTI to the revocation blacklist."""
        return self._auth_service.revoke_token(token)


# Module-level convenience functions
_default_jwt_service = JWTService()


def get_jwt_service() -> JWTService:
    """Obtain default singleton instance of JWTService."""
    return _default_jwt_service
