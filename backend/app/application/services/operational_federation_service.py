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
6. Process Ownership & Cleanup: Authorities spawned by this service are deterministically terminated in
   try/finally blocks without leaving orphaned background processes. Externally provisioned authorities
   are preserved.
7. Truthful Observability: Execution state, failures, and metrics are accurately tracked without fabricated
   success, dummy receipts, or synthetic fallbacks.
8. Model Registry Invariant: Saved candidate models are registered as inactive with TRAINING_RECORDED
   provenance; automatic champion promotion is strictly prevented.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from app.application.services.data_generator import FEATURE_NAMES, DataGenerator
from app.application.services.distributed_fl_service import (
    DistributedFederatedTrainingManager,
    IsolatedReceiptSigningAuthority,
    IsolatedTrainingCompletionAuthority,
    run_production_federation,
)
from app.application.services.holdout_provider import HoldoutDatasetProvider
from app.application.services.model_registry import ModelRegistry
from app.application.services.training_provenance_service import (
    FORBIDDEN_COORDINATOR_TOKENS,
    TrainingProvenanceConfigurationError,
    TrainingProvenanceSecurityError,
)
from app.config import get_settings

logger = logging.getLogger(__name__)


def _is_process_alive(pid: int | None) -> bool:
    """Check whether an operating system process with the given PID is actively running."""
    if pid is None or pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes

            # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000, STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            exit_code = ctypes.c_ulong()
            success = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            kernel32.CloseHandle(handle)
            return bool(success and exit_code.value == 259)
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


@dataclass(frozen=True)
class TrustedWorkerEndpoint:
    """Authenticated registration record for an independent bank worker node."""

    bank_id: str
    endpoint: dict[str, Any]
    pid: int | None
    registered_at: float
    capabilities: tuple[str, ...]


class WorkerEndpointRegistry:
    """Thread-safe registry of authenticated, provisioned bank worker communication endpoints.

    In production multi-institution topologies, independent bank worker daemons establish
    authenticated IPC/network proxies. Their endpoints are recorded here so the coordinator
    can dispatch training rounds without possessing bank-private credentials or data paths.
    """

    _lock = threading.Lock()
    _registry: dict[str, TrustedWorkerEndpoint] = {}

    @classmethod
    def register_worker_endpoint(
        cls,
        bank_id: str,
        endpoint: dict[str, Any],
        pid: int | None = None,
    ) -> None:
        """Register a provisioned bank worker endpoint with capability and liveness verification."""
        if not isinstance(bank_id, str) or not bank_id.strip():
            raise TrainingProvenanceConfigurationError("Bank identifier must be a non-empty string.")

        clean_id = bank_id.strip().upper()

        if not isinstance(endpoint, dict):
            raise TrainingProvenanceConfigurationError(f"Endpoint for bank [{clean_id}] must be a dictionary.")

        inbox = endpoint.get("inbox")
        outbox = endpoint.get("outbox")
        if not inbox or not hasattr(inbox, "put"):
            raise TrainingProvenanceConfigurationError(
                f"Endpoint for bank [{clean_id}] lacks a valid 'inbox' with callable .put() method."
            )
        if not outbox or not hasattr(outbox, "get"):
            raise TrainingProvenanceConfigurationError(
                f"Endpoint for bank [{clean_id}] lacks a valid 'outbox' with callable .get() method."
            )

        # Liveness check if PID is declared
        if pid is not None and not _is_process_alive(pid):
            raise TrainingProvenanceSecurityError(
                f"Cannot register dead/terminated worker process PID {pid} for bank [{clean_id}]."
            )

        caps = ("inbox", "outbox")
        record = TrustedWorkerEndpoint(
            bank_id=clean_id,
            endpoint=endpoint,
            pid=pid,
            registered_at=time.time(),
            capabilities=caps,
        )

        with cls._lock:
            cls._registry[clean_id] = record
            logger.info("Registered trusted worker endpoint for bank [%s] (pid=%s)", clean_id, pid)

    @classmethod
    def get_worker_endpoint(cls, bank_id: str) -> dict[str, Any] | None:
        """Retrieve pre-provisioned endpoint for a bank, evicting stale/dead registrations."""
        clean_id = bank_id.strip().upper()
        with cls._lock:
            rec = cls._registry.get(clean_id)
            if rec is None:
                return None

            # Re-verify process liveness if PID is tracked
            if rec.pid is not None and not _is_process_alive(rec.pid):
                logger.warning(
                    "Worker process PID %d for bank [%s] is dead. Evicting stale registration.",
                    rec.pid,
                    clean_id,
                )
                cls._registry.pop(clean_id, None)
                return None

            return rec.endpoint

    @classmethod
    def is_registered(cls, bank_id: str) -> bool:
        """Check if a bank worker is currently registered and alive."""
        return cls.get_worker_endpoint(bank_id) is not None

    @classmethod
    def get_trusted_endpoint(cls, bank_id: str) -> TrustedWorkerEndpoint | None:
        """Retrieve authoritative TrustedWorkerEndpoint record, evicting if stale/dead."""
        clean_id = bank_id.strip().upper()
        with cls._lock:
            rec = cls._registry.get(clean_id)
            if rec is None:
                return None
            if rec.pid is not None and not _is_process_alive(rec.pid):
                cls._registry.pop(clean_id, None)
                return None
            return rec

    @classmethod
    def get_worker_endpoints(cls, bank_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Retrieve pre-provisioned endpoints for all requested banks, ignoring stale/dead workers."""
        res: dict[str, dict[str, Any]] = {}
        for b in bank_ids:
            ep = cls.get_worker_endpoint(b)
            if ep is not None:
                res[b.strip().upper()] = ep
        return res

    @classmethod
    def get_worker_pids(cls, bank_ids: list[str]) -> dict[str, int]:
        """Retrieve recorded active worker process IDs."""
        with cls._lock:
            res: dict[str, int] = {}
            for b in bank_ids:
                clean_id = b.strip().upper()
                rec = cls._registry.get(clean_id)
                if rec and rec.pid is not None and _is_process_alive(rec.pid):
                    res[clean_id] = rec.pid
            return res

    @classmethod
    def unregister_worker_endpoint(cls, bank_id: str) -> None:
        """Remove a worker endpoint upon explicit shutdown or disconnection."""
        clean_id = bank_id.strip().upper()
        with cls._lock:
            cls._registry.pop(clean_id, None)

    @classmethod
    def clear(cls) -> None:
        """Clear all registered endpoints (useful for test isolation)."""
        with cls._lock:
            cls._registry.clear()


class OperationalFederationService:
    """Trusted orchestrator service for operational federated learning lifecycle execution.

    Coordinates server-side dependency resolution, verifies required authorities,
    invokes the canonical isolated runner, owns process cleanup, and returns truthful records.
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
        clean_banks = [b.strip().upper() for b in participating_banks]
        if self._worker_endpoints is not None:
            endpoints = {b: self._worker_endpoints[b] for b in clean_banks if b in self._worker_endpoints}
            pids = {b: self._worker_pids[b] for b in clean_banks if self._worker_pids and b in self._worker_pids}
        else:
            endpoints = WorkerEndpointRegistry.get_worker_endpoints(clean_banks)
            pids = WorkerEndpointRegistry.get_worker_pids(clean_banks)

        if is_prod:
            missing = [b for b in clean_banks if b not in endpoints]
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
    ) -> tuple[Any, bool]:
        """Resolve or initialize isolated receipt signing authority.

        Returns (authority_instance, was_spawned_by_service).
        """
        if self._signing_authority is not None:
            return self._signing_authority, False

        if is_prod:
            issuer_key_path = os.environ.get("CFI_TRAINING_ISSUER_KEY_PATH")
            if not issuer_key_path or not os.path.isfile(issuer_key_path):
                raise TrainingProvenanceConfigurationError(
                    "Production isolated receipt signing authority requires provisioned issuer key path "
                    "(CFI_TRAINING_ISSUER_KEY_PATH). Operational federation fails closed."
                )
            auth = IsolatedReceiptSigningAuthority(
                issuer_key_path=issuer_key_path,
                db_url=db_url,
                coordinator_token=coordinator_token,
                app_env=app_env,
            )
            return auth, True

        # In non-production (dev/test), if explicit key path or hex is available:
        key_hex = os.environ.get("CFI_TRAINING_ISSUER_KEY_HEX")
        key_path = os.environ.get("CFI_TRAINING_ISSUER_KEY_PATH")
        if key_hex or key_path:
            auth = IsolatedReceiptSigningAuthority(
                signing_key_hex=key_hex,
                issuer_key_path=key_path,
                db_url=db_url,
                coordinator_token=coordinator_token,
                app_env=app_env,
            )
            return auth, True
        return None, False

    def resolve_completion_authority(
        self,
        coordinator_token: str,
        app_env: str,
        is_prod: bool,
    ) -> tuple[IsolatedTrainingCompletionAuthority | None, tuple[str, int] | None, bool]:
        """Resolve isolated completion authority and its IPC/service endpoint.

        Returns (authority_instance_or_none, endpoint_or_none, was_spawned_by_service).
        """
        if self._comp_service_endpoint is not None:
            return self._comp_authority, self._comp_service_endpoint, False

        if self._comp_authority is not None:
            return self._comp_authority, self._comp_authority.service_endpoint, False

        if is_prod:
            comp_host = os.environ.get("CFI_COMPLETION_SERVICE_HOST")
            comp_port = os.environ.get("CFI_COMPLETION_SERVICE_PORT")
            vault_path = os.environ.get("CFI_COMPLETION_VAULT_PATH")

            if comp_host and comp_port:
                endpoint = (comp_host, int(comp_port))
                return None, endpoint, False
            if vault_path and os.path.exists(vault_path):
                authority = IsolatedTrainingCompletionAuthority(
                    vault_path=vault_path,
                    coordinator_token=coordinator_token,
                    app_env=app_env,
                )
                return authority, authority.service_endpoint, True

            raise TrainingProvenanceConfigurationError(
                "Production isolated completion authority is not configured (requires CFI_COMPLETION_SERVICE_HOST/PORT "
                "or CFI_COMPLETION_VAULT_PATH). Operational federation fails closed."
            )

        return None, None, False

    def resolve_holdout_data(
        self,
        is_prod: bool,
        holdout_data: Any = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Resolve authoritative holdout evaluation dataset server-side.

        Enforces strict validation:
        - Exact feature dimensionality (len(FEATURE_NAMES))
        - Exact feature ordering according to FEATURE_NAMES
        - Reuses DataGenerator.encode_features when raw transaction columns are provided
        - Binary target validation ({0, 1} with at least 2 classes)
        - Non-empty, finite arrays (no NaN or Inf)
        - Zero synthetic fallback in production (fails closed)
        """
        data_source = holdout_data if holdout_data is not None else self._holdout_val_data
        if data_source is not None:
            if isinstance(data_source, tuple) and len(data_source) == 2:
                X = np.asarray(data_source[0], dtype=np.float32)
                y = np.asarray(data_source[1], dtype=np.float32)
                self._validate_holdout_arrays(X, y)
                return X, y
            elif hasattr(data_source, "columns"):  # pandas DataFrame
                df = data_source
                if df.empty:
                    raise TrainingProvenanceConfigurationError("Holdout dataset is empty.")
                target_col = None
                for cand in ("is_fraud", "label", "target", "class", "fraud"):
                    if cand in df.columns:
                        target_col = cand
                        break
                if not target_col:
                    raise TrainingProvenanceConfigurationError("Holdout dataset missing binary target column ('is_fraud').")
                y = df[target_col].to_numpy(dtype=np.float32)
                has_raw_features = all(f in df.columns for f in FEATURE_NAMES)
                if has_raw_features:
                    X = DataGenerator.encode_features(df[list(FEATURE_NAMES)])
                else:
                    feat_df = df.drop(columns=[target_col])
                    X = feat_df.to_numpy(dtype=np.float32)
                self._validate_holdout_arrays(X, y)
                return X, y
            else:
                raise TrainingProvenanceConfigurationError(f"Unsupported holdout data type: {type(data_source)}")

        settings = get_settings()
        manifest_path = os.environ.get("CFI_HOLDOUT_MANIFEST_PATH") or getattr(settings, "holdout_manifest_path", "")
        holdout_path = os.environ.get("CFI_HOLDOUT_DATASET_PATH") or getattr(settings, "holdout_dataset_path", "")

        # 1. Manifest-backed resolution via HoldoutDatasetProvider
        if manifest_path and os.path.exists(manifest_path):
            try:
                provider = HoldoutDatasetProvider(manifest_path=manifest_path, auto_load=True)
                holdout_ds = provider.current_holdout
                if holdout_ds is not None:
                    X = np.asarray(holdout_ds.features, dtype=np.float32)
                    y = np.asarray(holdout_ds.labels, dtype=np.float32)
                    self._validate_holdout_arrays(X, y)
                    return X, y
            except Exception as e:
                raise TrainingProvenanceConfigurationError(f"Failed to load holdout from manifest '{manifest_path}': {e}") from e

        # 2. Direct data file resolution
        if holdout_path and os.path.exists(holdout_path):
            try:
                if holdout_path.endswith(".npz"):
                    data = np.load(holdout_path)
                    feat_key = "features" if "features" in data else ("X" if "X" in data else None)
                    lbl_key = "labels" if "labels" in data else ("y" if "y" in data else None)
                    if not feat_key or not lbl_key:
                        raise TrainingProvenanceConfigurationError(
                            f"NPZ holdout file must contain features and labels arrays: {list(data.keys())}"
                        )
                    X = np.asarray(data[feat_key], dtype=np.float32)
                    y = np.asarray(data[lbl_key], dtype=np.float32)
                    self._validate_holdout_arrays(X, y)
                    return X, y

                if holdout_path.endswith(".parquet") or holdout_path.endswith(".pq") or holdout_path.endswith(".csv") or holdout_path.endswith(".txt"):
                    df = pd.read_parquet(holdout_path) if (holdout_path.endswith(".parquet") or holdout_path.endswith(".pq")) else pd.read_csv(holdout_path)
                    if df.empty:
                        raise TrainingProvenanceConfigurationError("Holdout dataset is empty.")

                    # Identify target column
                    target_col = None
                    for cand in ("is_fraud", "label", "target", "class", "fraud"):
                        if cand in df.columns:
                            target_col = cand
                            break
                    if not target_col:
                        raise TrainingProvenanceConfigurationError("Holdout dataset missing binary target column ('is_fraud').")

                    y = df[target_col].to_numpy(dtype=np.float32)

                    # Check if raw transaction schema is present
                    has_raw_features = all(f in df.columns for f in FEATURE_NAMES)
                    if has_raw_features:
                        # Canonical preprocessing / encoding
                        X = DataGenerator.encode_features(df[list(FEATURE_NAMES)])
                    else:
                        feat_df = df.drop(columns=[target_col])
                        try:
                            X = feat_df.to_numpy(dtype=np.float32)
                        except Exception as parse_err:
                            raise TrainingProvenanceConfigurationError(
                                f"Holdout features contain unencoded non-numeric data: {parse_err}"
                            ) from parse_err

                    self._validate_holdout_arrays(X, y)
                    return X, y
            except Exception as e:
                raise TrainingProvenanceConfigurationError(f"Failed to load holdout dataset from '{holdout_path}': {e}") from e

        if is_prod:
            raise TrainingProvenanceConfigurationError(
                "Production holdout evaluation dataset is not configured (CFI_HOLDOUT_DATASET_PATH or "
                "CFI_HOLDOUT_MANIFEST_PATH). Operational federation fails closed without synthetic fallback."
            )

        # 3. Non-production synthetic holdout dataset (deterministic)
        gen = DataGenerator(seed=42)
        datasets = gen.generate_bank_datasets(bank_a_size=500, bank_b_size=200, bank_c_size=200)
        feats_df, labels = datasets["bank_a"]
        X = DataGenerator.encode_features(feats_df)
        y = labels.to_numpy(dtype=np.float32)
        self._validate_holdout_arrays(X, y)
        return X, y

    @staticmethod
    def _validate_holdout_arrays(X: np.ndarray, y: np.ndarray) -> None:
        """Verify strict dimensional, finite, and binary label invariants on holdout arrays."""
        if not isinstance(X, np.ndarray) or not isinstance(y, np.ndarray):
            raise TrainingProvenanceConfigurationError("Holdout features and labels must be numpy arrays.")

        if len(X) == 0 or len(y) == 0:
            raise TrainingProvenanceConfigurationError("Holdout evaluation dataset is empty.")

        if len(X) != len(y):
            raise TrainingProvenanceConfigurationError(
                f"Holdout feature count ({len(X)}) does not match label count ({len(y)})."
            )

        if X.ndim != 2 or X.shape[1] != len(FEATURE_NAMES):
            raise TrainingProvenanceConfigurationError(
                f"Holdout feature dimensionality ({X.shape[1] if X.ndim > 1 else 0}) does not match "
                f"required canonical model dimension ({len(FEATURE_NAMES)})."
            )

        if not np.all(np.isfinite(X)):
            raise TrainingProvenanceConfigurationError("Holdout dataset contains non-finite feature values (NaN/Inf).")

        if not np.all(np.isfinite(y)):
            raise TrainingProvenanceConfigurationError("Holdout dataset contains non-finite target label values.")

        unique_labels = np.unique(y)
        if not set(unique_labels).issubset({0.0, 1.0}):
            raise TrainingProvenanceConfigurationError(
                f"Holdout target labels must be binary values in {{0, 1}}. Found: {unique_labels.tolist()}"
            )

        if len(unique_labels) < 2:
            raise TrainingProvenanceConfigurationError(
                f"Holdout dataset must contain both positive and negative classes for evaluation (found: {unique_labels.tolist()})."
            )

    def execute_operational_federation(
        self,
        job_id: str | None = None,
        simulation_id: str | None = None,
        participating_banks: list[str] | None = None,
        num_rounds: int = 2,
        expected_unique_samples: int | None = None,
    ) -> dict[str, Any]:
        """Execute operational federated learning lifecycle with complete authority verification and lifecycle ownership."""
        settings = get_settings()
        active_env = (self._app_env or settings.app_env).strip()
        is_prod = active_env.lower() == "production"

        banks = [b.strip().upper() for b in participating_banks] if participating_banks else ["BANK_A", "BANK_B", "BANK_C"]
        clean_job_id = (job_id or f"job_fed_{uuid.uuid4().hex[:12]}").strip()
        clean_sim_id = (simulation_id or f"sim_{clean_job_id}").strip()
        training_run_id = f"run_{clean_job_id}"

        # 1. Resolve server-side authoritative dependencies
        coordinator_token = self.resolve_coordinator_token(is_prod)
        db_url = self.resolve_db_url(is_prod)
        worker_endpoints, worker_pids = self.resolve_worker_endpoints(banks, is_prod)
        signing_auth, spawned_signing = self.resolve_signing_authority(coordinator_token, db_url, active_env, is_prod)
        comp_auth, comp_endpoint, spawned_comp = self.resolve_completion_authority(coordinator_token, active_env, is_prod)
        holdout_data = self.resolve_holdout_data(is_prod)

        logger.info(
            "Executing operational federation: job_id=%s, simulation_id=%s, env=%s, banks=%s, rounds=%d",
            clean_job_id,
            clean_sim_id,
            active_env,
            banks,
            num_rounds,
        )

        try:
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

            # 3. Truthful execution status and evidence validation
            raw_status = result.get("status")
            is_verified = bool(result.get("is_verified", False))

            if raw_status != "COMPLETED" or not is_verified:
                raise TrainingProvenanceSecurityError(
                    f"Operational federation failed to achieve verified completion. "
                    f"Status: '{raw_status}', is_verified: {is_verified}"
                )

            saved_version = result.get("saved_version")
            ver_number = saved_version.get("version") if isinstance(saved_version, dict) else None

            return {
                "job_id": clean_job_id,
                "training_run_id": training_run_id,
                "simulation_id": clean_sim_id,
                "status": raw_status,
                "is_verified": is_verified,
                "metrics": result.get("metrics") or {},
                "artifact_sha256": result.get("final_artifact_sha256") or result.get("artifact_sha256") or "",
                "model_version": ver_number,
                "is_promoted_champion": bool(result.get("is_promoted_champion", False)),
                "coordinator_pid": result.get("coordinator_pid", 0),
                "worker_pids": result.get("worker_pids") or {},
                "saved_version": saved_version,
                "receipt": result.get("receipt"),
            }

        finally:
            # Deterministic process lifecycle cleanup: terminate authorities spawned by this invocation
            if spawned_signing and signing_auth is not None:
                with contextlib.suppress(Exception):
                    signing_auth.terminate()
                    logger.info("Cleaned up isolated signing authority process")

            if spawned_comp and comp_auth is not None:
                with contextlib.suppress(Exception):
                    comp_auth.terminate()
                    logger.info("Cleaned up isolated completion authority process")
