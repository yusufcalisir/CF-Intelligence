"""Idempotency Key Service.

Provides Redis-backed (with in-memory fallback) idempotency key deduplication
for POST endpoints that create resources.

Usage in a FastAPI handler::

    from app.application.services.idempotency import IdempotencyService

    @router.post("/resource", response_model=ResourceResponse)
    async def create_resource(
        req: ResourceCreateRequest,
        idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    ) -> ResourceResponse | JSONResponse:
        cached = IdempotencyService.get().get_cached(idempotency_key)
        if cached is not None:
            return JSONResponse(content=cached, status_code=200,
                                headers={"Idempotency-Replayed": "true"})
        result = ...  # actual creation logic
        IdempotencyService.get().store(idempotency_key, result)
        return result
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

# TTL for idempotency keys: 24 hours (matches Stripe/Adyen standard)
_IDEMPOTENCY_TTL_SECONDS = 86_400
_REDIS_KEY_PREFIX = "idem:"
_IN_PROGRESS_SENTINEL = "__IDEMPOTENCY_IN_PROGRESS__"


class IdempotencyService:
    """Redis-backed idempotency key store with automatic in-memory fallback.

    - When Redis is available:  uses SET NX EX / GET with ``_IDEMPOTENCY_TTL_SECONDS`` TTL.
    - When Redis is unavailable: falls back to an in-memory dict with timestamp-based
      expiry and in-flight mutex tracking.
    """

    _instance: IdempotencyService | None = None
    _lock: Lock = Lock()

    def __init__(self) -> None:
        self._redis_client: Any | None = None
        self._fallback: dict[str, tuple[Any, float, bool]] = {}  # key -> (payload, expires_at, is_in_progress)
        self._fallback_lock = Lock()
        self._init_redis()

    def _init_redis(self) -> None:
        try:
            import redis

            from app.config import get_settings

            settings = get_settings()
            url = getattr(settings, "redis_url", None) or "redis://localhost:6379/0"
            client = redis.Redis.from_url(url, socket_connect_timeout=1)
            client.ping()
            self._redis_client = client
            logger.info("IdempotencyService: connected to Redis at %s", url)
        except Exception as exc:
            logger.warning(
                "IdempotencyService: Redis unavailable (%s) -- using in-memory fallback", exc
            )
            self._redis_client = None

    @classmethod
    def get(cls) -> IdempotencyService:
        """Return the singleton instance (thread-safe lazy init)."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def acquire(
        self, idempotency_key: str | None, in_progress_timeout: float = 30.0
    ) -> tuple[str, Any | None]:
        """Atomically tests whether an idempotency key is already cached, in-progress, or acquires it.

        Returns:
            ("HIT", cached_response): already completed; safe to replay.
            ("IN_PROGRESS", None): another concurrent request is currently processing this key.
            ("ACQUIRED", None): lock acquired; proceed to create resource, then call complete().
        """
        if not idempotency_key:
            return ("ACQUIRED", None)

        redis_key = _REDIS_KEY_PREFIX + self._hash_key(idempotency_key)

        if self._redis_client is not None:
            try:
                acquired = self._redis_client.set(
                    redis_key, _IN_PROGRESS_SENTINEL, nx=True, ex=int(in_progress_timeout)
                )
                if acquired:
                    return ("ACQUIRED", None)

                raw = self._redis_client.get(redis_key)
                if raw:
                    val_str = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                    if val_str == _IN_PROGRESS_SENTINEL:
                        return ("IN_PROGRESS", None)
                    try:
                        return ("HIT", json.loads(val_str))
                    except Exception:
                        return ("HIT", val_str)
                return ("ACQUIRED", None)
            except Exception as exc:
                logger.warning("IdempotencyService Redis acquire failed: %s -- falling back", exc)
                self._redis_client = None

        with self._fallback_lock:
            now = time.monotonic()
            entry = self._fallback.get(redis_key)
            if entry:
                payload, expires_at, in_prog = entry
                if expires_at > now:
                    if in_prog:
                        return ("IN_PROGRESS", None)
                    return ("HIT", payload)
                else:
                    del self._fallback[redis_key]

            # Acquire reservation
            self._fallback[redis_key] = (None, now + in_progress_timeout, True)
            self._evict_expired()
            return ("ACQUIRED", None)

    def complete(self, idempotency_key: str | None, response_body: Any) -> None:
        """Persist final response_body under idempotency_key, clearing in-progress state."""
        if not idempotency_key:
            return
        redis_key = _REDIS_KEY_PREFIX + self._hash_key(idempotency_key)
        serialized = json.dumps(response_body, default=str)

        if self._redis_client is not None:
            try:
                self._redis_client.setex(redis_key, _IDEMPOTENCY_TTL_SECONDS, serialized)
                return
            except Exception as exc:
                logger.warning("IdempotencyService Redis SET failed: %s -- falling back", exc)
                self._redis_client = None

        with self._fallback_lock:
            expires_at = time.monotonic() + _IDEMPOTENCY_TTL_SECONDS
            self._fallback[redis_key] = (json.loads(serialized), expires_at, False)
            self._evict_expired()

    def release(self, idempotency_key: str | None) -> None:
        """Release in-progress reservation on error or cancellation."""
        if not idempotency_key:
            return
        redis_key = _REDIS_KEY_PREFIX + self._hash_key(idempotency_key)
        if self._redis_client is not None:
            try:
                raw = self._redis_client.get(redis_key)
                if raw:
                    val_str = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                    if val_str == _IN_PROGRESS_SENTINEL:
                        self._redis_client.delete(redis_key)
                return
            except Exception as exc:
                logger.warning("IdempotencyService Redis release failed: %s -- falling back", exc)
                self._redis_client = None

        with self._fallback_lock:
            entry = self._fallback.get(redis_key)
            if entry:
                in_prog = entry[2] if len(entry) > 2 else False
                if in_prog:  # only delete if in_progress
                    del self._fallback[redis_key]

    def get_cached(self, idempotency_key: str | None) -> Any | None:
        """Return previously stored response for idempotency_key, or None if in-progress/absent."""
        if not idempotency_key:
            return None
        redis_key = _REDIS_KEY_PREFIX + self._hash_key(idempotency_key)
        if self._redis_client is not None:
            try:
                raw = self._redis_client.get(redis_key)
                if raw:
                    val_str = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                    if val_str == _IN_PROGRESS_SENTINEL:
                        return None
                    try:
                        return json.loads(val_str)
                    except Exception:
                        return val_str
                return None
            except Exception as exc:
                logger.warning("IdempotencyService Redis GET failed: %s -- falling back", exc)
                self._redis_client = None

        with self._fallback_lock:
            entry = self._fallback.get(redis_key)
            if entry:
                if len(entry) == 2:
                    payload, expires_at = entry
                    in_prog = False
                else:
                    payload, expires_at, in_prog = entry
                if expires_at > time.monotonic() and not in_prog:
                    return payload
                if expires_at <= time.monotonic():
                    del self._fallback[redis_key]
            return None

    def store(self, idempotency_key: str | None, response_body: Any) -> None:
        """Persist response_body under idempotency_key for TTL seconds."""
        self.complete(idempotency_key, response_body)

    @staticmethod
    def _hash_key(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode()).hexdigest()

    def _evict_expired(self) -> None:
        """Remove expired entries (must be called under _fallback_lock)."""
        now = time.monotonic()
        expired = [k for k, v in self._fallback.items() if v[1] <= now]
        for k in expired:
            del self._fallback[k]
