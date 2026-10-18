"""Operational Federated Training Service.

Connects authorized operational entry points (authenticated API router, cfi-cli)
to the canonical hardened distributed federated learning lifecycle.

Security & Architectural Contracts Enforced:
1. Operator Authorization: Initiator must be authenticated and authorized via live RBAC / clearance.
2. Server-Side Authority Resolution: Sensitive credentials, signing keys, completion authority endpoints,
   database URLs, and bank worker endpoints are resolved strictly server-side; client JSON payloads never
   contain private credentials, file paths, or process handles.
3. Pre-Provisioned Bank Endpoints: In production, worker endpoints must be pre-provisioned / registered
   by institution daemons without exposing bank private credentials or raw datasets to the coordinator.
4. Canonical Runner Invocations: Invokes run_production_federation under Windows Restricted Token isolation
   in production environments.
5. Separation of Authorities: Coordinator holds coordinator_token only. Receipt signing authority and
   completion authority remain in separate isolated domains.
6. Truthful Observability: Execution state, failures, and metrics are accurately tracked without fabricated
   success, dummy receipts, or synthetic fallbacks.
7. Model Registry Invariant: Saved candidate models are registered as inactive with TRAINING_RECORDED
   provenance; automatic champion promotion is strictly prevented.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from typing import Any

import numpy as np

from app.application.services.data_generator import DataGenerator
from app.application.services.distributed_fl_service import (
    DistributedFederatedTrainingManager,
    IsolatedReceiptSigningAuthority,
    IsolatedTrainingCompletionAuthority,
    run_production_federation,
)
from app.application.services.model_registry import ModelRegistry
from app.application.services.training_provenance_service import (
    FORBIDDEN_COORDINATOR_TOKENS,
    TrainingProvenanceConfigurationError,
    TrainingProvenanceSecurityError,
)
from app.config import get_settings

logger = logging.getLogger(__name__)


class WorkerEndpointRegistry:
    """Thread-safe in-memory registry of pre-provisioned bank worker communication endpoints.

    In production multi-institution topologies, bank worker daemons establish authenticated
    connections to the platform. Their IPC/network proxy endpoints are recorded here so the
    coordinator can dispatch training tasks without possessing private credentials or data paths.
    """

    _lock = threading.Lock()
    _endpoints: dict[str, dict[str, Any]] = {}
    _worker_pids: dict[str, int] = {}

    @classmethod
    def register_worker_endpoint(
        cls,
        bank_id: str,
        endpoint: dict[str, Any],
        pid: int | None = None,
    ) -> None:
        """Register a pre-provisioned bank worker endpoint."""
        with cls._lock:
            cls._endpoints[bank_id] = endpoint
            if pid is not None:
                cls._worker_pids[bank_id] = pid
            logger.info("Registered worker endpoint for bank [%s] (pid=%s)", bank_id, pid)

    @classmethod
    def get_worker_endpoint(cls, bank_id: str) -> dict[str, Any] | None:
        """Retrieve pre-provisioned endpoint for a specific bank."""
        with cls._lock:
            return cls._endpoints.get(bank_id)

    @classmethod
    def get_worker_endpoints(cls, bank_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Retrieve pre-provisioned endpoints for all requested banks."""
        with cls._lock:
            return {b: cls._endpoints[b] for b in bank_ids if b in cls._endpoints}

    @classmethod
    def get_worker_pids(cls, bank_ids: list[str]) -> dict[str, int]:
        """Retrieve recorded worker process IDs."""
        with cls._lock:
            return {b: cls._worker_pids[b] for b in bank_ids if b in cls._worker_pids}

    @classmethod
    def unregister_worker_endpoint(cls, bank_id: str) -> None:
        """Remove a worker endpoint upon disconnection."""
        with cls._lock:
            cls._endpoints.pop(bank_id, None)
            cls._worker_pids.pop(bank_id, None)

    @classmethod
    def clear(cls) -> None:
        """Clear all registered endpoints (useful for testing)."""
        with cls._lock:
            cls._endpoints.clear()
            cls._worker_pids.clear()


class OperationalFederationService:
    """Trusted orchestrator service for operational federated learning lifecycle execution.

    Coordinates server-side dependency resolution, verifies required authorities,
    invokes the canonical isolated runner, and returns truthful execution records.
    """

    def __init__(
        self,
        coordinator_token: str | None = None,
        db_url: str | None = None,
        signing_authority: Any | None = None,
        comp_authority: IsolatedTrainingCompletionAuthority | None = None,
        comp_service_endpoint: tuple[str, int] | None = None,
        worker_endpoints: dict[str, dict[str, Any]] | None = None,
        worker_pids: dict[str, int] | None = None,
        holdout_val_data: tuple[np.ndarray, np.ndarray] | None = None,
        registry_storage_dir: str | None = None,
        model_registry: ModelRegistry | None = None,
        app_env: str | None = None,
    ) -> None:
        self._coordinator_token = coordinator_token
        self._db_url = db_url
        self._signing_authority = signing_authority
        self._comp_authority = comp_authority
        self._comp_service_endpoint = comp_service_endpoint
        self._worker_endpoints = worker_endpoints
        self._worker_pids = worker_pids
        self._holdout_val_data = holdout_val_data
        self._registry_storage_dir = registry_storage_dir
        self._model_registry = model_registry
        self._app_env = app_env

    def resolve_coordinator_token(self, is_prod: bool) -> str:
        """Resolve authoritative coordinator token server-side."""
        settings = get_settings()
        token = (
            self._coordinator_token
            or os.environ.get("CFI_TRAINING_COORDINATOR_TOKEN")
            or getattr(settings, "training_coordinator_token", None)
            or ""
        ).strip()

        if is_prod:
            if not token or token in FORBIDDEN_COORDINATOR_TOKENS or "change_me" in token:
                raise TrainingProvenanceConfigurationError(
                    "Production training coordinator credential is not configured or uses an insecure placeholder "
                    "(CFI_TRAINING_COORDINATOR_TOKEN). Operational federation fails closed."
                )
        elif not token:
            token = f"coord_dev_token_{uuid.uuid4().hex[:16]}"

        return token

    def resolve_db_url(self, is_prod: bool) -> str:
        """Resolve authoritative training job authority database URL server-side."""
        settings = get_settings()
        db_url = (
            self._db_url
            or os.environ.get("CFI_TRAINING_JOB_DB_URL")
            or os.environ.get("CFI_TRAINING_JOB_STORE_PATH")
            or getattr(settings, "training_job_db_url", None)
            or getattr(settings, "training_job_store_path", None)
            or ""
        ).strip()

        if is_prod:
            if not db_url:
                raise TrainingProvenanceConfigurationError(
                    "Production training job authority database URL is not configured (CFI_TRAINING_JOB_DB_URL). "
                    "Operational federation fails closed."
                )
            if not (db_url.startswith("postgresql://") or db_url.startswith("postgres://")):
                raise TrainingProvenanceConfigurationError(
                    "Production training job authority database must be PostgreSQL (postgresql://...). "
                    "In-memory, SQLite, and filesystem stores are strictly forbidden in production."
                )
        return db_url

    def resolve_worker_endpoints(
        self,
        participating_banks: list[str],
        is_prod: bool,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
        """Resolve pre-provisioned bank worker endpoints without exposing credentials to coordinator."""
        if self._worker_endpoints is not None:
            endpoints = dict(self._worker_endpoints)
            pids = dict(self._worker_pids or {})
        else:
            endpoints = WorkerEndpointRegistry.get_worker_endpoints(participating_banks)
            pids = WorkerEndpointRegistry.get_worker_pids(participating_banks)

        if is_prod:
            missing = [b for b in participating_banks if b not in endpoints]
            if missing:
                raise TrainingProvenanceSecurityError(
                    f"Production bank worker endpoints are not provisioned or registered for: {sorted(missing)} (BI-04). "
                    "Coordinator cannot generate bank credentials or spawn workers with generated secrets."
                )
        return endpoints, pids

    def resolve_signing_authority(
        self,
        coordinator_token: str,
        db_url: str,
        app_env: str,
        is_prod: bool,
    ) -> Any:
        """Resolve or initialize isolated receipt signing authority."""
        if self._signing_authority is not None:
            return self._signing_authority

        if is_prod:
            issuer_key_path = os.environ.get("CFI_TRAINING_ISSUER_KEY_PATH")
            if not issuer_key_path or not os.path.isfile(issuer_key_path):
                raise TrainingProvenanceConfigurationError(
                    "Production isolated receipt signing authority requires provisioned issuer key path "
                    "(CFI_TRAINING_ISSUER_KEY_PATH). Operational federation fails closed."
                )
            return IsolatedReceiptSigningAuthority(
                issuer_key_path=issuer_key_path,
                db_url=db_url,
                coordinator_token=coordinator_token,
                app_env=app_env,
            )

        # In non-production (dev/test), if explicit key path or hex is available:
        key_hex = os.environ.get("CFI_TRAINING_ISSUER_KEY_HEX")
        key_path = os.environ.get("CFI_TRAINING_ISSUER_KEY_PATH")
        if key_hex or key_path:
            return IsolatedReceiptSigningAuthority(
                signing_key_hex=key_hex,
                issuer_key_path=key_path,
                db_url=db_url,
                coordinator_token=coordinator_token,
                app_env=app_env,
            )
        return None

    def resolve_completion_authority(
        self,
        coordinator_token: str,
        app_env: str,
        is_prod: bool,
    ) -> tuple[IsolatedTrainingCompletionAuthority | None, tuple[str, int] | None]:
        """Resolve isolated completion authority and its IPC/service endpoint."""
        if self._comp_service_endpoint is not None:
            return self._comp_authority, self._comp_service_endpoint

        if self._comp_authority is not None:
            return self._comp_authority, self._comp_authority.service_endpoint

        if is_prod:
            comp_host = os.environ.get("CFI_COMPLETION_SERVICE_HOST")
            comp_port = os.environ.get("CFI_COMPLETION_SERVICE_PORT")
            vault_path = os.environ.get("CFI_COMPLETION_VAULT_PATH")

            if comp_host and comp_port:
                endpoint = (comp_host, int(comp_port))
                return None, endpoint
            if vault_path and os.path.exists(vault_path):
                authority = IsolatedTrainingCompletionAuthority(
                    vault_path=vault_path,
                    coordinator_token=coordinator_token,
                    app_env=app_env,
                )
                return authority, authority.service_endpoint

            raise TrainingProvenanceConfigurationError(
                "Production isolated completion authority is not configured (requires CFI_COMPLETION_SERVICE_HOST/PORT "
                "or CFI_COMPLETION_VAULT_PATH). Operational federation fails closed."
            )

        return None, None

    def resolve_holdout_data(self, is_prod: bool) -> tuple[np.ndarray, np.ndarray]:
        """Resolve authoritative holdout evaluation dataset server-side."""
        if self._holdout_val_data is not None:
            return self._holdout_val_data

        settings = get_settings()
        holdout_path = os.environ.get("CFI_HOLDOUT_DATASET_PATH") or getattr(settings, "holdout_dataset_path", "")

        if holdout_path and os.path.exists(holdout_path):
            try:
                if holdout_path.endswith(".npz"):
                    data = np.load(holdout_path)
                    return data["X"], data["y"]
                elif holdout_path.endswith(".parquet") or holdout_path.endswith(".csv"):
                    import pandas as pd

                    df = pd.read_parquet(holdout_path) if holdout_path.endswith(".parquet") else pd.read_csv(holdout_path)
                    feat_cols = [c for c in df.columns if c.startswith("feat_")]
                    X = df[feat_cols].to_numpy(dtype=np.float32)
                    y = df["is_fraud"].to_numpy(dtype=np.float32)
                    return X, y
            except Exception as e:
                raise TrainingProvenanceConfigurationError(f"Failed to load holdout dataset from {holdout_path}: {e}") from e

        if is_prod:
            raise TrainingProvenanceConfigurationError(
                "Production holdout evaluation dataset is not configured (CFI_HOLDOUT_DATASET_PATH). "
                "Operational federation fails closed."
            )

        # Non-production synthetic holdout dataset
        gen = DataGenerator(seed=42)
        datasets = gen.generate_bank_datasets(bank_a_size=100, bank_b_size=50, bank_c_size=50)
        feats_df, labels = datasets["bank_a"]
        X = DataGenerator.encode_features(feats_df)
        y = labels.to_numpy(dtype=np.float32)
        return X, y

    def execute_operational_federation(
        self,
        job_id: str | None = None,
        simulation_id: str | None = None,
        participating_banks: list[str] | None = None,
        num_rounds: int = 2,
        expected_unique_samples: int | None = None,
    ) -> dict[str, Any]:
        """Execute operational federated learning lifecycle with complete authority verification."""
        settings = get_settings()
        active_env = (self._app_env or settings.app_env).strip()
        is_prod = active_env.lower() == "production"

        banks = list(participating_banks) if participating_banks else ["BANK_A", "BANK_B", "BANK_C"]
        clean_job_id = (job_id or f"job_fed_{uuid.uuid4().hex[:12]}").strip()
        clean_sim_id = (simulation_id or f"sim_{clean_job_id}").strip()
        training_run_id = f"run_{clean_job_id}"

        # 1. Resolve server-side authoritative dependencies
        coordinator_token = self.resolve_coordinator_token(is_prod)
        db_url = self.resolve_db_url(is_prod)
        worker_endpoints, worker_pids = self.resolve_worker_endpoints(banks, is_prod)
        signing_auth = self.resolve_signing_authority(coordinator_token, db_url, active_env, is_prod)
        comp_auth, comp_endpoint = self.resolve_completion_authority(coordinator_token, active_env, is_prod)
        holdout_data = self.resolve_holdout_data(is_prod)

        logger.info(
            "Executing operational federation: job_id=%s, simulation_id=%s, env=%s, banks=%s, rounds=%d",
            clean_job_id,
            clean_sim_id,
            active_env,
            banks,
            num_rounds,
        )

        # 2. Invoke canonical runner
        if is_prod:
            if comp_endpoint is None:
                raise TrainingProvenanceConfigurationError(
                    "Production federation requires a valid completion authority service endpoint."
                )
            result = run_production_federation(
                coordinator_token=coordinator_token,
                db_url=db_url,
                job_id=clean_job_id,
                training_run_id=training_run_id,
                simulation_id=clean_sim_id,
                holdout_val_data=holdout_data,
                participating_banks=banks,
                worker_endpoints=worker_endpoints,
                comp_service_endpoint=comp_endpoint,
                signing_authority=signing_auth,
                num_rounds=num_rounds,
                expected_unique_samples=expected_unique_samples,
                registry_storage_dir=self._registry_storage_dir,
                worker_pids=worker_pids,
                app_env=active_env,
            )
        else:
            # Non-production path: direct execution through manager
            manager = DistributedFederatedTrainingManager(
                coordinator_token=coordinator_token,
                db_url=db_url,
                app_env=active_env,
            )
            result = manager.execute_distributed_federation(
                job_id=clean_job_id,
                training_run_id=training_run_id,
                simulation_id=clean_sim_id,
                holdout_val_data=holdout_data,
                participating_banks=banks,
                worker_endpoints=worker_endpoints if worker_endpoints else None,
                signing_authority=signing_auth,
                completion_authority=comp_auth,
                num_rounds=num_rounds,
                expected_unique_samples=expected_unique_samples,
                registry_storage_dir=self._registry_storage_dir,
                model_registry=self._model_registry,
            )

        saved_version = result.get("saved_version")
        ver_number = saved_version.get("version") if isinstance(saved_version, dict) else None

        return {
            "job_id": clean_job_id,
            "training_run_id": training_run_id,
            "simulation_id": clean_sim_id,
            "status": result.get("status", "COMPLETED"),
            "is_verified": bool(result.get("is_verified", False)),
            "metrics": result.get("metrics", {}),
            "artifact_sha256": result.get("final_artifact_sha256", ""),
            "model_version": ver_number,
            "is_promoted_champion": bool(result.get("is_promoted_champion", False)),
            "coordinator_pid": result.get("coordinator_pid", 0),
            "worker_pids": result.get("worker_pids", {}),
            "saved_version": saved_version,
            "receipt": result.get("receipt"),
        }
