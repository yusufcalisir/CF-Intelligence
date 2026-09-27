"""IBM AMLSim Multi-Hop Pattern Detection & Structural Laundering Benchmark.

Evaluates inductive graph representation learning (GraphSAGE) against tabular baselines
in intercepting complex multi-hop financial laundering typologies (cycles, fan-in, fan-out).
"""

from __future__ import annotations

from experiments.amlsim.evaluate_patterns import run_amlsim_pattern_benchmark

__all__ = ["run_amlsim_pattern_benchmark"]
