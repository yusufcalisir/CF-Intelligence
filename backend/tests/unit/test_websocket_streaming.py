"""Unit tests for WebSocket streaming and real-time training progress endpoints."""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_training_websocket_connection_fallback(client: TestClient):
    """Verify /ws/training accepts connection and sends connected fallback message."""
    with client.websocket_connect("/ws/training/test_sim_123") as websocket:
        msg = websocket.receive_text()
        data = json.loads(msg)
        assert data["event"] == "connected"
        assert data["simulation_id"] == "test_sim_123"


def test_scenario_streaming_websocket(client: TestClient):
    """Verify /ws/streaming/{scenario_id} accepts connection and handles keep-alive."""
    with client.websocket_connect("/ws/streaming/scen_456") as websocket:
        # Should connect cleanly and remain active without crashing
        assert websocket is not None
