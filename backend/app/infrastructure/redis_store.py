import json
import logging
import threading
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
    _lock: threading.RLock = threading.RLock()

    def __init__(self, prefix: str):
        self.prefix: str = prefix
        self.settings = get_settings()
        self._redis_client: Any = None
        self._redis_failed: bool = False

    @property
    def _fallback_store(self) -> dict:
        with RedisStore._lock:
            if self.prefix not in RedisStore._shared_fallback_stores:
                RedisStore._shared_fallback_stores[self.prefix] = {}
            return RedisStore._shared_fallback_stores[self.prefix]

    @property
    def client(self) -> Any:
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
                r_client: redis.Redis = redis.Redis.from_url(
                    url,
                    decode_responses=True,
                    socket_connect_timeout=2.5,
                    socket_timeout=3.0,
                )
                # Test connection
                r_client.ping()
                self._redis_client = r_client
                self._redis_failed = False
            except Exception as e:
                recovered = False
                if getattr(self.settings, "app_env", "development") == "development":
                    import urllib.parse

                    try:
                        parsed = urllib.parse.urlparse(url)
                        hosts_to_try = [parsed.hostname]
                        if parsed.hostname == "redis":
                            hosts_to_try.append("127.0.0.1")

                        pwds_to_try = [
                            parsed.password,
                            self.settings.redis_password,
                            None,
                        ]
                        seen_candidates = set()
                        for h in hosts_to_try:
                            if not h:
                                continue
                            for p in pwds_to_try:
                                cand_key = (h, p)
                                if cand_key in seen_candidates:
                                    continue
                                seen_candidates.add(cand_key)
                                netloc = (
                                    f":{p}@{h}:{parsed.port or 6379}"
                                    if p
                                    else f"{h}:{parsed.port or 6379}"
                                )
                                candidate_url = urllib.parse.urlunparse(
                                    parsed._replace(netloc=netloc)
                                )
                                if candidate_url == url:
                                    continue
                                try:
                                    alt_client = redis.Redis.from_url(
                                        candidate_url,
                                        decode_responses=True,
                                        socket_connect_timeout=0.5,
                                        socket_timeout=0.5,
                                    )
                                    alt_client.ping()
                                    self._redis_client = alt_client
                                    self._redis_failed = False
                                    recovered = True
                                    logger.info(
                                        "RedisStore auto-recovered connection for prefix '%s' on %s",
                                        self.prefix,
                                        h,
                                    )
                                    break
                                except Exception:
                                    continue
                            if recovered:
                                break
                    except Exception:
                        pass

                if not recovered:
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
        with RedisStore._lock:
            return self._fallback_store.get(key)

    def set(self, key: str, value: Any, ex: int | None = None) -> None:
        c = self.client
        if c:
            try:
                c.set(self._make_key(key), json.dumps(value), ex=ex)
                return
            except Exception as e:
                logger.error(f"Redis set failed for {key}: {e}")
        with RedisStore._lock:
            self._fallback_store[key] = value

    def update_conditional(
        self,
        key: str,
        new_value: Any,
        expected_status: str | None = None,
        expected_version: int | None = None,
        expected_timeline_hash: str | None = None,
        ex: int | None = None,
    ) -> bool:
        """Atomically update a stored dictionary if preconditions match.

        In production Redis, this is executed atomically via a Lua script.
        In fallback mode, it executes under RedisStore._lock across all instances.
        Returns True if update succeeded, False if precondition failed (CAS mismatch).
        """
        c = self.client
        if c:
            try:
                lua_script = """
                local raw = redis.call('GET', KEYS[1])
                if not raw then
                    return 0
                end
                local cur = cjson.decode(raw)
                if ARGV[1] ~= "" and string.lower(tostring(cur["status"] or "")) ~= string.lower(ARGV[1]) then
                    return 0
                end
                if ARGV[2] ~= "" and tostring(cur["version"] or "") ~= ARGV[2] then
                    return 0
                end
                if ARGV[3] ~= "" and tostring(cur["timeline_hash"] or "") ~= ARGV[3] then
                    return 0
                end
                if ARGV[5] ~= "0" then
                    redis.call('SET', KEYS[1], ARGV[4], 'EX', tonumber(ARGV[5]))
                else
                    redis.call('SET', KEYS[1], ARGV[4])
                end
                return 1
                """
                exp_st = str(expected_status).lower() if expected_status is not None else ""
                exp_ver = str(expected_version) if expected_version is not None else ""
                exp_hash = str(expected_timeline_hash) if expected_timeline_hash is not None else ""
                ttl_str = str(ex) if ex else "0"
                res = c.eval(
                    lua_script,
                    1,
                    self._make_key(key),
                    exp_st,
                    exp_ver,
                    exp_hash,
                    json.dumps(new_value),
                    ttl_str,
                )
                return bool(res == 1)
            except Exception as e:
                logger.error(f"Redis update_conditional failed for {key}: {e}")

        with RedisStore._lock:
            cur = self._fallback_store.get(key)
            if not cur or not isinstance(cur, dict):
                return False
            if expected_status is not None:
                cur_status = str(cur.get("status", "")).lower()
                if cur_status != str(expected_status).lower():
                    return False
            if expected_version is not None:
                cur_ver = cur.get("version")
                if cur_ver != expected_version:
                    return False
            if expected_timeline_hash is not None:
                cur_hash = cur.get("timeline_hash")
                if cur_hash != expected_timeline_hash:
                    return False
            self._fallback_store[key] = new_value
            return True

    def delete(self, key: str) -> None:
        c = self.client
        if c:
            try:
                c.delete(self._make_key(key))
                return
            except Exception as e:
                logger.error(f"Redis delete failed for {key}: {e}")
        with RedisStore._lock:
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
        with RedisStore._lock:
            return list(self._fallback_store.values())

    def list_keys(self) -> list[str]:
        c = self.client
        if c:
            try:
                keys = c.keys(f"{self.prefix}:*")
                prefix_len = len(self.prefix) + 1
                return [k[prefix_len:] for k in keys if not k.endswith(":list_data")]
            except Exception as e:
                logger.error(f"Redis list_keys failed: {e}")
        with RedisStore._lock:
            return [k for k in self._fallback_store if not k.endswith(":list_data")]

    def push_list(self, key: str, value: dict) -> None:
        c = self.client
        if c:
            try:
                c.rpush(self._make_key(f"{key}:list_data"), json.dumps(value))
                return
            except Exception as e:
                logger.error(f"Redis push_list failed: {e}")
        with RedisStore._lock:
            self._fallback_store.setdefault(f"{key}:list_data", []).append(value)

    def get_list(self, key: str) -> list[dict]:
        c = self.client
        if c:
            try:
                vals = c.lrange(self._make_key(f"{key}:list_data"), 0, -1)
                return [json.loads(v) for v in vals if v]
            except Exception as e:
                logger.error(f"Redis get_list failed: {e}")
        with RedisStore._lock:
            return list(self._fallback_store.get(f"{key}:list_data", []))

    def clear(self) -> None:
        c = self.client
        if c:
            try:
                keys = c.keys(f"{self.prefix}:*")
                if keys:
                    c.delete(*keys)
            except Exception as e:
                logger.error(f"Redis clear failed: {e}")
        with RedisStore._lock:
            self._fallback_store.clear()
