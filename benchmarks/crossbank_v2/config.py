"""Canonical Protocol Configuration for CrossBank v2 (CFI-CrossBank-02).

Preregistered scientific protocol, frozen seeds, sample sizes, condition matrices,
and architectural budgets.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum


class DataProvenance(StrEnum):
    PROJECT_SYNTHETIC = "PROJECT_SYNTHETIC"


class FederationType(StrEnum):
    SIMULATED_FEDERATION = "SIMULATED_FEDERATION"


class CurrencyUnit(StrEnum):
    SYNTHETIC_USD = "SYNTHETIC_USD"


class FeatureRegime(StrEnum):
    LOCAL_ONLY = "REGIME_LOCAL_ONLY"
    CONSORTIUM_SIGNAL = "REGIME_CONSORTIUM_SIGNAL"


class ConditionType(StrEnum):
    LOCAL_ISOLATED = "LOCAL_ISOLATED"
    FEDERATED_FEDAVG = "FEDERATED_FEDAVG"
    CENTRALIZED_POOLED = "CENTRALIZED_POOLED"
    COLD_START_ZERO_POSITIVE = "COLD_START_ZERO_POSITIVE"
    SIMPLE_BASELINE_LOGISTIC = "SIMPLE_BASELINE_LOGISTIC"


# Scientific Questions Formalization (Phase 2D Protocol Repair)
SCIENTIFIC_QUESTIONS = {
    "Q1": "Feasibility: Can a federated model be trained across simulated institutions without centrally pooling raw training rows?",
    "Q2": "Information Gain: Does access to broader distributed training information improve performance relative to institution-local training?",
    "Q3": "Centralized Comparison: Under matched nominal training passes and the same underlying distributed information universe, how does FedAvg compare with a centralized pooled control?",
    "Q4A": "Institution-Level Cold Start: How does an institution with zero historical fraud-positive training examples of ANY type benefit from federated parameter transfer?",
    "Q4B": "Typology-Level Cold Start: How does an institution with historical fraud examples but zero training examples of a novel target typology (Scenario 7) detect that typology?",
}


@dataclass(frozen=True)
class InstitutionConfig:
    bank_id: str
    name: str
    archetype: str
    volume_share: float
    account_count: int


DEFAULT_INSTITUTIONS: list[InstitutionConfig] = [
    InstitutionConfig(
        bank_id="bank_a",
        name="Bank Alpha",
        archetype="RETAIL_CONSUMER",
        volume_share=0.50,
        account_count=5000,
    ),
    InstitutionConfig(
        bank_id="bank_b",
        name="Bank Beta",
        archetype="COMMERCIAL_CORPORATE",
        volume_share=0.30,
        account_count=3000,
    ),
    InstitutionConfig(
        bank_id="bank_c",
        name="Bank Gamma",
        archetype="CROSS_BORDER_REMITTANCE",
        volume_share=0.20,
        account_count=2000,
    ),
]


@dataclass(frozen=True)
class ScenarioConfig:
    scenario_id: str
    title: str
    typology: str
    participating_banks: list[str]
    hops: int
    is_cold_start_target: bool = False


DEFAULT_SCENARIOS: list[ScenarioConfig] = [
    ScenarioConfig(
        scenario_id="SCENARIO_1",
        title="Localized Structuring",
        typology="LOCAL_SMURFING",
        participating_banks=["bank_a"],
        hops=2,
    ),
    ScenarioConfig(
        scenario_id="SCENARIO_2",
        title="Two-Bank Cross-Border Wire Layering",
        typology="CROSS_BANK_LAYERING",
        participating_banks=["bank_a", "bank_b"],
        hops=2,
    ),
    ScenarioConfig(
        scenario_id="SCENARIO_3",
        title="Three-Bank Cyclic Mule Ring",
        typology="CYCLIC_MULE_RING",
        participating_banks=["bank_a", "bank_b", "bank_c"],
        hops=3,
    ),
    ScenarioConfig(
        scenario_id="SCENARIO_4",
        title="Behavior-Shifting Smurfing Consolidation",
        typology="BEHAVIOR_SHIFTING",
        participating_banks=["bank_a", "bank_b", "bank_c"],
        hops=3,
    ),
    ScenarioConfig(
        scenario_id="SCENARIO_5",
        title="Cross-Archetype Arbitrage Flow",
        typology="CROSS_ARCHETYPE_ARBITRAGE",
        participating_banks=["bank_a", "bank_b", "bank_c"],
        hops=2,
    ),
    ScenarioConfig(
        scenario_id="SCENARIO_6",
        title="Extreme Rarity Injection",
        typology="SAMPLE_STARVATION",
        participating_banks=["bank_a", "bank_b", "bank_c"],
        hops=2,
    ),
    ScenarioConfig(
        scenario_id="SCENARIO_7",
        title="Cold-Start Zero-Positive Transfer",
        typology="ZERO_SHOT_INSTITUTIONAL_TRANSFER",
        participating_banks=["bank_a", "bank_c"],
        hops=1,
        is_cold_start_target=True,
    ),
]


@dataclass(frozen=True)
class CrossBankV2Config:
    """Master immutable configuration for CrossBank v2 protocol."""

    protocol_version: str = "2.1.0"
    benchmark_id: str = "CFI-CrossBank-02"
    data_provenance: str = DataProvenance.PROJECT_SYNTHETIC.value
    federation_type: str = FederationType.SIMULATED_FEDERATION.value
    currency_unit: str = CurrencyUnit.SYNTHETIC_USD.value

    # Predeclared Frozen Multi-Seed Set (Minimum 5 independent seeds)
    seeds: tuple[int, ...] = (42, 123, 456, 789, 2025)

    # Scale and Temporal Configuration
    timesteps: int = 168  # 1 week of hourly steps (0 to 167)
    train_end_step: int = 112  # Steps 0..112 (67.3% temporal train)
    val_end_step: int = 140  # Steps 113..140 (16.7% temporal val)
    # Test set: Steps 141..167 (16.0% temporal test)

    # Dataset Scale
    canonical_transactions: int = 35000
    smoke_transactions: int = 3500
    target_prevalence: float = 0.012  # Nominal 1.2% fraud prevalence (realistic synthetic proxy)

    # Target Low-FPR Evaluation
    target_fpr: float = 0.001  # 0.1% False Positive Rate operating point
    minimum_test_negatives: int = 5000  # Ensures minimum non-zero FPR <= 0.02%

    # Neural Architecture Hyperparameters (strictly matched across paradigms)
    hidden_dim: int = 48
    dropout_rate: float = 0.2
    learning_rate: float = 0.005
    weight_decay: float = 1e-4
    batch_size: int = 64

    # Optimization Budget Parity
    rounds: int = 5
    local_epochs_per_round: int = 2
    # Total effective client passes = rounds * local_epochs_per_round = 10 epochs
    centralized_epochs: int = 10  # Matched total optimization exposure

    # Institutions & Scenarios
    institutions: tuple[InstitutionConfig, ...] = tuple(DEFAULT_INSTITUTIONS)
    scenarios: tuple[ScenarioConfig, ...] = tuple(DEFAULT_SCENARIOS)

    # Guardrails
    max_single_feature_auc: float = 0.85  # Review threshold for trivial synthetic separability

    def compute_config_hash(self) -> str:
        """Compute deterministic SHA-256 hash of the frozen protocol configuration."""
        dumped = json.dumps(
            {
                "batch_size": self.batch_size,
                "benchmark_id": self.benchmark_id,
                "canonical_transactions": self.canonical_transactions,
                "centralized_epochs": self.centralized_epochs,
                "dropout_rate": self.dropout_rate,
                "hidden_dim": self.hidden_dim,
                "learning_rate": self.learning_rate,
                "local_epochs_per_round": self.local_epochs_per_round,
                "minimum_test_negatives": self.minimum_test_negatives,
                "protocol_version": self.protocol_version,
                "rounds": self.rounds,
                "seeds": list(self.seeds),
                "target_fpr": self.target_fpr,
                "target_prevalence": self.target_prevalence,
                "timesteps": self.timesteps,
                "train_end_step": self.train_end_step,
                "val_end_step": self.val_end_step,
                "weight_decay": self.weight_decay,
            },
            sort_keys=True,
        )
        return hashlib.sha256(dumped.encode("utf-8")).hexdigest()


@dataclass
class PlannedCondition:
    condition_id: str
    condition_type: ConditionType
    description: str
    feature_regime: FeatureRegime
    target_institution: str | None = None
    scientific_question_addressed: str = "Q1"


def get_planned_experiment_matrix() -> list[PlannedCondition]:
    """Generate the canonical planned experiment matrix."""
    return [
        PlannedCondition(
            condition_id="COND_LOCAL_ISOLATED",
            condition_type=ConditionType.LOCAL_ISOLATED,
            description="Independent local training per banking silo with zero cross-bank data or parameter sharing",
            feature_regime=FeatureRegime.LOCAL_ONLY,
            scientific_question_addressed="Q2",
        ),
        PlannedCondition(
            condition_id="COND_FEDERATED_FEDAVG_LOCAL_FEATS",
            condition_type=ConditionType.FEDERATED_FEDAVG,
            description="Federated consensus (FedAvg) over strictly local-only feature spaces across all 3 institutions",
            feature_regime=FeatureRegime.LOCAL_ONLY,
            scientific_question_addressed="Q1",
        ),
        PlannedCondition(
            condition_id="COND_CENTRALIZED_POOLED",
            condition_type=ConditionType.CENTRALIZED_POOLED,
            description="Equally informed centralized pooled upper bound with matched optimization budget",
            feature_regime=FeatureRegime.LOCAL_ONLY,
            scientific_question_addressed="Q3",
        ),
        PlannedCondition(
            condition_id="COND_COLD_START_ZERO_POSITIVE",
            condition_type=ConditionType.COLD_START_ZERO_POSITIVE,
            description="Bank Gamma cold-start transfer: 0 local training positives vs federated parameter transfer",
            feature_regime=FeatureRegime.LOCAL_ONLY,
            target_institution="bank_c",
            scientific_question_addressed="Q4",
        ),
        PlannedCondition(
            condition_id="COND_FEDERATED_CONSORTIUM_SIGNAL",
            condition_type=ConditionType.FEDERATED_FEDAVG,
            description="Federated learning evaluated with simulated oracle consortium signal (ORACLE_UPPER_BOUND_ABLATION)",
            feature_regime=FeatureRegime.CONSORTIUM_SIGNAL,
            scientific_question_addressed="Q1",
        ),
        PlannedCondition(
            condition_id="COND_SIMPLE_BASELINE_LOGISTIC",
            condition_type=ConditionType.SIMPLE_BASELINE_LOGISTIC,
            description="Centralized L2-regularized Logistic Regression to benchmark linear separability",
            feature_regime=FeatureRegime.LOCAL_ONLY,
            scientific_question_addressed="Q3",
        ),
    ]


def compute_experiment_matrix_hash(matrix: list[PlannedCondition]) -> str:
    """Compute deterministic SHA-256 hash of planned experiment matrix."""
    raw = [asdict(c) for c in matrix]
    dumped = json.dumps(raw, sort_keys=True)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()
