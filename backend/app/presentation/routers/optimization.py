"""REST API Router for Automated FL Hyperparameter Optimization (Optuna)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.application.services.fl_hyperparameter_optimizer import FLHyperparameterOptimizer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/admin/optimization", tags=["Hyperparameter Optimization"])

# In-memory storage for optimization studies
STORED_STUDIES: dict[str, dict[str, Any]] = {}


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

        STORED_STUDIES[payload.study_name] = results
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
    return list(STORED_STUDIES.keys())


@router.get("/studies/{study_name}", response_model=TuneResponse)
async def get_study_details(study_name: str) -> TuneResponse:
    """Retrieve details and best parameters for a specific Optuna study."""
    if study_name not in STORED_STUDIES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Study '{study_name}' not found.",
        )
    return TuneResponse(**STORED_STUDIES[study_name])
