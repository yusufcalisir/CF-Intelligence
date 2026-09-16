"""Centralized WebSocket Connection & Graceful Broadcast Manager.

Manages active client lifecycles, capacity boundaries, tenant and room-scoped
multiplexing, asynchronous multi-client fanout with per-client timeouts,
heartbeat liveness validation, rate-limiting, and graceful dead-connection eviction.

Dual-Path In-Process Event Dispatcher (Phase 70)
-------------------------------------------------
Every room maintains a bounded ring-buffer of the last ``ROOM_HISTORY_LIMIT``
events so newly connected clients can replay history without Redis.  Background
threads (simulation engine) may call ``broadcast_to_room_sync`` which schedules
the coroutine into the running event-loop via ``run_coroutine_threadsafe``.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import WebSocket

# Maximum history events kept per room for late-joining clients (no Redis needed)
ROOM_HISTORY_LIMIT: int = 200

logger = logging.getLogger(__name__)


class WebSocketConnectionManager:
    """Manages active WebSockets with room partitioning, graceful degradation, and broadcast timeouts."""

    def __init__(
        self,
        max_connections: int = 250,
        send_timeout_seconds: float = 1.5,
        max_payload_bytes: int = 65536,
    ) -> None:
        self.max_connections = max_connections
        self.send_timeout = send_timeout_seconds
        self.max_payload_bytes = max_payload_bytes

        self._active_connections: set[WebSocket] = set()
        self._rooms: dict[str, set[WebSocket]] = {}
        self._ws_rooms: dict[WebSocket, set[str]] = {}
        self._client_last_seen: dict[WebSocket, float] = {}
        self._inbound_counters: dict[WebSocket, list[float]] = {}

        # In-process event history ring-buffers keyed by room name.
        # Allows late-joining clients to replay past events without Redis.
        self._room_history: dict[str, collections.deque[str]] = {}

        self._lock = asyncio.Lock()
        self._total_broadcast_count: int = 0
        self._dropped_client_count: int = 0
        # The running asyncio event-loop, stored on first access so background
        # threads can schedule coroutines via run_coroutine_threadsafe.
        self._event_loop: asyncio.AbstractEventLoop | None = None

    async def connect(self, websocket: WebSocket, room: str | None = None) -> bool:
        """Accepts connection if within capacity limit, registers room, else closes with 1013."""
        async with self._lock:
            if len(self._active_connections) >= self.max_connections:
                logger.warning(
                    "WebSocket capacity limit (%d) reached. Gracefully rejecting client.",
                    self.max_connections,
                )
                with contextlib.suppress(Exception):
                    await websocket.close(code=1013, reason="Server at capacity; try again later")
                return False

            await websocket.accept()
            self._active_connections.add(websocket)
            self._client_last_seen[websocket] = time.time()

            if room:
                self._rooms.setdefault(room, set()).add(websocket)
                self._ws_rooms.setdefault(websocket, set()).add(room)

            logger.debug(
                "WebSocket client connected (room=%s). Active: %d",
                room or "global",
                len(self._active_connections),
            )
            return True

    async def join_room(self, websocket: WebSocket, room: str) -> bool:
        """Subscribes an already-connected WebSocket to a specific room or tenant channel."""
        async with self._lock:
            if websocket not in self._active_connections:
                return False
            self._rooms.setdefault(room, set()).add(websocket)
            self._ws_rooms.setdefault(websocket, set()).add(room)
            self._client_last_seen[websocket] = time.time()
            return True

    async def leave_room(self, websocket: WebSocket, room: str) -> None:
        """Unsubscribes a connected WebSocket from a specific room."""
        async with self._lock:
            if room in self._rooms:
                self._rooms[room].discard(websocket)
                if not self._rooms[room]:
                    del self._rooms[room]
            if websocket in self._ws_rooms:
                self._ws_rooms[websocket].discard(room)

    async def disconnect(self, websocket: WebSocket, room: str | None = None) -> None:
        """Removes a disconnected websocket client from a specific room or completely."""
        async with self._lock:
            if room is not None:
                # Remove from specified room only
                if room in self._rooms:
                    self._rooms[room].discard(websocket)
                    if not self._rooms[room]:
                        del self._rooms[room]
                if websocket in self._ws_rooms:
                    self._ws_rooms[websocket].discard(room)
            else:
                # Full teardown of client across all rooms
                self._active_connections.discard(websocket)
                rooms = self._ws_rooms.pop(websocket, set())
                for r in rooms:
                    if r in self._rooms:
                        self._rooms[r].discard(websocket)
                        if not self._rooms[r]:
                            del self._rooms[r]
                self._client_last_seen.pop(websocket, None)
                self._inbound_counters.pop(websocket, None)

            logger.debug("WebSocket client disconnected. Active: %d", len(self._active_connections))

    def record_client_activity(self, websocket: WebSocket) -> None:
        """Records timestamp of recent client inbound frame or ping."""
        self._client_last_seen[websocket] = time.time()

    def check_inbound_rate_limit(
        self,
        websocket: WebSocket,
        max_messages: int = 60,
        window_seconds: float = 60.0,
    ) -> bool:
        """Enforces a sliding-window message rate limit on inbound WebSocket frames.

        Returns True if within rate limit, False if threshold exceeded.
        """
        now = time.time()
        timestamps = self._inbound_counters.setdefault(websocket, [])
        # Prune timestamps outside the window
        valid_timestamps = [t for t in timestamps if now - t <= window_seconds]
        if len(valid_timestamps) >= max_messages:
            self._inbound_counters[websocket] = valid_timestamps
            return False
        valid_timestamps.append(now)
        self._inbound_counters[websocket] = valid_timestamps
        self._client_last_seen[websocket] = now
        return True

    def validate_frame_size(self, payload: str | bytes) -> bool:
        """Guards against oversized inbound denial-of-service payload frames."""
        size = len(payload.encode("utf-8") if isinstance(payload, str) else payload)
        return size <= self.max_payload_bytes

    async def _fanout_send(
        self,
        clients: list[WebSocket],
        msg_str: str,
    ) -> dict[str, int]:
        """Internal helper for fanout delivery with timeout and dead client eviction."""
        if not clients:
            return {"total": 0, "delivered": 0, "dropped": 0}

        delivered = 0
        dropped_clients: list[WebSocket] = []

        async def _send_one(ws: WebSocket) -> bool:
            try:
                await asyncio.wait_for(ws.send_text(msg_str), timeout=self.send_timeout)
                return True
            except Exception:
                return False

        results = await asyncio.gather(*[_send_one(ws) for ws in clients], return_exceptions=True)

        for ws, success in zip(clients, results):
            if success is True:
                delivered += 1
                self._client_last_seen[ws] = time.time()
            else:
                dropped_clients.append(ws)

        if dropped_clients:
            async with self._lock:
                for dead_ws in dropped_clients:
                    self._active_connections.discard(dead_ws)
                    rooms = self._ws_rooms.pop(dead_ws, set())
                    for r in rooms:
                        if r in self._rooms:
                            self._rooms[r].discard(dead_ws)
                            if not self._rooms[r]:
                                del self._rooms[r]
                    self._client_last_seen.pop(dead_ws, None)
                    self._inbound_counters.pop(dead_ws, None)
                    self._dropped_client_count += 1
                    with contextlib.suppress(Exception):
                        await dead_ws.close()
            logger.warning(
                "Broadcast evicted %d stale/slow WebSocket clients. Remaining active: %d",
                len(dropped_clients),
                len(self._active_connections),
            )

        self._total_broadcast_count += 1
        return {
            "total": len(clients),
            "delivered": delivered,
            "dropped": len(dropped_clients),
        }

    async def broadcast(self, message: dict[str, Any] | str) -> dict[str, int]:
        """Broadcasts a payload to all connected clients concurrently.

        Degrades gracefully: slow or non-responsive clients timing out after
        `send_timeout` are evicted without blocking or dropping active responsive clients.
        """
        msg_str = json.dumps(message, default=str) if isinstance(message, dict) else message
        async with self._lock:
            clients = list(self._active_connections)

        return await self._fanout_send(clients, msg_str)

    async def broadcast_to_room(self, room: str, message: dict[str, Any] | str) -> dict[str, int]:
        """Broadcasts a payload exclusively to clients subscribed to the specified room or tenant.

        Also appends the serialised message to the in-process history ring-buffer
        so late-joining clients can replay past events without Redis.
        """
        msg_str = json.dumps(message, default=str) if isinstance(message, dict) else message

        # Persist to in-process history ring-buffer (thread-safe via lock)
        async with self._lock:
            buf = self._room_history.setdefault(
                room, collections.deque(maxlen=ROOM_HISTORY_LIMIT)
            )
            buf.append(msg_str)
            clients = list(self._rooms.get(room, set()))

        return await self._fanout_send(clients, msg_str)

    async def _deliver_to_room(self, room: str, msg_str: str) -> dict[str, int]:
        """Internal: fanout delivery to room clients WITHOUT writing to the ring-buffer.

        Used by broadcast_to_room_sync after it has already eagerly written to
        the ring-buffer, to avoid double-counting history entries.
        """
        async with self._lock:
            clients = list(self._rooms.get(room, set()))
        return await self._fanout_send(clients, msg_str)

    def broadcast_to_room_sync(
        self,
        room: str,
        message: dict[str, Any] | str,
    ) -> None:
        """Thread-safe fire-and-forget bridge for background threads.

        Eagerly appends to the in-process ring-buffer so the history is available
        immediately (even before the coroutine runs), then schedules fanout delivery
        to connected clients via ``_deliver_to_room`` (which does NOT write to the
        ring-buffer again, preventing double-counting).
        """
        msg_str = json.dumps(message, default=str) if isinstance(message, dict) else message

        # Eagerly update the history ring-buffer from the calling thread.
        # The deque is thread-safe for appends so no lock is needed here.
        buf = self._room_history.setdefault(room, collections.deque(maxlen=ROOM_HISTORY_LIMIT))
        buf.append(msg_str)

        loop = self._event_loop
        if loop is None or not loop.is_running():
            # No event loop available yet — history is preserved, delivery skipped.
            logger.debug(
                "broadcast_to_room_sync: no running event loop for room %s; event buffered only",
                room,
            )
            return
        # Schedule delivery only (ring-buffer already written above)
        asyncio.run_coroutine_threadsafe(
            self._deliver_to_room(room, msg_str),
            loop,
        )

    def get_room_history(self, room: str) -> list[str]:
        """Returns a snapshot of serialised past events for the given room.

        Used by WebSocket handlers to replay history to late-joining clients
        when Redis is unavailable.
        """
        buf = self._room_history.get(room)
        if buf is None:
            return []
        return list(buf)

    def register_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Register the asyncio event loop so background threads can schedule coroutines."""
        self._event_loop = loop

    async def send_heartbeat(self, ping_payload: dict[str, Any] | None = None) -> dict[str, int]:
        """Actively pings all connected sockets and evicts dead/unresponsive sockets."""
        payload = ping_payload or {"event_type": "HEARTBEAT", "timestamp": time.time()}
        return await self.broadcast(payload)

    async def evict_idle_connections(self, max_idle_seconds: float = 300.0) -> int:
        """Closes and evicts sockets that have had no activity for longer than max_idle_seconds."""
        now = time.time()
        to_evict: list[WebSocket] = []

        async with self._lock:
            for ws, last_seen in list(self._client_last_seen.items()):
                if now - last_seen > max_idle_seconds:
                    to_evict.append(ws)

        if not to_evict:
            return 0

        async with self._lock:
            for ws in to_evict:
                self._active_connections.discard(ws)
                rooms = self._ws_rooms.pop(ws, set())
                for r in rooms:
                    if r in self._rooms:
                        self._rooms[r].discard(ws)
                        if not self._rooms[r]:
                            del self._rooms[r]
                self._client_last_seen.pop(ws, None)
                self._inbound_counters.pop(ws, None)
                self._dropped_client_count += 1
                with contextlib.suppress(Exception):
                    await ws.close(code=1000, reason="Inactivity timeout")

        logger.info("Evicted %d idle WebSocket connections (idle > %.1fs)", len(to_evict), max_idle_seconds)
        return len(to_evict)

    def get_stats(self) -> dict[str, Any]:
        """Returns current connection, room distribution, and broadcast operational telemetry."""
        return {
            "active_connections": len(self._active_connections),
            "max_connections": self.max_connections,
            "total_broadcasts": self._total_broadcast_count,
            "total_evicted_clients": self._dropped_client_count,
            "active_rooms_count": len(self._rooms),
            "room_distribution": {room: len(clients) for room, clients in self._rooms.items()},
        }

    def get_room_stats(self, room: str) -> dict[str, Any]:
        """Returns connection metrics for a specific room."""
        clients_in_room = len(self._rooms.get(room, set()))
        return {
            "room": room,
            "active_connections": clients_in_room,
        }

    def list_rooms(self) -> list[str]:
        """Returns list of active rooms."""
        return sorted(self._rooms.keys())


# Global singleton manager instances
global_telemetry_ws_manager = WebSocketConnectionManager(max_connections=250, send_timeout_seconds=1.5)
training_ws_manager = WebSocketConnectionManager(max_connections=250, send_timeout_seconds=1.5)
streaming_ws_manager = WebSocketConnectionManager(max_connections=250, send_timeout_seconds=1.5)
