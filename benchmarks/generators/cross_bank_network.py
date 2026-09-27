"""Benchmark generator entrypoint for Cross-Bank Synthetic Consortium Network (CFI-CrossBank-01).

Exposes CrossBankNetworkGenerator, SCENARIO_DEFINITIONS, and generation helpers
under the benchmarks.generators namespace.
"""

from __future__ import annotations

from experiments.cross_bank.topology_generator import (
    DEFAULT_CONSORTIUM_NODES,
    FEATURE_COLUMNS,
    SCENARIO_DEFINITIONS,
    CrossBankNetworkGenerator,
)

__all__ = [
    "CrossBankNetworkGenerator",
    "SCENARIO_DEFINITIONS",
    "DEFAULT_CONSORTIUM_NODES",
    "FEATURE_COLUMNS",
]
