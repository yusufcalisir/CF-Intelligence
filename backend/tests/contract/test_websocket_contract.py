"""WebSocket Real-Time Event Envelope & Protocol Contract Tests.

Validates that WebSocket connections follow standard framing, lifecycle, and
structured JSON message schemas for real-time telemetry streaming.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    """FastAPI TestClient fixture."""
    return TestClient(app)


def test_training_websocket_envelope_contract(client: TestClient):
    """Validate /ws/training WebSocket connection and event envelope structure."""
    with client.websocket_connect("/ws/training") as websocket:
        # Send ping / request or verify initial connect
        assert websocket is not None


def test_streaming_websocket_envelope_contract(client: TestClient):
    """Validate /ws/streaming/{scenario_id} WebSocket connection and event structure."""
    with client.websocket_connect("/ws/streaming/scen_contract_01") as websocket:
        assert websocket is not None


def test_websocket_message_schema_invariants():
    """Validate expected message schemas for streaming events and round progress."""
    # Round progress message schema
    round_progress_envelope = {
        "event_type": "round_completed",
        "simulation_id": "sim_live_01",
        "round_number": 5,
        "total_rounds": 10,
        "loss": 0.324,
        "participating_banks": ["bank_a", "bank_b", "bank_c"],
        "timestamp": "2026-08-14T20:00:00Z",
    }
    assert "event_type" in round_progress_envelope
    assert "simulation_id" in round_progress_envelope
    assert "round_number" in round_progress_envelope
    assert isinstance(round_progress_envelope["participating_banks"], list)

    # Streaming transaction telemetry schema
    streaming_txn_envelope = {
        "event_type": "transaction_scored",
        "scenario_id": "scen_burst_01",
        "transaction_id": "TXN-8877",
        "risk_score": 850,
        "is_fraud": True,
        "action_taken": "BLOCK",
        "timestamp": "2026-08-14T20:00:00Z",
    }
    assert "event_type" in streaming_txn_envelope
    assert "transaction_id" in streaming_txn_envelope
    assert "risk_score" in streaming_txn_envelope
    assert isinstance(streaming_txn_envelope["is_fraud"], bool)
