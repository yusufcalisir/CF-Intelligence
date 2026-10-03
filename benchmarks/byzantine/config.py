"""Configuration models, deterministic hashing, and execution manifests for Byzantine FL benchmarks.

Provides structured configuration for dataset isolation, federation partitioning,
multi-round training, attack definitions, and robust aggregation preconditions.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

CANONICAL_SEEDS = [42, 123, 456]


class ByzantineAttackType(StrEnum):
    """Supported distributed Byzantine attack modalities."""
    SIGN_FLIP = "sign_flip"
    GAUSSIAN_NOISE = "gaussian_noise"
    ALIE = "alie"
    NONE = "none"


class ByzantineAggregatorType(StrEnum):
    """Supported federated aggregation algorithms."""
    FEDAVG = "fedavg"
    COORDINATE_MEDIAN = "coordinate_median"
    TRIMMED_MEAN = "trimmed_mean"
    KRUM = "krum"
    MULTI_KRUM = "multi_krum"
    BULYAN = "bulyan"


class DatasetConfig(BaseModel):
    """Dataset identity, isolation split, and feature schema configuration."""
    model_config = ConfigDict(extra="ignore")

    dataset_name: str = "credit_card"
    data_path: str = "backend/storage/datasets/creditcard/creditcard.csv"
    physical_sha256: str = "76274b691b16a6c49d3f159c883398e03ccd6d1ee12d9d8ee38f4b4b98551a89"
    use_mock: bool = False
    n_mock_samples: int = 2000
    train_ratio: float = 0.80
    test_ratio: float = 0.20
    split_seed: int = 42
    feature_dim: int = 30


class FederationConfig(BaseModel):
    """Consortium scale, client partition, and Byzantine contamination configuration."""
    model_config = ConfigDict(extra="ignore")

    n_clients: int = 12
    f_byzantine: int = 2
    partition_alpha: float = 0.5
    partition_seed: int = 42
    min_samples_per_client: int = 50
    min_positives_per_client: int = 1


class TrainingConfig(BaseModel):
    """Multi-round neural federated optimization hyperparameters."""
    model_config = ConfigDict(extra="ignore")

    rounds: int = 10
    local_epochs: int = 1
    batch_size: int = 64
    learning_rate: float = 0.005
    hidden_dims: list[int] = Field(default_factory=lambda: [64, 32])
    dropout: float = 0.1
    pos_weight: float = 10.0
    update_representation: str = "MODEL_DELTA"


class AttackConfig(BaseModel):
    """Byzantine adversarial attack parameter specification and knowledge model."""
    model_config = ConfigDict(extra="ignore")

    attack_type: ByzantineAttackType = ByzantineAttackType.SIGN_FLIP
    attack_variant: str = "DEFAULT"
    statistics_source: str = "LOCAL_UPDATE_ONLY"
    std_estimator: str = "SAMPLE_STANDARD_DEVIATION"
    std_correction: int = 1
    std_convention_source: str = "PROJECT_PREDECLARED_CONVENTION"
    direction_rule: str = "CONSTANT_NEGATIVE_OFFSET"
    byzantine_collusion: bool = False
    scale: float = 3.0
    noise_std: float = 1.0
    alie_evaluated_z: float = 1.0
    alie_reference_supporters: int = 5
    alie_reference_probability_boundary: float = 0.5
    alie_reference_z_boundary: float = 0.0
    alie_reference_z_max: float = 0.0
    alie_z_source: str = "PREDECLARED_FIXED_PARAMETER"
    alie_z_max: float = 1.0  # Alias / backward compatibility
    malicious_client_ids: list[int] = Field(default_factory=lambda: [10, 11])
    knows_global_model: bool = True
    knows_own_update: bool = True
    knows_other_byzantine_updates: bool = False
    knows_honest_updates: bool = False
    knows_aggregation_rule: bool = False
    knows_test_data: bool = False


class AggregatorConfig(BaseModel):
    """Aggregator algorithm configuration and theoretical boundary constraints."""
    model_config = ConfigDict(extra="ignore")

    aggregator_type: ByzantineAggregatorType = ByzantineAggregatorType.TRIMMED_MEAN
    trimmed_mean_beta: float = 0.20
    krum_f: int = 2
    multi_krum_m: int | None = 10
    bulyan_f: int = 2


class ByzantineBenchmarkConfig(BaseModel):
    """Root configuration for canonical Byzantine federated robustness experiments."""
    model_config = ConfigDict(extra="ignore")

    benchmark_id: str = "byzantine_federated_canonical"
    schema_version: str = "2.1.0"
    seeds: list[int] = Field(default_factory=lambda: [42, 123, 456])
    actual_byzantine_count: int = 2
    assumed_byzantine_bound: int = 2
    dataset: DatasetConfig = Field(default_factory=DatasetConfig)
    federation: FederationConfig = Field(default_factory=FederationConfig)
    training: TrainingConfig = Field(default_factory=TrainingConfig)
    attacks: list[AttackConfig] = Field(default_factory=list)
    aggregators: list[AggregatorConfig] = Field(default_factory=list)
    is_canonical: bool = False
    config_sha256: str = ""
    condition_matrix_sha256: str = ""
    initial_state_dict_hash_by_seed: dict[int, str] = Field(default_factory=dict)
    partition_sha256_by_seed: dict[int, str] = Field(default_factory=dict)

    def ensure_config_hash(self) -> str:
        """Computes and assigns deterministic SHA-256 hash of canonical configuration."""
        data_to_hash = {
            "benchmark_id": self.benchmark_id,
            "schema_version": self.schema_version,
            "seeds": self.seeds,
            "actual_byzantine_count": self.actual_byzantine_count,
            "assumed_byzantine_bound": self.assumed_byzantine_bound,
            "dataset": self.dataset.model_dump(),
            "federation": self.federation.model_dump(),
            "training": self.training.model_dump(),
            "attacks": [a.model_dump() for a in self.attacks],
            "aggregators": [ag.model_dump() for ag in self.aggregators],
        }
        serialized = json.dumps(data_to_hash, sort_keys=True, separators=(",", ":"))
        self.config_sha256 = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        return self.config_sha256


def create_canonical_byzantine_config() -> ByzantineBenchmarkConfig:
    """Instantiates the authoritative frozen configuration for the canonical Byzantine benchmark."""
    cfg = ByzantineBenchmarkConfig(
        benchmark_id="byzantine_federated_canonical",
        schema_version="2.1.0",
        seeds=[42, 123, 456],
        actual_byzantine_count=2,
        assumed_byzantine_bound=2,
        dataset=DatasetConfig(
            dataset_name="credit_card",
            data_path="backend/storage/datasets/creditcard/creditcard.csv",
            physical_sha256="76274b691b16a6c49d3f159c883398e03ccd6d1ee12d9d8ee38f4b4b98551a89",
            use_mock=False,
            train_ratio=0.80,
            test_ratio=0.20,
            split_seed=42,
            feature_dim=30,
        ),
        federation=FederationConfig(
            n_clients=12,
            f_byzantine=2,
            partition_alpha=0.5,
            partition_seed=42,
            min_samples_per_client=50,
            min_positives_per_client=1,
        ),
        training=TrainingConfig(
            rounds=10,
            local_epochs=1,
            batch_size=64,
            learning_rate=0.005,
            hidden_dims=[64, 32],
            dropout=0.1,
            pos_weight=10.0,
            update_representation="MODEL_DELTA",
        ),
        attacks=[
            AttackConfig(
                attack_type=ByzantineAttackType.SIGN_FLIP,
                scale=3.0,
                malicious_client_ids=[10, 11],
                knows_global_model=True,
                knows_own_update=True,
                knows_honest_updates=False,
                knows_aggregation_rule=False,
                knows_test_data=False,
            ),
            AttackConfig(
                attack_type=ByzantineAttackType.GAUSSIAN_NOISE,
                noise_std=1.0,
                malicious_client_ids=[10, 11],
                knows_global_model=True,
                knows_own_update=True,
                knows_honest_updates=False,
                knows_aggregation_rule=False,
                knows_test_data=False,
            ),
            AttackConfig(
                attack_type=ByzantineAttackType.ALIE,
                attack_variant="OMNISCIENT_ALIE",
                statistics_source="HONEST_CONSORTIUM_UPDATES",
                std_estimator="SAMPLE_STANDARD_DEVIATION",
                std_correction=1,
                std_convention_source="PROJECT_PREDECLARED_CONVENTION",
                direction_rule="CONSTANT_NEGATIVE_OFFSET",
                byzantine_collusion=True,
                alie_evaluated_z=1.0,
                alie_reference_supporters=5,
                alie_reference_probability_boundary=0.5,
                alie_reference_z_boundary=0.0,
                alie_reference_z_max=0.0,
                alie_z_source="PREDECLARED_FIXED_PARAMETER",
                alie_z_max=1.0,
                malicious_client_ids=[10, 11],
                knows_global_model=True,
                knows_own_update=True,
                knows_other_byzantine_updates=True,
                knows_honest_updates=True,
                knows_aggregation_rule=True,
                knows_test_data=False,
            ),
        ],
        aggregators=[
            AggregatorConfig(aggregator_type=ByzantineAggregatorType.FEDAVG),
            AggregatorConfig(aggregator_type=ByzantineAggregatorType.COORDINATE_MEDIAN),
            AggregatorConfig(aggregator_type=ByzantineAggregatorType.TRIMMED_MEAN, trimmed_mean_beta=0.20),
            AggregatorConfig(aggregator_type=ByzantineAggregatorType.KRUM, krum_f=2),
            AggregatorConfig(aggregator_type=ByzantineAggregatorType.MULTI_KRUM, krum_f=2, multi_krum_m=10),
            AggregatorConfig(aggregator_type=ByzantineAggregatorType.BULYAN, bulyan_f=2),
        ],
        is_canonical=True,
    )
    cfg.ensure_config_hash()
    matrix = generate_canonical_condition_matrix(cfg)
    cfg.condition_matrix_sha256 = compute_condition_matrix_sha256(matrix)
    return cfg


def generate_canonical_condition_matrix(config: ByzantineBenchmarkConfig) -> list[dict[str, Any]]:
    """Generates the deduplicated list of experimental conditions across all seeds."""
    conditions: list[dict[str, Any]] = []
    condition_idx = 1

    for seed in config.seeds:
        # 1. Clean FedAvg Baseline (Executed once per seed)
        conditions.append({
            "condition_idx": condition_idx,
            "condition_id": f"seed_{seed}_clean_fedavg",
            "seed": seed,
            "actual_f": 0,
            "assumed_f": 0,
            "attack_type": "none",
            "aggregator_type": "fedavg",
            "parameters": {"sample_weighted": True},
        })
        condition_idx += 1

        # 2. Clean Defense Conditions (Experiment B: actual_f=0, assumed_f=2)
        defense_aggregators = [
            ag for ag in config.aggregators
            if ag.aggregator_type != ByzantineAggregatorType.FEDAVG
        ]
        for ag in defense_aggregators:
            params: dict[str, Any] = {}
            if ag.aggregator_type == ByzantineAggregatorType.TRIMMED_MEAN:
                params["beta"] = ag.trimmed_mean_beta
            elif ag.aggregator_type == ByzantineAggregatorType.KRUM:
                params["krum_f"] = ag.krum_f
            elif ag.aggregator_type == ByzantineAggregatorType.MULTI_KRUM:
                params["krum_f"] = ag.krum_f
                params["m"] = ag.multi_krum_m or (config.federation.n_clients - ag.krum_f)
            elif ag.aggregator_type == ByzantineAggregatorType.BULYAN:
                params["bulyan_f"] = ag.bulyan_f

            conditions.append({
                "condition_idx": condition_idx,
                "condition_id": f"seed_{seed}_clean_{ag.aggregator_type.value}",
                "seed": seed,
                "actual_f": 0,
                "assumed_f": config.assumed_byzantine_bound,
                "attack_type": "none",
                "aggregator_type": ag.aggregator_type.value,
                "parameters": params,
            })
            condition_idx += 1

        # 3. Attacked Conditions (actual_f=2, assumed_f=2)
        for att in config.attacks:
            att_params: dict[str, Any] = {"attack_type": att.attack_type.value}
            if att.attack_type == ByzantineAttackType.SIGN_FLIP:
                att_params["scale"] = att.scale
            elif att.attack_type == ByzantineAttackType.GAUSSIAN_NOISE:
                att_params["noise_std"] = att.noise_std
            elif att.attack_type == ByzantineAttackType.ALIE:
                att_params["attack_variant"] = att.attack_variant
                att_params["statistics_source"] = att.statistics_source
                att_params["std_estimator"] = att.std_estimator
                att_params["std_correction"] = att.std_correction
                att_params["std_convention_source"] = att.std_convention_source
                att_params["direction_rule"] = att.direction_rule
                att_params["byzantine_collusion"] = att.byzantine_collusion
                att_params["alie_evaluated_z"] = att.alie_evaluated_z
                att_params["alie_reference_supporters"] = att.alie_reference_supporters
                att_params["alie_reference_probability_boundary"] = att.alie_reference_probability_boundary
                att_params["alie_reference_z_boundary"] = att.alie_reference_z_boundary
                att_params["alie_reference_z_max"] = att.alie_reference_z_max
                att_params["alie_z_source"] = att.alie_z_source

            cond_attack_id = "alie_omniscient_z1" if att.attack_type == ByzantineAttackType.ALIE else att.attack_type.value

            for ag in config.aggregators:
                agg_params: dict[str, Any] = {}
                if ag.aggregator_type == ByzantineAggregatorType.FEDAVG:
                    agg_params["sample_weighted"] = True
                elif ag.aggregator_type == ByzantineAggregatorType.TRIMMED_MEAN:
                    agg_params["beta"] = ag.trimmed_mean_beta
                elif ag.aggregator_type == ByzantineAggregatorType.KRUM:
                    agg_params["krum_f"] = ag.krum_f
                elif ag.aggregator_type == ByzantineAggregatorType.MULTI_KRUM:
                    agg_params["krum_f"] = ag.krum_f
                    agg_params["m"] = ag.multi_krum_m or (config.federation.n_clients - ag.krum_f)
                elif ag.aggregator_type == ByzantineAggregatorType.BULYAN:
                    agg_params["bulyan_f"] = ag.bulyan_f

                conditions.append({
                    "condition_idx": condition_idx,
                    "condition_id": f"seed_{seed}_{cond_attack_id}_{ag.aggregator_type.value}",
                    "seed": seed,
                    "actual_f": config.actual_byzantine_count,
                    "assumed_f": config.assumed_byzantine_bound,
                    "attack_type": cond_attack_id,
                    "aggregator_type": ag.aggregator_type.value,
                    "parameters": {"attack": att_params, "aggregator": agg_params},
                })
                condition_idx += 1

    return conditions


def compute_condition_matrix_sha256(matrix: list[dict[str, Any]]) -> str:
    """Computes deterministic SHA-256 hash of canonical condition matrix."""
    canonical_repr = json.dumps(matrix, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_repr.encode("utf-8")).hexdigest()


def get_git_sha(repo_root: Path | None = None) -> tuple[str, bool]:
    """Retrieves current Git SHA and clean/dirty working tree status."""
    cwd = repo_root or Path(__file__).resolve().parents[2]
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=cwd, text=True).strip()
        status_out = subprocess.check_output(["git", "status", "--porcelain"], cwd=cwd, text=True).strip()
        is_clean = len(status_out) == 0
        return sha, is_clean
    except Exception:
        return "UNKNOWN", False


def generate_execution_manifest(config: ByzantineBenchmarkConfig, repo_root: Path | None = None) -> dict[str, Any]:
    """Generates execution manifest recording code identity, config hash, and theoretical constraints."""
    git_sha, is_clean = get_git_sha(repo_root)
    cfg_hash = config.ensure_config_hash()
    matrix = generate_canonical_condition_matrix(config)
    matrix_hash = compute_condition_matrix_sha256(matrix)
    config.condition_matrix_sha256 = matrix_hash

    manifest_type = "PRE_EXECUTION_PRE_COMMIT_MANIFEST" if not is_clean else "CANONICAL_EXECUTION_MANIFEST"

    return {
        "manifest_version": "2.1.0",
        "manifest_type": manifest_type,
        "protocol_commit_pending": not is_clean,
        "is_working_tree_clean": is_clean,
        "benchmark_id": config.benchmark_id,
        "config_sha256": cfg_hash,
        "condition_matrix_sha256": matrix_hash,
        "git_sha": git_sha,
        "seeds": config.seeds,
        "consortium_n": config.federation.n_clients,
        "actual_byzantine_count": config.actual_byzantine_count,
        "assumed_byzantine_bound": config.assumed_byzantine_bound,
        "byzantine_fraction": config.federation.f_byzantine / config.federation.n_clients,
        "model_architecture": "FraudMLP(30 -> 64 -> 32 -> 1)",
        "dataset_name": config.dataset.dataset_name,
        "dataset_expected_sha256": config.dataset.physical_sha256,
        "update_representation": config.training.update_representation,
        "attacks_evaluated": [a.attack_type.value for a in config.attacks],
        "aggregators_evaluated": [ag.aggregator_type.value for ag in config.aggregators],
        "total_conditions": len(matrix),
        "conditions_per_seed": len(matrix) // len(config.seeds) if config.seeds else 0,
        "partition_sha256_by_seed": config.partition_sha256_by_seed,
    }
