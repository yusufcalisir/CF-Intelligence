"""Multi-Paradigm Benchmark Baselines & Comparative Analysis Package.

Provides implementations for:
- Classical Tabular Baselines: Logistic Regression, Random Forest, Gradient Boosted Trees (HistGradientBoosting / LightGBM)
- Local Banking Silos: Isolated single-bank models evaluating cross-institution generalization breakdown
- Pooled Centralized Upper Bound: Theoretical privacy-violating ceiling pooling all bank data
- Comparative Benchmark Engine: Unified orchestration, delta calculations, and machine-readable exports
"""

from experiments.baselines.classical_baselines import ClassicalBaselines
from experiments.baselines.comparative_runner import ComparativeBenchmarkEngine
from experiments.baselines.local_silos import LocalSiloEvaluator
from experiments.baselines.pooled_upper_bound import PooledCentralizedBenchmark

__all__ = [
    "ClassicalBaselines",
    "ComparativeBenchmarkEngine",
    "LocalSiloEvaluator",
    "PooledCentralizedBenchmark",
]
