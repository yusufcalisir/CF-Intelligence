"""Cross-Bank Synthetic Consortium Benchmark Package (CFI-CrossBank-01).

Implements deterministic 7-scenario topology generation, strict partial information
horizons, and isolated vs federated vs pooled empirical evaluation.
"""

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
