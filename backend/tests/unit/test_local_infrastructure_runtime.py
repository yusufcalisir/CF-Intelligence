"""Unit tests for local infrastructure runtime, Redis authentication, and storage configuration."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings
from app.presentation.messaging.redis_listener import RedisBankClientListener
from app.presentation.websockets.training_ws import _stream_via_redis


def test_settings_redis_url_construction() -> None:
    """Validate Redis URL construction with and without passwords and explicit REDIS_URL."""
    # 1. No password
    s1 = Settings(redis_host="localhost", redis_port=6379, redis_db=0, redis_password="")
    assert s1.redis_url == "redis://localhost:6379/0"

    # 2. With password
    s2 = Settings(
        redis_host="localhost",
        redis_port=6379,
        redis_db=0,
        redis_password="cfi_test_redis_pass",
    )
    assert s2.redis_url == "redis://:cfi_test_redis_pass@localhost:6379/0"

    # 3. Empty host returns None
    s3 = Settings(redis_host="")
    with patch.dict(os.environ, {}, clear=True):
        assert s3.redis_url is None

    # 4. REDIS_URL override
    with patch.dict(os.environ, {"REDIS_URL": "redis://custom-redis:6380/2"}):
        assert s1.redis_url == "redis://custom-redis:6380/2"

    # 5. TLS URL construction with rediss:// scheme
    s4 = Settings(
        redis_host="managed-redis.cloud",
        redis_port=6380,
        redis_db=0,
        redis_password="secure_tls_pass",
        redis_tls=True,
    )
    assert s4.redis_url == "rediss://:secure_tls_pass@managed-redis.cloud:6380/0"

    # 6. Direct REDIS_URL env var override with rediss:// scheme
    with patch.dict(os.environ, {"REDIS_URL": "rediss://default:cloud_secret@upstash.io:6379/0"}):
        assert s1.redis_url == "rediss://default:cloud_secret@upstash.io:6379/0"


@pytest.mark.asyncio
async def test_redis_bank_client_listener_dual_channel_subscription() -> None:
    """Verify listener subscribes to both hyphenated and normalized underscore channel names."""
    mock_redis = MagicMock()
    mock_pubsub = MagicMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.unsubscribe = AsyncMock()
    mock_redis.pubsub.return_value = mock_pubsub
    mock_redis.close = AsyncMock()

    with patch("app.presentation.messaging.redis_listener.Redis.from_url", return_value=mock_redis):
        listener = RedisBankClientListener(redis_url="redis://localhost:6379/0", bank_id="bank-a")
        await listener.start()

        # Should subscribe to both bank-a and bank_a variants
        mock_pubsub.subscribe.assert_called_once()
        subscribed_channels = set(mock_pubsub.subscribe.call_args[0])

        assert "bank_client_bank-a_init" in subscribed_channels
        assert "bank_client_bank-a_train" in subscribed_channels
        assert "bank_client_bank-a_evaluate" in subscribed_channels
        assert "bank_client_bank_a_init" in subscribed_channels
        assert "bank_client_bank_a_train" in subscribed_channels
        assert "bank_client_bank_a_evaluate" in subscribed_channels

        await listener.stop()


def test_tenant_logging_storage_path_isolation() -> None:
    """Ensure tenant logging utilizes get_storage_dir() and storage directory is writable."""
    from app.infrastructure.storage.storage_utils import get_storage_dir

    storage_dir = get_storage_dir()
    assert os.path.isabs(storage_dir)
    assert os.path.exists(storage_dir)

    logs_dir = os.path.join(storage_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    assert os.path.isdir(logs_dir)


@pytest.mark.asyncio
async def test_stream_via_redis_delivers_replayed_and_live_events() -> None:
    """Verify Redis list replay and pub/sub live event streaming over WebSocket."""
    mock_ws = AsyncMock()
    mock_redis = MagicMock()

    # 1. Past events replay via Redis list
    mock_redis.lrange = MagicMock(return_value=['{"event": "round_1_complete"}'])

    # 2. Live event via Redis pubsub channel
    mock_pubsub = MagicMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.get_message = AsyncMock(
        side_effect=[
            {"type": "message", "data": '{"event": "round_2_complete"}'},
            asyncio.CancelledError(),
        ]
    )
    mock_redis.pubsub.return_value = mock_pubsub

    with pytest.raises(asyncio.CancelledError):
        await _stream_via_redis(mock_ws, "sim_test_123", mock_redis)

    mock_redis.lrange.assert_called_once_with("simulation:sim_test_123:events", 0, -1)
    mock_pubsub.subscribe.assert_called_once_with("training:sim_test_123")
    assert mock_ws.send_text.call_count == 2
    mock_ws.send_text.assert_any_call('{"event": "round_1_complete"}')
    mock_ws.send_text.assert_any_call('{"event": "round_2_complete"}')
