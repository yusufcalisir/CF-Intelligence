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
import os
import threading
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


from app.infrastructure.security.durable_account_store import (
    AuthenticationSecurityError,
    DurableAccountStore,
    UserRecord,
)


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
    Guarded by a reentrant lock to eliminate race conditions under concurrent load.
    """

    def __init__(
        self,
        max_failures: int = 5,
        lockout_duration_seconds: int = 900,
    ) -> None:
        self.max_failures = max_failures
        self.lockout_duration_seconds = lockout_duration_seconds
        self._lock = threading.RLock()
        # identifier -> list of failure timestamps
        self._failures: dict[str, list[float]] = {}
        # identifier -> lockout start timestamp
        self._locked_until: dict[str, float] = {}

    def record_failure(self, identifier: str) -> LockoutStatus:
        """Record a failed login attempt. Returns the updated LockoutStatus."""
        with self._lock:
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
        with self._lock:
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
        with self._lock:
            key = identifier.lower().strip()
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)


FORBIDDEN_JWT_SECRETS = {
    "",
    "cfi_oidc_jwt_secret_key_2026_enterprise_hs256",
    "cfi_oidc_jwt_secret_key_2026_super_secure_256bit_key_!",
    "secret",
    "changeme",
    "admin",
    "password",
    "default",
    "token",
}


class AuthenticationService:
    """Coordinates bcrypt credential validation, JWT token issuance, lockout policies, and durable identity verification."""

    _instance: AuthenticationService | None = None

    def __init__(
        self,
        signing_secret: str | None = None,
        access_token_expire_minutes: int = 15,
        refresh_token_expire_days: int = 7,
        max_auth_failures: int = 5,
        lockout_duration_seconds: int = 900,
        store_path: str | None = None,
        in_memory: bool = False,
        authority_secret: str | None = None,
        authority_public_key: str | None = None,
        app_env: str | None = None,
    ) -> None:
        settings = get_settings()
        active_env = (
            app_env
            or os.environ.get("APP_ENV")
            or os.environ.get("ENVIRONMENT")
            or getattr(settings, "app_env", "")
        ).lower()
        is_prod = active_env == "production"

        if signing_secret is not None:
            resolved_secret = signing_secret
        else:
            resolved_secret = (
                os.environ.get("CFI_JWT_SIGNING_SECRET")
                or os.environ.get("CFI_OIDC_JWT_SIGNING_SECRET")
                or getattr(settings, "oidc_jwt_signing_secret", None)
            )

        if is_prod:
            if (
                not resolved_secret
                or resolved_secret in FORBIDDEN_JWT_SECRETS
                or "change_me" in resolved_secret
                or len(resolved_secret.strip()) < 32
            ):
                raise AuthenticationSecurityError(
                    "Production JWT signing secret is unconfigured or uses a forbidden default/placeholder. "
                    "Production authentication fails closed."
                )
        elif not resolved_secret:
            resolved_secret = "cfi_oidc_jwt_secret_key_2026_super_secure_256bit_key_!"

        self.signing_secret = resolved_secret
        self.access_token_expire_minutes = access_token_expire_minutes
        self.refresh_token_expire_days = refresh_token_expire_days
        self.lockout_manager = BruteForceLockoutManager(
            max_failures=max_auth_failures,
            lockout_duration_seconds=lockout_duration_seconds,
        )

        resolved_auth_secret = authority_secret
        if not is_prod and not resolved_auth_secret:
            resolved_auth_secret = (
                os.environ.get("CFI_IDENTITY_STORE_SECRET")
                or getattr(settings, "identity_store_secret", None)
                or "cfi_dev_identity_store_authority_secret_2026_default"
            )

        # Durable multi-process identity and account state store
        self.identity_store = DurableAccountStore(
            store_path=store_path,
            in_memory=in_memory,
            authority_secret=resolved_auth_secret,
            authority_public_key=authority_public_key,
            app_env=active_env,
        )

        # Seed default administrative and analyst test accounts if store is uninitialized (non-production only)
        self._seed_default_users(is_prod=is_prod)

    @property
    def _users(self) -> dict[str, UserRecord]:
        """Backward-compatible mapping view of all users."""
        return {u.username: u for u in self.identity_store.list_users(include_inactive=True)}

    @property
    def _revoked_tokens(self) -> set[str]:
        """Backward-compatible set view of revoked JTIs."""
        return set(self.identity_store._revoked_tokens.keys())

    @property
    def _active_refresh_tokens(self) -> dict[str, str]:
        """Backward-compatible mapping of active refresh tokens."""
        return dict(self.identity_store._active_refresh_tokens)

    @classmethod
    def get_instance(cls) -> AuthenticationService:
        """Singleton accessor for the authentication service."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Testing utility to clear singleton instance."""
        cls._instance = None

    def _seed_default_users(self, is_prod: bool = False) -> None:
        """Provision default platform users if not already present in the durable identity store."""
        if is_prod:
            logger.info("Production environment: automatic demo/test user seeding is disabled.")
            return

        if self.identity_store.has_users():
            # Store already contains persisted accounts across worker processes / restarts
            return

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
            status="active",
        )
        self.identity_store.save_user(user)
        return user

    def get_user(self, username: str, include_inactive: bool = False) -> UserRecord | None:
        """Safely fetch a user by username from the authoritative durable store."""
        uname = username.lower().strip()
        return self.identity_store.get_user(uname, include_inactive=include_inactive)

    def deactivate_user(self, username: str) -> bool:
        """Deactivate account across all processes."""
        return self.identity_store.deactivate_user(username)

    def activate_user(self, username: str) -> bool:
        """Activate account across all processes."""
        return self.identity_store.activate_user(username)

    def suspend_user(self, username: str) -> bool:
        """Suspend account across all processes."""
        return self.identity_store.suspend_user(username)

    def delete_user(self, username: str) -> bool:
        """Permanently delete account across all processes."""
        return self.identity_store.delete_user(username)

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

        try:
            user = self.identity_store.get_user(uname, include_inactive=True)
        except AuthenticationSecurityError as sec_err:
            logger.error("Authoritative identity store security failure during authentication: %s", sec_err)
            return False, None, "Authentication authority unavailable.", user_lockout

        if not user or not user.is_active or user.status.lower() != "active":
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

        # Track active refresh token for rotation / revocation in durable store
        self.identity_store.track_refresh_token(refresh_jti, user.username)

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
            if not jti or not self.identity_store.is_refresh_token_active(jti):
                return False, None, "Refresh token is invalid, revoked, or has already been used."

            if self.identity_store.is_token_revoked(jti):
                return False, None, "Refresh token has been revoked."

            username = payload.get("username", "").lower()
            user = self.identity_store.get_user(username)
            if not user or not user.is_active or user.status.lower() != "active":
                return False, None, "Associated user account not found or disabled."

            # Invalidate the old refresh token (Token Rotation)
            self.identity_store.pop_refresh_token(jti)
            self.identity_store.revoke_token(jti, reason="rotated")

            # Issue new bundle
            new_bundle = self.create_token_bundle(user)
            return True, new_bundle, "Token refreshed successfully."

        except jwt.ExpiredSignatureError:
            return False, None, "Refresh token has expired. Please log in again."
        except jwt.InvalidTokenError as err:
            return False, None, f"Invalid refresh token: {err}"

    def verify_access_token(self, token_str: str) -> tuple[bool, dict[str, Any] | None, str]:
        """Verify and decode a short-lived access token with authoritative principal verification.

        Enforces:
        1. Cryptographic JWT signature and expiration verification.
        2. Access token type enforcement.
        3. Mandatory JTI identifier and durable revocation check across workers.
        4. Authoritative principal existence in durable store.
        5. Token claims-to-principal binding verification.
        6. Active account eligibility (rejects deactivated, suspended, or deleted accounts).
        7. Fail-closed rejection on store unavailability or corruption.
        """
        try:
            payload = jwt.decode(
                token_str,
                self.signing_secret,
                algorithms=["HS256"],
            )
        except jwt.ExpiredSignatureError:
            return False, None, "Access token has expired."
        except jwt.InvalidTokenError as err:
            return False, None, f"Invalid access token: {err}"

        if payload.get("token_type") != "access":
            return False, None, "Invalid token type. Expected access token."

        jti = payload.get("jti")
        if not jti:
            return False, None, "Access token is missing required 'jti' identifier."

        try:
            if self.identity_store.is_token_revoked(jti):
                return False, None, "Access token has been revoked."
        except AuthenticationSecurityError as sec_err:
            logger.error("Durable identity store corruption / security error: %s", sec_err)
            return False, None, f"Authoritative identity authority unavailable: {sec_err}"
        except Exception as exc:
            logger.error("Durable identity store query failed: %s", exc)
            return False, None, "Authoritative identity verification unavailable."

        token_username = (payload.get("username") or "").lower().strip()
        token_sub = (payload.get("sub") or "").strip()

        if not token_username and not token_sub:
            return False, None, "Access token lacks principal identity claims ('username' / 'sub')."

        try:
            user: UserRecord | None = None
            if token_username:
                user = self.identity_store.get_user(token_username, include_inactive=True)
            if not user and token_sub:
                user = self.identity_store.get_user_by_id(token_sub, include_inactive=True)
                if not user:
                    user = self.identity_store.get_user(token_sub, include_inactive=True)
        except AuthenticationSecurityError as sec_err:
            logger.error("Durable identity store corruption / security error: %s", sec_err)
            return False, None, f"Authoritative identity authority unavailable: {sec_err}"
        except Exception as exc:
            logger.error("Durable identity store query failed: %s", exc)
            return False, None, "Authoritative identity verification unavailable."

        if user is None:
            principal_name = token_username or token_sub
            return False, None, f"Authoritative principal '{principal_name}' does not exist or has been deleted."

        # Principal binding checks
        if token_username and user.username.lower() != token_username:
            return (
                False,
                None,
                f"Token username '{token_username}' does not match authoritative principal '{user.username}'.",
            )

        if token_sub and user.user_id != token_sub and user.username.lower() != token_sub.lower():
            return (
                False,
                None,
                f"Token subject '{token_sub}' is not bound to authoritative principal '{user.username}'.",
            )

        # Active account eligibility check
        if not user.is_active or user.status.lower() in ("deactivated", "suspended", "disabled", "inactive"):
            status_label = user.status if user.status.lower() in ("deactivated", "suspended", "disabled") else "deactivated"
            return False, None, f"Principal account '{user.username}' is {status_label}."

        return True, payload, "Token is valid."

    def revoke_token(self, token_str: str) -> bool:
        """Revoke a token across all workers by adding its JTI to the durable store."""
        try:
            # Decode without signature verification just to get JTI if expired
            unverified = jwt.decode(token_str, options={"verify_signature": False})
            jti = unverified.get("jti")
            if jti:
                self.identity_store.revoke_token(jti, reason="explicit_revocation")
                return True
        except Exception as exc:
            logger.warning("Failed to revoke token: %s", exc)
        return False

