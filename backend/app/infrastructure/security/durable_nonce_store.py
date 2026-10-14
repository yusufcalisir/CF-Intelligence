"""Durable Multi-Process Replay Prevention & Nonce Store.

Provides atomic, multi-worker synchronized nonce consumption with TTL-bounded
durability and fail-closed integrity verification. Survives worker restarts and
enforces strict cross-process replay protection for internal authority contexts.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Generator
from typing import Any

from filelock import FileLock

from app.config import get_settings
from app.infrastructure.storage.storage_utils import get_storage_dir

logger = logging.getLogger(__name__)


class NonceStoreIntegrityError(Exception):
    """Raised when the replay prevention store is corrupted, tampered, or fails integrity checks."""


class DurableNonceStore:
    """Synchronized, file-backed nonce store providing atomic replay protection across independent workers."""

    def __init__(
        self,
        store_path: str | None = None,
        in_memory: bool = False,
        app_env: str | None = None,
    ) -> None:
        settings = get_settings()
        raw_env = (
            app_env
            or os.environ.get("APP_ENV")
            or os.environ.get("ENVIRONMENT")
            or getattr(settings, "app_env", "")
            or ""
        )
        self._app_env = str(raw_env).lower()
        self._is_prod = self._app_env == "production"

        if self._is_prod and in_memory:
            raise NonceStoreIntegrityError(
                "In-memory nonce store is strictly forbidden in production mode: durable persistence is required."
            )

        self._in_memory = in_memory
        if self._in_memory:
            self._store_path = ""
            self._lock_path = ""
        else:
            configured_path = (
                store_path
                or os.environ.get("CFI_INTERNAL_NONCE_STORE_PATH")
                or getattr(settings, "internal_nonce_store_path", None)
            )
            if not configured_path:
                configured_path = os.path.join(get_storage_dir(), "security", "consumed_internal_nonces.json")
            self._store_path = os.path.abspath(configured_path)
            self._lock_path = f"{self._store_path}.lock"
            os.makedirs(os.path.dirname(self._store_path), exist_ok=True)

        self._lock = threading.RLock()
        self._consumed: dict[str, dict[str, Any]] = {}
        self._last_mtime: float = 0.0
        self._last_mtime_ns: int = 0
        self._last_size: int = -1
        self._is_corrupted: bool = False

        if not self._in_memory:
            self.reload()

    @property
    def is_prod(self) -> bool:
        return self._is_prod

    @contextlib.contextmanager
    def _file_lock_context(self) -> Generator[None, None, None]:
        if self._in_memory or not self._lock_path:
            yield
            return
        lock = FileLock(self._lock_path, timeout=15)
        with lock:
            yield

    def _compute_digest(self, consumed_data: dict[str, Any]) -> str:
        canonical_json = json.dumps(consumed_data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def reload(self) -> None:
        """Force complete reload of durable nonce store from disk."""
        if self._in_memory or not self._store_path:
            return
        with self._lock, self._file_lock_context():
            self._reload_unlocked(force=True)

    def reload_if_modified(self) -> None:
        """Check mtime, mtime_ns, and size, and reload if modified on disk."""
        if self._in_memory or not self._store_path:
            return
        with self._lock:
            if self._is_corrupted and self._is_prod:
                raise NonceStoreIntegrityError(
                    f"Durable nonce store at '{self._store_path}' is in corrupted state. Operations fail closed."
                )
            if os.path.exists(self._store_path):
                stat = os.stat(self._store_path)
                if (
                    stat.st_mtime_ns != self._last_mtime_ns
                    or stat.st_size != self._last_size
                    or stat.st_mtime != self._last_mtime
                ):
                    with self._file_lock_context():
                        self._reload_unlocked(force=True)

    def _reload_unlocked(self, force: bool = False) -> None:
        if not os.path.exists(self._store_path):
            self._consumed = {}
            self._last_mtime = 0.0
            self._last_mtime_ns = 0
            self._last_size = -1
            self._is_corrupted = False
            return

        stat = os.stat(self._store_path)
        if (
            not force
            and stat.st_mtime_ns == self._last_mtime_ns
            and stat.st_size == self._last_size
            and stat.st_mtime == self._last_mtime
        ):
            return

        try:
            with open(self._store_path, encoding="utf-8") as f:
                content = f.read()

            if not content.strip():
                if self._is_prod:
                    self._is_corrupted = True
                    raise NonceStoreIntegrityError(
                        f"Durable nonce store at '{self._store_path}' is unexpectedly empty in production."
                    )
                self._consumed = {}
                return

            data = json.loads(content)
            if not isinstance(data, dict):
                raise NonceStoreIntegrityError("Invalid root container: expected JSON object.")

            consumed = data.get("consumed_nonces", {})
            if not isinstance(consumed, dict):
                raise NonceStoreIntegrityError("Invalid 'consumed_nonces' attribute: expected JSON object.")

            recorded_digest = data.get("integrity_digest")
            computed_digest = self._compute_digest(consumed)
            if recorded_digest and recorded_digest != computed_digest:
                self._is_corrupted = True
                raise NonceStoreIntegrityError(
                    f"Durable nonce store failed integrity check: digest mismatch (expected {recorded_digest}, got {computed_digest})."
                )

            self._consumed = consumed
            self._last_mtime = stat.st_mtime
            self._last_mtime_ns = stat.st_mtime_ns
            self._last_size = stat.st_size
            self._is_corrupted = False
        except NonceStoreIntegrityError:
            self._is_corrupted = True
            raise
        except Exception as exc:
            self._is_corrupted = True
            raise NonceStoreIntegrityError(
                f"Failed to load or parse durable nonce store from '{self._store_path}': {exc}"
            ) from exc

    def _persist_unlocked(self) -> None:
        if self._in_memory or not self._store_path:
            return

        digest = self._compute_digest(self._consumed)
        container = {
            "version": 1,
            "integrity_digest": digest,
            "updated_at": time.time(),
            "consumed_nonces": self._consumed,
        }
        raw_json = json.dumps(container, indent=2, sort_keys=True)
        tmp_path = f"{self._store_path}.{os.getpid()}_{threading.get_ident()}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(raw_json)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self._store_path)
            stat = os.stat(self._store_path)
            self._last_mtime = stat.st_mtime
            self._last_mtime_ns = stat.st_mtime_ns
            self._last_size = stat.st_size
            self._is_corrupted = False
        finally:
            if os.path.exists(tmp_path):
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)

    def is_nonce_consumed(self, nonce: str) -> bool:
        """Check whether a nonce has already been consumed (non-mutating inspection)."""
        clean_nonce = nonce.strip()
        with self._lock:
            self.reload_if_modified()
            if self._is_corrupted and self._is_prod:
                return True  # Fail closed: treat as already consumed/unusable
            entry = self._consumed.get(clean_nonce)
            if entry is None:
                return False
            expires_at = entry.get("expires_at", 0.0)
            return time.time() <= expires_at

    def consume_nonce(
        self,
        nonce: str,
        expires_at: float,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Atomically consume a nonce.

        Returns:
            (True, "Consumed") if the nonce was successfully recorded and never consumed before.
            (False, reason) if the nonce was already consumed, expired, or if the store failed integrity checks.
        """
        clean_nonce = (nonce or "").strip()
        if not clean_nonce:
            return False, "Replay prevention rejected: nonce cannot be empty."

        now = time.time()
        with self._lock, self._file_lock_context():
            try:
                self._reload_unlocked(force=True)
            except NonceStoreIntegrityError as exc:
                if self._is_prod:
                    logger.error("Production nonce store failed closed: %s", exc)
                    return False, f"Replay authority unavailable or corrupted: {exc}"
                logger.warning("Nonce store reload warning: %s; re-initializing", exc)
                self._consumed = {}

            # Check existing entry
            existing = self._consumed.get(clean_nonce)
            if existing is not None:
                consumed_at = existing.get("consumed_at", now)
                return (
                    False,
                    f"Replay detected: nonce '{clean_nonce}' has already been consumed at {consumed_at:.4f}.",
                )

            # Purge expired nonces older than current time to prevent unbounded file growth
            pruned: dict[str, dict[str, Any]] = {}
            for k, v in self._consumed.items():
                exp = v.get("expires_at", 0.0)
                if exp > now:
                    pruned[k] = v
            self._consumed = pruned

            # Record new consumption
            record: dict[str, Any] = {
                "consumed_at": now,
                "expires_at": expires_at,
            }
            if metadata:
                record.update({k: v for k, v in metadata.items() if k not in record})
            self._consumed[clean_nonce] = record

            try:
                self._persist_unlocked()
            except Exception as exc:
                if self._is_prod:
                    return False, f"Failed to persist consumed nonce authority: {exc}"
                logger.error("Failed to persist nonce store: %s", exc)

            return True, "Consumed"


# Global singleton instance
_GLOBAL_NONCE_STORE: DurableNonceStore | None = None
_NONCE_STORE_GUARD = threading.Lock()


def get_default_nonce_store() -> DurableNonceStore:
    """Return the global DurableNonceStore singleton."""
    global _GLOBAL_NONCE_STORE
    with _NONCE_STORE_GUARD:
        if _GLOBAL_NONCE_STORE is None:
            _GLOBAL_NONCE_STORE = DurableNonceStore()
        return _GLOBAL_NONCE_STORE
