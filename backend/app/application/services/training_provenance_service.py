"""Training Provenance & Cryptographic Receipt Authority.

Establishes a verifiable chain of training execution:
- Cryptographic execution receipts binding artifact SHA-256, run ID, job ID,
  executed rounds/epochs, finite loss/metrics, and producer identity.
- Cryptographic domain separation for signing keys.
- Verification logic ensuring that candidate model artifact bytes match the receipt digest.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import logging
import math
import uuid
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

DOMAIN_TRAINING_RECEIPT = "cfi:training_receipt_signing:v1"
DOMAIN_MODEL_CACHE = "cfi:model_cache_auth:v1"
DOMAIN_APPROVAL_SIGNOFF = "cfi:model_approval_signoff:v1"


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
            key_id=str(data.get("key_id", "cfi_training_auth_key_v1")),
            git_commit_hash=data.get("git_commit_hash"),
            dataset_manifest_id=data.get("dataset_manifest_id"),
        )


class TrainingReceiptAuthority:
    """Issues and verifies cryptographic execution receipts for completed model training."""

    def __init__(self, signing_secret: str | None = None) -> None:
        self._secret = signing_secret or get_settings().payload_signing_secret

    def _get_signing_key(self) -> bytes:
        return derive_domain_key(self._secret, DOMAIN_TRAINING_RECEIPT)

    def issue_receipt(
        self,
        training_run_id: str,
        job_id: str,
        model_artifact_sha256: str,
        simulation_id: str,
        executed_rounds: int,
        executed_epochs: int,
        sample_count: int,
        metrics: dict[str, float],
        architecture: str = "FraudDetectionModel-GroupNorm",
        feature_schema_version: str = "v1.0",
        input_dim: int = 10,
        feature_names: list[str] | None = None,
        producer_id: str = "TRAINING_EXECUTOR",
        git_commit_hash: str | None = None,
        dataset_manifest_id: str | None = None,
    ) -> TrainingExecutionReceipt:
        """Issue an authenticated training execution receipt signed by the training authority."""
        if not math.isfinite(metrics.get("loss", 0.0)):
            raise ValueError("Training metrics must contain a finite numeric loss.")
        if executed_rounds < 1 and executed_epochs < 1:
            raise ValueError("Executed rounds or epochs must be at least 1.")
        if sample_count <= 0:
            raise ValueError("Training sample count must be positive.")

        receipt_id = f"rcpt_{uuid.uuid4().hex}"
        created_at = datetime.now(UTC).isoformat()
        key_id = "cfi_training_auth_key_v1"

        from app.application.services.data_generator import FEATURE_NAMES

        fnames = feature_names or FEATURE_NAMES

        receipt_data = {
            "receipt_id": receipt_id,
            "training_run_id": training_run_id,
            "job_id": job_id,
            "model_artifact_sha256": model_artifact_sha256,
            "architecture": architecture,
            "feature_schema_version": feature_schema_version,
            "input_dim": input_dim,
            "feature_names": fnames,
            "simulation_id": simulation_id,
            "status": "COMPLETED",
            "executed_rounds": executed_rounds,
            "executed_epochs": executed_epochs,
            "sample_count": sample_count,
            "metrics": metrics,
            "producer_id": producer_id,
            "schema_version": "1.0",
            "created_at": created_at,
            "git_commit_hash": git_commit_hash,
            "dataset_manifest_id": dataset_manifest_id,
            "key_id": key_id,
        }
        canonical_bytes = json.dumps(receipt_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(self._get_signing_key(), canonical_bytes, hashlib.sha256).hexdigest()

        return TrainingExecutionReceipt(
            receipt_id=receipt_id,
            training_run_id=training_run_id,
            job_id=job_id,
            model_artifact_sha256=model_artifact_sha256,
            architecture=architecture,
            feature_schema_version=feature_schema_version,
            input_dim=input_dim,
            feature_names=fnames,
            simulation_id=simulation_id,
            status="COMPLETED",
            executed_rounds=executed_rounds,
            executed_epochs=executed_epochs,
            sample_count=sample_count,
            metrics=metrics,
            producer_id=producer_id,
            schema_version="1.0",
            created_at=created_at,
            git_commit_hash=git_commit_hash,
            dataset_manifest_id=dataset_manifest_id,
            key_id=key_id,
            signature=signature,
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

        # Key ID validation
        if expected_key_id and receipt_obj.key_id != expected_key_id:
            return False, f"Receipt key_id mismatch: expected '{expected_key_id}', got '{receipt_obj.key_id}'."

        # Cryptographic signature validation
        expected_sig = hmac.new(self._get_signing_key(), receipt_obj.canonical_bytes(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(receipt_obj.signature, expected_sig):
            return False, "Cryptographic signature mismatch: receipt was tampered with or signed with untrusted key."

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
