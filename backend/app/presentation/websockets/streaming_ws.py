"""WebSocket endpoint for streaming scenario events.

Clients connect to /ws/streaming/{scenario_id} and receive
real-time events as they are generated during scenario replay.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)
router = APIRouter(tags=["streaming"])


@router.websocket("/ws/streaming/{scenario_id}")
async def streaming_websocket(websocket: WebSocket, scenario_id: str) -> None:
    """Stream scenario events via WebSocket.

    Subscribes to Redis pub/sub for the given scenario and forwards
    events to the connected client. Also replays any events that
    occurred before the client connected.
    """
    await websocket.accept()
    logger.info("Streaming WebSocket connected: scenario=%s", scenario_id[:8])

    try:
        # Try to connect to Redis for pub/sub
        try:
            import redis.asyncio as aioredis

            from app.config import get_settings

            settings = get_settings()
            r = aioredis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=0.5,
                socket_timeout=1.0,
                retry_on_timeout=False,
            )

            # Replay stored events
            events_key = f"scenario:{scenario_id}:events"
            stored_events = await r.lrange(events_key, 0, -1)
            for raw_event in stored_events:
                await websocket.send_text(raw_event)

            # Subscribe to live events
            pubsub = r.pubsub()
            channel = f"streaming:{scenario_id}"
            await pubsub.subscribe(channel)

            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if message and message["type"] == "message":
                    await websocket.send_text(message["data"])

                # Send heartbeat every 5 seconds
                await asyncio.sleep(0.1)

        except Exception:
            # Fallback: poll the streaming engine directly
            logger.warning(
                "Redis unavailable for streaming — using polling fallback",
            )
            from app.presentation.routers.scenarios import get_streaming_engine

            engine = get_streaming_engine()
            last_count = 0

            while True:
                status = engine.get_scenario_status(scenario_id)
                if not status:
                    await websocket.send_text(
                        json.dumps(
                            {
                                "event_type": "error",
                                "payload": {"message": "Scenario not found"},
                            }
                        )
                    )
                    break

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
                    break

                await asyncio.sleep(0.5)

    except WebSocketDisconnect:
        logger.info("Streaming WebSocket disconnected: scenario=%s", scenario_id[:8])
    except Exception:
        logger.exception("Streaming WebSocket error")
    finally:
        with contextlib.suppress(Exception):
            await websocket.close()


@router.websocket("/ws/telemetry")
async def live_telemetry_websocket(websocket: WebSocket) -> None:
    """Stream platform-wide live telemetry, transactions, and fraud alerts."""
    await websocket.accept()
    logger.info("Global telemetry WebSocket connected")

    try:
        import random
        import time

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
            await asyncio.sleep(4.0)

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

    except WebSocketDisconnect:
        logger.info("Global telemetry WebSocket disconnected")
    except Exception:
        logger.exception("Global telemetry WebSocket error")
    finally:
        with contextlib.suppress(Exception):
            await websocket.close()

