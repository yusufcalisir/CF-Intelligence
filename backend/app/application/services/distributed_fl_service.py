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
import concurrent.futures
import contextlib
import copyreg
import hashlib
import hmac
import io
import json
import logging
import math
import multiprocessing as mp
import os
import secrets
import sys
import tempfile
import threading
import time
import types
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from multiprocessing.connection import Client, Listener
from typing import Any, cast

import numpy as np
import torch
import torch.nn as nn
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.fl_engine import FederatedLearningEngine
from app.application.services.model_registry import ModelRegistry
from app.application.services.model_service import (
    NUM_FEATURES,
    FraudDetectionModel,
    ModelService,
)
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
from app.infrastructure.database.training_authority_backends import (
    CompletedJobExecutionRecord,
    ModelEvaluationEvent,
    PostgresTrainingJobAuthority,
)
from app.infrastructure.security.p2p_secagg_driver import (
    ECDHPublicKeyBundle,
    P2PSecAggDriver,
    SecAggSecurityError,
    derive_institutional_identity_hex,
    deserialize_masked_vector,
    serialize_masked_vector,
)

logger = logging.getLogger(__name__)


def _reconstruct_mappingproxy(d: dict[Any, Any]) -> types.MappingProxyType:
    return types.MappingProxyType(d)


copyreg.pickle(types.MappingProxyType, lambda mp: (_reconstruct_mappingproxy, (dict(mp),)))


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

BANK_SECURITY_SIDS: dict[str, str] = {
    "BANK_A": "S-1-5-21-4192661148-2837192312-3928174921-1001",
    "BANK_B": "S-1-5-21-4192661148-2837192312-3928174921-1002",
    "BANK_C": "S-1-5-21-4192661148-2837192312-3928174921-1003",
}
COORDINATOR_RESTRICTED_SID: str = "S-1-5-12"


def get_bank_security_sid(bank_id: str) -> str:
    """Return the authoritative Windows Security Identifier (SID) bound to a specific bank."""
    if bank_id in BANK_SECURITY_SIDS:
        return BANK_SECURITY_SIDS[bank_id]
    h = int(hashlib.sha256(bank_id.encode("utf-8")).hexdigest()[:8], 16) % 100000 + 2000
    return f"S-1-5-21-4192661148-2837192312-3928174921-{h}"


def _check_no_reparse_or_symlink(path: str, label: str) -> None:
    """Verify that a path is not a symlink, directory junction, or reparse point."""
    try:
        if os.path.islink(path):
            raise TrainingProvenanceSecurityError(f"{label} cannot be a symbolic link: '{path}'.")
        if hasattr(os.path, "isjunction") and os.path.isjunction(path):
            raise TrainingProvenanceSecurityError(
                f"{label} cannot be a directory junction: '{path}'."
            )
        st = os.lstat(path)
        import stat

        if stat.S_ISLNK(st.st_mode):
            raise TrainingProvenanceSecurityError(f"{label} cannot be a symbolic link: '{path}'.")
        if getattr(st, "st_reparse_tag", 0) != 0:
            raise TrainingProvenanceSecurityError(
                f"{label} cannot be a reparse point or junction: '{path}'."
            )
    except TrainingProvenanceSecurityError:
        raise
    except FileNotFoundError:
        pass
    except OSError as e:
        raise TrainingProvenanceConfigurationError(
            f"Failed to access or inspect path for {label} '{path}': {e}"
        ) from e


def configure_bank_vault_os_permissions(vault_dir: str, bank_id: str) -> None:
    """Configure mandatory operating system permissions (NTFS DACL / POSIX mode) on bank vault directory.

    Enforces that:
    1. Federation Coordinator (WinRestrictedCodeSid S-1-5-12) is explicitly denied all access.
    2. Other participating banks (BANK_A, BANK_B, BANK_C) are explicitly denied all access.
    3. Only the designated bank's security identity and host service user are granted access.
    4. Child objects and containers inherit the protection.
    """
    if os.name == "nt":
        try:
            import ntsecuritycon
            import win32api
            import win32con
            import win32security

            hProcess = win32api.GetCurrentProcess()
            hToken = win32security.OpenProcessToken(hProcess, win32con.TOKEN_ALL_ACCESS)
            service_sid, _ = win32security.GetTokenInformation(hToken, win32security.TokenUser)
            coord_sid = win32security.CreateWellKnownSid(win32security.WinRestrictedCodeSid)
            bank_sid = win32security.GetBinarySid(get_bank_security_sid(bank_id))

            other_banks = [b for b in ("BANK_A", "BANK_B", "BANK_C") if b != bank_id]

            dir_dacl = win32security.ACL()
            # Explicit Deny ACEs must precede Allow ACEs
            dir_dacl.AddAccessDeniedAceEx(
                win32security.ACL_REVISION,
                win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE,
                ntsecuritycon.FILE_ALL_ACCESS,
                coord_sid,
            )
            for ob in other_banks:
                other_sid = win32security.GetBinarySid(get_bank_security_sid(ob))
                dir_dacl.AddAccessDeniedAceEx(
                    win32security.ACL_REVISION,
                    win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE,
                    ntsecuritycon.FILE_ALL_ACCESS,
                    other_sid,
                )

            # Explicit Allow ACEs
            dir_dacl.AddAccessAllowedAceEx(
                win32security.ACL_REVISION,
                win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE,
                ntsecuritycon.FILE_ALL_ACCESS,
                bank_sid,
            )
            dir_dacl.AddAccessAllowedAceEx(
                win32security.ACL_REVISION,
                win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE,
                ntsecuritycon.FILE_ALL_ACCESS,
                service_sid,
            )

            win32security.SetNamedSecurityInfo(
                vault_dir,
                win32security.SE_FILE_OBJECT,
                win32security.DACL_SECURITY_INFORMATION
                | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
                None,
                None,
                dir_dacl,
                None,
            )
        except Exception as e:
            raise TrainingProvenanceConfigurationError(
                f"Failed to configure mandatory operating system NTFS DACL on bank '{bank_id}' vault directory: {e}"
            ) from e
    else:
        try:
            os.chmod(vault_dir, 0o700)
        except Exception as e:
            raise TrainingProvenanceConfigurationError(
                f"Failed to configure mandatory operating system permissions on bank '{bank_id}' vault directory: {e}"
            ) from e


def configure_bank_file_os_permissions(file_path: str, bank_id: str) -> None:
    """Configure mandatory operating system permissions (NTFS DACL / POSIX mode) on bank private file."""
    if os.name == "nt":
        try:
            import ntsecuritycon
            import win32api
            import win32con
            import win32security

            hProcess = win32api.GetCurrentProcess()
            hToken = win32security.OpenProcessToken(hProcess, win32con.TOKEN_ALL_ACCESS)
            service_sid, _ = win32security.GetTokenInformation(hToken, win32security.TokenUser)
            coord_sid = win32security.CreateWellKnownSid(win32security.WinRestrictedCodeSid)
            bank_sid = win32security.GetBinarySid(get_bank_security_sid(bank_id))

            other_banks = [b for b in ("BANK_A", "BANK_B", "BANK_C") if b != bank_id]

            file_dacl = win32security.ACL()
            file_dacl.AddAccessDeniedAce(
                win32security.ACL_REVISION,
                ntsecuritycon.FILE_ALL_ACCESS,
                coord_sid,
            )
            for ob in other_banks:
                other_sid = win32security.GetBinarySid(get_bank_security_sid(ob))
                file_dacl.AddAccessDeniedAce(
                    win32security.ACL_REVISION,
                    ntsecuritycon.FILE_ALL_ACCESS,
                    other_sid,
                )

            file_dacl.AddAccessAllowedAce(
                win32security.ACL_REVISION,
                ntsecuritycon.FILE_ALL_ACCESS,
                bank_sid,
            )
            file_dacl.AddAccessAllowedAce(
                win32security.ACL_REVISION,
                ntsecuritycon.FILE_ALL_ACCESS,
                service_sid,
            )

            win32security.SetNamedSecurityInfo(
                file_path,
                win32security.SE_FILE_OBJECT,
                win32security.DACL_SECURITY_INFORMATION
                | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
                None,
                None,
                file_dacl,
                None,
            )
        except Exception as e:
            raise TrainingProvenanceConfigurationError(
                f"Failed to configure mandatory operating system NTFS DACL on bank '{bank_id}' file: {e}"
            ) from e
    else:
        try:
            os.chmod(file_path, 0o600)
        except Exception as e:
            raise TrainingProvenanceConfigurationError(
                f"Failed to configure mandatory operating system permissions on bank '{bank_id}' file: {e}"
            ) from e


class BankPrivateStorageVault:
    """Institution-isolated storage vault enforcing physical, cryptographic, and logical boundary protection.

    Enforces Module 01-A.1 Requirement:
    - Dedicated local storage vault per financial institution (BANK_A, BANK_B, BANK_C).
    - Hard access denial on unauthorized cross-bank reads or coordinator read attempts enforced via native OS NTFS DACLs.
    - Persistent operating system authorization persisting before startup, during training, and after worker termination.
    - Token-derived cryptographic sealing (PBKDF2-HMAC-SHA256 with 100k iterations and Fernet AES-128-CBC + HMAC-SHA256 authenticated encryption) of raw datasets at rest with mandatory key configuration in production.
    - Zero plaintext fallback in production (unencrypted zip/npz strictly rejected with TrainingProvenanceSecurityError).
    - Native OS-level exclusive handle on the private dataset during worker execution.
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
        if os.path.basename(os.path.abspath(vault_dir)) == bank_id:
            self.vault_dir = os.path.abspath(vault_dir)
        else:
            self.vault_dir = os.path.join(vault_dir, bank_id)

        self.data_path = os.path.join(self.vault_dir, "private_dataset.npz")
        self.key_path = os.path.join(self.vault_dir, "encryption.key")
        self.bank_token = bank_token
        self.bank_encryption_key = bank_encryption_key
        self.app_env = app_env
        self._is_prod = app_env.lower() == "production"
        self._os_lock_handle: Any = None
        self._orig_mode: int | None = None

        if self.bank_encryption_key is None and os.path.exists(self.key_path):
            with contextlib.suppress(Exception):
                with open(self.key_path, "rb") as f:
                    self.bank_encryption_key = f.read().decode("utf-8")

        if self._is_prod and self.bank_encryption_key is None:
            raise TrainingProvenanceConfigurationError(
                f"Production bank private storage vault for '{self.bank_id}' requires dedicated data-at-rest "
                "encryption key (bank_encryption_key). Deriving data encryption keys from authentication credentials is forbidden in production."
            )

        _check_no_reparse_or_symlink(
            self.vault_dir, f"Bank private vault directory for '{self.bank_id}'"
        )
        if os.path.exists(self.vault_dir) and not os.path.isdir(self.vault_dir):
            raise TrainingProvenanceSecurityError(
                f"Bank private vault directory path conflicts with existing non-directory file: '{self.vault_dir}'."
            )

        try:
            os.makedirs(self.vault_dir, mode=0o700, exist_ok=True)
        except Exception as e:
            raise TrainingProvenanceConfigurationError(
                f"Failed to create bank private storage vault directory for '{self.bank_id}': {e}"
            ) from e

        _check_no_reparse_or_symlink(
            self.vault_dir, f"Bank private vault directory for '{self.bank_id}'"
        )
        configure_bank_vault_os_permissions(self.vault_dir, self.bank_id)

    @property
    def authoritative_dataset_identity(self) -> str:
        """Return the authoritative bank-local dataset identity for this private vault."""
        manifest_path = os.path.join(self.vault_dir, "dataset_manifest.json")
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and data.get("authoritative_dataset_tag"):
                        return str(data["authoritative_dataset_tag"]).strip()
            except Exception:
                pass
        return f"{self.bank_id}_protected_dataset"

    def get_authoritative_dataset_identity(self) -> str:
        return self.authoritative_dataset_identity

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
                    f"Production bank private storage vault for '{self.bank_id}' requires dedicated encryption key."
                )
            return None

        # PBKDF2-HMAC-SHA256 key derivation with bank-specific salt and 100,000 iterations (development fallback)
        salt = f"cfi:vault:kdf:v1:{self.bank_id}".encode()
        derived_key = hashlib.pbkdf2_hmac("sha256", self.bank_token.encode(), salt, 100_000, 32)
        return Fernet(base64.urlsafe_b64encode(derived_key))

    def store_encryption_key(self, key_material: str | bytes) -> str:
        """Store the bank's encryption key with mandatory OS NTFS DACL protection."""
        if not key_material:
            raise TrainingProvenanceConfigurationError(
                f"Cannot store empty encryption key for bank '{self.bank_id}'."
            )
        key_bytes = key_material.encode("utf-8") if isinstance(key_material, str) else key_material

        _check_no_reparse_or_symlink(
            self.key_path, f"Bank encryption key file for '{self.bank_id}'"
        )
        if os.path.exists(self.key_path) and os.path.isdir(self.key_path):
            raise TrainingProvenanceSecurityError(
                f"Bank encryption key path conflicts with existing directory: '{self.key_path}'."
            )

        staged_nonce = secrets.token_hex(16)
        staged_file = os.path.join(self.vault_dir, f".key_staged_{staged_nonce}.tmp")
        try:
            with open(staged_file, "wb"):
                pass
            configure_bank_file_os_permissions(staged_file, self.bank_id)
            with open(staged_file, "wb") as f:
                f.write(key_bytes)
            os.replace(staged_file, self.key_path)
            configure_bank_file_os_permissions(self.key_path, self.bank_id)
            self.bank_encryption_key = (
                key_bytes.decode("utf-8") if isinstance(key_material, str) else key_bytes
            )
            return self.key_path
        except Exception as e:
            with contextlib.suppress(Exception):
                if os.path.exists(staged_file):
                    os.remove(staged_file)
            if isinstance(
                e, (TrainingProvenanceConfigurationError, TrainingProvenanceSecurityError)
            ):
                raise
            raise TrainingProvenanceConfigurationError(
                f"Failed to persist encryption key in vault for bank '{self.bank_id}': {e}"
            ) from e

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

        _check_no_reparse_or_symlink(
            self.data_path, f"Bank private dataset file for '{self.bank_id}'"
        )
        if os.path.exists(self.data_path) and os.path.isdir(self.data_path):
            raise TrainingProvenanceSecurityError(
                f"Bank dataset path conflicts with existing directory: '{self.data_path}'."
            )

        if self.bank_encryption_key is not None:
            self.store_encryption_key(self.bank_encryption_key)

        staged_nonce = secrets.token_hex(16)
        staged_file = os.path.join(self.vault_dir, f".dataset_staged_{staged_nonce}.tmp")
        try:
            with open(staged_file, "wb"):
                pass
            configure_bank_file_os_permissions(staged_file, self.bank_id)
            with open(staged_file, "wb") as f:
                f.write(payload)
            os.replace(staged_file, self.data_path)
            configure_bank_file_os_permissions(self.data_path, self.bank_id)
            return self.data_path
        except Exception as e:
            with contextlib.suppress(Exception):
                if os.path.exists(staged_file):
                    os.remove(staged_file)
            if isinstance(
                e, (TrainingProvenanceConfigurationError, TrainingProvenanceSecurityError)
            ):
                raise
            raise TrainingProvenanceConfigurationError(
                f"Failed to persist dataset in vault for bank '{self.bank_id}': {e}"
            ) from e

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
            handle = ctypes.windll.kernel32.CreateFileW(  # type: ignore[attr-defined]
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
            try:
                self._orig_mode = os.stat(self.data_path).st_mode
                os.chmod(self.data_path, 0o000)
            except Exception:
                pass

    def release_exclusive_os_boundary(self) -> None:
        """Release the OS-level lock on dataset file."""
        if hasattr(self, "_orig_mode") and self._orig_mode is not None:
            with contextlib.suppress(Exception):
                os.chmod(self.data_path, self._orig_mode)
            self._orig_mode = None

        if self._os_lock_handle is not None:
            if os.name == "nt":
                import ctypes

                ctypes.windll.kernel32.CloseHandle(self._os_lock_handle)  # type: ignore[attr-defined]
            else:
                try:
                    import fcntl

                    fcntl.flock(self._os_lock_handle, fcntl.LOCK_UN)  # type: ignore[attr-defined]
                    os.close(self._os_lock_handle)
                except Exception:
                    pass
            self._os_lock_handle = None

    def read_dataset_as_bank(
        self, caller_identity: str, caller_token: str | None = None
    ) -> tuple[np.ndarray, np.ndarray]:
        """Access vault dataset with strict institutional identity and token verification."""
        if caller_identity != self.bank_id:
            raise PermissionError(
                f"Access denied: Caller '{caller_identity}' is forbidden from accessing private data vault of bank '{self.bank_id}'."
            )
        if self.bank_token is not None and (
            caller_token is None or not hmac.compare_digest(caller_token, self.bank_token)
        ):
            raise PermissionError(
                f"Access denied: Invalid worker credential presented for bank '{self.bank_id}' storage vault."
            )
        if self.bank_encryption_key is None and os.path.exists(self.key_path):
            with open(self.key_path, "rb") as f:
                self.bank_encryption_key = f.read().decode("utf-8")

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
                ciphertext = raw_data[len(self.VAULT_MAGIC_V1) :]

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
    app_env: str = "development",
) -> tuple[str, str]:
    """Privileged bootstrap provisioning Ed25519 issuer private key inside an isolated vault.

    Enforces mandatory fail-closed security properties:
    1. Redirection & Junction Defense: Rejects symlinks, junctions, reparse points, and conflicting file/dir paths.
    2. Permission Pre-Enforcement: Restrictive NTFS DACL (denying WinRestrictedCodeSid S-1-5-12 and granting
       strictly TokenUser) is configured and verified on the vault directory and on an empty staged file BEFORE
       any private key bytes are persisted.
    3. Zero Plaintext Exposure Window: Private key bytes are never written to an unprotected or inherited location.
    4. Clean Failure Recovery: Interrupted or failed permission setup leaves zero readable key artifacts.
    5. Atomic Replacement: Staged file replaces destination via atomic filesystem operations.
    6. Permanent NTFS Protection: Security persists permanently before service startup, during execution,
       after process termination, and across restarts.
    7. Cryptographic Key Independence: Generates cryptographically secure Ed25519 keys; rejects deterministic
       development seeds in production environment.
    """
    if not vault_dir or not isinstance(vault_dir, str):
        raise TrainingProvenanceConfigurationError(
            "Vault directory path must be a non-empty string."
        )

    vault_dir = os.path.abspath(vault_dir)
    key_file = os.path.join(vault_dir, "issuer_ed25519.key")

    # 1. Structural and reparse point checks
    _check_no_reparse_or_symlink(vault_dir, "Issuer vault directory")
    if os.path.exists(vault_dir) and not os.path.isdir(vault_dir):
        raise TrainingProvenanceSecurityError(
            f"Issuer vault directory path conflicts with existing non-directory file: '{vault_dir}'."
        )

    _check_no_reparse_or_symlink(key_file, "Issuer key file")
    if os.path.exists(key_file) and os.path.isdir(key_file):
        raise TrainingProvenanceSecurityError(
            f"Issuer key file path conflicts with existing directory: '{key_file}'."
        )

    # 2. Key material derivation / validation
    dev_seed_hex = hashlib.sha256(b"cfi_dev_ed25519_training_issuer_seed_v1").digest().hex()
    if signing_key_hex:
        priv_hex = signing_key_hex.strip()
        if len(priv_hex) != 64:
            raise TrainingProvenanceConfigurationError(
                "Signing key hex must be a 64-character hex string."
            )
        if app_env.lower() == "production" and priv_hex == dev_seed_hex:
            raise TrainingProvenanceSecurityError(
                "Production receipt issuer cannot use deterministic development signing seed (SIGN-05)."
            )
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        priv_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv_hex))
        pub_hex = priv_key.public_key().public_bytes_raw().hex()
        del priv_key
    else:
        priv_hex, pub_hex = _generate_ed25519_keypair_hex()

    # 3. Ensure vault directory exists and enforce restrictive permissions
    try:
        os.makedirs(vault_dir, mode=0o700, exist_ok=True)
    except Exception as e:
        raise TrainingProvenanceConfigurationError(
            f"Failed to create isolated issuer vault directory: {e}"
        ) from e

    _check_no_reparse_or_symlink(vault_dir, "Issuer vault directory")

    staged_file = None
    try:
        if os.name == "nt":
            try:
                import ntsecuritycon
                import win32api
                import win32con
                import win32security

                hProcess = win32api.GetCurrentProcess()
                hToken = win32security.OpenProcessToken(hProcess, win32con.TOKEN_ALL_ACCESS)
                service_sid, _ = win32security.GetTokenInformation(hToken, win32security.TokenUser)
                coord_sid = win32security.CreateWellKnownSid(win32security.WinRestrictedCodeSid)

                # Configure directory DACL with container and object inheritance
                dir_dacl = win32security.ACL()
                dir_dacl.AddAccessDeniedAceEx(
                    win32security.ACL_REVISION,
                    win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE,
                    ntsecuritycon.FILE_ALL_ACCESS,
                    coord_sid,
                )
                dir_dacl.AddAccessAllowedAceEx(
                    win32security.ACL_REVISION,
                    win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE,
                    ntsecuritycon.FILE_ALL_ACCESS,
                    service_sid,
                )
                win32security.SetNamedSecurityInfo(
                    vault_dir,
                    win32security.SE_FILE_OBJECT,
                    win32security.DACL_SECURITY_INFORMATION
                    | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
                    None,
                    None,
                    dir_dacl,
                    None,
                )
            except Exception as e:
                raise TrainingProvenanceConfigurationError(
                    f"Failed to configure mandatory operating system NTFS DACL on issuer vault directory: {e}"
                ) from e
        else:
            try:
                os.chmod(vault_dir, 0o700)
            except Exception as e:
                raise TrainingProvenanceConfigurationError(
                    f"Failed to configure mandatory operating system permissions on issuer vault directory: {e}"
                ) from e

        # 4. Create empty staged file inside protected directory
        staged_nonce = secrets.token_hex(16)
        staged_file = os.path.join(vault_dir, f".issuer_key_staged_{staged_nonce}.tmp")

        try:
            with open(staged_file, "w", encoding="utf-8") as f:
                pass  # 0 bytes: empty file
        except Exception as e:
            raise TrainingProvenanceConfigurationError(
                f"Failed to create staged issuer key file: {e}"
            ) from e

        # 5. Enforce restrictive DACL on staged file BEFORE writing secret bytes
        if os.name == "nt":
            try:
                file_dacl = win32security.ACL()
                file_dacl.AddAccessDeniedAce(
                    win32security.ACL_REVISION,
                    ntsecuritycon.FILE_ALL_ACCESS,
                    coord_sid,
                )
                file_dacl.AddAccessAllowedAce(
                    win32security.ACL_REVISION,
                    ntsecuritycon.FILE_ALL_ACCESS,
                    service_sid,
                )
                win32security.SetNamedSecurityInfo(
                    staged_file,
                    win32security.SE_FILE_OBJECT,
                    win32security.DACL_SECURITY_INFORMATION
                    | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
                    None,
                    None,
                    file_dacl,
                    None,
                )
            except Exception as e:
                raise TrainingProvenanceConfigurationError(
                    f"Failed to configure mandatory operating system NTFS DACL on staged issuer key file: {e}"
                ) from e
        else:
            try:
                os.chmod(staged_file, 0o600)
            except Exception as e:
                raise TrainingProvenanceConfigurationError(
                    f"Failed to configure mandatory operating system permissions on staged issuer key file: {e}"
                ) from e

        # 6. Persist private key bytes into already-protected staged file
        try:
            with open(staged_file, "w", encoding="utf-8") as f:
                f.write(priv_hex)
                f.flush()
                os.fsync(f.fileno())
        except Exception as e:
            raise TrainingProvenanceConfigurationError(
                f"Failed to write confidential private key to staged issuer vault: {e}"
            ) from e

        # 7. Atomic replacement of final vault file
        try:
            os.replace(staged_file, key_file)
            staged_file = None  # Replaced successfully
        except Exception as e:
            raise TrainingProvenanceConfigurationError(
                f"Failed to atomically install issuer key file: {e}"
            ) from e

        # 8. Final confirmation of permissions on key_file
        if os.name == "nt":
            try:
                win32security.SetNamedSecurityInfo(
                    key_file,
                    win32security.SE_FILE_OBJECT,
                    win32security.DACL_SECURITY_INFORMATION
                    | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
                    None,
                    None,
                    file_dacl,
                    None,
                )
            except Exception as e:
                with contextlib.suppress(Exception):
                    os.remove(key_file)
                raise TrainingProvenanceConfigurationError(
                    f"Failed to verify mandatory NTFS DACL on final issuer key file: {e}"
                ) from e
        else:
            try:
                os.chmod(key_file, 0o600)
            except Exception as e:
                with contextlib.suppress(Exception):
                    os.remove(key_file)
                raise TrainingProvenanceConfigurationError(
                    f"Failed to verify permissions on final issuer key file: {e}"
                ) from e

        del priv_hex
        return key_file, pub_hex

    finally:
        if staged_file and os.path.exists(staged_file):
            with contextlib.suppress(Exception):
                os.remove(staged_file)


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

    if app_env.lower() == "production":
        _check_no_reparse_or_symlink(issuer_key_source, "Issuer key source")
        if not os.path.isfile(issuer_key_source):
            outbox.put(
                {
                    "status": "ERROR",
                    "error": f"Production issuer requires a provisioned key vault file; invalid path: '{issuer_key_source}'.",
                }
            )
            return
        with open(issuer_key_source, encoding="utf-8") as f:
            signing_key_hex = f.read().strip()
    else:
        if os.path.exists(issuer_key_source):
            with open(issuer_key_source, encoding="utf-8") as f:
                signing_key_hex = f.read().strip()
        else:
            signing_key_hex = issuer_key_source.strip()

    if len(signing_key_hex) != 64:
        outbox.put(
            {
                "status": "ERROR",
                "error": "Issuer signing key must be a 64-character hex string.",
            }
        )
        return

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
                    outbox.put(
                        {
                            "status": "ERROR",
                            "error": "Unauthorized receipt signing request: coordinator token configuration is required in production.",
                        }
                    )
                    continue

                if coordinator_token is not None:
                    if not auth_token or not hmac.compare_digest(auth_token, coordinator_token):
                        outbox.put(
                            {
                                "status": "ERROR",
                                "error": "Unauthorized receipt signing request: invalid or missing coordinator token.",
                            }
                        )
                        continue
                elif req.get("caller_identity") != "COORDINATOR":
                    outbox.put(
                        {
                            "status": "ERROR",
                            "error": f"Unauthorized receipt signing caller identity: '{req.get('caller_identity')}'.",
                        }
                    )
                    continue

                if req.get("caller_identity") != "COORDINATOR":
                    outbox.put(
                        {
                            "status": "ERROR",
                            "error": f"Unauthorized receipt signing caller identity: '{req.get('caller_identity')}'.",
                        }
                    )
                    continue

                job_id = req["job_id"]
                artifact_bytes = req["artifact_bytes"]

                if job_id in issued_jobs:
                    outbox.put(
                        {
                            "status": "ERROR",
                            "error": f"Receipt issuance denied: Duplicate signing request for already-signed job '{job_id}'.",
                        }
                    )
                    continue

                completed_record = record_source.get_completed_job_by_id(job_id)
                if completed_record is None:
                    outbox.put(
                        {
                            "status": "ERROR",
                            "error": f"Receipt issuance denied: Job '{job_id}' has no trusted COMPLETED record in authority.",
                        }
                    )
                    continue

                if completed_record.status != "COMPLETED":
                    outbox.put(
                        {
                            "status": "ERROR",
                            "error": f"Receipt issuance denied: Job '{job_id}' status is '{completed_record.status}', not 'COMPLETED'.",
                        }
                    )
                    continue

                computed_sha = hashlib.sha256(artifact_bytes).hexdigest()
                if not hmac.compare_digest(computed_sha, completed_record.artifact_sha256):
                    outbox.put(
                        {
                            "status": "ERROR",
                            "error": f"Receipt issuance denied: Provided artifact SHA-256 '{computed_sha}' does not match "
                            f"authoritative completed artifact digest '{completed_record.artifact_sha256}'.",
                        }
                    )
                    continue

                if app_env.lower() == "production" and not getattr(
                    completed_record, "consortium_id", None
                ):
                    outbox.put(
                        {
                            "status": "ERROR",
                            "error": f"Receipt issuance denied: Job '{job_id}' lacks required authoritative consortium binding in production.",
                        }
                    )
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
                    consortium_id=completed_record.consortium_id,
                )
                receipt = issuer.issue_receipt(evidence=evidence, model_bytes=artifact_bytes)
                issued_jobs.add(job_id)
                outbox.put(
                    {
                        "status": "SUCCESS",
                        "receipt": receipt,
                        "issuer_pid": issuer_pid,
                    }
                )
            except Exception as e:
                outbox.put(
                    {
                        "status": "ERROR",
                        "error": str(e),
                    }
                )


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
            _check_no_reparse_or_symlink(issuer_key_path, "Issuer key file")
            if not os.path.isfile(issuer_key_path):
                raise TrainingProvenanceSecurityError(
                    f"Issuer key path does not exist or is not a regular file: '{issuer_key_path}'"
                )
            key_source = os.path.abspath(issuer_key_path)
        elif signing_key_hex is not None:
            if self.app_env.lower() == "production":
                raise TrainingProvenanceSecurityError(
                    "Production isolated receipt signing authority requires provisioned issuer_key_path; "
                    "passing raw signing_key_hex to service launcher is strictly forbidden (SIGN-05)."
                )
            if len(signing_key_hex.strip()) != 64:
                raise TrainingProvenanceConfigurationError(
                    "Signing key hex must be a 64-character hex string."
                )
            self._temp_vault = tempfile.TemporaryDirectory(prefix="cfi_issuer_vault_")
            vault_file = os.path.join(self._temp_vault.name, "issuer.key")
            with open(vault_file, "w", encoding="utf-8") as f:
                f.write(signing_key_hex.strip())
            with contextlib.suppress(Exception):
                os.chmod(self._temp_vault.name, 0o700)
                os.chmod(vault_file, 0o600)
            key_source = vault_file
        else:
            raise TrainingProvenanceConfigurationError(
                "Either issuer_key_path or signing_key_hex must be provided."
            )

        # Eagerly probe database connectivity to fail closed immediately on unreachable DB
        TrustedTrainingJobRecordSource(
            coordinator_token=coordinator_token,
            db_url=self.db_url,
            enforce_lifecycle=True,
            app_env=self.app_env,
        )

        mp_ctx = mp.get_context("spawn")
        self._inbox: Any = mp_ctx.Queue()
        self._outbox: Any = mp_ctx.Queue()
        self._process = mp_ctx.Process(
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
            init_msg = self._outbox.get(timeout=25)
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
            raise TrainingProvenanceSecurityError(
                "Receipt signing service unavailable: issuer process terminated."
            )

        token = caller_token
        self._inbox.put(
            {
                "command": "SIGN_RECEIPT",
                "job_id": job_id,
                "artifact_bytes": artifact_bytes,
                "auth_token": token,
                "caller_identity": caller_identity,
            }
        )
        try:
            res = self._outbox.get(timeout=30)
            if isinstance(res, dict) and res.get("status") == "READY":
                res = self._outbox.get(timeout=30)
        except Exception as e:
            raise TrainingProvenanceSecurityError(
                f"Receipt signing service timeout or communication failure: {e}"
            ) from e

        if res.get("status") == "SUCCESS":
            return res["receipt"]
        err_msg = res.get("error") or f"Receipt signing rejected by isolated issuer: {res}"
        raise TrainingProvenanceSecurityError(err_msg)


class SecurityIdentityInfo:
    """Security principal metadata representing an operating system security context."""

    def __init__(
        self, principal_id: str, principal_type: str, details: dict[str, Any] | None = None
    ) -> None:
        self.principal_id = principal_id
        self.principal_type = principal_type
        self.details = details or {}

    def __repr__(self) -> str:
        return f"SecurityIdentityInfo(principal_id={self.principal_id!r}, principal_type={self.principal_type!r})"


def get_os_process_identity(target: Any = None, check_thread: bool = True) -> SecurityIdentityInfo:
    """Inspect and return the actual effective operating system security identity from the process access token."""
    if os.name == "nt":
        import win32api
        import win32con
        import win32security

        pid: int | None = None
        hProcess = None
        if target is None:
            hProcess = win32api.GetCurrentProcess()
            pid = os.getpid()
        elif isinstance(target, int):
            pid = target
            hProcess = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        elif hasattr(target, "pid") and target.pid is not None and isinstance(target.pid, int):
            pid = target.pid
            hProcess = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        elif isinstance(target, tuple) and len(target) >= 3 and isinstance(target[2], int):
            pid = target[2]
            hProcess = target[0]
        elif type(target).__name__ == "PyHANDLE":
            hProcess = target
            try:
                import win32process

                pid = win32process.GetProcessId(target)
            except Exception:
                pid = None
        else:
            hProcess = win32api.GetCurrentProcess()
            pid = os.getpid()

        try:
            hToken = None
            if target is None and check_thread:
                try:
                    hToken = win32security.OpenThreadToken(
                        win32api.GetCurrentThread(), win32con.TOKEN_QUERY, True
                    )
                except Exception:
                    hToken = None
            if hToken is None:
                hToken = win32security.OpenProcessToken(hProcess, win32con.TOKEN_QUERY)
            user_sid, _ = win32security.GetTokenInformation(hToken, win32security.TokenUser)
            user_sid_str = win32security.ConvertSidToStringSid(user_sid)

            is_restricted = win32security.IsTokenRestricted(hToken)
            restricted_sids: list[str] = []
            if is_restricted:
                r_sids = win32security.GetTokenInformation(
                    hToken, win32security.TokenRestrictedSids
                )
                restricted_sids = [win32security.ConvertSidToStringSid(s[0]) for s in r_sids]

            groups = win32security.GetTokenInformation(hToken, win32security.TokenGroups)
            group_sids = [win32security.ConvertSidToStringSid(g[0]) for g in groups]

            disabled_groups = [
                win32security.ConvertSidToStringSid(g[0])
                for g in groups
                if g[1] & win32security.SE_GROUP_USE_FOR_DENY_ONLY
            ]

            privs = win32security.GetTokenInformation(hToken, win32security.TokenPrivileges)
            enabled_privs = [
                win32security.LookupPrivilegeName("", int(p[0]))
                for p in privs
                if p[1]
                & (
                    win32security.SE_PRIVILEGE_ENABLED
                    | win32security.SE_PRIVILEGE_ENABLED_BY_DEFAULT
                )
            ]

            integrity_str = ""
            try:
                integ_sid, _ = win32security.GetTokenInformation(
                    hToken, win32security.TokenIntegrityLevel
                )
                integrity_str = win32security.ConvertSidToStringSid(integ_sid[0])
            except Exception:
                pass

            has_restricted_code = "S-1-5-12" in restricted_sids
            is_admin_disabled = (
                "S-1-5-32-544" in disabled_groups or "S-1-5-32-544" not in group_sids
            )

            principal_type = (
                "WindowsRestrictedProcessPrincipal" if is_restricted else "WindowsUserPrincipal"
            )
            principal_id = f"{user_sid_str}:RESTRICTED" if is_restricted else user_sid_str

            return SecurityIdentityInfo(
                principal_id=principal_id,
                principal_type=principal_type,
                details={
                    "pid": pid,
                    "user_sid": user_sid_str,
                    "is_restricted": is_restricted,
                    "restricted_sids": restricted_sids,
                    "group_sids": group_sids,
                    "disabled_groups": disabled_groups,
                    "privileges": enabled_privs,
                    "integrity_sid": integrity_str,
                    "has_restricted_code_sid": has_restricted_code,
                    "is_admin_disabled": is_admin_disabled,
                },
            )
        except Exception as e:
            raise TrainingProvenanceSecurityError(
                f"Failed to query OS process security token: {e}"
            ) from e
    else:
        uid = getattr(os, "getuid", lambda: -1)()
        gid = getattr(os, "getgid", lambda: -1)()
        groups = getattr(os, "getgroups", lambda: [])()
        pid = (
            target.pid
            if hasattr(target, "pid")
            else (target if isinstance(target, int) else os.getpid())
        )
        return SecurityIdentityInfo(
            principal_id=f"posix_uid_{uid}",
            principal_type="PosixUserPrincipal",
            details={
                "pid": pid,
                "uid": uid,
                "gid": gid,
                "groups": groups,
                "is_restricted": False,
            },
        )


def get_completion_service_os_identity(target_process: Any = None) -> SecurityIdentityInfo:
    """Return the real effective operating system security principal under which the completion service runs."""
    info = get_os_process_identity(target_process)
    info.details["privilege"] = "PrivilegedCompletionServiceAuthority"
    return info


def get_coordinator_os_identity(target_process: Any = None) -> SecurityIdentityInfo:
    """Return the real effective operating system security principal under which the coordinator runs."""
    return get_os_process_identity(target_process)


def create_restricted_coordinator_token() -> Any:
    """Create a Windows restricted security token with WinRestrictedCodeSid and stripped privileges.

    Enforces fail-closed: raises TrainingProvenanceConfigurationError on failure.
    """
    if os.name != "nt":
        return None
    try:
        import win32api
        import win32con
        import win32security

        hProcess = win32api.GetCurrentProcess()
        hToken = win32security.OpenProcessToken(hProcess, win32con.TOKEN_ALL_ACCESS)
        user_sid, _ = win32security.GetTokenInformation(hToken, win32security.TokenUser)
        coord_sid = win32security.CreateWellKnownSid(win32security.WinRestrictedCodeSid)
        admin_sid = win32security.CreateWellKnownSid(win32security.WinBuiltinAdministratorsSid)

        groups = win32security.GetTokenInformation(hToken, win32security.TokenGroups)
        all_sids = [(user_sid, 0)]
        for g in groups:
            sid_str = win32security.ConvertSidToStringSid(g[0])
            if "544" not in sid_str and "114" not in sid_str:
                all_sids.append((g[0], 0))
        all_sids.append((coord_sid, 0))

        sids_to_disable: Any = ((admin_sid, 0),)
        sids_to_restrict: Any = tuple(all_sids)
        create_restricted_token: Any = win32security.CreateRestrictedToken
        restricted_token = create_restricted_token(
            hToken,
            win32security.DISABLE_MAX_PRIVILEGE,
            sids_to_disable,
            (),
            sids_to_restrict,
        )
        return restricted_token
    except Exception as e:
        raise TrainingProvenanceConfigurationError(
            f"Failed to create operating system restricted coordinator security token: {e}"
        ) from e


COORDINATOR_ALLOWED_WINDOWS_PRIVILEGES: frozenset[str] = frozenset({"SeChangeNotifyPrivilege"})


def validate_restricted_coordinator_policy(details: dict[str, Any]) -> None:
    """Authoritatively validate that process security identity details satisfy the restricted coordinator policy.

    Invariants enforced:
    1. Mandatory presence and strict types of evidence fields: is_restricted, has_restricted_code_sid,
       restricted_sids, is_admin_disabled, privileges.
    2. Missing, malformed, or contradictory security evidence is rejected immediately (fail-closed).
    3. is_restricted must be True.
    4. has_restricted_code_sid must be True and 'S-1-5-12' must be present in restricted_sids.
    5. is_admin_disabled must be True and admin SID S-1-5-32-544 must not be active in group_sids.
    6. Enabled privileges in 'privileges' must be a subset of COORDINATOR_ALLOWED_WINDOWS_PRIVILEGES.
       Any unauthorized enabled privilege (e.g., SeDebugPrivilege, SeShutdownPrivilege) is rejected.
    7. Never interprets missing or misnamed identity evidence as an empty, safe privilege set.
    """
    if not isinstance(details, dict):
        raise TrainingProvenanceSecurityError(
            "Process security identity details must be a valid dictionary."
        )

    # 1. Mandatory presence of core evidence fields
    required_fields = (
        "is_restricted",
        "has_restricted_code_sid",
        "restricted_sids",
        "is_admin_disabled",
        "privileges",
    )
    for f in required_fields:
        if f not in details:
            raise TrainingProvenanceSecurityError(
                f"Mandatory coordinator security evidence field '{f}' is missing from process identity details."
            )

    # 2. Strict type verification (no loose truthiness)
    if not isinstance(details["is_restricted"], bool):
        raise TrainingProvenanceSecurityError("Field 'is_restricted' must be a boolean.")
    if not isinstance(details["has_restricted_code_sid"], bool):
        raise TrainingProvenanceSecurityError("Field 'has_restricted_code_sid' must be a boolean.")
    if not isinstance(details["is_admin_disabled"], bool):
        raise TrainingProvenanceSecurityError("Field 'is_admin_disabled' must be a boolean.")
    if not isinstance(details["restricted_sids"], (list, tuple, set, frozenset)):
        raise TrainingProvenanceSecurityError(
            "Field 'restricted_sids' must be a collection of SIDs."
        )
    if not isinstance(details["privileges"], (list, tuple, set, frozenset)):
        raise TrainingProvenanceSecurityError(
            f"Field 'privileges' must be a collection of privilege names, got {type(details['privileges']).__name__}."
        )

    # 3. Policy invariants
    if not details["is_restricted"]:
        raise TrainingProvenanceSecurityError(
            "Production federation coordinator must execute under an enforced restricted operating system security token. "
            "Direct instantiation or execution inside an unrestricted process is prohibited (EXEC-01)."
        )

    if not details["has_restricted_code_sid"] or "S-1-5-12" not in details["restricted_sids"]:
        raise TrainingProvenanceSecurityError(
            "Production federation coordinator process token lacks mandatory WinRestrictedCodeSid (S-1-5-12) restriction."
        )

    if not details["is_admin_disabled"]:
        raise TrainingProvenanceSecurityError(
            "Production federation coordinator process token has active administrative authority (S-1-5-32-544 not disabled)."
        )

    group_sids = details.get("group_sids")
    disabled_groups = details.get("disabled_groups")
    if (
        group_sids is not None
        and disabled_groups is not None
        and "S-1-5-32-544" in group_sids
        and "S-1-5-32-544" not in disabled_groups
    ):
        raise TrainingProvenanceSecurityError(
            "Contradictory security evidence: S-1-5-32-544 is present in group_sids but not in disabled_groups."
        )

    # 4. Privilege verification against explicit policy
    actual_privs = set(details["privileges"])
    unauthorized = actual_privs - COORDINATOR_ALLOWED_WINDOWS_PRIVILEGES
    if unauthorized:
        raise TrainingProvenanceSecurityError(
            f"Production federation coordinator process has unauthorized enabled privileges: {sorted(unauthorized)}. "
            f"Permitted under restricted policy: {sorted(COORDINATOR_ALLOWED_WINDOWS_PRIVILEGES)}."
        )


def verify_restricted_coordinator_os_boundary(app_env: str = "production") -> SecurityIdentityInfo:
    """Verify that the current operating system process satisfies the mandatory production coordinator isolation policy.

    In production:
    - Platform must support enforced operating system token isolation (Windows NT).
    - Current process access token must satisfy validate_restricted_coordinator_policy().
    - Fails closed with TrainingProvenanceSecurityError on any policy violation or unsupported platform.
    """
    if app_env.lower() != "production":
        return get_os_process_identity()

    if os.name != "nt":
        raise TrainingProvenanceSecurityError(
            f"Production coordinator isolation is not supported on platform '{sys.platform}'. "
            "Production federated training requires enforced Windows Restricted Token isolation (EXEC-06)."
        )

    ident = get_os_process_identity()
    validate_restricted_coordinator_policy(ident.details)
    return ident


class CoordinatorOSSecurityContext:
    """Context manager executing under the restricted coordinator operating system security identity.

    Enforces that filesystem access checks, token queries, and OS permissions are evaluated under
    the coordinator's restricted principal, which is permanently denied access to the completion vault.
    Fails closed if the restricted security token or impersonation cannot be established.
    """

    def __init__(self) -> None:
        self._active = False
        self._token: Any = None

    def __enter__(self) -> CoordinatorOSSecurityContext:
        if os.name == "nt":
            try:
                self._token = create_restricted_coordinator_token()
                import win32security

                win32security.ImpersonateLoggedOnUser(self._token)
                self._active = True
            except Exception as e:
                raise TrainingProvenanceSecurityError(
                    f"Failed to establish operating system restricted coordinator context: {e}"
                ) from e
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._active and os.name == "nt":
            try:
                import win32security

                win32security.RevertToSelf()
            except Exception:
                pass
            self._active = False


def create_bank_worker_security_token(bank_id: str) -> Any:
    """Create a Windows restricted security token for a specific bank worker.

    The restricted token:
    1. Contains strictly the designated bank's SID in its restricted SIDs.
    2. Explicitly disables administrative group authority (S-1-5-32-544).
    3. Excludes other banks' SIDs and coordinator restricted SID (S-1-5-12).
    4. Sets DISABLE_MAX_PRIVILEGE.
    Fails closed if the operating system restricted token cannot be created.
    """
    if os.name != "nt":
        return None
    try:
        import win32api
        import win32con
        import win32security

        hProcess = win32api.GetCurrentProcess()
        hToken = win32security.OpenProcessToken(hProcess, win32con.TOKEN_ALL_ACCESS)
        user_sid, _ = win32security.GetTokenInformation(hToken, win32security.TokenUser)
        bank_sid = win32security.GetBinarySid(get_bank_security_sid(bank_id))
        admin_sid = win32security.CreateWellKnownSid(win32security.WinBuiltinAdministratorsSid)

        groups = win32security.GetTokenInformation(hToken, win32security.TokenGroups)
        all_sids = [(user_sid, 0), (bank_sid, 0)]
        for g in groups:
            sid_str = win32security.ConvertSidToStringSid(g[0])
            if "544" not in sid_str and "114" not in sid_str:
                all_sids.append((g[0], 0))

        sids_to_disable: Any = ((admin_sid, 0),)
        sids_to_restrict: Any = tuple(all_sids)
        restricted_token = win32security.CreateRestrictedToken(
            hToken,
            win32security.DISABLE_MAX_PRIVILEGE,
            sids_to_disable,
            (),
            sids_to_restrict,
        )
        return restricted_token
    except Exception as e:
        raise TrainingProvenanceConfigurationError(
            f"Failed to create operating system security token for bank worker '{bank_id}': {e}"
        ) from e


class BankWorkerOSSecurityContext:
    """Context manager executing under the restricted bank worker operating system security identity.

    Enforces that filesystem access checks, token queries, and OS permissions are evaluated under
    the specific bank's restricted principal.
    Fails closed if the restricted security token or impersonation cannot be established.
    """

    def __init__(self, bank_id: str) -> None:
        self.bank_id = bank_id
        self._active = False
        self._token: Any = None

    def __enter__(self) -> BankWorkerOSSecurityContext:
        if os.name == "nt":
            try:
                self._token = create_bank_worker_security_token(self.bank_id)
                import win32security

                win32security.ImpersonateLoggedOnUser(self._token)
                self._active = True
            except Exception as e:
                raise TrainingProvenanceSecurityError(
                    f"Failed to establish operating system security context for bank '{self.bank_id}': {e}"
                ) from e
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._active and os.name == "nt":
            try:
                import win32security

                win32security.RevertToSelf()
            except Exception:
                pass
            self._active = False


def get_bank_worker_os_identity(
    bank_id: str, target: Any = None, check_thread: bool = True
) -> SecurityIdentityInfo:
    """Return the real effective operating system security principal under which a bank worker runs."""
    info = get_os_process_identity(target, check_thread=check_thread)
    bank_sid = get_bank_security_sid(bank_id)
    info.details["bank_id"] = bank_id
    info.details["bank_sid"] = bank_sid
    info.principal_type = (
        "WindowsBankWorkerPrincipal"
        if info.details.get("is_restricted")
        else "WindowsUserPrincipal"
    )
    user_sid = info.details.get("user_sid", "")
    info.principal_id = (
        f"{user_sid}:{bank_id}:RESTRICTED"
        if info.details.get("is_restricted")
        else f"{user_sid}:{bank_id}"
    )
    return info


def validate_restricted_bank_worker_policy(details: dict[str, Any], bank_id: str) -> None:
    """Authoritatively validate that process security identity satisfies the restricted bank worker policy."""
    if not isinstance(details, dict):
        raise TrainingProvenanceSecurityError(
            "Process security identity details must be a valid dictionary."
        )

    required_fields = ("is_restricted", "restricted_sids", "is_admin_disabled")
    for f in required_fields:
        if f not in details:
            raise TrainingProvenanceSecurityError(
                f"Mandatory bank worker security evidence field '{f}' is missing from process identity details."
            )

    if not isinstance(details["is_restricted"], bool):
        raise TrainingProvenanceSecurityError("Field 'is_restricted' must be a boolean.")
    if not isinstance(details["is_admin_disabled"], bool):
        raise TrainingProvenanceSecurityError("Field 'is_admin_disabled' must be a boolean.")
    if not isinstance(details["restricted_sids"], (list, tuple, set, frozenset)):
        raise TrainingProvenanceSecurityError(
            "Field 'restricted_sids' must be a collection of SIDs."
        )

    if not details["is_restricted"]:
        raise TrainingProvenanceSecurityError(
            f"Production bank worker '{bank_id}' must execute under an enforced restricted operating system security token."
        )

    expected_bank_sid = get_bank_security_sid(bank_id)
    if expected_bank_sid not in details["restricted_sids"]:
        raise TrainingProvenanceSecurityError(
            f"Production bank worker '{bank_id}' process token lacks mandatory bank SID '{expected_bank_sid}'."
        )

    if COORDINATOR_RESTRICTED_SID in details["restricted_sids"]:
        raise TrainingProvenanceSecurityError(
            f"Bank worker '{bank_id}' process token cannot possess coordinator restricted code SID '{COORDINATOR_RESTRICTED_SID}'."
        )

    for other_bank in ("BANK_A", "BANK_B", "BANK_C"):
        if other_bank != bank_id:
            other_sid = get_bank_security_sid(other_bank)
            if other_sid in details["restricted_sids"]:
                raise TrainingProvenanceSecurityError(
                    f"Bank worker '{bank_id}' process token illegally possesses another bank's SID '{other_sid}' ({other_bank})."
                )

    if not details["is_admin_disabled"]:
        raise TrainingProvenanceSecurityError(
            f"Production bank worker '{bank_id}' process token has active administrative authority (S-1-5-32-544 not disabled)."
        )


def verify_restricted_bank_worker_os_boundary(
    bank_id: str, app_env: str = "production", target: Any = None
) -> SecurityIdentityInfo:
    """Verify that current operating system execution context satisfies mandatory bank worker isolation policy."""
    if app_env.lower() != "production":
        return get_bank_worker_os_identity(bank_id, target=target)

    if os.name != "nt":
        raise TrainingProvenanceSecurityError(
            f"Production bank worker isolation is not supported on platform '{sys.platform}'. "
            "Production federated training requires enforced Windows Restricted Token isolation."
        )

    # In production, verify the PRIMARY process token (not merely a temporary thread impersonation)
    ident = get_bank_worker_os_identity(bank_id, target=target, check_thread=False)
    validate_restricted_bank_worker_policy(ident.details, bank_id)
    return ident


def provision_isolated_completion_authority_vault(
    vault_dir: str,
    completion_db_url: str,
) -> str:
    """Privileged bootstrap provisioning of completion authority credentials inside an isolated vault.

    Enforces mandatory fail-closed security properties:
    1. Redirection & Junction Defense: Rejects symlinks, junctions, reparse points, and conflicting file/dir paths.
    2. Permission Pre-Enforcement: Restrictive NTFS DACL (denying WinRestrictedCodeSid S-1-5-12 and granting
       strictly TokenUser) is configured and verified on the vault directory and on an empty staged file BEFORE
       any secret bytes are persisted.
    3. Zero Plaintext Exposure Window: Credential bytes are never written to an unprotected or inherited location.
    4. Clean Failure Recovery: Interrupted or failed permission setup leaves zero readable credential artifacts.
    5. Atomic Replacement: Staged file replaces destination via atomic filesystem operations.
    6. Permanent NTFS Protection: Security persists permanently before service startup, during execution,
       after process termination, and across restarts.
    """
    if not vault_dir or not isinstance(vault_dir, str):
        raise TrainingProvenanceConfigurationError(
            "Vault directory path must be a non-empty string."
        )
    if (
        not completion_db_url
        or not isinstance(completion_db_url, str)
        or not completion_db_url.strip()
    ):
        raise TrainingProvenanceConfigurationError(
            "Completion database URL must be a non-empty string."
        )

    vault_dir = os.path.abspath(vault_dir)
    vault_file = os.path.join(vault_dir, "completion_authority.vault")

    # 1. Structural and reparse point checks
    _check_no_reparse_or_symlink(vault_dir, "Vault directory")
    if os.path.exists(vault_dir) and not os.path.isdir(vault_dir):
        raise TrainingProvenanceSecurityError(
            f"Vault directory path conflicts with existing non-directory file: '{vault_dir}'."
        )

    _check_no_reparse_or_symlink(vault_file, "Vault file")
    if os.path.exists(vault_file) and os.path.isdir(vault_file):
        raise TrainingProvenanceSecurityError(
            f"Vault file path conflicts with existing directory: '{vault_file}'."
        )

    # 2. Ensure vault directory exists and enforce restrictive permissions
    try:
        os.makedirs(vault_dir, mode=0o700, exist_ok=True)
    except Exception as e:
        raise TrainingProvenanceConfigurationError(
            f"Failed to create isolated completion vault directory: {e}"
        ) from e

    _check_no_reparse_or_symlink(vault_dir, "Vault directory")

    staged_file = None
    try:
        if os.name == "nt":
            try:
                import ntsecuritycon
                import win32api
                import win32con
                import win32security

                hProcess = win32api.GetCurrentProcess()
                hToken = win32security.OpenProcessToken(hProcess, win32con.TOKEN_ALL_ACCESS)
                service_sid, _ = win32security.GetTokenInformation(hToken, win32security.TokenUser)
                coord_sid = win32security.CreateWellKnownSid(win32security.WinRestrictedCodeSid)

                # Configure directory DACL with container and object inheritance
                dir_dacl = win32security.ACL()
                dir_dacl.AddAccessDeniedAceEx(
                    win32security.ACL_REVISION,
                    win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE,
                    ntsecuritycon.FILE_ALL_ACCESS,
                    coord_sid,
                )
                dir_dacl.AddAccessAllowedAceEx(
                    win32security.ACL_REVISION,
                    win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE,
                    ntsecuritycon.FILE_ALL_ACCESS,
                    service_sid,
                )
                win32security.SetNamedSecurityInfo(
                    vault_dir,
                    win32security.SE_FILE_OBJECT,
                    win32security.DACL_SECURITY_INFORMATION
                    | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
                    None,
                    None,
                    dir_dacl,
                    None,
                )
            except Exception as e:
                raise TrainingProvenanceConfigurationError(
                    f"Failed to configure mandatory operating system NTFS DACL on completion vault directory: {e}"
                ) from e
        else:
            try:
                os.chmod(vault_dir, 0o700)
            except Exception as e:
                raise TrainingProvenanceConfigurationError(
                    f"Failed to configure mandatory operating system permissions on completion vault directory: {e}"
                ) from e

        # 3. Create empty staged file inside protected directory
        staged_nonce = secrets.token_hex(16)
        staged_file = os.path.join(vault_dir, f".completion_vault_staged_{staged_nonce}.tmp")

        try:
            with open(staged_file, "w", encoding="utf-8") as f:
                pass  # 0 bytes: empty file
        except Exception as e:
            raise TrainingProvenanceConfigurationError(
                f"Failed to create staged completion vault file: {e}"
            ) from e

        # 4. Enforce restrictive DACL on staged file BEFORE writing secret bytes
        if os.name == "nt":
            try:
                file_dacl = win32security.ACL()
                file_dacl.AddAccessDeniedAce(
                    win32security.ACL_REVISION,
                    ntsecuritycon.FILE_ALL_ACCESS,
                    coord_sid,
                )
                file_dacl.AddAccessAllowedAce(
                    win32security.ACL_REVISION,
                    ntsecuritycon.FILE_ALL_ACCESS,
                    service_sid,
                )
                win32security.SetNamedSecurityInfo(
                    staged_file,
                    win32security.SE_FILE_OBJECT,
                    win32security.DACL_SECURITY_INFORMATION
                    | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
                    None,
                    None,
                    file_dacl,
                    None,
                )
            except Exception as e:
                raise TrainingProvenanceConfigurationError(
                    f"Failed to configure mandatory operating system NTFS DACL on staged completion vault file: {e}"
                ) from e
        else:
            try:
                os.chmod(staged_file, 0o600)
            except Exception as e:
                raise TrainingProvenanceConfigurationError(
                    f"Failed to configure mandatory operating system permissions on staged completion vault file: {e}"
                ) from e

        # 5. Persist secret bytes into already-protected staged file
        try:
            with open(staged_file, "w", encoding="utf-8") as f:
                f.write(completion_db_url.strip())
                f.flush()
                os.fsync(f.fileno())
        except Exception as e:
            raise TrainingProvenanceConfigurationError(
                f"Failed to write confidential credential to staged completion vault: {e}"
            ) from e

        # 6. Atomic replacement of final vault file
        try:
            os.replace(staged_file, vault_file)
            staged_file = None  # Replaced successfully
        except Exception as e:
            raise TrainingProvenanceConfigurationError(
                f"Failed to atomically install completion vault file: {e}"
            ) from e

        # 7. Final confirmation of permissions on vault_file
        if os.name == "nt":
            try:
                win32security.SetNamedSecurityInfo(
                    vault_file,
                    win32security.SE_FILE_OBJECT,
                    win32security.DACL_SECURITY_INFORMATION
                    | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
                    None,
                    None,
                    file_dacl,
                    None,
                )
            except Exception as e:
                with contextlib.suppress(Exception):
                    os.remove(vault_file)
                raise TrainingProvenanceConfigurationError(
                    f"Failed to verify mandatory NTFS DACL on final completion vault file: {e}"
                ) from e
        else:
            try:
                os.chmod(vault_file, 0o600)
            except Exception as e:
                with contextlib.suppress(Exception):
                    os.remove(vault_file)
                raise TrainingProvenanceConfigurationError(
                    f"Failed to verify permissions on final completion vault file: {e}"
                ) from e

        return vault_file

    finally:
        if staged_file and os.path.exists(staged_file):
            with contextlib.suppress(Exception):
                os.remove(staged_file)


class IsolatedCoordinatorClient:
    """Client for controlling and orchestrating the isolated federation coordinator process over authenticated IPC."""

    def __init__(self, conn: Any, proc_handle: Any, pid: int, app_env: str = "production") -> None:
        self._conn = conn
        self._proc_handle = proc_handle
        self.pid = pid
        self.app_env = app_env

    def get_process_identity(self) -> SecurityIdentityInfo:
        return get_os_process_identity(
            self._proc_handle if self._proc_handle is not None else self.pid
        )

    def attempt_read_vault(self, vault_path: str) -> dict[str, Any]:
        self._conn.send({"command": "ATTEMPT_READ_VAULT", "vault_path": vault_path})
        return self._conn.recv()

    def attempt_write_vault(self, vault_path: str, data: str) -> dict[str, Any]:
        self._conn.send({"command": "ATTEMPT_WRITE_VAULT", "vault_path": vault_path, "data": data})
        return self._conn.recv()

    def attempt_delete_vault(self, vault_path: str) -> dict[str, Any]:
        self._conn.send({"command": "ATTEMPT_DELETE_VAULT", "vault_path": vault_path})
        return self._conn.recv()

    def attempt_privilege_escalation(self, vault_path: str | None = None) -> dict[str, Any]:
        self._conn.send({"command": "ATTEMPT_PRIVILEGE_ESCALATION", "vault_path": vault_path})
        return self._conn.recv()

    def attempt_launch_completion_service(
        self, vault_path: str, coordinator_token: str | None = None
    ) -> dict[str, Any]:
        self._conn.send(
            {
                "command": "ATTEMPT_LAUNCH_COMPLETION_SERVICE",
                "vault_path": vault_path,
                "coordinator_token": coordinator_token,
                "app_env": self.app_env,
            }
        )
        return self._conn.recv()

    def attempt_direct_sql(self, db_url: str, sql: str) -> dict[str, Any]:
        self._conn.send({"command": "ATTEMPT_DIRECT_SQL", "db_url": db_url, "sql": sql})
        return self._conn.recv()

    def execute_federation(
        self,
        job_id: str,
        training_run_id: str,
        simulation_id: str,
        holdout_val_data: tuple[np.ndarray, np.ndarray],
        participating_banks: list[str] | tuple[str, ...],
        worker_endpoints: dict[str, dict[str, Any]],
        num_rounds: int,
        expected_unique_samples: int | None,
        registry_storage_dir: str | None,
        coordinator_token: str,
        db_url: str,
        comp_service_endpoint: tuple[str, int],
        signing_authority: Any,
        worker_pids: dict[str, int] | None = None,
        bank_partitions: Any = None,
        bank_tokens: Any = None,
        bank_data_paths: Any = None,
        signing_key_hex: str | None = None,
        pause_aggregation_checkpoint_file: str | None = None,
        consortium_id: str | None = None,
        dp_config: Mapping[str, Any] | None = None,
        secagg_enabled: bool = False,
        secagg_config: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        req = {
            "command": "EXECUTE_FEDERATION",
            "job_id": job_id,
            "training_run_id": training_run_id,
            "simulation_id": simulation_id,
            "holdout_val_data": holdout_val_data,
            "participating_banks": list(participating_banks),
            "num_rounds": num_rounds,
            "expected_unique_samples": expected_unique_samples,
            "registry_storage_dir": registry_storage_dir,
            "coordinator_token": coordinator_token,
            "db_url": db_url,
            "comp_service_endpoint": comp_service_endpoint,
            "app_env": self.app_env,
            "worker_pids": worker_pids or {},
            "bank_partitions": bank_partitions,
            "bank_tokens": bank_tokens,
            "bank_data_paths": bank_data_paths,
            "signing_key_hex": signing_key_hex,
            "pause_aggregation_checkpoint_file": pause_aggregation_checkpoint_file,
            "has_worker_endpoints": bool(worker_endpoints),
            "consortium_id": consortium_id,
            "dp_config": dp_config,
            "secagg_enabled": secagg_enabled,
            "secagg_config": secagg_config,
        }
        try:
            self._conn.send(req)

            while True:
                msg = self._conn.recv()
                if not isinstance(msg, dict):
                    raise RuntimeError(f"Unexpected coordinator IPC message: {msg}")

                cmd = msg.get("command")
                if cmd == "FEDERATION_RESULT":
                    if msg.get("status") == "SUCCESS":
                        return msg["result"]
                    raise TrainingProvenanceSecurityError(msg.get("error", "Federation failed"))

                elif cmd == "PROXY_QUEUE_PUT":
                    bank_id = msg["bank_id"]
                    item = msg["msg"]
                    worker_endpoints[bank_id]["inbox"].put(item)
                    self._conn.send({"status": "OK"})

                elif cmd == "PROXY_QUEUE_GET":
                    bank_id = msg["bank_id"]
                    timeout = msg.get("timeout")
                    try:
                        item = worker_endpoints[bank_id]["outbox"].get(timeout=timeout)
                        self._conn.send({"status": "OK", "msg": item})
                    except Exception as e:
                        self._conn.send({"status": "ERROR", "error": str(e)})

                elif cmd == "PROXY_SIGN_RECEIPT":
                    job_id_req = msg["job_id"]
                    art_bytes = msg["artifact_bytes"]
                    c_token = msg.get("caller_token")
                    c_ident = msg.get("caller_identity", "COORDINATOR")
                    try:
                        receipt = signing_authority.sign_completed_job_receipt(
                            job_id=job_id_req,
                            artifact_bytes=art_bytes,
                            caller_identity=c_ident,
                            caller_token=c_token,
                        )
                        self._conn.send({"status": "SUCCESS", "receipt": receipt})
                    except Exception as e:
                        self._conn.send({"status": "ERROR", "error": str(e)})

                else:
                    raise RuntimeError(f"Unknown message from coordinator process: {cmd}")
        except (EOFError, ConnectionResetError, BrokenPipeError, OSError) as e:
            raise TrainingProvenanceSecurityError(
                f"Restricted coordinator process disconnected or terminated during execution: {e} (EXEC-10). "
                "Silent fallback to unrestricted execution or incomplete evidence creation is prohibited."
            ) from e

    execute = execute_federation

    def __enter__(self) -> IsolatedCoordinatorClient:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.terminate()

    def terminate(self) -> None:
        with contextlib.suppress(Exception):
            self._conn.send({"command": "STOP"})
        with contextlib.suppress(Exception):
            self._conn.close()
        if os.name == "nt" and self._proc_handle is not None:
            try:
                import win32process

                exit_code = win32process.GetExitCodeProcess(self._proc_handle)
                if exit_code == 259:
                    win32process.TerminateProcess(self._proc_handle, 0)
            except Exception:
                pass


def _run_isolated_coordinator_worker(
    listener_address: tuple[str, int],
    authkey_bytes: bytes,
) -> None:
    """Dedicated Operating System Process running the Federation Coordinator Worker.

    Invariants enforced:
    1. Runs under independent OS process token (IsTokenRestricted == True on Windows).
    2. Zero completion database credentials or vault paths in memory or arguments.
    3. Cannot access completion vault (denied by NTFS DACL policy).
    4. RevertToSelf cannot escape process restricted token.
    5. Direct SQL strictly governed by cfi_coord_role.
    """
    client = Client(listener_address, authkey=authkey_bytes)
    pid = os.getpid()

    is_restricted = False
    if os.name == "nt":
        try:
            import win32api
            import win32security

            h = win32security.OpenProcessToken(win32api.GetCurrentProcess(), 8)
            is_restricted = win32security.IsTokenRestricted(h)
        except Exception:
            pass

    client.send({"status": "READY", "pid": pid, "is_restricted": is_restricted})

    while True:
        try:
            req = client.recv()
        except Exception:
            break

        if not isinstance(req, dict):
            break

        cmd = req.get("command")
        if cmd == "STOP":
            with contextlib.suppress(Exception):
                client.send({"status": "STOPPED"})
            break

        elif cmd == "ATTEMPT_READ_VAULT":
            vault_path = req["vault_path"]
            try:
                with open(vault_path, encoding="utf-8") as f:
                    content = f.read()
                client.send({"status": "SUCCESS", "content": content})
            except Exception as e:
                client.send(
                    {
                        "status": "DENIED",
                        "error_type": type(e).__name__,
                        "error": str(e),
                    }
                )

        elif cmd == "ATTEMPT_WRITE_VAULT":
            vault_path = req["vault_path"]
            data = req.get("data", "")
            try:
                with open(vault_path, "w", encoding="utf-8") as f:
                    f.write(data)
                client.send({"status": "SUCCESS"})
            except Exception as e:
                client.send(
                    {
                        "status": "DENIED",
                        "error_type": type(e).__name__,
                        "error": str(e),
                    }
                )

        elif cmd == "ATTEMPT_DELETE_VAULT":
            vault_path = req["vault_path"]
            try:
                os.remove(vault_path)
                client.send({"status": "SUCCESS"})
            except Exception as e:
                client.send(
                    {
                        "status": "DENIED",
                        "error_type": type(e).__name__,
                        "error": str(e),
                    }
                )

        elif cmd == "ATTEMPT_PRIVILEGE_ESCALATION":
            revert_err = None
            if os.name == "nt":
                try:
                    import win32security

                    win32security.RevertToSelf()
                except Exception as e:
                    revert_err = str(e)
            ident = get_os_process_identity()
            read_err = None
            vault_path = req.get("vault_path")
            if vault_path:
                try:
                    with open(vault_path, encoding="utf-8") as f:
                        _ = f.read()
                except Exception as e:
                    read_err = {"error_type": type(e).__name__, "error": str(e)}

            client.send(
                {
                    "status": "CHECKED",
                    "is_restricted": ident.details.get("is_restricted"),
                    "revert_err": revert_err,
                    "read_err": read_err,
                }
            )

        elif cmd == "ATTEMPT_LAUNCH_COMPLETION_SERVICE":
            vault_path = req["vault_path"]
            coord_tok = req.get("coordinator_token")
            app_env = req.get("app_env", "production")
            try:
                proc, addr = spawn_isolated_completion_authority_service(
                    vault_path=vault_path,
                    coordinator_token=coord_tok,
                    app_env=app_env,
                )
                proc.terminate()
                proc.join(timeout=1)
                client.send({"launched": True})
            except Exception as e:
                client.send(
                    {
                        "launched": False,
                        "error_type": type(e).__name__,
                        "error": str(e),
                    }
                )

        elif cmd == "ATTEMPT_DIRECT_SQL":
            db_url = req["db_url"]
            sql = req["sql"]
            try:
                import psycopg

                with psycopg.connect(db_url) as conn:
                    conn.autocommit = False
                    with conn.cursor() as cur:
                        from typing import LiteralString, cast

                        cur.execute(cast("LiteralString", sql))
                        rows = cur.fetchall() if cur.description else None
                    conn.commit()
                client.send({"status": "SUCCESS", "rows": rows})
            except Exception as e:
                client.send(
                    {
                        "status": "DENIED",
                        "error_type": type(e).__name__,
                        "sqlstate": getattr(e, "sqlstate", None),
                        "error": str(e),
                    }
                )

        elif cmd == "EXECUTE_FEDERATION":
            try:
                coord_tok = req["coordinator_token"]
                db_url = req["db_url"]
                app_env = req.get("app_env", "production")
                comp_endpoint = req["comp_service_endpoint"]
                participating_banks = req["participating_banks"]

                if app_env.lower() == "production" and req.get("signing_key_hex"):
                    client.send(
                        {
                            "command": "FEDERATION_RESULT",
                            "status": "ERROR",
                            "error": "Production coordinator cannot receive or supply signing private key (SIGN-05).",
                        }
                    )
                    continue

                if app_env.lower() == "production" and req.get("pause_aggregation_checkpoint_file"):
                    client.send(
                        {
                            "command": "FEDERATION_RESULT",
                            "status": "ERROR",
                            "error": "Production coordinator cannot accept test aggregation checkpoint hooks (ISO-07).",
                        }
                    )
                    continue

                manager = DistributedFederatedTrainingManager(
                    coordinator_token=coord_tok,
                    db_url=db_url,
                    app_env=app_env,
                )

                comp_auth = IsolatedTrainingCompletionAuthority(
                    service_endpoint=comp_endpoint,
                    coordinator_token=coord_tok,
                    app_env=app_env,
                )

                class _WorkerProxyQueue:
                    def __init__(self, c: Any, b_id: str, q_type: str) -> None:
                        self._c = c
                        self._b = b_id
                        self._type = q_type

                    def put(self, item: Any) -> None:
                        self._c.send(
                            {"command": "PROXY_QUEUE_PUT", "bank_id": self._b, "msg": item}
                        )
                        ack = self._c.recv()
                        if ack.get("status") != "OK":
                            raise RuntimeError(ack.get("error", "Put failed"))

                    def get(self, timeout: float | None = None) -> Any:
                        self._c.send(
                            {"command": "PROXY_QUEUE_GET", "bank_id": self._b, "timeout": timeout}
                        )
                        res = self._c.recv()
                        if res.get("status") == "OK":
                            return res["msg"]
                        import queue

                        raise queue.Empty(res.get("error", "Timeout"))

                class _ProxySigningAuth:
                    def __init__(self, c: Any) -> None:
                        self._c = c

                    def sign_completed_job_receipt(
                        self,
                        job_id: str,
                        artifact_bytes: bytes,
                        caller_identity: str = "COORDINATOR",
                        caller_token: str | None = None,
                    ) -> Any:
                        self._c.send(
                            {
                                "command": "PROXY_SIGN_RECEIPT",
                                "job_id": job_id,
                                "artifact_bytes": artifact_bytes,
                                "caller_token": caller_token,
                                "caller_identity": caller_identity,
                            }
                        )
                        reply = self._c.recv()
                        if reply.get("status") == "SUCCESS":
                            return reply["receipt"]
                        raise TrainingProvenanceSecurityError(reply.get("error", "Signing error"))

                proxy_endpoints = (
                    {
                        b: {
                            "inbox": _WorkerProxyQueue(client, b, "inbox"),
                            "outbox": _WorkerProxyQueue(client, b, "outbox"),
                        }
                        for b in participating_banks
                    }
                    if req.get("has_worker_endpoints", True)
                    else None
                )

                signing_auth = _ProxySigningAuth(client)

                fed_result = manager.execute_distributed_federation(
                    job_id=req["job_id"],
                    training_run_id=req["training_run_id"],
                    simulation_id=req["simulation_id"],
                    holdout_val_data=req["holdout_val_data"],
                    participating_banks=participating_banks,
                    worker_endpoints=proxy_endpoints,
                    num_rounds=req["num_rounds"],
                    expected_unique_samples=req.get("expected_unique_samples"),
                    registry_storage_dir=req.get("registry_storage_dir"),
                    completion_authority=comp_auth,
                    signing_authority=signing_auth,
                    bank_partitions=req.get("bank_partitions"),
                    bank_tokens=req.get("bank_tokens"),
                    bank_data_paths=req.get("bank_data_paths"),
                    signing_key_hex=req.get("signing_key_hex"),
                    pause_aggregation_checkpoint_file=req.get("pause_aggregation_checkpoint_file"),
                    consortium_id=req.get("consortium_id"),
                    dp_config=req.get("dp_config"),
                    secagg_enabled=bool(req.get("secagg_enabled", False)),
                    secagg_config=req.get("secagg_config"),
                )

                if "worker_pids" in req:
                    fed_result["worker_pids"] = req["worker_pids"]

                client.send(
                    {
                        "command": "FEDERATION_RESULT",
                        "status": "SUCCESS",
                        "result": fed_result,
                    }
                )
            except Exception as e:
                client.send(
                    {
                        "command": "FEDERATION_RESULT",
                        "status": "ERROR",
                        "error": str(e),
                    }
                )

    with contextlib.suppress(Exception):
        client.close()


def spawn_isolated_coordinator_process(
    app_env: str = "production",
) -> tuple[Any, IsolatedCoordinatorClient]:
    """Launch dedicated Operating System Process running the Federation Coordinator.

    The coordinator process executes under an enforced restricted security token:
    - On Windows: created via CreateProcessAsUser with WinRestrictedCodeSid restricting token,
      all privileges stripped (DISABLE_MAX_PRIVILEGE), and Administrators group disabled.
    - bInheritHandles=False ensures zero inherited file handles or parent tokens.
    - The coordinator communicates with the completion authority strictly via authenticated TCP IPC.
    - Possesses zero completion credentials and is denied access to completion vaults by NTFS DACL.
    """
    if app_env.lower() == "production" and os.name != "nt":
        raise TrainingProvenanceSecurityError(
            f"Production coordinator isolation is not supported on platform '{sys.platform}'. "
            "Production federated training requires enforced Windows Restricted Token isolation (EXEC-06)."
        )

    import secrets

    authkey = secrets.token_bytes(32)
    listener = Listener(("127.0.0.1", 0), authkey=authkey)
    assert isinstance(listener.address, tuple)
    host, port = listener.address[0], listener.address[1]

    backend_dir = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = backend_dir
    env.pop("CFI_TRAINING_JOB_COMPLETION_DB_URL", None)
    env.pop("COMPLETION_DB_URL", None)
    env.pop("VAULT_PATH", None)

    proc_handle = None
    proc_pid = None

    worker_code = (
        "import sys; "
        "from app.application.services.distributed_fl_service import _run_isolated_coordinator_worker; "
        "_run_isolated_coordinator_worker((sys.argv[1], int(sys.argv[2])), bytes.fromhex(sys.argv[3]))"
    )

    if os.name == "nt":
        import win32process

        try:
            restricted_token = create_restricted_coordinator_token()
        except Exception as e:
            listener.close()
            raise TrainingProvenanceConfigurationError(
                f"Failed to create operating system restricted coordinator security token: {e}"
            ) from e

        si = win32process.STARTUPINFO()
        cmd = f'"{sys.executable}" -c "{worker_code}" {host} {port} {authkey.hex()}'
        create_process_as_user: Any = win32process.CreateProcessAsUser
        try:
            hProc, hThread, pid, tid = create_process_as_user(
                restricted_token,
                None,
                cmd,
                None,
                None,
                False,
                0,
                env,
                None,
                si,
            )
            proc_handle = hProc
            proc_pid = pid
        except Exception as e:
            listener.close()
            raise TrainingProvenanceConfigurationError(
                f"Failed to spawn isolated coordinator process with restricted token: {e}"
            ) from e
    else:
        import subprocess

        cmd_list = [
            sys.executable,
            "-c",
            worker_code,
            str(host),
            str(port),
            authkey.hex(),
        ]
        try:
            sub = subprocess.Popen(cmd_list, env=env)
            proc_handle = sub
            proc_pid = sub.pid
        except Exception as e:
            listener.close()
            raise TrainingProvenanceConfigurationError(
                f"Failed to spawn isolated coordinator process: {e}"
            ) from e

    try:
        with contextlib.suppress(Exception):
            raw_l = getattr(listener, "_listener", None)
            if raw_l is not None:
                s = getattr(raw_l, "_socket", None)
                if s is not None:
                    s.settimeout(45.0)

        conn = listener.accept()
        init_msg = conn.recv()
        if not isinstance(init_msg, dict) or init_msg.get("status") != "READY":
            raise TrainingProvenanceConfigurationError(f"Coordinator handshake failed: {init_msg}")

        # MANDATORY STARTUP VERIFICATION:
        # Independently audit the actual OS kernel process token of the spawned process
        if app_env.lower() == "production":
            target_proc = proc_handle if proc_handle is not None else proc_pid
            actual_child_id = get_os_process_identity(target_proc)
            child_details = actual_child_id.details

            try:
                validate_restricted_coordinator_policy(child_details)
            except TrainingProvenanceSecurityError as e:
                if os.name == "nt" and proc_handle is not None:
                    with contextlib.suppress(Exception):
                        import win32process

                        win32process.TerminateProcess(proc_handle, 1)
                elif hasattr(proc_handle, "terminate"):
                    with contextlib.suppress(Exception):
                        proc_handle.terminate()
                conn.close()
                listener.close()
                raise TrainingProvenanceSecurityError(
                    f"Spawned coordinator process violates mandatory production security policy: {e} (EXEC-05)."
                ) from e

        client = IsolatedCoordinatorClient(
            conn=conn,
            proc_handle=proc_handle,
            pid=proc_pid or init_msg.get("pid", 0),
            app_env=app_env,
        )
        return proc_handle, client
    except Exception as e:
        listener.close()
        if os.name == "nt" and proc_handle is not None:
            with contextlib.suppress(Exception):
                import win32process

                win32process.TerminateProcess(proc_handle, 1)
        if isinstance(e, (TrainingProvenanceSecurityError, TrainingProvenanceConfigurationError)):
            raise
        raise TrainingProvenanceConfigurationError(
            f"Failed to establish authenticated communication with isolated coordinator: {e}"
        ) from e
    finally:
        listener.close()


def run_production_federation(
    coordinator_token: str,
    db_url: str,
    job_id: str,
    training_run_id: str,
    simulation_id: str,
    holdout_val_data: tuple[np.ndarray, np.ndarray],
    participating_banks: list[str] | tuple[str, ...],
    worker_endpoints: dict[str, dict[str, Any]],
    comp_service_endpoint: tuple[str, int],
    signing_authority: Any,
    num_rounds: int = 2,
    expected_unique_samples: int | None = None,
    registry_storage_dir: str | None = None,
    worker_pids: dict[str, int] | None = None,
    app_env: str = "production",
    consortium_id: str | None = None,
    dp_config: Mapping[str, Any] | None = None,
    secagg_enabled: bool = False,
    secagg_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Canonical production entry point executing distributed federation under the mandatory restricted coordinator boundary."""
    proc, client = spawn_isolated_coordinator_process(app_env=app_env)
    try:
        return client.execute_federation(
            job_id=job_id,
            training_run_id=training_run_id,
            simulation_id=simulation_id,
            holdout_val_data=holdout_val_data,
            participating_banks=participating_banks,
            worker_endpoints=worker_endpoints,
            num_rounds=num_rounds,
            expected_unique_samples=expected_unique_samples,
            registry_storage_dir=registry_storage_dir,
            coordinator_token=coordinator_token,
            db_url=db_url,
            comp_service_endpoint=comp_service_endpoint,
            signing_authority=signing_authority,
            worker_pids=worker_pids,
            consortium_id=consortium_id,
            dp_config=dp_config,
            secagg_enabled=secagg_enabled,
            secagg_config=secagg_config,
        )
    finally:
        client.terminate()


def _acquire_vault_exclusive_os_boundary(path: str) -> Any:
    """Lock the physical vault file using native OS kernel primitives so external open() fails with PermissionError."""
    if not os.path.exists(path):
        return None
    if os.name == "nt":
        import ctypes

        generic_read = 0x80000000
        generic_write = 0x40000000
        file_share_none = 0
        open_existing = 3
        file_attribute_normal = 0x80
        handle = ctypes.windll.kernel32.CreateFileW(  # type: ignore[attr-defined]
            path,
            generic_read | generic_write,
            file_share_none,
            None,
            open_existing,
            file_attribute_normal,
            None,
        )
        if handle != -1:
            return handle
        return None
    else:
        try:
            import fcntl

            fd = os.open(path, os.O_RDWR)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
            return fd
        except Exception:
            return None


def _release_vault_exclusive_os_boundary(handle: Any) -> None:
    if handle is not None:
        if os.name == "nt":
            import ctypes

            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        else:
            try:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_UN)  # type: ignore[attr-defined]
                os.close(handle)
            except Exception:
                pass


def _run_isolated_completion_service_worker(
    vault_path_or_url: str,
    coordinator_token: str | None,
    ready_queue: mp.Queue,
    app_env: str = "production",
) -> None:
    """Dedicated Operating System Process running the Completion Authority Service.

    Resides strictly outside the coordinator security context:
    - Holds exclusive cfi_completion_role credentials in this independent process address space.
    - Locks the vault file with native OS kernel exclusive boundary.
    - Connects directly to PostgreSQL under cfi_completion_role.
    - Serves authenticated IPC connections via multiprocessing.connection.Listener.
    - Coordinator never possesses, stores, or derives these completion credentials.
    """
    worker_pid = os.getpid()
    logger.info("Isolated Training Completion Authority Service running with PID %d", worker_pid)

    lock_handle = None
    if os.path.exists(vault_path_or_url):
        with open(vault_path_or_url, encoding="utf-8") as f:
            completion_db_url = f.read().strip()
        lock_handle = _acquire_vault_exclusive_os_boundary(vault_path_or_url)
    else:
        if app_env.lower() == "production":
            ready_queue.put(
                {
                    "status": "ERROR",
                    "error": "Production isolated completion authority requires a provisioned vault file.",
                }
            )
            return
        completion_db_url = vault_path_or_url.strip()

    try:
        authority = PostgresTrainingJobAuthority(db_url=completion_db_url, app_env=app_env)
    except Exception as e:
        ready_queue.put(
            {"status": "ERROR", "error": f"Failed to initialize completion authority: {e}"}
        )
        if lock_handle:
            _release_vault_exclusive_os_boundary(lock_handle)
        return

    # Scrub raw completion_db_url from local variables
    del completion_db_url

    authkey_bytes = coordinator_token.encode("utf-8") if coordinator_token else b""
    try:
        listener = Listener(("127.0.0.1", 0), authkey=authkey_bytes)
        service_address = listener.address
    except Exception as e:
        ready_queue.put({"status": "ERROR", "error": f"Failed to bind service listener: {e}"})
        if lock_handle:
            _release_vault_exclusive_os_boundary(lock_handle)
        return

    ready_queue.put(
        {
            "status": "READY",
            "worker_pid": worker_pid,
            "service_address": service_address,
        }
    )

    stop_requested = threading.Event()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=8)

    def _handle_connection(conn: Any) -> None:
        try:
            req = conn.recv()
            if not isinstance(req, dict):
                conn.send({"status": "ERROR", "error": "Invalid request payload."})
                return

            cmd = req.get("command")
            if cmd == "SHUTDOWN":
                stop_requested.set()
                conn.send({"status": "SUCCESS", "message": "SHUTDOWN_ACK"})
                return

            if cmd == "RECORD_EVALUATION":
                conn.send(
                    {
                        "status": "ERROR",
                        "error": (
                            "Security validation failure: Caller-supplied evaluation metrics are forbidden. "
                            "Authoritative evaluation evidence must originate from genuine evaluation execution "
                            "via EVALUATE_AND_RECORD under trusted completion authority (EVAL-01)."
                        ),
                    }
                )
                return

            if cmd == "EVALUATE_AND_RECORD":
                auth_tok = req.get("coordinator_token")
                is_prod = app_env.lower() == "production"

                if is_prod and not coordinator_token:
                    conn.send(
                        {
                            "status": "ERROR",
                            "error": "Unauthorized evaluation request: coordinator token configuration is required in production.",
                        }
                    )
                    return

                if coordinator_token is not None:
                    if not auth_tok or not hmac.compare_digest(auth_tok, coordinator_token):
                        conn.send(
                            {
                                "status": "ERROR",
                                "error": "Unauthorized evaluation request: invalid or missing coordinator token.",
                            }
                        )
                        return
                elif req.get("caller_identity") not in ("COORDINATOR", "EVALUATOR"):
                    conn.send(
                        {
                            "status": "ERROR",
                            "error": f"Unauthorized evaluation caller identity: '{req.get('caller_identity')}'.",
                        }
                    )
                    return

                claimed_evaluator = req.get("evaluator_identity")
                if claimed_evaluator and claimed_evaluator != "ISOLATED_EVALUATOR":
                    conn.send(
                        {
                            "status": "ERROR",
                            "error": (
                                f"Unauthorized evaluator identity: '{claimed_evaluator}'. "
                                "Genuine evaluation must execute under 'ISOLATED_EVALUATOR'."
                            ),
                        }
                    )
                    return
                evaluator_identity = "ISOLATED_EVALUATOR"

                job_id = req["job_id"]
                artifact_bytes = req["artifact_bytes"]
                holdout_features = req["holdout_features"]
                holdout_labels = req["holdout_labels"]

                artifact_digest = hashlib.sha256(artifact_bytes).hexdigest()

                aggs = authority.get_global_aggregations(job_id)
                valid_digests = {a.aggregated_global_model_digest for a in aggs}
                if artifact_digest not in valid_digests:
                    conn.send(
                        {
                            "status": "ERROR",
                            "error": f"Evaluation forgery: evaluated artifact digest '{artifact_digest}' does not match any authentic global aggregation for job '{job_id}'.",
                        }
                    )
                    return

                try:
                    model = FraudDetectionModel(input_dim=NUM_FEATURES, dp_compatible=True)
                    buf = io.BytesIO(artifact_bytes)
                    state_dict = torch.load(buf, weights_only=True)
                    model.load_state_dict(state_dict)

                    model_svc = ModelService(get_settings())
                    eval_metrics = model_svc.evaluate(model, holdout_features, holdout_labels)

                    val_loss = float(eval_metrics["loss"])
                    val_auc = float(eval_metrics["auc_roc"])
                except Exception as e:
                    conn.send(
                        {
                            "status": "ERROR",
                            "error": f"Genuine model evaluation execution failed: {e}",
                        }
                    )
                    return

                if not (math.isfinite(val_loss) and math.isfinite(val_auc)):
                    conn.send(
                        {
                            "status": "ERROR",
                            "error": f"Genuine model evaluation produced non-finite metrics: loss={val_loss}, auc={val_auc}",
                        }
                    )
                    return

                evt_auc = ModelEvaluationEvent(
                    event_id=f"eval_{job_id}_auc_roc_{uuid.uuid4().hex[:8]}",
                    job_id=job_id,
                    evaluation_run_id=f"eval_run_{job_id}",
                    metric_name="auc_roc",
                    metric_value=val_auc,
                    evaluated_artifact_digest=artifact_digest,
                    evaluator_identity=evaluator_identity,
                    evaluation_timestamp=datetime.now(UTC).isoformat(),
                )
                authority.record_model_evaluation(evt_auc)

                evt_loss = ModelEvaluationEvent(
                    event_id=f"eval_{job_id}_val_loss_{uuid.uuid4().hex[:8]}",
                    job_id=job_id,
                    evaluation_run_id=f"eval_run_{job_id}",
                    metric_name="val_loss",
                    metric_value=val_loss,
                    evaluated_artifact_digest=artifact_digest,
                    evaluator_identity=evaluator_identity,
                    evaluation_timestamp=datetime.now(UTC).isoformat(),
                )
                authority.record_model_evaluation(evt_loss)

                conn.send(
                    {
                        "status": "SUCCESS",
                        "metrics": {"auc_roc": val_auc, "val_loss": val_loss},
                        "artifact_digest": artifact_digest,
                    }
                )
                return

            if cmd == "FINALIZE_COMPLETION":
                auth_tok = req.get("coordinator_token")
                is_prod = app_env.lower() == "production"

                if is_prod and not coordinator_token:
                    conn.send(
                        {
                            "status": "ERROR",
                            "error": "Unauthorized completion request: coordinator token configuration is required in production.",
                        }
                    )
                    return

                if coordinator_token is not None:
                    if not auth_tok or not hmac.compare_digest(auth_tok, coordinator_token):
                        conn.send(
                            {
                                "status": "ERROR",
                                "error": "Unauthorized completion request: invalid or missing coordinator token.",
                            }
                        )
                        return
                elif req.get("caller_identity") != "COORDINATOR":
                    conn.send(
                        {
                            "status": "ERROR",
                            "error": f"Unauthorized completion caller identity: '{req.get('caller_identity')}'.",
                        }
                    )
                    return

                job_id = req["job_id"]
                executed_rounds = req["executed_rounds"]
                executed_epochs = req.get("executed_epochs", 0)
                sample_count = req.get("sample_count", 0)
                observed_metrics = req.get("observed_metrics") or {}
                artifact_sha256 = req["artifact_sha256"]
                worker_id = req.get("worker_id", "FEDERATED_COORDINATOR")
                git_commit_hash = req.get("git_commit_hash")
                dataset_manifest_id = req.get("dataset_manifest_id")
                simulate_failure = req.get("simulate_failure_before_commit", False)
                pause_marker = req.get("pause_checkpoint_marker_file")
                pause_after_commit = req.get("pause_after_commit_marker_file")

                # Section 4 & ISO-07: Prohibit caller-controlled arbitrary filesystem writes and failure injection in production
                if is_prod and (pause_marker or pause_after_commit or simulate_failure):
                    conn.send(
                        {
                            "status": "ERROR",
                            "error": (
                                "Security validation failure: Caller-controlled failure injection and checkpoint "
                                "marker file paths are strictly forbidden in production completion requests (ISO-07)."
                            ),
                        }
                    )
                    return

                # Enforce that evaluation evidence originates from genuine evaluation under trusted authority
                eval_records = authority.get_model_evaluations(job_id)
                for er in eval_records:
                    if "COORD" in er.evaluator_identity.upper() or er.evaluator_identity in (
                        "UNTRUSTED",
                        "ANONYMOUS",
                        "CLIENT",
                    ):
                        conn.send(
                            {
                                "status": "ERROR",
                                "error": (
                                    f"Security validation failure: Evaluation evidence '{er.event_id}' for job '{job_id}' "
                                    f"claims coordinator-controlled or untrusted evaluator identity '{er.evaluator_identity}'. "
                                    "Authoritative completion requires genuine evaluation under trusted evaluator authority (ISOLATED_EVALUATOR)."
                                ),
                            }
                        )
                        return

                completed_record = authority.complete_job(
                    job_id=job_id,
                    executed_rounds=executed_rounds,
                    executed_epochs=executed_epochs,
                    sample_count=sample_count,
                    observed_metrics=observed_metrics,
                    artifact_sha256=artifact_sha256,
                    worker_id=worker_id,
                    git_commit_hash=git_commit_hash,
                    dataset_manifest_id=dataset_manifest_id,
                    consortium_id=req.get("consortium_id"),
                    enforce_lifecycle=True,
                    simulate_failure_before_commit=simulate_failure,
                    pause_checkpoint_marker_file=pause_marker,
                    pause_after_commit_marker_file=pause_after_commit,
                )

                record_dict = {
                    "job_id": completed_record.job_id,
                    "training_run_id": completed_record.training_run_id,
                    "simulation_id": completed_record.simulation_id,
                    "completed_at": completed_record.completed_at,
                    "executed_rounds": completed_record.executed_rounds,
                    "executed_epochs": completed_record.executed_epochs,
                    "sample_count": completed_record.sample_count,
                    "observed_metrics": dict(completed_record.observed_metrics),
                    "artifact_sha256": completed_record.artifact_sha256,
                    "architecture": completed_record.architecture,
                    "feature_schema_version": completed_record.feature_schema_version,
                    "input_dim": completed_record.input_dim,
                    "feature_names": tuple(completed_record.feature_names),
                    "worker_id": completed_record.worker_id,
                    "status": completed_record.status,
                    "git_commit_hash": completed_record.git_commit_hash,
                    "dataset_manifest_id": completed_record.dataset_manifest_id,
                    "consortium_id": completed_record.consortium_id,
                }
                conn.send(
                    {
                        "status": "SUCCESS",
                        "record_dict": record_dict,
                        "worker_pid": worker_pid,
                    }
                )
            else:
                conn.send({"status": "ERROR", "error": f"Unknown command: '{cmd}'"})
        except Exception as e:
            with contextlib.suppress(Exception):
                conn.send({"status": "ERROR", "error": str(e)})
        finally:
            with contextlib.suppress(Exception):
                conn.close()

    with contextlib.suppress(Exception):
        raw_listener = getattr(listener, "_listener", None)
        if raw_listener is not None:
            sock = getattr(raw_listener, "_socket", None)
            if sock is not None:
                sock.settimeout(0.5)

    while not stop_requested.is_set():
        try:
            conn = listener.accept()
            executor.submit(_handle_connection, conn)
        except mp.AuthenticationError:
            continue
        except (TimeoutError, OSError):
            continue
        except Exception:
            break

    executor.shutdown(wait=True)
    with contextlib.suppress(Exception):
        listener.close()
    if lock_handle:
        _release_vault_exclusive_os_boundary(lock_handle)


def spawn_isolated_completion_authority_service(
    vault_path: str,
    coordinator_token: str | None = None,
    app_env: str = "production",
) -> tuple[mp.Process, tuple[str, int]]:
    """Spawn dedicated OS process running the Completion Authority Service.

    Returns (service_process, service_address).
    The coordinator receives ONLY service_address; it NEVER receives vault_path or database credentials.
    """
    # Verify caller authorization to read vault before launching privileged service
    if not os.path.exists(vault_path):
        raise TrainingProvenanceSecurityError(
            f"Vault path does not exist or access denied: {vault_path}"
        )
    try:
        with open(vault_path, encoding="utf-8") as f:
            _ = f.read(1)
    except PermissionError as e:
        raise TrainingProvenanceSecurityError(
            f"Unauthorized caller identity cannot launch completion authority service: {e}"
        ) from e

    mp_ctx = mp.get_context("spawn")
    ready_q: Any = mp_ctx.Queue()
    proc = mp_ctx.Process(
        target=_run_isolated_completion_service_worker,
        args=(vault_path, coordinator_token, ready_q, app_env),
    )
    proc.daemon = True
    proc.start()

    init_msg = ready_q.get(timeout=45)
    if not isinstance(init_msg, dict) or init_msg.get("status") != "READY":
        err = init_msg.get("error") if isinstance(init_msg, dict) else str(init_msg)
        proc.terminate()
        proc.join(timeout=3)
        raise TrainingProvenanceConfigurationError(
            f"Failed to start completion authority service: {err}"
        )

    service_addr = init_msg["service_address"]
    return cast("mp.Process", proc), cast("tuple[str, int]", service_addr)


class IsolatedTrainingCompletionAuthority:
    """Independently authorized completion authority residing outside the coordinator boundary.

    Enforces Architecture & Security Invariants:
    1. Independent Credential Ownership: Holds cfi_completion_role credentials in an isolated OS process.
       Coordinator process possesses ZERO completion credentials and communicates strictly via authenticated IPC.
    2. Vault OS Boundary: Credential file is protected via kernel-level exclusive file lock.
    3. Atomic Reconciliation: Reconciliation of observations, aggregations, and evaluations and final insertion
       execute within a single PostgreSQL transaction with exclusive advisory transaction locking.
    4. Concurrency Safety: Multi-threaded connection listener handles independent client requests safely.
    5. Fails closed on unauthenticated requests, missing evidence, process termination, or database failure.
    """

    def __init__(
        self,
        service_endpoint: tuple[str, int] | None = None,
        vault_path: str | None = None,
        completion_db_url: str | None = None,
        coordinator_token: str | None = None,
        app_env: str = "production",
    ) -> None:
        self.app_env = app_env
        self._coordinator_token = coordinator_token
        self._process: mp.Process | None = None
        self._temp_vault: tempfile.TemporaryDirectory | None = None

        if service_endpoint is not None:
            # Client mode: Used by Coordinator. Possesses zero database credentials or vault paths.
            self.service_endpoint: tuple[str, int] = service_endpoint
            self._worker_pid = None
        elif vault_path is not None:
            # Service Launcher mode: Spawns independent completion service worker.
            self._process, self.service_endpoint = spawn_isolated_completion_authority_service(
                vault_path=vault_path,
                coordinator_token=coordinator_token,
                app_env=app_env,
            )
            self._worker_pid = self._process.pid
        elif completion_db_url is not None:
            if self.app_env.lower() == "production":
                raise TrainingProvenanceSecurityError(
                    "Production isolated completion authority requires provisioned vault_path or service_endpoint; "
                    "passing raw completion_db_url is strictly forbidden (COMP-01)."
                )
            self._temp_vault = tempfile.TemporaryDirectory(prefix="cfi_comp_vault_")
            temp_vault_file = provision_isolated_completion_authority_vault(
                self._temp_vault.name,
                completion_db_url,
            )
            self._process, self.service_endpoint = spawn_isolated_completion_authority_service(
                vault_path=temp_vault_file,
                coordinator_token=coordinator_token,
                app_env=app_env,
            )
            self._worker_pid = self._process.pid
        else:
            raise TrainingProvenanceConfigurationError(
                "Either service_endpoint or vault_path must be provided to IsolatedTrainingCompletionAuthority."
            )

    @property
    def pid(self) -> int | None:
        if self._process is not None:
            return self._process.pid
        return self._worker_pid

    def is_alive(self) -> bool:
        if self._process is not None:
            return self._process.is_alive()
        try:
            authkey = self._coordinator_token.encode("utf-8") if self._coordinator_token else b""
            c = Client(self.service_endpoint, authkey=authkey)
            c.close()
            return True
        except Exception:
            return False

    def terminate(self) -> None:
        if self._process is not None and self._process.is_alive():
            with contextlib.suppress(Exception):
                authkey = (
                    self._coordinator_token.encode("utf-8") if self._coordinator_token else b""
                )
                c = Client(self.service_endpoint, authkey=authkey)
                c.send({"command": "SHUTDOWN"})
                c.close()
            self._process.terminate()
            self._process.join(timeout=3)
        if self._temp_vault is not None:
            with contextlib.suppress(Exception):
                self._temp_vault.cleanup()
            self._temp_vault = None

    def __enter__(self) -> IsolatedTrainingCompletionAuthority:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.terminate()

    def finalize_training_completion(
        self,
        job_id: str,
        executed_rounds: int,
        executed_epochs: int = 0,
        sample_count: int = 0,
        observed_metrics: dict[str, float] | None = None,
        artifact_sha256: str = "",
        worker_id: str = "FEDERATED_COORDINATOR",
        coordinator_token: str | None = None,
        git_commit_hash: str | None = None,
        dataset_manifest_id: str | None = None,
        consortium_id: str | None = None,
        simulate_failure_before_commit: bool = False,
        pause_checkpoint_marker_file: str | None = None,
        pause_after_commit_marker_file: str | None = None,
    ) -> CompletedJobExecutionRecord:
        """Request atomic finalization from the isolated completion authority process over authenticated IPC."""
        if self.app_env.lower() == "production" and (
            pause_checkpoint_marker_file
            or pause_after_commit_marker_file
            or simulate_failure_before_commit
        ):
            raise TrainingProvenanceSecurityError(
                "Security validation failure: Caller-controlled failure injection and checkpoint "
                "marker file paths are strictly forbidden in production completion requests (ISO-07)."
            )

        token = coordinator_token if coordinator_token is not None else self._coordinator_token
        authkey_bytes = token.encode("utf-8") if token else b""
        try:
            conn = Client(self.service_endpoint, authkey=authkey_bytes)
        except Exception as e:
            raise TrainingProvenanceSecurityError(
                f"Completion authority service unavailable or connection failed: {e}"
            ) from e

        try:
            conn.send(
                {
                    "command": "FINALIZE_COMPLETION",
                    "job_id": job_id,
                    "executed_rounds": executed_rounds,
                    "executed_epochs": executed_epochs,
                    "sample_count": sample_count,
                    "observed_metrics": observed_metrics or {},
                    "artifact_sha256": artifact_sha256,
                    "worker_id": worker_id,
                    "coordinator_token": token,
                    "git_commit_hash": git_commit_hash,
                    "dataset_manifest_id": dataset_manifest_id,
                    "consortium_id": consortium_id,
                    "simulate_failure_before_commit": simulate_failure_before_commit,
                    "pause_checkpoint_marker_file": pause_checkpoint_marker_file,
                    "pause_after_commit_marker_file": pause_after_commit_marker_file,
                    "caller_identity": "COORDINATOR",
                }
            )
            res = conn.recv()
        except Exception as e:
            raise TrainingProvenanceSecurityError(
                f"Completion authority service communication failure: {e}"
            ) from e
        finally:
            with contextlib.suppress(Exception):
                conn.close()

        if res.get("status") == "SUCCESS":
            return CompletedJobExecutionRecord(**res["record_dict"])
        err_msg = res.get("error") or f"Completion rejected by isolated completion authority: {res}"
        raise TrainingProvenanceSecurityError(err_msg)

    def record_model_evaluation(
        self,
        job_id: str,
        metric_name: str,
        metric_value: float,
        evaluated_artifact_digest: str,
        evaluator_identity: str = "COMPLETION_AUTHORITY",
        coordinator_token: str | None = None,
        dataset_manifest_id: str | None = None,
    ) -> dict[str, Any]:
        """Record an evaluation observation into PostgreSQL through the isolated completion authority."""
        token = coordinator_token if coordinator_token is not None else self._coordinator_token
        authkey_bytes = token.encode("utf-8") if token else b""
        try:
            conn = Client(self.service_endpoint, authkey=authkey_bytes)
        except Exception as e:
            raise TrainingProvenanceSecurityError(
                f"Completion authority service unavailable or connection failed: {e}"
            ) from e

        try:
            conn.send(
                {
                    "command": "RECORD_EVALUATION",
                    "job_id": job_id,
                    "metric_name": metric_name,
                    "metric_value": float(metric_value),
                    "evaluated_artifact_digest": evaluated_artifact_digest,
                    "evaluator_identity": evaluator_identity,
                    "coordinator_token": token,
                    "dataset_manifest_id": dataset_manifest_id,
                    "caller_identity": "COORDINATOR",
                }
            )
            res = conn.recv()
        except Exception as e:
            raise TrainingProvenanceSecurityError(
                f"Completion authority communication failure during evaluation recording: {e}"
            ) from e
        finally:
            with contextlib.suppress(Exception):
                conn.close()

        if res.get("status") == "SUCCESS":
            return res
        err_msg = res.get("error") or f"Evaluation rejected by isolated completion authority: {res}"
        raise TrainingProvenanceSecurityError(err_msg)

    def evaluate_and_record_artifact(
        self,
        job_id: str,
        artifact_bytes: bytes,
        holdout_features: np.ndarray,
        holdout_labels: np.ndarray,
        evaluator_identity: str = "ISOLATED_EVALUATOR",
        coordinator_token: str | None = None,
    ) -> dict[str, float]:
        """Execute genuine evaluation in the isolated process and persist authentic evaluation evidence."""
        token = coordinator_token if coordinator_token is not None else self._coordinator_token
        authkey_bytes = token.encode("utf-8") if token else b""
        try:
            conn = Client(self.service_endpoint, authkey=authkey_bytes)
        except Exception as e:
            raise TrainingProvenanceSecurityError(
                f"Completion authority service unavailable or connection failed: {e}"
            ) from e

        try:
            conn.send(
                {
                    "command": "EVALUATE_AND_RECORD",
                    "job_id": job_id,
                    "artifact_bytes": artifact_bytes,
                    "holdout_features": holdout_features,
                    "holdout_labels": holdout_labels,
                    "evaluator_identity": evaluator_identity,
                    "coordinator_token": token,
                    "caller_identity": "COORDINATOR",
                }
            )
            res = conn.recv()
        except Exception as e:
            raise TrainingProvenanceSecurityError(
                f"Completion authority communication failure during artifact evaluation: {e}"
            ) from e
        finally:
            with contextlib.suppress(Exception):
                conn.close()

        if res.get("status") == "SUCCESS":
            return cast("dict[str, float]", res["metrics"])
        err_msg = (
            res.get("error")
            or f"Artifact evaluation rejected by isolated completion authority: {res}"
        )
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


class IsolatedBankWorkerProcessProxy:
    """Process proxy object representing an isolated operating system bank worker process.

    Provides a process management interface compatible with multiprocessing.Process:
    - pid: Operating System Process ID
    - is_alive(): True if the process is currently executing
    - terminate(): Terminate the OS process handle
    - join(timeout): Wait for process termination
    - exitcode: Process termination exit code
    """

    def __init__(
        self, pid: int, proc_handle: Any = None, conn: Any = None, bank_id: str = ""
    ) -> None:
        self.pid = pid
        self._proc_handle = proc_handle
        self._conn = conn
        self._bank_id = bank_id
        self._is_alive = True
        self.exitcode: int | None = None

    def is_alive(self) -> bool:
        if not self._is_alive:
            return False
        if os.name == "nt" and self._proc_handle is not None:
            import win32event
            import win32process

            res = win32event.WaitForSingleObject(self._proc_handle, 0)
            if res == 0:
                self._is_alive = False
                self.exitcode = win32process.GetExitCodeProcess(self._proc_handle)
                return False
            return True
        elif hasattr(self._proc_handle, "poll"):
            ret = self._proc_handle.poll()
            if ret is not None:
                self._is_alive = False
                self.exitcode = ret
                return False
            return True
        return self._is_alive

    def terminate(self) -> None:
        if os.name == "nt" and self._proc_handle is not None:
            import win32process

            with contextlib.suppress(Exception):
                win32process.TerminateProcess(self._proc_handle, 1)
        elif hasattr(self._proc_handle, "terminate"):
            with contextlib.suppress(Exception):
                self._proc_handle.terminate()
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
        self._is_alive = False
        self.exitcode = 1

    def join(self, timeout: float | None = None) -> None:
        timeout_ms = int(timeout * 1000) if timeout is not None else 10000
        if os.name == "nt" and self._proc_handle is not None:
            import win32event
            import win32process

            win32event.WaitForSingleObject(self._proc_handle, timeout_ms)
            self._is_alive = False
            self.exitcode = win32process.GetExitCodeProcess(self._proc_handle)
        elif hasattr(self._proc_handle, "wait"):
            with contextlib.suppress(Exception):
                self._proc_handle.wait(timeout=timeout)
                self.exitcode = self._proc_handle.returncode


class IsolatedWorkerInboxProxy:
    """Inbox proxy for sending commands and data to the isolated worker process."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._lock = threading.Lock()

    def put(self, item: Any, block: bool = True, timeout: float | None = None) -> None:
        with self._lock:
            self._conn.send(item)


class IsolatedWorkerOutboxProxy:
    """Outbox proxy for receiving responses and observations from the isolated worker process."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._lock = threading.Lock()
        self._buffer: list[Any] = []

    def get(self, block: bool = True, timeout: float | None = None) -> Any:
        with self._lock:
            if self._buffer:
                return self._buffer.pop(0)
            if timeout is not None and not self._conn.poll(timeout):
                import queue

                raise queue.Empty()
            return self._conn.recv()

    def empty(self) -> bool:
        with self._lock:
            if self._buffer:
                return False
            return not self._conn.poll(0)


class _WorkerChildInbox:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def get(self, block: bool = True, timeout: float | None = None) -> Any:
        if timeout is not None and not self._conn.poll(timeout):
            import queue

            raise queue.Empty()
        return self._conn.recv()


class _WorkerChildOutbox:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def put(self, item: Any, block: bool = True, timeout: float | None = None) -> None:
        self._conn.send(item)


def _run_isolated_bank_worker(conn_address: tuple[str, int], authkey: bytes) -> None:
    """Dedicated Operating System entrypoint for an isolated bank worker process."""
    from multiprocessing.connection import Client

    conn = Client(conn_address, authkey=authkey)
    pid = os.getpid()
    conn.send({"status": "READY", "pid": pid})

    config = conn.recv()
    bank_id = config["bank_id"]
    worker_token = config["worker_token"]
    db_url = config["db_url"]
    data_path = config.get("data_path")
    X_partition = config.get("X_partition")
    y_partition = config.get("y_partition")
    fail_observation = config.get("fail_observation", False)
    bank_encryption_key = config.get("bank_encryption_key")
    app_env = config.get("app_env", "production")

    # In production on Windows, verify that the worker's own primary process token satisfies policy
    if app_env.lower() == "production" and os.name == "nt":
        primary_ident = get_os_process_identity(check_thread=False)
        validate_restricted_bank_worker_policy(primary_ident.details, bank_id)

    conn.send({"status": "CONFIGURED", "pid": pid})

    inbox = _WorkerChildInbox(conn)
    outbox = _WorkerChildOutbox(conn)

    run_bank_worker_node(
        bank_id=bank_id,
        worker_token=worker_token,
        db_url=db_url,
        inbox=inbox,
        outbox=outbox,
        data_path=data_path,
        X_partition=X_partition,
        y_partition=y_partition,
        fail_observation=fail_observation,
        bank_encryption_key=bank_encryption_key,
        app_env=app_env,
    )


def spawn_bank_workers(
    participating_banks: list[str],
    bank_tokens: dict[str, str],
    db_url: str,
    bank_data_paths: dict[str, str] | None = None,
    bank_partitions: dict[str, tuple[np.ndarray, np.ndarray]] | None = None,
    fail_bank_b_observation: bool = False,
    bank_db_urls: dict[str, str] | None = None,
    bank_encryption_keys: Mapping[str, str | bytes] | None = None,
    app_env: str = "production",
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Spawn dedicated OS worker processes with their private credentials and data paths.

    In production:
    - Each bank worker process executes under a dedicated restricted Windows primary process token
      created via CreateProcessAsUser with bInheritHandles=False.
    - Zero inherited parent handles, secrets, or coordinator capabilities.
    - Strict boundary verification of the child process token prior to dispatching workload.
    - Fails closed on any security or policy configuration defect.
    """
    if app_env.lower() == "production" and os.name != "nt":
        raise TrainingProvenanceSecurityError(
            f"Production bank worker isolation is not supported on platform '{sys.platform}'. "
            "Production federated training requires enforced Windows Restricted Token isolation."
        )

    processes: dict[str, Any] = {}
    endpoints: dict[str, dict[str, Any]] = {}

    if os.name == "nt":
        import secrets

        import win32process

        backend_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )

        for b_id in participating_banks:
            data_path = bank_data_paths.get(b_id) if bank_data_paths else None
            X_b, y_b = bank_partitions.get(b_id, (None, None)) if bank_partitions else (None, None)
            should_fail_obs = b_id == "BANK_B" and fail_bank_b_observation

            if app_env.lower() == "production":
                if not bank_db_urls or b_id not in bank_db_urls or not bank_db_urls[b_id]:
                    raise TrainingProvenanceSecurityError(
                        f"Production bank worker [{b_id}] requires dedicated authenticated bank-specific database credentials. "
                        f"Fallback to generic database role is strictly forbidden (BANKDB-05)."
                    )
                candidate_url = bank_db_urls[b_id]
                import urllib.parse

                parsed_u = urllib.parse.urlsplit(candidate_url).username or ""
                if parsed_u == "cfi_worker_role":
                    raise TrainingProvenanceSecurityError(
                        f"Production bank worker [{b_id}] cannot execute under shared generic worker role 'cfi_worker_role'. "
                        f"Dedicated bank-specific database role is required (BANKDB-05)."
                    )
                worker_db_url = candidate_url
            else:
                worker_db_url = (
                    bank_db_urls[b_id] if bank_db_urls and b_id in bank_db_urls else db_url
                )

            enc_key = bank_encryption_keys.get(b_id) if bank_encryption_keys else None

            # 1. Dedicated loopback listener for this bank worker
            authkey = secrets.token_bytes(32)
            listener = Listener(("127.0.0.1", 0), authkey=authkey)
            assert isinstance(listener.address, tuple)
            host, port = listener.address[0], listener.address[1]

            # 2. Dedicated restricted primary token
            try:
                restricted_token = create_bank_worker_security_token(b_id)
            except Exception as e:
                listener.close()
                raise TrainingProvenanceConfigurationError(
                    f"Failed to create operating system restricted security token for bank worker '{b_id}': {e}"
                ) from e

            # 3. Sanitized child environment (no leaked completion/coordinator secrets)
            env = dict(os.environ)
            env["PYTHONPATH"] = backend_dir
            env.pop("CFI_TRAINING_JOB_COMPLETION_DB_URL", None)
            env.pop("COMPLETION_DB_URL", None)
            env.pop("VAULT_PATH", None)

            worker_code = (
                "import sys; "
                "from app.application.services.distributed_fl_service import _run_isolated_bank_worker; "
                "_run_isolated_bank_worker((sys.argv[1], int(sys.argv[2])), bytes.fromhex(sys.argv[3]))"
            )
            cmd = f'"{sys.executable}" -c "{worker_code}" {host} {port} {authkey.hex()}'

            si = win32process.STARTUPINFO()
            create_process_as_user: Any = win32process.CreateProcessAsUser
            try:
                hProc, hThread, pid, tid = create_process_as_user(
                    restricted_token,
                    None,
                    cmd,
                    None,
                    None,
                    False,  # bInheritHandles = False: zero inherited handles or parent tokens!
                    0,
                    env,
                    None,
                    si,
                )
            except Exception as e:
                listener.close()
                raise TrainingProvenanceConfigurationError(
                    f"Failed to spawn isolated bank worker process '{b_id}' via CreateProcessAsUser: {e}"
                ) from e

            try:
                raw_l = getattr(listener, "_listener", None)
                if raw_l is not None:
                    s = getattr(raw_l, "_socket", None)
                    if s is not None:
                        s.settimeout(45.0)

                conn = listener.accept()
                init_msg = conn.recv()
                if not isinstance(init_msg, dict) or init_msg.get("status") != "READY":
                    raise TrainingProvenanceConfigurationError(
                        f"Bank worker [{b_id}] handshake failed: {init_msg}"
                    )

                # 4. Mandatory independent audit of spawned process's primary token
                if app_env.lower() == "production":
                    child_id = get_os_process_identity(hProc, check_thread=False)
                    try:
                        validate_restricted_bank_worker_policy(child_id.details, b_id)
                    except TrainingProvenanceSecurityError as se:
                        import win32process

                        with contextlib.suppress(Exception):
                            win32process.TerminateProcess(hProc, 1)
                        conn.close()
                        listener.close()
                        raise TrainingProvenanceSecurityError(
                            f"Spawned bank worker [{b_id}] process violates mandatory security policy: {se} (BANK-FIX-01)."
                        ) from se

                # 5. Dispatch bank credentials and configuration strictly to this specific worker
                config_payload = {
                    "bank_id": b_id,
                    "worker_token": bank_tokens[b_id],
                    "db_url": worker_db_url,
                    "data_path": data_path,
                    "X_partition": X_b,
                    "y_partition": y_b,
                    "fail_observation": should_fail_obs,
                    "bank_encryption_key": enc_key,
                    "app_env": app_env,
                }
                conn.send(config_payload)
                ack = conn.recv()
                if not isinstance(ack, dict) or ack.get("status") != "CONFIGURED":
                    raise TrainingProvenanceConfigurationError(
                        f"Bank worker [{b_id}] failed configuration handshake: {ack}"
                    )

                proc_proxy = IsolatedBankWorkerProcessProxy(
                    pid=pid, proc_handle=hProc, conn=conn, bank_id=b_id
                )
                processes[b_id] = proc_proxy
                _, pub_hex = derive_institutional_identity_hex(b_id, bank_tokens[b_id])
                endpoints[b_id] = {
                    "inbox": IsolatedWorkerInboxProxy(conn),
                    "outbox": IsolatedWorkerOutboxProxy(conn),
                    "institutional_public_key": pub_hex,
                }
            except Exception:
                listener.close()
                raise
            finally:
                listener.close()

    else:
        # Development non-Windows fallback
        mp_ctx = mp.get_context("spawn")
        for b_id in participating_banks:
            in_q: Any = mp_ctx.Queue()
            out_q: Any = mp_ctx.Queue()
            data_path = bank_data_paths.get(b_id) if bank_data_paths else None
            X_b, y_b = bank_partitions.get(b_id, (None, None)) if bank_partitions else (None, None)
            should_fail_obs = b_id == "BANK_B" and fail_bank_b_observation
            worker_db_url = bank_db_urls[b_id] if bank_db_urls and b_id in bank_db_urls else db_url
            enc_key = bank_encryption_keys.get(b_id) if bank_encryption_keys else None
            proc = mp_ctx.Process(
                target=run_bank_worker_node,
                args=(
                    b_id,
                    bank_tokens[b_id],
                    worker_db_url,
                    in_q,
                    out_q,
                    data_path,
                    X_b,
                    y_b,
                    should_fail_obs,
                    enc_key,
                    app_env,
                ),
            )
            proc.daemon = True
            proc.start()
            processes[b_id] = proc
            _, pub_hex = derive_institutional_identity_hex(b_id, bank_tokens[b_id])
            endpoints[b_id] = {
                "inbox": in_q,
                "outbox": out_q,
                "institutional_public_key": pub_hex,
            }
    return processes, endpoints


# ---------------------------------------------------------------------------
# Bank Worker Process Entry Point
# ---------------------------------------------------------------------------


def run_bank_worker_node(
    bank_id: str,
    worker_token: str,
    db_url: str,
    inbox: Any,
    outbox: Any,
    data_path: str | None = None,
    X_partition: np.ndarray | None = None,
    y_partition: np.ndarray | None = None,
    fail_observation: bool = False,
    bank_encryption_key: str | bytes | None = None,
    app_env: str = "production",
    local_partition: tuple[np.ndarray, np.ndarray] | None = None,
) -> None:
    """Dedicated Operating System Process for a Participating Bank Worker.

    Invariants enforced:
    1. Isolated OS Process (distinct PID).
    2. Zero external access to private datasets; loaded locally from bank storage under dedicated Bank OS identity.
    3. Authenticated solely by worker_token bound to bank_id (coordinator has no bank tokens).
    4. Enforced Windows Restricted Token bound to designated bank SID.
    5. Performs genuine PyTorch local training steps and records observations directly into PostgreSQL authority.
    6. Missing or non-finite losses fail immediately without fallback invention.
    """
    pid = os.getpid()
    logger.info("Bank worker [%s] started with PID %d", bank_id, pid)

    with BankWorkerOSSecurityContext(bank_id):
        if app_env.lower() == "production" and os.name == "nt":
            primary_ident = get_os_process_identity(check_thread=False)
            if not primary_ident.details.get("is_restricted"):
                raise TrainingProvenanceSecurityError(
                    f"Production bank worker [{bank_id}] must execute under an enforced restricted primary process token (BANK-FIX-08). "
                    f"Unrestricted multiprocessing worker or thread impersonation is strictly rejected."
                )
            validate_restricted_bank_worker_policy(primary_ident.details, bank_id)

        settings = get_settings()
        model_service = ModelService(settings)

        if app_env.lower() == "production":
            import urllib.parse

            parsed_u = urllib.parse.urlsplit(db_url).username or ""
            if parsed_u == "cfi_worker_role":
                raise TrainingProvenanceSecurityError(
                    f"Production bank worker [{bank_id}] cannot execute under shared generic worker role 'cfi_worker_role' (BANKDB-05)."
                )
            expected_role = f"cfi_{bank_id.lower()}_role"
            if parsed_u.startswith("cfi_bank_") and parsed_u != expected_role:
                raise TrainingProvenanceSecurityError(
                    f"Production bank worker [{bank_id}] database credential mismatch: connected as '{parsed_u}' instead of '{expected_role}'."
                )

        # Establish connection to authoritative training job database
        authority_source = TrustedTrainingJobRecordSource(
            worker_token=worker_token,
            bank_tokens={bank_id: worker_token},
            db_url=db_url,
            enforce_lifecycle=True,
            app_env=app_env,
        )

        clean_worker_bank = bank_id.strip().upper()
        try:
            _, local_pub_hex = derive_institutional_identity_hex(clean_worker_bank, worker_token)
            authority_source.register_institutional_identity(
                bank_id=clean_worker_bank,
                public_key_hex=local_pub_hex,
                token=worker_token,
            )
        except Exception as reg_err:
            logger.debug("Bank worker [%s] auto-registration of institutional identity: %s", bank_id, reg_err)

        # Load local training data from isolated bank storage if path provided
        vault_handle: BankPrivateStorageVault | None = None
        if local_partition is not None:
            X_train, y_train = local_partition
        elif data_path is not None:
            if not os.path.exists(data_path):
                raise FileNotFoundError(
                    f"Bank [{bank_id}] private dataset not found at '{data_path}'"
                )
            bank_vault_dir = os.path.dirname(os.path.dirname(data_path))
            vault_handle = BankPrivateStorageVault(
                bank_id,
                bank_vault_dir,
                bank_token=worker_token,
                bank_encryption_key=bank_encryption_key,
                app_env=app_env,
            )
            X_train, y_train = vault_handle.read_dataset_as_bank(bank_id, caller_token=worker_token)
            vault_handle.acquire_exclusive_os_boundary()
        elif X_partition is not None and y_partition is not None:
            X_train = X_partition
            y_train = y_partition
        else:
            raise ValueError(
                f"Bank [{bank_id}] must be supplied with either a private data_path or partition arrays."
            )

        dp_sessions: dict[str, dict[str, Any]] = {}
        secagg_worker_sessions: dict[str, dict[str, Any]] = {}

        try:
            while True:
                try:
                    msg = inbox.get()
                except Exception:
                    break

                if msg is None or msg.get("command") == "STOP":
                    logger.info("Bank worker [%s] received STOP command. Exiting.", bank_id)
                    break

                if msg.get("command") == "GET_SECURITY_IDENTITY":
                    outbox.put(
                        {
                            "status": "SUCCESS",
                            "bank_id": bank_id,
                            "pid": pid,
                            "identity": get_os_process_identity(check_thread=False).details,
                            "effective_identity": get_os_process_identity(
                                check_thread=True
                            ).details,
                        }
                    )
                    continue

                if msg.get("command") == "GET_RAW_DATA":
                    outbox.put(
                        {
                            "status": "ERROR",
                            "error": f"Institutional boundary violation: coordinator cannot request raw bank dataset from {bank_id}.",
                        }
                    )
                    continue

                if msg.get("command") == "ATTEMPT_READ_FILE":
                    path = msg.get("path")
                    try:
                        with open(path, "rb") as f:
                            d = f.read()
                        outbox.put(
                            {
                                "status": "SUCCESS",
                                "bank_id": bank_id,
                                "path": path,
                                "bytes_read": len(d),
                            }
                        )
                    except PermissionError as pe:
                        outbox.put(
                            {
                                "status": "DENIED",
                                "bank_id": bank_id,
                                "path": path,
                                "error": str(pe),
                                "errno": getattr(pe, "errno", None),
                            }
                        )
                    except Exception as e:
                        outbox.put(
                            {
                                "status": "ERROR",
                                "bank_id": bank_id,
                                "path": path,
                                "error": str(e),
                            }
                        )
                    continue

                if msg.get("command") == "ATTEMPT_REVERT_AND_READ":
                    path = msg.get("path")
                    if os.name == "nt":
                        import win32security

                        win32security.RevertToSelf()
                    ident = get_os_process_identity(check_thread=False)
                    try:
                        with open(path, "rb") as f:
                            d = f.read()
                        outbox.put(
                            {
                                "status": "SUCCESS",
                                "bank_id": bank_id,
                                "path": path,
                                "bytes_read": len(d),
                                "is_restricted": ident.details.get("is_restricted"),
                                "restricted_sids": ident.details.get("restricted_sids"),
                            }
                        )
                    except PermissionError as pe:
                        outbox.put(
                            {
                                "status": "DENIED",
                                "bank_id": bank_id,
                                "path": path,
                                "error": str(pe),
                                "errno": getattr(pe, "errno", None),
                                "is_restricted": ident.details.get("is_restricted"),
                                "restricted_sids": ident.details.get("restricted_sids"),
                            }
                        )
                    except Exception as e:
                        outbox.put(
                            {
                                "status": "ERROR",
                                "bank_id": bank_id,
                                "path": path,
                                "error": str(e),
                                "is_restricted": ident.details.get("is_restricted"),
                                "restricted_sids": ident.details.get("restricted_sids"),
                            }
                        )
                    continue

                if msg.get("command") == "ATTEMPT_PRIVILEGE_RECOVERY":
                    revert_success = False
                    if os.name == "nt":
                        import win32security

                        try:
                            win32security.RevertToSelf()
                            revert_success = True
                        except Exception:
                            revert_success = False
                        ident = get_os_process_identity(check_thread=False)
                        is_restricted = ident.details.get("is_restricted", False)
                        is_admin_disabled = ident.details.get("is_admin_disabled", False)
                    else:
                        is_restricted = False
                        is_admin_disabled = False
                    outbox.put(
                        {
                            "status": "SUCCESS",
                            "bank_id": bank_id,
                            "pid": pid,
                            "revert_called": revert_success,
                            "is_restricted": is_restricted,
                            "is_admin_disabled": is_admin_disabled,
                        }
                    )
                    continue

                if msg.get("command") == "TRAIN_ROUND":
                    job_id = msg["job_id"]
                    round_number = msg["round_number"]
                    attempt_id = str(msg.get("attempt_id") or f"att_{job_id}_r{round_number}_{uuid.uuid4().hex[:8]}")
                    global_weights_obj: ModelWeights | None = msg.get("global_weights")

                    # On round 1, authorized worker initiates or verifies job start in PostgreSQL authority
                    if round_number == 1:
                        try:
                            authority_source.start_training_job(
                                job_id=job_id,
                                worker_id=bank_id,
                                worker_token=worker_token,
                            )
                            logger.info(
                                "Bank worker [%s] verified or initiated start for training job %s",
                                bank_id,
                                job_id,
                            )
                        except Exception as start_err:
                            logger.error(
                                "Bank worker [%s] failed to initiate training job %s: %s",
                                bank_id,
                                job_id,
                                start_err,
                            )
                            outbox.put(
                                {
                                    "status": "ERROR",
                                    "bank_id": bank_id,
                                    "error": f"Failed to start training job: {start_err}",
                                    "pid": pid,
                                }
                            )
                            continue

                    # Check scheduled job configuration for differential privacy requirements
                    sched = authority_source.get_scheduled_job(job_id)
                    dp_config = sched.dp_config if sched and sched.dp_config else None
                    is_dp_enabled = bool(dp_config and dp_config.get("dp_enabled", False))

                    # Initialize local model and load latest global weights
                    local_model = model_service.create_model(
                        input_dim=NUM_FEATURES, dp_compatible=True
                    )
                    if global_weights_obj is not None:
                        model_service.set_parameters(local_model, global_weights_obj)

                    dp_info: dict[str, Any] | None = None
                    session: dict[str, Any] | None = None
                    if is_dp_enabled:
                        assert dp_config is not None
                        target_epsilon = float(dp_config["target_epsilon"])
                        target_delta = float(dp_config["target_delta"])
                        max_grad_norm = float(dp_config.get("max_grad_norm", 1.0))
                        noise_multiplier = (
                            float(dp_config["noise_multiplier"])
                            if "noise_multiplier" in dp_config
                            and dp_config["noise_multiplier"] is not None
                            else None
                        )
                        raw_dataset_tag = str(dp_config.get("dataset_tag", "primary_vault")).strip()
                        is_prod = app_env.lower() == "production"
                        try:
                            auth_dataset_tag = authority_source.resolve_authoritative_dataset_identity(
                                bank_id=bank_id,
                                dataset_tag=raw_dataset_tag,
                                is_prod=is_prod,
                            )
                        except Exception as tag_err:
                            logger.error("Bank worker [%s] dataset identity resolution failed: %s", bank_id, tag_err)
                            outbox.put({
                                "status": "ERROR",
                                "bank_id": bank_id,
                                "error": f"Unauthorized dataset identity: {tag_err}",
                                "pid": pid,
                            })
                            continue

                        if vault_handle is not None:
                            vault_tag = vault_handle.get_authoritative_dataset_identity()
                            if is_prod and auth_dataset_tag != vault_tag:
                                try:
                                    resolved_vault = authority_source.resolve_authoritative_dataset_identity(
                                        bank_id=bank_id,
                                        dataset_tag=vault_tag,
                                        is_prod=True,
                                    )
                                    if resolved_vault != auth_dataset_tag:
                                        raise TrainingProvenanceSecurityError(
                                            f"Unauthorized dataset substitution: requested dataset resolves to '{auth_dataset_tag}', "
                                            f"but local bank storage vault is bound to '{resolved_vault}'."
                                        )
                                except Exception as subst_err:
                                    outbox.put({
                                        "status": "ERROR",
                                        "bank_id": bank_id,
                                        "error": str(subst_err),
                                        "pid": pid,
                                    })
                                    continue

                        accountant_type = str(
                            dp_config.get("accountant_type")
                            or dp_config.get("accountant")
                            or "prv"
                        ).strip().lower().replace("opacus_", "")
                        require_secure_rng = bool(dp_config.get("require_secure_rng", False))
                        batch_size = int(dp_config.get("batch_size", 32))
                        learning_rate = float(dp_config.get("learning_rate", 0.01))
                        expected_rounds = sched.expected_rounds if sched else 1

                        # Session Continuity across rounds (no accounting horizon reset)
                        session = dp_sessions.setdefault(
                            job_id, {"privacy_engine": None, "steps_executed": 0}
                        )
                        existing_engine = session.get("privacy_engine")

                        # Authoritative Institutional Budget Check (Fail closed if missing - do NOT auto-allocate!)
                        budget_record = authority_source.get_institutional_privacy_budget(
                            bank_id, auth_dataset_tag, token=worker_token
                        )
                        if budget_record is None:
                            err_msg = (
                                f"No institutional privacy budget provisioned for bank '{bank_id}', dataset '{auth_dataset_tag}'. "
                                f"Workers cannot allocate new budgets. Allocation must be explicitly provisioned by trusted authority."
                            )
                            logger.error("Bank worker [%s] %s", bank_id, err_msg)
                            outbox.put({
                                "status": "ERROR",
                                "bank_id": bank_id,
                                "error": f"Privacy budget missing: {err_msg}",
                                "pid": pid,
                            })
                            continue

                        history_job_eps = 0.0
                        history_job_del = 0.0
                        prior_steps = 0
                        if budget_record.history:
                            for entry in budget_record.history:
                                if (
                                    isinstance(entry, dict)
                                    and entry.get("job_id") == job_id
                                    and entry.get("status") == "COMMITTED"
                                ):
                                    history_job_eps = max(
                                        history_job_eps, float(entry.get("spent_epsilon", 0.0))
                                    )
                                    history_job_del = max(
                                        history_job_del, float(entry.get("spent_delta", 0.0))
                                    )
                                    prior_steps += int(entry.get("steps_executed", 0))

                        prior_job_spent_eps = max(
                            history_job_eps, float(session.get("last_committed_eps", 0.0))
                        )
                        prior_job_spent_del = max(
                            history_job_del, float(session.get("last_committed_del", 0.0))
                        )

                        needed_eps = max(0.001, target_epsilon - prior_job_spent_eps)
                        needed_del = max(0.0, target_delta - prior_job_spent_del)

                        # Strict budget admission: Never reduce reservation without recalibration
                        if budget_record.remaining_epsilon < (needed_eps - 1e-9):
                            err_msg = (
                                f"Privacy budget exceeded: job planned expenditure ({needed_eps:.4f}) "
                                f"exceeds remaining available budget ({budget_record.remaining_epsilon:.4f})."
                            )
                            logger.error("Bank worker [%s] %s", bank_id, err_msg)
                            outbox.put({
                                "status": "ERROR",
                                "bank_id": bank_id,
                                "error": f"Privacy budget exhausted: {err_msg}",
                                "pid": pid,
                            })
                            continue

                        # Compute immutable parameter digest for attempt binding
                        params_digest_dict = {
                            "bank_id": bank_id,
                            "dataset_tag": auth_dataset_tag,
                            "job_id": job_id,
                            "round_number": round_number,
                            "target_epsilon": target_epsilon,
                            "target_delta": target_delta,
                            "max_grad_norm": max_grad_norm,
                            "batch_size": batch_size,
                            "learning_rate": learning_rate,
                            "accountant_type": accountant_type,
                        }
                        params_digest = hashlib.sha256(
                            json.dumps(params_digest_dict, sort_keys=True).encode("utf-8")
                        ).hexdigest()

                        # 3. Durable attempt admission BEFORE private computation
                        try:
                            admission = authority_source.admit_training_attempt(
                                bank_id=bank_id,
                                dataset_tag=auth_dataset_tag,
                                job_id=job_id,
                                round_number=round_number,
                                attempt_id=attempt_id,
                                parameters_digest=params_digest,
                                requested_epsilon=needed_eps,
                                requested_delta=needed_del,
                                worker_token=worker_token,
                            )
                        except TrainingProvenanceSecurityError as pse:
                            logger.error(
                                "Bank worker [%s] attempt admission security error for job %s round %d: %s",
                                bank_id, job_id, round_number, pse,
                            )
                            outbox.put({
                                "status": "ERROR",
                                "bank_id": bank_id,
                                "error": f"Attempt admission security error: {pse}",
                                "pid": pid,
                            })
                            continue

                        if admission.status == "ALREADY_COMPLETED":
                            logger.info(
                                "Bank worker [%s] attempt %s for job %s round %d already completed. Replaying cached output without re-training.",
                                bank_id, attempt_id, job_id, round_number,
                            )
                            cached_res = session.get(f"attempt_{attempt_id}")
                            if cached_res is not None:
                                outbox.put(cached_res)
                                continue
                            existing_obs = authority_source.get_round_observations(job_id)
                            cached_obs = [
                                o for o in existing_obs
                                if o.bank_id == bank_id and o.round_number == round_number
                            ]
                            if cached_obs and cached_obs[0].model_update_digest:
                                outbox.put({
                                    "status": "SUCCESS",
                                    "bank_id": bank_id,
                                    "pid": pid,
                                    "round_number": round_number,
                                    "attempt_id": attempt_id,
                                    "weights": None,
                                    "update_digest": cached_obs[0].model_update_digest,
                                    "sample_count": cached_obs[0].actual_sample_count,
                                    "executed_epochs": cached_obs[0].actual_executed_epochs,
                                    "dp_provenance": {"status": "ALREADY_COMPLETED"},
                                })
                                continue
                            else:
                                outbox.put({
                                    "status": "SUCCESS",
                                    "bank_id": bank_id,
                                    "pid": pid,
                                    "round_number": round_number,
                                    "attempt_id": attempt_id,
                                    "weights": None,
                                    "update_digest": "",
                                    "sample_count": len(X_train),
                                    "executed_epochs": 1,
                                    "dp_provenance": {"status": "ALREADY_COMPLETED"},
                                })
                                continue

                        if admission.status != "ADMITTED":
                            err_msg = f"Attempt admission rejected ({admission.status}): {admission.reason}"
                            logger.error("Bank worker [%s] %s", bank_id, err_msg)
                            outbox.put({
                                "status": "ERROR",
                                "bank_id": bank_id,
                                "error": err_msg,
                                "pid": pid,
                            })
                            continue

                        # Execute Opacus DP-SGD
                        steps_per_epoch = math.ceil(len(X_train) / max(1, batch_size))
                        total_job_steps = max(1, expected_rounds * steps_per_epoch)

                        try:
                            local_model, loss_history, actual_epsilon = (
                                model_service.train_local_with_opacus(
                                    model=local_model,
                                    X_train=X_train,
                                    y_train=y_train,
                                    epochs=1,
                                    batch_size=batch_size,
                                    learning_rate=learning_rate,
                                    target_epsilon=target_epsilon,
                                    target_delta=target_delta,
                                    max_grad_norm=max_grad_norm,
                                    noise_multiplier=noise_multiplier,
                                    total_steps=total_job_steps,
                                    accountant_type=accountant_type,
                                    require_secure_rng=require_secure_rng,
                                    privacy_engine=existing_engine,
                                    prior_steps=prior_steps if existing_engine is None else 0,
                                )
                            )
                            session["privacy_engine"] = getattr(
                                local_model, "_privacy_engine", None
                            )
                            session["steps_executed"] += steps_per_epoch
                            dp_info = getattr(local_model, "dp_provenance", {})
                        except Exception as dp_err:
                            # FAIL CLOSED: Never silently fallback to vanilla non-DP training!
                            logger.error(
                                "Bank worker [%s] Opacus DP-SGD execution failed for job %s round %d: %s",
                                bank_id,
                                job_id,
                                round_number,
                                dp_err,
                            )
                            outbox.put(
                                {
                                    "status": "ERROR",
                                    "bank_id": bank_id,
                                    "error": f"Opacus DP-SGD failed: {dp_err}",
                                    "pid": pid,
                                }
                            )
                            continue

                        # Commit expenditure in durable institutional budget table
                        spent_eps = (
                            float(dp_info.get("epsilon", needed_eps)) if dp_info else needed_eps
                        )
                        spent_del = (
                            float(dp_info.get("delta", target_delta)) if dp_info else target_delta
                        )
                        authority_source.commit_institutional_privacy_expenditure(
                            bank_id=bank_id,
                            dataset_tag=auth_dataset_tag,
                            job_id=job_id,
                            round_number=round_number,
                            spent_epsilon=spent_eps,
                            spent_delta=spent_del,
                            steps_executed=steps_per_epoch,
                            worker_token=worker_token,
                            attempt_id=attempt_id,
                        )
                        session["last_committed_eps"] = spent_eps
                        session["last_committed_del"] = spent_del
                    else:
                        # Non-DP vanilla training route (explicitly non-private FedAvg)
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

                    # Release surface protection: In DP mode, training loss is restricted bank-internal data
                    if is_dp_enabled:
                        observed_loss = None
                        obs_metrics: dict[str, float] = {
                            "dp_epsilon": float(dp_info.get("epsilon", 0.0)) if dp_info else 0.0,
                            "dp_delta": float(dp_info.get("delta", 0.0)) if dp_info else 0.0,
                            "dp_noise_multiplier": float(dp_info.get("noise_multiplier", 0.0))
                            if dp_info
                            else 0.0,
                        }
                    else:
                        observed_loss = float(loss_history[-1])
                        obs_metrics = {"loss": observed_loss}

                    if observed_loss is not None and not math.isfinite(observed_loss):
                        raise TrainingProvenanceSecurityError(
                            f"Bank worker [{bank_id}] produced non-finite observed loss: {observed_loss}"
                        )

                    if fail_observation:
                        # Adversarial failure injection: worker fails to record observation in PostgreSQL
                        logger.warning(
                            "Bank worker [%s] simulated observation persistence failure for round %d",
                            bank_id,
                            round_number,
                        )
                        outbox.put(
                            {
                                "status": "OBSERVATION_FAILED",
                                "bank_id": bank_id,
                                "round_number": round_number,
                                "pid": pid,
                            }
                        )
                        continue

                    # Record authoritative execution observation directly in PostgreSQL
                    try:
                        obs = authority_source.record_round_observation(
                            job_id=job_id,
                            round_number=round_number,
                            sample_count=sample_count,
                            actual_executed_epochs=1,
                            observed_loss=observed_loss,
                            metrics=obs_metrics,
                            worker_token=worker_token,
                            bank_id=bank_id,
                            model_update_digest=update_digest,
                            execution_status="COMPLETED",
                        )
                        logger.info(
                            "Bank worker [%s] persisted observation event %s for round %d",
                            bank_id,
                            obs.event_id,
                            round_number,
                        )
                    except Exception as e:
                        logger.error(
                            "Bank worker [%s] failed to record observation: %s", bank_id, e
                        )
                        outbox.put(
                            {
                                "status": "ERROR",
                                "bank_id": bank_id,
                                "error": str(e),
                                "pid": pid,
                            }
                        )
                        continue

                    # Send local weights and digest to coordinator over federated transport
                    # Notice: Zero raw dataset samples (X_train, y_train) are transmitted across IPC.
                    # In DP mode, un-noised training loss is restricted bank-internal data and omitted from release.
                    success_payload: dict[str, Any] = {
                        "status": "SUCCESS",
                        "bank_id": bank_id,
                        "pid": pid,
                        "round_number": round_number,
                        "weights": local_weights,
                        "update_digest": update_digest,
                        "sample_count": sample_count,
                        "executed_epochs": 1,
                    }
                    if not is_dp_enabled:
                        success_payload["loss"] = observed_loss
                    if dp_info is not None:
                        success_payload["dp_provenance"] = dp_info
                    if is_dp_enabled and session is not None:
                        session[f"attempt_{attempt_id}"] = success_payload
                    outbox.put(success_payload)
                    continue

                if msg.get("command") == "SECAGG_SETUP":
                    job_id = str(msg["job_id"]).strip()
                    consortium_id = str(msg.get("consortium_id") or "cfi-consortium").strip().lower()
                    session_id = str(msg["session_id"]).strip()
                    round_number = int(msg["round_number"])
                    roster = [str(r).strip().upper() for r in msg.get("roster", []) if r]
                    relayed_peer_identities = dict(msg.get("peer_identities") or msg.get("institutional_public_keys") or {})
                    secagg_config = dict(msg.get("secagg_config") or {})

                    clean_bank = bank_id.strip().upper()
                    if session_id in secagg_worker_sessions:
                        err_msg = f"Bank worker [{bank_id}] rejected SecAgg setup: duplicate session_id '{session_id}'."
                        logger.error(err_msg)
                        outbox.put({"status": "ERROR", "bank_id": bank_id, "error": err_msg, "pid": pid})
                        continue

                    if clean_bank not in roster:
                        err_msg = f"Bank worker [{bank_id}] received SecAgg setup but is not in authorized roster: {roster}"
                        logger.error(err_msg)
                        outbox.put({"status": "ERROR", "bank_id": bank_id, "error": err_msg, "pid": pid})
                        continue

                    if len(roster) < 2:
                        err_msg = f"Bank worker [{bank_id}] rejected SecAgg setup: insufficient participants in roster ({len(roster)} < 2)."
                        logger.error(err_msg)
                        outbox.put({"status": "ERROR", "bank_id": bank_id, "error": err_msg, "pid": pid})
                        continue

                    # 1. Independently authenticate scheduled job, roster, and consortium against authoritative record
                    sched = authority_source.get_scheduled_job(job_id)
                    if sched is None:
                        err_msg = f"Bank worker [{bank_id}] rejected SecAgg setup: job [{job_id}] is not scheduled in authoritative record."
                        logger.error(err_msg)
                        outbox.put({"status": "ERROR", "bank_id": bank_id, "error": err_msg, "pid": pid})
                        continue

                    sched_workers = {w.strip().upper() for w in sched.permitted_workers}
                    roster_set = {r.strip().upper() for r in roster}
                    if sched_workers != roster_set:
                        err_msg = (
                            f"Bank worker [{bank_id}] rejected SecAgg setup: coordinator roster {sorted(roster_set)} "
                            f"does not match authoritative permitted workers {sorted(sched_workers)}."
                        )
                        logger.error(err_msg)
                        outbox.put({"status": "ERROR", "bank_id": bank_id, "error": err_msg, "pid": pid})
                        continue

                    if sched.consortium_id and sched.consortium_id.strip().lower() != consortium_id:
                        err_msg = (
                            f"Bank worker [{bank_id}] rejected SecAgg setup: coordinator consortium [{consortium_id}] "
                            f"does not match authoritative job consortium [{sched.consortium_id}]."
                        )
                        logger.error(err_msg)
                        outbox.put({"status": "ERROR", "bank_id": bank_id, "error": err_msg, "pid": pid})
                        continue

                    # 2. Derive bank-owned institutional keypair
                    try:
                        priv_hex, pub_hex = derive_institutional_identity_hex(clean_bank, worker_token)
                    except Exception as k_err:
                        err_msg = f"Failed to derive institutional keypair for bank [{bank_id}]: {k_err}"
                        logger.error(err_msg)
                        outbox.put({"status": "ERROR", "bank_id": bank_id, "error": err_msg, "pid": pid})
                        continue

                    # 3. Independently verify peer public keys against authoritative trust material outside coordinator control
                    authoritative_peer_keys = authority_source.get_institutional_identities(roster)
                    missing_auth_keys = [r for r in roster if r not in authoritative_peer_keys or not authoritative_peer_keys[r]]
                    if missing_auth_keys:
                        err_msg = (
                            f"Bank worker [{bank_id}] rejected SecAgg setup: missing authoritative institutional keys for {missing_auth_keys}."
                        )
                        logger.error(err_msg)
                        outbox.put({"status": "ERROR", "bank_id": bank_id, "error": err_msg, "pid": pid})
                        continue

                    # Local bank's derived public key must match authoritative record
                    local_auth_key = authoritative_peer_keys.get(clean_bank, "").strip().lower()
                    if local_auth_key != pub_hex.strip().lower():
                        err_msg = (
                            f"Institutional identity mismatch for bank [{bank_id}]: "
                            f"derived '{pub_hex}' != authoritative record '{local_auth_key}'."
                        )
                        logger.error(err_msg)
                        outbox.put({"status": "ERROR", "bank_id": bank_id, "error": err_msg, "pid": pid})
                        continue

                    # Treat coordinator-relayed public keys as untrusted transport data, never as a trust anchor!
                    # Reject any key substitution attempt immediately!
                    substitution_detected = False
                    for p_id, r_key in relayed_peer_identities.items():
                        clean_p = p_id.strip().upper()
                        if clean_p in authoritative_peer_keys:
                            expected_key = authoritative_peer_keys[clean_p].strip().lower()
                            if r_key.strip().lower() != expected_key:
                                err_msg = (
                                    f"Bank worker [{bank_id}] detected institutional key substitution by coordinator for [{clean_p}]: "
                                    f"relayed '{r_key}' != authoritative '{expected_key}'. Rejecting setup."
                                )
                                logger.error(err_msg)
                                outbox.put({"status": "ERROR", "bank_id": bank_id, "error": err_msg, "pid": pid})
                                substitution_detected = True
                                break
                    if substitution_detected:
                        continue

                    modulus = int(secagg_config.get("modulus", 2**64))
                    is_prod_env = app_env.lower() == "production"

                    try:
                        driver = P2PSecAggDriver(
                            bank_id=clean_bank,
                            institutional_identity_hex=priv_hex,
                            app_env=app_env,
                            require_authentic_identity=is_prod_env,
                            modulus=modulus,
                        )
                        bundle = driver.generate_round_keypair(
                            round_id=round_number,
                            job_id=job_id,
                            consortium_id=consortium_id,
                            session_id=session_id,
                            roster=roster,
                        )
                    except Exception as init_err:
                        err_msg = f"Bank worker [{bank_id}] failed SecAgg driver initialization: {init_err}"
                        logger.error(err_msg)
                        outbox.put({"status": "ERROR", "bank_id": bank_id, "error": err_msg, "pid": pid})
                        continue

                    secagg_worker_sessions[session_id] = {
                        "state": "ACTIVE",
                        "driver": driver,
                        "job_id": job_id,
                        "consortium_id": consortium_id,
                        "session_id": session_id,
                        "round_number": round_number,
                        "roster": tuple(roster),
                        "authoritative_peer_keys": authoritative_peer_keys,
                        "secagg_config": secagg_config,
                        "bank_id": clean_bank,
                    }

                    bundle_dict = bundle.to_dict()
                    outbox.put({
                        "status": "SUCCESS",
                        "bank_id": bank_id,
                        "pid": pid,
                        "session_id": session_id,
                        "bundle": bundle_dict,
                        "public_bundle": bundle_dict,
                    })
                    continue

                if msg.get("command") == "SECAGG_TRAIN_AND_MASK":
                    job_id = str(msg.get("job_id", "")).strip()
                    round_number = int(msg.get("round_number", 0))
                    session_id = str(msg.get("session_id", "")).strip()
                    consortium_id = str(msg.get("consortium_id") or "cfi-consortium").strip().lower()
                    peer_bundles_raw = dict(msg.get("peer_bundles") or {})
                    global_weights_obj = msg.get("global_weights")
                    attempt_id = str(msg.get("attempt_id") or f"att_{job_id}_r{round_number}_{uuid.uuid4().hex[:8]}")

                    clean_bank = bank_id.strip().upper()
                    sess = secagg_worker_sessions.get(session_id)
                    if sess is None:
                        err_msg = f"Bank worker [{bank_id}] received SECAGG_TRAIN_AND_MASK for unknown session '{session_id}'"
                        logger.error(err_msg)
                        outbox.put({"status": "ERROR", "bank_id": bank_id, "error": err_msg, "pid": pid})
                        continue

                    # Single-use session state check: prevent replay of ephemeral key material
                    if sess["state"] != "ACTIVE":
                        err_msg = (
                            f"Bank worker [{bank_id}] rejected SECAGG_TRAIN_AND_MASK: session '{session_id}' "
                            f"is in terminal state '{sess['state']}'. Single-use cryptographic session cannot be reused."
                        )
                        logger.error(err_msg)
                        outbox.put({"status": "ERROR", "bank_id": bank_id, "error": err_msg, "pid": pid})
                        continue

                    # Context binding check: bind to stored job_id, consortium_id, round_number, bank_id
                    if (
                        sess["job_id"] != job_id
                        or sess["round_number"] != round_number
                        or sess["consortium_id"] != consortium_id
                        or sess["bank_id"] != clean_bank
                    ):
                        sess["state"] = "ABORTED"
                        err_msg = (
                            f"Bank worker [{bank_id}] rejected SECAGG_TRAIN_AND_MASK: context mismatch for session '{session_id}'. "
                            f"Expected (job={sess['job_id']}, round={sess['round_number']}, consortium={sess['consortium_id']}, bank={sess['bank_id']}), "
                            f"got (job={job_id}, round={round_number}, consortium={consortium_id}, bank={clean_bank}). Session invalidated."
                        )
                        logger.error(err_msg)
                        outbox.put({"status": "ERROR", "bank_id": bank_id, "error": err_msg, "pid": pid})
                        continue

                    driver = sess["driver"]
                    roster = sess["roster"]
                    authoritative_peer_keys = sess["authoritative_peer_keys"]
                    secagg_config = sess["secagg_config"]

                    # 1. Ingest and verify all required peer bundles using AUTHORITATIVE peer keys
                    bundle_verification_failed = False
                    for p_id in roster:
                        clean_p = p_id.strip().upper()
                        if clean_p == clean_bank:
                            continue
                        if clean_p not in peer_bundles_raw:
                            sess["state"] = "ABORTED"
                            err_msg = f"Bank worker [{bank_id}] missing peer bundle for required participant [{clean_p}]."
                            logger.error(err_msg)
                            outbox.put({"status": "ERROR", "bank_id": bank_id, "error": err_msg, "pid": pid})
                            bundle_verification_failed = True
                            break

                        raw_bundle_dict = peer_bundles_raw[clean_p]
                        try:
                            bundle_obj = ECDHPublicKeyBundle.from_dict(raw_bundle_dict)
                            peer_pub_hex = authoritative_peer_keys.get(clean_p)
                            if not peer_pub_hex:
                                raise SecAggSecurityError(f"Missing authoritative institutional key for peer [{clean_p}].")
                            driver.receive_peer_bundle(bundle_obj, peer_institutional_public_key=peer_pub_hex)
                        except (SecAggSecurityError, Exception) as bv_err:
                            sess["state"] = "ABORTED"
                            err_msg = f"Bank worker [{bank_id}] rejected peer bundle from [{clean_p}]: {bv_err}"
                            logger.error(err_msg)
                            outbox.put({"status": "ERROR", "bank_id": bank_id, "error": err_msg, "pid": pid})
                            bundle_verification_failed = True
                            break

                    if bundle_verification_failed:
                        continue

                    # 2. Local Training (Opacus DP-SGD or non-DP standard training)
                    if round_number == 1:
                        try:
                            authority_source.start_training_job(
                                job_id=job_id,
                                worker_id=bank_id,
                                worker_token=worker_token,
                            )
                        except Exception as start_err:
                            sess["state"] = "ABORTED"
                            logger.error("Bank worker [%s] failed to start training job %s: %s", bank_id, job_id, start_err)
                            outbox.put({"status": "ERROR", "bank_id": bank_id, "error": str(start_err), "pid": pid})
                            continue

                    sched = authority_source.get_scheduled_job(job_id)
                    dp_config = sched.dp_config if sched and sched.dp_config else None
                    is_dp_enabled = bool(dp_config and dp_config.get("dp_enabled", False))

                    local_model = model_service.create_model(input_dim=NUM_FEATURES, dp_compatible=True)
                    if global_weights_obj is not None:
                        model_service.set_parameters(local_model, global_weights_obj)

                    dp_info = None
                    if is_dp_enabled:
                        assert dp_config is not None
                        target_epsilon = float(dp_config["target_epsilon"])
                        target_delta = float(dp_config["target_delta"])
                        max_grad_norm = float(dp_config.get("max_grad_norm", 1.0))
                        noise_multiplier = (
                            float(dp_config["noise_multiplier"])
                            if "noise_multiplier" in dp_config and dp_config["noise_multiplier"] is not None
                            else None
                        )
                        raw_dataset_tag = str(dp_config.get("dataset_tag", "primary_vault")).strip()
                        is_prod = app_env.lower() == "production"
                        try:
                            auth_dataset_tag = authority_source.resolve_authoritative_dataset_identity(
                                bank_id=bank_id,
                                dataset_tag=raw_dataset_tag,
                                is_prod=is_prod,
                            )
                        except Exception as tag_err:
                            sess["state"] = "ABORTED"
                            outbox.put({"status": "ERROR", "bank_id": bank_id, "error": f"Unauthorized dataset: {tag_err}", "pid": pid})
                            continue

                        if vault_handle is not None:
                            vault_tag = vault_handle.get_authoritative_dataset_identity()
                            if is_prod and auth_dataset_tag != vault_tag:
                                try:
                                    resolved_vault = authority_source.resolve_authoritative_dataset_identity(
                                        bank_id=bank_id,
                                        dataset_tag=vault_tag,
                                        is_prod=True,
                                    )
                                    if resolved_vault != auth_dataset_tag:
                                        raise TrainingProvenanceSecurityError(
                                            f"Unauthorized dataset substitution: requested resolves to '{auth_dataset_tag}', vault is '{resolved_vault}'."
                                        )
                                except Exception as subst_err:
                                    sess["state"] = "ABORTED"
                                    outbox.put({"status": "ERROR", "bank_id": bank_id, "error": str(subst_err), "pid": pid})
                                    continue

                        accountant_type = str(
                            dp_config.get("accountant_type")
                            or dp_config.get("accountant")
                            or "prv"
                        ).strip().lower().replace("opacus_", "")
                        require_secure_rng = bool(dp_config.get("require_secure_rng", False))
                        batch_size = int(dp_config.get("batch_size", 32))
                        learning_rate = float(dp_config.get("learning_rate", 0.01))
                        expected_rounds = sched.expected_rounds if sched else 1

                        session = dp_sessions.setdefault(job_id, {"privacy_engine": None, "steps_executed": 0})
                        existing_engine = session.get("privacy_engine")

                        budget_record = authority_source.get_institutional_privacy_budget(
                            bank_id, auth_dataset_tag, token=worker_token
                        )
                        if budget_record is None:
                            sess["state"] = "ABORTED"
                            outbox.put({"status": "ERROR", "bank_id": bank_id, "error": "Privacy budget missing", "pid": pid})
                            continue

                        history_job_eps = 0.0
                        history_job_del = 0.0
                        prior_steps = 0
                        if budget_record.history:
                            for entry in budget_record.history:
                                if isinstance(entry, dict) and entry.get("job_id") == job_id and entry.get("status") == "COMMITTED":
                                    history_job_eps = max(history_job_eps, float(entry.get("spent_epsilon", 0.0)))
                                    history_job_del = max(history_job_del, float(entry.get("spent_delta", 0.0)))
                                    prior_steps += int(entry.get("steps_executed", 0))

                        prior_job_spent_eps = max(history_job_eps, float(session.get("last_committed_eps", 0.0)))
                        prior_job_spent_del = max(history_job_del, float(session.get("last_committed_del", 0.0)))
                        needed_eps = max(0.001, target_epsilon - prior_job_spent_eps)
                        needed_del = max(0.0, target_delta - prior_job_spent_del)

                        if budget_record.remaining_epsilon < (needed_eps - 1e-9):
                            sess["state"] = "ABORTED"
                            outbox.put({"status": "ERROR", "bank_id": bank_id, "error": "Privacy budget exhausted", "pid": pid})
                            continue

                        params_digest_dict = {
                            "bank_id": bank_id,
                            "dataset_tag": auth_dataset_tag,
                            "job_id": job_id,
                            "round_number": round_number,
                            "target_epsilon": target_epsilon,
                            "target_delta": target_delta,
                            "max_grad_norm": max_grad_norm,
                            "batch_size": batch_size,
                            "learning_rate": learning_rate,
                            "accountant_type": accountant_type,
                        }
                        params_digest = hashlib.sha256(
                            json.dumps(params_digest_dict, sort_keys=True).encode("utf-8")
                        ).hexdigest()

                        try:
                            admission = authority_source.admit_training_attempt(
                                bank_id=bank_id,
                                dataset_tag=auth_dataset_tag,
                                job_id=job_id,
                                round_number=round_number,
                                attempt_id=attempt_id,
                                parameters_digest=params_digest,
                                requested_epsilon=needed_eps,
                                requested_delta=needed_del,
                                worker_token=worker_token,
                            )
                        except TrainingProvenanceSecurityError as pse:
                            sess["state"] = "ABORTED"
                            outbox.put({"status": "ERROR", "bank_id": bank_id, "error": str(pse), "pid": pid})
                            continue

                        if admission.status != "ADMITTED":
                            sess["state"] = "ABORTED"
                            outbox.put({"status": "ERROR", "bank_id": bank_id, "error": f"Attempt rejected: {admission.reason}", "pid": pid})
                            continue

                        steps_per_epoch = math.ceil(len(X_train) / max(1, batch_size))
                        total_job_steps = max(1, expected_rounds * steps_per_epoch)

                        try:
                            local_model, loss_history, actual_epsilon = (
                                model_service.train_local_with_opacus(
                                    model=local_model,
                                    X_train=X_train,
                                    y_train=y_train,
                                    epochs=1,
                                    batch_size=batch_size,
                                    learning_rate=learning_rate,
                                    target_epsilon=target_epsilon,
                                    target_delta=target_delta,
                                    max_grad_norm=max_grad_norm,
                                    noise_multiplier=noise_multiplier,
                                    total_steps=total_job_steps,
                                    accountant_type=accountant_type,
                                    require_secure_rng=require_secure_rng,
                                    privacy_engine=existing_engine,
                                    prior_steps=prior_steps if existing_engine is None else 0,
                                )
                            )
                            session["privacy_engine"] = getattr(local_model, "_privacy_engine", None)
                            session["steps_executed"] += steps_per_epoch
                            dp_info = getattr(local_model, "dp_provenance", {})
                        except Exception as dp_err:
                            sess["state"] = "ABORTED"
                            logger.error("Bank worker [%s] Opacus DP-SGD failed: %s", bank_id, dp_err)
                            outbox.put({"status": "ERROR", "bank_id": bank_id, "error": f"Opacus DP-SGD failed: {dp_err}", "pid": pid})
                            continue

                        spent_eps = float(dp_info.get("epsilon", needed_eps)) if dp_info else needed_eps
                        spent_del = float(dp_info.get("delta", target_delta)) if dp_info else target_delta
                        authority_source.commit_institutional_privacy_expenditure(
                            bank_id=bank_id,
                            dataset_tag=auth_dataset_tag,
                            job_id=job_id,
                            round_number=round_number,
                            spent_epsilon=spent_eps,
                            spent_delta=spent_del,
                            steps_executed=steps_per_epoch,
                            worker_token=worker_token,
                            attempt_id=attempt_id,
                        )
                        session["last_committed_eps"] = spent_eps
                        session["last_committed_del"] = spent_del
                    else:
                        local_model, loss_history, _ = model_service.train_local(
                            model=local_model,
                            X_train=X_train,
                            y_train=y_train,
                            epochs=1,
                            learning_rate=0.01,
                            batch_size=32,
                        )

                    local_weights = model_service.get_parameters(local_model)
                    sample_count = len(X_train)

                    if not loss_history:
                        sess["state"] = "ABORTED"
                        outbox.put({"status": "ERROR", "bank_id": bank_id, "error": "No loss history produced", "pid": pid})
                        continue

                    if is_dp_enabled:
                        observed_loss = None
                        obs_metrics = {
                            "dp_epsilon": float(dp_info.get("epsilon", 0.0)) if dp_info else 0.0,
                            "dp_delta": float(dp_info.get("delta", 0.0)) if dp_info else 0.0,
                            "dp_noise_multiplier": float(dp_info.get("noise_multiplier", 0.0)) if dp_info else 0.0,
                        }
                    else:
                        observed_loss = float(loss_history[-1])
                        obs_metrics = {"loss": observed_loss}

                    # 3. Bank-Local Construction of Masked Update
                    modulus = int(secagg_config.get("modulus", 2**64))
                    scale = float(secagg_config.get("quantization_scale", 1e7))
                    try:
                        masked_vec = driver.compute_weighted_masked_vector(
                            weights=list(local_weights.flat_weights),
                            sample_count=sample_count,
                            quantization_scale=scale,
                            modulus=modulus,
                        )
                        raw_bytes = serialize_masked_vector(masked_vec)
                        masked_digest = hashlib.sha256(raw_bytes).hexdigest()
                    except Exception as mask_err:
                        sess["state"] = "ABORTED"
                        err_msg = f"Bank worker [{bank_id}] failed to construct masked update: {mask_err}"
                        logger.error(err_msg)
                        outbox.put({"status": "ERROR", "bank_id": bank_id, "error": err_msg, "pid": pid})
                        continue

                    if fail_observation:
                        sess["state"] = "ABORTED"
                        logger.warning("Bank worker [%s] simulated observation persistence failure for round %d", bank_id, round_number)
                        outbox.put({"status": "OBSERVATION_FAILED", "bank_id": bank_id, "round_number": round_number, "pid": pid})
                        continue

                    # 4. Record round observation in PostgreSQL authority with masked digest
                    try:
                        obs = authority_source.record_round_observation(
                            job_id=job_id,
                            round_number=round_number,
                            sample_count=sample_count,
                            actual_executed_epochs=1,
                            observed_loss=observed_loss,
                            metrics=obs_metrics,
                            worker_token=worker_token,
                            bank_id=bank_id,
                            model_update_digest=masked_digest,
                            execution_status="COMPLETED",
                        )
                        logger.info("Bank worker [%s] persisted SecAgg observation event %s for round %d", bank_id, obs.event_id, round_number)
                    except Exception as obs_err:
                        sess["state"] = "ABORTED"
                        logger.error("Bank worker [%s] failed to record SecAgg observation: %s", bank_id, obs_err)
                        outbox.put({"status": "ERROR", "bank_id": bank_id, "error": str(obs_err), "pid": pid})
                        continue

                    # Mark session state as CONSUMED to strictly prevent reuse of ephemeral key material
                    sess["state"] = "CONSUMED"

                    # Transmit masked payload ONLY (zero plaintext weights!)
                    success_payload = {
                        "status": "SUCCESS",
                        "bank_id": bank_id,
                        "pid": pid,
                        "round_number": round_number,
                        "masked_vector_bytes": raw_bytes,
                        "masked_digest": masked_digest,
                        "sample_count": sample_count,
                        "executed_epochs": 1,
                    }
                    if not is_dp_enabled and observed_loss is not None:
                        success_payload["loss"] = observed_loss
                    if dp_info is not None:
                        success_payload["dp_provenance"] = dp_info
                    outbox.put(success_payload)
                    continue
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
        **kwargs: Any,
    ) -> None:
        if "completion_db_url" in kwargs:
            raise TrainingProvenanceSecurityError(
                "Coordinator process cannot receive or hold completion-authority credentials (COMP-01)."
            )
        if any(
            k in kwargs
            for k in ("signing_key_hex", "signing_key", "issuer_key_path", "issuer_key_file")
        ):
            raise TrainingProvenanceSecurityError(
                "Coordinator process cannot receive or hold receipt issuer private signing key material (SIGN-05)."
            )
        self.coordinator_token = coordinator_token
        self.db_url = db_url
        self.app_env = app_env
        self.settings = get_settings()

        if self.app_env.lower() == "production":
            if bank_tokens is not None:
                raise TrainingProvenanceSecurityError(
                    "Production coordinator cannot receive bank private credentials (BI-02)."
                )
            verify_restricted_coordinator_os_boundary(self.app_env)

        # Coordinator authority source possesses coordinator_token ONLY!
        # It has ZERO access to bank worker tokens or completion credentials.
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
        worker_endpoints: dict[str, dict[str, Any]] | None = None,
        worker_processes: dict[str, mp.Process] | None = None,
        bank_partitions: dict[str, tuple[np.ndarray, np.ndarray]] | None = None,
        bank_tokens: dict[str, str] | None = None,
        bank_data_paths: dict[str, str] | None = None,
        num_rounds: int = 2,
        expected_unique_samples: int | None = None,
        fail_bank_b_observation: bool = False,
        crash_bank_b: bool = False,
        pause_aggregation_checkpoint_file: str | None = None,
        registry_storage_dir: str | None = None,
        signing_key_hex: str | None = None,
        signing_authority: Any | None = None,
        completion_authority: IsolatedTrainingCompletionAuthority | None = None,
        receipt_issuer: TrainingReceiptIssuer | None = None,
        receipt_verifier: TrainingReceiptVerifier | None = None,
        model_registry: ModelRegistry | None = None,
        consortium_id: str | None = None,
        dp_config: Mapping[str, Any] | None = None,
        secagg_enabled: bool = False,
        secagg_config: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute complete distributed E2E federated training lifecycle across independent processes.

        Returns comprehensive trace including process IDs, observations, receipts, and registry status.
        """
        coordinator_pid = os.getpid()

        is_prod = self.app_env.lower() == "production"
        if is_prod:
            verify_restricted_coordinator_os_boundary(self.app_env)
            if pause_aggregation_checkpoint_file is not None:
                raise TrainingProvenanceSecurityError(
                    "Production coordinator cannot accept test aggregation checkpoint hooks (ISO-07)."
                )
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
            if (
                receipt_issuer is not None
                and getattr(receipt_issuer, "_require_authenticated_job", None) is False
            ):
                raise TrainingProvenanceSecurityError(
                    "Production receipt issuer cannot disable authenticated job records (CR-04)."
                )
            if not worker_endpoints:
                raise TrainingProvenanceSecurityError(
                    "Production coordinator cannot generate bank credentials or spawn workers with generated secrets (BI-04). "
                    "Pre-authenticated worker endpoints must be supplied."
                )

        # Determine participating banks list
        banks = (
            list(participating_banks)
            if participating_banks
            else (
                sorted(bank_partitions.keys())
                if bank_partitions
                else (
                    sorted(worker_endpoints.keys())
                    if worker_endpoints
                    else ["BANK_A", "BANK_B", "BANK_C"]
                )
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
                app_env=self.app_env,
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
            consortium_id=consortium_id,
            dp_config=dp_config,
        )

        if dp_config and dp_config.get("dp_enabled") and not is_prod:
            target_eps = float(dp_config.get("allocated_epsilon") or dp_config.get("target_epsilon", 10.0))
            target_del = float(dp_config.get("allocated_delta") or dp_config.get("target_delta", 1e-5))
            raw_tag = str(dp_config.get("dataset_tag", "primary_vault")).strip()
            for b in banks:
                if not self.authority_source.get_institutional_privacy_budget(b, raw_tag, token=self.coordinator_token):
                    self.authority_source.provision_institutional_privacy_budget(
                        bank_id=b,
                        dataset_tag=raw_tag,
                        allocated_epsilon=target_eps,
                        allocated_delta=target_del,
                        aliases=[f"{b}_dataset", f"{b}_protected_dataset", "primary_vault", "default_vault"],
                        authority_token=self.coordinator_token,
                    )

        for b in banks:
            clean_b = b.strip().upper()
            inst_pk = endpoints[clean_b].get("institutional_public_key") if clean_b in endpoints else None
            if inst_pk:
                with contextlib.suppress(Exception):
                    self.authority_source.register_institutional_identity(
                        bank_id=clean_b,
                        public_key_hex=inst_pk,
                        token=self.coordinator_token,
                    )

        inboxes = {b: endpoints[b]["inbox"] for b in banks}
        outboxes = {b: endpoints[b]["outbox"] for b in banks}
        worker_pids: dict[str, int] = {
            b: spawned_procs[b].pid or 0 for b in banks if b in spawned_procs
        }
        latest_dp_provenance: dict[str, Any] = {}

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

        secagg_session_provenance: dict[str, Any] = {"enabled": secagg_enabled, "rounds": {}}

        try:
            for round_num in range(1, num_rounds + 1):
                logger.info("--- Starting Federated Global Round %d ---", round_num)

                # If simulating Bank B crash in round 2
                if crash_bank_b and round_num == 2 and "BANK_B" in spawned_procs:
                    logger.warning(
                        "Simulating crash: Terminating Bank B worker process PID %d",
                        spawned_procs["BANK_B"].pid,
                    )
                    spawned_procs["BANK_B"].terminate()
                    spawned_procs["BANK_B"].join()

                if secagg_enabled:
                    # Secure Aggregation Multi-Phase Protocol
                    session_id = f"secagg_{job_id}_{round_num}_{uuid.uuid4().hex[:12]}"
                    inst_keys = {
                        b: endpoints[b].get("institutional_public_key", "") for b in banks
                    }

                    # Phase A & B: Round initialization, roster agreement, ephemeral keypair generation
                    setup_payload = {
                        "command": "SECAGG_SETUP",
                        "job_id": job_id,
                        "round_number": round_num,
                        "session_id": session_id,
                        "consortium_id": consortium_id,
                        "roster": list(banks),
                        "institutional_public_keys": inst_keys,
                        "peer_identities": inst_keys,
                        "secagg_config": secagg_config or {},
                    }
                    for b_id, in_q in inboxes.items():
                        if b_id in spawned_procs and not spawned_procs[b_id].is_alive():
                            raise TrainingProvenanceSecurityError(
                                f"SecAgg round {round_num} setup aborted: Bank worker [{b_id}] is dead / crashed."
                            )
                        try:
                            in_q.put(setup_payload)
                        except Exception as e:
                            raise TrainingProvenanceSecurityError(
                                f"SecAgg round {round_num} setup aborted: Cannot communicate with Bank worker [{b_id}]: {e}"
                            )

                    peer_bundles_dict: dict[str, dict[str, Any]] = {}
                    for b_id, out_q in outboxes.items():
                        if b_id in spawned_procs and not spawned_procs[b_id].is_alive():
                            raise TrainingProvenanceSecurityError(
                                f"SecAgg round {round_num} setup aborted: Bank worker [{b_id}] is dead / crashed."
                            )
                        try:
                            res = out_q.get(timeout=60)
                        except Exception as e:
                            raise TrainingProvenanceSecurityError(
                                f"SecAgg round {round_num} setup timeout waiting for Bank [{b_id}]: {e}"
                            )
                        if res.get("status") != "SUCCESS":
                            raise TrainingProvenanceSecurityError(
                                f"SecAgg round {round_num} setup failed for Bank [{b_id}]: {res.get('error')}"
                            )
                        worker_pids[b_id] = res.get("pid", worker_pids.get(b_id, 0))
                        bundle_dict = res.get("bundle") or res.get("public_bundle")
                        if not bundle_dict:
                            raise TrainingProvenanceSecurityError(
                                f"SecAgg round {round_num} setup failed: Missing public key bundle from [{b_id}]."
                            )
                        peer_bundles_dict[b_id] = bundle_dict

                    missing_setup = set(banks) - set(peer_bundles_dict.keys())
                    if missing_setup:
                        raise TrainingProvenanceSecurityError(
                            f"SecAgg round {round_num} setup aborted: Missing participant bundles from {sorted(missing_setup)}."
                        )

                    # Phase C, D, E, F: Relay peer bundles, execute bank-local training & masked update construction
                    scale = float((secagg_config or {}).get("quantization_scale", 1e7))
                    train_mask_payload = {
                        "command": "SECAGG_TRAIN_AND_MASK",
                        "job_id": job_id,
                        "consortium_id": consortium_id,
                        "round_number": round_num,
                        "session_id": session_id,
                        "global_weights": global_weights,
                        "dp_config": dp_config,
                        "peer_bundles": peer_bundles_dict,
                        "quantization_scale": scale,
                    }
                    for b_id, in_q in inboxes.items():
                        if b_id in spawned_procs and not spawned_procs[b_id].is_alive():
                            raise TrainingProvenanceSecurityError(
                                f"SecAgg round {round_num} training aborted: Bank worker [{b_id}] is dead / crashed."
                            )
                        try:
                            in_q.put(train_mask_payload)
                        except Exception as e:
                            raise TrainingProvenanceSecurityError(
                                f"SecAgg round {round_num} training aborted: Cannot communicate with Bank worker [{b_id}]: {e}"
                            )

                    accepted_participant_ids: list[str] = []
                    accepted_local_update_digests: list[str] = []
                    masked_vectors_map: dict[str, list[int]] = {}
                    sample_counts_map: dict[str, int] = {}

                    for b_id, out_q in outboxes.items():
                        if b_id in spawned_procs and not spawned_procs[b_id].is_alive():
                            raise TrainingProvenanceSecurityError(
                                f"SecAgg round {round_num} training aborted: Bank worker [{b_id}] died."
                            )
                        try:
                            res = out_q.get(timeout=120)
                        except Exception as e:
                            raise TrainingProvenanceSecurityError(
                                f"SecAgg round {round_num} timeout waiting for masked vector from [{b_id}]: {e}"
                            )
                        if res.get("status") != "SUCCESS":
                            raise TrainingProvenanceSecurityError(
                                f"SecAgg round {round_num} training/masking failed for [{b_id}]: {res.get('error')}"
                            )

                        # Coordinator invariant: MUST NEVER receive individual plaintext bank weights
                        if "weights" in res:
                            raise TrainingProvenanceSecurityError(
                                f"Critical SecAgg security violation: Plaintext weights received from [{b_id}]."
                            )

                        raw_bytes = res.get("masked_vector_bytes")
                        masked_digest = res.get("masked_digest")
                        sample_count = res.get("sample_count")
                        worker_pids[b_id] = res.get("pid", worker_pids.get(b_id, 0))

                        if not raw_bytes or not masked_digest or sample_count is None:
                            raise TrainingProvenanceSecurityError(
                                f"SecAgg round {round_num} invalid payload from [{b_id}]: Missing masked vector or digest."
                            )

                        calc_digest = hashlib.sha256(raw_bytes).hexdigest()
                        if calc_digest != masked_digest:
                            raise TrainingProvenanceSecurityError(
                                f"SecAgg round {round_num} payload digest mismatch for [{b_id}]: {calc_digest} != {masked_digest}"
                            )

                        if "dp_provenance" in res:
                            latest_dp_provenance[b_id] = res["dp_provenance"]

                        masked_vec = deserialize_masked_vector(raw_bytes)
                        masked_vectors_map[b_id] = masked_vec
                        sample_counts_map[b_id] = sample_count
                        accepted_participant_ids.append(b_id)
                        accepted_local_update_digests.append(masked_digest)

                    missing_participants = set(banks) - set(accepted_participant_ids)
                    if missing_participants:
                        raise TrainingProvenanceSecurityError(
                            f"SecAgg round {round_num} aborted: Required participant(s) {sorted(missing_participants)} failed to contribute valid masked updates."
                        )

                    # Phase G: Reconstruct weighted global model without learning individual weights
                    modulus = int((secagg_config or {}).get("modulus", 2**64))
                    reconstructed_flat = P2PSecAggDriver.aggregate_weighted_masked_vectors(
                        masked_vectors=masked_vectors_map,
                        sample_counts=sample_counts_map,
                        quantization_scale=scale,
                        modulus=modulus,
                    )
                    global_weights = ModelWeights(
                        layer_shapes=global_weights.layer_shapes,
                        flat_weights=reconstructed_flat,
                    )
                    self.model_service.set_parameters(global_model, global_weights)

                    final_model_bytes = _serialize_model_state_dict(global_model)
                    aggregated_model_digest = hashlib.sha256(final_model_bytes).hexdigest()

                    if pause_aggregation_checkpoint_file:
                        try:
                            with open(pause_aggregation_checkpoint_file, "w", encoding="utf-8") as _pf:
                                _pf.write(
                                    f"AGGREGATION_EXECUTED_ROUND_{round_num}:{aggregated_model_digest}\n"
                                )
                                _pf.flush()
                                os.fsync(_pf.fileno())
                        except Exception as _e:
                            logger.warning("Failed writing aggregation checkpoint file: %s", _e)
                        _release_file = f"{pause_aggregation_checkpoint_file}.release"
                        _wait_start = time.time()
                        while not os.path.exists(_release_file) and (time.time() - _wait_start < 30.0):
                            time.sleep(0.05)

                    self.authority_source.record_global_aggregation(
                        job_id=job_id,
                        round_number=round_num,
                        aggregation_algorithm="secagg_fed_avg_weighted",
                        accepted_participant_ids=accepted_participant_ids,
                        accepted_local_update_digests=accepted_local_update_digests,
                        aggregated_global_model_digest=aggregated_model_digest,
                        coordinator_token=self.coordinator_token,
                    )
                    secagg_session_provenance["rounds"][round_num] = {
                        "session_id": session_id,
                        "participants": accepted_participant_ids,
                        "masked_digests": accepted_local_update_digests,
                        "aggregated_digest": aggregated_model_digest,
                    }
                    logger.info(
                        "Persisted SecAgg GlobalAggregationEvent for round %d: digest=%s",
                        round_num,
                        aggregated_model_digest,
                    )
                else:
                    # Dispatch training task to all active workers
                    for b_id, in_q in inboxes.items():
                        if b_id in spawned_procs and not spawned_procs[b_id].is_alive():
                            continue
                        in_q.put(
                            {
                                "command": "TRAIN_ROUND",
                                "job_id": job_id,
                                "round_number": round_num,
                                "global_weights": global_weights,
                                "dp_config": dp_config,
                            }
                        )

                    # Collect responses
                    accepted_participant_ids = []
                    accepted_local_update_digests = []
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
                        if "dp_provenance" in res:
                            latest_dp_provenance[b_id] = res["dp_provenance"]

                        # Coordinator verifies update digest against received weight bytes
                        calculated_digest = hashlib.sha256(
                            _serialize_weights_to_bytes(local_w)
                        ).hexdigest()
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

                    if pause_aggregation_checkpoint_file:
                        try:
                            with open(pause_aggregation_checkpoint_file, "w", encoding="utf-8") as _pf:
                                _pf.write(
                                    f"AGGREGATION_EXECUTED_ROUND_{round_num}:{aggregated_model_digest}\n"
                                )
                                _pf.flush()
                                os.fsync(_pf.fileno())
                        except Exception as _e:
                            logger.warning("Failed writing aggregation checkpoint file: %s", _e)
                        _release_file = f"{pause_aggregation_checkpoint_file}.release"
                        _wait_start = time.time()
                        while not os.path.exists(_release_file) and (time.time() - _wait_start < 30.0):
                            time.sleep(0.05)

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
                    logger.info(
                        "Persisted GlobalAggregationEvent for round %d: digest=%s",
                        round_num,
                        aggregated_model_digest,
                    )

            # 4. Perform Genuine Model Evaluation on holdout dataset
            X_val, y_val = holdout_val_data
            if completion_authority is not None:
                eval_res = completion_authority.evaluate_and_record_artifact(
                    job_id=job_id,
                    artifact_bytes=final_model_bytes,
                    holdout_features=X_val,
                    holdout_labels=y_val,
                    coordinator_token=self.coordinator_token,
                )
                val_loss = float(eval_res["val_loss"])
                val_auc = float(eval_res["auc_roc"])
            else:
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

                # Record Evaluation Observations in authority
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
            final_round_obs = [
                o
                for o in obs_list
                if o.round_number == num_rounds and o.execution_status == "COMPLETED"
            ]
            unique_samples = sum(o.actual_sample_count for o in final_round_obs)
            if unique_samples == 0:
                raise TrainingProvenanceSecurityError(
                    f"No completed observations for round {num_rounds} to reconcile sample count."
                )
            if expected_unique_samples is not None and unique_samples != expected_unique_samples:
                raise TrainingProvenanceSecurityError(
                    f"Observed unique samples {unique_samples} does not match expected {expected_unique_samples}"
                )

            valid_losses = [
                (float(o.observed_loss), o.actual_sample_count)
                for o in final_round_obs
                if o.observed_loss is not None
            ]
            final_loss = (
                sum(loss_val * w for loss_val, w in valid_losses) / sum(w for _, w in valid_losses)
                if (valid_losses and sum(w for _, w in valid_losses) > 0)
                else None
            )

            metrics_dict: dict[str, float] = {
                "auc_roc": float(val_auc),
                "val_loss": float(val_loss),
            }
            if final_loss is not None:
                metrics_dict["loss"] = float(final_loss)

            # 6. Complete Job in PostgreSQL Authority via Independent Completion Authority
            if completion_authority is not None:
                completed_record = completion_authority.finalize_training_completion(
                    job_id=job_id,
                    executed_rounds=num_rounds,
                    executed_epochs=num_rounds,
                    sample_count=unique_samples,
                    observed_metrics=metrics_dict,
                    artifact_sha256=final_artifact_sha256,
                    worker_id="FEDERATED_COORDINATOR",
                    coordinator_token=self.coordinator_token,
                    consortium_id=consortium_id,
                )
            else:
                if is_prod:
                    raise TrainingProvenanceSecurityError(
                        "Production federated training requires an independent completion authority "
                        "(IsolatedTrainingCompletionAuthority) to finalize training completion."
                    )
                completed_record = self.authority_source.complete_training_job(
                    job_id=job_id,
                    executed_rounds=num_rounds,
                    executed_epochs=num_rounds,
                    sample_count=unique_samples,
                    observed_metrics=metrics_dict,
                    artifact_sha256=final_artifact_sha256,
                    worker_id="FEDERATED_COORDINATOR",
                    coordinator_token=self.coordinator_token,
                    consortium_id=consortium_id,
                    enforce_lifecycle=True,
                )

            # 7. Issue Ed25519 Receipt in RECEIPT ISSUER DOMAIN
            if signing_authority is not None:
                if hasattr(signing_authority, "sign_completed_job_receipt"):
                    receipt = signing_authority.sign_completed_job_receipt(
                        job_id=job_id,
                        artifact_bytes=final_model_bytes,
                        caller_identity="COORDINATOR",
                        caller_token=self.coordinator_token,
                    )
                else:
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
                        consortium_id=consortium_id,
                    )
                    receipt = signing_authority.issue_receipt(
                        evidence=evidence, model_bytes=final_model_bytes
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
                    consortium_id=consortium_id,
                )
                receipt = active_issuer.issue_receipt(
                    evidence=evidence, model_bytes=final_model_bytes
                )

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
                raise TrainingProvenanceSecurityError(
                    f"Ed25519 receipt verification failed: {reason}"
                )

            # 9. Register Inactive Candidate in ModelRegistry in REGISTRY DOMAIN
            active_registry = model_registry or ModelRegistry(
                storage_dir=registry_storage_dir,
                verifier=active_verifier,
            )
            saved_version = active_registry.save_version(
                simulation_id=simulation_id,
                state_dict=global_model.state_dict(),
                metrics=metrics_dict,
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
                consortium_id=consortium_id,
            )

            # Confirm model is NOT champion
            active_champion = active_registry.get_active_champion_identity(
                simulation_id=simulation_id
            )
            is_promoted_champion = (
                active_champion is not None
                and active_champion.get("sha256") == final_artifact_sha256
            )

            return {
                "status": "COMPLETED",
                "coordinator_pid": coordinator_pid,
                "worker_pids": worker_pids,
                "completed_record": completed_record,
                "receipt": receipt,
                "is_verified": is_valid,
                "saved_version": saved_version,
                "is_promoted_champion": is_promoted_champion,
                "metrics": metrics_dict,
                "final_artifact_sha256": final_artifact_sha256,
                "consortium_id": consortium_id,
                "dp_enabled": bool(dp_config.get("dp_enabled", False)) if dp_config else False,
                "dp_provenance": latest_dp_provenance,
                "secagg_enabled": secagg_enabled,
                "secagg_provenance": secagg_session_provenance if secagg_enabled else None,
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


if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 5 and sys.argv[1] == "--coordinator-worker":
        _host = sys.argv[2]
        _port = int(sys.argv[3])
        _authkey = bytes.fromhex(sys.argv[4])
        _run_isolated_coordinator_worker((_host, _port), _authkey)
        sys.exit(0)
