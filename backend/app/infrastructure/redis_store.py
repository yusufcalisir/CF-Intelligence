import json
import logging
from typing import Any

import redis

from app.config import get_settings

logger = logging.getLogger(__name__)


class RedisStore:
    """Synchronous Redis-backed key-value and list storage helper.

    .. deprecated::
        RedisStore was the primary domain store before Phase 35.
        It is now **DEPRECATED** as a persistence layer.

        * Domain reads/writes  → use the AsyncSession repositories
          (AlertRepository, CaseRepository, EntityRepository, RoundRepository).
        * Caching              → use ``app.infrastructure.cache.CacheService``.
        * Pub/Sub              → use ``CacheService.publish_training_event()``.

        RedisStore is retained only for simulation progress tracking in
        ``simulation_tasks.py`` and rate-limiting in ``gateway.py`` until
        those are migrated.  All other uses should be removed.
    """

    # Class-level flag: once Redis is confirmed unreachable, skip for all instances
    _global_redis_unavailable: bool = False

    # Class-level shared in-memory stores for fallback mode, keyed by prefix
    _shared_fallback_stores: dict[str, dict] = {}

    def __init__(self, prefix: str):
        self.prefix = prefix
        self.settings = get_settings()
        self._redis_client = None
        self._redis_failed = False

    @property
    def _fallback_store(self) -> dict:
        if self.prefix not in RedisStore._shared_fallback_stores:
            RedisStore._shared_fallback_stores[self.prefix] = {}
        return RedisStore._shared_fallback_stores[self.prefix]

    @property
    def client(self):
        if self._redis_failed or RedisStore._global_redis_unavailable:
            return None
        if self._redis_client is None:
            url = self.settings.redis_url
            if not url:
                # Redis not configured — silently use in-memory storage
                self._redis_failed = True
                RedisStore._global_redis_unavailable = True
                return None
            try:
                self._redis_client = redis.Redis.from_url(
                    url,
                    decode_responses=True,
                    socket_connect_timeout=0.5,
                    socket_timeout=0.5,
                )
                # Test connection
                self._redis_client.ping()
                self._redis_failed = False
            except Exception as e:
                logger.warning(
                    f"Redis connection failed for prefix '{self.prefix}': {e}. "
                    "Falling back to local in-memory storage for all stores."
                )
                self._redis_client = None
                self._redis_failed = True
                RedisStore._global_redis_unavailable = True
        return self._redis_client

    def _make_key(self, key: str) -> str:
        return f"{self.prefix}:{key}"

    def get(self, key: str) -> Any:
        c = self.client
        if c:
            try:
                val = c.get(self._make_key(key))
                if val:
                    return json.loads(val)
            except Exception as e:
                logger.error(f"Redis get failed for {key}: {e}")
        return self._fallback_store.get(key)

    def set(self, key: str, value: Any, ex: int | None = None) -> None:
        c = self.client
        if c:
            try:
                c.set(self._make_key(key), json.dumps(value), ex=ex)
                return
            except Exception as e:
                logger.error(f"Redis set failed for {key}: {e}")
        self._fallback_store[key] = value

    def delete(self, key: str) -> None:
        c = self.client
        if c:
            try:
                c.delete(self._make_key(key))
                return
            except Exception as e:
                logger.error(f"Redis delete failed for {key}: {e}")
        self._fallback_store.pop(key, None)

    def list_values(self) -> list[dict]:
        c = self.client
        if c:
            try:
                keys = c.keys(f"{self.prefix}:*")
                if not keys:
                    return []
                # Filter out lists stored under push_list
                filtered_keys = [k for k in keys if not k.endswith(":list_data")]
                if not filtered_keys:
                    return []
                vals = c.mget(filtered_keys)
                return [json.loads(v) for v in vals if v]
            except Exception as e:
                logger.error(f"Redis list_values failed: {e}")
        return list(self._fallback_store.values())

    def push_list(self, key: str, value: dict) -> None:
        c = self.client
        if c:
            try:
                c.rpush(self._make_key(f"{key}:list_data"), json.dumps(value))
                return
            except Exception as e:
                logger.error(f"Redis push_list failed: {e}")
        self._fallback_store.setdefault(f"{key}:list_data", []).append(value)

    def get_list(self, key: str) -> list[dict]:
        c = self.client
        if c:
            try:
                vals = c.lrange(self._make_key(f"{key}:list_data"), 0, -1)
                return [json.loads(v) for v in vals if v]
            except Exception as e:
                logger.error(f"Redis get_list failed: {e}")
        return self._fallback_store.get(f"{key}:list_data", [])

    def clear(self) -> None:
        self._fallback_store.clear()
        c = self.client
        if c:
            try:
                keys = c.keys(f"{self.prefix}:*")
                if keys:
                    c.delete(*keys)
            except Exception as e:
                logger.error(f"Redis clear failed: {e}")
