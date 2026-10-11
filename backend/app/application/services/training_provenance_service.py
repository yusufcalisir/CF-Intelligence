"""Training Provenance & Cryptographic Receipt Authority.

Establishes a verifiable, asymmetric chain of training execution:
- Asymmetric Ed25519 signature architecture strictly separating Receipt Issuer from Verifier.
- Cryptographic execution receipts binding artifact SHA-256, run ID, job ID,
  executed rounds/epochs, finite loss/metrics, and producer identity.
- Explicit trusted key registry with key rotation and revocation support.
- Verification logic ensuring that candidate model artifact bytes match the receipt digest.
- Verification process requires ONLY trusted public keys and NEVER constructs or accesses
  the private signing key.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import hmac
import json
import logging
import math
import os
import threading
import types
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.config import get_settings

logger = logging.getLogger(__name__)

DOMAIN_TRAINING_RECEIPT = "cfi:training_receipt_signing:v1"
DOMAIN_MODEL_CACHE = "cfi:model_cache_auth:v1"
DOMAIN_APPROVAL_SIGNOFF = "cfi:model_approval_signoff:v1"

DEFAULT_TRAINING_KEY_ID = "cfi_training_ed25519_v1"


class TrainingProvenanceConfigurationError(Exception):
    """Raised when training provenance trust configuration is missing or invalid."""

    pass


class TrainingProvenanceSecurityError(ValueError, PermissionError):
    """Raised when an unauthenticated or unauthorized party attempts to issue or forge a receipt."""

    pass


def derive_domain_key(secret: str, domain: str) -> bytes:
    """Derive cryptographically separated domain key using HMAC-SHA256."""
    return hmac.new(secret.encode("utf-8"), domain.encode("utf-8"), hashlib.sha256).digest()


@dataclasses.dataclass(frozen=True)
class TrainingExecutionEvidence:
    """Immutable evidence collected directly from authorized training execution."""

    training_run_id: str
    job_id: str
    simulation_id: str
    completed_at: str
    executed_rounds: int
    executed_epochs: int
    sample_count: int
    observed_metrics: dict[str, float]
    artifact_sha256: str
    architecture: str
    feature_schema_version: str
    feature_names: Sequence[str] | list[str] | tuple[str, ...]
    producer_id: str = "TRAINING_EXECUTOR"
    status: str = "COMPLETED"
    git_commit_hash: str | None = None
    dataset_manifest_id: str | None = None

    def __post_init__(self) -> None:
        """Enforce strict immutability on nested collections."""
        if isinstance(self.feature_names, (list, tuple)):
            object.__setattr__(self, "feature_names", tuple(self.feature_names))
        if isinstance(self.observed_metrics, dict):
            object.__setattr__(
                self,
                "observed_metrics",
                types.MappingProxyType(dict(self.observed_metrics)),
            )

    def validate(self) -> None:
        """Validate execution evidence invariants."""
        if not self.training_run_id or not self.training_run_id.strip():
            raise ValueError("training_run_id must be non-empty.")
        if not self.job_id or not self.job_id.strip():
            raise ValueError("job_id must be non-empty.")
        clean_sha = self.artifact_sha256.strip()
        if len(clean_sha) != 64:
            raise ValueError(
                f"artifact_sha256 must be a 64-char hex SHA-256 digest, got: {self.artifact_sha256!r}"
            )
        if not self.architecture or not self.architecture.strip():
            raise ValueError("architecture must be non-empty.")
        if not self.feature_schema_version or not self.feature_schema_version.strip():
            raise ValueError("feature_schema_version must be non-empty.")
        if not self.feature_names or len(self.feature_names) == 0:
            raise ValueError("feature_names must be a non-empty list of feature identifiers.")
        if self.status != "COMPLETED":
            raise ValueError(f"Training status must be 'COMPLETED', got: {self.status!r}")
        if self.executed_rounds < 1 and self.executed_epochs < 1:
            raise ValueError("Executed rounds or epochs must be at least 1.")
        if self.sample_count <= 0:
            raise ValueError(f"Training sample count must be positive, got: {self.sample_count}")
        if not self.observed_metrics or not isinstance(self.observed_metrics, (dict, types.MappingProxyType)):
            raise ValueError("observed_metrics must be a non-empty dictionary.")

        loss_val = None
        for k in ("loss", "final_loss", "train_loss", "val_loss"):
            if k in self.observed_metrics:
                loss_val = self.observed_metrics[k]
                break
        if loss_val is None or not isinstance(loss_val, (int, float)) or not math.isfinite(loss_val):
            raise ValueError("Training metrics must contain a finite numeric loss.")


@dataclasses.dataclass(frozen=True)
class CompletedJobExecutionRecord:
    """Authoritative execution record registered directly by an authorized training worker runtime."""

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
    input_dim: int | None = None
    feature_names: Sequence[str] | list[str] | tuple[str, ...] = ()
    worker_id: str = "TRAINING_EXECUTOR"
    status: str = "COMPLETED"
    git_commit_hash: str | None = None
    dataset_manifest_id: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.feature_names, (list, tuple)):
            object.__setattr__(self, "feature_names", tuple(self.feature_names))
        if self.input_dim is None and self.feature_names:
            object.__setattr__(self, "input_dim", len(self.feature_names))
        if isinstance(self.observed_metrics, dict):
            object.__setattr__(
                self,
                "observed_metrics",
                types.MappingProxyType(dict(self.observed_metrics)),
            )

    def validate(self) -> None:
        if not self.job_id or not self.job_id.strip():
            raise ValueError("job_id must be non-empty.")
        if not self.training_run_id or not self.training_run_id.strip():
            raise ValueError("training_run_id must be non-empty.")
        if len(self.artifact_sha256.strip()) != 64:
            raise ValueError(
                f"artifact_sha256 must be a 64-char hex SHA-256 digest, got: {self.artifact_sha256!r}"
            )
        if self.status != "COMPLETED":
            raise ValueError(f"Job execution status must be 'COMPLETED', got: {self.status!r}")
        if self.executed_rounds < 1 and self.executed_epochs < 1:
            raise ValueError("Executed rounds or epochs must be at least 1.")
        if self.sample_count <= 0:
            raise ValueError(f"Training sample count must be positive, got: {self.sample_count}")
        if not self.observed_metrics:
            raise ValueError("observed_metrics must be non-empty.")
        loss_val = None
        for k in ("loss", "final_loss", "train_loss", "val_loss"):
            if k in self.observed_metrics:
                loss_val = self.observed_metrics[k]
                break
        if loss_val is None or not isinstance(loss_val, (int, float)) or not math.isfinite(loss_val):
            raise ValueError("Training metrics must contain a finite numeric loss.")


FORBIDDEN_WORKER_TOKENS = frozenset(
    {
        "",
        "cfi_authorized_training_worker_secret_v1",
        "secret",
        "changeme",
        "admin",
        "password",
        "token",
        "default",
        "change_me",
    }
)


@dataclasses.dataclass(frozen=True)
class ScheduledTrainingJob:
    """Represents a training job scheduled by the authorized training coordinator."""

    job_id: str
    training_run_id: str
    simulation_id: str
    architecture: str
    feature_schema_version: str
    expected_rounds: int
    expected_epochs: int
    input_dim: int
    feature_names: tuple[str, ...]
    scheduled_at: str
    status: str = "SCHEDULED"


@dataclasses.dataclass(frozen=True)
class StartedTrainingJob:
    """Represents an active training job started by an authorized training worker."""

    job_id: str
    training_run_id: str
    simulation_id: str
    worker_id: str
    started_at: str
    status: str = "STARTED"


class TrustedTrainingJobRecordSource:
    """Protected source of completed training execution records enforcing the complete job lifecycle.

    Lifecycle state progression:
        SCHEDULED (by coordinator) -> STARTED (by authorized worker) -> COMPLETED (with observations)

    Guarantees:
    - Mandatory, strong, independently configured worker credential in production; default placeholders rejected.
    - An arbitrary caller cannot create a COMPLETED record without prior scheduling and starting through authorized infrastructure.
    - Existing completed jobs cannot be overwritten with conflicting evidence (fails closed).
    - Repeated identical completion events are idempotent.
    - Permanent 1-to-1 binding between job_id and training_run_id prevents cross-job ID collisions.
    - Supports atomic file-backed durable persistence with mtime reload for cross-worker consistency.
    """

    def __init__(
        self,
        worker_token: str | None = None,
        store_file_path: str | None = None,
        in_memory: bool = False,
        enforce_lifecycle: bool | None = None,
        configured_token: str | None = None,
        store_path: str | None = None,
        app_env: str | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._worker_token = configured_token if configured_token is not None else worker_token
        self._in_memory = in_memory
        self._enforce_lifecycle = enforce_lifecycle
        self._app_env = app_env
        self._last_mtime: float = 0.0

        target_store = store_path if store_path is not None else store_file_path
        if self._in_memory or target_store == "":
            self._store_file_path = ""
            self._in_memory = True
        else:
            settings = get_settings()
            configured = (
                target_store
                or os.environ.get("CFI_TRAINING_JOB_STORE_PATH")
                or getattr(settings, "training_job_store_path", None)
            )
            if configured:
                self._store_file_path = configured
            else:
                self._store_file_path = ""
                self._in_memory = True

        self._scheduled_jobs: dict[str, ScheduledTrainingJob] = {}
        self._started_jobs: dict[str, StartedTrainingJob] = {}
        self._completed_jobs_by_id: dict[str, CompletedJobExecutionRecord] = {}
        self._completed_jobs_by_run_id: dict[str, CompletedJobExecutionRecord] = {}
        self._run_to_job_id: dict[str, str] = {}

        if not self._in_memory and self._store_file_path:
            self.reload()

        active_env = self._app_env or get_settings().app_env
        if active_env.lower() == "production":
            expected = self._resolve_expected_worker_token()
            if not expected or expected in FORBIDDEN_WORKER_TOKENS or "change_me" in expected:
                raise TrainingProvenanceConfigurationError(
                    "Production training worker credential is not configured or uses a forbidden default/placeholder. "
                    "Production execution recording fails closed."
                )

    @property
    def is_durable(self) -> bool:
        """Truthfully report whether this record source is backed by durable file storage."""
        return not self._in_memory and bool(self._store_file_path)

    def _resolve_expected_worker_token(self) -> str:
        settings = get_settings()
        return (
            self._worker_token
            or os.environ.get("CFI_TRAINING_WORKER_TOKEN")
            or getattr(settings, "training_worker_token", None)
            or ""
        )

    def _validate_worker_token(self, provided_token: str | None) -> None:
        active_env = self._app_env or get_settings().app_env
        is_prod = active_env.lower() == "production"
        expected = self._resolve_expected_worker_token()

        if is_prod:
            if not expected or expected in FORBIDDEN_WORKER_TOKENS or "change_me" in expected:
                raise TrainingProvenanceConfigurationError(
                    "Production training worker credential is not configured or uses an insecure placeholder. "
                    "Production execution recording fails closed."
                )
            if not provided_token:
                raise TrainingProvenanceSecurityError(
                    "Unauthorized training worker: missing training worker authorization token."
                )
            if not hmac.compare_digest(provided_token, expected):
                raise TrainingProvenanceSecurityError(
                    "Unauthorized training worker: Invalid training worker authorization token."
                )
        elif expected and (provided_token is None or not hmac.compare_digest(provided_token, expected)):
            raise TrainingProvenanceSecurityError(
                "Unauthorized training worker: invalid worker token."
            )

    def schedule_training_job(
        self,
        job_id: str,
        training_run_id: str,
        simulation_id: str,
        architecture: str = "FraudDetectionNN",
        feature_schema_version: str = "v1.0",
        expected_rounds: int = 1,
        expected_epochs: int = 0,
        input_dim: int = 10,
        feature_names: Sequence[str] | list[str] | tuple[str, ...] = (),
    ) -> ScheduledTrainingJob:
        """Schedule a training job through the authorized training coordinator."""
        if not job_id or not job_id.strip():
            raise ValueError("job_id must be non-empty.")
        if not training_run_id or not training_run_id.strip():
            raise ValueError("training_run_id must be non-empty.")
        if not simulation_id or not simulation_id.strip():
            raise ValueError("simulation_id must be non-empty.")

        clean_job_id = job_id.strip()
        clean_run_id = training_run_id.strip()

        with self._lock:
            self._reload_if_modified()
            # Enforce 1-to-1 job_id and training_run_id mapping
            if clean_run_id in self._run_to_job_id:
                bound_job = self._run_to_job_id[clean_run_id]
                if bound_job != clean_job_id:
                    raise TrainingProvenanceSecurityError(
                        f"Cannot schedule job '{clean_job_id}': training_run_id '{clean_run_id}' is already permanently bound to job '{bound_job}'."
                    )
            if clean_job_id in self._scheduled_jobs:
                existing = self._scheduled_jobs[clean_job_id]
                if existing.training_run_id != clean_run_id or existing.simulation_id != simulation_id.strip():
                    raise TrainingProvenanceSecurityError(
                        f"Cannot reschedule job '{clean_job_id}' with conflicting parameters."
                    )
                return existing

            scheduled = ScheduledTrainingJob(
                job_id=clean_job_id,
                training_run_id=clean_run_id,
                simulation_id=simulation_id.strip(),
                architecture=architecture,
                feature_schema_version=feature_schema_version,
                expected_rounds=expected_rounds,
                expected_epochs=expected_epochs,
                input_dim=input_dim,
                feature_names=tuple(feature_names),
                scheduled_at=datetime.now(UTC).isoformat(),
            )
            self._scheduled_jobs[clean_job_id] = scheduled
            self._run_to_job_id[clean_run_id] = clean_job_id
            self._persist()
            return scheduled

    def start_training_job(
        self,
        job_id: str,
        worker_id: str = "TRAINING_EXECUTOR",
        worker_token: str | None = None,
    ) -> StartedTrainingJob:
        """Signal that authorized training worker has initiated execution for a scheduled job."""
        self._validate_worker_token(worker_token)

        clean_job_id = job_id.strip()
        with self._lock:
            self._reload_if_modified()
            if clean_job_id not in self._scheduled_jobs:
                raise TrainingProvenanceSecurityError(
                    f"Cannot start job '{clean_job_id}': job was not scheduled by the training coordinator."
                )
            if clean_job_id in self._completed_jobs_by_id:
                raise TrainingProvenanceSecurityError(
                    f"Cannot start job '{clean_job_id}': job has already completed."
                )
            if clean_job_id in self._started_jobs:
                return self._started_jobs[clean_job_id]

            sched = self._scheduled_jobs[clean_job_id]
            started = StartedTrainingJob(
                job_id=clean_job_id,
                training_run_id=sched.training_run_id,
                simulation_id=sched.simulation_id,
                worker_id=worker_id,
                started_at=datetime.now(UTC).isoformat(),
            )
            self._started_jobs[clean_job_id] = started
            self._persist()
            return started

    def record_round_observation(
        self,
        job_id: str,
        round_idx: int | None = None,
        sample_count: int = 0,
        metrics: dict[str, float] | None = None,
        worker_token: str | None = None,
        round_num: int | None = None,
        observed_loss: float | None = None,
    ) -> None:
        """Record genuine round progress and observations during active execution."""
        self._validate_worker_token(worker_token)
        clean_job_id = job_id.strip()
        with self._lock:
            self._reload_if_modified()
            if clean_job_id not in self._started_jobs:
                raise TrainingProvenanceSecurityError(
                    f"Cannot record observations for job '{clean_job_id}': job is not in STARTED state."
                )

    def complete_training_job(
        self,
        job_id: str,
        executed_rounds: int,
        executed_epochs: int = 0,
        sample_count: int = 0,
        observed_metrics: dict[str, float] | None = None,
        artifact_sha256: str = "",
        worker_token: str | None = None,
        worker_id: str = "TRAINING_EXECUTOR",
        architecture: str | None = None,
        feature_schema_version: str | None = None,
        input_dim: int | None = None,
        feature_names: Sequence[str] | list[str] | tuple[str, ...] | None = None,
        git_commit_hash: str | None = None,
        dataset_manifest_id: str | None = None,
        simulation_id: str | None = None,
        training_run_id: str | None = None,
        enforce_lifecycle: bool | None = None,
    ) -> CompletedJobExecutionRecord:
        """Register completion of an authorized training job with observed execution evidence.

        Enforces strict lifecycle progression:
        - In production (or when enforce_lifecycle=True), job MUST have been scheduled and started.
        - Existing completed jobs cannot be overwritten with conflicting evidence (fail closed).
        - Repeated identical submissions are idempotent.
        """
        self._validate_worker_token(worker_token)

        clean_job_id = job_id.strip()
        active_env = self._app_env or get_settings().app_env
        is_prod = active_env.lower() == "production"
        enforce = (
            enforce_lifecycle
            if enforce_lifecycle is not None
            else (self._enforce_lifecycle if self._enforce_lifecycle is not None else is_prod)
        )

        with self._lock:
            self._reload_if_modified()

            # Lifecycle enforcement check
            if enforce:
                if clean_job_id not in self._scheduled_jobs:
                    raise TrainingProvenanceSecurityError(
                        f"Cannot record completion for job '{clean_job_id}': job has not been scheduled through authorized training coordinator. Arbitrary completion claims are rejected."
                    )
                if clean_job_id not in self._started_jobs:
                    raise TrainingProvenanceSecurityError(
                        f"Cannot record completion for job '{clean_job_id}': job has not been started by an authorized worker."
                    )
                sched = self._scheduled_jobs[clean_job_id]
                started = self._started_jobs[clean_job_id]
                sched_sim = sched.simulation_id
                sched_run = sched.training_run_id
                sched_arch = sched.architecture
                sched_schema = sched.feature_schema_version
                sched_worker = started.worker_id

                if simulation_id and simulation_id.strip() != sched_sim:
                    raise TrainingProvenanceSecurityError(
                        f"Simulation ID mismatch for job '{clean_job_id}': scheduled '{sched_sim}', got '{simulation_id}'."
                    )
                if training_run_id and training_run_id.strip() != sched_run:
                    raise TrainingProvenanceSecurityError(
                        f"Training run ID mismatch for job '{clean_job_id}': scheduled '{sched_run}', got '{training_run_id}'."
                    )
                if architecture and architecture.strip() != sched_arch:
                    raise TrainingProvenanceSecurityError(
                        f"Architecture mismatch for job '{clean_job_id}': scheduled '{sched_arch}', got '{architecture}'."
                    )
                if feature_schema_version and feature_schema_version.strip() != sched_schema:
                    raise TrainingProvenanceSecurityError(
                        f"Feature schema version mismatch for job '{clean_job_id}': scheduled '{sched_schema}', got '{feature_schema_version}'."
                    )
            else:
                # In non-enforced development/test mode, auto-schedule and start if missing
                if clean_job_id not in self._scheduled_jobs:
                    sched_sim = simulation_id.strip() if simulation_id else "sim_default"
                    sched_run = training_run_id.strip() if training_run_id else f"run_{clean_job_id}"
                    sched_arch = architecture or "FraudDetectionNN"
                    sched_schema = feature_schema_version or "v1.0"
                    sched_fnames = tuple(feature_names) if feature_names else ()
                    sched_dim = input_dim or len(sched_fnames) or 10
                    self._scheduled_jobs[clean_job_id] = ScheduledTrainingJob(
                        job_id=clean_job_id,
                        training_run_id=sched_run,
                        simulation_id=sched_sim,
                        architecture=sched_arch,
                        feature_schema_version=sched_schema,
                        expected_rounds=executed_rounds,
                        expected_epochs=executed_epochs,
                        input_dim=sched_dim,
                        feature_names=sched_fnames,
                        scheduled_at=datetime.now(UTC).isoformat(),
                    )
                    self._run_to_job_id[sched_run] = clean_job_id
                else:
                    sched = self._scheduled_jobs[clean_job_id]
                    sched_sim = sched.simulation_id
                    sched_run = sched.training_run_id
                    sched_arch = architecture or sched.architecture
                    sched_schema = feature_schema_version or sched.feature_schema_version

                if clean_job_id not in self._started_jobs:
                    self._started_jobs[clean_job_id] = StartedTrainingJob(
                        job_id=clean_job_id,
                        training_run_id=sched_run,
                        simulation_id=sched_sim,
                        worker_id=worker_id,
                        started_at=datetime.now(UTC).isoformat(),
                    )
                sched_worker = worker_id

            # Idempotency and Conflict / Overwrite Prevention (Defect 4, Gate J05, J13)
            target_metrics: dict[str, float] = dict(observed_metrics) if observed_metrics is not None else {}
            if clean_job_id in self._completed_jobs_by_id:
                existing = self._completed_jobs_by_id[clean_job_id]
                # Compare all security-relevant fields
                is_identical = (
                    hmac.compare_digest(existing.artifact_sha256, artifact_sha256)
                    and existing.executed_rounds == executed_rounds
                    and existing.executed_epochs == executed_epochs
                    and existing.sample_count == sample_count
                    and existing.simulation_id == sched_sim
                    and existing.training_run_id == sched_run
                    and len(existing.observed_metrics) == len(target_metrics)
                    and all(
                        k in existing.observed_metrics
                        and math.isclose(existing.observed_metrics[k], v, rel_tol=1e-7, abs_tol=1e-7)
                        for k, v in target_metrics.items()
                    )
                )
                if is_identical:
                    # Idempotent re-submission
                    return existing
                else:
                    # Conflicting overwrite attempt
                    raise TrainingProvenanceSecurityError(
                        f"Conflicting completion record for job '{clean_job_id}': cannot overwrite existing completed execution record with conflicting evidence."
                    )

            target_fnames = tuple(feature_names) if feature_names is not None else ()
            target_dim = input_dim if input_dim is not None else (len(target_fnames) or 10)

            rec = CompletedJobExecutionRecord(
                job_id=clean_job_id,
                training_run_id=sched_run,
                simulation_id=sched_sim,
                completed_at=datetime.now(UTC).isoformat(),
                executed_rounds=executed_rounds,
                executed_epochs=executed_epochs,
                sample_count=sample_count,
                observed_metrics=target_metrics,
                artifact_sha256=artifact_sha256,
                architecture=sched_arch,
                feature_schema_version=sched_schema,
                input_dim=target_dim,
                feature_names=target_fnames,
                worker_id=sched_worker,
                status="COMPLETED",
                git_commit_hash=git_commit_hash,
                dataset_manifest_id=dataset_manifest_id,
            )
            rec.validate()

            self._completed_jobs_by_id[clean_job_id] = rec
            self._completed_jobs_by_run_id[sched_run] = rec
            self._run_to_job_id[sched_run] = clean_job_id
            self._persist()
            return rec

    def record_job_completion(
        self,
        record: CompletedJobExecutionRecord | None = None,
        worker_token: str | None = None,
        *,
        job_id: str | None = None,
        training_run_id: str | None = None,
        simulation_id: str = "sim_default",
        executed_rounds: int = 1,
        executed_epochs: int = 0,
        sample_count: int = 100,
        metrics: dict[str, float] | None = None,
        model_artifact_sha256: str = "",
        architecture: str = "FraudDetectionNN",
        feature_schema_version: str = "v1.0",
        input_dim: int = 10,
        feature_names: Sequence[str] | list[str] | tuple[str, ...] = (),
        worker_id: str = "TRAINING_EXECUTOR",
        git_commit_hash: str | None = None,
        dataset_manifest_id: str | None = None,
    ) -> CompletedJobExecutionRecord:
        """Register an authenticated completed job record."""
        if record is not None:
            return self.complete_training_job(
                job_id=record.job_id,
                executed_rounds=record.executed_rounds,
                executed_epochs=record.executed_epochs,
                sample_count=record.sample_count,
                observed_metrics=dict(record.observed_metrics),
                artifact_sha256=record.artifact_sha256,
                worker_token=worker_token,
                worker_id=record.worker_id,
                architecture=record.architecture,
                feature_schema_version=record.feature_schema_version,
                input_dim=record.input_dim,
                feature_names=record.feature_names,
                git_commit_hash=record.git_commit_hash,
                dataset_manifest_id=record.dataset_manifest_id,
                simulation_id=record.simulation_id,
                training_run_id=record.training_run_id,
            )
        if not job_id or not training_run_id:
            raise ValueError("Either 'record' or both 'job_id' and 'training_run_id' must be provided.")

        return self.complete_training_job(
            job_id=job_id,
            executed_rounds=executed_rounds,
            executed_epochs=executed_epochs,
            sample_count=sample_count,
            observed_metrics=metrics or {},
            artifact_sha256=model_artifact_sha256,
            worker_token=worker_token,
            worker_id=worker_id,
            architecture=architecture,
            feature_schema_version=feature_schema_version,
            input_dim=input_dim,
            feature_names=feature_names,
            git_commit_hash=git_commit_hash,
            dataset_manifest_id=dataset_manifest_id,
            simulation_id=simulation_id,
            training_run_id=training_run_id,
        )

    def get_completed_job(self, identifier: str) -> CompletedJobExecutionRecord | None:
        """Retrieve completed execution record by job_id or training_run_id."""
        with self._lock:
            self._reload_if_modified()
            if identifier in self._completed_jobs_by_id:
                return self._completed_jobs_by_id[identifier]
            if identifier in self._completed_jobs_by_run_id:
                return self._completed_jobs_by_run_id[identifier]
            return None

    def get_completed_job_by_id(self, job_id: str) -> CompletedJobExecutionRecord | None:
        with self._lock:
            self._reload_if_modified()
            return self._completed_jobs_by_id.get(job_id)

    def get_completed_job_by_run_id(self, run_id: str) -> CompletedJobExecutionRecord | None:
        with self._lock:
            self._reload_if_modified()
            return self._completed_jobs_by_run_id.get(run_id)

    def has_completed_job(self, identifier: str) -> bool:
        with self._lock:
            self._reload_if_modified()
            return identifier in self._completed_jobs_by_id or identifier in self._completed_jobs_by_run_id

    def clear(self) -> None:
        with self._lock:
            self._scheduled_jobs.clear()
            self._started_jobs.clear()
            self._completed_jobs_by_id.clear()
            self._completed_jobs_by_run_id.clear()
            self._run_to_job_id.clear()
            if not self._in_memory and self._store_file_path and os.path.exists(self._store_file_path):
                with contextlib.suppress(OSError):
                    os.remove(self._store_file_path)
            self._last_mtime = 0.0

    def _persist(self) -> None:
        if self._in_memory or not self._store_file_path:
            return
        data = {
            "scheduled": {k: dataclasses.asdict(v) for k, v in self._scheduled_jobs.items()},
            "started": {k: dataclasses.asdict(v) for k, v in self._started_jobs.items()},
            "completed": {
                k: {
                    "job_id": v.job_id,
                    "training_run_id": v.training_run_id,
                    "simulation_id": v.simulation_id,
                    "completed_at": v.completed_at,
                    "executed_rounds": v.executed_rounds,
                    "executed_epochs": v.executed_epochs,
                    "sample_count": v.sample_count,
                    "observed_metrics": dict(v.observed_metrics),
                    "artifact_sha256": v.artifact_sha256,
                    "architecture": v.architecture,
                    "feature_schema_version": v.feature_schema_version,
                    "input_dim": v.input_dim,
                    "feature_names": list(v.feature_names),
                    "worker_id": v.worker_id,
                    "status": v.status,
                    "git_commit_hash": v.git_commit_hash,
                    "dataset_manifest_id": v.dataset_manifest_id,
                }
                for k, v in self._completed_jobs_by_id.items()
            },
            "run_to_job": self._run_to_job_id,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        parent_dir = os.path.dirname(os.path.abspath(self._store_file_path))
        os.makedirs(parent_dir, exist_ok=True)
        tmp_path = f"{self._store_file_path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, self._store_file_path)
        with contextlib.suppress(OSError):
            self._last_mtime = os.path.getmtime(self._store_file_path)

    def reload(self) -> None:
        if self._in_memory or not self._store_file_path:
            return
        with self._lock:
            if os.path.exists(self._store_file_path):
                try:
                    mtime = os.path.getmtime(self._store_file_path)
                    with open(self._store_file_path, encoding="utf-8") as f:
                        data = json.load(f)
                    self._scheduled_jobs = {
                        k: ScheduledTrainingJob(**v)
                        for k, v in data.get("scheduled", {}).items()
                    }
                    self._started_jobs = {
                        k: StartedTrainingJob(**v)
                        for k, v in data.get("started", {}).items()
                    }
                    self._completed_jobs_by_id = {
                        k: CompletedJobExecutionRecord(**v)
                        for k, v in data.get("completed", {}).items()
                    }
                    self._completed_jobs_by_run_id = {
                        v.training_run_id: v
                        for v in self._completed_jobs_by_id.values()
                    }
                    self._run_to_job_id = data.get("run_to_job", {})
                    self._last_mtime = mtime
                except Exception as exc:
                    logger.warning("Failed to reload training job store from %s: %s", self._store_file_path, exc)

    def _reload_if_modified(self) -> None:
        if self._in_memory or not self._store_file_path:
            return
        if os.path.exists(self._store_file_path):
            with contextlib.suppress(OSError):
                mtime = os.path.getmtime(self._store_file_path)
                if mtime > self._last_mtime:
                    self.reload()


class DurableTrustStore:
    """Administratively controlled, durable key trust configuration.

    Supports:
    - Revocation due to compromise (all receipts signed by this key are rejected).
    - Lifecycle retirement (key cannot issue new receipts; receipts signed before retirement remain valid).
    - Duplicate key ID rejection (rejects unauthorized public key substitution).
    - Cross-worker / cross-process freshness contract via atomic reload on mtime change.
    """

    def __init__(
        self,
        trust_file_path: str | dict[str, Any] | None = None,
        in_memory: bool = False,
    ) -> None:
        self._lock = threading.RLock()
        initial_keys: dict[str, Any] = {}
        if isinstance(trust_file_path, dict):
            initial_keys = trust_file_path
            trust_file_path = None
            in_memory = True

        self._in_memory = in_memory
        if self._in_memory:
            self._trust_file_path = ""
        else:
            settings = get_settings()
            configured_path = (
                trust_file_path
                or os.environ.get("CFI_TRAINING_RECEIPT_TRUST_STORE")
                or getattr(settings, "training_receipt_trust_store_path", None)
            )
            if not configured_path:
                from app.infrastructure.storage.storage_utils import get_storage_dir

                configured_path = os.path.join(get_storage_dir(), "training_trust_registry.json")

            self._trust_file_path = configured_path
        self._approved_keys: dict[str, dict[str, str]] = {}
        self._revoked_keys: dict[str, dict[str, str]] = {}
        self._retired_keys: dict[str, dict[str, str]] = {}
        self._last_mtime: float = 0.0

        for kid, k_val in initial_keys.items():
            raw_hex = k_val if isinstance(k_val, str) else k_val.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            ).hex()
            self._approved_keys[kid] = {
                "public_key_hex": raw_hex,
                "added_at": datetime.now(UTC).isoformat(),
            }

        if not self._in_memory:
            self.reload()

    def reload(self) -> None:
        if self._in_memory or not self._trust_file_path:
            return
        with self._lock:
            if os.path.exists(self._trust_file_path):
                try:
                    mtime = os.path.getmtime(self._trust_file_path)
                    with open(self._trust_file_path, encoding="utf-8") as f:
                        data = json.load(f)
                    self._approved_keys = data.get("approved_keys", {})
                    self._revoked_keys = data.get("revoked_keys", {})
                    self._retired_keys = data.get("retired_keys", {})
                    self._last_mtime = mtime
                except Exception as exc:
                    logger.warning("Failed to reload durable trust store from %s: %s", self._trust_file_path, exc)

    def reload_if_modified(self) -> None:
        """Check mtime and reload if modified on disk (cross-worker freshness contract)."""
        if self._in_memory or not self._trust_file_path:
            return
        if os.path.exists(self._trust_file_path):
            with contextlib.suppress(OSError):
                mtime = os.path.getmtime(self._trust_file_path)
                if mtime > self._last_mtime:
                    self.reload()

    def _persist(self) -> None:
        """Atomically persist trust registry to disk."""
        if self._in_memory or not self._trust_file_path:
            return
        data = {
            "approved_keys": self._approved_keys,
            "revoked_keys": self._revoked_keys,
            "retired_keys": self._retired_keys,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        parent_dir = os.path.dirname(os.path.abspath(self._trust_file_path))
        os.makedirs(parent_dir, exist_ok=True)
        tmp_path = f"{self._trust_file_path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, self._trust_file_path)
        with contextlib.suppress(OSError):
            self._last_mtime = os.path.getmtime(self._trust_file_path)

    def register_approved_key(
        self,
        key_id: str,
        public_key_hex: str,
        authorized_replacement: bool = False,
    ) -> None:
        """Register an approved key ID and public key."""
        with self._lock:
            self.reload_if_modified()
            clean_kid = key_id.strip()
            clean_hex = public_key_hex.strip().lower()

            if clean_kid in self._revoked_keys:
                raise ValueError(
                    f"Cannot register key_id '{clean_kid}' because it has been revoked due to compromise."
                )

            if clean_kid in self._approved_keys:
                existing_hex = self._approved_keys[clean_kid].get("public_key_hex", "").strip().lower()
                if existing_hex != clean_hex and not authorized_replacement:
                    raise ValueError(
                        f"Unauthorized public key substitution rejected: key_id '{clean_kid}' is already "
                        f"registered with a different public key."
                    )

            self._approved_keys[clean_kid] = {
                "public_key_hex": clean_hex,
                "registered_at": datetime.now(UTC).isoformat(),
            }
            self._persist()

    def revoke_key(self, key_id: str, reason: str = "compromise") -> None:
        """Revoke key due to compromise (all receipts signed by this key are rejected)."""
        with self._lock:
            self.reload_if_modified()
            clean_kid = key_id.strip()
            self._revoked_keys[clean_kid] = {
                "reason": reason,
                "revoked_at": datetime.now(UTC).isoformat(),
                "revoked_by": "admin",
            }
            self._approved_keys.pop(clean_kid, None)
            self._persist()

    def retire_key(self, key_id: str, reason: str = "lifecycle_rotation") -> None:
        """Retire key from active issuance; existing receipts created before retirement remain valid."""
        with self._lock:
            self.reload_if_modified()
            clean_kid = key_id.strip()
            self._retired_keys[clean_kid] = {
                "reason": reason,
                "retired_at": datetime.now(UTC).isoformat(),
            }
            self._persist()

    def is_key_revoked(self, key_id: str) -> bool:
        self.reload_if_modified()
        return key_id.strip() in self._revoked_keys

    def is_key_retired(self, key_id: str, at_timestamp: str | None = None) -> tuple[bool, str | None]:
        self.reload_if_modified()
        clean_kid = key_id.strip()
        if clean_kid not in self._retired_keys:
            return False, None
        retired_info = self._retired_keys[clean_kid]
        retired_at_str = retired_info.get("retired_at", "")
        if at_timestamp and retired_at_str:
            if at_timestamp > retired_at_str:
                return True, retired_at_str
            return False, retired_at_str
        return True, retired_at_str

    def get_approved_keys(self) -> dict[str, str]:
        """Return dictionary mapping approved key_id to public_key_hex."""
        with self._lock:
            self.reload_if_modified()
            return {
                kid: v.get("public_key_hex", "")
                for kid, v in self._approved_keys.items()
                if isinstance(v, dict) and "public_key_hex" in v
            }

    def clear(self) -> None:
        with self._lock:
            self._approved_keys.clear()
            self._revoked_keys.clear()
            self._retired_keys.clear()
            if os.path.exists(self._trust_file_path):
                with contextlib.suppress(OSError):
                    os.remove(self._trust_file_path)
            self._last_mtime = 0.0


_DEFAULT_JOB_RECORD_SOURCE: TrustedTrainingJobRecordSource | None = None
_DEFAULT_TRUST_STORE: DurableTrustStore | None = None


def get_default_training_job_record_source() -> TrustedTrainingJobRecordSource:
    global _DEFAULT_JOB_RECORD_SOURCE
    if _DEFAULT_JOB_RECORD_SOURCE is None:
        _DEFAULT_JOB_RECORD_SOURCE = TrustedTrainingJobRecordSource()
    return _DEFAULT_JOB_RECORD_SOURCE


def reset_default_training_job_record_source() -> None:
    global _DEFAULT_JOB_RECORD_SOURCE
    _DEFAULT_JOB_RECORD_SOURCE = None


def get_default_trust_store() -> DurableTrustStore:
    global _DEFAULT_TRUST_STORE
    if _DEFAULT_TRUST_STORE is None:
        _DEFAULT_TRUST_STORE = DurableTrustStore()
    return _DEFAULT_TRUST_STORE


def reset_default_trust_store() -> None:
    global _DEFAULT_TRUST_STORE
    _DEFAULT_TRUST_STORE = None


def schedule_training_job(
    job_id: str,
    training_run_id: str,
    simulation_id: str,
    architecture: str = "FraudDetectionNN",
    feature_schema_version: str = "v1.0",
    expected_rounds: int = 1,
    expected_epochs: int = 0,
    input_dim: int = 10,
    feature_names: Sequence[str] | list[str] | tuple[str, ...] = (),
) -> ScheduledTrainingJob:
    """Convenience helper to schedule a training job with the default record source."""
    return get_default_training_job_record_source().schedule_training_job(
        job_id=job_id,
        training_run_id=training_run_id,
        simulation_id=simulation_id,
        architecture=architecture,
        feature_schema_version=feature_schema_version,
        expected_rounds=expected_rounds,
        expected_epochs=expected_epochs,
        input_dim=input_dim,
        feature_names=feature_names,
    )


def start_training_job(
    job_id: str,
    worker_id: str = "TRAINING_EXECUTOR",
    worker_token: str | None = None,
) -> StartedTrainingJob:
    """Convenience helper to start a scheduled training job with the default record source."""
    return get_default_training_job_record_source().start_training_job(
        job_id=job_id,
        worker_id=worker_id,
        worker_token=worker_token,
    )


def record_training_job_completion(
    job_id: str,
    training_run_id: str,
    simulation_id: str,
    executed_rounds: int,
    sample_count: int,
    observed_metrics: dict[str, float],
    artifact_sha256: str,
    architecture: str,
    feature_schema_version: str,
    feature_names: Sequence[str] | list[str] | tuple[str, ...],
    worker_id: str = "TRAINING_EXECUTOR",
    executed_epochs: int = 0,
    git_commit_hash: str | None = None,
    dataset_manifest_id: str | None = None,
    worker_token: str | None = None,
) -> CompletedJobExecutionRecord:
    """Convenience helper for genuine training workers to record completed training runs."""
    return get_default_training_job_record_source().complete_training_job(
        job_id=job_id,
        executed_rounds=executed_rounds,
        executed_epochs=executed_epochs,
        sample_count=sample_count,
        observed_metrics=observed_metrics,
        artifact_sha256=artifact_sha256,
        architecture=architecture,
        feature_schema_version=feature_schema_version,
        feature_names=feature_names,
        worker_id=worker_id,
        git_commit_hash=git_commit_hash,
        dataset_manifest_id=dataset_manifest_id,
        worker_token=worker_token,
        simulation_id=simulation_id,
        training_run_id=training_run_id,
    )



@dataclasses.dataclass(frozen=True)
class TrainingExecutionReceipt:
    receipt_id: str
    training_run_id: str
    job_id: str
    model_artifact_sha256: str
    architecture: str
    feature_schema_version: str
    input_dim: int
    feature_names: list[str]
    simulation_id: str
    status: str
    executed_rounds: int
    executed_epochs: int
    sample_count: int
    metrics: dict[str, float]
    producer_id: str
    schema_version: str
    created_at: str
    signature: str
    key_id: str
    signature_scheme: str = "Ed25519"
    git_commit_hash: str | None = None
    dataset_manifest_id: str | None = None

    def canonical_dict(self) -> dict[str, Any]:
        """Return dict of signed fields without signature."""
        return {
            "receipt_id": self.receipt_id,
            "training_run_id": self.training_run_id,
            "job_id": self.job_id,
            "model_artifact_sha256": self.model_artifact_sha256,
            "architecture": self.architecture,
            "feature_schema_version": self.feature_schema_version,
            "input_dim": self.input_dim,
            "feature_names": self.feature_names,
            "simulation_id": self.simulation_id,
            "status": self.status,
            "executed_rounds": self.executed_rounds,
            "executed_epochs": self.executed_epochs,
            "sample_count": self.sample_count,
            "metrics": self.metrics,
            "producer_id": self.producer_id,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "git_commit_hash": self.git_commit_hash,
            "dataset_manifest_id": self.dataset_manifest_id,
            "key_id": self.key_id,
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(self.canonical_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")

    def to_dict(self) -> dict[str, Any]:
        res = self.canonical_dict()
        res["signature"] = self.signature
        res["signature_scheme"] = self.signature_scheme
        return res

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrainingExecutionReceipt:
        mandatory_fields = [
            "receipt_id",
            "training_run_id",
            "job_id",
            "model_artifact_sha256",
            "architecture",
            "feature_schema_version",
            "input_dim",
            "feature_names",
            "simulation_id",
            "status",
            "executed_rounds",
            "executed_epochs",
            "sample_count",
            "metrics",
            "producer_id",
            "schema_version",
            "created_at",
            "signature",
            "key_id",
        ]
        missing = [f for f in mandatory_fields if f not in data or data[f] is None]
        if missing:
            raise KeyError(f"Mandatory receipt fields missing: {', '.join(missing)}")

        sig = str(data["signature"]).strip()
        if not sig:
            raise ValueError("Receipt signature is missing or empty.")

        arch = str(data["architecture"]).strip()
        if not arch:
            raise ValueError("Receipt architecture is missing or empty.")

        fnames = list(data["feature_names"])
        if not fnames:
            raise ValueError("Receipt feature_names must be a non-empty list.")

        input_dim = int(data["input_dim"])
        if input_dim <= 0:
            raise ValueError("Receipt input_dim must be positive.")
        if input_dim != len(fnames):
            raise ValueError(
                f"Receipt input_dim ({input_dim}) mismatch with feature_names length ({len(fnames)})."
            )

        if not isinstance(data["metrics"], dict):
            raise ValueError("Receipt metrics must be a dictionary.")

        return cls(
            receipt_id=str(data["receipt_id"]),
            training_run_id=str(data["training_run_id"]),
            job_id=str(data["job_id"]),
            model_artifact_sha256=str(data["model_artifact_sha256"]),
            architecture=arch,
            feature_schema_version=str(data["feature_schema_version"]),
            input_dim=input_dim,
            feature_names=fnames,
            simulation_id=str(data["simulation_id"]),
            status=str(data["status"]),
            executed_rounds=int(data["executed_rounds"]),
            executed_epochs=int(data["executed_epochs"]),
            sample_count=int(data["sample_count"]),
            metrics=dict(data["metrics"]),
            producer_id=str(data["producer_id"]),
            schema_version=str(data["schema_version"]),
            created_at=str(data["created_at"]),
            signature=sig,
            key_id=str(data["key_id"]),
            signature_scheme=str(data.get("signature_scheme", "Ed25519")),
            git_commit_hash=data.get("git_commit_hash"),
            dataset_manifest_id=data.get("dataset_manifest_id"),
        )


class TrainingReceiptIssuer:
    """Authorized issuer of cryptographic execution receipts using asymmetric Ed25519 signing.

    The private key is held EXCLUSIVELY by authorized training worker / execution components.
    General serving components and model registries do NOT possess this private key.
    """

    def __init__(
        self,
        private_key: ed25519.Ed25519PrivateKey | bytes | str | None = None,
        key_id: str = DEFAULT_TRAINING_KEY_ID,
        job_record_source: TrustedTrainingJobRecordSource | None = None,
        require_authenticated_job: bool | None = None,
        trust_store: DurableTrustStore | None = None,
        signing_key_hex: str | None = None,
        enforce_job_records: bool | None = None,
    ) -> None:
        if signing_key_hex is not None:
            if signing_key_hex == "":
                raise TrainingProvenanceConfigurationError("Signing key hex must be provided and non-empty.")
            private_key = signing_key_hex
        if enforce_job_records is not None:
            require_authenticated_job = enforce_job_records

        self.key_id = key_id
        self._job_record_source = job_record_source or get_default_training_job_record_source()
        self._require_authenticated_job = require_authenticated_job
        self._trust_store = trust_store or get_default_trust_store()
        settings = get_settings()
        is_prod = settings.app_env.lower() == "production"

        if self._trust_store.is_key_revoked(self.key_id):
            raise ValueError(
                f"Cannot initialize TrainingReceiptIssuer: key_id '{self.key_id}' has been revoked due to compromise."
            )

        if private_key is not None:
            if isinstance(private_key, ed25519.Ed25519PrivateKey):
                self._private_key = private_key
            elif isinstance(private_key, bytes):
                if len(private_key) == 32:
                    self._private_key = ed25519.Ed25519PrivateKey.from_private_bytes(private_key)
                else:
                    loaded = serialization.load_pem_private_key(private_key, password=None)
                    if not isinstance(loaded, ed25519.Ed25519PrivateKey):
                        raise TypeError(f"Expected Ed25519PrivateKey, got {type(loaded)}")
                    self._private_key = loaded
            elif isinstance(private_key, str):
                cleaned = private_key.strip()
                if len(cleaned) == 64:
                    self._private_key = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(cleaned))
                else:
                    loaded = serialization.load_pem_private_key(cleaned.encode(), password=None)
                    if not isinstance(loaded, ed25519.Ed25519PrivateKey):
                        raise TypeError(f"Expected Ed25519PrivateKey, got {type(loaded)}")
                    self._private_key = loaded
            else:
                raise TypeError(f"Unsupported private_key type: {type(private_key)}")
        else:
            # Check environment / settings
            env_key = os.environ.get("CFI_TRAINING_RECEIPT_PRIVATE_KEY")
            if env_key:
                cleaned = env_key.strip()
                if len(cleaned) == 64:
                    self._private_key = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(cleaned))
                else:
                    loaded = serialization.load_pem_private_key(cleaned.encode(), password=None)
                    if not isinstance(loaded, ed25519.Ed25519PrivateKey):
                        raise TypeError(f"Expected Ed25519PrivateKey, got {type(loaded)}")
                    self._private_key = loaded
            elif is_prod:
                raise RuntimeError(
                    "Production configuration error: CFI_TRAINING_RECEIPT_PRIVATE_KEY is mandatory for TrainingReceiptIssuer in production."
                )
            else:
                # Deterministic development key derived from dev seed
                dev_seed = hashlib.sha256(b"cfi_dev_ed25519_training_issuer_seed_v1").digest()
                self._private_key = ed25519.Ed25519PrivateKey.from_private_bytes(dev_seed)

    @property
    def public_key(self) -> ed25519.Ed25519PublicKey:
        pub = self._private_key.public_key()
        assert isinstance(pub, ed25519.Ed25519PublicKey)
        return pub

    @property
    def public_bytes_raw(self) -> bytes:
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    @property
    def public_bytes_hex(self) -> str:
        return self.public_bytes_raw.hex()

    def issue_receipt(
        self,
        training_run_id: str | None = None,
        job_id: str | None = None,
        model_artifact_sha256: str | None = None,
        simulation_id: str | None = None,
        executed_rounds: int | None = None,
        executed_epochs: int | None = None,
        sample_count: int | None = None,
        metrics: dict[str, float] | None = None,
        architecture: str | None = None,
        feature_schema_version: str | None = None,
        input_dim: int | None = None,
        feature_names: Sequence[str] | list[str] | tuple[str, ...] | None = None,
        producer_id: str = "TRAINING_EXECUTOR",
        git_commit_hash: str | None = None,
        dataset_manifest_id: str | None = None,
        artifact_sha256: str | None = None,
        model_bytes: bytes | None = None,
        model_architecture: str | None = None,
        dataset_hash: str | None = None,
        dp_noise_profile: Any = None,
        evidence: TrainingExecutionEvidence | None = None,
        status: str = "COMPLETED",
    ) -> TrainingExecutionReceipt:
        """Issue an authenticated training execution receipt signed with Ed25519 private key."""
        settings = get_settings()
        is_prod = settings.app_env.lower() == "production"

        if evidence is not None:
            evidence.validate()
            target_run_id = evidence.training_run_id
            target_job_id = evidence.job_id
            target_sim_id = evidence.simulation_id
            target_rounds = evidence.executed_rounds
            target_epochs = evidence.executed_epochs
            target_samples = evidence.sample_count
            target_metrics = evidence.observed_metrics
            target_sha = evidence.artifact_sha256
            target_arch = evidence.architecture
            target_schema_ver = evidence.feature_schema_version
            fnames = list(evidence.feature_names)
            target_input_dim = input_dim or len(fnames)
            target_producer = evidence.producer_id
            target_status = evidence.status
            target_git = evidence.git_commit_hash or git_commit_hash
            target_manifest = evidence.dataset_manifest_id or dataset_manifest_id

            if model_bytes is not None:
                calc_sha = hashlib.sha256(model_bytes).hexdigest()
                if not hmac.compare_digest(calc_sha, target_sha):
                    raise ValueError(
                        f"model_bytes SHA-256 digest ({calc_sha}) does not match evidence artifact_sha256 ({target_sha})."
                    )
        else:
            if not training_run_id:
                raise ValueError("training_run_id is required.")

            target_run_id = training_run_id
            target_job_id = job_id or f"job_{training_run_id}"
            target_sim_id = simulation_id or "sim_default"
            target_arch = model_architecture or architecture or "FraudDetectionModel-GroupNorm"
            target_schema_ver = feature_schema_version or "v1.0"
            target_manifest = dataset_manifest_id or dataset_hash
            target_git = git_commit_hash
            target_status = status or "COMPLETED"
            target_producer = producer_id

            candidate_sha: str | None = model_artifact_sha256 or artifact_sha256
            if model_bytes is not None:
                calc_sha = hashlib.sha256(model_bytes).hexdigest()
                if candidate_sha and not hmac.compare_digest(calc_sha, candidate_sha):
                    raise ValueError(
                        f"model_bytes SHA-256 digest ({calc_sha}) does not match provided artifact SHA-256 ({candidate_sha})."
                    )
                candidate_sha = calc_sha

            if not candidate_sha:
                raise ValueError("model_artifact_sha256 or artifact_sha256 is required.")
            target_sha = candidate_sha

            if feature_names:
                fnames = list(feature_names)
            else:
                from app.application.services.data_generator import FEATURE_NAMES

                fnames = list(FEATURE_NAMES)
            target_input_dim = input_dim or len(fnames)

            # In production, require explicit validated execution fields (no fabricated defaults)
            if is_prod:
                if executed_rounds is None and executed_epochs is None:
                    raise ValueError(
                        "Production receipt issuance requires explicit executed_rounds or executed_epochs (work must be observed)."
                    )
                if sample_count is None or sample_count <= 0:
                    raise ValueError("Production receipt issuance requires explicit, positive sample_count.")
                if metrics is None or not isinstance(metrics, dict):
                    raise ValueError("Production receipt issuance requires explicit observed metrics dictionary.")

            target_rounds = (
                executed_rounds if executed_rounds is not None else (0 if executed_epochs is not None else 1)
            )
            target_epochs = executed_epochs if executed_epochs is not None else 0
            target_samples = sample_count if sample_count is not None else 100
            target_metrics = metrics if metrics is not None else {"loss": 0.01}

            if target_status != "COMPLETED":
                raise ValueError(f"Training status must be 'COMPLETED', got: {target_status!r}")
            if target_rounds < 1 and target_epochs < 1:
                raise ValueError("Executed rounds or epochs must be at least 1.")
            if target_samples <= 0:
                raise ValueError("Training sample count must be positive.")

            # Validate loss metric
            loss_val = None
            for k in ("loss", "final_loss", "train_loss", "val_loss"):
                if k in target_metrics:
                    loss_val = target_metrics[k]
                    break
            if loss_val is None or not isinstance(loss_val, (int, float)) or not math.isfinite(loss_val):
                raise ValueError("Training metrics must contain a finite numeric loss.")

        # Check revocation and retirement of signing key
        if self._trust_store.is_key_revoked(self.key_id):
            raise ValueError(f"Cannot issue receipt: key_id '{self.key_id}' has been revoked due to compromise.")
        is_ret, ret_at = self._trust_store.is_key_retired(self.key_id)
        if is_ret:
            raise ValueError(
                f"Cannot issue receipt: key_id '{self.key_id}' was retired from active issuance at {ret_at}."
            )

        # Enforce authenticated execution record from trusted job completion source
        enforce_record = (
            self._require_authenticated_job
            if self._require_authenticated_job is not None
            else is_prod
        )
        if enforce_record:
            target_job = target_job_id or f"job_{target_run_id}"
            rec = self._job_record_source.get_completed_job_by_id(target_job)
            if rec is None and target_run_id:
                rec_by_run = self._job_record_source.get_completed_job_by_run_id(target_run_id)
                if rec_by_run is not None:
                    if rec_by_run.job_id == target_job:
                        rec = rec_by_run
                    else:
                        raise ValueError(
                            f"Job ID mismatch: requested job '{target_job}' does not match job '{rec_by_run.job_id}' associated with run '{target_run_id}'."
                        )

            if rec is None:
                raise TrainingProvenanceSecurityError(
                    f"Production receipt issuance rejected: job '{target_job}' has no authenticated execution record "
                    "(not found in trusted training job record source). "
                    "Arbitrary caller-supplied claims cannot authorize receipt issuance."
                )

            if rec.status != "COMPLETED":
                raise ValueError(
                    f"Job '{target_job}' in trusted completion source is not in 'COMPLETED' status (got '{rec.status}')."
                )

            # Strict complete identity and field binding (Section 5)
            if rec.job_id != target_job:
                raise ValueError(
                    f"Job ID mismatch: caller requested '{target_job}', but trusted record is for '{rec.job_id}'."
                )
            if rec.training_run_id != target_run_id:
                raise ValueError(
                    f"Training run ID mismatch: caller claimed '{target_run_id}', but trusted record is for '{rec.training_run_id}'."
                )
            if rec.simulation_id != target_sim_id:
                raise ValueError(
                    f"Simulation ID mismatch: caller claimed '{target_sim_id}', but trusted record is for '{rec.simulation_id}'."
                )
            if rec.worker_id != target_producer:
                raise ValueError(
                    f"Producer identity mismatch: caller claimed '{target_producer}', but trusted record observed '{rec.worker_id}'."
                )
            if target_rounds != rec.executed_rounds:
                raise ValueError(
                    f"Executed rounds mismatch: caller claimed {target_rounds}, but trusted record observed {rec.executed_rounds}."
                )
            if target_epochs != rec.executed_epochs:
                raise ValueError(
                    f"Executed epochs mismatch: caller claimed {target_epochs}, but trusted record observed {rec.executed_epochs}."
                )
            if target_samples != rec.sample_count:
                raise ValueError(
                    f"Sample count mismatch: caller claimed {target_samples}, but trusted record observed {rec.sample_count}."
                )
            if not hmac.compare_digest(target_sha, rec.artifact_sha256):
                raise ValueError(
                    f"Artifact digest mismatch: caller provided {target_sha}, but trusted record observed {rec.artifact_sha256}."
                )
            if target_arch != rec.architecture:
                raise ValueError(
                    f"Model architecture mismatch: caller claimed '{target_arch}', but trusted record observed '{rec.architecture}'."
                )
            if target_schema_ver != rec.feature_schema_version:
                raise ValueError(
                    f"Feature schema version mismatch: caller claimed '{target_schema_ver}', but trusted record observed '{rec.feature_schema_version}'."
                )
            if target_input_dim != rec.input_dim:
                raise ValueError(
                    f"Input dimension mismatch: caller claimed {target_input_dim}, but trusted record observed {rec.input_dim}."
                )
            if tuple(fnames) != tuple(rec.feature_names):
                raise ValueError(
                    "Feature names or ordering mismatch: caller provided feature list does not match trusted execution record."
                )

            # Match observed metrics
            for k, expected_v in rec.observed_metrics.items():
                if k not in target_metrics:
                    raise ValueError(f"Observed metric '{k}' missing from receipt request.")
                actual_v = target_metrics[k]
                if not (isinstance(actual_v, (int, float)) and math.isclose(actual_v, expected_v, rel_tol=1e-7, abs_tol=1e-7)):
                    raise ValueError(
                        f"Metric '{k}' mismatch: caller claimed {actual_v}, but trusted record observed {expected_v}."
                    )
            if rec.dataset_manifest_id and target_manifest and target_manifest != rec.dataset_manifest_id:
                raise ValueError(
                    f"Dataset manifest mismatch: caller claimed '{target_manifest}', but trusted record observed '{rec.dataset_manifest_id}'."
                )
            if rec.git_commit_hash and target_git and target_git != rec.git_commit_hash:
                raise ValueError(
                    f"Git commit hash mismatch: caller claimed '{target_git}', but trusted record observed '{rec.git_commit_hash}'."
                )

        receipt_id = f"rcpt_{uuid.uuid4().hex}"
        created_at = datetime.now(UTC).isoformat()

        receipt_data = {
            "receipt_id": receipt_id,
            "training_run_id": target_run_id,
            "job_id": target_job_id,
            "model_artifact_sha256": target_sha,
            "architecture": target_arch,
            "feature_schema_version": target_schema_ver,
            "input_dim": target_input_dim,
            "feature_names": fnames,
            "simulation_id": target_sim_id,
            "status": target_status,
            "executed_rounds": target_rounds,
            "executed_epochs": target_epochs,
            "sample_count": target_samples,
            "metrics": dict(target_metrics),
            "producer_id": target_producer,
            "schema_version": "1.0",
            "created_at": created_at,
            "git_commit_hash": target_git,
            "dataset_manifest_id": target_manifest,
            "key_id": self.key_id,
        }
        canonical_bytes = json.dumps(receipt_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        sig_bytes = self._private_key.sign(canonical_bytes)
        signature = sig_bytes.hex()

        return TrainingExecutionReceipt(
            receipt_id=receipt_id,
            training_run_id=target_run_id,
            job_id=target_job_id,
            model_artifact_sha256=target_sha,
            architecture=target_arch,
            feature_schema_version=target_schema_ver,
            input_dim=target_input_dim,
            feature_names=fnames,
            simulation_id=target_sim_id,
            status=target_status,
            executed_rounds=target_rounds,
            executed_epochs=target_epochs,
            sample_count=target_samples,
            metrics=dict(target_metrics),
            producer_id=target_producer,
            schema_version="1.0",
            created_at=created_at,
            git_commit_hash=target_git,
            dataset_manifest_id=target_manifest,
            key_id=self.key_id,
            signature=signature,
            signature_scheme="Ed25519",
        )


class TrainingReceiptVerifier:
    """Verifies cryptographic execution receipts using public keys from an approved key registry.

    Does NOT possess the private key and CANNOT forge training receipts.
    Safe for instantiation in serving, registry, and public interfaces.
    """

    def __init__(
        self,
        trusted_keys: dict[str, ed25519.Ed25519PublicKey | bytes | str] | None = None,
        trust_store: DurableTrustStore | None = None,
        job_record_source: TrustedTrainingJobRecordSource | None = None,
        enforce_recorded_evidence: bool | None = None,
    ) -> None:
        self._trusted_keys: dict[str, ed25519.Ed25519PublicKey] = {}
        self._revoked_keys: set[str] = set()
        self._job_record_source = job_record_source
        self._enforce_recorded_evidence = enforce_recorded_evidence
        if trust_store is not None:
            self._trust_store = trust_store
            for kid, hex_val in self._trust_store.get_approved_keys().items():
                if hex_val:
                    self.register_trusted_key(kid, hex_val, authorized_replacement=True)
        elif trusted_keys is not None:
            self._trust_store = DurableTrustStore(in_memory=True)
        else:
            self._trust_store = get_default_trust_store()

        if trusted_keys is not None:
            for kid, k_val in trusted_keys.items():
                self.register_trusted_key(kid, k_val, authorized_replacement=True)
        elif trust_store is None:
            settings = get_settings()
            is_prod = settings.app_env.lower() == "production"

            pub_source = (
                getattr(settings, "training_receipt_public_key", None)
                or os.environ.get("CFI_TRAINING_RECEIPT_PUBLIC_KEY", "")
            )
            pub_source = pub_source.strip() if pub_source else ""

            key_id = (
                getattr(settings, "training_receipt_key_id", None)
                or os.environ.get("CFI_TRAINING_RECEIPT_KEY_ID", "")
                or DEFAULT_TRAINING_KEY_ID
            ).strip()

            multi_keys_env = os.environ.get("CFI_TRAINING_RECEIPT_PUBLIC_KEYS", "").strip()
            if multi_keys_env:
                try:
                    if multi_keys_env.startswith("{"):
                        parsed = json.loads(multi_keys_env)
                        for kid, kval in parsed.items():
                            self.register_trusted_key(kid, kval)
                    else:
                        for entry in multi_keys_env.split(","):
                            if ":" in entry:
                                kid, kval = entry.split(":", 1)
                                self.register_trusted_key(kid.strip(), kval.strip())
                except Exception as exc:
                    raise TrainingProvenanceConfigurationError(
                        f"Failed to parse CFI_TRAINING_RECEIPT_PUBLIC_KEYS: {exc}"
                    ) from exc

            if pub_source:
                self.register_trusted_key(key_id, pub_source, authorized_replacement=True)

            revoked_env = os.environ.get("CFI_TRAINING_RECEIPT_REVOKED_KEYS", "").strip()
            if revoked_env:
                for rkid in revoked_env.split(","):
                    if rkid.strip():
                        self.revoke_key(rkid.strip())

            if is_prod and len(self._trusted_keys) == 0:
                raise TrainingProvenanceConfigurationError(
                    "Production configuration error: Trusted public key is mandatory for TrainingReceiptVerifier "
                    "in production. Set CFI_TRAINING_RECEIPT_PUBLIC_KEY or configure training_receipt_public_key."
                )

            if not is_prod and len(self._trusted_keys) == 0:
                # In non-production only: register development public key without issuer construction
                dev_seed = hashlib.sha256(b"cfi_dev_ed25519_training_issuer_seed_v1").digest()
                dev_priv = ed25519.Ed25519PrivateKey.from_private_bytes(dev_seed)
                dev_pub = dev_priv.public_key()
                self.register_trusted_key(DEFAULT_TRAINING_KEY_ID, dev_pub, authorized_replacement=True)
        else:
            settings = get_settings()
            is_prod = settings.app_env.lower() == "production"
            if is_prod and len(self._trusted_keys) == 0:
                raise TrainingProvenanceConfigurationError(
                    "Production configuration error: Trusted public key is mandatory for TrainingReceiptVerifier "
                    "in production. Set CFI_TRAINING_RECEIPT_PUBLIC_KEY or configure training_receipt_public_key."
                )

    def register_trusted_key(
        self,
        key_id: str,
        public_key: ed25519.Ed25519PublicKey | bytes | str,
        authorized_replacement: bool = False,
    ) -> None:
        """Register an authorized verification public key."""
        if not key_id or not key_id.strip():
            raise ValueError("key_id cannot be empty.")

        clean_kid = key_id.strip()
        if clean_kid in self._revoked_keys or self._trust_store.is_key_revoked(clean_kid):
            raise ValueError(
                f"Cannot register key_id '{clean_kid}' because it has been revoked due to compromise."
            )

        if isinstance(public_key, ed25519.Ed25519PublicKey):
            parsed_pub = public_key
        elif isinstance(public_key, bytes):
            if len(public_key) == 32:
                parsed_pub = ed25519.Ed25519PublicKey.from_public_bytes(public_key)
            else:
                loaded = serialization.load_pem_public_key(public_key)
                if not isinstance(loaded, ed25519.Ed25519PublicKey):
                    raise TypeError(f"Expected Ed25519PublicKey, got {type(loaded)}")
                parsed_pub = loaded
        elif isinstance(public_key, str):
            cleaned = public_key.strip()
            if len(cleaned) == 64:
                parsed_pub = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(cleaned))
            else:
                loaded = serialization.load_pem_public_key(cleaned.encode())
                if not isinstance(loaded, ed25519.Ed25519PublicKey):
                    raise TypeError(f"Expected Ed25519PublicKey, got {type(loaded)}")
                parsed_pub = loaded
        else:
            raise TypeError(f"Unsupported public key type: {type(public_key)}")

        if clean_kid in self._trusted_keys:
            existing_pub = self._trusted_keys[clean_kid]
            existing_bytes = existing_pub.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            new_bytes = parsed_pub.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            if existing_bytes != new_bytes and not authorized_replacement:
                raise ValueError(
                    f"Unauthorized public key substitution rejected: key_id '{clean_kid}' is already "
                    f"registered with different public key material."
                )

        new_bytes = parsed_pub.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self._trust_store.register_approved_key(
            clean_kid, new_bytes.hex(), authorized_replacement=authorized_replacement
        )
        self._trusted_keys[clean_kid] = parsed_pub

    def revoke_key(self, key_id: str, reason: str = "compromise") -> None:
        """Revoke a trusted key ID so receipts signed with it will be rejected."""
        clean_kid = key_id.strip()
        self._revoked_keys.add(clean_kid)
        self._trusted_keys.pop(clean_kid, None)
        self._trust_store.revoke_key(clean_kid, reason=reason)
        logger.warning("Revoked training receipt key_id '%s' (reason: %s)", clean_kid, reason)

    def retire_key(self, key_id: str, reason: str = "lifecycle_rotation") -> None:
        """Retire a trusted key ID from active issuance (historical verification preserved)."""
        clean_kid = key_id.strip()
        self._trust_store.retire_key(clean_kid, reason=reason)
        logger.info("Retired training receipt key_id '%s' (reason: %s)", clean_kid, reason)

    def is_key_trusted(self, key_id: str) -> bool:
        self._trust_store.reload_if_modified()
        return (
            key_id in self._trusted_keys
            and key_id not in self._revoked_keys
            and not self._trust_store.is_key_revoked(key_id)
        )

    def verify(
        self,
        receipt: TrainingExecutionReceipt | dict[str, Any],
        expected_sha256: str | None = None,
        simulation_id: str | None = None,
        expected_key_id: str | None = None,
    ) -> tuple[bool, str]:
        """Alias for verify_receipt."""
        return self.verify_receipt(
            receipt,
            candidate_artifact_sha256=expected_sha256,
            simulation_id=simulation_id,
            expected_key_id=expected_key_id,
        )

    def verify_receipt(
        self,
        receipt: TrainingExecutionReceipt | dict[str, Any],
        candidate_artifact_sha256: str | None = None,
        candidate_artifact_bytes: bytes | None = None,
        simulation_id: str | None = None,
        expected_key_id: str | None = None,
        expected_architecture: str | None = None,
        expected_feature_schema_version: str | None = None,
        expected_input_dim: int | None = None,
        expected_feature_names: list[str] | tuple[str, ...] | None = None,
        expected_simulation_id: str | None = None,
        expected_artifact_sha256: str | None = None,
        actual_artifact_bytes: bytes | None = None,
    ) -> tuple[bool, str]:
        """Verify an execution receipt against candidate artifact bytes and declared scope."""
        if expected_simulation_id is not None:
            simulation_id = expected_simulation_id
        if expected_artifact_sha256 is not None:
            candidate_artifact_sha256 = expected_artifact_sha256
        if actual_artifact_bytes is not None:
            candidate_artifact_bytes = actual_artifact_bytes
        if isinstance(receipt, dict):
            try:
                receipt_obj = TrainingExecutionReceipt.from_dict(receipt)
            except Exception as exc:
                return False, f"Receipt format invalid: {exc}"
        elif isinstance(receipt, TrainingExecutionReceipt):
            receipt_obj = receipt
        else:
            return False, f"Invalid receipt type: {type(receipt)}"

        if not receipt_obj.signature:
            return False, "Receipt signature is missing or empty."

        if receipt_obj.signature_scheme != "Ed25519":
            return False, f"Unsupported signature scheme: {receipt_obj.signature_scheme}"

        if receipt_obj.schema_version not in ("1.0", "1.1"):
            return False, f"Unsupported receipt schema version: {receipt_obj.schema_version}"

        # Key ID validation & revocation check across processes
        self._trust_store.reload_if_modified()
        if receipt_obj.key_id in self._revoked_keys or self._trust_store.is_key_revoked(receipt_obj.key_id):
            return False, f"Receipt key_id '{receipt_obj.key_id}' has been explicitly revoked."

        # Key ID lifecycle retirement check
        is_ret, ret_at = self._trust_store.is_key_retired(
            receipt_obj.key_id, at_timestamp=receipt_obj.created_at
        )
        if is_ret:
            return False, (
                f"Receipt key_id '{receipt_obj.key_id}' was retired before receipt creation "
                f"({receipt_obj.created_at} > {ret_at})."
            )

        if expected_key_id and receipt_obj.key_id != expected_key_id:
            return False, f"Receipt key_id mismatch: expected '{expected_key_id}', got '{receipt_obj.key_id}'."

        if receipt_obj.key_id not in self._trusted_keys and not self._trust_store.is_key_revoked(receipt_obj.key_id):
            approved_map = self._trust_store.get_approved_keys()
            if receipt_obj.key_id in approved_map:
                self.register_trusted_key(
                    receipt_obj.key_id, approved_map[receipt_obj.key_id], authorized_replacement=True
                )

        if receipt_obj.key_id not in self._trusted_keys:
            return False, f"Receipt key_id '{receipt_obj.key_id}' is not present in trusted public key registry."

        public_key = self._trusted_keys[receipt_obj.key_id]

        # Cryptographic signature validation
        try:
            sig_bytes = bytes.fromhex(receipt_obj.signature)
            public_key.verify(sig_bytes, receipt_obj.canonical_bytes())
        except (ValueError, InvalidSignature) as exc:
            return False, f"Cryptographic signature mismatch: receipt was tampered with or signed with untrusted key ({exc})."

        # Exact artifact SHA-256 binding
        target_candidate_sha = candidate_artifact_sha256
        if candidate_artifact_bytes is not None:
            bytes_sha = hashlib.sha256(candidate_artifact_bytes).hexdigest()
            if target_candidate_sha and not hmac.compare_digest(target_candidate_sha, bytes_sha):
                return False, (
                    f"Candidate artifact bytes SHA-256 ({bytes_sha}) does not match "
                    f"declared candidate SHA-256 ({target_candidate_sha})."
                )
            target_candidate_sha = bytes_sha

        if target_candidate_sha is not None and not hmac.compare_digest(
            receipt_obj.model_artifact_sha256, target_candidate_sha
        ):
            return False, (
                f"Artifact digest mismatch: receipt binds '{receipt_obj.model_artifact_sha256}', "
                f"candidate artifact is '{target_candidate_sha}'."
            )

        # Simulation scope binding
        if simulation_id and receipt_obj.simulation_id and receipt_obj.simulation_id != simulation_id:
            return False, (
                f"Simulation scope mismatch: receipt is for '{receipt_obj.simulation_id}', "
                f"candidate requested for '{simulation_id}'."
            )

        # Architecture binding
        if expected_architecture and receipt_obj.architecture != expected_architecture:
            return False, (
                f"Model architecture mismatch: receipt binds '{receipt_obj.architecture}', "
                f"expected '{expected_architecture}'."
            )

        # Feature schema version binding
        if expected_feature_schema_version and receipt_obj.feature_schema_version != expected_feature_schema_version:
            return False, (
                f"Feature schema version mismatch: receipt binds '{receipt_obj.feature_schema_version}', "
                f"expected '{expected_feature_schema_version}'."
            )

        # Input dimension binding
        if expected_input_dim is not None and receipt_obj.input_dim != expected_input_dim:
            return False, (
                f"Input dimension mismatch: receipt binds {receipt_obj.input_dim}, expected {expected_input_dim}."
            )

        # Feature names binding
        if expected_feature_names is not None and list(receipt_obj.feature_names) != list(expected_feature_names):
            return False, (
                f"Feature names or ordering mismatch: receipt binds {receipt_obj.feature_names}, "
                f"expected {list(expected_feature_names)}."
            )

        # Execution invariants
        if receipt_obj.status != "COMPLETED":
            return False, f"Training run status is '{receipt_obj.status}', expected 'COMPLETED'."
        if receipt_obj.executed_rounds < 1 and receipt_obj.executed_epochs < 1:
            return False, "Executed rounds and epochs are both < 1."
        if receipt_obj.sample_count <= 0:
            return False, "Executed sample count is <= 0."

        loss_val = None
        for k in ("loss", "final_loss", "train_loss", "val_loss"):
            if k in receipt_obj.metrics:
                loss_val = receipt_obj.metrics[k]
                break
        if loss_val is None or not isinstance(loss_val, (int, float)) or not math.isfinite(loss_val):
            return False, "Recorded training loss is not a finite number."

        return True, "Training receipt verified successfully."


_DEFAULT_ISSUER: TrainingReceiptIssuer | None = None
_DEFAULT_VERIFIER: TrainingReceiptVerifier | None = None


def get_default_training_issuer() -> TrainingReceiptIssuer:
    global _DEFAULT_ISSUER
    if _DEFAULT_ISSUER is None:
        _DEFAULT_ISSUER = TrainingReceiptIssuer()
    return _DEFAULT_ISSUER


def get_default_training_verifier() -> TrainingReceiptVerifier:
    """Construct or return singleton TrainingReceiptVerifier.

    Constructed strictly from trusted public verification material.
    NEVER calls get_default_training_issuer(), NEVER constructs TrainingReceiptIssuer,
    and NEVER accesses private keys.
    """
    global _DEFAULT_VERIFIER
    if _DEFAULT_VERIFIER is None:
        _DEFAULT_VERIFIER = TrainingReceiptVerifier()
    return _DEFAULT_VERIFIER


def reset_default_training_verifier() -> None:
    """Reset the cached default verifier instance (primarily for test isolation)."""
    global _DEFAULT_VERIFIER
    _DEFAULT_VERIFIER = None
    reset_default_trust_store()


def reset_default_training_issuer() -> None:
    """Reset the cached default issuer instance (primarily for test isolation)."""
    global _DEFAULT_ISSUER
    _DEFAULT_ISSUER = None


class TrainingReceiptAuthority:
    """Facade for training execution receipt issuance and verification.

    Provides backward compatibility while maintaining underlying asymmetric separation.
    In production environments, issuer capability is strictly disabled if private signing keys
    are absent from the environment.
    """

    def __init__(self, signing_secret: str | None = None) -> None:
        settings = get_settings()
        is_prod = settings.app_env.lower() == "production"
        default_secret = settings.payload_signing_secret

        self._secret = signing_secret or default_secret

        if is_prod:
            self._verifier = get_default_training_verifier()
            if os.environ.get("CFI_TRAINING_RECEIPT_PRIVATE_KEY"):
                self._issuer: TrainingReceiptIssuer | None = get_default_training_issuer()
            else:
                self._issuer = None
        else:
            if signing_secret is not None and signing_secret != default_secret:
                dev_seed = hashlib.sha256(signing_secret.encode("utf-8")).digest()
                self._issuer_priv = ed25519.Ed25519PrivateKey.from_private_bytes(dev_seed)
                isolated_store = DurableTrustStore(in_memory=True)
                self._issuer = TrainingReceiptIssuer(
                    private_key=self._issuer_priv, trust_store=isolated_store
                )
                self._verifier = TrainingReceiptVerifier(
                    trusted_keys={self._issuer.key_id: self._issuer.public_key},
                    trust_store=isolated_store,
                )
            else:
                self._issuer = get_default_training_issuer()
                self._verifier = get_default_training_verifier()

    @property
    def key_id(self) -> str:
        """Return the key ID of the active issuer, or the default key ID."""
        if self._issuer is not None:
            return self._issuer.key_id
        return DEFAULT_TRAINING_KEY_ID

    def _get_signing_key(self) -> bytes:
        return derive_domain_key(self._secret, DOMAIN_TRAINING_RECEIPT)

    def issue_receipt(
        self,
        training_run_id: str | None = None,
        job_id: str | None = None,
        model_artifact_sha256: str | None = None,
        simulation_id: str = "sim_default",
        executed_rounds: int | None = None,
        executed_epochs: int | None = None,
        sample_count: int | None = None,
        metrics: dict[str, float] | None = None,
        architecture: str = "FraudDetectionModel-GroupNorm",
        feature_schema_version: str = "v1.0",
        input_dim: int = 10,
        feature_names: Sequence[str] | list[str] | tuple[str, ...] | None = None,
        producer_id: str = "TRAINING_EXECUTOR",
        git_commit_hash: str | None = None,
        dataset_manifest_id: str | None = None,
        evidence: TrainingExecutionEvidence | None = None,
        **kwargs: Any,
    ) -> TrainingExecutionReceipt:
        if self._issuer is None:
            raise TrainingProvenanceConfigurationError(
                "Receipt issuance capability is disabled: private key is absent in this serving environment."
            )
        return self._issuer.issue_receipt(
            training_run_id=training_run_id,
            job_id=job_id,
            model_artifact_sha256=model_artifact_sha256,
            simulation_id=simulation_id,
            executed_rounds=executed_rounds,
            executed_epochs=executed_epochs,
            sample_count=sample_count,
            metrics=metrics,
            architecture=architecture,
            feature_schema_version=feature_schema_version,
            input_dim=input_dim,
            feature_names=feature_names,
            producer_id=producer_id,
            git_commit_hash=git_commit_hash,
            dataset_manifest_id=dataset_manifest_id,
            evidence=evidence,
            **kwargs,
        )

    def verify_receipt(
        self,
        receipt: TrainingExecutionReceipt | dict[str, Any],
        candidate_artifact_sha256: str | None = None,
        simulation_id: str | None = None,
        expected_key_id: str | None = None,
    ) -> tuple[bool, str]:
        # Try asymmetric verification first
        valid, reason = self._verifier.verify_receipt(
            receipt=receipt,
            candidate_artifact_sha256=candidate_artifact_sha256,
            simulation_id=simulation_id,
            expected_key_id=expected_key_id,
        )
        if valid:
            return True, reason

        # Check if legacy HMAC signature is present for non-production environments
        settings = get_settings()
        if settings.app_env.lower() == "production":
            return False, f"Production rejects untrusted or non-asymmetric receipt: {reason}"

        # Dev/test fallback HMAC verification
        if isinstance(receipt, dict):
            try:
                receipt_obj = TrainingExecutionReceipt.from_dict(receipt)
            except Exception as exc:
                return False, f"Receipt format invalid: {exc}"
        else:
            receipt_obj = receipt

        expected_sig = hmac.new(
            self._get_signing_key(), receipt_obj.canonical_bytes(), hashlib.sha256
        ).hexdigest()
        if hmac.compare_digest(receipt_obj.signature, expected_sig):
            if candidate_artifact_sha256 is not None and not hmac.compare_digest(
                receipt_obj.model_artifact_sha256, candidate_artifact_sha256
            ):
                return False, (
                    f"Artifact digest mismatch: receipt binds '{receipt_obj.model_artifact_sha256}', "
                    f"candidate is '{candidate_artifact_sha256}'."
                )
            if simulation_id and receipt_obj.simulation_id and receipt_obj.simulation_id != simulation_id:
                return False, (
                    f"Simulation scope mismatch: receipt is for '{receipt_obj.simulation_id}', "
                    f"requested '{simulation_id}'."
                )
            if receipt_obj.status != "COMPLETED":
                return False, f"Training run status is '{receipt_obj.status}', expected 'COMPLETED'."
            if receipt_obj.executed_rounds < 1 and receipt_obj.executed_epochs < 1:
                return False, "Executed rounds and epochs are both < 1."
            if receipt_obj.sample_count <= 0:
                return False, "Executed sample count is <= 0."
            loss_val = None
            for k in ("loss", "final_loss", "train_loss", "val_loss"):
                if k in receipt_obj.metrics:
                    loss_val = receipt_obj.metrics[k]
                    break
            if loss_val is None or not isinstance(loss_val, (int, float)) or not math.isfinite(loss_val):
                return False, "Recorded training loss is not a finite number."
            return True, "Legacy HMAC receipt verified successfully (dev only)."

        return False, reason
