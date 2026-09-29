"""Cross-Bank Synthetic Consortium Benchmark Package (CFI-CrossBank-01).

Implements deterministic 7-scenario topology generation, strict partial information
horizons, and isolated vs federated vs pooled empirical evaluation.
"""

from experiments.cross_bank.quantify_information_gain import (
    CommunicationCostModel,
    ConsortiumValueQuantifier,
    InformationHorizonAnalyzer,
    run_consortium_value_quantification,
)
from experiments.cross_bank.run_flagship_experiment import (
    FlagshipConsortiumExperiment,
    FlagshipMLPClassifier,
    run_flagship_experiment,
)
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
    "InformationHorizonAnalyzer",
    "ConsortiumValueQuantifier",
    "CommunicationCostModel",
    "run_consortium_value_quantification",
    "FlagshipConsortiumExperiment",
    "FlagshipMLPClassifier",
    "run_flagship_experiment",
]
