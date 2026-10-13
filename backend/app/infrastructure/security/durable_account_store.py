"""Durable Account & Identity Store for Multi-Process Identity Verification.

Features:
- Durable, multi-process synchronized account and token revocation lifecycle.
- Zero lost updates: Protected by kernel/file-level file locks and atomic read-modify-write synchronization.
- Cross-process freshness: Microsecond mtime/size stat checking guarantees immediate visibility
  of deactivations, deletions, suspensions, and token revocations across independent worker processes.
- Fail-closed integrity: SHA-256 integrity digests detect corruption, truncation, or tampering.
  Empty or corrupt files fail closed in all production paths.
- Resilient recovery: Account state and token revocations survive full application restarts.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Generator

from filelock import FileLock
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.config import get_settings
from app.infrastructure.storage.storage_utils import get_storage_dir

logger = logging.getLogger(__name__)

FORBIDDEN_AUTHORITY_SECRETS = {
    "",
    "secret",
    "admin",
    "password",
    "changeme",
    "change_me",
    "change_me_in_production",
    "default",
    "token",
}


class AuthenticationSecurityError(Exception):
    """Raised when security-critical authentication, principal identity, or store invariants fail."""


@dataclass
class UserRecord:
    """Internal user model holding hashed credentials, status, and RBAC attributes."""

    user_id: str
    username: str
    password_hash: str
    bank_id: str
    roles: list[str]
    clearance_level: int = 1
    is_active: bool = True
    status: str = "active"  # "active", "deactivated", "suspended"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Serialize user record to a persistent JSON-compatible dictionary."""
        return {
            "user_id": self.user_id,
            "username": self.username,
            "password_hash": self.password_hash,
            "bank_id": self.bank_id,
            "roles": list(self.roles),
            "clearance_level": self.clearance_level,
            "is_active": self.is_active,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserRecord:
        """Hydrate user record from dictionary."""
        return cls(
            user_id=data["user_id"],
            username=data["username"],
            password_hash=data["password_hash"],
            bank_id=data.get("bank_id", "bank_a"),
            roles=list(data.get("roles", ["analyst"])),
            clearance_level=int(data.get("clearance_level", 1)),
            is_active=bool(data.get("is_active", True)),
            status=str(data.get("status", "active" if data.get("is_active", True) else "deactivated")),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
        )


_ACCOUNT_STORE_FILE_LOCKS: dict[str, FileLock] = {}
_ACCOUNT_STORE_LOCKS_GUARD = threading.Lock()


def _get_account_store_file_lock(file_path: str, timeout: float = 15.0) -> FileLock:
    """Return process-shared FileLock for durable account store."""
    lock_path = os.path.abspath(f"{file_path}.lock")
    with _ACCOUNT_STORE_LOCKS_GUARD:
        if lock_path not in _ACCOUNT_STORE_FILE_LOCKS:
            _ACCOUNT_STORE_FILE_LOCKS[lock_path] = FileLock(lock_path, timeout=timeout)
        return _ACCOUNT_STORE_FILE_LOCKS[lock_path]


class DurableAccountStore:
    """Durable, multi-process synchronized account and revocation store.

    Guarantees:
    1. Multi-Process Consistency: Workers synchronize mutations and invalidations
       via file locks and atomic writes.
    2. Zero Memory Divergence: Changes in one worker are observed by other workers
       on subsequent queries via stat freshness validation without restarts.
    3. Restart Durability: Account deletions, deactivations, and token revocations
       persist across full service and worker restarts.
    4. Fail-Closed Integrity: Malformed or corrupted persistence payloads strictly
       trigger fail-closed rejection.
    """

    def __init__(
        self,
        store_path: str | None = None,
        in_memory: bool = False,
        authority_secret: str | None = None,
        authority_public_key: str | None = None,
        app_env: str | None = None,
    ) -> None:
        settings = get_settings()
        self._app_env = (
            app_env
            or os.environ.get("APP_ENV")
            or os.environ.get("ENVIRONMENT")
            or getattr(settings, "app_env", "")
        ).lower()
        is_prod = self._is_prod

        if is_prod and in_memory:
            raise AuthenticationSecurityError(
                "In-memory identity store is strictly forbidden in production mode: durable persistence is required."
            )

        self._in_memory = in_memory
        if self._in_memory:
            self._store_path = ""
        else:
            configured_path = (
                store_path
                or os.environ.get("CFI_IDENTITY_STORE_PATH")
                or getattr(settings, "identity_store_path", None)
            )
            if not configured_path:
                configured_path = os.path.join(get_storage_dir(), "auth_identity_store.json")
            self._store_path = os.path.abspath(configured_path)

        # Independently configured authority credentials
        if authority_public_key and authority_secret is None:
            # Explicit public-key verifier: do not fall back to ambient symmetric authority secret
            self._authority_secret = None
            self._authority_public_key_hex = authority_public_key
        else:
            self._authority_secret = (
                authority_secret
                or os.environ.get("CFI_IDENTITY_STORE_SECRET")
                or os.environ.get("CFI_IDENTITY_STORE_HMAC_SECRET")
                or getattr(settings, "identity_store_secret", None)
                or (None if is_prod else "cfi_dev_identity_store_authority_secret_2026_default")
            )
            self._authority_public_key_hex = (
                authority_public_key
                or os.environ.get("CFI_IDENTITY_STORE_AUTHORITY_KEY")
                or getattr(settings, "identity_store_authority_key", None)
            )

        self._authority_priv: ed25519.Ed25519PrivateKey | None = None
        self._hmac_key: bytes | None = None

        if self._authority_secret:
            if is_prod and (self._authority_secret in FORBIDDEN_AUTHORITY_SECRETS or "change_me" in self._authority_secret):
                raise AuthenticationSecurityError(
                    "Production identity store authority secret is empty or uses a forbidden default/placeholder."
                )
            seed = hashlib.sha256(b"CFI-IDENTITY-STORE-AUTH-SEED-V1:" + self._authority_secret.encode("utf-8")).digest()
            self._authority_priv = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
            derived_pub = self._authority_priv.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            ).hex()
            if self._authority_public_key_hex and not hmac.compare_digest(
                self._authority_public_key_hex.lower(), derived_pub.lower()
            ):
                raise AuthenticationSecurityError(
                    "Authority key mismatch: configured authority_public_key does not match "
                    "the authority key derived from authority_secret."
                )
            self._authority_public_key_hex = derived_pub
            self._hmac_key = hashlib.sha256(b"CFI-IDENTITY-STORE-HMAC-KEY-V1:" + self._authority_secret.encode("utf-8")).digest()
        elif is_prod and not self._authority_public_key_hex:
            raise AuthenticationSecurityError(
                "Production identity store requires independently configured authority secret "
                "(CFI_IDENTITY_STORE_SECRET) or pinned authority public key (CFI_IDENTITY_STORE_AUTHORITY_KEY)."
            )

        self._lock = threading.RLock()
        self._users: dict[str, dict[str, Any]] = {}
        self._revoked_tokens: dict[str, dict[str, Any]] = {}
        self._active_refresh_tokens: dict[str, str] = {}

        self._last_mtime_ns: int = 0
        self._last_size: int = -1
        self._is_corrupted: bool = False

        if not self._in_memory:
            self.reload()

    @property
    def _is_prod(self) -> bool:
        """Whether this store operates in production mode."""
        active_env = (
            self._app_env
            or os.environ.get("APP_ENV")
            or os.environ.get("ENVIRONMENT")
            or getattr(get_settings(), "app_env", "")
        ).lower()
        return active_env == "production"

    @property
    def store_path(self) -> str:
        """Return absolute path to store file."""
        return self._store_path

    def get_authority_public_key(self) -> str | None:
        """Return the hex-encoded Ed25519 authority public key."""
        return self._authority_public_key_hex

    @contextlib.contextmanager
    def _file_lock_context(self) -> Generator[None, None, None]:
        """Acquire inter-process file lock if operating against durable filesystem backend."""
        if self._in_memory or not self._store_path:
            yield
            return
        lock = _get_account_store_file_lock(self._store_path)
        with lock:
            yield

    def _compute_digest(self) -> str:
        """Compute authoritative SHA-256 checksum over canonical identity payload."""
        canonical = json.dumps(
            {
                "users": self._users,
                "revoked_tokens": self._revoked_tokens,
                "active_refresh_tokens": self._active_refresh_tokens,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def _check_mutating_allowed(self) -> None:
        """Ensure current process possesses authority credentials to mutate store in production."""
        if self._is_prod and not self._authority_secret:
            raise AuthenticationSecurityError(
                "Read-only verifier process cannot perform mutating operations on DurableAccountStore: "
                "authority_secret is required."
            )

    def _reload_unlocked(self, force: bool = False) -> None:
        """Internal reload without acquiring locks (must be called with locks held)."""
        if self._in_memory or not self._store_path:
            return

        if not os.path.exists(self._store_path):
            self._last_mtime_ns = 0
            self._last_size = -1
            self._is_corrupted = False
            return

        try:
            stat = os.stat(self._store_path)
            if not force and stat.st_mtime_ns == self._last_mtime_ns and stat.st_size == self._last_size:
                return

            if stat.st_size == 0:
                self._is_corrupted = True
                raise AuthenticationSecurityError(
                    f"Durable identity store at '{self._store_path}' is corrupted: file is empty (0 bytes)."
                )

            with open(self._store_path, "rb") as f:
                raw_bytes = f.read()

            try:
                container = json.loads(raw_bytes.decode("utf-8"))
            except Exception as parse_err:
                self._is_corrupted = True
                raise AuthenticationSecurityError(
                    f"Durable identity store at '{self._store_path}' is corrupted: invalid JSON ({parse_err})."
                ) from parse_err

            if not isinstance(container, dict):
                self._is_corrupted = True
                raise AuthenticationSecurityError(
                    f"Durable identity store at '{self._store_path}' is malformed: root JSON structure must be an object."
                )

            data = container.get("data")
            if not isinstance(data, dict):
                data = {
                    "users": container.get("users", {}),
                    "revoked_tokens": container.get("revoked_tokens", {}),
                    "active_refresh_tokens": container.get("active_refresh_tokens", {}),
                }

            raw_users = data.get("users")
            raw_revoked = data.get("revoked_tokens")
            raw_refresh = data.get("active_refresh_tokens")

            if not isinstance(raw_users, dict) or not isinstance(raw_revoked, dict) or not isinstance(raw_refresh, dict):
                self._is_corrupted = True
                raise AuthenticationSecurityError(
                    f"Durable identity store at '{self._store_path}' is malformed: schema elements invalid."
                )

            canonical_bytes = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
            computed_sha = hashlib.sha256(canonical_bytes).hexdigest()

            # Layer 1: Integrity digest verification
            stored_digest = container.get("integrity_sha256")
            if self._is_prod and not stored_digest:
                self._is_corrupted = True
                raise AuthenticationSecurityError(
                    f"Durable identity store at '{self._store_path}' rejected: missing required 'integrity_sha256' in production."
                )
            if stored_digest and not hmac.compare_digest(stored_digest, computed_sha):
                self._is_corrupted = True
                raise AuthenticationSecurityError(
                    f"Durable identity store at '{self._store_path}' failed integrity verification: digest mismatch."
                )

            # Layer 2: Cryptographic Authenticity & Authority Verification
            has_auth = "integrity_hmac_sha256" in container or "authority_signature" in container
            if self._is_prod and not has_auth:
                self._is_corrupted = True
                raise AuthenticationSecurityError(
                    f"Durable identity store at '{self._store_path}' rejected: unauthenticated persistence format "
                    f"lacking cryptographic authentication signature or HMAC in production."
                )

            if self._hmac_key:
                expected_hmac = container.get("integrity_hmac_sha256")
                if not expected_hmac:
                    if self._is_prod:
                        self._is_corrupted = True
                        raise AuthenticationSecurityError(
                            f"Durable identity store at '{self._store_path}' failed cryptographic authentication: "
                            f"missing 'integrity_hmac_sha256' tag."
                        )
                else:
                    actual_hmac = hmac.new(self._hmac_key, canonical_bytes, hashlib.sha256).hexdigest()
                    if not hmac.compare_digest(expected_hmac, actual_hmac):
                        self._is_corrupted = True
                        raise AuthenticationSecurityError(
                            f"Durable identity store at '{self._store_path}' failed cryptographic HMAC authentication: "
                            f"authentication tag mismatch."
                        )

            if self._authority_public_key_hex:
                expected_sig = container.get("authority_signature")
                if not expected_sig:
                    if self._is_prod:
                        self._is_corrupted = True
                        raise AuthenticationSecurityError(
                            f"Durable identity store at '{self._store_path}' failed cryptographic authentication: "
                            f"missing 'authority_signature' signature."
                        )
                else:
                    try:
                        pub_bytes = bytes.fromhex(self._authority_public_key_hex)
                        verifier_pub = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
                        verifier_pub.verify(bytes.fromhex(expected_sig), canonical_bytes)
                    except Exception as sig_err:
                        self._is_corrupted = True
                        raise AuthenticationSecurityError(
                            f"Durable identity store at '{self._store_path}' failed authority signature verification "
                            f"against independently pinned authority public key ({sig_err})."
                        ) from sig_err

            if self._is_prod and not self._hmac_key and not self._authority_public_key_hex:
                self._is_corrupted = True
                raise AuthenticationSecurityError(
                    f"Production identity store at '{self._store_path}' requires independently configured authority secret "
                    f"or authority public key."
                )

            self._users = raw_users
            self._revoked_tokens = raw_revoked
            self._active_refresh_tokens = raw_refresh
            self._last_mtime_ns = stat.st_mtime_ns
            self._last_size = stat.st_size
            self._is_corrupted = False
        except AuthenticationSecurityError:
            raise
        except Exception as err:
            self._is_corrupted = True
            raise AuthenticationSecurityError(
                f"Failed to read durable identity store at '{self._store_path}': {err}"
            ) from err

    def _save_unlocked(self) -> None:
        """Internal atomic save (must be called with locks held)."""
        if self._in_memory or not self._store_path:
            return

        self._check_mutating_allowed()

        dir_name = os.path.dirname(self._store_path)
        os.makedirs(dir_name, exist_ok=True)

        data = {
            "users": self._users,
            "revoked_tokens": self._revoked_tokens,
            "active_refresh_tokens": self._active_refresh_tokens,
        }
        canonical_bytes = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(canonical_bytes).hexdigest()

        container: dict[str, Any] = {
            "schema_version": 2,
            "updated_at": time.time(),
            "data": data,
            "integrity_sha256": digest,
            "users": self._users,
            "revoked_tokens": self._revoked_tokens,
            "active_refresh_tokens": self._active_refresh_tokens,
        }

        if self._authority_secret and self._authority_priv and self._hmac_key:
            container["integrity_hmac_sha256"] = hmac.new(self._hmac_key, canonical_bytes, hashlib.sha256).hexdigest()
            container["authority_public_key_hex"] = self._authority_public_key_hex
            container["authority_signature"] = self._authority_priv.sign(canonical_bytes).hex()
        elif self._is_prod:
            raise AuthenticationSecurityError(
                "Cannot persist durable identity store in production without configured authority_secret."
            )

        raw_bytes = json.dumps(container, indent=2, sort_keys=True).encode("utf-8")
        tmp_path = f"{self._store_path}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"

        try:
            with open(tmp_path, "wb") as f:
                f.write(raw_bytes)
                f.flush()
                os.fsync(f.fileno())

            if os.name == "posix":
                with contextlib.suppress(Exception):
                    os.chmod(tmp_path, 0o600)

            os.replace(tmp_path, self._store_path)

            stat = os.stat(self._store_path)
            self._last_mtime_ns = stat.st_mtime_ns
            self._last_size = stat.st_size
            self._is_corrupted = False
        finally:
            if os.path.exists(tmp_path):
                with contextlib.suppress(OSError):
                    os.remove(tmp_path)

    def reload(self, force: bool = False) -> None:
        """Explicitly refresh in-memory state from the durable disk store."""
        with self._lock:
            with self._file_lock_context():
                self._reload_unlocked(force=force)

    def get_user(self, username: str, include_inactive: bool = False) -> UserRecord | None:
        """Fetch user record by username, checking active status and reloading on disk change."""
        with self._lock:
            with self._file_lock_context():
                self._reload_unlocked()
                if self._is_corrupted:
                    raise AuthenticationSecurityError(
                        f"Durable identity store at '{self._store_path}' is in corrupted state."
                    )
                u = self._users.get(username.lower().strip())
                if not u:
                    return None
                if not include_inactive and not u.get("is_active", True):
                    return None
                return UserRecord.from_dict(u)

    def get_user_by_id(self, user_id: str, include_inactive: bool = False) -> UserRecord | None:
        """Fetch user record by unique user_id."""
        with self._lock:
            with self._file_lock_context():
                self._reload_unlocked()
                if self._is_corrupted:
                    raise AuthenticationSecurityError(
                        f"Durable identity store at '{self._store_path}' is in corrupted state."
                    )
                for u in self._users.values():
                    if u.get("user_id") == user_id:
                        if not include_inactive and not u.get("is_active", True):
                            return None
                        return UserRecord.from_dict(u)
                return None

    def save_user(self, user: UserRecord) -> None:
        """Persist or update user record atomically across processes."""
        with self._lock:
            with self._file_lock_context():
                self._reload_unlocked()
                norm_uname = user.username.lower().strip()
                user.updated_at = time.time()
                self._users[norm_uname] = user.to_dict()
                self._save_unlocked()

    def deactivate_user(self, username: str) -> bool:
        """Deactivate account atomically across workers."""
        with self._lock:
            with self._file_lock_context():
                self._reload_unlocked()
                norm_uname = username.lower().strip()
                if norm_uname not in self._users:
                    return False
                self._users[norm_uname]["is_active"] = False
                self._users[norm_uname]["status"] = "deactivated"
                self._users[norm_uname]["updated_at"] = time.time()
                self._save_unlocked()
                logger.info("Authoritative identity deactivated for user '%s'", norm_uname)
                return True

    def activate_user(self, username: str) -> bool:
        """Activate account atomically across workers."""
        with self._lock:
            with self._file_lock_context():
                self._reload_unlocked()
                norm_uname = username.lower().strip()
                if norm_uname not in self._users:
                    return False
                self._users[norm_uname]["is_active"] = True
                self._users[norm_uname]["status"] = "active"
                self._users[norm_uname]["updated_at"] = time.time()
                self._save_unlocked()
                logger.info("Authoritative identity activated for user '%s'", norm_uname)
                return True

    def suspend_user(self, username: str) -> bool:
        """Suspend account atomically across workers."""
        with self._lock:
            with self._file_lock_context():
                self._reload_unlocked()
                norm_uname = username.lower().strip()
                if norm_uname not in self._users:
                    return False
                self._users[norm_uname]["is_active"] = False
                self._users[norm_uname]["status"] = "suspended"
                self._users[norm_uname]["updated_at"] = time.time()
                self._save_unlocked()
                logger.info("Authoritative identity suspended for user '%s'", norm_uname)
                return True

    def delete_user(self, username: str) -> bool:
        """Delete user account permanently across workers."""
        with self._lock:
            with self._file_lock_context():
                self._reload_unlocked()
                norm_uname = username.lower().strip()
                if norm_uname not in self._users:
                    return False
                del self._users[norm_uname]
                self._save_unlocked()
                logger.info("Authoritative identity deleted for user '%s'", norm_uname)
                return True

    def has_users(self) -> bool:
        """Check if any users are persisted in the store."""
        with self._lock:
            with self._file_lock_context():
                self._reload_unlocked()
                return len(self._users) > 0

    def has_user(self, username: str) -> bool:
        """Check if a specific username exists in the store (active or inactive)."""
        with self._lock:
            with self._file_lock_context():
                self._reload_unlocked()
                return username.lower().strip() in self._users

    def list_users(self, include_inactive: bool = True) -> list[UserRecord]:
        """List all users in store."""
        with self._lock:
            with self._file_lock_context():
                self._reload_unlocked()
                results: list[UserRecord] = []
                for u in self._users.values():
                    if include_inactive or u.get("is_active", True):
                        results.append(UserRecord.from_dict(u))
                return results

    def revoke_token(self, jti: str, reason: str = "revoked", expires_at: float | None = None) -> None:
        """Revoke a token JTI across all workers."""
        with self._lock:
            with self._file_lock_context():
                self._reload_unlocked()
                self._revoked_tokens[jti] = {
                    "jti": jti,
                    "revoked_at": time.time(),
                    "reason": reason,
                    "expires_at": expires_at,
                }
                self._active_refresh_tokens.pop(jti, None)
                self._save_unlocked()
                logger.info("Token JTI '%s' revoked across processes", jti)

    def is_token_revoked(self, jti: str) -> bool:
        """Check if a token JTI has been revoked across workers."""
        with self._lock:
            with self._file_lock_context():
                self._reload_unlocked()
                if self._is_corrupted:
                    raise AuthenticationSecurityError(
                        f"Durable identity store at '{self._store_path}' is in corrupted state."
                    )
                return jti in self._revoked_tokens

    def track_refresh_token(self, jti: str, username: str) -> None:
        """Track an active refresh token JTI."""
        with self._lock:
            with self._file_lock_context():
                self._reload_unlocked()
                self._active_refresh_tokens[jti] = username.lower().strip()
                self._save_unlocked()

    def pop_refresh_token(self, jti: str) -> str | None:
        """Consume and pop an active refresh token during rotation."""
        with self._lock:
            with self._file_lock_context():
                self._reload_unlocked()
                uname = self._active_refresh_tokens.pop(jti, None)
                if uname is not None:
                    self._save_unlocked()
                return uname

    def is_refresh_token_active(self, jti: str) -> bool:
        """Check if a refresh token JTI is active."""
        with self._lock:
            with self._file_lock_context():
                self._reload_unlocked()
                return jti in self._active_refresh_tokens

    def clear(self) -> None:
        """Clear all store contents (primarily for isolated test fixtures)."""
        with self._lock:
            with self._file_lock_context():
                self._users.clear()
                self._revoked_tokens.clear()
                self._active_refresh_tokens.clear()
                self._save_unlocked()
