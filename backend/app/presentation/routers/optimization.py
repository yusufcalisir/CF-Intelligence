"""REST API Router for Automated FL Hyperparameter Optimization (Optuna)."""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.application.services.fl_hyperparameter_optimizer import FLHyperparameterOptimizer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/admin/optimization", tags=["Hyperparameter Optimization"])

# Thread-safe bounded in-memory storage for optimization studies (Vector 9 & 18)
MAX_STORED_STUDIES = 100
_studies_lock = threading.Lock()
STORED_STUDIES: OrderedDict[str, dict[str, Any]] = OrderedDict()


def store_study_result(study_name: str, results: dict[str, Any]) -> None:
    """Store study result with thread-safety and FIFO eviction at MAX_STORED_STUDIES."""
    with _studies_lock:
        if len(STORED_STUDIES) >= MAX_STORED_STUDIES and study_name not in STORED_STUDIES:
            STORED_STUDIES.popitem(last=False)
        STORED_STUDIES[study_name] = results
        STORED_STUDIES.move_to_end(study_name)


def get_stored_study(study_name: str) -> dict[str, Any] | None:
    """Safely retrieve a study result by name."""
    with _studies_lock:
        return STORED_STUDIES.get(study_name)


def list_stored_study_names() -> list[str]:
    """Safely list all stored study names."""
    with _studies_lock:
        return list(STORED_STUDIES.keys())


def remove_stored_study(study_name: str) -> bool:
    """Safely remove a stored study."""
    with _studies_lock:
        if study_name in STORED_STUDIES:
            del STORED_STUDIES[study_name]
            return True
        return False


class TuneRequest(BaseModel):
    """Payload requesting an Optuna FL hyperparameter optimization session."""

    study_name: str = Field(default="fl_hpo_study", description="Unique study identifier")
    dirichlet_alpha: float = Field(
        default=0.5, ge=0.01, le=100.0, description="Non-IID Dirichlet concentration alpha"
    )
    num_clients: int = Field(default=3, ge=1, le=50, description="Number of client bank nodes")
    num_rounds: int = Field(default=5, ge=1, le=50, description="Number of FL rounds per trial")
    n_trials: int = Field(default=5, ge=1, le=100, description="Number of Optuna Bayesian trials")
    timeout_seconds: float | None = Field(default=60.0, description="Timeout limit in seconds")


class TuneResponse(BaseModel):
    """Response containing Optuna study optimization results."""

    study_name: str
    dirichlet_alpha: float
    best_trial_number: int
    best_value: float
    best_params: dict[str, Any]
    param_importances: dict[str, float]
    total_trials: int
    completed_trials: int
    pruned_trials: int
    duration_ms: float


@router.post("/tune", response_model=TuneResponse, status_code=status.HTTP_200_OK)
async def trigger_hyperparameter_tuning(payload: TuneRequest) -> TuneResponse:
    """Trigger an Optuna Bayesian TPE hyperparameter optimization session for FL."""
    try:
        optimizer = FLHyperparameterOptimizer(
            study_name=payload.study_name,
            dirichlet_alpha=payload.dirichlet_alpha,
            num_clients=payload.num_clients,
            num_rounds=payload.num_rounds,
        )

        results = optimizer.run_optimization(
            n_trials=payload.n_trials,
            timeout=payload.timeout_seconds,
        )

        store_study_result(payload.study_name, results)
        return TuneResponse(**results)
    except Exception as e:
        logger.error("Failed to run FL hyperparameter tuning study '%s': %s", payload.study_name, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Hyperparameter tuning error: {e!s}",
        ) from e


@router.get("/studies", response_model=list[str])
async def list_optimization_studies() -> list[str]:
    """List all completed or active Optuna hyperparameter study names."""
    return list_stored_study_names()


@router.get("/studies/{study_name}", response_model=TuneResponse)
async def get_study_details(study_name: str) -> TuneResponse:
    """Retrieve details and best parameters for a specific Optuna study."""
    study = get_stored_study(study_name)
    if study is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Study '{study_name}' not found.",
        )
    return TuneResponse(**study)


@router.delete("/studies/{study_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_study(study_name: str) -> Response:
    """Delete a completed or active Optuna study from memory."""
    deleted = remove_stored_study(study_name)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Study '{study_name}' not found.",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

