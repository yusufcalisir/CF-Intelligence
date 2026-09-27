"""Ablations and sensitivity experiment modules.

Provides:
- Comprehensive Dirichlet sensitivity sweep across FedAvg, FedProx, and SCAFFOLD.
- Component factorial ablations.
"""

from experiments.ablations.dirichlet_sweep import (
    DirichletSweepRunner,
    FLStrategyResult,
    SweepConfig,
    run_dirichlet_sensitivity_sweep,
)

__all__ = [
    "DirichletSweepRunner",
    "FLStrategyResult",
    "SweepConfig",
    "run_dirichlet_sensitivity_sweep",
]
