"""Unit tests for IdempotencyService with in-memory fallback and TTL eviction."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from app.application.services.idempotency import IdempotencyService


def test_idempotency_in_memory_store_and_get():
    """Verify storing and retrieving JSON-serializable payloads in in-memory fallback."""
    svc = IdempotencyService()
    svc._redis_client = None  # force fallback

    key = "req-uuid-12345"
    payload = {"status": "success", "simulation_id": "sim-999", "amount": 1500.50}

    assert svc.get_cached(key) is None

    svc.store(key, payload)
    cached = svc.get_cached(key)

    assert cached is not None
    assert cached["status"] == "success"
    assert cached["simulation_id"] == "sim-999"
    assert cached["amount"] == 1500.50


def test_idempotency_empty_or_none_key():
    """Verify None or empty keys return None and do not store."""
    svc = IdempotencyService()
    assert svc.get_cached(None) is None
    assert svc.get_cached("") is None

    svc.store(None, {"data": 123})
    svc.store("", {"data": 123})


def test_idempotency_ttl_expiration():
    """Verify expired items are evicted and return None."""
    svc = IdempotencyService()
    svc._redis_client = None

    key = "short-lived-key"
    payload = {"ok": True}

    svc.store(key, payload)
    redis_key = "idem:" + svc._hash_key(key)

    # Artificially set expiration in the past
    with svc._fallback_lock:
        svc._fallback[redis_key] = (payload, time.monotonic() - 1.0)

    assert svc.get_cached(key) is None
    with svc._fallback_lock:
        assert redis_key not in svc._fallback


def test_idempotency_redis_mode():
    """Verify Redis get/setex interactions when Redis client is active."""
    svc = IdempotencyService()
    mock_redis = MagicMock()
    mock_redis.get.return_value = '{"stored_via": "redis", "val": 42}'
    svc._redis_client = mock_redis

    key = "redis-key-abc"
    cached = svc.get_cached(key)
    assert cached == {"stored_via": "redis", "val": 42}
    mock_redis.get.assert_called_once()

    svc.store(key, {"new": 100})
    mock_redis.setex.assert_called_once()
