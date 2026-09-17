"""Unit tests for WebSocket streaming, telemetry, and training routes.

Verifies:
- Route Discovery: canonical /ws/... and versioned /api/v1/ws/..., /v1/ws/... route aliases
- Protocol Handshake: structured connection banners with online engine and active banks
- Bidirectional Keep-Alive: text and JSON ping-pong exchanges
- Security Invariants: oversized payload rejection (code 1009) and sliding-window rate limit (code 1008)
- Dual-Path In-Process Resilience: ring-buffer replay for late-joining scenario and training consumers
- Operational Telemetry: connection manager diagnostics and active room tracking
"""

from __future__ import annotations

import json
import time

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app.presentation.websockets.manager import (
    broadcast_telemetry_event,
    global_telemetry_ws_manager,
    streaming_ws_manager,
    training_ws_manager,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ── Route Discovery & Connection Handshake ─────────────────────────────────


def test_telemetry_websocket_route_aliases(client: TestClient):
    """Verify /ws/telemetry and all versioned /api/v1/ws/telemetry aliases accept connections."""
    for path in ["/ws/telemetry", "/api/v1/ws/telemetry", "/v1/ws/telemetry"]:
        with client.websocket_connect(path) as ws:
            raw = ws.receive_text()
            data = json.loads(raw)
            assert data["event_type"] == "CONNECTED"
            assert data["payload"]["status"] == "ONLINE"
            assert "active_banks" in data["payload"]


def test_training_websocket_route_aliases(client: TestClient):
    """Verify /ws/training and parameterized aliases connect cleanly."""
    paths = [
        "/ws/training",
        "/api/v1/ws/training",
        "/v1/ws/training",
        "/ws/training/sim_test_01",
        "/api/v1/training/ws/sim_test_01",
        "/api/v1/ws/training/sim_test_01",
        "/v1/ws/training/sim_test_01",
    ]
    for path in paths:
        with client.websocket_connect(path) as ws:
            raw = ws.receive_text()
            data = json.loads(raw)
            assert data["event"] == "connected"
            assert data["mode"] in ("in_process", "redis")


def test_scenario_streaming_route_aliases(client: TestClient):
    """Verify scenario streaming accepts connection on all canonical and versioned aliases."""
    paths = [
        "/ws/streaming/scen_test_99",
        "/ws/scenarios/scen_test_99",
        "/api/v1/ws/streaming/scen_test_99",
        "/v1/ws/streaming/scen_test_99",
        "/api/v1/ws/scenarios/scen_test_99",
        "/v1/ws/scenarios/scen_test_99",
        "/ws/streaming",
        "/ws/scenarios",
    ]
    for path in paths:
        with client.websocket_connect(path) as ws:
            assert ws is not None


# ── Ping-Pong Keepalive ───────────────────────────────────────────────────


def test_telemetry_ping_pong_text_exchange(client: TestClient):
    """Verify telemetry socket responds with PONG to text ping."""
    with client.websocket_connect("/ws/telemetry") as ws:
        # Initial greeting
        ws.receive_text()

        # Send text ping
        ws.send_text("PING")
        pong_raw = ws.receive_text()
        pong = json.loads(pong_raw)
        assert pong["event_type"] == "PONG"
        assert "timestamp" in pong


def test_training_ping_pong_exchange(client: TestClient):
    """Verify training socket responds with PONG and simulation identifier."""
    with client.websocket_connect("/ws/training/sim_ping_check") as ws:
        # Initial greeting
        ws.receive_text()

        # Send ping
        ws.send_text("ping")
        pong_raw = ws.receive_text()
        pong = json.loads(pong_raw)
        assert pong["event"] == "pong"
        assert pong["simulation_id"] == "sim_ping_check"


# ── Inbound Frame Validation & Rate Limiting ───────────────────────────────


def test_telemetry_rejects_oversized_payload(client: TestClient):
    """Verify oversized payload triggers immediate closure with code 1009."""
    with client.websocket_connect("/ws/telemetry") as ws:
        ws.receive_text()  # Consume initial connected banner

        # Send payload exceeding max_payload_bytes (default 65536)
        oversized = "A" * (65536 + 1024)
        ws.send_text(oversized)

        # Connection must be closed with 1009
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_text()
        assert exc_info.value.code == 1009


def test_telemetry_sliding_rate_limit_enforcement(client: TestClient):
    """Verify rapid message flooding beyond rate limit threshold closes connection with code 1008."""
    # Temporarily tighten rate limit for testing
    orig_check = global_telemetry_ws_manager.check_inbound_rate_limit

    def mock_check(websocket):
        # Allow 2 messages, then fail
        count = getattr(mock_check, "count", 0)
        mock_check.count = count + 1
        return count < 2

    global_telemetry_ws_manager.check_inbound_rate_limit = mock_check

    try:
        with client.websocket_connect("/ws/telemetry") as ws:
            ws.receive_text()  # Initial greeting

            # First message ok
            ws.send_text("ping")

            # Second message ok
            ws.send_text("ping")

            # Third message triggers rate limit violation (mock_check returns False)
            ws.send_text("ping")

            with pytest.raises(WebSocketDisconnect) as exc_info:
                for _ in range(10):
                    ws.receive_text()
            assert exc_info.value.code == 1008
    finally:
        global_telemetry_ws_manager.check_inbound_rate_limit = orig_check


# ── Dual-Path In-Process Resilience & Ring-Buffer Replay ──────────────────


def test_scenario_streaming_ring_buffer_replay_to_late_joiner(client: TestClient):
    """Late-joining clients must receive buffered historical scenario events upon connecting."""
    scenario_id = "scen_late_join_replay"
    room = f"scenario:{scenario_id}"

    # Buffer 2 events before client connects
    event_1 = {"event_id": "e1", "event_type": "transaction", "amount": 100.0}
    event_2 = {"event_id": "e2", "event_type": "alert", "severity": "HIGH"}
    streaming_ws_manager.broadcast_to_room_sync(room, event_1)
    streaming_ws_manager.broadcast_to_room_sync(room, event_2)

    with client.websocket_connect(f"/ws/streaming/{scenario_id}") as ws:
        msg1 = json.loads(ws.receive_text())
        assert msg1["event_id"] == "e1"
        msg2 = json.loads(ws.receive_text())
        assert msg2["event_id"] == "e2"


def test_broadcast_telemetry_event_convenience_helper():
    """Verify broadcast_telemetry_event buffers message in global telemetry manager."""
    event = {
        "event_type": "ALERT_TRIGGERED",
        "timestamp": time.time(),
        "payload": {
            "transaction_id": "txn_broadcast_helper_01",
            "bank_id": "bank_beta",
            "risk_score": 920,
            "severity": "critical",
            "typology": "TEST_TYPOLOGY",
            "description": "Integration test event",
            "amount": 50000.0,
            "currency": "EUR",
            "created_at": "2026-09-17T12:00:00Z",
        },
    }
    broadcast_telemetry_event(event)
    history = global_telemetry_ws_manager.get_room_history("telemetry:global")
    assert len(history) >= 1
    last_item = json.loads(history[-1])
    assert last_item["event_type"] == "ALERT_TRIGGERED"
    assert last_item["payload"]["transaction_id"] == "txn_broadcast_helper_01"


# ── Manager Health & Diagnostics ──────────────────────────────────────────


def test_connection_manager_diagnostics():
    """Verify get_manager_health and stats return valid operational metrics."""
    health = global_telemetry_ws_manager.get_manager_health()
    assert health["status"] == "HEALTHY"
    assert "active_connections" in health
    assert "max_connections" in health
    assert "send_timeout_seconds" in health
    assert "max_payload_bytes" in health

    stats = training_ws_manager.get_stats()
    assert "total_broadcasts" in stats
    assert "total_evicted_clients" in stats
    assert "active_rooms_count" in stats
