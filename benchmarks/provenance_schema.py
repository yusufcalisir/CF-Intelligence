"""Canonical Benchmark Provenance and Scientific Status Schema.

Provides structured, versioned, machine-readable provenance, execution budget,
per-seed metrics, mathematical invariant validation, and scientific status tracking
for all benchmarks across CF-Intelligence.
"""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

EMPTY_STRING_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class DatasetProvenanceType(StrEnum):
    """Scientific taxonomy classification for benchmark datasets."""
    REAL_DATA = "REAL_DATA"
    EXTERNALLY_SIMULATED = "EXTERNALLY_SIMULATED"
    PROJECT_SYNTHETIC = "PROJECT_SYNTHETIC"
    CONTROLLED_TESTBED = "CONTROLLED_TESTBED"
    AMBIGUOUS = "AMBIGUOUS"


class ArtifactStatus(StrEnum):
    """Machine-readable lifecycle and scientific authority status of benchmark artifacts."""
    CANONICAL = "CANONICAL"
    HISTORICAL = "HISTORICAL"
    SUPERSEDED = "SUPERSEDED"
    SUPERSEDED_PLACEHOLDER = "SUPERSEDED_PLACEHOLDER"
    EXPERIMENTAL = "EXPERIMENTAL"
    SMOKE_TEST = "SMOKE_TEST"
    PLACEHOLDER = "PLACEHOLDER"
    FAILED = "FAILED"
    NOT_EVALUATED = "NOT_EVALUATED"
    AMBIGUOUS = "AMBIGUOUS"


class EvidenceScope(StrEnum):
    """Scientific evidence validity and external generalizability scope."""
    REAL_WORLD_BENCHMARK = "REAL_WORLD_BENCHMARK"
    EXTERNAL_SIMULATION_BENCHMARK = "EXTERNAL_SIMULATION_BENCHMARK"
    CONTROLLED_EXPERIMENT = "CONTROLLED_EXPERIMENT"
    PROJECT_SYNTHETIC_EXPERIMENT = "PROJECT_SYNTHETIC_EXPERIMENT"
    SMOKE_ONLY = "SMOKE_ONLY"


class CommunicationEligibility(StrEnum):
    """Strict gate for external marketing, public reports, and commercial claims."""
    SAFE = "SAFE"
    SAFE_WITH_CAVEAT = "SAFE_WITH_CAVEAT"
    INTERNAL_ONLY = "INTERNAL_ONLY"
    HISTORICAL_ONLY = "HISTORICAL_ONLY"


class StatisticalSummary(BaseModel):
    """Statistical aggregate summary across multi-seed benchmark executions."""
    model_config = ConfigDict(extra="ignore")

    mean: float
    std: float
    min: float | None = None
    max: float | None = None
    ci95: tuple[float, float] | None = None


class ExecutionBudget(BaseModel):
    """Standardized training budget tracking for fair centralized vs federated comparison."""
    model_config = ConfigDict(extra="ignore")

    budget_equalized: bool = True
    centralized_epochs: int | None = None
    centralized_optimizer_steps: int | None = None
    fl_rounds: int | None = None
    fl_local_epochs: int | None = None
    fl_total_client_steps: int | None = None
    effective_dataset_passes: int | None = None
    batch_size: int = 64
    learning_rate: float = 0.001


class DatasetProvenance(BaseModel):
    """Exhaustive dataset provenance and metadata specification."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    dataset_name: str
    dataset_type: DatasetProvenanceType
    source_uri: str
    sha256_hash: str | None = Field(default=None, alias="sha256")
    physical_source_sha256: str | None = None
    logical_configuration_hash: str | None = None
    generator_path: str | None = None
    generator_seed: int | None = None
    generator_parameters: dict[str, Any] | None = None
    total_samples: int | None = None
    evaluated_samples: int | None = None
    fraud_samples: int | None = None
    fraud_prevalence_pct: float | None = None
    feature_count: int | None = None
    sampling_strategy: str | None = None
    split_method: str | None = None
    split_ratios: dict[str, float] | None = None
    train_samples: int | None = None
    test_samples: int | None = None
    train_fraud_count: int | None = None
    test_fraud_count: int | None = None
    client_partition_method: str | None = None
    client_count: int | None = None
    client_sample_counts: dict[str, int] | None = None
    client_fraud_counts: dict[str, int] | None = None

    @model_validator(mode="after")
    def validate_provenance_invariants(self) -> DatasetProvenance:
        """Enforce strict scientific provenance rules."""
        # 1. Reject empty-string SHA-256 in any provenance field
        if self.sha256_hash == EMPTY_STRING_SHA256:
            raise ValueError(
                f"Empty-string SHA-256 ({EMPTY_STRING_SHA256}) cannot be used as valid dataset provenance."
            )
        if self.physical_source_sha256 == EMPTY_STRING_SHA256:
            raise ValueError(
                f"Empty-string SHA-256 ({EMPTY_STRING_SHA256}) cannot be used as valid physical dataset provenance."
            )

        # 2. REAL_DATA and EXTERNALLY_SIMULATED must have non-empty physical hash
        if self.dataset_type in (DatasetProvenanceType.REAL_DATA, DatasetProvenanceType.EXTERNALLY_SIMULATED):
            has_hash = bool(self.sha256_hash and self.sha256_hash.strip()) or bool(
                self.physical_source_sha256 and self.physical_source_sha256.strip()
            )
            if not has_hash:
                raise ValueError(
                    f"Physical dataset evidence required: {self.dataset_type} for '{self.dataset_name}' "
                    f"must supply a valid, non-empty sha256_hash."
                )

        return self


class OperationalOperatingPoint(BaseModel):
    """Operational metric snapshot at a specific calibrated decision threshold."""
    model_config = ConfigDict(extra="ignore")

    target_fpr: float
    empirical_fpr: float | None = None
    threshold: float
    precision: float | None = None
    recall: float
    f1_score: float | None = None
    tp: int | None = None
    fp: int | None = None
    tn: int | None = None
    fn: int | None = None
    positive_support: int | None = None
    negative_support: int | None = None
    calibration_source: str = "validation_set"


class CanonicalBenchmarkArtifact(BaseModel):
    """Canonical versioned benchmark artifact container."""
    model_config = ConfigDict(extra="ignore")

    schema_version: str = "2.2.0"
    benchmark_id: str
    status: ArtifactStatus
    evidence_scope: EvidenceScope = EvidenceScope.CONTROLLED_EXPERIMENT
    communication_eligibility: CommunicationEligibility = CommunicationEligibility.INTERNAL_ONLY
    superseded_by: str | None = None
    supersession_reason: str | None = None
    provenance: DatasetProvenance
    budget: ExecutionBudget | None = None
    seeds: list[int] = Field(default_factory=list)
    per_seed_results: list[dict[str, Any]] = Field(default_factory=list)
    aggregate_metrics: dict[str, Any] = Field(default_factory=dict)
    operating_points: list[OperationalOperatingPoint] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    generated_at_utc: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    git_commit: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def validate_scientific_invariants(self) -> CanonicalBenchmarkArtifact:
        """Enforce machine-verifiable scientific invariants on canonical benchmark artifacts."""
        # Invariant 1: CANONICAL status cannot masquerade as NOT_EVALUATED, PLACEHOLDER, or SUPERSEDED
        if self.status == ArtifactStatus.CANONICAL:
            if not self.benchmark_id or not self.benchmark_id.strip():
                raise ValueError("Canonical artifact must have a valid non-empty benchmark_id.")

            # Invariant 2: Multi-seed declaration requires matching non-empty per_seed_results
            if self.seeds:
                if not self.per_seed_results:
                    raise ValueError(
                        f"Canonical multi-seed benchmark '{self.benchmark_id}' declares seeds {self.seeds} "
                        f"but per_seed_results is empty."
                    )
                if len(self.per_seed_results) != len(self.seeds):
                    raise ValueError(
                        f"Multi-seed count mismatch for '{self.benchmark_id}': declared {len(self.seeds)} seeds "
                        f"({self.seeds}) but per_seed_results has {len(self.per_seed_results)} entries."
                    )

            # Invariant 3: Automatic mathematical reconciliation of aggregate metrics
            if self.aggregate_metrics and self.per_seed_results:
                for key in ("centralized_pr_auc", "fedavg_pr_auc", "pr_auc"):
                    if key in self.aggregate_metrics:
                        agg = self.aggregate_metrics[key]
                        if isinstance(agg, dict) and "mean" in agg and "std" in agg:
                            vals: list[float] = []
                            for s in self.per_seed_results:
                                if key in s and isinstance(s[key], (int, float)):
                                    vals.append(float(s[key]))
                                elif "centralized" in s and key == "centralized_pr_auc" and "pr_auc" in s["centralized"]:
                                    vals.append(float(s["centralized"]["pr_auc"]))
                                elif "fedavg" in s and key == "fedavg_pr_auc" and "pr_auc" in s["fedavg"]:
                                    vals.append(float(s["fedavg"]["pr_auc"]))
                            if vals and len(vals) == len(self.per_seed_results):
                                validate_per_seed_aggregate(
                                    vals,
                                    float(agg["mean"]),
                                    float(agg["std"]),
                                    tolerance=2e-3,
                                    label=f"{self.benchmark_id}.{key}",
                                )

        # Invariant 4: NOT_EVALUATED status forbids non-null measured performance numbers
        elif self.status == ArtifactStatus.NOT_EVALUATED:
            if self.per_seed_results:
                raise ValueError(
                    f"NOT_EVALUATED artifact '{self.benchmark_id}' cannot contain per_seed_results."
                )
            if self.aggregate_metrics:
                for k, v in self.aggregate_metrics.items():
                    if v is not None:
                        if isinstance(v, dict):
                            for subk, subv in v.items():
                                if subv is not None and isinstance(subv, (int, float)):
                                    raise ValueError(
                                        f"NOT_EVALUATED artifact '{self.benchmark_id}' cannot contain "
                                        f"non-null measured performance metrics ({k}.{subk}={subv})."
                                    )
                        elif isinstance(v, (int, float)):
                            raise ValueError(
                                f"NOT_EVALUATED artifact '{self.benchmark_id}' cannot contain "
                                f"non-null measured performance metrics ({k}={v})."
                            )

        return self


def verify_physical_source_hash(
    file_path: Path | str,
    expected_sha256: str | None,
) -> tuple[str, str | None]:
    """Verify physical file on disk against expected SHA-256.

    Returns (status, computed_hash):
    - ('SOURCE_MISSING', None) if file does not exist
    - ('HASH_MISMATCH', computed_hash) if computed != expected
    - ('HASH_VERIFIED', computed_hash) if computed == expected
    - ('NO_HASH_SPECIFIED', computed_hash) if expected_sha256 is None
    """
    p = Path(file_path)
    if not p.is_file():
        return ("SOURCE_MISSING", None)

    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    digest = h.hexdigest()

    if expected_sha256 is None:
        return ("NO_HASH_SPECIFIED", digest)
    if digest.lower() == expected_sha256.lower():
        return ("HASH_VERIFIED", digest)
    return ("HASH_MISMATCH", digest)


def validate_per_seed_aggregate(
    values: list[float],
    reported_mean: float,
    reported_std: float,
    tolerance: float = 1e-3,
    label: str = "metric",
    ddof: int | None = None,
) -> None:
    """Validate that reported mean and standard deviation match per-seed runs.

    If ddof is None, validates that reported_std matches either sample std (ddof=1)
    or population std (ddof=0, standard NumPy default).
    Raises ValueError if mathematical discrepancy exceeds tolerance.
    """
    if not values:
        raise ValueError(f"Cannot validate empty per-seed values for {label}")

    n = len(values)
    computed_mean = sum(values) / n
    if abs(computed_mean - reported_mean) > tolerance:
        raise ValueError(
            f"Mathematical validation failed for {label} mean: "
            f"computed {computed_mean:.6f} != reported {reported_mean:.6f} (tolerance={tolerance})"
        )

    if n > 1:
        ss = sum((x - computed_mean) ** 2 for x in values)
        sample_std = math.sqrt(ss / (n - 1))
        pop_std = math.sqrt(ss / n)

        if ddof == 1:
            if abs(sample_std - reported_std) > tolerance:
                raise ValueError(
                    f"Mathematical validation failed for {label} sample std (ddof=1): "
                    f"computed {sample_std:.6f} != reported {reported_std:.6f} (tolerance={tolerance})"
                )
        elif ddof == 0:
            if abs(pop_std - reported_std) > tolerance:
                raise ValueError(
                    f"Mathematical validation failed for {label} population std (ddof=0): "
                    f"computed {pop_std:.6f} != reported {reported_std:.6f} (tolerance={tolerance})"
                )
        else:
            if abs(sample_std - reported_std) > tolerance and abs(pop_std - reported_std) > tolerance:
                raise ValueError(
                    f"Mathematical validation failed for {label} std: "
                    f"reported {reported_std:.6f} matches neither sample std ({sample_std:.6f}, ddof=1) "
                    f"nor population std ({pop_std:.6f}, ddof=0) within tolerance={tolerance}"
                )


def validate_retention_ratio(
    federated_val: float,
    centralized_val: float,
    reported_ratio: float,
    tolerance: float = 1e-3,
    label: str = "retention_ratio",
) -> None:
    """Validate that reported retention / parity ratio equals federated / centralized."""
    if centralized_val == 0.0:
        raise ValueError(f"Cannot compute {label} with zero centralized denominator")

    computed_ratio = federated_val / centralized_val
    if abs(computed_ratio - reported_ratio) > tolerance:
        raise ValueError(
            f"Mathematical validation failed for {label}: "
            f"computed {computed_ratio:.6f} != reported {reported_ratio:.6f} (tolerance={tolerance})"
        )
