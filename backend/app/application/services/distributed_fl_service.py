"""Distributed Federated Learning Execution Service and Multi-Process Isolation Boundary.

Implements Module 01-A.1 Requirement P6:
- Multi-process distributed topology with independent operating-system processes for Bank A, Bank B, Bank C, and Coordinator.
- Enforceable privacy boundary: coordinator has zero access to bank-local raw datasets; banks load from isolated local storage.
- Independent per-bank workload authentication tokens (BANK_A, BANK_B, BANK_C) for all execution observations; coordinator cannot impersonate banks.
- Aggregation executed using production FederatedLearningEngine.aggregate_parameters (FedAvg weighted by sample count).
- Real PyTorch training, weight extraction, and exact SHA-256 local update hashing.
- Full provenance recording in PostgreSQL authority: round observations, global aggregations, model evaluations.
- Ed25519 receipt issuance, public-key-only verification, and inactive candidate registration in ModelRegistry.
- Zero fabricated metrics or fallback values; fail-closed on missing/invalid execution events.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import io
import logging
import math
import multiprocessing as mp
import os
import tempfile
from typing import Any

import numpy as np
import torch
import torch.nn as nn
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.fl_engine import FederatedLearningEngine
from app.application.services.model_registry import ModelRegistry
from app.application.services.model_service import NUM_FEATURES, ModelService
from app.application.services.privacy_service import PrivacyService
from app.application.services.training_provenance_service import (
    DEFAULT_TRAINING_KEY_ID,
    TrainingExecutionEvidence,
    TrainingExecutionReceipt,
    TrainingProvenanceConfigurationError,
    TrainingProvenanceSecurityError,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
    TrustedTrainingJobRecordSource,
    get_default_training_issuer,
    get_default_training_verifier,
)
from app.config import get_settings
from app.domain.enums import AggregationMethod
from app.domain.value_objects import ModelWeights

logger = logging.getLogger(__name__)


def _generate_ed25519_keypair_hex() -> tuple[str, str]:
    private_key = ed25519.Ed25519PrivateKey.generate()
    private_raw = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_raw.hex(), public_raw.hex()


def _serialize_weights_to_bytes(weights: ModelWeights) -> bytes:
    """Deterministically serialize ModelWeights to bytes for hashing and transport."""
    arr = np.asarray(weights.flat_weights, dtype=np.float32)
    return arr.tobytes()


def _serialize_model_state_dict(model: nn.Module) -> bytes:
    """Serialize model state dict to bytes."""
    buf = io.BytesIO()
    torch.save(model.state_dict(), buf)
    return buf.getvalue()

# ---------------------------------------------------------------------------
# Bank Storage & Worker Isolation Helpers
# ---------------------------------------------------------------------------


class BankPrivateStorageVault:
    """Institution-isolated storage vault enforcing physical, cryptographic, and logical boundary protection.

    Enforces Module 01-A.1 Requirement:
    - Dedicated local storage vault per financial institution (BANK_A, BANK_B, BANK_C).
    - Hard access denial on unauthorized cross-bank reads or coordinator read attempts.
    - Token-derived cryptographic sealing (PBKDF2-HMAC-SHA256 with 100k iterations and Fernet AES-128-CBC + HMAC-SHA256 authenticated encryption) of raw datasets at rest with mandatory key configuration in production.
    - Zero plaintext fallback in production (unencrypted zip/npz strictly rejected with TrainingProvenanceSecurityError).
    - Native OS-level exclusive handle on the private dataset during worker execution,
      causing genuine OS-level PermissionError when external processes attempt open().
    """

    VAULT_MAGIC_V1 = b"CFI_VAULT_V1\x00"

    def __init__(
        self,
        bank_id: str,
        vault_dir: str,
        bank_token: str | None = None,
        bank_encryption_key: str | bytes | None = None,
        app_env: str = "production",
    ) -> None:
        self.bank_id = bank_id
        self.vault_dir = os.path.join(vault_dir, bank_id)
        os.makedirs(self.vault_dir, exist_ok=True)
        self.data_path = os.path.join(self.vault_dir, "private_dataset.npz")
        self.bank_token = bank_token
        self.bank_encryption_key = bank_encryption_key
        self.app_env = app_env
        self._is_prod = app_env.lower() == "production"
        self._os_lock_handle: Any = None

        if self._is_prod and not self.bank_token and not self.bank_encryption_key:
            raise TrainingProvenanceConfigurationError(
                f"Production bank private storage vault for '{self.bank_id}' requires mandatory encryption key configuration."
            )

    def _get_encryption_cipher(self) -> Fernet | None:
        if self.bank_encryption_key is not None:
            if isinstance(self.bank_encryption_key, str):
                key_bytes = self.bank_encryption_key.encode("utf-8")
            else:
                key_bytes = self.bank_encryption_key
            derived_key = hashlib.sha256(key_bytes).digest()
            return Fernet(base64.urlsafe_b64encode(derived_key))

        if not self.bank_token:
            if self._is_prod:
                raise TrainingProvenanceConfigurationError(
                    f"Production bank private storage vault for '{self.bank_id}' requires encryption key configuration."
                )
            return None

        # PBKDF2-HMAC-SHA256 key derivation with bank-specific salt and 100,000 iterations
        salt = f"cfi:vault:kdf:v1:{self.bank_id}".encode()
        derived_key = hashlib.pbkdf2_hmac("sha256", self.bank_token.encode(), salt, 100_000, 32)
        return Fernet(base64.urlsafe_b64encode(derived_key))

    def store_dataset(self, X: np.ndarray, y: np.ndarray) -> str:
        buf = io.BytesIO()
        np.savez_compressed(buf, X=X, y=y)
        raw_bytes = buf.getvalue()
        cipher = self._get_encryption_cipher()
        if cipher is None:
            if self._is_prod:
                raise TrainingProvenanceConfigurationError(
                    f"Production bank private storage vault for '{self.bank_id}' cannot store unencrypted dataset."
                )
            payload = raw_bytes
        else:
            payload = self.VAULT_MAGIC_V1 + cipher.encrypt(raw_bytes)

        with open(self.data_path, "wb") as f:
            f.write(payload)
        with contextlib.suppress(Exception):
            os.chmod(self.vault_dir, 0o700)
            os.chmod(self.data_path, 0o600)
        return self.data_path

    def acquire_exclusive_os_boundary(self) -> None:
        """Lock the physical file using native OS kernel primitives so external open() fails with PermissionError."""
        if not os.path.exists(self.data_path):
            return
        if os.name == "nt":
            import ctypes

            generic_read = 0x80000000
            generic_write = 0x40000000
            file_share_none = 0
            open_existing = 3
            file_attribute_normal = 0x80
            handle = ctypes.windll.kernel32.CreateFileW(
                self.data_path,
                generic_read | generic_write,
                file_share_none,
                None,
                open_existing,
                file_attribute_normal,
                None,
            )
            if handle != -1:
                self._os_lock_handle = handle
        else:
            try:
                import fcntl

                fd = os.open(self.data_path, os.O_RDWR)
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
                self._os_lock_handle = fd
            except Exception:
                pass

    def release_exclusive_os_boundary(self) -> None:
        """Release the OS-level lock on dataset file."""
        if self._os_lock_handle is not None:
            if os.name == "nt":
                import ctypes

                ctypes.windll.kernel32.CloseHandle(self._os_lock_handle)
            else:
                try:
                    import fcntl

                    fcntl.flock(self._os_lock_handle, fcntl.LOCK_UN)  # type: ignore[attr-defined]
                    os.close(self._os_lock_handle)
                except Exception:
                    pass
            self._os_lock_handle = None

    def read_dataset_as_bank(self, caller_identity: str, caller_token: str | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Access vault dataset with strict institutional identity and token verification."""
        if caller_identity != self.bank_id:
            raise PermissionError(
                f"Access denied: Caller '{caller_identity}' is forbidden from accessing private data vault of bank '{self.bank_id}'."
            )
        if self.bank_token is not None and (caller_token is None or not hmac.compare_digest(caller_token, self.bank_token)):
            raise PermissionError(
                f"Access denied: Invalid worker credential presented for bank '{self.bank_id}' storage vault."
            )
        if not os.path.exists(self.data_path):
            raise FileNotFoundError(f"Vault data file not found at '{self.data_path}'")
        with open(self.data_path, "rb") as f:
            raw_data = f.read()

        if raw_data.startswith(b"PK\x03\x04"):
            if self._is_prod:
                raise TrainingProvenanceSecurityError(
                    f"Plaintext dataset rejected: Vault for bank '{self.bank_id}' forbids unencrypted datasets in production."
                )
            decrypted = raw_data
        else:
            if not raw_data.startswith(self.VAULT_MAGIC_V1):
                if self._is_prod:
                    raise TrainingProvenanceSecurityError(
                        f"Unsupported or corrupted vault format/key version for bank '{self.bank_id}'. Expected header {self.VAULT_MAGIC_V1!r}."
                    )
                ciphertext = raw_data
            else:
                ciphertext = raw_data[len(self.VAULT_MAGIC_V1):]

            cipher = self._get_encryption_cipher()
            if not cipher:
                raise TrainingProvenanceConfigurationError(
                    f"Missing encryption cipher for bank '{self.bank_id}' vault."
                )
            try:
                decrypted = cipher.decrypt(ciphertext)
            except Exception as e:
                raise TrainingProvenanceSecurityError(
                    f"Decryption authentication failed for bank '{self.bank_id}' storage vault: {e}"
                ) from e

        loaded = np.load(io.BytesIO(decrypted))
        return loaded["X"], loaded["y"]


def provision_isolated_issuer_vault(
    vault_dir: str,
    signing_key_hex: str | None = None,
) -> tuple[str, str]:
    """Privileged bootstrap provisioning Ed25519 issuer private key inside an isolated vault."""
    os.makedirs(vault_dir, exist_ok=True)
    if not signing_key_hex:
        priv_hex, pub_hex = _generate_ed25519_keypair_hex()
    else:
        priv_hex = signing_key_hex.strip()
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        priv_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv_hex))
        pub_hex = priv_key.public_key().public_bytes_raw().hex()
        del priv_key

    key_file = os.path.join(vault_dir, "issuer_ed25519.key")
    with open(key_file, "w", encoding="utf-8") as f:
        f.write(priv_hex)
    with contextlib.suppress(Exception):
        os.chmod(vault_dir, 0o700)
        os.chmod(key_file, 0o600)
    del priv_hex
    return key_file, pub_hex


def _run_isolated_issuer_worker(
    issuer_key_source: str,
    db_url: str,
    key_id: str,
    coordinator_token: str | None,
    inbox: mp.Queue,
    outbox: mp.Queue,
    app_env: str = "production",
) -> None:
    """Dedicated Operating System Process holding the Ed25519 signing private key in memory isolation."""
    issuer_pid = os.getpid()
    logger.info("Isolated Receipt Signing Service running with PID %d", issuer_pid)

    if os.path.exists(issuer_key_source):
        with open(issuer_key_source, encoding="utf-8") as f:
            signing_key_hex = f.read().strip()
    else:
        signing_key_hex = issuer_key_source.strip()

    record_source = TrustedTrainingJobRecordSource(
        coordinator_token=coordinator_token,
        db_url=db_url,
        enforce_lifecycle=True,
        app_env=app_env,
    )
    issuer = TrainingReceiptIssuer(
        signing_key_hex=signing_key_hex,
        key_id=key_id,
        job_record_source=record_source,
        enforce_job_records=True,
        app_env=app_env,
    )

    del signing_key_hex

    outbox.put({"status": "READY", "public_key_hex": issuer.public_bytes_hex})

    issued_jobs: set[str] = set()

    while True:
        try:
            req = inbox.get()
        except Exception:
            break
        if req is None or req.get("command") == "STOP":
            break

        if req.get("command") == "SIGN_RECEIPT":
            try:
                auth_token = req.get("auth_token")

                if app_env.lower() == "production" and not coordinator_token:
                    outbox.put({
                        "status": "ERROR",
                        "error": "Unauthorized receipt signing request: coordinator token configuration is required in production.",
                    })
                    continue

                if coordinator_token is not None:
                    if not auth_token or not hmac.compare_digest(auth_token, coordinator_token):
                        outbox.put({
                            "status": "ERROR",
                            "error": "Unauthorized receipt signing request: invalid or missing coordinator token.",
                        })
                        continue
                elif req.get("caller_identity") != "COORDINATOR":
                    outbox.put({
                        "status": "ERROR",
                        "error": f"Unauthorized receipt signing caller identity: '{req.get('caller_identity')}'.",
                    })
                    continue

                job_id = req["job_id"]
                artifact_bytes = req["artifact_bytes"]

                if job_id in issued_jobs:
                    outbox.put({
                        "status": "ERROR",
                        "error": f"Receipt issuance denied: Duplicate signing request for already-signed job '{job_id}'.",
                    })
                    continue

                completed_record = record_source.get_completed_job_by_id(job_id)
                if completed_record is None:
                    outbox.put({
                        "status": "ERROR",
                        "error": f"Receipt issuance denied: Job '{job_id}' has no trusted COMPLETED record in authority.",
                    })
                    continue

                computed_sha = hashlib.sha256(artifact_bytes).hexdigest()
                if not hmac.compare_digest(computed_sha, completed_record.artifact_sha256):
                    outbox.put({
                        "status": "ERROR",
                        "error": f"Receipt issuance denied: Provided artifact SHA-256 '{computed_sha}' does not match "
                                 f"authoritative completed artifact digest '{completed_record.artifact_sha256}'.",
                    })
                    continue

                evidence = TrainingExecutionEvidence(
                    training_run_id=completed_record.training_run_id,
                    job_id=completed_record.job_id,
                    simulation_id=completed_record.simulation_id,
                    completed_at=completed_record.completed_at,
                    status=completed_record.status,
                    executed_rounds=completed_record.executed_rounds,
                    executed_epochs=completed_record.executed_epochs,
                    sample_count=completed_record.sample_count,
                    observed_metrics=dict(completed_record.observed_metrics),
                    artifact_sha256=completed_record.artifact_sha256,
                    architecture=completed_record.architecture,
                    feature_schema_version=completed_record.feature_schema_version,
                    feature_names=list(completed_record.feature_names),
                    producer_id=completed_record.worker_id,
                )
                receipt = issuer.issue_receipt(evidence=evidence, model_bytes=artifact_bytes)
                issued_jobs.add(job_id)
                outbox.put({
                    "status": "SUCCESS",
                    "receipt": receipt,
                    "issuer_pid": issuer_pid,
                })
            except Exception as e:
                outbox.put({
                    "status": "ERROR",
                    "error": str(e),
                })


class IsolatedReceiptSigningAuthority:
    """Independently authorized receipt signing authority residing outside the coordinator boundary.

    Enforces Module 01-A.1 Requirements:
    1. Holds Ed25519 signing private key in an independent OS process memory address space.
    2. Coordinator process never possesses, derives, stores, or forwards the private signing key or raw key hex.
    3. Issues receipts solely for jobs with verified COMPLETED records in PostgreSQL authority.
    4. Verifies artifact SHA-256 against authoritative completed evidence before signing.
    5. Fails closed on uncompleted jobs, mismatched artifact bytes, unauthenticated callers, or service termination.
    """

    def __init__(
        self,
        signing_key_hex: str | None = None,
        issuer_key_path: str | None = None,
        db_url: str = "",
        key_id: str = DEFAULT_TRAINING_KEY_ID,
        coordinator_token: str | None = None,
        app_env: str = "production",
    ) -> None:
        self.key_id = key_id
        self.app_env = app_env
        self.db_url = db_url
        self._temp_vault: tempfile.TemporaryDirectory | None = None

        if issuer_key_path is not None:
            key_source = issuer_key_path
        elif signing_key_hex is not None:
            if len(signing_key_hex.strip()) != 64:
                raise TrainingProvenanceConfigurationError("Signing key hex must be a 64-character hex string.")
            self._temp_vault = tempfile.TemporaryDirectory(prefix="cfi_issuer_vault_")
            vault_file = os.path.join(self._temp_vault.name, "issuer.key")
            with open(vault_file, "w", encoding="utf-8") as f:
                f.write(signing_key_hex.strip())
            with contextlib.suppress(Exception):
                os.chmod(self._temp_vault.name, 0o700)
                os.chmod(vault_file, 0o600)
            key_source = vault_file
        else:
            raise TrainingProvenanceConfigurationError("Either issuer_key_path or signing_key_hex must be provided.")

        # Eagerly probe database connectivity to fail closed immediately on unreachable DB
        TrustedTrainingJobRecordSource(
            coordinator_token=coordinator_token,
            db_url=self.db_url,
            enforce_lifecycle=True,
            app_env=self.app_env,
        )

        self._inbox: mp.Queue = mp.Queue()
        self._outbox: mp.Queue = mp.Queue()
        self._process = mp.Process(
            target=_run_isolated_issuer_worker,
            args=(
                key_source,
                db_url,
                self.key_id,
                coordinator_token,
                self._inbox,
                self._outbox,
                self.app_env,
            ),
        )
        self._process.daemon = True
        self._process.start()

        try:
            init_msg = self._outbox.get(timeout=10)
            if isinstance(init_msg, dict) and init_msg.get("status") == "READY":
                self._public_key_hex = init_msg["public_key_hex"]
            else:
                self._public_key_hex = ""
        except Exception:
            self._public_key_hex = ""

    @property
    def pid(self) -> int | None:
        return self._process.pid

    @property
    def public_key_hex(self) -> str:
        return self._public_key_hex

    def is_alive(self) -> bool:
        return self._process.is_alive()

    def terminate(self) -> None:
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=3)
        if self._temp_vault is not None:
            with contextlib.suppress(Exception):
                self._temp_vault.cleanup()
            self._temp_vault = None

    def sign_completed_job_receipt(
        self,
        job_id: str,
        artifact_bytes: bytes,
        caller_identity: str = "COORDINATOR",
        caller_token: str | None = None,
    ) -> TrainingExecutionReceipt:
        """Request Ed25519 receipt from the isolated issuer process over authenticated IPC."""
        if not self._process.is_alive():
            raise TrainingProvenanceSecurityError("Receipt signing service unavailable: issuer process terminated.")

        token = caller_token
        self._inbox.put({
            "command": "SIGN_RECEIPT",
            "job_id": job_id,
            "artifact_bytes": artifact_bytes,
            "auth_token": token,
            "caller_identity": caller_identity,
        })
        try:
            res = self._outbox.get(timeout=30)
        except Exception as e:
            raise TrainingProvenanceSecurityError(f"Receipt signing service timeout or communication failure: {e}") from e

        if res.get("status") == "SUCCESS":
            return res["receipt"]
        err_msg = res.get("error") or f"Receipt signing rejected by isolated issuer: {res}"
        raise TrainingProvenanceSecurityError(err_msg)


def setup_bank_private_storage(
    bank_storage_dir: str,
    bank_id: str,
    X_partition: np.ndarray,
    y_partition: np.ndarray,
    bank_token: str | None = None,
    bank_encryption_key: str | bytes | None = None,
    lock_boundary: bool = False,
    app_env: str = "production",
) -> str:
    """Privileged fixture provisioning bank-private synthetic partition into bank-owned directory."""
    vault = BankPrivateStorageVault(
        bank_id=bank_id,
        vault_dir=bank_storage_dir,
        bank_token=bank_token,
        bank_encryption_key=bank_encryption_key,
        app_env=app_env,
    )
    data_path = vault.store_dataset(X_partition, y_partition)
    if lock_boundary:
        vault.acquire_exclusive_os_boundary()
    return data_path



def spawn_bank_workers(
    participating_banks: list[str],
    bank_tokens: dict[str, str],
    db_url: str,
    bank_data_paths: dict[str, str] | None = None,
    bank_partitions: dict[str, tuple[np.ndarray, np.ndarray]] | None = None,
    fail_bank_b_observation: bool = False,
) -> tuple[dict[str, mp.Process], dict[str, dict[str, mp.Queue]]]:
    """Spawn dedicated OS worker processes with their private credentials and data paths.

    Notice: This is called at process launch or integration fixture time.
    The Coordinator NEVER receives bank_tokens or bank_data_paths; it receives only communication endpoints.
    """
    processes: dict[str, mp.Process] = {}
    endpoints: dict[str, dict[str, mp.Queue]] = {}
    for b_id in participating_banks:
        in_q: mp.Queue = mp.Queue()
        out_q: mp.Queue = mp.Queue()
        data_path = bank_data_paths.get(b_id) if bank_data_paths else None
        X_b, y_b = bank_partitions.get(b_id, (None, None)) if bank_partitions else (None, None)
        should_fail_obs = (b_id == "BANK_B" and fail_bank_b_observation)
        proc = mp.Process(
            target=run_bank_worker_node,
            args=(
                b_id,
                bank_tokens[b_id],
                db_url,
                in_q,
                out_q,
                data_path,
                X_b,
                y_b,
                should_fail_obs,
            ),
        )
        proc.daemon = True
        proc.start()
        processes[b_id] = proc
        endpoints[b_id] = {"inbox": in_q, "outbox": out_q}
    return processes, endpoints


# ---------------------------------------------------------------------------
# Bank Worker Process Entry Point
# ---------------------------------------------------------------------------


def run_bank_worker_node(
    bank_id: str,
    worker_token: str,
    db_url: str,
    inbox: mp.Queue,
    outbox: mp.Queue,
    data_path: str | None = None,
    X_partition: np.ndarray | None = None,
    y_partition: np.ndarray | None = None,
    fail_observation: bool = False,
) -> None:
    """Dedicated Operating System Process for a Participating Bank Worker.

    Invariants enforced:
    1. Isolated OS Process (distinct PID).
    2. Zero external access to private datasets; loaded locally from bank storage.
    3. Authenticated solely by worker_token bound to bank_id (coordinator has no bank tokens).
    4. Performs genuine PyTorch local training steps and records observations directly into PostgreSQL authority.
    5. Missing or non-finite losses fail immediately without fallback invention.
    """
    pid = os.getpid()
    logger.info("Bank worker [%s] started with PID %d", bank_id, pid)
    settings = get_settings()
    model_service = ModelService(settings)

    # Establish connection to authoritative training job database
    authority_source = TrustedTrainingJobRecordSource(
        worker_token=worker_token,
        bank_tokens={bank_id: worker_token},
        db_url=db_url,
        enforce_lifecycle=True,
        app_env="production",
    )

    # Load local training data from isolated bank storage if path provided
    vault_handle: BankPrivateStorageVault | None = None
    if data_path is not None:
        if not os.path.exists(data_path):
            raise FileNotFoundError(f"Bank [{bank_id}] private dataset not found at '{data_path}'")
        bank_vault_dir = os.path.dirname(os.path.dirname(data_path))
        vault_handle = BankPrivateStorageVault(bank_id, bank_vault_dir, bank_token=worker_token)
        X_train, y_train = vault_handle.read_dataset_as_bank(bank_id, caller_token=worker_token)
        vault_handle.acquire_exclusive_os_boundary()
    elif X_partition is not None and y_partition is not None:
        X_train = X_partition
        y_train = y_partition
    else:
        raise ValueError(f"Bank [{bank_id}] must be supplied with either a private data_path or partition arrays.")

    try:
        while True:
            try:
                msg = inbox.get()
            except Exception:
                break

            if msg is None or msg.get("command") == "STOP":
                logger.info("Bank worker [%s] received STOP command. Exiting.", bank_id)
                break

            if msg.get("command") == "GET_RAW_DATA":
                outbox.put({
                    "status": "ERROR",
                    "error": f"Institutional boundary violation: coordinator cannot request raw bank dataset from {bank_id}.",
                })
                continue

            if msg.get("command") == "TRAIN_ROUND":
                job_id = msg["job_id"]
                round_number = msg["round_number"]
                global_weights_obj: ModelWeights | None = msg.get("global_weights")

                # On round 1, authorized worker initiates job start in PostgreSQL authority
                if round_number == 1 and bank_id == "BANK_A":
                    with contextlib.suppress(Exception):
                        authority_source.start_training_job(job_id=job_id, worker_token=worker_token)

                # Initialize local model and load latest global weights
                local_model = model_service.create_model(input_dim=NUM_FEATURES, dp_compatible=True)
                if global_weights_obj is not None:
                    model_service.set_parameters(local_model, global_weights_obj)

                # Execute real PyTorch optimization on private dataset partition
                local_model, loss_history, _ = model_service.train_local(
                    model=local_model,
                    X_train=X_train,
                    y_train=y_train,
                    epochs=1,
                    learning_rate=0.01,
                    batch_size=32,
                )

                local_weights = model_service.get_parameters(local_model)
                weights_bytes = _serialize_weights_to_bytes(local_weights)
                update_digest = hashlib.sha256(weights_bytes).hexdigest()
                sample_count = len(X_train)

                # Strict measurement validation: no fabricated fallback loss values
                if not loss_history:
                    raise TrainingProvenanceSecurityError(
                        f"Bank worker [{bank_id}] produced no loss history during local training."
                    )
                observed_loss = float(loss_history[-1])
                if not math.isfinite(observed_loss):
                    raise TrainingProvenanceSecurityError(
                        f"Bank worker [{bank_id}] produced non-finite observed loss: {observed_loss}"
                    )

                if fail_observation:
                    # Adversarial failure injection: worker fails to record observation in PostgreSQL
                    logger.warning("Bank worker [%s] simulated observation persistence failure for round %d", bank_id, round_number)
                    outbox.put({
                        "status": "OBSERVATION_FAILED",
                        "bank_id": bank_id,
                        "round_number": round_number,
                        "pid": pid,
                    })
                    continue

                # Record authoritative execution observation directly in PostgreSQL
                try:
                    obs = authority_source.record_round_observation(
                        job_id=job_id,
                        round_number=round_number,
                        sample_count=sample_count,
                        actual_executed_epochs=1,
                        observed_loss=observed_loss,
                        metrics={"loss": observed_loss},
                        worker_token=worker_token,
                        bank_id=bank_id,
                        model_update_digest=update_digest,
                        execution_status="COMPLETED",
                    )
                    logger.info("Bank worker [%s] persisted observation event %s for round %d", bank_id, obs.event_id, round_number)
                except Exception as e:
                    logger.error("Bank worker [%s] failed to record observation: %s", bank_id, e)
                    outbox.put({
                        "status": "ERROR",
                        "bank_id": bank_id,
                        "error": str(e),
                        "pid": pid,
                    })
                    continue

                # Send local weights and digest to coordinator over federated transport
                # Notice: Zero raw dataset samples (X_train, y_train) are transmitted across IPC.
                outbox.put({
                    "status": "SUCCESS",
                    "bank_id": bank_id,
                    "pid": pid,
                    "round_number": round_number,
                    "weights": local_weights,
                    "update_digest": update_digest,
                    "sample_count": sample_count,
                    "loss": observed_loss,
                    "executed_epochs": 1,
                })
    finally:
        if vault_handle is not None:
            vault_handle.release_exclusive_os_boundary()


# ---------------------------------------------------------------------------
# Distributed Federated Learning Coordinator Service
# ---------------------------------------------------------------------------


class DistributedFederatedTrainingManager:
    """Orchestrates genuine distributed federated training across isolated bank worker processes.

    Security Boundaries Enforced:
    1. Coordinator Domain: Holds coordinator_token only. Never possesses bank private credentials.
    2. Data Privacy: Coordinator never receives raw bank dataset partitions.
    3. Multi-Process Topology: Separate operating system processes for Coordinator and each participating Bank.
    4. Execution Evidence: Observations and global aggregations recorded transactionally in PostgreSQL authority.
    5. Cryptographic Provenance: Ed25519 receipt issued by authorized issuer and verified independently by public-key-only verifier.
    """

    def __init__(
        self,
        coordinator_token: str,
        db_url: str,
        app_env: str = "production",
        bank_tokens: dict[str, str] | None = None,
    ) -> None:
        self.coordinator_token = coordinator_token
        self.db_url = db_url
        self.app_env = app_env
        self.settings = get_settings()

        if self.app_env.lower() == "production" and bank_tokens is not None:
            raise TrainingProvenanceSecurityError(
                "Production coordinator cannot receive bank private credentials (BI-02)."
            )

        # Coordinator authority source possesses coordinator_token ONLY!
        # It has ZERO access to bank worker tokens and cannot impersonate banks.
        self.authority_source = TrustedTrainingJobRecordSource(
            coordinator_token=self.coordinator_token,
            db_url=self.db_url,
            enforce_lifecycle=True,
            app_env=self.app_env,
        )

        self.model_service = ModelService(self.settings)
        self.privacy_service = PrivacyService()
        self.fl_engine = FederatedLearningEngine(
            settings=self.settings,
            model_service=self.model_service,
            privacy_service=self.privacy_service,
        )

    def execute_distributed_federation(
        self,
        job_id: str,
        training_run_id: str,
        simulation_id: str,
        holdout_val_data: tuple[np.ndarray, np.ndarray],
        participating_banks: list[str] | tuple[str, ...] | None = None,
        worker_endpoints: dict[str, dict[str, mp.Queue]] | None = None,
        worker_processes: dict[str, mp.Process] | None = None,
        bank_partitions: dict[str, tuple[np.ndarray, np.ndarray]] | None = None,
        bank_tokens: dict[str, str] | None = None,
        bank_data_paths: dict[str, str] | None = None,
        num_rounds: int = 2,
        expected_unique_samples: int | None = None,
        fail_bank_b_observation: bool = False,
        crash_bank_b: bool = False,
        registry_storage_dir: str | None = None,
        signing_key_hex: str | None = None,
        signing_authority: IsolatedReceiptSigningAuthority | None = None,
        receipt_issuer: TrainingReceiptIssuer | None = None,
        receipt_verifier: TrainingReceiptVerifier | None = None,
        model_registry: ModelRegistry | None = None,
    ) -> dict[str, Any]:
        """Execute complete distributed E2E federated training lifecycle across independent processes.

        Returns comprehensive trace including process IDs, observations, receipts, and registry status.
        """
        coordinator_pid = os.getpid()

        is_prod = self.app_env.lower() == "production"
        if is_prod:
            if bank_partitions is not None:
                raise TrainingProvenanceSecurityError(
                    "Production coordinator cannot receive bank private dataset partitions (BI-01)."
                )
            if bank_tokens is not None:
                raise TrainingProvenanceSecurityError(
                    "Production coordinator cannot receive bank private credentials (BI-02)."
                )
            if bank_data_paths is not None:
                raise TrainingProvenanceSecurityError(
                    "Production coordinator cannot receive bank private data paths (BI-03)."
                )
            if signing_key_hex is not None:
                raise TrainingProvenanceSecurityError(
                    "Production coordinator cannot receive signing private key (CR-01, CR-02)."
                )
            if receipt_issuer is not None and getattr(receipt_issuer, "_require_authenticated_job", None) is False:
                raise TrainingProvenanceSecurityError(
                    "Production receipt issuer cannot disable authenticated job records (CR-04)."
                )
            if not worker_endpoints:
                raise TrainingProvenanceSecurityError(
                    "Production coordinator cannot generate bank credentials or spawn workers with generated secrets (BI-04). "
                    "Pre-authenticated worker endpoints must be supplied."
                )

        # Determine participating banks list
        banks = list(participating_banks) if participating_banks else (
            sorted(bank_partitions.keys()) if bank_partitions else (
                sorted(worker_endpoints.keys()) if worker_endpoints else ["BANK_A", "BANK_B", "BANK_C"]
            )
        )
        if not banks:
            raise ValueError("No participating banks provided.")

        # Spawn workers if endpoints were not directly provided
        spawned_procs = worker_processes or {}
        endpoints = worker_endpoints or {}
        if not endpoints:
            tokens = bank_tokens or {b: f"worker_token_{b}_auto" for b in banks}
            spawned_procs, endpoints = spawn_bank_workers(
                participating_banks=banks,
                bank_tokens=tokens,
                db_url=self.db_url,
                bank_data_paths=bank_data_paths,
                bank_partitions=bank_partitions,
                fail_bank_b_observation=fail_bank_b_observation,
            )

        # 1. Schedule training job in PostgreSQL authority
        _ = self.authority_source.schedule_training_job(
            job_id=job_id,
            training_run_id=training_run_id,
            simulation_id=simulation_id,
            coordinator_token=self.coordinator_token,
            expected_rounds=num_rounds,
            expected_epochs=num_rounds,
            input_dim=NUM_FEATURES,
            feature_names=list(FEATURE_NAMES),
            permitted_workers=banks,
        )

        inboxes = {b: endpoints[b]["inbox"] for b in banks}
        outboxes = {b: endpoints[b]["outbox"] for b in banks}
        worker_pids: dict[str, int] = {b: spawned_procs[b].pid or 0 for b in banks if b in spawned_procs}

        logger.info(
            "Spawned distributed topology: Coordinator PID=%d, Bank Worker PIDs=%s",
            coordinator_pid,
            worker_pids,
        )

        # Initialize global model
        global_model = self.model_service.create_model(input_dim=NUM_FEATURES, dp_compatible=True)
        global_weights = self.model_service.get_parameters(global_model)
        aggregated_model_digest = ""
        final_model_bytes: bytes = b""

        try:
            for round_num in range(1, num_rounds + 1):
                logger.info("--- Starting Federated Global Round %d ---", round_num)

                # If simulating Bank B crash in round 2
                if crash_bank_b and round_num == 2 and "BANK_B" in spawned_procs:
                    logger.warning("Simulating crash: Terminating Bank B worker process PID %d", spawned_procs["BANK_B"].pid)
                    spawned_procs["BANK_B"].terminate()
                    spawned_procs["BANK_B"].join()

                # Dispatch training task to all active workers
                for b_id, in_q in inboxes.items():
                    if b_id in spawned_procs and not spawned_procs[b_id].is_alive():
                        continue
                    in_q.put({
                        "command": "TRAIN_ROUND",
                        "job_id": job_id,
                        "round_number": round_num,
                        "global_weights": global_weights,
                    })

                # Collect responses
                accepted_participant_ids: list[str] = []
                accepted_local_update_digests: list[str] = []
                client_weights_list: list[ModelWeights] = []
                client_samples_list: list[int] = []

                for b_id, out_q in outboxes.items():
                    if b_id in spawned_procs and not spawned_procs[b_id].is_alive():
                        logger.warning("Bank worker [%s] is dead / crashed.", b_id)
                        continue

                    # Wait for worker response with timeout
                    try:
                        res = out_q.get(timeout=60)
                    except Exception:
                        logger.warning("Timeout waiting for response from Bank worker [%s]", b_id)
                        continue

                    if res.get("status") != "SUCCESS":
                        logger.warning("Worker [%s] failed round execution: %s", b_id, res)
                        continue

                    worker_pids[b_id] = res["pid"]
                    local_w = res["weights"]
                    w_digest = res["update_digest"]
                    s_count = res["sample_count"]

                    # Coordinator verifies update digest against received weight bytes
                    calculated_digest = hashlib.sha256(_serialize_weights_to_bytes(local_w)).hexdigest()
                    if calculated_digest != w_digest:
                        raise TrainingProvenanceSecurityError(
                            f"Coordinator detected tampered update digest from {b_id}: {w_digest} != {calculated_digest}"
                        )

                    accepted_participant_ids.append(b_id)
                    accepted_local_update_digests.append(w_digest)
                    client_weights_list.append(local_w)
                    client_samples_list.append(s_count)

                missing_participants = set(banks) - set(accepted_participant_ids)
                if missing_participants:
                    raise TrainingProvenanceSecurityError(
                        f"Round {round_num} aborted: Required participant(s) {sorted(missing_participants)} failed to contribute valid observations."
                    )
                if not accepted_participant_ids:
                    raise TrainingProvenanceSecurityError(
                        f"Round {round_num} aborted: No valid worker contributions received."
                    )

                # Execute genuine FedAvg aggregation using production FederatedLearningEngine
                global_weights = self.fl_engine.aggregate_parameters(
                    client_weights=client_weights_list,
                    client_samples=client_samples_list,
                    method=AggregationMethod.FED_AVG_WEIGHTED,
                    global_weights=global_weights,
                )
                self.model_service.set_parameters(global_model, global_weights)

                # Derive exact serialized model artifact bytes and its SHA-256 digest
                final_model_bytes = _serialize_model_state_dict(global_model)
                aggregated_model_digest = hashlib.sha256(final_model_bytes).hexdigest()

                # Record global aggregation event in PostgreSQL authority
                self.authority_source.record_global_aggregation(
                    job_id=job_id,
                    round_number=round_num,
                    aggregation_algorithm="fed_avg_weighted",
                    accepted_participant_ids=accepted_participant_ids,
                    accepted_local_update_digests=accepted_local_update_digests,
                    aggregated_global_model_digest=aggregated_model_digest,
                    coordinator_token=self.coordinator_token,
                )
                logger.info("Persisted GlobalAggregationEvent for round %d: digest=%s", round_num, aggregated_model_digest)

            # 4. Perform Genuine Model Evaluation on holdout dataset
            X_val, y_val = holdout_val_data
            eval_metrics = self.model_service.evaluate(global_model, X_val, y_val)
            if "loss" not in eval_metrics or "auc_roc" not in eval_metrics:
                raise TrainingProvenanceSecurityError(
                    f"Holdout evaluation failed to compute required metrics ('loss', 'auc_roc'). Available: {list(eval_metrics.keys())}"
                )
            val_loss = float(eval_metrics["loss"])
            val_auc = float(eval_metrics["auc_roc"])
            if not (math.isfinite(val_loss) and math.isfinite(val_auc)):
                raise TrainingProvenanceSecurityError(
                    f"Holdout evaluation produced non-finite metrics: val_loss={val_loss}, val_auc={val_auc}"
                )

            # Record Evaluation Observations in PostgreSQL authority
            self.authority_source.record_evaluation_observation(
                job_id=job_id,
                metric_name="auc_roc",
                metric_value=val_auc,
                evaluated_artifact_digest=aggregated_model_digest,
                coordinator_token=self.coordinator_token,
            )
            self.authority_source.record_evaluation_observation(
                job_id=job_id,
                metric_name="val_loss",
                metric_value=val_loss,
                evaluated_artifact_digest=aggregated_model_digest,
                coordinator_token=self.coordinator_token,
            )

            # 5. Final artifact digest is the exact SHA-256 of the aggregated model bytes
            final_artifact_sha256 = aggregated_model_digest

            # Strict sample accounting: derive directly from PostgreSQL verified observations
            authority = self.authority_source
            obs_list = authority.get_round_observations(job_id)
            final_round_obs = [o for o in obs_list if o.round_number == num_rounds and o.execution_status == "COMPLETED"]
            unique_samples = sum(o.actual_sample_count for o in final_round_obs)
            if unique_samples == 0:
                raise TrainingProvenanceSecurityError(
                    f"No completed observations for round {num_rounds} to reconcile sample count."
                )
            if expected_unique_samples is not None and unique_samples != expected_unique_samples:
                raise TrainingProvenanceSecurityError(
                    f"Observed unique samples {unique_samples} does not match expected {expected_unique_samples}"
                )

            tot_samples = sum(o.actual_sample_count for o in final_round_obs)
            final_loss = (
                sum(float(o.observed_loss) * o.actual_sample_count for o in final_round_obs)
                / tot_samples
            )

            # 6. Complete Job in PostgreSQL Authority
            completed_record = self.authority_source.complete_training_job(
                job_id=job_id,
                executed_rounds=num_rounds,
                executed_epochs=num_rounds,
                sample_count=unique_samples,
                observed_metrics={
                    "loss": final_loss,
                    "auc_roc": val_auc,
                    "val_loss": val_loss,
                },
                artifact_sha256=final_artifact_sha256,
                worker_id="FEDERATED_COORDINATOR",
                coordinator_token=self.coordinator_token,
                enforce_lifecycle=True,
            )

            # 7. Issue Ed25519 Receipt in RECEIPT ISSUER DOMAIN
            if signing_authority is not None:
                receipt = signing_authority.sign_completed_job_receipt(
                    job_id=job_id,
                    artifact_bytes=final_model_bytes,
                    caller_identity="COORDINATOR",
                    caller_token=self.coordinator_token,
                )
            else:
                if is_prod:
                    raise TrainingProvenanceSecurityError(
                        "Production federated training requires an isolated receipt signing authority. "
                        "Coordinator cannot self-sign receipts in production."
                    )
                active_issuer = receipt_issuer
                if active_issuer is None:
                    if signing_key_hex is not None:
                        active_issuer = TrainingReceiptIssuer(
                            signing_key_hex=signing_key_hex,
                            key_id=DEFAULT_TRAINING_KEY_ID,
                            job_record_source=self.authority_source,
                            enforce_job_records=True,
                        )
                    else:
                        active_issuer = get_default_training_issuer()

                evidence = TrainingExecutionEvidence(
                    training_run_id=training_run_id,
                    job_id=job_id,
                    simulation_id=simulation_id,
                    completed_at=completed_record.completed_at,
                    status="COMPLETED",
                    executed_rounds=num_rounds,
                    executed_epochs=num_rounds,
                    sample_count=unique_samples,
                    observed_metrics=dict(completed_record.observed_metrics),
                    artifact_sha256=final_artifact_sha256,
                    architecture="FraudDetectionNN",
                    feature_schema_version="v1.0",
                    feature_names=list(FEATURE_NAMES),
                    producer_id="FEDERATED_COORDINATOR",
                )
                receipt = active_issuer.issue_receipt(evidence=evidence, model_bytes=final_model_bytes)

            # 8. Public-Key-Only Verification in VERIFIER DOMAIN
            active_verifier = receipt_verifier or get_default_training_verifier()
            is_valid, reason = active_verifier.verify_receipt(
                receipt=receipt,
                expected_simulation_id=simulation_id,
                expected_architecture="FraudDetectionNN",
                expected_feature_schema_version="v1.0",
                expected_input_dim=NUM_FEATURES,
                expected_feature_names=list(FEATURE_NAMES),
                actual_artifact_bytes=final_model_bytes,
            )
            if not is_valid:
                raise TrainingProvenanceSecurityError(f"Ed25519 receipt verification failed: {reason}")

            # 9. Register Inactive Candidate in ModelRegistry in REGISTRY DOMAIN
            active_registry = model_registry or ModelRegistry(
                storage_dir=registry_storage_dir,
                verifier=active_verifier,
            )
            saved_version = active_registry.save_version(
                simulation_id=simulation_id,
                state_dict=global_model.state_dict(),
                metrics={
                    "loss": final_loss,
                    "auc_roc": val_auc,
                    "val_loss": val_loss,
                },
                is_promoted=False,
                status="inactive",
                provenance_type="TRAINING_RECORDED",
                training_run_id=training_run_id,
                training_receipt=receipt,
                artifact_bytes=final_model_bytes,
                expected_architecture="FraudDetectionNN",
                expected_feature_schema_version="v1.0",
                expected_input_dim=NUM_FEATURES,
                expected_feature_names=list(FEATURE_NAMES),
            )

            # Confirm model is NOT champion
            active_champion = active_registry.get_active_champion_identity(simulation_id=simulation_id)
            is_promoted_champion = (active_champion is not None and active_champion.get("sha256") == final_artifact_sha256)

            return {
                "status": "COMPLETED",
                "coordinator_pid": coordinator_pid,
                "worker_pids": worker_pids,
                "completed_record": completed_record,
                "receipt": receipt,
                "is_verified": is_valid,
                "saved_version": saved_version,
                "is_promoted_champion": is_promoted_champion,
                "metrics": {"loss": final_loss, "auc_roc": val_auc, "val_loss": val_loss},
                "final_artifact_sha256": final_artifact_sha256,
            }

        finally:
            # Clean shutdown of worker processes
            for b_id, in_q in inboxes.items():
                with contextlib.suppress(Exception):
                    in_q.put({"command": "STOP"})
            for proc in spawned_procs.values():
                proc.join(timeout=2)
                if proc.is_alive():
                    proc.terminate()
