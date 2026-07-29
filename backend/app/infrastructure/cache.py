"""Redis cache layer — Phase 35.2.

Redis is the CACHE layer, not the primary data store.
PostgreSQL (via repositories) is the single source of truth.

Responsibilities:
  - Write-through cache: on DB write, push to Redis with TTL
  - Read-aside cache: on read, check Redis first, fall back to DB
  - Pub/Sub: broadcast real-time events to WebSocket consumers
  - Graceful degradation: if Redis is unavailable, all reads/writes
    fall through to the database transparently

Key naming convention:
  alert:{alert_id}          → single alert JSON, TTL=300s
  alerts:bank:{bank_id}     → list key, TTL=60s  (invalidated on write)
  case:{case_id}            → single case JSON, TTL=300s
  entity:{entity_id}        → single entity JSON, TTL=600s
  entity:hash:{privacy_id}  → entity_id lookup, TTL=600s
  simulation:{sid}:progress → in-flight simulation progress, TTL=3600s
  round:{round_id}          → federated round JSON, TTL=120s
  channel:training:{sid}    → pub/sub channel for WebSocket streaming
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis

from app.config import get_settings

logger = logging.getLogger(__name__)

# ── TTL constants (seconds) ────────────────────────────────────────────────────
TTL_ALERT = 300
TTL_ALERT_LIST = 60
TTL_CASE = 300
TTL_ENTITY = 600
TTL_ROUND = 120
TTL_SIMULATION_PROGRESS = 3600
TTL_RATE_LIMIT_WINDOW = 60


class CacheService:
    """Async Redis cache with transparent graceful degradation.

    If Redis is unavailable on any operation the exception is swallowed
    and the caller falls back to the database.  This keeps every cache
    interaction optional — the system stays correct without Redis.
    """

    _instance: CacheService | None = None
    _client: aioredis.Redis | None = None
    _unavailable: bool = False  # once True, skip all Redis attempts

    # ── Singleton ──────────────────────────────────────────────────────

    @classmethod
    def get(cls) -> CacheService:
        """Return the process-level singleton CacheService."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Client management ──────────────────────────────────────────────

    @property
    def client(self) -> aioredis.Redis | None:
        """Return the Redis client, or None when Redis is unavailable."""
        if self._unavailable:
            return None
        if self._client is None:
            settings = get_settings()
            url = getattr(settings, "redis_url", None) or getattr(
                settings, "celery_broker_url", None
            )
            if not url:
                self.__class__._unavailable = True
                return None
            try:
                self.__class__._client = aioredis.from_url(
                    url,
                    decode_responses=True,
                    socket_connect_timeout=0.5,
                    socket_timeout=1.0,
                    retry_on_timeout=False,
                )
            except Exception as exc:
                logger.warning("Redis client creation failed: %s — cache disabled", exc)
                self.__class__._unavailable = True
        return self.__class__._client

    async def ping(self) -> bool:
        """Return True if Redis is reachable."""
        c = self.client
        if c is None:
            return False
        try:
            return bool(await c.ping())
        except Exception:
            self.__class__._unavailable = True
            return False

    # ── Generic helpers ────────────────────────────────────────────────

    async def _get(self, key: str) -> Any | None:
        c = self.client
        if c is None:
            return None
        try:
            raw = await c.get(key)
            return json.loads(raw) if raw else None
        except Exception as exc:
            logger.debug("Cache GET miss/error key=%s: %s", key, exc)
            return None

    async def _set(self, key: str, value: Any, ttl: int) -> None:
        c = self.client
        if c is None:
            return
        try:
            await c.set(key, json.dumps(value), ex=ttl)
        except Exception as exc:
            logger.debug("Cache SET error key=%s: %s", key, exc)

    async def _delete(self, *keys: str) -> None:
        c = self.client
        if c is None:
            return
        try:
            await c.delete(*keys)
        except Exception as exc:
            logger.debug("Cache DEL error keys=%s: %s", keys, exc)

    # ── Alert cache ────────────────────────────────────────────────────

    async def get_alert(self, alert_id: str) -> dict | None:
        """Read-aside: return cached alert dict or None (caller queries DB)."""
        return await self._get(f"alert:{alert_id}")

    async def set_alert(self, alert_id: str, data: dict) -> None:
        """Write-through: cache alert after DB write."""
        await self._set(f"alert:{alert_id}", data, TTL_ALERT)

    async def invalidate_alert(self, alert_id: str, bank_id: str) -> None:
        """Invalidate single alert and its bank list on status change."""
        await self._delete(f"alert:{alert_id}", f"alerts:bank:{bank_id}")

    async def get_bank_alert_list(self, bank_id: str) -> list[dict] | None:
        """Return cached bank alert list or None."""
        return await self._get(f"alerts:bank:{bank_id}")

    async def set_bank_alert_list(self, bank_id: str, data: list[dict]) -> None:
        """Cache bank alert list (short TTL — invalidated on each new alert)."""
        await self._set(f"alerts:bank:{bank_id}", data, TTL_ALERT_LIST)

    # ── Case cache ─────────────────────────────────────────────────────

    async def get_case(self, case_id: str) -> dict | None:
        return await self._get(f"case:{case_id}")

    async def set_case(self, case_id: str, data: dict) -> None:
        await self._set(f"case:{case_id}", data, TTL_CASE)

    async def invalidate_case(self, case_id: str) -> None:
        await self._delete(f"case:{case_id}")

    # ── Entity cache ───────────────────────────────────────────────────

    async def get_entity(self, entity_id: str) -> dict | None:
        return await self._get(f"entity:{entity_id}")

    async def set_entity(self, entity_id: str, data: dict) -> None:
        await self._set(f"entity:{entity_id}", data, TTL_ENTITY)
        # Also maintain privacy_id → entity_id lookup
        if "privacy_id" in data:
            await self._set(f"entity:hash:{data['privacy_id']}", entity_id, TTL_ENTITY)

    async def get_entity_id_by_hash(self, privacy_id: str) -> str | None:
        """Resolve privacy_id hash → entity_id without hitting PostgreSQL."""
        return await self._get(f"entity:hash:{privacy_id}")

    async def invalidate_entity(self, entity_id: str, privacy_id: str | None = None) -> None:
        keys = [f"entity:{entity_id}"]
        if privacy_id:
            keys.append(f"entity:hash:{privacy_id}")
        await self._delete(*keys)

    # ── Federated round cache ──────────────────────────────────────────

    async def get_round(self, round_id: str) -> dict | None:
        return await self._get(f"round:{round_id}")

    async def set_round(self, round_id: str, data: dict) -> None:
        await self._set(f"round:{round_id}", data, TTL_ROUND)

    async def invalidate_round(self, round_id: str) -> None:
        await self._delete(f"round:{round_id}")

    # ── Simulation progress (pub/sub + KV) ────────────────────────────

    async def set_simulation_progress(self, simulation_id: str, data: dict) -> None:
        """Cache in-flight simulation progress with long TTL."""
        await self._set(f"simulation:{simulation_id}:progress", data, TTL_SIMULATION_PROGRESS)

    async def get_simulation_progress(self, simulation_id: str) -> dict | None:
        return await self._get(f"simulation:{simulation_id}:progress")

    async def publish_training_event(self, simulation_id: str, event: dict) -> None:
        """Publish a training event to Redis pub/sub for WebSocket consumers."""
        c = self.client
        if c is None:
            return
        try:
            channel = f"channel:training:{simulation_id}"
            await c.publish(channel, json.dumps(event))
        except Exception as exc:
            logger.debug("Pub/sub publish error simulation=%s: %s", simulation_id, exc)

    # ── Rate limiting (sliding window counter) ─────────────────────────

    async def rate_limit_check(
        self, key: str, limit: int, window: int = TTL_RATE_LIMIT_WINDOW
    ) -> bool:
        """Increment sliding-window counter. Return True if request is allowed.

        Uses Redis INCR + EXPIRE so the counter resets after the window.
        Falls back to True (allow) when Redis is unavailable.
        """
        c = self.client
        if c is None:
            return True  # graceful degradation — allow all when cache down
        try:
            rk = f"ratelimit:{key}"
            count = await c.incr(rk)
            if count == 1:
                await c.expire(rk, window)
            return count <= limit
        except Exception as exc:
            logger.debug("Rate limit check error key=%s: %s", key, exc)
            return True  # fail open

    # ── Health ─────────────────────────────────────────────────────────

    async def health(self) -> bool:
        """Public alias for health check endpoint."""
        return await self.ping()


# ── Module-level helpers (backward-compat with cache.py imports) ───────────────


def write_through_cache(ttl_seconds: int = 900, key_prefix: str = ""):
    """Decorator: on read -> check Redis first -> on miss, read DB -> populate Redis.

    Swallows Redis exceptions gracefully so DB calls always succeed.
    """
    import functools

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            cache = CacheService.get()
            # Construct a cache key from prefix and first string arg/kwarg
            key_arg = None
            if args and len(args) > 1 and isinstance(args[1], str):
                key_arg = args[1]
            elif kwargs:
                key_arg = next((v for v in kwargs.values() if isinstance(v, str)), None)

            prefix = key_prefix or func.__name__
            cache_key = f"{prefix}:{key_arg}" if key_arg else None

            # 1. Try Cache Read
            if cache_key:
                cached = await cache._get(cache_key)
                if cached is not None:
                    return cached

            # 2. Cache Miss: Execute DB query
            result = await func(*args, **kwargs)

            # 3. Populate Cache on DB result
            if cache_key and result is not None:
                # Convert ORM model or object to dict if needed
                data_to_cache = result
                if hasattr(result, "_to_dict"):
                    data_to_cache = result._to_dict()
                elif hasattr(result, "__dict__"):
                    data_to_cache = {
                        k: v for k, v in result.__dict__.items() if not k.startswith("_")
                    }
                await cache._set(cache_key, data_to_cache, ttl_seconds)

            return result

        return wrapper

    return decorator


async def set_simulation_progress(simulation_id: str, data: dict) -> None:
    """Backward-compatible wrapper kept for callers that import from cache directly."""
    await CacheService.get().set_simulation_progress(simulation_id, data)


async def get_simulation_progress(simulation_id: str) -> dict | None:
    """Backward-compatible wrapper."""
    return await CacheService.get().get_simulation_progress(simulation_id)


async def publish_training_event(simulation_id: str, event: dict) -> None:
    """Backward-compatible wrapper."""
    await CacheService.get().publish_training_event(simulation_id, event)


async def check_redis_health() -> bool:
    """Backward-compatible wrapper used by health.py router."""
    return await CacheService.get().health()


def get_redis_client() -> Any | None:
    """Synchronous Redis client helper for model cache and pubsub operations."""
    try:
        import redis

        settings = get_settings()
        url = getattr(settings, "redis_url", None) or "redis://localhost:6379/0"
        return redis.from_url(url)
    except Exception:
        return None
