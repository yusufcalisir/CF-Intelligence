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
    mock_ws.receive_text = AsyncMock(side_effect=TimeoutError())
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
    assert mock_ws.send_text.call_count >= 2
    mock_ws.send_text.assert_any_call('{"event": "round_1_complete"}')
    mock_ws.send_text.assert_any_call('{"event": "round_2_complete"}')


def test_redact_redis_url_masks_credentials() -> None:
    """Verify that redact_redis_url strips passwords/tokens while preserving connection endpoints."""
    from app.config import redact_redis_url

    # 1. Cloud managed Redis with username and password
    cloud_url = "rediss://default:upstash_token_secret@us1-fast-cat.upstash.io:6379/0"
    redacted = redact_redis_url(cloud_url)
    assert "upstash_token_secret" not in redacted
    assert "default:***@" in redacted
    assert "us1-fast-cat.upstash.io:6379/0" in redacted
    assert redacted.startswith("rediss://")

    # 2. Local Redis with password only
    local_pwd = "redis://:cfi_local_secret@127.0.0.1:6379/1"
    redacted_local = redact_redis_url(local_pwd)
    assert "cfi_local_secret" not in redacted_local
    assert ":***@" in redacted_local

    # 3. Plain unauthenticated Redis
    plain = "redis://localhost:6379/0"
    assert redact_redis_url(plain) == "redis://localhost:6379/0"

    # 4. None / empty
    assert redact_redis_url(None) == "not configured"
    assert redact_redis_url("") == "not configured"

    # 5. Settings property integration
    s = Settings(
        redis_host="managed.redis.net",
        redis_port=6380,
        redis_password="super_secret_pwd",
        redis_tls=True,
    )
    assert "super_secret_pwd" not in s.redacted_redis_url
    assert ":***@" in s.redacted_redis_url


@pytest.mark.asyncio
async def test_training_ws_handles_connect_timeout_with_diagnostic_precision() -> None:
    """Verify that TimeoutError during initial connect ping triggers CONNECT_TIMEOUT and in-process fallback."""
    from app.presentation.websockets.training_ws import _handle_training_ws

    mock_ws = AsyncMock()
    mock_ws.receive_text = AsyncMock(side_effect=asyncio.CancelledError())

    mock_redis = MagicMock()
    mock_redis.ping = AsyncMock(side_effect=TimeoutError("Connection probe timed out"))

    with (
        patch("app.presentation.websockets.training_ws.get_settings") as mock_settings,
        patch(
            "app.presentation.websockets.training_ws.get_training_redis_pool",
            return_value=MagicMock(),
        ),
        patch("redis.asyncio.Redis", return_value=mock_redis),
        patch(
            "app.presentation.websockets.training_ws.training_ws_manager.connect", return_value=True
        ),
        patch(
            "app.presentation.websockets.training_ws.training_ws_manager.disconnect",
            return_value=None,
        ),
        patch(
            "app.presentation.websockets.training_ws.training_ws_manager.get_room_history",
            return_value=[],
        ),
    ):
        settings_instance = MagicMock()
        settings_instance.redis_url = "rediss://default:cloud_token@us1.upstash.io:6379/0"
        mock_settings.return_value = settings_instance

        with pytest.raises(asyncio.CancelledError):
            await _handle_training_ws(mock_ws, simulation_id="live_prod_v2")

        # Ping was attempted
        mock_redis.ping.assert_called_once()
        # WebSocket sent the connected fallback event
        assert any(
            '"mode": "in_process"' in call.args[0]
            for call in mock_ws.send_text.call_args_list
            if call.args
        )


@pytest.mark.asyncio
async def test_stream_via_redis_handles_lrange_timeout_gracefully() -> None:
    """Verify that TimeoutError during lrange replay doesn't abort pub/sub streaming."""
    mock_ws = AsyncMock()
    mock_ws.receive_text = AsyncMock(side_effect=TimeoutError())
    mock_redis = MagicMock()

    # Replay times out (REPLAY_READ_TIMEOUT)
    mock_redis.lrange = MagicMock(side_effect=TimeoutError("Lrange timed out"))

    mock_pubsub = MagicMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.get_message = AsyncMock(
        side_effect=[
            None,  # Idle polling
            {"type": "message", "data": '{"event": "live_event"}'},
            asyncio.CancelledError(),
        ]
    )
    mock_pubsub.unsubscribe = AsyncMock()
    mock_pubsub.aclose = AsyncMock()
    mock_pubsub.close = AsyncMock()
    mock_redis.pubsub.return_value = mock_pubsub

    with pytest.raises(asyncio.CancelledError):
        await _stream_via_redis(mock_ws, "sim_test_timeout", mock_redis)

    # Should still have subscribed and received live event despite replay timeout
    mock_pubsub.subscribe.assert_called_once_with("training:sim_test_timeout")
    mock_ws.send_text.assert_any_call('{"event": "live_event"}')
    mock_pubsub.unsubscribe.assert_called_once_with("training:sim_test_timeout")
    assert mock_pubsub.aclose.call_count == 1 or mock_pubsub.close.call_count == 1


@pytest.mark.asyncio
async def test_manager_disconnect_cleans_active_connections_when_leaving_room() -> None:
    """Verify that manager disconnect(websocket, room=...) removes the socket from _active_connections

    when no other rooms remain, preventing stale connection accumulation.
    """
    from app.presentation.websockets.manager import WebSocketConnectionManager

    manager = WebSocketConnectionManager()
    mock_ws = AsyncMock()

    # Connect to a room
    await manager.connect(mock_ws, room="simulation:test_sim_1")
    assert mock_ws in manager._active_connections
    assert mock_ws in manager._rooms["simulation:test_sim_1"]

    # Disconnect with room specified
    await manager.disconnect(mock_ws, room="simulation:test_sim_1")
    assert mock_ws not in manager._active_connections
    assert "simulation:test_sim_1" not in manager._rooms
    assert mock_ws not in manager._ws_rooms
    assert mock_ws not in manager._client_last_seen


@pytest.mark.asyncio
async def test_client_disconnect_does_not_activate_inprocess_fallback() -> None:
    """Verify that WebSocketDisconnect during streaming does NOT trigger in-process fallback or send-after-close."""
    from fastapi import WebSocketDisconnect

    from app.presentation.websockets.training_ws import _handle_training_ws

    mock_ws = AsyncMock()
    mock_redis = MagicMock()
    mock_redis.ping = AsyncMock(return_value=True)

    with (
        patch("app.presentation.websockets.training_ws.get_settings") as mock_settings,
        patch(
            "app.presentation.websockets.training_ws.get_training_redis_pool",
            return_value=MagicMock(),
        ),
        patch("redis.asyncio.Redis", return_value=mock_redis),
        patch(
            "app.presentation.websockets.training_ws.training_ws_manager.connect", return_value=True
        ),
        patch(
            "app.presentation.websockets.training_ws.training_ws_manager.disconnect",
            return_value=None,
        ),
        patch(
            "app.presentation.websockets.training_ws._stream_via_redis",
            side_effect=WebSocketDisconnect(code=1000),
        ),
        patch("app.presentation.websockets.training_ws._stream_via_inprocess") as mock_inprocess,
    ):
        settings_instance = MagicMock()
        settings_instance.redis_url = "rediss://default:cloud_token@us1.upstash.io:6379/0"
        mock_settings.return_value = settings_instance

        # Client disconnect should exit cleanly without raising or calling fallback
        await _handle_training_ws(mock_ws, simulation_id="live_prod_v2")

        # In-process fallback MUST NOT be activated for client disconnect
        mock_inprocess.assert_not_called()


@pytest.mark.asyncio
async def test_genuine_redis_failure_activates_inprocess_fallback() -> None:
    """Verify that genuine Redis transport failure while client is alive activates in-process fallback."""
    from app.presentation.websockets.training_ws import _handle_training_ws

    mock_ws = AsyncMock()
    # Client is still connected
    mock_ws.client_state = 1  # CONNECTED
    mock_redis = MagicMock()
    mock_redis.ping = AsyncMock(return_value=True)

    with (
        patch("app.presentation.websockets.training_ws.get_settings") as mock_settings,
        patch(
            "app.presentation.websockets.training_ws.get_training_redis_pool",
            return_value=MagicMock(),
        ),
        patch("redis.asyncio.Redis", return_value=mock_redis),
        patch(
            "app.presentation.websockets.training_ws.training_ws_manager.connect", return_value=True
        ),
        patch(
            "app.presentation.websockets.training_ws.training_ws_manager.disconnect",
            return_value=None,
        ),
        patch(
            "app.presentation.websockets.training_ws._stream_via_redis",
            side_effect=ConnectionError("Redis dropped"),
        ),
        patch("app.presentation.websockets.training_ws._stream_via_inprocess") as mock_inprocess,
    ):
        settings_instance = MagicMock()
        settings_instance.redis_url = "rediss://default:cloud_token@us1.upstash.io:6379/0"
        mock_settings.return_value = settings_instance

        await _handle_training_ws(mock_ws, simulation_id="sim_live_123")

        # In-process fallback MUST be activated because socket is still connected
        mock_inprocess.assert_called_once_with(mock_ws, "sim_live_123", "simulation:sim_live_123")


@pytest.mark.asyncio
async def test_training_ws_query_param_simulation_id_extraction() -> None:
    """Verify that /ws/training extracts simulation_id from query parameters if provided."""
    from app.presentation.websockets.training_ws import training_websocket_default

    mock_ws = AsyncMock()
    mock_ws.query_params = {"simulation_id": "sim_custom_uuid_888"}

    with patch("app.presentation.websockets.training_ws._handle_training_ws") as mock_handle:
        await training_websocket_default(mock_ws)
        mock_handle.assert_called_once_with(mock_ws, "sim_custom_uuid_888")

    # Also test camelCase simulationId
    mock_ws2 = AsyncMock()
    mock_ws2.query_params = {"simulationId": "sim_custom_uuid_999"}
    with patch("app.presentation.websockets.training_ws._handle_training_ws") as mock_handle:
        await training_websocket_default(mock_ws2)
        mock_handle.assert_called_once_with(mock_ws2, "sim_custom_uuid_999")

    # Fallback to default
    mock_ws3 = AsyncMock()
    mock_ws3.query_params = {}
    with patch("app.presentation.websockets.training_ws._handle_training_ws") as mock_handle:
        await training_websocket_default(mock_ws3)
        mock_handle.assert_called_once_with(mock_ws3, "live_prod_v2")


@pytest.mark.asyncio
async def test_get_training_redis_pool_reuses_connection_pool() -> None:
    """Verify that get_training_redis_pool returns the singleton pool instance across calls."""
    from app.presentation.websockets.training_ws import (
        close_training_redis_pool,
        get_training_redis_pool,
    )

    await close_training_redis_pool()
    with patch("redis.asyncio.ConnectionPool.from_url") as mock_from_url:
        mock_pool = MagicMock()
        mock_pool.disconnect = AsyncMock()
        mock_from_url.return_value = mock_pool

        pool1 = await get_training_redis_pool("redis://localhost:6379")
        pool2 = await get_training_redis_pool("redis://localhost:6379")

        assert pool1 is pool2
        # Only one pool should be initialized
        assert mock_from_url.call_count == 1

    await close_training_redis_pool()
