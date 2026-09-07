"""Centralized WebSocket Connection & Graceful Broadcast Manager.

Manages active client lifecycles, capacity boundaries, asynchronous multi-client
fanout with per-client timeouts, and graceful dead-connection eviction.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketConnectionManager:
    """Manages active WebSockets with graceful degradation and broadcast timeouts."""

    def __init__(self, max_connections: int = 250, send_timeout_seconds: float = 1.5) -> None:
        self.max_connections = max_connections
        self.send_timeout = send_timeout_seconds
        self._active_connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._total_broadcast_count: int = 0
        self._dropped_client_count: int = 0

    async def connect(self, websocket: WebSocket) -> bool:
        """Accepts connection if within capacity limit, else closes with 1013."""
        async with self._lock:
            if len(self._active_connections) >= self.max_connections:
                logger.warning(
                    "WebSocket capacity limit (%d) reached. Gracefully rejecting client.",
                    self.max_connections,
                )
                await websocket.close(code=1013, reason="Server at capacity; try again later")
                return False

            await websocket.accept()
            self._active_connections.add(websocket)
            logger.debug("WebSocket client connected. Active: %d", len(self._active_connections))
            return True

    async def disconnect(self, websocket: WebSocket) -> None:
        """Removes a disconnected websocket client."""
        async with self._lock:
            self._active_connections.discard(websocket)
            logger.debug("WebSocket client disconnected. Active: %d", len(self._active_connections))

    async def broadcast(self, message: dict[str, Any] | str) -> dict[str, int]:
        """Broadcasts a payload to all connected clients concurrently.

        Degrades gracefully: slow or non-responsive clients timing out after
        `send_timeout` are evicted without blocking or dropping active responsive clients.
        """
        msg_str = json.dumps(message) if isinstance(message, dict) else str(message)
        async with self._lock:
            clients = list(self._active_connections)

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
            else:
                dropped_clients.append(ws)

        if dropped_clients:
            async with self._lock:
                for dead_ws in dropped_clients:
                    self._active_connections.discard(dead_ws)
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

    def get_stats(self) -> dict[str, Any]:
        """Returns current connection and broadcast operational telemetry."""
        return {
            "active_connections": len(self._active_connections),
            "max_connections": self.max_connections,
            "total_broadcasts": self._total_broadcast_count,
            "total_evicted_clients": self._dropped_client_count,
        }


# Global singleton manager instances
global_telemetry_ws_manager = WebSocketConnectionManager(max_connections=250, send_timeout_seconds=1.5)
training_ws_manager = WebSocketConnectionManager(max_connections=250, send_timeout_seconds=1.5)
streaming_ws_manager = WebSocketConnectionManager(max_connections=250, send_timeout_seconds=1.5)
