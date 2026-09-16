"""Unit tests for hardened WebSocketConnectionManager and streaming WebSocket endpoints.

Verifies:
- Room-based multi-tenant multiplexing and broadcast isolation
- Capacity boundaries and max connection rejection (code 1013)
- Active heartbeat fanout and dead client pruning
- Idle connection watchdog and eviction
- Sliding-window inbound message rate limiting
- Frame size guard against oversized DoS payloads
- Safe non-primitive JSON serialization (default=str)
- Room and telemetry statistics reporting
- Live telemetry ping-pong frame exchange
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocket

from app.main import app
from app.presentation.websockets.manager import WebSocketConnectionManager


@pytest.fixture
def mock_websocket() -> MagicMock:
    """Creates a mock WebSocket instance with async methods."""
    ws = MagicMock(spec=WebSocket)
    ws.accept = AsyncMock()
    ws.send_text = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.fixture
def manager() -> WebSocketConnectionManager:
    """Creates a fresh WebSocketConnectionManager for isolation."""
    return WebSocketConnectionManager(
        max_connections=5,
        send_timeout_seconds=0.5,
        max_payload_bytes=1024,
    )


@pytest.mark.asyncio
async def test_websocket_connect_and_disconnect_lifecycle(
    manager: WebSocketConnectionManager,
    mock_websocket: MagicMock,
):
    """Verify basic connect and full disconnect lifecycle."""
    connected = await manager.connect(mock_websocket, room="tenant_alpha")
    assert connected is True
    mock_websocket.accept.assert_awaited_once()

    stats = manager.get_stats()
    assert stats["active_connections"] == 1
    assert stats["active_rooms_count"] == 1
    assert "tenant_alpha" in manager.list_rooms()
    assert manager.get_room_stats("tenant_alpha")["active_connections"] == 1

    # Disconnect
    await manager.disconnect(mock_websocket)
    stats_after = manager.get_stats()
    assert stats_after["active_connections"] == 0
    assert stats_after["active_rooms_count"] == 0
    assert manager.list_rooms() == []


@pytest.mark.asyncio
async def test_websocket_capacity_rejection_at_max_connections():
    """Verify manager rejects new connections when max_connections limit is reached."""
    small_manager = WebSocketConnectionManager(max_connections=2)
    ws1 = MagicMock(spec=WebSocket, accept=AsyncMock(), close=AsyncMock())
    ws2 = MagicMock(spec=WebSocket, accept=AsyncMock(), close=AsyncMock())
    ws3 = MagicMock(spec=WebSocket, accept=AsyncMock(), close=AsyncMock())

    assert await small_manager.connect(ws1) is True
    assert await small_manager.connect(ws2) is True
    # Third connection must be rejected
    assert await small_manager.connect(ws3) is False
    ws3.close.assert_awaited_once_with(code=1013, reason="Server at capacity; try again later")


@pytest.mark.asyncio
async def test_room_based_broadcast_isolation(manager: WebSocketConnectionManager):
    """Verify broadcast_to_room delivers only to clients in the target room."""
    ws_alpha = MagicMock(spec=WebSocket, accept=AsyncMock(), send_text=AsyncMock(), close=AsyncMock())
    ws_beta = MagicMock(spec=WebSocket, accept=AsyncMock(), send_text=AsyncMock(), close=AsyncMock())
    ws_global = MagicMock(spec=WebSocket, accept=AsyncMock(), send_text=AsyncMock(), close=AsyncMock())

    await manager.connect(ws_alpha, room="bank_alpha")
    await manager.connect(ws_beta, room="bank_beta")
    await manager.connect(ws_global)

    # Broadcast to bank_alpha only
    payload = {"alert": "fraud_detected", "severity": "HIGH"}
    res = await manager.broadcast_to_room("bank_alpha", payload)

    assert res["total"] == 1
    assert res["delivered"] == 1
    assert res["dropped"] == 0
    ws_alpha.send_text.assert_awaited_once()
    ws_beta.send_text.assert_not_awaited()
    ws_global.send_text.assert_not_awaited()

    # Dynamic join room
    await manager.join_room(ws_global, "bank_alpha")
    res2 = await manager.broadcast_to_room("bank_alpha", {"ping": "check"})
    assert res2["delivered"] == 2
    ws_global.send_text.assert_awaited_once()

    # Dynamic leave room
    await manager.leave_room(ws_global, "bank_alpha")
    res3 = await manager.broadcast_to_room("bank_alpha", {"ping": "check_2"})
    assert res3["delivered"] == 1


@pytest.mark.asyncio
async def test_heartbeat_and_dead_client_pruning(manager: WebSocketConnectionManager):
    """Verify dead/unresponsive sockets are pruned during heartbeat or broadcast."""
    ws_healthy = MagicMock(spec=WebSocket, accept=AsyncMock(), send_text=AsyncMock(), close=AsyncMock())
    ws_dead = MagicMock(spec=WebSocket, accept=AsyncMock(), send_text=AsyncMock(), close=AsyncMock())
    ws_dead.send_text.side_effect = RuntimeError("Broken pipe")

    await manager.connect(ws_healthy, room="live")
    await manager.connect(ws_dead, room="live")
    assert manager.get_stats()["active_connections"] == 2

    # Send heartbeat
    res = await manager.send_heartbeat()
    assert res["total"] == 2
    assert res["delivered"] == 1
    assert res["dropped"] == 1

    # Dead client must have been evicted
    assert manager.get_stats()["active_connections"] == 1
    assert ws_dead not in manager._active_connections
    ws_dead.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_idle_connection_eviction(manager: WebSocketConnectionManager):
    """Verify sockets idle beyond threshold are safely closed and evicted."""
    ws_active = MagicMock(spec=WebSocket, accept=AsyncMock(), send_text=AsyncMock(), close=AsyncMock())
    ws_idle = MagicMock(spec=WebSocket, accept=AsyncMock(), send_text=AsyncMock(), close=AsyncMock())

    await manager.connect(ws_active, room="monitored")
    await manager.connect(ws_idle, room="monitored")

    # Simulate ws_idle being idle for 600s
    manager._client_last_seen[ws_idle] = time.time() - 600.0
    manager._client_last_seen[ws_active] = time.time() - 10.0

    evicted_count = await manager.evict_idle_connections(max_idle_seconds=300.0)
    assert evicted_count == 1
    assert manager.get_stats()["active_connections"] == 1
    assert ws_idle not in manager._active_connections
    ws_idle.close.assert_awaited_once_with(code=1000, reason="Inactivity timeout")


def test_inbound_rate_limiting(manager: WebSocketConnectionManager, mock_websocket: MagicMock):
    """Verify sliding-window rate limit threshold enforcement."""
    # Allow 3 messages per 10 seconds
    for _ in range(3):
        assert manager.check_inbound_rate_limit(mock_websocket, max_messages=3, window_seconds=10.0) is True

    # 4th message exceeds threshold
    assert manager.check_inbound_rate_limit(mock_websocket, max_messages=3, window_seconds=10.0) is False


def test_validate_frame_size(manager: WebSocketConnectionManager):
    """Verify payload size validation against max_payload_bytes."""
    small_payload = "hello world"
    assert manager.validate_frame_size(small_payload) is True

    oversized_payload = "x" * 2048  # Manager max is 1024 bytes
    assert manager.validate_frame_size(oversized_payload) is False


@pytest.mark.asyncio
async def test_safe_non_primitive_serialization(manager: WebSocketConnectionManager):
    """Verify broadcast converts non-standard primitives like sets and objects safely with default=str."""
    ws = MagicMock(spec=WebSocket, accept=AsyncMock(), send_text=AsyncMock(), close=AsyncMock())
    await manager.connect(ws)

    complex_payload = {
        "event": "audit_log",
        "tags": {"crypto", "secagg"},
        "timestamp": time.time(),
    }
    res = await manager.broadcast(complex_payload)
    assert res["delivered"] == 1
    ws.send_text.assert_awaited_once()


def test_telemetry_websocket_client_exchange():
    """Verify /ws/telemetry connects, emits initial telemetry handshake, and responds to ping."""
    client = TestClient(app)
    with client.websocket_connect("/ws/telemetry") as ws:
        # Initial greeting event
        initial_raw = ws.receive_text()
        assert "CONNECTED" in initial_raw
        assert "FastAPI Bi-Directional Stream" in initial_raw

        # Send inbound ping frame
        ws.send_text("PING")
        pong_raw = ws.receive_text()
        assert "PONG" in pong_raw
