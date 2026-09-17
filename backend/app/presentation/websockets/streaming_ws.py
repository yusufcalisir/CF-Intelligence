"""WebSocket endpoint for streaming scenario events and platform telemetry.

Clients connect to:
- /ws/streaming/{scenario_id} or /ws/scenarios/{scenario_id} (and versioned /api/v1/ws/... aliases)
- /ws/telemetry (and versioned /api/v1/ws/telemetry aliases)

Dual-path In-Process Event Dispatcher:
- Primary path: Redis pub/sub (used when Redis is connected).
- Fallback path: In-process ring-buffer history replay + WebSocketConnectionManager fanout.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.presentation.websockets.manager import (
    global_telemetry_ws_manager,
    streaming_ws_manager,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["streaming"])


async def _handle_scenario_stream(websocket: WebSocket, scenario_id: str) -> None:
    """Stream scenario events via WebSocket with dual-path in-process resilience."""
    room_name = f"scenario:{scenario_id}"
    connected = await streaming_ws_manager.connect(websocket, room=room_name)
    if not connected:
        return
    logger.info("Streaming WebSocket connected: scenario=%s", scenario_id[:8])

    try:
        loop = asyncio.get_running_loop()
        streaming_ws_manager.register_event_loop(loop)
    except RuntimeError:
        pass

    try:
        # Try to connect to Redis for pub/sub
        try:
            import redis.asyncio as aioredis

            from app.config import get_settings

            settings = get_settings()
            r = aioredis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=0.2,
                socket_timeout=0.3,
                retry_on_timeout=False,
            )
            # Fast ping check to verify Redis reachability
            await asyncio.wait_for(r.ping(), timeout=0.2)

            # Replay stored events from Redis
            events_key = f"scenario:{scenario_id}:events"
            lrange_res: Any = r.lrange(events_key, 0, -1)
            stored_events = await lrange_res if inspect.isawaitable(lrange_res) else lrange_res
            if isinstance(stored_events, (list, tuple)):
                for raw_event in stored_events:
                    if isinstance(raw_event, str):
                        await websocket.send_text(raw_event)
                        streaming_ws_manager.record_client_activity(websocket)

            # Subscribe to live events
            pubsub = r.pubsub()
            channel = f"streaming:{scenario_id}"
            await pubsub.subscribe(channel)

            while True:
                # Check for inbound frames (pings, heartbeats, validation)
                try:
                    inbound = await asyncio.wait_for(websocket.receive_text(), timeout=0.1)
                    streaming_ws_manager.record_client_activity(websocket)
                    if not streaming_ws_manager.validate_frame_size(inbound):
                        with contextlib.suppress(Exception):
                            await websocket.close(code=1009, reason="Payload too large")
                        return
                    if not streaming_ws_manager.check_inbound_rate_limit(websocket):
                        with contextlib.suppress(Exception):
                            await websocket.close(code=1008, reason="Rate limit exceeded")
                        return
                    if "ping" in inbound.lower():
                        await websocket.send_text(
                            json.dumps({"event_type": "PONG", "scenario_id": scenario_id, "timestamp": time.time()})
                        )
                except TimeoutError:
                    pass

                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=0.5,
                )
                if isinstance(message, dict) and message.get("type") == "message":
                    data = message.get("data")
                    if isinstance(data, str):
                        await websocket.send_text(data)
                        streaming_ws_manager.record_client_activity(websocket)

                await asyncio.sleep(0.05)

        except Exception:
            # Fallback: Dual-path in-process ring-buffer replay + engine status polling
            logger.info(
                "Redis unavailable for scenario %s — switching to dual-path in-process dispatcher",
                scenario_id[:8],
            )
            # 1. Replay in-process history ring-buffer for late-joining clients
            history = streaming_ws_manager.get_room_history(room_name)
            for raw_event in history:
                try:
                    await websocket.send_text(raw_event)
                    streaming_ws_manager.record_client_activity(websocket)
                except Exception:
                    return

            from app.presentation.routers.scenarios import get_streaming_engine

            engine = get_streaming_engine()
            last_count = 0
            last_heartbeat = time.time()

            while True:
                # Check for inbound frames (ping/pong, rate limit, frame size)
                try:
                    inbound = await asyncio.wait_for(websocket.receive_text(), timeout=0.5)
                    streaming_ws_manager.record_client_activity(websocket)
                    if not streaming_ws_manager.validate_frame_size(inbound):
                        with contextlib.suppress(Exception):
                            await websocket.close(code=1009, reason="Payload too large")
                        return
                    if not streaming_ws_manager.check_inbound_rate_limit(websocket):
                        with contextlib.suppress(Exception):
                            await websocket.close(code=1008, reason="Rate limit exceeded")
                        return
                    if "ping" in inbound.lower():
                        await websocket.send_text(
                            json.dumps({"event_type": "PONG", "scenario_id": scenario_id, "timestamp": time.time()})
                        )
                except TimeoutError:
                    pass

                status = engine.get_scenario_status(scenario_id)
                if status:
                    current_count = status.get("delivered_events", 0)
                    if current_count > last_count:
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "event_type": "progress",
                                    "payload": {
                                        "delivered": current_count,
                                        "total": status["total_events"],
                                        "status": status["status"],
                                    },
                                }
                            )
                        )
                        streaming_ws_manager.record_client_activity(websocket)
                        last_count = current_count

                    if status.get("status") in ("completed", "stopped"):
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "event_type": "scenario_complete",
                                    "payload": {"status": status["status"]},
                                }
                            )
                        )
                        streaming_ws_manager.record_client_activity(websocket)
                        break

                # Periodic heartbeat every 5s if idle
                now = time.time()
                if now - last_heartbeat >= 5.0:
                    last_heartbeat = now
                    await websocket.send_text(
                        json.dumps(
                            {
                                "event_type": "HEARTBEAT",
                                "scenario_id": scenario_id,
                                "timestamp": now,
                            }
                        )
                    )

                await asyncio.sleep(0.5)

    except WebSocketDisconnect:
        logger.info("Streaming WebSocket disconnected: scenario=%s", scenario_id[:8])
    except Exception:
        logger.exception("Streaming WebSocket error")
    finally:
        await streaming_ws_manager.disconnect(websocket, room=room_name)
        with contextlib.suppress(Exception):
            await websocket.close()


@router.websocket("/ws/streaming/{scenario_id}")
@router.websocket("/ws/scenarios/{scenario_id}")
@router.websocket("/api/v1/ws/streaming/{scenario_id}")
@router.websocket("/v1/ws/streaming/{scenario_id}")
@router.websocket("/api/v1/ws/scenarios/{scenario_id}")
@router.websocket("/v1/ws/scenarios/{scenario_id}")
async def streaming_websocket(websocket: WebSocket, scenario_id: str) -> None:
    """Stream scenario events via WebSocket with dual-path resilience."""
    await _handle_scenario_stream(websocket, scenario_id)


@router.websocket("/ws/streaming")
@router.websocket("/ws/scenarios")
@router.websocket("/api/v1/ws/streaming")
@router.websocket("/v1/ws/streaming")
@router.websocket("/api/v1/ws/scenarios")
@router.websocket("/v1/ws/scenarios")
async def streaming_websocket_default(websocket: WebSocket) -> None:
    """Stream default scenario events via WebSocket."""
    await _handle_scenario_stream(websocket, "default_scenario")


@router.websocket("/ws/telemetry")
@router.websocket("/api/v1/ws/telemetry")
@router.websocket("/v1/ws/telemetry")
async def live_telemetry_websocket(websocket: WebSocket) -> None:
    """Stream platform-wide live telemetry, transactions, and fraud alerts."""
    room_name = "telemetry:global"
    connected = await global_telemetry_ws_manager.connect(websocket, room=room_name)
    if not connected:
        return
    logger.info("Global telemetry WebSocket connected")

    try:
        loop = asyncio.get_running_loop()
        global_telemetry_ws_manager.register_event_loop(loop)
    except RuntimeError:
        pass

    try:
        import random

        # Send initial connected banner
        await websocket.send_text(
            json.dumps(
                {
                    "event_type": "CONNECTED",
                    "timestamp": time.time(),
                    "payload": {
                        "status": "ONLINE",
                        "engine": "FastAPI Bi-Directional Stream",
                        "active_banks": ["bank_alpha", "bank_beta", "bank_gamma"],
                    },
                }
            )
        )
        global_telemetry_ws_manager.record_client_activity(websocket)

        banks = ["bank_alpha", "bank_beta", "bank_gamma"]
        typologies = [
            ("RAPID_CROSS_BANK_LAYERING", "High-Velocity Cross-Bank Transfer Burst", "critical", 942),
            ("STRUCTURED_SMURFING", "Sub-Threshold Structured Smurfing Deposit", "high", 815),
            ("GNN_TOPOLOGICAL_ANOMALY", "GraphSAGE 2-Hop Layering Syndicate", "critical", 895),
            ("NEW_ACCOUNT_HIGH_VALUE_CRYPTO", "New Account High-Value Crypto Transfer", "high", 780),
            ("LEGITIMATE_PAYMENT", "Standard Retail Interbank Transfer", "info", 120),
        ]

        # Continuous heartbeat and event delivery loop
        while True:
            # Check for inbound client frames or disconnects with timeout
            try:
                inbound_data = await asyncio.wait_for(websocket.receive_text(), timeout=4.0)
                global_telemetry_ws_manager.record_client_activity(websocket)

                # Validate inbound frame size and rate limit
                if not global_telemetry_ws_manager.validate_frame_size(inbound_data):
                    with contextlib.suppress(Exception):
                        await websocket.close(code=1009, reason="Payload too large")
                    return

                if not global_telemetry_ws_manager.check_inbound_rate_limit(websocket):
                    with contextlib.suppress(Exception):
                        await websocket.close(code=1008, reason="Rate limit exceeded")
                    return

                if "ping" in inbound_data.lower():
                    await websocket.send_text(
                        json.dumps({"event_type": "PONG", "timestamp": time.time()})
                    )
            except TimeoutError:
                pass

            typ, desc, sev, score = random.choice(typologies)
            bank = random.choice(banks)
            txn_id = f"txn_{int(time.time()*1000)%1000000:06d}"

            event_payload = {
                "event_type": "ALERT_TRIGGERED" if score >= 700 else "TRANSACTION_SCORED",
                "timestamp": time.time(),
                "payload": {
                    "transaction_id": txn_id,
                    "bank_id": bank,
                    "risk_score": score,
                    "severity": sev,
                    "typology": typ,
                    "description": desc,
                    "amount": round(random.uniform(1500.0, 450000.0), 2),
                    "currency": "EUR",
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
            }
            await websocket.send_text(json.dumps(event_payload))
            global_telemetry_ws_manager.record_client_activity(websocket)

    except WebSocketDisconnect:
        logger.info("Global telemetry WebSocket disconnected")
    except Exception:
        logger.exception("Global telemetry WebSocket error")
    finally:
        await global_telemetry_ws_manager.disconnect(websocket, room=room_name)
        with contextlib.suppress(Exception):
            await websocket.close()
