"""Training Provenance & Cryptographic Receipt Authority.

Establishes a verifiable, asymmetric chain of training execution:
- Asymmetric Ed25519 signature architecture separating Receipt Issuer from Verifier.
- Cryptographic execution receipts binding artifact SHA-256, run ID, job ID,
  executed rounds/epochs, finite loss/metrics, and producer identity.
- Explicit trusted key registry with key rotation and revocation support.
- Verification logic ensuring that candidate model artifact bytes match the receipt digest.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import logging
import math
import os
import uuid
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


def derive_domain_key(secret: str, domain: str) -> bytes:
    """Derive cryptographically separated domain key using HMAC-SHA256."""
    return hmac.new(secret.encode("utf-8"), domain.encode("utf-8"), hashlib.sha256).digest()


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
        return cls(
            receipt_id=str(data["receipt_id"]),
            training_run_id=str(data["training_run_id"]),
            job_id=str(data["job_id"]),
            model_artifact_sha256=str(data["model_artifact_sha256"]),
            architecture=str(data.get("architecture", "FraudDetectionModel-GroupNorm")),
            feature_schema_version=str(data.get("feature_schema_version", "v1.0")),
            input_dim=int(data.get("input_dim", 10)),
            feature_names=list(data.get("feature_names", [])),
            simulation_id=str(data.get("simulation_id", "")),
            status=str(data.get("status", "COMPLETED")),
            executed_rounds=int(data.get("executed_rounds", 1)),
            executed_epochs=int(data.get("executed_epochs", 1)),
            sample_count=int(data.get("sample_count", 1)),
            metrics=dict(data.get("metrics", {})),
            producer_id=str(data.get("producer_id", "TRAINING_EXECUTOR")),
            schema_version=str(data.get("schema_version", "1.0")),
            created_at=str(data.get("created_at", "")),
            signature=str(data.get("signature", "")),
            key_id=str(data.get("key_id", DEFAULT_TRAINING_KEY_ID)),
            signature_scheme=str(data.get("signature_scheme", "Ed25519")),
            git_commit_hash=data.get("git_commit_hash"),
            dataset_manifest_id=data.get("dataset_manifest_id"),
        )


class TrainingReceiptIssuer:
    """Authorized issuer of cryptographic execution receipts using asymmetric Ed25519 signing.

    The private key is held EXCLUSIVELY by authorized training worker / execution components.
    General serving components and clients do NOT possess this private key.
    """

    def __init__(
        self,
        private_key: ed25519.Ed25519PrivateKey | bytes | str | None = None,
        key_id: str = DEFAULT_TRAINING_KEY_ID,
    ) -> None:
        self.key_id = key_id
        settings = get_settings()
        is_prod = settings.app_env.lower() == "production"

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
                # Deterministic development key derived from dev secret
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
        training_run_id: str,
        job_id: str | None = None,
        model_artifact_sha256: str | None = None,
        simulation_id: str = "sim_default",
        executed_rounds: int = 1,
        executed_epochs: int = 1,
        sample_count: int = 100,
        metrics: dict[str, float] | None = None,
        architecture: str = "FraudDetectionModel-GroupNorm",
        feature_schema_version: str = "v1.0",
        input_dim: int = 10,
        feature_names: list[str] | None = None,
        producer_id: str = "TRAINING_EXECUTOR",
        git_commit_hash: str | None = None,
        dataset_manifest_id: str | None = None,
        artifact_sha256: str | None = None,
        model_bytes: bytes | None = None,
        model_architecture: str | None = None,
        dataset_hash: str | None = None,
        dp_noise_profile: Any = None,
    ) -> TrainingExecutionReceipt:
        """Issue an authenticated training execution receipt signed with Ed25519 private key."""
        target_job_id = job_id or f"job_{training_run_id}"
        target_arch = model_architecture or architecture
        target_manifest = dataset_manifest_id or dataset_hash
        target_sha = model_artifact_sha256 or artifact_sha256
        if not target_sha and model_bytes is not None:
            target_sha = hashlib.sha256(model_bytes).hexdigest()
        if not target_sha:
            raise ValueError("model_artifact_sha256 or artifact_sha256 is required.")
        target_metrics = metrics if metrics is not None else {"loss": 0.01}
        if not math.isfinite(target_metrics.get("loss", 0.0)):
            raise ValueError("Training metrics must contain a finite numeric loss.")
        if executed_rounds < 1 and executed_epochs < 1:
            raise ValueError("Executed rounds or epochs must be at least 1.")
        if sample_count <= 0:
            raise ValueError("Training sample count must be positive.")

        receipt_id = f"rcpt_{uuid.uuid4().hex}"
        created_at = datetime.now(UTC).isoformat()

        from app.application.services.data_generator import FEATURE_NAMES

        fnames = feature_names or FEATURE_NAMES

        receipt_data = {
            "receipt_id": receipt_id,
            "training_run_id": training_run_id,
            "job_id": target_job_id,
            "model_artifact_sha256": target_sha,
            "architecture": target_arch,
            "feature_schema_version": feature_schema_version,
            "input_dim": input_dim,
            "feature_names": fnames,
            "simulation_id": simulation_id,
            "status": "COMPLETED",
            "executed_rounds": executed_rounds,
            "executed_epochs": executed_epochs,
            "sample_count": sample_count,
            "metrics": target_metrics,
            "producer_id": producer_id,
            "schema_version": "1.0",
            "created_at": created_at,
            "git_commit_hash": git_commit_hash,
            "dataset_manifest_id": target_manifest,
            "key_id": self.key_id,
        }
        canonical_bytes = json.dumps(receipt_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        sig_bytes = self._private_key.sign(canonical_bytes)
        signature = sig_bytes.hex()

        return TrainingExecutionReceipt(
            receipt_id=receipt_id,
            training_run_id=training_run_id,
            job_id=target_job_id,
            model_artifact_sha256=target_sha,
            architecture=target_arch,
            feature_schema_version=feature_schema_version,
            input_dim=input_dim,
            feature_names=fnames,
            simulation_id=simulation_id,
            status="COMPLETED",
            executed_rounds=executed_rounds,
            executed_epochs=executed_epochs,
            sample_count=sample_count,
            metrics=target_metrics,
            producer_id=producer_id,
            schema_version="1.0",
            created_at=created_at,
            git_commit_hash=git_commit_hash,
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
    ) -> None:
        self._trusted_keys: dict[str, ed25519.Ed25519PublicKey] = {}
        self._revoked_keys: set[str] = set()

        if trusted_keys:
            for kid, k_val in trusted_keys.items():
                self.register_trusted_key(kid, k_val)
        else:
            # Check environment for production public key
            pub_hex = os.environ.get("CFI_TRAINING_RECEIPT_PUBLIC_KEY")
            if pub_hex:
                cleaned = pub_hex.strip()
                self.register_trusted_key(
                    DEFAULT_TRAINING_KEY_ID,
                    ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(cleaned)),
                )
            else:
                settings = get_settings()
                if settings.app_env.lower() != "production":
                    # Register development public key
                    dev_seed = hashlib.sha256(b"cfi_dev_ed25519_training_issuer_seed_v1").digest()
                    dev_priv = ed25519.Ed25519PrivateKey.from_private_bytes(dev_seed)
                    self.register_trusted_key(DEFAULT_TRAINING_KEY_ID, dev_priv.public_key())

    def register_trusted_key(
        self,
        key_id: str,
        public_key: ed25519.Ed25519PublicKey | bytes | str,
    ) -> None:
        """Register an authorized verification public key."""
        if isinstance(public_key, ed25519.Ed25519PublicKey):
            self._trusted_keys[key_id] = public_key
        elif isinstance(public_key, bytes):
            if len(public_key) == 32:
                self._trusted_keys[key_id] = ed25519.Ed25519PublicKey.from_public_bytes(public_key)
            else:
                self._trusted_keys[key_id] = serialization.load_pem_public_key(public_key)  # type: ignore
        elif isinstance(public_key, str):
            cleaned = public_key.strip()
            if len(cleaned) == 64:
                self._trusted_keys[key_id] = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(cleaned))
            else:
                self._trusted_keys[key_id] = serialization.load_pem_public_key(cleaned.encode())  # type: ignore
        else:
            raise TypeError(f"Unsupported public key type: {type(public_key)}")

    def revoke_key(self, key_id: str) -> None:
        """Revoke a trusted key ID so receipts signed with it will be rejected."""
        self._revoked_keys.add(key_id)
        self._trusted_keys.pop(key_id, None)

    def is_key_trusted(self, key_id: str) -> bool:
        return key_id in self._trusted_keys and key_id not in self._revoked_keys

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
        simulation_id: str | None = None,
        expected_key_id: str | None = None,
    ) -> tuple[bool, str]:
        """Verify an execution receipt against candidate artifact bytes and declared scope."""
        if isinstance(receipt, dict):
            try:
                receipt_obj = TrainingExecutionReceipt.from_dict(receipt)
            except Exception as exc:
                return False, f"Receipt format invalid: {exc}"
        else:
            receipt_obj = receipt

        if not receipt_obj.signature:
            return False, "Receipt signature is missing or empty."

        # Key ID validation & revocation check
        if receipt_obj.key_id in self._revoked_keys:
            return False, f"Receipt key_id '{receipt_obj.key_id}' has been explicitly revoked."

        if expected_key_id and receipt_obj.key_id != expected_key_id:
            return False, f"Receipt key_id mismatch: expected '{expected_key_id}', got '{receipt_obj.key_id}'."

        if receipt_obj.key_id not in self._trusted_keys:
            return False, (
                f"Receipt key_id '{receipt_obj.key_id}' is not present in trusted public key registry."
            )

        public_key = self._trusted_keys[receipt_obj.key_id]

        # Cryptographic signature validation
        try:
            sig_bytes = bytes.fromhex(receipt_obj.signature)
            public_key.verify(sig_bytes, receipt_obj.canonical_bytes())
        except (ValueError, InvalidSignature) as exc:
            return False, f"Cryptographic signature mismatch: receipt was tampered with or signed with untrusted key ({exc})."

        # Artifact SHA-256 binding (if candidate digest provided)
        if candidate_artifact_sha256 is not None and not hmac.compare_digest(
            receipt_obj.model_artifact_sha256, candidate_artifact_sha256
        ):
            return False, (
                f"Artifact digest mismatch: receipt binds '{receipt_obj.model_artifact_sha256}', "
                f"candidate artifact is '{candidate_artifact_sha256}'."
            )

        # Simulation scope binding
        if simulation_id and receipt_obj.simulation_id and receipt_obj.simulation_id != simulation_id:
            return False, (
                f"Simulation scope mismatch: receipt is for '{receipt_obj.simulation_id}', "
                f"candidate requested for '{simulation_id}'."
            )

        # Execution invariants
        if receipt_obj.status != "COMPLETED":
            return False, f"Training run status is '{receipt_obj.status}', expected 'COMPLETED'."
        if receipt_obj.executed_rounds < 1 and receipt_obj.executed_epochs < 1:
            return False, "Executed rounds and epochs are both < 1."
        if receipt_obj.sample_count <= 0:
            return False, "Executed sample count is <= 0."
        if not math.isfinite(receipt_obj.metrics.get("loss", 0.0)):
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
    global _DEFAULT_VERIFIER
    if _DEFAULT_VERIFIER is None:
        issuer = get_default_training_issuer()
        _DEFAULT_VERIFIER = TrainingReceiptVerifier(
            trusted_keys={issuer.key_id: issuer.public_key}
        )
    return _DEFAULT_VERIFIER


class TrainingReceiptAuthority:
    """Facade for training execution receipt issuance and verification.

    Provides backward compatibility while maintaining underlying asymmetric separation.
    """

    def __init__(self, signing_secret: str | None = None) -> None:
        settings = get_settings()
        default_secret = settings.payload_signing_secret
        if signing_secret is None or signing_secret == default_secret:
            self._secret = default_secret
            self._issuer = get_default_training_issuer()
            self._verifier = get_default_training_verifier()
        else:
            self._secret = signing_secret
            dev_seed = hashlib.sha256(self._secret.encode("utf-8")).digest()
            self._issuer_priv = ed25519.Ed25519PrivateKey.from_private_bytes(dev_seed)
            self._issuer = TrainingReceiptIssuer(private_key=self._issuer_priv)
            self._verifier = TrainingReceiptVerifier(
                trusted_keys={self._issuer.key_id: self._issuer.public_key}
            )

    def _get_signing_key(self) -> bytes:
        return derive_domain_key(self._secret, DOMAIN_TRAINING_RECEIPT)

    def issue_receipt(
        self,
        training_run_id: str,
        job_id: str | None = None,
        model_artifact_sha256: str | None = None,
        simulation_id: str = "sim_default",
        executed_rounds: int = 1,
        executed_epochs: int = 1,
        sample_count: int = 100,
        metrics: dict[str, float] | None = None,
        architecture: str = "FraudDetectionModel-GroupNorm",
        feature_schema_version: str = "v1.0",
        input_dim: int = 10,
        feature_names: list[str] | None = None,
        producer_id: str = "TRAINING_EXECUTOR",
        git_commit_hash: str | None = None,
        dataset_manifest_id: str | None = None,
        **kwargs: Any,
    ) -> TrainingExecutionReceipt:
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

        expected_sig = hmac.new(self._get_signing_key(), receipt_obj.canonical_bytes(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(receipt_obj.signature, expected_sig):
            # Check candidate artifact and simulation scope
            if candidate_artifact_sha256 is not None and not hmac.compare_digest(
                receipt_obj.model_artifact_sha256, candidate_artifact_sha256
            ):
                return False, f"Artifact digest mismatch: receipt binds '{receipt_obj.model_artifact_sha256}', candidate is '{candidate_artifact_sha256}'."
            if simulation_id and receipt_obj.simulation_id and receipt_obj.simulation_id != simulation_id:
                return False, f"Simulation scope mismatch: receipt is for '{receipt_obj.simulation_id}', requested '{simulation_id}'."
            if receipt_obj.status != "COMPLETED":
                return False, f"Training run status is '{receipt_obj.status}', expected 'COMPLETED'."
            if receipt_obj.executed_rounds < 1 and receipt_obj.executed_epochs < 1:
                return False, "Executed rounds and epochs are both < 1."
            if receipt_obj.sample_count <= 0:
                return False, "Executed sample count is <= 0."
            if not math.isfinite(receipt_obj.metrics.get("loss", 0.0)):
                return False, "Recorded training loss is not a finite number."
            return True, "Legacy HMAC receipt verified successfully (dev only)."

        return False, reason
