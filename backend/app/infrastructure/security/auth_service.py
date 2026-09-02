"""Enterprise Authentication, JWT Token Manager & Brute-Force Lockout Service.

Features:
- Bcrypt password hashing (cost=12) with zero plaintext storage.
- Short-lived JWT access tokens (15–30 minutes) with HMAC-SHA256 signature.
- Cryptographically signed refresh tokens (7 days) with rotation on usage.
- Multi-identifier brute-force protection: 5 consecutive failed attempts trigger
  a 15-minute temporary lockout per account and IP address.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import jwt

from app.config import get_settings
from app.infrastructure.security.password_hasher import (
    hash_password,
    verify_password,
)

logger = logging.getLogger(__name__)


@dataclass
class UserRecord:
    """Internal user model holding hashed credentials and RBAC attributes."""

    user_id: str
    username: str
    password_hash: str
    bank_id: str
    roles: list[str]
    clearance_level: int = 1
    is_active: bool = True
    created_at: float = field(default_factory=time.time)


@dataclass
class TokenBundle:
    """Pair of access token and refresh token."""

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int = 900  # 15 minutes (900 seconds)
    refresh_expires_in: int = 604800  # 7 days (604800 seconds)


@dataclass
class LockoutStatus:
    """Lockout state for a given client identifier."""

    is_locked: bool
    failed_attempts: int
    max_attempts: int
    remaining_seconds: float = 0.0
    lockout_duration_seconds: int = 900


class BruteForceLockoutManager:
    """Tracks failed authentication attempts per identifier (username / IP).

    Enforces temporary lockout (default: 15 minutes / 900 seconds) after
    a threshold of consecutive failed login attempts (default: 5).
    """

    def __init__(
        self,
        max_failures: int = 5,
        lockout_duration_seconds: int = 900,
    ) -> None:
        self.max_failures = max_failures
        self.lockout_duration_seconds = lockout_duration_seconds
        # identifier -> list of failure timestamps
        self._failures: dict[str, list[float]] = {}
        # identifier -> lockout start timestamp
        self._locked_until: dict[str, float] = {}

    def record_failure(self, identifier: str) -> LockoutStatus:
        """Record a failed login attempt. Returns the updated LockoutStatus."""
        now = time.time()
        key = identifier.lower().strip()

        # Clean up failures older than lockout window
        recent_failures = [
            t for t in self._failures.get(key, [])
            if now - t <= self.lockout_duration_seconds
        ]
        recent_failures.append(now)
        self._failures[key] = recent_failures

        # Check if threshold reached
        if len(recent_failures) >= self.max_failures:
            self._locked_until[key] = now + self.lockout_duration_seconds
            logger.warning(
                "BRUTE-FORCE LOCKOUT TRIGGERED for identifier '%s'. Locked for %d seconds.",
                key,
                self.lockout_duration_seconds,
            )
            return LockoutStatus(
                is_locked=True,
                failed_attempts=len(recent_failures),
                max_attempts=self.max_failures,
                remaining_seconds=float(self.lockout_duration_seconds),
                lockout_duration_seconds=self.lockout_duration_seconds,
            )

        return LockoutStatus(
            is_locked=False,
            failed_attempts=len(recent_failures),
            max_attempts=self.max_failures,
            remaining_seconds=0.0,
            lockout_duration_seconds=self.lockout_duration_seconds,
        )

    def check_lockout(self, identifier: str) -> LockoutStatus:
        """Check if an identifier is currently locked out and return remaining duration."""
        now = time.time()
        key = identifier.lower().strip()

        locked_until = self._locked_until.get(key, 0.0)
        if locked_until > now:
            remaining = locked_until - now
            return LockoutStatus(
                is_locked=True,
                failed_attempts=len(self._failures.get(key, [])),
                max_attempts=self.max_failures,
                remaining_seconds=round(remaining, 1),
                lockout_duration_seconds=self.lockout_duration_seconds,
            )

        # Lockout expired, clean up
        if key in self._locked_until:
            del self._locked_until[key]
            self._failures.pop(key, None)

        recent = [
            t for t in self._failures.get(key, [])
            if now - t <= self.lockout_duration_seconds
        ]
        self._failures[key] = recent

        return LockoutStatus(
            is_locked=False,
            failed_attempts=len(recent),
            max_attempts=self.max_failures,
            remaining_seconds=0.0,
            lockout_duration_seconds=self.lockout_duration_seconds,
        )

    def reset(self, identifier: str) -> None:
        """Reset failed attempt counters upon successful authentication."""
        key = identifier.lower().strip()
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)


class AuthenticationService:
    """Coordinates bcrypt credential validation, JWT token issuance, and lockout policies."""

    _instance: AuthenticationService | None = None

    def __init__(
        self,
        signing_secret: str | None = None,
        access_token_expire_minutes: int = 15,
        refresh_token_expire_days: int = 7,
        max_auth_failures: int = 5,
        lockout_duration_seconds: int = 900,
    ) -> None:
        settings = get_settings()
        self.signing_secret = (
            signing_secret
            or getattr(settings, "oidc_jwt_signing_secret", None)
            or "cfi_oidc_jwt_secret_key_2026_super_secure_256bit_key_!"
        )
        self.access_token_expire_minutes = access_token_expire_minutes
        self.refresh_token_expire_days = refresh_token_expire_days
        self.lockout_manager = BruteForceLockoutManager(
            max_failures=max_auth_failures,
            lockout_duration_seconds=lockout_duration_seconds,
        )

        # In-memory user store (username -> UserRecord)
        self._users: dict[str, UserRecord] = {}

        # Active / Revoked refresh token tracking (jti -> user_id)
        self._active_refresh_tokens: dict[str, str] = {}
        self._revoked_tokens: set[str] = set()

        # Seed default administrative and analyst test accounts with bcrypt hashes
        self._seed_default_users()

    @classmethod
    def get_instance(cls) -> AuthenticationService:
        """Singleton accessor for the authentication service."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _seed_default_users(self) -> None:
        """Provision default platform users with cryptographically secure bcrypt hashes."""
        default_accounts = [
            ("admin", "AdminSecure2026!", "bank_a", ["admin", "compliance_officer"], 3),
            ("analyst_a1", "AnalystSecure2026!", "bank_a", ["analyst", "investigator"], 2),
            ("analyst_b1", "AnalystSecure2026!", "bank_b", ["analyst", "investigator"], 2),
            ("analyst_c1", "AnalystSecure2026!", "bank_c", ["analyst", "investigator"], 2),
            ("auditor", "AuditorSecure2026!", "bank_a", ["auditor"], 1),
        ]
        for uname, raw_pwd, bank, roles, clearance in default_accounts:
            self.register_user(
                username=uname,
                plain_password=raw_pwd,
                bank_id=bank,
                roles=roles,
                clearance_level=clearance,
            )

    def register_user(
        self,
        username: str,
        plain_password: str,
        bank_id: str = "bank_a",
        roles: list[str] | None = None,
        clearance_level: int = 1,
    ) -> UserRecord:
        """Register or update a user with a bcrypt-hashed password (never plaintext)."""
        uname = username.lower().strip()
        pwd_hash = hash_password(plain_password, rounds=12)
        user = UserRecord(
            user_id=f"usr_{uuid.uuid4().hex[:12]}",
            username=uname,
            password_hash=pwd_hash,
            bank_id=bank_id,
            roles=roles or ["analyst"],
            clearance_level=clearance_level,
            is_active=True,
        )
        self._users[uname] = user
        return user

    def authenticate(
        self,
        username: str,
        plain_password: str,
        client_ip: str = "127.0.0.1",
    ) -> tuple[bool, UserRecord | None, str, LockoutStatus]:
        """Authenticate user credentials with brute-force lockout and bcrypt verification."""
        uname = username.lower().strip()

        # 1. Check account lockout
        user_lockout = self.lockout_manager.check_lockout(uname)
        if user_lockout.is_locked:
            return (
                False,
                None,
                f"Account is temporarily locked out due to multiple failed login attempts. "
                f"Please retry in {int(user_lockout.remaining_seconds)} seconds.",
                user_lockout,
            )

        # 2. Check IP lockout
        ip_lockout = self.lockout_manager.check_lockout(client_ip)
        if ip_lockout.is_locked:
            return (
                False,
                None,
                f"Client IP is temporarily locked out. "
                f"Please retry in {int(ip_lockout.remaining_seconds)} seconds.",
                ip_lockout,
            )

        user = self._users.get(uname)
        if not user or not user.is_active:
            # Record failure against both user and IP
            u_stat = self.lockout_manager.record_failure(uname)
            ip_stat = self.lockout_manager.record_failure(client_ip)
            worst = u_stat if u_stat.is_locked else ip_stat
            if worst.is_locked:
                msg = (
                    f"Account or IP is temporarily locked out due to multiple failed login attempts. "
                    f"Please retry in {int(worst.remaining_seconds)} seconds."
                )
            else:
                msg = "Invalid username or password."
            return False, None, msg, worst

        # 3. Verify bcrypt password hash
        if not verify_password(plain_password, user.password_hash):
            u_stat = self.lockout_manager.record_failure(uname)
            ip_stat = self.lockout_manager.record_failure(client_ip)
            worst = u_stat if u_stat.is_locked else ip_stat
            if worst.is_locked:
                msg = (
                    f"Account or IP is temporarily locked out due to multiple failed login attempts. "
                    f"Please retry in {int(worst.remaining_seconds)} seconds."
                )
            else:
                msg = "Invalid username or password."
            return False, None, msg, worst

        # 4. Success: Reset lockout counters
        self.lockout_manager.reset(uname)
        self.lockout_manager.reset(client_ip)
        status = LockoutStatus(
            is_locked=False,
            failed_attempts=0,
            max_attempts=self.lockout_manager.max_failures,
            remaining_seconds=0.0,
        )
        return True, user, "Authentication successful.", status

    def create_token_bundle(self, user: UserRecord) -> TokenBundle:
        """Issue a short-lived access token (15m) and long-lived refresh token (7d)."""
        now = int(time.time())
        access_exp = now + (self.access_token_expire_minutes * 60)
        refresh_exp = now + (self.refresh_token_expire_days * 86400)

        access_jti = str(uuid.uuid4())
        refresh_jti = str(uuid.uuid4())

        # Access token payload (short-lived)
        access_payload: dict[str, Any] = {
            "sub": user.user_id,
            "username": user.username,
            "bank_id": user.bank_id,
            "roles": user.roles,
            "clearance_level": user.clearance_level,
            "token_type": "access",
            "jti": access_jti,
            "iat": now,
            "exp": access_exp,
        }

        # Refresh token payload (long-lived)
        refresh_payload: dict[str, Any] = {
            "sub": user.user_id,
            "username": user.username,
            "token_type": "refresh",
            "jti": refresh_jti,
            "iat": now,
            "exp": refresh_exp,
        }

        access_token = jwt.encode(
            access_payload,
            self.signing_secret,
            algorithm="HS256",
        )
        refresh_token = jwt.encode(
            refresh_payload,
            self.signing_secret,
            algorithm="HS256",
        )

        # Track active refresh token for rotation / revocation
        self._active_refresh_tokens[refresh_jti] = user.username

        return TokenBundle(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="Bearer",
            expires_in=self.access_token_expire_minutes * 60,
            refresh_expires_in=self.refresh_token_expire_days * 86400,
        )

    def refresh_access_token(self, refresh_token_str: str) -> tuple[bool, TokenBundle | None, str]:
        """Validate refresh token and issue a newly rotated access/refresh token bundle."""
        try:
            payload = jwt.decode(
                refresh_token_str,
                self.signing_secret,
                algorithms=["HS256"],
            )

            if payload.get("token_type") != "refresh":
                return False, None, "Invalid token type. Expected refresh token."

            jti = payload.get("jti")
            if not jti or jti not in self._active_refresh_tokens:
                return False, None, "Refresh token is invalid, revoked, or has already been used."

            if jti in self._revoked_tokens:
                return False, None, "Refresh token has been revoked."

            username = payload.get("username", "").lower()
            user = self._users.get(username)
            if not user or not user.is_active:
                return False, None, "Associated user account not found or disabled."

            # Invalidate the old refresh token (Token Rotation)
            self._active_refresh_tokens.pop(jti, None)
            self._revoked_tokens.add(jti)

            # Issue new bundle
            new_bundle = self.create_token_bundle(user)
            return True, new_bundle, "Token refreshed successfully."

        except jwt.ExpiredSignatureError:
            return False, None, "Refresh token has expired. Please log in again."
        except jwt.InvalidTokenError as err:
            return False, None, f"Invalid refresh token: {err}"

    def verify_access_token(self, token_str: str) -> tuple[bool, dict[str, Any] | None, str]:
        """Verify and decode a short-lived access token."""
        try:
            payload = jwt.decode(
                token_str,
                self.signing_secret,
                algorithms=["HS256"],
            )

            if payload.get("token_type") != "access":
                return False, None, "Invalid token type. Expected access token."

            jti = payload.get("jti")
            if jti and jti in self._revoked_tokens:
                return False, None, "Access token has been revoked."

            return True, payload, "Token is valid."

        except jwt.ExpiredSignatureError:
            return False, None, "Access token has expired."
        except jwt.InvalidTokenError as err:
            return False, None, f"Invalid access token: {err}"

    def revoke_token(self, token_str: str) -> bool:
        """Revoke a token by adding its JTI to the blacklist."""
        try:
            # Decode without signature verification just to get JTI if expired
            unverified = jwt.decode(token_str, options={"verify_signature": False})
            jti = unverified.get("jti")
            if jti:
                self._revoked_tokens.add(jti)
                self._active_refresh_tokens.pop(jti, None)
                return True
        except Exception as exc:
            logger.warning("Failed to revoke token: %s", exc)
        return False
