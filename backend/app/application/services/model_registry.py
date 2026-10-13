"""Model Registry service.

Handles versioned model persistence, registry manifest updates, promotions, and rollbacks.
Global models are saved per simulation run with metrics metadata.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import math
import os
import shutil
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import torch
from filelock import FileLock

from app.application.services.training_provenance_service import (
    DOMAIN_APPROVAL_SIGNOFF,
    TrainingExecutionReceipt,
    TrainingReceiptVerifier,
    derive_domain_key,
    get_default_training_verifier,
)
from app.domain.model_serving_errors import (
    ModelCompatibilityError,
    ModelIntegrityError,
    ModelNotAvailableError,
)

logger = logging.getLogger(__name__)

_REGISTRY_FILE_LOCKS: dict[str, FileLock] = {}
_REGISTRY_LOCKS_GUARD = threading.Lock()


def _get_registry_file_lock(sim_dir: str) -> FileLock:
    """Return shared FileLock instance for simulation dir to ensure process-local reentrancy."""
    lock_path = os.path.abspath(os.path.join(sim_dir, "registry.lock"))
    with _REGISTRY_LOCKS_GUARD:
        if lock_path not in _REGISTRY_FILE_LOCKS:
            _REGISTRY_FILE_LOCKS[lock_path] = FileLock(lock_path, timeout=15)
        return _REGISTRY_FILE_LOCKS[lock_path]


def compute_file_sha256(filepath: str) -> str:
    """Compute SHA-256 hex digest of a file on disk."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def generate_signoff_signature(
    simulation_id: str,
    version: int,
    artifact_sha256: str,
    role: str,
    user: str,
    signing_secret: str | None = None,
) -> str:
    """Generate canonical HMAC-SHA256 signature binding simulation, version, artifact digest, role, and user."""
    from app.config import get_settings

    secret = signing_secret or get_settings().payload_signing_secret
    norm_role = "compliance" if role in ("compliance", "compliance_officer") else "ml_engineer"
    canonical_payload = f"{simulation_id}:{version}:{artifact_sha256}:{norm_role}:{user}"
    domain_key = derive_domain_key(secret, DOMAIN_APPROVAL_SIGNOFF)
    return hmac.new(domain_key, canonical_payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _validate_training_evidence(
    training_run_id: str | None,
    training_evidence: dict[str, Any] | None,
) -> bool:
    """Validate whether genuine recorded training execution evidence is present.

    Requires:
    1. A valid, non-empty training run identifier.
    2. A structured training evidence dictionary containing verifiable numeric execution artifacts:
       - Finite numeric loss (or non-empty list of finite float losses),
       - Positive integer epochs or rounds (>= 1),
       - Positive integer batch_size or dataset sample count (> 0).
    Arbitrary strings, empty dicts, or unverified caller assertions do NOT qualify.
    """
    if not training_run_id or not isinstance(training_run_id, str) or not training_run_id.strip():
        return False
    if training_run_id.strip().lower() in ("unknown", "unspecified", "none"):
        return False
    if not isinstance(training_evidence, dict) or not training_evidence:
        return False

    has_loss = False
    if "loss" in training_evidence:
        val = training_evidence["loss"]
        if isinstance(val, (int, float)) and math.isfinite(val):
            has_loss = True
    elif "losses" in training_evidence:
        losses = training_evidence["losses"]
        if (
            isinstance(losses, (list, tuple))
            and len(losses) > 0
            and all(isinstance(x, (int, float)) and math.isfinite(x) for x in losses)
        ):
            has_loss = True

    has_progress = False
    for k in ("epochs", "epoch", "rounds", "round", "steps", "num_rounds"):
        if k in training_evidence:
            val = training_evidence[k]
            if isinstance(val, int) and val >= 1:
                has_progress = True
                break

    has_samples = False
    for k in ("samples", "sample_count", "batch_size", "dataset_samples", "num_samples", "participants"):
        if k in training_evidence:
            val = training_evidence[k]
            if isinstance(val, int) and val > 0:
                has_samples = True
                break
            if isinstance(val, (list, tuple)) and len(val) > 0:
                has_samples = True
                break

    return has_loss and (has_progress or has_samples)


def validate_production_admission(
    entry_or_provenance: dict[str, Any] | str,
    context: str = "production_admission",
) -> None:
    """Centralized production admission gate.

    In production (app_env == 'production'), strictly enforces:
    - UNVERIFIED, STRUCTURAL_FIXTURE, and LEGACY_UNVERIFIED cannot become operational champions.
    - Only models with verified/recorded training provenance and verified cryptographic receipts are eligible.
    - Models with is_production_eligible == False are rejected.
    - Revoked models are strictly rejected.
    - Artifacts missing integrity digest (sha256) are rejected.
    - Every production champion activation path requires two verified, distinct institutional sign-offs
      (Four-Eyes Principle: ml_engineer and compliance) signed by non-development accounts.
    """
    from app.config import get_settings

    settings = get_settings()
    if settings.app_env.lower() != "production":
        return

    # Enforce dual-approval contract on champion activation and serving entry points
    CHAMPION_CONTEXTS = (
        "save_version",
        "promote_version",
        "rollback",
        "resolve_and_verify_champion",
        "get_active_champion_identity",
        "champion_activation",
        "ModelService.get_champion",
    )

    prov: str
    is_eligible: bool | None = None
    sha256_val: str | None = None

    if isinstance(entry_or_provenance, dict):
        if entry_or_provenance.get("is_revoked") is True:
            raise ModelIntegrityError(
                f"Production admission rejected: model version has been revoked ({context})."
            )
        prov = str(entry_or_provenance.get("provenance_type", "UNVERIFIED")).strip().upper()
        is_eligible = entry_or_provenance.get("is_production_eligible")
        sha256_val = entry_or_provenance.get("sha256")
    else:
        prov = entry_or_provenance.strip().upper()

    status = entry_or_provenance.get("status", "") if isinstance(entry_or_provenance, dict) else ""
    target_role = (
        "champion"
        if (context in CHAMPION_CONTEXTS or "champion" in context or status in ("champion", "active"))
        else ("challenger" if ("challenger" in context or status == "challenger") else "operational")
    )

    if prov == "STRUCTURAL_FIXTURE":
        raise ModelIntegrityError(
            f"Production admission rejected: Structural fixture cannot be promoted to {target_role} in production environment ({context}). "
            f"Structural test fixture cannot be promoted."
        )

    if prov in ("UNVERIFIED", "LEGACY_UNVERIFIED", "CALLER_REPORTED_UNVERIFIED"):
        raise ModelIntegrityError(
            f"Production admission rejected: model provenance '{prov}' is ineligible for "
            f"operational {target_role} serving in production environment ({context})."
        )

    if prov not in ("TRAINING_RECORDED", "TRAINING_EVIDENCE_VERIFIED"):
        raise ModelIntegrityError(
            f"Production admission rejected: model provenance '{prov}' is not production-eligible ({context})."
        )

    if is_eligible is False:
        raise ModelIntegrityError(
            f"Production admission rejected: model is explicitly marked as not production-eligible ({context})."
        )

    if isinstance(entry_or_provenance, dict):
        if sha256_val is not None and not sha256_val.strip():
            raise ModelIntegrityError(
                f"Production admission rejected: model artifact digest is missing or empty ({context})."
            )

        # Enforce verified cryptographic training receipt
        if not entry_or_provenance.get("training_receipt"):
            raise ModelIntegrityError(
                f"Production admission rejected: model lacks verified cryptographic training execution receipt ({context})."
            )

        if context in CHAMPION_CONTEXTS:
            sign_offs = entry_or_provenance.get("sign_offs", [])
            if not isinstance(sign_offs, list) or len(sign_offs) < 2:
                raise ModelIntegrityError(
                    f"Production promotion rejected: Missing required sign-offs: model requires two authorized institutional sign-offs ({context})."
                )

            ml_signs = [
                s for s in sign_offs
                if s.get("role") in ("ml_engineer", "ml_lead") and s.get("is_revoked") is not True
            ]
            comp_signs = [
                s for s in sign_offs
                if s.get("role") in ("compliance", "compliance_officer") and s.get("is_revoked") is not True
            ]

            if not ml_signs:
                raise ModelIntegrityError(
                    f"Production promotion rejected: Missing required sign-offs: missing verified ML engineer sign-off ({context})."
                )
            if not comp_signs:
                raise ModelIntegrityError(
                    f"Production promotion rejected: Missing required sign-offs: missing verified compliance sign-off ({context})."
                )

            ml_s = ml_signs[0]
            comp_s = comp_signs[0]

            # Four-Eyes Principle: distinct individuals
            if ml_s.get("user") == comp_s.get("user"):
                raise ModelIntegrityError(
                    f"Production promotion rejected: Four-Eyes Principle violation, user '{ml_s.get('user')}' cannot approve both roles ({context})."
                )

            secret = settings.payload_signing_secret
            if not secret or not isinstance(secret, str) or not secret.strip() or len(secret.strip()) < 16:
                raise ModelIntegrityError(
                    f"Production promotion rejected: authoritative signing configuration is missing or invalid ({context})."
                )

            sim_id = (
                entry_or_provenance.get("simulation_id")
                or (entry_or_provenance.get("training_receipt") or {}).get("simulation_id")
            )
            if not sim_id and isinstance(sign_offs, list):
                for s in sign_offs:
                    if s.get("payload_bound"):
                        parts = s.get("payload_bound", "").split(":")
                        if len(parts) >= 5 and parts[0]:
                            sim_id = parts[0]
                            break
            if not sim_id:
                raise ModelIntegrityError(
                    f"Production promotion rejected: simulation identifier missing from model metadata ({context})."
                )

            version_val = entry_or_provenance.get("version")
            if version_val is None:
                raise ModelIntegrityError(
                    f"Production promotion rejected: version number missing from model metadata ({context})."
                )

            if not sha256_val or not sha256_val.strip():
                raise ModelIntegrityError(
                    f"Production promotion rejected: model artifact digest is missing or empty ({context})."
                )

            domain_key = derive_domain_key(secret, DOMAIN_APPROVAL_SIGNOFF)
            from app.infrastructure.security.auth_service import AuthenticationService
            auth_svc = AuthenticationService.get_instance()

            SEEDED_ACCOUNTS = {"admin", "analyst_a1", "analyst_b1", "analyst_c1", "auditor"}
            for s, expected_role in ((ml_s, "ml_engineer"), (comp_s, "compliance")):
                u = s.get("user", "")
                if not u or not isinstance(u, str) or not u.strip():
                    raise ModelIntegrityError(
                        f"Production promotion rejected: sign-off missing approver user ({context})."
                    )
                if u in SEEDED_ACCOUNTS:
                    raise ModelIntegrityError(
                        f"Production promotion rejected: development seeded account '{u}' cannot authorize promotion ({context})."
                    )
                if not s.get("is_role_verified") or not s.get("is_identity_verified"):
                    raise ModelIntegrityError(
                        f"Production promotion rejected: sign-off missing verified role authority ({context})."
                    )

                # Authoritative RBAC check against AuthenticationService
                user_rec = auth_svc.get_user(u)
                if user_rec is not None:
                    user_roles = [r.lower().strip() for r in user_rec.roles]
                    if expected_role == "ml_engineer" and not any(r in ("ml_engineer", "ml_lead") for r in user_roles):
                        raise ModelIntegrityError(
                            f"Production promotion rejected: user '{u}' lacks authoritative ml_engineer role ({context})."
                        )
                    if expected_role == "compliance" and not any(r in ("compliance", "compliance_officer") for r in user_roles):
                        raise ModelIntegrityError(
                            f"Production promotion rejected: user '{u}' lacks authoritative compliance role ({context})."
                        )

                # Authoritative cryptographic HMAC validation
                canonical_payload = f"{sim_id}:{version_val}:{sha256_val}:{expected_role}:{u}"
                if s.get("payload_bound") and s.get("payload_bound") != canonical_payload:
                    raise ModelIntegrityError(
                        f"Production promotion rejected: sign-off payload binding mismatch for user '{u}' ({context})."
                    )

                exp_sig_domain = hmac.new(
                    domain_key,
                    canonical_payload.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()
                exp_sig_raw = hmac.new(
                    secret.encode("utf-8"),
                    canonical_payload.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()

                sig = s.get("signature") or ""
                if not sig or not (
                    hmac.compare_digest(sig, exp_sig_domain)
                    or hmac.compare_digest(sig, exp_sig_raw)
                ):
                    raise ModelIntegrityError(
                        f"Production promotion rejected: sign-off for user '{u}' ({expected_role}) "
                        f"lacks valid cryptographic signature ({context})."
                    )


class ModelRegistry:
    """Manages versioned model artifacts and registry manifests.

    Models are stored in the filesystem under `storage/registry/{simulation_id}/`.
    A manifest file `registry.json` tracks versions, metrics, and active states.
    """

    def __init__(
        self,
        storage_dir: str | None = None,
        verifier: TrainingReceiptVerifier | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._verifier = verifier
        if storage_dir is None:
            # 1. Prefer explicit env override (e.g. set in docker-compose / HF Space secrets)
            env_dir = os.environ.get("CFI_STORAGE_DIR")
            if env_dir:
                self.storage_dir = os.path.abspath(env_dir)
            else:
                # 2. Default: <repo>/backend/app/storage
                default_dir = os.path.abspath(
                    os.path.join(
                        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                        "storage",
                    )
                )
                # 3. Fall back to OS temp dir if the default path is not writable (read-only containers)
                try:
                    os.makedirs(default_dir, exist_ok=True)
                    # quick writability probe
                    test_file = os.path.join(default_dir, ".write_test")
                    with open(test_file, "w") as _f:
                        pass
                    os.remove(test_file)
                    self.storage_dir = default_dir
                except OSError:
                    self.storage_dir = os.path.join(tempfile.gettempdir(), "cfi_storage")
                    logger.info(
                        "Default storage path not writable; using fallback: %s",
                        self.storage_dir,
                    )
        else:
            self.storage_dir = os.path.abspath(storage_dir)

        self.registry_root = os.path.join(self.storage_dir, "registry")
        os.makedirs(self.registry_root, exist_ok=True)

    def _get_sim_dir(self, simulation_id: str) -> str:
        sim_dir = os.path.join(self.registry_root, simulation_id)
        os.makedirs(sim_dir, exist_ok=True)
        return sim_dir

    def _load_manifest(self, simulation_id: str) -> list[dict[str, Any]]:
        sim_dir = self._get_sim_dir(simulation_id)
        file_lock = _get_registry_file_lock(sim_dir)
        with self._lock, file_lock:
            manifest_path = os.path.join(sim_dir, "registry.json")
            if not os.path.exists(manifest_path):
                return []
            try:
                with open(manifest_path, encoding="utf-8") as f:
                    content = f.read()
                    if not content.strip():
                        raise ModelIntegrityError(
                            f"Registry manifest for simulation '{simulation_id}' is empty (0 bytes)."
                        )
                    data = json.loads(content)
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict) and "simulation_id" not in item:
                                item["simulation_id"] = simulation_id
                        return data
                    raise ModelIntegrityError(
                        f"Registry manifest for simulation '{simulation_id}' is corrupted: expected JSON list, got {type(data).__name__}."
                    )
            except ModelIntegrityError:
                raise
            except Exception as e:
                logger.error("Failed to load registry manifest for %s: %s", simulation_id, e)
                raise ModelIntegrityError(
                    f"Registry manifest for simulation '{simulation_id}' is corrupted or unreadable: {e}"
                ) from e

    def _save_manifest(self, simulation_id: str, manifest: list[dict[str, Any]]) -> None:
        sim_dir = self._get_sim_dir(simulation_id)
        file_lock = _get_registry_file_lock(sim_dir)
        with self._lock, file_lock:
            manifest_path = os.path.join(sim_dir, "registry.json")
            tmp_manifest_path = os.path.join(
                sim_dir, f"registry_{os.getpid()}_{threading.get_ident()}_{int(time.time() * 1000)}.tmp"
            )
            try:
                with open(tmp_manifest_path, "w", encoding="utf-8") as f:
                    json.dump(manifest, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_manifest_path, manifest_path)
            except Exception as e:
                logger.error("Authoritative manifest persistence failed for %s: %s", simulation_id, e)
                if os.path.exists(tmp_manifest_path):
                    with contextlib.suppress(OSError):
                        os.remove(tmp_manifest_path)
                raise ModelIntegrityError(
                    f"Authoritative manifest persistence failed for simulation '{simulation_id}': {e}"
                ) from e

    def save_version(
        self,
        simulation_id: str,
        state_dict: dict[str, Any],
        metrics: dict[str, Any],
        is_promoted: bool = False,
        git_commit_hash: str | None = None,
        dataset_hash: str | None = None,
        dp_noise_profile: dict[str, Any] | None = None,
        status: str = "inactive",
        provenance_type: str = "UNVERIFIED",
        training_run_id: str | None = None,
        dataset_manifest_id: str | None = None,
        training_evidence: dict[str, Any] | None = None,
        training_receipt: dict[str, Any] | TrainingExecutionReceipt | None = None,
        artifact_bytes: bytes | None = None,
        provenance_receipt: dict[str, Any] | TrainingExecutionReceipt | None = None,
        expected_architecture: str | None = None,
        expected_feature_schema_version: str | None = None,
        expected_input_dim: int | None = None,
        expected_feature_names: list[str] | tuple[str, ...] | None = None,
        model_contract: Any | None = None,
    ) -> dict[str, Any]:
        """Save a new version of the global model weights. Defaults to inactive candidate.

        Args:
            simulation_id: ID of the active simulation run.
            state_dict: PyTorch model state dict.
            metrics: Dict of metrics (accuracy, f1_score, auc_roc, recall, etc.).
            is_promoted: Whether this version is promoted as active champion (defaults to False).
            git_commit_hash: Hash of the git commit.
            dataset_hash: Hash of the training dataset.
            dp_noise_profile: DP mechanism profile details.
            status: Initial status of the version.
            provenance_type: Provenance classification ('UNVERIFIED', 'STRUCTURAL_FIXTURE',
                             'TRAINING_RECORDED', 'TRAINING_EVIDENCE_VERIFIED', 'LEGACY_UNVERIFIED').
            training_run_id: Identifier of the training job.
            dataset_manifest_id: Identifier of the training dataset manifest.
            training_evidence: Execution evidence produced during training.
            training_receipt: Cryptographically signed training execution receipt from trusted authority.
            artifact_bytes: Raw bytes of the model artifact.
            provenance_receipt: Alias for training_receipt.
            expected_architecture: Expected model architecture name derived from candidate contract.
            expected_feature_schema_version: Expected feature schema version derived from candidate contract.
            expected_input_dim: Expected input dimension derived from candidate contract.
            expected_feature_names: Expected feature names list with exact ordering.
            model_contract: Optional candidate model contract object/dict providing expected schema.

        Returns:
            The created version entry metadata.
        """
        from app.config import get_settings
        settings = get_settings()

        # Derive expected contract parameters if model_contract provided
        exp_arch = expected_architecture
        exp_schema_ver = expected_feature_schema_version
        exp_dim = expected_input_dim
        exp_names = expected_feature_names

        if model_contract is not None:
            if isinstance(model_contract, dict):
                if exp_arch is None:
                    exp_arch = model_contract.get("architecture") or model_contract.get("expected_architecture")
                if exp_schema_ver is None:
                    exp_schema_ver = model_contract.get("feature_schema_version") or model_contract.get("expected_feature_schema_version")
                if exp_dim is None:
                    exp_dim = model_contract.get("input_dim") or model_contract.get("expected_input_dim")
                if exp_names is None:
                    exp_names = model_contract.get("feature_names") or model_contract.get("expected_feature_names")
            else:
                if exp_arch is None:
                    exp_arch = getattr(model_contract, "architecture", None) or getattr(model_contract, "expected_architecture", None)
                if exp_schema_ver is None:
                    exp_schema_ver = getattr(model_contract, "feature_schema_version", None) or getattr(model_contract, "expected_feature_schema_version", None)
                if exp_dim is None:
                    exp_dim = getattr(model_contract, "input_dim", None) or getattr(model_contract, "expected_input_dim", None)
                if exp_names is None:
                    exp_names = getattr(model_contract, "feature_names", None) or getattr(model_contract, "expected_feature_names", None)

        # Parse training receipt if provided
        receipt_dict: dict[str, Any] | None = None
        has_valid_receipt = False
        active_receipt = training_receipt or provenance_receipt
        if active_receipt is not None:
            if isinstance(active_receipt, TrainingExecutionReceipt):
                receipt_dict = active_receipt.to_dict()
            elif isinstance(active_receipt, dict):
                receipt_dict = dict(active_receipt)

        clean_prov = provenance_type.strip() if provenance_type else "UNVERIFIED"
        clean_upper = clean_prov.upper()

        has_recorded_evidence = _validate_training_evidence(training_run_id, training_evidence)

        attestation_status = "NONE"
        if receipt_dict is not None:
            verifier = self._verifier or get_default_training_verifier()
            is_valid_rcpt, rcpt_reason = verifier.verify_receipt(
                receipt_dict,
                candidate_artifact_sha256=None,
                simulation_id=simulation_id,
                expected_architecture=exp_arch,
                expected_feature_schema_version=exp_schema_ver,
                expected_input_dim=exp_dim,
                expected_feature_names=exp_names,
            )
            if is_valid_rcpt:
                has_valid_receipt = True
                final_provenance = "TRAINING_RECORDED"
                attestation_status = "RECEIPT_VERIFIED_TRAINING_AUTHORITY"
                is_prod_eligible = True
            else:
                final_provenance = "UNVERIFIED"
                attestation_status = f"INVALID_RECEIPT: {rcpt_reason}"
                is_prod_eligible = False
        else:
            # Caller supplied metadata without a cryptographically verified receipt!
            # Numeric evidence or caller-selected labels must NEVER establish trusted production provenance!
            if clean_upper == "STRUCTURAL_FIXTURE":
                final_provenance = "STRUCTURAL_FIXTURE"
            elif clean_upper == "LEGACY_UNVERIFIED":
                final_provenance = "LEGACY_UNVERIFIED"
            else:
                final_provenance = "UNVERIFIED"

            attestation_status = (
                "CALLER_NUMERIC_EVIDENCE_UNAUTHENTICATED"
                if has_recorded_evidence
                else "NONE"
            )
            is_prod_eligible = False

        sim_dir = self._get_sim_dir(simulation_id)
        file_lock = _get_registry_file_lock(sim_dir)

        with self._lock, file_lock:
            # Clean up any stale temporary files from previous crashed runs
            if os.path.isdir(sim_dir):
                for f_name in os.listdir(sim_dir):
                    if f_name.startswith(".tmp_model_") and f_name.endswith(".pt"):
                        with contextlib.suppress(OSError):
                            os.remove(os.path.join(sim_dir, f_name))

            manifest = self._load_manifest(simulation_id)
            next_version = 1
            if manifest:
                next_version = max(entry["version"] for entry in manifest) + 1

            filename = f"model_v{next_version}.pt"
            filepath = os.path.join(sim_dir, filename)
            final_already_existed = os.path.exists(filepath)

            tmp_filename = f".tmp_model_v{next_version}_{os.getpid()}_{threading.get_ident()}_{uuid.uuid4().hex}.pt"
            tmp_filepath = os.path.join(sim_dir, tmp_filename)

            newly_created_temp: str | None = None
            newly_created_final: str | None = None

            try:
                if artifact_bytes is not None:
                    with open(tmp_filepath, "wb") as f:
                        f.write(artifact_bytes)
                        f.flush()
                        os.fsync(f.fileno())
                else:
                    torch.save(state_dict, tmp_filepath)
                newly_created_temp = tmp_filepath
                logger.debug("Serialized model version %d to staging path %s", next_version, tmp_filepath)

                file_sha256 = compute_file_sha256(tmp_filepath)

                # If receipt provided, verify artifact SHA256 binding and schema constraints
                if receipt_dict is not None and has_valid_receipt:
                    verifier = self._verifier or get_default_training_verifier()
                    is_valid_binding, binding_reason = verifier.verify_receipt(
                        receipt_dict,
                        candidate_artifact_sha256=file_sha256,
                        simulation_id=simulation_id,
                        expected_architecture=exp_arch,
                        expected_feature_schema_version=exp_schema_ver,
                        expected_input_dim=exp_dim,
                        expected_feature_names=exp_names,
                    )
                    if not is_valid_binding:
                        has_valid_receipt = False
                        final_provenance = "UNVERIFIED"
                        attestation_status = f"INVALID_RECEIPT: {binding_reason}"
                        is_prod_eligible = False

                max_gen = max((e.get("generation", 0) for e in manifest), default=0)
                entry_gen = (max_gen + 1) if is_promoted else (max_gen if max_gen > 0 else 1)

                verified_schema_meta = None
                if has_valid_receipt and receipt_dict is not None:
                    verified_schema_meta = {
                        "architecture": receipt_dict.get("architecture"),
                        "feature_schema_version": receipt_dict.get("feature_schema_version"),
                        "input_dim": receipt_dict.get("input_dim"),
                        "feature_names": list(receipt_dict.get("feature_names", [])),
                    }

                entry: dict[str, Any] = {
                    "version": next_version,
                    "simulation_id": simulation_id,
                    "generation": entry_gen,
                    "filename": filename,
                    "architecture": verified_schema_meta["architecture"] if verified_schema_meta else exp_arch,
                    "feature_schema_version": verified_schema_meta["feature_schema_version"] if verified_schema_meta else exp_schema_ver,
                    "input_dim": verified_schema_meta["input_dim"] if verified_schema_meta else exp_dim,
                    "feature_names": verified_schema_meta["feature_names"] if verified_schema_meta else (list(exp_names) if exp_names is not None else None),
                    "verified_schema": verified_schema_meta,
                    "metrics": metrics,
                    "metrics_provenance": "CALLER_SUPPLIED_UNVERIFIED",
                    "is_active": is_promoted,
                    "status": status if status != "inactive" or not is_promoted else "champion",
                    "git_commit_hash": git_commit_hash or "unknown",
                    "dataset_hash": dataset_hash or "unknown",
                    "sha256": file_sha256,
                    "dp_noise_profile": dp_noise_profile
                    or {"mechanism": "none", "epsilon": 0.0, "delta": 0.0},
                    "provenance_type": final_provenance,
                    "is_production_eligible": is_prod_eligible,
                    "training_run_id": training_run_id or "unspecified",
                    "dataset_manifest_id": dataset_manifest_id or "unspecified",
                    "training_evidence": training_evidence or {},
                    "training_receipt": receipt_dict if has_valid_receipt else None,
                    "attestation_status": attestation_status,
                    "sign_offs": [],
                    "created_at": datetime.now(UTC).isoformat(),
                }

                # Centralized production admission check on promotion (BEFORE publishing artifact)
                if is_promoted:
                    validate_production_admission(entry, context="save_version")

                # Atomically promote staged artifact to authoritative filename
                os.replace(tmp_filepath, filepath)
                newly_created_temp = None
                newly_created_final = filepath
                logger.info("Saved model version %d to %s", next_version, filepath)

                # If this new version is promoted, set other versions to inactive
                if is_promoted:
                    for existing_entry in manifest:
                        existing_entry["is_active"] = False
                        if existing_entry.get("status") == "champion":
                            existing_entry["status"] = "inactive"

                manifest.append(entry)
                # Authoritative manifest is saved FIRST (Section 9.1)
                self._save_manifest(simulation_id, manifest)

                # If promoted, update secondary link and invalidate cache
                if is_promoted:
                    try:
                        self._update_global_model_link(simulation_id, filepath)
                    except Exception as link_err:
                        logger.error("Failed to update global model link: %s", link_err)
                    with contextlib.suppress(Exception):
                        from app.application.services.model_service import ModelService

                        ModelService(settings).invalidate_model_cache()

                return entry
            except Exception:
                if newly_created_temp and os.path.exists(newly_created_temp):
                    with contextlib.suppress(OSError):
                        os.remove(newly_created_temp)
                if newly_created_final and not final_already_existed and os.path.exists(newly_created_final):
                    with contextlib.suppress(OSError):
                        os.remove(newly_created_final)
                raise


    def resolve_and_verify_champion(
        self, simulation_id: str | None = None
    ) -> tuple[str, bytes, dict[str, Any]] | None:
        """Resolve, verify integrity, and return (artifact_path, bytes_data, metadata) for active champion.

        Enforces:
        1. Registry manifest is the single source of truth (status='champion' and is_active=True).
        2. Rejects ambiguous multiple active champions deterministically.
        3. Verifies file existence, containment within storage, regular file type, non-emptiness.
        4. Verifies in-memory snapshot SHA-256 against recorded manifest digest.
        5. Returns in-memory bytes to eliminate TOCTOU races during model loading.
        """
        with self._lock:
            if not os.path.isdir(self.registry_root):
                return None

            candidates: list[tuple[str, dict[str, Any], str]] = []

            sim_dirs = [simulation_id] if simulation_id else sorted(os.listdir(self.registry_root))
            for s_id in sim_dirs:
                sim_path = os.path.join(self.registry_root, s_id)
                if not os.path.isdir(sim_path):
                    continue
                manifest = self._load_manifest(s_id)
                active_entries = [
                    e for e in manifest if e.get("status") == "champion" and e.get("is_active")
                ]
                if len(active_entries) > 1:
                    raise ModelIntegrityError(
                        f"Corrupt manifest in simulation '{s_id}': multiple active champion entries found "
                        f"({[e.get('version') for e in active_entries]})."
                    )
                if len(active_entries) == 1:
                    entry = active_entries[0]
                    fname = entry.get("filename") or f"model_v{entry.get('version')}.pt"
                    fpath = os.path.join(sim_path, fname)
                    candidates.append((s_id, entry, fpath))

            if not candidates:
                return None

            if len(candidates) > 1:
                conflicting_sims = [c[0] for c in candidates]
                raise ModelCompatibilityError(
                    f"Ambiguous champion state: multiple conflicting active champions exist across simulations: "
                    f"{conflicting_sims}. Deterministic resolution requires specifying simulation_id or deactivating conflicting champions."
                )

            sim_id, entry, filepath = candidates[0]

            fname = entry.get("filename", os.path.basename(filepath))
            if ".." in fname or os.path.isabs(fname):
                raise ModelIntegrityError(
                    f"Path traversal detected in manifest filename '{fname}'."
                )

            # Resolve real filesystem paths to eliminate symlink traversal and escapes (Defect E)
            real_filepath = os.path.realpath(filepath)
            real_storage = os.path.realpath(self.storage_dir)

            # Path & regular file validation
            if not os.path.exists(real_filepath):
                raise ModelNotAvailableError(
                    f"Active champion artifact file '{filepath}' for simulation '{sim_id}' does not exist on disk."
                )
            if os.path.isdir(real_filepath) or not os.path.isfile(real_filepath):
                raise ModelIntegrityError(
                    f"Active champion artifact path '{filepath}' is not a regular file."
                )

            # Strict containment check: realpath must be strictly contained within real_storage
            try:
                common = os.path.commonpath([real_filepath, real_storage])
                if common != real_storage:
                    raise ModelIntegrityError(
                        f"Active champion artifact path '{filepath}' (realpath: '{real_filepath}') is located outside trusted storage directory '{self.storage_dir}'."
                    )
            except ValueError as exc:
                raise ModelIntegrityError(
                    f"Path containment check failed for artifact '{filepath}': {exc}"
                ) from exc

            # Explicit check if filepath is a symlink: reject if symlink targets outside storage root
            if os.path.islink(filepath):
                try:
                    symlink_target = os.path.realpath(os.readlink(filepath) if hasattr(os, "readlink") else filepath)
                    if os.path.commonpath([symlink_target, real_storage]) != real_storage:
                        raise ModelIntegrityError(
                            f"Active champion symlink '{filepath}' targets external path '{symlink_target}' outside trusted storage directory."
                        )
                except (OSError, ValueError) as exc:
                    raise ModelIntegrityError(
                        f"Symlink validation failed for '{filepath}': {exc}"
                    ) from exc

            # Non-empty check
            if os.path.getsize(real_filepath) == 0:
                raise ModelIntegrityError(
                    f"Active champion artifact file '{filepath}' is empty (0 bytes)."
                )

            # Read bytes into memory snapshot directly from resolved realpath to eliminate TOCTOU races
            try:
                with open(real_filepath, "rb") as f:
                    bytes_data = f.read()
            except Exception as exc:
                raise ModelIntegrityError(
                    f"Failed to read champion artifact '{filepath}': {exc}"
                ) from exc

            actual_sha256 = hashlib.sha256(bytes_data).hexdigest()
            expected_sha256 = entry.get("sha256")
            if not expected_sha256:
                raise ModelIntegrityError(
                    f"Champion manifest entry for simulation '{sim_id}' v{entry.get('version')} lacks recorded sha256."
                )
            if not hmac.compare_digest(actual_sha256, expected_sha256):
                raise ModelIntegrityError(
                    f"Champion artifact SHA-256 digest mismatch for simulation '{sim_id}' v{entry.get('version')}: "
                    f"expected '{expected_sha256}', actual '{actual_sha256}'."
                )

            version_val = entry.get("version")
            if version_val is None or not isinstance(version_val, int) or version_val < 1:
                raise ModelIntegrityError("Champion manifest entry contains invalid or missing version number.")

            # Provenance checks via centralized production admission policy
            provenance_type = entry.get("provenance_type", "UNVERIFIED")
            from app.config import get_settings
            settings = get_settings()
            if settings.app_env.lower() == "production":
                validate_production_admission(entry, context="resolve_and_verify_champion")
                receipt_dict = entry.get("training_receipt")
                if not receipt_dict:
                    raise ModelIntegrityError(
                        "Production admission rejected: model lacks verified cryptographic training execution receipt (resolve_and_verify_champion)."
                    )
                verifier = self._verifier or get_default_training_verifier()
                is_valid_rcpt, rcpt_reason = verifier.verify_receipt(
                    receipt_dict,
                    candidate_artifact_sha256=actual_sha256,
                    candidate_artifact_bytes=bytes_data,
                    simulation_id=sim_id,
                    expected_architecture=entry.get("architecture"),
                    expected_feature_schema_version=entry.get("feature_schema_version"),
                    expected_input_dim=entry.get("input_dim"),
                    expected_feature_names=entry.get("feature_names"),
                )
                if not is_valid_rcpt:
                    raise ModelIntegrityError(
                        f"Production admission rejected: cryptographic training receipt verification failed ({rcpt_reason}) (resolve_and_verify_champion)."
                    )
            elif entry.get("training_receipt"):
                receipt_dict = entry.get("training_receipt")
                if isinstance(receipt_dict, dict) and receipt_dict.get("key_id"):
                    verifier = self._verifier or get_default_training_verifier()
                    if not verifier.is_key_trusted(receipt_dict["key_id"]):
                        raise ModelIntegrityError(
                            f"Cryptographic training receipt verification failed (Receipt key_id '{receipt_dict['key_id']}' has been explicitly revoked.) (resolve_and_verify_champion)."
                        )

            # Truthful classification of sign-offs
            sign_offs = entry.get("sign_offs", [])
            authenticity_status = "INTEGRITY_VERIFIED_UNAUTHENTICATED_MANIFEST"
            authorization_scheme = "NONE"
            is_independent_human = False

            if isinstance(sign_offs, list) and len(sign_offs) > 0:
                ml_signs = [s for s in sign_offs if s.get("role") in ("ml_engineer", "ml_lead")]
                comp_signs = [s for s in sign_offs if s.get("role") in ("compliance", "compliance_officer")]

                hmac_valid = False
                if ml_signs and comp_signs:
                    ml_s = ml_signs[0]
                    comp_s = comp_signs[0]
                    # Four-Eyes Principle check: distinct individuals
                    if ml_s.get("user") != comp_s.get("user"):
                        domain_key = derive_domain_key(settings.payload_signing_secret, DOMAIN_APPROVAL_SIGNOFF)
                        expected_ml_sig_domain = hmac.new(
                            domain_key,
                            f"{sim_id}:{version_val}:{actual_sha256}:ml_engineer:{ml_s.get('user')}".encode(),
                            hashlib.sha256,
                        ).hexdigest()
                        expected_ml_sig_raw = hmac.new(
                            settings.payload_signing_secret.encode(),
                            f"{sim_id}:{version_val}:{actual_sha256}:ml_engineer:{ml_s.get('user')}".encode(),
                            hashlib.sha256,
                        ).hexdigest()
                        expected_comp_sig_domain = hmac.new(
                            domain_key,
                            f"{sim_id}:{version_val}:{actual_sha256}:compliance:{comp_s.get('user')}".encode(),
                            hashlib.sha256,
                        ).hexdigest()
                        expected_comp_sig_raw = hmac.new(
                            settings.payload_signing_secret.encode(),
                            f"{sim_id}:{version_val}:{actual_sha256}:compliance:{comp_s.get('user')}".encode(),
                            hashlib.sha256,
                        ).hexdigest()

                        ml_ok = hmac.compare_digest(ml_s.get("signature", ""), expected_ml_sig_domain) or hmac.compare_digest(ml_s.get("signature", ""), expected_ml_sig_raw)
                        comp_ok = hmac.compare_digest(comp_s.get("signature", ""), expected_comp_sig_domain) or hmac.compare_digest(comp_s.get("signature", ""), expected_comp_sig_raw)
                        if ml_ok and comp_ok:
                            hmac_valid = True

                if hmac_valid:
                    authenticity_status = "APPLICATION_HMAC_AUTHENTICATED"
                    authorization_scheme = "APPLICATION_HMAC_SHA256"
                    is_independent_human = False
                else:
                    if settings.app_env.lower() == "production":
                        raise ModelIntegrityError(
                            f"Production admission rejected: active champion sign-offs failed cryptographic HMAC verification "
                            f"for simulation '{sim_id}' v{version_val} (resolve_and_verify_champion)."
                        )
                    authenticity_status = "SIGNOFFS_RECORDED_UNVERIFIED"
                    authorization_scheme = "UNVERIFIED"
                    is_independent_human = False

            is_prod_eligible = provenance_type in ("TRAINING_RECORDED", "TRAINING_EVIDENCE_VERIFIED")

            resolved_metadata = {
                "version": version_val,
                "generation": entry.get("generation", 1),
                "filename": entry.get("filename", os.path.basename(filepath)),
                "simulation_id": sim_id,
                "artifact_path": filepath,
                "sha256": actual_sha256,
                "expected_sha256": expected_sha256,
                "status": "champion",
                "is_active": True,
                "is_verified": True,
                "authenticity_status": authenticity_status,
                "is_independent_human_authorization": is_independent_human,
                "authorization_scheme": authorization_scheme,
                "architecture": entry.get("architecture", "FraudDetectionModel-GroupNorm"),
                "input_dim": entry.get("input_dim", 10),
                "feature_schema_version": entry.get("feature_schema_version"),
                "feature_names": entry.get("feature_names"),
                "verified_schema": entry.get("verified_schema"),
                "training_receipt": entry.get("training_receipt"),
                "metrics": entry.get("metrics", {}),
                "metrics_provenance": entry.get("metrics_provenance", "CALLER_SUPPLIED_UNVERIFIED"),
                "git_commit_hash": entry.get("git_commit_hash", "unknown"),
                "dataset_hash": entry.get("dataset_hash", "unknown"),
                "dp_noise_profile": entry.get("dp_noise_profile", {}),
                "provenance_type": provenance_type,
                "is_production_eligible": is_prod_eligible,
                "training_run_id": entry.get("training_run_id", "unspecified"),
                "dataset_manifest_id": entry.get("dataset_manifest_id", "unspecified"),
                "attestation_status": entry.get("attestation_status", "NONE"),
                "sign_offs": sign_offs,
                "created_at": entry.get("created_at"),
            }

            return filepath, bytes_data, resolved_metadata

    def resolve_and_verify_challenger(
        self, simulation_id: str | None = None
    ) -> tuple[str, bytes, dict[str, Any]] | None:
        """Resolve, verify integrity, and return (artifact_path, bytes_data, metadata) for active challenger.

        Enforces:
        1. Registry manifest is the single source of truth (status='challenger').
        2. Rejects ambiguous multiple active challengers deterministically.
        3. Verifies file existence, containment within storage, regular file type, non-emptiness.
        4. Verifies in-memory snapshot SHA-256 against recorded manifest digest.
        5. In production, enforces valid cryptographic receipt, trusted issuer, artifact integrity,
           schema compatibility, and production admission eligibility (without demanding champion-only signoffs).
        6. Returns in-memory bytes to eliminate TOCTOU races during model loading.
        """
        with self._lock:
            if not os.path.isdir(self.registry_root):
                return None

            candidates: list[tuple[str, dict[str, Any], str]] = []

            sim_dirs = [simulation_id] if simulation_id else sorted(os.listdir(self.registry_root))
            for s_id in sim_dirs:
                sim_path = os.path.join(self.registry_root, s_id)
                if not os.path.isdir(sim_path):
                    continue
                manifest = self._load_manifest(s_id)
                challenger_entries = [
                    e for e in manifest if e.get("status") == "challenger"
                ]
                if len(challenger_entries) > 1:
                    raise ModelIntegrityError(
                        f"Corrupt manifest in simulation '{s_id}': multiple active challenger entries found "
                        f"({[e.get('version') for e in challenger_entries]})."
                    )
                if len(challenger_entries) == 1:
                    entry = challenger_entries[0]
                    fname = entry.get("filename") or f"model_v{entry.get('version')}.pt"
                    fpath = os.path.join(sim_path, fname)
                    candidates.append((s_id, entry, fpath))

            if not candidates:
                return None

            if len(candidates) > 1:
                conflicting_sims = [c[0] for c in candidates]
                raise ModelCompatibilityError(
                    f"Ambiguous challenger state: multiple conflicting active challengers exist across simulations: "
                    f"{conflicting_sims}. Deterministic resolution requires specifying simulation_id or deactivating conflicting challengers."
                )

            sim_id, entry, filepath = candidates[0]

            fname = entry.get("filename", os.path.basename(filepath))
            if ".." in fname or os.path.isabs(fname):
                raise ModelIntegrityError(
                    f"Path traversal detected in manifest filename '{fname}'."
                )

            real_filepath = os.path.realpath(filepath)
            real_storage = os.path.realpath(self.storage_dir)

            if not os.path.exists(real_filepath):
                raise ModelNotAvailableError(
                    f"Active challenger artifact file '{filepath}' for simulation '{sim_id}' does not exist on disk."
                )
            if os.path.isdir(real_filepath) or not os.path.isfile(real_filepath):
                raise ModelIntegrityError(
                    f"Active challenger artifact path '{filepath}' is not a regular file."
                )

            try:
                common = os.path.commonpath([real_filepath, real_storage])
                if common != real_storage:
                    raise ModelIntegrityError(
                        f"Active challenger artifact path '{filepath}' (realpath: '{real_filepath}') is located outside trusted storage directory '{self.storage_dir}'."
                    )
            except ValueError as exc:
                raise ModelIntegrityError(
                    f"Path containment check failed for artifact '{filepath}': {exc}"
                ) from exc

            if os.path.islink(filepath):
                try:
                    symlink_target = os.path.realpath(os.readlink(filepath) if hasattr(os, "readlink") else filepath)
                    if os.path.commonpath([symlink_target, real_storage]) != real_storage:
                        raise ModelIntegrityError(
                            f"Active challenger symlink '{filepath}' targets external path '{symlink_target}' outside trusted storage directory."
                        )
                except (OSError, ValueError) as exc:
                    raise ModelIntegrityError(
                        f"Symlink validation failed for '{filepath}': {exc}"
                    ) from exc

            if os.path.getsize(real_filepath) == 0:
                raise ModelIntegrityError(
                    f"Active challenger artifact file '{filepath}' is empty (0 bytes)."
                )

            try:
                with open(real_filepath, "rb") as f:
                    bytes_data = f.read()
            except Exception as exc:
                raise ModelIntegrityError(
                    f"Failed to read challenger artifact '{filepath}': {exc}"
                ) from exc

            actual_sha256 = hashlib.sha256(bytes_data).hexdigest()
            expected_sha256 = entry.get("sha256")
            if not expected_sha256:
                raise ModelIntegrityError(
                    f"Challenger manifest entry for simulation '{sim_id}' v{entry.get('version')} lacks recorded sha256."
                )
            if not hmac.compare_digest(actual_sha256, expected_sha256):
                raise ModelIntegrityError(
                    f"Challenger artifact SHA-256 digest mismatch for simulation '{sim_id}' v{entry.get('version')}: "
                    f"expected '{expected_sha256}', actual '{actual_sha256}'."
                )

            version_val = entry.get("version")
            if version_val is None or not isinstance(version_val, int) or version_val < 1:
                raise ModelIntegrityError("Challenger manifest entry contains invalid or missing version number.")

            from app.config import get_settings
            settings = get_settings()
            if settings.app_env.lower() == "production":
                validate_production_admission(entry, context="challenger_serving")
                receipt_dict = entry.get("training_receipt")
                if not receipt_dict:
                    raise ModelIntegrityError(
                        "Production admission rejected: model lacks verified cryptographic training execution receipt (challenger_serving)."
                    )
                verifier = self._verifier or get_default_training_verifier()
                is_valid_rcpt, rcpt_reason = verifier.verify_receipt(
                    receipt_dict,
                    candidate_artifact_sha256=actual_sha256,
                    candidate_artifact_bytes=bytes_data,
                    simulation_id=sim_id,
                    expected_architecture=entry.get("architecture"),
                    expected_feature_schema_version=entry.get("feature_schema_version"),
                    expected_input_dim=entry.get("input_dim"),
                    expected_feature_names=entry.get("feature_names"),
                )
                if not is_valid_rcpt:
                    raise ModelIntegrityError(
                        f"Production admission rejected: cryptographic training receipt verification failed ({rcpt_reason}) (challenger_serving)."
                    )
            elif entry.get("training_receipt"):
                receipt_dict = entry.get("training_receipt")
                if isinstance(receipt_dict, dict) and receipt_dict.get("key_id"):
                    verifier = self._verifier or get_default_training_verifier()
                    if not verifier.is_key_trusted(receipt_dict["key_id"]):
                        raise ModelIntegrityError(
                            f"Cryptographic training receipt verification failed (Receipt key_id '{receipt_dict['key_id']}' has been explicitly revoked.) (challenger_serving)."
                        )

            provenance_type = entry.get("provenance_type", "UNVERIFIED")
            is_prod_eligible = provenance_type in ("TRAINING_RECORDED", "TRAINING_EVIDENCE_VERIFIED")

            resolved_metadata = {
                "version": version_val,
                "generation": entry.get("generation", 1),
                "filename": entry.get("filename", os.path.basename(filepath)),
                "simulation_id": sim_id,
                "artifact_path": filepath,
                "sha256": actual_sha256,
                "expected_sha256": expected_sha256,
                "status": "challenger",
                "is_active": False,
                "is_verified": True,
                "authenticity_status": entry.get("attestation_status", "RECEIPT_VERIFIED_TRAINING_AUTHORITY"),
                "is_independent_human_authorization": False,
                "authorization_scheme": "CRYPTOGRAPHIC_TRAINING_RECEIPT",
                "architecture": entry.get("architecture", "FraudDetectionModel-GroupNorm"),
                "input_dim": entry.get("input_dim", 10),
                "feature_schema_version": entry.get("feature_schema_version"),
                "feature_names": entry.get("feature_names"),
                "verified_schema": entry.get("verified_schema"),
                "training_receipt": entry.get("training_receipt"),
                "metrics": entry.get("metrics", {}),
                "metrics_provenance": entry.get("metrics_provenance", "CALLER_SUPPLIED_UNVERIFIED"),
                "git_commit_hash": entry.get("git_commit_hash", "unknown"),
                "dataset_hash": entry.get("dataset_hash", "unknown"),
                "dp_noise_profile": entry.get("dp_noise_profile", {}),
                "provenance_type": provenance_type,
                "is_production_eligible": is_prod_eligible,
                "training_run_id": entry.get("training_run_id", "unspecified"),
                "dataset_manifest_id": entry.get("dataset_manifest_id", "unspecified"),
                "attestation_status": entry.get("attestation_status", "NONE"),
                "sign_offs": entry.get("sign_offs", []),
                "created_at": entry.get("created_at"),
            }

            return filepath, bytes_data, resolved_metadata

    def get_challenger_artifact_path(self, simulation_id: str | None = None) -> str | None:
        """Resolve the active challenger model file path on disk.

        Identified strictly via authoritative registry manifest.
        Returns None if no active challenger exists in the registry.
        """
        with self._lock:
            resolved = self.resolve_and_verify_challenger(simulation_id=simulation_id)
            if resolved is not None:
                return resolved[0]
            return None

    def get_challenger_metadata(self, simulation_id: str | None = None) -> dict[str, Any] | None:
        """Return metadata for the currently active challenger model."""
        with self._lock:
            resolved = self.resolve_and_verify_challenger(simulation_id=simulation_id)
            if resolved is not None:
                return resolved[2]
            return None

    def get_champion_artifact_path(self, simulation_id: str | None = None) -> str | None:
        """Resolve the active champion model file path on disk.

        Identified strictly via authoritative registry manifest.
        Returns None if no active champion exists in the registry.
        """
        with self._lock:
            resolved = self.resolve_and_verify_champion(simulation_id=simulation_id)
            if resolved is not None:
                return resolved[0]
            return None

    def get_champion_metadata(self, simulation_id: str | None = None) -> dict[str, Any] | None:
        """Return metadata for the currently active champion model."""
        with self._lock:
            resolved = self.resolve_and_verify_champion(simulation_id=simulation_id)
            if resolved is not None:
                return resolved[2]
            return None

    def get_active_champion_identity(self, simulation_id: str | None = None) -> dict[str, Any] | None:
        """Lightweight resolution of current active champion identity.

        Reads only manifest metadata without opening the model artifact file or computing SHA-256.
        Used on warm inference paths to revalidate champion version without avoidable I/O.
        Returns None if no active champion exists.
        """
        with self._lock:
            if not os.path.isdir(self.registry_root):
                return None

            candidates = []
            sim_dirs = [simulation_id] if simulation_id else sorted(os.listdir(self.registry_root))
            for s_id in sim_dirs:
                sim_path = os.path.join(self.registry_root, s_id)
                if not os.path.isdir(sim_path):
                    continue
                manifest = self._load_manifest(s_id)
                active_entries = [
                    e for e in manifest if e.get("status") == "champion" and e.get("is_active")
                ]
                if len(active_entries) == 1:
                    entry = active_entries[0]
                    candidates.append((s_id, entry))

            if len(candidates) != 1:
                return None

            s_id, entry = candidates[0]
            from app.config import get_settings
            settings = get_settings()
            if settings.app_env.lower() == "production":
                prov = str(entry.get("provenance_type", "UNVERIFIED")).strip().upper()
                if prov in ("UNVERIFIED", "STRUCTURAL_FIXTURE", "LEGACY_UNVERIFIED") or prov not in (
                    "TRAINING_RECORDED",
                    "TRAINING_EVIDENCE_VERIFIED",
                ):
                    return None

                # Production champion requires valid cryptographic dual sign-offs
                sign_offs = entry.get("sign_offs", [])
                if not isinstance(sign_offs, list) or len(sign_offs) < 2:
                    return None
                ml_signs = [
                    s for s in sign_offs
                    if s.get("role") in ("ml_engineer", "ml_lead") and s.get("is_revoked") is not True
                ]
                comp_signs = [
                    s for s in sign_offs
                    if s.get("role") in ("compliance", "compliance_officer") and s.get("is_revoked") is not True
                ]
                if not ml_signs or not comp_signs:
                    return None
                ml_s, comp_s = ml_signs[0], comp_signs[0]
                if ml_s.get("user") == comp_s.get("user"):
                    return None
                secret = settings.payload_signing_secret
                if not secret or not secret.strip() or len(secret.strip()) < 16:
                    return None
                domain_key = derive_domain_key(secret, DOMAIN_APPROVAL_SIGNOFF)
                actual_sha = entry.get("sha256") or ""
                ver_val = entry.get("version")
                for s, norm_role in ((ml_s, "ml_engineer"), (comp_s, "compliance")):
                    u = s.get("user", "")
                    if u in {"admin", "analyst_a1", "analyst_b1", "analyst_c1", "auditor"}:
                        return None
                    if not s.get("is_role_verified") or not s.get("is_identity_verified"):
                        return None
                    canonical_payload = f"{s_id}:{ver_val}:{actual_sha}:{norm_role}:{u}"
                    if s.get("payload_bound") and s.get("payload_bound") != canonical_payload:
                        return None
                    exp_domain = hmac.new(domain_key, canonical_payload.encode("utf-8"), hashlib.sha256).hexdigest()
                    exp_raw = hmac.new(secret.encode("utf-8"), canonical_payload.encode("utf-8"), hashlib.sha256).hexdigest()
                    sig = s.get("signature") or ""
                    if not sig or not (hmac.compare_digest(sig, exp_domain) or hmac.compare_digest(sig, exp_raw)):
                        return None

            key_id = None
            rcpt = entry.get("training_receipt")
            if isinstance(rcpt, dict):
                key_id = rcpt.get("key_id")
            if key_id:
                from app.application.services.training_provenance_service import (
                    get_default_training_verifier,
                )
                verifier = self._verifier or get_default_training_verifier()
                try:
                    if not verifier.is_key_trusted(key_id):
                        logger.warning(
                            "Active champion key_id '%s' for simulation '%s' is revoked or untrusted.",
                            key_id,
                            s_id,
                        )
                        return None
                except Exception as exc:
                    logger.error(
                        "Failed to verify trust for active champion key_id '%s': %s",
                        key_id,
                        exc,
                    )
                    return None
            elif settings.app_env.lower() == "production":
                return None

            return {
                "version": entry.get("version"),
                "generation": entry.get("generation", 1),
                "simulation_id": s_id,
                "sha256": entry.get("sha256"),
                "filename": entry.get("filename"),
                "architecture": entry.get("architecture", "FraudDetectionModel-GroupNorm"),
                "input_dim": entry.get("input_dim", 10),
                "feature_schema_version": entry.get("feature_schema_version"),
                "feature_names": entry.get("feature_names"),
                "verified_schema": entry.get("verified_schema"),
                "provenance_type": entry.get("provenance_type", "UNVERIFIED"),
                "issuer_key_id": key_id,
                "key_id": key_id,
            }

    def _update_global_model_link(self, simulation_id: str, filepath: str) -> None:
        """Update the root global_model.pt file to point to the active version atomically."""
        global_path = os.path.join(self.storage_dir, "global_model.pt")
        tmp_global_path = os.path.join(
            self.storage_dir, f"global_model_{os.getpid()}_{threading.get_ident()}.tmp"
        )
        try:
            shutil.copy2(filepath, tmp_global_path)
            os.replace(tmp_global_path, global_path)
            logger.info("Updated global_model.pt with version from %s", filepath)
        except Exception as e:
            logger.error("Failed to link/copy active version to global_model.pt: %s", e)
            if os.path.exists(tmp_global_path):
                with contextlib.suppress(OSError):
                    os.remove(tmp_global_path)

    def list_versions(self, simulation_id: str) -> list[dict[str, Any]]:
        """List all model versions tracked in this simulation registry."""
        return self._load_manifest(simulation_id)

    def load_version(self, simulation_id: str, version: int) -> dict[str, Any]:
        """Load state dict of a specific model version from the registry."""
        with self._lock:
            manifest = self._load_manifest(simulation_id)
            entry = next((e for e in manifest if e["version"] == version), None)
            if not entry:
                raise ValueError(f"Version {version} not found in registry for {simulation_id}")

            sim_dir = self._get_sim_dir(simulation_id)
            filepath = os.path.join(sim_dir, entry["filename"])
            if not os.path.exists(filepath):
                raise FileNotFoundError(f"Model file {filepath} not found on disk")

            try:
                return torch.load(filepath, map_location="cpu", weights_only=True)
            except Exception as e:
                logger.error("Failed to load model file: %s", e)
                raise

    def get_active_version(self, simulation_id: str) -> dict[str, Any] | None:
        """Get metadata of the currently active model version."""
        manifest = self._load_manifest(simulation_id)
        return next((e for e in manifest if e["is_active"]), None)

    def get_version_metadata(self, simulation_id: str, version: int) -> dict[str, Any] | None:
        """Get metadata of a specific model version from the registry."""
        manifest = self._load_manifest(simulation_id)
        return next((e for e in manifest if e["version"] == version), None)

    def promote_version(
        self,
        simulation_id: str,
        version: int,
        target_status: str = "champion",
    ) -> dict[str, Any]:
        """Promotes a model version to 'champion' or 'challenger' status."""
        if target_status not in ("champion", "challenger"):
            raise ValueError(f"Target status must be 'champion' or 'challenger', got '{target_status}'")

        sim_dir = self._get_sim_dir(simulation_id)
        file_lock = _get_registry_file_lock(sim_dir)
        with self._lock, file_lock:
            manifest = self._load_manifest(simulation_id)
            target_entry = next((e for e in manifest if e["version"] == version), None)
            if not target_entry:
                raise ValueError(f"Version {version} not found in registry for {simulation_id}")
            target_entry["simulation_id"] = simulation_id

            filepath = os.path.join(sim_dir, target_entry["filename"])
            if not os.path.exists(filepath):
                raise FileNotFoundError(f"Model file {filepath} not found on disk")

            from app.config import get_settings
            settings = get_settings()

            admission_context = "promote_version" if target_status == "champion" else "promote_challenger"
            validate_production_admission(target_entry, context=admission_context)

            if settings.app_env.lower() == "production":
                # Strict filename and path containment validation
                fname = target_entry.get("filename", "")
                if ".." in fname or os.path.isabs(fname):
                    raise ModelIntegrityError(f"Path traversal detected in manifest filename '{fname}'.")
                real_filepath = os.path.realpath(filepath)
                real_storage = os.path.realpath(self.storage_dir)
                try:
                    common = os.path.commonpath([real_filepath, real_storage])
                    if common != real_storage:
                        raise ModelIntegrityError(
                            f"Artifact path '{filepath}' is outside trusted storage directory '{self.storage_dir}'."
                        )
                except ValueError as exc:
                    raise ModelIntegrityError(f"Path containment check failed for artifact '{filepath}': {exc}") from exc

                if not os.path.isfile(real_filepath) or os.path.getsize(real_filepath) == 0:
                    raise ModelIntegrityError(f"Artifact file '{filepath}' is missing, not a regular file, or empty.")

                actual_sha256 = compute_file_sha256(real_filepath)
                expected_sha256 = target_entry.get("sha256")
                if not expected_sha256:
                    raise ModelIntegrityError(
                        f"Production admission rejected: model version {version} lacks recorded sha256 ({admission_context})."
                    )
                if not hmac.compare_digest(actual_sha256, expected_sha256):
                    raise ModelIntegrityError(
                        f"Production admission rejected: artifact SHA-256 digest mismatch for {simulation_id} v{version}: "
                        f"expected '{expected_sha256}', actual '{actual_sha256}' ({admission_context})."
                    )

                receipt_dict = target_entry.get("training_receipt")
                if not receipt_dict:
                    raise ModelIntegrityError(
                        f"Production admission rejected: model lacks verified cryptographic training execution receipt ({admission_context})."
                    )
                verifier = self._verifier or get_default_training_verifier()
                with open(real_filepath, "rb") as rf:
                    artifact_bytes = rf.read()
                is_valid_rcpt, rcpt_reason = verifier.verify_receipt(
                    receipt_dict,
                    candidate_artifact_sha256=actual_sha256,
                    candidate_artifact_bytes=artifact_bytes,
                    simulation_id=simulation_id,
                    expected_architecture=target_entry.get("architecture"),
                    expected_feature_schema_version=target_entry.get("feature_schema_version"),
                    expected_input_dim=target_entry.get("input_dim"),
                    expected_feature_names=target_entry.get("feature_names"),
                )
                if not is_valid_rcpt:
                    raise ModelIntegrityError(
                        f"Production admission rejected: cryptographic training receipt verification failed ({rcpt_reason}) ({admission_context})."
                    )
            elif target_entry.get("training_receipt"):
                rcpt = target_entry.get("training_receipt")
                if isinstance(rcpt, dict) and rcpt.get("key_id"):
                    verifier = self._verifier or get_default_training_verifier()
                    if not verifier.is_key_trusted(rcpt["key_id"]):
                        raise ModelIntegrityError(
                            f"Cryptographic training receipt verification failed (Receipt key_id '{rcpt['key_id']}' has been explicitly revoked.) ({admission_context})."
                        )

            max_gen = max((e.get("generation", 0) for e in manifest), default=0)
            if target_status == "champion":
                target_entry["generation"] = max_gen + 1
                for entry in manifest:
                    entry["is_active"] = entry["version"] == version
                    if entry["version"] == version:
                        entry["status"] = "champion"
                    elif entry.get("status") == "champion":
                        entry["status"] = "inactive"
            else:  # challenger
                for entry in manifest:
                    if entry["version"] == version:
                        entry["status"] = "challenger"
                        entry["is_active"] = False
                    elif entry.get("status") == "challenger":
                        entry["status"] = "inactive"

            # Authoritative manifest is saved FIRST (Section 9.1)
            self._save_manifest(simulation_id, manifest)

            # Secondary global model link updated SECOND
            if target_status == "champion":
                try:
                    self._update_global_model_link(simulation_id, filepath)
                except Exception as link_err:
                    logger.error("Failed to update global model link: %s", link_err)
                with contextlib.suppress(Exception):
                    from app.application.services.model_service import ModelService

                    ModelService(settings).invalidate_model_cache()

            with contextlib.suppress(Exception):
                from app.presentation.routers.realtime_inference import reset_model_cache

                reset_model_cache(simulation_id)

            with contextlib.suppress(Exception):
                from app.presentation.routers.predict import clear_all_serving_caches

                clear_all_serving_caches()

            logger.info("Promoted model version %d to %s for %s", version, target_status, simulation_id)
            return target_entry

    def rollback(self, simulation_id: str, version: int) -> dict[str, Any]:
        """Rollback/promote a specific historical version as the active model."""
        sim_dir = self._get_sim_dir(simulation_id)
        file_lock = _get_registry_file_lock(sim_dir)
        with self._lock, file_lock:
            manifest = self._load_manifest(simulation_id)
            target_entry = next((e for e in manifest if e["version"] == version), None)
            if not target_entry:
                raise ValueError(f"Version {version} not found in registry for {simulation_id}")
            target_entry["simulation_id"] = simulation_id

            filepath = os.path.join(sim_dir, target_entry["filename"])
            if not os.path.exists(filepath):
                raise FileNotFoundError(f"Model file {filepath} not found on disk")

            from app.config import get_settings
            settings = get_settings()
            admission_context = "rollback"
            validate_production_admission(target_entry, context=admission_context)

            if settings.app_env.lower() == "production":
                fname = target_entry.get("filename", "")
                if ".." in fname or os.path.isabs(fname):
                    raise ModelIntegrityError(f"Path traversal detected in manifest filename '{fname}'.")
                real_filepath = os.path.realpath(filepath)
                real_storage = os.path.realpath(self.storage_dir)
                try:
                    common = os.path.commonpath([real_filepath, real_storage])
                    if common != real_storage:
                        raise ModelIntegrityError(
                            f"Artifact path '{filepath}' is outside trusted storage directory '{self.storage_dir}'."
                        )
                except ValueError as exc:
                    raise ModelIntegrityError(f"Path containment check failed for artifact '{filepath}': {exc}") from exc

                if not os.path.isfile(real_filepath) or os.path.getsize(real_filepath) == 0:
                    raise ModelIntegrityError(f"Artifact file '{filepath}' is missing, not a regular file, or empty.")

                actual_sha256 = compute_file_sha256(real_filepath)
                expected_sha256 = target_entry.get("sha256")
                if not expected_sha256:
                    raise ModelIntegrityError(
                        f"Production admission rejected: model version {version} lacks recorded sha256 ({admission_context})."
                    )
                if not hmac.compare_digest(actual_sha256, expected_sha256):
                    raise ModelIntegrityError(
                        f"Production admission rejected: artifact SHA-256 digest mismatch for {simulation_id} v{version}: "
                        f"expected '{expected_sha256}', actual '{actual_sha256}' ({admission_context})."
                    )

                receipt_dict = target_entry.get("training_receipt")
                if not receipt_dict:
                    raise ModelIntegrityError(
                        f"Production admission rejected: model lacks verified cryptographic training execution receipt ({admission_context})."
                    )
                verifier = self._verifier or get_default_training_verifier()
                with open(real_filepath, "rb") as rf:
                    artifact_bytes = rf.read()
                is_valid_rcpt, rcpt_reason = verifier.verify_receipt(
                    receipt_dict,
                    candidate_artifact_sha256=actual_sha256,
                    candidate_artifact_bytes=artifact_bytes,
                    simulation_id=simulation_id,
                    expected_architecture=target_entry.get("architecture"),
                    expected_feature_schema_version=target_entry.get("feature_schema_version"),
                    expected_input_dim=target_entry.get("input_dim"),
                    expected_feature_names=target_entry.get("feature_names"),
                )
                if not is_valid_rcpt:
                    raise ModelIntegrityError(
                        f"Production admission rejected: cryptographic training receipt verification failed ({rcpt_reason}) ({admission_context})."
                    )
            elif target_entry.get("training_receipt"):
                rcpt = target_entry.get("training_receipt")
                if isinstance(rcpt, dict) and rcpt.get("key_id"):
                    verifier = self._verifier or get_default_training_verifier()
                    if not verifier.is_key_trusted(rcpt["key_id"]):
                        raise ModelIntegrityError(
                            f"Cryptographic training receipt verification failed (Receipt key_id '{rcpt['key_id']}' has been explicitly revoked.) ({admission_context})."
                        )

            max_gen = max((e.get("generation", 0) for e in manifest), default=0)
            target_entry["generation"] = max_gen + 1

            # Mark target as active, all others as inactive
            for entry in manifest:
                entry["is_active"] = entry["version"] == version
                if entry["version"] == version:
                    entry["status"] = "champion"
                elif entry.get("status") == "champion":
                    entry["status"] = "inactive"

            # Authoritative manifest is saved FIRST (Section 9.1)
            self._save_manifest(simulation_id, manifest)

            # Secondary global model link updated SECOND
            try:
                self._update_global_model_link(simulation_id, filepath)
            except Exception as link_err:
                logger.error("Failed to update global model link during rollback: %s", link_err)

            with contextlib.suppress(Exception):
                from app.application.services.model_service import ModelService

                ModelService(settings).invalidate_model_cache()

            with contextlib.suppress(Exception):
                from app.presentation.routers.realtime_inference import reset_model_cache

                reset_model_cache(simulation_id)

            with contextlib.suppress(Exception):
                from app.presentation.routers.predict import clear_all_serving_caches

                clear_all_serving_caches()

            logger.info("Rolled back registry of %s to version %d (generation %d)", simulation_id, version, target_entry["generation"])
            return target_entry

    def sign_off(
        self,
        simulation_id: str,
        version: int,
        role: str,
        user: str,
        signature: str,
        auth_token: str | None = None,
        fairness_score: float = 1.0,
        bias_metric: float = 0.0,
        drift_divergence: float = 0.0,
    ) -> dict[str, Any]:
        """Approve a model version by adding an authenticated sign-off.

        Promotes version to challenger/champion if ML engineer and compliance officer have signed off.
        Enforces Four-Eyes principle (distinct individuals), authenticated principal validation,
        and binds cryptographic signature to canonical payload.
        """
        sim_dir = self._get_sim_dir(simulation_id)
        file_lock = _get_registry_file_lock(sim_dir)
        with self._lock, file_lock:
            manifest = self._load_manifest(simulation_id)
            entry = next((e for e in manifest if e["version"] == version), None)
            if not entry:
                raise ValueError(f"Version {version} not found in registry for {simulation_id}")

            if role not in ("compliance", "compliance_officer", "ml_engineer"):
                raise ValueError("Role must be 'compliance' or 'ml_engineer'")

            from app.config import get_settings
            settings = get_settings()

            # Prevent development seeded accounts from signing off in production
            SEEDED_ACCOUNTS = {"admin", "analyst_a1", "analyst_b1", "analyst_c1", "auditor"}
            if settings.app_env.lower() == "production" and user in SEEDED_ACCOUNTS:
                raise ModelIntegrityError(
                    f"Production promotion rejected: development seeded account '{user}' cannot authorize promotion."
                )

            norm_role = "compliance" if role in ("compliance", "compliance_officer") else "ml_engineer"

            # Initialize sign_offs list if not present (backward compatibility)
            entry["simulation_id"] = simulation_id
            if "sign_offs" not in entry:
                entry["sign_offs"] = []

            # Prevent duplicate sign-offs for the same role
            existing_roles = [
                ("compliance" if s["role"] in ("compliance", "compliance_officer") else "ml_engineer")
                for s in entry["sign_offs"]
                if s.get("is_revoked") is not True
            ]
            if norm_role in existing_roles:
                raise ValueError(f"Role '{role}' has already signed off on this version")

            # Four-Eyes Principle: Prevent same user from approving multiple roles on the same version
            existing_users = [s["user"] for s in entry["sign_offs"] if s.get("is_revoked") is not True]
            if user in existing_users:
                raise ValueError(
                    f"Four-Eyes Principle violation: user '{user}' cannot approve multiple roles on the same version"
                )

            # Verify identity and role membership against AuthenticationService
            from app.infrastructure.security.auth_service import AuthenticationService
            auth_svc = AuthenticationService.get_instance()

            is_identity_verified = False
            is_role_verified = False

            if auth_token:
                is_valid_tok, tok_payload, tok_err = auth_svc.verify_access_token(auth_token)
                if not is_valid_tok or not tok_payload:
                    raise ModelIntegrityError(
                        f"Production promotion rejected: sign-off authentication failed: {tok_err}"
                    )
                tok_user = tok_payload.get("username", "").lower()
                if tok_user != user.lower() and tok_payload.get("sub") != user:
                    raise ModelIntegrityError(
                        f"Production promotion rejected: token user '{tok_user}' does not match sign-off user '{user}'."
                    )
                tok_roles = [r.lower().strip() for r in tok_payload.get("roles", [])]
                if norm_role == "compliance" and not any(r in ("compliance", "compliance_officer", "admin") for r in tok_roles):
                    raise ModelIntegrityError("Production promotion rejected: token claims lack compliance role authority.")
                if norm_role == "ml_engineer" and not any(r in ("ml_engineer", "ml_lead") for r in tok_roles):
                    raise ModelIntegrityError("Production promotion rejected: token claims lack ml_engineer role authority.")
                authoritative_user = auth_svc.get_user(user)
                if not authoritative_user or not authoritative_user.is_active:
                    raise ModelIntegrityError(
                        f"Production promotion rejected: approver principal '{user}' is not an active authoritative user."
                    )
                is_identity_verified = True
                is_role_verified = True
            elif settings.app_env.lower() == "production":
                raise ModelIntegrityError(
                    "Production promotion rejected: authenticated session token is required for institutional sign-off."
                )
            else:
                user_rec = auth_svc.get_user(user)
                is_identity_verified = user_rec is not None
                if user_rec is not None:
                    user_roles = [r.lower().strip() for r in user_rec.roles]
                    if norm_role == "compliance":
                        is_role_verified = any(r in ("compliance", "compliance_officer", "admin") for r in user_roles)
                    elif norm_role == "ml_engineer":
                        is_role_verified = any(r in ("ml_engineer", "ml_lead") for r in user_roles)

            # Cryptographic signature verification over canonical payload
            artifact_sha = entry.get("sha256") or compute_file_sha256(os.path.join(sim_dir, entry["filename"]))
            canonical_payload = f"{simulation_id}:{version}:{artifact_sha}:{norm_role}:{user}"

            domain_key = derive_domain_key(settings.payload_signing_secret, DOMAIN_APPROVAL_SIGNOFF)
            expected_sig_domain = hmac.new(
                domain_key,
                canonical_payload.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            expected_sig_raw = hmac.new(
                settings.payload_signing_secret.encode("utf-8"),
                canonical_payload.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()

            is_signature_valid = bool(
                signature and (
                    hmac.compare_digest(signature, expected_sig_domain)
                    or hmac.compare_digest(signature, expected_sig_raw)
                )
            )

            # Append new sign-off details
            entry["sign_offs"].append(
                {
                    "role": norm_role,
                    "user": user,
                    "signature": signature,
                    "signature_scheme": "APPLICATION_HMAC_SHA256",
                    "is_signature_valid": is_signature_valid,
                    "is_identity_verified": is_identity_verified,
                    "is_role_verified": is_role_verified,
                    "is_independent_human_authorization": is_identity_verified and is_role_verified and user not in SEEDED_ACCOUNTS,
                    "payload_bound": canonical_payload,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "fairness_score": fairness_score,
                    "bias_metric": bias_metric,
                    "drift_divergence": drift_divergence,
                    "is_revoked": False,
                }
            )

            # Check if both compliance and ml_engineer have signed off
            signed_roles = [
                ("compliance" if s["role"] in ("compliance", "compliance_officer") else "ml_engineer")
                for s in entry["sign_offs"]
            ]
            if "compliance" in signed_roles and "ml_engineer" in signed_roles:
                if settings.app_env.lower() == "production":
                    all_sigs_valid = all(s.get("is_signature_valid", False) for s in entry["sign_offs"])
                    all_roles_valid = all(s.get("is_role_verified", False) for s in entry["sign_offs"])
                    if not all_sigs_valid:
                        self._save_manifest(simulation_id, manifest)
                        raise ModelIntegrityError(
                            "Production promotion rejected: sign-offs lack valid cryptographic signatures."
                        )
                    if not all_roles_valid:
                        self._save_manifest(simulation_id, manifest)
                        raise ModelIntegrityError(
                            "Production promotion rejected: sign-off missing verified role authority."
                        )
                    validate_production_admission(entry, context="sign_off")

                # Check if there is another active champion model in the manifest
                has_other_champion = any(
                    e.get("status") == "champion" and e.get("is_active") and e.get("version") != version
                    for e in manifest
                )

                if not has_other_champion:
                    # Promote directly to champion
                    entry["status"] = "champion"
                    entry["is_active"] = True
                    max_gen = max((e.get("generation", 0) for e in manifest), default=0)
                    entry["generation"] = max_gen + 1

                    # Deactivate all other entries
                    for e in manifest:
                        if e["version"] != version:
                            e["is_active"] = False
                            if e.get("status") == "champion":
                                e["status"] = "inactive"

                    filepath = os.path.join(sim_dir, entry["filename"])
                    # Manifest is saved FIRST
                    self._save_manifest(simulation_id, manifest)
                    try:
                        self._update_global_model_link(simulation_id, filepath)
                    except Exception as err:
                        logger.error("Failed to update global link after signoff: %s", err)
                    return entry
                else:
                    # Set as challenger
                    entry["status"] = "challenger"
                    entry["is_active"] = False

            self._save_manifest(simulation_id, manifest)
            return entry

    def revoke_sign_off(
        self,
        simulation_id: str,
        version: int,
        role: str,
        user: str = "",
        reason: str = "Unspecified",
        revoked_by: str | None = None,
    ) -> dict[str, Any]:
        """Revoke an approval on a model version, triggering automatic demotion if champion."""
        sim_dir = self._get_sim_dir(simulation_id)
        file_lock = _get_registry_file_lock(sim_dir)
        with self._lock, file_lock:
            manifest = self._load_manifest(simulation_id)
            entry = next((e for e in manifest if e["version"] == version), None)
            if not entry:
                raise ValueError(f"Version {version} not found in registry for {simulation_id}")

            norm_role = "compliance" if role in ("compliance", "compliance_officer") else "ml_engineer"
            effective_user = user or revoked_by or ""
            found = False
            for s in entry.get("sign_offs", []):
                s_role = "compliance" if s["role"] in ("compliance", "compliance_officer") else "ml_engineer"
                if s_role == norm_role and (not effective_user or s.get("user") == effective_user):
                    s["is_revoked"] = True
                    s["revocation_reason"] = reason
                    s["revoked_at"] = datetime.now(UTC).isoformat()
                    found = True

            if not found:
                raise ValueError(f"No sign-off found for role '{role}' on version {version}")

            # Filter active sign-offs list so revoked sign-offs are removed from active governance
            entry["sign_offs"] = [
                s for s in entry.get("sign_offs", [])
                if not (
                    ("compliance" if s["role"] in ("compliance", "compliance_officer") else "ml_engineer") == norm_role
                    and (not effective_user or s.get("user") == effective_user)
                )
            ]

            # ALWAYS bump generation upon revocation to invalidate caches
            max_gen = max((e.get("generation", 0) for e in manifest), default=0)
            entry["generation"] = max_gen + 1

            # If this version is champion or active, demote it
            if entry.get("is_active") or entry.get("status") == "champion":
                entry["is_active"] = False
                entry["status"] = "inactive"

            self._save_manifest(simulation_id, manifest)

            # Invalidate caches
            from app.config import get_settings
            with contextlib.suppress(Exception):
                from app.application.services.model_service import ModelService
                ModelService(get_settings()).invalidate_model_cache()

            return entry

    def revoke_version(
        self,
        simulation_id: str,
        version: int,
        reason: str = "Unspecified",
    ) -> dict[str, Any]:
        """Revoke a model version completely, marking it permanently ineligible for production."""
        sim_dir = self._get_sim_dir(simulation_id)
        file_lock = _get_registry_file_lock(sim_dir)
        with self._lock, file_lock:
            manifest = self._load_manifest(simulation_id)
            entry = next((e for e in manifest if e["version"] == version), None)
            if not entry:
                raise ValueError(f"Version {version} not found in registry for {simulation_id}")

            entry["is_revoked"] = True
            entry["revocation_reason"] = reason
            entry["revoked_at"] = datetime.now(UTC).isoformat()
            entry["is_production_eligible"] = False

            if entry.get("is_active") or entry.get("status") == "champion":
                entry["is_active"] = False
                entry["status"] = "inactive"
                max_gen = max((e.get("generation", 0) for e in manifest), default=0)
                entry["generation"] = max_gen + 1

            self._save_manifest(simulation_id, manifest)

            from app.config import get_settings
            with contextlib.suppress(Exception):
                from app.application.services.model_service import ModelService
                ModelService(get_settings()).invalidate_model_cache()

            return entry


class ModelEvaluationEngine:
    """Tracks model scoring performance, shadow latency, and triggers auto-promotion/rollbacks."""

    def __init__(self, registry: ModelRegistry) -> None:
        from app.infrastructure.redis_store import RedisStore

        self.registry = registry
        self._store = RedisStore("model_evaluation")
        self._lock = threading.RLock()

    def log_prediction(
        self,
        simulation_id: str,
        transaction_id: str,
        champion_version: int,
        champion_prob: float,
        champion_latency_ms: float,
        challenger_version: int | None = None,
        challenger_prob: float | None = None,
        challenger_latency_ms: float | None = None,
        routed_to: str = "champion",
    ) -> None:
        """Log scoring details for a transaction."""
        with self._lock:
            key = f"{simulation_id}:prediction:{transaction_id}"
            record = {
                "transaction_id": transaction_id,
                "champion_version": champion_version,
                "champion_prob": champion_prob,
                "champion_latency_ms": champion_latency_ms,
                "challenger_version": challenger_version,
                "challenger_prob": challenger_prob,
                "challenger_latency_ms": challenger_latency_ms,
                "actual_label": None,
                "routed_to": routed_to,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            self._store.set(key, record)

            list_key = f"{simulation_id}:prediction_keys"
            existing_val = self._store.get(list_key)
            existing = existing_val if isinstance(existing_val, list) else []
            existing.append(key)
            if len(existing) > 1000:
                oldest = existing.pop(0)
                self._store.delete(oldest)
            self._store.set(list_key, existing)

    def log_feedback(
        self, simulation_id: str, transaction_id: str, actual_label: int
    ) -> dict[str, Any]:
        """Record the ground truth label for a transaction and evaluate performance.

        Raises:
            KeyError: If transaction prediction was not previously logged (Zero-Mock Invariant).
        """
        with self._lock:
            key = f"{simulation_id}:prediction:{transaction_id}"
            record = self._store.get(key)
            if not record:
                raise KeyError(
                    f"Prediction for transaction '{transaction_id}' not found in evaluation store. "
                    "Cannot log feedback for unscored transactions (Zero-Mock Invariant)."
                )

            record["actual_label"] = actual_label
            self._store.set(key, record)

            return self.evaluate_performance(simulation_id)

    def evaluate_performance(self, simulation_id: str) -> dict[str, Any]:
        """Compute metrics over logged predictions with labels and check routing rules."""
        with self._lock:
            list_key = f"{simulation_id}:prediction_keys"
            keys = self._store.get(list_key) or []
            if not keys:
                return {}

            records = []
            for k in keys:
                rec = self._store.get(k)
                if rec and rec.get("actual_label") is not None:
                    records.append(rec)

            # Retrieve traffic share
            share_key = f"{simulation_id}:challenger_traffic_share"
            traffic_share = self._store.get(share_key) or 0.0

            # Get active/champion version
            active_version = self.registry.get_active_version(simulation_id)
            active_ver_num = active_version["version"] if active_version else 1

            if len(records) < 5:  # Require small warmup size for local testing/simulations
                return {
                    "status": "warmup",
                    "sample_count": len(records),
                    "champion_version": active_ver_num,
                    "traffic_share": traffic_share,
                }

            y_true = [r["actual_label"] for r in records]

            # Champion metrics
            y_pred_champ = [r["champion_prob"] for r in records]
            champ_latencies = [r["champion_latency_ms"] for r in records]
            avg_champ_latency = sum(champ_latencies) / len(champ_latencies)

            # Challenger metrics
            y_pred_chall = [
                r["challenger_prob"] for r in records if r["challenger_version"] is not None
            ]
            chall_latencies = [
                r["challenger_latency_ms"] for r in records if r["challenger_version"] is not None
            ]
            avg_chall_latency = sum(chall_latencies) / len(chall_latencies) if chall_latencies else 0.0

            from app.domain.metrics_service import safe_pr_auc_score, safe_roc_auc_score

            champ_auc = safe_roc_auc_score(y_true, y_pred_champ)
            champ_pr_auc = safe_pr_auc_score(y_true, y_pred_champ)

            champ_fp = 0
            champ_tn = 0
            for r in records:
                if r["actual_label"] == 0:
                    if r["champion_prob"] >= 0.5:
                        champ_fp += 1
                    else:
                        champ_tn += 1
            champ_fpr = champ_fp / (champ_fp + champ_tn) if (champ_fp + champ_tn) > 0 else 0.0

            chall_auc: float | None = None
            chall_pr_auc: float | None = None
            chall_fpr = 0.0
            if y_pred_chall:
                y_true_chall = [
                    r["actual_label"] for r in records if r["challenger_version"] is not None
                ]
                chall_auc = safe_roc_auc_score(y_true_chall, y_pred_chall)
                chall_pr_auc = safe_pr_auc_score(y_true_chall, y_pred_chall)

                chall_fp = 0
                chall_tn = 0
                for r in records:
                    if r["challenger_version"] is not None and r["actual_label"] == 0:
                        if r["challenger_prob"] >= 0.5:
                            chall_fp += 1
                        else:
                            chall_tn += 1
                chall_fpr = chall_fp / (chall_fp + champ_tn) if (chall_fp + champ_tn) > 0 else 0.0

            # Rollback Gating:
            rollback_triggered = False
            rollback_message = ""
            has_both_classes = len(set(y_true)) >= 2
            if (has_both_classes and champ_auc is not None and champ_auc < 0.65) or avg_champ_latency > 200.0 or champ_fpr > 0.05:
                rollback_triggered = True
                manifest = self.registry._load_manifest(simulation_id)
                previous_versions = [v for v in manifest if v["version"] < active_ver_num]
                if previous_versions:
                    stable_version = max(previous_versions, key=lambda x: x["version"])
                    self.registry.rollback(simulation_id, stable_version["version"])
                    traffic_share = 0.0
                    self._store.set(share_key, 0.0)
                    rollback_message = f"Auto-rollback triggered to version {stable_version['version']} due to performance degradation (AUC: {champ_auc:.4f}, FPR: {champ_fpr:.4f}, Latency: {avg_champ_latency:.1f}ms)."
                    logger.warning(rollback_message)
                else:
                    rollback_message = (
                        "Performance degradation detected, but no stable rollback candidate exists."
                    )
                    logger.warning(rollback_message)

            # Promotion Gating (Fail-closed on undefined/missing PR-AUC):
            promotion_triggered = False
            promotion_message = ""
            if not rollback_triggered and y_pred_chall and len(y_pred_chall) >= 5:
                if champ_pr_auc is None or chall_pr_auc is None:
                    promotion_message = "Challenger promotion evaluation deferred: PR-AUC undefined (single class or empty evaluation window)."
                    logger.info(promotion_message)
                elif traffic_share == 0.0 and chall_pr_auc > champ_pr_auc:
                    traffic_share = 0.1
                    self._store.set(share_key, 0.1)
                    promotion_message = f"Challenger (PR-AUC: {chall_pr_auc:.4f}) outperformed Champion (PR-AUC: {champ_pr_auc:.4f}). Routed 10% traffic to Challenger."
                    logger.info(promotion_message)
                    promotion_triggered = True
                elif traffic_share == 0.1 and chall_pr_auc > champ_pr_auc:
                    challenger_ver_num = next(
                        r["challenger_version"] for r in records if r["challenger_version"] is not None
                    )
                    self.registry.rollback(simulation_id, challenger_ver_num)
                    traffic_share = 0.0
                    self._store.set(share_key, 0.0)
                    promotion_message = f"Challenger version {challenger_ver_num} promoted to Champion after continued outperformance (PR-AUC: {chall_pr_auc:.4f} vs {champ_pr_auc:.4f})."
                    logger.info(promotion_message)
                    promotion_triggered = True

            metrics = {
                "champion_version": active_ver_num,
                "champion_auc": champ_auc,
                "champion_pr_auc": champ_pr_auc,
                "champion_fpr": champ_fpr,
                "champion_latency_ms": avg_champ_latency,
                "challenger_auc": chall_auc,
                "challenger_pr_auc": chall_pr_auc,
                "challenger_fpr": chall_fpr,
                "challenger_latency_ms": avg_chall_latency,
                "traffic_share": traffic_share,
                "sample_count": len(records),
                "rollback_triggered": rollback_triggered,
                "rollback_message": rollback_message,
                "promotion_triggered": promotion_triggered,
                "promotion_message": promotion_message,
            }

            metrics_key = f"{simulation_id}:shadow_metrics"
            self._store.set(metrics_key, metrics)

            from app.presentation.routers.simulation import _simulation_events

            if rollback_triggered:
                _simulation_events.push_list(
                    simulation_id,
                    {
                        "event_type": "rollback",
                        "data": {
                            "message": rollback_message,
                            "timestamp": datetime.now(UTC).isoformat(),
                        },
                    },
                )
            elif promotion_triggered:
                _simulation_events.push_list(
                    simulation_id,
                    {
                        "event_type": "canary",
                        "data": {
                            "message": promotion_message,
                            "timestamp": datetime.now(UTC).isoformat(),
                        },
                    },
                )

            return metrics

    def get_shadow_metrics(self, simulation_id: str) -> dict[str, Any]:
        """Retrieve the latest computed shadow deployment metrics."""
        metrics_key = f"{simulation_id}:shadow_metrics"
        return self._store.get(metrics_key) or {
            "champion_version": 1,
            "champion_auc": 0.0,
            "champion_pr_auc": 0.0,
            "champion_fpr": 0.0,
            "champion_latency_ms": 0.0,
            "challenger_auc": 0.0,
            "challenger_pr_auc": 0.0,
            "challenger_fpr": 0.0,
            "challenger_latency_ms": 0.0,
            "traffic_share": 0.0,
            "sample_count": 0,
        }

    def get_traffic_share(self, simulation_id: str) -> float:
        """Get the current traffic share routed to the challenger model."""
        share_key = f"{simulation_id}:challenger_traffic_share"
        val = self._store.get(share_key)
        return float(val) if val is not None else 0.0
