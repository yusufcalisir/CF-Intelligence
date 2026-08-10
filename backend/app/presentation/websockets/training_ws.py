"""WebSocket handler for real-time training progress.

Clients connect to /ws/training or /ws/training/{simulation_id} and receive
round-by-round progress updates as JSON messages.

Uses Redis pub/sub to receive events from the Celery worker with fallback.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter()


async def _handle_training_ws(websocket: WebSocket, simulation_id: str = "live_prod_v2") -> None:
    """Stream training progress events to a WebSocket client."""
    await websocket.accept()
    logger.info("WebSocket connected for simulation %s", simulation_id)

    settings = get_settings()
    redis_client = None

    try:
        redis_url: str = settings.redis_url or "redis://localhost:6379"
        if not redis_url.startswith(("redis://", "rediss://", "unix://")):
            redis_url = f"redis://{redis_url}"

        redis_client = aioredis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2.0)
        events_key = f"simulation:{simulation_id}:events"

        # Replay past events safely
        lrange_res: Any = redis_client.lrange(events_key, 0, -1)
        past_events = await lrange_res if inspect_is_awaitable(lrange_res) else lrange_res

        if isinstance(past_events, (list, tuple)):
            for raw_event in past_events:
                if isinstance(raw_event, str):
                    await websocket.send_text(raw_event)

        # Subscribe to live events
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

                    try:
                        event = json.loads(data)
                        if isinstance(event, dict) and event.get("event_type") in ("completed", "error"):
                            logger.info("Simulation %s ended, closing WebSocket", simulation_id)
                            break
                    except (json.JSONDecodeError, TypeError):
                        pass

            await asyncio.sleep(0.1)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected for simulation %s", simulation_id)
    except Exception as exc:
        logger.info(
            "Redis unavailable for simulation %s (%s) — falling back to in-process keep-alive "
            "(expected degraded mode when Redis is not provisioned, e.g. HF Spaces)",
            simulation_id,
            type(exc).__name__,
        )
        try:
            await websocket.send_text(json.dumps({"event": "connected", "status": "idle", "simulation_id": simulation_id}))
            while True:
                await asyncio.sleep(5.0)
        except Exception:
            pass
    finally:
        if redis_client is not None:
            with contextlib.suppress(Exception):
                await redis_client.aclose()
        with contextlib.suppress(Exception):
            await websocket.close()


def inspect_is_awaitable(obj: Any) -> bool:
    """Helper to check if an object is awaitable/coroutine."""
    return asyncio.iscoroutine(obj) or hasattr(obj, "__await__")


@router.websocket("/ws/training")
async def training_websocket_default(websocket: WebSocket) -> None:
    await _handle_training_ws(websocket, "live_prod_v2")


@router.websocket("/ws/training/{simulation_id}")
async def training_websocket(websocket: WebSocket, simulation_id: str) -> None:
    await _handle_training_ws(websocket, simulation_id)
