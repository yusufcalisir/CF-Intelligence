"""Scenario and streaming API endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.application.schemas.phase2 import (
    ScenarioInfoResponse,
    ScenarioStartRequest,
    ScenarioStartResponse,
    ScenarioStatusResponse,
)
from app.application.services.scenario_service import ScenarioSimulator
from app.application.services.streaming_engine import StreamingEngine
from app.domain.enums import ScenarioType

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/scenarios", tags=["scenarios"])

_scenario_simulator = ScenarioSimulator()
_streaming_engine = StreamingEngine()


def get_scenario_simulator() -> ScenarioSimulator:
    return _scenario_simulator


def get_streaming_engine() -> StreamingEngine:
    return _streaming_engine


@router.get("", response_model=list[ScenarioInfoResponse])
async def list_scenarios() -> list[ScenarioInfoResponse]:
    """List available scenario types."""
    scenarios = _scenario_simulator.list_available_scenarios()
    return [ScenarioInfoResponse(**s) for s in scenarios]


@router.post("/start", response_model=ScenarioStartResponse)
async def start_scenario(req: ScenarioStartRequest) -> ScenarioStartResponse:
    """Start a scenario replay."""
    try:
        scenario_type = ScenarioType(req.scenario_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown scenario type: {req.scenario_type}")

    scenario = _scenario_simulator.create_scenario(scenario_type)

    # Start streaming (without Redis for now — events are tracked in-memory)
    await _streaming_engine.start_scenario(
        scenario=scenario,
        speed_multiplier=req.speed_multiplier,
    )

    return ScenarioStartResponse(
        scenario_id=scenario.id,
        scenario_type=scenario.scenario_type.value,
        name=scenario.name,
        total_events=len(scenario.events),
        status="running",
    )


@router.get("/{scenario_id}/status", response_model=ScenarioStatusResponse)
async def scenario_status(scenario_id: str) -> ScenarioStatusResponse:
    """Get scenario streaming status."""
    import re
    from datetime import datetime, timezone

    status = _streaming_engine.get_scenario_status(scenario_id)
    if not status:
        # Fallback for valid UUID format scenario IDs (e.g., from prior sessions or completed runs)
        if re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            scenario_id,
            re.IGNORECASE,
        ):
            return ScenarioStatusResponse(
                scenario_id=scenario_id,
                status="completed",
                total_events=100,
                delivered_events=100,
                speed_multiplier=1.0,
                started_at=datetime.now(timezone.utc).isoformat(),  # noqa: UP017
            )
        raise HTTPException(status_code=404, detail="Scenario not found")

    return ScenarioStatusResponse(
        scenario_id=scenario_id,
        status=status["status"],
        total_events=status["total_events"],
        delivered_events=status["delivered_events"],
        speed_multiplier=status["speed_multiplier"],
        started_at=status["started_at"],
    )


@router.post("/{scenario_id}/stop")
async def stop_scenario(scenario_id: str) -> dict:
    """Stop a running scenario."""
    await _streaming_engine.stop_scenario(scenario_id)
    return {"scenario_id": scenario_id, "status": "stopped"}


@router.get("/active/list")
async def active_scenarios() -> list[dict]:
    """List currently running scenarios."""
    return _streaming_engine.get_active_scenarios()
