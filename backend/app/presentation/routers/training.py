"""Training progress endpoints.

Provides access to per-round training data, progress streams, convergence metrics,
and simulation history. Reads from Redis-backed event and result stores with in-process fallbacks.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Path, Query, status

from app.application.schemas.simulation import (
    OperationalFederationRequest,
    OperationalFederationResponse,
    TrainingHistoryItem,
    TrainingHistoryResponse,
    TrainingMetricsSummaryResponse,
    TrainingProgressResponse,
    TrainingRoundResponse,
)
from app.application.services.operational_federation_service import OperationalFederationService
from app.application.services.training_provenance_service import (
    TrainingProvenanceConfigurationError,
    TrainingProvenanceSecurityError,
)
from app.presentation.routers.model_registry import _authenticate_and_authorize_operator
from app.presentation.routers.simulation import _simulation_events, _simulation_results

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/training", tags=["training"])
api_router = APIRouter(prefix="/v1/training", tags=["training"])


@router.post("/federation/jobs", response_model=OperationalFederationResponse, status_code=status.HTTP_201_CREATED)
@api_router.post("/federation/jobs", response_model=OperationalFederationResponse, status_code=status.HTTP_201_CREATED)
async def start_operational_federation_job(
    payload: OperationalFederationRequest,
    authorization: str | None = Header(None, description="Bearer <operator_jwt>"),
) -> OperationalFederationResponse:
    """Authorized operational entry point to initiate the canonical distributed federated learning lifecycle.

    Guarantees:
    - Cryptographically authenticates initiating operator via live RBAC (requires admin, ml_lead, or ml_engineer role with clearance level >= 2).
    - Resolves authorities, credentials, vault paths, and pre-provisioned bank endpoints strictly server-side.
    - Never accepts private keys, database URLs, bank credentials, raw datasets, or process handles from clients.
    - Executes under isolated Windows Restricted Token in production via run_production_federation.
    - Tracks actual resulting execution state without fabricated success, dummy receipts, or synthetic metrics.
    - Registers verified candidate model in ModelRegistry with inactive status (never automatically promotes champion).
    """
    operator_user, claims, _ = _authenticate_and_authorize_operator(
        authorization=authorization,
        allowed_roles={"admin", "ml_lead", "ml_engineer"},
        operation_name="operational federated training initiation",
        min_clearance=2,
    )
    logger.info("Operator [%s] authorized for operational federation job", operator_user.username)

    svc = OperationalFederationService()
    try:
        result = svc.execute_operational_federation(
            job_id=payload.job_id,
            simulation_id=payload.simulation_id,
            participating_banks=payload.participating_banks,
            num_rounds=payload.num_rounds,
            expected_unique_samples=payload.expected_unique_samples,
        )
        saved_ver = result.get("saved_version")
        ver_number = result.get("model_version")
        if ver_number is None and isinstance(saved_ver, dict):
            ver_number = saved_ver.get("version")

        return OperationalFederationResponse(
            job_id=result.get("job_id", payload.job_id or ""),
            training_run_id=result.get("training_run_id", ""),
            simulation_id=result.get("simulation_id", payload.simulation_id or ""),
            status=result.get("status", "COMPLETED"),
            is_verified=bool(result.get("is_verified", False)),
            metrics=result.get("metrics", {}),
            artifact_sha256=result.get("artifact_sha256") or result.get("final_artifact_sha256", ""),
            model_version=ver_number,
            is_promoted_champion=bool(result.get("is_promoted_champion", False)),
            coordinator_pid=result.get("coordinator_pid", 0),
            worker_pids=result.get("worker_pids", {}),
        )
    except TrainingProvenanceSecurityError as exc:
        logger.error("Operational federation rejected due to security policy violation: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Federation security policy violation: {exc}",
        ) from exc
    except TrainingProvenanceConfigurationError as exc:
        logger.error("Operational federation failed closed due to configuration error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Federation authority configuration error: {exc}",
        ) from exc
    except ValueError as exc:
        logger.warning("Operational federation bad request: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("Operational federation execution failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Operational federation failed: {exc}",
        ) from exc


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
            loss_val = d.get("loss")
            if loss_val is not None:
                losses.append(float(loss_val))
            auc_val = d.get("auc")
            if auc_val is not None:
                aucs.append(float(auc_val))
            for b, val in d.get("per_bank_auc", {}).items():
                if val is not None:
                    per_bank_auc.setdefault(b, []).append(float(val))
            for b, val in d.get("per_bank_loss", {}).items():
                if val is not None:
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
        if not isinstance(sim, dict):
            continue
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
    runs.sort(key=lambda r: r.created_at or "", reverse=True)
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
            loss_raw = data.get("loss")
            auc_raw = data.get("auc")
            pb_raw = data.get("privacy_budget")
            rounds.append(
                TrainingRoundResponse(
                    round_number=data.get("round", 0),
                    total_rounds=data.get("total", sim.get("total_rounds", 10)),
                    global_loss=float(loss_raw) if loss_raw is not None else None,
                    auc=float(auc_raw) if auc_raw is not None else None,
                    per_bank_auc=data.get("per_bank_auc", {}),
                    per_bank_loss=data.get("per_bank_loss", {}),
                    participating_banks=data.get("participants", []),
                    dropped_banks=data.get("dropped", []),
                    duration_ms=float(data.get("duration_ms", 0.0)),
                    privacy_budget=float(pb_raw) if pb_raw is not None else None,
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
