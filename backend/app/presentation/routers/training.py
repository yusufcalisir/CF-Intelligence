"""Training progress endpoints.

Provides access to per-round training data for a simulation.
Reads from the Redis-backed event store (with in-memory fallback).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.presentation.routers.simulation import _simulation_events

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/training", tags=["training"])


@router.get("/{simulation_id}/rounds")
async def get_training_rounds(simulation_id: str) -> list[dict]:
    """Get all training rounds for a simulation.

    Returns round-by-round metrics including loss, participants,
    dropouts, and timing data.
    """
    events = _simulation_events.get_list(simulation_id)

    rounds = []
    for event in events:
        if event.get("event_type") == "round_complete":
            data = event["data"]
            rounds.append(
                {
                    "round_number": data.get("round"),
                    "total_rounds": data.get("total"),
                    "global_loss": data.get("loss", 0),
                    "auc": data.get("auc", 0.0),
                    "per_bank_auc": data.get("per_bank_auc", {}),
                    "per_bank_loss": data.get("per_bank_loss", {}),
                    "participating_banks": data.get("participants", []),
                    "dropped_banks": data.get("dropped", []),
                    "duration_ms": data.get("duration_ms", 0),
                    "privacy_budget": data.get("privacy_budget", 0),
                    "feature_importance": data.get("feature_importance", {}),
                    "canary_info": data.get("canary_info", {}),
                }
            )

    return rounds


@router.get("/{simulation_id}/rounds/{round_number}")
async def get_training_round(simulation_id: str, round_number: int) -> dict:
    """Get details for a specific training round."""
    rounds = await get_training_rounds(simulation_id)

    for r in rounds:
        if r.get("round_number") == round_number:
            return r

    raise HTTPException(
        status_code=404,
        detail=f"Round {round_number} not found for simulation {simulation_id}",
    )


@router.get("/{simulation_id}/progress")
async def get_training_progress(simulation_id: str) -> dict:
    """Get the latest progress update for a running simulation."""
    events = _simulation_events.get_list(simulation_id)

    if events:
        return events[-1]

    return {"event_type": "unknown", "data": {"message": "No progress data available"}}
