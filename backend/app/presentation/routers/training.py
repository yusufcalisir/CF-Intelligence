"""Training progress endpoints.

Provides access to per-round training data, progress streams, convergence metrics,
and simulation history. Reads from Redis-backed event and result stores with in-process fallbacks.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Path, Query, status

from app.application.schemas.simulation import (
    TrainingHistoryItem,
    TrainingHistoryResponse,
    TrainingMetricsSummaryResponse,
    TrainingProgressResponse,
    TrainingRoundResponse,
)
from app.presentation.routers.simulation import _simulation_events, _simulation_results

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/training", tags=["training"])
api_router = APIRouter(prefix="/v1/training", tags=["training"])


@router.get("/progress", response_model=TrainingProgressResponse)
@api_router.get("/progress", response_model=TrainingProgressResponse)
async def get_latest_training_progress(
    simulation_id: str | None = Query(default=None, description="Optional target simulation run ID"),
) -> TrainingProgressResponse:
    """Retrieve real-time training progress for the specified or most recent simulation."""
    target_id = simulation_id
    if not target_id:
        all_sims = list(_simulation_results.list_values())
        if all_sims:
            target_id = all_sims[-1].get("id")

    if not target_id:
        return TrainingProgressResponse(
            simulation_id=None,
            event_type="unknown",
            data={"message": "No active simulations available."},
        )

    return await get_training_progress(target_id)


@router.get("/metrics", response_model=TrainingMetricsSummaryResponse)
@api_router.get("/metrics", response_model=TrainingMetricsSummaryResponse)
async def get_training_metrics(
    simulation_id: str | None = Query(default=None, description="Optional target simulation run ID"),
) -> TrainingMetricsSummaryResponse:
    """Retrieve training convergence loss and per-bank AUC progression."""
    target_id = simulation_id
    if not target_id:
        all_sims = list(_simulation_results.list_values())
        if all_sims:
            target_id = all_sims[-1].get("id")

    if not target_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active simulations found to aggregate training metrics.",
        )

    sim = _simulation_results.get(target_id)
    if not sim:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation '{target_id}' not found.",
        )

    events = _simulation_events.get_list(target_id)
    losses: list[float] = []
    aucs: list[float] = []
    per_bank_auc: dict[str, list[float]] = {}
    per_bank_loss: dict[str, list[float]] = {}

    for event in events:
        if event.get("event_type") == "round_complete":
            d = event.get("data", {})
            losses.append(float(d.get("loss", 0.0)))
            aucs.append(float(d.get("auc", 0.0)))
            for b, val in d.get("per_bank_auc", {}).items():
                per_bank_auc.setdefault(b, []).append(float(val))
            for b, val in d.get("per_bank_loss", {}).items():
                per_bank_loss.setdefault(b, []).append(float(val))

    return TrainingMetricsSummaryResponse(
        simulation_id=target_id,
        total_rounds=sim.get("total_rounds", len(losses)),
        global_losses=losses,
        auc_history=aucs,
        per_bank_auc=per_bank_auc,
        per_bank_loss=per_bank_loss,
    )


@router.get("/history", response_model=TrainingHistoryResponse)
@api_router.get("/history", response_model=TrainingHistoryResponse)
async def get_training_history(
    limit: int = Query(default=10, ge=1, le=100, description="Max historical simulation runs to return"),
) -> TrainingHistoryResponse:
    """Retrieve historical federated training simulation runs."""
    all_sims = list(_simulation_results.list_values())
    runs: list[TrainingHistoryItem] = []
    for sim in all_sims:
        runs.append(
            TrainingHistoryItem(
                simulation_id=sim.get("id", ""),
                status=sim.get("status", "unknown"),
                current_round=sim.get("current_round", 0),
                total_rounds=sim.get("total_rounds", 10),
                progress_pct=float(sim.get("progress_pct", 0.0)),
                created_at=sim.get("created_at"),
                completed_at=sim.get("completed_at"),
                duration_seconds=sim.get("duration_seconds"),
            )
        )
    return TrainingHistoryResponse(
        total_count=len(runs),
        runs=runs[:limit],
    )


@router.get("/{simulation_id}/rounds", response_model=list[TrainingRoundResponse])
@router.get("/rounds/{simulation_id}", response_model=list[TrainingRoundResponse])
@api_router.get("/{simulation_id}/rounds", response_model=list[TrainingRoundResponse])
@api_router.get("/rounds/{simulation_id}", response_model=list[TrainingRoundResponse])
async def get_training_rounds(
    simulation_id: str = Path(..., min_length=1, description="Simulation run identifier"),
) -> list[TrainingRoundResponse]:
    """Get all training rounds for a simulation.

    Returns round-by-round metrics including loss, participants,
    dropouts, and timing data.
    """
    sim = _simulation_results.get(simulation_id)
    if not sim:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation '{simulation_id}' not found",
        )

    events = _simulation_events.get_list(simulation_id)
    rounds: list[TrainingRoundResponse] = []
    for event in events:
        if event.get("event_type") == "round_complete":
            data = event.get("data", {})
            rounds.append(
                TrainingRoundResponse(
                    round_number=data.get("round", 0),
                    total_rounds=data.get("total", sim.get("total_rounds", 10)),
                    global_loss=float(data.get("loss", 0.0)),
                    auc=float(data.get("auc", 0.0)),
                    per_bank_auc=data.get("per_bank_auc", {}),
                    per_bank_loss=data.get("per_bank_loss", {}),
                    participating_banks=data.get("participants", []),
                    dropped_banks=data.get("dropped", []),
                    duration_ms=float(data.get("duration_ms", 0.0)),
                    privacy_budget=float(data.get("privacy_budget", 0.0)),
                    feature_importance=data.get("feature_importance", {}),
                    canary_info=data.get("canary_info", {}),
                )
            )

    return rounds


@router.get("/{simulation_id}/rounds/{round_number}", response_model=TrainingRoundResponse)
@api_router.get("/{simulation_id}/rounds/{round_number}", response_model=TrainingRoundResponse)
async def get_training_round(
    simulation_id: str = Path(..., min_length=1, description="Simulation run identifier"),
    round_number: int = Path(..., ge=1, description="Training round index"),
) -> TrainingRoundResponse:
    """Get details for a specific training round."""
    rounds = await get_training_rounds(simulation_id)

    for r in rounds:
        if r.round_number == round_number:
            return r

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Round {round_number} not found for simulation {simulation_id}",
    )


@router.get("/{simulation_id}/progress", response_model=TrainingProgressResponse)
@api_router.get("/{simulation_id}/progress", response_model=TrainingProgressResponse)
async def get_training_progress(
    simulation_id: str = Path(..., min_length=1, description="Simulation run identifier"),
) -> TrainingProgressResponse:
    """Get the latest progress update for a running simulation."""
    sim = _simulation_results.get(simulation_id)
    if not sim:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation '{simulation_id}' not found",
        )

    events = _simulation_events.get_list(simulation_id)
    if events:
        last_event = events[-1]
        return TrainingProgressResponse(
            simulation_id=simulation_id,
            event_type=last_event.get("event_type", "unknown"),
            status=sim.get("status"),
            current_round=sim.get("current_round", 0),
            total_rounds=sim.get("total_rounds", 10),
            progress_pct=float(sim.get("progress_pct", 0.0)),
            data=last_event.get("data", {}),
        )

    return TrainingProgressResponse(
        simulation_id=simulation_id,
        event_type="pending",
        status=sim.get("status"),
        current_round=sim.get("current_round", 0),
        total_rounds=sim.get("total_rounds", 10),
        progress_pct=float(sim.get("progress_pct", 0.0)),
        data={"message": "No progress data available yet"},
    )
