"""Ablations and sensitivity experiment modules.

Provides:
- Comprehensive Dirichlet sensitivity sweep across FedAvg, FedProx, and SCAFFOLD.
- Component factorial ablations (Graph x DP x SecAgg x CrossBank).
"""

from experiments.ablations.dirichlet_sweep import (
    DirichletSweepRunner,
    FLStrategyResult,
    SweepConfig,
    run_dirichlet_sensitivity_sweep,
)
from experiments.ablations.factorial_runner import (
    ComponentAblationResult,
    FactorialAblationRunner,
    FactorialAblationSuiteResult,
    FactorialConfig,
    InteractionEffectResult,
    MainEffectResult,
    run_factorial_ablation_sweep,
)

__all__ = [
    "ComponentAblationResult",
    "DirichletSweepRunner",
    "FLStrategyResult",
    "FactorialAblationRunner",
    "FactorialAblationSuiteResult",
    "FactorialConfig",
    "InteractionEffectResult",
    "MainEffectResult",
    "SweepConfig",
    "run_dirichlet_sensitivity_sweep",
    "run_factorial_ablation_sweep",
]
