"""WebSocket handler for real-time training progress.

Clients connect to /ws/training or /ws/training/{simulation_id} and receive
round-by-round progress updates as JSON messages.

Dual-Path In-Process Event Dispatcher (Phase 70)
-------------------------------------------------
Primary path  — Redis pub/sub:  used when Redis is available.
Fallback path — In-process bus: used when Redis is absent (e.g. HF Spaces).

In the fallback path the handler:
  1. Replays history from the manager's in-process ring-buffer.
  2. Subscribes to a per-room asyncio.Queue that the background simulation
     thread drives via ``training_ws_manager.broadcast_to_room_sync``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import get_settings
from app.presentation.websockets.manager import training_ws_manager

logger = logging.getLogger(__name__)

router = APIRouter()

# Per-room asyncio queues that the in-process fallback path listens on.
# Keyed by room name (e.g. "simulation:<id>").  Created lazily.
_room_queues: dict[str, asyncio.Queue[str]] = {}
_room_queues_lock = asyncio.Lock()


async def _get_or_create_room_queue(room: str) -> asyncio.Queue[str]:
    """Return (creating if necessary) the asyncio.Queue for *room*."""
    async with _room_queues_lock:
        if room not in _room_queues:
            _room_queues[room] = asyncio.Queue(maxsize=500)
        return _room_queues[room]


async def _handle_training_ws(websocket: WebSocket, simulation_id: str = "live_prod_v2") -> None:
    """Stream training progress events to a WebSocket client.

    Attempts to use Redis pub/sub first; falls back to the in-process
    event dispatcher transparently without dropping any events.
    """
    room_name = f"simulation:{simulation_id}"
    connected = await training_ws_manager.connect(websocket, room=room_name)
    if not connected:
        return

    # Register the running event-loop so background threads can schedule
    # broadcast_to_room coroutines via run_coroutine_threadsafe.
    try:
        loop = asyncio.get_running_loop()
        training_ws_manager.register_event_loop(loop)
    except RuntimeError:
        pass

    logger.info("WebSocket connected for simulation %s", simulation_id)

    settings = get_settings()
    redis_client = None
    redis_available = False

    try:
        redis_url: str = settings.redis_url or "redis://localhost:6379"
        if not redis_url.startswith(("redis://", "rediss://", "unix://")):
            redis_url = f"redis://{redis_url}"

        redis_client = aioredis.from_url(
            redis_url, decode_responses=True, socket_connect_timeout=2.0
        )
        # Probe connectivity with a lightweight ping
        await asyncio.wait_for(redis_client.ping(), timeout=2.0)
        redis_available = True
        logger.debug("Redis available for simulation %s — using primary path", simulation_id)
    except Exception as exc:
        logger.info(
            "Redis not available for simulation %s (%s) — switching to in-process event bus",
            simulation_id,
            type(exc).__name__,
        )
        if redis_client is not None:
            with contextlib.suppress(Exception):
                await redis_client.aclose()
            redis_client = None

    try:
        if redis_available and redis_client is not None:
            await _stream_via_redis(websocket, simulation_id, redis_client)
        else:
            await _stream_via_inprocess(websocket, simulation_id, room_name)
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected for simulation %s", simulation_id)
    except Exception as exc:
        logger.warning(
            "Unexpected error in WebSocket handler for simulation %s: %s",
            simulation_id,
            exc,
        )
    finally:
        await training_ws_manager.disconnect(websocket, room=room_name)
        if redis_client is not None:
            with contextlib.suppress(Exception):
                await redis_client.aclose()
        with contextlib.suppress(Exception):
            await websocket.close()


async def _stream_via_redis(
    websocket: WebSocket,
    simulation_id: str,
    redis_client: aioredis.Redis,  # type: ignore[type-arg]
) -> None:
    """Primary streaming path: Redis list replay + pub/sub live events."""
    events_key = f"simulation:{simulation_id}:events"

    # Replay past events stored in Redis list
    lrange_res: Any = redis_client.lrange(events_key, 0, -1)
    past_events = await lrange_res if _is_awaitable(lrange_res) else lrange_res

    if isinstance(past_events, (list, tuple)):
        for raw_event in past_events:
            if isinstance(raw_event, str):
                await websocket.send_text(raw_event)
                training_ws_manager.record_client_activity(websocket)

    # Subscribe to live events channel
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(f"training:{simulation_id}")

    while True:
        message = await pubsub.get_message(
            ignore_subscribe_messages=True,
            timeout=1.0,
        )

        if isinstance(message, dict) and message.get("type") == "message":
            data = message.get("data")
            if isinstance(data, str):
                await websocket.send_text(data)
                training_ws_manager.record_client_activity(websocket)

                try:
                    event = json.loads(data)
                    if isinstance(event, dict) and event.get("event_type") in (
                        "completed",
                        "error",
                    ):
                        logger.info(
                            "Simulation %s ended, closing WebSocket (Redis path)",
                            simulation_id,
                        )
                        return
                except (json.JSONDecodeError, TypeError):
                    pass

        await asyncio.sleep(0.05)


async def _stream_via_inprocess(
    websocket: WebSocket,
    simulation_id: str,
    room_name: str,
) -> None:
    """Fallback streaming path: in-process ring-buffer replay + queue polling.

    The background simulation thread writes events via
    ``training_ws_manager.broadcast_to_room_sync``, which:
      1. Appends to the ring-buffer (immediate, thread-safe).
      2. Schedules ``broadcast_to_room`` into the event-loop so connected
         clients receive the message directly.

    This handler therefore only needs to handle:
      • Replaying ring-buffer history on connect.
      • Detecting simulation completion to close cleanly.
      • Sending heartbeats when no activity is detected.
    """
    await websocket.send_text(
        json.dumps({
            "event": "connected",
            "status": "streaming",
            "mode": "in_process",
            "simulation_id": simulation_id,
        })
    )
    training_ws_manager.record_client_activity(websocket)

    # Step 1: Replay ring-buffer history for late-joining clients
    history = training_ws_manager.get_room_history(room_name)
    for raw_event in history:
        try:
            await websocket.send_text(raw_event)
            training_ws_manager.record_client_activity(websocket)
        except Exception:
            return  # Client disconnected during replay

    # Step 2: Keep-alive + detect terminal events forwarded by broadcast_to_room.
    # The manager's broadcast_to_room already sends to this websocket directly
    # (because it is registered in the room).  We just need to keep the coroutine
    # alive, send heartbeats, and bail out when the simulation completes.
    last_heartbeat = time.time()
    heartbeat_interval = 5.0

    while True:
        try:
            inbound = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
            training_ws_manager.record_client_activity(websocket)
            if "ping" in inbound.lower():
                await websocket.send_text(
                    json.dumps({"event": "pong", "simulation_id": simulation_id})
                )
        except TimeoutError:
            pass  # No inbound frame; continue keep-alive logic
        except WebSocketDisconnect:
            raise

        # Check whether the simulation has finished by inspecting ring-buffer tail
        now = time.time()
        if now - last_heartbeat >= heartbeat_interval:
            last_heartbeat = now
            # Scan recent history for terminal events to self-close cleanly
            recent = training_ws_manager.get_room_history(room_name)
            for raw in reversed(recent[-10:]):
                try:
                    evt = json.loads(raw)
                    if isinstance(evt, dict) and evt.get("event_type") in ("completed", "error"):
                        logger.info(
                            "Simulation %s ended, closing WebSocket (in-process path)",
                            simulation_id,
                        )
                        return
                except (json.JSONDecodeError, TypeError):
                    pass

            await websocket.send_text(
                json.dumps({
                    "event": "heartbeat",
                    "status": "streaming",
                    "mode": "in_process",
                    "simulation_id": simulation_id,
                    "timestamp": now,
                })
            )


def _is_awaitable(obj: Any) -> bool:
    """Helper to check if an object is awaitable/coroutine."""
    return asyncio.iscoroutine(obj) or hasattr(obj, "__await__")


@router.websocket("/ws/training")
async def training_websocket_default(websocket: WebSocket) -> None:
    await _handle_training_ws(websocket, "live_prod_v2")


@router.websocket("/ws/training/{simulation_id}")
@router.websocket("/api/v1/training/ws/{simulation_id}")
async def training_websocket(websocket: WebSocket, simulation_id: str) -> None:
    await _handle_training_ws(websocket, simulation_id)
