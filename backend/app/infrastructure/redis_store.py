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

    def __init__(self, prefix: str, strict_production: bool = False):
        self.prefix: str = prefix
        self.strict_production: bool = strict_production
        self.settings = get_settings()
        self._redis_client: Any = None
        self._redis_failed: bool = False
        self._had_command_failure: bool = False

    @property
    def is_production(self) -> bool:
        return getattr(self.settings, "app_env", "development").lower().strip() == "production"

    def is_live(self) -> bool:
        """Check if the Redis client is operational and responding to PING."""
        if self._redis_failed or RedisStore._global_redis_unavailable:
            return False
        c = self.client
        if not c:
            return False
        try:
            return bool(c.ping())
        except Exception as e:
            logger.warning("Redis ping failed for prefix '%s': %s", self.prefix, e)
            self._redis_failed = True
            self._had_command_failure = True
            return False

    def has_active_shared_storage(self) -> bool:
        """Verify that genuine shared authoritative Redis storage is actively reachable and healthy."""
        if self._redis_failed or self._had_command_failure or RedisStore._global_redis_unavailable:
            return False
        return self.is_live()

    def reconnect(self) -> bool:
        """Attempt to re-establish connection to authoritative Redis after an outage."""
        self._redis_failed = False
        self._had_command_failure = False
        RedisStore._global_redis_unavailable = False
        self._redis_client = None
        return self.is_live()

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
                    if getattr(self.settings, "app_env", "development") == "development":
                        logger.info(
                            "Redis: connection not established for prefix '%s' (%s: %s). "
                            "Using local in-memory storage fallback (standalone/development mode).",
                            self.prefix,
                            type(e).__name__,
                            e,
                        )
                    else:
                        logger.warning(
                            "Redis connection failed for prefix '%s': %s. "
                            "Falling back to local in-memory storage for all stores.",
                            self.prefix,
                            e,
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
                return None
            except Exception as e:
                logger.error(f"Redis get failed for {key}: {e}")
                self._had_command_failure = True
                self._redis_failed = True
                if self.strict_production and self.is_production:
                    raise RuntimeError(f"Redis get failed in production for '{key}': {e}") from e
        if self.strict_production and self.is_production:
            raise RuntimeError(f"Redis store unavailable in production for get('{key}')")
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
                self._had_command_failure = True
                self._redis_failed = True
                if self.strict_production and self.is_production:
                    raise RuntimeError(f"Redis set failed in production for '{key}': {e}") from e
        if self.strict_production and self.is_production:
            raise RuntimeError(f"Redis store unavailable in production for set('{key}')")
        with RedisStore._lock:
            self._fallback_store[key] = value

    def incr(self, key: str) -> int:
        """Atomically increment the integer value of a key by one.

        In production Redis, this invokes the atomic INCR command across distributed workers.
        In fallback mode, it executes under RedisStore._lock for thread safety within the process.
        """
        c = self.client
        if c:
            try:
                return int(c.incr(self._make_key(key)))
            except Exception as e:
                logger.error(f"Redis incr failed for {key}: {e}")
                self._had_command_failure = True
                self._redis_failed = True
                if self.strict_production and self.is_production:
                    raise RuntimeError(f"Redis incr failed in production for '{key}': {e}") from e
        if self.strict_production and self.is_production:
            raise RuntimeError(f"Redis store unavailable in production for incr('{key}')")
        with RedisStore._lock:
            val = int(self._fallback_store.get(key) or 0) + 1
            self._fallback_store[key] = val
            return val

    def record_prediction_evidence(
        self,
        simulation_id: str,
        transaction_id: str,
        payload: dict[str, Any],
        bank_id: str | None = None,
        max_window_size: int = 1000,
    ) -> dict[str, Any]:
        """Atomically record prediction evidence and maintain bounded prediction index.

        In production Redis, executes via atomic Lua script:
        - Allocates monotonic sequence number via INCR on first appearance.
        - Preserves existing sequence, timestamp, and feedback on duplicate/retry.
        - Atomically inserts key into prediction_keys list and evicts oldest items if exceeding max_window_size.
        - Deletes evicted prediction records from Redis.
        - Scopes evidence identity to authoritative (simulation_id, bank_id, transaction_id).
        - In production with strict_production, fails closed (raises RuntimeError) if Redis write fails.
        """
        clean_bank = (
            str(bank_id or payload.get("bank_id") or "").lower().replace("-", "_").strip()
            if (bank_id or payload.get("bank_id"))
            else None
        )
        if clean_bank:
            rel_key = f"{simulation_id}:prediction:{clean_bank}:{transaction_id}"
            payload["bank_id"] = clean_bank
        else:
            rel_key = f"{simulation_id}:prediction:{transaction_id}"
            payload["bank_id"] = None

        pred_key = self._make_key(rel_key)
        seq_key = self._make_key(f"{simulation_id}:prediction_seq")
        list_key = self._make_key(f"{simulation_id}:prediction_keys")

        c = self.client
        if c:
            try:
                lua_script = """
                local pred_key = KEYS[1]
                local seq_key = KEYS[2]
                local list_key = KEYS[3]

                local tx_id = ARGV[1]
                local max_window = tonumber(ARGV[2])
                local payload = cjson.decode(ARGV[3])
                local rel_key = ARGV[4]
                local prefix = ARGV[5]

                local existing_raw = redis.call('GET', pred_key)
                local pred_seq
                local timestamp
                local actual_label = nil
                local is_retry = 0

                if existing_raw then
                    local existing = cjson.decode(existing_raw)
                    pred_seq = existing["pred_seq"]
                    timestamp = existing["timestamp"]
                    actual_label = existing["actual_label"]
                    is_retry = 1
                else
                    pred_seq = redis.call('INCR', seq_key)
                    timestamp = payload["timestamp"]
                    actual_label = payload["actual_label"]
                end

                payload["transaction_id"] = tx_id
                payload["pred_seq"] = pred_seq
                payload["timestamp"] = timestamp
                payload["actual_label"] = actual_label

                redis.call('SET', pred_key, cjson.encode(payload))

                local list_raw = redis.call('GET', list_key)
                local keys = {}
                if list_raw then
                    keys = cjson.decode(list_raw)
                end

                local found = false
                for i, k in ipairs(keys) do
                    if k == rel_key then
                        found = true
                        break
                    end
                end

                if not found then
                    table.insert(keys, rel_key)
                    while #keys > max_window do
                        local oldest_rel = table.remove(keys, 1)
                        local oldest_full = prefix .. ":" .. oldest_rel
                        redis.call('DEL', oldest_full)
                    end
                    redis.call('SET', list_key, cjson.encode(keys))
                end

                return cjson.encode({
                    pred_seq = pred_seq,
                    is_retry = (is_retry == 1),
                    record = payload,
                    retained_count = #keys
                })
                """
                res_raw = c.eval(
                    lua_script,
                    3,
                    pred_key,
                    seq_key,
                    list_key,
                    transaction_id,
                    str(max_window_size),
                    json.dumps(payload),
                    rel_key,
                    self.prefix,
                )
                if res_raw:
                    return json.loads(res_raw)
            except Exception as e:
                logger.error("Redis record_prediction_evidence failed for %s: %s", rel_key, e)
                self._had_command_failure = True
                self._redis_failed = True
                if self.strict_production and self.is_production:
                    raise RuntimeError(
                        f"Production evaluation storage failure: failed to record prediction evidence for '{transaction_id}' in Redis: {e}"
                    ) from e

        if self.is_production and (
            self._had_command_failure or self._redis_failed or self.strict_production
        ):
            raise RuntimeError(
                f"Production evaluation storage failure: shared Redis store is unavailable for transaction '{transaction_id}'."
            )

        with RedisStore._lock:
            existing = self._fallback_store.get(rel_key)
            if existing and "pred_seq" in existing:
                pred_seq = existing["pred_seq"]
                timestamp = existing.get("timestamp") or payload.get("timestamp")
                actual_label = existing.get("actual_label")
                is_retry = True
            else:
                seq_val = int(self._fallback_store.get(f"{simulation_id}:prediction_seq") or 0) + 1
                self._fallback_store[f"{simulation_id}:prediction_seq"] = seq_val
                pred_seq = seq_val
                timestamp = payload.get("timestamp")
                actual_label = payload.get("actual_label")
                is_retry = False

            payload["transaction_id"] = transaction_id
            payload["pred_seq"] = pred_seq
            payload["timestamp"] = timestamp
            payload["actual_label"] = actual_label
            self._fallback_store[rel_key] = payload

            list_val = self._fallback_store.get(f"{simulation_id}:prediction_keys")
            keys = list(list_val) if isinstance(list_val, list) else []
            if rel_key not in keys:
                keys.append(rel_key)
                while len(keys) > max_window_size:
                    oldest_rel = keys.pop(0)
                    self._fallback_store.pop(oldest_rel, None)
                self._fallback_store[f"{simulation_id}:prediction_keys"] = keys

            return {
                "pred_seq": pred_seq,
                "is_retry": is_retry,
                "record": payload,
                "retained_count": len(keys),
            }

    def get_prediction_evidence(
        self,
        simulation_id: str,
        transaction_id: str,
        bank_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Retrieve stored prediction evidence for a transaction with institutional scoping."""
        clean_bank = str(bank_id).lower().replace("-", "_").strip() if bank_id else None
        if clean_bank:
            scoped_key = f"{simulation_id}:prediction:{clean_bank}:{transaction_id}"
            rec = self.get(scoped_key)
            if rec is not None:
                return rec

        # Check legacy unscoped key
        legacy_key = f"{simulation_id}:prediction:{transaction_id}"
        rec = self.get(legacy_key)
        if rec is not None:
            rec_bank = rec.get("bank_id")
            if clean_bank and rec_bank:
                norm_rec_bank = str(rec_bank).lower().replace("-", "_").strip()
                if norm_rec_bank != clean_bank:
                    return rec
            return rec

        # Check if another bank owns this transaction_id in the simulation
        # (Allows upstream authorization gates to inspect institutional ownership and fail closed with 403)
        c = self.client
        if c:
            try:
                pattern = self._make_key(f"{simulation_id}:prediction:*:{transaction_id}")
                matched_keys = c.keys(pattern)
                prefix_len = len(self.prefix) + 1
                for k in matched_keys:
                    rel_k = k[prefix_len:]
                    other_rec = self.get(rel_k)
                    if other_rec is not None:
                        return other_rec
            except Exception as e:
                logger.error("Redis get_prediction_evidence pattern scan failed: %s", e)
        else:
            with RedisStore._lock:
                prefix_tag = f"{simulation_id}:prediction:"
                suffix_tag = f":{transaction_id}"
                for k, v in self._fallback_store.items():
                    if k.startswith(prefix_tag) and k.endswith(suffix_tag) and isinstance(v, dict):
                        return v

        return None

    def record_feedback(
        self,
        simulation_id: str,
        transaction_id: str,
        actual_label: int,
        bank_id: str | None = None,
    ) -> dict[str, Any]:
        """Atomically record ground truth label for an existing prediction record."""
        clean_bank = str(bank_id).lower().replace("-", "_").strip() if bank_id else ""
        scoped_rel_key = f"{simulation_id}:prediction:{clean_bank}:{transaction_id}" if clean_bank else ""
        legacy_rel_key = f"{simulation_id}:prediction:{transaction_id}"

        c = self.client
        if c:
            try:
                lua_script = """
                local scoped_key = KEYS[1]
                local legacy_key = KEYS[2]

                local label = tonumber(ARGV[1])
                local exp_bank = ARGV[2]
                local is_prod = tonumber(ARGV[3])
                local sim_id = ARGV[4]
                local tx_id = ARGV[5]
                local pfx = ARGV[6]

                local target_key = nil
                local raw = nil

                if scoped_key ~= "" then
                    raw = redis.call('GET', scoped_key)
                    if raw then
                        target_key = scoped_key
                    end
                end

                if not raw and legacy_key ~= "" then
                    raw = redis.call('GET', legacy_key)
                    if raw then
                        target_key = legacy_key
                    end
                end

                if not raw and exp_bank ~= "" and sim_id ~= "" and tx_id ~= "" then
                    local pat = pfx .. ":" .. sim_id .. ":prediction:*:" .. tx_id
                    local matched = redis.call('KEYS', pat)
                    if matched and #matched > 0 then
                        raw = redis.call('GET', matched[1])
                        target_key = matched[1]
                    end
                end

                if not raw then
                    return cjson.encode({found = false})
                end

                local rec = cjson.decode(raw)
                local rec_bank = string.lower(tostring(rec["bank_id"] or ""))

                if exp_bank ~= "" then
                    if rec_bank == "" then
                        if is_prod == 1 then
                            return cjson.encode({found = true, mismatch = true, error = "missing_owner"})
                        end
                    elseif rec_bank ~= exp_bank then
                        return cjson.encode({found = true, mismatch = true, error = "bank_mismatch"})
                    end
                end

                rec["actual_label"] = label
                redis.call('SET', target_key, cjson.encode(rec))
                return cjson.encode({found = true, record = rec})
                """
                scoped_full = self._make_key(scoped_rel_key) if scoped_rel_key else ""
                legacy_full = self._make_key(legacy_rel_key)
                is_prod_val = 1 if self.is_production else 0

                res_raw = c.eval(
                    lua_script,
                    2,
                    scoped_full,
                    legacy_full,
                    str(actual_label),
                    clean_bank,
                    str(is_prod_val),
                    simulation_id,
                    transaction_id,
                    self.prefix,
                )
                if res_raw:
                    return json.loads(res_raw)
            except Exception as e:
                logger.error("Redis record_feedback failed for %s (%s): %s", scoped_rel_key or legacy_rel_key, clean_bank, e)
                self._had_command_failure = True
                self._redis_failed = True
                if self.is_production:
                    raise RuntimeError(
                        f"Production evaluation storage failure: failed to record feedback for '{transaction_id}' in Redis: {e}"
                    ) from e

        if self.is_production and (
            self._had_command_failure or self._redis_failed or self.strict_production
        ):
            raise RuntimeError(
                f"Production evaluation storage failure: shared Redis store is unavailable for feedback on '{transaction_id}'."
            )

        with RedisStore._lock:
            rec = None
            target_key = None
            if scoped_rel_key and scoped_rel_key in self._fallback_store:
                target_key = scoped_rel_key
                rec = self._fallback_store.get(scoped_rel_key)
            elif legacy_rel_key in self._fallback_store:
                target_key = legacy_rel_key
                rec = self._fallback_store.get(legacy_rel_key)
            elif clean_bank:
                prefix_tag = f"{simulation_id}:prediction:"
                suffix_tag = f":{transaction_id}"
                for k, v in self._fallback_store.items():
                    if k.startswith(prefix_tag) and k.endswith(suffix_tag) and isinstance(v, dict):
                        target_key = k
                        rec = v
                        break

            if not rec or not isinstance(rec, dict):
                return {"found": False}

            rec_bank = str(rec.get("bank_id") or "").lower().replace("-", "_").strip()
            if clean_bank:
                if not rec_bank:
                    if self.is_production:
                        return {"found": True, "mismatch": True, "error": "missing_owner"}
                elif rec_bank != clean_bank:
                    return {"found": True, "mismatch": True, "error": "bank_mismatch"}

            rec["actual_label"] = actual_label
            self._fallback_store[target_key] = rec
            return {"found": True, "record": rec}

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
                self._had_command_failure = True
                self._redis_failed = True
                if self.strict_production and self.is_production:
                    raise RuntimeError(
                        f"Redis update_conditional failed in production for '{key}': {e}"
                    ) from e

        if self.strict_production and self.is_production:
            raise RuntimeError(
                f"Redis store unavailable in production for update_conditional('{key}')"
            )

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
                self._had_command_failure = True
                self._redis_failed = True
                if self.strict_production and self.is_production:
                    raise RuntimeError(f"Redis delete failed in production for '{key}': {e}") from e
        if self.strict_production and self.is_production:
            raise RuntimeError(f"Redis store unavailable in production for delete('{key}')")
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
