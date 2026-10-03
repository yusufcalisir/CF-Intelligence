"""Pydantic schema definitions for canonical Byzantine benchmark artifacts.

Validates machine-readable provenance, federation topology, multi-seed aggregation,
theoretical constraint satisfaction, and communication boundaries.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ByzantineClientMetadata(BaseModel):
    """Metadata specification for an individual consortium client."""
    model_config = ConfigDict(extra="ignore")

    client_id: int
    sample_count: int
    positive_count: int
    negative_count: int
    prevalence: float
    is_malicious: bool = False


class ByzantineRoundDiagnostic(BaseModel):
    """Per-round update geometry and optimization diagnostics."""
    model_config = ConfigDict(extra="ignore")

    round_idx: int
    honest_norm_mean: float
    honest_norm_std: float
    malicious_norm_mean: float | None = None
    aggregated_norm: float
    cosine_similarity_mean: float | None = None
    pairwise_distance_mean: float | None = None


class ByzantineConditionResult(BaseModel):
    """Evaluation metrics for a specific (seed, attack, aggregator) condition.

    Semantics:
    - Primary evaluation metrics (test_pr_auc, test_roc_auc, f1_score) are bounded in [0.0, 1.0].
    - retention_of_clean is a dimensionless ratio in [0.0, +inf) comparing condition AP against same-seed clean FedAvg AP.
      Values > 1.0 are mathematically valid and indicate AP exceeding clean FedAvg for that seed replicate.
    - clean_penalty is a legacy serialized field representing relative retention loss (1.0 - retention_of_clean).
      It is NOT an absolute AP difference. If clean defense AP > clean FedAvg AP, this value is negative.
    """
    model_config = ConfigDict(extra="ignore")

    seed: int
    attack_name: str
    aggregator_name: str
    actual_f: int = 2
    assumed_f: int = 2
    test_pr_auc: float
    test_roc_auc: float
    retention_of_clean: float  # Primary retention ratio: AP(condition) / AP(clean_fedavg, same_seed) in [0, +inf)
    defense_self_retention: float | None = None  # Diagnostic: AP(condition) / AP(clean_same_defense, same_seed)
    clean_penalty: float | None = None  # Legacy field: relative loss = 1.0 - (AP(clean_defense) / AP(clean_fedavg))
    f1_score: float | None = None
    rounds_completed: int = 10

    @property
    def clean_relative_loss(self) -> float | None:
        """Explicit alias for relative retention loss: 1.0 - retention_of_clean."""
        return self.clean_penalty

    @property
    def clean_retention_ratio(self) -> float | None:
        """Retention ratio of clean defense relative to clean FedAvg."""
        if self.clean_penalty is not None:
            return 1.0 - self.clean_penalty
        return self.retention_of_clean if self.attack_name == "none" else None

    def compute_clean_ap_difference(self, clean_fedavg_ap: float) -> float | None:
        """Computes absolute AP difference: AP(condition) - AP(clean_fedavg)."""
        return self.test_pr_auc - clean_fedavg_ap


class ByzantineAggregatedMetric(BaseModel):
    """Multi-seed statistical summary across replicates.

    Semantics:
    - pr_auc_mean / roc_auc_mean: sample means across evaluated seeds.
    - pr_auc_std / roc_auc_std: sample standard deviations (ddof=1).
    - retention_ratio_mean: sample mean of condition AP / clean FedAvg AP (can be > 1.0).
    - defense_self_retention_mean: sample mean of condition AP / clean same-defense AP.
    - clean_penalty_mean: sample mean of legacy relative loss (1.0 - clean_retention_ratio).
    """
    model_config = ConfigDict(extra="ignore")

    condition_name: str
    attack_name: str
    aggregator_name: str
    actual_f: int = 2
    assumed_f: int = 2
    pr_auc_mean: float
    pr_auc_std: float
    roc_auc_mean: float
    roc_auc_std: float
    retention_ratio_mean: float
    retention_ratio_std: float
    defense_self_retention_mean: float | None = None
    clean_penalty_mean: float | None = None  # Legacy field: relative loss mean = 1.0 - clean_retention_ratio_mean
    n_seeds: int = 3

    @property
    def clean_relative_loss_mean(self) -> float | None:
        """Explicit alias for clean_penalty_mean representing relative retention loss."""
        return self.clean_penalty_mean


class ByzantineBenchmarkArtifact(BaseModel):
    """Complete machine-readable canonical artifact schema."""
    model_config = ConfigDict(extra="ignore")

    benchmark_id: str = "byzantine_federated_canonical"
    schema_version: str = "2.1.0"
    status: str = "NOT_EVALUATED"
    lifecycle: str = "PENDING_EXECUTION"
    timestamp_utc: str
    git_sha: str
    is_working_tree_clean: bool = True
    config_sha256: str
    condition_matrix_sha256: str = ""
    initial_state_dict_hash_by_seed: dict[int, str] = Field(default_factory=dict)
    partition_sha256_by_seed: dict[int, str] = Field(default_factory=dict)

    dataset: dict[str, Any]
    federation: dict[str, Any]
    training: dict[str, Any]
    seeds: list[int]

    attacks_evaluated: list[str]
    aggregators_evaluated: list[str]

    per_seed_results: list[ByzantineConditionResult] = Field(default_factory=list)
    aggregated_results: list[ByzantineAggregatedMetric] = Field(default_factory=list)
    round_diagnostics: list[ByzantineRoundDiagnostic] = Field(default_factory=list)

    mandatory_caveat: str
    communication_scope: str
