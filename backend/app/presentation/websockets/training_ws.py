"""WebSocket handler for real-time training progress.

Clients connect to /ws/training or /ws/training/{simulation_id} and receive
round-by-round progress updates as JSON messages.

Dual-Path In-Process Event Dispatcher (Phase 70)
-------------------------------------------------
Primary path  — Redis pub/sub:  used when Redis is available (shared connection pool).
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
from starlette.websockets import WebSocketState

from app.config import get_settings, redact_redis_url
from app.presentation.websockets.manager import training_ws_manager

logger = logging.getLogger(__name__)

router = APIRouter()

# Application-scoped async connection pool for training WebSockets
_training_redis_pool: aioredis.ConnectionPool | None = None
_pool_lock: asyncio.Lock | None = None


def _get_pool_lock() -> asyncio.Lock:
    global _pool_lock
    if _pool_lock is None:
        _pool_lock = asyncio.Lock()
    return _pool_lock


async def get_training_redis_pool(redis_url: str) -> aioredis.ConnectionPool:
    """Return an application-scoped async Redis connection pool.

    Reuses existing authenticated TLS connections across WebSocket lifecycles
    to prevent connection churn and eliminate TLS handshake latency under CPU load.
    """
    global _training_redis_pool
    if _training_redis_pool is not None:
        return _training_redis_pool

    async with _get_pool_lock():
        if _training_redis_pool is not None:
            return _training_redis_pool

        _training_redis_pool = aioredis.ConnectionPool.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=3.0,
            socket_timeout=5.0,
            max_connections=50,
            health_check_interval=30,
        )
        return _training_redis_pool


async def close_training_redis_pool() -> None:
    """Gracefully disconnect the connection pool on application shutdown."""
    global _training_redis_pool
    async with _get_pool_lock():
        if _training_redis_pool is not None:
            res = _training_redis_pool.disconnect()
            if asyncio.iscoroutine(res):
                await res
            _training_redis_pool = None


def _is_client_disconnect(websocket: WebSocket, exc: BaseException | None = None) -> bool:
    """Determine if an error or state represents client disconnect rather than infrastructure failure."""
    if getattr(websocket, "client_state", None) == WebSocketState.DISCONNECTED:
        return True
    if getattr(websocket, "application_state", None) == WebSocketState.DISCONNECTED:
        return True

    if exc is None:
        return False

    if isinstance(exc, WebSocketDisconnect):
        return True

    exc_name = type(exc).__name__.lower()
    if any(k in exc_name for k in ("disconnect", "connectionclosed", "brokenpipe")):
        return True

    if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
        return True

    msg = str(exc).lower()
    return "close message has been sent" in msg or "not connected" in msg


async def _close_pubsub(pubsub: Any) -> None:
    """Safely close pubsub object supporting both aclose() and close()."""
    with contextlib.suppress(Exception):
        if hasattr(pubsub, "aclose"):
            await pubsub.aclose()
        elif hasattr(pubsub, "close"):
            res = pubsub.close()
            if asyncio.iscoroutine(res):
                await res


async def _handle_training_ws(websocket: WebSocket, simulation_id: str = "live_prod_v2") -> None:
    """Stream training progress events to a WebSocket client.

    Attempts to use pooled Redis pub/sub first; falls back to the in-process
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

    redis_url: str | None = settings.redis_url
    if not redis_url and getattr(settings, "app_env", "development") == "development":
        redis_url = "redis://localhost:6379"

    if redis_url:
        try:
            if not redis_url.startswith(("redis://", "rediss://", "unix://")):
                redis_url = f"redis://{redis_url}"

            # Acquire lightweight client attached to shared application connection pool
            pool = await get_training_redis_pool(redis_url)
            redis_client = aioredis.Redis(connection_pool=pool)

            # Probe connectivity on pooled client with operation-appropriate timeout (2.5s)
            await asyncio.wait_for(redis_client.ping(), timeout=2.5)
            redis_available = True
            logger.debug(
                "Redis available for simulation %s via %s — using primary path",
                simulation_id,
                redact_redis_url(redis_url),
            )
        except TimeoutError:
            logger.info(
                "Redis CONNECT_TIMEOUT for simulation %s — switching to in-process event bus",
                simulation_id,
            )
            redis_client = None
        except Exception as exc:
            logger.info(
                "Redis connection failed for simulation %s (%s) — switching to in-process event bus",
                simulation_id,
                type(exc).__name__,
            )
            redis_client = None
    else:
        logger.debug(
            "Redis not configured for simulation %s — using in-process event bus",
            simulation_id,
        )

    try:
        if redis_available and redis_client is not None:
            try:
                await _stream_via_redis(websocket, simulation_id, redis_client)
            except Exception as stream_exc:
                if _is_client_disconnect(websocket, stream_exc):
                    logger.info("WebSocket disconnected for simulation %s", simulation_id)
                    return
                # Genuine Redis transport failure while socket is still alive
                logger.warning(
                    "Redis streaming failed for simulation %s (%s) — activating in-process fallback",
                    simulation_id,
                    type(stream_exc).__name__,
                )
                if not _is_client_disconnect(websocket):
                    await _stream_via_inprocess(websocket, simulation_id, room_name)
        else:
            if not _is_client_disconnect(websocket):
                await _stream_via_inprocess(websocket, simulation_id, room_name)
    except Exception as exc:
        if _is_client_disconnect(websocket, exc):
            logger.info("WebSocket disconnected for simulation %s", simulation_id)
        else:
            logger.warning(
                "Unexpected error in WebSocket handler for simulation %s: %s",
                simulation_id,
                exc,
            )
    finally:
        await training_ws_manager.disconnect(websocket, room=room_name)
        with contextlib.suppress(Exception):
            if getattr(websocket, "client_state", None) != WebSocketState.DISCONNECTED:
                await websocket.close()


async def _stream_via_redis(
    websocket: WebSocket,
    simulation_id: str,
    redis_client: aioredis.Redis,  # type: ignore[type-arg]
) -> None:
    """Primary streaming path: Redis list replay + pub/sub live events."""
    events_key = f"simulation:{simulation_id}:events"

    # Step 1: Replay past events stored in Redis list
    try:
        lrange_res: Any = redis_client.lrange(events_key, 0, -1)
        past_events = (
            await asyncio.wait_for(lrange_res, timeout=2.0)
            if _is_awaitable(lrange_res)
            else lrange_res
        )
    except TimeoutError:
        logger.warning(
            "Redis REPLAY_READ_TIMEOUT reading %s (>2.0s) — continuing to live pub/sub",
            events_key,
        )
        past_events = []
    except Exception as exc:
        logger.warning(
            "Redis replay read failed for %s (%s) — continuing to live pub/sub",
            events_key,
            type(exc).__name__,
        )
        past_events = []

    if isinstance(past_events, (list, tuple)):
        for raw_event in past_events:
            if isinstance(raw_event, str):
                if _is_client_disconnect(websocket):
                    return
                try:
                    await websocket.send_text(raw_event)
                    training_ws_manager.record_client_activity(websocket)
                except Exception as send_err:
                    if _is_client_disconnect(websocket, send_err):
                        return
                    raise

    # Step 2: Subscribe to live events channel
    pubsub = redis_client.pubsub()
    try:
        await asyncio.wait_for(pubsub.subscribe(f"training:{simulation_id}"), timeout=2.5)
    except TimeoutError:
        logger.error(
            "Redis SUBSCRIBE_TIMEOUT for channel training:%s (>2.5s)",
            simulation_id,
        )
        with contextlib.suppress(Exception):
            await _close_pubsub(pubsub)
        raise

    last_heartbeat = time.time()
    heartbeat_interval = 5.0

    try:
        while True:
            if _is_client_disconnect(websocket):
                return

            # Check for inbound frames (pings, heartbeats, validation)
            try:
                inbound = await asyncio.wait_for(websocket.receive_text(), timeout=0.05)
                training_ws_manager.record_client_activity(websocket)
                if isinstance(inbound, str):
                    if not training_ws_manager.validate_frame_size(inbound):
                        with contextlib.suppress(Exception):
                            await websocket.close(code=1009, reason="Payload too large")
                        return
                    if not training_ws_manager.check_inbound_rate_limit(websocket):
                        with contextlib.suppress(Exception):
                            await websocket.close(code=1008, reason="Rate limit exceeded")
                        return
                    if "ping" in inbound.lower():
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "event": "pong",
                                    "simulation_id": simulation_id,
                                    "timestamp": time.time(),
                                }
                            )
                        )
            except TimeoutError:
                pass  # Normal idle wait on inbound WebSocket frame
            except Exception as in_err:
                if _is_client_disconnect(websocket, in_err):
                    return
                raise

            # Read next pub/sub message (0.5s polling timeout)
            # A timeout here is normal idle wait (APPLICATION_IDLE_WAIT), NOT an infrastructure failure.
            try:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=0.5,
                )
            except TimeoutError:
                # Normal idle polling timeout — no message currently available
                message = None
            except Exception as read_exc:
                logger.warning(
                    "Redis PUBSUB_READ_ERROR on channel training:%s: %s",
                    simulation_id,
                    type(read_exc).__name__,
                )
                raise

            if isinstance(message, dict) and message.get("type") == "message":
                data = message.get("data")
                if isinstance(data, str):
                    if _is_client_disconnect(websocket):
                        return
                    try:
                        await websocket.send_text(data)
                        training_ws_manager.record_client_activity(websocket)
                    except Exception as send_err:
                        if _is_client_disconnect(websocket, send_err):
                            return
                        raise

                    try:
                        event = json.loads(data)
                        if (
                            isinstance(event, dict)
                            and event.get("event_type") in ("completed", "error")
                            and simulation_id not in ("live_prod_v2", "default", "simulation_live")
                        ):
                            logger.info(
                                "Simulation %s ended, closing WebSocket (Redis path)",
                                simulation_id,
                            )
                            return
                    except (json.JSONDecodeError, TypeError):
                        pass

            # Send periodic heartbeat to keep cloud reverse proxy connection alive
            now = time.time()
            if now - last_heartbeat >= heartbeat_interval:
                last_heartbeat = now
                if _is_client_disconnect(websocket):
                    return
                try:
                    await websocket.send_text(
                        json.dumps(
                            {
                                "event": "heartbeat",
                                "status": "streaming",
                                "mode": "redis",
                                "simulation_id": simulation_id,
                                "timestamp": now,
                            }
                        )
                    )
                except Exception as hb_err:
                    if _is_client_disconnect(websocket, hb_err):
                        return
                    raise

            await asyncio.sleep(0.01)
    finally:
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(f"training:{simulation_id}")
        await _close_pubsub(pubsub)


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
    if _is_client_disconnect(websocket):
        return

    try:
        await websocket.send_text(
            json.dumps(
                {
                    "event": "connected",
                    "status": "streaming",
                    "mode": "in_process",
                    "simulation_id": simulation_id,
                }
            )
        )
        training_ws_manager.record_client_activity(websocket)
    except Exception as send_err:
        if _is_client_disconnect(websocket, send_err):
            return
        raise

    # Step 1: Replay ring-buffer history for late-joining clients
    history = training_ws_manager.get_room_history(room_name)
    for raw_event in history:
        if _is_client_disconnect(websocket):
            return
        try:
            await websocket.send_text(raw_event)
            training_ws_manager.record_client_activity(websocket)
        except Exception:
            return  # Client disconnected during replay

    # Step 2: Keep-alive + detect terminal events forwarded by broadcast_to_room.
    last_heartbeat = time.time()
    heartbeat_interval = 5.0

    while True:
        if _is_client_disconnect(websocket):
            return

        try:
            inbound = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
            training_ws_manager.record_client_activity(websocket)
            if not training_ws_manager.validate_frame_size(inbound):
                with contextlib.suppress(Exception):
                    await websocket.close(code=1009, reason="Payload too large")
                return
            if not training_ws_manager.check_inbound_rate_limit(websocket):
                with contextlib.suppress(Exception):
                    await websocket.close(code=1008, reason="Rate limit exceeded")
                return
            if "ping" in inbound.lower():
                await websocket.send_text(
                    json.dumps(
                        {"event": "pong", "simulation_id": simulation_id, "timestamp": time.time()}
                    )
                )
        except TimeoutError:
            pass  # No inbound frame; continue keep-alive logic
        except Exception as in_err:
            if _is_client_disconnect(websocket, in_err):
                return
            raise

        # Check whether the simulation has finished by inspecting ring-buffer tail
        now = time.time()
        if now - last_heartbeat >= heartbeat_interval:
            last_heartbeat = now
            # Do NOT close persistent streams (e.g. live_prod_v2 default operations dashboard feed)
            if simulation_id not in ("live_prod_v2", "default", "simulation_live"):
                recent = training_ws_manager.get_room_history(room_name)
                for raw in reversed(recent[-10:]):
                    try:
                        evt = json.loads(raw)
                        if isinstance(evt, dict) and evt.get("event_type") in (
                            "completed",
                            "error",
                        ):
                            logger.info(
                                "Simulation %s ended, closing WebSocket (in-process path)",
                                simulation_id,
                            )
                            return
                    except (json.JSONDecodeError, TypeError):
                        pass

            if _is_client_disconnect(websocket):
                return
            try:
                await websocket.send_text(
                    json.dumps(
                        {
                            "event": "heartbeat",
                            "status": "streaming",
                            "mode": "in_process",
                            "simulation_id": simulation_id,
                            "timestamp": now,
                        }
                    )
                )
            except Exception as hb_err:
                if _is_client_disconnect(websocket, hb_err):
                    return
                raise


def _is_awaitable(obj: Any) -> bool:
    """Helper to check if an object is awaitable/coroutine."""
    return asyncio.iscoroutine(obj) or hasattr(obj, "__await__")


@router.websocket("/ws/training")
@router.websocket("/api/v1/ws/training")
@router.websocket("/v1/ws/training")
async def training_websocket_default(websocket: WebSocket) -> None:
    sim_id = (
        websocket.query_params.get("simulation_id")
        or websocket.query_params.get("simulationId")
        or "live_prod_v2"
    )
    await _handle_training_ws(websocket, sim_id)


@router.websocket("/ws/training/{simulation_id}")
@router.websocket("/api/v1/training/ws/{simulation_id}")
@router.websocket("/api/v1/ws/training/{simulation_id}")
@router.websocket("/v1/ws/training/{simulation_id}")
async def training_websocket(websocket: WebSocket, simulation_id: str) -> None:
    await _handle_training_ws(websocket, simulation_id)
