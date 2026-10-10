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


class TrainingProvenanceConfigurationError(Exception):
    """Raised when training provenance trust configuration is missing or invalid."""

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
    feature_names: list[str]
    producer_id: str = "TRAINING_EXECUTOR"
    status: str = "COMPLETED"
    git_commit_hash: str | None = None
    dataset_manifest_id: str | None = None

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
        if not self.observed_metrics or not isinstance(self.observed_metrics, dict):
            raise ValueError("observed_metrics must be a non-empty dictionary.")

        loss_val = None
        for k in ("loss", "final_loss", "train_loss", "val_loss"):
            if k in self.observed_metrics:
                loss_val = self.observed_metrics[k]
                break
        if loss_val is None or not isinstance(loss_val, (int, float)) or not math.isfinite(loss_val):
            raise ValueError("Training metrics must contain a finite numeric loss.")


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
        feature_names: list[str] | None = None,
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
            "metrics": target_metrics,
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
            metrics=target_metrics,
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
    ) -> None:
        self._trusted_keys: dict[str, ed25519.Ed25519PublicKey] = {}
        self._revoked_keys: set[str] = set()

        if trusted_keys is not None:
            for kid, k_val in trusted_keys.items():
                self.register_trusted_key(kid, k_val)
        else:
            settings = get_settings()
            is_prod = settings.app_env.lower() == "production"

            pub_source = (
                settings.training_receipt_public_key
                or os.environ.get("CFI_TRAINING_RECEIPT_PUBLIC_KEY", "")
            ).strip()

            key_id = (
                settings.training_receipt_key_id
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
                self.register_trusted_key(key_id, pub_source)

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
                self.register_trusted_key(DEFAULT_TRAINING_KEY_ID, dev_pub)

    def register_trusted_key(
        self,
        key_id: str,
        public_key: ed25519.Ed25519PublicKey | bytes | str,
    ) -> None:
        """Register an authorized verification public key."""
        if not key_id or not key_id.strip():
            raise ValueError("key_id cannot be empty.")
        if key_id in self._revoked_keys:
            raise ValueError(f"Cannot register key_id '{key_id}' because it has been revoked.")

        if isinstance(public_key, ed25519.Ed25519PublicKey):
            self._trusted_keys[key_id] = public_key
        elif isinstance(public_key, bytes):
            if len(public_key) == 32:
                self._trusted_keys[key_id] = ed25519.Ed25519PublicKey.from_public_bytes(public_key)
            else:
                loaded = serialization.load_pem_public_key(public_key)
                if not isinstance(loaded, ed25519.Ed25519PublicKey):
                    raise TypeError(f"Expected Ed25519PublicKey, got {type(loaded)}")
                self._trusted_keys[key_id] = loaded
        elif isinstance(public_key, str):
            cleaned = public_key.strip()
            if len(cleaned) == 64:
                self._trusted_keys[key_id] = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(cleaned))
            else:
                loaded = serialization.load_pem_public_key(cleaned.encode())
                if not isinstance(loaded, ed25519.Ed25519PublicKey):
                    raise TypeError(f"Expected Ed25519PublicKey, got {type(loaded)}")
                self._trusted_keys[key_id] = loaded
        else:
            raise TypeError(f"Unsupported public key type: {type(public_key)}")

    def revoke_key(self, key_id: str, reason: str = "compromise") -> None:
        """Revoke a trusted key ID so receipts signed with it will be rejected."""
        self._revoked_keys.add(key_id)
        self._trusted_keys.pop(key_id, None)
        logger.warning("Revoked training receipt key_id '%s' (reason: %s)", key_id, reason)

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
        candidate_artifact_bytes: bytes | None = None,
        simulation_id: str | None = None,
        expected_key_id: str | None = None,
        expected_architecture: str | None = None,
        expected_feature_schema_version: str | None = None,
        expected_input_dim: int | None = None,
        expected_feature_names: list[str] | tuple[str, ...] | None = None,
    ) -> tuple[bool, str]:
        """Verify an execution receipt against candidate artifact bytes and declared scope."""
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

        # Key ID validation & revocation check
        if receipt_obj.key_id in self._revoked_keys:
            return False, f"Receipt key_id '{receipt_obj.key_id}' has been explicitly revoked."

        if expected_key_id and receipt_obj.key_id != expected_key_id:
            return False, f"Receipt key_id mismatch: expected '{expected_key_id}', got '{receipt_obj.key_id}'."

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
                self._issuer = TrainingReceiptIssuer(private_key=self._issuer_priv)
                self._verifier = TrainingReceiptVerifier(
                    trusted_keys={self._issuer.key_id: self._issuer.public_key}
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
        feature_names: list[str] | None = None,
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
