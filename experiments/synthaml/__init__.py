"""Danish Spar Nord Bank SynthAML Synthetic AML Benchmark.

Evaluates federated learning consensus (FedAvg, FedProx) against isolated banking silos
and centralized upper bounds in detecting suspicious activity report (SAR) escalations
from multi-table lookback transaction investigations.
"""

from __future__ import annotations

from experiments.synthaml.run_synthaml_benchmark import (
    AlertMLPClassifier,
    SynthAMLPartitioner,
    run_synthaml_benchmark,
)

__all__ = [
    "AlertMLPClassifier",
    "SynthAMLPartitioner",
    "run_synthaml_benchmark",
]
