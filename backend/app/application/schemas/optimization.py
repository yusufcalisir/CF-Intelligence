"""Pydantic v2 schemas for Federated Hyperparameter Optimization & Pareto Tuning API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TuneRequest(BaseModel):
    """Payload requesting an Optuna FL hyperparameter optimization session."""

    model_config = ConfigDict(extra="ignore")

    study_name: str = Field(
        default="fl_hpo_study",
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Unique study identifier",
    )
    dirichlet_alpha: float = Field(
        default=0.5,
        ge=0.01,
        le=100.0,
        description="Non-IID Dirichlet concentration alpha",
    )
    num_clients: int = Field(
        default=3,
        ge=1,
        le=50,
        description="Number of client bank nodes",
    )
    num_rounds: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Number of FL rounds per trial",
    )
    n_trials: int = Field(
        default=5,
        ge=1,
        le=100,
        description="Number of Optuna Bayesian trials",
    )
    timeout_seconds: float | None = Field(
        default=60.0,
        ge=1.0,
        le=3600.0,
        description="Timeout limit in seconds",
    )


class TuneResponse(BaseModel):
    """Response containing Optuna study optimization results."""

    model_config = ConfigDict(extra="ignore")

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


class HyperparameterBound(BaseModel):
    """Search space specification for a single tunable hyperparameter."""

    model_config = ConfigDict(extra="ignore")

    name: str
    type: str = Field(..., description="Parameter data type: float, int, categorical")
    range: list[float] | None = Field(default=None, description="[min, max] range for numerical parameters")
    choices: list[Any] | None = Field(default=None, description="Allowed choices for categorical parameters")
    scale: str = Field(default="linear", description="Sampling scale: linear, log")
    default_value: Any = Field(..., description="Default baseline value")
    description: str = Field(..., description="Parameter functionality description")


class HyperparameterRangesResponse(BaseModel):
    """Response cataloging the entire FL hyperparameter search space."""

    model_config = ConfigDict(extra="ignore")

    search_space: dict[str, HyperparameterBound]
    sampler: str = "TPESampler (Tree-structured Parzen Estimator)"
    pruner: str = "MedianPruner (n_startup_trials=2, n_warmup_steps=1)"
    default_objective: str = "validation_auc_roc (maximize)"


class ParetoPoint(BaseModel):
    """Evaluation point in multi-objective space (AUC vs Privacy Epsilon vs Latency)."""

    model_config = ConfigDict(extra="ignore")

    trial_id: int
    learning_rate: float
    batch_size: int
    fedprox_mu: float
    dp_epsilon: float
    auc_roc: float
    latency_ms: float
    is_pareto_optimal: bool = True


class ParetoFrontResponse(BaseModel):
    """Multi-objective Pareto frontier response."""

    model_config = ConfigDict(extra="ignore")

    total_evaluated_points: int
    pareto_optimal_count: int
    pareto_points: list[ParetoPoint]
    recommendation: ParetoPoint | None = None


class StudyListResponse(BaseModel):
    """List of all registered optimization studies."""

    model_config = ConfigDict(extra="ignore")

    studies: list[str]
    total_studies: int
