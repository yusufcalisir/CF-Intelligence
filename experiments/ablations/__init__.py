"""Ablations and sensitivity experiment modules.

Provides:
- Comprehensive Dirichlet sensitivity sweep across FedAvg, FedProx, and SCAFFOLD.
- Component factorial ablations (Graph x DP x SecAgg x CrossBank).
- Controlled Feature Paradigm Ablation (Tabular Only vs Graph Only vs Tabular + Graph).
- Graph Topology Complexity & Density Sensitivity Analysis.
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
from experiments.ablations.graph_vs_tabular import (
    FeatureParadigm,
    GraphVsTabularConfig,
    GraphVsTabularSuiteResult,
    GraphVsTabularUpliftResult,
    ParadigmEvaluationResult,
    run_graph_vs_tabular_ablation,
)
from experiments.ablations.topology_sensitivity import (
    DegreePointResult,
    GraphStructureType,
    HopPointResult,
    IsolationDegradationPointResult,
    TopologyModelPointResult,
    TopologySensitivityConfig,
    TopologySensitivitySuiteResult,
    run_topology_sensitivity_sweep,
)

__all__ = [
    "ComponentAblationResult",
    "DegreePointResult",
    "DirichletSweepRunner",
    "FLStrategyResult",
    "FactorialAblationRunner",
    "FactorialAblationSuiteResult",
    "FactorialConfig",
    "FeatureParadigm",
    "GraphStructureType",
    "GraphVsTabularConfig",
    "GraphVsTabularSuiteResult",
    "GraphVsTabularUpliftResult",
    "HopPointResult",
    "InteractionEffectResult",
    "IsolationDegradationPointResult",
    "MainEffectResult",
    "ParadigmEvaluationResult",
    "SweepConfig",
    "TopologyModelPointResult",
    "TopologySensitivityConfig",
    "TopologySensitivitySuiteResult",
    "run_dirichlet_sensitivity_sweep",
    "run_factorial_ablation_sweep",
    "run_graph_vs_tabular_ablation",
    "run_topology_sensitivity_sweep",
]
