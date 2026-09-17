"""Pydantic v2 schemas for real-time WebSocket envelopes, frames, and telemetry events.

Provides typed models for:
- Standard WebSocket event envelopes and connection banners
- Client inbound frames (ping/pong, client telemetry ack)
- Streaming scenario replay events and completion signals
- Real-time training round progress and metric convergence frames
- Platform-wide live fraud telemetry and transaction alerts
- WebSocket error frames (rate limit exceeded, payload too large)
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class WebSocketBaseEnvelope(BaseModel):
    """Base schema for structured WebSocket messages."""

    event_type: str = Field(..., description="Unique event identifier (e.g. CONNECTED, ALERT_TRIGGERED, PONG)")
    timestamp: float = Field(..., description="Unix epoch timestamp in seconds of event emission")
    payload: dict[str, Any] = Field(default_factory=dict, description="Event-specific data payload")


class WebSocketConnectPayload(BaseModel):
    """Initial connection greeting payload."""

    status: str = Field(default="ONLINE", description="Server operational status")
    engine: str = Field(default="FastAPI Bi-Directional Stream", description="Underlying streaming engine")
    active_banks: list[str] = Field(
        default_factory=lambda: ["bank_alpha", "bank_beta", "bank_gamma"],
        description="Active consortium banks streaming telemetry",
    )


class WebSocketConnectBanner(BaseModel):
    """Initial greeting frame sent upon successful WebSocket handshake."""

    event_type: Literal["CONNECTED"] = "CONNECTED"
    timestamp: float = Field(..., description="Unix epoch timestamp")
    payload: WebSocketConnectPayload = Field(default_factory=WebSocketConnectPayload)


class WebSocketPongResponse(BaseModel):
    """Response frame emitted upon receipt of client ping."""

    event_type: Literal["PONG"] = "PONG"
    timestamp: float = Field(..., description="Unix epoch timestamp")
    simulation_id: str | None = Field(default=None, description="Optional simulation identifier")


class WebSocketInProcessConnectBanner(BaseModel):
    """Fallback greeting frame for in-process training stream."""

    event: Literal["connected"] = "connected"
    status: Literal["streaming"] = "streaming"
    mode: Literal["in_process", "redis"] = "in_process"
    simulation_id: str = Field(..., description="Simulation identifier")


class WebSocketHeartbeatFrame(BaseModel):
    """Periodic keep-alive heartbeat frame for in-process training streams."""

    event: Literal["heartbeat"] = "heartbeat"
    status: Literal["streaming"] = "streaming"
    mode: Literal["in_process", "redis"] = "in_process"
    simulation_id: str = Field(..., description="Simulation identifier")
    timestamp: float = Field(..., description="Unix epoch timestamp")


class WebSocketErrorFrame(BaseModel):
    """Structured error frame emitted prior to terminal close."""

    event_type: Literal["ERROR", "RATE_LIMIT_EXCEEDED", "PAYLOAD_TOO_LARGE"] = "ERROR"
    code: int = Field(..., description="WebSocket closure or error code (e.g. 1008, 1009, 1013)")
    message: str = Field(..., description="Human-readable error description")
    timestamp: float = Field(..., description="Unix epoch timestamp")


class LiveStreamTransactionPayload(BaseModel):
    """Payload schema for live scored transaction and fraud alert frames."""

    transaction_id: str = Field(..., description="Unique transaction identifier")
    bank_id: str = Field(..., description="Source institution identifier")
    risk_score: int = Field(..., ge=0, le=1000, description="Risk score calibrated from 0 to 1000")
    severity: Literal["low", "medium", "high", "critical", "info"] = Field(..., description="Alert severity level")
    typology: str = Field(..., description="Fraud typology code (e.g. RAPID_CROSS_BANK_LAYERING)")
    description: str = Field(..., description="Detailed typology explanation")
    amount: float = Field(..., ge=0.0, description="Transaction monetary amount")
    currency: str = Field(default="EUR", description="ISO 4217 currency code")
    created_at: str = Field(..., description="ISO 8601 creation timestamp")


class LiveTelemetryEvent(BaseModel):
    """Full message envelope for live platform telemetry stream."""

    event_type: Literal["ALERT_TRIGGERED", "TRANSACTION_SCORED", "CONNECTED", "PONG"] = "TRANSACTION_SCORED"
    timestamp: float = Field(..., description="Unix epoch timestamp")
    payload: LiveStreamTransactionPayload | WebSocketConnectPayload | dict[str, Any] = Field(
        ..., description="Nested telemetry payload"
    )


class ScenarioStreamingProgressPayload(BaseModel):
    """Payload for scenario replay progress events."""

    delivered: int = Field(..., ge=0, description="Number of delivered events so far")
    total: int = Field(..., ge=0, description="Total events in scenario")
    status: str = Field(..., description="Current scenario replay status")


class ScenarioStreamingEvent(BaseModel):
    """Message envelope for scenario streaming replay."""

    event_id: str | None = Field(default=None, description="Scenario event ID")
    event_type: str = Field(..., description="Event type (progress, scenario_complete, transaction, alert)")
    bank_id: str | None = Field(default=None, description="Associated bank identifier")
    timestamp: str | float | None = Field(default=None, description="Event emission timestamp")
    payload: dict[str, Any] = Field(default_factory=dict, description="Scenario event details")
    sequence: int | None = Field(default=None, description="Event sequence index")
    total: int | None = Field(default=None, description="Total events in scenario")
    scenario_id: str | None = Field(default=None, description="Scenario identifier")


class TrainingRoundProgressPayload(BaseModel):
    """Payload for federated training round progress updates."""

    simulation_id: str = Field(..., description="Federated simulation ID")
    round_number: int = Field(..., ge=0, description="Current training round")
    total_rounds: int = Field(..., ge=1, description="Target total rounds")
    loss: float = Field(..., description="Global cross-entropy loss")
    auc: float | None = Field(default=None, ge=0.0, le=1.0, description="Global ROC-AUC score")
    participating_banks: list[str] = Field(default_factory=list, description="Banks submitting gradients this round")
    per_bank_auc: dict[str, float] = Field(default_factory=dict, description="Per-bank local validation ROC-AUC")
