"""Unit and integration tests for Section 35.2 — Cache Layer & Graceful Degradation.

Tests:
  1. CacheService initialization & singleton instance.
  2. Read-aside and Write-through operations for Alerts, Cases, and Entities.
  3. TTL expiration logic & key formatting.
  4. Graceful degradation when Redis is offline (all methods fall back gracefully without raising exceptions).
  5. test_cache_miss_falls_back_to_db
  6. test_cache_hit_skips_db
  7. test_cache_invalidation_on_write
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.infrastructure.cache import (
    TTL_ALERT,
    TTL_CASE,
    TTL_ENTITY,
    CacheService,
    write_through_cache,
)


@pytest.fixture
def cache_service():
    """Reset CacheService singleton state for testing."""
    CacheService._instance = None
    CacheService._client = None
    CacheService._unavailable = False
    return CacheService.get()


@pytest.mark.asyncio
async def test_cache_service_singleton(cache_service: CacheService) -> None:
    """CacheService.get() must return the same process-level singleton instance."""
    instance1 = CacheService.get()
    instance2 = CacheService.get()
    assert instance1 is instance2


@pytest.mark.asyncio
async def test_graceful_degradation_when_redis_unavailable(cache_service: CacheService) -> None:
    """When Redis is unavailable or unconfigured, all methods must return None/default without raising exceptions."""
    # Force unavailable state
    CacheService._unavailable = True

    # Reads should return None
    assert await cache_service.get_alert("alert-123") is None
    assert await cache_service.get_case("case-123") is None
    assert await cache_service.get_entity("entity-123") is None
    assert await cache_service.get_entity_id_by_hash("hash-123") is None

    # Writes & invalidations should fail silently
    await cache_service.set_alert("alert-123", {"id": "alert-123", "status": "new"})
    await cache_service.invalidate_alert("alert-123", "bank_alpha")
    await cache_service.set_case("case-123", {"id": "case-123"})
    await cache_service.set_entity("entity-123", {"id": "entity-123", "privacy_id": "p123"})

    # Health check should return False
    assert await cache_service.health() is False

    # Rate limiter enforces sliding window locally when Redis is down without failing open
    assert await cache_service.rate_limit_check("test-ip", limit=5) is True
    for _ in range(4):
        assert await cache_service.rate_limit_check("test-ip", limit=5) is True
    # 6th request must be rejected under strict rate limiting
    assert await cache_service.rate_limit_check("test-ip", limit=5) is False


@pytest.mark.asyncio
async def test_ttl_constants() -> None:
    """Verify production TTL configurations align with design spec."""
    assert TTL_ALERT == 300
    assert TTL_CASE == 300
    assert TTL_ENTITY == 600


@pytest.mark.asyncio
async def test_cache_miss_falls_back_to_db(cache_service: CacheService) -> None:
    """Mock Redis miss -> assert DB function is called -> assert result returned."""
    mock_db_func = AsyncMock(return_value={"id": "alert-999", "status": "new"})

    @write_through_cache(ttl_seconds=300, key_prefix="alert")
    async def get_alert_from_db(self, alert_id: str):
        return await mock_db_func(alert_id)

    # Force cache miss
    CacheService._unavailable = True

    res = await get_alert_from_db(None, "alert-999")
    assert res == {"id": "alert-999", "status": "new"}
    mock_db_func.assert_called_once_with("alert-999")


@pytest.mark.asyncio
async def test_cache_hit_skips_db(cache_service: CacheService) -> None:
    """When cache has data, DB function must NOT be called."""
    mock_db_func = AsyncMock(return_value={"id": "alert-888", "status": "new"})

    @write_through_cache(ttl_seconds=300, key_prefix="alert")
    async def get_alert_from_db(self, alert_id: str):
        return await mock_db_func(alert_id)

    # Mock Redis client get to return cached data
    mock_redis = AsyncMock()
    mock_redis.get.return_value = '{"id": "alert-888", "status": "cached"}'
    CacheService._client = mock_redis
    CacheService._unavailable = False

    res = await get_alert_from_db(None, "alert-888")
    assert res == {"id": "alert-888", "status": "cached"}
    mock_db_func.assert_not_called()


@pytest.mark.asyncio
async def test_cache_invalidation_on_write(cache_service: CacheService) -> None:
    """Invalidate alert must delete key from Redis."""
    mock_redis = AsyncMock()
    CacheService._client = mock_redis
    CacheService._unavailable = False

    await cache_service.invalidate_alert("alert-777", "bank_alpha")
    mock_redis.delete.assert_called_once_with("alert:alert-777", "alerts:bank:bank_alpha")
