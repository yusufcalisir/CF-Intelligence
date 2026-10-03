"""Canonical Byzantine Robustness Federated Benchmark Infrastructure.

Provides mathematically verified robust aggregation, adversarial attack injection,
strict train/test isolation, and multi-seed federated training orchestration.
"""

from __future__ import annotations

from benchmarks.byzantine.config import (
    CANONICAL_SEEDS,
    AggregatorConfig,
    AttackConfig,
    ByzantineAggregatorType,
    ByzantineAttackType,
    ByzantineBenchmarkConfig,
    DatasetConfig,
    FederationConfig,
    TrainingConfig,
    compute_condition_matrix_sha256,
    create_canonical_byzantine_config,
    generate_canonical_condition_matrix,
    generate_execution_manifest,
)

__all__ = [
    "CANONICAL_SEEDS",
    "AggregatorConfig",
    "AttackConfig",
    "ByzantineAggregatorType",
    "ByzantineAttackType",
    "ByzantineBenchmarkConfig",
    "DatasetConfig",
    "FederationConfig",
    "TrainingConfig",
    "compute_condition_matrix_sha256",
    "create_canonical_byzantine_config",
    "generate_canonical_condition_matrix",
    "generate_execution_manifest",
]
