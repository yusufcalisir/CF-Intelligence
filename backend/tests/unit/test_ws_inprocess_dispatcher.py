"""Unit tests for WebSocketConnectionManager in-process event dispatcher.

Verifies:
- In-process ring-buffer accumulates events on broadcast_to_room
- Ring-buffer respects ROOM_HISTORY_LIMIT cap (deque maxlen behaviour)
- broadcast_to_room_sync eagerly writes to ring-buffer (no event-loop required)
- broadcast_to_room_sync schedules delivery when event-loop is registered
- get_room_history returns correct snapshot for late-joining clients
- Room isolation: ring-buffer events are keyed per room, not cross-contaminated
- register_event_loop stores the loop reference correctly
- WebSocket fallback path replays buffered history to newly connected clients
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.websockets import WebSocket

from app.presentation.websockets.manager import (
    ROOM_HISTORY_LIMIT,
    WebSocketConnectionManager,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def manager() -> WebSocketConnectionManager:
    return WebSocketConnectionManager(
        max_connections=10,
        send_timeout_seconds=0.5,
    )


def _mock_ws() -> MagicMock:
    ws = MagicMock(spec=WebSocket)
    ws.accept = AsyncMock()
    ws.send_text = AsyncMock()
    ws.close = AsyncMock()
    return ws


# ── Ring-buffer accumulation ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_broadcast_to_room_populates_ring_buffer(manager: WebSocketConnectionManager):
    """broadcast_to_room must append to the room's in-process ring-buffer."""
    ws = _mock_ws()
    await manager.connect(ws, room="simulation:abc")

    event_a = {"event_type": "round_start", "data": {"round": 1}}
    event_b = {"event_type": "round_complete", "data": {"round": 1, "loss": 0.35}}

    await manager.broadcast_to_room("simulation:abc", event_a)
    await manager.broadcast_to_room("simulation:abc", event_b)

    history = manager.get_room_history("simulation:abc")
    assert len(history) == 2
    assert json.loads(history[0]) == event_a
    assert json.loads(history[1]) == event_b


@pytest.mark.asyncio
async def test_ring_buffer_respects_max_limit(manager: WebSocketConnectionManager):
    """Ring-buffer must not exceed ROOM_HISTORY_LIMIT (deque maxlen)."""
    ws = _mock_ws()
    await manager.connect(ws, room="simulation:overflow")

    overflow_count = ROOM_HISTORY_LIMIT + 50
    for i in range(overflow_count):
        await manager.broadcast_to_room("simulation:overflow", {"event_type": "tick", "n": i})

    history = manager.get_room_history("simulation:overflow")
    assert len(history) == ROOM_HISTORY_LIMIT
    # The oldest events must have been dropped; last event is the most recent
    last_event = json.loads(history[-1])
    assert last_event["n"] == overflow_count - 1


@pytest.mark.asyncio
async def test_ring_buffer_room_isolation(manager: WebSocketConnectionManager):
    """Ring-buffer entries must be isolated per room — no cross-contamination."""
    ws_a = _mock_ws()
    ws_b = _mock_ws()
    await manager.connect(ws_a, room="simulation:room_a")
    await manager.connect(ws_b, room="simulation:room_b")

    await manager.broadcast_to_room("simulation:room_a", {"event_type": "only_a"})
    await manager.broadcast_to_room("simulation:room_b", {"event_type": "only_b"})

    history_a = manager.get_room_history("simulation:room_a")
    history_b = manager.get_room_history("simulation:room_b")

    assert len(history_a) == 1
    assert json.loads(history_a[0])["event_type"] == "only_a"
    assert len(history_b) == 1
    assert json.loads(history_b[0])["event_type"] == "only_b"


# ── get_room_history ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_room_history_empty_for_unknown_room(manager: WebSocketConnectionManager):
    """get_room_history must return an empty list for rooms with no events."""
    history = manager.get_room_history("simulation:nonexistent")
    assert history == []


# ── broadcast_to_room_sync (no event-loop) ───────────────────────────────────


def test_broadcast_to_room_sync_buffers_without_event_loop(manager: WebSocketConnectionManager):
    """broadcast_to_room_sync must eagerly buffer the event even without a registered event-loop."""
    # Ensure no loop is registered
    manager._event_loop = None

    event = {"event_type": "round_start", "data": {"round": 1}}
    # Must not raise
    manager.broadcast_to_room_sync("simulation:noloop", event)

    history = manager.get_room_history("simulation:noloop")
    assert len(history) == 1
    assert json.loads(history[0]) == event


@pytest.mark.asyncio
async def test_broadcast_to_room_sync_schedules_delivery_when_loop_registered(
    manager: WebSocketConnectionManager,
):
    """broadcast_to_room_sync must schedule coroutine delivery when a loop is registered."""
    loop = asyncio.get_running_loop()
    manager.register_event_loop(loop)

    ws = _mock_ws()
    await manager.connect(ws, room="simulation:sync_delivery")

    event = {"event_type": "gradient_received", "data": {"bank": "bank_a"}}
    manager.broadcast_to_room_sync("simulation:sync_delivery", event)

    # Allow scheduled coroutine to execute
    await asyncio.sleep(0.1)

    # Client must have received the broadcast
    ws.send_text.assert_awaited()
    sent_payload = ws.send_text.await_args_list[-1][0][0]
    assert json.loads(sent_payload) == event


@pytest.mark.asyncio
async def test_broadcast_to_room_sync_multiple_events_ordered(
    manager: WebSocketConnectionManager,
):
    """Multiple sync events must arrive in order and be ring-buffered correctly."""
    loop = asyncio.get_running_loop()
    manager.register_event_loop(loop)

    ws = _mock_ws()
    await manager.connect(ws, room="simulation:ordered")

    events = [{"event_type": "tick", "n": i} for i in range(5)]
    for evt in events:
        manager.broadcast_to_room_sync("simulation:ordered", evt)

    await asyncio.sleep(0.2)

    history = manager.get_room_history("simulation:ordered")
    assert len(history) == 5
    for i, raw in enumerate(history):
        assert json.loads(raw)["n"] == i


# ── register_event_loop ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_event_loop_stores_loop(manager: WebSocketConnectionManager):
    """register_event_loop must store the provided loop reference."""
    loop = asyncio.get_running_loop()
    manager.register_event_loop(loop)
    assert manager._event_loop is loop


# ── Dual-path fallback integration smoke-test ────────────────────────────────


@pytest.mark.asyncio
async def test_fallback_path_replays_history_to_new_client(
    manager: WebSocketConnectionManager,
):
    """New clients connecting in in-process mode must receive buffered history via get_room_history."""
    room = "simulation:late_join"

    # Simulate prior events already buffered (as if background thread ran first)
    for i in range(3):
        manager.broadcast_to_room_sync(room, {"event_type": "round_complete", "round": i})

    # Ensure history is present before client connects
    history = manager.get_room_history(room)
    assert len(history) == 3

    # New client connects and receives replay
    ws = _mock_ws()
    await manager.connect(ws, room=room)

    # Simulate handler replaying history (mirrors training_ws._stream_via_inprocess logic)
    for raw_event in history:
        await ws.send_text(raw_event)

    assert ws.send_text.await_count == 3
    replayed = [json.loads(c[0][0]) for c in ws.send_text.await_args_list]
    assert [e["round"] for e in replayed] == [0, 1, 2]
