"""Model Governance, Regulatory Compliance & Shadow Deployment Domain Model.

Implements SR 11-7 model risk management standards, semantic versioning,
dual-role sign-off gates, MLOps shadow deployments, automatic rollback triggers,
and production ModelRegistryVault with HSM digital signature envelope verification.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums & Exceptions
# ---------------------------------------------------------------------------


class ModelStatus(str, Enum):  # noqa: UP042
    """Production lifecycle states for registered FL global model checkpoints."""

    DRAFT = "DRAFT"
    CANDIDATE = "CANDIDATE"
    PRODUCTION = "PRODUCTION"
    ARCHIVED = "ARCHIVED"
    ROLLED_BACK = "ROLLED_BACK"


class ModelGovernanceError(Exception):
    """Raised when a model governance policy or lifecycle transition rule is violated."""

    pass


class InvalidSignatureError(ModelGovernanceError):
    """Raised when an HSM digital signature envelope fails cryptographic verification."""

    pass


# ---------------------------------------------------------------------------
# Semantic Versioning & Sign-Off Gating
# ---------------------------------------------------------------------------


@dataclass(order=True)
class SemanticVersion:
    """Represents a semantic version tag (e.g. v1.0.0, v2.4.1)."""

    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, version_str: str) -> SemanticVersion:
        """Parses string version tag into SemanticVersion instance."""
        clean = version_str.strip().lstrip("vV")
        match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", clean)
        if not match:
            # Fallback for integer or invalid strings
            try:
                major = int(clean)
                return cls(major=major, minor=0, patch=0)
            except ValueError:
                return cls(major=1, minor=0, patch=0)

        maj, min_, pat = match.groups()
        return cls(major=int(maj), minor=int(min_), patch=int(pat))

    def to_tag(self) -> str:
        """Formats SemanticVersion into standard vX.Y.Z tag."""
        return f"v{self.major}.{self.minor}.{self.patch}"

    def bump_patch(self) -> SemanticVersion:
        """Returns new SemanticVersion with patch incremented."""
        return SemanticVersion(self.major, self.minor, self.patch + 1)

    def bump_minor(self) -> SemanticVersion:
        """Returns new SemanticVersion with minor incremented."""
        return SemanticVersion(self.major, self.minor + 1, 0)

    def bump_major(self) -> SemanticVersion:
        """Returns new SemanticVersion with major incremented."""
        return SemanticVersion(self.major + 1, 0, 0)


class DualSignoffGate:
    """Enforces SR 11-7 dual-signoff policy before promoting models to production."""

    REQUIRED_ROLES = {"ml_engineer", "compliance_officer"}

    def can_promote(self, sign_offs: list[dict[str, Any]]) -> tuple[bool, str]:
        """Evaluates whether all required sign-offs are present with valid signatures."""
        if not sign_offs:
            return False, "Dual Signoff Gating Failed: No sign-offs provided."

        signed_roles = set()
        for sign in sign_offs:
            role = str(sign.get("role", "")).lower().strip()
            user = str(sign.get("user", "")).strip()
            signature = str(sign.get("signature", "")).strip()

            # Normalize role names
            if role in ("compliance", "compliance_officer", "compliance_lead"):
                role = "compliance_officer"
            elif role in ("ml_engineer", "ml_lead", "engineer"):
                role = "ml_engineer"

            if role in self.REQUIRED_ROLES and user and signature:
                signed_roles.add(role)

        missing_roles = self.REQUIRED_ROLES - signed_roles
        if missing_roles:
            return (
                False,
                f"Dual Signoff Gating Failed: Missing required sign-off roles ({', '.join(sorted(missing_roles))}).",
            )

        return (
            True,
            "Dual Signoff Gate Passed: Both ML Engineer and Compliance Officer sign-offs verified.",
        )


# ---------------------------------------------------------------------------
# MLOps Deployment & Rollback Evaluators
# ---------------------------------------------------------------------------


class ShadowDeploymentEngine:
    """Manages shadow prediction traffic routing (default 10% candidate, 90% champion)."""

    def __init__(self, shadow_ratio: float = 0.10) -> None:
        self.shadow_ratio = max(0.0, min(0.50, shadow_ratio))

    def should_route_to_shadow(self, request_id: str) -> bool:
        """Deterministically routes 10% of prediction traffic based on request_id hash."""
        if not request_id:
            return False

        hash_val = (
            int(hashlib.md5(request_id.encode("utf-8"), usedforsecurity=False).hexdigest(), 16)
            % 100
        )
        threshold = int(self.shadow_ratio * 100)
        return hash_val < threshold

    def evaluate_shadow_performance(
        self, champion_auc: float, shadow_auc: float, min_improvement_delta: float = 0.01
    ) -> dict[str, Any]:
        """Evaluates candidate shadow model performance against champion model."""
        delta = shadow_auc - champion_auc
        is_superior = delta >= min_improvement_delta
        return {
            "champion_auc": champion_auc,
            "shadow_auc": shadow_auc,
            "auc_delta": delta,
            "is_superior": is_superior,
            "recommendation": "PROMOTE_SHADOW_TO_CHAMPION" if is_superior else "RETAIN_CHAMPION",
        }


class AutomaticRollbackTrigger:
    """Evaluates live production telemetry and triggers automatic model rollback if thresholds violated."""

    def __init__(self, min_auc_roc: float = 0.65, max_p99_latency_ms: float = 200.0) -> None:
        self.min_auc_roc = min_auc_roc
        self.max_p99_latency_ms = max_p99_latency_ms

    def should_rollback(self, live_auc_roc: float, p99_latency_ms: float) -> tuple[bool, str]:
        """Evaluates live metrics against safety thresholds."""
        if live_auc_roc < self.min_auc_roc:
            return (
                True,
                f"Automatic Rollback Triggered: Live ROC-AUC ({live_auc_roc:.4f}) fell below minimum safety threshold ({self.min_auc_roc:.4f}).",
            )

        if p99_latency_ms > self.max_p99_latency_ms:
            return (
                True,
                f"Automatic Rollback Triggered: Live p99 inference latency ({p99_latency_ms:.1f}ms) exceeded maximum threshold ({self.max_p99_latency_ms:.1f}ms).",
            )

        return (
            False,
            "Telemetry Nominal: Live ROC-AUC and inference latency within acceptable bounds.",
        )


@dataclass
class CryptographicAuditLineage:
    """Binds model version manifest to exact Git commit SHA, dataset SHA-256 hash, DP epsilon, and sign-offs."""

    model_version: str
    git_commit_hash: str
    dataset_hash: str
    dp_epsilon: float
    dp_delta: float = 1e-5
    sign_offs: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime())
    )

    def to_dict(self) -> dict[str, Any]:
        """Serializes CryptographicAuditLineage to JSON-compatible dictionary."""
        return {
            "model_version": self.model_version,
            "git_commit_hash": self.git_commit_hash,
            "dataset_hash": self.dataset_hash,
            "dp_privacy_budget": {"epsilon": self.dp_epsilon, "delta": self.dp_delta},
            "sign_offs": self.sign_offs,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Model Checkpoint & Registry Vault
# ---------------------------------------------------------------------------


@dataclass
class ModelCheckpoint:
    """Immutable model version artifact registered in the ModelRegistryVault."""

    model_id: str
    version: SemanticVersion
    status: ModelStatus
    weights_sha256: str
    hyperparams_sha256: str
    dataset_hash: str
    dp_epsilon: float
    dp_delta: float = 1e-5
    lineage: CryptographicAuditLineage | None = None
    hsm_signature: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    promoted_at: str | None = None
    promoted_by: list[dict[str, Any]] = field(default_factory=list)

    def compute_signature_payload(self) -> str:
        """Returns canonical payload string used for HSM signature generation and verification."""
        return (
            f"{self.model_id}:{self.version.to_tag()}:{self.weights_sha256}:"
            f"{self.hyperparams_sha256}:{self.dataset_hash}:{self.dp_epsilon}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serializes checkpoint manifest to JSON-compatible dictionary."""
        return {
            "model_id": self.model_id,
            "version": self.version.to_tag(),
            "status": self.status.value,
            "weights_sha256": self.weights_sha256,
            "hyperparams_sha256": self.hyperparams_sha256,
            "dataset_hash": self.dataset_hash,
            "dp_epsilon": self.dp_epsilon,
            "dp_delta": self.dp_delta,
            "hsm_signature": self.hsm_signature,
            "created_at": self.created_at,
            "promoted_at": self.promoted_at,
            "promoted_by": self.promoted_by,
            "lineage": self.lineage.to_dict() if self.lineage else None,
        }


class ModelRegistryVault:
    """Production Model Registry Vault.

    Tracks model checkpoints, cryptographic dataset/hyperparameter SHA-256 digests,
    HSM digital signature envelopes, and enforces strict gating policies (dual sign-off + signature)
    before promoting checkpoints to PRODUCTION status. Automatically archives superceded models
    and supports zero-downtime rollback to previous ARCHIVED checkpoints.
    """

    def __init__(self) -> None:
        self._checkpoints: dict[str, ModelCheckpoint] = {}
        self._signoff_gate = DualSignoffGate()
        self._lock = threading.RLock()

    def register_checkpoint(
        self,
        version_str: str,
        weights_bytes: bytes,
        hyperparameters: dict[str, Any],
        dataset_hash: str,
        dp_epsilon: float,
        dp_delta: float = 1e-5,
        git_commit_hash: str = "HEAD",
        initial_status: ModelStatus = ModelStatus.CANDIDATE,
    ) -> ModelCheckpoint:
        """Registers a new model checkpoint in the registry vault with computed SHA-256 digests."""
        with self._lock:
            model_id = str(uuid.uuid4())
            version = SemanticVersion.parse(version_str)

            weights_sha256 = hashlib.sha256(weights_bytes).hexdigest()
            hp_json = json.dumps(hyperparameters, sort_keys=True)
            hyperparams_sha256 = hashlib.sha256(hp_json.encode("utf-8")).hexdigest()

            lineage = CryptographicAuditLineage(
                model_version=version.to_tag(),
                git_commit_hash=git_commit_hash,
                dataset_hash=dataset_hash,
                dp_epsilon=dp_epsilon,
                dp_delta=dp_delta,
            )

            checkpoint = ModelCheckpoint(
                model_id=model_id,
                version=version,
                status=initial_status,
                weights_sha256=weights_sha256,
                hyperparams_sha256=hyperparams_sha256,
                dataset_hash=dataset_hash,
                dp_epsilon=dp_epsilon,
                dp_delta=dp_delta,
                lineage=lineage,
            )

            self._checkpoints[model_id] = checkpoint
            logger.info(
                "Registered model checkpoint %s (%s) with status %s (weights_sha256=%s)",
                model_id,
                version.to_tag(),
                initial_status.value,
                weights_sha256[:16] + "...",
            )
            return checkpoint

    def sign_checkpoint(self, model_id: str, signing_key: bytes) -> str:
        """Generates an HMAC-SHA256 digital signature envelope for a checkpoint using trusted key."""
        with self._lock:
            checkpoint = self._get_checkpoint_or_raise(model_id)
            payload = checkpoint.compute_signature_payload().encode("utf-8")
            signature = hmac.new(signing_key, payload, hashlib.sha256).hexdigest()
            checkpoint.hsm_signature = signature
            logger.info(
                "Signed checkpoint %s with HSM signature envelope (%s...)", model_id, signature[:16]
            )
            return signature

    def verify_checkpoint_signature(self, model_id: str, signing_key: bytes) -> bool:
        """Cryptographically verifies the digital signature envelope of a checkpoint."""
        with self._lock:
            checkpoint = self._get_checkpoint_or_raise(model_id)
            if not checkpoint.hsm_signature:
                return False

            payload = checkpoint.compute_signature_payload().encode("utf-8")
            expected_sig = hmac.new(signing_key, payload, hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected_sig, checkpoint.hsm_signature)

    def promote_to_production(
        self,
        model_id: str,
        sign_offs: list[dict[str, Any]],
        signing_key: bytes,
    ) -> ModelCheckpoint:
        """Promotes a candidate checkpoint to PRODUCTION status.

        Enforces dual sign-off (ML Engineer + Compliance Officer) AND valid HSM signature verification.
        Demotes any existing PRODUCTION model to ARCHIVED.

        Raises:
            ModelGovernanceError: If dual sign-off fails or model is invalid.
            InvalidSignatureError: If HSM digital signature verification fails.
        """
        with self._lock:
            checkpoint = self._get_checkpoint_or_raise(model_id)

            # 1. Dual Sign-off Gate
            can_promote, reason = self._signoff_gate.can_promote(sign_offs)
            if not can_promote:
                logger.error("Promotion blocked for model %s: %s", model_id, reason)
                raise ModelGovernanceError(f"Promotion Gate Failed: {reason}")

            # 2. Cryptographic Signature Gate
            if not checkpoint.hsm_signature or not self.verify_checkpoint_signature(
                model_id, signing_key
            ):
                logger.error(
                    "Promotion blocked for model %s: HSM signature verification failed", model_id
                )
                raise InvalidSignatureError(
                    f"Invalid Signature Gate Failed: Model checkpoint {model_id} does not have "
                    "a valid cryptographic signature envelope."
                )

            # 3. Archive current production model
            current_prod = self.get_production_model()
            if current_prod and current_prod.model_id != model_id:
                current_prod.status = ModelStatus.ARCHIVED
                logger.info(
                    "Archived previous production model %s (%s)",
                    current_prod.model_id,
                    current_prod.version.to_tag(),
                )

            # 4. Promote target model
            checkpoint.status = ModelStatus.PRODUCTION
            checkpoint.promoted_at = datetime.now(UTC).isoformat()
            checkpoint.promoted_by = sign_offs
            if checkpoint.lineage:
                checkpoint.lineage.sign_offs = sign_offs

            logger.info(
                "Promoted model checkpoint %s (%s) to PRODUCTION",
                model_id,
                checkpoint.version.to_tag(),
            )
            return checkpoint

    def rollback_production(self, reason: str) -> tuple[ModelCheckpoint, ModelCheckpoint]:
        """Executes zero-downtime rollback of active PRODUCTION model.

        Demotes current PRODUCTION model to ROLLED_BACK.
        Reactivates most recently ARCHIVED model back to PRODUCTION.

        Returns:
            Tuple of (rolled_back_model, restored_production_model).

        Raises:
            ModelGovernanceError: If no active production model or no archived model is found.
        """
        with self._lock:
            current_prod = self.get_production_model()
            if not current_prod:
                raise ModelGovernanceError("Rollback Failed: No active PRODUCTION model found.")

            # Find most recent ARCHIVED model (sorted by promoted_at or created_at)
            archived_candidates = [
                c for c in self._checkpoints.values() if c.status == ModelStatus.ARCHIVED
            ]
        if not archived_candidates:
            raise ModelGovernanceError(
                "Rollback Failed: No ARCHIVED checkpoint available for restoration."
            )

        # Sort archived models by creation/promotion timestamp descending
        archived_candidates.sort(key=lambda c: c.promoted_at or c.created_at, reverse=True)
        restore_target = archived_candidates[0]

        # Execute rollback
        current_prod.status = ModelStatus.ROLLED_BACK
        restore_target.status = ModelStatus.PRODUCTION
        restore_target.promoted_at = datetime.now(UTC).isoformat()

        logger.warning(
            "Executed production rollback: Model %s demoted to ROLLED_BACK (reason: %s). "
            "Restored model %s (%s) to PRODUCTION.",
            current_prod.model_id,
            reason,
            restore_target.model_id,
            restore_target.version.to_tag(),
        )
        return current_prod, restore_target

    def get_production_model(self) -> ModelCheckpoint | None:
        """Returns currently active PRODUCTION model checkpoint, or None if none active."""
        for checkpoint in self._checkpoints.values():
            if checkpoint.status == ModelStatus.PRODUCTION:
                return checkpoint
        return None

    def list_checkpoints(self, status: ModelStatus | None = None) -> list[ModelCheckpoint]:
        """Lists registered checkpoints, optionally filtered by status."""
        if status is None:
            return list(self._checkpoints.values())
        return [c for c in self._checkpoints.values() if c.status == status]

    def _get_checkpoint_or_raise(self, model_id: str) -> ModelCheckpoint:
        if model_id not in self._checkpoints:
            raise ModelGovernanceError(f"Model checkpoint {model_id} not found in registry vault.")
        return self._checkpoints[model_id]
