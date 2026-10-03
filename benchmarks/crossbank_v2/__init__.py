"""CrossBank v2 Canonical Protocol Package.

Scientific methodology repair for cross-bank federated fraud detection benchmarks.
"""

from benchmarks.crossbank_v2.config import (
    DEFAULT_INSTITUTIONS,
    DEFAULT_SCENARIOS,
    ConditionType,
    CrossBankV2Config,
    FeatureRegime,
    PlannedCondition,
    compute_experiment_matrix_hash,
    get_planned_experiment_matrix,
)
from benchmarks.crossbank_v2.features import (
    CONSORTIUM_SIGNAL_COLUMN,
    LOCAL_FEATURE_COLUMNS,
    LocalPreprocessor,
    PartitionFirstFeatureExtractor,
    compute_feature_schema_hash,
)
from benchmarks.crossbank_v2.generator import (
    CrossBankV2NetworkGenerator,
)
from benchmarks.crossbank_v2.metrics import (
    compute_comprehensive_metrics,
    compute_low_fpr_resolution,
    compute_scenario_specific_metrics,
    select_threshold_on_validation,
)
from benchmarks.crossbank_v2.model import (
    ColdStartLocalBaseline,
    CrossBankMLP,
    SimpleLogisticBaseline,
    aggregate_fedavg,
    train_pytorch_model,
)
from benchmarks.crossbank_v2.runner import CrossBankV2Runner
from benchmarks.crossbank_v2.schema import (
    ConditionResult,
    ConfusionMatrixCounts,
    CrossBankV2Artifact,
    EvaluationMetrics,
    InformationBudgetAccounting,
    LowFPRResolution,
    ScenarioMetricResult,
    SplitSummary,
    ThresholdProvenance,
    TrainingBudgetAccounting,
)
from benchmarks.crossbank_v2.verifier import (
    CrossBankV2ProtocolVerifier,
    ProtocolVerificationError,
)

__all__ = [
    "CONSORTIUM_SIGNAL_COLUMN",
    "ColdStartLocalBaseline",
    "ConditionResult",
    "ConditionType",
    "ConfusionMatrixCounts",
    "CrossBankMLP",
    "CrossBankV2Artifact",
    "CrossBankV2Config",
    "CrossBankV2NetworkGenerator",
    "CrossBankV2ProtocolVerifier",
    "CrossBankV2Runner",
    "DEFAULT_INSTITUTIONS",
    "DEFAULT_SCENARIOS",
    "EvaluationMetrics",
    "FeatureRegime",
    "InformationBudgetAccounting",
    "LOCAL_FEATURE_COLUMNS",
    "LocalPreprocessor",
    "LowFPRResolution",
    "PartitionFirstFeatureExtractor",
    "PlannedCondition",
    "ProtocolVerificationError",
    "ScenarioMetricResult",
    "SimpleLogisticBaseline",
    "SplitSummary",
    "ThresholdProvenance",
    "TrainingBudgetAccounting",
    "aggregate_fedavg",
    "compute_comprehensive_metrics",
    "compute_experiment_matrix_hash",
    "compute_feature_schema_hash",
    "compute_low_fpr_resolution",
    "compute_scenario_specific_metrics",
    "get_planned_experiment_matrix",
    "select_threshold_on_validation",
    "train_pytorch_model",
]
