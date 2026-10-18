"""Training Job Authority Persistence Backends.

Provides concrete database backends for the Training Execution Authority:
- TrainingJobAuthorityBackend: Abstract contract
- PostgresTrainingJobAuthority: Multi-host relational production authority backed by psycopg 3
- SQLiteTrainingJobAuthority: Development and single-host WAL-mode authority
"""

from __future__ import annotations

import abc
import contextlib
import dataclasses
import hashlib
import json
import logging
import math
import os
import secrets
import sqlite3
import threading
import time
import types
import urllib.parse
import uuid
from collections.abc import Generator, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, LiteralString

try:
    import psycopg
    from psycopg import sql as pg_sql
    from psycopg.rows import dict_row as pg_dict_row
except ImportError:
    psycopg = None  # type: ignore[assignment]
    pg_sql = None  # type: ignore[assignment]
    pg_dict_row = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core Provenance Exception Classes
# ---------------------------------------------------------------------------


class TrainingProvenanceConfigurationError(Exception):
    """Raised when training provenance or authority configuration is invalid, missing, or insecure."""

    pass


class TrainingProvenanceSecurityError(ValueError, PermissionError):
    """Raised when an unauthorized, forged, conflicting, or mismatched training event is detected."""

    pass


def redact_database_url(url: str) -> str:
    """Redact sensitive password credentials from database URLs for safe logging and error reporting."""
    if not url:
        return ""
    try:
        clean = url.strip()
        parsed = urllib.parse.urlsplit(clean)
        if parsed.password:
            netloc = parsed.netloc.replace(f":{parsed.password}@", ":***@")
            return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
        return clean
    except Exception:
        import re
        return re.sub(r":([^@/:]+)@", ":***@", url)


def sanitize_database_error_message(msg: str) -> str:
    """Scrub database URLs, passwords, and sensitive connection credentials from error messages and logs."""
    if not msg:
        return ""
    import re

    # Scrub standard URI pattern: postgresql://user:password@host
    scrubbed = re.sub(r"://([^:@/\s]+):([^@/\s]+)@", r"://\1:***@", msg)
    # Scrub password=xyz, password='xyz', password="xyz" parameters
    scrubbed = re.sub(r"""(password|pwd)\s*=\s*(?:'[^']*'|"[^"]*"|[^\s;,]+)""", r"\1=***", scrubbed, flags=re.IGNORECASE)
    # Scrub standalone password credentials in connection strings
    scrubbed = re.sub(r":([^@/:\s]{3,})@", r":***@", scrubbed)
    return scrubbed


# ---------------------------------------------------------------------------
# Evidence & Observation Dataclasses
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ScheduledTrainingJob:
    """Represents a training job scheduled by the authorized training coordinator."""

    job_id: str
    training_run_id: str
    simulation_id: str
    coordinator_id: str
    architecture: str
    feature_schema_version: str
    expected_rounds: int
    expected_epochs: int
    input_dim: int
    feature_names: Sequence[str] = ()
    permitted_workers: Sequence[str] = ()
    config_fingerprint: str = ""
    scheduled_at: str = ""
    status: str = "SCHEDULED"
    consortium_id: str | None = None
    dp_config: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if isinstance(self.feature_names, (list, tuple)):
            object.__setattr__(self, "feature_names", tuple(self.feature_names))
        if isinstance(self.permitted_workers, (list, tuple)):
            object.__setattr__(self, "permitted_workers", tuple(self.permitted_workers))


@dataclasses.dataclass(frozen=True)
class InstitutionalPrivacyBudget:
    """Authoritative durable record of an institution's privacy budget for a protected dataset."""

    bank_id: str
    dataset_tag: str
    allocated_epsilon: float
    allocated_delta: float
    spent_epsilon: float = 0.0
    spent_delta: float = 0.0
    reserved_epsilon: float = 0.0
    reserved_delta: float = 0.0
    total_steps_executed: int = 0
    active_reservations: dict[str, Any] = dataclasses.field(default_factory=dict)
    history: list[Any] = dataclasses.field(default_factory=list)
    last_job_id: str | None = None
    created_at: str | None = None
    updated_at: str | float = 0.0

    @property
    def remaining_epsilon(self) -> float:
        return max(0.0, self.allocated_epsilon - (self.spent_epsilon + self.reserved_epsilon))

    @property
    def remaining_delta(self) -> float:
        return max(0.0, self.allocated_delta - (self.spent_delta + self.reserved_delta))


@dataclasses.dataclass(frozen=True)
class StartedTrainingJob:
    """Represents an active training job started by an authorized training worker."""

    job_id: str
    training_run_id: str
    simulation_id: str
    worker_id: str
    started_at: str
    status: str = "STARTED"


@dataclasses.dataclass(frozen=True)
class RoundObservation:
    """Immutable record of an authorized training round observation."""

    event_id: str
    job_id: str
    training_run_id: str
    simulation_id: str
    worker_id: str
    bank_id: str
    round_number: int
    attempt_number: int
    actual_sample_count: int
    actual_executed_epochs: int
    observed_loss: float | None = None
    metrics: dict[str, float] = dataclasses.field(default_factory=dict)
    execution_timestamp: str = ""
    model_update_digest: str | None = None
    execution_status: str = "COMPLETED"

    def __post_init__(self) -> None:
        if isinstance(self.metrics, dict):
            object.__setattr__(
                self,
                "metrics",
                types.MappingProxyType(dict(self.metrics)),
            )

    def validate(self) -> None:
        if not self.event_id or not self.event_id.strip():
            raise ValueError("event_id must be non-empty.")
        if not self.job_id or not self.job_id.strip():
            raise ValueError("job_id must be non-empty.")
        if not self.worker_id or not self.worker_id.strip():
            raise ValueError("worker_id must be non-empty.")
        if self.round_number < 1:
            raise ValueError(f"round_number must be >= 1, got {self.round_number}.")
        if self.attempt_number < 1:
            raise ValueError(f"attempt_number must be >= 1, got {self.attempt_number}.")
        if self.actual_sample_count <= 0:
            raise ValueError(f"actual_sample_count must be positive, got {self.actual_sample_count}.")
        if self.observed_loss is not None and (
            not isinstance(self.observed_loss, (int, float)) or not math.isfinite(self.observed_loss)
        ):
            raise ValueError(f"observed_loss must be a finite float if provided, got {self.observed_loss!r}.")
        if self.execution_status not in ("COMPLETED", "FAILED"):
            raise ValueError(f"Invalid execution_status: {self.execution_status!r}.")

    @property
    def payload_digest(self) -> str:
        """Deterministic digest of observation content for idempotency and conflict detection."""
        h = hashlib.sha256()
        h.update(str(self.round_number).encode())
        h.update(str(self.attempt_number).encode())
        h.update(str(self.actual_sample_count).encode())
        h.update(str(self.actual_executed_epochs).encode())
        if self.observed_loss is not None:
            h.update(f"{float(self.observed_loss):.8f}".encode())
        else:
            h.update(b"observed_loss:none")
        for k in sorted(self.metrics.keys()):
            h.update(f"{k}:{float(self.metrics[k]):.8f}".encode())
        if self.model_update_digest:
            h.update(self.model_update_digest.encode())
        h.update(self.execution_status.encode())
        return h.hexdigest()


@dataclasses.dataclass(frozen=True)
class GlobalAggregationEvent:
    """Immutable record of an authorized federated aggregation operation."""

    event_id: str
    job_id: str
    round_number: int
    aggregation_algorithm: str
    accepted_participant_ids: tuple[str, ...]
    accepted_local_update_digests: tuple[str, ...]
    aggregation_configuration: dict[str, Any]
    aggregated_global_model_digest: str
    timestamp: str
    aggregation_status: str = "COMPLETED"

    def __post_init__(self) -> None:
        if isinstance(self.accepted_participant_ids, list):
            object.__setattr__(self, "accepted_participant_ids", tuple(self.accepted_participant_ids))
        if isinstance(self.accepted_local_update_digests, list):
            object.__setattr__(self, "accepted_local_update_digests", tuple(self.accepted_local_update_digests))
        if isinstance(self.aggregation_configuration, dict):
            object.__setattr__(
                self,
                "aggregation_configuration",
                types.MappingProxyType(dict(self.aggregation_configuration)),
            )

    def validate(self) -> None:
        if not self.event_id or not self.event_id.strip():
            raise ValueError("event_id must be non-empty.")
        if not self.job_id or not self.job_id.strip():
            raise ValueError("job_id must be non-empty.")
        if self.round_number < 1:
            raise ValueError(f"round_number must be >= 1, got {self.round_number}.")
        if not self.accepted_participant_ids:
            raise ValueError("accepted_participant_ids cannot be empty.")
        if not self.accepted_local_update_digests:
            raise ValueError("accepted_local_update_digests cannot be empty.")
        if len(self.accepted_participant_ids) != len(self.accepted_local_update_digests):
            raise ValueError("Count of accepted_participant_ids must match accepted_local_update_digests.")
        if not self.aggregated_global_model_digest or len(self.aggregated_global_model_digest) != 64:
            raise ValueError("aggregated_global_model_digest must be a 64-char hex SHA-256.")

    @property
    def payload_digest(self) -> str:
        h = hashlib.sha256()
        h.update(self.job_id.strip().encode())
        h.update(str(self.round_number).encode())
        h.update(self.aggregation_algorithm.strip().encode())
        for p in self.accepted_participant_ids:
            h.update(p.strip().encode())
        for d in self.accepted_local_update_digests:
            h.update(d.strip().encode())
        h.update(self.aggregated_global_model_digest.strip().encode())
        h.update(self.aggregation_status.strip().encode())
        return h.hexdigest()


@dataclasses.dataclass(frozen=True)
class ModelEvaluationEvent:
    """Immutable record of an authorized model evaluation."""

    event_id: str
    job_id: str
    evaluation_run_id: str
    metric_name: str
    metric_value: float
    evaluated_artifact_digest: str
    evaluator_identity: str
    evaluation_timestamp: str
    evaluation_dataset_manifest: str | None = None

    def validate(self) -> None:
        if not self.event_id or not self.event_id.strip():
            raise ValueError("event_id must be non-empty.")
        if not self.job_id or not self.job_id.strip():
            raise ValueError("job_id must be non-empty.")
        if not self.metric_name or not self.metric_name.strip():
            raise ValueError("metric_name must be non-empty.")
        if not isinstance(self.metric_value, (int, float)) or not math.isfinite(self.metric_value):
            raise ValueError(f"metric_value must be a finite float, got {self.metric_value!r}.")
        if not self.evaluated_artifact_digest or len(self.evaluated_artifact_digest) != 64:
            raise ValueError("evaluated_artifact_digest must be a 64-char hex SHA-256.")

    @property
    def payload_digest(self) -> str:
        h = hashlib.sha256()
        h.update(self.job_id.strip().encode())
        h.update(self.evaluation_run_id.strip().encode())
        h.update(self.metric_name.strip().encode())
        h.update(f"{float(self.metric_value):.8f}".encode())
        h.update(self.evaluated_artifact_digest.strip().encode())
        h.update(self.evaluator_identity.strip().encode())
        if self.evaluation_dataset_manifest:
            h.update(self.evaluation_dataset_manifest.strip().encode())
        return h.hexdigest()


@dataclasses.dataclass(frozen=True)
class CompletedJobExecutionRecord:
    """Authoritative, persisted record of a successfully completed training execution."""

    job_id: str
    training_run_id: str
    simulation_id: str
    completed_at: str
    executed_rounds: int
    executed_epochs: int
    sample_count: int
    observed_metrics: dict[str, float]
    artifact_sha256: str
    architecture: str = "FraudDetectionNN"
    feature_schema_version: str = "v1.0"
    input_dim: int = 10
    feature_names: Sequence[str] = ()
    worker_id: str = "TRAINING_EXECUTOR"
    status: str = "COMPLETED"
    git_commit_hash: str | None = None
    dataset_manifest_id: str | None = None
    consortium_id: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.observed_metrics, dict):
            object.__setattr__(
                self,
                "observed_metrics",
                types.MappingProxyType(dict(self.observed_metrics)),
            )
        if isinstance(self.feature_names, (list, tuple)):
            object.__setattr__(self, "feature_names", tuple(self.feature_names))
        if self.feature_names and (self.input_dim is None or self.input_dim == 0):
            object.__setattr__(self, "input_dim", len(self.feature_names))

    def validate(self) -> None:
        if not self.job_id or not self.job_id.strip():
            raise ValueError("job_id must be non-empty.")
        if not self.training_run_id or not self.training_run_id.strip():
            raise ValueError("training_run_id must be non-empty.")
        if not self.simulation_id or not self.simulation_id.strip():
            raise ValueError("simulation_id must be non-empty.")
        if not self.artifact_sha256 or len(self.artifact_sha256) != 64:
            raise ValueError(f"artifact_sha256 must be a 64-char hex string, got {self.artifact_sha256!r}.")
        if self.executed_rounds < 0:
            raise ValueError("executed_rounds must be non-negative.")
        if self.executed_epochs < 0:
            raise ValueError("executed_epochs must be non-negative.")
        if self.sample_count <= 0:
            raise ValueError("sample_count must be positive.")
        for k, v in self.observed_metrics.items():
            if not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ValueError(f"Metric '{k}' must be a finite float, got {v!r}.")


def canonicalize_dataset_tag(bank_id: str, dataset_tag: str) -> str:
    """Resolve caller-supplied dataset tag to canonical institutional protected dataset identity.

    Guarantees:
    - Protects cumulative budget namespace from client aliases and consortium changes.
    - Normalizes alias variations to prevent budget evasion.
    """
    clean_tag = dataset_tag.strip()
    if not clean_tag:
        return "canonical_fraud_partition"
    clean_lower = clean_tag.lower()
    for suffix in ("_alias", "_copy", "_link", "_ref"):
        if clean_lower.endswith(suffix):
            clean_lower = clean_lower[: -len(suffix)]
            break
    return clean_lower


def validate_dp_config(dp_config: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Validates DP configuration parameters, failing closed on invalid or missing requirements."""
    if dp_config is None:
        return None
    if not isinstance(dp_config, (dict, Mapping)):
        raise TrainingProvenanceSecurityError("dp_config must be a dictionary or mapping if provided.")
    clean = dict(dp_config)
    dp_enabled = clean.get("dp_enabled", False)
    if not isinstance(dp_enabled, bool):
        raise TrainingProvenanceSecurityError("dp_config['dp_enabled'] must be a boolean.")
    if not dp_enabled:
        return {"dp_enabled": False}

    target_epsilon = clean.get("target_epsilon")
    if (
        target_epsilon is None
        or not isinstance(target_epsilon, (int, float))
        or not math.isfinite(target_epsilon)
        or target_epsilon <= 0.0
    ):
        raise TrainingProvenanceSecurityError(
            f"DP-enabled job requires finite positive target_epsilon, got: {target_epsilon}"
        )

    target_delta = clean.get("target_delta")
    if target_delta is None:
        target_delta = 1e-5
        clean["target_delta"] = target_delta
    if (
        not isinstance(target_delta, (int, float))
        or not math.isfinite(target_delta)
        or not (0.0 < target_delta < 1.0)
    ):
        raise TrainingProvenanceSecurityError(
            f"DP-enabled job requires target_delta in (0, 1), got: {target_delta}"
        )

    max_grad_norm = clean.get("max_grad_norm", 0.5)
    if (
        not isinstance(max_grad_norm, (int, float))
        or not math.isfinite(max_grad_norm)
        or max_grad_norm <= 0.0
    ):
        raise TrainingProvenanceSecurityError(
            f"DP-enabled job requires finite positive max_grad_norm, got: {max_grad_norm}"
        )

    noise_multiplier = clean.get("noise_multiplier")
    if noise_multiplier is not None and (
        not isinstance(noise_multiplier, (int, float))
        or not math.isfinite(noise_multiplier)
        or noise_multiplier <= 0.0
    ):
        raise TrainingProvenanceSecurityError(
            f"noise_multiplier must be positive and finite if provided, got: {noise_multiplier}"
        )

    raw_tag = str(clean.get("dataset_tag", "canonical_fraud_partition")).strip()
    if not raw_tag:
        raise TrainingProvenanceSecurityError(
            "DP-enabled job requires non-empty dataset_tag identifying the protected dataset."
        )
    dataset_tag = canonicalize_dataset_tag("BANK_ANY", raw_tag)

    accountant_raw = clean.get("accountant_type") or clean.get("accountant") or "prv"
    accountant_type = str(accountant_raw).strip().lower().replace("opacus_", "")
    if accountant_type not in ("prv", "rdp"):
        raise TrainingProvenanceSecurityError(
            f"Unsupported DP accountant_type: '{accountant_type}'. Supported: 'prv', 'rdp'."
        )

    require_secure_rng = bool(clean.get("require_secure_rng", False))
    allocated_epsilon = clean.get("allocated_epsilon")
    if allocated_epsilon is not None and (
        not isinstance(allocated_epsilon, (int, float))
        or not math.isfinite(allocated_epsilon)
        or allocated_epsilon <= 0.0
    ):
        raise TrainingProvenanceSecurityError(
            f"allocated_epsilon must be positive and finite if provided, got: {allocated_epsilon}"
        )

    return {
        "dp_enabled": True,
        "target_epsilon": float(target_epsilon),
        "target_delta": float(target_delta),
        "max_grad_norm": float(max_grad_norm),
        "noise_multiplier": float(noise_multiplier) if noise_multiplier is not None else None,
        "dataset_tag": dataset_tag,
        "accountant_type": accountant_type,
        "accountant": accountant_type,
        "require_secure_rng": require_secure_rng,
        "allocated_epsilon": float(allocated_epsilon) if allocated_epsilon is not None else None,
    }


validate_dp_configuration = validate_dp_config


def compute_config_fingerprint(
    job_id: str,
    training_run_id: str,
    simulation_id: str,
    architecture: str,
    feature_schema_version: str,
    expected_rounds: int,
    expected_epochs: int,
    input_dim: int,
    feature_names: Sequence[str],
    coordinator_id: str = "COORDINATOR",
    permitted_workers: Sequence[str] = (),
    min_quorum: int | None = None,
    training_configuration: dict[str, Any] | None = None,
    consortium_id: str | None = None,
    dp_config: Mapping[str, Any] | None = None,
) -> str:
    """Computes deterministic canonical SHA-256 fingerprint binding all security-critical scheduled parameters.

    Includes job identity, training run, simulation, coordinator, architecture, feature schema,
    expected rounds/epochs, input dimension, feature names, authorized participants, and quorum policy.
    """
    payload = {
        "schema_version": "cfi_cfg_v2",
        "job_id": job_id.strip(),
        "training_run_id": training_run_id.strip(),
        "simulation_id": simulation_id.strip(),
        "coordinator_id": coordinator_id.strip(),
        "architecture": architecture.strip(),
        "feature_schema_version": feature_schema_version.strip(),
        "expected_rounds": expected_rounds,
        "expected_epochs": expected_epochs,
        "input_dim": input_dim,
        "feature_names": [fn.strip() for fn in feature_names],
        "permitted_workers": sorted(pw.strip() for pw in permitted_workers),
        "min_quorum": min_quorum if min_quorum is not None else len(permitted_workers),
        "training_configuration": training_configuration or {},
        "consortium_id": consortium_id.strip() if consortium_id else None,
        "dp_config": dict(dp_config) if dp_config else None,
    }
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def compute_evidence_fingerprint(
    training_run_id: str,
    simulation_id: str,
    executed_rounds: int,
    executed_epochs: int,
    sample_count: int,
    artifact_sha256: str,
    architecture: str,
    feature_schema_version: str,
    input_dim: int,
    feature_names: Sequence[str],
    observed_metrics: dict[str, float],
    consortium_id: str | None = None,
) -> str:
    h = hashlib.sha256()
    h.update(training_run_id.strip().encode())
    h.update(simulation_id.strip().encode())
    h.update(str(executed_rounds).encode())
    h.update(str(executed_epochs).encode())
    h.update(str(sample_count).encode())
    h.update(artifact_sha256.strip().encode())
    h.update(architecture.strip().encode())
    h.update(feature_schema_version.strip().encode())
    h.update(str(input_dim).encode())
    for fn in feature_names:
        h.update(fn.strip().encode())
    for k in sorted(observed_metrics.keys()):
        h.update(f"{k}:{float(observed_metrics[k]):.8f}".encode())
    if consortium_id:
        h.update(consortium_id.strip().encode())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# TrainingJobAuthorityBackend Contract
# ---------------------------------------------------------------------------


class TrainingJobAuthorityBackend(abc.ABC):
    """Abstract storage backend for training job authority."""

    @property
    @abc.abstractmethod
    def backend_type(self) -> str:
        """Returns the type of backend: 'postgres' or 'sqlite'."""
        ...

    @abc.abstractmethod
    def init_schema(self) -> None:
        """Initialize database schema tables, constraints, and indexes."""
        ...

    @abc.abstractmethod
    def schedule_job(
        self,
        job_id: str,
        training_run_id: str,
        simulation_id: str,
        architecture: str,
        feature_schema_version: str,
        expected_rounds: int,
        expected_epochs: int,
        input_dim: int,
        feature_names: Sequence[str],
        coordinator_id: str = "COORDINATOR",
        permitted_workers: Sequence[str] = (),
        consortium_id: str | None = None,
        dp_config: Mapping[str, Any] | None = None,
    ) -> ScheduledTrainingJob: ...

    @abc.abstractmethod
    def start_job(self, job_id: str, worker_id: str = "TRAINING_EXECUTOR") -> StartedTrainingJob: ...

    @abc.abstractmethod
    def record_observation(self, obs: RoundObservation) -> RoundObservation: ...

    @abc.abstractmethod
    def get_observations(self, job_id: str) -> list[RoundObservation]: ...

    def get_round_observations(self, job_id: str) -> list[RoundObservation]:
        """Alias for get_observations for provenance record source compatibility."""
        return self.get_observations(job_id)

    @abc.abstractmethod
    def record_global_aggregation(self, event: GlobalAggregationEvent) -> GlobalAggregationEvent: ...

    @abc.abstractmethod
    def get_global_aggregations(self, job_id: str) -> list[GlobalAggregationEvent]: ...

    @abc.abstractmethod
    def record_model_evaluation(self, event: ModelEvaluationEvent) -> ModelEvaluationEvent: ...

    @abc.abstractmethod
    def get_model_evaluations(self, job_id: str) -> list[ModelEvaluationEvent]: ...

    @abc.abstractmethod
    def complete_job(
        self,
        job_id: str,
        executed_rounds: int,
        executed_epochs: int,
        sample_count: int,
        observed_metrics: dict[str, float],
        artifact_sha256: str,
        worker_id: str = "TRAINING_EXECUTOR",
        git_commit_hash: str | None = None,
        dataset_manifest_id: str | None = None,
        simulation_id: str | None = None,
        training_run_id: str | None = None,
        architecture: str | None = None,
        feature_schema_version: str | None = None,
        input_dim: int | None = None,
        feature_names: Sequence[str] | list[str] | tuple[str, ...] | None = None,
        enforce_lifecycle: bool = True,
        simulate_failure_before_commit: bool = False,
        pause_checkpoint_marker_file: str | None = None,
        pause_after_commit_marker_file: str | None = None,
        consortium_id: str | None = None,
    ) -> CompletedJobExecutionRecord: ...

    @abc.abstractmethod
    def get_completed_job_by_id(self, job_id: str) -> CompletedJobExecutionRecord | None: ...

    @abc.abstractmethod
    def get_completed_job_by_run_id(self, run_id: str) -> CompletedJobExecutionRecord | None: ...

    @abc.abstractmethod
    def get_scheduled_job(self, job_id: str) -> ScheduledTrainingJob | None: ...

    @abc.abstractmethod
    def get_started_job(self, job_id: str) -> StartedTrainingJob | None: ...

    @abc.abstractmethod
    def get_or_create_institutional_privacy_budget(
        self,
        bank_id: str,
        dataset_tag: str,
        allocated_epsilon: float = 10.0,
        allocated_delta: float = 1e-5,
    ) -> InstitutionalPrivacyBudget: ...

    @abc.abstractmethod
    def get_institutional_privacy_budget(
        self,
        bank_id: str,
        dataset_tag: str,
    ) -> InstitutionalPrivacyBudget | None: ...

    @abc.abstractmethod
    def reserve_institutional_privacy_budget(
        self,
        bank_id: str,
        dataset_tag: str,
        job_id: str,
        round_number: int,
        requested_epsilon: float,
        requested_delta: float,
        attempt_id: str | None = None,
    ) -> tuple[bool, str, float]: ...

    @abc.abstractmethod
    def commit_institutional_privacy_expenditure(
        self,
        bank_id: str,
        dataset_tag: str,
        job_id: str,
        round_number: int,
        spent_epsilon: float,
        spent_delta: float,
        steps_executed: int,
        attempt_id: str | None = None,
    ) -> InstitutionalPrivacyBudget: ...

    @abc.abstractmethod
    def clear(self) -> None: ...

    @abc.abstractmethod
    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Helper: Reconcile Completion Evidence
# ---------------------------------------------------------------------------


def reconcile_job_completion_evidence(
    clean_job_id: str,
    sched_row: Mapping[str, Any],
    executed_rounds: int,
    executed_epochs: int,
    sample_count: int,
    target_metrics: dict[str, float],
    clean_sha: str,
    obs_rows: list[RoundObservation],
    agg_rows: list[GlobalAggregationEvent],
    eval_rows: list[ModelEvaluationEvent],
    enforce_lifecycle: bool = True,
    is_production_authority: bool = False,
) -> None:
    """Enforces strict, unambiguous reconciliation across rounds, samples, epochs, metrics, and aggregation provenance."""
    if is_production_authority:
        if not enforce_lifecycle:
            raise TrainingProvenanceSecurityError(
                f"Cannot complete job '{clean_job_id}': enforce_lifecycle=False is forbidden. Execution observations and aggregation evidence are mandatory."
            )
        if not obs_rows:
            raise TrainingProvenanceSecurityError(
                f"Cannot complete job '{clean_job_id}': no verified execution observations recorded. Fabricated completion is rejected."
            )
    else:
        # Development authority (SQLite):
        if not enforce_lifecycle and not obs_rows:
            # Development-only unverified path for low-level table conflict tests
            return
        if not obs_rows:
            raise TrainingProvenanceSecurityError(
                f"Cannot complete job '{clean_job_id}': no verified execution observations recorded. Fabricated completion is rejected."
            )

    sched_expected_rounds = sched_row["expected_rounds"]

    completed_obs = [r for r in obs_rows if r.execution_status == "COMPLETED"]
    if not completed_obs:
        raise TrainingProvenanceSecurityError(
            f"Cannot complete job '{clean_job_id}': no successfully completed execution observations recorded."
        )

    observed_round_nums = sorted({r.round_number for r in completed_obs})

    # 1. Round count and continuity
    if executed_rounds != len(observed_round_nums):
        raise TrainingProvenanceSecurityError(
            f"Executed rounds mismatch for job '{clean_job_id}': claimed {executed_rounds}, but {len(observed_round_nums)} rounds observed."
        )
    expected_seq = list(range(1, executed_rounds + 1))
    if observed_round_nums != expected_seq:
        raise TrainingProvenanceSecurityError(
            f"Executed rounds non-continuous for job '{clean_job_id}': expected rounds {expected_seq}, got {observed_round_nums}."
        )
    if sched_expected_rounds > 0 and executed_rounds < sched_expected_rounds:
        raise TrainingProvenanceSecurityError(
            f"Job '{clean_job_id}' completed fewer rounds than scheduled contract: expected {sched_expected_rounds}, got {executed_rounds}."
        )

    # 2. Strict, Unambiguous Sample Accounting Contract
    latest_round = max(observed_round_nums)
    accepted_global_round_count = len(observed_round_nums)
    accepted_bank_contribution_count = len(completed_obs)
    processed_sample_exposures = sum(r.actual_sample_count * max(1, r.actual_executed_epochs) for r in completed_obs)

    # Dataset sample consistency across rounds (AC-07)
    bank_sample_counts: dict[str, int] = {}
    for obs in completed_obs:
        if obs.bank_id in bank_sample_counts:
            if obs.actual_sample_count != bank_sample_counts[obs.bank_id]:
                raise TrainingProvenanceSecurityError(
                    f"Inconsistent sample count observed for bank '{obs.bank_id}' across rounds for job '{clean_job_id}': "
                    f"round {obs.round_number} reported {obs.actual_sample_count}, but earlier round reported {bank_sample_counts[obs.bank_id]}."
                )
        else:
            bank_sample_counts[obs.bank_id] = obs.actual_sample_count

    canonical_unique_samples = sum(bank_sample_counts.values())

    logger.debug(
        "Reconciliation accounting for %s: rounds=%d, contributions=%d, exposures=%d, canonical_unique_samples=%d",
        clean_job_id,
        accepted_global_round_count,
        accepted_bank_contribution_count,
        processed_sample_exposures,
        canonical_unique_samples,
    )

    # Canonical receipt contract: sample_count MUST represent the unique dataset samples across participating banks.
    # Repeated sample exposures cannot be substituted for unique dataset samples (AC-06).
    if sample_count != canonical_unique_samples:
        if sample_count == processed_sample_exposures and processed_sample_exposures != canonical_unique_samples:
            raise TrainingProvenanceSecurityError(
                f"Sample count mismatch for job '{clean_job_id}': claimed sample exposures ({sample_count}) "
                f"instead of canonical unique dataset samples ({canonical_unique_samples}). "
                f"Repeated sample exposures cannot substitute for unique dataset samples."
            )
        raise TrainingProvenanceSecurityError(
            f"Sample count mismatch for job '{clean_job_id}': claimed {sample_count}, "
            f"but canonical unique dataset samples across participating banks is {canonical_unique_samples} "
            f"(processed_sample_exposures={processed_sample_exposures})."
        )

    # 3. Canonical Executed Epochs Reconciliation (AC-01 through AC-05)
    canonical_executed_epochs = sum(
        max(r.actual_executed_epochs for r in completed_obs if r.round_number == rnd)
        for rnd in observed_round_nums
    )
    total_worker_epochs = sum(r.actual_executed_epochs for r in completed_obs)
    last_round_epochs = max(r.actual_executed_epochs for r in completed_obs if r.round_number == latest_round)

    if executed_epochs != canonical_executed_epochs:
        if executed_epochs == 0 and canonical_executed_epochs > 0:
            raise TrainingProvenanceSecurityError(
                f"Executed epochs mismatch for job '{clean_job_id}': claimed 0 executed epochs despite {canonical_executed_epochs} observed executed epochs."
            )
        if canonical_executed_epochs == 0 and executed_epochs > 0:
            raise TrainingProvenanceSecurityError(
                f"Executed epochs mismatch for job '{clean_job_id}': claimed {executed_epochs}, but zero executed epochs were observed."
            )
        if executed_epochs == total_worker_epochs and total_worker_epochs != canonical_executed_epochs:
            raise TrainingProvenanceSecurityError(
                f"Executed epochs mismatch for job '{clean_job_id}': claimed total worker-epochs ({executed_epochs}) "
                f"instead of canonical sequential global epochs ({canonical_executed_epochs}). "
                f"Sum of worker epochs cannot substitute for canonical sequential epochs."
            )
        if executed_epochs == last_round_epochs and last_round_epochs != canonical_executed_epochs:
            raise TrainingProvenanceSecurityError(
                f"Executed epochs mismatch for job '{clean_job_id}': claimed last-round epochs ({executed_epochs}) "
                f"instead of full-run canonical sequential epochs ({canonical_executed_epochs})."
            )
        raise TrainingProvenanceSecurityError(
            f"Executed epochs mismatch for job '{clean_job_id}': claimed {executed_epochs}, "
            f"but canonical observed executed epochs is {canonical_executed_epochs}. Planned configuration epochs cannot substitute for observed epochs."
        )

    # 4. Global aggregation provenance chain reconciliation
    if not agg_rows:
        raise TrainingProvenanceSecurityError(
            f"Cannot complete federated training job '{clean_job_id}': missing global aggregation records. "
            "All federated rounds require authoritative aggregation evidence."
        )
    agg_round_nums = sorted({a.round_number for a in agg_rows})
    if len(agg_round_nums) != executed_rounds or agg_round_nums != expected_seq:
        raise TrainingProvenanceSecurityError(
            f"Global aggregation round continuity mismatch for job '{clean_job_id}': expected rounds {expected_seq}, but got aggregated rounds {agg_round_nums}."
        )

    # Strictly cross-reference accepted update digests in each round against completed round observations
    seen_digests: set[str] = set()
    for agg in sorted(agg_rows, key=lambda a: a.round_number):
        r_num = agg.round_number
        round_obs = [o for o in completed_obs if o.round_number == r_num]
        obs_map = {o.bank_id: o.model_update_digest for o in round_obs if o.model_update_digest}

        if len(agg.accepted_participant_ids) != len(agg.accepted_local_update_digests):
            raise TrainingProvenanceSecurityError(
                f"Global aggregation for job '{clean_job_id}' round {r_num} has mismatched participants and update digests count."
            )
        if not agg.accepted_participant_ids:
            raise TrainingProvenanceSecurityError(
                f"Global aggregation for job '{clean_job_id}' round {r_num} has zero accepted participants."
            )

        for p_id, u_dig in zip(agg.accepted_participant_ids, agg.accepted_local_update_digests, strict=True):
            if p_id not in obs_map:
                raise TrainingProvenanceSecurityError(
                    f"Global aggregation for job '{clean_job_id}' round {r_num} accepted unobserved participant '{p_id}'."
                )
            if obs_map[p_id] != u_dig:
                raise TrainingProvenanceSecurityError(
                    f"Global aggregation for job '{clean_job_id}' round {r_num} update digest mismatch for '{p_id}': accepted {u_dig}, observed {obs_map[p_id]}."
                )
            if u_dig in seen_digests:
                raise TrainingProvenanceSecurityError(
                    f"Global aggregation for job '{clean_job_id}' round {r_num} replayed update digest '{u_dig}' from an earlier round."
                )
            seen_digests.add(u_dig)

    latest_agg = max(agg_rows, key=lambda a: a.round_number)
    if latest_agg.aggregated_global_model_digest != clean_sha:
        raise TrainingProvenanceSecurityError(
            f"Artifact SHA-256 does not match final accepted aggregation digest for job '{clean_job_id}': expected {latest_agg.aggregated_global_model_digest}, got {clean_sha}."
        )

    # 5. Training Loss Verification
    if "loss" in target_metrics:
        claimed_loss = float(target_metrics["loss"])
        obs_losses: list[tuple[float, float]] = [
            (float(r.observed_loss), float(r.actual_sample_count))
            for r in completed_obs
            if r.round_number == latest_round and r.observed_loss is not None
        ]
        if obs_losses:
            final_losses = [lw[0] for lw in obs_losses]
            tot_s = sum(lw[1] for lw in obs_losses) or 1.0
            weighted_final_loss = sum(lw[0] * lw[1] for lw in obs_losses) / tot_s
            unweighted_final_loss = sum(final_losses) / len(final_losses)
            valid_losses = [*final_losses, weighted_final_loss, unweighted_final_loss]
            if not any(math.isclose(claimed_loss, loss_val, rel_tol=1e-3, abs_tol=1e-3) for loss_val in valid_losses):
                raise TrainingProvenanceSecurityError(
                    f"Observed loss mismatch for job '{clean_job_id}': claimed {claimed_loss}, but observed final loss is {final_losses[-1]}."
                )

    # 6. Evaluation Metrics Provenance Verification (ROC-AUC, validation loss, etc.)
    eval_metrics = {
        k: v for k, v in target_metrics.items()
        if k not in ("loss", "train_loss", "training_loss")
    }
    if eval_metrics:
        if not eval_rows:
            raise TrainingProvenanceSecurityError(
                f"Evaluation metrics {list(eval_metrics.keys())} claimed for job '{clean_job_id}' without verified model evaluation observations."
            )

        valid_agg_digests = {a.aggregated_global_model_digest for a in agg_rows}
        for e in eval_rows:
            if e.evaluated_artifact_digest not in valid_agg_digests:
                raise TrainingProvenanceSecurityError(
                    f"Evaluation event '{e.event_id}' for job '{clean_job_id}' references unaggregated artifact digest '{e.evaluated_artifact_digest}'."
                )
            if e.evaluator_identity in ("UNTRUSTED", "ANONYMOUS", "CLIENT", "cfi_coord_role"):
                raise TrainingProvenanceSecurityError(
                    f"Evaluation event '{e.event_id}' for job '{clean_job_id}' claims unauthorized evaluator identity '{e.evaluator_identity}'."
                )

        for m_name, m_val in eval_metrics.items():
            matching_evals = [
                e for e in eval_rows
                if e.metric_name == m_name
                and e.evaluated_artifact_digest == clean_sha
                and e.evaluator_identity not in ("UNTRUSTED", "ANONYMOUS", "CLIENT", "cfi_coord_role")
            ]
            if not matching_evals:
                raise TrainingProvenanceSecurityError(
                    f"Evaluation metric '{m_name}' claimed for job '{clean_job_id}' has no matching trusted evaluation event bound to artifact {clean_sha}."
                )
            trusted_val = matching_evals[-1].metric_value
            if not math.isclose(float(m_val), float(trusted_val), rel_tol=1e-3, abs_tol=1e-3):
                raise TrainingProvenanceSecurityError(
                    f"Evaluation metric '{m_name}' value mismatch for job '{clean_job_id}': claimed {m_val}, but trusted evaluation recorded {trusted_val}."
                )


FORBIDDEN_DATABASE_PASSWORDS = {
    "cfi_coord_pass_2026",
    "cfi_worker_pass_2026",
    "cfi_issuer_pass_2026",
    "cfi_serving_pass_2026",
    "cfi_completion_pass_2026",
    "cfi_evaluator_pass_2026",
    "password",
    "admin",
    "123456",
    "postgres",
}


AUTHENTIC_EVIDENCE_TRIGGERS_SQL: LiteralString = """
CREATE OR REPLACE FUNCTION cfi_enforce_global_aggregation_authenticity()
RETURNS TRIGGER AS $$
DECLARE
    v_started INT;
    v_completed INT;
    v_prev_agg INT;
    v_part_count INT;
    v_dig_count INT;
    v_part TEXT;
    v_dig TEXT;
    v_i INT;
    v_parts JSONB;
    v_digs JSONB;
    v_obs_count INT;
BEGIN
    SELECT COUNT(*) INTO v_started FROM cfi_started_jobs WHERE job_id = NEW.job_id;
    IF v_started = 0 THEN
        RAISE EXCEPTION 'Aggregation rejected: job % has not been started.', NEW.job_id
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    SELECT COUNT(*) INTO v_completed FROM cfi_completed_jobs WHERE job_id = NEW.job_id;
    IF v_completed > 0 THEN
        RAISE EXCEPTION 'Aggregation rejected: job % is already finalized.', NEW.job_id
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    IF NEW.round_number < 1 THEN
        RAISE EXCEPTION 'Invalid aggregation round number %.', NEW.round_number
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.round_number > 1 THEN
        SELECT COUNT(*) INTO v_prev_agg FROM cfi_global_aggregations
        WHERE job_id = NEW.job_id AND round_number = NEW.round_number - 1;
        IF v_prev_agg = 0 THEN
            RAISE EXCEPTION 'Aggregation round continuity violation: round % requires round %.', NEW.round_number, NEW.round_number - 1
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;

    BEGIN
        v_parts := NEW.accepted_participants_json::jsonb;
        v_digs := NEW.accepted_digests_json::jsonb;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'Invalid JSON format in accepted participants or digests.'
            USING ERRCODE = 'check_violation';
    END;

    v_part_count := jsonb_array_length(v_parts);
    v_dig_count := jsonb_array_length(v_digs);

    IF v_part_count = 0 OR v_part_count <> v_dig_count THEN
        RAISE EXCEPTION 'Aggregation participant and digest arrays must be non-empty and of identical length.'
            USING ERRCODE = 'check_violation';
    END IF;

    FOR v_i IN 0..(v_part_count - 1) LOOP
        v_part := v_parts->>v_i;
        v_dig := v_digs->>v_i;

        SELECT COUNT(*) INTO v_obs_count
        FROM cfi_round_observations
        WHERE job_id = NEW.job_id
          AND round_number = NEW.round_number
          AND bank_id = v_part
          AND model_update_digest = v_dig
          AND execution_status = 'COMPLETED';

        IF v_obs_count = 0 THEN
            RAISE EXCEPTION 'Direct aggregation forgery: participant % digest % has no authentic completed observation in round % for job %',
                v_part, v_dig, NEW.round_number, NEW.job_id
                USING ERRCODE = 'insufficient_privilege';
        END IF;

        IF NEW.round_number > 1 THEN
            IF EXISTS (
                SELECT 1 FROM cfi_global_aggregations
                WHERE job_id = NEW.job_id
                  AND round_number < NEW.round_number
                  AND accepted_digests_json::jsonb @> to_jsonb(v_dig)
            ) THEN
                RAISE EXCEPTION 'Direct aggregation forgery: update digest % was already used in a previous round.', v_dig
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
        END IF;
    END LOOP;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS trg_cfi_enforce_global_aggregation_authenticity ON cfi_global_aggregations;
CREATE TRIGGER trg_cfi_enforce_global_aggregation_authenticity
BEFORE INSERT OR UPDATE ON cfi_global_aggregations
FOR EACH ROW
EXECUTE FUNCTION cfi_enforce_global_aggregation_authenticity();

CREATE OR REPLACE FUNCTION cfi_enforce_model_evaluation_authenticity()
RETURNS TRIGGER AS $$
DECLARE
    v_started INT;
    v_completed INT;
    v_agg_count INT;
BEGIN
    SELECT COUNT(*) INTO v_started FROM cfi_started_jobs WHERE job_id = NEW.job_id;
    IF v_started = 0 THEN
        RAISE EXCEPTION 'Evaluation rejected: job % has not been started.', NEW.job_id
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    SELECT COUNT(*) INTO v_completed FROM cfi_completed_jobs WHERE job_id = NEW.job_id;
    IF v_completed > 0 THEN
        RAISE EXCEPTION 'Evaluation rejected: job % is already finalized.', NEW.job_id
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    SELECT COUNT(*) INTO v_agg_count FROM cfi_global_aggregations
    WHERE job_id = NEW.job_id
      AND aggregated_model_digest = NEW.evaluated_artifact_digest;

    IF v_agg_count = 0 THEN
        RAISE EXCEPTION 'Evaluation forgery: evaluated artifact digest % does not match any authentic global aggregation for job %',
            NEW.evaluated_artifact_digest, NEW.job_id
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    IF (CURRENT_USER = 'cfi_completion_role' AND NEW.evaluator_identity ILIKE '%COORD%')
       OR NEW.evaluator_identity = 'cfi_coord_role'
       OR NEW.evaluator_identity IN ('UNTRUSTED', 'ANONYMOUS', 'CLIENT') THEN
        RAISE EXCEPTION 'Evaluation rejected: unauthorized or coordinator-controlled evaluator identity %', NEW.evaluator_identity
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    IF NEW.metric_value = 'NaN'::double precision
       OR NEW.metric_value = 'Infinity'::double precision
       OR NEW.metric_value = '-Infinity'::double precision THEN
        RAISE EXCEPTION 'Evaluation metric % has non-finite value.', NEW.metric_name
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.metric_name IN ('auc_roc', 'pr_auc') THEN
        IF NEW.metric_value < 0.0 OR NEW.metric_value > 1.0 THEN
            RAISE EXCEPTION 'Evaluation metric % out of valid range [0.0, 1.0]: %', NEW.metric_name, NEW.metric_value
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF NEW.metric_name IN ('loss', 'val_loss', 'train_loss') THEN
        IF NEW.metric_value < 0.0 THEN
            RAISE EXCEPTION 'Evaluation loss cannot be negative: %', NEW.metric_value
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS trg_cfi_enforce_model_evaluation_authenticity ON cfi_model_evaluations;
CREATE TRIGGER trg_cfi_enforce_model_evaluation_authenticity
BEFORE INSERT OR UPDATE ON cfi_model_evaluations
FOR EACH ROW
EXECUTE FUNCTION cfi_enforce_model_evaluation_authenticity();
"""


def provision_runtime_service_roles(
    admin_db_url: str,
    custom_passwords: dict[str, str] | None = None,
    app_env: str = "production",
) -> dict[str, str]:
    """Provision strictly isolated PostgreSQL service roles for coordinator, workers, completion, evaluator, issuer, and serving.

    Enforces Module 01-A.1 Role Separation:
    - cfi_coord_role: Can schedule, record aggregations (governed by authenticity trigger).
      Forbidden from directly inserting completed evidence (SQLSTATE 42501).
      Forbidden from directly inserting evaluations (SQLSTATE 42501).
      Forbidden from inserting bank round observations (SQLSTATE 42501).
    - cfi_completion_role: Dedicated completion authority possessing exclusive INSERT on cfi_completed_jobs and authorized model evaluations.
    - cfi_evaluator_role: Dedicated evaluation authority possessing exclusive INSERT on cfi_model_evaluations.
    - cfi_worker_role & per-bank roles (cfi_bank_a_role, cfi_bank_b_role, cfi_bank_c_role):
      Can start jobs and record bank round observations with Row-Level Security isolation.
      Forbidden from scheduling, completing, or modifying global aggregations/evaluations (SQLSTATE 42501).
    - cfi_issuer_role: Strictly READ-ONLY access to scheduled, started, and completed records.
      Forbidden from inserting or updating any training authority table (SQLSTATE 42501).
    - cfi_serving_role: Strictly READ-ONLY access to completed candidate models.
      Forbidden from mutating or deleting authoritative evidence (SQLSTATE 42501).
    - Immutability: UPDATE and DELETE are REVOKED on all tables across all non-admin runtime roles.
    - Migration safety: Idempotently creates or alters roles without dropping owned tables or cascading objects.
    - Dynamic secrets: High-entropy random passwords by default, rejecting known compromised passwords in production.
    """
    if not psycopg or pg_sql is None:
        raise TrainingProvenanceConfigurationError("PostgreSQL driver (psycopg) is required to provision service roles.")

    parsed = urllib.parse.urlsplit(admin_db_url)
    is_prod = app_env.lower() == "production"

    role_mapping = {
        "coordinator": "cfi_coord_role",
        "completion": "cfi_completion_role",
        "evaluator": "cfi_evaluator_role",
        "worker": "cfi_worker_role",
        "worker_bank_a": "cfi_bank_a_role",
        "worker_bank_b": "cfi_bank_b_role",
        "worker_bank_c": "cfi_bank_c_role",
        "issuer": "cfi_issuer_role",
        "serving": "cfi_serving_role",
    }

    passwords: dict[str, str] = {}
    for role_key in role_mapping:
        provided = (custom_passwords or {}).get(role_key)
        if provided:
            cleaned = provided.strip()
            if is_prod:
                if cleaned in FORBIDDEN_DATABASE_PASSWORDS:
                    raise TrainingProvenanceConfigurationError(
                        f"Insecure or known compromised password rejected for role '{role_key}' in production."
                    )
                if len(cleaned) < 16:
                    raise TrainingProvenanceConfigurationError(
                        f"Database password for role '{role_key}' must have at least 16 characters in production."
                    )
            passwords[role_key] = cleaned
        else:
            if is_prod and custom_passwords is not None and role_key in custom_passwords and not custom_passwords[role_key]:
                raise TrainingProvenanceConfigurationError(f"Missing production database credential for role '{role_key}'.")
            passwords[role_key] = secrets.token_urlsafe(32)

    role_urls: dict[str, str] = {"admin": admin_db_url}

    # 0. Ensure schema tables are initialized under admin authority before applying grants
    PostgresTrainingJobAuthority(db_url=admin_db_url).init_schema()

    with psycopg.connect(admin_db_url) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            # 1. Idempotently create or alter roles without dropping owned tables or cascading objects
            for role_key, rname in role_mapping.items():
                rpass = passwords[role_key]
                cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (rname,))
                if cur.fetchone():
                    cur.execute(
                        pg_sql.SQL("ALTER ROLE {role} WITH LOGIN PASSWORD {pwd}").format(
                            role=pg_sql.Identifier(rname),
                            pwd=pg_sql.Literal(rpass),
                        )
                    )
                else:
                    cur.execute(
                        pg_sql.SQL("CREATE ROLE {role} WITH LOGIN PASSWORD {pwd}").format(
                            role=pg_sql.Identifier(rname),
                            pwd=pg_sql.Literal(rpass),
                        )
                    )

            # 2. Grant schema usage
            for rname in role_mapping.values():
                cur.execute(pg_sql.SQL("GRANT USAGE ON SCHEMA public TO {role}").format(role=pg_sql.Identifier(rname)))

            # 3. Coordinator Role (cfi_coord_role)
            # Can schedule jobs, record global aggregations (subject to authenticity trigger).
            # Strictly CANNOT insert into completed_jobs, model_evaluations, or round observations!
            cur.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO cfi_coord_role")
            cur.execute("GRANT INSERT ON cfi_scheduled_jobs, cfi_global_aggregations TO cfi_coord_role")
            cur.execute("REVOKE INSERT ON cfi_completed_jobs, cfi_model_evaluations, cfi_started_jobs, cfi_round_observations FROM cfi_coord_role")
            cur.execute("REVOKE UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM cfi_coord_role")

            # 4. Completion Authority Role (cfi_completion_role)
            # Dedicated role with exclusive authorization to write reconciled completion records to cfi_completed_jobs and evaluations.
            cur.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO cfi_completion_role")
            cur.execute("GRANT INSERT ON cfi_completed_jobs, cfi_model_evaluations TO cfi_completion_role")
            cur.execute("REVOKE INSERT ON cfi_scheduled_jobs, cfi_started_jobs, cfi_round_observations, cfi_global_aggregations FROM cfi_completion_role")
            cur.execute("REVOKE UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM cfi_completion_role")

            # 4b. Dedicated Model Evaluator Role (cfi_evaluator_role)
            # Dedicated role holding exclusive authorization to record model evaluations.
            cur.execute("GRANT SELECT ON cfi_scheduled_jobs, cfi_started_jobs, cfi_global_aggregations, cfi_model_evaluations, cfi_completed_jobs TO cfi_evaluator_role")
            cur.execute("GRANT INSERT ON cfi_model_evaluations TO cfi_evaluator_role")
            cur.execute("REVOKE INSERT ON cfi_scheduled_jobs, cfi_started_jobs, cfi_round_observations, cfi_global_aggregations, cfi_completed_jobs FROM cfi_evaluator_role")
            cur.execute("REVOKE UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM cfi_evaluator_role")

            # 5. Worker Roles
            # 5a. Bank-Specific Worker Roles (cfi_bank_a_role, cfi_bank_b_role, cfi_bank_c_role)
            # Dedicated per-bank roles holding exclusive authorization to write observations for their respective bank.
            bank_roles = ["cfi_bank_a_role", "cfi_bank_b_role", "cfi_bank_c_role"]
            for br in bank_roles:
                cur.execute(pg_sql.SQL("GRANT SELECT ON cfi_scheduled_jobs, cfi_started_jobs, cfi_round_observations, cfi_completed_jobs TO {role}").format(role=pg_sql.Identifier(br)))
                cur.execute(pg_sql.SQL("GRANT INSERT ON cfi_started_jobs, cfi_round_observations TO {role}").format(role=pg_sql.Identifier(br)))
                cur.execute(pg_sql.SQL("REVOKE INSERT ON cfi_scheduled_jobs, cfi_global_aggregations, cfi_model_evaluations, cfi_completed_jobs FROM {role}").format(role=pg_sql.Identifier(br)))
                cur.execute(pg_sql.SQL("REVOKE UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM {role}").format(role=pg_sql.Identifier(br)))

            # 5b. Shared Generic Worker Role (cfi_worker_role)
            # Strictly FORBIDDEN from inserting into cfi_round_observations (BANKDB-04).
            # No shared worker role may persist authoritative observations for any participating bank.
            cur.execute("GRANT SELECT ON cfi_scheduled_jobs, cfi_started_jobs, cfi_completed_jobs TO cfi_worker_role")
            cur.execute("GRANT INSERT ON cfi_started_jobs TO cfi_worker_role")
            cur.execute("REVOKE ALL ON cfi_round_observations FROM cfi_worker_role")
            cur.execute("REVOKE INSERT ON cfi_scheduled_jobs, cfi_global_aggregations, cfi_model_evaluations, cfi_completed_jobs FROM cfi_worker_role")
            cur.execute("REVOKE UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM cfi_worker_role")

            # 6. Row-Level Security on cfi_round_observations for bank isolation
            cur.execute("ALTER TABLE cfi_round_observations ENABLE ROW LEVEL SECURITY")
            cur.execute("ALTER TABLE cfi_round_observations FORCE ROW LEVEL SECURITY")
            cur.execute("REVOKE ALL ON cfi_round_observations FROM PUBLIC")

            cur.execute("DROP POLICY IF EXISTS cfi_bank_a_policy ON cfi_round_observations")
            cur.execute("DROP POLICY IF EXISTS cfi_bank_b_policy ON cfi_round_observations")
            cur.execute("DROP POLICY IF EXISTS cfi_bank_c_policy ON cfi_round_observations")
            cur.execute("DROP POLICY IF EXISTS cfi_general_worker_policy ON cfi_round_observations")
            cur.execute("DROP POLICY IF EXISTS cfi_readers_obs_policy ON cfi_round_observations")

            # Strict policy for Bank A:
            # - USING: Can only view its own rows (bank_id = 'BANK_A') (BANKDB-07).
            # - WITH CHECK: Can only insert rows where bank_id = 'BANK_A' AND worker_id is either 'BANK_A'
            #   or does not claim another bank identity (BANKDB-02, BANKDB-06).
            cur.execute("""
                CREATE POLICY cfi_bank_a_policy ON cfi_round_observations
                FOR ALL TO cfi_bank_a_role
                USING (bank_id = 'BANK_A')
                WITH CHECK (bank_id = 'BANK_A' AND (worker_id = 'BANK_A' OR worker_id NOT LIKE 'BANK_%'))
            """)
            cur.execute("""
                CREATE POLICY cfi_bank_b_policy ON cfi_round_observations
                FOR ALL TO cfi_bank_b_role
                USING (bank_id = 'BANK_B')
                WITH CHECK (bank_id = 'BANK_B' AND (worker_id = 'BANK_B' OR worker_id NOT LIKE 'BANK_%'))
            """)
            cur.execute("""
                CREATE POLICY cfi_bank_c_policy ON cfi_round_observations
                FOR ALL TO cfi_bank_c_role
                USING (bank_id = 'BANK_C')
                WITH CHECK (bank_id = 'BANK_C' AND (worker_id = 'BANK_C' OR worker_id NOT LIKE 'BANK_%'))
            """)
            cur.execute("""
                CREATE POLICY cfi_readers_obs_policy ON cfi_round_observations
                FOR SELECT TO cfi_coord_role, cfi_completion_role, cfi_evaluator_role, cfi_issuer_role
                USING (true)
            """)

            # 7. Issuer Role (cfi_issuer_role)
            cur.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO cfi_issuer_role")
            cur.execute("REVOKE INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM cfi_issuer_role")

            # 8. Serving Role (cfi_serving_role)
            cur.execute("GRANT SELECT ON cfi_completed_jobs TO cfi_serving_role")
            cur.execute("REVOKE INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM cfi_serving_role")

            # 9. Apply Authentic Evidence Triggers
            cur.execute(pg_sql.SQL(AUTHENTIC_EVIDENCE_TRIGGERS_SQL) if pg_sql else AUTHENTIC_EVIDENCE_TRIGGERS_SQL)

    for role_key, rname in role_mapping.items():
        rpass = passwords[role_key]
        netloc = f"{rname}:{rpass}@{parsed.hostname}:{parsed.port}"
        role_urls[role_key] = urllib.parse.urlunsplit(
            (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
        )

    return role_urls


class PostgresTrainingJobAuthority(TrainingJobAuthorityBackend):
    """PostgreSQL-backed ACID relational authority enforcing multi-host concurrency and row-level locking."""

    def __init__(self, db_url: str, app_env: str = "development") -> None:
        if psycopg is None:
            raise TrainingProvenanceConfigurationError(
                "psycopg (v3) driver is not installed. PostgreSQL training authority cannot operate without psycopg."
            )

        clean_url = db_url.strip()
        if not (clean_url.startswith("postgresql://") or clean_url.startswith("postgres://")):
            redacted_url = redact_database_url(clean_url)
            raise TrainingProvenanceConfigurationError(
                f"Unsupported database URL scheme for PostgreSQL authority: '{redacted_url}'."
            )

        self._db_url = clean_url
        self.app_env = app_env
        self._is_prod = app_env.lower() == "production"
        self._lock = threading.RLock()
        self._test_connection()
        self.init_schema()

    @property
    def backend_type(self) -> str:
        return "postgres"

    def _test_connection(self) -> None:
        if psycopg is None:
            raise TrainingProvenanceConfigurationError(
                "psycopg (v3) driver is not installed. PostgreSQL training authority cannot operate without psycopg."
            )
        try:
            with psycopg.connect(self._db_url, connect_timeout=10) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
        except Exception as e:
            redacted_url = redact_database_url(self._db_url)
            sanitized_err = sanitize_database_error_message(str(e))
            logger.error("PostgreSQL connection test failed on URL %s: %s", redacted_url, sanitized_err)
            raise TrainingProvenanceConfigurationError(
                f"Failed to connect to PostgreSQL authority at '{redacted_url}': {sanitized_err}"
            ) from None

    @contextlib.contextmanager
    def _connect(self) -> Generator[Any, None, None]:
        if psycopg is None or pg_dict_row is None:
            raise TrainingProvenanceConfigurationError(
                "psycopg (v3) driver is not installed. PostgreSQL training authority cannot operate without psycopg."
            )
        try:
            conn: Any = psycopg.connect(self._db_url, row_factory=pg_dict_row, autocommit=False)
        except TrainingProvenanceConfigurationError:
            raise
        except Exception as e:
            redacted_url = redact_database_url(self._db_url)
            sanitized_err = sanitize_database_error_message(str(e))
            raise TrainingProvenanceConfigurationError(f"PostgreSQL connection failure to {redacted_url}: {sanitized_err}") from None

        try:
            yield conn
        finally:
            conn.close()

    def init_schema(self) -> None:
        try:
            with self._connect() as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute("SELECT pg_advisory_xact_lock(hashtext('cfi_schema_init_lock'))")
                        cur.execute(
                            """
                            CREATE TABLE IF NOT EXISTS cfi_scheduled_jobs (
                                job_id VARCHAR(128) PRIMARY KEY,
                                training_run_id VARCHAR(128) UNIQUE NOT NULL,
                                simulation_id VARCHAR(128) NOT NULL,
                                coordinator_id VARCHAR(128) NOT NULL,
                                architecture VARCHAR(128) NOT NULL,
                                feature_schema_version VARCHAR(64) NOT NULL,
                                expected_rounds INT NOT NULL,
                                expected_epochs INT NOT NULL,
                                input_dim INT NOT NULL,
                                feature_names_json TEXT NOT NULL,
                                permitted_workers_json TEXT,
                                config_fingerprint VARCHAR(64) NOT NULL,
                                scheduled_at VARCHAR(64) NOT NULL,
                                status VARCHAR(32) NOT NULL DEFAULT 'SCHEDULED',
                                consortium_id VARCHAR(128),
                                dp_config_json TEXT
                            );
                            """
                        )
                        cur.execute("ALTER TABLE cfi_scheduled_jobs ADD COLUMN IF NOT EXISTS consortium_id VARCHAR(128);")
                        cur.execute("ALTER TABLE cfi_scheduled_jobs ADD COLUMN IF NOT EXISTS dp_config_json TEXT;")
                        cur.execute(
                            """
                            CREATE TABLE IF NOT EXISTS cfi_institutional_privacy_budgets (
                                bank_id VARCHAR(128) NOT NULL,
                                dataset_tag VARCHAR(128) NOT NULL,
                                allocated_epsilon DOUBLE PRECISION NOT NULL,
                                allocated_delta DOUBLE PRECISION NOT NULL,
                                spent_epsilon DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                                spent_delta DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                                reserved_epsilon DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                                reserved_delta DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                                total_steps_executed INT NOT NULL DEFAULT 0,
                                active_reservations_json TEXT NOT NULL DEFAULT '{}',
                                history_json TEXT NOT NULL DEFAULT '[]',
                                created_at VARCHAR(64) NOT NULL,
                                updated_at VARCHAR(64) NOT NULL,
                                PRIMARY KEY (bank_id, dataset_tag)
                            );
                            """
                        )
                        cur.execute(
                            """
                            CREATE TABLE IF NOT EXISTS cfi_started_jobs (
                                job_id VARCHAR(128) PRIMARY KEY REFERENCES cfi_scheduled_jobs(job_id),
                                training_run_id VARCHAR(128) NOT NULL,
                                simulation_id VARCHAR(128) NOT NULL,
                                worker_id VARCHAR(128) NOT NULL,
                                started_at VARCHAR(64) NOT NULL,
                                status VARCHAR(32) NOT NULL DEFAULT 'STARTED'
                            );
                            """
                        )
                        cur.execute(
                            """
                            CREATE TABLE IF NOT EXISTS cfi_round_observations (
                                event_id VARCHAR(128) PRIMARY KEY,
                                job_id VARCHAR(128) NOT NULL REFERENCES cfi_scheduled_jobs(job_id),
                                training_run_id VARCHAR(128) NOT NULL,
                                simulation_id VARCHAR(128) NOT NULL,
                                worker_id VARCHAR(128) NOT NULL,
                                bank_id VARCHAR(128) NOT NULL,
                                round_number INT NOT NULL,
                                attempt_number INT NOT NULL DEFAULT 1,
                                actual_sample_count INT NOT NULL,
                                actual_executed_epochs INT NOT NULL DEFAULT 0,
                                observed_loss DOUBLE PRECISION,
                                metrics_json TEXT NOT NULL,
                                model_update_digest VARCHAR(64),
                                execution_timestamp VARCHAR(64) NOT NULL,
                                execution_status VARCHAR(32) NOT NULL DEFAULT 'COMPLETED',
                                payload_digest VARCHAR(64) NOT NULL,
                                CONSTRAINT uq_cfi_round_obs UNIQUE(job_id, bank_id, round_number, attempt_number)
                            );
                            """
                        )
                        cur.execute(
                            """
                            CREATE TABLE IF NOT EXISTS cfi_global_aggregations (
                                event_id VARCHAR(128) PRIMARY KEY,
                                job_id VARCHAR(128) NOT NULL REFERENCES cfi_scheduled_jobs(job_id),
                                round_number INT NOT NULL,
                                aggregation_algorithm VARCHAR(64) NOT NULL,
                                accepted_participants_json TEXT NOT NULL,
                                accepted_digests_json TEXT NOT NULL,
                                aggregation_config_json TEXT NOT NULL,
                                aggregated_model_digest VARCHAR(64) NOT NULL,
                                timestamp VARCHAR(64) NOT NULL,
                                aggregation_status VARCHAR(32) NOT NULL DEFAULT 'COMPLETED',
                                payload_digest VARCHAR(64) NOT NULL,
                                CONSTRAINT uq_cfi_global_agg UNIQUE(job_id, round_number)
                            );
                            """
                        )
                        cur.execute(
                            """
                            CREATE TABLE IF NOT EXISTS cfi_model_evaluations (
                                event_id VARCHAR(128) PRIMARY KEY,
                                job_id VARCHAR(128) NOT NULL REFERENCES cfi_scheduled_jobs(job_id),
                                evaluation_run_id VARCHAR(128) NOT NULL,
                                metric_name VARCHAR(64) NOT NULL,
                                metric_value DOUBLE PRECISION NOT NULL,
                                evaluated_artifact_digest VARCHAR(64) NOT NULL,
                                evaluator_identity VARCHAR(128) NOT NULL,
                                evaluation_timestamp VARCHAR(64) NOT NULL,
                                dataset_manifest_id TEXT,
                                payload_digest VARCHAR(64) NOT NULL,
                                CONSTRAINT uq_cfi_model_eval UNIQUE(job_id, metric_name, evaluated_artifact_digest)
                            );
                            """
                        )
                        cur.execute(
                            """
                            CREATE TABLE IF NOT EXISTS cfi_completed_jobs (
                                job_id VARCHAR(128) PRIMARY KEY REFERENCES cfi_scheduled_jobs(job_id),
                                training_run_id VARCHAR(128) UNIQUE NOT NULL,
                                simulation_id VARCHAR(128) NOT NULL,
                                completed_at VARCHAR(64) NOT NULL,
                                executed_rounds INT NOT NULL,
                                executed_epochs INT NOT NULL,
                                sample_count INT NOT NULL,
                                observed_metrics_json TEXT NOT NULL,
                                artifact_sha256 VARCHAR(64) NOT NULL,
                                architecture VARCHAR(128) NOT NULL,
                                feature_schema_version VARCHAR(64) NOT NULL,
                                input_dim INT NOT NULL,
                                feature_names_json TEXT NOT NULL,
                                worker_id VARCHAR(128) NOT NULL,
                                status VARCHAR(32) NOT NULL DEFAULT 'COMPLETED',
                                git_commit_hash VARCHAR(128),
                                dataset_manifest_id TEXT,
                                evidence_fingerprint VARCHAR(64) NOT NULL,
                                consortium_id VARCHAR(128)
                            );
                            """
                        )
                        cur.execute("ALTER TABLE cfi_completed_jobs ADD COLUMN IF NOT EXISTS consortium_id VARCHAR(128);")
                        cur.execute(pg_sql.SQL(AUTHENTIC_EVIDENCE_TRIGGERS_SQL) if pg_sql else AUTHENTIC_EVIDENCE_TRIGGERS_SQL)
        except Exception as e:
            sanitized = str(e)
            if "permission denied" in sanitized.lower() or "42501" in sanitized:
                try:
                    with self._connect() as conn:
                        with conn.cursor() as cur:
                            cur.execute("SELECT current_user")
                            c_user_row = cur.fetchone()
                            c_user = c_user_row["current_user"] if isinstance(c_user_row, dict) else (c_user_row[0] if c_user_row else "")
                            authorized_service_roles = {
                                "cfi_coord_role",
                                "cfi_completion_role",
                                "cfi_evaluator_role",
                                "cfi_worker_role",
                                "cfi_bank_a_role",
                                "cfi_bank_b_role",
                                "cfi_bank_c_role",
                                "cfi_issuer_role",
                                "cfi_serving_role",
                            }
                            if c_user in authorized_service_roles:
                                cur.execute("SELECT 1 FROM cfi_scheduled_jobs LIMIT 1")
                                return
                except Exception:
                    pass
            raise

    def schedule_job(
        self,
        job_id: str,
        training_run_id: str,
        simulation_id: str,
        architecture: str,
        feature_schema_version: str,
        expected_rounds: int,
        expected_epochs: int,
        input_dim: int,
        feature_names: Sequence[str],
        coordinator_id: str = "COORDINATOR",
        permitted_workers: Sequence[str] = (),
        consortium_id: str | None = None,
        dp_config: Mapping[str, Any] | None = None,
    ) -> ScheduledTrainingJob:
        clean_job_id = job_id.strip()
        clean_run_id = training_run_id.strip()
        clean_sim_id = simulation_id.strip()
        clean_consortium_id = consortium_id.strip() if consortium_id else None
        clean_dp_config = validate_dp_config(dp_config)
        fnames_tuple = tuple(feature_names)
        perm_workers_tuple = tuple(permitted_workers)
        now_str = datetime.now(UTC).isoformat()

        cfg_fingerprint = compute_config_fingerprint(
            clean_job_id,
            clean_run_id,
            clean_sim_id,
            architecture,
            feature_schema_version,
            expected_rounds,
            expected_epochs,
            input_dim,
            fnames_tuple,
            coordinator_id=coordinator_id,
            permitted_workers=perm_workers_tuple,
            consortium_id=clean_consortium_id,
            dp_config=clean_dp_config,
        )

        with self._connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM cfi_scheduled_jobs WHERE job_id = %s", (clean_job_id,))
                    existing = cur.fetchone()
                    if existing:
                        if existing["config_fingerprint"] == cfg_fingerprint:
                            return ScheduledTrainingJob(
                                job_id=existing["job_id"],
                                training_run_id=existing["training_run_id"],
                                simulation_id=existing["simulation_id"],
                                coordinator_id=existing["coordinator_id"],
                                architecture=existing["architecture"],
                                feature_schema_version=existing["feature_schema_version"],
                                expected_rounds=existing["expected_rounds"],
                                expected_epochs=existing["expected_epochs"],
                                input_dim=existing["input_dim"],
                                feature_names=tuple(json.loads(existing["feature_names_json"])),
                                permitted_workers=tuple(json.loads(existing["permitted_workers_json"] or "[]")),
                                config_fingerprint=existing["config_fingerprint"],
                                scheduled_at=existing["scheduled_at"],
                                status=existing["status"],
                                consortium_id=existing.get("consortium_id"),
                                dp_config=json.loads(existing["dp_config_json"]) if existing.get("dp_config_json") else None,
                            )
                        else:
                            raise TrainingProvenanceSecurityError(
                                f"Conflicting training job scheduling for '{clean_job_id}': cannot reschedule existing job with different parameters."
                            )

                    cur.execute(
                        "SELECT job_id FROM cfi_scheduled_jobs WHERE training_run_id = %s",
                        (clean_run_id,),
                    )
                    existing_run = cur.fetchone()
                    if existing_run:
                        raise TrainingProvenanceSecurityError(
                            f"Training run ID '{clean_run_id}' already bound to job '{existing_run['job_id']}': duplicate run ID rejected."
                        )

                    cur.execute(
                        """
                        INSERT INTO cfi_scheduled_jobs (
                            job_id, training_run_id, simulation_id, coordinator_id,
                            architecture, feature_schema_version, expected_rounds,
                            expected_epochs, input_dim, feature_names_json,
                            permitted_workers_json, config_fingerprint, scheduled_at, status,
                            consortium_id, dp_config_json
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            clean_job_id,
                            clean_run_id,
                            clean_sim_id,
                            coordinator_id,
                            architecture,
                            feature_schema_version,
                            expected_rounds,
                            expected_epochs,
                            input_dim,
                            json.dumps(list(fnames_tuple)),
                            json.dumps(list(perm_workers_tuple)),
                            cfg_fingerprint,
                            now_str,
                            "SCHEDULED",
                            clean_consortium_id,
                            json.dumps(clean_dp_config) if clean_dp_config else None,
                        ),
                    )

        return ScheduledTrainingJob(
            job_id=clean_job_id,
            training_run_id=clean_run_id,
            simulation_id=clean_sim_id,
            coordinator_id=coordinator_id,
            architecture=architecture,
            feature_schema_version=feature_schema_version,
            expected_rounds=expected_rounds,
            expected_epochs=expected_epochs,
            input_dim=input_dim,
            feature_names=fnames_tuple,
            permitted_workers=perm_workers_tuple,
            config_fingerprint=cfg_fingerprint,
            scheduled_at=now_str,
            status="SCHEDULED",
            consortium_id=clean_consortium_id,
            dp_config=clean_dp_config,
        )

    def start_job(self, job_id: str, worker_id: str = "TRAINING_EXECUTOR") -> StartedTrainingJob:
        clean_job_id = job_id.strip()
        now_str = datetime.now(UTC).isoformat()

        with self._connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM cfi_scheduled_jobs WHERE job_id = %s", (clean_job_id,))
                    sched = cur.fetchone()
                    if not sched:
                        raise TrainingProvenanceSecurityError(
                            f"Cannot start job '{clean_job_id}': job was not scheduled by the training coordinator."
                        )

                    cur.execute("SELECT job_id FROM cfi_completed_jobs WHERE job_id = %s", (clean_job_id,))
                    if cur.fetchone():
                        raise TrainingProvenanceSecurityError(
                            f"Cannot start job '{clean_job_id}': job has already completed."
                        )

                    perm_workers = json.loads(sched["permitted_workers_json"] or "[]")
                    if perm_workers and worker_id not in perm_workers:
                        raise TrainingProvenanceSecurityError(
                            f"Worker '{worker_id}' is not authorized to start job '{clean_job_id}' (permitted: {perm_workers})."
                        )

                    cur.execute("SELECT * FROM cfi_started_jobs WHERE job_id = %s", (clean_job_id,))
                    existing = cur.fetchone()
                    if existing:
                        return StartedTrainingJob(
                            job_id=existing["job_id"],
                            training_run_id=existing["training_run_id"],
                            simulation_id=existing["simulation_id"],
                            worker_id=existing["worker_id"],
                            started_at=existing["started_at"],
                            status=existing["status"],
                        )

                    cur.execute(
                        """
                        INSERT INTO cfi_started_jobs (
                            job_id, training_run_id, simulation_id, worker_id, started_at, status
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (job_id) DO NOTHING
                        """,
                        (
                            clean_job_id,
                            sched["training_run_id"],
                            sched["simulation_id"],
                            worker_id,
                            now_str,
                            "STARTED",
                        ),
                    )

                    cur.execute("SELECT * FROM cfi_started_jobs WHERE job_id = %s", (clean_job_id,))
                    row = cur.fetchone()
                    if not row:
                        raise TrainingProvenanceSecurityError(
                            f"Failed to record or retrieve started training job '{clean_job_id}'."
                        )
                    return StartedTrainingJob(
                        job_id=row["job_id"],
                        training_run_id=row["training_run_id"],
                        simulation_id=row["simulation_id"],
                        worker_id=row["worker_id"],
                        started_at=row["started_at"],
                        status=row["status"],
                    )

    def record_observation(self, obs: RoundObservation) -> RoundObservation:
        obs.validate()
        clean_job_id = obs.job_id.strip()

        with self._connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_xact_lock_shared(hashtext(%s))", (clean_job_id,))
                    cur.execute("SELECT * FROM cfi_started_jobs WHERE job_id = %s", (clean_job_id,))
                    started = cur.fetchone()
                    if not started:
                        raise TrainingProvenanceSecurityError(
                            f"Cannot record observations for job '{clean_job_id}': job is not in STARTED state."
                        )

                    cur.execute("SELECT job_id FROM cfi_completed_jobs WHERE job_id = %s", (clean_job_id,))
                    if cur.fetchone():
                        raise TrainingProvenanceSecurityError(
                            f"Cannot add round observation to finalized completed job '{clean_job_id}'."
                        )

                    cur.execute("SELECT * FROM cfi_round_observations WHERE event_id = %s", (obs.event_id,))
                    existing_evt = cur.fetchone()
                    if existing_evt:
                        if existing_evt["payload_digest"] == obs.payload_digest:
                            return obs
                        raise TrainingProvenanceSecurityError(
                            f"Duplicate event ID '{obs.event_id}' with conflicting content rejected."
                        )

                    cur.execute(
                        """
                        SELECT * FROM cfi_round_observations
                        WHERE job_id = %s AND bank_id = %s AND round_number = %s AND attempt_number = %s
                        """,
                        (clean_job_id, obs.bank_id, obs.round_number, obs.attempt_number),
                    )
                    existing_round = cur.fetchone()
                    if existing_round:
                        if existing_round["payload_digest"] == obs.payload_digest:
                            return obs
                        raise TrainingProvenanceSecurityError(
                            f"Duplicate observation for job '{clean_job_id}' bank '{obs.bank_id}' round {obs.round_number} attempt {obs.attempt_number} with conflicting content rejected."
                        )

                    cur.execute(
                        """
                        INSERT INTO cfi_round_observations (
                            event_id, job_id, training_run_id, simulation_id,
                            worker_id, bank_id, round_number, attempt_number,
                            actual_sample_count, actual_executed_epochs, observed_loss,
                            metrics_json, model_update_digest, execution_timestamp,
                            execution_status, payload_digest
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            obs.event_id,
                            clean_job_id,
                            started["training_run_id"],
                            started["simulation_id"],
                            obs.worker_id,
                            obs.bank_id,
                            obs.round_number,
                            obs.attempt_number,
                            obs.actual_sample_count,
                            obs.actual_executed_epochs,
                            float(obs.observed_loss) if obs.observed_loss is not None else None,
                            json.dumps(dict(obs.metrics)),
                            obs.model_update_digest,
                            obs.execution_timestamp,
                            obs.execution_status,
                            obs.payload_digest,
                        ),
                    )

        return obs

    def get_observations(self, job_id: str) -> list[RoundObservation]:
        clean_job_id = job_id.strip()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT * FROM cfi_round_observations
                    WHERE job_id = %s
                    ORDER BY round_number ASC, attempt_number ASC
                    """,
                (clean_job_id,),
            )
            rows = cur.fetchall()
            return [
                RoundObservation(
                    event_id=r["event_id"],
                    job_id=r["job_id"],
                    training_run_id=r["training_run_id"],
                    simulation_id=r["simulation_id"],
                    worker_id=r["worker_id"],
                    bank_id=r["bank_id"],
                    round_number=r["round_number"],
                    attempt_number=r["attempt_number"],
                    actual_sample_count=r["actual_sample_count"],
                    actual_executed_epochs=r["actual_executed_epochs"],
                    observed_loss=float(r["observed_loss"]) if r["observed_loss"] is not None else None,
                    metrics=json.loads(r["metrics_json"] or "{}"),
                    execution_timestamp=r["execution_timestamp"],
                    model_update_digest=r["model_update_digest"],
                    execution_status=r["execution_status"],
                )
                for r in rows
            ]

    def record_global_aggregation(self, event: GlobalAggregationEvent) -> GlobalAggregationEvent:
        event.validate()
        clean_job_id = event.job_id.strip()

        with self._connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_xact_lock_shared(hashtext(%s))", (clean_job_id,))
                    cur.execute("SELECT * FROM cfi_started_jobs WHERE job_id = %s", (clean_job_id,))
                    if not cur.fetchone():
                        raise TrainingProvenanceSecurityError(
                            f"Cannot record global aggregation for job '{clean_job_id}': job is not in STARTED state."
                        )

                    cur.execute("SELECT job_id FROM cfi_completed_jobs WHERE job_id = %s", (clean_job_id,))
                    if cur.fetchone():
                        raise TrainingProvenanceSecurityError(
                            f"Cannot record global aggregation for finalized job '{clean_job_id}'."
                        )

                    cur.execute("SELECT * FROM cfi_global_aggregations WHERE event_id = %s", (event.event_id,))
                    existing_evt = cur.fetchone()
                    if existing_evt:
                        if existing_evt["payload_digest"] == event.payload_digest:
                            return event
                        raise TrainingProvenanceSecurityError(
                            f"Conflicting global aggregation event ID '{event.event_id}'."
                        )

                    cur.execute(
                        "SELECT * FROM cfi_global_aggregations WHERE job_id = %s AND round_number = %s",
                        (clean_job_id, event.round_number),
                    )
                    existing_round = cur.fetchone()
                    if existing_round:
                        if existing_round["payload_digest"] == event.payload_digest:
                            return event
                        raise TrainingProvenanceSecurityError(
                            f"Conflicting global aggregation for job '{clean_job_id}' round {event.round_number}."
                        )

                    cur.execute(
                        """
                        SELECT bank_id, model_update_digest FROM cfi_round_observations
                        WHERE job_id = %s AND round_number = %s AND execution_status = 'COMPLETED'
                        """,
                        (clean_job_id, event.round_number),
                    )
                    obs_rows = cur.fetchall()
                    obs_dict = {r["bank_id"]: r["model_update_digest"] for r in obs_rows}
                    for p_id, d_val in zip(event.accepted_participant_ids, event.accepted_local_update_digests, strict=True):
                        if p_id not in obs_dict or obs_dict[p_id] != d_val:
                            raise TrainingProvenanceSecurityError(
                                f"Direct aggregation forgery: participant '{p_id}' digest '{d_val}' has no authentic completed observation in round {event.round_number} for job '{clean_job_id}'."
                            )

                    cur.execute(
                        """
                        INSERT INTO cfi_global_aggregations (
                            event_id, job_id, round_number, aggregation_algorithm,
                            accepted_participants_json, accepted_digests_json,
                            aggregation_config_json, aggregated_model_digest,
                            timestamp, aggregation_status, payload_digest
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            event.event_id,
                            clean_job_id,
                            event.round_number,
                            event.aggregation_algorithm,
                            json.dumps(list(event.accepted_participant_ids)),
                            json.dumps(list(event.accepted_local_update_digests)),
                            json.dumps(dict(event.aggregation_configuration)),
                            event.aggregated_global_model_digest,
                            event.timestamp,
                            event.aggregation_status,
                            event.payload_digest,
                        ),
                    )

        return event

    def get_global_aggregations(self, job_id: str) -> list[GlobalAggregationEvent]:
        clean_job_id = job_id.strip()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT * FROM cfi_global_aggregations
                    WHERE job_id = %s
                    ORDER BY round_number ASC
                    """,
                (clean_job_id,),
            )
            rows = cur.fetchall()
            return [
                GlobalAggregationEvent(
                    event_id=r["event_id"],
                    job_id=r["job_id"],
                    round_number=r["round_number"],
                    aggregation_algorithm=r["aggregation_algorithm"],
                    accepted_participant_ids=tuple(json.loads(r["accepted_participants_json"])),
                    accepted_local_update_digests=tuple(json.loads(r["accepted_digests_json"])),
                    aggregation_configuration=json.loads(r["aggregation_config_json"] or "{}"),
                    aggregated_global_model_digest=r["aggregated_model_digest"],
                    timestamp=r["timestamp"],
                    aggregation_status=r["aggregation_status"],
                )
                for r in rows
            ]

    def record_model_evaluation(self, event: ModelEvaluationEvent) -> ModelEvaluationEvent:
        event.validate()
        clean_job_id = event.job_id.strip()

        with self._connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_xact_lock_shared(hashtext(%s))", (clean_job_id,))
                    cur.execute("SELECT * FROM cfi_started_jobs WHERE job_id = %s", (clean_job_id,))
                    if not cur.fetchone():
                        raise TrainingProvenanceSecurityError(
                            f"Cannot record model evaluation for job '{clean_job_id}': job is not in STARTED state."
                        )

                    cur.execute("SELECT job_id FROM cfi_completed_jobs WHERE job_id = %s", (clean_job_id,))
                    if cur.fetchone():
                        raise TrainingProvenanceSecurityError(
                            f"Cannot record evaluation for finalized completed job '{clean_job_id}'."
                        )

                    cur.execute("SELECT * FROM cfi_model_evaluations WHERE event_id = %s", (event.event_id,))
                    existing = cur.fetchone()
                    if existing:
                        if existing["payload_digest"] == event.payload_digest:
                            return event
                        raise TrainingProvenanceSecurityError(
                            f"Conflicting model evaluation event ID '{event.event_id}'."
                        )

                    cur.execute(
                        """
                        SELECT 1 FROM cfi_global_aggregations
                        WHERE job_id = %s AND aggregated_model_digest = %s
                        """,
                        (clean_job_id, event.evaluated_artifact_digest),
                    )
                    if not cur.fetchone():
                        raise TrainingProvenanceSecurityError(
                            f"Evaluation forgery: evaluated artifact digest '{event.evaluated_artifact_digest}' does not match any authentic global aggregation for job '{clean_job_id}'."
                        )

                    cur.execute(
                        """
                        SELECT * FROM cfi_model_evaluations
                        WHERE job_id = %s AND metric_name = %s AND evaluated_artifact_digest = %s
                        """,
                        (clean_job_id, event.metric_name, event.evaluated_artifact_digest),
                    )
                    existing_metric = cur.fetchone()
                    if existing_metric:
                        if existing_metric["payload_digest"] == event.payload_digest:
                            return event
                        raise TrainingProvenanceSecurityError(
                            f"Conflicting evaluation observation for job '{clean_job_id}' metric '{event.metric_name}'."
                        )

                    cur.execute(
                        """
                        INSERT INTO cfi_model_evaluations (
                            event_id, job_id, evaluation_run_id, metric_name,
                            metric_value, evaluated_artifact_digest, evaluator_identity,
                            evaluation_timestamp, dataset_manifest_id, payload_digest
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            event.event_id,
                            clean_job_id,
                            event.evaluation_run_id,
                            event.metric_name,
                            float(event.metric_value),
                            event.evaluated_artifact_digest,
                            event.evaluator_identity,
                            event.evaluation_timestamp,
                            event.evaluation_dataset_manifest,
                            event.payload_digest,
                        ),
                    )

        return event

    def get_model_evaluations(self, job_id: str) -> list[ModelEvaluationEvent]:
        clean_job_id = job_id.strip()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT * FROM cfi_model_evaluations
                    WHERE job_id = %s
                    ORDER BY evaluation_timestamp ASC
                    """,
                (clean_job_id,),
            )
            rows = cur.fetchall()
            return [
                ModelEvaluationEvent(
                    event_id=r["event_id"],
                    job_id=r["job_id"],
                    evaluation_run_id=r["evaluation_run_id"],
                    metric_name=r["metric_name"],
                    metric_value=float(r["metric_value"]),
                    evaluated_artifact_digest=r["evaluated_artifact_digest"],
                    evaluator_identity=r["evaluator_identity"],
                    evaluation_timestamp=r["evaluation_timestamp"],
                    evaluation_dataset_manifest=r["dataset_manifest_id"],
                )
                for r in rows
            ]

    def complete_job(
        self,
        job_id: str,
        executed_rounds: int,
        executed_epochs: int,
        sample_count: int,
        observed_metrics: dict[str, float],
        artifact_sha256: str,
        worker_id: str = "TRAINING_EXECUTOR",
        git_commit_hash: str | None = None,
        dataset_manifest_id: str | None = None,
        simulation_id: str | None = None,
        training_run_id: str | None = None,
        architecture: str | None = None,
        feature_schema_version: str | None = None,
        input_dim: int | None = None,
        feature_names: Sequence[str] | list[str] | tuple[str, ...] | None = None,
        enforce_lifecycle: bool = True,
        simulate_failure_before_commit: bool = False,
        pause_checkpoint_marker_file: str | None = None,
        pause_after_commit_marker_file: str | None = None,
        consortium_id: str | None = None,
    ) -> CompletedJobExecutionRecord:
        clean_job_id = job_id.strip()
        if not enforce_lifecycle:
            raise TrainingProvenanceSecurityError(
                f"Cannot complete job '{clean_job_id}': enforce_lifecycle=False is forbidden. Execution observations and aggregation evidence are mandatory."
            )
        clean_sha = artifact_sha256.strip()
        target_metrics = dict(observed_metrics) if observed_metrics is not None else {}
        now_str = datetime.now(UTC).isoformat()

        with self._connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    # Atomic transaction boundary: acquire exclusive transaction-level advisory lock on job_id
                    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (clean_job_id,))
                    cur.execute("SELECT * FROM cfi_scheduled_jobs WHERE job_id = %s", (clean_job_id,))
                    sched_row = cur.fetchone()

                    if not sched_row:
                        raise TrainingProvenanceSecurityError(
                            f"Cannot record completion for job '{clean_job_id}': job has not been scheduled through authorized training coordinator."
                        )
                    cur.execute("SELECT * FROM cfi_started_jobs WHERE job_id = %s", (clean_job_id,))
                    started_row = cur.fetchone()
                    if not started_row:
                        raise TrainingProvenanceSecurityError(
                            f"Cannot record completion for job '{clean_job_id}': job has not been started by an authorized worker."
                        )

                    if pause_checkpoint_marker_file:
                        if self._is_prod:
                            raise TrainingProvenanceSecurityError(
                                f"Arbitrary filesystem checkpoint files are strictly forbidden in production authority for job '{clean_job_id}' (ISO-07)."
                            )
                        with open(pause_checkpoint_marker_file, "w", encoding="utf-8") as f:
                            f.write("IN_TRANSACTION_CHECKPOINT")
                        release_file = pause_checkpoint_marker_file + ".release"
                        for _ in range(30):
                            if os.path.exists(release_file):
                                break
                            time.sleep(0.1)

                    sched_run_id = sched_row["training_run_id"]
                    sched_sim_id = sched_row["simulation_id"]
                    sched_arch = sched_row["architecture"]
                    sched_schema_ver = sched_row["feature_schema_version"]
                    sched_input_dim = sched_row["input_dim"]
                    sched_feature_names = tuple(json.loads(sched_row["feature_names_json"]))
                    sched_consortium_val = sched_row.get("consortium_id")
                    clean_sched_consortium = sched_consortium_val.strip() if sched_consortium_val else None
                    clean_supplied_consortium = (
                        consortium_id.strip() if (consortium_id and consortium_id.strip()) else None
                    )

                    # Production enforcement: scheduled job must have an authoritative bound consortium
                    if self._is_prod and not clean_sched_consortium:
                        raise TrainingProvenanceSecurityError(
                            f"Cannot complete job '{clean_job_id}': authoritative scheduled job lacks a valid consortium binding. "
                            "Production training jobs must establish an authoritative consortium binding before execution."
                        )

                    # Strict consortium matching and rejection of retroactive assignment
                    if clean_supplied_consortium is not None:
                        if clean_sched_consortium is None:
                            raise TrainingProvenanceSecurityError(
                                f"Consortium mismatch for job '{clean_job_id}': job was scheduled without a consortium binding, "
                                f"but completion supplied '{clean_supplied_consortium}'. Retroactive consortium binding is rejected."
                            )
                        if clean_supplied_consortium != clean_sched_consortium:
                            raise TrainingProvenanceSecurityError(
                                f"Consortium ID mismatch for job '{clean_job_id}': scheduled '{clean_sched_consortium}', got '{clean_supplied_consortium}'."
                            )

                    # Derive effective consortium exclusively from the authoritative scheduled record
                    effective_consortium = clean_sched_consortium

                    # Strict metadata & feature contract enforcement
                    if simulation_id and simulation_id.strip() != sched_sim_id:
                        raise TrainingProvenanceSecurityError(
                            f"Simulation ID mismatch for job '{clean_job_id}': scheduled '{sched_sim_id}', got '{simulation_id}'."
                        )
                    if training_run_id and training_run_id.strip() != sched_run_id:
                        raise TrainingProvenanceSecurityError(
                            f"Training run ID mismatch for job '{clean_job_id}': scheduled '{sched_run_id}', got '{training_run_id}'."
                        )
                    if architecture and architecture.strip() != sched_arch:
                        raise TrainingProvenanceSecurityError(
                            f"Architecture mismatch for job '{clean_job_id}': scheduled '{sched_arch}', got '{architecture}'."
                        )
                    if feature_schema_version and feature_schema_version.strip() != sched_schema_ver:
                        raise TrainingProvenanceSecurityError(
                            f"Feature schema version mismatch for job '{clean_job_id}': scheduled '{sched_schema_ver}', got '{feature_schema_version}'."
                        )
                    if input_dim is not None and input_dim != sched_input_dim:
                        raise TrainingProvenanceSecurityError(
                            f"Input dimension mismatch for job '{clean_job_id}': scheduled {sched_input_dim}, got {input_dim}."
                        )
                    if feature_names is not None:
                        if sched_feature_names and tuple(feature_names) != sched_feature_names:
                            raise TrainingProvenanceSecurityError(
                                f"Feature names mismatch for job '{clean_job_id}': scheduled {sched_feature_names}, got {tuple(feature_names)}."
                            )
                        target_fnames = sched_feature_names or tuple(feature_names)
                    else:
                        target_fnames = sched_feature_names

                    evidence_fp = compute_evidence_fingerprint(
                        sched_run_id,
                        sched_sim_id,
                        executed_rounds,
                        executed_epochs,
                        sample_count,
                        clean_sha,
                        sched_arch,
                        sched_schema_ver,
                        sched_input_dim,
                        target_fnames,
                        target_metrics,
                        consortium_id=effective_consortium,
                    )

                    # Check existing completion for idempotency vs conflict
                    cur.execute("SELECT * FROM cfi_completed_jobs WHERE job_id = %s", (clean_job_id,))
                    existing_comp = cur.fetchone()
                    if existing_comp:
                        if existing_comp["evidence_fingerprint"] == evidence_fp:
                            return CompletedJobExecutionRecord(
                                job_id=existing_comp["job_id"],
                                training_run_id=existing_comp["training_run_id"],
                                simulation_id=existing_comp["simulation_id"],
                                completed_at=existing_comp["completed_at"],
                                executed_rounds=existing_comp["executed_rounds"],
                                executed_epochs=existing_comp["executed_epochs"],
                                sample_count=existing_comp["sample_count"],
                                observed_metrics=json.loads(existing_comp["observed_metrics_json"]),
                                artifact_sha256=existing_comp["artifact_sha256"],
                                architecture=existing_comp["architecture"],
                                feature_schema_version=existing_comp["feature_schema_version"],
                                input_dim=existing_comp["input_dim"],
                                feature_names=tuple(json.loads(existing_comp["feature_names_json"])),
                                worker_id=existing_comp["worker_id"],
                                status=existing_comp["status"],
                                git_commit_hash=existing_comp["git_commit_hash"],
                                dataset_manifest_id=existing_comp["dataset_manifest_id"],
                                consortium_id=existing_comp.get("consortium_id"),
                            )
                        raise TrainingProvenanceSecurityError(
                            f"Conflicting completion record for job '{clean_job_id}': cannot overwrite existing completed execution record with conflicting evidence."
                        )

                    # Fetch observations, aggregations, and evaluations for reconciliation
                    cur.execute(
                        "SELECT * FROM cfi_round_observations WHERE job_id = %s ORDER BY round_number ASC, attempt_number ASC",
                        (clean_job_id,),
                    )
                    obs_db_rows = cur.fetchall()
                    obs_objects = [
                        RoundObservation(
                            event_id=r["event_id"],
                            job_id=r["job_id"],
                            training_run_id=r["training_run_id"],
                            simulation_id=r["simulation_id"],
                            worker_id=r["worker_id"],
                            bank_id=r["bank_id"],
                            round_number=r["round_number"],
                            attempt_number=r["attempt_number"],
                            actual_sample_count=r["actual_sample_count"],
                            actual_executed_epochs=r["actual_executed_epochs"],
                            observed_loss=float(r["observed_loss"]) if r["observed_loss"] is not None else None,
                            metrics=json.loads(r["metrics_json"] or "{}"),
                            execution_timestamp=r["execution_timestamp"],
                            model_update_digest=r["model_update_digest"],
                            execution_status=r["execution_status"],
                        )
                        for r in obs_db_rows
                    ]

                    cur.execute(
                        "SELECT * FROM cfi_global_aggregations WHERE job_id = %s ORDER BY round_number ASC",
                        (clean_job_id,),
                    )
                    agg_db_rows = cur.fetchall()
                    agg_objects = [
                        GlobalAggregationEvent(
                            event_id=r["event_id"],
                            job_id=r["job_id"],
                            round_number=r["round_number"],
                            aggregation_algorithm=r["aggregation_algorithm"],
                            accepted_participant_ids=tuple(json.loads(r["accepted_participants_json"])),
                            accepted_local_update_digests=tuple(json.loads(r["accepted_digests_json"])),
                            aggregation_configuration=json.loads(r["aggregation_config_json"] or "{}"),
                            aggregated_global_model_digest=r["aggregated_model_digest"],
                            timestamp=r["timestamp"],
                            aggregation_status=r["aggregation_status"],
                        )
                        for r in agg_db_rows
                    ]

                    cur.execute(
                        "SELECT * FROM cfi_model_evaluations WHERE job_id = %s ORDER BY evaluation_timestamp ASC",
                        (clean_job_id,),
                    )
                    eval_db_rows = cur.fetchall()
                    eval_objects = [
                        ModelEvaluationEvent(
                            event_id=r["event_id"],
                            job_id=r["job_id"],
                            evaluation_run_id=r["evaluation_run_id"],
                            metric_name=r["metric_name"],
                            metric_value=float(r["metric_value"]),
                            evaluated_artifact_digest=r["evaluated_artifact_digest"],
                            evaluator_identity=r["evaluator_identity"],
                            evaluation_timestamp=r["evaluation_timestamp"],
                            evaluation_dataset_manifest=r["dataset_manifest_id"],
                        )
                        for r in eval_db_rows
                    ]

                    reconcile_job_completion_evidence(
                        clean_job_id=clean_job_id,
                        sched_row=sched_row,
                        executed_rounds=executed_rounds,
                        executed_epochs=executed_epochs,
                        sample_count=sample_count,
                        target_metrics=target_metrics,
                        clean_sha=clean_sha,
                        obs_rows=obs_objects,
                        agg_rows=agg_objects,
                        eval_rows=eval_objects,
                        enforce_lifecycle=enforce_lifecycle,
                        is_production_authority=True,
                    )

                    record = CompletedJobExecutionRecord(
                        job_id=clean_job_id,
                        training_run_id=sched_run_id,
                        simulation_id=sched_sim_id,
                        completed_at=now_str,
                        executed_rounds=executed_rounds,
                        executed_epochs=executed_epochs,
                        sample_count=sample_count,
                        observed_metrics=target_metrics,
                        artifact_sha256=clean_sha,
                        architecture=sched_arch,
                        feature_schema_version=sched_schema_ver,
                        input_dim=sched_input_dim,
                        feature_names=target_fnames,
                        worker_id=worker_id,
                        status="COMPLETED",
                        git_commit_hash=git_commit_hash,
                        dataset_manifest_id=dataset_manifest_id,
                        consortium_id=effective_consortium,
                    )
                    record.validate()

                    if simulate_failure_before_commit:
                        raise RuntimeError(
                            f"Simulated execution checkpoint failure before commit for job '{clean_job_id}': transaction rolled back."
                        )

                    try:
                        cur.execute(
                            """
                            INSERT INTO cfi_completed_jobs (
                                job_id, training_run_id, simulation_id, completed_at,
                                executed_rounds, executed_epochs, sample_count,
                                observed_metrics_json, artifact_sha256, architecture,
                                feature_schema_version, input_dim, feature_names_json,
                                worker_id, status, git_commit_hash, dataset_manifest_id,
                                evidence_fingerprint, consortium_id
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                clean_job_id,
                                sched_run_id,
                                sched_sim_id,
                                now_str,
                                executed_rounds,
                                executed_epochs,
                                sample_count,
                                json.dumps(target_metrics),
                                clean_sha,
                                sched_arch,
                                sched_schema_ver,
                                sched_input_dim,
                                json.dumps(list(target_fnames)),
                                worker_id,
                                "COMPLETED",
                                git_commit_hash,
                                dataset_manifest_id,
                                evidence_fp,
                                effective_consortium,
                            ),
                        )
                    except Exception as insert_err:
                        sanitized_insert_err = str(insert_err)
                        if "unique constraint" in sanitized_insert_err.lower() or "23505" in sanitized_insert_err:
                            cur.execute("SELECT * FROM cfi_completed_jobs WHERE job_id = %s", (clean_job_id,))
                            concurrent_comp = cur.fetchone()
                            if concurrent_comp and concurrent_comp["evidence_fingerprint"] == evidence_fp:
                                return CompletedJobExecutionRecord(
                                    job_id=concurrent_comp["job_id"],
                                    training_run_id=concurrent_comp["training_run_id"],
                                    simulation_id=concurrent_comp["simulation_id"],
                                    completed_at=concurrent_comp["completed_at"],
                                    executed_rounds=concurrent_comp["executed_rounds"],
                                    executed_epochs=concurrent_comp["executed_epochs"],
                                    sample_count=concurrent_comp["sample_count"],
                                    observed_metrics=json.loads(concurrent_comp["observed_metrics_json"]),
                                    artifact_sha256=concurrent_comp["artifact_sha256"],
                                    architecture=concurrent_comp["architecture"],
                                    feature_schema_version=concurrent_comp["feature_schema_version"],
                                    input_dim=concurrent_comp["input_dim"],
                                    feature_names=tuple(json.loads(concurrent_comp["feature_names_json"])),
                                    worker_id=concurrent_comp["worker_id"],
                                    status=concurrent_comp["status"],
                                    git_commit_hash=concurrent_comp["git_commit_hash"],
                                    dataset_manifest_id=concurrent_comp["dataset_manifest_id"],
                                    consortium_id=concurrent_comp.get("consortium_id"),
                                )
                            raise TrainingProvenanceSecurityError(
                                f"Conflicting completion record for job '{clean_job_id}': cannot overwrite existing completed execution record with conflicting evidence."
                            ) from None
                        raise

        if pause_after_commit_marker_file:
            if self._is_prod:
                raise TrainingProvenanceSecurityError(
                    f"Arbitrary filesystem checkpoint files are strictly forbidden in production authority for job '{clean_job_id}' (ISO-07)."
                )
            with open(pause_after_commit_marker_file, "w", encoding="utf-8") as f:
                f.write("COMMITTED_CHECKPOINT")
            release_file = pause_after_commit_marker_file + ".release"
            for _ in range(300):
                if os.path.exists(release_file):
                    break
                time.sleep(0.1)

        return record

    def get_completed_job_by_id(self, job_id: str) -> CompletedJobExecutionRecord | None:
        clean_job_id = job_id.strip()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM cfi_completed_jobs WHERE job_id = %s", (clean_job_id,))
            r = cur.fetchone()
            if not r:
                return None
            return CompletedJobExecutionRecord(
                job_id=r["job_id"],
                training_run_id=r["training_run_id"],
                simulation_id=r["simulation_id"],
                completed_at=r["completed_at"],
                executed_rounds=r["executed_rounds"],
                executed_epochs=r["executed_epochs"],
                sample_count=r["sample_count"],
                observed_metrics=json.loads(r["observed_metrics_json"]),
                artifact_sha256=r["artifact_sha256"],
                architecture=r["architecture"],
                feature_schema_version=r["feature_schema_version"],
                input_dim=r["input_dim"],
                feature_names=tuple(json.loads(r["feature_names_json"])),
                worker_id=r["worker_id"],
                status=r["status"],
                git_commit_hash=r["git_commit_hash"],
                dataset_manifest_id=r["dataset_manifest_id"],
                consortium_id=r.get("consortium_id"),
            )

    def get_completed_job_by_run_id(self, run_id: str) -> CompletedJobExecutionRecord | None:
        clean_run_id = run_id.strip()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM cfi_completed_jobs WHERE training_run_id = %s", (clean_run_id,))
                r = cur.fetchone()
                if not r:
                    return None
                return CompletedJobExecutionRecord(
                    job_id=r["job_id"],
                    training_run_id=r["training_run_id"],
                    simulation_id=r["simulation_id"],
                    completed_at=r["completed_at"],
                    executed_rounds=r["executed_rounds"],
                    executed_epochs=r["executed_epochs"],
                    sample_count=r["sample_count"],
                    observed_metrics=json.loads(r["observed_metrics_json"]),
                    artifact_sha256=r["artifact_sha256"],
                    architecture=r["architecture"],
                    feature_schema_version=r["feature_schema_version"],
                    input_dim=r["input_dim"],
                    feature_names=tuple(json.loads(r["feature_names_json"])),
                    worker_id=r["worker_id"],
                    status=r["status"],
                    git_commit_hash=r["git_commit_hash"],
                    dataset_manifest_id=r["dataset_manifest_id"],
                    consortium_id=r.get("consortium_id"),
                )

    def get_scheduled_job(self, job_id: str) -> ScheduledTrainingJob | None:
        clean_job_id = job_id.strip()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM cfi_scheduled_jobs WHERE job_id = %s", (clean_job_id,))
            r = cur.fetchone()
            if not r:
                return None
            return ScheduledTrainingJob(
                job_id=r["job_id"],
                training_run_id=r["training_run_id"],
                simulation_id=r["simulation_id"],
                coordinator_id=r["coordinator_id"],
                architecture=r["architecture"],
                feature_schema_version=r["feature_schema_version"],
                expected_rounds=r["expected_rounds"],
                expected_epochs=r["expected_epochs"],
                input_dim=r["input_dim"],
                feature_names=tuple(json.loads(r["feature_names_json"])),
                permitted_workers=tuple(json.loads(r["permitted_workers_json"] or "[]")),
                config_fingerprint=r["config_fingerprint"],
                scheduled_at=r["scheduled_at"],
                status=r["status"],
                consortium_id=r.get("consortium_id"),
                dp_config=json.loads(r["dp_config_json"]) if r.get("dp_config_json") else None,
            )

    def get_started_job(self, job_id: str) -> StartedTrainingJob | None:
        clean_job_id = job_id.strip()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM cfi_started_jobs WHERE job_id = %s", (clean_job_id,))
            r = cur.fetchone()
            if not r:
                return None
            return StartedTrainingJob(
                job_id=r["job_id"],
                training_run_id=r["training_run_id"],
                simulation_id=r["simulation_id"],
                worker_id=r["worker_id"],
                started_at=r["started_at"],
                status=r["status"],
            )

    def get_or_create_institutional_privacy_budget(
        self,
        bank_id: str,
        dataset_tag: str,
        allocated_epsilon: float = 10.0,
        allocated_delta: float = 1e-5,
    ) -> InstitutionalPrivacyBudget:
        clean_bank = bank_id.strip()
        clean_tag = canonicalize_dataset_tag(clean_bank, dataset_tag)
        if not clean_bank:
            raise ValueError("bank_id must be non-empty")
        if not clean_tag:
            raise ValueError("dataset_tag must be non-empty")
        if allocated_epsilon <= 0.0 or not math.isfinite(allocated_epsilon):
            raise ValueError(f"allocated_epsilon must be positive finite float, got {allocated_epsilon}")
        if allocated_delta <= 0.0 or allocated_delta >= 1.0 or not math.isfinite(allocated_delta):
            raise ValueError(f"allocated_delta must be in (0, 1), got {allocated_delta}")

        now_str = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM cfi_institutional_privacy_budgets WHERE bank_id = %s AND dataset_tag = %s FOR UPDATE",
                        (clean_bank, clean_tag),
                    )
                    r = cur.fetchone()
                    if r:
                        return InstitutionalPrivacyBudget(
                            bank_id=r["bank_id"],
                            dataset_tag=r["dataset_tag"],
                            allocated_epsilon=float(r["allocated_epsilon"]),
                            allocated_delta=float(r["allocated_delta"]),
                            spent_epsilon=float(r["spent_epsilon"]),
                            spent_delta=float(r["spent_delta"]),
                            reserved_epsilon=float(r["reserved_epsilon"]),
                            reserved_delta=float(r["reserved_delta"]),
                            total_steps_executed=int(r["total_steps_executed"]),
                            active_reservations=json.loads(r["active_reservations_json"] or "{}"),
                            history=json.loads(r["history_json"] or "[]"),
                            created_at=r["created_at"],
                            updated_at=r["updated_at"],
                        )
                    cur.execute(
                        """
                        INSERT INTO cfi_institutional_privacy_budgets (
                            bank_id, dataset_tag, allocated_epsilon, allocated_delta,
                            spent_epsilon, spent_delta, reserved_epsilon, reserved_delta,
                            total_steps_executed, active_reservations_json, history_json,
                            created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, 0.0, 0.0, 0.0, 0.0, 0, '{}', '[]', %s, %s)
                        """,
                        (clean_bank, clean_tag, float(allocated_epsilon), float(allocated_delta), now_str, now_str),
                    )
                    return InstitutionalPrivacyBudget(
                        bank_id=clean_bank,
                        dataset_tag=clean_tag,
                        allocated_epsilon=float(allocated_epsilon),
                        allocated_delta=float(allocated_delta),
                        spent_epsilon=0.0,
                        spent_delta=0.0,
                        reserved_epsilon=0.0,
                        reserved_delta=0.0,
                        total_steps_executed=0,
                        active_reservations={},
                        history=[],
                        created_at=now_str,
                        updated_at=now_str,
                    )

    def get_institutional_privacy_budget(
        self,
        bank_id: str,
        dataset_tag: str,
    ) -> InstitutionalPrivacyBudget | None:
        clean_bank = bank_id.strip()
        clean_tag = canonicalize_dataset_tag(clean_bank, dataset_tag)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM cfi_institutional_privacy_budgets WHERE bank_id = %s AND dataset_tag = %s",
                (clean_bank, clean_tag),
            )
            r = cur.fetchone()
            if not r:
                return None
            return InstitutionalPrivacyBudget(
                bank_id=r["bank_id"],
                dataset_tag=r["dataset_tag"],
                allocated_epsilon=float(r["allocated_epsilon"]),
                allocated_delta=float(r["allocated_delta"]),
                spent_epsilon=float(r["spent_epsilon"]),
                spent_delta=float(r["spent_delta"]),
                reserved_epsilon=float(r["reserved_epsilon"]),
                reserved_delta=float(r["reserved_delta"]),
                total_steps_executed=int(r["total_steps_executed"]),
                active_reservations=json.loads(r["active_reservations_json"] or "{}"),
                history=json.loads(r["history_json"] or "[]"),
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )

    def reserve_institutional_privacy_budget(
        self,
        bank_id: str,
        dataset_tag: str,
        job_id: str,
        round_number: int,
        requested_epsilon: float,
        requested_delta: float,
        attempt_id: str | None = None,
    ) -> tuple[bool, str, float]:
        clean_bank = bank_id.strip()
        clean_tag = canonicalize_dataset_tag(clean_bank, dataset_tag)
        clean_job = job_id.strip()
        clean_attempt = attempt_id.strip() if attempt_id is not None else None
        if requested_epsilon <= 0.0 or not math.isfinite(requested_epsilon):
            raise ValueError(f"requested_epsilon must be positive finite float, got {requested_epsilon}")
        if requested_delta < 0.0 or not math.isfinite(requested_delta):
            raise ValueError(f"requested_delta must be non-negative finite float, got {requested_delta}")

        now_str = datetime.now(UTC).isoformat()
        res_key = f"{clean_job}:{round_number}"

        with self._connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM cfi_institutional_privacy_budgets WHERE bank_id = %s AND dataset_tag = %s FOR UPDATE",
                        (clean_bank, clean_tag),
                    )
                    r = cur.fetchone()
                    if not r:
                        return (False, f"No institutional privacy budget found for bank '{clean_bank}', dataset '{clean_tag}'.", 0.0)

                    allocated_eps = float(r["allocated_epsilon"])
                    spent_eps = float(r["spent_epsilon"])
                    reserved_eps = float(r["reserved_epsilon"])
                    allocated_del = float(r["allocated_delta"])
                    spent_del = float(r["spent_delta"])
                    reserved_del = float(r["reserved_delta"])

                    active_res = json.loads(r["active_reservations_json"] or "{}")

                    if res_key in active_res:
                        remaining_eps = max(0.0, allocated_eps - (spent_eps + reserved_eps))
                        return (True, f"Reservation already active for {res_key}.", remaining_eps)

                    if spent_eps + reserved_eps + requested_epsilon > allocated_eps + 1e-9:
                        remaining_eps = max(0.0, allocated_eps - (spent_eps + reserved_eps))
                        return (
                            False,
                            f"Privacy budget exceeded for bank '{clean_bank}', dataset '{clean_tag}': "
                            f"allocated={allocated_eps:.4f}, spent={spent_eps:.4f}, reserved={reserved_eps:.4f}, requested={requested_epsilon:.4f}.",
                            remaining_eps,
                        )

                    if spent_del + reserved_del + requested_delta > allocated_del + 1e-12:
                        remaining_eps = max(0.0, allocated_eps - (spent_eps + reserved_eps))
                        return (
                            False,
                            f"Privacy delta budget exceeded for bank '{clean_bank}', dataset '{clean_tag}': "
                            f"allocated={allocated_del:.2e}, spent={spent_del:.2e}, reserved={reserved_del:.2e}, requested={requested_delta:.2e}.",
                            remaining_eps,
                        )

                    new_reserved_eps = reserved_eps + requested_epsilon
                    new_reserved_del = reserved_del + requested_delta
                    active_res[res_key] = {
                        "job_id": clean_job,
                        "round_number": round_number,
                        "attempt_id": clean_attempt or f"att_{round_number}",
                        "requested_epsilon": requested_epsilon,
                        "requested_delta": requested_delta,
                        "reserved_at": now_str,
                    }

                    cur.execute(
                        """
                        UPDATE cfi_institutional_privacy_budgets
                        SET reserved_epsilon = %s, reserved_delta = %s, active_reservations_json = %s, updated_at = %s
                        WHERE bank_id = %s AND dataset_tag = %s
                        """,
                        (new_reserved_eps, new_reserved_del, json.dumps(active_res), now_str, clean_bank, clean_tag),
                    )
                    remaining_eps = max(0.0, allocated_eps - (spent_eps + new_reserved_eps))
                    return (True, "Reserved successfully.", remaining_eps)

    def commit_institutional_privacy_expenditure(
        self,
        bank_id: str,
        dataset_tag: str,
        job_id: str,
        round_number: int,
        spent_epsilon: float,
        spent_delta: float,
        steps_executed: int,
        attempt_id: str | None = None,
    ) -> InstitutionalPrivacyBudget:
        clean_bank = bank_id.strip()
        clean_tag = canonicalize_dataset_tag(clean_bank, dataset_tag)
        clean_job = job_id.strip()
        clean_attempt = attempt_id.strip() if attempt_id is not None else None
        if spent_epsilon < 0.0 or not math.isfinite(spent_epsilon):
            raise ValueError(f"spent_epsilon must be non-negative finite float, got {spent_epsilon}")
        if spent_delta < 0.0 or not math.isfinite(spent_delta):
            raise ValueError(f"spent_delta must be non-negative finite float, got {spent_delta}")
        if steps_executed < 0:
            raise ValueError(f"steps_executed must be non-negative, got {steps_executed}")

        now_str = datetime.now(UTC).isoformat()
        res_key = f"{clean_job}:{round_number}"

        with self._connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM cfi_institutional_privacy_budgets WHERE bank_id = %s AND dataset_tag = %s FOR UPDATE",
                        (clean_bank, clean_tag),
                    )
                    r = cur.fetchone()
                    if not r:
                        raise TrainingProvenanceSecurityError(
                            f"Cannot commit privacy expenditure: no budget record for bank '{clean_bank}', dataset '{clean_tag}'."
                        )

                    allocated_eps = float(r["allocated_epsilon"])
                    spent_eps = float(r["spent_epsilon"])
                    reserved_eps = float(r["reserved_epsilon"])
                    allocated_del = float(r["allocated_delta"])
                    spent_del = float(r["spent_delta"])
                    reserved_del = float(r["reserved_delta"])
                    total_steps = int(r["total_steps_executed"])
                    active_res = json.loads(r["active_reservations_json"] or "{}")
                    history = json.loads(r["history_json"] or "[]")

                    # Idempotency check for duplicate commit of same attempt
                    if clean_attempt is not None:
                        for h in history:
                            if (
                                isinstance(h, dict)
                                and h.get("job_id") == clean_job
                                and h.get("round_number") == round_number
                                and h.get("attempt_id") == clean_attempt
                                and h.get("status") != "REJECTED_OVERSPEND_EXHAUSTED"
                            ):
                                return InstitutionalPrivacyBudget(
                                    bank_id=clean_bank,
                                    dataset_tag=clean_tag,
                                    allocated_epsilon=allocated_eps,
                                    allocated_delta=allocated_del,
                                    spent_epsilon=spent_eps,
                                    spent_delta=spent_del,
                                    reserved_epsilon=reserved_eps,
                                    reserved_delta=reserved_del,
                                    total_steps_executed=total_steps,
                                    active_reservations=active_res,
                                    history=history,
                                    created_at=r["created_at"],
                                    updated_at=r["updated_at"],
                                )

                    # Release active reservation for this job & round
                    if res_key in active_res:
                        old_res = active_res.pop(res_key)
                        res_eps = float(old_res.get("requested_epsilon", 0.0))
                        res_del = float(old_res.get("requested_delta", 0.0))
                        reserved_eps = max(0.0, reserved_eps - res_eps)
                        reserved_del = max(0.0, reserved_del - res_del)

                    # Check if round was already executed under another attempt
                    round_already_committed = False
                    for h in history:
                        if (
                            isinstance(h, dict)
                            and h.get("job_id") == clean_job
                            and h.get("round_number") == round_number
                            and h.get("status") != "REJECTED_OVERSPEND_EXHAUSTED"
                        ):
                            round_already_committed = True
                            break

                    prior_job_spent_eps = 0.0
                    prior_job_spent_del = 0.0
                    for h in history:
                        if (
                            isinstance(h, dict)
                            and h.get("job_id") == clean_job
                            and h.get("status") != "REJECTED_OVERSPEND_EXHAUSTED"
                        ):
                            prior_job_spent_eps = max(prior_job_spent_eps, float(h.get("spent_epsilon", 0.0)))
                            prior_job_spent_del = max(prior_job_spent_del, float(h.get("spent_delta", 0.0)))

                    if round_already_committed:
                        # Genuinely repeated computation: consumes additional cumulative privacy budget
                        if spent_epsilon <= 0.0:
                            incremental_eps = 0.0
                            incremental_del = 0.0
                        else:
                            incremental_eps = spent_epsilon
                            incremental_del = max(0.0, spent_delta - prior_job_spent_del)
                    else:
                        if spent_epsilon > prior_job_spent_eps:
                            incremental_eps = spent_epsilon - prior_job_spent_eps
                            incremental_del = max(0.0, spent_delta - prior_job_spent_del)
                        else:
                            if round_number > 1 and steps_executed > 0 and spent_epsilon > 0.0:
                                incremental_eps = spent_epsilon
                                incremental_del = max(0.0, spent_delta - prior_job_spent_del)
                            else:
                                incremental_eps = max(0.0, spent_epsilon - prior_job_spent_eps)
                                incremental_del = max(0.0, spent_delta - prior_job_spent_del)

                    new_spent_eps = spent_eps + incremental_eps
                    new_spent_del = spent_del + incremental_del
                    new_total_steps = total_steps + steps_executed

                    # Transactional overspend enforcement & conservative accounting
                    if new_spent_eps > allocated_eps + 1e-9:
                        exhausted_spent_eps = max(spent_eps, allocated_eps)
                        history.append({
                            "job_id": clean_job,
                            "round_number": round_number,
                            "attempt_id": clean_attempt or f"att_{round_number}",
                            "spent_epsilon": spent_epsilon,
                            "spent_delta": spent_delta,
                            "steps_executed": steps_executed,
                            "status": "REJECTED_OVERSPEND_EXHAUSTED",
                            "committed_at": now_str,
                        })
                        cur.execute(
                            """
                            UPDATE cfi_institutional_privacy_budgets
                            SET spent_epsilon = %s, spent_delta = %s, reserved_epsilon = %s, reserved_delta = %s,
                                total_steps_executed = %s, active_reservations_json = %s, history_json = %s, updated_at = %s
                            WHERE bank_id = %s AND dataset_tag = %s
                            """,
                            (
                                exhausted_spent_eps,
                                max(spent_del, allocated_del),
                                reserved_eps,
                                reserved_del,
                                new_total_steps,
                                json.dumps(active_res),
                                json.dumps(history),
                                now_str,
                                clean_bank,
                                clean_tag,
                            ),
                        )
                        raise TrainingProvenanceSecurityError(
                            f"Privacy expenditure rejected for bank '{clean_bank}', dataset '{clean_tag}': "
                            f"actual expenditure ({new_spent_eps:.4f} eps) exceeds allocated budget ({allocated_eps:.4f} eps). "
                            f"Budget exhausted."
                        )
                    if new_spent_del > allocated_del + 1e-12:
                        exhausted_spent_eps = max(spent_eps, allocated_eps)
                        history.append({
                            "job_id": clean_job,
                            "round_number": round_number,
                            "attempt_id": clean_attempt or f"att_{round_number}",
                            "spent_epsilon": spent_epsilon,
                            "spent_delta": spent_delta,
                            "steps_executed": steps_executed,
                            "status": "REJECTED_OVERSPEND_EXHAUSTED",
                            "committed_at": now_str,
                        })
                        cur.execute(
                            """
                            UPDATE cfi_institutional_privacy_budgets
                            SET spent_epsilon = %s, spent_delta = %s, reserved_epsilon = %s, reserved_delta = %s,
                                total_steps_executed = %s, active_reservations_json = %s, history_json = %s, updated_at = %s
                            WHERE bank_id = %s AND dataset_tag = %s
                            """,
                            (
                                max(spent_eps, allocated_eps),
                                max(spent_del, allocated_del),
                                reserved_eps,
                                reserved_del,
                                new_total_steps,
                                json.dumps(active_res),
                                json.dumps(history),
                                now_str,
                                clean_bank,
                                clean_tag,
                            ),
                        )
                        raise TrainingProvenanceSecurityError(
                            f"Privacy delta expenditure rejected for bank '{clean_bank}', dataset '{clean_tag}': "
                            f"actual delta expenditure ({new_spent_del:.4e}) exceeds allocated delta ({allocated_del:.4e}). "
                            f"Budget exhausted."
                        )

                    history.append({
                        "job_id": clean_job,
                        "round_number": round_number,
                        "attempt_id": clean_attempt or f"att_{round_number}",
                        "spent_epsilon": spent_epsilon,
                        "spent_delta": spent_delta,
                        "steps_executed": steps_executed,
                        "status": "COMMITTED",
                        "committed_at": now_str,
                    })

                    cur.execute(
                        """
                        UPDATE cfi_institutional_privacy_budgets
                        SET spent_epsilon = %s, spent_delta = %s, reserved_epsilon = %s, reserved_delta = %s,
                            total_steps_executed = %s, active_reservations_json = %s, history_json = %s, updated_at = %s
                        WHERE bank_id = %s AND dataset_tag = %s
                        """,
                        (
                            new_spent_eps,
                            new_spent_del,
                            reserved_eps,
                            reserved_del,
                            new_total_steps,
                            json.dumps(active_res),
                            json.dumps(history),
                            now_str,
                            clean_bank,
                            clean_tag,
                        ),
                    )

                    return InstitutionalPrivacyBudget(
                        bank_id=clean_bank,
                        dataset_tag=clean_tag,
                        allocated_epsilon=allocated_eps,
                        allocated_delta=allocated_del,
                        spent_epsilon=new_spent_eps,
                        spent_delta=new_spent_del,
                        reserved_epsilon=reserved_eps,
                        reserved_delta=reserved_del,
                        total_steps_executed=new_total_steps,
                        active_reservations=active_res,
                        history=history,
                        created_at=r["created_at"],
                        updated_at=now_str,
                    )

    def clear(self) -> None:
        with self._connect() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("DELETE FROM cfi_institutional_privacy_budgets")
            cur.execute("DELETE FROM cfi_completed_jobs")
            cur.execute("DELETE FROM cfi_model_evaluations")
            cur.execute("DELETE FROM cfi_global_aggregations")
            cur.execute("DELETE FROM cfi_round_observations")
            cur.execute("DELETE FROM cfi_started_jobs")
            cur.execute("DELETE FROM cfi_scheduled_jobs")

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# SQLite Training Job Authority Backend
# ---------------------------------------------------------------------------


class SQLiteTrainingJobAuthority(TrainingJobAuthorityBackend):
    """SQLite-backed authority implementing WAL mode and ACID transactions for development and tests."""

    def __init__(self, db_target: str, in_memory: bool = False, app_env: str = "development") -> None:
        self._lock = threading.RLock()
        self._in_memory = in_memory
        self._db_target = db_target
        self.app_env = app_env
        self._mem_conn: sqlite3.Connection | None = None

        if self._in_memory or db_target in (":memory:", ""):
            self._is_memory = True
            self._file_path = ""
            self._mem_conn = sqlite3.connect(
                f"file:cfi_mem_jobs_{uuid.uuid4().hex}?mode=memory&cache=shared",
                uri=True,
                check_same_thread=False,
                timeout=15.0,
                isolation_level=None,
            )
            self._mem_conn.row_factory = sqlite3.Row
            self._init_sqlite_schema(self._mem_conn)
        else:
            self._is_memory = False
            clean_path = db_target
            if clean_path.startswith("sqlite:///"):
                clean_path = clean_path[len("sqlite:///") :]
            elif clean_path.startswith("sqlite://"):
                clean_path = clean_path[len("sqlite://") :]
            self._file_path = os.path.abspath(clean_path)
            parent = os.path.dirname(self._file_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with self._connect() as conn:
                self._init_sqlite_schema(conn)

    @property
    def backend_type(self) -> str:
        return "sqlite"

    @property
    def _is_prod(self) -> bool:
        return self.app_env.lower() == "production"

    @contextlib.contextmanager
    def _connect(self):
        if self._is_memory:
            assert self._mem_conn is not None
            with self._lock:
                yield self._mem_conn
        else:
            conn = sqlite3.connect(
                self._file_path,
                timeout=15.0,
                check_same_thread=False,
                isolation_level=None,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 15000")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            try:
                yield conn
            finally:
                conn.close()

    def init_schema(self) -> None:
        with self._connect() as conn:
            self._init_sqlite_schema(conn)

    def _init_sqlite_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 15000")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cfi_scheduled_jobs (
                job_id TEXT PRIMARY KEY,
                training_run_id TEXT UNIQUE NOT NULL,
                simulation_id TEXT NOT NULL,
                coordinator_id TEXT NOT NULL,
                architecture TEXT NOT NULL,
                feature_schema_version TEXT NOT NULL,
                expected_rounds INTEGER NOT NULL,
                expected_epochs INTEGER NOT NULL,
                input_dim INTEGER NOT NULL,
                feature_names_json TEXT NOT NULL,
                permitted_workers_json TEXT,
                config_fingerprint TEXT NOT NULL,
                scheduled_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'SCHEDULED',
                consortium_id TEXT,
                dp_config_json TEXT
            )
            """
        )
        with contextlib.suppress(sqlite3.OperationalError):
            conn.execute("ALTER TABLE cfi_scheduled_jobs ADD COLUMN consortium_id TEXT")
        with contextlib.suppress(sqlite3.OperationalError):
            conn.execute("ALTER TABLE cfi_scheduled_jobs ADD COLUMN dp_config_json TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cfi_institutional_privacy_budgets (
                bank_id TEXT NOT NULL,
                dataset_tag TEXT NOT NULL,
                allocated_epsilon REAL NOT NULL,
                allocated_delta REAL NOT NULL,
                spent_epsilon REAL NOT NULL DEFAULT 0.0,
                spent_delta REAL NOT NULL DEFAULT 0.0,
                reserved_epsilon REAL NOT NULL DEFAULT 0.0,
                reserved_delta REAL NOT NULL DEFAULT 0.0,
                total_steps_executed INTEGER NOT NULL DEFAULT 0,
                active_reservations_json TEXT NOT NULL DEFAULT '{}',
                history_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (bank_id, dataset_tag)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cfi_started_jobs (
                job_id TEXT PRIMARY KEY REFERENCES cfi_scheduled_jobs(job_id),
                training_run_id TEXT NOT NULL,
                simulation_id TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'STARTED'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cfi_round_observations (
                event_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES cfi_scheduled_jobs(job_id),
                training_run_id TEXT NOT NULL,
                simulation_id TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                bank_id TEXT NOT NULL,
                round_number INTEGER NOT NULL,
                attempt_number INTEGER NOT NULL DEFAULT 1,
                actual_sample_count INTEGER NOT NULL,
                actual_executed_epochs INTEGER NOT NULL DEFAULT 0,
                observed_loss REAL,
                metrics_json TEXT NOT NULL,
                model_update_digest TEXT,
                execution_timestamp TEXT NOT NULL,
                execution_status TEXT NOT NULL DEFAULT 'COMPLETED',
                payload_digest TEXT NOT NULL,
                UNIQUE(job_id, bank_id, round_number, attempt_number)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cfi_global_aggregations (
                event_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES cfi_scheduled_jobs(job_id),
                round_number INTEGER NOT NULL,
                aggregation_algorithm TEXT NOT NULL,
                accepted_participants_json TEXT NOT NULL,
                accepted_digests_json TEXT NOT NULL,
                aggregation_config_json TEXT NOT NULL,
                aggregated_model_digest TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                aggregation_status TEXT NOT NULL DEFAULT 'COMPLETED',
                payload_digest TEXT NOT NULL,
                UNIQUE(job_id, round_number)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cfi_model_evaluations (
                event_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES cfi_scheduled_jobs(job_id),
                evaluation_run_id TEXT NOT NULL,
                metric_name TEXT NOT NULL,
                metric_value REAL NOT NULL,
                evaluated_artifact_digest TEXT NOT NULL,
                evaluator_identity TEXT NOT NULL,
                evaluation_timestamp TEXT NOT NULL,
                dataset_manifest_id TEXT,
                payload_digest TEXT NOT NULL,
                UNIQUE(job_id, metric_name, evaluated_artifact_digest)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cfi_completed_jobs (
                job_id TEXT PRIMARY KEY REFERENCES cfi_scheduled_jobs(job_id),
                training_run_id TEXT UNIQUE NOT NULL,
                simulation_id TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                executed_rounds INTEGER NOT NULL,
                executed_epochs INTEGER NOT NULL,
                sample_count INTEGER NOT NULL,
                observed_metrics_json TEXT NOT NULL,
                artifact_sha256 TEXT NOT NULL,
                architecture TEXT NOT NULL,
                feature_schema_version TEXT NOT NULL,
                input_dim INTEGER NOT NULL,
                feature_names_json TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'COMPLETED',
                git_commit_hash TEXT,
                dataset_manifest_id TEXT,
                evidence_fingerprint TEXT NOT NULL,
                consortium_id TEXT
            )
            """
        )
        with contextlib.suppress(sqlite3.OperationalError):
            conn.execute("ALTER TABLE cfi_completed_jobs ADD COLUMN consortium_id TEXT")

    def schedule_job(
        self,
        job_id: str,
        training_run_id: str,
        simulation_id: str,
        architecture: str,
        feature_schema_version: str,
        expected_rounds: int,
        expected_epochs: int,
        input_dim: int,
        feature_names: Sequence[str],
        coordinator_id: str = "COORDINATOR",
        permitted_workers: Sequence[str] = (),
        consortium_id: str | None = None,
        dp_config: Mapping[str, Any] | None = None,
    ) -> ScheduledTrainingJob:
        clean_job_id = job_id.strip()
        clean_run_id = training_run_id.strip()
        clean_sim_id = simulation_id.strip()
        clean_consortium_id = consortium_id.strip() if consortium_id else None
        clean_dp_config = validate_dp_config(dp_config)
        fnames_tuple = tuple(feature_names)
        perm_workers_tuple = tuple(permitted_workers)
        now_str = datetime.now(UTC).isoformat()

        cfg_fingerprint = compute_config_fingerprint(
            clean_job_id,
            clean_run_id,
            clean_sim_id,
            architecture,
            feature_schema_version,
            expected_rounds,
            expected_epochs,
            input_dim,
            fnames_tuple,
            coordinator_id=coordinator_id,
            permitted_workers=perm_workers_tuple,
            consortium_id=clean_consortium_id,
            dp_config=clean_dp_config,
        )

        with self._connect() as conn:
            with self._lock:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    cur = conn.execute(
                        "SELECT * FROM cfi_scheduled_jobs WHERE job_id = ?",
                        (clean_job_id,),
                    )
                    existing = cur.fetchone()
                    if existing:
                        if existing["config_fingerprint"] == cfg_fingerprint:
                            conn.execute("COMMIT")
                            return ScheduledTrainingJob(
                                job_id=existing["job_id"],
                                training_run_id=existing["training_run_id"],
                                simulation_id=existing["simulation_id"],
                                coordinator_id=existing["coordinator_id"],
                                architecture=existing["architecture"],
                                feature_schema_version=existing["feature_schema_version"],
                                expected_rounds=existing["expected_rounds"],
                                expected_epochs=existing["expected_epochs"],
                                input_dim=existing["input_dim"],
                                feature_names=tuple(json.loads(existing["feature_names_json"])),
                                permitted_workers=tuple(json.loads(existing["permitted_workers_json"] or "[]")),
                                config_fingerprint=existing["config_fingerprint"],
                                scheduled_at=existing["scheduled_at"],
                                status=existing["status"],
                                consortium_id=existing["consortium_id"] if "consortium_id" in set(existing.keys()) else None,
                                dp_config=json.loads(existing["dp_config_json"]) if "dp_config_json" in set(existing.keys()) and existing["dp_config_json"] else None,
                            )
                        else:
                            raise TrainingProvenanceSecurityError(
                                f"Conflicting training job scheduling for '{clean_job_id}': cannot reschedule existing job with different parameters."
                            )

                    cur = conn.execute(
                        "SELECT job_id FROM cfi_scheduled_jobs WHERE training_run_id = ?",
                        (clean_run_id,),
                    )
                    existing_run = cur.fetchone()
                    if existing_run:
                        raise TrainingProvenanceSecurityError(
                            f"Training run ID '{clean_run_id}' already bound to job '{existing_run['job_id']}': duplicate run ID rejected."
                        )

                    conn.execute(
                        """
                        INSERT INTO cfi_scheduled_jobs (
                            job_id, training_run_id, simulation_id, coordinator_id,
                            architecture, feature_schema_version, expected_rounds,
                            expected_epochs, input_dim, feature_names_json,
                            permitted_workers_json, config_fingerprint, scheduled_at, status,
                            consortium_id, dp_config_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            clean_job_id,
                            clean_run_id,
                            clean_sim_id,
                            coordinator_id,
                            architecture,
                            feature_schema_version,
                            expected_rounds,
                            expected_epochs,
                            input_dim,
                            json.dumps(list(fnames_tuple)),
                            json.dumps(list(perm_workers_tuple)),
                            cfg_fingerprint,
                            now_str,
                            "SCHEDULED",
                            clean_consortium_id,
                            json.dumps(clean_dp_config) if clean_dp_config else None,
                        ),
                    )
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise

        return ScheduledTrainingJob(
            job_id=clean_job_id,
            training_run_id=clean_run_id,
            simulation_id=clean_sim_id,
            coordinator_id=coordinator_id,
            architecture=architecture,
            feature_schema_version=feature_schema_version,
            expected_rounds=expected_rounds,
            expected_epochs=expected_epochs,
            input_dim=input_dim,
            feature_names=fnames_tuple,
            permitted_workers=perm_workers_tuple,
            config_fingerprint=cfg_fingerprint,
            scheduled_at=now_str,
            status="SCHEDULED",
            consortium_id=clean_consortium_id,
            dp_config=clean_dp_config,
        )

    def start_job(self, job_id: str, worker_id: str = "TRAINING_EXECUTOR") -> StartedTrainingJob:
        clean_job_id = job_id.strip()
        now_str = datetime.now(UTC).isoformat()

        with self._connect() as conn:
            with self._lock:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    cur = conn.execute("SELECT * FROM cfi_scheduled_jobs WHERE job_id = ?", (clean_job_id,))
                    sched = cur.fetchone()
                    if not sched:
                        raise TrainingProvenanceSecurityError(
                            f"Cannot start job '{clean_job_id}': job was not scheduled by the training coordinator."
                        )

                    cur = conn.execute("SELECT job_id FROM cfi_completed_jobs WHERE job_id = ?", (clean_job_id,))
                    if cur.fetchone():
                        raise TrainingProvenanceSecurityError(
                            f"Cannot start job '{clean_job_id}': job has already completed."
                        )

                    perm_workers = json.loads(sched["permitted_workers_json"] or "[]")
                    if perm_workers and worker_id not in perm_workers:
                        raise TrainingProvenanceSecurityError(
                            f"Worker '{worker_id}' is not authorized to start job '{clean_job_id}' (permitted: {perm_workers})."
                        )

                    cur = conn.execute("SELECT * FROM cfi_started_jobs WHERE job_id = ?", (clean_job_id,))
                    existing = cur.fetchone()
                    if existing:
                        conn.execute("COMMIT")
                        return StartedTrainingJob(
                            job_id=existing["job_id"],
                            training_run_id=existing["training_run_id"],
                            simulation_id=existing["simulation_id"],
                            worker_id=existing["worker_id"],
                            started_at=existing["started_at"],
                            status=existing["status"],
                        )

                    conn.execute(
                        """
                        INSERT OR IGNORE INTO cfi_started_jobs (
                            job_id, training_run_id, simulation_id, worker_id, started_at, status
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            clean_job_id,
                            sched["training_run_id"],
                            sched["simulation_id"],
                            worker_id,
                            now_str,
                            "STARTED",
                        ),
                    )
                    cur = conn.execute("SELECT * FROM cfi_started_jobs WHERE job_id = ?", (clean_job_id,))
                    row = cur.fetchone()
                    if not row:
                        raise TrainingProvenanceSecurityError(
                            f"Failed to record or retrieve started training job '{clean_job_id}'."
                        )
                    conn.execute("COMMIT")
                    return StartedTrainingJob(
                        job_id=row["job_id"],
                        training_run_id=row["training_run_id"],
                        simulation_id=row["simulation_id"],
                        worker_id=row["worker_id"],
                        started_at=row["started_at"],
                        status=row["status"],
                    )
                except Exception:
                    conn.execute("ROLLBACK")
                    raise

    def record_observation(self, obs: RoundObservation) -> RoundObservation:
        obs.validate()
        clean_job_id = obs.job_id.strip()

        with self._connect() as conn:
            with self._lock:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    cur = conn.execute("SELECT * FROM cfi_started_jobs WHERE job_id = ?", (clean_job_id,))
                    started = cur.fetchone()
                    if not started:
                        raise TrainingProvenanceSecurityError(
                            f"Cannot record observations for job '{clean_job_id}': job is not in STARTED state."
                        )

                    cur = conn.execute("SELECT job_id FROM cfi_completed_jobs WHERE job_id = ?", (clean_job_id,))
                    if cur.fetchone():
                        raise TrainingProvenanceSecurityError(
                            f"Cannot add round observation to finalized completed job '{clean_job_id}'."
                        )

                    cur = conn.execute("SELECT * FROM cfi_round_observations WHERE event_id = ?", (obs.event_id,))
                    existing_evt = cur.fetchone()
                    if existing_evt:
                        if existing_evt["payload_digest"] == obs.payload_digest:
                            conn.execute("COMMIT")
                            return obs
                        raise TrainingProvenanceSecurityError(
                            f"Duplicate event ID '{obs.event_id}' with conflicting content rejected."
                        )

                    cur = conn.execute(
                        """
                        SELECT * FROM cfi_round_observations
                        WHERE job_id = ? AND bank_id = ? AND round_number = ? AND attempt_number = ?
                        """,
                        (clean_job_id, obs.bank_id, obs.round_number, obs.attempt_number),
                    )
                    existing_round = cur.fetchone()
                    if existing_round:
                        if existing_round["payload_digest"] == obs.payload_digest:
                            conn.execute("COMMIT")
                            return obs
                        raise TrainingProvenanceSecurityError(
                            f"Duplicate observation for job '{clean_job_id}' bank '{obs.bank_id}' round {obs.round_number} attempt {obs.attempt_number} with conflicting content rejected."
                        )

                    conn.execute(
                        """
                        INSERT INTO cfi_round_observations (
                            event_id, job_id, training_run_id, simulation_id,
                            worker_id, bank_id, round_number, attempt_number,
                            actual_sample_count, actual_executed_epochs, observed_loss,
                            metrics_json, model_update_digest, execution_timestamp,
                            execution_status, payload_digest
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            obs.event_id,
                            clean_job_id,
                            started["training_run_id"],
                            started["simulation_id"],
                            obs.worker_id,
                            obs.bank_id,
                            obs.round_number,
                            obs.attempt_number,
                            obs.actual_sample_count,
                            obs.actual_executed_epochs,
                            float(obs.observed_loss) if obs.observed_loss is not None else None,
                            json.dumps(dict(obs.metrics)),
                            obs.model_update_digest,
                            obs.execution_timestamp,
                            obs.execution_status,
                            obs.payload_digest,
                        ),
                    )
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise

        return obs

    def get_observations(self, job_id: str) -> list[RoundObservation]:
        clean_job_id = job_id.strip()
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT * FROM cfi_round_observations
                WHERE job_id = ?
                ORDER BY round_number ASC, attempt_number ASC
                """,
                (clean_job_id,),
            )
            rows = cur.fetchall()
            return [
                RoundObservation(
                    event_id=r["event_id"],
                    job_id=r["job_id"],
                    training_run_id=r["training_run_id"],
                    simulation_id=r["simulation_id"],
                    worker_id=r["worker_id"],
                    bank_id=r["bank_id"],
                    round_number=r["round_number"],
                    attempt_number=r["attempt_number"],
                    actual_sample_count=r["actual_sample_count"],
                    actual_executed_epochs=r["actual_executed_epochs"],
                    observed_loss=float(r["observed_loss"]) if r["observed_loss"] is not None else None,
                    metrics=json.loads(r["metrics_json"] or "{}"),
                    execution_timestamp=r["execution_timestamp"],
                    model_update_digest=r["model_update_digest"],
                    execution_status=r["execution_status"],
                )
                for r in rows
            ]

    def record_global_aggregation(self, event: GlobalAggregationEvent) -> GlobalAggregationEvent:
        event.validate()
        clean_job_id = event.job_id.strip()

        with self._connect() as conn:
            with self._lock:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    cur = conn.execute("SELECT * FROM cfi_started_jobs WHERE job_id = ?", (clean_job_id,))
                    if not cur.fetchone():
                        raise TrainingProvenanceSecurityError(
                            f"Cannot record global aggregation for job '{clean_job_id}': job is not in STARTED state."
                        )

                    cur = conn.execute("SELECT job_id FROM cfi_completed_jobs WHERE job_id = ?", (clean_job_id,))
                    if cur.fetchone():
                        raise TrainingProvenanceSecurityError(
                            f"Cannot record global aggregation for finalized job '{clean_job_id}'."
                        )

                    cur = conn.execute("SELECT * FROM cfi_global_aggregations WHERE event_id = ?", (event.event_id,))
                    existing_evt = cur.fetchone()
                    if existing_evt:
                        if existing_evt["payload_digest"] == event.payload_digest:
                            conn.execute("COMMIT")
                            return event
                        raise TrainingProvenanceSecurityError(
                            f"Conflicting global aggregation event ID '{event.event_id}'."
                        )

                    cur = conn.execute(
                        "SELECT * FROM cfi_global_aggregations WHERE job_id = ? AND round_number = ?",
                        (clean_job_id, event.round_number),
                    )
                    existing_round = cur.fetchone()
                    if existing_round:
                        if existing_round["payload_digest"] == event.payload_digest:
                            conn.execute("COMMIT")
                            return event
                        raise TrainingProvenanceSecurityError(
                            f"Conflicting global aggregation for job '{clean_job_id}' round {event.round_number}."
                        )

                    cur = conn.execute(
                        """
                        SELECT bank_id, model_update_digest FROM cfi_round_observations
                        WHERE job_id = ? AND round_number = ? AND execution_status = 'COMPLETED'
                        """,
                        (clean_job_id, event.round_number),
                    )
                    obs_rows = cur.fetchall()
                    obs_dict = {r[0]: r[1] for r in obs_rows}
                    for p_id, d_val in zip(event.accepted_participant_ids, event.accepted_local_update_digests, strict=True):
                        if p_id not in obs_dict or obs_dict[p_id] != d_val:
                            raise TrainingProvenanceSecurityError(
                                f"Direct aggregation forgery: participant '{p_id}' digest '{d_val}' has no authentic completed observation in round {event.round_number} for job '{clean_job_id}'."
                            )

                    conn.execute(
                        """
                        INSERT INTO cfi_global_aggregations (
                            event_id, job_id, round_number, aggregation_algorithm,
                            accepted_participants_json, accepted_digests_json,
                            aggregation_config_json, aggregated_model_digest,
                            timestamp, aggregation_status, payload_digest
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event.event_id,
                            clean_job_id,
                            event.round_number,
                            event.aggregation_algorithm,
                            json.dumps(list(event.accepted_participant_ids)),
                            json.dumps(list(event.accepted_local_update_digests)),
                            json.dumps(dict(event.aggregation_configuration)),
                            event.aggregated_global_model_digest,
                            event.timestamp,
                            event.aggregation_status,
                            event.payload_digest,
                        ),
                    )
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise

        return event

    def get_global_aggregations(self, job_id: str) -> list[GlobalAggregationEvent]:
        clean_job_id = job_id.strip()
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT * FROM cfi_global_aggregations
                WHERE job_id = ?
                ORDER BY round_number ASC
                """,
                (clean_job_id,),
            )
            rows = cur.fetchall()
            return [
                GlobalAggregationEvent(
                    event_id=r["event_id"],
                    job_id=r["job_id"],
                    round_number=r["round_number"],
                    aggregation_algorithm=r["aggregation_algorithm"],
                    accepted_participant_ids=tuple(json.loads(r["accepted_participants_json"])),
                    accepted_local_update_digests=tuple(json.loads(r["accepted_digests_json"])),
                    aggregation_configuration=json.loads(r["aggregation_config_json"] or "{}"),
                    aggregated_global_model_digest=r["aggregated_model_digest"],
                    timestamp=r["timestamp"],
                    aggregation_status=r["aggregation_status"],
                )
                for r in rows
            ]

    def record_model_evaluation(self, event: ModelEvaluationEvent) -> ModelEvaluationEvent:
        event.validate()
        clean_job_id = event.job_id.strip()

        with self._connect() as conn:
            with self._lock:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    cur = conn.execute("SELECT * FROM cfi_started_jobs WHERE job_id = ?", (clean_job_id,))
                    if not cur.fetchone():
                        raise TrainingProvenanceSecurityError(
                            f"Cannot record model evaluation for job '{clean_job_id}': job is not in STARTED state."
                        )

                    cur = conn.execute("SELECT job_id FROM cfi_completed_jobs WHERE job_id = ?", (clean_job_id,))
                    if cur.fetchone():
                        raise TrainingProvenanceSecurityError(
                            f"Cannot record evaluation for finalized completed job '{clean_job_id}'."
                        )

                    cur = conn.execute("SELECT * FROM cfi_model_evaluations WHERE event_id = ?", (event.event_id,))
                    existing = cur.fetchone()
                    if existing:
                        if existing["payload_digest"] == event.payload_digest:
                            conn.execute("COMMIT")
                            return event
                        raise TrainingProvenanceSecurityError(
                            f"Conflicting model evaluation event ID '{event.event_id}'."
                        )

                    cur = conn.execute(
                        """
                        SELECT 1 FROM cfi_global_aggregations
                        WHERE job_id = ? AND aggregated_model_digest = ?
                        """,
                        (clean_job_id, event.evaluated_artifact_digest),
                    )
                    if not cur.fetchone():
                        raise TrainingProvenanceSecurityError(
                            f"Evaluation forgery: evaluated artifact digest '{event.evaluated_artifact_digest}' does not match any authentic global aggregation for job '{clean_job_id}'."
                        )

                    cur = conn.execute(
                        """
                        SELECT * FROM cfi_model_evaluations
                        WHERE job_id = ? AND metric_name = ? AND evaluated_artifact_digest = ?
                        """,
                        (clean_job_id, event.metric_name, event.evaluated_artifact_digest),
                    )
                    existing_metric = cur.fetchone()
                    if existing_metric:
                        if existing_metric["payload_digest"] == event.payload_digest:
                            conn.execute("COMMIT")
                            return event
                        raise TrainingProvenanceSecurityError(
                            f"Conflicting evaluation observation for job '{clean_job_id}' metric '{event.metric_name}'."
                        )

                    conn.execute(
                        """
                        INSERT INTO cfi_model_evaluations (
                            event_id, job_id, evaluation_run_id, metric_name,
                            metric_value, evaluated_artifact_digest, evaluator_identity,
                            evaluation_timestamp, dataset_manifest_id, payload_digest
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event.event_id,
                            clean_job_id,
                            event.evaluation_run_id,
                            event.metric_name,
                            float(event.metric_value),
                            event.evaluated_artifact_digest,
                            event.evaluator_identity,
                            event.evaluation_timestamp,
                            event.evaluation_dataset_manifest,
                            event.payload_digest,
                        ),
                    )
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise

        return event

    def get_model_evaluations(self, job_id: str) -> list[ModelEvaluationEvent]:
        clean_job_id = job_id.strip()
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT * FROM cfi_model_evaluations
                WHERE job_id = ?
                ORDER BY evaluation_timestamp ASC
                """,
                (clean_job_id,),
            )
            rows = cur.fetchall()
            return [
                ModelEvaluationEvent(
                    event_id=r["event_id"],
                    job_id=r["job_id"],
                    evaluation_run_id=r["evaluation_run_id"],
                    metric_name=r["metric_name"],
                    metric_value=float(r["metric_value"]),
                    evaluated_artifact_digest=r["evaluated_artifact_digest"],
                    evaluator_identity=r["evaluator_identity"],
                    evaluation_timestamp=r["evaluation_timestamp"],
                    evaluation_dataset_manifest=r["dataset_manifest_id"],
                )
                for r in rows
            ]

    def complete_job(
        self,
        job_id: str,
        executed_rounds: int,
        executed_epochs: int,
        sample_count: int,
        observed_metrics: dict[str, float],
        artifact_sha256: str,
        worker_id: str = "TRAINING_EXECUTOR",
        git_commit_hash: str | None = None,
        dataset_manifest_id: str | None = None,
        simulation_id: str | None = None,
        training_run_id: str | None = None,
        architecture: str | None = None,
        feature_schema_version: str | None = None,
        input_dim: int | None = None,
        feature_names: Sequence[str] | list[str] | tuple[str, ...] | None = None,
        enforce_lifecycle: bool = True,
        simulate_failure_before_commit: bool = False,
        pause_checkpoint_marker_file: str | None = None,
        pause_after_commit_marker_file: str | None = None,
        consortium_id: str | None = None,
    ) -> CompletedJobExecutionRecord:
        clean_job_id = job_id.strip()
        if self._is_prod and not enforce_lifecycle:
            raise TrainingProvenanceSecurityError(
                f"Cannot complete job '{clean_job_id}': enforce_lifecycle=False is forbidden. Execution observations and aggregation evidence are mandatory."
            )
        clean_sha = artifact_sha256.strip()
        target_metrics = dict(observed_metrics) if observed_metrics is not None else {}
        now_str = datetime.now(UTC).isoformat()

        with self._connect() as conn:
            with self._lock:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    cur = conn.execute("SELECT * FROM cfi_scheduled_jobs WHERE job_id = ?", (clean_job_id,))
                    sched_row = cur.fetchone()

                    if not sched_row:
                        raise TrainingProvenanceSecurityError(
                            f"Cannot record completion for job '{clean_job_id}': job has not been scheduled through authorized training coordinator."
                        )
                    cur = conn.execute("SELECT * FROM cfi_started_jobs WHERE job_id = ?", (clean_job_id,))
                    started_row = cur.fetchone()
                    if not started_row:
                        raise TrainingProvenanceSecurityError(
                            f"Cannot record completion for job '{clean_job_id}': job has not been started by an authorized worker."
                        )

                    sched_run_id = sched_row["training_run_id"]
                    sched_sim_id = sched_row["simulation_id"]
                    sched_arch = sched_row["architecture"]
                    sched_schema_ver = sched_row["feature_schema_version"]
                    sched_input_dim = sched_row["input_dim"]
                    sched_feature_names = tuple(json.loads(sched_row["feature_names_json"]))
                    sched_consortium_val = (
                        sched_row["consortium_id"] if "consortium_id" in set(sched_row.keys()) else None
                    )
                    clean_sched_consortium = sched_consortium_val.strip() if sched_consortium_val else None
                    clean_supplied_consortium = (
                        consortium_id.strip() if (consortium_id and consortium_id.strip()) else None
                    )

                    # Production enforcement: scheduled job must have an authoritative bound consortium
                    if self._is_prod and not clean_sched_consortium:
                        raise TrainingProvenanceSecurityError(
                            f"Cannot complete job '{clean_job_id}': authoritative scheduled job lacks a valid consortium binding. "
                            "Production training jobs must establish an authoritative consortium binding before execution."
                        )

                    # Strict consortium matching and rejection of retroactive assignment
                    if clean_supplied_consortium is not None:
                        if clean_sched_consortium is None:
                            raise TrainingProvenanceSecurityError(
                                f"Consortium mismatch for job '{clean_job_id}': job was scheduled without a consortium binding, "
                                f"but completion supplied '{clean_supplied_consortium}'. Retroactive consortium binding is rejected."
                            )
                        if clean_supplied_consortium != clean_sched_consortium:
                            raise TrainingProvenanceSecurityError(
                                f"Consortium ID mismatch for job '{clean_job_id}': scheduled '{clean_sched_consortium}', got '{clean_supplied_consortium}'."
                            )

                    # Derive effective consortium exclusively from the authoritative scheduled record
                    effective_consortium = clean_sched_consortium

                    if simulation_id and simulation_id.strip() != sched_sim_id:
                        raise TrainingProvenanceSecurityError(
                            f"Simulation ID mismatch for job '{clean_job_id}': scheduled '{sched_sim_id}', got '{simulation_id}'."
                        )
                    if training_run_id and training_run_id.strip() != sched_run_id:
                        raise TrainingProvenanceSecurityError(
                            f"Training run ID mismatch for job '{clean_job_id}': scheduled '{sched_run_id}', got '{training_run_id}'."
                        )
                    if architecture and architecture.strip() != sched_arch:
                        raise TrainingProvenanceSecurityError(
                            f"Architecture mismatch for job '{clean_job_id}': scheduled '{sched_arch}', got '{architecture}'."
                        )
                    if feature_schema_version and feature_schema_version.strip() != sched_schema_ver:
                        raise TrainingProvenanceSecurityError(
                            f"Feature schema version mismatch for job '{clean_job_id}': scheduled '{sched_schema_ver}', got '{feature_schema_version}'."
                        )
                    if input_dim is not None and input_dim != sched_input_dim:
                        raise TrainingProvenanceSecurityError(
                            f"Input dimension mismatch for job '{clean_job_id}': scheduled {sched_input_dim}, got {input_dim}."
                        )
                    if feature_names is not None:
                        if sched_feature_names and tuple(feature_names) != sched_feature_names:
                            raise TrainingProvenanceSecurityError(
                                f"Feature names mismatch for job '{clean_job_id}': scheduled {sched_feature_names}, got {tuple(feature_names)}."
                            )
                        target_fnames = sched_feature_names or tuple(feature_names)
                    else:
                        target_fnames = sched_feature_names

                    evidence_fp = compute_evidence_fingerprint(
                        sched_run_id,
                        sched_sim_id,
                        executed_rounds,
                        executed_epochs,
                        sample_count,
                        clean_sha,
                        sched_arch,
                        sched_schema_ver,
                        sched_input_dim,
                        target_fnames,
                        target_metrics,
                        consortium_id=effective_consortium,
                    )

                    cur = conn.execute("SELECT * FROM cfi_completed_jobs WHERE job_id = ?", (clean_job_id,))
                    existing_comp = cur.fetchone()
                    if existing_comp:
                        if existing_comp["evidence_fingerprint"] == evidence_fp:
                            conn.execute("COMMIT")
                            return CompletedJobExecutionRecord(
                                job_id=existing_comp["job_id"],
                                training_run_id=existing_comp["training_run_id"],
                                simulation_id=existing_comp["simulation_id"],
                                completed_at=existing_comp["completed_at"],
                                executed_rounds=existing_comp["executed_rounds"],
                                executed_epochs=existing_comp["executed_epochs"],
                                sample_count=existing_comp["sample_count"],
                                observed_metrics=json.loads(existing_comp["observed_metrics_json"]),
                                artifact_sha256=existing_comp["artifact_sha256"],
                                architecture=existing_comp["architecture"],
                                feature_schema_version=existing_comp["feature_schema_version"],
                                input_dim=existing_comp["input_dim"],
                                feature_names=tuple(json.loads(existing_comp["feature_names_json"])),
                                worker_id=existing_comp["worker_id"],
                                status=existing_comp["status"],
                                git_commit_hash=existing_comp["git_commit_hash"],
                                dataset_manifest_id=existing_comp["dataset_manifest_id"],
                                consortium_id=existing_comp["consortium_id"] if "consortium_id" in set(existing_comp.keys()) else None,
                            )
                        raise TrainingProvenanceSecurityError(
                            f"Conflicting completion record for job '{clean_job_id}': cannot overwrite existing completed execution record with conflicting evidence."
                        )

                    cur = conn.execute(
                        "SELECT * FROM cfi_round_observations WHERE job_id = ? ORDER BY round_number ASC, attempt_number ASC",
                        (clean_job_id,),
                    )
                    obs_db_rows = cur.fetchall()
                    obs_objects = [
                        RoundObservation(
                            event_id=r["event_id"],
                            job_id=r["job_id"],
                            training_run_id=r["training_run_id"],
                            simulation_id=r["simulation_id"],
                            worker_id=r["worker_id"],
                            bank_id=r["bank_id"],
                            round_number=r["round_number"],
                            attempt_number=r["attempt_number"],
                            actual_sample_count=r["actual_sample_count"],
                            actual_executed_epochs=r["actual_executed_epochs"],
                            observed_loss=float(r["observed_loss"]) if r["observed_loss"] is not None else None,
                            metrics=json.loads(r["metrics_json"] or "{}"),
                            execution_timestamp=r["execution_timestamp"],
                            model_update_digest=r["model_update_digest"],
                            execution_status=r["execution_status"],
                        )
                        for r in obs_db_rows
                    ]

                    cur = conn.execute(
                        "SELECT * FROM cfi_global_aggregations WHERE job_id = ? ORDER BY round_number ASC",
                        (clean_job_id,),
                    )
                    agg_db_rows = cur.fetchall()
                    agg_objects = [
                        GlobalAggregationEvent(
                            event_id=r["event_id"],
                            job_id=r["job_id"],
                            round_number=r["round_number"],
                            aggregation_algorithm=r["aggregation_algorithm"],
                            accepted_participant_ids=tuple(json.loads(r["accepted_participants_json"])),
                            accepted_local_update_digests=tuple(json.loads(r["accepted_digests_json"])),
                            aggregation_configuration=json.loads(r["aggregation_config_json"] or "{}"),
                            aggregated_global_model_digest=r["aggregated_model_digest"],
                            timestamp=r["timestamp"],
                            aggregation_status=r["aggregation_status"],
                        )
                        for r in agg_db_rows
                    ]

                    cur = conn.execute(
                        "SELECT * FROM cfi_model_evaluations WHERE job_id = ? ORDER BY evaluation_timestamp ASC",
                        (clean_job_id,),
                    )
                    eval_db_rows = cur.fetchall()
                    eval_objects = [
                        ModelEvaluationEvent(
                            event_id=r["event_id"],
                            job_id=r["job_id"],
                            evaluation_run_id=r["evaluation_run_id"],
                            metric_name=r["metric_name"],
                            metric_value=float(r["metric_value"]),
                            evaluated_artifact_digest=r["evaluated_artifact_digest"],
                            evaluator_identity=r["evaluator_identity"],
                            evaluation_timestamp=r["evaluation_timestamp"],
                            evaluation_dataset_manifest=r["dataset_manifest_id"],
                        )
                        for r in eval_db_rows
                    ]

                    reconcile_job_completion_evidence(
                        clean_job_id=clean_job_id,
                        sched_row=sched_row,
                        executed_rounds=executed_rounds,
                        executed_epochs=executed_epochs,
                        sample_count=sample_count,
                        target_metrics=target_metrics,
                        clean_sha=clean_sha,
                        obs_rows=obs_objects,
                        agg_rows=agg_objects,
                        eval_rows=eval_objects,
                        enforce_lifecycle=enforce_lifecycle,
                        is_production_authority=self._is_prod,
                    )

                    record = CompletedJobExecutionRecord(
                        job_id=clean_job_id,
                        training_run_id=sched_run_id,
                        simulation_id=sched_sim_id,
                        completed_at=now_str,
                        executed_rounds=executed_rounds,
                        executed_epochs=executed_epochs,
                        sample_count=sample_count,
                        observed_metrics=target_metrics,
                        artifact_sha256=clean_sha,
                        architecture=sched_arch,
                        feature_schema_version=sched_schema_ver,
                        input_dim=sched_input_dim,
                        feature_names=target_fnames,
                        worker_id=worker_id,
                        status="COMPLETED",
                        git_commit_hash=git_commit_hash,
                        dataset_manifest_id=dataset_manifest_id,
                        consortium_id=effective_consortium,
                    )
                    record.validate()

                    if simulate_failure_before_commit:
                        conn.execute("ROLLBACK")
                        raise RuntimeError(
                            f"Simulated execution checkpoint failure before commit for job '{clean_job_id}': transaction rolled back."
                        )

                    conn.execute(
                        """
                        INSERT INTO cfi_completed_jobs (
                            job_id, training_run_id, simulation_id, completed_at,
                            executed_rounds, executed_epochs, sample_count,
                            observed_metrics_json, artifact_sha256, architecture,
                            feature_schema_version, input_dim, feature_names_json,
                            worker_id, status, git_commit_hash, dataset_manifest_id,
                            evidence_fingerprint, consortium_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            clean_job_id,
                            sched_run_id,
                            sched_sim_id,
                            now_str,
                            executed_rounds,
                            executed_epochs,
                            sample_count,
                            json.dumps(target_metrics),
                            clean_sha,
                            sched_arch,
                            sched_schema_ver,
                            sched_input_dim,
                            json.dumps(list(target_fnames)),
                            worker_id,
                            "COMPLETED",
                            git_commit_hash,
                            dataset_manifest_id,
                            evidence_fp,
                            effective_consortium,
                        ),
                    )
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise

        if pause_after_commit_marker_file:
            with open(pause_after_commit_marker_file, "w", encoding="utf-8") as f:
                f.write("COMMITTED_CHECKPOINT")
            release_file = pause_after_commit_marker_file + ".release"
            for _ in range(300):
                if os.path.exists(release_file):
                    break
                time.sleep(0.1)

        return record

    def get_completed_job_by_id(self, job_id: str) -> CompletedJobExecutionRecord | None:
        clean_job_id = job_id.strip()
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM cfi_completed_jobs WHERE job_id = ?", (clean_job_id,))
            r = cur.fetchone()
            if not r:
                return None
            return CompletedJobExecutionRecord(
                job_id=r["job_id"],
                training_run_id=r["training_run_id"],
                simulation_id=r["simulation_id"],
                completed_at=r["completed_at"],
                executed_rounds=r["executed_rounds"],
                executed_epochs=r["executed_epochs"],
                sample_count=r["sample_count"],
                observed_metrics=json.loads(r["observed_metrics_json"]),
                artifact_sha256=r["artifact_sha256"],
                architecture=r["architecture"],
                feature_schema_version=r["feature_schema_version"],
                input_dim=r["input_dim"],
                feature_names=tuple(json.loads(r["feature_names_json"])),
                worker_id=r["worker_id"],
                status=r["status"],
                git_commit_hash=r["git_commit_hash"],
                dataset_manifest_id=r["dataset_manifest_id"],
                consortium_id=r["consortium_id"] if "consortium_id" in set(r.keys()) else None,
            )

    def get_completed_job_by_run_id(self, run_id: str) -> CompletedJobExecutionRecord | None:
        clean_run_id = run_id.strip()
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM cfi_completed_jobs WHERE training_run_id = ?", (clean_run_id,))
            r = cur.fetchone()
            if not r:
                return None
            return CompletedJobExecutionRecord(
                job_id=r["job_id"],
                training_run_id=r["training_run_id"],
                simulation_id=r["simulation_id"],
                completed_at=r["completed_at"],
                executed_rounds=r["executed_rounds"],
                executed_epochs=r["executed_epochs"],
                sample_count=r["sample_count"],
                observed_metrics=json.loads(r["observed_metrics_json"]),
                artifact_sha256=r["artifact_sha256"],
                architecture=r["architecture"],
                feature_schema_version=r["feature_schema_version"],
                input_dim=r["input_dim"],
                feature_names=tuple(json.loads(r["feature_names_json"])),
                worker_id=r["worker_id"],
                status=r["status"],
                git_commit_hash=r["git_commit_hash"],
                dataset_manifest_id=r["dataset_manifest_id"],
                consortium_id=r["consortium_id"] if "consortium_id" in set(r.keys()) else None,
            )

    def get_scheduled_job(self, job_id: str) -> ScheduledTrainingJob | None:
        clean_job_id = job_id.strip()
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM cfi_scheduled_jobs WHERE job_id = ?", (clean_job_id,))
            r = cur.fetchone()
            if not r:
                return None
            return ScheduledTrainingJob(
                job_id=r["job_id"],
                training_run_id=r["training_run_id"],
                simulation_id=r["simulation_id"],
                coordinator_id=r["coordinator_id"],
                architecture=r["architecture"],
                feature_schema_version=r["feature_schema_version"],
                expected_rounds=r["expected_rounds"],
                expected_epochs=r["expected_epochs"],
                input_dim=r["input_dim"],
                feature_names=tuple(json.loads(r["feature_names_json"])),
                permitted_workers=tuple(json.loads(r["permitted_workers_json"] or "[]")),
                config_fingerprint=r["config_fingerprint"],
                scheduled_at=r["scheduled_at"],
                status=r["status"],
                consortium_id=r["consortium_id"] if "consortium_id" in set(r.keys()) else None,
                dp_config=json.loads(r["dp_config_json"]) if "dp_config_json" in set(r.keys()) and r["dp_config_json"] else None,
            )

    def get_started_job(self, job_id: str) -> StartedTrainingJob | None:
        clean_job_id = job_id.strip()
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM cfi_started_jobs WHERE job_id = ?", (clean_job_id,))
            r = cur.fetchone()
            if not r:
                return None
            return StartedTrainingJob(
                job_id=r["job_id"],
                training_run_id=r["training_run_id"],
                simulation_id=r["simulation_id"],
                worker_id=r["worker_id"],
                started_at=r["started_at"],
                status=r["status"],
            )

    def get_or_create_institutional_privacy_budget(
        self,
        bank_id: str,
        dataset_tag: str,
        allocated_epsilon: float = 10.0,
        allocated_delta: float = 1e-5,
    ) -> InstitutionalPrivacyBudget:
        clean_bank = bank_id.strip()
        clean_tag = canonicalize_dataset_tag(clean_bank, dataset_tag)
        if not clean_bank:
            raise ValueError("bank_id must be non-empty")
        if not clean_tag:
            raise ValueError("dataset_tag must be non-empty")
        if allocated_epsilon <= 0.0 or not math.isfinite(allocated_epsilon):
            raise ValueError(f"allocated_epsilon must be positive finite float, got {allocated_epsilon}")
        if allocated_delta <= 0.0 or allocated_delta >= 1.0 or not math.isfinite(allocated_delta):
            raise ValueError(f"allocated_delta must be in (0, 1), got {allocated_delta}")

        now_str = datetime.now(UTC).isoformat()
        with self._connect() as conn, self._lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute(
                    "SELECT * FROM cfi_institutional_privacy_budgets WHERE bank_id = ? AND dataset_tag = ?",
                    (clean_bank, clean_tag),
                )
                r = cur.fetchone()
                if r:
                    conn.execute("COMMIT")
                    return InstitutionalPrivacyBudget(
                        bank_id=r["bank_id"],
                        dataset_tag=r["dataset_tag"],
                        allocated_epsilon=float(r["allocated_epsilon"]),
                        allocated_delta=float(r["allocated_delta"]),
                        spent_epsilon=float(r["spent_epsilon"]),
                        spent_delta=float(r["spent_delta"]),
                        reserved_epsilon=float(r["reserved_epsilon"]),
                        reserved_delta=float(r["reserved_delta"]),
                        total_steps_executed=int(r["total_steps_executed"]),
                        active_reservations=json.loads(r["active_reservations_json"] or "{}"),
                        history=json.loads(r["history_json"] or "[]"),
                        created_at=r["created_at"],
                        updated_at=r["updated_at"],
                    )
                conn.execute(
                    """
                    INSERT INTO cfi_institutional_privacy_budgets (
                        bank_id, dataset_tag, allocated_epsilon, allocated_delta,
                        spent_epsilon, spent_delta, reserved_epsilon, reserved_delta,
                        total_steps_executed, active_reservations_json, history_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 0.0, 0.0, 0.0, 0.0, 0, '{}', '[]', ?, ?)
                    """,
                    (clean_bank, clean_tag, float(allocated_epsilon), float(allocated_delta), now_str, now_str),
                )
                conn.execute("COMMIT")
                return InstitutionalPrivacyBudget(
                    bank_id=clean_bank,
                    dataset_tag=clean_tag,
                    allocated_epsilon=float(allocated_epsilon),
                    allocated_delta=float(allocated_delta),
                    spent_epsilon=0.0,
                    spent_delta=0.0,
                    reserved_epsilon=0.0,
                    reserved_delta=0.0,
                    total_steps_executed=0,
                    active_reservations={},
                    history=[],
                    created_at=now_str,
                    updated_at=now_str,
                )
            except Exception:
                with contextlib.suppress(Exception):
                    conn.execute("ROLLBACK")
                raise

    def get_institutional_privacy_budget(
        self,
        bank_id: str,
        dataset_tag: str,
    ) -> InstitutionalPrivacyBudget | None:
        clean_bank = bank_id.strip()
        clean_tag = canonicalize_dataset_tag(clean_bank, dataset_tag)
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM cfi_institutional_privacy_budgets WHERE bank_id = ? AND dataset_tag = ?",
                (clean_bank, clean_tag),
            )
            r = cur.fetchone()
            if not r:
                return None
            return InstitutionalPrivacyBudget(
                bank_id=r["bank_id"],
                dataset_tag=r["dataset_tag"],
                allocated_epsilon=float(r["allocated_epsilon"]),
                allocated_delta=float(r["allocated_delta"]),
                spent_epsilon=float(r["spent_epsilon"]),
                spent_delta=float(r["spent_delta"]),
                reserved_epsilon=float(r["reserved_epsilon"]),
                reserved_delta=float(r["reserved_delta"]),
                total_steps_executed=int(r["total_steps_executed"]),
                active_reservations=json.loads(r["active_reservations_json"] or "{}"),
                history=json.loads(r["history_json"] or "[]"),
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )

    def reserve_institutional_privacy_budget(
        self,
        bank_id: str,
        dataset_tag: str,
        job_id: str,
        round_number: int,
        requested_epsilon: float,
        requested_delta: float,
        attempt_id: str | None = None,
    ) -> tuple[bool, str, float]:
        clean_bank = bank_id.strip()
        clean_tag = canonicalize_dataset_tag(clean_bank, dataset_tag)
        clean_job = job_id.strip()
        clean_attempt = attempt_id.strip() if attempt_id is not None else None
        if requested_epsilon <= 0.0 or not math.isfinite(requested_epsilon):
            raise ValueError(f"requested_epsilon must be positive finite float, got {requested_epsilon}")
        if requested_delta < 0.0 or not math.isfinite(requested_delta):
            raise ValueError(f"requested_delta must be non-negative finite float, got {requested_delta}")

        now_str = datetime.now(UTC).isoformat()
        res_key = f"{clean_job}:{round_number}"

        with self._connect() as conn, self._lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute(
                    "SELECT * FROM cfi_institutional_privacy_budgets WHERE bank_id = ? AND dataset_tag = ?",
                    (clean_bank, clean_tag),
                )
                r = cur.fetchone()
                if not r:
                    conn.execute("COMMIT")
                    return (False, f"No institutional privacy budget found for bank '{clean_bank}', dataset '{clean_tag}'.", 0.0)

                allocated_eps = float(r["allocated_epsilon"])
                spent_eps = float(r["spent_epsilon"])
                reserved_eps = float(r["reserved_epsilon"])
                allocated_del = float(r["allocated_delta"])
                spent_del = float(r["spent_delta"])
                reserved_del = float(r["reserved_delta"])

                active_res = json.loads(r["active_reservations_json"] or "{}")

                if res_key in active_res:
                    conn.execute("COMMIT")
                    remaining_eps = max(0.0, allocated_eps - (spent_eps + reserved_eps))
                    return (True, f"Reservation already active for {res_key}.", remaining_eps)

                if spent_eps + reserved_eps + requested_epsilon > allocated_eps + 1e-9:
                    conn.execute("COMMIT")
                    remaining_eps = max(0.0, allocated_eps - (spent_eps + reserved_eps))
                    return (
                        False,
                        f"Privacy budget exceeded for bank '{clean_bank}', dataset '{clean_tag}': "
                        f"allocated={allocated_eps:.4f}, spent={spent_eps:.4f}, reserved={reserved_eps:.4f}, requested={requested_epsilon:.4f}.",
                        remaining_eps,
                    )

                if spent_del + reserved_del + requested_delta > allocated_del + 1e-12:
                    conn.execute("COMMIT")
                    remaining_eps = max(0.0, allocated_eps - (spent_eps + reserved_eps))
                    return (
                        False,
                        f"Privacy delta budget exceeded for bank '{clean_bank}', dataset '{clean_tag}': "
                        f"allocated={allocated_del:.2e}, spent={spent_del:.2e}, reserved={reserved_del:.2e}, requested={requested_delta:.2e}.",
                        remaining_eps,
                    )

                new_reserved_eps = reserved_eps + requested_epsilon
                new_reserved_del = reserved_del + requested_delta
                active_res[res_key] = {
                    "job_id": clean_job,
                    "round_number": round_number,
                    "attempt_id": clean_attempt or f"att_{round_number}",
                    "requested_epsilon": requested_epsilon,
                    "requested_delta": requested_delta,
                    "reserved_at": now_str,
                }

                conn.execute(
                    """
                    UPDATE cfi_institutional_privacy_budgets
                    SET reserved_epsilon = ?, reserved_delta = ?, active_reservations_json = ?, updated_at = ?
                    WHERE bank_id = ? AND dataset_tag = ?
                    """,
                    (new_reserved_eps, new_reserved_del, json.dumps(active_res), now_str, clean_bank, clean_tag),
                )
                conn.execute("COMMIT")
                remaining_eps = max(0.0, allocated_eps - (spent_eps + new_reserved_eps))
                return (True, "Reserved successfully.", remaining_eps)
            except Exception:
                with contextlib.suppress(Exception):
                    conn.execute("ROLLBACK")
                raise

    def commit_institutional_privacy_expenditure(
        self,
        bank_id: str,
        dataset_tag: str,
        job_id: str,
        round_number: int,
        spent_epsilon: float,
        spent_delta: float,
        steps_executed: int,
        attempt_id: str | None = None,
    ) -> InstitutionalPrivacyBudget:
        clean_bank = bank_id.strip()
        clean_tag = canonicalize_dataset_tag(clean_bank, dataset_tag)
        clean_job = job_id.strip()
        clean_attempt = attempt_id.strip() if attempt_id is not None else None
        if spent_epsilon < 0.0 or not math.isfinite(spent_epsilon):
            raise ValueError(f"spent_epsilon must be non-negative finite float, got {spent_epsilon}")
        if spent_delta < 0.0 or not math.isfinite(spent_delta):
            raise ValueError(f"spent_delta must be non-negative finite float, got {spent_delta}")
        if steps_executed < 0:
            raise ValueError(f"steps_executed must be non-negative, got {steps_executed}")

        now_str = datetime.now(UTC).isoformat()
        res_key = f"{clean_job}:{round_number}"

        with self._connect() as conn, self._lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute(
                    "SELECT * FROM cfi_institutional_privacy_budgets WHERE bank_id = ? AND dataset_tag = ?",
                    (clean_bank, clean_tag),
                )
                r = cur.fetchone()
                if not r:
                    raise TrainingProvenanceSecurityError(
                        f"Cannot commit privacy expenditure: no budget record for bank '{clean_bank}', dataset '{clean_tag}'."
                    )

                allocated_eps = float(r["allocated_epsilon"])
                spent_eps = float(r["spent_epsilon"])
                reserved_eps = float(r["reserved_epsilon"])
                allocated_del = float(r["allocated_delta"])
                spent_del = float(r["spent_delta"])
                reserved_del = float(r["reserved_delta"])
                total_steps = int(r["total_steps_executed"])
                active_res = json.loads(r["active_reservations_json"] or "{}")
                history = json.loads(r["history_json"] or "[]")

                # Idempotency check for duplicate commit of same attempt
                if clean_attempt is not None:
                    for h in history:
                        if (
                            isinstance(h, dict)
                            and h.get("job_id") == clean_job
                            and h.get("round_number") == round_number
                            and h.get("attempt_id") == clean_attempt
                            and h.get("status") != "REJECTED_OVERSPEND_EXHAUSTED"
                        ):
                            conn.execute("COMMIT")
                            return InstitutionalPrivacyBudget(
                                bank_id=clean_bank,
                                dataset_tag=clean_tag,
                                allocated_epsilon=allocated_eps,
                                allocated_delta=allocated_del,
                                spent_epsilon=spent_eps,
                                spent_delta=spent_del,
                                reserved_epsilon=reserved_eps,
                                reserved_delta=reserved_del,
                                total_steps_executed=total_steps,
                                active_reservations=active_res,
                                history=history,
                                created_at=r["created_at"],
                                updated_at=r["updated_at"],
                            )

                # Release active reservation for this job & round
                if res_key in active_res:
                    old_res = active_res.pop(res_key)
                    res_eps = float(old_res.get("requested_epsilon", 0.0))
                    res_del = float(old_res.get("requested_delta", 0.0))
                    reserved_eps = max(0.0, reserved_eps - res_eps)
                    reserved_del = max(0.0, reserved_del - res_del)

                # Check if round was already executed under another attempt
                round_already_committed = False
                for h in history:
                    if (
                        isinstance(h, dict)
                        and h.get("job_id") == clean_job
                        and h.get("round_number") == round_number
                        and h.get("status") != "REJECTED_OVERSPEND_EXHAUSTED"
                    ):
                        round_already_committed = True
                        break

                prior_job_spent_eps = 0.0
                prior_job_spent_del = 0.0
                for h in history:
                    if (
                        isinstance(h, dict)
                        and h.get("job_id") == clean_job
                        and h.get("status") != "REJECTED_OVERSPEND_EXHAUSTED"
                    ):
                        prior_job_spent_eps = max(prior_job_spent_eps, float(h.get("spent_epsilon", 0.0)))
                        prior_job_spent_del = max(prior_job_spent_del, float(h.get("spent_delta", 0.0)))

                if round_already_committed:
                    # Genuinely repeated computation: consumes additional cumulative privacy budget
                    if spent_epsilon <= 0.0:
                        incremental_eps = 0.0
                        incremental_del = 0.0
                    else:
                        incremental_eps = spent_epsilon
                        incremental_del = max(0.0, spent_delta - prior_job_spent_del)
                else:
                    if spent_epsilon > prior_job_spent_eps:
                        incremental_eps = spent_epsilon - prior_job_spent_eps
                        incremental_del = max(0.0, spent_delta - prior_job_spent_del)
                    else:
                        # If worker restarted or accountant reset without continuity, charge reported expenditure
                        if round_number > 1 and steps_executed > 0 and spent_epsilon > 0.0:
                            incremental_eps = spent_epsilon
                            incremental_del = max(0.0, spent_delta - prior_job_spent_del)
                        else:
                            incremental_eps = max(0.0, spent_epsilon - prior_job_spent_eps)
                            incremental_del = max(0.0, spent_delta - prior_job_spent_del)

                new_spent_eps = spent_eps + incremental_eps
                new_spent_del = spent_del + incremental_del
                new_total_steps = total_steps + steps_executed

                # Transactional overspend enforcement & conservative accounting
                if new_spent_eps > allocated_eps + 1e-9:
                    exhausted_spent_eps = max(spent_eps, allocated_eps)
                    history.append({
                        "job_id": clean_job,
                        "round_number": round_number,
                        "attempt_id": clean_attempt or f"att_{round_number}",
                        "spent_epsilon": spent_epsilon,
                        "spent_delta": spent_delta,
                        "steps_executed": steps_executed,
                        "status": "REJECTED_OVERSPEND_EXHAUSTED",
                        "committed_at": now_str,
                    })
                    conn.execute(
                        """
                        UPDATE cfi_institutional_privacy_budgets
                        SET spent_epsilon = ?, spent_delta = ?, reserved_epsilon = ?, reserved_delta = ?,
                            total_steps_executed = ?, active_reservations_json = ?, history_json = ?, updated_at = ?
                        WHERE bank_id = ? AND dataset_tag = ?
                        """,
                        (
                            exhausted_spent_eps,
                            max(spent_del, allocated_del),
                            reserved_eps,
                            reserved_del,
                            new_total_steps,
                            json.dumps(active_res),
                            json.dumps(history),
                            now_str,
                            clean_bank,
                            clean_tag,
                        ),
                    )
                    conn.execute("COMMIT")
                    raise TrainingProvenanceSecurityError(
                        f"Privacy expenditure rejected for bank '{clean_bank}', dataset '{clean_tag}': "
                        f"actual expenditure ({new_spent_eps:.4f} eps) exceeds allocated budget ({allocated_eps:.4f} eps). "
                        f"Budget exhausted."
                    )
                if new_spent_del > allocated_del + 1e-12:
                    exhausted_spent_eps = max(spent_eps, allocated_eps)
                    history.append({
                        "job_id": clean_job,
                        "round_number": round_number,
                        "attempt_id": clean_attempt or f"att_{round_number}",
                        "spent_epsilon": spent_epsilon,
                        "spent_delta": spent_delta,
                        "steps_executed": steps_executed,
                        "status": "REJECTED_OVERSPEND_EXHAUSTED",
                        "committed_at": now_str,
                    })
                    conn.execute(
                        """
                        UPDATE cfi_institutional_privacy_budgets
                        SET spent_epsilon = ?, spent_delta = ?, reserved_epsilon = ?, reserved_delta = ?,
                            total_steps_executed = ?, active_reservations_json = ?, history_json = ?, updated_at = ?
                        WHERE bank_id = ? AND dataset_tag = ?
                        """,
                        (
                            exhausted_spent_eps,
                            max(spent_del, allocated_del),
                            reserved_eps,
                            reserved_del,
                            new_total_steps,
                            json.dumps(active_res),
                            json.dumps(history),
                            now_str,
                            clean_bank,
                            clean_tag,
                        ),
                    )
                    conn.execute("COMMIT")
                    raise TrainingProvenanceSecurityError(
                        f"Privacy delta expenditure rejected for bank '{clean_bank}', dataset '{clean_tag}': "
                        f"actual delta expenditure ({new_spent_del:.4e}) exceeds allocated delta ({allocated_del:.4e}). "
                        f"Budget exhausted."
                    )

                history.append({
                    "job_id": clean_job,
                    "round_number": round_number,
                    "attempt_id": clean_attempt or f"att_{round_number}",
                    "spent_epsilon": spent_epsilon,
                    "spent_delta": spent_delta,
                    "steps_executed": steps_executed,
                    "status": "COMMITTED",
                    "committed_at": now_str,
                })

                conn.execute(
                    """
                    UPDATE cfi_institutional_privacy_budgets
                    SET spent_epsilon = ?, spent_delta = ?, reserved_epsilon = ?, reserved_delta = ?,
                        total_steps_executed = ?, active_reservations_json = ?, history_json = ?, updated_at = ?
                    WHERE bank_id = ? AND dataset_tag = ?
                    """,
                    (
                        new_spent_eps,
                        new_spent_del,
                        reserved_eps,
                        reserved_del,
                        new_total_steps,
                        json.dumps(active_res),
                        json.dumps(history),
                        now_str,
                        clean_bank,
                        clean_tag,
                    ),
                )
                conn.execute("COMMIT")

                return InstitutionalPrivacyBudget(
                    bank_id=clean_bank,
                    dataset_tag=clean_tag,
                    allocated_epsilon=allocated_eps,
                    allocated_delta=allocated_del,
                    spent_epsilon=new_spent_eps,
                    spent_delta=new_spent_del,
                    reserved_epsilon=reserved_eps,
                    reserved_delta=reserved_del,
                    total_steps_executed=new_total_steps,
                    active_reservations=active_res,
                    history=history,
                    created_at=r["created_at"],
                    updated_at=now_str,
                )
            except Exception:
                with contextlib.suppress(Exception):
                    conn.execute("ROLLBACK")
                raise

    def clear(self) -> None:
        with self._connect() as conn, self._lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("DELETE FROM cfi_institutional_privacy_budgets")
                conn.execute("DELETE FROM cfi_completed_jobs")
                conn.execute("DELETE FROM cfi_model_evaluations")
                conn.execute("DELETE FROM cfi_global_aggregations")
                conn.execute("DELETE FROM cfi_round_observations")
                conn.execute("DELETE FROM cfi_started_jobs")
                conn.execute("DELETE FROM cfi_scheduled_jobs")
                conn.execute("COMMIT")
            except Exception:
                with contextlib.suppress(Exception):
                    conn.execute("ROLLBACK")
                raise

    def close(self) -> None:
        if self._mem_conn:
            with contextlib.suppress(Exception):
                self._mem_conn.close()
            self._mem_conn = None
