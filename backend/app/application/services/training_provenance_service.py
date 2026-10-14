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
from collections.abc import Generator, Sequence
from datetime import UTC, datetime
from typing import Any

from filelock import FileLock

try:
    import psycopg
    from psycopg.rows import dict_row as pg_dict_row
except ImportError:
    psycopg = None  # type: ignore[assignment]
    pg_dict_row = None  # type: ignore[assignment]

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.data_generator import FEATURE_NAMES as CANONICAL_FEATURE_NAMES
from app.config import get_settings
from app.infrastructure.database.training_authority_backends import (
    CompletedJobExecutionRecord,
    GlobalAggregationEvent,
    ModelEvaluationEvent,
    PostgresTrainingJobAuthority,
    RoundObservation,
    ScheduledTrainingJob,
    SQLiteTrainingJobAuthority,
    StartedTrainingJob,
    TrainingJobAuthorityBackend,
    TrainingProvenanceConfigurationError,
    TrainingProvenanceSecurityError,
)

logger = logging.getLogger(__name__)

DOMAIN_TRAINING_RECEIPT = "cfi:training_receipt_signing:v1"
DOMAIN_MODEL_CACHE = "cfi:model_cache_auth:v1"
DOMAIN_APPROVAL_SIGNOFF = "cfi:model_approval_signoff:v1"

DEFAULT_TRAINING_KEY_ID = "cfi_training_ed25519_v1"


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
    owning_bank_id: str | None = None
    consortium_id: str | None = None

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

FORBIDDEN_COORDINATOR_TOKENS = frozenset(
    {
        "",
        "cfi_authorized_training_coordinator_secret_v1",
        "secret",
        "changeme",
        "admin",
        "password",
        "token",
        "default",
        "change_me",
    }
)



class TrainingJobAuthorityDatabase:
    """ACID transactional database authority dispatching to PostgreSQL or SQLite backend."""

    def __init__(
        self,
        db_target: str | None = None,
        in_memory: bool = False,
        app_env: str | None = None,
        db_url: str | None = None,
        is_prod: bool | None = None,
    ) -> None:
        effective_target = db_target if db_target is not None else (db_url or "")
        self._db_target = effective_target
        self._in_memory = in_memory
        clean = (effective_target or "").strip()

        if is_prod is not None:
            is_production = is_prod
        else:
            active_env = (app_env or get_settings().app_env).lower()
            is_production = active_env == "production"

        if is_production and (in_memory or not (clean.startswith("postgresql://") or clean.startswith("postgres://"))):
            raise TrainingProvenanceConfigurationError(
                "Production training job authority database must be a PostgreSQL connection URL (e.g. postgresql://...). "
                "Plain filesystem paths, SQLite URLs, and in-memory storage are strictly forbidden in production."
            )

        if in_memory or clean in (":memory:", ""):
            self._backend: TrainingJobAuthorityBackend = SQLiteTrainingJobAuthority(
                db_target=":memory:", in_memory=True
            )
        elif clean.startswith("postgresql://") or clean.startswith("postgres://"):
            self._backend = PostgresTrainingJobAuthority(db_url=clean)
        elif clean.startswith("sqlite://") or "://" not in clean:
            self._backend = SQLiteTrainingJobAuthority(db_target=clean, in_memory=False)
        else:
            raise TrainingProvenanceConfigurationError(
                f"Unsupported database URL scheme: '{clean}'. Only PostgreSQL ('postgresql://...') and SQLite ('sqlite://...') backends are supported."
            )

    @property
    def backend_type(self) -> str:
        return self._backend.backend_type

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
    ) -> ScheduledTrainingJob:
        return self._backend.schedule_job(
            job_id=job_id,
            training_run_id=training_run_id,
            simulation_id=simulation_id,
            architecture=architecture,
            feature_schema_version=feature_schema_version,
            expected_rounds=expected_rounds,
            expected_epochs=expected_epochs,
            input_dim=input_dim,
            feature_names=feature_names,
            coordinator_id=coordinator_id,
            permitted_workers=permitted_workers,
        )

    def start_job(self, job_id: str, worker_id: str = "TRAINING_EXECUTOR") -> StartedTrainingJob:
        return self._backend.start_job(job_id=job_id, worker_id=worker_id)

    def record_observation(self, obs: RoundObservation) -> RoundObservation:
        return self._backend.record_observation(obs)

    def get_observations(self, job_id: str) -> list[RoundObservation]:
        return self._backend.get_observations(job_id)

    def get_round_observations(self, job_id: str) -> list[RoundObservation]:
        return self._backend.get_observations(job_id)

    def record_global_aggregation(self, event: GlobalAggregationEvent) -> GlobalAggregationEvent:
        return self._backend.record_global_aggregation(event)

    def get_global_aggregations(self, job_id: str) -> list[GlobalAggregationEvent]:
        return self._backend.get_global_aggregations(job_id)

    def record_model_evaluation(self, event: ModelEvaluationEvent) -> ModelEvaluationEvent:
        return self._backend.record_model_evaluation(event)

    def get_model_evaluations(self, job_id: str) -> list[ModelEvaluationEvent]:
        return self._backend.get_model_evaluations(job_id)

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
    ) -> CompletedJobExecutionRecord:
        return self._backend.complete_job(
            job_id=job_id,
            executed_rounds=executed_rounds,
            executed_epochs=executed_epochs,
            sample_count=sample_count,
            observed_metrics=observed_metrics,
            artifact_sha256=artifact_sha256,
            worker_id=worker_id,
            git_commit_hash=git_commit_hash,
            dataset_manifest_id=dataset_manifest_id,
            simulation_id=simulation_id,
            training_run_id=training_run_id,
            architecture=architecture,
            feature_schema_version=feature_schema_version,
            input_dim=input_dim,
            feature_names=feature_names,
            enforce_lifecycle=enforce_lifecycle,
            simulate_failure_before_commit=simulate_failure_before_commit,
        )

    def get_completed_job_by_id(self, job_id: str) -> CompletedJobExecutionRecord | None:
        return self._backend.get_completed_job_by_id(job_id)

    def get_completed_job_by_run_id(self, run_id: str) -> CompletedJobExecutionRecord | None:
        return self._backend.get_completed_job_by_run_id(run_id)

    def get_scheduled_job(self, job_id: str) -> ScheduledTrainingJob | None:
        return self._backend.get_scheduled_job(job_id)

    def get_started_job(self, job_id: str) -> StartedTrainingJob | None:
        return self._backend.get_started_job(job_id)

    def clear(self) -> None:
        self._backend.clear()

    def close(self) -> None:
        self._backend.close()


class TrustedTrainingJobRecordSource:
    """Protected source of completed training execution records enforcing the complete job lifecycle.

    Guarantees:
    - ACID transactional persistence via PostgreSQL (production) or SQLite WAL mode (development).
    - Workload authorization strictly separating coordinator scheduling from authenticated per-bank worker execution.
    - Prevents identity impersonation across banking institutions.
    - Full round observation recording and derivation/reconciliation of completion claims.
    - Feature contract immutability from scheduling through completion.
    - Fail-closed behavior in production: missing database or credentials raises TrainingProvenanceConfigurationError.
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
        coordinator_token: str | None = None,
        db_url: str | None = None,
        bank_tokens: dict[str, str] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._worker_token = configured_token if configured_token is not None else worker_token
        self._coordinator_token = coordinator_token
        self._enforce_lifecycle = enforce_lifecycle
        self._app_env = app_env

        settings = get_settings()
        self._bank_tokens = dict(bank_tokens) if bank_tokens is not None else dict(getattr(settings, "training_bank_tokens", {}))
        env_bank_tokens = os.environ.get("CFI_TRAINING_BANK_TOKENS")
        if env_bank_tokens:
            try:
                parsed = json.loads(env_bank_tokens)
                if isinstance(parsed, dict):
                    self._bank_tokens.update(parsed)
            except Exception:
                pass

        target_store = store_path if store_path is not None else (store_file_path or db_url)
        active_env = self._app_env or settings.app_env
        is_prod = active_env.lower() == "production"

        if is_prod:
            expected_coord = self._resolve_expected_coordinator_token()
            has_worker_creds = bool(self._bank_tokens or self._resolve_expected_worker_token())

            if (expected_coord or not has_worker_creds) and (
                not expected_coord or expected_coord in FORBIDDEN_COORDINATOR_TOKENS or "change_me" in expected_coord
            ):
                raise TrainingProvenanceConfigurationError(
                    "Production training coordinator credential is not configured or uses a forbidden default/placeholder. "
                    "Production execution recording fails closed."
                )

            if self._bank_tokens:
                seen_tokens: set[str] = set()
                for b_id, b_tok in self._bank_tokens.items():
                    if not b_tok or b_tok in FORBIDDEN_WORKER_TOKENS or "change_me" in b_tok:
                        raise TrainingProvenanceConfigurationError(
                            f"Production training bank token for '{b_id}' is empty or uses a forbidden placeholder."
                        )
                    if expected_coord and hmac.compare_digest(b_tok, expected_coord):
                        raise TrainingProvenanceConfigurationError(
                            f"Production training bank token for '{b_id}' and coordinator token cannot be identical (privilege separation required)."
                        )
                    if b_tok in seen_tokens:
                        raise TrainingProvenanceConfigurationError(
                            f"Production training bank token for '{b_id}' reuses another bank's credential."
                        )
                    seen_tokens.add(b_tok)
            elif has_worker_creds or not expected_coord:
                expected_worker = self._resolve_expected_worker_token()
                if not expected_worker or expected_worker in FORBIDDEN_WORKER_TOKENS or "change_me" in expected_worker:
                    raise TrainingProvenanceConfigurationError(
                        "Production training worker credential is not configured or uses a forbidden default/placeholder. "
                        "Production execution recording fails closed."
                    )
                if expected_coord and hmac.compare_digest(expected_worker, expected_coord):
                    raise TrainingProvenanceConfigurationError(
                        "Production training coordinator and worker credentials cannot be identical (privilege separation required)."
                    )

        if in_memory or target_store == "":
            if is_prod:
                raise TrainingProvenanceConfigurationError(
                    "Production training job authority database/store is not configured. "
                    "In-memory fallback is strictly forbidden in production."
                )
            self._store_file_path = ""
            self._in_memory = True
        else:
            configured = (
                target_store
                or os.environ.get("CFI_TRAINING_JOB_DB_URL")
                or os.environ.get("CFI_TRAINING_JOB_STORE_PATH")
                or getattr(settings, "training_job_db_url", None)
                or getattr(settings, "training_job_store_path", None)
            )
            if configured:
                clean_target = configured.strip()
                if is_prod and not (clean_target.startswith("postgresql://") or clean_target.startswith("postgres://")):
                    raise TrainingProvenanceConfigurationError(
                        "Production training job authority database must be a PostgreSQL connection URL (e.g. postgresql://...). "
                        "Plain filesystem paths, SQLite URLs, and in-memory storage are strictly forbidden in production."
                    )
                self._store_file_path = clean_target
                self._in_memory = False
            elif is_prod:
                raise TrainingProvenanceConfigurationError(
                    "Production training job authority database/store is not configured. "
                    "In-memory fallback is strictly forbidden in production."
                )
            else:
                self._store_file_path = ""
                self._in_memory = True

        self._db = TrainingJobAuthorityDatabase(
            db_target=self._store_file_path,
            in_memory=self._in_memory,
            app_env=active_env,
        )

    @property
    def is_durable(self) -> bool:
        """Truthfully report whether this record source is backed by durable database storage."""
        return not self._in_memory and bool(self._store_file_path)

    @property
    def backend_type(self) -> str:
        """Report whether the underlying authority backend is 'postgres' or 'sqlite'."""
        return self._db.backend_type

    @property
    def _is_prod(self) -> bool:
        """Whether this record source is running in production mode."""
        active_env = self._app_env or get_settings().app_env
        return active_env.lower() == "production"

    def _resolve_expected_worker_token(self) -> str:
        settings = get_settings()
        return (
            self._worker_token
            or os.environ.get("CFI_TRAINING_WORKER_TOKEN")
            or getattr(settings, "training_worker_token", None)
            or ""
        )

    def _resolve_expected_coordinator_token(self) -> str:
        settings = get_settings()
        return (
            self._coordinator_token
            or os.environ.get("CFI_TRAINING_COORDINATOR_TOKEN")
            or getattr(settings, "training_coordinator_token", None)
            or ""
        )

    def _validate_coordinator_token(self, provided_token: str | None) -> None:
        active_env = self._app_env or get_settings().app_env
        is_prod = active_env.lower() == "production"
        expected = self._resolve_expected_coordinator_token()
        expected_worker = self._resolve_expected_worker_token()

        if is_prod:
            if not expected or expected in FORBIDDEN_COORDINATOR_TOKENS or "change_me" in expected:
                raise TrainingProvenanceConfigurationError(
                    "Production training coordinator credential is not configured or uses an insecure placeholder. "
                    "Production execution recording fails closed."
                )
            if not provided_token:
                raise TrainingProvenanceSecurityError(
                    "Unauthorized training coordinator: missing training coordinator authorization token."
                )
            if expected_worker and hmac.compare_digest(provided_token, expected_worker):
                raise TrainingProvenanceSecurityError(
                    "Worker credential cannot authorize job scheduling; coordinator role required."
                )
            for b_tok in self._bank_tokens.values():
                if b_tok and hmac.compare_digest(provided_token, b_tok):
                    raise TrainingProvenanceSecurityError(
                        "Bank worker credential cannot authorize coordinator actions; coordinator role required."
                    )
            if not hmac.compare_digest(provided_token, expected):
                raise TrainingProvenanceSecurityError(
                    "Unauthorized training coordinator: Invalid training coordinator authorization token."
                )
        else:
            if expected_worker and provided_token and hmac.compare_digest(provided_token, expected_worker):
                raise TrainingProvenanceSecurityError(
                    "Worker credential cannot authorize job scheduling; coordinator role required."
                )
            for b_tok in self._bank_tokens.values():
                if b_tok and provided_token and hmac.compare_digest(provided_token, b_tok):
                    raise TrainingProvenanceSecurityError(
                        "Bank worker credential cannot authorize coordinator actions; coordinator role required."
                    )
            if expected and (provided_token is None or not hmac.compare_digest(provided_token, expected)):
                raise TrainingProvenanceSecurityError(
                    "Unauthorized training coordinator: invalid coordinator token."
                )

    def _validate_worker_token(self, provided_token: str | None) -> None:
        active_env = self._app_env or get_settings().app_env
        is_prod = active_env.lower() == "production"
        expected = self._resolve_expected_worker_token()
        expected_coord = self._resolve_expected_coordinator_token()

        if is_prod:
            if not provided_token:
                raise TrainingProvenanceSecurityError(
                    "Unauthorized training worker: missing training worker authorization token."
                )
            if expected_coord and hmac.compare_digest(provided_token, expected_coord):
                raise TrainingProvenanceSecurityError(
                    "Coordinator credential cannot authorize worker execution events."
                )
            if self._bank_tokens:
                if not any(hmac.compare_digest(provided_token, bt) for bt in self._bank_tokens.values() if bt):
                    raise TrainingProvenanceSecurityError(
                        "Unauthorized training worker: Invalid training worker authorization token."
                    )
            else:
                if not expected or expected in FORBIDDEN_WORKER_TOKENS or "change_me" in expected:
                    raise TrainingProvenanceConfigurationError(
                        "Production training worker credential is not configured or uses an insecure placeholder. "
                        "Production execution recording fails closed."
                    )
                if not hmac.compare_digest(provided_token, expected):
                    raise TrainingProvenanceSecurityError(
                        "Unauthorized training worker: Invalid training worker authorization token."
                    )
        else:
            if expected_coord and provided_token and hmac.compare_digest(provided_token, expected_coord):
                raise TrainingProvenanceSecurityError(
                    "Coordinator credential cannot authorize worker execution events."
                )
            if (
                self._bank_tokens
                and provided_token
                and any(hmac.compare_digest(provided_token, bt) for bt in self._bank_tokens.values() if bt)
            ):
                return
            if expected and (provided_token is None or not hmac.compare_digest(provided_token, expected)):
                raise TrainingProvenanceSecurityError(
                    "Unauthorized training worker: invalid worker token."
                )

    def authenticate_bank_worker(
        self,
        provided_token: str | None,
        claimed_bank_id: str | None = None,
    ) -> str:
        """Authenticate bank worker credential and return authoritative bank identity.

        Strictly prevents identity spoofing:
        - Rejects missing or empty credentials.
        - Rejects coordinator credentials attempting worker actions.
        - Matches token to configured bank identity (BANK_A, BANK_B, BANK_C).
        - If claimed_bank_id is passed, rejects if it differs from authenticated identity.
        - Rejects global worker token fallback claiming arbitrary bank identities.
        """
        active_env = self._app_env or get_settings().app_env
        is_prod = active_env.lower() == "production"
        expected_coord = self._resolve_expected_coordinator_token()
        expected_worker = self._resolve_expected_worker_token()

        if not provided_token:
            if is_prod or self._bank_tokens or expected_worker:
                raise TrainingProvenanceSecurityError(
                    "Unauthorized training worker: missing training worker authorization token."
                )
            return claimed_bank_id.strip() if claimed_bank_id else "BANK_A"

        if expected_coord and hmac.compare_digest(provided_token, expected_coord):
            raise TrainingProvenanceSecurityError(
                "Coordinator credential cannot authorize worker execution events."
            )

        matched_bank: str | None = None
        for b_id, b_tok in self._bank_tokens.items():
            if b_tok and hmac.compare_digest(provided_token, b_tok):
                matched_bank = b_id
                break

        if matched_bank is not None:
            if claimed_bank_id and claimed_bank_id.strip() != matched_bank:
                raise TrainingProvenanceSecurityError(
                    f"Identity spoofing rejected: credential authenticated as '{matched_bank}', "
                    f"but caller requested bank_id='{claimed_bank_id}'."
                )
            return matched_bank

        if self._bank_tokens:
            raise TrainingProvenanceSecurityError(
                "Unauthorized bank worker: credential does not match any recognized bank workload identity."
            )

        if is_prod:
            raise TrainingProvenanceConfigurationError(
                "Production training requires bank-specific workload credentials (_bank_tokens). "
                "Global worker tokens cannot authenticate bank participants."
            )

        # In development mode only:
        if expected_worker and hmac.compare_digest(provided_token, expected_worker):
            return claimed_bank_id.strip() if claimed_bank_id else "BANK_A"
        if not expected_worker:
            return claimed_bank_id.strip() if claimed_bank_id else "BANK_A"

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
        coordinator_token: str | None = None,
        coordinator_id: str = "COORDINATOR",
        permitted_workers: Sequence[str] = (),
    ) -> ScheduledTrainingJob:
        """Schedule a training job through the authorized training coordinator."""
        self._validate_coordinator_token(coordinator_token)

        if not job_id or not job_id.strip():
            raise ValueError("job_id must be non-empty.")
        if not training_run_id or not training_run_id.strip():
            raise ValueError("training_run_id must be non-empty.")
        if not simulation_id or not simulation_id.strip():
            raise ValueError("simulation_id must be non-empty.")

        fnames = tuple(feature_names) if feature_names else tuple(CANONICAL_FEATURE_NAMES)
        dim = input_dim or len(fnames) or 10

        return self._db.schedule_job(
            job_id=job_id,
            training_run_id=training_run_id,
            simulation_id=simulation_id,
            architecture=architecture,
            feature_schema_version=feature_schema_version,
            expected_rounds=expected_rounds,
            expected_epochs=expected_epochs,
            input_dim=dim,
            feature_names=fnames,
            coordinator_id=coordinator_id,
            permitted_workers=permitted_workers,
        )

    def start_training_job(
        self,
        job_id: str,
        worker_id: str | None = None,
        worker_token: str | None = None,
    ) -> StartedTrainingJob:
        """Signal that authorized training worker has initiated execution for a scheduled job."""
        clean_job_id = job_id.strip()
        if not clean_job_id:
            raise ValueError("job_id must be non-empty.")

        if self._bank_tokens:
            auth_bank = self.authenticate_bank_worker(
                worker_token,
                claimed_bank_id=worker_id if (worker_id and worker_id in self._bank_tokens) else None,
            )
            resolved_worker_id = auth_bank
        else:
            self._validate_worker_token(worker_token)
            resolved_worker_id = worker_id or "TRAINING_EXECUTOR"
            auth_bank = None

        sched = self._db.get_scheduled_job(clean_job_id)
        if (
            sched
            and sched.permitted_workers
            and auth_bank
            and auth_bank not in sched.permitted_workers
            and resolved_worker_id not in sched.permitted_workers
        ):
            raise TrainingProvenanceSecurityError(
                f"Bank '{auth_bank or resolved_worker_id}' is not authorized to start job '{clean_job_id}' (permitted: {sched.permitted_workers})."
            )

        return self._db.start_job(job_id=clean_job_id, worker_id=resolved_worker_id)

    def record_round_observation(
        self,
        job_id: str,
        round_number: int | None = None,
        sample_count: int = 0,
        metrics: dict[str, float] | None = None,
        worker_token: str | None = None,
        worker_id: str = "TRAINING_EXECUTOR",
        bank_id: str = "BANK_A",
        attempt_number: int = 1,
        actual_executed_epochs: int = 0,
        observed_loss: float | None = None,
        model_update_digest: str | None = None,
        event_id: str | None = None,
        execution_status: str = "COMPLETED",
        round_num: int | None = None,
        round_idx: int | None = None,
    ) -> RoundObservation:
        """Record genuine round progress and observations during active execution."""
        auth_bank = self.authenticate_bank_worker(worker_token, claimed_bank_id=bank_id)
        clean_job_id = job_id.strip()

        sched = self._db.get_scheduled_job(clean_job_id)
        if sched and sched.permitted_workers and auth_bank not in sched.permitted_workers:
            raise TrainingProvenanceSecurityError(
                f"Bank '{auth_bank}' is not authorized to record observations for job '{clean_job_id}' (permitted: {sched.permitted_workers})."
            )

        r_num = round_number if round_number is not None else (round_num if round_num is not None else (round_idx if round_idx is not None else 1))

        if model_update_digest is not None and not model_update_digest.strip():
            raise TrainingProvenanceSecurityError("model_update_digest cannot be empty.")

        digest_val = model_update_digest
        if not digest_val and self._is_prod:
            raise TrainingProvenanceSecurityError("model_update_digest is mandatory for recorded round observation.")

        loss_val = observed_loss
        target_metrics = dict(metrics) if metrics is not None else {}
        if loss_val is None:
            for k in ("loss", "final_loss", "train_loss", "val_loss"):
                if k in target_metrics:
                    loss_val = float(target_metrics[k])
                    break
        if loss_val is None or not math.isfinite(loss_val):
            raise TrainingProvenanceSecurityError(
                f"Missing or non-finite observed loss for round {r_num} of job '{clean_job_id}'. "
                "Loss cannot be defaulted to 0.0 or fabricated."
            )

        eid = event_id or f"evt_{clean_job_id}_r{r_num}_{auth_bank}_{attempt_number}_{uuid.uuid4().hex[:8]}"

        obs = RoundObservation(
            event_id=eid,
            job_id=clean_job_id,
            training_run_id="",
            simulation_id="",
            worker_id=worker_id or auth_bank,
            bank_id=auth_bank,
            round_number=r_num,
            attempt_number=attempt_number,
            actual_sample_count=sample_count,
            actual_executed_epochs=actual_executed_epochs,
            observed_loss=loss_val,
            metrics=target_metrics,
            execution_timestamp=datetime.now(UTC).isoformat(),
            model_update_digest=digest_val,
            execution_status=execution_status,
        )
        return self._db.record_observation(obs)

    def record_global_aggregation(
        self,
        job_id: str,
        round_number: int,
        aggregation_algorithm: str,
        accepted_participant_ids: Sequence[str],
        accepted_local_update_digests: Sequence[str],
        aggregated_global_model_digest: str,
        aggregation_configuration: dict[str, Any] | None = None,
        coordinator_token: str | None = None,
        event_id: str | None = None,
        timestamp: str | None = None,
    ) -> GlobalAggregationEvent:
        """Record global federated aggregation evidence performed by the authorized coordinator."""
        self._validate_coordinator_token(coordinator_token)
        clean_job_id = job_id.strip()

        if not accepted_participant_ids:
            raise TrainingProvenanceSecurityError(
                f"Global aggregation in round {round_number} requires at least one accepted participant (quorum check failed)."
            )

        if len(accepted_participant_ids) != len(accepted_local_update_digests):
            raise TrainingProvenanceSecurityError(
                f"Aggregation participants count ({len(accepted_participant_ids)}) does not match update digests count ({len(accepted_local_update_digests)})."
            )

        if len(set(accepted_participant_ids)) != len(accepted_participant_ids):
            raise TrainingProvenanceSecurityError(
                f"Duplicate participant ID in accepted aggregation for round {round_number}: {accepted_participant_ids}."
            )

        if len(set(accepted_local_update_digests)) != len(accepted_local_update_digests):
            raise TrainingProvenanceSecurityError(
                f"Duplicate update digest in accepted aggregation for round {round_number}: {accepted_local_update_digests}."
            )

        sched = self._db.get_scheduled_job(clean_job_id)
        if sched and sched.permitted_workers:
            for p_id in accepted_participant_ids:
                if p_id not in sched.permitted_workers:
                    raise TrainingProvenanceSecurityError(
                        f"Participant '{p_id}' is not in permitted workers for job '{clean_job_id}': permitted={sched.permitted_workers}."
                    )

        obs_list = self._db.get_observations(clean_job_id)
        existing_aggs = self._db.get_global_aggregations(clean_job_id)

        # Previous round aggregation continuity
        if round_number > 1:
            earlier_rounds = {agg.round_number for agg in existing_aggs}
            if (round_number - 1) not in earlier_rounds:
                raise TrainingProvenanceSecurityError(
                    f"Aggregation round continuity violation for job '{clean_job_id}': round {round_number} cannot be recorded without round {round_number - 1} aggregation."
                )

        earlier_digests: set[str] = set()
        for agg in existing_aggs:
            if agg.round_number < round_number:
                earlier_digests.update(agg.accepted_local_update_digests)

        for d in accepted_local_update_digests:
            if d in earlier_digests:
                raise TrainingProvenanceSecurityError(
                    f"Replay of earlier round update digest '{d}' rejected in round {round_number}."
                )

        round_obs = [o for o in obs_list if o.round_number == round_number and o.execution_status == "COMPLETED"]
        obs_by_bank = {o.bank_id: o for o in round_obs}

        for p_id, p_digest in zip(accepted_participant_ids, accepted_local_update_digests, strict=True):
            if p_id not in obs_by_bank:
                raise TrainingProvenanceSecurityError(
                    f"Accepted participant '{p_id}' has no verified completed round observation in round {round_number}."
                )
            obs = obs_by_bank[p_id]
            if obs.job_id != clean_job_id:
                raise TrainingProvenanceSecurityError(
                    f"Observation for participant '{p_id}' belongs to job '{obs.job_id}', not '{clean_job_id}'."
                )
            actual_digest = (obs.model_update_digest or "").strip()
            p_digest_clean = (p_digest or "").strip()
            if self._is_prod and not actual_digest:
                raise TrainingProvenanceSecurityError(
                    f"Participant '{p_id}' observation in round {round_number} contains missing or empty model update digest."
                )
            if not p_digest_clean:
                raise TrainingProvenanceSecurityError(
                    f"Accepted local update digest for participant '{p_id}' is missing or empty."
                )
            if actual_digest and p_digest_clean and not hmac.compare_digest(actual_digest, p_digest_clean):
                raise TrainingProvenanceSecurityError(
                    f"Accepted local update digest for participant '{p_id}' ({p_digest_clean}) does not match observed digest ({actual_digest})."
                )

        # Quorum policy
        min_quorum: int = 1
        if aggregation_configuration and any(k in aggregation_configuration for k in ("min_quorum", "min_participants", "quorum")):
            for q_key in ("min_quorum", "min_participants", "quorum"):
                if q_key in aggregation_configuration:
                    min_quorum = int(aggregation_configuration[q_key])
                    break
        elif sched and sched.permitted_workers:
            min_quorum = len(sched.permitted_workers)
        else:
            min_quorum = 1

        if len(accepted_participant_ids) < min_quorum:
            raise TrainingProvenanceSecurityError(
                f"Quorum failure for job '{clean_job_id}' round {round_number}: requires at least {min_quorum} participants, but only {len(accepted_participant_ids)} accepted ({accepted_participant_ids})."
            )

        eid = event_id or f"agg_{clean_job_id}_r{round_number}_{uuid.uuid4().hex[:8]}"
        now_str = timestamp or datetime.now(UTC).isoformat()

        event = GlobalAggregationEvent(
            event_id=eid,
            job_id=clean_job_id,
            round_number=round_number,
            aggregation_algorithm=aggregation_algorithm,
            accepted_participant_ids=tuple(accepted_participant_ids),
            accepted_local_update_digests=tuple(accepted_local_update_digests),
            aggregation_configuration=aggregation_configuration or {},
            aggregated_global_model_digest=aggregated_global_model_digest,
            timestamp=now_str,
            aggregation_status="COMPLETED",
        )
        return self._db.record_global_aggregation(event)

    def record_evaluation_observation(
        self,
        job_id: str,
        metric_name: str,
        metric_value: float,
        evaluated_artifact_digest: str,
        evaluator_identity: str = "COORDINATOR",
        evaluation_run_id: str | None = None,
        evaluation_dataset_manifest: str | None = None,
        coordinator_token: str | None = None,
        event_id: str | None = None,
    ) -> ModelEvaluationEvent:
        """Record model evaluation evidence bound to the exact evaluated artifact digest."""
        self._validate_coordinator_token(coordinator_token)
        clean_job_id = job_id.strip()
        clean_sha = evaluated_artifact_digest.strip()

        aggs = self._db.get_global_aggregations(clean_job_id)
        valid_digests = {a.aggregated_global_model_digest for a in aggs}
        if clean_sha not in valid_digests:
            raise TrainingProvenanceSecurityError(
                f"Evaluation forgery: evaluated artifact digest '{clean_sha}' does not match any authentic global aggregation for job '{clean_job_id}'."
            )

        run_id = evaluation_run_id or f"eval_{clean_job_id}_{metric_name}"
        eid = event_id or f"evt_eval_{clean_job_id}_{metric_name}_{uuid.uuid4().hex[:8]}"

        event = ModelEvaluationEvent(
            event_id=eid,
            job_id=clean_job_id,
            evaluation_run_id=run_id,
            metric_name=metric_name,
            metric_value=float(metric_value),
            evaluated_artifact_digest=clean_sha,
            evaluator_identity=evaluator_identity,
            evaluation_timestamp=datetime.now(UTC).isoformat(),
            evaluation_dataset_manifest=evaluation_dataset_manifest,
        )
        return self._db.record_model_evaluation(event)

    def get_global_aggregations(self, job_id: str) -> list[GlobalAggregationEvent]:
        return self._db.get_global_aggregations(job_id)

    def get_model_evaluations(self, job_id: str) -> list[ModelEvaluationEvent]:
        return self._db.get_model_evaluations(job_id)

    def complete_training_job(
        self,
        job_id: str,
        executed_rounds: int,
        executed_epochs: int = 0,
        sample_count: int = 0,
        observed_metrics: dict[str, float] | None = None,
        artifact_sha256: str = "",
        worker_token: str | None = None,
        coordinator_token: str | None = None,
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
        simulate_failure_before_commit: bool = False,
    ) -> CompletedJobExecutionRecord:
        """Register completion of an authorized training job with observed execution evidence."""
        if coordinator_token is not None:
            self._validate_coordinator_token(coordinator_token)
        else:
            self._validate_worker_token(worker_token)

        active_env = self._app_env or get_settings().app_env
        is_prod = active_env.lower() == "production"
        if is_prod and enforce_lifecycle is False:
            raise TrainingProvenanceSecurityError(
                "Production lifecycle enforcement cannot be disabled (enforce_lifecycle=False is forbidden in production)."
            )
        if is_prod and self._enforce_lifecycle is False:
            raise TrainingProvenanceSecurityError(
                "Production lifecycle enforcement cannot be disabled."
            )
        if is_prod:
            enforce = True
        elif enforce_lifecycle is not None:
            enforce = enforce_lifecycle
        elif self._enforce_lifecycle is not None:
            enforce = self._enforce_lifecycle
        else:
            enforce = bool(self._db.get_observations(job_id))

        return self._db.complete_job(
            job_id=job_id,
            executed_rounds=executed_rounds,
            executed_epochs=executed_epochs,
            sample_count=sample_count,
            observed_metrics=observed_metrics or {},
            artifact_sha256=artifact_sha256,
            worker_id=worker_id,
            git_commit_hash=git_commit_hash,
            dataset_manifest_id=dataset_manifest_id,
            simulation_id=simulation_id,
            training_run_id=training_run_id,
            architecture=architecture,
            feature_schema_version=feature_schema_version,
            input_dim=input_dim,
            feature_names=feature_names,
            enforce_lifecycle=enforce,
            simulate_failure_before_commit=simulate_failure_before_commit,
        )

    def record_job_completion(
        self,
        record: CompletedJobExecutionRecord | None = None,
        worker_token: str | None = None,
        coordinator_token: str | None = None,
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
                coordinator_token=coordinator_token,
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

        if not self._is_prod and not self._db.get_scheduled_job(job_id):
            self.schedule_training_job(
                job_id=job_id,
                training_run_id=training_run_id,
                simulation_id=simulation_id,
                expected_rounds=executed_rounds,
                expected_epochs=executed_epochs,
                input_dim=input_dim,
                feature_names=feature_names,
                architecture=architecture,
                feature_schema_version=feature_schema_version,
            )
            self.start_training_job(job_id=job_id, worker_token=worker_token)

        return self.complete_training_job(
            job_id=job_id,
            executed_rounds=executed_rounds,
            executed_epochs=executed_epochs,
            sample_count=sample_count,
            observed_metrics=metrics or {},
            artifact_sha256=model_artifact_sha256,
            worker_token=worker_token,
            coordinator_token=coordinator_token,
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
        rec = self._db.get_completed_job_by_id(identifier)
        if rec is not None:
            return rec
        return self._db.get_completed_job_by_run_id(identifier)

    def get_completed_job_by_id(self, job_id: str) -> CompletedJobExecutionRecord | None:
        return self._db.get_completed_job_by_id(job_id)

    def get_completed_job_by_run_id(self, run_id: str) -> CompletedJobExecutionRecord | None:
        return self._db.get_completed_job_by_run_id(run_id)

    def get_round_observations(self, job_id: str) -> list[RoundObservation]:
        return self._db.get_round_observations(job_id)

    def has_completed_job(self, identifier: str) -> bool:
        return self.get_completed_job(identifier) is not None

    def get_scheduled_job(self, job_id: str) -> ScheduledTrainingJob | None:
        return self._db.get_scheduled_job(job_id)

    def get_started_job(self, job_id: str) -> StartedTrainingJob | None:
        return self._db.get_started_job(job_id)

    def get_training_job(
        self, job_id: str
    ) -> ScheduledTrainingJob | StartedTrainingJob | CompletedJobExecutionRecord | None:
        """Retrieve job state across completed, started, or scheduled lifecycle stages."""
        completed = self._db.get_completed_job_by_id(job_id)
        if completed is not None:
            return completed
        started = self._db.get_started_job(job_id)
        if started is not None:
            return started
        return self._db.get_scheduled_job(job_id)

    def clear(self) -> None:
        self._db.clear()

    def reload(self) -> None:
        pass


_TRUST_STORE_FILE_LOCKS: dict[str, FileLock] = {}
_TRUST_STORE_LOCKS_GUARD = threading.Lock()


def _get_trust_store_file_lock(file_path: str, timeout: float = 15.0) -> FileLock:
    """Return process-shared FileLock for durable trust store."""
    lock_path = os.path.abspath(f"{file_path}.lock")
    with _TRUST_STORE_LOCKS_GUARD:
        if lock_path not in _TRUST_STORE_FILE_LOCKS:
            _TRUST_STORE_FILE_LOCKS[lock_path] = FileLock(lock_path, timeout=timeout)
        return _TRUST_STORE_FILE_LOCKS[lock_path]


def _restrict_file_permissions(filepath: str) -> None:
    """Restrict file permissions to owner read/write on POSIX systems."""
    try:
        if os.name == "posix":
            os.chmod(filepath, 0o600)
    except Exception:
        pass


class DurableTrustStore:
    """Administratively controlled, durable key trust configuration.

    Guarantees:
    - Zero Lost Updates & Non-Atomic Operations: Multi-process concurrency controlled via kernel/file locks
      and atomic read-modify-write synchronization.
    - Fail-Closed Persistence & Cryptographic Authenticity: Malformed, truncated, or tampered stores are
      detected via SHA-256 integrity digests, HMAC authentication tags, and Ed25519 administrative signatures.
      Recomputed unkeyed checksums without legitimate cryptographic authority strictly fail closed.
    - Revocation Freshness Across Processes: Verifiers detect modifications via timestamp/stat tracking and
      immediately evict revoked keys without requiring process restarts.
    - Administrative Authorization Boundary: Mutations require authorized credentials independently provisioned.
      Missing administrator credentials fail closed and disable mutations.
    """

    def __init__(
        self,
        trust_file_path: str | dict[str, Any] | None = None,
        in_memory: bool = False,
        admin_token: str | None = None,
        authority_public_key: str | None = None,
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

            self._trust_file_path = os.path.abspath(configured_path)

        self._admin_token: str | None = (
            admin_token
            or os.environ.get("CFI_TRUST_STORE_ADMIN_TOKEN")
            or getattr(get_settings(), "trust_store_admin_token", None)
        )
        self._authority_public_key_hex: str | None = (
            authority_public_key
            or os.environ.get("CFI_TRUST_STORE_AUTHORITY_KEY")
            or getattr(get_settings(), "trust_store_authority_key", None)
        )

        if self._admin_token:
            _, derived_pub, _ = self._get_authority_keys()
            if derived_pub:
                if self._authority_public_key_hex and not hmac.compare_digest(
                    self._authority_public_key_hex.lower(), derived_pub.lower()
                ):
                    raise TrainingProvenanceSecurityError(
                        "Authority key mismatch: configured authority_public_key does not match "
                        "the authority key derived from admin_token."
                    )
                self._authority_public_key_hex = derived_pub

        self._approved_keys: dict[str, dict[str, str]] = {}
        self._revoked_keys: dict[str, dict[str, str]] = {}
        self._retired_keys: dict[str, dict[str, str]] = {}
        self._last_mtime: float = 0.0
        self._last_mtime_ns: int = 0
        self._last_size: int = -1
        self._is_corrupted: bool = False

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

    def get_authority_public_key(self) -> str | None:
        """Return the hex-encoded Ed25519 authority public key."""
        return self._authority_public_key_hex

    @contextlib.contextmanager
    def _file_lock_context(self) -> Generator[None, None, None]:
        if self._in_memory or not self._trust_file_path:
            yield
            return
        lock = _get_trust_store_file_lock(self._trust_file_path)
        with lock:
            yield

    def _get_authority_keys(self) -> tuple[ed25519.Ed25519PrivateKey | None, str | None, bytes | None]:
        """Derive authoritative cryptographic key material from independently provisioned admin secret."""
        if not self._admin_token:
            return None, self._authority_public_key_hex, None
        seed = hashlib.sha256(b"CFI-TRUST-STORE-AUTH-SEED-V2:" + self._admin_token.encode("utf-8")).digest()
        priv = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
        pub_hex = priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ).hex()
        hmac_key = hashlib.sha256(b"CFI-TRUST-STORE-HMAC-KEY-V2:" + self._admin_token.encode("utf-8")).digest()
        return priv, pub_hex, hmac_key

    def _verify_admin_authorization(
        self,
        token: str | None = None,
        operator_token: str | None = None,
        internal_context: Any | None = None,
        action_name: str = "administrative_mutation",
    ) -> dict[str, Any]:
        """Verify administrative credentials for mutating trust operations.

        Enforces strict fail-closed authorization:
        - Any disk-backed store or production environment MUST have an independently provisioned admin credential.
        - Missing or unconfigured administrator authority strictly disables mutations (fail-closed).
        - Caller-supplied tokens must match the authoritative credential via timing-safe comparison.
        - When an operator_token (JWT) is provided, it must be cryptographically valid, not revoked, and have an admin role.
        - When internal_context is provided, it must be verified as a trusted internal context.
        - Arbitrary caller-controlled parameters cannot establish administrative authority.
        - Returns structured attribution metadata without fabricating an operator username.
        """
        is_prod = (
            (os.environ.get("APP_ENV") or os.environ.get("ENVIRONMENT") or get_settings().app_env).lower()
            == "production"
        )

        if internal_context is not None:
            from app.application.services.model_registry import verify_trusted_internal_context

            is_valid_ctx, ctx_reason = verify_trusted_internal_context(
                internal_context,
                expected_action=action_name,
                expected_service_name="trust_store_operator",
                expected_resource_type="trust_store",
                consume_nonce=True,
            )
            if not is_valid_ctx:
                raise TrainingProvenanceSecurityError(
                    f"Internal orchestration authority rejected for {action_name}: {ctx_reason}"
                )
            svc_name = getattr(internal_context, "service_name", None) or (
                internal_context.get("service_name") if isinstance(internal_context, dict) else ""
            )
            return {
                "actor_id": f"service:{svc_name}",
                "actor_type": "trusted_internal_service",
                "actor_institution": "system",
                "effective_role": "internal_service",
                "attribution_limitation": None,
            }

        if not self._in_memory or is_prod:
            if not self._admin_token:
                raise TrainingProvenanceSecurityError(
                    "Administrative authority is unconfigured for DurableTrustStore: "
                    "mutating operations are disabled (fail-closed)."
                )
            if not token or not hmac.compare_digest(token, self._admin_token):
                raise TrainingProvenanceSecurityError(
                    "Unauthorized administrative operation: invalid or missing admin token for DurableTrustStore."
                )
        elif self._admin_token is not None and (not token or not hmac.compare_digest(token, self._admin_token)):
            raise TrainingProvenanceSecurityError(
                "Unauthorized administrative operation: invalid or missing admin token for DurableTrustStore."
            )

        if operator_token:
            from app.infrastructure.security.auth_service import AuthenticationService

            auth_svc = AuthenticationService.get_instance()
            is_valid, claims, err = auth_svc.verify_access_token(operator_token)
            if not is_valid or not claims:
                raise TrainingProvenanceSecurityError(f"Operator token verification failed: {err}")

            raw_roles = claims.get("roles") or []
            if isinstance(raw_roles, str):
                raw_roles = [raw_roles]
            user_roles = {r.lower().strip() for r in raw_roles}
            if not (user_roles & {"admin", "security_officer", "compliance_officer"}):
                raise TrainingProvenanceSecurityError(
                    f"Operator '{claims.get('sub')}' lacks required administrative role for trust store mutation."
                )

            uname = claims.get("username") or claims.get("sub") or "unknown_user"
            bank = claims.get("bank_id") or "system"
            return {
                "actor_id": f"user:{uname}@{bank}",
                "actor_type": "authenticated_operator",
                "actor_institution": bank,
                "effective_role": list(user_roles),
                "attribution_limitation": None,
            }

        return {
            "actor_id": "unattributed_shared_admin_secret",
            "actor_type": "shared_secret",
            "actor_institution": "system",
            "effective_role": "admin_secret_holder",
            "attribution_limitation": "AUTHENTICATION_SECRET_PRESENT_BUT_INDIVIDUAL_OPERATOR_UNATTRIBUTED",
        }

    def _reload_unlocked(self, force: bool = False) -> None:
        """Internal reload without acquiring locks (must be called with self._lock and file lock held)."""
        if self._in_memory or not self._trust_file_path:
            return
        if not os.path.exists(self._trust_file_path):
            self._last_mtime = 0.0
            self._last_mtime_ns = 0
            self._last_size = -1
            self._is_corrupted = False
            return

        try:
            stat = os.stat(self._trust_file_path)
            last_ns = getattr(self, "_last_mtime_ns", 0)
            last_sz = getattr(self, "_last_size", -1)
            if not force and stat.st_mtime_ns == last_ns and stat.st_size == last_sz and stat.st_mtime == self._last_mtime:
                return

            if stat.st_size == 0:
                self._is_corrupted = True
                raise TrainingProvenanceSecurityError(
                    f"Durable trust store at '{self._trust_file_path}' is corrupted: file is empty (0 bytes)."
                )

            with open(self._trust_file_path, "rb") as f:
                raw_bytes = f.read()

            try:
                container = json.loads(raw_bytes.decode("utf-8"))
            except Exception as parse_err:
                self._is_corrupted = True
                raise TrainingProvenanceSecurityError(
                    f"Durable trust store at '{self._trust_file_path}' is corrupted: invalid JSON ({parse_err})."
                ) from parse_err

            if not isinstance(container, dict):
                self._is_corrupted = True
                raise TrainingProvenanceSecurityError(
                    f"Durable trust store at '{self._trust_file_path}' is malformed: root JSON structure must be an object."
                )

            # Layer 1: Schema structure validation
            data = container.get("data", container)
            if not isinstance(data, dict):
                self._is_corrupted = True
                raise TrainingProvenanceSecurityError(
                    f"Durable trust store at '{self._trust_file_path}' is malformed: 'data' payload must be a dictionary."
                )

            for key_field in ("approved_keys", "revoked_keys", "retired_keys"):
                val = data.get(key_field)
                if val is not None and not isinstance(val, dict):
                    self._is_corrupted = True
                    raise TrainingProvenanceSecurityError(
                        f"Durable trust store at '{self._trust_file_path}' is malformed: '{key_field}' must be a dictionary."
                    )

            canonical_bytes = json.dumps(data, sort_keys=True).encode("utf-8")

            # Layer 2: Fast accidental corruption detection (checksum)
            if "integrity_sha256" in container:
                expected_sha = container["integrity_sha256"]
                actual_sha = hashlib.sha256(canonical_bytes).hexdigest()
                if not hmac.compare_digest(expected_sha, actual_sha):
                    self._is_corrupted = True
                    raise TrainingProvenanceSecurityError(
                        f"Durable trust store at '{self._trust_file_path}' failed cryptographic integrity check: "
                        f"checksum mismatch (expected {expected_sha}, computed {actual_sha})."
                    )

            # Layer 3: Cryptographic Authenticity & Source-of-Authority Verification
            is_prod = (
                (os.environ.get("APP_ENV") or os.environ.get("ENVIRONMENT") or get_settings().app_env).lower()
                == "production"
            )
            has_auth = "authority_signature" in container or "integrity_hmac_sha256" in container
            if not has_auth:
                if is_prod or self._admin_token is not None or self._authority_public_key_hex is not None:
                    self._is_corrupted = True
                    raise TrainingProvenanceSecurityError(
                        f"Durable trust store at '{self._trust_file_path}' rejected: "
                        f"unauthenticated persistence format lacking cryptographic authentication signature or HMAC."
                    )
            else:
                # Keyed HMAC check if administrator credentials available
                if self._admin_token:
                    _, _, hmac_key = self._get_authority_keys()
                    if hmac_key:
                        expected_hmac = container.get("integrity_hmac_sha256")
                        if not expected_hmac:
                            self._is_corrupted = True
                            raise TrainingProvenanceSecurityError(
                                f"Durable trust store at '{self._trust_file_path}' failed cryptographic authentication: "
                                "missing 'integrity_hmac_sha256' authentication tag."
                            )
                        actual_hmac = hmac.new(hmac_key, canonical_bytes, hashlib.sha256).hexdigest()
                        if not hmac.compare_digest(expected_hmac, actual_hmac):
                            self._is_corrupted = True
                            raise TrainingProvenanceSecurityError(
                                f"Durable trust store at '{self._trust_file_path}' failed cryptographic authentication: "
                                "HMAC authentication tag mismatch (unauthorized modification)."
                            )

                # Signature verification (applicable to both workers and admin)
                file_pub = container.get("authority_public_key_hex")
                file_sig = container.get("authority_signature")
                if not file_pub or not file_sig:
                    self._is_corrupted = True
                    raise TrainingProvenanceSecurityError(
                        f"Durable trust store at '{self._trust_file_path}' failed cryptographic authentication: "
                        "missing authority public key or signature."
                    )

                is_prod = (
                    (os.environ.get("APP_ENV") or os.environ.get("ENVIRONMENT") or get_settings().app_env).lower()
                    == "production"
                )

                if is_prod and not self._authority_public_key_hex:
                    self._is_corrupted = True
                    raise TrainingProvenanceSecurityError(
                        f"Production security boundary violation: DurableTrustStore at '{self._trust_file_path}' "
                        "requires an independently provisioned authority public key (authority_public_key or "
                        "CFI_TRUST_STORE_AUTHORITY_KEY). Establishing trust from unverified disk files is prohibited."
                    )

                if self._authority_public_key_hex:
                    expected_pub = self._authority_public_key_hex
                    anchor_path = f"{self._trust_file_path}.authority_pub"
                    if os.path.exists(anchor_path):
                        with open(anchor_path, encoding="utf-8") as af:
                            anchor_pub = af.read().strip()
                        if not hmac.compare_digest(anchor_pub.lower(), expected_pub.lower()):
                            self._is_corrupted = True
                            raise TrainingProvenanceSecurityError(
                                f"Durable trust store at '{self._trust_file_path}' authority anchor mismatch: "
                                f"expected authority '{expected_pub}', anchor file contains '{anchor_pub}'."
                            )
                else:
                    anchor_path = f"{self._trust_file_path}.authority_pub"
                    if os.path.exists(anchor_path):
                        with open(anchor_path, encoding="utf-8") as af:
                            expected_pub = af.read().strip()
                        if not expected_pub:
                            self._is_corrupted = True
                            raise TrainingProvenanceSecurityError(
                                f"Durable trust store at '{self._trust_file_path}' rejected: "
                                "authority anchor file is empty. Trust-on-First-Use from unverified JSON container is prohibited."
                            )
                        self._authority_public_key_hex = expected_pub
                    else:
                        self._is_corrupted = True
                        raise TrainingProvenanceSecurityError(
                            f"Durable trust store at '{self._trust_file_path}' rejected: "
                            "no trusted authority public key configured and no authority anchor file found. "
                            "Trust-on-First-Use from unverified JSON container is prohibited."
                        )

                if not hmac.compare_digest(file_pub.lower(), expected_pub.lower()):
                    self._is_corrupted = True
                    raise TrainingProvenanceSecurityError(
                        f"Durable trust store at '{self._trust_file_path}' authority mismatch: "
                        f"expected authority '{expected_pub}', found '{file_pub}'."
                    )

                try:
                    pub_obj = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(expected_pub))
                    pub_obj.verify(bytes.fromhex(file_sig), canonical_bytes)
                except Exception as sig_err:
                    self._is_corrupted = True
                    raise TrainingProvenanceSecurityError(
                        f"Durable trust store at '{self._trust_file_path}' failed cryptographic signature verification: "
                        f"unauthorized modification detected ({sig_err})."
                    ) from sig_err

            self._approved_keys = dict(data.get("approved_keys", {}))
            self._revoked_keys = dict(data.get("revoked_keys", {}))
            self._retired_keys = dict(data.get("retired_keys", {}))
            self._last_mtime = stat.st_mtime
            self._last_mtime_ns = stat.st_mtime_ns
            self._last_size = stat.st_size
            self._is_corrupted = False
        except TrainingProvenanceSecurityError:
            self._is_corrupted = True
            raise
        except Exception as exc:
            self._is_corrupted = True
            raise TrainingProvenanceSecurityError(
                f"Failed to access or parse durable trust store from '{self._trust_file_path}': {exc}"
            ) from exc

    def reload(self) -> None:
        """Force complete reload of durable trust store from disk."""
        if self._in_memory or not self._trust_file_path:
            return
        with self._lock, self._file_lock_context():
            self._reload_unlocked(force=True)

    def reload_if_modified(self) -> None:
        """Check mtime, mtime_ns, and size, and reload if modified on disk (cross-worker freshness contract)."""
        if self._in_memory or not self._trust_file_path:
            return
        with self._lock:
            if self._is_corrupted:
                raise TrainingProvenanceSecurityError(
                    f"Durable trust store at '{self._trust_file_path}' is in corrupted state. Operations fail closed."
                )
            if os.path.exists(self._trust_file_path):
                stat = os.stat(self._trust_file_path)
                last_ns = getattr(self, "_last_mtime_ns", 0)
                last_sz = getattr(self, "_last_size", -1)
                if stat.st_mtime_ns != last_ns or stat.st_size != last_sz or stat.st_mtime != self._last_mtime:
                    with self._file_lock_context():
                        self._reload_unlocked(force=True)

    def _persist_unlocked(self) -> None:
        """Atomically persist trust registry to disk with cryptographic authentication, integrity digest, and fsync."""
        if self._in_memory or not self._trust_file_path:
            return

        priv_key, pub_hex, hmac_key = self._get_authority_keys()
        if priv_key is None or pub_hex is None or hmac_key is None:
            raise TrainingProvenanceSecurityError(
                "Cannot persist durable trust store: authoritative administrative signing credentials are required."
            )

        # Ensure administrator authority cannot be replaced merely by supplying a different admin_token
        if os.path.exists(self._trust_file_path) and os.path.getsize(self._trust_file_path) > 0:
            try:
                with open(self._trust_file_path, "rb") as ef:
                    existing_raw = ef.read().decode("utf-8")
                existing_container = json.loads(existing_raw)
                if isinstance(existing_container, dict):
                    existing_pub = existing_container.get("authority_public_key_hex")
                    if existing_pub and not hmac.compare_digest(existing_pub.lower(), pub_hex.lower()):
                        raise TrainingProvenanceSecurityError(
                            f"Cannot persist durable trust store at '{self._trust_file_path}': "
                            f"cannot replace existing authoritative trust anchor '{existing_pub}' with '{pub_hex}'."
                        )
            except (TrainingProvenanceSecurityError, json.JSONDecodeError):
                raise
            except Exception:
                pass

        payload = {
            "approved_keys": self._approved_keys,
            "revoked_keys": self._revoked_keys,
            "retired_keys": self._retired_keys,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        canonical_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
        checksum = hashlib.sha256(canonical_bytes).hexdigest()
        auth_sig = priv_key.sign(canonical_bytes).hex()
        auth_hmac = hmac.new(hmac_key, canonical_bytes, hashlib.sha256).hexdigest()

        container = {
            "schema_version": "2.0",
            "data": payload,
            "integrity_sha256": checksum,
            "integrity_hmac_sha256": auth_hmac,
            "authority_public_key_hex": pub_hex,
            "authority_signature": auth_sig,
        }

        parent_dir = os.path.dirname(os.path.abspath(self._trust_file_path))
        os.makedirs(parent_dir, exist_ok=True)
        tmp_path = f"{self._trust_file_path}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
        with open(tmp_path, "wb") as f:
            f.write(json.dumps(container, indent=2).encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())

        _restrict_file_permissions(tmp_path)
        os.replace(tmp_path, self._trust_file_path)

        # Write authority anchor file
        anchor_path = f"{self._trust_file_path}.authority_pub"
        anchor_tmp = f"{anchor_path}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
        with open(anchor_tmp, "w", encoding="utf-8") as af:
            af.write(pub_hex)
            af.flush()
            os.fsync(af.fileno())
        _restrict_file_permissions(anchor_tmp)
        os.replace(anchor_tmp, anchor_path)

        stat = os.stat(self._trust_file_path)
        self._last_mtime = stat.st_mtime
        self._last_mtime_ns = stat.st_mtime_ns
        self._last_size = stat.st_size
        self._authority_public_key_hex = pub_hex
        self._is_corrupted = False

    def register_approved_key(
        self,
        key_id: str,
        public_key_hex: str,
        authorized_replacement: bool = False,
        admin_token: str | None = None,
        operator_token: str | None = None,
        internal_context: Any | None = None,
    ) -> None:
        """Register an approved key ID and public key."""
        attribution = self._verify_admin_authorization(
            token=admin_token,
            operator_token=operator_token,
            internal_context=internal_context,
            action_name="register_approved_key",
        )
        with self._lock, self._file_lock_context():
            self._reload_unlocked(force=True)
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

            from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain

            ImmutableAuditChain.get_instance().append_event(
                event_type="TRUST_STORE_KEY_REGISTRATION",
                actor_id=attribution["actor_id"],
                action=f"register_approved_key:{clean_kid}",
                resource_id=f"trust_store:key:{clean_kid}",
                details={
                    "key_id": clean_kid,
                    "public_key_hex": clean_hex,
                    "authorized_replacement": authorized_replacement,
                    "actor_type": attribution["actor_type"],
                    "actor_institution": attribution["actor_institution"],
                    "effective_role": attribution["effective_role"],
                    "attribution_limitation": attribution["attribution_limitation"],
                    "store_path": self._trust_file_path or ":memory:",
                },
            )

            self._approved_keys[clean_kid] = {
                "public_key_hex": clean_hex,
                "registered_at": datetime.now(UTC).isoformat(),
                "registered_by": attribution["actor_id"],
                "actor_type": attribution["actor_type"],
                "actor_institution": attribution["actor_institution"],
                "attribution_limitation": attribution["attribution_limitation"],
            }
            self._persist_unlocked()

    def revoke_key(
        self,
        key_id: str,
        reason: str = "compromise",
        admin_token: str | None = None,
        operator_token: str | None = None,
        internal_context: Any | None = None,
    ) -> None:
        """Revoke key due to compromise (all receipts signed by this key are rejected)."""
        attribution = self._verify_admin_authorization(
            token=admin_token,
            operator_token=operator_token,
            internal_context=internal_context,
            action_name="revoke_key",
        )
        with self._lock, self._file_lock_context():
            self._reload_unlocked(force=True)
            clean_kid = key_id.strip()

            from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain

            ImmutableAuditChain.get_instance().append_event(
                event_type="TRUST_STORE_KEY_REVOCATION",
                actor_id=attribution["actor_id"],
                action=f"revoke_key:{clean_kid}",
                resource_id=f"trust_store:key:{clean_kid}",
                details={
                    "key_id": clean_kid,
                    "reason": reason,
                    "actor_type": attribution["actor_type"],
                    "actor_institution": attribution["actor_institution"],
                    "effective_role": attribution["effective_role"],
                    "attribution_limitation": attribution["attribution_limitation"],
                    "store_path": self._trust_file_path or ":memory:",
                },
            )

            self._revoked_keys[clean_kid] = {
                "reason": reason,
                "revoked_at": datetime.now(UTC).isoformat(),
                "revoked_by": attribution["actor_id"],
                "actor_type": attribution["actor_type"],
                "actor_institution": attribution["actor_institution"],
                "effective_role": attribution["effective_role"],
                "attribution_limitation": attribution["attribution_limitation"],
            }
            self._approved_keys.pop(clean_kid, None)
            self._persist_unlocked()

    def retire_key(
        self,
        key_id: str,
        reason: str = "lifecycle_rotation",
        admin_token: str | None = None,
        operator_token: str | None = None,
        internal_context: Any | None = None,
    ) -> None:
        """Retire key from active issuance; existing receipts created before retirement remain valid."""
        attribution = self._verify_admin_authorization(
            token=admin_token,
            operator_token=operator_token,
            internal_context=internal_context,
            action_name="retire_key",
        )
        with self._lock, self._file_lock_context():
            self._reload_unlocked(force=True)
            clean_kid = key_id.strip()

            from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain

            ImmutableAuditChain.get_instance().append_event(
                event_type="TRUST_STORE_KEY_RETIREMENT",
                actor_id=attribution["actor_id"],
                action=f"retire_key:{clean_kid}",
                resource_id=f"trust_store:key:{clean_kid}",
                details={
                    "key_id": clean_kid,
                    "reason": reason,
                    "actor_type": attribution["actor_type"],
                    "actor_institution": attribution["actor_institution"],
                    "effective_role": attribution["effective_role"],
                    "attribution_limitation": attribution["attribution_limitation"],
                    "store_path": self._trust_file_path or ":memory:",
                },
            )

            self._retired_keys[clean_kid] = {
                "reason": reason,
                "retired_at": datetime.now(UTC).isoformat(),
                "retired_by": attribution["actor_id"],
                "actor_type": attribution["actor_type"],
                "actor_institution": attribution["actor_institution"],
                "effective_role": attribution["effective_role"],
                "attribution_limitation": attribution["attribution_limitation"],
            }
            self._persist_unlocked()

    def rotate_key(
        self,
        old_key_id: str,
        new_key_id: str,
        new_public_key_hex: str,
        reason: str = "lifecycle_rotation",
        admin_token: str | None = None,
        operator_token: str | None = None,
        internal_context: Any | None = None,
    ) -> None:
        """Atomically rotate trusted keys by retiring old key and registering new key."""
        attribution = self._verify_admin_authorization(
            token=admin_token,
            operator_token=operator_token,
            internal_context=internal_context,
            action_name="rotate_key",
        )
        with self._lock, self._file_lock_context():
            self._reload_unlocked(force=True)
            clean_old = old_key_id.strip()
            clean_new = new_key_id.strip()
            clean_hex = new_public_key_hex.strip().lower()

            if clean_new in self._revoked_keys:
                raise ValueError(
                    f"Cannot rotate to key_id '{clean_new}' because it has been revoked due to compromise."
                )

            from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain

            ImmutableAuditChain.get_instance().append_event(
                event_type="TRUST_STORE_KEY_ROTATION",
                actor_id=attribution["actor_id"],
                action=f"rotate_key:{clean_old}->{clean_new}",
                resource_id=f"trust_store:key:{clean_new}",
                details={
                    "old_key_id": clean_old,
                    "new_key_id": clean_new,
                    "new_public_key_hex": clean_hex,
                    "reason": reason,
                    "actor_type": attribution["actor_type"],
                    "actor_institution": attribution["actor_institution"],
                    "effective_role": attribution["effective_role"],
                    "attribution_limitation": attribution["attribution_limitation"],
                    "store_path": self._trust_file_path or ":memory:",
                },
            )

            self._retired_keys[clean_old] = {
                "reason": reason,
                "retired_at": datetime.now(UTC).isoformat(),
                "retired_by": attribution["actor_id"],
                "actor_type": attribution["actor_type"],
                "actor_institution": attribution["actor_institution"],
                "effective_role": attribution["effective_role"],
                "attribution_limitation": attribution["attribution_limitation"],
            }
            self._approved_keys[clean_new] = {
                "public_key_hex": clean_hex,
                "registered_at": datetime.now(UTC).isoformat(),
                "registered_by": attribution["actor_id"],
                "actor_type": attribution["actor_type"],
                "actor_institution": attribution["actor_institution"],
                "attribution_limitation": attribution["attribution_limitation"],
            }
            self._persist_unlocked()

    def is_key_revoked(self, key_id: str) -> bool:
        with self._lock:
            self.reload_if_modified()
            if self._is_corrupted:
                raise TrainingProvenanceSecurityError("Durable trust store is corrupted. Access denied.")
            return key_id.strip() in self._revoked_keys

    def is_key_retired(self, key_id: str, at_timestamp: str | None = None) -> tuple[bool, str | None]:
        with self._lock:
            self.reload_if_modified()
            if self._is_corrupted:
                raise TrainingProvenanceSecurityError("Durable trust store is corrupted. Access denied.")
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
            if self._is_corrupted:
                raise TrainingProvenanceSecurityError("Durable trust store is corrupted. Access denied.")
            return {
                kid: v.get("public_key_hex", "")
                for kid, v in self._approved_keys.items()
                if isinstance(v, dict) and "public_key_hex" in v
            }

    def get_revoked_keys(self) -> dict[str, dict[str, str]]:
        with self._lock:
            self.reload_if_modified()
            if self._is_corrupted:
                raise TrainingProvenanceSecurityError("Durable trust store is corrupted. Access denied.")
            return dict(self._revoked_keys)

    def clear(self, admin_token: str | None = None) -> None:
        self._verify_admin_authorization(admin_token)
        with self._lock, self._file_lock_context():
            self._approved_keys.clear()
            self._revoked_keys.clear()
            self._retired_keys.clear()
            if self._trust_file_path and os.path.exists(self._trust_file_path):
                with contextlib.suppress(OSError):
                    os.remove(self._trust_file_path)
            self._last_mtime = 0.0
            self._last_mtime_ns = 0
            self._last_size = -1
            self._is_corrupted = False


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
    coordinator_token: str | None = None,
    coordinator_id: str = "COORDINATOR",
    permitted_workers: Sequence[str] = (),
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
        coordinator_token=coordinator_token,
        coordinator_id=coordinator_id,
        permitted_workers=permitted_workers,
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


def record_round_observation(
    job_id: str,
    round_number: int | None = None,
    sample_count: int = 0,
    metrics: dict[str, float] | None = None,
    worker_token: str | None = None,
    worker_id: str = "TRAINING_EXECUTOR",
    bank_id: str = "BANK_A",
    attempt_number: int = 1,
    actual_executed_epochs: int = 0,
    observed_loss: float | None = None,
    model_update_digest: str | None = None,
    event_id: str | None = None,
    execution_status: str = "COMPLETED",
    round_num: int | None = None,
    round_idx: int | None = None,
) -> RoundObservation:
    """Convenience helper to record genuine round execution observations with the default record source."""
    return get_default_training_job_record_source().record_round_observation(
        job_id=job_id,
        round_number=round_number,
        sample_count=sample_count,
        metrics=metrics,
        worker_token=worker_token,
        worker_id=worker_id,
        bank_id=bank_id,
        attempt_number=attempt_number,
        actual_executed_epochs=actual_executed_epochs,
        observed_loss=observed_loss,
        model_update_digest=model_update_digest,
        event_id=event_id,
        execution_status=execution_status,
        round_num=round_num,
        round_idx=round_idx,
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
    owning_bank_id: str | None = None
    consortium_id: str | None = None

    def canonical_dict(self) -> dict[str, Any]:
        """Return dict of signed fields without signature."""
        d = {
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
        if self.owning_bank_id is not None:
            d["owning_bank_id"] = self.owning_bank_id
        if self.consortium_id is not None:
            d["consortium_id"] = self.consortium_id
        return d

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
            owning_bank_id=data.get("owning_bank_id"),
            consortium_id=data.get("consortium_id"),
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
        app_env: str | None = None,
    ) -> None:
        if signing_key_hex is not None:
            if signing_key_hex == "":
                raise TrainingProvenanceConfigurationError("Signing key hex must be provided and non-empty.")
            private_key = signing_key_hex
        if enforce_job_records is not None:
            require_authenticated_job = enforce_job_records

        self.key_id = key_id
        self._job_record_source = job_record_source or get_default_training_job_record_source()
        self._trust_store = trust_store or get_default_trust_store()
        settings = get_settings()
        is_prod = (app_env.lower() == "production") if app_env is not None else (settings.app_env.lower() == "production")

        if is_prod and require_authenticated_job is False:
            raise TrainingProvenanceConfigurationError(
                "Production receipt issuance requires authenticated job record verification (require_authenticated_job=False is forbidden in production)."
            )
        self._require_authenticated_job = True if is_prod else require_authenticated_job

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
        owning_bank_id: str | None = None,
        consortium_id: str | None = None,
    ) -> TrainingExecutionReceipt:
        """Issue an authenticated training execution receipt signed with Ed25519 private key."""
        settings = get_settings()
        is_prod = settings.app_env.lower() == "production"

        target_owning_bank = owning_bank_id
        target_consortium = consortium_id

        if evidence is not None:
            evidence.validate()
            if getattr(evidence, "owning_bank_id", None) and target_owning_bank is None:
                target_owning_bank = evidence.owning_bank_id
            if getattr(evidence, "consortium_id", None) and target_consortium is None:
                target_consortium = evidence.consortium_id
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
            True
            if is_prod
            else (self._require_authenticated_job if self._require_authenticated_job is not None else False)
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
                raise TrainingProvenanceSecurityError(
                    f"Job '{target_job}' in trusted completion source is not in 'COMPLETED' status (got '{rec.status}')."
                )

            # Strict complete identity and field binding (Section 5)
            if rec.job_id != target_job:
                raise TrainingProvenanceSecurityError(
                    f"Job ID mismatch: caller requested '{target_job}', but trusted record is for '{rec.job_id}'."
                )
            if rec.training_run_id != target_run_id:
                raise TrainingProvenanceSecurityError(
                    f"Training run ID mismatch: caller claimed '{target_run_id}', but trusted record is for '{rec.training_run_id}'."
                )
            if rec.simulation_id != target_sim_id:
                raise TrainingProvenanceSecurityError(
                    f"Simulation ID mismatch: caller claimed '{target_sim_id}', but trusted record is for '{rec.simulation_id}'."
                )
            if rec.worker_id != target_producer:
                raise TrainingProvenanceSecurityError(
                    f"Producer identity mismatch: caller claimed '{target_producer}', but trusted record observed '{rec.worker_id}'."
                )
            if target_rounds != rec.executed_rounds:
                raise TrainingProvenanceSecurityError(
                    f"Executed rounds mismatch: caller claimed {target_rounds}, but trusted record observed {rec.executed_rounds}."
                )
            if target_epochs != rec.executed_epochs:
                raise TrainingProvenanceSecurityError(
                    f"Executed epochs mismatch: caller claimed {target_epochs}, but trusted record observed {rec.executed_epochs}."
                )
            if target_samples != rec.sample_count:
                raise TrainingProvenanceSecurityError(
                    f"Sample count mismatch: caller claimed {target_samples}, but trusted record observed {rec.sample_count}."
                )
            if not hmac.compare_digest(target_sha, rec.artifact_sha256):
                raise TrainingProvenanceSecurityError(
                    f"Artifact digest mismatch: caller provided {target_sha}, but trusted record observed {rec.artifact_sha256}."
                )
            if target_arch != rec.architecture:
                raise TrainingProvenanceSecurityError(
                    f"Model architecture mismatch: caller claimed '{target_arch}', but trusted record observed '{rec.architecture}'."
                )
            if target_schema_ver != rec.feature_schema_version:
                raise TrainingProvenanceSecurityError(
                    f"Feature schema version mismatch: caller claimed '{target_schema_ver}', but trusted record observed '{rec.feature_schema_version}'."
                )
            if target_input_dim != rec.input_dim:
                raise TrainingProvenanceSecurityError(
                    f"Input dimension mismatch: caller claimed {target_input_dim}, but trusted record observed {rec.input_dim}."
                )
            if tuple(fnames) != tuple(rec.feature_names):
                raise TrainingProvenanceSecurityError(
                    "Feature names or ordering mismatch: caller provided feature list does not match trusted execution record."
                )

            # Match observed metrics
            for k, expected_v in rec.observed_metrics.items():
                if k not in target_metrics:
                    raise TrainingProvenanceSecurityError(f"Observed metric '{k}' missing from receipt request.")
                actual_v = target_metrics[k]
                if not (isinstance(actual_v, (int, float)) and math.isclose(actual_v, expected_v, rel_tol=1e-7, abs_tol=1e-7)):
                    raise TrainingProvenanceSecurityError(
                        f"Metric '{k}' mismatch: caller claimed {actual_v}, but trusted record observed {expected_v}."
                    )
            if rec.dataset_manifest_id and target_manifest and target_manifest != rec.dataset_manifest_id:
                raise TrainingProvenanceSecurityError(
                    f"Dataset manifest mismatch: caller claimed '{target_manifest}', but trusted record observed '{rec.dataset_manifest_id}'."
                )
            if rec.git_commit_hash and target_git and target_git != rec.git_commit_hash:
                raise TrainingProvenanceSecurityError(
                    f"Git commit hash mismatch: caller claimed '{target_git}', but trusted record observed '{rec.git_commit_hash}'."
                )

            # Finding SIGN-TRUST-01: Issuer independently reconciles underlying evidence chain
            is_durable_source = getattr(self._job_record_source, "is_durable", False)
            source_enforces_lifecycle = getattr(self._job_record_source, "_enforce_lifecycle", False)
            if (is_prod or source_enforces_lifecycle) and rec.executed_rounds > 0 and is_durable_source:
                if hasattr(self._job_record_source, "get_round_observations"):
                    obs_list = self._job_record_source.get_round_observations(target_job)
                    if not obs_list:
                        raise TrainingProvenanceSecurityError(
                            f"Production receipt issuance rejected: job '{target_job}' has no observed round events in authority. "
                            "Direct SQL insertion or completed row forgery detected."
                        )
                if hasattr(self._job_record_source, "get_global_aggregations"):
                    aggs = self._job_record_source.get_global_aggregations(target_job)
                    if not aggs:
                        raise TrainingProvenanceSecurityError(
                            f"Production receipt issuance rejected: job '{target_job}' has no recorded global aggregation evidence. "
                            "Direct SQL insertion or completed row forgery detected."
                        )
                if hasattr(self._job_record_source, "get_model_evaluations"):
                    evals = self._job_record_source.get_model_evaluations(target_job)
                    if evals:
                        eval_metrics = {ev.metric_name: ev.metric_value for ev in evals}
                        for m_name in ("val_auc", "auc_roc", "val_loss"):
                            if (
                                m_name in eval_metrics
                                and m_name in rec.observed_metrics
                                and not math.isclose(eval_metrics[m_name], rec.observed_metrics[m_name], rel_tol=1e-5, abs_tol=1e-5)
                            ):
                                raise TrainingProvenanceSecurityError(
                                    f"Production receipt issuance rejected: metric '{m_name}' in completion record ({rec.observed_metrics[m_name]}) "
                                    f"does not match authoritative evaluation event ({eval_metrics[m_name]})."
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
        if target_owning_bank is not None:
            receipt_data["owning_bank_id"] = target_owning_bank
        if target_consortium is not None:
            receipt_data["consortium_id"] = target_consortium
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
            owning_bank_id=target_owning_bank,
            consortium_id=target_consortium,
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
        self._in_memory_only = False
        if trust_store is not None:
            self._trust_store = trust_store
            try:
                for kid, hex_val in self._trust_store.get_approved_keys().items():
                    if hex_val:
                        with contextlib.suppress(Exception):
                            self._trusted_keys[kid] = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(hex_val))
            except TrainingProvenanceSecurityError:
                self._trusted_keys.clear()
        elif trusted_keys is not None:
            self._trust_store = DurableTrustStore(in_memory=True)
            self._in_memory_only = True
        else:
            settings = get_settings()
            if settings.app_env.lower() != "production" and not os.environ.get("CFI_TRAINING_RECEIPT_TRUST_STORE"):
                self._trust_store = DurableTrustStore(in_memory=True)
                self._in_memory_only = True
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
        admin_token: str | None = None,
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
        if not self._in_memory_only:
            try:
                approved_map = self._trust_store.get_approved_keys()
                if clean_kid in approved_map and approved_map[clean_kid].lower() == new_bytes.hex().lower():
                    self._trusted_keys[clean_kid] = parsed_pub
                    return
            except TrainingProvenanceSecurityError:
                pass

        self._trust_store.register_approved_key(
            clean_kid, new_bytes.hex(), authorized_replacement=authorized_replacement, admin_token=admin_token
        )
        self._trusted_keys[clean_kid] = parsed_pub

    def _sync_with_trust_store(self) -> None:
        """Synchronize in-memory verifier caches with authoritative durable trust store."""
        try:
            self._trust_store.reload_if_modified()
        except TrainingProvenanceSecurityError:
            self._trusted_keys.clear()
            raise

        for rkid in self._trust_store.get_revoked_keys():
            self._revoked_keys.add(rkid)
            self._trusted_keys.pop(rkid, None)

        if not self._in_memory_only:
            approved_map = self._trust_store.get_approved_keys()
            to_remove = [k for k in self._trusted_keys if k not in approved_map]
            for k in to_remove:
                self._trusted_keys.pop(k, None)

    def revoke_key(
        self,
        key_id: str,
        reason: str = "compromise",
        admin_token: str | None = None,
    ) -> None:
        """Revoke a trusted key ID so receipts signed with it will be rejected."""
        clean_kid = key_id.strip()
        self._revoked_keys.add(clean_kid)
        self._trusted_keys.pop(clean_kid, None)
        self._trust_store.revoke_key(clean_kid, reason=reason, admin_token=admin_token)
        logger.warning("Revoked training receipt key_id '%s' (reason: %s)", clean_kid, reason)

    def retire_key(
        self,
        key_id: str,
        reason: str = "lifecycle_rotation",
        admin_token: str | None = None,
    ) -> None:
        """Retire a trusted key ID from active issuance (historical verification preserved)."""
        clean_kid = key_id.strip()
        self._trust_store.retire_key(clean_kid, reason=reason, admin_token=admin_token)
        logger.info("Retired training receipt key_id '%s' (reason: %s)", clean_kid, reason)

    def is_key_trusted(self, key_id: str) -> bool:
        clean_kid = key_id.strip()
        try:
            self._sync_with_trust_store()
        except (TrainingProvenanceSecurityError, Exception):
            self._trusted_keys.clear()
            return False
        if clean_kid in self._revoked_keys or self._trust_store.is_key_revoked(clean_kid):
            self._trusted_keys.pop(clean_kid, None)
            return False
        if not self._in_memory_only:
            try:
                approved_map = self._trust_store.get_approved_keys()
            except (TrainingProvenanceSecurityError, Exception):
                self._trusted_keys.clear()
                return False
            if clean_kid not in approved_map:
                self._trusted_keys.pop(clean_kid, None)
                return False
            if clean_kid not in self._trusted_keys and clean_kid in approved_map:
                self.register_trusted_key(clean_kid, approved_map[clean_kid], authorized_replacement=True)
        return clean_kid in self._trusted_keys

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
        try:
            self._sync_with_trust_store()
        except (TrainingProvenanceSecurityError, Exception) as exc:
            return False, f"Trust store corrupted or unavailable: {exc}"

        if receipt_obj.key_id in self._revoked_keys or self._trust_store.is_key_revoked(receipt_obj.key_id):
            self._trusted_keys.pop(receipt_obj.key_id, None)
            self._revoked_keys.add(receipt_obj.key_id)
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

        if not self._in_memory_only:
            approved_map = self._trust_store.get_approved_keys()
            if receipt_obj.key_id not in approved_map:
                self._trusted_keys.pop(receipt_obj.key_id, None)
                return False, f"Receipt key_id '{receipt_obj.key_id}' is not present in trusted public key registry."
            if receipt_obj.key_id not in self._trusted_keys:
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
