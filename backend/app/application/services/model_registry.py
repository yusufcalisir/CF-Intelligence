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
import os
import shutil
import tempfile
import threading
from datetime import UTC, datetime
from typing import Any

import torch

from app.domain.model_serving_errors import (
    ModelCompatibilityError,
    ModelIntegrityError,
    ModelNotAvailableError,
)

logger = logging.getLogger(__name__)


def compute_file_sha256(filepath: str) -> str:
    """Compute SHA-256 hex digest of a file on disk."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


class ModelRegistry:
    """Manages versioned model artifacts and registry manifests.

    Models are stored in the filesystem under `storage/registry/{simulation_id}/`.
    A manifest file `registry.json` tracks versions, metrics, and active states.
    """

    def __init__(self, storage_dir: str | None = None) -> None:
        self._lock = threading.RLock()
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
        with self._lock:
            sim_dir = self._get_sim_dir(simulation_id)
            manifest_path = os.path.join(sim_dir, "registry.json")
            if not os.path.exists(manifest_path):
                return []
            try:
                with open(manifest_path, encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return data
            except Exception as e:
                logger.warning("Failed to load registry manifest for %s: %s", simulation_id, e)
            return []

    def _save_manifest(self, simulation_id: str, manifest: list[dict[str, Any]]) -> None:
        with self._lock:
            sim_dir = self._get_sim_dir(simulation_id)
            manifest_path = os.path.join(sim_dir, "registry.json")
            tmp_manifest_path = os.path.join(sim_dir, f"registry_{os.getpid()}_{threading.get_ident()}.tmp")
            try:
                with open(tmp_manifest_path, "w", encoding="utf-8") as f:
                    json.dump(manifest, f, indent=2)
                os.replace(tmp_manifest_path, manifest_path)
            except Exception as e:
                logger.error("Failed to save registry manifest for %s: %s", simulation_id, e)
                if os.path.exists(tmp_manifest_path):
                    with contextlib.suppress(OSError):
                        os.remove(tmp_manifest_path)

    def save_version(
        self,
        simulation_id: str,
        state_dict: dict[str, Any],
        metrics: dict[str, Any],
        is_promoted: bool = True,
        git_commit_hash: str | None = None,
        dataset_hash: str | None = None,
        dp_noise_profile: dict[str, Any] | None = None,
        status: str = "inactive",
        provenance_type: str = "trained",
    ) -> dict[str, Any]:
        """Save a new version of the global model weights.

        Args:
            simulation_id: ID of the active simulation run.
            state_dict: PyTorch model state dict.
            metrics: Dict of metrics (accuracy, f1_score, auc_roc, recall, etc.).
            is_promoted: Whether this version is currently promoted as active.
            git_commit_hash: Hash of the git commit.
            dataset_hash: Hash of the training dataset.
            dp_noise_profile: DP mechanism profile details.
            status: Initial status of the version.
            provenance_type: Provenance classification ('trained', 'structural_fixture', 'legacy').

        Returns:
            The created version entry metadata.
        """
        with self._lock:
            manifest = self._load_manifest(simulation_id)
            next_version = 1
            if manifest:
                next_version = max(entry["version"] for entry in manifest) + 1

            sim_dir = self._get_sim_dir(simulation_id)
            filename = f"model_v{next_version}.pt"
            filepath = os.path.join(sim_dir, filename)

            try:
                torch.save(state_dict, filepath)
                logger.info("Saved model version %d to %s", next_version, filepath)
            except Exception as e:
                logger.error("Failed to save model file: %s", e)
                raise

            # If this new version is promoted, set other versions to inactive
            if is_promoted:
                for entry in manifest:
                    entry["is_active"] = False
                    if entry.get("status") == "champion":
                        entry["status"] = "inactive"

            file_sha256 = compute_file_sha256(filepath)
            entry = {
                "version": next_version,
                "filename": filename,
                "metrics": metrics,
                "is_active": is_promoted,
                "status": status if status != "inactive" or not is_promoted else "champion",
                "git_commit_hash": git_commit_hash or "unknown",
                "dataset_hash": dataset_hash or "unknown",
                "sha256": file_sha256,
                "dp_noise_profile": dp_noise_profile
                or {"mechanism": "none", "epsilon": 0.0, "delta": 0.0},
                "provenance_type": provenance_type,
                "sign_offs": [],
                "created_at": datetime.now(UTC).isoformat(),
            }
            manifest.append(entry)
            self._save_manifest(simulation_id, manifest)

            # If promoted, also copy/link this version to the main global model path for backward compatibility
            if is_promoted:
                self._update_global_model_link(simulation_id, filepath)
                with contextlib.suppress(Exception):
                    from app.config import get_settings
                    from app.application.services.model_service import ModelService

                    ModelService(get_settings()).invalidate_model_cache()

            return entry

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

            # Path & regular file validation
            if not os.path.exists(filepath):
                raise ModelNotAvailableError(
                    f"Active champion artifact file '{filepath}' for simulation '{sim_id}' does not exist on disk."
                )
            if not os.path.isfile(filepath):
                raise ModelIntegrityError(
                    f"Active champion artifact path '{filepath}' is not a regular file."
                )

            # Containment check (must reside inside storage directory)
            abs_filepath = os.path.abspath(filepath)
            abs_storage = os.path.abspath(self.storage_dir)
            try:
                rel = os.path.relpath(abs_filepath, abs_storage)
                if rel.startswith("..") or os.path.isabs(rel):
                    raise ModelIntegrityError(
                        f"Active champion artifact path '{filepath}' is located outside trusted storage directory '{self.storage_dir}'."
                    )
            except ValueError as exc:
                raise ModelIntegrityError(
                    f"Path validation failed for artifact '{filepath}': {exc}"
                ) from exc

            # Non-empty check
            if os.path.getsize(filepath) == 0:
                raise ModelIntegrityError(
                    f"Active champion artifact file '{filepath}' is empty (0 bytes)."
                )

            # Read bytes into memory snapshot to eliminate TOCTOU vulnerability
            try:
                with open(filepath, "rb") as f:
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

            # Authenticity status distinction (Section 3.3)
            sign_offs = entry.get("sign_offs", [])
            has_dual_signoff = (
                isinstance(sign_offs, list)
                and any(s.get("role") == "ml_engineer" for s in sign_offs)
                and any(s.get("role") == "compliance" for s in sign_offs)
            )
            authenticity_status = (
                "DUAL_SIGNOFF_AUTHORIZED"
                if has_dual_signoff
                else "INTEGRITY_VERIFIED_UNAUTHENTICATED_MANIFEST"
            )

            resolved_metadata = {
                "version": version_val,
                "filename": entry.get("filename", os.path.basename(filepath)),
                "simulation_id": sim_id,
                "artifact_path": filepath,
                "sha256": actual_sha256,
                "expected_sha256": expected_sha256,
                "status": "champion",
                "is_active": True,
                "is_verified": True,
                "authenticity_status": authenticity_status,
                "architecture": entry.get("architecture", "FraudDetectionModel-GroupNorm"),
                "input_dim": entry.get("input_dim", 10),
                "metrics": entry.get("metrics", {}),
                "git_commit_hash": entry.get("git_commit_hash", "unknown"),
                "dataset_hash": entry.get("dataset_hash", "unknown"),
                "dp_noise_profile": entry.get("dp_noise_profile", {}),
                "provenance_type": entry.get("provenance_type", "trained"),
                "sign_offs": sign_offs,
                "created_at": entry.get("created_at"),
            }

            return filepath, bytes_data, resolved_metadata

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

        with self._lock:
            manifest = self._load_manifest(simulation_id)
            target_entry = next((e for e in manifest if e["version"] == version), None)
            if not target_entry:
                raise ValueError(f"Version {version} not found in registry for {simulation_id}")

            sim_dir = self._get_sim_dir(simulation_id)
            filepath = os.path.join(sim_dir, target_entry["filename"])
            if not os.path.exists(filepath):
                raise FileNotFoundError(f"Model file {filepath} not found on disk")

            if target_status == "champion":
                for entry in manifest:
                    entry["is_active"] = entry["version"] == version
                    if entry["version"] == version:
                        entry["status"] = "champion"
                    elif entry.get("status") == "champion":
                        entry["status"] = "inactive"
                self._update_global_model_link(simulation_id, filepath)
                with contextlib.suppress(Exception):
                    from app.config import get_settings
                    from app.application.services.model_service import ModelService

                    ModelService(get_settings()).invalidate_model_cache()
            else:  # challenger
                for entry in manifest:
                    if entry["version"] == version:
                        entry["status"] = "challenger"
                        entry["is_active"] = False
                    elif entry.get("status") == "challenger":
                        entry["status"] = "inactive"

            self._save_manifest(simulation_id, manifest)
            logger.info("Promoted model version %d to %s for %s", version, target_status, simulation_id)
            return target_entry

    def rollback(self, simulation_id: str, version: int) -> dict[str, Any]:
        """Rollback/promote a specific historical version as the active model."""
        with self._lock:
            manifest = self._load_manifest(simulation_id)
            target_entry = next((e for e in manifest if e["version"] == version), None)
            if not target_entry:
                raise ValueError(f"Version {version} not found in registry for {simulation_id}")

            sim_dir = self._get_sim_dir(simulation_id)
            filepath = os.path.join(sim_dir, target_entry["filename"])
            if not os.path.exists(filepath):
                raise FileNotFoundError(f"Model file {filepath} not found on disk")

            # Mark target as active, all others as inactive
            for entry in manifest:
                entry["is_active"] = entry["version"] == version
                if entry["version"] == version:
                    entry["status"] = "champion"
                elif entry.get("status") == "champion":
                    entry["status"] = "inactive"

            self._save_manifest(simulation_id, manifest)
            self._update_global_model_link(simulation_id, filepath)
            with contextlib.suppress(Exception):
                from app.config import get_settings
                from app.application.services.model_service import ModelService

                ModelService(get_settings()).invalidate_model_cache()

            logger.info("Rolled back registry of %s to version %d", simulation_id, version)
            return target_entry

    def sign_off(
        self,
        simulation_id: str,
        version: int,
        role: str,
        user: str,
        signature: str,
        fairness_score: float = 1.0,
        bias_metric: float = 0.0,
        drift_divergence: float = 0.0,
    ) -> dict[str, Any]:
        """Approve a model version by adding a cryptographic sign-off.

        Promotes version to challenger/champion if ML engineer and compliance officer have signed off.
        """
        with self._lock:
            manifest = self._load_manifest(simulation_id)
            entry = next((e for e in manifest if e["version"] == version), None)
            if not entry:
                raise ValueError(f"Version {version} not found in registry for {simulation_id}")

            if role not in ("compliance", "ml_engineer"):
                raise ValueError("Role must be 'compliance' or 'ml_engineer'")

            # Initialize sign_offs list if not present (backward compatibility)
            if "sign_offs" not in entry:
                entry["sign_offs"] = []

            # Prevent duplicate sign-offs for the same role
            existing_roles = [s["role"] for s in entry["sign_offs"]]
            if role in existing_roles:
                raise ValueError(f"Role '{role}' has already signed off on this version")

            # Append new sign-off details
            entry["sign_offs"].append(
                {
                    "role": role,
                    "user": user,
                    "signature": signature,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "fairness_score": fairness_score,
                    "bias_metric": bias_metric,
                    "drift_divergence": drift_divergence,
                }
            )

            # Check if both compliance and ml_engineer have signed off
            signed_roles = [s["role"] for s in entry["sign_offs"]]
            if "compliance" in signed_roles and "ml_engineer" in signed_roles:
                # Check if there is an active champion model in the manifest
                has_champion = any(
                    e.get("status") == "champion" and e.get("is_active") for e in manifest
                )

                if not has_champion:
                    # Promote directly to champion
                    entry["status"] = "champion"
                    entry["is_active"] = True

                    # Deactivate all other entries
                    for e in manifest:
                        if e["version"] != version:
                            e["is_active"] = False
                            if e.get("status") == "champion":
                                e["status"] = "inactive"

                    sim_dir = self._get_sim_dir(simulation_id)
                    filepath = os.path.join(sim_dir, entry["filename"])
                    self._update_global_model_link(simulation_id, filepath)
                else:
                    # Set as challenger
                    entry["status"] = "challenger"
                    entry["is_active"] = False

            self._save_manifest(simulation_id, manifest)
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
